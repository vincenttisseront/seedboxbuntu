"""SeedBoxBuntu - audit des copies cachées : un fichier présent deux fois (téléchargements et médiathèque) qui pourrait n'occuper la place qu'une fois.

Quand Radarr ou Sonarr importent un téléchargement par COPIE au lieu d'un lien dur (dossiers sur deux disques, ou réglage
« utiliser les liens durs » désactivé), le même film occupe la place deux fois : une dans « téléchargements terminés » (pour
le partage torrent) et une dans la médiathèque. Cet audit les repère : même taille, même contenu, même disque, fichiers distincts.
« Relier » remplace alors la copie des téléchargements par un lien dur vers le fichier de la médiathèque : le torrent continue de
partager le même contenu, l'espace de la copie est récupéré, rien n'est perdu.
Garanties : le fichier de la médiathèque n'est jamais modifié ni supprimé ; avant de relier, les deux fichiers sont comparés
octet par octet ; le lien est créé sous un nom provisoire puis posé d'un seul geste (jamais de fichier manquant, même si la machine
s'arrête en cours). Deux disques différents : rien n'est fait (un lien dur n'y est pas possible).
Limites assumées : seuls les fichiers de 50 Mo et plus sont examinés ; la liste vient du dernier relevé des bibliothèques ; un
torrent qui verrait ensuite son fichier « modifié » par un autre programme modifierait aussi la médiathèque (c'est le propre d'un lien dur).
    seedboxctl copyaudit show | scan | relink CHEMIN...
"""
import json
import os
import time
from pathlib import Path

import seedboxctl as ctl

FILE = ctl.STATE / "copyaudit.json"
MIN_SIZE = 50 << 20
MAX_WALK = 300000
MAX_CANDIDATES = 300
SAMPLE = 1 << 20
BLOCK = 8 << 20


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def downloads_root():
    try:
        container = ctl.shares().location("complete")
    except Exception:  # noqa: BLE001
        container = "/data/downloads"
    return ctl.libscan().to_host(container)


def _sample(path, size):
    """Empreinte rapide : taille et trois extraits (début, milieu, fin). None si le fichier ne se lit pas
    (partage réseau qui refuse la lecture, fichier verrouillé ou disparu) : il est alors ignoré, jamais fatal."""
    import hashlib
    digest = hashlib.sha256(str(size).encode())
    try:
        with open(path, "rb") as handle:
            for offset in (0, max(0, size // 2 - SAMPLE // 2), max(0, size - SAMPLE)):
                handle.seek(offset)
                digest.update(handle.read(SAMPLE))
    except OSError:
        return None
    return digest.hexdigest()


def identical(a, b):
    """Comparaison complète, octet par octet."""
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as x, open(b, "rb") as y:
            while True:
                bx, by = x.read(BLOCK), y.read(BLOCK)
                if bx != by:
                    return False
                if not bx:
                    return True
    except OSError:
        return False


def media_index(records):
    """{taille: [(chemin serveur, enregistrement)]} des fichiers de médiathèque du relevé."""
    libscan = ctl.libscan()
    index = {}
    for r in records:
        host = libscan.to_host(r["path"])
        if host and r["size"] >= MIN_SIZE:
            index.setdefault(r["size"], []).append((host, r))
    return index


def candidates(records=None, root=None, limit=MAX_CANDIDATES):
    """Copies probables : [{download, media, size, title, same_disk}], les plus grosses d'abord."""
    libscan = ctl.libscan()
    records = libscan.records() if records is None else records
    root = Path(root) if root else downloads_root()
    if not root or not root.is_dir():
        return {"items": [], "walked": 0, "note": "dossier des téléchargements terminés introuvable"}
    index = media_index(records)
    found, walked, unreadable = [], 0, 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            walked += 1
            if walked > MAX_WALK:
                break
            path = Path(folder) / name
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if info.st_size < MIN_SIZE or not os.path.isfile(path) or os.path.islink(path) or info.st_nlink > 1:
                continue                                          # déjà relié (nlink > 1) ou illisible : rien à gagner
            for host, record in index.get(info.st_size, []):
                try:
                    media = os.stat(host)
                except OSError:
                    continue
                if media.st_ino == info.st_ino and media.st_dev == info.st_dev:
                    continue
                same_disk = media.st_dev == info.st_dev
                mine, theirs = _sample(path, info.st_size), _sample(host, info.st_size)
                if mine is None or theirs is None:
                    unreadable += 1
                    break                                         # illisible : ni copie reconnue, ni erreur
                if mine == theirs:
                    found.append({"download": str(path), "media": str(host), "size": info.st_size, "title": record["title"],
                                  "same_disk": same_disk})
                    break
    found.sort(key=lambda e: -e["size"])
    return {"items": found[:limit], "walked": walked, "unreadable": unreadable, "total": len(found), "saving": sum(e["size"] for e in found if e["same_disk"]),
            "blocked": sum(e["size"] for e in found if not e["same_disk"])}


def relink(paths, records=None, root=None, by="console"):
    """Remplace chaque copie des téléchargements par un lien dur vers le fichier de médiathèque. Renvoie {done, saved, skipped}."""
    wanted = {str(p) for p in paths}
    if not wanted:
        raise ctl.SeedboxError("aucun fichier choisi")
    found = {e["download"]: e for e in candidates(records, root, limit=10 ** 6)["items"]}
    done, saved, skipped = 0, 0, []
    for path in sorted(wanted):
        entry = found.get(path)
        if not entry:
            skipped.append((path, "n'est plus une copie reconnue"))
            continue
        if not entry["same_disk"]:
            skipped.append((path, "deux disques différents : lien impossible"))
            continue
        if not identical(entry["download"], entry["media"]):
            skipped.append((path, "contenus différents : rien n'est touché"))
            continue
        temp = entry["download"] + ".sb-lien"
        try:
            if os.path.lexists(temp):
                os.unlink(temp)
            os.link(entry["media"], temp)
            os.replace(temp, entry["download"])
        except OSError as error:
            try:
                os.unlink(temp)
            except OSError:
                pass
            skipped.append((path, f"lien refusé ({error.strerror})"))
            continue
        done += 1
        saved += entry["size"]
    try:
        ctl.audit().record(by, "", "Copies reliées par un lien dur", f"{done} fichier(s)", "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    _journal("relink", done, saved)
    say(f"copies reliées : {done} fichier(s), {saved} octets récupérés")
    return {"done": done, "saved": saved, "skipped": [{"path": p, "reason": r} for p, r in skipped]}


def _journal(kind, count, size=0):
    try:
        ctl.libreport().record(kind, count, size)
    except Exception:  # noqa: BLE001 - le rapport ne doit jamais gêner l'action
        pass


def scan(records=None, root=None):
    """Recherche des copies (peut durer quelques minutes sur un gros dossier) et mémorise le résultat."""
    result = candidates(records, root)
    result["at"] = int(time.time())
    FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = FILE.with_name(FILE.name + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False))
    os.replace(temp, FILE)
    say(f"audit des copies : {result.get('total', 0)} copie(s), {result.get('saving', 0)} octets récupérables"
        + (f", {result['unreadable']} fichier(s) illisible(s) ignoré(s)" if result.get("unreadable") else ""))
    return result


def overview():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {"items": [], "at": 0, "walked": 0, "total": 0, "saving": 0, "blocked": 0}
