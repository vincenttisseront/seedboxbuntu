"""SeedBoxBuntu - diagnostic réseau : chaque point est testé et expliqué en clair.

Tests (aucun ne modifie quoi que ce soit) :
  internet      le serveur joint Internet (connexion TCP vers un résolveur public) ;
  dns           les noms se résolvent (le nom de domaine du serveur s'il est personnalisé, sinon un site connu) ;
  domaine       le nom de domaine pointe vers une adresse de ce serveur (ou vers l'adresse publique vue par Internet) ;
  certificat    le certificat présenté sur le port 443 est valide et sa fin est lointaine ;
  heure         l'horloge est synchronisée (un écart casse HTTPS, la double authentification et les jetons) ;
  vpn           (mode VPN) le tunnel est en marche et l'adresse de sortie n'est pas celle du serveur ;
  port          (mode VPN, redirection de port) le port entrant de qBittorrent est celui annoncé par le fournisseur ;
  qbittorrent   qBittorrent est joignable et se dit « connecté » (et non « derrière un pare-feu »).
Chaque test rend un état (ok, attention, erreur, inconnu), une phrase et, si utile, la conduite à tenir.
Le test « adresse publique » interroge un service extérieur (api.ipify.org) : rien d'autre n'en sort.
    seedboxctl netcheck
"""
import json
import re
import socket
import ssl
import subprocess
import time
import urllib.request

import seedboxctl as ctl

CERT_WARN_DAYS = 14
RESOLVERS = (("1.1.1.1", 443), ("9.9.9.9", 443))


def item(check_id, title, state, detail, hint=""):
    return {"id": check_id, "title": title, "state": state, "detail": detail, "hint": hint}


# --- sondes (remplacées par des doubles dans les tests) -------------------------------------------
class Probes:
    def tcp(self, host, port, timeout=4):
        with socket.create_connection((host, port), timeout=timeout):
            return True

    def resolve(self, name):
        return sorted({info[4][0] for info in socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)})

    def public_ip(self):
        with urllib.request.urlopen("https://api.ipify.org", timeout=8) as response:
            return response.read().decode().strip()

    def local_ips(self):
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
        return out

    def certificate(self, host):
        """(jours restants, émetteur) du certificat servi sur le port 443 local pour ce nom."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", 443), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        pem = ssl.DER_cert_to_PEM_cert(der)
        out = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-issuer"], input=pem, capture_output=True, text=True, timeout=10).stdout
        end, issuer = 0, ""
        for line in out.splitlines():
            if line.startswith("notAfter="):
                end = ssl.cert_time_to_seconds(line.split("=", 1)[1].strip())
            elif line.startswith("issuer="):
                match = re.search(r"\bO\s*=\s*([^,/]+)", line) or re.search(r"\bCN\s*=\s*([^,/]+)", line)
                issuer = match.group(1).strip() if match else ""
        if not end:
            raise OSError("certificat illisible")
        return (end - time.time()) / 86400, issuer

    def ntp_synchronized(self):
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True, timeout=5).stdout
        return out.strip() == "yes"

    def vpn(self):
        return ctl.vpn_overview()

    def qbit_info(self):
        cookie = ctl.qbit_login()
        base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"
        info = ctl.request("GET", base + "/transfer/info", headers={"Cookie": cookie}, timeout=10).json() or {}
        prefs = ctl.request("GET", base + "/app/preferences", headers={"Cookie": cookie}, timeout=10).json() or {}
        return {"status": info.get("connection_status", ""), "port": prefs.get("listen_port")}


def run(probes=None, env=None):
    probes, env = probes or Probes(), env if env is not None else ctl.env_read()
    out = []

    def check(check_id, title, compute):
        try:
            out.append(compute())
        except Exception as error:  # noqa: BLE001 - un test en panne ne masque pas les autres
            out.append(item(check_id, title, "unknown", f"Test impossible : {str(error)[:120] or error.__class__.__name__}"))

    custom = env.get("DOMAIN_MODE") == "custom" and env.get("DOMAIN")

    def internet():
        for host, port in RESOLVERS:
            try:
                probes.tcp(host, port)
                return item("internet", "Accès à Internet", "ok", "Le serveur joint Internet.")
            except OSError:
                continue
        return item("internet", "Accès à Internet", "error", "Le serveur ne joint pas Internet.",
                    "Vérifiez le câble, la box et la passerelle (menu texte : réseau).")

    def dns():
        name = env["DOMAIN"] if custom else "releases.ubuntu.com"
        try:
            probes.resolve(name)
        except OSError:
            return item("dns", "Résolution des noms", "error", f"Le nom « {name} » ne se résout pas.",
                        "Vérifiez le serveur DNS du serveur et, pour votre domaine, l'enregistrement chez votre fournisseur." if custom
                        else "Vérifiez le serveur DNS du serveur (menu texte : réseau).")
        return item("dns", "Résolution des noms", "ok", f"« {name} » se résout.")

    def domain():
        if not custom:
            return item("domain", "Nom de domaine", "info", "Adresse automatique (IP du serveur) : pas de nom de domaine à contrôler.")
        found = probes.resolve(env["DOMAIN"])
        local = set(probes.local_ips())
        if local & set(found):
            return item("domain", "Nom de domaine", "ok", f"« {env['DOMAIN']} » pointe vers ce serveur ({', '.join(sorted(local & set(found)))}).")
        try:
            public = probes.public_ip()
        except OSError:
            return item("domain", "Nom de domaine", "unknown", "L'adresse publique du serveur n'a pas pu être lue (service extérieur injoignable).")
        if public in found:
            return item("domain", "Nom de domaine", "ok", f"« {env['DOMAIN']} » pointe vers l'adresse publique du serveur ({public}).")
        return item("domain", "Nom de domaine", "warn", f"« {env['DOMAIN']} » pointe vers {', '.join(found)}, pas vers l'adresse de ce serveur ({public}).",
                    "Corrigez l'enregistrement DNS chez votre fournisseur ; ouvrez aussi les ports 80 et 443 de la box vers le serveur.")

    def certificate():
        host = env.get("DOMAIN") or "localhost"
        days, issuer = probes.certificate(host)
        if days < 0:
            return item("certificate", "Certificat HTTPS", "error", "Le certificat est expiré.", "Ajoutez ou changez une adresse dans la console pour le renouveler.")
        if days < CERT_WARN_DAYS:
            return item("certificate", "Certificat HTTPS", "warn", f"Le certificat expire dans {days:.0f} jours ({issuer}).",
                        "Un renouvellement automatique est attendu ; vérifiez que les ports 80 et 443 sont joignables.")
        return item("certificate", "Certificat HTTPS", "ok", f"Valable encore {days:.0f} jours ({issuer or 'autorité locale'}).")

    def clock():
        if probes.ntp_synchronized():
            return item("clock", "Horloge", "ok", "L'horloge est synchronisée.")
        return item("clock", "Horloge", "warn", "L'horloge n'est pas synchronisée.",
                    "Un écart d'heure casse HTTPS, la double authentification et les jetons. Vérifiez « timedatectl » et l'accès au service de temps.")

    def vpn():
        data = probes.vpn()
        if not data.get("enabled"):
            return item("vpn", "Tunnel VPN", "info", "qBittorrent est en accès direct, sans VPN.")
        state = data.get("state")
        if state not in ("healthy", "running"):
            return item("vpn", "Tunnel VPN", "error", f"Le tunnel n'est pas en marche (état : {state or 'inconnu'}).",
                        "qBittorrent n'a aucun accès tant que le tunnel est coupé (volontaire : pas de fuite). Voir la page VPN et les journaux de gluetun.")
        exit_ip = data.get("ip")
        if not exit_ip:
            return item("vpn", "Tunnel VPN", "warn", "Le tunnel est en marche mais son adresse de sortie n'a pas pu être lue.")
        try:
            own = probes.public_ip()
        except OSError:
            return item("vpn", "Tunnel VPN", "ok", f"Tunnel en marche, adresse de sortie {exit_ip}.")
        if own == exit_ip:
            return item("vpn", "Tunnel VPN", "error", f"L'adresse de sortie du tunnel ({exit_ip}) est celle du serveur : le trafic ne passe pas par le VPN.",
                        "Vérifiez le fournisseur et les identifiants VPN ; arrêtez qBittorrent tant que ce n'est pas réglé.")
        return item("vpn", "Tunnel VPN", "ok", f"Adresse de sortie {exit_ip}, différente de celle du serveur ({own}).")

    def port():
        data = probes.vpn()
        if not data.get("enabled") or not data.get("port_forwarding"):
            return item("port", "Port entrant", "info", "Pas de redirection de port par le fournisseur VPN.")
        forwarded = data.get("port")
        if not forwarded:
            return item("port", "Port entrant", "warn", "Aucun port entrant annoncé.", "Le fournisseur doit accepter la redirection de port (NAT-PMP pour Proton).")
        return item("port", "Port entrant", "ok", f"Port entrant {forwarded}.")

    def qbit():
        if not ctl.qbit_mode():
            return item("qbittorrent", "qBittorrent", "info", "qBittorrent n'est pas installé.")
        info = probes.qbit_info()
        status = info.get("status", "")
        if status == "connected":
            return item("qbittorrent", "qBittorrent", "ok", f"Connecté (port {info.get('port')}).")
        if status == "firewalled":
            return item("qbittorrent", "qBittorrent", "warn", "qBittorrent est derrière un pare-feu : il télécharge, mais reçoit peu de connexions entrantes.",
                        "Ouvrez le port de partage dans la box (mode direct) ou activez la redirection de port du VPN.")
        return item("qbittorrent", "qBittorrent", "error", "qBittorrent n'a aucune connexion.", "Vérifiez Internet, le DNS et le tunnel VPN.")

    for check_id, title, compute in (("internet", "Accès à Internet", internet), ("dns", "Résolution des noms", dns), ("domain", "Nom de domaine", domain),
                                    ("certificate", "Certificat HTTPS", certificate), ("clock", "Horloge", clock), ("vpn", "Tunnel VPN", vpn),
                                    ("port", "Port entrant", port), ("qbittorrent", "qBittorrent", qbit)):
        check(check_id, title, compute)
    problems = [i for i in out if i["state"] in ("warn", "error")]
    return {"items": out, "problems": len(problems), "errors": sum(1 for i in out if i["state"] == "error"), "at": int(time.time())}


def show():
    print(json.dumps(run(), indent=2, ensure_ascii=False))
