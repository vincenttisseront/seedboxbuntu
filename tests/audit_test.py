"""Audit : journal chaîné, masquage des secrets, collecte Authelia/SSH, recherche dans les journaux (sans Docker)."""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbaud"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import audit as au  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


ctl.STATE.mkdir(parents=True, exist_ok=True)

# --- enregistrement et recherche -------------------------------------------------------------------------------
first = au.record("admin", "10.0.0.2", "POST /api/users", "login=marie, role=user")
au.record("admin", "10.0.0.2", "Sauvegarde", "", "console", "ok")
au.record("marie", "1.2.3.4", "connexion (mot de passe)", "", "authelia", "refusé")
check("événements enregistrés", au.search()["total"] == 3 and first is not None)
check("recherche libre", au.search(q="marie")["total"] == 2)
check("filtre source", au.search(source="authelia")["total"] == 1)
check("filtre résultat", au.search(outcome="refusé")["total"] == 1)
check("filtre utilisateur", au.search(user="admin")["total"] == 2)
check("liste des utilisateurs", au.search()["users"] == ["admin", "marie"])
check("pagination", len(au.search(limit=2)["events"]) == 2 and len(au.search(limit=2, offset=2)["events"]) == 1)
check("plus récent d'abord", au.search()["events"][0]["id"] > au.search()["events"][-1]["id"])
check("chaîne intacte", au.verify()["ok"] and au.verify()["events"] == 3)
check("base en 0600", oct(au.DB_FILE.stat().st_mode & 0o777) == "0o600")

# --- masquage des secrets ----------------------------------------------------------------------------------------
au.record("admin", "10.0.0.2", "essai", "password=Hunter2xx token: abcd1234 https://x/y?apikey=ZZZ999&a=1 Authorization: Bearer SECRET42")
au.record("admin", "10.0.0.2", "github", "ghp_abcdefghijklmnop1234")
au.record("admin", "10.0.0.2", "clé", "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----")
text = json.dumps(au.search(limit=50)["events"])
for secret in ("Hunter2xx", "abcd1234", "ZZZ999", "SECRET42", "abcdefghijklmnop1234", "AAAA"):
    check(f"secret masqué ({secret[:6]})", secret not in text)
check("contexte conservé", "apikey=***" in text and "a=1" in text)

# --- détail d'une requête : seules les clés sûres ------------------------------------------------------------------
detail = au.safe_detail({"login": "marie", "password": "pw-secret", "role": "user", "ids": [1, 2, 3], "token": "t", "label": "x" * 200})
check("détail sans secret", detail == "login=marie, role=user, ids=3 élément(s)", detail)
au.request_event("admin", "10.0.0.2", "/api/users", {"login": "paul", "password": "pw-secret"}, 200, "Création du compte paul")
au.request_event("", "10.0.0.2", "/api/x", {}, 400)
au.request_event("admin", "10.0.0.2", "/api/x", {}, 500)
last = au.search(limit=3)["events"]
check("tâche : titre et résultat", any(e["action"] == "Création du compte paul" and e["outcome"] == "ok" for e in last))
check("refus : résultat", any(e["outcome"] == "refusé" and e["user"] == "inconnu" for e in last))
check("erreur : résultat", any(e["outcome"] == "erreur" for e in last))
check("mot de passe absent du journal", "pw-secret" not in json.dumps(au.search(limit=100)["events"]))

# --- altération détectée -------------------------------------------------------------------------------------------
check("chaîne intacte (2)", au.verify()["ok"])
db = sqlite3.connect(au.DB_FILE)
db.execute("UPDATE events SET user = 'autre' WHERE id = 2")
db.commit()
status = au.verify()
check("modification détectée", not status["ok"] and status["first_bad"] == 2, str(status))
db.execute("UPDATE events SET user = 'admin' WHERE id = 2")
db.commit()
check("restauration de la valeur : chaîne intacte", au.verify()["ok"])
db.execute("DELETE FROM events WHERE id = 3")
db.commit()
check("suppression au milieu détectée", not au.verify()["ok"])
db.close()

# --- une base saine pour la suite -------------------------------------------------------------------------------------
au.DB_FILE.unlink()
au.record("a", "", "un")
au.record("b", "", "deux")
db = sqlite3.connect(au.DB_FILE)
last_id = db.execute("SELECT MAX(id) FROM events").fetchone()[0]
db.execute("DELETE FROM events WHERE id = ?", (last_id,))
db.commit()
db.close()
check("retrait de la fin détecté", not au.verify()["ok"] and au.verify()["first_bad"] == -1)
au.DB_FILE.unlink()

# --- export ----------------------------------------------------------------------------------------------------------------
au.record("admin", "10.0.0.2", "Action, avec virgule", "a;b", "console")
csv_text, kind = au.export("csv")
check("export csv", csv_text.splitlines()[0].startswith("id,date,source") and '"Action, avec virgule"' in csv_text and kind.startswith("text/csv"))
json_text, kind = au.export("json")
check("export json", json.loads(json_text)[0]["action"] == "Action, avec virgule" and kind == "application/json")

# --- conservation et chaîne après élagage ---------------------------------------------------------------------------------
au.DB_FILE.unlink()
old = int(time.time()) - 500 * 86400
for n in range(4):
    au.record("a", "", f"ancien {n}", ts=old)
for n in range(3):
    au.record("a", "", f"récent {n}")
check("avant élagage", au.search()["total"] == 7)
removed = au.prune()
check("anciens événements supprimés", removed == 4 and au.search()["total"] == 3, str(removed))
check("chaîne valable après élagage", au.verify()["ok"], str(au.verify()))
au.record("a", "", "après")
check("on peut continuer après élagage", au.verify()["ok"] and au.search()["total"] == 4)
check("rien à élaguer", au.prune() == 0)

# --- collecte Authelia et SSH ------------------------------------------------------------------------------------------------------
now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
authelia_lines = "\n".join([
    f'time="{now}" level=info msg="Successful 1FA authentication attempt made by user \'marie\'" method=POST path=/api/firstfactor remote_ip=203.0.113.5',
    f'time="{now}" level=error msg="Unsuccessful 1FA authentication attempt by user \'paul\'" remote_ip=198.51.100.7',
    f'time="{now}" level=info msg="Successful TOTP authentication attempt made by user \'marie\'" remote_ip=203.0.113.5',
    f'time="{now}" level=debug msg="Autre chose" remote_ip=1.1.1.1',
    "ligne illisible",
])
ssh_lines = "\n".join([
    f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}+00:00 serveur sshd[123]: Accepted publickey for vincent from 192.0.2.9 port 5555 ssh2",
    f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}+00:00 serveur sshd[124]: Failed password for root from 192.0.2.66 port 4444 ssh2",
    f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}+00:00 serveur sshd[125]: Invalid user test from 192.0.2.67 port 3333",
    f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}+00:00 serveur sshd[126]: Connection closed by 192.0.2.9",
])
journal_lines = {"authelia": authelia_lines, "ssh": ssh_lines}
au.DB_FILE.unlink()


def fake_command(*argv, timeout=40):
    if argv[0] == "docker" and argv[1] == "logs":
        return 0, journal_lines["authelia"] if argv[-1] == "authelia" else "ligne 1\nligne 2"
    if argv[0] == "journalctl":
        return 0, journal_lines["ssh"]
    return 127, ""


au.command = fake_command
added = au.sync()
events = au.search(limit=50)["events"]
check("collecte : 6 événements", added == 6 and len(events) == 6, f"{added}")
check("Authelia : succès", any(e["user"] == "marie" and e["ip"] == "203.0.113.5" and e["action"] == "connexion (mot de passe)" and e["outcome"] == "ok" for e in events))
check("Authelia : échec", any(e["user"] == "paul" and e["outcome"] == "refusé" and e["source"] == "authelia" for e in events))
check("Authelia : double authentification", any(e["action"] == "connexion (double authentification)" for e in events))
check("SSH : accepté", any(e["source"] == "ssh" and e["user"] == "vincent" and e["outcome"] == "ok" for e in events))
check("SSH : refusé", any(e["user"] == "root" and e["ip"] == "192.0.2.66" and e["outcome"] == "refusé" for e in events))
check("SSH : compte inexistant", any(e["user"] == "test" and "inexistant" in e["action"] for e in events))
again = au.sync()
check("pas de doublon à la passe suivante", again == 0 and au.search()["total"] == 6, str(again))
check("curseur enregistré", set(json.loads(au.CURSOR_FILE.read_text())) == {"authelia", "ssh"})
journal_lines["ssh"] += f"\n{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}+00:00 serveur sshd[130]: Accepted password for nouveau from 192.0.2.10 port 1 ssh2"
check("nouvelle ligne ingérée", au.sync() == 1)
check("chaîne intacte après collecte", au.verify()["ok"])
check("ligne parsée : vide ignorée", au.authelia_event("") is None and au.ssh_event("rien") is None)
check("maintain", au.maintain() == 0)

# --- recherche dans les journaux ----------------------------------------------------------------------------------------------------
class FakeSys:
    @staticmethod
    def log_sources():
        return {"containers": ["radarr", "authelia"], "host": ["ssh"]}


ctl.sysinfo = lambda: FakeSys
stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60))
stamp2 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 30))


def fake_command2(*argv, timeout=40):
    if argv[0] == "docker":
        name = argv[-1]
        if name == "radarr":
            return 0, f"{stamp}.000000000Z erreur apikey=SUPERSECRET sur le film\n{stamp2}.000000000Z tout va bien"
        return 0, f"{stamp}.123Z connexion refusée pour paul"
    return 0, f"{stamp2}+00:00 serveur sshd[1]: Failed password for root from 1.1.1.1"


au.command = fake_command2
result = au.search_logs()
check("tous les journaux", result["sources"] == 3 and result["total"] == 4, str(result["total"]))
check("plus récent d'abord", result["lines"][0]["ts"] >= result["lines"][-1]["ts"])
check("secret masqué dans les journaux", "SUPERSECRET" not in json.dumps(result) and "apikey=***" in json.dumps(result))
check("recherche texte", au.search_logs("refusée")["total"] == 1)
check("recherche sans casse", au.search_logs("TOUT VA BIEN")["total"] == 1)
check("recherche regex", au.search_logs(r"erreur|refus", regex=True)["total"] == 2)
try:
    au.search_logs("(", regex=True)
    check("regex invalide refusée", False)
except ctl.SeedboxError:
    check("regex invalide refusée", True)
try:
    au.search_logs("x" * 300, regex=True)
    check("regex trop longue refusée", False)
except ctl.SeedboxError:
    check("regex trop longue refusée", True)
check("source choisie", au.search_logs(sources=["c:radarr"])["sources"] == 1)
try:
    au.search_logs(sources=["c:inconnu"])
    check("source inconnue refusée", False)
except ctl.SeedboxError:
    check("source inconnue refusée", True)
try:
    au.search_logs(since="3ans")
    check("période inconnue refusée", False)
except ctl.SeedboxError:
    check("période inconnue refusée", True)
check("limite", len(au.search_logs(limit=2)["lines"]) == 2 and au.search_logs(limit=2)["truncated"])
exported = au.export_logs(query="erreur", since="1h")
check("export texte", "[c:radarr]" in exported and "apikey=***" in exported and exported.endswith("\n"))
check("overview", au.overview()["chain"]["ok"] and au.overview()["keep_days"] == 400)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
