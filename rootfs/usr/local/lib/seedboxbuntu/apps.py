"""SeedBoxBuntu - catalogue d'applications installables depuis la console.

Sources du catalogue
    intégré       stack/catalog.json, livré avec l'appliance : applications reliées de bout en bout
                  (Lidarr, Audiobookshelf, Navidrome...).
    catalogue     un fichier JSON publié par l'administrateur (adresse réglable : CATALOG_URL dans .env),
                  qui apporte de nouvelles applications sans toucher à l'appliance.
    linuxserver   la liste publique de linuxserver.io (https://api.linuxserver.io), proposée en mode
                  « générique » : routage par chemin et accès réservé aux administrateurs, sans liaison.

    « Actualiser » (seedboxctl apps refresh) relit les deux sources web et les garde dans
    /var/lib/seedboxbuntu/catalog-remote.json. L'intégré l'emporte toujours sur une source web.

Sécurité
    Une entrée venue du web ne peut déclarer que des champs connus, une image d'un registre
    courant, des volumes limités à CONFIG_DIR et DATA_DIR, et des variables prises dans une liste :
    ni port publié, ni mode privilégié, ni accès au socket Docker, ni secret de la stack.
    Rien n'est installé sans l'action d'un administrateur.

Installation
    Les applications installées sont décrites dans <base>/docker-compose.apps.yml, régénéré à chaque
    déploiement : elles survivent aux mises à jour. Traefik les découvre par leurs étiquettes ; Authelia
    protège celles qui n'ont pas de compte propre ; Homepage reçoit une carte.
"""
import json
import os
import re
import secrets
import time
import urllib.request
from pathlib import Path

import seedboxctl as ctl

APPS_COMPOSE = ctl.BASE / "docker-compose.apps.yml"
INSTALLED_FILE = ctl.STATE / "apps.json"
REMOTE_FILE = ctl.STATE / "catalog-remote.json"
LSIO_URL = "https://api.linuxserver.io/api/v1/images?include_config=true&include_deprecated=false"
MAX_DOWNLOAD = 12 * 1024 * 1024

# Services et chemins de la stack : une entrée du catalogue ne peut pas les prendre
RESERVED = {"authelia-redis", "socket-proxy", "traefik", "authelia", "homepage", "dockge", "jellyfin", "seerr", "radarr", "sonarr",
            "prowlarr", "jackett", "flaresolverr", "autobrr", "p2p-aggregator", "qbit-manage", "cross-seed",
            "qbittorrent", "qbittorrent-direct", "gluetun", "watchtower", "console", "auth", "qbm", "p2p"}
ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
IMAGE = re.compile(r"^(lscr\.io|ghcr\.io|docker\.io|quay\.io)/[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?$|"
                   r"^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*(:[A-Za-z0-9._-]+)?$")
VOLUME = re.compile(r"^\$\{(CONFIG_DIR|DATA_DIR)\}(/[A-Za-z0-9._-]+)*:(/[A-Za-z0-9._-]+)+(:ro)?$")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
SIMPLE_VARS = {"TZ", "PUID", "PGID", "UMASK", "DOMAIN", "CONFIG_DIR", "DATA_DIR"}
# clés des applications de la stack : seules les entrées livrées avec l'appliance peuvent les utiliser
STACK_KEYS = {"RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY"}
FIELDS = {"id", "name", "category", "description", "image", "port", "path", "auth", "access", "lsio", "user", "env",
          "volumes", "health", "secrets", "dirs", "integration", "icon", "widget", "generic", "strip", "slash",
          "source", "headless"}
TOOLS = {"curl", "wget"}


class CatalogError(ctl.SeedboxError):
    pass


# ----------------------------------------------------------------------------
#  Validation d'une entrée
# ----------------------------------------------------------------------------
def validate(entry, trusted=False):
    """Renvoie l'entrée nettoyée ou lève CatalogError. « trusted » : entrée livrée avec l'appliance."""
    if not isinstance(entry, dict):
        raise CatalogError("entrée illisible")
    unknown = set(entry) - (FIELDS - {"headless"})
    if unknown:
        raise CatalogError(f"champs inconnus : {', '.join(sorted(unknown))}")
    ident = entry.get("id", "")
    if not isinstance(ident, str) or not ID.match(ident):
        raise CatalogError(f"identifiant invalide : {ident!r}")
    if ident in RESERVED:
        raise CatalogError(f"{ident} : nom réservé")
    clean = {"id": ident}
    for key in ("name", "category", "description", "icon"):
        value = entry.get(key, "")
        if not isinstance(value, str) or len(value) > 400 or re.search(r"[\x00-\x1f]", value):
            raise CatalogError(f"{ident} : {key} invalide")
        clean[key] = value
    clean["name"] = clean["name"] or ident.capitalize()
    clean["category"] = clean["category"] or "Autre"
    clean["icon"] = clean["icon"] if re.fullmatch(r"[A-Za-z0-9._-]*", clean["icon"]) else ""
    image = entry.get("image", "")
    if not isinstance(image, str) or not IMAGE.match(image):
        raise CatalogError(f"{ident} : image non autorisée ({image!r})")
    clean["image"] = image
    port = entry.get("port")
    if port is None:                     # application sans interface web : ni routeur Traefik, ni carte Homepage
        if not trusted:
            raise CatalogError(f"{ident} : port manquant")
        clean["port"], clean["path"], clean["headless"] = None, "", True
    else:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise CatalogError(f"{ident} : port invalide")
        clean["port"], clean["headless"] = port, False
        path = entry.get("path") or f"/{ident}"
        if not isinstance(path, str) or not re.fullmatch(r"/[a-z0-9][a-z0-9-]{0,30}", path) or path[1:] in RESERVED:
            raise CatalogError(f"{ident} : chemin invalide")
        clean["path"] = path
    clean["auth"] = entry.get("auth", "sso")
    clean["access"] = entry.get("access", "admin")
    if clean["auth"] not in ("sso", "own") or clean["access"] not in ("admin", "user"):
        raise CatalogError(f"{ident} : auth ou access invalide")
    for flag in ("lsio", "user", "generic", "strip", "slash"):
        clean[flag] = bool(entry.get(flag, False))
    allowed_vars = SIMPLE_VARS | {f"{ident.upper().replace('-', '_')}_{suffix}" for suffix in ("API_KEY", "ADMIN_PASSWORD")}
    if trusted:
        allowed_vars |= STACK_KEYS
    env = entry.get("env") or {}
    if not isinstance(env, dict):
        raise CatalogError(f"{ident} : env invalide")
    for key, value in env.items():
        if not ENV_KEY.match(key) or not isinstance(value, str) or len(value) > 300 or re.search(r"[\x00-\x1f]", value):
            raise CatalogError(f"{ident} : variable {key!r} invalide")
        if set(VAR.findall(value)) - allowed_vars or "$" in VAR.sub("", value):
            raise CatalogError(f"{ident} : {key} utilise une substitution non autorisée")
    clean["env"] = env
    volumes = entry.get("volumes") or []
    if not isinstance(volumes, list) or len(volumes) > 12 or not all(isinstance(v, str) and VOLUME.match(v) and ".." not in v for v in volumes):
        raise CatalogError(f"{ident} : volumes non autorisés (seuls CONFIG_DIR et DATA_DIR sont permis)")
    clean["volumes"] = volumes
    health = entry.get("health")
    if health is not None and not clean["headless"]:
        if (not isinstance(health, dict) or health.get("tool") not in TOOLS
                or not re.fullmatch(r"/[A-Za-z0-9._/-]{0,100}", str(health.get("path", "")))):
            raise CatalogError(f"{ident} : contrôle d'état invalide")
        health = {"tool": health["tool"], "path": health["path"]}
    clean["health"] = health if not clean["headless"] else None
    secret_names = entry.get("secrets") or []
    if not isinstance(secret_names, list) or not all(s in {f"{ident.upper().replace('-', '_')}_API_KEY"} for s in secret_names):
        raise CatalogError(f"{ident} : secrets invalides")
    clean["secrets"] = secret_names
    dirs = entry.get("dirs") or []
    if not isinstance(dirs, list) or not all(isinstance(d, str) and re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", d) and ".." not in d for d in dirs):
        raise CatalogError(f"{ident} : dossiers invalides")
    clean["dirs"] = dirs
    widget = entry.get("widget")
    if widget is not None:
        if not isinstance(widget, dict) or not re.fullmatch(r"[a-z0-9]+", str(widget.get("type", ""))):
            raise CatalogError(f"{ident} : widget invalide")
        key = widget.get("key")
        if key is not None and key not in secret_names:
            raise CatalogError(f"{ident} : le widget ne peut utiliser qu'un des secrets de l'application")
        widget = {"type": widget["type"], **({"key": key} if key else {})}
    clean["widget"] = widget
    integration = entry.get("integration")
    if integration is not None and (not trusted or integration not in INTEGRATIONS):
        raise CatalogError(f"{ident} : liaison non disponible pour une entrée du web")
    clean["integration"] = integration
    return clean


# ----------------------------------------------------------------------------
#  Catalogue : intégré + web
# ----------------------------------------------------------------------------
def builtin():
    data = json.loads((ctl.SHARE / "stack" / "catalog.json").read_text())
    return {e["id"]: {**validate(e, trusted=True), "source": "intégré"} for e in data["apps"]}


def remote_read():
    try:
        return json.loads(REMOTE_FILE.read_text())
    except (OSError, ValueError):
        return {"fetched": None, "apps": [], "rejected": [], "sources": {}}


def catalog():
    """Toutes les applications connues, indexées par identifiant."""
    entries = {}
    for entry in remote_read().get("apps", []):
        entries[entry["id"]] = entry
    entries.update(builtin())
    return entries


def installed_read():
    try:
        data = json.loads(INSTALLED_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    return data.get("installed", {})


def installed_write(installed):
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    temp = INSTALLED_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"installed": installed}, indent=1, ensure_ascii=False) + "\n")
    os.replace(temp, INSTALLED_FILE)


def installed_entries():
    """Entrées des applications installées. L'entrée est gardée à l'installation : une application
    reste décrite même si le catalogue ne la propose plus."""
    return [e["entry"] for e in installed_read().values() if "entry" in e]


def fetch(url):
    if not url.startswith("https://"):
        raise CatalogError("l'adresse du catalogue doit commencer par https://")
    request = urllib.request.Request(url, headers={"User-Agent": "seedboxbuntu-catalog"})
    with urllib.request.urlopen(request, timeout=40) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise CatalogError("fichier trop volumineux")
    return json.loads(data)


def from_json(data):
    items = data.get("apps") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise CatalogError("liste d'applications attendue")
    good, rejected = [], []
    for item in items:
        try:
            good.append({**validate(item), "source": "catalogue"})
        except CatalogError as error:
            rejected.append(str(error))
    return good, rejected


def from_linuxserver(data):
    repos = (((data or {}).get("data") or {}).get("repositories") or {}).get("linuxserver") or []
    good = []
    for repo in repos:
        name = repo.get("name") or ""
        if repo.get("deprecated") or name.startswith(("baseimage", "docker-")) or name in RESERVED:
            continue
        ports = [p for p in ((repo.get("config") or {}).get("ports") or [])
                 if str(p.get("internal", "")).isdigit() and int(p["internal"]) not in (22, 53, 443, 8443, 3389, 3000 if name == "webtop" else 0)]
        if not ports:
            continue
        try:
            entry = validate({
                "id": name, "name": name, "category": (repo.get("category") or "Autre").split(",")[0].strip()[:40],
                "description": (repo.get("description") or "")[:300].replace("\n", " "),
                "image": f"lscr.io/linuxserver/{name}:latest", "port": int(ports[0]["internal"]),
                "generic": True, "strip": True, "lsio": True,
                "volumes": [f"${{CONFIG_DIR}}/{name}:/config", "${DATA_DIR}:/data"]})
        except CatalogError:
            continue
        good.append({**entry, "source": "linuxserver"})
    return good


def refresh():
    """Relit les sources web ; une source injoignable laisse la précédente en place."""
    env = ctl.env_read()
    result = {"fetched": int(time.time()), "apps": [], "rejected": [], "sources": {}}
    previous = remote_read()
    url = env.get("CATALOG_URL", "")
    if url:
        try:
            good, rejected = from_json(fetch(url))
            result["apps"] += good
            result["rejected"] += rejected
            result["sources"]["catalogue"] = f"{len(good)} application(s)"
            ctl.say(f"catalogue {url} : {len(good)} application(s), {len(rejected)} refusée(s)")
        except (OSError, ValueError, CatalogError) as error:
            ctl.say(f"catalogue {url} : {error}")
            result["sources"]["catalogue"] = f"échec : {error}"
            result["apps"] += [a for a in previous.get("apps", []) if a.get("source") == "catalogue"]
    if env.get("CATALOG_LINUXSERVER", "true") != "false":
        try:
            good = from_linuxserver(fetch(LSIO_URL))
            result["apps"] += good
            result["sources"]["linuxserver"] = f"{len(good)} application(s)"
            ctl.say(f"linuxserver.io : {len(good)} application(s)")
        except (OSError, ValueError, CatalogError) as error:
            ctl.say(f"linuxserver.io : {error}")
            result["sources"]["linuxserver"] = f"échec : {error}"
            result["apps"] += [a for a in previous.get("apps", []) if a.get("source") == "linuxserver"]
    seen, unique = set(), []
    for entry in result["apps"]:
        if entry["id"] not in seen:        # le catalogue de l'administrateur passe avant linuxserver.io
            seen.add(entry["id"])
            unique.append(entry)
    result["apps"] = unique
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    REMOTE_FILE.write_text(json.dumps(result, ensure_ascii=False))
    return result


def set_url(url):
    if url:
        if not re.fullmatch(r"https://[A-Za-z0-9._~:/?#\[\]@!&'()*+,;=%-]+", url):
            raise CatalogError("adresse invalide : https://... attendu")
    ctl.env_set(CATALOG_URL=url)
    ctl.say(f"adresse du catalogue : {url or 'aucune'}")


def listing():
    """Liste pour la console : une ligne par application, avec son état."""
    installed = installed_read()
    states = ctl.containers()
    rows = []
    rank = {"intégré": 0, "catalogue": 1}     # les applications reliées d'abord, puis le catalogue personnel
    for ident, entry in sorted(catalog().items(), key=lambda kv: (rank.get(kv[1]["source"], 2), kv[1]["category"], kv[1]["name"].lower())):
        state = states.get(ident, {})
        rows.append({
            "id": ident, "name": entry["name"], "category": entry["category"], "description": entry["description"],
            "source": entry["source"], "generic": entry["generic"], "auth": entry["auth"], "path": entry["path"],
            "installed": ident in installed, "state": state.get("state", ""), "health": state.get("health", ""),
            "integrated": bool(entry.get("integration")), "headless": bool(entry.get("headless")),
        })
    for ident, record in installed.items():       # installée mais retirée du catalogue
        if ident not in catalog() and "entry" in record:
            entry = record["entry"]
            rows.append({"id": ident, "name": entry["name"], "category": entry["category"],
                         "description": entry["description"], "source": entry.get("source", ""),
                         "generic": entry["generic"], "auth": entry["auth"], "path": entry["path"],
                         "installed": True, "state": states.get(ident, {}).get("state", ""),
                         "health": states.get(ident, {}).get("health", ""), "integrated": bool(entry.get("integration")),
                         "headless": bool(entry.get("headless"))})
    remote = remote_read()
    return {"apps": rows, "fetched": remote.get("fetched"), "sources": remote.get("sources", {}),
            "rejected": remote.get("rejected", []), "catalog_url": ctl.env_read().get("CATALOG_URL", ""),
            "packs": packs_listing(installed),
            "preset": (ctl.env_read().get("RECYCLARR_PRESET") or "1080p") if "recyclarr" in installed else ""}


# ----------------------------------------------------------------------------
#  Fichier compose des applications
# ----------------------------------------------------------------------------
def service_definition(entry):
    ident = entry["id"]
    router = f"app-{ident}"
    env = {}
    if entry["lsio"]:
        env.update({"PUID": "${PUID}", "PGID": "${PGID}", "TZ": "${TZ}", "UMASK": "${UMASK}"})
    else:
        env["TZ"] = "${TZ}"
    env.update(entry["env"])
    if entry.get("headless"):
        return {
            "image": entry["image"], "container_name": ident, "restart": "unless-stopped",
            "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
            "security_opt": ["no-new-privileges:true"], "environment": env, "volumes": entry["volumes"],
            "networks": ["seedbox"], **({"user": "${PUID}:${PGID}"} if entry["user"] else {}),
        }
    middlewares = []
    if entry["auth"] == "sso":
        middlewares.append("authelia@file")
    if entry["slash"]:
        middlewares.insert(0, "slash@file")
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.{router}.rule": f"PathPrefix(`{entry['path']}`)",
        f"traefik.http.routers.{router}.entrypoints": "websecure",
        f"traefik.http.services.{router}.loadbalancer.server.port": str(entry["port"]),
    }
    if entry["strip"]:
        labels[f"traefik.http.middlewares.{router}-strip.stripprefix.prefixes"] = entry["path"]
        middlewares.append(f"{router}-strip")
    middlewares.append("secure-headers@file")
    labels[f"traefik.http.routers.{router}.middlewares"] = ",".join(middlewares)
    service = {
        "image": entry["image"], "container_name": ident, "restart": "unless-stopped",
        "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
        "security_opt": ["no-new-privileges:true"], "environment": env, "volumes": entry["volumes"],
        "networks": ["seedbox"], "labels": labels,
    }
    if entry["user"]:
        service["user"] = "${PUID}:${PGID}"
    if entry["health"]:
        tool, url = entry["health"]["tool"], f"http://127.0.0.1:{entry['port']}{entry['health']['path']}"
        command = f"curl -fsS -o /dev/null {url}" if tool == "curl" else f"wget -q -O /dev/null {url}"
        service["healthcheck"] = {"test": ["CMD-SHELL", command], "interval": "30s", "timeout": "5s",
                                  "retries": 3, "start_period": "60s"}
    return service


def regenerate():
    """Écrit docker-compose.apps.yml d'après les applications installées (JSON est du YAML valide)."""
    entries = installed_entries()
    if not entries:
        APPS_COMPOSE.unlink(missing_ok=True)
        ctl.sync_compose_files()
        return entries
    document = {"services": {e["id"]: service_definition(e) for e in entries}}
    header = ("# Généré par seedboxctl (applications ajoutées depuis la console) : ne pas modifier.\n"
              "# Personnalisations : docker-compose.local.yml.\n")
    text = header + json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if not APPS_COMPOSE.exists() or APPS_COMPOSE.read_text() != text:
        APPS_COMPOSE.write_text(text)
    ctl.sync_compose_files()
    return entries


def routed(entry):
    """Vrai pour une application qui a une interface web derrière Traefik."""
    return not entry.get("headless")


def admin_paths():
    """Chemins des applications protégées par Authelia et réservées aux administrateurs."""
    return [e["path"].lstrip("/") for e in installed_entries() if routed(e) and e["auth"] == "sso" and e["access"] == "admin"]


def manager_paths():
    """Applications reliées de la seedbox (livrées avec l'appliance) ouvertes aux gestionnaires ; les
    applications génériques ou du web restent réservées aux administrateurs."""
    return [e["path"].lstrip("/") for e in installed_entries()
            if routed(e) and e["auth"] == "sso" and e["access"] == "admin" and e.get("integration") and not e.get("generic")]


def homepage_cards():
    """Groupe ajouté à la fin des services de Homepage."""
    entries = [e for e in installed_entries() if routed(e)]
    if not entries:
        return ""
    env = ctl.env_read()
    lines = ["", "- Applications ajoutées:"]
    for entry in entries:
        name = json.dumps(entry["name"], ensure_ascii=False)
        lines += [f"    - {name}:",
                  f"        icon: {entry['icon'] or 'mdi-application'}",
                  f"        href: \"{entry['path']}/\"",
                  f"        description: {json.dumps(entry['description'][:90], ensure_ascii=False)}",
                  f"        siteMonitor: http://{entry['id']}:{entry['port']}{entry['health']['path'] if entry['health'] else entry['path'] + '/'}"]
        widget = entry.get("widget")
        if widget and env.get(widget.get("key", ""), "x"):
            lines += ["        widget:", f"          type: {widget['type']}",
                      f"          url: http://{entry['id']}:{entry['port']}{entry['path']}"]
            if widget.get("key"):
                lines.append(f"          key: {json.dumps(env.get(widget['key'], ''))}")
    return "\n".join(lines) + "\n"


def apply_front(restart_authelia):
    """Réécrit Authelia et Homepage ; Authelia relit sa configuration au redémarrage."""
    ctl.render_templates()
    if restart_authelia:
        ctl.run("docker", "restart", "authelia", check=False, capture=True)
        try:
            ctl.wait_http("authelia", 9091, "/auth/api/health", timeout=90)
        except ctl.SeedboxError as error:
            ctl.say(f"Authelia : {error}")


# ----------------------------------------------------------------------------
#  Installation et retrait
# ----------------------------------------------------------------------------
def admin_login(entry, env):
    return "admin", env.get(f"{entry['id'].upper().replace('-', '_')}_ADMIN_PASSWORD", "")


def prepare_disk(entry, env):
    uid, gid = int(env["PUID"]), int(env["PGID"])
    config = ctl.CONFIG / entry["id"]
    config.mkdir(parents=True, exist_ok=True)
    os.chown(config, uid, gid)
    data = Path(env.get("DATA_DIR", str(ctl.STORAGE)))
    for relative in entry["dirs"]:
        target = data / relative
        target.mkdir(parents=True, exist_ok=True)
        for part in [target, *target.parents]:
            if part == data or data not in part.parents:
                break
            os.chown(part, uid, gid)
    for volume in entry["volumes"]:
        source = volume.split(":", 1)[0].replace("${CONFIG_DIR}", str(ctl.CONFIG)).replace("${DATA_DIR}", str(data))
        if str(source).startswith(str(ctl.CONFIG)):
            Path(source).mkdir(parents=True, exist_ok=True)
            os.chown(source, uid, gid)


def install(ident):
    entry = catalog().get(ident)
    if not entry:
        raise CatalogError(f"{ident} : application inconnue (actualisez le catalogue)")
    installed = installed_read()
    if ident in installed:
        raise CatalogError(f"{ident} est déjà installée")
    taken = {e["path"] for e in installed_entries() if routed(e)}
    if routed(entry) and entry["path"] in taken:
        raise CatalogError(f"le chemin {entry['path']} est déjà pris par une autre application")
    env = ctl.env_read()
    for name in entry["secrets"]:
        if not env.get(name):
            ctl.env_set(**{name: ctl.hex_secret(16)})
    if entry["auth"] == "own":
        key = f"{ident.upper().replace('-', '_')}_ADMIN_PASSWORD"
        if not env.get(key):
            ctl.env_set(**{key: ctl.human_password()})
    env = ctl.env_read()
    prepare_disk(entry, env)
    installed[ident] = {"entry": entry, "since": int(time.time()), "integrated": False}
    installed_write(installed)
    regenerate()
    ctl.say(f"{entry['name']} : téléchargement et démarrage")
    result = ctl.compose("up", "-d", ident, check=False, capture=True)
    if result.returncode != 0:
        remove(ident, purge=False, quiet=True)
        raise CatalogError(f"démarrage impossible : {(result.stderr or result.stdout or '').strip()[-400:]}")
    apply_front(restart_authelia=routed(entry) and entry["auth"] == "sso" and entry["access"] == "admin")
    hook = INTEGRATIONS.get(entry.get("integration"))
    if hook:
        try:
            hook(entry, ctl.env_read())
            installed = installed_read()
            installed[ident]["integrated"] = True
            installed_write(installed)
        except Exception as error:      # l'application tourne : la liaison se refait avec « apps install » après retrait
            ctl.say(f"{entry['name']} : liaison incomplète : {error}")
            raise CatalogError(f"{entry['name']} est démarrée mais sa liaison a échoué : {error}")
    else:
        ctl.say(f"{entry['name']} : démarrée (aucune liaison automatique, accès {entry['path']}/)")
    ctl.say(f"{entry['name']} : OK" + (f", accessible sous {entry['path']}/" if routed(entry) else " (service sans interface web)"))


def remove(ident, purge=False, quiet=False):
    installed = installed_read()
    record = installed.get(ident)
    if not record:
        raise CatalogError(f"{ident} n'est pas installée")
    entry = record["entry"]
    undo = UNDO.get(entry.get("integration"))
    if undo:
        try:
            undo(entry, ctl.env_read())
        except Exception as error:
            ctl.say(f"{entry['name']} : liaison non retirée : {error}")
    ctl.compose("rm", "-sf", ident, check=False, capture=True)
    del installed[ident]
    installed_write(installed)
    regenerate()
    apply_front(restart_authelia=routed(entry) and entry["auth"] == "sso" and entry["access"] == "admin")
    if purge:
        target = ctl.CONFIG / ident
        if target.is_dir() and target.resolve().parent == ctl.CONFIG.resolve():
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            ctl.say(f"{entry['name']} : configuration supprimée")
    if not quiet:
        ctl.say(f"{entry['name']} retirée" + ("" if purge else " (configuration conservée dans " + str(ctl.CONFIG / ident) + ")"))


def credentials():
    env = ctl.env_read()
    rows = {}
    for entry in installed_entries():
        row = {}
        for name in entry["secrets"]:
            row["clé API"] = env.get(name)
        if entry["auth"] == "own":
            row["identifiant"], row["mot de passe"] = admin_login(entry, env)
        if row:
            rows[entry["name"]] = row
    return rows


def check_entries():
    """Pour le contrôle de santé : applications installées."""
    return installed_entries()


# ----------------------------------------------------------------------------
#  Liaisons propres à chaque application
# ----------------------------------------------------------------------------
def jellyfin_library(name, kind, path):
    folders = {f["Name"]: f for f in ctl.jellyfin_api("GET", "/Library/VirtualFolders").json()}
    if name in folders:
        return
    ctl.say(f"Jellyfin : bibliothèque {name}")
    query = ctl.urllib.parse.urlencode({"name": name, "collectionType": kind, "refreshLibrary": "true"})
    result = ctl.jellyfin_api("POST", f"/Library/VirtualFolders?{query}", {
        "LibraryOptions": {"PathInfos": [{"Path": path}], "EnableRealtimeMonitor": True,
                           "PreferredMetadataLanguage": "fr", "MetadataCountryCode": "FR"}})
    if not result.ok:
        raise ctl.SeedboxError(f"Jellyfin : bibliothèque {name} refusée (HTTP {result.status})")


def integrate_lidarr(entry, env):
    ctl.wait_http("lidarr", 8686, "/lidarr/ping")
    folder = "/data/media/music"
    roots = {r["path"].rstrip("/") for r in ctl.arr_api("lidarr", "GET", "/rootfolder").json()}
    if folder not in roots:
        quality = ctl.arr_api("lidarr", "GET", "/qualityprofile").json()
        metadata = ctl.arr_api("lidarr", "GET", "/metadataprofile").json()
        result = ctl.arr_api("lidarr", "POST", "/rootfolder", {
            "name": "Musique", "path": folder, "defaultQualityProfileId": quality[0]["id"],
            "defaultMetadataProfileId": metadata[0]["id"], "defaultMonitorOption": "all",
            "defaultNewItemMonitorOption": "all", "defaultTags": []})
        if not result.ok:
            raise ctl.SeedboxError(f"Lidarr : dossier racine refusé {result.text[:200]}")
        ctl.say(f"Lidarr : dossier racine {folder}")
    ctl.arr_upsert("lidarr", "/downloadclient", "QBittorrent", "qBittorrent", {
        "host": ctl.qbit_host(), "port": 8080, "useSsl": False, "username": env["QBIT_USERNAME"],
        "password": env["QBIT_PASSWORD"], "musicCategory": "lidarr"}, {"enable": ctl.qbit_reachable()})
    ctl.arr_upsert("prowlarr", "/applications", "Lidarr", "Lidarr", {
        "prowlarrUrl": "http://prowlarr:9696/prowlarr", "baseUrl": "http://lidarr:8686/lidarr",
        "apiKey": env["LIDARR_API_KEY"]}, {"syncLevel": "fullSync"})
    jellyfin_library("Musique", "music", folder)
    ctl.say("Lidarr : relié à Prowlarr, qBittorrent et Jellyfin")


def undo_lidarr(entry, env):
    for item in ctl.arr_api("prowlarr", "GET", "/applications").json():
        if item["name"] == "Lidarr":
            ctl.arr_api("prowlarr", "DELETE", f"/applications/{item['id']}")
            ctl.say("Prowlarr : liaison Lidarr retirée")


def integrate_audiobookshelf(entry, env):
    base = ctl.svc("audiobookshelf", 80) + entry["path"]
    user, password = admin_login(entry, env)
    ctl.wait_http("audiobookshelf", 80, entry["path"] + "/healthcheck")
    status = ctl.request("GET", base + "/status", timeout=15).json() or {}
    if not status.get("isInit"):
        result = ctl.request("POST", base + "/init", body={"newRoot": {"username": user, "password": password}}, timeout=30)
        if not result.ok:
            raise ctl.SeedboxError(f"Audiobookshelf : compte refusé (HTTP {result.status}) {result.text[:160]}")
        ctl.say(f"Audiobookshelf : compte {user} créé")
    login = ctl.request("POST", base + "/login", body={"username": user, "password": password}, timeout=30)
    if not login.ok:
        ctl.say("Audiobookshelf : compte déjà créé avec un autre mot de passe ; bibliothèque à ajouter dans l'interface")
        return
    token = login.json()["user"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    libraries = ctl.request("GET", base + "/api/libraries", headers=headers, timeout=30).json().get("libraries", [])
    if not libraries:
        result = ctl.request("POST", base + "/api/libraries", headers=headers, timeout=30, body={
            "name": "Livres audio", "folders": [{"fullPath": "/audiobooks"}], "mediaType": "book", "icon": "audiobookshelf"})
        if not result.ok:
            raise ctl.SeedboxError(f"Audiobookshelf : bibliothèque refusée (HTTP {result.status}) {result.text[:160]}")
        ctl.say("Audiobookshelf : bibliothèque Livres audio (/audiobooks)")


def integrate_navidrome(entry, env):
    base = ctl.svc("navidrome", 4533) + entry["path"]
    user, password = admin_login(entry, env)
    ctl.wait_http("navidrome", 4533, entry["path"] + "/ping")
    result = ctl.request("POST", base + "/auth/createAdmin", body={"username": user, "password": password}, timeout=30)
    if result.ok:
        ctl.say(f"Navidrome : compte {user} créé")
    elif result.status in (403, 400, 409):
        ctl.say("Navidrome : compte administrateur déjà créé")
    else:
        raise ctl.SeedboxError(f"Navidrome : compte refusé (HTTP {result.status}) {result.text[:160]}")


# --- Recyclarr : profils de qualité et formats personnalisés (guides TRaSH) pour Radarr et Sonarr ---------------
RECYCLARR_MARK = "# seedboxbuntu-managed"
RECYCLARR_PRESETS = {
    "1080p": {"radarr": ("radarr-quality-definition-movie", "radarr-quality-profile-hd-bluray-web", "radarr-custom-formats-hd-bluray-web"),
              "sonarr": ("sonarr-quality-definition-series", "sonarr-v4-quality-profile-web-1080p", "sonarr-v4-custom-formats-web-1080p")},
    "2160p": {"radarr": ("radarr-quality-definition-movie", "radarr-quality-profile-uhd-bluray-web", "radarr-custom-formats-uhd-bluray-web"),
              "sonarr": ("sonarr-quality-definition-series", "sonarr-v4-quality-profile-web-2160p", "sonarr-v4-custom-formats-web-2160p")},
}


def recyclarr_config(preset):
    """Fichier recyclarr.yml : les clés API restent dans l'environnement du conteneur (!env_var)."""
    lines = [RECYCLARR_MARK + f" (préréglage {preset}) : modifié par « seedboxctl apps preset » ;",
             "# supprimez cette première ligne pour garder votre propre configuration.",
             "radarr:", "  radarr:", "    base_url: http://radarr:7878/radarr", "    api_key: !env_var RADARR_API_KEY", "    include:"]
    lines += [f"      - template: {name}" for name in RECYCLARR_PRESETS[preset]["radarr"]]
    lines += ["sonarr:", "  sonarr:", "    base_url: http://sonarr:8989/sonarr", "    api_key: !env_var SONARR_API_KEY", "    include:"]
    lines += [f"      - template: {name}" for name in RECYCLARR_PRESETS[preset]["sonarr"]]
    return "\n".join(lines) + "\n"


def recyclarr_sync():
    result = ctl.run("timeout", "420", "docker", "exec", "recyclarr", "recyclarr", "sync", check=False, capture=True)
    if result.returncode != 0:
        # GitHub injoignable ou guide modifié : la synchronisation quotidienne du conteneur réessaiera
        ctl.say("Recyclarr : synchronisation à refaire plus tard : " + ((result.stderr or result.stdout or "").strip()[-300:] or "échec"))
        return False
    ctl.say("Recyclarr : profils et formats personnalisés synchronisés avec Radarr et Sonarr")
    return True


def integrate_recyclarr(entry, env):
    preset = env.get("RECYCLARR_PRESET") or "1080p"
    if preset not in RECYCLARR_PRESETS:
        preset = "1080p"
    config = ctl.CONFIG / "recyclarr" / "recyclarr.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    managed = not config.exists() or config.read_text().startswith(RECYCLARR_MARK)
    if managed:
        text = recyclarr_config(preset)
        if not config.exists() or config.read_text() != text:
            config.write_text(text)
            os.chown(config, int(env["PUID"]), int(env["PGID"]))
            ctl.say(f"Recyclarr : configuration {preset} écrite")
        ctl.env_set(RECYCLARR_PRESET=preset)
    else:
        ctl.say("Recyclarr : configuration personnalisée conservée")
    recyclarr_sync()


def set_preset(value=None):
    """Préréglage de qualité de Recyclarr ; sans valeur, affiche le préréglage actuel."""
    env = ctl.env_read()
    if value is None:
        return env.get("RECYCLARR_PRESET") or "1080p"
    if value not in RECYCLARR_PRESETS:
        raise CatalogError("préréglage inconnu : " + ", ".join(RECYCLARR_PRESETS))
    if "recyclarr" not in installed_read():
        raise CatalogError("Recyclarr n'est pas installé")
    config = ctl.CONFIG / "recyclarr" / "recyclarr.yml"
    if config.exists() and not config.read_text().startswith(RECYCLARR_MARK):
        raise CatalogError("la configuration de Recyclarr est personnalisée (première ligne supprimée) : modifiez-la directement")
    ctl.env_set(RECYCLARR_PRESET=value)
    integrate_recyclarr(installed_read()["recyclarr"]["entry"], ctl.env_read())


# --- Bazarr : sous-titres, relié à Radarr et Sonarr ---------------------------------------------------------------
def yaml_section_set(text, section, values):
    """Fixe des clés simples dans une section de premier niveau d'un YAML à deux niveaux (config.yaml de
    Bazarr). Les valeurs sont déjà au format YAML ; les autres lignes ne sont pas touchées."""
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.rstrip() == f"{section}:"), None)
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(f"{section}:")
        start = len(lines) - 1
    end = start + 1
    while end < len(lines) and (lines[end].startswith(" ") or not lines[end].strip()):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1                      # les lignes vides finales n'appartiennent pas à la section
    for key, value in values.items():
        pattern = re.compile(rf"^  {re.escape(key)}:")
        for index in range(start + 1, end):
            if pattern.match(lines[index]):
                lines[index] = f"  {key}: {value}"
                break
        else:
            lines.insert(end, f"  {key}: {value}")
            end += 1
    return "\n".join(lines)


def bazarr_config_text(text, env):
    text = yaml_section_set(text, "general", {"base_url": "/bazarr", "use_radarr": "true", "use_sonarr": "true"})
    text = yaml_section_set(text, "radarr", {"ip": "radarr", "port": "7878", "base_url": "/radarr", "ssl": "false",
                                             "apikey": json.dumps(env["RADARR_API_KEY"])})
    return yaml_section_set(text, "sonarr", {"ip": "sonarr", "port": "8989", "base_url": "/sonarr", "ssl": "false",
                                             "apikey": json.dumps(env["SONARR_API_KEY"])})


def integrate_bazarr(entry, env):
    config = ctl.CONFIG / "bazarr" / "config" / "config.yaml"
    deadline = time.time() + 180
    while not config.exists():             # créé par Bazarr à son premier démarrage
        if time.time() > deadline:
            raise ctl.SeedboxError("Bazarr n'a pas créé sa configuration")
        time.sleep(3)
    time.sleep(3)
    current = config.read_text()
    wanted = bazarr_config_text(current, env)
    if wanted == current:
        return
    # Bazarr réécrit sa configuration quand il tourne : on l'arrête le temps de la modifier
    ctl.run("docker", "stop", "-t", "20", "bazarr", check=False, capture=True)
    config.write_text(wanted)
    os.chown(config, int(env["PUID"]), int(env["PGID"]))
    ctl.run("docker", "start", "bazarr", capture=True)
    ctl.wait_http("bazarr", 6767, "/bazarr/", timeout=120)
    ctl.say("Bazarr : relié à Radarr et Sonarr (choisissez vos langues de sous-titres dans Réglages > Langues)")


INTEGRATIONS = {"lidarr": integrate_lidarr, "audiobookshelf": integrate_audiobookshelf, "navidrome": integrate_navidrome,
                "recyclarr": integrate_recyclarr, "bazarr": integrate_bazarr}
UNDO = {"lidarr": undo_lidarr}


# ----------------------------------------------------------------------------
#  Packs : plusieurs applications installées d'un seul geste
# ----------------------------------------------------------------------------
def packs_read():
    try:
        data = json.loads((ctl.SHARE / "stack" / "catalog.json").read_text())
    except (OSError, ValueError):
        return []
    known = builtin()
    packs = []
    for pack in data.get("packs", []):
        members = [a for a in pack.get("apps", []) if a in known]
        if ID.match(pack.get("id", "")) and members:
            packs.append({"id": pack["id"], "name": pack.get("name", pack["id"]), "description": pack.get("description", ""), "apps": members})
    return packs


def packs_listing(installed=None):
    installed = installed_read() if installed is None else installed
    known = builtin()
    rows = []
    for pack in packs_read():
        members = [{"id": a, "name": known[a]["name"], "installed": a in installed} for a in pack["apps"]]
        rows.append({**pack, "apps": members, "complete": all(m["installed"] for m in members)})
    return rows


def install_pack(pack_id):
    """Installe les applications du pack qui manquent ; une application en échec n'arrête pas les autres."""
    pack = next((p for p in packs_read() if p["id"] == pack_id), None)
    if not pack:
        raise CatalogError(f"{pack_id} : pack inconnu")
    ctl.say(f"Pack {pack['name']} : {', '.join(pack['apps'])}")
    failures = []
    for ident in pack["apps"]:
        if ident in installed_read():
            ctl.say(f"{ident} : déjà installée")
            continue
        try:
            install(ident)
        except ctl.SeedboxError as error:
            ctl.say(f"{ident} : ÉCHEC : {error}")
            failures.append(f"{ident} ({error})")
    if failures:
        raise CatalogError("pack incomplet : " + " ; ".join(failures))
    ctl.say(f"Pack {pack['name']} : installé")
