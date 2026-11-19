"""autobrr : filtre de nouveautés freeleech posé sur tous les indexeurs, sans écraser un choix de l'utilisateur ; reprise d'un mot de passe désynchronisé."""
import json
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


def scenario(filters, indexers, detail=None, created=None, preset="1080p", state=None, refuse=None):
    """detail : le filtre existant (id 9) tel que le rend autobrr ; created : le filtre juste créé (id 42)."""
    calls = []
    ctl.AUTOBRR_FEEDS_STATE = root / "var/sb/autobrr-flux.json"
    ctl.AUTOBRR_FEEDS_STATE.unlink(missing_ok=True)
    if state is not None:
        ctl.AUTOBRR_FEEDS_STATE.write_text(json.dumps(state))

    def api(method, path, body=None, cookie=None):
        calls.append((method, path, body))
        if path == "/downloaders":
            return Reply([{"id": 7, "name": "qBittorrent"}])
        if path == "/indexer":
            return Reply(indexers)
        if path == "/filters" and method == "GET":
            return Reply(filters)
        if path == "/filters" and method == "POST":
            return Reply({}, 500) if refuse and body["name"] == refuse else Reply({"id": 42})
        if path.startswith("/filters/") and method == "GET":
            return Reply((created if path == "/filters/42" else detail) or {})
        return Reply({})
    real = (ctl.autobrr_api, ctl.env_read)
    ctl.autobrr_api, ctl.env_read = api, lambda: {"RECYCLARR_PRESET": preset}
    try:
        ctl.provision_autobrr_filter("c")
    finally:
        ctl.autobrr_api, ctl.env_read = real
    return calls


def sent(calls, method, name=None):
    return [b for m, p, b in calls if m == method and p.startswith("/filters") and (name is None or b.get("name") == name)]


NAMES = [ctl.FILTER_NAME, ctl.FILTER_MUSIC, ctl.FILTER_BOOKS]
calls = scenario([], [{"id": 1, "enabled": True}, {"id": 2, "enabled": True}, {"id": 3, "enabled": False}])
put = sent(calls, "PUT", ctl.FILTER_NAME)[0]
check("nouveau filtre : créé puis complété, freeleech, 25 Go, 3 par heure, indexeurs actifs seulement, client qBittorrent",
      put["freeleech"] and put["max_size"] == "25GB" and put["max_downloads"] == 3 and [i["id"] for i in put["indexers"]] == [1, 2]
      and put["actions"][0]["client_id"] == 7 and put["id"] == 42, str(put)[:150])
check("trois filtres : films et séries, musique, livres (la qualité vidéo écarterait la musique et les livres, sans résolution)",
      [b["name"] for b in sent(calls, "POST")] == NAMES and [b["name"] for b in sent(calls, "PUT")] == NAMES)
check("musique et livres : 1 téléchargement par heure, étiquette de leur contenu",
      [sent(calls, "PUT", n)[0]["max_downloads"] for n in NAMES] == [3, 1, 1]
      and [sent(calls, "PUT", n)[0]["actions"][0]["tags"] for n in NAMES] == [
          "autobrr,nouveautes,films-series", "autobrr,nouveautes,musique", "autobrr,nouveautes,livres"])
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": False}], [{"id": 1}])
put = sent(calls, "PUT", ctl.FILTER_NAME)[0]
check("filtre existant coupé par l'utilisateur : mis à jour mais jamais réactivé, pas de doublon",
      put["enabled"] is False and put["id"] == 9 and not sent(calls, "POST", ctl.FILTER_NAME)
      and [b["name"] for b in sent(calls, "POST")] == NAMES[1:])

# contenus et qualité
categories = {b["name"]: (b["match_categories"], b["except_categories"]) for b in sent(calls, "PUT")}
check("catégories : films (Movies, 20xx) et séries (TV, 50xx) ; musique (Audio, 30xx, livres audio compris) ; livres (Books, 70xx)",
      categories[ctl.FILTER_NAME] == ("Movies*,20??,TV*,50??", "")
      and categories[ctl.FILTER_MUSIC] == ("Audio*,30??", "") and categories[ctl.FILTER_BOOKS] == ("Books*,70??", ""))
check("jeux, logiciels et adulte ne sont dans aucun filtre (PC, 4xxx, XXX, 6xxx)",
      not any(token in "".join(categories[n][0] for n in categories) for token in ("PC", "XXX", "40??", "60??")))
quality = {n: (sent(calls, "PUT", n)[0]["resolutions"], sent(calls, "PUT", n)[0]["sources"]) for n in NAMES}
check("qualité vidéo : préréglage 1080p de Recyclarr (1080p, Blu-ray + WEB), rien pour la musique et les livres",
      quality[ctl.FILTER_NAME] == (["1080p"], ["BluRay", "WEB", "WEB-DL", "WEBRip"])
      and quality[ctl.FILTER_MUSIC] == ([], []) and quality[ctl.FILTER_BOOKS] == ([], []), str(quality)[:160])
calls = scenario([], [{"id": 1}], preset="2160p")
put = sent(calls, "PUT", ctl.FILTER_NAME)[0]
check("qualité vidéo : préréglage 2160p (UHD Blu-ray + WEB)", put["resolutions"] == ["2160p"] and "UHD.BluRay" in put["sources"])
check("préréglage reconnu après la pose (rien à refaire tant qu'il ne change pas)", ctl.autobrr_state().get("preset") == "2160p")
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}], detail={"id": 9, "resolutions": ["720p"]},
                 state={"preset": "1080p"})
check("préréglage inchangé : la résolution réglée par l'utilisateur est gardée",
      sent(calls, "PUT", ctl.FILTER_NAME)[0]["resolutions"] == ["720p"])
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}], detail={"id": 9, "resolutions": ["720p"]},
                 state={"preset": "1080p"}, preset="2160p")
check("préréglage changé dans la console : la qualité du filtre suit (2160p)",
      sent(calls, "PUT", ctl.FILTER_NAME)[0]["resolutions"] == ["2160p"] and ctl.autobrr_state()["preset"] == "2160p")
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}], detail={"id": 9, "resolutions": [], "match_categories": ""})
put = sent(calls, "PUT", ctl.FILTER_NAME)[0]
check("filtre créé par la version précédente (sans qualité ni catégories) : complété à la première mise à jour",
      put["resolutions"] == ["1080p"] and put["match_categories"] == "Movies*,20??,TV*,50??")
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}],
                 detail={"id": 9, "match_categories": "Movies/HD"}, state={"preset": "1080p"})
check("catégories réglées par l'utilisateur : gardées", sent(calls, "PUT", ctl.FILTER_NAME)[0]["match_categories"] == "Movies/HD")
try:
    scenario([], [{"id": 1}], refuse=ctl.FILTER_MUSIC)
    raised = False
except ctl.SeedboxError as error:
    raised = ctl.FILTER_MUSIC in str(error) and "HTTP 500" in str(error)
check("un filtre refusé n'empêche pas les autres, l'erreur remonte, le préréglage n'est pas noté (nouvel essai)",
      raised and "preset" not in ctl.autobrr_state())

# autobrr écrit NULL pour une liste absente du JSON et SQLite la refuse (HTTP 500, NOT NULL constraint failed: filter.resolutions) :
# chaque liste doit être envoyée, vide au besoin, à la création comme à la mise à jour
calls = scenario([], [{"id": 1, "enabled": True}])
bodies = sent(calls, "POST") + sent(calls, "PUT")
check("création : toutes les listes d'autobrr sont envoyées, vides (sinon NOT NULL constraint failed: filter.resolutions)",
      len(bodies) == 6 and all(isinstance(b.get(name), list) for b in bodies for name in ctl.AUTOBRR_FILTER_LISTS)
      and all(not b[name] for b in bodies for name in ctl.AUTOBRR_FILTER_LISTS
              if name != "announce_types" and not (b["name"] == ctl.FILTER_NAME and name in ("resolutions", "sources"))),
      str([name for name in ctl.AUTOBRR_FILTER_LISTS if not all(isinstance(b.get(name), list) for b in bodies)]))
check("liste des champs : celle que l'interface web d'autobrr envoie toujours",
      {"resolutions", "codecs", "sources", "containers", "match_hdr", "except_hdr", "match_other", "except_other",
       "match_release_types", "formats", "quality", "media", "origins", "except_origins", "bonus", "announce_types",
       "match_language", "except_language"} == set(ctl.AUTOBRR_FILTER_LISTS))
check("types d'annonce : « nouveautés » (NEW) à la création, comme le fait autobrr, jamais vide qui prendrait promotions et re-publications",
      all(b["announce_types"] == ["NEW"] for b in bodies))
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}],
                 detail={"id": 9, "announce_types": [], "resolutions": ["1080p"], "codecs": None}, state={"preset": "1080p"})
put = sent(calls, "PUT", ctl.FILTER_NAME)[0]
check("mise à jour : les listes réglées par l'utilisateur sont gardées, les autres envoyées vides (jamais null)",
      put["resolutions"] == ["1080p"] and put["codecs"] == [] and all(isinstance(put[name], list) for name in ctl.AUTOBRR_FILTER_LISTS))
check("mise à jour : un filtre resté sans type d'annonce (version précédente) retrouve NEW ; un type réglé par l'utilisateur est gardé",
      put["announce_types"] == ["NEW"]
      and sent(scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}],
                        detail={"id": 9, "announce_types": ["NEW", "PROMO"]}), "PUT", ctl.FILTER_NAME)[0]["announce_types"] == ["NEW", "PROMO"])

# actions : sans identifiant autobrr en ajoute une de plus à chaque mise à jour (chaque nouveauté partirait plusieurs fois vers qBittorrent)
action = "qBittorrent : agrégateur"
calls = scenario([{"id": 9, "name": ctl.FILTER_NAME, "enabled": True}], [{"id": 1}],
                 detail={"id": 9, "actions": [{"id": 31, "name": action}, {"id": 32, "name": action}, {"id": 33, "name": "Mon action"}]})
put = sent(calls, "PUT", ctl.FILTER_NAME)[0]
check("mise à jour : l'action existante est reprise par son identifiant, pas ajoutée une fois de plus",
      [a.get("id") for a in put["actions"]] == [31] and len(put["actions"]) == 1, str(put["actions"])[:120])
check("mise à jour : les doublons déjà créés sont supprimés, l'action de l'utilisateur est laissée",
      [p for m, p, b in calls if m == "DELETE"] == ["/actions/32"])
calls = scenario([], [{"id": 1}], created={"id": 42, "actions": [{"id": 7, "name": action}]})
check("création : l'action posée avec le filtre est reprise par la mise à jour qui suit (pas de doublon dès le premier passage)",
      all([a.get("id") for a in b["actions"]] == [7] for b in sent(calls, "PUT")))

# contrôle horaire : les filtres suivent les nouveaux indexeurs et le préréglage Recyclarr
ran = []
real = (ctl.autobrr_session, ctl.autobrr_sync_indexers, ctl.provision_autobrr_filter, ctl.container_running, ctl.env_read)
ctl.autobrr_session, ctl.container_running = lambda: "c", lambda name: True
ctl.provision_autobrr_filter = lambda cookie: ran.append("filtres")
try:
    ctl.autobrr_sync_indexers, ctl.env_read = lambda cookie: 0, lambda: {"RECYCLARR_PRESET": "1080p"}
    ctl.AUTOBRR_FEEDS_STATE.write_text(json.dumps({"preset": "1080p"}))
    ctl.autobrr_follow_prowlarr()
    check("contrôle horaire : rien de changé, les filtres ne sont pas réécrits (réglages de l'utilisateur épargnés)", ran == [])
    ctl.env_read = lambda: {"RECYCLARR_PRESET": "2160p"}
    ctl.autobrr_follow_prowlarr()
    check("contrôle horaire : préréglage Recyclarr changé -> filtres mis à jour", ran == ["filtres"])
    ctl.autobrr_sync_indexers, ctl.env_read = lambda cookie: 2, lambda: {"RECYCLARR_PRESET": "1080p"}
    ctl.autobrr_follow_prowlarr()
    check("contrôle horaire : nouveaux indexeurs -> filtres mis à jour pour les couvrir", ran == ["filtres", "filtres"])
finally:
    ctl.autobrr_session, ctl.autobrr_sync_indexers, ctl.provision_autobrr_filter, ctl.container_running, ctl.env_read = real

# indexeurs de Prowlarr -> autobrr : un flux Torznab par indexeur torrent actif, sans doublon ni écrasement
class Feeds:
    """Faux autobrr : garde les indexeurs et flux créés, comme le vrai."""
    def __init__(self, feeds=None, indexers=None, refuse_feed=False, refuse_start=False):
        self.feeds, self.indexers, self.calls = feeds or [], indexers or [], []
        self.refuse_feed, self.refuse_start = refuse_feed, refuse_start

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
            # comme le vrai autobrr : le flux créé par l'API n'a ni dernière ni prochaine exécution tant qu'on ne l'ordonnance pas
            self.feeds.append({**body, "id": 200 + len(self.feeds), "indexer": {"id": body.get("indexer_id")}})
            return Reply({"id": self.feeds[-1]["id"]}, 201)
        if method == "PATCH" and path.startswith("/feeds/") and path.endswith("/enabled"):
            if self.refuse_start:
                return Reply({}, 500)
            for feed in self.feeds:
                if path == f"/feeds/{feed['id']}/enabled":
                    feed["next_run"] = "2026-11-18T17:00:00+01:00"
            return Reply({}, 204)
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
check("flux créé : lancé aussitôt (autobrr ne l'ordonnance pas de lui-même, le flux ne tournerait jamais)",
      [(p, b) for m, p, b in fake.calls if m == "PATCH"] == [("/feeds/200/enabled", {"enabled": True}), ("/feeds/201/enabled", {"enabled": True})]
      and all(f.get("next_run") for f in fake.feeds))
fake.calls.clear()
check("2e passage : rien n'est refait (pas de doublon, pas d'écriture, pas de nouveau lancement)", sync(fake, prowlarr) == 0
      and not [1 for m, p, b in fake.calls if m in ("POST", "PUT", "PATCH")])
fake.calls.clear()
count = sync(fake, prowlarr, key="cle2")
puts = [(p, b) for m, p, b in fake.calls if m == "PUT"]
check("clé de Prowlarr renouvelée : les flux sont corrigés, sans toucher à l'état ni à l'intervalle choisis",
      count == 2 and len(puts) == 2 and all(b["api_key"] == "cle2" for p, b in puts))
manual = Feeds(feeds=[{"id": 5, "name": "Mon Alpha", "enabled": False, "interval": 60, "url": "http://prowlarr:9696/prowlarr/1/api", "indexer": {"id": 9}}])
(root / "var/sb/autobrr-flux.json").write_text('{"key": "%s"}' % __import__("hashlib").sha256(b"cle1").hexdigest())
sync(manual, prowlarr[:1])
check("flux déjà ajouté à la main (même adresse) : reconnu, ni doublé, ni réactivé (ni lancé), ni renommé",
      not [1 for m, p, b in manual.calls if m in ("POST", "PUT", "PATCH")])

# flux créés par une version qui ne les lançait pas : jamais ordonnancés (dernière et prochaine exécution à 0001-01-01)
KEY1 = '{"key": "%s"}' % __import__("hashlib").sha256(b"cle1").hexdigest()
NEVER, PLANNED = "0001-01-01T00:00:00Z", "2026-11-18T17:00:00+01:00"
stuck = Feeds(feeds=[
    {"id": 5, "name": "Prowlarr : Alpha", "enabled": True, "url": "http://prowlarr:9696/prowlarr/1/api", "indexer": {"id": 11}, "last_run": NEVER, "next_run": NEVER},
    {"id": 6, "name": "Prowlarr : Jackett : Gamma", "enabled": True, "url": "http://prowlarr:9696/prowlarr/4/api", "indexer": {"id": 12}, "next_run": PLANNED}],
    indexers=[{"id": 11, "name": "Prowlarr : Alpha", "enabled": True}, {"id": 12, "name": "Prowlarr : Jackett : Gamma", "enabled": True}])
(root / "var/sb/autobrr-flux.json").write_text(KEY1)
count = sync(stuck, [prowlarr[0], prowlarr[3]])
check("flux existant jamais ordonnancé : lancé ; celui déjà planifié : laissé ; rien à recréer ni à réécrire",
      [(p, b) for m, p, b in stuck.calls if m == "PATCH"] == [("/feeds/5/enabled", {"enabled": True})] and count == 0
      and not [1 for m, p, b in stuck.calls if m in ("POST", "PUT")], str(stuck.calls)[:200])
stuck.calls.clear()
sync(stuck, [prowlarr[0], prowlarr[3]])
check("contrôle suivant : plus rien à lancer", not [1 for m, p, b in stuck.calls if m == "PATCH"])
off = Feeds(feeds=[{"id": 5, "name": "Prowlarr : Alpha", "enabled": True, "url": "http://prowlarr:9696/prowlarr/1/api", "indexer": {"id": 11}, "next_run": NEVER}],
            indexers=[{"id": 11, "name": "Prowlarr : Alpha", "enabled": False}])
sync(off, prowlarr[:1])
check("indexeur coupé par l'utilisateur dans autobrr : son flux n'est pas relancé", not [1 for m, p, b in off.calls if m == "PATCH"])
(root / "var/sb/autobrr-flux.json").unlink(missing_ok=True)
nostart = Feeds(refuse_start=True)
try:
    sync(nostart, prowlarr[:1])
    raised = False
except ctl.SeedboxError as error:
    raised = "non lancé" in str(error)
check("lancement refusé : l'erreur remonte, le flux créé reste, l'état n'est pas noté (nouvel essai au contrôle suivant)",
      raised and len(nostart.feeds) == 1 and len(nostart.indexers) == 1 and not (root / "var/sb/autobrr-flux.json").exists())
nostart.refuse_start = False
nostart.calls.clear()
sync(nostart, prowlarr[:1])
check("contrôle suivant : indexeur et flux reconnus, rien n'est recréé",
      len(nostart.feeds) == 1 and len(nostart.indexers) == 1 and not [1 for m, p, b in nostart.calls if m == "POST"])
(root / "var/sb/autobrr-flux.json").unlink(missing_ok=True)

# verrouillage d'autobrr : après 5 connexions ratées de suite il répond 429 (un essai toutes les 20 s)
class Rep:
    def __init__(self, status):
        self.status = status

    ok = property(lambda self: 200 <= self.status < 300)

    def cookies(self):
        return "sid=1"


def session_with(logins, recover=lambda password: False):
    answers = iter(logins)
    seen = []

    def request(method, url, **kw):
        if url.endswith("/onboard"):
            return Rep(404)
        seen.append(url)
        return Rep(next(answers))
    real = (ctl.request, ctl.env_read, ctl.autobrr_recover_password, ctl.AUTOBRR_LOGIN_WAIT, ctl.svc)
    ctl.request, ctl.env_read, ctl.autobrr_recover_password, ctl.AUTOBRR_LOGIN_WAIT = request, (lambda: {"AUTOBRR_ADMIN_PASSWORD": "pw"}), recover, 0
    ctl.svc = lambda name, port: f"http://{name}:{port}"
    try:
        return ctl.autobrr_session(), len(seen)
    finally:
        ctl.request, ctl.env_read, ctl.autobrr_recover_password, ctl.AUTOBRR_LOGIN_WAIT, ctl.svc = real


check("connexion à autobrr bloquée (429) : attente puis nouvel essai, sans toucher au mot de passe", session_with([429, 204]) == ("sid=1", 2))
try:
    session_with([429, 429])
    message = ""
except ctl.SeedboxError as error:
    message = str(error)
check("autobrr toujours bloqué après l'attente : message clair (réessayer dans une minute), pas « mot de passe changé »",
      "trop de connexions ratées" in message and "dans une minute" in message and "ne correspond plus" not in message, message)
check("mot de passe refusé (403) : repris par l'outil d'autobrr puis nouvelle connexion", session_with([403, 204], recover=lambda password: True) == ("sid=1", 2))

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
