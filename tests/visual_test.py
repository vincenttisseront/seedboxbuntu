#!/usr/bin/env python3
"""Garde-fous visuels de la console : chaque page est ouverte dans un vrai navigateur (Playwright) avec de
fausses données (tests/ui_mock.py), en clair, en sombre et sur téléphone.

Contrôles, pour chaque page et chaque variante :
  - aucune erreur JavaScript ni débordement horizontal ;
  - aucun tableau resté en « chargement » ;
  - tous les boutons utilisent les classes autorisées (btn, tab, seg, icon-btn, tabbar-item) ;
  - contraste AA (4,5 : 1) mesuré sur les couleurs réellement affichées, en clair, sombre et contraste élevé ;
  - la capture est comparée à la référence de tests/visual/baseline/ (écart toléré : 1,5 % des pixels).
Parcours : notification, tiroir, confirmation, réglages d'affichage (mémorisés après rechargement), barre mobile.

    python3 tests/visual_test.py            vérifie
    python3 tests/visual_test.py --update   remplace les références (après un changement voulu du rendu)
    python3 tests/visual_test.py --only guests
VISUAL_COMPARE=off : ne compare pas les captures (les autres contrôles restent actifs).
Sans Playwright ou Pillow installés, le test est ignoré (code 0) : pip install playwright pillow.
"""
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageChops
except ImportError as error:
    print(f"visual_test : ignoré ({error.name} absent)")
    sys.exit(0)
import ui_mock

WEB = ROOT / "rootfs/usr/local/lib/seedboxbuntu/console/web"
BASELINE = ROOT / "tests/visual/baseline"
CURRENT = ROOT / "tests/visual/current"
TOLERANCE = 0.015
VARIANTS = (("d", "dark", 1180), ("l", "light", 1180), ("m", "dark", 390))
BUTTON_OK = {"btn", "tab", "seg", "icon-btn", "tabbar-item"}
UPDATE = "--update" in sys.argv
COMPARE = os.environ.get("VISUAL_COMPARE") != "off"   # CI : les captures dépendent des polices et du moteur du poste
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
failures = []


def fail(text):
    failures.append(text)
    print("  ÉCHEC", text)


def views():
    ui = (WEB / "ui.js").read_text()
    return re.findall(r"view: '([a-z]+)'", ui)


def settle(page, view):
    page.wait_for_function(f"!document.getElementById('view-{view}').hasAttribute('aria-busy')", timeout=8000)
    page.wait_for_timeout(350)


def compare(name, path):
    if not COMPARE and not UPDATE:
        return
    ref = BASELINE / name
    if UPDATE or not ref.exists():
        if not UPDATE:
            fail(f"{name} : pas de référence (lancez avec --update)")
            return
        BASELINE.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, ref)
        return
    a, b = Image.open(ref).convert("RGB"), Image.open(path).convert("RGB")
    if a.size != b.size:
        fail(f"{name} : taille différente ({a.size} -> {b.size})")
        return
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 40 else 0)
    ratio = diff.histogram()[255] / (a.size[0] * a.size[1])
    if ratio > TOLERANCE:
        fail(f"{name} : {ratio:.1%} des pixels ont changé (tolérance {TOLERANCE:.1%}) ; voir tests/visual/current/{name}")


def shrink(path):
    """Références légères : un tiers de la taille, 32 couleurs."""
    image = Image.open(path).convert("RGB")
    image = image.resize((max(1, image.width // 3), max(1, image.height // 3)), Image.LANCZOS)
    image.quantize(32).convert("RGB").save(path, optimize=True)


def check_page(page, view, tag, errors):
    over = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    if over:
        fail(f"{tag}-{view} : débordement horizontal")
    if page.locator(f"#view-{view} .skeleton-row").count():
        fail(f"{tag}-{view} : lignes de chargement restantes")
    bad = page.evaluate("""(allowed) => [...document.querySelectorAll('button, a.btn')]
        .filter((b) => ![...b.classList].some((c) => allowed.includes(c))).map((b) => b.id || b.textContent.trim().slice(0, 30))""", sorted(BUTTON_OK))
    if bad:
        fail(f"{tag}-{view} : boutons hors classes autorisées : {bad}")
    if errors:
        fail(f"{tag}-{view} : erreurs JavaScript : {errors[:3]}")
        errors.clear()


def check_extra(page, tag, errors):
    """Pages hors console (invitation, erreur Traefik) : mêmes jetons, donc mêmes couleurs que la console."""
    if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"):
        fail(f"{tag} : débordement horizontal")
    back = page.evaluate("getComputedStyle(document.body).backgroundColor")
    if back not in ("rgb(2, 6, 23)", "rgb(241, 245, 249)"):
        fail(f"{tag} : fond {back} différent de celui de la console")
    if errors:
        fail(f"{tag} : erreurs JavaScript : {errors[:3]}")
        errors.clear()


def contrast_modes(page):
    """Mesure le contraste réel dans chaque combinaison de thème et de contraste."""
    for scheme in ("dark", "light"):
        for contrast in ("normal", "high"):
            page.evaluate("([s, c]) => Display.set({ theme: s, contrast: c })", [scheme, contrast])
            rows = page.evaluate("contrastReport()")
            for row in rows:
                if not row["ok"]:
                    fail(f"contraste {scheme}/{contrast} : {row['fg']} sur {row['bg']} = {row['ratio']:.2f}")
    page.evaluate("Display.reset()")


def journeys(page, base):
    page.goto(base + "#styleguide")
    settle(page, "styleguide")
    page.click("#sg-toast")
    if not page.locator("#toasts .toast.ok").count():
        fail("parcours : la notification n'apparaît pas")
    page.click("#sg-drawer")
    if page.locator("#drawer").evaluate("e => e.classList.contains('hidden')"):
        fail("parcours : le tiroir ne s'ouvre pas")
    page.keyboard.press("Escape")
    if not page.locator("#drawer").evaluate("e => e.classList.contains('hidden')"):
        fail("parcours : Échap ne ferme pas le tiroir")
    page.click("#sg-confirm")
    if not page.locator("#dialog[open]").count():
        fail("parcours : la confirmation ne s'ouvre pas")
    page.click("#dialog-no")
    if page.locator("#dialog[open]").count():
        fail("parcours : Annuler ne ferme pas la confirmation")
    # réglages d'affichage mémorisés
    page.click("#sg-display")
    page.click(".display-controls .seg:has-text('Clair')")
    page.click(".display-controls .seg:has-text('Compacte')")
    page.click(".display-controls .seg:has-text('Élevé')")
    page.click("#display-close")
    page.reload()
    page.wait_for_timeout(400)
    got = page.evaluate("[document.documentElement.dataset.scheme, document.documentElement.dataset.density, document.documentElement.dataset.contrast]")
    if got != ["light", "compact", "high"]:
        fail(f"parcours : réglages d'affichage non mémorisés ({got})")
    page.evaluate("Display.reset()")


def journeys_extra(page, base):
    """Recherche Ctrl+K, notifications, personnalisation de la vue d'ensemble avec annulation, mode kiosque, anglais."""
    page.goto(base + "#overview")
    settle(page, "overview")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette[open]")
    page.fill("#palette-input", "sauveg")
    if not page.locator("#palette-list [role=option]").count():
        fail("parcours : la recherche ne propose rien pour « sauveg »")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    if "backup" not in page.url or page.locator("#palette[open]").count():
        fail(f"parcours : la recherche n'ouvre pas la page Sauvegardes ({page.url})")
    page.goto(base + "#overview")
    settle(page, "overview")
    page.locator("[data-notif-open]:visible").first.click()
    page.wait_for_selector("#notif-dialog[open]")
    if not page.locator("#notif-list li").count():
        fail("parcours : le centre de notifications est vide")
    page.keyboard.press("Escape")
    # personnalisation : masquer une tuile, puis défaire
    page.click("#overview-customize")
    page.click("[data-tile-act=hide-backup]")
    if not page.evaluate("document.getElementById('tile-backup').hasAttribute('data-hidden')"):
        fail("parcours : la tuile Sauvegardes n'est pas marquée masquée après « masquer »")
    page.click("#toasts .toast button")
    page.wait_for_timeout(300)
    if page.evaluate("document.getElementById('tile-backup').hasAttribute('data-hidden')"):
        fail("parcours : « Défaire » ne réaffiche pas la tuile")
    page.click("#overview-customize")
    # kiosque
    page.click("#overview-kiosk")
    try:
        page.wait_for_function("document.body.classList.contains('kiosk')", timeout=3000)
        if page.locator("#kiosk-bar:visible").count() != 1:
            fail("parcours : la barre du mode kiosque n'est pas affichée")
        page.click("#kiosk-exit")
        page.wait_for_function("!document.body.classList.contains('kiosk')", timeout=3000)
    except Exception:  # noqa: BLE001
        fail("parcours : le mode kiosque ne s'active ou ne se quitte pas")
    # anglais, puis retour
    page.evaluate("Display.set({ lang: 'en' })")
    page.reload()                                                    # la langue s'applique au chargement de la page
    settle(page, "overview")
    if page.evaluate("document.querySelector('h1').textContent.trim()") != "Overview":
        fail("parcours : la page ne passe pas en anglais")
    page.evaluate("Display.reset()")
    page.reload()


def journeys_ops(page, base):
    """Vérification d'une sauvegarde, import de réglages, fenêtre de maintenance, vacances, diagnostic réseau, push."""
    page.goto(base + "#backup")
    settle(page, "backup")
    page.click("#sandbox-run")
    try:
        page.wait_for_selector("#sandbox-result:not(.hidden) .notice.err", timeout=4000)
    except Exception:  # noqa: BLE001
        fail("parcours : la vérification d'une sauvegarde n'affiche pas son verdict")
    if not page.locator("#sandbox-result li").count():
        fail("parcours : la vérification n'énumère pas ses problèmes")
    page.set_input_files("#config-file", {"name": "reglages.json", "mimeType": "application/json", "buffer": b'{"format": "seedboxbuntu-console", "version": 1, "sections": {}}'})
    page.click("#config-plan")
    page.wait_for_selector("#config-plan-box:not(.hidden)")
    if page.locator("#config-plan-list tr").count() != 3 or page.locator("#config-plan-list input[type=checkbox]").count() != 1:
        fail("parcours : le plan d'import doit lister 3 sections dont une seule applicable")
    ui_mock.CALLS.clear()
    page.click("#config-apply")
    page.wait_for_timeout(400)
    sent = [body for path, body in ui_mock.CALLS if path.endswith("/api/config/import")]
    if not sent or sent[-1].get("apply") is not True or sent[-1].get("only") != ["alerts"]:
        fail(f"parcours : l'import n'envoie que les sections cochées ({sent})")
    page.goto(base + "#settings")
    settle(page, "settings")
    if page.locator("#window-days input").count() != 7 or not page.inner_text("#window-state").strip():
        fail("parcours : la fenêtre de maintenance n'affiche ni ses jours ni son état")
    ui_mock.CALLS.clear()
    page.click("#window-card button[type=submit]")
    page.wait_for_timeout(400)
    sent = [body for path, body in ui_mock.CALLS if path.endswith("/api/window/settings")]
    if not sent or sent[-1].get("days") != [0, 3] or sent[-1].get("start") != "03:00":
        fail(f"parcours : la fenêtre n'envoie pas ses réglages ({sent})")
    page.goto(base + "#vacation")
    settle(page, "vacation")
    page.fill("#vacation-form input[name=until]", "2026-12-20")
    ui_mock.CALLS.clear()
    page.click("#vacation-form button[type=submit]")
    page.wait_for_timeout(400)
    if not [b for path, b in ui_mock.CALLS if path.endswith("/api/vacation/start") and b.get("until") == "2026-12-20"]:
        fail("parcours : le mode vacances n'envoie pas sa date de reprise")
    page.goto(base + "#netcheck")
    settle(page, "netcheck")
    if page.locator("#netcheck-list li").count() < 4 or not page.locator("#netcheck-list .pill.err").count():
        fail("parcours : le diagnostic réseau n'affiche pas ses contrôles et son erreur")
    page.goto(base + "#alerts")
    settle(page, "alerts")
    if not page.inner_text("#push-state").strip() or page.locator("#msglang-select").input_value() != "fr":
        fail("parcours : la carte des notifications push ou la langue des messages est vide")
    page.goto(base + "#system")
    settle(page, "system")
    if page.locator("#resources-list tr").count() != 3 or not page.locator("#resources-list .pill.warn").count():
        fail("parcours : les ressources par conteneur n'affichent pas 3 lignes dont une dérive")


def journeys_ops2(page, base):
    """Santé des disques, débit, ratio, lots, comparaison, dépannage, accès de secours, liste des médias, télécommande."""
    page.goto(base + "#disks")
    settle(page, "disks")
    if page.locator("#disks-list tr").count() != 2 or not page.locator("#disks-list .pill.warn").count() or not page.locator("#disks-list svg.spark").count():
        fail("parcours : la santé des disques n'affiche pas 2 disques dont un à surveiller avec sa tendance")
    page.goto(base + "#bandwidth")
    settle(page, "bandwidth")
    if page.locator("#bandwidth-slots .slot").count() != 1 or not page.inner_text("#bandwidth-state").strip():
        fail("parcours : le débit n'affiche pas sa plage et son état")
    page.click("#bandwidth-add")
    ui_mock.CALLS.clear()
    page.click("#bandwidth-form button[type=submit]")
    page.wait_for_timeout(400)
    sent = [b for path, b in ui_mock.CALLS if path.endswith("/api/bandwidth/settings")]
    if not sent or len(sent[-1].get("slots", [])) != 2 or sent[-1]["slots"][0].get("days") != [0, 1, 2, 3, 4] or sent[-1]["slots"][0].get("up") != 1000:
        fail(f"parcours : le débit n'envoie pas ses plages ({sent})")
    page.goto(base + "#trackers")
    settle(page, "trackers")
    page.wait_for_timeout(300)
    if page.locator("#ratio-list tr").count() != 3 or not page.locator("#ratio-list .pill.err").count():
        fail("parcours : les objectifs de ratio n'affichent pas 3 trackers dont un sous l'objectif")
    page.goto(base + "#apps")
    settle(page, "apps")
    page.wait_for_timeout(300)
    if page.locator("#rollout-list tr").count() != 2 or "traefik" not in page.inner_text("#rollout-apart"):
        fail("parcours : la mise à jour par lots n'affiche pas son lot et les services à part")
    page.goto(base + "#backup")
    settle(page, "backup")
    page.wait_for_timeout(300)
    page.click("#backupdiff-run")
    page.wait_for_selector("#backupdiff-result:not(.hidden) details")
    if "[masqué:" not in page.inner_text("#backupdiff-result") and page.locator("#backupdiff-result details").first.evaluate("(d) => { d.open = true; return d.textContent; }").find("[masqué:") < 0:
        fail("parcours : la comparaison de sauvegardes n'affiche pas de valeur masquée")
    page.goto(base + "#diagnose")
    settle(page, "diagnose")
    page.click("#diagnose-run")
    page.wait_for_selector("#diagnose-result:not(.hidden)")
    if page.locator("#diagnose-steps li").count() != 2 or page.locator("#diagnose-actions li").count() != 2:
        fail("parcours : le dépannage n'affiche pas ses vérifications et ses actions")
    page.goto(base + "#users")
    settle(page, "users")
    page.wait_for_timeout(300)
    ui_mock.CALLS.clear()
    page.click("#emergency-init")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_selector("#emergency-code:not(.hidden)")
    if "ABCD-" not in page.inner_text("#emergency-code-text"):
        fail("parcours : le code de secours n'est pas affiché")
    page.goto(base + "#medialist")
    settle(page, "medialist")
    page.wait_for_timeout(300)
    page.click("#medialist-list button")
    page.wait_for_selector("#medialist-plan-card:not(.hidden)")
    if "12 à ajouter" not in page.inner_text("#medialist-plan") or page.locator("#medialist-apply").is_disabled():
        fail("parcours : la simulation du réimport n'affiche pas ce qui serait ajouté")
    page.goto(base + "#telecommande")
    settle(page, "telecommande")
    ui_mock.CALLS.clear()
    page.click("#remote-pause")
    page.wait_for_timeout(400)
    if not [1 for path, _ in ui_mock.CALLS if path.endswith("/api/remote-control/pause")]:
        fail("parcours : la télécommande n'envoie pas la pause générale")
    # --- dix nouveautés : journal, qualité, liens, partage d'un titre, secrets ---
    page.goto(base + "#changes")
    settle(page, "changes")
    page.wait_for_timeout(300)
    page.click("#changes-list tr:first-child button")
    page.wait_for_selector("#changes-detail-card:not(.hidden)")
    if "masqué" not in page.inner_text("#changes-detail") or "token" not in page.inner_text("#changes-detail"):
        fail("parcours : le détail d'un changement n'affiche pas les lignes modifiées (secrets masqués)")
    ui_mock.CALLS.clear()
    page.click("#changes-list tr:first-child button:has-text('Revenir')")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/changelog/undo") and body.get("id") == "e2"]:
        fail("parcours : « Revenir » n'envoie pas l'entrée du journal")
    page.goto(base + "#quality")
    settle(page, "quality")
    page.wait_for_timeout(300)
    if "Alien" not in page.inner_text("#quality-radarr-list") or "Fargo" not in page.inner_text("#quality-sonarr-list"):
        fail("parcours : la qualité des médias n'affiche pas les titres signalés")
    ui_mock.CALLS.clear()
    page.click("#quality-radarr-upgrade")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/mediaquality/upgrade") and body.get("app") == "radarr"]:
        fail("parcours : la recherche d'une meilleure version n'est pas envoyée")
    page.goto(base + "#netcheck")
    settle(page, "netcheck")
    page.click("#linkcheck-run")
    page.wait_for_selector("#linkcheck-list li strong")
    if "Unable to connect" not in page.inner_text("#linkcheck-list") or "À faire" not in page.inner_text("#linkcheck-list"):
        fail("parcours : le contrôle des liens n'affiche pas l'erreur et le conseil")
    page.goto(base + "#guests")
    settle(page, "guests")
    page.wait_for_timeout(300)
    page.fill("#titleshare-form [name=q]", "dune")
    page.click("#titleshare-search")
    page.wait_for_function("document.querySelectorAll('#titleshare-results option').length === 2")
    ui_mock.CALLS.clear()
    page.click("#titleshare-form button[type=submit]")
    page.wait_for_selector("#titleshare-link:not(.hidden)")
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/titleshare/create") and body.get("id") == 12 and body.get("kind") == "movie"] or "/invite/" not in page.inner_text("#titleshare-link-text"):
        fail("parcours : la création d'un partage n'envoie pas le titre choisi ou n'affiche pas le lien")
    if "Marie" not in page.inner_text("#peoplereport-list") and "marie" not in page.inner_text("#peoplereport-list"):
        fail("parcours : le bilan mensuel n'affiche pas les comptes")
    page.goto(base + "#secrets")
    settle(page, "secrets")
    page.wait_for_timeout(300)
    if page.locator("#rotation-list button:not([disabled])").count() != 3:
        fail("parcours : la rotation doit proposer 3 secrets renouvelables (le service arrêté est désactivé)")
    page.goto(base + "#backup")
    settle(page, "backup")
    page.wait_for_timeout(300)
    if "pas de réponse" not in page.inner_text("#restoredrill-list"):
        fail("parcours : la simulation de restauration n'affiche pas le détail de l'échec")
    page.goto(base + "#maison")
    settle(page, "maison")
    page.wait_for_timeout(300)
    if not page.evaluate("document.body.classList.contains('kiosk')") or page.locator("#maison-tiles .tile").count() != 6:
        fail("parcours : la page maison doit s'ouvrir en mode kiosque avec 6 tuiles")
    # --- bibliothèques par partage, remplacements, seeds, connecteur Ollama ---
    page.goto(base + "#library")
    settle(page, "library")
    page.wait_for_timeout(500)
    if "nas" not in page.inner_text("#library-volumes") or "Dune" not in page.inner_text("#multiver-list"):
        fail("parcours : la carte des bibliothèques n'affiche pas les volumes et les versions multiples")
    ui_mock.CALLS.clear()
    page.check('input[aria-label="Relier Dune"]')
    page.click("#copyaudit-relink")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/copyaudit/relink") and body.get("paths")]:
        fail("parcours : « Relier la sélection » n'envoie pas les copies cochées")
    if not page.is_disabled('input[aria-label="Relier Alien"]'):
        fail("parcours : une copie sur deux disques ne doit pas pouvoir être reliée")
    ui_mock.CALLS.clear()
    page.check('input[aria-label="Déplacer Metropolis"]')
    page.click("#relocate-move")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/relocate/move") and body.get("app") == "radarr" and body.get("ids") == [3] and body.get("to") == "nas"]:
        fail("parcours : le déplacement n'envoie pas le titre coché vers le volume d'arrivée")
    page.goto(base + "#upgrades")
    settle(page, "upgrades")
    page.wait_for_timeout(500)
    if "Alien" not in page.inner_text("#replacer-list") or not page.is_disabled('input[aria-label="Remplacer Heat"]'):
        fail("parcours : un remplacement hors budget doit être listé mais non cochable")
    if "Simulation" not in page.inner_text("#replacer-sim"):
        fail("parcours : la simulation d'espace n'est pas affichée")
    ui_mock.CALLS.clear()
    page.click("#replacer-apply")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/replacer/apply") and body.get("ids") == ["radarr:2"]]:
        fail("parcours : le remplacement n'envoie que les propositions cochées")
    ui_mock.CALLS.clear()
    page.check('input[aria-label="Alléger Gladiator"]')
    page.click("#mediaslim-add")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/mediaslim/add") and body.get("kind") == "recompress" and len(body.get("paths", [])) == 1]:
        fail("parcours : l'ajout à la file d'allègement n'envoie pas le fichier coché")
    page.goto(base + "#seeds")
    settle(page, "seeds")
    page.wait_for_timeout(500)
    trackers_text = page.inner_text("#seeds-trackers").lower()      # les titres de carte sont mis en capitales par la feuille de style
    if "prive.exemple" not in trackers_text or "public.exemple" not in trackers_text:
        fail("parcours : l'analyse des seeds n'affiche pas une carte par tracker")
    ui_mock.CALLS.clear()
    page.check('input[aria-label^="Réannoncer"]')
    page.click("#seeds-reannounce")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/seedopt/reannounce") and len(body.get("hashes", [])) == 1]:
        fail("parcours : la réannonce n'envoie pas le torrent coché")
    ui_mock.CALLS.clear()
    page.check('input[aria-label^="Retirer Sample.Torrent.5"]')
    page.click("#seeds-retire")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/seedopt/retire") and len(body.get("hashes", [])) == 1 and body.get("delete_files") is False]:
        fail("parcours : le retrait n'envoie pas le torrent coché sans suppression de fichiers par défaut")
    ui_mock.CALLS.clear()
    page.click("#seeds-ai")
    page.wait_for_timeout(500)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/llm/explain") and body.get("topic") == "seeds"]:
        fail("parcours : le bouton d'avis de l'IA n'envoie pas le sujet « seeds »")
    # --- entretien des médias, quotas des invités, ratios réels des trackers ---
    page.goto(base + "#care")
    settle(page, "care")
    page.wait_for_timeout(600)
    care = page.inner_text("#view-care").lower()
    for needle in ("alpha", "charlie", "delta", "echo", "foxtrot", "nas"):
        if needle not in care:
            fail(f"parcours : la page entretien n'affiche pas « {needle} »")
    ui_mock.CALLS.clear()
    page.check('input[aria-label="Ne plus suivre Alpha"]')
    page.click("#dormant-unmonitor")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/dormant/unmonitor") and body.get("app") == "radarr" and body.get("ids") == [1]]:
        fail("parcours : « Ne plus suivre » n'envoie pas le titre coché")
    ui_mock.CALLS.clear()
    page.check('input[aria-label="Renommer Echo"]')
    page.click("#renamer-apply")
    page.wait_for_selector("#dialog[open]")
    page.click("#dialog-yes")
    page.wait_for_timeout(400)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/renamer/apply") and body.get("app") == "sonarr" and body.get("ids") == [9]]:
        fail("parcours : le renommage n'envoie pas le titre coché")
    page.goto(base + "#guests")
    settle(page, "guests")
    page.wait_for_timeout(500)
    if "invite-b" not in page.inner_text("#guestquota-list") or "suspendue" not in page.inner_text("#guestquota-list"):
        fail("parcours : les quotas des invités ne montrent pas la lecture suspendue")
    ui_mock.CALLS.clear()
    page.click('#guestquota-list button:has-text("Rétablir")')
    page.wait_for_timeout(300)
    if not [1 for path, body in ui_mock.CALLS if path.endswith("/api/guestquota/resume") and body.get("login") == "invite-b"]:
        fail("parcours : « Rétablir » n'envoie pas le compte suspendu")
    page.goto(base + "#trackers")
    settle(page, "trackers")
    page.wait_for_timeout(500)
    stats = page.inner_text("#trackerstats-list")
    if "torr9" not in stats or "sous le ratio exigé" not in stats:
        fail("parcours : les ratios réels n'affichent pas le tracker en danger")
    page.goto(base + "#settings")
    settle(page, "settings")
    page.wait_for_timeout(400)
    ui_mock.CALLS.clear()
    page.click("#llm-test")
    page.wait_for_timeout(500)
    if not [1 for path, _ in ui_mock.CALLS if path.endswith("/api/llm/test")]:
        fail("parcours : « Tester » ne contacte pas le serveur Ollama")


def observer_run(browser):
    """Compte « observateur » : navigation réduite, aucun bouton d'action actif, aucune erreur."""
    server = ui_mock.serve(8774, background=True, role="observer")
    base = "http://127.0.0.1:8774/console/"
    try:
        context = browser.new_context(viewport={"width": 1180, "height": 900}, color_scheme="dark", locale="fr-FR", reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        allowed = ["overview", "maison", "apps", "system", "alerts", "report"]
        page.goto(base + "#overview")
        page.reload()
        settle(page, "overview")
        shown = page.evaluate("[...document.querySelectorAll('#nav a[data-view]')].map((a) => a.dataset.view)")
        if sorted(shown) != sorted(allowed):
            fail(f"observateur : navigation {shown}, attendu {allowed}")
        for view in allowed:
            page.goto(base + "#" + view)
            page.reload()
            settle(page, view)
            active = page.evaluate(f"""[...document.querySelectorAll('#view-{view} .actions button, #view-{view} form button[type=submit]')]
                .filter((b) => b.getBoundingClientRect().width > 0 && !b.disabled && !['overview-refresh', 'overview-customize', 'overview-kiosk'].includes(b.id)
                  && !/Actualiser|Rafraîchir|Voir/.test(b.textContent)).map((b) => b.id || b.textContent.trim().slice(0, 25))""")
            if active:
                fail(f"observateur : {view} garde des boutons d'action actifs : {active}")
            check_page(page, view, "obs", errors)
        page.goto(base + "#alerts")
        settle(page, "alerts")
        if not page.locator("#push-card").is_visible() or page.locator("#msglang-form").is_visible():
            fail("observateur : la carte push doit être visible et la langue des messages masquée")
        page.goto(base + "#system")
        settle(page, "system")
        if page.locator("#resources-card").is_visible():
            fail("observateur : les ressources par conteneur sont réservées aux administrateurs")
        page.goto(base + "#users")                                     # page interdite : retour à la vue d'ensemble, sans erreur
        page.wait_for_timeout(500)
        if page.evaluate("document.getElementById('view-users') && !document.getElementById('view-users').classList.contains('hidden')"):
            fail("observateur : la page Comptes reste accessible")
        context.close()
    finally:
        server.shutdown()


def journey_mobile(page, base):
    page.goto(base + "#overview")
    settle(page, "overview")
    page.click(".tabbar-item[data-group=services]")
    if page.locator("#sheet a").count() < 4:
        fail("parcours mobile : la feuille de navigation n'affiche pas les pages du groupe")
    page.click("#sheet a[data-view=catalog]")
    page.wait_for_timeout(300)
    if "catalog" not in page.url or page.locator("#sheet:not(.hidden)").count():
        fail("parcours mobile : la navigation ne ferme pas la feuille")


def main():
    shutil.rmtree(CURRENT, ignore_errors=True)
    CURRENT.mkdir(parents=True)
    server = ui_mock.serve(8767, background=True)
    base = "http://127.0.0.1:8767/console/"
    pages = [v for v in views() if not ONLY or v == ONLY]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for tag, scheme, width in VARIANTS:
                context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, locale="fr-FR",
                                              timezone_id="Europe/Paris", reduced_motion="reduce", device_scale_factor=1)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
                page.clock.set_fixed_time("2026-11-06T12:00:00+01:00")
                for view in pages:
                    print(f"{tag}-{view}")
                    page.goto(base + "#" + view)
                    page.reload()
                    settle(page, view)
                    check_page(page, view, tag, errors)
                    name = f"{tag}-{view}.png"
                    path = CURRENT / name
                    page.screenshot(path=path, full_page=True)
                    shrink(path)
                    compare(name, path)
                if not ONLY:
                    for extra, route in (("invite", "invite/TOKEN"), ("erreur", "errors/503"), ("moi", "me/")):
                        print(f"{tag}-{extra}")
                        page.goto(base.replace("console/", route))
                        page.wait_for_timeout(500)
                        page.wait_for_selector("#me-content:not(.hidden)" if extra == "moi" else "main.invite .card:not(.hidden)")
                        check_extra(page, f"{tag}-{extra}", errors)
                        path = CURRENT / f"{tag}-{extra}.png"
                        page.screenshot(path=path, full_page=True)
                        shrink(path)
                        compare(f"{tag}-{extra}.png", path)
                if tag == "d" and not ONLY:
                    page.goto(base + "#overview")
                    settle(page, "overview")
                    contrast_modes(page)
                    journeys(page, base)
                    journeys_extra(page, base)
                    journeys_ops(page, base)
                    journeys_ops2(page, base)
                if tag == "m" and not ONLY:
                    journey_mobile(page, base)
                context.close()
            if not ONLY:
                observer_run(browser)
            browser.close()
    finally:
        server.shutdown()
    print(f"visual_test : {len(failures)} échec(s)" if failures else "visual_test : tous les contrôles réussis")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
