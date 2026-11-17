"""Entretien des médias : prévision de saturation, rapport hebdomadaire, jamais lus et séries abandonnées, intégrité, renommage,
sous-titres, quotas invités, énergie des disques, ratios réels des trackers (sans Docker, sans réseau)."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbcare"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares",
                    "SB_SYS": "sys", "SB_PROC": "proc"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/console"))
import seedboxctl as ctl  # noqa: E402

bad = 0
NOW = 1_800_000_000
DAY = 86400
GB = 1 << 30


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def raises(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (ctl.SeedboxError, ValueError) as error:
        return str(error) or "erreur"
    return None


class Reply:
    def __init__(self, data=None, status=200):
        self.data, self.status = data, status
        self.ok = 200 <= status < 300

    def json(self):
        return self.data

    def cookies(self):
        return "c=1"


SENT = []
ctl.audit = lambda: type("A", (), {"record": staticmethod(lambda *a, **k: None)})()
ctl.alerts = lambda: type("N", (), {"notify": staticmethod(lambda title, message, level="warning", force=False: SENT.append((title, message)) or {})})()
ctl.container_running = lambda name: True
ctl.env_read = lambda: {"PUID": "1000", "PGID": "1000"}

# --- 1. prévision de saturation ----------------------------------------------------------------------------------------------------
sf = ctl.spacefore()
for day in range(10):
    sf.sample(NOW - (10 - day) * DAY, {"local": (1000 * GB, (500 - day * 10) * GB), "nas": (5000 * GB, 3000 * GB), "tout": (100 * GB, 50 * GB + (day % 2) * GB)})
rows = {r["volume"]: r for r in sf.forecast(NOW)}
check("prévision : -10 Go par jour, 410 Go libres -> environ 41 jours", rows["local"]["days_left"] and 38 <= rows["local"]["days_left"] <= 44, str(rows["local"]))
check("prévision : volume stable -> pas de date", rows["nas"]["date"] is None and rows["nas"]["level"] == "ok")
check("prévision : bruit sans tendance -> pas de date", rows["tout"]["date"] is None)
sf.save({"warn_days": 60})
check("prévision : alerte sous 60 jours, une seule par semaine", sf.tick(NOW, {"local": (1000 * GB, 400 * GB)}) == 1 and sf.tick(NOW + 3600) == 0 and "local" in SENT[-1][0])
check("prévision : réglage hors limites refusé", raises(sf.save, {"warn_days": 1}) and raises(sf.save, {"x": 1}))
sf2_hist = sf.history()
check("prévision : trop peu de points -> inconnu", sf.fit([[NOW, 5, 9], [NOW + 60, 4, 9]], NOW + 60) is None)

# --- 2. rapport hebdomadaire ----------------------------------------------------------------------------------------------------------
lr = ctl.libreport()
lr.record("relink", 3, 9 * GB, NOW - DAY)
lr.record("replace", 2, 0, NOW - 2 * DAY)
lr.record("relink", 1, GB, NOW - 30 * DAY)
lr.record("inconnu", 1)
totals = lr.totals(NOW)
check("rapport : 7 jours seulement, types inconnus ignorés", totals["relink"] == {"count": 3, "size": 9 * GB} and totals["replace"]["count"] == 2)
text = lr.build(NOW)
check("rapport : chiffres seulement, saturation incluse", "3 copies reliées, 9 Go" in text and "2 remplacements lancés" in text and "saturation" in text, text)
check("rapport : désactivé par défaut", lr.tick(NOW) is False)
monday = time.mktime((2026, 11, 16, 10, 0, 0, 0, 0, -1))
lr.save({"enabled": True, "day": time.localtime(monday).tm_wday, "hour": 9})
check("rapport : envoyé le bon jour passé l'heure, puis pas avant une semaine", lr.tick(monday) is True and lr.tick(monday + 3600) is False)
check("rapport : réglage invalide refusé", raises(lr.save, {"day": 9}) and raises(lr.save, {"last": 1}))

# --- 3. jamais lus et séries abandonnées --------------------------------------------------------------------------------------
dm = ctl.dormant()
recs = [{"app": "radarr", "id": 1, "title": "Vu", "size": 5 * GB, "added": "2020-01-01", "path": "/data/media/a.mkv"},
        {"app": "radarr", "id": 2, "title": "Jamais", "size": 9 * GB, "added": "2020-01-01", "path": "/data/media/b.mkv"},
        {"app": "radarr", "id": 3, "title": "Récent", "size": 7 * GB, "added": time.strftime("%Y-%m-%d", time.gmtime(NOW - 10 * DAY)), "path": "/data/media/c.mkv"},
        {"app": "sonarr", "id": 7, "title": "Série", "size": GB, "added": "2020-05-05", "path": "/data/media/s1.mkv"}]
found = dm.never_played(recs, {"/data/media/a.mkv": NOW - DAY}, dm.settings(), NOW)
check("jamais lus : ni les lus ni les récents", [t["title"] for t in found] == ["Jamais", "Série"], str(found))
series = [{"id": 7, "title": "Série", "monitored": True, "statistics": {"episodeFileCount": 3, "episodeCount": 10, "sizeOnDisk": 3 * GB}, "ended": True},
          {"id": 8, "title": "Complète", "monitored": True, "statistics": {"episodeFileCount": 10, "episodeCount": 10}},
          {"id": 9, "title": "En cours", "monitored": True, "nextAiring": "2027-01-01", "statistics": {"episodeFileCount": 3, "episodeCount": 10}},
          {"id": 10, "title": "Non suivie", "monitored": False, "statistics": {"episodeFileCount": 1, "episodeCount": 10}}]
ab = dm.abandoned(series, {7: "2020-05-05", 8: "2020-05-05", 9: "2020-05-05", 10: "2020-05-05"}, dm.settings(), NOW)
check("séries abandonnées : incomplète, suivie, sans épisode à venir et sans fichier récent", [s["id"] for s in ab] == [7], str(ab))
calls = []


def arr(app, method, path, body=None):
    calls.append((app, method, path, body))
    if path == "/series":
        return Reply(series)
    return Reply({})


data = dm.scan(NOW, api=arr, played={"/data/media/a.mkv": NOW})
check("analyse : relevé conservé", data["reliable"] and dm.load()["at"] == int(NOW))
ctl.libscan().records = lambda kind=None: recs
data = dm.scan(NOW, api=arr, played={"/data/media/a.mkv": NOW})
check("analyse : titres jamais lus listés", [t["id"] for t in data["unplayed"]] == [2, 7] and data["abandoned"][0]["id"] == 7, str(data["unplayed"]))
unreliable = dm.scan(NOW, api=arr, played={})
check("analyse : sans aucune lecture connue, liste déclarée peu fiable et vide", not unreliable["reliable"] and unreliable["unplayed"] == [])
dm.scan(NOW, api=arr, played={"/data/media/a.mkv": NOW})
check("ne plus suivre : seulement les titres du relevé", dm.unmonitor("radarr", [2], api=arr)["done"] == 1 and calls[-1][3] == {"movieIds": [2], "monitored": False})
check("ne plus suivre : titre inconnu, application inconnue, liste vide refusés", raises(dm.unmonitor, "radarr", [999], api=arr) and raises(dm.unmonitor, "x", [1], api=arr) and raises(dm.unmonitor, "radarr", [], api=arr))

# --- 4. intégrité -------------------------------------------------------------------------------------------------------------------
ig = ctl.integrity()
slim = ctl.mediaslim()
slim.image = lambda: "jellyfin/img:test"
good = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}], "format": {"duration": "7000"}}
check("intégrité : fichier sain", ig.judge(good, {"runtime": 120, "seconds": 0}, "")[0] == "bon")
check("intégrité : tronqué sous 85 % de la durée annoncée", ig.judge({**good, "format": {"duration": "3000"}}, {"runtime": 120}, "")[1].startswith("tronqué : 42 %"))
check("intégrité : sans vidéo, sans durée, fin abîmée", ig.judge({"streams": [{"codec_type": "audio"}], "format": {"duration": "9"}}, {}, "")[1] == "aucune piste vidéo"
      and ig.judge({"streams": [{"codec_type": "video"}], "format": {}}, {}, "")[1] == "durée illisible" and ig.judge(good, {}, "err")[1].startswith("fin du fichier"))
files = [{"app": "radarr", "id": 1, "title": "A", "path": "/data/media/a.mkv", "size": 5, "file_id": 11, "runtime": 120, "seconds": 0, "added": "2024-01-01"},
         {"app": "radarr", "id": 2, "title": "B", "path": "/data/media/b.mkv", "size": 6, "file_id": 12, "runtime": 120, "seconds": 0, "added": "2024-02-01"}]
commands = []


def runner(command, **kwargs):
    commands.append(command)
    joined = " ".join(command)
    if "ffprobe" in joined:
        duration = "3000" if "b.mkv" in joined else "7200"
        return subprocess.CompletedProcess(command, 0, json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": duration}}), "")
    return subprocess.CompletedProcess(command, 0, "", "")


check("intégrité : rien à faire si désactivé", ig.tick(NOW, launcher=lambda: None) is False)
ig.save({"enabled": True, "per_night": 1})
check("intégrité : à contrôler = jamais vus, les récents d'abord", [r["id"] for r in ig.pending(files, {})] == [2, 1])
ig.run(runner=runner, once=True, records=files)
check("intégrité : au plus per_night fichiers par passage", ig.overview()["checked"] == 1)
ig.save({"per_night": 10})
ig.run(runner=runner, once=True, records=files)
problems = ig.problems()
check("intégrité : le fichier tronqué est signalé, l'autre non", [p["title"] for p in problems] == ["B"] and "tronqué" in problems[0]["reason"], str(problems))
check("intégrité : conteneur sans réseau, médias en lecture seule non requis mais réseau coupé", all("--network" in c and "none" in c for c in commands))
check("intégrité : un fichier inchangé n'est pas recontrôlé", ig.pending(files, ig.load()) == [])
changed = [dict(files[1], size=99)]
check("intégrité : un fichier changé est recontrôlé", [r["id"] for r in ig.pending(changed, ig.load())] == [2])
rcalls = []
ig.research("radarr", 2, 12, api=lambda *a: rcalls.append(a) or Reply({}))
check("intégrité : recherche d'une autre version (film)", rcalls[-1][3] == {"name": "MoviesSearch", "movieIds": [2]})
ig.research("sonarr", 5, 40, api=lambda app, m, path, body=None: rcalls.append((app, m, path, body)) or Reply([{"id": 901, "episodeFileId": 40}, {"id": 902, "episodeFileId": 41}]))
check("intégrité : recherche d'une autre version (épisode du fichier)", rcalls[-1][3] == {"name": "EpisodeSearch", "episodeIds": [901]})
check("intégrité : épisode introuvable refusé", raises(ig.research, "sonarr", 5, 77, api=lambda *a, **k: Reply([])))

# --- 5. renommage -----------------------------------------------------------------------------------------------------------------
rn = ctl.renamer()
recs2 = [{"app": "radarr", "id": 1, "title": "Dune"}, {"app": "radarr", "id": 2, "title": "Alien"}, {"app": "sonarr", "id": 5, "title": "Fargo"}]
sent = []


def arr2(app, method, path, body=None):
    sent.append((app, method, path, body))
    if method == "GET" and path == "/rename?movieId=1":
        return Reply([{"movieId": 1, "movieFileId": 11, "existingPath": "Dune/dune.mkv", "newPath": "Dune (2021)/Dune (2021).mkv"}])
    if method == "GET" and path == "/rename?movieId=2":
        return Reply([{"movieId": 2, "movieFileId": 12, "existingPath": "a.mkv", "newPath": "a.mkv"}])
    if method == "GET" and path == "/rename?seriesId=5":
        return Reply([{"seriesId": 5, "episodeFileId": 40 + i, "existingPath": f"x{i}.mkv", "newPath": f"y{i}.mkv"} for i in range(3)])
    return Reply({})


res = rn.scan(recs2, api=arr2, batch=2, now=NOW)
check("renommage : un passage examine un lot et mémorise le curseur", res["examined"] == 2 and rn.load()["cursor"] == 2 and not rn.load()["complete"])
res = rn.scan(recs2, api=arr2, batch=2, now=NOW)
over = rn.overview()
check("renommage : seuls les titres à renommer sont listés, le plus chargé d'abord", [t["title"] for t in over["titles"]] == ["Fargo", "Dune"] and over["files_total"] == 4, str(over["titles"]))
check("renommage : exemple avant/après", over["titles"][1]["example"] == {"before": "dune.mkv", "after": "Dune (2021).mkv"})
done = rn.apply("sonarr", [5], api=arr2)
check("renommage : commande RenameFiles avec les fichiers de l'aperçu", done["done"] == 1 and sent[-1][3] == {"name": "RenameFiles", "files": [40, 41, 42], "seriesId": 5})
check("renommage : titre hors aperçu écarté, excès refusé", rn.apply("radarr", [2], api=arr2)["skipped"] and raises(rn.apply, "radarr", list(range(30)), api=arr2) and raises(rn.apply, "x", [1], api=arr2))

# --- 6. sous-titres ---------------------------------------------------------------------------------------------------------------
sg = ctl.subsgap()
recs3 = [{"app": "radarr", "id": 1, "title": "Japonais", "size": GB, "audio": "Japanese", "subs": ""},
         {"app": "radarr", "id": 2, "title": "Sous-titré", "size": GB, "audio": "Japanese", "subs": "French"},
         {"app": "radarr", "id": 3, "title": "Français", "size": GB, "audio": "French", "subs": ""},
         {"app": "radarr", "id": 4, "title": "Codes", "size": 3 * GB, "audio": "jpn / kor", "subs": "ger"},
         {"app": "radarr", "id": 5, "title": "Pas analysé", "size": GB, "audio": "", "subs": ""}]
titles = [g["title"] for g in sg.gaps(recs3)]
check("sous-titres : signalés seulement sans audio ni sous-titres voulus, non analysés ignorés", titles == ["Codes", "Japonais"], str(titles))
sg.save({"langs": ["deu"]})
check("sous-titres : langue voulue personnalisée (codes ou noms)", [g["title"] for g in sg.gaps(recs3)] == ["Japonais", "Sous-titré", "Français"])
check("sous-titres : codes invalides refusés", raises(sg.save, {"langs": ["fr"]}) and raises(sg.save, {"langs": []}))

# --- 7. quotas invités -----------------------------------------------------------------------------------------------------------
gq = ctl.guestquota()
policy_calls = []
stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(t))


def jelly(method, path, body=None):
    if path == "/Users":
        return Reply([{"Id": "u1", "Name": "alice", "Policy": {"IsAdministrator": False}}, {"Id": "u2", "Name": "bob", "Policy": {"IsAdministrator": False}},
                      {"Id": "ad", "Name": "admin", "Policy": {"IsAdministrator": True}}])
    if path.startswith("/Users/u1/Items"):
        return Reply({"Items": [{"UserData": {"LastPlayedDate": stamp(NOW - i * DAY)}} for i in range(1, 6)] + [{"UserData": {"LastPlayedDate": stamp(NOW - 90 * DAY)}}]})
    if path.startswith("/Users/u2/Items"):
        return Reply({"Items": [{"UserData": {"LastPlayedDate": stamp(NOW - DAY)}}]})
    if method == "GET" and path.startswith("/Users/"):
        return Reply({"Policy": {"EnableMediaPlayback": True, "X": 1}})
    if method == "POST" and path.endswith("/Policy"):
        policy_calls.append((path, body["EnableMediaPlayback"]))
        return Reply({})
    return Reply({})


rows = gq.usage(jelly, NOW)
check("quotas : seuls les comptes non administrateurs, 30 jours glissants", [(r["name"], r["played"]) for r in rows] == [("alice", 5), ("bob", 1)], str(rows))
check("quotas : rien sans quota", gq.tick(NOW, jelly, force=True) == [])
gq.set_quota("alice", 3)
SENT.clear()
check("quotas : dépassement -> alerte seulement, sans application", [a[0] for a in gq.tick(NOW, jelly, force=True)] == ["alerte"] and not policy_calls and SENT)
gq.set_enforce(True)
acts = gq.tick(NOW + 7200, jelly)
check("quotas : application -> suspension de la lecture", ("suspendu", "alice") in acts and policy_calls[-1] == ("/Users/u1/Policy", False))
check("quotas : un compte déjà sous quota n'est pas touché", all(a[1] != "bob" for a in acts))
gq.set_quota("alice", 10)
acts = gq.tick(NOW + 14400, jelly)
check("quotas : sous le quota, le compte suspendu par la console est rétabli", ("rétabli", "alice") in acts and policy_calls[-1] == ("/Users/u1/Policy", True))
check("quotas : une suspension faite à la main n'est jamais levée", raises(gq.resume, "bob", jelly))
check("quotas : valeurs invalides refusées", raises(gq.set_quota, "../x", 3) and raises(gq.set_quota, "alice", 0) and raises(gq.set_default, -1))

# --- 8. énergie -----------------------------------------------------------------------------------------------------------------------
en = ctl.energy()
check("énergie : conversion hdparm -S", en.hdparm_value(0) == 0 and en.hdparm_value(5) == 60 and en.hdparm_value(20) == 240 and en.hdparm_value(30) == 241 and en.hdparm_value(120) == 244)
st = os.stat(ctl.STORAGE)
major, minor = os.major(st.st_dev), os.minor(st.st_dev)
sysroot = root / "sys"
part = sysroot / "devices/pci/ata/block/sda/sda1"
part.mkdir(parents=True)
(part / "partition").write_text("1")
(sysroot / "dev/block").mkdir(parents=True)
(sysroot / "dev/block" / f"{major}:{minor}").symlink_to(part)
(sysroot / "block/sda/queue").mkdir(parents=True)
(sysroot / "block/sda/queue/rotational").write_text("1")
if major:
    check("énergie : disque physique d'un dossier (partition -> disque)", en.device_of(ctl.STORAGE, sysroot) == "sda" and en.rotational("sda", sysroot))
else:
    check("énergie : périphérique virtuel (major 0) = non mesurable", en.device_of(ctl.STORAGE, sysroot) is None)
en.volume_paths = lambda: {"local": ctl.STORAGE}
en.device_of = lambda path, sys_root=None: "sda"
en.rotational = lambda name, sys_root=None: name == "sda"
en.system_device = lambda: "sdb"
ran = []
check("énergie : réglage de veille appliqué par hdparm", en.set_standby("sda", 30, runner=lambda c, **k: ran.append(c) or subprocess.CompletedProcess(c, 0, "", ""))["minutes"] == 30
      and ran[-1] == ["hdparm", "-S", "241", "/dev/sda"] and en.settings()["standby"] == {"sda": 30})
en.system_device = lambda: "sda"
check("énergie : disque du système refusé", "système" in (raises(en.set_standby, "sda", 30, runner=None, apply=False) or ""))
en.system_device = lambda: "sdb"
check("énergie : disque inconnu, SSD, délai invalide refusés", raises(en.set_standby, "sdz", 30, apply=False) and raises(en.set_standby, "sda", 3, apply=False) and raises(en.set_standby, "../sda", 30, apply=False))
check("énergie : hdparm en échec -> erreur claire, réglage non retenu", "hdparm" in (raises(en.set_standby, "sda", 60, runner=lambda c, **k: subprocess.CompletedProcess(c, 1, "", "refus")) or "") and en.settings()["standby"] == {"sda": 30})
for h in range(10):
    en.sample(NOW + h * 3600, {"sda": (h * 2_000_000, h * 600_000 if h < 5 else 5 * 600_000 + (h - 5) * 100)})
idle, read = en.activity(en._read(en.FILE, {})["series"]["sda"])
check("énergie : part de repos et octets lus", idle is not None and 0.4 < idle < 0.6 and read > 0, f"{idle} {read}")
rep = en.report(NOW, spaces={"local": (4 * 10 ** 12, 10 ** 12)}, paths={"local": ctl.STORAGE})[0]
check("énergie : coût par To d'après les watts et la veille", rep["device"] == "sda" and rep["watts"] < 6 and rep["eur_tb_year"] is not None and rep["eur_tb_year"] < rep["eur_year"], str(rep))
(root / "proc/sys/kernel/random").mkdir(parents=True)
(root / "proc/sys/kernel/random/boot_id").write_text("boot-1\n")
ran.clear()
check("énergie : réapplication après redémarrage, une fois par démarrage", en.reapply(lambda c, **k: ran.append(c) or subprocess.CompletedProcess(c, 0, "", "")) == 1 and en.reapply(lambda c, **k: ran.append(c)) == 0 and len(ran) == 1)

# --- 9. optimisation des seeds : ratio du site fourni par une source externe -----------------------------------------------------
site = {"tracker.c411.org": {"ratio": 0.8, "required": 1.0, "danger": True}}
so = ctl.seedopt()
torrents = [{"hash": "a" * 40, "name": "x", "tracker": "https://tracker.c411.org/announce", "size": GB, "uploaded": 0, "downloaded": GB, "amount_left": 0, "state": "stalledUP",
             "num_complete": 20, "num_incomplete": 0, "upspeed": 0, "completion_on": NOW - 90 * DAY, "added_on": NOW - 90 * DAY}]
so.save_rules = None
history = {"days": {}}
res = so.analyse(torrents, history, so.settings(), NOW, {}, site)
check("seeds : ratio du site sous l'exigé -> torrents protégés + conseil", res["rows"][0]["class"] == "protégé" and "ratio du site" in res["trackers"][0]["advice"][0], str(res["rows"][0]["class"]))

# --- 10. ligne de commande ---------------------------------------------------------------------------------------------------------
env = dict(os.environ, SB_SRC=str(SRC))
for command in (["spacefore", "show"], ["libreport", "show"], ["integrity", "show"], ["subsgap", "show"], ["energy", "show"]):
    result = subprocess.run([sys.executable, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/seedboxctl.py"), *command], capture_output=True, text=True, env=env, timeout=60)
    check(f"CLI : {' '.join(command)}", result.returncode == 0 and result.stdout.strip().startswith("{"), (result.stderr or result.stdout)[-160:])

print("échecs :", bad)
sys.exit(1 if bad else 0)
