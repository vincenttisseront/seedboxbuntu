"""Historique des ressources : enregistrement, résumé horaire, séries, prévision de saturation et alerte (sans Docker)."""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbmet"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import metrics as mt  # noqa: E402
import alerts  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


GIB = 1 << 30
NOW = 1_800_000_000
DISK = "/mnt/storage"

# --- 1. mesures réelles (cette machine) : plausibles ----------------------------------------------------------
for folder in (ctl.STATE, ctl.BASE, ctl.STORAGE):
    folder.mkdir(parents=True, exist_ok=True)
ctl.containers = lambda: {"a": {"state": "running"}, "b": {"state": "exited"}}
first = mt.read_sources()
time.sleep(0.3)
second = mt.read_sources()
check("mesure : mémoire en pourcentage", 0 < second.get("mem", 0) <= 100, second.get("mem"))
check("mesure : processeur calculé à partir de la mesure précédente", "cpu" not in first and 0 <= second.get("cpu", -1) <= 100, (first.get("cpu"), second.get("cpu")))
check("mesure : débit réseau calculé", "net_rx" in second and second["net_rx"] >= 0, second.get("net_rx"))
check("mesure : conteneurs en marche", second.get("containers") == 1)
check("mesure : disques (utilisé et total)", any(k.startswith("disk:") for k in second) and any(k.startswith("disktotal:") for k in second))

# --- 2. enregistrement, résumé horaire ---------------------------------------------------------------------------
db_path = mt.DB_FILE
db_path.unlink(missing_ok=True)
# 20 jours d'historique, un point toutes les heures : le disque se remplit de 10 Go par jour (utilisé 500 Go sur 1 To)
start = NOW - 20 * 86400
for i in range(20 * 24 + 1):
    ts = start + i * 3600
    used = 300 * GIB + (ts - start) / 86400 * 10 * GIB
    mt.collect({f"disk:{DISK}": used, f"disktotal:{DISK}": 1000 * GIB, "mem": 40 + (i % 5), "cpu": 10}, now=ts)
with mt.connect() as db:
    raw = db.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    hourly = db.execute("SELECT COUNT(*) FROM hourly").fetchone()[0]
    oldest_raw = db.execute("SELECT MIN(ts) FROM raw").fetchone()[0]
check("résumé : les mesures de plus de 2 jours sont passées en moyennes horaires", hourly > 0 and oldest_raw >= NOW - mt.RAW_KEEP - 3600, (raw, hourly))
check("résumé : la base reste petite", raw < 4 * 24 * 3 * 4 and db_path.stat().st_size < 2_000_000, (raw, db_path.stat().st_size))

# --- 3. séries ------------------------------------------------------------------------------------------------------
for name, seconds in (("6h", 6 * 3600), ("7j", 7 * 86400), ("1an", 365 * 86400)):
    series = mt.points("mem", seconds, NOW)
    check(f"série {name} : points ordonnés, au plus {mt.MAX_POINTS}", 0 < len(series) <= mt.MAX_POINTS and series == sorted(series), len(series))
history = mt.history("7j", NOW)
check("historique : séries, totaux, prévision", "mem" in history["series"] and "disktotal:" + DISK not in history["series"]
      and history["totals"][DISK] == 1000 * GIB and history["forecast"], list(history["series"]))
try:
    mt.history("3 siècles")
    check("période inconnue refusée", False)
except ctl.SeedboxError:
    check("période inconnue refusée", True)

# --- 4. prévision ------------------------------------------------------------------------------------------------------
f = mt.forecast_for(DISK, NOW)
used_now = 300 + 200
check("prévision : rythme de 10 Go par jour", abs(f["per_day"] / GIB - 10) < 0.5, f["per_day"] / GIB)
check("prévision : plein dans environ 50 jours (500 Go libres)", abs(f["days"] - 50) < 3 and f["reliable"] and f["r2"] > 0.99, f)
check("prévision : date de saturation", f["full_on"] > time.strftime("%Y-%m-%d", time.localtime(NOW + 40 * 86400)), f["full_on"])
check("prévision : filtrage par seuil de jours", mt.risky(30, NOW) == [] and len(mt.risky(60, NOW)) == 1 and mt.risky(0, NOW) == [])

# disque stable : pas de prévision de saturation
for i in range(20 * 24):
    mt.collect({f"disk:/stable": 400 * GIB + (i % 3) * 1024, "disktotal:/stable": 1000 * GIB}, now=start + i * 3600)
stable = mt.forecast_for("/stable", NOW)
check("disque stable : prévision non fiable ou très lointaine", not stable["reliable"] or (stable["days"] or 9999) > 365, stable)
# peu d'historique : pas fiable
for i in range(30):
    mt.collect({"disk:/neuf": 100 * GIB + i * 5 * GIB / 24, "disktotal:/neuf": 200 * GIB}, now=NOW - 3 * 3600 + i * 360)
young = mt.forecast_for("/neuf", NOW)
check("peu d'historique : prévision signalée non fiable", young["reliable"] is False and young["span_days"] < 1, young)
# disque qui se vide
for i in range(20 * 24):
    mt.collect({"disk:/vide": 900 * GIB - i * GIB, "disktotal:/vide": 1000 * GIB}, now=start + i * 3600)
emptying = mt.forecast_for("/vide", NOW)
check("disque qui se vide : pas de date de saturation", emptying["days"] is None and emptying["full_on"] == "", emptying)

# --- 5. alerte de prévision ------------------------------------------------------------------------------------------------
real_time = time.time
mt_forecast_now = mt.forecast
mt.risky = lambda limit, now=None: [x for x in mt_forecast_now(NOW) if x["reliable"] and x["days"] is not None and x["days"] < limit]
ctl.health = lambda: type("H", (), {"expected_services": staticmethod(lambda env: [])})()
ctl.env_read = lambda: {}
alerts.disk_paths = lambda: []
ctl.steps_load = lambda: {}
ctl.containers = lambda: {}
found = alerts.findings(alerts.settings())
check("alerte : désactivée sous le seuil", not any(k.startswith("prevision:") for k in found), list(found))
cfg = alerts.settings()
cfg["forecast_days"] = 60
found = alerts.findings(cfg)
item = found.get(f"prevision:{DISK}")
check("alerte : disque plein dans moins de 60 jours", item and "Disque plein dans environ" in item[0] and item[2] == "warning", item)
check("alerte : le seuil est un réglage validé", alerts.validate({"forecast_days": 500})["forecast_days"] == 90 and alerts.validate({"forecast_days": 0})["forecast_days"] == 0)

print("échecs :", bad)
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if bad else 0)
