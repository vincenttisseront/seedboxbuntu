"""SeedBoxBuntu - bilan du mois par personne : demandes faites, disponibles, en attente.

Deux usages, un seul calcul (les demandes des 30 derniers jours dans Seerr) :
  - chaque personne voit son propre bilan sur « Mon espace » (/me/) : combien de demandes, combien sont disponibles ;
  - l'administrateur, s'il active le bilan mensuel, reçoit un résumé de tout le monde sur ses canaux d'alerte (ntfy, Discord,
    webhook, e-mail) le jour choisi du mois, une seule fois.
Ce bilan ne contient que des nombres : jamais un titre, jamais ce qui a été regardé. Il est désactivé par défaut, car il
rend visible l'usage de chacun à l'administrateur : c'est un choix à faire en connaissance de cause (et à annoncer aux personnes).
Les comptes de l'appliance n'ont pas d'adresse e-mail réelle : le bilan personnel n'est donc pas envoyé par courrier, il
se consulte sur « Mon espace ». Les lectures dans Jellyfin ne sont pas comptées (Jellyfin ne les expose pas sans module complémentaire).
    seedboxctl peoplereport show | set | send
"""
import json
import os
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "peoplereport.json"
LAST_FILE = ctl.STATE / "peoplereport-last.json"
DEFAULTS = {"enabled": False, "day": 1}
WINDOW_DAYS = 30


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


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
        else:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 28:
                raise ctl.SeedboxError("jour du mois : un entier de 1 à 28")
            cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    say(f"bilan mensuel : {'activé' if cfg['enabled'] else 'désactivé'}, le {cfg['day']} du mois")
    return cfg


def _stamp(text):
    try:
        return int(time.mktime(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)
    except ValueError:
        return 0


def month(login, now=None, fetch=None):
    """Bilan d'un compte sur 30 jours : nombres seulement. None si Seerr ne le connaît pas."""
    now = time.time() if now is None else now
    try:
        if fetch is None:
            jellyfin = ctl.jellyfin_user(login)
            seerr = ctl.seerr_user_id(jellyfin["Id"]) if jellyfin else None
            if not seerr:
                return None
            rows = (ctl.seerr_api("GET", f"/user/{seerr}/requests?take=100&skip=0&sort=added").json() or {}).get("results", [])
        else:
            rows = fetch(login)
            if rows is None:
                return None
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        return None
    since = now - WINDOW_DAYS * 86400
    recent = [r for r in rows if _stamp(r.get("createdAt")) >= since]
    available = sum(1 for r in recent if (r.get("media") or {}).get("status") in (4, 5))
    return {"days": WINDOW_DAYS, "requests": len(recent), "available": available,
            "pending": sum(1 for r in recent if r.get("status") == 1), "declined": sum(1 for r in recent if r.get("status") == 3)}


def summary(now=None, fetch=None):
    people = []
    for account in ctl.user_list():
        if account["role"] == "observer":
            continue
        data = month(account["login"], now, fetch)
        people.append({"login": account["login"], "role": account["role"], **(data or {"days": WINDOW_DAYS, "requests": None})})
    people.sort(key=lambda p: (-(p["requests"] or 0), p["login"]))
    return people


def text_of(people):
    lines = [f"{p['login']} : " + (f"{p['requests']} demande(s), {p['available']} disponible(s)" + (f", {p['pending']} en attente" if p.get("pending") else "")
                                   if p["requests"] is not None else "pas de compte Seerr")
             for p in people]
    return "\n".join(lines)


def send(now=None, fetch=None):
    people = summary(now, fetch)
    text = text_of(people)
    ctl.alerts().notify("Bilan du mois par personne", text[:1500], "ok", force=True)
    say(f"bilan mensuel envoyé ({len(people)} personne(s))")
    return {"people": len(people)}


def tick(now=None, fetch=None):
    """Passage de 5 minutes : envoie le bilan le jour choisi, une seule fois par mois."""
    cfg = settings()
    if not cfg["enabled"]:
        return False
    now = time.time() if now is None else now
    local = time.localtime(now)
    if local.tm_mday != cfg["day"] or local.tm_hour < 9:
        return False
    stamp = time.strftime("%Y-%m", local)
    try:
        if json.loads(LAST_FILE.read_text()).get("month") == stamp:
            return False
    except (OSError, ValueError, AttributeError):
        pass
    LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(json.dumps({"month": stamp, "at": int(now)}))
    send(now, fetch)
    return True


def overview(now=None):
    cfg = settings()
    result = {"settings": cfg, "people": [], "text": ""}
    if cfg["enabled"]:
        result["people"] = summary(now)
    try:
        result["last"] = json.loads(LAST_FILE.read_text()).get("at", 0)
    except (OSError, ValueError, AttributeError):
        result["last"] = 0
    return result
