"""SeedBoxBuntu - liste de la médiathèque (Radarr, Sonarr) : export planifié et réimport.

Les films et séries SUIVIS par Radarr et Sonarr sont exportés en JSON : titre, année, identifiants (TMDB, TVDB, IMDb),
suivi actif ou non, profil de qualité, dossier, présence du fichier. L'export est enregistré dans /etc/seedboxbuntu/catalogue/
(donc inclus dans chaque sauvegarde de l'appliance), une fois par semaine par défaut, les 4 derniers sont conservés.
Un fichier CSV du dernier export se télécharge depuis la console, pour un tableur.

Réimport : « seedboxctl catalogexport plan NOM » compare un export au contenu actuel de Radarr et de Sonarr sans rien
modifier (à blanc) ; « apply NOM » ajoute ce qui manque, avec le suivi d'origine et SANS lancer de recherche : rien ne
se télécharge tant que vous ne le demandez pas. Un dossier d'origine qui se trouve dans un dossier racine connu est repris
tel quel, pour que des fichiers déjà présents soient reconnus.
Limite assumée : c'est la LISTE qui est sauvegardée, pas les fichiers vidéo. Les réglages d'application (profils, formats
personnalisés, indexeurs) sont dans la sauvegarde de l'appliance, pas ici.
    seedboxctl catalogexport show | run | plan [NOM] | apply [NOM]
"""
import csv
import io
import json
import os
import re
import time
from pathlib import Path

import seedboxctl as ctl

DIR = ctl.ETC / "catalogue"
SETTINGS_FILE = ctl.ETC / "catalogexport.json"
NAME = re.compile(r"^catalogue-[0-9]{8}-[0-9]{6}\.json$")
DEFAULTS = {"enabled": True, "days": 7, "keep": 4}
FORMAT = "seedboxbuntu-catalog"
MAX_ITEMS = 50000
SAMPLE = 10


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        if key == "enabled":
            cfg[key] = bool(value)
        else:
            low, high = (1, 90) if key == "days" else (1, 20)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ctl.SeedboxError(f"{key} : entier de {low} à {high}")
            cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg, indent=1))
    say("export du catalogue : réglages enregistrés")
    return cfg


# --- lecture de Radarr et de Sonarr --------------------------------------------------------------
def api(name, path):
    result = ctl.arr_api(name, "GET", path)
    if not result.ok:
        raise ctl.SeedboxError(f"{name} : réponse HTTP {result.status}")
    return result.json() or []


def profile_names(name):
    return {p["id"]: p.get("name", "") for p in api(name, "/qualityprofile")}


def collect(api=api):
    """Contenu actuel de Radarr et de Sonarr, au format d'export. Un service arrêté donne une liste vide et une mention."""
    bundle = {"format": FORMAT, "version": 1, "created": int(time.time()), "movies": [], "series": [], "skipped": []}
    try:
        names = {p["id"]: p.get("name", "") for p in api("radarr", "/qualityprofile")}
        for movie in api("radarr", "/movie"):
            bundle["movies"].append({
                "title": str(movie.get("title", ""))[:200], "year": movie.get("year") or 0, "tmdb": movie.get("tmdbId") or 0,
                "imdb": movie.get("imdbId") or "", "monitored": bool(movie.get("monitored")),
                "quality": names.get(movie.get("qualityProfileId"), ""), "path": str(movie.get("path", ""))[:400],
                "has_file": bool(movie.get("hasFile")), "size": int(movie.get("sizeOnDisk") or 0)})
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        bundle["skipped"].append("radarr : " + str(error)[:120])
    try:
        names = {p["id"]: p.get("name", "") for p in api("sonarr", "/qualityprofile")}
        for show in api("sonarr", "/series"):
            stats = show.get("statistics") or {}
            bundle["series"].append({
                "title": str(show.get("title", ""))[:200], "year": show.get("year") or 0, "tvdb": show.get("tvdbId") or 0,
                "tmdb": show.get("tmdbId") or 0, "imdb": show.get("imdbId") or "", "monitored": bool(show.get("monitored")),
                "quality": names.get(show.get("qualityProfileId"), ""), "path": str(show.get("path", ""))[:400],
                "episodes": int(stats.get("episodeFileCount") or 0), "episodes_total": int(stats.get("episodeCount") or 0),
                "size": int(stats.get("sizeOnDisk") or 0)})
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        bundle["skipped"].append("sonarr : " + str(error)[:120])
    return bundle


# --- fichiers ------------------------------------------------------------------------------------
def listing():
    if not DIR.is_dir():
        return []
    rows = []
    for path in sorted((p for p in DIR.iterdir() if NAME.match(p.name)), reverse=True):
        try:
            data = json.loads(path.read_text())
            rows.append({"name": path.name, "created": data.get("created", int(path.stat().st_mtime)),
                         "movies": len(data.get("movies", [])), "series": len(data.get("series", [])), "bytes": path.stat().st_size})
        except (OSError, ValueError):
            rows.append({"name": path.name, "created": int(path.stat().st_mtime), "movies": 0, "series": 0, "bytes": 0})
    return rows


def run(collector=collect, now=None):
    """Exporte maintenant. Une liste entièrement vide n'est pas enregistrée si elle provient de services en échec."""
    bundle = collector()
    if bundle["skipped"] and not bundle["movies"] and not bundle["series"]:
        raise ctl.SeedboxError("export impossible : " + " ; ".join(bundle["skipped"]))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"catalogue-{stamp}.json"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(bundle, ensure_ascii=False, indent=1))
    os.chmod(temp, 0o640)
    os.replace(temp, path)
    for old in [e["name"] for e in listing()][settings()["keep"]:]:
        (DIR / old).unlink(missing_ok=True)
    say(f"catalogue exporté : {len(bundle['movies'])} film(s), {len(bundle['series'])} série(s)")
    return {"name": path.name, "movies": len(bundle["movies"]), "series": len(bundle["series"]), "skipped": bundle["skipped"]}


def tick(now=None, collector=collect):
    """Passage de surveillance : exporte si le dernier export a plus de « days » jours. Renvoie True si un export a eu lieu."""
    cfg = settings()
    now = time.time() if now is None else now
    if not cfg["enabled"]:
        return False
    rows = listing()
    if rows and now - rows[0]["created"] < cfg["days"] * 86400:
        return False
    if not ctl.container_running("radarr") and not ctl.container_running("sonarr"):
        return False
    try:
        run(collector, now)
    except (ctl.SeedboxError, OSError) as error:
        say(f"export du catalogue : {str(error)[:120]}")
        return False
    return True


def load(name=None):
    rows = listing()
    if name is None:
        if not rows:
            raise ctl.SeedboxError("aucun export : lancez d'abord « seedboxctl catalogexport run »")
        name = rows[0]["name"]
    if not NAME.match(str(name)) or not (DIR / name).is_file():
        raise ctl.SeedboxError("export introuvable")
    return parse((DIR / name).read_text(), name)


def parse(text, name=""):
    try:
        data = json.loads(text)
    except ValueError:
        raise ctl.SeedboxError("fichier d'export illisible")
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != 1:
        raise ctl.SeedboxError("ce fichier n'est pas un export de catalogue SeedBoxBuntu")
    movies, series = data.get("movies", []), data.get("series", [])
    if not isinstance(movies, list) or not isinstance(series, list) or len(movies) + len(series) > MAX_ITEMS:
        raise ctl.SeedboxError("export invalide ou trop volumineux")
    data["movies"] = [m for m in movies if isinstance(m, dict) and isinstance(m.get("tmdb"), int) and m["tmdb"] > 0]
    data["series"] = [s for s in series if isinstance(s, dict) and isinstance(s.get("tvdb"), int) and s["tvdb"] > 0]
    data["rejected"] = len(movies) + len(series) - len(data["movies"]) - len(data["series"])
    data["name"] = name
    return data


def to_csv(bundle):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["type", "titre", "annee", "tmdb", "tvdb", "imdb", "suivi", "qualite", "dossier", "octets"])

    def safe(value):
        text = str(value)
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text

    for m in bundle.get("movies", []):
        writer.writerow(["film", safe(m.get("title", "")), m.get("year", ""), m.get("tmdb", ""), "", m.get("imdb", ""),
                         "oui" if m.get("monitored") else "non", safe(m.get("quality", "")), safe(m.get("path", "")), m.get("size", 0)])
    for s in bundle.get("series", []):
        writer.writerow(["série", safe(s.get("title", "")), s.get("year", ""), s.get("tmdb", ""), s.get("tvdb", ""), s.get("imdb", ""),
                         "oui" if s.get("monitored") else "non", safe(s.get("quality", "")), safe(s.get("path", "")), s.get("size", 0)])
    return out.getvalue()


# --- réimport ------------------------------------------------------------------------------------
def targets(api=api):
    """État des cibles : identifiants déjà suivis, profils de qualité et dossiers racine, par application."""
    state = {}
    for name, key in (("radarr", "tmdbId"), ("sonarr", "tvdbId")):
        try:
            existing = {item.get(key) for item in api(name, "/movie" if name == "radarr" else "/series")}
            profiles = {p.get("name", ""): p["id"] for p in api(name, "/qualityprofile")}
            roots = [r["path"].rstrip("/") for r in api(name, "/rootfolder") if r.get("path")]
            state[name] = {"ok": True, "existing": existing, "profiles": profiles, "roots": roots}
        except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
            state[name] = {"ok": False, "error": str(error)[:120], "existing": set(), "profiles": {}, "roots": []}
    return state


def resolve(item, target):
    """(profil, dossier racine, dossier du titre ou None) à utiliser pour un ajout, avec les écarts constatés."""
    notes = []
    profile = target["profiles"].get(item.get("quality", ""))
    if profile is None and target["profiles"]:
        profile = sorted(target["profiles"].values())[0]
        notes.append("profil de qualité remplacé")
    path = str(item.get("path", "")).rstrip("/")
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    if parent and parent in target["roots"]:
        return profile, parent, path, notes
    if target["roots"]:
        notes.append("dossier racine remplacé")
        return profile, target["roots"][0], None, notes
    return profile, None, None, notes


def plan(bundle, state=None):
    """À blanc : ce qui serait ajouté. Ne modifie rien."""
    state = state or targets()
    result = {"name": bundle.get("name", ""), "rejected": bundle.get("rejected", 0), "apps": {}}
    for app, items, key in (("radarr", bundle["movies"], "tmdb"), ("sonarr", bundle["series"], "tvdb")):
        target = state[app]
        entry = {"ok": target["ok"], "error": target.get("error", ""), "total": len(items), "already": 0, "to_add": 0,
                 "replaced": 0, "sample": []}
        if target["ok"]:
            for item in items:
                if item[key] in target["existing"]:
                    entry["already"] += 1
                    continue
                profile, root, path, notes = resolve(item, target)
                if profile is None or root is None:
                    entry["error"] = "aucun profil de qualité ou dossier racine configuré dans " + app
                    break
                entry["to_add"] += 1
                entry["replaced"] += 1 if notes else 0
                if len(entry["sample"]) < SAMPLE:
                    entry["sample"].append({"title": item.get("title", ""), "year": item.get("year", 0), "notes": notes})
        result["apps"][app] = entry
    return result


def apply(bundle, state=None, post=None, sleep=time.sleep):
    """Ajoute ce qui manque, suivi d'origine conservé, sans recherche. Renvoie {app: {added, failed, errors}}."""
    state = state or targets()
    post = post or (lambda app, path, body: ctl.arr_api(app, "POST", path, body))
    languages = None
    outcome = {}
    for app, items, key in (("radarr", bundle["movies"], "tmdb"), ("sonarr", bundle["series"], "tvdb")):
        target, added, failed, errors = state[app], 0, 0, []
        if not target["ok"]:
            outcome[app] = {"added": 0, "failed": 0, "errors": [target.get("error", "indisponible")]}
            continue
        if app == "sonarr":
            try:                                                 # Sonarr 3 exige un profil de langue ; Sonarr 4 n'en a plus
                profiles = ctl.arr_api("sonarr", "GET", "/languageprofile")
                languages = (profiles.json() or [{}])[0].get("id") if profiles.ok else None
            except (ctl.SeedboxError, OSError, ValueError, IndexError):
                languages = None
        for item in items:
            if item[key] in target["existing"]:
                continue
            profile, root, path, _ = resolve(item, target)
            if profile is None or root is None:
                failed += 1
                errors.append(f"{item.get('title', '?')} : aucun profil ou dossier racine")
                continue
            body = {"title": item.get("title", ""), "qualityProfileId": profile, "rootFolderPath": root,
                    "monitored": bool(item.get("monitored", True))}
            if path:
                body["path"] = path
            if app == "radarr":
                body.update(tmdbId=item["tmdb"], minimumAvailability="released", addOptions={"searchForMovie": False})
                route = "/movie"
            else:
                body.update(tvdbId=item["tvdb"], seasonFolder=True, seriesType="standard",
                            addOptions={"searchForMissingEpisodes": False, "monitor": "all" if item.get("monitored", True) else "none"})
                if languages:
                    body["languageProfileId"] = languages
                route = "/series"
            try:
                result = post(app, route, body)
                if result.ok:
                    added += 1
                else:
                    failed += 1
                    errors.append(f"{item.get('title', '?')} : HTTP {result.status}")
            except (ctl.SeedboxError, OSError, ValueError) as error:
                failed += 1
                errors.append(f"{item.get('title', '?')} : {str(error)[:80]}")
            sleep(0.2)
        outcome[app] = {"added": added, "failed": failed, "errors": errors[:10]}
        say(f"{app} : {added} ajouté(s), {failed} en échec")
    return outcome


def overview():
    return {"settings": settings(), "exports": listing(), "retention": settings()["keep"],
            "apps": {name: bool(ctl.container_running(name)) for name in ("radarr", "sonarr")}}
