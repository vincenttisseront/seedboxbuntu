"""SeedBoxBuntu - transcodage matériel de Jellyfin (GPU Intel, AMD ou NVIDIA).

Détecte les cartes graphiques du serveur, les transmet au conteneur Jellyfin (avec les groupes « render » et
« video », sans lesquels un conteneur qui ne tourne pas en root ne peut pas ouvrir le GPU) et règle le
transcodage matériel de Jellyfin lui-même (Tableau de bord > Lecture). Un essai réel d'encodage dans le
conteneur dit si le GPU fonctionne.

  Intel / AMD   VAAPI (/dev/dri), pilotes i915, xe ou amdgpu
  Intel         QSV, pour les processeurs récents
  NVIDIA        NVENC : nécessite nvidia-container-toolkit sur le serveur (le pilote et l'exécutable nvidia-smi)

Réglage « mode » : auto (recommandé), off, vaapi, qsv, nvenc. Fichier /etc/seedboxbuntu/gpu.json.

    seedboxctl gpu show | set --mode MODE | apply | test
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "gpu.json"
OVERRIDE_NAME = "docker-compose.override.yml"
SYS_DRM = Path(os.environ.get("SB_SYS_DRM", "/sys/class/drm"))
DEV_DRI = Path(os.environ.get("SB_DEV_DRI", "/dev/dri"))
MODES = ("auto", "off", "vaapi", "qsv", "nvenc")
VENDORS = {"0x8086": "intel", "0x1002": "amd", "0x10de": "nvidia"}
HEADER = "# Généré par seedboxctl : transcodage matériel de Jellyfin (gpu.py)\n"
# Codecs décodés par le matériel (Jellyfin ignore ceux que la puce ne gère pas)
DECODING = ["h264", "hevc", "mpeg2video", "vc1", "vp8", "vp9", "av1"]


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    mode = data.get("mode", "auto")
    return {"mode": mode if mode in MODES else "auto"}


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def lspci_name(slot):
    if not slot or not shutil.which("lspci"):
        return ""
    result = subprocess.run(["lspci", "-s", slot, "-mm"], capture_output=True, text=True)
    parts = [p for p in result.stdout.replace('"', "\x00").split("\x00") if p.strip()]
    return " ".join(parts[2:4]).strip() if len(parts) >= 4 else ""


def detect():
    """GPU présents : cartes DRI (Intel, AMD) et NVIDIA."""
    devices = []
    for node in sorted(DEV_DRI.glob("renderD*")) if DEV_DRI.is_dir() else []:
        sysdir = SYS_DRM / node.name / "device"
        vendor = VENDORS.get(read(sysdir / "vendor"), "autre")
        driver = Path(os.path.realpath(sysdir / "driver")).name if (sysdir / "driver").exists() else ""
        slot = Path(os.path.realpath(sysdir)).name if sysdir.exists() else ""
        try:
            gid = node.stat().st_gid
        except OSError:
            gid = None
        devices.append({"node": str(node), "vendor": vendor, "driver": driver, "slot": slot,
                        "name": lspci_name(slot) or {"intel": "GPU Intel", "amd": "GPU AMD", "nvidia": "GPU NVIDIA"}.get(vendor, "GPU"),
                        "gid": gid})
    nvidia = {"smi": bool(shutil.which("nvidia-smi")), "runtime": False, "name": ""}
    if nvidia["smi"]:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True)
        nvidia["name"] = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
        info = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True) if shutil.which("docker") else None
        nvidia["runtime"] = bool(info and info.returncode == 0 and "nvidia" in info.stdout)
    video_gid = None
    try:
        import grp
        video_gid = grp.getgrnam("video").gr_gid
    except (ImportError, KeyError):
        pass
    return {"devices": devices, "nvidia": nvidia, "video_gid": video_gid}


def choose(mode, detection):
    """Technique retenue : (mode effectif, raison). 'none' si aucun GPU utilisable."""
    dri = [d for d in detection["devices"] if d["vendor"] in ("intel", "amd")]
    nv = detection["nvidia"]
    if mode == "off":
        return "none", "transcodage matériel désactivé"
    if mode == "nvenc" or (mode == "auto" and not dri and nv["smi"]):
        if not nv["smi"]:
            return "none", "aucune carte NVIDIA détectée (nvidia-smi absent)"
        if not nv["runtime"]:
            return "none", "NVIDIA détectée mais nvidia-container-toolkit n'est pas installé : Docker ne peut pas lui donner accès"
        return "nvenc", "NVIDIA : " + (nv["name"] or "GPU")
    if mode in ("vaapi", "qsv", "auto"):
        if not dri:
            return "none", "aucun GPU Intel ou AMD détecté (/dev/dri absent)" if mode == "auto" else f"{mode} demandé mais aucun GPU Intel ou AMD détecté"
        if mode == "qsv" and not any(d["vendor"] == "intel" for d in dri):
            return "none", "QSV demandé mais aucun GPU Intel détecté"
        if mode == "auto":
            return "vaapi", "GPU " + dri[0]["name"]
        return mode, "GPU " + dri[0]["name"]
    return "none", "mode inconnu"


def override_text(effective, detection):
    if effective == "none":
        return ""
    if effective == "nvenc":
        return (HEADER + "services:\n  jellyfin:\n    environment:\n      NVIDIA_VISIBLE_DEVICES: all\n"
                "      NVIDIA_DRIVER_CAPABILITIES: all\n    deploy:\n      resources:\n        reservations:\n          devices:\n"
                "            - driver: nvidia\n              count: all\n              capabilities: [gpu]\n")
    groups = sorted({str(g) for g in [detection.get("video_gid")] + [d["gid"] for d in detection["devices"]] if g not in (None, 0)})
    text = HEADER + "services:\n  jellyfin:\n    devices:\n      - /dev/dri:/dev/dri\n"
    if groups:
        text += "    group_add:\n" + "".join(f'      - "{g}"\n' for g in groups)
    return text


def write_override(detection=None):
    """Écrit (ou retire) le fichier de surcharge Docker Compose. Renvoie True s'il a changé."""
    override = ctl.BASE / OVERRIDE_NAME
    detection = detection or detect()
    effective, _ = choose(settings()["mode"], detection)
    text = override_text(effective, detection)
    current = override.read_text() if override.exists() else ""
    if text:
        changed = current != text
        if changed:
            override.write_text(text)
    else:
        changed = override.exists() and (current.startswith(HEADER) or "GPU détecté" in current)   # ancien format compris
        if changed:
            override.unlink()
    if changed:
        ctl.sync_compose_files()
    return changed


def jellyfin_encoding(effective, detection):
    """Réglages d'encodage de Jellyfin pour la technique retenue."""
    if effective == "none":
        return {"HardwareAccelerationType": "none", "EnableHardwareEncoding": False}
    node = next((d["node"] for d in detection["devices"] if d["vendor"] in ("intel", "amd")), "/dev/dri/renderD128")
    values = {"HardwareAccelerationType": effective, "EnableHardwareEncoding": True, "EnableTonemapping": True,
              "HardwareDecodingCodecs": DECODING, "EnableDecodingColorDepth10Hevc": True, "EnableDecodingColorDepth10Vp9": True,
              "AllowHevcEncoding": False}
    if effective in ("vaapi", "qsv"):
        values["VaapiDevice"] = node
        values["QsvDevice"] = node
    return values


def configure_jellyfin(detection=None):
    """Règle le transcodage matériel dans Jellyfin (API d'administration)."""
    detection = detection or detect()
    effective, reason = choose(settings()["mode"], detection)
    if not ctl.container_running("jellyfin"):
        say("Jellyfin est arrêté : réglage appliqué à son prochain démarrage de la console")
        return effective
    current = ctl.jellyfin_api("GET", "/System/Configuration/encoding")
    if not current.ok:
        raise ctl.SeedboxError(f"Jellyfin : lecture des réglages d'encodage refusée (HTTP {current.status})")
    body = {**current.json(), **jellyfin_encoding(effective, detection)}
    result = ctl.jellyfin_api("POST", "/System/Configuration/encoding", body=body)
    if not result.ok:
        raise ctl.SeedboxError(f"Jellyfin : écriture des réglages d'encodage refusée (HTTP {result.status})")
    say(f"Jellyfin : transcodage matériel {'désactivé' if effective == 'none' else effective.upper()} ({reason})")
    return effective


def apply(restart=True):
    detection = detect()
    changed = write_override(detection)
    if changed and restart and (ctl.ENV_FILE.exists() and ctl.container_running("jellyfin")):
        say("Jellyfin est recréé pour recevoir (ou rendre) le GPU")
        ctl.compose("up", "-d", "--force-recreate", "jellyfin", capture=True)
    configure_jellyfin(detection)
    return changed


def set_mode(mode):
    if mode not in MODES:
        raise ctl.SeedboxError("mode inconnu : " + ", ".join(MODES))
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"mode": mode}) + "\n")
    say(f"transcodage matériel : mode {mode}")
    return apply()


def test():
    """Encode une seconde d'image dans le conteneur Jellyfin avec le GPU : le seul vrai juge."""
    detection = detect()
    effective, reason = choose(settings()["mode"], detection)
    if effective == "none":
        raise ctl.SeedboxError(reason)
    if not ctl.container_running("jellyfin"):
        raise ctl.SeedboxError("Jellyfin n'est pas démarré")
    ffmpeg = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
    if effective == "nvenc":
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=1",
                   "-c:v", "h264_nvenc", "-f", "null", "-"]
    else:
        node = next(d["node"] for d in detection["devices"] if d["vendor"] in ("intel", "amd"))
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-init_hw_device", f"vaapi=va:{node}", "-filter_hw_device", "va",
                   "-f", "lavfi", "-i", "color=c=black:s=256x256:d=1", "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"]
    result = subprocess.run(["docker", "exec", "jellyfin", *command], capture_output=True, text=True, timeout=90)
    output = (result.stderr or result.stdout).strip()[-400:]
    if result.returncode != 0:
        raise ctl.SeedboxError("l'encodage matériel échoue dans Jellyfin : " + (output or "aucun détail"))
    say(f"essai d'encodage matériel réussi ({effective.upper()}, {reason})")
    return {"ok": True, "mode": effective, "reason": reason}


def overview():
    detection = detect()
    cfg = settings()
    effective, reason = choose(cfg["mode"], detection)
    override = ctl.BASE / OVERRIDE_NAME
    return {"settings": cfg, "modes": list(MODES), "devices": detection["devices"], "nvidia": detection["nvidia"],
            "effective": effective, "reason": reason, "active": override.exists() and override.read_text().startswith(HEADER)}
