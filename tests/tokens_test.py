"""Jetons de design : contrastes AA dans chaque thème, copies des couleurs (Homepage, méta theme-color)
et séries de graphiques distinctes. Pur Python : lit tokens.css, sans navigateur."""
import os
import re
import sys
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
WEB = SRC / "rootfs/usr/local/lib/seedboxbuntu/console/web"
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220])


def blocks(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        out.setdefault(selector.strip(), {}).update({k[2:]: v.strip() for k, v in re.findall(r"(--[\w-]+):([^;]+)", body)})
    return out


def color(value):
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    numbers = [float(x) for x in re.match(r"rgba?\(([^)]+)\)", value).group(1).split(",")]
    return (numbers[0], numbers[1], numbers[2], numbers[3] if len(numbers) > 3 else 1.0)


def over(front, back):
    a = front[3]
    return tuple(front[i] * a + back[i] * (1 - a) for i in range(3)) + (1.0,)


def luminance(c):
    def channel(x):
        x /= 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2])


def ratio(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


css = (WEB / "tokens.css").read_text()
b = blocks(css)
dark = dict(b[":root"])
light = {**dark, **b["html[data-scheme=light]"]}
dark_hc = {**dark, **b["html[data-contrast=high]"]}
light_hc = {**light, **b["html[data-contrast=high][data-scheme=light]"]}
MODES = {"sombre": dark, "clair": light, "sombre contraste élevé": dark_hc, "clair contraste élevé": light_hc}
# (texte, fond, teinte posée sur le fond, seuil)
PAIRS = [("text", "bg", None, 4.5), ("text", "panel", None, 4.5), ("text", "panel2", None, 4.5), ("muted", "bg", None, 4.5), ("muted", "panel", None, 4.5),
         ("muted", "panel2", None, 4.5), ("muted", "chip", None, 4.5), ("brand", "bg", None, 4.5), ("brand", "panel", None, 4.5),
         ("on-accent", "accent", None, 4.5), ("on-accent", "accent-hover", None, 4.5), ("focus", "bg", None, 3.0),
         ("ok", "panel", "ok-bg", 4.5), ("warn", "panel", "warn-bg", 4.5), ("err", "panel", "err-bg", 4.5), ("info", "panel", "info-bg", 4.5),
         ("unknown", "panel", "unknown-bg", 4.5), ("ok", "bg", None, 4.5), ("warn", "bg", None, 4.5), ("err", "bg", None, 4.5), ("info", "bg", None, 4.5),
         ("c0", "panel", None, 3.0), ("c1", "panel", None, 3.0), ("c2", "panel", None, 3.0), ("c3", "panel", None, 3.0)]
for name, tokens in MODES.items():
    failed = []
    for fg, bg, tint, need in PAIRS:
        back = color(tokens[bg])
        if tint:
            back = over(color(tokens[tint]), back)
        front = color(tokens[fg])
        if front[3] < 1:
            front = over(front, back)
        r = ratio(front, back)
        if r < need:
            failed.append(f"{fg}/{bg}{'+' + tint if tint else ''}={r:.2f}")
    check(f"contraste AA, thème {name} ({len(PAIRS)} paires)", not failed, failed)

for mode, tokens in MODES.items():
    series = [tokens[f"c{i}"] for i in range(4)]
    check(f"séries de graphiques distinctes, thème {mode}", len(set(series)) == 4)
    status = {tokens[k] for k in ("ok", "warn", "err", "info")}
    check(f"couleurs d'état toutes différentes, thème {mode}", len(status) == 4)

# Homepage reprend le thème sombre
home = blocks((SRC / "templates/config/homepage/custom.css").read_text())[":root"]
mismatch = [k for k, v in home.items() if k in dark and dark[k] != v]
check("Homepage : variables identiques à tokens.css (thème sombre)", not mismatch and len(set(home) & set(dark)) >= 10, mismatch)

# couleur de la barre du navigateur
display = (WEB / "display.js").read_text()
theme = dict(re.findall(r"(dark|light): '(#[0-9a-fA-F]{6})'", display[display.index("THEME_COLOR"):]))
check("display.js : theme-color = fond de chaque thème", theme.get("dark") == dark["bg"] and theme.get("light") == light["bg"], theme)
for page in ("index.html", "invite.html", "error.html"):
    meta = re.search(r'name="theme-color" content="(#[0-9a-fA-F]{6})"', (WEB / page).read_text())
    check(f"{page} : theme-color initial = fond sombre", meta and meta.group(1) == dark["bg"])
ui = (WEB / "ui.js").read_text()
warn = re.search(r"THRESHOLDS = \{ warn: (\d+), crit: (\d+) \}", ui)
flat = css.replace(" ", "")
check("seuils partagés : tokens.css = ui.js", bool(warn) and f"--th-warn:{warn.group(1)}" in flat and f"--th-crit:{warn.group(2)}" in flat)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
