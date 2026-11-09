# -*- coding: utf-8 -*-
"""
Genera las dos tipografías de la app (licencia SIL OFL 1.1) a partir de los ficheros de github.com/google/fonts:

    ofl/fraunces/Fraunces[SOFT,WONK,opsz,wght].ttf
    ofl/manrope/Manrope[wght].ttf

(los sha256 de los ficheros usados están en SHA, abajo; si no coinciden, avisa)

y escribe _app/static/fraunces.woff2 y _app/static/manrope.woff2. El aviso de copyright y la licencia van en
_app/static/fuentes-OFL.txt (y en la tabla «name» de cada fuente, que no se toca).

- Fraunces (títulos y marca): SOFT y WONK fijos en 0, como en la hoja de marca; tamaño óptico (opsz) entero, para que
  el navegador lo ajuste solo; peso de 400 a 800; solo latín (básico, Latin-1, Latin Extended-A y puntuación).
- Manrope (interfaz y texto): entera, solo pasada a WOFF2.

Lo que no tengan (símbolos, emojis, otros alfabetos) sale de las fuentes del sistema, como antes. Herramienta de
desarrollo: no viaja en el zip. Necesita fontTools y brotli (pip install fonttools brotli).

    R="repos/google/fonts/contents/ofl"; A="Accept: application/vnd.github.raw"
    gh api -H "$A" "$R/fraunces/Fraunces[SOFT,WONK,opsz,wght].ttf" > fraunces.ttf
    gh api -H "$A" "$R/manrope/Manrope[wght].ttf" > manrope.ttf
    python design/fuentes.py fraunces.ttf manrope.ttf
"""
import hashlib
import io
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "_app", "static")
SHA = {"fraunces": "177ff6c0f14e5550a3c624247cd1189611d4eb65d000b14944c63d967958abbb",
       "manrope": "3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6"}
LATIN = ("U+0000-00FF,U+0100-017F,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+20AC,U+2122,"
         "U+2190-2199,U+2212,U+2215,U+FEFF,U+FFFD")


def woff2(font, out, unicodes=None):
    """Recorta `font` (TTFont) a `unicodes` (None: todo lo que tenga) y la guarda en WOFF2, con la tabla name entera
    (copyright, licencia y su URL se quedan dentro)."""
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.recalc_timestamp = False  # misma entrada, mismo fichero (head.modified sale del original)
    s = subset.Subsetter(opts)
    s.populate(unicodes=subset.parse_unicodes(unicodes) if unicodes else font.getBestCmap().keys())
    s.subset(font)
    subset.save_font(font, out, opts)
    print(f"{out}: {os.path.getsize(out)} bytes, {len(TTFont(out).getGlyphOrder())} glifos")


def comprobar(path, nombre):
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    if sha != SHA[nombre]:
        print(f"Aviso: {path} no es el fichero de origen documentado ({sha[:12]}…): revisa fuentes-OFL.txt")


def main(fraunces, manrope, out_dir=STATIC):
    comprobar(fraunces, "fraunces")
    comprobar(manrope, "manrope")
    fr = instancer.instantiateVariableFont(TTFont(fraunces, recalcTimestamp=False),
                                           {"SOFT": 0, "WONK": 0, "wght": (400, 800)}, updateFontNames=False)
    buf = io.BytesIO()  # recortar la instancia sin pasar por disco falla (gvar perezoso de fontTools): se relee
    fr.save(buf)  # (TTFont.save no toca head.modified si la fuente se abrió con recalcTimestamp=False)
    buf.seek(0)
    woff2(TTFont(buf, recalcTimestamp=False), os.path.join(out_dir, "fraunces.woff2"), LATIN)
    woff2(TTFont(manrope, recalcTimestamp=False), os.path.join(out_dir, "manrope.woff2"))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
