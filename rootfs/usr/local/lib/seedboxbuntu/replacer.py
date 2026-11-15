"""SeedBoxBuntu - remplacer un média par une meilleure version SANS faire exploser l'espace : recherche, budget, filet.

Trois temps, dans cet ordre :
  1. RECHERCHE (en tâche de fond, lente : chaque titre interroge tous les indexeurs). Pour chaque film ou épisode sous le seuil de
     qualité (ou très compressé), la console demande à Radarr/Sonarr les versions disponibles et retient la meilleure
     « amélioration à moindre coût » : résolution supérieure, ou encodage plus récent (x265) à résolution égale, sans grossir
     de plus de X % (10 % par défaut), avec assez de sources (seeders) et sans autre rejet que « ce n'est pas une amélioration ».
  2. BUDGET. Par volume (disque local, chaque partage), un budget de croissance en Go et un espace libre à préserver. Les
     propositions sont classées par gain de qualité par Go ajouté ; la sélection s'arrête quand le budget ou l'espace libre
     serait dépassé. La simulation dit : croissance, pic temporaire (l'ancien fichier reste dans la corbeille de Radarr/Sonarr
     le temps du délai) et bilan final.
  3. FILET. Aucune version n'est téléchargée sans corbeille active dans Radarr et Sonarr (dossier + délai de conservation) :
     l'ancien fichier y est déplacé au lieu d'être effacé, et peut être remis tant que le délai n'est pas écoulé.
Limites assumées : une proposition n'est pas une garantie (Radarr/Sonarr peuvent refuser l'import à l'arrivée, par exemple si la
version n'est pas jugée meilleure par leur profil) ; la taille d'une version annoncée par un indexeur peut être inexacte ;
l'encodage est deviné d'après le nom de la version ; le pic temporaire compte les deux fichiers jusqu'à la purge de la corbeille.
    seedboxctl replacer show | search [N] | plan | apply ID... | safety on [JOURS]
"""
import json
import os
import re
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "replacer.json"
LAST_FILE = ctl.STATE / "replacer.json"
DEFAULTS = {"max_growth_pct": 10, "min_seeders": 2, "per_run": 10, "budget_gb": 50, "keep_free_gb": 100,
            "recycle_days": 14, "recycle_path": "/data/corbeille-medias"}
LIMITS = {"max_growth_pct": (0, 200), "min_seeders": (0, 1000), "per_run": (1, 40), "budget_gb": (0, 100000), "keep_free_gb": (0, 100000),
          "recycle_days": (1, 365)}
GB = 1 << 30
TIERS = [0, 480, 576, 720, 1080, 2160]
SOFT = re.compile(r"(?i)upgrade|cutoff|already|existing file|meets|equal or higher|better|not wanted in profile|déjà|amélior")
PROPOSALS_MAX = 120


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    cfg = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS or k == "budgets"}}
    cfg.setdefault("budgets", {})
    return cfg


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key == "budgets":
            if not isinstance(value, dict) or len(value) > 30:
                raise ctl.SeedboxError("budgets : un dictionnaire volume -> Go")
            clean = {}
            for volume, gb in value.items():
                if not re.match(r"^[a-z][a-z0-9]{0,19}$|^local$", str(volume)) or isinstance(gb, bool) or not isinstance(gb, int) or not 0 <= gb <= 100000:
                    raise ctl.SeedboxError("budget par volume : nom de partage valide, Go entier de 0 à 100000")
                clean[str(volume)] = gb
            cfg["budgets"] = clean
        elif key == "recycle_path":
            if not re.match(r"^/(data|shares)/[\w .-]+(/[\w .-]+){0,3}$", str(value)) or ".." in str(value):
                raise ctl.SeedboxError("dossier de la corbeille : un chemin sous /data ou /shares")
            cfg[key] = str(value)
        elif key in LIMITS:
            low, high = LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ctl.SeedboxError(f"{key} : entier de {low} à {high}")
            cfg[key] = value
        else:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


# --- lecture des versions disponibles ---------------------------------------------------------------
def guess_codec(title):
    text = str(title or "").lower()
    if re.search(r"x\.?265|h\.?265|hevc", text):
        return "x265"
    if re.search(r"x\.?264|h\.?264|avc", text):
        return "x264"
    if "av1" in text:
        return "av1"
    return ""


def _codec_of(name):
    return guess_codec(name or "") or ("x265" if "265" in str(name) else "x264" if "264" in str(name) else "")


def _rank(resolution):
    return max((i for i, t in enumerate(TIERS) if resolution >= t), default=0)


def judge(current, release, cfg):
    """Un candidat est-il une amélioration à moindre coût ? Renvoie (gain, raison) ; gain 0 = non."""
    if release.get("rejections") and any(not SOFT.search(r) for r in release["rejections"]):
        return 0, "refusée par l'application"
    if release.get("protocol") == "torrent" and release.get("seeders", 0) < cfg["min_seeders"]:
        return 0, "trop peu de sources"
    steps = _rank(release["resolution"]) - _rank(current["resolution"])
    if steps < 0:
        return 0, "résolution inférieure"
    delta = release["size"] - current["size"]
    allowed = current["size"] * (cfg["max_growth_pct"] + 100 * steps) / 100        # une marche de résolution autorise jusqu'à +100 %
    if delta > allowed:
        return 0, "trop gros par rapport au gain"
    gain, why = 0, []
    if steps > 0:
        gain += 100 * steps
        why.append(f"résolution {current['resolution'] or '?'} → {release['resolution']}")
    if release["codec"] == "x265" and _codec_of(current.get("codec", "")) == "x264":
        gain += 30
        why.append("encodage plus récent (x265)")
    return gain, " ; ".join(why)


def parse_release(item):
    quality = (item.get("quality") or {}).get("quality") or {}
    return {"guid": item.get("guid", ""), "indexer_id": item.get("indexerId", 0), "indexer": str(item.get("indexer", ""))[:40],
            "title": str(item.get("title", ""))[:200], "size": int(item.get("size") or 0), "quality": quality.get("name", ""),
            "resolution": int(quality.get("resolution") or 0), "seeders": int(item.get("seeders") or 0), "age": int(item.get("age") or 0),
            "protocol": str(item.get("protocol", "")).lower(), "codec": guess_codec(item.get("title", "")),
            "rejections": [str(r)[:160] for r in (item.get("rejections") or [])]}


def releases(app, cid, api):
    path = f"/release?movieId={cid}" if app == "radarr" else f"/release?episodeId={cid}"
    result = api(app, "GET", path)
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : recherche refusée (HTTP {result.status})")
    return [parse_release(i) for i in (result.json() or []) if i.get("guid")]


def candidate_list(api, records, limit):
    """Titres à améliorer : sous le seuil (Radarr, Sonarr) puis films très compressés ; le plus ancien relevé d'abord."""
    found, seen = [], set()

    def add(app, cid, title, file, volume, reason):
        if (app, cid) in seen or not file:
            return
        seen.add((app, cid))
        quality = (file.get("quality") or {}).get("quality") or {}
        info = file.get("mediaInfo") or {}
        found.append({"app": app, "id": cid, "title": title, "volume": volume, "reason": reason,
                      "cur": {"size": int(file.get("size") or 0), "quality": quality.get("name", ""), "resolution": int(quality.get("resolution") or 0),
                              "codec": str(info.get("videoCodec") or "").lower(), "path": file.get("path", "")}})
    libscan = ctl.libscan()
    for app, extra in (("radarr", ""), ("sonarr", "&includeSeries=true&includeEpisodeFile=true")):
        try:
            result = api(app, "GET", f"/wanted/cutoff?page=1&pageSize={limit * 2}{extra}")
            if not result.ok:
                continue
            for row in (result.json() or {}).get("records", []):
                if app == "radarr":
                    file = row.get("movieFile")
                    add(app, row["id"], row.get("title", ""), file, libscan.volume_of((file or {}).get("path") or row.get("path", "")), "sous le seuil")
                else:
                    file = row.get("episodeFile")
                    title = f"{(row.get('series') or {}).get('title', '')} S{row.get('seasonNumber', 0):02d}E{row.get('episodeNumber', 0):02d}"
                    add(app, row["id"], title, file, libscan.volume_of((file or {}).get("path", "")), "sous le seuil")
        except (ctl.SeedboxError, OSError, ValueError, KeyError):
            continue
    for r in sorted((r for r in records if r["app"] == "radarr" and r.get("seconds", 0) >= 1800), key=lambda r: r["size"] / r["seconds"]):
        if r["size"] / (1 << 20) / (r["seconds"] / 60) >= 2.5:
            break
        add("radarr", r["id"], r["title"], {"size": r["size"], "quality": {"quality": {"name": r["quality"], "resolution": r["resolution"]}},
                                              "mediaInfo": {"videoCodec": r["codec"]}, "path": r["path"]}, r["volume"], "très compressé")
    return found[:limit]


def search(count=None, api=None, sleep=time.sleep):
    """Recherche des améliorations ; mémorise les propositions. Lent par nature : lancer en tâche de fond."""
    api = api or ctl.arr_api
    cfg = settings()
    count = int(count or cfg["per_run"])
    records = ctl.libscan().records()
    proposals, errors, searched = [], [], 0
    for cand in candidate_list(api, records, count):
        try:
            found = releases(cand["app"], cand["id"], api)
        except (ctl.SeedboxError, OSError, ValueError) as error:
            errors.append(f"{cand['title']} : {str(error)[:100]}")
            continue
        searched += 1
        best = None
        for release in found:
            gain, why = judge(cand["cur"], release, cfg)
            if gain:
                delta = release["size"] - cand["cur"]["size"]
                if best is None or (gain, -delta) > (best["gain"], -best["delta"]):
                    best = {"gain": gain, "why": why, "delta": delta, "release": release}
        if best:
            proposals.append({"id": f"{cand['app']}:{cand['id']}", "app": cand["app"], "cid": cand["id"], "title": cand["title"],
                              "volume": cand["volume"], "reason": cand["reason"], "cur": cand["cur"], **best})
        sleep(2)
    proposals.sort(key=lambda p: -(p["gain"] / max(p["delta"] / GB, 0.2)))
    data = {"at": int(time.time()), "proposals": proposals[:PROPOSALS_MAX], "searched": searched, "errors": errors[:10]}
    LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(json.dumps(data, ensure_ascii=False))
    say(f"remplacements : {searched} titre(s) examiné(s), {len(proposals)} proposition(s)")
    return {"searched": searched, "proposals": len(proposals)}


def last():
    try:
        return json.loads(LAST_FILE.read_text())
    except (OSError, ValueError):
        return {"at": 0, "proposals": [], "searched": 0, "errors": []}


# --- budget et simulation ---------------------------------------------------------------------------
def plan(proposals, free, cfg=None, only=None):
    """Sélection sous budget. free : {volume: octets libres}. Renvoie {selected, deferred, growth, peak, final}."""
    cfg = cfg or settings()
    chosen = [p for p in proposals if only is None or p["id"] in only]
    chosen.sort(key=lambda p: -(p["gain"] / max(p["delta"] / GB, 0.2)))
    used_growth, used_peak, selected, deferred = {}, {}, [], []
    for p in chosen:
        volume = p["volume"]
        budget = cfg["budgets"].get(volume, cfg["budget_gb"]) * GB
        growth = max(p["delta"], 0)
        peak = p["release"]["size"]
        if used_growth.get(volume, 0) + growth > budget:
            deferred.append({**p, "defer": "budget de croissance dépassé"})
            continue
        room = free.get(volume)
        if room is not None and room - used_peak.get(volume, 0) - peak < cfg["keep_free_gb"] * GB:
            deferred.append({**p, "defer": "espace libre insuffisant"})
            continue
        used_growth[volume] = used_growth.get(volume, 0) + growth
        used_peak[volume] = used_peak.get(volume, 0) + peak
        selected.append(p)
    return {"selected": selected, "deferred": deferred, "growth": sum(max(p["delta"], 0) for p in selected),
            "peak": sum(p["release"]["size"] for p in selected), "final": sum(p["delta"] for p in selected)}


def free_by_volume():
    return {name: space[1] for name, space in ctl.libscan().volumes_space().items() if space and space[1] is not None}


# --- filet : corbeille de Radarr et Sonarr ----------------------------------------------------------
def safety(api=None):
    api = api or ctl.arr_api
    state = {}
    for app in ("radarr", "sonarr"):
        try:
            result = api(app, "GET", "/config/mediamanagement")
            data = result.json() or {} if result.ok else {}
            state[app] = {"path": data.get("recycleBin", ""), "days": int(data.get("recycleBinCleanupDays", 0) or 0),
                          "ok": bool(data.get("recycleBin")) and int(data.get("recycleBinCleanupDays", 0) or 0) > 0}
        except (ctl.SeedboxError, OSError, ValueError):
            state[app] = {"path": "", "days": 0, "ok": False, "error": "injoignable"}
    size = 0
    host = ctl.libscan().to_host(settings()["recycle_path"])
    if host and host.is_dir():
        for folder, _dirs, files in os.walk(host):
            for name in files:
                try:
                    size += os.path.getsize(os.path.join(folder, name))
                except OSError:
                    pass
    return {"apps": state, "ok": all(s["ok"] for s in state.values()), "size": size}


def enable_safety(days=None, api=None):
    api = api or ctl.arr_api
    cfg = settings()
    days = int(days or cfg["recycle_days"])
    for app in ("radarr", "sonarr"):
        result = api(app, "GET", "/config/mediamanagement")
        if not result.ok:
            raise ctl.SeedboxError(f"{app} : réglages illisibles (HTTP {result.status})")
        data = result.json() or {}
        data.update(recycleBin=cfg["recycle_path"], recycleBinCleanupDays=days)
        put = api(app, "PUT", f"/config/mediamanagement/{data.get('id', 1)}", data)
        if not put.ok:
            raise ctl.SeedboxError(f"{app} : corbeille refusée (HTTP {put.status})")
    if days != cfg["recycle_days"]:
        save({"recycle_days": days})
    say(f"corbeille activée : {cfg['recycle_path']}, {days} jours")
    return safety(api)


# --- application ------------------------------------------------------------------------------------
def apply(ids, api=None, by="console", free=None):
    """Télécharge les versions choisies, dans la limite du budget. Chaque version est redemandée juste avant (les liens expirent)."""
    api = api or ctl.arr_api
    state = safety(api)
    if not state["ok"]:
        raise ctl.SeedboxError("corbeille inactive dans Radarr ou Sonarr : activez le filet avant de remplacer quoi que ce soit")
    wanted = {str(i) for i in ids}
    if not wanted:
        raise ctl.SeedboxError("aucune proposition choisie")
    data = last()
    free = free_by_volume() if free is None else free
    chosen = plan(data["proposals"], free, only=wanted)
    done, skipped = 0, [{"id": p["id"], "title": p["title"], "reason": p["defer"]} for p in chosen["deferred"]]
    cfg = settings()
    for p in chosen["selected"]:
        try:
            fresh = [r for r in releases(p["app"], p["cid"], api) if r["guid"] == p["release"]["guid"]
                     or (r["title"] == p["release"]["title"] and r["size"] == p["release"]["size"])]
            if not fresh:
                skipped.append({"id": p["id"], "title": p["title"], "reason": "la version n'est plus proposée"})
                continue
            if not judge(p["cur"], fresh[0], cfg)[0]:
                skipped.append({"id": p["id"], "title": p["title"], "reason": "n'est plus une amélioration"})
                continue
            grab = api(p["app"], "POST", "/release", {"guid": fresh[0]["guid"], "indexerId": fresh[0]["indexer_id"]})
            if not grab.ok:
                skipped.append({"id": p["id"], "title": p["title"], "reason": f"refusé (HTTP {grab.status})"})
                continue
            done += 1
        except (ctl.SeedboxError, OSError, ValueError) as error:
            skipped.append({"id": p["id"], "title": p["title"], "reason": str(error)[:100]})
    try:
        ctl.audit().record(by, "", "Remplacements de médias lancés", f"{done} version(s)", "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    try:
        ctl.libreport().record("replace", done)
    except Exception:  # noqa: BLE001
        pass
    say(f"remplacements lancés : {done}, écartés : {len(skipped)}")
    return {"done": done, "skipped": skipped}


def overview(api=None):
    cfg = settings()
    data = last()
    try:
        free = free_by_volume()
    except Exception:  # noqa: BLE001
        free = {}
    result = {"settings": cfg, "at": data.get("at", 0), "searched": data.get("searched", 0), "errors": data.get("errors", []),
              "free": free, "plan": plan(data["proposals"], free, cfg)}
    try:
        result["safety"] = safety(api)
    except Exception as error:  # noqa: BLE001
        result["safety"] = {"apps": {}, "ok": False, "size": 0, "error": str(error)[:100]}
    return result
