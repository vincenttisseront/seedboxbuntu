"""SeedBoxBuntu - journal des réglages : qui a changé quoi dans /etc/seedboxbuntu, et retour à la version d'avant.

Toutes les 5 minutes (passage de surveillance), chaque fichier de réglages de /etc/seedboxbuntu est comparé à sa dernière
copie. Un changement crée une entrée du journal : fichier, date, auteur probable (l'action de la console enregistrée dans le
journal d'audit au même moment, sinon « hors console » : modification à la main en SSH ou par une tâche) et lignes modifiées.
Les copies successives (20 par fichier) sont gardées dans /var/lib/seedboxbuntu/changelog/, lisibles par root seulement :
elles contiennent les réglages tels quels, y compris leurs secrets. À l'écran, les mots de passe, clés et jetons sont masqués
par un repère (comme dans la comparaison de sauvegardes) : on voit qu'une valeur a changé, jamais sa valeur.

« Annuler » remet la version d'avant pour UN fichier, seulement si cette entrée est la plus récente du fichier (sinon on
annule d'abord les plus récentes). L'annulation est elle-même une entrée du journal, donc annulable. Un réglage est lu à
chaque usage : aucun redémarrage n'est nécessaire.
Limites assumées : seuls les fichiers de /etc/seedboxbuntu sont suivis (pas les comptes, ni le .env, ni les bases) ; le code de
secours (emergency.json) est exclu volontairement ; l'auteur est une déduction (même minute dans le journal d'audit), pas une preuve.
    seedboxctl changelog show | undo ID | tick
"""
import hashlib
import json
import os
import re
import time

import seedboxctl as ctl

DIR = ctl.STATE / "changelog"
EVENTS = DIR / "events.jsonl"
INDEX = DIR / "index.json"
EXCLUDED = {"emergency.json"}
MAX_VERSIONS = 20
MAX_BYTES = 256 * 1024
MAX_EVENTS = 500
SHOWN = 100
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}\.json$")
SKEW = 90


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path, value):
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o700)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(value, ensure_ascii=False))
    os.replace(temp, path)


def watched():
    """Fichiers de réglages suivis : {nom: chemin}."""
    try:
        return {p.name: p for p in sorted(ctl.ETC.glob("*.json")) if NAME.match(p.name) and p.name not in EXCLUDED
                and p.is_file() and p.stat().st_size <= MAX_BYTES}
    except OSError:
        return {}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _keep(name, data, now):
    """Enregistre une copie de la version et renvoie son identifiant."""
    folder = DIR / "versions" / name
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR / "versions", 0o700)
    os.chmod(folder, 0o700)
    version = f"{int(now)}-{_sha(data)[:8]}"
    target = folder / version
    if not target.exists():
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    for old in sorted(folder.iterdir())[:-MAX_VERSIONS]:
        old.unlink(missing_ok=True)
    return version


def _version_text(name, version):
    if not NAME.match(name) or not re.fullmatch(r"\d+-[0-9a-f]{8}", str(version)):
        raise ctl.SeedboxError("version inconnue")
    path = DIR / "versions" / name / version
    try:
        return path.read_bytes()
    except OSError:
        raise ctl.SeedboxError("cette version n'est plus conservée (seules les 20 dernières le sont)")


def events():
    rows = []
    try:
        for line in EVENTS.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def _append(event):
    rows = events()
    rows.append(event)
    rows = rows[-MAX_EVENTS:]
    DIR.mkdir(parents=True, exist_ok=True)
    temp = EVENTS.with_name("events.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    os.replace(temp, EVENTS)


def author(changed_at):
    """Action de la console enregistrée dans le journal d'audit au moment du changement, sinon « hors console »."""
    try:
        found = ctl.audit().search(source="console", since=int(changed_at) - SKEW - 300, until=int(changed_at) + SKEW, limit=50)["events"]
    except Exception:  # noqa: BLE001 - le journal d'audit ne doit pas empêcher le suivi
        return "", ""
    for row in found:                                           # du plus récent au plus ancien
        if row["outcome"] == "ok" and row["action"].startswith(("POST ", "Réglage", "Mise", "Enregistr")) and row["ts"] <= changed_at + SKEW:
            return row["user"], row["action"]
    return "", ""


def _lines(name, data, masker):
    return masker.text(data.decode("utf-8", errors="replace"))


def summary(name, before, after):
    """(lignes modifiées, nombre de lignes de plus) avec secrets masqués ; le même repère pour une même valeur."""
    import difflib
    masker = ctl.backupdiff().Masker()
    old = _lines(name, before, masker) if before is not None else []
    new = _lines(name, after, masker) if after is not None else []
    lines = [l for l in difflib.unified_diff(old, new, lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
    return lines[:60], max(0, len(lines) - 60)


def tick(now=None):
    """Détecte les changements depuis le dernier passage. Le premier passage ne crée que des copies de départ."""
    now = time.time() if now is None else now
    index = _read(INDEX, {})
    if not isinstance(index, dict):
        index = {}
    first = not index
    created = 0
    for name, path in watched().items():
        try:
            data = path.read_bytes()
            changed_at = path.stat().st_mtime
        except OSError:
            continue
        sha = _sha(data)
        known = index.get(name)
        if known and known["sha"] == sha:
            continue
        version = _keep(name, data, changed_at if known else now)
        if known and not first:
            who, action = author(changed_at)
            _append({"id": f"{int(changed_at)}-{_sha((name + sha).encode())[:6]}", "file": name, "at": int(changed_at), "by": who or "hors console",
                     "action": action, "before": known["version"], "after": version, "undo": False})
            created += 1
            say(f"réglage modifié : {name} ({who or 'hors console'})")
        index[name] = {"sha": sha, "version": version}
    for name in [n for n in index if n not in watched() and not (ctl.ETC / n).exists()]:
        index.pop(name)                                         # fichier supprimé : on ne le suit plus
    _write(INDEX, index)
    return created


def listing(limit=SHOWN):
    rows = list(reversed(events()))[:limit]
    latest = {}
    for event in events():
        latest[event["file"]] = event["id"]
    return [{**{k: e[k] for k in ("id", "file", "at", "by", "action", "undo")}, "latest": latest.get(e["file"]) == e["id"]} for e in rows]


def detail(event_id):
    event = next((e for e in events() if e["id"] == event_id), None)
    if not event:
        raise ctl.SeedboxError("entrée inconnue")
    try:
        before, after = _version_text(event["file"], event["before"]), _version_text(event["file"], event["after"])
    except ctl.SeedboxError as error:
        return {"id": event_id, "file": event["file"], "lines": [], "more": 0, "note": str(error)}
    lines, more = summary(event["file"], before, after)
    return {"id": event_id, "file": event["file"], "lines": lines, "more": more, "note": ""}


def undo(event_id, by="console", now=None):
    now = time.time() if now is None else now
    rows = events()
    event = next((e for e in rows if e["id"] == event_id), None)
    if not event:
        raise ctl.SeedboxError("entrée inconnue")
    newer = [e for e in rows[rows.index(event) + 1:] if e["file"] == event["file"]]
    if newer:
        raise ctl.SeedboxError("une modification plus récente de ce fichier existe : annulez-la d'abord")
    name = event["file"]
    if name not in watched() and not (ctl.ETC / name).exists():
        raise ctl.SeedboxError("ce fichier n'existe plus")
    target = ctl.ETC / name
    old = _version_text(name, event["before"])
    try:
        json.loads(old)
    except ValueError:
        raise ctl.SeedboxError("la version d'avant n'est pas un JSON valide : annulation refusée")
    current = target.read_bytes() if target.exists() else b""
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    temp = target.with_name(target.name + ".undo")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(old)
    os.replace(temp, target)
    index = _read(INDEX, {})
    after = _keep(name, old, now)
    before = _keep(name, current, now - 1) if current else event["after"]
    index[name] = {"sha": _sha(old), "version": after}
    _write(INDEX, index)
    _append({"id": f"{int(now)}-{_sha((name + 'undo').encode())[:6]}", "file": name, "at": int(now), "by": by, "action": f"Annulation de {event_id}",
             "before": before, "after": after, "undo": True})
    try:
        ctl.audit().record(by, "", f"Réglage annulé : {name}", f"entrée {event_id}", "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    say(f"réglage annulé : {name} (entrée {event_id})")
    return {"file": name}


def overview():
    return {"events": listing(), "files": len(watched()), "keep": MAX_VERSIONS}
