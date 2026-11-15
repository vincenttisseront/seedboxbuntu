"""Exploitation avancée : ressources par conteneur, fenêtre de maintenance, vérification d'une sauvegarde, activité,
langue des messages, vulnérabilités, diagnostic réseau, mode vacances, export/import des réglages (sans Docker)."""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbops"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


def raises(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (ctl.SeedboxError, ValueError) as error:
        return str(error) or "erreur"
    return None


for folder in (ctl.STATE, ctl.BASE, ctl.ETC, ctl.STORAGE):
    folder.mkdir(parents=True, exist_ok=True)
ctl.notifications().record = lambda *a, **k: None        # le centre de notifications est testé ailleurs
ctl.alerts().notify = lambda *a, **k: None               # aucun envoi réel

# --- 1. ressources par conteneur ------------------------------------------------------------------------------
metrics, resources = ctl.metrics(), ctl.resources()
MB = 1 << 20
NOW = 1_800_000_000
rows = [{"name": "web", "cpu": 200.0, "memory": 100 * MB, "memory_percent": 12.0}]
values = resources.sample(rows, cores=4)
check("ressources : cpu en part de la machine, mémoire, pourcentage", values == {"ctr.cpu:web": 50.0, "ctr.mem:web": 100 * MB, "ctr.memp:web": 12.0}, values)
real_sysinfo = ctl.sysinfo
ctl.sysinfo = lambda: (_ for _ in ()).throw(OSError("docker arrêté"))
check("ressources : Docker en panne -> aucune mesure", resources.sample() == {})
ctl.sysinfo = real_sysinfo
for i in range(36):
    last = i >= 33
    metrics.collect({"ctr.cpu:web": 95.0 if i >= 30 else 10.0, "ctr.mem:web": (1200 if last else 300) * MB, "ctr.memp:web": 95.0 if last else 20.0,
                     "ctr.cpu:calm": 5.0, "ctr.mem:calm": 200 * MB, "ctr.memp:calm": 10.0}, now=NOW - (36 - i) * 300)
problems = {code for code, _ in resources.drift("web", now=NOW)}
check("ressources : mémoire saturée, fuite probable et processeur détectés", problems == {"memory", "leak", "cpu"}, problems)
check("ressources : conteneur calme sans dérive", resources.drift("calm", now=NOW) == [])
overview = resources.overview(now=NOW)
check("ressources : page triée, dérives d'abord", [c["name"] for c in overview["containers"]] == ["web", "calm"] and overview["containers"][0]["problems"], overview["containers"][0]["name"])
found = resources.findings(now=NOW)
check("ressources : alertes ressource:memory:web, leak, cpu", {"ressource:memory:web", "ressource:leak:web", "ressource:cpu:web"} <= set(found), sorted(found))
resources.save({"ignore_cpu": ["web"]})
check("ressources : exception processeur respectée", "cpu" not in {c for c, _ in resources.drift("web", now=NOW)})
check("ressources : réglage inconnu et valeur folle refusés", raises(resources.save, {"x": 1}) and raises(resources.save, {"memory_percent": 0}) and raises(resources.save, {"ignore_cpu": ["a;b"]}))
resources.save({"enabled": False})
check("ressources : désactivé -> aucune alerte", resources.findings(now=NOW) == {})
check("métriques : séries des conteneurs absentes de l'historique machine", not any(k.startswith("ctr.") for k in metrics.history("24h", NOW)["series"]))

# --- 2. fenêtre de maintenance --------------------------------------------------------------------------------
mw = ctl.maintwindow()
at = lambda day, hour, minute=0: time.mktime((2026, 11, day, hour, minute, 0, 0, 0, -1))     # 16 novembre 2026 = lundi
check("fenêtre : désactivée = toujours ouverte", mw.is_open(at(16, 12)))
mw.save({"enabled": True, "days": [0], "start": "02:00", "end": "05:00"})
check("fenêtre : lundi 03:00 ouverte, 06:00 fermée, mardi 03:00 fermée", mw.is_open(at(16, 3)) and not mw.is_open(at(16, 6)) and not mw.is_open(at(17, 3)))
check("fenêtre : prochaine ouverture = lundi suivant 02:00", abs(mw.next_open(at(16, 6)) - at(23, 2)) <= 300, mw.next_open(at(16, 6)))
mw.save({"days": [0], "start": "23:00", "end": "05:00"})
check("fenêtre à cheval sur minuit : lundi 23:30 et mardi 02:00 ouvertes, mardi 23:30 et lundi 02:00 fermées",
      mw.is_open(at(16, 23, 30)) and mw.is_open(at(17, 2)) and not mw.is_open(at(17, 23, 30)) and not mw.is_open(at(16, 2)))
check("fenêtre : début = fin, jours vides, heure invalide refusés", raises(mw.save, {"start": "04:00", "end": "04:00"}) and raises(mw.save, {"days": []})
      and raises(mw.save, {"start": "25:00"}) and raises(mw.save, {"days": [9]}) and raises(mw.save, {"inconnu": 1}))
check("fenêtre : tâche inconnue refusée", raises(mw.allow, "rm-rf") is not None)
check("fenêtre : tâche hors fenêtre reportée", mw.allow("vulnscan", at(17, 12)) is False and mw.overview(at(17, 12))["pending"] == ["vulnscan"])
launched = []
check("fenêtre : rien n'est lancé tant qu'elle est fermée", mw.tick(at(17, 12), launched.append) == [] and not launched)
check("fenêtre : lancée à l'ouverture, une seule fois", mw.tick(at(17, 2), launched.append) == ["vulnscan"] and launched == ["vulnscan"] and mw.tick(at(17, 3), launched.append) == [])
check("fenêtre : tâche autorisée dans la plage", mw.allow("appliance", at(16, 23, 30)) is True)
mw.save({"enabled": False})

# --- 3. vérification d'une sauvegarde -------------------------------------------------------------------------
backup, sandbox = ctl.backup(), ctl.sandbox()
(ctl.BASE / ".env").write_text("A=1\n")
(ctl.BASE / "config").mkdir(exist_ok=True)
(ctl.BASE / "config/app.json").write_text('{"ok": true}')
good = sqlite3.connect(ctl.BASE / "config/good.db")
good.execute("CREATE TABLE t (x)")
good.commit()
good.close()
name = backup.create()
report = sandbox.check(name)
check("sauvegarde saine : verdict ok, fichiers et base comptés", report["ok"] and report["files"] >= 3 and report["databases"] == 1, {k: report[k] for k in ("ok", "files", "databases")})
check("sauvegarde saine : comparée au serveur (tout identique)", report["changes"]["changed"] == 0 and report["changes"]["same"] >= 3, report["changes"])
(ctl.BASE / "config/app.json").write_text('{"ok": false}')
check("après modification : une restauration remplacerait 1 fichier", sandbox.check(name)["changes"]["changed"] == 1)
time.sleep(1.1)
(ctl.BASE / "config/broken.db").write_bytes(os.urandom(2048))
(ctl.BASE / "config/bad.json").write_text("{pas du json")
broken = backup.create()
report = sandbox.check(broken)
files = {p["file"].rsplit("/", 1)[-1] for p in report["problems"]}
check("sauvegarde abîmée : base corrompue et JSON illisible signalés", not report["ok"] and {"broken.db", "bad.json"} <= files, files)
check("sauvegarde : mauvaise clé et nom inconnu refusés", raises(sandbox.check, name, "mauvaise-cle") and raises(sandbox.check, "../../etc/passwd"))
check("sauvegarde : rien n'a été restauré par la vérification", json.loads((ctl.BASE / "config/app.json").read_text()) == {"ok": False})

# --- 4. activité par personne ---------------------------------------------------------------------------------
activity = ctl.activity()
check("activité : désactivée par défaut, aucune donnée", activity.overview(NOW) == {"settings": activity.settings(), "enabled": False, "people": []} and activity.settings()["enabled"] is False)
activity.save({"enabled": True, "show_playing": True})


class Reply:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def jellyfin(method, path, *a, **k):
    if path == "/Sessions":
        return Reply([{"UserName": "Marie", "NowPlayingItem": {"Name": "Film secret"}}])
    return Reply([{"Name": "marie", "Id": "j1", "LastLoginDate": "2026-11-14T10:00:00.000Z", "LastActivityDate": "2026-11-14T11:00:00.000Z"},
                  {"Name": "paul", "Id": "j2", "LastLoginDate": "2026-09-01T10:00:00.000Z", "LastActivityDate": "2026-09-02T10:00:00.000Z"}])


ctl.jellyfin_api = jellyfin
ctl.user_list = lambda: [{"login": "marie", "role": "user"}, {"login": "paul", "role": "manager"}, {"login": "vincent", "role": "admin"}]
ctl.seerr_user_id = lambda jf: 7 if jf == "j1" else None
ctl.seerr_api = lambda method, path, *a, **k: Reply({"requestCount": 14, "movie": {"limit": 5, "used": 2, "days": 7}} if path.endswith("/7") or "quota" in path else {})
people = activity.overview(time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1)))["people"]
by = {p["login"]: p for p in people}
check("activité : tri par dernière activité, en lecture sans titre", [p["login"] for p in people][:2] == ["marie", "paul"] and by["marie"]["playing"] and "Film secret" not in json.dumps(people))
check("activité : inactivité en jours, demandes et quotas", by["paul"]["inactive_days"] > 60 and by["marie"]["requests"] == 14 and by["marie"]["quota"].get("movie", {}).get("limit") == 5, by["paul"]["inactive_days"])
check("activité : compte sans Jellyfin listé sans activité", by["vincent"]["last_activity"] == 0 and by["vincent"]["inactive_days"] is None)

# --- 5. langue des messages -----------------------------------------------------------------------------------
texts = ctl.texts()
check("langue : français par défaut, texte inchangé", texts.language() == "fr" and texts.tr("Sauvegarde échouée") == "Sauvegarde échouée")
texts.save("en")
check("langue : anglais, phrase exacte et modèle", texts.tr("Sauvegarde échouée") == "Backup failed" and texts.tr("Radarr : plein dans environ 12 jours") == "Radarr: full in about 12 days", texts.tr("Radarr : plein dans environ 12 jours"))
check("langue : phrase inconnue conservée, multi-lignes traduit ligne à ligne", texts.tr("Phrase jamais vue") == "Phrase jamais vue" and texts.tr("Sauvegarde échouée\nPhrase jamais vue") == "Backup failed\nPhrase jamais vue")
check("langue : valeur inconnue refusée", raises(texts.save, "de") is not None)
texts.save("fr")

# --- 6. vulnérabilités ----------------------------------------------------------------------------------------
vulnscan = ctl.vulnscan()
vulnscan.save({"enabled": True})
TRIVY = {"Results": [{"Vulnerabilities": [
    {"VulnerabilityID": "CVE-1", "PkgName": "libssl3", "InstalledVersion": "1", "FixedVersion": "2", "Severity": "CRITICAL"},
    {"VulnerabilityID": "CVE-2", "PkgName": "zlib", "InstalledVersion": "1", "FixedVersion": "1.1", "Severity": "HIGH"},
    {"VulnerabilityID": "CVE-3", "PkgName": "busybox", "InstalledVersion": "1", "Severity": "CRITICAL"},
    {"VulnerabilityID": "CVE-4", "PkgName": "x", "InstalledVersion": "1", "FixedVersion": "2", "Severity": "LOW"}]}]}
summary = vulnscan.summarize(TRIVY)
check("vulnérabilités : seules les failles corrigeables comptent, sans correctif à part", (summary["critical"], summary["high"], summary["unfixed"]) == (1, 1, 1) and summary["top"][0]["id"] == "CVE-1", summary)


def scanner(image):
    if "broken" in image:
        raise ctl.SeedboxError("image introuvable")
    return TRIVY if "radarr" in image else {}


result = vulnscan.scan({"lscr.io/radarr:5": ["radarr"], "ghcr.io/ok:1": ["ok"], "ghcr.io/broken:1": ["b"]}, scanner, NOW)
state = {i["image"]: i for i in result["images"]}
check("vulnérabilités : une image en échec n'arrête pas les autres", state["lscr.io/radarr:5"]["critical"] == 1 and state["ghcr.io/ok:1"]["critical"] == 0 and "error" in state["ghcr.io/broken:1"] and result["error"])
check("vulnérabilités : une alerte par image critique corrigeable", list(vulnscan.findings()) == ["vuln:lscr.io_radarr_5"], list(vulnscan.findings()))
check("vulnérabilités : résultat relu par la page", vulnscan.overview()["totals"]["critical"] == 1 and vulnscan.overview()["scanned"] == NOW)
vulnscan.save({"enabled": False})
check("vulnérabilités : désactivé -> ni alerte ni analyse automatique", vulnscan.findings() == {} and vulnscan.auto() is None)
vulnscan.save({"enabled": True})
current = time.localtime(time.time() + 7200)
mw.save({"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6], "start": time.strftime("%H:%M", current), "end": time.strftime("%H:%M", time.localtime(time.time() + 10800))})
check("vulnérabilités : analyse automatique reportée hors fenêtre", vulnscan.auto() is None and "vulnscan" in mw.overview()["pending"])
mw.save({"enabled": False})

# --- 7. diagnostic réseau -------------------------------------------------------------------------------------
netcheck = ctl.netcheck()
ctl.qbit_mode = lambda: True


class Probes(netcheck.Probes):
    def __init__(self, **over):
        self.over = over

    def tcp(self, host, port, timeout=4):
        if self.over.get("offline"):
            raise OSError("hors ligne")
        return True

    def resolve(self, name):
        return ["203.0.113.9"]

    def public_ip(self):
        return "203.0.113.9"

    def local_ips(self):
        return ["192.168.1.5"]

    def certificate(self, host):
        if self.over.get("cert_error"):
            raise OSError("pas de certificat")
        return self.over.get("days", 60), "Let's Encrypt"

    def ntp_synchronized(self):
        return self.over.get("ntp", True)

    def vpn(self):
        return self.over.get("vpn", {"enabled": False})

    def qbit_info(self):
        return {"status": "connected", "port": 51413}


env = {"DOMAIN_MODE": "custom", "DOMAIN": "seedbox.example.org"}
healthy = netcheck.run(Probes(), env)
ids = [i["id"] for i in healthy["items"]]
check("réseau : huit contrôles dans l'ordre", ids == ["internet", "dns", "domain", "certificate", "clock", "vpn", "port", "qbittorrent"], ids)
check("réseau : tout va bien -> aucun problème", healthy["problems"] == 0 and healthy["errors"] == 0, [(i["id"], i["state"]) for i in healthy["items"] if i["state"] != "ok"])
sick = netcheck.run(Probes(offline=True, days=5, ntp=False, vpn={"enabled": True, "state": "exited", "port_forwarding": True}), env)
states = {i["id"]: i["state"] for i in sick["items"]}
check("réseau : Internet coupé, certificat proche, horloge, VPN en panne détectés", states["internet"] == "error" and states["certificate"] == "warn" and states["clock"] == "warn" and states["vpn"] == "error", states)
check("réseau : chaque problème dit quoi faire", all(i["hint"] for i in sick["items"] if i["state"] in ("warn", "error")))
leak = netcheck.run(Probes(vpn={"enabled": True, "state": "running", "ip": "203.0.113.9", "port_forwarding": True, "port": 0}), env)
check("réseau : sortie VPN = adresse du serveur signalée comme fuite", {i["id"]: i["state"] for i in leak["items"]}["vpn"] == "error")
broken_cert = netcheck.run(Probes(cert_error=True), env)
check("réseau : un test en panne ne masque pas les autres", {i["id"]: i["state"] for i in broken_cert["items"]}["certificate"] == "unknown" and len(broken_cert["items"]) == 8)

# --- 8. mode vacances -----------------------------------------------------------------------------------------
vacation = ctl.vacation()


class FakeQbit:
    def __init__(self):
        self.torrents_list = [{"hash": "a" * 40, "state": "downloading"}, {"hash": "b" * 40, "state": "pausedDL"}, {"hash": "c" * 40, "state": "uploading"}]
        self.prefs = {"start_paused_enabled": False}
        self.resumed = []

    def torrents(self):
        return self.torrents_list

    def preferences(self):
        return self.prefs

    def pause(self, hashes):
        for t in self.torrents_list:
            if t["hash"] in hashes:
                t["state"] = "pausedDL"

    def resume(self, hashes):
        self.resumed = list(hashes)

    def set_preferences(self, values):
        self.prefs = {"start_paused_enabled": values.get("start_paused_enabled")}


T0 = time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1))
fake = FakeQbit()
check("vacances : date passée, absurde ou trop lointaine refusée", raises(vacation.start, "2020-01-01", "x", fake, T0) and raises(vacation.start, "demain", "x", fake, T0) and raises(vacation.start, "2027-12-31", "x", fake, T0))
vacation.start("2026-11-20", "vincent", fake, T0)
check("vacances : seuls les torrents actifs sont mis en pause, démarrage en pause demandé", set(vacation.state()["paused"]) == {"a" * 40, "c" * 40} and fake.prefs["start_paused_enabled"] is True, vacation.state()["paused"])
check("vacances : actif avant la date, terminé après", vacation.active(T0 + 86400) and not vacation.active(T0 + 6 * 86400) and vacation.overview(T0)["paused"] == 2)
check("vacances : second démarrage refusé", raises(vacation.start, "2026-11-25", "x", fake, T0) is not None)
check("vacances : rien ne se passe avant la date", vacation.tick(T0 + 86400, fake) is False and vacation.active(T0 + 86400))
check("vacances : fin à l'heure, seuls les torrents mis en pause reprennent, préférence rétablie",
      vacation.tick(T0 + 6 * 86400, fake) is True and sorted(fake.resumed) == ["a" * 40, "c" * 40] and fake.prefs["start_paused_enabled"] is False and not vacation.state())


class Failing(FakeQbit):
    def resume(self, hashes):
        raise ctl.SeedboxError("qBittorrent injoignable")


failing = Failing()
vacation.start("2026-11-20", "vincent", failing, T0)
check("vacances : reprise en échec -> le mode reste actif pour un nouvel essai", raises(vacation.tick, T0 + 6 * 86400, failing) is not None and vacation.state() != {})
vacation.stop(FakeQbit())
check("vacances : « reprendre » manuel", not vacation.state())
guests = ctl.guests()
vacation.start("2026-11-20", "vincent", FakeQbit(), time.time())
check("vacances : les liens d'invitation sont refusés pendant le mode", guests.lookup("a" * 32) is None)
vacation.stop(FakeQbit())

# --- 9. export et import des réglages --------------------------------------------------------------------------
bundle_module = ctl.configbundle()
alerts = ctl.alerts()
alerts.save({"enabled": True, "ntfy": {"url": "https://ntfy.sh", "topic": "tres-secret-topic", "token": "tk_secret"}})
mw.save({"enabled": True, "days": [1, 2], "start": "01:00", "end": "04:00"})
public = bundle_module.export(False)
check("export : format, version et sections attendues", public["format"] and public["version"] and {"alerts", "window", "report", "backup", "language", "ui_prefs"} <= set(public["sections"]), sorted(public["sections"]))
check("export : sans demande, aucun secret (jeton, sujet ntfy)", "tk_secret" not in json.dumps(public) and "tres-secret-topic" not in json.dumps(public))
check("export : avec demande, secrets inclus", "tk_secret" in json.dumps(bundle_module.export(True)))
plan = {r["section"]: r["state"] for r in bundle_module.plan(public)}
check("import : un fichier identique ne change rien", set(plan.values()) <= {"inchangée"}, plan)
edited = json.loads(json.dumps(public))
edited["sections"]["window"]["start"] = "03:30"
edited["sections"]["inconnue"] = {"x": 1}
plan = {r["section"]: r["state"] for r in bundle_module.plan(edited)}
check("import : modification et section inconnue reconnues, rien n'est écrit à blanc", plan["window"] == "modifiée" and plan["inconnue"] == "ignorée" and mw.settings()["start"] == "01:00", plan)
bundle_module.apply(edited, only=["report"])
check("import : seules les sections cochées sont appliquées", mw.settings()["start"] == "01:00")
rows = {r["section"]: r["state"] for r in bundle_module.apply(edited, only=["window"])}
check("import : section cochée appliquée", rows["window"] == "appliquée" and mw.settings()["start"] == "03:30")
edited["sections"]["window"]["start"] = "99:99"
rows = {r["section"]: r["state"] for r in bundle_module.apply(edited)}
check("import : valeur invalide refusée sans rien casser", rows["window"] == "refusée" and mw.settings()["start"] == "03:30", rows)
check("import : JSON, format, version et taille refusés", all(raises(bundle_module.parse, t) for t in ("pas du json", '{"format": "autre"}', json.dumps({**public, "version": 99}), "x" * 70000)))

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
