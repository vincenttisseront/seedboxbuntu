"""Mise à jour N-1 -> N de l'appliance (seedboxctl upgrade) : sans Docker, tout ce qui touche au système est simulé.

Cas couverts
  - Jackett démarré par une version sans configuration (clé aléatoire, pas de /jackett) : réparé, indexeurs gardés
  - Authelia et Traefik redémarrés seulement si leur configuration change (pas à chaque nouvel essai)
  - une étape annexe en échec ne fait pas échouer la mise à jour ; une étape critique, si
  - la mise à jour échouée garde son marqueur, la réussie l'efface et s'inscrit dans l'historique
  - abandon d'une mise à jour qui boucle
"""
import os as _os
SRC = _os.environ.get("SB_SRC") or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbup"))
for k, d in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[k] = str(root / d)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as c  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:200], flush=True)


# --- état de départ : une appliance déployée avec la version N-1 -------------------------------------------------
for path in (c.BASE, c.STATE, c.ETC, c.CONFIG, c.APPS_DIR):
    path.mkdir(parents=True, exist_ok=True)
(c.STATE / "deployed").write_text("2026-10-01\n")
(c.STATE / "upgrade-pending").write_text("2026.10.01 -> 2026.11.09\n")
key = "k" * 32
ENV = {"PUID": os.getuid(), "PGID": os.getgid(), "JACKETT_API_KEY": key, "DOMAIN": "seedbox.local"}
c.env_read = lambda: {k: str(v) for k, v in ENV.items()}
jackett = c.CONFIG / "Jackett" if False else c.CONFIG / "jackett" / "Jackett" / "ServerConfig.json"
jackett.parent.mkdir(parents=True, exist_ok=True)
jackett.write_text(json.dumps({"Port": 9117, "APIKey": "aleatoire-de-jackett", "BasePathOverride": "", "OmdbApiKey": "garder-moi"}))
front = c.CONFIG / "authelia" / "configuration.yml"
front.parent.mkdir(parents=True, exist_ok=True)
front.write_text("version: N-1\n")

# --- doublures -----------------------------------------------------------------------------------------------------
calls = {"restart": [], "compose": [], "say": []}


class Maintenance:
    @staticmethod
    def harden():
        return []

    @staticmethod
    def apply_apps():
        pass


c.maintenance = lambda: Maintenance
for name in ("ensure_env", "update_addresses", "ensure_certificates", "set_ownership", "p2p_ensure_image", "refresh_firewall_safely"):
    setattr(c, name, lambda *a, **k: None)
c.version = lambda: "2026.11.09"
c.say = lambda message: calls["say"].append(message)
c.compose = lambda *a, **k: calls["compose"].append(a)
render = {"content": "version: N-1\n"}
c.render_templates = lambda: front.write_text(render["content"])


class Done:
    returncode, stdout, stderr = 0, "", ""


def fake_run(*command, **kwargs):
    if command[:2] == ("docker", "restart"):
        calls["restart"].append(command[2:])
    return Done()


c.run = fake_run
outcome = {"critical": None, "jackett": None}


def provision_steps():
    def critical():
        if outcome["critical"]:
            raise c.SeedboxError(outcome["critical"])

    def jackett_step():
        if outcome["jackett"]:
            raise c.SeedboxError(outcome["jackett"])
    return [("Radarr/Sonarr/Prowlarr", critical), ("Jackett", jackett_step), ("trackers", lambda: None)]


c.provision_steps = provision_steps


def attempt():
    calls["restart"].clear()
    try:
        c.upgrade()
        return None
    except c.SeedboxError as error:
        return str(error)


# --- 1. première tentative : configuration d'Authelia modifiée par la nouvelle version, Jackett à réparer -------------
render["content"] = "version: N\n"
outcome["jackett"] = "cookie non délivré"           # étape annexe en échec
error = attempt()
config = json.loads(jackett.read_text())
check("Jackett réparé : clé de .env et chemin /jackett", config["APIKey"] == key and config["BasePathOverride"] == "/jackett", config)
check("Jackett : réglages existants conservés", config.get("OmdbApiKey") == "garder-moi")
check("Jackett en échec n'échoue pas la mise à jour", error is None, error)
check("mise à jour réussie : marqueur retiré et historique écrit",
      not (c.STATE / "upgrade-pending").exists() and "version 2026.11.09" in (c.STATE / "upgrade-history").read_text())
check("Authelia et Traefik redémarrés (configuration changée)", calls["restart"] == [("traefik", "authelia")], calls["restart"])
status = c.steps_load()
check("l'étape Jackett est suivie pour un nouvel essai", status["Jackett"]["ok"] is False and status["Jackett"]["critical"] is False)
check("l'étape critique est notée réussie", status["Radarr/Sonarr/Prowlarr"]["ok"] is True)

# --- 2. étape critique en échec : la mise à jour boucle sans déconnecter personne --------------------------------------
(c.STATE / "upgrade-pending").write_text("2026.10.01 -> 2026.11.09\n")
(c.STATE / "upgrade-history").unlink()
outcome["critical"] = "Radarr injoignable"
for essai in (1, 2, 3):
    error = attempt()
    check(f"essai {essai} : échec signalé", bool(error) and "Radarr injoignable" in error, error)
    check(f"essai {essai} : aucun redémarrage d'Authelia (configuration inchangée)", calls["restart"] == [], calls["restart"])
check("marqueur conservé tant que ça échoue", (c.STATE / "upgrade-pending").exists())
check("pas d'historique tant que ça échoue", not (c.STATE / "upgrade-history").exists())

# --- 3. la configuration change pendant la boucle : un seul redémarrage ----------------------------------------------
render["content"] = "version: N+1\n"
attempt()
check("configuration modifiée : redémarrage une fois", calls["restart"] == [("traefik", "authelia")], calls["restart"])
attempt()
check("essai suivant : de nouveau aucun redémarrage", calls["restart"] == [], calls["restart"])

# --- 4. reprise : l'étape critique repart, la mise à jour se termine -----------------------------------------------------
outcome["critical"] = None
outcome["jackett"] = None
error = attempt()
check("mise à jour terminée après correction", error is None and not (c.STATE / "upgrade-pending").exists(), error)
c.steps_retry()
check("étape annexe corrigée au passage horaire", c.steps_load()["Jackett"]["ok"] is True)

# --- 5. abandon d'une mise à jour qui boucle ---------------------------------------------------------------------------
(c.STATE / "upgrade-pending").write_text("2026.10.01 -> 2026.12.01\n")
c.upgrade_skip()
check("abandon : marqueur retiré et tracé", not (c.STATE / "upgrade-pending").exists() and "ABANDONNÉE" in (c.STATE / "upgrade-history").read_text())
try:
    c.upgrade_skip()
    check("abandon sans mise à jour en attente refusé", False)
except c.SeedboxError:
    check("abandon sans mise à jour en attente refusé", True)

# --- 6. appliance non déployée : rien n'est démarré ----------------------------------------------------------------------
(c.STATE / "deployed").unlink()
(c.STATE / "upgrade-pending").write_text("a -> b\n")
calls["compose"].clear()
error = attempt()
check("non déployée : aucune action sur la stack", error is None and calls["compose"] == [], calls["compose"])

print("échecs :", bad)
sys.exit(1 if bad else 0)
