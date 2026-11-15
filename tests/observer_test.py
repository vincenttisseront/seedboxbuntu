"""Rôle observateur (lecture seule), notifications, préférences par compte, assistant et rapport hebdomadaire (sans Docker)."""
import http.client
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbobs"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/console"))
import seedboxctl as ctl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


ctl.STATE.mkdir(parents=True, exist_ok=True)
ctl.ETC.mkdir(parents=True, exist_ok=True)
notes = ctl.notifications()

# --- 1. événements et fil de notifications ----------------------------------------------------------------------------------------
notes.record("job", "Sauvegarde : terminé", "", "ok")
notes.record("job", "Sauvegarde : terminé", "", "ok")
check("événement : deux enregistrements identiques rapprochés comptent pour un", len(notes.recent()) == 1, notes.recent())
notes.record("alert", "Disque presque plein", "85 %", "warning", "alerts")
notes.record("alert", "Vue invalide", "", "info", "../x")
check("événement : vue invalide neutralisée", notes.recent()[0]["view"] == "")
for i in range(notes.MAX_EVENTS + 20):
    notes.record("job", f"Tâche {i}", "", "info")
check("événements : plafonnés", len(json.loads(notes.EVENTS_FILE.read_text())) == notes.MAX_EVENTS)
(ctl.STATE / "alerts-state.json").write_text(json.dumps({
    "disque": {"seen": 3, "notified": 100.0, "since": 90.0, "title": "Disque plein", "detail": "97 %", "level": "critical"},
    "attente": {"seen": 1, "notified": 0, "since": 95.0, "title": "Pas confirmée", "detail": "", "level": "warning"}}))
feed = notes.feed(now=1000.0)
active = [i for i in feed["items"] if i["active"]]
check("fil : alerte confirmée présente, alerte non confirmée absente", [i["title"] for i in active] == ["Disque plein"], active)
check("fil : alertes en cours en tête, puis événements du plus récent au plus ancien", feed["items"][0]["active"]
      and [i["t"] for i in feed["items"][1:]] == sorted((i["t"] for i in feed["items"][1:]), reverse=True))
check("fil : limité", len(notes.feed(limit=10)["items"]) == 10)

# --- 2. préférences par compte -------------------------------------------------------------------------------------------------------
notes.prefs_set("marie", {"lang": "en", "overview": [{"id": "health", "hidden": False}, {"id": "space", "hidden": True}], "notif_seen": 12.5})
notes.prefs_set("paul", {"lang": "fr"})
check("préférences : propres à chaque compte", notes.prefs_get("marie")["lang"] == "en" and notes.prefs_get("paul")["lang"] == "fr")
check("préférences : fusionnées", notes.prefs_get("marie")["overview"][1] == {"id": "space", "hidden": True})
for label, data in (("clé inconnue", {"admin": True}), ("langue inconnue", {"lang": "de"}), ("tuile invalide", {"overview": [{"id": "../x"}]}),
                    ("trop de tuiles", {"overview": [{"id": "health"}] * 17}), ("lecture négative", {"notif_seen": -1}), ("booléen en nombre", {"notif_seen": True}),
                    ("pas un objet", ["lang"])):
    try:
        notes.prefs_set("marie", data)
        check(f"préférences : {label} refusé", False)
    except ctl.SeedboxError:
        check(f"préférences : {label} refusé", True)
try:
    notes.prefs_set("../etc", {"lang": "fr"})
    check("préférences : compte invalide refusé", False)
except ctl.SeedboxError:
    check("préférences : compte invalide refusé", True)

# --- 3. rapport hebdomadaire -----------------------------------------------------------------------------------------------------------
import report as rp  # noqa: E402
import alerts as al  # noqa: E402
ctl.status = lambda: {"apps": [{"name": "Radarr", "state": "running", "health": ""}, {"name": "Sonarr", "state": "exited", "health": ""}]}
ctl.sysinfo().disks = lambda: [{"label": "Médias", "path": "/mnt/st", "percent": 96, "used": 960 * 1024 ** 3, "total": 1024 ** 4}]
ctl.metrics().forecast = lambda now=None: [{"path": "/mnt/st", "reliable": True, "days": 12.0}]
ctl.backup().listing = lambda: [{"name": "x", "where": "local", "size": 1, "time": time.time() - 20 * 86400}]
ctl.backup().settings = lambda: {"enabled": True}
ctl.offsite().overview = lambda: {"configured": True, "last_test": {"ok": False, "at": time.time() - 3600}}
ctl.appliance().overview = lambda: {"installed": {"version": "2026.11.15-1500"}, "check": {"available": True}, "config": {"auto": {"enabled": False}}}
ctl.guests().overview = lambda: {"invites": [{"status": "active", "expires": time.time() + 3600}, {"status": "revoked"}]}
report = rp.build()
levels = {text: level for level, text in report["problems"]}
check("rapport : disque critique et service arrêté signalés", any("96 %" in t and l == "critical" for t, l in levels.items()) and any("Sonarr" in t for t in levels), levels)
check("rapport : prévision de saturation signalée", any("plein dans environ 12" in t for t in levels), levels)
check("rapport : sauvegarde trop ancienne et test échoué signalés", any("moins de 8 jours" in t for t in levels) and any("test de restauration" in t for t in levels))
check("rapport : niveau global critique", report["level"] == "critical")
title, body = rp.render(report)
check("rapport : texte lisible (titre, services, disque, version, invitations)", "action nécessaire" in title and "1 / 2 fonctionnent" in body and "96 %" in body
      and "mise à jour disponible" in body and "Invitations actives : 1" in body, body)
check("rapport : aucun secret ni chemin interne superflu", "password" not in body.lower() and "token" not in body.lower())
ctl.backup().listing = lambda: []
ctl.metrics().forecast = lambda now=None: (_ for _ in ()).throw(ctl.SeedboxError("base absente"))
report = rp.build()
check("rapport : une section en panne n'empêche pas les autres", "error" in report["sections"]["disks"] and "total" in report["sections"]["services"], report["sections"]["disks"])
try:
    rp.save({"day": 9}); check("rapport : jour invalide refusé", False)
except ctl.SeedboxError:
    check("rapport : jour invalide refusé", True)
try:
    rp.save({"secret": 1}); check("rapport : réglage inconnu refusé", False)
except ctl.SeedboxError:
    check("rapport : réglage inconnu refusé", True)
sent = []
al.notify = lambda title, message, level="warning", force=False: sent.append((title, level, force)) or {"ntfy": None}
monday = time.mktime((2026, 11, 16, 9, 0, 0, 0, 0, -1))
rp.save({"enabled": True, "day": 0})
check("auto : n'envoie pas un autre jour", rp.auto(now=monday + 86400) is False and not sent)
check("auto : envoie le jour choisi", rp.auto(now=monday) is True and len(sent) == 1 and sent[0][2] is True, sent)
check("auto : une seule fois par jour", rp.auto(now=monday + 3600) is False and len(sent) == 1)
rp.save({"enabled": False})
check("auto : rien quand c'est désactivé", rp.auto(now=monday + 7 * 86400) is False)
al.notify = lambda title, message, level="warning", force=False: {}
try:
    rp.send(); check("envoi : sans canal, erreur explicite", False)
except ctl.SeedboxError as error:
    check("envoi : sans canal, erreur explicite", "aucun canal" in str(error))
check("rapport : dernier rapport conservé pour la console", rp.overview()["last"]["title"].startswith("Rapport hebdomadaire"))

# --- 4. assistant de première installation ----------------------------------------------------------------------------------------
import assistant as asst  # noqa: E402
ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret", "DOMAIN_MODE": "auto", "AUTH_2FA": "none", "DOMAIN": "10.0.0.2"}
ctl.user_list = lambda: [{"login": "admin", "role": "admin"}]
ctl.shares().overview = lambda: {"warnings": [], "shares": []}
ctl.backup().settings = lambda: {"enabled": False}
ctl.offsite().configured = lambda cfg=None: False
data = asst.checklist()
states = {i["id"]: i["state"] for i in data["items"]}
check("assistant : double authentification et sauvegarde signalées", states["twofa"] == "warn" and states["backup"] == "warn" and states["offsite"] == "warn", states)
check("assistant : points facultatifs en information", states["address"] == "info" and states["accounts"] == "info", states)
check("assistant : non sécurisé tant qu'un point sécurité ou données manque", data["secure"] is False and data["todo"] >= 3)
original_settings = ctl.alerts().settings
ctl.alerts().settings = lambda: (_ for _ in ()).throw(RuntimeError("panne"))
check("assistant : un contrôle en panne devient « inconnu »", {i["id"]: i["state"] for i in asst.checklist()["items"]}["alerts"] == "unknown")
ctl.alerts().settings = original_settings
check("assistant : chaque point mène à une page de la console", all(i["view"] for i in data["items"]))

# --- 5. console : rôle observateur (serveur réel) -----------------------------------------------------------------------------------
import server  # noqa: E402
ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret"}
ctl.alerts().overview = lambda: {"settings": {"ntfy": {"topic": "prive"}}, "active": [{"title": "x"}], "channels": ["ntfy"]}
ctl.appliance().overview = lambda: {"installed": {"version": "1"}, "check": None, "status": None, "config": {"repo": "x/y"},
                                    "log": "journal", "token": True, "signers": [], "backups": ["a"]}
ctl.sysinfo().overview = lambda: {"disks": []}
ctl.metrics().history = lambda range_name="24h", now=None: {"series": {}}
ctl.sysinfo().containers = lambda: []
ctl.version = lambda: "test"
ctl.notifications().recent = lambda limit=50: []
server.start_job = lambda title, argv, stdin=None: "abcdefabcdef"
console = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=console.serve_forever, daemon=True).start()
conn = http.client.HTTPConnection("127.0.0.1", console.server_port, timeout=20)


def call(path, groups, user="x", body=None, secret="secret"):
    headers = {"X-Seedbox-Proxy-Secret": secret, "Remote-Groups": groups, "Remote-User": user}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body)
    conn.request("POST" if body is not None else "GET", path, body=data, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode(errors="replace")


for path in sorted(server.OBSERVER_GET):
    status, _ = call(path, "observers", "obs1")
    check(f"observateur : lecture de {path}", status == 200, status)
for path in ("/api/credentials", "/api/users", "/api/backup", "/api/backup/key", "/api/audit", "/api/system/logs?source=x", "/api/logs/search",
             "/api/guests", "/api/shares", "/api/offsite", "/api/steps", "/api/auth", "/api/cleanup", "/api/assistant", "/api/remote", "/api/p2p",
             "/api/settings/updates", "/api/security", "/api/check", "/api/catalog"):
    check(f"observateur : {path} refusé", call(path, "observers", "obs1")[0] == 403)
for path in ("/api/deploy", "/api/users", "/api/backup/create", "/api/services/radarr/restart", "/api/report/send", "/api/report/settings",
             "/api/appliance/apply", "/api/cleanup/trash", "/api/shares", "/api/vpn/disable"):
    check(f"observateur : POST {path} refusé", call(path, "observers", "obs1", body={})[0] == 403)
status, alerts_view = call("/api/alerts", "observers", "obs1")
check("observateur : alertes sans réglages des canaux", "settings" not in alerts_view and alerts_view["active"], alerts_view)
status, app_view = call("/api/appliance", "observers", "obs1")
check("observateur : mise à jour sans dépôt, jeton ni journal", set(app_view) == {"installed", "check", "status"}, app_view)
status, admin_alerts = call("/api/alerts", "admins", "root")
check("administrateur : alertes complètes", "settings" in admin_alerts)
check("administrateur : réglages complets de l'appliance", "config" in call("/api/appliance", "admins", "root")[1])
check("administrateur : assistant disponible", call("/api/assistant", "admins", "root")[0] == 200)
session = call("/api/session", "observers", "obs1")[1]
check("session : rôle et lecture seule signalés", session["role"] == "observer" and session["readonly"] is True, session)
check("session : administrateur", call("/api/session", "admins", "root")[1]["readonly"] is False)
check("observateur : préférences modifiables", call("/api/prefs", "observers", "obs1", body={"lang": "en"})[0] == 200
      and call("/api/prefs", "observers", "obs1")[1]["lang"] == "en")
check("observateur : préférences d'un autre compte intouchées", call("/api/prefs", "admins", "root")[1] == {})
check("observateur : préférences invalides refusées", call("/api/prefs", "observers", "obs1", body={"admin": 1})[0] == 400)
check("sans groupe : refusé", call("/api/status", "users", "bob")[0] == 403 and call("/api/status", "", "bob")[0] == 403)
check("mauvais secret : refusé", call("/api/status", "admins", "root", secret="mauvais")[0] == 403)
# connexion persistante : le rôle d'une requête ne doit jamais servir à la suivante
call("/api/status", "admins", "root")
check("connexion persistante : l'observateur qui suit un administrateur reste limité", call("/api/credentials", "observers", "obs1")[0] == 403)
call("/api/status", "admins", "root")
check("connexion persistante : un compte sans groupe qui suit un administrateur est refusé", call("/api/status", "users", "bob")[0] == 403)
check("fichiers de la console accessibles à l'observateur", call("/index.html", "observers", "obs1")[0] == 200)
check("observateur : fichiers du serveur toujours cachés", call("/server.py", "observers", "obs1")[0] in (403, 404))

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
