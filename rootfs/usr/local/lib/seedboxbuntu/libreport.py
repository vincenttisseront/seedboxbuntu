"""SeedBoxBuntu - rapport hebdomadaire des bibliothèques.

Les autres modules y inscrivent ce qu'ils ont fait (copies reliées, remplacements lancés, fichiers allégés, titres déplacés,
torrents retirés) dans /var/lib/seedboxbuntu/libevents.json (500 entrées au plus). Chaque semaine, si le rapport est activé, un
résumé en chiffres part sur les canaux d'alerte : ce qui a été fait, l'espace gagné, les prévisions de saturation et les fichiers
douteux. Rien d'autre n'y figure : jamais de titre ni de nom de personne.
Désactivé par défaut. Jour (0 = lundi) et heure réglables ; « Envoyer maintenant » pour essayer.
    seedboxctl libreport show | send | set enabled on|off | set day N | set hour N
"""
import json
import os
import time

import seedboxctl as ctl

FILE = ctl.STATE / "libevents.json"
SETTINGS_FILE = ctl.ETC / "libreport.json"
DEFAULTS = {"enabled": False, "day": 0, "hour": 9, "last": 0}
KEEP = 500
KINDS = {"relink": "copies reliées", "replace": "remplacements lancés", "slim": "fichiers allégés", "move": "titres déplacés",
         "retire": "torrents retirés", "rename": "titres renommés"}
WEEK = 7 * 86400


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


def save(values, internal=False):
    cfg = settings()
    for key, value in values.items():
        if key == "enabled" and isinstance(value, bool):
            cfg[key] = value
        elif key == "day" and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6:
            cfg[key] = value
        elif key == "hour" and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23:
            cfg[key] = value
        elif key == "last" and internal:
            cfg[key] = int(value)
        else:
            raise ctl.SeedboxError(f"réglage invalide : {key} (enabled vrai/faux, day 0 à 6, hour 0 à 23)")
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def record(kind, count=1, size=0, now=None):
    """Inscrit une action ; ne lève jamais d'erreur (le rapport ne doit pas gêner l'action)."""
    if kind not in KINDS or count <= 0:
        return
    try:
        events = _read(FILE, [])
        events.append({"at": int(time.time() if now is None else now), "kind": kind, "count": int(count), "size": max(0, int(size))})
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(events[-KEEP:]))
    except (OSError, ValueError, TypeError):
        pass


def totals(now=None, days=7):
    now = time.time() if now is None else now
    out = {kind: {"count": 0, "size": 0} for kind in KINDS}
    for e in _read(FILE, []):
        if now - e.get("at", 0) <= days * 86400 and e.get("kind") in out:
            out[e["kind"]]["count"] += e["count"]
            out[e["kind"]]["size"] += e["size"]
    return out


def build(now=None):
    """Texte du rapport (chiffres seulement)."""
    now = time.time() if now is None else now
    done = totals(now)
    lines = []
    for kind, label in KINDS.items():
        if done[kind]["count"]:
            size = f", {done[kind]['size'] >> 30} Go" if done[kind]["size"] else ""
            lines.append(f"- {done[kind]['count']} {label}{size}")
    lines = lines or ["- rien de fait cette semaine"]
    try:
        for row in ctl.spacefore().forecast(now):
            if row["level"] == "warn":
                lines.append(f"- {row['volume']} : saturation dans environ {row['days_left']:.0f} jours")
    except Exception:  # noqa: BLE001 - une section du rapport en échec ne l'empêche pas de partir
        pass
    try:
        bad = len(ctl.integrity().problems())
        if bad:
            lines.append(f"- {bad} fichier(s) douteux au contrôle d'intégrité")
    except Exception:  # noqa: BLE001
        pass
    return "Cette semaine :\n" + "\n".join(lines)


def send(now=None):
    now = time.time() if now is None else now
    text = build(now)
    results = ctl.alerts().notify("Rapport hebdomadaire des bibliothèques", text, "ok", force=True)
    save({"last": int(now)}, internal=True)
    return {"text": text, "channels": results}


def tick(now=None):
    cfg = settings()
    if not cfg["enabled"]:
        return False
    now = time.time() if now is None else now
    local = time.localtime(now)
    if now - cfg["last"] < WEEK - 3600 or local.tm_wday != cfg["day"] or local.tm_hour < cfg["hour"]:
        return False
    send(now)
    return True


def overview(now=None):
    return {"settings": settings(), "week": totals(now), "preview": build(now)}
