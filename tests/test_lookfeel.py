# -*- coding: utf-8 -*-
"""Aspecto y arranque sin consola (31/10/2026): nombre de la app centralizado (common.APP_NAME), /api/apagar y
/api/mostrar sobre el servidor HTTP real, apagado ordenado, log sin consola (pythonw.exe) y la ventana propia
(window.py) con un pywebview de mentira."""
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

from support import APP, FakeJob, bare_jobs, server

import common  # noqa: E402
import window  # noqa: E402

STATIC = os.path.join(APP, "static")


def read(*parts):
    with open(os.path.join(APP, *parts), encoding="utf-8") as f:
        return f.read()


class NombreCentralizadoTests(unittest.TestCase):
    def test_constantes(self):
        self.assertEqual(common.APP_NAME, "Notas")
        self.assertRegex(common.APP_ID, r"^[A-Za-z0-9._-]{1,128}$")  # AppUserModelID: sin espacios, <= 128
        self.assertIs(server.APP_NAME, common.APP_NAME)

    def test_server_sin_literales_sueltos(self):
        """Solo quedan "Notas/..." (la carpeta raiz del zip, que no se renombra) y las docstrings."""
        tree = ast.parse(read("server.py"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docs.add(id(body[0].value))
        sueltos = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "Notas" in node.value:
                if id(node) in docs or node.value.startswith("Notas/"):
                    continue
                sueltos.append((node.lineno, node.value))
        self.assertEqual(sueltos, [])

    def test_js_y_html_usan_app_name(self):
        app = read("static", "app.js")
        self.assertIn("let APP_NAME = document.title;", app)
        self.assertIn("if (c.app_name) APP_NAME = c.app_name;", app)
        # literales con el nombre de la app en app.js: ninguno (los textos usan ${APP_NAME})
        self.assertEqual(re.findall(r"""['"][^'"\n]*\bNotas\b[^'"\n]*['"]""", app), [])
        self.assertNotIn("Notas · versión", app)
        self.assertNotIn("Bienvenido a Notas", read("static", "notas.js"))
        self.assertNotIn("Notas separará", read("static", "voces.js"))
        html = read("static", "index.html")
        self.assertIn("<title>{{APP_NAME}}</title>", html)
        self.assertIn('alt="">{{APP_NAME}}</a>', html)
        self.assertNotIn("<title>Notas", html)

    def test_boton_apagar_en_ajustes(self):
        app = read("static", "app.js")
        for s in ('id="sOff"', "confirmShutdown()", "api('/api/apagar', {})", "function shutdownScreen()",
                  "se ha apagado</h2>", "if (S.off) return;"):
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
        cls.saved = (server.PORT, server.URL, server.Handler.app, server.WINDOW)
        server.PORT, server.URL = cls.port, f"http://127.0.0.1:{cls.port}/"
        server.Handler.app = cls.app
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.PORT, server.URL, server.Handler.app, server.WINDOW = cls.saved

    def call(self, path, body=None, header=True):
        headers = {"Content-Type": "application/json"}
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
        win.show.return_value = True
        with mock.patch.object(server, "open_ui") as open_ui:
            server.WINDOW = win
            try:
                code, body = self.call("/api/mostrar", {})
            finally:
                server.WINDOW = None
        self.assertEqual((code, body), (200, {"ok": True}))
        win.show.assert_called_once()
        open_ui.assert_not_called()

    def test_ask_running_to_show_usa_la_ruta(self):
        win = mock.Mock()
        win.show.return_value = True
        server.WINDOW = win
        try:
            self.assertTrue(server.ask_running_to_show())
        finally:
            server.WINDOW = None
        win.show.assert_called_once()

    def test_config_lleva_app_name(self):
        """/api/config es el camino por el que app.js recibe el nombre (y si hay ventana propia)."""
        src = read("server.py")
        self.assertIn('"app_name": APP_NAME, "window": WINDOW is not None', src)


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
            with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
                self.assertFalse(common.has_console())
                common.setup_logging(tmp)
                kinds = [type(h).__name__ for h in common.LOGGER.handlers]
                self.assertEqual(kinds, ["RotatingFileHandler"])
                common.log("hola sin consola")
                common.alert("Prueba", "aviso sin consola")  # fuera de Windows: solo al log
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
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name in ("main", "shutdown", "fatal", "schedule_shutdown", "ask_running_to_show")):
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                     and c.func.id == "print"]
            self.assertEqual(calls, [], fn.name)
        src = read("server.py")
        self.assertIn("fatal(f\"No se puede usar el puerto {PORT}", src)
        self.assertIn("MessageBoxW", read("common.py"))


class InstaladorTests(unittest.TestCase):
    """instalar.ps1 e INSTALAR.bat no se pueden ejecutar aqui: se comprueba el texto (el parseo lo hace pwsh aparte)."""

    def read_repo(self, *parts):
        with open(os.path.join(os.path.dirname(APP), *parts), "rb") as f:
            return f.read()

    def test_instalador_sin_consola_y_ventana(self):
        ps1 = self.read_repo("_app", "instalar.ps1").decode("utf-8-sig")
        for s in ("[switch]$Consola", "pythonw.exe", "Set-ShortcutAppId", "/api/apagar", "System.Windows.Forms",
                  "GetConsoleWindow", "New-InstallerWindow", "Finish-Ui", '"pywebview==6.2.1"', "Abrir $AppName",
                  "Copiar el detalle", "$sc.WorkingDirectory", "[string]$AppName"):
            self.assertIn(s, ps1, s)
        m = re.search(r'\$AppId = "([^"]+)"', ps1)
        self.assertEqual(m.group(1), common.APP_ID)  # el .lnk y el proceso llevan el mismo AppUserModelID
        self.assertNotIn("Write-Host \"    $s\"", ps1)  # la salida de pip pasa por Say (consola + ventana)

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
        return window.Window("http://127.0.0.1:8765/", self.tmp, os.path.join(STATIC, "notas.ico"), webview=wv)

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
        self.assertTrue(kw["icon"].endswith("notas.ico"))
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
