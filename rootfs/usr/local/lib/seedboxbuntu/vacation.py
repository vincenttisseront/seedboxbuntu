"""SeedBoxBuntu - mode vacances : tout se calme jusqu'à une date, puis reprend seul.

Pendant les vacances :
  - les torrents en cours sont mis en pause (ceux qui l'étaient déjà ne sont pas touchés) et les nouveaux torrents
    démarrent en pause : Radarr et Sonarr peuvent continuer à chercher, rien ne se télécharge ;
  - les liens d'invitation sont refusés (la page d'invitation affiche « lien invalide ») ; les invitations ne sont ni
    révoquées ni modifiées, elles refonctionnent à la fin.
Le serveur, les comptes existants, Jellyfin et Seerr continuent de fonctionner normalement.
À la date de fin (ou à « Reprendre »), seuls les torrents mis en pause par ce mode reprennent, et la préférence de
qBittorrent est remise comme avant. Le passage de surveillance de 5 minutes termine le mode à l'heure.
    seedboxctl vacation show | start --until AAAA-MM-JJ | stop | tick
"""
import json
import os
import re
import time

import seedboxctl as ctl

STATE_FILE = ctl.STATE / "vacation.json"
PAUSED_STATES = ("pausedDL", "pausedUP", "stoppedDL", "stoppedUP", "error", "missingFiles")
MAX_DAYS = 120
HASH = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def state():
    try:
        value = json.loads(STATE_FILE.read_text())
        return value if isinstance(value, dict) and value.get("until") else {}
    except (OSError, ValueError):
        return {}


def active(now=None):
    current = state()
    return bool(current) and (time.time() if now is None else now) < current["until"]


# --- qBittorrent (v4 : pause/resume, v5 : stop/start) -----------------------------------------------
class Qbit:
    def __init__(self):
        self.cookie = ctl.qbit_login()
        self.base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"

    def call(self, method, path, form=None):
        return ctl.request(method, self.base + path, headers={"Cookie": self.cookie}, form=form, timeout=30)

    def torrents(self):
        return self.call("GET", "/torrents/info").json() or []

    def hashes_action(self, names, hashes):
        """Essaie le nom d'action de qBittorrent 4 puis celui de la version 5."""
        for name in names:
            for start in range(0, len(hashes), 40):
                result = self.call("POST", f"/torrents/{name}", {"hashes": "|".join(hashes[start:start + 40])})
                if result.status == 404:
                    break
                if result.status >= 400:
                    raise ctl.SeedboxError(f"qBittorrent : {name} refusé (HTTP {result.status})")
            else:
                return
        raise ctl.SeedboxError("qBittorrent : action de pause inconnue")

    def pause(self, hashes):
        if hashes:
            self.hashes_action(("pause", "stop"), hashes)

    def resume(self, hashes):
        if hashes:
            self.hashes_action(("resume", "start"), hashes)

    def preferences(self):
        return self.call("GET", "/app/preferences").json() or {}

    def set_preferences(self, values):
        result = self.call("POST", "/app/setPreferences", {"json": json.dumps(values)})
        if result.status >= 400:
            raise ctl.SeedboxError(f"qBittorrent : réglage refusé (HTTP {result.status})")


def _write(value):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    temp.write_text(json.dumps(value))
    os.chmod(temp, 0o644)
    os.replace(temp, STATE_FILE)


def parse_until(text, now=None):
    now = time.time() if now is None else now
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(text or "")):
        raise ctl.SeedboxError("date de reprise : AAAA-MM-JJ attendu")
    until = time.mktime(time.strptime(text + " 00:00", "%Y-%m-%d %H:%M"))
    if until <= now:
        raise ctl.SeedboxError("la date de reprise doit être dans le futur")
    if until > now + MAX_DAYS * 86400:
        raise ctl.SeedboxError(f"la reprise est limitée à {MAX_DAYS} jours : revenez prolonger si besoin")
    return int(until)


def start(until_text, by="", qbit=None, now=None):
    now = time.time() if now is None else now
    until = parse_until(until_text, now)
    if state():
        raise ctl.SeedboxError("le mode vacances est déjà actif : reprenez d'abord, puis relancez-le avec la nouvelle date")
    paused, previous = [], None
    if ctl.qbit_mode():
        try:
            qbit = qbit or Qbit()
            running = [t["hash"] for t in qbit.torrents() if t.get("state") not in PAUSED_STATES and HASH.match(t.get("hash", ""))]
            previous = bool(qbit.preferences().get("start_paused_enabled", qbit.preferences().get("add_stopped_torrents", False)))
            qbit.pause(running)
            qbit.set_preferences({"start_paused_enabled": True, "add_stopped_torrents": True})
            paused = running
        except (OSError, ctl.SeedboxError) as error:
            raise ctl.SeedboxError(f"qBittorrent n'a pas pu être mis en pause : {error}")
    _write({"until": until, "started": int(now), "by": str(by)[:30], "paused": paused, "previous_start_paused": previous})
    ctl.notifications().record("vacation", "Mode vacances activé", f"jusqu'au {time.strftime('%d/%m/%Y', time.localtime(until))}", "info", "vacation")
    say(f"mode vacances jusqu'au {time.strftime('%Y-%m-%d', time.localtime(until))} : {len(paused)} torrent(s) en pause")
    return state()


def stop(qbit=None, reason="reprise"):
    current = state()
    if not current:
        return {}
    errors = []
    if ctl.qbit_mode() and (current.get("paused") or current.get("previous_start_paused") is not None):
        try:
            qbit = qbit or Qbit()
            known = {t["hash"] for t in qbit.torrents()}
            qbit.set_preferences({"start_paused_enabled": bool(current.get("previous_start_paused")),
                                  "add_stopped_torrents": bool(current.get("previous_start_paused"))})
            qbit.resume([h for h in current.get("paused", []) if h in known])
        except (OSError, ctl.SeedboxError) as error:
            errors.append(str(error))
    if errors:
        # Le mode reste actif pour qu'un nouvel essai ait lieu (tick) : rien n'est oublié à moitié
        raise ctl.SeedboxError("la reprise a échoué, nouvel essai au prochain passage : " + errors[0])
    STATE_FILE.unlink(missing_ok=True)
    ctl.notifications().record("vacation", "Mode vacances terminé", reason, "ok", "vacation")
    try:
        ctl.alerts().notify("Mode vacances terminé", "Les téléchargements et les invitations ont repris.", "ok")
    except Exception:  # noqa: BLE001
        pass
    say("mode vacances terminé : torrents repris")
    return {}


def tick(now=None, qbit=None):
    """Passage de surveillance : termine le mode à l'heure. Renvoie True s'il vient de se terminer."""
    current = state()
    if not current or (time.time() if now is None else now) < current["until"]:
        return False
    stop(qbit, "date atteinte")
    return True


def overview(now=None):
    current = state()
    now = time.time() if now is None else now
    return {"active": bool(current) and now < current["until"], "until": current.get("until", 0), "started": current.get("started", 0),
            "by": current.get("by", ""), "paused": len(current.get("paused", [])), "max_days": MAX_DAYS,
            "qbittorrent": bool(ctl.qbit_mode())}
