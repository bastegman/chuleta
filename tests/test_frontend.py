# -*- coding: utf-8 -*-
"""Comprobaciones de la interfaz (app.js / notas.js / index.html). Las funciones puras de títulos se ejecutan
con node (si está instalado); lo demás son comprobaciones sobre el texto."""
import os
import re
import shutil
import subprocess
import unittest

from support import REPO

STATIC = os.path.join(REPO, "_app", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def js_defs(src, names):
    """Líneas `const X = …` o `function X(…) {…}` (de una línea o con llaves balanceadas) de app.js."""
    out = []
    for n in names:
        m = re.search(rf"^(const {n} = .*|function {n}\(.*)$", src, re.M)
        assert m, n
        if m.group(0).startswith("function") and not m.group(0).rstrip().endswith("}"):
            start = m.start()
            end = src.index("\n}\n", start) + 3
            out.append(src[start:end])
        else:
            out.append(m.group(0))
    return "\n".join(out)


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class TitulosTests(unittest.TestCase):
    def run_js(self, code):
        defs = js_defs(read("app.js"), ["nfc", "pad", "MES", "DIA", "aDate", "shortTitle", "longTitle", "fullTitle"])
        defs += "\n" + js_defs(read("notas.js"), ["titleOf"])
        r = subprocess.run(["node", "-e", defs + "\n" + code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_full_title_con_titulo(self):
        out = self.run_js("const a = {date: '2026-10-30T15:03:00', props: {titulo: 'Kick-off'}, label: 'x', stem: 's'};"
                          "console.log(fullTitle(a))")
        self.assertEqual(out, "Kick-off · 30 oct · 15:03")

    def test_full_title_sin_titulo(self):
        out = self.run_js("const a = {date: '2026-10-30T15:03:00', props: {}, label: 'Kick-off', stem: 's'};"
                          "console.log(fullTitle(a))")
        self.assertEqual(out, "Viernes 30 oct 2026 · 15:03 · Kick-off")
        out = self.run_js("console.log(fullTitle({date: null, props: {}, label: '', stem: 'suelto'}))")
        self.assertEqual(out, "suelto")


class AtajosTests(unittest.TestCase):
    def test_ctrl_k_busca_y_ctrl_p_abre_la_paleta(self):
        app = read("app.js")
        html = read("index.html")
        self.assertIn("<kbd>Ctrl K</kbd>", html)  # junto a la caja de búsqueda
        self.assertIn("<kbd>Ctrl P</kbd>", html)  # junto a «Buscar» (paleta)
        self.assertRegex(app, r"k === 'k'[^\n]*\$\('#gq'\)")  # Ctrl+K enfoca la caja de búsqueda
        self.assertRegex(app, r"k === 'p'[^\n]*openPalette\(\)")  # Ctrl+P abre la paleta
        self.assertNotRegex(app, r"\(k === 'k' \|\| k === 'p'\)")

    def test_nombre_del_proveedor_en_vez_de_groq_fijo(self):
        notas = read("notas.js")
        self.assertNotIn("Hecho con IA en Groq", notas)
        self.assertIn("provName()", notas)
        self.assertIn("const provName = ", read("app.js"))


if __name__ == "__main__":
    unittest.main()
