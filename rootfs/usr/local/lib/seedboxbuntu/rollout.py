"""SeedBoxBuntu - mise à jour des applications par lots, une à la fois, avec test de santé et retour arrière.

« seedboxctl rollout run » met à jour les services un par un (versions.update : téléchargement, recréation, attente de
l'état « en marche » et « healthy », retour automatique à l'image précédente si le service ne repart pas). Entre deux
services, la méthode ajoute :
  - un second contrôle quelques secondes après le démarrage : un service qui plante en boucle juste après son lancement
    passe le premier test ; il est alors ramené à la version précédente ;
  - l'arrêt du lot au premier échec (par défaut) : mieux vaut un service à examiner que dix services dans l'inconnu ;
    les suivants sont marqués « ignoré » et rien d'autre n'est touché.
Hors lot, à mettre à jour à la main : Traefik et Authelia (une coupure efface la session de la console pendant le test)
et gluetun (le tunnel de qBittorrent : sa recréation exige celle de qBittorrent). Les services épinglés après un retour
arrière ne sont pas touchés non plus.
Limite assumée : le test vaut ce que vaut le contrôle d'état de l'image. Sans « healthcheck » le service est seulement
vu « en marche » ; le plan l'indique par service.
    seedboxctl rollout plan | run [--only SERVICE ...] [--continue]
"""
import json
import time

import seedboxctl as ctl

REPORT_FILE = ctl.STATE / "rollout.json"
APART = ("traefik", "authelia", "gluetun")
SETTLE = 20


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def plan():
    """Services du lot et services à part, avec la qualité du test de santé de chacun."""
    versions = ctl.versions()
    states = ctl.containers()
    pins = versions.pins_read()
    batch, apart, skipped = [], [], []
    for name in versions.unpinned_services():
        state = states.get(name, {})
        row = {"service": name, "running": state.get("state") == "running", "healthcheck": bool(state.get("health"))}
        (apart if name in APART else batch).append(row)
    for name in sorted(pins):
        skipped.append({"service": name, "reason": "épinglé après un retour arrière"})
    batch.sort(key=lambda r: r["service"])
    return {"batch": batch, "apart": apart, "skipped": skipped}


def last_report():
    try:
        value = json.loads(REPORT_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def run(only=None, stop_on_failure=True, wait=time.sleep, settle=SETTLE):
    versions = ctl.versions()
    candidates = [r for r in plan()["batch"]]
    if only:
        wanted = set(only)
        unknown = wanted - {r["service"] for r in candidates}
        if unknown:
            raise ctl.SeedboxError("hors lot ou inconnu : " + ", ".join(sorted(unknown)))
        candidates = [r for r in candidates if r["service"] in wanted]
    if not candidates:
        raise ctl.SeedboxError("aucun service à mettre à jour dans le lot")
    report = {"started": int(time.time()), "items": [], "stop_on_failure": stop_on_failure}
    stopped = False
    for row in candidates:
        name = row["service"]
        item = {"service": name, "result": "ignoré", "detail": "", "seconds": 0}
        report["items"].append(item)
        if stopped:
            item["detail"] = "lot arrêté après un échec"
            continue
        began = time.time()
        try:
            say(f"[{len(report['items'])}/{len(candidates)}] {name}")
            outcome = versions.update(name)
            item["result"] = outcome
            if outcome == "mis à jour":
                wait(settle)
                state = ctl.containers().get(name, {})
                if state.get("state") != "running" or state.get("health") not in ("", "healthy"):
                    say(f"{name} : instable {settle} s après le démarrage, retour à la version précédente")
                    try:
                        versions.rollback(name)
                        item.update(result="retour arrière", detail="le service s'est arrêté peu après son démarrage ; version précédente rétablie et épinglée")
                    except ctl.SeedboxError as error:
                        item.update(result="échec", detail="instable après le démarrage et retour impossible : " + str(error)[:200])
                elif not row["healthcheck"]:
                    item["detail"] = "sans contrôle d'état propre : vu « en marche » seulement"
        except ctl.SeedboxError as error:
            text = str(error)
            rolled = "précédente rétablie" in text
            item.update(result="retour arrière" if rolled else "échec", detail=text[:300])
        item["seconds"] = round(time.time() - began)
        if item["result"] in ("retour arrière", "échec") and stop_on_failure:
            stopped = True
    report["finished"] = int(time.time())
    failures = [i for i in report["items"] if i["result"] in ("retour arrière", "échec")]
    updated = [i for i in report["items"] if i["result"] == "mis à jour"]
    report["summary"] = {"updated": len(updated), "same": sum(1 for i in report["items"] if i["result"] == "déjà à jour"),
                         "failed": len(failures), "ignored": sum(1 for i in report["items"] if i["result"] == "ignoré")}
    ctl.STATE.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    title = "Mise à jour par lots : " + (f"{len(failures)} service(s) à examiner" if failures else f"{len(updated)} mis à jour")
    ctl.notifications().record("update", title, ", ".join(f"{i['service']} : {i['result']}" for i in failures)[:300],
                               "warning" if failures else "ok", "versions")
    if failures:
        try:
            ctl.alerts().notify(title, "\n".join(f"{i['service']} : {i['result']} {i['detail']}" for i in failures)[:600], "warning")
        except Exception:  # noqa: BLE001
            pass
        raise ctl.SeedboxError(title)
    say(title)
    return report


def overview():
    return {"plan": plan(), "last": last_report()}
