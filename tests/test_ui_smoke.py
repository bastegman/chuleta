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
        # ⚙ abre en «Al grabar» (rediseño B2); volver de un subdiálogo (openSettings sin sección), en la última vista
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal:not(.hidden) .aj-nav [data-sec='algrabar'].on", timeout=5000)
        pg.click(".aj-nav [data-sec='avanzado']")
        pg.evaluate("openSettings()")
        pg.wait_for_selector("#modal:not(.hidden) .aj-nav [data-sec='avanzado'].on", timeout=5000)
        # un enlace de dentro («Proyectos» en la ayuda, el origen de una línea…) lleva el foco a su sección del menú
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.click(".ag-como-btn")
        pg.focus("#agComo [data-sec-ir='proyectos']")
        pg.keyboard.press("Enter")
        self.assertTrue(pg.evaluate("document.activeElement.matches(\".aj-nav [data-sec='proyectos'].on\")"))
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
        # «Comité de pr»: con «Comit», otra reunión de la suite (la de «Copiar para correo», más reciente) iba primero
        pg.type("#grNombre", "Comité de pr")
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
        # con la rama fix-calendario: el Outlook clásico instalado y apagado (aquí no lo hay), y «Activar» que responde
        apagado = {"nivel": 1, "estado": "apagado", "frase": "Tienes el Outlook clásico: activa el calendario.",
                   "accion": {"id": "activar", "texto": "Activar"}, "activado": False}
        activados = []

        def activar(route):
            b = json.loads(route.request.post_data)
            activados.append(b)
            route.fulfill(json=dict(apagado, estado="conectado", frase="Outlook clásico: conectado", accion=None,
                                    activado=True) if b.get("activar") else apagado)
        pg.route("**/api/calendario/estado*", lambda r: r.fulfill(json=apagado))
        pg.route("**/api/calendario/activar", activar)
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
        pg.wait_for_function("document.querySelector('#pjTabla .pj-estado')?.textContent === 'Idioma guardado'",
                             timeout=5000)
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
        # Outlook una sola vez, en su fila: la casilla de reunion.js no se enseña y solo hay un «Activar» o un
        # «Desactivar» a la vista (revisión 2, P1-5). Con fix-calendario la frase es su #rnEstado y guardan al momento
        # (aquí, con su estado simulado: en este PC no hay Outlook clásico); sin ella, con «Guardar», y se dice
        self.assertFalse(pg.is_visible("#rnOutlook"))
        self.assertTrue(pg.is_visible("#ctOutFila"))
        botones = ("[...document.querySelectorAll('#ctOutAcc button, #rnEstado button, label:has(#rnOutlook)')]"
                   ".filter(b => b.offsetParent && /Activar|Desactivar|Leer mi calendario/.test(b.textContent))"
                   ".map(b => b.textContent.trim())")
        acc = "document.querySelector('#ctOutAcc').innerText"
        if pg.evaluate("!!document.querySelector('#rnEstado')"):
            pg.wait_for_function(f"{acc}.includes('Activar')", timeout=5000)
            self.assertEqual(pg.evaluate(botones), ["Activar"])
            self.assertEqual(pg.inner_text("#ctOutEstado"), "Apagado")
            self.assertTrue(pg.is_visible("#ctOutFila #rnEstado"))
            self.assertEqual(pg.get_attribute("#ctOutAcc [data-ct-outlook]", "aria-label"),
                             "Activar el calendario de Outlook")
            # con el teclado: el foco sigue en el botón de la fila, también tras activar (revisión 3, P1-c)
            pg.focus("#ctOutAcc [data-ct-outlook='1']")
            pg.keyboard.press("Enter")
            pg.wait_for_function(f"{acc}.includes('Desactivar')", timeout=5000)
            pg.wait_for_function("document.activeElement.matches(\"#ctOutAcc [data-ct-outlook='0']\")", timeout=5000)
            self.assertEqual(pg.evaluate(botones), ["Desactivar"])
            self.assertTrue(pg.is_checked("#rnOutlook"))
            self.assertEqual(pg.inner_text("#ctOutEstado"), "Activado")
            self.assertNotIn("sin guardar", pg.inner_text(".aj-pend"))  # ya está guardado
            pg.click("#ctOutAcc [data-ct-outlook='0']")
            pg.wait_for_function(f"{acc}.includes('Activar')", timeout=5000)
            self.assertFalse(pg.is_checked("#rnOutlook"))
            self.assertEqual([c["activar"] for c in activados], [True, False])
            # «Guardar» no manda lo que se guarda solo: no pisa un «Activar» que aún espera a Outlook (revisión 3, P1-b)
            enviados = []
            pg.route("**/api/settings", lambda r: (enviados.append(json.loads(r.request.post_data or "{}"))
                                                   if r.request.method == "POST" else None, r.continue_())[1])
            pg.click("#modal [data-m='ok']")
            pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
            self.assertTrue(enviados and all("outlook_on" not in b for b in enviados), enviados)
            pg.unroute("**/api/settings")
            pg.click("#btnSettings")
            pg.click(".aj-nav [data-sec='calendario']")
        elif pg.evaluate("/win/i.test(navigator.platform)"):
            self.assertEqual(pg.inner_text("#ctOutEstado"), "No conectado")
            self.assertEqual(pg.evaluate(botones), ["Activar"])
            self.assertEqual(pg.get_attribute("#ctOutAcc [data-ct-outlook]", "aria-label"),
                             "Activar el calendario de Outlook")
            self.assertFalse(pg.is_visible("#rnProbar"))  # «Probar» es de Outlook: con Outlook activado, en su fila
            pg.click("#ctOutAcc [data-ct-outlook='1']")
            self.assertTrue(pg.is_checked("#rnOutlook"))
            self.assertEqual(pg.inner_text("#ctOutEstado"), "Activado")
            self.assertEqual(pg.evaluate(botones), ["Desactivar"])
            self.assertTrue(pg.is_visible("#ctOutFila #rnProbar"))
            self.assertIn("Se activa al pulsar Guardar", pg.inner_text("#ctOutFrase"))
            pg.click("#ctOutAcc [data-ct-outlook='0']")
            self.assertFalse(pg.is_checked("#rnOutlook"))
        else:  # fuera de Windows no se ofrece activar algo que no funcionaría (revisión 2, P2-11)
            self.assertEqual(pg.inner_text("#ctOutEstado"), "Solo en Windows")
            self.assertEqual(pg.evaluate(botones), [])
            self.assertIn("solo se lee en Windows", pg.inner_text("#ctOutFrase"))
            self.assertFalse(pg.is_visible("#rnProbar"))
        ayuda = "[data-secc='calendario'] .ct-filas + details.ag-como .help"
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
        pg.wait_for_function("document.querySelector('#pjTabla .pj-estado')?.textContent === 'Carpeta guardada'",
                             timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["workstream_folders"], {ws: "Proyecto"})
        pg.click("#pjTabla [data-pj-no]")
        celda = f"document.querySelector('#pjTabla tr[data-pjw=\"{ws}\"] td:nth-child(2)').innerText"
        pg.wait_for_function(f"!{celda}.includes('la del proyecto')", timeout=5000)
        # dos cambios seguidos en la misma fila y enseguida otra fila: no se pierde ninguno (revisión 2, P1-1)
        pg.click("#pjTabla [data-pj-editar='Proyecto']")
        pg.select_option("#pjTabla tr.pj-ed [data-pj-f]", "Proyecto")
        pg.select_option("#pjTabla tr.pj-ed [data-pj-l]", "de")  # enseguida, el otro desplegable
        pg.click(f"#pjTabla [data-pjw-editar='{ws}']")  # y enseguida otra fila
        # la fila nueva, en cuanto se abre (la vieja se cierra tras guardar lo pendiente: no se elige en ella)
        fila_ws = f"#pjTabla tr.pj-ed[data-alcance='workstream'][data-vocab='{ws}']"
        pg.wait_for_selector(fila_ws, timeout=5000)
        pg.select_option(f"{fila_ws} [data-pj-l]", "fr")
        pg.click("#pjTabla [data-pj-no]")
        todo = ("api('/api/config').then(c => (c.project_folders_asignadas || c.project_folders)?.Proyecto"
                " === 'Proyecto'"
                f" && c.project_langs?.Proyecto === 'de' && c.workstream_langs?.['{ws}'] === 'fr')")
        pg.wait_for_function(todo, timeout=5000)
        for body in ({"clave": "idioma", "valor": None, "alcance": "proyecto", "vocab": ws},
                     {"clave": "idioma", "valor": None, "alcance": "workstream", "vocab": ws},
                     {"clave": "carpeta", "valor": None, "alcance": "proyecto", "vocab": ws},
                     {"clave": "carpeta", "valor": None, "alcance": "workstream", "vocab": ws}):
            pg.evaluate("b => api('/api/grabar/recordar', b)", body)  # deja los ajustes como estaban
        cfg = pg.evaluate("api('/api/config')")
        self.assertEqual((cfg["workstream_folders"], cfg["workstream_langs"], cfg["project_langs"]), ({}, {}, {}))
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_modo_sin_aplicar_y_filas_que_no_cambian(self):
        """Revisión 2 de B2: la opción marcada en «Dónde se procesan» y sin aplicar llega al plan y es «sin guardar»,
        porque «Guardar» la aplica (P1-2); y al recalcular, solo cambian las filas del plan que cambian: un lector de
        pantalla no vuelve a leer el plan entero (P2-4). Lo que se guarda solo y llega después de abrir («Arrancar con
        Windows», ya activado) no cuenta como cambio."""
        pg, errores, red = self.pagina()
        pg.route("**/api/inicio-windows", lambda r: r.fulfill(json={"activado": True}))
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila[data-fila='resume']", timeout=10000)
        pg.wait_for_function("document.querySelector('#sInicioWin').checked", timeout=5000)
        self.assertIsNone(pg.get_attribute(".ag-plan", "aria-live"))
        self.assertEqual(pg.get_attribute("#agFilas", "aria-live"), "polite")
        antes = pg.evaluate("api('/api/config')")["procesado"]
        otro = "agente" if antes != "agente" else "local"
        resume = "document.querySelector('#agFilas [data-fila=resume] .gr-v').textContent"
        r0 = pg.evaluate(resume)
        pg.evaluate("window.__audio = document.querySelector('#agFilas [data-fila=audio]')")
        pg.click(".aj-nav [data-sec='procesado']")
        pg.check(f"#modal input[name='proc'][value='{otro}']")
        self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.wait_for_function(f"{resume} !== {json.dumps(r0)}", timeout=5000)
        self.assertIn("Claude" if otro == "agente" else "No se resume", pg.evaluate(resume))
        self.assertTrue(pg.evaluate("window.__audio === document.querySelector('#agFilas [data-fila=audio]')"))
        self.assertEqual(pg.evaluate("api('/api/config')")["procesado"], antes)  # aún no
        pg.click(".aj-nav [data-sec='procesado']")
        pg.check(f"#modal input[name='proc'][value='{antes}']")
        self.assertNotIn("sin guardar", pg.inner_text(".aj-pend"))
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.wait_for_function(f"{resume} === {json.dumps(r0)}", timeout=5000)
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_lo_no_guardado_vuelve_solo_al_volver(self):
        """Revisiones 3 y 4 de B2: lo escrito y sin guardar en Ajustes vuelve al volver de un subdiálogo abierto desde
        Ajustes (una lista, «Apagar» › «Cancelar», también con Escape), y solo lo cambiado: el motor del directo y el
        modo sin aplicar incluidos, y «Guardar» los guarda. Cualquier otra forma de abrir Ajustes (⚙, la tarjeta, un
        error) lo abre de nuevo: antes un borrador abandonado volvía y «Guardar» deshacía lo guardado entretanto."""
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        cfg = pg.evaluate("api('/api/config')")
        lang, otro = cfg["language"], "pt" if cfg["language"] != "pt" else "de"
        motor = next(x["id"] for x in cfg["presets"] if x["id"] != cfg["live_preset"] and x["engine"] == "local")
        modo = "agente" if cfg["procesado"] != "agente" else "local"
        antes = {k: cfg[k] for k in ("rec_sys", "live_preset", "language", "procesado")}
        self.addCleanup(pg.evaluate, "b => api('/api/settings', b)", antes)
        abrir = "openVocabEditor(S.vocab[0], openSettings)"  # como «editar» en Proyectos: ocupa el hueco de Ajustes
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila", timeout=10000)
        # vuelve: «Cancelar» del editor lleva de vuelta, con lo escrito (idioma, motor y modo sin aplicar)
        pg.select_option("#sLang", otro)
        pg.click(".aj-nav [data-sec='procesado']")
        pg.select_option("#lPreset", motor)
        pg.check(f"#modal input[name='proc'][value='{modo}']")
        pg.evaluate(abrir)
        pg.click("#modal [data-m='no']")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertEqual((pg.input_value("#sLang"), pg.input_value("#lPreset")), (otro, motor))
        self.assertTrue(pg.is_checked(f"#modal input[name='proc'][value='{modo}']"))
        self.assertIn("aún no está aplicado", pg.inner_text("#procGuardado"))
        self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
        # «Apagar» › Escape en la confirmación: también vuelve, con lo escrito
        pg.click(".aj-nav [data-sec='ayuda']")
        pg.click("#sOff")
        pg.keyboard.press("Escape")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertEqual(pg.input_value("#sLang"), otro)
        # «Guardar» guarda lo repuesto, motor incluido (revisión 4: el repuesto se veía y no se mandaba)
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.select_option("#sLang", lang)
        pg.click(".aj-nav [data-sec='procesado']")
        pg.check(f"#modal input[name='proc'][value='{cfg['procesado']}']")
        pg.click("#modal [data-m='ok']")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["live_preset"], motor)
        # no vuelve: un subdiálogo dejado a medias y Ajustes abierto por otro camino (la tarjeta, un error, ⚙)
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila", timeout=10000)
        pg.select_option("#sLang", otro)
        pg.evaluate(abrir)
        pg.evaluate("openSettings({ sec: 'procesado' })")  # «Abrir Ajustes › Procesado» desde la tarjeta
        pg.wait_for_selector("#modal .aj-nav [data-sec='procesado'].on", timeout=5000)
        self.assertEqual((pg.input_value("#sLang"), pg.inner_text(".aj-pend")), (lang, ""))
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.select_option("#sLang", otro)
        pg.evaluate(abrir)
        pg.keyboard.press("Escape")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        pg.evaluate("api('/api/settings', {rec_sys: 'default'}).then(setConfig)")  # como «Usarlo en» en la tarjeta
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila", timeout=10000)
        self.assertEqual((pg.input_value("#sLang"), pg.inner_text(".aj-pend")), (lang, ""))
        pg.click("#modal [data-m='ok']")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        c = pg.evaluate("api('/api/config')")
        self.assertEqual((c["language"], c["rec_sys"]), (lang, "default"))  # lo guardado entretanto, intacto
        self.assertFalse(pg.evaluate("document.body.classList.contains('aj-abierto')"))
        # lo guardado en el subdiálogo (el asistente guarda el modo o el motor) gana a lo escrito antes (revisión 5)
        pg.click("#btnSettings")
        pg.click(".aj-nav [data-sec='procesado']")
        pg.check(f"#modal input[name='proc'][value='{modo}']")
        pg.select_option("#lPreset", antes["live_preset"])
        pg.evaluate(abrir)
        # la base de config_rev se pide al salir de Ajustes: lo de fuera llega después (una persona tarda más que esto
        # en guardar algo en un subdiálogo; sin esperarla, el servidor podía contestarla ya con el cambio dentro)
        pg.evaluate("S.ajBorradorRev")
        guardado = "api" if modo != "api" else "local"
        tercero = next(x["id"] for x in cfg["presets"]
                       if x["engine"] == "local" and x["id"] not in (motor, antes["live_preset"]))
        pg.evaluate("b => api('/api/settings', b).then(setConfig)", {"procesado": guardado, "live_preset": tercero})
        pg.click("#modal [data-m='no']")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertTrue(pg.is_checked(f"#modal input[name='proc'][value='{guardado}']"))
        self.assertEqual((pg.input_value("#lPreset"), pg.inner_text(".aj-pend")), (tercero, ""))
        # el asistente abierto desde Ajustes y «Ir a Avanzado»: una vuelta, con lo escrito y a Avanzado (revisión 5)
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.select_option("#sLang", otro)
        pg.click(".aj-nav [data-sec='procesado']")
        pg.click("#modal [data-cfg='cambiar']")
        pg.wait_for_selector("#modal .dialog.wz", timeout=5000)
        pg.evaluate("wzAccion('avanzado')")
        pg.wait_for_selector("#modal .aj-nav [data-sec='avanzado'].on", timeout=5000)
        pg.wait_for_timeout(300)  # el cierre del asistente ya no manda a Procesado después
        self.assertTrue(pg.is_visible("#modal .aj-nav [data-sec='avanzado'].on"))
        self.assertEqual(pg.input_value("#sLang"), otro)
        # «Apagar» desde el aviso de cerrar la ventana y «Cancelar»: no abre Ajustes con lo de antes (revisión 5)
        pg.evaluate(abrir)
        pg.keyboard.press("Escape")
        pg.evaluate("localStorage.removeItem('bgNotice'); onWindowClosing()")
        pg.click("#modal [data-m='off']")
        pg.click("#modal [data-m='no']")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        pg.click("#btnSettings")
        self.assertEqual(pg.input_value("#sLang"), lang)
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, [r for r in red if "/api/ocultar" not in r])
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_borrador_de_la_nube_y_el_asistente(self):
        """Revisión 6 de B2: lo escrito en la parte de la nube (proveedor, modelo, clave) vuelve aunque entretanto
        llegue otra configuración con el cupo cambiado (antes se firmaba la nube entera, con quota.reset_in, y se
        tiraba); el proveedor vuelve con su modelo; lo que el asistente guarda (ajOlvidarAsistente) ya no vuelve del
        borrador; un desplegable cuya opción ya no existe se queda en lo guardado (antes en blanco, y «Guardar» mandaba
        «»); y «Revisar en Ajustes» del asistente enseña marcado el modo elegido allí."""
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        abrir = "openVocabEditor(S.vocab[0], openSettings)"
        poner = ("([id, v]) => { const el = document.querySelector('#' + id); el.value = v;"
                 " el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }")
        # guardado OpenAI; se elige Groq con el modelo que no es el que pinta por defecto (el caso de la revisión)
        prov_antes = pg.evaluate("api('/api/config')")["cloud"]["provider"]
        pg.evaluate("api('/api/settings', {cloud: {provider: 'openai'}}).then(setConfig)")
        self.addCleanup(pg.evaluate, "p => api('/api/settings', {cloud: {provider: p}}).then(setConfig)", prov_antes)
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila", timeout=10000)
        prov0, otro = "openai", "groq"
        self.assertEqual(pg.input_value("#cProv"), prov0)
        pg.evaluate(poner, ["cProv", otro])
        modelo = pg.evaluate("[...document.querySelectorAll('#cModel option')].map(o => o.value)"
                             ".find(v => v !== document.querySelector('#cModel').value)")  # no el que pinta
        self.assertTrue(modelo)
        pg.evaluate(poner, ["cModel", modelo])
        pg.evaluate(poner, ["cKey", "clave-de-prueba"])
        pg.evaluate(abrir)
        # llega otra configuración con el cupo de la nube cambiado (p. ej. al guardar una fila de Proyectos)
        pg.evaluate("setConfig({ ...S.config, cloud: { ...S.config.cloud, quota: { reset_in: 1.5 } } })")
        pg.click("#modal [data-m='no']")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertEqual([pg.input_value(x) for x in ("#cProv", "#cModel", "#cKey")], [otro, modelo, "clave-de-prueba"])
        pg.keyboard.press("Escape")  # lo que guarda el asistente: test_ajustes_borrador_y_lo_que_guarda_el_asistente
        # un desplegable sin la opción del borrador: lo guardado, no en blanco
        pg.evaluate("S.ajBorrador = { pfFolder: 'No/Existe' }; openSettings()")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertEqual(pg.input_value("#pfFolder"), "__auto")
        pg.keyboard.press("Escape")
        # igual con «Proyecto al grabar» (una lista borrada o renombrada en su editor), también a la segunda vuelta
        # (revisión 7: acababa en «Sin proyecto», que nadie eligió)
        for _ in range(2):
            pg.evaluate("S.ajBorrador = S.ajBorrador || { pfVocab: 'Otro/Vocabulario/Borrada.txt' }; openSettings()")
            pg.wait_for_function("S.vocab.length && [...document.querySelectorAll('#pfVocab option')]"
                                 ".some(o => o.value === 'Proyecto/Vocabulario/General.txt')", timeout=5000)
            self.assertEqual(pg.input_value("#pfVocab"), "__auto")
            pg.select_option("#sLang", pg.evaluate("S.config.language") or "es")  # algo que pase por marcar()
        self.assertNotIn("pfVocab", pg.evaluate("S.ajBorrador || {}"))
        pg.keyboard.press("Escape")
        # un micrófono fijo escrito y dos vueltas antes de que llegue la lista de dispositivos: sigue esperándola y,
        # cuando llega, se pone (revisión 7: a la segunda vuelta volvía a «El de Windows»)
        retenidas = []
        pg.route("**/api/rec/devices*", lambda r: retenidas.append(r))
        pg.evaluate("S.ajBorrador = { agMic: 'm2' }; openSettings()")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        pg.select_option("#sLang", "en")
        self.assertEqual(pg.evaluate("S.ajBorrador.agMic"), "m2")
        pg.evaluate("openSettings()")
        pg.wait_for_function(f"document.querySelector('#modal .aj-nav') && {len(retenidas)} < 99", timeout=5000)
        devs = {"mics": [{"id": "m2", "name": "Jabra", "default": False},
                         {"id": "m1", "name": "Realtek", "default": True}],
                "mic": "default", "sys": "all", "outputs": []}
        for r in retenidas:
            r.fulfill(json=devs)
        pg.wait_for_function("document.querySelector('#agMic').value === 'm2'", timeout=5000)
        pg.keyboard.press("Escape")
        # y uno que no está en la lista que llega: deja de esperar (no se queda en el borrador para siempre)
        retenidas.clear()
        pg.evaluate("S.ajBorrador = { agMic: 'm9' }; openSettings()")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        for _ in range(100):  # la petición de dispositivos, retenida
            if retenidas:
                break
            pg.wait_for_timeout(50)
        self.assertTrue(retenidas)
        for r in retenidas:
            r.fulfill(json=devs)
        pg.wait_for_function("[...document.querySelectorAll('#agMic option')].some(o => o.value === 'm2')",
                             timeout=5000)
        pg.select_option("#sLang", "es")  # algo que pase por marcar()
        self.assertNotIn("agMic", pg.evaluate("S.ajBorrador || {}"))
        pg.unroute("**/api/rec/devices*")
        pg.keyboard.press("Escape")
        # «Revisar en Ajustes» con «Con tu cuenta de Claude» elegido en el asistente
        pg.evaluate("openSettings({ sec: 'procesado', proc: 'agente' })")
        pg.wait_for_selector("#modal .aj-nav [data-sec='procesado'].on", timeout=5000)
        self.assertTrue(pg.is_checked("#modal input[name='proc'][value='agente']"))
        self.assertTrue(pg.is_visible("#procAgente"))
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_borrador_y_lo_que_guarda_el_asistente(self):
        """Revisiones 7 y 8 de B2, por los caminos de verdad del asistente (wzGuardar): si el asistente cambia un
        ajuste, lo escrito en Ajustes se descarta entero y se dice (config_rev); si vuelve sin guardar, o guarda lo que
        ya había («Probar» › «Terminar»), lo escrito vuelve tal cual y sin aviso. Y «Revisar en Ajustes» con «Con tu
        cuenta de Claude» elegida en el asistente la enseña marcada."""
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        cfg = pg.evaluate("api('/api/config')")
        guardado = cfg["procesado"]
        marcado = "agente" if guardado != "agente" else "local"
        motor = next(x["id"] for x in cfg["presets"] if x["engine"] == "local" and x["id"] != cfg["live_preset"])
        antes = {"procesado": guardado, "live_preset": cfg["live_preset"],
                 "cloud": {"provider": cfg["cloud"]["provider"]}}
        self.addCleanup(pg.evaluate, "b => api('/api/settings', b).then(setConfig)", antes)
        volver = "() => openSettings({ sec: 'procesado', volver: true })"

        def escribir():
            pg.click("#btnSettings")
            pg.click(".aj-nav [data-sec='procesado']")
            pg.check(f"#modal input[name='proc'][value='{marcado}']")
            pg.select_option("#lPreset", motor)

        def de_vuelta():
            pg.wait_for_selector("#modal .aj-nav [data-sec='procesado'].on", timeout=10000)
            return pg.evaluate("[document.querySelector('input[name=proc]:checked')?.value,"
                               " document.querySelector('#lPreset').value]")
        # el asistente sin guardar («Ahora no», Escape): vuelve lo escrito, tal cual
        escribir()
        pg.evaluate(f"abrirConfigurar({{ paso: 3, modo: S.config.procesado, volver: {volver} }})")
        pg.wait_for_selector("#modal .dialog.wz", timeout=5000)
        pg.keyboard.press("Escape")
        self.assertEqual(de_vuelta(), [marcado, motor])
        self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
        pg.keyboard.press("Escape")
        aviso = "document.querySelector('#toasts').innerText.includes('Los ajustes cambiaron en otra parte')"
        # «Probar» › «Terminar» y «Cambiar…» › guardar el modo de siempre: el asistente guarda lo que ya había, no ha
        # cambiado nada y lo escrito vuelve tal cual, sin aviso (revisión 8: se descartaba con un aviso falso)
        for abrir in (f"abrirConfigurar({{ paso: 3, modo: S.config.procesado, volver: {volver} }})",
                      "document.querySelector('#modal [data-cfg=cambiar]').click()"):
            escribir()
            pg.evaluate(abrir)
            pg.wait_for_selector("#modal .dialog.wz", timeout=5000)
            pg.evaluate("wzGuardar()")
            self.assertEqual(de_vuelta(), [marcado, motor])
            self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
            self.assertFalse(pg.evaluate(aviso))
            pg.keyboard.press("Escape")
        # con «API externa» y otro proveedor: la nube que había escrito en Ajustes ya no vuelve
        otro = "openai" if cfg["cloud"]["provider"] != "openai" else "groq"
        pg.click("#btnSettings")
        pg.evaluate("v => { const k = document.querySelector('#cKey'); k.value = v;"
                    " k.dispatchEvent(new Event('input', { bubbles: true })); }", "escrita-en-ajustes")
        pg.evaluate(f"abrirConfigurar({{ paso: 3, modo: 'api', volver: {volver} }})")
        pg.wait_for_selector("#modal .dialog.wz", timeout=5000)
        pg.evaluate("p => { W.cloud.provider = p; return wzGuardar(); }", otro)
        de_vuelta()
        self.assertEqual((pg.input_value("#cProv"), pg.input_value("#cKey")), (otro, ""))
        pg.wait_for_function(aviso, timeout=5000)  # y se dice
        self.assertEqual(pg.inner_text(".aj-pend"), "")
        pg.keyboard.press("Escape")
        # «Revisar en Ajustes» desde el asistente con «Con tu cuenta de Claude» elegida (sin guardar)
        pg.evaluate("b => api('/api/settings', b).then(setConfig)", antes)
        pg.click("#btnSettings")
        pg.click(".aj-nav [data-sec='procesado']")
        pg.click("#modal [data-cfg='cambiar']")
        pg.wait_for_selector("#modal .dialog.wz", timeout=5000)
        pg.evaluate("W.modo = 'agente'; wzAccion('procesado')")
        de_vuelta()
        self.assertTrue(pg.is_checked("#modal input[name='proc'][value='agente']"))
        self.assertTrue(pg.is_visible("#procAgente"))
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))

    def test_ajustes_borrador_y_config_rev(self):
        """Revisión 8 de B2, lo escrito en Ajustes frente a config_rev:
        - lo que Ajustes mismo guarda antes de salir (aunque no pida /api/config después) no es «de fuera»: repintar
          Ajustes desde dentro («Probar otra vez», «Importar listas…») y volver de un subdiálogo lo dejan tal cual;
        - «Otra carpeta (fuera de Chuleta)…» en «Reuniones sin proyecto» es un cambio sin guardar como otro;
        - renombrar en su editor la lista guardada como «Proyecto al grabar» cambia un ajuste: lo escrito se descarta,
          se dice, y Ajustes enseña la lista con su nombre nuevo y la guarda;
        - con las listas llegando después de abrir, lo escrito se pone si no lo has tocado (y cuenta como sin
          guardar); si lo has tocado, queda lo tuyo."""
        import re
        pg, errores, red = self.pagina()
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        aviso = "document.querySelector('#toasts').innerText.includes('Los ajustes cambiaron en otra parte')"
        callar = "document.querySelectorAll('#toasts .toast').forEach(t => t.remove())"
        cfg = pg.evaluate("api('/api/config')")
        self.addCleanup(pg.evaluate, "b => api('/api/settings', b)",
                        {"language": cfg["language"], "perfil": {"folder": None, "vocab": None, "context": ""}})
        escrito = "escrito y sin guardar"

        def escribir():
            pg.click("#btnSettings")
            pg.wait_for_selector("#modal .aj-nav", timeout=5000)
            pg.fill("#pfContext", escrito)

        def ida_y_vuelta():  # un subdiálogo («editar» una lista) y «Cancelar»
            pg.evaluate("openVocabEditor(S.vocab[0], openSettings)")
            pg.wait_for_selector("#modal #veName", timeout=5000)
            pg.click("#modal [data-m='no']")
            pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        # 1. lo guardado desde Ajustes antes de salir, sin pedir /api/config: ni al repintar ni al volver se descarta
        escribir()
        otro = "pt" if cfg["language"] != "pt" else "en"
        pg.evaluate("l => api('/api/settings', { language: l })", otro)
        for repintar in ("openSettings()", "openSettings({ sec: 'algrabar', volver: true })"):
            pg.evaluate(repintar)
            pg.wait_for_selector("#modal .aj-nav", timeout=5000)
            self.assertEqual(pg.input_value("#pfContext"), escrito, repintar)
        ida_y_vuelta()
        self.assertEqual(pg.input_value("#pfContext"), escrito)
        self.assertFalse(pg.evaluate(aviso))
        pg.keyboard.press("Escape")
        # 2. «Otra carpeta (fuera de Chuleta)…»: se vincula, cuenta como sin guardar y vuelve de un subdiálogo
        carpeta = os.path.join(self.tmp, "fuera-r8")
        os.makedirs(carpeta, exist_ok=True)
        pg.route("**/api/link/pick", lambda r: r.fulfill(json={"path": carpeta}))
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        pg.select_option("#pfFolder", "__pick")
        pg.wait_for_function("!['__auto', '__pick'].includes(document.querySelector('#pfFolder').value)", timeout=5000)
        rel = pg.input_value("#pfFolder")
        self.addCleanup(pg.evaluate, "p => api('/api/link/remove', { path: p })", rel)
        self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
        ida_y_vuelta()
        self.assertEqual(pg.input_value("#pfFolder"), rel)
        self.assertFalse(pg.evaluate(aviso))
        pg.keyboard.press("Escape")
        pg.unroute("**/api/link/pick")
        # 3. la lista de «Proyecto al grabar», renombrada en su editor
        lista = pg.evaluate("api('/api/vocab/save', { project: 'Cliente R8', name: 'Legal', terms: 'uno' })")["id"]
        self.addCleanup(pg.evaluate, "() => Promise.allSettled(['Legal', 'Legal 2', 'Legal 3'].map(n =>"
                        " api('/api/vocab/delete', { id: `Cliente R8/Vocabulario/${n}.txt` })))")
        pg.evaluate("loadVocab(S.config.language ?? 'es-en')")
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        pg.select_option("#pfVocab", lista)
        pg.click("#modal .aj-pie [data-m='ok']")
        pg.wait_for_function("S.config.perfil?.vocab === 'Cliente R8/Vocabulario/Legal.txt' && !document.querySelector("
                             "'#modal .aj-nav')", timeout=5000)
        escribir()
        pg.evaluate(callar)
        pg.evaluate("id => openVocabEditor(S.vocab.find(v => v.id === id), openSettings)", lista)
        pg.wait_for_selector("#modal #veName", timeout=5000)
        pg.fill("#veName", "Legal 2")
        pg.click("#modal [data-m='ok']")
        pg.wait_for_function(aviso, timeout=5000)
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertEqual(pg.input_value("#pfContext"), "")
        self.assertEqual(pg.input_value("#pfVocab"), "Cliente R8/Vocabulario/Legal 2.txt")
        self.assertNotIn("no se encuentra", pg.inner_text("#pfVocab"))
        pg.fill("#pfContext", escrito)
        pg.click("#modal .aj-pie [data-m='ok']")
        pg.wait_for_function("!document.querySelector('#modal .aj-nav')", timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["perfil"]["context"], escrito)
        # y sin nada escrito: no hay nada que descartar ni que decir, pero Ajustes enseña el nombre nuevo y guarda
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        pg.evaluate(callar)
        pg.evaluate("openVocabEditor(S.vocab.find(v => v.id === 'Cliente R8/Vocabulario/Legal 2.txt'), openSettings)")
        pg.wait_for_selector("#modal #veName", timeout=5000)
        pg.fill("#veName", "Legal 3")
        pg.click("#modal [data-m='ok']")
        nuevo = "Cliente R8/Vocabulario/Legal 3.txt"
        pg.wait_for_function(f"document.querySelector('#modal #pfVocab')?.value === '{nuevo}'", timeout=5000)
        self.assertNotIn("no se encuentra", pg.inner_text("#pfVocab"))
        self.assertFalse(pg.evaluate(aviso))
        pg.click("#modal .aj-pie [data-m='ok']")
        pg.wait_for_function("!document.querySelector('#modal .aj-nav')", timeout=5000)
        self.assertEqual(pg.evaluate("api('/api/config')")["perfil"]["vocab"], "Cliente R8/Vocabulario/Legal 3.txt")
        # 4. las listas llegan después de abrir: lo escrito se pone (y cuenta) si no lo tocas; si lo tocas, lo tuyo
        retenidas = []
        pg.route(re.compile(r"/api/vocab(\?|$)"), lambda r: retenidas.append(r))
        general = "Proyecto/Vocabulario/General.txt"
        for tocar in (False, True):
            retenidas.clear()
            pg.evaluate("id => { S.vocab = []; S.vocabCarpetas = undefined; S.vocabError = null;"
                        " S.ajBorrador = { pfVocab: id }; S.ajBorradorRev = null; openSettings(); }", general)
            pg.wait_for_selector("#modal .aj-nav", timeout=5000)
            if tocar:
                pg.select_option("#pfVocab", "")
            for _ in range(100):  # la lectura de las listas, retenida
                if retenidas:
                    break
                pg.wait_for_timeout(50)
            self.assertTrue(retenidas)
            for r in retenidas:
                r.continue_()
            hay = f"[...document.querySelectorAll('#pfVocab option')].some(o => o.value === '{general}')"
            pg.wait_for_function(hay, timeout=5000)
            self.assertEqual(pg.input_value("#pfVocab"), "" if tocar else general)
            self.assertIn("sin guardar", pg.inner_text(".aj-pend"))
            pg.keyboard.press("Escape")
        # y si lo escrito no está en las listas que llegan, deja de esperarlas sin que haga falta tocar nada
        retenidas.clear()
        pg.evaluate("() => { S.vocab = []; S.vocabCarpetas = undefined; S.vocabError = null;"
                    " S.ajBorrador = { pfVocab: 'No/Vocabulario/Existe.txt' }; openSettings(); }")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        for _ in range(100):
            if retenidas:
                break
            pg.wait_for_timeout(50)
        for r in retenidas:
            r.continue_()
        pg.wait_for_function("!('pfVocab' in (S.ajBorrador || {}))", timeout=5000)
        pg.keyboard.press("Escape")
        pg.unroute(re.compile(r"/api/vocab(\?|$)"))
        # 5. Proyectos: un idioma cambiado en una fila y, enseguida, «Editar la lista»: se guarda antes de salir, así
        # que al volver no cuenta como de fuera
        self.addCleanup(pg.evaluate, "id => api('/api/grabar/recordar', { clave: 'idioma', alcance: 'workstream',"
                        " vocab: id, valor: null })", general)
        pg.click("#btnSettings")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        pg.fill("#pfContext", "otro escrito")  # el de antes ya está guardado (3)
        pg.evaluate(callar)  # el aviso de la 3 aún se ve
        pg.click(".aj-nav [data-sec='proyectos']")
        pg.click(f"[data-pjw-editar='{general}']")
        pg.select_option("tr.pj-ed [data-pj-l]", "en")
        pg.click("tr.pj-ed [data-pj-lista]")
        pg.wait_for_selector("#modal #veName", timeout=5000)
        pg.wait_for_timeout(500)  # más que el temporizador de la fila (300 ms)
        pg.click("#modal [data-m='no']")
        pg.wait_for_selector("#modal .aj-nav", timeout=5000)
        self.assertEqual(pg.input_value("#pfContext"), "otro escrito")
        self.assertFalse(pg.evaluate(aviso))
        self.assertEqual(pg.evaluate("api('/api/config')")["workstream_langs"].get(general), "en")
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_tarjeta_dice_los_avisos_del_plan(self):
        """Revisión 4 de B2: los avisos del plan (aquí, «Falta la API key» con «API externa» sin clave) salen en la
        tarjeta de grabar, en su línea, como en Ajustes: es al grabar cuando importan."""
        pg, errores, red = self.pagina()
        nada = {"teams": None, "outlook": None, "reason": None, "sugerencia": {}}
        pg.route("**/api/reunion/actual", lambda r: r.fulfill(json=nada))
        pg.route("**/api/rec/monitor", lambda r: r.fulfill(json={"ok": True}))
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        antes = pg.evaluate("api('/api/config')")["procesado"]
        self.addCleanup(pg.evaluate, "m => api('/api/settings', {procesado: m}).then(setConfig)", antes)
        pg.evaluate("api('/api/settings', {procesado: 'api'}).then(setConfig)")
        pg.click("#btnRec")
        pg.wait_for_selector(".dialog.gr [data-fila='resume'] .gr-aviso", timeout=10000)
        self.assertEqual(pg.inner_text(".dialog.gr [data-fila='resume'] .gr-aviso"),
                         "Falta la API key: ponla en Ajustes › Procesado.")
        pg.keyboard.press("Escape")
        # y el de la carpeta: la de las reuniones sin proyecto, guardada y que ya no se encuentra (revisión 5)
        fuera = os.path.join(self.root, "Fuera")
        os.makedirs(fuera, exist_ok=True)
        self.addCleanup(shutil.rmtree, fuera, True)
        pg.evaluate("api('/api/settings', {perfil: {folder: 'Fuera'}}).then(setConfig)")
        self.addCleanup(pg.evaluate, "api('/api/settings', {perfil: {folder: null}}).then(setConfig)")
        os.rmdir(fuera)
        pg.click("#btnRec")
        pg.wait_for_selector(".dialog.gr [data-fila='carpeta'] .gr-v code", timeout=10000)
        pg.select_option("#grProy", "")  # sin proyecto: se graba en la de Ajustes, que no está
        pg.wait_for_selector(".dialog.gr [data-fila='carpeta'] .gr-aviso", timeout=10000)
        self.assertIn("No se encuentra la carpeta de las reuniones sin proyecto (Chuleta\\Fuera\\)",
                      pg.inner_text(".dialog.gr [data-fila='carpeta'] .gr-aviso"))
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))

    def test_ajustes_sin_poder_leer_las_listas(self):
        """Revisión 2 de B2, P1-4: si /api/vocab falla, Proyectos no dice «Todavía no hay proyectos» (invitaba a
        crearlos otra vez): una frase con «Probar otra vez», también en las listas y en Al grabar."""
        pg, errores, red = self.pagina()
        pg.route("**/api/vocab*", lambda r: r.fulfill(status=500, json={"error": "OneDrive no responde"}))
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        pg.click("#btnSettings")
        pg.wait_for_selector("[data-secc='algrabar'] .ag-sin-listas", timeout=10000)
        self.assertIn("solo se puede comprobar «Sin proyecto»", pg.inner_text("[data-secc='algrabar'] .ag-sin-listas"))
        pg.click(".aj-nav [data-sec='proyectos']")
        pg.wait_for_selector("#pjTabla .ag-sin-listas", timeout=10000)
        self.assertIn("No se han podido leer las listas de vocabulario", pg.inner_text("#pjTabla"))
        self.assertNotIn("Todavía no hay", pg.inner_text("#pjTabla"))
        pg.wait_for_selector("#sVocab .ag-sin-listas", timeout=10000)
        self.assertNotIn("Todavía no hay listas", pg.inner_text("#sVocab"))
        self.assertNotIn("OneDrive no responde", pg.inner_text("#modal"))  # sin el texto técnico
        pg.unroute("**/api/vocab*")
        pg.click("#pjTabla [data-aj-listas-otra]")  # Ajustes otra vez, en Proyectos
        pg.wait_for_selector("#modal [data-secc='proyectos']:not([hidden]) #pjTabla [data-pj-editar='Proyecto']",
                             timeout=10000)
        self.assertEqual(pg.locator(".ag-sin-listas").count(), 0)
        pg.keyboard.press("Escape")
        err, red = self.limpiar(errores, [r for r in red if "/api/vocab" not in r])
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_lo_guardado_que_ahora_no_se_encuentra(self):
        """Revisión 2 de B2, P1-3: la carpeta de las reuniones sin proyecto y el proyecto al grabar guardados, si ahora
        no se encuentran (una vinculada desconectada, una lista que no se lee), se ven tal cual y cualquier «Guardar»
        los conserva. Antes el desplegable enseñaba «Automática» y el «Guardar» de otra sección los borraba."""
        pg, errores, red = self.pagina()
        omega, fuera = os.path.join(self.root, "Omega"), os.path.join(self.tmp, "Omega fuera")
        os.makedirs(os.path.join(omega, "Vocabulario"))
        os.makedirs(os.path.join(omega, "Clientes"))
        with open(os.path.join(omega, "Vocabulario", "Gen.txt"), "w", encoding="utf-8") as f:
            f.write("alfa, beta\n")
        self.addCleanup(shutil.rmtree, omega, True)
        self.addCleanup(shutil.rmtree, fuera, True)
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        perfil = {"folder": "Omega/Clientes", "vocab": "Omega/Vocabulario/Gen.txt"}
        pg.evaluate("p => api('/api/settings', {perfil: p})", perfil)
        # al terminar, como estaba (antes de cerrar la página: las limpiezas van al revés)
        self.addCleanup(pg.evaluate, "api('/api/settings', {perfil: {folder: null, vocab: null},"
                                     " summary: {extra: ''}})")
        os.rename(omega, fuera)  # ahora no se encuentran
        pg.reload(wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        # el árbol del servidor se lee como mucho cada segundo: hasta que ya no tenga la carpeta
        pg.wait_for_function("refreshTree(true).then(() => !S.folders.has('Omega/Clientes'))", polling=500,
                             timeout=10000)
        pg.click("#btnSettings")
        pg.wait_for_selector("#agFilas .gr-fila", timeout=10000)
        elegido = "(id => { const s = document.querySelector(id); return s.options[s.selectedIndex].textContent; })"
        pg.wait_for_function(f"{elegido}('#pfVocab').includes('no se encuentra')", timeout=5000)
        self.assertEqual(pg.evaluate(f"{elegido}('#pfVocab')"), "Omega › Gen (no se encuentra)")
        self.assertEqual(pg.evaluate(f"{elegido}('#pfFolder')"), "Omega\\Clientes (no se encuentra)")
        self.assertNotIn("sin guardar", pg.inner_text(".aj-pend"))
        pg.click(".aj-nav [data-sec='resumen']")
        pg.fill("#sExtra", "Céntrate en fechas")
        pg.click("#modal [data-m='ok']")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=5000)
        c = pg.evaluate("api('/api/config')")
        self.assertEqual(({k: c["perfil"][k] for k in perfil}, c["summary"]["extra"]), (perfil, "Céntrate en fechas"))
        err, red = self.limpiar(errores, red)
        self.assertEqual(err, [], (err, red))
        self.assertEqual(red, [], red)

    def test_ajustes_lo_no_guardado_errores_y_foco(self):
        """Revisión de B2 (09/11/2026): al abrir, el foco va a la sección y el plan no se desplaza fuera de la vista
        (900×600); el plan cambia con el motor del directo elegido en Procesado sin guardar, y «Transcribe» dice que
        sale de allí; sin micrófono y sin audio del PC lo avisa y no deja guardarlo; si el plan falla, una frase con
        «Probar otra vez» (en Al grabar y en Proyectos), sin el texto técnico."""
        pg, errores, red = self.pagina()
        # un PC con micrófono y altavoces (sin grabadora aquí, la lista no llega y su aviso ocupa una línea más)
        devs = {"mics": [{"id": "m1", "name": "Micrófono (Realtek)", "default": True}], "mic": "default", "sys": "all",
                "outputs": [{"id": "o1", "name": "Altavoces (Realtek)", "default": True}]}
        pg.route("**/api/rec/devices*", lambda r: r.fulfill(json=devs))
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
        # «Al grabar»: las reglas, en una pantalla (§4.0 del rediseño) a 1500×920, sin desplazar
        pg.set_viewport_size({"width": 1500, "height": 920})
        pg.click(".aj-nav [data-sec='algrabar']")
        pg.wait_for_selector("#agFilas .gr-fila[data-fila='audio']", timeout=5000)
        alto = pg.evaluate("(() => { const m = document.querySelector('.aj-main');"
                           " return [m.scrollHeight, m.clientHeight]; })()")
        self.assertLessEqual(alto[0], alto[1], alto)
        pg.click(".ag-como-btn")  # «¿Cómo funciona?», plegado en la línea de arriba
        self.assertTrue(pg.is_visible("#agComo"))
        self.assertEqual(pg.get_attribute(".ag-como-btn", "aria-expanded"), "true")
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
