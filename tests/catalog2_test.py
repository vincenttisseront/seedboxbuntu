"""Catalogue : applications sans interface, Recyclarr/Bazarr/Unpackerr, packs ; versions : mise à jour et
retour arrière (Docker simulé en mémoire, sans conteneur)."""
import os as _os
SRC = _os.environ.get("SB_SRC") or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbcat"))
for k, d in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[k] = str(root / d)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

apps = ctl.apps()
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220])


# ---------------------------------------------------------------- catalogue intégré
built = apps.builtin()
check("6 applications intégrées valides", sorted(built) == ["audiobookshelf", "bazarr", "lidarr", "navidrome", "recyclarr", "unpackerr"], sorted(built))
check("Recyclarr et Unpackerr sans interface", built["recyclarr"]["headless"] and built["unpackerr"]["headless"] and not built["bazarr"]["headless"])
check("Bazarr routée sur /bazarr", built["bazarr"]["path"] == "/bazarr" and built["bazarr"]["port"] == 6767)

# une entrée du web ne peut pas être sans interface ni utiliser les clés de la stack
web = {"id": "monapp", "name": "x", "image": "ghcr.io/exemple/monapp:1", "volumes": ["${CONFIG_DIR}/monapp:/config"]}
for label, entry in {"sans port (web)": web,
                     "clé Radarr (web)": {**web, "port": 80, "env": {"K": "${RADARR_API_KEY}"}},
                     "clé qBittorrent": {**web, "port": 80, "env": {"K": "${QBIT_PASSWORD}"}}}.items():
    try:
        apps.validate(entry)
        check(f"refusé : {label}", False)
    except apps.CatalogError:
        check(f"refusé : {label}", True)
entry = apps.validate({**web, "env": {"K": "${RADARR_API_KEY}"}}, trusted=True)
check("entrée de confiance sans port et avec clé Radarr acceptée", entry["headless"] and entry["path"] == "")

# service compose d'une application sans interface
definition = apps.service_definition(built["unpackerr"])
check("sans interface : aucune étiquette Traefik", "labels" not in definition and "healthcheck" not in definition)
check("sans interface : utilisateur et clés", definition["user"] == "${PUID}:${PGID}" and definition["environment"]["UN_RADARR_0_API_KEY"] == "${RADARR_API_KEY}")
web_def = apps.service_definition(built["bazarr"])
check("Bazarr : routeur Traefik et protection Authelia", "app-bazarr" in json.dumps(web_def["labels"]) and "authelia@file" in json.dumps(web_def["labels"]))

# ---------------------------------------------------------------- YAML de Bazarr
bazarr = """analytics:
  enabled: true
auth:
  apikey: abc
  type: null
general:
  base_url: ''
  use_radarr: false
  use_sonarr: false
  port: 6767

radarr:
  apikey: ''
  base_url: /
  ip: 127.0.0.1
  port: 7878
  ssl: false
sonarr:
  apikey: ''
  ip: 127.0.0.1
"""
env = {"RADARR_API_KEY": "r" * 32, "SONARR_API_KEY": "s" * 32}
done = apps.bazarr_config_text(bazarr, env)
check("Bazarr : base_url et utilisation de Radarr/Sonarr", "  base_url: /bazarr" in done and "  use_radarr: true" in done and "  use_sonarr: true" in done)
check("Bazarr : Radarr relié", '  apikey: "' + "r" * 32 + '"' in done and "  ip: radarr" in done and "  base_url: /radarr" in done)
check("Bazarr : clé manquante ajoutée à Sonarr", "  base_url: /sonarr" in done and "  port: 8989" in done)
check("Bazarr : autres sections intactes", "auth:\n  apikey: abc\n  type: null" in done and "  port: 6767" in done)
check("Bazarr : idempotent", apps.bazarr_config_text(done, env) == done)
check("Bazarr : section absente créée", "sonarr:" in apps.yaml_section_set("a:\n  b: 1\n", "sonarr", {"ip": "x"}))

# ---------------------------------------------------------------- Recyclarr
for preset in ("1080p", "2160p"):
    text = apps.recyclarr_config(preset)
    check(f"Recyclarr {preset} : modèles Radarr et Sonarr", text.count("- template:") == 6 and ("uhd" in text) == (preset == "2160p"))
check("Recyclarr : clés lues dans l'environnement", "!env_var RADARR_API_KEY" in text and "RADARR_API_KEY" not in text.replace("!env_var RADARR_API_KEY", ""))
check("Recyclarr : marque de gestion en première ligne", text.startswith(apps.RECYCLARR_MARK))
try:
    apps.set_preset("720p")
    check("préréglage inconnu refusé", False)
except apps.CatalogError:
    check("préréglage inconnu refusé", True)

# ---------------------------------------------------------------- packs
packs = apps.packs_read()
check("4 packs", [p["id"] for p in packs] == ["musique", "livres", "sous-titres", "qualite"], [p["id"] for p in packs])
listing = apps.packs_listing({"lidarr": {}})
music = next(p for p in listing if p["id"] == "musique")
check("pack musique : Lidarr installée, Navidrome non", [m["installed"] for m in music["apps"]] == [True, False] and not music["complete"])

calls = []
apps.installed_read = lambda: {"lidarr": {}}
orig_install = apps.install


def fake_install(ident):
    calls.append(ident)
    if ident == "recyclarr":
        raise ctl.SeedboxError("échec simulé")


apps.install = fake_install
apps.install_pack("musique")
check("pack : n'installe que ce qui manque", calls == ["navidrome"], calls)
calls.clear()
try:
    apps.install_pack("qualite")
    check("pack : échec signalé en fin de course", False)
except apps.CatalogError as error:
    check("pack : une app en échec n'empêche pas l'autre", calls == ["recyclarr", "unpackerr"] and "recyclarr" in str(error), (calls, str(error)))
try:
    apps.install_pack("inconnu")
    check("pack inconnu refusé", False)
except apps.CatalogError:
    check("pack inconnu refusé", True)
apps.install = orig_install

# ---------------------------------------------------------------- versions (Docker simulé)
ver = ctl.versions()
ver.PINS_FILE = ctl.STATE / "pins.json"
ver.PINS_COMPOSE = ctl.BASE / "docker-compose.pins.yml"
ctl.BASE.mkdir(parents=True, exist_ok=True)
ctl.STATE.mkdir(parents=True, exist_ok=True)

images = {  # identifiant -> description
    "sha256:" + "a" * 64: {"created": "2026-09-01T10:00:00", "version": "5.1.0"},
    "sha256:" + "b" * 64: {"created": "2026-10-01T10:00:00", "version": "5.2.0"},
    "sha256:" + "c" * 64: {"created": "2026-11-01T10:00:00", "version": "5.3.0"},
}
tags = {"lscr.io/linuxserver/radarr:latest": "sha256:" + "a" * 64}     # étiquette -> image
containers = {"radarr": {"image": "sha256:" + "a" * 64, "ref": "lscr.io/linuxserver/radarr:latest", "running": True},
              "sonarr": {"image": "sha256:" + "a" * 64, "ref": "lscr.io/linuxserver/sonarr:latest", "running": True}}
tags["lscr.io/linuxserver/sonarr:latest"] = "sha256:" + "a" * 64
remote = {"lscr.io/linuxserver/radarr:latest": "sha256:" + "b" * 64, "lscr.io/linuxserver/sonarr:latest": "sha256:" + "a" * 64}
broken = set()          # images qui font échouer le démarrage


class Done:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def resolve(name):
    if name in images:
        return name
    if name in tags:
        return tags[name]
    for full in images:
        if full.startswith("sha256:" + name) or full == name:
            return full
    return None


def fake_run(*command, check=True, capture=False, **kw):
    command = [str(c) for c in command]
    assert command[0] == "docker", command
    verb = command[1]
    if verb == "ps":
        return Done("\n".join(containers))
    if verb == "inspect":
        data = [{"Name": "/" + n, "Image": c["image"], "Config": {"Image": c["ref"], "Labels": {
            "com.docker.compose.service": n, "com.docker.compose.project": "seedbox"}}, "State": {"Running": c["running"]}}
            for n, c in containers.items() if n in command[2:]]
        return Done(json.dumps(data))
    if verb == "image" and command[2] == "inspect":
        image = resolve(command[-1])
        if not image:
            return Done("", 1, "no such image")
        if "-f" in command:
            return Done(image + "\n")
        info = images[image]
        return Done(json.dumps([{"Id": image, "Created": info["created"] + "Z", "Config": {"Labels": {"org.opencontainers.image.version": info["version"]}}}]))
    if verb == "image" and command[2] == "ls":
        repo = command[3]
        return Done("\n".join(t.split(":", 1)[1] for t in tags if t.startswith(repo + ":")))
    if verb == "tag":
        tags[command[3]] = resolve(command[2])
        return Done()
    if verb == "rmi":
        tags.pop(command[2], None)
        return Done()
    if verb == "stop" or verb == "start":
        return Done()
    raise AssertionError(command)


compose_log = []


def fake_compose(*args, check=True, capture=False):
    args = list(args)
    compose_log.append(args)
    if args[0] == "config":
        return Done("\n".join(["radarr", "sonarr", "p2p-aggregator"]) + "\n")
    if args[0] == "pull":
        service = args[-1]
        ref = containers[service]["ref"]
        tags[ref] = remote[ref]
        return Done()
    if args[0] == "up":
        service = args[-1]
        ref = containers[service]["ref"]
        containers[service]["image"] = tags[ref]
        containers[service]["running"] = containers[service]["image"] not in broken
        return Done()
    raise AssertionError(args)


ctl.run, ctl.compose = fake_run, fake_compose
ctl.containers = lambda: {n: {"state": "running" if c["running"] else "exited", "health": ""} for n, c in containers.items()}
ctl.say = lambda message: print("   ·", message)
ver.WAIT = 1
ver.time.sleep = lambda s: None

check("snapshot_all : un service sauvegardé par service", ver.snapshot_all() == 2)
check("étiquette de retour arrière créée", "seedbox-rollback/radarr:" + "a" * 12 in tags)
rows = {r["service"]: r for r in ver.listing()["services"]}
check("listing : version et aucune version précédente", rows["radarr"]["version"] == "5.1.0" and rows["radarr"]["previous"] == [])

check("mise à jour Radarr", ver.update("radarr") == "mis à jour" and containers["radarr"]["image"].endswith("b" * 64))
check("mise à jour Sonarr déjà à jour", ver.update("sonarr") == "déjà à jour")
rows = {r["service"]: r for r in ver.listing()["services"]}
check("version précédente proposée", [p["version"] for p in rows["radarr"]["previous"]] == ["5.1.0"], rows["radarr"]["previous"])

ver.rollback("radarr")
check("retour arrière : ancienne image active", containers["radarr"]["image"].endswith("a" * 64))
check("retour arrière : service épinglé", "radarr" in ver.pins_read() and ver.PINS_COMPOSE.exists()
      and "watchtower.enable" in ver.PINS_COMPOSE.read_text())
check("retour arrière : mise à jour en bloc l'ignore", ver.unpinned_services() == ["sonarr"], ver.unpinned_services())
rows = {r["service"]: r for r in ver.listing()["services"]}
check("retour arrière : la version quittée reste disponible", [p["version"] for p in rows["radarr"]["previous"]] == ["5.2.0"], rows["radarr"]["previous"])

ver.unpin("radarr")
check("libéré : fichier d'épinglage supprimé", not ver.PINS_COMPOSE.exists() and ver.pins_read() == {})

# mise à jour qui ne démarre pas -> retour automatique
remote["lscr.io/linuxserver/radarr:latest"] = "sha256:" + "c" * 64
broken.add("sha256:" + "c" * 64)
try:
    ver.update("radarr")
    check("mise à jour défaillante signalée", False)
except ctl.SeedboxError as error:
    check("mise à jour défaillante : erreur claire", "précédente rétablie" in str(error), str(error))
check("mise à jour défaillante : ancienne version relancée", containers["radarr"]["running"] and containers["radarr"]["image"].endswith("a" * 64),
      containers["radarr"])

for label, call in {"service inconnu": lambda: ver.update("inconnu"), "nom invalide": lambda: ver.update("../x"),
                    "image locale": lambda: ver.update("p2p-aggregator")}.items():
    try:
        call()
        check(f"refusé : {label}", False)
    except ctl.SeedboxError:
        check(f"refusé : {label}", True)

# conservation limitée à KEEP versions
for index in range(6):
    new = "sha256:" + format(index + 10, "x") * 64
    images[new] = {"created": f"2026-12-0{index + 1}T10:00:00", "version": f"6.{index}"}
    containers["sonarr"]["image"] = new
    ver.snapshot("sonarr")
kept = [t for t in tags if t.startswith("seedbox-rollback/sonarr:")]
check(f"au plus {ver.KEEP + 1} étiquettes gardées par service", len(kept) <= ver.KEEP + 1, len(kept))

print("échecs :", bad)
sys.exit(1 if bad else 0)
