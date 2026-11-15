"""SeedBoxBuntu - normalisation des noms de fichiers (renommage par Radarr et Sonarr).

Radarr et Sonarr savent calculer le nom que chaque fichier devrait porter d'après leur format de nommage (GET /rename). Cette page
fait cet aperçu pour la bibliothèque, titre par titre, et affiche combien de fichiers changeraient et un exemple avant/après. « Renommer »
demande ensuite à l'application de le faire (commande RenameFiles) : elle renomme sur place, sans copie.
Étapes : « Analyser » (un appel par titre, 300 titres par passage, en reprenant où le passage précédent s'est arrêté, en tâche de
fond), puis cocher et renommer. 25 titres au plus par action.
Limites assumées : seuls les fichiers sont renommés (les dossiers de titre ne bougent pas) ; un lecteur qui retient des chemins
(Jellyfin) les retrouve au prochain balayage ; un fichier encore partagé par un torrent garde son nom côté torrent (le lien dur
suit : seul le nom dans la médiathèque change, le torrent continue de partager).
    seedboxctl renamer show | scan | apply radarr|sonarr ID...
"""
import json
import time

import seedboxctl as ctl

FILE = ctl.STATE / "renamer.json"
BATCH = 300
MAX_APPLY = 25
SHOWN = 80
APPS = {"radarr": ("/rename?movieId={id}", "movieFileId", "movieId"), "sonarr": ("/rename?seriesId={id}", "episodeFileId", "seriesId")}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def load():
    try:
        value = json.loads(FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def store(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data))


def _name(path):
    return str(path or "").rsplit("/", 1)[-1]


def scan(records=None, api=None, batch=BATCH, now=None):
    """Un passage : examine `batch` titres à partir du curseur ; renvoie le résumé."""
    api = api or ctl.arr_api
    records = ctl.libscan().records() if records is None else records
    owners = []
    seen = set()
    for r in records:
        if (r["app"], r["id"]) not in seen:
            seen.add((r["app"], r["id"]))
            owners.append((r["app"], r["id"], r["title"]))
    data = load()
    data.setdefault("titles", {})
    cursor = data.get("cursor", 0) % max(len(owners), 1)
    chosen = (owners[cursor:] + owners[:cursor])[:batch]
    errors = 0
    for app, owner_id, title in chosen:
        path, file_field, _owner_field = APPS[app]
        key = f"{app}:{owner_id}"
        try:
            result = api(app, "GET", path.format(id=owner_id))
        except (ctl.SeedboxError, OSError, ValueError):
            errors += 1
            continue
        if not result.ok:
            errors += 1
            continue
        items = [i for i in (result.json() or []) if i.get("existingPath") != i.get("newPath")]
        if items:
            data["titles"][key] = {"app": app, "id": owner_id, "title": title, "files": [i.get(file_field) for i in items if i.get(file_field)],
                                   "count": len(items), "example": {"before": _name(items[0].get("existingPath")), "after": _name(items[0].get("newPath"))}}
        else:
            data["titles"].pop(key, None)
    data["cursor"] = (cursor + len(chosen)) % max(len(owners), 1)
    data["at"] = int(time.time() if now is None else now)
    data["complete"] = len(chosen) >= len(owners)
    data["total"] = len(owners)
    store(data)
    return {"examined": len(chosen), "errors": errors, "titles": len(data["titles"])}


def apply(app, ids, api=None, by="console"):
    if app not in APPS:
        raise ctl.SeedboxError("application inconnue")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_APPLY or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ctl.SeedboxError(f"1 à {MAX_APPLY} identifiants")
    api = api or ctl.arr_api
    data = load()
    done, skipped = 0, []
    for owner_id in ids:
        entry = data.get("titles", {}).get(f"{app}:{owner_id}")
        if not entry:
            skipped.append({"id": owner_id, "reason": "pas dans l'aperçu : relancez l'analyse"})
            continue
        body = {"name": "RenameFiles", "files": entry["files"], APPS[app][2]: owner_id}
        result = api(app, "POST", "/command", body)
        if result.ok:
            done += 1
            data["titles"].pop(f"{app}:{owner_id}", None)
        else:
            skipped.append({"id": owner_id, "reason": f"refusé (HTTP {result.status})"})
    store(data)
    if done:
        ctl.audit().record(by, "", "Fichiers renommés", f"{done} titre(s) dans {app}", "console", "ok")
        try:
            ctl.libreport().record("rename", done)
        except Exception:  # noqa: BLE001
            pass
    return {"done": done, "skipped": skipped}


def overview():
    data = load()
    titles = sorted(data.get("titles", {}).values(), key=lambda t: -t["count"])
    return {"at": data.get("at", 0), "total": data.get("total", 0), "complete": data.get("complete", False), "cursor": data.get("cursor", 0),
            "titles": titles[:SHOWN], "titles_total": len(titles), "files_total": sum(t["count"] for t in titles)}
