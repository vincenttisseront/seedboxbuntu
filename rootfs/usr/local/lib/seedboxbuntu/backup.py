"""SeedBoxBuntu - sauvegarde et restauration de la configuration de l'appliance.

Contenu : .env (secrets), configuration de chaque application (comptes, clés, bibliothèques), partages et
réglages (/etc/seedboxbuntu), applications ajoutées. Exclus : médias et téléchargements (c'est le stockage),
caches, journaux, vignettes et métadonnées de Jellyfin (régénérées par l'analyse des bibliothèques).

L'archive contient des secrets : elle est toujours chiffrée (AES-256, openssl) avec une clé propre à ce
serveur, /etc/seedboxbuntu/backup.key. Cette clé n'est jamais dans l'archive : conservez-la ailleurs, elle est
nécessaire pour restaurer sur un autre serveur. La copie se fait à chaud : une application qui écrit au
même instant peut laisser une base légèrement en retard, ce qui reste sans conséquence pour Radarr, Sonarr...
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "backup.json"
KEY_FILE = ctl.ETC / "backup.key"
LOCAL_DIR = ctl.STATE / "sauvegardes"
SUBDIR = "seedbox-sauvegardes"
DEFAULTS = {"enabled": True, "days": "daily", "time": "02:30", "keep": 7, "destination": ""}
NAME = re.compile(r"^seedbox-[0-9]{8}-[0-9]{6}(-[a-z]+)?\.tar\.gz\.enc$")
EXCLUDE = ("config/jellyfin/cache", "config/jellyfin/transcodes", "config/jellyfin/log", "config/jellyfin/metadata",
           "config/*/logs", "config/*/Logs", "config/*/log", "config/*/cache", "config/*/Cache",
           "config/*/MediaCover", "config/*/Backups", "config/*/backups")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def rel(path):
    return str(Path(path)).lstrip("/")


def contents():
    """Chemins sauvegardés, relatifs à la racine."""
    wanted = [ctl.ENV_FILE, ctl.BASE / "docker-compose.local.yml", ctl.CONFIG, ctl.ETC,
              ctl.STATE / "apps.json", ctl.STATE / "provision-status.json"]
    return [rel(p) for p in wanted if Path(p).exists() and Path(p) != KEY_FILE]


def allowed(member):
    """Chemins qu'une restauration a le droit d'écrire : rien d'autre que ce que contient une sauvegarde."""
    name = member[2:] if member.startswith("./") else member
    if name.startswith("/") or ".." in Path(name).parts:
        return False
    prefixes = tuple(contents_prefixes())
    return name.rstrip("/") in prefixes or name.startswith(tuple(p + "/" for p in prefixes))


def contents_prefixes():
    return [rel(p) for p in (ctl.ENV_FILE, ctl.BASE / "docker-compose.local.yml", ctl.CONFIG, ctl.ETC,
                             ctl.STATE / "apps.json", ctl.STATE / "provision-status.json")]


def settings():
    stored = {}
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        pass
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def key():
    """Clé de chiffrement, créée au premier besoin."""
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(36) + "\n")
    return KEY_FILE.read_text().strip()


def destination_dir(settings_=None):
    """Dossier de destination : local, ou un partage réseau monté (rôle de sauvegarde sur un NAS)."""
    name = (settings_ or settings())["destination"]
    if not name:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_DIR.chmod(0o700)
        return LOCAL_DIR
    shares = ctl.shares()
    if not shares.NAME.match(name):
        raise ctl.SeedboxError("destination invalide")
    if str(shares.ROOT / name) not in shares.mounted_targets():
        raise ctl.SeedboxError(f"le partage « {name} » n'est pas monté : sauvegarde impossible")
    target = shares.ROOT / name / SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def listing():
    cfg = settings()
    folders = [("local", LOCAL_DIR)]
    if cfg["destination"]:
        try:
            folders.append((cfg["destination"], destination_dir(cfg)))
        except ctl.SeedboxError:
            pass
    entries = []
    for where, folder in folders:
        for path in sorted(Path(folder).glob("seedbox-*.tar.gz.enc")):
            if NAME.match(path.name):
                info = path.stat()
                entries.append({"name": path.name, "where": where, "size": info.st_size, "time": info.st_mtime})
    return sorted(entries, key=lambda e: -e["time"])


def find(name):
    if not NAME.match(name or ""):
        raise ctl.SeedboxError("nom de sauvegarde invalide")
    for entry in listing():
        if entry["name"] == name:
            folder = LOCAL_DIR if entry["where"] == "local" else destination_dir()
            return Path(folder) / name
    raise ctl.SeedboxError("sauvegarde introuvable")


def overview():
    cfg = settings()
    return {"settings": cfg, "backups": listing(), "local_dir": str(LOCAL_DIR),
            "destination_ok": _destination_ok(cfg), "key_present": KEY_FILE.exists()}


def _destination_ok(cfg):
    if not cfg["destination"]:
        return True
    try:
        destination_dir(cfg)
        return True
    except ctl.SeedboxError:
        return False


def create(label=""):
    """Archive chiffrée dans le dossier de destination ; supprime les plus anciennes au-delà de « keep »."""
    cfg = settings()
    folder = destination_dir(cfg)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    target = Path(folder) / f"seedbox-{stamp}{suffix}.tar.gz.enc"
    partial = target.with_suffix(".part")
    command = ["tar", "-czf", "-", "-C", "/", "--ignore-failed-read", "--warning=no-file-changed"]
    command += [f"--exclude={rel(ctl.BASE)}/{pattern}" for pattern in EXCLUDE] + [f"--exclude={rel(KEY_FILE)}"] + contents()
    say(f"sauvegarde vers {target}")
    key()
    with open(partial, "wb") as out:
        os.chmod(partial, 0o600)
        with tempfile.TemporaryFile() as tar_log:
            tar = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=tar_log)
            enc = subprocess.Popen(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", f"file:{KEY_FILE}"],
                                   stdin=tar.stdout, stdout=out, stderr=subprocess.PIPE)
            tar.stdout.close()
            enc_err = enc.communicate()[1]
            tar.wait()
            tar_log.seek(0)
            tar_err = tar_log.read()
    # tar renvoie 1 quand un fichier a changé pendant la lecture : sans gravité
    if enc.returncode != 0 or tar.returncode > 1:
        partial.unlink(missing_ok=True)
        raise ctl.SeedboxError("sauvegarde échouée : " + (enc_err or tar_err).decode(errors="replace")[:200])
    partial.replace(target)
    prune(folder, cfg["keep"])
    say(f"sauvegarde terminée : {target.name} ({target.stat().st_size // 1024} Ko)")
    if not label:
        try:
            ctl.offsite().push(target.name)     # copie hors du serveur si elle est configurée
        except ctl.SeedboxError as error:
            say(f"envoi hors serveur : {error}")
    return target.name


def prune(folder, keep):
    # Les sauvegardes « avant-restauration » ne comptent pas dans la rétention : elles sont rares et précieuses
    files = sorted((p for p in Path(folder).glob("seedbox-*.tar.gz.enc") if NAME.match(p.name) and "-avant" not in p.name),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[max(int(keep), 1):]:
        old.unlink(missing_ok=True)
        say(f"ancienne sauvegarde supprimée : {old.name}")


def delete(name):
    find(name).unlink()
    say(f"sauvegarde supprimée : {name}")


def open_archive(path, key_text, workdir):
    """Déchiffre vers un fichier temporaire et contrôle son contenu avant toute écriture."""
    plain = Path(workdir) / "archive.tar.gz"
    keyfile = Path(workdir) / "key"
    keyfile.write_text(key_text.strip() + "\n")
    keyfile.chmod(0o600)
    result = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{keyfile}",
                             "-in", str(path), "-out", str(plain)], capture_output=True)
    keyfile.unlink()
    if result.returncode != 0:
        raise ctl.SeedboxError("déchiffrement impossible : clé incorrecte ou sauvegarde endommagée")
    try:
        with tarfile.open(plain) as archive:
            for member in archive.getmembers():
                if not allowed(member.name):
                    raise ctl.SeedboxError(f"contenu inattendu dans la sauvegarde : {member.name}")
                if member.issym() or member.islnk():
                    if not allowed(os.path.normpath(os.path.join(os.path.dirname(member.name), member.linkname))):
                        raise ctl.SeedboxError(f"lien suspect dans la sauvegarde : {member.name}")
                if member.isdev():
                    raise ctl.SeedboxError(f"fichier spécial refusé : {member.name}")
    except tarfile.TarError as error:
        raise ctl.SeedboxError(f"archive illisible : {error}")
    return plain


def restore(name_or_path, key_text=""):
    """Remplace la configuration par celle de la sauvegarde, puis redémarre la pile et relie les applications.
    Une sauvegarde de l'état actuel est faite d'abord."""
    path = Path(name_or_path)
    if not path.is_absolute():
        path = find(name_or_path)
    if not path.is_file():
        raise ctl.SeedboxError("fichier de sauvegarde introuvable")
    key_text = key_text or (KEY_FILE.read_text() if KEY_FILE.exists() else "")
    if not key_text.strip():
        raise ctl.SeedboxError("clé de chiffrement absente : fournissez celle du serveur d'origine")
    with tempfile.TemporaryDirectory(dir=ctl.STATE) as workdir:
        plain = open_archive(path, key_text, workdir)
        safety = create("avant")
        say(f"état actuel conservé : {safety}")
        if ctl.ENV_FILE.exists():
            say("arrêt de la pile")
            ctl.compose("down", check=False, capture=True)
        say("restauration des fichiers")
        subprocess.run(["tar", "-xzf", str(plain), "-C", "/", "--no-overwrite-dir"], check=True)
    if key_text.strip() and not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(key_text.strip() + "\n")
        KEY_FILE.chmod(0o600)
    ctl.ensure_env()
    errors = ctl.apply_addresses()
    say("restauration terminée")
    return errors


def validate(values):
    module = ctl.maintenance()
    check_days, check_time = module.check_days, module.check_time
    result = settings()
    for k, v in values.items():
        if k not in DEFAULTS or v is None:
            continue
        if k == "enabled":
            result[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "oui", "yes")
        elif k == "days":
            result[k] = check_days(v)
        elif k == "time":
            result[k] = check_time(v, "heure")
        elif k == "keep":
            try:
                result[k] = max(1, min(int(v), 60))
            except (TypeError, ValueError):
                raise ctl.SeedboxError("nombre de sauvegardes à conserver : 1 à 60")
        elif k == "destination":
            name = str(v).strip()
            if name and not ctl.shares().NAME.match(name):
                raise ctl.SeedboxError("destination invalide")
            result[k] = name
    return result


def apply_schedule(cfg):
    module = ctl.maintenance()
    changed = module.timer_dropin("seedbox-backup.timer", module.calendar(cfg["days"], cfg["time"]))
    if changed and module.LIVE:
        module.system("systemctl", "daemon-reload")
        module.system("systemctl", "restart", "seedbox-backup.timer")


def set_settings(values):
    cfg = validate(values)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    apply_schedule(cfg)
    say("réglages de sauvegarde enregistrés")
    return cfg


def auto():
    """Appelée par le minuteur : le jour et l'heure sont déjà portés par le minuteur lui-même."""
    cfg = settings()
    if not cfg["enabled"]:
        say("sauvegarde automatique désactivée")
        return None
    try:
        return create()
    except ctl.SeedboxError as error:
        try:
            ctl.alerts().notify("Sauvegarde échouée", str(error), "critical")
        except Exception:  # noqa: BLE001 - l'échec de l'alerte ne doit pas masquer celui de la sauvegarde
            pass
        raise
