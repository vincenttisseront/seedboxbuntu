"""Réseau de l'appliance depuis la console (validation, netplan, retour arrière) et lecture de la configuration SSH (sans réseau)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbnet"))
for key, folder in {"SB_ROOT": "", "SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st",
                    "SB_SHARES": "mnt/shares", "SB_SYS": "sys"}.items():
    os.environ[key] = str(root / folder) if folder else str(root)
    (root / folder).mkdir(parents=True, exist_ok=True)
for folder in ("etc/netplan", "etc/ssh/sshd_config.d", "run/systemd/resolve", "sys/class/net/enp1s0"):
    (root / folder).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import netconf  # noqa: E402
import maintenance  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def raises(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ctl.SeedboxError as error:
        return str(error)
    return None


# --- validation ---------------------------------------------------------------------------------------------------
ok = netconf.validate("enp1s0", "static", "192.168.1.50/24", "192.168.1.1", ["1.1.1.1", "9.9.9.9", "1.1.1.1"])
check("adresse fixe valide, DNS dédoublonnés", ok["address"] == "192.168.1.50/24" and ok["dns"] == ["1.1.1.1", "9.9.9.9"], ok)
check("DHCP : aucun autre champ retenu", netconf.validate("enp1s0", "dhcp", "x", "y", ["z"]) == {"iface": "enp1s0", "mode": "dhcp", "address": "", "gateway": "", "dns": []})
for label, args in {"adresse sans masque": ("static", "192.168.1.50", "", ()), "adresse absurde": ("static", "999.1.1.1/24", "", ()),
                    "adresse du réseau": ("static", "192.168.1.0/24", "", ()), "diffusion": ("static", "192.168.1.255/24", "", ()),
                    "masque /31": ("static", "192.168.1.50/31", "", ()), "boucle locale": ("static", "127.0.0.2/8", "", ()),
                    "IPv6": ("static", "fe80::1/64", "", ()), "passerelle hors réseau": ("static", "192.168.1.50/24", "10.0.0.1", ()),
                    "passerelle = adresse": ("static", "192.168.1.50/24", "192.168.1.50", ()),
                    "DNS invalide": ("static", "192.168.1.50/24", "192.168.1.1", ("pas-une-ip",)),
                    "cinq DNS": ("static", "192.168.1.50/24", "192.168.1.1", ("1.1.1.1", "1.0.0.1", "9.9.9.9", "8.8.8.8", "8.8.4.4"))}.items():
    check(f"refusé : {label}", raises(netconf.validate, "enp1s0", *args) is not None)
check("refusé : carte docker0", raises(netconf.validate, "docker0", "dhcp") is not None)
check("refusé : mode inconnu", raises(netconf.validate, "enp1s0", "pppoe") is not None)

# --- texte netplan --------------------------------------------------------------------------------------------------
text = netconf.netplan_text(ok)
check("netplan statique : adresse, route par défaut, DNS", 'addresses: ["192.168.1.50/24"]' in text and 'via: "192.168.1.1"' in text and '"1.1.1.1", "9.9.9.9"' in text and "dhcp4: false" in text, text)
check("netplan DHCP", "dhcp4: true" in netconf.netplan_text(netconf.validate("enp1s0", "dhcp")))

# --- application, confirmation, retour arrière ------------------------------------------------------------------
calls = []
fail = set()


def fake(command, timeout):
    calls.append(tuple(str(c) for c in command))
    name = str(command[0])
    if name in fail:
        return subprocess.CompletedProcess(command, 1, "", f"{name} a échoué")
    return subprocess.CompletedProcess(command, 0, "", "")


netconf.RUN = fake
original = {"50-cloud-init.yaml": "network:\n  version: 2\n  ethernets:\n    enp1s0:\n      dhcp4: true\n", "01-seedboxbuntu.yaml": "# ancien\nnetwork:\n  version: 2\n"}
for name, body in original.items():
    (root / "etc/netplan" / name).write_text(body)
(ctl.ETC).mkdir(parents=True, exist_ok=True)
(ctl.ETC / "network.conf").write_text("NET_IF=enp1s0\nNET_MODE=dhcp\nNET_ADDR=''\nNET_GW=''\nNET_DNS=''\n")
check("configuration enregistrée relue", (netconf.configured() or {}).get("mode") == "dhcp", netconf.configured())

result = netconf.apply("enp1s0", "static", "192.168.1.50/24", "192.168.1.1", "1.1.1.1 9.9.9.9", by="test")
files = sorted(p.name for p in (root / "etc/netplan").glob("*.yaml"))
check("apply : seul 01-seedboxbuntu.yaml reste, avec la nouvelle adresse", files == ["01-seedboxbuntu.yaml"] and "192.168.1.50/24" in (root / "etc/netplan/01-seedboxbuntu.yaml").read_text(), files)
check("apply : fichier en 0600", oct((root / "etc/netplan/01-seedboxbuntu.yaml").stat().st_mode & 0o777) == "0o600")
check("apply : changement en attente avec délai", result["pending"] and 100 <= result["pending"]["seconds_left"] <= 120 and result["address"] == "192.168.1.50", result)
check("apply : netplan generate puis minuterie de retour arrière", ("netplan", "generate") == calls[0][:2] and any(c[0] == "systemd-run" and "--on-active=120s" in c and c[-2:] == ("network", "revert") for c in calls), calls)
check("apply : netplan apply en tâche détachée, ensuite seulement", calls[-1][0] == "detached" and "netplan apply" in calls[-1][1] and "refresh" in calls[-1][1], calls[-1])
check("apply : menu texte à jour", "NET_MODE=static" in (ctl.ETC / "network.conf").read_text() and "NET_ADDR=192.168.1.50/24" in (ctl.ETC / "network.conf").read_text())
check("apply : deuxième changement refusé tant que le premier attend", raises(netconf.apply, "enp1s0", "dhcp") is not None)

calls.clear()
netconf.revert()
restored = {p.name: p.read_text() for p in (root / "etc/netplan").glob("*.yaml")}
check("revert : fichiers netplan d'origine rétablis", restored == original, sorted(restored))
check("revert : plus rien en attente, minuterie arrêtée, netplan réappliqué", netconf.pending() is None and any(c[:2] == ("systemctl", "stop") for c in calls) and any(c[0] == "detached" and "netplan apply" in c[1] for c in calls), calls)
check("revert : menu texte rétabli", "NET_MODE=dhcp" in (ctl.ETC / "network.conf").read_text())
check("revert sans changement en attente : refusé", raises(netconf.revert) is not None)

calls.clear()
netconf.apply("enp1s0", "dhcp")
netconf.confirm()
check("confirm : changement conservé, minuterie arrêtée", netconf.pending() is None and any(c[:2] == ("systemctl", "stop") for c in calls) and "dhcp4: true" in (root / "etc/netplan/01-seedboxbuntu.yaml").read_text())
check("confirm sans changement en attente : refusé", raises(netconf.confirm) is not None)

# --- échecs : rien ne doit rester à moitié fait ------------------------------------------------------------------------
before = {p.name: p.read_text() for p in (root / "etc/netplan").glob("*.yaml")}
fail.add("netplan")
message = raises(netconf.apply, "enp1s0", "static", "192.168.1.50/24", "192.168.1.1", "1.1.1.1")
after = {p.name: p.read_text() for p in (root / "etc/netplan").glob("*.yaml")}
check("netplan generate refusé : fichiers inchangés, rien en attente", message and "netplan refuse" in message and before == after and netconf.pending() is None, message)
fail.discard("netplan")
fail.add("systemd-run")
message = raises(netconf.apply, "enp1s0", "static", "192.168.1.50/24", "192.168.1.1", "1.1.1.1")
after = {p.name: p.read_text() for p in (root / "etc/netplan").glob("*.yaml")}
check("minuterie impossible : on ne coupe rien, fichiers rétablis", message and "retour arrière" in message and before == after and netconf.pending() is None, message)
fail.clear()
check("sauvegardes : cinq au plus", len(list(netconf.BACKUPS.iterdir())) <= 5)

# --- lecture de l'état -------------------------------------------------------------------------------------------------
netconf.RUN = lambda command, timeout: subprocess.CompletedProcess(command, 0, json.dumps(
    [{"ifname": "lo", "operstate": "UNKNOWN", "addr_info": []},
     {"ifname": "docker0", "operstate": "DOWN", "addr_info": []},
     {"ifname": "enp1s0", "operstate": "UP", "address": "52:54:00:aa:bb:cc",
      "addr_info": [{"family": "inet", "local": "192.168.1.50", "prefixlen": 24, "dynamic": True}, {"family": "inet6", "local": "fe80::1", "prefixlen": 64}]}]
    if "addr" in command else [{"gateway": "192.168.1.1", "dev": "enp1s0"}]), "")
(root / "run/systemd/resolve/resolv.conf").write_text("nameserver 192.168.1.1\nnameserver 127.0.0.53\n")
(root / "sys/class/net/enp1s0/speed").write_text("1000\n")
(root / "etc/hostname").write_text("seedbox\n")
state = netconf.overview()
check("état : cartes physiques seulement, adresses IPv4, vitesse", [i["name"] for i in state["interfaces"]] == ["enp1s0"] and state["interfaces"][0]["addresses"] == [{"cidr": "192.168.1.50/24", "dynamic": True}] and state["interfaces"][0]["speed"] == "1000 Mb/s", state["interfaces"])
check("état : passerelle, DNS amont, nom de machine", state["route"]["gateway"] == "192.168.1.1" and state["dns"] == ["192.168.1.1"] and state["hostname"] == "seedbox", state)
netconf.RUN = None
out = subprocess.run([sys.executable, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/seedboxctl.py"), "network", "show"], capture_output=True, text=True, env=os.environ)
check("CLI : seedboxctl network show", out.returncode == 0 and "interfaces" in out.stdout, out.stderr[-200:])

# --- SSH : lecture directe des fichiers quand sshd -T est muet ------------------------------------------------------------
(root / "etc/ssh/sshd_config").write_text("Include /etc/ssh/sshd_config.d/*.conf\nPermitRootLogin yes\nMaxAuthTries 6\nX11Forwarding yes\nMatch User bob\n    PermitRootLogin yes\n")
(root / "etc/ssh/sshd_config.d/50-seedboxbuntu.conf").write_text(maintenance.SSH_CONF)
values = maintenance.sshd_files_values()
check("SSH : la première valeur l'emporte, les blocs Match sont ignorés", values.get("permitrootlogin") == "no" and values.get("maxauthtries") == "4" and values.get("x11forwarding") == "no", values)
(root / "etc/ssh/sshd_config.d/50-seedboxbuntu.conf").unlink()
check("SSH : sans fichier de durcissement, les valeurs par défaut ressortent", maintenance.sshd_files_values().get("permitrootlogin") == "yes")
check("SSH : le texte embarqué est celui du fichier livré", maintenance.SSH_CONF == (SRC / "rootfs/etc/ssh/sshd_config.d/50-seedboxbuntu.conf").read_text())

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
