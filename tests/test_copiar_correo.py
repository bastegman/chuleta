# -*- coding: utf-8 -*-
"""«Copiar para correo» (bloque B4 del rediseño, mock 08): el resumen va al portapapeles como acta con formato (HTML)
y, de reserva, en texto limpio; sin Markdown (##, **, - [ ]), sin los minutos [mm:ss] y sin firma de IA (D10). Los
helpers de notas.js se ejecutan con node (si está instalado); el portapapeles de verdad lo prueban test_ui_smoke
(Chromium) y test_portapapeles_webview2 (WebView2, en el runner de Windows). Los nombres son inventados."""
import json
import shutil
import subprocess
import unittest

from test_frontend import js_defs, read

RESUMEN = """## Resumen
La migración va **en plazo** salvo el entorno de pruebas, bloqueado por permisos de red [1:12].

## Puntos clave
- Plazo: tres semanas para el piloto [0:45]
- El proveedor confirma la *ventana* del sábado [12:03]

## Decisiones
- Se aplaza la prueba de carga [1:02:10]

## Próximos pasos
- [ ] Luis: escalar los permisos de red y avisar por correo — viernes [2:05]
- [x] Ana: pasar el inventario actualizado [3:20]

## Participantes
- Ana (cliente)
- Luis (equipo)
"""


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class ActaTests(unittest.TestCase):
    def run_js(self, code):
        defs = js_defs(read("app.js"), ["esc", "nfc", "pad", "MES", "DIA", "aDate"])
        defs += "\n" + js_defs(read("notas.js"), ["ACTA_GENTE", "ACTA_FONT", "actaInline", "actaItem", "actaCorreo",
                                                  "actaInfo"])
        r = subprocess.run(["node", "-e", defs + "\n" + code], capture_output=True, text=True, encoding="utf-8",
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def acta(self, info):
        out = self.run_js(f"console.log(JSON.stringify(actaCorreo({json.dumps(RESUMEN)}, {json.dumps(info)})));")
        return json.loads(out)

    def test_texto_limpio_sin_markdown_ni_minutos(self):
        r = self.acta({"title": "Comité de seguimiento", "when": "viernes 6 nov, 10:00", "people": "Ana, Luis"})
        self.assertEqual(r["text"], "\n".join([
            "Comité de seguimiento · viernes 6 nov, 10:00 · Ana, Luis",
            "",
            "Resumen",
            "La migración va en plazo salvo el entorno de pruebas, bloqueado por permisos de red.",
            "",
            "Puntos clave",
            "• Plazo: tres semanas para el piloto",
            "• El proveedor confirma la ventana del sábado",
            "",
            "Decisiones",
            "• Se aplaza la prueba de carga",
            "",
            "Próximos pasos",
            "• Luis: escalar los permisos de red y avisar por correo — viernes",
            "• Ana: pasar el inventario actualizado",
        ]) + "\n")  # con asistentes confirmados, la sección «Participantes» del resumen sobra

    def test_html_con_formato_y_sin_marcas(self):
        r = self.acta({"title": "Comité", "when": "viernes 6 nov, 10:00", "people": "Ana, Luis"})
        h = r["html"]
        self.assertTrue(h.startswith('<div style="font-family:Aptos,Calibri,Arial,sans-serif;font-size:11pt">'))
        self.assertIn("<p style=\"margin:0 0 10pt\"><b>Comité</b> · viernes 6 nov, 10:00 · Ana, Luis</p>", h)
        self.assertIn("<b>Resumen</b>", h)
        self.assertIn("va <b>en plazo</b> salvo", h)
        self.assertIn("la <i>ventana</i> del sábado", h)
        self.assertIn("<li><b>Luis</b>: escalar los permisos de red y avisar por correo — viernes</li>", h)
        self.assertIn("<li><b>Plazo</b>: tres semanas para el piloto</li>", h)
        self.assertEqual(h.count("<ul"), 3)
        for marca in ("##", "[ ]", "[x]", "**", "[1:12]", "[1:02:10]", "Participantes", "- "):
            self.assertNotIn(marca, h, marca)
            self.assertNotIn(marca, r["text"], marca)

    def test_sin_firma_de_ia(self):
        r = self.acta({"title": "Comité", "when": "", "people": ""})
        for firma in ("IA", "Chuleta", "Generado", "generado", "Claude", "modelo", "revísalo"):
            self.assertNotIn(firma, r["text"], firma)
            self.assertNotIn(firma, r["html"], firma)

    def test_sin_asistentes_confirmados_se_queda_la_seccion_del_resumen(self):
        r = self.acta({"title": "Comité", "when": "", "people": ""})
        self.assertTrue(r["text"].startswith("Comité\n\nResumen\n"))
        self.assertIn("Participantes\n• Ana (cliente)\n• Luis (equipo)\n", r["text"])

    def test_escapa_el_html_y_aguanta_lo_raro(self):
        out = self.run_js("""
const r = actaCorreo('# Acta <script>alert(1)</script>\\n\\nTexto con <b>etiquetas</b> & "comillas"\\n- [ ] \\n---\\n'
  + '1. Primero [0:10]\\n2) Segundo\\n\\n## Vacía\\n\\n',
  {title: '<img src=x onerror=alert(1)>', when: '', people: ''});
console.log(JSON.stringify(r));
console.log(JSON.stringify(actaCorreo('', {})));
console.log(JSON.stringify(actaCorreo('solo un párrafo\\nen dos líneas', null)));""")
        r, vacio, uno = [json.loads(x) for x in out.splitlines()]
        self.assertNotIn("<script", r["html"])
        self.assertNotIn("<img", r["html"])
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", r["html"])
        self.assertIn("Texto con &lt;b&gt;etiquetas&lt;/b&gt; &amp; &quot;comillas&quot;", r["html"])
        self.assertIn("<li>Primero</li><li>Segundo</li>", r["html"])
        self.assertNotIn("Vacía", r["text"])  # una sección sin contenido no sale
        self.assertEqual(vacio, {"text": "\n", "html": '<div style="font-family:Aptos,Calibri,Arial,sans-serif;'
                                                       'font-size:11pt"></div>'})
        self.assertEqual(uno["text"], "solo un párrafo en dos líneas\n")

    def test_cabecera_de_la_reunion(self):
        out = self.run_js("""
const a = {date: new Date(2026, 10, 6, 10, 5).getTime(), label: 'Kick-off',
  props: {titulo: 'Comité de seguimiento', participantes: 'Ana, Luis', participantes_origen: ''}};
console.log(JSON.stringify(actaInfo(a)));
console.log(JSON.stringify(actaInfo({date: new Date(2025, 0, 2, 9, 0).getTime(), label: 'Kick-off',
  props: {participantes: 'Ana', participantes_origen: 'outlook'}})));
console.log(JSON.stringify(actaInfo({props: {}})));
console.log(JSON.stringify(actaInfo(undefined)));""")
        lines = [json.loads(x) for x in out.splitlines()]
        self.assertEqual(lines[0], {"title": "Comité de seguimiento", "when": "viernes 6 nov, 10:05",
                                    "people": "Ana, Luis"})
        # sugeridos por Outlook sin confirmar: no salen; otro año: con el año
        self.assertEqual(lines[1], {"title": "Kick-off", "when": "jueves 2 ene 2025, 09:00", "people": ""})
        self.assertEqual(lines[2], {"title": "Reunión", "when": "", "people": ""})
        self.assertEqual(lines[3], {"title": "Reunión", "when": "", "people": ""})


class GanchosTests(unittest.TestCase):
    def test_el_boton_copia_el_acta_con_formato(self):
        notas, app = read("notas.js"), read("app.js")
        self.assertIn('data-act="sum-copy"', notas)
        self.assertIn("Copiar para correo</button>", notas)
        self.assertNotIn("copy(S.sum.text", notas)  # antes: el Markdown tal cual
        self.assertRegex(notas, r"actaCorreo\(S\.sum\.text, actaInfo\(")
        self.assertIn("copyRich(r.html, r.text,", notas)
        fn = app[app.index("async function copyRich("):app.index("async function copy(")]
        self.assertIn("new ClipboardItem({ 'text/html': new Blob([html], { type: 'text/html' }),", fn)
        self.assertIn("'text/plain': new Blob([text], { type: 'text/plain' })", fn)
        self.assertIn("navigator.clipboard.writeText(text)", fn)  # de reserva: el texto limpio
        self.assertNotRegex(notas[notas.index("const ACTA_GENTE"):notas.index("function actaInfo")],
                            r"[\U0001F300-\U0001FAFF☀-⛿]")


if __name__ == "__main__":
    unittest.main()
