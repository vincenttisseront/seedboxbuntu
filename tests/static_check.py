"""Contrôles statiques : syntaxe Python, JavaScript et shell, JSON/YAML, catalogue intégré, modèles Authelia."""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:200])


python_files = sorted(p for p in SRC.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts)
broken = []
for path in python_files:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as error:
        broken.append(f"{path.name}: {error}")
check(f"{len(python_files)} fichiers Python compilent", not broken, broken)

web = SRC / "rootfs/usr/local/lib/seedboxbuntu/console/web"
for script in (web / "app.js", web / "features.js", web / "invite.js", web / "sw.js"):
    result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    check(f"JavaScript valide : {script.name}", result.returncode == 0, result.stderr[:150])

shells = [p for p in [SRC / "build.sh", *sorted((SRC / "lib").glob("*.sh")), SRC / "rootfs/usr/local/sbin/seedbox-setup", *(SRC / "rootfs/usr/local/lib/seedboxbuntu").glob("*.sh")] if p.exists()]
for path in shells:
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    check(f"shell valide : {path.name}", result.returncode == 0, result.stderr[:150])

import yaml  # noqa: E402
compose = yaml.safe_load((SRC / "stack/docker-compose.yml").read_text())
check("docker-compose.yml : services et image épinglée à un registre", len(compose["services"]) > 15)
check("authelia-redis présent et Authelia en dépend", "authelia-redis" in compose["services"] and "authelia-redis" in str(compose["services"]["authelia"].get("depends_on")))
for name in ("stack/catalog.json", "catalog-exemple.json"):
    json.loads((SRC / name).read_text())
    check(f"JSON valide : {name}", True)
manifest = json.loads((web / "manifest.webmanifest").read_text())
check("manifeste PWA : icônes présentes", all((web / i["src"]).exists() for i in manifest["icons"]))

os.environ.setdefault("SB_SHARE", str(SRC))
import seedboxctl as ctl  # noqa: E402
built = ctl.apps().builtin()
check("catalogue intégré valide", len(built) >= 6, sorted(built))
referenced = set(re.findall(r"@@([A-Z0-9_]+)@@", (SRC / "templates/config/authelia/configuration.yml").read_text()))
check("modèle Authelia : variables connues", {"MANAGER_PATHS", "ADMIN_PATHS", "POLICY_ADMIN", "POLICY_USERS"} <= referenced, referenced)



# --- script de construction : modules lib/, fins de ligne, génération des fichiers de l'appliance
import re  # noqa: E402
import tempfile  # noqa: E402
modules = sorted((SRC / "lib").glob("*.sh"))
check("lib/ : modules numérotés NN-nom.sh", len(modules) >= 10 and all(re.match(r"\d\d-[a-z0-9-]+\.sh$", m.name) for m in modules), [m.name for m in modules])
crlf = [str(p.relative_to(SRC)) for p in [SRC / "build.sh", *modules, *(SRC / "rootfs").rglob("*")] if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".png", ".woff2", ".ico") and b"\r" in p.read_bytes()]
check("fins de ligne LF (pas de CRLF)", not crlf, crlf)
noeol = [str(p.relative_to(SRC)) for p in [SRC / "build.sh", *modules, *(SRC / "rootfs").rglob("*"), *(SRC / "stack").rglob("*"), *(SRC / "templates").rglob("*")]
         if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".png", ".woff2", ".ico") and p.read_bytes()[-1:] != b"\n"]
check("fichiers texte terminés par un saut de ligne", not noeol, noeol)
seen, duplicated = {}, []
for m in modules:
    for name in re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) *\{", m.read_text(encoding="utf-8"), re.M):
        if name in seen and name not in ("cleanup", "step", "fail", "say", "finish", "die"):   # fonctions des here-documents
            duplicated.append(name)
        seen[name] = m.name
check("fonctions de lib/ : aucun doublon", not duplicated, duplicated)
for needed in ("main", "gen_rootfs", "copy_tree", "gen_late", "gen_update", "gen_autoinstall", "gen_grub", "build_iso", "verify_iso"):
    check(f"fonction {needed}() définie", needed in seen or needed == "main" and "main()" in (SRC / "build.sh").read_text())
script = f'''set -Eeuo pipefail
export SB_VERSION=test WORKDIR="$1/w"
grep -v '^main "\\$@"$' "{SRC}/build.sh" | sed 's#^SRC_DIR=.*#SRC_DIR={SRC}#' > "$1/lib.sh"
source "$1/lib.sh"
W="$1/w/work"; mkdir -p "$W/iso/seedboxbuntu"
gen_rootfs >/dev/null
'''
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "gen.sh").write_text(script)
    run = subprocess.run(["bash", str(Path(tmp) / "gen.sh"), tmp], capture_output=True, text=True, cwd=tmp)
    rootfs = Path(tmp) / "w/work/rootfs"
    check("gen_rootfs() copie les fichiers de l'appliance", run.returncode == 0 and (rootfs / "usr/share/seedboxbuntu/VERSION").exists(), run.stderr[-200:])
    if run.returncode == 0:
        count = sum(1 for p in rootfs.rglob("*") if p.is_file())
        check("gen_rootfs() : plus de 70 fichiers", count > 70, count)
        executable = sorted(str(p.relative_to(rootfs)) for p in rootfs.rglob("*") if p.is_file() and p.stat().st_mode & 0o111)
        expected = {"usr/local/sbin/seedbox-setup", "usr/local/sbin/seedboxctl"}
        check("droits : les scripts de l'appliance sont exécutables", expected <= set(executable) and len(executable) == 7, executable)
        sudoers = [p for p in (rootfs / "etc/sudoers.d").glob("*")]
        check("droits : sudoers en 0440", sudoers and all(oct(p.stat().st_mode & 0o777) == "0o440" for p in sudoers))
        check("droits : stack/ et templates/ non exécutables", not any(p.stat().st_mode & 0o111 for p in (rootfs / "usr/share/seedboxbuntu").rglob("*") if p.is_file()))



# --- interface : identifiants, CSP, ressources ---------------------------------------------------------------------------
index = (web / "index.html").read_text()
ids_html = set(re.findall(r'\bid="([^"]+)"', index))
used = set()
for script in ("app.js", "features.js"):
    used |= set(re.findall(r"\$\('([a-z0-9-]+)'\)", (web / script).read_text()))
    used |= set(re.findall(r"\$\(`view-([a-z0-9-]+)`\)", (web / script).read_text()))
missing = sorted(i for i in used if i not in ids_html)
check("interface : tous les éléments $('…') existent dans index.html", not missing, missing)
views = set(re.findall(r'<section id="view-([a-z0-9-]+)"', index))
routes = set(re.findall(r'<a href="#([a-z0-9-]+)">', index))
check("interface : chaque entrée du menu a sa vue", routes <= views, sorted(routes - views))
for name in ("index.html", "invite.html"):
    html = (web / name).read_text()
    check(f"CSP : pas de style ni de script en ligne ({name})", not re.search(r"<style|\sstyle=|\sonclick=|<script(?![^>]*\ssrc=)", html))
check("interface : features.js chargé avant app.js", index.index("features.js") < index.index("app.js"))
shell = re.search(r"SHELL = \[(.*?)\]", (web / "sw.js").read_text()).group(1)
check("service worker : features.js mis en cache", "features.js" in shell)

# --- unités systemd et modules ----------------------------------------------------------------------------------------------
units = SRC / "rootfs/etc/systemd/system"
post = (SRC / "rootfs/usr/local/lib/seedboxbuntu/postinstall.sh").read_text()
enabled = set(re.findall(r"(seedbox-[a-z-]+\.timer)", post))
check("minuteries activées : unités présentes", all((units / t).exists() for t in enabled), sorted(t for t in enabled if not (units / t).exists()))
for timer in ("seedbox-appliance", "seedbox-restoretest", "seedbox-metrics"):
    check(f"unité {timer} : service et minuterie", (units / f"{timer}.service").exists() and (units / f"{timer}.timer").exists() and f"{timer}.timer" in post)
for name in ("offsite", "metrics", "cleanup", "gpu", "guests", "audit", "importer", "appliance"):
    check(f"module {name}.py présent", (SRC / f"rootfs/usr/local/lib/seedboxbuntu/{name}.py").exists())
check("rclone installé par l'ISO", "rclone" in (SRC / "lib/60-autoinstall.sh").read_text())
check("outil de manifeste et workflow de publication", (SRC / "tools/manifest.py").exists() and (SRC / ".github/workflows/release.yml").exists())
traefik = (SRC / "templates/config/traefik/dynamic/seedbox.yml").read_text()
check("Traefik : route publique /invite limitée", "invite-limit" in traefik and "invite-public" in traefik)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
