"""Sauvegarde hors serveur (rclone) et test de restauration, avec un faux rclone local : aucun réseau, aucun root."""
import http.server
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sboff"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
os.environ["SB_ROOT"] = str(root / "system")

# --- faux rclone : « offsite: » est un dossier local ------------------------------------------------------------------------
remote = root / "remote"
remote.mkdir()
fake = root / "bin" / "rclone"
fake.parent.mkdir()
fake.write_text(f'''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
REMOTE = Path({str(remote)!r})
args = sys.argv[1:]
if os.environ.get("FAKE_FAIL"):
    print("2026/11/11 ERROR : connexion refusée (simulée)", file=sys.stderr); sys.exit(1)
if args[:1] == ["--config"]:
    conf = args[1]; args = args[2:]
    if not Path(conf).exists(): print("config introuvable", file=sys.stderr); sys.exit(1)
def local(path):
    return REMOTE / path.split(":", 1)[1] if path.startswith("offsite:") else Path(path)
cmd = args[0]
if cmd == "obscure":
    print("OBS(" + args[1] + ")")
elif cmd == "copyto":
    src, dst = local(args[1]), local(args[2])
    dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst)
elif cmd == "deletefile":
    local(args[1]).unlink(missing_ok=True)
elif cmd == "lsf":
    folder = local(args[-1])
    for f in sorted(folder.glob("*")) if folder.exists() else []:
        if f.is_file(): print(f"{{f.stat().st_size}}|{{f.name}}")
else:
    print("commande inconnue", cmd, file=sys.stderr); sys.exit(2)
''')
fake.chmod(0o755)
os.environ["SB_RCLONE"] = str(fake)

sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import backup as bk  # noqa: E402
import offsite as off  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


alerts = []
ctl.alerts = lambda: type("A", (), {"notify": staticmethod(lambda t, m, l="warning", force=False: alerts.append((t, m, l)) or {})})()
B, E, S = ctl.BASE, ctl.ETC, ctl.STATE
for path in (B, E, S):
    path.mkdir(parents=True, exist_ok=True)
(B / ".env").write_text("SECRET=abc\n")
database = B / "config/radarr/radarr.db"
database.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(database) as db:
    db.execute("create table t (a text)")
    db.execute("insert into t values ('x')")
(B / "config/sonarr").mkdir(parents=True, exist_ok=True)
(B / "config/sonarr/config.xml").write_text("<a/>")


def make_backup():
    time.sleep(1.05)                                          # noms à la seconde près
    return bk.create()


# --- 1. réglages -------------------------------------------------------------------------------------------------------------------------
check("rclone détecté", off.installed())
for label, data in (("type inconnu", {"type": "ftp"}), ("port invalide", {"type": "sftp", "fields": {"host": "nas", "port": "99999"}}),
                    ("dossier distant dangereux", {"type": "sftp", "path": "../etc"}),
                    ("adresse WebDAV non https", {"type": "webdav", "fields": {"url": "ftp://x"}}),
                    ("hôte avec retour à la ligne", {"type": "sftp", "fields": {"host": "nas\nfoo"}})):
    try:
        off.validate(data)
        check(f"refusé : {label}", False)
    except ctl.SeedboxError:
        check(f"refusé : {label}", True)
try:
    off.save({"type": "sftp", "enabled": True, "fields": {"host": "nas.local"}, "secrets": {"password": "a\nb"}})
    check("secret sur plusieurs lignes refusé", False)
except ctl.SeedboxError:
    check("secret sur plusieurs lignes refusé", True)
try:
    off.save({"enabled": True})
    check("activation sans destination refusée", False)
except ctl.SeedboxError:
    check("activation sans destination refusée", True)

cfg = off.save({"type": "sftp", "enabled": True, "path": "seedbox/nas", "keep": 2,
                "fields": {"host": "nas.local", "port": "2222", "user": "backup"}, "secrets": {"password": "S3cret-Pass"}})
conf = off.CONF_FILE.read_text()
check("rclone.conf : droits 0600", stat.S_IMODE(off.CONF_FILE.stat().st_mode) == 0o600)
check("rclone.conf : destination SFTP, mot de passe masqué par rclone", "[offsite]" in conf and "host = nas.local" in conf
      and "pass = OBS(S3cret-Pass)" in conf and "port = 2222" in conf, conf)
check("offsite.json : aucun secret", "S3cret" not in off.SETTINGS_FILE.read_text() and stat.S_IMODE(off.SETTINGS_FILE.stat().st_mode) == 0o600)
overview = off.overview()
check("vue d'ensemble : aucun secret, présence indiquée", "S3cret" not in json.dumps(overview) and overview["secrets_present"].get("password") is True
      and overview["configured"], overview["secrets_present"])
off.save({"type": "sftp", "enabled": True, "path": "seedbox/nas", "keep": 2, "fields": {"host": "nas2.local", "port": "2222", "user": "backup"}})
conf = off.CONF_FILE.read_text()
check("champ public modifié sans ressaisir le secret : secret conservé", "host = nas2.local" in conf and "pass = OBS(S3cret-Pass)" in conf, conf)

# --- 2. connexion, envoi automatique, rétention -----------------------------------------------------------------------------------------------
check("essai de connexion", off.connection()["ok"] is True)
names = [make_backup() for _ in range(3)]
remote_dir = remote / "seedbox/nas"
present = sorted(p.name for p in remote_dir.glob("seedbox-*"))
check("envoi automatique après chaque sauvegarde, rétention distante (2)", present == names[1:], present)
status = off.read_json(off.STATUS_FILE)
check("état du dernier envoi", status["last_push"]["ok"] and status["last_push"]["name"] == names[-1], status.get("last_push"))
check("copie distante identique à la locale", (remote_dir / names[-1]).read_bytes() == bk.find(names[-1]).read_bytes())

# --- 3. destination en panne : la sauvegarde locale réussit, une alerte part ----------------------------------------------------------------------
os.environ["FAKE_FAIL"] = "1"
alerts.clear()
name = make_backup()
check("destination en panne : sauvegarde locale réussie", bk.find(name).exists())
check("destination en panne : alerte critique", alerts and alerts[0][2] == "critical" and "hors serveur" in alerts[0][0], alerts)
check("destination en panne : état consigné", off.read_json(off.STATUS_FILE)["last_push"]["ok"] is False)
try:
    off.connection()
    check("essai de connexion en échec signalé", False)
except ctl.SeedboxError as error:
    check("essai de connexion en échec signalé", "connexion refusée" in str(error), error)
del os.environ["FAKE_FAIL"]
check("envoi manuel de la dernière sauvegarde", off.push_latest() == name and (remote_dir / name).exists())

# --- 4. test de restauration ----------------------------------------------------------------------------------------------------------------------------
result = off.restore_test()
check("test de restauration (copie distante) réussi", result["ok"] and result["where"] == "distante" and result["databases"] == 1 and result["files"] >= 3, result)
check("test de restauration : état consigné", off.read_json(off.STATUS_FILE)["last_test"]["ok"] is True)
check("test de restauration : configuration en service intacte", (B / ".env").read_text() == "SECRET=abc\n")
check("test de restauration : dossier temporaire supprimé", not list(S.glob("tmp*")))
result = off.restore_test("local")
check("test de restauration de la copie locale", result["ok"] and result["where"] == "locale", result)
# base endommagée
database.write_bytes(b"SQLite format 3\x00" + b"\xff" * 2000)
broken = make_backup()
alerts.clear()
try:
    off.restore_test()
    check("test de restauration : base endommagée détectée", False)
except ctl.SeedboxError as error:
    check("test de restauration : base endommagée détectée", "endommagée" in str(error) and "radarr.db" in str(error), error)
check("test de restauration : alerte critique", alerts and "restauration" in alerts[0][0] and alerts[0][2] == "critical", alerts)
check("test de restauration : échec consigné", off.read_json(off.STATUS_FILE)["last_test"]["ok"] is False)
# mauvaise clé
with sqlite3.connect(database := B / "config/radarr/radarr.db") as db:
    pass
database.unlink()
with sqlite3.connect(database) as db:
    db.execute("create table t (a text)")
good_key = bk.KEY_FILE.read_text()
bk.KEY_FILE.write_text("autre-cle\n")
try:
    off.restore_test("local")
    check("test de restauration : mauvaise clé détectée", False)
except ctl.SeedboxError as error:
    check("test de restauration : mauvaise clé détectée", "déchiffrement" in str(error), error)
bk.KEY_FILE.write_text(good_key)
make_backup()                                              # sauvegarde saine : la base a été recréée

# --- 5. configuration rclone avancée, retrait ----------------------------------------------------------------------------------------------------------------
off.save({"type": "raw", "enabled": False, "path": "x", "secrets": {"config": "[mon-nas]\ntype = sftp\nhost = 10.0.0.2\nuser = u\n"}})
check("configuration avancée : première section renommée « offsite »", off.CONF_FILE.read_text().startswith("[offsite]") and "host = 10.0.0.2" in off.CONF_FILE.read_text())
try:
    off.save({"type": "raw", "secrets": {"config": "pas de section"}})
    check("configuration avancée sans section refusée", False)
except ctl.SeedboxError:
    check("configuration avancée sans section refusée", True)
off.save({"type": "s3", "enabled": True, "path": "sb", "fields": {"provider": "Wasabi", "endpoint": "https://s3.wasabisys.com", "bucket": "mon-seau", "access_key": "AKIA1"},
          "secrets": {"secret_key": "SK1"}})
check("S3 : destination et seau", "provider = Wasabi" in off.CONF_FILE.read_text() and off.remote_path() == "offsite:mon-seau/sb", off.remote_path())
off.clear()
check("retrait : plus de configuration", not off.CONF_FILE.exists() and not off.SETTINGS_FILE.exists() and not off.configured())
check("sans destination, la sauvegarde ne cherche pas à envoyer", off.push("seedbox-20260101-000000.tar.gz.enc") is None)
result = off.restore_test()
check("sans destination, le test porte sur la copie locale", result["ok"] and result["where"] == "locale")

# --- 6. routes de la console -------------------------------------------------------------------------------------------------------------------------------------
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/console"))
import server  # noqa: E402

ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret"}
started = []
server.start_job = lambda title, argv, stdin=None: started.append((title, argv, stdin)) or "0123456789ab"
console = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=console.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{console.server_port}"


def call(path, body=None, groups="admins"):
    headers = {"X-Seedbox-Proxy-Secret": "secret", "Remote-Groups": groups}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


code, data = call("/api/offsite")
check("console : vue d'ensemble", code == 200 and "types" in data and "sftp" in data["types"], list(data)[:4])
code, data = call("/api/offsite/settings", {"type": "ftp"})
check("console : réglage invalide refusé avant la tâche", code == 400 and not started, data)
code, data = call("/api/offsite/settings", {"type": "sftp", "fields": {"host": "nas"}, "secrets": {"password": "pw"}})
check("console : réglage transmis à la tâche par l'entrée standard", code == 200 and started[-1][1] == ["offsite", "set"] and "pw" in started[-1][2]
      and "pw" not in " ".join(started[-1][1]), started[-1][:2])
for action in ("connection", "push", "restore-test", "clear"):
    code, data = call(f"/api/offsite/{action}", {})
    check(f"console : {action}", code == 200 and started[-1][1][:2] == ["offsite", action], started[-1][:2])
check("console : réservé aux administrateurs", call("/api/offsite", groups="users")[0] == 403)

print("échecs :", bad)
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if bad else 0)
