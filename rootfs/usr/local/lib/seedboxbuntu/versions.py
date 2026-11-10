"""SeedBoxBuntu - versions des services : mise à jour une par une et retour à la version précédente.

Principe
    Avant toute mise à jour, l'image utilisée par un service reçoit une étiquette locale
    « seedbox-rollback/<service>:<identifiant d'image> » : l'ancienne version reste disponible même après
    le téléchargement de la nouvelle (et même si Watchtower a mis le service à jour la nuit : l'actualisation
    horaire a déjà gardé l'image précédente). On conserve les KEEP dernières versions par service.

    Retour arrière : l'étiquette d'origine du service (par exemple lscr.io/linuxserver/radarr:latest) est
    replacée sur l'ancienne image, puis le service est recréé sans téléchargement. Le service est ensuite
    « épinglé » : Watchtower et « Mettre à jour les applications » le laissent tel quel jusqu'à ce qu'on le
    mette à jour de nouveau.
"""
import json
import re
import time

import seedboxctl as ctl

KEEP = 3
PINS_FILE = ctl.STATE / "pins.json"
PINS_COMPOSE = ctl.BASE / "docker-compose.pins.yml"
REPO = "seedbox-rollback"
SERVICE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
LOCAL_SERVICES = {"p2p-aggregator"}          # image construite sur place : mise à jour par son propre mécanisme
WAIT = 120


def pins_read():
    try:
        return json.loads(PINS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def pins_write(pins):
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    PINS_FILE.write_text(json.dumps(pins, indent=1, ensure_ascii=False) + "\n")
    if pins:
        document = {"services": {name: {"labels": {"com.centurylinklabs.watchtower.enable": "false"}} for name in sorted(pins)}}
        text = "# Généré par seedboxctl (services épinglés après un retour en arrière) : ne pas modifier.\n" \
               + json.dumps(document, indent=2) + "\n"
        if not PINS_COMPOSE.exists() or PINS_COMPOSE.read_text() != text:
            PINS_COMPOSE.write_text(text)
    else:
        PINS_COMPOSE.unlink(missing_ok=True)
    ctl.sync_compose_files()


def docker_json(*command):
    result = ctl.run("docker", *command, check=False, capture=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def service_containers():
    """service -> informations du conteneur (nom, image d'origine, identifiant d'image, étiquettes)."""
    names = [line for line in (ctl.run("docker", "ps", "-a", "--format", "{{.Names}}", check=False, capture=True).stdout or "").split()]
    if not names:
        return {}
    data = docker_json("inspect", *names) or []
    rows = {}
    for item in data:
        labels = (item.get("Config") or {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if not service or labels.get("com.docker.compose.project") is None:
            continue
        rows[service] = {"container": item["Name"].lstrip("/"), "ref": item["Config"]["Image"], "image_id": item["Image"],
                         "running": (item.get("State") or {}).get("Running", False), "labels": labels}
    return rows


def image_info(image_id):
    data = docker_json("image", "inspect", image_id)
    if not data:
        return {}
    item = data[0]
    labels = (item.get("Config") or {}).get("Labels") or {}
    version = labels.get("org.opencontainers.image.version") or labels.get("build_version") or ""
    return {"id": item["Id"], "created": (item.get("Created") or "")[:19], "version": version.split(" ")[0][:40] if version else ""}


def snapshots(service):
    """Versions conservées d'un service, de la plus récente à la plus ancienne."""
    result = ctl.run("docker", "image", "ls", f"{REPO}/{service}", "--format", "{{.Tag}}", "--no-trunc", check=False, capture=True)
    rows = []
    for tag in (result.stdout or "").split():
        info = image_info(f"{REPO}/{service}:{tag}")
        if info:
            rows.append({"tag": tag, "id": info["id"][7:19], "created": info["created"], "version": info["version"]})
    return sorted(rows, key=lambda r: r["created"], reverse=True)


def snapshot(service, info=None):
    """Garde l'image actuelle du service ; renvoie son identifiant court, ou None si le service n'existe pas."""
    info = info or service_containers().get(service)
    if not info:
        return None
    short = info["image_id"].replace("sha256:", "")[:12]
    ctl.run("docker", "tag", info["image_id"], f"{REPO}/{service}:{short}", check=False, capture=True)
    kept = snapshots(service)
    for old in kept[KEEP + 1:]:                # la version actuelle compte parmi les étiquettes
        ctl.run("docker", "rmi", f"{REPO}/{service}:{old['tag']}", check=False, capture=True)
    return short


def snapshot_all():
    """Appelé à chaque actualisation : garde l'image courante de chaque service, avant qu'une mise à jour
    nocturne (Watchtower) ne la remplace."""
    count = 0
    for service, info in service_containers().items():
        if service in LOCAL_SERVICES:
            continue
        if snapshot(service, info):
            count += 1
    return count


def listing():
    pins = pins_read()
    rows = []
    for service, info in sorted(service_containers().items()):
        current = image_info(info["image_id"])
        short = info["image_id"].replace("sha256:", "")[:12]
        rows.append({
            "service": service, "image": info["ref"], "running": info["running"], "local": service in LOCAL_SERVICES,
            "version": current.get("version", ""), "created": current.get("created", ""), "id": short,
            "pinned": service in pins,
            "previous": [s for s in snapshots(service) if s["id"] != short][:KEEP] if service not in LOCAL_SERVICES else [],
        })
    return {"services": rows}


def known(service):
    if not SERVICE.match(service):
        raise ctl.SeedboxError("nom de service invalide")
    info = service_containers().get(service)
    if not info:
        raise ctl.SeedboxError(f"{service} : service inconnu")
    if service in LOCAL_SERVICES:
        raise ctl.SeedboxError(f"{service} : image construite sur place, mise à jour par son propre mécanisme")
    return info


def wait_ready(service, timeout=WAIT):
    """Attend que le service tourne (et soit « healthy » s'il a un contrôle d'état)."""
    deadline = time.time() + timeout
    state = {}
    while time.time() < deadline:
        state = ctl.containers().get(service, {})
        if state.get("state") == "running" and state.get("health") in ("", "healthy"):
            return True
        if state.get("state") in ("exited", "dead"):
            return False
        time.sleep(4)
    return False


def recreate(service, pull):
    args = ["up", "-d", "--no-deps"] + ([] if pull else ["--pull", "never"]) + [service]
    result = ctl.compose(*args, check=False, capture=True)
    if result.returncode != 0:
        raise ctl.SeedboxError(f"{service} : démarrage impossible : {(result.stderr or result.stdout or '').strip()[-300:]}")


def update(service):
    """Télécharge la dernière image du service et le recrée ; revient en arrière si le service ne repart pas."""
    info = known(service)
    before = snapshot(service, info)
    pins = pins_read()
    if service in pins:
        del pins[service]
        pins_write(pins)
        ctl.say(f"{service} : plus épinglé")
    ctl.say(f"{service} : téléchargement de {info['ref']}")
    pulled = ctl.compose("pull", "--quiet", service, check=False, capture=True)
    if pulled.returncode != 0:
        raise ctl.SeedboxError(f"{service} : téléchargement impossible : {(pulled.stderr or pulled.stdout or '').strip()[-300:]}")
    new_id = ctl.run("docker", "image", "inspect", "-f", "{{.Id}}", info["ref"], check=False, capture=True).stdout.strip()
    if new_id and new_id == info["image_id"] and info["running"]:
        ctl.say(f"{service} : déjà à jour")
        return "déjà à jour"
    ctl.say(f"{service} : recréation")
    recreate(service, pull=False)
    if wait_ready(service):
        ctl.say(f"{service} : mis à jour")
        return "mis à jour"
    ctl.say(f"{service} : ne repart pas avec la nouvelle version, retour à la précédente")
    if before:
        rollback(service, before)
    raise ctl.SeedboxError(f"{service} : la nouvelle version ne démarre pas ; version précédente rétablie")


def rollback(service, target=None):
    """Rétablit une version conservée (la plus récente par défaut) et épingle le service."""
    info = known(service)
    short = info["image_id"].replace("sha256:", "")[:12]
    candidates = [s for s in snapshots(service) if s["id"] != short]
    if target:
        wanted = [s for s in candidates if s["tag"] == target or s["id"] == target]
    else:
        wanted = candidates
    if not wanted:
        raise ctl.SeedboxError(f"{service} : aucune version précédente conservée")
    chosen = wanted[0]
    snapshot(service, info)                    # la version qu'on quitte reste disponible
    ctl.say(f"{service} : retour à la version {chosen['version'] or chosen['id']} ({chosen['created'][:10]})")
    ctl.run("docker", "tag", f"{REPO}/{service}:{chosen['tag']}", info["ref"], capture=True)
    recreate(service, pull=False)
    pins = pins_read()
    pins[service] = {"since": int(time.time()), "image": chosen["id"]}
    pins_write(pins)
    if not wait_ready(service):
        raise ctl.SeedboxError(f"{service} : ne repart pas avec la version {chosen['id']}")
    ctl.say(f"{service} : rétabli et épinglé (aucune mise à jour automatique) ; « Mettre à jour » le libère")
    return chosen["id"]


def unpin(service):
    pins = pins_read()
    if service not in pins:
        raise ctl.SeedboxError(f"{service} n'est pas épinglé")
    del pins[service]
    pins_write(pins)
    ctl.say(f"{service} : de nouveau mis à jour automatiquement")


def unpinned_services():
    """Services à mettre à jour en bloc : tous sauf les services épinglés."""
    pins = pins_read()
    result = ctl.compose("config", "--services", check=False, capture=True)
    return [name for name in (result.stdout or "").split() if name not in pins and name not in LOCAL_SERVICES]
