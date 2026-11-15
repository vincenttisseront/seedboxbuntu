"""SeedBoxBuntu - failles connues dans les images Docker utilisées (Trivy).

Désactivé par défaut : l'analyse télécharge l'image de Trivy (quelques dizaines de Mo) et sa base de failles. Une fois
activée, elle passe chaque semaine (seedbox-vulnscan.timer, dans la fenêtre de maintenance si elle est réglée) et à la
demande. Pour chaque image en service : nombre de failles CRITIQUES et ÉLEVÉES **qui ont un correctif publié** (les failles
sans correctif ne sont pas actionnables et sont comptées à part). Une alerte est envoyée quand une image a une faille
critique corrigeable ; la page indique laquelle et quelle mise à jour la règle.
Trivy ne regarde que les paquets de l'image : il ne remplace ni les mises à jour de l'hôte ni une analyse de configuration.
    seedboxctl vulnscan show | set | scan | auto
"""
import json
import os
import re
import subprocess
import time

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "vulnscan.json"
RESULT_FILE = ctl.STATE / "vulns.json"
TRIVY_IMAGE = "ghcr.io/aquasecurity/trivy:0.58.1"
CACHE_VOLUME = "seedbox-trivy-cache"
DEFAULTS = {"enabled": False}
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,200}$")
MAX_LISTED = 8


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        cfg[key] = bool(value)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    say(f"analyse de vulnérabilités : {'activée' if cfg['enabled'] else 'désactivée'}")
    return cfg


def read_results():
    try:
        return json.loads(RESULT_FILE.read_text())
    except (OSError, ValueError):
        return {"scanned": 0, "images": [], "error": ""}


def running_images():
    """{image: [noms de conteneurs]} des conteneurs de la seedbox."""
    out = ctl.run("docker", "ps", "--format", "{{.Image}}\t{{.Names}}", capture=True).stdout or ""
    images = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        image, name = line.split("\t", 1)
        if IMAGE_RE.match(image):
            images.setdefault(image, []).append(name.strip())
    return images


def trivy(image):
    """Sortie JSON de Trivy pour une image (le démon Docker est lu en lecture seule, la base est mise en cache)."""
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock:ro", "-v", f"{CACHE_VOLUME}:/root/.cache/",
         TRIVY_IMAGE, "image", "--quiet", "--format", "json", "--severity", "HIGH,CRITICAL", "--scanners", "vuln", image],
        capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise ctl.SeedboxError("Trivy : " + (result.stderr.strip().splitlines() or ["échec"])[-1][:160])
    return json.loads(result.stdout or "{}")


def summarize(data):
    """Résumé d'une sortie Trivy : failles corrigeables par gravité, failles sans correctif, liste des plus graves."""
    fixable, unfixed, rows = {"CRITICAL": 0, "HIGH": 0}, 0, []
    for target in data.get("Results") or []:
        for vuln in target.get("Vulnerabilities") or []:
            severity = vuln.get("Severity", "")
            if severity not in fixable:
                continue
            if vuln.get("FixedVersion"):
                fixable[severity] += 1
                rows.append({"id": str(vuln.get("VulnerabilityID", ""))[:40], "package": str(vuln.get("PkgName", ""))[:60],
                             "installed": str(vuln.get("InstalledVersion", ""))[:40], "fixed": str(vuln.get("FixedVersion", ""))[:60],
                             "severity": severity.lower()})
            else:
                unfixed += 1
    rows.sort(key=lambda r: (r["severity"] != "critical", r["package"]))
    return {"critical": fixable["CRITICAL"], "high": fixable["HIGH"], "unfixed": unfixed, "top": rows[:MAX_LISTED]}


def scan(images=None, scanner=None, now=None):
    """Analyse toutes les images en service (ou la liste donnée). Une image en échec n'arrête pas les autres."""
    scanner = scanner or trivy
    now = int(now if now is not None else time.time())
    images = images if images is not None else running_images()
    results, errors = [], []
    for image, names in sorted(images.items()):
        try:
            summary = summarize(scanner(image))
        except (ctl.SeedboxError, subprocess.SubprocessError, ValueError, OSError) as error:
            errors.append(f"{image} : {str(error)[:120]}")
            results.append({"image": image, "containers": names, "error": str(error)[:160]})
            continue
        results.append({"image": image, "containers": names, **summary})
        say(f"{image} : {summary['critical']} critique(s), {summary['high']} élevée(s) corrigeable(s)")
    data = {"scanned": now, "images": results, "error": "; ".join(errors[:3])}
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(data, ensure_ascii=False))
    ctl.notifications().record("vulnscan", f"Analyse de vulnérabilités : {sum(r.get('critical', 0) for r in results)} critique(s)", "", "info", "vulns")
    return data


def auto():
    """Minuteur hebdomadaire : n'analyse que si c'est activé et si la fenêtre de maintenance le permet."""
    if not settings()["enabled"]:
        say("analyse de vulnérabilités désactivée")
        return None
    if not ctl.maintwindow().allow("vulnscan"):
        return None
    return scan()


def overview():
    data = read_results()
    return {"settings": settings(), "scanned": data.get("scanned", 0), "images": data.get("images", []), "error": data.get("error", ""),
            "totals": {"critical": sum(i.get("critical", 0) for i in data.get("images", [])),
                       "high": sum(i.get("high", 0) for i in data.get("images", []))}}


def findings():
    """Pour alerts.findings : une alerte par image qui a une faille critique corrigeable."""
    if not settings()["enabled"]:
        return {}
    found = {}
    for item in read_results().get("images", []):
        if item.get("critical"):
            key = "vuln:" + re.sub(r"[^A-Za-z0-9_.-]", "_", item["image"])
            found[key] = (f"{item['image'].split('/')[-1]} : {item['critical'] + item.get('high', 0)} faille(s) corrigeable(s) dont {item['critical']} critique(s)",
                          "Mettez à jour l'application concernée depuis Applications > Mises à jour. Détail : page Vulnérabilités.", "warning")
    return found
