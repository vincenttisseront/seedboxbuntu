"""SeedBoxBuntu - diagnostic du partage : « pourquoi ça n'envoie pas ? » et réglages de qBittorrent pour le seed.

Le diagnostic est MÉCANIQUE : il lit les réglages et l'état de qBittorrent (connectivité, port entrant, limites de débit, files d'attente,
limites de partage, chiffrement, mode anonyme, liaison au tunnel VPN) et l'état des torrents (sans demande, bloqués, limités), puis
applique des règles qui nomment une cause et un remède. Rien n'est deviné : chaque constat cite la valeur lue.
L'IA externe (facultative) ne fait qu'EXPLIQUER ces constats dans l'ordre utile ; elle ne les invente pas.
Les remèdes simples (réglages qBittorrent) s'appliquent en un clic, par identifiant choisi parmi ceux que CE module vient de proposer :
la console ne transmet jamais un réglage libre à qBittorrent.
    seedboxctl seeddiag show | apply IDENTIFIANT
"""
import json
import re
import time

import seedboxctl as ctl

LAST_FILE = ctl.STATE / "seeddiag-last.json"
SEVERITY = {"critical": 0, "warning": 1, "info": 2}
LOW_UPLOAD = 1 << 20            # une limite globale d'envoi sous 1 Mo/s est un frein
SEED_STATES = {"uploading", "stalledUP", "queuedUP", "forcedUP", "checkingUP", "pausedUP", "stoppedUP"}


def _size(octets):
    value = float(octets)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024 or unit == "To":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1024


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# --- collecte ---------------------------------------------------------------------------------------
def _get(path, cookie):
    return ctl.request("GET", ctl.svc(ctl.qbit_host(), 8080) + "/api/v2" + path, headers={"Cookie": cookie}, timeout=30).json()


def collect():
    """Faits lus dans qBittorrent et le système. Renvoie None quand qBittorrent est arrêté."""
    if not ctl.container_running("qbittorrent"):
        return None
    cookie = ctl.qbit_login()
    facts = {"prefs": _get("/app/preferences", cookie) or {}, "transfer": _get("/transfer/info", cookie) or {},
             "torrents": _get("/torrents/info", cookie) or [], "mode": ctl.qbit_mode(), "bound": ctl.qbit_interface_bound()}
    try:
        facts["version"] = ctl.request("GET", ctl.svc(ctl.qbit_host(), 8080) + "/api/v2/app/version", headers={"Cookie": cookie}, timeout=15).text.strip()
    except (OSError, ctl.SeedboxError, ValueError):
        facts["version"] = ""
    env = ctl.env_read()
    facts["vpn"] = {"provider": env.get("VPN_SERVICE_PROVIDER", ""), "forwarding": env.get("VPN_PORT_FORWARDING") == "on"}
    if facts["mode"] == "vpn" and ctl.container_running("gluetun"):
        result = ctl.run("docker", "exec", "gluetun", "cat", "/tmp/gluetun/forwarded_port", check=False, capture=True)
        text = (result.stdout or "").strip()
        facts["vpn"]["forwarded_port"] = int(text) if text.isdigit() else None
    return facts


# --- règles -----------------------------------------------------------------------------------------
def _finding(ident, severity, title, cause, fix, apply=None):
    item = {"id": ident, "severity": severity, "title": title, "cause": cause, "fix": fix}
    if apply:
        item["apply"] = apply                       # {"label": "...", "prefs": {...}} : réglages qBittorrent à écrire
    return item


def diagnose(facts):
    """Liste de constats, du plus grave au plus léger, d'après les faits collectés."""
    prefs, transfer, torrents = facts["prefs"], facts["transfer"], facts["torrents"]
    seeding = [t for t in torrents if t.get("state") in SEED_STATES or t.get("progress", 0) >= 1]
    out = []
    mode, vpn = facts.get("mode"), facts.get("vpn", {})

    # 1. Rien ne sort : liaison d'interface incohérente avec le mode
    if facts.get("bound") is not None and mode and facts["bound"] != (mode == "vpn"):
        out.append(_finding("interface", "critical", "qBittorrent est lié à une interface qui n'existe pas",
                            "Le fichier de qBittorrent impose une interface réseau (tun0) incompatible avec le mode « " + mode + " » : aucune connexion ne peut s'établir.",
                            "Changez de mode puis revenez, ou lancez « seedboxctl vpn heal » ; la console répare cette liaison à chaque mise à jour."))

    # 2. Port entrant
    status = transfer.get("connection_status")
    port = prefs.get("listen_port")
    forwarded = vpn.get("forwarded_port")
    if mode == "vpn" and forwarded and port != forwarded:
        out.append(_finding("port-forward", "critical", "Le port d'écoute n'est pas le port ouvert par le VPN",
                            f"qBittorrent écoute sur le port {port}, mais le VPN a ouvert le {forwarded} : personne ne peut se connecter à vous, les envois restent à zéro.",
                            f"Mettre le port d'écoute sur {forwarded}.", {"label": f"Utiliser le port {forwarded}", "prefs": {"listen_port": forwarded}}))
    elif status == "firewalled":
        if mode == "vpn" and not vpn.get("forwarded_port"):
            out.append(_finding("no-forwarding", "critical", "Aucun port entrant : connexions entrantes impossibles",
                                "qBittorrent est « derrière un pare-feu » : le VPN n'ouvre pas de port vers vous (la redirection de port n'est pas active ou le fournisseur ne la propose pas). "
                                "Sans connexion entrante, seuls les pairs qui acceptent d'être contactés par vous reçoivent des données : l'envoi chute, surtout sur les trackers privés.",
                                "Activez la redirection de port dans le VPN (fournisseur compatible : ProtonVPN, Private Internet Access) ou ouvrez et redirigez le port sur la box en accès direct."))
        else:
            out.append(_finding("firewalled", "critical", "Le port d'écoute n'est pas joignable depuis Internet",
                                f"qBittorrent signale « pare-feu » (port {port}) : les pairs ne peuvent pas vous contacter.",
                                "Redirigez ce port (TCP et UDP) sur la box vers le serveur, vérifiez le pare-feu et l'adresse du serveur."))
    elif status == "disconnected":
        out.append(_finding("disconnected", "critical", "qBittorrent est déconnecté du réseau",
                            "Aucun pair, aucun DHT : qBittorrent ne voit pas le réseau (VPN tombé, interface liée absente ou pas de connexion Internet).",
                            "Vérifiez l'état du VPN dans la console, puis la liaison d'interface."))

    # 3. Limites de débit
    up_limit = transfer.get("up_rate_limit", 0) or prefs.get("up_limit", 0) or 0
    if transfer.get("use_alt_speed_limits"):
        alt = prefs.get("alt_up_limit", 0)
        out.append(_finding("alt-speed", "warning", "Les limites alternatives de débit sont actives",
                            f"Le mode « tortue » plafonne l'envoi à {_size(alt)}/s." if alt else "Le mode « tortue » (limites alternatives) est actif.",
                            "Désactivez-le, ou retirez le planning s'il le réactive à chaque fois.", {"label": "Désactiver le mode tortue", "toggle_alt": True}))
    elif up_limit and up_limit < LOW_UPLOAD:
        out.append(_finding("up-limit", "warning", "Limite globale d'envoi très basse",
                            f"L'envoi est plafonné à {_size(up_limit)}/s pour tous les torrents.",
                            "Retirez la limite (0 = illimité) ou fixez-la à ce que votre ligne tient réellement (environ 80 % du débit montant).",
                            {"label": "Retirer la limite d'envoi", "prefs": {"up_limit": 0}}))
    limited = [t for t in torrents if t.get("up_limit", -1) not in (-1, 0)]
    if limited:
        out.append(_finding("torrent-limits", "warning", f"{len(limited)} torrent(s) ont une limite d'envoi individuelle",
                            "Une limite par torrent s'ajoute à la limite globale et reste invisible dans les réglages généraux.",
                            "Retirez ces limites dans qBittorrent (clic droit sur le torrent, Limiter le débit d'envoi, Illimité)."))

    # 4. File d'attente : des torrents en attente d'une place pour envoyer
    queued = [t for t in torrents if t.get("state") == "queuedUP"]
    if prefs.get("queueing_enabled") and queued:
        out.append(_finding("queue", "warning", f"{len(queued)} torrent(s) attendent une place pour envoyer",
                            f"La file d'attente est active : {prefs.get('max_active_uploads')} envois et {prefs.get('max_active_torrents')} torrents actifs au plus ; les autres n'envoient rien.",
                            "Sur une seedbox, désactivez la file d'attente ou relevez ces limites.",
                            {"label": "Désactiver la file d'attente", "prefs": {"queueing_enabled": False}}))

    # 5. Limites de partage : les torrents s'arrêtent seuls
    stopped = [t for t in torrents if t.get("state") in ("pausedUP", "stoppedUP")]
    limits = []
    if prefs.get("max_ratio_enabled"):
        limits.append(f"ratio {prefs.get('max_ratio')}")
    if prefs.get("max_seeding_time_enabled"):
        limits.append(f"{prefs.get('max_seeding_time')} min de partage")
    if prefs.get("max_inactive_seeding_time_enabled"):
        limits.append(f"{prefs.get('max_inactive_seeding_time')} min d'inactivité")
    if limits and (stopped or prefs.get("max_ratio_act") in (1, 3)):
        action = {0: "mis en pause", 1: "supprimé", 2: "supprimé avec ses fichiers", 3: "mis en partage « super seeding »"}.get(prefs.get("max_ratio_act"), "arrêté")
        out.append(_finding("share-limits", "critical" if stopped else "warning", "Les torrents s'arrêtent seuls une fois la limite de partage atteinte",
                            f"Limite globale : {' / '.join(limits)}, puis le torrent est {action}. {len(stopped)} torrent(s) sont déjà à l'arrêt.",
                            "Retirez ces limites : la durée et le ratio exigés par chaque tracker sont gérés dans « Optimisation des seeds », pas ici.",
                            {"label": "Retirer les limites de partage",
                             "prefs": {"max_ratio_enabled": False, "max_seeding_time_enabled": False, "max_inactive_seeding_time_enabled": False}}))
    elif stopped:
        out.append(_finding("stopped", "warning", f"{len(stopped)} torrent(s) sont à l'arrêt",
                            "Ils n'envoient rien tant qu'ils ne sont pas relancés (arrêt manuel, limite d'un tracker ou règle d'un outil externe comme qbit-manage).",
                            "Relancez-les dans qBittorrent, et vérifiez qu'aucun outil ne les arrête (qbit-manage, autobrr)."))

    # 6. Connectivité des pairs
    if prefs.get("anonymous_mode"):
        out.append(_finding("anonymous", "warning", "Le mode anonyme est activé",
                            "qBittorrent ne dévoile pas son port aux trackers : les connexions entrantes sont réduites et certains trackers privés refusent ce mode.",
                            "Désactivez le mode anonyme (le VPN protège déjà votre adresse).", {"label": "Désactiver le mode anonyme", "prefs": {"anonymous_mode": False}}))
    if prefs.get("encryption") == 1:
        out.append(_finding("encryption", "warning", "Le chiffrement est obligatoire",
                            "Les pairs qui ne chiffrent pas sont refusés : le nombre de pairs possibles baisse nettement.",
                            "Choisissez « Autoriser le chiffrement » (valeur par défaut).", {"label": "Autoriser le chiffrement", "prefs": {"encryption": 0}}))
    if 0 < (prefs.get("max_uploads_per_torrent") or 0) < 3:
        out.append(_finding("slots", "info", "Très peu d'emplacements d'envoi par torrent",
                            f"Seulement {prefs.get('max_uploads_per_torrent')} pair(s) peuvent recevoir en même temps pour chaque torrent.",
                            "Relevez-le à 4 ou plus, ou laissez illimité.", {"label": "Laisser illimité", "prefs": {"max_uploads_per_torrent": -1}}))
    if prefs.get("dht") is False and any(not t.get("private") for t in seeding):
        out.append(_finding("dht", "info", "Le DHT est désactivé", "Les torrents publics y perdent des pairs ; les trackers privés n'en dépendent pas.",
                            "Réactivez-le si vous partagez des torrents publics.", {"label": "Activer le DHT", "prefs": {"dht": True}}))

    # 7. Ce qui reste est normal : pas de demande
    waiting = [t for t in seeding if t.get("state") == "stalledUP"]
    no_demand = [t for t in waiting if (t.get("num_incomplete") or 0) == 0]
    wanted = [t for t in waiting if (t.get("num_leechs") or 0) > 0 or (t.get("num_incomplete") or 0) > 0]
    if wanted and (status != "connected" or len(wanted) >= max(3, len(waiting) // 3)):
        out.append(_finding("wanted-silent", "warning", f"{len(wanted)} torrent(s) ont des demandeurs mais n'envoient rien",
                            "Des pairs veulent ces fichiers (le tracker le dit) sans que rien ne parte : connexion entrante impossible, limite ou fichiers lents à lire.",
                            "Traitez d'abord les constats de connectivité ci-dessus ; si rien n'y change, vérifiez la lecture du disque (partage réseau lent)."))
    if seeding and len(no_demand) >= max(1, int(len(seeding) * 0.6)):
        out.append(_finding("no-demand", "info", f"{len(no_demand)} torrent(s) sur {len(seeding)} n'ont personne à qui envoyer",
                            "Aucun téléchargeur sur ces torrents : c'est la cause la plus fréquente d'un envoi nul, et elle ne se règle pas par un réglage. "
                            "Les torrents récents ou rares sur des trackers actifs envoient le plus ; les vieux fichiers très répandus n'envoient presque rien.",
                            "Gardez le partage sur les trackers privés qui l'exigent, ajoutez des torrents récents (autobrr, filtres de nouveautés) et retirez le reste dans « Optimisation des seeds »."))

    if not out:
        out.append(_finding("ok", "info", "Aucun défaut de réglage trouvé", f"Connexion « {status or 'inconnue'} », port {port}, aucune limite bloquante.",
                            "Si l'envoi reste faible, la cause est la demande des pairs (voir le tableau par tracker)."))
    return sorted(out, key=lambda f: SEVERITY[f["severity"]])


def summary(facts):
    """Chiffres clés du diagnostic (sans noms de torrents ni de trackers)."""
    torrents, transfer, prefs = facts["torrents"], facts["transfer"], facts["prefs"]
    states = {}
    for t in torrents:
        states[t.get("state", "?")] = states.get(t.get("state", "?"), 0) + 1
    return {"qbittorrent": facts.get("version", ""), "mode": facts.get("mode"), "connection": transfer.get("connection_status"),
            "dht_nodes": transfer.get("dht_nodes"), "port": prefs.get("listen_port"), "up_speed": transfer.get("up_info_speed", 0),
            "up_limit": transfer.get("up_rate_limit", 0), "torrents": len(torrents), "states": states,
            "forwarded_port": facts.get("vpn", {}).get("forwarded_port"), "vpn": facts.get("vpn", {}).get("provider", "")}


def overview(facts=None):
    """Diagnostic complet. `facts` : relevé injecté (tests)."""
    facts = facts if facts is not None else collect()
    if facts is None:
        return {"running": False, "findings": [], "summary": {}, "at": int(time.time())}
    result = {"running": True, "findings": diagnose(facts), "summary": summary(facts), "at": int(time.time())}
    try:
        LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_FILE.write_text(json.dumps(result, ensure_ascii=False))
    except OSError:
        pass
    return result


# --- remèdes ----------------------------------------------------------------------------------------
PREF_KEYS = {"listen_port", "up_limit", "queueing_enabled", "max_ratio_enabled", "max_seeding_time_enabled", "max_inactive_seeding_time_enabled",
             "anonymous_mode", "encryption", "max_uploads_per_torrent", "dht"}


def apply(ident, facts=None, post=None, by="console"):
    """Applique le remède du constat `ident`, à condition que ce constat existe dans le diagnostic du moment."""
    facts = facts if facts is not None else collect()
    if facts is None:
        raise ctl.SeedboxError("qBittorrent est arrêté")
    finding = next((f for f in diagnose(facts) if f["id"] == str(ident)), None)
    if not finding or "apply" not in finding:
        raise ctl.SeedboxError("ce remède n'est plus proposé : actualisez le diagnostic")
    remedy = finding["apply"]
    if post is None:
        cookie = ctl.qbit_login()
        base = ctl.svc(ctl.qbit_host(), 8080) + "/api/v2"

        def post(path, form):
            result = ctl.request("POST", base + path, headers={"Cookie": cookie}, form=form, timeout=30)
            if not result.ok:
                raise ctl.SeedboxError(f"qBittorrent : HTTP {result.status}")
    if remedy.get("toggle_alt"):
        post("/transfer/toggleSpeedLimitsMode", {})
    else:
        prefs = remedy["prefs"]
        if not set(prefs) <= PREF_KEYS:
            raise ctl.SeedboxError("réglage non autorisé")
        post("/app/setPreferences", {"json": json.dumps(prefs)})
    say(f"diagnostic du partage : {remedy['label']}")
    try:
        ctl.audit().record(by, "", "Réglage de qBittorrent", remedy["label"], "console", "ok")
    except Exception:  # noqa: BLE001
        pass
    return {"applied": remedy["label"]}


def last():
    try:
        return json.loads(LAST_FILE.read_text())
    except (OSError, ValueError):
        return None
