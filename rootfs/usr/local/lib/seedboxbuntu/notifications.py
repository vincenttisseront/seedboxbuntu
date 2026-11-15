"""SeedBoxBuntu - centre de notifications et préférences d'interface par compte.

- Journal d'événements (alertes déclenchées ou rétablies, tâches terminées) : /var/lib/seedboxbuntu/events.json,
  les 200 derniers, sans secret ni détail sensible.
- Préférences d'interface de chaque compte (langue, tuiles de la vue d'ensemble, notifications lues, assistant) :
  /var/lib/seedboxbuntu/ui-prefs.json. Elles ne donnent aucun droit : un observateur peut régler les siennes.
Le fil des notifications mélange les alertes actives et les derniers événements ; « lu » se mesure par compte.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

import seedboxctl as ctl

EVENTS_FILE = ctl.STATE / "events.json"
PREFS_FILE = ctl.STATE / "ui-prefs.json"
MAX_EVENTS = 200
LEVELS = ("ok", "info", "warning", "critical")
LANGS = ("fr", "en")
TILE_ID = re.compile(r"^[a-z][a-z-]{1,24}$")
MAX_USERS = 100
LOCK = threading.Lock()


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False))
    os.chmod(temp, 0o644)
    os.replace(temp, path)


# ----------------------------------------------------------------------------
#  Événements
# ----------------------------------------------------------------------------
def record(kind, title, detail="", level="info", view=""):
    """Ajoute un événement ; ne lève jamais : une panne du journal ne doit rien casser."""
    try:
        level = level if level in LEVELS else "info"
        with LOCK:
            events = _read(EVENTS_FILE, [])
            now = time.time()
            if events and events[-1].get("title") == title and events[-1].get("level") == level and now - events[-1]["t"] < 60:
                return
            events.append({"t": round(now, 1), "kind": kind, "title": str(title)[:160], "detail": str(detail)[:300],
                           "level": level, "view": view if re.fullmatch(r"[a-z]{2,16}", view or "") else ""})
            _write(EVENTS_FILE, events[-MAX_EVENTS:])
    except Exception:  # noqa: BLE001
        pass


def recent(limit=50):
    return list(reversed(_read(EVENTS_FILE, [])[-limit:]))


def feed(limit=50, now=None):
    """Fil des notifications : alertes en cours (toujours en tête), puis événements récents du plus récent au plus ancien."""
    now = time.time() if now is None else now
    active, items = [], []
    state = _read(ctl.STATE / "alerts-state.json", {})
    for key, entry in sorted(state.items()):
        if not entry.get("notified") and entry.get("seen", 0) < 2:
            continue                                                  # pas encore confirmée
        active.append({"id": f"a-{key}", "t": entry.get("since") or entry.get("notified") or now, "level": entry.get("level", "warning"),
                      "title": entry.get("title", key), "detail": entry.get("detail", ""), "active": True, "view": "alerts"})
    for event in recent(limit):
        items.append({"id": f"e-{event['t']}", "t": event["t"], "level": event.get("level", "info"), "title": event["title"],
                      "detail": event.get("detail", ""), "active": False, "view": event.get("view", "")})
    items.sort(key=lambda i: -i["t"])
    active.sort(key=lambda i: -i["t"])
    return {"items": active + items[:max(0, limit - len(active))], "now": now}   # les alertes en cours passent toujours en tête


# ----------------------------------------------------------------------------
#  Préférences par compte
# ----------------------------------------------------------------------------
def clean(data):
    """Valide et normalise les préférences reçues ; toute clé inconnue est refusée."""
    if not isinstance(data, dict):
        raise ctl.SeedboxError("préférences invalides")
    out = {}
    for key, value in data.items():
        if key == "lang":
            if value not in LANGS:
                raise ctl.SeedboxError("langue inconnue : " + ", ".join(LANGS))
            out[key] = value
        elif key == "overview":
            if not isinstance(value, list) or len(value) > 16:
                raise ctl.SeedboxError("disposition invalide")
            tiles = []
            for tile in value:
                if not isinstance(tile, dict) or not TILE_ID.match(str(tile.get("id", ""))):
                    raise ctl.SeedboxError("tuile invalide")
                tiles.append({"id": tile["id"], "hidden": bool(tile.get("hidden"))})
            out[key] = tiles
        elif key == "notif_seen":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ctl.SeedboxError("date de lecture invalide")
            out[key] = float(value)
        elif key == "setup_dismissed":
            out[key] = bool(value)
        else:
            raise ctl.SeedboxError(f"préférence inconnue : {key}")
    return out


def prefs_get(user):
    return _read(PREFS_FILE, {}).get(user, {}) if user else {}


def prefs_set(user, data):
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,30}", user or ""):
        raise ctl.SeedboxError("compte inconnu")
    values = clean(data)
    with LOCK:
        everything = _read(PREFS_FILE, {})
        if user not in everything and len(everything) >= MAX_USERS:
            raise ctl.SeedboxError("trop de comptes enregistrés")
        everything.setdefault(user, {}).update(values)
        _write(PREFS_FILE, everything)
        return everything[user]
