"""Transcodage matériel : détection (faux /sys et /dev), choix de la technique, surcharge Compose, réglage de Jellyfin."""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbgpu"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
os.environ["SB_SYS_DRM"] = str(root / "sys/class/drm")
os.environ["SB_DEV_DRI"] = str(root / "dev/dri")
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402
import gpu  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


for folder in (ctl.BASE, ctl.ETC, ctl.STATE):
    folder.mkdir(parents=True, exist_ok=True)
calls = []
ctl.sync_compose_files = lambda: calls.append("sync")
ctl.compose = lambda *a, **k: calls.append(("compose",) + a)
ctl.container_running = lambda name: True
override = ctl.BASE / "docker-compose.override.yml"
ctl.ENV_FILE.write_text("DOMAIN=x\n")


def fake_gpu(vendor_id, driver, node="renderD128"):
    (root / "dev/dri").mkdir(parents=True, exist_ok=True)
    (root / "dev/dri" / node).write_text("")
    sysdir = root / "sys/class/drm" / node / "device"
    sysdir.mkdir(parents=True, exist_ok=True)
    (sysdir / "vendor").write_text(vendor_id + "\n")
    target = root / "sys/bus/pci/drivers" / driver
    target.mkdir(parents=True, exist_ok=True)
    if (sysdir / "driver").is_symlink():
        (sysdir / "driver").unlink()
    (sysdir / "driver").symlink_to(target)


# --- 1. aucun GPU ------------------------------------------------------------------------------------------------------
detection = gpu.detect()
check("aucun GPU : rien détecté", detection["devices"] == [] and not detection["nvidia"]["smi"], detection)
effective, reason = gpu.choose("auto", detection)
check("aucun GPU : technique « none » avec explication", effective == "none" and "aucun GPU" in reason, reason)
check("aucun GPU : pas de surcharge Compose", gpu.write_override(detection) is False and not override.exists())
# ancienne surcharge (sans groupes) remplacée quand un GPU apparaît, retirée quand il disparaît
override.write_text("# Généré par seedboxctl : GPU détecté, transmis à Jellyfin\nservices:\n  jellyfin:\n    devices:\n      - /dev/dri:/dev/dri\n")
check("ancienne surcharge de GPU retirée si le GPU a disparu", gpu.write_override(detection) is True and not override.exists())

# --- 2. GPU Intel ----------------------------------------------------------------------------------------------------------
fake_gpu("0x8086", "i915")
detection = gpu.detect()
check("Intel détecté (pilote i915)", detection["devices"][0]["vendor"] == "intel" and detection["devices"][0]["driver"] == "i915", detection["devices"])
check("auto -> VAAPI", gpu.choose("auto", detection)[0] == "vaapi")
check("QSV accepté sur Intel", gpu.choose("qsv", detection)[0] == "qsv")
check("NVENC refusé sans carte NVIDIA", gpu.choose("nvenc", detection)[0] == "none")
calls.clear()
check("surcharge écrite", gpu.write_override(detection) is True and override.exists())
text = override.read_text()
node_gid = (root / "dev/dri/renderD128").stat().st_gid
wanted = {g for g in (gpu.detect()["video_gid"], node_gid) if g}
check("surcharge : périphérique et groupes render/video", "/dev/dri:/dev/dri" in text and all(f'"{g}"' in text for g in wanted), text)
check("surcharge : fichiers Compose resynchronisés", "sync" in calls)
check("surcharge inchangée : pas de réécriture", gpu.write_override(detection) is False)
import yaml  # noqa: E402
check("surcharge : YAML valide", yaml.safe_load(text)["services"]["jellyfin"]["devices"] == ["/dev/dri:/dev/dri"])

# --- 3. réglages de Jellyfin ------------------------------------------------------------------------------------------------------
enc = gpu.jellyfin_encoding("vaapi", detection)
check("Jellyfin : VAAPI avec le bon périphérique", enc["HardwareAccelerationType"] == "vaapi" and enc["VaapiDevice"] == str(root / "dev/dri/renderD128")
      and enc["EnableHardwareEncoding"] and "hevc" in enc["HardwareDecodingCodecs"], enc)
check("Jellyfin : désactivé", gpu.jellyfin_encoding("none", detection)["HardwareAccelerationType"] == "none")

store = {"config": {"HardwareAccelerationType": "none", "TranscodingTempPath": "/config/transcodes", "EncoderPreset": "auto"}}
ctl.jellyfin_api = lambda method, path, body=None, token=None: type("R", (), {
    "ok": True, "status": 200, "json": lambda self=None: dict(store["config"]),
    "_": store["config"].update(body) if method == "POST" else None})()
override.unlink()
calls.clear()
gpu.set_mode("auto")
check("réglage appliqué à Jellyfin, autres réglages conservés", store["config"]["HardwareAccelerationType"] == "vaapi"
      and store["config"]["TranscodingTempPath"] == "/config/transcodes", store["config"])
check("mode enregistré", json.loads(gpu.SETTINGS_FILE.read_text()) == {"mode": "auto"})
check("Jellyfin recréé quand la surcharge change", any(c[:1] == ("compose",) and "jellyfin" in c for c in calls if isinstance(c, tuple)), calls)
gpu.set_mode("off")
check("mode off : surcharge retirée et Jellyfin réglé sur « none »", not override.exists() and store["config"]["HardwareAccelerationType"] == "none")
try:
    gpu.set_mode("turbo")
    check("mode inconnu refusé", False)
except ctl.SeedboxError:
    check("mode inconnu refusé", True)
gpu.set_mode("auto")

# --- 4. AMD, puis NVIDIA ------------------------------------------------------------------------------------------------------------
shutil.rmtree(root / "dev"), shutil.rmtree(root / "sys")
fake_gpu("0x1002", "amdgpu")
d = gpu.detect()
check("AMD détecté -> VAAPI", d["devices"][0]["vendor"] == "amd" and gpu.choose("auto", d)[0] == "vaapi")
check("QSV refusé sur AMD", gpu.choose("qsv", d)[0] == "none")
shutil.rmtree(root / "dev"), shutil.rmtree(root / "sys")
bin_dir = root / "bin"
bin_dir.mkdir()
(bin_dir / "nvidia-smi").write_text("#!/bin/sh\necho 'NVIDIA GeForce RTX 3060'\n")
(bin_dir / "nvidia-smi").chmod(0o755)
(bin_dir / "docker").write_text("#!/bin/sh\necho '{\"nvidia\":{},\"runc\":{}}'\n")
(bin_dir / "docker").chmod(0o755)
os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
d = gpu.detect()
check("NVIDIA détectée avec le runtime Docker", d["nvidia"]["smi"] and d["nvidia"]["runtime"] and "RTX 3060" in d["nvidia"]["name"], d["nvidia"])
effective, reason = gpu.choose("auto", d)
check("auto -> NVENC", effective == "nvenc" and "RTX" in reason, (effective, reason))
nv_text = gpu.override_text("nvenc", d)
nv = yaml.safe_load(nv_text)["services"]["jellyfin"]
check("surcharge NVIDIA : réservation de périphérique et variables", nv["deploy"]["resources"]["reservations"]["devices"][0]["driver"] == "nvidia"
      and nv["environment"]["NVIDIA_VISIBLE_DEVICES"] == "all", nv)
(bin_dir / "docker").write_text("#!/bin/sh\necho '{\"runc\":{}}'\n")
d2 = gpu.detect()
effective, reason = gpu.choose("auto", d2)
check("NVIDIA sans nvidia-container-toolkit : explication", effective == "none" and "nvidia-container-toolkit" in reason, reason)

# --- 5. essai d'encodage -----------------------------------------------------------------------------------------------------------
fake_gpu("0x8086", "i915")
(bin_dir / "docker").write_text("#!/bin/sh\necho \"$@\" > " + str(root / "docker-args") + "\nexit 0\n")
result = gpu.test()
args = (root / "docker-args").read_text()
check("essai : encodage h264_vaapi dans le conteneur Jellyfin", result["ok"] and "exec jellyfin" in args and "h264_vaapi" in args and "renderD128" in args, args[:150])
(bin_dir / "docker").write_text("#!/bin/sh\necho 'Failed to initialise VAAPI' >&2\nexit 1\n")
try:
    gpu.test()
    check("essai en échec expliqué", False)
except ctl.SeedboxError as error:
    check("essai en échec expliqué", "VAAPI" in str(error), error)

# --- 6. console ------------------------------------------------------------------------------------------------------------------------
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu/console"))
import server  # noqa: E402

ctl.env_read = lambda: {"CONSOLE_PROXY_SECRET": "secret"}
started = []
server.start_job = lambda title, argv, stdin=None: started.append(argv) or "0123456789ab"
console = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=console.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{console.server_port}"


def call(path, body=None, groups="admins"):
    headers = {"X-Seedbox-Proxy-Secret": "secret", "Remote-Groups": groups}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


code, data = call("/api/gpu")
check("console : vue d'ensemble", code == 200 and data["devices"] and data["effective"] == "vaapi", data.get("effective"))
check("console : mode transmis à la tâche", call("/api/gpu/settings", {"mode": "off"})[0] == 200 and started[-1] == ["gpu", "set", "--mode", "off"])
check("console : mode invalide refusé", call("/api/gpu/settings", {"mode": "x; rm"})[0] == 400)
check("console : essai", call("/api/gpu/test", {})[0] == 200 and started[-1] == ["gpu", "test"])
check("console : réservé aux administrateurs", call("/api/gpu", groups="users")[0] == 403)

print("échecs :", bad)
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if bad else 0)
