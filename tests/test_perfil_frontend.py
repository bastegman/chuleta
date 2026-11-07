# -*- coding: utf-8 -*-
"""Interfaz del perfil de reunión (perfil.js): carga y ganchos en app.js / notas.js / reunion.js / index.html, textos
obligatorios, nada externo, y los helpers puros ejecutados con node (si está instalado)."""
import re
import shutil
import subprocess
import unittest

from test_frontend import js_defs, read

# ganchos de app.js y notas.js: cada uno una sola vez y protegido con typeof (la app funciona sin perfil.js)
HOOKS_APP = ("perfilTemaHtml(lastWs)", "perfilBindRec(d)", "perfilCamposHtml(S.config.language ?? 'es-en', pf.context)",
             "perfilRecExtra(d)", "perfilLiveHtml(a)", "perfilFieldsetHtml()", "bindPerfilSettings(d)",
             "collectPerfilSettings(d)", "perfilLangOf(list[0])", "perfilImportar('', openSettings)",
             "perfilVocabEmptyHtml()")
GUARDED = ("perfilTemaHtml", "perfilBindRec", "perfilCamposHtml", "perfilRecExtra", "perfilLiveHtml",
           "perfilFieldsetHtml", "bindPerfilSettings", "collectPerfilSettings", "perfilLangOf", "perfilImportar",
           "perfilVocabEmptyHtml", "perfilPropsHtml", "perfilSavedText")


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
        # el idioma ya no va dentro de las opciones del directo (es de la reunion, tambien con «Solo grabar»)
        # (07/11/2026: el selector va detrás de «Cambiar», con su etiqueta; dentro sigue sin haber idioma)
        self.assertRegex(app, r'<div id="rLiveOpts"[^>]*>(<label[^<]*>Modelo del directo</label>)?'
                              r'<select id="rPreset">\$\{presets\}</select></div>')
        self.assertNotIn("langSelect('rLang', S.config.language ?? 'es-en')}</div>", app)
        # el motor del directo, el idioma del resumen y las instrucciones extra viven en el perfil (con fallback)
        self.assertNotIn('<label>Motor para el directo</label>', app)
        self.assertIn("$('#lPreset', d)?.value", app)
        self.assertIn("$('#sSumLang', d)?.value", app)
        # mezclas nuevas en la lista de idiomas y el toast de Ctrl+Alt+R con «clic para ajustar»
        self.assertIn("['es-pt', 'Español + portugués (mezcla)'], ['en-pt', 'Inglés + portugués (mezcla)']", app)
        self.assertIn("clic para ajustar idioma, tema y contexto", app)
        # el idioma de la reunion no cambia el ajuste general al grabar
        self.assertNotIn("S.config.language = S.selLang = $('#rLang', d).value;", app)
        # la sugerencia de Teams no pisa un tema fijado por el perfil
        self.assertIn("!d.dataset.pfVocab", read("reunion.js"))

    def test_textos_y_sin_nada_externo(self):
        js = read("perfil.js")
        # «Importar listas…» pasó a «Copiar listas…» con las carpetas conectadas (backlog 10, msg 1148)
        for s in ("Guardar como perfil por defecto", "Copiar listas de otra carpeta…", "Sin vocabulario", "Nuevo…",
                  "Usar las listas de una carpeta…", "/api/vocab/link/add", "/api/vocab/link/remove",
                  "Perfil de reunión", "Destinos de salida", "Añadir carpeta…", "subcarpeta por proyecto",
                  "Markdown (.md)", "Texto (.txt)", "Word (.docx)", "/api/perfil", "/api/vocab/import",
                  "/api/link/pick", "<Proyecto>\\\\Vocabulario\\\\*.txt", "uno por línea", "Por defecto · ",
                  "Automática · la última usada", "Automático · el que sugiera la reunión", "Contexto",
                  "el directo lo usa desde el siguiente trozo", "${esc(APP_NAME)}"):
            self.assertIn(s, js, s)
        self.assertNotIn("fetch(", js)  # todo pasa por api() de app.js
        self.assertNotRegex(js, r"https?://")
        self.assertNotRegex(js, r"[\U0001F300-\U0001FAFF\u2600-\u26FF]")  # sin emojis: iconos I.*
        self.assertNotIn("Notas", js)  # el nombre de la app sale de APP_NAME
        for fn in ("perfilLangLabel", "perfilLangOptions", "perfilLangOf", "perfilResolve", "perfilGroups",
                   "perfilTemaOptions", "perfilTemaHtml", "perfilBindTema", "perfilCamposHtml", "perfilBindRec",
                   "perfilRecExtra", "perfilImportar", "perfilImportText", "perfilPropsHtml", "perfilLiveHtml",
                   "perfilSavedText", "perfilFieldsetHtml", "perfilOutsHtml", "bindPerfilOuts", "bindPerfilSettings",
                   "collectPerfilSettings", "perfilVocabEmptyHtml", "vocabConectar", "vocabConectadaText",
                   "vocabDesconectar", "vocabAvisoText", "vocabCarpetasHtml"):
            self.assertRegex(js, re.compile(rf"^(async )?function {fn}\(", re.M), fn)
        self.assertNotIn("Importar listas de otra carpeta", js + read("app.js"))


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class PerfilHelpersTests(unittest.TestCase):
    def run_js(self, code):
        app = read("app.js")
        langs = re.search(r"^const LANGS = \[.*?\]\];$", app, re.S | re.M).group(0)
        defs = js_defs(app, ["esc", "nfc"]) + "\n" + langs + "\nconst S = {selLang: null, config: {language: 'pt'}};\n"
        defs += js_defs(read("perfil.js"), ["perfilLangLabel", "perfilLangOptions", "perfilLangOf", "perfilResolve",
                                            "perfilGroups", "perfilTemaOptions", "perfilImportText", "perfilSavedText",
                                            "perfilOutsHtml", "PERFIL_FORMATS", "vocabConectadaText", "vocabAvisoText",
                                            "vocabCarpetasHtml"])
        defs = "const I = {folder: '[F]', ext: '[E]', check: '[C]'}; const APP_NAME = 'Chuleta';\n" + defs
        r = subprocess.run(["node", "-e", defs + "\n" + code], capture_output=True, text=True, encoding="utf-8",
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_precedencias_como_el_servidor(self):
        out = self.run_js("""
const perfil = {folder: null, vocab: null, context: '', language: 'es'};
const sug = {name: 'Comité', vocab: 'Beta/Vocabulario/Legal.txt'};
const last = {folder: 'Otra', vocab: 'Alfa/Vocabulario/General.txt'};
const r1 = perfilResolve(perfil, sug, last);
console.log([r1.vocab, r1.origen.vocab, r1.folder, r1.origen.folder, r1.name, r1.language].join('|'));
const r2 = perfilResolve({folder: 'Alfa/Reuniones', vocab: '', context: 'ctx', language: 'pt'}, sug, last);
console.log([r2.vocab, r2.origen.vocab, r2.folder, r2.origen.folder, r2.name, r2.origen.name, r2.context].join('|'));
const r3 = perfilResolve({vocab: ''}, sug, last, {vocab: 'X', folder: '', language: 'en-pt', name: 'N'});
console.log([r3.vocab, r3.folder, r3.origen.folder, r3.language, r3.name].join('|'));
console.log(JSON.stringify(perfilResolve(null, null, null)));""")
        lines = out.splitlines()
        self.assertEqual(lines[0], "Beta/Vocabulario/Legal.txt|reunion|Otra|ultimo|Comité|es")
        self.assertEqual(lines[1], "|perfil|Alfa/Reuniones|perfil|Comité|reunion|ctx")
        self.assertEqual(lines[2], "X||elegido|en-pt|N")
        self.assertIn('"vocab":""', lines[3])

    def test_tema_en_dos_pasos_e_idiomas(self):
        out = self.run_js("""
const vocab = [{id: 'A/Vocabulario/General.txt', project: 'A', name: 'General', desc: 'd'},
  {id: 'A/Vocabulario/Legal.txt', project: 'A', name: 'Legal'}, {id: 'B/Vocabulario/X.txt', project: 'B', name: 'X'}];
console.log(perfilGroups(vocab).map(([p, vs]) => p + ':' + vs.length).join(','));
const html = perfilTemaOptions(vocab, 'A', 'A/Vocabulario/Legal.txt');
console.log((html.match(/<option/g) || []).length, (html.match(/ hidden/g) || []).length,
  html.includes(' selected>Legal'), html.includes('General · d'),
  html.startsWith('<option value="">Sin vocabulario</option>'));
console.log(perfilLangOptions('', 'pt'));
console.log(perfilLangOptions('auto', 'pt')
  .includes('<option value="auto" selected>Detectar (cualquier idioma)</option>'));
console.log(perfilLangOptions('es-pt', '').includes('<option value="es-pt" selected>'));
console.log(perfilLangOf({props: {idioma: 'auto'}}) === '', perfilLangOf({props: {idioma: 'en'}}),
  perfilLangOf({props: {}}));""")
        lines = out.splitlines()
        self.assertEqual(lines[0], "A:2,B:1")
        self.assertEqual(lines[1], "4 1 true true true")  # 3 listas + «Sin vocabulario»; la de B oculta
        self.assertTrue(lines[2].startswith('<option value="" selected>Por defecto · Português</option>'))
        self.assertNotIn('<option value="">Detectar', lines[2])  # detectar es «auto», no vacío
        self.assertEqual(lines[3:], ["true", "true", "true en pt"])

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

    def test_carpetas_conectadas(self):
        out = self.run_js("""
console.log(vocabConectadaText({name: 'Listas <W>', listas: 3, avisos: [], ya: false}));
console.log(vocabConectadaText({name: 'L', listas: 1, avisos: [{lista: 'a.txt', motivo: 'nube'}], ya: true}));
console.log(vocabAvisoText({lista: 'Sub\\\\Infra.txt', motivo: 'nube'}));
console.log(vocabAvisoText({lista: 'Glosario.txt', motivo: 'grande', kb: 120}));
const h = vocabCarpetasHtml([{rel: ':vabc123', name: 'Listas', path: 'C:\\\\OneDrive\\\\Listas', existe: true,
  listas: 2, avisos: [{lista: 'G.txt', motivo: 'grande', kb: 70}]},
  {rel: ':vdef456', name: 'Vieja', path: 'D:\\\\X', existe: false, listas: 0, avisos: []}]);
console.log((h.match(/<tr>/g) || []).length, h.includes('data-vunlink=":vabc123"'), h.includes('data-vopen=":vabc123"'),
  h.includes('data-vopen=":vdef456"'), h.includes('data-vunlink=":vdef456"'), h.includes('2 listas'),
  h.includes('No se encuentra'), h.includes('pesa 70 KB'));
console.log(vocabCarpetasHtml([]).includes('Ninguna'));""")
        lines = out.splitlines()
        self.assertEqual(lines[0], "[C]Conectada «Listas &lt;W&gt;» · 3 listas · se leen al grabar, sin copiarlas")
        self.assertEqual(lines[1], "«L» ya estaba conectada · 1 lista · 1 se salta: lo ves en Ajustes › Vocabulario")
        self.assertIn("«Sub\\Infra.txt» está solo en OneDrive", lines[2])
        self.assertIn("Mantener siempre en este dispositivo", lines[2])
        self.assertEqual(lines[3], "«Glosario.txt» pesa 120 KB: no parece una lista (se leen hasta 64 KB)")
        # 2 carpetas + el aviso de la primera + «no se encuentra» de la segunda (que no lleva «Abrir»)
        self.assertEqual(lines[4], "4 true true false true true true true")
        self.assertEqual(lines[5], "true")


if __name__ == "__main__":
    unittest.main()
