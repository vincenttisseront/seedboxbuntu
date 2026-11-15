"""SeedBoxBuntu - contrôle d'intégrité des fichiers de la médiathèque.

Repère les fichiers tronqués ou illisibles avant que quelqu'un tombe dessus en pleine lecture. Pour chaque fichier du relevé des
bibliothèques, la nuit, un conteneur jetable (image de Jellyfin, sans réseau, médias montés en lecture seule) lance :
  1. ffprobe : le fichier doit s'ouvrir, avoir une piste vidéo et une durée ;
  2. si la durée annoncée par Radarr (film) ou par le relevé est connue : une durée mesurée sous 85 % de l'annoncée = « tronqué » ;
  3. un décodage des 5 dernières secondes (ffmpeg -sseof -5 ... -f null) : des erreurs de décodage en fin de fichier = « fin abîmée ».
Un fichier n'est contrôlé qu'une fois tant que sa taille et son identifiant dans Radarr/Sonarr ne changent pas. Au plus
`per_night` fichiers par nuit (100 par défaut) pour ne pas user les disques. Désactivé par défaut.
Action : « Chercher une autre version » lance la recherche dans Radarr (film) ou Sonarr (épisode) ; rien n'est supprimé.
Limites assumées : un défaut au milieu du fichier n'est pas vu (ce n'est pas un décodage complet) ; un film dont la durée annoncée
est fausse peut être signalé à tort ; le montage de tous les partages en lecture seule suppose les mêmes chemins que les conteneurs.
    seedboxctl integrity show | run | research APP ID FILE_ID | set enabled on|off | set per_night N
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import seedboxctl as ctl

FILE = ctl.STATE / "integrity.json"
LOCK_FILE = ctl.STATE / "integrity.lock"
SETTINGS_FILE = ctl.ETC / "integrity.json"
DEFAULTS = {"enabled": False, "per_night": 100, "window_start": 1, "window_end": 7, "deep": True}
MIN_RATIO = 0.85
SHOWN = 100


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        if key in ("enabled", "deep"):
            if not isinstance(value, bool):
                raise ctl.SeedboxError(f"{key} : vrai ou faux")
        elif isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 2000 if key == "per_night" else 0 <= value <= 23):
            raise ctl.SeedboxError(f"{key} : valeur hors limites")
        cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def load():
    try:
        value = json.loads(FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def store(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data))


def in_window(now=None, cfg=None):
    cfg = cfg or settings()
    hour = time.localtime(time.time() if now is None else now).tm_hour
    start, end = cfg["window_start"], cfg["window_end"]
    return start <= hour < end if start < end else hour >= start or hour < end


def pending(records=None, data=None):
    """Fichiers jamais contrôlés (ou changés depuis), les plus récents d'abord."""
    records = ctl.libscan().records() if records is None else records
    done = (data if data is not None else load()).get("files", {})
    todo = [r for r in records if (done.get(r["path"]) or {}).get("size") != r["size"] or (done.get(r["path"]) or {}).get("file_id") != r["file_id"]]
    return sorted(todo, key=lambda r: r["added"], reverse=True)


def expected_seconds(record):
    if record.get("runtime"):
        return record["runtime"] * 60
    return record.get("seconds") or 0


def judge(info, record, tail_errors):
    """(état, raison) d'après la sortie ffprobe et les erreurs du décodage de la fin."""
    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in streams):
        return "défaut", "aucune piste vidéo"
    try:
        duration = float((info.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0
    if duration <= 0:
        return "défaut", "durée illisible"
    expected = expected_seconds(record)
    if expected and duration < expected * MIN_RATIO:
        return "défaut", f"tronqué : {round(100 * duration / expected)} % de la durée annoncée"
    if tail_errors:
        return "défaut", "fin du fichier abîmée (erreurs de décodage)"
    return "bon", ""


def check_file(record, cfg, runner=None):
    slim = ctl.mediaslim()
    run = runner or subprocess.run
    info = slim.probe(record["path"], runner=run)
    errors = ""
    if cfg["deep"] and (info.get("format") or {}).get("duration"):
        command = slim.ffmpeg_command(slim.mounts(), ["-v", "error", "-sseof", "-5", "-i", record["path"], "-f", "null", "-"])
        result = run(command, capture_output=True, text=True, timeout=600)
        errors = (result.stderr or "").strip() if result.returncode == 0 else (result.stderr or "erreur").strip()
    return judge(info, record, errors)


def run(now=None, runner=None, once=False, records=None):
    cfg = settings()
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    checked = 0
    try:
        data = load()
        data.setdefault("files", {})
        for record in pending(records, data):
            if checked >= cfg["per_night"] or not (once or in_window(now, cfg)):
                break
            try:
                state, reason = check_file(record, cfg, runner)
            except (ctl.SeedboxError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                state, reason = "inconnu", str(error)[:120]
            data["files"][record["path"]] = {"size": record["size"], "file_id": record["file_id"], "state": state, "reason": reason, "at": int(time.time()),
                                              "title": record["title"], "app": record["app"], "id": record["id"]}
            checked += 1
            if checked % 10 == 0:
                store(data)
        data["last_run"] = int(time.time())
        store(data)
    finally:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
    return {"checked": checked}


def running():
    try:
        os.kill(int(LOCK_FILE.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def tick(now=None, launcher=None):
    if not settings()["enabled"] or running() or not in_window(now):
        return False
    if not pending():
        return False
    if launcher:
        launcher()
        return True
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "seedboxctl.py"), "integrity", "run"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def problems(data=None):
    """Fichiers douteux dont la taille n'a pas changé depuis le contrôle."""
    files = (data if data is not None else load()).get("files", {})
    return [{"path": p, **v} for p, v in files.items() if v.get("state") == "défaut"]


def research(app, owner_id, file_id, api=None):
    """Lance la recherche d'une autre version : film entier (Radarr) ou épisode du fichier (Sonarr)."""
    api = api or ctl.arr_api
    if app == "radarr":
        result = api("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [int(owner_id)]})
    elif app == "sonarr":
        episodes = api("sonarr", "GET", f"/episode?seriesId={int(owner_id)}")
        ids = [e["id"] for e in (episodes.json() or []) if e.get("episodeFileId") == int(file_id)] if episodes.ok else []
        if not ids:
            raise ctl.SeedboxError("épisode introuvable pour ce fichier")
        result = api("sonarr", "POST", "/command", {"name": "EpisodeSearch", "episodeIds": ids})
    else:
        raise ctl.SeedboxError("application inconnue")
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : recherche refusée (HTTP {result.status})")
    return {"started": True}


def overview():
    data = load()
    cfg = settings()
    records = ctl.libscan().records()
    return {"settings": cfg, "running": running(), "last_run": data.get("last_run", 0), "checked": len(data.get("files", {})),
            "pending": len(pending(records, data)), "total": len(records), "problems": problems(data)[:SHOWN],
            "unknown": sum(1 for v in data.get("files", {}).values() if v.get("state") == "inconnu")}
