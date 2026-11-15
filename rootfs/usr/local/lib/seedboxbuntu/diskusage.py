"""SeedBoxBuntu - espace disque par dossier : qui fait grossir le disque, et à quelle vitesse.

La prévision « disque plein dans N jours » dit QUAND, pas QUI. Une fois par jour (en tâche de fond : une mesure peut durer
plusieurs minutes sur une grosse médiathèque), la taille de chaque dossier est relevée :
  - chaque sous-dossier du stockage (téléchargements, médias, importations, corbeille du nettoyage...) ;
  - la configuration de chaque application (/opt/seedbox/config/<app>) ;
  - Docker (images, volumes, cache de construction) d'après « docker system df ».
Les mesures vont dans metrics.db (clés « du.<nom> », absentes de l'historique machine). La page montre la taille, la
croissance par jour sur 30 jours (régression, comme la prévision des disques) et la part de chaque dossier. Quand une
prévision fiable annonce un disque plein dans moins de 30 jours, une alerte nomme le dossier qui grossit le plus.
Limite assumée : la croissance est celle de la taille des fichiers (du), pas des blocs réellement occupés : un fichier
creux ou une copie en dur compte deux fois ; la tendance, elle, reste fiable. Dossiers suivis : 60 au plus.
    seedboxctl diskusage show | scan
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import seedboxctl as ctl

LAST_FILE = ctl.STATE / "diskusage-last.json"
LOCK_FILE = ctl.STATE / "diskusage.lock"
EVERY = 20 * 3600
WINDOW = 30 * 86400
MAX_DIRS = 60
TIMEOUT = 900
KEY = re.compile(r"^du\.[a-z0-9][a-z0-9._-]{0,60}$")
SKIP = {"lost+found", ".Trash", ".seedbox-bibliotheque"}
MIN_GROWTH = 50 << 20                                   # sous 50 Mo par jour : bruit, pas une alerte


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def slug(text):
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-")[:56] or "dossier"


def folders():
    """[(clé, libellé, chemin)] des dossiers à mesurer."""
    rows = []
    try:
        for path in sorted(ctl.STORAGE.iterdir()):
            if path.is_dir() and not path.is_symlink() and path.name not in SKIP:
                rows.append((f"du.stockage-{slug(path.name)}", f"Stockage : {path.name}", path))
                if path.name == "media":
                    for sub in sorted(path.iterdir()):
                        if sub.is_dir() and not sub.is_symlink() and sub.name not in SKIP:
                            rows.append((f"du.media-{slug(sub.name)}", f"Médias : {sub.name}", sub))
    except OSError:
        pass
    try:
        for path in sorted(ctl.CONFIG.iterdir()):
            if path.is_dir() and not path.is_symlink():
                rows.append((f"du.config-{slug(path.name)}", f"Configuration : {path.name}", path))
    except OSError:
        pass
    return rows[:MAX_DIRS]


def du(path, timeout=TIMEOUT):
    """Taille en octets d'un dossier (même système de fichiers), ou None si la mesure échoue ou dépasse le délai."""
    try:
        result = subprocess.run(["nice", "-n", "19", "ionice", "-c3", "du", "-sxb", str(path)], capture_output=True, text=True, timeout=timeout)
        return int(result.stdout.split()[0]) if result.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def docker_sizes():
    """Tailles des catégories de « docker system df » : {clé: octets}."""
    try:
        result = subprocess.run(["docker", "system", "df", "--format", "{{json .}}"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return {}
    names = {"Images": "images", "Containers": "conteneurs", "Local Volumes": "volumes", "Build Cache": "cache"}
    out = {}
    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = names.get(row.get("Type"))
        size = ctl.sysinfo().size_to_bytes(str(row.get("Size", ""))) if key else 0
        if key:
            out[f"du.docker-{key}"] = size
    return out


def measure(measurer=du, docker=docker_sizes, folder_list=None):
    values = {}
    for key, _, path in (folder_list if folder_list is not None else folders()):
        size = measurer(path)
        if size is not None and KEY.match(key):
            values[key] = float(size)
    values.update({k: float(v) for k, v in docker().items() if KEY.match(k)})
    return values


def scan(now=None, measurer=du, docker=docker_sizes, folder_list=None):
    """Mesure tout et l'enregistre (processus de fond ou ligne de commande)."""
    now = time.time() if now is None else now
    values = measure(measurer, docker, folder_list)
    ctl.metrics().collect(values, now)
    LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(json.dumps({"time": int(now), "count": len(values)}))
    say(f"espace par dossier : {len(values)} mesure(s)")
    return values


def last():
    try:
        value = json.loads(LAST_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def due(now=None):
    now = time.time() if now is None else now
    return now - last().get("time", 0) >= EVERY


def running():
    try:
        pid = int(LOCK_FILE.read_text())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def tick(now=None, launcher=None):
    """Passage de 5 minutes : lance la mesure du jour en tâche de fond si elle est due. Ne bloque jamais la surveillance."""
    if not due(now) or running():
        return False
    if launcher:
        launcher()
        return True
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "seedboxctl.py"), "diskusage", "scan"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ctl.STATE.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(str(process.pid))
    except OSError:
        pass
    return True


# --- lecture ----------------------------------------------------------------------------------------
def labels():
    return {key: label for key, label, _ in folders()}


def rows(now=None):
    metrics = ctl.metrics()
    names = labels()
    found = []
    for key in metrics.keys():
        if not key.startswith("du."):
            continue
        series = [(t, v) for t, v in metrics.points(key, WINDOW, now)]
        if not series:
            continue
        size = series[-1][1]
        per_day, span = 0, 0.0
        if len(series) >= 3 and series[-1][0] - series[0][0] >= 2 * 86400:
            slope, _ = metrics.fit(series)
            per_day, span = int(slope * 86400), round((series[-1][0] - series[0][0]) / 86400, 1)
        label = names.get(key) or ("Docker : " + key.split("docker-", 1)[1] if key.startswith("du.docker-") else key[3:])
        found.append({"key": key, "label": label, "size": int(size), "per_day": per_day, "span_days": span,
                      "spark": [int(v) for _, v in series[-30:]]})
    total = sum(r["size"] for r in found) or 1
    for row in found:
        row["share"] = round(100 * row["size"] / total, 1)
    found.sort(key=lambda r: -r["size"])
    return found


def overview(now=None):
    state = last()
    data = rows(now)
    growers = sorted((r for r in data if r["per_day"] > 0), key=lambda r: -r["per_day"])[:3]
    return {"rows": data, "last": int(state.get("time", 0)), "running": running(), "top_growers": [r["key"] for r in growers],
            "every_hours": EVERY // 3600}


def findings(now=None):
    """Alerte quand un disque doit se remplir en moins de 30 jours : on nomme le dossier qui grossit le plus."""
    try:
        risky = ctl.metrics().risky(30, now)
    except Exception:  # noqa: BLE001
        return {}
    if not risky:
        return {}
    growers = [r for r in rows(now) if r["per_day"] >= MIN_GROWTH]
    if not growers:
        return {}
    growers.sort(key=lambda r: -r["per_day"])
    top = growers[0]
    gb = round(top["per_day"] / (1 << 30), 1)
    return {"du:" + top["key"]: (f"L'espace se remplit surtout par « {top['label']} »",
                                 f"Ce dossier grossit d'environ {gb} Go par jour ; un disque doit être plein dans moins de 30 jours "
                                 f"(voir Système > Santé des disques).", "warning")}
