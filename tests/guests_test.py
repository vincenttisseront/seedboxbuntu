"""Invités : invitations, limites, comptes temporaires, route publique de la console (sans Docker)."""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbgst"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import guests as gs  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


ctl.STATE.mkdir(parents=True, exist_ok=True)
# --- faux annuaire et faux services ---------------------------------------------------------------------------------------------------
accounts = {"admin": "admin", "marie": "user", "boss": "manager"}
jelly = {}
seerr = {}
alerts = []
created_calls = []


def fake_user_add(login, password, role="user"):
    ctl.validate_account(login, password)
    if login in accounts:
        raise ctl.SeedboxError(f"le compte {login} existe déjà")
    if login == "casse":
        raise ctl.SeedboxError("Jellyfin : création refusée (HTTP 500)")
    accounts[login] = role
    jelly[login] = {"Id": "jf-" + login, "Name": login, "Policy": {"IsAdministrator": False, "MaxActiveSessions": 0}}
    created_calls.append((login, password, role))
    return ["Seerr : import refusé (HTTP 500)"] if login == "avertis" else []


ctl.user_add = fake_user_add
ctl.users_load = lambda: {k: {"groups": []} for k in accounts}
ctl.user_list = lambda: [{"login": k, "role": v} for k, v in sorted(accounts.items())]
ctl.user_del = lambda login: (accounts.pop(login), jelly.pop(login, None), [])
ctl.jellyfin_user = lambda login: jelly.get(login)


def fake_jelly_api(method, path, body=None, token=None):
    login = next(k for k, v in jelly.items() if path == f"/Users/{v['Id']}/Policy")
    if method == "POST":
        jelly[login]["Policy"] = body
    return type("R", (), {"ok": True, "status": 204})()


ctl.jellyfin_api = fake_jelly_api
ctl.seerr_user_id = lambda jid: jid.replace("jf-", "sr-")


def fake_seerr(method, path, body=None, cookie=None):
    login = "u-" + path.split("/")[2]
    if method == "POST":
        seerr[login] = body
    return type("R", (), {"ok": True, "status": 200, "json": lambda self=None: dict(seerr.get(login, {"username": "x", "locale": "fr"}))})()


ctl.seerr_api = fake_seerr
ctl.alerts = lambda: type("A", (), {"notify": staticmethod(lambda t, m, l="warning", force=False: alerts.append((t, m, l)) or {})})()

# --- 1. profil ---------------------------------------------------------------------------------------------------------------------
check("profil par défaut", gs.clean_profile({}) == gs.PROFILE_DEFAULTS)
for bad_value in ({"max_sessions": 99}, {"bitrate_mbps": "beaucoup"}, {"account_days": -1}, {"seerr_movie_days": 0}):
    try:
        gs.clean_profile(bad_value)
        check(f"profil refusé {bad_value}", False)
    except ctl.SeedboxError:
        check(f"profil refusé {bad_value}", True)
check("profil : clés inconnues ignorées", "pirate" not in gs.clean_profile({"pirate": 1}))

# --- 2. création d'une invitation --------------------------------------------------------------------------------------------------
for label, kwargs in (("durée trop longue", {"days": 90}), ("trop d'utilisations", {"uses": 100}), ("nom piégé", {"label": "<script>"}), ("durée non numérique", {"days": "x"})):
    try:
        gs.create(**kwargs)
        check(f"invitation refusée : {label}", False)
    except ctl.SeedboxError:
        check(f"invitation refusée : {label}", True)
made = gs.create("Pour Paul", 7, 2, {"account_days": 30, "max_sessions": 2, "bitrate_mbps": 8, "seerr_movie_limit": 3, "seerr_movie_days": 7}, "admin")
token = made["token"]
check("lien : jeton long et chemin /invite/", len(token) >= 30 and made["path"] == f"/invite/{token}")
stored = gs.INVITES_FILE.read_text()
check("le jeton n'est jamais enregistré en clair", token not in stored and gs.token_hash(token) in stored)
check("fichier des invitations en 0600", oct(gs.INVITES_FILE.stat().st_mode)[-3:] == "600")
listing = gs.listing()
check("liste : aucun jeton ni condensé exposé", token not in json.dumps(listing) and "hash" not in listing[0] and listing[0]["status"] == "active", listing[0])

# --- 3. consultation et réservation ---------------------------------------------------------------------------------------------------
check("information : invitation valide", gs.info(token)["valid"] is True and gs.info(token)["profile"]["max_sessions"] == 2)
for wrong in ("", "court", "x" * 32, token[:-1] + ("A" if token[-1] != "A" else "B"), "../../etc/passwd"):
    check(f"information : jeton invalide refusé ({wrong[:12]!r})", gs.info(wrong) == {"valid": False})
for login, password, expected in (("Marie", "motdepasse1", "invalide"), ("marie", "motdepasse1", "déjà pris"), ("bob", "court", "trop court"),
                                  ("bob", "pass\nword12", "invalide")):
    try:
        gs.reserve(token, login, password)
        check(f"réservation refusée : {login!r} {password!r}", False)
    except ctl.SeedboxError as error:
        check(f"réservation refusée : {login!r} {password!r}", True, error)
try:
    gs.reserve("z" * 32, "bob", "motdepasse1")
    check("faux jeton refusé", False)
except ctl.SeedboxError as error:
    check("faux jeton refusé", "invalide ou expirée" in str(error))
check("aucune utilisation consommée par les refus", gs.invites()[0]["uses"] == 0)
invite_id = gs.reserve(token, "paul", "motdepasse1")
check("réservation : une utilisation consommée", gs.invites()[0]["uses"] == 1 and gs.invites()[0]["pending"] == ["paul"])
try:
    gs.reserve(token, "paul", "motdepasse1")
    check("même identifiant en cours de création refusé", False)
except ctl.SeedboxError:
    check("même identifiant en cours de création refusé", True)

# --- 4. création du compte et profil ------------------------------------------------------------------------------------------------------
warnings = gs.redeem(invite_id, "paul", "motdepasse1")
check("compte créé avec le rôle « utilisateur »", created_calls[-1] == ("paul", "motdepasse1", "user") and accounts["paul"] == "user")
policy = jelly["paul"]["Policy"]
check("Jellyfin : flux simultanés et débit", policy["MaxActiveSessions"] == 2 and policy["RemoteClientBitrateLimit"] == 8_000_000 and policy["EnableContentDownloading"] is True, policy)
quota = seerr["u-sr-paul"]
check("Seerr : quota de films, séries sans quota", quota["movieQuotaLimit"] == 3 and quota["movieQuotaDays"] == 7 and quota["tvQuotaLimit"] is None and quota["locale"] == "fr", quota)
guest = json.loads(gs.GUESTS_FILE.read_text())["paul"]
check("échéance du compte à 30 jours", abs(guest["expires"] - (time.time() + 30 * 86400)) < 120 and guest["label"] == "Pour Paul")
check("invitation : compte noté, réservation soldée", gs.invites()[0]["accounts"] == ["paul"] and gs.invites()[0]["pending"] == [] and gs.invites()[0]["uses"] == 1)
check("historique tenu", "paul" in gs.HISTORY_FILE.read_text())
try:
    gs.redeem(invite_id, "intrus", "motdepasse1")
    check("compte sans réservation refusé", False)
except ctl.SeedboxError:
    check("compte sans réservation refusé", "intrus" not in accounts)

# échec de création : l'utilisation est rendue
second = gs.reserve(token, "casse", "motdepasse1")
check("deuxième utilisation consommée (2/2)", gs.invites()[0]["uses"] == 2 and gs.info(token) == {"valid": False}, gs.invites()[0]["uses"])
try:
    gs.redeem(second, "casse", "motdepasse1")
    check("échec de création signalé", False)
except ctl.SeedboxError:
    check("échec de création signalé", True)
check("échec de création : utilisation rendue, invitation de nouveau valable", gs.invites()[0]["uses"] == 1 and gs.info(token)["valid"])
third = gs.reserve(token, "avertis", "motdepasse1")
warnings = gs.redeem(third, "avertis", "motdepasse1")
check("avertissement non bloquant (compte créé)", "avertis" in accounts and warnings and "Seerr" in warnings[0], warnings)
try:
    gs.reserve(token, "quatrieme", "motdepasse1")
    check("invitation épuisée refusée", False)
except ctl.SeedboxError:
    check("invitation épuisée refusée", True)
check("invitation épuisée : statut « utilisée »", gs.listing()[0]["status"] == "utilisée")

# --- 5. expiration et révocation de l'invitation ------------------------------------------------------------------------------------------------
old = gs.create("Ancienne", 1, 1)
data = gs.invites()
data[-1]["expires"] = int(time.time()) - 5
gs.write_json(gs.INVITES_FILE, data)
check("invitation expirée refusée", gs.info(old["token"]) == {"valid": False} and gs.status_of(gs.invites()[-1]) == "expirée")
fresh = gs.create("À révoquer")
gs.revoke(next(i["id"] for i in gs.invites() if i["label"] == "À révoquer"))
check("invitation révoquée refusée", gs.info(fresh["token"]) == {"valid": False})
try:
    gs.revoke("zzzzzzzz")
    check("révocation d'une invitation inconnue refusée", False)
except ctl.SeedboxError:
    check("révocation d'une invitation inconnue refusée", True)

# --- 6. limites d'un compte existant, prolongation, échéance --------------------------------------------------------------------------------------
jelly["marie"] = {"Id": "jf-marie", "Name": "marie", "Policy": {}}
gs.set_profile("marie", {"max_sessions": 1, "downloads": False})
check("limites : Jellyfin mis à jour", jelly["marie"]["Policy"]["MaxActiveSessions"] == 1 and jelly["marie"]["Policy"]["EnableContentDownloading"] is False)
for login in ("boss", "admin"):
    try:
        gs.set_profile(login, {})
        check(f"limites refusées pour le rôle de {login}", False)
    except ctl.SeedboxError:
        check(f"limites refusées pour le rôle de {login}", True)
gs.extend("paul", 90)
check("prolongation de l'échéance", json.loads(gs.GUESTS_FILE.read_text())["paul"]["expires"] > time.time() + 89 * 86400)
gs.extend("paul", 0)
check("échéance supprimée", json.loads(gs.GUESTS_FILE.read_text())["paul"]["expires"] == 0)
try:
    gs.extend("inconnu", 5)
    check("prolongation d'un compte non invité refusée", False)
except ctl.SeedboxError:
    check("prolongation d'un compte non invité refusée", True)
guests_now = json.loads(gs.GUESTS_FILE.read_text())
guests_now["paul"]["expires"] = int(time.time()) - 60
guests_now["avertis"]["expires"] = int(time.time()) + 86400
gs.write_json(gs.GUESTS_FILE, guests_now)
removed = gs.expire()
check("échéance : compte expiré supprimé, autre conservé", removed == ["paul"] and "paul" not in accounts and "avertis" in accounts, removed)
check("échéance : alerte d'information", alerts and "échéance" in alerts[-1][0] and "paul" in alerts[-1][1])
check("échéance : enregistrement retiré", "paul" not in json.loads(gs.GUESTS_FILE.read_text()))
accounts.pop("avertis")
gs.expire()
check("compte supprimé à la main : enregistrement nettoyé", "avertis" not in json.loads(gs.GUESTS_FILE.read_text()))
overview = gs.overview()
check("vue d'ensemble : invitations, comptes, limites", overview["invites"] and any(a["login"] == "marie" for a in overview["accounts"])
      and all(a["login"] not in ("admin", "boss") for a in overview["accounts"]), [a["login"] for a in overview["accounts"]])

# --- 7. console : route publique ------------------------------------------------------------------------------------------------------------------
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/console"))
import server  # noqa: E402

ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret"}
server.ATTEMPT_LIMIT = 12


def fake_start_job(title, argv, stdin=None):
    identifier = "%012x" % (len(server.JOBS) + 1)
    server.JOBS[identifier] = {"id": identifier, "title": title, "state": "running", "log": "mot de passe: " + (stdin or ""), "started": time.time()}

    def work():
        try:
            gs.redeem(argv[2], argv[3], stdin)
            server.JOBS[identifier]["state"] = "ok"
        except Exception:                                     # noqa: BLE001
            server.JOBS[identifier]["state"] = "error"
    threading.Thread(target=work).start()
    return identifier


server.start_job = fake_start_job
console = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=console.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{console.server_port}"


def call(path, body=None, public=True, admin=False, ip="10.0.0.1", extra=None):
    headers = {"X-Forwarded-For": ip}
    if public:
        headers["X-Seedbox-Public-Secret"] = "secret"
    if admin:
        headers.update({"X-Seedbox-Proxy-Secret": "secret", "Remote-Groups": "admins", "Remote-User": "admin"})
    headers.update(extra or {})
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=20) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if response.headers["Content-Type"].startswith("application/json") else raw.decode(errors="replace")), response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.load(error), error.headers


invite = gs.create("Pour Julie", 3, 1, {"account_days": 7, "max_sessions": 1})
token2 = invite["token"]
code, html, headers = call(f"/invite/{token2}")
check("page d'invitation servie", code == 200 and "Créer mon compte" in html and "no-referrer" in (headers.get("Referrer-Policy") or ""), code)
check("page d'invitation : en-têtes de sécurité", "frame-ancestors 'none'" in headers["Content-Security-Policy"] and headers["Cache-Control"] == "no-store")
check("sans le secret public : refusée", call(f"/invite/{token2}", public=False)[0] == 403)
check("avec le seul secret d'administration : refusée sur la route publique", call("/invite/api/info?token=" + token2, public=False, admin=True)[0] == 403)
code, data, _ = call("/invite/api/info?token=" + token2, ip="10.0.0.2")
check("information publique", code == 200 and data["valid"] and data["profile"]["account_days"] == 7 and "hash" not in json.dumps(data))
code, data, _ = call("/invite/api/info?token=" + "x" * 30, ip="10.0.0.3")
check("jeton inconnu : réponse neutre", code == 200 and data == {"valid": False})
for path in ("/api/users", "/api/status", "/api/credentials", "/api/guests", "/api/appliance"):
    code, data, _ = call(path, public=True, admin=False)
    check(f"route d'administration fermée à la route publique {path}", code == 403, code)
    code, data, _ = call(path, public=True, admin=True)
    check(f"... même avec un faux en-tête d'administration {path}", code == 403, code)
check("POST d'administration fermé à la route publique", call("/api/guests/invite", {"label": "x"}, public=True, admin=True)[0] == 403)
check("fichiers publics : feuille de style", call("/invite/static/app.css", ip="10.0.0.4")[0] == 200)
check("fichiers publics : police", call("/invite/static/fonts/fira-sans-latin-400-normal.woff2", ip="10.0.0.4")[0] == 200)
for hidden in ("server.py", "index.html", "app.js", "../server.py", "fonts/../server.py", "fonts/x.py"):
    code = call("/invite/static/" + hidden, ip="10.0.0.5")[0]
    check(f"fichier non public refusé : {hidden}", code in (403, 404), code)
code, data, _ = call("/invite/api/accept", {"token": token2, "login": "julie", "password": "court"}, ip="10.0.0.6")
check("création publique : mot de passe trop court refusé", code == 400 and "court" in data["error"], data)
code, data, _ = call("/invite/api/accept", {"token": "x" * 30, "login": "julie", "password": "motdepasse1"}, ip="10.0.0.6")
check("création publique : faux jeton refusé", code == 400 and "invalide" in data["error"])
code, data, _ = call("/invite/api/accept", {"token": token2, "login": "julie", "password": "motdepasse1"}, ip="10.0.0.7")
check("création publique : tâche lancée", code == 200 and "job" in data, data)
job = data["job"]
for _ in range(50):
    state = call("/invite/api/job?id=" + job, ip="10.0.0.7")[1]
    if state.get("state") != "running":
        break
    time.sleep(0.1)
check("suivi public : seul l'état est visible, jamais le journal", state == {"state": "ok"}, state)
check("compte créé par la route publique", accounts.get("julie") == "user" and jelly["julie"]["Policy"]["MaxActiveSessions"] == 1)
check("suivi public : tâche d'administration invisible", call("/invite/api/job?id=%012x" % 999, ip="10.0.0.7")[0] == 404)
server.JOBS["abcdefabcdef"] = {"id": "abcdefabcdef", "state": "ok", "log": "secret", "started": time.time()}
check("suivi public : tâche non publique invisible", call("/invite/api/job?id=abcdefabcdef", ip="10.0.0.7")[0] == 404)
code, data, _ = call("/invite/api/accept", {"token": token2, "login": "julie2", "password": "motdepasse1"}, ip="10.0.0.8")
check("invitation à usage unique : deuxième création refusée", code == 400)
# limitation du débit
codes = [call("/invite/api/info?token=aaaa", ip="10.9.9.9")[0] for _ in range(16)]
check("limite d'essais par adresse (429 au-delà de 12)", codes[:12] == [200] * 12 and 429 in codes[12:], codes)
check("limite d'essais : une autre adresse n'est pas touchée", call("/invite/api/info?token=aaaa", ip="10.9.9.10")[0] == 200)
# administration
code, data, _ = call("/api/guests", public=False, admin=True)
check("administration : vue d'ensemble", code == 200 and data["invites"] and "defaults" in data)
code, data, _ = call("/api/guests/invite", {"label": "Depuis la console", "days": 5, "uses": 3, "profile": {"max_sessions": 2}}, public=False, admin=True)
check("administration : invitation créée, lien rendu une seule fois", code == 200 and data["path"].startswith("/invite/") and data["token"] in data["path"], data)
code, data, _ = call("/api/guests/invite", {"days": 400}, public=False, admin=True)
check("administration : invitation invalide refusée", code == 400)
rid = next(i["id"] for i in gs.invites() if i["label"] == "Depuis la console")
code, data, _ = call("/api/guests/revoke", {"id": rid}, public=False, admin=True)
check("administration : révocation", code == 200 and next(i for i in data["invites"] if i["id"] == rid)["status"] == "révoquée")
check("administration : identifiant piégé refusé", call("/api/guests/revoke", {"id": "../x"}, public=False, admin=True)[0] == 400
      and call("/api/guests/profile", {"login": "x;y", "profile": {}}, public=False, admin=True)[0] == 400)
check("administration : réservé aux administrateurs", call("/api/guests", public=False, admin=False, extra={"X-Seedbox-Proxy-Secret": "secret", "Remote-Groups": "users"})[0] == 403)

print("échecs :", bad)
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if bad else 0)
