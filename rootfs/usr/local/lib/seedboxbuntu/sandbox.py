"""SeedBoxBuntu - vérification approfondie d'une sauvegarde, sans rien restaurer.

« seedboxctl backup check [nom] » déchiffre la sauvegarde dans un dossier temporaire, l'extrait avec les mêmes garde-fous
qu'une restauration, puis contrôle :
  - chaque base SQLite (PRAGMA integrity_check) ;
  - chaque fichier JSON et YAML de configuration (il doit se lire) ;
  - la présence de .env et de la clé de chiffrement des sauvegardes ;
et compare le contenu au serveur actuel : fichiers ajoutés, modifiés ou identiques, et ce qu'une restauration remplacerait.
Aucun conteneur n'est démarré et rien n'est écrit hors du dossier temporaire : c'est une répétition à blanc, pas un démarrage.
"""
import hashlib
import json
import tarfile
import tempfile
import time
from pathlib import Path

import seedboxctl as ctl

DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
MAX_SAMPLE = 12


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _readable_config(path):
    """None si le fichier se lit, sinon la raison."""
    try:
        text = path.read_text(errors="strict")
    except (OSError, UnicodeDecodeError) as error:
        return f"illisible ({error.__class__.__name__})"
    try:
        if path.suffix == ".json":
            json.loads(text)
        elif path.suffix in (".yml", ".yaml"):
            import yaml
            yaml.safe_load(text)
    except Exception as error:  # noqa: BLE001 - toute erreur d'analyse = fichier à signaler
        return f"syntaxe invalide ({str(error).splitlines()[0][:60]})"
    return None


def compare(extracted, root=Path("/")):
    """Compare l'extraction au serveur actuel : {added, changed, same, sample}."""
    counts = {"added": 0, "changed": 0, "same": 0}
    sample = []
    for path in sorted(p for p in extracted.rglob("*") if p.is_file()):
        relative = path.relative_to(extracted)
        current = root / relative
        if not current.is_file():
            kind = "added"
        elif current.stat().st_size == path.stat().st_size and _sha(current) == _sha(path):
            kind = "same"
        else:
            kind = "changed"
        counts[kind] += 1
        if kind != "same" and len(sample) < MAX_SAMPLE:
            sample.append({"path": "/" + str(relative), "change": kind})
    return {**counts, "sample": sample}


def check(name=None, key_text="", root=Path("/")):
    """Répétition à blanc ; renvoie un rapport. Lève SeedboxError si la sauvegarde est inutilisable."""
    backup, offsite = ctl.backup(), ctl.offsite()
    started = time.time()
    if name:
        archive = backup.find(name)
    else:
        local = [e for e in backup.listing() if "-avant" not in e["name"]]
        if not local:
            raise ctl.SeedboxError("aucune sauvegarde à vérifier")
        archive = backup.find(local[0]["name"])
    if not archive.is_file():
        raise ctl.SeedboxError("sauvegarde introuvable")
    key_text = key_text or (backup.KEY_FILE.read_text() if backup.KEY_FILE.exists() else "")
    if not key_text.strip():
        raise ctl.SeedboxError("clé de chiffrement absente : fournissez celle du serveur d'origine")
    problems = []
    with tempfile.TemporaryDirectory(dir=ctl.STATE if ctl.STATE.exists() else None) as work:
        work = Path(work)
        plain = backup.open_archive(archive, key_text, work)
        extracted = work / "contenu"
        extracted.mkdir()
        with tarfile.open(plain) as tar:
            members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
            try:
                tar.extractall(extracted, members=members, filter="data")
            except TypeError:
                tar.extractall(extracted, members=members)
        files = [p for p in extracted.rglob("*") if p.is_file()]
        if not files:
            raise ctl.SeedboxError("la sauvegarde est vide")
        databases = [p for p in files if p.suffix in DB_SUFFIXES and p.stat().st_size > 0]
        for path in databases:
            if not offsite._check_database(path):
                problems.append({"file": "/" + str(path.relative_to(extracted)), "problem": "base de données endommagée"})
        configs = [p for p in files if p.suffix in (".json", ".yml", ".yaml")]
        for path in configs:
            reason = _readable_config(path)
            if reason:
                problems.append({"file": "/" + str(path.relative_to(extracted)), "problem": reason})
        if not any(p.name == ".env" for p in files):
            problems.append({"file": ".env", "problem": "absent de la sauvegarde"})
        changes = compare(extracted, root)
    report = {"ok": not problems, "name": archive.name, "files": len(files), "databases": len(databases), "configs": len(configs),
              "problems": problems[:20], "changes": changes, "seconds": round(time.time() - started, 1), "at": int(time.time())}
    ctl.say(f"vérification de {archive.name} : {'saine' if report['ok'] else str(len(problems)) + ' problème(s)'}, "
            f"{changes['changed']} fichier(s) remplacé(s), {changes['added']} ajouté(s) à une restauration")
    return report
