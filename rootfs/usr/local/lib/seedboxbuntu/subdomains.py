"""SeedBoxBuntu - sous-domaines : chaque application sur sa propre adresse (radarr.exemple.fr), derrière le Traefik de l'appliance.

Par défaut les applications sont servies par chemin sous une seule adresse (https://serveur/radarr/). Avec un domaine
d'applications (par exemple « exemple.fr »), chacune répond AUSSI sur radarr.exemple.fr, sonarr.exemple.fr... ; les chemins
actuels continuent de fonctionner.
Ce que le module fabrique :
  - un fichier Traefik (traefik/dynamic/subdomains.yml, relu à chaud) : un routeur par application et par nom. Les applications qui
    vivent sous un chemin de base (Radarr, Sonarr, Jellyfin...) reçoivent ce chemin ajouté AVANT Authelia, qui juge donc la même
    requête qu'avec l'adresse par chemin : aucune règle d'accès n'est contournée ;
  - côté Authelia : le domaine d'applications (*.exemple.fr) dans les règles d'accès et UN cookie de session sur le domaine parent,
    pour rester connecté d'une application à l'autre ; la page de connexion reste l'adresse principale (ou auth.exemple.fr si
    aucune adresse de l'appliance n'est sous ce domaine) ;
  - côté certificats : un nom générique (*.exemple.fr) dans le certificat de l'appliance ; avec Let's Encrypt, un certificat
    générique (défi DNS) ou un nom par application (défi HTTP) ;
  - la liste des noms à créer dans votre DNS, avec leur état (résolution comparée à l'adresse du serveur).
Limites assumées : les noms doivent exister dans votre DNS (un CNAME vers l'adresse principale suffit) ; avec Let's Encrypt en défi
HTTP, chaque nom doit être public et le port 80 ouvert ; les adresses internes que les applications s'échangent (Seerr vers Radarr...)
restent celles par chemin. Tableau de bord, console, Authelia et Traefik ne sont pas proposés en sous-domaine.
    seedboxctl subdomains show | check | set DOMAINE | off | rename ID NOM | enable ID | disable ID
"""
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor, wait

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "subdomains.json"
DYNAMIC_FILE = ctl.CONFIG / "traefik" / "dynamic" / "subdomains.yml"
LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$")
# « auth » sert de page de connexion quand aucune adresse de l'appliance n'est sous le domaine d'applications
RESERVED = {"auth", "www"}
PRIORITY_PATH, PRIORITY_HOST = 600, 500

# Applications de la stack proposées en sous-domaine. prefix : chemin de base de l'application (ajouté avant Authelia) ;
# strip : l'application n'a pas de chemin de base, Traefik le retire après Authelia ; auth : protégée par Authelia ;
# slash : /app redirigé vers /app/. Les valeurs reprennent celles des étiquettes de stack/docker-compose.yml.
STACK = {
    "jellyfin": {"service": "jellyfin", "prefix": "/jellyfin", "auth": False, "extra": []},
    "seerr": {"service": "seerr", "prefix": "", "auth": False, "extra": ["rate-limit@file"]},
    "p2p": {"service": "p2p", "prefix": "", "auth": False, "extra": ["rate-limit@file"]},
    "qbittorrent": {"service": "qbittorrent", "prefix": "/qbittorrent", "strip": True, "auth": True, "extra": []},
    "prowlarr": {"service": "prowlarr", "prefix": "/prowlarr", "auth": True, "extra": []},
    "jackett": {"service": "jackett", "prefix": "/jackett", "auth": True, "slash": True, "extra": []},
    "radarr": {"service": "radarr", "prefix": "/radarr", "auth": True, "extra": []},
    "sonarr": {"service": "sonarr", "prefix": "/sonarr", "auth": True, "extra": []},
    "autobrr": {"service": "autobrr", "prefix": "/autobrr", "auth": True, "slash": True, "extra": []},
    "qbm": {"service": "qbit-manage", "prefix": "/qbm", "auth": True, "slash": True, "extra": []},
    # Dockge n'a pas de chemin : la chaîne « authelia-dockge » présente à Authelia le chemin fictif /dockge/
    "dockge": {"service": "dockge", "prefix": "", "auth": False, "chain": "authelia-dockge@file", "extra": []},
}


def settings():
    """Réglages enregistrés : domaine d'applications, noms personnalisés, applications retirées."""
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    domain = stored.get("domain") if isinstance(stored.get("domain"), str) else ""
    names = stored.get("names") if isinstance(stored.get("names"), dict) else {}
    disabled = stored.get("disabled") if isinstance(stored.get("disabled"), list) else []
    return {"domain": domain if domain and ctl.valid_domain(domain) else "",
            "names": {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, str) and LABEL.match(v)},
            "disabled": [x for x in disabled if isinstance(x, str)]}


def save_settings(cfg):
    ctl.ETC.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def domain():
    """Domaine d'applications actif, ou chaîne vide."""
    return settings()["domain"]


def routes():
    """Applications joignables en sous-domaine : [{id, label, spec}], pile puis catalogue."""
    labels = {a["id"]: a["name"] for a in ctl.APPS}
    found = [{"id": ident, "label": labels.get(ident, ident), "spec": spec} for ident, spec in STACK.items()]
    try:
        entries = ctl.apps().installed_entries()
    except (OSError, ValueError, KeyError):
        entries = []
    for entry in entries:
        if entry.get("headless"):
            continue
        spec = {"service": f"app-{entry['id']}", "prefix": entry["path"].rstrip("/"), "auth": entry["auth"] == "sso",
                "strip": bool(entry.get("strip")), "slash": bool(entry.get("slash")), "extra": []}
        found.append({"id": entry["id"], "label": entry["name"], "spec": spec})
    return found


def host_label(ident, cfg=None):
    cfg = cfg or settings()
    return cfg["names"].get(ident, ident)


def portal(env=None):
    """Adresse de la page de connexion pour le domaine d'applications : la première adresse de l'appliance sous le domaine,
    sinon auth.<domaine>. Chaîne vide quand les sous-domaines sont désactivés."""
    base = domain()
    if not base:
        return ""
    env = env or ctl.env_read()
    for host in [h for h in env.get("PUBLIC_HOSTS", "").split(",") if h] + [env.get("DOMAIN", "")]:
        if host and under(host, base):
            return host
    return f"auth.{base}"


def under(host, base):
    return bool(base) and (host == base or host.endswith("." + base))


def hosts(env=None):
    """Noms servis : un par application active, plus la page de connexion si elle est sous auth.<domaine>."""
    base = domain()
    if not base:
        return []
    cfg = settings()
    names = [f"{host_label(r['id'], cfg)}.{base}" for r in routes() if r["id"] not in cfg["disabled"]]
    page = portal(env)
    if page and page not in names and page.startswith("auth."):
        names.append(page)
    return names


def cert_names():
    """Noms à ajouter au certificat de l'appliance."""
    base = domain()
    return [f"*.{base}"] if base else []


def acme_names(env):
    """Noms supplémentaires du certificat Let's Encrypt : générique avec un défi DNS, un par application avec le défi HTTP."""
    base = domain()
    if not base:
        return []
    if env.get("ACME_DNS_PROVIDER"):
        return [f"*.{base}"]
    return [h for h in hosts(env) if h != env.get("DOMAIN")]


def cookies(env_hosts, env=None):
    """Adresses qui portent leur propre cookie de session : celles hors du domaine d'applications ; le domaine reçoit le sien."""
    base = domain()
    return [h for h in env_hosts if not under(h, base)] if base else list(env_hosts)


def quote(text):
    return json.dumps(text, ensure_ascii=False)


def render_yaml(env=None):
    """Contenu du fichier de routes Traefik, ou chaîne vide quand les sous-domaines sont désactivés."""
    base = domain()
    if not base:
        return ""
    cfg = settings()
    middlewares, routers = [], []
    for route in routes():
        ident, spec = route["id"], route["spec"]
        if ident in cfg["disabled"]:
            continue
        host = f"{host_label(ident, cfg)}.{base}"
        prefix, strip = spec["prefix"], bool(spec.get("strip"))
        authn = [spec["chain"]] if spec.get("chain") else (["authelia@file"] if spec["auth"] else [])
        tail = [*spec["extra"], "secure-headers@file"]
        service = quote(f"{spec['service']}@docker")
        name = f"sub-{ident}"
        if prefix:
            middlewares.append(f"    {name}-prefix:\n      addPrefix:\n        prefix: {quote(prefix)}\n")
            if strip:
                middlewares.append(f"    {name}-strip:\n      stripPrefix:\n        prefixes:\n          - {quote(prefix)}\n")
            chain = [f"{name}-prefix", *authn, *([f"{name}-strip"] if strip else []), *tail]
            if not strip:
                # Adresse déjà sous le chemin de base (liens et redirections de l'application) : transmise telle quelle
                direct = [*(["slash@file"] if spec.get("slash") else []), *authn, *tail]
                routers.append(route_yaml(f"{name}-path", f"Host(`{host}`) && PathPrefix(`{prefix}`)", PRIORITY_PATH, direct, service))
        else:
            chain = [*authn, *tail]
        routers.append(route_yaml(name, f"Host(`{host}`)", PRIORITY_HOST, chain, service))
    return ("# Généré par seedboxctl (sous-domaines des applications) : ne pas modifier, réécrit à chaque changement.\n"
            "http:\n  middlewares:\n" + "".join(middlewares) + "  routers:\n" + "".join(routers))


def route_yaml(name, rule, priority, chain, service):
    return (f"    {name}:\n      rule: {quote(rule)}\n      priority: {priority}\n      entryPoints:\n        - websecure\n"
            f"      service: {service}\n      middlewares:\n" + "".join(f"        - {quote(m)}\n" for m in chain))


def render_file(env=None):
    """Écrit (ou retire) le fichier de routes ; relu à chaud par Traefik."""
    text = render_yaml(env)
    if not text:
        DYNAMIC_FILE.unlink(missing_ok=True)
        return False
    DYNAMIC_FILE.parent.mkdir(parents=True, exist_ok=True)
    DYNAMIC_FILE.write_text(text)
    return True


# ----------------------------------------------------------------------------
#  État, DNS
# ----------------------------------------------------------------------------
def resolve(name):
    """Adresses IPv4 d'un nom ; point d'entrée unique (les tests le remplacent)."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET)})
    except OSError:
        return []


def server_addresses(env):
    found = []
    for host in [*(h for h in env.get("PUBLIC_HOSTS", "").split(",") if h), env.get("DOMAIN", "")]:
        if ctl.is_ip(host):
            found.append(host)
        elif host:
            found += resolve(host)
    try:
        found.append(ctl.primary_ip())
    except OSError:
        pass
    return sorted({a for a in found if a and a != "127.0.0.1"})


def dns_states(names, env):
    """État DNS de chaque nom : ok (même adresse que le serveur), autre (autre adresse : NAT, mandataire...), absent."""
    mine = set(server_addresses(env))
    result = {}
    pool = ThreadPoolExecutor(max_workers=8)
    futures = {name: pool.submit(resolve, name) for name in names}
    wait(list(futures.values()), timeout=8)
    for name, future in futures.items():
        if not future.done():
            result[name] = {"state": "inconnu", "ips": []}
            continue
        ips = future.result()
        result[name] = {"state": "absent" if not ips else "ok" if mine & set(ips) else "autre", "ips": ips}
    pool.shutdown(wait=False, cancel_futures=True)
    return result


def suggestion(env):
    """Domaine d'applications probable : l'adresse principale sans son premier nom (vmdmz-seedbox01.ar-systems.fr -> ar-systems.fr)."""
    main = env.get("DOMAIN", "")
    if main and not ctl.is_ip(main) and main.count(".") >= 2:
        return main.split(".", 1)[1]
    return ""


def overview(check=True):
    env = ctl.env_read()
    cfg = settings()
    base = cfg["domain"]
    labels = {r["id"]: r for r in routes()}
    rows = []
    for ident, route in labels.items():
        host = f"{host_label(ident, cfg)}.{base}" if base else ""
        rows.append({"id": ident, "label": route["label"], "name": host_label(ident, cfg), "host": host,
                     "enabled": ident not in cfg["disabled"], "url": f"https://{host}/" if base else "",
                     "protected": bool(route["spec"]["auth"] or route["spec"].get("chain"))})
    page = portal(env)
    extra = []
    if base and page.startswith("auth."):
        extra.append({"id": "auth", "label": "Page de connexion", "name": "auth", "host": page, "enabled": True,
                      "url": f"https://{page}/auth/", "protected": False, "portal": True})
    if base and check:
        states = dns_states([r["host"] for r in [*rows, *extra] if r["enabled"]], env)
        for row in [*rows, *extra]:
            row.update(states.get(row["host"], {"state": "", "ips": []}))
    main = env.get("DOMAIN", "")
    acme = bool(env.get("ACME_EMAIL"))
    tls = "acme-dns" if acme and env.get("ACME_DNS_PROVIDER") else "acme-http" if acme else "local"
    warnings = []
    if base and tls == "acme-http":
        warnings.append("Let's Encrypt en défi HTTP : chaque nom doit être public et le port 80 ouvert depuis Internet ; "
                        "un défi DNS donnerait un seul certificat générique.")
    if base and tls == "local":
        warnings.append("Certificat de l'autorité locale : installez-la sur vos appareils (page Adresse) pour éviter l'alerte du navigateur.")
    if base and page.startswith("auth."):
        warnings.append(f"Aucune adresse de l'appliance n'est sous {base} : la page de connexion sera {page} (à créer dans le DNS).")
    return {"enabled": bool(base), "domain": base, "suggestion": suggestion(env), "main": main, "portal": page,
            "target": main if main and not ctl.is_ip(main) else (server_addresses(env) or [""])[0],
            "record": "A" if not main or ctl.is_ip(main) else "CNAME", "tls": tls, "apps": [*rows, *extra],
            "addresses": server_addresses(env), "warnings": warnings}


# ----------------------------------------------------------------------------
#  Changements
# ----------------------------------------------------------------------------
def check_domain(value):
    value = (value or "").strip().lower().rstrip(".")
    if not value:
        raise ctl.SeedboxError("domaine d'applications vide")
    if ctl.is_ip(value) or not ctl.valid_domain(value):
        raise ctl.SeedboxError("domaine invalide : un nom comme exemple.fr est attendu")
    return value


def check_name(ident, name):
    name = (name or "").strip().lower()
    if ident not in {r["id"] for r in routes()}:
        raise ctl.SeedboxError(f"application inconnue : {ident}")
    if not name:
        return ""
    if not LABEL.match(name):
        raise ctl.SeedboxError("nom invalide : lettres minuscules, chiffres et tirets (63 caractères au plus)")
    if name in RESERVED:
        raise ctl.SeedboxError(f"« {name} » est réservé")
    cfg = settings()
    for other in routes():
        if other["id"] != ident and host_label(other["id"], cfg) == name:
            raise ctl.SeedboxError(f"le nom « {name} » est déjà celui de {other['label']}")
    return name


def reload_front(env):
    """Les routes sont relues à chaud ; seul le certificat Let's Encrypt par nom (défi HTTP) demande de relancer Traefik."""
    if env.get("ACME_EMAIL") and not env.get("ACME_DNS_PROVIDER"):
        ctl.run("docker", "restart", "traefik", check=False, capture=True)


def apply_names():
    """Réécrit les routes après un changement de nom ou d'activation : sans redémarrage d'Authelia."""
    env = ctl.env_read()
    ctl.render_templates()
    ctl.set_ownership()
    reload_front(env)
    return overview(check=False)


def set_domain(value):
    """Active les sous-domaines sous ce domaine : certificat, cookies et règles d'Authelia, routes. Relance Traefik et Authelia."""
    value = check_domain(value)
    cfg = settings()
    cfg["domain"] = value
    save_settings(cfg)
    return ctl.apply_addresses()


def disable():
    cfg = settings()
    if not cfg["domain"]:
        return []
    cfg["domain"] = ""
    save_settings(cfg)
    return ctl.apply_addresses()


def rename(ident, name):
    name = check_name(ident, name)
    cfg = settings()
    if name and name != ident:
        cfg["names"][ident] = name
    else:
        cfg["names"].pop(ident, None)
    save_settings(cfg)
    return apply_names()


def set_enabled(ident, on):
    if ident not in {r["id"] for r in routes()}:
        raise ctl.SeedboxError(f"application inconnue : {ident}")
    cfg = settings()
    cfg["disabled"] = [x for x in cfg["disabled"] if x != ident]
    if not on:
        cfg["disabled"].append(ident)
    save_settings(cfg)
    return apply_names()
