"""SeedBoxBuntu - alléger les fichiers existants : recompression en x265 et retrait des pistes inutiles.

Deux travaux, une seule file d'attente traitée la nuit (par défaut de 1 h à 7 h), un fichier à la fois :
  - RECOMPRESSION : un film encodé en x264 devient du x265 (HEVC) à qualité visuelle voisine, souvent 30 à 50 % plus petit. Le
    travail dure plusieurs heures par film sur un processeur seul ; la carte graphique n'est pas utilisée ici (le matériel code
    plus vite mais moins bien à taille égale).
  - PISTES : retire sans réencoder les pistes audio et sous-titres dans des langues dont vous ne voulez pas (le gain va de
    quelques % à plus de 10 % pour un fichier à nombreuses pistes). L'image n'est pas touchée.
Le travail se fait dans l'image de Jellyfin (elle embarque ffmpeg) lancée à part, avec les droits du compte de l'appliance.
Un fichier n'est remplacé que si : la sortie est lisible, sa durée est la même (à 1 s près), elle compte les mêmes pistes
vidéo, et elle est plus petite d'au moins 10 % (recompression) ou de 1 % (pistes). L'original est alors DÉPLACÉ dans une corbeille
du même volume (« .sb-corbeille »), jamais effacé, et Radarr ou Sonarr relisent le dossier. Purge de la corbeille : après 14 jours.
Limites assumées : si le fichier est lié à un torrent encore partagé (lien dur), l'espace n'est PAS récupéré tant que le torrent
existe (le gain réel est annoncé à 0 pour ces fichiers, et ils ne sont pas proposés par défaut) ; la qualité perçue dépend du
contenu (les images très granuleuses se compressent mal) ; un fichier peut changer d'extension (.mkv) ; les sous-titres image
(PGS) sont conservés tels quels ; aucune estimation n'est une promesse : seule la taille finale compte.
    seedboxctl mediaslim show | add recompress|tracks CHEMIN... | remove ID | run | clear
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

SETTINGS_FILE = ctl.ETC / "mediaslim.json"
QUEUE_FILE = ctl.STATE / "mediaslim-queue.json"
LOCK_FILE = ctl.STATE / "mediaslim.lock"
DEFAULTS = {"window_start": 1, "window_end": 7, "keep_audio": ["fra", "eng"], "keep_subs": ["fra", "eng"], "crf": 24, "preset": "medium",
            "trash_days": 14, "include_linked": False}
TRASH = ".sb-corbeille"
MIN_SAVING = {"recompress": 0.10, "tracks": 0.01}
MAX_QUEUE = 200
LANG = re.compile(r"^[a-z]{2,3}$")
FFMPEG = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
FFPROBE = "/usr/lib/jellyfin-ffmpeg/ffprobe"


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
        if key in ("window_start", "window_end"):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
                raise ctl.SeedboxError("fenêtre : une heure de 0 à 23")
        elif key in ("keep_audio", "keep_subs"):
            if not isinstance(value, list) or not 1 <= len(value) <= 12 or not all(isinstance(v, str) and LANG.match(v) for v in value):
                raise ctl.SeedboxError("langues : codes de 2 ou 3 lettres (fra, eng...), 1 à 12")
        elif key == "crf":
            if isinstance(value, bool) or not isinstance(value, int) or not 18 <= value <= 30:
                raise ctl.SeedboxError("CRF : de 18 (très fidèle) à 30")
        elif key == "preset":
            if value not in ("fast", "medium", "slow"):
                raise ctl.SeedboxError("préréglage : fast, medium ou slow")
        elif key == "trash_days":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 365:
                raise ctl.SeedboxError("conservation : 1 à 365 jours")
        elif key == "include_linked" and not isinstance(value, bool):
            raise ctl.SeedboxError("include_linked : vrai ou faux")
        cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def load_queue():
    try:
        value = json.loads(QUEUE_FILE.read_text())
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def store_queue(queue):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = QUEUE_FILE.with_name(QUEUE_FILE.name + ".tmp")
    temp.write_text(json.dumps(queue, ensure_ascii=False))
    os.replace(temp, QUEUE_FILE)


# --- candidats ----------------------------------------------------------------------------------------
def candidates(records=None, cfg=None):
    """Fichiers qui gagneraient à être allégés, avec le gain RÉEL estimé (0 si lié à un torrent)."""
    libscan = ctl.libscan()
    records = libscan.records() if records is None else records
    cfg = cfg or settings()
    keep = set(cfg["keep_audio"])
    queued = {q["path"] for q in load_queue()}
    found = []
    for r in records:
        host = libscan.to_host(r["path"])
        links = libscan.link_count(host) if host else 0
        linked = links > 1
        entry = {"path": r["path"], "title": r["title"], "volume": r["volume"], "size": r["size"], "codec": r["codec"], "linked": linked,
                 "queued": r["path"] in queued}
        codec = r["codec"]
        if r["app"] == "radarr" and codec in ("h264", "x264", "avc", "avc1") and r["size"] >= 2 << 30 and r["resolution"] >= 720:
            estimate = int(r["size"] * 0.35)
            found.append({**entry, "kind": "recompress", "estimate": 0 if linked else estimate, "potential": estimate,
                          "why": f"{r['quality'] or r['resolution']} en x264 : environ {estimate >> 30} Go de moins en x265"})
        langs = {x for x in re.split(r"[/,]", r.get("audio", "")) if x.strip()}
        names = {_lang_code(x) for x in langs}
        extra = {n for n in names if n and n not in keep}
        if len(langs) >= 3 and extra:
            estimate = int(r["size"] * min(0.03 * len(extra), 0.12))
            found.append({**entry, "kind": "tracks", "estimate": 0 if linked else estimate, "potential": estimate,
                          "why": f"{len(langs)} pistes audio, dont {len(extra)} dans des langues non gardées"})
    found.sort(key=lambda e: (-e["estimate"], -e["potential"]))
    return [e for e in found if cfg["include_linked"] or not e["linked"]][:300]


LANG_NAMES = {"english": "eng", "french": "fra", "german": "deu", "spanish": "spa", "italian": "ita", "japanese": "jpn", "korean": "kor",
              "portuguese": "por", "russian": "rus", "chinese": "zho", "arabic": "ara", "dutch": "nld", "polish": "pol", "turkish": "tur",
              "hindi": "hin", "swedish": "swe", "danish": "dan", "norwegian": "nor", "finnish": "fin", "czech": "ces", "greek": "ell",
              "hungarian": "hun", "hebrew": "heb", "thai": "tha", "ukrainian": "ukr", "romanian": "ron"}


def _lang_code(name):
    name = name.strip().lower()
    return LANG_NAMES.get(name, name if LANG.match(name) else "")


# --- file d'attente ----------------------------------------------------------------------------------
def add(kind, paths, records=None, by="console"):
    if kind not in MIN_SAVING:
        raise ctl.SeedboxError("travail inconnu : recompress ou tracks")
    libscan = ctl.libscan()
    records = libscan.records() if records is None else records
    known = {r["path"]: r for r in records}
    queue = load_queue()
    added = 0
    for path in paths:
        queue = [q for q in queue if not (q["path"] == str(path) and q["state"] not in ("attente", "en cours"))]   # un travail terminé peut être refait
        record = known.get(str(path))
        if not record:
            raise ctl.SeedboxError("fichier inconnu du relevé des bibliothèques")
        host = libscan.to_host(record["path"])
        if not host or not host.is_file():
            raise ctl.SeedboxError("fichier introuvable sur le serveur")
        if any(q["path"] == record["path"] for q in queue):
            continue
        if len(queue) >= MAX_QUEUE:
            raise ctl.SeedboxError("file d'attente pleine : laissez-la se vider")
        queue.append({"id": f"{int(time.time() * 1000):x}{added:02x}", "kind": kind, "path": record["path"], "title": record["title"],
                      "app": record["app"], "owner": record["id"], "size": record["size"], "state": "attente", "added": int(time.time()), "by": by})
        added += 1
    store_queue(queue)
    say(f"file d'allègement : {added} fichier(s) ajouté(s)")
    return {"added": added}


def remove(item_id):
    queue = load_queue()
    left = [q for q in queue if q["id"] != item_id or q["state"] == "en cours"]
    if len(left) == len(queue):
        raise ctl.SeedboxError("élément introuvable ou en cours de traitement")
    store_queue(left)
    return {"removed": 1}


def clear():
    store_queue([q for q in load_queue() if q["state"] == "en cours"])
    return {"ok": True}


# --- exécution ---------------------------------------------------------------------------------------
def image():
    result = ctl.run("docker", "inspect", "-f", "{{.Config.Image}}", "jellyfin", check=False, capture=True)
    name = result.stdout.strip()
    if not name:
        raise ctl.SeedboxError("image de Jellyfin introuvable : l'allègement en a besoin pour ffmpeg")
    return name


def ffmpeg_command(mounts, arguments, tool=FFMPEG, runner_image=None):
    env = ctl.env_read()
    return ["docker", "run", "--rm", "--network", "none", "--user", f"{env.get('PUID', 1000)}:{env.get('PGID', 1000)}",
            "--entrypoint", tool, *mounts, runner_image or image(), *arguments]


def mounts():
    libscan = ctl.libscan()
    return ["-v", f"{ctl.STORAGE}:/data", "-v", f"{libscan.shares_root()}:/shares"]


def probe(path_in_container, runner=None):
    command = ffmpeg_command(mounts(), ["-v", "error", "-show_entries", "stream=index,codec_type,codec_name:stream_tags=language:format=duration",
                                         "-of", "json", path_in_container], FFPROBE)
    result = (runner or subprocess.run)(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise ctl.SeedboxError("analyse du fichier impossible")
    return json.loads(result.stdout)


def map_arguments(info, cfg, kind):
    """Arguments -map : vidéo toujours ; audio et sous-titres dans les langues gardées (au moins une piste audio garde la priorité)."""
    streams = info.get("streams", [])
    args = ["-map", "0:v"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    kept_audio = [s for s in audio if (s.get("tags", {}).get("language") or "und") in cfg["keep_audio"] or (s.get("tags", {}).get("language") or "und") == "und"]
    if not kept_audio:
        kept_audio = audio[:1]
    kept_subs = [s for s in subs if (s.get("tags", {}).get("language") or "und") in cfg["keep_subs"] or (s.get("tags", {}).get("language") or "und") == "und"]
    for s in kept_audio + kept_subs:
        args += ["-map", f"0:{s['index']}"]
    return args, len(audio) - len(kept_audio), len(subs) - len(kept_subs)


def trash_path(host_path):
    """Corbeille sur le même volume : <racine>/.sb-corbeille/<date>/<nom> ; la racine est le dossier de médias ou le partage."""
    parts = host_path.parts
    base = host_path.parent
    for marker in ("media", "shares"):
        if marker in parts:
            base = Path(*parts[:parts.index(marker) + (2 if marker == "shares" else 1)])
            break
    return base / TRASH / time.strftime("%Y%m%d") / host_path.name


def process(item, cfg=None, runner=None, notify=None, now=None):
    """Traite un élément ; renvoie (état, détail, octets gagnés). Ne remplace le fichier que si toutes les vérifications passent."""
    cfg = cfg or settings()
    libscan = ctl.libscan()
    host = libscan.to_host(item["path"])
    if not host or not host.is_file():
        return "échec", "fichier introuvable", 0
    inside = item["path"]
    work = host.with_name(f".sb-{host.stem}.part.mkv")
    out_inside = str(Path(inside).with_name(work.name))
    info = probe(inside, runner)
    duration = float((info.get("format") or {}).get("duration") or 0)
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if item["kind"] == "recompress":
        if videos and videos[0].get("codec_name") not in ("h264", "avc1", "mpeg4"):
            return "écarté", "ce n'est pas du x264", 0
        args = ["-v", "error", "-y", "-i", inside, "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-c:v", "libx265", "-crf", str(cfg["crf"]),
                "-preset", cfg["preset"], "-tag:v", "hvc1", "-c:a", "copy", "-c:s", "copy", out_inside]
    else:
        maps, drop_audio, drop_subs = map_arguments(info, cfg, "tracks")
        if not (drop_audio or drop_subs):
            return "écarté", "aucune piste à retirer", 0
        args = ["-v", "error", "-y", "-i", inside, *maps, "-c", "copy", out_inside]
    command = ffmpeg_command(mounts(), args)
    try:
        result = (runner or subprocess.run)(command, capture_output=True, text=True, timeout=60 * 60 * 20)
        if result.returncode != 0 or not work.is_file():
            raise ctl.SeedboxError("ffmpeg a échoué : " + (result.stderr or "")[-160:])
        checked = probe(out_inside, runner)
        new_duration = float((checked.get("format") or {}).get("duration") or 0)
        if abs(new_duration - duration) > 1.5:
            raise ctl.SeedboxError("durée différente : sortie rejetée")
        if len([s for s in checked.get("streams", []) if s.get("codec_type") == "video"]) != len(videos[:1] if item["kind"] == "recompress" else videos):
            raise ctl.SeedboxError("pistes vidéo différentes : sortie rejetée")
        old, new = host.stat().st_size, work.stat().st_size
        if new > old * (1 - MIN_SAVING[item["kind"]]):
            raise ctl.SeedboxError(f"gain insuffisant ({max(0, round(100 - 100 * new / old))} %) : original conservé")
        if os.stat(host).st_nlink > 1 and not cfg["include_linked"]:
            raise ctl.SeedboxError("fichier lié à un torrent partagé : espace non récupérable, original conservé")
        kept = trash_path(host)
        kept.parent.mkdir(parents=True, exist_ok=True)
        final = host.with_suffix(".mkv")
        os.replace(host, kept)
        os.replace(work, final)
        if notify:
            notify(item, final)
        return "fait", f"{old >> 20} Mo -> {new >> 20} Mo", old - new
    finally:
        try:
            work.unlink()
        except OSError:
            pass


def rescan(item, final, api=None):
    api = api or ctl.arr_api
    command = {"name": "RescanMovie", "movieId": item["owner"]} if item["app"] == "radarr" else {"name": "RescanSeries", "seriesId": item["owner"]}
    try:
        api(item["app"], "POST", "/command", command)
    except (ctl.SeedboxError, OSError):
        pass


def purge_trash(now=None, cfg=None):
    """Efface les éléments de .sb-corbeille plus vieux que le délai ; renvoie les octets libérés."""
    cfg = cfg or settings()
    now = time.time() if now is None else now
    libscan = ctl.libscan()
    freed = 0
    roots = {libscan.to_host("/data/media"), *[libscan.shares_root() / n for n in (ctl.shares().load().get("shares") or {})]}
    for root in filter(None, roots):
        bin_dir = root / TRASH
        if not bin_dir.is_dir():
            continue
        for day in bin_dir.iterdir():
            try:
                if now - day.stat().st_mtime > cfg["trash_days"] * 86400 and day.is_dir():
                    freed += sum(f.stat().st_size for f in day.rglob("*") if f.is_file())
                    shutil.rmtree(day, ignore_errors=True)
            except OSError:
                continue
    return freed


def in_window(now=None, cfg=None):
    cfg = cfg or settings()
    hour = time.localtime(time.time() if now is None else now).tm_hour
    start, end = cfg["window_start"], cfg["window_end"]
    return start <= hour < end if start < end else hour >= start or hour < end


def running():
    try:
        os.kill(int(LOCK_FILE.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def run(now=None, runner=None, once=False):
    """Traite la file d'attente tant que la fenêtre de nuit est ouverte. Un seul traitement à la fois."""
    cfg = settings()
    done = 0
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    try:
        while in_window(now, cfg) or once:
            queue = load_queue()
            todo = next((q for q in queue if q["state"] == "attente"), None)
            if not todo:
                break
            todo["state"] = "en cours"
            store_queue(queue)
            try:
                state, detail, saved = process(todo, cfg, runner, notify=rescan)
            except (ctl.SeedboxError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                state, detail, saved = "échec", str(error)[:200], 0
            queue = load_queue()
            for q in queue:
                if q["id"] == todo["id"]:
                    q.update(state=state, detail=detail, saved=saved, finished=int(time.time()))
            store_queue(queue)
            say(f"allègement : {todo['title']} -> {state} ({detail})")
            done += 1
            if once:
                break
        purge_trash(cfg=cfg)
    finally:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
    return {"done": done}


def tick(now=None, launcher=None):
    """Passage de 5 minutes : lance le traitement de nuit s'il y a du travail. Ne bloque jamais la surveillance."""
    if running() or not in_window(now):
        return False
    if not any(q["state"] == "attente" for q in load_queue()):
        return False
    if launcher:
        launcher()
        return True
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "seedboxctl.py"), "mediaslim", "run"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def overview():
    cfg = settings()
    queue = load_queue()
    return {"settings": cfg, "queue": queue, "candidates": candidates(None, cfg), "running": running(),
            "saved": sum(q.get("saved", 0) for q in queue if q["state"] == "fait")}
