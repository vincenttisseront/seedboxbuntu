"""Génère logo.svg (icône + nom, texte converti en tracés : aucune police requise à l'affichage)."""
import pathlib
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

HERE = pathlib.Path(__file__).resolve().parent
ICON = (HERE / "logo-icone.svg").read_text()
inner = ICON.split("</defs>")[1].split("</svg>")[0]
defs = ICON.split("<defs>")[1].split("</defs>")[0]


def outline(path, text, size, x, y, spacing=0.0):
    font = TTFont(path)
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    kern, cursor = [], x
    for char in text:
        name = cmap[ord(char)]
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, y)))
        kern.append(pen.getCommands())
        cursor += hmtx[name][0] * scale + spacing
    return " ".join(kern), cursor


p1, end = outline("/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf", "SeedBox", 150, 424, 212, -2)
p2, end = outline("/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf", "Buntu", 150, end, 212, -2)
p3, end3 = outline("/usr/share/fonts/opentype/inter/Inter-Medium.otf", "SERVARR · SÉCURISÉE · DOCKER", 40, 430, 280, 7)
width = int(max(end, end3) + 40)
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 384" role="img" aria-label="SeedBoxBuntu">
  <title>SeedBoxBuntu</title>
  <defs>{defs}</defs>
  <g transform="translate(16 16) scale(0.71875)">{inner}</g>
  <path d="{p1}" fill="#134e4a"/>
  <path d="{p2}" fill="#0d9488"/>
  <path d="{p3}" fill="#5d6b7a"/>
</svg>
'''
(HERE / "logo.svg").write_text(svg)
# version pour fond sombre
(HERE / "logo-sombre.svg").write_text(svg.replace('fill="#134e4a"', 'fill="#e7edf3"').replace('fill="#0d9488"', 'fill="#2dd4bf"').replace('fill="#5d6b7a"', 'fill="#9aa8b6"'))
print(width)
