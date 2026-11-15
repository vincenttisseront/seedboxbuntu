"""SeedBoxBuntu - prévision de saturation des volumes (disque local et partages).

Un relevé de l'espace libre par volume est pris une fois par jour (au plus 400 points gardés dans /var/lib/seedboxbuntu/spacehist.json).
La prévision est une droite des moindres carrés sur les 30 derniers jours : si l'espace libre baisse, la date de saturation est
l'espace libre actuel divisé par la pente. Il faut au moins 3 relevés sur 3 jours pour oser une date ; une pente qui remonte
(ménage récent, suppression) donne « stable ». Une alerte est envoyée (une fois par semaine au plus et par volume) quand la date
tombe dans moins de `warn_days` jours.
Limites assumées : une droite ne voit pas les à-coups (un gros import, un nettoyage) ; un partage réseau dont l'espace libre
varie à cause d'autres machines donne une prévision à prendre comme indicative.
    seedboxctl spacefore show | sample | set warn_days N | alert on|off
"""
import json
import os
import time

import seedboxctl as ctl

FILE = ctl.STATE / "spacehist.json"
SETTINGS_FILE = ctl.ETC / "spacefore.json"
DEFAULTS = {"warn_days": 30, "alert": True}
KEEP = 400
WINDOW_DAYS = 30
EVERY = 20 * 3600
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
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key == "warn_days":
            if isinstance(value, bool) or not isinstance(value, int) or not 3 <= value <= 365:
                raise ctl.SeedboxError("alerte : de 3 à 365 jours")
        elif key == "alert":
            if not isinstance(value, bool):
                raise ctl.SeedboxError("alert : vrai ou faux")
        else:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def history():
    return _read(FILE, {})


def sample(now=None, space=None):
    """Ajoute un point par volume ; renvoie le nombre de volumes relevés."""
    now = int(time.time() if now is None else now)
    space = space or ctl.libscan().volumes_space()
    data = history()
    count = 0
    for volume, (total, free) in space.items():
        if not total or free is None:
            continue
        points = data.setdefault(volume, {"points": [], "alerted": 0})
        points["points"].append([now, int(free), int(total)])
        points["points"] = points["points"][-KEEP:]
        count += 1
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data))
    return count


def fit(points, now):
    """Pente de l'espace libre en octets par jour sur les 30 derniers jours ; None si trop peu de points."""
    recent = [(t, f) for t, f, _total in points if now - t <= WINDOW_DAYS * DAY]
    if len(recent) < 3 or recent[-1][0] - recent[0][0] < 3 * DAY:
        return None
    n = len(recent)
    mean_t = sum(t for t, _ in recent) / n
    mean_f = sum(f for _, f in recent) / n
    denominator = sum((t - mean_t) ** 2 for t, _ in recent)
    if not denominator:
        return None
    return sum((t - mean_t) * (f - mean_f) for t, f in recent) / denominator * DAY


def forecast(now=None):
    now = int(time.time() if now is None else now)
    cfg = settings()
    rows = []
    for volume, entry in sorted(history().items()):
        points = entry.get("points") or []
        if not points:
            continue
        _t, free, total = points[-1]
        slope = fit(points, now)
        row = {"volume": volume, "free": free, "total": total, "points": len(points), "slope_day": None, "days_left": None, "date": None, "level": "unknown"}
        if slope is not None:
            row["slope_day"] = int(slope)
            if slope < -1 << 20:                      # au moins 1 Mo de perte par jour pour parler de tendance
                days = free / -slope
                row.update(days_left=round(days, 1), date=int(now + days * DAY), level="warn" if days < cfg["warn_days"] else "ok")
            else:
                row["level"] = "ok"
        rows.append(row)
    return rows


def tick(now=None, space=None):
    now = time.time() if now is None else now
    data = history()
    last = max((e["points"][-1][0] for e in data.values() if e.get("points")), default=0)
    if now - last >= EVERY:
        sample(now, space)
    cfg = settings()
    if not cfg["alert"]:
        return 0
    data = history()
    sent = 0
    for row in forecast(now):
        entry = data.get(row["volume"], {})
        if row["level"] == "warn" and now - entry.get("alerted", 0) >= 7 * DAY:
            ctl.alerts().notify(f"Saturation prévue : {row['volume']}",
                                f"Au rythme des 30 derniers jours, il restera de la place pendant {row['days_left']:.0f} jours environ "
                                f"({row['free'] >> 30} Go libres).", "warning")
            entry["alerted"] = int(now)
            sent += 1
    if sent:
        FILE.write_text(json.dumps(data))
    return sent


def overview(now=None):
    return {"settings": settings(), "volumes": forecast(now)}
