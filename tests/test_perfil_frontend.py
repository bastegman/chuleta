# -*- coding: utf-8 -*-
"""Interfaz del perfil de reunión (perfil.js): carga y ganchos en app.js / notas.js / reunion.js / index.html, textos
obligatorios, nada externo, y los helpers puros ejecutados con node (si está instalado)."""
import re
import shutil
import subprocess
import unittest

from test_frontend import js_defs, read

# ganchos de app.js y notas.js: cada uno una sola vez y protegido con typeof (la app funciona sin perfil.js)
# (07/11/2026: el diálogo Grabar es la tarjeta de grabar.js; sus ganchos de perfil.js se fueron con él)
# (07/11/2026, B2: el «Perfil de reunión» se reparte por secciones: motores, resumen y copias aquí; las reglas de
# grabar, en ajustes.js)
HOOKS_APP = ("perfilLiveHtml(a)", "perfilMotoresHtml()", "perfilResumenHtml()", "perfilSalidasHtml()",
             "bindPerfilSettings(d)",
             "collectPerfilSettings(d)", "perfilLangOf(list[0])", "perfilImportar('', openSettings)",
             "perfilVocabEmptyHtml()")
GUARDED = ("perfilLiveHtml", "perfilMotoresHtml", "perfilResumenHtml", "perfilSalidasHtml", "bindPerfilSettings",
           "collectPerfilSettings", "perfilLangOf",
           "perfilImportar", "perfilVocabEmptyHtml", "perfilPropsHtml", "perfilSavedText")


class PerfilCargaTests(unittest.TestCase):
    def test_index_carga_perfil_el_ultimo(self):
        html = read("index.html")
        self.assertIn('<script src="/perfil.js"></script>', html)
        for before in ("app.js", "notas.js", "reunion.js", "directo.js"):
            self.assertLess(html.index(f'<script src="/{before}">'), html.index('<script src="/perfil.js">'))

    def test_ganchos_en_app_js_y_notas_js(self):
        app, notas, src = read("app.js"), read("notas.js"), read("app.js") + read("notas.js")
        for h in HOOKS_APP:
            self.assertEqual(app.count(h), 1, h)
        self.assertEqual(notas.count("perfilPropsHtml(a)"), 1)
        self.assertEqual(notas.count("perfilSavedText('idioma'"), 1)
        self.assertEqual(notas.count("perfilSavedText('contexto'"), 1)
        for fn in GUARDED:
            self.assertRegex(src, rf"typeof {fn} === 'function'", fn)
        # el motor del directo, el idioma del resumen y las instrucciones extra viven en el perfil (con fallback)
        self.assertNotIn('<label>Motor para el directo</label>', app)
        self.assertIn("$('#lPreset', d)?.value", app)
        self.assertIn("$('#sSumLang', d)?.value", app)
        # mezclas nuevas en la lista de idiomas y el toast de Ctrl+Alt+R con «clic para ajustar»
        self.assertIn("['es-pt', 'Español + portugués (mezcla)'], ['en-pt', 'Inglés + portugués (mezcla)']", app)
        self.assertIn("clic para verla y cambiar el idioma o el contexto", app)
        # el idioma de la reunion no cambia el ajuste general al grabar
        self.assertNotIn("S.config.language = S.selLang = $('#rLang', d).value;", app)

    def test_textos_y_sin_nada_externo(self):
        js = read("perfil.js")
        for s in ("Sin proyecto", "Perfil de reunión", "Copias en otras carpetas", "Añadir carpeta…",
                  "subcarpeta por proyecto", "Markdown (.md)", "Texto (.txt)", "Word (.docx)", "/api/perfil",
                  "/api/vocab/import", "/api/link/pick", "Vocabulario\\\\&lt;Tema&gt;.txt",
                  "junto a <code>_app</code>", "Por defecto · ",
                  "Automática · la última usada", "Automático · con su motivo", "Contexto", "Reuniones sin proyecto",
                  "el directo lo usa desde el siguiente trozo", "${esc(APP_NAME)}"):
            self.assertIn(s, js, s)
        self.assertNotIn("fetch(", js)  # todo pasa por api() de app.js
        self.assertNotRegex(js, r"https?://")
        self.assertNotRegex(js, r"[\U0001F300-\U0001FAFF\u2600-\u26FF]")  # sin emojis: iconos I.*
        self.assertNotIn("Notas", js)  # el nombre de la app sale de APP_NAME
        for fn in ("perfilLangLabel", "perfilLangOptions", "perfilLangOf", "perfilGroups", "perfilImportar",
                   "perfilImportText", "perfilPropsHtml", "perfilLiveHtml", "perfilSavedText", "perfilReglasHtml",
                   "perfilMotoresHtml", "perfilResumenHtml", "perfilSalidasHtml",
                   "perfilOutsHtml", "bindPerfilOuts", "bindPerfilSettings", "collectPerfilSettings",
                   "perfilVocabEmptyHtml"):
            self.assertRegex(js, re.compile(rf"^(async )?function {fn}\(", re.M), fn)


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class PerfilHelpersTests(unittest.TestCase):
    def run_js(self, code):
        app = read("app.js")
        langs = re.search(r"^const LANGS = \[.*?\]\];$", app, re.S | re.M).group(0)
        defs = js_defs(app, ["esc", "nfc"]) + "\n" + langs + "\nconst S = {selLang: null, config: {language: 'pt'}};\n"
        defs += js_defs(read("perfil.js"), ["perfilLangLabel", "perfilLangOptions", "perfilLangOf", "perfilGroups",
                                            "perfilImportText", "perfilSavedText", "perfilOutsHtml", "PERFIL_FORMATS"])
        r = subprocess.run(["node", "-e", defs + "\n" + code], capture_output=True, text=True, encoding="utf-8",
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_listas_por_proyecto_e_idiomas(self):
        out = self.run_js("""
const vocab = [{id: 'A/Vocabulario/General.txt', project: 'A', name: 'General', desc: 'd'},
  {id: 'A/Vocabulario/Legal.txt', project: 'A', name: 'Legal'}, {id: 'B/Vocabulario/X.txt', project: 'B', name: 'X'}];
console.log(perfilGroups(vocab).map(([p, vs]) => p + ':' + vs.length).join(','));
console.log(perfilLangOptions('', 'pt'));
console.log(perfilLangOptions('auto', 'pt')
  .includes('<option value="auto" selected>Detectar (cualquier idioma)</option>'));
console.log(perfilLangOptions('es-pt', '').includes('<option value="es-pt" selected>'));
console.log(perfilLangOf({props: {idioma: 'auto'}}) === '', perfilLangOf({props: {idioma: 'en'}}),
  perfilLangOf({props: {}}));""")
        lines = out.splitlines()
        self.assertEqual(lines[0], "A:2,B:1")
        self.assertTrue(lines[1].startswith('<option value="" selected>Por defecto · Portugués</option>'))
        self.assertNotIn('<option value="">Detectar', lines[1])  # detectar es «auto», no vacío
        self.assertEqual(lines[2:], ["true", "true", "true en pt"])

    def test_textos_de_importar_guardar_y_destinos(self):
        out = self.run_js("""
console.log(perfilImportText({copiadas: 2, omitidas: 1, proyectos: ['Alfa', 'B<eta>']}));
console.log(perfilImportText({copiadas: 0, omitidas: 0}));
console.log(perfilSavedText('idioma', true, true));
console.log(perfilSavedText('contexto', false, true));
console.log(perfilSavedText('contexto', false, false));
const h = perfilOutsHtml([{path: 'C:\\\\Vault', format: 'docx', by_project: true}, {path: '/x', format: 'md'}]);
console.log((h.match(/<tr>/g) || []).length, h.includes('<option value="docx" selected>'),
  h.includes('data-out-proj="0" checked'), h.includes('data-out-del="1"'), h.includes('C:\\\\Vault'));
console.log(perfilOutsHtml([]));""")
        lines = out.splitlines()
        self.assertEqual(lines[0], "2 listas copiadas · Alfa, B&lt;eta&gt; · 1 ya existían")
        self.assertEqual(lines[1], "No había listas nuevas")
        self.assertEqual(lines[2], "Idioma: el directo lo usa desde el siguiente trozo")
        self.assertEqual(lines[3], "Contexto guardado · se aplicará si la vuelves a transcribir (y al resumen)")
        self.assertEqual(lines[4], "Contexto guardado · se usará al transcribirla")
        self.assertEqual(lines[5], "2 true true true true")
        self.assertIn("Ninguno", lines[6])


if __name__ == "__main__":
    unittest.main()
