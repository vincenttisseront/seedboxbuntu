"""SeedBoxBuntu - accès distant : adresses supplémentaires, Tailscale, Let's Encrypt par DNS, Seerr depuis Internet.

Tailscale (mode « userspace », sans privilège) publie l'appliance sur votre réseau privé avec un vrai
certificat HTTPS, sans ouvrir aucun port de la box : « tailscale serve » transmet à Traefik. Le nom du
serveur sur le tailnet devient une adresse de l'appliance (cookie de session, certificat).
Let's Encrypt par DNS (défi DNS-01) délivre le certificat d'un nom de domaine sans exposer le port 80.

Seerr depuis Internet : Seerr a son propre point d'entrée Traefik (port 5055) sur lequel AUCUNE autre application n'est
servie. Rediriger le port 443 de la box vers le port 5055 du serveur publie donc Seerr seul : la console, Authelia, Radarr,
qBittorrent... restent injoignables depuis Internet. Il suffit d'un nom public (DuckDNS gratuit, ou votre domaine) :
  - un certificat Let's Encrypt propre à ce nom (résolveur « seerr » : défi DNS avec le jeton DuckDNS, sinon défi HTTP par
    le port 80 de la box, qui ne sert que la redirection vers HTTPS et la validation) ;
  - l'adresse de Seerr (liens des e-mails et notifications) devient https://nom-public ;
  - avec DuckDNS, l'adresse IP du nom suit celle de la box (minuteur toutes les 5 minutes).
La connexion à Seerr reste celle de Seerr (comptes Jellyfin), avec la limite de débit de Traefik.
"""
import json
import re
import time

import seedboxctl as ctl

HOSTNAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
AUTHKEY = re.compile(r"^tskey-[A-Za-z0-9_-]{10,200}$")
PROVIDER = re.compile(r"^[a-z0-9]{2,30}$")
VARNAME = re.compile(r"^[A-Z][A-Z0-9_]{1,60}$")
DNS_ENV = ctl.CONFIG / "traefik" / "acme-dns.env"
SERVE_DIR = ctl.CONFIG / "tailscale-serve"
DUCKDNS = re.compile(r"^([a-z0-9-]{1,63})\.duckdns\.org$")
TOKEN = re.compile(r"^[A-Za-z0-9-]{20,64}$")
DDNS_STATE = ctl.STATE / "seerr-ddns.json"
SERVE = {"TCP": {"443": {"HTTPS": True}},
         "Web": {"${TS_CERT_DOMAIN}:443": {"Handlers": {"/": {"Proxy": "https+insecure://172.30.0.2:443"}}}}}
# Fournisseurs DNS courants de Traefik (lego) ; la liste complète : doc.traefik.io, « ACME DNS challenge »
PROVIDERS = {"cloudflare": "Cloudflare (CF_DNS_API_TOKEN)", "ovh": "OVH (OVH_ENDPOINT, OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET, OVH_CONSUMER_KEY)",
             "gandiv5": "Gandi (GANDIV5_PERSONAL_ACCESS_TOKEN)", "digitalocean": "DigitalOcean (DO_AUTH_TOKEN)",
             "route53": "Amazon Route 53 (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)", "duckdns": "DuckDNS (DUCKDNS_TOKEN)",
             "hetzner": "Hetzner (HETZNER_API_KEY)", "scaleway": "Scaleway (SCW_ACCESS_KEY, SCW_SECRET_KEY)",
             "infomaniak": "Infomaniak (INFOMANIAK_ACCESS_TOKEN)", "ionos": "IONOS (IONOS_API_KEY)",
             "namecheap": "Namecheap (NAMECHEAP_API_USER, NAMECHEAP_API_KEY)", "porkbun": "Porkbun (PORKBUN_API_KEY, PORKBUN_SECRET_API_KEY)"}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def set_profile(name, on):
    profiles = ctl.profiles_read()
    (profiles.add if on else profiles.discard)(name)
    ctl.env_set(COMPOSE_PROFILES=",".join(sorted(profiles)))


# --- adresses supplémentaires -----------------------------------------------------------------
def set_hosts(value):
    hosts = [h.strip().lower() for h in re.split(r"[,\s]+", value or "") if h.strip()]
    for host in hosts:
        if not (ctl.is_ip(host) or ctl.valid_domain(host)):
            raise ctl.SeedboxError(f"adresse invalide : {host}")
    ctl.env_set(EXTRA_HOSTS=",".join(hosts))
    say("adresses supplémentaires : " + (", ".join(hosts) or "aucune"))
    return ctl.apply_addresses()


# --- Tailscale --------------------------------------------------------------------------------
def tailscale_status():
    env = ctl.env_read()
    info = {"enabled": "tailscale" in ctl.profiles_read(), "running": ctl.container_running("tailscale"),
            "hostname": env.get("TAILSCALE_HOSTNAME", "seedbox"), "dns_name": env.get("TAILSCALE_DNSNAME", ""),
            "state": "", "login_url": "", "ips": [], "has_key": bool(env.get("TAILSCALE_AUTHKEY"))}
    if info["running"]:
        result = ctl.run("docker", "exec", "tailscale", "tailscale", "status", "--json", check=False, capture=True)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            return info
        info["state"] = data.get("BackendState", "")
        info["login_url"] = data.get("AuthURL", "") or ""
        me = data.get("Self") or {}
        info["ips"] = me.get("TailscaleIPs") or []
        info["live_dns_name"] = (me.get("DNSName") or "").rstrip(".")
    return info


def tailscale_sync():
    """Le nom du serveur sur le tailnet devient une adresse de l'appliance (cookie, certificat)."""
    status = tailscale_status()
    wanted = status.get("live_dns_name", "") if status["state"] == "Running" else ""
    if wanted and wanted != ctl.env_read().get("TAILSCALE_DNSNAME", ""):
        ctl.env_set(TAILSCALE_DNSNAME=wanted)
        say(f"Tailscale : adresse {wanted}")
        return ctl.apply_addresses()
    return []


def tailscale_set(authkey="", hostname="seedbox"):
    authkey = authkey.strip()
    if not HOSTNAME.match(hostname):
        raise ctl.SeedboxError("nom Tailscale invalide : minuscules, chiffres et tirets")
    if authkey and not AUTHKEY.match(authkey):
        raise ctl.SeedboxError("clé d'authentification invalide (tskey-...)")
    SERVE_DIR.mkdir(parents=True, exist_ok=True)
    (SERVE_DIR / "serve.json").write_text(json.dumps(SERVE, indent=2) + "\n")
    (ctl.CONFIG / "tailscale").mkdir(parents=True, exist_ok=True)
    updates = {"TAILSCALE_HOSTNAME": hostname}
    if authkey:
        updates["TAILSCALE_AUTHKEY"] = authkey
    ctl.env_set(**updates)
    set_profile("tailscale", True)
    ctl.compose("up", "-d", "tailscale", capture=True)
    say("Tailscale démarré : attente de la connexion")
    deadline = time.time() + 90
    status = {}
    while time.time() < deadline:
        status = tailscale_status()
        if status["state"] == "Running" or status["login_url"]:
            break
        time.sleep(3)
    if status.get("state") == "Running":
        return tailscale_sync()
    if status.get("login_url"):
        say(f"Tailscale : validez la connexion de ce serveur sur {status['login_url']} puis actualisez la page")
    else:
        say("Tailscale : connexion en cours, actualisez la page dans un instant")
    return []


def tailscale_disable():
    set_profile("tailscale", False)
    ctl.run("docker", "rm", "-f", "tailscale", check=False, capture=True)
    ctl.env_set(TAILSCALE_DNSNAME="", TAILSCALE_AUTHKEY="")
    say("Tailscale désactivé (supprimez aussi la machine dans la console Tailscale)")
    return ctl.apply_addresses()


# --- Let's Encrypt par DNS --------------------------------------------------------------------
def dns_variables():
    try:
        return [l.split("=", 1)[0] for l in DNS_ENV.read_text().splitlines() if "=" in l]
    except OSError:
        return []


def dns_set(provider, variables_text):
    env = ctl.env_read()
    if env.get("DOMAIN_MODE") != "custom" or not env.get("ACME_EMAIL"):
        raise ctl.SeedboxError("configurez d'abord votre nom de domaine et l'e-mail Let's Encrypt (page Adresse)")
    if not PROVIDER.match(provider):
        raise ctl.SeedboxError("fournisseur invalide")
    lines = []
    for raw in variables_text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        name, _, value = raw.partition("=")
        name = name.strip()
        if not VARNAME.match(name) or not value or any(c in value for c in "\r\n\x00"):
            raise ctl.SeedboxError(f"variable invalide : {name or raw[:20]} (NOM_EN_MAJUSCULES=valeur)")
        lines.append(f"{name}={value.strip()}")
    if not lines:
        raise ctl.SeedboxError("renseignez au moins une variable d'accès au fournisseur")
    DNS_ENV.parent.mkdir(parents=True, exist_ok=True)
    DNS_ENV.write_text("\n".join(lines) + "\n")
    DNS_ENV.chmod(0o600)
    ctl.env_set(ACME_DNS_PROVIDER=provider)
    ctl.render_templates()
    ctl.compose("up", "-d", "traefik", capture=True)
    ctl.run("docker", "restart", "traefik", check=False, capture=True)
    say(f"Let's Encrypt par DNS ({provider}) activé : le certificat est demandé au prochain accès à {env.get('DOMAIN')}")


def dns_disable():
    ctl.env_set(ACME_DNS_PROVIDER="")
    DNS_ENV.write_text("")
    DNS_ENV.chmod(0o600)
    ctl.render_templates()
    ctl.run("docker", "restart", "traefik", check=False, capture=True)
    say("Let's Encrypt : retour au défi HTTP (port 80)")


# --- Seerr depuis Internet --------------------------------------------------------------------
def dns_lines():
    try:
        return [l for l in DNS_ENV.read_text().splitlines() if "=" in l]
    except OSError:
        return []


def write_dns_lines(lines):
    DNS_ENV.parent.mkdir(parents=True, exist_ok=True)
    DNS_ENV.write_text("".join(f"{l}\n" for l in lines))
    DNS_ENV.chmod(0o600)


def duckdns_token():
    return next((l.split("=", 1)[1] for l in dns_lines() if l.startswith("DUCKDNS_TOKEN=")), "")


def seerr_public_set(host, email, token=""):
    host = (host or "").strip().lower().rstrip(".")
    email = (email or "").strip()
    token = (token or "").strip()
    if not ctl.valid_domain(host):
        raise ctl.SeedboxError("nom public invalide (exemple : maseedbox.duckdns.org)")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ctl.SeedboxError("adresse e-mail invalide (Let's Encrypt l'exige pour le certificat)")
    if token and not TOKEN.match(token):
        raise ctl.SeedboxError("jeton DuckDNS invalide")
    if token and not DUCKDNS.match(host):
        raise ctl.SeedboxError("le jeton DuckDNS ne sert qu'avec un nom en .duckdns.org")
    if token:
        write_dns_lines([l for l in dns_lines() if not l.startswith("DUCKDNS_TOKEN=")] + [f"DUCKDNS_TOKEN={token}"])
    dns = bool(token or (DUCKDNS.match(host) and duckdns_token()))
    ctl.env_set(SEERR_PUBLIC_HOST=host, SEERR_PUBLIC_EMAIL=email, SEERR_PUBLIC_DNS="duckdns" if dns else "")
    ctl.render_templates()
    ctl.compose("up", "-d", "traefik", capture=True)
    ctl.run("docker", "restart", "traefik", check=False, capture=True)
    if dns:
        ddns_update(quiet=True)
    try:
        ctl.provision_seerr()        # liens des e-mails et notifications : la nouvelle adresse
    except (ctl.SeedboxError, OSError) as error:
        say(f"Seerr : adresse publique non enregistrée dans Seerr ({error})")
    say(f"Seerr depuis Internet : https://{host} ; redirigez le port 443 de la box vers le port 5055 de ce serveur"
        + ("" if dns else ", et le port 80 vers le port 80 (validation du certificat)"))


def seerr_public_disable():
    if ctl.env_read().get("SEERR_PUBLIC_DNS") == "duckdns" and not ctl.env_read().get("ACME_DNS_PROVIDER") == "duckdns":
        write_dns_lines([l for l in dns_lines() if not l.startswith("DUCKDNS_TOKEN=")])
    ctl.env_set(SEERR_PUBLIC_HOST="", SEERR_PUBLIC_EMAIL="", SEERR_PUBLIC_DNS="")
    ctl.render_templates()
    ctl.run("docker", "restart", "traefik", check=False, capture=True)
    try:
        ctl.provision_seerr()
    except (ctl.SeedboxError, OSError) as error:
        say(f"Seerr : adresse non remise à jour ({error})")
    say("Seerr depuis Internet : désactivé ; retirez la redirection de port de la box")


def ddns_get(url):
    """Appel DuckDNS ; point d'entrée unique (les tests le remplacent)."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read().decode(errors="replace").strip()


def ddns_update(quiet=False):
    """DuckDNS : l'adresse du nom suit l'adresse publique de la box (DuckDNS la lit sur la requête)."""
    host = ctl.env_read().get("SEERR_PUBLIC_HOST", "")
    match = DUCKDNS.match(host)
    token = duckdns_token()
    if not match or not token:
        return {"skipped": True}
    try:
        answer = ddns_get(f"https://www.duckdns.org/update?domains={match.group(1)}&token={token}&ip=")
    except OSError as error:
        answer = f"injoignable ({error})"
    state = {"ok": answer.startswith("OK"), "answer": answer[:60], "at": int(time.time())}
    try:
        DDNS_STATE.parent.mkdir(parents=True, exist_ok=True)
        DDNS_STATE.write_text(json.dumps(state))
    except OSError:
        pass
    if not quiet or not state["ok"]:
        say(f"DuckDNS {host} : {'adresse à jour' if state['ok'] else 'refusé : ' + state['answer']}")
    return state


def seerr_public():
    env = ctl.env_read()
    host = env.get("SEERR_PUBLIC_HOST", "")
    if not host:
        return {"enabled": False, "server": ctl.auto_address()}
    try:
        ddns = json.loads(DDNS_STATE.read_text())
    except (OSError, ValueError):
        ddns = None
    try:
        resolved = ctl.subdomains().resolve(host)
    except Exception:  # noqa: BLE001 - simple indication
        resolved = []
    return {"enabled": True, "host": host, "url": f"https://{host}", "email": env.get("SEERR_PUBLIC_EMAIL", ""),
            "dns_challenge": env.get("SEERR_PUBLIC_DNS") == "duckdns", "duckdns": bool(DUCKDNS.match(host)),
            "resolved": resolved, "ddns": ddns, "server": ctl.auto_address()}


def overview():
    env = ctl.env_read()
    return {"extra_hosts": env.get("EXTRA_HOSTS", ""), "tailscale": tailscale_status(),
            "dns": {"provider": env.get("ACME_DNS_PROVIDER", ""), "variables": dns_variables(),
                    "custom_domain": env.get("DOMAIN_MODE") == "custom" and bool(env.get("ACME_EMAIL")),
                    "providers": PROVIDERS},
            "seerr": seerr_public()}
