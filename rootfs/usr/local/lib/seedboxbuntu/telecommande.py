"""SeedBoxBuntu - télécommande : la vue réduite de la console pour un téléphone (#telecommande).

Trois informations et deux gestes, rien d'autre :
  - alertes en cours (titre et niveau) ;
  - téléchargements : combien sont actifs, débit montant et descendant, combien sont en pause ;
  - mode vacances (état, date de reprise) ;
  - « Tout mettre en pause » et « Tout reprendre » pour les torrents. La reprise ne relance QUE les torrents que cette
    télécommande a mis en pause : un torrent arrêté à la main reste arrêté.
La page s'installe comme une application (manifeste : raccourcis « Télécommande » et « Alertes »). Hors de la maison, elle
n'est joignable que par l'adresse de la console (domaine) ou Tailscale ; la connexion et la double authentification restent
celles de la console.
    seedboxctl remote-control show | pause | resume
"""
import json
import os
import re
import time

import seedboxctl as ctl

STATE_FILE = ctl.STATE / "remote-paused.json"
DOWNLOADING = ("downloading", "stalledDL", "metaDL", "forcedDL", "queuedDL", "checkingDL", "allocating")
PAUSED = ("pausedDL", "pausedUP", "stoppedDL", "stoppedUP")
HASH = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def paused_by_us():
    try:
        value = json.loads(STATE_FILE.read_text())
        return [h for h in value.get("hashes", []) if HASH.match(str(h))] if isinstance(value, dict) else []
    except (OSError, ValueError):
        return []


def _write(hashes):
    if hashes:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"hashes": hashes, "since": int(time.time())}))
    else:
        STATE_FILE.unlink(missing_ok=True)


def pause(qbit=None):
    """Met en pause tous les torrents qui tournent ; renvoie leur nombre."""
    if ctl.vacation().active():
        raise ctl.SeedboxError("le mode vacances est actif : il gère déjà la pause (reprenez-le depuis « Vacances »)")
    if not ctl.qbit_mode() and qbit is None:
        raise ctl.SeedboxError("qBittorrent n'est pas installé")
    qbit = qbit or ctl.vacation().Qbit()
    running = [t["hash"] for t in qbit.torrents() if t.get("state") not in PAUSED + ("error", "missingFiles") and HASH.match(t.get("hash", ""))]
    qbit.pause(running)
    _write(sorted(set(paused_by_us()) | set(running)))
    ctl.notifications().record("remote", "Pause générale des torrents", f"{len(running)} torrent(s)", "info", "remote")
    say(f"pause générale : {len(running)} torrent(s)")
    return len(running)


def resume(qbit=None):
    """Reprend les torrents mis en pause par la télécommande ; renvoie leur nombre."""
    mine = paused_by_us()
    if not mine:
        return 0
    if ctl.vacation().active():
        raise ctl.SeedboxError("le mode vacances est actif : reprenez-le depuis « Vacances »")
    qbit = qbit or ctl.vacation().Qbit()
    known = {t["hash"] for t in qbit.torrents()}
    again = [h for h in mine if h in known]
    qbit.resume(again)
    _write([])
    ctl.notifications().record("remote", "Reprise des torrents", f"{len(again)} torrent(s)", "ok", "remote")
    say(f"reprise : {len(again)} torrent(s)")
    return len(again)


def overview(qbit=None):
    active = ctl.alerts().overview()["active"]
    result = {"alerts": [{"title": a["title"], "level": a["level"]} for a in active][:8], "alerts_total": len(active),
              "vacation": ctl.vacation().overview(), "downloads": {"available": False, "active": 0, "paused": 0, "down": 0, "up": 0},
              "paused_by_remote": len(paused_by_us())}
    if ctl.qbit_mode() or qbit is not None:
        try:
            qbit = qbit or ctl.vacation().Qbit()
            torrents = qbit.torrents()
            result["downloads"] = {"available": True, "active": sum(1 for t in torrents if t.get("state") in DOWNLOADING),
                                   "paused": sum(1 for t in torrents if t.get("state") in PAUSED),
                                   "down": sum(int(t.get("dlspeed", 0)) for t in torrents), "up": sum(int(t.get("upspeed", 0)) for t in torrents)}
        except (OSError, ctl.SeedboxError):
            pass
    return result
