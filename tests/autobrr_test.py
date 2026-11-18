"""autobrr : filtre de nouveautés freeleech posé sur tous les indexeurs, sans écraser un choix de l'utilisateur ; reprise d'un mot de passe désynchronisé."""
import os
import sys
import tempfile
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbabrr"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


class Reply:
    def __init__(self, data=None, status=200):
        self.data, self.status, self.ok, self.text = data, status, status < 300, ""

    def json(self):
        return self.data


def scenario(filters, indexers):
    calls = []

    def api(method, path, body=None, cookie=None):
        calls.append((method, path, body))
        if path == "/downloaders":
            return Reply([{"id": 7, "name": "qBittorrent"}])
        if path == "/indexer":
            return Reply(indexers)
        if path == "/filters" and method == "GET":
            return Reply(filters)
        if path == "/filters" and method == "POST":
            return Reply({"id": 42})
        return Reply({})
    real = ctl.autobrr_api
    ctl.autobrr_api = api
    try:
        ctl.provision_autobrr_filter("c")
    finally:
        ctl.autobrr_api = real
    return calls


calls = scenario([], [{"id": 1, "enabled": True}, {"id": 2, "enabled": True}, {"id": 3, "enabled": False}])
put = next(b for m, p, b in calls if m == "PUT")
check("nouveau filtre : créé puis complété, freeleech, 25 Go, 3 par heure, indexeurs actifs seulement, client qBittorrent",
      put["freeleech"] and put["max_size"] == "25GB" and put["max_downloads"] == 3 and [i["id"] for i in put["indexers"]] == [1, 2]
      and put["actions"][0]["client_id"] == 7 and put["id"] == 42, str(put)[:150])
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": False}], [{"id": 1}])
put = next(b for m, p, b in calls if m == "PUT")
check("filtre existant coupé par l'utilisateur : mis à jour mais jamais réactivé, pas de doublon",
      put["enabled"] is False and put["id"] == 9 and not [1 for m, p, b in calls if m == "POST"])

# reprise du mot de passe : la 2e variante de saisie est tentée si la 1re échoue
tried = []
real_run = ctl.run


def fake_run(*command, **kw):
    tried.append(kw.get("stdin"))
    return type("R", (), {"returncode": 0 if len(tried) == 2 else 1})()


ctl.run = fake_run
try:
    ok = ctl.autobrr_recover_password("pw")
finally:
    ctl.run = real_run
check("mot de passe désynchronisé : l'outil d'autobrr est essayé avec les deux formes de saisie", ok and tried == ["pw\n", "pw\npw\n"], str(tried))
print(f"ECHECS: {bad}")
sys.exit(1 if bad else 0)
