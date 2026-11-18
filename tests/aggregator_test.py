"""Agrégateur de releases : lecture des titres, regroupement par œuvre, collecte (Prowlarr, Jackett, Torznab), catalogue (période,
catégorie, recherche, filtres, tri, pagination), purge, envoi à qBittorrent sans doublon, tableau de bord et alerte, avec des
réponses simulées (aucun service réel)."""
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbagg"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

agg = ctl.aggregator()
P = agg.P
ctl.ENV_FILE.write_text("PROWLARR_API_KEY=pk\nJACKETT_API_KEY=jk\nQBIT_USERNAME=admin\nQBIT_PASSWORD=secret\n")
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def fails(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ctl.SeedboxError as error:
        return str(error)
    return None


# ------------------------------------------------------------------ titres, catégories, rapprochement
def ident(title, cats=()):
    category = P.resolve_category(list(cats), title)
    return P.identity(category, [title], title, P.normalize_key(title))


matrix = ident("The.Matrix.1999.1080p.BluRay.x264-GRP", ["2040"])
check("film : titre et année extraits", (matrix["category"], matrix["title"], matrix["year"]) == ("films", "The Matrix", 1999), str(matrix))
episode = ident("Show.Name.S01E02.1080p.WEB-DL-GRP", ["5040"])
check("série : épisode retiré du titre d'œuvre", (episode["category"], episode["title"]) == ("series", "Show Name"), str(episode))
check("pack de saison « S01 » reconnu comme série", P.infer_category("Show Name S01 COMPLETE 720p") == "series")
album = ident("Daft Punk - Random Access Memories (2013) [FLAC 24bit]")
check("musique : format retiré, année du disque gardée", (album["category"], album["year"], "flac" in album["normalized"]) == ("musique", 2013, False), str(album))
check("jeu reconnu", P.infer_category("Elden Ring Deluxe Edition GOG") == "jeux")
check("logiciel reconnu", P.infer_category("Photoshop 2025 v26.0 x64 Crack") == "logiciels")
check("presse : numéro de revue gardé", ident("Science et Vie N°1250 Novembre 2026 PDF", ["7010"])["category"] == "presse")
check("identifiants Torznab : 2040 = films, 5000 = séries, 3000 = musique, 7020 = livres",
      [P.category_from_ids([x]) for x in ("2040", "5000", "3000", "7020", "102010")] == ["films", "series", "musique", "livres", "films"])
check("qualités : 2160p → 4K, 1080p → FHD, 720p → HD, FLAC → AUDIO", [P.quality_bucket(x) for x in ("2160p", "1080p", "720p", "FLAC")] == ["4K", "FHD", "HD", "AUDIO"])


def sim(a, b, category="films", ya=1999, yb=1999):
    return P.similarity({"category": category, "normalized": a, "year": ya}, {"category": category, "normalized": b, "year": yb})


check("« the matrix » et « matrix » : même œuvre", sim("the matrix", "matrix") >= P.MATCH_THRESHOLD)
check("« alien » et « aliens » : œuvres distinctes", sim("alien", "aliens") < P.MATCH_THRESHOLD)
check("années différentes : œuvres distinctes", sim("the matrix", "the matrix", yb=2003) == 0.0)
check("même IMDb : même œuvre quel que soit le titre", P.similarity({"category": "films", "normalized": "a", "year": 1, "imdb": "tt1"}, {"category": "films", "normalized": "zzz", "year": 2, "imdb": "tt1"}) == 1.0)
check("film et série compatibles, film et musique non", sim("dune", "dune", "films") == 1.0 and P.similarity(
    {"category": "films", "normalized": "dune", "year": None}, {"category": "musique", "normalized": "dune", "year": None}) == 0.0)
check("épisode et saison lus", (P.episode_label("Show.S02E05.720p"), P.season_key("Show.S02E05.720p"), P.season_key("Show Intégrale 1080p")) == ("S02E05", "2", "complete"))
check("clé : IMDb d'abord, puis TMDB, puis titre et année", (P.group_key("tt1", "5", "films", "x", 1999, ["x"]), P.group_key(None, "5", "films", "x", 1999, ["x"]),
                                                              P.group_key(None, None, "films", "x", 1999, ["x"])) == ("imdb:tt1", "tmdb:5", "work::1999::x"))

# ------------------------------------------------------------------ lecture des flux
now = int(time.time())


def rfc(offset):
    return time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(now - offset))


def iso(offset):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - offset))


def torznab(items):
    body = ""
    for it in items:
        attrs = "".join(f'<torznab:attr name="{k}" value="{v}"/>' for k, v in it.get("attrs", {}).items())
        body += (f"<item><title>{it['title']}</title><guid>{it['guid']}</guid><link>{it.get('link', '')}</link><pubDate>{rfc(it['age'])}</pubDate>"
                 f"<size>{it.get('size', 1000)}</size><jackettindexer id=\"x\">{it['indexer']}</jackettindexer>{attrs}</item>")
    return ('<?xml version="1.0"?><rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>' + body + "</channel></rss>").encode()


parsed = agg.parse_torznab(torznab([{"title": "Dune 2021 2160p", "guid": "g1", "link": "http://j/dl/1", "age": 100, "indexer": "Tracker A",
                                     "attrs": {"seeders": 12, "peers": 15, "grabs": 7, "imdbid": "1160419", "category": 2000, "magneturl": "magnet:?xt=urn:btih:" + "a" * 40}}]).decode(), "Jackett")
item = parsed[0]
check("Torznab : champs lus (tracker, IMDb, pairs, magnet, taille)", (item["tracker"], item["imdb"], item["seeders"], item["leechers"], item["grabs"], item["size"]) == ("Tracker A", "tt1160419", 12, 3, 7, 1000), str(item))
check("Torznab : empreinte du magnet", agg.infohash_of_magnet(item["magnet"]) == "a" * 40)
check("Torznab : DOCTYPE refusé", "refusé" in (fails(agg.parse_torznab, '<!DOCTYPE x [<!ENTITY a "b">]><rss/>', "J") or ""))
check("Torznab : erreur de l'indexeur remontée", "Invalid API Key" in (fails(agg.parse_torznab, '<error code="100" description="Invalid API Key"/>', "J") or ""))
check("flux illisible refusé", "illisible" in (fails(agg.parse_torznab, "pas du xml", "J") or ""))
pr = agg.parse_prowlarr(json.dumps([{"title": "X.2020.1080p", "guid": "u", "indexer": "I", "seeders": 4, "leechers": 1, "grabs": 2, "publishDate": iso(60),
                                     "downloadUrl": "http://p/1/download", "categories": [{"id": 2040}], "imdbId": 123456, "size": 5}]), "Prowlarr")
check("Prowlarr : champs lus", (pr[0]["tracker"], pr[0]["imdb"], pr[0]["cats"], pr[0]["seeders"]) == ("I", "tt0123456", ["2040"], 4), str(pr))
check("Prowlarr : message d'erreur remonté", "Unauthorized" in (fails(agg.parse_prowlarr, json.dumps({"message": "Unauthorized"}), "P") or ""))

# ------------------------------------------------------------------ page du torrent sur le tracker
rss = ('<?xml version="1.0"?><rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>'
       '<item><title>Page.Un.2020.1080p</title><guid>https://tracker.exemple/torrent/42</guid><comments>https://tracker.exemple/torrent/42#comments</comments>'
       '<link>http://j/dl/idx/?jackett_apikey=SECRET&amp;path=abc</link><jackettindexer id="x">T</jackettindexer></item>'
       '<item><title>Page.Deux.2020.1080p</title><guid>https://tracker.exemple/details.php?id=7</guid><link>http://j/dl/idx/?jackett_apikey=SECRET</link></item>'
       '<item><title>Page.Trois.2020.1080p</title><guid>https://tracker.exemple/dl/7?passkey=ABC</guid><comments>https://tracker.exemple/t/7?apikey=XYZ</comments><link>http://j/dl/3</link></item>'
       '<item><title>Page.Quatre.2020.1080p</title><guid>abc-123</guid><link>http://j/dl/4</link></item>'
       '<item><title>Page.Cinq.2020.1080p</title><guid>javascript:alert(1)</guid><comments>magnet:?xt=urn:btih:' + "c" * 40 + '</comments><link>http://j/dl/5</link></item>'
       '</channel></rss>')
pages = {r["title"]: r["details"] for r in agg.parse_torznab(rss, "Jackett")}
check("page tracker : lue depuis <comments>", pages["Page.Un.2020.1080p"] == "https://tracker.exemple/torrent/42#comments", str(pages))
check("page tracker : à défaut, le guid quand c'est une page web", pages["Page.Deux.2020.1080p"] == "https://tracker.exemple/details.php?id=7")
check("page tracker : jamais une adresse qui porte une clé ou un téléchargement", pages["Page.Trois.2020.1080p"] == "")
check("page tracker : guid qui n'est pas une adresse ignoré", pages["Page.Quatre.2020.1080p"] == "")
check("page tracker : schémas javascript et magnet refusés", pages["Page.Cinq.2020.1080p"] == "")
check("page tracker : l'adresse de téléchargement n'est jamais une page", agg.page_url("http://j/dl/1", download="http://j/dl/1") == "")
check("page tracker : jeton dans l'adresse refusé", agg.page_url("https://t.exemple/x?token=1") == "" and agg.page_url("https://t.exemple/x?rss_key=1") == "")
check("page tracker : adresse trop longue refusée", agg.page_url("https://t.exemple/" + "a" * 600) == "")
check("page tracker : un guid de type « guid » n'est pas pris pour une clé", agg.page_url("https://t.exemple/t?guid=5") == "https://t.exemple/t?guid=5")
pr2 = agg.parse_prowlarr(json.dumps([{"title": "P.Un.2020.1080p", "guid": "https://t.exemple/t/1", "infoUrl": "https://t.exemple/torrent/1", "downloadUrl": "http://p/1/download?apikey=K&file=x"},
                                     {"title": "P.Deux.2020.1080p", "guid": "https://t.exemple/t/2", "downloadUrl": "http://p/2/download"},
                                     {"title": "P.Trois.2020.1080p", "guid": "xyz", "downloadUrl": "http://p/3/download"}]), "Prowlarr")
check("Prowlarr : infoUrl, sinon guid web, sinon rien", [r["details"] for r in pr2] == ["https://t.exemple/torrent/1", "https://t.exemple/t/2", ""], str([r["details"] for r in pr2]))

# ------------------------------------------------------------------ simulation des services
ctl.container_running = lambda name: name in ("prowlarr", "jackett", "qbittorrent")
ctl.svc = lambda name, port: f"http://{name}:{port}"
ctl.say = lambda *a, **k: None
agg.say = lambda *a, **k: None
Reply = agg.Reply
calls, notes = [], []
world = {"prowlarr": {}, "jackett": {}, "custom": {}, "fail": set(), "torrents": {}, "qbit": {}, "qbit_login": True}


def prowlarr_row(title, guid, indexer, age, seeders=5, cats=(2040,), imdb=None, link=None):
    return {"title": title, "guid": guid, "indexer": indexer, "seeders": seeders, "leechers": 1, "grabs": 3, "publishDate": iso(age),
            "downloadUrl": link or f"http://prowlarr:9696/prowlarr/1/download?id={guid}", "categories": [{"id": c} for c in cats], "imdbId": imdb, "size": 4 * 1024 ** 3}


def bencode_torrent(name):
    info = f"d6:lengthi1e4:name{len(name)}:{name}12:piece lengthi16384e6:pieces20:{'x' * 20}e".encode()
    return b"d4:infod" + info[1:-1] + b"ee", hashlib.sha1(info).hexdigest()


def fake_call(method, url, headers=None, data=None, timeout=60):
    calls.append((method, url))
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    host = parts.hostname
    if host in world["fail"]:
        raise OSError("connexion refusée")
    if parts.path == "/prowlarr/api/v1/search":
        cat = tuple(v for k, v in urllib.parse.parse_qsl(parts.query) if k == "categories")
        key = ",".join(cat)
        return Reply(200, {}, json.dumps(world["prowlarr"].get(key, [])).encode(), "")
    if parts.path.startswith("/jackett/api/v2.0/indexers/all/results/torznab/api"):
        return Reply(200, {}, world["jackett"].get(query.get("cat", ""), torznab([])), "")
    if host == "indexeur.test":
        if query.get("apikey") != "cle-secrete-xyz":
            return Reply(401, {}, b"", "")
        return Reply(200, {}, world["custom"].get(query.get("cat", ""), torznab([])), "")
    if host == "qbittorrent":
        if parts.path == "/api/v2/auth/login":
            ok = world["qbit_login"] and b"secret" in (data or b"")
            return Reply(200 if ok else 403, {}, b"Ok." if ok else b"Fails.", "SID=abc" if ok else "")
        if headers.get("Cookie") != "SID=abc":
            return Reply(403, {}, b"Forbidden", "")
        if parts.path == "/api/v2/torrents/info":
            return Reply(200, {}, json.dumps([{"hash": h} for h in query.get("hashes", "").split("|") if h in world["qbit"]]).encode(), "")
        if parts.path == "/api/v2/torrents/createCategory":
            return Reply(200, {}, b"", "")
        if parts.path == "/api/v2/torrents/add":
            world["qbit_added"] = data
            for h in world.get("adds", []):
                world["qbit"][h] = True
            return Reply(200, {}, b"Ok.", "")
    if host == "prowlarr" and "/download" in parts.path:
        name = query.get("id", "x")
        if name in world["torrents"]:
            kind, payload = world["torrents"][name]
            if kind == "magnet":
                return Reply(302, {"location": payload}, b"", "")
            return Reply(200, {}, payload, "")
        return Reply(404, {}, b"", "")
    raise AssertionError(f"requête inattendue : {method} {url}")


agg.call = fake_call
ctl.alerts().notify = lambda title, detail, level="warning": notes.append((title, detail))

# ------------------------------------------------------------------ collecte
world["prowlarr"]["2000"] = [
    prowlarr_row("The.Matrix.1999.1080p.BluRay.x264-AA", "m1", "Tracker A", 3600, 20, imdb=133093),
    prowlarr_row("Matrix 1999 2160p UHD", "m2", "Tracker B", 7200, 5),
    prowlarr_row("Alien.1979.1080p", "a1", "Tracker A", 5000),
    prowlarr_row("Aliens.1986.1080p", "a2", "Tracker A", 5000),
    prowlarr_row("Vieux.Film.2001.720p", "old", "Tracker A", 20 * 86400),
    prowlarr_row("Film.Semaine.2020.1080p", "w1", "Tracker A", 3 * 86400, 2),
]
world["prowlarr"]["5000"] = [
    prowlarr_row("Show.Name.S01E01.1080p.WEB-DL-GRP", "s1", "Tracker A", 4000, 9, cats=(5040,)),
    prowlarr_row("Show.Name.S01E02.1080p.WEB-DL-GRP", "s2", "Tracker A", 3000, 8, cats=(5040,)),
    prowlarr_row("Show.Name.S02E01.720p.WEB-DL-GRP", "s3", "Tracker B", 2000, 4, cats=(5040,)),
]
world["jackett"]["2000"] = torznab([{"title": "The Matrix 1999 720p", "guid": "jm1", "link": "http://jackett:9117/dl/1", "age": 1000, "indexer": "Tracker C",
                                     "attrs": {"seeders": 30, "peers": 31, "imdbid": "tt0133093", "category": 2040}}])
world["jackett"]["3000"] = torznab([{"title": "Artiste - Album (2020) [FLAC]", "guid": "jx1", "link": "http://jackett:9117/dl/2", "age": 900, "indexer": "Tracker C",
                                     "attrs": {"seeders": 7, "category": 3040}}])
result = agg.sync(now=now)
check("collecte : œuvres regroupées (Matrix ×3 en une, Alien ≠ Aliens, série en une)", result["works"] == 6 and result["torrents"] == 10, str(result))
check("collecte : l'entrée de 20 jours est ignorée", result["collected"] > result["new"] and all("Vieux" not in w["title"] for w in agg.catalogue(period="all", now=now)["works"]))
cat = agg.catalogue(period="24h", now=now)
matrix_work = next(w for w in cat["works"] if w["title"] == "The Matrix")
check("Matrix : 3 torrents, 3 trackers, IMDb conservé, meilleur seeders", (matrix_work["count"], matrix_work["trackers"], matrix_work["imdb"], matrix_work["seeders"]) == (3, ["Tracker A", "Tracker B", "Tracker C"], "tt0133093", 30), str(matrix_work))
check("Matrix : variantes classées 4K, FHD, HD", [g["bucket"] for g in matrix_work["groups"]] == ["4K", "FHD", "HD"])
show = next(w for w in cat["works"] if w["category"] == "series")
check("série : saisons séparées (2 puis 1) avec épisodes", show["seasons"] and [s["season"] for s in show["seasons"]] == ["2", "1"] and show["count"] == 3, str(show["seasons"] and [s["season"] for s in show["seasons"]]))
check("période 24 h : 5 œuvres (la semaine en plus donne 6)", cat["total"] == 5 and agg.catalogue(period="week", now=now)["total"] == 6, f"{cat['total']}")
check("compteurs par catégorie et par période", cat["counts"].get("films") == 3 and cat["counts"].get("series") == 1 and cat["counts"].get("musique") == 1 and cat["periods"]["all"] == 6, str(cat["counts"]) + str(cat["periods"]))
check("filtre catégorie", {w["category"] for w in agg.catalogue(period="all", category="series", now=now)["works"]} == {"series"})
check("recherche : ignore la période", agg.catalogue(period="24h", q="semaine", now=now)["total"] == 1)
check("recherche par mot d'un torrent", agg.catalogue(period="all", q="2160p", now=now)["total"] == 1)
check("filtre tracker et qualité", agg.catalogue(period="all", tracker="Tracker C", now=now)["total"] == 2 and agg.catalogue(period="all", quality="4K", now=now)["total"] == 1)
check("tri par seeders décroissant", [w["title"] for w in agg.catalogue(period="all", sort="seeders", now=now)["works"]][0] == "The Matrix")
check("tri par titre croissant", [w["title"] for w in agg.catalogue(period="all", sort="title", direction="asc", now=now)["works"]][:2] == ["Alien", "Aliens"])
check("pagination 20 / tout / page hors limites", agg.catalogue(period="all", per_page=20, now=now)["pages"] == 1 and agg.catalogue(period="all", per_page=0, now=now)["per_page"] == 0
      and agg.catalogue(period="all", per_page=20, page=9, now=now)["page"] == 1)
check("paramètres invalides ramenés aux valeurs sûres", agg.catalogue(period="x", sort="DROP", per_page="9", category="zz", now=now)["period"] == "24h")
check("recherche avec guillemets et pourcentage sans erreur SQL", agg.catalogue(period="all", q="100% ' \" _", now=now)["total"] == 0)

before = result["torrents"]
world["prowlarr"]["2000"][0]["seeders"] = 99
again = agg.sync(now=now + 60)
check("deuxième collecte : rien de nouveau, seeders mis à jour", again["new"] == 0 and again["torrents"] == before and next(w for w in agg.catalogue(period="all", now=now)["works"] if w["title"] == "The Matrix")["seeders"] == 99, str(again))

# purge : l'horloge avance de 8 jours, plus rien ne reste
late = now + 8 * 86400
check("purge à 7 jours : tout est retiré", agg.sync(now=late)["torrents"] <= 1 and agg.purge_now(now=late + 1) >= 0)
agg.configure(retention=30)
check("rétention réglable (1 à 60 jours)", agg.settings()["retention"] == 30 and "rétention" in (fails(agg.configure, retention=0) or ""))

# ------------------------------------------------------------------ sources et réglages
check("intervalle invalide refusé, valide gardé", "intervalle" in (fails(agg.configure, interval=7) or "") and agg.configure(interval=120)["interval"] == 120)
check("catégorie qBittorrent invalide refusée", "catégorie" in (fails(agg.configure, category="a/b") or ""))
check("source : adresse non http refusée", "http" in (fails(agg.add_source, "X", "ftp://x") or ""))
sid = agg.add_source("Mon Indexeur", "http://indexeur.test/api", "cle-secrete-xyz")
check("source ajoutée, identifiant propre, clé jamais montrée", sid == "mon-indexeur" and "cle-secrete-xyz" not in json.dumps(agg.dashboard(now)))
check("fichier de réglages réservé à root", oct(os.stat(agg.SETTINGS_FILE).st_mode)[-3:] == "600")
check("source homonyme : identifiant distinct", agg.add_source("Mon Indexeur", "http://indexeur.test/api", "cle-secrete-xyz") == "mon-indexeur-2")
agg.remove_source("mon-indexeur-2")
check("Prowlarr ne se supprime pas", "se suppriment pas" in (fails(agg.remove_source, "prowlarr") or ""))
world["custom"]["2000"] = torznab([{"title": "Dune.2021.2160p", "guid": "d1", "link": "http://indexeur.test/dl/1", "age": 500, "indexer": "Indexeur Perso", "attrs": {"seeders": 3, "category": 2040}}])
check("test d'une source", agg.test_source("mon-indexeur")["count"] == 1)
agg.set_source("prowlarr", False)
agg.set_source("jackett", False)
world["prowlarr"]["2000"][0]["seeders"] = 20
r = agg.sync(now=late + 100)
check("collecte limitée aux sources actives (Torznab seul)", r["new"] == 1 and any("indexeur.test" in u for _, u in calls[-10:]), str(r))
agg.set_source("prowlarr", True)
agg.set_source("jackett", True)

# ------------------------------------------------------------------ envoi à qBittorrent
world["prowlarr"]["2000"] = [prowlarr_row("Film.Envoi.2024.1080p", "e1", "Tracker A", 100, link="http://prowlarr:9696/prowlarr/1/download?id=t1"),
                             prowlarr_row("Film.Magnet.2024.1080p", "e2", "Tracker A", 100, link="http://prowlarr:9696/prowlarr/1/download?id=t2"),
                             prowlarr_row("Film.Casse.2024.1080p", "e3", "Tracker A", 100, link="http://prowlarr:9696/prowlarr/1/download?id=t3")]
torrent_bytes, torrent_hash = bencode_torrent("Film.Envoi.2024")
world["torrents"] = {"t1": ("file", torrent_bytes), "t2": ("magnet", "magnet:?xt=urn:btih:" + "b" * 40), "t3": ("file", b"<html>erreur</html>")}
agg.sync(now=late + 200)
check("empreinte d'un .torrent calculée (SHA-1 du dictionnaire info)", agg.torrent_infohash(torrent_bytes) == torrent_hash)
try:
    agg.torrent_infohash(b"<html>")
    check("fichier non torrent refusé", False)
except ValueError:
    check("fichier non torrent refusé", True)


def row_id(title):
    con = agg.connect()
    found = con.execute("SELECT id FROM torrents WHERE title = ?", (title,)).fetchone()["id"]
    con.close()
    return found


world["adds"] = [torrent_hash]
first = agg.send(row_id("Film.Envoi.2024.1080p"), now=late + 300)
check("envoi : .torrent téléchargé par la console puis ajouté (fichier joint, pas d'adresse)", first["state"] == "envoye" and b"catalogue.torrent" in world["qbit_added"] and b"name=\"urls\"" not in world["qbit_added"], str(first))
check("envoi : catégorie qBittorrent appliquée", b"agregateur" in world["qbit_added"])
check("envoi : le catalogue marque le torrent envoyé", any(v["sent"] for w in agg.catalogue(period="all", now=late + 300)["works"] for g in (w["groups"] or []) for v in g["variants"]))
second = agg.send(row_id("Film.Envoi.2024.1080p"), now=late + 301)
check("envoi : jamais deux fois", second["state"] == "deja")
world["qbit"][torrent_hash] = True
with agg.connect() as con:
    con.execute("DELETE FROM sent")
third = agg.send(row_id("Film.Envoi.2024.1080p"), now=late + 302)
check("envoi : déjà présent dans qBittorrent → pas d'ajout", third["state"] == "present")
world["adds"] = ["b" * 40]
magnet = agg.send(row_id("Film.Magnet.2024.1080p"), now=late + 303)
check("envoi : redirection vers un magnet suivie, ajouté par adresse", magnet["state"] == "envoye" and b'name="urls"' in world["qbit_added"] and b"btih:" + b"b" * 40 in world["qbit_added"], str(magnet))
check("envoi : réponse qui n'est pas un .torrent → message clair", "n'a pas fourni" in (fails(agg.send, row_id("Film.Casse.2024.1080p")) or ""))
check("envoi : torrent purgé → message clair", "introuvable" in (fails(agg.send, 99999) or ""))
world["qbit_login"] = False
check("envoi : connexion qBittorrent refusée → message clair", "bannie" in (fails(agg.send, row_id("Film.Envoi.2024.1080p"), True) or ""))
world["qbit_login"] = True
running_before = ctl.container_running
ctl.container_running = lambda name: name in ("prowlarr", "jackett")
check("envoi : qBittorrent arrêté → message clair", "pas démarré" in (fails(agg.send, row_id("Film.Magnet.2024.1080p"), True) or ""))
ctl.container_running = running_before

# ------------------------------------------------------------------ santé, alerte, planification
dash = agg.dashboard(late + 400)
check("tableau de bord : sources, totaux et chronologie de 7 jours", len(dash["sources"]) == 3 and len(dash["timeline"]) == 7 and dash["totals"]["torrents"] > 0, str(dash["totals"]))
world["fail"] = {"prowlarr"}
for step in range(3):
    agg.sync(now=late + 1000 + step * 100, only="prowlarr")
dash = agg.dashboard(late + 1300)
prow = next(s for s in dash["sources"] if s["id"] == "prowlarr")
check("source en échec : état, 3 échecs de suite, problème signalé", prow["state"] == "error" and prow["fails"] >= 3 and dash["problems"], str(prow["state"]) + str(prow["fails"]))
check("alerte envoyée une seule fois par jour", len(notes) == 1 and "Prowlarr" in notes[0][0], str(notes))
agg.sync(now=late + 1400, only="prowlarr")
check("pas de seconde alerte le même jour", len(notes) == 1)
agg.sync(now=late + 1400 + 2 * 86400, only="prowlarr")
check("nouvelle alerte le lendemain si la panne dure", len(notes) == 2)
world["fail"] = set()

launched = []
with agg.connect() as con:
    agg.kv_set(con, "last_start", late)
check("planification : pas avant l'intervalle", agg.tick(late + 60 * 60, launcher=lambda: launched.append(1)) is False and not launched)
check("planification : lancée une fois l'intervalle écoulé (120 min)", agg.tick(late + 121 * 60, launcher=lambda: launched.append(1)) is True and launched == [1])
agg.configure(enabled=False)
check("planification : rien quand la collecte est désactivée", agg.tick(late + 10 ** 6, launcher=lambda: launched.append(2)) is False)
agg.configure(enabled=True)
agg.LOCK_FILE.write_text(str(os.getppid()))
check("collecte déjà en cours : seconde refusée", "déjà en cours" in (fails(agg.sync) or "") and agg.tick(late + 10 ** 6, launcher=lambda: launched.append(3)) is False)
agg.LOCK_FILE.unlink()

# ------------------------------------------------------------------ la page du tracker traverse le catalogue
with agg.connect() as con:
    idx = agg.Index(con)
    agg.ingest(con, agg.parse_torznab(rss, "Jackett")[:2], "src-page", late, idx, 0)
    agg.ingest(con, [{**agg.parse_torznab(rss, "Jackett")[3], "details": ""}], "src-page", late, idx, 0)
    refreshed = [{**agg.parse_torznab(rss, "Jackett")[0], "details": "https://tracker.exemple/torrent/42"}]
    agg.ingest(con, refreshed, "src-page", late + 5, idx, 0)
    stored = {r["title"]: r["details"] for r in con.execute("SELECT title, details FROM torrents WHERE source = 'src-page'")}
check("page tracker : enregistrée à l'insertion", stored["Page.Deux.2020.1080p"] == "https://tracker.exemple/details.php?id=7", str(stored))
check("page tracker : mise à jour quand elle change", stored["Page.Un.2020.1080p"] == "https://tracker.exemple/torrent/42")
check("page tracker : absente = chaîne vide, pas de valeur nulle", stored["Page.Quatre.2020.1080p"] == "")
shown = {v["torrent_title"]: v["details"] for w in agg.catalogue(period="all", q="Page", now=late + 10)["works"] for g in (w["groups"] or []) for v in g["variants"]}
check("page tracker : exposée par le catalogue, sans aucune clé", shown.get("Page.Deux.2020.1080p") == "https://tracker.exemple/details.php?id=7"
      and not any("apikey" in (v or "").lower() for v in shown.values()), str(shown))
with agg.connect() as con:
    con.execute("UPDATE torrents SET details = NULL WHERE title = 'Page.Deux.2020.1080p'")
    agg.ingest(con, [agg.parse_torznab(rss, "Jackett")[1]], "src-page", late + 9, agg.Index(con), 0)
    con.execute("UPDATE torrents SET details = NULL")
check("catalogue : une ancienne ligne sans page ne casse rien", all(isinstance(v, str) for w in agg.catalogue(period="all", now=late + 11)["works"] for g in (w["groups"] or []) for v in g["variants"] for v in [v["details"]]))

# ancienne base (avant la colonne « page ») : migrée au premier accès, données conservées
import sqlite3 as _sqlite3
agg.DB_FILE.unlink()
legacy = _sqlite3.connect(agg.DB_FILE)
legacy.execute("CREATE TABLE torrents (id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL, source TEXT NOT NULL, guid TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL, "
               "norm_title TEXT NOT NULL, tracker TEXT NOT NULL, category TEXT NOT NULL, bucket TEXT NOT NULL, qrank INTEGER NOT NULL, link TEXT, magnet TEXT, infohash TEXT, "
               "size INTEGER NOT NULL DEFAULT 0, seeders INTEGER NOT NULL DEFAULT 0, leechers INTEGER NOT NULL DEFAULT 0, grabs INTEGER NOT NULL DEFAULT 0, published INTEGER, "
               "first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, UNIQUE(source, guid))")
legacy.execute("INSERT INTO torrents(work_id, source, guid, ref, title, norm_title, tracker, category, bucket, qrank, first_seen, last_seen) VALUES(1,'s','g','r','Ancien.2019','ancien','T','films','FHD',1,1,1)")
legacy.commit()
legacy.close()
with agg.connect() as con:
    columns = {r["name"] for r in con.execute("PRAGMA table_info(torrents)")}
    kept = con.execute("SELECT title, details FROM torrents").fetchone()
check("migration : colonne « details » ajoutée, lignes conservées", "details" in columns and kept["title"] == "Ancien.2019" and kept["details"] is None)
with agg.connect() as con:
    check("migration : un second accès ne refait rien", "details" in {r["name"] for r in con.execute("PRAGMA table_info(torrents)")})

# --- connexion à qBittorrent : réponses de connexion inhabituelles ---------------------------------------------------------
real_call = agg.call
def qbit_reply(login, probe=None):
    def fake(method, url, headers=None, data=None, timeout=60):
        if url.endswith("/auth/login"):
            return login
        if url.endswith("/app/version"):
            return probe
        raise AssertionError(url)
    agg.call = fake
    try:
        return agg.qbit_session()
    except ctl.SeedboxError as error:
        return str(error)
    finally:
        agg.call = real_call
check("qBittorrent : réponse 204 sans session acceptée quand l'adresse est de confiance", qbit_reply(Reply(204, {}, b"", ""), Reply(200, {}, b"v5", ""))[1] == {})
_Cookie = type("R", (), {"__init__": lambda self, status, body=b"", cookie="": setattr(self, "status", status) or setattr(self, "body", body) or setattr(self, "cookie", cookie)})
seen = []
def cookie_fake(method, url, headers=None, data=None, timeout=60):
    seen.append(dict(headers or {}))
    if url.endswith("/auth/login"):
        return _Cookie(204, b"", "SID=abc")
    return _Cookie(200 if (headers or {}).get("Cookie") == "SID=abc" else 403)
real_call2 = agg.call
agg.call = cookie_fake
try:
    got = agg.qbit_session()
finally:
    agg.call = real_call2
check("qBittorrent : réponse 204 portant le cookie de session -> le cookie est utilisé", got[1] == {"Cookie": "SID=abc"}, str(got))
check("qBittorrent : réponse 204 puis accès refusé -> message précis", "login HTTP 204, puis HTTP 403" in qbit_reply(Reply(204, {}, b"", ""), Reply(403, {}, b"", "")))
check("qBittorrent : « Fails. » -> identifiants refusés", "mot de passe refusé" in qbit_reply(Reply(200, {}, b"Fails.", "")))
check("qBittorrent : 403 -> adresse bannie", "bannie" in qbit_reply(Reply(403, {}, b"", "")))

# --- suggestions pour le ratio ------------------------------------------------------------------------
pick_now = 2_000_000_000
con = agg.connect()
for i, (title, seeders, leechers, age_h, size) in enumerate([("Très demandé", 2, 30, 2, 4 << 30), ("Demandé mais vieux", 2, 30, 60, 4 << 30), ("Saturé de seeders", 80, 30, 2, 4 << 30),
                                                           ("Sans téléchargeur", 5, 0, 1, 4 << 30), ("Trop gros", 1, 40, 1, 60 << 30), ("Déjà envoyé", 1, 40, 1, 4 << 30)], 9001):
    con.execute("INSERT INTO works(id, key, category, title, norm, created, latest) VALUES(?,?,?,?,?,?,?)", (i, f"k{i}", "movies", title, title.lower(), pick_now, pick_now))
    con.execute("INSERT INTO torrents(id, work_id, source, guid, ref, title, norm_title, tracker, category, bucket, qrank, size, seeders, leechers, published, first_seen, last_seen) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, i, "s", f"pg{i}", f"pr{i}", title, title.lower(), "tk", "movies", "1080p", 1, size, seeders, leechers,
                                                              pick_now - age_h * 3600, pick_now, pick_now))
con.execute("INSERT INTO sent(ref, infohash, title, at, state) VALUES('pr9006', '', 'x', ?, 'envoye')", (pick_now,))
con.commit()
con.close()
got = [p["title"] for p in agg.picks(10, now=pick_now)]
check("suggestions : récent, très demandé, peu de seeders en tête ; vieux, saturé, sans demande, trop gros ou déjà envoyé exclus ou après",
      got[0] == "Très demandé" and "Sans téléchargeur" not in got and "Trop gros" not in got and "Déjà envoyé" not in got and "Demandé mais vieux" not in got, str(got))

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
