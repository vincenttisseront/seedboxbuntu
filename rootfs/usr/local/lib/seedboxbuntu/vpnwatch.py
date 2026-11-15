"""SeedBoxBuntu - surveillance du VPN : le tunnel porte-t-il vraiment le trafic, et sous quelle adresse sort-on ?

Quand qBittorrent passe par le VPN (mode « vpn »), toutes les 5 minutes :
  - l'adresse de sortie vue depuis le tunnel (via gluetun) est comparée à l'adresse de la maison, vue depuis le serveur.
    Si elles sont identiques, le trafic sort SANS passer par le VPN : alerte critique, et, si « arrêt automatique » est
    activé, tous les torrents sont mis en pause (ils reprennent seuls quand le tunnel est bon à nouveau) ;
  - si le tunnel est démarré mais ne donne aucune adresse de sortie plusieurs fois de suite : alerte « sans trafic » ;
  - un changement d'adresse de sortie (reconnexion vers un autre serveur) est noté dans les notifications : certains trackers
    privés n'aiment pas les changements d'adresse fréquents, l'historique permet de le constater.
Limites assumées : gluetun coupe déjà tout trafic hors tunnel (pare-feu), et qBittorrent partage son réseau : une vraie fuite
est donc improbable ; cette surveillance est un filet de sécurité, pas la protection principale. Elle interroge un service
public (api.ipify.org) depuis le serveur, ce qui lui révèle l'adresse de la maison : c'est pourquoi elle est désactivée par
défaut. Un tunnel arrêté est déjà signalé par la surveillance des conteneurs ; ici, seul le trafic est vérifié. Si le tunnel
est tombé, qBittorrent est lui-même injoignable : la pause automatique ne s'applique donc qu'au cas d'une fuite.
    seedboxctl vpnwatch show | set --enable|--disable [--auto-stop|--no-auto-stop] | tick
"""
import json
import os
import time
import urllib.request

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "vpnwatch.json"
STATE_FILE = ctl.STATE / "vpnwatch.json"
DEFAULTS = {"enabled": False, "auto_stop": False}
NO_TRAFFIC_AFTER = 3           # passages consécutifs sans adresse de sortie
HOME_IP_TTL = 3600
HISTORY = 20


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: bool(v) for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        cfg[key] = bool(value)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    say(f"surveillance du VPN : {'activée' if cfg['enabled'] else 'désactivée'}, arrêt automatique {'oui' if cfg['auto_stop'] else 'non'}")
    return cfg


def state():
    try:
        value = json.loads(STATE_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _store(value):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    temp.write_text(json.dumps(value))
    os.replace(temp, STATE_FILE)


def home_ip():
    """Adresse de la maison, vue depuis le serveur (hors tunnel)."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=8) as response:  # noqa: S310 - adresse fixe
            text = response.read(64).decode().strip()
        return text if text.replace(".", "").replace(":", "").isalnum() else None
    except (OSError, ValueError):
        return None


def collect(cache):
    """Faits du moment : {running, healthy, exit, home}. Séparé pour que les tests le remplacent."""
    states = ctl.containers().get("gluetun", {})
    running = states.get("state") == "running"
    result = {"running": running, "healthy": states.get("health") in ("", "healthy"), "exit": ctl.public_ip() if running else None, "home": cache.get("home")}
    if result["exit"] and (not cache.get("home") or time.time() - cache.get("home_at", 0) > HOME_IP_TTL):
        fetched = home_ip()
        if fetched:
            result["home"], result["home_new"] = fetched, True
    return result


def evaluate(facts, previous):
    """Problèmes et événements d'après les faits et l'état précédent. Fonction pure."""
    problems, events = {}, []
    exit_ip, home = facts.get("exit"), facts.get("home")
    misses = 0
    if facts.get("running") and not exit_ip:
        misses = previous.get("misses", 0) + 1
        if misses >= NO_TRAFFIC_AFTER:
            problems["vpn-sans-trafic"] = ("Le VPN ne donne pas d'adresse de sortie", "Le tunnel est démarré mais aucun trafic n'en sort (plusieurs contrôles de suite). "
                                           "Voir VPN > Journal, ou relancez gluetun.", "warning")
    if exit_ip and home and exit_ip == home:
        problems["vpn-fuite"] = ("Le trafic ne passe pas par le VPN", f"L'adresse de sortie vue depuis le tunnel ({exit_ip}) est celle de la maison : "
                                 "les téléchargements ne sont pas protégés.", "critical")
    if exit_ip and previous.get("exit") and exit_ip != previous["exit"]:
        events.append(("Adresse de sortie du VPN changée", f"{previous['exit']} → {exit_ip}"))
    return problems, events, misses


def tick(now=None, facts=None, qbit_ops=None):
    cfg = settings()
    if not cfg["enabled"] or ctl.qbit_mode() != "vpn":
        return False
    now = time.time() if now is None else now
    current = state()
    facts = facts if facts is not None else collect(current)
    problems, events, misses = evaluate(facts, current)
    history = list(current.get("history", []))
    for title, detail in events:
        history.append({"at": int(now), "title": title, "detail": detail})
        try:
            ctl.notifications().record("vpn", title, detail, "info", "vpn")
        except Exception:  # noqa: BLE001
            pass
    stopped = bool(current.get("stopped"))
    if "vpn-fuite" in problems and cfg["auto_stop"] and not stopped:
        try:
            count = (qbit_ops or ctl.telecommande()).pause()
            stopped = True
            history.append({"at": int(now), "title": "Torrents mis en pause (fuite)", "detail": f"{count} torrent(s)"})
            say(f"fuite constatée : {count} torrent(s) mis en pause")
        except (ctl.SeedboxError, OSError) as error:
            say(f"pause automatique impossible : {str(error)[:100]}")
    elif stopped and "vpn-fuite" not in problems and facts.get("exit"):
        try:
            (qbit_ops or ctl.telecommande()).resume()
            stopped = False
            history.append({"at": int(now), "title": "Torrents repris", "detail": "le tunnel est de nouveau bon"})
            say("tunnel de nouveau bon : reprise des torrents")
        except (ctl.SeedboxError, OSError) as error:
            say(f"reprise impossible : {str(error)[:100]}")
    _store({"checked": int(now), "exit": facts.get("exit") or current.get("exit", ""), "home": facts.get("home") or current.get("home", ""),
            "home_at": int(now) if facts.get("home_new") else current.get("home_at", 0), "misses": misses, "stopped": stopped,
            "problems": {k: list(v) for k, v in problems.items()}, "history": history[-HISTORY:]})
    return True


def findings():
    """Pour la surveillance : les problèmes du dernier passage (rien si la surveillance est désactivée)."""
    if not settings()["enabled"]:
        return {}
    return {key: tuple(value) for key, value in state().get("problems", {}).items()}


def overview():
    current = state()
    cfg = settings()
    return {"settings": cfg, "mode": ctl.qbit_mode(), "checked": current.get("checked", 0), "exit": current.get("exit", ""),
            "leak": "vpn-fuite" in current.get("problems", {}), "stopped": bool(current.get("stopped")),
            "history": list(reversed(current.get("history", [])))}
