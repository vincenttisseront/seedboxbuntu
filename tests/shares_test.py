"""Découverte des partages d'un serveur (SMB, NFS) avec de faux smbclient et showmount : liste, filtres, erreurs, secret hors ligne de commande."""
import os
import stat
import sys
import tempfile
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbshares"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
bin_dir = root / "bin"
bin_dir.mkdir()
os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import shares  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def tool(name, body):
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def raises(function, *args):
    try:
        function(*args)
    except ctl.SeedboxError as error:
        return str(error)
    return None


log = root / "smb.log"
tool("smbclient", f'''echo "$@" > {log}
for a in "$@"; do last="$prev"; prev="$a"; if [ "$last" = "-A" ]; then cat "$a" >> {log}; fi; done
case "$3" in bad) echo "session setup failed: NT_STATUS_LOGON_FAILURE"; exit 1;; down) echo "Connection to down failed (Error NT_STATUS_HOST_UNREACHABLE)"; exit 1;; empty) exit 0;; esac
printf 'Disk|torrents|Téléchargements\\nDisk|Films 4K|\\nDisk|C$|Partage par défaut\\nIPC|IPC$|IPC Service\\nPrinter|hp|Imprimante\\nDisk|bad,name|x\\n'
''')
found = shares.discover("smb", "nas.local", "seedbox", "s3cret mot", "WORKGROUP", "3.0")
check("SMB : seuls les disques visibles (ni $, ni IPC, ni imprimante, ni nom interdit)", [f["name"] for f in found] == ["torrents", "Films 4K"], found)
check("SMB : adresse complète prête à monter", found[0]["source"] == "//nas.local/torrents" and found[0]["comment"] == "Téléchargements", found[0])
text = log.read_text()
check("SMB : mot de passe dans le fichier d'authentification, jamais dans la commande", "s3cret" in text and text.splitlines()[0].count("s3cret") == 0 and "-A" in text.splitlines()[0], text)
check("SMB : version demandée transmise", "-m SMB3_00" in text.splitlines()[0], text.splitlines()[0])
check("SMB : fichier d'authentification supprimé", not list(Path(tempfile.gettempdir()).glob("sbdisc*")))
shares.discover("smb", "nas.local")
check("SMB sans identifiant : accès invité (-N)", " -N" in log.read_text().splitlines()[0])
check("SMB : mauvais identifiant expliqué", "identifiant ou mot de passe incorrect" in (raises(shares.discover, "smb", "bad", "u", "p") or ""))
check("SMB : serveur éteint expliqué", "injoignable" in (raises(shares.discover, "smb", "down", "u", "p") or ""))
check("SMB : serveur sans partage visible", "aucun partage" in (raises(shares.discover, "smb", "empty", "u", "p") or ""))
check("SMB : serveur invalide refusé", raises(shares.discover, "smb", "//nas/torrents") is not None and raises(shares.discover, "smb", "nas; rm -rf /") is not None)
check("SMB : version inconnue refusée", raises(shares.discover, "smb", "nas", "", "", "", "9.9") is not None)
check("SMB : identifiant avec retour à la ligne refusé", raises(shares.discover, "smb", "nas", "a\nb", "p") is not None)

tool("showmount", '''case "$3" in down) echo "clnt_create: RPC: Port mapper failure - Timed out" >&2; exit 1;; v4) echo "clnt_create: RPC: Program not registered" >&2; exit 1;; none) exit 0;; esac
printf '/volume1/torrents 192.168.1.0/24\\n/volume1/films *\\n'
''')
found = shares.discover("nfs", "nas.local")
check("NFS : exports et groupes autorisés", [f["source"] for f in found] == ["nas.local:/volume1/torrents", "nas.local:/volume1/films"], found)
check("NFS : serveur v4 seul expliqué", "à la main" in (raises(shares.discover, "nfs", "v4") or ""))
check("NFS : serveur éteint expliqué", "injoignable" in (raises(shares.discover, "nfs", "down") or ""))
check("NFS : aucun export", "aucun export" in (raises(shares.discover, "nfs", "none") or ""))
check("type disque refusé", raises(shares.discover, "disk", "nas") is not None)
check("smbclient dans la liste des paquets de l'appliance", "smbclient" in __import__("maintenance").PACKAGES)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
