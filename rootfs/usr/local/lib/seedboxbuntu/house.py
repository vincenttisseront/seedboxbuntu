"""SeedBoxBuntu - page « maison » : l'état du serveur d'un coup d'œil, lisible de loin (écran de TV, tablette au mur).

Une seule lecture, quelques gros nombres : tout va bien ou non, combien de services tournent, les alertes en cours, les
téléchargements, le VPN, la dernière sauvegarde, la place restante. Aucun geste dangereux sur cette page : elle se contente
de se mettre à jour toute seule toutes les 30 secondes. Elle utilise la même connexion que la console (SSO), donc un écran de
TV doit y être connecté une fois ; rien n'est publié sans mot de passe.
    seedboxctl house
"""
import time

import seedboxctl as ctl

LEVELS = {"ok": 0, "info": 0, "warning": 1, "critical": 2}


def _safe(function, default):
    try:
        return function()
    except Exception:  # noqa: BLE001 - une source en panne n'empêche pas d'afficher les autres
        return default


def verdict(services, alerts, backup_age_days, disk_percent):
    """« ok », « warn » ou « bad » d'après les faits : fonction pure, testable."""
    if services["down"] or any(a["level"] == "critical" for a in alerts) or disk_percent >= 95:
        return "bad"
    if alerts or services["unhealthy"] or disk_percent >= 85 or (backup_age_days is not None and backup_age_days > 14):
        return "warn"
    return "ok"


def overview(now=None):
    now = time.time() if now is None else now
    states = _safe(ctl.containers, {})
    expected = _safe(lambda: ctl.health().expected_services(ctl.env_read()), list(states))
    running = [n for n in expected if states.get(n, {}).get("state") == "running"]
    unhealthy = [n for n in running if states[n].get("health") == "unhealthy"]
    down = [n for n in expected if n not in running]
    services = {"total": len(expected), "running": len(running), "down": down[:6], "unhealthy": unhealthy[:6]}
    remote = _safe(ctl.telecommande().overview, {"alerts": [], "alerts_total": 0, "downloads": {"available": False}, "vacation": {}})
    alerts = remote.get("alerts", [])
    backups = _safe(lambda: ctl.backup().listing(), [])
    age = None
    if backups:
        age = max(0, int((now - backups[0]["time"]) / 86400))
    disks = _safe(lambda: ctl.sysinfo().disks(), [])
    fullest = max(disks, key=lambda d: d["percent"], default=None)
    vpn = None
    if ctl.qbit_mode() == "vpn":
        health = states.get("gluetun", {})
        vpn = {"state": health.get("health") or health.get("state") or "arrêté", "exit": _safe(lambda: ctl.vpnwatch().state().get("exit", ""), ""),
               "leak": bool(_safe(lambda: ctl.vpnwatch().overview()["leak"], False))}
    percent = fullest["percent"] if fullest else 0
    return {"at": int(now), "verdict": verdict(services, alerts, age, percent), "services": services, "alerts": alerts[:5],
            "alerts_total": remote.get("alerts_total", 0), "downloads": remote.get("downloads", {}), "vacation": bool((remote.get("vacation") or {}).get("active")),
            "vpn": vpn, "backup": {"age_days": age, "count": len(backups)},
            "disk": {"label": fullest["label"], "percent": percent, "free": fullest["total"] - fullest["used"]} if fullest else None}
