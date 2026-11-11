"""SeedBoxBuntu - importer une autre seedbox : bibliothèques Radarr/Sonarr/… et torrents de qBittorrent.

Principe : l'ancienne seedbox fournit ses sauvegardes, déposées dans le dossier « import » du stockage
(<stockage>/import, atteignable par le partage réseau ou SFTP). L'assistant les trouve, les décrit, puis,
sur demande, les installe en gardant ce qui fait fonctionner CETTE seedbox.

  Radarr, Sonarr, Lidarr, Readarr, Prowlarr
      la sauvegarde de l'application (radarr_backup_….zip) ; seule la base de données est reprise :
      bibliothèque, historique, profils, indexeurs. La clé d'API, le port, l'authentification restent
      ceux d'ici (config.xml n'est pas touché). Les chemins de médias sont convertis selon la table
      « ancien dossier -> nouveau dossier ». Ensuite les liaisons de l'appliance (qBittorrent, Jellyfin,
      Prowlarr) sont refaites. Si l'application ne redémarre pas avec la nouvelle base, l'ancienne revient.
  qBittorrent
      le dossier BT_backup (fichiers .torrent et .fastresume) : les torrents sont ajoutés, avec les chemins
      convertis, EN PAUSE : à vous de les reprendre après avoir copié les données et vérifié les chemins.
      Un torrent déjà présent n'est jamais remplacé.

Une sauvegarde complète du serveur est faite avant toute installation. Les données elles-mêmes (films, séries,
fichiers des torrents) se copient avec le partage réseau, vers l'endroit choisi dans la table des chemins.

    seedboxctl import scan | show | run ID... [--map ANCIEN=NOUVEAU ...] [--dry-run] | clear
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

import seedboxctl as ctl

IMPORT_DIR = ctl.STORAGE / "import"
SCAN_FILE = ctl.STATE / "import-scan.json"
HISTORY_FILE = ctl.STATE / "import-history.json"
MAX_DEPTH = 4
MAX_DB = 4 * 1024 ** 3
MAX_TORRENTS = 20000
MAX_ZIP_ENTRIES = 60000
ARR = {   # application -> (port, point de contrôle, tables qui comptent, tables dont la colonne Path est convertie)
    "radarr": (7878, "/radarr/ping", "Movies", ("Movies", "RootFolders")),
    "sonarr": (8989, "/sonarr/ping", "Series", ("Series", "RootFolders")),
    "lidarr": (8686, "/lidarr/ping", "Artists", ("Artists", "RootFolders")),
    "readarr": (8787, "/readarr/ping", "Authors", ("Authors", "RootFolders")),
    "prowlarr": (9696, "/prowlarr/ping", "Indexers", ()),
}
ARR_ZIP = re.compile(r"^(radarr|sonarr|lidarr|readarr|prowlarr)_backup_.*\.zip$", re.I)
ID_RE = re.compile(r"^(arr:(radarr|sonarr|lidarr|readarr|prowlarr)|qbit)$")
MAP_RE = re.compile(r"^[^\x00\n]{1,300}$")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# --- chemins ---------------------------------------------------------------------------------------------
def normalize(path):
    path = path.replace("\\", "/")
    return path.rstrip("/") if len(path) > 1 else path


def clean_map(pairs):
    """[(ancien, nouveau)] validé. Un dossier ancien ne peut pas être vide ni « / » ; le nouveau est absolu."""
    result = []
    for pair in pairs or []:
        old, new = (pair if isinstance(pair, (list, tuple)) else (pair.get("from", ""), pair.get("to", "")))
        old, new = str(old).strip(), str(new).strip()
        if not old and not new:
            continue
        if not MAP_RE.match(old) or not MAP_RE.match(new):
            raise ctl.SeedboxError("table des chemins invalide")
        old, new = normalize(old), normalize(new)
        if old in ("", "/") or re.fullmatch(r"[A-Za-z]:", old):
            raise ctl.SeedboxError("l'ancien dossier ne peut pas être la racine : indiquez un dossier précis")
        if not new.startswith("/") or ".." in new.split("/"):
            raise ctl.SeedboxError(f"le nouveau dossier doit être un chemin absolu du serveur ({new})")
        result.append((old, new))
    return sorted(result, key=lambda p: len(p[0]), reverse=True)     # le préfixe le plus long d'abord


def remap(path, mapping):
    """Nouveau chemin, ou None si aucun préfixe ne correspond."""
    flat = normalize(path)
    for old, new in mapping:
        if flat == old or flat.startswith(old + "/"):
            return new + flat[len(old):]
        if re.match(r"^[A-Za-z]:", old) and flat.lower().startswith(old.lower() + "/"):
            return new + flat[len(old):]
    return None


# --- bencode (fichiers .fastresume) ----------------------------------------------------------------------------
def bdecode(data, pos=0):
    char = data[pos:pos + 1]
    if char == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if char == b"l":
        pos += 1
        items = []
        while data[pos:pos + 1] != b"e":
            value, pos = bdecode(data, pos)
            items.append(value)
        return items, pos + 1
    if char == b"d":
        pos += 1
        items = {}
        while data[pos:pos + 1] != b"e":
            key, pos = bdecode(data, pos)
            value, pos = bdecode(data, pos)
            items[key] = value
        return items, pos + 1
    if char.isdigit():
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        start = colon + 1
        if start + length > len(data):
            raise ValueError("bencode tronqué")
        return data[start:start + length], start + length
    raise ValueError("bencode invalide")


def bencode(value):
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b"e"
    raise ValueError("type inattendu")


def load_resume(data):
    value, end = bdecode(data)
    if end != len(data) or not isinstance(value, dict):
        raise ValueError("fichier de reprise invalide")
    return value


PATH_KEYS = (b"save_path", b"qBt-savePath", b"qBt-downloadPath")


def convert_resume(data, mapping, pause=True):
    """(octets convertis, chemins non convertis). Les chemins vides sont ignorés."""
    resume = load_resume(data)
    unmatched = []
    for key in PATH_KEYS:
        raw = resume.get(key)
        if not isinstance(raw, bytes) or not raw:
            continue
        text = raw.decode("utf-8", "replace")
        new = remap(text, mapping)
        if new is None:
            unmatched.append(text)
        else:
            resume[key] = new.encode()
    if pause:
        resume[b"paused"] = 1
        resume[b"auto_managed"] = 0
        resume[b"stopped"] = 1
    return bencode(resume), unmatched


# --- archives ---------------------------------------------------------------------------------------------------------
def walk(folder, depth=0):
    """Fichiers et dossiers jusqu'à MAX_DEPTH, sans suivre de lien symbolique."""
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_symlink():
            continue
        yield entry
        if entry.is_dir() and depth < MAX_DEPTH:
            yield from walk(entry, depth + 1)


def safe_member(name):
    parts = name.replace("\\", "/").split("/")
    return not name.startswith(("/", "\\")) and ".." not in parts and "\x00" not in name


def read_db_info(path, app):
    """Quelques chiffres et les dossiers de médias d'une base, lue en lecture seule."""
    info = {"count": 0, "folders": [], "clients": [], "valid": False}
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return info
    try:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return info
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        count_table = ARR[app][2]
        if count_table in tables:
            info["count"] = db.execute(f'SELECT COUNT(*) FROM "{count_table}"').fetchone()[0]
        if "RootFolders" in tables:
            info["folders"] = [r[0] for r in db.execute("SELECT Path FROM RootFolders") if r[0]][:20]
        if "DownloadClients" in tables:
            info["clients"] = [r[0] for r in db.execute("SELECT Name FROM DownloadClients")][:10]
        info["valid"] = bool(tables)
    except sqlite3.Error:
        info["valid"] = False
    finally:
        db.close()
    return info


def extract_db(zip_path, app, workdir):
    """Extrait <app>.db de la sauvegarde. Renvoie le chemin, ou None."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if len(archive.namelist()) > MAX_ZIP_ENTRIES:
                return None
            for member in archive.infolist():
                if member.filename.lower() == f"{app}.db" and safe_member(member.filename) and 0 < member.file_size <= MAX_DB:
                    target = Path(workdir) / f"{app}.db"
                    with archive.open(member) as source, open(target, "wb") as out:
                        shutil.copyfileobj(source, out, 1 << 20)
                    return target
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def qbit_sources(folder):
    """Dossiers BT_backup (ou archives zip qui en contiennent un) sous le dossier d'import."""
    found = []
    for entry in walk(folder):
        if entry.is_dir() and entry.name == "BT_backup":
            files = [f for f in entry.iterdir() if f.is_file() and f.suffix in (".fastresume", ".torrent")]
            if files:
                found.append({"kind": "dir", "path": entry, "torrents": len([f for f in files if f.suffix == ".torrent"])})
        elif entry.is_file() and entry.suffix.lower() == ".zip" and not ARR_ZIP.match(entry.name):
            try:
                with zipfile.ZipFile(entry) as archive:
                    names = [n for n in archive.namelist() if "/BT_backup/" in "/" + n and n.endswith(".torrent") and safe_member(n)]
            except (zipfile.BadZipFile, OSError):
                continue
            if names:
                found.append({"kind": "zip", "path": entry, "torrents": len(names)})
    return found


def scan():
    """Cherche ce qui peut être importé dans le dossier d'import et enregistre le résultat."""
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    newest = {}
    for entry in walk(IMPORT_DIR):
        match = ARR_ZIP.match(entry.name) if entry.is_file() else None
        if match:
            app = match.group(1).lower()
            if app not in newest or entry.stat().st_mtime > newest[app].stat().st_mtime:
                newest[app] = entry
    running = ctl.containers()
    for app in ARR:
        if app not in newest:
            continue
        zip_path = newest[app]
        item = {"id": f"arr:{app}", "type": "arr", "app": app, "file": str(zip_path.relative_to(IMPORT_DIR)),
                "size": zip_path.stat().st_size, "date": int(zip_path.stat().st_mtime), "installed": app in running}
        with tempfile.TemporaryDirectory(prefix="sbimp") as work:
            db = extract_db(zip_path, app, work)
            info = read_db_info(db, app) if db else {"valid": False}
        item.update({"count": info.get("count", 0), "folders": info.get("folders", []), "clients": info.get("clients", []),
                     "valid": bool(info.get("valid"))})
        if not item["valid"]:
            item["problem"] = "base de données absente ou illisible dans cette sauvegarde"
        elif not item["installed"]:
            item["problem"] = f"{app} n'est pas installé sur ce serveur"
        items.append(item)
    sources = qbit_sources(IMPORT_DIR)
    if sources:
        best = max(sources, key=lambda s: s["torrents"])
        items.append({"id": "qbit", "type": "qbit", "app": "qbittorrent", "file": str(best["path"].relative_to(IMPORT_DIR)),
                      "kind": best["kind"], "torrents": best["torrents"], "valid": True, "installed": "qbittorrent" in running,
                      "size": 0, "date": int(best["path"].stat().st_mtime)})
        if "qbittorrent" not in running:
            items[-1]["problem"] = "qBittorrent n'est pas installé sur ce serveur"
    result = {"scanned": int(time.time()), "folder": str(IMPORT_DIR), "items": items, "targets": targets()}
    SCAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCAN_FILE.write_text(json.dumps(result, ensure_ascii=False))
    say(f"import : {len(items)} élément(s) trouvé(s) dans {IMPORT_DIR}")
    return result


def targets():
    """Dossiers de médias de CE serveur, pour remplir la table des chemins."""
    try:
        return {role: ctl.shares().location(role) for role in ("movies", "tv", "music", "books", "downloads") if ctl.shares().location(role)}
    except (ctl.SeedboxError, OSError, KeyError, AttributeError):
        return {}


# --- installation -------------------------------------------------------------------------------------------------------
def own(path):
    env = ctl.env_read()
    try:
        os.chown(path, int(env.get("PUID", 1000)), int(env.get("PGID", 1000)))
    except (OSError, ValueError):
        pass


def convert_db(path, app, mapping):
    """Convertit les chemins de médias d'une copie de base. Renvoie le nombre de lignes converties."""
    changed = 0
    db = sqlite3.connect(path)
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with db:
            for table in ARR[app][3]:
                if table not in tables or "Path" not in {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}:
                    continue
                for row_id, value in db.execute(f'SELECT Id, Path FROM "{table}"').fetchall():
                    new = remap(value or "", mapping) if value else None
                    if new is not None:
                        db.execute(f'UPDATE "{table}" SET Path = ? WHERE Id = ?', (new, row_id))
                        changed += 1
    finally:
        db.close()
    return changed


def import_arr(item, mapping, stamp, dry):
    app = item["app"]
    port, ping = ARR[app][0], ARR[app][1]
    zip_path = IMPORT_DIR / item["file"]
    live = ctl.CONFIG / app / f"{app}.db"
    with tempfile.TemporaryDirectory(prefix="sbimp") as work:
        db = extract_db(zip_path, app, work)
        if not db or not read_db_info(db, app)["valid"]:
            raise ctl.SeedboxError(f"{app} : sauvegarde illisible")
        changed = convert_db(db, app, mapping)
        if dry:
            return [f"{app} : {item['count']} élément(s), {changed} chemin(s) converti(s) (simulation)"]
        if not live.parent.is_dir():
            raise ctl.SeedboxError(f"{app} : dossier de configuration introuvable")
        saved = live.parent / f"avant-import-{stamp}"
        saved.mkdir(parents=True, exist_ok=True)
        say(f"{app} : arrêt")
        ctl.compose("stop", app, check=False)
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(live) + suffix)
            if source.exists():
                shutil.move(source, saved / source.name)
        shutil.copyfile(db, live)
        own(live)
        own(saved)
        say(f"{app} : démarrage avec la base importée")
        ctl.compose("start", app, check=False)
        try:
            ctl.wait_http(app, port, ping, timeout=240)
        except ctl.SeedboxError:
            say(f"{app} : ne redémarre pas avec cette base, retour à l'ancienne")
            ctl.compose("stop", app, check=False)
            for suffix in ("", "-wal", "-shm"):
                Path(str(live) + suffix).unlink(missing_ok=True)
                kept = saved / (live.name + suffix)
                if kept.exists():
                    shutil.move(kept, Path(str(live) + suffix))
            ctl.compose("start", app, check=False)
            raise ctl.SeedboxError(f"{app} : la base importée est refusée par cette version (plus récente ?) ; rien n'a été changé")
    return [f"{app} : {item['count']} élément(s) importé(s), {changed} chemin(s) converti(s) ; ancienne base gardée dans {saved.name}"]


def torrent_files(item):
    """Itère (nom, octets) des fichiers .torrent/.fastresume de la source choisie."""
    source = IMPORT_DIR / item["file"]
    if item["kind"] == "dir":
        for entry in sorted(source.iterdir()):
            if entry.is_file() and entry.suffix in (".fastresume", ".torrent") and entry.stat().st_size < 64 * 1024 ** 2:
                yield entry.name, entry.read_bytes()
        return
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            name = member.filename
            if safe_member(name) and "/BT_backup/" in "/" + name and name.endswith((".fastresume", ".torrent")) and member.file_size < 64 * 1024 ** 2:
                yield name.rsplit("/", 1)[-1], archive.read(member)


def import_qbit(item, mapping, stamp, dry):
    folder = ctl.CONFIG / "qbittorrent" / "qBittorrent" / "BT_backup"
    files = dict(torrent_files(item))
    hashes = sorted({n.rsplit(".", 1)[0] for n in files if n.endswith(".torrent")})
    if len(hashes) > MAX_TORRENTS:
        raise ctl.SeedboxError("trop de torrents dans cette sauvegarde")
    added, skipped, broken, unmatched = 0, 0, 0, set()
    plan = []
    for h in hashes:
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", h):
            broken += 1
            continue
        resume, torrent = files.get(h + ".fastresume"), files[h + ".torrent"]
        if resume is None:
            broken += 1
            continue
        try:
            load_resume(torrent)
            converted, bad = convert_resume(resume, mapping)
        except (ValueError, IndexError):
            broken += 1
            continue
        unmatched.update(bad)
        if (folder / f"{h}.torrent").exists():
            skipped += 1
            continue
        plan.append((h, torrent, converted))
        added += 1
    notes = [f"qBittorrent : {added} torrent(s) {'à ajouter' if dry else 'ajouté(s) en pause'}, {skipped} déjà présent(s), {broken} ignoré(s) (fichier incomplet)"]
    if unmatched:
        sample = ", ".join(sorted(unmatched)[:3])
        notes.append(f"qBittorrent : {len(unmatched)} dossier(s) sans correspondance dans la table des chemins (ex. {sample}) ; ces torrents garderont leur ancien chemin")
    if dry or not plan:
        return notes
    service = ctl.qbit_service()
    say("qBittorrent : arrêt")
    ctl.compose("stop", service, check=False)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for h, torrent, converted in plan:
            for name, data in ((f"{h}.torrent", torrent), (f"{h}.fastresume", converted)):
                (folder / name).write_bytes(data)
                own(folder / name)
        own(folder)
    finally:
        say("qBittorrent : démarrage")
        ctl.compose("start", service, check=False)
    return notes


def run(ids, pairs=None, dry=False):
    """Installe les éléments choisis. Renvoie la liste des remarques."""
    ids = list(ids or [])
    if not ids or any(not ID_RE.match(i) for i in ids):
        raise ctl.SeedboxError("élément à importer invalide")
    mapping = clean_map(pairs)
    current = scan()
    chosen = []
    for identifier in ids:
        item = next((i for i in current["items"] if i["id"] == identifier), None)
        if not item:
            raise ctl.SeedboxError(f"{identifier} : introuvable dans le dossier d'import")
        if item.get("problem"):
            raise ctl.SeedboxError(f"{identifier} : {item['problem']}")
        chosen.append(item)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if not dry:
        say("sauvegarde complète du serveur avant l'import")
        ctl.backup().create("avantimport")
    notes = []
    arr_done = False
    for item in chosen:
        if item["type"] == "qbit":
            notes += import_qbit(item, mapping, stamp, dry)
        else:
            notes += import_arr(item, mapping, stamp, dry)
            arr_done = arr_done or not dry
    if arr_done:
        say("liaisons entre applications")
        for hook in (ctl.provision_arr, ctl.provision_apps):
            try:
                hook()
            except Exception as error:  # noqa: BLE001 - une liaison manquée ne défait pas l'import
                notes.append(f"liaisons : {error} (elles se refont avec « seedboxctl upgrade »)")
    if not dry:
        history()
        entries = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
        entries.append({"date": int(time.time()), "items": ids, "map": [list(p) for p in mapping], "notes": notes})
        HISTORY_FILE.write_text(json.dumps(entries[-30:], ensure_ascii=False))
    for note in notes:
        say(note)
    return notes


def history():
    try:
        return json.loads(HISTORY_FILE.read_text())
    except (OSError, ValueError):
        return []


def clear():
    """Vide le dossier d'import (après un import réussi) : les sauvegardes d'origine disparaissent."""
    removed = 0
    if IMPORT_DIR.is_dir():
        for entry in IMPORT_DIR.iterdir():
            shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
            removed += 1
    SCAN_FILE.unlink(missing_ok=True)
    say(f"dossier d'import vidé ({removed} élément(s))")
    return removed


def overview():
    try:
        last = json.loads(SCAN_FILE.read_text())
    except (OSError, ValueError):
        last = {"scanned": 0, "folder": str(IMPORT_DIR), "items": [], "targets": targets()}
    return dict(last, history=history()[-5:], apps=sorted(ARR))
