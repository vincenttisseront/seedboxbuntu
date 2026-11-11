"""SeedBoxBuntu - invités : invitations par lien, profils de limites, comptes temporaires.

Invitation : l'administrateur crée un lien à usage limité (validité de 1 à 30 jours, 1 à 20 utilisations). La
personne invitée ouvre le lien (https://serveur/invite/...), choisit son identifiant et son mot de passe, et son
compte est créé comme par la console (Authelia, Jellyfin, Seerr, p2p-aggregator), avec le rôle « utilisateur »
uniquement. Les liens ne sont jamais enregistrés en clair : seul leur condensé SHA-256 est conservé, le lien
complet n'est montré qu'à la création.

Profil de limites, appliqué au compte à sa création ou à tout moment :
  - flux Jellyfin simultanés, débit maximal à distance, téléchargement des médias autorisé ou non
  - quota de demandes Seerr (films et séries, par période en jours)
  - durée de vie du compte : à l'échéance, le compte est supprimé (contrôle toutes les heures)

    seedboxctl guests list | create | revoke ID | redeem ID LOGIN | profile LOGIN | extend LOGIN DAYS | expire
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

import seedboxctl as ctl

INVITES_FILE = ctl.STATE / "invites.json"
GUESTS_FILE = ctl.STATE / "guests.json"
HISTORY_FILE = ctl.STATE / "guests-history.log"
PROFILE_DEFAULTS = {"account_days": 0, "max_sessions": 0, "bitrate_mbps": 0, "downloads": True,
                    "seerr_movie_limit": 0, "seerr_movie_days": 7, "seerr_tv_limit": 0, "seerr_tv_days": 7}
PROFILE_LIMITS = {"account_days": (0, 365), "max_sessions": (0, 10), "bitrate_mbps": (0, 200),
                  "seerr_movie_limit": (0, 100), "seerr_movie_days": (1, 90), "seerr_tv_limit": (0, 100), "seerr_tv_days": (1, 90)}
MAX_INVITES = 200
LABEL_RE = re.compile(r"^[\w .,'@+-]{0,60}$", re.U)
ID_RE = re.compile(r"^[0-9a-f]{8}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def read_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.new")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# --- profil --------------------------------------------------------------------------------------
def clean_profile(values):
    """Profil validé : valeurs inconnues ignorées, nombres bornés."""
    profile = dict(PROFILE_DEFAULTS)
    for key, value in (values or {}).items():
        if key == "downloads":
            profile[key] = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "oui", "yes")
        elif key in PROFILE_LIMITS:
            low, high = PROFILE_LIMITS[key]
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ctl.SeedboxError(f"{key} : nombre attendu")
            if not low <= number <= high:
                raise ctl.SeedboxError(f"{key} : entre {low} et {high}")
            profile[key] = number
    return profile


def apply_profile(login, profile):
    """Limites Jellyfin et quotas Seerr du compte. Renvoie la liste des avertissements (une étape en échec n'arrête pas les autres)."""
    warnings = []
    jellyfin = ctl.jellyfin_user(login)
    if not jellyfin:
        return [f"Jellyfin : compte {login} introuvable"]
    try:
        policy = jellyfin["Policy"]
        policy.update(MaxActiveSessions=profile["max_sessions"], RemoteClientBitrateLimit=profile["bitrate_mbps"] * 1_000_000,
                      EnableContentDownloading=profile["downloads"])
        result = ctl.jellyfin_api("POST", f"/Users/{jellyfin['Id']}/Policy", policy)
        if not result.ok:
            warnings.append(f"Jellyfin : limites refusées (HTTP {result.status})")
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        warnings.append(f"Jellyfin : {error}")
    try:
        seerr_id = ctl.seerr_user_id(jellyfin["Id"])
        if seerr_id:
            current = ctl.seerr_api("GET", f"/user/{seerr_id}/settings/main").json() or {}
            current.update(movieQuotaLimit=profile["seerr_movie_limit"] or None, movieQuotaDays=profile["seerr_movie_days"] if profile["seerr_movie_limit"] else None,
                           tvQuotaLimit=profile["seerr_tv_limit"] or None, tvQuotaDays=profile["seerr_tv_days"] if profile["seerr_tv_limit"] else None)
            result = ctl.seerr_api("POST", f"/user/{seerr_id}/settings/main", current)
            if not result.ok:
                warnings.append(f"Seerr : quotas refusés (HTTP {result.status})")
    except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
        warnings.append(f"Seerr : {error}")
    return warnings


# --- invitations ---------------------------------------------------------------------------------
def invites():
    return read_json(INVITES_FILE, [])


def status_of(invite, now=None):
    now = now or time.time()
    if invite.get("revoked"):
        return "révoquée"
    if invite["expires"] < now:
        return "expirée"
    if invite["uses"] >= invite["max_uses"]:
        return "utilisée"
    return "active"


def create(label="", days=7, uses=1, profile=None, by=""):
    label = (label or "").strip()
    if not LABEL_RE.match(label):
        raise ctl.SeedboxError("nom de l'invitation invalide (lettres, chiffres, espaces et . , ' @ + - ; 60 caractères au plus)")
    try:
        days, uses = int(days), int(uses)
    except (TypeError, ValueError):
        raise ctl.SeedboxError("durée et nombre d'utilisations : nombres attendus")
    if not 1 <= days <= 30:
        raise ctl.SeedboxError("validité de l'invitation : 1 à 30 jours")
    if not 1 <= uses <= 20:
        raise ctl.SeedboxError("utilisations : 1 à 20")
    profile = clean_profile(profile)
    current = invites()
    if sum(1 for i in current if status_of(i) == "active") >= MAX_INVITES:
        raise ctl.SeedboxError("trop d'invitations actives : révoquez-en avant d'en créer")
    token = secrets.token_urlsafe(24)
    now = int(time.time())
    invite = {"id": secrets.token_hex(4), "hash": token_hash(token), "label": label, "created": now, "by": by,
              "expires": now + days * 86400, "max_uses": uses, "uses": 0, "accounts": [], "profile": profile, "revoked": False}
    current.append(invite)
    write_json(INVITES_FILE, current[-500:])
    say(f"invitation créée : {label or invite['id']} ({days} jours, {uses} utilisation(s))")
    return {"id": invite["id"], "token": token, "path": f"/invite/{token}", "expires": invite["expires"]}


def public(invite):
    return {k: invite[k] for k in ("id", "label", "created", "expires", "max_uses", "uses", "accounts", "profile", "by")} | {"status": status_of(invite)}


def listing():
    return [public(i) for i in sorted(invites(), key=lambda i: -i["created"])]


def revoke(invite_id):
    current = invites()
    for invite in current:
        if invite["id"] == invite_id:
            invite["revoked"] = True
            write_json(INVITES_FILE, current)
            say(f"invitation révoquée : {invite['label'] or invite_id}")
            return
    raise ctl.SeedboxError("invitation introuvable")


def lookup(token, now=None):
    """Invitation active correspondant au jeton, ou None (aucune indication sur la raison)."""
    if not TOKEN_RE.match(token or ""):
        return None
    wanted = token_hash(token)
    found = None
    for invite in invites():
        if hmac.compare_digest(invite["hash"], wanted):
            found = invite
    return found if found and status_of(found, now) == "active" else None


def info(token):
    invite = lookup(token)
    if not invite:
        return {"valid": False}
    return {"valid": True, "label": invite["label"], "expires": invite["expires"], "profile": invite["profile"],
            "remaining": invite["max_uses"] - invite["uses"]}


def reserve(token, login, password):
    """Contrôles puis réservation d'une utilisation, avant de lancer la création du compte. Renvoie l'identifiant de l'invitation."""
    ctl.validate_account(login, password)
    if login in ctl.users_load():
        raise ctl.SeedboxError("cet identifiant est déjà pris")
    current = invites()
    wanted = token_hash(token) if TOKEN_RE.match(token or "") else ""
    for invite in current:
        if wanted and hmac.compare_digest(invite["hash"], wanted):
            if status_of(invite) != "active":
                break
            if login in invite.get("pending", []):
                raise ctl.SeedboxError("cet identifiant est en cours de création")
            invite["uses"] += 1
            invite.setdefault("pending", []).append(login)
            write_json(INVITES_FILE, current)
            return invite["id"]
    raise ctl.SeedboxError("invitation invalide ou expirée")


def release(invite_id, login):
    current = invites()
    for invite in current:
        if invite["id"] == invite_id and login in invite.get("pending", []):
            invite["pending"].remove(login)
            invite["uses"] = max(0, invite["uses"] - 1)
            write_json(INVITES_FILE, current)


def redeem(invite_id, login, password):
    """Crée le compte invité (tâche lancée par la console après la réservation), applique le profil, note l'échéance."""
    invite = next((i for i in invites() if i["id"] == invite_id and login in i.get("pending", [])), None)
    if not invite:
        raise ctl.SeedboxError("aucune réservation pour cette invitation")
    try:
        warnings = list(ctl.user_add(login, password, "user"))
        warnings += apply_profile(login, invite["profile"])
    except Exception:
        release(invite_id, login)
        raise
    current = invites()
    for stored in current:
        if stored["id"] == invite_id:
            stored["pending"] = [p for p in stored.get("pending", []) if p != login]
            stored["accounts"].append(login)
    write_json(INVITES_FILE, current)
    guests = read_json(GUESTS_FILE, {})
    days = invite["profile"]["account_days"]
    guests[login] = {"profile": invite["profile"], "expires": int(time.time()) + days * 86400 if days else 0,
                     "invite": invite_id, "label": invite["label"], "created": int(time.time())}
    write_json(GUESTS_FILE, guests)
    history(f"compte {login} créé par l'invitation {invite['label'] or invite_id}")
    for warning in warnings:
        say(f"attention : {warning}")
    say(f"compte invité créé : {login}")
    return warnings


# --- comptes invités -------------------------------------------------------------------------------
def history(text):
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%FT%T%z')} {text}\n")
    except OSError:
        pass


def guest_list():
    guests = read_json(GUESTS_FILE, {})
    users = {u["login"]: u["role"] for u in ctl.user_list()}
    rows = []
    for login, role in sorted(users.items()):
        if role != "user":
            continue
        entry = guests.get(login, {})
        rows.append({"login": login, "guest": bool(entry), "label": entry.get("label", ""), "expires": entry.get("expires", 0),
                     "created": entry.get("created", 0), "profile": entry.get("profile", dict(PROFILE_DEFAULTS))})
    return rows


def set_profile(login, values, extend_days=None):
    """Profil d'un compte « utilisateur » existant (invité ou non). `extend_days` repousse l'échéance."""
    users = {u["login"]: u["role"] for u in ctl.user_list()}
    if users.get(login) != "user":
        raise ctl.SeedboxError("les limites s'appliquent aux comptes de rôle « utilisateur » seulement")
    profile = clean_profile(values)
    warnings = apply_profile(login, profile)
    guests = read_json(GUESTS_FILE, {})
    entry = guests.get(login, {"invite": "", "label": "", "created": int(time.time()), "expires": 0})
    entry["profile"] = profile
    days = profile["account_days"]
    if extend_days is not None:
        entry["expires"] = int(time.time()) + int(extend_days) * 86400 if int(extend_days) else 0
    elif days and not entry.get("expires"):
        entry["expires"] = int(time.time()) + days * 86400
    elif not days and "account_days" in (values or {}):
        entry["expires"] = 0
    guests[login] = entry
    write_json(GUESTS_FILE, guests)
    history(f"limites de {login} modifiées")
    for warning in warnings:
        say(f"attention : {warning}")
    say(f"limites enregistrées pour {login}")
    return warnings


def extend(login, days):
    guests = read_json(GUESTS_FILE, {})
    if login not in guests:
        raise ctl.SeedboxError("ce compte n'est pas un compte invité")
    days = int(days)
    if not 0 <= days <= 365:
        raise ctl.SeedboxError("prolongation : 0 à 365 jours (0 : sans échéance)")
    guests[login]["expires"] = int(time.time()) + days * 86400 if days else 0
    write_json(GUESTS_FILE, guests)
    history(f"échéance de {login} : {'supprimée' if not days else f'dans {days} jours'}")
    say(f"échéance de {login} mise à jour")


def expire(now=None):
    """Supprime les comptes invités arrivés à échéance. Appelée toutes les heures."""
    now = now or time.time()
    guests = read_json(GUESTS_FILE, {})
    users = ctl.users_load()
    removed, changed = [], False
    for login, entry in list(guests.items()):
        if login not in users:
            del guests[login]
            changed = True
            continue
        if entry.get("expires") and entry["expires"] < now:
            try:
                ctl.user_del(login)
            except (ctl.SeedboxError, OSError, ValueError) as error:
                say(f"échéance de {login} : suppression impossible ({error}), nouvel essai à la prochaine passe")
                continue
            del guests[login]
            changed = True
            removed.append(login)
            history(f"compte {login} supprimé à l'échéance")
            say(f"compte invité arrivé à échéance, supprimé : {login}")
    if changed:
        write_json(GUESTS_FILE, guests)
    if removed:
        try:
            ctl.alerts().notify("Comptes invités arrivés à échéance", "Supprimés : " + ", ".join(removed), "ok")
        except Exception:                                     # noqa: BLE001
            pass
    return removed


def overview():
    return {"invites": listing(), "accounts": guest_list(), "defaults": PROFILE_DEFAULTS, "limits": PROFILE_LIMITS,
            "history": [line for line in (HISTORY_FILE.read_text(errors="replace").splitlines()[-15:] if HISTORY_FILE.exists() else [])]}
