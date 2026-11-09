# -*- coding: utf-8 -*-
"""La marca Tenca (hoja de marca 01b, 09/11/2026): tipografías locales con su licencia, el icono, el logotipo, la
paleta con contraste AA y el tributo a la tenca en Acerca de.

- Las fuentes viajan en la app (funciona sin conexión): se sirven como font/woff2, entran en el zip y el desinstalador
  las borra; la licencia SIL OFL 1.1 va con ellas (fuentes-OFL.txt).
- El contraste se mide sobre los tokens del propio app.css, en claro y en oscuro: si alguien cambia un color, el test
  dice qué par deja de llegar a AA.
"""
import os
import re
import struct
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from unittest import mock

from support import APP, REPO, server

STATIC = os.path.join(APP, "static")
FUENTES = ("manrope.woff2", "fraunces.woff2")
LICENCIA = "fuentes-OFL.txt"


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8-sig") as f:
        return f.read()


class FuentesTests(unittest.TestCase):
    def test_las_fuentes_estan_y_son_woff2(self):
        for n in FUENTES:
            with open(os.path.join(STATIC, n), "rb") as f:
                self.assertEqual(f.read(4), b"wOF2", n)

    def test_el_css_las_carga_en_local(self):
        """@font-face con rutas relativas (las sirve la propia app: nada de Google Fonts ni de otra web) y la
        interfaz en Manrope y los títulos en Fraunces, con las del sistema detrás."""
        css = read(STATIC, "app.css")
        for fam, n in (("Manrope", "manrope.woff2"), ("Fraunces", "fraunces.woff2")):
            rx = rf'@font-face \{{ font-family: "{fam}"; src: url\("{re.escape(n)}"\) format\("woff2"\)'
            self.assertRegex(css, rx)
        self.assertNotRegex(css, r"url\(\s*['\"]?(https?:)?//")
        self.assertRegex(css, r'--font: "Manrope", "Segoe UI Variable Text", "Segoe UI"')
        self.assertRegex(css, r'--font-display: "Fraunces", Georgia')
        html = read(STATIC, "index.html")
        for n in FUENTES:
            self.assertIn(f'<link rel="preload" href="/{n}" as="font" type="font/woff2" crossorigin>', html)

    def test_licencia_ofl_con_los_avisos(self):
        """OFL 1.1: cada copia lleva el aviso de copyright y la licencia (también la versión recortada de Fraunces)."""
        txt = read(STATIC, LICENCIA)
        for s in ("SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007",
                  "Copyright 2018 The Fraunces Project Authors (https://github.com/undercasetype/Fraunces)",
                  "Copyright 2018 The Manrope Project Authors (https://github.com/googlefonts/manrope)",
                  "fraunces.woff2", "manrope.woff2", "design/fuentes.py"):
            self.assertIn(s, txt)

    def test_fraunces_lleva_todos_sus_caracteres(self):
        """Fraunces sale en títulos y en el nombre de la reunión: rumano, alemán y vietnamita, sin letras de otra
        fuente."""
        with open(os.path.join(STATIC, "fraunces.woff2"), "rb") as f:
            data = f.read()
        try:
            from fontTools.ttLib import TTFont
        except ImportError:  # la CI no instala fontTools: basta con que no sea el recorte a latín (~77 KB)
            self.assertGreater(len(data), 90_000)
            return
        import io
        cmap = TTFont(io.BytesIO(data)).getBestCmap()
        for ch in "șțẞệơ«»€":
            self.assertIn(ord(ch), cmap, ch)

    def test_se_sirven_como_font_woff2(self):
        self.assertEqual(server.MIME[".woff2"], "font/woff2")
        # la última línea del diccionario no cambia: scripts/lookfeel_spike.py la busca para añadir .webmanifest
        self.assertIn('".png": "image/png", ".ico": "image/x-icon"}', read(APP, "server.py"))
        srv = server.Server(("127.0.0.1", 0), server.Handler)
        port = srv.server_address[1]
        saved = (server.PORT, server.URL, server.Handler.app, getattr(server.Handler, "token", None))
        server.PORT, server.URL, server.Handler.app, server.Handler.token = port, f"http://127.0.0.1:{port}/", \
            mock.Mock(), "token-de-prueba-" + "x" * 27
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for n in FUENTES:
                with opener.open(f"http://127.0.0.1:{port}/{n}", timeout=5) as r:
                    self.assertEqual((r.status, r.headers["Content-Type"]), (200, "font/woff2"), n)
                    self.assertEqual(r.read(4), b"wOF2")
        finally:
            srv.shutdown()
            srv.server_close()
            server.PORT, server.URL, server.Handler.app, server.Handler.token = saved

    def test_van_en_el_zip(self):
        tmp = tempfile.mkdtemp(prefix="marca-zip-")
        dest = os.path.join(tmp, "app.zip")
        server.build_package(dest)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            lic = z.read(f"{server.PKG_ROOT}/_app/static/{LICENCIA}").decode("utf-8-sig")
        for n in FUENTES + (LICENCIA, "logo.svg", "chuleta.ico", "chuleta.png"):
            self.assertIn(f"{server.PKG_ROOT}/_app/static/{n}", names)
        self.assertIn("SIL OPEN FONT LICENSE", lic)

    def test_el_desinstalador_las_borra(self):
        """Solo borra lo suyo, por extensión y carpeta: sin «.woff2» las fuentes se quedarían como ajenas y _app no se
        borraría (el caso de verdad lo prueba test_desinstalar en Windows)."""
        ps1 = read(APP, "desinstalar.ps1")
        static = re.search(r'"static"\s*=\s*@\(([^)]*)\)', ps1).group(1)
        self.assertIn('".woff2"', static)
        txt = re.search(r"\$CodeTxt = @\(([^)]*)\)", ps1).group(1)
        self.assertIn('"static\\fuentes-OFL.txt"', txt)


def ico_entries(path):
    """[(lado, bpp, 'bmp'|'png', offset)] de la cabecera de un .ico."""
    with open(path, "rb") as f:
        b = f.read()
    n = struct.unpack("<H", b[4:6])[0]
    out = []
    for i in range(n):
        w, h, cc, r, planes, bpp, size, off = struct.unpack("<BBBBHHII", b[6 + 16 * i:6 + 16 * i + 16])
        out.append((w or 256, bpp, "png" if b[off:off + 8] == b"\x89PNG\r\n\x1a\n" else "bmp", off))
    return b, out


class IconoTests(unittest.TestCase):
    def test_nueve_tamanos_en_bmp(self):
        _, e = ico_entries(os.path.join(STATIC, "chuleta.ico"))
        self.assertEqual(sorted(x[0] for x in e), [16, 20, 24, 32, 40, 48, 64, 128, 256])
        self.assertTrue(all(x[1] == 32 and x[2] == "bmp" for x in e), e)

    def test_es_el_azulejo_boldo(self):
        """El píxel de la izquierda a media altura (fondo del azulejo, fuera de los renglones) es Boldo #1E5A46 en los
        tamaños de la barra de tareas y la bandeja."""
        b, e = ico_entries(os.path.join(STATIC, "chuleta.ico"))
        for side, _, _, off in e:
            if side not in (16, 24, 32, 48):
                continue
            hdr = struct.unpack("<IiiHH", b[off:off + 16])
            self.assertEqual(hdr[0], 40)  # BITMAPINFOHEADER; filas de abajo arriba, BGRA
            x, y = max(1, side // 16), side // 2 - 1
            row = side - 1 - y
            px = off + 40 + (row * side + x) * 4
            bl, g, r, a = b[px:px + 4]
            self.assertEqual((r, g, bl, a), (0x1E, 0x5A, 0x46, 255), side)

    def test_el_logo_de_la_cabecera_sigue_al_modo_oscuro(self):
        """logo.svg es la tenca sola (sin azulejo) y cambia de color con el modo del sistema; el maestro del icono es
        design/icono.svg."""
        svg = read(STATIC, "logo.svg")
        self.assertIn("@media (prefers-color-scheme:dark)", svg)
        self.assertIn("#18211D", svg)
        self.assertNotIn("<rect width=\"64\"", svg)
        src = read(REPO, "make_icons.py")
        self.assertIn('os.path.join(ROOT, "design", "icono.svg")', src)
        for n in ("icono.svg", "logo-small.svg", "icono-20.svg", "icono-24.svg", "icono-32.svg"):
            self.assertIn("#1E5A46", read(REPO, "design", n), n)


def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = (lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def mix(a, b, p):
    """color-mix(in srgb, a p, b)."""
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * p + int(b[i:i + 2], 16) * (1 - p)):02x}" for i in (1, 3, 5))


def lab(h):
    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    r, g, b = ((c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in
               (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    x, y, z = ((r * .4124 + g * .3576 + b * .1805) / .95047, r * .2126 + g * .7152 + b * .0722,
               (r * .0193 + g * .1192 + b * .9505) / 1.08883)
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(a, b):
    return sum((p - q) ** 2 for p, q in zip(lab(a), lab(b))) ** 0.5


def tokens(block):
    return dict(re.findall(r"--([a-z0-9-]+): (#[0-9a-f]{6})", block))


class ContrasteTests(unittest.TestCase):
    """WCAG 2.2 AA: texto ≥ 4,5:1 sobre todos los fondos en los que sale; iconos y detalles ≥ 3:1."""
    TEXTO = [(fg, bg) for fg in ("ink", "ink-2", "muted") for bg in ("bg", "panel", "panel-2", "hover", "accent-soft")]
    TEXTO += [("accent", bg) for bg in ("bg", "panel", "panel-2", "hover", "accent-soft")]
    TEXTO += [("accent-ink", "accent"), ("accent-ink", "accent-strong"), ("ok", "panel"), ("ok", "ok-soft"),
              ("warn", "panel"), ("warn", "warn-soft"), ("err", "panel"), ("err", "err-soft"), ("err-ink", "err"),
              ("cloud", "panel"), ("cloud", "cloud-soft"), ("ink", "mark")]
    ICONOS = [("star", "panel"), ("star", "bg"), ("accent", "panel")]

    @classmethod
    def setUpClass(cls):
        css = read(STATIC, "app.css")
        claro = tokens(css[css.index(":root {"):css.index("@media (prefers-color-scheme: dark)")])
        i = css.index("@media (prefers-color-scheme: dark)")
        oscuro = dict(claro, **tokens(css[i:css.index("* { box-sizing")]))
        cls.modos = {"claro": claro, "oscuro": oscuro}
        cls.css = css

    def test_la_paleta_es_la_de_la_hoja_de_marca(self):
        claro, oscuro = self.modos["claro"], self.modos["oscuro"]
        self.assertEqual((claro["bg"], claro["ink"], claro["muted"], claro["accent"], claro["star"]),
                         ("#f4f1e8", "#18211d", "#56615a", "#1e5a46", "#9c5a08"))
        self.assertEqual((oscuro["bg"], oscuro["ink"], oscuro["muted"], oscuro["accent"], oscuro["star"]),
                         ("#101613", "#e8ede9", "#a2aca5", "#7cc7a6", "#e8a94a"))

    def test_texto_y_estados_aa(self):
        for modo, t in self.modos.items():
            for fg, bg in self.TEXTO:
                self.assertGreaterEqual(ratio(t[fg], t[bg]), 4.5, f"{modo}: {fg} sobre {bg}")
            for fg, bg in self.ICONOS:
                self.assertGreaterEqual(ratio(t[fg], t[bg]), 3.0, f"{modo}: {fg} sobre {bg}")

    def test_tu_y_la_persona_1_se_distinguen(self):
        """La etiqueta «tú» (--ok) y la persona 1 (Boldo) son las dos verdes: que se vean distintas (ΔE76 ≥ 35)."""
        for modo, t in self.modos.items():
            self.assertGreaterEqual(delta_e(t["ok"], t["accent"]), 35, modo)

    def test_etiquetas_de_voces_aa(self):
        """Cada persona tiene su color (c1 = Boldo) sobre un fondo del 9 % de ese color: el nombre se lee en AA."""
        rx = r"\.spk\.(c\d) \{ --spk: (#[0-9a-f]{6}); \}"
        i = self.css.index("@media (prefers-color-scheme: dark) {\n  .spk.c2")
        claro = dict(re.findall(rx, self.css[self.css.index(".spk.c1"):i]))
        oscuro = dict(re.findall(rx, self.css[i:self.css.index("}\n}", i) + 1]))
        self.assertEqual(set(claro), {"c2", "c3", "c4", "c5"})
        self.assertEqual(set(oscuro), {"c2", "c3", "c4", "c5"})
        for modo, cols in (("claro", claro), ("oscuro", oscuro)):
            panel = self.modos[modo]["panel"]
            for c, col in list(cols.items()) + [("c1", self.modos[modo]["accent"])]:
                self.assertGreaterEqual(ratio(col, mix(col, panel, .09)), 4.5, f"{modo}: voz {c}")


class TributoTests(unittest.TestCase):
    def test_solo_cuando_la_app_se_llama_tenca(self):
        """«El nombre viene de la tenca» solo es verdad con el renombrado: si esta parte saliera antes, no se dice."""
        app = read(STATIC, "app.js")
        self.assertIn("${ac.tenca && /^tenca$/i.test(APP_NAME) ? `<p class=\"ac-tenca\">", app)

    def test_enlace_oficial_a_la_tenca(self):
        """Acerca de enlaza el artículo del Museo Nacional de Historia Natural de Chile sobre la tenca (Mimus thenca)
        por su DOI (prefijo 10.54830, el del Museo en Crossref), que no cambia aunque cambie su web."""
        self.assertEqual(server.TENCA_URL, "https://doi.org/10.54830/bmnhn.v61.2012.161")


if __name__ == "__main__":
    unittest.main()
