"""SeedBoxBuntu - ratios réels des trackers, tels que les voit p2p-aggregator.

p2p-aggregator se connecte à chaque tracker (avec tracker-session pour renouveler les sessions protégées, comme C411) et garde
pour chaque compte : ratio, ratio exigé, envoyé, reçu, messages non lus. Ce module lit ces chiffres (GET /api/dashboard/stats)
et les met à côté de ce que mesure qBittorrent : le ratio du tracker fait foi, celui de qBittorrent n'est qu'une estimation.
  - historique : un relevé toutes les 6 heures (400 points) d'où l'évolution du ratio et de l'envoyé sur 7 jours ;
  - rapprochement : un tracker de qBittorrent (hôte d'annonce, par exemple tracker.c411.org) est relié à un compte de
    p2p-aggregator par le nom de son domaine (c411) ou par un alias déclaré à la main ;
  - garde-fou : si le ratio du site est sous le ratio exigé, les torrents de ce tracker sont tous classés « protégé » par
    l'optimisation des seeds : rien n'est proposé au retrait tant que le ratio n'est pas redressé ;
  - alerte : ratio sous l'exigé (une fois par jour au plus et par compte).
Compte lu : celui de service de p2p-aggregator d'abord ; s'il ne voit aucun tracker (les comptes ajoutés à la main dans
l'interface appartiennent à « admin »), le compte « admin » avec le mot de passe de l'assistant, comme le fait déjà le reste de
la console. Le mot de passe n'est jamais enregistré ici.
Limites assumées : p2p-aggregator ne sait pas lire le ratio de tous les trackers (champ vide = non pris en charge) ; ses chiffres
peuvent avoir plusieurs heures ; l'actualisation force un test de chaque compte, donc une connexion à chaque tracker.
    seedboxctl trackerstats show | refresh | alias HOTE_QBIT NOM_COMPTE | unalias HOTE_QBIT | alert on|off
"""
import json
import os
import re
import time
import urllib.parse

import seedboxctl as ctl

CACHE = ctl.STATE / "trackerstats.json"
SETTINGS_FILE = ctl.ETC / "trackerstats.json"
DEFAULTS = {"aliases": {}, "alert": True}
HOST = re.compile(r"^[a-z0-9][a-z0-9.-]{0,98}$")
KEEP = 400
EVERY = 3600
HISTORY_EVERY = 6 * 3600
DAY = 86400


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def _read(path, default):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def settings():
    stored = _read(SETTINGS_FILE, {})
    cfg = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    cfg["aliases"] = dict(cfg["aliases"]) if isinstance(cfg["aliases"], dict) else {}
    return cfg


def _write(cfg):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def set_alias(host, name):
    host = str(host).strip().lower()
    if not HOST.match(host) or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ctl.SeedboxError("alias : un hôte de tracker et le nom du compte dans p2p-aggregator")
    cfg = settings()
    if len(cfg["aliases"]) >= 100 and host not in cfg["aliases"]:
        raise ctl.SeedboxError("trop d'alias")
    cfg["aliases"][host] = name.strip()
    return _write(cfg)


def remove_alias(host):
    cfg = settings()
    cfg["aliases"].pop(str(host).strip().lower(), None)
    return _write(cfg)


def set_alert(on):
    if not isinstance(on, bool):
        raise ctl.SeedboxError("alert : vrai ou faux")
    cfg = settings()
    cfg["alert"] = on
    return _write(cfg)


# --- lecture de p2p-aggregator ----------------------------------------------------------------------------
def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalise(payload):
    """Lignes simples à partir de la réponse /api/dashboard/stats."""
    rows = []
    for t in (payload or {}).get("trackers", []) if isinstance(payload, dict) else []:
        stats = t.get("stats") or {}
        host = (urllib.parse.urlsplit(t.get("siteUrl") or "").hostname or "").lower()
        ratio, required = stats.get("ratio"), stats.get("requiredRatio")
        danger = stats.get("ratioHealth") == "danger" or bool(stats.get("lowRatio")) or (
            isinstance(ratio, (int, float)) and isinstance(required, (int, float)) and required > 0 and ratio < required)
        rows.append({"name": str(t.get("name", ""))[:80], "key": str(t.get("trackerKey", ""))[:40], "host": host, "enabled": bool(t.get("enabled", True)),
                     "id": str(t.get("id", ""))[:60], "ratio": ratio if isinstance(ratio, (int, float)) else None,
                     "required": required if isinstance(required, (int, float)) else None, "up": _int(stats.get("uploadedBytes")), "down": _int(stats.get("downloadedBytes")),
                     "unread": stats.get("unreadMessages"), "fetched_at": stats.get("fetchedAt"), "error": (stats.get("error") or "")[:160] or None,
                     "supported": bool(stats.get("supported", True)), "danger": danger})
    return rows


def has_stats(rows):
    return any(r["ratio"] is not None or r["up"] is not None for r in rows)


def fetch(api=None, login=None):
    """Lit les trackers : compte de service, puis « admin » s'il ne voit rien."""
    api = api or ctl.p2p_api
    rows = normalise(api("GET", "/api/dashboard/stats").json())
    if has_stats(rows) or not (login or ctl.p2p_login):
        return rows
    try:
        env = ctl.env_read()
        reply = (login or ctl.p2p_login)("admin", ctl.admin_password(env))
        if reply.ok:
            return normalise(api("GET", "/api/dashboard/stats", cookie=reply.cookies()).json())
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        pass
    return rows


def cache():
    return _read(CACHE, {})


def session_health(api=None):
    """État du service tracker-session vu par p2p-aggregator (prêt, dégradé, injoignable, non configuré) ; None si illisible."""
    try:
        data = (api or ctl.p2p_api)("GET", "/api/admin/tracker-session").json()
    except (ctl.SeedboxError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {"state": str(data.get("state", ""))[:20], "reachable": bool(data.get("reachable")), "healthy": bool(data.get("healthy")),
            "latency_ms": data.get("latencyMs"), "message": str(data.get("message", ""))[:200], "trackers": [str(x)[:30] for x in (data.get("supportedTrackers") or [])][:20],
            "service": str(data.get("service") or "")[:40]}


def _store(data):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data))


def refresh(api=None, login=None, now=None, force_test=False):
    """Relit les ratios (et, si demandé, force un test de chaque compte d'abord) ; met à jour le cache et l'historique."""
    now = int(time.time() if now is None else now)
    api = api or ctl.p2p_api
    if force_test:
        for row in fetch(api, login):
            if row["enabled"] and row["id"]:
                try:
                    api("POST", f"/api/trackers/{row['id']}/test", {})
                except (ctl.SeedboxError, OSError, ValueError):
                    pass
    rows = fetch(api, login)
    data = cache()
    hist = data.setdefault("history", {})
    for row in rows:
        points = hist.setdefault(row["name"], [])
        if row["ratio"] is not None or row["up"] is not None:
            if not points or now - points[-1][0] >= HISTORY_EVERY:
                points.append([now, row["up"] or 0, row["down"] or 0, row["ratio"]])
            hist[row["name"]] = points[-KEEP:]
    data.update(at=now, rows=rows, session=session_health(api))
    _store(data)
    return rows


def trend(name, now=None, days=7):
    """Évolution sur `days` jours : {up, down, ratio_from, ratio_to} ; None sans deux points assez éloignés."""
    now = time.time() if now is None else now
    points = [p for p in (cache().get("history") or {}).get(name, []) if now - p[0] <= days * DAY]
    if len(points) < 2 or points[-1][0] - points[0][0] < DAY:
        return None
    first, last = points[0], points[-1]
    return {"up": max(0, last[1] - first[1]), "down": max(0, last[2] - first[2]), "ratio_from": first[3], "ratio_to": last[3], "days": round((last[0] - first[0]) / DAY, 1)}


# --- rapprochement ------------------------------------------------------------------------------------
def base(host):
    """tracker.c411.org -> c411 ; c411.org -> c411."""
    labels = [x for x in str(host or "").lower().split(".") if x]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")


def match(qbit_host, rows, aliases):
    wanted = aliases.get(qbit_host)
    if wanted:
        return next((r for r in rows if r["name"].lower() == wanted.lower()), None)
    key = base(qbit_host)
    if not key:
        return None
    return (next((r for r in rows if base(r["host"]) == key), None)
            or next((r for r in rows if key in r["name"].lower() or key in r["key"].lower()), None))


def by_host(hosts, rows=None, aliases=None):
    """{hôte qBittorrent: ligne de p2p-aggregator} d'après le cache (sans appel réseau)."""
    rows = cache().get("rows", []) if rows is None else rows
    aliases = settings()["aliases"] if aliases is None else aliases
    out = {}
    for host in hosts:
        row = match(host, rows, aliases)
        if row:
            out[host] = row
    return out


def tick(now=None, api=None, login=None):
    now = time.time() if now is None else now
    data = cache()
    if now - data.get("at", 0) < EVERY:
        return 0
    if not ctl.container_running("p2p-aggregator"):
        return 0
    rows = refresh(api, login, now)
    sent = 0
    if settings()["alert"]:
        data = cache()
        alerted = data.setdefault("alerted", {})
        for row in rows:
            if row["enabled"] and row["danger"] and now - alerted.get(row["name"], 0) >= DAY:
                ctl.alerts().notify(f"Ratio sous le minimum : {row['name']}", f"Ratio {row['ratio']:.2f} pour {row['required']:.2f} exigé : "
                                    "les torrents de ce tracker restent protégés, laissez-les partager.", "warning")
                alerted[row["name"]] = int(now)
                sent += 1
        if sent:
            _store(data)
    return sent


def overview(now=None):
    data = cache()
    rows = data.get("rows", [])
    cfg = settings()
    out = []
    for r in rows:
        out.append({**r, "trend": trend(r["name"], now)})
    return {"settings": cfg, "at": data.get("at", 0), "trackers": out, "available": bool(rows), "session": data.get("session")}
