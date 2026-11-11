"""Assistant de nettoyage : analyse, garde-fous, corbeille, restauration, vidage (sans Docker ni qBittorrent réel)."""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbcln"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import cleanup as cl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


MB = 1024 * 1024
ST = ctl.STORAGE
DAY = 86400
now = time.time()


def make(path, size=1000, age_days=0, content=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else os.urandom(size))
    stamp = now - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


for folder in (ctl.STATE, ctl.BASE, ST / "downloads", ST / "media/movies", ST / "media/tv"):
    folder.mkdir(parents=True, exist_ok=True)
# téléchargements : un torrent actif, un orphelin ancien, un orphelin récent, un déjà importé (lien dur), un partiel abandonné
make(ST / "downloads/radarr/Actif.2024/film.mkv", 5000, 90)
make(ST / "downloads/radarr/Orphelin.2020/film.mkv", 8000, 120)
make(ST / "downloads/radarr/Orphelin.2020/film.nfo", 100, 120)
make(ST / "downloads/radarr/Recent.2026/film.mkv", 3000, 2)
make(ST / "downloads/sonarr/Importe.S01/ep1.mkv", 4000, 200)
(ST / "media/tv/Serie").mkdir(parents=True)
os.link(ST / "downloads/sonarr/Importe.S01/ep1.mkv", ST / "media/tv/Serie/ep1.mkv")
make(ST / "downloads/incomplete/Abandon/x.mkv.!qB", 2000, 60)
make(ST / "downloads/vieux.part", 1500, 80)
make(ST / "downloads/fichier-orphelin.iso", 6000, 100)
# médiathèque : deux copies identiques (50 Mo), un lien dur (non doublon), un fichier différent de même taille
big = os.urandom(60 * MB)
make(ST / "media/movies/Film (2020)/Film.mkv", content=big)
make(ST / "media/movies/Film (2020) copie/Film.mkv", content=big, age_days=0)
os.link(ST / "media/movies/Film (2020)/Film.mkv", ST / "media/movies/Film (2020)/Film.lien.mkv")
make(ST / "media/movies/Autre/Autre.mkv", content=os.urandom(60 * MB))
(ST / "media/movies/Vide").mkdir()
os.utime(ST / "media/movies/Vide", (now - 5 * DAY, now - 5 * DAY))
(ST / "media/movies/Recent-vide").mkdir()
make(ctl.CONFIG / "jellyfin/transcodes/ancien.ts", 4000, 3)
make(ctl.CONFIG / "jellyfin/transcodes/en-cours.ts", 4000, 0)

# --- 1. analyse avec qBittorrent -----------------------------------------------------------------------------------------
cl.active_torrent_paths = lambda: {ST / "downloads/radarr/Actif.2024", ST / "downloads/radarr/Recent.2026"}
cl.docker_dangling = lambda: [{"id": cl.item_id("docker", "images"), "category": "docker", "path": "docker", "name": "2 image(s)", "size": 300 * MB,
                               "mtime": int(now), "reason": "images sans étiquette"}]
result = cl.scan()
by = {c: [i for i in result["items"] if i["category"] == c] for c in cl.CATEGORIES}
names = lambda cat: sorted(i["name"] for i in by[cat])  # noqa: E731
check("orphelins : seulement ce qui n'est plus suivi, ancien, sans lien dur", names("orphan") == ["Orphelin.2020", "fichier-orphelin.iso"], names("orphan"))
check("orphelin : taille du dossier entier", next(i for i in by["orphan"] if i["name"] == "Orphelin.2020")["size"] == 8100)
check("torrent actif et récent jamais proposés", not any("Actif" in i["path"] or "Recent.2026" in i["path"] for i in result["items"]))
check("fichier déjà en bibliothèque (lien dur) jamais proposé", not any("Importe" in i["path"] for i in result["items"]))
check("partiels abandonnés", names("partial") == ["Abandon", "vieux.part"], names("partial"))
check("doublons : une seule copie proposée, la plus récente", len(by["duplicate"]) == 1 and by["duplicate"][0]["path"].endswith("Film (2020) copie/Film.mkv")
      and by["duplicate"][0]["keep"].endswith("Film (2020)/Film.mkv"), by["duplicate"])
check("doublons : lien dur et fichier différent ignorés", not any("lien" in i["name"] or "Autre" in i["path"] for i in by["duplicate"]))
check("dossiers vides : ancien seulement, jamais la racine", names("empty_dir") == ["Vide"], names("empty_dir"))
check("transcodage : fichier ancien seulement", names("transcode") == ["ancien.ts"], names("transcode"))
check("docker : images inutiles", len(by["docker"]) == 1)
overview = cl.overview()
check("vue d'ensemble : total récupérable par catégorie", overview["total"] == sum(i["size"] for i in result["items"]) and
      {c["id"] for c in overview["categories"]} == set(cl.CATEGORIES), overview["total"])
check("l'analyse ne supprime rien", (ST / "downloads/radarr/Orphelin.2020/film.mkv").exists() and (ST / "media/movies/Vide").exists())

# --- 2. qBittorrent injoignable : pas d'orphelins (on ne sait pas ce qui est actif) ---------------------------------------------------
cl.active_torrent_paths = lambda: None
unknown = cl.scan()
check("qBittorrent injoignable : orphelins et partiels non analysés, avec explication",
      not any(i["category"] in ("orphan", "partial") for i in unknown["items"]) and unknown["notes"] and "qBittorrent" in unknown["notes"][0], unknown["notes"])
cl.active_torrent_paths = lambda: {ST / "downloads/radarr/Actif.2024", ST / "downloads/radarr/Recent.2026"}
result = cl.scan()
by = {c: [i for i in result["items"] if i["category"] == c] for c in cl.CATEGORIES}

# --- 3. corbeille -------------------------------------------------------------------------------------------------------------------------------
orphan = next(i for i in by["orphan"] if i["name"] == "Orphelin.2020")
dup = by["duplicate"][0]
empty_dir = by["empty_dir"][0]
transcode = by["transcode"][0]
out = cl.trash([orphan["id"], dup["id"], empty_dir["id"]])
check("corbeille : éléments déplacés", len(out["moved"]) == 3 and not Path(orphan["path"]).exists() and not Path(dup["path"]).exists()
      and not Path(empty_dir["path"]).exists())
stored = [r for r in cl.trash_load()]
check("corbeille : dans le dossier .corbeille du stockage", all(r["trash"].startswith(str(ST / ".corbeille") + "/") and Path(r["trash"]).exists() for r in stored))
check("corbeille : le contenu est intact (déplacement, pas copie)", (Path(stored[0]["trash"]).exists()))
check("corbeille : éléments retirés de l'analyse", not any(i["id"] in out["moved"] for i in cl.read_json(cl.SCAN_FILE)["items"]))
check("corbeille : l'original à conserver est intact", (ST / "media/movies/Film (2020)/Film.mkv").exists())
cl.trash([transcode["id"]])
check("transcodage : supprimé directement (temporaire)", not Path(transcode["path"]).exists() and (ctl.CONFIG / "jellyfin/transcodes/en-cours.ts").exists())
after = cl.scan()
check("nouvelle analyse : corbeille non analysée, rien de neuf", not any(".corbeille" in i["path"] for i in after["items"]))

# --- 4. garde-fous ------------------------------------------------------------------------------------------------------------------------
fresh = cl.scan()
partial = next(i for i in fresh["items"] if i["name"] == "vieux.part")
make(partial["path"], 1500, 80)
os.utime(partial["path"], (now - 1, now - 1))
try:
    cl.trash([partial["id"]])
    check("fichier modifié depuis l'analyse refusé", False)
except ctl.SeedboxError as error:
    check("fichier modifié depuis l'analyse refusé", "changé" in str(error), error)
check("fichier modifié : rien déplacé", Path(partial["path"]).exists())
fresh = cl.scan()
orphan_iso = next(i for i in fresh["items"] if i["name"] == "fichier-orphelin.iso")
os.link(orphan_iso["path"], ST / "media/movies/lien-iso.iso")
try:
    cl.trash([orphan_iso["id"]])
    check("orphelin devenu lié à la bibliothèque refusé", False)
except ctl.SeedboxError as error:
    check("orphelin devenu lié à la bibliothèque refusé", "lié" in str(error), error)
(ST / "media/movies/lien-iso.iso").unlink()
# évasion : une analyse falsifiée pointant hors du stockage
scan_data = cl.read_json(cl.SCAN_FILE)
outside = root / "secret.txt"
outside.write_text("secret")
forged = {"id": "aaaaaaaaaaaa", "category": "orphan", "path": str(outside), "name": "secret.txt", "size": 6, "mtime": int(outside.stat().st_mtime), "reason": ""}
scan_data["items"].append(forged)
cl.write_json(cl.SCAN_FILE, scan_data)
try:
    cl.trash(["aaaaaaaaaaaa"])
    check("chemin hors du stockage refusé", False)
except ctl.SeedboxError as error:
    check("chemin hors du stockage refusé", "en dehors" in str(error), error)
check("chemin hors du stockage : fichier intact", outside.exists())
link = ST / "downloads/lien-symbolique"
link.symlink_to(outside)
forged2 = {**forged, "id": "bbbbbbbbbbbb", "path": str(link), "name": "lien-symbolique"}
scan_data["items"].append(forged2)
cl.write_json(cl.SCAN_FILE, scan_data)
try:
    cl.trash(["bbbbbbbbbbbb"])
    check("lien symbolique refusé", False)
except ctl.SeedboxError:
    check("lien symbolique refusé", True)
link.unlink()
for bad_ids in (["../../etc"], [], ["zz"], ["0123456789ab"]):
    try:
        cl.trash(bad_ids)
        check(f"identifiants invalides refusés {bad_ids}", False)
    except ctl.SeedboxError:
        check(f"identifiants invalides refusés {bad_ids}", True)
root_dir = {**forged, "id": "cccccccccccc", "category": "empty_dir", "path": str(ST / "downloads"), "name": "downloads", "size": 0}
(ST / "downloads/vide-test").mkdir()
scan_data["items"].append(root_dir)
cl.write_json(cl.SCAN_FILE, scan_data)
try:
    cl.trash(["cccccccccccc"])
    check("racine du dossier de téléchargement jamais déplacée", False)
except ctl.SeedboxError:
    check("racine du dossier de téléchargement jamais déplacée", (ST / "downloads").exists())

# --- 5. restauration -----------------------------------------------------------------------------------------------------------------------------
record = next(r for r in cl.trash_load() if r["category"] == "duplicate")
cl.restore([record["id"]])
check("restauration : retour à l'emplacement d'origine", Path(record["original"]).exists() and not Path(record["trash"]).exists()
      and all(r["id"] != record["id"] for r in cl.trash_load()))
make(record["original"], 10, 0)
record2 = next(r for r in cl.trash_load() if r["category"] == "orphan")
make(record2["original"] + "-occupe", 10)
Path(record2["original"]).mkdir(parents=True, exist_ok=True)
try:
    cl.restore([record2["id"]])
    check("restauration : emplacement occupé refusé", False)
except ctl.SeedboxError as error:
    check("restauration : emplacement occupé refusé", "occupé" in str(error), error)
Path(record2["original"]).rmdir()

# --- 6. vidage et expiration -----------------------------------------------------------------------------------------------------------------------
log = cl.trash_load()
log[0]["at"] = int(now - 20 * DAY)
cl.write_json(cl.TRASH_FILE, log)
out = cl.purge_expired()
check("expiration : seuls les éléments de plus de 14 jours partent", out["removed"] == 1 and len(cl.trash_load()) == len(log) - 1, out)
rest = cl.trash_load()
out = cl.empty()
check("vidage complet de la corbeille", out["removed"] == len(rest) and not cl.trash_load() and not any((ST / ".corbeille").rglob("*.mkv")), out)
check("vidage : le dossier .corbeille ne garde pas d'éléments", not [p for p in (ST / ".corbeille").rglob("*") if p.is_file()])

# --- 7. docker --------------------------------------------------------------------------------------------------------------------------------------
pruned = []
cl.docker_prune = lambda: pruned.append(1)
fresh = cl.scan()
docker = next(i for i in fresh["items"] if i["category"] == "docker")
cl.trash([docker["id"]])
check("docker : élagage lancé (pas de corbeille)", pruned == [1])

# --- 8. routes de la console -----------------------------------------------------------------------------------------------------------------------------
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/console"))
import server  # noqa: E402

ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret"}
started = []
server.start_job = lambda title, argv, stdin=None: started.append((title, argv)) or "0123456789ab"
console = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=console.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{console.server_port}"


def call(path, body=None, groups="admins"):
    headers = {"X-Seedbox-Proxy-Secret": "secret", "Remote-Groups": groups}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


code, data = call("/api/cleanup")
check("console : vue d'ensemble", code == 200 and data["categories"] and "trash" in data)
check("console : analyse", call("/api/cleanup/scan", {})[0] == 200 and started[-1][1] == ["cleanup", "scan"])
check("console : mise à la corbeille", call("/api/cleanup/trash", {"ids": ["0123456789ab"]})[0] == 200 and started[-1][1] == ["cleanup", "trash", "0123456789ab"])
check("console : sélection vide refusée", call("/api/cleanup/trash", {"ids": []})[0] == 400)
check("console : identifiant piégé refusé", call("/api/cleanup/trash", {"ids": ["--days=1"]})[0] == 400 and call("/api/cleanup/restore", {"ids": "abc"})[0] == 400)
check("console : vidage", call("/api/cleanup/empty", {})[0] == 200 and started[-1][1] == ["cleanup", "empty"])
check("console : réservé aux administrateurs", call("/api/cleanup", groups="users")[0] == 403)

print("échecs :", bad)
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if bad else 0)
