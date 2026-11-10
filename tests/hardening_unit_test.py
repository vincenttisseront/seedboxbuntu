"""Mises à jour planifiées et durcissement : logique pure et fichiers produits (sans toucher au système)."""
import os as _os
SRC = _os.environ.get("SB_SRC") or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbroot"))
os.environ.update({"SB_ROOT": str(root), "SB_ETC": str(root / "etc/seedboxbuntu"), "SB_STATE": str(root / "var/lib/seedboxbuntu"),
                   "SB_BASE": str(root / "opt/seedbox"), "SB_SHARE": SRC})
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402
m = ctl.maintenance()
results = []


def check(label, condition, detail=""):
    results.append(bool(condition))
    print(f"[{'OK' if condition else 'ECHEC'}] {label} {detail}"[:170])


def raises(function, *args):
    try:
        function(*args)
    except ctl.SeedboxError as error:
        return str(error)
    return None


# --- validation
check("heure 3:5 refusée", raises(m.check_time, "3:5"))
check("heure 25:00 refusée", raises(m.check_time, "25:00"))
check("heure 3:30 -> 03:30", m.check_time("3:30") == "03:30")
check("jours tous -> daily", m.check_days("mon,tue,wed,thu,fri,sat,sun") == "daily")
check("jours triés", m.check_days("thu,mon") == "mon,thu")
check("jour inconnu refusé", raises(m.check_days, "mon,xyz"))
check("jours vides refusés", raises(m.check_days, "x"))
check("périmètre inconnu refusé", raises(m.validate, "security", {"scope": "tout"}))
# --- expressions
check("calendrier quotidien", m.calendar("daily", "03:30") == "*-*-* 03:30:00")
check("calendrier jours", m.calendar("mon,thu", "03:30") == "Mon,Thu *-*-* 03:30:00")
check("calendrier -30 min", m.calendar("daily", "03:30", -30) == "*-*-* 03:00:00")
check("calendrier passage minuit", m.calendar("daily", "00:10", -30) == "*-*-* 23:40:00")
check("cron Watchtower quotidien", m.cron("daily", "03:00") == "0 0 3 * * *")
check("cron Watchtower jours", m.cron("mon,thu", "04:15") == "0 15 4 * * MON,THU")
if shutil.which("systemd-analyze"):
    for expression in (m.calendar("daily", "03:30"), m.calendar("mon,thu", "23:59", 15)):
        ok = subprocess.run(["systemd-analyze", "calendar", expression], capture_output=True).returncode == 0
        check(f"systemd accepte « {expression} »", ok)
# --- réglages par défaut puis enregistrement
check("réglages par défaut", m.settings()["security"]["time"] == "03:30" and m.settings()["apps"]["enabled"] is True)
m.set_updates({"enabled": True, "days": "mon,thu", "time": "2:15", "reboot": "true", "reboot_time": "5:00", "scope": "all"},
              {"enabled": False, "p2p": True, "time": "04:45"})
saved = json.loads((root / "etc/seedboxbuntu/maintenance.json").read_text())
check("réglages enregistrés", saved["security"]["days"] == "mon,thu" and saved["security"]["time"] == "02:15" and saved["apps"]["p2p"] is True)
apt = (root / "etc/apt/apt.conf.d/52seedboxbuntu-updates").read_text()
check("apt : mises à jour actives", 'Unattended-Upgrade "1"' in apt and 'Update-Package-Lists "1"' in apt)
check("apt : redémarrage 05:00", 'Automatic-Reboot "true"' in apt and 'Automatic-Reboot-Time "05:00"' in apt)
check("apt : mises à jour ordinaires ajoutées", "-updates" in apt)
drop = (root / "etc/systemd/system/apt-daily-upgrade.timer.d/seedboxbuntu.conf").read_text()
check("minuteur : Mon,Thu 02:15", "OnCalendar=Mon,Thu *-*-* 02:15:00" in drop and "OnCalendar=\n" in drop)
lists = (root / "etc/systemd/system/apt-daily.timer.d/seedboxbuntu.conf").read_text()
check("listes de paquets 30 min plus tôt", "OnCalendar=Mon,Thu *-*-* 01:45:00" in lists)
auto = (root / "etc/systemd/system/seedbox-autoupdate.timer.d/seedboxbuntu.conf").read_text()
check("minuteur p2p 15 min après les applications", "OnCalendar=*-*-* 05:00:00" in auto)
m.set_updates({"enabled": False}, {})
apt = (root / "etc/apt/apt.conf.d/52seedboxbuntu-updates").read_text()
check("apt : désactivé", 'Unattended-Upgrade "0"' in apt and 'Update-Package-Lists "0"' in apt)
check("saisie invalide : rien n'est modifié", raises(m.set_updates, {"time": "99:99"}, {}) and m.settings()["security"]["enabled"] is False)
# --- ports
check("ports : liste", m.parse_ports("8080, 51413/udp 9000") == {"tcp": [8080, 9000], "udp": [51413]})
check("ports : déjà ouverts ignorés", m.parse_ports("22 443 5055") == {"tcp": [], "udp": []})
check("ports : 0 refusé", raises(m.parse_ports, "0"))
check("ports : 70000 refusé", raises(m.parse_ports, "70000"))
check("ports : texte refusé", raises(m.parse_ports, "8080;rm"))
check("ports : protocole refusé", raises(m.parse_ports, "80/icmp"))
m.set_extra_ports("8080, 51413/udp")
check("ports : autorisés = base + ajoutés", m.allowed_ports() == {"tcp": [80, 443, 3000, 5001, 5055, 8080], "udp": [51413]})
rules = [" ".join(r) for r in m.firewall_rules()]
check("pare-feu : SSH limité", rules[0] == "limit 22/tcp")
check("pare-feu : console pour Traefik", rules[-1] == "allow in on br-seedbox to 172.30.0.1 port 8090 proto tcp")
check("pare-feu : 51413/udp", "allow 51413/udp" in rules)
check("pare-feu : aucun autre port TCP/UDP", sorted(r for r in rules if r.startswith("allow ") and "/" in r) ==
      sorted([f"allow {p}/tcp" for p in (80, 443, 3000, 5001, 5055, 8080)] + ["allow 51413/udp"]))
# --- fichiers statiques de l'appliance
rootfs = Path(SRC + "/rootfs")
sshd = (rootfs / "etc/ssh/sshd_config.d/50-seedboxbuntu.conf").read_text()
for line in ("PermitRootLogin no", "MaxAuthTries 4", "AllowTcpForwarding no", "X11Forwarding no", "PermitEmptyPasswords no"):
    check(f"sshd : {line}", line in sshd)
jail = (rootfs / "etc/fail2ban/jail.d/seedboxbuntu.local").read_text()
check("fail2ban : jail sshd actif", "[sshd]" in jail and "enabled = true" in jail)
env_t = (Path(SRC + "/stack/env.template")).read_text()
check("modèle .env : Watchtower planifié et profil", "WATCHTOWER_SCHEDULE=" in env_t and "COMPOSE_PROFILES=autoupdate" in env_t)
shutil.rmtree(root, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} contrôles réussis")
sys.exit(0 if all(results) else 1)
