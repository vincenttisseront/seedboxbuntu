"""SeedBoxBuntu - assistant de nettoyage : trouve ce qui prend de la place pour rien, avec corbeille.

L'analyse ne supprime rien. Elle liste, par catégorie, ce qui peut partir :

  orphelins   téléchargements que qBittorrent ne suit plus (torrent supprimé), sans copie en bibliothèque
              (aucun lien dur), plus vieux que 30 jours
  partiels    fichiers .!qB / .part abandonnés (plus de 14 jours, aucun torrent actif)
  doublons    même fichier vidéo à deux emplacements de la médiathèque (même taille, mêmes extraits)
  dossiers    dossiers vides
  transcodage fichiers temporaires de Jellyfin (plus de 24 h)
  docker      images Docker devenues inutiles (après une mise à jour)

« Mettre à la corbeille » déplace les éléments dans <stockage>/.corbeille/ (même disque : instantané, rien n'est
perdu). L'espace n'est rendu qu'au vidage de la corbeille, automatique après 14 jours. Chaque élément est
revérifié juste avant d'être déplacé (même taille et même date que lors de l'analyse, toujours dans le stockage,
toujours sans lien dur pour les orphelins). Les téléchargements en cours ne sont jamais proposés.

    seedboxctl cleanup scan | show | trash ID... | restore ID... | empty [--days N] | docker
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import seedboxctl as ctl

SCAN_FILE = ctl.STATE / "cleanup-scan.json"
TRASH_FILE = ctl.STATE / "cleanup-trash.json"
TRASH_NAME = ".corbeille"
TRASH_DAYS = 14
ORPHAN_DAYS = 30
PARTIAL_DAYS = 14
EMPTY_DIR_DAYS = 1
TRANSCODE_HOURS = 24
DUP_MIN_SIZE = 50 * 1024 * 1024
VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".iso"}
PARTIAL_SUFFIXES = (".!qb", ".part", ".parts", ".!ut")
LIMIT_ITEMS = 400
ID_RE = re.compile(r"^[0-9a-f]{12}$")
CATEGORIES = {
    "orphan": ("Téléchargements orphelins", "Plus suivis par qBittorrent et sans copie en bibliothèque", True),
    "partial": ("Fichiers partiels abandonnés", "Téléchargements interrompus depuis longtemps", True),
    "duplicate": ("Doublons de la médiathèque", "Même fichier à deux emplacements : une copie est proposée", True),
    "empty_dir": ("Dossiers vides", "Restes de dossiers après un déplacement", True),
    "transcode": ("Transcodage temporaire", "Fichiers temporaires de Jellyfin", False),
    "docker": ("Images Docker inutiles", "Anciennes versions d'images après une mise à jour", False),
}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.new")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def item_id(category, path):
    return hashlib.sha1(f"{category}:{path}".encode()).hexdigest()[:12]


def roots():
    return {"downloads": ctl.STORAGE / "downloads", "media": ctl.STORAGE / "media"}


def inside(path, base):
    """path (sans suivre de lien) est-il dans base ? Les liens symboliques sont refusés."""
    try:
        if Path(path).is_symlink():
            return False
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except (ValueError, OSError):
        return False


def tree_size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    total = 0
    for sub in path.rglob("*"):
        try:
            if sub.is_file() and not sub.is_symlink():
                total += sub.stat().st_size
        except OSError:
            pass
    return total


def tree_files(path):
    path = Path(path)
    if path.is_file():
        return [path]
    found = []
    for sub in path.rglob("*"):
        try:
            if sub.is_file() and not sub.is_symlink():
                found.append(sub)
        except OSError:
            pass
    return found


def active_torrent_paths():
    """Chemins (sur l'hôte) des contenus que qBittorrent suit ; None si qBittorrent est injoignable."""
    try:
        if not ctl.container_running("qbittorrent"):
            return None
        cookie = ctl.qbit_login()
        base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"
        torrents = ctl.request("GET", base + "/torrents/info", headers={"Cookie": cookie}, timeout=30).json() or []
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        return None
    paths = set()
    for torrent in torrents:
        for key in ("content_path", "save_path"):
            value = str(torrent.get(key) or "")
            if key == "content_path" and value.startswith("/data/"):
                paths.add(ctl.STORAGE / value[len("/data/"):])
        if torrent.get("save_path") and torrent.get("name") and str(torrent["save_path"]).startswith("/data/"):
            paths.add(ctl.STORAGE / str(torrent["save_path"])[len("/data/"):].strip("/") / torrent["name"])
    return paths


def tracked(path, torrent_paths):
    path = Path(path)
    return any(path == t or t in path.parents or path in t.parents for t in torrent_paths)


def entry(category, path, size, reason, **extra):
    stat = Path(path).stat()
    return {"id": item_id(category, str(path)), "category": category, "path": str(path), "name": Path(path).name, "size": size,
            "mtime": int(stat.st_mtime), "reason": reason, **extra}


CATEGORY_DIRS = {"radarr", "sonarr", "lidarr", "readarr", "prowlarr", "autobrr", "cross-seed", "tv", "movies", "music", "books",
                 "complete", "incomplete", "torrents", "films", "series", "manual", "tv-sonarr", "radarr-movies"}


def units(base, torrent_paths):
    """Éléments candidats : entrées du dossier de téléchargement, ou contenu des dossiers de catégorie (radarr, sonarr...)."""
    result = []
    for top in sorted(base.iterdir()):
        if top.name.startswith(".") or top.is_symlink():
            continue
        holds_torrents = any(top in t.parents for t in torrent_paths)
        if top.is_dir() and (holds_torrents or top.name.lower() in CATEGORY_DIRS):
            result += [sub for sub in sorted(top.iterdir()) if not sub.name.startswith(".") and not sub.is_symlink()]
        else:
            result.append(top)
    return result


def scan_orphans_and_partials(torrent_paths, now, orphan_days, partial_days):
    items = []
    base = roots()["downloads"]
    if not base.is_dir():
        return items
    for unit in units(base, torrent_paths):
        try:
            files = tree_files(unit)
            age = now - max([f.stat().st_mtime for f in files] or [unit.stat().st_mtime])
        except OSError:
            continue
        if tracked(unit, torrent_paths):
            continue
        if unit.is_file() and unit.name.lower().endswith(PARTIAL_SUFFIXES):
            if age > partial_days * 86400:
                items.append(entry("partial", unit, unit.stat().st_size, f"fichier partiel inactif depuis {int(age // 86400)} jours"))
            continue
        if not files or any(f.stat().st_nlink > 1 for f in files):
            continue                                          # vide, ou copie en bibliothèque : supprimer ici ne libère rien
        partials = [f for f in files if f.name.lower().endswith(PARTIAL_SUFFIXES)]
        if partials and len(partials) == len(files) and age > partial_days * 86400:
            items.append(entry("partial", unit, tree_size(unit), f"téléchargement abandonné depuis {int(age // 86400)} jours"))
        elif age > orphan_days * 86400:
            items.append(entry("orphan", unit, tree_size(unit), f"plus suivi par qBittorrent, inactif depuis {int(age // 86400)} jours"))
    return items


def sample_hash(path, size):
    digest = hashlib.sha256(str(size).encode())
    block = 4 * 1024 * 1024
    with open(path, "rb") as handle:
        for position in (0, max(0, size // 2 - block // 2), max(0, size - block)):
            handle.seek(position)
            digest.update(handle.read(block))
    return digest.hexdigest()


def scan_duplicates():
    base = roots()["media"]
    if not base.is_dir():
        return []
    by_size = {}
    for path in base.rglob("*"):
        try:
            if path.suffix.lower() in VIDEO and path.is_file() and not path.is_symlink():
                info = path.stat()
                if info.st_size >= DUP_MIN_SIZE:
                    by_size.setdefault(info.st_size, []).append((path, info))
        except OSError:
            continue
    items = []
    for size, group in by_size.items():
        if len(group) < 2:
            continue
        by_hash = {}
        for path, info in group:
            try:
                by_hash.setdefault(sample_hash(path, size), []).append((path, info))
            except OSError:
                continue
        for same in by_hash.values():
            distinct = {}
            for path, info in sorted(same, key=lambda pi: (len(str(pi[0])), str(pi[0]))):
                distinct.setdefault((info.st_dev, info.st_ino), (path, info))
            if len(distinct) < 2:
                continue                                      # simples liens durs : un seul fichier sur le disque
            ordered = sorted(distinct.values(), key=lambda pi: (pi[1].st_mtime, len(str(pi[0]))))
            keep = ordered[0][0]
            for path, info in ordered[1:]:
                items.append(entry("duplicate", path, size, f"identique à {keep.name}", keep=str(keep)))
    return items


def scan_empty_dirs(now):
    items = []
    top_level = set(roots().values()) | {p for r in roots().values() if r.is_dir() for p in r.iterdir() if p.is_dir()}
    for base in roots().values():
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            try:
                if (path.is_dir() and not path.is_symlink() and path not in top_level and not any(path.iterdir())
                        and now - path.stat().st_mtime > EMPTY_DIR_DAYS * 86400 and not path.name.startswith(".")):
                    items.append(entry("empty_dir", path, 0, "dossier vide"))
            except OSError:
                continue
    return items


def scan_transcodes(now):
    folder = ctl.CONFIG / "jellyfin" / "transcodes"
    items = []
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            try:
                if now - path.stat().st_mtime > TRANSCODE_HOURS * 3600:
                    items.append(entry("transcode", path, tree_size(path), "fichier temporaire de transcodage"))
            except OSError:
                continue
    return items


def docker_dangling():
    if not shutil.which("docker"):
        return []
    result = subprocess.run(["docker", "image", "ls", "-f", "dangling=true", "--format", "{{.ID}}|{{.Size}}"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return []
    sizes = ctl.sysinfo().size_to_bytes
    rows = [line.split("|", 1) for line in result.stdout.splitlines() if "|" in line]
    total = sum(sizes(size) for _, size in rows)
    if not rows:
        return []
    return [{"id": item_id("docker", "images"), "category": "docker", "path": "docker", "name": f"{len(rows)} image(s)",
             "size": total, "mtime": int(time.time()), "reason": "images sans étiquette (anciennes versions)"}]


def scan(orphan_days=ORPHAN_DAYS, partial_days=PARTIAL_DAYS):
    """Analyse complète ; le résultat est mémorisé (rien n'est supprimé)."""
    now = time.time()
    torrent_paths = active_torrent_paths()
    items, notes = [], []
    if torrent_paths is None:
        notes.append("qBittorrent injoignable : orphelins et fichiers partiels ne sont pas analysés (impossible de savoir ce qui est actif)")
    else:
        items += scan_orphans_and_partials(torrent_paths, now, orphan_days, partial_days)
    items += scan_duplicates() + scan_empty_dirs(now) + scan_transcodes(now) + docker_dangling()
    result = {"scanned": int(now), "notes": notes, "items": items}
    write_json(SCAN_FILE, result)
    say(f"analyse terminée : {len(items)} élément(s), {sum(i['size'] for i in items) // (1 << 20)} Mo récupérables")
    return result


# --- corbeille ---------------------------------------------------------------------------------
def trash_dir():
    return ctl.STORAGE / TRASH_NAME


def trash_load():
    data = read_json(TRASH_FILE, [])
    return data if isinstance(data, list) else []


def revalidate(item):
    """L'élément est-il encore ce que l'analyse a vu ? Lève SeedboxError sinon."""
    path = Path(item["path"])
    category = item["category"]
    allowed = list(roots().values()) + ([ctl.CONFIG / "jellyfin" / "transcodes"] if category == "transcode" else [])
    if not path.exists() or path.is_symlink():
        raise ctl.SeedboxError(f"{item['name']} n'existe plus")
    if not any(inside(path, base) and Path(path).resolve() != Path(base).resolve() for base in allowed):
        raise ctl.SeedboxError(f"{item['name']} : en dehors des dossiers nettoyables")
    if category == "empty_dir":
        if not path.is_dir() or any(path.iterdir()):
            raise ctl.SeedboxError(f"{item['name']} n'est plus vide")
        return
    if int(path.stat().st_mtime) != item["mtime"] and category in ("duplicate", "partial"):
        raise ctl.SeedboxError(f"{item['name']} a changé depuis l'analyse")
    if tree_size(path) != item["size"]:
        raise ctl.SeedboxError(f"{item['name']} a changé depuis l'analyse")
    if category == "orphan" and any(f.stat().st_nlink > 1 for f in tree_files(path)):
        raise ctl.SeedboxError(f"{item['name']} est maintenant lié à la bibliothèque")
    if category == "duplicate":
        keep = Path(item.get("keep", ""))
        if not keep.is_file() or os.path.samefile(keep, path):
            raise ctl.SeedboxError(f"{item['name']} : la copie à conserver a disparu")


def selected(ids):
    wanted = [i for i in ids if ID_RE.match(str(i))]
    if len(wanted) != len(ids) or not wanted:
        raise ctl.SeedboxError("identifiants invalides")
    items = {i["id"]: i for i in read_json(SCAN_FILE).get("items", [])}
    missing = [i for i in wanted if i not in items]
    if missing:
        raise ctl.SeedboxError("élément inconnu : relancez l'analyse")
    return [items[i] for i in wanted]


def trash(ids):
    """Déplace les éléments vers la corbeille (ou les supprime pour les catégories temporaires)."""
    moved, freed, errors = [], 0, []
    log = trash_load()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for item in selected(ids):
        try:
            if item["category"] == "docker":
                docker_prune()
                moved.append(item["id"])
                freed += item["size"]
                continue
            revalidate(item)
            path = Path(item["path"])
            if item["category"] == "transcode":
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                moved.append(item["id"])
                freed += item["size"]
                continue
            target = trash_dir() / stamp / item["id"] / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, target) if path.stat().st_dev == target.parent.stat().st_dev else shutil.move(str(path), str(target))
            log.append({"id": item["id"], "original": str(path), "trash": str(target), "size": item["size"], "at": int(time.time()),
                        "category": item["category"], "name": item["name"]})
            moved.append(item["id"])
            freed += item["size"]
        except (ctl.SeedboxError, OSError) as error:
            errors.append(f"{item['name']} : {error}")
    write_json(TRASH_FILE, log)
    scan_data = read_json(SCAN_FILE)
    scan_data["items"] = [i for i in scan_data.get("items", []) if i["id"] not in moved]
    write_json(SCAN_FILE, scan_data)
    say(f"{len(moved)} élément(s) traité(s), {freed // (1 << 20)} Mo" + (f" ; {len(errors)} refusé(s)" if errors else ""))
    for error in errors:
        say("refusé : " + error)
    if errors and not moved:
        raise ctl.SeedboxError("; ".join(errors[:3]))
    return {"moved": moved, "errors": errors}


def docker_prune():
    result = subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise ctl.SeedboxError("docker image prune : " + (result.stderr or "échec").strip()[-160:])


def restore(ids):
    log = trash_load()
    restored, errors = [], []
    for ident in ids:
        if not ID_RE.match(str(ident)):
            raise ctl.SeedboxError("identifiants invalides")
        record = next((r for r in log if r["id"] == ident), None)
        if not record:
            errors.append(f"{ident} : introuvable dans la corbeille")
            continue
        original, stored = Path(record["original"]), Path(record["trash"])
        try:
            if not inside(original.parent, ctl.STORAGE) or not stored.exists():
                raise ctl.SeedboxError("emplacement d'origine ou copie indisponible")
            if original.exists():
                raise ctl.SeedboxError("l'emplacement d'origine est occupé")
            original.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stored, original)
            log = [r for r in log if r["id"] != ident]
            restored.append(ident)
            try:
                stored.parent.rmdir()
                stored.parent.parent.rmdir()
            except OSError:
                pass
        except (ctl.SeedboxError, OSError) as error:
            errors.append(f"{record['name']} : {error}")
    write_json(TRASH_FILE, log)
    say(f"{len(restored)} élément(s) remis en place")
    for error in errors:
        say("refusé : " + error)
    if errors and not restored:
        raise ctl.SeedboxError("; ".join(errors[:3]))
    return {"restored": restored, "errors": errors}


def empty(days=0):
    """Vide la corbeille (éléments de plus de `days` jours ; 0 : tout)."""
    log, kept, freed, removed = trash_load(), [], 0, 0
    limit = time.time() - days * 86400
    for record in log:
        if days and record["at"] > limit:
            kept.append(record)
            continue
        target = Path(record["trash"])
        if inside(target, trash_dir()) or target.exists():
            if not str(target).startswith(str(trash_dir()) + os.sep):
                kept.append(record)                           # chemin inattendu : jamais supprimé
                continue
            shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink(missing_ok=True)
        freed += record["size"]
        removed += 1
        for parent in (target.parent, target.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                pass
    write_json(TRASH_FILE, kept)
    if removed:
        say(f"corbeille : {removed} élément(s) supprimé(s) définitivement, {freed // (1 << 20)} Mo rendus")
    return {"removed": removed, "freed": freed}


def purge_expired():
    """Appelée par l'actualisation horaire : la corbeille garde les éléments TRASH_DAYS jours."""
    if trash_load():
        return empty(TRASH_DAYS)
    return {"removed": 0, "freed": 0}


def overview():
    data = read_json(SCAN_FILE)
    items = data.get("items", [])
    categories = []
    for key, (label, hint, trashable) in CATEGORIES.items():
        rows = [i for i in items if i["category"] == key]
        categories.append({"id": key, "label": label, "hint": hint, "trashable": trashable, "count": len(rows),
                           "size": sum(i["size"] for i in rows),
                           "items": sorted(rows, key=lambda i: -i["size"])[:LIMIT_ITEMS]})
    log = trash_load()
    return {"scanned": data.get("scanned"), "notes": data.get("notes", []), "categories": categories,
            "total": sum(i["size"] for i in items), "trash": {"count": len(log), "size": sum(r["size"] for r in log), "days": TRASH_DAYS,
                                                               "items": sorted(log, key=lambda r: -r["at"])[:LIMIT_ITEMS]}}
