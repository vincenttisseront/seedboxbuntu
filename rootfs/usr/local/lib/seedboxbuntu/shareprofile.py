"""SeedBoxBuntu - un profil de qualité par partage : « enfants » plafonné, « archive » léger, « référence » en grande qualité.

Radarr et Sonarr rattachent un profil de qualité à CHAQUE titre, pas à un dossier. Cette page ajoute la notion de « profil
d'un volume » : pour chaque partage (ou le disque local) vous choisissez, parmi les profils qui existent déjà dans Radarr et
Sonarr, celui que doivent avoir les titres rangés dessus. « Appliquer » met à jour les titres existants du volume ; en mode
automatique, un contrôle horaire met aussi au profil les titres ajoutés ensuite (par exemple depuis Seerr).
Rien n'est téléchargé ni supprimé : changer de profil modifie seulement ce que Radarr et Sonarr chercheront ensuite. Un fichier
déjà présent n'est pas remplacé tant que le nouveau profil ne le juge pas insuffisant (voir « Remplacements »).
Limites assumées : les profils eux-mêmes se créent dans Radarr et Sonarr (la console ne les invente pas) ; un titre déplacé vers un
autre volume garde son profil jusqu'au prochain passage (automatique ou manuel) ; le volume d'un titre est celui de son dossier.
    seedboxctl shareprofile show | set APP VOLUME PROFIL_ID | unset APP VOLUME | auto on|off | apply [APP]
"""
import json
import os
import re
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "shareprofile.json"
LAST_FILE = ctl.STATE / "shareprofile-last.json"
APPS = ("radarr", "sonarr")
VOLUME = re.compile(r"^(local|[a-z][a-z0-9]{0,19})$")
EVERY = 3300
CHUNK = 100


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    mapping = {app: {v: int(p) for v, p in (stored.get("mapping", {}).get(app) or {}).items() if VOLUME.match(v) and isinstance(p, int)} for app in APPS}
    return {"mapping": mapping, "auto": bool(stored.get("auto", False))}


def _write(cfg):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)


def set_profile(app, volume, profile_id):
    if app not in APPS or not VOLUME.match(str(volume)):
        raise ctl.SeedboxError("application ou volume invalide")
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
        raise ctl.SeedboxError("profil : un identifiant entier")
    cfg = settings()
    cfg["mapping"][app][volume] = profile_id
    _write(cfg)
    say(f"profil du volume {volume} ({app}) : {profile_id}")
    return cfg


def unset_profile(app, volume):
    cfg = settings()
    cfg["mapping"].get(app, {}).pop(volume, None)
    _write(cfg)
    return cfg


def set_auto(on):
    cfg = settings()
    cfg["auto"] = bool(on)
    _write(cfg)
    return cfg


def profiles(api):
    found = {}
    for app in APPS:
        result = api(app, "GET", "/qualityprofile")
        if result.ok:
            found[app] = [{"id": p["id"], "name": str(p.get("name", ""))[:60]} for p in (result.json() or [])]
    return found


def titles(api):
    """{app: [(id, titre, chemin, volume, profil)]} d'après Radarr et Sonarr."""
    libscan = ctl.libscan()
    found = {}
    for app, path in (("radarr", "/movie"), ("sonarr", "/series")):
        result = api(app, "GET", path)
        if not result.ok:
            raise ctl.SeedboxError(f"{app} : HTTP {result.status}")
        found[app] = [(t["id"], str(t.get("title", ""))[:100], t.get("path", ""), libscan.volume_of(t.get("path", "")), t.get("qualityProfileId", 0))
                      for t in (result.json() or [])]
    return found


def apply(app=None, api=None, by="console"):
    """Met au profil voulu les titres des volumes configurés. Renvoie {app: nombre de titres changés}."""
    api = api or ctl.arr_api
    cfg = settings()
    data = titles(api)
    changed = {}
    for name in (app,) if app else APPS:
        if name not in APPS:
            raise ctl.SeedboxError("application inconnue")
        groups = {}
        for tid, _title, _path, volume, profile in data.get(name, []):
            wanted = cfg["mapping"][name].get(volume)
            if wanted and wanted != profile:
                groups.setdefault(wanted, []).append(tid)
        count = 0
        for wanted, ids in groups.items():
            for start in range(0, len(ids), CHUNK):
                chunk = ids[start:start + CHUNK]
                body = {"movieIds": chunk} if name == "radarr" else {"seriesIds": chunk}
                body["qualityProfileId"] = wanted
                result = api(name, "PUT", "/movie/editor" if name == "radarr" else "/series/editor", body)
                if not result.ok:
                    raise ctl.SeedboxError(f"{name} : changement refusé (HTTP {result.status})")
                count += len(chunk)
        changed[name] = count
    if any(changed.values()):
        try:
            ctl.audit().record(by, "", "Profils de qualité appliqués par volume", ", ".join(f"{k} {v}" for k, v in changed.items()), "console", "ok")
        except Exception:  # noqa: BLE001
            pass
        say(f"profils appliqués : {changed}")
    return changed


def tick(now=None, api=None):
    cfg = settings()
    if not cfg["auto"] or not any(cfg["mapping"].values()):
        return False
    now = time.time() if now is None else now
    try:
        if now - json.loads(LAST_FILE.read_text()).get("at", 0) < EVERY:
            return False
    except (OSError, ValueError, AttributeError):
        pass
    LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(json.dumps({"at": int(now)}))
    try:
        apply(None, api, by="automatique")
    except (ctl.SeedboxError, OSError, ValueError) as error:
        say(f"profils par volume : {str(error)[:100]}")
        return False
    return True


def overview(api=None):
    api = api or ctl.arr_api
    cfg = settings()
    result = {"settings": cfg, "profiles": {}, "volumes": {}, "errors": {}}
    try:
        result["profiles"] = profiles(api)
        data = titles(api)
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        result["errors"]["all"] = str(error)[:160]
        return result
    for app in APPS:
        names = {p["id"]: p["name"] for p in result["profiles"].get(app, [])}
        per = {}
        for _id, _t, _p, volume, profile in data.get(app, []):
            entry = per.setdefault(volume, {"titles": 0, "wrong": 0, "wanted": cfg["mapping"][app].get(volume), "profiles": {}})
            entry["titles"] += 1
            entry["profiles"][names.get(profile, str(profile))] = entry["profiles"].get(names.get(profile, str(profile)), 0) + 1
            if entry["wanted"] and entry["wanted"] != profile:
                entry["wrong"] += 1
        result["volumes"][app] = per
    return result
