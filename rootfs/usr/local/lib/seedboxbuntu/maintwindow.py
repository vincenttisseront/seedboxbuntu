"""SeedBoxBuntu - fenêtre de maintenance : les tâches lourdes automatiques ne démarrent que dans la plage choisie.

Réglages (/etc/seedboxbuntu/window.json) : jours de la semaine (0 = lundi) et heures de début et de fin (la fin peut passer
minuit : 23:00 -> 05:00). Tâches concernées : mise à jour automatique de l'appliance, mise à jour automatique
de p2p-aggregator, analyse de vulnérabilités, test de restauration mensuel. Une tâche qui se déclenche hors fenêtre est
mise en attente (window-pending.json) ; le passage de surveillance (5 minutes) la lance dès que la fenêtre s'ouvre.
Un lancement manuel depuis la console n'est jamais retenu.
    seedboxctl window show | set | tick
"""
import json
import os
import re
import subprocess
import sys
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "window.json"
PENDING_FILE = ctl.STATE / "window-pending.json"
DEFAULTS = {"enabled": False, "days": [0, 1, 2, 3, 4, 5, 6], "start": "02:00", "end": "06:00"}
# tâche -> arguments de seedboxctl ; seules ces tâches peuvent être mises en attente
TASKS = {"appliance": ["appliance", "auto"], "autoupdate": ["autoupdate"], "vulnscan": ["vulnscan", "auto"],
         "restoretest": ["offsite", "restore-test", "--auto"]}
TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def clean(values, base=None):
    cfg = dict(base or settings())
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        if key == "enabled":
            cfg[key] = bool(value)
        elif key == "days":
            if not isinstance(value, list) or not value or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in value):
                raise ctl.SeedboxError("jours : liste de 0 (lundi) à 6 (dimanche), au moins un")
            cfg[key] = sorted(set(value))
        else:
            if not isinstance(value, str) or not TIME.fullmatch(value):
                raise ctl.SeedboxError(f"{key} : heure HH:MM attendue")
            cfg[key] = value
    if cfg["start"] == cfg["end"]:
        raise ctl.SeedboxError("la fenêtre doit avoir une durée : début et fin différents")
    return cfg


def save(values):
    cfg = clean(values)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    say(f"fenêtre de maintenance : {'activée' if cfg['enabled'] else 'désactivée'}")
    return cfg


def _minutes(text):
    return int(text[:2]) * 60 + int(text[3:])


def is_open(now=None, cfg=None):
    """Vrai si l'instant est dans la fenêtre (ou si aucune fenêtre n'est activée)."""
    cfg = cfg or settings()
    if not cfg["enabled"]:
        return True
    moment = time.localtime(time.time() if now is None else now)
    minute = moment.tm_hour * 60 + moment.tm_min
    start, end = _minutes(cfg["start"]), _minutes(cfg["end"])
    if start < end:
        return moment.tm_wday in cfg["days"] and start <= minute < end
    # fenêtre à cheval sur minuit : la partie après minuit appartient au jour précédent
    if minute >= start:
        return moment.tm_wday in cfg["days"]
    return (moment.tm_wday - 1) % 7 in cfg["days"] and minute < end


def next_open(now=None, cfg=None):
    """Prochain instant d'ouverture (secondes), ou None si la fenêtre est ouverte ou désactivée."""
    cfg = cfg or settings()
    now = time.time() if now is None else now
    if is_open(now, cfg):
        return None
    step = 60 * 5
    for i in range(1, 8 * 24 * 12 + 1):
        if is_open(now + i * step, cfg):
            return int(now + i * step)
    return None


def _pending():
    try:
        value = json.loads(PENDING_FILE.read_text())
        return [t for t in value if t in TASKS]
    except (OSError, ValueError, TypeError):
        return []


def _write_pending(items):
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(sorted(set(items))))


def allow(task, now=None):
    """Appelée au début d'une tâche automatique : True = elle peut tourner ; False = reportée (elle sera relancée)."""
    if task not in TASKS:
        raise ValueError(task)
    if is_open(now):
        return True
    _write_pending(_pending() + [task])
    say(f"{task} : hors de la fenêtre de maintenance, reportée à l'ouverture")
    return False


def tick(now=None, runner=None):
    """Appelée toutes les 5 minutes : lance les tâches reportées dès que la fenêtre est ouverte. Renvoie les tâches lancées."""
    pending = _pending()
    if not pending or not is_open(now):
        return []
    _write_pending([])
    runner = runner or (lambda task: subprocess.Popen([sys.executable, ctl.__file__, *TASKS[task]], stdout=subprocess.DEVNULL,
                                                      stderr=subprocess.DEVNULL, start_new_session=True))
    for task in pending:
        say(f"fenêtre ouverte : lancement de {task}")
        runner(task)
    return pending


def overview(now=None):
    cfg = settings()
    after = next_open(now, cfg)
    return {"settings": cfg, "open": is_open(now, cfg), "next_open": after, "pending": _pending(), "tasks": sorted(TASKS)}
