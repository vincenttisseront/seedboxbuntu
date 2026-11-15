"""SeedBoxBuntu - ressources de chaque conteneur : historique et dérive.

Le minuteur de mesures (seedbox-metrics.timer, 5 minutes) enregistre, en plus des mesures de la machine, le processeur
et la mémoire de chaque conteneur dans metrics.db (clés « ctr.cpu:nom » et « ctr.mem:nom »). Cette page lit ces séries
et repère trois dérives, qui alimentent une alerte :
  - mémoire : plus de 90 % de la limite du conteneur pendant 3 mesures de suite ;
  - fuite probable : la mémoire dépasse 512 Mo et vaut plus de 2 fois sa médiane des 24 dernières heures ;
  - processeur : plus de 90 % de la machine pendant 6 mesures de suite (30 minutes). Jellyfin (transcodage) est ignoré.
Seuils et liste d'exceptions : /etc/seedboxbuntu/resources.json.
    seedboxctl resources show | set
"""
import json
import os
import statistics
import time

import seedboxctl as ctl
import metrics

SETTINGS_FILE = ctl.ETC / "resources.json"
DEFAULTS = {"enabled": True, "memory_percent": 90, "memory_samples": 3, "leak_mb": 512, "leak_factor": 2.0,
            "cpu_percent": 90, "cpu_samples": 6, "ignore_cpu": ["jellyfin"]}
DAY = 86400


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
        if key == "enabled":
            cfg[key] = bool(value)
        elif key == "ignore_cpu":
            if not isinstance(value, list) or len(value) > 30 or not all(isinstance(v, str) and v.replace("-", "").replace("_", "").isalnum() for v in value):
                raise ctl.SeedboxError("ignore_cpu : liste de noms de conteneurs")
            cfg[key] = value
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ctl.SeedboxError(f"{key} : nombre attendu")
            if not 1 <= number <= 100000:
                raise ctl.SeedboxError(f"{key} : valeur hors limites")
            cfg[key] = int(number) if key != "leak_factor" else number
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def sample(rows=None, cores=None):
    """Mesures instantanées pour metrics.collect : {clé: valeur}. Une panne de Docker ne produit aucune mesure."""
    try:
        rows = ctl.sysinfo().containers() if rows is None else rows
    except Exception:  # noqa: BLE001
        return {}
    cores = cores or os.cpu_count() or 1
    values = {}
    for row in rows:
        name = row["name"]
        values[f"ctr.cpu:{name}"] = round(row["cpu"] / cores, 1)               # part de la machine entière (docker : 100 % = un cœur)
        values[f"ctr.mem:{name}"] = row["memory"]
        values[f"ctr.memp:{name}"] = row["memory_percent"]
    return values


def _names():
    return sorted({key.split(":", 1)[1] for key in metrics.keys() if key.startswith("ctr.mem:")})


def series(name, seconds=DAY, now=None):
    return {"cpu": metrics.points(f"ctr.cpu:{name}", seconds, now), "mem": metrics.points(f"ctr.mem:{name}", seconds, now),
            "memp": metrics.points(f"ctr.memp:{name}", seconds, now)}


def _values(points):
    return [v for _, v in points]


def drift(name, data=None, cfg=None, now=None):
    """Liste des dérives du conteneur : [(code, détail)]."""
    cfg = cfg or settings()
    data = data or series(name, DAY, now)
    out = []
    memp = _values(data["memp"])[-cfg["memory_samples"]:]
    if len(memp) >= cfg["memory_samples"] and all(v >= cfg["memory_percent"] for v in memp):
        out.append(("memory", f"mémoire à {memp[-1]:.0f} % de sa limite"))
    mem = _values(data["mem"])
    if len(mem) >= 24:
        recent = statistics.median(mem[-3:])
        base = statistics.median(mem[:-3])
        if recent > cfg["leak_mb"] * 1024 * 1024 and base > 0 and recent > cfg["leak_factor"] * base:
            out.append(("leak", f"mémoire {recent / 2 ** 20:.0f} Mo, {recent / base:.1f} fois sa valeur habituelle"))
    cpu = _values(data["cpu"])[-cfg["cpu_samples"]:]
    if name not in cfg["ignore_cpu"] and len(cpu) >= cfg["cpu_samples"] and all(v >= cfg["cpu_percent"] for v in cpu):
        out.append(("cpu", f"processeur à {cpu[-1]:.0f} % de la machine depuis {cfg['cpu_samples'] * 5} minutes"))
    return out


def overview(now=None):
    cfg = settings()
    rows = []
    for name in _names():
        data = series(name, DAY, now)
        cpu, mem = _values(data["cpu"]), _values(data["mem"])
        if not mem:
            continue
        problems = drift(name, data, cfg, now)
        rows.append({"name": name, "cpu": cpu[-1] if cpu else 0, "cpu_max": max(cpu) if cpu else 0,
                     "mem": mem[-1], "mem_max": max(mem), "mem_avg": int(sum(mem) / len(mem)), "samples": len(mem),
                     "spark": [round(v) for v in mem[-48:]], "problems": [{"code": c, "detail": d} for c, d in problems]})
    rows.sort(key=lambda r: (-len(r["problems"]), -r["mem"]))
    return {"settings": cfg, "containers": rows, "now": int(now if now is not None else time.time())}


def findings(cfg=None, now=None):
    """Pour alerts.findings : {clé: (titre, détail, niveau)}."""
    cfg = cfg or settings()
    if not cfg["enabled"]:
        return {}
    found = {}
    for name in _names():
        for code, detail in drift(name, None, cfg, now):
            title = {"memory": f"{name} : mémoire presque saturée", "leak": f"{name} : mémoire en forte hausse",
                     "cpu": f"{name} : processeur saturé"}[code]
            found[f"ressource:{code}:{name}"] = (title, detail.capitalize() + ".", "warning")
    return found
