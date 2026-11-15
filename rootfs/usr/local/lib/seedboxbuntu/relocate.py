"""SeedBoxBuntu - déplacer entre partages les titres que personne ne regarde plus (ou les remonter sur le disque rapide).

Idée : le disque local est petit et rapide, un partage réseau est grand et plus lent. Les titres jamais lus depuis longtemps
n'ont pas besoin du disque rapide. Cette page liste, pour un volume de départ, les titres inactifs depuis N jours (365 par défaut) :
  - « inactif » = aucun compte Jellyfin ne les a lus depuis N jours, ou jamais lus ET ajoutés depuis plus de N jours ;
  - le gain affiché est l'espace RÉEL libéré sur le volume de départ : les fichiers encore liés à un torrent (lien dur) ne libèrent
    rien tant que le torrent existe, ils sont annoncés à 0 ;
  - « Déplacer » demande à Radarr ou Sonarr de déplacer eux-mêmes le titre vers un dossier racine du volume d'arrivée : ils gardent
    ainsi leur suivi (profil, noms, recherche). Un dossier racine doit y exister, avec assez de place (marge de 100 Go).
Limites assumées : l'historique de lecture est celui de Jellyfin pour les comptes qu'il connaît (lectures faites ailleurs, ou avant
l'ajout d'un compte, non comptées) : si Jellyfin ne remonte AUCUNE lecture, la liste est déclarée peu fiable ; un déplacement entre
deux disques est une copie puis une suppression, donc long et sans retour immédiat ; Jellyfin voit le titre disparaître puis
revenir, le temps d'un balayage ; 20 titres au plus par déplacement.
    seedboxctl relocate show VOLUME | move APP VOLUME_ARRIVEE ID...
"""
import json
import os
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "relocate.json"
DEFAULTS = {"idle_days": 365, "min_free_gb": 100}
MAX_MOVE = 20
SHOWN = 60
GB = 1 << 30


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        low, high = (30, 3650) if key == "idle_days" else (0, 100000)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ctl.SeedboxError(f"{key} : entier de {low} à {high}")
        cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def _stamp(text):
    try:
        return int(time.mktime(time.strptime(str(text)[:19].replace("Z", ""), "%Y-%m-%dT%H:%M:%S")) - time.timezone)
    except ValueError:
        return 0


def played_paths(jellyfin=None):
    """{chemin du fichier: dernière lecture (epoch)} pour tous les comptes ; None si Jellyfin est injoignable."""
    jellyfin = jellyfin or ctl.jellyfin_api
    try:
        users = jellyfin("GET", "/Users").json() or []
        found = {}
        for user in users:
            result = jellyfin("GET", f"/Users/{user['Id']}/Items?Recursive=true&IncludeItemTypes=Movie,Episode&Filters=IsPlayed"
                                     "&Fields=Path&EnableUserData=true&Limit=20000")
            if not result.ok:
                continue
            for item in (result.json() or {}).get("Items", []):
                when = _stamp((item.get("UserData") or {}).get("LastPlayedDate"))
                path = item.get("Path")
                if path and when:
                    found[path] = max(found.get(path, 0), when)
        return found
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        return None


def candidates(volume, records=None, played=None, now=None, cfg=None):
    """Titres inactifs d'un volume : [{app, id, title, size, reclaim, last_played, files}], les plus gros d'abord."""
    cfg = cfg or settings()
    libscan = ctl.libscan()
    records = libscan.records() if records is None else records
    now = time.time() if now is None else now
    limit = now - cfg["idle_days"] * 86400
    titles = {}
    for r in records:
        if r["volume"] != volume:
            continue
        t = titles.setdefault((r["app"], r["id"]), {"app": r["app"], "id": r["id"], "title": r["title"], "size": 0, "reclaim": 0, "files": 0,
                                                    "last_played": 0, "added": "9999"})
        t["size"] += r["size"]
        t["files"] += 1
        t["added"] = min(t["added"], r.get("added") or "9999")
        host = libscan.to_host(r["path"])
        if host and libscan.link_count(host) == 1:
            t["reclaim"] += r["size"]
        t["last_played"] = max(t["last_played"], (played or {}).get(r["path"], 0))
    found = []
    for t in titles.values():
        if t["last_played"]:
            if t["last_played"] > limit:
                continue
        else:
            added = _stamp(t["added"] + "T00:00:00") if t["added"] != "9999" else 0
            if not added or added > limit:
                continue
        found.append(t)
    found.sort(key=lambda t: -t["size"])
    return found


def targets(api=None):
    """Dossiers racine Radarr et Sonarr, par volume : {volume: {app: [chemins]}}."""
    api = api or ctl.arr_api
    libscan = ctl.libscan()
    found = {}
    for app in ("radarr", "sonarr"):
        result = api(app, "GET", "/rootfolder")
        if result.ok:
            for folder in result.json() or []:
                found.setdefault(libscan.volume_of(folder["path"]), {}).setdefault(app, []).append(folder["path"])
    return found


def move(app, volume_to, ids, volume_from=None, api=None, records=None, played=None, now=None, by="console"):
    """Déplace des titres inactifs vers un dossier racine du volume d'arrivée. Chaque identifiant est revérifié côté serveur."""
    if app not in ("radarr", "sonarr"):
        raise ctl.SeedboxError("application inconnue")
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise ctl.SeedboxError("identifiants : des nombres")
    if not ids or len(ids) > MAX_MOVE:
        raise ctl.SeedboxError(f"1 à {MAX_MOVE} titres à la fois")
    api = api or ctl.arr_api
    libscan = ctl.libscan()
    cfg = settings()
    records = libscan.records() if records is None else records
    if not volume_from:
        raise ctl.SeedboxError("volume de départ manquant")
    if volume_from == volume_to:
        raise ctl.SeedboxError("le volume d'arrivée doit différer du volume de départ")
    played = played_paths() if played is None else played
    if played is None:
        raise ctl.SeedboxError("Jellyfin injoignable : l'historique de lecture est nécessaire")
    allowed = {t["id"]: t for t in candidates(volume_from, records, played, now, cfg) if t["app"] == app}
    unknown = [i for i in ids if i not in allowed]
    if unknown:
        raise ctl.SeedboxError("titre non inactif ou inconnu : le déplacement est refusé")
    roots = targets(api).get(volume_to, {}).get(app)
    if not roots:
        raise ctl.SeedboxError(f"aucun dossier racine {app} sur le volume « {volume_to} » : ajoutez-en un dans l'application")
    need = sum(allowed[i]["size"] for i in ids)
    free = (libscan.volumes_space().get(volume_to) or (None, None))[1]
    if free is not None and free - need < cfg["min_free_gb"] * GB:
        raise ctl.SeedboxError("espace insuffisant sur le volume d'arrivée (marge de %d Go comprise)" % cfg["min_free_gb"])
    body = {("movieIds" if app == "radarr" else "seriesIds"): ids, "rootFolderPath": roots[0], "moveFiles": True}
    result = api(app, "PUT", "/movie/editor" if app == "radarr" else "/series/editor", body)
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : déplacement refusé (HTTP {result.status})")
    try:
        ctl.audit().record(by, "", "Titres déplacés entre volumes", f"{len(ids)} titre(s) vers {volume_to}", "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    say(f"déplacement lancé : {len(ids)} titre(s) vers {volume_to}")
    return {"moved": len(ids), "bytes": need, "root": roots[0]}


def overview(volume=None, api=None):
    libscan = ctl.libscan()
    data = libscan.load()
    volumes = sorted({r["volume"] for r in data["records"]})
    cfg = settings()
    volume = volume if volume in volumes else (volumes[0] if volumes else "")
    played = played_paths()
    rows = candidates(volume, data["records"], played, None, cfg) if volume else []
    try:
        roots = {v: sorted(apps) for v, apps in targets(api).items()}
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        roots = {}
    return {"settings": cfg, "volumes": volumes, "volume": volume, "history": bool(played), "reliable": bool(played),
            "candidates": [{**t, "last_played": t["last_played"]} for t in rows[:SHOWN]], "total": len(rows),
            "reclaim": sum(t["reclaim"] for t in rows), "roots": roots}
