"""Dix nouveautés : santé SMART, débit par plages, objectifs de ratio, mises à jour par lots, comparaison de sauvegardes,
assistant de dépannage, accès de secours, export du catalogue, « Mon espace », télécommande (sans Docker)."""
import http.client
import http.server
import json
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbops2"))
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


def raises(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (ctl.SeedboxError, ValueError) as error:
        return str(error) or "erreur"
    return None


for folder in (ctl.STATE, ctl.BASE, ctl.ETC, ctl.STORAGE, ctl.CONFIG / "authelia"):
    folder.mkdir(parents=True, exist_ok=True)
ctl.notifications().record = lambda *a, **k: None
sent = []
ctl.alerts().notify = lambda title, message, level="warning", force=False: sent.append((title, message, level))
NOW = 1_800_000_000
metrics = ctl.metrics()

# --- 1. SMART : lecture, historique, tendance --------------------------------------------------------------------------------------
smart = ctl.smarthist()
ata = {"smart_status": {"passed": True}, "temperature": {"current": 38}, "power_on_time": {"hours": 1234},
       "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": 8}}, {"id": 197, "raw": {"value": 2}}, {"id": 198, "raw": {"value": 0}},
                                         {"id": 9, "raw": {"value": 99}}, {"id": 187, "raw": {"value": 0}}]}}
parsed = smart.parse(ata)
check("SMART : attributs ATA lus (réalloués, en attente, température)", parsed["realloc"] == 8 and parsed["pending"] == 2 and parsed["temp"] == 38 and parsed["passed"] == 1, parsed)
nvme = smart.parse({"smart_status": {"passed": True}, "temperature": {"current": 41}, "nvme_smart_health_information_log": {"percentage_used": 93, "media_errors": 0}})
check("SMART : NVMe (usure, erreurs) lu", nvme["wear"] == 93 and nvme["nvme"] == 1 and nvme["uncorrect"] == 0, nvme)
check("SMART : disque sans réponse SMART ignoré", smart.parse({"smart_status": {}}) == {})
check("SMART : compteur 48 bits tronqué à 16 bits", smart.parse({**ata, "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": (7 << 40) + 3}}]}})["realloc"] == 3)
smart.disk_names = lambda: ["sda", "nvme0n1"]
smart.read_disk = lambda name: ("Modèle " + name, smart.parse(ata) if name == "sda" else nvme)
values = smart.sample(NOW)
check("SMART : lecture -> clés smart.<disque>.<mesure>", values["smart.sda.realloc"] == 8 and values["smart.nvme0n1.wear"] == 93, sorted(values)[:3])
check("SMART : au plus une lecture par heure", smart.sample(NOW + 600) == {} and smart.sample(NOW + 3600) != {} and smart.sample(NOW + 3700, force=True) != {})
smart.disk_names = lambda: []
check("SMART : machine virtuelle -> raison affichée", smart.sample(NOW + 99999) == {} and "machine virtuelle" in smart.last_state()["reason"])
smart.disk_names = lambda: ["sda", "sdb", "sdc", "sdd"]
profiles = {
    "sda": lambda t, i: {"realloc": 0, "pending": 0, "temp": 35, "passed": 1},                              # sain
    "sdb": lambda t, i: {"realloc": 0 if i < 25 else 4, "pending": 0, "temp": 36, "passed": 1},             # dérive récente
    "sdc": lambda t, i: {"realloc": 120, "pending": 9, "temp": 62, "passed": 1},                            # critique
    "sdd": lambda t, i: {"realloc": 5, "pending": 0, "temp": 30, "passed": 1}}                              # stable
for index in range(30):
    stamp = NOW - (30 - index) * 86400
    row = {}
    for name, make in profiles.items():
        row.update({f"smart.{name}.{key}": value for key, value in make(stamp, index).items()})
    metrics.collect(row, now=stamp)
smart.last_state = lambda: {"time": NOW, "models": {"sda": "A", "sdb": "B", "sdc": "C", "sdd": "D"}, "reason": ""}
levels = {name: smart.judge(name, NOW)["level"] for name in profiles}
check("SMART : disque sain ok, dérive récente avertissement, disque en échec critique, stable ok",
      levels == {"sda": "ok", "sdb": "warning", "sdc": "critical", "sdd": "ok"}, levels)
check("SMART : la tendance (réalloués en hausse sur 30 jours) est expliquée", "+4" in " ".join(smart.judge("sdb", NOW)["notes"]), smart.judge("sdb", NOW)["notes"])
check("SMART : secteurs stables mentionnés sans alerte", any("stable" in n for n in smart.judge("sdd", NOW)["notes"]))
found = smart.findings(NOW)
check("SMART : alertes seulement pour les disques qui dérivent", set(found) == {"smart:sdb", "smart:sdc"} and found["smart:sdc"][2] == "critical", sorted(found))
check("SMART : séries pour le graphique", len(smart.judge("sdc", NOW)["series"]["temp"]) > 5)
check("SMART : clés cachées de l'historique machine", not any(k.startswith("smart.") for k in metrics.history("24h", NOW)["series"]))
view = smart.overview(NOW)
check("SMART : page (disques, modèle, niveau)", view["available"] and [d["name"] for d in view["disks"]] == ["nvme0n1", "sda", "sdb", "sdc", "sdd"][:0] + sorted(d["name"] for d in view["disks"]) and view["disks"][0]["model"] != "None")

# --- 2. débit par plages horaires --------------------------------------------------------------------------------------------------
bandwidth = ctl.bandwidth()


def at(text):
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))      # 2026-11-16 est un lundi


class FakeQbit:
    def __init__(self):
        self.prefs, self.sets, self.resumed, self.paused, self.items = {"dl_limit": 0, "up_limit": 500 * 1024}, [], [], [], []

    def preferences(self):
        return dict(self.prefs)

    def set_preferences(self, values):
        self.sets.append(values)
        self.prefs.update(values)

    def torrents(self):
        return self.items

    def pause(self, hashes):
        self.paused += list(hashes)

    def resume(self, hashes):
        self.resumed += list(hashes)


slots = [{"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "23:00", "down": 0, "up": 2000},
         {"days": [4, 5], "start": "23:00", "end": "06:00", "down": 5000, "up": 0}]
bandwidth.save({"enabled": True, "default_down": 100000, "default_up": 100000, "slots": slots})
cfg = bandwidth.settings()
check("débit : plage de jour (lundi 10:00)", bandwidth.limits(cfg, at("2026-11-16 10:00")) == (0, 2000, 0))
check("débit : hors plage (lundi 07:00) -> débit par défaut", bandwidth.limits(cfg, at("2026-11-16 07:00")) == (100000, 100000, None))
check("débit : plage qui passe minuit (vendredi 23:30)", bandwidth.limits(cfg, at("2026-11-20 23:30"))[2] == 1)
check("débit : suite de la plage le lendemain (samedi 03:00)", bandwidth.limits(cfg, at("2026-11-21 03:00"))[2] == 1)
check("débit : la nuit de dimanche à lundi n'est pas couverte (lundi 03:00)", bandwidth.limits(cfg, at("2026-11-16 03:00"))[2] is None)
fake = FakeQbit()
ctl.qbit_mode = lambda: "vpn"
check("débit : première application", bandwidth.tick(at("2026-11-16 10:00"), fake) is True and fake.prefs == {"dl_limit": 0, "up_limit": 2000 * 1024})
check("débit : aucun appel tant que la plage ne change pas", bandwidth.tick(at("2026-11-16 11:00"), fake) is False and len(fake.sets) == 1)
bandwidth.tick(at("2026-11-16 23:30"), fake)
check("débit : hors plage (lundi 23:30) -> défaut", fake.prefs == {"dl_limit": 100000 * 1024, "up_limit": 100000 * 1024}, fake.prefs)
check("débit : limites d'origine mémorisées une seule fois", bandwidth.state()["original"] == [0, 500 * 1024], bandwidth.state())
upcoming = bandwidth.next_change(bandwidth.settings(), at("2026-11-16 23:30"))
check("débit : prochain changement annoncé (mardi 08:00)", upcoming and upcoming["at"] == at("2026-11-17 08:00"), upcoming)
bandwidth.save({"enabled": False})
check("débit : désactivation -> limites d'origine rétablies", bandwidth.tick(at("2026-11-17 09:00"), fake) is True and fake.prefs == {"dl_limit": 0, "up_limit": 500 * 1024} and not bandwidth.state())
for label, data in (("plage sans jour", {"slots": [{"days": [], "start": "08:00", "end": "09:00", "down": 0, "up": 0}]}),
                    ("heure invalide", {"slots": [{"days": [0], "start": "25:00", "end": "09:00", "down": 0, "up": 0}]}),
                    ("début = fin", {"slots": [{"days": [0], "start": "08:00", "end": "08:00", "down": 0, "up": 0}]}),
                    ("débit négatif", {"slots": [{"days": [0], "start": "08:00", "end": "09:00", "down": -1, "up": 0}]}),
                    ("trop de plages", {"slots": [{"days": [0], "start": "08:00", "end": "09:00", "down": 0, "up": 0}] * 9}),
                    ("réglage inconnu", {"x": 1})):
    check(f"débit : {label} refusé", raises(bandwidth.save, data) is not None)

# --- 3. objectifs de ratio -----------------------------------------------------------------------------------------------------------
ratio = ctl.ratiogoals()
GB = 1 << 30
check("ratio : sous l'objectif, envoi manquant calculé", ratio.judge(50 * GB, 100 * GB, 1.0) == {"status": "below", "ratio": 0.5, "missing": 50 * GB})
check("ratio : proche (marge 10 %)", ratio.judge(105 * GB, 100 * GB, 1.0)["status"] == "near")
check("ratio : confortable", ratio.judge(150 * GB, 100 * GB, 1.0)["status"] == "ok")
check("ratio : trop peu de données reçues -> pas de verdict", ratio.judge(0, 1 * GB, 1.0)["status"] == "unknown")
check("ratio : sans objectif", ratio.judge(1, 1, None)["status"] == "none")
for label, data in (("tracker invalide", {"goals": {"../x": 1}}), ("ratio négatif", {"goals": {"a.org": -1}}), ("ratio absurde", {"goals": {"a.org": 99}}),
                    ("marge hors bornes", {"margin": 500}), ("réglage inconnu", {"zzz": 1})):
    check(f"ratio : {label} refusé", raises(ratio.save, data) is not None)
ratio.save({"goals": {"tracker-a.org": 1.0, "tracker-b.org": 1.5}, "margin": 10, "min_gb": 5})
ctl.tracker_stats = lambda: [{"tracker": "tracker-a.org", "torrents": 3, "uploaded": 40 * GB, "downloaded": 100 * GB, "upspeed": 5},
                             {"tracker": "tracker-b.org", "torrents": 1, "uploaded": 400 * GB, "downloaded": 100 * GB, "upspeed": 0},
                             {"tracker": "inconnu", "torrents": 1, "uploaded": 1, "downloaded": 1, "upspeed": 0}]
sampled = ratio.sample(NOW, force=True)
check("ratio : totaux enregistrés par tracker, tracker inconnu ignoré", sampled == {"trk.tracker-a.org.up": 40 * GB, "trk.tracker-a.org.down": 100 * GB,
                                                                                    "trk.tracker-b.org.up": 400 * GB, "trk.tracker-b.org.down": 100 * GB}, sorted(sampled))
check("ratio : une lecture par heure", ratio.sample(NOW + 60) == {})
for index in range(8):
    stamp = NOW - (7 - index) * 86400
    metrics.collect({"trk.tracker-a.org.up": (10 + index * 5) * GB, "trk.tracker-a.org.down": (60 + index * 5) * GB,
                     "trk.tracker-b.org.up": 300 * GB, "trk.tracker-b.org.down": 100 * GB}, now=stamp)
view = {r["tracker"]: r for r in ratio.rows(NOW, ctl.tracker_stats())}
check("ratio : page (statut, ratio, objectif)", view["tracker-a.org"]["status"] == "below" and view["tracker-a.org"]["ratio"] == 0.4 and view["tracker-b.org"]["status"] == "ok", view)
check("ratio : évolution de la semaine", view["tracker-a.org"]["week_up"] > 0 and view["tracker-a.org"]["week_down"] > 0)
found = ratio.findings(NOW)
check("ratio : alerte pour le seul tracker sous l'objectif", list(found) == ["ratio:tracker-a.org"] and "50 Go" in found["ratio:tracker-a.org"][1], found)
check("ratio : clés cachées de l'historique machine", not any(k.startswith("trk.") for k in metrics.history("24h", NOW)["series"]))

# --- 4. mises à jour par lots --------------------------------------------------------------------------------------------------------
rollout, versions = ctl.rollout(), ctl.versions()
calls = []
state = {"radarr": {"state": "running", "health": "healthy"}, "sonarr": {"state": "running", "health": ""}, "bazarr": {"state": "running", "health": "healthy"},
         "traefik": {"state": "running", "health": "healthy"}, "gluetun": {"state": "running", "health": "healthy"}}
ctl.containers = lambda: state
versions.unpinned_services = lambda: ["traefik", "radarr", "gluetun", "sonarr", "bazarr"]
versions.pins_read = lambda: {"prowlarr": {"since": 1}}
outcomes = {"radarr": "mis à jour", "sonarr": "mis à jour", "bazarr": "déjà à jour"}


def fake_update(service):
    calls.append(service)
    result = outcomes[service]
    if isinstance(result, Exception):
        raise result
    return result


versions.update = fake_update
versions.rollback = lambda service, target=None: calls.append("rollback:" + service)
plan = rollout.plan()
check("lot : Traefik et gluetun hors lot, épinglés listés", [r["service"] for r in plan["batch"]] == ["bazarr", "radarr", "sonarr"]
      and sorted(r["service"] for r in plan["apart"]) == ["gluetun", "traefik"] and plan["skipped"][0]["service"] == "prowlarr", plan)
check("lot : l'absence de contrôle d'état est signalée", {r["service"]: r["healthcheck"] for r in plan["batch"]}["sonarr"] is False)
report = rollout.run(wait=lambda s: None)
check("lot : un service à la fois, dans l'ordre", calls == ["bazarr", "radarr", "sonarr"] and report["summary"] == {"updated": 2, "same": 1, "failed": 0, "ignored": 0}, (calls, report["summary"]))
check("lot : service sans contrôle d'état mentionné dans le rapport", "contrôle d'état" in [i for i in report["items"] if i["service"] == "sonarr"][0]["detail"])
check("lot : rapport conservé", rollout.last_report()["summary"]["updated"] == 2)
calls.clear()
outcomes["radarr"] = ctl.SeedboxError("radarr : la nouvelle version ne démarre pas ; version précédente rétablie")
err = raises(rollout.run, wait=lambda s: None)
rep = rollout.last_report()
check("lot : échec -> arrêt du lot, suivants ignorés, rien d'autre touché", calls == ["bazarr", "radarr"] and [i["result"] for i in rep["items"]] == ["déjà à jour", "retour arrière", "ignoré"] and err, (calls, rep["items"]))
check("lot : l'échec est annoncé sur les canaux d'alerte", any("Mise à jour par lots" in t for t, _, _ in sent))
calls.clear()
raises(rollout.run, stop_on_failure=False, wait=lambda s: None)
check("lot : option « continuer » traite la suite", calls == ["bazarr", "radarr", "sonarr"], calls)
calls.clear()
outcomes["radarr"] = "mis à jour"
state["radarr"] = {"state": "exited", "health": ""}
err = raises(rollout.run, only=["radarr"], wait=lambda s: None)
check("lot : service instable après le délai -> retour arrière explicite", "rollback:radarr" in calls and rollout.last_report()["items"][0]["result"] == "retour arrière" and err, calls)
state["radarr"] = {"state": "running", "health": "healthy"}
check("lot : service hors lot refusé", raises(rollout.run, only=["traefik"]) is not None)

# --- 5. comparaison de sauvegardes ------------------------------------------------------------------------------------------------------
diff = ctl.backupdiff()
masker = diff.Masker()
samples = ["QBIT_PASSWORD=hunter2", "  password: '$pbkdf2-sha512$310000$aaaa$bbbb'", '"apiKey": "abc, def",', "<ApiKey>0123456789abcdef0123456789abcdef</ApiKey>",
           '{"a":"b","token":"zzz9"}', "jwt_secret: monsecret", "ADMIN_USER=vincent", "port: 8080"]
masked = [masker.line(s) for s in samples]
check("diff : secrets masqués (mot de passe, condensé, clé API, XML, JSON en ligne, secret)", not any(x in " ".join(masked) for x in ("hunter2", "pbkdf2", "abc, def", "0123456789abcdef", "zzz9", "monsecret")), masked)
check("diff : valeurs ordinaires conservées", masked[6] == "ADMIN_USER=vincent" and masked[7] == "port: 8080")
check("diff : même secret = même repère, secret différent = repère différent", masker.line("TOKEN=a") == masker.line("TOKEN=a") and masker.line("TOKEN=a") != masker.line("TOKEN=b"))
check("diff : repère différent d'une comparaison à l'autre", diff.Masker().line("TOKEN=a") != masker.line("TOKEN=a"))
old, new = root / "old", root / "new"
for folder, env, extra in ((old, "DOMAIN=a.fr\nQBIT_PASSWORD=ancien\nPORT=1\n", {"gone.yml": "x: 1\n"}), (new, "DOMAIN=b.fr\nQBIT_PASSWORD=nouveau\nPORT=1\n", {"added.json": '{"k": 1}'})):
    (folder / "etc").mkdir(parents=True)
    (folder / ".env").write_text(env)
    (folder / "etc" / "same.yml").write_text("a: 1\n")
    (folder / "etc" / "app.db").write_bytes(os.urandom(64) if folder is new else b"\0" * 64)
    (folder / "etc" / "logs").mkdir()
    (folder / "etc" / "logs" / "bruit.log").write_text(str(time.time()))
    for name, text in extra.items():
        (folder / "etc" / name).write_text(text)
result = diff.compare_trees(diff.walk(old), diff.walk(new))
paths = {f["path"]: f for f in result["files"]}
env_diff = " ".join(paths["/.env"]["diff"])
check("diff : .env modifié, domaine visible, mots de passe jamais", "DOMAIN=a.fr" in env_diff and "DOMAIN=b.fr" in env_diff and "ancien" not in env_diff and "nouveau" not in env_diff, env_diff)
check("diff : mot de passe changé = repères différents", len(set(re.findall(r"\[masqué:[0-9a-f]{4}\]", env_diff))) == 2, env_diff)
check("diff : fichiers ajoutés, retirés, base de données signalée sans contenu", paths["/etc/added.json"]["change"] == "added" and paths["/etc/gone.yml"]["change"] == "removed"
      and paths["/etc/app.db"]["kind"] == "binaire" and not paths["/etc/app.db"]["diff"], sorted(paths))
check("diff : journaux ignorés, fichiers identiques comptés", "/etc/logs/bruit.log" not in paths and result["summary"]["same"] == 1 and result["summary"]["added"] == 1 and result["summary"]["removed"] == 1, result["summary"])
check("diff : texte modifié classé avant les autres", result["files"][0]["path"] == "/.env")

backup = ctl.backup()
archives = {}
for name, folder in (("seedbox-20261101-020000.tar.gz.enc", old), ("seedbox-20261108-020000.tar.gz.enc", new)):
    archive = root / (name + ".tar")
    with tarfile.open(archive, "w") as tar:
        tar.add(folder / ".env", "opt/seedbox/.env")
        tar.add(folder / "etc" / "same.yml", "opt/seedbox/same.yml")
    archives[name] = archive
backup.find = lambda name: archives[name] if name in archives else (_ for _ in ()).throw(ctl.SeedboxError("sauvegarde introuvable"))
backup.open_archive = lambda path, key, work: path
backup.KEY_FILE = root / "backup.key"
backup.KEY_FILE.write_text("cle")
backup.listing = lambda: [{"name": n, "where": "local", "size": 1, "time": 1.0} for n in archives] + [{"name": "seedbox-20261109-020000-avant.tar.gz.enc", "where": "local", "size": 1, "time": 2.0}]
names = list(archives)
out = diff.diff(names[0], names[1])
check("diff : deux sauvegardes comparées de bout en bout, secrets absents du résultat", out["summary"]["changed"] == 1 and "ancien" not in json.dumps(out) and "nouveau" not in json.dumps(out), out["summary"])
check("diff : liste sans les sauvegardes « avant » restauration", len(diff.listing()["backups"]) == 2)
check("diff : « courant » refusé en première position, sauvegarde inconnue refusée", raises(diff.diff, "courant", names[0]) is not None and raises(diff.diff, "x", names[1]) is not None)

# --- 6. assistant de dépannage ----------------------------------------------------------------------------------------------------------------
diagnose = ctl.diagnose()
check("dépannage : dépendances lues dans l'étiquette de Compose", diagnose.parse_depends("gluetun:service_healthy:false,traefik:service_started:true") == ["gluetun", "traefik"] and diagnose.parse_depends("") == [])
healthy = diagnose.analyse({"status": "running", "started_at": NOW - 7200, "restarts": 0, "health": "healthy", "mem_free_percent": 40, "disks": [("stockage", 50)],
                            "dependencies": {"gluetun": "healthy"}, "logs": "ok\nfine"}, NOW)
check("dépannage : service sain -> aucun problème, aucune action", healthy["level"] == "ok" and healthy["actions"] == [] and "Aucun problème" in healthy["summary"], healthy["summary"])
loop = diagnose.analyse({"status": "restarting", "restarts": 9, "health": "", "previous": 2, "mem_free_percent": 30, "disks": [], "dependencies": {"gluetun": "exited"},
                         "logs": "bind: address already in use\npermission denied"}, NOW)
ids = [a["id"] for a in loop["actions"]]
check("dépannage : boucle de redémarrage détectée, dépendance en panne désignée", loop["level"] == "fail" and any(s["key"] == "deps" and "gluetun" in s["detail"] for s in loop["steps"]))
check("dépannage : causes courantes reconnues dans le journal", any("port est déjà utilisé" in s["detail"] and "Droits" in s["detail"] for s in loop["steps"] if s["key"] == "logs"))
check("dépannage : actions proposées (redémarrer, version précédente) et jamais exécutées", ids == ["restart", "rollback"], ids)
oom = diagnose.analyse({"status": "exited", "exit_code": 137, "oom": True, "restarts": 0, "health": "", "disks": [("stockage", 98)], "logs": "no space left on device"}, NOW)
check("dépannage : manque de mémoire et disque plein repérés, nettoyage proposé", any(s["key"] == "memory" and s["level"] == "fail" for s in oom["steps"])
      and any(s["key"] == "disk" and s["level"] == "fail" for s in oom["steps"]) and "cleanup" in [a["id"] for a in oom["actions"]], oom["summary"])
check("dépannage : conteneur absent", diagnose.analyse({"status": "absent"}, NOW)["level"] == "fail")
unhealthy = diagnose.analyse({"status": "running", "started_at": NOW - 30, "restarts": 2, "health": "unhealthy", "health_output": "curl: (7) refused\n", "disks": [], "logs": ""}, NOW)
check("dépannage : « unhealthy » avec la dernière sortie du contrôle", any("refused" in s["detail"] for s in unhealthy["steps"] if s["key"] == "health") and unhealthy["actions"][0]["id"] == "restart")
check("dépannage : nom de service invalide refusé", raises(diagnose.gather, "../etc") is not None)

# --- 7. accès de secours ------------------------------------------------------------------------------------------------------------------------
emergency = ctl.emergency()
check("secours : rien de configuré au départ, usage refusé", emergency.status(NOW)["configured"] is False and "aucun code" in (raises(emergency.use, "admin", "x") or ""))
code = emergency.init("vincent", iterations=2000, now=NOW)
check("secours : code lisible de 24 caractères en 6 groupes", re.fullmatch(r"([0-9A-HJKMNP-TV-Z]{4}-){5}[0-9A-HJKMNP-TV-Z]{4}", code), code)
stored = (ctl.ETC / "emergency.json").read_text()
check("secours : seul un condensé est conservé, fichier en 0600", code.replace("-", "") not in stored and oct((ctl.ETC / "emergency.json").stat().st_mode & 0o777) == "0o600")
check("secours : saisie tolérante (minuscules, espaces, O/0, I/L/1)", emergency.normalize("ab0o-i1lx") == "AB00111X")
ctl.users_store_add("admin", "motdepasse-initial", "admin")
ctl.users_store_add("marie", "motdepasse-marie", "user")
con = sqlite3.connect(ctl.CONFIG / "authelia" / "db.sqlite3")
con.execute("CREATE TABLE totp_configurations (username TEXT)")
con.execute("CREATE TABLE webauthn_credentials (username TEXT)")
con.executemany("INSERT INTO totp_configurations VALUES (?)", [("admin",), ("marie",)])
con.execute("INSERT INTO webauthn_credentials VALUES ('admin')")
con.commit()
con.close()
slept = []
clock = [NOW]
for attempt in range(1, 5):
    err = raises(emergency.use, "admin", "FAUX-FAUX-FAUX", now=clock[0], sleep=slept.append)
check("secours : code faux refusé, essais restants annoncés", err and "1 essai" in err, err)
err = raises(emergency.use, "admin", "FAUX", now=clock[0], sleep=slept.append)
check("secours : 5e échec -> verrouillé 15 minutes, alerte envoyée", err and "verrouillé" in err and any("Accès de secours" in t for t, _, _ in sent), err)
check("secours : le bon code ne passe pas pendant le verrouillage", "verrouillé" in (raises(emergency.use, "admin", code, now=clock[0] + 60, sleep=slept.append) or ""))
check("secours : freinage d'une seconde par échec", len(slept) == 5)
ctl.user_passwd = lambda login, password: (_ for _ in ()).throw(ctl.SeedboxError("Jellyfin arrêté"))
check("secours : compte non administrateur refusé", "administrateur" in (raises(emergency.use, "marie", code, now=clock[0] + 1000, sleep=slept.append) or ""))
result = emergency.use("admin", code.lower().replace("-", " "), now=clock[0] + 1000, sleep=slept.append)
check("secours : bon code -> mot de passe temporaire, secondes étapes retirées", len(result["password"]) >= 16 and result["removed"] == 2, result["removed"])
check("secours : repli sur Authelia seul quand les autres applications ne répondent pas", result["warnings"] and "Authelia" in result["warnings"][0])
check("secours : le nouveau mot de passe est celui d'Authelia", ctl.users_load()["admin"]["password"].startswith("$pbkdf2-sha512$"))
con = sqlite3.connect(ctl.CONFIG / "authelia" / "db.sqlite3")
left = con.execute("SELECT username FROM totp_configurations").fetchall()
con.close()
check("secours : seules les secondes étapes du compte visé sont retirées", left == [("marie",)], left)
check("secours : code à usage unique, remplacé par un nouveau", result["new_code"] != code and "périmé" not in "" and raises(emergency.use, "admin", code, now=clock[0] + 2000, sleep=slept.append) is not None)
check("secours : le nouveau code fonctionne", emergency.use("admin", result["new_code"], now=clock[0] + 3000, sleep=slept.append)["login"] == "admin")
check("secours : usage et alerte journalisés, état remis à zéro", emergency.status(NOW + 4000)["fails"] == 0 and emergency.status()["last_used"] > 0 and any("utilisé" in t for t, _, _ in sent))
events = ctl.audit().search(q="secours")["events"]
check("secours : chaque essai est dans le journal d'audit, sans code ni mot de passe", len(events) >= 8 and "FAUX" not in json.dumps(events) and result["password"] not in json.dumps(events), len(events))

# --- 8. export et réimport du catalogue -----------------------------------------------------------------------------------------------------------
catalog = ctl.catalogexport()
movies = [{"id": 1, "title": "Alien", "year": 1979, "tmdbId": 348, "imdbId": "tt0078748", "monitored": True, "qualityProfileId": 4, "path": "/mnt/films/Alien (1979)", "hasFile": True, "sizeOnDisk": 5},
          {"id": 2, "title": "=HYPERLINK(\"x\")", "year": 2000, "tmdbId": 5, "monitored": False, "qualityProfileId": 9, "path": "/autre/Piege", "hasFile": False}]
shows = [{"title": "Severance", "year": 2022, "tvdbId": 371980, "tmdbId": 95396, "monitored": True, "qualityProfileId": 4, "path": "/mnt/series/Severance",
          "statistics": {"episodeFileCount": 9, "episodeCount": 19, "sizeOnDisk": 7}}]
data = {("radarr", "/movie"): movies, ("radarr", "/qualityprofile"): [{"id": 4, "name": "HD"}, {"id": 9, "name": "Ultra"}], ("radarr", "/rootfolder"): [{"path": "/mnt/films/"}],
        ("sonarr", "/series"): shows, ("sonarr", "/qualityprofile"): [{"id": 4, "name": "HD"}], ("sonarr", "/rootfolder"): [{"path": "/mnt/series"}]}
fake_api = lambda name, path: data[(name, path)]
bundle = catalog.collect(fake_api)
check("catalogue : films et séries collectés (profil par nom, présence du fichier)", len(bundle["movies"]) == 2 and bundle["movies"][0]["quality"] == "HD" and bundle["series"][0]["episodes"] == 9, bundle["movies"][0])
ctl.container_running = lambda name: True
catalog.save({"keep": 2, "days": 7})
for index in range(4):
    catalog.run(lambda: bundle, now=NOW + index * 10)
check("catalogue : seuls les derniers exports sont conservés (réglage keep)", len(catalog.listing()) == 2, [e["name"] for e in catalog.listing()])
check("catalogue : export vide issu d'un service en panne non enregistré", raises(catalog.run, lambda: {"movies": [], "series": [], "skipped": ["radarr : HTTP 500"]}) is not None and len(catalog.listing()) == 2)
latest = catalog.listing()[0]["created"]
check("catalogue : pas de nouvel export avant l'échéance", catalog.tick(latest + 86400, lambda: bundle) is False)
check("catalogue : export planifié après l'échéance", catalog.tick(latest + 8 * 86400, lambda: bundle) is True)
csv_text = catalog.to_csv(bundle)
check("catalogue : CSV sans injection de formule", "'=HYPERLINK" in csv_text and "\n=HYPERLINK" not in csv_text and csv_text.splitlines()[0].startswith("type,titre"))
check("catalogue : fichier étranger ou altéré refusé", raises(catalog.parse, "pas du json") is not None and raises(catalog.parse, json.dumps({"format": "autre", "version": 1})) is not None)
loaded = catalog.load()
radarr_state = {"ok": True, "existing": {348}, "profiles": {"HD": 4}, "roots": ["/mnt/films"]}
sonarr_state = {"ok": True, "existing": set(), "profiles": {"HD": 4}, "roots": ["/mnt/series"]}
plan = catalog.plan(loaded, {"radarr": radarr_state, "sonarr": sonarr_state})
check("catalogue : à blanc, ce qui existe est ignoré et ce qui manque compté", plan["apps"]["radarr"]["already"] == 1 and plan["apps"]["radarr"]["to_add"] == 1 and plan["apps"]["sonarr"]["to_add"] == 1, plan["apps"])
check("catalogue : profil de qualité inconnu signalé comme remplacé", plan["apps"]["radarr"]["replaced"] == 1)
posts = []


class Reply:
    ok, status = True, 201


ctl.arr_api = lambda *a, **k: type("R", (), {"ok": False, "status": 404, "json": lambda self: []})()
outcome = catalog.apply(loaded, {"radarr": radarr_state, "sonarr": sonarr_state}, post=lambda app, path, body: posts.append((app, path, body)) or Reply(), sleep=lambda s: None)
radarr_post = [b for a, p, b in posts if a == "radarr"][0]
sonarr_post = [b for a, p, b in posts if a == "sonarr"][0]
check("catalogue : ajouts sans recherche, suivi d'origine conservé", radarr_post["addOptions"] == {"searchForMovie": False} and radarr_post["monitored"] is False
      and sonarr_post["addOptions"]["searchForMissingEpisodes"] is False and outcome["radarr"]["added"] == 1 and outcome["sonarr"]["added"] == 1, outcome)
check("catalogue : dossier d'origine repris s'il est dans un dossier racine, sinon dossier racine par défaut", sonarr_post["path"] == "/mnt/series/Severance" and "path" not in radarr_post and radarr_post["rootFolderPath"] == "/mnt/films")
check("catalogue : application arrêtée -> rapportée sans rien tenter", catalog.apply(loaded, {"radarr": {**radarr_state, "ok": False, "error": "arrêté"}, "sonarr": sonarr_state}, post=lambda *a: Reply(), sleep=lambda s: None)["radarr"]["errors"] == ["arrêté"])

# --- 9. « Mon espace » : données du seul compte connecté, route durcie ---------------------------------------------------------------------------
myspace = ctl.myspace()
ctl.user_list = lambda: [{"login": "marie", "role": "user"}, {"login": "paul", "role": "user"}]
guests = ctl.guests()
guests.write_json(guests.GUESTS_FILE, {"marie": {"expires": int(NOW) + 5 * 86400, "profile": {"max_sessions": 2, "bitrate_mbps": 8, "downloads": False}},
                                         "paul": {"expires": 0, "profile": {}}})
ctl.jellyfin_user = lambda login: {"Id": "jf-" + login}
ctl.seerr_user_id = lambda jf: {"jf-marie": 11, "jf-paul": 12}[jf]
SEERR = {"/user/11/quota": {"movie": {"limit": 5, "used": 3, "remaining": 2, "days": 7}, "tv": {"limit": 0}},
         "/user/11/requests?take=12&skip=0&sort=added": {"results": [
             {"id": 1, "type": "movie", "status": 2, "createdAt": "2026-11-10T10:00:00.000Z", "media": {"tmdbId": 348, "status": 5, "mediaType": "movie"}},
             {"id": 2, "type": "tv", "status": 1, "createdAt": "2026-11-11T10:00:00.000Z", "media": {"tmdbId": 95396, "status": 2}},
             {"id": 3, "type": "movie", "status": 3, "createdAt": "2026-11-12T10:00:00.000Z", "media": {"tmdbId": 5, "status": 1}}]},
         "/user/12/quota": {"movie": {"limit": 1, "used": 0, "remaining": 1, "days": 7}}, "/movie/348": {"title": "Alien"}, "/tv/95396": {"name": "<b>Severance</b>"}}
ctl.seerr_api = lambda method, path, body=None, cookie=None: type("R", (), {"ok": True, "status": 200, "json": lambda self: SEERR.get(path, {})})()
mine = myspace.overview("marie", NOW)
check("Mon espace : durée restante, limites, quota et ce qu'il en reste", mine["days_left"] == 5 and mine["limits"]["sessions"] == 2 and mine["quota"]["movie"]["remaining"] == 2 and mine["quota"]["tv"] is None, mine["quota"])
check("Mon espace : état des demandes (disponible, en attente, refusée)", [r["status"] for r in mine["requests"]] == ["Disponible", "En attente d'approbation", "Refusée"] and mine["requests"][0]["title"] == "Alien", mine["requests"])
check("Mon espace : titre inconnu remplacé sans erreur", mine["requests"][2]["title"].startswith("Titre n°"))
check("Mon espace : rien d'un autre compte ni d'identifiant interne", "paul" not in json.dumps(mine) and "jf-" not in json.dumps(mine) and "11" not in {k for r in mine["requests"] for k in r})
check("Mon espace : compte inconnu refusé", raises(myspace.overview, "inconnu", NOW) is not None)
check("Mon espace : 60 lectures par minute puis refus", not any(myspace.throttled("x1", NOW + i * 0.1) for i in range(60)) and myspace.throttled("x1", NOW + 7) and not myspace.throttled("x1", NOW + 70))

ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret", "DOMAIN_MODE": "auto", "AUTH_2FA": "none", "DOMAIN": "10.0.0.2"}
import server  # noqa: E402
console = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=console.serve_forever, daemon=True).start()
conn = http.client.HTTPConnection("127.0.0.1", console.server_port, timeout=20)


def call(path, headers=None, method="GET", body=None):
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    response = conn.getresponse()
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode(errors="replace")


ME = {"X-Seedbox-Me-Secret": "secret", "Remote-User": "marie", "Remote-Groups": "users"}
status, page = call("/me/", ME)
check("route /me : page servie au compte connecté", status == 200 and "<html" in page)
status, data = call("/me/api/overview", ME)
check("route /me : données du compte connecté seulement", status == 200 and data["login"] == "marie" and "paul" not in json.dumps(data), status)
status, data = call("/me/api/overview", {**ME, "Remote-User": "paul"})
check("route /me : l'identité vient de l'en-tête d'Authelia", status == 200 and data["login"] == "paul")
check("route /me : un paramètre ne change jamais le compte", call("/me/api/overview?login=paul&user=paul", ME)[1]["login"] == "marie")
check("route /me : sans secret propre à la page -> refusé", call("/me/api/overview", {"Remote-User": "marie"})[0] == 403 and call("/me/api/overview", {**ME, "X-Seedbox-Me-Secret": "faux"})[0] == 403)
check("route /me : le secret de l'administration n'ouvre pas /me", call("/me/api/overview", {"X-Seedbox-Proxy-Secret": "secret", "Remote-User": "marie"})[0] == 403)
check("route /me : un en-tête d'administration ou public présent -> refusé", call("/me/api/overview", {**ME, "X-Seedbox-Proxy-Secret": "secret"})[0] == 403
      and call("/me/api/overview", {**ME, "X-Seedbox-Public-Secret": "secret"})[0] == 403)
check("route /me : identité absente ou invalide -> refusé", call("/me/api/overview", {"X-Seedbox-Me-Secret": "secret"})[0] == 403
      and call("/me/api/overview", {"X-Seedbox-Me-Secret": "secret", "Remote-User": "../etc"})[0] == 403)
check("route /me : écriture refusée", call("/me/api/overview", ME, "POST", {})[0] == 405)
check("route /me : pas d'accès à l'administration avec le secret de /me", call("/api/status", ME)[0] == 403 and call("/api/users", {**ME, "Remote-Groups": "admins"})[0] == 403)
check("route /me : chemins inconnus et traversée refusés", call("/me/api/autre", ME)[0] == 404 and call("/me/static/../../etc/passwd", ME)[0] == 404 and call("/me/static/server.py", ME)[0] == 404)
check("route /me : l'administration ne répond pas à /me avec un seul compte non admin", call("/api/diagnose", {"X-Seedbox-Proxy-Secret": "secret", "Remote-User": "marie", "Remote-Groups": "users"})[0] == 403)

# --- 10. télécommande ------------------------------------------------------------------------------------------------------------------------------
remote = ctl.telecommande()
H1, H2, H3 = "a" * 40, "b" * 40, "c" * 40
fake = FakeQbit()
fake.items = [{"hash": H1, "state": "downloading", "dlspeed": 100, "upspeed": 10}, {"hash": H2, "state": "pausedDL", "dlspeed": 0, "upspeed": 0},
              {"hash": H3, "state": "stalledUP", "dlspeed": 0, "upspeed": 5}]
ctl.vacation().active = lambda now=None: False
ctl.vacation().overview = lambda now=None: {"active": False, "until": 0, "started": 0, "by": "", "paused": 0, "max_days": 120, "qbittorrent": True}
ctl.alerts().overview = lambda: {"active": [{"title": "Disque plein", "level": "critical"}], "settings": {}, "channels": []}
view = remote.overview(fake)
check("télécommande : alertes, téléchargements actifs et en pause, débits", view["alerts_total"] == 1 and view["downloads"] == {"available": True, "active": 1, "paused": 1, "down": 100, "up": 15}, view["downloads"])
check("télécommande : pause générale = torrents qui tournaient, pas ceux déjà en pause", remote.pause(fake) == 2 and sorted(fake.paused) == [H1, H3])
fake.items[0]["state"], fake.items[2]["state"] = "pausedDL", "pausedUP"
check("télécommande : reprise = seulement ce que la télécommande a arrêté", remote.resume(fake) == 2 and sorted(fake.resumed) == [H1, H3] and H2 not in fake.resumed)
check("télécommande : plus rien à reprendre ensuite", remote.resume(fake) == 0 and remote.paused_by_us() == [])
ctl.vacation().active = lambda now=None: True
check("télécommande : refus pendant le mode vacances", raises(remote.pause, fake) is not None)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
