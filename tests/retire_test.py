"""Retrait de p2p-aggregator et tracker-session : conteneurs, images, sources et archive supprimés, données conservées ; plus aucune trace dans la stack."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbretire"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402
import yaml  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


calls, said = [], []
ctl.run = lambda *a, **k: calls.append(tuple(str(x) for x in a)) or subprocess.CompletedProcess(a, 0, "", "")
ctl.say = lambda message: said.append(message)

# --- installation existante : sources, archive d'image et données ----------------------------------------------------------------------
sources = ctl.APPS_DIR / "p2p-aggregator"
(sources / "src").mkdir(parents=True)
(sources / "package.json").write_text("{}")
archive = ctl.STATE / "images" / "p2p-aggregator.tar.zst"
archive.parent.mkdir(parents=True)
archive.write_bytes(b"x" * 100)
data = ctl.CONFIG / "p2p-aggregator"
data.mkdir(parents=True)
(data / "p2p.db").write_text("comptes de trackers")

ctl.retire_old_apps()
check("conteneurs p2p-aggregator et tracker-session supprimés", ("docker", "rm", "-f", "p2p-aggregator") in calls and ("docker", "rm", "-f", "tracker-session") in calls, str(calls))
check("mise à jour : images, sources et archive gardées (retour arrière possible)", sources.exists() and archive.exists() and not any(c[1] == "rmi" for c in calls), str(calls))
check("mise à jour : le délai de grâce démarre", ctl.RETIRED_MARKER.exists())
calls.clear()
ctl.retire_old_apps(purge=True)
check("purge trop tôt : rien n'est supprimé", sources.exists() and archive.exists() and not calls)
ctl.RETIRED_MARKER.write_text(str(int(time.time()) - ctl.RETIRED_GRACE - 60) + "\n")
ctl.retire_old_apps(purge=True)
check("images supprimées (current, previous, candidate)", all(("docker", "rmi", "-f", f"seedbox/{n}:{t}") in calls for n in ("p2p-aggregator", "tracker-session") for t in ("current", "previous", "candidate")))
check("sources et archive d'image supprimées, délai effacé", not sources.exists() and not archive.exists() and not ctl.RETIRED_MARKER.exists())
check("les données (base, comptes de trackers) ne sont jamais effacées", (data / "p2p.db").read_text() == "comptes de trackers")
check("l'utilisateur est informé, données mentionnées", any("restent dans config/p2p-aggregator" in m for m in said), str(said))
calls.clear(); said.clear()
ctl.retire_old_apps(purge=True)
check("purge relancée : idempotente, rien à signaler", not said and not calls)

# --- plus aucune trace dans la stack et les modèles -----------------------------------------------------------------------------------------
compose = yaml.safe_load((Path(SRC) / "stack/docker-compose.yml").read_text())
check("docker-compose : plus de service p2p-aggregator ni tracker-session", not {"p2p-aggregator", "tracker-session"} & set(compose["services"]))
check("docker-compose : le port 3000 n'est plus publié par Traefik", "3000:3000" not in compose["services"]["traefik"]["ports"])
env_template = (Path(SRC) / "stack/env.template").read_text()
check("modèle .env : plus de secret P2P_", "P2P_" not in env_template)
check("applications : plus d'entrée P2P Aggregator", all(a["id"] != "p2p" for a in ctl.APPS))
check("secrets générés : plus de clés P2P_", not any(k.startswith("P2P_") for k in ctl.generate_secrets.__code__.co_consts if isinstance(k, str)))
for path in ("templates/config/traefik/traefik.yml", "templates/config/traefik/dynamic/seedbox.yml", "templates/config/homepage/services.yaml", "templates/config/qbit-manage/config.yml"):
    check(f"{path} : plus de p2p", "p2p" not in (Path(SRC) / path).read_text().lower())
check("pare-feu : le port 3000 n'est plus ouvert", 3000 not in ctl.maintenance().APP_PORTS)
check("plus de commande « seedboxctl p2p »", subprocess.run([sys.executable, SRC + "/rootfs/usr/local/lib/seedboxbuntu/seedboxctl.py", "p2p", "status"], capture_output=True, text=True,
      env=dict(os.environ, SB_SRC=SRC), timeout=60).returncode != 0)
print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
