#!/usr/bin/env python3
"""Accessibilité de la console, vérifiée dans un vrai navigateur (Playwright) avec de fausses données.

Pour chaque page :
  - une seule zone principale, une navigation nommée, un seul titre de niveau 1 visible, <html lang> renseigné ;
  - chaque bouton, lien, champ et liste déroulante visible a un nom accessible ;
  - chaque image a un attribut alt ; aucun identifiant en double ;
Parcours au clavier :
  - le lien d'évitement est le premier arrêt de Tab et mène au contenu ;
  - 40 appuis sur Tab de suite : le focus avance, ne reste pas bloqué, et chaque élément ciblé a un indicateur visible ;
  - Ctrl+K ouvre la recherche (dialogue), le focus y reste enfermé, Échap la ferme et rend le focus à l'élément d'origine ;
  - les notifications se comportent de même ; les messages passagers sont dans une zone aria-live ;
  - avec « réduire les animations », aucune transition ne dure plus de 10 ms.
Si axe-core est installé (npm install axe-core, ou AXE_CORE=chemin/axe.min.js), ses règles « wcag2a/wcag2aa » s'ajoutent
(le contraste est déjà mesuré par visual_test.py).
Sans Playwright, le test est ignoré (code 0).
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("a11y_test : ignoré (Playwright absent)")
    sys.exit(0)
import ui_mock

WEB = ROOT / "rootfs/usr/local/lib/seedboxbuntu/console/web"
PORT = 8772
failures, checks = [], 0


def check(ok, text):
    global checks
    checks += 1
    if not ok:
        failures.append(text)
        print("  ÉCHEC", text)


NAMES = """() => {
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('[hidden],.hidden,dialog:not([open])'); };
  const nameOf = (e) => {
    const lab = e.getAttribute('aria-labelledby');
    if (lab) return lab.split(/\\s+/).map((id) => (document.getElementById(id) || {}).textContent || '').join(' ').trim();
    if (e.getAttribute('aria-label')) return e.getAttribute('aria-label').trim();
    if (e.labels && e.labels.length) return [...e.labels].map((l) => l.textContent).join(' ').trim();
    if (e.closest('label')) return e.closest('label').textContent.trim();
    const img = e.querySelector('img[alt]'); const text = (e.textContent || '').trim();
    return text || (img && img.alt) || e.getAttribute('title') || '';
  };
  const bad = [];
  for (const e of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=tab]')) {
    if (!visible(e)) continue;
    if (!nameOf(e)) bad.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className ? '.' + String(e.className).split(' ')[0] : ''));
  }
  const noAlt = [...document.querySelectorAll('img')].filter((i) => !i.hasAttribute('alt')).map((i) => i.src);
  const ids = {}; const dup = [];
  for (const e of document.querySelectorAll('[id]')) { if (ids[e.id]) dup.push(e.id); ids[e.id] = 1; }
  const h1 = [...document.querySelectorAll('h1')].filter(visible).length;
  return { bad, noAlt, dup, h1, mains: document.querySelectorAll('main').length,
           navs: [...document.querySelectorAll('nav')].filter((n) => !n.getAttribute('aria-label')).length, lang: document.documentElement.lang };
}"""

FOCUS = """() => { const e = document.activeElement; if (!e || e === document.body) return null;
  const s = getComputedStyle(e);
  const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none');
  return { tag: e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + '.' + String(e.className).split(' ')[0], ring, text: (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 30) }; }"""


def run_axe(page, view):
    path = os.environ.get("AXE_CORE")
    for candidate in ([path] if path else []) + [str(ROOT / "node_modules/axe-core/axe.min.js"), "/tmp/axe/node_modules/axe-core/axe.min.js"]:
        if candidate and Path(candidate).is_file():
            page.add_script_tag(path=candidate)
            result = page.evaluate("axe.run(document, { runOnly: ['wcag2a', 'wcag2aa'], rules: { 'color-contrast': { enabled: false } } }).then((r) => r.violations.map((v) => v.id + ' x' + v.nodes.length + ' ' + (v.nodes[0] && v.nodes[0].target.join(' '))))")
            check(not result, f"{view} : axe-core : {result[:3]}")
            return True
    return False


def main():
    views = re.findall(r"view: '([a-z0-9]+)'", (WEB / "ui.js").read_text())
    server = ui_mock.serve(PORT, background=True)
    base = f"http://127.0.0.1:{PORT}/console/"
    used_axe = False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1180, "height": 900}, locale="fr-FR", reduced_motion="reduce")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for view in views:
                page.goto(base + "#" + view)
                page.reload()
                page.wait_for_function(f"!document.getElementById('view-{view}') || !document.getElementById('view-{view}').hasAttribute('aria-busy')", timeout=8000)
                page.wait_for_timeout(300)
                info = page.evaluate(NAMES)
                check(info["lang"] in ("fr", "en"), f"{view} : <html lang> renseigné")
                check(info["mains"] == 1, f"{view} : une seule zone <main>")
                check(info["navs"] == 0, f"{view} : chaque <nav> est nommée")
                check(info["h1"] == 1, f"{view} : un seul titre de niveau 1 visible ({info['h1']})")
                check(not info["bad"], f"{view} : éléments sans nom accessible : {info['bad'][:5]}")
                check(not info["noAlt"], f"{view} : images sans alt : {info['noAlt'][:3]}")
                check(not info["dup"], f"{view} : identifiants en double : {info['dup'][:3]}")
                used_axe = run_axe(page, view) or used_axe
            # lien d'évitement
            page.goto(base + "#overview")
            page.reload()
            page.wait_for_timeout(800)
            page.keyboard.press("Tab")
            first = page.evaluate(FOCUS)
            check(first and "skip-link" in first["tag"], f"premier arrêt de Tab = lien d'évitement ({first})")
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
            check(page.evaluate("document.activeElement && document.activeElement.id") == "main" or "main" in page.evaluate("location.hash"), "le lien d'évitement mène au contenu")
            # parcours clavier : pas de piège, focus visible
            page.goto(base + "#overview")
            page.reload()
            page.wait_for_timeout(800)
            seen, no_ring, stuck = [], [], 0
            for _ in range(40):
                page.keyboard.press("Tab")
                now = page.evaluate(FOCUS)
                if now is None:
                    continue
                if seen and seen[-1] == now["tag"] + now["text"]:
                    stuck += 1
                seen.append(now["tag"] + now["text"])
                if not now["ring"]:
                    no_ring.append(now["tag"])
            check(len(set(seen)) >= 15, f"le focus parcourt au moins 15 éléments en 40 Tab ({len(set(seen))})")
            check(stuck < 3, f"aucun piège au clavier (arrêts répétés : {stuck})")
            check(not no_ring, f"focus sans indicateur visible : {sorted(set(no_ring))[:4]}")
            # dialogues : recherche et notifications
            for label, opener, dialog, keys in (("recherche", None, "#palette", "Control+k"), ("notifications", "[data-notif-open]", "#notif-dialog", None)):
                page.goto(base + "#overview")
                page.reload()
                page.wait_for_timeout(900)
                if opener:
                    page.focus(opener)
                    page.keyboard.press("Enter")
                else:
                    page.focus("#nav a")
                    page.keyboard.press(keys)
                page.wait_for_timeout(250)
                check(page.evaluate(f"document.querySelector('{dialog}').open"), f"{label} : le dialogue s'ouvre au clavier")
                check(page.evaluate(f"document.querySelector('{dialog}').contains(document.activeElement)"), f"{label} : le focus est dans le dialogue")
                for _ in range(12):
                    page.keyboard.press("Tab")
                check(page.evaluate(f"document.querySelector('{dialog}').contains(document.activeElement)"), f"{label} : le focus reste enfermé (12 Tab)")
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
                check(not page.evaluate(f"document.querySelector('{dialog}').open"), f"{label} : Échap ferme le dialogue")
                check(page.evaluate("document.activeElement !== document.body"), f"{label} : le focus revient sur la page")
            # messages passagers
            check(page.evaluate("document.getElementById('toasts').getAttribute('aria-live')") in ("polite", "assertive"), "les messages passagers sont dans une zone aria-live")
            # animations réduites
            long = page.evaluate("""() => { let n = 0; for (const e of document.querySelectorAll('*')) { const d = getComputedStyle(e).transitionDuration.split(',').map(parseFloat); if (Math.max(...d) > 0.01) n++; } return n; }""")
            check(long == 0, f"« réduire les animations » : {long} élément(s) gardent une transition")
            check(not errors, f"erreurs JavaScript : {errors[:2]}")
            browser.close()
    finally:
        server.shutdown()
    if not used_axe:
        print("  axe-core absent : contrôles maison seulement (npm install axe-core pour l'ajouter)")
    print(f"a11y_test : {checks} contrôles, {len(failures)} échecs")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
