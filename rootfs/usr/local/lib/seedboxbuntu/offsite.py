"""SeedBoxBuntu - sauvegarde hors du serveur (rclone) et test de restauration.

Les archives de sauvegarde sont déjà chiffrées (backup.py, AES-256) : elles partent telles quelles vers
SFTP, un stockage compatible S3 (Backblaze, Wasabi, Scaleway, MinIO...), WebDAV (Nextcloud...), Backblaze B2
ou toute destination décrite dans une configuration rclone collée par l'administrateur. Les identifiants
sont rangés dans /etc/seedboxbuntu/rclone.conf (0600) et n'apparaissent jamais dans la console.

Test de restauration : une sauvegarde qu'on n'a jamais essayé de relire ne vaut rien. Chaque mois (ou à la
demande), la dernière archive est récupérée depuis la destination, déchiffrée, extraite dans un dossier
temporaire, et les bases SQLite qu'elle contient sont contrôlées (PRAGMA integrity_check). Un échec envoie
une alerte. Rien n'est écrit dans la configuration en service.

    seedboxctl offsite show | set | connection | push | restore-test | clear
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "offsite.json"
CONF_FILE = ctl.ETC / "rclone.conf"
STATUS_FILE = ctl.STATE / "offsite-status.json"
REMOTE = "offsite"
TYPES = {
    "sftp": {"label": "SFTP (autre serveur, NAS)", "fields": ("host", "port", "user"), "secrets": ("password", "private_key")},
    "s3": {"label": "Stockage compatible S3", "fields": ("provider", "endpoint", "region", "bucket", "access_key"), "secrets": ("secret_key",)},
    "webdav": {"label": "WebDAV (Nextcloud, ownCloud...)", "fields": ("url", "user", "vendor"), "secrets": ("password",)},
    "b2": {"label": "Backblaze B2", "fields": ("account", "bucket"), "secrets": ("key",)},
    "raw": {"label": "Configuration rclone (avancé)", "fields": (), "secrets": ("config",)},
}
DEFAULTS = {"enabled": False, "type": "", "path": "seedbox", "keep": 14, "fields": {}}
SAFE = re.compile(r"^[A-Za-z0-9_.@:/ +-]{1,200}$")
PATH_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+){0,4}$")
NAME_RE = re.compile(r"^seedbox-[0-9]{8}-[0-9]{6}(-[a-z]+)?\.tar\.gz\.enc$")
PROVIDERS = ("AWS", "Backblaze", "Wasabi", "Scaleway", "DigitalOcean", "Cloudflare", "Minio", "Ceph", "Other")
RCLONE = os.environ.get("SB_RCLONE", "rclone")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.new")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def settings():
    stored = read_json(SETTINGS_FILE)
    result = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    result["fields"] = dict(result["fields"] or {})
    return result


def installed():
    return shutil.which(RCLONE) is not None


def ensure_rclone():
    """Installe rclone s'il manque (serveurs installés avant cette fonction)."""
    if installed():
        return
    maintenance = ctl.maintenance()
    if not maintenance.LIVE:
        raise ctl.SeedboxError("rclone n'est pas installé")
    say("installation de rclone")
    result = subprocess.run(["apt-get", "install", "-y", "-qq", "rclone"], capture_output=True, text=True,
                            env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
    if result.returncode != 0 or not installed():
        raise ctl.SeedboxError("installation de rclone impossible : " + (result.stderr or "").strip()[-160:])


def line(label, value, limit=200):
    value = str(value or "").strip()
    if not SAFE.fullmatch(value) or len(value) > limit:
        raise ctl.SeedboxError(f"{label} invalide")
    return value


def validate(data):
    """Normalise les réglages saisis ; les secrets vides conservent ceux déjà enregistrés."""
    current = settings()
    kind = data.get("type", current["type"])
    if kind not in TYPES and kind != "":
        raise ctl.SeedboxError("type de destination inconnu : " + ", ".join(TYPES))
    path = str(data.get("path", current["path"])).strip().strip("/")
    if path and (not PATH_RE.fullmatch(path) or any(part in (".", "..") for part in path.split("/"))):
        raise ctl.SeedboxError("dossier distant invalide (lettres, chiffres, . _ - et / seulement)")
    try:
        keep = max(1, min(int(data.get("keep", current["keep"])), 365))
    except (TypeError, ValueError):
        raise ctl.SeedboxError("nombre de sauvegardes à conserver : 1 à 365")
    fields = {}
    for name in TYPES.get(kind, {"fields": ()})["fields"]:
        value = (data.get("fields") or {}).get(name, current["fields"].get(name, ""))
        if name == "port" and str(value).strip():
            if not str(value).strip().isdigit() or not 1 <= int(value) <= 65535:
                raise ctl.SeedboxError("port invalide")
        elif name == "provider" and str(value).strip() and value not in PROVIDERS:
            raise ctl.SeedboxError("fournisseur S3 inconnu : " + ", ".join(PROVIDERS))
        elif name in ("url", "endpoint") and str(value).strip() and not re.fullmatch(r"https?://[A-Za-z0-9._~:/?#\[\]@!&'()*+,;=%-]+", str(value).strip()):
            raise ctl.SeedboxError(f"adresse invalide : https://... attendu ({name})")
        elif str(value).strip():
            line(name, value)
        fields[name] = str(value).strip()
    enabled = data.get("enabled", current["enabled"])
    enabled = enabled if isinstance(enabled, bool) else str(enabled).lower() in ("1", "true", "on", "oui", "yes")
    return {"enabled": enabled, "type": kind, "path": path, "keep": keep, "fields": fields}


def obscure(text):
    result = subprocess.run([RCLONE, "obscure", text], capture_output=True, text=True)
    if result.returncode != 0:
        raise ctl.SeedboxError("rclone obscure a échoué")
    return result.stdout.strip()


def build_conf(kind, fields, secrets):
    """Contenu de rclone.conf pour la destination « offsite »."""
    values = []
    f = lambda k: fields.get(k, "")  # noqa: E731
    if kind == "sftp":
        values = [("type", "sftp"), ("host", f("host")), ("user", f("user"))]
        if f("port"):
            values.append(("port", f("port")))
        if secrets.get("private_key"):
            values.append(("key_pem", secrets["private_key"].replace("\r", "").strip().replace("\n", "\\n")))
        if secrets.get("password"):
            values.append(("pass", obscure(secrets["password"])))
    elif kind == "s3":
        values = [("type", "s3"), ("provider", f("provider") or "Other"), ("access_key_id", f("access_key")),
                  ("secret_access_key", secrets.get("secret_key", "")), ("env_auth", "false")]
        for key, name in (("endpoint", "endpoint"), ("region", "region")):
            if f(name):
                values.append((key, f(name)))
    elif kind == "webdav":
        values = [("type", "webdav"), ("url", f("url")), ("user", f("user")), ("vendor", f("vendor") or "other")]
        if secrets.get("password"):
            values.append(("pass", obscure(secrets["password"])))
    elif kind == "b2":
        values = [("type", "b2"), ("account", f("account")), ("key", secrets.get("key", ""))]
    if kind == "raw":
        text = secrets.get("config", "")
        sections = re.findall(r"^\[([^\]\n]+)\]\s*$", text, re.M)
        if not sections:
            raise ctl.SeedboxError("configuration rclone vide ou sans section [nom]")
        # La destination utilisée s'appelle toujours « offsite » : la première section est renommée
        return re.sub(r"^\[[^\]\n]+\]\s*$", f"[{REMOTE}]", text, count=1, flags=re.M).strip() + "\n"
    return f"[{REMOTE}]\n" + "".join(f"{k} = {v}\n" for k, v in values if str(v) != "")


def save(data):
    """Réglages (JSON lu sur l'entrée standard) : champs publics dans offsite.json, secrets dans rclone.conf.
    Un secret laissé vide conserve celui déjà enregistré."""
    cfg = validate(data)
    previous = settings()
    allowed = TYPES.get(cfg["type"], {"secrets": ()})["secrets"]
    secrets = {k: str(v) for k, v in (data.get("secrets") or {}).items() if k in allowed and str(v).strip()}
    for name, value in secrets.items():
        if name not in ("private_key", "config") and ("\n" in value or "\r" in value or len(value) > 512):
            raise ctl.SeedboxError(f"{name} invalide : une seule ligne attendue")
        if len(value) > 20000:
            raise ctl.SeedboxError(f"{name} trop long")
    if not cfg["type"]:
        CONF_FILE.unlink(missing_ok=True)
    elif secrets or previous["type"] != cfg["type"] or not CONF_FILE.exists():
        ensure_rclone()
        conf = build_conf(cfg["type"], cfg["fields"], secrets)
        CONF_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(CONF_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(conf)
    elif previous["fields"] != cfg["fields"] and cfg["type"] != "raw":
        conf = rebuild_keeping_secrets(CONF_FILE.read_text(), cfg)
        fd = os.open(CONF_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(conf)
    if cfg["enabled"] and not (cfg["type"] and CONF_FILE.exists()):
        raise ctl.SeedboxError("choisissez et renseignez une destination avant d'activer l'envoi")
    write_json(SETTINGS_FILE, cfg, 0o600)
    say("réglages de la sauvegarde hors serveur enregistrés")
    return cfg


def rebuild_keeping_secrets(previous, cfg):
    """Champs publics modifiés sans ressaisir les secrets : on réécrit les lignes publiques, on garde le reste."""
    mapping = {"host": "host", "user": "user", "port": "port", "provider": "provider", "endpoint": "endpoint", "region": "region",
               "access_key": "access_key_id", "url": "url", "vendor": "vendor", "account": "account"}
    lines = previous.splitlines()
    for name, key in mapping.items():
        value = cfg["fields"].get(name, "")
        for index, text in enumerate(lines):
            if re.match(rf"^{key}\s*=", text):
                if value:
                    lines[index] = f"{key} = {value}"
                else:
                    lines[index] = ""
                break
        else:
            if value and name in cfg["fields"]:
                lines.append(f"{key} = {value}")
    return "\n".join(l for l in lines if l.strip() != "") + "\n"


def rclone(*args, check=True, timeout=600):
    command = [RCLONE, "--config", str(CONF_FILE), *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ctl.SeedboxError("rclone n'est pas installé")
    except subprocess.TimeoutExpired:
        raise ctl.SeedboxError("délai dépassé : la destination ne répond pas")
    if check and result.returncode != 0:
        raise ctl.SeedboxError("rclone : " + (result.stderr.strip().splitlines() or ["échec"])[-1][:240])
    return result


def remote_path(cfg=None, name=""):
    cfg = cfg or settings()
    base = cfg["path"].strip("/")
    if cfg["type"] == "s3" and cfg["fields"].get("bucket"):
        base = f"{cfg['fields']['bucket']}/{base}".strip("/")
    elif cfg["type"] == "b2" and cfg["fields"].get("bucket"):
        base = f"{cfg['fields']['bucket']}/{base}".strip("/")
    return f"{REMOTE}:{base}/{name}".rstrip("/") if name else f"{REMOTE}:{base}"


def configured(cfg=None):
    cfg = cfg or settings()
    return bool(cfg["type"]) and CONF_FILE.exists()


def remote_list(cfg=None):
    """[(nom, taille)] des archives présentes à distance, la plus récente en premier."""
    cfg = cfg or settings()
    result = rclone("lsf", "--files-only", "--format", "sp", "--separator", "|", remote_path(cfg), timeout=120)
    entries = []
    for text in result.stdout.splitlines():
        if "|" not in text:
            continue
        size, name = text.split("|", 1)
        if NAME_RE.match(name.strip()):
            entries.append({"name": name.strip(), "size": int(size) if size.strip().isdigit() else 0})
    return sorted(entries, key=lambda e: e["name"], reverse=True)


def connection():
    """Essai de connexion : écrit puis lit un petit fichier dans le dossier distant."""
    cfg = settings()
    if not configured(cfg):
        raise ctl.SeedboxError("aucune destination enregistrée")
    ensure_rclone()
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as probe:
        probe.write("seedboxbuntu\n")
        probe.flush()
        target = remote_path(cfg, ".seedbox-test")
        rclone("copyto", probe.name, target, timeout=120)
        rclone("deletefile", target, timeout=120)
    entries = remote_list(cfg)
    say(f"destination joignable ; {len(entries)} sauvegarde(s) présente(s)")
    return {"ok": True, "backups": len(entries)}


def record(**fields):
    status = read_json(STATUS_FILE)
    status.update(fields)
    write_json(STATUS_FILE, status)


def notify(title, message, level):
    try:
        ctl.alerts().notify(title, message, level)
    except Exception:                                         # noqa: BLE001 - l'alerte ne doit pas masquer l'erreur d'origine
        pass


def push(name):
    """Envoie une archive locale vers la destination, puis applique la rétention distante."""
    cfg = settings()
    if not (cfg["enabled"] and configured(cfg)):
        return None
    if not NAME_RE.match(name or ""):
        raise ctl.SeedboxError("nom de sauvegarde invalide")
    source = ctl.backup().find(name)
    try:
        ensure_rclone()
        say(f"envoi de {name} vers la destination distante")
        rclone("copyto", str(source), remote_path(cfg, name), "--checksum", timeout=3600)
        # Contrôle : l'archive distante a la même taille
        sizes = {e["name"]: e["size"] for e in remote_list(cfg)}
        if sizes.get(name) != source.stat().st_size:
            raise ctl.SeedboxError("envoi incomplet : la taille distante ne correspond pas")
        pruned = prune_remote(cfg)
        record(last_push={"name": name, "at": int(time.time()), "size": source.stat().st_size, "ok": True, "error": ""}, pruned=pruned)
        say(f"copie distante vérifiée ({source.stat().st_size // 1024} Ko)")
        return name
    except ctl.SeedboxError as error:
        record(last_push={"name": name, "at": int(time.time()), "ok": False, "error": str(error)})
        notify("Sauvegarde hors serveur échouée", str(error), "critical")
        raise


def prune_remote(cfg):
    entries = [e for e in remote_list(cfg) if "-avant" not in e["name"]]
    removed = 0
    for old in entries[cfg["keep"]:]:
        rclone("deletefile", remote_path(cfg, old["name"]), check=False, timeout=120)
        removed += 1
    return removed


def push_latest():
    local = ctl.backup().listing()
    if not local:
        raise ctl.SeedboxError("aucune sauvegarde locale à envoyer : créez-en une d'abord")
    return push(local[0]["name"])


def _check_database(path):
    try:
        connection_ = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return connection_.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection_.close()
    except sqlite3.Error:
        return False


def restore_test(source=None):
    """Récupère la dernière sauvegarde (distante si possible), la déchiffre, l'extrait et contrôle les bases."""
    cfg = settings()
    backup = ctl.backup()
    started = time.time()
    where, name = "locale", ""
    try:
        with tempfile.TemporaryDirectory(dir=ctl.STATE if ctl.STATE.exists() else None) as work:
            work = Path(work)
            if source != "local" and configured(cfg):
                ensure_rclone()
                entries = [e for e in remote_list(cfg) if "-avant" not in e["name"]]
                if not entries:
                    raise ctl.SeedboxError("aucune sauvegarde sur la destination distante")
                name, where = entries[0]["name"], "distante"
                archive = work / name
                rclone("copyto", remote_path(cfg, name), str(archive), timeout=3600)
            else:
                local = [e for e in backup.listing() if "-avant" not in e["name"]]
                if not local:
                    raise ctl.SeedboxError("aucune sauvegarde à tester")
                name = local[0]["name"]
                archive = backup.find(name)
            key = backup.KEY_FILE.read_text() if backup.KEY_FILE.exists() else ""
            if not key.strip():
                raise ctl.SeedboxError("clé de chiffrement absente de ce serveur")
            plain = backup.open_archive(archive, key, work)
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
            databases = [p for p in files if p.suffix in (".db", ".sqlite", ".sqlite3") and p.stat().st_size > 0]
            broken = [str(p.relative_to(extracted)) for p in databases if not _check_database(p)]
            if broken:
                raise ctl.SeedboxError("base endommagée dans la sauvegarde : " + ", ".join(broken[:3]))
            essential = [p for p in files if p.name == ".env"]
            if ctl.ENV_FILE.exists() and not essential:
                raise ctl.SeedboxError("le fichier .env est absent de la sauvegarde")
            result = {"ok": True, "name": name, "where": where, "files": len(files), "databases": len(databases),
                      "seconds": round(time.time() - started, 1), "at": int(time.time()), "error": ""}
    except ctl.SeedboxError as error:
        result = {"ok": False, "name": name, "where": where, "files": 0, "databases": 0,
                  "seconds": round(time.time() - started, 1), "at": int(time.time()), "error": str(error)}
        record(last_test=result)
        notify("Test de restauration échoué", f"{name or 'sauvegarde'} : {error}", "critical")
        raise
    record(last_test=result)
    say(f"test de restauration réussi ({where}) : {result['files']} fichiers, {result['databases']} base(s) saine(s)")
    return result


def auto_test():
    """Minuteur mensuel : teste la copie distante si elle existe, sinon la dernière copie locale."""
    return restore_test()


def overview():
    cfg = settings()
    secret_marks = {name: bool(CONF_FILE.exists() and cfg["type"]) for name in TYPES.get(cfg["type"], {"secrets": ()})["secrets"]}
    status = read_json(STATUS_FILE)
    return {"settings": cfg, "types": {k: {"label": v["label"], "fields": list(v["fields"]), "secrets": list(v["secrets"])}
                                       for k, v in TYPES.items()},
            "providers": list(PROVIDERS), "configured": configured(cfg), "installed": installed(), "secrets_present": secret_marks,
            "last_push": status.get("last_push"), "last_test": status.get("last_test")}


def clear():
    SETTINGS_FILE.unlink(missing_ok=True)
    CONF_FILE.unlink(missing_ok=True)
    say("destination distante retirée (les copies déjà envoyées restent sur la destination)")
