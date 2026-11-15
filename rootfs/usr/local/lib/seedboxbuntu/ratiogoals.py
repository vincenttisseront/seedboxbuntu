"""SeedBoxBuntu - objectifs de ratio par tracker, avec alerte avant d'être sous le minimum.

Les trackers privés exigent un ratio minimal (envoyé / reçu). Pour chaque tracker connu de qBittorrent, la console
affiche le ratio, la quantité à envoyer pour atteindre l'objectif et l'évolution sur la semaine. Une fois par heure, les
totaux envoyé et reçu de chaque tracker sont enregistrés dans metrics.db (clés « trk.<hôte>.up », « .down »).

Objectif (/etc/seedboxbuntu/ratiogoals.json) : un ratio minimal par tracker, une marge d'alerte (10 % par défaut :
l'alerte part dès que le ratio passe sous objectif × 1,10) et un volume reçu minimal (5 Go) sous lequel aucune alerte
n'est envoyée, car un ratio calculé sur quelques Mo ne veut rien dire.

Limite assumée : le ratio vient des statistiques de qBittorrent pour les torrents qu'il connaît encore. Après un
import, un nettoyage ou une réinstallation, il peut différer de celui que le tracker affiche : c'est celui du tracker qui
fait foi, l'objectif d'ici est une alerte de prudence.
    seedboxctl ratiogoals show | set TRACKER RATIO | unset TRACKER
"""
import json
import os
import re
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "ratiogoals.json"
LAST_FILE = ctl.STATE / "ratio-last.json"
EVERY = 3300
HOST = re.compile(r"^[a-z0-9][a-z0-9.-]{0,98}$")
DEFAULTS = {"goals": {}, "margin": 10, "min_gb": 5}
WEEK = 7 * 86400


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    cfg = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    if not isinstance(cfg["goals"], dict):
        cfg["goals"] = {}
    return cfg


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        if key == "goals":
            if not isinstance(value, dict) or len(value) > 100:
                raise ctl.SeedboxError("objectifs : un ratio par tracker, 100 au plus")
            goals = {}
            for host, ratio in value.items():
                host = str(host).strip().lower()
                if not HOST.match(host):
                    raise ctl.SeedboxError(f"tracker invalide : {host[:40]}")
                if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0.1 <= ratio <= 50:
                    raise ctl.SeedboxError("ratio : un nombre entre 0,1 et 50")
                goals[host] = round(float(ratio), 2)
            cfg[key] = goals
        elif key == "margin":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise ctl.SeedboxError("marge : entier de 0 à 100 %")
            cfg[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100000:
                raise ctl.SeedboxError("volume minimal : nombre de Go entre 0 et 100000")
            cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    temp.write_text(json.dumps(cfg, indent=1))
    os.chmod(temp, 0o644)
    os.replace(temp, SETTINGS_FILE)
    say("objectifs de ratio enregistrés")
    return cfg


def sample(now=None, force=False):
    """Mesures pour metrics.collect : totaux envoyé et reçu par tracker. Vide si une lecture récente existe."""
    now = time.time() if now is None else now
    try:
        last = json.loads(LAST_FILE.read_text()).get("time", 0)
    except (OSError, ValueError, AttributeError):
        last = 0
    if not force and now - last < EVERY:
        return {}
    values = {}
    for item in ctl.tracker_stats():
        host = str(item.get("tracker", "")).lower()
        if not HOST.match(host) or host == "inconnu":
            continue
        values[f"trk.{host}.up"] = item["uploaded"]
        values[f"trk.{host}.down"] = item["downloaded"]
    try:
        LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_FILE.write_text(json.dumps({"time": now}))
    except OSError:
        pass
    return values


def judge(uploaded, downloaded, goal, margin=10, min_gb=5):
    """Situation d'un tracker : status (none, unknown, ok, near, below), ratio, envoi manquant en octets."""
    ratio = round(uploaded / downloaded, 2) if downloaded else None
    if not goal:
        return {"status": "none", "ratio": ratio, "missing": 0}
    if ratio is None or downloaded < min_gb * (1 << 30):
        return {"status": "unknown", "ratio": ratio, "missing": 0}
    missing = max(0, int(goal * downloaded - uploaded))
    if ratio < goal:
        return {"status": "below", "ratio": ratio, "missing": missing}
    if ratio < goal * (1 + margin / 100):
        return {"status": "near", "ratio": ratio, "missing": 0}
    return {"status": "ok", "ratio": ratio, "missing": 0}


def hosts_known():
    found = set()
    for key in ctl.metrics().keys():
        if key.startswith("trk.") and key.endswith((".up", ".down")):
            found.add(key[4:].rsplit(".", 1)[0])
    return found


def totals(host, now=None):
    """(envoyé, reçu, envoyé il y a 7 jours, reçu il y a 7 jours) d'après l'historique."""
    metrics = ctl.metrics()
    up = metrics.points(f"trk.{host}.up", WEEK, now)
    down = metrics.points(f"trk.{host}.down", WEEK, now)
    if not up or not down:
        return None
    return up[-1][1], down[-1][1], up[0][1], down[0][1]


def rows(now=None, live=None):
    cfg = settings()
    live = {str(i["tracker"]).lower(): i for i in (live or [])}
    result = []
    for host in sorted(hosts_known() | set(cfg["goals"]) | set(live)):
        if host in live:
            uploaded, downloaded = live[host]["uploaded"], live[host]["downloaded"]
            torrents, speed = live[host]["torrents"], live[host]["upspeed"]
        else:
            stored = totals(host, now)
            uploaded, downloaded = (stored[0], stored[1]) if stored else (0, 0)
            torrents, speed = 0, 0
        stored = totals(host, now)
        goal = cfg["goals"].get(host)
        verdict = judge(uploaded, downloaded, goal, cfg["margin"], cfg["min_gb"])
        result.append({"tracker": host, "torrents": torrents, "uploaded": uploaded, "downloaded": downloaded,
                       "ratio": verdict["ratio"], "goal": goal, "status": verdict["status"], "missing": verdict["missing"],
                       "week_up": max(0, int(uploaded - stored[2])) if stored else 0,
                       "week_down": max(0, int(downloaded - stored[3])) if stored else 0})
    return result


def overview(now=None):
    try:
        live = ctl.tracker_stats()
    except (ctl.SeedboxError, OSError, ValueError):
        live = []
    return {"settings": settings(), "trackers": rows(now, live), "qbittorrent": bool(ctl.qbit_mode())}


def findings(now=None):
    cfg = settings()
    found = {}
    for item in rows(now):
        if item["status"] not in ("below", "near"):
            continue
        goal = item["goal"]
        if item["status"] == "below":
            found[f"ratio:{item['tracker']}"] = (
                f"Ratio sous l'objectif : {item['tracker']}",
                f"Ratio {item['ratio']} pour un objectif de {goal}. Il manque environ {item['missing'] // (1 << 30)} Go à envoyer "
                f"pour l'atteindre : laissez les torrents de ce tracker en partage.", "warning")
        else:
            found[f"ratio:{item['tracker']}"] = (
                f"Ratio proche du minimum : {item['tracker']}",
                f"Ratio {item['ratio']} pour un objectif de {goal} (alerte à moins de {cfg['margin']} % au-dessus).", "warning")
    return found
