"""fail2ban dans la console : état des jails, débannir, bannir, adresses ignorées, seuils (écrits puis testés, annulés si refusés),
avec un faux fail2ban-client."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbbans"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
os.environ["SB_F2B_DIR"] = str(root / "fail2ban-jail.d")
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

bans = ctl.bans()
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


world = {"active": True, "jails": {"sshd": {"203.0.113.7": ("2026-11-16 18:00:00", 3600), "198.51.100.4": ("2026-11-17 17:00:00", 86400)}, "recidive": {}},
         "test_ok": True, "calls": []}


def fake_client(*args):
    world["calls"].append(args)
    if args == ("--version",):
        return 0, "Fail2Ban v1.0.2", ""
    if args == ("status",):
        return 0, f"Status\n|- Number of jail:\t{len(world['jails'])}\n`- Jail list:\t{', '.join(world['jails'])}\n", ""
    if args[0] == "status":
        jail = world["jails"].get(args[1])
        if jail is None:
            return 255, "", "Sorry but the jail '%s' does not exist" % args[1]
        return 0, ("Status for the jail: %s\n|- Filter\n|  |- Currently failed:\t2\n|  |- Total failed:\t17\n|  `- Journal matches:\t_SYSTEMD_UNIT=sshd.service\n"
                   "`- Actions\n   |- Currently banned:\t%d\n   |- Total banned:\t9\n   `- Banned IP list:\t%s\n") % (args[1], len(jail), " ".join(jail)), ""
    if args[0] == "get" and args[2:] == ("banip", "--with-time"):
        return 0, "\n".join(f"{ip} \t{end} + {sec} = {end}" for ip, (end, sec) in world["jails"][args[1]].items()) + "\n", ""
    if args[0] == "set" and args[2] == "unbanip":
        return 0, "1" if world["jails"][args[1]].pop(args[3], None) else "0", ""
    if args[0] == "set" and args[2] == "banip":
        world["jails"][args[1]][args[3]] = ("2026-11-16 19:00:00", 3600)
        return 0, "1", ""
    if args == ("unban", "--all"):
        count = sum(len(j) for j in world["jails"].values())
        for j in world["jails"].values():
            j.clear()
        return 0, str(count), ""
    if args == ("--test",):
        return (0, "OK: configuration test is successful", "") if world["test_ok"] else (255, "", "ERROR: option invalide")
    if args == ("reload",):
        return 0, "OK", ""
    return 255, "", "inconnu " + " ".join(args)


bans.client = fake_client
bans.systemctl = lambda *a: (0 if world["active"] else 3, "active" if world["active"] else "inactive")
journal = ("2026-11-16T16:05:01+0100 seedbox fail2ban-server[812]: 2026-11-16 16:05:01,123 fail2ban.actions [812]: NOTICE  [sshd] Ban 203.0.113.7\n"
           "2026-11-16T16:40:00+0100 seedbox fail2ban-server[812]: 2026-11-16 16:40:00,456 fail2ban.actions [812]: NOTICE  [sshd] Unban 192.0.2.9\n"
           "2026-11-16T16:41:00+0100 seedbox systemd[1]: bruit sans rapport\n")
real_run = ctl.run
ctl.run = lambda *a, **k: SimpleNamespace(returncode=0, stdout=journal, stderr="") if a and a[0] == "journalctl" else real_run(*a, **k)

# ------------------------------------------------------------------ état
view = bans.overview("203.0.113.7")
check("état : actif, 2 jails, 2 adresses bannies", view["active"] and [j["name"] for j in view["jails"]] == ["sshd", "recidive"] and view["total_banned"] == 2, str(view["total_banned"]))
sshd = view["jails"][0]
check("jail sshd : échecs et bannissements lus", (sshd["failed"], sshd["failed_total"], sshd["banned_total"], len(sshd["banned"])) == (2, 17, 9, 2), str(sshd))
check("adresse bannie : fin et durée lues", sshd["banned"][0]["address"] == "203.0.113.7" and sshd["banned"][0]["until"] and sshd["banned"][0]["seconds"] == 3600, str(sshd["banned"][0]))
check("historique : seulement bannissements et débannissements, du plus récent au plus ancien", [(r["action"], r["address"]) for r in view["recent"]] == [("Unban", "192.0.2.9"), ("Ban", "203.0.113.7")], str(view["recent"]))
check("votre adresse : bannie et non ignorée signalées", view["me_banned"] and not view["me_ignored"])
world["active"] = False
check("fail2ban arrêté : état sans jails", not bans.overview()["active"] and bans.overview()["jails"] == [])
world["active"] = True

# ------------------------------------------------------------------ débannir, bannir
check("débannir : un jail", bans.unban("203.0.113.7", "sshd") == 1 and "203.0.113.7" not in world["jails"]["sshd"])
check("débannir une adresse non bannie : message clair", "n'est pas banni" in (fails(bans.unban, "203.0.113.7") or ""))
check("adresse invalide refusée (pas d'injection)", "invalide" in (fails(bans.unban, "1.2.3.4; reboot") or "") and "invalide" in (fails(bans.ban, "pas-une-ip") or ""))
check("jail invalide refusé", "invalide" in (fails(bans.unban, "198.51.100.4", "../x") or "") and "inconnu" in (fails(bans.ban, "192.0.2.50", "nexiste") or ""))
check("bannir à la main", bans.ban("192.0.2.50") and "192.0.2.50" in world["jails"]["sshd"])
check("débannir de tous les jails", bans.unban("192.0.2.50") == 1)
check("tout débannir", bans.unban_all() == 1 and not world["jails"]["sshd"])

# ------------------------------------------------------------------ adresses ignorées
world["jails"]["sshd"]["203.0.113.7"] = ("2026-11-16 18:00:00", 3600)
cfg = bans.ignore("203.0.113.7")
text = bans.CONF_FILE.read_text()
check("ignorer : écrit dans le fichier à part, sans toucher à celui de l'appliance", "203.0.113.7" in text and "127.0.0.1/8 ::1 203.0.113.7" in text and bans.CONF_FILE.name.startswith("zz-"))
check("ignorer une adresse bannie : débannie aussitôt", "203.0.113.7" not in world["jails"]["sshd"])
check("ignorer un réseau", "192.168.1.0/24" in bans.ignore("192.168.1.0/24")["ignore"])
check("ignorer : valeur invalide refusée", "invalide" in (fails(bans.ignore, "pas un réseau") or ""))
check("bannir une adresse ignorée : refusé", "ignorées" in (fails(bans.ban, "192.168.1.55") or ""))
check("votre adresse ignorée est reconnue", bans.overview("192.168.1.77")["me_ignored"])
check("retirer des adresses ignorées", "192.168.1.0/24" not in bans.unignore("192.168.1.0/24")["ignore"] and "inconnu" not in (fails(bans.unignore, "203.0.113.7") or "x"))
check("retirer une adresse absente : message clair", "pas dans les adresses ignorées" in (fails(bans.unignore, "10.9.9.9") or ""))

# ------------------------------------------------------------------ seuils
saved = bans.save(ban_minutes=120, window_minutes=15, retries=3, increment=False, max_hours=48)
text = bans.CONF_FILE.read_text()
check("seuils écrits au format fail2ban", all(x in text for x in ("bantime = 120m", "findtime = 15m", "maxretry = 3", "bantime.increment = false", "bantime.maxtime = 48h")), text)
check("seuils relus", bans.settings()["retries"] == 3 and not bans.settings()["increment"])
for field, value in (("retries", 1), ("ban_minutes", 0), ("max_hours", 99999), ("window_minutes", "abc")):
    check(f"seuil {field} = {value} refusé", fails(bans.save, **{field: value}) is not None)
before = bans.CONF_FILE.read_text()
world["test_ok"] = False
check("réglage refusé par fail2ban : message et ancienne configuration conservée", "refuse ces réglages" in (fails(bans.save, retries=9) or "") and bans.CONF_FILE.read_text() == before and bans.settings()["retries"] == 3)
world["test_ok"] = True
calls = world["calls"]
check("un rechargement suit chaque réglage accepté, jamais un refus", calls.count(("reload",)) >= 5 and calls.index(("--test",)) < calls.index(("reload",)))
check("fichier de l'appliance intact (jail sshd actif fourni à part)", "[sshd]" in (Path(SRC) / "rootfs/etc/fail2ban/jail.d/seedboxbuntu.local").read_text())

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
