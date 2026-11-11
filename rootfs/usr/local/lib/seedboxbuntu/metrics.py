"""SeedBoxBuntu - historique des ressources et prévision de saturation des disques.

Un minuteur (seedbox-metrics.timer, toutes les 5 minutes) enregistre dans /var/lib/seedboxbuntu/metrics.db :
processeur, mémoire, charge, débit réseau, nombre de conteneurs en marche et occupation de chaque disque.
Les mesures de plus de 2 jours sont résumées en moyennes horaires (et maximums), conservées 400 jours : un
an d'historique pèse quelques mégaoctets.

Prévision : pour chaque disque, une droite est ajustée sur l'occupation des 14 derniers jours. Elle donne le
rythme de remplissage (Go par jour) et la date à laquelle le disque serait plein. La prévision n'est affichée
comme fiable qu'avec au moins 3 jours d'historique et une tendance nette (r² >= 0,5) ; elle alimente une
alerte « disque plein dans N jours » (alerts.py).

    seedboxctl metrics collect | show [--range 24h] | forecast
"""
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import seedboxctl as ctl

DB_FILE = ctl.STATE / "metrics.db"
LAST_FILE = ctl.STATE / "metrics-last.json"
RAW_KEEP = 2 * 86400
HOURLY_KEEP = 400 * 86400
FORECAST_WINDOW = 14 * 86400
MIN_SPAN = 3 * 86400
MIN_R2 = 0.5
MAX_POINTS = 240
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7j": 7 * 86400, "30j": 30 * 86400, "1an": 365 * 86400}


def connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_FILE, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS raw (ts INTEGER NOT NULL, key TEXT NOT NULL, value REAL NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS raw_key ON raw (key, ts)")
    db.execute("CREATE TABLE IF NOT EXISTS hourly (ts INTEGER NOT NULL, key TEXT NOT NULL, value REAL NOT NULL, peak REAL NOT NULL, "
               "PRIMARY KEY (key, ts))")
    return db


# --- mesures -----------------------------------------------------------------------------------
def cpu_counters():
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(v) for v in fields]
        return {"idle": values[3] + (values[4] if len(values) > 4 else 0), "total": sum(values)}
    except (OSError, ValueError, IndexError):
        return {}


def read_sources():
    """Mesures instantanées : {clé : valeur}. Compteurs cumulés (processeur, réseau) : transformés en taux d'après la mesure précédente."""
    system = ctl.sysinfo()
    now = time.time()
    last = {}
    try:
        last = json.loads(LAST_FILE.read_text())
    except (OSError, ValueError):
        pass
    values, counters = {}, {"time": now}
    cpu = cpu_counters()
    if cpu:
        counters["cpu"] = cpu
        before = last.get("cpu")
        if before and cpu["total"] > before["total"]:
            busy = 1 - (cpu["idle"] - before["idle"]) / (cpu["total"] - before["total"])
            values["cpu"] = round(max(0.0, min(100.0, busy * 100)), 1)
    memory = system.meminfo()
    if memory.get("total"):
        values["mem"] = round(100 * memory["used"] / memory["total"], 1)
        if memory.get("swap_total"):
            values["swap"] = round(100 * memory["swap_used"] / memory["swap_total"], 1)
    if hasattr(os, "getloadavg"):
        values["load"] = round(os.getloadavg()[0], 2)
    net = system.network()
    counters["net"] = {"rx": net["rx"], "tx": net["tx"]}
    before = last.get("net")
    elapsed = now - last.get("time", now)
    if before and 0 < elapsed < 3600 and net["rx"] >= before["rx"] and net["tx"] >= before["tx"]:
        values["net_rx"] = round((net["rx"] - before["rx"]) / elapsed)
        values["net_tx"] = round((net["tx"] - before["tx"]) / elapsed)
    for disk in system.disks():
        values[f"disk:{disk['path']}"] = disk["used"]
        values[f"disktotal:{disk['path']}"] = disk["total"]
    try:
        values["containers"] = sum(1 for state in ctl.containers().values() if state.get("state") == "running")
    except Exception:                                         # noqa: BLE001 - Docker arrêté : pas de mesure, rien d'autre
        pass
    try:
        LAST_FILE.write_text(json.dumps(counters))
    except OSError:
        pass
    return values


def collect(values=None, now=None):
    """Enregistre une mesure, puis résume les anciennes. Renvoie le nombre de valeurs enregistrées."""
    now = int(now if now is not None else time.time())
    values = read_sources() if values is None else values
    with connect() as db:
        db.executemany("INSERT INTO raw (ts, key, value) VALUES (?, ?, ?)", [(now, k, float(v)) for k, v in values.items()])
        compact(db, now)
    return len(values)


def compact(db, now):
    """Mesures de plus de RAW_KEEP -> moyennes horaires ; historique horaire au-delà de HOURLY_KEEP supprimé."""
    limit = now - RAW_KEEP
    limit -= limit % 3600                                     # heures entières seulement
    rows = db.execute("SELECT (ts / 3600) * 3600 AS hour, key, AVG(value), MAX(value) FROM raw WHERE ts < ? GROUP BY hour, key",
                      (limit,)).fetchall()
    if rows:
        db.executemany("INSERT OR REPLACE INTO hourly (ts, key, value, peak) VALUES (?, ?, ?, ?)", [(h, k, a, m) for h, k, a, m in rows])
        db.execute("DELETE FROM raw WHERE ts < ?", (limit,))
    db.execute("DELETE FROM hourly WHERE ts < ?", (now - HOURLY_KEEP,))


# --- lecture -----------------------------------------------------------------------------------
def points(key, seconds, now=None):
    """[[ts, valeur], ...] sur la période, au plus MAX_POINTS points (moyennes par tranche)."""
    now = int(now if now is not None else time.time())
    start = now - seconds
    with connect() as db:
        rows = db.execute("SELECT ts, value FROM hourly WHERE key = ? AND ts >= ? AND ts < ? ORDER BY ts",
                          (key, start - 3600, now - RAW_KEEP + 3600)).fetchall() if seconds > RAW_KEEP else []
        rows += db.execute("SELECT ts, value FROM raw WHERE key = ? AND ts >= ? ORDER BY ts", (key, start)).fetchall()
    rows = [(t, v) for t, v in rows if t >= start]
    if len(rows) <= MAX_POINTS:
        return [[t, round(v, 2)] for t, v in rows]
    bucket = seconds / MAX_POINTS
    grouped = {}
    for t, v in rows:
        grouped.setdefault(int((t - start) // bucket), []).append((t, v))
    return [[round(sum(t for t, _ in g) / len(g)), round(sum(v for _, v in g) / len(g), 2)] for _, g in sorted(grouped.items())]


def keys():
    with connect() as db:
        return sorted({r[0] for r in db.execute("SELECT DISTINCT key FROM raw")} | {r[0] for r in db.execute("SELECT DISTINCT key FROM hourly")})


def history(range_name="24h", now=None):
    if range_name not in RANGES:
        raise ctl.SeedboxError("période inconnue : " + ", ".join(RANGES))
    seconds = RANGES[range_name]
    series = {}
    for key in keys():
        if key.startswith("disktotal:"):
            continue
        series[key] = points(key, seconds, now)
    totals = {}
    with connect() as db:
        for key in [k for k in keys() if k.startswith("disktotal:")]:
            row = db.execute("SELECT value FROM raw WHERE key = ? ORDER BY ts DESC LIMIT 1", (key,)).fetchone() or \
                db.execute("SELECT value FROM hourly WHERE key = ? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
            if row:
                totals[key.split(":", 1)[1]] = row[0]
    return {"range": range_name, "ranges": list(RANGES), "series": series, "totals": totals, "now": int(now if now is not None else time.time()),
            "forecast": forecast(now=now)}


# --- prévision ---------------------------------------------------------------------------------
def fit(samples):
    """Régression linéaire (moindres carrés) : (pente par seconde, r²)."""
    n = len(samples)
    mean_t = sum(t for t, _ in samples) / n
    mean_v = sum(v for _, v in samples) / n
    sxx = sum((t - mean_t) ** 2 for t, _ in samples)
    sxy = sum((t - mean_t) * (v - mean_v) for t, v in samples)
    syy = sum((v - mean_v) ** 2 for _, v in samples)
    if sxx == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return slope, r2


def forecast_for(path, now=None):
    now = int(now if now is not None else time.time())
    series = [(t, v) for t, v in points(f"disk:{path}", FORECAST_WINDOW, now)]
    with connect() as db:
        row = db.execute("SELECT value FROM raw WHERE key = ? ORDER BY ts DESC LIMIT 1", (f"disktotal:{path}",)).fetchone()
    total = row[0] if row else 0
    result = {"path": path, "total": int(total), "used": int(series[-1][1]) if series else 0, "per_day": 0, "days": None,
              "full_on": "", "reliable": False, "r2": 0.0, "span_days": 0.0}
    if len(series) < 12 or not total:
        return result
    span = series[-1][0] - series[0][0]
    slope, r2 = fit(series)
    result.update(per_day=int(slope * 86400), r2=round(r2, 2), span_days=round(span / 86400, 1),
                  reliable=span >= MIN_SPAN and r2 >= MIN_R2)
    if slope > 0:
        days = max(0.0, (total - series[-1][1]) / slope / 86400)
        result["days"] = round(days, 1)
        if days < 20 * 365:
            result["full_on"] = time.strftime("%Y-%m-%d", time.localtime(now + days * 86400))
    return result


def forecast(now=None):
    with connect() as db:
        paths = [r[0].split(":", 1)[1] for r in db.execute("SELECT DISTINCT key FROM raw WHERE key LIKE 'disktotal:%'")]
    return [forecast_for(path, now) for path in sorted(paths)]


def risky(limit_days, now=None):
    """Disques dont la prévision fiable annonce la saturation dans moins de limit_days jours."""
    if not limit_days:
        return []
    return [f for f in forecast(now) if f["reliable"] and f["days"] is not None and f["days"] < limit_days]
