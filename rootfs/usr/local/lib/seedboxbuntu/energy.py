"""SeedBoxBuntu - énergie des disques : mise en veille et coût par To.

Chaque volume (disque local, partage) est relié au disque physique qui le porte (via le numéro de périphérique du dossier, puis
/sys/dev/block). Un partage réseau (NFS, SMB) n'a pas de disque local : il est signalé « non mesurable ».
  - Mesure : une fois par heure, les compteurs de /proc/diskstats donnent, par disque, la part de temps occupé et les octets lus.
    Un intervalle est « au repos » si le disque a été occupé moins de 1 % du temps. Les 7 derniers jours sont conservés.
  - Mise en veille : `hdparm -S` règle le délai d'arrêt du moteur d'un disque à plateaux (5 à 330 minutes ; 0 = jamais). Refusé pour
    le disque du système, les SSD et tout ce qui n'est pas un disque à plateaux. hdparm ne survit pas à un redémarrage : le réglage est
    réappliqué au premier passage après chaque démarrage (identifiant de démarrage du noyau).
  - Coût : puissance moyenne estimée × durée × prix du kWh ; ramené au To (de capacité et de données) et à l'année. Les watts sont
    des réglages (6 W par disque à plateaux, 2 W par SSD, 0,8 W en veille, 0,25 €/kWh par défaut), pas des mesures : mesurez avec
    une prise connectée pour un chiffre fiable. Un disque en veille compte pour 0,8 W pendant ses heures de repos.
Limites assumées : un disque derrière un RAID matériel, une machine virtuelle ou un adaptateur USB qui ignore ATA SET FEATURES ne
se met pas en veille ; la veille allonge le premier accès (quelques secondes) et use un peu plus les moteurs si elle bat trop
souvent : mettre un délai d'au moins 30 minutes pour un disque que Jellyfin ou les téléchargements réveillent souvent.
    seedboxctl energy show | sample | standby DISQUE MINUTES | set watts_hdd|watts_ssd|watts_standby|price_kwh X | tick
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import seedboxctl as ctl

FILE = ctl.STATE / "energy.json"
SETTINGS_FILE = ctl.ETC / "energy.json"
DEFAULTS = {"watts_hdd": 6.0, "watts_ssd": 2.0, "watts_standby": 0.8, "price_kwh": 0.25, "standby": {}}
FLOATS = {"watts_hdd": (0.1, 50), "watts_ssd": (0.1, 30), "watts_standby": (0.0, 10), "price_kwh": (0.0, 5)}
DEVICE = re.compile(r"^(sd[a-z]{1,2}|vd[a-z]|nvme\d+n\d+|hd[a-z])$")
KEEP_HOURS = 24 * 7
SYS = Path(os.environ.get("SB_SYS", "/sys"))
PROC = Path(os.environ.get("SB_PROC", "/proc"))
HOURS_YEAR = 8760
TB = 10 ** 12


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
    cfg["standby"] = dict(cfg["standby"])
    return cfg


def _write(cfg):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in FLOATS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        low, high = FLOATS[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ctl.SeedboxError(f"{key} : nombre de {low} à {high}")
        cfg[key] = float(value)
    return _write(cfg)


# --- volumes -> disques -------------------------------------------------------------------------
def device_of(path, sys_root=None):
    """Nom du disque physique (sda, nvme0n1...) qui porte `path` ; None pour un partage réseau ou si illisible."""
    sys_root = Path(sys_root or SYS)
    try:
        st = os.stat(path)
    except OSError:
        return None
    major, minor = os.major(st.st_dev), os.minor(st.st_dev)
    if major == 0:
        return None
    node = sys_root / "dev/block" / f"{major}:{minor}"
    try:
        real = node.resolve()
    except OSError:
        return None
    if (real / "partition").exists():
        real = real.parent
    name = real.name
    return name if DEVICE.match(name) else None


def rotational(name, sys_root=None):
    try:
        return (Path(sys_root or SYS) / "block" / name / "queue/rotational").read_text().strip() == "1"
    except OSError:
        return False


def volume_paths():
    paths = {"local": ctl.STORAGE}
    try:
        for name in ctl.shares().load().get("shares") or {}:
            paths[name] = ctl.libscan().shares_root() / name
    except Exception:  # noqa: BLE001
        pass
    return paths


def system_device():
    return device_of("/")


# --- mesure -----------------------------------------------------------------------------------------
def read_counters(proc_root=None):
    """{disque: (secteurs lus, millisecondes occupé)} depuis /proc/diskstats."""
    out = {}
    try:
        for line in (Path(proc_root or PROC) / "diskstats").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 14 and DEVICE.match(parts[2]):
                out[parts[2]] = (int(parts[5]), int(parts[12]))
    except (OSError, ValueError):
        pass
    return out


def sample(now=None, counters=None):
    now = int(time.time() if now is None else now)
    counters = counters if counters is not None else read_counters()
    data = _read(FILE, {})
    series = data.setdefault("series", {})
    for name, (sectors, busy) in counters.items():
        points = series.setdefault(name, [])
        points.append([now, sectors, busy])
        series[name] = points[-(KEEP_HOURS + 1):]
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data))
    return len(counters)


def activity(points):
    """(part de temps au repos 0..1, octets lus) sur les points conservés ; None sans deux relevés."""
    idle = total = 0
    read = 0
    for (t0, s0, b0), (t1, s1, b1) in zip(points, points[1:]):
        span = (t1 - t0) * 1000
        if span <= 0 or s1 < s0 or b1 < b0:                  # redémarrage : compteurs remis à zéro
            continue
        total += 1
        idle += (b1 - b0) < span * 0.01
        read += (s1 - s0) * 512
    return (idle / total if total else None), read


# --- coût -------------------------------------------------------------------------------------------
def disk_power(name, idle_fraction, cfg, standby_minutes):
    """Puissance moyenne estimée en watts."""
    base = cfg["watts_hdd"] if rotational(name) else cfg["watts_ssd"]
    if standby_minutes and idle_fraction is not None and rotational(name):
        return base * (1 - idle_fraction) + cfg["watts_standby"] * idle_fraction
    return base


def report(now=None, spaces=None, paths=None):
    cfg = settings()
    data = _read(FILE, {})
    paths = paths if paths is not None else volume_paths()
    spaces = spaces if spaces is not None else ctl.libscan().volumes_space()
    rows, total_read = [], 0
    per_device = {}
    for volume, path in paths.items():
        name = device_of(path)
        total, free = spaces.get(volume, (None, None))
        idle, read = activity((data.get("series") or {}).get(name, [])) if name else (None, 0)
        per_device.setdefault(name, 0)
        rows.append({"volume": volume, "device": name, "rotational": bool(name and rotational(name)), "total": total, "free": free,
                     "used": (total - free) if total and free is not None else None, "idle": idle, "read": read,
                     "standby": cfg["standby"].get(name, 0) if name else 0})
    seen = set()
    for row in rows:
        total_read += row["read"] if row["device"] not in seen and row["device"] else 0
        seen.add(row["device"])
    for row in rows:
        row["read_share"] = (row["read"] / total_read) if total_read and row["device"] else None
        if row["device"]:
            watts = disk_power(row["device"], row["idle"], cfg, row["standby"])
            kwh = watts * HOURS_YEAR / 1000
            row.update(watts=round(watts, 2), kwh_year=round(kwh, 1), eur_year=round(kwh * cfg["price_kwh"], 2),
                       eur_tb_year=round(kwh * cfg["price_kwh"] / (row["total"] / TB), 2) if row["total"] else None,
                       eur_tb_used_year=round(kwh * cfg["price_kwh"] / (row["used"] / TB), 2) if row["used"] else None)
        else:
            row.update(watts=None, kwh_year=None, eur_year=None, eur_tb_year=None, eur_tb_used_year=None)
    return rows


# --- veille -----------------------------------------------------------------------------------------
def hdparm_value(minutes):
    """Valeur de `hdparm -S` pour un délai en minutes : 1-240 = 5 s par pas, 241-251 = 30 min par pas."""
    if minutes == 0:
        return 0
    if minutes <= 20:
        return max(1, round(minutes * 60 / 5))
    return 240 + max(1, min(11, round(minutes / 30)))


def set_standby(name, minutes, runner=None, apply=True):
    if not isinstance(name, str) or not DEVICE.match(name):
        raise ctl.SeedboxError("disque invalide")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not (minutes == 0 or 5 <= minutes <= 330):
        raise ctl.SeedboxError("délai : 0 (jamais) ou de 5 à 330 minutes")
    known = {device_of(p) for p in volume_paths().values()}
    if name not in known:
        raise ctl.SeedboxError("ce disque ne porte aucun volume de la seedbox")
    if name == system_device():
        raise ctl.SeedboxError("le disque du système n'est jamais mis en veille")
    if not rotational(name):
        raise ctl.SeedboxError("seuls les disques à plateaux se mettent en veille")
    if apply:
        result = (runner or subprocess.run)(["hdparm", "-S", str(hdparm_value(minutes)), f"/dev/{name}"], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise ctl.SeedboxError("hdparm a refusé : " + ((result.stderr or result.stdout or "").strip()[-160:] or "erreur"))
    cfg = settings()
    if minutes:
        cfg["standby"][name] = minutes
    else:
        cfg["standby"].pop(name, None)
    _write(cfg)
    return {"device": name, "minutes": minutes}


def boot_id():
    try:
        return (PROC / "sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def reapply(runner=None):
    """Réapplique les délais de veille après un redémarrage."""
    data = _read(FILE, {})
    current = boot_id()
    if not current or data.get("boot") == current:
        return 0
    count = 0
    for name, minutes in settings()["standby"].items():
        try:
            set_standby(name, minutes, runner)
            count += 1
        except ctl.SeedboxError as error:
            say(f"veille {name} : {error}")
    data["boot"] = current
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data))
    return count


def tick(now=None, counters=None, runner=None):
    now = time.time() if now is None else now
    data = _read(FILE, {})
    last = max((p[-1][0] for p in (data.get("series") or {}).values() if p), default=0)
    if now - last >= 3300:
        sample(now, counters)
    reapply(runner)


def overview(now=None):
    cfg = settings()
    return {"settings": cfg, "volumes": report(now), "system_device": system_device(), "hdparm": bool(shutil_which("hdparm"))}


def shutil_which(name):
    import shutil
    return shutil.which(name)
