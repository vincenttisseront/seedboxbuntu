"""SeedBoxBuntu - rapport hebdomadaire : l'état du serveur en un message.

Contenu : services, espace disque et prévision, sauvegardes (copie hors serveur et test de restauration),
mises à jour, incidents de la semaine, invitations. Envoyé par les canaux d'alerte déjà configurés (ntfy,
Discord, webhook, e-mail). seedbox-report.timer passe chaque jour ; « seedboxctl report auto » n'envoie que
le jour choisi, une seule fois par jour. Réglages : /etc/seedboxbuntu/report.json.
"""
import json
import os
import time
from pathlib import Path

import seedboxctl as ctl
import notifications

SETTINGS_FILE = ctl.ETC / "report.json"
STATE_FILE = ctl.STATE / "report-last.json"
DEFAULTS = {"enabled": False, "day": 0, "send": True}          # day : 0 = lundi ... 6 = dimanche
DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
WARN, CRIT = 85, 95                                              # mêmes seuils que la console (--th-warn / --th-crit)
BACKUP_MAX_AGE_DAYS = 8
WEEK = 7 * 86400


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def settings():
    stored = _read(SETTINGS_FILE, {})
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    current = settings()
    for key in values:
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
    day = values.get("day", current["day"])
    if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
        raise ctl.SeedboxError("jour invalide (0 = lundi ... 6 = dimanche)")
    merged = {"enabled": bool(values.get("enabled", current["enabled"])), "day": day, "send": bool(values.get("send", current["send"]))}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(merged))
    os.chmod(SETTINGS_FILE, 0o644)
    say(f"rapport hebdomadaire : {'activé, le ' + DAYS[day] if merged['enabled'] else 'désactivé'}")
    return merged


def _size(value):
    value = float(value or 0)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024 or unit == "To":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return ""


def _day(stamp):
    return time.strftime("%d/%m/%Y", time.localtime(stamp)) if stamp else "jamais"


def build(now=None):
    """Rapport structuré ; chaque section est calculée à part (une panne n'empêche pas le reste)."""
    now = time.time() if now is None else now
    report = {"generated": int(now), "host": os.uname().nodename, "sections": {}, "problems": [], "notes": []}
    sections = report["sections"]

    def section(name, compute):
        try:
            sections[name] = compute()
        except Exception as error:  # noqa: BLE001
            sections[name] = {"error": str(error)[:160]}
            report["notes"].append(f"{name} : indisponible ({str(error)[:80]})")

    def services():
        status = ctl.status()
        down = [a["name"] for a in status["apps"] if a.get("state") != "running" or a.get("health") == "unhealthy"]
        if down:
            report["problems"].append(("critical", "Services à l’arrêt ou en difficulté : " + ", ".join(down[:6])))
        return {"total": len(status["apps"]), "down": down}

    def disks():
        rows, forecasts = [], {f["path"]: f for f in ctl.metrics().forecast()}
        for disk in ctl.sysinfo().disks():
            percent = int(disk.get("percent", 0))
            row = {"label": disk.get("label", ""), "path": disk.get("path", ""), "percent": percent,
                   "used": disk.get("used", 0), "total": disk.get("total", 0)}
            future = forecasts.get(row["path"])
            if future and future.get("reliable") and future.get("days") is not None and future["days"] < 60:
                row["full_in_days"] = future["days"]
                report["problems"].append(("warning", f"{row['label'] or row['path']} : plein dans environ {future['days']:.0f} jours".replace(".", ",")))
            if percent >= CRIT:
                report["problems"].append(("critical", f"{row['label'] or row['path']} : {percent} % utilisé"))
            elif percent >= WARN:
                report["problems"].append(("warning", f"{row['label'] or row['path']} : {percent} % utilisé"))
            rows.append(row)
        return rows

    def backups():
        entries = ctl.backup().listing()
        newest = entries[0]["time"] if entries else 0
        cfg = ctl.backup().settings()
        if cfg.get("enabled") and (not newest or now - newest > BACKUP_MAX_AGE_DAYS * 86400):
            report["problems"].append(("warning", "Aucune sauvegarde de moins de 8 jours alors qu’elles sont activées"))
        offsite = ctl.offsite().overview()
        test = offsite.get("last_test") or {}
        if test and not test.get("ok"):
            report["problems"].append(("warning", "Le dernier test de restauration a échoué"))
        return {"count": len(entries), "newest": newest, "enabled": bool(cfg.get("enabled")), "offsite": bool(offsite.get("configured")),
                "restore_test": {"at": test.get("at", 0), "ok": bool(test.get("ok"))} if test else None}

    def updates():
        data = ctl.appliance().overview()
        check = data.get("check") or {}
        return {"version": (data.get("installed") or {}).get("version", ""), "available": bool(check.get("available"))}

    def incidents():
        events = [e for e in notifications.recent(200) if e["t"] >= now - WEEK and e.get("kind") == "alert" and e.get("level") in ("warning", "critical")]
        active = [i for i in notifications.feed(now=now)["items"] if i.get("active")]
        for item in active:
            report["problems"].append((("critical" if item["level"] == "critical" else "warning"), "Alerte en cours : " + item["title"]))
        return {"week": len(events), "titles": sorted({e["title"] for e in events})[:5], "active": len(active)}

    def guests():
        data = ctl.guests().overview()
        active = [i for i in data["invites"] if i.get("status") == "active"]
        soon = [i for i in active if i.get("expires") and i["expires"] - now < WEEK]
        return {"active": len(active), "expiring": len(soon)}

    section("services", services)
    section("disks", disks)
    section("backups", backups)
    section("updates", updates)
    section("incidents", incidents)
    section("guests", guests)
    levels = [level for level, _ in report["problems"]]
    report["level"] = "critical" if "critical" in levels else "warning" if levels else "ok"
    return report


def render(report):
    """Message texte (ntfy, e-mail, Discord) : titre et corps, sans mise en forme propre à un canal."""
    s = report["sections"]
    title = {"ok": "Rapport hebdomadaire : tout va bien", "warning": "Rapport hebdomadaire : points à regarder",
             "critical": "Rapport hebdomadaire : action nécessaire"}[report["level"]]
    lines = [f"{report['host']} - semaine du {_day(report['generated'] - WEEK)} au {_day(report['generated'])}", ""]
    if report["problems"]:
        lines += ["À REGARDER"] + [f"- {text}" for _, text in report["problems"]] + [""]
    if "total" in s.get("services", {}):
        lines.append(f"Services : {s['services']['total'] - len(s['services']['down'])} / {s['services']['total']} fonctionnent")
    if isinstance(s.get("disks"), list):
        for disk in s["disks"]:
            lines.append(f"Disque {disk['label'] or disk['path']} : {disk['percent']} % ({_size(disk['used'])} sur {_size(disk['total'])})")
    b = s.get("backups", {})
    if "count" in b:
        lines.append(f"Sauvegardes : {b['count']} (dernière : {_day(b['newest'])}), copie hors serveur {'oui' if b['offsite'] else 'non'}"
                     + (f", test de restauration {'réussi' if b['restore_test']['ok'] else 'ÉCHOUÉ'} le {_day(b['restore_test']['at'])}" if b.get("restore_test") else ""))
    u = s.get("updates", {})
    if "version" in u:
        lines.append(f"Version : {u['version']}" + (" (mise à jour disponible)" if u["available"] else ""))
    i = s.get("incidents", {})
    if "week" in i:
        lines.append(f"Incidents cette semaine : {i['week']}" + (" (" + ", ".join(i["titles"]) + ")" if i["titles"] else ""))
    g = s.get("guests", {})
    if "active" in g:
        lines.append(f"Invitations actives : {g['active']}" + (f", dont {g['expiring']} expire(nt) sous 7 jours" if g["expiring"] else ""))
    lines += [""] + [f"Note : {n}" for n in report["notes"]]
    return title, "\n".join(line for line in lines).strip()


def build_and_store(now=None):
    report = build(now)
    title, body = render(report)
    report["title"], report["text"] = title, body
    previous = _read(STATE_FILE, {})
    report["sent"] = previous.get("sent", 0)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(report, ensure_ascii=False))
    return report


def last():
    return _read(STATE_FILE, None)


def overview():
    cfg = settings()
    module = ctl.alerts()
    alert_cfg = module.settings()
    return {"settings": cfg, "days": list(DAYS), "last": last(),
            "channels": [name for name, configured, _ in module.CHANNELS if configured(alert_cfg)]}


def send(now=None):
    """Construit le rapport et l'envoie sur les canaux configurés (même si les alertes sont coupées : demande explicite)."""
    report = build_and_store(now)
    results = ctl.alerts().notify(report["title"], report["text"], report["level"] if report["level"] != "critical" else "critical", force=True)
    if not results:
        raise ctl.SeedboxError("aucun canal d'alerte configuré : le rapport est affiché dans la console seulement")
    failed = {name: error for name, error in results.items() if error}
    if failed and len(failed) == len(results):
        raise ctl.SeedboxError("envoi impossible : " + "; ".join(f"{n} : {e}" for n, e in failed.items()))
    report["sent"] = int(time.time() if now is None else now)
    STATE_FILE.write_text(json.dumps(report, ensure_ascii=False))
    notifications.record("report", "Rapport hebdomadaire envoyé", "", "info", "report")
    say("rapport envoyé : " + ", ".join(n for n, e in results.items() if not e))
    return results


def auto(now=None):
    """Appel du minuteur quotidien : n'envoie que le jour choisi, une fois."""
    cfg = settings()
    now = time.time() if now is None else now
    if not cfg["enabled"] or time.localtime(now).tm_wday != cfg["day"]:
        return False
    previous = (last() or {}).get("sent", 0)
    if previous and time.strftime("%Y-%m-%d", time.localtime(previous)) == time.strftime("%Y-%m-%d", time.localtime(now)):
        return False
    if cfg["send"]:
        send(now)
    else:
        build_and_store(now)
    return True
