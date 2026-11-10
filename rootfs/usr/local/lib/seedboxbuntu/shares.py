"""SeedBoxBuntu - partages réseau (SMB, NFS) et disques externes.

Montage
    Chaque partage est monté sur l'hôte sous /mnt/shares/<nom>, par une ligne du bloc géré
    de /etc/fstab (option nofail : un NAS absent ne bloque pas le démarrage). Les conteneurs
    voient /mnt/shares sous /shares avec une propagation « rslave » : un partage monté après
    leur démarrage y apparaît sans les redémarrer.

    Le point de montage vide est rendu immuable (chattr +i). Si le NAS est absent, une
    application qui y écrit reçoit une erreur au lieu de remplir le disque système. Le
    minuteur seedbox-shares remonte les partages absents toutes les 5 minutes, puis relance
    la vérification des torrents qui s'y trouvent.

Identifiants
    Les identifiants SMB sont dans /etc/seedboxbuntu/shares/<nom>.cred (0600, root), transmis
    au noyau par l'option credentials= : ils n'apparaissent ni dans fstab ni dans la liste
    des processus. La console les transmet par l'entrée standard.

Rôles
    Un rôle indique ce qu'un partage reçoit : téléchargements terminés, en cours, dossier
    surveillé de .torrent, copie des .torrent, bibliothèques Films et Séries. Les applications
    sont reconfigurées par « seedboxctl provision », qui lit l'emplacement de chaque rôle.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import seedboxctl as ctl

ROOT = Path(os.environ.get("SB_SHARES", "/mnt/shares"))
INNER = "/shares"   # même arborescence vue depuis les conteneurs
FSTAB = Path(os.environ.get("SB_FSTAB", "/etc/fstab"))
SHARES_FILE = ctl.ETC / "shares.json"
CRED_DIR = ctl.ETC / "shares"
BEGIN = "# --- SeedBoxBuntu : partages gérés par la console (ne pas modifier ce bloc) ---"
END = "# --- fin des partages SeedBoxBuntu ---"

KINDS = {"smb": "Partage Windows ou Samba (SMB)", "nfs": "Export NFS", "disk": "Disque branché sur le serveur"}
SMB_VERSIONS = ("", "3.1.1", "3.0", "2.1", "2.0", "1.0")
NFS_VERSIONS = ("", "4.2", "4.1", "4", "3")
DISK_TYPES = ("ntfs", "exfat", "vfat", "ext4", "xfs", "btrfs")

# Rôle : libellé, sous-dossier proposé, emplacement local quand aucun partage n'est choisi
ROLES = {
    "complete": ("Téléchargements terminés", "downloads", "/data/downloads"),
    "incomplete": ("Téléchargements en cours", "incomplete", ""),
    "watch": ("Dossier surveillé : .torrent à ajouter", "watch", ""),
    "torrents": ("Copie des fichiers .torrent ajoutés", "torrents", ""),
    "movies": ("Bibliothèque Films", "movies", "/data/media/movies"),
    "tv": ("Bibliothèque Séries", "tv", "/data/media/tv"),
}

NAME = re.compile(r"^[a-z][a-z0-9]{0,19}$")
HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$")
SMB_PART = re.compile(r"^[^/\\\x00-\x1f\x7f,\"]{1,80}$")
NFS_PATH = re.compile(r"^/[A-Za-z0-9._/+@:=-]{0,200}$")
SUBDIR = re.compile(r"^\w[\w .-]{0,60}(/\w[\w .-]{0,60}){0,5}$")   # lettres accentuées acceptées
UUID = re.compile(r"^[0-9A-Fa-f-]{4,40}$")
DOMAIN = re.compile(r"^[A-Za-z0-9._-]{0,64}$")


# ----------------------------------------------------------------------------
#  Données
# ----------------------------------------------------------------------------
def load():
    if SHARES_FILE.exists():
        data = json.loads(SHARES_FILE.read_text())
    else:
        data = {}
    data.setdefault("shares", {})
    data.setdefault("roles", {})
    return data


def save(data):
    SHARES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SHARES_FILE.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(temp, SHARES_FILE)


def location(role):
    """Chemin vu par les conteneurs ; chaîne vide si le rôle est désactivé."""
    entry = load()["roles"].get(role)
    if entry:
        return f"{INNER}/{entry['share']}/{entry['path']}"
    return ROLES[role][2]


def volume(role):
    """Système de fichiers d'un rôle : nom du partage, « local », ou None si désactivé."""
    entry = load()["roles"].get(role)
    if entry:
        return entry["share"]
    return "local" if ROLES[role][2] else None


# ----------------------------------------------------------------------------
#  Validation
# ----------------------------------------------------------------------------
def parse_smb(source):
    """//serveur/partage[/sous/dossier] ; les barres obliques inverses de Windows sont acceptées."""
    source = source.strip().replace("\\", "/")
    if not source.startswith("//"):
        raise ctl.SeedboxError("adresse SMB attendue : //serveur/partage")
    parts = source[2:].split("/")
    if len(parts) < 2 or not HOST.match(parts[0]) or not all(SMB_PART.match(p) for p in parts[1:] if p):
        raise ctl.SeedboxError("adresse SMB invalide : //serveur/partage (caractères , \" et \\ interdits)")
    return "//" + "/".join(p for p in parts if p)


def parse_nfs(source):
    source = source.strip()
    host, _, path = source.partition(":")
    if not HOST.match(host) or not NFS_PATH.match(path):
        raise ctl.SeedboxError("adresse NFS attendue : serveur:/chemin/de/l/export (sans espace)")
    return f"{host}:{path.rstrip('/') or '/'}"


def check_line(label, value, limit=128):
    if len(value) > limit or re.search(r"[\x00-\x1f\x7f]", value):
        raise ctl.SeedboxError(f"{label} : caractères non autorisés")
    return value


def check_subdir(path):
    path = path.strip().strip("/")
    if not SUBDIR.match(path) or any(part in (".", "..") for part in path.split("/")):
        raise ctl.SeedboxError("sous-dossier invalide : lettres, chiffres, espace, . _ - et / uniquement, sans ..")
    return path


# ----------------------------------------------------------------------------
#  Système
# ----------------------------------------------------------------------------
def has_systemd():
    return Path("/run/systemd/system").is_dir()


def mounted_targets():
    """Points de montage actifs, lus sans toucher aux partages (un NAS figé ne bloque pas)."""
    targets = set()
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        field = line.split(" ")[4]
        targets.add(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field))
    return targets


def is_mounted(name):
    return str(ROOT / name) in mounted_targets()


def puid():
    env = ctl.env_read()
    return int(env.get("PUID", 1000)), int(env.get("PGID", 1000))


def as_puid(*command, timeout=30):
    """Commande exécutée avec l'identité des conteneurs : vérifie les droits réels (NFS root_squash)."""
    uid, gid = puid()
    try:
        return subprocess.run(["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--", *command],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ctl.SeedboxError("le partage ne répond pas")


def protect(path, on=True):
    """Point de montage vide immuable : rien ne s'écrit sur le disque système si le partage manque."""
    subprocess.run(["chattr", "+i" if on else "-i", str(path)], capture_output=True)


def space(path):
    try:
        result = subprocess.run(["stat", "-f", "-c", "%b %a %S", str(path)], capture_output=True, text=True, timeout=5)
        blocks, free, size = map(int, result.stdout.split())
        return blocks * size, free * size
    except (subprocess.TimeoutExpired, ValueError):
        return None, None


def disks():
    """Partitions non montées et lisibles : disques USB ou internes ajoutés au serveur."""
    try:
        result = subprocess.run(["lsblk", "-J", "-b", "-o", "PATH,SIZE,FSTYPE,UUID,LABEL,MOUNTPOINTS,TYPE"],
                                capture_output=True, text=True, timeout=10)
        devices = json.loads(result.stdout).get("blockdevices", [])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    used = {entry.get("source") for entry in load()["shares"].values() if entry["kind"] == "disk"}
    found = []

    def probe(item):
        """Sans base udev (conteneur, système minimal), lsblk ignore l'UUID : blkid le lit sur le disque."""
        if item.get("fstype") or not item.get("path"):
            return
        try:
            result = subprocess.run(["blkid", "-o", "export", item["path"]], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        item.update(fstype=values.get("TYPE"), uuid=values.get("UUID"), label=values.get("LABEL", ""))

    def walk(items):
        for item in items:
            probe(item)
            mounts = [m for m in (item.get("mountpoints") or []) if m]
            if (item.get("fstype") in DISK_TYPES and item.get("uuid") and not mounts
                    and item["uuid"] not in used):
                found.append({"uuid": item["uuid"], "fstype": item["fstype"], "size": int(item.get("size") or 0),
                              "label": item.get("label") or "", "device": item.get("path") or ""})
            walk(item.get("children") or [])
    walk(devices)
    return found


def module_available(module):
    return bool(shutil.which("modprobe")) and subprocess.run(["modprobe", "-n", module], capture_output=True).returncode == 0


def ensure_driver(module):
    """Le noyau « virtual » (images cloud, certaines installations) n'a pas le pilote SMB :
    il est dans linux-modules-extra, installé à la demande pour le noyau en service."""
    if not shutil.which("modprobe") or module_available(module):
        return
    package = f"linux-modules-extra-{os.uname().release}"
    ctl.say(f"pilote {module} absent : installation de {package}")
    result = subprocess.run(["apt-get", "install", "-y", "-q", "-o", "DPkg::Lock::Timeout=180", package],
                            capture_output=True, text=True, env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
    if result.returncode != 0:
        raise ctl.SeedboxError(f"pilote {module} absent et {package} impossible à installer : {result.stderr.strip()[-160:]}")


def ntfs_driver():
    """Pilote du noyau s'il existe, sinon ntfs-3g (installé avec l'appliance)."""
    return "ntfs3" if module_available("ntfs3") else "ntfs-3g"


def escape(value):
    return value.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")


def fstab_line(name, entry):
    uid, gid = puid()
    target = escape(str(ROOT / name))
    # before=docker : au démarrage, les conteneurs attendent ce montage (30 s au plus), sans en dépendre
    common = "nofail,x-systemd.mount-timeout=30s,x-systemd.before=docker.service"
    if entry["kind"] == "smb":
        options = [f"credentials={CRED_DIR / (name + '.cred')}" if entry.get("username") else "guest",
                   f"uid={uid}", f"gid={gid}", "file_mode=0664", "dir_mode=0775", "iocharset=utf8"]
        if entry.get("version"):
            options.append(f"vers={entry['version']}")
        options += ["_netdev", common]
        return f"{escape(entry['source'])} {target} cifs {','.join(options)} 0 0"
    if entry["kind"] == "nfs":
        options = ["hard", "timeo=600", "retrans=3"]
        if entry.get("version"):
            options.append(f"nfsvers={entry['version']}")
        if entry.get("version") == "3":
            options.append("nolock")  # verrous NFSv3 : rappels entrants bloqués par le pare-feu
        options += ["_netdev", common]
        return f"{entry['source']} {target} nfs {','.join(options)} 0 0"
    fstype = entry["fstype"]
    common = "nofail,x-systemd.device-timeout=10s,x-systemd.before=docker.service"
    if fstype == "ntfs":
        return f"UUID={entry['source']} {target} {ntfs_driver()} uid={uid},gid={gid},umask=0002,{common} 0 0"
    if fstype in ("exfat", "vfat"):
        return f"UUID={entry['source']} {target} {fstype} uid={uid},gid={gid},umask=0002,{common} 0 0"
    return f"UUID={entry['source']} {target} {fstype} defaults,{common} 0 0"


def render_fstab(data):
    text = FSTAB.read_text() if FSTAB.exists() else ""
    text = re.sub(rf"\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n?", "\n", text, flags=re.S).rstrip("\n") + "\n"
    if data["shares"]:
        lines = [fstab_line(name, entry) for name, entry in sorted(data["shares"].items())]
        text += "\n".join([BEGIN, *lines, END]) + "\n"
    temp = FSTAB.with_name(FSTAB.name + ".seedbox")
    temp.write_text(text)
    if FSTAB.exists():
        os.chmod(temp, FSTAB.stat().st_mode & 0o777)
    os.replace(temp, FSTAB)
    if has_systemd():
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)


def write_credentials(name, username, password, domain):
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CRED_DIR, 0o700)
    target = CRED_DIR / f"{name}.cred"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(f"username={username}\npassword={password}\n" + (f"domain={domain}\n" if domain else ""))


def explain(output, kind):
    """Message de mount.cifs, mount.nfs ou du noyau traduit en cause probable."""
    text = output.strip()
    rules = [
        (r"error\(13\)|Permission denied|STATUS_LOGON_FAILURE", "accès refusé : identifiant, mot de passe ou droits du partage"),
        (r"access denied by server", "export refusé : autorisez l'adresse IP de ce serveur dans la configuration NFS du NAS"),
        (r"error\(2\)|No such file or directory|does not exist", "partage introuvable sur le serveur : vérifiez son nom"
         if kind == "smb" else "export introuvable sur le serveur : vérifiez le chemin"),
        (r"error\(112\)|error\(113\)|error\(110\)|Host is down|No route to host|timed out|Connection refused|error\(111\)"
         r"|could not connect|suitable address",
         "serveur injoignable : adresse, réseau ou service SMB/NFS arrêté"),
        (r"resolve|Name or service not known|bad UNC", "nom du serveur introuvable : utilisez son adresse IP"),
        (r"error\(95\)|Operation not supported|protocol not supported",
         "version refusée par le serveur : choisissez une autre version du protocole"),
        (r"not supported by the system|error\(19\)|No such device|unknown filesystem type",
         "pilote absent du noyau (paquet linux-modules-extra ou cifs-utils manquant)"),
        (r"can't find (UUID|LABEL)|special device .* does not exist", "disque absent : branchez-le, puis réessayez"),
        (r"wrong fs type|bad superblock", "système de fichiers illisible, ou option refusée par le serveur"),
    ]
    for pattern, message in rules:
        if re.search(pattern, text, re.I):
            return message
    lines = [line for line in text.splitlines() if line.strip() and "Refer to the" not in line]
    detail = next((line for line in lines if "error" in line.lower()), lines[-1] if lines else "erreur inconnue")
    return "montage impossible : " + detail[:200]


def refresh_readers():
    """Jellyfin voit /shares en lecture seule sans propagation : il doit redémarrer pour voir un
    partage monté, ou relâcher un partage démonté."""
    if ctl.container_running("jellyfin"):
        subprocess.run(["docker", "restart", "jellyfin"], capture_output=True, timeout=180)


def mount(name):
    data = load()
    entry = data["shares"][name]
    if is_mounted(name):
        return
    try:
        result = subprocess.run(["mount", str(ROOT / name)], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise ctl.SeedboxError("serveur injoignable : le montage n'a pas abouti en 60 s")
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise ctl.SeedboxError(explain(output, entry["kind"]))
    if not is_mounted(name):
        # Avec l'option nofail de fstab, mount répond 0 même quand le serveur ou le disque manque
        if output.strip():
            raise ctl.SeedboxError(explain(output, entry["kind"]))
        raise ctl.SeedboxError("serveur injoignable ou disque absent : le partage n'a pas été monté"
                               if entry["kind"] != "disk" else "disque absent : branchez-le, puis réessayez")
    refresh_readers()


def umount(name):
    if not is_mounted(name):
        return
    result = subprocess.run(["umount", str(ROOT / name)], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        if re.search(r"busy", result.stderr, re.I):
            raise ctl.SeedboxError("partage en cours d'utilisation : retirez ses rôles ou arrêtez les applications")
        raise ctl.SeedboxError("démontage impossible : " + result.stderr.strip()[-200:])
    refresh_readers()


def check_writable(name):
    """Ce que font les applications : dossier, écriture, renommage, suppression."""
    script = ('d="$1/.seedbox-test-$$"; mkdir "$d" && echo ok > "$d/f" && mv "$d/f" "$d/g" '
              '&& rm -f "$d/g" && rmdir "$d"; r=$?; rm -rf "$d" 2>/dev/null; exit $r')
    result = as_puid("sh", "-c", script, "sh", str(ROOT / name))
    if result.returncode != 0:
        uid, _ = puid()
        raise ctl.SeedboxError(
            f"partage monté mais pas inscriptible par les applications (utilisateur {uid}) : "
            "donnez l'écriture au compte SMB, ou pour NFS mappez les accès sur cet identifiant (all_squash, anonuid)")


# ----------------------------------------------------------------------------
#  Opérations
# ----------------------------------------------------------------------------
def add(name, kind, source, username="", password="", domain="", version=""):
    data = load()
    if not NAME.match(name or ""):
        raise ctl.SeedboxError("nom invalide : minuscules et chiffres, 20 caractères au plus, commençant par une lettre")
    if name in data["shares"]:
        raise ctl.SeedboxError(f"le partage {name} existe déjà")
    entry = {"kind": kind}
    if kind == "smb":
        entry["source"] = parse_smb(source)
        if version not in SMB_VERSIONS:
            raise ctl.SeedboxError("version SMB inconnue")
        if not DOMAIN.match(domain or ""):
            raise ctl.SeedboxError("domaine invalide")
        check_line("identifiant", username)
        check_line("mot de passe", password, 256)
        entry.update(username=username, domain=domain or "", version=version)
    elif kind == "nfs":
        entry["source"] = parse_nfs(source)
        if version not in NFS_VERSIONS:
            raise ctl.SeedboxError("version NFS inconnue")
        entry["version"] = version
    elif kind == "disk":
        source = source.strip().removeprefix("UUID=")
        disk = next((d for d in disks() if d["uuid"] == source), None)
        if not UUID.match(source) or not disk:
            raise ctl.SeedboxError("disque introuvable ou déjà utilisé : choisissez-le dans la liste détectée")
        entry.update(source=source, fstype=disk["fstype"], label=disk["label"])
    else:
        raise ctl.SeedboxError("type de partage inconnu : smb, nfs ou disk")
    if any(e.get("source") == entry["source"] for e in data["shares"].values()):
        raise ctl.SeedboxError("cette source est déjà montée sous un autre nom")

    if kind in ("smb", "nfs"):
        ensure_driver("cifs" if kind == "smb" else "nfs")
    target = ROOT / name
    ROOT.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=True)
    protect(target)
    if kind == "smb" and username:
        write_credentials(name, username, password, domain)
    data["shares"][name] = entry
    save(data)
    render_fstab(data)
    try:
        ctl.say(f"montage de {entry['source']}")
        mount(name)
        if kind == "disk" and entry["fstype"] in ("ext4", "xfs", "btrfs"):
            # Disque Linux neuf : la racine appartient à root ; les applications doivent y écrire
            uid, gid = puid()
            if os.stat(target).st_uid == 0 and not any(p for p in target.iterdir() if p.name != "lost+found"):
                os.chown(target, uid, gid)
        check_writable(name)
    except (ctl.SeedboxError, OSError) as error:
        ctl.say("échec : le partage n'est pas conservé")
        try:
            umount(name)
        except ctl.SeedboxError:
            pass
        forget(name)
        raise ctl.SeedboxError(str(error))
    total, free = space(target)
    ctl.say(f"partage {name} monté : {format_size(free)} libres sur {format_size(total)}")
    return []


def forget(name):
    data = load()
    data["shares"].pop(name, None)
    save(data)
    render_fstab(data)
    (CRED_DIR / f"{name}.cred").unlink(missing_ok=True)
    target = ROOT / name
    if target.is_dir() and not is_mounted(name):
        protect(target, False)
        try:
            target.rmdir()
        except OSError:
            pass


def torrents_on(name):
    """Torrents dont les fichiers sont sur ce partage (qBittorrent doit être démarré)."""
    if not ctl.container_running("qbittorrent"):
        return []
    cookie = ctl.qbit_login()
    torrents = ctl.request("GET", ctl.svc(ctl.qbit_host(), 8080) + "/api/v2/torrents/info",
                           headers={"Cookie": cookie}, timeout=30).json() or []
    prefix = f"{INNER}/{name}/"
    return [t for t in torrents if (t.get("save_path", "") + "/").startswith(prefix)
            or (t.get("download_path") or "").startswith(prefix)]


def remove(name, force=False):
    data = load()
    if name not in data["shares"]:
        raise ctl.SeedboxError(f"partage inconnu : {name}")
    roles = [ROLES[r][0] for r, e in data["roles"].items() if e["share"] == name]
    if roles:
        raise ctl.SeedboxError("partage utilisé par : " + ", ".join(roles) + ". Choisissez d'abord un autre emplacement.")
    if not force:
        try:
            count = len(torrents_on(name))
        except (ctl.SeedboxError, OSError):
            count = 0
        if count:
            raise ctl.SeedboxError(f"{count} torrent(s) partagent encore des fichiers de ce partage : "
                                   "supprimez-les ou déplacez-les dans qBittorrent")
    umount(name)
    forget(name)
    ctl.say(f"partage {name} retiré")
    return []


def set_credentials(name, username, password, domain=""):
    data = load()
    entry = data["shares"].get(name)
    if not entry or entry["kind"] != "smb":
        raise ctl.SeedboxError("partage SMB inconnu")
    check_line("identifiant", username)
    check_line("mot de passe", password, 256)
    if not DOMAIN.match(domain or ""):
        raise ctl.SeedboxError("domaine invalide")
    old = (CRED_DIR / f"{name}.cred").read_text() if (CRED_DIR / f"{name}.cred").exists() else None
    entry.update(username=username, domain=domain)
    save(data)
    if username:
        write_credentials(name, username, password, domain)
    render_fstab(data)
    umount(name)
    try:
        mount(name)
        check_writable(name)
    except ctl.SeedboxError:
        if old is not None:  # retour aux identifiants précédents
            (CRED_DIR / f"{name}.cred").write_text(old)
            try:
                mount(name)
            except ctl.SeedboxError:
                pass
        raise
    ctl.say(f"identifiants de {name} mis à jour")
    return []


def set_role(role, share="", path=""):
    """Choisit l'emplacement d'un rôle, puis reconfigure les applications."""
    if role not in ROLES:
        raise ctl.SeedboxError("rôle inconnu : " + ", ".join(ROLES))
    data = load()
    if share:
        if share not in data["shares"]:
            raise ctl.SeedboxError(f"partage inconnu : {share}")
        if not is_mounted(share):
            raise ctl.SeedboxError(f"le partage {share} n'est pas monté")
        path = check_subdir(path or ROLES[role][1])
        result = as_puid("mkdir", "-p", "--", str(ROOT / share / path))
        if result.returncode != 0:
            raise ctl.SeedboxError(f"création de {path} impossible sur {share} : {result.stderr.strip()[-160:]}")
        data["roles"][role] = {"share": share, "path": path}
        ctl.say(f"{ROLES[role][0]} : {share}/{path}")
    else:
        data["roles"].pop(role, None)
        ctl.say(f"{ROLES[role][0]} : " + (ROLES[role][2] and "stockage local" or "désactivé"))
    save(data)
    return ctl.provision()


def remount():
    """Minuteur : remonte les partages absents et relance les torrents qui en dépendent."""
    messages = []
    for name in load()["shares"]:
        if not is_mounted(name):
            try:
                mount(name)
                ctl.say(f"partage {name} remonté")
            except ctl.SeedboxError as error:
                messages.append(f"{name} : {error}")
                continue
        # Aussi après un montage tardif par systemd : qBittorrent a pu démarrer avant le NAS
        recover(name)
    return messages


def recover(name):
    """Après une coupure du NAS, qBittorrent marque les torrents « fichiers manquants »."""
    try:
        stuck = [t["hash"] for t in torrents_on(name) if t.get("state") == "missingFiles"]
    except (ctl.SeedboxError, OSError):
        return
    if not stuck:
        return
    cookie = ctl.qbit_login()
    base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2/torrents"
    form = {"hashes": "|".join(stuck)}
    ctl.request("POST", base + "/recheck", form=form, headers={"Cookie": cookie})
    if not ctl.request("POST", base + "/start", form=form, headers={"Cookie": cookie}).ok:
        ctl.request("POST", base + "/resume", form=form, headers={"Cookie": cookie})  # qBittorrent 4
    ctl.say(f"{len(stuck)} torrent(s) revérifiés sur {name}")


# ----------------------------------------------------------------------------
#  Applications
# ----------------------------------------------------------------------------
def apply_qbittorrent():
    """Emplacements de qBittorrent. Les torrents existants restent où ils sont (mode manuel)."""
    if not ctl.container_running("qbittorrent"):
        ctl.say("qBittorrent arrêté : emplacements appliqués à la configuration du VPN")
        return
    incomplete, watch, torrents = location("incomplete"), location("watch"), location("torrents")
    preferences = {
        "save_path": location("complete"),
        "temp_path_enabled": bool(incomplete),
        "scan_dirs": {watch: 1} if watch else {},
        "export_dir": torrents,                          # qBittorrent jusqu'à 5.2
        "torrent_files_backup_enabled": bool(torrents),  # même réglage, renommé en 5.3
        # Changer l'emplacement par défaut ne déplace pas des téraoctets de données déjà partagées
        "save_path_changed_tmm_enabled": False,
    }
    if incomplete:
        preferences["temp_path"] = incomplete
    if torrents:
        preferences["torrent_files_backup_dir"] = torrents
    cookie = ctl.qbit_login()
    base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2/app"
    result = ctl.request("POST", base + "/setPreferences", form={"json": json.dumps(preferences)},
                         headers={"Cookie": cookie})
    if not result.ok:
        raise ctl.SeedboxError(f"qBittorrent : emplacements refusés (HTTP {result.status})")
    current = ctl.request("GET", base + "/preferences", headers={"Cookie": cookie}).json()
    if current.get("save_path", "").rstrip("/") != preferences["save_path"].rstrip("/"):
        raise ctl.SeedboxError("qBittorrent : emplacement non pris en compte")


def jellyfin_paths(default, role):
    """Chemins d'une bibliothèque Jellyfin : le stockage local, plus le partage s'il est choisi."""
    paths = [default]
    if volume(role) not in ("local", None):
        paths.append(location(role))
    return paths


# ----------------------------------------------------------------------------
#  État pour la console
# ----------------------------------------------------------------------------
def format_size(value):
    if not value:
        return "?"
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024 or unit == "To":
            return f"{value:.0f} {unit}" if unit in ("o", "Ko") else f"{value:.1f} {unit}"
        value /= 1024


def warnings(data):
    notes = []
    roles = data["roles"]

    def vol(role):
        return roles[role]["share"] if role in roles else ("local" if ROLES[role][2] else None)
    apart = [label for library, label in (("movies", "Films"), ("tv", "Séries")) if vol("complete") != vol(library)]
    if apart:
        notes.append(f"Téléchargements terminés et bibliothèque{'s' if len(apart) > 1 else ''} {' et '.join(apart)} "
                     "sur des volumes différents : Radarr et Sonarr copieront les fichiers au lieu de créer un lien "
                     "physique. L'espace est consommé deux fois tant que le torrent est partagé.")
    if "complete" in roles:
        notes.append(f"Les torrents terminés sont partagés depuis {roles['complete']['share']} : le débit d'envoi dépend "
                     "de ce support (et du réseau local pour un NAS). Les torrents existants restent à leur emplacement actuel.")
    if vol("incomplete") and vol("incomplete") != vol("complete"):
        notes.append("Téléchargements en cours et terminés sur des volumes différents : qBittorrent copie "
                     "chaque fichier à la fin du téléchargement.")
    for name, entry in data["shares"].items():
        if entry.get("kind") == "smb" and entry.get("version") == "1.0":
            notes.append(f"{name} : SMB 1.0 est ancien et peu sûr ; à réserver aux NAS qui n'acceptent rien d'autre.")
    return notes


def overview():
    data = load()
    targets = mounted_targets()
    shares = []
    for name, entry in sorted(data["shares"].items()):
        mounted = str(ROOT / name) in targets
        total, free = space(ROOT / name) if mounted else (None, None)
        shares.append({
            "name": name, "kind": entry["kind"], "source": entry["source"], "version": entry.get("version", ""),
            "username": entry.get("username", ""), "fstype": entry.get("fstype", ""), "label": entry.get("label", ""),
            "mounted": mounted, "total": total, "free": free, "host_path": str(ROOT / name), "path": f"{INNER}/{name}",
            "roles": [r for r, e in data["roles"].items() if e["share"] == name],
        })
    roles = [{"role": role, "label": label, "share": data["roles"].get(role, {}).get("share", ""),
              "path": data["roles"].get(role, {}).get("path", default_sub), "location": location(role),
              "local": local}
             for role, (label, default_sub, local) in ROLES.items()]
    return {"shares": shares, "roles": roles, "disks": disks(), "warnings": warnings(data),
            "kinds": KINDS, "smb_versions": SMB_VERSIONS, "nfs_versions": NFS_VERSIONS}
