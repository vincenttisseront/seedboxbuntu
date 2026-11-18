"""SeedBoxBuntu - agrégateur de releases : catalogue par œuvre, collecte planifiée, envoi vers qBittorrent.

Remplace P2P Aggregator (retiré) :
  - collecte : les indexeurs de Prowlarr et de Jackett (installés avec la boîte) et toute source Torznab ajoutée à la main,
    par catégories (films, séries, musique, jeux, logiciels, livres/presse), toutes les 15 min à 6 h (1 h par défaut) ;
  - catalogue : les torrents sont regroupés par œuvre (IMDb, TMDB, revue, titre + année, avec rapprochement des titres proches),
    nouveautés 24 h / semaine, 7 catégories, recherche, filtres tracker et qualité, tri, pagination 20/40/60/tout ; pour une
    série, les variantes sont rangées par saison et par épisode ;
  - envoi : le .torrent est récupéré par la console puis ajouté à qBittorrent (aucun accès de qBittorrent à Prowlarr nécessaire) ;
    un torrent déjà envoyé ou déjà présent dans qBittorrent n'est jamais ajouté deux fois ;
  - purge : tout ce qui est plus vieux que la rétention (7 jours par défaut) disparaît ; un relevé par collecte alimente la
    chronologie du tableau de bord (indexés, ajoutés, purgés) ;
  - santé : état de chaque source ; alerte (une fois par jour) après 3 collectes en échec de suite.
Données : base SQLite dans /var/lib/seedboxbuntu/aggregator.db ; réglages et sources dans /etc/seedboxbuntu/aggregator.json
(clés d'API : fichier lisible par root seul).
Limites : les trackers privés qui n'ont pas d'API Torznab (ratio, messages non lus, sessions protégées) ne sont pas lus ici :
ces fonctions de P2P Aggregator et de tracker-session ont été retirées avec eux.
    seedboxctl aggregator show | sync | sources | add NOM URL [--key CLE] | remove ID | enable ID | disable ID | test ID
                          | config [--interval MIN] [--retention JOURS] [--limit N] [--category NOM] [--enable|--disable] | send ID | purge
"""
import hashlib
import http.client
import json
import os
import re
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import namedtuple
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import aggparse as P
import seedboxctl as ctl

DB_FILE = ctl.STATE / "aggregator.db"
SETTINGS_FILE = ctl.ETC / "aggregator.json"
LOCK_FILE = ctl.STATE / "aggregator.lock"
DAY = 86400
INTERVALS = (15, 30, 60, 120, 180, 240, 360)
PAGE_SIZES = (20, 40, 60, 0)
PERIODS = ("24h", "week", "all")
SORTS = {"seeders": "w.seeders", "leechers": "w.leechers", "completed": "w.grabs", "publishedAt": "w.latest", "title": "w.title COLLATE NOCASE",
         "quality": "w.qrank", "tracker": "(SELECT MIN(t.tracker) FROM torrents t WHERE t.work_id = w.id) COLLATE NOCASE"}
DEFAULTS = {"enabled": True, "interval": 60, "retention": 7, "limit": 100, "category": "agregateur", "alert": True, "prowlarr": True, "jackett": True,
            "sources": []}
GROUPS = (("films", ("2000",)), ("series", ("5000",)), ("musique", ("3000",)), ("jeux", ("1000", "4050")), ("logiciels", ("4000",)),
          ("livres", ("7000",)))
AUTO = {"prowlarr": ("Prowlarr", "prowlarr", 9696), "jackett": ("Jackett", "jackett", 9117)}
TORZNAB_NS = "{http://torznab.com/schemas/2015/feed}"
Reply = namedtuple("Reply", "status headers body cookie")
FAILS_BEFORE_ALERT = 3
MAX_FOLLOW = 4


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# ---------------------------------------------------------------------------------------------------------------
#  Réglages et sources
# ---------------------------------------------------------------------------------------------------------------
def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    cfg = {**DEFAULTS, **{k: v for k, v in (stored if isinstance(stored, dict) else {}).items() if k in DEFAULTS}}
    cfg["interval"] = min(INTERVALS, key=lambda v: abs(v - int(cfg["interval"] or 60))) if isinstance(cfg["interval"], (int, float)) else 60
    cfg["retention"] = max(1, min(60, int(cfg["retention"]))) if isinstance(cfg["retention"], (int, float)) else 7
    cfg["limit"] = max(10, min(300, int(cfg["limit"]))) if isinstance(cfg["limit"], (int, float)) else 100
    cfg["sources"] = [s for s in cfg["sources"] if isinstance(s, dict) and s.get("id") and s.get("url")] if isinstance(cfg["sources"], list) else []
    return cfg


def _save(cfg):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(cfg, ensure_ascii=False))
    os.chmod(temp, 0o600)
    os.replace(temp, SETTINGS_FILE)
    return cfg


def configure(interval=None, retention=None, limit=None, category=None, enabled=None, alert=None, prowlarr=None, jackett=None):
    cfg = settings()
    if interval is not None:
        if int(interval) not in INTERVALS:
            raise ctl.SeedboxError("intervalle de collecte : " + ", ".join(str(v) for v in INTERVALS) + " minutes")
        cfg["interval"] = int(interval)
    if retention is not None:
        if not 1 <= int(retention) <= 60:
            raise ctl.SeedboxError("rétention : de 1 à 60 jours")
        cfg["retention"] = int(retention)
    if limit is not None:
        if not 10 <= int(limit) <= 300:
            raise ctl.SeedboxError("résultats par requête : de 10 à 300")
        cfg["limit"] = int(limit)
    if category is not None:
        name = str(category).strip()
        if not re.fullmatch(r"[\w .-]{0,40}", name):
            raise ctl.SeedboxError("catégorie qBittorrent : lettres, chiffres, espace, point, tiret (40 au plus)")
        cfg["category"] = name
    for key, value in (("enabled", enabled), ("alert", alert), ("prowlarr", prowlarr), ("jackett", jackett)):
        if value is not None:
            cfg[key] = bool(value)
    return _save(cfg)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", P.fold(str(text).lower())).strip("-")[:40]


def add_source(name, url, key=""):
    name, url, key = str(name).strip(), str(url).strip(), str(key or "").strip()
    if not name or len(name) > 60:
        raise ctl.SeedboxError("nom de source : 1 à 60 caractères")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ctl.SeedboxError("adresse de la source : http:// ou https:// requis (flux Torznab)")
    ident = slug(name) or "source"
    cfg = settings()
    taken = {s["id"] for s in cfg["sources"]} | set(AUTO)
    base, n = ident, 2
    while ident in taken:
        ident, n = f"{base}-{n}", n + 1
    cfg["sources"].append({"id": ident, "name": name, "type": "torznab", "url": url, "key": key, "enabled": True})
    _save(cfg)
    return ident


def remove_source(ident):
    cfg = settings()
    if ident in AUTO:
        raise ctl.SeedboxError("Prowlarr et Jackett se désactivent dans les réglages, ils ne se suppriment pas")
    kept = [s for s in cfg["sources"] if s["id"] != ident]
    if len(kept) == len(cfg["sources"]):
        raise ctl.SeedboxError(f"source inconnue : {ident}")
    cfg["sources"] = kept
    _save(cfg)
    with connect() as con:
        con.execute("DELETE FROM torrents WHERE source = ?", (ident,))
        con.execute("DELETE FROM works WHERE id NOT IN (SELECT DISTINCT work_id FROM torrents)")
    return True


def set_source(ident, enabled):
    if ident in AUTO:
        configure(**{ident: enabled})
        return True
    cfg = settings()
    for source in cfg["sources"]:
        if source["id"] == ident:
            source["enabled"] = bool(enabled)
            _save(cfg)
            return True
    raise ctl.SeedboxError(f"source inconnue : {ident}")


def sources():
    """Sources connues : Prowlarr et Jackett quand ils tournent, puis celles ajoutées à la main."""
    cfg, env, out = settings(), ctl.env_read(), []
    for ident, (name, container, port) in AUTO.items():
        key = env.get(f"{ident.upper()}_API_KEY", "")
        present = bool(key) and ctl.container_running(container)
        out.append({"id": ident, "name": name, "type": ident, "auto": True, "container": container, "port": port, "key": key, "present": present,
                    "enabled": bool(cfg[ident]) and present})
    for source in cfg["sources"]:
        out.append({**source, "auto": False, "present": True, "enabled": bool(source.get("enabled", True))})
    return out


# ---------------------------------------------------------------------------------------------------------------
#  Base de données
# ---------------------------------------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL, norm TEXT NOT NULL, year INTEGER,
  imdb TEXT, tmdb TEXT, magazine TEXT, created INTEGER NOT NULL, latest INTEGER NOT NULL DEFAULT 0, seeders INTEGER NOT NULL DEFAULT 0,
  leechers INTEGER NOT NULL DEFAULT 0, grabs INTEGER NOT NULL DEFAULT 0, qrank INTEGER NOT NULL DEFAULT 9, ntorrents INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS works_key ON works(key);
CREATE INDEX IF NOT EXISTS works_latest ON works(latest);
CREATE INDEX IF NOT EXISTS works_category ON works(category);
CREATE TABLE IF NOT EXISTS torrents (
  id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL, source TEXT NOT NULL, guid TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL,
  norm_title TEXT NOT NULL, tracker TEXT NOT NULL, category TEXT NOT NULL, bucket TEXT NOT NULL, qrank INTEGER NOT NULL, link TEXT, magnet TEXT,
  infohash TEXT, size INTEGER NOT NULL DEFAULT 0, seeders INTEGER NOT NULL DEFAULT 0, leechers INTEGER NOT NULL DEFAULT 0,
  grabs INTEGER NOT NULL DEFAULT 0, published INTEGER, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, UNIQUE(source, guid));
CREATE INDEX IF NOT EXISTS torrents_work ON torrents(work_id);
CREATE INDEX IF NOT EXISTS torrents_ref ON torrents(ref);
CREATE TABLE IF NOT EXISTS sent (ref TEXT PRIMARY KEY, infohash TEXT, title TEXT, at INTEGER NOT NULL, state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sent_hash ON sent(infohash);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, source TEXT NOT NULL, ok INTEGER NOT NULL, error TEXT, collected INTEGER NOT NULL,
  new INTEGER NOT NULL, ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS runs_source ON runs(source, ts);
CREATE TABLE IF NOT EXISTS metrics (ts INTEGER PRIMARY KEY, works INTEGER NOT NULL, torrents INTEGER NOT NULL, collected INTEGER NOT NULL,
  new INTEGER NOT NULL, purged INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    con.executescript(SCHEMA)
    if "details" not in {row["name"] for row in con.execute("PRAGMA table_info(torrents)")}:
        con.execute("ALTER TABLE torrents ADD COLUMN details TEXT")     # page du torrent sur le tracker
    return con


def kv_get(con, key, default=None):
    row = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(con, key, value):
    con.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


# ---------------------------------------------------------------------------------------------------------------
#  Réseau (un seul point d'entrée, remplaçable par les tests)
# ---------------------------------------------------------------------------------------------------------------
def call(method, url, headers=None, data=None, timeout=60):
    """Requête sans suivi des redirections ; renvoie Reply(statut, en-têtes en minuscules, octets, cookies)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout, context=ssl._create_unverified_context())
    else:
        connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        connection.request(method, path, data, dict(headers or {}))
        response = connection.getresponse()
        body = response.read(30 * 1024 * 1024)
        pairs = response.getheaders()
        cookie = "; ".join(v.split(";", 1)[0] for k, v in pairs if k.lower() == "set-cookie")
        return Reply(response.status, {k.lower(): v for k, v in pairs}, body, cookie)
    finally:
        connection.close()


def _fetch_follow(url, headers=None, timeout=60):
    """GET avec suivi des redirections ; renvoie (Reply, adresse finale ou magnet)."""
    for _ in range(MAX_FOLLOW + 1):
        reply = call("GET", url, headers, None, timeout)
        location = reply.headers.get("location", "")
        if reply.status in (301, 302, 303, 307, 308) and location:
            if location.startswith("magnet:"):
                return reply, location
            url = urllib.parse.urljoin(url, location)
            continue
        return reply, url
    raise ctl.SeedboxError("trop de redirections en récupérant le torrent")


# ---------------------------------------------------------------------------------------------------------------
#  Lecture des réponses des indexeurs
# ---------------------------------------------------------------------------------------------------------------
def imdb_id(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return f"tt{int(digits):07d}" if digits and int(digits) > 0 else None


def tmdb_id(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return str(int(digits)) if digits and int(digits) > 0 else None


def to_epoch(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        return int(parsedate_to_datetime(text).timestamp())
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp())
    except ValueError:
        return None


def to_int(value):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


SECRET_KEYS = re.compile(r"(api_?key|pass_?key|torrent_?pass|rss_?key|token|secret|key)$", re.I)


def page_url(*candidates, download=""):
    """Première adresse utilisable comme page du torrent sur le tracker : http(s) seulement, jamais une adresse de téléchargement ni une
    adresse qui porte une clé (clé d'API, passkey...) : elle s'afficherait dans la console et serait copiée par un clic."""
    for value in candidates:
        value = str(value or "").strip()
        parts = urllib.parse.urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or len(value) > 500 or value == download:
            continue
        if any(SECRET_KEYS.search(key) for key, _ in urllib.parse.parse_qsl(parts.query)):
            continue
        if re.search(r"/(dl|download|torrent/download|get)(/|$)", parts.path, re.I) or parts.path.lower().endswith((".torrent", ".magnet")):
            continue
        return value
    return ""


def parse_torznab(text, label):
    """Flux Torznab (RSS) → liste de torrents bruts."""
    if "<!DOCTYPE" in text[:2000].upper() or "<!ENTITY" in text.upper():
        raise ctl.SeedboxError("flux refusé (déclaration XML non admise)")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ctl.SeedboxError(f"flux illisible : {error}") from error
    if root.tag == "error":
        raise ctl.SeedboxError(f"source : {root.get('description') or root.get('code') or 'erreur'}")
    out = []
    for item in root.iter("item"):
        attrs, cats = {}, []
        for node in item:
            if node.tag == TORZNAB_NS + "attr" or node.tag == "attr":
                name, value = node.get("name", ""), node.get("value", "")
                if name == "category":
                    cats.append(value)
                else:
                    attrs.setdefault(name, value)
            elif node.tag == "category" and (node.text or "").strip():
                cats.append(node.text.strip())
        enclosure = item.find("enclosure")
        link = (item.findtext("link") or "").strip() or (enclosure.get("url", "") if enclosure is not None else "")
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        size = to_int(item.findtext("size")) or to_int(attrs.get("size")) or (to_int(enclosure.get("length")) if enclosure is not None else 0)
        seeders = to_int(attrs.get("seeders"))
        peers = to_int(attrs.get("peers"))
        indexer = (item.findtext("jackettindexer") or item.findtext("prowlarrindexer") or "").strip()
        magnet = attrs.get("magneturl") or (link if link.startswith("magnet:") else "")
        guid = (item.findtext("guid") or link or title).strip()
        out.append({"guid": guid, "details": page_url(item.findtext("comments"), guid, download=link), "title": title, "tracker": indexer or label,
                    "link": link if not link.startswith("magnet:") else "",
                    "magnet": magnet, "infohash": (attrs.get("infohash") or "").lower() or None, "size": size, "seeders": seeders,
                    "leechers": to_int(attrs.get("leechers")) if attrs.get("leechers") else max(peers - seeders, 0), "grabs": to_int(attrs.get("grabs")),
                    "published": to_epoch(item.findtext("pubDate")), "cats": cats, "imdb": imdb_id(attrs.get("imdbid") or attrs.get("imdb")),
                    "tmdb": tmdb_id(attrs.get("tmdbid") or attrs.get("tmdb"))})
    return out


def parse_prowlarr(text, label):
    """Réponse JSON de /api/v1/search de Prowlarr → liste de torrents bruts."""
    try:
        rows = json.loads(text)
    except ValueError as error:
        raise ctl.SeedboxError(f"Prowlarr : réponse illisible ({error})") from error
    if isinstance(rows, dict):
        raise ctl.SeedboxError("Prowlarr : " + str(rows.get("message") or rows.get("error") or "réponse inattendue"))
    out = []
    for row in rows or []:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        link, magnet = str(row.get("downloadUrl") or ""), str(row.get("magnetUrl") or "")
        if link.startswith("magnet:"):
            link, magnet = "", magnet or link
        guid = str(row.get("guid") or link or title)
        out.append({"guid": guid, "details": page_url(row.get("infoUrl"), guid, download=link), "title": title, "tracker": str(row.get("indexer") or label),
                    "link": link, "magnet": magnet,
                    "infohash": (str(row.get("infoHash") or "").lower() or None), "size": to_int(row.get("size")), "seeders": to_int(row.get("seeders")),
                    "leechers": to_int(row.get("leechers")), "grabs": to_int(row.get("grabs")), "published": to_epoch(row.get("publishDate")),
                    "cats": [str(c.get("id")) for c in row.get("categories") or [] if isinstance(c, dict) and c.get("id") is not None],
                    "imdb": imdb_id(row.get("imdbId")), "tmdb": tmdb_id(row.get("tmdbId"))})
    return out


def torznab_url(source, cats, limit):
    parts = urllib.parse.urlsplit(source["url"])
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"t": "search", "limit": str(limit)})
    if cats:
        query["cat"] = ",".join(cats)
    if source.get("key") and "apikey" not in query:
        query["apikey"] = source["key"]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))


def fetch(source, cats, limit, timeout=90):
    """Une requête (une série de catégories) à une source ; liste de torrents bruts."""
    if source["type"] == "prowlarr":
        base = ctl.svc(source["container"], source["port"]) + ctl.BASE_PATHS.get("prowlarr", "/prowlarr")
        query = [("query", ""), ("type", "search"), ("limit", limit), ("apikey", source["key"])] + [("categories", c) for c in cats]
        reply = call("GET", base + "/api/v1/search?" + urllib.parse.urlencode(query), {"Accept": "application/json"}, None, timeout)
        if reply.status != 200:
            raise ctl.SeedboxError(f"{source['name']} : HTTP {reply.status}")
        return parse_prowlarr(reply.body.decode("utf-8", "replace"), source["name"])
    if source["type"] == "jackett":
        base = ctl.svc(source["container"], source["port"]) + ctl.BASE_PATHS.get("jackett", "/jackett")
        url = torznab_url({"url": base + "/api/v2.0/indexers/all/results/torznab/api", "key": source["key"]}, cats, limit)
    else:
        url = torznab_url(source, cats, limit)
    reply = call("GET", url, {"Accept": "application/xml, text/xml"}, None, timeout)
    if reply.status != 200:
        raise ctl.SeedboxError(f"{source['name']} : HTTP {reply.status}")
    return parse_torznab(reply.body.decode("utf-8", "replace"), source["name"])


# ---------------------------------------------------------------------------------------------------------------
#  Regroupement par œuvre
# ---------------------------------------------------------------------------------------------------------------
class Index:
    """Œuvres en mémoire pour un passage de collecte : clé exacte, IMDb, TMDB et mots du titre."""

    def __init__(self, con):
        self.works, self.by_key, self.by_imdb, self.by_tmdb, self.tokens = {}, {}, {}, {}, {}
        for row in con.execute("SELECT * FROM works"):
            self.add(dict(row))

    def add(self, work):
        self.works[work["id"]] = work
        self.by_key[work["key"]] = work["id"]
        if work.get("imdb"):
            self.by_imdb[work["imdb"]] = work["id"]
        if work.get("tmdb"):
            self.by_tmdb[work["tmdb"]] = work["id"]
        for token in set(P.match_tokens(work["norm"], work["category"])):
            self.tokens.setdefault(token, set()).add(work["id"])

    def find(self, probe):
        if probe.get("imdb") in self.by_imdb:
            return self.by_imdb[probe["imdb"]]
        if probe.get("tmdb") in self.by_tmdb:
            return self.by_tmdb[probe["tmdb"]]
        if probe["key"] in self.by_key:
            return self.by_key[probe["key"]]
        shared = {}
        for token in set(P.match_tokens(probe["norm"], probe["category"])):
            for ident in self.tokens.get(token, ()):
                shared[ident] = shared.get(ident, 0) + 1
        best, best_score = None, 0.0
        for ident in sorted(shared, key=lambda i: -shared[i])[:60]:
            work = self.works[ident]
            if work.get("imdb") and probe.get("imdb") and work["imdb"] != probe["imdb"]:
                continue
            score = P.similarity({"category": work["category"], "normalized": work["norm"], "year": work["year"], "imdb": work.get("imdb"),
                                  "tmdb": work.get("tmdb"), "magazine": work.get("magazine")},
                                 {"category": probe["category"], "normalized": probe["norm"], "year": probe["year"], "imdb": probe.get("imdb"),
                                  "tmdb": probe.get("tmdb"), "magazine": probe.get("magazine")})
            if score >= P.MATCH_THRESHOLD and score > best_score:
                best, best_score = ident, score
        return best


def classify(item):
    """Catégorie, titre d'œuvre, clé de rapprochement et qualité d'un torrent brut."""
    title = item["title"]
    category = P.resolve_category(item.get("cats") or [], title)
    ident = P.identity(category, [title], title, P.normalize_key(title))
    effective = ident["category"] if ident["category"] in P.CATEGORIES else category
    if effective == "unknown" and ident["category"] in P.CATEGORIES:
        effective = ident["category"]
    magazine = P.magazine_key([title]) if effective in ("presse", "livres") else None
    key = P.group_key(item.get("imdb"), item.get("tmdb"), effective, ident["normalized"], ident["year"], [title])
    parsed = P.parse_name(title)
    bucket = P.quality_bucket(parsed["resolution"] or title, effective)
    return {"category": effective, "title": ident["title"], "norm": ident["normalized"], "year": ident["year"], "key": key, "magazine": magazine,
            "imdb": item.get("imdb"), "tmdb": item.get("tmdb"), "bucket": bucket}


def infohash_of_magnet(magnet):
    found = re.search(r"btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})", magnet or "")
    if not found:
        return None
    value = found.group(1)
    if len(value) == 40:
        return value.lower()
    import base64
    try:
        return base64.b32decode(value.upper()).hex()
    except ValueError:
        return None


def ingest(con, items, source_id, now, index, cutoff):
    """Range les torrents bruts d'une source : renvoie (lus, nouveaux, ignorés trop vieux, œuvres touchées)."""
    seen = new = skipped = 0
    touched = set()
    for item in items:
        seen += 1
        stamp = item.get("published") or now
        if stamp < cutoff:
            skipped += 1
            continue
        ref = hashlib.sha1(f"{source_id}\n{item['guid']}".encode()).hexdigest()[:24]
        found = con.execute("SELECT id, work_id FROM torrents WHERE source = ? AND guid = ?", (source_id, item["guid"])).fetchone()
        if found:
            con.execute("UPDATE torrents SET seeders = ?, leechers = ?, grabs = ?, last_seen = ?, details = CASE WHEN ? != '' THEN ? ELSE details END WHERE id = ?",
                        (item["seeders"], item["leechers"], item["grabs"], now, item.get("details") or "", item.get("details") or "", found["id"]))
            touched.add(found["work_id"])
            continue
        info = classify(item)
        work_id = index.find(info)
        if work_id is None:
            cursor = con.execute("INSERT INTO works(key, category, title, norm, year, imdb, tmdb, magazine, created, latest) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                 (info["key"], info["category"], info["title"], info["norm"], info["year"], info["imdb"], info["tmdb"], info["magazine"], now, stamp))
            work_id = cursor.lastrowid
            index.add({"id": work_id, "key": info["key"], "category": info["category"], "title": info["title"], "norm": info["norm"], "year": info["year"],
                       "imdb": info["imdb"], "tmdb": info["tmdb"], "magazine": info["magazine"]})
        else:
            work = index.works[work_id]
            fill = {}
            if info["imdb"] and not work.get("imdb"):
                fill["imdb"] = info["imdb"]
            if info["tmdb"] and not work.get("tmdb"):
                fill["tmdb"] = info["tmdb"]
            if info["category"] == "series" and work["category"] == "films":
                fill["category"] = "series"
            if fill:
                work.update(fill)
                con.execute("UPDATE works SET " + ", ".join(f"{k} = ?" for k in fill) + " WHERE id = ?", (*fill.values(), work_id))
                index.add(work)
        infohash = item.get("infohash") or infohash_of_magnet(item.get("magnet"))
        con.execute("INSERT INTO torrents(work_id, source, guid, ref, title, norm_title, tracker, category, bucket, qrank, link, magnet, infohash, size, seeders, "
                    "leechers, grabs, published, first_seen, last_seen, details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (work_id, source_id, item["guid"], ref, item["title"], P.normalize_key(item["title"]), item["tracker"], info["category"], info["bucket"],
                     P.QUALITY_ORDER.index(info["bucket"]), item.get("link") or "", item.get("magnet") or "", infohash, item["size"], item["seeders"],
                     item["leechers"], item["grabs"], item.get("published"), now, now, item.get("details") or ""))
        touched.add(work_id)
        new += 1
    return seen, new, skipped, touched


def refresh_works(con, ids):
    for ident in ids:
        con.execute("UPDATE works SET latest = COALESCE((SELECT MAX(COALESCE(published, first_seen)) FROM torrents WHERE work_id = works.id), latest), "
                    "seeders = COALESCE((SELECT MAX(seeders) FROM torrents WHERE work_id = works.id), 0), "
                    "leechers = COALESCE((SELECT MAX(leechers) FROM torrents WHERE work_id = works.id), 0), "
                    "grabs = COALESCE((SELECT MAX(grabs) FROM torrents WHERE work_id = works.id), 0), "
                    "qrank = COALESCE((SELECT MIN(qrank) FROM torrents WHERE work_id = works.id), 9), "
                    "ntorrents = (SELECT COUNT(*) FROM torrents WHERE work_id = works.id) WHERE id = ?", (ident,))


def purge(con, now, retention):
    cutoff = now - retention * DAY
    removed = con.execute("DELETE FROM torrents WHERE COALESCE(published, first_seen) < ?", (cutoff,)).rowcount
    con.execute("DELETE FROM works WHERE id NOT IN (SELECT DISTINCT work_id FROM torrents)")
    con.execute("DELETE FROM sent WHERE at < ?", (now - 90 * DAY,))
    con.execute("DELETE FROM runs WHERE ts < ?", (now - 30 * DAY,))
    con.execute("DELETE FROM metrics WHERE ts < ?", (now - 30 * DAY,))
    return max(removed, 0)


# ---------------------------------------------------------------------------------------------------------------
#  Collecte
# ---------------------------------------------------------------------------------------------------------------
def running():
    try:
        pid = int(LOCK_FILE.read_text())
        if pid == os.getpid():
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def sync(now=None, only=None, progress=say):
    """Une collecte complète : sources → catalogue, purge, relevé. Une seule à la fois."""
    if running():
        raise ctl.SeedboxError("une collecte est déjà en cours")
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    now = int(time.time() if now is None else now)
    cfg = settings()
    try:
        con = connect()
        with con:
            kv_set(con, "last_start", now)
        index = Index(con)
        cutoff = now - cfg["retention"] * DAY
        total_seen = total_new = 0
        chosen = [s for s in sources() if s["enabled"] and (not only or s["id"] == only)]
        if not chosen:
            progress("aucune source active : Prowlarr, Jackett ou une source Torznab à ajouter")
        for source in chosen:
            started = time.time()
            seen = new = 0
            errors = []
            for label, cats in GROUPS:
                try:
                    items = fetch(source, cats, cfg["limit"])
                except (ctl.SeedboxError, OSError, http.client.HTTPException, ValueError) as error:
                    errors.append(f"{label} : {str(error)[:120]}")
                    continue
                with con:
                    got, added, _skipped, touched = ingest(con, items, source["id"], now, index, cutoff)
                    refresh_works(con, touched)
                seen, new = seen + got, new + added
            ok = len(errors) < len(GROUPS)
            with con:
                con.execute("INSERT INTO runs(ts, source, ok, error, collected, new, ms) VALUES(?,?,?,?,?,?,?)",
                            (now, source["id"], int(ok), " ; ".join(errors)[:400] or None, seen, new, int((time.time() - started) * 1000)))
            progress(f"{source['name']} : {seen} résultat(s), {new} nouveau(x)" + (f" ; échecs : {len(errors)}/{len(GROUPS)}" if errors else ""))
            total_seen, total_new = total_seen + seen, total_new + new
        with con:
            purged = purge(con, now, cfg["retention"])
            counts = con.execute("SELECT (SELECT COUNT(*) FROM works) w, (SELECT COUNT(*) FROM torrents) t").fetchone()
            con.execute("INSERT OR REPLACE INTO metrics(ts, works, torrents, collected, new, purged) VALUES(?,?,?,?,?,?)",
                        (now, counts["w"], counts["t"], total_seen, total_new, purged))
            kv_set(con, "last_sync", now)
        progress(f"catalogue : {counts['w']} œuvre(s), {counts['t']} torrent(s) ; {total_new} nouveau(x), {purged} purgé(s)")
        watch(con, now, cfg)
        con.close()
        return {"works": counts["w"], "torrents": counts["t"], "collected": total_seen, "new": total_new, "purged": purged}
    finally:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass


def purge_now(now=None):
    now = int(time.time() if now is None else now)
    con = connect()
    with con:
        removed = purge(con, now, settings()["retention"])
    con.close()
    return removed


def watch(con, now, cfg):
    """Alerte une fois par jour pour une source qui échoue 3 collectes de suite."""
    if not cfg["alert"]:
        return 0
    sent = 0
    for source in sources():
        if not source["enabled"]:
            continue
        rows = con.execute("SELECT ok, error FROM runs WHERE source = ? ORDER BY ts DESC, id DESC LIMIT ?", (source["id"], FAILS_BEFORE_ALERT)).fetchall()
        if len(rows) < FAILS_BEFORE_ALERT or any(r["ok"] for r in rows):
            continue
        key = f"alert:{source['id']}"
        if now - int(kv_get(con, key, 0) or 0) < DAY:
            continue
        ctl.alerts().notify(f"Agrégateur : {source['name']} ne répond plus", f"{FAILS_BEFORE_ALERT} collectes de suite en échec ({rows[0]['error'] or 'erreur'}).",
                            "warning")
        with con:
            kv_set(con, key, now)
        sent += 1
    return sent


def due(now=None):
    cfg = settings()
    if not cfg["enabled"] or not any(s["enabled"] for s in sources()):
        return False
    now = time.time() if now is None else now
    con = connect()
    last = int(kv_get(con, "last_start", 0) or 0)
    con.close()
    return now - last >= cfg["interval"] * 60 - 60


def tick(now=None, launcher=None):
    """Passage de 5 minutes : lance la collecte en tâche de fond quand l'intervalle est écoulé, sans jamais bloquer la surveillance."""
    if running() or not due(now):
        return False
    if launcher:
        launcher()
        return True
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "seedboxctl.py"), "aggregator", "sync"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        ctl.STATE.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(str(process.pid))
    except OSError:
        pass
    return True


def test_source(ident):
    for source in sources():
        if source["id"] == ident:
            if not source["present"]:
                raise ctl.SeedboxError(f"{source['name']} n'est pas installé ou sa clé d'API est absente")
            items = fetch(source, ("2000",), 10, timeout=60)
            trackers = sorted({i["tracker"] for i in items})
            return {"count": len(items), "trackers": trackers[:20], "sample": items[0]["title"] if items else ""}
    raise ctl.SeedboxError(f"source inconnue : {ident}")


# ---------------------------------------------------------------------------------------------------------------
#  Catalogue
# ---------------------------------------------------------------------------------------------------------------
def like(token):
    return "%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def where(period, category, q, tracker, quality, now, skip=()):
    clauses, args = [], []
    tokens = P.normalize_key(q or "").split()
    for token in tokens:
        clauses.append("(w.norm LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM torrents t WHERE t.work_id = w.id AND t.norm_title LIKE ? ESCAPE '\\'))")
        args += [like(token), like(token)]
    if period in ("24h", "week") and not tokens and "period" not in skip:
        clauses.append("w.latest >= ?")
        args.append(now - (DAY if period == "24h" else 7 * DAY))
    if category and "category" not in skip:
        clauses.append("w.category = ?")
        args.append(category)
    if tracker:
        clauses.append("EXISTS (SELECT 1 FROM torrents t WHERE t.work_id = w.id AND t.tracker = ?)")
        args.append(tracker)
    if quality:
        clauses.append("EXISTS (SELECT 1 FROM torrents t WHERE t.work_id = w.id AND t.bucket = ?)")
        args.append(quality)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def variant(row, done):
    text = row["title"]
    return {"id": row["id"], "torrent_title": text, "label": P.episode_label(text), "tracker": row["tracker"], "bucket": row["bucket"], "size": row["size"],
            "seeders": row["seeders"], "leechers": row["leechers"], "grabs": row["grabs"], "published": row["published"] or row["first_seen"],
            "sent": row["ref"] in done, "magnet": bool(row["magnet"]), "link": bool(row["link"]), "details": row["details"] or ""}


def catalogue(period="24h", category="", q="", tracker="", quality="", sort="publishedAt", direction="desc", page=1, per_page=20, now=None):
    now = int(time.time() if now is None else now)
    period = period if period in PERIODS else "24h"
    sort = sort if sort in SORTS else "publishedAt"
    direction = "ASC" if str(direction).lower() == "asc" else "DESC"
    per_page = int(per_page) if str(per_page).isdigit() and int(per_page) in PAGE_SIZES else 20
    category = category if category in (*P.CATEGORIES, "unknown") else ""
    con = connect()
    try:
        clause, args = where(period, category, q, tracker, quality, now)
        total = con.execute("SELECT COUNT(*) FROM works w" + clause, args).fetchone()[0]
        pages = 1 if per_page == 0 else max(1, -(-total // per_page))
        page = max(1, min(int(page or 1), pages))
        order = SORTS[sort] + " " + direction + (", w.qrank ASC" if sort != "quality" else "") + ", w.id DESC"
        sql = "SELECT w.* FROM works w" + clause + " ORDER BY " + order
        params = list(args)
        if per_page:
            sql += " LIMIT ? OFFSET ?"
            params += [per_page, (page - 1) * per_page]
        works = con.execute(sql, params).fetchall()
        done = {r["ref"] for r in con.execute("SELECT ref FROM sent")}
        by_work = {}
        if works:
            marks = ",".join("?" for _ in works)
            for row in con.execute(f"SELECT * FROM torrents WHERE work_id IN ({marks}) ORDER BY seeders DESC, id", [w["id"] for w in works]):
                by_work.setdefault(row["work_id"], []).append(variant(row, done))
        rows = []
        for work in works:
            variants = by_work.get(work["id"], [])
            seasons = P.season_groups(work["category"], variants)
            rows.append({"id": work["id"], "title": work["title"], "category": work["category"], "label": P.CATEGORY_LABELS.get(work["category"], "Autres"),
                         "year": work["year"], "imdb": work["imdb"], "tmdb": work["tmdb"], "latest": work["latest"], "seeders": work["seeders"],
                         "leechers": work["leechers"], "grabs": work["grabs"], "count": work["ntorrents"],
                         "trackers": sorted({v["tracker"] for v in variants}, key=str.lower), "qualities": [b for b in P.QUALITY_ORDER if any(v["bucket"] == b for v in variants)],
                         "seasons": seasons, "groups": None if seasons else P.quality_groups(variants)})
        cat_clause, cat_args = where(period, "", q, tracker, quality, now, skip=("category",))
        counts = {r["category"]: r["n"] for r in con.execute("SELECT w.category, COUNT(*) n FROM works w" + cat_clause + " GROUP BY w.category", cat_args)}
        per = {}
        for name in PERIODS:
            c, a = where(name, category, q, tracker, quality, now)
            per[name] = con.execute("SELECT COUNT(*) FROM works w" + c, a).fetchone()[0]
        trackers = [r[0] for r in con.execute("SELECT DISTINCT tracker FROM torrents ORDER BY tracker COLLATE NOCASE")]
        return {"works": rows, "total": total, "page": page, "pages": pages, "per_page": per_page, "period": period, "category": category, "q": q, "sort": sort,
                "direction": direction.lower(), "counts": counts, "periods": per, "trackers": trackers, "qualities": list(P.QUALITY_ORDER),
                "categories": [{"id": c, "label": P.CATEGORY_LABELS[c]} for c in P.CATEGORIES]}
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------------------------
#  Suggestions pour le ratio
# ---------------------------------------------------------------------------------------------------------------
PICK_HOURS = 48
PICK_MAX_SIZE = 25 << 30


def picks(count=10, now=None, hours=PICK_HOURS, max_size=PICK_MAX_SIZE):
    """Les torrents qui ont le plus de chances de faire monter le ratio : récents, beaucoup de téléchargeurs par rapport aux
    seeders, de taille raisonnable, pas déjà envoyés. Aucune garantie (personne ne peut prédire une demande), mais c'est
    exactement ce qui fait envoyer le plus : arriver tôt sur un torrent que beaucoup de gens veulent."""
    now = int(time.time() if now is None else now)
    con = connect()
    try:
        done = {r["ref"] for r in con.execute("SELECT ref FROM sent")}
        rows = con.execute("SELECT t.*, w.title AS work_title FROM torrents t JOIN works w ON w.id = t.work_id "
                           "WHERE t.leechers >= 2 AND t.size > 0 AND t.size <= ? AND COALESCE(t.published, t.first_seen) >= ?",
                           (max_size, now - hours * 3600)).fetchall()
    finally:
        con.close()
    best = {}
    for r in rows:
        if r["ref"] in done:
            continue
        age = max((now - (r["published"] or r["first_seen"])) / 3600, 0.5)
        demand = r["leechers"] / (r["seeders"] + 1)
        score = demand * min(r["leechers"], 30) / (1 + age / 12)           # demande relative, volume de téléchargeurs, fraîcheur
        if r["work_id"] not in best or score > best[r["work_id"]][0]:
            best[r["work_id"]] = (score, r, age, demand)
    ranked = sorted(best.values(), key=lambda x: -x[0])[:max(1, min(int(count), 30))]
    return [{"id": r["id"], "title": r["work_title"], "torrent_title": r["title"], "tracker": r["tracker"], "size": r["size"], "seeders": r["seeders"],
             "leechers": r["leechers"], "age_hours": round(age, 1), "score": round(score, 1),
             "why": f"{r['leechers']} téléchargeur(s) pour {r['seeders']} seeder(s), publié il y a {age:.0f} h"} for score, r, age, demand in ranked]


# ---------------------------------------------------------------------------------------------------------------
#  Envoi vers qBittorrent
# ---------------------------------------------------------------------------------------------------------------
def _bskip(data, i):
    char = data[i:i + 1]
    if char == b"i":
        return data.index(b"e", i) + 1
    if char in (b"l", b"d"):
        i += 1
        while data[i:i + 1] != b"e":
            i = _bskip(data, i)
        return i + 1
    if char.isdigit():
        colon = data.index(b":", i)
        return colon + 1 + int(data[i:colon])
    raise ValueError("bencode")


def torrent_infohash(data):
    """Empreinte (SHA-1 du dictionnaire « info ») d'un fichier .torrent ; ValueError si ce n'en est pas un."""
    try:
        if data[:1] != b"d":
            raise ValueError("pas un torrent")
        i = 1
        while data[i:i + 1] != b"e":
            colon = data.index(b":", i)
            size = int(data[i:colon])
            key = data[colon + 1:colon + 1 + size]
            i = colon + 1 + size
            end = _bskip(data, i)
            if key == b"info":
                return hashlib.sha1(data[i:end]).hexdigest()
            i = end
    except (IndexError, ValueError) as error:
        raise ValueError("fichier .torrent illisible") from error
    raise ValueError("fichier .torrent sans section info")


def multipart(fields, files=None):
    boundary = "----sb" + hashlib.sha1(os.urandom(16)).hexdigest()[:20]
    body = b""
    for name, value in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
    for name, (filename, content) in (files or {}).items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: application/x-bittorrent\r\n\r\n').encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def qbit_session():
    env = ctl.env_read()
    base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"
    body = urllib.parse.urlencode({"username": env.get("QBIT_USERNAME", ""), "password": env.get("QBIT_PASSWORD", "")}).encode()
    reply = call("POST", base + "/auth/login", {"Content-Type": "application/x-www-form-urlencoded"}, body, 20)
    if reply.status == 200 and reply.body.strip() == b"Ok.":
        return base, {"Cookie": reply.cookie}
    if reply.status == 204 or (reply.status == 200 and not reply.body.strip()):
        # Réponse vide : qBittorrent n'exige pas de session pour cette adresse (réseau de confiance) ; on le vérifie avant de continuer
        probe = call("GET", base + "/app/version", {}, None, 20)
        if probe.status == 200:
            return base, {}
        raise ctl.SeedboxError(f"qBittorrent : connexion sans session refusée (login HTTP {reply.status}, puis HTTP {probe.status})")
    if reply.status == 200:
        raise ctl.SeedboxError("qBittorrent : identifiant ou mot de passe refusé (comparez QBIT_USERNAME et QBIT_PASSWORD avec l'interface de qBittorrent)")
    if reply.status == 403:
        raise ctl.SeedboxError("qBittorrent : adresse temporairement bannie après trop d'échecs de connexion (HTTP 403) ; attendez quelques minutes")
    raise ctl.SeedboxError(f"qBittorrent : connexion refusée (HTTP {reply.status})")


def send(torrent_id, force=False, now=None):
    """Ajoute un torrent du catalogue à qBittorrent ; ne l'ajoute jamais deux fois."""
    now = int(time.time() if now is None else now)
    con = connect()
    try:
        row = con.execute("SELECT * FROM torrents WHERE id = ?", (int(torrent_id),)).fetchone()
        if not row:
            raise ctl.SeedboxError("torrent introuvable (purgé depuis ?)")
        if not force and con.execute("SELECT 1 FROM sent WHERE ref = ?", (row["ref"],)).fetchone():
            return {"state": "deja", "message": "déjà envoyé à qBittorrent", "title": row["title"]}
        if not ctl.container_running("qbittorrent"):
            raise ctl.SeedboxError("qBittorrent n'est pas démarré")
        infohash, payload, magnet = row["infohash"], None, row["magnet"] or ""
        if row["link"]:
            try:
                reply, final = _fetch_follow(row["link"], None, 60)
                if final.startswith("magnet:"):
                    magnet = final
                elif reply.status == 200 and reply.body[:1] == b"d":
                    infohash, payload = torrent_infohash(reply.body), reply.body
                elif not magnet:
                    raise ctl.SeedboxError(f"le tracker n'a pas fourni de .torrent (HTTP {reply.status})")
            except (OSError, http.client.HTTPException, ValueError) as error:
                if not magnet:
                    raise ctl.SeedboxError(f"téléchargement du .torrent impossible : {str(error)[:120]}") from error
        if payload is None and not magnet:
            raise ctl.SeedboxError("ni lien de téléchargement ni lien magnet pour ce torrent")
        infohash = infohash or infohash_of_magnet(magnet)
        base, auth = qbit_session()
        if infohash:
            present = call("GET", base + "/torrents/info?hashes=" + infohash, auth, None, 20)
            if present.status == 200 and json.loads(present.body or b"[]"):
                with con:
                    con.execute("INSERT OR REPLACE INTO sent(ref, infohash, title, at, state) VALUES(?,?,?,?,?)", (row["ref"], infohash, row["title"], now, "present"))
                return {"state": "present", "message": "déjà présent dans qBittorrent", "title": row["title"]}
            twin = con.execute("SELECT 1 FROM sent WHERE infohash = ?", (infohash,)).fetchone()
            if twin and not force:
                with con:
                    con.execute("INSERT OR REPLACE INTO sent(ref, infohash, title, at, state) VALUES(?,?,?,?,?)", (row["ref"], infohash, row["title"], now, "deja"))
                return {"state": "deja", "message": "ce torrent a déjà été envoyé (même empreinte)", "title": row["title"]}
        category = settings()["category"]
        if category:
            call("POST", base + "/torrents/createCategory", {**auth, "Content-Type": "application/x-www-form-urlencoded"},
                 urllib.parse.urlencode({"category": category, "savePath": ""}).encode(), 20)
        fields = {"category": category} if category else {}
        if payload is not None:
            body, kind = multipart(fields, {"torrents": ("catalogue.torrent", payload)})
        else:
            body, kind = multipart({**fields, "urls": magnet})
        answer = call("POST", base + "/torrents/add", {**auth, "Content-Type": kind}, body, 60)
        if answer.status != 200 or b"Fails" in answer.body:
            raise ctl.SeedboxError(f"qBittorrent a refusé le torrent (HTTP {answer.status})")
        with con:
            con.execute("INSERT OR REPLACE INTO sent(ref, infohash, title, at, state) VALUES(?,?,?,?,?)", (row["ref"], infohash, row["title"], now, "envoye"))
        return {"state": "envoye", "message": "envoyé à qBittorrent", "title": row["title"], "infohash": infohash}
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------------------------
#  Tableau de bord
# ---------------------------------------------------------------------------------------------------------------
def dashboard(now=None):
    now = int(time.time() if now is None else now)
    cfg = settings()
    con = connect()
    try:
        last_sync = int(kv_get(con, "last_sync", 0) or 0)
        last_start = int(kv_get(con, "last_start", 0) or 0)
        totals = con.execute("SELECT (SELECT COUNT(*) FROM works) works, (SELECT COUNT(*) FROM torrents) torrents, (SELECT COUNT(*) FROM sent) sent, "
                             "(SELECT COUNT(*) FROM works WHERE latest >= ?) day, (SELECT COUNT(*) FROM works WHERE latest >= ?) week",
                             (now - DAY, now - 7 * DAY)).fetchone()
        rows = []
        for source in sources():
            last = con.execute("SELECT * FROM runs WHERE source = ? ORDER BY ts DESC, id DESC LIMIT 1", (source["id"],)).fetchone()
            fails = 0
            for run in con.execute("SELECT ok FROM runs WHERE source = ? ORDER BY ts DESC, id DESC LIMIT 20", (source["id"],)):
                if run["ok"]:
                    break
                fails += 1
            shown = con.execute("SELECT COUNT(*) FROM torrents WHERE source = ?", (source["id"],)).fetchone()[0]
            if not source["present"]:
                state = "absent"
            elif not source["enabled"]:
                state = "off"
            elif not last:
                state = "wait"
            else:
                state = "ok" if last["ok"] and not last["error"] else "partial" if last["ok"] else "error"
            rows.append({"id": source["id"], "name": source["name"], "auto": source["auto"], "enabled": source["enabled"], "present": source["present"],
                         "state": state, "last": last["ts"] if last else None, "error": last["error"] if last else None, "collected": last["collected"] if last else 0,
                         "new": last["new"] if last else 0, "ms": last["ms"] if last else 0, "fails": fails, "torrents": shown,
                         "url": "" if source["auto"] else re.sub(r"(apikey=)[^&]+", r"\1•••", source["url"]), "has_key": bool(source.get("key"))})
        timeline = []
        for back in range(6, -1, -1):
            start = (int(now // DAY) - back) * DAY
            agg = con.execute("SELECT COALESCE(SUM(collected), 0) c, COALESCE(SUM(new), 0) n, COALESCE(SUM(purged), 0) p FROM metrics WHERE ts >= ? AND ts < ?",
                              (start, start + DAY)).fetchone()
            timeline.append({"day": time.strftime("%Y-%m-%d", time.gmtime(start)), "indexed": agg["c"], "added": agg["n"], "purged": agg["p"]})
        per_category = {r["category"]: r["n"] for r in con.execute("SELECT category, COUNT(*) n FROM works GROUP BY category")}
        problems = [f"{r['name']} : {r['error'] or 'aucune réponse'}" for r in rows if r["state"] == "error"]
        return {"enabled": cfg["enabled"], "interval": cfg["interval"], "retention": cfg["retention"], "limit": cfg["limit"], "category": cfg["category"],
                "alert": cfg["alert"], "intervals": list(INTERVALS), "running": running(), "last_sync": last_sync or None,
                "next_sync": (last_start + cfg["interval"] * 60) if cfg["enabled"] and last_start else None,
                "totals": {"works": totals["works"], "torrents": totals["torrents"], "sent": totals["sent"], "day": totals["day"], "week": totals["week"]},
                "sources": rows, "timeline": timeline, "per_category": per_category, "problems": problems,
                "qbittorrent": ctl.container_running("qbittorrent")}
    finally:
        con.close()
