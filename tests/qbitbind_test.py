"""qBittorrent : la liaison d'interface (tun0) suit le mode. En accès direct, tun0 n'existe pas : qBittorrent ne se connecterait à rien."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbqbit"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


conf = ctl.CONFIG / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"
conf.parent.mkdir(parents=True, exist_ok=True)
BASE = "[BitTorrent]\nSession\\Port=6881\nSession\\Interface=tun0\nSession\\InterfaceName=tun0\nSession\\DefaultSavePath=/data\n"
calls = []
ctl.run = lambda *a, **k: calls.append(tuple(str(x) for x in a)) or subprocess.CompletedProcess(a, 0, "", "")
ctl.say = lambda *a, **k: None
profiles = {"value": {"direct"}}
ctl.profiles_read = lambda: set(profiles["value"])

conf.write_text(BASE)
check("direct : liaison à tun0 détectée", ctl.qbit_interface_bound() is True)
check("direct + tun0 : corrigé", ctl.qbit_heal_interface() is True)
check("direct : plus aucune ligne d'interface", "Interface" not in conf.read_text() and "DefaultSavePath=/data" in conf.read_text() and "Port=6881" in conf.read_text())
check("direct : conteneur arrêté AVANT la modification puis relancé",
      calls == [("docker", "stop", "qbittorrent"), ("docker", "start", "qbittorrent")], str(calls))
calls.clear()
check("direct, déjà propre : rien à faire", ctl.qbit_heal_interface() is False and not calls)

profiles["value"] = {"vpn"}
check("VPN sans liaison : tun0 remis (coupe-circuit cohérent)", ctl.qbit_heal_interface() is True and "Session\\Interface=tun0" in conf.read_text())
calls.clear()
check("VPN avec liaison : rien à faire", ctl.qbit_heal_interface() is False and not calls)

profiles["value"] = set()
conf.write_text(BASE)
check("aucun mode : on ne touche à rien", ctl.qbit_heal_interface() is False and "tun0" in conf.read_text())
profiles["value"] = {"direct"}
conf.unlink()
check("pas de configuration : rien à faire", ctl.qbit_interface_bound() is None and ctl.qbit_heal_interface() is False)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
