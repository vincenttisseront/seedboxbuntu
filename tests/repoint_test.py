"""Seed depuis le NAS : repointage sûr d'un torrent vers la copie du NAS, avec retour arrière."""
import os
import sys
import tempfile
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbrepoint"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

rp = ctl.repoint()
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


MIN = rp.MIN_SIZE
data = b"x" * (MIN + 10)
storage = ctl.STORAGE
shares = root / "shares"
(storage / "downloads/Film.2020").mkdir(parents=True)
(shares / "nas/movies/Film (2020)").mkdir(parents=True)
local = storage / "downloads/Film.2020/film.mkv"
nas = shares / "nas/movies/Film (2020)/Film (2020).mkv"
local.write_bytes(data)
nas.write_bytes(data)
(storage / "downloads/Film.2020/film.nfo").write_text("x")

# correspondance de chemins : /data -> stockage, /shares -> dossier simulé
ls = ctl.libscan()
ls.to_host = lambda p: (storage / p[len("/data/"):]) if str(p).startswith("/data/") else (shares / p[len("/shares/"):]) if str(p).startswith("/shares/") else None
ctl.libscan = lambda: ls


class Fake:
    """qBittorrent simulé : retient emplacement, noms et état ; la revérification lit vraiment les fichiers."""
    def __init__(self, fail_check=False):
        self.save = "/data/downloads"
        self.files = [{"index": 0, "name": "Film.2020/film.mkv", "size": len(data), "priority": 1, "progress": 1},
                      {"index": 1, "name": "Film.2020/film.nfo", "size": 1, "priority": 1, "progress": 1}]
        self.calls, self.fail_check, self.checking = [], fail_check, 0

    def get(self, path):
        if path.startswith("/torrents/files"):
            return [dict(f) for f in self.files]
        if path.startswith("/torrents/info?hashes"):
            return [{"state": "pausedUP", "progress": 1}]
        return [{"hash": "h1", "name": "Film.2020", "progress": 1, "save_path": self.save}]

    def post(self, path, form):
        self.calls.append((path, dict(form)))
        if path == "/torrents/setLocation":
            self.save = form["location"]
        elif path == "/torrents/renameFile":
            for f in self.files:
                if f["name"] == form["oldPath"]:
                    f["name"] = form["newPath"]
        elif path == "/torrents/filePrio":
            self.files[int(form["id"])]["priority"] = 0
        elif path == "/torrents/recheck":
            for f in self.files:
                present = os.path.isfile(rp._host(f"{self.save}/{f['name']}") or "")
                f["progress"] = 1 if present and not self.fail_check else 0
        return None

    stop = start = lambda self, ident: self.calls.append(("state", ident))


records = [{"path": "/shares/nas/movies/Film (2020)/Film (2020).mkv", "size": len(data), "title": "Film"}]
index = rp._media_by_size(records)
torrent = {"hash": "h1", "name": "Film.2020", "progress": 1, "save_path": "/data/downloads"}
plan = rp.plan_for(torrent, Fake().get("/torrents/files"), index)
check("copie identique sur un partage -> plan, nfo laissé de côté",
      plan.get("save_new") == "/shares/nas/movies/Film (2020)" and plan["moves"][0]["new"] == "Film (2020).mkv" and plan["extras"] == ["Film.2020/film.nfo"], str(plan)[:150])
check("torrent déjà sur un partage ignoré", "skip" in rp.plan_for({**torrent, "save_path": "/shares/nas/x"}, [], index))
nas.write_bytes(data[:-1] + b"y")
check("contenu différent -> pas de plan", "skip" in rp.plan_for(torrent, Fake().get("/torrents/files"), index))
nas.write_bytes(data)

fake = Fake()
freed = rp.apply(plan, qbit=fake, timeout=5)
check("repointage réussi : copie locale supprimée, emplacement et nom changés, octets libérés",
      not local.exists() and not Path(str(local) + rp.ASIDE).exists() and fake.save == "/shares/nas/movies/Film (2020)"
      and any(f["name"] == "Film (2020).mkv" for f in fake.files) and freed == len(data) and nas.exists())

# échec de la revérification : tout est remis comme avant
local.write_bytes(data)
fake = Fake(fail_check=True)
plan = rp.plan_for(torrent, fake.get("/torrents/files"), index)
try:
    rp.apply(plan, qbit=fake, timeout=5)
    failed = False
except ctl.SeedboxError:
    failed = True
check("vérification incomplète -> erreur, copie locale restaurée, emplacement et noms d'origine",
      failed and local.read_bytes() == data and not Path(str(local) + rp.ASIDE).exists() and fake.save == "/data/downloads"
      and fake.files[0]["name"] == "Film.2020/film.mkv" and nas.exists())
check("réglage automatique par défaut, désactivable", rp.settings()["enabled"] and not rp.set_enabled(False)["enabled"] and rp.run("timer").get("disabled"))
print(f"ECHECS: {bad}")
sys.exit(1 if bad else 0)
