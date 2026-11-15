"""SeedBoxBuntu - activité par personne : dernière connexion, lecture en cours et quotas de demandes.

Facultatif et désactivé par défaut : il regarde ce que font les personnes qui utilisent la seedbox, ce qui est un choix de
l'administrateur et non un réglage neutre. Rien n'est enregistré : les informations sont lues à la demande dans Jellyfin
(dernière connexion, dernière activité, lecture en cours) et dans Seerr (demandes et quotas). Aucun titre de film ou de série
n'est conservé ; la lecture en cours n'est visible que pendant qu'elle a lieu.
    seedboxctl activity show | set
"""
import json
import os
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "activity.json"
DEFAULTS = {"enabled": False, "show_playing": False}


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
        cfg[key] = bool(value)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    ctl.say(f"activité par personne : {'activée' if cfg['enabled'] else 'désactivée'}")
    return cfg


def _stamp(text):
    """Date ISO de Jellyfin -> secondes, ou 0."""
    if not text:
        return 0
    try:
        return int(time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)
    except ValueError:
        return 0


def _quota(seerr_id):
    try:
        data = ctl.seerr_api("GET", f"/user/{seerr_id}/quota").json() or {}
    except (OSError, ctl.SeedboxError):
        return {}
    out = {}
    for kind in ("movie", "tv"):
        part = data.get(kind) or {}
        if part.get("limit"):
            out[kind] = {"used": part.get("used", 0), "limit": part.get("limit", 0), "days": part.get("days", 0)}
    return out


def overview(now=None):
    cfg = settings()
    if not cfg["enabled"]:
        return {"settings": cfg, "enabled": False, "people": []}
    now = int(now if now is not None else time.time())
    playing = {}
    if cfg["show_playing"]:
        try:
            for session in ctl.jellyfin_api("GET", "/Sessions").json() or []:
                if session.get("NowPlayingItem") and session.get("UserName"):
                    playing[session["UserName"].lower()] = True      # seulement « en lecture » : jamais le titre
        except (OSError, ctl.SeedboxError):
            pass
    accounts = {u["login"]: u for u in ctl.user_list()}
    jellyfin = {}
    try:
        jellyfin = {u["Name"].lower(): u for u in ctl.jellyfin_api("GET", "/Users").json() or []}
    except (OSError, ctl.SeedboxError):
        pass
    people = []
    for login, account in sorted(accounts.items()):
        jf = jellyfin.get(login.lower())
        entry = {"login": login, "role": account["role"], "jellyfin": bool(jf), "last_login": 0, "last_activity": 0,
                 "playing": bool(playing.get(login.lower())), "requests": None, "quota": {}}
        if jf:
            entry["last_login"] = _stamp(jf.get("LastLoginDate"))
            entry["last_activity"] = _stamp(jf.get("LastActivityDate"))
            try:
                seerr_id = ctl.seerr_user_id(jf["Id"])
                if seerr_id:
                    entry["requests"] = (ctl.seerr_api("GET", f"/user/{seerr_id}").json() or {}).get("requestCount")
                    entry["quota"] = _quota(seerr_id)
            except (OSError, ctl.SeedboxError):
                pass
        entry["inactive_days"] = int((now - entry["last_activity"]) / 86400) if entry["last_activity"] else None
        people.append(entry)
    people.sort(key=lambda p: -(p["last_activity"] or 0))
    return {"settings": cfg, "enabled": True, "people": people, "now": now}
