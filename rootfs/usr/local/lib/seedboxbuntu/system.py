"""SeedBoxBuntu - page Système : ressources, disques (SMART), conteneurs et journaux."""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import seedboxctl as ctl

HOST_UNITS = ("seedbox-console", "seedbox-deploy", "seedbox-upgrade", "seedbox-refresh", "seedbox-alerts",
              "seedbox-backup", "seedbox-autoupdate", "seedbox-shares", "seedbox-firewall", "docker", "ssh")
LINES = (50, 100, 200, 500)


def run(*command, timeout=20):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", str(error)


def meminfo():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            values[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {}
    total, available = values.get("MemTotal", 0), values.get("MemAvailable", 0)
    swap_total, swap_free = values.get("SwapTotal", 0), values.get("SwapFree", 0)
    return {"total": total, "used": total - available, "swap_total": swap_total, "swap_used": swap_total - swap_free}


def uptime():
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def network():
    """Compteurs cumulés de l'interface principale : le navigateur en déduit le débit."""
    best = None
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, _, data = line.partition(":")
            name = name.strip()
            if name == "lo" or name.startswith(("docker", "br-", "veth", "tun", "wg")):
                continue
            fields = data.split()
            counters = {"interface": name, "rx": int(fields[0]), "tx": int(fields[8])}
            if best is None or counters["rx"] + counters["tx"] > best["rx"] + best["tx"]:
                best = counters
    except (OSError, ValueError, IndexError):
        pass
    return best or {"interface": "", "rx": 0, "tx": 0}


def disks():
    seen, rows = set(), []
    candidates = [("Stockage (médias, téléchargements)", ctl.STORAGE), ("Système et configuration", ctl.BASE)]
    try:
        shares = ctl.shares()
        for target in shares.mounted_targets():
            if str(target).startswith(str(shares.ROOT)):
                candidates.append((f"Partage {Path(target).name}", Path(target)))
    except Exception:  # noqa: BLE001 - un partage illisible ne doit pas casser la page
        pass
    for label, path in candidates:
        try:
            device = os.stat(path).st_dev
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        if device in seen:
            continue
        seen.add(device)
        rows.append({"label": label, "path": str(path), "total": usage.total, "used": usage.used,
                     "percent": round(100 * usage.used / usage.total) if usage.total else 0})
    return rows


def smart():
    """État SMART des disques physiques ; indisponible sur une machine virtuelle ou sans smartmontools."""
    if not shutil.which("smartctl"):
        return {"available": False, "disks": [], "reason": "smartmontools n'est pas installé"}
    code, out, _ = run("lsblk", "-dn", "-o", "NAME,TYPE,MODEL", "-J")
    names = []
    try:
        names = [d for d in json.loads(out)["blockdevices"] if d.get("type") == "disk" and not str(d["name"]).startswith(("loop", "ram", "zram"))]
    except (ValueError, KeyError, TypeError):
        pass
    rows = []
    for disk in names:
        code, out, _ = run("smartctl", "-H", "-A", "-j", f"/dev/{disk['name']}", timeout=30)
        try:
            data = json.loads(out)
        except ValueError:
            continue
        passed = (data.get("smart_status") or {}).get("passed")
        if passed is None:
            rows.append({"name": disk["name"], "model": (disk.get("model") or "").strip(), "supported": False})
            continue
        rows.append({"name": disk["name"], "model": data.get("model_name") or (disk.get("model") or "").strip(), "supported": True,
                     "passed": bool(passed), "temperature": (data.get("temperature") or {}).get("current"),
                     "hours": (data.get("power_on_time") or {}).get("hours")})
    return {"available": True, "disks": rows, "reason": ""}


def overview():
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    return {"time": time.time(), "uptime": uptime(), "cpus": os.cpu_count() or 1, "load": list(load),
            "memory": meminfo(), "network": network(), "disks": disks(), "smart": smart()}


def size_to_bytes(text):
    match = re.fullmatch(r"([0-9.]+)\s*([KMGT]?i?B)", text.strip())
    if not match:
        return 0
    units = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}
    return int(float(match.group(1)) * units.get(match.group(2).upper(), 1))


def containers():
    """CPU et mémoire de chaque conteneur (docker stats, une mesure)."""
    code, out, err = run("docker", "stats", "--no-stream", "--format", "{{json .}}", timeout=40)
    if code != 0:
        raise ctl.SeedboxError("docker stats : " + (err.strip() or "indisponible"))
    rows = []
    for line in out.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        used = item.get("MemUsage", "").split("/")[0]
        rows.append({"name": item.get("Name", ""), "cpu": float(item.get("CPUPerc", "0").rstrip("%") or 0),
                     "memory": size_to_bytes(used), "memory_percent": float(item.get("MemPerc", "0").rstrip("%") or 0),
                     "net": item.get("NetIO", "")})
    return sorted(rows, key=lambda r: -r["cpu"])


def log_sources():
    names = sorted(ctl.containers().keys())
    return {"containers": names, "host": list(HOST_UNITS)}


def logs(source, lines=200):
    """Dernières lignes d'un conteneur (« c:nom ») ou d'une unité systemd de l'appliance (« h:unité »)."""
    lines = lines if lines in LINES else 200
    kind, _, name = source.partition(":")
    if kind == "c":
        if name not in ctl.containers():
            raise ctl.SeedboxError("conteneur inconnu")
        code, out, err = run("docker", "logs", "--tail", str(lines), "--timestamps", name, timeout=30)
        text = (out + err)
    elif kind == "h":
        if name not in HOST_UNITS:
            raise ctl.SeedboxError("service inconnu")
        code, out, err = run("journalctl", "-u", name, "-n", str(lines), "--no-pager", "-o", "short-iso", timeout=30)
        text = out + err
    else:
        raise ctl.SeedboxError("source inconnue")
    return {"source": source, "text": text[-60000:]}
