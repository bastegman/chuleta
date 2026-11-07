# -*- coding: utf-8 -*-
"""Humo de la interfaz en un navegador real (Playwright + Chromium): la app arranca, se abre con su token y lo que
Bernardo pulsa primero (⚙ Ajustes, Reuniones, Calendario, Inicio, ● Grabar) responde sin errores de JavaScript.

Hasta el 02/11/2026 ningún test abría la interfaz en un navegador: una pantalla con los botones muertos pasaba la
suite entera. Se salta si Playwright no está instalado (la CI lo instala en `ui-smoke.yml`):

    pip install playwright && python -m playwright install chromium
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from support import REPO

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - sin Playwright el test se salta
    sync_playwright = None

SERVER = os.path.join(REPO, "_app", "server.py")
# fuera de Windows la grabadora (soundcard) no existe y /api/rec/devices contesta 400: no es un fallo de la interfaz
PERMITIDAS = ("/api/rec/devices",) if sys.platform != "win32" else ()


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(sync_playwright, "playwright no instalado")
class InterfazEnNavegadorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuleta-ui-")
        cls.root = os.path.join(cls.tmp, "Chuleta")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(cls.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(cls.root, "Proyecto", "Vocabulario"))
        with open(os.path.join(cls.root, "Proyecto", "Vocabulario", "General.txt"), "w", encoding="utf-8") as f:
            f.write("término uno, término dos\n")
        cls.datos = os.path.join(cls.tmp, "datos")
        cls.port = puerto_libre()
        cls.log = open(os.path.join(cls.tmp, "salida.log"), "w", encoding="utf-8")
        # elegir un modo que transcribe en el PC empieza a bajar Whisper medium (1,5 GB): aquí, contra un puerto
        # cerrado (falla al instante); la descarga de verdad la prueba e2e-procesado.yml
        env = dict(os.environ, PYTHONIOENCODING="utf-8",
                   CHULETA_PRUEBAS_URL_MODELOS="http://127.0.0.1:9/{repo}/resolve/{rev}/{file}")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(cls.root, "_app", "server.py"), "--port",
                                     str(cls.port), "--data", cls.datos, "--no-browser"],
                                    cwd=cls.root, stdout=cls.log, stderr=subprocess.STDOUT, env=env)
        cls.base = f"http://127.0.0.1:{cls.port}"
        token_file = os.path.join(cls.datos, "sesion.token")
        fin = time.time() + 60
        cls.token = None
        while time.time() < fin and cls.proc.poll() is None:
            if os.path.isfile(token_file):
                with open(token_file, encoding="utf-8") as f:
                    cls.token = f.read().strip()
                try:
                    req = urllib.request.Request(cls.base + "/api/ping", headers={"X-Chuleta-Token": cls.token})
                    if json.load(urllib.request.urlopen(req, timeout=2)).get("app") == "audios":
                        break
                except Exception:
                    pass
            time.sleep(0.3)
        else:
            cls.tearDownClass()
            raise unittest.SkipTest("el servidor de prueba no arrancó (ver salida.log)")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        for attr in ("browser", "pw"):
            try:
                getattr(cls, attr).stop() if attr == "pw" else getattr(cls, attr).close()
            except Exception:
                pass
        if getattr(cls, "proc", None) and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        try:
            cls.log.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pagina(self):
        # cookies propias (la sesion de un test no vale para el siguiente) y sin la CSP de la pagina, que bloquea el
        # eval con el que Playwright evalua las expresiones de wait_for_function (la CSP la prueba test_seguridad)
        ctx = self.browser.new_context(bypass_csp=True)
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        errores, red = [], []
        pg.on("console", lambda m: errores.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
        pg.on("response", lambda r: red.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        pg.on("requestfailed", lambda r: red.append(f"fallo {r.url}"))
        self.addCleanup(pg.close)
        return pg, errores, red

    def limpiar(self, errores, red):
        """Errores que cuentan: todo menos los 4xx permitidos (y su eco en la consola)."""
        red = [r for r in red if not any(p in r for p in PERMITIDAS)]
        err = [e for e in errores if not (not red and "Failed to load resource" in e)]
        return err, red

    def test_arranque_y_lo_primero_que_se_pulsa(self):
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        self.assertEqual(pg.url, self.base + "/")  # ?t= entrega la cookie y redirige
        self.assertEqual(pg.title(), "Chuleta")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal:not(.hidden) h3:has-text('Ajustes')", timeout=5000)
        # «Arrancar con Windows» (bandeja): sin la app instalada (aquí, y fuera de Windows) la casilla sale desactivada
        # con su nota; la API de verdad contesta (GET /api/inicio-windows)
        pg.wait_for_selector("#modal #sInicioWin", state="attached", timeout=5000)
        nota = "document.querySelector('#sInicioWinNota').textContent.includes('solo con la app instalada')"
        pg.wait_for_function(nota, timeout=10000)
        self.assertTrue(pg.evaluate("document.querySelector('#sInicioWin').disabled"))
        # «Donde se procesan tus reuniones»: las tres opciones, y cada una ensena lo suyo (02/11/2026)
        for v in ("local", "agente", "api"):
            pg.wait_for_selector(f"#modal input[name='proc'][value='{v}']", state="attached", timeout=5000)
        self.assertEqual(pg.evaluate("document.querySelector('#modal input[name=proc]:checked').value"), "local")
        self.assertTrue(pg.evaluate("document.querySelector('#procApi').hidden"))
        self.assertTrue(pg.evaluate("document.querySelector('#procAgente').hidden"))
        pg.check("#modal input[name='proc'][value='agente']")
        pg.wait_for_function("!document.querySelector('#procAgente').hidden", timeout=5000)
        self.assertIn("Comprobar", pg.inner_text("#procAgente"))
        self.assertIn("se envía a Anthropic", pg.inner_text("#modal"))
        pg.check("#modal input[name='proc'][value='api']")
        pg.wait_for_function("!document.querySelector('#procApi').hidden", timeout=5000)
        self.assertTrue(pg.evaluate("document.querySelector('#procAgente').hidden"))
        self.assertIn("Transcripción en la nube", pg.inner_text("#procApi"))
        pg.keyboard.press("Escape")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        pg.click("[data-nav='r']")
        pg.wait_for_function("location.hash === '#/r/'", timeout=5000)
        pg.wait_for_selector("#main .page", timeout=5000)
        pg.click("[data-nav='c']")
        pg.wait_for_function("location.hash === '#/c/'", timeout=5000)
        pg.click("[data-nav='home']")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=5000)
        pg.click("#btnRec")
        pg.wait_for_selector("#modal:not(.hidden) .dialog.gr", timeout=5000)
        self.assertIn("Nueva grabación", pg.text_content("#modal"))
        pg.wait_for_selector(".dialog.gr [data-fila='carpeta'] .gr-v code", timeout=15000)  # el plan del servidor
        pg.keyboard.press("Escape")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        self.assertTrue(pg.evaluate("document.querySelector('#meetChip').classList.contains('hidden')"))
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], err)
        self.assertEqual(red, [], red)

    def test_asistente_configurar_tres_pasos_y_probar_sin_guardar(self):
        """«Configurar Chuleta» (07/11/2026): tres pasos, «Probar» de verdad (aquí falla en la descarga del modelo,
        contra un puerto cerrado, con su frase y su botón) y nada se guarda hasta terminar."""
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        antes = pg.evaluate("api('/api/config')")["procesado"]
        pg.evaluate("abrirConfigurar()")
        pg.wait_for_selector(".dialog.wz h2:has-text('¿Dónde se procesan tus reuniones?')", timeout=5000)
        pg.check("input[name='wzModo'][value='agente']")
        self.assertIn("se envía a Anthropic", pg.inner_text(".dialog.wz"))
        pg.click("[data-wz='sig']")
        pg.wait_for_selector(".dialog.wz h2:has-text('Conecta Claude Code')", timeout=5000)
        self.assertIn("irm https://claude.ai/install.ps1 | iex", pg.inner_text(".dialog.wz"))
        pg.click("[data-wz='atras']")
        pg.check("input[name='wzModo'][value='local']")
        pg.click("[data-wz='sig']")
        pg.wait_for_selector(".dialog.wz h2:has-text('de transcripción')", timeout=5000)  # el modelo o los modelos
        pg.click("[data-wz='sig']")
        pg.wait_for_selector(".dialog.wz h2:has-text('Prueba que todo funciona')", timeout=5000)
        pg.click(".wz-probar [data-wz='probar']")
        # en la CI falla la descarga (puerto cerrado: «Reintentar»); en un PC con los modelos ya bajados y sin Whisper
        # instalado, la transcripción («Copiar diagnóstico»). En los dos casos: una frase y su botón
        pg.wait_for_selector(".wz-res.err [data-wz-accion]", timeout=90000)
        self.assertTrue(pg.inner_text(".wz-res.err").strip())
        self.assertTrue(pg.is_disabled("[data-wz='terminar']"))
        self.assertEqual(pg.evaluate("api('/api/config')")["procesado"], antes)  # una prueba no guarda nada
        pg.keyboard.press("Escape")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        # Ajustes por secciones: «Aplicar ahora» sigue aplicando (el 07/11/2026 una variable «api» tapó la función
        # api() en todo Ajustes y solo lo vio la e2e de Windows) y la sección elegida se recuerda
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal:not(.hidden) .aj-nav", timeout=5000)
        otro = "agente" if antes != "agente" else "local"
        pg.check(f"#modal input[name='proc'][value='{otro}']")
        pg.click("#procAplicar")
        pg.wait_for_function("document.querySelector('#procGuardado').innerText.includes('Aplicado')", timeout=10000)
        self.assertEqual(pg.evaluate("api('/api/config')")["procesado"], otro)
        pg.check(f"#modal input[name='proc'][value='{antes}']")
        pg.click("#procAplicar")
        pg.wait_for_function("document.querySelector('#procGuardado').innerText.includes('Aplicado')", timeout=10000)
        pg.click(".aj-nav [data-sec='avanzado']")
        pg.fill("#sThreads", "2")
        pg.wait_for_function("document.querySelector('.aj-pend').innerText.includes('sin guardar')", timeout=5000)
        pg.click("#modal [data-m='ok']")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["threads"], 2)  # guardado desde una sección oculta al abrir
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal:not(.hidden) .aj-nav [data-sec='avanzado'].on", timeout=5000)  # la última vista
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [])
        self.assertEqual(red, [])

    def test_tarjeta_de_grabar(self):
        """La tarjeta «Así se va a grabar» (rediseño B1, 07/11/2026): cabe en 900×600, cada línea dice de dónde sale,
        la reunión que llega tarde no pisa lo que has escrito (P2-30, D4), «Usarlo en» guarda para el proyecto y
        «Empezar a grabar» manda a /api/rec/start exactamente el plan que se ve."""
        pg, errores, red = self.pagina()
        pg.set_viewport_size({"width": 900, "height": 600})
        devs = {"mics": [{"id": "m1", "name": "Micro de prueba", "default": True}], "mic": "default", "sys": "all",
                "outputs": [{"id": "o1", "name": "Altavoces de prueba", "default": True}]}
        pg.route("**/api/rec/devices*", lambda r: r.fulfill(json=devs))
        pg.route("**/api/rec/monitor", lambda r: r.fulfill(json={"ok": True}))  # sin grabadora de verdad
        retenidas, enviados = [], []
        pg.route("**/api/reunion/actual", lambda r: retenidas.append(r))  # la reunión «tarda» (Outlook)

        def start(route):
            enviados.append(json.loads(route.request.post_data))
            route.fulfill(json={"error": "grabadora de prueba"}, status=400)  # no se graba: solo se mira el cuerpo
        pg.route("**/api/rec/start", start)
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnRec")
        pg.wait_for_selector(".dialog.gr .gr-cargando", timeout=5000)  # «Buscando la reunión de Teams…»
        # no espera a la reunión más de ~2 s: se pinta el plan igualmente
        pg.wait_for_selector(".dialog.gr [data-fila='carpeta'] .gr-v code", timeout=8000)
        pg.wait_for_function("!document.querySelector('#rGo').disabled", timeout=5000)
        vis = pg.evaluate("[...document.querySelectorAll('.gr-fila:not(.hidden)')].map(f => f.dataset.fila)")
        self.assertEqual(vis, ["carpeta", "idioma", "vocab", "transcribe", "resume", "audio"])
        for f in vis:
            self.assertTrue(pg.text_content(f".gr-fila[data-fila='{f}'] .gr-origen").strip(), f)
        b = pg.evaluate("(() => { const r = document.querySelector('#rGo').getBoundingClientRect();"
                        " return [r.top, r.bottom, innerHeight]; })()")
        self.assertTrue(0 <= b[0] and b[1] <= b[2], b)  # «Empezar a grabar» a la vista en 900×600
        # escribes un nombre; luego llega la reunión de Teams: se propone, no se pisa
        pg.fill("#grNombre", "Mi reunión")
        sug = {"name": "Comité", "source": "teams", "vocab": "", "participants": []}
        retenidas[0].fulfill(json={"teams": {"title": "Comité"}, "outlook": None, "reason": None, "sugerencia": sug})
        pg.wait_for_selector("#grPropuesta:not(.hidden)", timeout=5000)
        self.assertIn("Teams dice «Comité»", pg.inner_text("#grPropuesta"))
        self.assertEqual(pg.input_value("#grNombre"), "Mi reunión")
        # proyecto y, con «Usarlo en», el idioma para todas sus reuniones
        pg.select_option("#grProy", "Proyecto/Vocabulario/General.txt")
        pg.wait_for_function("document.querySelector('[data-fila=vocab] .gr-origen').textContent === 'del workstream'",
                             timeout=5000)
        pg.click("[data-gr-cambiar='idioma']")
        pg.select_option("[data-panel='idioma'] select", "pt")
        pg.wait_for_function("document.querySelector('[data-fila=idioma] .gr-origen').classList.contains('cambiado')",
                             timeout=5000)
        pg.check("input[name='grAlc_idioma'][value='proyecto']")
        pg.click("[data-gr-listo='idioma']")
        pg.wait_for_function("document.querySelector('[data-fila=idioma] .gr-origen').textContent === 'de Proyecto'",
                             timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["project_langs"], {"Proyecto": "pt"})
        # lo que se ve es lo que se usa: el cuerpo de /api/rec/start es el plan pintado
        plan = pg.evaluate("G.plan")
        pg.click("#rGo")
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('grabadora de prueba')",
                             timeout=5000)
        self.assertEqual(enviados[0], dict(plan["rec"], resumen=plan["resumen"]))
        self.assertEqual((enviados[0]["name"], enviados[0]["language"], enviados[0]["vocab"]),
                         ("Mi reunión", "pt", "Proyecto/Vocabulario/General.txt"))
        pg.evaluate("api('/api/grabar/recordar', {clave: 'idioma', valor: null, alcance: 'proyecto',"
                    " vocab: 'Proyecto/Vocabulario/General.txt'})")  # deja los ajustes como estaban
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, [r for r in red if "/api/rec/start" not in r])
        self.assertEqual([e for e in err if "grabadora de prueba" not in e and "400" not in e], [])
        self.assertEqual(red, [])

    def test_sin_token_no_hay_sesion(self):
        """La pagina se sirve, pero sin la cookie la API contesta 403 y la pagina lo dice (no una pantalla muerta)."""
        pg, _, _ = self.pagina()
        r = pg.goto(self.base + "/", wait_until="load")
        self.assertEqual(r.status, 200)
        pg.wait_for_selector("#main:has-text('Sesión no válida')", timeout=10000)
        self.assertIn("acceso directo", pg.inner_text("#main"))

    def test_version_anterior_abierta_se_dice(self):
        """La pagina de esta version con la API de una anterior (sin app_name ni perfil): un aviso claro, no botones
        muertos."""
        pg, errores, red = self.pagina()

        def vieja(route):
            r = route.fetch()
            d = r.json()
            d.pop("app_name", None)
            d.pop("perfil", None)
            d["version"] = "30/10/2026 18:37"
            route.fulfill(response=r, json=d)
        pg.route("**/api/config", vieja)
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main:has-text('versión anterior')", timeout=10000)
        self.assertIn("30/10/2026", pg.inner_text("#main"))
        pg.click("[data-nav='r']")  # los clics no revientan: la ruta espera a la configuracion
        time.sleep(0.5)
        self.assertEqual(self.limpiar(errores, red)[0], [])

    def test_servidor_lento_se_dice_y_los_botones_no_revientan(self):
        """/api/config tarda (02/11/2026: 8 minutos por una importacion): la pagina dice que espera, con los segundos,
        y pulsar ⚙ o Reuniones da un aviso o nada, nunca un error de JavaScript. La peticion se retiene en Playwright
        (sin dormir en el manejador, que bloquearia su bucle) y se suelta al final: se pinta la ruta elegida."""
        pg, errores, red = self.pagina()
        retenidas = []
        pg.route("**/api/config", lambda route: retenidas.append(route))  # retenida hasta soltarla
        pg.goto(f"{self.base}/?t={self.token}", wait_until="commit")
        pg.wait_for_selector("#main:has-text('tarda en contestar')", timeout=10000)
        self.assertEqual(len(retenidas), 1)
        pg.click("#btnSettings")
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('arrancando')", timeout=5000)
        pg.click("[data-nav='r']")
        time.sleep(0.5)
        self.assertIn("tarda en contestar", pg.inner_text("#main"))  # sigue esperando, sin reventar
        retenidas[0].continue_()
        pg.wait_for_selector("#main .page:has-text('Reuniones')", timeout=15000)  # al llegar la configuracion,
        self.assertEqual(pg.evaluate("location.hash"), "#/r/")                     # se pinta la ruta elegida
        self.assertEqual(self.limpiar(errores, red)[0], [])


if __name__ == "__main__":
    unittest.main()
