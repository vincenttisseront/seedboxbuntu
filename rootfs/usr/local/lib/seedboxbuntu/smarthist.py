"""SeedBoxBuntu - santé des disques dans le temps : attributs SMART, tendance et alerte avant la panne.

Une fois par heure au plus (le minuteur de métriques passe toutes les 5 minutes), les attributs SMART de chaque disque
physique sont lus (smartctl -n standby : un disque en veille n'est jamais réveillé pour cela) et enregistrés dans
metrics.db sous les clés « smart.<disque>.<mesure> » : secteurs réalloués, secteurs en attente, erreurs non corrigées,
température, usure (NVMe), état global. Ces clés n'apparaissent pas dans l'historique de la machine.

Une dérive se voit sur la durée, pas sur une lecture : un disque qui réalloue des secteurs depuis des semaines est un
disque à remplacer, même si son état SMART global affiche encore « PASSED ». L'alerte part donc de la tendance :
  - état SMART en échec                                   -> critique
  - secteurs en attente (pending) : au moins 1 / au moins 8 -> avertissement / critique
  - secteurs réalloués en hausse sur 30 jours : au moins 1 / au moins 10 -> avertissement / critique
  - erreurs non corrigées, usure NVMe >= 90 %, température élevée (disque 55 °C, NVMe 70 °C sur 24 h) -> avertissement
Limite assumée : une machine virtuelle ou un RAID matériel ne laisse pas voir les disques réels ; la page l'indique.

    seedboxctl smarthist show | sample
"""
import json
import re
import time

import seedboxctl as ctl

LAST_FILE = ctl.STATE / "smart-last.json"
EVERY = 3300                                  # une lecture par heure au plus
WINDOW = 30 * 86400
DEVICE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
ATA_IDS = {5: "realloc", 187: "uncorrect", 197: "pending", 198: "offline"}
TEMP_DISK = (55, 60)
TEMP_NVME = (70, 80)
MEASURES = ("realloc", "pending", "uncorrect", "temp", "wear", "hours", "passed", "nvme")


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def parse(data):
    """Mesures utiles d'une sortie « smartctl -j » : {nom: valeur}. Vide si le disque ne répond pas au SMART."""
    out = {}
    status = (data.get("smart_status") or {}).get("passed")
    if status is None:
        return out
    out["passed"] = 1 if status else 0
    temperature = (data.get("temperature") or {}).get("current")
    if isinstance(temperature, (int, float)):
        out["temp"] = float(temperature)
    hours = (data.get("power_on_time") or {}).get("hours")
    if isinstance(hours, (int, float)):
        out["hours"] = float(hours)
    nvme = data.get("nvme_smart_health_information_log")
    if isinstance(nvme, dict):
        out["nvme"] = 1
        if isinstance(nvme.get("percentage_used"), (int, float)):
            out["wear"] = float(nvme["percentage_used"])
        if isinstance(nvme.get("media_errors"), (int, float)):
            out["uncorrect"] = float(nvme["media_errors"])
        return out
    for item in ((data.get("ata_smart_attributes") or {}).get("table") or []):
        name = ATA_IDS.get(item.get("id"))
        raw = (item.get("raw") or {}).get("value")
        if name and isinstance(raw, (int, float)):
            # Les octets de poids fort de certains disques portent d'autres compteurs : seuls les 16 bits bas comptent ici
            out[name] = float(raw & 0xFFFF) if raw > 0xFFFFFFFF else float(raw)
    return out


def read_disk(name):
    """Lit un disque : (modèle, mesures). Remplacée par les tests."""
    code, out, _ = ctl.sysinfo().run("smartctl", "-n", "standby", "-H", "-A", "-i", "-j", f"/dev/{name}", timeout=30)
    try:
        data = json.loads(out)
    except ValueError:
        return "", {}
    return (data.get("model_name") or "").strip(), parse(data)


def disk_names():
    import shutil
    if not shutil.which("smartctl"):
        return None
    code, out, _ = ctl.sysinfo().run("lsblk", "-dn", "-o", "NAME,TYPE", "-J")
    try:
        return [d["name"] for d in json.loads(out)["blockdevices"]
                if d.get("type") == "disk" and DEVICE.match(str(d["name"])) and not str(d["name"]).startswith(("loop", "ram", "zram"))]
    except (ValueError, KeyError, TypeError):
        return []


def last_state():
    try:
        value = json.loads(LAST_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def sample(now=None, force=False):
    """Mesures prêtes pour metrics.collect : {« smart.sda.temp »: 34.0, ...}. Vide si une lecture récente existe."""
    now = time.time() if now is None else now
    state = last_state()
    if not force and now - state.get("time", 0) < EVERY:
        return {}
    names = disk_names()
    values, models = {}, {}
    if names is None:
        state = {"time": now, "models": {}, "reason": "smartmontools n'est pas installé"}
    else:
        for name in names:
            try:
                model, measures = read_disk(name)
            except Exception:  # noqa: BLE001 - un disque qui ne répond pas ne bloque pas les autres
                continue
            if not measures:
                continue
            models[name] = model
            values.update({f"smart.{name}.{key}": value for key, value in measures.items()})
        state = {"time": now, "models": models,
                 "reason": "" if models else "aucun disque physique ne répond au SMART (machine virtuelle, RAID matériel ou pont USB)"}
    try:
        LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_FILE.write_text(json.dumps(state))
    except OSError:
        pass
    return values


# --- lecture et analyse -------------------------------------------------------------------------
def series(name, measure, seconds=WINDOW, now=None):
    return ctl.metrics().points(f"smart.{name}.{measure}", seconds, now)


def latest(points):
    return points[-1][1] if points else None


def growth(points):
    """Hausse d'un compteur sur la période (dernière valeur - première), 0 sans historique."""
    return max(0.0, points[-1][1] - points[0][1]) if len(points) >= 2 else 0.0


def judge(name, now=None):
    """Évaluation d'un disque : niveau (ok, warning, critical), constats en français et mesures."""
    now = time.time() if now is None else now
    data = {m: series(name, m, WINDOW, now) for m in MEASURES}
    last = {m: latest(p) for m, p in data.items()}
    notes, level = [], "ok"

    def raise_to(new):
        nonlocal level
        if ("ok", "warning", "critical").index(new) > ("ok", "warning", "critical").index(level):
            level = new

    if last["passed"] == 0:
        raise_to("critical")
        notes.append("L'état SMART global signale une défaillance : sauvegardez et remplacez ce disque.")
    pending = last["pending"] or 0
    if pending >= 8:
        raise_to("critical")
    if pending >= 1:
        raise_to("warning")
        notes.append(f"{int(pending)} secteur(s) en attente de réallocation : le disque n'arrive plus à les lire.")
    rise = growth(data["realloc"])
    if rise >= 10:
        raise_to("critical")
    if rise >= 1:
        raise_to("warning")
        notes.append(f"Secteurs réalloués : +{int(rise)} sur 30 jours (total {int(last['realloc'] or 0)}) : la surface se dégrade.")
    elif (last["realloc"] or 0) > 0:
        notes.append(f"{int(last['realloc'])} secteur(s) réalloué(s) au total, stable sur 30 jours.")
    if (last["uncorrect"] or 0) > 0:
        raise_to("warning")
        notes.append(f"{int(last['uncorrect'])} erreur(s) non corrigée(s) rapportée(s) par le disque.")
    wear = last["wear"]
    if wear is not None and wear >= 90:
        raise_to("critical" if wear >= 100 else "warning")
        notes.append(f"Usure du SSD : {int(wear)} % de sa durée de vie annoncée.")
    day = [v for t, v in data["temp"] if t >= now - 86400]
    peak = max(day) if day else last["temp"]
    limits = TEMP_NVME if last["nvme"] else TEMP_DISK
    if peak is not None and peak >= limits[0]:
        raise_to("critical" if peak >= limits[1] else "warning")
        notes.append(f"Température élevée : {int(peak)} °C au plus sur 24 h (seuil {limits[0]} °C).")
    return {"level": level, "notes": notes,
            "values": {m: last[m] for m in MEASURES if last[m] is not None},
            "peak_temp_24h": peak, "realloc_growth_30d": int(rise),
            "series": {"temp": data["temp"][-120:], "realloc": data["realloc"][-120:]}}


def names_known():
    prefix = "smart."
    found = set()
    for key in ctl.metrics().keys():
        if key.startswith(prefix) and key.count(".") == 2:
            found.add(key.split(".")[1])
    return sorted(found)


def overview(now=None):
    state = last_state()
    models = state.get("models", {})
    disks = []
    for name in names_known():
        disks.append({"name": name, "model": models.get(name, ""), **judge(name, now)})
    reason = state.get("reason", "")
    return {"available": bool(disks), "reason": reason if not disks else "", "disks": disks,
            "last": int(state.get("time", 0)), "every_minutes": EVERY // 60}


def findings(now=None):
    """Constats pour alerts.findings : un par disque dont la santé dérive."""
    found = {}
    models = last_state().get("models", {})
    for name in names_known():
        verdict = judge(name, now)
        if verdict["level"] == "ok":
            continue
        label = f"{name} ({models[name]})" if models.get(name) else name
        found[f"smart:{name}"] = (f"Santé du disque {label} : à surveiller" if verdict["level"] == "warning"
                                  else f"Santé du disque {label} : remplacement à prévoir",
                                  " ".join(verdict["notes"]) or "Voir Système > Santé des disques.", verdict["level"])
    return found
