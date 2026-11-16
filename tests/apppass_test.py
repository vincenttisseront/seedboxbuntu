"""Mots de passe des applications : réinitialisation Jellyfin, qBittorrent (API et reprise par la configuration),
autobrr, Audiobookshelf, avec des services simulés ; le renouvellement de qBittorrent et d'autobrr passe par les mêmes fonctions."""
import os
import sys
import tempfile
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbpass"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

apppass = ctl.apppasswords()
ctl.ENV_FILE.write_text("QBIT_USERNAME=admin\nQBIT_PASSWORD=ancien\nAUTOBRR_ADMIN_PASSWORD=ancien\nAUDIOBOOKSHELF_ADMIN_PASSWORD=ancien\nJELLYFIN_API_KEY=cle\n")
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def fails(function, *args):
    try:
        function(*args)
    except ctl.SeedboxError as error:
        return str(error)
    return None


class Answer:
    def __init__(self, data=None, status=200):
        self.status, self._data, self.text = status, data, ""

    ok = property(lambda self: 200 <= self.status < 300)

    def json(self):
        return self._data


calls = []
ctl.say = lambda *a, **k: None
apppass.say = lambda *a, **k: None
ctl.compose = lambda *a, **k: calls.append(("compose", a))
ctl.provision = lambda: calls.append(("provision",)) or []
running = {"jellyfin": True, "qbittorrent": True, "autobrr": True, "audiobookshelf": True}
ctl.container_running = lambda name: running.get(name, False)

# ------------------------------------------------------------------ Jellyfin
state = {"password": "inconnu"}


def jellyfin_api(method, path, body=None, token=None):
    calls.append((method, path, body))
    if path == "/Users" and method == "GET":
        return Answer([{"Id": "u1", "Name": "Vincent"}, {"Id": "u2", "Name": "papa"}])
    if path.endswith("/Password"):
        state["password"] = body["NewPw"]
        return Answer({})
    if path == "/Users/AuthenticateByName":
        return Answer({"AccessToken": "t"} if body["Pw"] == state["password"] else {}, 200 if body["Pw"] == state["password"] else 401)
    return Answer({})


ctl.jellyfin_api = jellyfin_api
ctl.apps().installed_entries = lambda: [{"id": "audiobookshelf", "name": "Audiobookshelf", "path": "/audiobookshelf", "auth": "own", "secrets": []}]
view = {a["id"]: a for a in apppass.overview()["apps"]}
check("vue : Jellyfin avec ses comptes, mot de passe à saisir", view["jellyfin"]["accounts"] == ["Vincent", "papa"] and view["jellyfin"]["typed"])
check("vue : qBittorrent avec son compte, mot de passe généré", view["qbittorrent"]["accounts"] == ["admin"] and not view["qbittorrent"]["typed"])
check("vue : Audiobookshelf présent car installé", "audiobookshelf" in view)
apppass.reset("jellyfin", "papa", "nouveau-mdp")
check("Jellyfin : mot de passe changé et vérifié par une connexion", state["password"] == "nouveau-mdp" and ("POST", "/Users/u2/Password", {"NewPw": "nouveau-mdp", "ResetPassword": False}) in calls)
check("Jellyfin : le nom de compte est insensible à la casse", apppass.reset("jellyfin", "vincent", "autre-mdp-12") == [] and state["password"] == "autre-mdp-12")
check("Jellyfin : compte inconnu refusé", "inconnu" in (fails(apppass.reset, "jellyfin", "fantome", "motdepasse1") or ""))
check("Jellyfin : mot de passe obligatoire", fails(apppass.reset, "jellyfin", "papa", "") is not None)
check("Jellyfin : mot de passe trop court refusé", fails(apppass.reset, "jellyfin", "papa", "court") is not None)
check("application inconnue refusée", fails(apppass.reset, "plex", "", "motdepasse1") is not None)

# ------------------------------------------------------------------ qBittorrent
conf = ctl.CONFIG / "qbittorrent" / "qBittorrent"
conf.mkdir(parents=True)
(conf / "qBittorrent.conf").write_text('[Preferences]\nWebUI\\Password_PBKDF2="@ByteArray(vieux:hash)"\nWebUI\\Port=8080\n')
posted = []


def request(method, url, headers=None, form=None, body=None, **_):
    posted.append((method, url.rsplit("/api", 1)[-1], form))
    return Answer({})


ctl.request, ctl.qbit_host, ctl.svc = request, (lambda: "qbittorrent"), (lambda name, port: f"http://{name}:{port}")
logins = []


def qbit_login_ok():
    logins.append(1)
    return "SID=1"


ctl.qbit_login = qbit_login_ok
calls.clear()
apppass.reset("qbittorrent", "", "mon-nouveau-pass")
check("qBittorrent : passe par l'API quand la console s'y connecte", any(p[1] == "/v2/app/setPreferences" and "mon-nouveau-pass" in p[2]["json"] for p in posted))
check("qBittorrent : mot de passe conservé et applications reliées à nouveau", ctl.env_read()["QBIT_PASSWORD"] == "mon-nouveau-pass" and ("provision",) in calls)
check("qBittorrent : configuration intacte quand l'API suffit", "vieux:hash" in (conf / "qBittorrent.conf").read_text())

attempts = {"n": 0}


def qbit_login_stale():
    attempts["n"] += 1
    if attempts["n"] == 1:
        raise ctl.SeedboxError("qBittorrent : connexion refusée (HTTP 403)")
    return "SID=2"


ctl.qbit_login = qbit_login_stale
calls.clear()
apppass.reset("qbittorrent", "", "")
text = (conf / "qBittorrent.conf").read_text()
generated = ctl.env_read()["QBIT_PASSWORD"]
check("qBittorrent : mot de passe de la console périmé → configuration réécrite", "vieux:hash" not in text and "WebUI\\Port=8080" in text and "@ByteArray(" in text)
check("qBittorrent : conteneur arrêté pendant l'écriture puis relancé", [c[1][:2] for c in calls if c[0] == "compose"][:1] == [("stop", "qbittorrent")] and ("up", "-d", "qbittorrent") in [c[1] for c in calls if c[0] == "compose"])
check("qBittorrent : mot de passe généré, 32 caractères, conservé", len(generated) == 32 and generated != "mon-nouveau-pass")
(conf / "qBittorrent.conf").write_text("[Preferences]\nWebUI\\Port=8080\n")
attempts["n"] = 0
apppass.reset("qbittorrent", "", "autre-passe-1")
check("qBittorrent : clé absente de la configuration ajoutée sous [Preferences]", (conf / "qBittorrent.conf").read_text().count("Password_PBKDF2") == 1)
ctl.qbit_login = qbit_login_ok
calls.clear()
ctl.rotate("qbittorrent")
check("renouvellement de qBittorrent : mêmes fonctions", len(ctl.env_read()["QBIT_PASSWORD"]) == 32 and ("provision",) in calls)

# ------------------------------------------------------------------ autobrr
autobrr = {"password": "ancien", "patched": None}
ctl.autobrr_session = lambda: "cookie"


def autobrr_api(method, path, body=None, cookie=None):
    if body["password_current"] != autobrr["password"]:
        return Answer({}, 403)
    autobrr["password"] = body["password_new"]
    return Answer({})


ctl.autobrr_api = autobrr_api
apppass.reset("autobrr", "", "")
check("autobrr : mot de passe généré et conservé", ctl.env_read()["AUTOBRR_ADMIN_PASSWORD"] == autobrr["password"] and len(autobrr["password"]) == 32)
ctl.env_set(AUTOBRR_ADMIN_PASSWORD="perdu")
message = fails(apppass.reset, "autobrr", "", "")
check("autobrr : mot de passe changé hors console → message clair", message is not None and "inconnu de la console" in message, message)

# ------------------------------------------------------------------ Audiobookshelf
abs_state = {"password": "ancien"}


def abs_request(method, url, headers=None, body=None, **_):
    if url.endswith("/login"):
        good = body["password"] == abs_state["password"]
        return Answer({"user": {"id": "r1", "token": "tok"}} if good else {}, 200 if good else 401)
    if method == "PATCH" and url.endswith("/api/users/r1"):
        abs_state["password"] = body["password"]
        return Answer({})
    return Answer({})


ctl.request = abs_request
apppass.reset("audiobookshelf", "", "livres-audio-12")
check("Audiobookshelf : mot de passe changé, conservé et vérifié", abs_state["password"] == "livres-audio-12" and ctl.env_read()["AUDIOBOOKSHELF_ADMIN_PASSWORD"] == "livres-audio-12")
ctl.env_set(AUDIOBOOKSHELF_ADMIN_PASSWORD="perdu")
check("Audiobookshelf : mot de passe changé hors console → message clair", "inconnu de la console" in (fails(apppass.reset, "audiobookshelf", "", "") or ""))

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
