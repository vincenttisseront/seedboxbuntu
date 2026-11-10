"""Mise à jour d'une ancienne installation : dossier des piles Dockge et mode de qBittorrent."""
import os as _os
SRC = _os.environ.get("SB_SRC") or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, sys, tempfile, pathlib
tmp = pathlib.Path(tempfile.mkdtemp())
base = tmp / "opt" / "seedbox"
(base / "stacks" / "monappli").mkdir(parents=True)
(base / "stacks" / "monappli" / "compose.yaml").write_text("services: {}\n")
os.environ.update({"SB_BASE": str(base), "SB_STORAGE": str(tmp / "storage"), "SB_ETC": str(tmp / "etc"),
                   "SB_STATE": str(tmp / "state"), "SB_SHARE": SRC})
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as c
results = []
def check(label, ok, detail=""):
    results.append(bool(ok)); print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}", flush=True)

# ancien .env : piles dans <base>/stacks, ni vpn ni direct (qBittorrent n'a jamais démarré)
old = (c.SHARE / "stack" / "env.template").read_text()
old = c.substitute(old, {"BASE": str(base), "STORAGE": str(tmp / "storage"), "STACKS": str(base.parent)})
old = old.replace(f"STACKS_DIR={base.parent}", f"STACKS_DIR={base}/stacks").replace("COMPOSE_PROFILES=autoupdate,direct", "COMPOSE_PROFILES=autoupdate")
c.ENV_FILE.parent.mkdir(parents=True, exist_ok=True); c.ENV_FILE.write_text(old)
check("ancien état : aucun mode", c.qbit_mode() is None)
c.ensure_env()
env = c.env_read()
check("dossier des piles = parent de la base", env["STACKS_DIR"] == str(base.parent), env["STACKS_DIR"])
check("pile existante déplacée", (base.parent / "monappli" / "compose.yaml").exists())
check("ancien dossier retiré", not (base / "stacks").exists())
check("mode direct par défaut", c.qbit_mode() == "direct" and "autoupdate" in c.profiles_read(), env["COMPOSE_PROFILES"])
check("port de partage défini", env.get("QBIT_PEER_PORT") == "6881")
c.ensure_env()   # idempotent
check("second passage identique", c.env_read() == env)

# ancienne installation avec VPN : le mode vpn est conservé
c.env_set(COMPOSE_PROFILES="autoupdate,vpn")
c.ensure_env()
check("VPN existant conservé", c.qbit_mode() == "vpn" and c.qbit_host() == "gluetun")

# interface liée au tunnel selon le mode
conf = c.CONFIG / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"
conf.parent.mkdir(parents=True)
conf.write_text("[BitTorrent]\nSession\\Port=6881\nSession\\Interface=tun0\nSession\\InterfaceName=tun0\nSession\\BTProtocol=TCP\n")
c.qbit_bind_interface(False)
check("direct : interface libre", "Interface" not in conf.read_text() and "Session\\Port=6881" in conf.read_text())
c.qbit_bind_interface(True)
check("vpn : lié à tun0", conf.read_text().count("tun0") == 2 and "Session\\Port=6881" in conf.read_text())
c.qbit_bind_interface(True)
check("vpn : idempotent", conf.read_text().count("Interface=tun0") == 1)
print(f"{sum(results)}/{len(results)} contrôles réussis"); sys.exit(0 if all(results) else 1)
