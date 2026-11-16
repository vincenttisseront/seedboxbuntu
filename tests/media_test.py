"""Emplacements Musique, Livres audio et Livres : rôles, définitions Docker (Navidrome, Audiobookshelf, Lidarr),
liaisons (Lidarr, Jellyfin, Audiobookshelf) et table de chemins de l'import, avec des services simulés."""
import json
import os
import sys
import tempfile
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbmedia"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402
import shares  # noqa: E402
import importer  # noqa: E402

apps = ctl.apps()
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def choose(role, share, path):
    data = shares.load()
    data["shares"].setdefault(share, {"kind": "smb", "source": "//nas/x"})
    data["roles"][role] = {"share": share, "path": path}
    shares.save(data)


# ---------------------------------------------------------------- rôles
for role in ("music", "audiobooks", "books"):
    check(f"rôle {role} existant, local par défaut", role in shares.ROLES and shares.volume(role) == "local" and shares.location(role).startswith("/data/media/"))
check("chemins locaux par défaut", [shares.location(r) for r in ("music", "audiobooks", "books")] == ["/data/media/music", "/data/media/audiobooks", "/data/media/books"])
check("dossier d'application : local = chemin du conteneur", shares.app_folder("music", "/music") == "/music")
choose("music", "nas", "Musique/Flac")
check("dossier d'application : partage = /shares/<nom>/<dossier>", shares.app_folder("music", "/music") == "/shares/nas/Musique/Flac")
check("chemins de bibliothèque : local + partage", shares.library_paths("/data/media/music", "music") == ["/data/media/music", "/shares/nas/Musique/Flac"])
overview = {r["role"]: r for r in shares.overview()["roles"]}
check("vue d'ensemble : 9 emplacements, musique sur le partage", len(overview) == 9 and overview["music"]["share"] == "nas" and overview["books"]["location"] == "/data/media/books", list(overview))
check("avertissement : musique et téléchargements sur des volumes différents", any("Musique" in n and "Lidarr" in n for n in shares.warnings(shares.load())), shares.warnings(shares.load()))

# ---------------------------------------------------------------- catalogue et Docker
built = apps.builtin()
check("catalogue : accès aux partages de Lidarr, Navidrome, Audiobookshelf", (built["lidarr"]["shares"], built["navidrome"]["shares"], built["audiobookshelf"]["shares"]) == ("rw", "ro", "rw"))
web = {"id": "webapp", "image": "lscr.io/linuxserver/bazarr:latest", "port": 6767, "shares": "rw"}
try:
    apps.validate(web)
    check("entrée du web : accès aux partages refusé", False)
except apps.CatalogError:
    check("entrée du web : accès aux partages refusé", True)
navidrome = apps.service_definition(built["navidrome"])
check("Navidrome : dossier musique = partage choisi", navidrome["environment"]["ND_MUSICFOLDER"] == "/shares/nas/Musique/Flac", navidrome["environment"])
check("Navidrome : /shares en lecture seule avec propagation", "${SHARES_DIR:-/mnt/shares}:/shares:ro,rslave" in navidrome["volumes"], navidrome["volumes"])
check("Lidarr : /shares en lecture-écriture avec propagation", "${SHARES_DIR:-/mnt/shares}:/shares:rslave" in apps.service_definition(built["lidarr"])["volumes"])
check("Audiobookshelf : /shares monté", any(v.endswith(":/shares:rslave") for v in apps.service_definition(built["audiobookshelf"])["volumes"]))
check("application sans accès : aucun montage /shares", not any(":/shares" in v for v in apps.service_definition(built["bazarr"])["volumes"]))
data = shares.load(); data["roles"].pop("music"); shares.save(data)
check("Navidrome : retour au dossier local /music", apps.service_definition(built["navidrome"])["environment"]["ND_MUSICFOLDER"] == "/music")

# application installée avant l'accès aux Emplacements : reçoit le champ sans réinstallation
old = {k: v for k, v in built["navidrome"].items() if k != "shares"}
apps.installed_write({"navidrome": {"entry": old, "since": 1, "integrated": True}})
check("application déjà installée : accès aux partages ajouté", apps.installed_entries()[0]["shares"] == "ro")
apps.regenerate()
check("docker-compose.apps.yml : /shares présent", "/shares:ro,rslave" in apps.APPS_COMPOSE.read_text())

# ---------------------------------------------------------------- liaisons
calls = []


class Answer:
    def __init__(self, data=None, status=200):
        self.status, self._data, self.text = status, data, json.dumps(data)[:200]

    ok = property(lambda self: 200 <= self.status < 300)

    def json(self):
        return self._data


library = {"id": "L1", "name": "Livres audio", "folders": [{"id": "f1", "fullPath": "/audiobooks"}, {"id": "f2", "fullPath": "/shares/ancien/livres"}]}


def fake_request(method, url, headers=None, body=None, **_):
    calls.append((method, url.split(":80/audiobookshelf", 1)[-1], body))
    if url.endswith("/status"):
        return Answer({"isInit": True})
    if url.endswith("/login"):
        return Answer({"user": {"token": "t"}})
    if url.endswith("/api/libraries") and method == "GET":
        return Answer({"libraries": [library]})
    return Answer({})


ctl.request, ctl.wait_http, ctl.svc = fake_request, lambda *a, **k: None, lambda name, port: f"http://{name}:{port}"
choose("audiobooks", "nas", "Audio")
choose("books", "nas", "Ebooks")
apps.integrate_audiobookshelf(built["audiobookshelf"], {"AUDIOBOOKSHELF_ADMIN_PASSWORD": "x"})
patch = [c for c in calls if c[0] == "PATCH"]
check("Audiobookshelf : dossiers de la bibliothèque mis à jour (ancien partage retiré, nouveau ajouté)",
      len(patch) == 1 and [f["fullPath"] for f in patch[0][2]["folders"]] == ["/audiobooks", "/shares/nas/Audio"], patch)
created = [c for c in calls if c[0] == "POST" and c[1] == "/api/libraries"]
check("Audiobookshelf : bibliothèque Livres créée avec le partage choisi",
      len(created) == 1 and created[0][2]["name"] == "Livres" and [f["fullPath"] for f in created[0][2]["folders"]] == ["/books", "/shares/nas/Ebooks"], created)

choose("music", "nas", "Musique")
arr, jellyfin = [], []


def fake_arr(name, method, path, body=None):
    arr.append((name, method, path, body))
    return Answer([{"id": 1, "path": "/data/media/music"}] if path == "/rootfolder" and method == "GET" else [{"id": 1}])


def fake_jellyfin(method, path, body=None, token=None):
    jellyfin.append((method, path, body))
    if path == "/Library/VirtualFolders" and method == "GET":
        return Answer([{"Name": "Musique", "Locations": ["/data/media/music", "/shares/vieux/musique"]}])
    return Answer({})


ctl.arr_api, ctl.jellyfin_api, ctl.arr_upsert, ctl.qbit_host, ctl.qbit_reachable = fake_arr, fake_jellyfin, lambda *a, **k: None, lambda: "qbittorrent", lambda: True
apps.integrate_lidarr(built["lidarr"], {"QBIT_USERNAME": "u", "QBIT_PASSWORD": "p", "LIDARR_API_KEY": "k"})
roots = [c for c in arr if c[1] == "POST" and c[2] == "/rootfolder"]
check("Lidarr : dossier racine = emplacement Musique", len(roots) == 1 and roots[0][3]["path"] == "/shares/nas/Musique", roots)
adds = [c for c in jellyfin if c[1].startswith("/Library/VirtualFolders/Paths") and c[0] == "POST"]
removes = [c for c in jellyfin if c[0] == "DELETE"]
check("Jellyfin : bibliothèque Musique inclut le partage choisi", len(adds) == 1 and adds[0][2]["PathInfo"]["Path"] == "/shares/nas/Musique", adds)
check("Jellyfin : bibliothèque Musique n'inclut plus l'ancien partage", len(removes) == 1 and "vieux" in removes[0][1], removes)

# ---------------------------------------------------------------- import : table des chemins
targets = importer.targets()
check("import : table des chemins avec musique et livres", targets.get("music") == "/shares/nas/Musique" and targets.get("books") == "/shares/nas/Ebooks"
      and targets.get("movies") == "/data/media/movies" and targets.get("downloads") == "/data/downloads", targets)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
