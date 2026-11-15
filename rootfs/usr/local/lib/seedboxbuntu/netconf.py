"""SeedBoxBuntu - réseau de l'appliance : adresse IP fixe ou DHCP, passerelle, DNS, depuis la console.

Même fichier que le menu texte (`seedbox-setup`) : /etc/netplan/01-seedboxbuntu.yaml, renderer networkd. Changer l'adresse peut
couper l'accès à la console ; la procédure est donc faite pour se rattraper toute seule :
  1. la nouvelle configuration est vérifiée (adresse, masque, passerelle dans le même réseau, DNS) puis `netplan generate` ;
  2. la configuration actuelle est copiée (dossier netplan-backup) et un retour arrière automatique est programmé
     (minuterie systemd transitoire, 120 secondes) ;
  3. `netplan apply` est lancé 3 secondes après la réponse, en tâche détachée, puis `seedboxctl refresh` met à jour le certificat,
     les cookies Authelia et les adresses des applications pour la nouvelle IP ;
  4. depuis la nouvelle adresse, « Conserver » annule le retour arrière ; sans clic, l'ancienne configuration revient.
Un seul changement en attente à la fois. Seules les cartes physiques (en*, eth*, wl*) sont proposées ; IPv4 uniquement ici
(IPv6 reste en configuration automatique).
    seedboxctl network show | set IFACE dhcp|static [ADRESSE/CIDR PASSERELLE DNS...] | confirm | revert
"""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import seedboxctl as ctl

ROOT = Path(os.environ.get("SB_ROOT", "/"))
LIVE = ROOT == Path("/")
NETPLAN_DIR = ROOT / "etc/netplan"
NETPLAN_FILE = NETPLAN_DIR / "01-seedboxbuntu.yaml"
PENDING = ctl.STATE / "network-pending.json"
BACKUPS = ctl.STATE / "netplan-backup"
CONF = ctl.ETC / "network.conf"       # relu par le menu texte (seedbox-setup)
SYS = Path(os.environ.get("SB_SYS", "/sys"))
UNIT = "seedbox-netrevert"
TIMEOUT = 120
CTL_BIN = "/usr/local/sbin/seedboxctl"
IFACE = re.compile(r"^(en[a-z0-9]+|eth\d+|wl[a-z0-9]+)$")
RUN = None            # tests : remplace l'exécution des commandes (cmd, timeout) -> CompletedProcess


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def sh(*command, timeout=30):
    if RUN is not None:
        return RUN(command, timeout)
    if not LIVE:
        return subprocess.CompletedProcess(command, 0, "", "")
    try:
        return subprocess.run([str(c) for c in command], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, "", f"{command[0]} : commande introuvable")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} : délai dépassé")


def detached(script):
    """Commande shell lancée hors du processus courant (qui peut perdre son réseau) ; sans effet hors de l'appliance."""
    if RUN is not None:
        RUN(("detached", script), 0)
        return
    if LIVE:
        subprocess.Popen(["sh", "-c", script], start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ----------------------------------------------------------------------------
#  Lecture
# ----------------------------------------------------------------------------
def interfaces():
    out = sh("ip", "-j", "addr").stdout
    try:
        data = json.loads(out or "[]")
    except ValueError:
        data = []
    found = []
    for entry in data:
        name = entry.get("ifname", "")
        if not IFACE.match(name):
            continue
        addresses = [{"cidr": f"{a['local']}/{a['prefixlen']}", "dynamic": bool(a.get("dynamic"))}
                     for a in entry.get("addr_info", []) if a.get("family") == "inet"]
        speed = ""
        try:
            value = int((SYS / "class/net" / name / "speed").read_text().strip())
            speed = f"{value} Mb/s" if value > 0 else ""
        except (OSError, ValueError):
            pass
        found.append({"name": name, "state": str(entry.get("operstate", "")).lower(), "mac": entry.get("address", ""),
                      "speed": speed, "addresses": addresses})
    return found


def default_route():
    out = sh("ip", "-j", "-4", "route", "show", "default").stdout
    try:
        routes = json.loads(out or "[]")
    except ValueError:
        routes = []
    return {"gateway": routes[0].get("gateway", ""), "dev": routes[0].get("dev", "")} if routes else {"gateway": "", "dev": ""}


def dns_servers():
    for name in ("run/systemd/resolve/resolv.conf", "etc/resolv.conf"):
        try:
            text = (ROOT / name).read_text()
        except OSError:
            continue
        found = [m for m in re.findall(r"^nameserver\s+(\S+)", text, re.M) if m not in ("127.0.0.53", "127.0.0.1")]
        if found:
            return found
    return []


def configured():
    """Réglage enregistré (menu texte ou console) : {iface, mode, address, gateway, dns} ou None."""
    try:
        values = dict(re.findall(r"^(NET_[A-Z]+)=(.*)$", CONF.read_text(), re.M))
    except OSError:
        return None
    clean = {k: v.strip().strip("'\"") for k, v in values.items()}
    if not clean.get("NET_IF"):
        return None
    return {"iface": clean["NET_IF"], "mode": clean.get("NET_MODE", "dhcp"), "address": clean.get("NET_ADDR", ""),
            "gateway": clean.get("NET_GW", ""), "dns": clean.get("NET_DNS", "").replace("\\ ", " ").split()}


def pending():
    try:
        data = json.loads(PENDING.read_text())
    except (OSError, ValueError):
        return None
    data["seconds_left"] = max(0, int(data.get("deadline", 0) - time.time()))
    return data


def overview():
    try:
        hostname = (ROOT / "etc/hostname").read_text().strip()
    except OSError:
        hostname = ""
    return {"interfaces": interfaces(), "route": default_route(), "dns": dns_servers(), "configured": configured(),
            "hostname": hostname, "pending": pending(), "timeout": TIMEOUT}


# ----------------------------------------------------------------------------
#  Validation et écriture
# ----------------------------------------------------------------------------
def validate(iface, mode, address="", gateway="", dns=()):
    """Renvoie la configuration nettoyée ou lève SeedboxError avec une phrase claire."""
    iface = str(iface or "").strip()
    if not IFACE.match(iface) or (LIVE and iface not in {i["name"] for i in interfaces()}):
        raise ctl.SeedboxError("carte réseau inconnue")
    if mode not in ("dhcp", "static"):
        raise ctl.SeedboxError("mode attendu : dhcp ou static")
    if mode == "dhcp":
        return {"iface": iface, "mode": "dhcp", "address": "", "gateway": "", "dns": []}
    try:
        interface = ipaddress.ip_interface(str(address).strip())
    except ValueError:
        raise ctl.SeedboxError("adresse invalide : écrivez-la avec son masque, par exemple 192.168.1.50/24")
    if interface.version != 4 or "/" not in str(address):
        raise ctl.SeedboxError("adresse IPv4 avec masque attendue, par exemple 192.168.1.50/24")
    network = interface.network
    if network.prefixlen < 8 or network.prefixlen > 30:
        raise ctl.SeedboxError("masque entre /8 et /30 attendu")
    if interface.ip in (network.network_address, network.broadcast_address):
        raise ctl.SeedboxError("cette adresse est celle du réseau ou de diffusion : choisissez une adresse d'hôte")
    if interface.ip.is_loopback or interface.ip.is_multicast or interface.ip.is_link_local or interface.ip.is_unspecified:
        raise ctl.SeedboxError("adresse non utilisable (boucle locale, multidiffusion, lien local)")
    gateway = str(gateway or "").strip()
    if gateway:
        try:
            gate = ipaddress.ip_address(gateway)
        except ValueError:
            raise ctl.SeedboxError("passerelle invalide")
        if gate.version != 4 or gate not in network or gate == interface.ip or gate in (network.network_address, network.broadcast_address):
            raise ctl.SeedboxError(f"la passerelle doit être une autre adresse du réseau {network}")
    servers = []
    for item in dns if isinstance(dns, (list, tuple)) else str(dns or "").replace(",", " ").split():
        item = str(item).strip()
        if not item:
            continue
        try:
            ip = ipaddress.ip_address(item)
        except ValueError:
            raise ctl.SeedboxError(f"serveur DNS invalide : {item[:40]}")
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            raise ctl.SeedboxError(f"serveur DNS non utilisable : {item}")
        if str(ip) not in servers:
            servers.append(str(ip))
    if len(servers) > 4:
        raise ctl.SeedboxError("quatre serveurs DNS au plus")
    return {"iface": iface, "mode": "static", "address": str(interface), "gateway": gateway, "dns": servers}


def netplan_text(cfg):
    lines = [f"# Généré par SeedBoxBuntu (console) le {time.strftime('%F %T')}", "network:", "  version: 2", "  renderer: networkd",
             "  ethernets:", f"    {cfg['iface']}:"]
    if cfg["mode"] == "static":
        lines += ["      dhcp4: false", f"      addresses: [\"{cfg['address']}\"]"]
        if cfg["gateway"]:
            lines += ["      routes:", "        - to: default", f"          via: \"{cfg['gateway']}\""]
        if cfg["dns"]:
            lines += ["      nameservers:", "        addresses: [" + ", ".join(f'"{d}"' for d in cfg["dns"]) + "]"]
    else:
        lines.append("      dhcp4: true")
    return "\n".join(lines) + "\n"


def save_conf(cfg):
    ctl.ETC.mkdir(parents=True, exist_ok=True)
    CONF.write_text("".join(f"{key}={_shell(value)}\n" for key, value in (
        ("NET_IF", cfg["iface"]), ("NET_MODE", cfg["mode"]), ("NET_ADDR", cfg["address"]), ("NET_GW", cfg["gateway"]),
        ("NET_DNS", " ".join(cfg["dns"])))))


def _shell(value):
    value = str(value)
    return value if re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]*", value) and value else "'" + value.replace("'", "'\\''") + "'"


def backup():
    """Copie de tous les fichiers netplan actuels ; renvoie le dossier."""
    folder = BACKUPS / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for path in sorted(NETPLAN_DIR.glob("*.yaml")):
        shutil.copy2(path, folder / path.name)
        names.append(path.name)
    (folder / "list.json").write_text(json.dumps(names))
    for old in sorted(BACKUPS.iterdir())[:-5]:     # cinq sauvegardes au plus
        shutil.rmtree(old, ignore_errors=True)
    return folder


def restore(folder):
    folder = Path(folder)
    try:
        names = json.loads((folder / "list.json").read_text())
    except (OSError, ValueError):
        names = [p.name for p in folder.glob("*.yaml")]
    for path in NETPLAN_DIR.glob("*.yaml"):
        if path.name not in names:
            path.unlink()
    for name in names:
        shutil.copy2(folder / name, NETPLAN_DIR / name)
        (NETPLAN_DIR / name).chmod(0o600)


# ----------------------------------------------------------------------------
#  Application et retour arrière
# ----------------------------------------------------------------------------
def apply(iface, mode, address="", gateway="", dns=(), by="console", timeout=None):
    """Prépare et lance le changement ; la suite se joue depuis la nouvelle adresse (confirm) ou toute seule (revert)."""
    if pending():
        raise ctl.SeedboxError("un changement réseau attend déjà sa confirmation : conservez-le ou annulez-le d'abord")
    cfg = validate(iface, mode, address, gateway, dns)
    timeout = int(timeout or TIMEOUT)
    NETPLAN_DIR.mkdir(parents=True, exist_ok=True)
    folder = backup()
    previous = configured()
    try:
        # Les autres fichiers netplan seraient en conflit avec le nôtre pour la même carte : ils sont mis de côté
        for path in NETPLAN_DIR.glob("*.yaml"):
            if path != NETPLAN_FILE:
                path.unlink()
        tmp = NETPLAN_FILE.with_suffix(".tmp")
        tmp.write_text(netplan_text(cfg))
        tmp.chmod(0o600)
        tmp.replace(NETPLAN_FILE)
        check = sh("netplan", "generate", timeout=60)
        if check.returncode != 0:
            raise ctl.SeedboxError("netplan refuse cette configuration : " + (check.stderr or check.stdout or "").strip()[-300:])
    except Exception:
        restore(folder)
        raise
    save_conf(cfg)
    state = {"at": time.time(), "deadline": time.time() + timeout, "backup": str(folder), "by": by, "previous": previous, "new": cfg}
    PENDING.write_text(json.dumps(state))
    arm = sh("systemd-run", "--unit", UNIT, f"--on-active={timeout}s", "--timer-property=AccuracySec=1s", CTL_BIN, "network", "revert")
    if arm.returncode != 0:      # sans filet de sécurité, on ne coupe pas la connexion
        restore(folder)
        if previous:
            save_conf({"iface": previous["iface"], "mode": previous["mode"], "address": previous["address"],
                       "gateway": previous["gateway"], "dns": previous["dns"]})
        PENDING.unlink(missing_ok=True)
        sh("netplan", "generate", timeout=60)
        raise ctl.SeedboxError("retour arrière automatique impossible à programmer : rien n'a été modifié (" + (arm.stderr or "").strip()[-160:] + ")")
    detached(f"sleep 3; netplan apply; sleep 10; {CTL_BIN} refresh")
    say(f"réseau : {cfg['iface']} {cfg['mode']} {cfg['address']} (retour arrière dans {timeout} s sans confirmation)")
    return {"pending": pending(), "address": cfg["address"].split("/")[0] if cfg["mode"] == "static" else "", "mode": cfg["mode"]}


def confirm(by="console"):
    state = pending()
    if not state:
        raise ctl.SeedboxError("aucun changement réseau en attente")
    sh("systemctl", "stop", f"{UNIT}.timer", f"{UNIT}.service")
    PENDING.unlink(missing_ok=True)
    say("réseau : changement conservé")
    return {"kept": True}


def revert(by="auto"):
    state = pending()
    if not state:
        raise ctl.SeedboxError("aucun changement réseau en attente")
    restore(state["backup"])
    previous = state.get("previous")
    if previous:
        save_conf({"iface": previous["iface"], "mode": previous["mode"], "address": previous["address"],
                   "gateway": previous["gateway"], "dns": previous["dns"]})
    else:
        CONF.unlink(missing_ok=True)
    PENDING.unlink(missing_ok=True)
    sh("systemctl", "stop", f"{UNIT}.timer", f"{UNIT}.service")
    sh("netplan", "generate", timeout=60)
    detached(f"netplan apply; sleep 10; {CTL_BIN} refresh")
    say("réseau : ancienne configuration rétablie")
    try:
        ctl.alerts().notify("Réseau : ancienne configuration rétablie",
                            "Le changement réseau n'a pas été confirmé à temps : l'ancienne configuration est revenue.", "warning", True)
    except Exception:       # une alerte qui échoue ne doit pas empêcher le retour arrière
        pass
    return {"reverted": True}
