"""SeedBoxBuntu - où est passé l'espace ? Ce qui occupe le disque local, et ce qu'on peut faire.

L'analyse ne supprime rien. Pour chaque disque local (stockage, système) elle mesure ce qui l'occupe :
téléchargements, médiathèque locale, corbeille du nettoyage, sauvegardes locales, configuration des applications, images
et caches Docker, journaux, cache apt, données de l'appliance, dossiers personnels ; le reste est « non identifié ».
Elle liste aussi les plus gros éléments du stockage.

Les remèdes sont classés par espace récupérable :
  - correctifs sûrs exécutables d'un clic : vider la corbeille, images Docker inutiles, journaux trop anciens, cache apt ;
  - résultats de l'assistant de nettoyage (orphelins, partiels, doublons, transcodage) : on renvoie vers la page Nettoyage ;
  - conseils : déplacer la médiathèque sur un partage réseau, régler la durée des seeds, agrandir le disque.

    seedboxctl space show | scan | fix trash-empty|docker|journal|apt
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import seedboxctl as ctl

SCAN_FILE = ctl.STATE / "space-scan.json"
JOURNAL_KEEP = 200 * 1024 * 1024          # taille conservée après « journaux »
BIGGEST = 15
DU_TIMEOUT = 240
FIXES = ("trash-empty", "docker", "journal", "apt")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# ----------------------------------------------------------------------------
#  Mesures (points d'entrée remplaçables par les tests)
# ----------------------------------------------------------------------------
def du(path, depth=1):
    """Tailles disque {chemin: octets} de path et de ses sous-dossiers jusqu'à `depth`, sans quitter le système de fichiers."""
    if not Path(path).exists():
        return {}
    try:
        result = subprocess.run(["du", "-B1", "-x", f"-d{depth}", "--", str(path)], capture_output=True, text=True, timeout=DU_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    sizes = {}
    for line in result.stdout.splitlines():
        size, _, where = line.partition("\t")
        if size.isdigit() and where:
            sizes[where] = int(size)
    return sizes


def capture(*command):
    """Sortie standard d'une commande, ou chaîne vide."""
    if not shutil.which(command[0]):
        return ""
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def docker_usage():
    """Occupation de Docker : [{kind, size, reclaimable}] (octets)."""
    convert = ctl.sysinfo().size_to_bytes
    rows = []
    for line in capture("docker", "system", "df", "--format", "{{json .}}").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        reclaim = re.match(r"\s*([\d.,]+\s*[A-Za-z]*)", str(row.get("Reclaimable", "")))
        rows.append({"kind": str(row.get("Type", "")), "size": convert(str(row.get("Size", "0B"))),
                     "reclaimable": convert(reclaim.group(1).replace(",", ".")) if reclaim else 0})
    return rows


def journal_bytes():
    found = re.search(r"take up ([\d.,]+)\s*([KMGT]?)", capture("journalctl", "--disk-usage"))   # journald écrit « 1.2G » (puissances de 1024)
    return ctl.sysinfo().size_to_bytes(found.group(1).replace(",", ".") + (found.group(2) + "iB" if found.group(2) else "B")) if found else 0


DOCKER_LABELS = {"Images": "Images Docker", "Containers": "Conteneurs Docker", "Local Volumes": "Volumes Docker", "Build Cache": "Cache de construction Docker"}


# ----------------------------------------------------------------------------
#  Disques
# ----------------------------------------------------------------------------
def volumes():
    """Disques locaux à surveiller (stockage, système) avec leur remplissage actuel."""
    alerts = ctl.alerts()
    threshold = alerts.settings()["disk_percent"]
    found = []
    for label, path in alerts.disk_paths():
        if label not in ("stockage", "système et configuration"):
            continue                                  # partages réseau : hors sujet pour « le disque local »
        try:
            usage = os.statvfs(path)
            device = os.stat(path).st_dev
        except OSError:
            continue
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        percent = round(100 * (total - free) / total) if total else 0
        level = "critical" if percent >= 95 else "warning" if percent >= threshold else "ok"
        found.append({"label": label, "path": str(path), "device": device, "total": total, "free": free, "used": total - free,
                      "percent": percent, "level": level})
    return found


def candidates():
    """Emplacements mesurés : (id, libellé, chemin, explication, profondeur du détail)."""
    storage = ctl.STORAGE
    return [
        ("downloads", "Téléchargements", storage / "downloads", "Fichiers de qBittorrent : seeds en cours et téléchargements terminés.", 1),
        ("media", "Médiathèque locale", storage / "media", "Films, séries, musique et livres rangés sur ce disque.", 1),
        ("trash", "Corbeille du nettoyage", storage / ".corbeille", "Éléments mis de côté, supprimés au vidage ou après 14 jours.", 0),
        ("config", "Configuration des applications", ctl.CONFIG, "Bases, métadonnées et images de Jellyfin, Radarr, Sonarr…", 1),
        ("backups", "Sauvegardes locales", ctl.STATE / "sauvegardes", "Copies de la configuration gardées sur la machine.", 0),
        ("state", "Données de l'appliance", ctl.STATE, "Historique, agrégateur, journal d'audit.", 0),
        ("journal", "Journaux système", Path("/var/log"), "Journal systemd et fichiers /var/log.", 0),
        ("apt", "Cache des paquets (apt)", Path("/var/cache/apt"), "Paquets téléchargés, sans utilité une fois installés.", 0),
        ("home", "Dossiers personnels", Path("/home"), "Fichiers déposés par les utilisateurs de la machine.", 1),
        ("tmp", "Fichiers temporaires", Path("/var/tmp"), "Fichiers laissés par des programmes.", 0),
    ]


def same_device(path, device):
    try:
        return os.stat(path).st_dev == device
    except OSError:
        return False


def analyse_volume(volume, scanned_state):
    """Répartition de l'occupation d'un disque."""
    items, seen = [], set()
    for ident, label, path, hint, depth in candidates():
        if not path.exists() or not same_device(path, volume["device"]):
            continue
        real = str(path.resolve())
        if any(real == other or real.startswith(other + "/") for other in seen):
            continue                                   # déjà compté dans un dossier parent
        sizes = du(path, depth)
        total = sizes.get(str(path), 0)
        if not total:
            continue
        if ident == "journal":
            total = max(total, journal_bytes())
        children = sorted(((Path(p).name, s) for p, s in sizes.items() if p != str(path)), key=lambda kv: -kv[1])[:8] if depth else []
        items.append({"id": ident, "label": label, "path": str(path), "size": total, "hint": hint,
                      "children": [{"name": n, "size": s} for n, s in children if s > 0]})
        seen.add(real)
    if same_device("/var/lib/docker", volume["device"]):
        for row in docker_usage():
            if row["size"] > 0:
                items.append({"id": "docker-" + row["kind"].lower().replace(" ", "-"), "label": DOCKER_LABELS.get(row["kind"], row["kind"]),
                              "path": "/var/lib/docker", "size": row["size"], "hint": "Géré par Docker (docker system df).", "children": [],
                              "reclaimable": row["reclaimable"]})
    # Un dossier contenu dans un autre (sauvegardes dans les données de l'appliance) n'est compté qu'une fois : retiré du parent
    for parent in items:
        for child in items:
            if child is not parent and child["path"].startswith(parent["path"].rstrip("/") + "/") and parent["path"] != "/var/lib/docker":
                parent["size"] = max(0, parent["size"] - child["size"])
    items = [i for i in items if i["size"] > 0]
    items.sort(key=lambda i: -i["size"])
    other = max(0, volume["used"] - sum(i["size"] for i in items))
    return {"items": items, "other": other}


def biggest(volume):
    """Plus gros éléments du stockage : releases téléchargées et titres de la médiathèque (niveau 3 sous le stockage)."""
    base = ctl.STORAGE
    if not same_device(base, volume["device"]):
        return []
    sizes = du(base, 3)
    rows = []
    for path, size in sizes.items():
        relative = Path(path).relative_to(base).parts if path != str(base) else ()
        if len(relative) == 3 and relative[0] in ("downloads", "media"):
            rows.append({"path": "/".join(relative), "size": size})
    return sorted(rows, key=lambda r: -r["size"])[:BIGGEST]


# ----------------------------------------------------------------------------
#  Analyse, mémoire, remèdes
# ----------------------------------------------------------------------------
def scan():
    """Mesure chaque disque local. Les remèdes reposent sur l'assistant de nettoyage : on relance son analyse si elle a plus de 6 h."""
    try:
        cleanup = ctl.cleanup()
        if time.time() - (cleanup.read_json(cleanup.SCAN_FILE, {}).get("scanned") or 0) > 6 * 3600:
            say("analyse de l'espace récupérable (nettoyage)")
            cleanup.scan()
    except (ctl.SeedboxError, OSError) as error:
        say(f"nettoyage : {error}")
    result = {"scanned": int(time.time()), "volumes": []}
    for volume in volumes():
        say(f"analyse de {volume['label']} ({volume['path']})")
        analysis = analyse_volume(volume, None)
        result["volumes"].append({"label": volume["label"], "path": volume["path"], "device": volume["device"], **analysis, "biggest": biggest(volume)})
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    SCAN_FILE.write_text(json.dumps(result, ensure_ascii=False))
    return result


def saved():
    try:
        data = json.loads(SCAN_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remedies(volume_scan, live):
    """Remèdes proposés pour un disque, du plus rentable au plus incertain."""
    items = {i["id"]: i for i in volume_scan.get("items", [])}
    cleanup = ctl.cleanup().overview()
    categories = {c["id"]: c for c in cleanup["categories"]}
    found = []

    def add(ident, title, why, reclaim, kind, **extra):
        found.append({"id": ident, "title": title, "why": why, "reclaim": int(reclaim or 0), "kind": kind, **extra})

    trash = cleanup["trash"]["size"] if same_device(ctl.STORAGE / ".corbeille", live["device"]) else 0
    if trash:
        add("trash-empty", "Vider la corbeille du nettoyage", "Ces éléments sont déjà mis de côté ; l'espace n'est rendu qu'au vidage (automatique après 14 jours).",
            trash, "fix", fix="trash-empty")
    on_storage = same_device(ctl.STORAGE, live["device"])
    if on_storage:
        for key, title, why in (("orphan", "Supprimer les téléchargements orphelins", "Fichiers que qBittorrent ne suit plus et qui n'ont pas de copie en médiathèque."),
                                ("partial", "Supprimer les fichiers partiels abandonnés", "Téléchargements interrompus depuis plus de 14 jours."),
                                ("duplicate", "Supprimer les doublons de la médiathèque", "Même fichier vidéo à deux emplacements."),
                                ("transcode", "Vider les fichiers temporaires de transcodage", "Laissés par Jellyfin depuis plus de 24 h."),
                                ("empty_dir", "Supprimer les dossiers vides", "Dossiers sans contenu.")):
            category = categories.get(key)
            if category and category["count"] and category["size"]:
                add("cleanup-" + key, title, why, category["size"], "page", view="cleanup")
    docker = [i for i in volume_scan.get("items", []) if i["id"].startswith("docker-")]
    docker_reclaim = sum(i.get("reclaimable", 0) for i in docker)
    if docker_reclaim:
        add("docker", "Nettoyer Docker (images inutilisées et cache de construction)", "Anciennes versions d'images laissées par les mises à jour.", docker_reclaim, "fix", fix="docker")
    journal = items.get("journal")
    if journal and journal["size"] > JOURNAL_KEEP * 1.5:
        add("journal", "Réduire les journaux système", f"Conserve les {JOURNAL_KEEP // (1 << 20)} Mo les plus récents.", journal["size"] - JOURNAL_KEEP, "fix", fix="journal")
    apt = items.get("apt")
    if apt and apt["size"] > 50 * 1024 * 1024:
        add("apt", "Vider le cache des paquets", "Les paquets déjà installés n'ont pas besoin d'être gardés.", apt["size"], "fix", fix="apt")
    media = items.get("media")
    try:
        has_share = bool(ctl.shares().mounted_targets())
    except Exception:  # noqa: BLE001 - partages illisibles : conseil non proposé
        has_share = False
    if media and media["size"] > live["total"] * 0.05:
        add("media-share", "Ranger la médiathèque sur un partage réseau" if has_share else "Ranger la médiathèque sur un partage réseau (NAS)",
            "Définissez l'emplacement des films, séries, musique ou livres sur un partage : ils ne sont plus sur le disque local."
            + ("" if has_share else " Aucun partage n'est monté pour l'instant : ajoutez-en un dans Partages."), media["size"], "advice", view="shares")
    downloads = items.get("downloads")
    if downloads and downloads["size"] > live["total"] * 0.10:
        add("seeds", "Régler la durée et le ratio des seeds", "Les torrents terminés restent en seed ; qbit_manage peut retirer ceux qui ont atteint leur objectif.",
            downloads["size"] * 0.3, "advice", view="seeds", estimate=True)
    if live["percent"] >= 90:
        add("grow", "Agrandir le disque", "Agrandissez le disque virtuel dans votre hyperviseur, puis étendez la partition et le système de fichiers (growpart puis resize2fs, ou lvextend, selon votre disposition).",
            0, "advice")
    return sorted(found, key=lambda r: (r["kind"] == "advice", -r["reclaim"]))


def overview():
    data = saved()
    disks = {v["path"]: v for v in data.get("volumes", [])}
    out = []
    for live in volumes():
        scanned = disks.get(live["path"])
        entry = {k: live[k] for k in ("label", "path", "total", "free", "used", "percent", "level")}
        if scanned:
            entry.update({"items": scanned["items"], "other": scanned["other"], "biggest": scanned["biggest"], "remedies": remedies(scanned, live)})
        else:
            entry.update({"items": [], "other": 0, "biggest": [], "remedies": []})
        out.append(entry)
    return {"scanned": data.get("scanned"), "volumes": out}


# ----------------------------------------------------------------------------
#  Correctifs sûrs
# ----------------------------------------------------------------------------
def fix(name):
    if name == "trash-empty":
        ctl.cleanup().empty()
    elif name == "docker":
        ctl.cleanup().docker_prune()
        if shutil.which("docker"):
            ctl.run("docker", "builder", "prune", "-f", check=False, capture=True)
    elif name == "journal":
        ctl.run("journalctl", f"--vacuum-size={JOURNAL_KEEP // (1 << 20)}M", capture=True)
    elif name == "apt":
        ctl.run("apt-get", "clean", capture=True)
    else:
        raise ctl.SeedboxError(f"correctif inconnu : {name}")
    say(f"correctif appliqué : {name}")
