"""SeedBoxBuntu - relevé des bibliothèques Radarr et Sonarr, partage par partage.

Base commune des analyses de bibliothèque (carte, versions multiples, remplacements, recompression, déplacement...).
Un relevé interroge Radarr (tous les films, fichier compris) et Sonarr (un appel par série pour lister ses fichiers), puis
range pour chaque fichier : titre, taille, résolution, codec, durée, qualité, dossier et « volume » (le disque local ou le
nom du partage réseau). Il est conservé dans /var/lib/seedboxbuntu/libscan.json et rafraîchi chaque nuit en tâche de fond ;
les analyses lisent ce relevé sans retoucher à Radarr ni Sonarr.
Volume : « local » pour /data/..., le nom du partage pour /shares/<nom>/... (même convention que les conteneurs).
Limites assumées : le relevé est celui des applications (un fichier absent de Radarr et Sonarr n'y figure pas) ; le codec et
la durée viennent de leur analyse du fichier (absents tant qu'elle n'a pas eu lieu) ; une série de plus de 400 fichiers est
tronquée à 400 pour garder un relevé raisonnable.
    seedboxctl libscan show | scan
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import seedboxctl as ctl

FILE = ctl.STATE / "libscan.json"
LOCK_FILE = ctl.STATE / "libscan.lock"
EVERY = 22 * 3600
MAX_SERIES = 600
MAX_FILES_PER_SERIES = 400
MAX_RECORDS = 60000


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# --- chemins -------------------------------------------------------------------------------------
def shares_root():
    return Path(os.environ.get("SB_SHARES", "/mnt/shares"))


def to_host(path):
    """Chemin vu par un conteneur -> chemin sur le serveur ; None s'il sort de /data et /shares."""
    text = str(path or "")
    if ".." in text.split("/"):
        return None
    if text.startswith("/data/"):
        return ctl.STORAGE / text[len("/data/"):]
    if text.startswith("/shares/"):
        return shares_root() / text[len("/shares/"):]
    return None


def volume_of(path):
    text = str(path or "")
    if text.startswith("/shares/"):
        parts = text.split("/")
        return parts[2] if len(parts) > 2 and parts[2] else "local"
    return "local"


def root_of(path):
    """Racine « de volume » d'un chemin de conteneur : /data/media ou /shares/<nom>."""
    text = str(path or "")
    if text.startswith("/shares/"):
        parts = text.split("/")
        return "/".join(parts[:3])
    return "/data/media" if text.startswith("/data/media") else "/data"


# --- relevé --------------------------------------------------------------------------------------
def _resolution(file):
    quality = (file.get("quality") or {}).get("quality") or {}
    return int(quality.get("resolution") or 0)


def _record(app, owner_id, title, year, file, extra=None):
    info = file.get("mediaInfo") or {}
    seconds = _seconds(info.get("runTime"))
    path = file.get("path") or ""
    record = {"app": app, "id": owner_id, "title": str(title)[:120], "year": year or 0, "path": path, "volume": volume_of(path),
              "size": int(file.get("size") or 0), "resolution": _resolution(file),
              "quality": ((file.get("quality") or {}).get("quality") or {}).get("name", ""),
              "codec": str(info.get("videoCodec") or "").lower(), "seconds": seconds,
              "audio": str(info.get("audioLanguages") or ""), "subs": str(info.get("subtitles") or ""),
              "file_id": file.get("id", 0), "added": str(file.get("dateAdded") or "")[:10]}
    if extra:
        record.update(extra)
    return record


def _seconds(value):
    """Durée Radarr/Sonarr « 1:52:03 » ou « 00:45:10.5 » -> secondes."""
    match = re.match(r"^(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})", str(value or ""))
    if not match:
        return 0
    days, hours, minutes, seconds = (int(x or 0) for x in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def collect(api=None, progress=None):
    """Relevé complet. Renvoie {records, errors, at}."""
    api = api or ctl.arr_api
    records, errors = [], {}
    try:
        result = api("radarr", "GET", "/movie")
        if not result.ok:
            raise ctl.SeedboxError(f"HTTP {result.status}")
        for movie in result.json() or []:
            file = movie.get("movieFile")
            if file and file.get("size"):
                if not file.get("path"):
                    file = {**file, "path": f"{movie.get('path', '')}/{file.get('relativePath', '')}"}
                records.append(_record("radarr", movie["id"], movie.get("title", ""), movie.get("year"), file,
                                       {"runtime": int(movie.get("runtime") or 0), "root": root_of(movie.get("path"))}))
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        errors["radarr"] = str(error)[:160]
    try:
        result = api("sonarr", "GET", "/series")
        if not result.ok:
            raise ctl.SeedboxError(f"HTTP {result.status}")
        series = [s for s in (result.json() or []) if (s.get("statistics") or {}).get("episodeFileCount")][:MAX_SERIES]
        for index, show in enumerate(series):
            got = api("sonarr", "GET", f"/episodefile?seriesId={show['id']}")
            if not got.ok:
                continue
            for file in (got.json() or [])[:MAX_FILES_PER_SERIES]:
                records.append(_record("sonarr", show["id"], show.get("title", ""), show.get("year"), file,
                                       {"season": file.get("seasonNumber", 0), "root": root_of(show.get("path"))}))
            if progress:
                progress(index + 1, len(series))
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        errors["sonarr"] = str(error)[:160]
    return {"at": int(time.time()), "records": records[:MAX_RECORDS], "errors": errors, "truncated": len(records) > MAX_RECORDS}


def store(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = FILE.with_name(FILE.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    os.replace(temp, FILE)


def load():
    try:
        data = json.loads(FILE.read_text())
        return data if isinstance(data, dict) and isinstance(data.get("records"), list) else {"at": 0, "records": [], "errors": {}}
    except (OSError, ValueError):
        return {"at": 0, "records": [], "errors": {}}


def scan(api=None):
    data = collect(api, progress=lambda i, n: say(f"séries lues : {i}/{n}") if i % 50 == 0 else None)
    if not data["records"] and data["errors"]:
        raise ctl.SeedboxError("relevé impossible : " + " ; ".join(f"{k} ({v})" for k, v in data["errors"].items()))
    store(data)
    say(f"relevé des bibliothèques : {len(data['records'])} fichier(s)")
    return {"files": len(data["records"]), "errors": data["errors"]}


def running():
    try:
        pid = int(LOCK_FILE.read_text())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def due(now=None):
    now = time.time() if now is None else now
    return now - load().get("at", 0) >= EVERY


def tick(now=None, launcher=None):
    """Passage de 5 minutes : relevé nocturne en tâche de fond, sans jamais bloquer la surveillance."""
    if not due(now) or running():
        return False
    local = time.localtime(time.time() if now is None else now)
    if load().get("at", 0) and not 1 <= local.tm_hour < 7:
        return False                                              # un relevé existant se rafraîchit la nuit seulement
    if launcher:
        launcher()
        return True
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "seedboxctl.py"), "libscan", "scan"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ctl.STATE.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(str(process.pid))
    except OSError:
        pass
    return True


def records(kind=None):
    data = load()
    rows = data["records"]
    return [r for r in rows if kind is None or r["app"] == kind]


def volumes_space():
    """{volume: (total, libre)} : disque local et partages montés."""
    result = {}
    try:
        shares = ctl.shares()
        local = shares.space(ctl.STORAGE)
        result["local"] = local
        for name in (shares.load().get("shares") or {}):
            result[name] = shares.space(shares_root() / name)
    except Exception:  # noqa: BLE001 - l'espace est un confort, jamais une condition
        pass
    return result


def link_count(path):
    """Nombre de liens durs d'un fichier du serveur ; 0 si illisible."""
    try:
        return os.stat(path).st_nlink
    except OSError:
        return 0
