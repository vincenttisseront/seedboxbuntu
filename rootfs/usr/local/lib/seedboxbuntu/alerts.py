"""SeedBoxBuntu - alertes : ntfy, Discord, webhook générique, e-mail.

« seedboxctl alerts check » (minuteur de 5 minutes) surveille : conteneurs attendus, espace disque,
mise à jour qui échoue en boucle, étapes de configuration en échec, certificat proche de l'expiration.
Une alerte n'est envoyée qu'après deux constats successifs (un redémarrage ne déclenche rien), rappelée
toutes les 6 h tant qu'elle dure, et un message « rétabli » l'annonce à sa disparition.
Réglages dans /etc/seedboxbuntu/alerts.json (0600) : les secrets n'en sortent jamais vers la console.
"""
import json
import os
import re
import smtplib
import ssl
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "alerts.json"
STATE_FILE = ctl.STATE / "alerts-state.json"
MASK = "********"
SECRET_FIELDS = {"ntfy": ("token",), "discord": ("webhook",), "webhook": ("url", "secret"), "email": ("password",)}
DEFAULTS = {
    "enabled": True, "disk_percent": 85, "repeat_hours": 6, "forecast_days": 14,
    "ntfy": {"url": "https://ntfy.sh", "topic": "", "token": ""},
    "discord": {"webhook": ""},
    "webhook": {"url": "", "secret": ""},
    "email": {"host": "", "port": 587, "security": "starttls", "user": "", "password": "", "sender": "", "to": ""},
}
CONFIRM_AFTER = 2          # constats successifs avant d'alerter
CERT_DAYS = 30


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    stored = {}
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        pass
    merged = {}
    for key, default in DEFAULTS.items():
        if isinstance(default, dict):
            merged[key] = {**default, **{k: v for k, v in (stored.get(key) or {}).items() if k in default}}
        else:
            merged[key] = stored.get(key, default)
    return merged


def masked(cfg=None):
    cfg = cfg or settings()
    out = json.loads(json.dumps(cfg))
    for channel, fields in SECRET_FIELDS.items():
        for field in fields:
            if out[channel].get(field):
                out[channel][field] = MASK
    return out


def valid_url(value, schemes=("https", "http")):
    parts = urllib.parse.urlsplit(value)
    return parts.scheme in schemes and bool(parts.hostname) and not any(c in value for c in " \r\n\t")


def validate(values):
    """Fusionne des valeurs reçues avec les réglages ; MASK = secret inchangé."""
    cfg = settings()
    if "enabled" in values:
        cfg["enabled"] = bool(values["enabled"])
    for key, low, high in (("disk_percent", 50, 99), ("repeat_hours", 1, 168), ("forecast_days", 0, 90)):
        if key in values:
            try:
                cfg[key] = max(low, min(int(values[key]), high))
            except (TypeError, ValueError):
                raise ctl.SeedboxError(f"{key} : nombre attendu")
    for channel in ("ntfy", "discord", "webhook", "email"):
        for field, value in (values.get(channel) or {}).items():
            if field not in DEFAULTS[channel]:
                continue
            if value == MASK:
                continue
            if isinstance(DEFAULTS[channel][field], int):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ctl.SeedboxError(f"{channel} {field} : nombre attendu")
            else:
                value = str(value).strip()
                if "\n" in value or "\r" in value:
                    raise ctl.SeedboxError(f"{channel} {field} : saisie invalide")
            cfg[channel][field] = value
    n, d, w, e = cfg["ntfy"], cfg["discord"], cfg["webhook"], cfg["email"]
    if n["url"] and not valid_url(n["url"]):
        raise ctl.SeedboxError("ntfy : adresse http(s) attendue")
    if n["topic"] and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n["topic"]):
        raise ctl.SeedboxError("ntfy : sujet (lettres, chiffres, - et _)")
    if d["webhook"] and not re.fullmatch(r"https://(discord|discordapp)\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+", d["webhook"]):
        raise ctl.SeedboxError("Discord : adresse de webhook invalide (https://discord.com/api/webhooks/...)")
    if w["url"] and not valid_url(w["url"]):
        raise ctl.SeedboxError("webhook : adresse http(s) attendue")
    if e["security"] not in ("starttls", "ssl", "none"):
        raise ctl.SeedboxError("e-mail : sécurité starttls, ssl ou none")
    for field in ("sender", "to"):
        if e[field] and not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", e[field]):
            raise ctl.SeedboxError(f"e-mail : adresse « {field} » invalide")
    return cfg


def save(values):
    cfg = validate(values)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(SETTINGS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(cfg, indent=2) + "\n")
    say("réglages des alertes enregistrés")
    return cfg


# --- envoi -------------------------------------------------------------------------------------
def _post(url, data, headers, timeout=15):
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        return response.status


def send_ntfy(cfg, title, message, level):
    n = cfg["ntfy"]
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode() or "SeedBoxBuntu"
    headers = {"Title": plain,
               "Priority": {"critical": "5", "warning": "4", "ok": "3"}.get(level, "3"),
               "Tags": {"critical": "rotating_light", "warning": "warning", "ok": "white_check_mark"}.get(level, "bell")}
    if n["token"]:
        headers["Authorization"] = f"Bearer {n['token']}"
    _post(f"{n['url'].rstrip('/')}/{n['topic']}", message.encode("utf-8"), headers)


def send_discord(cfg, title, message, level):
    icon = {"critical": "🔴", "warning": "🟠", "ok": "🟢"}.get(level, "ℹ️")
    _post(cfg["discord"]["webhook"], json.dumps({"content": f"{icon} **{title}**\n{message}"[:1900]}).encode(),
          {"Content-Type": "application/json", "User-Agent": "SeedBoxBuntu"})


def send_webhook(cfg, title, message, level):
    w = cfg["webhook"]
    headers = {"Content-Type": "application/json", "User-Agent": "SeedBoxBuntu"}
    if w["secret"]:
        headers["X-SeedBox-Secret"] = w["secret"]
    _post(w["url"], json.dumps({"title": title, "message": message, "level": level, "host": ctl.version()}).encode(), headers)


def send_email(cfg, title, message, level):
    e = cfg["email"]
    mail = EmailMessage()
    mail["Subject"], mail["From"], mail["To"] = f"[SeedBoxBuntu] {title}", e["sender"] or e["user"], e["to"]
    mail.set_content(message)
    if e["security"] == "ssl":
        client = smtplib.SMTP_SSL(e["host"], int(e["port"]), timeout=20, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(e["host"], int(e["port"]), timeout=20)
    with client:
        if e["security"] == "starttls":
            client.starttls(context=ssl.create_default_context())
        if e["user"]:
            client.login(e["user"], e["password"])
        client.send_message(mail)


CHANNELS = (("ntfy", lambda c: c["ntfy"]["topic"], send_ntfy), ("discord", lambda c: c["discord"]["webhook"], send_discord),
            ("webhook", lambda c: c["webhook"]["url"], send_webhook), ("email", lambda c: c["email"]["host"] and c["email"]["to"], send_email))


def notify(title, message, level="warning", force=False):
    """Envoie sur tous les canaux configurés ; renvoie {canal: None | message d'erreur}."""
    cfg = settings()
    results = {}
    if not cfg["enabled"] and not force:
        return results
    for name, configured, sender in CHANNELS:
        if not configured(cfg):
            continue
        try:
            sender(cfg, title, message, level)
            results[name] = None
        except Exception as error:  # un canal en panne ne doit pas empêcher les autres
            results[name] = str(error)[:200] or error.__class__.__name__
    return results


def test():
    results = notify("Test SeedBoxBuntu", "Les alertes fonctionnent : ce message est un essai.", "ok", force=True)
    if not results:
        raise ctl.SeedboxError("aucun canal configuré")
    for name, error in results.items():
        say(f"{name} : {'envoyé' if error is None else 'ÉCHEC : ' + error}")
    if any(results.values()):
        raise ctl.SeedboxError("au moins un canal a échoué")


# --- surveillance ------------------------------------------------------------------------------
def disk_paths():
    paths, seen = [], set()
    candidates = [("stockage", ctl.STORAGE), ("système et configuration", ctl.BASE)]
    try:
        shares = ctl.shares()
        candidates += [(Path(t).name, Path(t)) for t in shares.mounted_targets() if str(t).startswith(str(shares.ROOT))]
    except Exception:  # noqa: BLE001 - un partage illisible ne doit pas bloquer la surveillance
        pass
    for label, path in candidates:
        try:
            device = os.stat(path).st_dev
        except OSError:
            continue
        if device not in seen:
            seen.add(device)
            paths.append((label, path))
    return paths


def findings(cfg=None):
    """Problèmes constatés maintenant : {clé: (titre, détail, niveau)}."""
    cfg = cfg or settings()
    found = {}
    health = ctl.health()
    states = ctl.containers()
    for name in health.expected_services(ctl.env_read()):
        state = states.get(name)
        if not state or state["state"] != "running":
            found[f"conteneur:{name}"] = (f"{name} est arrêté", f"Le service {name} n'est pas en marche ({(state or {}).get('state', 'absent')}).", "critical")
        elif state["health"] == "unhealthy":
            found[f"conteneur:{name}"] = (f"{name} en difficulté", f"Le service {name} répond « unhealthy ».", "warning")
    for label, path in disk_paths():
        try:
            usage = os.statvfs(path)
        except OSError:
            continue
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        percent = round(100 * (total - free) / total) if total else 0
        if percent >= cfg["disk_percent"]:
            found[f"disque:{path}"] = (f"Disque presque plein : {label}", f"{percent} % utilisés, {free // (1 << 30)} Go libres ({path}).", "critical" if percent >= 95 else "warning")
    if cfg["forecast_days"]:
        try:
            for item in ctl.metrics().risky(cfg["forecast_days"]):
                found[f"prevision:{item['path']}"] = (
                    f"Disque plein dans environ {max(1, round(item['days']))} jour(s) : {item['path']}",
                    f"Le disque se remplit de {item['per_day'] / (1 << 30):.1f} Go par jour ; il serait plein vers le {item['full_on']} "
                    f"({(item['total'] - item['used']) // (1 << 30)} Go libres). Voir Système > Historique, ou lancez l'assistant de nettoyage.",
                    "critical" if item["days"] < 3 else "warning")
        except (ctl.SeedboxError, OSError, ValueError, KeyError):
            pass
    pending = ctl.STATE / "upgrade-pending"
    if pending.exists() and time.time() - pending.stat().st_mtime > 900:
        found["mise-a-jour"] = ("Mise à jour de l'appliance bloquée", "Elle échoue depuis plus de 15 minutes : voir Applications > Configuration et mises à jour.", "warning")
    for label, value in ctl.steps_load().items():
        if not value["ok"] and not value["ignored"] and not value["critical"]:
            found[f"etape:{label}"] = (f"Étape en échec : {label}", value["error"][:300], "warning")
    cert = ctl.CONFIG / "traefik" / "certs" / "server.crt"
    if cert.exists():
        check = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", str(CERT_DAYS * 86400)],
                               capture_output=True)
        if check.returncode == 1:
            found["certificat"] = ("Certificat bientôt expiré", f"Le certificat de l'appliance expire dans moins de {CERT_DAYS} jours : ajoutez ou changez une adresse dans la console pour le renouveler.", "warning")
    return found


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def check():
    """Une passe de surveillance ; renvoie le nombre de messages envoyés."""
    cfg = settings()
    if not cfg["enabled"]:
        return 0
    now = time.time()
    state, found, sent = load_state(), findings(cfg), 0
    for key, (title, detail, level) in found.items():
        entry = state.setdefault(key, {"seen": 0, "notified": 0, "title": title})
        entry.update(seen=entry["seen"] + 1, title=title, detail=detail, level=level)
        due = entry["notified"] == 0 and entry["seen"] >= CONFIRM_AFTER
        if entry["notified"] and now - entry["notified"] >= cfg["repeat_hours"] * 3600:
            due = True
        if due:
            notify(title, detail, level)
            entry["notified"] = now
            sent += 1
            say(f"alerte : {title}")
    for key in [k for k in state if k not in found]:
        entry = state.pop(key)
        if entry.get("notified"):
            notify(f"Rétabli : {entry.get('title', key)}", "Le problème signalé n'est plus constaté.", "ok")
            sent += 1
            say(f"rétabli : {entry.get('title', key)}")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    return sent


def overview():
    state = load_state()
    return {"settings": masked(), "active": [{"title": v.get("title", k), "detail": v.get("detail", ""), "level": v.get("level", "warning"),
                                              "notified": bool(v.get("notified"))} for k, v in sorted(state.items())],
            "channels": [name for name, configured, _ in CHANNELS if configured(settings())]}
