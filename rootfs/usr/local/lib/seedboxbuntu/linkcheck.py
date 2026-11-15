"""SeedBoxBuntu - contrôle des liens entre applications : Prowlarr ↔ Radarr/Sonarr, clients de téléchargement, Seerr, Jellyfin.

Un lien cassé se voit tard : un film demandé qui ne part jamais. Cette page teste, avec les fonctions de test des
applications elles-mêmes (le bouton « Tester » de leurs réglages), chaque lien de la pile :
  - Prowlarr -> Radarr et Sonarr (applications synchronisées) ;
  - Radarr et Sonarr -> client de téléchargement (qBittorrent) ;
  - Radarr et Sonarr : au moins un indexeur, dossiers racines présents et accessibles, espace libre ;
  - Prowlarr : indexeurs en échec (état « désactivé jusqu'à » signalé par Prowlarr) ;
  - Seerr -> Radarr et Sonarr ;
  - Jellyfin : le serveur répond, au moins une médiathèque.
Les indexeurs eux-mêmes ne sont pas contactés (ce serait autant de requêtes vers vos trackers) ; seul l'état déjà connu de
Prowlarr est lu. Rien n'est modifié. Pour un lien cassé, la réparation est « Relancer la configuration des applications »
(Applications) qui relie à nouveau tout ce que la console provisionne.
Limite assumée : un test réussi prouve que l'adresse et la clé sont bonnes, pas qu'une recherche donnera des résultats.
    seedboxctl linkcheck
"""
import time
from concurrent.futures import ThreadPoolExecutor

import seedboxctl as ctl

FIX = "Relancer la configuration des applications (Applications) relie à nouveau ce que la console provisionne."


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def item(check_id, title, state, detail, hint=""):
    return {"id": check_id, "title": title, "state": state, "detail": detail, "hint": hint}


def _failures(payload):
    """Messages d'échec d'un résultat « testall » des applications Arr."""
    if not isinstance(payload, list):
        return []
    out = []
    for row in payload:
        if row.get("isValid") is False:
            messages = [f.get("errorMessage", "") for f in row.get("validationFailures", []) if f.get("errorMessage")]
            out.append(f"{row.get('name') or row.get('id')} : " + ("; ".join(messages)[:140] or "échec"))
    return out


def _testall(api, app, path, title, check_id, ok_text, missing_text):
    try:
        listing = api(app, "GET", path)
        if not listing.ok:
            return item(check_id, title, "warn", f"{app} répond HTTP {listing.status}", FIX)
        if not (listing.json() or []):
            return item(check_id, title, "warn", missing_text, FIX)
        tested = api(app, "POST", path + "/testall")
        if tested.status == 404:
            return item(check_id, title, "ok", ok_text + " (test non disponible dans cette version)")
        failed = _failures(tested.json())
        if failed:
            return item(check_id, title, "error", "; ".join(failed)[:300], FIX)
        return item(check_id, title, "ok", ok_text)
    except (ctl.SeedboxError, OSError, ValueError) as error:
        return item(check_id, title, "warn", f"{app} injoignable : {str(error)[:100]}", "Vérifiez que le service est en marche (Applications).")


def check_arr(api, app):
    out = []
    version = "v1" if app == "prowlarr" else "v3"
    if app == "prowlarr":
        out.append(_testall(api, app, "/applications", "Prowlarr → Radarr, Sonarr", "prowlarr-apps", "Les applications reliées répondent.",
                            "Aucune application n'est reliée à Prowlarr : les indexeurs ne sont pas synchronisés."))
        try:
            status = api(app, "GET", "/indexerstatus")
            if status.ok:
                now = time.time()
                bad = []
                for row in status.json() or []:
                    until = row.get("disabledTill", "")
                    if until and until > time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)):
                        bad.append(str(row.get("indexerId")))
                out.append(item("prowlarr-indexers", "Prowlarr : indexeurs", "warn" if bad else "ok",
                                f"{len(bad)} indexeur(s) désactivé(s) temporairement par Prowlarr après des échecs." if bad else "Aucun indexeur en échec connu.",
                                "Ouvrez Prowlarr > Indexeurs pour voir lesquels et pourquoi." if bad else ""))
        except (ctl.SeedboxError, OSError, ValueError):
            pass
        return out
    out.append(_testall(api, app, "/downloadclient", f"{app.capitalize()} → client de téléchargement", f"{app}-client", "Le client de téléchargement répond.",
                        "Aucun client de téléchargement n'est configuré : rien ne peut être téléchargé."))
    try:
        indexers = api(app, "GET", "/indexer")
        count = len(indexers.json() or []) if indexers.ok else 0
        out.append(item(f"{app}-indexers", f"{app.capitalize()} : indexeurs", "ok" if count else "warn",
                        f"{count} indexeur(s) configuré(s)." if count else "Aucun indexeur : aucune recherche ne peut aboutir.",
                        "" if count else "Synchronisez-les depuis Prowlarr, ou " + FIX[0].lower() + FIX[1:]))
        roots = api(app, "GET", "/rootfolder")
        rows = roots.json() or [] if roots.ok else []
        broken = [r.get("path", "?") for r in rows if r.get("accessible") is False]
        out.append(item(f"{app}-roots", f"{app.capitalize()} : dossiers racines", "error" if broken or not rows else "ok",
                        ("Inaccessible : " + ", ".join(broken)) if broken else (f"{len(rows)} dossier(s) racine(s) accessible(s)." if rows else "Aucun dossier racine configuré."),
                        "Vérifiez que le partage ou le disque est monté (Partages)." if broken else ""))
    except (ctl.SeedboxError, OSError, ValueError):
        pass
    return out


def check_seerr(api_seerr):
    out = []
    for app in ("radarr", "sonarr"):
        title = f"Seerr → {app.capitalize()}"
        try:
            listing = api_seerr("GET", f"/settings/{app}")
            if not listing.ok:
                out.append(item(f"seerr-{app}", title, "warn", f"Seerr répond HTTP {listing.status}", FIX))
                continue
            servers = listing.json() or []
            if not servers:
                out.append(item(f"seerr-{app}", title, "warn", f"Seerr n'a aucun serveur {app.capitalize()} : les demandes ne partiront pas.", FIX))
                continue
            problems = []
            for server in servers:
                body = {"hostname": server.get("hostname"), "port": server.get("port"), "apiKey": server.get("apiKey"),
                        "useSsl": bool(server.get("useSsl")), "baseUrl": server.get("baseUrl", "")}
                test = api_seerr("POST", f"/settings/{app}/test", body)
                if not test.ok:
                    problems.append(f"{server.get('name', app)} : HTTP {test.status}")
            out.append(item(f"seerr-{app}", title, "error" if problems else "ok", "; ".join(problems) if problems else "Le serveur répond avec sa clé d'API.",
                            FIX if problems else ""))
        except (ctl.SeedboxError, OSError, ValueError) as error:
            out.append(item(f"seerr-{app}", title, "warn", f"Seerr injoignable : {str(error)[:100]}", "Vérifiez que Seerr est en marche (Applications)."))
    return out


def check_jellyfin(api_jellyfin):
    try:
        info = api_jellyfin("GET", "/System/Info/Public")
        if not info.ok:
            return [item("jellyfin", "Jellyfin", "warn", f"Jellyfin répond HTTP {info.status}", "")]
        folders = api_jellyfin("GET", "/Library/VirtualFolders")
        count = len(folders.json() or []) if folders.ok else 0
        return [item("jellyfin", "Jellyfin : médiathèques", "ok" if count else "warn",
                     f"{count} médiathèque(s) configurée(s)." if count else "Aucune médiathèque : rien à regarder.", "" if count else FIX)]
    except (ctl.SeedboxError, OSError, ValueError) as error:
        return [item("jellyfin", "Jellyfin", "warn", f"Jellyfin injoignable : {str(error)[:100]}", "Vérifiez que Jellyfin est en marche (Applications).")]


def run(api=None, seerr=None, jellyfin=None, running=None):
    api, seerr, jellyfin = api or ctl.arr_api, seerr or ctl.seerr_api, jellyfin or ctl.jellyfin_api
    running = running or ctl.container_running
    jobs = []
    for app in ("prowlarr", "radarr", "sonarr"):
        if running(app):
            jobs.append(lambda a=app: check_arr(api, a))
    if running("seerr"):
        jobs.append(lambda: check_seerr(seerr))
    if running("jellyfin"):
        jobs.append(lambda: check_jellyfin(jellyfin))
    items = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for part in pool.map(lambda job: job(), jobs):
            items.extend(part)
    problems = [i for i in items if i["state"] != "ok"]
    return {"at": int(time.time()), "items": items, "problems": len(problems), "errors": sum(1 for i in items if i["state"] == "error")}
