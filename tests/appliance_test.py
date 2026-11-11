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
  - canaux dev / stable, garde-fou (retour automatique, version écartée), mise à jour planifiée
  - versions signées : manifeste SSH, empreintes des fichiers, rejeu, signature obligatoire
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
       "short": "5" * 39 + "f", "sick": "6" * 39 + "a", "v3": "7" * 39 + "b"}
DATES = {"v0": "2026-11-10T08:00:00Z", "v1": "2026-11-10T10:20:00Z", "v2": "2026-11-11T09:30:00Z", "bad": "2026-11-12T09:00:00Z",
         "evil": "2026-11-12T10:00:00Z", "short": "2026-11-12T11:00:00Z", "sick": "2026-11-13T09:00:00Z", "v3": "2026-11-14T09:00:00Z"}
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


def add_v3(tree):
    add_v2(tree)
    (tree / LIB / "health.py").write_text((tree / LIB / "health.py").read_text() + "# v3\n")


MUTATIONS = {"v0": None, "v1": add_v1, "v2": add_v2, "bad": broken_python, "short": without_required, "evil": None,
             "sick": add_v2, "v3": add_v3}
TARBALLS = {}
requests_seen = []
STATE_SERVER = {"head": "v1", "known": {"v0", "v1", "v2"}, "repo_exists": True, "release": None, "tag_commit": "v2",
                "verified": False}
ASSETS = {}


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
        if path.startswith("/repos/octo/seedboxbuntu/releases/latest"):
            if STATE_SERVER["release"] is None:
                return self.reply(404, {"message": "Not Found"})
            return self.reply(200, STATE_SERVER["release"])
        if path.startswith("/repos/octo/seedboxbuntu/commits/"):
            ref = path.rsplit("/", 1)[1]
            name = STATE_SERVER["tag_commit"] if ref.startswith("v1.") else STATE_SERVER["head"]
            return self.reply(200, {"sha": SHA[name], "commit": {"committer": {"date": DATES[name]}, "message": f"Version {name}\n\ndétails",
                                                                 "verification": {"verified": STATE_SERVER["verified"]}}})
        if path.startswith("/repos/octo/seedboxbuntu/assets/"):
            ident = path.rsplit("/", 1)[1]
            return self.reply(302, headers={"Location": f"http://127.0.0.1:{self.server.server_port}/storage/{ident}"})
        if path.startswith("/storage/"):
            return self.reply(200, raw=ASSETS[path.rsplit("/", 1)[1]], headers={"Content-Type": "application/octet-stream"})
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
for repo, ref in (("pas-un-depot", "main"), ("a/b", "../x"), (".a/b", "main"), ("a/b;rm", "main")):
    try:
        app.set_config(repo, ref)
        check(f"source refusée : {repo!r} {ref!r}", False)
    except ctl.SeedboxError:
        check(f"source refusée : {repo!r} {ref!r}", True)
app.set_config("octo/seedboxbuntu", "main")
check("source enregistrée", app.config() == {"repo": "octo/seedboxbuntu", "ref": "main", "channel": "dev", "guard": True,
                                              "require_signed": False, "auto": {"enabled": False, "days": "daily", "time": "04:30"}}, app.config())
STATE_SERVER["repo_exists"] = False
missing = app.check()
check("dépôt introuvable : message avec la piste du jeton", "jeton" in missing["error"] and not missing["available"], missing["error"])
STATE_SERVER["repo_exists"] = True


# --- 11b. canaux : dev (branche) et stable (dernière version publiée) -------------------------------------------------------------
app.GUARD_WAIT = app.GUARD_STEP = 0
alerts_sent = []
app.alert = lambda title, message, level: alerts_sent.append((title, message, level))
health = {"fail": set()}


def fake_health():
    if (SYSTEM / "usr/share/seedboxbuntu/VERSION").read_text().strip() == "2026.11.13-0900":
        return {"Jellyfin : API": "fail", "Radarr : API": "ok"}
    return {name: "fail" for name in health["fail"]} | {"Jellyfin : API": "ok", "Radarr : API": "ok"}


app.health_snapshot = fake_health
app.set_config("octo/seedboxbuntu", "main", channel="stable")
check("canal stable sans version publiée : message clair", "aucune version stable" in app.check()["error"], app.check()["error"])
STATE_SERVER["release"] = {"tag_name": "v1.0", "body": "Notes de la version 1.0", "assets": []}
stable = app.check()
check("canal stable : suit la version publiée", stable["remote"]["commit"] == SHA["v2"] and stable["remote"]["tag"] == "v1.0"
      and stable["available"] and "Notes" in stable["remote"]["notes"], stable)
STATE_SERVER["head"] = "v3"
check("canal stable : ignore les commits de la branche", app.check()["remote"]["commit"] == SHA["v2"])
try:
    app.set_config("octo/seedboxbuntu", "main", channel="beta")
    check("canal inconnu refusé", False)
except ctl.SeedboxError:
    check("canal inconnu refusé", True)
check("apply stable : installe la version publiée (v2)", app.apply() is True and app.installed()["commit"] == SHA["v2"]
      and app.installed()["tag"] == "v1.0", app.installed())
app.set_config("octo/seedboxbuntu", "main", channel="dev")
STATE_SERVER["head"] = "v2"

# --- 11c. garde-fou ------------------------------------------------------------------------------------------------------------------
app.health_snapshot = lambda: {"A": "ok", "B": "fail"}
check("garde-fou : un échec déjà présent avant n'est pas imputé à la mise à jour", app.guard_wait({"A": "ok", "B": "fail"}) == (True, []))
app.health_snapshot = lambda: {"A": "fail", "B": "fail"}
check("garde-fou : un nouvel échec est détecté", app.guard_wait({"A": "ok", "B": "fail"}) == (False, ["A"]))
app.health_snapshot = lambda: None
check("garde-fou : contrôle indisponible -> pas de retour automatique", app.guard_wait({"A": "ok"})[0] is True)
app.health_snapshot = fake_health

STATE_SERVER["head"] = "sick"
TARBALLS[SHA["sick"]] = tarball(None, SHA["sick"], add_v2)
before_sick = snapshot(SYSTEM)
installed_before = app.installed()
calls["restart"] = 0
alerts_sent.clear()
try:
    app.apply()
    check("garde-fou : mise à jour dégradée refusée", False)
except ctl.SeedboxError as error:
    check("garde-fou : mise à jour dégradée annulée", "annulée automatiquement" in str(error) and "Jellyfin" in str(error), error)
check("garde-fou : arbre identique à avant la mise à jour", snapshot(SYSTEM) == before_sick,
      [k for k in set(before_sick) | set(snapshot(SYSTEM)) if before_sick.get(k) != snapshot(SYSTEM).get(k)][:5])
check("garde-fou : version installée inchangée", app.installed()["commit"] == installed_before["commit"])
status = app.current_status()
check("garde-fou : état « rolled_back »", status["state"] == "rolled_back" and "Jellyfin" in status["error"], status)
check("garde-fou : version fautive mise de côté", app.read_json(app.BLOCKED_FILE).get("commit") == SHA["sick"])
check("garde-fou : alerte critique envoyée", alerts_sent and alerts_sent[0][2] == "critical", alerts_sent)
check("garde-fou : console redémarrée, stack réappliquée", calls["restart"] == 1 and calls["run"][-1] == ["UPGRADE"], calls["run"][-2:])
check("garde-fou : la recherche signale la version écartée", app.check()["blocked"] is True)

# mise à jour planifiée : désactivée, puis activée, version écartée non réessayée, nouvelle version installée
check("planifiée : désactivée par défaut", app.auto() is False)
app.set_config("octo/seedboxbuntu", "main", auto_settings={"enabled": "true", "days": "mon,thu", "time": "3:15"})
dropin = SYSTEM / "etc/systemd/system/seedbox-appliance.timer.d/seedboxbuntu.conf"
check("planifiée : jours et heure écrits dans le minuteur", dropin.exists() and "OnCalendar=Mon,Thu *-*-* 03:15:00" in dropin.read_text(),
      dropin.read_text() if dropin.exists() else "absent")
requests_seen.clear()
check("planifiée : version écartée non réessayée", app.auto() is False and not any(p.startswith("/repos/octo/seedboxbuntu/tarball/") for p, _ in requests_seen))
for bad_value in ({"time": "25:00"}, {"days": "lundi"}):
    try:
        app.set_config("octo/seedboxbuntu", "main", auto_settings=bad_value)
        check(f"planifiée : réglage invalide refusé {bad_value}", False)
    except ctl.SeedboxError:
        check(f"planifiée : réglage invalide refusé {bad_value}", True)
STATE_SERVER["head"] = "v3"
alerts_sent.clear()
check("planifiée : installe la nouvelle version", app.auto() is True and app.installed()["commit"] == SHA["v3"], app.installed())
check("planifiée : alerte d'information", alerts_sent and alerts_sent[-1][2] == "ok", alerts_sent)
check("planifiée : version écartée oubliée après une installation réussie", not app.BLOCKED_FILE.exists())
check("planifiée : déjà à jour -> rien", app.auto() is False)
# garde-fou désactivé : la même version dégradée reste installée
app.set_config("octo/seedboxbuntu", "main", guard="false", auto_settings={"enabled": "false"})
STATE_SERVER["head"] = "sick"
check("garde-fou désactivé : version installée telle quelle", app.apply(force=True) is True and app.installed()["commit"] == SHA["sick"])
app.rollback()
app.set_config("octo/seedboxbuntu", "main", guard="true")
check("garde-fou réactivé", app.config()["guard"] is True)

# --- 11d. versions signées ------------------------------------------------------------------------------------------------------------------
keys = root / "keys"
keys.mkdir()
for name in ("good", "other"):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(keys / name), "-C", name], check=True)
signed_dir = make_tree(root / "tree-signed")
add_v2(signed_dir)


def publish(manifest_obj, key="good", with_sig=True, tag="v1.0"):
    data = json.dumps(manifest_obj, sort_keys=True).encode()
    (keys / "manifest.json").write_bytes(data)
    (keys / "manifest.json.sig").unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(keys / key), "-n", "seedboxbuntu-release", str(keys / "manifest.json")], check=True)
    ASSETS["1"], ASSETS["2"] = data, (keys / "manifest.json.sig").read_bytes()
    assets = [{"name": "manifest.json", "url": f"{app.API}/repos/octo/seedboxbuntu/assets/1"}]
    if with_sig:
        assets.append({"name": "manifest.json.sig", "url": f"{app.API}/repos/octo/seedboxbuntu/assets/2"})
    STATE_SERVER["release"] = {"tag_name": tag, "body": "", "assets": assets}


good_manifest = {"version": 1, "tag": "v1.0", "commit": SHA["v2"], "files": app.tree_hashes(signed_dir)}
helper = subprocess.run([sys.executable, str(SRC / "tools/manifest.py"), "build", "--tag", "v1.0", "--commit", SHA["v2"], "--root", str(signed_dir)],
                        capture_output=True, text=True)
check("tools/manifest.py : mêmes empreintes que le serveur", json.loads(helper.stdout) == good_manifest, helper.stderr[:200])

STATE_SERVER["head"], STATE_SERVER["tag_commit"] = "v2", "v2"
app.set_config("octo/seedboxbuntu", "main", channel="stable", require_signed="false")
publish(good_manifest)
check("signature : sans clé de confiance, le manifeste est ignoré (facultatif)", app.verify_release(app.config(), app.remote_head(app.config()), signed_dir)["state"] == "none")
try:
    app.set_signers("ceci n'est pas une clé")
    check("clé de confiance invalide refusée", False)
except ctl.SeedboxError:
    check("clé de confiance invalide refusée", True)
fingerprints = app.set_signers((keys / "good.pub").read_text())
check("clé de confiance enregistrée (empreinte)", fingerprints and fingerprints[0].startswith("SHA256:") and "namespaces" in app.SIGNERS_FILE.read_text(), fingerprints)
cfg_now = app.config()
verdict = app.verify_release(cfg_now, app.remote_head(cfg_now), signed_dir)
check("signature : manifeste signé valide accepté", verdict["state"] == "signed", verdict)


def refused(label, word, tree=signed_dir):
    try:
        app.verify_release(app.config(), app.remote_head(app.config()), tree)
        check(label, False)
    except ctl.SeedboxError as error:
        check(label, word in str(error), error)


publish(good_manifest, key="other")
refused("signature : clé inconnue refusée", "signature du manifeste invalide")
publish(good_manifest, with_sig=False)
refused("signature : manifeste sans signature refusé", "sans signature")
publish({**good_manifest, "commit": SHA["v1"]})
refused("signature : manifeste d'un autre commit (rejeu) refusé", "ne correspond pas")
publish({**good_manifest, "tag": "v0.9"})
refused("signature : manifeste d'une autre version refusé", "ne correspond pas")
tampered = {**good_manifest, "files": {**good_manifest["files"], f"{LIB}/health.py": "0" * 64}}
publish(tampered)
refused("signature : empreinte différente refusée", "différents du manifeste")
publish(good_manifest)
altered = make_tree(root / "tree-altered")
add_v2(altered)
(altered / LIB / "health.py").write_text((altered / LIB / "health.py").read_text() + "# porte dérobée\n")
refused("signature : fichier modifié dans l'archive refusé", "différents du manifeste", altered)
extra = make_tree(root / "tree-extra")
add_v2(extra)
(extra / "rootfs/usr/share/seedboxbuntu/intrus.txt").write_text("x")
refused("signature : fichier ajouté dans l'archive refusé", "différents du manifeste", extra)

# signature obligatoire
app.set_signers("")
check("clés de confiance retirées", app.signer_fingerprints() == [] and not app.SIGNERS_FILE.exists())
app.set_config("octo/seedboxbuntu", "main", require_signed="true")
STATE_SERVER["verified"] = False
refused("obligatoire : manifeste sans clé de confiance et commit non vérifié refusé", "signature obligatoire")
STATE_SERVER["verified"] = True
check("obligatoire : commit vérifié par GitHub accepté", app.verify_release(app.config(), app.remote_head(app.config()), signed_dir)["state"] == "commit")
STATE_SERVER["verified"] = False
app.set_signers((keys / "good.pub").read_text())
check("obligatoire : manifeste signé accepté", app.verify_release(app.config(), app.remote_head(app.config()), signed_dir)["state"] == "signed")
# installation complète signée, avec le contenu noté
check("apply signé : installe et note la vérification", app.apply(force=True) is True and app.installed()["trust"] == "signed", app.installed())
check("apply signé : état de la mise à jour", app.current_status().get("trust") == "signed", app.current_status())
# une archive modifiée (le serveur sert autre chose que ce qui est signé) ne s'installe pas
TARBALLS[SHA["v2"]] = tarball(None, SHA["v2"], lambda t: (add_v2(t), (t / LIB / "health.py").write_text((t / LIB / "health.py").read_text() + "# piégé\n")))
before_trap = snapshot(SYSTEM)
try:
    app.apply(force=True)
    check("apply signé : archive piégée refusée", False)
except ctl.SeedboxError as error:
    check("apply signé : archive piégée refusée", "différents du manifeste" in str(error), error)
check("apply signé : archive piégée, aucun fichier modifié", snapshot(SYSTEM) == before_trap)
app.set_config("octo/seedboxbuntu", "main", require_signed="false", channel="dev")
app.set_signers("")
STATE_SERVER["release"] = None
STATE_SERVER["head"] = "v1"
TARBALLS[SHA["v2"]] = tarball(None, SHA["v2"], add_v2)

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
check("console : source enregistrée", code == 200 and data["config"]["repo"] == "octo/autre" and data["config"]["ref"] == "dev", data)
code, data = call("/api/appliance/source", {"repo": "octo/autre", "ref": "dev", "channel": "stable", "guard": False,
                                            "require_signed": True, "auto": {"enabled": True, "days": "sun", "time": "05:00"}})
check("console : canal, garde-fou, signature et planification enregistrés", code == 200 and data["config"]["channel"] == "stable"
      and data["config"]["guard"] is False and data["config"]["require_signed"] is True and data["config"]["auto"]["time"] == "05:00", data.get("config"))
code, data = call("/api/appliance/signer", {"key": (keys / "good.pub").read_text()})
check("console : clé de confiance enregistrée", code == 200 and data["signers"] and data["signers"][0].startswith("SHA256:"), data.get("signers"))
code, data = call("/api/appliance/signer", {"key": ""})
check("console : clé de confiance retirée", code == 200 and data["signers"] == [], data.get("signers"))
code, data = call("/api/appliance/source", {"repo": "mauvais", "ref": "dev"})
check("console : source invalide refusée", code == 400 and "dépôt" in data["error"], data)
app.set_config("octo/seedboxbuntu", "main", channel="dev", guard="true", require_signed="false", auto_settings={"enabled": "false"})
code, data = call("/api/appliance/apply", {"force": True})
check("console : mise à jour lancée", code == 200 and data["status"]["state"] == "running", data.get("status"))
code, data = call("/api/appliance/rollback", {})
check("console : retour arrière refusé pendant une mise à jour", code == 400, data)
check("console : réservé aux administrateurs", call("/api/appliance", groups="users")[0] == 403)
check("console : secret du proxy exigé", call("/api/appliance", secret="autre")[0] == 403)

print("échecs :", bad)
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if bad else 0)
