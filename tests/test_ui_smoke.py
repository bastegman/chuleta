# -*- coding: utf-8 -*-
"""Humo de la interfaz en un navegador real (Playwright + Chromium): la app arranca, se abre con su token y lo que
Bernardo pulsa primero (⚙ Ajustes, Reuniones, Calendario, Inicio, ● Grabar) responde sin errores de JavaScript.

Hasta el 02/11/2026 ningún test abría la interfaz en un navegador: una pantalla con los botones muertos pasaba la
suite entera. Se salta si Playwright no está instalado (la CI lo instala en `ui-smoke.yml`):

    pip install playwright && python -m playwright install chromium
"""
import importlib.util
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
# sin soundcard (fuera de Windows, y en ui-smoke.yml también en Windows: no lo instala) la grabadora no lista los
# dispositivos y /api/rec/devices contesta 400: no es un fallo de la interfaz. Hasta el 08/11/2026 se decidía por el
# sistema y en Windows no se permitía: pasaba porque nadie esperaba esa respuesta (el diálogo de grabar se cerraba
# antes de que llegara); Ajustes › Al grabar la pide al abrirse y espera lo bastante (f5366aa, run 42421762235)
PERMITIDAS = ("/api/rec/devices",) if importlib.util.find_spec("soundcard") is None else ()


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
        # una reunión ya grabada, para el autocompletado de la tarjeta de grabar (su proyecto sale de su cabecera)
        base = os.path.join(cls.root, "Proyecto", "2026-10-30 10-00-00 - Comité de prueba")
        with open(base + ".wav", "wb") as f:
            f.write(b"RIFF" + (36 + 32000).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
                    + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
                    + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
                    + b"data" + (32000).to_bytes(4, "little") + b"\0" * 32000)
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write("---\nproyecto: Proyecto\nworkstream: General\n---\n")
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
        # Ajustes abre en «Al grabar», con el plan del servidor arriba (rediseño B2, 07/11/2026)
        pg.wait_for_selector(".aj-nav [data-sec='algrabar'].on", timeout=5000)
        pg.wait_for_selector("#agFilas .gr-fila[data-fila='carpeta']", timeout=10000)
        self.assertIn("Así se graba ahora", pg.text_content("[data-secc='algrabar']"))
        pg.click(".aj-nav [data-sec='procesado']")
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
        self.assertEqual(err, [], (err, red))
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
        pg.click(".aj-nav [data-sec='procesado']")
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
        self.assertEqual(err, [], (err, red))
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
        # el autocompletado: Intro elige la reunión y trae su proyecto, pero no empieza a grabar (revisión de B1)
        pg.fill("#grNombre", "")
        pg.type("#grNombre", "Comit")
        pg.wait_for_selector("#grSug:not(.hidden) [data-sug-titulo]", timeout=5000)
        pg.keyboard.press("ArrowDown")
        pg.keyboard.press("Enter")
        pg.wait_for_function("document.querySelector('#grMotivo').textContent.includes('como la última')", timeout=5000)
        self.assertEqual(pg.input_value("#grNombre"), "Comité de prueba")
        self.assertEqual(pg.input_value("#grProy"), "Proyecto/Vocabulario/General.txt")
        self.assertTrue(pg.is_visible("#modal .dialog.gr"))
        self.assertEqual(enviados, [])
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

    def test_ajustes_al_grabar_y_proyectos(self):
        """Ajustes (rediseño B2, 07/11/2026): abre en «Al grabar» con el plan del servidor arriba, que cambia con lo
        escrito aunque no se haya guardado; «Proyectos» guarda el idioma de un proyecto en su fila y lo dice como suyo;
        «Calendario y Teams» dice el estado de Teams en una frase."""
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila[data-fila='idioma']", timeout=10000)
        if PERMITIDAS:  # sin grabadora la lista de dispositivos no llega: una frase, no el error de Python
            pg.wait_for_selector("#agDevsAviso.gr-aviso", timeout=30000)
            aviso = pg.inner_text("#agDevsAviso")
            self.assertIn("No se ha podido leer la lista de micrófonos", aviso)
            self.assertNotIn("Traceback", aviso)
            self.assertNotIn("soundcard", aviso)
        antes = pg.evaluate("api('/api/config')")["language"]
        otro = "pt" if antes != "pt" else "de"
        pg.select_option("#sLang", otro)
        nombre = {"pt": "Portugués", "de": "Alemán"}[otro]
        fila = "document.querySelector('#agFilas [data-fila=idioma] .gr-v').textContent"
        pg.wait_for_function(f"{fila}.includes('{nombre}')", timeout=5000)
        self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
        self.assertEqual(pg.evaluate("api('/api/config')")["language"], antes)  # aún no
        pg.select_option("#sLang", antes)  # se deja como estaba
        # el plan vuelve a lo de antes antes de seguir: nada de lo de abajo depende de una petición que llegue tarde
        pg.wait_for_function(f"!{fila}.includes('{nombre}')", timeout=5000)
        pg.click(".aj-nav [data-sec='proyectos']")
        pg.wait_for_selector("#pjTabla [data-pj-editar='Proyecto']", timeout=10000)
        # la carpeta, del mismo cálculo que la tarjeta: sin asignar, la suya del árbol (11a′), en gris
        celda = "document.querySelector('#pjTabla tr[data-pj=\"Proyecto\"] td:nth-child(2)')?.innerText || ''"
        pg.wait_for_function(f"({celda}).includes('su carpeta')", timeout=10000)
        self.assertIn("Proyecto\\", pg.evaluate(celda))
        self.assertNotIn("la última usada", pg.evaluate(celda))
        pg.click("#pjTabla [data-pj-editar='Proyecto']")
        self.assertTrue(pg.evaluate("document.activeElement.matches('#pjTabla tr.pj-ed select')"))  # foco en la fila
        self.assertIn("Sin asignar (su carpeta", pg.inner_text("#pjTabla [data-pj-f]"))
        pg.select_option("#pjTabla [data-pj-l]", "en")
        # se guarda al momento, sin ningún «Guardar» (revisión de B2: el de abajo lo perdía)
        pg.wait_for_function("document.querySelector('#pjTabla .pj-estado')?.textContent === 'Guardado'", timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["project_langs"], {"Proyecto": "en"})
        pg.click("#pjTabla [data-pj-no]")  # «Listo»
        fila_p = "document.querySelector('#pjTabla tr[data-pj=\"Proyecto\"]')?.innerText"
        pg.wait_for_function(f"{fila_p}.includes('Solo inglés')", timeout=5000)
        self.assertTrue(pg.evaluate("document.activeElement.matches(\"[data-pj-editar='Proyecto']\")"))
        pg.click(".aj-nav [data-sec='algrabar']")
        origen = "document.querySelector('#agFilas [data-fila=idioma] .gr-origen').textContent"
        pg.wait_for_function(f"{origen} === 'de Proyecto'", timeout=5000)
        pg.click(".aj-nav [data-sec='calendario']")
        self.assertTrue(pg.inner_text("#ctTeamsEstado").strip())
        # Outlook una sola vez. Con fix-calendario, su estado lo pinta reunion.js (#rnEstado) y la fila de aquí se
        # retira; sin ella, la fila con su estado, «Activar» y «Desactivar» (la casilla de reunion.js no se enseña dos
        # veces) y «Probar» en la fila. La ayuda larga, plegada
        if pg.evaluate("!!document.querySelector('#rnEstado')"):
            self.assertFalse(pg.is_visible("#ctOutFila"))
            self.assertTrue(pg.is_visible("#rnEstado"))
        else:
            self.assertFalse(pg.is_visible("#rnOutlook"))
            self.assertEqual(pg.inner_text("#ctOutEstado"), "No conectado")
            self.assertFalse(pg.is_visible("#rnProbar"))  # «Probar» es de Outlook: con Outlook activado, en su fila
            pg.click("#ctOutAcc [data-ct-outlook='1']")
            self.assertTrue(pg.is_checked("#rnOutlook"))
            self.assertEqual(pg.inner_text("#ctOutEstado"), "Activado")
            self.assertTrue(pg.is_visible("#ctOutFila #rnProbar"))
            self.assertIn("Se activa al pulsar Guardar", pg.inner_text("#ctOutFrase"))
            pg.click("#ctOutAcc [data-ct-outlook='0']")
            self.assertFalse(pg.is_checked("#rnOutlook"))
        ayuda = "[data-secc='calendario'] fieldset details.ag-como .help"
        self.assertTrue(pg.evaluate(f"!!document.querySelector(\"{ayuda}\")"))  # la ayuda larga existe, plegada
        self.assertFalse(pg.is_visible(ayuda))
        # el aviso flotante depende de «Llamada detectada» (Al grabar): apagada, no se puede marcar
        self.assertFalse(pg.is_disabled("#rnAviso"))
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.uncheck("#lMeet")
        pg.click(".aj-nav [data-sec='calendario']")
        self.assertTrue(pg.is_disabled("#rnAviso"))
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.check("#lMeet")
        pg.evaluate("api('/api/grabar/recordar', {clave: 'idioma', valor: null, alcance: 'proyecto',"
                    " vocab: 'Proyecto/Vocabulario/General.txt'})")
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [])

    def test_proyectos_lo_de_una_fila_no_se_pierde(self):
        """Revisión de B2 (09/11/2026, P0-1): una fila editada y el «Guardar» de abajo justo después. Antes la fila solo
        se guardaba con su propio «Guardar» y el de abajo decía «Ajustes guardados» sin ella; ahora se guarda al
        momento. Y la carpeta de un workstream también se elige en su fila."""
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.click(".aj-nav [data-sec='proyectos']")
        pg.wait_for_selector("#pjTabla [data-pj-editar='Proyecto']", timeout=10000)
        pg.click("#pjTabla [data-pj-editar='Proyecto']")
        pg.select_option("#pjTabla [data-pj-l]", "pt")
        pg.click("#modal [data-m='ok']")  # el «Guardar» de abajo, enseguida
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        pg.wait_for_function("api('/api/config').then(c => c.project_langs?.Proyecto === 'pt')", timeout=5000)
        # la carpeta de un workstream
        ws = "Proyecto/Vocabulario/General.txt"
        pg.click("#btnSettings")
        pg.click(".aj-nav [data-sec='proyectos']")
        pg.wait_for_selector(f"#pjTabla [data-pjw-editar='{ws}']", timeout=10000)
        pg.click(f"#pjTabla [data-pjw-editar='{ws}']")
        self.assertIn("La del proyecto", pg.inner_text("#pjTabla tr.pj-ed [data-pj-f] option"))
        pg.select_option("#pjTabla tr.pj-ed [data-pj-f]", "Proyecto")
        pg.wait_for_function("document.querySelector('#pjTabla .pj-estado')?.textContent === 'Guardado'", timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["workstream_folders"], {ws: "Proyecto"})
        pg.click("#pjTabla [data-pj-no]")
        celda = f"document.querySelector('#pjTabla tr[data-pjw=\"{ws}\"] td:nth-child(2)').innerText"
        pg.wait_for_function(f"!{celda}.includes('la del proyecto')", timeout=5000)
        for body in ({"clave": "idioma", "valor": None, "alcance": "proyecto", "vocab": ws},
                     {"clave": "carpeta", "valor": None, "alcance": "workstream", "vocab": ws}):
            pg.evaluate("b => api('/api/grabar/recordar', b)", body)  # deja los ajustes como estaban
        self.assertEqual(pg.evaluate("api('/api/config')")["workstream_folders"], {})
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_lo_no_guardado_errores_y_foco(self):
        """Revisión de B2 (09/11/2026): al abrir, el foco va a la sección y el plan no se desplaza fuera de la vista
        (900×600); el plan cambia con el motor del directo elegido en Procesado sin guardar, y «Transcribe» dice que
        sale de allí; sin micrófono y sin audio del PC lo avisa y no deja guardarlo; si el plan falla, una frase con
        «Probar otra vez» (en Al grabar y en Proyectos), sin el texto técnico."""
        pg, errores, red = self.pagina()
        pg.set_viewport_size({"width": 900, "height": 600})
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila[data-fila='transcribe']", timeout=10000)
        self.assertTrue(pg.evaluate("document.activeElement.matches('.aj-nav [data-sec].on')"))
        self.assertEqual(pg.evaluate("document.querySelector('.aj-main').scrollTop"), 0)
        cfg = pg.evaluate("api('/api/config')")
        actual = cfg["live_preset"]
        otro = next(p for p in cfg["presets"] if p["id"] != actual and p["engine"] == "local")
        pg.click(".aj-nav [data-sec='procesado']")
        pg.select_option("#lPreset", otro["id"])
        pg.click(".aj-nav [data-sec='algrabar']")
        t = "document.querySelector('#agFilas [data-fila=transcribe]').innerText"
        pg.wait_for_function(f"{t}.includes({json.dumps(otro['live_label'].split(' · ')[0])})", timeout=5000)
        self.assertIn("Ajustes › Procesado", pg.inner_text("#agFilas [data-fila='transcribe']"))
        pg.click(".aj-nav [data-sec='procesado']")
        pg.select_option("#lPreset", actual)
        pg.click(".aj-nav [data-sec='algrabar']")
        # sin micrófono y sin el audio del PC
        pg.select_option("#agMic", "none")
        pg.select_option("#agSys", "none")
        audio = "document.querySelector('#agFilas [data-fila=audio]').innerText"
        pg.wait_for_function(f"{audio}.includes('Así no se graba nada')", timeout=5000)
        pg.click("#modal [data-m='ok']")
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('no se graba nada')", timeout=5000)
        self.assertTrue(pg.is_visible("#modal .aj-nav"))  # Ajustes sigue abierto, con lo escrito
        c2 = pg.evaluate("api('/api/config')")
        self.assertEqual((c2["rec_mic"], c2["rec_sys"]), (cfg["rec_mic"], cfg["rec_sys"]))
        pg.select_option("#agMic", "default")
        pg.select_option("#agSys", "all")
        # el plan falla: una frase y «Probar otra vez», sin el error de Python
        tecnico = {"error": "'NoneType' object has no attribute 'get'"}
        pg.route("**/api/grabar/plan", lambda r: r.fulfill(status=500, json=tecnico))
        pg.select_option("#sLang", "de")
        pg.wait_for_selector("#agFilas [data-ag-reintentar]", timeout=5000)
        self.assertIn("No se ha podido calcular", pg.inner_text("#agFilas"))
        self.assertNotIn("NoneType", pg.inner_text("#agFilas"))
        pg.click(".aj-nav [data-sec='proyectos']")
        pg.wait_for_selector("#pjTabla [data-pj-reintentar]", timeout=5000)
        self.assertNotIn("sin asignar", pg.inner_text("#pjTabla"))  # no una tabla que parezca correcta
        pg.unroute("**/api/grabar/plan")
        pg.click("#pjTabla [data-pj-reintentar]")
        celda = "document.querySelector('#pjTabla tr[data-pj=\"Proyecto\"] td:nth-child(2)')?.innerText || ''"
        pg.wait_for_function(f"({celda}).includes('su carpeta')", timeout=10000)
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, [r for r in red if "/api/grabar/plan" not in r and "/api/settings" not in r])
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_micro_sin_guardar_y_listas_que_cambian(self):
        """Revisión de B2 (09/11/2026): un micrófono elegido sin guardar se conserva al volver de un subdiálogo aunque
        la lista de dispositivos llegue después; y si cambian las listas de vocabulario (p. ej. al conectar una carpeta,
        rama vocab-carpeta), la tabla de Proyectos y el selector de «Así se graba ahora» se rehacen."""
        pg, errores, red = self.pagina()
        devs = {"mics": [{"id": "m1", "name": "Micro uno", "default": True},
                         {"id": "m2", "name": "Micro dos", "default": False}],
                "outputs": [{"id": "o1", "name": "Altavoces", "default": True}], "mic": "default", "sys": "all"}
        pendientes = []
        pg.route("**/api/rec/devices*", lambda r: pendientes.append(r))
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.wait_for_function("document.querySelector('#agFilas .gr-fila')", timeout=10000)
        pendientes.pop(0).fulfill(json=devs)
        pg.wait_for_selector("#agMic option[value='m2']", state="attached", timeout=5000)
        pg.select_option("#agMic", "m2")
        pg.wait_for_function("document.querySelector('.aj-pend').innerText.includes('sin guardar')", timeout=5000)
        ws = "Proyecto/Vocabulario/General.txt"
        pg.click(".aj-nav [data-sec='proyectos']")
        pg.click(f"#pjTabla [data-pjw-editar='{ws}']")
        pg.click("#pjTabla [data-pj-lista]")  # el editor de la lista: Ajustes se cierra
        pg.wait_for_selector("#modal textarea#veTerms", timeout=5000)
        pg.click("#modal [data-m='no']")  # vuelve a Ajustes, con lo escrito
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertNotEqual(pg.input_value("#agMic"), "m2")  # aún no está la opción
        for _ in range(50):  # la segunda petición (al reabrir Ajustes), que se responde ahora
            if pendientes:
                break
            pg.wait_for_timeout(100)
        pendientes.pop(0).fulfill(json=devs)
        pg.wait_for_function("document.querySelector('#agMic').value === 'm2'", timeout=5000)
        self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
        # las listas cambian (otro proyecto) y el bloque de listas se repinta: la tabla y el selector, también
        pg.evaluate("api('/api/vocab/save', {project: 'Otro cliente', name: 'General', terms: 'uno, dos'})")
        pg.evaluate("loadVocab(S.config.language ?? 'es-en').then(() => { const b = document.querySelector('#sVocab');"
                    " b.innerHTML = b.innerHTML; })")
        pg.wait_for_selector("#pjTabla [data-pj-editar='Otro cliente']", timeout=5000)
        pg.wait_for_selector("#agProy option[value='Otro cliente/Vocabulario/General.txt']", state="attached",
                             timeout=5000)
        pg.evaluate("api('/api/vocab/delete', {id: 'Otro cliente/Vocabulario/General.txt'})")
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_claude_de_empresa_en_ajustes_y_en_el_asistente(self):
        """Rama claude-empresa (08/11/2026): «Comprobar» dice qué copia de Claude Code respondió (la de VS Code) y,
        si la cuenta no deja el modelo elegido, el aviso; el paso 2 del asistente dice dónde lo encontró. Las respuestas
        del servidor se simulan con la forma de esa rama (en esta no existen esos campos)."""
        pg, errores, red = self.pagina()
        aviso = ("El modelo elegido (sonnet) no está disponible con esta cuenta: se ha usado el predeterminado de tu "
                 "Claude Code (empresa-modelo).")
        pg.route("**/api/agente/comprobar", lambda r: r.fulfill(json={
            "ok": True, "version": "2.1.319", "seconds": 1.4, "ruta": "C:/x/claude.exe",
            "origen": "Claude Code de VS Code", "modelo_usado": "empresa-modelo", "aviso": aviso}))
        pg.route("**/api/agente/estado", lambda r: r.fulfill(json={
            "encontrado": True, "label": "Claude Code", "origen": "Claude Code de VS Code"}))
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.click(".aj-nav [data-sec='procesado']")
        pg.check("#modal input[name='proc'][value='agente']")
        pg.wait_for_function("!document.querySelector('#procAgente').hidden", timeout=5000)
        self.assertIn("Abrir PowerShell", pg.inner_text("#procAgente"))
        self.assertIn("Login successful", pg.inner_text("#procAgente"))
        pg.click("#agCheck")
        pg.wait_for_function("document.querySelector('#agRes').innerText.includes('responde')", timeout=5000)
        res = pg.inner_text("#agRes")
        self.assertIn("Claude Code de VS Code 2.1.319 responde (1,4 s)", res)
        self.assertIn(aviso, res)
        self.assertNotIn("· modelo", res)  # con el aviso, el modelo ya va dicho en él
        self.assertIn("Claude Code de VS Code 2.1.319", pg.inner_text("#agEstado"))
        pg.click("#modal [data-m='no']")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        pg.evaluate("abrirConfigurar()")
        pg.check("input[name='wzModo'][value='agente']")
        pg.click("[data-wz='sig']")
        pg.wait_for_selector(".dialog.wz h2:has-text('Conecta Claude Code')", timeout=5000)
        pg.wait_for_function("document.querySelector('[data-wz-encontrado]').innerText.includes('VS Code')",
                             timeout=10000)
        self.assertEqual(pg.inner_text("[data-wz-encontrado]"), "Instalado en este PC (Claude Code de VS Code)")
        paso = pg.inner_text(".dialog.wz")
        for trozo in ("Entra con tu cuenta", "Login successful", "pulsa c para copiar el enlace",
                      "La app de escritorio de Claude no basta"):
            self.assertIn(trozo, paso.replace("\n", " "))
        self.assertTrue(pg.is_visible("[data-wz='powershell']"))
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_tarjeta_con_listas_de_una_carpeta_conectada(self):
        """Rama vocab-carpeta (msg 1148): si existe vocabConectar (perfil.js de esa rama), la tarjeta ofrece «Usar las
        listas de una carpeta…» en Proyecto y en el panel del vocabulario; elegirla no cambia el proyecto y, al volver,
        la tarjeta sigue con lo escrito. Aquí vocabConectar es de prueba, con su firma: vocabConectar(after)."""
        pg, errores, red = self.pagina()
        devs = {"mics": [], "outputs": [], "mic": "default", "sys": "all"}
        pg.route("**/api/rec/devices*", lambda r: r.fulfill(json=devs))
        pg.route("**/api/rec/monitor", lambda r: r.fulfill(json={"ok": True}))
        nada = {"teams": None, "outlook": None, "reason": None, "sugerencia": {}}
        pg.route("**/api/reunion/actual", lambda r: r.fulfill(json=nada))
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        # (dentro de una función: evaluate llama a la función si la expresión acaba en una)
        pg.evaluate("() => { window.vocabLlamadas = 0; window.vocabConectar = after => { window.vocabLlamadas++;"
                    " setTimeout(after, 50); }; }")
        pg.click("#btnRec")
        pg.wait_for_selector(".dialog.gr [data-fila='carpeta'] .gr-v code", timeout=15000)
        # un nombre con signos que no admite un archivo: se dice cómo se guardará (y se graba así)
        pg.fill("#grNombre", "Comité: Q4")
        pg.wait_for_function("document.querySelector('#grNombreTag').textContent.includes('se guarda como')",
                             timeout=5000)
        self.assertEqual(pg.inner_text("#grNombreTag"), "se guarda como «Comité Q4»")
        pg.fill("#grNombre", "Mi reunión")
        pg.wait_for_function("!document.querySelector('#grNombreTag').textContent.includes('se guarda como')",
                             timeout=5000)
        antes = pg.input_value("#grProy")
        self.assertIn("Usar las listas de una carpeta…", pg.inner_text("#grProy"))
        pg.select_option("#grProy", "__conectar")
        pg.wait_for_function("window.vocabLlamadas === 1", timeout=5000)
        pg.wait_for_selector(".dialog.gr [data-fila='carpeta'] .gr-v code", timeout=15000)
        self.assertEqual(pg.input_value("#grNombre"), "Mi reunión")  # se vuelve a la tarjeta con lo escrito
        self.assertEqual(pg.input_value("#grProy"), antes)  # «Usar las listas…» no es un proyecto
        pg.click("[data-gr-cambiar='vocab']")
        pg.wait_for_selector("[data-panel='vocab'] [data-gr-ir='conectar']", timeout=5000)
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

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
