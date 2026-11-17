"""Où est passé l'espace : mesures, répartition, remèdes classés, correctifs sûrs."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbspace"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

bad = 0
G = 1 << 30
M = 1 << 20


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def fails(function, *args):
    try:
        function(*args)
    except ctl.SeedboxError as error:
        return str(error)
    return None


space = ctl.space()
storage = ctl.STORAGE
for folder in ("downloads", "media", ".corbeille"):
    (storage / folder).mkdir(parents=True, exist_ok=True)
(ctl.STATE / "sauvegardes").mkdir(parents=True, exist_ok=True)
ctl.CONFIG.mkdir(parents=True, exist_ok=True)

# --- mesures : lecture des sorties de Docker et de journald --------------------------------------------------------------------
space.capture = lambda *c: ('{"Type":"Images","Size":"2.5GB","Reclaimable":"1.2GB (48%)"}\n{"Type":"Containers","Size":"100MB","Reclaimable":"0B (0%)"}\n'
                            '{"Type":"Local Volumes","Size":"300MB","Reclaimable":"0B (0%)"}\n{"Type":"Build Cache","Size":"700MB","Reclaimable":"700MB"}\nbruit\n'
                            if c[0] == "docker" else "Archived and active journals take up 1.2G in the file system.\n")
rows = {r["kind"]: r for r in space.docker_usage()}
check("Docker : tailles et espace récupérable lus", abs(rows["Images"]["size"] - 2.5 * 1e9) < 0.2e9 and abs(rows["Images"]["reclaimable"] - 1.2e9) < 0.1e9, str(rows))
check("Docker : ligne illisible ignorée", len(rows) == 4)
check("journald : taille lue", 1.1 * G < space.journal_bytes() < 1.3 * G, str(space.journal_bytes()))
space.capture = lambda *c: ""
check("journald absent : zéro", space.journal_bytes() == 0 and space.docker_usage() == [])

# --- du réel : somme des fichiers --------------------------------------------------------------------------------------------
probe = root / "probe"
(probe / "a").mkdir(parents=True)
(probe / "a" / "big.bin").write_bytes(b"x" * (3 * M))
(probe / "small.bin").write_bytes(b"x" * 1000)
sizes = space.du(probe, 1)
check("du : taille d'un dossier et de ses enfants", sizes[str(probe)] >= 3 * M and sizes[str(probe / "a")] >= 3 * M and str(probe / "small.bin") not in sizes, str(sizes))
check("du : dossier absent = rien", space.du(root / "nope") == {})

# --- répartition ----------------------------------------------------------------------------------------------------------------
real_du = space.du
tree = {
    str(storage / "downloads"): {str(storage / "downloads"): 40 * G, str(storage / "downloads/radarr"): 30 * G, str(storage / "downloads/sonarr"): 10 * G},
    str(storage / "media"): {str(storage / "media"): 20 * G, str(storage / "media/films"): 15 * G, str(storage / "media/series"): 5 * G},
    str(storage / ".corbeille"): {str(storage / ".corbeille"): 2 * G},
    str(ctl.CONFIG): {str(ctl.CONFIG): 3 * G, str(ctl.CONFIG / "jellyfin"): 2 * G, str(ctl.CONFIG / "radarr"): 1 * G},
    str(ctl.STATE / "sauvegardes"): {str(ctl.STATE / "sauvegardes"): 1 * G},
    str(ctl.STATE): {str(ctl.STATE): int(1.5 * G)},
}
space.du = lambda path, depth=1: tree.get(str(path), {})
volume = {"label": "stockage", "path": str(storage), "device": os.stat(storage).st_dev, "total": 100 * G, "free": 8 * G, "used": 92 * G, "percent": 92, "level": "warning"}
analysis = space.analyse_volume(volume, None)
items = {i["id"]: i for i in analysis["items"]}
check("répartition : téléchargements, médiathèque, corbeille, configuration identifiés", {"downloads", "media", "trash", "config", "backups", "state"} <= set(items), str(set(items)))
check("répartition : triée du plus gros au plus petit", [i["size"] for i in analysis["items"]] == sorted((i["size"] for i in analysis["items"]), reverse=True))
check("répartition : détail par sous-dossier", [c["name"] for c in items["downloads"]["children"]] == ["radarr", "sonarr"] and items["config"]["children"][0]["name"] == "jellyfin")
check("répartition : les sauvegardes ne sont pas comptées deux fois (retirées des données de l'appliance)", items["backups"]["size"] == G and items["state"]["size"] == int(0.5 * G))
known = sum(i["size"] for i in analysis["items"])
check("répartition : le reste est « non identifié » et la somme retombe sur l'occupation", analysis["other"] == volume["used"] - known and known + analysis["other"] == volume["used"], f"{known / G} + {analysis['other'] / G}")
check("répartition : emplacement sur un autre disque ignoré", not [i for i in space.analyse_volume({**volume, "device": volume["device"] + 999}, None)["items"] if i["id"] in ("downloads", "media")])
(storage / "media").rmdir()
check("répartition : dossier absent ignoré", "media" not in {i["id"] for i in space.analyse_volume(volume, None)["items"]})
(storage / "media").mkdir()
space.capture = lambda *c: ('{"Type":"Images","Size":"4GB","Reclaimable":"3GB (75%)"}\n' if c[0] == "docker" else "")
real_same = space.same_device
space.same_device = lambda path, device: True if str(path) == "/var/lib/docker" else real_same(path, device)
check("Docker compté quand son dossier est sur ce disque", "docker-images" in {i["id"] for i in space.analyse_volume(volume, None)["items"]})
space.capture = lambda *c: ""
space.same_device = real_same

# --- plus gros éléments --------------------------------------------------------------------------------------------------------
space.du = lambda path, depth=1: {str(storage): 60 * G, str(storage / "downloads"): 40 * G, str(storage / "downloads/radarr"): 30 * G, str(storage / "downloads/radarr/Gros.Film.2020"): 25 * G,
                                  str(storage / "downloads/sonarr/Serie.S01"): 8 * G, str(storage / "media/films/Titre (2019)"): 12 * G, str(storage / "media/films"): 15 * G}
top = space.biggest(volume)
check("plus gros éléments : niveau 3 seulement, triés", [r["path"] for r in top] == ["downloads/radarr/Gros.Film.2020", "media/films/Titre (2019)", "downloads/sonarr/Serie.S01"], str(top))
check("plus gros éléments : autre disque ignoré", space.biggest({**volume, "device": volume["device"] + 1}) == [])

# --- remèdes --------------------------------------------------------------------------------------------------------------------
cleanup = ctl.cleanup()
cleanup.overview = lambda: {"categories": [
    {"id": "orphan", "count": 4, "size": 12 * G}, {"id": "partial", "count": 2, "size": 1 * G}, {"id": "duplicate", "count": 0, "size": 0},
    {"id": "transcode", "count": 1, "size": 3 * G}, {"id": "empty_dir", "count": 3, "size": 0}, {"id": "docker", "count": 1, "size": 2 * G}],
    "trash": {"count": 2, "size": 2 * G}}
scan_data = {"items": [
    {"id": "downloads", "size": 40 * G, "children": []}, {"id": "media", "size": 20 * G, "children": []}, {"id": "journal", "size": 800 * M, "children": []},
    {"id": "apt", "size": 120 * M, "children": []}, {"id": "docker-images", "size": 4 * G, "reclaimable": 3 * G, "children": []}, {"id": "docker-containers", "size": G, "reclaimable": 0, "children": []}]}


class Shares:
    ROOT = root / "mnt/shares"

    @staticmethod
    def mounted_targets():
        return []

    on_share = set()

    @classmethod
    def volume(cls, role):
        return "nas" if role in cls.on_share else "local"


ctl.shares = lambda: Shares
found = space.remedies(scan_data, volume)
by_id = {r["id"]: r for r in found}
check("remèdes : corbeille, orphelins, partiels, transcodage, Docker, journaux, apt",
      {"trash-empty", "cleanup-orphan", "cleanup-partial", "cleanup-transcode", "docker", "journal", "apt"} <= set(by_id), str(list(by_id)))
check("remèdes : rien pour une catégorie vide (doublons, dossiers vides)", "cleanup-duplicate" not in by_id and "cleanup-empty_dir" not in by_id)
check("remèdes : l'espace récupérable est celui mesuré", by_id["cleanup-orphan"]["reclaim"] == 12 * G and by_id["docker"]["reclaim"] == 3 * G and by_id["trash-empty"]["reclaim"] == 2 * G)
check("remèdes : journaux = ce qui dépasse la taille conservée", by_id["journal"]["reclaim"] == 800 * M - space.JOURNAL_KEEP)
check("remèdes : l'image Docker pendante du nettoyage n'est pas proposée deux fois", "cleanup-docker" not in by_id)
check("remèdes : correctifs sûrs = ceux que la console sait exécuter", all(r["fix"] in space.FIXES for r in found if r["kind"] == "fix"))
check("remèdes : ceux de l'assistant renvoient vers la page Nettoyage", by_id["cleanup-orphan"]["kind"] == "page" and by_id["cleanup-orphan"]["view"] == "cleanup")
check("remèdes : classés du plus rentable, conseils en dernier",
      [r["kind"] == "advice" for r in found] == sorted(r["kind"] == "advice" for r in found) and [r["reclaim"] for r in found if r["kind"] != "advice"] == sorted((r["reclaim"] for r in found if r["kind"] != "advice"), reverse=True))
check("conseil : médiathèque sur un partage (aucun monté : le dit)", by_id["media-share"]["kind"] == "advice" and "aucun partage" in by_id["media-share"]["why"].lower() and by_id["media-share"]["view"] == "shares")
check("conseil : seeds quand les téléchargements pèsent lourd", by_id["seeds"]["kind"] == "advice" and by_id["seeds"].get("estimate"))
check("conseil : agrandir le disque à partir de 90 %", "grow" in by_id and by_id["grow"]["reclaim"] == 0)
calm = space.remedies({"items": [{"id": "media", "size": G, "children": []}]}, {**volume, "percent": 40, "used": 40 * G})
cleanup.overview = lambda: {"categories": [], "trash": {"count": 0, "size": 0}}
calm = space.remedies({"items": [{"id": "media", "size": G, "children": []}]}, {**volume, "percent": 40, "used": 40 * G})
check("disque sain : aucun remède superflu", calm == [], str(calm))
Shares.mounted_targets = staticmethod(lambda: [root / "mnt/shares/nas"])
mounted = space.remedies({"items": [{"id": "media", "size": 20 * G, "children": []}]}, volume)
# bibliothèque déjà sur un partage mais ancien contenu resté en local
Shares.on_share = {"movies", "music"}
stranded = space.remedies({"items": [{"id": "media", "size": 30 * G, "children": [{"name": "movies", "size": 25 * G}, {"name": "tv", "size": 4 * G}, {"name": "music", "size": 100 * M}]}]}, volume)
sid = {r["id"]: r for r in stranded}
check("conseil : films restés en local alors que le partage est réglé", "media-stranded" in sid and sid["media-stranded"]["reclaim"] == 25 * G and "Films" in sid["media-stranded"]["why"] and sid["media-stranded"]["view"] == "library", str(sid.get("media-stranded")))
check("conseil : sous 1 Gio resté en local, la musique n'est pas citée", "Musique" not in sid["media-stranded"]["why"])
check("conseil : le reste en local (sous 5 % du disque) ne redéclenche pas « ranger sur un partage »", "media-share" not in sid)
Shares.on_share = set()
check("conseil : aucune bibliothèque sur un partage → pas de déplacement proposé", "media-stranded" not in {r["id"]: r for r in space.remedies(scan_data, volume)})
check("conseil : partage monté → pas de mention d'absence", "aucun partage" not in {r["id"]: r for r in mounted}["media-share"]["why"].lower())

# --- analyse, mémoire, aperçu --------------------------------------------------------------------------------------------------
live = [{**volume}]
space.volumes = lambda: [dict(v) for v in live]
space.du = lambda path, depth=1: tree.get(str(path), {})
space.biggest = lambda v: [{"path": "downloads/radarr/Gros.Film.2020", "size": 25 * G}]
before = space.overview()
check("aperçu avant analyse : disque et remplissage, rien d'autre", before["scanned"] is None and before["volumes"][0]["percent"] == 92 and before["volumes"][0]["items"] == [] and before["volumes"][0]["remedies"] == [])
cleanup.scan = lambda: calls_scan.append(1)
calls_scan = []
result = space.scan()
check("analyse : relance celle du nettoyage quand elle est absente ou ancienne", calls_scan == [1])
cleanup.write_json(cleanup.SCAN_FILE, {"scanned": int(__import__("time").time()), "items": []})
space.scan()
check("analyse : ne la relance pas quand elle est récente", calls_scan == [1])
check("analyse : enregistrée", SCAN_FILE_OK := (space.SCAN_FILE.exists() and result["volumes"][0]["items"]))
after = space.overview()
disk = after["volumes"][0]
check("aperçu après analyse : répartition, plus gros éléments, remèdes", disk["items"] and disk["biggest"][0]["size"] == 25 * G and disk["remedies"] and after["scanned"])
live[0]["percent"] = 97
check("aperçu : le remplissage est toujours celui du moment", space.overview()["volumes"][0]["percent"] == 97)
space.SCAN_FILE.write_text("pas du json")
check("analyse illisible : aperçu vide, pas d'erreur", space.overview()["scanned"] is None)

# --- correctifs ----------------------------------------------------------------------------------------------------------------
calls = []
cleanup.empty = lambda days=0: calls.append("empty")
cleanup.docker_prune = lambda: calls.append("prune")
ctl.run = lambda *a, **k: calls.append(tuple(str(x) for x in a)) or subprocess.CompletedProcess(a, 0, "", "")
space.shutil.which = lambda name: "/usr/bin/" + name
space.fix("trash-empty")
check("correctif : vider la corbeille", calls == ["empty"])
calls.clear()
space.fix("docker")
check("correctif : images inutiles puis cache de construction", calls[0] == "prune" and calls[1][:3] == ("docker", "builder", "prune"), str(calls))
calls.clear()
space.fix("journal")
check("correctif : journaux réduits à la taille conservée", calls == [("journalctl", "--vacuum-size=200M")], str(calls))
calls.clear()
space.fix("apt")
check("correctif : cache apt", calls == [("apt-get", "clean")])
check("correctif inconnu refusé (rien d'exécuté)", "inconnu" in (fails(space.fix, "rm -rf /") or "") and len(calls) == 1)

# --- ligne de commande --------------------------------------------------------------------------------------------------------
sys.argv = ["seedboxctl", "space", "show"]
try:
    code = ctl.main()
except SystemExit as stop:
    code = stop.code
check("seedboxctl space show", code in (0, None))

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
