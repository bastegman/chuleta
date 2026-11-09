# -*- coding: utf-8 -*-
"""Sin Groq por defecto (Bernardo, msg 1386: «Lo que no quiero es sugerir Groq a un nuevo instalador»). En una
instalación nueva lo de por defecto es «Solo en este PC» y nada sugiere ni preselecciona Groq: ni la portada, ni el
asistente, ni Ajustes. Groq sigue dentro de «API externa» para quien lo elija, y quien ya lo usa no nota nada.
El servidor de verdad y la interfaz en Chromium (como test_ui_smoke; en ui-smoke.yml, Ubuntu y Windows)."""
import json
import os
import unittest

from support import TempTree, server  # noqa: F401  (support prepara sys.path)
from test_subir_audio import Servidor

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

CLAVE = "gsk_" + "prueba" * 6  # falsa: nunca sale a ningún sitio (no se pulsa «Probar conexión» con ella)
GROQ_YA = {"procesado": "api", "preset": "nube", "live_preset": "nube",
           "cloud": {"provider": "groq", "api_key": CLAVE, "model": "whisper-large-v3"}}


class ProveedorElegidoTests(unittest.TestCase):
    def test_cuando_hay_uno_elegido(self):
        nuevo = dict(server.DEFAULT_SETTINGS)
        self.assertFalse(server.proveedor_elegido(nuevo))
        self.assertEqual(server.procesado_de(nuevo), "local")  # lo de por defecto: solo en este PC
        cloud = dict(server.DEFAULT_SETTINGS["cloud"])
        for cambio in ({"procesado": "api"}, {"cloud": dict(cloud, api_key="x")},
                       {"cloud": dict(cloud, base_url="https://api.empresa.example/v1")},
                       {"cloud": dict(cloud, azure_endpoint="https://r.openai.azure.com")},
                       {"cloud": dict(cloud, extra_keys_openai=["k"])},
                       {"cloud": dict(cloud, provider="openai")}):  # otro que el de por defecto: alguien lo eligió
            with self.subTest(cambio=cambio):
                self.assertTrue(server.proveedor_elegido(dict(nuevo, **cambio)))


class GuardarTests(TempTree):
    def app(self, **st):
        from test_carpetas import make_app
        a = make_app(self, **st)
        a.config = lambda q: {}  # lo que contesta tras guardar (aquí solo importa lo guardado)
        return a

    def test_un_proveedor_vacio_no_se_guarda(self):
        """«Elige el proveedor…» sin elegir: si llegara vacío, el motor tomaría Groq (engines: provider or "groq")."""
        a = self.app()
        a.save_settings({"cloud": {"provider": "", "chunk_minutes": 10}})
        self.assertEqual(a.settings.get()["cloud"]["provider"], "groq")  # el de los ajustes, sin tocar
        a.save_settings({"cloud": {"provider": "inventado"}})
        self.assertEqual(a.settings.get()["cloud"]["provider"], "groq")
        a.save_settings({"cloud": {"extra_keys_by": {"": ["gsk_sin_proveedor"]}}})
        self.assertEqual(a.settings.get()["cloud"].get("extra_keys") or [], [])  # no van a parar a las de Groq
        a.save_settings({"cloud": {"provider": "openai"}})
        self.assertEqual(a.settings.get()["cloud"]["provider"], "openai")

    def test_la_prueba_sin_proveedor_no_va_a_groq(self):
        """La prueba del asistente con un proveedor vacío o desconocido no va a Groq (revisor 2). Que «Guardar» y
        «Aplicar ahora» no guarden la API sin proveedor lo pone la interfaz (test_portada_asistente_y_ajustes_sin_groq):
        el servidor sigue aceptando {procesado: api} como antes, que es lo que esperan otros caminos y tests."""
        a = self.app()
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "", "api_key": "k"}}})
        self.assertEqual(st["cloud"]["provider"], "")  # el motor lo rechaza: nada sale hacia Groq
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "inventado", "api_key": "k"}}})
        self.assertEqual(st["cloud"]["provider"], "")
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "openai", "api_key": "k"}}})
        self.assertEqual(st["cloud"]["provider"], "openai")
        a.save_settings({"procesado": "api", "cloud": {"provider": "openai", "api_key": "sk-x"}})
        self.assertEqual((a.settings.get()["procesado"], a.settings.get()["cloud"]["provider"]), ("api", "openai"))

    def test_quien_ya_tiene_groq_puede_volver_a_la_api(self):
        """Con su clave de Groq guardada (y el procesado en local), «Aplicar ahora» con la API sigue como siempre."""
        a = self.app(cloud={"provider": "groq", "api_key": "gsk_x"}, procesado="local")
        a.save_settings({"procesado": "api"})
        self.assertEqual((a.settings.get()["procesado"], a.settings.get()["cloud"]["provider"]), ("api", "groq"))
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {}}})
        self.assertEqual(st["cloud"]["provider"], "groq")

    def test_el_motor_no_manda_a_groq_sin_proveedor(self):
        from support import engines
        for p in ("", "inventado"):
            with self.subTest(p=p), self.assertRaises(engines.EngineError) as cm:
                engines.CloudEngine.endpoint({"provider": p, "api_key": "k"})
            self.assertIn("Elige el proveedor", str(cm.exception))
        self.assertIn("api.groq.com", engines.CloudEngine.endpoint({"api_key": "k"})[0])  # sin la clave: como siempre


@unittest.skipUnless(sync_playwright, "playwright no instalado")
class InstalacionNuevaTests(unittest.TestCase):
    """Una instalación nueva (sin settings.json) y otra que ya usa Groq, en Chromium."""

    @classmethod
    def setUpClass(cls):
        cls.nueva = Servidor()
        cls.groq = Servidor(ajustes=GROQ_YA)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.art = os.environ.get("NOTAS_ARTEFACTOS")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.nueva.cerrar()
            cls.groq.cerrar()

    def pagina(self, s):
        ctx = self.browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 900})
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        self.errores = []
        pg.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        pg.on("console", lambda m: self.errores.append(m.text) if m.type == "error" else None)
        pg.goto(f"{s.base}/?t={s.token}", wait_until="load")
        pg.wait_for_selector("#main h1", timeout=15000)
        return pg

    def captura(self, pg, nombre):
        if self.art:
            os.makedirs(self.art, exist_ok=True)
            pg.screenshot(path=os.path.join(self.art, f"singroq_{nombre}.png"))

    def visible(self, pg):
        """El texto que se ve, sin las listas desplegables (sus opciones son la lista de proveedores, también Groq)."""
        return pg.evaluate("() => { const st = document.createElement('style');"
                           " st.textContent = 'select{display:none!important}'; document.head.append(st);"
                           " const t = document.body.innerText; st.remove(); return t }")

    def sin_groq(self, pg, donde):
        self.assertNotIn("groq", self.visible(pg).lower(), donde)

    def sin_errores(self):
        self.assertEqual([e for e in self.errores if "Failed to load resource" not in e], [])

    def test_portada_asistente_y_ajustes_sin_groq(self):
        self.assertFalse(self.nueva.get("/api/config")["cloud"]["elegido"])
        pg = self.pagina(self.nueva)
        pg.wait_for_selector(".card.welcome", timeout=10000)
        self.sin_groq(pg, "portada")
        self.captura(pg, "01_portada")
        # el asistente: «Solo en este PC» marcado; «API externa» sin nombrar a nadie
        pg.click("[data-act='cfg-abrir']")
        pg.wait_for_selector(".wz-opts", timeout=10000)
        self.assertTrue(pg.locator("input[name='wzModo'][value='local']").is_checked())
        self.sin_groq(pg, "asistente, paso 1")
        pg.check("input[name='wzModo'][value='api']")
        self.sin_groq(pg, "asistente, paso 1 con API")
        pg.click(".wz-foot [data-wz='sig']")
        pg.wait_for_selector("#wzProv", timeout=5000)
        self.assertEqual(pg.input_value("#wzProv"), "")  # ninguno preseleccionado
        self.assertEqual(pg.locator(".wz-body h2").text_content(), "Elige el proveedor y pega su clave")
        opciones = pg.eval_on_selector_all("#wzProv option", "os => os.map(o => o.value)")
        self.assertEqual(opciones[0], "")
        self.assertIn("groq", opciones)  # sigue disponible para quien lo elija
        self.assertEqual(opciones[-1], "custom")  # la API de tu empresa, al final
        self.assertEqual(opciones[1:-1], sorted(opciones[1:-1], key=lambda k: self.nueva.get("/api/config")
                                                ["providers"][k]["label"].lower()))
        self.sin_groq(pg, "asistente, paso 2")
        self.captura(pg, "02_asistente_proveedor")
        pg.click(".wz-foot [data-wz='sig']")
        self.assertEqual(pg.locator("[data-wz-err]").text_content(), "Elige el proveedor para seguir.")
        pg.select_option("#wzProv", "openai")
        pg.wait_for_selector("#wzKey", timeout=5000)
        self.assertIn("OpenAI", pg.locator(".wz-body h2").text_content())
        pg.click(".wz-foot [data-wz='atras']")
        pg.click(".wz-foot [data-wz='cerrar']")
        # Ajustes › Procesado: «API externa» sin nombres y sin proveedor elegido; probar pide elegirlo antes
        pg.evaluate("() => openSettings({sec: 'procesado'})")
        pg.wait_for_selector("#cProv", state="attached", timeout=10000)
        self.sin_groq(pg, "Ajustes › Procesado")
        pg.check("input[name='proc'][value='api']")
        pg.wait_for_selector("#cProv", state="visible", timeout=5000)
        self.assertEqual(pg.input_value("#cProv"), "")
        self.sin_groq(pg, "Ajustes › Procesado con API")
        self.captura(pg, "03_ajustes_api")
        pg.click("#cTest")
        self.assertEqual(pg.locator("#cTestRes").text_content(), "Elige antes el proveedor.")
        pg.click(".aj-nav [data-sec='avanzado']")  # lo de la nube en Avanzado (claves adicionales), tampoco
        self.sin_groq(pg, "Ajustes › Avanzado con API")
        self.assertTrue(pg.locator("#dkAdd").is_disabled())  # sin proveedor, no hay de quién añadir claves
        # «Guardar» con «API externa» y sin proveedor: no se guarda (se quedaría con Groq, el de por defecto)
        pg.click(".aj-pie [data-m='ok']")
        self.assertEqual(pg.locator("#cTestRes").text_content(), "Elige antes el proveedor.")
        self.assertFalse(pg.locator("#modal").is_hidden())
        self.assertEqual(pg.evaluate("document.activeElement.id"), "cProv")
        # «Aplicar ahora» (sin cerrar Ajustes), lo mismo
        pg.click("#procAplicar")
        self.assertIn("Elige antes el proveedor", pg.locator("#procGuardado").text_content())
        self.assertEqual(pg.evaluate("document.activeElement.id"), "cProv")
        # nada se ha guardado por mirar
        self.assertFalse(self.nueva.get("/api/config")["cloud"]["elegido"])
        self.assertNotEqual(self.nueva.get("/api/config").get("procesado"), "api")
        self.assertNotRegex(self.visible(pg), r"\ba el\b")  # «las envía a tu proveedor», no «a el proveedor…»
        # el asistente, con Groq elegido a mano: el modelo es el guardado (el más preciso), no el primero de la lista
        pg.keyboard.press("Escape")
        cand = pg.evaluate("() => { abrirConfigurar({paso: 2, modo: 'api'}); W.cloud.provider = 'groq'; "
                           "const c = wzCandidata(); closeModal(false); return c }")
        self.assertEqual(cand["cloud"]["provider"], "groq")
        self.assertNotIn("model", cand["cloud"])
        self.sin_errores()

    def test_quien_ya_usa_groq_no_nota_nada(self):
        cfg = self.groq.get("/api/config")
        self.assertTrue(cfg["cloud"]["elegido"])
        self.assertEqual((cfg["procesado"], cfg["cloud"]["provider"]), ("api", "groq"))
        pg = self.pagina(self.groq)
        pg.evaluate("() => openSettings({sec: 'procesado'})")
        pg.wait_for_selector("#cProv", state="visible", timeout=10000)
        self.assertEqual(pg.input_value("#cProv"), "groq")
        self.assertIn("console.groq.com", pg.locator("#cFields").text_content())  # su clave, como siempre
        pg.keyboard.press("Escape")
        pg.evaluate("() => abrirConfigurar({paso: 2, modo: 'api'})")
        pg.wait_for_selector(".wz-body h2", timeout=5000)
        self.assertEqual(pg.locator(".wz-body h2").text_content(), "Pega la clave de Groq")
        self.assertIn("Es el que ya usas", pg.locator(".wz-body").text_content())
        pg.keyboard.press("Escape")
        with open(os.path.join(self.groq.datos, "settings.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual((st["procesado"], st["cloud"]["provider"]), ("api", "groq"))  # sin migración
        self.sin_errores()


if __name__ == "__main__":
    unittest.main()
