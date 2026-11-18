"""SeedBoxBuntu - seed depuis le NAS : plus de copie locale d'un média déjà rangé sur un stockage externe.

Quand le téléchargement terminé reste sur le disque du serveur alors que Radarr/Sonarr rangent le média sur un NAS, le même fichier
existe deux fois et un lien dur est impossible (deux systèmes de fichiers). Ce module fait pointer qBittorrent sur la copie du NAS,
puis supprime la copie locale : le torrent continue de partager, depuis le NAS, et l'espace du serveur est libéré.
Garanties, torrent par torrent :
  - tous les fichiers « lourds » du torrent ont une copie identique octet par octet dans une médiathèque située sur un partage ;
  - la copie locale n'est jamais supprimée avant que qBittorrent ait revérifié le torrent à 100 % depuis le NAS ;
  - au moindre doute (vérification incomplète, erreur, délai dépassé), tout est remis comme avant : fichiers locaux, emplacement, noms.
Les petits fichiers annexes (nfo, sous-titres...) restent où ils sont et sont simplement désélectionnés du torrent.
Limite assumée : le torrent dépend ensuite du fichier du NAS ; si Radarr/Sonarr le renomment ou le remplacent, il passera en erreur
(la console le signale dans le diagnostic du partage).
    seedboxctl repoint show | run
"""
import json
import os
import time
from pathlib import Path

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "repoint.json"
STATE_FILE = ctl.STATE / "repoint.json"
MIN_SIZE = 50 << 20
ASIDE = ".sbrepoint"
RECHECK_TIMEOUT = 3600
DEFAULTS = {"enabled": True}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def set_enabled(enabled):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"enabled": bool(enabled)}))
    return settings()


# --- qBittorrent ------------------------------------------------------------------------------------
class Qbit:
    def __init__(self):
        self.cookie = ctl.qbit_login()
        self.base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"

    def get(self, path):
        return ctl.request("GET", self.base + path, headers={"Cookie": self.cookie}, timeout=60).json()

    def post(self, path, form):
        answer = ctl.request("POST", self.base + path, form=form, headers={"Cookie": self.cookie}, timeout=60)
        if not answer.ok:
            raise ctl.SeedboxError(f"qBittorrent {path} : HTTP {answer.status}")
        return answer

    def stop(self, ident):
        try:
            self.post("/torrents/stop", {"hashes": ident})
        except ctl.SeedboxError:
            self.post("/torrents/pause", {"hashes": ident})        # qBittorrent avant 5.0

    def start(self, ident):
        try:
            self.post("/torrents/start", {"hashes": ident})
        except ctl.SeedboxError:
            self.post("/torrents/resume", {"hashes": ident})


def connect():
    return Qbit() if ctl.container_running("qbittorrent") else None


# --- recherche des copies ---------------------------------------------------------------------------
def _external(container_path):
    return str(container_path).startswith("/shares/")


def _host(container_path):
    return ctl.libscan().to_host(container_path)


def _media_by_size(records):
    index = {}
    for r in records:
        if r["size"] >= MIN_SIZE and _external(r["path"]):
            index.setdefault(r["size"], []).append(r["path"])
    return index


def match_file(local_host, size, index, same):
    """Chemin conteneur du fichier identique rangé sur un partage, ou None."""
    audit = ctl.copyaudit()
    for candidate in index.get(size, []):
        host = _host(candidate)
        if not host or not os.path.isfile(host):
            continue
        if audit._sample(local_host, size) != audit._sample(host, size):
            continue
        if same(local_host, host):
            return candidate
    return None


def plan_for(torrent, files, index, same=None):
    """Plan de repointage d'un torrent terminé, ou {"skip": motif}."""
    same = same or ctl.copyaudit().identical
    save = str(torrent.get("save_path", "")).rstrip("/")
    if _external(save):
        return {"skip": "déjà sur un partage"}
    if torrent.get("progress", 0) < 1:
        return {"skip": "pas terminé"}
    moves, extras = [], []
    for f in files:
        name = f["name"]
        local = _host(f"{save}/{name}")
        if f.get("priority", 1) == 0:
            continue
        if f["size"] < MIN_SIZE:
            extras.append(name)
            continue
        if not local or not os.path.isfile(local) or os.path.islink(local) or os.stat(local).st_nlink > 1:
            return {"skip": f"fichier local introuvable ou déjà lié : {name}"}
        target = match_file(local, f["size"], index, same)
        if not target:
            return {"skip": f"pas de copie identique sur un partage : {name}"}
        moves.append({"name": name, "local": str(local), "target": target})
    if not moves:
        return {"skip": "aucun gros fichier"}
    parent = os.path.commonpath([os.path.dirname(m["target"]) for m in moves])
    for m in moves:
        m["new"] = os.path.relpath(m["target"], parent)
    if len({m["new"] for m in moves}) != len(moves):
        return {"skip": "noms de destination ambigus"}
    return {"hash": torrent["hash"], "name": torrent.get("name", ""), "save_old": save, "save_new": parent, "moves": moves,
            "extras": extras, "size": sum(f["size"] for f in files if f["name"] in {m["name"] for m in moves})}


def candidates(qbit=None, records=None):
    qbit = qbit or connect()
    if qbit is None:
        return {"plans": [], "skipped": [], "note": "qBittorrent est arrêté"}
    records = ctl.libscan().records() if records is None else records
    index = _media_by_size(records)
    plans, skipped = [], []
    if not index:
        return {"plans": [], "skipped": [], "note": "aucune médiathèque sur un partage dans le dernier relevé"}
    for t in qbit.get("/torrents/info") or []:
        if t.get("progress", 0) < 1 or _external(str(t.get("save_path", ""))):
            continue
        plan = plan_for(t, qbit.get(f"/torrents/files?hash={t['hash']}") or [], index)
        (skipped if "skip" in plan else plans).append({**plan, "hash": t["hash"], "name": t.get("name", "")})
    return {"plans": plans, "skipped": skipped}


# --- application, avec retour arrière ---------------------------------------------------------------
def _wait_recheck(qbit, ident, timeout):
    deadline = time.time() + timeout
    time.sleep(1)
    while time.time() < deadline:
        info = (qbit.get(f"/torrents/info?hashes={ident}") or [{}])[0]
        if info and not str(info.get("state", "")).startswith("checking"):
            return info
        time.sleep(3)
    raise ctl.SeedboxError("vérification trop longue")


def _rollback(qbit, plan, renamed, aside):
    ident = plan["hash"]
    qbit.stop(ident)
    qbit.post("/torrents/setLocation", {"hashes": ident, "location": plan["save_old"]})
    for m in renamed:
        try:
            qbit.post("/torrents/renameFile", {"hash": ident, "oldPath": m["new"], "newPath": m["name"]})
        except ctl.SeedboxError:
            pass
    for m in aside:
        if os.path.exists(m["local"] + ASIDE):
            os.replace(m["local"] + ASIDE, m["local"])
    qbit.post("/torrents/recheck", {"hashes": ident})


def apply(plan, qbit=None, timeout=RECHECK_TIMEOUT, by="console"):
    """Repointe un torrent vers le NAS. Renvoie les octets libérés ; lève une erreur après remise en état."""
    qbit = qbit or connect()
    if qbit is None:
        raise ctl.SeedboxError("qBittorrent est arrêté")
    ident = plan["hash"]
    aside, renamed = [], []
    qbit.stop(ident)
    try:
        for m in plan["moves"]:
            os.replace(m["local"], m["local"] + ASIDE)               # même disque : instantané, réversible
            aside.append(m)
        qbit.post("/torrents/setLocation", {"hashes": ident, "location": plan["save_new"]})
        for m in plan["moves"]:
            if m["new"] != m["name"]:
                qbit.post("/torrents/renameFile", {"hash": ident, "oldPath": m["name"], "newPath": m["new"]})
            renamed.append(m)
        for name in plan["extras"]:
            index = next((f["index"] for f in qbit.get(f"/torrents/files?hash={ident}") if f["name"] == name), None)
            if index is not None:
                qbit.post("/torrents/filePrio", {"hash": ident, "id": index, "priority": 0})
        qbit.post("/torrents/recheck", {"hashes": ident})
        info = _wait_recheck(qbit, ident, timeout)
        done = {m["new"] for m in plan["moves"]}
        files = qbit.get(f"/torrents/files?hash={ident}") or []
        wanted = [f for f in files if f.get("priority", 1) != 0]
        if not wanted or any(f.get("progress", 0) < 1 for f in wanted) or {f["name"] for f in wanted} - done - set(plan["extras"]):
            raise ctl.SeedboxError("vérification incomplète depuis le NAS")
        if info.get("progress", 0) < 0 or not all(os.path.isfile(_host(f"{plan['save_new']}/{m['new']}") or "") for m in plan["moves"]):
            raise ctl.SeedboxError("fichier du NAS introuvable")
    except (ctl.SeedboxError, OSError) as error:
        _rollback(qbit, plan, renamed, aside)
        qbit.start(ident)
        raise ctl.SeedboxError(f"{plan['name']} : laissé sur le disque local ({error})")
    for m in aside:
        os.remove(m["local"] + ASIDE)
    qbit.start(ident)
    say(f"{plan['name']} : seed depuis le NAS, {plan['size'] >> 20} Mo libérés ({by})")
    return plan["size"]


# --- torrents cross-seed restés en erreur -----------------------------------------------------------
ERROR_STATES = ("error", "missingFiles")
ERROR_AGE = 24 * 3600
ERROR_MAX_PROGRESS = 0.05


def errored(torrents, now=None):
    """Torrents cross-seed en erreur depuis plus de 24 h et à moins de 5 % : sans rien à partager, sans ratio à perdre.
    Les autres torrents en erreur (avancés, ou hors cross-seed) ne sont jamais touchés."""
    now = int(time.time() if now is None else now)
    found = []
    for t in torrents:
        tags = {x.strip() for x in str(t.get("tags", "")).split(",")}
        if t.get("state") not in ERROR_STATES or not (t.get("category") == "cross-seed-link" or "cross-seed" in tags):
            continue
        if t.get("progress", 0) >= ERROR_MAX_PROGRESS or now - int(t.get("added_on", now)) < ERROR_AGE:
            continue
        found.append(t)
    return found


def clean_errors(qbit=None, now=None, by="timer"):
    """Supprime ces torrents et leurs quelques fichiers partiels ; cross-seed les rajoutera si un correspondant existe encore."""
    qbit = qbit or connect()
    if qbit is None:
        return []
    found = errored(qbit.get("/torrents/info") or [], now)
    if found:
        qbit.post("/torrents/delete", {"hashes": "|".join(t["hash"] for t in found), "deleteFiles": "true"})
        for t in found:
            say(f"torrent cross-seed en erreur supprimé : {t.get('name', '')} ({by})")
    return [t.get("name", "") for t in found]


def run(by="timer", limit=None):
    """Repointe tous les torrents éligibles. Une erreur sur un torrent ne bloque pas les suivants."""
    if by == "timer" and not settings()["enabled"]:
        return {"freed": 0, "done": 0, "failed": [], "cleaned": 0, "disabled": True}
    found = candidates()
    freed, done, failed = 0, 0, []
    plans = found["plans"][:limit] if limit else found["plans"]
    say(f"{len(plans)} torrent(s) à repointer vers le NAS")
    for i, plan in enumerate(plans, 1):
        say(f"{i}/{len(plans)} : {plan['name']}")
        try:
            freed += apply(plan, by=by)
            done += 1
        except ctl.SeedboxError as error:
            failed.append(str(error))
            say(f"ignoré : {error}")
    cleaned = []
    try:
        cleaned = clean_errors(by=by)
    except (ctl.SeedboxError, OSError) as error:
        say(f"nettoyage des erreurs cross-seed ignoré : {error}")
    result = {"freed": freed, "done": done, "failed": failed, "cleaned": len(cleaned), "at": int(time.time())}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(result))
    except OSError:
        pass
    return result


def overview():
    found = candidates()
    try:
        last = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        last = None
    return {"enabled": settings()["enabled"], "plans": [{k: p[k] for k in ("hash", "name", "size", "save_new")} for p in found["plans"]],
            "skipped": found["skipped"], "errors": len(errored(connect().get("/torrents/info") or [])) if connect() else 0, "saving": sum(p["size"] for p in found["plans"]), "note": found.get("note", ""), "last": last}
