"""SeedBoxBuntu - rotation guidée des secrets internes : un nouveau secret, repropagé, vérifié, avec retour en cas d'échec.

Secrets que la console sait renouveler sans perte :
  - radarr, sonarr, prowlarr : clé d'API de l'application. Le nouveau secret est écrit dans .env, les modèles sont régénérés
    (Homepage, qbit-manage...), la pile Docker recrée ce qui en dépend, puis le provisionnement relie à nouveau les applications
    (Prowlarr ↔ Radarr/Sonarr, Seerr, Bazarr...). On vérifie alors que l'application répond avec la nouvelle clé ; sinon l'ancienne
    clé est remise et tout est relié à nouveau.
  - console : secret partagé entre Traefik et la console (en-têtes X-Seedbox-*-Secret). Renouvelé dans .env et dans la
    configuration de Traefik, qui la relit seule ; les pages ouvertes se rechargent d'un clic.
Ne sont PAS renouvelés ici : les secrets d'Authelia (la clé de stockage chiffre sa base : la changer la rend illisible), les
mots de passe des comptes (Comptes), la clé de chiffrement des sauvegardes (la changer rendrait les anciennes sauvegardes
illisibles) et le mot de passe de qBittorrent.
Pendant une rotation d'une clé d'API, l'application concernée est indisponible quelques dizaines de secondes. L'ancienne valeur
n'est jamais écrite dans le journal ; elle n'est gardée qu'en mémoire le temps de la vérification.
    seedboxctl rotation show | run ITEM
"""
import json
import time

import seedboxctl as ctl

STATE_FILE = ctl.STATE / "rotation.json"
ITEMS = {
    "radarr": ("Radarr", "RADARR_API_KEY", 16),
    "sonarr": ("Sonarr", "SONARR_API_KEY", 16),
    "prowlarr": ("Prowlarr", "PROWLARR_API_KEY", 16),
    "console": ("Secret console ↔ Traefik", "CONSOLE_PROXY_SECRET", 32),
}
OLD_AFTER_DAYS = 365
WAIT = 180


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def history():
    try:
        value = json.loads(STATE_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(item, by, now):
    data = history()
    data[item] = {"at": int(now), "by": by}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data))


def overview(now=None):
    now = time.time() if now is None else now
    data = history()
    env = ctl.env_read()
    rows = []
    for key, (label, var, _) in ITEMS.items():
        stored = data.get(key, {})
        present = bool(env.get(var)) and env.get(var) != "__AUTO__"
        age = int((now - stored["at"]) / 86400) if stored.get("at") else None
        rows.append({"id": key, "label": label, "present": present, "last": stored.get("at", 0), "by": stored.get("by", ""), "age_days": age,
                     "running": bool(ctl.container_running(key)) if key != "console" else True})
    return {"items": rows, "old_after_days": OLD_AFTER_DAYS}


class Applier:
    """Les gestes qui touchent le serveur : isolés pour que les tests les remplacent."""

    def write(self, var, value):
        ctl.env_set(**{var: value})

    def apply(self):
        ctl.render_templates()
        ctl.compose("up", "-d", "--remove-orphans", capture=True)
        errors = ctl.provision()
        if errors:
            raise ctl.SeedboxError("provisionnement : " + "; ".join(errors)[:200])

    def render_only(self):
        ctl.render_templates()

    def verify(self, item, wait=WAIT, sleep=time.sleep, clock=time.time):
        if item == "console":
            return True
        deadline = clock() + wait
        while clock() < deadline:
            try:
                if ctl.arr_api(item, "GET", "/system/status").ok:
                    return True
            except (ctl.SeedboxError, OSError):
                pass
            sleep(4)
        return False


def rotate(item, by="console", applier=None, now=None, new_value=None):
    if item not in ITEMS:
        raise ctl.SeedboxError("secret inconnu : " + str(item))
    label, var, size = ITEMS[item]
    applier = applier or Applier()
    now = time.time() if now is None else now
    env = ctl.env_read()
    old = env.get(var, "")
    if not old or old == "__AUTO__":
        raise ctl.SeedboxError(f"{label} : aucun secret en place à renouveler")
    new = new_value or ctl.hex_secret(size)
    say(f"rotation : {label}")
    applier.write(var, new)
    try:
        if item == "console":
            applier.render_only()
            ok = True
        else:
            applier.apply()
            ok = applier.verify(item)
        if not ok:
            raise ctl.SeedboxError(f"{label} ne répond pas avec le nouveau secret")
    except Exception as error:
        say(f"rotation de {label} échouée ({str(error)[:120]}) : retour à l'ancien secret")
        applier.write(var, old)
        try:
            applier.render_only() if item == "console" else applier.apply()
        except Exception as again:  # noqa: BLE001
            say(f"retour incomplet : {str(again)[:120]}")
            raise ctl.SeedboxError(f"{label} : échec et retour incomplet, lancez « Relancer la configuration des applications »")
        raise ctl.SeedboxError(f"{label} : échec, l'ancien secret est rétabli ({str(error)[:120]})")
    _record(item, by, now)
    try:
        ctl.audit().record(by, "", f"Secret renouvelé : {label}", "", "console", "ok")
        ctl.notifications().record("rotation", f"Secret renouvelé : {label}", "", "ok", "secrets")
    except Exception:  # noqa: BLE001
        pass
    say(f"secret renouvelé : {label}")
    return {"item": item}

