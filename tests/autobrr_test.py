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


def scenario(filters, indexers, detail=None):
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
        if path.startswith("/filters/") and method == "GET":
            return Reply(detail or {})
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

# autobrr écrit NULL pour une liste absente du JSON et SQLite la refuse (HTTP 500, NOT NULL constraint failed: filter.resolutions) :
# chaque liste doit être envoyée, vide au besoin, à la création comme à la mise à jour
calls = scenario([], [{"id": 1, "enabled": True}])
bodies = [b for m, p, b in calls if m in ("POST", "PUT") and p.startswith("/filters")]
check("création : toutes les listes d'autobrr sont envoyées, vides (sinon NOT NULL constraint failed: filter.resolutions)",
      len(bodies) == 2 and all(isinstance(b.get(name), list) and not b[name] for b in bodies for name in ctl.AUTOBRR_FILTER_LISTS),
      str([name for name in ctl.AUTOBRR_FILTER_LISTS if not all(isinstance(b.get(name), list) for b in bodies)]))
check("liste des champs : celle que l'interface web d'autobrr envoie toujours",
      {"resolutions", "codecs", "sources", "containers", "match_hdr", "except_hdr", "match_other", "except_other",
       "match_release_types", "formats", "quality", "media", "origins", "except_origins", "bonus", "announce_types",
       "match_language", "except_language"} == set(ctl.AUTOBRR_FILTER_LISTS))
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}], detail={"id": 9, "resolutions": ["1080p"], "codecs": None})
put = next(b for m, p, b in calls if m == "PUT")
check("mise à jour : les listes réglées par l'utilisateur sont gardées, les autres envoyées vides (jamais null)",
      put["resolutions"] == ["1080p"] and put["codecs"] == [] and all(isinstance(put[name], list) for name in ctl.AUTOBRR_FILTER_LISTS))

# actions : sans identifiant autobrr en ajoute une de plus à chaque mise à jour (chaque nouveauté partirait plusieurs fois vers qBittorrent)
action = "qBittorrent : agrégateur"
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}],
                 detail={"id": 9, "actions": [{"id": 31, "name": action}, {"id": 32, "name": action}, {"id": 33, "name": "Mon action"}]})
put = next(b for m, p, b in calls if m == "PUT")
check("mise à jour : l'action existante est reprise par son identifiant, pas ajoutée une fois de plus",
      [a.get("id") for a in put["actions"]] == [31] and len(put["actions"]) == 1, str(put["actions"])[:120])
check("mise à jour : les doublons déjà créés sont supprimés, l'action de l'utilisateur est laissée",
      [p for m, p, b in calls if m == "DELETE"] == ["/actions/32"])
calls = scenario([], [{"id": 1}], detail={"id": 42, "actions": [{"id": 7, "name": action}]})
put = next(b for m, p, b in calls if m == "PUT")
check("création : l'action posée avec le filtre est reprise par la mise à jour qui suit (pas de doublon dès le premier passage)",
      [a.get("id") for a in put["actions"]] == [7])

# indexeurs de Prowlarr -> autobrr : un flux Torznab par indexeur torrent actif, sans doublon ni écrasement
class Feeds:
    """Faux autobrr : garde les indexeurs et flux créés, comme le vrai."""
    def __init__(self, feeds=None, indexers=None, refuse_feed=False):
        self.feeds, self.indexers, self.calls, self.refuse_feed = feeds or [], indexers or [], [], refuse_feed

    def api(self, method, path, body=None, cookie=None):
        self.calls.append((method, path, body))
        if path == "/feeds" and method == "GET":
            return Reply(self.feeds)
        if path == "/indexer" and method == "GET":
            return Reply(self.indexers)
        if path == "/indexer" and method == "POST":
            self.indexers.append({"id": 100 + len(self.indexers), "name": body["name"]})
            return Reply(self.indexers[-1], 201)
        if path == "/feeds" and method == "POST":
            if self.refuse_feed:
                return Reply({}, 500)
            self.feeds.append({**body, "id": 200 + len(self.feeds)})
            return Reply({}, 201)
        return Reply({})


def sync(fake, prowlarr, key="cle1"):
    ctl.AUTOBRR_FEEDS_STATE = root / "var/sb/autobrr-flux.json"
    real = (ctl.autobrr_api, ctl.prowlarr_indexers, ctl.env_read)
    ctl.autobrr_api, ctl.prowlarr_indexers, ctl.env_read = fake.api, lambda: prowlarr, lambda: {"PROWLARR_API_KEY": key}
    try:
        return ctl.autobrr_sync_indexers("c")
    finally:
        ctl.autobrr_api, ctl.prowlarr_indexers, ctl.env_read = real


prowlarr = [{"id": 1, "name": "Alpha", "enable": True, "protocol": "torrent"},
            {"id": 2, "name": "Beta", "enable": False, "protocol": "torrent"},
            {"id": 3, "name": "Usenet", "enable": True, "protocol": "usenet"},
            {"id": 4, "name": "Jackett : Gamma", "enable": True, "protocol": "torrent"}]
(root / "var/sb/autobrr-flux.json").unlink(missing_ok=True)
fake = Feeds()
count = sync(fake, prowlarr)
created = [b for m, p, b in fake.calls if m == "POST" and p == "/feeds"]
check("Prowlarr : un flux Torznab par indexeur torrent actif (ni désactivé, ni usenet), actif, via l'adresse interne de Prowlarr",
      count == 2 and [f["url"] for f in created] == ["http://prowlarr:9696/prowlarr/1/api", "http://prowlarr:9696/prowlarr/4/api"]
      and all(f["type"] == "TORZNAB" and f["enabled"] and f["api_key"] == "cle1" and f["categories"] == [] for f in created)
      and [i["name"] for i in fake.indexers] == ["Prowlarr : Alpha", "Prowlarr : Jackett : Gamma"], str(created)[:200])
check("indexeur autobrr : modèle Torznab générique, indexer_id du flux = indexeur créé",
      all(b["identifier"] == "torznab" and b["implementation"] == "torznab" for m, p, b in fake.calls if m == "POST" and p == "/indexer")
      and [f["indexer_id"] for f in created] == [100, 101])
fake.calls.clear()
check("2e passage : rien n'est refait (pas de doublon, pas d'écriture)", sync(fake, prowlarr) == 0
      and not [1 for m, p, b in fake.calls if m in ("POST", "PUT")])
fake.calls.clear()
count = sync(fake, prowlarr, key="cle2")
puts = [(p, b) for m, p, b in fake.calls if m == "PUT"]
check("clé de Prowlarr renouvelée : les flux sont corrigés, sans toucher à l'état ni à l'intervalle choisis",
      count == 2 and len(puts) == 2 and all(b["api_key"] == "cle2" for p, b in puts))
manual = Feeds(feeds=[{"id": 5, "name": "Mon Alpha", "enabled": False, "interval": 60, "url": "http://prowlarr:9696/prowlarr/1/api", "indexer": {"id": 9}}])
(root / "var/sb/autobrr-flux.json").write_text('{"key": "%s"}' % __import__("hashlib").sha256(b"cle1").hexdigest())
sync(manual, prowlarr[:1])
check("flux déjà ajouté à la main (même adresse) : reconnu, ni doublé, ni réactivé, ni renommé",
      not [1 for m, p, b in manual.calls if m in ("POST", "PUT")])
(root / "var/sb/autobrr-flux.json").unlink(missing_ok=True)
refused = Feeds(refuse_feed=True)
try:
    sync(refused, prowlarr[:1])
    raised = False
except ctl.SeedboxError as error:
    raised = "HTTP 500" in str(error)
check("flux refusé : l'indexeur créé est supprimé (pas d'orphelin) et l'erreur remonte avec la réponse d'autobrr",
      raised and [1 for m, p, b in refused.calls if m == "DELETE" and p == "/indexer/100"]
      and not (root / "var/sb/autobrr-flux.json").exists())
check("sans indexeur actif dans Prowlarr : rien à faire", sync(Feeds(), []) == 0)

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
