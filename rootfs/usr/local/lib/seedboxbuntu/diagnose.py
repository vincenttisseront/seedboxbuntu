"""SeedBoxBuntu - assistant de dépannage : de l'alerte à la cause probable, étape par étape.

Pour un service en difficulté, l'assistant déroule les vérifications qu'un administrateur ferait à la main :
état du conteneur et code de sortie, nombre de redémarrages (boucle ?), contrôle d'état de l'image, manque de mémoire,
espace disque, services dont il dépend, et les dernières lignes du journal (secrets masqués) où il cherche les causes
courantes (permission refusée, disque plein, port déjà pris, base verrouillée, mémoire, connexion refusée).
Il termine par des actions PROPOSÉES (redémarrer, revenir à la version précédente, libérer de l'espace) : l'assistant ne
modifie jamais rien lui-même, chaque action reste un clic confirmé de l'administrateur.
Limite assumée : il reconnaît des causes courantes ; une panne propre à une application reste à lire dans son journal.
    seedboxctl diagnose list | run SERVICE
"""
import calendar
import os
import re
import time

import seedboxctl as ctl

LEVELS = ("ok", "info", "warn", "fail")
DISK_WARN, DISK_FAIL = 90, 97
LOOP = 5
CAUSES = (
    (re.compile(r"(?i)permission denied|EACCES"), "Droits insuffisants sur un fichier ou un dossier : vérifiez le propriétaire des dossiers de configuration et des médias."),
    (re.compile(r"(?i)no space left|disk full|ENOSPC"), "Plus de place sur un disque : libérez de l'espace (Système > Nettoyage)."),
    (re.compile(r"(?i)address already in use|bind: .*in use|EADDRINUSE"), "Un port est déjà utilisé par un autre programme."),
    (re.compile(r"(?i)database is locked|database disk image is malformed|SQLITE_CORRUPT"), "Base de données verrouillée ou endommagée : une restauration de sauvegarde peut être nécessaire."),
    (re.compile(r"(?i)out of memory|cannot allocate memory|oom"), "Manque de mémoire vive : réduisez la charge ou ajoutez de la mémoire."),
    (re.compile(r"(?i)connection refused|no route to host|name resolution|could not resolve|dial tcp"), "Un service ou une adresse nécessaire ne répond pas : voir les dépendances ci-dessus et le réseau."),
)


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def step(key, title, level, detail):
    return {"key": key, "title": title, "level": level, "detail": detail}


def parse_depends(label):
    """« gluetun:service_healthy:false,traefik:service_started:true » -> ['gluetun', 'traefik']."""
    return [part.split(":")[0] for part in str(label or "").split(",") if part.split(":")[0]]


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} j"


def analyse(facts, now=None):
    """Étapes et actions proposées à partir des faits recueillis (fonction pure : testable sans Docker)."""
    now = time.time() if now is None else now
    steps, actions = [], []
    state = facts.get("status", "absent")
    since = facts.get("started_at", 0)
    if state == "absent":
        steps.append(step("state", "État du conteneur", "fail", "Le service ne figure pas parmi les conteneurs de l'appliance."))
    elif state == "running":
        steps.append(step("state", "État du conteneur", "ok", f"En marche depuis {ago(now - since)}." if since else "En marche."))
    elif state == "restarting":
        steps.append(step("state", "État du conteneur", "fail", "Redémarre en boucle : il s'arrête dès qu'il démarre."))
    else:
        code = facts.get("exit_code")
        steps.append(step("state", "État du conteneur", "fail", f"Arrêté ({state}"
                          + (f", code de sortie {code}" if code not in (None, 0) else "") + ")."))
    restarts = int(facts.get("restarts", 0) or 0)
    if restarts >= LOOP:
        steps.append(step("restarts", "Redémarrages", "fail", f"{restarts} redémarrages automatiques : boucle de redémarrage."))
    elif restarts:
        recent = state == "running" and since and now - since < 600
        steps.append(step("restarts", "Redémarrages", "warn", f"{restarts} redémarrage(s)" + (" et démarré il y a moins de 10 minutes : encore instable ?" if recent else ".")))
    else:
        steps.append(step("restarts", "Redémarrages", "ok", "Aucun redémarrage automatique."))
    health = facts.get("health", "")
    if health == "unhealthy":
        out = (facts.get("health_output") or "").strip().splitlines()
        steps.append(step("health", "Contrôle d'état", "fail", "L'application répond « unhealthy »" + (f" : {out[-1][:160]}" if out else ".")))
    elif health == "starting":
        steps.append(step("health", "Contrôle d'état", "info", "Démarrage en cours : le contrôle n'a pas encore répondu."))
    elif health == "healthy":
        steps.append(step("health", "Contrôle d'état", "ok", "L'application répond au contrôle d'état."))
    else:
        steps.append(step("health", "Contrôle d'état", "info", "Cette image n'a pas de contrôle d'état : seul le fait d'être « en marche » est connu."))
    if facts.get("oom"):
        steps.append(step("memory", "Mémoire", "fail", "Le système a arrêté le conteneur faute de mémoire (OOM)."))
    elif facts.get("mem_free_percent") is not None and facts["mem_free_percent"] < 5:
        steps.append(step("memory", "Mémoire", "warn", f"Mémoire de la machine presque épuisée ({facts['mem_free_percent']} % libres)."))
    else:
        steps.append(step("memory", "Mémoire", "ok", "Pas de manque de mémoire constaté."))
    full = [(label, pct) for label, pct in facts.get("disks", []) if pct >= DISK_WARN]
    if full:
        worst = max(p for _, p in full)
        steps.append(step("disk", "Espace disque", "fail" if worst >= DISK_FAIL else "warn",
                          "Presque plein : " + ", ".join(f"{label} {pct} %" for label, pct in full) + "."))
    else:
        steps.append(step("disk", "Espace disque", "ok", "Assez d'espace libre sur les disques."))
    broken = [(name, st) for name, st in facts.get("dependencies", {}).items() if st not in ("running", "healthy")]
    if facts.get("dependencies"):
        if broken:
            steps.append(step("deps", "Services dont il dépend", "fail", "; ".join(f"{name} : {st}" for name, st in broken) + " - corrigez-les d'abord."))
        else:
            steps.append(step("deps", "Services dont il dépend", "ok", "Tous répondent : " + ", ".join(sorted(facts["dependencies"])) + "."))
    lines = [l for l in str(facts.get("logs", "")).splitlines() if l.strip()]
    causes = []
    for pattern, hint in CAUSES:
        if any(pattern.search(l) for l in lines) and hint not in causes:
            causes.append(hint)
    if lines:
        steps.append(step("logs", "Journal du service", "warn" if causes else "info",
                          " ".join(causes) if causes else "Aucune cause courante reconnue dans les dernières lignes : lisez-les ci-dessous."))
    else:
        steps.append(step("logs", "Journal du service", "info", "Aucune ligne de journal disponible."))
    failing = [s for s in steps if s["level"] in ("fail", "warn")]
    bad = state != "running" or health == "unhealthy" or restarts >= LOOP
    if bad or failing:
        actions.append({"id": "restart", "label": "Redémarrer", "why": "Règle les blocages passagers ; sans effet si la cause persiste."})
    if bad and facts.get("previous", 0):
        actions.append({"id": "rollback", "label": "Revenir à la version précédente",
                        "why": f"{facts['previous']} version(s) précédente(s) conservée(s) ; le service sera épinglé jusqu'à une nouvelle mise à jour."})
    if any(s["key"] == "disk" and s["level"] in ("warn", "fail") for s in steps):
        actions.append({"id": "cleanup", "label": "Analyser l'espace récupérable", "why": "Ouvre l'assistant de nettoyage : rien n'est supprimé sans votre accord."})
    level = "ok"
    for s in steps:
        if LEVELS.index(s["level"]) > LEVELS.index(level) and s["level"] != "info":
            level = s["level"]
    summary = next((s["detail"] for s in steps if s["level"] == "fail"), None) or next((s["detail"] for s in steps if s["level"] == "warn"), None) \
        or "Aucun problème constaté : le service est en marche et répond."
    return {"level": level, "summary": summary, "steps": steps, "actions": actions, "log": lines[-12:]}


# --- recueil des faits ---------------------------------------------------------------------------
def services():
    versions = ctl.versions()
    return sorted(versions.service_containers())


def gather(service):
    versions = ctl.versions()
    if not versions.SERVICE.match(service):
        raise ctl.SeedboxError("nom de service invalide")
    info = versions.service_containers().get(service)
    if not info:
        return {"status": "absent"}
    data = (versions.docker_json("inspect", info["container"]) or [{}])[0]
    state = data.get("State") or {}
    health = state.get("Health") or {}
    try:
        started = calendar.timegm(time.strptime((state.get("StartedAt") or "")[:19], "%Y-%m-%dT%H:%M:%S"))   # Docker donne l'heure UTC
    except (ValueError, OverflowError):
        started = 0
    facts = {"status": "restarting" if state.get("Restarting") else state.get("Status", "absent"), "exit_code": state.get("ExitCode"),
             "oom": bool(state.get("OOMKilled")), "restarts": data.get("RestartCount", 0), "started_at": started,
             "health": health.get("Status", ""), "health_output": ((health.get("Log") or [{}])[-1] or {}).get("Output", "")}
    states = ctl.containers()
    deps = {}
    for name in parse_depends(info["labels"].get("com.docker.compose.depends_on", "")):
        current = states.get(name, {})
        deps[name] = current.get("health") if current.get("health") in ("healthy", "unhealthy", "starting") else current.get("state", "absent")
    facts["dependencies"] = deps
    memory = ctl.sysinfo().meminfo()
    facts["mem_free_percent"] = round(100 * (memory["total"] - memory["used"]) / memory["total"]) if memory.get("total") else None
    disks = []
    try:
        for label, path in ctl.alerts().disk_paths():
            usage = os.statvfs(path)
            total = usage.f_blocks * usage.f_frsize
            disks.append((label, round(100 * (total - usage.f_bavail * usage.f_frsize) / total) if total else 0))
    except Exception:  # noqa: BLE001
        pass
    facts["disks"] = disks
    code, out, err = ctl.sysinfo().run("docker", "logs", "--tail", "40", info["container"], timeout=30)
    facts["logs"] = ctl.audit().redact(out + err)[-6000:]
    facts["previous"] = 0 if service in versions.LOCAL_SERVICES else len(
        [s for s in versions.snapshots(service) if s["id"] != info["image_id"].replace("sha256:", "")[:12]])
    return facts


def run(service, now=None):
    result = analyse(gather(service), now)
    result["service"] = service
    say(f"{service} : {result['level']} - {result['summary'][:100]}")
    return result


def overview():
    return {"services": services()}
