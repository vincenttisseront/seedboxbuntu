"""SeedBoxBuntu - qualité des médias : ce qui est en dessous du niveau voulu, ce qui manque, ce qui ressemble à un doublon.

Lecture seule de Radarr et Sonarr (rien n'est modifié en lisant) :
  - « sous le seuil » : fichiers en dessous de la qualité visée par le profil (liste « cutoff unmet » des applications) ;
  - « manquants » : titres suivis, déjà sortis, sans fichier ;
  - « très compressés » (films) : moins de 2,5 Mo par minute, ce qui donne une image pauvre quelle que soit l'étiquette ;
  - « doublons possibles » (films) : même titre et même année enregistrés deux fois, sous deux identifiants.
Une seule action : « Chercher une meilleure version » pour les N premiers titres sous le seuil (20 par défaut, 50 au plus,
pour ne pas saturer les indexeurs). Elle lance une recherche dans Radarr ou Sonarr ; ce sont eux qui décident de télécharger
ou non selon leur profil, et qui remplacent le fichier seulement par une meilleure version. Rien n'est supprimé ici.
Limites assumées : « manquants » dépend de ce que Radarr et Sonarr savent de la date de sortie ; la compression est une
estimation (taille / durée annoncée) ; les doublons sont repérés par le nom, pas par le contenu des fichiers.
    seedboxctl mediaquality show | upgrade radarr|sonarr [N]
"""
import time

import seedboxctl as ctl

LOW_MB_PER_MIN = 2.5
SHOWN = 30
DEFAULT_UPGRADE = 20
MAX_UPGRADE = 50
APPS = ("radarr", "sonarr")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def _get(api, app, path):
    result = api(app, "GET", path)
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : HTTP {result.status}")
    return result.json()


def radarr_view(api):
    movies = _get(api, "radarr", "/movie") or []
    cutoff = _get(api, "radarr", f"/wanted/cutoff?page=1&pageSize={SHOWN}&sortKey=movieMetadata.sortTitle&sortDirection=ascending") or {}
    missing_all = [m for m in movies if m.get("monitored") and m.get("isAvailable") and not m.get("hasFile")]
    low_rate = []
    resolutions = {}
    seen = {}
    for movie in movies:
        file = movie.get("movieFile") or {}
        if file:
            res = ((file.get("quality") or {}).get("quality") or {}).get("resolution") or 0
            resolutions[str(res)] = resolutions.get(str(res), 0) + 1
            minutes = movie.get("runtime") or 0
            if minutes >= 30 and file.get("size"):
                rate = file["size"] / (1 << 20) / minutes
                if rate < LOW_MB_PER_MIN:
                    low_rate.append({"id": movie["id"], "title": movie.get("title", ""), "year": movie.get("year", 0), "mb_per_min": round(rate, 2),
                                     "size": file["size"]})
        seen.setdefault((str(movie.get("title", "")).strip().lower(), movie.get("year", 0)), []).append(movie)
    duplicates = [{"title": group[0].get("title", ""), "year": group[0].get("year", 0), "ids": [m["id"] for m in group]}
                  for group in seen.values() if len(group) > 1]
    low_rate.sort(key=lambda r: r["mb_per_min"])
    return {"app": "radarr", "total": len(movies), "with_file": sum(1 for m in movies if m.get("hasFile")),
            "below_cutoff": int(cutoff.get("totalRecords", 0)),
            "below_sample": [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year", 0),
                              "quality": (((m.get("movieFile") or {}).get("quality") or {}).get("quality") or {}).get("name", "")}
                             for m in cutoff.get("records", [])[:SHOWN]],
            "missing": len(missing_all), "missing_sample": [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year", 0)} for m in missing_all[:SHOWN]],
            "low_rate": len(low_rate), "low_rate_sample": low_rate[:SHOWN], "duplicates": len(duplicates), "duplicates_sample": duplicates[:SHOWN],
            "resolutions": resolutions}


def sonarr_view(api):
    series = _get(api, "sonarr", "/series") or []
    cutoff = _get(api, "sonarr", f"/wanted/cutoff?page=1&pageSize={SHOWN}&includeSeries=true") or {}
    missing = _get(api, "sonarr", f"/wanted/missing?page=1&pageSize={SHOWN}&includeSeries=true&monitored=true") or {}

    def episode(row):
        return {"id": row["id"], "title": (row.get("series") or {}).get("title", ""), "season": row.get("seasonNumber", 0), "episode": row.get("episodeNumber", 0)}

    return {"app": "sonarr", "total": len(series), "with_file": sum(1 for s in series if (s.get("statistics") or {}).get("episodeFileCount")),
            "below_cutoff": int(cutoff.get("totalRecords", 0)), "below_sample": [episode(r) for r in cutoff.get("records", [])[:SHOWN]],
            "missing": int(missing.get("totalRecords", 0)), "missing_sample": [episode(r) for r in missing.get("records", [])[:SHOWN]]}


def overview(api=None):
    api = api or ctl.arr_api
    result = {"at": int(time.time()), "apps": {}, "errors": {}}
    for app, build in (("radarr", radarr_view), ("sonarr", sonarr_view)):
        if not ctl.container_running(app) and api is ctl.arr_api:
            result["errors"][app] = "le service n'est pas en marche"
            continue
        try:
            result["apps"][app] = build(api)
        except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
            result["errors"][app] = str(error)[:160]
    return result


def upgrade(app, count=DEFAULT_UPGRADE, api=None, by="console"):
    """Lance la recherche d'une meilleure version pour les premiers titres sous le seuil. Renvoie le nombre de titres."""
    if app not in APPS:
        raise ctl.SeedboxError("application inconnue")
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ctl.SeedboxError("nombre de titres : un entier attendu")
    if not 1 <= count <= MAX_UPGRADE:
        raise ctl.SeedboxError(f"nombre de titres : 1 à {MAX_UPGRADE}")
    api = api or ctl.arr_api
    data = _get(api, app, f"/wanted/cutoff?page=1&pageSize={count}" + ("&includeSeries=true" if app == "sonarr" else "")) or {}
    ids = [r["id"] for r in data.get("records", [])[:count] if isinstance(r.get("id"), int)]
    if not ids:
        return {"searched": 0}
    body = {"name": "MoviesSearch", "movieIds": ids} if app == "radarr" else {"name": "EpisodeSearch", "episodeIds": ids}
    result = api(app, "POST", "/command", body)
    if not result.ok:
        raise ctl.SeedboxError(f"{app} : la recherche a été refusée (HTTP {result.status})")
    try:
        ctl.audit().record(by, "", f"Recherche d'une meilleure version : {app}", f"{len(ids)} titre(s)", "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    say(f"recherche d'une meilleure version : {app}, {len(ids)} titre(s)")
    return {"searched": len(ids)}
