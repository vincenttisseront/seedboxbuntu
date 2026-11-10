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
"""
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
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
BACKUPS = ctl.STATE / "appliance-backups"
WORK = ctl.STATE / "appliance-work"
LOG_FILE = Path(os.environ.get("SB_SELFUPDATE_LOG", "/var/log/seedboxbuntu-selfupdate.log"))
UNIT = "seedbox-selfupdate"
KEEP = 3                                                    # sauvegardes conservées (retours arrière possibles)
CHECK_EVERY = 6 * 3600
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


def config():
    data = read_json(CONFIG_FILE)
    repo, ref = data.get("repo") or DEFAULT_REPO, data.get("ref") or DEFAULT_REF
    validate_source(repo, ref)
    return {"repo": repo, "ref": ref}


def set_config(repo, ref):
    repo, ref = (repo or "").strip(), (ref or "").strip()
    validate_source(repo, ref)
    write_json(CONFIG_FILE, {"repo": repo, "ref": ref}, 0o600)
    CHECK_FILE.unlink(missing_ok=True)
    ctl.say(f"source des mises à jour : {repo} ({ref})")


def installed():
    """Version installée : commit connu (installé par la console, ou noté par l'ISO à sa construction)."""
    version = ctl.version()
    state = read_json(INSTALLED_FILE)
    # Fichiers remplacés depuis par une ISO : l'état de la console ne décrit plus l'installation
    if state.get("commit") and state.get("version") == version:
        return {"commit": state["commit"], "date": state.get("date", ""), "message": state.get("message", ""),
                "version": version, "source": "console", "applied": state.get("applied", "")}
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


def api_get(path, repo):
    try:
        with urllib.request.urlopen(request(API + path, True), timeout=20) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
        raise ctl.SeedboxError(explain(error, repo))


def remote_head(cfg):
    ref = cfg["ref"]
    data = api_get(f"/repos/{cfg['repo']}/commits/{urllib.parse.quote(ref, safe='')}", cfg["repo"])
    commit = data.get("commit") or {}
    sha = data.get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ctl.SeedboxError("réponse de GitHub inattendue")
    return {"commit": sha, "short": sha[:7], "date": (commit.get("committer") or {}).get("date", ""),
            "message": (commit.get("message") or "").strip().splitlines()[0][:200] if commit.get("message") else ""}


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
    result = {"checked": int(time.time()), "repo": cfg["repo"], "ref": cfg["ref"], "error": ""}
    try:
        head = remote_head(cfg)
        available, reason, changes = evaluate(cfg, head, current)
        result.update(remote=head, available=available, reason=reason, changes=changes)
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


def download_archive(cfg, sha, destination):
    """Archive du commit. GitHub redirige vers codeload : le jeton n'est pas renvoyé à cette adresse."""
    opener = urllib.request.build_opener(NoRedirect)
    url = f"{API}/repos/{cfg['repo']}/tarball/{sha}"
    try:
        try:
            response = opener.open(request(url, True, "application/vnd.github+json"), timeout=60)
        except urllib.error.HTTPError as redirect:
            if redirect.code not in (301, 302, 303, 307, 308) or not redirect.headers.get("Location"):
                raise
            response = urllib.request.urlopen(request(redirect.headers["Location"], False, "*/*"), timeout=120)
        total = 0
        with response, open(destination, "wb") as out:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                total += len(block)
                if total > MAX_DOWNLOAD:
                    raise ctl.SeedboxError("archive anormalement volumineuse : téléchargement abandonné")
                out.write(block)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise ctl.SeedboxError(explain(error, cfg["repo"]))


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


def install(tree, cfg, head):
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
                                    "message": head["message"], "version": new_version, "applied": time.strftime("%FT%T%z")})
        write_json(MANIFEST_FILE, sorted(new_set))
    except BaseException:
        restore_archive(archive)
        archive.unlink(missing_ok=True)
        raise
    prune_backups()
    return len(items), len(removable), new_version


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


def overview():
    current = installed()
    last = read_json(CHECK_FILE)
    try:
        cfg = config()
    except ctl.SeedboxError:
        cfg = {"repo": DEFAULT_REPO, "ref": DEFAULT_REF}
    return {"config": cfg, "installed": current, "token": bool(token()), "check": last or None,
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
    """Télécharge, vérifie et installe la dernière version du dépôt. Renvoie False si déjà à jour."""
    status_write(state="running", action="apply", pid=os.getpid(), started=time.time(), finished=None, error="",
                 phase="recherche de la dernière version")
    try:
        cfg = config()
        if ref:
            validate_source(cfg["repo"], ref)
            cfg["ref"] = ref
        ctl.say(f"source : {cfg['repo']} ({cfg['ref']})")
        head, current = remote_head(cfg), installed()
        available, reason, _ = evaluate(cfg, head, current)
        if not available and not force:
            ctl.say(f"{reason} ({head['short']})")
            finish_status("ok", f"{reason} ({head['short']})")
            return False
        phase(f"téléchargement de {head['short']} : {head['message']}")
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True, exist_ok=True)
        archive = WORK / "source.tar.gz"
        download_archive(cfg, head["commit"], archive)
        phase("vérification des fichiers")
        free = shutil.disk_usage(ctl.STATE if ctl.STATE.exists() else ROOT).free
        if free < 300 * 1024 * 1024:
            raise ctl.SeedboxError("espace disque insuffisant (300 Mo nécessaires)")
        tree = extract(archive, WORK / "tree")
        validate_tree(tree, lambda new: not compose_valid(Path(ctl.BASE / "docker-compose.yml")) or compose_valid(new))
        before = ctl.version()
        phase("sauvegarde puis remplacement des fichiers de l'appliance")
        count, removed, new_version = ctl.locked(install)(tree, cfg, head)
        ctl.say(f"{count} fichiers installés" + (f", {removed} retirés" if removed else "") + f" : version {new_version}")
    except ctl.SeedboxError as error:
        finish_status("error", "échec", str(error))
        raise
    except Exception as error:                                # noqa: BLE001 - tout échec est consigné pour la console
        finish_status("error", "échec", f"erreur inattendue : {error}")
        raise ctl.SeedboxError(f"erreur inattendue : {error}")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    complete = finalize(f"de {before} vers {new_version} (console, {head['short']})")
    if complete:
        finish_status("ok", f"installée : {new_version} ({head['short']})")
    else:
        finish_status("partial", "fichiers installés ; la stack se termine en arrière-plan (nouvel essai automatique)")
    CHECK_FILE.unlink(missing_ok=True)
    ctl.say("redémarrage de la console web")
    restart_console()
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
