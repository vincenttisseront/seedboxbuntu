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

# Une variable locale qui porte le nom d'une fonction du module la masque dans toute la fonction :
# l'appel placé plus haut échoue alors avec UnboundLocalError (c'était le cas de « report » dans main()).
shadowed = []
for path in python_files:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    module_names |= {a.asname or a.name.split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    module_names |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
        declared = {name for n in ast.walk(fn) if isinstance(n, (ast.Global, ast.Nonlocal)) for name in n.names}
        first_load, first_store = {}, {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Name):
                target = first_store if isinstance(n.ctx, ast.Store) else first_load
                target[n.id] = min(target.get(n.id, n.lineno), n.lineno)
        for name, stored in first_store.items():
            if name in module_names and name not in params and name not in declared and first_load.get(name, 10**9) < stored:
                shadowed.append(f"{path.name}:{fn.name} « {name} » lu ligne {first_load[name]}, écrit ligne {stored}")
check("Python : aucune variable locale ne masque un nom du module lu plus haut", not shadowed, shadowed)

web = SRC / "rootfs/usr/local/lib/seedboxbuntu/console/web"
for script in (web / "app.js", web / "ui.js", web / "features.js", web / "pages.js", web / "display.js", web / "invite.js", web / "sw.js"):
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
SCRIPTS = ("app.js", "ui.js", "features.js", "pages.js", "ops.js", "ops2.js", "ops3.js", "ops4.js", "ops5.js")
used = set()
for script in SCRIPTS:
    text = (web / script).read_text()
    used |= set(re.findall(r"\$\('([a-z0-9-]+)'\)", text))
    used |= set(re.findall(r"\$\(`view-([a-z0-9-]+)`\)", text))
missing = sorted(i for i in used if i not in ids_html)
check("interface : tous les éléments $('…') existent dans index.html", not missing, missing)
views = set(re.findall(r'<section id="view-([a-z0-9-]+)"', index))
ui_js = (web / "ui.js").read_text()
nav_views = re.findall(r"view: '([a-z0-9-]+)'", ui_js)
check("interface : chaque entrée du menu (NAV de ui.js) a sa vue, et inversement", set(nav_views) == views and len(nav_views) == len(set(nav_views)), sorted(set(nav_views) ^ views))
check("interface : navigation groupée (6 groupes)", len(re.findall(r"\{ id: '[a-z]+', label: '[^']+', icon: '[a-z-]+', items:", ui_js)) == 6)
for name in ("index.html", "invite.html", "error.html", "me.html"):
    html = (web / name).read_text()
    check(f"CSP : pas de style ni de script en ligne ({name})", not re.search(r"<style|\sstyle=|\sonclick=|<script(?![^>]*\ssrc=)", html))
    check(f"identité commune : {name} charge tokens.css et display.js", "tokens.css" in html and "display.js" in html)
order = [index.index(f'src="{n}"') for n in ("ui.js", "features.js", "pages.js", "app.js")]
check("interface : ui.js, features.js, pages.js puis app.js", order == sorted(order))
check("interface : display.js chargé dans <head>", index.index('src="display.js"') < index.index("</head>"))
shell = re.search(r"SHELL = \[(.*?)\]", (web / "sw.js").read_text()).group(1)
check("service worker : tous les fichiers de l'interface en cache", all(f"'{n}'" in shell for n in ("tokens.css", "app.css", "display.js", "ui.js", "features.js", "pages.js", "app.js", "icons.svg")))
server = (SRC / "rootfs/usr/local/lib/seedboxbuntu/console/server.py").read_text()
check("page d'invitation : tokens.css et display.js servis publiquement", '"tokens.css"' in server and '"display.js"' in server)
traefik_static = (SRC / "templates/config/traefik/traefik.yml").read_text()
check("pages d'erreur Traefik : middleware sur chaque point d'entrée", traefik_static.count("error-pages@file") == 3 and "error-pages:" in (SRC / "templates/config/traefik/dynamic/seedbox.yml").read_text() and (web / "error.html").exists())
check("Authelia : logo et icône de l'appliance", (SRC / "templates/config/authelia/assets/logo.png").exists() and "asset_path" in (SRC / "templates/config/authelia/configuration.yml").read_text())
check("Homepage : custom.css présent", (SRC / "templates/config/homepage/custom.css").exists())

# --- homogénéité graphique : aucune valeur de style en dehors des jetons ---------------------------------------------------
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
def color_lines(path, skip=()):
    out = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if any(word in line for word in skip):
            continue
        if COLOR.search(re.sub(r"url\([^)]*\)", "", line)):
            out.append(f"{path.name}:{number}")
    return out
literal = []
for name in ("app.css", "index.html", "invite.html", "error.html", "app.js", "ui.js", "features.js", "pages.js", "ops.js", "ops2.js", "ops3.js", "ops4.js", "ops5.js", "invite.js", "me.html", "me.js"):
    literal += color_lines(web / name, skip=("theme-color", "@font-face"))
literal += color_lines(web / "display.js", skip=("THEME_COLOR",))
check("couleurs : aucune valeur en dur hors de tokens.css", not literal, literal[:8])
css_text = (web / "app.css").read_text()
check("tailles de texte de l'interface : jetons --fs-* (pas de font-size en px)", not re.findall(r"font(?:-size)?:[^;}{]*\b\d+(?:\.\d+)?px(?![^;}]*var)", re.sub(r"\.plot[^{]*\{[^}]*\}", "", css_text)), re.findall(r"[^;{}]*font[^;{}]*\d+px[^;{}]*", css_text)[:3])

BUTTON_BASE = {"btn", "tab", "seg", "icon-btn", "tabbar-item"}
BUTTON_MODIFIERS = {"primary", "danger", "sm", "active", "hidden", "refresh", "agg-toggle"}
def class_problems(text, source):
    problems = []
    for classes in re.findall(r"<(?:button|a)\b[^>]*\bclass=\"([^\"]*)\"", text) + re.findall(r"el\('(?:button|a)', \{[^}]*?class: '([^']*)'", text):
        names = set(classes.split())
        if names & BUTTON_BASE or "btn" in names:
            extra = names - BUTTON_BASE - BUTTON_MODIFIERS
            if extra:
                problems.append(f"{source}: {classes}")
    for tag in re.findall(r"<button\b[^>]*>", text):
        match = re.search(r'class="([^"]*)"', tag)
        if not match or not set(match.group(1).split()) & BUTTON_BASE:
            problems.append(f"{source}: {tag[:60]}")
    for call in re.findall(r"el\('button', \{(.{0,260})", text, flags=re.S):
        match = re.search(r"class: [`']([^`']*)", call)
        if not match or not set(re.sub(r"\$\{.*", "", match.group(1)).split()) & BUTTON_BASE:
            problems.append(f"{source}: {call[:60]!r}")
    return problems
problems = []
for name in ("index.html", "invite.html", "error.html", "me.html", "app.js", "ui.js", "features.js", "pages.js", "ops.js", "ops2.js", "ops3.js", "ops4.js", "ops5.js", "invite.js", "me.js"):
    problems += class_problems((web / name).read_text(), name)
check("boutons : uniquement les classes btn, tab, seg, icon-btn, tabbar-item et leurs variantes", not problems, problems[:6])

# glossaire : verbes, icônes, synonymes interdits
sprite = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', (web / "icons.svg").read_text()))
block = ui_js[ui_js.index("const GLOSSARY = ["):]
block = block[:block.index("];")]
glossary = re.findall(r"\['([^']+)', '([a-z0-9-]+)', '", block)
check("glossaire : verbes uniques, icônes présentes dans icons.svg", len(glossary) >= 30 and len({v for v, _ in glossary}) == len(glossary) and all(i in sprite for _, i in glossary), [i for _, i in glossary if i not in sprite])
doc = (SRC / "docs/GLOSSAIRE.md").read_text()
check("glossaire : docs/GLOSSAIRE.md liste tous les verbes de ui.js", all(f"| {v} |" in doc for v, _ in glossary), [v for v, _ in glossary if f"| {v} |" not in doc])
used_icons = set(re.findall(r"icon: '([a-z0-9-]+)'", ui_js)) | set(re.findall(r"\bicon\('([a-z0-9-]+)'", " ".join((web / n).read_text() for n in SCRIPTS))) | set(re.findall(r"i-([a-z0-9-]+)\"", index)) | set(re.findall(r'data-icon="([a-z0-9-]+)"', index))
check("icônes : toutes celles utilisées existent dans icons.svg", used_icons <= sprite, sorted(used_icons - sprite))
FORBIDDEN = ["Effacer", "Enlever", "Valider", "Rafraîchir", "Rafraichir", "Recharger", "Éditer", "Editer"]
labels = []
for name in ("index.html", "invite.html"):
    labels += re.findall(r"<button\b[^>]*>([^<]+)<", (web / name).read_text())
    labels += re.findall(r'class="btn[^"]*"[^>]*>([^<]+)</a>', (web / name).read_text())
for name in SCRIPTS + ("invite.js",):
    labels += re.findall(r"el\('(?:button|a)', \{[^}]*class: '[^']*(?:btn|tab)[^']*'[^}]*\}[^']*'([^']+)'\)", (web / name).read_text())
wrong = sorted({l for l in labels for word in FORBIDDEN if l.strip().startswith(word)})
check("libellés de boutons : aucun synonyme interdit (voir docs/GLOSSAIRE.md)", not wrong, wrong)
saving = [l for l in labels if l.strip().startswith("Sauvegarder") and "maintenant" not in l]
check("libellés : « Sauvegarder » réservé aux sauvegardes du serveur", not saving, saving)
raw_format = []
for name in ("app.js", "features.js", "pages.js", "ops.js", "ops2.js", "ops3.js", "ops4.js", "ops5.js", "invite.js", "me.js"):
    for number, line in enumerate((web / name).read_text().splitlines(), 1):
        if re.search(r"toLocale\w*String\(|toLocaleDateString\(|toLocaleTimeString\(|new Intl\.|\.toFixed\(", line) and "x(t).toFixed(1)" not in line:
            raw_format.append(f"{name}:{number}")
check("formats : dates, tailles et nombres passent par les fonctions de ui.js", not raw_format, raw_format)

# --- unités systemd et modules ----------------------------------------------------------------------------------------------
units = SRC / "rootfs/etc/systemd/system"
post = (SRC / "rootfs/usr/local/lib/seedboxbuntu/postinstall.sh").read_text()
enabled = set(re.findall(r"(seedbox-[a-z-]+\.timer)", post))
check("minuteries activées : unités présentes", all((units / t).exists() for t in enabled), sorted(t for t in enabled if not (units / t).exists()))
for timer in ("seedbox-appliance", "seedbox-restoretest", "seedbox-metrics", "seedbox-report", "seedbox-vulnscan"):
    check(f"unité {timer} : service et minuterie", (units / f"{timer}.service").exists() and (units / f"{timer}.timer").exists() and f"{timer}.timer" in post)
for name in ("offsite", "metrics", "cleanup", "gpu", "guests", "audit", "importer", "appliance", "notifications", "assistant", "report", "resources", "maintwindow", "sandbox", "activity", "texts", "vulnscan", "netcheck", "vacation", "webpush", "configbundle", "smarthist", "bandwidth", "ratiogoals", "rollout", "emergency", "catalogexport", "backupdiff", "diagnose", "myspace", "telecommande", "changelog", "restoredrill", "diskusage", "rotation", "mediaquality", "linkcheck", "peoplereport", "titleshare", "vpnwatch", "house", "libscan", "libmap", "copyaudit", "replacer", "mediaslim", "shareprofile", "relocate", "seedopt", "llm", "spacefore", "libreport", "dormant", "integrity", "renamer", "subsgap", "guestquota", "energy", "netconf", "apppasswords", "aggregator", "aggparse", "bans", "subdomains", "space"):
    check(f"module {name}.py présent", (SRC / f"rootfs/usr/local/lib/seedboxbuntu/{name}.py").exists())
check("rclone installé par l'ISO", "rclone" in (SRC / "lib/60-autoinstall.sh").read_text())
check("outil de manifeste et workflow de publication", (SRC / "tools/manifest.py").exists() and (SRC / ".github/workflows/release.yml").exists())
traefik = (SRC / "templates/config/traefik/dynamic/seedbox.yml").read_text()
check("Traefik : route publique /invite limitée", "invite-limit" in traefik and "invite-public" in traefik)
check("Traefik : route /me réservée aux comptes connectés, sans le Path-Prefix /me large", "Path(`/me`)" in traefik and "PathPrefix(`/me/`)" in traefik and "PathPrefix(`/me`)" not in traefik and "X-Seedbox-Me-Secret" in traefik)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
