"""SeedBoxBuntu - journal des changements de configuration : comparaison de deux sauvegardes (ou d'une sauvegarde et du serveur).

« Qu'est-ce qui a changé depuis jeudi ? » : deux sauvegardes sont déchiffrées dans un dossier temporaire (rien n'est
restauré, rien n'est écrit ailleurs), puis comparées fichier par fichier. Pour les fichiers de configuration texte
(.env, YAML, JSON, XML, ini...), les lignes ajoutées et retirées sont montrées ; les bases de données et autres
fichiers binaires sont seulement signalés « modifiés » (une base change à chaque utilisation : ce n'est pas un réglage).

Les secrets ne sortent jamais dans la comparaison : toute valeur dont le nom évoque un mot de passe, une clé, un jeton,
un secret, un sel ou un condensé est remplacée par « [masqué:xxxx] ». Le repère xxxx est un condensé court, propre à cette
comparaison : deux valeurs identiques portent le même repère, deux valeurs différentes des repères différents ; on voit
donc « le mot de passe a changé » sans jamais le voir. Les longues valeurs hexadécimales et les condensés $pbkdf2 sont
masqués aussi, quel que soit leur nom.
    seedboxctl backupdiff list | diff ANCIENNE NOUVELLE     (NOUVELLE peut être « courant » : le serveur tel qu'il est)
"""
import fnmatch
import hashlib
import hmac
import os
import re
import secrets
import tarfile
import tempfile
import time
import difflib
from pathlib import Path

import seedboxctl as ctl

CURRENT = "courant"
TEXT_SUFFIXES = {".env", ".yml", ".yaml", ".json", ".xml", ".conf", ".ini", ".toml", ".cfg", ".txt", ".properties", ".config"}
BINARY_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".png", ".jpg", ".jpeg", ".gz", ".zip", ".bak"}
NOISE = ("*/logs/*", "*/log/*", "*/cache/*", "*.log", "*.lock", "*.pid", "*/MediaCover/*", "*/Backups/*", "*/metadata/*", "*/transcodes/*")
MAX_TEXT = 512 * 1024
MAX_FILES = 60
MAX_DIFF_LINES = 40
MAX_WALK = 200000
HASH_LIMIT = 64 << 20
SECRET_NAME = re.compile(r"(?i)(pass|secret|token|api[_-]?key|private|credential|cookie|jwt|salt|hash|webhook|session|encryption|auth[_-]?key|"
                         r"(^|[_\-.])key([_\-.]|$)|key$)")
LINE_PATTERNS = (
    re.compile(r"""^(?P<head>\s*(?:export\s+)?(?P<name>[\w.\-]+)\s*=\s*)(?P<q>['"]?)(?P<value>.*?)(?P=q)\s*$"""),            # KEY=value
    re.compile(r"""^(?P<head>\s*-?\s*(?P<name>[\w.\-]+)\s*:\s*)(?P<q>['"]?)(?P<value>[^'"#]*?)(?P=q)\s*(#.*)?$"""),           # key: value
    re.compile(r'''^(?P<head>\s*"(?P<name>[\w.\-]+)"\s*:\s*)(?P<q>")(?P<value>(?:[^"\\]|\\.)*)"(?P<tail>,?\s*)$'''),   # "key": "valeur"
    re.compile(r'''^(?P<head>\s*"(?P<name>[\w.\-]+)"\s*:\s*)(?P<q>)(?P<value>[^",\s\]}]+)(?P<tail>,?\s*)$'''),            # "key": nombre
)
INLINE_JSON = re.compile(r'"(?P<name>[\w.\-]+)"\s*:\s*"(?P<value>(?:[^"\\]|\\.)*)"')
XML_SECRET = re.compile(r"(?i)<(?P<name>[\w.\-]*(?:pass|secret|token|apikey|key|salt|hash)[\w.\-]*)>(?P<value>[^<]*)</(?P=name)>")
LONG_SECRET = re.compile(r"\$pbkdf2[^\s'\"]+|\$argon2[^\s'\"]+|\b[0-9a-fA-F]{32,}\b")


class Masker:
    """Remplace les secrets par un repère stable pendant une comparaison, illisible d'une comparaison à l'autre."""

    def __init__(self):
        self.key = secrets.token_bytes(16)

    def tag(self, value):
        return "[masqué:" + hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()[:4] + "]"

    def line(self, text):
        for index, pattern in enumerate(LINE_PATTERNS):
            match = pattern.match(text)
            if match and SECRET_NAME.search(match.group("name")) and match.group("value").strip():
                tail = match.groupdict().get("tail") or ""
                quote = match.group("q")
                return f"{match.group('head')}{quote}{self.tag(match.group('value'))}{quote}{tail}"
        text = INLINE_JSON.sub(lambda m: f'"{m.group("name")}": "{self.tag(m.group("value"))}"'
                               if SECRET_NAME.search(m.group("name")) and m.group("value").strip() else m.group(0), text)
        text = XML_SECRET.sub(lambda m: f"<{m.group('name')}>{self.tag(m.group('value'))}</{m.group('name')}>" if m.group("value").strip() else m.group(0), text)
        return LONG_SECRET.sub(lambda m: self.tag(m.group(0)), text)

    def text(self, content):
        return [self.line(line) for line in content.splitlines()]


# --- arbres de fichiers --------------------------------------------------------------------------
def noisy(relative):
    return any(fnmatch.fnmatch("/" + relative, pattern) for pattern in NOISE)


def extract(archive, key_text, work):
    backup = ctl.backup()
    plain = backup.open_archive(archive, key_text, work)
    target = Path(work) / "contenu"
    target.mkdir()
    with tarfile.open(plain) as tar:
        members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
        try:
            tar.extractall(target, members=members, filter="data")
        except TypeError:
            tar.extractall(target, members=members)
    return target


def walk(root, only=None):
    """{chemin relatif: Path} des fichiers sous root (ou sous les chemins « only » relatifs à root)."""
    found, count = {}, 0
    bases = [root / p for p in only] if only else [root]
    for base in bases:
        if base.is_file():
            found[str(base.relative_to(root))] = base
            continue
        for path in base.rglob("*"):
            count += 1
            if count > MAX_WALK:
                raise ctl.SeedboxError("trop de fichiers à comparer")
            if path.is_file() and not path.is_symlink():
                relative = str(path.relative_to(root))
                if not noisy(relative):
                    found[relative] = path
    return found


def is_text(path):
    if path.suffix.lower() in BINARY_SUFFIXES:
        return False
    if path.name == ".env" or path.suffix.lower() in TEXT_SUFFIXES:
        return path.stat().st_size <= MAX_TEXT
    return False


def same(a, b):
    sa, sb = a.stat().st_size, b.stat().st_size
    if sa != sb:
        return False
    if sa > HASH_LIMIT:
        return True
    return _sha(a) == _sha(b)


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def compare_trees(old, new, masker=None):
    """Comparaison de deux {relatif: Path} : résumé et détail par fichier."""
    masker = masker or Masker()
    entries = []
    summary = {"added": 0, "removed": 0, "changed": 0, "binary": 0, "same": 0}
    for relative in sorted(set(old) | set(new)):
        a, b = old.get(relative), new.get(relative)
        if a and not b:
            summary["removed"] += 1
            entries.append({"path": "/" + relative, "change": "removed", "kind": "texte" if is_text(a) else "binaire", "diff": [], "more": 0})
        elif b and not a:
            summary["added"] += 1
            entries.append({"path": "/" + relative, "change": "added", "kind": "texte" if is_text(b) else "binaire", "diff": [], "more": 0})
        elif same(a, b):
            summary["same"] += 1
        elif is_text(a) and is_text(b):
            before = masker.text(a.read_text(errors="replace"))
            after = masker.text(b.read_text(errors="replace"))
            lines = [l for l in difflib.unified_diff(before, after, lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
            if not lines:
                summary["same"] += 1                              # seules des fins de ligne ou des espaces avaient changé
                continue
            summary["changed"] += 1
            entries.append({"path": "/" + relative, "change": "changed", "kind": "texte", "diff": lines[:MAX_DIFF_LINES],
                            "more": max(0, len(lines) - MAX_DIFF_LINES)})
        else:
            summary["binary"] += 1
            entries.append({"path": "/" + relative, "change": "changed", "kind": "binaire", "diff": [],
                            "size": [a.stat().st_size, b.stat().st_size], "more": 0})
    entries.sort(key=lambda e: (0 if e["kind"] == "texte" and e["change"] == "changed" else 1 if e["change"] != "changed" else 2, e["path"]))
    shown = entries[:MAX_FILES]
    return {"summary": summary, "files": shown, "omitted": max(0, len(entries) - MAX_FILES)}


# --- interface -----------------------------------------------------------------------------------
def listing():
    backup = ctl.backup()
    rows = [e for e in backup.listing() if "-avant" not in e["name"]]
    return {"backups": [{"name": e["name"], "where": e["where"], "time": int(e["time"]), "size": e["size"]} for e in rows],
            "current": CURRENT}


def diff(older, newer, key_text=""):
    """Compare deux sauvegardes, ou une sauvegarde et le serveur actuel (newer = « courant »)."""
    backup = ctl.backup()
    if older == CURRENT:
        raise ctl.SeedboxError("la première comparée doit être une sauvegarde")
    key_text = key_text or (backup.KEY_FILE.read_text() if backup.KEY_FILE.exists() else "")
    if not key_text.strip():
        raise ctl.SeedboxError("clé de chiffrement absente : fournissez celle du serveur d'origine")
    started = time.time()
    with tempfile.TemporaryDirectory(dir=ctl.STATE if ctl.STATE.exists() else None) as work:
        work = Path(work)
        (work / "a").mkdir()
        old_root = extract(backup.find(older), key_text, work / "a")
        old = walk(old_root)
        if newer == CURRENT:
            new = walk(Path("/"), only=[p for p in backup.contents()])
            new = {k: v for k, v in new.items() if k in old or not noisy(k)}
            # Fichiers du serveur absents de l'ancienne sauvegarde = ajoutés depuis ; ceux de la sauvegarde absents du serveur = retirés
        else:
            (work / "b").mkdir()
            new = walk(extract(backup.find(newer), key_text, work / "b"))
        result = compare_trees(old, new)
    result.update(older=older, newer=newer, seconds=round(time.time() - started, 1), at=int(time.time()))
    ctl.say(f"comparaison {older} -> {newer} : {result['summary']['changed']} fichier(s) texte modifié(s), "
            f"{result['summary']['added']} ajouté(s), {result['summary']['removed']} retiré(s)")
    return result
