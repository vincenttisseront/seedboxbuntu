#!/usr/bin/env python3
"""seedboxctl - pilotage de l'appliance SeedBoxBuntu.

Module utilisé par la ligne de commande, la console web et le menu texte. Toutes les
opérations sont idempotentes : « provision » rapproche l'état réel des applications de
/opt/seedbox/.env, et peut être relancé à tout moment.

  seedboxctl deploy                     déploiement complet (premier démarrage)
  seedboxctl provision                  relie les applications entre elles
  seedboxctl status | credentials       état, identifiants
  seedboxctl user list|add|passwd|del   comptes (Authelia, Jellyfin, Seerr)
  seedboxctl domain DOMAINE [--email E] nom personnalisé (vide : adresse IP du serveur)
  seedboxctl vpn direct|set|status      qBittorrent sans VPN (par défaut) ou dans un tunnel VPN
  seedboxctl check [--json]             contrôle de santé : API et liaisons entre applications
  seedboxctl rotate SERVICE             renouvelle un mot de passe ou une clé de service
  seedboxctl apppass show|reset APP     réinitialise le mot de passe d'une application (Jellyfin, qBittorrent...)
  seedboxctl trackers list|sync|rule    trackers et règles de seed
  seedboxctl github token [JETON]       jeton de lecture GitHub (dépôt privé de l'appliance)
  seedboxctl share list|add|role|...    partages réseau (SMB, NFS) et disques externes
  seedboxctl import scan|show|run|clear    importer une autre seedbox
  seedboxctl audit show|sync|verify|export|logs   journal d'audit, recherche dans les journaux
  seedboxctl guests list|create|revoke|profile|extend|expire   invitations par lien, limites, comptes temporaires
  seedboxctl gpu show|set|apply|test   transcodage matériel de Jellyfin (GPU)
  seedboxctl cleanup scan|show|trash|restore|empty   assistant de nettoyage avec corbeille
  seedboxctl metrics collect|show|forecast   historique des ressources, prévision de saturation des disques
  seedboxctl report show|build|send|set|auto  rapport hebdomadaire (canaux d'alerte)
  seedboxctl assistant show                  liste de contrôle de la configuration
  seedboxctl offsite show|set|connection|push|restore-test   sauvegarde hors du serveur (rclone)
  seedboxctl appliance status|check|apply|rollback|source|auto|signer   mise à jour de l'appliance depuis GitHub (sans ISO)
  seedboxctl resources show|set              ressources et dérives de chaque conteneur
  seedboxctl backup check [nom]              vérification à blanc d'une sauvegarde (bases, fichiers, ce qui serait remplacé)
  seedboxctl window show|set|tick            fenêtre de maintenance des tâches automatiques lourdes
  seedboxctl activity show|set               activité par personne (facultatif)
  seedboxctl vulnscan show|set|scan|auto     failles connues dans les images Docker (Trivy)
  seedboxctl netcheck                        diagnostic réseau
  seedboxctl vacation show|start|stop|tick   mode vacances
  seedboxctl push show|test                    notifications push dans le navigateur
  seedboxctl config export|import            export et import des réglages de la console
  seedboxctl smarthist show|sample           santé des disques dans le temps (SMART)
  seedboxctl bandwidth show|tick             débit de qBittorrent par plages horaires
  seedboxctl ratiogoals show|set|unset       objectifs de ratio par tracker
  seedboxctl rollout plan|run                mise à jour des applications par lots, avec test de santé
  seedboxctl backupdiff list|diff            comparaison de deux sauvegardes (secrets masqués)
  seedboxctl diagnose list|run SERVICE       assistant de dépannage d'un service
  seedboxctl emergency show|init|use         accès de secours (code imprimé, usage unique, local)
  seedboxctl catalogexport show|run|plan|apply   liste de la médiathèque : export et réimport
  seedboxctl myspace show LOGIN              ce que voit un compte dans « Mon espace »
  seedboxctl remote-control show|pause|resume    télécommande : pause générale des torrents
  seedboxctl changelog show|undo ID|tick     journal des réglages : qui a changé quoi, retour en arrière
  seedboxctl restoredrill show|run [NOM]     simulation de restauration d'une sauvegarde dans des conteneurs jetables
  seedboxctl diskusage show|scan             espace par dossier et par application, évolution
  seedboxctl rotation show|run ELEMENT       renouvellement guidé d'un secret interne, avec retour en cas d'échec
  seedboxctl mediaquality show|upgrade radarr|sonarr [N]   qualité des médias (sous le seuil, manquants, doublons)
  seedboxctl linkcheck                       contrôle des liens entre les applications de la pile
  seedboxctl peoplereport show|set|send      bilan du mois par personne
  seedboxctl titleshare list|create|revoke|tick   partager un seul titre, pour une durée limitée
  seedboxctl libscan show|scan                relevé des bibliothèques Radarr et Sonarr, par partage
  seedboxctl libmap show                      carte des bibliothèques et versions multiples
  seedboxctl copyaudit show|scan|relink [--all]  copies cachées entre téléchargements et médiathèque
  seedboxctl replacer show|search|apply|...   remplacer un média par une meilleure version, sous budget d'espace
  seedboxctl mediaslim show|add|run|...       recompression x265 et retrait des pistes inutiles
  seedboxctl shareprofile show|set|apply      un profil de qualité par partage
  seedboxctl relocate show|move               déplacer les titres inactifs entre partages
  seedboxctl seedopt show|rule|retire|...     optimisation des seeds, analyse par tracker
  seedboxctl seeddiag show|apply ID           pourquoi ça n'envoie pas : diagnostic et réglages de qBittorrent
  seedboxctl llm show|set|test|explain        connecteur vers un serveur Ollama externe
  seedboxctl network show|set|confirm|revert  réseau de l'appliance (IP fixe ou DHCP, passerelle, DNS)
  seedboxctl spacefore show|sample|set        prévision de saturation des volumes
  seedboxctl libreport show|send|set          rapport hebdomadaire des bibliothèques
  seedboxctl dormant show|scan|unmonitor|set  titres jamais lus et séries abandonnées
  seedboxctl integrity show|run|research|set  contrôle d'intégrité des fichiers (ffprobe)
  seedboxctl renamer show|scan|apply          normalisation des noms par Radarr et Sonarr
  seedboxctl subsgap show|set                 titres sans sous-titres dans les langues voulues
  seedboxctl guestquota show|default|quota|...  quotas de lecture par compte invité
  seedboxctl energy show|sample|standby|set   veille des disques et coût par To
  seedboxctl aggregator show|sync|sources|add|config|send   catalogue des releases par œuvre (Prowlarr, Jackett, Torznab)
  seedboxctl bans show|unban|ban|ignore|set      fail2ban : adresses bannies, adresses ignorées, seuils
  seedboxctl space show|scan|fix                 où est passé l'espace disque, et correctifs sûrs
  seedboxctl subdomains show|check|set|off|rename|enable|disable   chaque application sur son sous-domaine (radarr.exemple.fr)
  seedboxctl vpnwatch show|set|tick          surveillance du VPN (fuite, changement d'adresse)
  seedboxctl house                           état d'ensemble pour l'écran « maison »
"""
import argparse
import base64
import fcntl
import getpass
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import string
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

BASE = Path(os.environ.get("SB_BASE", "/opt/seedbox"))
STORAGE = Path(os.environ.get("SB_STORAGE", "/mnt/storage"))
SHARE = Path(os.environ.get("SB_SHARE", "/usr/share/seedboxbuntu"))
ETC = Path(os.environ.get("SB_ETC", "/etc/seedboxbuntu"))
STATE = Path(os.environ.get("SB_STATE", "/var/lib/seedboxbuntu"))
ENV_FILE = BASE / ".env"
CONFIG = BASE / "config"
APPS_DIR = BASE / "apps"
TRACKERS_FILE = CONFIG / "trackers.json"
USERS_FILE = CONFIG / "authelia" / "users_database.yml"
TOKEN_FILE = ETC / "github-token"
# Mot de passe choisi dans l'assistant de premier démarrage : lu par le déploiement, puis effacé
PASSWORD_FILE = ETC / "initial-password"
CONFIGURED_FLAG = STATE / "configured"
LOCK_FILE = STATE / "seedboxctl.lock"
# Anciennes applications retirées : leurs conteneurs, images et sources sont supprimés à la mise à jour (jamais leurs données)
RETIRED_CONTAINERS = ("p2p-aggregator", "tracker-session")
RETIRED_IMAGES = ("seedbox/p2p-aggregator", "seedbox/tracker-session")
TRAEFIK_IP = "172.30.0.2"

ROLES = ("admin", "manager", "user", "observer")
# Groupe Authelia de chaque rôle. « manager » : Radarr, Sonarr, Prowlarr, qBittorrent et les applications
# reliées du catalogue, sans la console, Traefik, Dockge, Jackett ni autobrr. « observer » : comme un utilisateur, plus la
# console en lecture seule (vue d'ensemble, applications, système, alertes, rapport) ; jamais d'action ni de secret.
ROLE_GROUPS = {"admin": "admins", "manager": "managers", "user": "users", "observer": "observers"}
# Droits Seerr : ADMIN = 2 ; gestionnaire = MANAGE_REQUESTS (16) + REQUEST (32) + AUTO_APPROVE (128) ; utilisateur = REQUEST (32)
SEERR_PERMISSIONS = {"admin": 2, "manager": 16 | 32 | 128, "user": 32, "observer": 32}
VALID_LOGIN = re.compile(r"^[a-z][a-z0-9_.-]{1,30}$")
MIN_PASSWORD = 8

# Applications présentées par la console : chemin public (ou port HTTPS dédié pour celles
# qui ne fonctionnent qu'à la racine d'une adresse), service Docker, type de connexion
APPS = [
    {"id": "homepage", "name": "Tableau de bord", "path": "/", "service": "homepage", "login": "sso"},
    {"id": "jellyfin", "name": "Jellyfin", "path": "/jellyfin/", "service": "jellyfin", "login": "comptes"},
    {"id": "seerr", "name": "Seerr", "port": 5055, "service": "seerr", "login": "comptes"},
    {"id": "qbittorrent", "name": "qBittorrent", "path": "/qbittorrent/", "service": "qbittorrent", "login": "sso"},
    {"id": "prowlarr", "name": "Prowlarr", "path": "/prowlarr/", "service": "prowlarr", "login": "sso"},
    {"id": "jackett", "name": "Jackett", "path": "/jackett/", "service": "jackett", "login": "sso"},
    {"id": "radarr", "name": "Radarr", "path": "/radarr/", "service": "radarr", "login": "sso"},
    {"id": "sonarr", "name": "Sonarr", "path": "/sonarr/", "service": "sonarr", "login": "sso"},
    {"id": "autobrr", "name": "autobrr", "path": "/autobrr/", "service": "autobrr", "login": "sso"},
    {"id": "qbm", "name": "qbit_manage", "path": "/qbm/", "service": "qbit-manage", "login": "sso"},
    {"id": "dockge", "name": "Dockge", "port": 5001, "service": "dockge", "login": "sso"},
    {"id": "traefik", "name": "Traefik", "path": "/traefik/dashboard/", "service": "traefik", "login": "sso"},
    {"id": "authelia", "name": "Authelia", "path": "/auth/", "service": "authelia", "login": "public"},
]
# Chemin de base des applications servies dans un sous-chemin : leurs API internes le portent aussi
BASE_PATHS = {"radarr": "/radarr", "sonarr": "/sonarr", "prowlarr": "/prowlarr", "jellyfin": "/jellyfin", "jackett": "/jackett", "lidarr": "/lidarr",
              "autobrr": "/autobrr"}
SEERR_PORT = 5055


class SeedboxError(Exception):
    """Erreur destinée à l'utilisateur."""


def profiles_read():
    return set(filter(None, env_read().get("COMPOSE_PROFILES", "").split(",")))


def qbit_mode():
    """« vpn » : qBittorrent dans le tunnel de gluetun ; « direct » : sans VPN (accès local)."""
    profiles = profiles_read()
    return "vpn" if "vpn" in profiles else "direct" if "direct" in profiles else None


def qbit_host():
    """Nom Docker sous lequel les autres applications joignent qBittorrent (port 8080)."""
    return "gluetun" if qbit_mode() == "vpn" else "qbittorrent"


def qbit_service():
    return "qbittorrent" if qbit_mode() == "vpn" else "qbittorrent-direct"


def apps():
    """Catalogue d'applications installables, chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import apps as module
    return module


def all_apps():
    """Applications de la stack et applications ajoutées depuis le catalogue."""
    try:
        extra = [{"id": e["id"], "name": e["name"], "path": e["path"] + "/", "service": e["id"],
                  "login": "sso" if e["auth"] == "sso" else "comptes"} for e in apps().installed_entries() if not e.get("headless")]
    except (OSError, ValueError, KeyError):
        extra = []
    return APPS + extra


def health():
    """Contrôle de santé, chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import health as module
    return module


def maintenance():
    """Mises à jour planifiées et durcissement, chargés à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import maintenance as module
    return module


def remote():
    """Accès distant (adresses, Tailscale, Let's Encrypt par DNS), chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import remote as module
    return module


def sysinfo():
    """Ressources, disques et journaux, chargés à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import system as module
    return module


def appliance():
    """Mise à jour de l'appliance depuis GitHub, chargée à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import appliance as module
    return module


def alerts():
    """Alertes, chargées à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import alerts as module
    return module


def versions():
    """Versions des services, mise à jour et retour arrière, chargées à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import versions as module
    return module


def backup():
    """Sauvegarde et restauration, chargées à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import backup as module
    return module


def audit():
    """Journal d'audit et recherche dans les journaux, chargés à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import audit as module
    return module


def importer():
    """Import depuis une autre seedbox, chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import importer as module
    return module


def guests():
    """Invités : invitations, limites, comptes temporaires, chargés à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import guests as module
    return module


def gpu():
    """Transcodage matériel de Jellyfin, chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import gpu as module
    return module


def cleanup():
    """Assistant de nettoyage, chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import cleanup as module
    return module


def _lazy(name):
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    return __import__(name)


def resources():
    """Ressources par conteneur et dérives, chargées à la demande."""
    return _lazy("resources")


def texts():
    """Langue des messages envoyés."""
    return _lazy("texts")


def vulnscan():
    return _lazy("vulnscan")


def maintwindow():
    return _lazy("maintwindow")


def netcheck():
    return _lazy("netcheck")


def vacation():
    return _lazy("vacation")


def webpush():
    return _lazy("webpush")


def activity():
    return _lazy("activity")


def configbundle():
    return _lazy("configbundle")


def smarthist():
    return _lazy("smarthist")


def ratiogoals():
    return _lazy("ratiogoals")


def bandwidth():
    return _lazy("bandwidth")


def catalogexport():
    return _lazy("catalogexport")


def rollout():
    return _lazy("rollout")


def changelog():
    return _lazy("changelog")


def restoredrill():
    return _lazy("restoredrill")


def diskusage():
    return _lazy("diskusage")


def rotation():
    return _lazy("rotation")


def apppasswords():
    return _lazy("apppasswords")


def aggregator():
    return _lazy("aggregator")


def bans():
    return _lazy("bans")


def subdomains():
    return _lazy("subdomains")


def space():
    return _lazy("space")


def mediaquality():
    return _lazy("mediaquality")


def linkcheck():
    return _lazy("linkcheck")


def peoplereport():
    return _lazy("peoplereport")


def titleshare():
    return _lazy("titleshare")


def vpnwatch():
    return _lazy("vpnwatch")


def house():
    return _lazy("house")


def libscan():
    return _lazy("libscan")


def libmap():
    return _lazy("libmap")


def copyaudit():
    return _lazy("copyaudit")


def replacer():
    return _lazy("replacer")


def mediaslim():
    return _lazy("mediaslim")


def shareprofile():
    return _lazy("shareprofile")


def relocate():
    return _lazy("relocate")


def seedopt():
    return _lazy("seedopt")


def seeddiag():
    return _lazy("seeddiag")


def repoint():
    return _lazy("repoint")


def llm():
    return _lazy("llm")


def spacefore():
    return _lazy("spacefore")


def netconf():
    return _lazy("netconf")


def libreport():
    return _lazy("libreport")


def dormant():
    return _lazy("dormant")


def integrity():
    return _lazy("integrity")


def renamer():
    return _lazy("renamer")


def subsgap():
    return _lazy("subsgap")


def guestquota():
    return _lazy("guestquota")


def energy():
    return _lazy("energy")


def backupdiff():
    return _lazy("backupdiff")


def diagnose():
    return _lazy("diagnose")


def emergency():
    return _lazy("emergency")


def myspace():
    return _lazy("myspace")


def telecommande():
    return _lazy("telecommande")


def sandbox():
    return _lazy("sandbox")


def weekly_report():
    """Rapport hebdomadaire, chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import report as module
    return module


def assistant():
    """Assistant de première installation (liste de contrôle), chargé à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import assistant as module
    return module


def notifications():
    """Journal d'événements et préférences d'interface, chargés à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import notifications as module
    return module


def metrics():
    """Historique des ressources et prévision, chargés à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import metrics as module
    return module


def offsite():
    """Sauvegarde hors serveur (rclone), chargée à la demande."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import offsite as module
    return module


def shares():
    """Module des partages, chargé à la demande (il importe lui-même ce module)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:  # python3 -I n'ajoute pas le dossier du script
        sys.path.insert(0, here)
    import shares as module
    return module


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# ----------------------------------------------------------------------------
#  Fichier .env
# ----------------------------------------------------------------------------
def env_read():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2)
    return values


def env_set(**updates):
    lines = ENV_FILE.read_text().splitlines()
    pending = dict(updates)
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match and match.group(1) in pending:
            lines[index] = f"{match.group(1)}={pending.pop(match.group(1))}"
    lines.extend(f"{key}={value}" for key, value in pending.items())
    temp = ENV_FILE.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    env = env_read()
    os.chown(temp, int(env.get("PUID", 1000)), int(env.get("PGID", 1000)))
    os.replace(temp, ENV_FILE)


def check_env_value(label, value):
    """Une valeur saisie ne doit pas pouvoir ajouter une ligne ou une substitution à .env."""
    if re.search(r"[\s\"'`$\\#]", value.replace(" ", "x") if label == "pays" else value):
        raise SeedboxError(f"{label} : caractères non autorisés")
    return value


def hex_secret(size=16):
    return secrets.token_hex(size)


def human_password(size=20):
    # Sans caractères ambigus : ce mot de passe peut être recopié depuis un écran
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "O0Il1")
    return "".join(secrets.choice(alphabet) for _ in range(size))


def generate_secrets():
    env = env_read()
    sizes = {
        "AUTHELIA_SESSION_SECRET": 32, "AUTHELIA_STORAGE_KEY": 32, "AUTHELIA_JWT_SECRET": 32, "AUTHELIA_REDIS_PASSWORD": 24,
        "AUTOBRR_SESSION_SECRET": 16, "AUTOBRR_ADMIN_PASSWORD": 16, "CONSOLE_PROXY_SECRET": 32,
        "QBIT_PASSWORD": 16,
        "RADARR_API_KEY": 16, "SONARR_API_KEY": 16, "PROWLARR_API_KEY": 16, "JACKETT_API_KEY": 16,
    }
    updates = {k: hex_secret(v) for k, v in sizes.items() if env.get(k) == "__AUTO__"}
    if env.get("ADMIN_INITIAL_PASSWORD") == "__AUTO__":
        updates["ADMIN_INITIAL_PASSWORD"] = human_password()
    if not env.get("CROSS_SEED_API_KEY"):
        updates["CROSS_SEED_API_KEY"] = hex_secret(24)
    if updates:
        env_set(**updates)


# ----------------------------------------------------------------------------
#  Commandes et HTTP
# ----------------------------------------------------------------------------
def run(*command, check=True, capture=False, cwd=None, env=None, stdin=None):
    result = subprocess.run(
        [str(part) for part in command], cwd=cwd, env=env, input=stdin, text=True,
        stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or "").strip()[-400:] if capture else ""
        raise SeedboxError(f"échec de « {' '.join(map(str, command[:4]))} » {detail}")
    return result


COMPOSE_FILES = ("docker-compose.yml", "docker-compose.override.yml", "docker-compose.apps.yml", "docker-compose.pins.yml",
                 "docker-compose.local.yml")


def sync_compose_files():
    """COMPOSE_FILE dans .env : Dockge (et tout « docker compose » lancé dans le dossier de la pile) voit
    ainsi les mêmes fichiers que seedboxctl. Sans cela, son « Déployer » retirerait les applications
    ajoutées depuis le catalogue, qu'il prendrait pour des conteneurs orphelins."""
    if not ENV_FILE.exists():
        return
    wanted = ":".join(name for name in COMPOSE_FILES if (BASE / name).exists())
    if env_read().get("COMPOSE_FILE") != wanted:
        env_set(COMPOSE_FILE=wanted)


def compose(*args, check=True, capture=False):
    files = ["-f", BASE / "docker-compose.yml"]
    # override : généré (GPU) ; apps : catalogue ; local : personnalisations de l'administrateur, jamais modifiées
    for name in COMPOSE_FILES[1:]:
        if (BASE / name).exists():
            files += ["-f", BASE / name]
    return run("docker", "compose", "--project-directory", BASE, *files, *args,
               check=check, capture=capture, cwd=BASE)


class Response:
    def __init__(self, status, text, headers):
        self.status, self.text, self.headers = status, text, headers

    def json(self):
        return json.loads(self.text) if self.text else None

    @property
    def ok(self):
        return 200 <= self.status < 300

    def cookies(self):
        return "; ".join(v.split(";", 1)[0] for k, v in self.headers if k.lower() == "set-cookie")


def request(method, url, headers=None, body=None, form=None, host=None, timeout=30):
    """Requête directe : ignore volontairement les variables de proxy de l'hôte."""
    parts = urllib.parse.urlsplit(url)
    headers = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        payload = urllib.parse.urlencode(form).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    if host:
        headers["Host"] = host
    if parts.scheme == "https":
        context = ssl._create_unverified_context()  # certificat de l'appliance, connexion locale
        connection = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout, context=context)
    else:
        connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8", "replace")
        return Response(response.status, text, response.getheaders())
    finally:
        connection.close()


def container_ip(name):
    result = run("docker", "inspect", "-f",
                 '{{with index .NetworkSettings.Networks "seedbox"}}{{.IPAddress}}{{end}}',
                 name, check=False, capture=True)
    return result.stdout.strip()


def svc(name, port):
    address = container_ip(name)
    if not address:
        raise SeedboxError(f"conteneur {name} introuvable sur le réseau seedbox")
    return f"http://{address}:{port}"


def wait_http(name, port, path="/", timeout=600):
    # Large : un premier démarrage sur un petit processeur ou un disque lent peut prendre plusieurs minutes
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            result = request("GET", svc(name, port) + path, timeout=5)
            if result.status < 500:
                return True
            last = f"HTTP {result.status}"
        except (OSError, SeedboxError, http.client.HTTPException) as error:
            last = str(error)
        time.sleep(3)
    raise SeedboxError(f"{name} ne répond pas ({last})")


def container_running(name):
    result = run("docker", "inspect", "-f", "{{.State.Running}}", name, check=False, capture=True)
    return result.stdout.strip() == "true"


# ----------------------------------------------------------------------------
#  Initialisation : arborescence, modèles, domaine, certificats
# ----------------------------------------------------------------------------
def primary_ip():
    """Adresse de sortie de l'hôte (le connect UDP n'envoie aucun paquet) ; sans passerelle,
    première adresse IPv4 globale."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("1.1.1.1", 53))
            return probe.getsockname()[0]
        except OSError:
            pass
    result = run("ip", "-4", "-o", "addr", "show", "scope", "global", check=False, capture=True)
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        if len(fields) > 3 and "/" in fields[3] and not fields[1].startswith(("docker", "br-", "veth")):
            return fields[3].split("/")[0]
    return "127.0.0.1"


def auto_address():
    """Adresse principale en mode automatique : l'IP du serveur, sans DNS à configurer."""
    return primary_ip()


def is_ip(value):
    try:
        socket.inet_aton(value)
        return value.count(".") == 3
    except OSError:
        return False


def machine_names():
    """Nom complet du serveur (ligne 127.0.1.1 de /etc/hosts, écrite par l'assistant) : seul un
    nom avec un point peut porter un cookie de session Authelia."""
    names = []
    try:
        for line in Path(os.environ.get("SB_HOSTS_FILE", "/etc/hosts")).read_text().splitlines():
            fields = line.split("#", 1)[0].split()
            if fields and fields[0] == "127.0.1.1":
                names += [n.lower() for n in fields[1:] if "." in n and valid_domain(n.lower())]
    except OSError:
        pass
    return names


def public_hosts(env=None):
    """Adresses servies : adresse principale, IP du serveur, nom complet de la machine."""
    env = env or env_read()
    hosts = []
    extra = [h for h in env.get("EXTRA_HOSTS", "").split(",") if h]
    for host in (env.get("DOMAIN", ""), primary_ip(), *machine_names(), *extra, env.get("TAILSCALE_DNSNAME", "")):
        host = host.strip().lower()
        if host and host not in hosts and host != "127.0.0.1" and (is_ip(host) or valid_domain(host)):
            hosts.append(host)
    return hosts


def app_url(app, host):
    return f"https://{host}:{app['port']}/" if "port" in app else f"https://{host}{app['path']}"


def ensure_certificates(env):
    """Certificat de l'appliance, signé par une autorité locale : IP, nom du serveur et domaine.
    Toujours présent, même avec Let's Encrypt : il sert les accès par l'adresse IP."""
    certs = CONFIG / "traefik" / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    (CONFIG / "traefik" / "acme").mkdir(exist_ok=True)
    ETC.mkdir(parents=True, exist_ok=True)
    hosts = [h for h in env.get("PUBLIC_HOSTS", "").split(",") if h] or [env["DOMAIN"]]
    ca_key, ca_crt = ETC / "ca.key", certs / "ca.crt"
    if not ca_key.exists() or not ca_crt.exists():
        say("création de l'autorité de certification locale")
        run("openssl", "req", "-x509", "-newkey", "rsa:4096", "-nodes", "-sha256", "-days", "3650",
            "-keyout", ca_key, "-out", ca_crt, "-subj", "/O=SeedBoxBuntu/CN=SeedBoxBuntu CA",
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            capture=True)
        ca_key.chmod(0o600)
    stamp = certs / "domain.txt"
    hosts = hosts + [n for n in subdomains().cert_names() if n not in hosts]   # *.domaine des applications
    wanted = ",".join(hosts)
    if stamp.exists() and stamp.read_text().strip() == wanted and (certs / "server.crt").exists():
        return
    say(f"certificat pour {', '.join(hosts)}")
    key, csr = certs / "server.key", certs / "server.csr"
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", csr,
        "-subj", f"/O=SeedBoxBuntu/CN={hosts[0]}", capture=True)
    extension = certs / "server.ext"
    names = ",".join(f"IP:{h}" if is_ip(h) else f"DNS:{h}" for h in hosts)
    extension.write_text(
        f"subjectAltName={names}\nextendedKeyUsage=serverAuth\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n")
    run("openssl", "x509", "-req", "-in", csr, "-CA", ca_crt, "-CAkey", ca_key, "-CAcreateserial",
        "-days", "825", "-sha256", "-extfile", extension, "-out", certs / "server.crt", capture=True)
    csr.unlink()
    extension.unlink()
    key.chmod(0o600)
    stamp.write_text(wanted + "\n")


def qbm_rules():
    rules = load_trackers()
    trackers = {"other": {"tag": "autre"}}
    limits = {}
    for index, (keyword, rule) in enumerate(sorted(rules.items()), start=1):
        tag = rule.get("tag") or keyword
        trackers[keyword] = {"tag": tag}
        limit = {
            "priority": index,
            "include_all_tags": [tag],
            # -1 : ne jamais arrêter le partage. Les seuils minimaux protègent des hit-and-run.
            "max_ratio": float(rule.get("max_ratio", -1)),
            "max_seeding_time": rule.get("max_seeding_time", -1),
            # Vérifié par qbit_manage quand le ratio maximal est atteint : avant ce temps minimal,
            # il lève la limite et relance le partage (protection contre les hit-and-run)
            "min_seeding_time": f"{int(rule.get('min_seed_hours', 0))}h",
            "resume_torrent_after_change": True,
            "cleanup": False,
        }
        limits[f"tracker_{tag}"] = limit
    limits["defaut"] = {"priority": 999, "max_ratio": -1, "max_seeding_time": -1, "cleanup": False}
    # Deux clés YAML de premier niveau ; leur valeur JSON est une syntaxe YAML valide
    return (f"tracker: {json.dumps(trackers, ensure_ascii=False)}\n"
            f"share_limits: {json.dumps(limits, ensure_ascii=False)}")


def template_values(env):
    values = dict(env)
    values["BASE"], values["STORAGE"] = str(BASE), str(STORAGE)
    acme = bool(env.get("ACME_EMAIL"))
    # Let's Encrypt pour le domaine personnalisé ; le certificat de l'appliance pour le reste (IP)
    sub = subdomains()
    sans = "".join(f"\n            - {json.dumps(n)}" for n in sub.acme_names(env))
    values["TRAEFIK_TLS"] = (f"\n        certResolver: le\n        domains:\n          - main: {env.get('DOMAIN', '')}"
                             + (f"\n            sans:{sans}" if sans else "")
                             if acme else "{}")
    values["TRAEFIK_ACME"] = (
        "\ncertificatesResolvers:\n  le:\n    acme:\n"
        f"      email: {env.get('ACME_EMAIL', '')}\n      storage: /acme/acme.json\n"
        + (f"      dnsChallenge:\n        provider: {env['ACME_DNS_PROVIDER']}\n        resolvers:\n          - 1.1.1.1:53\n          - 8.8.8.8:53\n"
           if env.get("ACME_DNS_PROVIDER") else "      httpChallenge:\n        entryPoint: web\n") if acme else "")
    values["TRAEFIK_DEFAULT_CERT"] = (
        "  stores:\n    default:\n      defaultCertificate:\n"
        "        certFile: /certs/server.crt\n        keyFile: /certs/server.key\n")
    hosts = [h for h in env.get("PUBLIC_HOSTS", "").split(",") if h] or [env.get("DOMAIN") or "127.0.0.1"]
    # Sous-domaines des applications : règles d'accès sur *.domaine, un cookie sur le domaine parent, noms connus de Traefik
    app_domain = sub.domain()
    app_hosts = sub.hosts(env)
    values["AUTH_DOMAINS"] = json.dumps(hosts + ([f"*.{app_domain}"] if app_domain else []))
    values["OTHER_HOSTS_RULE"] = " && ".join(f"!Host(`{h}`)" for h in [*hosts, *app_hosts])
    cookie_hosts = [(h, h) for h in sub.cookies(hosts)]
    if app_domain:
        cookie_hosts.append((app_domain, sub.portal(env)))
    values["AUTH_COOKIES"] = "\n".join(
        f"    - name: seedbox_session\n      domain: '{d}'\n      authelia_url: 'https://{h}/auth'\n"
        f"      default_redirection_url: 'https://{h}/'\n      expiration: 12h\n      inactivity: 2h" for d, h in cookie_hosts)
    mode = env.get("AUTH_2FA", "none")
    values["POLICY_ADMIN"] = "two_factor" if mode in ("admins", "all") else "one_factor"
    values["POLICY_USERS"] = "two_factor" if mode == "all" else "one_factor"
    values["QBM_RULES"] = qbm_rules()
    values["QBIT_HOST"] = qbit_host()
    catalog = apps()
    values["ADMIN_PATHS"] = "|".join(["console", "traefik", "dockge", "qbittorrent", "qbm", "autobrr", "prowlarr",
                                      "jackett", "radarr", "sonarr"] + catalog.admin_paths())
    values["MANAGER_PATHS"] = "|".join(["radarr", "sonarr", "prowlarr", "qbittorrent"] + catalog.manager_paths())
    values["EXTRA_SERVICES"] = catalog.homepage_cards()
    values["DOWNLOADS"] = shares().location("complete").rstrip("/")
    return values


def substitute(text, values):
    return re.sub(r"@@([A-Z0-9_]+)@@", lambda m: str(values.get(m.group(1), m.group(0))), text)


# Fichiers modifiés ensuite par leur application : écrits une seule fois
ONCE = {"qbittorrent/qBittorrent/qBittorrent.conf", "jellyfin/network.xml"}


DNS_ENV_FILE = CONFIG / "traefik" / "acme-dns.env"


def render_templates():
    env = env_read()
    (CONFIG / "redis").mkdir(parents=True, exist_ok=True)
    if not DNS_ENV_FILE.exists():   # fichier lu par le conteneur Traefik (env_file) : vide si pas de défi DNS
        DNS_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        DNS_ENV_FILE.write_text("")
        DNS_ENV_FILE.chmod(0o600)
    values = template_values(env)
    values["QBIT_PBKDF2"] = qbit_pbkdf2(env["QBIT_PASSWORD"])
    root = SHARE / "templates" / "config"
    for source in root.rglob("*"):
        if source.is_dir():
            continue
        relative = str(source.relative_to(root))
        target = CONFIG / relative
        if relative in ONCE and target.exists():
            continue
        fresh = not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in (".png", ".ico"):      # images (logo d'Authelia) : copiées telles quelles
            target.write_bytes(source.read_bytes())
            continue
        target.write_text(substitute(source.read_text(), values))
        if fresh and relative == "qbittorrent/qBittorrent/qBittorrent.conf":
            qbit_bind_interface(qbit_mode() == "vpn")
    apps().regenerate()
    subdomains().render_file(env)       # routes par sous-domaine (retirées quand le domaine d'applications est vide)
    for secret in (CONFIG / "traefik" / "dynamic" / "seedbox.yml",
                   CONFIG / "qbit-manage" / "config.yml",
                   CONFIG / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"):
        if secret.exists():
            secret.chmod(0o600)


def front_fingerprint():
    """Empreinte de la configuration d'Authelia et de Traefik (qui ne se rechargent pas à chaud)."""
    digest = hashlib.sha256()
    files = [CONFIG / "authelia" / "configuration.yml", CONFIG / "traefik" / "traefik.yml",
             *sorted((CONFIG / "traefik" / "dynamic").glob("*.yml"))]
    for path in files:
        try:
            digest.update(path.read_bytes())
        except OSError:
            pass
    return digest.hexdigest()


FLARESOLVERR_URL = "http://flaresolverr:8191"


def jackett_config_wanted(env):
    """Configuration de Jackett à écrire, ou None si elle est déjà conforme. Jackett crée son
    ServerConfig.json au premier démarrage, avec une clé aléatoire et sans chemin de base : on le
    pose avant, pour que la clé soit connue et l'adresse /jackett prête. S'il a démarré sans (mise à
    jour depuis une version sans Jackett), le fichier est corrigé : la clé de .env fait foi."""
    if not env.get("JACKETT_API_KEY"):
        return None
    path = CONFIG / "jackett" / "Jackett" / "ServerConfig.json"
    wanted = {"Port": 9117, "AllowExternal": True, "APIKey": env["JACKETT_API_KEY"], "AdminPassword": "",
              "BasePathOverride": BASE_PATHS["jackett"], "UpdateDisabled": True, "UpdatePrerelease": False,
              "FlareSolverrUrl": FLARESOLVERR_URL, "FlareSolverrMaxTimeout": 55000, "CacheEnabled": True}
    if not path.exists():
        return wanted
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError):
        current = {}
    if current.get("APIKey") == wanted["APIKey"] and current.get("BasePathOverride") == wanted["BasePathOverride"]:
        return None
    keep = {k: v for k, v in current.items() if k not in wanted}   # indexeurs, cache... conservés
    return {**keep, **wanted}


def seed_jackett(env):
    config = jackett_config_wanted(env)
    if config is None:
        return False
    path = CONFIG / "jackett" / "Jackett" / "ServerConfig.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    path.chmod(0o600)
    os.chown(path.parent, int(env["PUID"]), int(env["PGID"]))
    os.chown(path, int(env["PUID"]), int(env["PGID"]))
    return True


def qbit_bind_interface(vpn):
    """qBittorrent n'émet que par le tunnel (tun0) en mode VPN, par n'importe quelle interface en
    mode direct. Le fichier n'est modifié que qBittorrent arrêté : il le réécrit en quittant."""
    conf = CONFIG / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"
    if not conf.exists():
        return
    lines = [l for l in conf.read_text().splitlines() if not re.match(r"Session\\Interface(Name)?=", l)]
    if vpn:
        index = next((i for i, l in enumerate(lines) if l.startswith("Session\\Port=")), len(lines) - 1)
        lines[index + 1:index + 1] = ["Session\\Interface=tun0", "Session\\InterfaceName=tun0"]
    conf.write_text("\n".join(lines) + "\n")


def qbit_interface_bound():
    """Vrai si qBittorrent est lié à une interface (tun0) dans sa configuration ; None sans fichier."""
    conf = CONFIG / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"
    if not conf.exists():
        return None
    return any(re.match(r"Session\\Interface(Name)?=", l) for l in conf.read_text().splitlines())


def qbit_heal_interface():
    """Remet la liaison d'interface de qBittorrent d'accord avec le mode : en accès direct, une liaison à tun0
    (qui n'existe pas) coupe toute connexion : tous les torrents « dormants », DHT à 0 nœud. Le fichier est corrigé
    conteneur arrêté, car qBittorrent le réécrit en quittant. Renvoie True si quelque chose a été corrigé."""
    mode = qbit_mode()
    bound = qbit_interface_bound()
    if mode is None or bound is None or bound == (mode == "vpn"):
        return False
    say("qBittorrent : liaison d'interface remise d'accord avec le mode " + ("VPN (tun0)" if mode == "vpn" else "direct (aucune)"))
    run("docker", "stop", "qbittorrent", check=False, capture=True)
    qbit_bind_interface(mode == "vpn")
    run("docker", "start", "qbittorrent", check=False, capture=True)
    return True


def qbit_stop():
    """Libère le nom de conteneur « qbittorrent » avant de démarrer l'autre mode."""
    run("docker", "rm", "-f", "qbittorrent", check=False, capture=True)


def qbit_switch(mode):
    """Bascule qBittorrent entre « direct » et « vpn » : profil, interface, pare-feu, liens."""
    previous = qbit_mode()
    profiles = profiles_read() - {"vpn", "direct"}
    profiles.add(mode)
    if previous != mode:
        compose("stop", "qbit-manage", "cross-seed", check=False, capture=True)
        qbit_stop()
        if mode == "direct":
            compose("stop", "gluetun", check=False, capture=True)
        qbit_bind_interface(mode == "vpn")
    env_set(COMPOSE_PROFILES=",".join(sorted(profiles)))
    return previous


def qbit_pbkdf2(password):
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100000, 64)
    return base64.b64encode(salt).decode() + ":" + base64.b64encode(key).decode()


def write_gpu_override():
    """Transmet le GPU à Jellyfin s'il existe (transcodage matériel) : voir gpu.py."""
    gpu().write_override()


def set_ownership():
    """Non récursif : les conteneurs écrivent ensuite avec PUID et PGID, et un chown complet
    à chaque opération serait très long sur une grande bibliothèque."""
    env = env_read()
    uid, gid = int(env["PUID"]), int(env["PGID"])
    paths = [STORAGE, ENV_FILE]
    paths += [p for p in STORAGE.glob("*") if p.is_dir()] + [p for p in STORAGE.glob("*/*") if p.is_dir()]
    # Traefik et Dockge gèrent eux-mêmes les droits de leurs données
    for child in CONFIG.iterdir():
        if child.is_dir() and child.name not in ("traefik", "dockge"):
            paths.append(child)
    root = SHARE / "templates" / "config"
    for source in root.rglob("*"):
        relative = source.relative_to(root)
        if relative.parts[0] not in ("traefik", "dockge") and (CONFIG / relative).exists():
            paths.append(CONFIG / relative)
    for path in paths:
        os.chown(path, uid, gid)


def ensure_env():
    template = substitute((SHARE / "stack" / "env.template").read_text(),
                          {"BASE": str(BASE), "STORAGE": str(STORAGE), "STACKS": str(BASE.parent)})
    if not ENV_FILE.exists():
        fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(template)
    else:
        # Mise à jour de l'appliance : les variables apparues depuis sont ajoutées, jamais modifiées
        known = env_read()
        added = {}
        for line in template.splitlines():
            match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
            if match and match.group(1) not in known:
                added[match.group(1)] = match.group(2)
        if added:
            env_set(**added)
            say("nouvelles variables : " + ", ".join(added))
        migrate_stacks_dir()
        if qbit_mode() is None:
            env_set(COMPOSE_PROFILES=",".join(sorted(profiles_read() | {"direct"})))
            say("qBittorrent : mode direct (sans VPN) ; le VPN reste activable dans la console")
    generate_secrets()


def migrate_stacks_dir():
    """Ancienne version : Dockge ne voyait que <base>/stacks et ne gérait donc pas la pile seedbox.
    Son dossier devient le parent de <base> ; les piles déjà créées dans l'ancien dossier suivent."""
    old = BASE / "stacks"
    if env_read().get("STACKS_DIR") != str(old):
        return
    new = BASE.parent
    env_set(STACKS_DIR=str(new))
    if old.is_dir():
        for child in sorted(old.iterdir()):
            if child.is_dir() and not (new / child.name).exists():
                shutil.move(str(child), str(new / child.name))
                say(f"pile Dockge déplacée : {child.name} -> {new / child.name}")
        try:
            old.rmdir()
        except OSError:
            say(f"{old} n'est pas vide : conservé")
    say(f"Dockge gère désormais la pile seedbox (dossier des piles : {new})")


def update_addresses():
    """Adresse principale (IP en mode automatique) et liste des adresses servies, dans .env."""
    env = env_read()
    updates = {}
    if env.get("DOMAIN_MODE", "auto") == "auto" and env.get("DOMAIN") != auto_address():
        updates["DOMAIN"] = auto_address()
    hosts = ",".join(public_hosts({**env, **updates}))
    if env.get("PUBLIC_HOSTS") != hosts:
        updates["PUBLIC_HOSTS"] = hosts
    if updates:
        env_set(**updates)
    return bool(updates)


def admin_password(env=None):
    """Mot de passe de l'administrateur principal : celui choisi dans l'assistant de premier
    démarrage tant que le déploiement ne l'a pas appliqué, sinon le mot de passe initial."""
    try:
        chosen = PASSWORD_FILE.read_text().rstrip("\n")
        if chosen:
            return chosen
    except OSError:
        pass
    return (env or env_read()).get("ADMIN_INITIAL_PASSWORD", "")


def init():
    say("initialisation de l'arborescence")
    for path in (STATE, ETC, APPS_DIR, shares().ROOT):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("downloads/radarr", "downloads/sonarr", "downloads/autobrr", "downloads/cross-seed",
                 "media/movies", "media/tv", "media/music", "media/books"):
        (STORAGE / name).mkdir(parents=True, exist_ok=True)
    # Jellyfin n'enregistre pas une bibliothèque vide et ignore alors les notifications :
    # sans ce fichier masqué, le premier film n'apparaîtrait qu'à l'analyse suivante, 12 h plus tard
    for name in ("media/movies", "media/tv"):
        (STORAGE / name / ".seedbox-bibliotheque").touch()
    for name in ("authelia", "autobrr", "cross-seed", "dockge", "gluetun", "homepage", "jellyfin",
                 "prowlarr", "redis", "jackett/Jackett", "qbit-manage", "qbittorrent/qBittorrent/data/BT_backup",
                 "radarr", "seerr", "sonarr", "traefik/dynamic"):
        (CONFIG / name).mkdir(parents=True, exist_ok=True)
    shutil.copy(SHARE / "stack" / "docker-compose.yml", BASE / "docker-compose.yml")
    ensure_env()
    update_addresses()
    env = env_read()
    ensure_certificates(env)
    render_templates()
    seed_jackett(env)
    if not USERS_FILE.exists():
        users_save({})
        users_store_add(env["ADMIN_USER"], admin_password(env), "admin")
    write_gpu_override()
    set_ownership()


# ----------------------------------------------------------------------------
#  Annuaire Authelia
# ----------------------------------------------------------------------------
def authelia_hash(password):
    """PBKDF2-SHA512 : format accepté par Authelia, calculable sans dépendance."""
    salt = os.urandom(16)
    iterations = 310000
    key = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations, 64)
    ab64 = lambda raw: base64.b64encode(raw).decode().rstrip("=").replace("+", ".")
    return f"$pbkdf2-sha512${iterations}${ab64(salt)}${ab64(key)}"


def users_load():
    """Lecture du sous-ensemble YAML écrit par users_save."""
    users, current = {}, None
    if not USERS_FILE.exists():
        return users
    for line in USERS_FILE.read_text().splitlines():
        match = re.match(r"^  ([^ :#]+):\s*$", line)
        if match:
            current = match.group(1)
            users[current] = {"groups": []}
            continue
        match = re.match(r"^    (displayname|password|email): '(.*)'$", line)
        if match and current:
            users[current][match.group(1)] = match.group(2)
            continue
        match = re.match(r"^      - (\w+)$", line)
        if match and current:
            users[current]["groups"].append(match.group(1))
    return users


def users_save(users):
    lines = ["# Annuaire SeedBoxBuntu : géré par seedboxctl et la console", "users:"]
    if not users:
        lines[-1] = "users: {}"
    for login, user in sorted(users.items()):
        lines += [f"  {login}:", f"    displayname: '{user['displayname']}'",
                  f"    password: '{user['password']}'", f"    email: '{user['email']}'", "    groups:"]
        lines += [f"      - {group}" for group in user["groups"]]
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(USERS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    env = env_read()
    os.chown(USERS_FILE, int(env.get("PUID", 1000)), int(env.get("PGID", 1000)))


def users_store_add(login, password, role):
    users = users_load()
    users[login] = {
        "displayname": login, "password": authelia_hash(password),
        # Adresse fictive : Authelia en exige une, et celle du serveur peut être une IP
        "email": f"{login}@seedbox.local",
        "groups": [ROLE_GROUPS[role]],
    }
    users_save(users)


def validate_account(login, password=None):
    if not VALID_LOGIN.match(login):
        raise SeedboxError("identifiant invalide : 2 à 31 caractères, a-z, 0-9, « . », « _ » ou « - »")
    if password is not None and len(password) < MIN_PASSWORD:
        raise SeedboxError(f"mot de passe trop court : {MIN_PASSWORD} caractères au minimum")
    if password is not None and "\n" in password:
        raise SeedboxError("mot de passe invalide")


# ----------------------------------------------------------------------------
#  Clients d'API
# ----------------------------------------------------------------------------
def jellyfin_api(method, path, body=None, token=None):
    env = env_read()
    token = token or env.get("JELLYFIN_API_KEY", "")
    header = 'MediaBrowser Client="seedboxctl", Device="seedbox", DeviceId="seedboxctl", Version="1.0"'
    if token:
        header += f', Token="{token}"'
    result = None
    for _ in range(40):  # refus de connexion ou 503 tant que le serveur termine son démarrage
        try:
            result = request(method, svc("jellyfin", 8096) + BASE_PATHS["jellyfin"] + path,
                             headers={"Authorization": header}, body=body, timeout=60)
            if result.status != 503:
                return result
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(3)
    if result is None:
        raise SeedboxError("Jellyfin ne répond pas")
    return result


def jellyfin_user(login):
    result = jellyfin_api("GET", "/Users")
    for user in result.json() or []:
        if user["Name"].lower() == login.lower():
            return user
    return None


def arr_api(name, method, path, body=None):
    env = env_read()
    port, version = {"radarr": (7878, "v3"), "sonarr": (8989, "v3"), "prowlarr": (9696, "v1"), "lidarr": (8686, "v1")}[name]
    return request(method, f"{svc(name, port)}{BASE_PATHS[name]}/api/{version}{path}", body=body,
                   headers={"X-Api-Key": env[f"{name.upper()}_API_KEY"]}, timeout=60)


def seerr_api(method, path, body=None, cookie=None):
    env = env_read()
    headers = {"Cookie": cookie} if cookie else {"X-Api-Key": env.get("SEERR_API_KEY", "")}
    return request(method, f"{svc('seerr', 5055)}/api/v1{path}", headers=headers, body=body, timeout=60)


def autobrr_session():
    """L'image publiée ne délègue pas encore l'authentification : compte intégré, derrière Authelia."""
    env = env_read()
    base = svc("autobrr", 7474) + BASE_PATHS["autobrr"] + "/api/auth"
    credentials_body = {"username": "admin", "password": env["AUTOBRR_ADMIN_PASSWORD"]}
    if request("GET", f"{base}/onboard", timeout=15).ok:
        result = request("POST", f"{base}/onboard", body=credentials_body, timeout=15)
        if not result.ok:
            raise SeedboxError(f"autobrr : création du compte refusée (HTTP {result.status})")
    result = request("POST", f"{base}/login", body=credentials_body, timeout=15)
    if result.status == 429:        # trop d'échecs de suite depuis cette adresse : autobrr rend un essai toutes les 20 secondes
        time.sleep(AUTOBRR_LOGIN_WAIT)
        result = request("POST", f"{base}/login", body=credentials_body, timeout=15)
    if not result.ok and result.status == 403 and autobrr_recover_password(env["AUTOBRR_ADMIN_PASSWORD"]):
        result = request("POST", f"{base}/login", body=credentials_body, timeout=15)
    if result.status == 429:
        raise SeedboxError("autobrr : trop de connexions ratées de suite, autobrr bloque l'adresse quelques instants : réessayez dans une minute")
    if not result.ok:
        raise SeedboxError(f"autobrr : connexion refusée (HTTP {result.status}) : le mot de passe d'autobrr ne correspond plus à celui de la console ; "
                           "sudo docker exec -it autobrr autobrrctl --config /config change-password admin, puis saisissez le mot de passe affiché dans Secrets")
    return result.cookies()


AUTOBRR_LOGIN_WAIT = 21


def autobrr_force_password(password):
    """Remplace le mot de passe du compte admin avec l'outil d'autobrr, sans connaître l'ancien : aucune connexion, donc aucun échec
    que le verrouillage d'autobrr (5 essais ratés par adresse) compterait. Selon la version, l'outil lit la saisie seule ou avec sa
    confirmation : les deux formes sont essayées. Le résultat est vérifié par l'appelant ; faux si l'outil est inutilisable."""
    for stdin in (f"{password}\n", f"{password}\n{password}\n"):
        result = run("docker", "exec", "-i", "autobrr", "autobrrctl", "--config", "/config", "change-password", "admin",
                     check=False, capture=True, stdin=stdin)
        if result.returncode == 0:
            return True
    return False


def autobrr_recover_password(password):
    """Mot de passe d'autobrr différent de celui de la console (renouvellement interrompu, base restaurée...) : on remet celui de la
    console avec l'outil d'autobrr. Le résultat est vérifié par l'appelant, qui retente la connexion ; sans effet si l'outil refuse."""
    if autobrr_force_password(password):
        say("autobrr : mot de passe remis à celui de la console")
        return True
    return False


def autobrr_api(method, path, body=None, cookie=None):
    return request(method, f"{svc('autobrr', 7474)}{BASE_PATHS['autobrr']}/api{path}", body=body,
                   headers={"Cookie": cookie}, timeout=30)


def set_fields(item, values):
    for field in item.get("fields", []):
        if field["name"] in values:
            field["value"] = values[field["name"]]
    return item


def schema_item(name, path, implementation):
    for item in arr_api(name, "GET", f"{path}/schema").json():
        if item["implementation"] == implementation:
            return item
    raise SeedboxError(f"{name} : modèle {implementation} introuvable")


def arr_upsert(name, path, implementation, label, fields, extra=None):
    existing = [i for i in arr_api(name, "GET", path).json() if i["implementation"] == implementation]
    item = existing[0] if existing else schema_item(name, path, implementation)
    item.update({"name": label, **(extra or {})})
    set_fields(item, fields)
    method, target = ("PUT", f"{path}/{item['id']}") if existing else ("POST", path)
    # forceSave : qBittorrent peut être arrêté tant que le VPN n'est pas renseigné
    result = arr_api(name, method, f"{target}?forceSave=true", item)
    if not result.ok:
        raise SeedboxError(f"{name} : {label} refusé (HTTP {result.status}) {result.text[:200]}")


# ----------------------------------------------------------------------------
#  Provisionnement : chaque étape crée ou met à jour, sans jamais dupliquer
# ----------------------------------------------------------------------------
def provision_jellyfin():
    env = env_read()
    info = None
    for _ in range(200):  # Jellyfin répond 503 tant que ses migrations ne sont pas terminées
        try:
            result = request("GET", svc("jellyfin", 8096) + BASE_PATHS["jellyfin"] + "/System/Info/Public", timeout=10)
            # Pendant son démarrage, Jellyfin 12 répond par un serveur provisoire (clés en
            # minuscules, « startupWizardCompleted: false ») : l'assistant serait relancé à tort
            if result.ok and '"StartupWizardCompleted"' in result.text:
                info = result.json()
                break
        except (OSError, ValueError, http.client.HTTPException):
            pass
        time.sleep(3)
    if info is None:
        raise SeedboxError("Jellyfin ne répond pas")
    login, password = env["ADMIN_USER"], admin_password(env)
    if not info.get("StartupWizardCompleted"):
        say("Jellyfin : assistant initial")
        jellyfin_api("POST", "/Startup/Configuration", {
            "UICulture": "fr", "MetadataCountryCode": "FR", "PreferredMetadataLanguage": "fr"})
        jellyfin_api("GET", "/Startup/User")
        jellyfin_api("POST", "/Startup/User", {"Name": login, "Password": password})
        jellyfin_api("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False})
        result = jellyfin_api("POST", "/Startup/Complete")
        if not result.ok:
            raise SeedboxError(f"Jellyfin : assistant refusé (HTTP {result.status})")
    if not env.get("JELLYFIN_API_KEY"):
        token = None
        for _ in range(20):
            result = jellyfin_api("POST", "/Users/AuthenticateByName", {"Username": login, "Pw": password}, token="-")
            if result.ok:
                token = result.json()["AccessToken"]
                break
            time.sleep(2)
        if not token:
            raise SeedboxError("Jellyfin : connexion administrateur impossible")
        jellyfin_api("POST", "/Auth/Keys?app=seedboxbuntu", token=token)
        keys = jellyfin_api("GET", "/Auth/Keys", token=token).json()["Items"]
        env_set(JELLYFIN_API_KEY=keys[-1]["AccessToken"])
    folders = {f["Name"]: f for f in jellyfin_api("GET", "/Library/VirtualFolders").json()}
    created = False
    for name, kind, default, role in (("Films", "movies", "/data/media/movies", "movies"),
                                      ("Séries", "tvshows", "/data/media/tv", "tv")):
        created |= jellyfin_library_sync(name, kind, shares().library_paths(default, role), folders)
    if created:
        # Une bibliothèque jamais analysée ignore aussi les notifications de Radarr et Sonarr
        jellyfin_api("POST", "/Library/Refresh")


def jellyfin_library_sync(name, kind, wanted, folders, refresh=False):
    """Crée la bibliothèque, ou ajuste ses dossiers : ajoute le partage choisi, retire un partage abandonné.
    Renvoie True si la bibliothèque vient d'être créée."""
    if name in folders:
        current = [p.rstrip("/") for p in folders[name].get("Locations") or []]
        for path in wanted:
            if path not in current:
                say(f"Jellyfin : {name} inclut {path}")
                jellyfin_api("POST", "/Library/VirtualFolders/Paths?refreshLibrary=true",
                             {"Name": name, "PathInfo": {"Path": path}})
        for path in current:
            if path.startswith(shares().INNER + "/") and path not in wanted:
                say(f"Jellyfin : {name} n'inclut plus {path}")
                query = urllib.parse.urlencode({"name": name, "path": path, "refreshLibrary": "true"})
                jellyfin_api("DELETE", f"/Library/VirtualFolders/Paths?{query}")
        return False
    say(f"Jellyfin : bibliothèque {name}")
    query = urllib.parse.urlencode({"name": name, "collectionType": kind, "refreshLibrary": "true" if refresh else "false"})
    result = jellyfin_api("POST", f"/Library/VirtualFolders?{query}", {
        "LibraryOptions": {"PathInfos": [{"Path": path} for path in wanted], "EnableRealtimeMonitor": True,
                           "PreferredMetadataLanguage": "fr", "MetadataCountryCode": "FR"}})
    if not result.ok:
        raise SeedboxError(f"Jellyfin : bibliothèque {name} refusée (HTTP {result.status})")
    return True


def qbit_reachable():
    try:
        return container_running(qbit_host()) and request("GET", svc(qbit_host(), 8080) + "/", timeout=5).status < 500
    except (OSError, SeedboxError, http.client.HTTPException):
        return False


def provision_arr():
    env = env_read()
    wait_http("radarr", 7878, "/radarr/ping")
    wait_http("sonarr", 8989, "/sonarr/ping")
    wait_http("prowlarr", 9696, "/prowlarr/ping")
    if qbit_mode():
        try:
            wait_http(qbit_host(), 8080, "/", timeout=150)  # en mode VPN, qBittorrent attend que le tunnel soit sain
        except SeedboxError as error:
            say(f"qBittorrent n'est pas encore joignable : {error}")
    for name, folder, category_field, category in (
            ("radarr", shares().location("movies"), "movieCategory", "radarr"),
            ("sonarr", shares().location("tv"), "tvCategory", "sonarr")):
        roots = {r["path"].rstrip("/") for r in arr_api(name, "GET", "/rootfolder").json()}
        if folder not in roots:
            say(f"{name} : dossier racine {folder}")
            result = arr_api(name, "POST", "/rootfolder", {"path": folder})
            if not result.ok:
                raise SeedboxError(f"{name} : dossier racine refusé {result.text[:200]}")
        arr_upsert(name, "/downloadclient", "QBittorrent", "qBittorrent", {
            "host": qbit_host(), "port": 8080, "useSsl": False, "username": env["QBIT_USERNAME"],
            "password": env["QBIT_PASSWORD"], category_field: category},
            # Radarr et Sonarr refusent d'activer un client injoignable (tunnel pas encore établi)
            {"enable": qbit_reachable()})
        arr_upsert(name, "/notification", "MediaBrowser", "Jellyfin", {
            "host": "jellyfin", "port": 8096, "useSsl": False, "urlBase": BASE_PATHS["jellyfin"],
            "apiKey": env["JELLYFIN_API_KEY"],
            "notify": False, "updateLibrary": True},
            {"onDownload": True, "onUpgrade": True, "onRename": True,
             "onMovieDelete" if name == "radarr" else "onSeriesDelete": True})
    for name, port, label in (("radarr", 7878, "Radarr"), ("sonarr", 8989, "Sonarr")):
        arr_upsert("prowlarr", "/applications", label, label, {
            "prowlarrUrl": "http://prowlarr:9696/prowlarr", "baseUrl": f"http://{name}:{port}/{name}",
            "apiKey": env[f"{name.upper()}_API_KEY"]}, {"syncLevel": "fullSync"})


def provision_apps():
    """Relie de nouveau les applications ajoutées depuis le catalogue (mode qBittorrent changé, mise à jour)."""
    module, errors = apps(), []
    entries = module.installed_entries()
    if entries:
        # Emplacements changés (Navidrome, Audiobookshelf, Lidarr) : les conteneurs sont recréés si leur définition a changé
        module.regenerate()
        compose("up", "-d", *[e["id"] for e in entries], check=False, capture=True)
    for entry in entries:
        hook = module.INTEGRATIONS.get(entry.get("integration"))
        if not hook or not container_running(entry["id"]):
            continue
        try:
            hook(entry, env_read())
        except Exception as error:
            errors.append(f"{entry['name']} : {error}")
    if errors:
        raise SeedboxError("; ".join(errors))


def jackett_session():
    """Sans mot de passe, Jackett délivre quand même un cookie d'accès : on suit ses redirections
    (Dashboard, Login, TestCookie, Dashboard) en gardant les cookies. Authelia protège déjà l'accès."""
    base = svc("jackett", 9117)
    path, jar = BASE_PATHS["jackett"] + "/UI/Dashboard", {}
    for _ in range(8):
        result = request("GET", base + path, headers={"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}, timeout=30)
        for key, value in result.headers:
            if key.lower() == "set-cookie":
                name, _, rest = value.partition("=")
                jar[name.strip()] = rest.split(";", 1)[0]
        location = next((v for k, v in result.headers if k.lower() == "location"), None)
        if not 300 <= result.status < 400 or not location:
            if result.ok and "Jackett" in jar:
                return "; ".join(f"{k}={v}" for k, v in jar.items())
            break
        target = urllib.parse.urlsplit(urllib.parse.urljoin(base + path, location))
        path = target.path + (f"?{target.query}" if target.query else "")
    raise SeedboxError("Jackett : accès à l'interface refusé (cookie non délivré)")


def jackett_api(method, path, body=None):
    return request(method, f"{svc('jackett', 9117)}{BASE_PATHS['jackett']}/api/v2.0{path}", body=body, timeout=60,
                   headers={"Cookie": jackett_session()})


def provision_jackett():
    """Clé API, chemin de base et FlareSolverr de Jackett ; FlareSolverr comme proxy de Prowlarr."""
    env = env_read()
    if jackett_config_wanted(env) is not None:
        # Jackett a démarré avec sa configuration par défaut (mise à jour depuis une version sans
        # Jackett) : arrêt, correction du fichier, redémarrage
        say("Jackett : configuration initiale corrigée (clé API et chemin /jackett)")
        running = container_running("jackett")
        if running:
            run("docker", "stop", "jackett", check=False, capture=True)
        seed_jackett(env)
        if running:
            run("docker", "start", "jackett", check=False, capture=True)
    wait_http("jackett", 9117, BASE_PATHS["jackett"] + "/UI/Dashboard")
    result = jackett_api("GET", "/server/config")
    if not result.ok:
        raise SeedboxError(f"Jackett : configuration refusée (HTTP {result.status})")
    config = result.json()
    wanted = {"flaresolverrurl": FLARESOLVERR_URL, "flaresolverrmaxtimeout": 55000, "external": True,
              "updatedisabled": True}
    if config.get("api_key") != env["JACKETT_API_KEY"]:
        raise SeedboxError("Jackett : sa clé API diffère de celle de .env (supprimez config/jackett pour repartir à zéro)")
    changes = {k: v for k, v in wanted.items() if config.get(k) != v}
    if changes:
        say("Jackett : " + ", ".join(changes))
        result = jackett_api("POST", "/server/config", {**config, **changes})
        if not result.ok:
            raise SeedboxError(f"Jackett : réglage refusé (HTTP {result.status}) {result.text[:160]}")


def provision_prowlarr_flaresolverr():
    """Proxy d'indexeur FlareSolverr de Prowlarr, activé par l'étiquette « flaresolverr » :
    ajoutez cette étiquette à un indexeur protégé par Cloudflare pour le faire passer par lui."""
    wait_http("prowlarr", 9696, BASE_PATHS["prowlarr"] + "/ping")
    tags = {tag["label"]: tag["id"] for tag in arr_api("prowlarr", "GET", "/tag").json()}
    if "flaresolverr" not in tags:
        result = arr_api("prowlarr", "POST", "/tag", {"label": "flaresolverr"})
        if not result.ok:
            raise SeedboxError(f"Prowlarr : étiquette refusée (HTTP {result.status})")
        tags["flaresolverr"] = result.json()["id"]
    # Prowlarr teste le proxy à l'enregistrement : FlareSolverr lance un navigateur, lent au premier appel
    wait_http("flaresolverr", 8191, "/health", timeout=240)
    for attempt in range(4):
        try:
            return arr_upsert("prowlarr", "/indexerproxy", "FlareSolverr", "FlareSolverr",
                              {"host": FLARESOLVERR_URL + "/", "requestTimeout": 60}, {"tags": [tags["flaresolverr"]]})
        except (SeedboxError, OSError) as error:
            if attempt == 3:
                raise
            say(f"Prowlarr : proxy FlareSolverr, nouvel essai ({error})")
            time.sleep(15)


def jackett_sync():
    """Les indexeurs configurés dans Jackett deviennent des indexeurs Torznab de Prowlarr, qui les
    propage à Radarr et Sonarr. Un indexeur déjà présent n'est pas touché."""
    env = env_read()
    if not container_running("jackett") or not container_running("prowlarr"):
        return 0
    configured = jackett_api("GET", "/indexers?configured=true")
    if not configured.ok:
        raise SeedboxError(f"Jackett : liste refusée (HTTP {configured.status})")
    present = {i["name"] for i in arr_api("prowlarr", "GET", "/indexer").json()}
    created = 0
    for indexer in configured.json() or []:
        label = f"Jackett : {indexer.get('name') or indexer['id']}"
        if label in present:
            continue
        schema = schema_item("prowlarr", "/indexer", "Torznab")
        schema.update({"name": label, "enable": True, "appProfileId": 1, "priority": 25})
        set_fields(schema, {"baseUrl": f"http://jackett:9117{BASE_PATHS['jackett']}/api/v2.0/indexers/{indexer['id']}/results/torznab",
                            "apiPath": "/api", "apiKey": env["JACKETT_API_KEY"]})
        result = arr_api("prowlarr", "POST", "/indexer?forceSave=true", schema)
        if not result.ok:
            # Prowlarr teste l'indexeur à l'enregistrement et refuse un test sans résultat (indexeur tout
            # juste configuré, site momentanément indisponible) : on le crée désactivé, à activer ensuite
            schema["enable"] = False
            result = arr_api("prowlarr", "POST", "/indexer?forceSave=true", schema)
            if not result.ok:
                raise SeedboxError(f"Prowlarr : {label} refusé (HTTP {result.status}) {result.text[:160]}")
            say(f"Prowlarr : {label} créé désactivé (le test de recherche n'a rien renvoyé)")
        else:
            say(f"Prowlarr : {label}")
        created += 1
    return created


def seerr_read_key():
    settings = CONFIG / "seerr" / "settings.json"
    if settings.exists():
        return json.loads(settings.read_text()).get("main", {}).get("apiKey", "")
    return ""


def seerr_jellyfin_host():
    settings = CONFIG / "seerr" / "settings.json"
    if settings.exists():
        return json.loads(settings.read_text()).get("jellyfin", {}).get("ip", "")
    return ""


def provision_seerr():
    env = env_read()
    wait_http("seerr", 5055, "/api/v1/status")
    domain = env["DOMAIN"]
    public = seerr_api("GET", "/settings/public").json()
    cookie = None
    if not public.get("initialized"):
        say("Seerr : liaison avec Jellyfin")
        # Seerr exige une adresse e-mail de forme valide : l'IP du serveur n'en fournit pas
        login = {"username": env["ADMIN_USER"], "password": admin_password(env),
                 "email": f"{env['ADMIN_USER']}@seedbox.local"}
        if not seerr_jellyfin_host():  # le serveur n'est transmis qu'à la première connexion
            login.update({"hostname": "jellyfin", "port": 8096, "urlBase": BASE_PATHS["jellyfin"], "useSsl": False,
                          "serverType": 2})
        result = request("POST", svc("seerr", 5055) + "/api/v1/auth/jellyfin", body=login, timeout=60)
        if not result.ok:
            raise SeedboxError(f"Seerr : connexion à Jellyfin refusée (HTTP {result.status}) {result.text[:200]}")
        cookie = result.cookies()
        libraries = seerr_api("POST", "/settings/jellyfin/library/sync", {}, cookie=cookie)
        if not libraries.ok:
            raise SeedboxError(f"Seerr : bibliothèques refusées (HTTP {libraries.status}) {libraries.text[:200]}")
        for library in libraries.json():
            seerr_api("PUT", f"/settings/jellyfin/library/{library['id']}", {"enabled": True}, cookie=cookie)
    key = seerr_read_key()
    if key and key != env.get("SEERR_API_KEY"):
        env_set(SEERR_API_KEY=key)
    headers_cookie = cookie
    seerr_api("POST", "/settings/main", {"applicationUrl": f"https://{domain}:{SEERR_PORT}", "locale": "fr"},
              cookie=headers_cookie)
    # Liens « Lire dans Jellyfin » : adresse publique, et non le nom Docker du serveur
    jellyfin = seerr_api("GET", "/settings/jellyfin", cookie=cookie).json() or {}
    external = f"https://{domain}{BASE_PATHS['jellyfin']}"
    if jellyfin.get("ip") and jellyfin.get("externalHostname") != external:
        body = {k: v for k, v in jellyfin.items() if k in ("ip", "port", "useSsl", "urlBase", "apiKey")}
        seerr_api("POST", "/settings/jellyfin", {**body, "externalHostname": external}, cookie=cookie)
    env = env_read()
    for name, port, kind in (("radarr", 7878, "movies"), ("sonarr", 8989, "tv")):
        profile = arr_api(name, "GET", "/qualityprofile").json()[0]
        folder = shares().location("movies" if name == "radarr" else "tv")
        payload = {
            "name": name.capitalize(), "hostname": name, "port": port, "apiKey": env[f"{name.upper()}_API_KEY"],
            "useSsl": False, "baseUrl": BASE_PATHS[name], "activeProfileId": profile["id"],
            "activeProfileName": profile["name"],
            "activeDirectory": folder, "tags": [], "is4k": False, "isDefault": True,
            "externalUrl": f"https://{domain}/{name}", "syncEnabled": True, "preventSearch": False,
        }
        if name == "radarr":
            payload["minimumAvailability"] = "released"
        else:
            payload.update({"seriesType": "standard", "animeSeriesType": "anime", "activeAnimeProfileId": profile["id"],
                            "activeAnimeProfileName": profile["name"], "activeAnimeDirectory": folder,
                            "enableSeasonFolders": True, "monitorNewItems": "all"})
        existing = seerr_api("GET", f"/settings/{name}", cookie=cookie).json() or []
        if existing:
            current = {k: v for k, v in existing[0].items() if k != "id"}  # id en lecture seule
            result = seerr_api("PUT", f"/settings/{name}/{existing[0]['id']}", {**current, **payload}, cookie=cookie)
        else:
            result = seerr_api("POST", f"/settings/{name}", payload, cookie=cookie)
        if not result.ok:
            raise SeedboxError(f"Seerr : {name} refusé (HTTP {result.status}) {result.text[:200]}")
    if cookie:
        seerr_api("POST", "/settings/initialize", {}, cookie=cookie)


def provision_dockge():
    """Authelia protège Dockge : on crée son compte unique et on coupe sa connexion propre."""
    database = CONFIG / "dockge" / "dockge.db"
    wait_http("dockge", 5001, "/")
    for _ in range(30):
        if database.exists():
            break
        time.sleep(1)
    with sqlite3.connect(database) as db:
        has_user = db.execute("SELECT COUNT(*) FROM user").fetchone()[0] > 0
        disabled = db.execute("SELECT value FROM setting WHERE key='disableAuth'").fetchone()
    if has_user and disabled and json.loads(disabled[0]) is True:
        return
    say("Dockge : connexion déléguée à Authelia")
    script = "console.log(require('/app/node_modules/bcryptjs').hashSync(process.argv[1],10))"
    hashed = run("docker", "exec", "dockge", "node", "-e", script, hex_secret(24), capture=True).stdout.strip()
    with sqlite3.connect(database) as db:
        if not has_user:
            db.execute("INSERT INTO user (username, password, active) VALUES ('admin', ?, 1)", (hashed,))
        db.execute("INSERT INTO setting (key, value, type) VALUES ('disableAuth', 'true', 'security') "
                   "ON CONFLICT(key) DO UPDATE SET value='true'")
    run("docker", "restart", "dockge", capture=True)
    wait_http("dockge", 5001, "/")


def provision_autobrr():
    env = env_read()
    if not container_running("autobrr"):
        return
    wait_http("autobrr", 7474, "/autobrr/api/healthz/liveness")
    payload = {
        "name": "qBittorrent", "type": "QBITTORRENT", "enabled": True, "host": f"http://{qbit_host()}:8080", "port": 0,
        "tls": False, "tls_skip_verify": False, "username": env["QBIT_USERNAME"], "password": env["QBIT_PASSWORD"],
        "settings": {"basic": {}, "rules": {"enabled": False}},
    }
    cookie = autobrr_session()
    result = autobrr_api("GET", "/downloaders", cookie=cookie)
    if not result.ok:
        raise SeedboxError(f"autobrr : liste refusée (HTTP {result.status}) {result.text[:200]}")
    existing = [c for c in (result.json() or []) if c["name"] == "qBittorrent"]
    if existing:
        result = autobrr_api("PUT", "/downloaders", {**payload, "id": existing[0]["id"]}, cookie)
    else:
        say("autobrr : client qBittorrent")
        result = autobrr_api("POST", "/downloaders", payload, cookie)
    if not result.ok:
        raise SeedboxError(f"autobrr : client refusé (HTTP {result.status}) {result.text[:200]}")
    try:
        autobrr_sync_indexers(cookie)
    except (SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"autobrr : indexeurs de Prowlarr non repris ({error})")
    try:
        provision_autobrr_filter(cookie)
    except (SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"autobrr : filtre de nouveautés non posé ({error})")


FILTER_NAME = "Nouveautés freeleech (SeedBoxBuntu)"            # films et séries : nom d'origine, gardé pour retrouver le filtre déjà créé
FILTER_MUSIC = "Nouveautés freeleech : musique (SeedBoxBuntu)"
FILTER_BOOKS = "Nouveautés freeleech : livres (SeedBoxBuntu)"
# Listes que autobrr range dans des colonnes NOT NULL : absentes du JSON, il les écrit NULL et SQLite refuse la requête
# (HTTP 500 « NOT NULL constraint failed: filter.resolutions »). L'interface web les envoie toujours, vides ou remplies.
AUTOBRR_FILTER_LISTS = ("announce_types", "origins", "except_origins", "bonus", "resolutions", "codecs", "sources",
                        "containers", "match_hdr", "except_hdr", "match_other", "except_other", "match_release_types",
                        "formats", "quality", "media", "match_language", "except_language")


# Trois filtres : autobrr écarte tout torrent sans résolution dès qu'une résolution est fixée, la qualité vidéo ne peut donc pas
# partager un filtre avec la musique et les livres. Catégories : autobrr ramène toute catégorie Newznab standard à son parent
# (2040 devient Movies, 2000 ; 3030 devient Audio, 3000) : les livres audio arrivent donc avec la musique. Jeux, logiciels et adulte
# ne sont jamais repris. Musique et livres : 1 par heure, la vidéo 3.
AUTOBRR_FILTERS = (
    {"kind": "video", "name": FILTER_NAME, "tag": "films-series", "per_hour": 3,
     "match_categories": "Movies*,20??,TV*,50??", "except_categories": ""},
    {"kind": "music", "name": FILTER_MUSIC, "tag": "musique", "per_hour": 1,
     "match_categories": "Audio*,30??", "except_categories": ""},
    {"kind": "books", "name": FILTER_BOOKS, "tag": "livres", "per_hour": 1,
     "match_categories": "Books*,70??", "except_categories": ""},
)
# Qualité vidéo : le préréglage Recyclarr de la console (1080p = Blu-ray + WEB, 2160p = UHD Blu-ray + WEB)
AUTOBRR_VIDEO_QUALITY = {
    "1080p": {"resolutions": ["1080p"], "sources": ["BluRay", "WEB", "WEB-DL", "WEBRip"]},
    "2160p": {"resolutions": ["2160p"], "sources": ["UHD.BluRay", "BluRay", "WEB", "WEB-DL", "WEBRip"]},
}


def autobrr_quality_preset():
    preset = env_read().get("RECYCLARR_PRESET") or "1080p"
    return preset if preset in AUTOBRR_VIDEO_QUALITY else "1080p"


def autobrr_filter_payload(indexers, client_id, current=None, kind="video", preset="1080p", apply_quality=True):
    """Nouveautés freeleech, tous trackers : un téléchargement freeleech ne coûte aucun ratio, et un torrent tout juste publié est celui
    qui trouve des téléchargeurs. Plafonds : 25 Go par torrent, 3 par heure pour la vidéo (le disque et le VPN ne se remplissent pas d'un coup).
    `current` : le filtre déjà présent dans autobrr, dont on garde les listes (résolutions, sources...) que l'utilisateur a pu remplir.
    La qualité vidéo (`apply_quality`) n'est reposée qu'à la création et quand le préréglage Recyclarr change."""
    spec = next(f for f in AUTOBRR_FILTERS if f["kind"] == kind)
    lists = {name: list((current or {}).get(name) or []) for name in AUTOBRR_FILTER_LISTS}
    # Comme autobrr à la création d'un filtre : sans type d'annonce, « nouveautés » (NEW). Vide, le filtre prendrait aussi les
    # re-publications et les promotions de torrents anciens.
    lists["announce_types"] = lists["announce_types"] or ["NEW"]
    if kind == "video" and (apply_quality or current is None):
        lists.update({name: list(values) for name, values in AUTOBRR_VIDEO_QUALITY[preset].items()})
    categories = {name: (current or {}).get(name) or spec[name] for name in ("match_categories", "except_categories")}
    return {
        **lists, **categories,
        "name": spec["name"], "enabled": True, "priority": 10, "freeleech": True, "max_size": "25GB",
        "max_downloads": spec["per_hour"], "max_downloads_unit": "HOUR", "indexers": [{"id": i["id"]} for i in indexers],
        "actions": [{"name": "qBittorrent : agrégateur", "type": "QBITTORRENT", "enabled": True, "client_id": client_id,
                     "category": "agregateur", "tags": "autobrr,nouveautes," + spec["tag"], "paused": False, "ignore_rules": False}],
    }


def autobrr_filter_detail(ident, cookie):
    """Le filtre complet (la liste des filtres est abrégée : ni listes ni actions)."""
    result = autobrr_api("GET", f"/filters/{ident}", cookie=cookie)
    return (result.json() or {}) if result.ok else {}


def autobrr_reuse_actions(payload, detail, cookie):
    """Les actions déjà dans le filtre gardent leur identifiant : sans lui, autobrr en ajoute une de plus à chaque mise à jour et chaque
    nouveauté part plusieurs fois vers qBittorrent. Les doublons déjà créés (même nom) sont supprimés."""
    present = {}
    for action in (detail or {}).get("actions") or []:
        if action.get("name") in present:
            autobrr_api("DELETE", f"/actions/{action['id']}", cookie=cookie)
        else:
            present[action.get("name")] = action["id"]
    for action in payload.get("actions", []):
        if action["name"] in present:
            action["id"] = present[action["name"]]


def autobrr_state():
    try:
        data = json.loads(AUTOBRR_FEEDS_STATE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def autobrr_state_write(**fields):
    STATE.mkdir(parents=True, exist_ok=True)
    AUTOBRR_FEEDS_STATE.write_text(json.dumps({**autobrr_state(), **fields}) + "\n")


def provision_autobrr_filter(cookie):
    """Crée (ou met à jour, sans jamais le réactiver s'il a été coupé) les filtres de nouveautés freeleech sur tous les indexeurs d'autobrr :
    films et séries (qualité du préréglage Recyclarr), musique, livres."""
    clients = [c for c in (autobrr_api("GET", "/downloaders", cookie=cookie).json() or []) if c["name"] == "qBittorrent"]
    if not clients:
        return
    indexers = [i for i in (autobrr_api("GET", "/indexer", cookie=cookie).json() or []) if i.get("enabled", True)]
    preset = autobrr_quality_preset()
    apply_quality = autobrr_state().get("preset") != preset
    failed = []
    for spec in AUTOBRR_FILTERS:
        try:
            provision_autobrr_one(cookie, spec, clients[0]["id"], indexers, preset, apply_quality)
        except SeedboxError as error:
            failed.append(f"{spec['name']} : {error}")
    if failed:
        raise SeedboxError(" ; ".join(failed[:3]))
    autobrr_state_write(preset=preset)
    if not indexers:
        say("autobrr : aucun indexeur actif, les filtres ne s'appliqueront qu'après l'ajout d'un indexeur dans autobrr")


def provision_autobrr_one(cookie, spec, client_id, indexers, preset, apply_quality):
    kind = spec["kind"]
    payload = autobrr_filter_payload(indexers, client_id, None, kind, preset)
    existing = [f for f in (autobrr_api("GET", "/filters", cookie=cookie).json() or []) if f["name"] == spec["name"]]
    if existing:
        # Le détail porte les listes que l'utilisateur a pu régler et les actions déjà posées
        detail = autobrr_filter_detail(existing[0]["id"], cookie)
        payload = autobrr_filter_payload(indexers, client_id, detail, kind, preset, apply_quality)
        autobrr_reuse_actions(payload, detail, cookie)
        payload["enabled"] = existing[0].get("enabled", True)
        result = autobrr_api("PUT", f"/filters/{existing[0]['id']}", {**payload, "id": existing[0]["id"]}, cookie)
    else:
        say(f"autobrr : filtre « {spec['name']} »")
        # autobrr refuse (HTTP 500) un filtre réduit à son nom : on envoie le filtre complet, puis en secours sans les actions
        # (complétées ensuite par une mise à jour), en gardant la réponse de la dernière tentative pour le message d'erreur
        created = None
        for attempt in ({**payload, "enabled": False}, {**{k: v for k, v in payload.items() if k != "actions"}, "enabled": False, "actions": []}):
            created = autobrr_api("POST", "/filters", attempt, cookie)
            if created.ok:
                break
        if not created.ok:
            raise SeedboxError(f"filtre refusé (HTTP {created.status}) {created.text[:200]}")
        ident = (created.json() or {}).get("id")
        if ident is None:
            ident = next((f["id"] for f in (autobrr_api("GET", "/filters", cookie=cookie).json() or []) if f["name"] == spec["name"]), None)
        if ident is None:
            raise SeedboxError("filtre créé mais introuvable")
        # L'action envoyée à la création existe déjà : la mise à jour qui suit la reprend au lieu d'en ajouter une seconde
        autobrr_reuse_actions(payload, autobrr_filter_detail(ident, cookie), cookie)
        result = autobrr_api("PUT", f"/filters/{ident}", {**payload, "id": ident}, cookie)
    if not result.ok:
        raise SeedboxError(f"filtre refusé (HTTP {result.status}) {result.text[:150]}")


AUTOBRR_FEEDS_STATE = STATE / "autobrr-flux.json"


def autobrr_feed_url(prowlarr_id):
    """Adresse Torznab d'un indexeur de Prowlarr, telle que la voit autobrr (même réseau Docker que cross-seed)."""
    return f"http://prowlarr:9696{BASE_PATHS['prowlarr']}/{prowlarr_id}/api"


def autobrr_start_feed(feed_id, cookie):
    """autobrr n'ordonnance un flux créé par l'API qu'au prochain démarrage d'autobrr (dernière et prochaine exécution restent à
    0001-01-01 et il ne tourne jamais) : le basculer sur « actif » le planifie tout de suite."""
    return autobrr_api("PATCH", f"/feeds/{feed_id}/enabled", {"enabled": True}, cookie).ok


def autobrr_feed_unscheduled(feed, indexer_enabled):
    """Flux actif, d'un indexeur actif, sans prochaine exécution : jamais ordonnancé. Un flux ou un indexeur coupé par l'utilisateur
    n'en fait pas partie."""
    next_run = str(feed.get("next_run") or "")
    return bool(feed.get("enabled") and (not next_run or next_run.startswith("0001-"))
                and indexer_enabled.get((feed.get("indexer") or {}).get("id"), True))


def autobrr_sync_indexers(cookie):
    """Chaque indexeur torrent actif de Prowlarr devient un indexeur « Prowlarr : nom » d'autobrr, lu en Torznab : autobrr voit alors
    les nouveautés de tous les trackers de la console, freeleech compris (Prowlarr transmet le facteur de téléchargement). Un indexeur
    est reconnu à son adresse Torznab, jamais à son nom : celui qu'un utilisateur a ajouté à la main n'est ni doublé ni renommé.
    Un flux existant n'est modifié que si l'adresse ou la clé de Prowlarr a changé ; son état (actif ou coupé), son intervalle et ses
    catégories restent ceux de l'utilisateur. Renvoie le nombre d'indexeurs ajoutés ou corrigés."""
    wanted = [i for i in prowlarr_indexers() if i.get("enable") and i.get("protocol") == "torrent" and i.get("id") is not None]
    if not wanted:
        return 0
    api_key = env_read()["PROWLARR_API_KEY"]
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    known_digest = autobrr_state().get("key")
    feeds = autobrr_api("GET", "/feeds", cookie=cookie)
    if not feeds.ok:
        raise SeedboxError(f"autobrr : liste des flux refusée (HTTP {feeds.status}) {feeds.text[:200]}")
    by_url = {f.get("url"): f for f in (feeds.json() or [])}
    indexers = autobrr_api("GET", "/indexer", cookie=cookie)
    known = (indexers.json() or []) if indexers.ok else []
    by_name = {i["name"]: i for i in known}
    indexer_enabled = {i.get("id"): i.get("enabled", True) for i in known}
    changed, failed = 0, []
    for indexer in wanted:
        url, name = autobrr_feed_url(indexer["id"]), "Prowlarr : " + str(indexer["name"]).strip()
        feed = by_url.get(url)
        if feed:
            if known_digest == digest:
                if autobrr_feed_unscheduled(feed, indexer_enabled):
                    if autobrr_start_feed(feed["id"], cookie):
                        say(f"autobrr : flux {name} lancé")
                    else:
                        failed.append(f"{name} : flux non lancé")
                continue
            result = autobrr_api("PUT", f"/feeds/{feed['id']}", {**feed, "url": url, "api_key": api_key}, cookie)
            if not result.ok:
                failed.append(f"{name} : flux refusé (HTTP {result.status}) {result.text[:120]}")
            else:
                changed += 1
            continue
        # Indexeur déjà là sans son flux (création interrompue) : on le réutilise au lieu de le doubler
        existing = by_name.get(name)
        created_here = existing is None
        if created_here:
            result = autobrr_api("POST", "/indexer", {"name": name, "identifier": "torznab", "implementation": "torznab",
                                                       "enabled": True, "settings": {}}, cookie)
            if not result.ok:
                failed.append(f"{name} : indexeur refusé (HTTP {result.status}) {result.text[:120]}")
                continue
            existing = result.json() or {}
        # Les listes (categories) restent présentes et vides : autobrr les range dans des colonnes NOT NULL
        result = autobrr_api("POST", "/feeds", {
            "name": name, "enabled": True, "type": "TORZNAB", "url": url, "api_key": api_key, "interval": 30, "timeout": 60,
            "indexer_id": existing.get("id"), "categories": [], "capabilities": None,
            "settings": {"download_type": "TORRENT"}}, cookie)
        if not result.ok:
            failed.append(f"{name} : flux refusé (HTTP {result.status}) {result.text[:120]}")
            if created_here and existing.get("id") is not None:
                autobrr_api("DELETE", f"/indexer/{existing['id']}", cookie=cookie)     # pas d'indexeur orphelin
            continue
        changed += 1
        say(f"autobrr : indexeur {name}")
        feed_id = (result.json() or {}).get("id")
        if feed_id is None or not autobrr_start_feed(feed_id, cookie):
            failed.append(f"{name} : flux créé mais non lancé")       # repris au prochain contrôle (flux sans prochaine exécution)
    if failed:
        raise SeedboxError(" ; ".join(failed[:3]))
    autobrr_state_write(key=digest)
    return changed


def autobrr_follow_prowlarr():
    """Contrôle horaire : les indexeurs ajoutés dans Prowlarr (ou Jackett, qui passe par Prowlarr) rejoignent autobrr et son filtre."""
    if not container_running("autobrr"):
        return
    cookie = autobrr_session()
    if autobrr_sync_indexers(cookie) or autobrr_state().get("preset") != autobrr_quality_preset():
        provision_autobrr_filter(cookie)            # nouveaux indexeurs à couvrir, ou préréglage Recyclarr changé depuis


PROVISION_STATUS = STATE / "provision-status.json"
UPGRADE_LOG = Path("/var/log/seedboxbuntu-upgrade.log")
# Seules ces étapes font échouer un déploiement ou une mise à jour (qui sont alors relancés). Les autres
# (Jackett, applications ajoutées...) sont signalées dans la console et réessayées toutes les heures :
# une application annexe en panne ne doit ni bloquer l'appliance ni redémarrer la connexion unique.
CRITICAL_STEPS = {"Jellyfin", "Radarr/Sonarr/Prowlarr", "qBittorrent : emplacements", "Pile Docker"}


def provision_steps():
    return [("Jellyfin", provision_jellyfin), ("Radarr/Sonarr/Prowlarr", provision_arr),
            ("Jackett", provision_jackett), ("FlareSolverr dans Prowlarr", provision_prowlarr_flaresolverr),
            ("qBittorrent : emplacements", lambda: shares().apply_qbittorrent()),
            ("Applications ajoutées", provision_apps), ("Seerr", provision_seerr), ("Dockge", provision_dockge),
            ("autobrr", provision_autobrr),
            ("Jackett vers Prowlarr", jackett_sync), ("trackers", trackers_sync)]


def steps_load():
    try:
        data = json.loads(PROVISION_STATUS.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def steps_record(label, error):
    STATE.mkdir(parents=True, exist_ok=True)
    data = steps_load()
    previous = data.get(label, {})
    data[label] = {"ok": error is None, "error": error or "", "time": time.strftime("%FT%T%z"),
                   "critical": label in CRITICAL_STEPS,
                   "ignored": bool(previous.get("ignored")) and error is not None}
    tmp = PROVISION_STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(PROVISION_STATUS)


STEP_ERRORS = (Exception,)


def run_step(label, step):
    """Exécute une étape et enregistre son résultat ; renvoie le message d'erreur ou None."""
    try:
        step()
    except STEP_ERRORS as error:  # une application en panne ne doit pas bloquer les autres
        message = str(error) or error.__class__.__name__
        steps_record(label, message)
        say(f"{label} : ÉCHEC : {message}")
        return message
    steps_record(label, None)
    say(f"{label} : OK")
    return None


def provision():
    """Relie les applications ; une étape en échec n'empêche pas les suivantes. Seules les erreurs des
    étapes critiques sont renvoyées (elles relancent le déploiement ou la mise à jour)."""
    errors = []
    for label, step in provision_steps():
        message = run_step(label, step)
        if message and label in CRITICAL_STEPS:
            errors.append(f"{label} : {message}")
    try:
        compose("up", "-d", "--remove-orphans", capture=True)
        steps_record("Pile Docker", None)
    except SeedboxError as error:
        steps_record("Pile Docker", str(error))
        errors.append(str(error))
    failed = [l for l, v in steps_load().items() if not v["ok"] and not v["critical"] and not v["ignored"]]
    if failed:
        say("à surveiller (réessayé chaque heure, voir la console) : " + ", ".join(failed))
    return errors


def steps_retry():
    """Relance les étapes non critiques en échec (tâche horaire et bouton de la console)."""
    wanted = {l for l, v in steps_load().items() if not v["ok"] and not v["critical"] and not v["ignored"]}
    for label, step in provision_steps():
        if label in wanted:
            run_step(label, step)


def steps_ignore(label):
    data = steps_load()
    if label not in data or data[label]["ok"]:
        raise SeedboxError("étape inconnue ou sans erreur")
    data[label]["ignored"] = True
    PROVISION_STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def upgrade_skip():
    """Abandonne une mise à jour qui échoue en boucle : le marqueur est retiré, le service s'arrête."""
    pending = STATE / "upgrade-pending"
    if not pending.exists():
        raise SeedboxError("aucune mise à jour en attente")
    detail = pending.read_text().strip()
    with open(STATE / "upgrade-history", "a") as handle:
        handle.write(f"{time.strftime('%FT%T%z')} {detail} ABANDONNÉE par l'administrateur\n")
    pending.unlink()
    run("systemctl", "stop", "seedbox-upgrade", check=False, capture=True)


def tail_file(path, lines=40):
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def steps_status():
    pending = STATE / "upgrade-pending"
    history = tail_file(STATE / "upgrade-history", 5).splitlines()
    active = run("systemctl", "is-active", "seedbox-upgrade", check=False, capture=True)
    return {"steps": [{"label": label, **value} for label, value in steps_load().items()],
            "upgrade": {"pending": pending.exists(), "detail": pending.read_text().strip() if pending.exists() else "",
                        "service": (active.stdout or "").strip() or "inconnu", "history": history,
                        "log": tail_file(UPGRADE_LOG) if pending.exists() else ""}}


# ----------------------------------------------------------------------------
#  Trackers
# ----------------------------------------------------------------------------
def load_trackers():
    if TRACKERS_FILE.exists():
        return json.loads(TRACKERS_FILE.read_text())
    return {}


def save_trackers(rules):
    TRACKERS_FILE.write_text(json.dumps(rules, indent=2, ensure_ascii=False) + "\n")


def tracker_keyword(indexer):
    if indexer["name"].startswith("Jackett : "):   # tous partagent l'hôte « jackett » : le nom les distingue
        return re.sub(r"[^a-z0-9]+", "", indexer["name"][len("Jackett : "):].lower())
    urls = indexer.get("indexerUrls") or []
    host = urllib.parse.urlsplit(urls[0]).hostname if urls else ""
    name = (host or indexer["name"]).lower().removeprefix("www.")
    parts = name.split(".")
    if re.fullmatch(r"[0-9.]+", name) or len(parts) < 2:
        return name
    return parts[-2]


def prowlarr_indexers():
    if not container_running("prowlarr"):
        return []
    result = arr_api("prowlarr", "GET", "/indexer")
    return result.json() if result.ok else []


def write_if_changed(target, text, env):
    """Compare au dernier rendu plutôt qu'au fichier : qbit_manage complète lui-même le sien."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    marker = STATE / "rendus" / f"{target.parent.name}-{target.name}.sha256"
    if target.exists() and marker.exists() and marker.read_text() == digest:
        return False
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chown(target, int(env["PUID"]), int(env["PGID"]))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(digest)
    return True


def write_cross_seed(env, indexers):
    api_key = env.get("PROWLARR_API_KEY", "")
    torznab = [f"http://prowlarr:9696/prowlarr/{i['id']}/api?apikey={api_key}"
               for i in indexers if i.get("enable") and i.get("protocol") == "torrent"]
    config = {
        "apiKey": env.get("CROSS_SEED_API_KEY", ""), "torznab": torznab, "host": "0.0.0.0", "port": 2468,
        "torrentClients": [f"qbittorrent:http://{env['QBIT_USERNAME']}:{env['QBIT_PASSWORD']}@{qbit_host()}:8080"],
        "useClientTorrents": True, "delay": 30, "dataDirs": [], "linkCategory": "cross-seed-link",
        # Un lien physique n'est possible que sur le volume des données : un dossier par volume
        "linkDirs": sorted({shares().location("complete").rstrip("/") + "/cross-seed", "/data/downloads/cross-seed"}),
        "linkType": "hardlink", "flatLinking": False,
        "matchMode": "flexible", "skipRecheck": True, "autoResumeMaxDownload": 52428800,
        "maxDataDepth": 2, "action": "inject", "duplicateCategories": False,
        "rssCadence": "30 minutes", "searchCadence": "1 day", "excludeOlder": "2 weeks",
        "excludeRecentSearch": "3 days", "snatchTimeout": "30 seconds", "searchTimeout": "2 minutes",
        "searchLimit": 400,
    }
    target = CONFIG / "cross-seed" / "config.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = '"use strict";\n// Généré par seedboxctl trackers sync\nmodule.exports = ' + json.dumps(config, indent=2) + ";\n"
    changed = write_if_changed(target, text, env)
    return len(torznab), changed


def trackers_sync():
    """Propage les indexeurs de Prowlarr vers qbit_manage et cross-seed."""
    env = env_read()
    indexers = prowlarr_indexers()
    rules = load_trackers()
    changed = False
    for indexer in indexers:
        keyword = tracker_keyword(indexer)
        if keyword and keyword not in rules:
            rules[keyword] = {"name": indexer["name"], "tag": keyword, "min_seed_hours": 0, "max_ratio": -1}
            changed = True
    if changed:
        save_trackers(rules)
    restart = []
    if render_qbm(env):
        restart.append("qbit-manage")
    count, changed = write_cross_seed(env, indexers)
    if changed:
        restart.append("cross-seed")
    for service in restart:
        if container_running(service):
            run("docker", "restart", service, capture=True)
    say(f"trackers : {len(indexers)} indexeur(s), {count} dans cross-seed")
    try:
        autobrr_follow_prowlarr()
    except (SeedboxError, OSError, ValueError, KeyError, http.client.HTTPException) as error:
        say(f"autobrr : indexeurs de Prowlarr non repris ({error})")


def render_qbm(env):
    source = SHARE / "templates" / "config" / "qbit-manage" / "config.yml"
    target = CONFIG / "qbit-manage" / "config.yml"
    return write_if_changed(target, substitute(source.read_text(), template_values(env)), env)


def tracker_rule(keyword, min_seed_hours=None, max_ratio=None, tag=None):
    rules = load_trackers()
    rule = rules.setdefault(keyword, {"name": keyword, "tag": keyword, "min_seed_hours": 0, "max_ratio": -1})
    if min_seed_hours is not None:
        rule["min_seed_hours"] = max(0, int(min_seed_hours))
    if max_ratio is not None:
        rule["max_ratio"] = float(max_ratio)
    if tag:
        rule["tag"] = tag
    save_trackers(rules)
    trackers_sync()


def qbit_login():
    env = env_read()
    result = request("POST", svc(qbit_host(), 8080) + "/api/v2/auth/login", form={
        "username": env["QBIT_USERNAME"], "password": env["QBIT_PASSWORD"]}, timeout=15)
    if not result.ok:
        raise SeedboxError(f"qBittorrent : connexion refusée (HTTP {result.status})")
    return result.cookies()


def tracker_stats():
    """Statistiques réelles par tracker, calculées depuis qBittorrent."""
    if not container_running("qbittorrent"):
        return []
    cookie = qbit_login()
    base = svc(qbit_host(), 8080) + "/api/v2"
    torrents = request("GET", base + "/torrents/info", headers={"Cookie": cookie}, timeout=30).json() or []
    stats = {}
    for torrent in torrents:
        host = urllib.parse.urlsplit(torrent.get("tracker") or "").hostname or "inconnu"
        entry = stats.setdefault(host, {"tracker": host, "torrents": 0, "uploaded": 0, "downloaded": 0, "upspeed": 0})
        entry["torrents"] += 1
        entry["uploaded"] += torrent.get("uploaded", 0)
        entry["downloaded"] += torrent.get("downloaded", 0)
        entry["upspeed"] += torrent.get("upspeed", 0)
    for entry in stats.values():
        entry["ratio"] = round(entry["uploaded"] / entry["downloaded"], 2) if entry["downloaded"] else None
    return sorted(stats.values(), key=lambda e: e["uploaded"], reverse=True)


def vpn_port():
    """Port entrant : valeur configurée dans qBittorrent, mise à jour par gluetun."""
    if not container_running("qbittorrent"):
        return None
    cookie = qbit_login()
    return request("GET", svc(qbit_host(), 8080) + "/api/v2/app/preferences",
                   headers={"Cookie": cookie}, timeout=15).json().get("listen_port")


# ----------------------------------------------------------------------------
#  Comptes
# ----------------------------------------------------------------------------
def user_list():
    users = users_load()
    return [{"login": login, "role": "admin" if "admins" in user["groups"] else "manager" if "managers" in user["groups"]
            else "observer" if "observers" in user["groups"] else "user"}
            for login, user in sorted(users.items())]


AUTH_MODES = ("none", "admins", "all")


def auth_devices():
    """Comptes ayant enregistré une seconde étape (application TOTP ou clé de sécurité) dans Authelia."""
    found = set()
    try:
        with sqlite3.connect(f"file:{CONFIG / 'authelia' / 'db.sqlite3'}?mode=ro", uri=True) as con:
            for table in ("totp_configurations", "webauthn_credentials"):
                try:
                    found |= {row[0] for row in con.execute(f"SELECT username FROM {table}")}
                except sqlite3.Error:
                    pass
    except sqlite3.Error:
        pass
    return found


def auth_overview():
    devices = auth_devices()
    return {"mode": env_read().get("AUTH_2FA", "none"),
            "users": [{**user, "second_factor": user["login"] in devices} for user in user_list()],
            # Authelia écrit ici les codes et liens de vérification d'identité (pas de serveur de messagerie)
            "notifications": tail_file(CONFIG / "authelia" / "notification.txt", 30)}


def auth_set_2fa(mode):
    if mode not in AUTH_MODES:
        raise SeedboxError("mode inconnu : none, admins ou all")
    if mode != "none":
        # Évite de s'enfermer dehors : chaque compte concerné doit déjà avoir enregistré sa seconde étape
        devices = auth_devices()
        missing = [u["login"] for u in user_list() if (mode == "all" or u["role"] == "admin") and u["login"] not in devices]
        if missing:
            raise SeedboxError("enregistrez d'abord une seconde étape (page /auth/settings) pour : " + ", ".join(missing))
    env_set(AUTH_2FA=mode)
    render_templates()
    run("docker", "restart", "authelia", capture=True)
    wait_http("authelia", 9091, "/auth/api/health", timeout=90)
    say(f"double authentification : {mode}")


def seerr_user_id(jellyfin_id):
    users = seerr_api("GET", "/user?take=1000").json().get("results", [])
    wanted = jellyfin_id.replace("-", "").lower()
    for user in users:
        if (user.get("jellyfinUserId") or "").replace("-", "").lower() == wanted:
            return user["id"]
    return None


def user_add(login, password, role="user"):
    validate_account(login, password)
    if role not in ROLES:
        raise SeedboxError("rôle invalide : admin, manager, user ou observer")
    if login in users_load():
        raise SeedboxError(f"le compte {login} existe déjà")
    warnings = []
    result = jellyfin_api("POST", "/Users/New", {"Name": login, "Password": password})
    if not result.ok:
        raise SeedboxError(f"Jellyfin : création refusée (HTTP {result.status}) {result.text[:160]}")
    jellyfin = result.json()
    if role == "admin":
        policy = jellyfin["Policy"]
        policy["IsAdministrator"] = True
        jellyfin_api("POST", f"/Users/{jellyfin['Id']}/Policy", policy)
    try:
        result = seerr_api("POST", "/user/import-from-jellyfin", {"jellyfinUserIds": [jellyfin["Id"]]})
        if not result.ok:
            warnings.append(f"Seerr : import refusé (HTTP {result.status})")
        elif role != "user":
            seerr_id = seerr_user_id(jellyfin["Id"])
            if seerr_id:
                seerr_api("POST", f"/user/{seerr_id}/settings/permissions", {"permissions": SEERR_PERMISSIONS[role]})
    except (SeedboxError, OSError, ValueError) as error:
        warnings.append(f"Seerr : {error}")
    users_store_add(login, password, role)
    return warnings


def user_set_role(login, role):
    """Change le rôle d'un compte existant partout : Authelia, Jellyfin, Seerr."""
    if role not in ROLES:
        raise SeedboxError("rôle invalide : admin, manager, user ou observer")
    users = users_load()
    if login not in users:
        raise SeedboxError(f"compte {login} inconnu")
    if login == env_read()["ADMIN_USER"] and role != "admin":
        raise SeedboxError("le compte administrateur principal reste administrateur")
    if sum(1 for u in user_list() if u["role"] == "admin" and u["login"] != login) == 0 and role != "admin":
        raise SeedboxError("il doit rester au moins un administrateur")
    warnings = []
    jellyfin = jellyfin_user(login)
    if jellyfin:
        policy = jellyfin["Policy"]
        policy["IsAdministrator"] = role == "admin"
        result = jellyfin_api("POST", f"/Users/{jellyfin['Id']}/Policy", policy)
        if not result.ok:
            warnings.append(f"Jellyfin : droits non modifiés (HTTP {result.status})")
        try:
            seerr_id = seerr_user_id(jellyfin["Id"])
            if seerr_id:
                seerr_api("POST", f"/user/{seerr_id}/settings/permissions", {"permissions": SEERR_PERMISSIONS[role]})
        except (SeedboxError, OSError, ValueError) as error:
            warnings.append(f"Seerr : {error}")
    users[login]["groups"] = [ROLE_GROUPS[role]]
    users_save(users)
    say(f"{login} : rôle {role}" + (" (reconnexion nécessaire pour appliquer les nouveaux droits)" if role != "admin" else ""))
    return warnings


def user_passwd(login, password):
    validate_account(login, password)
    users = users_load()
    if login not in users:
        raise SeedboxError(f"compte {login} inconnu")
    warnings = []
    jellyfin = jellyfin_user(login)
    if jellyfin:
        result = jellyfin_api("POST", f"/Users/{jellyfin['Id']}/Password", {"NewPw": password, "ResetPassword": False})
        if not result.ok:
            warnings.append(f"Jellyfin : changement refusé (HTTP {result.status})")
    users[login]["password"] = authelia_hash(password)
    users_save(users)
    if login == env_read()["ADMIN_USER"]:
        # Le mot de passe choisi par l'administrateur n'est conservé nulle part en clair
        env_set(ADMIN_INITIAL_PASSWORD="")
    return warnings


def user_del(login):
    users = users_load()
    if login not in users:
        raise SeedboxError(f"compte {login} inconnu")
    if login == env_read()["ADMIN_USER"]:
        raise SeedboxError("le compte administrateur principal ne peut pas être supprimé")
    warnings = []
    jellyfin = jellyfin_user(login)
    if jellyfin:
        try:
            seerr_id = seerr_user_id(jellyfin["Id"])
            if seerr_id:
                seerr_api("DELETE", f"/user/{seerr_id}")
        except (SeedboxError, OSError, ValueError) as error:
            warnings.append(f"Seerr : {error}")
        jellyfin_api("DELETE", f"/Users/{jellyfin['Id']}")
    del users[login]
    users_save(users)
    return warnings


# ----------------------------------------------------------------------------
#  Secrets de service
# ----------------------------------------------------------------------------
def qbit_set_password(password):
    """Applique un mot de passe à qBittorrent : par son API si la console peut s'y connecter, sinon par le
    fichier de configuration, conteneur arrêté (mot de passe de la console périmé : changé dans l'interface)."""
    cookie = None
    running = container_running("qbittorrent")
    if running:
        try:
            cookie = qbit_login()
        except (SeedboxError, OSError, ValueError):
            cookie = None
    if cookie:
        result = request("POST", svc(qbit_host(), 8080) + "/api/v2/app/setPreferences", headers={"Cookie": cookie},
                         form={"json": json.dumps({"web_ui_password": password})}, timeout=20)
        if not result.ok:
            raise SeedboxError(f"qBittorrent : changement refusé (HTTP {result.status})")
    else:
        conf = CONFIG / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"
        if not conf.exists():
            raise SeedboxError("qBittorrent : fichier de configuration introuvable")
        if running:
            compose("stop", "qbittorrent", capture=True)      # il réécrit sa configuration en s'arrêtant
        line = f'WebUI\\Password_PBKDF2="@ByteArray({qbit_pbkdf2(password)})"'
        text = conf.read_text()
        if re.search(r'WebUI\\Password_PBKDF2=".*"', text):
            text = re.sub(r'WebUI\\Password_PBKDF2=".*"', lambda m: line, text)
        elif "[Preferences]" in text:
            text = text.replace("[Preferences]", "[Preferences]\n" + line, 1)
        else:
            text += "\n[Preferences]\n" + line + "\n"
        conf.write_text(text)
        if running:
            compose("up", "-d", "qbittorrent", capture=True)
    env_set(QBIT_PASSWORD=password)


def autobrr_set_password(password):
    result = autobrr_api("PATCH", "/auth/user/admin", {
        "username_current": "admin", "password_current": env_read()["AUTOBRR_ADMIN_PASSWORD"],
        "password_new": password}, cookie=autobrr_session())
    if not result.ok:
        raise SeedboxError(f"autobrr : changement refusé (HTTP {result.status})")
    env_set(AUTOBRR_ADMIN_PASSWORD=password)


def rotate(service):
    if service == "qbittorrent":
        qbit_set_password(hex_secret(16))
    elif service in ("radarr", "sonarr", "prowlarr"):
        env_set(**{f"{service.upper()}_API_KEY": hex_secret(16)})
    elif service == "autobrr":
        autobrr_set_password(hex_secret(16))
    else:
        raise SeedboxError("service à renouveler : qbittorrent, autobrr, radarr, sonarr ou prowlarr")
    compose("up", "-d", capture=True)
    return provision()


# ----------------------------------------------------------------------------
#  Domaine et VPN
# ----------------------------------------------------------------------------
def valid_domain(domain):
    return bool(re.fullmatch(r"(?=.{4,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", domain))


def set_domain(domain="", email=""):
    domain = domain.strip().lower().rstrip(".")
    email = email.strip()
    if domain and not valid_domain(domain):
        raise SeedboxError("nom de domaine invalide")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise SeedboxError("adresse e-mail invalide")
    if email and not domain:
        raise SeedboxError("Let's Encrypt exige votre propre domaine")
    env_set(DOMAIN_MODE="custom" if domain else "auto", DOMAIN=domain or auto_address(), ACME_EMAIL=email)
    return apply_addresses()


def apply_addresses():
    """Adresses servies modifiées (IP, nom du serveur, domaine) : certificat, cookies de session
    Authelia, adresses autorisées par Homepage et adresses publiques des applications."""
    update_addresses()
    ensure_certificates(env_read())
    render_templates()
    set_ownership()
    compose("up", "-d", capture=True)
    run("docker", "restart", "traefik", "authelia", capture=True)
    try:  # la connexion unique doit répondre avant que la console ne recharge la page
        wait_http("authelia", 9091, "/auth/api/health", timeout=90)
    except SeedboxError as error:
        say(f"Authelia : {error}")
    return provision()


def set_vpn(provider, private_key, addresses, countries="", port_forwarding=False,
            endpoint_ip="", endpoint_port="", public_key=""):
    if not provider or not private_key:
        raise SeedboxError("fournisseur et clé privée WireGuard requis")
    if not re.fullmatch(r"[a-z0-9 ]{2,40}", provider):
        raise SeedboxError("fournisseur invalide")
    for label, value in (("clé privée", private_key), ("adresse", addresses), ("point d'accès", endpoint_ip),
                         ("port", endpoint_port), ("clé publique", public_key), ("pays", countries)):
        check_env_value(label, value)
    forwardable = provider in ("protonvpn", "private internet access", "privatevpn")
    if port_forwarding and not forwardable:
        raise SeedboxError("redirection de port disponible avec protonvpn, private internet access et privatevpn")
    env_set(VPN_SERVICE_PROVIDER=provider, WIREGUARD_PRIVATE_KEY=private_key, WIREGUARD_ADDRESSES=addresses,
            SERVER_COUNTRIES=countries, VPN_PORT_FORWARDING="on" if port_forwarding else "off",
            WIREGUARD_ENDPOINT_IP=endpoint_ip, WIREGUARD_ENDPOINT_PORT=endpoint_port,
            WIREGUARD_PUBLIC_KEY=public_key)
    qbit_switch("vpn")
    return apply_qbit_mode()


def vpn_disable():
    """Retour au mode direct : qBittorrent quitte le tunnel et sort par le réseau du serveur."""
    qbit_switch("direct")
    return apply_qbit_mode()


def apply_qbit_mode():
    """Démarre qBittorrent dans son mode, puis reporte son adresse dans toutes les applications."""
    render_templates()
    compose("up", "-d", "--remove-orphans", capture=True)
    maintenance().refresh_firewall()
    return provision()


def public_ip():
    """Adresse de sortie vue par gluetun : vérifie que le tunnel transporte bien le trafic."""
    if not container_running("gluetun"):
        return None
    result = run("docker", "exec", "gluetun", "wget", "-q", "-T", "8", "-O-", "https://api.ipify.org",
                 check=False, capture=True)
    return result.stdout.strip() or None


# ----------------------------------------------------------------------------
#  Jeton GitHub, images Docker et retrait des anciennes applications
# ----------------------------------------------------------------------------
def github_env():
    """Le jeton passe par l'environnement de git : jamais dans la ligne de commande."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}"})
    return env


RETIRED_MARKER = STATE / "retired-apps"
RETIRED_GRACE = 6 * 3600          # images et sources gardées ce temps après la mise à jour : un retour arrière doit pouvoir les relancer


def retire_old_apps(purge=False):
    """Retire p2p-aggregator et tracker-session (remplacés par l'agrégateur intégré). Les conteneurs sont supprimés tout de suite ;
    images, sources et archive d'image ne le sont que RETIRED_GRACE secondes plus tard (purge=True, depuis refresh) : si la mise
    à jour est annulée, l'ancienne version doit pouvoir les relancer. Le dossier de données (config/p2p-aggregator : base,
    comptes de trackers) n'est jamais effacé."""
    leftovers = [APPS_DIR / "p2p-aggregator", STATE / "images" / "p2p-aggregator.tar.zst"]
    if not purge:
        for name in RETIRED_CONTAINERS:
            run("docker", "rm", "-f", name, check=False, capture=True)
        if any(path.exists() for path in leftovers) and not RETIRED_MARKER.exists():
            RETIRED_MARKER.parent.mkdir(parents=True, exist_ok=True)
            RETIRED_MARKER.write_text(str(int(time.time())) + "\n")
        return
    try:
        since = int(RETIRED_MARKER.read_text().strip())
    except (OSError, ValueError):
        since = None
    if since is None or time.time() - since < RETIRED_GRACE:
        return
    for image in RETIRED_IMAGES:
        for tag in ("current", "previous", "candidate"):
            run("docker", "rmi", "-f", f"{image}:{tag}", check=False, capture=True)
    for leftover in leftovers:
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
        elif leftover.exists():
            leftover.unlink()
    RETIRED_MARKER.unlink(missing_ok=True)
    say("p2p-aggregator : image et sources supprimées (ses données restent dans config/p2p-aggregator)")


def github_set_token(token):
    token = token.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token):
        raise SeedboxError("jeton GitHub invalide")
    ETC.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token + "\n")


def docker_image_exists(image):
    return run("docker", "image", "inspect", image, check=False, capture=True).returncode == 0


def wait_healthy(name, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run("docker", "inspect", "-f", "{{.State.Health.Status}}", name, check=False, capture=True)
        state = result.stdout.strip()
        if state == "healthy":
            return True
        if state == "unhealthy":
            return False
        time.sleep(3)
    return False


# ----------------------------------------------------------------------------
#  État et déploiement
# ----------------------------------------------------------------------------
def version():
    path = SHARE / "VERSION"
    return path.read_text().strip() if path.exists() else "dev"


def trackers_overview():
    indexers = prowlarr_indexers()
    return {"rules": load_trackers(), "stats": tracker_stats(),
            "indexers": [{"name": i["name"], "enabled": i.get("enable"), "privacy": i.get("privacy")} for i in indexers]}


def vpn_overview():
    env = env_read()
    states = containers().get("gluetun", {})
    return {"enabled": "vpn" in env.get("COMPOSE_PROFILES", ""), "mode": qbit_mode(),
            "provider": env.get("VPN_SERVICE_PROVIDER", ""),
            "port_forwarding": env.get("VPN_PORT_FORWARDING") == "on", "state": states.get("health") or states.get("state"),
            "ip": public_ip(), "port": vpn_port()}


def containers():
    result = compose("ps", "-a", "--format", "json", check=False, capture=True)
    states = {}
    for line in (result.stdout or "").splitlines():
        if line.strip():
            item = json.loads(line)
            # qBittorrent a deux services (vpn, direct) mais une seule application
            name = "qbittorrent" if item["Service"] == "qbittorrent-direct" else item["Service"]
            states[name] = {"state": item.get("State"), "health": item.get("Health") or ""}
    return states


def status():
    env = env_read()
    states = containers()
    domain = env.get("DOMAIN", "")
    apps = []
    for app in all_apps():
        state = states.get(app["service"], {"state": "absent", "health": ""})
        apps.append({**app, **state, "url": app_url(app, domain)})
    apps.insert(1, {"id": "console", "name": "Console", "path": "/console/", "service": "console", "login": "sso",
                    "state": "running", "health": "", "url": app_url({"path": "/console/"}, domain)})
    return {"domain": domain, "addresses": [h for h in env.get("PUBLIC_HOSTS", "").split(",") if h],
            "domain_mode": env.get("DOMAIN_MODE"), "acme": bool(env.get("ACME_EMAIL")),
            "vpn": {"enabled": "vpn" in env.get("COMPOSE_PROFILES", ""), "mode": qbit_mode(), "provider": env.get("VPN_SERVICE_PROVIDER", ""),
                    "port_forwarding": env.get("VPN_PORT_FORWARDING") == "on",
                    "state": states.get("gluetun", {}).get("health") or states.get("gluetun", {}).get("state", "arrêté")},
            "apps": apps, "deployed": (STATE / "deployed").exists(), "configured": CONFIGURED_FLAG.exists()}


def credentials():
    env = env_read()
    return {
        # Mot de passe choisi dans l'assistant ou modifié ensuite : jamais conservé en clair
        "Administrateur web": {"identifiant": env.get("ADMIN_USER"),
                               "mot de passe": env.get("ADMIN_INITIAL_PASSWORD") or "non conservé"},
        "qBittorrent": {"identifiant": env.get("QBIT_USERNAME"), "mot de passe": env.get("QBIT_PASSWORD")},
        "autobrr": {"identifiant": "admin", "mot de passe": env.get("AUTOBRR_ADMIN_PASSWORD")},
        "Radarr": {"clé API": env.get("RADARR_API_KEY")},
        "Sonarr": {"clé API": env.get("SONARR_API_KEY")},
        "Prowlarr": {"clé API": env.get("PROWLARR_API_KEY")},
        "Jackett": {"clé API": env.get("JACKETT_API_KEY")},
        "Jellyfin": {"clé API": env.get("JELLYFIN_API_KEY")},
        "Seerr": {"clé API": env.get("SEERR_API_KEY")},
        "cross-seed": {"clé API": env.get("CROSS_SEED_API_KEY")},
        **apps().credentials(),
    }


def prepare():
    """Pendant l'assistant de premier démarrage : téléchargement des images, qui ne dépend ni de
    l'adresse ni du mot de passe. Un échec n'est pas bloquant : le déploiement reprend ce qui manque."""
    for path in (STATE, ETC, APPS_DIR):
        path.mkdir(parents=True, exist_ok=True)
    shutil.copy(SHARE / "stack" / "docker-compose.yml", BASE / "docker-compose.yml")
    ensure_env()
    say("téléchargement des images Docker")
    result = compose("--profile", "vpn", "--profile", "direct", "pull", "--ignore-pull-failures", "--quiet", check=False, capture=True)
    if result.returncode != 0:
        say(f"téléchargement incomplet, repris au déploiement : {(result.stderr or '').strip()[-300:]}")


def wait_configured():
    """Le déploiement attend l'assistant : l'adresse et le mot de passe en dépendent."""
    if CONFIGURED_FLAG.exists():
        return
    say("en attente de l'assistant de premier démarrage (console du serveur)")
    while not CONFIGURED_FLAG.exists():
        time.sleep(5)
    say("configuration initiale terminée")


def refresh_firewall_safely():
    try:
        maintenance().refresh_firewall()
    except (SeedboxError, OSError) as error:
        say(f"pare-feu : {error}")


def deploy():
    """Déploiement complet, relançable : c'est ce que fait le premier démarrage."""
    if not CONFIGURED_FLAG.exists():
        prepare()
        wait_configured()
    init()
    retire_old_apps()
    say("démarrage de la stack (téléchargement des images au premier lancement)")
    compose("up", "-d", "--remove-orphans")
    refresh_firewall_safely()   # port de partage de qBittorrent (mode direct)
    errors = provision()
    if errors:
        raise SeedboxError("déploiement incomplet : " + " ; ".join(errors))
    (STATE / "deployed").write_text(time.strftime("%FT%T%z") + "\n")
    if PASSWORD_FILE.exists():
        # Appliqué partout : le mot de passe choisi dans l'assistant n'est conservé nulle part en clair
        PASSWORD_FILE.unlink()
        env_set(ADMIN_INITIAL_PASSWORD="")
    env = env_read()
    say(f"déployé : https://{env['DOMAIN']}/ (identifiant {env['ADMIN_USER']})")


def upgrade():
    """Après une mise à jour par l'ISO (seedbox-upgrade.service) : les fichiers de l'appliance
    sont déjà remplacés ; ce qui dépend du système et de la stack est appliqué ici, avec Internet.
    Rien n'est effacé : comptes, secrets, bibliothèques, partages et réglages sont conservés."""
    pending = STATE / "upgrade-pending"
    detail = pending.read_text().strip() if pending.exists() else ""
    say(f"mise à jour de l'appliance {detail}")
    module = maintenance()
    errors = module.harden()
    if (STATE / "deployed").exists():
        for path in (STATE, ETC, APPS_DIR):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copy(SHARE / "stack" / "docker-compose.yml", BASE / "docker-compose.yml")
        ensure_env()
        module.apply_apps()
        update_addresses()
        ensure_certificates(env_read())
        before = front_fingerprint()
        render_templates()
        seed_jackett(env_read())
        set_ownership()
        retire_old_apps()
        say("démarrage de la stack avec les nouveaux fichiers")
        compose("up", "-d", "--remove-orphans")
        refresh_firewall_safely()
        errors += provision()
        # Les sessions d'Authelia sont en mémoire : un redémarrage déconnecte tout le monde. Il n'a
        # lieu que si leur configuration a changé, pas à chaque nouvel essai d'une mise à jour qui échoue.
        if front_fingerprint() != before:
            run("docker", "restart", "traefik", "authelia", check=False, capture=True)
    else:
        say("déploiement initial non terminé : il appliquera les nouveaux fichiers")
    if errors:
        raise SeedboxError("mise à jour incomplète, nouvel essai plus tard : " + " ; ".join(errors))
    with open(STATE / "upgrade-history", "a") as handle:
        handle.write(f"{time.strftime('%FT%T%z')} {detail} version {version()}\n")
    pending.unlink(missing_ok=True)
    say(f"appliance à jour : version {version()}")


def refresh():
    """Tâche périodique : suit l'adresse IP et le nom du serveur, synchronise les trackers."""
    if not (STATE / "deployed").exists():
        update_addresses()
        return
    if update_addresses():
        say(f"nouvelles adresses : {env_read()['PUBLIC_HOSTS']}")
        for warning in apply_addresses():
            say(f"attention : {warning}")
    try:
        retire_old_apps(purge=True)     # supprime l'image et les sources de p2p-aggregator une fois le délai de retour arrière passé
    except (SeedboxError, OSError) as error:
        say(f"p2p-aggregator : {error}")
    try:
        qbit_heal_interface()   # qBittorrent lié à tun0 sans VPN : plus aucune connexion
    except (SeedboxError, OSError) as error:
        say(f"qBittorrent : {error}")
    if "tailscale" in profiles_read():
        try:
            remote().tailscale_sync()
        except (SeedboxError, OSError, ValueError) as error:
            say(f"Tailscale : {error}")
    steps_retry()
    try:
        appliance().refresh_check()    # nouvelle version de l'appliance sur GitHub ? (au plus toutes les 6 h)
    except (SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"mise à jour de l'appliance : {error}")
    try:
        audit().maintain()             # connexions Authelia et SSH dans le journal d'audit
    except (SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"audit : {error}")
    try:
        guests().expire()              # comptes invités arrivés à échéance
    except (SeedboxError, OSError, ValueError, KeyError, http.client.HTTPException) as error:
        say(f"invités : {error}")
    try:
        cleanup().purge_expired()      # la corbeille du nettoyage garde les éléments 14 jours
    except (SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"corbeille : {error}")
    try:
        versions().snapshot_all()      # garde l'image courante de chaque service avant la mise à jour nocturne
    except (SeedboxError, OSError, ValueError, KeyError) as error:
        say(f"versions : {error}")
    try:
        created = jackett_sync()
        if created:
            say(f"Jackett : {created} indexeur(s) ajouté(s) à Prowlarr")
    except (SeedboxError, OSError, ValueError, KeyError, http.client.HTTPException) as error:
        say(f"Jackett vers Prowlarr : {error}")
    trackers_sync()


def restart():
    """Redémarre toute la stack, puis signale les applications qui ne repartent pas."""
    compose("restart", check=False)
    errors = []
    if container_running("gluetun") and not wait_healthy("gluetun", 90):
        errors.append("VPN : le tunnel ne s'établit pas, qBittorrent reste à l'arrêt (vérifiez le fournisseur)")
    return errors


CARE_HELP = {"spacefore": "prévision de saturation", "libreport": "rapport hebdomadaire", "dormant": "titres jamais lus, séries abandonnées",
             "integrity": "contrôle d'intégrité", "renamer": "normalisation des noms", "subsgap": "sous-titres manquants",
             "guestquota": "quotas de lecture", "energy": "énergie des disques"}
_KV = [("key", {}), ("value", {})]
_IDS = [("app", {"choices": ("radarr", "sonarr")}), ("ids", {"nargs": "+", "type": int})]
CARE_COMMANDS = {
    "spacefore": {"show": [], "sample": [], "set": _KV},
    "libreport": {"show": [], "send": [], "set": _KV},
    "dormant": {"show": [], "scan": [], "unmonitor": _IDS, "set": _KV},
    "integrity": {"show": [], "run": [], "research": [("app", {"choices": ("radarr", "sonarr")}), ("owner", {"type": int}), ("file", {"type": int})], "set": _KV},
    "renamer": {"show": [], "scan": [], "apply": _IDS},
    "subsgap": {"show": [], "set": [("langs", {"nargs": "+"})]},
    "guestquota": {"show": [], "tick": [], "default": [("count", {"type": int})], "quota": [("login", {}), ("count", {"type": int})],
                   "unquota": [("login", {})], "enforce": [("mode", {"choices": ("on", "off")})], "resume": [("login", {})]},
    "energy": {"show": [], "sample": [], "tick": [], "standby": [("disk", {}), ("minutes", {"type": int})], "set": _KV},
}


def run_care(args):
    """Commandes des modules d'entretien (même schéma : show, actions, set clé valeur)."""
    module = globals()[args.command]()
    action = args.action
    out = None
    if action == "show":
        out = module.overview()
    elif action == "set":
        out = module.save({"langs": args.langs} if args.command == "subsgap" else {args.key: _cli_value(args.value)})
    elif args.command == "guestquota":
        out = {"default": lambda: module.set_default(args.count), "quota": lambda: module.set_quota(args.login, args.count),
               "unquota": lambda: module.remove_quota(args.login), "enforce": lambda: module.set_enforce(args.mode == "on"),
               "resume": lambda: module.resume(args.login), "tick": lambda: module.tick(force=True)}[action]()
    elif args.command == "dormant":
        out = module.scan() if action == "scan" else locked(module.unmonitor)(args.app, args.ids)
    elif args.command == "integrity":
        out = locked(module.run)(once=True) if action == "run" else module.research(args.app, args.owner, args.file)
    elif args.command == "renamer":
        out = module.scan() if action == "scan" else locked(module.apply)(args.app, args.ids)
    elif args.command == "energy":
        out = module.sample() if action == "sample" else module.tick() if action == "tick" else module.set_standby(args.disk, args.minutes)
    elif args.command == "libreport":
        out = module.send()
    elif args.command == "spacefore":
        out = module.sample()
    if out is not None:
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


def _cli_value(text):
    """Valeur de ligne de commande : nombre, booléen ou liste JSON si elle y ressemble, sinon le texte."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def locked(function):
    def wrapper(*args, **kwargs):
        STATE.mkdir(parents=True, exist_ok=True)
        with open(LOCK_FILE, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SeedboxError("une autre opération est en cours")
            return function(*args, **kwargs)
    return wrapper


def read_password(argument):
    if argument:
        return argument
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Mot de passe : ")
    if first != getpass.getpass("Confirmation : "):
        raise SeedboxError("les deux saisies diffèrent")
    return first


def report(warnings):
    for warning in warnings or []:
        print(f"Attention : {warning}", file=sys.stderr)
    return 1 if warnings else 0


def main():
    parser = argparse.ArgumentParser(prog="seedboxctl", description="Pilotage de l'appliance SeedBoxBuntu")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "deploy", "provision", "refresh", "restart", "status", "credentials", "upgrade",
                 "harden", "autoupdate", "firewall-guard"):
        commands.add_parser(name)
    rm = commands.add_parser("remote", help="accès distant : adresses, Tailscale, Let's Encrypt par DNS").add_subparsers(
        dest="action", required=True)
    rm.add_parser("show")
    rm.add_parser("hosts").add_argument("value", nargs="?", default="")
    ts = rm.add_parser("tailscale").add_subparsers(dest="mode", required=True)
    ts_set = ts.add_parser("set")
    ts_set.add_argument("--hostname", default="seedbox")
    ts_set.add_argument("--key-stdin", action="store_true", help="clé d'authentification lue sur l'entrée standard")
    ts.add_parser("sync")
    ts.add_parser("disable")
    dns = rm.add_parser("dns").add_subparsers(dest="mode", required=True)
    dns.add_parser("set").add_argument("--provider", required=True)
    dns.add_parser("disable")
    sy = commands.add_parser("system", help="ressources, disques et conteneurs").add_subparsers(dest="action", required=True)
    sy.add_parser("show")
    sy.add_parser("containers")
    al = commands.add_parser("alerts", help="alertes ntfy, Discord, webhook, e-mail").add_subparsers(
        dest="action", required=True)
    for name in ("show", "check", "test", "set"):
        al.add_parser(name)
    bk = commands.add_parser("backup", help="sauvegarde et restauration de la configuration").add_subparsers(
        dest="action", required=True)
    for name in ("show", "list", "create", "auto", "key"):
        bk.add_parser(name)
    bk.add_parser("delete").add_argument("name")
    restore_cmd = bk.add_parser("restore")
    restore_cmd.add_argument("name", help="nom d'une sauvegarde listée, ou chemin d'un fichier")
    restore_cmd.add_argument("--key-file", default="", help="clé du serveur d'origine (sinon celle de ce serveur)")
    bk_check = bk.add_parser("check", help="vérification à blanc d'une sauvegarde")
    bk_check.add_argument("name", nargs="?", default="")
    bk_set = bk.add_parser("set")
    bk_set.add_argument("--enabled")
    bk_set.add_argument("--days")
    bk_set.add_argument("--time")
    bk_set.add_argument("--keep")
    bk_set.add_argument("--destination")
    for command, actions, helptext in (
            ("resources", ("show", "set"), "ressources et dérives de chaque conteneur"),
            ("window", ("show", "set", "tick"), "fenêtre de maintenance des tâches automatiques"),
            ("activity", ("show", "set"), "activité par personne (facultatif)"),
            ("vulnscan", ("show", "set", "scan", "auto"), "failles connues dans les images Docker"),
            ("push", ("show", "test"), "notifications push dans le navigateur")):
        sub = commands.add_parser(command, help=helptext).add_subparsers(dest="action", required=True)
        for name in actions:
            sub.add_parser(name)
    commands.add_parser("netcheck", help="diagnostic réseau")
    vac = commands.add_parser("vacation", help="mode vacances").add_subparsers(dest="action", required=True)
    vac_start = vac.add_parser("start")
    vac_start.add_argument("--until", required=True, help="date de reprise AAAA-MM-JJ")
    vac_start.add_argument("--by", default="cli", help="compte à l'origine de la demande (journal)")
    for name in ("show", "stop", "tick"):
        vac.add_parser(name)
    sm = commands.add_parser("smarthist", help="santé des disques dans le temps").add_subparsers(dest="action", required=True)
    for name in ("show", "sample"):
        sm.add_parser(name)
    bw = commands.add_parser("bandwidth", help="débit de qBittorrent par plages horaires").add_subparsers(dest="action", required=True)
    for name in ("show", "tick"):
        bw.add_parser(name)
    rg = commands.add_parser("ratiogoals", help="objectifs de ratio par tracker").add_subparsers(dest="action", required=True)
    rg.add_parser("show")
    rg_set = rg.add_parser("set")
    rg_set.add_argument("tracker")
    rg_set.add_argument("ratio", type=float)
    rg.add_parser("unset").add_argument("tracker")
    ro = commands.add_parser("rollout", help="mise à jour par lots").add_subparsers(dest="action", required=True)
    ro.add_parser("plan")
    ro_run = ro.add_parser("run")
    ro_run.add_argument("--only", nargs="*", default=None, help="services à traiter (tous ceux du lot par défaut)")
    ro_run.add_argument("--continue", dest="keep_going", action="store_true", help="ne pas s'arrêter au premier échec")
    bd = commands.add_parser("backupdiff", help="comparaison de deux sauvegardes").add_subparsers(dest="action", required=True)
    bd.add_parser("list")
    bd_diff = bd.add_parser("diff")
    bd_diff.add_argument("older")
    bd_diff.add_argument("newer", help="nom d'une sauvegarde, ou « courant » pour le serveur actuel")
    dg = commands.add_parser("diagnose", help="assistant de dépannage").add_subparsers(dest="action", required=True)
    dg.add_parser("list")
    dg.add_parser("run").add_argument("service")
    em = commands.add_parser("emergency", help="accès de secours").add_subparsers(dest="action", required=True)
    em.add_parser("show")
    em_init = em.add_parser("init", help="génère un nouveau code de secours (affiché une seule fois)")
    em_init.add_argument("--by", default="cli")
    em_use = em.add_parser("use", help="réinitialise un administrateur ; le code est lu sur l'entrée standard")
    em_use.add_argument("login")
    em_use.add_argument("--keep-2fa", action="store_true", help="ne retire pas les secondes étapes enregistrées")
    ce = commands.add_parser("catalogexport", help="liste de la médiathèque (Radarr, Sonarr)").add_subparsers(dest="action", required=True)
    for name in ("show", "run"):
        ce.add_parser(name)
    for name in ("plan", "apply"):
        ce.add_parser(name).add_argument("name", nargs="?", default=None)
    commands.add_parser("myspace", help="vue d'un compte dans « Mon espace »").add_subparsers(dest="action", required=True).add_parser("show").add_argument("login")
    rc = commands.add_parser("remote-control", help="télécommande").add_subparsers(dest="action", required=True)
    for name in ("show", "pause", "resume"):
        rc.add_parser(name)
    cl = commands.add_parser("changelog", help="journal des réglages").add_subparsers(dest="action", required=True)
    for name in ("show", "tick"):
        cl.add_parser(name)
    cl_undo = cl.add_parser("undo")
    cl_undo.add_argument("id")
    cl_undo.add_argument("--by", default="cli")
    rd = commands.add_parser("restoredrill", help="simulation de restauration").add_subparsers(dest="action", required=True)
    rd.add_parser("show")
    rd_run = rd.add_parser("run")
    rd_run.add_argument("name", nargs="?", default=None, help="sauvegarde (la plus récente par défaut)")
    rd_run.add_argument("--only", nargs="*", default=None, help="applications à essayer")
    du_cmd = commands.add_parser("diskusage", help="espace par dossier et par application").add_subparsers(dest="action", required=True)
    for name in ("show", "scan"):
        du_cmd.add_parser(name)
    rt = commands.add_parser("rotation", help="renouvellement guidé des secrets").add_subparsers(dest="action", required=True)
    rt.add_parser("show")
    rt_run = rt.add_parser("run")
    rt_run.add_argument("item")
    rt_run.add_argument("--by", default="cli")
    ap = commands.add_parser("apppass", help="mots de passe propres aux applications").add_subparsers(dest="action", required=True)
    ap.add_parser("show")
    ap_reset = ap.add_parser("reset", help="nouveau mot de passe sur l'entrée standard ; vide = généré")
    ap_reset.add_argument("app")
    ap_reset.add_argument("--login", default="")
    bn = commands.add_parser("bans", help="fail2ban : bannissements et réglages").add_subparsers(dest="action", required=True)
    for name in ("show", "unban-all"):
        bn.add_parser(name)
    for name in ("unban", "ban"):
        bn_a = bn.add_parser(name)
        bn_a.add_argument("address")
        bn_a.add_argument("--jail", default="" if name == "unban" else "sshd")
    for name in ("ignore", "unignore"):
        bn.add_parser(name).add_argument("address")
    bn_set = bn.add_parser("set")
    bn_set.add_argument("--ban-minutes", type=int)
    bn_set.add_argument("--window-minutes", type=int)
    bn_set.add_argument("--retries", type=int)
    bn_set.add_argument("--max-hours", type=int)
    bn_set.add_argument("--increment", dest="increment", action="store_true", default=None)
    bn_set.add_argument("--no-increment", dest="increment", action="store_false")
    sp = commands.add_parser("space", help="occupation de l'espace disque et remèdes").add_subparsers(dest="action", required=True)
    for name in ("show", "scan"):
        sp.add_parser(name)
    sp.add_parser("fix").add_argument("name")
    sd = commands.add_parser("subdomains", help="sous-domaines des applications").add_subparsers(dest="action", required=True)
    for name in ("show", "check", "off"):
        sd.add_parser(name)
    sd.add_parser("set", help="active les sous-domaines sous ce domaine").add_argument("domain")
    sd_rename = sd.add_parser("rename", help="change le nom d'une application (vide : nom par défaut)")
    sd_rename.add_argument("app")
    sd_rename.add_argument("name", nargs="?", default="")
    for name in ("enable", "disable"):
        sd.add_parser(name).add_argument("app")
    ag = commands.add_parser("aggregator", help="catalogue des releases par œuvre").add_subparsers(dest="action", required=True)
    for name in ("show", "sources", "purge"):
        ag.add_parser(name)
    ag_sync = ag.add_parser("sync", help="collecte les sources maintenant")
    ag_sync.add_argument("--only", default="")
    ag_add = ag.add_parser("add", help="ajoute une source Torznab")
    ag_add.add_argument("name")
    ag_add.add_argument("url")
    ag_add.add_argument("--key-stdin", action="store_true", help="clé d'API sur l'entrée standard")
    for name in ("remove", "enable", "disable", "test", "send"):
        ag.add_parser(name).add_argument("id")
    ag_cfg = ag.add_parser("config")
    ag_cfg.add_argument("--interval", type=int)
    ag_cfg.add_argument("--retention", type=int)
    ag_cfg.add_argument("--limit", type=int)
    ag_cfg.add_argument("--category")
    ag_cfg.add_argument("--enable", action="store_true")
    ag_cfg.add_argument("--disable", action="store_true")
    mq = commands.add_parser("mediaquality", help="qualité des médias").add_subparsers(dest="action", required=True)
    mq.add_parser("show")
    mq_up = mq.add_parser("upgrade")
    mq_up.add_argument("app")
    mq_up.add_argument("count", nargs="?", type=int, default=20)
    commands.add_parser("linkcheck", help="contrôle des liens entre applications")
    pr = commands.add_parser("peoplereport", help="bilan du mois par personne").add_subparsers(dest="action", required=True)
    for name in ("show", "send"):
        pr.add_parser(name)
    pr_set = pr.add_parser("set")
    pr_set.add_argument("--enable", action="store_true")
    pr_set.add_argument("--disable", action="store_true")
    pr_set.add_argument("--day", type=int, default=None)
    ts = commands.add_parser("titleshare", help="partager un seul titre").add_subparsers(dest="action", required=True)
    for name in ("list", "tick"):
        ts.add_parser(name)
    ts_create = ts.add_parser("create")
    ts_create.add_argument("kind", choices=("movie", "series"))
    ts_create.add_argument("id", type=int)
    ts_create.add_argument("--days", type=int, default=7)
    ts_create.add_argument("--by", default="cli")
    ts.add_parser("revoke").add_argument("id")
    vw = commands.add_parser("vpnwatch", help="surveillance du VPN").add_subparsers(dest="action", required=True)
    for name in ("show", "tick"):
        vw.add_parser(name)
    vw_set = vw.add_parser("set")
    vw_set.add_argument("--enable", action="store_true")
    vw_set.add_argument("--disable", action="store_true")
    vw_set.add_argument("--auto-stop", dest="auto_stop", action="store_true")
    vw_set.add_argument("--no-auto-stop", dest="no_auto_stop", action="store_true")
    commands.add_parser("house", help="état d'ensemble pour l'écran maison")
    ls = commands.add_parser("libscan", help="relevé des bibliothèques").add_subparsers(dest="action", required=True)
    for name in ("show", "scan"):
        ls.add_parser(name)
    commands.add_parser("libmap", help="carte des bibliothèques").add_subparsers(dest="action", required=True).add_parser("show")
    ca = commands.add_parser("copyaudit", help="copies cachées").add_subparsers(dest="action", required=True)
    for name in ("show", "scan"):
        ca.add_parser(name)
    relink_parser = ca.add_parser("relink")
    relink_parser.add_argument("paths", nargs="*")
    relink_parser.add_argument("--all", action="store_true", help="relier toutes les copies possibles")
    rp = commands.add_parser("replacer", help="remplacements sous budget").add_subparsers(dest="action", required=True)
    rp.add_parser("show")
    rp.add_parser("search").add_argument("count", nargs="?", type=int, default=None)
    rp.add_parser("apply").add_argument("ids", nargs="+")
    rp.add_parser("safety-on").add_argument("days", nargs="?", type=int, default=None)
    rp_set = rp.add_parser("set")
    rp_set.add_argument("key")
    rp_set.add_argument("value")
    ms = commands.add_parser("mediaslim", help="alléger les fichiers").add_subparsers(dest="action", required=True)
    for name in ("show", "run", "clear"):
        ms.add_parser(name)
    ms_add = ms.add_parser("add")
    ms_add.add_argument("kind", choices=("recompress", "tracks"))
    ms_add.add_argument("paths", nargs="+")
    ms.add_parser("remove").add_argument("id")
    ms_set = ms.add_parser("set")
    ms_set.add_argument("key")
    ms_set.add_argument("value")
    sp = commands.add_parser("shareprofile", help="profil de qualité par partage").add_subparsers(dest="action", required=True)
    sp.add_parser("show")
    sp_set = sp.add_parser("set")
    sp_set.add_argument("app")
    sp_set.add_argument("volume")
    sp_set.add_argument("profile", type=int)
    sp_unset = sp.add_parser("unset")
    sp_unset.add_argument("app")
    sp_unset.add_argument("volume")
    sp.add_parser("auto").add_argument("mode", choices=("on", "off"))
    sp.add_parser("apply").add_argument("app", nargs="?", default=None)
    rl = commands.add_parser("relocate", help="déplacer les titres inactifs").add_subparsers(dest="action", required=True)
    rl.add_parser("show").add_argument("volume", nargs="?", default=None)
    rl_move = rl.add_parser("move")
    rl_move.add_argument("app")
    rl_move.add_argument("volume_from")
    rl_move.add_argument("volume_to")
    rl_move.add_argument("ids", nargs="+", type=int)
    sd = commands.add_parser("seeddiag", help="diagnostic du partage").add_subparsers(dest="action", required=True)
    sd.add_parser("show")
    sd.add_parser("apply").add_argument("ident")
    rp = commands.add_parser("repoint", help="seed depuis le NAS, sans copie locale").add_subparsers(dest="action", required=True)
    for name in ("show", "run", "enable", "disable"):
        rp.add_parser(name)
    so = commands.add_parser("seedopt", help="optimisation des seeds").add_subparsers(dest="action", required=True)
    so.add_parser("show").add_argument("--tracker", default=None)
    for name in ("rules", "sample"):
        so.add_parser(name)
    so_rule = so.add_parser("rule")
    so_rule.add_argument("tracker")
    so_rule.add_argument("ratio", type=float)
    so_rule.add_argument("hours", type=int)
    so.add_parser("unrule").add_argument("tracker")
    so.add_parser("reannounce").add_argument("hashes", nargs="+")
    so_retire = so.add_parser("retire")
    so_retire.add_argument("hashes", nargs="+")
    so_retire.add_argument("--delete-files", dest="delete_files", action="store_true")
    lm = commands.add_parser("llm", help="connecteur Ollama").add_subparsers(dest="action", required=True)
    for name in ("show", "test"):
        lm.add_parser(name)
    lm_set = lm.add_parser("set")
    lm_set.add_argument("key")
    lm_set.add_argument("value")
    lm.add_parser("explain").add_argument("topic", choices=("seeds", "diag", "library"))
    for name, actions in CARE_COMMANDS.items():
        care = commands.add_parser(name, help=CARE_HELP[name]).add_subparsers(dest="action", required=True)
        for action, arguments in actions.items():
            sub = care.add_parser(action)
            for argument, options in arguments:
                sub.add_argument(argument, **options)
    net = commands.add_parser("network", help="réseau de l'appliance : IP fixe ou DHCP, passerelle, DNS").add_subparsers(dest="action", required=True)
    net.add_parser("show")
    net_set = net.add_parser("set", help="network set CARTE dhcp | network set CARTE static 192.168.1.50/24 192.168.1.1 1.1.1.1 9.9.9.9")
    net_set.add_argument("iface")
    net_set.add_argument("mode", choices=("dhcp", "static"))
    net_set.add_argument("address", nargs="?", default="")
    net_set.add_argument("gateway", nargs="?", default="")
    net_set.add_argument("dns", nargs="*")
    net_set.add_argument("--timeout", type=int, default=0, help="secondes avant le retour arrière sans confirmation")
    net.add_parser("confirm", help="conserve le changement en attente")
    net.add_parser("revert", help="rétablit la configuration précédente")
    cfg_cmd = commands.add_parser("config", help="export et import des réglages de la console").add_subparsers(dest="action", required=True)
    cfg_export = cfg_cmd.add_parser("export")
    cfg_export.add_argument("--secrets", action="store_true", help="inclut les secrets des canaux d'alerte")
    cfg_import = cfg_cmd.add_parser("import", help="lit le fichier sur l'entrée standard ; à blanc sans --apply")
    cfg_import.add_argument("--apply", action="store_true")
    au = commands.add_parser("audit", help="journal d'audit et recherche dans les journaux").add_subparsers(dest="action", required=True)
    for name in ("sync", "verify", "prune"):
        au.add_parser(name)
    au_show = au.add_parser("show")
    au_show.add_argument("--limit", type=int, default=50)
    au_show.add_argument("--q", default="")
    au_export = au.add_parser("export")
    au_export.add_argument("--format", choices=("csv", "json"), default="csv")
    au_find = au.add_parser("logs", help="recherche dans les journaux de tous les services")
    au_find.add_argument("query", nargs="?", default="")
    au_find.add_argument("--since", default="1h")
    au_find.add_argument("--regex", action="store_true")
    im = commands.add_parser("import", help="importer une autre seedbox (sauvegardes Radarr/Sonarr…, torrents qBittorrent)").add_subparsers(
        dest="action", required=True)
    for name in ("scan", "show", "clear"):
        im.add_parser(name)
    im_run = im.add_parser("run")
    im_run.add_argument("ids", nargs="+")
    im_run.add_argument("--map", action="append", default=[], metavar="ANCIEN=NOUVEAU")
    im_run.add_argument("--dry-run", action="store_true")
    gs = commands.add_parser("guests", help="invitations par lien, limites et comptes temporaires").add_subparsers(dest="action", required=True)
    for name in ("list", "expire"):
        gs.add_parser(name)
    gs_create = gs.add_parser("create", help="profil de limites lu en JSON sur l'entrée standard")
    gs_create.add_argument("--label", default="")
    gs_create.add_argument("--days", default="7")
    gs_create.add_argument("--uses", default="1")
    gs_create.add_argument("--by", default="")
    gs.add_parser("revoke").add_argument("id")
    gs_redeem = gs.add_parser("redeem", help="mot de passe lu sur l'entrée standard")
    gs_redeem.add_argument("id")
    gs_redeem.add_argument("login")
    gs.add_parser("profile", help="profil de limites lu en JSON sur l'entrée standard").add_argument("login")
    gs_extend = gs.add_parser("extend")
    gs_extend.add_argument("login")
    gs_extend.add_argument("days")
    gp = commands.add_parser("gpu", help="transcodage matériel de Jellyfin (GPU)").add_subparsers(dest="action", required=True)
    for name in ("show", "apply", "test"):
        gp.add_parser(name)
    gp.add_parser("set").add_argument("--mode", required=True)
    cln = commands.add_parser("cleanup", help="assistant de nettoyage avec corbeille").add_subparsers(dest="action", required=True)
    for name in ("scan", "show", "docker"):
        cln.add_parser(name)
    cln.add_parser("trash").add_argument("ids", nargs="+")
    cln.add_parser("restore").add_argument("ids", nargs="+")
    cln_empty = cln.add_parser("empty")
    cln_empty.add_argument("--days", type=int, default=0)
    met = commands.add_parser("metrics", help="historique des ressources et prévision de saturation des disques").add_subparsers(
        dest="action", required=True)
    met.add_parser("collect")
    met.add_parser("forecast")
    met_show = met.add_parser("show")
    met_show.add_argument("--range", default="24h")
    rep = commands.add_parser("report", help="rapport hebdomadaire de l'état du serveur").add_subparsers(dest="action", required=True)
    for name in ("show", "build", "send", "set", "auto"):
        rep.add_parser(name)
    commands.add_parser("assistant", help="liste de contrôle de la configuration").add_subparsers(dest="action", required=True).add_parser("show")
    off = commands.add_parser("offsite", help="sauvegarde hors du serveur (rclone) et test de restauration").add_subparsers(
        dest="action", required=True)
    for name in ("show", "set", "connection", "push", "clear"):
        off.add_parser(name)
    off_test = off.add_parser("restore-test", help="récupère, déchiffre et vérifie la dernière sauvegarde")
    off_test.add_argument("--local", action="store_true", help="teste la copie locale même si une destination est configurée")
    off_test.add_argument("--auto", action="store_true", help="appel du minuteur mensuel")
    auth = commands.add_parser("auth", help="connexion unique : double authentification").add_subparsers(
        dest="action", required=True)
    auth.add_parser("show")
    auth.add_parser("code", help="codes et liens de vérification écrits par Authelia")
    auth.add_parser("2fa").add_argument("mode", choices=AUTH_MODES)
    steps = commands.add_parser("steps", help="état des étapes de configuration et des mises à jour").add_subparsers(
        dest="action", required=True)
    steps.add_parser("list")
    steps.add_parser("retry")
    steps.add_parser("ignore").add_argument("label")
    steps.add_parser("skip-upgrade")
    check = commands.add_parser("check", help="contrôle de santé : API et liaisons entre applications")
    check.add_argument("--json", action="store_true")
    updates = commands.add_parser("updates").add_subparsers(dest="action", required=True)
    updates.add_parser("show")
    set_updates = updates.add_parser("set")
    for prefix, options in (("security", ("enabled", "days", "time", "scope", "reboot", "reboot-time")),
                            ("apps", ("enabled", "days", "time"))):
        for option in options:
            set_updates.add_argument(f"--{prefix}-{option}", default=None)
    updates.add_parser("run").add_argument("what", choices=("security", "apps"))
    security = commands.add_parser("security").add_subparsers(dest="action", required=True)
    security.add_parser("audit")
    security.add_parser("apply")
    security.add_parser("ports").add_argument("ports", nargs="?", default=None,
                                              help="ports supplémentaires, ex. « 8080 51413/udp » ; vide pour les lister")
    user = commands.add_parser("user").add_subparsers(dest="action", required=True)
    user.add_parser("list")
    add = user.add_parser("add")
    add.add_argument("login")
    add.add_argument("--role", choices=ROLES, default="user")
    add.add_argument("--password", default="")
    passwd = user.add_parser("passwd")
    passwd.add_argument("login")
    passwd.add_argument("--password", default="")
    remove = user.add_parser("del")
    remove.add_argument("login")
    change_role = user.add_parser("role", help="change le rôle d'un compte")
    change_role.add_argument("login")
    change_role.add_argument("role", choices=ROLES)
    domain = commands.add_parser("domain")
    domain.add_argument("domain", nargs="?", default="")
    domain.add_argument("--email", default="")
    vpn = commands.add_parser("vpn").add_subparsers(dest="action", required=True)
    setup = vpn.add_parser("set")
    setup.add_argument("--provider", required=True)
    setup.add_argument("--addresses", default="")
    setup.add_argument("--countries", default="")
    setup.add_argument("--port-forwarding", action="store_true")
    setup.add_argument("--endpoint-ip", default="")
    setup.add_argument("--endpoint-port", default="")
    setup.add_argument("--public-key", default="")
    vpn.add_parser("disable", help="alias de direct")
    vpn.add_parser("direct", help="qBittorrent sans VPN (accès local)")
    vpn.add_parser("status")
    rotation = commands.add_parser("rotate")
    rotation.add_argument("service")
    trackers = commands.add_parser("trackers").add_subparsers(dest="action", required=True)
    trackers.add_parser("list")
    trackers.add_parser("sync")
    rule = trackers.add_parser("rule")
    rule.add_argument("keyword")
    rule.add_argument("--min-seed-hours", type=int)
    rule.add_argument("--max-ratio", type=float)
    rule.add_argument("--tag")
    catalog = commands.add_parser("apps", help="catalogue d'applications installables").add_subparsers(dest="action", required=True)
    listing = catalog.add_parser("list")
    listing.add_argument("--json", action="store_true")
    catalog.add_parser("refresh", help="relit le catalogue sur le web (JSON de l'administrateur et linuxserver.io)")
    catalog.add_parser("install").add_argument("id")
    removal = catalog.add_parser("remove")
    removal.add_argument("id")
    removal.add_argument("--purge", action="store_true", help="supprime aussi la configuration de l'application")
    catalog.add_parser("url", help="adresse du catalogue JSON").add_argument("value", nargs="?", default=None)
    pack_cmd = catalog.add_parser("pack", help="packs d'applications").add_subparsers(dest="pack_action", required=True)
    pack_cmd.add_parser("list")
    pack_cmd.add_parser("install").add_argument("id")
    catalog.add_parser("preset", help="préréglage de qualité de Recyclarr (1080p ou 2160p)").add_argument("value", nargs="?", default=None)
    ver = commands.add_parser("versions", help="versions des services, mise à jour et retour arrière").add_subparsers(
        dest="action", required=True)
    ver.add_parser("list")
    ver.add_parser("snapshot")
    ver.add_parser("update").add_argument("service")
    rollback_cmd = ver.add_parser("rollback")
    rollback_cmd.add_argument("service")
    rollback_cmd.add_argument("--to", default="", help="identifiant de la version (sinon la plus récente)")
    ver.add_parser("unpin").add_argument("service")
    app = commands.add_parser("appliance", help="mise à jour de l'appliance depuis GitHub, sans ISO").add_subparsers(
        dest="action", required=True)
    app.add_parser("status")
    app.add_parser("check", help="cherche une nouvelle version sur GitHub")
    apply_cmd = app.add_parser("apply", help="télécharge et installe la dernière version")
    apply_cmd.add_argument("--ref", default="", help="branche ou étiquette (sinon celle des réglages)")
    apply_cmd.add_argument("--force", action="store_true", help="réinstalle même si la version est déjà à jour")
    app.add_parser("rollback", help="annule la dernière mise à jour faite depuis la console")
    source_cmd = app.add_parser("source", help="dépôt GitHub, branche, canal et options de mise à jour")
    source_cmd.add_argument("--repo", default="")
    source_cmd.add_argument("--ref", default="")
    source_cmd.add_argument("--channel", choices=("dev", "stable"), default=None, help="dev : la branche ; stable : dernière version publiée")
    source_cmd.add_argument("--guard", default=None, help="retour automatique si la santé se dégrade (true/false)")
    source_cmd.add_argument("--require-signed", default=None, help="n'installer que des versions signées (true/false)")
    source_cmd.add_argument("--auto-enabled", default=None, help="mise à jour automatique planifiée (true/false)")
    source_cmd.add_argument("--auto-days", default=None)
    source_cmd.add_argument("--auto-time", default=None)
    app.add_parser("auto", help="mise à jour planifiée (lancée par le minuteur seedbox-appliance)")
    signer_cmd = app.add_parser("signer", help="clés publiques SSH qui signent les versions publiées (lues sur l'entrée standard)")
    signer_cmd.add_argument("--set", action="store_true", help="remplace les clés par celles de l'entrée standard (vide : aucune)")
    github = commands.add_parser("github").add_subparsers(dest="action", required=True)
    token = github.add_parser("token")
    token.add_argument("value", nargs="?", default="")
    share = commands.add_parser("share").add_subparsers(dest="action", required=True)
    share.add_parser("list")
    share.add_parser("disks")
    share.add_parser("remount")
    find = share.add_parser("discover", help="partages proposés par un serveur ; mot de passe SMB lu sur l'entrée standard")
    find.add_argument("kind", choices=("smb", "nfs"))
    find.add_argument("host")
    find.add_argument("--username", default="")
    find.add_argument("--domain", default="")
    find.add_argument("--version", default="")
    new = share.add_parser("add", help="mot de passe SMB lu sur l'entrée standard")
    new.add_argument("name")
    new.add_argument("--kind", choices=("smb", "nfs", "disk"), required=True)
    new.add_argument("--source", required=True, help="//serveur/partage, serveur:/export ou UUID du disque")
    new.add_argument("--username", default="")
    new.add_argument("--domain", default="")
    new.add_argument("--version", default="")
    for action in ("mount", "umount"):
        share.add_parser(action).add_argument("name")
    drop = share.add_parser("remove")
    drop.add_argument("name")
    drop.add_argument("--force", action="store_true")
    login = share.add_parser("passwd", help="mot de passe lu sur l'entrée standard")
    login.add_argument("name")
    login.add_argument("--username", required=True)
    login.add_argument("--domain", default="")
    role = share.add_parser("role", help="sans partage : retour au stockage local")
    role.add_argument("role")
    role.add_argument("share", nargs="?", default="")
    role.add_argument("--path", default="")
    args = parser.parse_args()

    if args.command == "init":
        locked(init)()
    elif args.command == "deploy":
        locked(deploy)()
    elif args.command == "upgrade":
        locked(upgrade)()
    elif args.command == "harden":
        return report(locked(maintenance().harden)())
    elif args.command == "check":
        return health().main(args.json)
    elif args.command == "firewall-guard":
        maintenance().guard()
    elif args.command == "autoupdate":
        if maintwindow().allow("autoupdate"):
            locked(maintenance().autoupdate)()
    elif args.command == "updates":
        module = maintenance()
        if args.action == "show":
            print(json.dumps(module.updates_overview(), indent=2, ensure_ascii=False))
        elif args.action == "run":
            locked(module.run_security_updates if args.what == "security" else module.run_app_updates)()
        else:
            values = {"security": {}, "apps": {}}
            for key, value in vars(args).items():
                for prefix in values:
                    if key.startswith(prefix + "_") and value is not None:
                        values[prefix][key[len(prefix) + 1:]] = value
            locked(module.set_updates)(values["security"], values["apps"])
            print(json.dumps(module.settings(), indent=2, ensure_ascii=False))
    elif args.command == "security":
        module = maintenance()
        if args.action == "audit":
            print(json.dumps(module.security_overview(), indent=2, ensure_ascii=False))
        elif args.action == "apply":
            return report(locked(module.harden)())
        elif args.ports is None:
            print(json.dumps(module.extra_ports()))
        else:
            print(json.dumps(locked(module.set_extra_ports)(args.ports)))
    elif args.command == "provision":
        return report(locked(provision)())
    elif args.command == "remote":
        module = remote()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "hosts":
            return report(locked(module.set_hosts)(args.value))
        elif args.action == "tailscale":
            if args.mode == "set":
                return report(locked(module.tailscale_set)(read_password("") if args.key_stdin else "", args.hostname))
            if args.mode == "sync":
                return report(locked(module.tailscale_sync)())
            return report(locked(module.tailscale_disable)())
        elif args.mode == "set":
            locked(module.dns_set)(args.provider, sys.stdin.read())
        else:
            locked(module.dns_disable)()
    elif args.command == "system":
        module = sysinfo()
        print(json.dumps(module.overview() if args.action == "show" else module.containers(), indent=2, ensure_ascii=False))
    elif args.command == "alerts":
        module = alerts()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "check":
            locked(module.check)()
        elif args.action == "test":
            locked(module.test)()
        else:
            # JSON lu sur l'entrée standard : les secrets n'apparaissent jamais dans la liste des processus
            locked(module.save)(json.loads(sys.stdin.read() or "{}"))
    elif args.command == "backup":
        module = backup()
        if args.action in ("show", "list"):
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "create":
            locked(module.create)()
        elif args.action == "auto":
            locked(module.auto)()
        elif args.action == "key":
            print(module.key())
        elif args.action == "check":
            print(json.dumps(sandbox().check(args.name or None), indent=2, ensure_ascii=False))
        elif args.action == "delete":
            locked(module.delete)(args.name)
        elif args.action == "restore":
            key_text = Path(args.key_file).read_text() if args.key_file else ""
            return report(locked(module.restore)(args.name, key_text))
        else:
            values = {k: v for k, v in {"enabled": args.enabled, "days": args.days, "time": args.time,
                                         "keep": args.keep, "destination": args.destination}.items() if v is not None}
            locked(module.set_settings)(values)
    elif args.command == "audit":
        module = audit()
        if args.action == "sync":
            module.sync()
        elif args.action == "verify":
            result = module.verify()
            print(json.dumps(result))
            return 0 if result["ok"] else 1
        elif args.action == "prune":
            print(module.prune(), "événement(s) supprimé(s)")
        elif args.action == "show":
            for event in reversed(module.search(q=args.q, limit=args.limit)["events"]):
                print(f"{time.strftime('%F %T', time.localtime(event['ts']))}  {event['source']:<10} {event['user']:<14} {event['ip']:<16} "
                      f"{event['outcome']:<7} {event['action']} {event['detail']}")
        elif args.action == "export":
            print(module.export(args.format)[0], end="")
        else:
            for item in reversed(module.search_logs(args.query, args.since, regex=args.regex)["lines"]):
                print(f"{time.strftime('%F %T', time.localtime(item['ts']))} [{item['source']}] {item['line']}")
    elif args.command == "import":
        module = importer()
        if args.action == "scan":
            locked(module.scan)()
        elif args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "clear":
            locked(module.clear)()
        else:
            pairs = []
            for text in args.map:
                if "=" not in text:
                    raise SeedboxError("table des chemins : ANCIEN=NOUVEAU")
                pairs.append(tuple(text.split("=", 1)))
            locked(module.run)(args.ids, pairs, args.dry_run)
    elif args.command == "guests":
        module = guests()
        if args.action == "list":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "create":
            print(json.dumps(locked(module.create)(args.label, args.days, args.uses, json.loads(sys.stdin.read() or "{}"), args.by)))
        elif args.action == "revoke":
            locked(module.revoke)(args.id)
        elif args.action == "redeem":
            locked(module.redeem)(args.id, args.login, read_password(""))     # avertissements : dans le journal, le compte existe
        elif args.action == "profile":
            return report(locked(module.set_profile)(args.login, json.loads(sys.stdin.read() or "{}")))
        elif args.action == "extend":
            locked(module.extend)(args.login, args.days)
        else:
            locked(module.expire)()
    elif args.command == "gpu":
        module = gpu()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "set":
            locked(module.set_mode)(args.mode)
        elif args.action == "apply":
            locked(module.apply)()
        else:
            print(json.dumps(module.test()))
    elif args.command == "cleanup":
        module = cleanup()
        if args.action == "scan":
            locked(module.scan)()
        elif args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "trash":
            locked(module.trash)(args.ids)
        elif args.action == "restore":
            locked(module.restore)(args.ids)
        elif args.action == "empty":
            locked(module.empty)(args.days)
        else:
            locked(module.docker_prune)()
    elif args.command == "report":
        module = weekly_report()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "build":
            print(module.build_and_store()["text"])
        elif args.action == "send":
            locked(module.send)()
        elif args.action == "set":
            module.save(json.loads(sys.stdin.read() or "{}"))
        else:
            module.auto()
    elif args.command == "assistant":
        assistant().show()
    elif args.command == "resources":
        module = resources()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(module.save(json.loads(sys.stdin.read() or "{}")), ensure_ascii=False))
    elif args.command == "window":
        module = maintwindow()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "set":
            module.save(json.loads(sys.stdin.read() or "{}"))
        else:
            module.tick()
    elif args.command == "activity":
        module = activity()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            module.save(json.loads(sys.stdin.read() or "{}"))
    elif args.command == "vulnscan":
        module = vulnscan()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "set":
            module.save(json.loads(sys.stdin.read() or "{}"))
        elif args.action == "scan":
            locked(module.scan)()
        else:
            locked(module.auto)()
    elif args.command == "smarthist":
        module = smarthist()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            values = module.sample(force=True)
            metrics().collect(values)                                  # enregistre ces mesures dans l'historique
            print(json.dumps(values, indent=2, ensure_ascii=False))
    elif args.command == "bandwidth":
        module = bandwidth()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            locked(module.tick)()
    elif args.command == "ratiogoals":
        module = ratiogoals()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "set":
            module.save({"goals": {**module.settings()["goals"], args.tracker: args.ratio}})
        else:
            goals = dict(module.settings()["goals"])
            goals.pop(args.tracker.lower(), None)
            module.save({"goals": goals})
    elif args.command == "rollout":
        module = rollout()
        if args.action == "plan":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            locked(module.run)(args.only, not args.keep_going)
    elif args.command == "backupdiff":
        module = backupdiff()
        if args.action == "list":
            print(json.dumps(module.listing(), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(module.diff(args.older, args.newer), indent=2, ensure_ascii=False))
    elif args.command == "diagnose":
        module = diagnose()
        if args.action == "list":
            print("\n".join(module.services()))
        else:
            result = module.run(args.service)
            marks = {"ok": "[OK]  ", "info": "[i]   ", "warn": "[!]   ", "fail": "[ECHEC]"}
            print(f"{args.service} : {result['summary']}\n")
            for item in result["steps"]:
                print(f"{marks[item['level']]} {item['title']} : {item['detail']}")
            for action in result["actions"]:
                print(f"-> proposé : {action['label']} ({action['why']})")
    elif args.command == "emergency":
        module = emergency()
        if args.action == "show":
            print(json.dumps(module.status(), indent=2, ensure_ascii=False))
        elif args.action == "init":
            code = locked(module.init)(args.by)
            print(f"Code de secours (à imprimer et ranger sous clé, il ne sera plus affiché) :\n\n    {code}\n")
        else:
            code = sys.stdin.readline().strip() if not sys.stdin.isatty() else getpass.getpass("Code de secours : ")
            result = locked(module.use)(args.login, code, args.keep_2fa)
            print(f"Compte : {result['login']}\nMot de passe temporaire (à changer après connexion) : {result['password']}\n"
                  f"Secondes étapes retirées : {result['removed']}\n"
                  f"NOUVEAU code de secours (l'ancien est périmé, notez celui-ci) : {result['new_code']}")
            for warning in result["warnings"]:
                print("attention : " + warning)
    elif args.command == "catalogexport":
        module = catalogexport()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "run":
            module.run()
        elif args.action == "plan":
            print(json.dumps(module.plan(module.load(args.name)), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(locked(module.apply)(module.load(args.name)), indent=2, ensure_ascii=False))
    elif args.command == "myspace":
        print(json.dumps(myspace().overview(args.login), indent=2, ensure_ascii=False))
    elif args.command == "remote-control":
        module = telecommande()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "pause":
            print(locked(module.pause)())
        else:
            print(locked(module.resume)())
    elif args.command == "changelog":
        module = changelog()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "tick":
            locked(module.tick)()
        else:
            locked(module.undo)(args.id, args.by)
    elif args.command == "restoredrill":
        module = restoredrill()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            outcome = module.run(args.name, only=args.only)
            print(json.dumps(outcome, indent=2, ensure_ascii=False))
            if not outcome["ok"]:
                sys.exit(1)
    elif args.command == "diskusage":
        module = diskusage()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            module.scan()
    elif args.command == "rotation":
        module = rotation()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            locked(module.rotate)(args.item, args.by)
    elif args.command == "space":
        module = space()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "scan":
            locked(module.scan)()
        else:
            locked(module.fix)(args.name)
    elif args.command == "subdomains":
        module = subdomains()
        if args.action in ("show", "check"):
            print(json.dumps(module.overview(check=args.action == "check"), indent=2, ensure_ascii=False))
        elif args.action == "set":
            return report(locked(module.set_domain)(args.domain))
        elif args.action == "off":
            return report(locked(module.disable)())
        elif args.action == "rename":
            locked(module.rename)(args.app, args.name)
        else:
            locked(module.set_enabled)(args.app, args.action == "enable")
    elif args.command == "apppass":
        module = apppasswords()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            password = "" if args.app != "jellyfin" and sys.stdin.isatty() else read_password("")
            return report(locked(module.reset)(args.app, args.login, password))
    elif args.command == "bans":
        module = bans()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "unban":
            print(f"{args.address} débanni ({module.unban(args.address, args.jail)} jail(s))")
        elif args.action == "unban-all":
            print(f"{module.unban_all()} adresse(s) débannie(s)")
        elif args.action == "ban":
            module.ban(args.address, args.jail)
        elif args.action == "ignore":
            module.ignore(args.address)
        elif args.action == "unignore":
            module.unignore(args.address)
        else:
            module.save(args.ban_minutes, args.window_minutes, args.retries, args.increment, args.max_hours)
    elif args.command == "aggregator":
        module = aggregator()
        if args.action == "show":
            print(json.dumps(module.dashboard(), indent=2, ensure_ascii=False))
        elif args.action == "sources":
            print(json.dumps(module.dashboard()["sources"], indent=2, ensure_ascii=False))
        elif args.action == "sync":
            module.sync(only=args.only)
        elif args.action == "purge":
            print(f"{module.purge_now()} torrent(s) purgé(s)")
        elif args.action == "add":
            print(module.add_source(args.name, args.url, sys.stdin.readline().strip() if args.key_stdin else ""))
        elif args.action in ("remove", "enable", "disable"):
            module.remove_source(args.id) if args.action == "remove" else module.set_source(args.id, args.action == "enable")
        elif args.action == "test":
            print(json.dumps(module.test_source(args.id), indent=2, ensure_ascii=False))
        elif args.action == "send":
            print(json.dumps(module.send(args.id), indent=2, ensure_ascii=False))
        else:
            module.configure(interval=args.interval, retention=args.retention, limit=args.limit, category=args.category,
                             enabled=True if args.enable else False if args.disable else None)
    elif args.command == "mediaquality":
        module = mediaquality()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(module.upgrade(args.app, args.count, by="cli"), ensure_ascii=False))
    elif args.command == "linkcheck":
        outcome = linkcheck().run()
        for item in outcome["items"]:
            print(f"{item['state']:5} {item['title']} : {item['detail']}")
        if outcome["errors"]:
            sys.exit(1)
    elif args.command == "peoplereport":
        module = peoplereport()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "send":
            module.send()
        else:
            values = {}
            if args.enable or args.disable:
                values["enabled"] = bool(args.enable)
            if args.day is not None:
                values["day"] = args.day
            module.save(values)
    elif args.command == "titleshare":
        module = titleshare()
        if args.action == "list":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "create":
            print(json.dumps(locked(module.create)(args.kind, args.id, args.days, args.by), ensure_ascii=False))
        elif args.action == "revoke":
            locked(module.remove)(args.id)
        else:
            locked(module.tick)()
    elif args.command == "vpnwatch":
        module = vpnwatch()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "tick":
            module.tick()
        else:
            values = {}
            if args.enable or args.disable:
                values["enabled"] = bool(args.enable)
            if args.auto_stop or args.no_auto_stop:
                values["auto_stop"] = bool(args.auto_stop)
            module.save(values)
    elif args.command == "house":
        print(json.dumps(house().overview(), indent=2, ensure_ascii=False))
    elif args.command == "libscan":
        module = libscan()
        if args.action == "show":
            data = module.load()
            print(json.dumps({"at": data.get("at"), "files": len(data["records"]), "errors": data.get("errors")}, ensure_ascii=False))
        else:
            module.scan()
    elif args.command == "libmap":
        print(json.dumps(libmap().overview(), indent=2, ensure_ascii=False))
    elif args.command == "copyaudit":
        module = copyaudit()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "scan":
            module.scan()
        else:
            if not args.all and not args.paths:
                raise SeedboxError("indiquez des fichiers ou --all")
            print(json.dumps(locked(module.relink)(None if args.all else args.paths), ensure_ascii=False))
    elif args.command == "replacer":
        module = replacer()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "search":
            module.search(args.count)
        elif args.action == "apply":
            print(json.dumps(locked(module.apply)(args.ids), ensure_ascii=False))
        elif args.action == "safety-on":
            print(json.dumps(locked(module.enable_safety)(args.days), ensure_ascii=False))
        else:
            module.save({args.key: _cli_value(args.value)})
    elif args.command == "mediaslim":
        module = mediaslim()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "add":
            print(json.dumps(module.add(args.kind, args.paths), ensure_ascii=False))
        elif args.action == "remove":
            module.remove(args.id)
        elif args.action == "clear":
            module.clear()
        elif args.action == "set":
            module.save({args.key: _cli_value(args.value)})
        else:
            module.run()
    elif args.command == "shareprofile":
        module = shareprofile()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "set":
            module.set_profile(args.app, args.volume, args.profile)
        elif args.action == "unset":
            module.unset_profile(args.app, args.volume)
        elif args.action == "auto":
            module.set_auto(args.mode == "on")
        else:
            print(json.dumps(locked(module.apply)(args.app), ensure_ascii=False))
    elif args.command == "relocate":
        module = relocate()
        if args.action == "show":
            print(json.dumps(module.overview(args.volume), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(locked(module.move)(args.app, args.volume_to, args.ids, volume_from=args.volume_from), ensure_ascii=False))
    elif args.command == "seeddiag":
        module = seeddiag()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(locked(module.apply)(args.ident), ensure_ascii=False))
    elif args.command == "repoint":
        module = repoint()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "run":
            print(json.dumps(locked(module.run)(), ensure_ascii=False))
        else:
            print(json.dumps(module.set_enabled(args.action == "enable")))
    elif args.command == "seedopt":
        module = seedopt()
        if args.action == "show":
            print(json.dumps(module.overview(args.tracker), indent=2, ensure_ascii=False))
        elif args.action == "rules":
            print(json.dumps(module.settings(), indent=2, ensure_ascii=False))
        elif args.action == "rule":
            module.set_rule(args.tracker, args.ratio, args.hours)
        elif args.action == "unrule":
            module.unset_rule(args.tracker)
        elif args.action == "reannounce":
            module.reannounce(args.hashes)
        elif args.action == "retire":
            module.retire(args.hashes, args.delete_files)
        else:
            print(module.sample(force=True))
    elif args.command == "llm":
        module = llm()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "set":
            module.save({args.key: _cli_value(args.value)})
        elif args.action == "test":
            print(json.dumps(module.test(), indent=2, ensure_ascii=False))
        else:
            print(module.explain(args.topic)["text"])
    elif args.command in CARE_COMMANDS:
        run_care(args)
    elif args.command == "network":
        module = netconf()
        if args.action == "show":
            out = module.overview()
        elif args.action == "set":
            out = locked(module.apply)(args.iface, args.mode, args.address, args.gateway, args.dns, by="cli", timeout=args.timeout)
        else:
            out = {"confirm": module.confirm, "revert": module.revert}[args.action]()
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    elif args.command == "netcheck":
        netcheck().show()
    elif args.command == "vacation":
        module = vacation()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "start":
            locked(module.start)(args.until, args.by)
        elif args.action == "stop":
            locked(module.stop)()
        else:
            locked(module.tick)()
    elif args.command == "push":
        module = webpush()
        if args.action == "show":
            print(json.dumps(module.status(""), indent=2, ensure_ascii=False))
        else:
            result = module.send("Test SeedBoxBuntu", "Les notifications push fonctionnent.")
            print(json.dumps(result, ensure_ascii=False))
    elif args.command == "config":
        module = configbundle()
        if args.action == "export":
            print(json.dumps(module.export(args.secrets), indent=2, ensure_ascii=False))
        else:
            bundle = module.parse(sys.stdin.read())
            rows = module.apply(bundle) if args.apply else module.plan(bundle)
            print(json.dumps(rows, indent=2, ensure_ascii=False))
    elif args.command == "metrics":
        module = metrics()
        if args.action == "collect":
            module.collect()
        elif args.action == "forecast":
            print(json.dumps(module.forecast(), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(module.history(args.range), ensure_ascii=False))
    elif args.command == "offsite":
        module = offsite()
        if args.action == "show":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "set":
            locked(module.save)(json.loads(sys.stdin.read() or "{}"))
        elif args.action == "connection":
            print(json.dumps(locked(module.connection)()))
        elif args.action == "push":
            locked(module.push_latest)()
        elif args.action == "restore-test":
            if args.auto and not maintwindow().allow("restoretest"):
                return 0
            print(json.dumps(locked(module.restore_test)("local" if args.local else None), ensure_ascii=False))
        else:
            locked(module.clear)()
    elif args.command == "auth":
        if args.action == "show":
            print(json.dumps(auth_overview(), indent=2, ensure_ascii=False))
        elif args.action == "code":
            print(tail_file(CONFIG / "authelia" / "notification.txt", 30))
        else:
            locked(auth_set_2fa)(args.mode)
    elif args.command == "steps":
        if args.action == "list":
            print(json.dumps(steps_status(), indent=2, ensure_ascii=False))
        elif args.action == "retry":
            locked(steps_retry)()
        elif args.action == "ignore":
            locked(steps_ignore)(args.label)
        else:
            locked(upgrade_skip)()
    elif args.command == "refresh":
        locked(refresh)()
    elif args.command == "restart":
        return report(locked(restart)())
    elif args.command == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    elif args.command == "credentials":
        print(json.dumps(credentials(), indent=2, ensure_ascii=False))
    elif args.command == "user":
        if args.action == "list":
            print(json.dumps(user_list(), indent=2, ensure_ascii=False))
        elif args.action == "add":
            return report(locked(user_add)(args.login, read_password(args.password), args.role))
        elif args.action == "passwd":
            return report(locked(user_passwd)(args.login, read_password(args.password)))
        elif args.action == "role":
            return report(locked(user_set_role)(args.login, args.role))
        else:
            return report(locked(user_del)(args.login))
    elif args.command == "domain":
        return report(locked(set_domain)(args.domain, args.email))
    elif args.command == "vpn":
        if args.action == "set":
            # Clé lue sur l'entrée standard : elle n'apparaît jamais dans la liste des processus
            return report(locked(set_vpn)(args.provider, read_password(""), args.addresses, args.countries,
                                          args.port_forwarding, args.endpoint_ip, args.endpoint_port, args.public_key))
        if args.action in ("disable", "direct"):
            return report(locked(vpn_disable)())
        else:
            print(json.dumps({"port": vpn_port(), "ip": public_ip()}, indent=2))
    elif args.command == "rotate":
        return report(locked(rotate)(args.service))
    elif args.command == "trackers":
        if args.action == "list":
            print(json.dumps({"regles": load_trackers(), "statistiques": tracker_stats()}, indent=2, ensure_ascii=False))
        elif args.action == "sync":
            locked(trackers_sync)()
        else:
            locked(tracker_rule)(args.keyword, args.min_seed_hours, args.max_ratio, args.tag)
    elif args.command == "apps":
        module = apps()
        if args.action == "list":
            data = module.listing()
            if args.json:
                print(json.dumps(data, ensure_ascii=False))
            else:
                for row in data["apps"]:
                    mark = "installée " if row["installed"] else "          "
                    print(f"{mark}{row['id']:<18}{row['category']:<24}{row['source']:<12}{row['description'][:70]}")
                print(f"\ncatalogue web : {data['catalog_url'] or 'aucun'} ; actualisé : "
                      f"{time.strftime('%F %T', time.localtime(data['fetched'])) if data['fetched'] else 'jamais'}")
        elif args.action == "refresh":
            module.refresh()
        elif args.action == "install":
            module.install(args.id)
        elif args.action == "remove":
            module.remove(args.id, purge=args.purge)
        elif args.action == "url":
            if args.value is None:
                print(env_read().get("CATALOG_URL", ""))
            else:
                module.set_url(args.value)
        elif args.action == "pack":
            if args.pack_action == "list":
                print(json.dumps(module.packs_listing(), indent=2, ensure_ascii=False))
            else:
                locked(module.install_pack)(args.id)
        elif args.action == "preset":
            if args.value is None:
                print(module.set_preset())
            else:
                locked(module.set_preset)(args.value)
    elif args.command == "versions":
        module = versions()
        if args.action == "list":
            print(json.dumps(module.listing(), indent=2, ensure_ascii=False))
        elif args.action == "snapshot":
            print(module.snapshot_all(), "service(s) sauvegardé(s)")
        elif args.action == "update":
            locked(module.update)(args.service)
        elif args.action == "rollback":
            locked(module.rollback)(args.service, args.to or None)
        else:
            locked(module.unpin)(args.service)
    elif args.command == "appliance":
        module = appliance()
        if args.action == "status":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "check":
            print(json.dumps(module.check(), indent=2, ensure_ascii=False))
        elif args.action == "apply":
            module.apply(args.ref or None, args.force)
        elif args.action == "rollback":
            module.rollback()
        elif args.action == "auto":
            if maintwindow().allow("appliance"):
                locked(module.auto)()
        elif args.action == "signer":
            if args.set:
                print(json.dumps(module.set_signers(sys.stdin.read()), ensure_ascii=False))
            else:
                print(json.dumps(module.signer_fingerprints(), ensure_ascii=False))
        else:
            options = {"channel": args.channel, "guard": args.guard, "require_signed": args.require_signed,
                       "auto_settings": {"enabled": args.auto_enabled, "days": args.auto_days, "time": args.auto_time}}
            if args.repo or args.ref or args.channel or args.guard is not None or args.require_signed is not None \
                    or any(v is not None for v in options["auto_settings"].values()):
                module.set_config(args.repo, args.ref, **options)
            else:
                print(json.dumps(module.config(), ensure_ascii=False))
    elif args.command == "github":
        github_set_token(args.value or read_password(""))
    elif args.command == "share":
        module = shares()
        if args.action == "list":
            print(json.dumps(module.overview(), indent=2, ensure_ascii=False))
        elif args.action == "disks":
            print(json.dumps(module.disks(), indent=2, ensure_ascii=False))
        elif args.action == "remount":
            return report(locked(module.remount)())
        elif args.action == "discover":
            password = read_password("") if args.kind == "smb" and args.username else ""
            print(json.dumps(module.discover(args.kind, args.host, args.username, password, args.domain, args.version), indent=2, ensure_ascii=False))
        elif args.action == "add":
            password = read_password("") if args.kind == "smb" and args.username else ""
            return report(locked(module.add)(args.name, args.kind, args.source, args.username, password,
                                             args.domain, args.version))
        elif args.action == "mount":
            locked(module.mount)(args.name)
            module.recover(args.name)
        elif args.action == "umount":
            locked(module.umount)(args.name)
        elif args.action == "remove":
            return report(locked(module.remove)(args.name, args.force))
        elif args.action == "passwd":
            return report(locked(module.set_credentials)(args.name, args.username, read_password(""), args.domain))
        else:
            return report(locked(module.set_role)(args.role, args.share, args.path))
    return 0


if __name__ == "__main__":
    sys.modules.setdefault("seedboxctl", sys.modules[__name__])  # partagé avec le module shares
    try:
        sys.exit(main())
    except SeedboxError as error:
        print(f"Erreur : {error}", file=sys.stderr)
        sys.exit(1)
