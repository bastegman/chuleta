# -*- coding: utf-8 -*-
"""«Subir audio › Desde el móvil» de punta a punta en Chromium: el menú de «Subir audio», la tarjeta «Recibir del
móvil», el código QR, un móvil emulado (iPhone) que abre la página y manda un audio de WhatsApp de verdad, el PC que lo
ve llegar y lo pone en la cola, otro móvil que no puede usar el mismo código, «Dejar de recibir», y una red que no es
privada. El servidor escucha para el móvil en 127.0.0.1 (CHULETA_PRUEBAS_RED_MOVIL): sin wifi ni Windows de verdad.
En ui-smoke.yml, Ubuntu y Windows."""
import os
import unittest

from test_subir_audio import FIX, Servidor

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(sync_playwright, "playwright no instalado")
class DesdeElMovilTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Servidor(env={"CHULETA_PRUEBAS_RED_MOVIL": "127.0.0.1"})
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.art = os.environ.get("NOTAS_ARTEFACTOS")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.s.cerrar()

    def contexto(self, **kw):
        ctx = self.browser.new_context(**kw)
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        pg.errores = []
        pg.on("pageerror", lambda e: pg.errores.append(f"pageerror: {e}"))
        pg.on("console", lambda m: pg.errores.append(m.text) if m.type == "error" else None)
        return pg

    def pc(self):
        pg = self.contexto(bypass_csp=True, viewport={"width": 1280, "height": 900})
        pg.goto(f"{self.s.base}/?t={self.s.token}", wait_until="load")
        pg.wait_for_selector("#main h1", timeout=15000)
        return pg

    def captura(self, pg, nombre, sel=None):
        if self.art:
            os.makedirs(self.art, exist_ok=True)
            pg.wait_for_timeout(250)  # las entradas animadas (el menú) ya del todo
            (pg.locator(sel) if sel else pg).screenshot(path=os.path.join(self.art, f"movil_{nombre}.png"))

    def sin_errores(self, *pgs):
        for pg in pgs:
            self.assertEqual([e for e in pg.errores if "Failed to load resource" not in e], [])

    def test_de_punta_a_punta(self):
        pc = self.pc()
        # el menú de «Subir audio», con el teclado
        pc.click("#btnSubir")
        pc.wait_for_selector(".sb-menu", timeout=5000)
        self.assertEqual(pc.get_attribute("#btnSubir", "aria-expanded"), "true")
        self.assertEqual(pc.evaluate("document.activeElement.dataset.sbm"), "pc")
        pc.keyboard.press("ArrowDown")
        self.assertEqual(pc.evaluate("document.activeElement.dataset.sbm"), "movil")
        self.captura(pc, "01_menu")
        pc.keyboard.press("Enter")
        pc.wait_for_selector(".dialog.mvd #rGo:not([disabled])", timeout=10000)
        self.assertEqual(pc.locator(".sb-menu").count(), 0)
        self.assertIn("Recibir del móvil", pc.locator(".dialog.mvd .gr-kicker").text_content())
        self.assertIn("Mostrar el código", pc.locator("#rGo").text_content())
        pc.click("#rGo")
        pc.wait_for_selector(".mv-qrimg svg", timeout=10000)
        url = pc.locator(".mv-url code").text_content()
        self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/m/[A-Za-z0-9_-]{22}$")
        self.assertIn("Caduca en", pc.locator(".mv-caduca").text_content())
        self.assertIn("Aún nada", pc.locator(".mv-rec").text_content())
        self.assertIn("Redes privadas", pc.locator(".mv-notas").text_content())
        self.captura(pc, "02_codigo", ".dialog.mvd")
        # el móvil: abre la página y manda un audio de WhatsApp
        movil = self.contexto(**self.pw.devices["iPhone 13"])
        movil.goto(url, wait_until="load")
        self.assertEqual(movil.locator("h1").text_content(), "Enviar a Tenca")
        self.assertIn("audio/*", movil.get_attribute("#mArchivos", "accept"))
        self.captura(movil, "03_movil")
        movil.set_input_files("#mArchivos", [os.path.join(FIX, "whatsapp.opus")])
        movil.wait_for_selector(".m-item.ok", timeout=60000)
        self.assertIn("Enviado", movil.locator(".m-item").text_content())
        self.captura(movil, "04_movil_enviado")
        pc.wait_for_selector(".mv-item.en_cola", timeout=10000)
        self.assertIn("whatsapp.opus", pc.locator(".mv-item").text_content())
        # el PC sabe que un móvil lo ha abierto, y ofrece otro código por si no era el tuyo
        self.assertIn("Un móvil ya ha abierto el código", pc.locator(".mv-conectado").text_content())
        self.assertIn("Mostrar otro código", pc.locator(".mv-conectado").text_content())
        # con el código en pantalla, soltar audios en la ventana no rompe la tarjeta: se dice por qué no
        self.assertIn("cierra antes la tarjeta del móvil", pc.evaluate("sbNoSePuedeSoltar()"))
        self.captura(pc, "05_recibido", ".dialog.mvd")
        estado = self.s.get("/api/movil/estado")
        self.assertEqual(len(estado["recibidos"]), 1)
        self.assertIn(estado["recibidos"][0]["path"], [j["path"] for j in self.s.get("/api/jobs")["jobs"]])
        # un archivo que no es un audio: lo dice el móvil y no se manda
        txt = os.path.join(self.s.tmp, "notas.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("hola")
        movil.set_input_files("#mArchivos", [txt])
        movil.wait_for_selector(".m-item.mal", timeout=5000)
        self.assertIn("No es un audio", movil.locator(".m-item.mal").text_content())
        # otro móvil, con el mismo código: no
        otro = self.contexto(**self.pw.devices["Pixel 7"])
        r = otro.goto(url)
        self.assertEqual(r.status, 409)
        # «Dejar de recibir»: el móvil ya no llega
        pc.click("[data-mv='parar']")
        pc.wait_for_selector(".mv-fin", timeout=5000)
        self.assertIn("Ya no se reciben audios", pc.locator(".mv-fin").text_content())
        self.assertFalse(self.s.get("/api/movil/estado")["activa"])
        pc.wait_for_timeout(1500)  # un sondeo después: lo recibido sigue ahí (antes, «Aún nada»)
        self.assertIn("whatsapp.opus", pc.locator(".mv-rec").text_content())
        self.assertNotIn("Aún nada", pc.locator(".mv-rec").text_content())
        self.captura(pc, "06_parado", ".dialog.mvd")
        movil.set_input_files("#mArchivos", [os.path.join(FIX, "whatsapp.opus")])
        movil.wait_for_selector(".m-item.mal >> nth=0", timeout=15000)
        self.assertRegex(movil.locator(".m-item").first.text_content(), "No llega al PC|ya no vale")
        # «Listo» cierra la tarjeta; lo recibido sigue en el árbol
        pc.click("[data-mv='listo']")
        pc.wait_for_function("() => document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        self.sin_errores(pc, otro)

    def test_cerrar_la_tarjeta_cierra_el_codigo(self):
        pc = self.pc()
        pc.evaluate("() => abrirSubir(null, {movil: true})")
        pc.wait_for_selector(".dialog.mvd #rGo:not([disabled])", timeout=10000)
        pc.click("#rGo")
        pc.wait_for_selector(".mv-qrimg svg", timeout=10000)
        self.assertTrue(self.s.get("/api/movil/estado")["activa"])
        # «Cambiar»: vuelve al plan con el código vivo, y «Volver al código» lo enseña otra vez (el mismo)
        url = pc.locator(".mv-url code").text_content()
        # la cuenta atrás no le quita el foco a quien va con el teclado
        pc.focus("[data-mv='cambiar']")
        reloj = pc.locator("[data-mv-reloj]").text_content()
        pc.wait_for_function(f"document.querySelector('[data-mv-reloj]').textContent !== {reloj!r}", timeout=5000)
        self.assertEqual(pc.evaluate("document.activeElement.dataset.mv"), "cambiar")
        pc.keyboard.press("Enter")
        self.assertIn("Volver al código", pc.locator("#rGo").text_content())
        self.assertTrue(pc.locator(".gr-plan").is_visible())
        pc.wait_for_timeout(1300)  # el sondeo sigue: «Dejar de recibir» no vuelve a salir en el plan
        self.assertTrue(pc.locator("[data-mv='parar']").is_hidden())
        # «Nuevo proyecto…» o «Editar» y de vuelta (grVolver rehace la tarjeta): el código sigue
        pc.evaluate("() => grVolver()")
        pc.wait_for_function("() => document.querySelector('#rGo span')?.textContent === 'Volver al código'",
                             timeout=10000)
        self.assertTrue(self.s.get("/api/movil/estado")["activa"])
        pc.click("#rGo")
        pc.wait_for_selector(".mv-qrimg svg", timeout=10000)
        self.assertEqual(pc.locator(".mv-url code").text_content(), url)
        # «Mostrar otro código» con doble clic: un solo código nuevo
        pc.evaluate("""() => { window.nAbrir = 0; const orig = api; window.api = (u, b) => {
            if (u === '/api/movil/abrir') window.nAbrir++; return orig(u, b); }; }""")
        self.s.post("/api/movil/cerrar", {})  # (caducado o parado: sale «Mostrar otro código»)
        pc.wait_for_selector(".mv-fin [data-mv='otro']", timeout=5000)
        pc.dblclick(".mv-fin [data-mv='otro']")
        pc.wait_for_selector(".mv-qrimg svg", timeout=10000)
        pc.wait_for_timeout(500)
        self.assertEqual(pc.evaluate("window.nAbrir"), 1)
        self.assertNotEqual(pc.locator(".mv-url code").text_content(), url)
        pc.keyboard.press("Escape")
        pc.wait_for_function("() => document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        pc.wait_for_timeout(500)
        self.assertFalse(self.s.get("/api/movil/estado")["activa"])
        self.sin_errores(pc)

    def test_cerrar_mientras_se_abre(self):
        """Esc mientras «Mostrar el código» espera al servidor (PowerShell puede tardar): no se queda abierto."""
        pc = self.pc()
        pc.evaluate("""() => { const orig = api; window.api = (u, b) => u === '/api/movil/abrir'
            ? new Promise(r => setTimeout(r, 800)).then(() => orig(u, b)) : orig(u, b); }""")
        pc.evaluate("() => abrirSubir(null, {movil: true})")
        pc.wait_for_selector(".dialog.mvd #rGo:not([disabled])", timeout=10000)
        pc.click("#rGo")
        pc.keyboard.press("Escape")
        pc.wait_for_timeout(1800)
        self.assertFalse(self.s.get("/api/movil/estado")["activa"])
        self.sin_errores(pc)

    def test_en_una_red_que_no_es_privada_no_se_ofrece(self):
        pc = self.pc()
        texto = "Tu wifi («Bar») está como pública en Windows, y en una red pública no se reciben audios del móvil."
        pc.route("**/api/movil/red", lambda r: r.fulfill(json={"ok": False, "motivo": "publica", "red": "Bar",
                                                               "texto": texto}))
        pc.evaluate("() => abrirSubir(null, {movil: true})")
        pc.wait_for_selector("#sbError:not(.hidden)", timeout=10000)
        self.assertEqual(pc.locator("#sbError").text_content(), texto)
        self.assertTrue(pc.locator("#rGo").is_disabled())
        self.captura(pc, "07_red_publica", ".dialog.mvd")
        pc.keyboard.press("Escape")
        self.assertFalse(self.s.get("/api/movil/estado")["activa"])
        self.sin_errores(pc)


if __name__ == "__main__":
    unittest.main()
