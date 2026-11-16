"""SeedBoxBuntu - mots de passe propres aux applications (Identifiants) : réinitialisation depuis la console.

Les comptes de la page Comptes (connexion unique, Jellyfin, Seerr, p2p-aggregator) se changent d'un seul geste là-bas. Ici, les
applications qui ont leur propre mot de passe, y compris quand celui-ci a été perdu ou changé hors de la console :
  - Jellyfin : n'importe quel compte, par la clé d'API de la console (aucun mot de passe actuel nécessaire). Saisi par vous,
    jamais conservé.
  - qBittorrent : par son API ; si le mot de passe connu de la console est périmé, par le fichier de configuration (conteneur
    arrêté puis relancé). Vide = généré ; conservé pour relier Radarr, Sonarr, autobrr..., affiché dans Identifiants.
  - autobrr, Audiobookshelf : par l'API de l'application avec le mot de passe que connaît la console (si l'application refuse
    celui-ci, il a été changé dans son interface : réinitialisation impossible d'ici, le message le dit).
Chaque réinitialisation est vérifiée par une vraie connexion avec le nouveau mot de passe.
    seedboxctl apppass show | reset APP [--login COMPTE]   (nouveau mot de passe sur l'entrée standard, vide = généré)
"""
import time

import seedboxctl as ctl

APPS = {
    "jellyfin": "Jellyfin",
    "qbittorrent": "qBittorrent",
    "autobrr": "autobrr",
    "audiobookshelf": "Audiobookshelf",
}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def installed_catalog():
    try:
        return {e["id"]: e for e in ctl.apps().installed_entries()}
    except (OSError, ValueError, KeyError):
        return {}


def jellyfin_accounts():
    try:
        return sorted(u["Name"] for u in ctl.jellyfin_api("GET", "/Users").json() or [])
    except (ctl.SeedboxError, OSError, ValueError, KeyError):
        return []


def overview():
    present = installed_catalog()
    rows = []
    for ident, name in APPS.items():
        if ident == "audiobookshelf" and ident not in present:
            continue
        running = ctl.container_running(ident)
        row = {"id": ident, "name": name, "running": running, "typed": ident == "jellyfin"}
        if ident == "jellyfin":
            row["accounts"] = jellyfin_accounts() if running else []
        else:
            row["accounts"] = [{"qbittorrent": ctl.env_read().get("QBIT_USERNAME") or "admin"}.get(ident, "admin")]
        rows.append(row)
    return {"apps": rows}


def choose(password, typed):
    if password:
        ctl.validate_account("compte", password)
        return password
    if typed:
        raise ctl.SeedboxError("saisissez le nouveau mot de passe (8 caractères au minimum)")
    return ctl.hex_secret(16)


def reset_jellyfin(login, password):
    user = ctl.jellyfin_user(login) if login else None
    if not user:
        raise ctl.SeedboxError(f"compte Jellyfin « {login} » inconnu")
    result = ctl.jellyfin_api("POST", f"/Users/{user['Id']}/Password", {"NewPw": password, "ResetPassword": False})
    if not result.ok:
        raise ctl.SeedboxError(f"Jellyfin : changement refusé (HTTP {result.status})")
    check = ctl.jellyfin_api("POST", "/Users/AuthenticateByName", {"Username": user["Name"], "Pw": password}, token="-")
    if not check.ok:
        raise ctl.SeedboxError(f"Jellyfin : le nouveau mot de passe n'ouvre pas la session (HTTP {check.status})")
    say(f"Jellyfin : mot de passe de {user['Name']} changé et vérifié")
    return []


def reset_qbittorrent(password):
    ctl.qbit_set_password(password)
    ctl.compose("up", "-d", capture=True)
    for _ in range(20):
        try:
            ctl.qbit_login()
            break
        except (ctl.SeedboxError, OSError, ValueError):
            time.sleep(3)
    else:
        raise ctl.SeedboxError("qBittorrent : le nouveau mot de passe n'ouvre pas la session")
    say("qBittorrent : mot de passe changé et vérifié ; Radarr, Sonarr, autobrr... reliés à nouveau")
    return ctl.provision()


def reset_autobrr(password):
    try:
        ctl.autobrr_set_password(password)
    except (ctl.SeedboxError, OSError, ValueError) as error:
        raise ctl.SeedboxError(f"autobrr : mot de passe actuel inconnu de la console (changé dans l'interface ?) : {error}")
    ctl.autobrr_session()
    say("autobrr : mot de passe changé et vérifié")
    return []


def reset_audiobookshelf(password):
    entry = installed_catalog().get("audiobookshelf")
    if not entry:
        raise ctl.SeedboxError("Audiobookshelf n'est pas installé")
    base = ctl.svc("audiobookshelf", 80) + entry["path"]
    env = ctl.env_read()
    user, current = ctl.apps().admin_login(entry, env)
    login = ctl.request("POST", base + "/login", body={"username": user, "password": current}, timeout=30)
    if not login.ok:
        raise ctl.SeedboxError("Audiobookshelf : mot de passe actuel inconnu de la console (changé dans l'interface ?)")
    data = login.json()["user"]
    result = ctl.request("PATCH", base + f"/api/users/{data['id']}", headers={"Authorization": f"Bearer {data['token']}"},
                         body={"password": password}, timeout=30)
    if not result.ok:
        raise ctl.SeedboxError(f"Audiobookshelf : changement refusé (HTTP {result.status})")
    key = f"{entry['id'].upper().replace('-', '_')}_ADMIN_PASSWORD"
    ctl.env_set(**{key: password})
    check = ctl.request("POST", base + "/login", body={"username": user, "password": password}, timeout=30)
    if not check.ok:
        raise ctl.SeedboxError("Audiobookshelf : le nouveau mot de passe n'ouvre pas la session")
    say("Audiobookshelf : mot de passe changé et vérifié")
    return []


def reset(app, login="", password=""):
    if app not in APPS:
        raise ctl.SeedboxError("application inconnue : " + ", ".join(APPS))
    password = choose(password, app == "jellyfin")
    if app == "jellyfin":
        return reset_jellyfin(login, password)
    if app == "qbittorrent":
        return reset_qbittorrent(password)
    if app == "autobrr":
        return reset_autobrr(password)
    return reset_audiobookshelf(password)
