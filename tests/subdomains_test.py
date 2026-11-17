"""Sous-domaines des applications : routes Traefik, règles et cookies Authelia, certificats, état DNS, changements."""
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbsub"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def fails(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ctl.SeedboxError as error:
        return str(error)
    return None


apps = ctl.apps()
built = apps.builtin()
extra = [built["lidarr"], {**apps.validate({"id": "heimdall", "image": "lscr.io/linuxserver/heimdall:latest", "port": 80, "generic": True, "strip": True}),
                           "source": "linuxserver"}, built["recyclarr"]]
apps.installed_entries = lambda: extra
env = {"DOMAIN": "vmdmz-seedbox01.ar-systems.fr", "PUBLIC_HOSTS": "vmdmz-seedbox01.ar-systems.fr,172.24.0.109", "AUTH_2FA": "none",
       "PUID": "1000", "PGID": "1000"}
ctl.env_read = lambda: dict(env)
ctl.qbm_rules = lambda: ""
ctl.qbit_host = lambda: "qbittorrent"
ctl.primary_ip = lambda: "172.24.0.109"


class Shares:
    @staticmethod
    def location(role):
        return "/data/downloads"


ctl.shares = Shares
sub = ctl.subdomains()

# --- désactivé par défaut : rien ne change -------------------------------------------------------------------------------
check("désactivé : aucun domaine, aucun nom", sub.domain() == "" and sub.hosts(env) == [])
check("désactivé : aucun fichier de routes", sub.render_yaml() == "" and not sub.render_file())
values = ctl.template_values(env)
cookies = yaml.safe_load("c:\n" + values["AUTH_COOKIES"])["c"]
check("désactivé : un cookie par adresse, comme avant", [c["domain"] for c in cookies] == ["vmdmz-seedbox01.ar-systems.fr", "172.24.0.109"])
check("désactivé : règles Authelia sur les adresses seulement", "*." not in values["AUTH_DOMAINS"])
check("désactivé : certificat Let's Encrypt inchangé", ctl.template_values({**env, "ACME_EMAIL": "a@b.fr"})["TRAEFIK_TLS"].count("sans") == 0)

# --- validation ----------------------------------------------------------------------------------------------------------
check("domaine : une adresse IP est refusée", "invalide" in (fails(sub.check_domain, "10.0.0.5") or ""))
check("domaine : un nom sans point est refusé", "invalide" in (fails(sub.check_domain, "localhost") or ""))
check("domaine : vide refusé", fails(sub.check_domain, "") is not None)
check("domaine : normalisé", sub.check_domain(" Ar-Systems.FR. ") == "ar-systems.fr")
check("nom : application inconnue", "inconnue" in (fails(sub.check_name, "nexistepas", "x") or ""))
check("nom : caractères invalides", "invalide" in (fails(sub.check_name, "radarr", "a b") or ""))
check("nom : réservé", "réservé" in (fails(sub.check_name, "radarr", "auth") or ""))
check("nom : déjà pris", "déjà" in (fails(sub.check_name, "radarr", "sonarr") or ""))
check("nom : vide = nom par défaut", sub.check_name("radarr", "") == "")

# --- activation ----------------------------------------------------------------------------------------------------------
calls = []
ctl.apply_addresses = lambda: calls.append("apply") or []
ctl.render_templates = lambda: calls.append("render") or sub.render_file(env)
ctl.set_ownership = lambda: calls.append("owner")
def fake_run(*a, **k):
    calls.append(("run",) + tuple(str(x) for x in a))
    return subprocess.CompletedProcess(a, 0, "", "")


ctl.run = fake_run
check("activer : relance l'application des adresses", sub.set_domain("ar-systems.fr") == [] and calls == ["apply"], str(calls))
check("activer : domaine enregistré", sub.domain() == "ar-systems.fr")
render = sub.render_file(env)
check("fichier de routes écrit", render and sub.DYNAMIC_FILE.exists())
doc = yaml.safe_load(sub.DYNAMIC_FILE.read_text())
routers, middlewares = doc["http"]["routers"], doc["http"]["middlewares"]
names = sub.hosts(env)
check("noms : un par application, catalogue compris, sans Recyclarr",
      "radarr.ar-systems.fr" in names and "lidarr.ar-systems.fr" in names and "heimdall.ar-systems.fr" in names
      and not any(n.startswith("recyclarr") for n in names), str(names))
check("noms : pas de page auth. (l'adresse principale est sous le domaine)", not any(n.startswith("auth.") for n in names))
check("toutes les routes en websecure", all(r["entryPoints"] == ["websecure"] for r in routers.values()))
check("toutes les routes sont protégées par les en-têtes de sécurité", all("secure-headers@file" in r["middlewares"] for r in routers.values()))
check("services Docker référencés", routers["sub-radarr"]["service"] == "radarr@docker" and routers["sub-lidarr"]["service"] == "app-lidarr@docker"
      and routers["sub-qbm"]["service"] == "qbit-manage@docker")
check("priorités : chemin > hôte > Homepage", routers["sub-radarr-path"]["priority"] > routers["sub-radarr"]["priority"] > 1)
radarr = routers["sub-radarr"]["middlewares"]
check("Radarr : le chemin est ajouté AVANT Authelia", radarr.index("sub-radarr-prefix") < radarr.index("authelia@file"), str(radarr))
check("Radarr : prefixe /radarr", middlewares["sub-radarr-prefix"]["addPrefix"]["prefix"] == "/radarr")
check("Radarr : adresse déjà sous /radarr transmise telle quelle (pas de double chemin)",
      "sub-radarr-prefix" not in routers["sub-radarr-path"]["middlewares"] and "PathPrefix(`/radarr`)" in routers["sub-radarr-path"]["rule"]
      and routers["sub-radarr-path"]["rule"].startswith("Host(`radarr.ar-systems.fr`)"))
qbit = routers["sub-qbittorrent"]["middlewares"]
check("qBittorrent : chemin ajouté, Authelia, puis retiré", qbit.index("sub-qbittorrent-prefix") < qbit.index("authelia@file") < qbit.index("sub-qbittorrent-strip"), str(qbit))
check("qBittorrent : pas de route directe sous le chemin", "sub-qbittorrent-path" not in routers)
check("Jackett : redirection /jackett vers /jackett/ sur la route directe", routers["sub-jackett-path"]["middlewares"][0] == "slash@file")
heimdall = routers["sub-heimdall"]["middlewares"]
check("application générique du catalogue : chemin ajouté, Authelia, puis retiré",
      heimdall.index("sub-heimdall-prefix") < heimdall.index("authelia@file") < heimdall.index("sub-heimdall-strip"), str(heimdall))
check("Seerr : à la racine, sans chemin ni Authelia (comptes Jellyfin), avec limitation",
      routers["sub-seerr"]["middlewares"] == ["rate-limit@file", "secure-headers@file"] and "sub-seerr-prefix" not in middlewares)
check("Dockge : chaîne Authelia dédiée (chemin fictif /dockge/)", routers["sub-dockge"]["middlewares"][0] == "authelia-dockge@file")
check("Jellyfin : /jellyfin ajouté, aucune connexion Authelia (comptes Jellyfin)",
      "authelia@file" not in routers["sub-jellyfin"]["middlewares"] and middlewares["sub-jellyfin-prefix"]["addPrefix"]["prefix"] == "/jellyfin")
check("règles de routage : sans tabulation ni chaîne vide", all(r["rule"].count("`") % 2 == 0 and "Host(`" in r["rule"] for r in routers.values()))

# --- Authelia : règles et cookies -----------------------------------------------------------------------------------------
values = ctl.template_values(env)
check("règles d'accès : *.domaine ajouté", '"*.ar-systems.fr"' in values["AUTH_DOMAINS"], values["AUTH_DOMAINS"])
cookies = yaml.safe_load("c:\n" + values["AUTH_COOKIES"])["c"]
by_domain = {c["domain"]: c for c in cookies}
check("cookies : l'adresse sous le domaine n'a plus de cookie propre (sinon Authelia refuse la configuration)",
      "vmdmz-seedbox01.ar-systems.fr" not in by_domain and set(by_domain) == {"172.24.0.109", "ar-systems.fr"}, str(list(by_domain)))
check("cookie du domaine parent : page de connexion sur l'adresse principale",
      by_domain["ar-systems.fr"]["authelia_url"] == "https://vmdmz-seedbox01.ar-systems.fr/auth"
      and by_domain["ar-systems.fr"]["default_redirection_url"] == "https://vmdmz-seedbox01.ar-systems.fr/")
check("cookie de l'adresse IP conservé", by_domain["172.24.0.109"]["authelia_url"] == "https://172.24.0.109/auth")
check("noms inconnus : les sous-domaines servis ne sont pas redirigés vers l'adresse principale",
      "!Host(`radarr.ar-systems.fr`)" in values["OTHER_HOSTS_RULE"] and "!Host(`172.24.0.109`)" in values["OTHER_HOSTS_RULE"])

text = ctl.substitute((ctl.SHARE / "templates/config/authelia/configuration.yml").read_text(), values)
config = yaml.safe_load(text)
rules = config["access_control"]["rules"]


def decide(groups, host, path):
    for rule in rules:
        domains = rule["domain"] if isinstance(rule["domain"], list) else [rule["domain"]]
        if not any(fnmatch.fnmatch(host, d) for d in domains):
            continue
        if "resources" in rule and not any(re.search(r, path) for r in rule["resources"]):
            continue
        if "subject" in rule and not any(s == f"group:{g}" for s in rule["subject"] for g in groups):
            continue
        return rule["policy"]
    return config["access_control"]["default_policy"]


def seen_path(route_id):
    """Chemin que voit Authelia pour la racine d'un sous-domaine : le chemin ajouté avant la vérification."""
    chain = routers[f"sub-{route_id}"]["middlewares"]
    if f"sub-{route_id}-prefix" in chain and chain.index(f"sub-{route_id}-prefix") < chain.index("authelia@file"):
        return middlewares[f"sub-{route_id}-prefix"]["addPrefix"]["prefix"] + "/"
    return "/"


# Sur chaque sous-domaine protégé, Authelia juge la requête comme sur l'adresse par chemin : rien n'est contourné
for ident in ("radarr", "sonarr", "prowlarr", "qbittorrent", "jackett", "autobrr", "qbm", "lidarr", "heimdall"):
    host = f"{ident}.ar-systems.fr"
    path = seen_path(ident)
    check(f"{ident} : Authelia voit {path}, pas la racine", path == f"/{ident}/", path)
    check(f"{ident} : un utilisateur simple est refusé", decide(["users"], host, path) == "deny", decide(["users"], host, path))
    check(f"{ident} : un administrateur est accepté", decide(["admins"], host, path) in ("one_factor", "two_factor"))
check("gestionnaire : accepté sur radarr, refusé sur jackett et autobrr",
      decide(["managers"], "radarr.ar-systems.fr", "/radarr/") == "one_factor" and decide(["managers"], "jackett.ar-systems.fr", "/jackett/") == "deny"
      and decide(["managers"], "autobrr.ar-systems.fr", "/autobrr/") == "deny")
check("le contournement par la racine serait ouvert aux comptes simples (raison de l'ajout du chemin AVANT Authelia)",
      decide(["users"], "radarr.ar-systems.fr", "/") == "one_factor")
check("autre domaine : aucune règle", decide(["admins"], "radarr.autre.fr", "/radarr/") == "deny")

# --- certificats ---------------------------------------------------------------------------------------------------------
tls = {"ACME_EMAIL": "moi@ar-systems.fr"}
http_values = ctl.template_values({**env, **tls})
traefik = yaml.safe_load(ctl.substitute((ctl.SHARE / "templates/config/traefik/traefik.yml").read_text(), http_values))
domains = traefik["entryPoints"]["websecure"]["http"]["tls"]["domains"][0]
check("Let's Encrypt (défi HTTP) : un nom par application, sans l'adresse principale",
      domains["main"] == "vmdmz-seedbox01.ar-systems.fr" and "radarr.ar-systems.fr" in domains["sans"] and domains["main"] not in domains["sans"], str(domains))
dns_values = ctl.template_values({**env, **tls, "ACME_DNS_PROVIDER": "cloudflare"})
traefik = yaml.safe_load(ctl.substitute((ctl.SHARE / "templates/config/traefik/traefik.yml").read_text(), dns_values))
check("Let's Encrypt (défi DNS) : certificat générique", traefik["entryPoints"]["websecure"]["http"]["tls"]["domains"][0]["sans"] == ["*.ar-systems.fr"])
check("autorité locale : le générique est dans les noms du certificat", sub.cert_names() == ["*.ar-systems.fr"])
if shutil.which("openssl"):
    subprocess_run = subprocess.run
    ctl.run = lambda *a, check=True, capture=False, **k: subprocess_run([str(x) for x in a], check=check, capture_output=capture, text=True)
    ctl.ensure_certificates({**env})
    dump = subprocess.run(["openssl", "x509", "-in", str(ctl.CONFIG / "traefik/certs/server.crt"), "-noout", "-ext", "subjectAltName"],
                          capture_output=True, text=True).stdout
    check("certificat de l'appliance : SAN générique et adresses", "DNS:*.ar-systems.fr" in dump and "IP Address:172.24.0.109" in dump
          and "DNS:vmdmz-seedbox01.ar-systems.fr" in dump, dump.strip()[:200])
    stamp = (ctl.CONFIG / "traefik/certs/server.crt").read_bytes()
    ctl.ensure_certificates({**env})
    check("certificat : inchangé quand rien ne change", (ctl.CONFIG / "traefik/certs/server.crt").read_bytes() == stamp)
    ctl.run = fake_run
else:
    print("[SKIP] openssl absent : certificat non vérifié")

# --- adresse principale hors du domaine : page de connexion auth.<domaine> -------------------------------------------------
outside = {**env, "DOMAIN": "seedbox.exemple.org", "PUBLIC_HOSTS": "seedbox.exemple.org,172.24.0.109"}
ctl.env_read = lambda: dict(outside)
check("hors domaine : la page de connexion devient auth.<domaine>", sub.portal(outside) == "auth.ar-systems.fr" and "auth.ar-systems.fr" in sub.hosts(outside))
vals = ctl.template_values(outside)
cookies = {c["domain"]: c for c in yaml.safe_load("c:\n" + vals["AUTH_COOKIES"])["c"]}
check("hors domaine : cookie du domaine, page de connexion auth.<domaine>, adresse principale conservée",
      cookies["ar-systems.fr"]["authelia_url"] == "https://auth.ar-systems.fr/auth" and "seedbox.exemple.org" in cookies, str(list(cookies)))
view = sub.overview(check=False)
check("hors domaine : ligne « Page de connexion » et avertissement", any(a.get("portal") for a in view["apps"]) and any("auth.ar-systems.fr" in w for w in view["warnings"]))
ctl.env_read = lambda: dict(env)

# --- état DNS -----------------------------------------------------------------------------------------------------------
table = {"radarr.ar-systems.fr": ["172.24.0.109"], "sonarr.ar-systems.fr": ["10.9.9.9"], "vmdmz-seedbox01.ar-systems.fr": ["172.24.0.109"]}
sub.resolve = lambda name: table.get(name, [])
view = sub.overview()
rows = {a["id"]: a for a in view["apps"]}
check("DNS : même adresse que le serveur = ok", rows["radarr"]["state"] == "ok", str(rows["radarr"]))
check("DNS : autre adresse = autre", rows["sonarr"]["state"] == "autre" and rows["sonarr"]["ips"] == ["10.9.9.9"])
check("DNS : introuvable = absent", rows["prowlarr"]["state"] == "absent")
check("aperçu : CNAME vers l'adresse principale", view["record"] == "CNAME" and view["target"] == "vmdmz-seedbox01.ar-systems.fr")
check("aperçu : URL des applications", rows["radarr"]["url"] == "https://radarr.ar-systems.fr/" and rows["radarr"]["protected"] and not rows["seerr"]["protected"])
check("aperçu : suggestion de domaine", sub.suggestion(env) == "ar-systems.fr")
check("aperçu : avertissement sur l'autorité locale", any("autorité" in w for w in view["warnings"]))
ip_only = {**env, "DOMAIN": "172.24.0.109", "PUBLIC_HOSTS": "172.24.0.109"}
ctl.env_read = lambda: dict(ip_only)
check("adresse IP seule : pas de suggestion, enregistrement A", sub.suggestion(ip_only) == "" and sub.overview(check=False)["record"] == "A")
ctl.env_read = lambda: dict(env)

# --- noms et activation par application ----------------------------------------------------------------------------------
calls.clear()
sub.rename("seerr", "overseerr")
doc = yaml.safe_load(sub.DYNAMIC_FILE.read_text())
check("renommer : le nouveau nom sert la route (alias DNS existant « overseerr »)", doc["http"]["routers"]["sub-seerr"]["rule"] == "Host(`overseerr.ar-systems.fr`)")
check("renommer : sans redémarrage de conteneur (relu à chaud)", calls == ["render", "owner"], str(calls))
check("renommer : l'ancien nom est libéré", fails(sub.check_name, "dockge", "overseerr") is not None and fails(sub.check_name, "dockge", "seerr") is None)
sub.rename("seerr", "")
check("renommer : vide rétablit le nom par défaut", "seerr" not in sub.settings()["names"])
sub.set_enabled("dockge", False)
doc = yaml.safe_load(sub.DYNAMIC_FILE.read_text())
check("retirer une application : plus de route", "sub-dockge" not in doc["http"]["routers"] and "dockge.ar-systems.fr" not in sub.hosts(env))
check("retirer une application : la route directe de ses voisines reste", "sub-radarr" in doc["http"]["routers"])
sub.set_enabled("dockge", True)
check("remettre une application", "sub-dockge" in yaml.safe_load(sub.DYNAMIC_FILE.read_text())["http"]["routers"])
check("application inconnue refusée", fails(sub.set_enabled, "xx", True) is not None)
calls.clear()
ctl.env_read = lambda: {**env, "ACME_EMAIL": "moi@ar-systems.fr"}
sub.rename("seerr", "demandes")
check("Let's Encrypt en défi HTTP : Traefik relancé pour les nouveaux noms", ("run", "docker", "restart", "traefik") in [c[:4] for c in calls if isinstance(c, tuple)], str(calls))
sub.rename("seerr", "")
ctl.env_read = lambda: dict(env)

# --- désactivation -------------------------------------------------------------------------------------------------------
calls.clear()
check("désactiver : relance l'application des adresses", sub.disable() == [] and calls == ["apply"])
check("désactiver : domaine vidé", sub.domain() == "" and sub.hosts(env) == [])
check("désactiver : un second appel ne fait rien", sub.disable() == [] and calls == ["apply"])
sub.render_file(env)
check("désactiver : le fichier de routes disparaît", not sub.DYNAMIC_FILE.exists())
values = ctl.template_values(env)
check("désactiver : retour aux cookies par adresse", "ar-systems.fr'" not in values["AUTH_COOKIES"].split("domain: '172")[0].replace("vmdmz-seedbox01.ar-systems.fr", ""))

# --- ligne de commande ---------------------------------------------------------------------------------------------------
sys.argv = ["seedboxctl", "subdomains", "show"]
try:
    code = ctl.main()
except SystemExit as stop:
    code = stop.code
check("seedboxctl subdomains show", code in (0, None))

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
