"""SeedBoxBuntu - optimisation des seeds : ce que chaque torrent apporte vraiment à chaque tracker, et ce qui ne sert à rien.

Une fois par heure, la quantité envoyée par chaque torrent est relevée (qBittorrent ne donne que le total depuis le début). On
sait alors, après quelques jours, combien chaque torrent a envoyé sur 7 et 30 jours : c'est la mesure qui manque pour décider.
Analyse par tracker :
  - volume partagé, envoyé sur 7 et 30 jours, envoyé par Go stocké (efficacité), ratio, délai estimé pour atteindre l'objectif
    de ratio (page Trackers) au rythme de la semaine ;
  - répartition par âge (moins de 7 jours, 30, 90, plus) : où se trouve ce qui rapporte ;
  - torrents « rares » (peu de sources dans l'essaim : leur partage compte le plus), « attendus mais muets » (des demandeurs dans
    l'essaim, rien d'envoyé : une réannonce suffit souvent), « sans valeur » (aucun envoi depuis 30 jours, aucun demandeur) ;
  - « protégés » : les règles du tracker (ratio minimal, durée de partage minimale) ne sont pas encore remplies : jamais proposés
    au retrait, pour ne pas finir en « hit and run ».
Règles : /etc/seedboxbuntu/seedopt.json, par tracker : {"min_ratio": 1.0, "min_hours": 72} (0 et 0 = aucune exigence, tracker public).
Un tracker SANS règle déclarée n'est jamais proposé au retrait : la console ne devine pas ce que votre tracker exige.
Actions : « Réannoncer » (inoffensif) et « Retirer de qBittorrent » (les fichiers restent par défaut ; ils ne sont supprimés que si
chacun a un autre lien dur, par exemple dans la médiathèque : rien n'est alors perdu). La console revérifie tout côté serveur.
Limites assumées : sous 7 jours d'historique les chiffres sur 7 et 30 jours sont partiels et marqués ainsi ; « rare » et « demandeurs »
viennent du tracker tel que qBittorrent le voit (pas de garantie) ; retirer un torrent ne libère de l'espace que si ses fichiers sont supprimés ET ont un autre lien ailleurs, auquel cas la médiathèque garde le contenu et rien n'est libéré : l'intérêt est d'arrêter de partager ce qui n'apporte rien, l'espace des copies se récupère dans Bibliothèques ;
la console n'a pas accès à la vue du tracker sur votre compte (son ratio et ses règles font foi, pas ceux d'ici).
    seedboxctl seedopt show [TRACKER] | rules | rule TRACKER RATIO HEURES | unrule TRACKER | reannounce HASH... | retire HASH... [--delete-files] | sample
"""
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "seedopt.json"
HIST_FILE = ctl.STATE / "seedhist.json"
EVERY = 3300
KEEP_DAYS = 62
DAY = 86400
HASH = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
HOST = re.compile(r"^[a-z0-9][a-z0-9.-]{0,98}$")
DEFAULTS = {"rules": {}, "idle_days": 30, "idle_pct": 1.0}
UP_STATES = {"uploading", "stalledUP", "queuedUP", "forcedUP", "checkingUP", "pausedUP", "stoppedUP"}
BUCKETS = ((7, "moins de 7 jours"), (30, "7 à 30 jours"), (90, "1 à 3 mois"), (10 ** 6, "plus de 3 mois"))
SHOWN = 12


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# --- réglages ---------------------------------------------------------------------------------------
def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    cfg = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    cfg["rules"] = {h: r for h, r in (cfg.get("rules") or {}).items() if HOST.match(str(h)) and isinstance(r, dict)}
    return cfg


def _write(cfg):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)


def set_rule(host, min_ratio, min_hours):
    host = str(host).lower()
    if not HOST.match(host):
        raise ctl.SeedboxError("nom de tracker invalide")
    try:
        min_ratio, min_hours = float(min_ratio), int(min_hours)
    except (TypeError, ValueError):
        raise ctl.SeedboxError("ratio minimal : un nombre ; durée : des heures entières")
    if not 0 <= min_ratio <= 100 or not 0 <= min_hours <= 24 * 365:
        raise ctl.SeedboxError("ratio de 0 à 100, durée de 0 à 8760 heures")
    cfg = settings()
    cfg["rules"][host] = {"min_ratio": round(min_ratio, 2), "min_hours": min_hours}
    _write(cfg)
    say(f"règle du tracker {host} : ratio {min_ratio}, {min_hours} h")
    return cfg


def unset_rule(host):
    cfg = settings()
    cfg["rules"].pop(str(host).lower(), None)
    _write(cfg)
    return cfg


def set_thresholds(idle_days=None, idle_pct=None):
    cfg = settings()
    if idle_days is not None:
        if isinstance(idle_days, bool) or not isinstance(idle_days, int) or not 7 <= idle_days <= 180:
            raise ctl.SeedboxError("période d'inactivité : 7 à 180 jours")
        cfg["idle_days"] = idle_days
    if idle_pct is not None:
        if isinstance(idle_pct, bool) or not isinstance(idle_pct, (int, float)) or not 0 <= idle_pct <= 50:
            raise ctl.SeedboxError("seuil d'envoi : 0 à 50 % de la taille")
        cfg["idle_pct"] = float(idle_pct)
    _write(cfg)
    return cfg


# --- qBittorrent ------------------------------------------------------------------------------------
def fetch():
    """Liste brute des torrents de qBittorrent ; None si le service est arrêté."""
    if not ctl.container_running("qbittorrent"):
        return None
    cookie = ctl.qbit_login()
    base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"
    return ctl.request("GET", base + "/torrents/info", headers={"Cookie": cookie}, timeout=60).json() or []


def host_of(torrent):
    host = urllib.parse.urlsplit(torrent.get("tracker") or "").hostname
    return (host or "sans-tracker").lower()


# --- historique -------------------------------------------------------------------------------------
def _day(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def load_history():
    try:
        value = json.loads(HIST_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def sample(now=None, torrents=None, force=False):
    """Relevé horaire : la dernière valeur de chaque jour est conservée. Renvoie le nombre de torrents relevés."""
    now = time.time() if now is None else now
    history = load_history()
    stamp = history.get("_at", 0)
    if not force and now - stamp < EVERY:
        return 0
    torrents = fetch() if torrents is None else torrents
    if torrents is None:
        return 0
    today = _day(now)
    cutoff = _day(now - KEEP_DAYS * DAY)
    present = set()
    for t in torrents:
        h = str(t.get("hash", "")).lower()
        if not HASH.match(h):
            continue
        present.add(h)
        entry = history.setdefault(h, {"d": {}})
        entry["d"][today] = int(t.get("uploaded", 0))
        entry["d"] = {d: v for d, v in entry["d"].items() if d >= cutoff}
    for h in [k for k in history if k != "_at" and k not in present]:
        del history[h]                                                # torrent retiré : son historique n'a plus d'usage
    history["_at"] = int(now)
    HIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = HIST_FILE.with_name(HIST_FILE.name + ".tmp")
    temp.write_text(json.dumps(history, separators=(",", ":")))
    os.replace(temp, HIST_FILE)
    return len(present)


def tick(now=None, torrents=None):
    try:
        return bool(sample(now, torrents))
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"relevé des seeds : {str(error)[:100]}")
        return False


def sent(history, h, uploaded, days, now):
    """(octets envoyés sur `days` jours, jours réellement couverts)."""
    entry = (history.get(h) or {}).get("d") or {}
    if not entry:
        return 0, 0
    first = min(entry)
    since = _day(now - days * DAY)
    base_days = [d for d in entry if d <= since]
    if base_days:
        return max(0, uploaded - entry[max(base_days)]), days
    covered = max(1, int((time.mktime(time.strptime(_day(now), "%Y-%m-%d")) - time.mktime(time.strptime(first, "%Y-%m-%d"))) / DAY))
    return max(0, uploaded - entry[first]), min(days, covered)


# --- analyse ----------------------------------------------------------------------------------------
def protected(t, rule, now):
    if not rule:
        return False
    if rule.get("min_hours") and t.get("seeding_time", 0) < rule["min_hours"] * 3600:
        return True
    downloaded = t.get("downloaded", 0) or t.get("size", 0)
    ratio = t.get("uploaded", 0) / downloaded if downloaded else 0
    return bool(rule.get("min_ratio")) and ratio < rule["min_ratio"]


def unique_of(t):
    """(octets qui n'existent que dans ce torrent, tous les fichiers ont-ils un autre lien ?)."""
    libscan = ctl.libscan()
    path = str(t.get("content_path") or "")
    host = libscan.to_host(path)
    if not host or not host.exists():
        return 0, False
    files = [host] if host.is_file() else [Path(f) / n for f, _d, names in os.walk(host) for n in names][:2000]
    total, linked = 0, True
    for f in files:
        try:
            info = os.stat(f)
        except OSError:
            continue
        if info.st_nlink == 1:
            total += info.st_size
            linked = False
    return total, linked and bool(files)


def classify(t, rule, cfg, up30, covered, now):
    """protégé, attendu mais muet, rare, sans valeur, utile ou normal."""
    if t.get("amount_left", 0) > 0:
        return "en cours"
    if rule is not None and protected(t, rule, now):
        return "protégé"
    swarm_seeds, swarm_leech = int(t.get("num_complete", 0) or 0), int(t.get("num_incomplete", 0) or 0)
    if swarm_leech > 0 and t.get("upspeed", 0) == 0 and t.get("state") in ("stalledUP", "queuedUP"):
        return "attendu mais muet"
    if 0 <= swarm_seeds <= 2 and swarm_leech > 0:
        return "rare"
    size = max(t.get("size", 0), 1)
    if covered >= 14 and up30 < size * cfg["idle_pct"] / 100 and swarm_leech == 0:
        return "sans valeur" if rule is not None else "sans valeur (règles inconnues)"
    return "utile" if up30 >= size * 0.05 else "normal"


def analyse(torrents, history=None, cfg=None, now=None, goals=None):
    """Analyse par tracker. torrents : liste qBittorrent ; renvoie {trackers, torrents, summary}."""
    cfg = cfg or settings()
    history = load_history() if history is None else history
    now = time.time() if now is None else now
    goals = goals or {}
    rows, per_host = [], {}
    for t in torrents:
        h = str(t.get("hash", "")).lower()
        host = host_of(t)
        up7, cov7 = sent(history, h, t.get("uploaded", 0), 7, now)
        up30, cov30 = sent(history, h, t.get("uploaded", 0), 30, now)
        rule = cfg["rules"].get(host)
        kind = classify(t, rule, cfg, up30, cov30, now)
        age = max(0, (now - (t.get("completion_on") or t.get("added_on") or now)) / DAY)
        row = {"hash": h, "name": str(t.get("name", ""))[:160], "tracker": host, "size": int(t.get("size", 0)), "up7": up7, "up30": up30,
               "covered": cov30, "class": kind, "age": round(age, 1), "seeds": int(t.get("num_complete", 0) or 0), "leechers": int(t.get("num_incomplete", 0) or 0),
               "ratio": round(t.get("uploaded", 0) / t["downloaded"], 2) if t.get("downloaded") else None, "state": t.get("state", ""),
               "category": t.get("category", ""), "protected": kind == "protégé"}
        rows.append(row)
        d = per_host.setdefault(host, {"tracker": host, "torrents": 0, "size": 0, "uploaded": 0, "downloaded": 0, "up7": 0, "up30": 0,
                                       "covered": 0, "classes": {}, "buckets": {label: {"torrents": 0, "size": 0, "up30": 0} for _l, label in BUCKETS},
                                       "rule": rule})
        d["torrents"] += 1
        d["size"] += row["size"]
        d["uploaded"] += int(t.get("uploaded", 0))
        d["downloaded"] += int(t.get("downloaded", 0))
        d["up7"] += up7
        d["up30"] += up30
        d["covered"] = max(d["covered"], cov30)
        cls = d["classes"].setdefault(kind, {"torrents": 0, "size": 0})
        cls["torrents"] += 1
        cls["size"] += row["size"]
        for limit, label in BUCKETS:
            if age < limit:
                b = d["buckets"][label]
                b["torrents"] += 1
                b["size"] += row["size"]
                b["up30"] += up30
                break
    trackers = []
    for host, d in per_host.items():
        goal = goals.get(host)
        eta = None
        if goal and d["downloaded"]:
            missing = goal * d["downloaded"] - d["uploaded"]
            eta = 0 if missing <= 0 else (round(missing / (d["up7"] / 7) / 1) if d["up7"] > 0 and d["covered"] >= 3 else None)
        gib = max(d["size"] / (1 << 30), 0.001)
        d.update(ratio=round(d["uploaded"] / d["downloaded"], 2) if d["downloaded"] else None,
                 efficiency=round(d["up30"] / (1 << 30) / gib, 3), goal=goal, eta_days=eta, buckets=[{"label": k, **v} for k, v in d["buckets"].items()],
                 classes=[{"class": k, **v} for k, v in sorted(d["classes"].items())], covered_ok=d["covered"] >= 7,
                 top=sorted([r for r in rows if r["tracker"] == host], key=lambda r: -r["up30"])[:SHOWN])
        d["advice"] = advice(host, d, [r for r in rows if r["tracker"] == host])
        trackers.append(d)
    trackers.sort(key=lambda d: -d["up30"])
    return {"trackers": trackers, "torrents": len(rows), "history_days": max((d["covered"] for d in per_host.values()), default=0),
            "rows": rows}


def advice(host, d, rows):
    """Conseils chiffrés, en phrases : ce qui est démontré par les mesures, rien de plus."""
    notes = []
    if d["covered"] < 7:
        notes.append(f"Historique de {d['covered']} jour(s) seulement : les chiffres sur 7 et 30 jours seront fiables dans quelques jours.")
    silent = [r for r in rows if r["class"] == "attendu mais muet"]
    if silent:
        notes.append(f"{len(silent)} torrent(s) ont des demandeurs dans l'essaim mais n'envoient rien : une réannonce peut les relancer.")
    rare = [r for r in rows if r["class"] == "rare"]
    if rare:
        notes.append(f"{len(rare)} torrent(s) ont très peu de sources : les garder partagés est ce qui aide le plus l'essaim (et votre ratio).")
    useless = [r for r in rows if r["class"] == "sans valeur"]
    if useless:
        notes.append(f"{len(useless)} torrent(s) n'ont rien envoyé depuis la période d'inactivité et n'ont aucun demandeur : "
                     f"{sum(r['size'] for r in useless) >> 30} Go de partage inutile, retirables sans risque pour les règles du tracker.")
    unknown = [r for r in rows if r["class"] == "sans valeur (règles inconnues)"]
    if unknown:
        notes.append(f"{len(unknown)} torrent(s) semblent inutiles mais aucune règle n'est déclarée pour {host} : renseignez-la pour pouvoir les retirer.")
    if d["goal"] and d["ratio"] is not None and d["ratio"] < d["goal"]:
        notes.append(f"Ratio {d['ratio']} sous l'objectif {d['goal']}" + (f" ; objectif atteint dans environ {d['eta_days']} jour(s) au rythme actuel." if d["eta_days"] else "."))
    if not notes:
        notes.append("Rien à signaler.")
    return notes


def goals_from_ratiogoals():
    try:
        return dict(ctl.ratiogoals().settings().get("goals", {}))
    except Exception:  # noqa: BLE001
        return {}


def overview(host=None, torrents=None, now=None):
    cfg = settings()
    torrents = fetch() if torrents is None else torrents
    if torrents is None:
        return {"settings": cfg, "available": False, "trackers": [], "torrents": 0, "history_days": 0, "retirable": []}
    result = analyse(torrents, None, cfg, now, goals_from_ratiogoals())
    rows = result.pop("rows")
    retirable = [r for r in rows if r["class"] == "sans valeur" and (host is None or r["tracker"] == host)]
    by_hash = {str(t.get("hash", "")).lower(): t for t in torrents}
    for r in retirable[:60]:
        unique, safe = unique_of(by_hash[r["hash"]])
        r["unique"], r["all_linked"] = unique, safe
    return {**result, "settings": cfg, "available": True, "retirable": retirable[:60],
            "retirable_total": len(retirable), "retirable_size": sum(r["size"] for r in retirable),
            "retirable_unique": sum(r.get("unique", 0) for r in retirable[:60]),
            "silent": [r for r in rows if r["class"] == "attendu mais muet"][:60]}


# --- actions ----------------------------------------------------------------------------------------
def _clean_hashes(hashes):
    clean = [str(h).lower() for h in hashes]
    if not clean or len(clean) > 100 or not all(HASH.match(h) for h in clean):
        raise ctl.SeedboxError("1 à 100 identifiants de torrents valides")
    return clean


def _post(path, form, cookie=None):
    cookie = cookie or ctl.qbit_login()
    base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"
    result = ctl.request("POST", base + path, headers={"Cookie": cookie}, form=form, timeout=60)
    if not result.ok:
        raise ctl.SeedboxError(f"qBittorrent : HTTP {result.status}")
    return result


def reannounce(hashes, post=None, by="console"):
    clean = _clean_hashes(hashes)
    (post or _post)("/torrents/reannounce", {"hashes": "|".join(clean)})
    say(f"réannonce : {len(clean)} torrent(s)")
    return {"done": len(clean)}


def retire(hashes, delete_files=False, torrents=None, post=None, now=None, by="console"):
    """Retire de qBittorrent des torrents classés « sans valeur » (règles connues et remplies). Revérifié côté serveur."""
    clean = _clean_hashes(hashes)
    cfg = settings()
    torrents = fetch() if torrents is None else torrents
    if torrents is None:
        raise ctl.SeedboxError("qBittorrent est arrêté")
    result = analyse(torrents, None, cfg, now, {})
    known = {r["hash"]: r for r in result["rows"]}
    by_hash = {str(t.get("hash", "")).lower(): t for t in torrents}
    refused = [h for h in clean if h not in known or known[h]["class"] != "sans valeur"]
    if refused:
        raise ctl.SeedboxError(f"{len(refused)} torrent(s) ne sont pas retirables (protégés, utiles, sans règle déclarée ou inconnus)")
    if delete_files:
        for h in clean:
            _reclaim, linked = unique_of(by_hash[h])
            if not linked:
                raise ctl.SeedboxError("suppression des fichiers refusée : au moins un fichier n'a aucun autre lien (il serait perdu)")
    (post or _post)("/torrents/delete", {"hashes": "|".join(clean), "deleteFiles": "true" if delete_files else "false"})
    try:
        ctl.audit().record(by, "", "Torrents retirés (sans valeur)", f"{len(clean)} torrent(s)" + (", fichiers supprimés" if delete_files else ""), "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    say(f"torrents retirés : {len(clean)}" + (" (fichiers supprimés)" if delete_files else ""))
    return {"done": len(clean), "bytes": sum(known[h]["size"] for h in clean)}


def check_trackers(hashes, get=None):
    """État du tracker pour quelques torrents (« non enregistré » = le tracker ne connaît plus ce torrent)."""
    clean = _clean_hashes(hashes)[:30]
    cookie = None
    out = []
    for h in clean:
        if get:
            rows = get(h)
        else:
            cookie = cookie or ctl.qbit_login()
            base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"
            rows = ctl.request("GET", base + f"/torrents/trackers?hash={h}", headers={"Cookie": cookie}, timeout=30).json() or []
        real = [r for r in rows if str(r.get("url", "")).startswith("http")]
        message = " ".join(str(r.get("msg", "")) for r in real)[:200]
        out.append({"hash": h, "unregistered": bool(re.search(r"(?i)unregistered|not registered|not found|torrent introuvable|non enregistr", message)),
                    "working": any(r.get("status") == 2 for r in real), "message": message})
    return out
