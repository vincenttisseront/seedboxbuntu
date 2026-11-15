"""SeedBoxBuntu - accès de secours : retrouver la main si le seul administrateur est enfermé dehors.

Cas visé : mot de passe perdu ET seconde étape (application TOTP, clé de sécurité) perdue. Sans cela, il ne reste que
l'édition manuelle des fichiers d'Authelia.

Principe
  - Un code de secours est généré (24 caractères, 120 bits, lisibles : XXXX-XXXX-XXXX-XXXX-XXXX-XXXX), affiché UNE seule fois,
    à imprimer et ranger sous clé. Seul son condensé (PBKDF2-SHA256, sel propre) est conservé : /etc/seedboxbuntu/emergency.json (0600).
  - Il s'utilise en LOCAL seulement : « seedboxctl emergency use LOGIN » (console de la machine, SSH, menu texte). Aucune
    route web n'accepte le code : si la console est inaccessible, il n'y a de toute façon personne pour l'y saisir.
  - Il ne sert qu'à un compte administrateur existant : il génère un nouveau mot de passe (affiché une fois), retire les
    secondes étapes enregistrées (le compte devra les enregistrer de nouveau) et ne crée ni n'élève aucun compte.
  - Usage unique : après un succès, le code est remplacé par un nouveau, à noter.
  - Freinage : 5 codes faux de suite verrouillent l'accès de secours 15 minutes ; chaque essai, réussi ou non, entre dans le
    journal d'audit et un échec répété ou une réussite déclenche une alerte.
Limite assumée : le code vaut un mot de passe administrateur. Imprimé et rangé sous clé, il est plus sûr qu'un fichier
oublié sur le bureau ; photographié ou laissé dans un mail, il devient une faiblesse. Il faut un accès à la machine (console
ou SSH) pour l'utiliser : c'est voulu.
    seedboxctl emergency show | init | use LOGIN [--keep-2fa]   (le code est lu sur l'entrée standard)
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time

import seedboxctl as ctl

CONFIG_FILE = ctl.ETC / "emergency.json"
STATE_FILE = ctl.STATE / "emergency-state.json"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"          # Crockford : sans I, L, O, U
LENGTH = 24
ITERATIONS = 600_000
MAX_FAILS = 5
LOCK_SECONDS = 900


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def new_code():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, LENGTH, 4))


def normalize(text):
    """Saisie tolérante : minuscules, tirets, espaces ; O lu comme 0, I et L lus comme 1."""
    raw = "".join(ch for ch in str(text).upper() if ch not in "- \t\r\n")
    return raw.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def digest(code, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", normalize(code).encode(), salt, iterations).hex()


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(value, indent=1))
    os.replace(temp, path)


def audit(action, detail, outcome="ok"):
    try:
        ctl.audit().record("secours", "local", action, detail, "système", outcome)
    except Exception:  # noqa: BLE001 - le journal ne doit jamais empêcher une récupération
        pass


def alert(title, message, level="warning"):
    try:
        ctl.alerts().notify(title, message, level)
    except Exception:  # noqa: BLE001
        pass


def init(by="console", iterations=ITERATIONS, now=None):
    """Génère (ou remplace) le code de secours et le renvoie : c'est la seule fois où il est visible."""
    now = int(time.time() if now is None else now)
    code = new_code()
    salt = os.urandom(16)
    previous = read_json(CONFIG_FILE)
    write_json(CONFIG_FILE, {"salt": salt.hex(), "iterations": iterations, "hash": digest(code, salt, iterations), "created": now,
                             "by": str(by)[:30], "last_used": previous.get("last_used", 0)})
    audit("Code de secours généré" if not previous else "Code de secours remplacé", f"par {str(by)[:30]}")
    ctl.notifications().record("security", "Code de secours " + ("remplacé" if previous else "généré"), "À imprimer et ranger sous clé.", "info", "emergency")
    say("code de secours généré : notez-le maintenant, il ne sera plus jamais affiché")
    return code


def status(now=None):
    now = time.time() if now is None else now
    config, state = read_json(CONFIG_FILE), read_json(STATE_FILE)
    locked = max(0, int(state.get("locked_until", 0) - now))
    return {"configured": bool(config.get("hash")), "created": config.get("created", 0), "by": config.get("by", ""),
            "last_used": config.get("last_used", 0), "locked_seconds": locked, "fails": state.get("fails", 0),
            "max_fails": MAX_FAILS, "lock_minutes": LOCK_SECONDS // 60}


def check_code(code, now=None, sleep=time.sleep):
    """Vérifie le code avec freinage. Lève SeedboxError s'il est faux ou si l'accès est verrouillé."""
    now = time.time() if now is None else now
    config, state = read_json(CONFIG_FILE), read_json(STATE_FILE)
    if not config.get("hash"):
        raise ctl.SeedboxError("aucun code de secours : générez-en un dans la console (Sécurité > Accès de secours) ou avec « seedboxctl emergency init »")
    remaining = int(state.get("locked_until", 0) - now)
    if remaining > 0:
        audit("Accès de secours refusé", "verrouillé", "refusé")
        raise ctl.SeedboxError(f"accès de secours verrouillé encore {max(1, remaining // 60 + 1)} minute(s) après trop d'essais")
    expected = digest(code, bytes.fromhex(config["salt"]), int(config.get("iterations", ITERATIONS)))
    if hmac.compare_digest(expected, config["hash"]):
        return config
    fails = int(state.get("fails", 0)) + 1
    sleep(1)
    if fails >= MAX_FAILS:
        write_json(STATE_FILE, {"fails": 0, "locked_until": int(now) + LOCK_SECONDS})
        audit("Accès de secours verrouillé", f"{MAX_FAILS} codes faux de suite", "refusé")
        alert("Accès de secours : essais répétés", f"{MAX_FAILS} codes de secours faux de suite : l'accès est verrouillé {LOCK_SECONDS // 60} minutes. "
                                                    "Si ce n'est pas vous, quelqu'un a un accès à la machine.", "warning")
        raise ctl.SeedboxError(f"code incorrect : accès de secours verrouillé {LOCK_SECONDS // 60} minutes")
    write_json(STATE_FILE, {"fails": fails, "locked_until": 0})
    audit("Accès de secours : code faux", f"essai {fails}/{MAX_FAILS}", "refusé")
    raise ctl.SeedboxError(f"code incorrect ({MAX_FAILS - fails} essai(s) avant verrouillage)")


def reset_second_factor(login, directory=None):
    """Retire les secondes étapes d'un compte dans Authelia. Renvoie leur nombre."""
    removed = 0
    path = (directory or ctl.CONFIG / "authelia") / "db.sqlite3"
    try:
        with sqlite3.connect(path, timeout=15) as con:
            for table in ("totp_configurations", "webauthn_credentials"):
                try:
                    removed += con.execute(f"DELETE FROM {table} WHERE username = ?", (login,)).rowcount
                except sqlite3.Error:
                    pass
    except sqlite3.Error as error:
        raise ctl.SeedboxError(f"base d'Authelia illisible : {error}")
    return removed


def set_password(login, password):
    """Mot de passe sur toutes les applications ; à défaut (conteneurs arrêtés), au moins dans Authelia qui commande l'accès."""
    try:
        return ctl.user_passwd(login, password) or []
    except Exception as error:  # noqa: BLE001 - Docker arrêté, Jellyfin absent : l'accès à la console prime
        users = ctl.users_load()
        users[login]["password"] = ctl.authelia_hash(password)
        ctl.users_save(users)
        return [f"seules les connexions web (Authelia) ont changé : {str(error)[:120]}"]


def use(login, code, keep_second_factor=False, now=None, sleep=time.sleep):
    """Réinitialise un administrateur. Renvoie {login, password, new_code, removed, warnings}."""
    users = ctl.users_load()
    config = check_code(code, now, sleep)
    if login not in users or "admins" not in users[login].get("groups", []):
        audit("Accès de secours refusé", f"compte {str(login)[:31]} : pas un administrateur", "refusé")
        raise ctl.SeedboxError("le code de secours ne sert qu'à un compte administrateur existant")
    password = secrets.token_urlsafe(15)
    warnings = set_password(login, password)
    removed = 0
    if not keep_second_factor:
        removed = reset_second_factor(login)
    config.update(last_used=int(time.time() if now is None else now))
    write_json(CONFIG_FILE, config)
    write_json(STATE_FILE, {"fails": 0, "locked_until": 0})
    new = init("secours")                                       # usage unique : le code utilisé est remplacé
    audit("Accès de secours utilisé", f"compte={login}, secondes étapes retirées={removed}")
    ctl.notifications().record("security", "Accès de secours utilisé", f"compte {login}", "warning", "emergency")
    alert("Accès de secours utilisé", f"Le code de secours a servi à réinitialiser le compte {login} (mot de passe"
                                       f"{'' if keep_second_factor else ' et seconde étape'}). Si ce n'est pas vous, changez les mots de passe et ouvrez le journal d'audit.", "warning")
    say(f"compte {login} réinitialisé")
    return {"login": login, "password": password, "new_code": new, "removed": removed, "warnings": warnings}
