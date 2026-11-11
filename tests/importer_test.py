"""Import d'une autre seedbox : sauvegardes Radarr/Sonarr, torrents qBittorrent, chemins, retour arrière (sans Docker)."""
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbimp"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import importer as im  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


def raises(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ctl.SeedboxError as error:
        return str(error)
    return None


ctl.STATE.mkdir(parents=True, exist_ok=True)
im.IMPORT_DIR.mkdir(parents=True)
for app in ("radarr", "sonarr", "prowlarr"):
    (ctl.CONFIG / app).mkdir(parents=True)
(ctl.CONFIG / "qbittorrent" / "qBittorrent" / "BT_backup").mkdir(parents=True)

# --- doubles -------------------------------------------------------------------------------------------------------------
calls, state = [], {"wait_fail": False, "containers": {"radarr": {}, "sonarr": {}, "prowlarr": {}, "qbittorrent": {}}}
ctl.compose = lambda *a, **k: calls.append(a)
ctl.containers = lambda: state["containers"]
ctl.qbit_service = lambda: "qbittorrent-direct"
ctl.provision_arr = lambda: calls.append(("provision_arr",))
ctl.provision_apps = lambda: calls.append(("provision_apps",))
ctl.env_read = lambda: {"PUID": str(os.getuid()), "PGID": str(os.getgid())}


def fake_wait(name, port, path="/", timeout=600):
    if state["wait_fail"]:
        raise ctl.SeedboxError("injoignable")
    return True


ctl.wait_http = fake_wait


class FakeBackup:
    @staticmethod
    def create(label=""):
        calls.append(("backup", label))
        return "seedbox-x.tar.gz.enc"


ctl.backup = lambda: FakeBackup


class FakeShares:
    @staticmethod
    def location(role):
        return {"movies": "/shares/films", "tv": "/data/media/tv"}.get(role)


ctl.shares = lambda: FakeShares


def make_db(path, table, rows, roots):
    db = sqlite3.connect(path)
    db.execute(f'CREATE TABLE "{table}" (Id INTEGER PRIMARY KEY, Title TEXT, Path TEXT)')
    db.execute("CREATE TABLE RootFolders (Id INTEGER PRIMARY KEY, Path TEXT)")
    db.execute("CREATE TABLE DownloadClients (Id INTEGER PRIMARY KEY, Name TEXT)")
    db.executemany(f'INSERT INTO "{table}" (Title, Path) VALUES (?, ?)', rows)
    db.executemany("INSERT INTO RootFolders (Path) VALUES (?)", [(r,) for r in roots])
    db.execute("INSERT INTO DownloadClients (Name) VALUES ('Ancien qBit')")
    db.commit()
    db.close()


def make_zip(app, table, rows, roots, name=None, mtime=None):
    tmp = Path(tempfile.mkdtemp(prefix="sbdb")) / f"{app}.db"
    make_db(tmp, table, rows, roots)
    target = im.IMPORT_DIR / "Backups" / (name or f"{app}_backup_v5_2026.zip")
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w") as z:
        z.write(tmp, f"{app}.db")
        z.writestr("config.xml", "<Config><ApiKey>VIEILLECLE</ApiKey></Config>")
    if mtime:
        os.utime(target, (mtime, mtime))
    return target


# --- chemins -------------------------------------------------------------------------------------------------------------------------
m = im.clean_map([("/home/old/media/", "/data/media/"), ("D:\\Films", "/shares/films"), ("/home/old/media/tv", "/data/media/series")])
check("table triée : préfixe le plus long d'abord", m[0][0] == "/home/old/media/tv", str(m))
check("remap simple", im.remap("/home/old/media/tv/Show/S01", m) == "/data/media/series/Show/S01")
check("remap dossier racine", im.remap("/home/old/media", [("/home/old/media", "/x")]) == "/x")
check("remap Windows", im.remap("D:\\Films\\Alien (1979)", m) == "/shares/films/Alien (1979)")
check("remap casse Windows", im.remap("d:/films/x", m) == "/shares/films/x")
check("pas de faux préfixe", im.remap("/home/old/mediatheque/x", m) is None)
check("aucune correspondance", im.remap("/ailleurs/x", m) is None)
check("racine refusée", raises(im.clean_map, [("/", "/data")]) is not None)
check("nouveau chemin relatif refusé", raises(im.clean_map, [("/a", "data")]) is not None)
check("nouveau chemin avec .. refusé", raises(im.clean_map, [("/a", "/data/../etc")]) is not None)
check("ligne vide ignorée", im.clean_map([("", "")]) == [])
check("dictionnaires acceptés", im.clean_map([{"from": "/a", "to": "/b"}]) == [("/a", "/b")])

# --- bencode ------------------------------------------------------------------------------------------------------------------------
sample = {b"save_path": b"/old/dl", b"qBt-category": b"radarr", b"file_priority": [1, 0, 1], b"x": {b"a": 5}, b"paused": 0}
check("bencode aller-retour", im.bdecode(im.bencode(sample))[0] == sample)
raw = im.bencode(sample)
out, unmatched = im.convert_resume(raw, [("/old", "/data")])
new = im.load_resume(out)
check("fastresume : chemin converti", new[b"save_path"] == b"/data/dl" and not unmatched)
check("fastresume : en pause", new[b"paused"] == 1 and new[b"auto_managed"] == 0)
check("fastresume : reste intact", new[b"qBt-category"] == b"radarr" and new[b"file_priority"] == [1, 0, 1])
out2, unmatched2 = im.convert_resume(raw, [("/autre", "/data")])
check("fastresume : chemin non converti signalé", unmatched2 == ["/old/dl"] and im.load_resume(out2)[b"save_path"] == b"/old/dl")
for broken in (b"", b"i12", b"d3:abc", b"x", raw + b"zz", b"li1e"):
    try:
        im.load_resume(broken)
        check(f"bencode invalide refusé ({broken[:6]!r})", False)
    except (ValueError, IndexError):
        check(f"bencode invalide refusé ({broken[:6]!r})", True)

# --- scan ------------------------------------------------------------------------------------------------------------------------------
check("dossier vide : rien", im.scan()["items"] == [])
make_zip("radarr", "Movies", [("Alien", "/home/old/media/movies/Alien"), ("Heat", "/ailleurs/Heat")], ["/home/old/media/movies"], mtime=1000000)
newest = make_zip("radarr", "Movies", [("Alien", "/home/old/media/movies/Alien"), ("Heat", "/ailleurs/Heat"), ("Brazil", "/home/old/media/movies/Brazil")],
                  ["/home/old/media/movies"], name="radarr_backup_v5_recent.zip", mtime=2000000)
make_zip("sonarr", "Series", [("Lost", "/home/old/media/tv/Lost")], ["/home/old/media/tv"])
make_zip("prowlarr", "Indexers", [("YGG", "")], [])
# qBittorrent : dossier BT_backup
bt = im.IMPORT_DIR / "qbit" / "BT_backup"
bt.mkdir(parents=True)
h1, h2, h3 = "a" * 40, "b" * 40, "c" * 40
torrent = im.bencode({b"announce": b"http://t", b"info": {b"name": b"x", b"length": 1, b"piece length": 16384, b"pieces": b"0" * 20}})
for h, path in ((h1, b"/home/old/media/dl/a"), (h2, b"D:\\Torrents\\b")):
    (bt / f"{h}.torrent").write_bytes(torrent)
    (bt / f"{h}.fastresume").write_bytes(im.bencode({b"save_path": path, b"qBt-category": b"radarr", b"paused": 0}))
(bt / f"{h3}.torrent").write_bytes(torrent)                       # sans fastresume
(bt / "readme.txt").write_text("x")
result = im.scan()
ids = [i["id"] for i in result["items"]]
check("éléments trouvés", ids == ["arr:radarr", "arr:sonarr", "arr:prowlarr", "qbit"], str(ids))
radarr = result["items"][0]
check("le plus récent est retenu", radarr["file"].endswith("radarr_backup_v5_recent.zip") and radarr["count"] == 3)
check("dossiers de médias décrits", radarr["folders"] == ["/home/old/media/movies"] and radarr["clients"] == ["Ancien qBit"])
check("qBittorrent décrit", result["items"][3]["torrents"] == 3 and result["items"][3]["kind"] == "dir")
check("dossiers du serveur proposés", result["targets"] == {"movies": "/shares/films", "tv": "/data/media/tv"})
check("lidarr absent : pas listé", "arr:lidarr" not in ids)
bad_zip = im.IMPORT_DIR / "Backups" / "lidarr_backup_v1.zip"          # sauvegarde illisible
bad_zip.write_bytes(b"pas un zip")
state["containers"]["lidarr"] = {}
lid = [i for i in im.scan()["items"] if i["id"] == "arr:lidarr"]
check("sauvegarde corrompue : signalée", lid and lid[0]["valid"] is False and "illisible" in lid[0]["problem"])
del state["containers"]["lidarr"]
check("sauvegarde illisible refusée au lancement", "illisible" in (raises(im.run, ["arr:lidarr"]) or ""))
bad_zip.unlink()

# --- zip piégé : pas de sortie du dossier ---------------------------------------------------------------------------------------
trap = im.IMPORT_DIR / "sonarr_backup_piege.zip"
with zipfile.ZipFile(trap, "w") as z:
    z.writestr("../sonarr.db", "x")
    z.writestr("/abs/sonarr.db", "x")
im.scan()
check("zip piégé ignoré", not (im.IMPORT_DIR.parent / "sonarr.db").exists() and Path("/abs/sonarr.db").exists() is False)
trap.unlink()

# --- simulation --------------------------------------------------------------------------------------------------------------------------
live_db = ctl.CONFIG / "radarr" / "radarr.db"
make_db(live_db, "Movies", [("Neuf", "/data/media/movies/Neuf")], ["/data/media/movies"])
calls.clear()
notes = im.run(["arr:radarr", "qbit"], [("/home/old/media/movies", "/shares/films"), ("/home/old/media/dl", "/data/dl")], dry=True)
check("simulation : rien arrêté, pas de sauvegarde", not calls)
check("simulation : décrit les chemins", any("3 chemin(s) converti(s)" in n for n in notes) and any("2 torrent(s) à ajouter" in n for n in notes), str(notes))
check("simulation : chemin sans correspondance signalé", any("sans correspondance" in n and "D:\\Torrents\\b" in n for n in notes), str(notes))
check("simulation : base intacte", sqlite3.connect(live_db).execute("SELECT Title FROM Movies").fetchall() == [("Neuf",)])
check("simulation : BT_backup intact", not list((ctl.CONFIG / "qbittorrent" / "qBittorrent" / "BT_backup").iterdir()))

# --- refus ---------------------------------------------------------------------------------------------------------------------------------
check("identifiant invalide refusé", raises(im.run, ["../etc"]) is not None)
check("identifiant inconnu refusé", "introuvable" in (raises(im.run, ["arr:readarr"]) or ""))
check("aucun identifiant refusé", raises(im.run, []) is not None)
state["containers"].pop("sonarr")
check("application absente refusée", "pas installé" in (raises(im.run, ["arr:sonarr"]) or ""))
state["containers"]["sonarr"] = {}

# --- installation réelle --------------------------------------------------------------------------------------------------------------------
calls.clear()
notes = im.run(["arr:radarr", "qbit"], [("/home/old/media/movies", "/shares/films"), ("/home/old/media/dl", "/data/dl"), ("D:\\Torrents", "/data/dl")])
check("sauvegarde complète d'abord", calls[0] == ("backup", "avantimport"), str(calls[:2]))
check("radarr arrêté puis redémarré", ("stop", "radarr") in [c[:2] for c in calls] and ("start", "radarr") in [c[:2] for c in calls])
check("qBittorrent arrêté puis redémarré", ("stop", "qbittorrent-direct") in [c[:2] for c in calls] and ("start", "qbittorrent-direct") in [c[:2] for c in calls])
check("liaisons refaites", ("provision_arr",) in calls and ("provision_apps",) in calls)
db = sqlite3.connect(live_db)
rows = dict(db.execute("SELECT Title, Path FROM Movies"))
check("base importée", set(rows) == {"Alien", "Heat", "Brazil"})
check("chemins convertis", rows["Alien"] == "/shares/films/Alien" and rows["Brazil"] == "/shares/films/Brazil")
check("chemin hors table inchangé", rows["Heat"] == "/ailleurs/Heat")
check("dossier racine converti", db.execute("SELECT Path FROM RootFolders").fetchone()[0] == "/shares/films")
db.close()
saved = list((ctl.CONFIG / "radarr").glob("avant-import-*"))
check("ancienne base gardée", len(saved) == 1 and (saved[0] / "radarr.db").exists() and
      sqlite3.connect(saved[0] / "radarr.db").execute("SELECT Title FROM Movies").fetchall() == [("Neuf",)])
check("config.xml non touché", not (ctl.CONFIG / "radarr" / "config.xml").exists())
folder = ctl.CONFIG / "qbittorrent" / "qBittorrent" / "BT_backup"
check("torrents copiés", sorted(p.name for p in folder.iterdir()) == sorted([f"{h1}.torrent", f"{h1}.fastresume", f"{h2}.torrent", f"{h2}.fastresume"]))
r1, r2 = im.load_resume((folder / f"{h1}.fastresume").read_bytes()), im.load_resume((folder / f"{h2}.fastresume").read_bytes())
check("torrents : chemins convertis et en pause", r1[b"save_path"] == b"/data/dl/a" and r2[b"save_path"] == b"/data/dl/b" and r1[b"paused"] == 1)
check("torrent sans reprise ignoré", any("1 ignoré" in n for n in notes), str(notes))
check("historique", im.history()[-1]["items"] == ["arr:radarr", "qbit"] and im.overview()["history"])

# --- un torrent déjà présent n'est jamais remplacé -----------------------------------------------------------------------------------------
(folder / f"{h1}.fastresume").write_bytes(im.bencode({b"save_path": b"/modifie", b"paused": 0}))
notes = im.run(["qbit"], [("/home/old/media/dl", "/data/dl")])
check("torrent existant conservé", im.load_resume((folder / f"{h1}.fastresume").read_bytes())[b"save_path"] == b"/modifie" and any("2 déjà présent" in n for n in notes), str(notes))

# --- base refusée par l'application : retour arrière ----------------------------------------------------------------------------------------
before = sqlite3.connect(ctl.CONFIG / "sonarr" / "sonarr.db").execute("SELECT 1").fetchone() if (ctl.CONFIG / "sonarr" / "sonarr.db").exists() else None
make_db(ctl.CONFIG / "sonarr" / "sonarr.db", "Series", [("Ancienne", "/data/media/tv/Ancienne")], [])
state["wait_fail"] = True
calls.clear()
message = raises(im.run, ["arr:sonarr"], [("/home/old/media/tv", "/data/media/tv")])
state["wait_fail"] = False
check("échec expliqué", message and "rien n'a été changé" in message, str(message))
check("ancienne base remise", sqlite3.connect(ctl.CONFIG / "sonarr" / "sonarr.db").execute("SELECT Title FROM Series").fetchall() == [("Ancienne",)])
check("sonarr redémarré après retour", [c[:2] for c in calls if c and c[0] in ("stop", "start")].count(("start", "sonarr")) == 2)

# --- fin ---------------------------------------------------------------------------------------------------------------------------------------
check("overview", im.overview()["apps"] == sorted(im.ARR) and im.overview()["items"])
removed = im.clear()
check("dossier d'import vidé", removed >= 2 and not list(im.IMPORT_DIR.iterdir()) and not im.SCAN_FILE.exists())

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
