"""Rôles : règles d'accès Authelia générées (administrateur, gestionnaire, utilisateur) et gestion des comptes."""
import os as _os
SRC = _os.environ.get("SB_SRC") or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import json, os, re, sys, tempfile
from pathlib import Path
import yaml
root = Path(tempfile.mkdtemp(prefix="sbrole"))
for k, d in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[k] = str(root / d)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl
bad = 0
def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:200])

apps = ctl.apps()
ctl.BASE.mkdir(parents=True, exist_ok=True); ctl.STATE.mkdir(parents=True, exist_ok=True)
built = apps.builtin()
apps.installed_read = lambda: {k: {"entry": built[k]} for k in ("lidarr", "bazarr", "recyclarr")}
apps.installed_entries = lambda: [built[k] for k in ("lidarr", "bazarr", "recyclarr")] + [
    {**apps.validate({"id": "heimdall", "image": "lscr.io/linuxserver/heimdall:latest", "port": 80, "generic": True, "strip": True}), "source": "linuxserver"}]
ctl.env_read = lambda: {"DOMAIN": "seedbox.local", "PUBLIC_HOSTS": "seedbox.local", "AUTH_2FA": "admins", "PUID": "1000", "PGID": "1000"}
ctl.qbm_rules = lambda: ""
ctl.qbit_host = lambda: "qbittorrent"
class S:
    @staticmethod
    def location(role): return "/data/downloads"
ctl.shares = lambda: S
values = ctl.template_values(ctl.env_read())
check("chemins gestionnaires : radarr, sonarr, prowlarr, qbittorrent, Lidarr, Bazarr", values["MANAGER_PATHS"] == "radarr|sonarr|prowlarr|qbittorrent|lidarr|bazarr", values["MANAGER_PATHS"])
check("chemins admin : tout, y compris l'app générique", all(x in values["ADMIN_PATHS"].split("|") for x in ("console", "traefik", "dockge", "jackett", "autobrr", "heimdall", "lidarr", "bazarr")))
check("pas de chemin pour Recyclarr (sans interface)", "recyclarr" not in values["ADMIN_PATHS"])
text = ctl.substitute((ctl.SHARE / "templates/config/authelia/configuration.yml").read_text(), values)
config = yaml.safe_load(text)
rules = config["access_control"]["rules"]

def decide(groups, path):
    for rule in rules:
        if "resources" in rule and not any(re.search(r, path) for r in rule["resources"]):
            continue
        if "subject" in rule and not any(s == f"group:{g}" for s in rule["subject"] for g in groups):
            continue
        return rule["policy"]
    return config["access_control"]["default_policy"]

cases = [
    (["admins"], "/console/", "two_factor"), (["admins"], "/radarr/", "two_factor"), (["admins"], "/jackett/x", "two_factor"),
    (["managers"], "/radarr/", "one_factor"), (["managers"], "/sonarr/api", "one_factor"), (["managers"], "/prowlarr/", "one_factor"),
    (["managers"], "/qbittorrent/", "one_factor"), (["managers"], "/lidarr/", "one_factor"), (["managers"], "/bazarr/", "one_factor"),
    (["managers"], "/console/", "deny"), (["managers"], "/traefik/", "deny"), (["managers"], "/dockge/", "deny"),
    (["managers"], "/jackett/", "deny"), (["managers"], "/autobrr/", "deny"), (["managers"], "/heimdall/", "deny"),
    (["managers"], "/", "one_factor"), (["managers"], "/jellyfin/", "one_factor"),
    (["users"], "/radarr/", "deny"), (["users"], "/console/", "deny"), (["users"], "/", "one_factor"), (["users"], "/jellyfin/", "one_factor"),
    ([], "/", "deny"),
]
for groups, path, expected in cases:
    got = decide(groups, path)
    check(f"{'/'.join(groups) or 'sans groupe'} {path} -> {expected}", got == expected, got)
# un chemin qui commence comme un chemin protégé ne doit pas passer
check("managers /radarrx/ n'ouvre pas /radarr (regex)", decide(["managers"], "/radarr-evil/") in ("one_factor", "deny"))

# comptes
ctl.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
ctl.users_store_add("alice", "motdepasse1", "manager")
ctl.users_store_add("bob", "motdepasse1", "user")
ctl.users_store_add("root1", "motdepasse1", "admin")
roles = {u["login"]: u["role"] for u in ctl.user_list()}
check("rôles enregistrés et relus", roles == {"alice": "manager", "bob": "user", "root1": "admin"}, roles)
check("groupe Authelia managers", "managers" in ctl.users_load()["alice"]["groups"])
try:
    ctl.user_add("carl", "motdepasse1", "dieu"); check("rôle invalide refusé", False)
except ctl.SeedboxError: check("rôle invalide refusé", True)
ctl.env_read = lambda: {"ADMIN_USER": "root1", "PUID": "1000", "PGID": "1000"}
for login, role, label in (("root1", "user", "administrateur principal protégé"), ("inconnu", "user", "compte inconnu")):
    try:
        ctl.user_set_role(login, role); check(label, False)
    except ctl.SeedboxError: check(label, True)
# le changement de rôle d'Alice passe par Jellyfin/Seerr/p2p : simulés
calls = []
ctl.jellyfin_user = lambda login: {"Id": "J1", "Policy": {"IsAdministrator": False}}
ctl.jellyfin_api = lambda method, path, body=None, token=None: (calls.append(("jf", path, body)), type("R", (), {"ok": True, "status": 200})())[1]
ctl.seerr_user_id = lambda jid: 7
ctl.seerr_api = lambda method, path, body=None, cookie=None: (calls.append(("seerr", path, body)), type("R", (), {"ok": True})())[1]
ctl.p2p_session = lambda: "c"
ctl.p2p_user_id = lambda login, cookie: 3
ctl.p2p_api = lambda method, path, body=None, cookie=None: (calls.append(("p2p", path, body)), type("R", (), {"ok": True})())[1]
ctl.say = lambda m: None
ctl.user_set_role("alice", "admin")
check("alice -> admin : groupe, Jellyfin, Seerr, p2p", "admins" in ctl.users_load()["alice"]["groups"]
      and ("jf", "/Users/J1/Policy", {"IsAdministrator": True}) in calls
      and ("seerr", "/user/7/settings/permissions", {"permissions": 2}) in calls
      and ("p2p", "/api/admin/users/3", {"role": "ADMIN"}) in calls, calls)
ctl.user_set_role("alice", "manager")
check("alice -> manager : droits Seerr de gestion des demandes", ("seerr", "/user/7/settings/permissions", {"permissions": 176}) in calls
      and ("jf", "/Users/J1/Policy", {"IsAdministrator": False}) in calls)
print("échecs :", bad); sys.exit(1 if bad else 0)
