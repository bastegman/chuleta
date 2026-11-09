# -*- coding: utf-8 -*-
"""
Genera el logotipo de Tenca (la tenca sobre tres renglones y la palabra «Tenca» en Fraunces), como en la hoja de marca
01b, en SVG con el texto convertido en trazados (no necesita la fuente instalada) y en PNG:

    design/tenca-logo.svg / .png            sobre fondo claro (grafito y pico ocre)
    design/tenca-logo-oscuro.svg / .png     sobre fondo oscuro (texto claro y pico ocre claro)

La palabra sale de _app/static/fraunces.woff2 (peso 620 y tamaño óptico 50, SOFT y WONK a 0) con su interletraje, y
el PNG, de Chrome o Edge headless, con fondo transparente. Herramienta de desarrollo: no viaja en el zip. Necesita
fontTools, brotli y uharfbuzz (pip install fonttools brotli uharfbuzz).

    python design/logotipo.py
"""
import io
import os
import subprocess
import sys
import tempfile

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = os.path.dirname(os.path.abspath(__file__))
FUENTE = os.path.join(os.path.dirname(HERE), "_app", "static", "fraunces.woff2")
sys.path.insert(0, os.path.dirname(HERE))
from make_icons import BROWSERS  # noqa: E402

TEXTO, PESO, OPSZ = "Tenca", 620, 50
S = 64                 # lado de la tenca (unidades del SVG = las de su viewBox)
T = S * 1.05           # cuerpo de la letra (hoja de marca: 1,05 × el símbolo)
HUECO = 18             # entre la tenca y la palabra
TRACK = -0.01 * T      # letter-spacing: -.01em
MODOS = {"tenca-logo": ("#18211D", "#9C5A08"), "tenca-logo-oscuro": ("#E8EDE9", "#E8A94A")}
AVE = ('<path fill="{c}" fill-rule="evenodd" d="M33 21a10 10 0 1 0 20 0a10 10 0 1 0-20 0ZM43.6 18.8a2 2 0 1 0 4 0'
       'a2 2 0 1 0-4 0Z"/><path fill="{p}" d="M51.6 16.6 62.5 21 51.6 25.4Z"/><rect fill="{c}" x="6" y="32" width="40"'
       ' height="7" rx="3.5"/><rect fill="{c}" x="6" y="43" width="29" height="7" rx="3.5"/><rect fill="{c}" x="6"'
       ' y="54" width="18" height="7" rx="3.5"/>')


def instancia():
    f = instancer.instantiateVariableFont(TTFont(FUENTE), {"wght": PESO, "opsz": OPSZ}, updateFontNames=False)
    buf = io.BytesIO()
    f.flavor = None
    f.save(buf)
    return buf.getvalue()


def palabra(data):
    """(trazado SVG, ancho) de TEXTO a cuerpo T, con la línea base centrada como en la hoja de marca (flex, center,
    line-height 1: la caja de la línea mide T y se centra con la tenca)."""
    font = TTFont(io.BytesIO(data))
    upm = font["head"].unitsPerEm
    asc, desc = font["hhea"].ascent, font["hhea"].descent
    k = T / upm
    base = S / 2 - T / 2 + (T - (asc - desc) * k) / 2 + asc * k
    buf = hb.Buffer()
    buf.add_str(TEXTO)
    buf.guess_segment_properties()
    hbf = hb.Font(hb.Face(data))
    hb.shape(hbf, buf, {"kern": True, "liga": True})
    glyphs, names = font.getGlyphSet(), font.getGlyphOrder()
    pen = SVGPathPen(glyphs)
    x = S + HUECO
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        m = (k, 0, 0, -k, x + pos.x_offset * k, base - pos.y_offset * k)  # unidades de la fuente → SVG, y hacia abajo
        glyphs[names[info.codepoint]].draw(TransformPen(pen, m))
        x += pos.x_advance * k + TRACK
    return pen.getCommands(), x - TRACK


def main():
    d, ancho = palabra(instancia())
    w, h = round(ancho + 2), S
    for nombre, (c, p) in MODOS.items():
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w * 2}" height="{h * 2}">'
               f'<title>Tenca</title>{AVE.format(c=c, p=p)}<path fill="{c}" d="{d}"/></svg>\n')
        ruta = os.path.join(HERE, nombre + ".svg")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(svg)
        png = os.path.join(HERE, nombre + ".png")
        work = tempfile.mkdtemp(prefix="logotipo-")
        html = os.path.join(work, "l.html")
        with open(html, "w", encoding="utf-8") as f:
            f.write(f'<!doctype html><body style="margin:0;background:transparent"><img src="file://{ruta}" '
                    f'width="{w * 4}" height="{h * 4}" style="display:block"></body>')
        subprocess.run(BROWSERS[:1] + ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                                       f"--user-data-dir={os.path.join(work, 'p')}",
                                       "--default-background-color=00000000",
                                       f"--window-size={w * 4},{h * 4 + 200}", f"--screenshot={png}", f"file://{html}"],
                       check=True, capture_output=True, timeout=60)
        from PIL import Image
        Image.open(png).convert("RGBA").crop((0, 0, w * 4, h * 4)).save(png, optimize=True)
        print(f"{ruta} ({w}×{h}) y {png} ({w * 4}×{h * 4})")


if __name__ == "__main__":
    main()
