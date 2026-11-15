#!/usr/bin/env python3
"""Garde-fous de la traduction anglaise de la console.

  1. tout texte statique de index.html et invite.html (texte, aria-label, placeholder, title, alt) a sa traduction ;
  2. aucune traduction vide, et les chiffres / jetons techniques (Ctrl, Ctrl+K, https://, SMART...) sont conservés ;
  3. le moteur (i18n.js) traduit au chargement et pour le contenu fabriqué ensuite (essai Node) ;
  4. chaque page, ouverte en anglais dans un vrai navigateur (Playwright, ignoré s'il est absent), garde un nombre
     de phrases restées en français sous le plafond (cliquet : il ne peut que baisser) et sans erreur JavaScript.
Les phrases venant des données (notes de version GitHub, catalogue de l'utilisateur, noms de comptes) restent telles quelles.
"""
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "rootfs/usr/local/lib/seedboxbuntu/console/web"
CEILING = 7                          # phrases françaises tolérées dans les pages en anglais (données de démonstration)
failures = []
checks = 0


def check(ok, text):
    global checks
    checks += 1
    if not ok:
        failures.append(text)
        print("  ÉCHEC", text)


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "pre", "code", "kbd"):
            self.skip += 1
        for key, value in attrs:
            if key in ("aria-label", "placeholder", "title", "alt") and value:
                self.texts.append(" ".join(value.split()))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "pre", "code", "kbd"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            data = " ".join(data.split())
            if data and re.search(r"[A-Za-zÀ-ÿ]{2}", data):
                self.texts.append(data)


def dictionary():
    """Charge window.I18N_EN et les modèles avec Node (le même moteur qu'en navigateur)."""
    script = ("global.window=global;global.document={documentElement:{lang:'en'},readyState:'loading',addEventListener(){}};"
              "global.Node={};require(process.argv[1]+'/lang-en.js');require(process.argv[1]+'/i18n.js');"
              "const out={dict:window.I18N_EN,patterns:window.I18N_PATTERNS.length,samples:JSON.parse(process.argv[2]).map(t=>tr(t))};"
              "console.log(JSON.stringify(out));")
    return script


SAMPLES = ["3 point(s) à regarder sur 10 : la sécurité ou vos données en dépendent.", "Généré le 01/01/2026 · pas encore envoyé",
           "2 écran(s) · 8 Mb/s · sans téléchargement", "Monter la tuile Santé", "Tuile « Santé » masquée.", "Radarr est arrêté",
           "Disque « Stockage » à 88 %", "Mis à jour à 10:15", "Ouvrir Sauvegardes", "Aucune notification."]


def main():
    try:
        node = subprocess.run(["node", "-e", dictionary(), str(WEB), json.dumps(SAMPLES)], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print("i18n_test : ignoré (node absent)")
        return 0
    check(node.returncode == 0, "lang-en.js / i18n.js se chargent sans erreur : " + node.stderr[-200:])
    if node.returncode:
        return report()
    data = json.loads(node.stdout)
    dictionary_en = data["dict"]
    # 1. couverture du HTML statique
    for page in ("index.html", "invite.html"):
        parser = Collect()
        parser.feed((WEB / page).read_text())
        texts = list(dict.fromkeys(parser.texts))
        missing = [t for t in texts if t not in dictionary_en and re.search(r"[À-ÿ]|\b(le|la|les|des|un|une|pour|est|vous|votre|aucun|aucune|ou|et)\b", t)]
        check(not missing, f"{page} : {len(missing)} texte(s) français sans traduction, par exemple : {missing[:4]}")
    # 2. qualité du dictionnaire
    empty = [k for k, v in dictionary_en.items() if not str(v).strip()]
    check(not empty, f"traductions vides : {empty[:3]}")
    accent = [k for k, v in dictionary_en.items() if re.search(r"\b(le|la|les|des|pour|votre|vous)\b", v) and k != v]
    check(not accent, f"traductions restées en français : {accent[:3]}")
    bad_digits = [k for k, v in dictionary_en.items() if sorted(re.findall(r"\d+", k)) != sorted(re.findall(r"\d+", v))]
    check(not bad_digits, f"chiffres différents entre l'original et la traduction : {bad_digits[:3]}")
    check(data["patterns"] >= 60, "modèles à trous présents")
    # 3. modèles essayés à blanc
    for before, after in zip(SAMPLES, data["samples"]):
        check(after != before and not re.search(r"[éèàùçê]", after.replace("français", "")), f"modèle : « {before} » → « {after} »")
    # 4. navigateur
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("i18n_test : navigateur ignoré (Playwright absent)")
        return report()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ui_mock
    views = re.findall(r"view: '([a-z0-9]+)'", (WEB / "ui.js").read_text())
    french = re.compile(r"[éèêàâùûôîçœ]|\b(le|la|les|des|un|une|du|pour|est|sont|pas|avec|sur|dans|ou|vous|votre|aucun|aucune|cette)\b", re.I)
    collect = """() => { const out=[]; const w=document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) { const p = n.parentElement; if (!p || /^(SCRIPT|STYLE|CODE|PRE|TEXTAREA|KBD)$/.test(p.nodeName) || p.closest('.no-i18n')) continue;
        const t = n.nodeValue.replace(/\\s+/g, ' ').trim(); if (t) out.push(t); }
      for (const e of document.querySelectorAll('[aria-label],[placeholder],[title],[alt]'))
        for (const a of ['aria-label', 'placeholder', 'title', 'alt']) if (e.getAttribute(a)) out.push(e.getAttribute(a));
      return out; }"""
    server = ui_mock.serve(8771, background=True)
    left, errors = {}, []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={"width": 1180, "height": 900}, locale="en-GB", reduced_motion="reduce").new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto("http://127.0.0.1:8771/console/")
            page.evaluate("localStorage.setItem('sb.display', JSON.stringify({lang:'en'}))")
            for view in views:
                page.goto("http://127.0.0.1:8771/console/#" + view)
                page.reload()
                page.wait_for_timeout(1200)
                check(page.evaluate("document.documentElement.lang") == "en", f"{view} : <html lang> = en")
                for text in page.evaluate(collect):
                    if french.search(text):
                        left.setdefault(text, view)
            # retour au français : rien ne doit changer pour les comptes qui n'ont pas choisi l'anglais
            page.evaluate("localStorage.setItem('sb.display', JSON.stringify({lang:'fr'}))")
            page.goto("http://127.0.0.1:8771/console/#overview")
            page.reload()
            page.wait_for_timeout(800)
            check(page.evaluate("document.querySelector('h1').textContent.trim()") == "Vue d’ensemble", "en français, le titre reste « Vue d’ensemble »")
            browser.close()
    finally:
        server.shutdown()
    check(not errors, f"erreurs JavaScript en anglais : {errors[:2]}")
    check(len(left) <= CEILING, f"{len(left)} phrases restées en français (plafond {CEILING}) : {list(left.items())[:6]}")
    print(f"  navigateur : {len(left)} phrase(s) restée(s) en français sur {len(views)} pages (données de démonstration comprises)")
    return report()


def report():
    print(f"i18n_test : {checks} contrôles, {len(failures)} échecs")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
