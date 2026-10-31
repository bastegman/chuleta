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
                  "Traducir al español", "Modo seguir", "/api/live/traducir", "Claves adicionales (opcional)",
                  "Modelo del directo", "Traducción al español", "Directo en pausa por cupo", "directoCss"):
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


if __name__ == "__main__":
    unittest.main()
