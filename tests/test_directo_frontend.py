# -*- coding: utf-8 -*-
"""Interfaz del directo en la nube (directo.js): carga y hooks en app.js, textos obligatorios, nada externo, y los
helpers puros ejecutados con node (si está instalado)."""
import re
import shutil
import subprocess
import unittest

from test_frontend import js_defs, read

HOOKS = ("onDirectoJobs(prev)", "directoAfterRender(tx, lj)", 'id="directoBar"', "renderDirectoBar(a, j)",
         "directoSettingsHtml(c)", "bindDirectoSettings(d)", "collectDirectoSettings(d)")


class DirectoCargaTests(unittest.TestCase):
    def test_index_carga_directo_tras_voces(self):
        html = read("index.html")
        self.assertIn('<script src="/directo.js"></script>', html)
        self.assertLess(html.index('<script src="/voces.js">'), html.index('<script src="/directo.js">'))
        self.assertLess(html.index('<script src="/app.js">'), html.index('<script src="/directo.js">'))

    def test_app_js_tiene_los_hooks_guardados_con_typeof(self):
        app = read("app.js")
        for h in HOOKS:
            self.assertIn(h, app, h)
        # cada hook de funcion va protegido: app.js sigue funcionando aunque directo.js no cargue
        for fn in ("onDirectoJobs", "directoAfterRender", "renderDirectoBar", "directoSettingsHtml",
                   "bindDirectoSettings", "collectDirectoSettings"):
            self.assertRegex(app, rf"typeof {fn} === 'function'", fn)
        # y una sola vez cada uno (no se duplicaron al fusionar)
        for h in HOOKS:
            self.assertEqual(app.count(h), 1, h)

    def test_textos_y_sin_nada_externo(self):
        js = read("directo.js")
        for s in ("Cada clave es de una cuenta distinta: el audio y el texto se procesan bajo esa cuenta",
                  "Traducir al", "Modo seguir", "/api/live/traducir", "Claves adicionales (opcional)",
                  "Modelo del directo", "Traducción al ${directoTradNombre(to)}", "Directo en pausa por cupo",
                  "directoCss", "['es', 'español'], ['en', 'inglés']", 'id="directoTradTo"'):
            self.assertIn(s, js, s)
        self.assertNotIn("fetch(", js)        # todo pasa por api() de app.js (mismo origen)
        self.assertNotIn("<link", js)
        self.assertNotIn("@import", js)
        self.assertNotRegex(js, r"https?://")  # sin fuentes, scripts ni llamadas externas
        for fn in ("onDirectoJobs", "directoAfterRender", "renderDirectoBar", "renderDirectoTrad",
                   "directoSettingsHtml", "bindDirectoSettings", "collectDirectoSettings",
                   "directoFmtWait", "directoQuotaText", "directoLatencyText"):
            self.assertRegex(js, re.compile(rf"^function {fn}\(", re.M), fn)


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class DirectoHelpersTests(unittest.TestCase):
    def run_js(self, code):
        defs = js_defs(read("directo.js"), ["directoFmtWait", "directoQuotaText", "directoLatencyText"])
        r = subprocess.run(["node", "-e", defs + "\n" + code], capture_output=True, text=True, encoding="utf-8",
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_fmt_wait(self):
        out = self.run_js("console.log([11520, 1500, 40, 0].map(directoFmtWait).join('|'))")
        self.assertEqual(out, "3 h 12 min|25 min|40 s|0 s")

    def test_quota_text(self):
        out = self.run_js("console.log(directoQuotaText({daily:true, remaining:1234, reset_in:11520, keys:1}))")
        self.assertIn("quedan 1234 peticiones hoy", out)
        self.assertIn("3 h 12 min", out)
        self.assertNotIn("clave", out)  # con una sola clave no se numera
        out = self.run_js("console.log(directoQuotaText({daily:true, remaining:null, keys:1}))")
        self.assertIn("sin medir", out)
        out = self.run_js("console.log(JSON.stringify(directoQuotaText({daily:false})))")
        self.assertEqual(out, '""')
        out = self.run_js("console.log(directoQuotaText({daily:true, remaining:50, reset_in:600, keys:3, active:2, "
                          "others_remaining:1800}))")
        self.assertIn("clave 2/3", out)
        self.assertIn("+1800 en otras claves", out)
        out = self.run_js("console.log(directoQuotaText({daily:true, remaining:0, blocked_for:5400, keys:1}))")
        self.assertIn("Groq pide esperar 1 h 30 min", out)

    def test_latency_text(self):
        out = self.run_js("console.log(directoLatencyText({n:0}))")
        self.assertEqual(out, "midiendo…")
        out = self.run_js("console.log(directoLatencyText({n:5, p50:1.8, p95:3.2}))")
        self.assertIn("1,8 s", out)
        self.assertIn("3,2 s", out)
        self.assertNotRegex(out, re.compile(r"\d\.\d"))  # coma decimal, no punto
        self.assertNotIn("red", out)
        # por fases: cuanto es red (y Groq, si lo declara) y cuanto cupo; el resto es de este lado
        out = self.run_js("console.log(directoLatencyText({n:5, p50:1.8, p95:3.2, phases:{net:{p50:1.1, p95:2.0}, "
                          "infer:{p50:0.8, p95:1.1}, wait:{p50:0.3, p95:1.0}}}))")
        self.assertIn("red 1,1 s (inferencia 0,8 s)", out)
        self.assertIn("cupo 0,3 s", out)
        out = self.run_js("console.log(directoLatencyText({n:5, p50:1.8, p95:3.2, phases:{net:{p50:1.1, p95:2.0}, "
                          "wait:{p50:0.01, p95:0.02}}}))")
        self.assertNotIn("cupo", out)
        self.assertNotIn("inferencia", out)


if __name__ == "__main__":
    unittest.main()


class AvisosFranjaTests(unittest.TestCase):
    """B6, puntos 1-3: los avisos de la franja del directo (directo.js) y sus ganchos en app.js."""

    def test_ganchos_en_app_js(self):
        app = read("app.js")
        self.assertEqual(app.count('<div class="lh-avisos" data-lh-avisos></div>'), 1)
        # tras los medidores (cada 300 ms con la grabación) y al pintar la cabecera; protegidos con typeof
        self.assertEqual(app.count("if (typeof renderDirectoAvisos === 'function') renderDirectoAvisos();"), 2)
        self.assertIn("(typeof directoParadaTexto === 'function' && directoParadaTexto(S.rec))", app)
        poll = app[app.index("async function pollRec()"):app.index("function onRecChange()")]
        self.assertLess(poll.index("updateMeters()"), poll.index("renderDirectoAvisos()"))

    def test_textos_y_rutas(self):
        js = read("directo.js")
        for s in ("La llamada ha terminado: paro en ", "No terminar", "El micro no recibe sonido desde hace ",
                  "El ordenador no suena desde hace ", "Lo último suena a ", "/api/rec/seguir",
                  "/api/live/propuesta", "No volver a proponerlo en esta grabación",
                  "■ La llamada ha terminado: grabación parada y guardada"):
            self.assertIn(s, js, s)
        for fn in ("directoAvisos", "directoParadaTexto", "renderDirectoAvisos", "directoCambiarIdioma",
                   "directoSeguir"):
            self.assertRegex(js, re.compile(rf"^(async )?function {fn}\(", re.M), fn)
        # «Cambiar» es el cambio de idioma de siempre: el selector de la cabecera (notas.js / app.js lo guardan)
        self.assertIn("#lHero select[data-prop='idioma']", js)
        self.assertIn("dispatchEvent(new Event('change', { bubbles: true }))", js)

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_lo_que_se_ensena(self):
        nombres = ["directoIdiomaNombre", "directoMinTxt", "directoAvisos", "directoParadaTexto"]
        defs = js_defs(read("directo.js"), nombres)
        defs = "const DIRECTO_IDIOMAS = { es: 'español', en: 'inglés', pt: 'portugués', fr: 'francés' };\n" + defs

        def run(code):
            r = subprocess.run(["node", "-e", defs + "\nconsole.log(JSON.stringify(" + code + "))"],
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            import json
            return json.loads(r.stdout)
        self.assertEqual(run("directoAvisos({}, null, true)"), [])
        a = run("directoAvisos({fin_llamada: {quedan_s: 27.4}, callada: {mic: 75, sys: 190}}, null, true)")
        self.assertEqual([x["id"] for x in a], ["fin", "mic", "sys"])
        self.assertEqual((a[0]["texto"], a[0]["quedan"], a[0]["botones"][0]["label"]),
                         ("La llamada ha terminado: paro en ", 27, "No terminar"))
        self.assertEqual(a[1]["texto"], "El micro no recibe sonido desde hace 1 min")
        self.assertEqual(a[2]["texto"], "El ordenador no suena desde hace 3 min")
        # lo de la grabadora solo si esta app graba esta reunión (con OBS, solo el idioma)
        self.assertEqual(run("directoAvisos({fin_llamada: {quedan_s: 5}, callada: {mic: 75}}, null, false)"), [])
        p = run("directoAvisos({}, {propuesta: {idioma: 'pt', cambiar_a: 'pt'}}, false)")
        self.assertEqual(p[0]["texto"], "Lo último suena a portugués")
        self.assertEqual([(b["label"], b["valor"]) for b in p[0]["botones"]], [("Cambiar", "pt"), ("No", "pt")])
        m = run("directoAvisos({}, {propuesta: {idioma: 'pt', cambiar_a: 'es-pt'}}, false)")
        self.assertEqual(m[0]["botones"][0]["label"], "Cambiar a español + portugués")
        self.assertEqual(m[0]["botones"][0]["valor"], "es-pt")
        self.assertEqual(m[0]["botones"][1]["valor"], "pt")  # «No» rechaza el idioma oído, no la mezcla
        self.assertEqual(run("directoParadaTexto({parada: {motivo: 'llamada'}})"),
                         "■ La llamada ha terminado: grabación parada y guardada · la transcripción termina sola")
        self.assertEqual(run("directoParadaTexto({recording: false})"), "")
