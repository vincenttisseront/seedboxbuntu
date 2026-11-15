"""SeedBoxBuntu - quotas de lecture par compte invité.

Compte, pour chaque compte Jellyfin non administrateur, les titres DISTINCTS lus au cours des 30 derniers jours (d'après la
date de dernière lecture que Jellyfin garde pour chaque titre). Un quota (nombre de titres sur 30 jours) se fixe par compte, ou
un quota par défaut pour tous. Au-delà :
  - une alerte est envoyée (une fois par compte et par période) ;
  - si l'application automatique est activée, la lecture du compte est suspendue (EnableMediaPlayback = false dans sa politique
    Jellyfin) puis rétablie d'elle-même quand le compte repasse sous son quota, la fenêtre de 30 jours glissant.
Seuls les comptes que CE module a suspendus sont rétablis par lui : une suspension faite à la main dans Jellyfin n'est jamais levée.
Limites assumées : Jellyfin ne garde que la DERNIÈRE lecture d'un titre : un titre relu compte une fois ; ce n'est pas un temps de
visionnage ; les administrateurs ne sont jamais comptés ni suspendus ; l'effet est la lecture seule (le compte peut toujours
se connecter et parcourir).
    seedboxctl guestquota show | set default N | quota LOGIN N | unquota LOGIN | enforce on|off | resume LOGIN | tick
"""
import json
import os
import re
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "guestquota.json"
STATE_FILE = ctl.STATE / "guestquota.json"
DEFAULTS = {"default": 0, "quotas": {}, "enforce": False}
LOGIN = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
DAYS = 30
EVERY = 3600


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
    cfg["quotas"] = dict(cfg["quotas"])
    return cfg


def _write(cfg):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def _count(value, low=0, high=10000):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ctl.SeedboxError(f"quota : entier de {low} à {high}")
    return value


def set_default(value):
    cfg = settings()
    cfg["default"] = _count(value)
    return _write(cfg)


def set_quota(login, value):
    if not LOGIN.match(str(login)):
        raise ctl.SeedboxError("identifiant invalide")
    cfg = settings()
    if len(cfg["quotas"]) >= 200 and login not in cfg["quotas"]:
        raise ctl.SeedboxError("trop de quotas")
    cfg["quotas"][login] = _count(value, 1)
    return _write(cfg)


def remove_quota(login):
    cfg = settings()
    cfg["quotas"].pop(login, None)
    return _write(cfg)


def set_enforce(on):
    if not isinstance(on, bool):
        raise ctl.SeedboxError("enforce : vrai ou faux")
    cfg = settings()
    cfg["enforce"] = on
    return _write(cfg)


def _stamp(text):
    try:
        return int(time.mktime(time.strptime(str(text)[:19].replace("Z", ""), "%Y-%m-%dT%H:%M:%S")) - time.timezone)
    except ValueError:
        return 0


def usage(jellyfin=None, now=None):
    """[{id, name, played, policy}] pour les comptes non administrateurs ; None si Jellyfin est injoignable."""
    jellyfin = jellyfin or ctl.jellyfin_api
    now = time.time() if now is None else now
    limit = now - DAYS * 86400
    try:
        users = jellyfin("GET", "/Users").json() or []
        rows = []
        for user in users:
            policy = user.get("Policy") or {}
            if policy.get("IsAdministrator"):
                continue
            result = jellyfin("GET", f"/Users/{user['Id']}/Items?Recursive=true&IncludeItemTypes=Movie,Episode&Filters=IsPlayed"
                                     "&EnableUserData=true&Limit=20000")
            items = (result.json() or {}).get("Items", []) if result.ok else []
            played = sum(1 for i in items if _stamp((i.get("UserData") or {}).get("LastPlayedDate")) >= limit)
            rows.append({"id": user["Id"], "name": user.get("Name", ""), "played": played, "blocked": policy.get("EnableMediaPlayback") is False})
        return rows
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        return None


def _state():
    return _read(STATE_FILE, {})


def _store(data):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data))


def _policy(jellyfin, user_id, playback):
    user = jellyfin("GET", f"/Users/{user_id}").json() or {}
    policy = user.get("Policy") or {}
    policy["EnableMediaPlayback"] = playback
    result = jellyfin("POST", f"/Users/{user_id}/Policy", policy)
    if not result.ok:
        raise ctl.SeedboxError(f"Jellyfin : politique refusée (HTTP {result.status})")


def quota_of(name, cfg):
    return cfg["quotas"].get(name, cfg["default"])


def tick(now=None, jellyfin=None, force=False):
    """Compte, alerte, suspend ou rétablit. Renvoie la liste des actions."""
    now = time.time() if now is None else now
    cfg = settings()
    state = _state()
    if not force and now - state.get("at", 0) < EVERY:
        return []
    if not cfg["default"] and not cfg["quotas"]:
        return []
    jellyfin = jellyfin or ctl.jellyfin_api
    rows = usage(jellyfin, now)
    if rows is None:
        return []
    actions = []
    ours = state.setdefault("suspended", {})
    alerted = state.setdefault("alerted", {})
    for row in rows:
        quota = quota_of(row["name"], cfg)
        if not quota:
            continue
        over = row["played"] > quota
        if over and now - alerted.get(row["name"], 0) >= DAYS * 86400:
            ctl.alerts().notify("Quota de lecture dépassé", f"Un compte invité a lu {row['played']} titres sur 30 jours (quota : {quota}).", "warning")
            alerted[row["name"]] = int(now)
            actions.append(("alerte", row["name"]))
        if over and cfg["enforce"] and not row["blocked"]:
            _policy(jellyfin, row["id"], False)
            ours[row["id"]] = int(now)
            actions.append(("suspendu", row["name"]))
        elif not over and row["id"] in ours:
            _policy(jellyfin, row["id"], True)
            ours.pop(row["id"], None)
            actions.append(("rétabli", row["name"]))
    state["at"] = int(now)
    _store(state)
    return actions


def resume(login, jellyfin=None):
    """Rétablit à la main un compte suspendu par ce module."""
    jellyfin = jellyfin or ctl.jellyfin_api
    state = _state()
    for user in jellyfin("GET", "/Users").json() or []:
        if user.get("Name") == login and user["Id"] in state.get("suspended", {}):
            _policy(jellyfin, user["Id"], True)
            state["suspended"].pop(user["Id"], None)
            _store(state)
            return {"resumed": True}
    raise ctl.SeedboxError("ce compte n'a pas été suspendu par la console")


def overview(jellyfin=None, now=None):
    cfg = settings()
    rows = usage(jellyfin, now)
    state = _state()
    users = []
    for row in rows or []:
        quota = quota_of(row["name"], cfg)
        users.append({"name": row["name"], "played": row["played"], "quota": quota, "own_quota": row["name"] in cfg["quotas"],
                      "over": bool(quota and row["played"] > quota), "suspended": row["id"] in state.get("suspended", {}), "blocked": row["blocked"]})
    return {"settings": cfg, "available": rows is not None, "users": sorted(users, key=lambda u: -u["played"]), "days": DAYS}
