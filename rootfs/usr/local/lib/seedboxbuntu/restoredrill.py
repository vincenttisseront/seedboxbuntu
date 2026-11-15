"""SeedBoxBuntu - simulation de restauration complète : la sauvegarde démarre-t-elle vraiment ?

« backup check » prouve qu'une sauvegarde se déchiffre et que ses bases sont saines. Ici on va plus loin : pour Radarr,
Sonarr, Prowlarr (et Lidarr s'il est installé), la configuration de la sauvegarde est extraite dans un dossier temporaire,
puis l'application est démarrée dans un conteneur JETABLE :
  - sans réseau (--network none) : il ne peut contacter ni les trackers, ni les autres applications, ni Internet ;
  - sans port publié ni lien avec les vrais dossiers de médias : seul le dossier temporaire de configuration est monté ;
  - limité en mémoire et en processeur, étiqueté, supprimé à la fin quoi qu'il arrive (et les restes d'une exécution
    interrompue sont supprimés au passage suivant).
On vérifie qu'il répond (/ping), que la clé d'API de la sauvegarde ouvre bien l'API, et on compte les éléments retrouvés
(films, séries, indexeurs). Les vraies applications ne sont jamais touchées : rien n'est arrêté, rien n'est restauré.
Limites assumées : seule la configuration (réglages et base de chaque application) est essayée, pas Jellyfin ni Authelia ;
un démarrage réussi sans réseau ne prouve pas que les liens entre applications fonctionneront ; il faut environ 1,5 Go de
mémoire libre et quelques minutes par application.
    seedboxctl restoredrill show | run [NOM] [--only SERVICE ...]
"""
import json
import re
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

import seedboxctl as ctl

LAST_FILE = ctl.STATE / "restoredrill.json"
LABEL = "seedboxbuntu.drill=1"
PREFIX = "seedbox-drill-"
# service -> (port interne, chemin de l'API, ressource comptée, clé de config.xml)
APPS = {"radarr": (7878, "/api/v3", "movie", "movies"), "sonarr": (8989, "/api/v3", "series", "series"),
        "prowlarr": (9696, "/api/v1", "indexer", "indexers"), "lidarr": (8686, "/api/v1", "artist", "artists")}
MEMORY = "768m"
MIN_FREE_MB = 1500
WAIT = 150
NAME = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def docker(*args, timeout=60):
    """Remplacée par les tests : (code, sortie)."""
    result = ctl.run("docker", *args, check=False, capture=True)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def free_memory_mb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def installed(runner=docker):
    """Services dont le conteneur existe sur ce serveur (donc dont l'image est connue)."""
    found = {}
    for service in APPS:
        code, out = runner("inspect", "-f", "{{.Config.Image}}", service)
        if code == 0 and out.strip():
            found[service] = out.strip().splitlines()[0]
    return found


def cleanup_orphans(runner=docker, now=None):
    """Supprime les conteneurs d'essai oubliés (exécution interrompue)."""
    code, out = runner("ps", "-a", "--filter", f"label={LABEL}", "--format", "{{.Names}}")
    removed = 0
    for name in out.split():
        if name.startswith(PREFIX) and runner("rm", "-f", name)[0] == 0:
            removed += 1
    return removed


def find_config(extracted, service):
    """Dossier de configuration du service dans l'extraction : celui qui porte config.xml et se nomme comme lui."""
    candidates = sorted((p.parent for p in extracted.rglob("config.xml") if p.parent.name == service), key=lambda p: len(p.parts))
    return candidates[0] if candidates else None


def api_key(config_dir):
    try:
        match = re.search(r"<ApiKey>([^<]+)</ApiKey>", (config_dir / "config.xml").read_text())
    except OSError:
        return ""
    return match.group(1).strip() if match else ""


def url_base(config_dir):
    try:
        match = re.search(r"<UrlBase>([^<]*)</UrlBase>", (config_dir / "config.xml").read_text())
    except OSError:
        return ""
    base = (match.group(1).strip() if match else "").strip("/")
    return f"/{base}" if base else ""


def copy_config(source, target):
    """Copie la configuration utile : réglages et bases, sans les images et journaux (volumineux, inutiles pour démarrer)."""
    skip = {"MediaCover", "logs", "Backups", "Sentry", "UpdateLogs", "asp", "Definitions"}
    target.mkdir(parents=True)
    for item in source.iterdir():
        if item.name in skip:
            continue
        if item.is_dir():
            shutil.copytree(item, target / item.name, symlinks=False, ignore_dangling_symlinks=True)
        elif item.is_file() and not item.is_symlink():
            shutil.copy2(item, target / item.name)


def try_service(service, image, config_dir, runner=docker, sleep=time.sleep, clock=time.time, wait=WAIT):
    """Démarre un conteneur d'essai et vérifie ce qu'il répond. Toujours supprimé à la fin."""
    port, api, resource, label = APPS[service]
    name = f"{PREFIX}{service}-{uuid.uuid4().hex[:6]}"
    key, base = api_key(config_dir), url_base(config_dir)
    result = {"service": service, "started": False, "answers": False, "api": False, "count": None, "label": label, "seconds": 0, "detail": ""}
    started = clock()
    try:
        code, out = runner("run", "-d", "--name", name, "--label", LABEL, "--network", "none", "--memory", MEMORY, "--cpus", "1",
                           "--pids-limit", "512", "-e", "PUID=1000", "-e", "PGID=1000", "-e", "TZ=UTC",
                           "-v", f"{config_dir}:/config", image)
        if code != 0:
            result["detail"] = "le conteneur n'a pas pu être créé : " + out.strip().splitlines()[-1][:160] if out.strip() else "le conteneur n'a pas pu être créé"
            return result
        result["started"] = True
        deadline = clock() + wait
        while clock() < deadline:
            code, out = runner("exec", name, "curl", "-fsS", "-m", "3", f"http://localhost:{port}{base}/ping")
            if code == 0 and "OK" in out.upper():
                result["answers"] = True
                break
            state = runner("inspect", "-f", "{{.State.Running}}", name)[1].strip()
            if state == "false":
                result["detail"] = "le conteneur s'est arrêté : " + (runner("logs", "--tail", "3", name)[1].strip().splitlines() or [""])[-1][:160]
                return result
            sleep(3)
        if not result["answers"]:
            result["detail"] = "pas de réponse au bout de %d s" % wait
            return result
        if not key:
            result["detail"] = "clé d'API introuvable dans la configuration sauvegardée"
            return result
        code, out = runner("exec", name, "curl", "-fsS", "-m", "10", "-H", f"X-Api-Key: {key}", f"http://localhost:{port}{base}{api}/{resource}")
        if code != 0:
            result["detail"] = "l'API refuse la clé de la sauvegarde"
            return result
        result["api"] = True
        try:
            data = json.loads(out)
            result["count"] = len(data) if isinstance(data, list) else None
        except ValueError:
            result["detail"] = "réponse de l'API illisible"
        return result
    finally:
        result["seconds"] = round(clock() - started, 1)
        runner("rm", "-f", name)


def run(name=None, key_text="", only=None, runner=docker, sleep=time.sleep, clock=time.time, wait=WAIT, root=None):
    backup = ctl.backup()
    cleanup_orphans(runner)
    if free_memory_mb() < MIN_FREE_MB and root is None:
        raise ctl.SeedboxError(f"mémoire disponible insuffisante pour la simulation (il faut environ {MIN_FREE_MB} Mo libres)")
    if name:
        archive = backup.find(name)
    else:
        local = [e for e in backup.listing() if "-avant" not in e["name"]]
        if not local:
            raise ctl.SeedboxError("aucune sauvegarde à essayer")
        archive = backup.find(local[0]["name"])
    if not archive.is_file():
        raise ctl.SeedboxError("sauvegarde introuvable")
    key_text = key_text or (backup.KEY_FILE.read_text() if backup.KEY_FILE.exists() else "")
    if not key_text.strip():
        raise ctl.SeedboxError("clé de chiffrement absente : fournissez celle du serveur d'origine")
    images = installed(runner)
    wanted = [s for s in (only or list(APPS)) if s in APPS]
    wanted = [s for s in wanted if s in images]
    if not wanted:
        raise ctl.SeedboxError("aucune application à essayer (Radarr, Sonarr, Prowlarr ou Lidarr doit exister sur ce serveur)")
    started = clock()
    items = []
    with tempfile.TemporaryDirectory(dir=ctl.STATE if ctl.STATE.exists() else None) as work:
        work = Path(work)
        plain = backup.open_archive(archive, key_text, work)
        extracted = work / "contenu"
        extracted.mkdir()
        with tarfile.open(plain) as tar:
            members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
            try:
                tar.extractall(extracted, members=members, filter="data")
            except TypeError:
                tar.extractall(extracted, members=members)
        for service in wanted:
            source = find_config(extracted, service)
            if not source:
                items.append({"service": service, "started": False, "answers": False, "api": False, "count": None,
                              "label": APPS[service][3], "seconds": 0, "detail": "configuration absente de la sauvegarde"})
                continue
            copy = work / "essai" / service
            try:
                copy_config(source, copy)
            except OSError as error:
                items.append({"service": service, "started": False, "answers": False, "api": False, "count": None,
                              "label": APPS[service][3], "seconds": 0, "detail": f"copie impossible ({error.__class__.__name__})"})
                continue
            items.append(try_service(service, images[service], copy, runner, sleep, clock, wait))
            shutil.rmtree(copy, ignore_errors=True)
    ok = all(i["api"] for i in items)
    report = {"ok": ok, "name": archive.name, "items": items, "seconds": round(clock() - started, 1), "at": int(time.time())}
    try:
        LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_FILE.write_text(json.dumps(report, ensure_ascii=False))
    except OSError:
        pass
    say(f"simulation de restauration de {archive.name} : " + ", ".join(f"{i['service']} {'ok' if i['api'] else 'ÉCHEC'}" for i in items))
    if not ok:
        try:
            ctl.alerts().notify("La simulation de restauration a échoué", "Échec : " + ", ".join(i["service"] for i in items if not i["api"]), "warning")
        except Exception:  # noqa: BLE001
            pass
    return report


def last_report():
    try:
        value = json.loads(LAST_FILE.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def overview():
    images = installed()
    local = [e for e in ctl.backup().listing() if "-avant" not in e["name"]]
    return {"services": sorted(images), "backups": [{"name": e["name"], "time": int(e["time"])} for e in local[:10]],
            "last": last_report(), "free_mb": free_memory_mb(), "needed_mb": MIN_FREE_MB}
