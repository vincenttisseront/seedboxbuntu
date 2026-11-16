"""SeedBoxBuntu - fail2ban : bannissements en cours, débannir, bannir, adresses ignorées, seuils.

fail2ban surveille les connexions SSH (jail « sshd », fournie par l'appliance) et bannit pendant un temps croissant l'adresse
qui échoue trop souvent. Ce module fait depuis la console ce qu'on ferait avec fail2ban-client :
  - état : service actif ou non, jails, adresses bannies (avec le temps restant), échecs en cours, historique récent ;
  - débannir une adresse (d'un jail ou de tous), ou toutes d'un coup ; bannir une adresse à la main ;
  - adresses ignorées (jamais bannies) : la vôtre, celle du réseau local... ; 127.0.0.1 et ::1 le restent toujours ;
  - seuils : durée du bannissement, fenêtre de comptage, essais permis, durée croissante pour les récidivistes.
Les réglages sont écrits dans un fichier à part (jail.d/zz-seedboxbuntu-console.local, lu après celui de l'appliance) : le
durcissement de l'appliance ne les écrase pas. Un réglage refusé par fail2ban (test de configuration) est annulé avant le
rechargement, pour ne jamais laisser le service arrêté.
Limites assumées : seule la protection SSH est fournie par l'appliance ; la connexion web est protégée par la régulation
d'Authelia (5 essais, puis 10 minutes), qui n'est pas gérée ici. Un bannissement concerne le pare-feu du serveur : il coupe
l'accès SSH mais pas la console web.
    seedboxctl bans show | unban ADRESSE [--jail JAIL] | unban-all | ban ADRESSE [--jail JAIL] | ignore ADRESSE | unignore ADRESSE
                    | set [--ban-minutes N] [--window-minutes N] [--retries N] [--increment|--no-increment] [--max-hours N]
"""
import ipaddress
import json
import os
import re
import time
from pathlib import Path

import seedboxctl as ctl

CONF_DIR = Path(os.environ.get("SB_F2B_DIR", "/etc/fail2ban/jail.d"))
CONF_FILE = CONF_DIR / "zz-seedboxbuntu-console.local"
SETTINGS_FILE = ctl.ETC / "bans.json"
ALWAYS = ("127.0.0.1/8", "::1")
DEFAULTS = {"ban_minutes": 60, "window_minutes": 10, "retries": 5, "increment": True, "max_hours": 168, "ignore": []}
LIMITS = {"ban_minutes": (1, 43200), "window_minutes": (1, 1440), "retries": (2, 30), "max_hours": (1, 8760)}
JAIL = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def client(*args):
    """fail2ban-client ; point d'entrée unique (les tests le remplacent). Renvoie (code, sortie, erreur)."""
    result = ctl.run("fail2ban-client", *args, check=False, capture=True)
    return result.returncode, result.stdout or "", result.stderr or ""


def systemctl(*args):
    result = ctl.run("systemctl", *args, check=False, capture=True)
    return result.returncode, (result.stdout or "").strip()


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    cfg = dict(DEFAULTS)
    for key, (low, high) in LIMITS.items():
        value = stored.get(key) if isinstance(stored, dict) else None
        cfg[key] = max(low, min(high, int(value))) if isinstance(value, (int, float)) and not isinstance(value, bool) else DEFAULTS[key]
    cfg["increment"] = bool(stored.get("increment", True)) if isinstance(stored, dict) else True
    raw = stored.get("ignore", []) if isinstance(stored, dict) else []
    cfg["ignore"] = [x for x in (raw if isinstance(raw, list) else []) if isinstance(x, str) and valid_network(x)]
    return cfg


def valid_network(text):
    try:
        ipaddress.ip_network(text.strip(), strict=False)
        return True
    except ValueError:
        return False


def clean_address(text):
    """Adresse IP unique, normalisée ; ValueError sinon."""
    try:
        return str(ipaddress.ip_address(str(text).strip()))
    except ValueError as error:
        raise ctl.SeedboxError(f"adresse IP invalide : {str(text)[:60]}") from error


def render(cfg):
    ignore = " ".join([*ALWAYS, *cfg["ignore"]])
    return ("# Écrit par la console SeedBoxBuntu (page Bannissements) : ne pas modifier à la main\n[DEFAULT]\n"
            f"ignoreip = {ignore}\nbantime = {cfg['ban_minutes']}m\nfindtime = {cfg['window_minutes']}m\nmaxretry = {cfg['retries']}\n"
            f"bantime.increment = {'true' if cfg['increment'] else 'false'}\nbantime.maxtime = {cfg['max_hours']}h\n")


def apply(cfg):
    """Écrit la configuration, la teste, recharge fail2ban ; remet l'ancienne si fail2ban la refuse."""
    previous = CONF_FILE.read_text() if CONF_FILE.exists() else None
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    CONF_FILE.write_text(render(cfg))
    code, _out, err = client("--test")
    if code != 0:
        if previous is None:
            CONF_FILE.unlink(missing_ok=True)
        else:
            CONF_FILE.write_text(previous)
        raise ctl.SeedboxError("fail2ban refuse ces réglages, ancienne configuration conservée : " + (err or _out).strip()[-200:])
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({k: cfg[k] for k in DEFAULTS}))
    code, _out, err = client("reload")
    if code != 0:
        raise ctl.SeedboxError("rechargement de fail2ban impossible : " + (err or _out).strip()[-200:])
    return cfg


def save(ban_minutes=None, window_minutes=None, retries=None, increment=None, max_hours=None):
    cfg = settings()
    for key, value in (("ban_minutes", ban_minutes), ("window_minutes", window_minutes), ("retries", retries), ("max_hours", max_hours)):
        if value is None:
            continue
        low, high = LIMITS[key]
        try:
            number = int(value)
        except (TypeError, ValueError) as error:
            raise ctl.SeedboxError(f"{key} : nombre entier attendu") from error
        if not low <= number <= high:
            raise ctl.SeedboxError(f"{key} : de {low} à {high}")
        cfg[key] = number
    if increment is not None:
        cfg["increment"] = bool(increment)
    return apply(cfg)


def ignore(address):
    text = str(address).strip()
    if not valid_network(text):
        raise ctl.SeedboxError(f"adresse ou réseau invalide : {text[:60]} (exemples : 192.168.1.20, 192.168.1.0/24)")
    network = str(ipaddress.ip_network(text, strict=False)) if "/" in text else str(ipaddress.ip_address(text))
    cfg = settings()
    if network not in cfg["ignore"]:
        cfg["ignore"].append(network)
    apply(cfg)
    if "/" not in network:
        try:
            unban(network)                   # une adresse ignorée et déjà bannie est libérée tout de suite
        except ctl.SeedboxError:
            pass
    return cfg


def unignore(address):
    text = str(address).strip()
    cfg = settings()
    if text not in cfg["ignore"]:
        raise ctl.SeedboxError(f"{text[:60]} n'est pas dans les adresses ignorées")
    cfg["ignore"].remove(text)
    return apply(cfg)


# ---------------------------------------------------------------------------------------------------------------
#  État
# ---------------------------------------------------------------------------------------------------------------
def jail_names():
    code, out, _err = client("status")
    if code != 0:
        return []
    found = re.search(r"Jail list:\s*(.*)", out)
    return [n.strip() for n in (found.group(1).split(",") if found else []) if n.strip()]


def number(out, label):
    found = re.search(rf"{label}:\s*(\d+)", out)
    return int(found.group(1)) if found else 0


def banned_with_time(jail):
    """{adresse: secondes restantes ou None} d'après « get JAIL banip --with-time »."""
    code, out, _err = client("get", jail, "banip", "--with-time")
    rows = {}
    if code != 0:
        return rows
    for line in out.splitlines():
        found = re.match(r"\s*(\S+)\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*\+\s*(-?\d+)\s*=\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
        if found:
            try:
                end = time.mktime(time.strptime(found.group(4), "%Y-%m-%d %H:%M:%S"))
                rows[found.group(1)] = {"until": int(end), "seconds": int(found.group(3))}
            except ValueError:
                rows[found.group(1)] = {"until": None, "seconds": int(found.group(3))}
        elif line.strip() and re.match(r"\s*[0-9a-fA-F:.]+\s*$", line):
            rows[line.strip()] = {"until": None, "seconds": None}
    return rows


def jail_status(name):
    code, out, err = client("status", name)
    if code != 0:
        return {"name": name, "error": (err or out).strip()[-160:], "banned": [], "failed": 0, "failed_total": 0, "banned_total": 0}
    listed = re.search(r"Banned IP list:\s*(.*)", out)
    addresses = [a for a in (listed.group(1).split() if listed else []) if a]
    times = banned_with_time(name) if addresses else {}
    return {"name": name, "error": "", "failed": number(out, "Currently failed"), "failed_total": number(out, "Total failed"),
            "banned_total": number(out, "Total banned"),
            "banned": [{"address": a, "until": (times.get(a) or {}).get("until"), "seconds": (times.get(a) or {}).get("seconds")} for a in addresses]}


def recent(limit=40):
    """Dernières décisions de fail2ban (bannissements, débannissements) d'après son journal, du plus récent au plus ancien."""
    result = ctl.run("journalctl", "-u", "fail2ban", "--since", "-3 days", "-o", "short-iso", "--no-pager", "-n", "400", check=False, capture=True)
    rows = []
    for line in (result.stdout or "").splitlines():
        found = re.search(r"^(\S+)\s+\S+\s+\S+\[\d+\]:.*?\[(\S+)\]\s+(Ban|Unban|Restore Ban)\s+(\S+)", line)
        if found:
            rows.append({"at": found.group(1), "jail": found.group(2), "action": found.group(3), "address": found.group(4)})
    return rows[::-1][:limit]


def overview(client_address=""):
    active = systemctl("is-active", "fail2ban")[1] == "active"
    jails = [jail_status(n) for n in jail_names()] if active else []
    cfg = settings()
    return {"active": active, "installed": client("--version")[0] == 0, "jails": jails, "settings": cfg, "always": list(ALWAYS), "limits": {k: list(v) for k, v in LIMITS.items()},
            "recent": recent() if active else [], "me": client_address,
            "me_ignored": bool(client_address) and any(ip_in(client_address, n) for n in [*ALWAYS, *cfg["ignore"]]),
            "me_banned": bool(client_address) and any(b["address"] == client_address for j in jails for b in j["banned"]),
            "total_banned": sum(len(j["banned"]) for j in jails)}


def ip_in(address, network):
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False


# ---------------------------------------------------------------------------------------------------------------
#  Actions
# ---------------------------------------------------------------------------------------------------------------
def check_jail(jail):
    if not JAIL.match(jail or ""):
        raise ctl.SeedboxError("nom de jail invalide")
    if jail not in jail_names():
        raise ctl.SeedboxError(f"jail inconnu ou arrêté : {jail}")


def unban(address, jail=""):
    """Débannit une adresse du jail indiqué, ou de tous les jails si aucun n'est donné ; renvoie le nombre de jails concernés."""
    address = clean_address(address)
    names = [jail] if jail else jail_names()
    if jail:
        check_jail(jail)
    done = 0
    for name in names:
        code, out, _err = client("set", name, "unbanip", address)
        done += code == 0 and out.strip() not in ("0", "")
    if not done:
        raise ctl.SeedboxError(f"{address} n'est pas banni")
    return done


def unban_all():
    code, out, err = client("unban", "--all")
    if code != 0:
        raise ctl.SeedboxError("débannissement impossible : " + (err or out).strip()[-160:])
    return int(re.sub(r"\D", "", out.strip().splitlines()[-1]) or 0) if out.strip() else 0


def ban(address, jail="sshd"):
    address = clean_address(address)
    check_jail(jail)
    cfg = settings()
    if any(ip_in(address, n) for n in [*ALWAYS, *cfg["ignore"]]):
        raise ctl.SeedboxError(f"{address} fait partie des adresses ignorées : retirez-la d'abord de cette liste")
    code, out, err = client("set", jail, "banip", address)
    if code != 0:
        raise ctl.SeedboxError("bannissement impossible : " + (err or out).strip()[-160:])
    return True
