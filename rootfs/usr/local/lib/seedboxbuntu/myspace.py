"""SeedBoxBuntu - « Mon espace » : la page personnelle de chaque compte (https://serveur/me/).

Chaque personne connectée (invité, utilisateur, gestionnaire, administrateur) y voit, en lecture seule, SES informations :
durée restante du compte, limites (lectures simultanées, débit, téléchargement), quota de demandes Seerr et ce qu'il
en reste, état de ses dernières demandes (en attente, approuvée, en préparation, disponible, refusée).

Surface exposée, donc durcie :
  - la route Traefik /me exige Authelia ; elle ne transmet PAS le secret de l'administration, seulement un secret propre à
    « Mon espace » ; le serveur de la console n'y répond jamais qu'à des lectures de /me/ ;
  - l'identité vient de l'en-tête posé par Authelia (Remote-User), jamais d'un paramètre : aucune requête ne peut viser un
    autre compte, il n'existe ni liste de comptes ni identifiant à deviner ;
  - la réponse ne contient que des textes et des nombres propres au compte (jamais d'identifiant interne, de clé ou
    d'adresse) ; les titres viennent de TMDB via Seerr et sont affichés comme du texte, jamais comme du HTML ;
  - freinage : 60 requêtes par minute et par compte.
    seedboxctl myspace show LOGIN
"""
import json
import time

import seedboxctl as ctl

TITLE_TTL = 3600
TITLES = {}
REQUEST_STATUS = {1: "En attente d'approbation", 2: "Approuvée", 3: "Refusée", 4: "Échec"}
MEDIA_STATUS = {3: "En préparation", 4: "Partiellement disponible", 5: "Disponible"}
LIMIT = 60
WINDOW = 60
HITS = {}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def throttled(login, now=None):
    """Plus de LIMIT lectures par minute pour ce compte : refus."""
    now = time.time() if now is None else now
    recent = [t for t in HITS.get(login, []) if now - t < WINDOW]
    if len(recent) >= LIMIT:
        HITS[login] = recent
        return True
    HITS[login] = recent + [now]
    if len(HITS) > 500:
        for key in [k for k, v in HITS.items() if not v or now - v[-1] > WINDOW]:
            del HITS[key]
    return False


def status_label(request):
    status = request.get("status")
    if status in (1, 3, 4):
        return REQUEST_STATUS[status]
    media = (request.get("media") or {}).get("status")
    return MEDIA_STATUS.get(media, REQUEST_STATUS.get(status, "Approuvée"))


def title_of(kind, tmdb, now=None, fetch=None):
    now = time.time() if now is None else now
    key = (kind, tmdb)
    cached = TITLES.get(key)
    if cached and now - cached[0] < TITLE_TTL:
        return cached[1]
    title = ""
    try:
        data = (fetch or (lambda k, i: ctl.seerr_api("GET", f"/{'movie' if k == 'movie' else 'tv'}/{i}").json()))(kind, tmdb) or {}
        title = str(data.get("title") or data.get("name") or "")[:120]
    except (ctl.SeedboxError, OSError, ValueError):
        title = ""
    if len(TITLES) > 500:
        TITLES.clear()
    TITLES[key] = (now, title)
    return title


def quota_view(raw):
    """Quota d'un type de demande : {limit, used, remaining, days} ; None sans quota."""
    if not isinstance(raw, dict) or not raw.get("limit"):
        return None
    limit = int(raw.get("limit") or 0)
    used = int(raw.get("used") or 0)
    return {"limit": limit, "used": used, "remaining": max(0, int(raw.get("remaining", max(0, limit - used)) or 0)),
            "days": int(raw.get("days") or 0), "restricted": bool(raw.get("restricted"))}


def overview(login, now=None, fetch=None):
    """Tout ce que la personne voit : uniquement les informations du compte « login »."""
    now = time.time() if now is None else now
    users = {u["login"]: u["role"] for u in ctl.user_list()}
    if login not in users:
        raise ctl.SeedboxError("compte introuvable")
    guests = ctl.guests()
    entry = guests.read_json(guests.GUESTS_FILE, {}).get(login, {})
    profile = {**guests.PROFILE_DEFAULTS, **(entry.get("profile") or {})}
    expires = int(entry.get("expires") or 0)
    result = {"login": login, "role": users[login], "expires": expires, "days_left": max(0, (expires - int(now)) // 86400) if expires else None,
              "limits": {"sessions": profile["max_sessions"], "bitrate_mbps": profile["bitrate_mbps"], "downloads": bool(profile["downloads"])},
              "quota": {"movie": None, "tv": None}, "requests": [], "requests_available": False}
    try:
        jellyfin = ctl.jellyfin_user(login)
        seerr = ctl.seerr_user_id(jellyfin["Id"]) if jellyfin else None
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        seerr = None
    if seerr:
        result["requests_available"] = True
        try:
            quota = ctl.seerr_api("GET", f"/user/{seerr}/quota").json() or {}
            result["quota"] = {"movie": quota_view(quota.get("movie")), "tv": quota_view(quota.get("tv"))}
        except (ctl.SeedboxError, OSError, ValueError):
            pass
        try:
            rows = (ctl.seerr_api("GET", f"/user/{seerr}/requests?take=12&skip=0&sort=added").json() or {}).get("results", [])
        except (ctl.SeedboxError, OSError, ValueError):
            rows = []
        for row in rows[:12]:
            media = row.get("media") or {}
            kind = "movie" if (row.get("type") or media.get("mediaType")) == "movie" else "tv"
            tmdb = media.get("tmdbId")
            if not isinstance(tmdb, int):
                continue
            result["requests"].append({"kind": "film" if kind == "movie" else "série", "title": title_of(kind, tmdb, now, fetch) or f"Titre n°{tmdb}",
                                       "status": status_label(row), "date": str(row.get("createdAt", ""))[:10]})
    return result
