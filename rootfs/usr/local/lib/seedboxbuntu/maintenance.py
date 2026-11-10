"""SeedBoxBuntu - mises à jour planifiées et durcissement de l'hôte.

Mises à jour
    Sécurité du système : unattended-upgrades, déclenché par les minuteurs apt-daily de
    systemd dont l'heure et les jours viennent de /etc/seedboxbuntu/maintenance.json
    (redémarrage automatique facultatif). Applications : Watchtower, dont l'expression cron
    est tenue à jour dans .env ; un minuteur facultatif met aussi à jour p2p-aggregator.

Durcissement (« seedboxctl harden », rejouable)
    - ufw : refus par défaut en entrée ; seuls SSH, HTTPS/HTTP de Traefik et les ports
      dédiés des applications sont ouverts, plus les ports ajoutés par l'administrateur.
    - Docker publie ses ports en contournant ufw (règles NAT) : la chaîne SB-INGRESS, branchée
      sur DOCKER-USER, refuse toute connexion vers un port publié qui n'est pas autorisé.
    - SSH, sysctl, modules noyau rares, fail2ban : fichiers fournis par l'appliance.
    - « seedboxctl security » audite le résultat (pare-feu, ports en écoute, ports publiés...).
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import seedboxctl as ctl

ROOT = Path(os.environ.get("SB_ROOT", "/"))
LIVE = ROOT == Path("/")          # hors test : les commandes système sont réellement exécutées
SETTINGS_FILE = ctl.ETC / "maintenance.json"
PORTS_FILE = ctl.ETC / "firewall.json"

SSH_PORT = 22
# Applications publiées par Traefik : accès web, puis un port HTTPS dédié pour celles qui ne
# fonctionnent pas dans un sous-chemin (Seerr, Dockge, p2p-aggregator)
APP_PORTS = [80, 443, 5055, 5001, 3000]
CONSOLE_RULE = ("allow", "in", "on", "br-seedbox", "to", "172.30.0.1", "port", "8090", "proto", "tcp")
PACKAGES = ["ufw", "fail2ban", "unattended-upgrades", "smartmontools"]
# Services inutiles sur un serveur : apport (rapports d'erreur), annonces du jour d'Ubuntu
DISABLE = ["apport.service", "motd-news.timer", "ModemManager.service"]

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABEL = dict(zip(DAYS, ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]))
DEFAULTS = {
    "security": {"enabled": True, "days": "daily", "time": "03:30", "scope": "security",
                 "reboot": False, "reboot_time": "04:30"},
    "apps": {"enabled": True, "days": "daily", "time": "03:00", "p2p": False},
}
SCOPES = {"security": "Correctifs de sécurité uniquement", "all": "Sécurité et mises à jour ordinaires d'Ubuntu"}


def at(path):
    return ROOT / str(path).lstrip("/")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def system(*command, check=False):
    """Commande système : sans effet hors de l'appliance (tests)."""
    if not LIVE:
        return subprocess.CompletedProcess(command, 0, "", "")
    try:
        return ctl.run(*command, check=check, capture=True)
    except FileNotFoundError:   # outil absent (système minimal, conteneur de test)
        return subprocess.CompletedProcess(command, 127, "", f"{command[0]} : commande introuvable")


def write_if_changed(path, text, mode=0o644):
    path = Path(path)
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text)
    temp.chmod(mode)
    os.replace(temp, path)
    return True


# ----------------------------------------------------------------------------
#  Réglages des mises à jour
# ----------------------------------------------------------------------------
def check_time(value, label="heure"):
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(value).strip())
    if not match:
        raise ctl.SeedboxError(f"{label} invalide : format HH:MM attendu")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def check_days(value):
    if isinstance(value, (list, tuple)):
        value = ",".join(value)
    value = str(value).strip().lower()
    if value in ("daily", "", "tous", "all"):
        return "daily"
    chosen = [d for d in value.split(",") if d]
    if not chosen or any(d not in DAYS for d in chosen):
        raise ctl.SeedboxError("jours invalides : daily ou une liste parmi " + ",".join(DAYS))
    ordered = [d for d in DAYS if d in chosen]
    return "daily" if len(ordered) == 7 else ",".join(ordered)


def settings():
    stored = {}
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        pass
    merged = {}
    for section, defaults in DEFAULTS.items():
        merged[section] = {**defaults, **{k: v for k, v in (stored.get(section) or {}).items() if k in defaults}}
    return merged


def validate(section, values):
    """Valide un lot de réglages d'une section et renvoie la section complète."""
    current = settings()[section]
    result = dict(current)
    for key, value in values.items():
        if key not in current or value is None:
            continue
        if key in ("enabled", "reboot", "p2p"):
            result[key] = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "oui", "yes")
        elif key == "days":
            result[key] = check_days(value)
        elif key in ("time", "reboot_time"):
            result[key] = check_time(value, "heure de redémarrage" if key == "reboot_time" else "heure")
        elif key == "scope":
            if value not in SCOPES:
                raise ctl.SeedboxError("périmètre inconnu : " + ", ".join(SCOPES))
            result[key] = value
    return result


def calendar(days, hhmm, minutes=0):
    """Expression OnCalendar de systemd ; minutes décale l'heure (négatif : plus tôt)."""
    hour, minute = map(int, hhmm.split(":"))
    total = (hour * 60 + minute + minutes) % (24 * 60)
    prefix = "" if days == "daily" else ",".join(d.capitalize() for d in days.split(",")) + " "
    return f"{prefix}*-*-* {total // 60:02d}:{total % 60:02d}:00"


def cron(days, hhmm):
    """Expression cron à six champs de Watchtower."""
    hour, minute = map(int, hhmm.split(":"))
    dow = "*" if days == "daily" else ",".join(d.upper() for d in days.split(","))
    return f"0 {minute} {hour} * * {dow}"


def timer_dropin(name, expression, enabled=True):
    path = at(f"/etc/systemd/system/{name}.d/seedboxbuntu.conf")
    text = ("# SeedBoxBuntu : planification choisie dans la console\n[Timer]\nOnCalendar=\n"
            f"OnCalendar={expression}\nRandomizedDelaySec=0\nPersistent=true\n")
    return write_if_changed(path, text)


def apt_config(cfg):
    on = '"1"' if cfg["enabled"] else '"0"'
    lines = ["// SeedBoxBuntu : mises à jour automatiques, réglées dans la console",
             f"APT::Periodic::Update-Package-Lists {on};",
             f"APT::Periodic::Unattended-Upgrade {on};",
             'APT::Periodic::AutocleanInterval "7";',
             'Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";',
             'Unattended-Upgrade::Remove-Unused-Dependencies "true";',
             f'Unattended-Upgrade::Automatic-Reboot "{"true" if cfg["reboot"] else "false"}";',
             'Unattended-Upgrade::Automatic-Reboot-WithUsers "true";',
             f'Unattended-Upgrade::Automatic-Reboot-Time "{cfg["reboot_time"]}";']
    if cfg["scope"] == "all":
        lines.append('Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-updates";')
    return "\n".join(lines) + "\n"


def apply_security_updates(cfg=None):
    cfg = cfg or settings()["security"]
    changed = write_if_changed(at("/etc/apt/apt.conf.d/52seedboxbuntu-updates"), apt_config(cfg))
    # Les listes de paquets sont rafraîchies une demi-heure avant l'installation
    changed |= timer_dropin("apt-daily-upgrade.timer", calendar(cfg["days"], cfg["time"]))
    changed |= timer_dropin("apt-daily.timer", calendar(cfg["days"], cfg["time"], -30))
    if changed:
        system("systemctl", "daemon-reload")
        system("systemctl", "restart", "apt-daily.timer", "apt-daily-upgrade.timer")
    return changed


def apply_apps(cfg=None):
    cfg = cfg or settings()["apps"]
    changed = timer_dropin("seedbox-autoupdate.timer", calendar(cfg["days"], cfg["time"], 15))
    if changed:
        system("systemctl", "daemon-reload")
        system("systemctl", "restart", "seedbox-autoupdate.timer")
    if not ctl.ENV_FILE.exists():
        return changed
    env = ctl.env_read()
    profiles = set(filter(None, env.get("COMPOSE_PROFILES", "").split(",")))
    (profiles.add if cfg["enabled"] else profiles.discard)("autoupdate")
    updates = {"WATCHTOWER_SCHEDULE": cron(cfg["days"], cfg["time"]), "COMPOSE_PROFILES": ",".join(sorted(profiles))}
    if any(env.get(k) != v for k, v in updates.items()):
        ctl.env_set(**updates)
        changed = True
        if (ctl.STATE / "deployed").exists() and LIVE:
            if cfg["enabled"]:
                ctl.compose("up", "-d", "watchtower", capture=True)
            else:
                ctl.run("docker", "rm", "-f", "watchtower", check=False, capture=True)
    return changed


def set_updates(security=None, apps=None):
    """Enregistre et applique les réglages ; une saisie invalide ne modifie rien."""
    current = settings()
    new = {"security": validate("security", security or {}), "apps": validate("apps", apps or {})}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({**current, **new}, indent=2) + "\n")
    apply_security_updates(new["security"])
    apply_apps(new["apps"])
    return new


def apply_updates():
    cfg = settings()
    apply_security_updates(cfg["security"])
    apply_apps(cfg["apps"])


def timer_value(unit, prop):
    out = system("systemctl", "show", unit, "-p", prop, "--value").stdout.strip()
    return "" if out in ("", "n/a", "0") else out


def updates_overview():
    cfg = settings()
    reboot = at("/run/reboot-required")
    packages = []
    try:
        packages = at("/run/reboot-required.pkgs").read_text().split()
    except OSError:
        pass
    return {"settings": cfg, "days": [{"id": d, "label": DAY_LABEL[d]} for d in DAYS],
            "scopes": SCOPES,
            "next": {"security": timer_value("apt-daily-upgrade.timer", "NextElapseUSecRealtime") if cfg["security"]["enabled"] else "",
                     "apps": timer_value("seedbox-autoupdate.timer", "NextElapseUSecRealtime") if cfg["apps"]["enabled"] else ""},
            "reboot_required": reboot.exists(), "reboot_packages": packages[:8]}


def run_security_updates():
    """Installe les correctifs maintenant (la planification reste inchangée)."""
    say("mise à jour des listes de paquets")
    ctl.run("apt-get", "update", "-q")
    say("installation des mises à jour automatiques (unattended-upgrade)")
    ctl.run("unattended-upgrade", "-v")
    if at("/run/reboot-required").exists():
        say("un redémarrage du serveur est nécessaire pour terminer l'installation")


def run_app_updates():
    """Télécharge les images plus récentes et recrée les conteneurs concernés."""
    versions = ctl.versions()
    try:
        versions.snapshot_all()           # permet de revenir à la version précédente
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"versions précédentes non gardées : {error}")
    pinned = sorted(versions.pins_read())
    if pinned:
        say("services épinglés, non mis à jour : " + ", ".join(pinned))
    say("téléchargement des images")
    ctl.compose("pull", "--ignore-pull-failures", "--quiet", *versions.unpinned_services())
    say("recréation des conteneurs mis à jour")
    ctl.compose("up", "-d", "--remove-orphans")
    ctl.run("docker", "image", "prune", "-f", check=False, capture=True)
    say("applications à jour")


def autoupdate():
    """Minuteur seedbox-autoupdate : p2p-aggregator, dont l'image est construite sur place."""
    if not settings()["apps"]["p2p"]:
        return
    status = ctl.p2p_status()
    if not status.get("token"):
        say("p2p-aggregator : jeton GitHub absent, mise à jour automatique impossible")
        return
    if status.get("update_available"):
        say(f"p2p-aggregator : mise à jour vers {status.get('latest')}")
        ctl.p2p_update()
    else:
        say("p2p-aggregator est à jour")


# ----------------------------------------------------------------------------
#  Pare-feu
# ----------------------------------------------------------------------------
def extra_ports():
    try:
        data = json.loads(PORTS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    return {proto: sorted({int(p) for p in data.get(proto, []) if str(p).isdigit()}) for proto in ("tcp", "udp")}


def peer_port():
    """Port de partage de qBittorrent, publié seulement en mode direct (en mode VPN, le tunnel
    et la redirection de port du fournisseur s'en chargent)."""
    if ctl.qbit_mode() != "direct":
        return None
    value = ctl.env_read().get("QBIT_PEER_PORT", "6881")
    return int(value) if value.isdigit() else 6881


def allowed_ports():
    extra = extra_ports()
    port = peer_port()
    tcp = set(APP_PORTS) | set(extra["tcp"]) | ({port} if port else set())
    udp = set(extra["udp"]) | ({port} if port else set())
    return {"tcp": sorted(tcp), "udp": sorted(udp)}


def refresh_firewall():
    """Après un changement de mode de qBittorrent : ufw et garde Docker suivent le port de partage."""
    firewall()
    guard()


def parse_ports(text):
    """« 8080, 51413/udp » -> {"tcp": [8080], "udp": [51413]}."""
    result = {"tcp": set(), "udp": set()}
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d{1,5})(?:/(tcp|udp))?", token.lower())
        if not match or not 1 <= int(match.group(1)) <= 65535:
            raise ctl.SeedboxError(f"port invalide : {token} (exemple : 8080 ou 51413/udp)")
        port, proto = int(match.group(1)), match.group(2) or "tcp"
        if proto == "tcp" and port in (SSH_PORT, *APP_PORTS):
            continue   # déjà ouvert par l'appliance
        result[proto].add(port)
    return {proto: sorted(ports) for proto, ports in result.items()}


def set_extra_ports(text):
    ports = parse_ports(text)
    PORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORTS_FILE.write_text(json.dumps(ports) + "\n")
    firewall()
    guard()
    return ports


def firewall_rules():
    rules = [("limit", f"{SSH_PORT}/tcp")]   # limit : ralentit la force brute sur SSH
    ports = allowed_ports()
    rules += [("allow", f"{port}/tcp") for port in ports["tcp"]]
    rules += [("allow", f"{port}/udp") for port in ports["udp"]]
    rules.append(CONSOLE_RULE)   # Traefik (conteneur) doit joindre la console, service de l'hôte
    return rules


def ufw_added():
    out = system("ufw", "show", "added").stdout
    return [" ".join(line.split()[1:]) for line in out.splitlines() if line.startswith("ufw ")]


def firewall(force=False):
    """ufw : tout est refusé en entrée sauf la liste ; ne touche à rien si elle est déjà appliquée."""
    if not LIVE or not shutil.which("ufw"):
        return False
    wanted = [" ".join(rule) for rule in firewall_rules()]
    status = system("ufw", "status", "verbose").stdout
    if (not force and "Status: active" in status and "deny (incoming)" in status
            and sorted(ufw_added()) == sorted(wanted)):
        return False
    say("pare-feu : application des règles")
    ctl.run("ufw", "--force", "reset", capture=True)
    ctl.run("ufw", "default", "deny", "incoming", capture=True)
    ctl.run("ufw", "default", "allow", "outgoing", capture=True)
    for rule in firewall_rules():
        ctl.run("ufw", *rule, capture=True)
    ctl.run("ufw", "logging", "low", capture=True)
    ctl.run("ufw", "--force", "enable", capture=True)
    return True


def iptables(*args, check=True):
    try:
        return ctl.run("iptables", "-w", "10", *args, check=check, capture=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", "iptables : commande introuvable")


def guard():
    """Docker publie ses ports par NAT, avant ufw : sans ce garde, un conteneur qui publie un
    port l'ouvre à tout le réseau. Les connexions vers un port publié (état DNAT) non autorisé
    sont refusées dans DOCKER-USER, avant les règles de Docker."""
    if not LIVE or not shutil.which("iptables"):
        return False
    if iptables("-S", "DOCKER-USER", check=False).returncode != 0:
        say("garde Docker : chaîne DOCKER-USER absente (Docker n'est pas démarré), appliqué à son démarrage")
        return False
    iptables("-N", "SB-INGRESS", check=False)
    iptables("-F", "SB-INGRESS")
    ports = allowed_ports()
    for proto in ("tcp", "udp"):
        for port in ports[proto]:
            iptables("-A", "SB-INGRESS", "-p", proto, "-m", "conntrack", "--ctorigdstport", str(port), "-j", "RETURN")
    iptables("-A", "SB-INGRESS", "-j", "DROP")
    hooks = (["-m", "conntrack", "--ctstate", "DNAT", "-j", "SB-INGRESS"],
             ["-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "RETURN"])
    for hook in hooks:
        while iptables("-D", "DOCKER-USER", *hook, check=False).returncode == 0:
            pass
    for hook in hooks:   # le dernier inséré passe en premier : connexions établies d'abord
        iptables("-I", "DOCKER-USER", "1", *hook)
    return True


# ----------------------------------------------------------------------------
#  Application du durcissement
# ----------------------------------------------------------------------------
def ensure_packages():
    if not LIVE:
        return []
    missing = [p for p in PACKAGES if system("dpkg-query", "-W", "-f=${Status}", p).stdout.find("install ok installed") < 0]
    if missing:
        say("installation des paquets de sécurité : " + ", ".join(missing))
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        ctl.run("apt-get", "update", "-q", env=env, capture=True)
        ctl.run("apt-get", "install", "-y", "-q", "--no-install-recommends", *missing, env=env, capture=True)
    return missing


def docker_daemon():
    """/etc/docker/daemon.json : live-restore (les conteneurs survivent à une mise à jour de
    Docker) et rotation des journaux des conteneurs créés hors de l'appliance."""
    path = at("/etc/docker/daemon.json")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    wanted = {"live-restore": True, "log-driver": "json-file", "log-opts": {"max-size": "20m", "max-file": "3"}}
    merged = {**wanted, **{k: v for k, v in data.items() if k not in wanted}}
    if write_if_changed(path, json.dumps(merged, indent=2) + "\n"):
        system("systemctl", "reload", "docker")
        return True
    return False


def harden():
    """Applique tout le durcissement ; chaque étape est rejouable et indépendante."""
    errors = []
    steps = [("paquets", ensure_packages), ("pare-feu", firewall), ("Docker", docker_daemon),
             ("garde des ports publiés par Docker", guard), ("mises à jour planifiées", apply_updates),
             ("noyau et SSH", kernel_and_ssh), ("services inutiles", disable_services)]
    for label, step in steps:
        try:
            step()
            say(f"durcissement : {label} : OK")
        except (ctl.SeedboxError, OSError, ValueError) as error:
            errors.append(f"{label} : {error}")
            say(f"durcissement : {label} : ÉCHEC : {error}")
    return errors


def kernel_and_ssh():
    if not LIVE:
        return
    system("sysctl", "--system")
    if shutil.which("sshd"):
        test = system("sshd", "-t")
        if test.returncode == 0:
            system("systemctl", "reload", "ssh")
        else:
            # Une configuration refusée par sshd ne doit jamais couper l'accès à distance
            conf = Path("/etc/ssh/sshd_config.d/50-seedboxbuntu.conf")
            conf.unlink(missing_ok=True)
            say("SSH : configuration durcie refusée par sshd, retirée : " + (test.stderr or "").strip()[-200:])
    if shutil.which("fail2ban-client"):
        system("systemctl", "enable", "--now", "fail2ban")
        system("systemctl", "restart", "fail2ban")


def disable_services():
    for unit in DISABLE:
        if system("systemctl", "cat", unit).returncode == 0:
            system("systemctl", "disable", "--now", unit)


# ----------------------------------------------------------------------------
#  Audit
# ----------------------------------------------------------------------------
def item(identifier, label, state, detail=""):
    return {"id": identifier, "label": label, "state": state, "detail": detail}


def listening():
    """Sockets en écoute accessibles hors de la machine : (protocole, port)."""
    found = set()
    for line in system("ss", "-H", "-lntu").stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        address, _, port = parts[4].rpartition(":")
        if address.strip("[]").startswith("127.") or address.strip("[]") in ("::1", ""):
            continue
        if address.startswith("127.0.0.53") or "%lo" in address:
            continue
        if port.isdigit():
            found.add((parts[0], int(port)))
    return sorted(found)


def published_ports():
    """Ports hôte publiés par les conteneurs : {port: [conteneurs]}."""
    result = {}
    out = system("docker", "ps", "--format", "{{.Names}}\t{{.Ports}}").stdout
    for line in out.splitlines():
        name, _, ports = line.partition("\t")
        for match in re.finditer(r"(?:[\d.]+|\[::\]):(\d+)(?:-(\d+))?->\d+(?:-\d+)?/(tcp|udp)", ports):
            low, high = int(match.group(1)), int(match.group(2) or match.group(1))
            for port in range(low, high + 1):
                result.setdefault((match.group(3), port), set()).add(name)
    return result


def sshd_effective():
    out = system("sshd", "-T").stdout
    return dict(line.split(None, 1) for line in out.splitlines() if " " in line)


def security_audit():
    if not LIVE:
        return []
    checks = []
    allowed = allowed_ports()
    # Pare-feu
    status = system("ufw", "status", "verbose").stdout
    active = "Status: active" in status
    checks.append(item("ufw", "Pare-feu actif, entrées refusées par défaut",
                       "ok" if active and "deny (incoming)" in status else "fail",
                       "" if active else "ufw est inactif"))
    wanted = sorted(" ".join(rule) for rule in firewall_rules())
    added = sorted(ufw_added())
    checks.append(item("ufw-rules", "Seuls les ports nécessaires sont ouverts", "ok" if added == wanted else "warn",
                       "ports : " + ", ".join(f"{p}/{proto}" for proto in ("tcp", "udp") for p in allowed[proto])
                       if added == wanted else "règles différentes de celles de l'appliance : « seedboxctl harden »"))
    # Ports en écoute
    exposed = [(proto, port) for proto, port in listening()
               if port not in allowed[proto[:3]] and not (proto == "tcp" and port == SSH_PORT)]
    # Informatif : un service en écoute hors liste n'est pas joignable, ufw l'arrête
    checks.append(item("listen", "Services en écoute hors liste : tous bloqués par le pare-feu",
                       "ok" if active else "fail",
                       ("en écoute, non joignables : " + ", ".join(f"{p}/{proto}" for proto, p in exposed)) if exposed
                       else "aucun port hors liste"))
    # Ports publiés par Docker
    rogue = {key: names for key, names in published_ports().items() if key[1] not in allowed[key[0]]}
    guard_ok = iptables("-C", "DOCKER-USER", "-m", "conntrack", "--ctstate", "DNAT", "-j", "SB-INGRESS", check=False).returncode == 0
    checks.append(item("docker-guard", "Garde Docker : ports publiés par les conteneurs filtrés",
                       "ok" if guard_ok else "fail", "" if guard_ok else "chaîne SB-INGRESS absente : « seedboxctl harden »"))
    checks.append(item("docker-ports", "Conteneurs : aucun port publié hors liste", "ok" if not rogue else "warn",
                       "" if not rogue else "bloqués : " + ", ".join(f"{p}/{proto} ({', '.join(sorted(n))})"
                                                                     for (proto, p), n in sorted(rogue.items()))))
    # SSH
    effective = sshd_effective()
    wanted_ssh = {"permitrootlogin": "no", "x11forwarding": "no", "permitemptypasswords": "no",
                  "allowtcpforwarding": "no", "allowagentforwarding": "no", "maxauthtries": "4"}
    bad = [f"{k} = {effective.get(k, '?')}" for k, v in wanted_ssh.items() if effective.get(k) != v]
    checks.append(item("ssh", "SSH durci (pas de root, pas de redirection, 4 essais)", "ok" if not bad else "fail",
                       ", ".join(bad)))
    f2b = system("systemctl", "is-active", "fail2ban").stdout.strip()
    jail = system("fail2ban-client", "status", "sshd").stdout
    checks.append(item("fail2ban", "fail2ban protège SSH", "ok" if f2b == "active" and "sshd" in jail else "fail",
                       "" if f2b == "active" else f"service {f2b or 'absent'}"))
    # Mises à jour
    cfg = settings()
    apt = system("apt-config", "dump").stdout
    unattended = 'APT::Periodic::Unattended-Upgrade "1"' in apt
    checks.append(item("updates", "Mises à jour de sécurité planifiées",
                       "ok" if unattended == cfg["security"]["enabled"] and unattended else "warn",
                       "désactivées dans les paramètres" if not cfg["security"]["enabled"] else ""))
    reboot = at("/run/reboot-required").exists()
    checks.append(item("reboot", "Aucun redémarrage en attente", "warn" if reboot else "ok",
                       "redémarrage nécessaire pour appliquer des correctifs" if reboot else ""))
    # Noyau
    wanted_sysctl = {"kernel.kptr_restrict": "2", "kernel.dmesg_restrict": "1", "kernel.randomize_va_space": "2",
                     "kernel.unprivileged_bpf_disabled": "1", "net.ipv4.tcp_syncookies": "1",
                     "net.ipv4.conf.all.accept_redirects": "0", "net.ipv4.conf.all.log_martians": "1",
                     "kernel.sysrq": "0", "fs.suid_dumpable": "0"}
    off = []
    for key, value in wanted_sysctl.items():
        current = system("sysctl", "-n", key).stdout.strip()
        if current != value:
            off.append(f"{key} = {current or '?'}")
    checks.append(item("sysctl", "Noyau durci (sysctl)", "ok" if not off else "warn", ", ".join(off)))
    aa = system("aa-status", "--enabled").returncode == 0
    checks.append(item("apparmor", "AppArmor actif", "ok" if aa else "warn"))
    live = False
    try:
        live = json.loads(at("/etc/docker/daemon.json").read_text()).get("live-restore") is True
    except (OSError, ValueError):
        pass
    checks.append(item("docker", "Docker : conteneurs conservés pendant une mise à jour du moteur", "ok" if live else "warn"))
    return checks


def security_overview():
    checks = security_audit()
    order = {"fail": 0, "warn": 1, "ok": 2}
    return {"checks": checks, "ports": {"base": {"ssh": SSH_PORT, "web": APP_PORTS}, "extra": extra_ports()},
            "score": {state: sum(1 for c in checks if c["state"] == state) for state in order}}
