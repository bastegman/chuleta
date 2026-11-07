# -*- coding: utf-8 -*-
"""Aspecto y arranque sin consola (31/10/2026): nombre de la app centralizado (common.APP_NAME, «Chuleta» desde el
renombrado), /api/apagar y /api/mostrar sobre el servidor HTTP real, apagado ordenado, log sin consola (pythonw.exe),
la ventana propia (window.py) con un pywebview de mentira, la versión (_app/version.txt) y Ajustes > Acerca de."""
import ast
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from support import APP, FakeJob, bare_jobs, patched_paths, server

import common  # noqa: E402
import window  # noqa: E402

STATIC = os.path.join(APP, "static")


def read(*parts):
    with open(os.path.join(APP, *parts), encoding="utf-8") as f:
        return f.read()


class NombreCentralizadoTests(unittest.TestCase):
    def test_constantes(self):
        self.assertEqual(common.APP_NAME, "Chuleta")
        self.assertEqual(common.APP_ID, "Bernardo.Chuleta")  # no cambia con el nombre: los anclajes hechos lo llevan
        self.assertRegex(common.APP_ID, r"^[A-Za-z0-9._-]{1,128}$")  # AppUserModelID: sin espacios, <= 128
        self.assertIs(server.APP_NAME, common.APP_NAME)
        self.assertEqual(server.PKG_ROOT, "Chuleta")  # raíz del zip instalable
        self.assertEqual(server.Handler.server_version, "Chuleta/1.0")
        self.assertEqual(server.REPO_URL, "https://github.com/bastegman/chuleta")

    def test_server_sin_literales_sueltos(self):
        """Ni «Notas» (nombre anterior) ni «Chuleta» como literal en server.py: el nombre sale de common.APP_NAME.
        Solo se permiten en docstrings."""
        tree = ast.parse(read("server.py"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docs.add(id(body[0].value))
        sueltos = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docs:
                continue
            if re.search(r"Notas|Chuleta", node.value):
                sueltos.append((node.lineno, node.value))
        self.assertEqual(sueltos, [])

    def test_lo_que_no_cambia_con_el_nombre(self):
        """Compatibilidad con las instalaciones y los datos existentes: DATA_DIR (AudiosApp), la cabecera X-Audios,
        /api/ping ("audios") y la pestaña «Notas» de cada reunión (es la función, no la marca)."""
        self.assertTrue(server.DATA_DIR.endswith("AudiosApp"))
        src = read("server.py")
        self.assertIn('self.headers.get("X-Audios") != "1"', src)
        # el ping sigue diciendo "audios" (y la version y, para cederle el primer plano, el pid)
        self.assertIn('{"app": "audios", "version": VERSION, "pid": os.getpid()}', src)
        notas = read("static", "notas.js")
        self.assertIn("${I.notes}Notas", notas)  # la pestaña
        self.assertIn("Tomar Notas", notas)
        self.assertNotIn("Notas separará", read("static", "voces.js"))

    def test_js_y_html_usan_app_name(self):
        app = read("static", "app.js")
        self.assertIn("let APP_NAME = document.title;", app)
        self.assertIn("if (c.app_name) APP_NAME = c.app_name;", app)
        # literales con el nombre de la app en app.js: ninguno (los textos usan ${APP_NAME}), ni el nuevo ni el anterior
        self.assertEqual(re.findall(r"""['"][^'"\n]*\b(?:Notas|Chuleta)\b[^'"\n]*['"]""", app), [])
        self.assertNotIn("Notas · versión", app)
        self.assertNotIn("Quitarla de Notas", app)
        self.assertNotIn("Bienvenido a Notas", read("static", "notas.js"))
        self.assertNotIn("Notas separará", read("static", "voces.js"))
        html = read("static", "index.html")
        self.assertIn("<title>{{APP_NAME}}</title>", html)
        self.assertIn('alt="">{{APP_NAME}}</a>', html)
        self.assertIn('title="{{APP_NAME}} ha detectado', html)
        self.assertNotIn("<title>Notas", html)
        self.assertNotIn("Notas ", html)
        self.assertIn('href="/chuleta.ico"', html)
        self.assertIn('href="/chuleta.png"', html)
        self.assertNotIn("notas.ico", html)
        for n in ("chuleta.ico", "chuleta.png", "notas.js"):
            self.assertTrue(os.path.isfile(os.path.join(STATIC, n)), n)
        # los iconos renombrados: ninguna referencia a los nombres antiguos en el código ni en la interfaz (el aviso
        # flotante, la pantalla de apagado y el instalador los cargan por ruta)
        files = [n for n in os.listdir(APP) if n.endswith((".py", ".ps1"))]
        files += [os.path.join("static", n) for n in os.listdir(STATIC) if n.endswith((".js", ".html", ".css"))]
        for n in files:
            with open(os.path.join(APP, n), encoding="utf-8-sig") as f:
                src = f.read()
            self.assertNotIn("notas.ico", src, n)
            self.assertNotIn("notas.png", src, n)

    def test_acerca_de_en_ajustes(self):
        """Al final de Ajustes: nombre, versión, licencia, repositorio, carpeta de datos con «Abrir» y «Copiar
        diagnóstico» (sin emojis: iconos I.*)."""
        app = read("static", "app.js")
        for s in ("${I.info}Acerca de</legend>", "versión ${esc(S.config.version || '')}",
                  "licencia ${esc(ac.licencia)}", 'id="aRepo"', 'id="aDatos"', 'id="aDiag"',
                  "api('/api/acerca/abrir', { que: 'datos' })",
                  "api('/api/acerca/abrir', { que: 'repo' })", "api('/api/diagnostico')", "copy(r.texto,",
                  'id="aDonar"', "api('/api/acerca/abrir', { que: 'donar' })", "${esc(ac.donar || '#')}",
                  "const ac = S.config.acerca || {};", "${esc(ac.datos || '')}"):
            self.assertIn(s, app, s)
        self.assertIn("info: '<svg", app)
        self.assertNotIn("versión del ${", app)

    def test_boton_apagar_en_ajustes(self):
        app = read("static", "app.js")
        for s in ('id="sOff"', "confirmShutdown()", "api('/api/apagar', {})", "function shutdownScreen()",
                  "se ha apagado</h2>", "if (S.off) return;"):
            self.assertIn(s, app, s)

    def test_aviso_sigue_en_segundo_plano(self):
        """La primera vez que se cierra la ventana propia: aviso con «No volver a avisar» y /api/ocultar al cerrarlo."""
        app = read("static", "app.js")
        for s in ("function onWindowClosing()", "sigue en segundo plano</h3>", "No volver a avisar",
                  "localStorage.getItem('bgNotice') === 'off'", "api('/api/ocultar', {})", "return 'handled';",
                  "Apagar ahora"):
            self.assertIn(s, app, s)


class ServidorHttpTests(unittest.TestCase):
    """El servidor de verdad en un puerto libre con un App minimo: cabeceras, rutas y respuestas tal cual."""

    @classmethod
    def setUpClass(cls):
        cls.app = server.App.__new__(server.App)
        cls.app.rec = cls.app.monitor = None
        cls.app.rec_lock = threading.Lock()
        cls.app.jobs = bare_jobs([])
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls.saved = (server.PORT, server.URL, server.Handler.app, server.WINDOW, server.Handler.token)
        server.PORT, server.URL = cls.port, f"http://127.0.0.1:{cls.port}/"
        server.Handler.app = cls.app
        server.Handler.token = "prueba-lookfeel"  # el token de sesion que exige la API (P1-02)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.PORT, server.URL, server.Handler.app, server.WINDOW, server.Handler.token = cls.saved

    def call(self, path, body=None, header=True):
        headers = {"Content-Type": "application/json", common.TOKEN_HEADER: server.Handler.token}
        if header:
            headers["X-Audios"] = "1"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(server.URL.rstrip("/") + path, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def test_index_lleva_el_nombre(self):
        code, body = self.call("/")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn(f"<title>{common.APP_NAME}</title>", html)
        self.assertNotIn("{{APP_NAME}}", html)

    def test_apagar_exige_cabecera(self):
        self.assertEqual(self.call("/api/apagar", {}, header=False)[0], 403)

    def test_apagar_se_niega_si_graba(self):
        self.app.rec = mock.Mock()
        self.app.rec.alive.return_value = True
        try:
            with mock.patch.object(server, "schedule_shutdown") as sched:
                code, body = self.call("/api/apagar", {})
            self.assertEqual(code, 409)
            self.assertIn("grabación", body["error"])
            self.assertIn(common.APP_NAME, body["error"])
            sched.assert_not_called()
        finally:
            self.app.rec = None

    def test_apagar_programa_el_cierre(self):
        self.app.jobs.list[:] = [FakeJob("a.wav", status="queued"), FakeJob("b.wav", status="done")]
        try:
            with mock.patch.object(server, "schedule_shutdown") as sched:
                code, body = self.call("/api/apagar", {})
            self.assertEqual((code, body), (200, {"ok": True, "activos": 1}))
            sched.assert_called_once()
        finally:
            self.app.jobs.list[:] = []

    def test_mostrar_sin_ventana_abre_el_navegador(self):
        with mock.patch.object(server, "open_ui") as open_ui:
            server.WINDOW = None
            code, body = self.call("/api/mostrar", {})
        self.assertEqual((code, body), (200, {"ok": True, "browser": True}))
        open_ui.assert_called_once()

    def test_mostrar_con_ventana_propia(self):
        win = mock.Mock()
        win.request_show.return_value = "shown"
        with mock.patch.object(server, "open_ui") as open_ui:
            server.WINDOW = win
            try:
                code, body = self.call("/api/mostrar", {})
            finally:
                server.WINDOW = None
        self.assertEqual((code, body), (200, {"ok": True}))
        win.request_show.assert_called_once()
        open_ui.assert_not_called()

    def test_mostrar_con_la_ventana_en_camino_no_abre_edge(self):
        """Doble apertura (msg 1143): el acceso directo otra vez mientras la ventana propia se abre. Ni Edge ni
        nada: sale sola."""
        win = mock.Mock()
        win.request_show.return_value = "pending"
        with mock.patch.object(server, "open_ui") as open_ui:
            server.WINDOW = win
            try:
                code, body = self.call("/api/mostrar", {})
            finally:
                server.WINDOW = None
        self.assertEqual((code, body), (200, {"ok": True, "pending": True}))
        open_ui.assert_not_called()

    def test_mostrar_si_la_ventana_propia_ya_no_esta_abre_edge(self):
        win = mock.Mock()
        win.request_show.return_value = None
        with mock.patch.object(server, "open_ui") as open_ui:
            server.WINDOW = win
            try:
                code, body = self.call("/api/mostrar", {})
            finally:
                server.WINDOW = None
        self.assertEqual((code, body), (200, {"ok": True, "browser": True}))
        open_ui.assert_called_once()

    def test_ocultar(self):
        win = mock.Mock()
        win.hide.return_value = True
        server.WINDOW = win
        try:
            self.assertEqual(self.call("/api/ocultar", {}), (200, {"ok": True}))
        finally:
            server.WINDOW = None
        win.hide.assert_called_once()
        self.assertEqual(self.call("/api/ocultar", {}), (200, {"ok": False}))  # sin ventana propia

    def test_ask_running_to_show_usa_la_ruta(self):
        """El acceso directo (la copia que se abre cuando la app ya corre) lee el token de DATA_DIR\\sesion.token."""
        win = mock.Mock()
        win.request_show.return_value = "shown"
        server.WINDOW = win
        tmp = tempfile.mkdtemp(prefix="chuleta-token-")
        try:
            with patched_paths(DATA_DIR=tmp):
                self.assertIsNone(server.ask_running_to_show())  # sin sesion.token: 403
                win.request_show.assert_not_called()
                server.write_token(tmp, server.Handler.token)
                self.assertEqual(server.ask_running_to_show(), {"ok": True})
        finally:
            server.WINDOW = None
        win.request_show.assert_called_once()

    def test_config_lleva_app_name(self):
        """/api/config es el camino por el que app.js recibe el nombre (y si hay ventana propia) y los datos de
        «Acerca de»."""
        src = read("server.py")
        self.assertIn('"app_name": APP_NAME, "window": WINDOW is not None', src)
        self.assertIn('"acerca": {"licencia": license_name(), "repo": REPO_URL, "datos": DATA_DIR, "donar": DONAR_URL}',
                      src)

    def test_acerca_abrir(self):
        with mock.patch.object(server, "open_dir") as open_dir, mock.patch.object(server.webbrowser, "open") as wb:
            self.assertEqual(self.call("/api/acerca/abrir", {"que": "datos"}), (200, {"ok": True}))
            open_dir.assert_called_once_with(server.DATA_DIR)
            self.assertEqual(self.call("/api/acerca/abrir", {"que": "repo"}), (200, {"ok": True}))
            wb.assert_called_once_with(server.REPO_URL)
            self.assertEqual(self.call("/api/acerca/abrir", {"que": "donar"}), (200, {"ok": True}))
            self.assertEqual(wb.call_args_list[-1].args, (server.DONAR_URL,))
            code, body = self.call("/api/acerca/abrir", {"que": "otra"})
        self.assertEqual(code, 400)
        self.assertEqual(self.call("/api/acerca/abrir", {"que": "datos"}, header=False)[0], 403)

    def test_invitar_a_un_cafe(self):
        """Donaciones discretas: un enlace de PayPal al correo del autor en Acerca de, el README y el LEEME (y su
        plantilla), sin ventanas emergentes ni recordatorios."""
        self.assertTrue(server.DONAR_URL.startswith("https://www.paypal.com/donate?business="), server.DONAR_URL)
        raiz = os.path.dirname(APP)
        for n in ("README.md", "LEEME.txt", os.path.join("_app", "export", "LEEME.txt")):
            with open(os.path.join(raiz, n), encoding="utf-8") as f:
                self.assertIn(server.DONAR_URL, f.read(), n)
        app = read("static", "app.js")
        self.assertNotIn("donar", app.split("Acerca de</legend>")[0].lower())  # solo en Acerca de, nada antes
        self.assertNotIn("toast(", app.split('id="aDonar"')[1].split("</fieldset>")[0])

    def test_diagnostico_enmascara_claves(self):
        """Versión, Python, sistema, motor y las últimas 30 líneas de server.log sin la API key ni las adicionales."""
        tmp = tempfile.mkdtemp(prefix="chuleta-diag-")
        key, extra = "gsk_" + "a1b2c3d4e5f6g7h8i9j0" * 2, "sk-proj-ZZZZYYYYXXXXWWWWVVVV"
        with open(os.path.join(tmp, "server.log"), "w", encoding="utf-8") as f:
            for i in range(40):
                f.write(f"2026-10-31 02:00:{i:02d} I línea {i}\n")
            f.write(f"2026-10-31 02:01:00 I petición con {key} y {extra} y otra gsk_QQQQQQQQQQQQQQQQQQQQ\n")
        settings = mock.Mock()
        settings.get.return_value = {"preset": "nube", "live_preset": "nube",
                                     "cloud": {"provider": "groq", "model": "whisper-large-v3", "api_key": key,
                                               "extra_keys": [extra]}}
        self.app.settings = settings
        try:
            with patched_paths(DATA_DIR=tmp):
                code, body = self.call("/api/diagnostico")
        finally:
            del self.app.settings
        self.assertEqual(code, 200)
        t = body["texto"]
        self.assertTrue(t.startswith(f"{common.APP_NAME} · versión {server.VERSION}\n"), t.splitlines()[0])
        self.assertIn("Python " + sys.version.split()[0], t)
        self.assertIn("nube (groq) · modelo whisper-large-v3", t)
        self.assertIn("Datos: en otra carpeta", t)  # sin la ruta (con el usuario de Windows)
        self.assertNotIn(tmp, t)
        self.assertNotIn(key, t)
        self.assertNotIn(extra, t)
        self.assertNotIn("gsk_QQQQ", t)
        self.assertNotIn(server.masked(key), t)  # la linea entera es texto libre: ni enmascarada
        self.assertIn("2026-10-31 02:00:39 I <texto>\n2026-10-31 02:01:00 I <texto>", t)
        self.assertNotIn("02:00:10", t)  # solo las últimas 30
        self.assertIn("server.log (últimas 30 líneas; lo que no es técnico va como <texto>):", t)

    def test_diagnostico_sin_log(self):
        settings = mock.Mock()
        settings.get.return_value = {"preset": "rapido", "cloud": {}}
        self.app.settings = settings
        try:
            with patched_paths(DATA_DIR=tempfile.mkdtemp(prefix="chuleta-diag-vacio-")):
                code, body = self.call("/api/diagnostico")
        finally:
            del self.app.settings
        self.assertEqual(code, 200)
        self.assertIn("(no hay server.log)", body["texto"])
        self.assertIn("local (rapido)", body["texto"])


class VersionYLicenciaTests(unittest.TestCase):
    def test_version_file_y_fallback(self):
        tmp = tempfile.mkdtemp(prefix="chuleta-ver-")
        vf = os.path.join(tmp, "version.txt")
        with patched_paths(VERSION_FILE=vf):
            self.assertIsNone(server.version_file())  # no existe
            self.assertRegex(server.read_version(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")  # fecha y hora del código
            with open(vf, "w", encoding="utf-8") as f:
                f.write("\n")
            self.assertIsNone(server.version_file())  # vacío
            with open(vf, "w", encoding="utf-8-sig") as f:
                f.write("2026-10-31 · abc1234\r\nlo que sea\r\n")  # con BOM y CRLF también
            self.assertEqual(server.version_file(), "2026-10-31 · abc1234")
            self.assertEqual(server.read_version(), "2026-10-31 · abc1234")
            self.assertEqual(server.package_version(), "2026-10-31 · abc1234")
        self.assertIsInstance(server.VERSION, str)
        self.assertRegex(server.BUILD, r"^[0-9a-f]+$")  # la recarga automática sigue por mtime

    def test_license_name(self):
        self.assertEqual(server.license_name(), "MIT")  # el LICENSE del repo
        tmp = tempfile.mkdtemp(prefix="chuleta-lic-")
        with patched_paths(ROOT=tmp):
            self.assertEqual(server.license_name(), "")
            with open(os.path.join(tmp, "LICENSE"), "w", encoding="utf-8") as f:
                f.write("\nApache License\nVersion 2.0\n")
            self.assertEqual(server.license_name(), "Apache")
            with open(os.path.join(tmp, "LICENSE"), "w", encoding="utf-8") as f:
                f.write("GNU GENERAL PUBLIC LICENSE\n")
            self.assertEqual(server.license_name(), "GNU GENERAL PUBLIC")

    def test_scrub(self):
        self.assertEqual(server.scrub("sin claves"), "sin claves")
        self.assertEqual(server.scrub("x gsk_abcdefghijklmnopqrst y", []), "x gsk_…qrst y")
        self.assertEqual(server.scrub("clave CORTA12", ["CORTA12"]), "clave ••••")
        self.assertEqual(server.scrub("k=1234567890ABCDEFG", ["1234567890ABCDEFG", ""]), "k=1234…DEFG")


class ApagadoTests(unittest.TestCase):
    def test_shutdown_para_todo_y_sale(self):
        app, srv, win, exit_fn = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(server.Handler, "app", app), mock.patch.object(server, "SERVER", srv), \
                mock.patch.object(server, "WINDOW", win), mock.patch.object(logging, "shutdown") as log_shutdown:
            server.shutdown("test", exit_fn=exit_fn)
        app.shutdown_prepare.assert_called_once()
        win.destroy.assert_called_once()
        srv.shutdown.assert_called_once()
        log_shutdown.assert_called_once()
        exit_fn.assert_called_once_with(0)

    def test_shutdown_sigue_aunque_algo_falle(self):
        app = mock.Mock()
        app.shutdown_prepare.side_effect = RuntimeError("x")
        exit_fn = mock.Mock()
        with mock.patch.object(server.Handler, "app", app), mock.patch.object(server, "SERVER", None), \
                mock.patch.object(server, "WINDOW", None), mock.patch.object(logging, "shutdown"):
            server.shutdown("test", exit_fn=exit_fn)
        exit_fn.assert_called_once_with(0)

    def test_schedule_shutdown_espera(self):
        with mock.patch.object(server, "shutdown") as sd:
            t = server.schedule_shutdown("test", delay=0.05)
            t.join(2)
        sd.assert_called_once_with("test")

    def test_shutdown_prepare_para_medidor_y_trabajos(self):
        app = server.App.__new__(server.App)
        app.rec_lock = threading.Lock()
        app.monitor = mock.Mock()
        app.monitor.alive.return_value = True
        app.rec = None
        q, r = FakeJob("q.wav", status="queued"), FakeJob("r.wav", status="running")
        app.jobs = bare_jobs([q, r])
        app.jobs.local = mock.Mock()
        app.jobs.live_worker = mock.Mock()
        monitor = app.monitor
        app.shutdown_prepare()
        monitor.stop.assert_called_once()
        self.assertIsNone(app.monitor)
        self.assertEqual((q.status, r.status, r.cancel), ("cancelled", "running", True))
        app.jobs.local.worker.kill.assert_called_once()
        app.jobs.live_worker.kill.assert_called_once()


class LogSinConsolaTests(unittest.TestCase):
    def test_setup_logging_con_stdout_none(self):
        """pythonw.exe: sys.stdout es None; nada de StreamHandler(None) (escribiria en sys.stderr, tambien None)."""
        tmp = tempfile.mkdtemp(prefix="notas-log-")
        try:
            # WINDOWS=False tambien en el job de Windows: alert() abriria un MessageBox real y dejaria el test colgado
            with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None), \
                    mock.patch.object(common, "WINDOWS", False):
                self.assertFalse(common.has_console())
                common.setup_logging(tmp)
                kinds = [type(h).__name__ for h in common.LOGGER.handlers]
                self.assertEqual(kinds, ["RotatingFileHandler"])
                common.log("hola sin consola")
                common.alert("Prueba", "aviso sin consola")  # sin Windows: solo al log
                try:
                    raise ValueError("traza")
                except ValueError:
                    common.log_exc()
            for h in common.LOGGER.handlers:
                h.flush()
            with open(os.path.join(tmp, "server.log"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("hola sin consola", text)
            self.assertIn("Prueba: aviso sin consola", text)
            self.assertIn("ValueError: traza", text)
        finally:
            common.setup_logging(None)

    def test_arranque_sin_print(self):
        """Con pythonw print() no falla pero se pierde: el arranque y el apagado solo hablan por el log."""
        tree = ast.parse(read("server.py"))
        nombres = ("main", "shutdown", "fatal", "schedule_shutdown", "ask_running_to_show", "show_running")
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in nombres):
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                     and c.func.id == "print"]
            self.assertEqual(calls, [], fn.name)
        src = read("server.py")
        self.assertIn("fatal(f\"No se puede usar el puerto {PORT}", src)
        self.assertIn("MessageBoxW", read("common.py"))


class InstaladorTests(unittest.TestCase):
    """instalar.ps1 e INSTALAR.bat no se pueden ejecutar aqui: se comprueba el texto. El parseo con Windows PowerShell
    5.1 lo hace el job de Windows de tests.yml (ParseFile), y la instalacion de verdad, el workflow lookfeel-e2e."""

    def read_repo(self, *parts):
        with open(os.path.join(os.path.dirname(APP), *parts), "rb") as f:
            return f.read()

    def test_instalador_sin_consola_y_ventana(self):
        ps1 = self.read_repo("_app", "instalar.ps1").decode("utf-8-sig")
        for s in ("[switch]$Consola", "pythonw.exe", "Set-ShortcutAppId", "/api/apagar", "System.Windows.Forms",
                  "GetConsoleWindow", "New-InstallerWindow", "Finish-Ui", '"pywebview==6.2.1"', "Abrir $AppName",
                  "Copiar el detalle", "$sc.WorkingDirectory", '[string]$AppName = "Chuleta"', "[int]$AutoCerrar",
                  "NOTAS_INSTALADOR_AUTOCERRAR", "NOTAS_INSTALADOR_SIMULAR_FALLO", "instalar.log",
                  'static\\chuleta.ico', 'static\\chuleta.png', 'Join-Path $app "Chuleta.bat"',
                  "instalación (sin permisos de administrador)", "Espera a que termine la instalación",
                  "Dónde se procesan tus reuniones", "(versión $ver)", "Join-Path $app 'version.txt'",
                  "-Encoding UTF8"):
            self.assertIn(s, ps1, s)
        m = re.search(r'\$AppId = "([^"]+)"', ps1)
        self.assertEqual(m.group(1), common.APP_ID)  # el .lnk y el proceso llevan el mismo AppUserModelID
        self.assertNotIn("Write-Host \"    $s\"", ps1)  # la salida de pip pasa por Say (consola + ventana)
        for s in ("instalacion (", "Transcripcion en", "notas.ico", "notas.png", '"Notas.bat"', "ABRIR NOTAS.bat)"):
            self.assertNotIn(s, ps1, s)
        self.assertTrue(ps1.endswith("\n") and "\r\n" in ps1)  # CRLF (PowerShell 5.1)

    def test_instalador_migra_el_acceso_directo_antiguo(self):
        """Notas.lnk (nombre anterior) sirve para encontrar la instalación anterior y pasa a llamarse Chuleta.lnk:
        se renombra, nunca se borra; si ya existe el nuevo se conserva el viejo y se dice en el detalle."""
        ps1 = self.read_repo("_app", "instalar.ps1").decode("utf-8-sig")
        for s in ('$LegacyName = "Notas"', 'foreach ($name in @("$AppName.lnk", "$LegacyName.lnk"))',
                  '$legacy = Join-Path $dir "$LegacyName.lnk"',
                  'Rename-Item -LiteralPath $legacy -NewName "$AppName.lnk"',
                  "Se conserva el acceso directo antiguo '$LegacyName'", "pasa a llamarse '$AppName'",
                  '-like "*$LegacyName.bat*"', '@("ABRIR NOTAS.bat", "_app\\Notas.bat")', "ya no hacen falta",
                  '(Join-Path $fromApp "version.txt")', '"ABRIR CHULETA.bat"'):
            self.assertIn(s, ps1, s)
        self.assertNotIn("Remove-Item", ps1)  # el instalador no borra nada del usuario
        self.assertNotIn("NotasLnk", ps1)

    def test_instalar_bat_solo_para_si_falla(self):
        for parts in (("INSTALAR.bat",), ("_app", "export", "INSTALAR.bat")):
            bat = self.read_repo(*parts)
            self.assertIn(b"if errorlevel 1 (", bat)
            self.assertIn(b'instalar.ps1" %*', bat)
            self.assertNotIn(b"\r\necho.\r\npause", bat)


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        return [h() for h in self.handlers]


class FakeWin:
    def __init__(self, title, url, **kw):
        self.title, self.url, self.kw = title, url, kw
        self.events = type("E", (), {})()
        self.events.closing, self.events.shown = FakeEvent(), FakeEvent()
        self.calls = []
        self.native = None
        self.js_result = "no"  # lo que contesta la pagina a onWindowClosing(): "handled" = ella pide /api/ocultar

    def evaluate_js(self, script):
        self.calls.append("js:" + script[:40])
        return self.js_result

    def hide(self):
        self.calls.append("hide")

    def show(self):
        self.calls.append("show")

    def destroy(self):
        self.calls.append("destroy")


class FakeWebview:
    def __init__(self, icon_kw=True):
        self.icon_kw, self.started, self.win = icon_kw, [], None

    def create_window(self, title, url, **kw):
        self.win = FakeWin(title, url, **kw)
        return self.win

    def start(self, **kw):
        if "icon" in kw and not self.icon_kw:
            raise TypeError("start() got an unexpected keyword argument 'icon'")
        self.started.append(kw)
        self.win.events.shown.fire()
        self.closing_results = self.win.events.closing.fire()  # el usuario cierra la ventana: se oculta


class VentanaPropiaTests(unittest.TestCase):
    def make(self, wv):
        self.tmp = tempfile.mkdtemp(prefix="notas-win-")
        return window.Window("http://127.0.0.1:8765/", self.tmp, os.path.join(STATIC, "chuleta.ico"), webview=wv)

    def test_run_configura_la_ventana(self):
        wv = FakeWebview()
        w = self.make(wv)
        self.assertTrue(w.available)
        self.assertTrue(w.run())
        self.assertEqual(wv.win.title, common.APP_NAME)
        self.assertTrue(wv.win.kw["text_select"])
        self.assertTrue(wv.win.kw["zoomable"])
        kw = wv.started[0]
        self.assertFalse(kw["private_mode"])
        self.assertEqual(kw["storage_path"], os.path.join(self.tmp, "webview"))
        self.assertTrue(kw["icon"].endswith("chuleta.ico"))
        # cerrar = ocultar: el evento closing devuelve False y hide() llega poco despues (Timer)
        self.assertEqual(wv.closing_results, [False])
        time.sleep(0.3)
        self.assertIn("hide", wv.win.calls)
        self.assertFalse(w.alive)  # start() ha vuelto

    def test_pywebview_antiguo_sin_icon(self):
        wv = FakeWebview(icon_kw=False)
        w = self.make(wv)
        self.assertTrue(w.run())
        self.assertNotIn("icon", wv.started[0])

    def test_show_y_destroy(self):
        wv = FakeWebview()
        w = self.make(wv)
        self.assertFalse(w.show())  # aun no hay ventana
        w.w, w.alive = FakeWin("t", "u"), True
        self.assertTrue(w.show())
        self.assertIn("show", w.w.calls)
        self.assertTrue(w._on_closing() is False)
        w.destroy()
        self.assertTrue(w.quitting)
        self.assertIn("destroy", w.w.calls)
        self.assertTrue(w._on_closing())  # apagando: ahora si se deja cerrar

    def test_cerrar_pregunta_a_la_pagina(self):
        wv = FakeWebview()
        w = self.make(wv)
        w.w, w.alive = FakeWin("t", "u"), True
        w.w.js_result = "handled"  # la pagina ensena el aviso y pedira /api/ocultar
        self.assertIs(w._on_closing(), False)
        time.sleep(0.3)
        self.assertNotIn("hide", w.w.calls)
        self.assertTrue(any(c.startswith("js:") for c in w.w.calls))
        self.assertTrue(w.hide())  # lo que hace /api/ocultar
        self.assertIn("hide", w.w.calls)
        w.alive = False
        self.assertFalse(w.hide())

    def test_sin_pywebview(self):
        with mock.patch.object(window, "import_webview", return_value=None):
            w = window.Window("http://127.0.0.1:8765/", tempfile.mkdtemp(), "x.ico")
        self.assertFalse(w.available)

    def test_run_falla_sin_romper(self):
        wv = FakeWebview()
        wv.create_window = mock.Mock(side_effect=RuntimeError("sin WebView2"))
        w = self.make(wv)
        self.assertFalse(w.run())
        self.assertFalse(w.alive)


if __name__ == "__main__":
    unittest.main()
