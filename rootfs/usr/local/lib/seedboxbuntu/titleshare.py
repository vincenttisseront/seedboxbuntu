"""SeedBoxBuntu - partager UN film ou UNE série avec quelqu'un, pour une durée limitée.

Jellyfin ne sait pas limiter un compte à un seul titre : il limite à des médiathèques. Le partage d'un titre fait donc :
  1. un dossier « partages/<id> » dans les médias, contenant des LIENS DURS vers les fichiers du titre (aucune copie : pas
     d'espace de plus, et le titre reste dans sa médiathèque normale) ;
  2. une médiathèque Jellyfin « Partage <id> » qui ne contient que ce dossier ;
  3. une invitation (page /invite/…) dont le compte créé n'a accès QU'À cette médiathèque, ne peut ni télécharger ni demander
     de médias dans Seerr, et disparaît à l'échéance.
À l'échéance (ou à la révocation), la médiathèque et le dossier de liens sont supprimés. Supprimer des liens durs ne touche
pas aux fichiers d'origine, qui gardent leur propre lien.
Limites assumées : les liens durs exigent que le titre et le dossier « partages » soient sur le même disque (sinon le partage
est refusé avec un message clair, jamais une copie silencieuse) ; Jellyfin transcode ou non selon l'appareil comme pour tout
compte ; la personne voit les noms de fichiers du titre ; ce n'est pas un contrôle d'accès contre quelqu'un qui enregistrerait
l'écran. Jellyfin 10.9+ attendu (l'accès par médiathèque est ancien, mais n'a pas été essayé ici contre toutes les versions).
    seedboxctl titleshare list | create movie|series ID [--days N] | revoke ID | tick
"""
import json
import os
import re
import secrets
import shutil
import time
import urllib.parse

import seedboxctl as ctl

FILE = ctl.STATE / "titleshares.json"
ROOT_NAME = "partages"
ID = re.compile(r"^[0-9a-f]{8}$")
LIBRARY = re.compile(r"^Partage [0-9a-f]{8}$")
MAX_SHARES = 50
KINDS = {"movie": ("radarr", "movies", "/movie/{id}"), "series": ("sonarr", "tvshows", "/series/{id}")}
MEDIA_PREFIX = "/data/media/"
SKIP_SUFFIXES = (".nfo", ".jpg", ".png", ".txt", ".url", ".sfv", ".nzb")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def load():
    try:
        value = json.loads(FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def store(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = FILE.with_name(FILE.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(temp, FILE)


def media_root():
    return ctl.STORAGE / "media"


def host_path(arr_path, root=None):
    """Chemin vu par Radarr/Sonarr (/data/media/...) -> chemin sur le serveur, sinon erreur : jamais hors des médias."""
    root = root or media_root()
    text = str(arr_path or "")
    if not text.startswith(MEDIA_PREFIX) or ".." in text.split("/"):
        raise ctl.SeedboxError("ce titre n'est pas dans le dossier des médias de ce serveur : partage impossible")
    path = root / text[len(MEDIA_PREFIX):]
    real = path.resolve()
    if not str(real).startswith(str(root.resolve()) + os.sep) or ROOT_NAME in real.relative_to(root.resolve()).parts[:1]:
        raise ctl.SeedboxError("chemin du titre refusé")
    return real


def link_tree(source, target):
    """Liens durs des fichiers de `source` dans `target` (même arborescence). Renvoie le nombre de fichiers liés."""
    count = 0
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink():
            continue
        if path.is_dir():
            (target / relative).mkdir(parents=True, exist_ok=True)
        elif path.is_file() and not path.name.lower().endswith(SKIP_SUFFIXES):
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, target / relative)
            except FileExistsError:
                pass
            except OSError as error:
                if error.errno == 18:                                   # EXDEV : autre système de fichiers
                    raise ctl.SeedboxError("le titre est sur un autre disque que le dossier « partages » : un lien est impossible (aucune copie n'est faite)")
                raise ctl.SeedboxError(f"lien impossible ({error.strerror})")
            count += 1
    if not count:
        raise ctl.SeedboxError("aucun fichier à partager dans ce titre")
    return count


def title_of(kind, ident, api=None):
    app, collection, path = KINDS[kind]
    api = api or ctl.arr_api
    result = api(app, "GET", path.format(id=int(ident)))
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : titre introuvable (HTTP {result.status})")
    data = result.json() or {}
    return {"title": str(data.get("title", ""))[:120], "year": data.get("year", 0), "path": data.get("path", ""), "has_file": bool(data.get("hasFile", True))}


def search(kind, query, api=None):
    """Titres de la bibliothèque (Radarr ou Sonarr) qui ont des fichiers et dont le nom contient la recherche : 20 au plus."""
    if kind not in KINDS:
        raise ctl.SeedboxError("type inconnu : movie ou series")
    app = KINDS[kind][0]
    result = (api or ctl.arr_api)(app, "GET", "/movie" if kind == "movie" else "/series")
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : HTTP {result.status}")
    wanted = str(query or "").strip().lower()
    rows = []
    for item in result.json() or []:
        has_file = item.get("hasFile") if kind == "movie" else (item.get("statistics") or {}).get("episodeFileCount")
        if has_file and wanted in str(item.get("title", "")).lower():
            rows.append({"id": item["id"], "title": str(item.get("title", ""))[:120], "year": item.get("year", 0)})
    rows.sort(key=lambda r: r["title"].lower())
    return rows[:20]


def create(kind, ident, days=7, by="console", api=None, jellyfin=None, guests=None, root=None):
    if kind not in KINDS:
        raise ctl.SeedboxError("type inconnu : movie ou series")
    try:
        ident, days = int(ident), int(days)
    except (TypeError, ValueError):
        raise ctl.SeedboxError("identifiant et durée : des nombres")
    if not 1 <= days <= 30:
        raise ctl.SeedboxError("durée du partage : 1 à 30 jours")
    data = load()
    if len(data) >= MAX_SHARES:
        raise ctl.SeedboxError("trop de partages actifs : révoquez-en avant d'en créer")
    api, jellyfin, guests = api or ctl.arr_api, jellyfin or ctl.jellyfin_api, guests or ctl.guests()
    root = root or media_root()
    info = title_of(kind, ident, api)
    source = host_path(info["path"], root)
    if not source.is_dir():
        raise ctl.SeedboxError("le dossier du titre est introuvable sur le serveur")
    share_id = secrets.token_hex(4)
    folder = root / ROOT_NAME / share_id
    library = f"Partage {share_id}"
    linked = link_tree(source, folder)
    try:
        query = urllib.parse.urlencode({"name": library, "collectionType": KINDS[kind][1], "refreshLibrary": "true"})
        result = jellyfin("POST", f"/Library/VirtualFolders?{query}",
                          {"LibraryOptions": {"PathInfos": [{"Path": f"{MEDIA_PREFIX}{ROOT_NAME}/{share_id}"}], "EnableRealtimeMonitor": False}})
        if not result.ok:
            raise ctl.SeedboxError(f"Jellyfin : médiathèque refusée (HTTP {result.status})")
        invite = guests.create(label=("Partage - " + re.sub(r"[^\w .,'@+-]", " ", info["title"]).strip())[:60], days=days, uses=1,
                               profile={"account_days": days, "downloads": False, "library": library, "max_sessions": 1}, by=by)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        try:
            jellyfin("DELETE", "/Library/VirtualFolders?" + urllib.parse.urlencode({"name": library, "refreshLibrary": "false"}))
        except (ctl.SeedboxError, OSError):
            pass
        raise
    now = int(time.time())
    data[share_id] = {"kind": kind, "app_id": ident, "title": info["title"], "year": info["year"], "library": library, "files": linked,
                      "created": now, "expires": now + (days + 1) * 86400, "invite": invite["id"], "by": by}
    store(data)
    try:
        ctl.audit().record(by, "", "Partage d'un titre créé", info["title"], "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    say(f"partage créé : {info['title']} ({linked} fichier(s), {days} jours)")
    return {"id": share_id, "path": invite["path"], "expires": data[share_id]["expires"], "title": info["title"]}


def remove(share_id, jellyfin=None, guests=None, root=None, now=None):
    data = load()
    entry = data.get(share_id)
    if not entry or not ID.match(share_id):
        raise ctl.SeedboxError("partage inconnu")
    jellyfin, root = jellyfin or ctl.jellyfin_api, root or media_root()
    guests = guests or ctl.guests()
    try:
        guests.revoke(entry["invite"])
        for invite in guests.invites():                             # le compte déjà créé par ce lien disparaît avec le partage
            if invite["id"] == entry["invite"]:
                for login in invite.get("accounts", []):
                    try:
                        ctl.user_del(login)
                    except (ctl.SeedboxError, OSError, ValueError):
                        pass
    except (ctl.SeedboxError, OSError, KeyError):
        pass
    try:
        jellyfin("DELETE", "/Library/VirtualFolders?" + urllib.parse.urlencode({"name": entry["library"], "refreshLibrary": "false"}))
    except (ctl.SeedboxError, OSError):
        pass
    folder = root / ROOT_NAME / share_id
    if folder.is_dir() and folder.parent.name == ROOT_NAME and not folder.is_symlink():
        shutil.rmtree(folder, ignore_errors=True)
    data.pop(share_id, None)
    store(data)
    say(f"partage supprimé : {entry['title']}")
    return {"id": share_id}


def tick(now=None, **kw):
    """Supprime les partages échus (le compte invité a déjà disparu à son échéance)."""
    now = time.time() if now is None else now
    done = 0
    for share_id, entry in list(load().items()):
        if entry.get("expires", 0) < now:
            try:
                remove(share_id, **kw)
                done += 1
            except ctl.SeedboxError as error:
                say(f"partage {share_id} : {error}")
    return done


def restrict_policy(login, library, jellyfin=None):
    """Compte invité d'un partage : accès à cette seule médiathèque, aucune demande Seerr. Renvoie des avertissements."""
    jellyfin = jellyfin or ctl.jellyfin_api
    warnings = []
    if not LIBRARY.match(library or ""):
        return ["médiathèque de partage invalide"]
    user = ctl.jellyfin_user(login)
    if not user:
        return [f"Jellyfin : compte {login} introuvable"]
    folders = {f["Name"]: f for f in jellyfin("GET", "/Library/VirtualFolders").json() or []}
    folder = folders.get(library)
    if not folder:
        return [f"Jellyfin : médiathèque {library} introuvable"]
    policy = user["Policy"]
    policy.update(EnableAllFolders=False, EnabledFolders=[folder["ItemId"]], EnableContentDownloading=False, EnableRemoteControlOfOtherUsers=False)
    result = jellyfin("POST", f"/Users/{user['Id']}/Policy", policy)
    if not result.ok:
        warnings.append(f"Jellyfin : accès à la médiathèque refusé (HTTP {result.status})")
    try:
        seerr = ctl.seerr_user_id(user["Id"])
        if seerr:
            ctl.seerr_api("POST", f"/user/{seerr}/settings/permissions", {"permissions": 0})
    except (ctl.SeedboxError, OSError, ValueError) as error:
        warnings.append(f"Seerr : {error}")
    return warnings


def overview():
    now = time.time()
    rows = [{"id": k, "title": v["title"], "year": v.get("year", 0), "kind": v["kind"], "files": v.get("files", 0), "created": v["created"],
             "expires": v["expires"], "active": v["expires"] >= now} for k, v in sorted(load().items(), key=lambda kv: -kv[1]["created"])]
    return {"shares": rows, "max": MAX_SHARES}
