"""SeedBoxBuntu - titres jamais lus et séries abandonnées.

  - « jamais lu » : un film ou une série ajouté il y a plus de N jours (180 par défaut) et qu'aucun compte Jellyfin n'a jamais lu.
    Si Jellyfin ne remonte AUCUNE lecture, la liste est déclarée peu fiable (rien ne prouve que « jamais » soit vrai) ;
  - « série abandonnée » : suivie par Sonarr, incomplète (il manque des épisodes déjà diffusés), sans aucun fichier ajouté
    depuis plus de N jours (365 par défaut) et sans épisode à venir connu.
Action prudente : « Ne plus suivre » demande à Radarr ou Sonarr d'arrêter la recherche de ce titre (monitored = false). Aucun
fichier n'est touché ; le titre reste dans la médiathèque et se re-suit en un clic dans l'application.
Le relevé est conservé dans /var/lib/seedboxbuntu/dormant.json (« Analyser » le rafraîchit : quelques secondes à quelques
minutes selon le nombre de comptes Jellyfin).
    seedboxctl dormant show | scan | unmonitor radarr|sonarr ID... | set unplayed_days N | set abandon_days N
"""
import json
import os
import time

import seedboxctl as ctl

FILE = ctl.STATE / "dormant.json"
SETTINGS_FILE = ctl.ETC / "dormant.json"
DEFAULTS = {"unplayed_days": 180, "abandon_days": 365}
RANGES = {"unplayed_days": (30, 3650), "abandon_days": (90, 3650)}
MAX_UNMONITOR = 50
SHOWN = 80
EDITOR = {"radarr": ("/movie/editor", "movieIds"), "sonarr": ("/series/editor", "seriesIds")}


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
        low, high = RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ctl.SeedboxError(f"{key} : entier de {low} à {high}")
        cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def _day(text):
    try:
        return int(time.mktime(time.strptime(str(text)[:10], "%Y-%m-%d")))
    except ValueError:
        return 0


def never_played(records, played, cfg, now):
    """Titres ajoutés avant la limite et sans aucune lecture : [{app, id, title, size, added, files}], les plus gros d'abord."""
    limit = now - cfg["unplayed_days"] * 86400
    titles = {}
    for r in records:
        key = (r["app"], r["id"])
        t = titles.setdefault(key, {"app": r["app"], "id": r["id"], "title": r["title"], "size": 0, "files": 0, "added": r["added"], "read": False})
        t["size"] += r["size"]
        t["files"] += 1
        if r["added"] and (not t["added"] or r["added"] < t["added"]):
            t["added"] = r["added"]
        if r["path"] in played:
            t["read"] = True
    found = [t for t in titles.values() if not t["read"] and t["added"] and _day(t["added"]) <= limit]
    for t in found:
        t.pop("read")
    return sorted(found, key=lambda t: -t["size"])


def abandoned(series, last_added, cfg, now):
    """Séries suivies, incomplètes, sans fichier récent ni épisode à venir. `last_added` : {id: date du dernier fichier ajouté}."""
    limit = now - cfg["abandon_days"] * 86400
    found = []
    for show in series:
        stats = show.get("statistics") or {}
        have, total = int(stats.get("episodeFileCount") or 0), int(stats.get("episodeCount") or 0)
        if not show.get("monitored") or not have or have >= total or show.get("nextAiring"):
            continue
        last = last_added.get(show["id"], "")
        if not last or _day(last) > limit:
            continue
        found.append({"app": "sonarr", "id": show["id"], "title": show.get("title", ""), "have": have, "total": total,
                      "last_added": last, "size": int(stats.get("sizeOnDisk") or 0), "ended": bool(show.get("ended"))})
    return sorted(found, key=lambda s: -s["size"])


def scan(now=None, api=None, played=None):
    now = time.time() if now is None else now
    api = api or ctl.arr_api
    cfg = settings()
    libscan = ctl.libscan()
    records = libscan.records()
    if played is None:
        played = ctl.relocate().played_paths()
    reliable = bool(played)
    errors = []
    series = []
    try:
        result = api("sonarr", "GET", "/series")
        if result.ok:
            series = result.json() or []
        else:
            errors.append(f"sonarr : HTTP {result.status}")
    except (ctl.SeedboxError, OSError, ValueError) as error:
        errors.append(f"sonarr : {str(error)[:100]}")
    last = {}
    for r in records:
        if r["app"] == "sonarr" and r["added"] and r["added"] > last.get(r["id"], ""):
            last[r["id"]] = r["added"]
    data = {"at": int(now), "reliable": reliable, "errors": errors,
            "unplayed": never_played(records, played or {}, cfg, now)[:300] if reliable else [],
            "abandoned": abandoned(series, last, cfg, now)[:300]}
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data))
    return data


def load():
    try:
        value = json.loads(FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def unmonitor(app, ids, api=None, by="console"):
    if app not in EDITOR:
        raise ctl.SeedboxError("application inconnue")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_UNMONITOR or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ctl.SeedboxError(f"1 à {MAX_UNMONITOR} identifiants")
    data = load()
    known = {t["id"] for t in data.get("unplayed", []) if t["app"] == app} | {t["id"] for t in data.get("abandoned", []) if t["app"] == app}
    if not set(ids) <= known:
        raise ctl.SeedboxError("titre inconnu du dernier relevé : relancez l'analyse")
    path, field = EDITOR[app]
    result = (api or ctl.arr_api)(app, "PUT", path, {field: ids, "monitored": False})
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : refusé (HTTP {result.status})")
    ctl.audit().record(by, "", "Titres retirés du suivi", f"{len(ids)} titre(s) dans {app}", "console", "ok")
    return {"done": len(ids)}


def overview():
    data = load()
    return {"settings": settings(), "at": data.get("at", 0), "reliable": data.get("reliable", True), "errors": data.get("errors", []),
            "unplayed": data.get("unplayed", [])[:SHOWN], "unplayed_total": len(data.get("unplayed", [])),
            "unplayed_size": sum(t["size"] for t in data.get("unplayed", [])),
            "abandoned": data.get("abandoned", [])[:SHOWN], "abandoned_total": len(data.get("abandoned", [])),
            "abandoned_size": sum(t["size"] for t in data.get("abandoned", []))}
