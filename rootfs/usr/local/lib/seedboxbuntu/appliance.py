"""SeedBoxBuntu - mise à jour de l'appliance depuis GitHub, sans passer par l'ISO.

Principe
    Les fichiers de l'appliance (rootfs/, stack/, templates/, p2p/ du dépôt) sont les mêmes que ceux
    embarqués dans l'ISO. Cette mise à jour télécharge l'archive d'un commit du dépôt, la vérifie, puis
    fait ce que fait la mise à jour par l'ISO : sauvegarde des fichiers remplacés, remplacement, activation
    des services (postinstall.sh), puis « seedboxctl upgrade » (stack, modèles, liaisons). Aucun redémarrage
    du serveur n'est nécessaire ; seule la console web redémarre, à la toute fin.

    Comptes, secrets, bibliothèques, partages et réglages ne sont jamais touchés.

Sécurité
    - L'archive n'est extraite qu'en fichiers et dossiers réguliers, sans chemin absolu ni « .. ».
    - Rien n'est installé si une vérification échoue (Python, shell, JSON, fichiers indispensables,
      lancement de la nouvelle version, validité du docker-compose.yml).
    - Les fichiers remplacés sont archivés : « seedboxctl appliance rollback » revient en arrière.
    - Le jeton GitHub (le même que celui de P2P Aggregator) n'est envoyé qu'à l'API GitHub, jamais à
      l'adresse de téléchargement vers laquelle GitHub redirige.

Canaux
    « dev » suit la branche du dépôt ; « stable » suit la dernière version publiée (release GitHub).

Garde-fou
    Après une mise à jour, la santé des applications est contrôlée pendant quelques minutes. Si un contrôle
    qui réussissait avant échoue maintenant, la version précédente est rétablie automatiquement, la version
    fautive est mise de côté (la planification ne la réessaie pas) et une alerte est envoyée.

Mises à jour planifiées
    seedbox-appliance.timer lance « seedboxctl appliance auto » aux jours et à l'heure choisis.

Signatures
    Une version publiée peut porter un manifeste (manifest.json, hachage de chaque fichier) signé avec une clé
    SSH (ssh-keygen -Y sign). La clé publique de confiance est enregistrée sur le serveur ; si elle l'est, un
    manifeste dont la signature ou les empreintes ne correspondent pas fait refuser la mise à jour. Avec
    l'option « signature obligatoire », seules passent les versions au manifeste signé valide, ou dont le
    commit est signé et vérifié par GitHub.
"""
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import seedboxctl as ctl

DEFAULT_REPO = os.environ.get("SB_APPLIANCE_REPO", "vincenttisseront/seedboxbuntu")
DEFAULT_REF = "main"
API = os.environ.get("SB_GITHUB_API", "https://api.github.com").rstrip("/")
ROOT = Path(os.environ.get("SB_ROOT", "/"))                 # racine du système (autre que « / » dans les tests)
CONFIG_FILE = ctl.ETC / "appliance-source.json"             # dépôt et branche suivis
INSTALLED_FILE = ctl.STATE / "appliance-source.json"        # commit installé par la console
MANIFEST_FILE = ctl.STATE / "appliance-manifest.json"       # fichiers installés par la console
STATUS_FILE = ctl.STATE / "appliance-update.json"           # état de la mise à jour en cours ou terminée
CHECK_FILE = ctl.STATE / "appliance-check.json"             # dernier contrôle auprès de GitHub
BLOCKED_FILE = ctl.STATE / "appliance-blocked.json"         # version annulée par le garde-fou (non réessayée seule)
SIGNERS_FILE = ctl.ETC / "appliance-signers"                # clés publiques de confiance (format allowed_signers)
BACKUPS = ctl.STATE / "appliance-backups"
WORK = ctl.STATE / "appliance-work"
LOG_FILE = Path(os.environ.get("SB_SELFUPDATE_LOG", "/var/log/seedboxbuntu-selfupdate.log"))
UNIT = "seedbox-selfupdate"
KEEP = 3                                                    # sauvegardes conservées (retours arrière possibles)
CHECK_EVERY = 6 * 3600
CHANNELS = ("dev", "stable")
DEFAULT_AUTO = {"enabled": False, "days": "daily", "time": "04:30"}
GUARD_WAIT = int(os.environ.get("SB_GUARD_WAIT", "240"))    # secondes d'observation après une mise à jour
GUARD_STEP = int(os.environ.get("SB_GUARD_STEP", "15"))
SIGN_NAMESPACE = "seedboxbuntu-release"
SIGN_PRINCIPAL = "seedboxbuntu"
MANIFEST_NAME, SIGNATURE_NAME = "manifest.json", "manifest.json.sig"
MAX_ASSET = 5 * 1024 * 1024
SHARE_DIRS = ("stack", "templates", "p2p")
SHARE_SYSTEM = "/usr/share/seedboxbuntu"
MAX_DOWNLOAD = 100 * 1024 * 1024
MAX_FILES = 5000
REQUIRED = ("rootfs/usr/local/lib/seedboxbuntu/seedboxctl.py", "rootfs/usr/local/lib/seedboxbuntu/postinstall.sh",
            "rootfs/usr/local/lib/seedboxbuntu/console/server.py", "stack/docker-compose.yml", "stack/catalog.json",
            "templates/config/authelia/configuration.yml")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
STAMP_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}-\d{4}$")
# Commande qui applique la mise à jour de la stack avec les NOUVEAUX fichiers (remplacée dans les tests)
UPGRADE_COMMAND = [str(ROOT / "usr/local/sbin/seedboxctl"), "upgrade"]
POSTINSTALL = ROOT / "usr/local/lib/seedboxbuntu/postinstall.sh"


# ----------------------------------------------------------------------------
#  Petits utilitaires
# ----------------------------------------------------------------------------
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


def parse_date(text):
    """Date ISO de GitHub (« 2026-11-10T10:20:00Z ») ou de git (« +01:00 ») -> datetime UTC, ou None."""
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def stamp_of(text):
    """Identifiant de version de l'appliance (AAAA.MM.JJ-HHMM, comparable) tiré de la date d'un commit."""
    date = parse_date(text)
    return date.strftime("%Y.%m.%d-%H%M") if date else ""


def system_path(path):
    """Chemin réel d'un chemin du système installé (« /etc/x » -> ROOT/etc/x)."""
    return ROOT / str(path).lstrip("/")


# ----------------------------------------------------------------------------
#  Configuration et version installée
# ----------------------------------------------------------------------------
def validate_source(repo, ref):
    if not REPO_RE.match(repo or "") or any(part.startswith(".") for part in repo.split("/")):
        raise ctl.SeedboxError("dépôt invalide : utilisez le format « propriétaire/dépôt »")
    if not REF_RE.match(ref or "") or ".." in ref or ref.endswith(("/", ".lock")):
        raise ctl.SeedboxError("branche ou étiquette invalide")


def as_bool(value):
    return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "oui", "yes")


def schedule():
    return ctl.maintenance()


def config():
    data = read_json(CONFIG_FILE)
    repo, ref = data.get("repo") or DEFAULT_REPO, data.get("ref") or DEFAULT_REF
    validate_source(repo, ref)
    channel = data.get("channel") if data.get("channel") in CHANNELS else "dev"
    auto_cfg = {**DEFAULT_AUTO, **{k: v for k, v in (data.get("auto") or {}).items() if k in DEFAULT_AUTO}}
    return {"repo": repo, "ref": ref, "channel": channel, "guard": as_bool(data.get("guard", True)),
            "require_signed": as_bool(data.get("require_signed", False)), "auto": auto_cfg}


def set_config(repo, ref, channel=None, guard=None, require_signed=None, auto_settings=None):
    """Enregistre la source et les options ; seuls les arguments donnés changent."""
    current = config()
    repo, ref = (repo or "").strip() or current["repo"], (ref or "").strip() or current["ref"]
    validate_source(repo, ref)
    if channel is not None and channel not in CHANNELS:
        raise ctl.SeedboxError("canal inconnu : " + ", ".join(CHANNELS))
    auto_cfg = dict(current["auto"])
    if auto_settings:
        module = schedule()
        for key, value in auto_settings.items():
            if key not in DEFAULT_AUTO or value is None:
                continue
            if key == "enabled":
                auto_cfg[key] = as_bool(value)
            elif key == "days":
                auto_cfg[key] = module.check_days(value)
            else:
                auto_cfg[key] = module.check_time(value, "heure")
    result = {"repo": repo, "ref": ref, "channel": channel or current["channel"],
              "guard": current["guard"] if guard is None else as_bool(guard),
              "require_signed": current["require_signed"] if require_signed is None else as_bool(require_signed),
              "auto": auto_cfg}
    write_json(CONFIG_FILE, result, 0o600)
    CHECK_FILE.unlink(missing_ok=True)
    apply_schedule(result["auto"])
    ctl.say(f"source des mises à jour : {repo} ({ref}), canal {result['channel']}")
    return result


def apply_schedule(auto_cfg):
    """Jours et heure de la mise à jour planifiée : fichier de réglage du minuteur systemd."""
    module = schedule()
    changed = module.timer_dropin("seedbox-appliance.timer", module.calendar(auto_cfg["days"], auto_cfg["time"]))
    if changed and module.LIVE:
        module.system("systemctl", "daemon-reload")
        module.system("systemctl", "restart", "seedbox-appliance.timer")


def installed():
    """Version installée : commit connu (installé par la console, ou noté par l'ISO à sa construction)."""
    version = ctl.version()
    state = read_json(INSTALLED_FILE)
    # Fichiers remplacés depuis par une ISO : l'état de la console ne décrit plus l'installation
    if state.get("commit") and state.get("version") == version:
        return {"commit": state["commit"], "date": state.get("date", ""), "message": state.get("message", ""),
                "version": version, "source": "console", "applied": state.get("applied", ""),
                "tag": state.get("tag", ""), "trust": state.get("trust", "")}
    try:
        parts = (ctl.SHARE / "SOURCE_COMMIT").read_text().split()
    except OSError:
        parts = []
    if len(parts) >= 3 and re.fullmatch(r"[0-9a-f]{40}", parts[0]) and parts[2] == version and "modifié" not in parts[3:]:
        return {"commit": parts[0], "date": parts[1], "message": "", "version": version, "source": "iso", "applied": ""}
    return {"commit": "", "date": "", "message": "", "version": version, "source": "iso", "applied": ""}


# ----------------------------------------------------------------------------
#  GitHub
# ----------------------------------------------------------------------------
def token():
    try:
        return ctl.TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def request(url, authorized, accept="application/vnd.github+json"):
    headers = {"User-Agent": "SeedBoxBuntu", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if authorized and token():
        headers["Authorization"] = f"Bearer {token()}"
    return urllib.request.Request(url, headers=headers)


def explain(error, repo):
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (401, 403) and error.headers.get("X-RateLimit-Remaining") == "0":
            return "limite de requêtes GitHub atteinte (enregistrez un jeton GitHub, ou réessayez plus tard)"
        if error.code == 401:
            return "jeton GitHub refusé : enregistrez-en un valide (page P2P Aggregator ou menu de l'appliance)"
        if error.code in (403, 404):
            return (f"dépôt ou branche introuvable : {repo}. Pour un dépôt privé, enregistrez un jeton GitHub "
                    "ayant accès en lecture à ce dépôt (page P2P Aggregator ou menu de l'appliance)")
        return f"GitHub a répondu HTTP {error.code}"
    return f"GitHub injoignable : {getattr(error, 'reason', error)}"


def api_get(path, repo, missing=""):
    try:
        with urllib.request.urlopen(request(API + path, True), timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and missing:
            raise ctl.SeedboxError(missing)
        raise ctl.SeedboxError(explain(error, repo))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
        raise ctl.SeedboxError(explain(error, repo))


def resolve_ref(cfg):
    """(référence à installer, version publiée ou None) : la branche (dev) ou la dernière version publiée (stable)."""
    if cfg.get("channel", "dev") != "stable":
        return cfg["ref"], None
    release = api_get(f"/repos/{cfg['repo']}/releases/latest", cfg["repo"],
                      "aucune version stable publiée dans ce dépôt (choisissez le canal « dev » pour suivre la branche)")
    tag = release.get("tag_name") or ""
    if not REF_RE.match(tag) or ".." in tag:
        raise ctl.SeedboxError("la dernière version publiée porte une étiquette inattendue")
    return tag, release


def remote_head(cfg):
    ref, release = resolve_ref(cfg)
    data = api_get(f"/repos/{cfg['repo']}/commits/{urllib.parse.quote(ref, safe='')}", cfg["repo"])
    commit = data.get("commit") or {}
    sha = data.get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ctl.SeedboxError("réponse de GitHub inattendue")
    verification = commit.get("verification") or {}
    assets = [{"name": a.get("name"), "url": a.get("url")} for a in (release or {}).get("assets", [])
              if a.get("name") in (MANIFEST_NAME, SIGNATURE_NAME) and a.get("url")]
    return {"commit": sha, "short": sha[:7], "date": (commit.get("committer") or {}).get("date", ""),
            "message": (commit.get("message") or "").strip().splitlines()[0][:200] if commit.get("message") else "",
            "ref": ref, "tag": ref if release else "", "notes": ((release or {}).get("body") or "").strip()[:1500],
            "verified": bool(verification.get("verified")), "assets": assets}


def compare(cfg, base, head):
    """Commits entre la version installée et la dernière : (statut, liste de titres), ou None si indisponible."""
    try:
        data = api_get(f"/repos/{cfg['repo']}/compare/{base}...{head}", cfg["repo"])
    except ctl.SeedboxError:
        return None
    titles = [(c.get("commit", {}).get("message") or "").strip().splitlines()[0][:160] for c in data.get("commits", [])
              if c.get("commit", {}).get("message")]
    return data.get("status", ""), list(reversed(titles))[:20]


def evaluate(cfg, head, current):
    """(mise à jour disponible, raison, liste des changements)."""
    if current["commit"]:
        if current["commit"] == head["commit"]:
            return False, "à jour", []
        result = compare(cfg, current["commit"], head["commit"])
        if result is None:
            return True, "autre version disponible", []
        status, titles = result
        if status == "identical":
            return False, "à jour", []
        if status == "behind":
            return False, "la version installée est plus récente que la branche suivie", []
        return True, "mise à jour disponible", titles
    version = current["version"]
    if STAMP_RE.match(version) and stamp_of(head["date"]) and stamp_of(head["date"]) <= version:
        return False, "à jour (l'ISO installée est postérieure au dernier commit)", []
    return True, "mise à jour disponible", [head["message"]] if head["message"] else []


def check():
    """Interroge GitHub et mémorise le résultat (affiché ensuite sans appel réseau)."""
    cfg, current = config(), installed()
    result = {"checked": int(time.time()), "repo": cfg["repo"], "ref": cfg["ref"], "channel": cfg["channel"], "error": ""}
    try:
        head = remote_head(cfg)
        available, reason, changes = evaluate(cfg, head, current)
        blocked = read_json(BLOCKED_FILE).get("commit") == head["commit"]
        result.update(remote=head, available=available, reason=reason, changes=changes, blocked=blocked)
    except ctl.SeedboxError as error:
        result.update(remote=None, available=False, reason="", changes=[], error=str(error))
    write_json(CHECK_FILE, result)
    return result


def refresh_check():
    """Appelé par l'actualisation horaire : au plus un contrôle toutes les CHECK_EVERY secondes."""
    last = read_json(CHECK_FILE)
    if last.get("checked") and time.time() - last["checked"] < CHECK_EVERY:
        return
    check()


# ----------------------------------------------------------------------------
#  Téléchargement et vérification
# ----------------------------------------------------------------------------
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url, out, limit, repo, accept="application/vnd.github+json"):
    """Télécharge url dans out (fichier ouvert en écriture binaire). GitHub redirige vers un autre hôte :
    le jeton n'est envoyé qu'à la première adresse, jamais à la destination de la redirection."""
    opener = urllib.request.build_opener(NoRedirect)
    try:
        try:
            response = opener.open(request(url, True, accept), timeout=60)
        except urllib.error.HTTPError as redirect:
            if redirect.code not in (301, 302, 303, 307, 308) or not redirect.headers.get("Location"):
                raise
            response = urllib.request.urlopen(request(redirect.headers["Location"], False, "*/*"), timeout=120)
        total = 0
        with response:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise ctl.SeedboxError("fichier anormalement volumineux : téléchargement abandonné")
                out.write(block)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise ctl.SeedboxError(explain(error, repo))


def download_archive(cfg, sha, destination):
    with open(destination, "wb") as out:
        fetch(f"{API}/repos/{cfg['repo']}/tarball/{sha}", out, MAX_DOWNLOAD, cfg["repo"])


def download_asset(cfg, asset):
    buffer = io.BytesIO()
    fetch(asset["url"], buffer, MAX_ASSET, cfg["repo"], "application/octet-stream")
    return buffer.getvalue()


def extract(archive, destination):
    """Extraction sûre : fichiers et dossiers réguliers uniquement, dans destination. Renvoie la racine du dépôt."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    count = 0
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ctl.SeedboxError(f"archive refusée : chemin suspect « {member.name} »")
            parts = Path(member.name).parts
            if len(parts) < 2 or not (member.isreg() or member.isdir()):
                continue                                      # dossier racine, liens et fichiers spéciaux ignorés
            count += 1
            if count > MAX_FILES:
                raise ctl.SeedboxError("archive refusée : trop de fichiers")
            target = (root / Path(*parts[1:])).resolve()
            if root not in target.parents:
                raise ctl.SeedboxError(f"archive refusée : chemin suspect « {member.name} »")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            target.chmod(0o755 if member.mode & 0o100 else 0o644)
    return root


def tree_files(base):
    return sorted(p for p in Path(base).rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def validate_tree(tree, compose_ok=None):
    """Contrôles avant toute installation. Lève SeedboxError au premier problème."""
    for name in REQUIRED:
        if not (tree / name).is_file():
            raise ctl.SeedboxError(f"archive incomplète : {name} est absent")
    problems = []
    for path in tree_files(tree / "rootfs"):
        relative = path.relative_to(tree)
        data = path.read_bytes()
        if path.suffix in (".png", ".woff2", ".ico"):
            continue
        if b"\r" in data and (path.suffix in (".py", ".sh") or data.startswith(b"#!")):
            problems.append(f"{relative} : fins de ligne Windows (CRLF)")
        elif path.suffix == ".py":
            try:
                ast.parse(data.decode("utf-8"), filename=str(relative))
            except (SyntaxError, UnicodeDecodeError, ValueError) as error:
                problems.append(f"{relative} : {error}")
        elif path.suffix == ".json":
            try:
                json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as error:
                problems.append(f"{relative} : JSON invalide ({error})")
        elif path.suffix == ".sh" or data.startswith((b"#!/bin/bash", b"#!/usr/bin/env bash")):
            check = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            if check.returncode != 0:
                problems.append(f"{relative} : {check.stderr.strip()[:160]}")
    # Un fichier sudoers invalide bloquerait sudo pour tous les administrateurs
    if shutil.which("visudo"):
        for path in tree_files(tree / "rootfs/etc/sudoers.d"):
            if subprocess.run(["visudo", "-cf", str(path)], capture_output=True, text=True).returncode != 0:
                problems.append(f"{path.relative_to(tree)} : fichier sudoers invalide")
    for name in ("stack/catalog.json",):
        try:
            json.loads((tree / name).read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            problems.append(f"{name} : JSON invalide ({error})")
    if problems:
        raise ctl.SeedboxError("archive refusée : " + " ; ".join(problems[:5]))
    # La nouvelle version doit au moins démarrer (imports, arguments)
    probe = subprocess.run([sys.executable, "-B", str(tree / "rootfs/usr/local/lib/seedboxbuntu/seedboxctl.py"), "--help"],
                           capture_output=True, text=True, cwd=str(tree), timeout=60,
                           env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
                                "SB_BASE": str(tree / ".probe"), "SB_STATE": str(tree / ".probe"), "SB_ETC": str(tree / ".probe")})
    if probe.returncode != 0:
        raise ctl.SeedboxError("la nouvelle version ne démarre pas : " + (probe.stderr or probe.stdout).strip()[-200:])
    if compose_ok is not None and not compose_ok(tree / "stack/docker-compose.yml"):
        raise ctl.SeedboxError("le nouveau docker-compose.yml est invalide")


def compose_valid(path):
    """docker compose config : valable ou non. Sans Docker (ou fichier d'environnement absent), pas d'avis."""
    if not ctl.ENV_FILE.exists() or shutil.which("docker") is None:
        return True
    return subprocess.run(["docker", "compose", "--env-file", str(ctl.ENV_FILE), "-f", str(path), "config", "-q"],
                          capture_output=True, text=True, timeout=60).returncode == 0


# ----------------------------------------------------------------------------
#  Signatures : manifeste de la version publiée
# ----------------------------------------------------------------------------
HASHED_DIRS = ("rootfs", "stack", "templates", "p2p")
KEY_RE = re.compile(r"((?:ssh|sk-ssh|ecdsa-sha2)-[A-Za-z0-9@.-]+)\s+([A-Za-z0-9+/=]{20,})")


def tree_hashes(tree):
    """{chemin relatif : SHA-256} des fichiers de l'appliance (même calcul que tools/manifest.py)."""
    return {str(path.relative_to(tree)): hashlib.sha256(path.read_bytes()).hexdigest()
            for sub in HASHED_DIRS for path in tree_files(Path(tree) / sub)}


def normalize_signers(text):
    """Lignes « allowed_signers » à partir de clés publiques SSH collées telles quelles."""
    lines = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        found = KEY_RE.search(raw)
        if not found:
            raise ctl.SeedboxError("clé publique SSH non reconnue (ssh-ed25519 AAAA... attendu)")
        key = f"{found.group(1)} {found.group(2)}"
        if not shutil.which("ssh-keygen"):
            raise ctl.SeedboxError("ssh-keygen introuvable : paquet openssh-client à installer")
        with tempfile.NamedTemporaryFile("w", suffix=".pub") as handle:
            handle.write(key + "\n")
            handle.flush()
            check = subprocess.run(["ssh-keygen", "-l", "-f", handle.name], capture_output=True, text=True)
        if check.returncode != 0:
            raise ctl.SeedboxError("clé publique SSH invalide")
        lines.append(f'{SIGN_PRINCIPAL} namespaces="{SIGN_NAMESPACE}" {key}')
    return lines


def signer_lines():
    try:
        return [line for line in SIGNERS_FILE.read_text().splitlines() if line.strip() and not line.startswith("#")]
    except OSError:
        return []


def signer_fingerprints():
    result = []
    for line in signer_lines():
        found = KEY_RE.search(line)
        if not found or not shutil.which("ssh-keygen"):
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".pub") as handle:
            handle.write(f"{found.group(1)} {found.group(2)}\n")
            handle.flush()
            out = subprocess.run(["ssh-keygen", "-l", "-f", handle.name], capture_output=True, text=True).stdout.split()
        if len(out) >= 2:
            result.append(out[1])
    return result


def set_signers(text):
    """Enregistre les clés de confiance (vide : aucune)."""
    lines = normalize_signers(text)
    if not lines:
        SIGNERS_FILE.unlink(missing_ok=True)
        ctl.say("aucune clé de signature de confiance")
        return []
    SIGNERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIGNERS_FILE.write_text("# SeedBoxBuntu : clés qui signent les versions publiées\n" + "\n".join(lines) + "\n")
    SIGNERS_FILE.chmod(0o644)
    ctl.say(f"{len(lines)} clé(s) de signature de confiance enregistrée(s)")
    return signer_fingerprints()


def verify_signature(manifest, signature):
    if not shutil.which("ssh-keygen"):
        raise ctl.SeedboxError("ssh-keygen introuvable : signature impossible à vérifier")
    with tempfile.TemporaryDirectory() as directory:
        sig_file = Path(directory) / "manifest.sig"
        sig_file.write_bytes(signature)
        check = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(SIGNERS_FILE), "-I", SIGN_PRINCIPAL,
                                "-n", SIGN_NAMESPACE, "-s", str(sig_file)], input=manifest, capture_output=True)
    return check.returncode == 0


def check_manifest(manifest, head, tree):
    """Le manifeste signé décrit-il exactement ce commit et ces fichiers ?"""
    try:
        data = json.loads(manifest.decode("utf-8"))
        expected = data["files"]
    except (ValueError, KeyError, UnicodeDecodeError):
        raise ctl.SeedboxError("manifeste illisible : version refusée")
    if data.get("commit") != head["commit"] or (head.get("tag") and data.get("tag") != head["tag"]):
        raise ctl.SeedboxError("le manifeste signé ne correspond pas à cette version (rejeu d'une ancienne signature ?)")
    actual = tree_hashes(tree)
    changed = sorted(name for name in actual if name in expected and actual[name] != expected[name])
    extra = sorted(set(actual) - set(expected))
    missing = sorted(set(expected) - set(actual))
    if changed or extra or missing:
        detail = (changed + extra + missing)[:3]
        raise ctl.SeedboxError("fichiers différents du manifeste signé : " + ", ".join(detail))


def verify_release(cfg, head, tree):
    """Authenticité de la version téléchargée. Renvoie {"state", "detail"} ; lève SeedboxError si refusée."""
    assets = {a["name"]: a for a in head.get("assets", [])}
    if MANIFEST_NAME in assets and signer_lines():
        if SIGNATURE_NAME not in assets:
            raise ctl.SeedboxError("version publiée sans signature du manifeste : refusée")
        manifest = download_asset(cfg, assets[MANIFEST_NAME])
        if not verify_signature(manifest, download_asset(cfg, assets[SIGNATURE_NAME])):
            raise ctl.SeedboxError("signature du manifeste invalide : version refusée")
        check_manifest(manifest, head, tree)
        return {"state": "signed", "detail": "manifeste signé valide, empreintes de tous les fichiers conformes"}
    if cfg["require_signed"]:
        if head.get("verified"):
            return {"state": "commit", "detail": "commit signé, vérifié par GitHub"}
        raise ctl.SeedboxError("signature obligatoire : ni manifeste signé valide (clé de confiance et manifeste "
                               "signé dans la version publiée), ni commit signé et vérifié par GitHub")
    return {"state": "none", "detail": "version non signée (signature facultative)"}


# ----------------------------------------------------------------------------
#  Installation des fichiers
# ----------------------------------------------------------------------------
def mode_for(system, source_mode):
    """Mêmes droits que lib/40-appliance.sh (appliance_mode) : indépendants du bit exécutable du dépôt."""
    if system.startswith("/etc/sudoers.d/"):
        return 0o440
    if system.startswith(("/usr/local/sbin/", "/usr/local/bin/", "/etc/update-motd.d/")):
        return 0o755
    if system.endswith(("/seedboxctl.py", "/server.py", "/postinstall.sh")):
        return 0o755
    if system.startswith(SHARE_SYSTEM + "/"):
        return 0o644
    return 0o755 if source_mode & 0o100 else 0o644


def plan(tree):
    """Fichiers à installer : [(chemin dans le système, fichier source, droits)]."""
    items = []
    for path in tree_files(tree / "rootfs"):
        system = "/" + str(path.relative_to(tree / "rootfs"))
        items.append((system, path, mode_for(system, path.stat().st_mode)))
    for sub in SHARE_DIRS:
        for path in tree_files(tree / sub):
            system = f"{SHARE_SYSTEM}/{sub}/{path.relative_to(tree / sub)}"
            items.append((system, path, mode_for(system, path.stat().st_mode)))
    return items


def place(source, target, mode):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.sb-new")
    shutil.copyfile(source, temp)
    os.chmod(temp, mode)
    if os.geteuid() == 0:
        os.chown(temp, 0, 0)
    os.replace(temp, target)


def restore_archive(archive):
    """Remet en place les fichiers archivés, supprime ceux qui avaient été créés, restaure l'état noté."""
    with tarfile.open(archive, "r:gz") as tar:
        meta = json.loads(tar.extractfile("meta.json").read().decode("utf-8"))
        members = [m for m in tar.getmembers() if m.name != "meta.json"]
        try:
            tar.extractall(ROOT, members=members, filter="tar")
        except TypeError:                                    # Python sans filtres d'extraction
            tar.extractall(ROOT, members=members)
    for system in meta.get("created", []):
        system_path(system).unlink(missing_ok=True)
    for key, path in (("installed_before", INSTALLED_FILE), ("manifest_before", MANIFEST_FILE)):
        if meta.get(key) is None:
            path.unlink(missing_ok=True)
        else:
            write_json(path, meta[key])
    return meta


def prune_backups():
    for old in sorted(BACKUPS.glob("*.tar.gz"))[:-KEEP]:
        old.unlink(missing_ok=True)


def install(tree, cfg, head, trust=None):
    """Sauvegarde puis remplacement. En cas d'erreur en cours de route, tout est remis comme avant."""
    items = plan(tree)
    new_set = {system for system, _, _ in items}
    version_system = f"{SHARE_SYSTEM}/VERSION"
    previous_manifest = read_json(MANIFEST_FILE, [])
    previous_manifest = previous_manifest if isinstance(previous_manifest, list) else []
    removable = [s for s in previous_manifest if s not in new_set and s != version_system and s.startswith("/")]
    BACKUPS.mkdir(parents=True, exist_ok=True)
    BACKUPS.chmod(0o700)
    archive = BACKUPS / f"{time.strftime('%Y%m%d-%H%M%S')}-{head['commit'][:7]}.tar.gz"
    created = []
    touched = [s for s, _, _ in items] + [version_system] + removable
    with tarfile.open(archive, "w:gz") as tar:
        for system in touched:
            target = system_path(system)
            if target.is_file():
                tar.add(target, arcname=system.lstrip("/"), recursive=False)
            else:
                created.append(system)
        meta = {"created": created, "from": ctl.version(), "to": head["commit"],
                "installed_before": read_json(INSTALLED_FILE) or None,
                "manifest_before": previous_manifest or None, "made": time.strftime("%FT%T%z")}
        data = json.dumps(meta, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo("meta.json")
        info.size = len(data)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))
    archive.chmod(0o600)
    new_version = stamp_of(head["date"]) or ctl.version()
    try:
        for system, source, mode in items:
            place(source, system_path(system), mode)
        for system in removable:
            system_path(system).unlink(missing_ok=True)
        version_file = system_path(version_system)
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(new_version + "\n")
        write_json(INSTALLED_FILE, {"repo": cfg["repo"], "ref": cfg["ref"], "commit": head["commit"], "date": head["date"],
                                    "message": head["message"], "version": new_version, "applied": time.strftime("%FT%T%z"),
                                    "channel": cfg.get("channel", "dev"), "tag": head.get("tag", ""),
                                    "trust": (trust or {}).get("state", "none")})
        write_json(MANIFEST_FILE, sorted(new_set))
    except BaseException:
        restore_archive(archive)
        archive.unlink(missing_ok=True)
        raise
    prune_backups()
    return len(items), len(removable), new_version, archive


# ----------------------------------------------------------------------------
#  État de l'opération en cours
# ----------------------------------------------------------------------------
def status_write(**fields):
    current = read_json(STATUS_FILE)
    current.update(fields)
    write_json(STATUS_FILE, current)


def phase(text):
    ctl.say(text)
    status_write(phase=text)


def process_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    try:
        return b"seedboxctl" in Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except OSError:
        return True                                           # pas de /proc (tests) : le signal suffit


def current_status():
    status = read_json(STATUS_FILE)
    if status.get("state") == "running":
        alive = process_alive(status["pid"]) if status.get("pid") else time.time() - status.get("started", 0) < 90
        if not alive:
            status.update(state="error", error="l'opération a été interrompue", phase="interrompue")
    return status


def log_tail(limit=6000):
    try:
        return LOG_FILE.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


# ----------------------------------------------------------------------------
#  Garde-fou : la santé des applications après une mise à jour
# ----------------------------------------------------------------------------
def health_snapshot():
    """{contrôle : état} d'après « seedboxctl check », ou None si le contrôle est indisponible."""
    try:
        items = ctl.health().run_checks()
    except Exception:                                         # noqa: BLE001 - un contrôle cassé ne doit rien bloquer
        return None
    return {f"{item['group']} : {item['label']}": item["status"] for item in items}


def guard_wait(baseline):
    """Observe jusqu'à GUARD_WAIT secondes. (sain, contrôles qui échouent maintenant et réussissaient avant)."""
    known = {name for name, state in baseline.items() if state == "fail"}
    deadline = time.time() + GUARD_WAIT
    problems = []
    while True:
        snapshot = health_snapshot()
        if snapshot is not None:
            problems = sorted(name for name, state in snapshot.items() if state == "fail" and name not in known)
            if not problems:
                return True, []
        if time.time() >= deadline:
            return snapshot is None, problems
        phase(f"contrôle de santé après la mise à jour ({len(problems)} problème(s), nouvel essai)" if problems
              else "contrôle de santé après la mise à jour")
        time.sleep(GUARD_STEP)


def alert(title, message, level):
    try:
        ctl.alerts().notify(title, message, level)
    except Exception:                                         # noqa: BLE001 - une alerte en échec ne change pas le résultat
        pass


def overview():
    current = installed()
    last = read_json(CHECK_FILE)
    try:
        cfg = config()
    except ctl.SeedboxError:
        cfg = {"repo": DEFAULT_REPO, "ref": DEFAULT_REF, "channel": "dev", "guard": True, "require_signed": False,
               "auto": dict(DEFAULT_AUTO)}
    return {"config": cfg, "installed": current, "token": bool(token()), "check": last or None,
            "signers": signer_fingerprints(), "blocked": read_json(BLOCKED_FILE) or None,
            "status": current_status(), "log": log_tail(), "can_rollback": bool(sorted(BACKUPS.glob("*.tar.gz"))),
            "backups": [p.name for p in sorted(BACKUPS.glob("*.tar.gz"))][-KEEP:],
            "pending": (ctl.STATE / "upgrade-pending").exists()}


# ----------------------------------------------------------------------------
#  Opérations
# ----------------------------------------------------------------------------
def finalize(label):
    """Après le remplacement des fichiers : services, puis mise à jour de la stack avec la NOUVELLE version."""
    phase("activation des services")
    if POSTINSTALL.exists():
        env = dict(os.environ, PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        ctl.run("bash", POSTINSTALL, env=env, capture=True, check=False)
    ctl.run("systemctl", "daemon-reload", check=False, capture=True)
    (ctl.STATE).mkdir(parents=True, exist_ok=True)
    (ctl.STATE / "upgrade-pending").write_text(f"{label}\n")
    phase("application à la stack (téléchargements éventuels, quelques minutes)")
    result = ctl.run(*UPGRADE_COMMAND, check=False)
    if result.returncode != 0:
        # La mise à jour reste en attente : le service dédié réessaie toutes les 2 minutes jusqu'à la réussite
        ctl.run("systemctl", "start", "--no-block", "seedbox-upgrade.service", check=False, capture=True)
        return False
    return True


def restart_console():
    ctl.run("systemctl", "restart", "seedbox-console.service", check=False, capture=True)


def finish_status(state, phase_text, error=""):
    status_write(state=state, phase=phase_text, error=error, finished=time.time())


def apply(ref=None, force=False):
    """Télécharge, vérifie et installe la dernière version du dépôt. Renvoie False si déjà à jour.
    Lève SeedboxError en cas d'échec, y compris quand le garde-fou a rétabli la version précédente."""
    status_write(state="running", action="apply", pid=os.getpid(), started=time.time(), finished=None, error="",
                 phase="recherche de la dernière version", trust="")
    try:
        cfg = config()
        if ref:
            validate_source(cfg["repo"], ref)
            cfg.update(ref=ref, channel="dev")                # une référence demandée explicitement : branche ou étiquette
        ctl.say(f"source : {cfg['repo']} ({cfg['ref'] if cfg['channel'] == 'dev' else 'dernière version stable'})")
        head, current = remote_head(cfg), installed()
        available, reason, _ = evaluate(cfg, head, current)
        if not available and not force:
            ctl.say(f"{reason} ({head['short']})")
            finish_status("ok", f"{reason} ({head['short']})")
            return False
        phase(f"téléchargement de {head['short']}" + (f" ({head['tag']})" if head["tag"] else "") + f" : {head['message']}")
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True, exist_ok=True)
        archive = WORK / "source.tar.gz"
        download_archive(cfg, head["commit"], archive)
        phase("vérification des fichiers")
        free = shutil.disk_usage(ctl.STATE if ctl.STATE.exists() else ROOT).free
        if free < 300 * 1024 * 1024:
            raise ctl.SeedboxError("espace disque insuffisant (300 Mo nécessaires)")
        tree = extract(archive, WORK / "tree")
        trust = verify_release(cfg, head, tree)
        ctl.say(f"authenticité : {trust['detail']}")
        status_write(trust=trust["state"])
        validate_tree(tree, lambda new: not compose_valid(Path(ctl.BASE / "docker-compose.yml")) or compose_valid(new))
        baseline = health_snapshot() if cfg["guard"] else None
        before = ctl.version()
        phase("sauvegarde puis remplacement des fichiers de l'appliance")
        count, removed, new_version, backup_file = ctl.locked(install)(tree, cfg, head, trust)
        ctl.say(f"{count} fichiers installés" + (f", {removed} retirés" if removed else "") + f" : version {new_version}")
    except ctl.SeedboxError as error:
        finish_status("error", "échec", str(error))
        raise
    except Exception as error:                                # noqa: BLE001 - tout échec est consigné pour la console
        finish_status("error", "échec", f"erreur inattendue : {error}")
        raise ctl.SeedboxError(f"erreur inattendue : {error}")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    BLOCKED_FILE.unlink(missing_ok=True)
    complete = finalize(f"de {before} vers {new_version} (console, {head['short']})")
    healthy, problems = True, []
    if baseline is not None:
        healthy, problems = guard_wait(baseline)
    elif cfg["guard"]:
        ctl.say("garde-fou : contrôle de santé indisponible avant la mise à jour, pas de retour automatique")
    if not healthy:
        return auto_rollback(backup_file, head, before, new_version, problems)
    if complete:
        finish_status("ok", f"installée : {new_version} ({head['short']})")
    else:
        finish_status("partial", "fichiers installés ; la stack se termine en arrière-plan (nouvel essai automatique)")
    CHECK_FILE.unlink(missing_ok=True)
    ctl.say("redémarrage de la console web")
    restart_console()
    return True


def auto_rollback(archive, head, before, new_version, problems):
    """Le garde-fou a constaté une régression : version précédente rétablie, version fautive mise de côté."""
    reason = "contrôles en échec depuis la mise à jour : " + " ; ".join(problems[:4])
    phase("régression constatée : retour automatique à la version précédente")
    try:
        ctl.locked(restore_archive)(archive)
    except Exception as error:                                # noqa: BLE001
        finish_status("error", "échec", f"{reason} ; retour arrière impossible : {error}")
        alert("Mise à jour de l'appliance : retour arrière impossible", f"{reason}. {error}", "critical")
        raise ctl.SeedboxError(f"{reason} ; retour arrière impossible : {error}")
    archive.unlink(missing_ok=True)
    write_json(BLOCKED_FILE, {"commit": head["commit"], "short": head["short"], "tag": head.get("tag", ""),
                              "reason": reason, "at": time.strftime("%FT%T%z")})
    CHECK_FILE.unlink(missing_ok=True)
    finalize(f"de {new_version} vers {before} (retour automatique, {head['short']} écartée)")
    message = f"{head['short']} annulée : {reason}"
    finish_status("rolled_back", f"version précédente rétablie ({ctl.version()})", message)
    alert("Mise à jour de l'appliance annulée automatiquement", message, "critical")
    ctl.say("redémarrage de la console web")
    restart_console()
    raise ctl.SeedboxError("mise à jour annulée automatiquement : " + reason)


def auto():
    """Appelée par le minuteur seedbox-appliance : met à jour seule si la planification est activée."""
    cfg = config()
    if not cfg["auto"]["enabled"]:
        ctl.say("mise à jour automatique de l'appliance désactivée")
        return False
    if current_status().get("state") == "running":
        ctl.say("une mise à jour de l'appliance est déjà en cours")
        return False
    result = check()
    if result["error"]:
        raise ctl.SeedboxError(result["error"])
    if not result["available"]:
        ctl.say("appliance à jour")
        return False
    head = result["remote"]
    if read_json(BLOCKED_FILE).get("commit") == head["commit"]:
        ctl.say(f"version {head['short']} écartée par le garde-fou : pas de nouvel essai automatique")
        return False
    try:
        apply()
    except ctl.SeedboxError as error:
        if "annulée automatiquement" not in str(error):       # le garde-fou a déjà alerté
            alert("Mise à jour automatique de l'appliance en échec", str(error), "critical")
        raise
    alert("Appliance mise à jour", f"version {installed()['version']} ({head['short']}) installée automatiquement.", "ok")
    return True


def rollback():
    """Annule la dernière mise à jour faite depuis la console : fichiers, version, état."""
    status_write(state="running", action="rollback", pid=os.getpid(), started=time.time(), finished=None, error="",
                 phase="retour à la version précédente")
    archives = sorted(BACKUPS.glob("*.tar.gz"))
    if not archives:
        finish_status("error", "échec", "aucune sauvegarde de mise à jour à restaurer")
        raise ctl.SeedboxError("aucune sauvegarde de mise à jour à restaurer")
    last = archives[-1]
    try:
        before = ctl.version()
        meta = ctl.locked(restore_archive)(last)
    except Exception as error:                                # noqa: BLE001
        finish_status("error", "échec", str(error))
        raise ctl.SeedboxError(f"retour arrière impossible : {error}")
    last.unlink(missing_ok=True)
    CHECK_FILE.unlink(missing_ok=True)
    ctl.say(f"fichiers restaurés : version {ctl.version()}")
    complete = finalize(f"de {before} vers {ctl.version()} (retour arrière depuis la console)")
    finish_status("ok" if complete else "partial", f"version précédente rétablie : {ctl.version()}")
    ctl.say("redémarrage de la console web")
    restart_console()
    return meta


def launch(action, ref=None, force=False):
    """Démarre apply/rollback dans une unité systemd indépendante de la console, qui redémarre à la fin."""
    status = current_status()
    if status.get("state") == "running":
        raise ctl.SeedboxError("une mise à jour de l'appliance est déjà en cours")
    if action not in ("apply", "rollback"):
        raise ctl.SeedboxError("action inconnue")
    command = [str(ROOT / "usr/local/sbin/seedboxctl"), "appliance", action]
    if action == "apply":
        if ref:
            validate_source(config()["repo"], ref)
            command += ["--ref", ref]
        if force:
            command.append("--force")
    status_write(state="running", action=action, pid=None, started=time.time(), finished=None, error="",
                 phase="démarrage")
    result = ctl.run("systemd-run", "--unit", UNIT, "--collect", "--quiet", "--setenv=PYTHONUNBUFFERED=1",
                     f"--property=StandardOutput=truncate:{LOG_FILE}", "--property=StandardError=inherit",
                     *command, check=False, capture=True)
    if result.returncode != 0:
        finish_status("error", "échec", "démarrage impossible : " + (result.stderr or "").strip()[-200:])
        raise ctl.SeedboxError("démarrage de la mise à jour impossible : " + (result.stderr or "").strip()[-200:])
