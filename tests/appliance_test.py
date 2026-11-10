"""Mise à jour de l'appliance depuis GitHub (seedboxctl appliance) : faux GitHub local, aucun accès réseau ni root.

Cas couverts
  - version installée : commit noté par l'ISO, ancienne note ignorée, état de la console
  - recherche : mise à jour disponible / à jour / installation plus récente, liste des nouveautés
  - installation : fichiers, droits (identiques à lib/40-appliance.sh), fichiers créés et retirés, VERSION, sauvegarde
  - jeton envoyé à l'API seulement, jamais à l'adresse de téléchargement
  - archives refusées (Python invalide, chemin hors dossier, fichier indispensable absent) : rien n'est modifié
  - échec en cours d'installation : tout est remis comme avant
  - retour arrière : fichiers, VERSION et état restaurés
  - étape finale en échec : installation conservée, nouvel essai planifié
  - routes de la console
"""
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbapp"))
SYSTEM = root / "system"
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st",
                    "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
os.environ["SB_ROOT"] = str(SYSTEM)
os.environ["SB_SHARE"] = str(SYSTEM / "usr/share/seedboxbuntu")
os.environ["SB_SELFUPDATE_LOG"] = str(root / "selfupdate.log")
os.environ["SB_APPLIANCE_REPO"] = "octo/seedboxbuntu"
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("https_proxy", None)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import appliance as app  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


# --- dépôt de démonstration : l'arbre des sources, avec trois états successifs ------------------------------------
KEEP_DIRS = ("rootfs", "stack", "templates", "p2p")


def make_tree(destination):
    for sub in KEEP_DIRS:
        shutil.copytree(SRC / sub, destination / sub, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destination


def tarball(tree, sha, mutate=None, members=None):
    """Archive à la manière de GitHub : un dossier racine « octo-seedboxbuntu-<sha7> »."""
    work = root / f"tree-{sha[:7]}"
    shutil.rmtree(work, ignore_errors=True)
    make_tree(work)
    if mutate:
        mutate(work)
    buffer = io.BytesIO()
    top = f"octo-seedboxbuntu-{sha[:7]}"
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(work.rglob("*")):
            tar.add(path, arcname=f"{top}/{path.relative_to(work)}", recursive=False)
        for name, data in (members or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


SHA = {"v0": "0" * 39 + "a", "v1": "1" * 39 + "b", "v2": "2" * 39 + "c", "bad": "3" * 39 + "d", "evil": "4" * 39 + "e",
       "short": "5" * 39 + "f"}
DATES = {"v0": "2026-11-10T08:00:00Z", "v1": "2026-11-10T10:20:00Z", "v2": "2026-11-11T09:30:00Z", "bad": "2026-11-12T09:00:00Z",
         "evil": "2026-11-12T10:00:00Z", "short": "2026-11-12T11:00:00Z"}
LIB = "rootfs/usr/local/lib/seedboxbuntu"
LIBSYS = "usr/local/lib/seedboxbuntu"


def add_v1(tree):
    (tree / LIB / "health.py").write_text((tree / LIB / "health.py").read_text() + "\n# v1\n")
    (tree / "rootfs/usr/share/seedboxbuntu").mkdir(parents=True, exist_ok=True)
    (tree / "rootfs/usr/share/seedboxbuntu/nouveau.txt").write_text("fichier ajouté\n")
    (tree / "templates/config/ancien.yml").write_text("a: 1\n")


def add_v2(tree):
    add_v1(tree)
    (tree / LIB / "health.py").write_text((tree / LIB / "health.py").read_text() + "# v2\n")
    (tree / "templates/config/ancien.yml").unlink()            # retiré du dépôt : retiré du serveur
    (tree / "rootfs/usr/share/seedboxbuntu/nouveau.txt").write_text("fichier modifié\n")


def broken_python(tree):
    (tree / LIB / "health.py").write_text("def casse(:\n")


def without_required(tree):
    (tree / "stack/catalog.json").unlink()


MUTATIONS = {"v0": None, "v1": add_v1, "v2": add_v2, "bad": broken_python, "short": without_required, "evil": None}
TARBALLS = {}
requests_seen = []
STATE_SERVER = {"head": "v1", "known": {"v0", "v1", "v2"}, "repo_exists": True}


class FakeGithub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, payload=None, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(payload or {}).encode()
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        requests_seen.append((self.path, self.headers.get("Authorization")))
        path = self.path
        if not STATE_SERVER["repo_exists"] and path.startswith("/repos/"):
            return self.reply(404, {"message": "Not Found"})
        name_by_sha = {sha: name for name, sha in SHA.items()}
        if path.startswith("/repos/octo/seedboxbuntu/commits/"):
            name = STATE_SERVER["head"]
            return self.reply(200, {"sha": SHA[name], "commit": {"committer": {"date": DATES[name]}, "message": f"Version {name}\n\ndétails"}})
        if path.startswith("/repos/octo/seedboxbuntu/compare/"):
            base, head = path.rsplit("/", 1)[1].split("...")
            status = "identical" if base == head else ("behind" if DATES[name_by_sha[base]] > DATES[name_by_sha[head]] else "ahead")
            commits = [{"commit": {"message": "Second changement\n\nx"}}, {"commit": {"message": "Premier changement"}}]
            return self.reply(200, {"status": status, "commits": commits if status == "ahead" else []})
        if path.startswith("/repos/octo/seedboxbuntu/tarball/"):
            sha = path.rsplit("/", 1)[1]
            return self.reply(302, headers={"Location": f"http://127.0.0.1:{self.server.server_port}/codeload/{sha}.tar.gz"})
        if path.startswith("/codeload/"):
            sha = path.rsplit("/", 1)[1].removesuffix(".tar.gz")
            return self.reply(200, raw=TARBALLS[sha], headers={"Content-Type": "application/gzip"})
        self.reply(404, {"message": "Not Found"})


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGithub)
threading.Thread(target=server.serve_forever, daemon=True).start()
app.API = f"http://127.0.0.1:{server.server_port}"
for name, sha in SHA.items():
    if name == "evil":
        TARBALLS[sha] = tarball(None, sha, members={"octo-seedboxbuntu-4444444/../../evil.txt": b"x"})
    else:
        TARBALLS[sha] = tarball(None, sha, MUTATIONS[name])

# --- doublures du système -----------------------------------------------------------------------------------------
calls = {"run": [], "restart": 0}
real_run = ctl.run


def fake_run(*command, **kwargs):
    calls["run"].append([str(part) for part in command])

    class Done:
        returncode, stdout, stderr = 0, "", ""
    return Done()


def fake_upgrade_ok(*command, **kwargs):
    calls["run"].append([str(part) for part in command])

    class Done:
        returncode, stdout, stderr = 0, "", ""
    (ctl.STATE / "upgrade-pending").unlink(missing_ok=True)      # comme « seedboxctl upgrade » en cas de réussite
    return Done()


upgrade_result = {"code": 0}


def fake_run_upgrade(*command, **kwargs):
    calls["run"].append([str(part) for part in command])
    code = upgrade_result["code"] if command[:1] == ("UPGRADE",) else 0
    if command[:1] == ("UPGRADE",) and code == 0:
        (ctl.STATE / "upgrade-pending").unlink(missing_ok=True)

    class Done:
        returncode, stdout, stderr = code, "", ""
    return Done()


ctl.run = fake_run_upgrade
app.UPGRADE_COMMAND = ["UPGRADE"]
app.POSTINSTALL = SYSTEM / "absent-postinstall.sh"
app.restart_console = lambda: calls.__setitem__("restart", calls["restart"] + 1)
app.compose_valid = lambda path: True
for path in (ctl.STATE, ctl.ETC, ctl.BASE):
    path.mkdir(parents=True, exist_ok=True)
(ctl.ETC / "github-token").write_text("ghp_" + "t" * 36 + "\n")


def snapshot(base):
    return {str(p.relative_to(base)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777)
            for p in sorted(Path(base).rglob("*")) if p.is_file()}


# --- installation de départ : comme une ISO (version 2026.11.10-0800, commit v0 noté) -------------------------------
tree0 = make_tree(root / "initial")
for system, source, mode in app.plan(tree0):
    app.place(source, app.system_path(system), mode)
iso_version = "2026.11.10-0800"
(SYSTEM / "usr/share/seedboxbuntu/VERSION").write_text(iso_version + "\n")
(SYSTEM / "usr/share/seedboxbuntu/SOURCE_COMMIT").write_text(f"{SHA['v0']} {DATES['v0']} {iso_version}\n")
start = snapshot(SYSTEM)

# --- 1. version installée -------------------------------------------------------------------------------------------
current = app.installed()
check("version installée : commit noté par l'ISO", current["commit"] == SHA["v0"] and current["source"] == "iso", current)
(SYSTEM / "usr/share/seedboxbuntu/SOURCE_COMMIT").write_text(f"{SHA['v0']} {DATES['v0']} 2020.01.01-0000\n")
check("ancienne note d'une autre ISO ignorée", app.installed()["commit"] == "")
(SYSTEM / "usr/share/seedboxbuntu/SOURCE_COMMIT").write_text(f"{SHA['v0']} {DATES['v0']} {iso_version} modifié\n")
check("ISO construite depuis des fichiers modifiés : commit inconnu", app.installed()["commit"] == "")
(SYSTEM / "usr/share/seedboxbuntu/SOURCE_COMMIT").write_text(f"{SHA['v0']} {DATES['v0']} {iso_version}\n")

# --- 2. recherche -----------------------------------------------------------------------------------------------------
result = app.check()
check("recherche : mise à jour disponible", result["available"] and result["remote"]["commit"] == SHA["v1"], result)
check("recherche : nouveautés listées (plus ancienne en premier)", result["changes"] == ["Premier changement", "Second changement"], result["changes"])
check("recherche : jeton envoyé à l'API", any(a == "Bearer ghp_" + "t" * 36 for p, a in requests_seen if p.startswith("/repos/")))
(SYSTEM / "usr/share/seedboxbuntu/SOURCE_COMMIT").unlink()
older = app.check()
check("ISO sans commit noté : version plus ancienne que le dernier commit -> disponible", older["available"], older)
(SYSTEM / "usr/share/seedboxbuntu/VERSION").write_text("2026.11.10-1500\n")
newer = app.check()
check("ISO sans commit noté, plus récente que le dernier commit -> à jour", not newer["available"], newer["reason"])
(SYSTEM / "usr/share/seedboxbuntu/VERSION").write_text(iso_version + "\n")
(SYSTEM / "usr/share/seedboxbuntu/SOURCE_COMMIT").write_text(f"{SHA['v0']} {DATES['v0']} {iso_version}\n")

# --- 3. installation de la version 1 ------------------------------------------------------------------------------------
requests_seen.clear()
ctl.STATE.joinpath("upgrade-pending").unlink(missing_ok=True)
check("apply : installe", app.apply() is True)
status = app.current_status()
check("apply : état « terminé » avec la version", status["state"] == "ok" and "2026.11.10-1020" in status["phase"], status)
check("apply : VERSION déduite de la date du commit", (SYSTEM / "usr/share/seedboxbuntu/VERSION").read_text().strip() == "2026.11.10-1020")
check("apply : fichier modifié installé", (SYSTEM / LIBSYS / "health.py").read_text().endswith("# v1\n"))
check("apply : fichier créé", (SYSTEM / "usr/share/seedboxbuntu/nouveau.txt").read_text() == "fichier ajouté\n")
check("apply : état installé noté", app.installed()["commit"] == SHA["v1"] and app.installed()["source"] == "console", app.installed())
check("apply : stack appliquée avec la nouvelle version, console redémarrée",
      ["UPGRADE"] in calls["run"] and calls["restart"] == 1, calls["run"][-3:])
check("apply : marqueur de mise à jour retiré après réussite", not (ctl.STATE / "upgrade-pending").exists())
download = [(p, a) for p, a in requests_seen if p.startswith("/codeload/")]
check("jeton jamais envoyé à l'adresse de téléchargement", download and all(a is None for _, a in download), download)
check("répertoire de travail nettoyé", not app.WORK.exists())
check("une sauvegarde est conservée", len(list(app.BACKUPS.glob("*.tar.gz"))) == 1)
after = snapshot(SYSTEM)
check("droits des fichiers inchangés par la mise à jour", all(after[k][1] == start[k][1] for k in start if k in after))
check("fichier retiré du dépôt absent de la v1 (rien à retirer la première fois)", (SYSTEM / "usr/share/seedboxbuntu/templates/config/ancien.yml").exists())

# --- 4. déjà à jour ---------------------------------------------------------------------------------------------------------
calls["restart"] = 0
check("apply : déjà à jour", app.apply() is False and app.current_status()["state"] == "ok" and calls["restart"] == 0)

# --- 5. version 2 : fichier retiré du dépôt, retiré du serveur ----------------------------------------------------------
STATE_SERVER["head"] = "v2"
check("apply v2", app.apply() is True)
check("v2 : fichier retiré du dépôt supprimé du serveur", not (SYSTEM / "usr/share/seedboxbuntu/templates/config/ancien.yml").exists())
check("v2 : fichier mis à jour", (SYSTEM / "usr/share/seedboxbuntu/nouveau.txt").read_text() == "fichier modifié\n")
check("deux sauvegardes", len(list(app.BACKUPS.glob("*.tar.gz"))) == 2)

# --- 6. retour arrière (v2 -> v1), puis (v1 -> ISO) -------------------------------------------------------------------------
calls["restart"] = 0
app.rollback()
check("retour arrière : fichier restauré", not (SYSTEM / LIBSYS / "health.py").read_text().endswith("# v2\n")
      and (SYSTEM / "usr/share/seedboxbuntu/templates/config/ancien.yml").exists())
check("retour arrière : VERSION et état de la v1", (SYSTEM / "usr/share/seedboxbuntu/VERSION").read_text().strip() == "2026.11.10-1020"
      and app.installed()["commit"] == SHA["v1"])
check("retour arrière : console redémarrée", calls["restart"] == 1)
app.rollback()
check("retour arrière complet : arbre identique à l'installation de départ", snapshot(SYSTEM) == start,
      [k for k in set(start) | set(snapshot(SYSTEM)) if start.get(k) != snapshot(SYSTEM).get(k)][:5])
check("retour arrière complet : l'ISO redevient la source", app.installed()["source"] == "iso" and not list(app.BACKUPS.glob("*.tar.gz")))
try:
    app.rollback()
    check("retour arrière sans sauvegarde refusé", False)
except ctl.SeedboxError:
    check("retour arrière sans sauvegarde refusé", True)

# --- 7. archives refusées : rien n'est modifié ------------------------------------------------------------------------------
for name, expected in (("bad", "casse|health.py|archive refusée"), ("short", "incomplète"), ("evil", "suspect")):
    STATE_SERVER["head"] = name
    before = snapshot(SYSTEM)
    try:
        app.apply()
        check(f"archive {name} refusée", False)
    except ctl.SeedboxError as error:
        check(f"archive {name} refusée", any(word in str(error) for word in expected.split("|")), error)
    check(f"archive {name} : aucun fichier modifié", snapshot(SYSTEM) == before)
    check(f"archive {name} : état « échec » consigné", app.current_status()["state"] == "error", app.current_status())

# --- 8. échec en cours d'installation : tout est remis ------------------------------------------------------------------------
STATE_SERVER["head"] = "v1"
before = snapshot(SYSTEM)
real_place = app.place
counter = {"n": 0}


def failing_place(source, target, mode):
    counter["n"] += 1
    if counter["n"] == 12:
        raise OSError("disque plein (simulé)")
    return real_place(source, target, mode)


app.place = failing_place
try:
    app.apply()
    check("échec d'écriture signalé", False)
except ctl.SeedboxError as error:
    check("échec d'écriture signalé", "disque plein" in str(error), error)
app.place = real_place
check("échec d'écriture : tout est remis comme avant", snapshot(SYSTEM) == before)
check("échec d'écriture : aucune sauvegarde orpheline", not list(app.BACKUPS.glob("*.tar.gz")))

# --- 9. étape finale en échec : fichiers installés, nouvel essai planifié --------------------------------------------------------
upgrade_result["code"] = 1
calls["run"].clear()
app.apply(force=True)
check("étape finale en échec : état « partial »", app.current_status()["state"] == "partial", app.current_status())
check("étape finale en échec : service de reprise lancé", ["systemctl", "start", "--no-block", "seedbox-upgrade.service"] in calls["run"], calls["run"][-3:])
check("étape finale en échec : marqueur conservé", (ctl.STATE / "upgrade-pending").exists())
upgrade_result["code"] = 0

# --- 10. mêmes droits que la construction de l'ISO (lib/40-appliance.sh) ---------------------------------------------------------
function = subprocess.run(["sed", "-n", "/^appliance_mode() {/,/^}/p", str(SRC / "lib/40-appliance.sh")], capture_output=True, text=True).stdout
mismatch = []
for system, source, mode in app.plan(tree0):
    for executable in (False, True):
        probe = root / "probe"
        probe.write_text("x")
        probe.chmod(0o755 if executable else 0o644)
        shell = subprocess.run(["bash", "-c", function + '\nappliance_mode "$1" "$2"', "x", system, str(probe)], capture_output=True, text=True).stdout.strip()
        if int(shell, 8) != app.mode_for(system, 0o755 if executable else 0o644):
            mismatch.append((system, executable, shell, oct(app.mode_for(system, 0o755 if executable else 0o644))))
check("droits : identiques à ceux de la construction de l'ISO", not mismatch, mismatch[:3])

# --- 11. réglages et dépôt introuvable ---------------------------------------------------------------------------------------------
for repo, ref in (("pas-un-depot", "main"), ("a/b", "../x"), ("a/b", ""), (".a/b", "main"), ("a/b;rm", "main")):
    try:
        app.set_config(repo, ref)
        check(f"source refusée : {repo!r} {ref!r}", False)
    except ctl.SeedboxError:
        check(f"source refusée : {repo!r} {ref!r}", True)
app.set_config("octo/seedboxbuntu", "main")
check("source enregistrée", app.config() == {"repo": "octo/seedboxbuntu", "ref": "main"})
STATE_SERVER["repo_exists"] = False
missing = app.check()
check("dépôt introuvable : message avec la piste du jeton", "jeton" in missing["error"] and not missing["available"], missing["error"])
STATE_SERVER["repo_exists"] = True

# --- 12. lancement par la console et état interrompu ----------------------------------------------------------------------------------
calls["run"].clear()
app.status_write(state="ok")
app.launch("apply", "main", True)
launched = calls["run"][-1]
check("lancement : unité systemd dédiée, détachée de la console", launched[0] == "systemd-run" and "--unit" in launched and "appliance" in launched and "--force" in launched, launched)
check("lancement : journal tronqué à chaque exécution", any(a.startswith("--property=StandardOutput=truncate:") for a in launched))
try:
    app.launch("apply")
    check("deuxième lancement refusé pendant l'exécution", False)
except ctl.SeedboxError:
    check("deuxième lancement refusé pendant l'exécution", True)
app.status_write(state="running", pid=2 ** 22 + 12345, started=time.time() - 500)
check("opération interrompue détectée", app.current_status()["state"] == "error", app.current_status())
try:
    app.launch("apply", "../x")
    check("branche invalide refusée au lancement", False)
except ctl.SeedboxError:
    check("branche invalide refusée au lancement", True)

# --- 13. routes de la console ---------------------------------------------------------------------------------------------------------------
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/console"))
import server  # noqa: E402

ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret"}
console = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=console.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{console.server_port}"


def call(path, body=None, groups="admins", secret="secret"):
    headers = {"X-Seedbox-Proxy-Secret": secret, "Remote-Groups": groups}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


app.status_write(state="ok", pid=None)
code, data = call("/api/appliance")
check("console : état de l'appliance", code == 200 and data["installed"]["version"] and data["config"]["repo"] == "octo/seedboxbuntu", data)
code, data = call("/api/appliance/check", {})
check("console : recherche", code == 200 and data["check"]["remote"]["commit"] == SHA["v1"], data.get("check"))
code, data = call("/api/appliance/source", {"repo": "octo/autre", "ref": "dev"})
check("console : source enregistrée", code == 200 and data["config"] == {"repo": "octo/autre", "ref": "dev"}, data)
code, data = call("/api/appliance/source", {"repo": "mauvais", "ref": "dev"})
check("console : source invalide refusée", code == 400 and "dépôt" in data["error"], data)
app.set_config("octo/seedboxbuntu", "main")
code, data = call("/api/appliance/apply", {"force": True})
check("console : mise à jour lancée", code == 200 and data["status"]["state"] == "running", data.get("status"))
code, data = call("/api/appliance/rollback", {})
check("console : retour arrière refusé pendant une mise à jour", code == 400, data)
check("console : réservé aux administrateurs", call("/api/appliance", groups="users")[0] == 403)
check("console : secret du proxy exigé", call("/api/appliance", secret="autre")[0] == 403)

print("échecs :", bad)
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if bad else 0)
