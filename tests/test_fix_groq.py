# -*- coding: utf-8 -*-
"""fix-groq (10/11/2026, msgs 1468-1474: «se queda loading y no hace transcript nunca… para mí debería seguir
funcionando»). Desde sin-groq (d74bf0a) el motor rechazaba unos ajustes con un proveedor vacío o desconocido, que hasta
fe29522 transcribían con Groq. Ahora solo los rechaza en «Probar» del asistente sin proveedor elegido (la marca
engines.SIN_PROVEEDOR que pone server._candidata); unos ajustes guardados siguen yendo a Groq, como siempre. Una
instalación nueva sigue sin sugerir Groq (test_sin_groq)."""
import json
import os
import shutil
import tempfile
import time
import unittest

from support import TempTree, engines, server  # noqa: F401  (support prepara sys.path)
from test_e2e_procesado import CLAVE, GroqFalso
from test_sin_groq import _claro
from test_subir_audio import Servidor

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

try:
    import av  # noqa: F401  (write_flac: la petición de verdad manda un FLAC)
except ImportError:  # pragma: no cover
    av = None


class MotorTests(unittest.TestCase):
    def test_ajustes_guardados_con_proveedor_vacio_van_a_groq(self):
        """Como en fe29522: «provider» vacío, desconocido o ausente en unos ajustes guardados = Groq."""
        for cfg in ({"provider": "", "api_key": "k"}, {"provider": "inventado", "api_key": "k"},
                    {"provider": None, "api_key": "k"}, {"api_key": "k"}):
            with self.subTest(cfg=cfg):
                url, headers, model = engines.CloudEngine.endpoint(cfg)
                self.assertEqual(url, engines.PROVIDERS["groq"]["url"])
                self.assertEqual(headers, {"Authorization": "Bearer k"})
                self.assertEqual(model, "whisper-large-v3-turbo")  # el primero de Groq, como antes
        self.assertEqual(engines.CloudEngine.endpoint({"provider": "", "api_key": "k", "model": "whisper-large-v3"})[2],
                         "whisper-large-v3")

    def test_probar_sin_proveedor_no_va_a_nadie(self):
        for p in ("", "inventado"):
            with self.subTest(p=p), self.assertRaises(engines.EngineError) as cm:
                engines.CloudEngine.endpoint({"provider": p, "api_key": "k", engines.SIN_PROVEEDOR: True})
            self.assertIn("Elige el proveedor", str(cm.exception))

    @unittest.skipUnless(av, "PyAV no instalado (el FLAC de la petición de verdad lo necesita)")
    def test_transcribe_de_verdad_contra_un_groq_falso(self):
        """Una petición real (CloudEngine.test, la de «Probar conexión») con unos ajustes guardados sin proveedor:
        llega a Groq con la clave guardada. Con 0d5169b fallaba antes de salir («Elige el proveedor…»)."""
        groq = GroqFalso()
        tmp = tempfile.mkdtemp(prefix="fixgroq-")
        antes = dict(engines.PROVIDERS["groq"])
        try:
            engines.PROVIDERS["groq"] = dict(antes, url=groq.base + "/audio/transcriptions")
            secs = engines.CloudEngine(tmp).test({"provider": "", "api_key": CLAVE, "model": "whisper-large-v3"})
            self.assertGreaterEqual(secs, 0)
            self.assertEqual(groq.peticiones, [("/openai/v1/audio/transcriptions", "whisper-large-v3", True)])
            with self.assertRaises(engines.EngineError):  # y «Probar» del asistente sin proveedor, no sale
                engines.CloudEngine(tmp).test({"provider": "", "api_key": CLAVE, engines.SIN_PROVEEDOR: True})
            self.assertEqual(len(groq.peticiones), 1)
        finally:
            engines.PROVIDERS["groq"] = antes
            groq.httpd.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)


class CandidataTests(TempTree):
    def app(self, **st):
        from test_carpetas import make_app
        return make_app(self, **st)

    def test_la_marca_solo_en_la_prueba_sin_proveedor(self):
        a = self.app()
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "", "api_key": "k"}}})
        self.assertTrue(st["cloud"].get(engines.SIN_PROVEEDOR))
        with self.assertRaises(engines.EngineError):
            engines.CloudEngine.endpoint(st["cloud"])
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "groq", "api_key": "k"}}})
        self.assertNotIn(engines.SIN_PROVEEDOR, st["cloud"])
        self.assertNotIn(engines.SIN_PROVEEDOR, a.settings.get()["cloud"])  # la prueba no guarda nada

    def test_quien_ya_usa_groq_sin_proveedor_guardado(self):
        """Unos ajustes viejos con «provider» vacío y su clave: la prueba (sin elegir nada) y la cola, a Groq."""
        a = self.app(procesado="api", cloud={"provider": "", "api_key": "gsk_x"})
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {}}})
        self.assertNotIn(engines.SIN_PROVEEDOR, st["cloud"])  # elegido (procesado api): no se marca
        self.assertEqual(engines.CloudEngine.endpoint(st["cloud"])[0], engines.PROVIDERS["groq"]["url"])
        self.assertEqual(engines.CloudEngine.endpoint(a.settings.get()["cloud"])[0], engines.PROVIDERS["groq"]["url"])


GSK = "gsk_" + "prueba" * 6  # falsa: nunca sale a ningún sitio
# lo que podía tener Bernardo: un proveedor que no existe (de una versión anterior)…
SIN_PROVEEDOR = {"procesado": "api", "preset": "nube", "live_preset": "nube",
                 "cloud": {"provider": "", "api_key": GSK, "model": "whisper-large-v3"}}
# …y lo que dejaba en ese caso un «Guardar» de 0d5169b: el desplegable no marcaba ninguno, el navegador marcaba el
# primero (Azure OpenAI, con el orden alfabético de sin-groq) y se guardaba Azure sin endpoint
AZURE_DE_0D5169B = {"procesado": "api", "preset": "nube", "live_preset": "nube",
                    "cloud": {"provider": "azure", "api_key": GSK, "model": "whisper-large-v3", "azure_endpoint": "",
                              "azure_deployment": "", "azure_api_version": "2024-06-01"}}


class RepararProveedorTests(TempTree):
    def test_lo_que_se_repara_y_lo_que_no(self):
        casos = [({"provider": ""}, "groq"), ({"provider": "inventado"}, "groq"), ({"provider": None}, "groq"),
                 ({"provider": 3}, "groq"),
                 (dict(AZURE_DE_0D5169B["cloud"]), "groq"),
                 (dict(AZURE_DE_0D5169B["cloud"], azure_endpoint="https://r.openai.azure.com"), "azure"),
                 (dict(AZURE_DE_0D5169B["cloud"], azure_deployment="whisper"), "azure"),
                 (dict(AZURE_DE_0D5169B["cloud"], api_key="clave-de-azure"), "azure"),  # no es de Groq: es suya
                 (dict(AZURE_DE_0D5169B["cloud"], api_key=""), "azure"),
                 ({"provider": "groq", "api_key": GSK}, "groq"), ({"provider": "openai", "api_key": GSK}, "openai"),
                 ({"provider": "custom", "api_key": GSK}, "custom")]
        for cloud, esperado in casos:
            with self.subTest(cloud=cloud):
                d = {"cloud": dict(cloud)}
                frase = server.reparar_proveedor(d)
                self.assertEqual(d["cloud"]["provider"], esperado)
                self.assertEqual(bool(frase), cloud.get("provider") != esperado)
        self.assertIsNone(server.reparar_proveedor({}))
        self.assertIsNone(server.reparar_proveedor({"cloud": None}))
        nuevo = json.loads(json.dumps(server.DEFAULT_SETTINGS))  # una instalación nueva: nada que reparar
        self.assertIsNone(server.reparar_proveedor(nuevo))
        self.assertFalse(server.proveedor_elegido(nuevo))

    def test_al_arrancar_solo_escribe_si_hay_algo(self):
        from test_carpetas import make_app
        a = make_app(self, procesado="api", cloud={"provider": "groq", "api_key": GSK})
        antes = os.stat(a.settings.path).st_mtime_ns
        time.sleep(0.02)
        self.assertIsNone(a._reparar_proveedor())
        self.assertEqual(os.stat(a.settings.path).st_mtime_ns, antes)  # nada que reparar: no se reescribe
        b = make_app(self, **json.loads(json.dumps(AZURE_DE_0D5169B)))  # copia: el Store se queda el dict
        self.assertIn("Azure", b._reparar_proveedor())
        self.assertEqual(b.settings.get()["cloud"]["provider"], "groq")
        self.assertEqual(b.settings.get()["cloud"]["api_key"], GSK)  # la clave, intacta
        with open(b.settings.path, encoding="utf-8") as f:  # y en el disco
            c = json.load(f)["cloud"]
        self.assertEqual((c["provider"], _claro(c["api_key"])), ("groq", GSK))


@unittest.skipUnless(sync_playwright, "Playwright no instalado")
class ArrancarYGuardarTests(unittest.TestCase):
    """El servidor de verdad con los dos estados de arriba en settings.json (la clave, cifrada al escribir: DPAPI en
    Windows): al arrancar pasa a Groq, Ajustes › Procesado lo enseña elegido y «Guardar» sin tocar nada lo deja en
    Groq, con la misma clave. Con 0d5169b, el primero salía como «Azure OpenAI» y «Guardar» lo dejaba en Azure."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def comprobar(self, ajustes):
        s = Servidor(ajustes=ajustes)
        self.addCleanup(s.cerrar)
        archivo = os.path.join(s.datos, "settings.json")

        def leer():
            with open(archivo, encoding="utf-8") as f:
                c = json.load(f)["cloud"]
            return c.get("provider"), _claro(c.get("api_key"))
        cfg = s.get("/api/config")
        self.assertEqual((cfg["procesado"], cfg["cloud"]["provider"], cfg["cloud"]["elegido"]), ("api", "groq", True))
        self.assertEqual(leer(), ("groq", GSK))  # reparado al arrancar, y la clave sigue siendo la suya
        with open(os.path.join(s.datos, "server.log"), encoding="utf-8") as f:
            self.assertIn("proveedor de la nube:", f.read())
        ctx = self.browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 900})
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        errores = []
        pg.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
        pg.goto(f"{s.base}/?t={s.token}", wait_until="load")
        pg.wait_for_selector("#main h1", timeout=15000)
        pg.evaluate("() => openSettings({sec: 'procesado'})")
        pg.wait_for_selector("#cProv", state="visible", timeout=10000)
        self.assertEqual(pg.input_value("#cProv"), "groq")
        antes = os.path.getmtime(archivo)
        pg.click(".aj-pie [data-m='ok']")  # «Guardar», sin haber tocado nada
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=10000)
        fin = time.time() + 10
        while time.time() < fin and os.path.getmtime(archivo) == antes:
            time.sleep(0.1)
        self.assertNotEqual(os.path.getmtime(archivo), antes, "«Guardar» no ha escrito settings.json")
        self.assertEqual(leer(), ("groq", GSK))
        self.assertEqual(s.get("/api/config")["cloud"]["provider"], "groq")
        self.assertEqual(errores, [])

    def test_proveedor_que_no_existe(self):
        self.comprobar(SIN_PROVEEDOR)

    def test_azure_sin_endpoint_que_dejaba_0d5169b(self):
        self.comprobar(AZURE_DE_0D5169B)


if __name__ == "__main__":
    unittest.main()
