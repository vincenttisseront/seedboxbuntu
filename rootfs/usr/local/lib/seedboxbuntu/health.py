"""SeedBoxBuntu - contrôle de santé : chaque application répond-elle, et les liaisons entre
applications (API, clés, clients de téléchargement) sont-elles en place ?

Ce module n'écrit rien : il interroge les applications comme le font Homepage, Radarr, Sonarr,
Seerr, autobrr... et signale ce qui ne répond pas.

    seedboxctl check          liste lisible, code de sortie 1 s'il y a un échec
    seedboxctl check --json   même résultat pour la console web
"""
import http.client
import json
import re
import shutil

import seedboxctl as ctl

OK, WARN, FAIL = "ok", "warn", "fail"


class Report:
    def __init__(self):
        self.items = []

    def add(self, group, label, status, detail=""):
        self.items.append({"group": group, "label": label, "status": status, "detail": str(detail)[:240]})
        return status == OK

    def attempt(self, group, label, probe, failure=FAIL):
        """probe() renvoie un détail (succès) ou lève une exception (échec)."""
        try:
            return self.add(group, label, OK, probe() or "")
        except (ctl.SeedboxError, OSError, ValueError, KeyError, IndexError, TypeError,
                http.client.HTTPException) as error:
            return self.add(group, label, failure, error)


def expect(response, what="réponse"):
    if not response.ok:
        raise ctl.SeedboxError(f"{what} : HTTP {response.status} {response.text[:120]}")
    return response


def expected_services(env):
    """Conteneurs attendus selon le mode de qBittorrent."""
    mode = ctl.qbit_mode()
    services = ["socket-proxy", "traefik", "authelia-redis", "authelia", "homepage", "dockge", "jellyfin", "seerr", "radarr",
                "sonarr", "prowlarr", "jackett", "flaresolverr", "autobrr", "p2p-aggregator", "qbit-manage", "cross-seed", "qbittorrent"]
    if mode == "vpn":
        services.append("gluetun")
    if "autoupdate" in ctl.profiles_read():
        services.append("watchtower")
    if "tailscale" in ctl.profiles_read():
        services.append("tailscale")
    services += [e["id"] for e in ctl.apps().installed_entries()]
    return services


def check_containers(report, env):
    states = ctl.containers()
    for name in expected_services(env):
        state = states.get(name)
        label = f"conteneur {name}"
        if not state:
            report.add("Conteneurs", label, FAIL, "absent")
        elif state["state"] != "running":
            report.add("Conteneurs", label, FAIL, state["state"])
        elif state["health"] in ("unhealthy", "starting"):
            report.add("Conteneurs", label, WARN if state["health"] == "starting" else FAIL, state["health"])
        else:
            report.add("Conteneurs", label, OK, state["health"] or "en marche")


def arr_checks(report, env, name, label):
    group = label
    port = {"radarr": 7878, "sonarr": 8989, "prowlarr": 9696}[name]
    report.attempt(group, "API (clé)", lambda: "version " + str(expect(ctl.arr_api(name, "GET", "/system/status")).json()["version"]))

    def health():
        issues = [i for i in expect(ctl.arr_api(name, "GET", "/health")).json() if i.get("type") in ("error", "warning")]
        if issues:
            raise ctl.SeedboxError("; ".join(f"{i['source']}: {i['message']}"[:110] for i in issues[:3]))
        return "aucun problème signalé"
    # Les alertes d'une application (indexeur absent, liste vide...) n'empêchent pas son travail
    report.attempt(group, "état interne", health, failure=WARN)
    if name == "prowlarr":
        def applications():
            apps = {a["name"]: a for a in expect(ctl.arr_api(name, "GET", "/applications")).json()}
            missing = [n for n in ("Radarr", "Sonarr") if n not in apps]
            if missing:
                raise ctl.SeedboxError("non reliée à " + ", ".join(missing))
            return "reliée à Radarr et Sonarr (synchronisation complète)"
        report.attempt(group, "liaison avec Radarr et Sonarr", applications)

        def flaresolverr_proxy():
            proxies = [p for p in expect(ctl.arr_api(name, "GET", "/indexerproxy")).json() if p["implementation"] == "FlareSolverr"]
            if not proxies:
                raise ctl.SeedboxError("proxy FlareSolverr absent")
            tags = {tag["id"]: tag["label"] for tag in expect(ctl.arr_api(name, "GET", "/tag")).json()}
            if "flaresolverr" not in tags.values():
                raise ctl.SeedboxError("étiquette « flaresolverr » absente")
            for attempt in range(3):          # FlareSolverr peut être lent juste après son démarrage
                try:
                    test = ctl.arr_api(name, "POST", "/indexerproxy/test", proxies[0])
                except OSError as error:
                    test = ctl.Response(0, str(error), [])
                if test.ok:
                    break
                if attempt < 2:
                    ctl.time.sleep(10)
            if not test.ok:
                raise ctl.SeedboxError("test refusé : " + test.text[:140])
            return "proxy FlareSolverr : test réussi (étiquette « flaresolverr » à poser sur un indexeur)"
        report.attempt(group, "proxy FlareSolverr", flaresolverr_proxy)
        return

    def download_client():
        clients = [c for c in expect(ctl.arr_api(name, "GET", "/downloadclient")).json() if c["implementation"] == "QBittorrent"]
        if not clients:
            raise ctl.SeedboxError("aucun client qBittorrent")
        fields = {f["name"]: f.get("value") for f in clients[0]["fields"]}
        if fields.get("host") != ctl.qbit_host():
            raise ctl.SeedboxError(f"client sur {fields.get('host')}, attendu {ctl.qbit_host()}")
        if not clients[0].get("enable"):
            raise ctl.SeedboxError("client désactivé")
        test = ctl.arr_api(name, "POST", "/downloadclient/test", clients[0])
        if not test.ok:
            raise ctl.SeedboxError("test refusé : " + test.text[:140])
        return f"qBittorrent ({ctl.qbit_host()}:8080) : test réussi"
    report.attempt(group, "client de téléchargement qBittorrent", download_client)

    def jellyfin_link():
        notes = [n for n in expect(ctl.arr_api(name, "GET", "/notification")).json() if n["implementation"] == "MediaBrowser"]
        if not notes:
            raise ctl.SeedboxError("notification Jellyfin absente")
        return "notifie Jellyfin à chaque import"
    report.attempt(group, "liaison avec Jellyfin", jellyfin_link)

    def indexers():
        count = len(expect(ctl.arr_api(name, "GET", "/indexer")).json())
        if not count:
            raise ctl.SeedboxError("aucun indexeur : ajoutez-en dans Prowlarr, ils sont synchronisés ici")
        return f"{count} indexeur(s) reçu(s) de Prowlarr"
    report.attempt(group, "indexeurs", indexers, failure=WARN)


def check_jellyfin(report, env):
    def info():
        data = expect(ctl.jellyfin_api("GET", "/System/Info")).json()
        return f"{data.get('ServerName')} {data.get('Version')}"
    report.attempt("Jellyfin", "API (clé)", info)

    def libraries():
        names = [f["Name"] for f in expect(ctl.jellyfin_api("GET", "/Library/VirtualFolders")).json()]
        if not names:
            raise ctl.SeedboxError("aucune bibliothèque")
        return ", ".join(names)
    report.attempt("Jellyfin", "bibliothèques", libraries)


def check_seerr(report, env):
    report.attempt("Seerr", "API (clé)", lambda: "version " + str(expect(ctl.seerr_api("GET", "/status")).json()["version"]))

    def initialized():
        if not expect(ctl.seerr_api("GET", "/settings/public")).json().get("initialized"):
            raise ctl.SeedboxError("assistant initial non terminé")
        return "configuré, relié à Jellyfin"
    report.attempt("Seerr", "configuration initiale", initialized)
    for service, label in (("radarr", "Radarr"), ("sonarr", "Sonarr")):
        def servers(service=service):
            found = expect(ctl.seerr_api("GET", f"/settings/{service}")).json()
            if not found:
                raise ctl.SeedboxError(f"aucun serveur {label} déclaré")
            return f"{found[0].get('hostname')}:{found[0].get('port')}"
        report.attempt("Seerr", f"liaison avec {label}", servers)


def check_qbittorrent(report, env):
    host = ctl.qbit_host()

    def login():
        cookie = ctl.qbit_login()
        base = ctl.svc(host, 8080) + "/api/v2"
        version = expect(ctl.request("GET", base + "/app/version", headers={"Cookie": cookie}, timeout=15)).text
        return f"{version} sur {host}:8080"
    report.attempt("qBittorrent", "API (identifiants)", login)

    def binding():
        cookie = ctl.qbit_login()
        prefs = expect(ctl.request("GET", ctl.svc(host, 8080) + "/api/v2/app/preferences",
                                   headers={"Cookie": cookie}, timeout=15)).json()
        interface = prefs.get("current_network_interface", "")
        if ctl.qbit_mode() == "vpn":
            if interface != "tun0":
                raise ctl.SeedboxError(f"interface « {interface or 'toutes'} » : le trafic doit être lié au tunnel (tun0)")
            return "lié au tunnel VPN (tun0)"
        if interface:
            raise ctl.SeedboxError(f"interface « {interface} » imposée en accès direct : aucun trafic ne sortirait")
        return f"accès direct, port de partage {prefs.get('listen_port')}"
    report.attempt("qBittorrent", "interface réseau selon le mode", binding)

    if ctl.qbit_mode() == "vpn":
        def tunnel():
            address = ctl.public_ip()
            if not address:
                raise ctl.SeedboxError("le tunnel ne transporte pas de trafic")
            return f"adresse de sortie {address}"
        # Alerte et non échec : l'adresse de sortie vient d'un service Internet tiers
        report.attempt("qBittorrent", "tunnel VPN", tunnel, failure=WARN)


def check_autobrr(report, env):
    def downloader():
        cookie = ctl.autobrr_session()
        found = [c for c in expect(ctl.autobrr_api("GET", "/downloaders", cookie=cookie)).json() or []
                 if c["name"] == "qBittorrent"]
        if not found:
            raise ctl.SeedboxError("client qBittorrent absent")
        if ctl.qbit_host() not in found[0].get("host", ""):
            raise ctl.SeedboxError(f"client sur {found[0].get('host')}, attendu {ctl.qbit_host()}")
        test = ctl.autobrr_api("POST", "/downloaders/test", found[0], cookie=cookie)
        if not test.ok:
            raise ctl.SeedboxError("test refusé : " + test.text[:140])
        return f"qBittorrent ({found[0].get('host')}) : test réussi"
    report.attempt("autobrr", "client de téléchargement qBittorrent", downloader)


def check_p2p(report, env):
    report.attempt("P2P Aggregator", "API", lambda: expect(ctl.request(
        "GET", ctl.svc("p2p-aggregator", 3000) + "/api/health", timeout=15)).text[:60])

    def client():
        cookie = ctl.p2p_session()
        clients = expect(ctl.p2p_api("GET", "/api/user/download-clients", cookie=cookie)).json()
        clients = clients.get("items", clients.get("clients", [])) if isinstance(clients, dict) else clients
        found = [c for c in clients if c.get("name") == "qBittorrent"]
        if not found:
            raise ctl.SeedboxError("client qBittorrent absent")
        if ctl.qbit_host() not in str(found[0].get("baseUrl", "")):
            raise ctl.SeedboxError(f"client sur {found[0].get('baseUrl')}, attendu {ctl.qbit_host()}")
        return f"qBittorrent ({found[0].get('baseUrl')})"
    report.attempt("P2P Aggregator", "client de téléchargement qBittorrent", client)


def check_qbm_and_cross_seed(report, env):
    host = ctl.qbit_host()

    def qbm():
        text = (ctl.CONFIG / "qbit-manage" / "config.yml").read_text()
        # qbit_manage réécrit son fichier : guillemets facultatifs
        if not re.search(rf'^\s*host:\s*"?{host}:8080"?\s*$', text, re.M):
            raise ctl.SeedboxError(f"configuration sur une autre adresse que {host}:8080")
        response = ctl.request("GET", ctl.svc("qbit-manage", 8181) + "/qbm/api/health", timeout=15)
        if response.status >= 400 and ctl.request("GET", ctl.svc("qbit-manage", 8181) + "/qbm/", timeout=15).status >= 400:
            raise ctl.SeedboxError("interface web injoignable")
        return f"interface sous /qbm, qBittorrent sur {host}:8080"
    report.attempt("qbit_manage", "interface et liaison avec qBittorrent", qbm)

    def cross_seed():
        text = (ctl.CONFIG / "cross-seed" / "config.js").read_text()
        if f"@{host}:8080" not in text:
            raise ctl.SeedboxError(f"configuration sur une autre adresse que {host}:8080")
        return f"qBittorrent sur {host}:8080"
    report.attempt("cross-seed", "liaison avec qBittorrent", cross_seed)


def check_front(report, env):
    report.attempt("Accès", "Authelia (connexion unique)", lambda: expect(ctl.request(
        "GET", ctl.svc("authelia", 9091) + "/auth/api/health", timeout=15)).text[:60])
    report.attempt("Accès", "Homepage", lambda: f"HTTP {expect(ctl.request('GET', ctl.svc('homepage', 3000) + '/api/services', headers={'Host': env.get('DOMAIN', 'localhost')}, timeout=20)).status}")

    def dockge():
        stacks = ctl.Path(env.get("STACKS_DIR", ""))
        if not (stacks / ctl.BASE.name / "docker-compose.yml").exists():
            raise ctl.SeedboxError(f"la pile seedbox n'est pas dans {stacks} : Dockge ne peut pas la gérer")
        mounted = ctl.run("docker", "inspect", "-f", "{{range .Mounts}}{{.Destination}} {{end}}", "dockge",
                          check=False, capture=True).stdout.split()
        if str(stacks) not in mounted:
            raise ctl.SeedboxError(f"Dockge n'a pas {stacks} : recréez-le (seedboxctl restart)")
        return f"Dockge gère la pile seedbox ({stacks}/{ctl.BASE.name})"
    report.attempt("Accès", "Dockge gère la pile seedbox", dockge)


def check_jackett(report, env):
    base = ctl.BASE_PATHS["jackett"]
    group = "Jackett et FlareSolverr"

    def config():
        result = ctl.jackett_api("GET", "/server/config")
        data = expect(result).json()
        if data.get("api_key") != env.get("JACKETT_API_KEY"):
            raise ctl.SeedboxError("clé API différente de .env")
        if data.get("flaresolverrurl") != ctl.FLARESOLVERR_URL:
            raise ctl.SeedboxError(f"FlareSolverr non renseigné ({data.get('flaresolverrurl') or 'vide'})")
        return "clé API et FlareSolverr renseignés"
    report.attempt(group, "Jackett : configuration", config)

    def flaresolverr():
        data = expect(ctl.request("GET", ctl.svc("flaresolverr", 8191) + "/", timeout=15)).json()
        return f"FlareSolverr {data.get('version', '?')} : {data.get('msg', 'prêt')}"
    report.attempt(group, "FlareSolverr", flaresolverr)

    def indexers():
        configured = expect(ctl.jackett_api("GET", "/indexers?configured=true")).json() or []
        if not configured:
            return "aucun indexeur configuré (à ajouter dans Jackett, ils seront repris par Prowlarr)"
        names = {i["name"] for i in expect(ctl.arr_api("prowlarr", "GET", "/indexer")).json()}
        missing = [i.get("name") or i["id"] for i in configured if f"Jackett : {i.get('name') or i['id']}" not in names]
        if missing:
            raise ctl.SeedboxError("pas encore dans Prowlarr : " + ", ".join(missing[:4]))
        return f"{len(configured)} indexeur(s) repris par Prowlarr"
    report.attempt(group, "indexeurs Jackett dans Prowlarr", indexers, failure=WARN)


def check_added_apps(report, env):
    """Applications ajoutées depuis le catalogue : réponse HTTP, et liaisons pour celles qui en ont."""
    for entry in ctl.apps().installed_entries():
        group = entry["name"]
        if entry.get("headless"):
            continue          # pas d'interface : son état de conteneur suffit
        path = entry["health"]["path"] if entry["health"] else entry["path"] + "/"

        def reply(entry=entry, path=path):
            result = ctl.request("GET", ctl.svc(entry["id"], entry["port"]) + path, timeout=20)
            if result.status >= 400:
                raise ctl.SeedboxError(f"HTTP {result.status} sur {path}")
            return f"répond ({path})"
        report.attempt(group, "réponse HTTP", reply)
        if entry.get("integration") != "lidarr":
            continue
        report.attempt(group, "API (clé)", lambda: "version " + str(expect(ctl.arr_api("lidarr", "GET", "/system/status")).json()["version"]))

        def link():
            apps = {a["name"] for a in expect(ctl.arr_api("prowlarr", "GET", "/applications")).json()}
            if "Lidarr" not in apps:
                raise ctl.SeedboxError("non reliée à Prowlarr")
            return "reliée à Prowlarr"
        report.attempt(group, "liaison avec Prowlarr", link)

        def client():
            clients = [c for c in expect(ctl.arr_api("lidarr", "GET", "/downloadclient")).json() if c["implementation"] == "QBittorrent"]
            if not clients:
                raise ctl.SeedboxError("aucun client qBittorrent")
            fields = {f["name"]: f.get("value") for f in clients[0]["fields"]}
            if fields.get("host") != ctl.qbit_host():
                raise ctl.SeedboxError(f"client sur {fields.get('host')}, attendu {ctl.qbit_host()}")
            return f"qBittorrent ({ctl.qbit_host()}:8080)"
        report.attempt(group, "client de téléchargement qBittorrent", client)


def check_system(report, env):
    for path, label in ((ctl.STORAGE, "espace de stockage"), (ctl.BASE, "disque système")):
        try:
            usage = shutil.disk_usage(path)
        except OSError as error:
            report.add("Système", label, FAIL, error)
            continue
        free = usage.free * 100 // usage.total
        status = OK if free >= 10 else WARN if free >= 5 else FAIL
        report.add("Système", label, status, f"{free} % libre ({usage.free // 2**30} Go)")


def run_checks():
    env = ctl.env_read()
    report = Report()
    check_containers(report, env)
    check_front(report, env)
    for name, label in (("prowlarr", "Prowlarr"), ("radarr", "Radarr"), ("sonarr", "Sonarr")):
        arr_checks(report, env, name, label)
    check_jackett(report, env)
    check_added_apps(report, env)
    check_jellyfin(report, env)
    check_seerr(report, env)
    check_qbittorrent(report, env)
    check_qbm_and_cross_seed(report, env)
    check_autobrr(report, env)
    check_p2p(report, env)
    check_system(report, env)
    return report.items


def summary(items):
    counts = {status: sum(1 for i in items if i["status"] == status) for status in (OK, WARN, FAIL)}
    return counts


def render(items):
    marks = {OK: "OK    ", WARN: "ALERTE", FAIL: "ÉCHEC "}
    lines, group = [], None
    for item in items:
        if item["group"] != group:
            group = item["group"]
            lines.append(f"\n{group}")
        lines.append(f"  [{marks[item['status']]}] {item['label']}" + (f" : {item['detail']}" if item["detail"] else ""))
    counts = summary(items)
    lines.append(f"\n{counts[OK]} réussi(s), {counts[WARN]} alerte(s), {counts[FAIL]} échec(s)")
    return "\n".join(lines).lstrip("\n")


def main(as_json=False):
    items = run_checks()
    if as_json:
        print(json.dumps({"items": items, "summary": summary(items)}, ensure_ascii=False))
    else:
        print(render(items))
    return 1 if summary(items)[FAIL] else 0
