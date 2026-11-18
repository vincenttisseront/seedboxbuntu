"""Serveur de maquette de la console : sert les fichiers web et de fausses réponses /console/api/*.
Utilisé par tests/visual_test.py (captures de chaque page, contrôles de contraste) ; sans aucun service réel.
    python3 tests/ui_mock.py [port]
"""
import http.server, json, pathlib, sys, threading, time, math, urllib.parse
WEB = pathlib.Path(__file__).resolve().parent.parent / "rootfs" / "usr" / "local" / "lib" / "seedboxbuntu" / "console" / "web"
now = 1794000000   # 2026-11-06 : valeur fixe, les captures restent comparables d'un jour à l'autre
CALLS = []
def series(seconds, step, fn):
    return [[now - seconds + i * step, round(fn(i), 2)] for i in range(int(seconds / step) + 1)]
def metrics(rng):
    sec = {"1h": 3600, "6h": 21600, "24h": 86400, "7j": 604800, "30j": 2592000, "1an": 31536000}[rng]
    step = max(300, sec // 200)
    n = lambda i: i
    return {"range": rng, "ranges": ["1h", "6h", "24h", "7j", "30j", "1an"], "now": now, "totals": {"/mnt/storage": 4e12, "/": 120e9},
        "series": {
            "cpu": series(sec, step, lambda i: 25 + 20 * math.sin(i / 9) + (i % 7)),
            "mem": series(sec, step, lambda i: 48 + 6 * math.sin(i / 17)),
            "load": series(sec, step, lambda i: 1.2 + 0.8 * math.sin(i / 11) + 0.3),
            "net_rx": series(sec, step, lambda i: 4e6 * (1.2 + math.sin(i / 7))),
            "net_tx": series(sec, step, lambda i: 1.5e6 * (1.2 + math.cos(i / 5))),
            "disk:/mnt/storage": series(sec, step, lambda i: 2.1e12 + i * 4e9),
            "disk:/": series(sec, step, lambda i: 38e9 + i * 2e7),
            "containers": series(sec, step, lambda i: 22 if i % 40 else 21)},
        "forecast": [{"path": "/mnt/storage", "total": 4e12, "used": 2.9e12, "per_day": 3.1e10, "days": 35, "full_on": "2026-12-16", "reliable": True, "r2": .93, "span_days": 14},
                     {"path": "/", "total": 120e9, "used": 40e9, "per_day": 0, "days": None, "full_on": "", "reliable": False, "r2": 0, "span_days": 1}]}
STATE = {"import_scanned": True}
# role « observer » : le serveur de maquette refuse comme le vrai serveur ce qui n'est pas de la lecture
OBSERVER_GET = {"/api/session", "/api/status", "/api/system", "/api/system/containers", "/api/alerts", "/api/appliance", "/api/metrics", "/api/notifications", "/api/prefs", "/api/report", "/api/vacation", "/api/push", "/api/house"}
REPORT = {"generated": now - 3600, "sent": now - 3500, "level": "warning", "title": "Rapport hebdomadaire : points à regarder",
          "text": "seedbox - semaine du 30/10/2026 au 06/11/2026\n\nÀ REGARDER\n- Services à l'arrêt ou en difficulté : Radarr\n- Stockage : 72 % utilisé\n\nServices : 4 / 6 fonctionnent\nDisque Stockage : 72 % (2,9 To sur 4 To)\nSauvegardes : 1 (dernière : 06/11/2026), copie hors serveur oui\nVersion : 2026.11.10-0945 (mise à jour disponible)\nIncidents cette semaine : 2 (Radarr ne répond plus)\nInvitations actives : 1"}
def route(p, q, role="admin", PREFS=None):
    PREFS = {} if PREFS is None else PREFS
    if p == "/api/session":
        return {"user": "vincent", "version": "2026.11.11-1600", "role": role, "readonly": role != "admin", "prefs": PREFS}
    if p == "/api/prefs": return PREFS
    if p == "/api/notifications": return {"now": now, "seen": PREFS.get("notif_seen", 0), "items": [
        {"id": "a-radarr", "t": now - 720, "level": "critical", "title": "Radarr ne répond plus", "detail": "depuis 12 minutes", "active": True, "view": "alerts"},
        {"id": "e-1", "t": now - 3600, "level": "ok", "title": "Sauvegarde : terminé", "detail": "", "active": False, "view": ""},
        {"id": "e-2", "t": now - 86400, "level": "ok", "title": "Rétabli : Disque « Stockage » à 88 %", "detail": "", "active": False, "view": "alerts"}]}
    if p == "/api/assistant": return {"todo": 4, "total": 10, "secure": False, "items": [
        {"id": "deployed", "group": "Base", "title": "Services déployés", "state": "ok", "detail": "Tous les services sont déployés.", "view": "apps"},
        {"id": "address", "group": "Base", "title": "Adresse d’accès", "state": "info", "detail": "Adresse automatique (IP du serveur) : un nom de domaine donne un certificat reconnu.", "view": "domain"},
        {"id": "accounts", "group": "Sécurité", "title": "Comptes personnels", "state": "ok", "detail": "3 comptes.", "view": "users"},
        {"id": "twofa", "group": "Sécurité", "title": "Double authentification", "state": "warn", "detail": "La double authentification est désactivée : un mot de passe volé suffit à entrer.", "view": "users"},
        {"id": "storage", "group": "Stockage", "title": "Stockage et partages", "state": "ok", "detail": "2 partage(s) configuré(s).", "view": "shares"},
        {"id": "backup", "group": "Données", "title": "Sauvegarde automatique", "state": "ok", "detail": "Sauvegarde automatique activée.", "view": "backup"},
        {"id": "offsite", "group": "Données", "title": "Copie hors serveur", "state": "ok", "detail": "Copie hors serveur configurée.", "view": "backup"},
        {"id": "alerts", "group": "Exploitation", "title": "Alertes", "state": "warn", "detail": "Aucun canal d’alerte : un service en panne ne vous préviendra pas.", "view": "alerts"},
        {"id": "updates", "group": "Exploitation", "title": "Mises à jour", "state": "info", "detail": "Mise à jour de l’appliance manuelle : activez-la ou vérifiez régulièrement.", "view": "settings"},
        {"id": "report", "group": "Exploitation", "title": "Rapport hebdomadaire", "state": "unknown", "detail": "Contrôle impossible : exemple.", "view": "report"}]}
    if p == "/api/report": return {"settings": {"enabled": True, "day": 0, "send": True}, "days": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"], "last": REPORT, "channels": ["ntfy"]}
    if p == "/api/resources": return {"now": now, "settings": {"enabled": True, "memory_percent": 90, "memory_samples": 3, "leak_mb": 512, "leak_factor": 2.0, "cpu_percent": 90, "cpu_samples": 6, "ignore_cpu": ["jellyfin"]}, "containers": [
        {"name": "jellyfin", "cpu": 42, "cpu_max": 88, "mem": 1.4e9, "mem_max": 2.1e9, "mem_avg": 1.2e9, "samples": 288, "spark": [40, 42, 45, 44, 50, 58, 61, 66, 70, 74], "problems": [{"code": "leak", "detail": "mémoire 1400 Mo, 2.3 fois sa valeur habituelle"}]},
        {"name": "radarr", "cpu": 3, "cpu_max": 11, "mem": 3.2e8, "mem_max": 3.4e8, "mem_avg": 3.1e8, "samples": 288, "spark": [30, 31, 30, 31, 32, 31, 31, 32, 31, 31], "problems": []},
        {"name": "qbittorrent", "cpu": 8, "cpu_max": 35, "mem": 9e8, "mem_max": 1e9, "mem_avg": 8.5e8, "samples": 288, "spark": [50, 52, 51, 55, 54, 53, 56, 55, 54, 55], "problems": []}]}
    if p == "/api/window": return {"settings": {"enabled": True, "days": [0, 3], "start": "03:00", "end": "05:00"}, "open": False, "next_open": now + 86400, "pending": ["vulnscan"], "tasks": ["appliance", "autoupdate", "restoretest", "vulnscan"]}
    if p == "/api/activity": return {"settings": {"enabled": True, "show_playing": True}, "enabled": True, "now": now, "people": [
        {"login": "marie", "role": "user", "jellyfin": True, "last_login": now - 3600, "last_activity": now - 600, "playing": True, "requests": 14, "quota": {"movie": {"used": 2, "limit": 5, "days": 7}}, "inactive_days": 0},
        {"login": "paul", "role": "manager", "jellyfin": True, "last_login": now - 86400 * 41, "last_activity": now - 86400 * 40, "playing": False, "requests": 3, "quota": {}, "inactive_days": 40},
        {"login": "vincent", "role": "admin", "jellyfin": False, "last_login": 0, "last_activity": 0, "playing": False, "requests": None, "quota": {}, "inactive_days": None}]}
    if p == "/api/vulns": return {"settings": {"enabled": True}, "scanned": now - 86400 * 2, "error": "", "totals": {"critical": 1, "high": 3}, "images": [
        {"image": "lscr.io/linuxserver/radarr:5.14", "containers": ["radarr"], "critical": 1, "high": 2, "unfixed": 14, "top": [{"id": "CVE-2026-1111", "package": "libssl3", "installed": "3.0.1", "fixed": "3.0.2", "severity": "critical"}]},
        {"image": "lscr.io/linuxserver/sonarr:4.0", "containers": ["sonarr"], "critical": 0, "high": 1, "unfixed": 3, "top": []},
        {"image": "ghcr.io/example/old:1", "containers": ["old"], "error": "image introuvable"}]}
    if p == "/api/netcheck": return {"at": now, "problems": 3, "errors": 1, "items": [
        {"id": "internet", "title": "Accès à Internet", "state": "ok", "detail": "Le serveur joint Internet.", "hint": ""},
        {"id": "dns", "title": "Résolution des noms", "state": "ok", "detail": "« releases.ubuntu.com » se résout.", "hint": ""},
        {"id": "certificate", "title": "Certificat HTTPS", "state": "warn", "detail": "Le certificat expire dans 9 jours (R3).", "hint": "Un renouvellement automatique est attendu ; vérifiez que les ports 80 et 443 sont joignables."},
        {"id": "port", "title": "Port entrant", "state": "warn", "detail": "Aucun port entrant annoncé.", "hint": "Le fournisseur doit accepter la redirection de port (NAT-PMP pour Proton)."},
        {"id": "vpn", "title": "Tunnel VPN", "state": "error", "detail": "Le tunnel n'est pas en marche (état : exited).", "hint": "Vérifiez le fournisseur et les identifiants VPN ; arrêtez qBittorrent tant que ce n'est pas réglé."}]}
    if p == "/api/smart": return {"available": True, "reason": "", "last": now - 1200, "every_minutes": 55, "disks": [
        {"name": "sda", "model": "WDC WD40EFRX", "level": "warning", "notes": ["Secteurs réalloués : +3 sur 30 jours (total 8) : la surface se dégrade."], "values": {"realloc": 8, "pending": 0, "temp": 41, "hours": 21000}, "peak_temp_24h": 43, "realloc_growth_30d": 3,
         "series": {"temp": [[now - 3600 * (40 - i), 38 + (i % 6)] for i in range(40)], "realloc": []}},
        {"name": "nvme0n1", "model": "Samsung 970", "level": "ok", "notes": [], "values": {"temp": 36, "wear": 6, "uncorrect": 0}, "peak_temp_24h": 38, "realloc_growth_30d": 0,
         "series": {"temp": [[now - 3600 * (40 - i), 34 + (i % 4)] for i in range(40)], "realloc": []}}]}
    if p == "/api/bandwidth": return {"settings": {"enabled": True, "default_down": 0, "default_up": 5000, "slots": [{"days": [0, 1, 2, 3, 4], "start": "18:00", "end": "23:00", "down": 0, "up": 1000}]}, "max_slots": 8, "qbittorrent": True,
        "now": {"down": 0, "up": 5000, "slot": None}, "applied": True, "since": now - 7200, "next": {"at": now + 3 * 3600, "down": 0, "up": 1000}}
    if p == "/api/ratio": return {"qbittorrent": True, "settings": {"goals": {"tracker-a": 1.0, "tracker-b": 1.0}, "margin": 10, "min_gb": 5}, "trackers": [
        {"tracker": "tracker-a", "torrents": 214, "uploaded": 4.2e12, "downloaded": 1.1e12, "ratio": 3.82, "goal": 1.0, "status": "ok", "missing": 0, "week_up": 8e10, "week_down": 1e10},
        {"tracker": "tracker-b", "torrents": 12, "uploaded": 3e10, "downloaded": 4e10, "ratio": 0.75, "goal": 1.0, "status": "below", "missing": 1e10, "week_up": 1e9, "week_down": 2e9},
        {"tracker": "tracker-c", "torrents": 2, "uploaded": 1e8, "downloaded": 2e8, "ratio": 0.5, "goal": None, "status": "none", "missing": 0, "week_up": 0, "week_down": 0}]}
    if p == "/api/rollout": return {"plan": {"batch": [{"service": "radarr", "running": True, "healthcheck": True}, {"service": "sonarr", "running": True, "healthcheck": False}], "apart": [{"service": "traefik", "running": True, "healthcheck": True}], "skipped": [{"service": "jackett", "reason": "épinglé"}]},
        "last": {"items": [{"service": "radarr", "result": "mis à jour", "detail": "", "seconds": 40}, {"service": "sonarr", "result": "retour arrière", "detail": "ne répond plus après la mise à jour", "seconds": 80}]}}
    if p == "/api/backupdiff": return {"current": "courant", "backups": [{"name": "seedbox-20261111-023000.tar.gz.enc", "where": "local", "time": now - 86400, "size": 1e8}, {"name": "seedbox-20261104-023000.tar.gz.enc", "where": "local", "time": now - 86400 * 8, "size": 9e7}]}
    if p == "/api/diagnose": return {"services": ["radarr", "sonarr", "traefik"]}
    if p == "/api/diagnose/run": return {"service": q.get("service", ["radarr"])[0], "level": "fail", "summary": "Redémarre en boucle : il s'arrête dès qu'il démarre.", "steps": [
        {"key": "state", "title": "État du conteneur", "level": "fail", "detail": "Redémarre en boucle."}, {"key": "disk", "title": "Espace disque", "level": "ok", "detail": "72 % utilisé."}],
        "actions": [{"id": "restart", "label": "Redémarrer", "why": "Règle les blocages passagers."}, {"id": "rollback", "label": "Revenir à la version précédente", "why": "La version a changé hier."}], "log": ["ligne 1", "ligne 2"]}
    if p == "/api/emergency": return {"configured": True, "created": now - 86400 * 30, "by": "vincent", "last_used": 0, "locked_seconds": 0, "fails": 0, "max_fails": 5, "lock_minutes": 15}
    if p == "/api/catalogexport": return {"settings": {"enabled": True, "days": 7, "keep": 4}, "retention": 4, "apps": {"radarr": True, "sonarr": True}, "exports": [{"name": "catalogue-20261110-030000.json", "created": now - 86400 * 5, "movies": 412, "series": 38, "bytes": 123456}]}
    if p == "/api/remote-control": return {"alerts": [{"title": "Radarr ne répond plus", "level": "critical"}], "alerts_total": 1, "vacation": {"active": False, "until": 0}, "paused_by_remote": 0,
        "downloads": {"available": True, "active": 3, "paused": 1, "down": 4e6, "up": 1e6}}
    if p == "/api/house": return {"at": now, "verdict": "warn", "services": {"total": 22, "running": 21, "down": [], "unhealthy": ["jellyfin"]},
        "alerts": [{"title": "Disque presque plein : Stockage", "level": "warning"}], "alerts_total": 1,
        "downloads": {"available": True, "active": 3, "paused": 1, "down": 4e6, "up": 1e6}, "vacation": False,
        "vpn": {"state": "healthy", "exit": "", "leak": False}, "backup": {"age_days": 1, "count": 7},
        "disk": {"label": "Stockage (médias, téléchargements)", "percent": 72, "free": 1.1e12}}
    if p == "/api/changelog": return {"files": 9, "keep": 20, "events": [
        {"id": "e2", "file": "alerts.json", "at": now - 1800, "by": "vincent", "action": "POST /api/alerts/settings", "undo": False, "latest": True},
        {"id": "e1", "file": "bandwidth.json", "at": now - 86400, "by": "hors console", "action": "", "undo": False, "latest": True},
        {"id": "e0", "file": "alerts.json", "at": now - 86400 * 3, "by": "vincent", "action": "Annulation de e-1", "undo": True, "latest": False}]}
    if p == "/api/changelog/detail": return {"id": "e2", "file": "alerts.json", "lines": ["-  \"disk_percent\": 90", "+  \"disk_percent\": 85", "   \"token\": \"[masqué:ab12]\""], "more": 0, "note": ""}
    if p == "/api/restoredrill": return {"services": ["radarr", "sonarr", "prowlarr"], "free_mb": 3200, "needed_mb": 1500,
        "backups": [{"name": "seedbox-20261111-023000.tar.gz.enc", "time": now - 86400}, {"name": "seedbox-20261110-023000.tar.gz.enc", "time": now - 86400 * 2}],
        "last": {"ok": False, "name": "seedbox-20261111-023000.tar.gz.enc", "at": now - 3600, "seconds": 142, "items": [
            {"service": "radarr", "started": True, "answers": True, "api": True, "count": 412, "label": "movies", "seconds": 40, "detail": ""},
            {"service": "sonarr", "started": True, "answers": False, "api": False, "count": None, "label": "series", "seconds": 150, "detail": "pas de réponse au bout de 150 s"}]}}
    if p == "/api/diskusage": return {"last": now - 7200, "running": False, "every_hours": 20, "top_growers": ["du.stockage-torrents"], "rows": [
        {"key": "du.stockage-torrents", "label": "Stockage : torrents", "size": 1.4e12, "per_day": 3.1e10, "span_days": 14, "share": 55.0, "spark": [1.2e12, 1.25e12, 1.3e12, 1.36e12, 1.4e12]},
        {"key": "du.media-films", "label": "Médias : films", "size": 8e11, "per_day": 2e9, "span_days": 14, "share": 31.4, "spark": [7.9e11, 7.92e11, 7.95e11, 7.98e11, 8e11]},
        {"key": "du.docker-images", "label": "Docker : images", "size": 9e9, "per_day": 0, "span_days": 14, "share": 0.4, "spark": [9e9, 9e9, 9e9]},
        {"key": "du.config-jellyfin", "label": "Configuration : jellyfin", "size": 3e9, "per_day": 0, "span_days": 0, "share": 0.1, "spark": [3e9]}]}
    if p == "/api/apppasswords": return {"apps": [
        {"id": "jellyfin", "name": "Jellyfin", "running": True, "typed": True, "accounts": ["vincent", "papa"]},
        {"id": "qbittorrent", "name": "qBittorrent", "running": True, "typed": False, "accounts": ["admin"]},
        {"id": "autobrr", "name": "autobrr", "running": False, "typed": False, "accounts": ["admin"]}]}
    if p == "/api/rotation": return {"old_after_days": 365, "items": [
        {"id": "radarr", "label": "Radarr", "present": True, "last": now - 86400 * 30, "by": "vincent", "age_days": 30, "running": True},
        {"id": "sonarr", "label": "Sonarr", "present": True, "last": 0, "by": "", "age_days": None, "running": True},
        {"id": "prowlarr", "label": "Prowlarr", "present": True, "last": now - 86400 * 400, "by": "vincent", "age_days": 400, "running": False},
        {"id": "console", "label": "Secret console ↔ Traefik", "present": True, "last": 0, "by": "", "age_days": None, "running": True}]}
    if p == "/api/mediaquality": return {"at": now, "errors": {}, "apps": {
        "radarr": {"app": "radarr", "total": 412, "with_file": 398, "below_cutoff": 14, "missing": 3, "low_rate": 2, "duplicates": 1, "resolutions": {"1080": 300, "720": 98},
            "below_sample": [{"id": 2, "title": "Alien", "year": 1979, "quality": "DVD"}], "missing_sample": [{"id": 3, "title": "Absent", "year": 2020}],
            "low_rate_sample": [{"id": 5, "title": "Heat", "year": 1995, "mb_per_min": 1.8, "size": 2e8}], "duplicates_sample": [{"title": "Dune", "year": 2021, "ids": [1, 9]}]},
        "sonarr": {"app": "sonarr", "total": 38, "with_file": 36, "below_cutoff": 6, "missing": 12, "below_sample": [{"id": 31, "title": "Severance", "season": 1, "episode": 2}],
            "missing_sample": [{"id": 40, "title": "Fargo", "season": 2, "episode": 1}]}}}
    if p == "/api/peoplereport": return {"settings": {"enabled": True, "day": 1}, "last": 0, "text": "", "people": [
        {"login": "marie", "role": "user", "days": 30, "requests": 4, "available": 3, "pending": 1, "declined": 0},
        {"login": "paul", "role": "manager", "days": 30, "requests": None}]}
    if p == "/api/titleshare": return {"max": 50, "shares": [{"id": "ab12cd34", "title": "Dune", "year": 2021, "kind": "movie", "files": 2, "created": now - 3600, "expires": now + 86400 * 6, "active": True}]}
    if p == "/api/titleshare/search": return {"results": [{"id": 12, "title": "Dune", "year": 2021}, {"id": 13, "title": "Dune : deuxième partie", "year": 2024}]}
    if p == "/api/vpnwatch": return {"settings": {"enabled": True, "auto_stop": False}, "mode": "vpn", "checked": now - 120, "exit": "203.0.113.5", "leak": False, "stopped": False,
        "history": [{"at": now - 86400, "title": "Adresse de sortie du VPN changée", "detail": "203.0.113.4 → 203.0.113.5"}]}
    if p == "/api/library": return {"at": now - 3600, "files": 412, "errors": {}, "scanning": False, "volumes": [
        {"volume": "local", "files": 300, "titles": 180, "size": 2.1e12, "mb_per_min": 58.2, "total": 4e12, "free": 1.1e12,
         "resolutions": [{"label": "1080p", "files": 200, "size": 1.4e12}, {"label": "4K", "files": 20, "size": 5e11}, {"label": "720p", "files": 80, "size": 2e11}],
         "codecs": [{"label": "x264 (AVC)", "files": 240, "size": 1.6e12}, {"label": "x265 (HEVC)", "files": 60, "size": 5e11}]},
        {"volume": "nas", "files": 112, "titles": 64, "size": 9e11, "mb_per_min": 31.5, "total": 2e13, "free": 1.6e13,
         "resolutions": [{"label": "720p", "files": 90, "size": 6e11}, {"label": "1080p", "files": 22, "size": 3e11}], "codecs": [{"label": "x265 (HEVC)", "files": 112, "size": 9e11}]}],
        "versions": [{"kind": "film", "title": "Dune", "year": 2021, "saving": 4e9, "files": [
            {"path": "/data/media/films/Dune/dune.mkv", "size": 9e9, "quality": "Bluray-1080p", "volume": "local", "app": "radarr"},
            {"path": "/data/media/films/Dune (2)/dune.mkv", "size": 5e9, "quality": "WEBDL-1080p", "volume": "local", "app": "radarr"}]}], "versions_total": 1, "versions_saving": 4e9}
    if p == "/api/repoint": return {"enabled": True, "plans": [{"hash": "h1", "name": "Film.2020", "size": 4 << 30, "save_new": "/shares/nas/movies"}], "skipped": [], "saving": 4 << 30, "note": "", "last": None}
    if p == "/api/seeddiag": return {"running": True, "at": now, "summary": {"connection": "firewalled", "port": 6881, "forwarded_port": 51413, "up_speed": 0, "torrents": 40, "states": {"stalledUP": 30}},
        "findings": [
            {"id": "port-forward", "severity": "critical", "title": "Le port d'écoute n'est pas le port ouvert par le VPN", "cause": "qBittorrent écoute sur le port 6881, mais le VPN a ouvert le 51413.",
             "fix": "Mettre le port d'écoute sur 51413.", "apply": {"label": "Utiliser le port 51413", "prefs": {"listen_port": 51413}}},
            {"id": "no-demand", "severity": "info", "title": "30 torrent(s) sur 40 n'ont personne à qui envoyer", "cause": "Aucun téléchargeur sur ces torrents.", "fix": "Ajoutez des torrents récents."}]}
    if p == "/api/jobs": return {"running": 1, "busy": True, "jobs": [
        {"id": "a1b2c3d4e5f6", "title": "Liens durs : toutes les copies cachées", "state": "running", "started": now - 95, "log": "[seedboxctl] 2/5 : Dune.mkv (1 reliée(s), 9000000000 octets récupérés)\n"},
        {"id": "a1b2c3d4e5f7", "title": "Relevé des bibliothèques", "state": "ok", "started": now - 4000, "log": ""}]}
    if p == "/api/copyaudit": return {"at": now - 7200, "walked": 1200, "total": 2, "saving": 9e9, "blocked": 3e9, "items": [
        {"download": "/mnt/storage/downloads/Dune.2021.1080p.mkv", "media": "/mnt/storage/media/films/Dune/dune.mkv", "size": 9e9, "title": "Dune", "same_disk": True},
        {"download": "/mnt/storage/downloads/Alien.mkv", "media": "/mnt/shares/nas/Films/Alien/alien.mkv", "size": 3e9, "title": "Alien", "same_disk": False}]}
    if p == "/api/shareprofile": return {"settings": {"mapping": {"radarr": {"nas": 2}, "sonarr": {}}, "auto": False}, "errors": {},
        "profiles": {"radarr": [{"id": 1, "name": "HD-1080p"}, {"id": 2, "name": "Archive 720p"}], "sonarr": [{"id": 1, "name": "HD-1080p"}]},
        "volumes": {"radarr": {"local": {"titles": 180, "wrong": 0, "wanted": None, "profiles": {"HD-1080p": 180}},
                               "nas": {"titles": 64, "wrong": 12, "wanted": 2, "profiles": {"HD-1080p": 12, "Archive 720p": 52}}},
                    "sonarr": {"nas": {"titles": 20, "wrong": 0, "wanted": None, "profiles": {"HD-1080p": 20}}}}}
    if p == "/api/relocate": return {"settings": {"idle_days": 365, "min_free_gb": 100}, "volumes": ["local", "nas"], "volume": "local", "history": True, "reliable": True,
        "total": 2, "reclaim": 5e9, "roots": {"local": ["radarr", "sonarr"], "nas": ["radarr"]}, "candidates": [
        {"app": "radarr", "id": 3, "title": "Metropolis", "size": 5e9, "reclaim": 5e9, "files": 1, "last_played": 0, "added": "2019-05-05"},
        {"app": "radarr", "id": 9, "title": "Nosferatu", "size": 3e9, "reclaim": 0, "files": 1, "last_played": now - 500 * 86400, "added": "2020-01-01"}]}
    if p == "/api/replacer":
        proposal = {"id": "radarr:2", "app": "radarr", "cid": 2, "title": "Alien", "volume": "nas", "reason": "sous le seuil", "gain": 130, "why": "résolution 720 → 1080 ; encodage plus récent (x265)",
                    "delta": 1e8, "cur": {"size": 2e9, "quality": "HDTV-720p", "resolution": 720, "codec": "h264", "path": "/shares/nas/Films/Alien/alien.mkv"},
                    "release": {"guid": "g1", "indexer_id": 3, "indexer": "Indexeur A", "title": "Alien.1979.1080p.BluRay.x265", "size": 2.1e9, "quality": "Bluray-1080p",
                                "resolution": 1080, "seeders": 14, "age": 3, "protocol": "torrent", "codec": "x265", "rejections": []}}
        late = dict(proposal, id="radarr:5", title="Heat", delta=4e9, volume="local", defer="budget de croissance dépassé", release=dict(proposal["release"], size=6e9))
        return {"settings": {"max_growth_pct": 10, "min_seeders": 2, "per_run": 10, "budget_gb": 50, "keep_free_gb": 100, "recycle_days": 14, "recycle_path": "/data/corbeille-medias", "budgets": {"nas": 200}},
                "at": now - 1800, "searched": 10, "errors": [], "free": {"local": 1.1e12, "nas": 1.6e13},
                "plan": {"selected": [proposal], "deferred": [late], "growth": 1e8, "peak": 2.1e9, "final": 1e8},
                "safety": {"apps": {"radarr": {"path": "/data/corbeille-medias", "days": 14, "ok": True}, "sonarr": {"path": "/data/corbeille-medias", "days": 14, "ok": True}}, "ok": True, "size": 2.5e9}}
    if p == "/api/mediaslim": return {"settings": {"window_start": 1, "window_end": 7, "keep_audio": ["fra", "eng"], "keep_subs": ["fra", "eng"], "crf": 24, "preset": "medium", "trash_days": 14, "include_linked": False},
        "running": False, "saved": 4e9, "queue": [
            {"id": "a1", "kind": "recompress", "path": "/data/media/films/Heat/heat.mkv", "title": "Heat", "state": "attente", "size": 12e9, "added": now - 3600},
            {"id": "a2", "kind": "tracks", "path": "/data/media/films/Dune/dune.mkv", "title": "Dune", "state": "fait", "size": 9e9, "saved": 4e9, "detail": "9100 Mo -> 5100 Mo", "finished": now - 86400}],
        "candidates": [{"kind": "recompress", "path": "/data/media/films/Gladiator/g.mkv", "title": "Gladiator", "volume": "local", "size": 11e9, "codec": "h264", "linked": False, "queued": False,
                        "estimate": 3.8e9, "potential": 3.8e9, "why": "Bluray-1080p en x264 : environ 3 Go de moins en x265"},
                       {"kind": "tracks", "path": "/data/media/films/Ran/r.mkv", "title": "Ran", "volume": "nas", "size": 6e9, "codec": "h264", "linked": True, "queued": False,
                        "estimate": 0, "potential": 5e8, "why": "5 pistes audio, dont 2 dans des langues non gardées"}]}
    if p == "/api/seedopt":
        row = lambda n, cls, **k: dict({"hash": f"{n:040x}", "name": f"Sample.Torrent.{n}.1080p", "tracker": "prive.exemple", "size": 12e9, "up7": 2e9, "up30": 9e9, "covered": 12, "class": cls, "age": 40.0,
                                        "seeds": 14, "leechers": 2, "ratio": 1.6, "state": "stalledUP", "category": "films", "protected": cls == "protégé"}, **k)
        private = {"tracker": "prive.exemple", "torrents": 120, "size": 1.2e12, "uploaded": 1.4e12, "downloaded": 1.0e12, "up7": 8e10, "up30": 2.9e11, "covered": 12, "ratio": 1.4, "goal": 1.0,
                   "efficiency": .241, "eta_days": 0, "covered_ok": True, "rule": {"min_ratio": 1.0, "min_hours": 72},
                   "classes": [{"class": "utile", "torrents": 70, "size": 8e11}, {"class": "rare", "torrents": 6, "size": 6e10}, {"class": "attendu mais muet", "torrents": 3, "size": 3e10},
                               {"class": "sans valeur", "torrents": 4, "size": 4e10}, {"class": "protégé", "torrents": 8, "size": 9e10}],
                   "buckets": [{"label": "moins de 7 jours", "torrents": 8, "size": 9e10, "up30": 4e10}, {"label": "7 à 30 jours", "torrents": 30, "size": 3e11, "up30": 1.5e11},
                               {"label": "1 à 3 mois", "torrents": 50, "size": 5e11, "up30": 8e10}, {"label": "plus de 3 mois", "torrents": 32, "size": 3e11, "up30": 2e10}],
                   "top": [row(1, "utile", up30=3e10), row(2, "rare", up30=2e10, seeds=1, leechers=4)],
                   "advice": ["3 torrent(s) ont des demandeurs dans l'essaim mais n'envoient rien : une réannonce peut les relancer.", "4 torrent(s) n'ont rien envoyé depuis la période d'inactivité et n'ont aucun demandeur : 40 Go de partage inutile, retirables sans risque pour les règles du tracker."]}
        public = dict(private, tracker="public.exemple", torrents=28, size=2e11, up7=1e9, up30=5e9, ratio=0.3, goal=None, eta_days=None, rule=None, efficiency=.023, classes=[{"class": "normal", "torrents": 28, "size": 2e11}],
                      top=[row(7, "normal", tracker="public.exemple", up30=1e9)], advice=["Rien à signaler."])
        return {"available": True, "torrents": 148, "history_days": 12, "settings": {"rules": {"prive.exemple": {"min_ratio": 1.0, "min_hours": 72}}, "idle_days": 30, "idle_pct": 1.0},
                "trackers": [private, public], "retirable_total": 4, "retirable_size": 4e10, "retirable_unique": 0,
                "retirable": [row(4, "sans valeur", up30=0, up7=0, all_linked=True, unique=0, age=120.0), row(5, "sans valeur", up30=0, up7=0, all_linked=False, unique=1.2e10, age=90.0)],
                "silent": [row(3, "attendu mais muet", up30=0, leechers=5)]}
    if p == "/api/llm": return {"settings": {"enabled": True, "url": "http://192.168.1.50:11434", "model": "llama3.1:8b", "timeout": 180, "send_titles": False, "send_trackers": False,
                                              "verify_tls": True, "temperature": 0.2, "has_token": False},
                                "last": {"topic": "seeds", "at": now - 600, "model": "llama3.1:8b", "prompt_chars": 900,
                                         "text": "Le tracker A rapporte le plus : 70 torrents utiles pour 290 Go envoyés en 30 jours. Trois torrents attendus mais muets méritent une réannonce."}}
    if p == "/api/network": return {"interfaces": [{"name": "enp1s0", "state": "up", "mac": "52:54:00:aa:bb:cc", "speed": "1000 Mb/s", "addresses": [{"cidr": "172.24.0.230/24", "dynamic": True}]}],
        "route": {"gateway": "172.24.0.1", "dev": "enp1s0"}, "dns": ["172.24.0.1"], "hostname": "seedbox", "timeout": 120, "pending": None,
        "configured": {"iface": "enp1s0", "mode": "dhcp", "address": "", "gateway": "", "dns": []}}
    if p == "/api/spacefore": return {"settings": {"warn_days": 30, "alert": True}, "volumes": [
        {"volume": "local", "free": 4.2e11, "total": 2e12, "points": 12, "slope_day": -1.5e10, "days_left": 28.0, "date": now + 86400 * 28, "level": "warn"},
        {"volume": "nas", "free": 3.1e12, "total": 8e12, "points": 12, "slope_day": 2e9, "days_left": None, "date": 0, "level": "ok"}]}
    if p == "/api/libreport": return {"settings": {"enabled": True, "day": 0, "hour": 8}, "week": {}, "preview": "Bibliothèque : 412 fichiers, 3 ajouts cette semaine, 1 titre douteux."}
    if p == "/api/dormant": return {"settings": {"unplayed_days": 180, "abandon_days": 365}, "at": now - 7200, "reliable": True, "errors": [], "unplayed_total": 2, "unplayed_size": 4.4e10,
        "unplayed": [{"app": "radarr", "id": 1, "title": "Alpha", "size": 3e10, "added": "2025-12-01"}, {"app": "sonarr", "id": 2, "title": "Bravo", "size": 1.4e10, "added": "2025-11-02"}],
        "abandoned_total": 1, "abandoned_size": 2e10,
        "abandoned": [{"app": "sonarr", "id": 3, "title": "Charlie", "ended": True, "have": 3, "total": 10, "size": 2e10, "last_added": "2024-05-01"}]}
    if p == "/api/integrity": return {"settings": {"enabled": True, "per_night": 50}, "running": False, "last_run": now - 86400, "checked": 300, "pending": 112, "total": 412, "unknown": 0,
        "problems": [{"app": "radarr", "id": 7, "file_id": 70, "title": "Delta", "reason": "durée tronquée", "at": now - 86400, "file": "/data/Films/Delta.mkv"}]}
    if p == "/api/renamer": return {"at": now - 3600, "total": 412, "complete": True, "cursor": 412, "titles_total": 1, "files_total": 2,
        "titles": [{"app": "sonarr", "id": 9, "title": "Echo", "files": [], "count": 2, "example": {"before": "echo s01e01.mkv", "after": "Echo - S01E01 - Pilote.mkv"}}]}
    if p == "/api/subsgap": return {"settings": {"langs": ["fra", "eng"]}, "total": 1, "analysed": 400, "files": 412,
        "titles": [{"app": "radarr", "id": 11, "title": "Foxtrot", "size": 8e9, "audio": ["jpn"], "subs": [], "missing": ["fra", "eng"]}]}
    if p == "/api/guestquota": return {"settings": {"default": 10, "enforce": True}, "available": True, "days": 7, "users": [
        {"name": "invite-a", "played": 12, "quota": 10, "own_quota": False, "over": True, "suspended": False, "blocked": False},
        {"name": "invite-b", "played": 3, "quota": 5, "own_quota": True, "over": False, "suspended": True, "blocked": False}]}
    if p == "/api/energy": return {"settings": {"watts_hdd": 6.0, "watts_ssd": 2.0, "watts_standby": 1.0, "price_kwh": 0.2}, "hdparm": True, "system_device": "sda", "volumes": [
        {"volume": "local", "device": "sda", "rotational": False, "total": 2e12, "free": 4.2e11, "used": 1.58e12, "idle": 0.7, "read": 3e9, "read_share": 0.1, "standby": 0, "watts": 2.0, "kwh_year": 17.5, "eur_year": 3.5, "eur_tb_year": 1.75, "eur_tb_used_year": 2.2},
        {"volume": "nas", "device": "sdb", "rotational": True, "total": 8e12, "free": 3.1e12, "used": 4.9e12, "idle": 0.8, "read": 1e9, "read_share": 0.05, "standby": 20, "watts": 3.0, "kwh_year": 26.0, "eur_year": 5.2, "eur_tb_year": 0.65, "eur_tb_used_year": 1.06}]}
    if p == "/api/vacation": return {"active": False, "until": 0, "started": 0, "by": "", "paused": 0, "max_days": 120, "qbittorrent": True}
    if p == "/api/push": return {"available": True, "key": "BP" + "A" * 85, "mine": 0, "total": 2}
    if p == "/api/messages-language": return {"notify": "fr", "choices": ["fr", "en"]}
    if p == "/api/metrics": return metrics(q.get("range", ["24h"])[0])
    if p == "/api/system": return {"uptime": 900000, "load": [1.1, 1.0, .9], "cpus": 4, "memory": {"total": 16e9, "used": 7e9, "swap_total": 0, "swap_used": 0}, "network": {"interface": "eth0", "rx": 1e12, "tx": 5e11}, "disks": [{"label": "Stockage", "path": "/mnt/storage", "used": 2.9e12, "total": 4e12, "percent": 72}], "smart": {"available": False, "reason": "VM", "disks": []}, "time": now}
    if p == "/api/system/containers": return {"containers": []}
    if p == "/api/system/sources": return {"containers": ["radarr", "sonarr", "authelia"], "host": ["ssh", "seedbox-console"]}
    if p == "/api/system/logs": return {"source": "c:radarr", "text": "ligne"}
    if p == "/api/gpu": return {"settings": {"mode": "auto"}, "modes": ["auto", "off", "vaapi", "qsv", "nvenc"], "devices": [{"name": "Intel UHD 630", "path": "/dev/dri/renderD128"}], "nvidia": {"smi": False, "runtime": False, "name": ""}, "effective": "qsv", "reason": "Intel détecté : Quick Sync", "active": True}
    if p == "/api/backup": return {"settings": {"enabled": True, "days": "daily", "time": "02:30", "keep": 7, "destination": ""}, "backups": [{"name": "seedbox-20261111-023000.tar.gz.enc", "where": "local", "size": 4e6, "time": now - 3600}], "local_dir": "/x", "destination_ok": True, "key_present": True}
    if p == "/api/offsite": return {"settings": {"enabled": True, "type": "sftp", "path": "seedbox", "keep": 14, "fields": {"host": "nas.local", "port": "22", "user": "backup"}},
        "types": {"sftp": {"label": "SFTP (autre serveur, NAS)", "fields": ["host", "port", "user"], "secrets": ["password", "private_key"]}, "s3": {"label": "Stockage compatible S3", "fields": ["provider", "endpoint", "region", "bucket", "access_key"], "secrets": ["secret_key"]}},
        "providers": ["AWS", "Wasabi", "Other"], "configured": True, "installed": True, "secrets_present": {"password": True},
        "last_push": {"name": "seedbox-20261111-023000.tar.gz.enc", "at": now - 3500, "size": 4e6, "ok": True, "error": ""},
        "last_test": {"ok": True, "name": "seedbox-x", "where": "distante", "files": 412, "databases": 9, "seconds": 3.1, "at": now - 86400 * 9, "error": ""}}
    if p == "/api/import": return {"scanned": now, "folder": "/mnt/storage/import", "targets": {"movies": "/shares/films", "tv": "/data/media/tv"}, "apps": [],
        "history": [], "items": [{"id": "arr:radarr", "type": "arr", "app": "radarr", "file": "Backups/radarr_backup_v5.zip", "size": 1e6, "date": now - 86400, "installed": True, "count": 1523, "folders": ["/home/old/media/movies"], "clients": ["qBit"], "valid": True},
        {"id": "arr:sonarr", "type": "arr", "app": "sonarr", "file": "Backups/sonarr_backup_v4.zip", "size": 1e6, "date": now - 86400, "installed": True, "count": 312, "folders": ["/home/old/media/tv"], "clients": [], "valid": True},
        {"id": "qbit", "type": "qbit", "app": "qbittorrent", "file": "qbit/BT_backup", "kind": "dir", "torrents": 842, "valid": True, "installed": True, "size": 0, "date": now - 86400},
        {"id": "arr:lidarr", "type": "arr", "app": "lidarr", "file": "Backups/lidarr_backup.zip", "size": 1, "date": now, "installed": False, "count": 0, "folders": [], "clients": [], "valid": True, "problem": "lidarr n'est pas installé sur ce serveur"}]}
    if p == "/api/guests": return {"invites": [{"id": "ab12cd34", "label": "Marie, cousine", "created": now - 86400, "expires": now + 86400 * 5, "max_uses": 1, "uses": 0, "accounts": [], "profile": {}, "by": "vincent", "status": "active"},
        {"id": "ef56ab78", "label": "Collègues", "created": now - 86400 * 9, "expires": now - 86400, "max_uses": 3, "uses": 2, "accounts": ["paul", "lea"], "profile": {}, "by": "vincent", "status": "expirée"}],
        "accounts": [{"login": "paul", "guest": True, "label": "Collègues", "expires": now + 86400 * 20, "created": now, "profile": {"account_days": 30, "max_sessions": 2, "bitrate_mbps": 8, "downloads": False, "seerr_movie_limit": 5, "seerr_movie_days": 7, "seerr_tv_limit": 2, "seerr_tv_days": 7}},
                     {"login": "marie", "guest": False, "label": "", "expires": 0, "created": 0, "profile": {"account_days": 0, "max_sessions": 0, "bitrate_mbps": 0, "downloads": True, "seerr_movie_limit": 0, "seerr_movie_days": 7, "seerr_tv_limit": 0, "seerr_tv_days": 7}}],
        "defaults": {"account_days": 0, "max_sessions": 0, "bitrate_mbps": 0, "downloads": True, "seerr_movie_limit": 0, "seerr_movie_days": 7, "seerr_tv_limit": 0, "seerr_tv_days": 7},
        "limits": {"account_days": [0, 365], "max_sessions": [0, 10], "bitrate_mbps": [0, 200], "seerr_movie_limit": [0, 100], "seerr_movie_days": [1, 90], "seerr_tv_limit": [0, 100], "seerr_tv_days": [1, 90]}, "history": ["2026-11-10 invitation créée : Marie", "2026-11-10 compte paul créé"]}
    if p == "/api/space": return {"scanned": now - 900, "volumes": [
        {"label": "stockage", "path": "/mnt/storage", "total": 94.86e9, "used": 86.44e9, "free": 8.42e9, "percent": 91, "level": "warning",
         "items": [{"id": "downloads", "label": "Téléchargements", "path": "/mnt/storage/downloads", "size": 52e9, "hint": "Fichiers de qBittorrent : seeds en cours et téléchargements terminés.",
                    "children": [{"name": "radarr", "size": 38e9}, {"name": "sonarr", "size": 14e9}]},
                   {"id": "media", "label": "Médiathèque locale", "path": "/mnt/storage/media", "size": 21e9, "hint": "Films, séries, musique et livres rangés sur ce disque.", "children": [{"name": "films", "size": 17e9}]},
                   {"id": "docker-images", "label": "Images Docker", "path": "/var/lib/docker", "size": 6e9, "hint": "Géré par Docker (docker system df).", "children": [], "reclaimable": 3e9},
                   {"id": "trash", "label": "Corbeille du nettoyage", "path": "/mnt/storage/.corbeille", "size": 2e9, "hint": "Éléments mis de côté.", "children": []}],
         "other": 5.44e9, "biggest": [{"path": "downloads/radarr/Gros.Film.2020.2160p", "size": 25e9}, {"path": "media/films/Titre (2019)", "size": 12e9}],
         "remedies": [{"id": "cleanup-orphan", "title": "Supprimer les téléchargements orphelins", "why": "Fichiers que qBittorrent ne suit plus et qui n'ont pas de copie en médiathèque.", "reclaim": 12e9, "kind": "page", "view": "cleanup"},
                      {"id": "docker", "title": "Nettoyer Docker (images inutilisées et cache de construction)", "why": "Anciennes versions d'images laissées par les mises à jour.", "reclaim": 3e9, "kind": "fix", "fix": "docker"},
                      {"id": "trash-empty", "title": "Vider la corbeille du nettoyage", "why": "Ces éléments sont déjà mis de côté.", "reclaim": 2e9, "kind": "fix", "fix": "trash-empty"},
                      {"id": "media-share", "title": "Ranger la médiathèque sur un partage réseau", "why": "Définissez l'emplacement des films, séries, musique ou livres sur un partage : ils ne sont plus sur le disque local.", "reclaim": 21e9, "kind": "advice", "view": "shares"},
                      {"id": "seeds", "title": "Régler la durée et le ratio des seeds", "why": "Les torrents terminés restent en seed ; qbit_manage peut retirer ceux qui ont atteint leur objectif.", "reclaim": 15e9, "kind": "advice", "view": "seeds", "estimate": True},
                      {"id": "grow", "title": "Agrandir le disque", "why": "Agrandissez le disque virtuel dans votre hyperviseur, puis étendez la partition et le système de fichiers (growpart puis resize2fs, ou lvextend, selon votre disposition).", "reclaim": 0, "kind": "advice"}]}]}
    if p == "/api/cleanup": return {"scanned": now - 600, "notes": ["Analyse limitée aux 400 plus gros éléments par catégorie."], "total": 120e9, "categories": [
        {"id": "orphan", "label": "Téléchargements orphelins", "hint": "Plus suivis par qBittorrent et sans copie en bibliothèque", "trashable": True, "count": 2, "size": 90e9, "items": [
            {"id": "a1" * 6, "category": "orphan", "path": "/mnt/storage/downloads/Film.2019.2160p.mkv", "name": "Film.2019.2160p.mkv", "size": 60e9, "mtime": now - 86400 * 50, "reason": "torrent supprimé il y a 50 jours, aucun lien dur"},
            {"id": "b2" * 6, "category": "orphan", "path": "/mnt/storage/downloads/Serie.S01", "name": "Serie.S01", "size": 30e9, "mtime": now - 86400 * 40, "reason": "torrent supprimé il y a 40 jours"}]},
        {"id": "docker", "label": "Images Docker inutiles", "hint": "Anciennes versions d'images après une mise à jour", "trashable": False, "count": 1, "size": 30e9, "items": [
            {"id": "c3" * 6, "category": "docker", "path": "docker", "name": "14 image(s)", "size": 30e9, "mtime": now, "reason": "images sans conteneur"}]}],
        "trash": {"count": 1, "size": 4e9, "days": 14, "items": [{"id": "d4" * 6, "original": "/mnt/storage/downloads/vieux.iso", "trash": "/x", "size": 4e9, "at": now - 86400, "category": "orphan", "name": "vieux.iso"}]}}
    if p == "/api/audit": return {"total": 3, "users": ["vincent", "marie"], "sources": ["console", "tâche", "authelia", "ssh", "invitation", "système"], "events": [
        {"id": 3, "ts": now - 60, "source": "authelia", "user": "marie", "ip": "203.0.113.5", "action": "connexion (mot de passe)", "detail": "", "outcome": "refusé"},
        {"id": 2, "ts": now - 600, "source": "console", "user": "vincent", "ip": "192.168.1.20", "action": "Création de la sauvegarde", "detail": "label=avant-test", "outcome": "ok"},
        {"id": 1, "ts": now - 3600, "source": "ssh", "user": "vincent", "ip": "192.168.1.20", "action": "connexion SSH (publickey)", "detail": "", "outcome": "ok"}]}
    if p == "/api/audit/status": return {"chain": {"ok": True, "events": 3, "first_bad": None}, "keep_days": 400}
    if p == "/api/logs/search": return {"query": "", "since": "1h", "sources": 5, "total": 2, "truncated": False, "lines": [{"ts": now - 20, "source": "c:radarr", "line": "erreur apikey=*** sur le film"}, {"ts": now - 50, "source": "h:ssh", "line": "Failed password for root"}]}
    if p == "/api/appliance": return {"config": {"repo": "vincenttisseront/seedboxbuntu", "ref": "main", "channel": "stable", "guard": True, "require_signed": True, "auto": {"enabled": True, "days": "mon,thu", "time": "04:30"}},
        "token": True, "can_rollback": True, "pending": False, "signers": ["SHA256:abcdEFGH1234"], "blocked": {"commit": "c" * 40, "short": "ccccccc", "tag": "v1.4.0", "reason": "radarr ne répond plus", "at": "2026-11-10T04:35:00+0100"},
        "installed": {"commit": "a" * 40, "date": "2026-11-10T08:00:00Z", "version": "2026.11.10-0945", "source": "console", "message": "", "tag": "v1.3.2", "trust": "signed"},
        "check": {"checked": now, "available": True, "reason": "mise à jour disponible", "error": "", "channel": "stable", "blocked": False, "remote": {"commit": "b" * 40, "short": "bbbbbbb", "date": "2026-11-11T10:20:00Z", "message": "Version 1.5.0", "tag": "v1.5.0", "notes": "Garde-fou, copie hors serveur et invitations.", "verified": True, "assets": []}, "changes": ["Ajoute les invitations", "Ajoute la copie hors serveur"]},
        "status": {"state": "rolled_back", "action": "apply", "phase": "version précédente rétablie", "error": "ccccccc annulée : radarr ne répond plus"}, "log": "", "backups": ["x"]}
    if p == "/api/status": return {"deployed": True, "domain": "seedbox.local", "domain_mode": "auto", "acme": False, "addresses": ["192.168.1.50", "seedbox.local"], "apps": [
        {"id": "homepage", "name": "Tableau de bord", "path": "/", "service": "homepage", "login": "sso", "state": "running", "health": "healthy"},
        {"id": "jellyfin", "name": "Jellyfin", "path": "/jellyfin/", "service": "jellyfin", "login": "comptes", "state": "running", "health": "healthy"},
        {"id": "radarr", "name": "Radarr", "path": "/radarr/", "service": "radarr", "login": "sso", "state": "running", "health": "unhealthy"},
        {"id": "sonarr", "name": "Sonarr", "path": "/sonarr/", "service": "sonarr", "login": "sso", "state": "running", "health": "healthy"},
        {"id": "lidarr", "name": "Lidarr", "path": "/lidarr/", "service": "lidarr", "login": "sso", "state": "exited", "health": ""},
        {"id": "console", "name": "Console", "path": "/console/", "service": "console", "login": "sso", "state": "running", "health": "healthy"}]}
    if p == "/api/steps": return {"upgrade": {"pending": False}, "steps": [{"label": "Prowlarr ↔ Radarr", "ok": True, "ignored": False, "critical": True, "error": ""}, {"label": "Jackett", "ok": False, "ignored": False, "critical": False, "error": "délai dépassé"}]}
    if p == "/api/users": return {"users": [{"login": "vincent", "role": "admin"}, {"login": "marie", "role": "user"}, {"login": "paul", "role": "manager"}]}
    if p == "/api/trackers": return {"rules": {"tracker-a": {"name": "Tracker A", "min_seed_hours": 72, "max_ratio": 2}, "tracker-b": {"name": "Tracker B", "min_seed_hours": 0, "max_ratio": -1}},
        "stats": [{"tracker": "Tracker A", "torrents": 214, "uploaded": 4.2e12, "downloaded": 1.1e12, "ratio": 3.8, "upspeed": 2.4e6}, {"tracker": "Tracker B", "torrents": 12, "uploaded": 3e10, "downloaded": 4e10, "ratio": 0.75, "upspeed": 0}]}
    if p == "/api/shares/folders": return {"folders": ["Films", "Films/HD", "Séries"]}
    if p == "/api/shares": return {"warnings": ["Le partage « nas » n'est pas monté."], "nfs_versions": ["", "4.1", "4"], "smb_versions": ["", "3.0", "2.1"], "disks": [],
        "roles": [{"role": "downloads", "label": "Téléchargements", "share": "", "path": "", "location": "/mnt/storage/downloads", "local": True}, {"role": "movies", "label": "Films", "share": "nas", "path": "films", "location": "/mnt/nas/films", "local": False}],
        "shares": [{"name": "nas", "kind": "smb", "version": "3.0", "username": "seedbox", "source": "//192.168.1.10/torrents", "label": "", "fstype": "cifs", "mounted": False, "free": 0, "total": 0, "roles": ["movies"]},
                   {"name": "usb", "kind": "disk", "version": "", "username": "", "source": "", "label": "Archive", "fstype": "ext4", "mounted": True, "free": 8e11, "total": 2e12, "roles": []}]}
    if p == "/api/vpn": return {"enabled": False, "state": "", "provider": "", "ip": "", "port": 6881, "port_forwarding": False}
    if p == "/api/remote": return {"extra_hosts": "", "tailscale": {"enabled": False, "running": False, "state": "", "hostname": "seedbox", "ips": []}, "dns": {"providers": {"cloudflare": "Cloudflare", "ovh": "OVH"}, "provider": "", "custom_domain": "", "variables": []}}
    if p == "/api/subdomains": return {"enabled": True, "domain": "ar-systems.fr", "suggestion": "ar-systems.fr", "main": "vmdmz-seedbox01.ar-systems.fr",
        "portal": "vmdmz-seedbox01.ar-systems.fr", "target": "vmdmz-seedbox01.ar-systems.fr", "record": "CNAME", "tls": "local", "addresses": ["172.24.0.109"],
        "warnings": ["Certificat de l'autorité locale : installez-la sur vos appareils (page Adresse) pour éviter l'alerte du navigateur."],
        "apps": [{"id": "radarr", "label": "Radarr", "name": "radarr", "host": "radarr.ar-systems.fr", "enabled": True, "url": "https://radarr.ar-systems.fr/", "protected": True, "state": "ok", "ips": ["172.24.0.109"]},
                 {"id": "sonarr", "label": "Sonarr", "name": "sonarr", "host": "sonarr.ar-systems.fr", "enabled": True, "url": "https://sonarr.ar-systems.fr/", "protected": True, "state": "autre", "ips": ["10.9.9.9"]},
                 {"id": "seerr", "label": "Seerr", "name": "seerr", "host": "seerr.ar-systems.fr", "enabled": True, "url": "https://seerr.ar-systems.fr/", "protected": False, "state": "absent", "ips": []},
                 {"id": "dockge", "label": "Dockge", "name": "dockge", "host": "dockge.ar-systems.fr", "enabled": False, "url": "https://dockge.ar-systems.fr/", "protected": True}]}
    if p == "/api/bans": return {"active": True, "installed": True, "total_banned": 2, "me": "192.168.1.20", "me_ignored": False, "me_banned": True,
        "always": ["127.0.0.1/8", "::1"], "limits": {}, "settings": {"ban_minutes": 60, "window_minutes": 10, "retries": 5, "increment": True, "max_hours": 168, "ignore": ["192.168.1.0/24"]},
        "jails": [{"name": "sshd", "error": "", "failed": 2, "failed_total": 17, "banned_total": 9, "banned": [
            {"address": "192.168.1.20", "until": now + 1800, "seconds": 3600}, {"address": "203.0.113.7", "until": now + 86400, "seconds": 86400}]}],
        "recent": [{"at": "2026-11-16T16:05:01+01:00", "jail": "sshd", "action": "Ban", "address": "203.0.113.7"}, {"at": "2026-11-16T15:40:00+01:00", "jail": "sshd", "action": "Unban", "address": "192.0.2.9"}]}
    if p == "/api/aggregator": return {"enabled": True, "interval": 60, "retention": 7, "limit": 100, "category": "agregateur", "alert": True, "intervals": [15, 30, 60, 120, 180, 240, 360],
        "running": False, "last_sync": now - 1800, "next_sync": now + 1800, "qbittorrent": True, "problems": ["Jackett : 3 collectes en échec"],
        "totals": {"works": 5, "torrents": 9, "sent": 1, "day": 3, "week": 5}, "per_category": {"films": 2, "series": 1, "musique": 1},
        "timeline": [{"day": f"2026-11-{d:02d}", "indexed": 120 * (d - 4), "added": 30 + d, "purged": 10 * (d % 3)} for d in range(5, 12)],
        "sources": [{"id": "prowlarr", "name": "Prowlarr", "auto": True, "enabled": True, "present": True, "state": "ok", "last": now - 1800, "error": None, "collected": 240, "new": 18, "ms": 4200, "fails": 0, "torrents": 7, "url": "", "has_key": True},
                    {"id": "jackett", "name": "Jackett", "auto": True, "enabled": True, "present": True, "state": "error", "last": now - 1800, "error": "films : connexion refusée", "collected": 0, "new": 0, "ms": 100, "fails": 3, "torrents": 0, "url": "", "has_key": True},
                    {"id": "mon-indexeur", "name": "Mon indexeur", "auto": False, "enabled": False, "present": True, "state": "off", "last": None, "error": None, "collected": 0, "new": 0, "ms": 0, "fails": 0, "torrents": 2, "url": "https://indexeur.exemple/api?apikey=•••", "has_key": True}]}
    if p == "/api/aggregator/catalogue":
        def variant(i, title, tracker, bucket, seeders, label=None, sent=False):
            return {"id": i, "torrent_title": title, "label": label, "tracker": tracker, "bucket": bucket, "size": 4.2e9, "seeders": seeders, "leechers": 2, "grabs": 40, "published": now - 3600 * i, "sent": sent, "magnet": False, "link": True,
                    "details": "" if i in (4, 5, 6) else f"https://tracker-{tracker[-1].lower()}.exemple/torrent/{i}"}
        works = [{"id": 1, "title": "The Matrix", "category": "films", "label": "Films", "year": 1999, "imdb": "tt0133093", "tmdb": None, "latest": now - 3600, "seeders": 30, "leechers": 2, "grabs": 40, "count": 3,
                  "trackers": ["Tracker A", "Tracker B"], "qualities": ["4K", "FHD"], "seasons": None,
                  "groups": [{"bucket": "4K", "variants": [variant(2, "Matrix 1999 2160p UHD", "Tracker B", "4K", 5)]},
                             {"bucket": "FHD", "variants": [variant(1, "The.Matrix.1999.1080p.BluRay.x264-AA", "Tracker A", "FHD", 30, sent=True), variant(3, "The Matrix 1999 1080p", "Tracker B", "FHD", 9)]}]},
                 {"id": 2, "title": "Show Name", "category": "series", "label": "Séries", "year": None, "imdb": None, "tmdb": None, "latest": now - 7200, "seeders": 9, "leechers": 2, "grabs": 40, "count": 3,
                  "trackers": ["Tracker A"], "qualities": ["HD", "FHD"], "groups": None, "seasons": [
                      {"season": "2", "label": "Saison 2", "groups": [{"bucket": "HD", "variants": [variant(6, "Show.Name.S02E01.720p.WEB-DL-GRP", "Tracker A", "HD", 4, "S02E01")]}]},
                      {"season": "1", "label": "Saison 1", "groups": [{"bucket": "FHD", "variants": [variant(4, "Show.Name.S01E01.1080p.WEB-DL-GRP", "Tracker A", "FHD", 9, "S01E01"), variant(5, "Show.Name.S01E02.1080p.WEB-DL-GRP", "Tracker A", "FHD", 8, "S01E02")]}]}]},
                 {"id": 3, "title": "Artiste - Album", "category": "musique", "label": "Musique", "year": 2020, "imdb": None, "tmdb": None, "latest": now - 9000, "seeders": 7, "leechers": 2, "grabs": 40, "count": 1,
                  "trackers": ["Tracker C"], "qualities": ["AUDIO"], "seasons": None, "groups": [{"bucket": "AUDIO", "variants": [variant(7, "Artiste - Album (2020) [FLAC]", "Tracker C", "AUDIO", 7)]}]}]
        return {"works": works, "total": 3, "page": 1, "pages": 1, "per_page": 20, "period": (q.get("period") or ["24h"])[0], "category": (q.get("category") or [""])[0], "q": (q.get("q") or [""])[0],
                "sort": "publishedAt", "direction": "desc", "counts": {"films": 1, "series": 1, "musique": 1}, "periods": {"24h": 3, "week": 5, "all": 5}, "trackers": ["Tracker A", "Tracker B", "Tracker C"],
                "qualities": ["4K", "FHD", "HD", "SD", "AUDIO", "EBOOK", "GAME", "OTHER"], "categories": [{"id": c, "label": l} for c, l in (("films", "Films"), ("series", "Séries"), ("musique", "Musique"), ("jeux", "Jeux"), ("livres", "Livres"), ("presse", "Presse"), ("logiciels", "Logiciels"))]}
    if p == "/api/alerts": return {"settings": {"enabled": True, "disk_percent": 90, "repeat_hours": 24, "ntfy": {"url": "https://ntfy.sh", "topic": "xxxx"}}, "active": [
        {"level": "critical", "title": "Radarr ne répond plus", "detail": "depuis 12 minutes", "notified": True}, {"level": "warning", "title": "Disque « Stockage » à 88 %", "detail": "", "notified": False}]}
    if p == "/api/versions": return {"services": [{"service": "radarr", "version": "5.14", "created": "2026-11-01", "local": False, "pinned": False, "running": True, "previous": [{"id": "x", "version": "5.13", "created": "2026-10-20"}]}, {"service": "p2p-aggregator", "version": "1.8.2", "created": "2026-11-05", "local": True, "pinned": False, "running": True, "previous": []}]}
    if p == "/api/auth": return {"mode": "none", "users": [{"login": "vincent", "role": "admin", "second_factor": True}, {"login": "marie", "role": "user", "second_factor": False}], "notifications": ""}
    if p == "/api/security": return {"score": {"ok": 14, "warn": 1, "fail": 0}, "checks": [{"label": "Pare-feu actif", "state": "ok", "detail": ""}, {"label": "Connexion SSH par mot de passe", "state": "warn", "detail": "désactivez-la après avoir ajouté votre clé"}], "ports": {"base": {"ssh": 22, "web": [80, 443]}, "extra": {"tcp": [8080], "udp": [51413]}}}
    if p == "/api/credentials": return {"qBittorrent": {"identifiant": "admin", "mot de passe": "••••••••"}, "Radarr": {"clé API": "0123456789abcdef"}}
    if p == "/api/catalog": return {"fetched": now - 3600, "sources": {"linuxserver.io": "412 applications"}, "rejected": [], "catalog_url": "", "preset": "1080p", "packs": [{"id": "musique", "name": "Musique", "description": "Lidarr, Navidrome", "apps": ["lidarr", "navidrome"], "installed": 1}],
        "apps": [{"id": "lidarr", "name": "Lidarr", "category": "Musique", "source": "intégré", "description": "Gestion de la bibliothèque musicale", "installed": False}, {"id": "radarr", "name": "Radarr", "category": "Films", "source": "intégré", "description": "Gestion des films", "installed": True, "state": "running", "health": "healthy"}]}
    return {"apps": [], "deployed": True, "domain": "x", "addresses": [], "settings": {"security": {"days": "daily", "enabled": False, "time": "03:00"}, "apps": {"days": "daily", "enabled": False, "time": "03:00"}}, "scopes": {}, "next": {}, "reboot_required": False, "reboot_packages": [], "steps": [], "users": [], "role": "admin", "mode": "none", "notifications": "",
            "upgrade": {"pending": False}, "score": {"ok": 0, "warn": 0, "fail": 0}, "checks": [], "ports": {"base": {"ssh": 22, "web": [80, 443]}, "extra": {"tcp": [], "udp": []}}, "settings_": {}}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        try:
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass   # le navigateur a quitté la page avant la fin de la réponse
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); p = u.path
        if p.startswith("/invite/static/"):
            f = WEB / p[len("/invite/static/"):]
            types = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2"}
            return self.send(200, f.read_bytes(), types.get(f.suffix, "application/octet-stream")) if f.is_file() else self.send(404, {})
        if p == "/invite/api/info":
            return self.send(200, {"valid": True, "label": "Marie, cousine", "profile": {"account_days": 30, "max_sessions": 2, "bitrate_mbps": 8, "seerr_movie_limit": 5, "seerr_movie_days": 7}})
        if p.startswith("/me/static/"):
            f = WEB / p[len("/me/static/"):]
            types = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2"}
            return self.send(200, f.read_bytes(), types.get(f.suffix, "application/octet-stream")) if f.is_file() else self.send(404, {})
        if p == "/me/api/overview":
            return self.send(200, {"login": "marie", "role": "user", "expires": now + 86400 * 12, "days_left": 12, "limits": {"sessions": 2, "bitrate_mbps": 8, "downloads": False},
                                   "quota": {"movie": {"limit": 5, "used": 3, "remaining": 2, "days": 7}, "tv": None}, "requests_available": True, "month": {"days": 30, "requests": 4, "available": 3, "pending": 1, "declined": 0},
                                   "requests": [{"kind": "film", "title": "Alien", "status": "Disponible", "date": "2026-11-02"}, {"kind": "série", "title": "Severance", "status": "En attente d'approbation", "date": "2026-11-09"}]})
        if p in ("/me", "/me/"):
            return self.send(200, (WEB / "me.html").read_bytes(), "text/html; charset=utf-8")
        if p.startswith("/invite/"):
            return self.send(200, (WEB / "invite.html").read_bytes(), "text/html; charset=utf-8")
        if p.startswith("/errors/"):
            body = (WEB / "error.html").read_text().replace("{{status}}", "503").replace("{{title}}", "Service indisponible").replace("{{text}}", "Le service est arrêté ou en cours de redémarrage : réessayez dans une minute.")
            return self.send(200, body.encode(), "text/html; charset=utf-8")
        if p.startswith("/console/api/"):
            api = p[len("/console"):]
            if self.role == "observer" and api not in OBSERVER_GET:
                return self.send(403, {"error": "lecture seule : cette action est réservée aux administrateurs"})
            body = route(api, urllib.parse.parse_qs(u.query), self.role, self.prefs)
            if self.role == "observer" and api == "/api/alerts": body = {k: body[k] for k in ("active",)} | {"channels": []}
            return self.send(200, body)
        rel = p[len("/console/"):] or "index.html"
        f = WEB / rel
        if f.is_file():
            types = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".webmanifest": "application/json"}
            return self.send(200, f.read_bytes(), types.get(f.suffix, "application/octet-stream"))
        self.send(404, {})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        CALLS.append((self.path, body))
        if self.role == "observer" and not self.path.endswith(("/api/prefs", "/api/push/subscribe", "/api/push/unsubscribe")):
            return self.send(403, {"error": "lecture seule : cette action est réservée aux administrateurs"})
        if self.path.endswith("/api/prefs"): self.prefs.update(body); return self.send(200, self.prefs)
        if self.path.endswith("/api/report/build"): return self.send(200, REPORT)
        if self.path.endswith("/api/backup/check"): return self.send(200, {"ok": False, "name": "seedbox-20261111-023000.tar.gz.enc", "files": 412, "databases": 9, "configs": 31, "seconds": 2.4, "at": now,
            "problems": [{"file": "/var/lib/seedboxbuntu/guests.json", "problem": "JSON illisible"}], "changes": {"same": 380, "changed": 31, "added": 1, "sample": [{"path": "/etc/seedboxbuntu/alerts.json", "change": "changed"}]}})
        if self.path.endswith("/api/config/import"): return self.send(200, {"sections": [{"section": "alerts", "title": "Alertes", "state": "modifiée", "detail": ""}, {"section": "report", "title": "Rapport hebdomadaire", "state": "inchangée", "detail": ""}, {"section": "window", "title": "Fenêtre de maintenance", "state": "refusée", "detail": "heure HH:MM attendue"}]})
        if self.path.endswith("/api/backupdiff/compare"): return self.send(200, {"summary": {"added": 1, "removed": 0, "changed": 1, "binary": 1, "same": 30}, "omitted": 0, "older": "a", "newer": "b", "files": [
            {"path": "/etc/seedboxbuntu/alerts.json", "change": "changed", "kind": "texte", "diff": ["-  \"disk_percent\": 90", "+  \"disk_percent\": 85", "+  \"token\": \"[masqué:ab12]\""], "more": 0},
            {"path": "/etc/seedboxbuntu/bandwidth.json", "change": "added", "kind": "texte", "diff": [], "more": 0}, {"path": "/var/lib/authelia/db.sqlite3", "change": "changed", "kind": "binaire", "diff": [], "more": 0}]})
        if self.path.endswith("/api/emergency/init"): return self.send(200, {"code": "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ", "status": {"configured": True, "created": now, "by": "vincent", "last_used": 0, "locked_seconds": 0, "fails": 0}})
        if self.path.endswith("/api/catalogexport/plan"): return self.send(200, {"name": "catalogue-20261110-030000.json", "rejected": 0, "apps": {
            "radarr": {"ok": True, "error": "", "total": 412, "already": 400, "to_add": 12, "replaced": 2, "sample": [{"title": "Alien", "year": 1979, "notes": []}, {"title": "Heat", "year": 1995, "notes": ["profil remplacé"]}]},
            "sonarr": {"ok": True, "error": "", "total": 38, "already": 38, "to_add": 0, "replaced": 0, "sample": []}}})
        if self.path.endswith("/api/linkcheck/run"): return self.send(200, {"at": now, "problems": 2, "errors": 1, "items": [
            {"id": "prowlarr-apps", "title": "Prowlarr → Radarr, Sonarr", "state": "ok", "detail": "Les applications reliées répondent.", "hint": ""},
            {"id": "radarr-client", "title": "Radarr → client de téléchargement", "state": "error", "detail": "qBittorrent : Unable to connect", "hint": "Relancer la configuration des applications (Applications) relie à nouveau ce que la console provisionne."},
            {"id": "seerr-sonarr", "title": "Seerr → Sonarr", "state": "warn", "detail": "Seerr n'a aucun serveur Sonarr : les demandes ne partiront pas.", "hint": ""}]})
        if self.path.endswith(("/api/bans/unban", "/api/bans/unban-all", "/api/bans/ban", "/api/bans/ignore", "/api/bans/unignore", "/api/bans/settings")):
            return self.send(200, route("/api/bans", {}, self.role, self.prefs))
        if self.path.endswith("/api/aggregator/send"): return self.send(200, {"state": "envoye", "message": "envoyé à qBittorrent", "title": "Matrix 1999 2160p UHD"})
        if self.path.endswith("/api/aggregator/source/test"): return self.send(200, {"count": 12, "trackers": ["Tracker A", "Tracker B"], "sample": "Dune"})
        if self.path.endswith(("/api/aggregator/settings", "/api/aggregator/source/add", "/api/aggregator/source/remove", "/api/aggregator/source/toggle")): return self.send(200, route("/api/aggregator", {}, self.role, self.prefs))
        if self.path.endswith("/api/mediaquality/upgrade"): return self.send(200, {"searched": 14})
        if self.path.endswith("/api/changelog/undo"): return self.send(200, {"file": "alerts.json"})
        if self.path.endswith("/api/titleshare/create"): return self.send(200, {"id": "ab12cd34", "path": "/invite/" + "S" * 32, "expires": now + 86400 * 8, "title": "Dune"})
        if self.path.endswith(("/api/titleshare/revoke", "/api/peoplereport/settings", "/api/vpnwatch/settings")): return self.send(200, {"settings": body})
        if self.path.endswith("/api/remote-control/pause"): return self.send(200, {"paused": 3})
        if self.path.endswith("/api/remote-control/resume"): return self.send(200, {"resumed": 3})
        if self.path.endswith(("/api/bandwidth/settings", "/api/ratio/settings", "/api/catalogexport/settings")): return self.send(200, {"settings": body})
        if self.path.endswith("/api/push/unsubscribe"): return self.send(200, {"available": True, "key": "BP" + "A" * 85, "mine": 0, "total": 1})
        if self.path.endswith("/guests/invite"): return self.send(200, {"id": "ab12", "token": "T" * 32, "path": "/invite/" + "T" * 32, "expires": now + 86400 * 7})
        if self.path.endswith("/api/copyaudit/relink") and body.get("all"): return self.send(200, {"job": "j1"})
        if self.path.endswith("/api/copyaudit/relink"): return self.send(200, {"done": 1, "saved": 9e9, "skipped": []})
        if self.path.endswith("/api/replacer/apply"): return self.send(200, {"done": 1, "skipped": []})
        if self.path.endswith("/api/replacer/safety"): return self.send(200, {"ok": True})
        if self.path.endswith("/api/mediaslim/add"): return self.send(200, {"added": 1})
        if self.path.endswith(("/api/mediaslim/remove", "/api/mediaslim/clear")): return self.send(200, {"removed": 1})
        if self.path.endswith("/api/shareprofile/apply"): return self.send(200, {"changed": {"radarr": 12, "sonarr": 0}})
        if self.path.endswith("/api/relocate/move"): return self.send(200, {"moved": 1, "bytes": 5e9, "root": "/shares/nas/Films"})
        if self.path.endswith("/api/seedopt/reannounce"): return self.send(200, {"done": 1})
        if self.path.endswith("/api/seedopt/retire"): return self.send(200, {"done": 1, "bytes": 1e10})
        if self.path.endswith("/api/llm/certificate"): return self.send(200, {"url": "https://ia.exemple:11434", "host": "ia.exemple:11434", "sha256": "ab" * 32, "self_signed": True, "pinned": False,
            "subject": "CN=ia.exemple", "issuer": "CN=ia.exemple", "not_before": "Nov  1 00:00:00 2026 GMT", "not_after": "Nov  1 00:00:00 2027 GMT", "names": ["ia.exemple"]})
        if self.path.endswith("/api/seeddiag/apply"): return self.send(200, {"applied": "Utiliser le port 51413"})
        if self.path.endswith("/api/llm/certificate/trust"): return self.send(200, {"settings": {}})
        if self.path.endswith("/api/llm/test"): return self.send(200, {"ok": True, "version": "0.5.1", "models": [{"name": "llama3.1:8b", "size": 4e9}, {"name": "mistral:latest", "size": 3e9}], "model_ok": True, "ms": 120})
        if self.path.endswith(("/api/replacer/settings", "/api/mediaslim/settings", "/api/shareprofile/set", "/api/shareprofile/unset", "/api/shareprofile/auto", "/api/relocate/settings",
                               "/api/seedopt/rule", "/api/seedopt/unrule", "/api/seedopt/thresholds", "/api/llm/settings")): return self.send(200, {"settings": body})
        if self.path.endswith(("/api/spacefore/settings", "/api/libreport/settings", "/api/dormant/settings", "/api/integrity/settings", "/api/subsgap/settings", "/api/guestquota/settings",
                               "/api/energy/settings")): return self.send(200, {"settings": body})
        if self.path.endswith("/api/network/apply"): return self.send(200, {"pending": {"seconds_left": 120, "deadline": now + 120, "new": {}}, "address": body.get("address", "").split("/")[0], "mode": body.get("mode", "")})
        if self.path.endswith(("/api/network/confirm", "/api/network/revert")): return self.send(200, {"kept": True})
        if self.path.endswith("/api/shares/discover"):
            if body.get("host") == "absent": return self.send(400, {"error": "serveur injoignable : adresse, réseau ou service SMB arrêté"})
            return self.send(200, {"shares": [{"name": "torrents", "comment": "Téléchargements", "source": "//%s/torrents" % body.get("host")}, {"name": "Films 4K", "comment": "", "source": "//%s/Films 4K" % body.get("host")}]})
        if self.path.endswith("/api/libreport/send"): return self.send(200, {"sent": True})
        if self.path.endswith(("/api/dormant/unmonitor", "/api/renamer/apply")): return self.send(200, {"done": 1})
        if self.path.endswith(("/api/integrity/research", "/api/energy/standby", "/api/guestquota/quota", "/api/guestquota/resume")): return self.send(200, {"ok": True})
        if "/jobs/" in self.path: return self.send(200, {})
        self.send(200, {"job": "j1"})
def serve(port=8766, background=False, role="admin", prefs=None):
    handler = type("Handler", (H,), {"role": role, "prefs": dict(prefs or {})})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    if background:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    server.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8766)
