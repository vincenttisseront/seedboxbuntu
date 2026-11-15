"""SeedBoxBuntu - débit de qBittorrent par plages horaires.

Exemple : 2 000 ko/s en montée la journée (pour laisser de la place à la connexion de la maison), illimité la nuit.
Réglages (/etc/seedboxbuntu/bandwidth.json) : un débit par défaut (hors plage) et jusqu'à 8 plages, chacune avec des jours
(0 = lundi), une heure de début et de fin (la fin peut passer minuit : 22:00 -> 06:00), un débit descendant et montant
en ko/s (0 = illimité). La première plage qui correspond à l'heure s'applique.

Le passage de surveillance (5 minutes) règle les limites GLOBALES de qBittorrent (dl_limit et up_limit) quand la plage
change, et seulement alors : un réglage fait à la main dans qBittorrent entre deux changements de plage est conservé.
À la désactivation, les limites d'origine sont rétablies. Il s'agit d'une limite posée dans qBittorrent, pas d'un
contrôle du réseau : Jellyfin, Radarr et les autres applications ne sont pas freinés.
    seedboxctl bandwidth show | tick
"""
import json
import os
import re
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "bandwidth.json"
STATE_FILE = ctl.STATE / "bandwidth-state.json"
MAX_SLOTS = 8
MAX_KIB = 10_000_000
TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DEFAULTS = {"enabled": False, "default_down": 0, "default_up": 0, "slots": []}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    cfg = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    if not isinstance(cfg["slots"], list):
        cfg["slots"] = []
    return cfg


def _minutes(text):
    match = TIME.match(str(text))
    if not match:
        raise ctl.SeedboxError("heure : HH:MM attendu")
    return int(match.group(1)) * 60 + int(match.group(2))


def _rate(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_KIB:
        raise ctl.SeedboxError(f"{label} : nombre entier de ko/s entre 0 et {MAX_KIB} (0 : illimité)")
    return value


def clean_slot(slot):
    if not isinstance(slot, dict):
        raise ctl.SeedboxError("plage invalide")
    days = slot.get("days")
    if not isinstance(days, list) or not days or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days):
        raise ctl.SeedboxError("jours : liste de 0 (lundi) à 6 (dimanche), au moins un")
    start, end = str(slot.get("start", "")), str(slot.get("end", ""))
    if _minutes(start) == _minutes(end):
        raise ctl.SeedboxError("une plage ne peut pas commencer et finir à la même heure")
    return {"days": sorted(set(days)), "start": start, "end": end,
            "down": _rate(slot.get("down", 0), "débit descendant"), "up": _rate(slot.get("up", 0), "débit montant")}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        if key == "enabled":
            cfg[key] = bool(value)
        elif key in ("default_down", "default_up"):
            cfg[key] = _rate(value, "débit par défaut")
        else:
            if not isinstance(value, list) or len(value) > MAX_SLOTS:
                raise ctl.SeedboxError(f"au plus {MAX_SLOTS} plages")
            cfg[key] = [clean_slot(s) for s in value]
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    temp.write_text(json.dumps(cfg, indent=1))
    os.chmod(temp, 0o644)
    os.replace(temp, SETTINGS_FILE)
    say("plages de débit enregistrées")
    return cfg


def slot_index(cfg, now=None):
    """Numéro de la première plage qui correspond à l'heure (heure locale du serveur), ou None."""
    local = time.localtime(time.time() if now is None else now)
    minute, today, yesterday = local.tm_hour * 60 + local.tm_min, local.tm_wday, (local.tm_wday - 1) % 7
    for index, slot in enumerate(cfg["slots"]):
        start, end = _minutes(slot["start"]), _minutes(slot["end"])
        if start < end:
            if today in slot["days"] and start <= minute < end:
                return index
        elif (today in slot["days"] and minute >= start) or (yesterday in slot["days"] and minute < end):
            return index
    return None


def limits(cfg, now=None):
    """(ko/s descendant, ko/s montant, numéro de plage ou None) pour l'heure donnée."""
    index = slot_index(cfg, now)
    if index is None:
        return cfg["default_down"], cfg["default_up"], None
    return cfg["slots"][index]["down"], cfg["slots"][index]["up"], index


def state():
    try:
        value = json.loads(STATE_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(value):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if value:
        STATE_FILE.write_text(json.dumps(value))
    else:
        STATE_FILE.unlink(missing_ok=True)


def tick(now=None, qbit=None):
    """Applique la plage en cours si elle a changé. Renvoie True si les limites de qBittorrent ont été modifiées."""
    cfg, current = settings(), state()
    if not ctl.qbit_mode() and qbit is None:
        return False
    if not cfg["enabled"]:
        if not current:
            return False
        qbit = qbit or ctl.vacation().Qbit()
        original = current.get("original") or [0, 0]
        qbit.set_preferences({"dl_limit": int(original[0]), "up_limit": int(original[1])})
        _write_state({})
        say("débit : limites d'origine rétablies")
        return True
    down, up, index = limits(cfg, now)
    wanted = [down * 1024, up * 1024]
    if current.get("applied") == wanted:
        return False
    qbit = qbit or ctl.vacation().Qbit()
    original = current.get("original")
    if original is None:
        prefs = qbit.preferences()
        original = [int(prefs.get("dl_limit", 0) or 0), int(prefs.get("up_limit", 0) or 0)]
    qbit.set_preferences({"dl_limit": wanted[0], "up_limit": wanted[1]})
    _write_state({"applied": wanted, "slot": -1 if index is None else index, "original": original,
                  "since": int(time.time() if now is None else now)})
    say(f"débit : {'plage ' + str(index + 1) if index is not None else 'hors plage'} -> descendant {down or 'illimité'}, montant {up or 'illimité'} ko/s")
    return True


def next_change(cfg, now=None):
    """Prochain changement de limites dans les 8 jours : {at, down, up} ou None."""
    now = int(time.time() if now is None else now)
    here = limits(cfg, now)[:2]
    step = 300
    for offset in range(step, 8 * 86400, step):
        there = limits(cfg, now + offset)
        if there[:2] != here:
            return {"at": now + offset - (now + offset) % step, "down": there[0], "up": there[1]}
    return None


def overview(now=None):
    cfg, current = settings(), state()
    down, up, index = limits(cfg, now)
    return {"settings": cfg, "max_slots": MAX_SLOTS, "qbittorrent": bool(ctl.qbit_mode()),
            "now": {"down": down, "up": up, "slot": index},
            "applied": bool(current), "since": current.get("since", 0),
            "next": next_change(cfg, now) if cfg["enabled"] and cfg["slots"] else None}
