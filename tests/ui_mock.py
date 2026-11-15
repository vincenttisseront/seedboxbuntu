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
OBSERVER_GET = {"/api/session", "/api/status", "/api/system", "/api/system/containers", "/api/alerts", "/api/appliance", "/api/metrics", "/api/notifications", "/api/prefs", "/api/report", "/api/vacation", "/api/push"}
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
    if p == "/api/shares": return {"warnings": ["Le partage « nas » n'est pas monté."], "nfs_versions": ["", "4.1", "4"], "smb_versions": ["", "3.0", "2.1"], "disks": [],
        "roles": [{"role": "downloads", "label": "Téléchargements", "share": "", "path": "", "location": "/mnt/storage/downloads", "local": True}, {"role": "movies", "label": "Films", "share": "nas", "path": "films", "location": "/mnt/nas/films", "local": False}],
        "shares": [{"name": "nas", "kind": "smb", "version": "3.0", "username": "seedbox", "source": "//192.168.1.10/torrents", "label": "", "fstype": "cifs", "mounted": False, "free": 0, "total": 0, "roles": ["movies"]},
                   {"name": "usb", "kind": "disk", "version": "", "username": "", "source": "", "label": "Archive", "fstype": "ext4", "mounted": True, "free": 8e11, "total": 2e12, "roles": []}]}
    if p == "/api/vpn": return {"enabled": False, "state": "", "provider": "", "ip": "", "port": 6881, "port_forwarding": False}
    if p == "/api/remote": return {"extra_hosts": "", "tailscale": {"enabled": False, "running": False, "state": "", "hostname": "seedbox", "ips": []}, "dns": {"providers": {"cloudflare": "Cloudflare", "ovh": "OVH"}, "provider": "", "custom_domain": "", "variables": []}}
    if p == "/api/p2p": return {"version": "1.8.2", "commit": "a1b2c3d", "update_available": False, "error": "", "token": True}
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
        if self.path.endswith("/api/push/unsubscribe"): return self.send(200, {"available": True, "key": "BP" + "A" * 85, "mine": 0, "total": 1})
        if self.path.endswith("/guests/invite"): return self.send(200, {"id": "ab12", "token": "T" * 32, "path": "/invite/" + "T" * 32, "expires": now + 86400 * 7})
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
