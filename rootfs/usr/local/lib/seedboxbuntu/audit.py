"""SeedBoxBuntu - journal d'audit et recherche dans les journaux.

Journal d'audit : qui a fait quoi, quand, depuis quelle adresse. Il rassemble
  - les actions faites dans la console web (chaque requête qui modifie quelque chose, avec l'utilisateur
    indiqué par Authelia, l'adresse du poste et le résultat ; jamais un mot de passe, une clé ou un jeton)
  - les connexions à la connexion unique (Authelia : réussies, refusées, double authentification)
  - les connexions SSH (acceptées, refusées)
  - les comptes créés par invitation
Les événements sont enregistrés dans /var/lib/seedboxbuntu/audit.db (SQLite), conservés 400 jours. Chacun
porte une empreinte chaînée avec la précédente : modifier ou supprimer un événement après coup se voit
(« seedboxctl audit verify »). Cette chaîne détecte une altération maladroite ; contre un administrateur
système qui sait la refaire, l'exportation régulière vers un autre support reste la vraie protection.

Recherche dans les journaux : une seule recherche sur tous les conteneurs et services de l'hôte, classée par
date, avec masquage des mots de passe, clés d'API et jetons repérables. Exportable en texte.

    seedboxctl audit show | sync | verify | export [--format csv|json] | prune
"""
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import seedboxctl as ctl

DB_FILE = ctl.STATE / "audit.db"
CURSOR_FILE = ctl.STATE / "audit-cursor.json"
KEEP_DAYS = 400
SOURCES = ("console", "tâche", "authelia", "ssh", "invitation", "système")
SAFE_KEYS = {"login", "role", "service", "id", "name", "mode", "label", "what", "kind", "value", "keyword", "ref", "channel", "days",
             "uses", "share", "action", "source", "type", "provider", "enabled", "guard", "require_signed", "repo", "domain", "email",
             "hostname", "to", "local", "force", "purge", "preset", "pack", "range", "ids", "ports", "scope"}
REDACT = [
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s\"']+"), r"\1***"),
    (re.compile(r"(?i)((?:api[_-]?key|apikey|x-api-key|token|access_token|secret|password|passwd|pwd|passphrase|auth)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+"), r"\1***"),
    (re.compile(r"(?i)([?&](?:api[_-]?key|apikey|token|password|key|secret)=)[^&\s\"']+"), r"\1***"),
    (re.compile(r"\b(ghp_|gho_|ghs_|github_pat_|tskey-[a-z]+-)[A-Za-z0-9_-]{8,}"), r"\1***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "***clé privée masquée***"),
]
CONTEXT = threading.local()
LOCK = threading.Lock()


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    fresh = not DB_FILE.exists()
    db = sqlite3.connect(DB_FILE, timeout=15)
    if fresh:
        os.chmod(DB_FILE, 0o600)
    db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, source TEXT NOT NULL, "
               "user TEXT NOT NULL, ip TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, outcome TEXT NOT NULL, "
               "uid TEXT UNIQUE, chain TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return db


def redact(text):
    for pattern, replacement in REDACT:
        text = pattern.sub(replacement, text)
    return text


def clip(value, limit):
    return str(value or "").replace("\n", " ").replace("\r", " ")[:limit]


def link(previous, fields):
    return hashlib.sha256((previous + json.dumps(fields, ensure_ascii=False, separators=(",", ":"))).encode()).hexdigest()


def record(user, ip, action, detail="", source="console", outcome="ok", ts=None, uid=None):
    """Ajoute un événement ; renvoie son numéro, ou None si ce uid est déjà connu."""
    ts = int(ts if ts is not None else time.time())
    fields = [ts, source, clip(user, 64), clip(ip, 64), clip(redact(action), 200), clip(redact(detail), 400), outcome]
    with LOCK:
        db = connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                if uid and db.execute("SELECT 1 FROM events WHERE uid = ?", (uid,)).fetchone():
                    return None
                row = db.execute("SELECT value FROM meta WHERE key = 'head'").fetchone()
                chain = link(row[0] if row else "", fields)
                cursor = db.execute("INSERT INTO events (ts, source, user, ip, action, detail, outcome, uid, chain) VALUES (?,?,?,?,?,?,?,?,?)",
                                    (*fields, uid, chain))
                db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('head', ?)", (chain,))
                return cursor.lastrowid
        finally:
            db.close()


def safe_detail(data):
    """Résumé d'une requête sans secret : seules les clés connues et sûres, valeurs courtes."""
    parts = []
    for key, value in (data or {}).items():
        if key not in SAFE_KEYS:
            continue
        if isinstance(value, list):
            parts.append(f"{key}={len(value)} élément(s)")
        elif isinstance(value, (str, int, float, bool)) and len(str(value)) <= 80:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def request_event(user, ip, path, data, status, job_title=""):
    """Événement d'une action de la console. Les tâches portent leur titre ; sinon le chemin de l'API."""
    outcome = "ok" if status and status < 400 else "refusé" if status in (400, 403, 429) else "erreur"
    action = job_title or "POST " + path
    return record(user or "inconnu", ip, action, safe_detail(data), "console", outcome)


# --- lecture -----------------------------------------------------------------------------------
def search(q="", user="", source="", outcome="", since=0, until=0, limit=200, offset=0):
    where, args = [], []
    if q:
        where.append("(action LIKE ? OR detail LIKE ? OR user LIKE ? OR ip LIKE ?)")
        args += [f"%{q}%"] * 4
    for column, value in (("user", user), ("source", source), ("outcome", outcome)):
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    if since:
        where.append("ts >= ?")
        args.append(int(since))
    if until:
        where.append("ts <= ?")
        args.append(int(until))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    db = connect()
    try:
        total = db.execute("SELECT COUNT(*) FROM events" + clause, args).fetchone()[0]
        rows = db.execute("SELECT id, ts, source, user, ip, action, detail, outcome FROM events" + clause +
                          " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?", args + [max(1, min(int(limit), 1000)), max(0, int(offset))]).fetchall()
        users = [r[0] for r in db.execute("SELECT DISTINCT user FROM events ORDER BY user")]
    finally:
        db.close()
    keys = ("id", "ts", "source", "user", "ip", "action", "detail", "outcome")
    return {"total": total, "events": [dict(zip(keys, r)) for r in rows], "users": users, "sources": list(SOURCES)}


def export(fmt="csv", **filters):
    filters.setdefault("limit", 1000)
    rows = search(**filters)["events"]
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=1), "application/json"
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "date", "source", "utilisateur", "adresse", "action", "détail", "résultat"])
    for r in rows:
        writer.writerow([r["id"], datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), r["source"], r["user"],
                         r["ip"], r["action"], r["detail"], r["outcome"]])
    return out.getvalue(), "text/csv; charset=utf-8"


def verify():
    """Recontrôle la chaîne d'empreintes. Renvoie {ok, events, first_bad}."""
    db = connect()
    try:
        row = db.execute("SELECT value FROM meta WHERE key = 'anchor'").fetchone()
        previous = row[0] if row else ""
        count, bad = 0, None
        for id_, ts, source, user, ip, action, detail, outcome, chain in db.execute(
                "SELECT id, ts, source, user, ip, action, detail, outcome, chain FROM events ORDER BY id"):
            count += 1
            expected = link(previous, [ts, source, user, ip, action, detail, outcome])
            if expected != chain and bad is None:
                bad = id_
            previous = chain
        head = db.execute("SELECT value FROM meta WHERE key = 'head'").fetchone()
        if bad is None and ((head and head[0] != previous) or (not head and count)):
            bad = -1                                          # la fin du journal a été retirée
    finally:
        db.close()
    return {"ok": bad is None, "events": count, "first_bad": bad}


def prune(days=KEEP_DAYS):
    limit = int(time.time()) - days * 86400
    db = connect()
    try:
        with db:
            last = db.execute("SELECT chain FROM events WHERE ts < ? ORDER BY id DESC LIMIT 1", (limit,)).fetchone()
            if not last:
                return 0
            # Les événements sont rangés par numéro : on coupe au dernier événement ancien d'une série continue
            first_recent = db.execute("SELECT MIN(id) FROM events WHERE ts >= ?", (limit,)).fetchone()[0]
            cutoff = (first_recent - 1) if first_recent else db.execute("SELECT MAX(id) FROM events").fetchone()[0]
            anchor = db.execute("SELECT chain FROM events WHERE id = ?", (cutoff,)).fetchone()
            if not anchor:
                return 0
            removed = db.execute("DELETE FROM events WHERE id <= ?", (cutoff,)).rowcount
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('anchor', ?)", (anchor[0],))
    finally:
        db.close()
    return removed


# --- collecte : Authelia et SSH ------------------------------------------------------------------
AUTHELIA_LINE = re.compile(r'time="?(?P<time>[^" ]+)"? level=(?P<level>\w+) msg="(?P<msg>(?:[^"\\]|\\.)*)"(?P<rest>.*)')
SSH_LINE = re.compile(r"^(?P<time>\S+) \S+ sshd(?:-session)?\[\d+\]: (?P<msg>.*)$")


def parse_iso(text):
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            value = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    return int((value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp())


def authelia_event(line):
    """(ts, utilisateur, adresse, action, résultat) d'une ligne de journal Authelia, ou None."""
    found = AUTHELIA_LINE.search(line)
    if not found:
        return None
    msg = found.group("msg").replace('\\"', '"')
    if "authentication attempt" not in msg:
        return None
    ts = parse_iso(found.group("time"))
    user = re.search(r"user '([^']*)'", msg)
    ip = re.search(r"remote_ip=\"?([0-9a-fA-F:.]+)\"?", found.group("rest"))
    kind = "mot de passe" if "1FA" in msg else "double authentification" if ("2FA" in msg or "TOTP" in msg or "WebAuthn" in msg or "Duo" in msg) else "connexion"
    success = msg.startswith("Successful")
    return (ts, user.group(1) if user else "inconnu", ip.group(1) if ip else "", f"connexion ({kind})", "ok" if success else "refusé")


def ssh_event(line):
    found = SSH_LINE.match(line.strip())
    if not found:
        return None
    msg = found.group("msg")
    ts = parse_iso(found.group("time"))
    accepted = re.match(r"Accepted (\S+) for (\S+) from (\S+)", msg)
    if accepted:
        return (ts, accepted.group(2), accepted.group(3), f"connexion SSH ({accepted.group(1)})", "ok")
    failed = re.match(r"Failed (\S+) for (?:invalid user )?(\S+) from (\S+)", msg)
    if failed:
        return (ts, failed.group(2), failed.group(3), f"connexion SSH ({failed.group(1)})", "refusé")
    invalid = re.match(r"Invalid user (\S*) from (\S+)", msg)
    if invalid:
        return (ts, invalid.group(1) or "inconnu", invalid.group(2), "connexion SSH (compte inexistant)", "refusé")
    return None


def command(*argv, timeout=40):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def sync():
    """Ingère les connexions Authelia et SSH depuis la dernière passe. Renvoie le nombre d'événements ajoutés."""
    try:
        cursor = json.loads(CURSOR_FILE.read_text())
    except (OSError, ValueError):
        cursor = {}
    now = int(time.time())
    added = 0
    for source, since in (("authelia", cursor.get("authelia", now - 86400)), ("ssh", cursor.get("ssh", now - 86400))):
        window = max(60, now - int(since) + 120)              # léger recouvrement : le uid écarte les doublons
        if source == "authelia":
            code, text = command("docker", "logs", "--since", f"{window}s", "authelia")
            parse = authelia_event
        else:
            code, text = command("journalctl", "-u", "ssh", "-u", "sshd", "--since", f"@{now - window}", "--no-pager", "-o", "short-iso")
            parse = ssh_event
        if code != 0 and not text:
            continue
        for line in text.splitlines():
            event = parse(line)
            if not event or event[0] is None:
                continue
            ts, user, ip, action, outcome = event
            if record(user, ip, action, "", source, outcome, ts, uid=hashlib.sha1(f"{source}:{line}".encode()).hexdigest()):
                added += 1
        cursor[source] = now
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor))
    if added:
        say(f"journal d'audit : {added} événement(s) ajouté(s)")
    return added


def maintain():
    """Appelée toutes les heures : collecte, puis suppression de ce qui dépasse la durée de conservation."""
    added = sync()
    prune()
    return added


# --- recherche dans les journaux -------------------------------------------------------------------
SINCE = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 86400, "7j": 7 * 86400}
PER_SOURCE = 3000
MAX_RESULTS = 1000


def line_time(line):
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?", line)
    if not match:
        return None
    stamp = match.group(1) + (match.group(3) or "Z")
    stamp = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", stamp)
    return parse_iso(stamp)


def read_source(source, seconds):
    kind, _, name = source.partition(":")
    if kind == "c":
        code, text = command("docker", "logs", "--since", f"{seconds}s", "--timestamps", name)
    else:
        code, text = command("journalctl", "-u", name, "--since", f"@{int(time.time()) - seconds}", "--no-pager", "-o", "short-iso")
    lines = text.splitlines()[-PER_SOURCE:]
    result, last = [], int(time.time()) - seconds
    for line in lines:
        ts = line_time(line)
        last = ts if ts is not None else last
        body = re.sub(r"^\S+\s+", "", line, count=1) if ts is not None and kind == "c" else line
        result.append({"ts": last, "source": source, "line": redact(body)[:1500]})
    return result


def search_logs(query="", since="1h", sources=None, regex=False, limit=500):
    """Recherche dans les journaux de plusieurs conteneurs et services. Renvoie les lignes les plus récentes d'abord."""
    seconds = SINCE.get(since)
    if not seconds:
        raise ctl.SeedboxError("période inconnue : " + ", ".join(SINCE))
    known = ctl.sysinfo().log_sources()
    allowed = {f"c:{n}" for n in known["containers"]} | {f"h:{n}" for n in known["host"]}
    chosen = [s for s in (sources or sorted(allowed)) if s in allowed]
    if sources and not chosen:
        raise ctl.SeedboxError("aucune source valide")
    if regex:
        if len(query) > 200:
            raise ctl.SeedboxError("expression trop longue")
        try:
            pattern = re.compile(query, re.I)
        except re.error as error:
            raise ctl.SeedboxError(f"expression invalide : {error}")
        match = pattern.search
    else:
        needle = query.lower()
        match = lambda text: needle in text.lower()  # noqa: E731
    with ThreadPoolExecutor(max_workers=8) as pool:
        chunks = list(pool.map(lambda s: read_source(s, seconds), chosen))
    lines = [item for chunk in chunks for item in chunk if not query or match(item["line"])]
    lines.sort(key=lambda item: item["ts"], reverse=True)
    limit = max(1, min(int(limit), MAX_RESULTS))
    return {"query": query, "since": since, "sources": len(chosen), "total": len(lines), "lines": lines[:limit], "truncated": len(lines) > limit}


def export_logs(**kwargs):
    kwargs["limit"] = MAX_RESULTS
    result = search_logs(**kwargs)
    text = "".join(f"{datetime.fromtimestamp(i['ts'], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z [{i['source']}] {i['line']}\n"
                   for i in reversed(result["lines"]))
    return text


def overview():
    status = verify()
    return {"chain": status, "keep_days": KEEP_DAYS, "sources": list(SOURCES), "log_ranges": list(SINCE)}
