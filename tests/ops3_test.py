"""Dix nouveautés : journal des réglages, simulation de restauration, espace par dossier, rotation des secrets, qualité des médias,
contrôle des liens, bilan mensuel, partage d'un titre, surveillance du VPN, page maison (sans Docker, sans réseau)."""
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbops3"))
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


class Reply:
    def __init__(self, data=None, status=200):
        self.data, self.status = data, status
        self.ok = 200 <= status < 300
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self.data


# --- 1. journal des réglages -------------------------------------------------------------------------------------------------------
log = ctl.changelog()
(ctl.ETC / "alerts.json").write_text(json.dumps({"enabled": True, "ntfy": {"topic": "secret-topic", "token": "tk_old"}, "disk_percent": 90}))
(ctl.ETC / "emergency.json").write_text(json.dumps({"hash": "x"}))
check("journal : le code de secours n'est jamais suivi", "emergency.json" not in log.watched() and "alerts.json" in log.watched())
check("journal : premier passage = copies de départ, aucune entrée", log.tick(NOW) == 0 and log.events() == [])
check("journal : sans changement, rien", log.tick(NOW + 300) == 0)
(ctl.ETC / "alerts.json").write_text(json.dumps({"enabled": True, "ntfy": {"topic": "secret-topic", "token": "tk_new"}, "disk_percent": 80}))
os.utime(ctl.ETC / "alerts.json", (NOW + 400, NOW + 400))
ctl.audit().record("marie", "10.0.0.2", "POST /api/alerts/settings", "", "console", "ok", ts=NOW + 395)
check("journal : un changement crée une entrée", log.tick(NOW + 600) == 1 and len(log.events()) == 1)
event = log.events()[0]
check("journal : auteur déduit du journal d'audit", event["by"] == "marie" and event["file"] == "alerts.json", event)
info = log.detail(event["id"])
text = json.dumps(info, ensure_ascii=False)
check("journal : les lignes modifiées sont montrées", any("disk_percent" in str(line) for line in info["lines"]), text[:160])
check("journal : le jeton n'apparaît jamais en clair", "tk_new" not in text and "tk_old" not in text and "secret-topic" not in text, text[:200])
check("journal : copies conservées en lecture root seule", (log.DIR / "versions" / "alerts.json").stat().st_mode & 0o077 == 0)
check("journal : l'entrée la plus récente est annulable", log.listing()[0]["latest"] is True)
log.undo(event["id"], "marie", NOW + 700)
back = json.loads((ctl.ETC / "alerts.json").read_text())
check("journal : annuler remet la version d'avant", back["disk_percent"] == 90 and back["ntfy"]["token"] == "tk_old", back)
check("journal : l'annulation est une entrée annulable", log.events()[-1]["undo"] is True and log.tick(NOW + 800) == 0)
check("journal : une entrée plus ancienne n'est plus annulable avant la plus récente", raises(log.undo, event["id"], "marie", NOW + 900) is not None)
log.undo(log.events()[-1]["id"], "marie", NOW + 1000)
check("journal : annuler l'annulation rétablit la modification", json.loads((ctl.ETC / "alerts.json").read_text())["disk_percent"] == 80)
(ctl.ETC / "alerts.json").write_text(json.dumps({"enabled": True}))
os.utime(ctl.ETC / "alerts.json", (NOW + 1100, NOW + 1100))
log.tick(NOW + 1200)
check("journal : sans trace dans l'audit -> « hors console »", log.events()[-1]["by"] == "hors console")
for index in range(25):
    (ctl.ETC / "alerts.json").write_text(json.dumps({"enabled": True, "n": index}))
    log.tick(NOW + 2000 + index * 400)
check("journal : 20 versions au plus par fichier", len(list((log.DIR / "versions" / "alerts.json").iterdir())) <= 20)
check("journal : entrée inconnue refusée", raises(log.undo, "nope", "x") is not None and raises(log.detail, "nope") is not None)

# --- 2. simulation de restauration -------------------------------------------------------------------------------------------------
drill = ctl.restoredrill()
backup = ctl.backup()
backup.LOCAL_DIR.mkdir(parents=True, exist_ok=True)
(ctl.CONFIG / "radarr").mkdir(parents=True, exist_ok=True)
(ctl.CONFIG / "radarr" / "config.xml").write_text("<Config><ApiKey>abc123</ApiKey><UrlBase></UrlBase></Config>")
(ctl.CONFIG / "radarr" / "radarr.db").write_text("db")
(ctl.CONFIG / "radarr" / "MediaCover").mkdir(exist_ok=True)
(ctl.CONFIG / "radarr" / "MediaCover" / "big.jpg").write_text("x" * 100)
(ctl.CONFIG / "sonarr").mkdir(parents=True, exist_ok=True)
(ctl.CONFIG / "sonarr" / "config.xml").write_text("<Config><ApiKey>def456</ApiKey><UrlBase>/sonarr</UrlBase></Config>")
(ctl.ETC / "backup.key").write_text("clé-de-test\n")
plain = root / "plain.tar.gz"
with tarfile.open(plain, "w:gz") as tar:
    tar.add(ctl.CONFIG / "radarr", arcname=backup.rel(ctl.CONFIG / "radarr"))
    tar.add(ctl.CONFIG / "sonarr", arcname=backup.rel(ctl.CONFIG / "sonarr"))
archive = backup.LOCAL_DIR / "seedbox-20261101-030000.tar.gz.enc"
subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{ctl.ETC / 'backup.key'}", "-in", str(plain), "-out", str(archive)], check=True, capture_output=True)


class FakeDocker:
    def __init__(self, healthy=True, key_ok=True):
        self.calls, self.healthy, self.key_ok, self.names, self.volumes = [], healthy, key_ok, [], []

    def __call__(self, *args, timeout=60):
        self.calls.append(args)
        if args[0] == "inspect" and args[1] == "-f" and "Config.Image" in args[2]:
            return (0, "lscr.io/linuxserver/" + args[3] + ":latest") if args[3] in ("radarr", "sonarr") else (1, "")
        if args[0] == "ps":
            return 0, "seedbox-drill-old-111111\nautre\n"
        if args[0] == "run":
            self.names.append(args[args.index("--name") + 1])
            self.volumes.append(args[args.index("-v") + 1])
            return 0, "id"
        if args[0] == "exec" and args[-1].endswith("/ping"):
            return (0, '{"status":"OK"}') if self.healthy else (1, "")
        if args[0] == "exec" and "X-Api-Key" in " ".join(args):
            if not self.key_ok:
                return 1, "401"
            return 0, json.dumps([{"id": 1}, {"id": 2}, {"id": 3}])
        if args[0] == "inspect":
            return 0, "true" if self.healthy else "false"
        if args[0] == "logs":
            return 0, "erreur de base"
        return 0, ""


fake = FakeDocker()
report = drill.run(None, runner=fake, sleep=lambda s: None, root=root)
check("simulation : l'archive chiffrée est restaurée dans des conteneurs jetables, sans réseau", report["ok"] is True and len(report["items"]) == 2, report)
check("simulation : l'API répond avec la clé de la sauvegarde et compte les éléments", all(i["api"] and i["count"] == 3 for i in report["items"]), report["items"])
check("simulation : conteneurs sans réseau, mémoire bornée, étiquetés", all(("--network", "none") == (c[c.index("--network")], c[c.index("--network") + 1]) and "--memory" in c and "--label" in c
                                                                          for c in fake.calls if c[0] == "run"))
check("simulation : les conteneurs d'essai sont toujours supprimés", sum(1 for c in fake.calls if c[0] == "rm" and c[2].startswith("seedbox-drill-")) >= 2 and all(
    any(c[0] == "rm" and c[-1] == n for c in fake.calls) for n in fake.names))
check("simulation : un conteneur oublié d'une exécution interrompue est nettoyé", any(c[:3] == ("rm", "-f", "seedbox-drill-old-111111") for c in fake.calls))
check("simulation : la configuration restaurée est une copie (le dossier réel n'est pas monté)", all(str(ctl.CONFIG) not in v for v in fake.volumes), fake.volumes)
check("simulation : les images de pochettes ne sont pas copiées", not any("MediaCover" in v for v in fake.volumes))
check("simulation : rapport conservé", drill.last_report()["ok"] is True and drill.overview()["last"]["name"].endswith(".enc"))
sent.clear()
bad_run = drill.run(None, runner=FakeDocker(healthy=False), sleep=lambda s: None, root=root, wait=0)
check("simulation : sauvegarde qui ne démarre pas -> échec signalé", bad_run["ok"] is False and sent and "échoué" in sent[0][0], bad_run["items"][0]["detail"])
check("simulation : clé d'API refusée -> échec", drill.run(None, runner=FakeDocker(key_ok=False), sleep=lambda s: None, root=root)["ok"] is False)
check("simulation : mauvaise clé de chiffrement refusée", raises(drill.run, None, "mauvaise", None, FakeDocker(), lambda s: None, time.time, 5, root) is not None)
check("simulation : service absent du serveur ignoré", len(drill.run(None, only=["radarr"], runner=FakeDocker(), sleep=lambda s: None, root=root)["items"]) == 1)
check("simulation : nom de sauvegarde invalide refusé", raises(drill.run, "../../etc/passwd", runner=FakeDocker(), root=root) is not None)

# --- 3. espace par dossier ---------------------------------------------------------------------------------------------------------
usage = ctl.diskusage()
(ctl.STORAGE / "media" / "films").mkdir(parents=True)
(ctl.STORAGE / "media" / "series").mkdir(parents=True)
(ctl.STORAGE / "torrents").mkdir(parents=True)
(ctl.STORAGE / "lost+found").mkdir()
names = [k for k, _, _ in usage.folders()]
check("espace : dossiers du stockage, des médias et de la configuration listés", "du.media-films" in names and "du.stockage-torrents" in names and "du.config-radarr" in names, names)
check("espace : lost+found ignoré", not any("lost" in n for n in names))
sizes = {}
for day in range(20):
    stamp = NOW - (20 - day) * 86400
    values = usage.scan(stamp, measurer=lambda p, d=day: (10 << 30) + (d * (2 << 30) if p.name == "torrents" else d * (1 << 20)), docker=lambda: {"du.docker-images": 5 << 30})
rows = {r["key"]: r for r in usage.rows(NOW)}
check("espace : la mesure est enregistrée dans l'historique", "du.stockage-torrents" in rows and "du.docker-images" in rows)
check("espace : évolution par jour calculée", abs(rows["du.stockage-torrents"]["per_day"] - (2 << 30)) < (1 << 28), rows["du.stockage-torrents"]["per_day"])
check("espace : dossier stable -> croissance faible", rows["du.docker-images"]["per_day"] < (1 << 20))
check("espace : les mesures n'apparaissent pas dans l'historique machine", not any(k.startswith("du.") for k in ctl.metrics().history("30j", NOW)["series"]))
ctl.metrics().risky = lambda days, now=None: [{"path": "/mnt/st", "days": 10}]
alert = usage.findings(NOW)
check("espace : l'alerte nomme le dossier qui grossit", alert and "torrents" in list(alert.values())[0][0].lower(), alert)
ctl.metrics().risky = lambda days, now=None: []
check("espace : pas d'alerte sans risque de saturation", usage.findings(NOW) == {})
launched = []
first = usage.tick(NOW + 99999, launcher=lambda: launched.append(1))
usage.scan(NOW + 99999, measurer=lambda p: 1, docker=lambda: {})
check("espace : la mesure se lance en tâche de fond quand elle est due, pas deux fois de suite", first is True
      and usage.tick(NOW + 100100, launcher=lambda: launched.append(1)) is False and len(launched) == 1)

# --- 4. rotation des secrets -------------------------------------------------------------------------------------------------------
rotation = ctl.rotation()
ctl.ENV_FILE.write_text("")
ctl.env_set(RADARR_API_KEY="oldradarr0000000", CONSOLE_PROXY_SECRET="oldconsolesecret", SONARR_API_KEY="__AUTO__")
steps = []


class FakeApplier:
    def __init__(self, ok=True, apply_fails=False):
        self.ok, self.apply_fails = ok, apply_fails

    def write(self, var, value):
        steps.append(("write", var, value))
        ctl.env_set(**{var: value})

    def apply(self):
        steps.append(("apply",))
        if self.apply_fails and sum(1 for s in steps if s[0] == "apply") == 1:
            raise ctl.SeedboxError("provisionnement : échec")

    def render_only(self):
        steps.append(("render",))

    def verify(self, item):
        steps.append(("verify", item))
        return self.ok


rotation.rotate("radarr", "marie", FakeApplier(), NOW, new_value="newradarr1111111")
check("rotation : nouveau secret écrit puis appliqué puis vérifié", [s[0] for s in steps] == ["write", "apply", "verify"] and ctl.env_read()["RADARR_API_KEY"] == "newradarr1111111", steps)
check("rotation : historique daté et attribué", rotation.history()["radarr"]["by"] == "marie")
steps.clear()
err = raises(rotation.rotate, "radarr", "marie", FakeApplier(ok=False), NOW, "badkey0000000000")
check("rotation : vérification en échec -> ancien secret rétabli et réappliqué", err and ctl.env_read()["RADARR_API_KEY"] == "newradarr1111111"
      and [s[0] for s in steps] == ["write", "apply", "verify", "write", "apply"], (err, steps))
steps.clear()
raises(rotation.rotate, "radarr", "marie", FakeApplier(apply_fails=True), NOW, "badkey2222222222")
check("rotation : application en échec -> retour à l'ancien secret", ctl.env_read()["RADARR_API_KEY"] == "newradarr1111111")
steps.clear()
rotation.rotate("console", "marie", FakeApplier(), NOW + 5, new_value="newconsolesecret")
check("rotation : secret console = rendu seulement, sans redémarrage ni provisionnement", [s[0] for s in steps] == ["write", "render"] and ctl.env_read()["CONSOLE_PROXY_SECRET"] == "newconsolesecret", steps)
check("rotation : secret non renouvelable ou absent refusé", raises(rotation.rotate, "authelia", "x", FakeApplier()) is not None and raises(rotation.rotate, "sonarr", "x", FakeApplier()) is not None)
view = rotation.overview(NOW + 400 * 86400)
check("rotation : vue = ancienneté, présence", {i["id"]: i for i in view["items"]}["radarr"]["age_days"] == 400 and {i["id"]: i for i in view["items"]}["sonarr"]["present"] is False)
check("rotation : l'ancienne valeur n'est écrite nulle part", "oldradarr0000000" not in json.dumps(view) and "oldradarr0000000" not in (ctl.STATE / "rotation.json").read_text())

# --- 5. qualité des médias ---------------------------------------------------------------------------------------------------------
quality = ctl.mediaquality()
movies = [{"id": 1, "title": "Dune", "year": 2021, "monitored": True, "isAvailable": True, "hasFile": True, "runtime": 155,
           "movieFile": {"size": 3 << 30, "quality": {"quality": {"name": "WEBDL-1080p", "resolution": 1080}}}},
          {"id": 2, "title": "Alien", "year": 1979, "monitored": True, "isAvailable": True, "hasFile": True, "runtime": 117,
           "movieFile": {"size": 200 << 20, "quality": {"quality": {"name": "DVD", "resolution": 480}}}},
          {"id": 3, "title": "Absent", "year": 2020, "monitored": True, "isAvailable": True, "hasFile": False, "runtime": 90},
          {"id": 4, "title": "Futur", "year": 2030, "monitored": True, "isAvailable": False, "hasFile": False, "runtime": 90},
          {"id": 5, "title": "dune", "year": 2021, "monitored": False, "isAvailable": True, "hasFile": False, "runtime": 155}]
commands = []


def fake_arr(app, method, path, body=None):
    if method == "POST":
        commands.append((app, path, body))
        return Reply({"id": 1})
    if app == "radarr" and path == "/movie":
        return Reply(movies)
    if "/wanted/cutoff" in path:
        return Reply({"totalRecords": 2, "records": [{"id": 2, "title": "Alien", "year": 1979}, {"id": 7, "title": "X", "year": 2000}] if app == "radarr"
                      else [{"id": 31, "seasonNumber": 1, "episodeNumber": 2, "series": {"title": "Série"}}]})
    if app == "sonarr" and path == "/series":
        return Reply([{"id": 1, "statistics": {"episodeFileCount": 4}}, {"id": 2, "statistics": {"episodeFileCount": 0}}])
    if "/wanted/missing" in path:
        return Reply({"totalRecords": 5, "records": [{"id": 40, "seasonNumber": 2, "episodeNumber": 1, "series": {"title": "Série"}}]})
    return Reply([], 404)


ctl.container_running = lambda name: True
view = quality.overview(fake_arr)
radarr = view["apps"]["radarr"]
check("qualité : films sous le seuil, manquants (seulement sortis et suivis), doublons", radarr["below_cutoff"] == 2 and radarr["missing"] == 1 and radarr["duplicates"] == 1, radarr)
check("qualité : film très compressé repéré (Mo par minute)", radarr["low_rate"] == 1 and radarr["low_rate_sample"][0]["title"] == "Alien", radarr["low_rate_sample"])
check("qualité : séries sous le seuil et manquantes", view["apps"]["sonarr"]["below_cutoff"] == 2 or view["apps"]["sonarr"]["missing"] == 5, view["apps"]["sonarr"])
check("qualité : lecture seule (aucune commande envoyée)", commands == [])
done = quality.upgrade("radarr", 20, fake_arr, "marie")
check("qualité : la recherche d'une meilleure version part pour les titres sous le seuil", done["searched"] == 2 and commands[-1][2] == {"name": "MoviesSearch", "movieIds": [2, 7]}, commands)
quality.upgrade("sonarr", 5, fake_arr, "marie")
check("qualité : Sonarr cherche par épisode", commands[-1][2]["name"] == "EpisodeSearch" and commands[-1][2]["episodeIds"] == [31])
check("qualité : nombre borné et application connue", raises(quality.upgrade, "radarr", 500, fake_arr) is not None and raises(quality.upgrade, "lidarr", 5, fake_arr) is not None
      and raises(quality.upgrade, "radarr", 0, fake_arr) is not None)
check("qualité : application en panne -> erreur isolée", "radarr" in quality.overview(lambda *a, **k: Reply(None, 500))["errors"])

# --- 6. contrôle des liens ---------------------------------------------------------------------------------------------------------
links = ctl.linkcheck()
mode = {"client": "ok", "roots": "ok", "seerr": "ok"}


def link_arr(app, method, path, body=None):
    if path == "/applications":
        return Reply([{"id": 1, "name": "Radarr"}])
    if path == "/applications/testall":
        return Reply([{"id": 1, "name": "Radarr", "isValid": True}])
    if path == "/indexerstatus":
        return Reply([{"indexerId": 4, "disabledTill": "2999-01-01T00:00:00Z"}])
    if path == "/downloadclient":
        return Reply([{"id": 1}])
    if path == "/downloadclient/testall":
        if mode["client"] == "ok":
            return Reply([{"id": 1, "name": "qBittorrent", "isValid": True}])
        return Reply([{"id": 1, "name": "qBittorrent", "isValid": False, "validationFailures": [{"errorMessage": "Unable to connect"}]}], 400)
    if path == "/indexer":
        return Reply([{"id": 1}])
    if path == "/rootfolder":
        return Reply([{"path": "/data/media/films", "accessible": mode["roots"] == "ok"}])
    return Reply([], 404)


def link_seerr(method, path, body=None):
    if method == "GET":
        return Reply([{"name": "Radarr", "hostname": "radarr", "port": 7878, "apiKey": "k", "useSsl": False}])
    return Reply({"ok": True}) if mode["seerr"] == "ok" else Reply(None, 500)


def link_jellyfin(method, path, body=None):
    return Reply({"Version": "10.9"}) if "Info" in path else Reply([{"Name": "Films"}])


good = links.run(link_arr, link_seerr, link_jellyfin, running=lambda n: True)
check("liens : une pile saine ne signale que ce qui est déjà connu (indexeur désactivé par Prowlarr)", good["errors"] == 0 and good["problems"] == 1, [i for i in good["items"] if i["state"] != "ok"])
mode["client"] = "ko"
broken = links.run(link_arr, link_seerr, link_jellyfin, running=lambda n: True)
check("liens : client de téléchargement injoignable -> erreur avec le message de l'application", broken["errors"] >= 1 and any("Unable to connect" in i["detail"] for i in broken["items"]))
check("liens : un conseil de réparation accompagne l'erreur", any(i["state"] == "error" and i["hint"] for i in broken["items"]))
mode.update(client="ok", roots="ko", seerr="ko")
broken = links.run(link_arr, link_seerr, link_jellyfin, running=lambda n: True)
check("liens : dossier racine inaccessible et Seerr en échec détectés", sum(1 for i in broken["items"] if i["state"] == "error") >= 2)
check("liens : service arrêté -> ni test ni erreur", links.run(link_arr, link_seerr, link_jellyfin, running=lambda n: n == "jellyfin")["items"][0]["id"] == "jellyfin")
check("liens : application qui plante -> avertissement, pas d'exception", links.run(lambda *a, **k: (_ for _ in ()).throw(ctl.SeedboxError("refusé")), link_seerr, link_jellyfin, running=lambda n: n == "radarr")["problems"] >= 1)

# --- 7. bilan mensuel par personne -------------------------------------------------------------------------------------------------
people = ctl.peoplereport()
ctl.user_list = lambda: [{"login": "marie", "role": "user"}, {"login": "paul", "role": "user"}, {"login": "obs", "role": "observer"}, {"login": "sans-seerr", "role": "admin"}]
day = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(NOW - 5 * 86400))
old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(NOW - 60 * 86400))
data = {"marie": [{"createdAt": day, "status": 2, "media": {"status": 5, "title": "Secret"}}, {"createdAt": day, "status": 1, "media": {"status": 3}},
                  {"createdAt": old, "status": 2, "media": {"status": 5}}], "paul": [], "sans-seerr": None}
fetch = lambda login: data.get(login)  # noqa: E731
month = people.month("marie", NOW, fetch)
check("bilan : seulement les 30 derniers jours", month["requests"] == 2 and month["available"] == 1 and month["pending"] == 1, month)
check("bilan : des nombres, jamais de titre", "Secret" not in json.dumps(people.summary(NOW, fetch)) and "Secret" not in people.text_of(people.summary(NOW, fetch)))
summary = people.summary(NOW, fetch)
check("bilan : les observateurs n'y figurent pas, un compte inconnu de Seerr est indiqué", [p["login"] for p in summary] == ["marie", "paul", "sans-seerr"] and summary[-1]["requests"] is None, summary)
sent.clear()
check("bilan : désactivé par défaut, aucun envoi", people.settings()["enabled"] is False and people.tick(NOW, fetch) is False and not sent)
check("bilan : jour du mois borné", raises(people.save, {"day": 31}) is not None and raises(people.save, {"day": True}) is not None and raises(people.save, {"inconnu": 1}) is not None)
people.save({"enabled": True, "day": 15})
stamp = time.mktime((2026, 11, 15, 10, 0, 0, 0, 0, -1))
sent.clear()
check("bilan : envoyé le jour choisi après 9 h", people.tick(stamp, fetch) is True and len(sent) == 1 and "marie" in sent[0][1])
check("bilan : une seule fois par mois", people.tick(stamp + 3600, fetch) is False and len(sent) == 1)
check("bilan : pas un autre jour, pas avant 9 h", people.tick(stamp + 86400, fetch) is False and people.tick(time.mktime((2026, 12, 15, 7, 0, 0, 0, 0, -1)), fetch) is False)

# --- 8. partage d'un seul titre ----------------------------------------------------------------------------------------------------
share = ctl.titleshare()
media = ctl.STORAGE / "media"
movie_dir = media / "films" / "Dune (2021)"
movie_dir.mkdir(parents=True)
(movie_dir / "Dune.mkv").write_bytes(b"video" * 1000)
(movie_dir / "Dune.fr.srt").write_text("sous-titres")
(movie_dir / "poster.jpg").write_bytes(b"jpg")
calls = []


def share_arr(app, method, path, body=None):
    if path == "/movie/12":
        return Reply({"title": "Dune: Deuxième partie", "year": 2024, "path": "/data/media/films/Dune (2021)", "hasFile": True})
    if path == "/movie/13":
        return Reply({"title": "Dehors", "year": 2000, "path": "/etc", "hasFile": True})
    return Reply(None, 404)


def share_jellyfin(method, path, body=None):
    calls.append((method, path, body))
    return Reply({}, 204)


class FakeGuests:
    def __init__(self):
        self.created, self.revoked = [], []

    def create(self, **kw):
        self.created.append(kw)
        return {"id": "abcd1234", "token": "t", "path": "/invite/token", "expires": NOW + 7 * 86400}

    def revoke(self, invite_id):
        self.revoked.append(invite_id)

    def invites(self):
        return [{"id": "abcd1234", "accounts": ["invite1"]}]


guests = FakeGuests()
deleted = []
ctl.user_del = lambda login: deleted.append(login)
result = share.create("movie", 12, 7, "marie", share_arr, share_jellyfin, guests, media)
folder = media / "partages" / result["id"]
check("partage : liens durs des fichiers, sans copie (même inode)", (folder / "Dune.mkv").stat().st_ino == (movie_dir / "Dune.mkv").stat().st_ino and (folder / "Dune.mkv").stat().st_nlink == 2)
check("partage : sous-titres liés, affiches et fichiers annexes ignorés", (folder / "Dune.fr.srt").exists() and not (folder / "poster.jpg").exists())
check("partage : médiathèque Jellyfin dédiée sur le dossier de partage", calls and calls[0][0] == "POST" and "Partage" in calls[0][1] and calls[0][2]["LibraryOptions"]["PathInfos"][0]["Path"] == f"/data/media/partages/{result['id']}", calls)
check("partage : invitation à usage unique, profil limité à la médiathèque, sans téléchargement", guests.created[0]["uses"] == 1 and guests.created[0]["profile"]["library"] == f"Partage {result['id']}"
      and guests.created[0]["profile"]["downloads"] is False)
check("partage : le libellé de l'invitation passe le contrôle des invitations", ctl.guests().LABEL_RE.match(guests.created[0]["label"]) is not None, guests.created[0]["label"])
check("partage : profil de bibliothèque validé par les invitations", ctl.guests().clean_profile({"library": f"Partage {result['id']}"})["library"] == f"Partage {result['id']}"
      and raises(ctl.guests().clean_profile, {"library": "../../etc"}) is not None)
check("partage : listé", share.overview()["shares"][0]["title"].startswith("Dune"))
check("partage : titre hors du dossier des médias refusé", raises(share.create, "movie", 13, 7, "m", share_arr, share_jellyfin, FakeGuests(), media) is not None)
check("partage : durée et type bornés", raises(share.create, "movie", 12, 99, "m", share_arr, share_jellyfin, FakeGuests(), media) is not None
      and raises(share.create, "album", 12, 7, "m", share_arr, share_jellyfin, FakeGuests(), media) is not None)
before = len(share.load())
failing = lambda method, path, body=None: Reply({}, 500)  # noqa: E731
check("partage : échec de Jellyfin -> liens supprimés, rien d'enregistré", raises(share.create, "movie", 12, 7, "m", share_arr, failing, FakeGuests(), media) is not None
      and len(share.load()) == before and sorted(p.name for p in (media / "partages").iterdir()) == [result["id"]])
check("partage : les fichiers d'origine ne sont pas touchés par les échecs", (movie_dir / "Dune.mkv").read_bytes() == b"video" * 1000)
calls.clear()
check("partage : rien d'échu = rien supprimé", share.tick(time.time(), jellyfin=share_jellyfin, guests=guests, root=media) == 0 and folder.exists())
removed = share.tick(time.time() + 20 * 86400, jellyfin=share_jellyfin, guests=guests, root=media)
check("partage : à l'échéance, médiathèque et liens supprimés, original intact", removed == 1 and not folder.exists() and (movie_dir / "Dune.mkv").exists()
      and calls and calls[0][0] == "DELETE" and "Partage" in calls[0][1], calls)
check("partage : invitation révoquée et compte créé supprimé", guests.revoked == ["abcd1234"] and deleted == ["invite1"], (guests.revoked, deleted))
(movie_dir / "Dune.mkv").unlink()
(movie_dir / "Dune.fr.srt").unlink()
check("partage : titre sans fichier refusé", "aucun fichier" in (raises(share.create, "movie", 12, 7, "m", share_arr, share_jellyfin, FakeGuests(), media) or ""))
check("partage : identifiant de partage invalide refusé à la suppression", raises(share.remove, "../x", share_jellyfin, FakeGuests(), media) is not None)

policy = {}
ctl.jellyfin_user = lambda login: {"Id": "u1", "Policy": {"EnableAllFolders": True}} if login == "invite1" else None
ctl.seerr_user_id = lambda jid: 9
seerr_calls = []
ctl.seerr_api = lambda method, path, body=None: (seerr_calls.append((method, path, body)), Reply({}))[1]


def restrict_jellyfin(method, path, body=None):
    if method == "GET":
        return Reply([{"Name": "Partage 0123abcd", "ItemId": "folder-1"}, {"Name": "Films", "ItemId": "folder-2"}])
    policy.update(body)
    return Reply({}, 204)


warnings = share.restrict_policy("invite1", "Partage 0123abcd", restrict_jellyfin)
check("partage : le compte invité ne voit que la médiathèque du partage", warnings == [] and policy["EnableAllFolders"] is False and policy["EnabledFolders"] == ["folder-1"]
      and policy["EnableContentDownloading"] is False, (warnings, policy))
check("partage : aucune permission de demande dans Seerr", seerr_calls and seerr_calls[0][2] == {"permissions": 0}, seerr_calls)
check("partage : médiathèque inconnue ou nom invalide -> avertissement", share.restrict_policy("invite1", "Partage ffffffff", restrict_jellyfin)[0].startswith("Jellyfin")
      and share.restrict_policy("invite1", "Films", restrict_jellyfin) == ["médiathèque de partage invalide"])

# --- 9. surveillance du VPN --------------------------------------------------------------------------------------------------------
watch = ctl.vpnwatch()
ctl.qbit_mode = lambda: "vpn"
check("VPN : désactivée par défaut, rien n'est interrogé", watch.settings() == {"enabled": False, "auto_stop": False} and watch.tick(NOW, facts={}) is False and watch.findings() == {})
watch.save({"enabled": True})


class FakeRemote:
    def __init__(self):
        self.paused, self.resumed = 0, 0

    def pause(self):
        self.paused += 1
        return 4

    def resume(self):
        self.resumed += 1
        return 4


remote_ops = FakeRemote()
watch.tick(NOW, {"running": True, "exit": "203.0.113.5", "home": "198.51.100.7"}, remote_ops)
check("VPN : tunnel sain -> aucun problème", watch.findings() == {} and watch.overview()["exit"] == "203.0.113.5")
sent_events = []
ctl.notifications().record = lambda *a, **k: sent_events.append(a)
watch.tick(NOW + 300, {"running": True, "exit": "203.0.113.9", "home": "198.51.100.7"}, remote_ops)
check("VPN : changement d'adresse de sortie noté, sans alerte", sent_events and "changée" in sent_events[0][1] and watch.findings() == {} and watch.overview()["history"][0]["detail"].endswith("203.0.113.9"))
watch.tick(NOW + 600, {"running": True, "exit": "198.51.100.7", "home": "198.51.100.7"}, remote_ops)
leak = watch.findings()
check("VPN : adresse de sortie = adresse de la maison -> alerte critique", "vpn-fuite" in leak and leak["vpn-fuite"][2] == "critical" and watch.overview()["leak"] is True)
check("VPN : sans arrêt automatique, les torrents ne sont pas touchés", remote_ops.paused == 0)
watch.save({"auto_stop": True})
watch.tick(NOW + 900, {"running": True, "exit": "198.51.100.7", "home": "198.51.100.7"}, remote_ops)
check("VPN : fuite + arrêt automatique -> pause générale, une seule fois", remote_ops.paused == 1 and watch.overview()["stopped"] is True)
watch.tick(NOW + 1200, {"running": True, "exit": "198.51.100.7", "home": "198.51.100.7"}, remote_ops)
check("VPN : pas de seconde pause tant que la fuite dure", remote_ops.paused == 1)
watch.tick(NOW + 1500, {"running": True, "exit": "203.0.113.9", "home": "198.51.100.7"}, remote_ops)
check("VPN : tunnel de nouveau bon -> reprise des seuls torrents mis en pause", remote_ops.resumed == 1 and watch.overview()["stopped"] is False and watch.findings() == {})
for step in range(3):
    watch.tick(NOW + 2000 + step * 300, {"running": True, "exit": None, "home": "198.51.100.7"}, remote_ops)
    if step < 2:
        check(f"VPN : {step + 1} passage(s) sans adresse = pas encore d'alerte", "vpn-sans-trafic" not in watch.findings())
check("VPN : tunnel démarré sans trafic plusieurs fois de suite -> avertissement", "vpn-sans-trafic" in watch.findings())
check("VPN : tunnel arrêté = déjà couvert par les conteneurs, pas de doublon", (watch.tick(NOW + 5000, {"running": False, "exit": None, "home": ""}, remote_ops), "vpn-sans-trafic" not in watch.findings())[1])
ctl.qbit_mode = lambda: "direct"
check("VPN : sans VPN, rien à surveiller", watch.tick(NOW + 6000, {}, remote_ops) is False)
check("VPN : réglage inconnu refusé", raises(watch.save, {"x": 1}) is not None)

# --- 10. page maison ---------------------------------------------------------------------------------------------------------------
house = ctl.house()
check("maison : tout va bien -> ok", house.verdict({"down": [], "unhealthy": []}, [], 2, 40) == "ok")
check("maison : service arrêté, alerte critique ou disque presque plein -> rouge", house.verdict({"down": ["radarr"], "unhealthy": []}, [], 1, 10) == "bad"
      and house.verdict({"down": [], "unhealthy": []}, [{"level": "critical"}], 1, 10) == "bad" and house.verdict({"down": [], "unhealthy": []}, [], 1, 96) == "bad")
check("maison : alerte, service en difficulté, disque à 85 % ou sauvegarde de plus de 14 jours -> orange", house.verdict({"down": [], "unhealthy": ["x"]}, [], 1, 10) == "warn"
      and house.verdict({"down": [], "unhealthy": []}, [{"level": "warning"}], 1, 10) == "warn" and house.verdict({"down": [], "unhealthy": []}, [], 20, 10) == "warn"
      and house.verdict({"down": [], "unhealthy": []}, [], 1, 86) == "warn")
ctl.containers = lambda: {"radarr": {"state": "running", "health": "healthy"}, "sonarr": {"state": "exited", "health": ""}, "jellyfin": {"state": "running", "health": "unhealthy"}}
ctl.health().expected_services = lambda env: ["radarr", "sonarr", "jellyfin"]
ctl.telecommande().overview = lambda qbit=None: {"alerts": [{"title": "Disque", "level": "warning"}], "alerts_total": 1, "vacation": {"active": False},
                                                 "downloads": {"available": True, "active": 2, "paused": 0, "down": 1000, "up": 10}}
ctl.sysinfo().disks = lambda: [{"label": "Stockage", "path": "/mnt/st", "total": 100 << 30, "used": 90 << 30, "percent": 90}]
state = house.overview(NOW)
check("maison : agrège services, alertes, téléchargements, disque, sauvegarde", state["services"]["running"] == 2 and state["services"]["down"] == ["sonarr"] and state["alerts_total"] == 1
      and state["downloads"]["active"] == 2 and state["disk"]["percent"] == 90 and state["backup"]["count"] >= 1 and state["verdict"] == "bad", state)
ctl.telecommande().overview = lambda qbit=None: (_ for _ in ()).throw(ctl.SeedboxError("panne"))
check("maison : une source en panne n'empêche pas d'afficher le reste", house.overview(NOW)["services"]["total"] == 3)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
