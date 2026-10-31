# -*- coding: utf-8 -*-
"""Arreglos de la auditoría final (31/10/2026), grupo Windows: P1-01 (aviso de Windows sin código del usuario), P1-07
(formato regional del filtro de Outlook), P1-08 (selector de carpetas con tildes desde pythonw), P1-09 (la ventana
propia deja apagar Windows), P1-12 (las correcciones de la transcripción no se pierden al cambiar de reunión), P2-32
(«Avisarme si estoy en una llamada» apaga todos los avisos) y P2-35 (sin WebView2, Edge en vez de MSHTML).

Cada test fallaba antes de su arreglo. Los de Windows de verdad (PowerShell 5.1, kernel32, la ventana con pywebview y
WebView2) solo corren allí: los del aviso y del formato regional en cualquier job de Windows (tests.yml); los de la
ventana real, con NOTAS_VENTANA_REAL=1 y pywebview instalado (workflow windows-real)."""
import base64
import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from support import APP, server
import calendario  # noqa: E402
import recording  # noqa: E402
import window  # noqa: E402

WIN = sys.platform == "win32"
ART = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(tempfile.gettempdir(), "notas-artefactos")
STATIC = os.path.join(APP, "static")

# textos que rompian el aviso: comillas tipograficas (PowerShell tambien las toma por comillas), $, backtick y saltos
NASTY = ["Pla d’acció", "L’informe · ‘Kick-off’ ‚x‛", 'Cliente "A" · “Fase 2” · „B“', "$(Get-Date) ${env:USERNAME} $x",
         "`n `$ `` fin", "línea 1\nlínea 2\r\nlínea 3", "'; Remove-Item -Recurse C:\\ ; '", "x’)) > $null; calc; ((’"]


def ps_encoded(script):
    return base64.b64encode(script.encode("utf-16-le")).decode()


# ----------------------------------------------------------------------------- P1-01 · aviso de Windows (toast)

class AvisoSinCodigoDelUsuarioTests(unittest.TestCase):
    """El script de PowerShell del aviso no depende del título ni del texto: van por variables de entorno."""

    def capture(self, title, body):
        calls = []
        with mock.patch.object(recording, "WINDOWS", True), \
                mock.patch.object(recording.subprocess, "Popen", lambda *a, **k: calls.append((a, k))):
            recording.notify(title, body)
        self.assertEqual(len(calls), 1)
        (argv,), kw = calls[0]
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        return argv, script, kw.get("env")

    def test_el_script_es_el_mismo_con_cualquier_texto(self):
        _, ref, _ = self.capture("● Grabando", "Alfa · General")
        for s in NASTY:
            with self.subTest(texto=s):
                argv, script, env = self.capture(s, s)
                self.assertEqual(script, ref)  # ni un caracter del usuario en el codigo
                self.assertNotIn(s, " ".join(argv))
                self.assertEqual(env[recording.TOAST_ENV[0]], s)
                self.assertEqual(env[recording.TOAST_ENV[1]], s)
        self.assertIn("$env:" + recording.TOAST_ENV[0], ref)
        self.assertIn("$env:" + recording.TOAST_ENV[1], ref)

    def test_texto_apto_para_el_entorno(self):
        self.assertEqual(recording.toast_text("a\x00b"), "ab")  # una variable de entorno no admite NUL
        self.assertEqual(recording.toast_text(None), "")
        self.assertEqual(len(recording.toast_text("x" * 5000)), 400)
        _, env = recording.toast_command("t", "b")
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))  # el resto del entorno, el de siempre


def old_toast_script(title, body):
    """El script de antes (comillas simples dobladas a mano): solo para comprobar que el payload de prueba de verdad
    inyecta en PowerShell 5.1."""
    def q(s):
        return s.replace("'", "''")
    return ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] > $null;"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$x = $t.GetElementsByTagName('text');"
            f"$x.Item(0).AppendChild($t.CreateTextNode('{q(title)}')) > $null;"
            f"$x.Item(1).AppendChild($t.CreateTextNode('{q(body)}')) > $null;")


# cierra la cadena con ’ (que q() no doblaba), ejecuta New-Item y deja el resto del script bien formado
INYECCION = "x’)) > $null; New-Item -ItemType File -Path $env:NOTAS_MARCA -Force > $null; $null = ((’"


@unittest.skipUnless(WIN, "solo Windows (PowerShell 5.1)")
class AvisoPowerShellRealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notas-aviso-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_ps(self, script, env, timeout=120):
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", ps_encoded(script)],
                              env=env, capture_output=True, timeout=timeout)

    def test_el_script_del_aviso_se_parsea_sin_errores(self):
        check = ("$tk = $null; $er = $null;"
                 "[System.Management.Automation.Language.Parser]::ParseInput($env:NOTAS_SCRIPT, [ref]$tk, [ref]$er)"
                 " > $null; if ($er.Count) { $er | ForEach-Object { $_.Message }; exit 3 }; exit 0")
        r = self.run_ps(check, dict(os.environ, NOTAS_SCRIPT=recording.TOAST_PS))
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))

    def test_powershell_recibe_el_texto_tal_cual(self):
        for s in NASTY:
            with self.subTest(texto=s):
                _, env = recording.toast_command(s, s + " · cuerpo")
                out_t, out_b = os.path.join(self.tmp, "t.txt"), os.path.join(self.tmp, "b.txt")
                env.update(NOTAS_T=out_t, NOTAS_B=out_b)
                ps = ("$enc = New-Object System.Text.UTF8Encoding $false;"
                      f"[IO.File]::WriteAllText($env:NOTAS_T, [string]$env:{recording.TOAST_ENV[0]}, $enc);"
                      f"[IO.File]::WriteAllText($env:NOTAS_B, [string]$env:{recording.TOAST_ENV[1]}, $enc)")
                r = self.run_ps(ps, env)
                self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
                with open(out_t, encoding="utf-8", newline="") as f:
                    self.assertEqual(f.read(), s)
                with open(out_b, encoding="utf-8", newline="") as f:
                    self.assertEqual(f.read(), s + " · cuerpo")

    def test_un_nombre_preparado_no_ejecuta_nada(self):
        # control: con el script de antes, el mismo nombre ejecuta New-Item (la prueba es sensible de verdad)
        antes = os.path.join(self.tmp, "antes.txt")
        r0 = self.run_ps(old_toast_script(INYECCION, "cuerpo"), dict(os.environ, NOTAS_MARCA=antes))
        self.assertTrue(os.path.exists(antes), "el payload ya no inyecta con el script antiguo: revisar la prueba\n"
                        + r0.stderr.decode("utf-8", "replace")[-1500:])
        # ahora: el aviso real (el mismo argv que notify) con ese nombre de tema en el titulo y en el texto
        ahora = os.path.join(self.tmp, "ahora.txt")
        argv, env = recording.toast_command(f"● Grabando en {INYECCION}", f"{INYECCION} · $(New-Item {ahora})")
        env["NOTAS_MARCA"] = ahora
        r = subprocess.run(argv, env=env, capture_output=True, timeout=120)
        self.assertFalse(os.path.exists(ahora), r.stderr.decode("utf-8", "replace")[-800:])
        os.makedirs(ART, exist_ok=True)
        with open(os.path.join(ART, "aviso_powershell.json"), "w", encoding="utf-8") as f:
            json.dump({"rc": r.returncode, "stderr": r.stderr.decode("utf-8", "replace")[-2000:]}, f,
                      ensure_ascii=False)


# ----------------------------------------------------------------------------- P1-07 · fecha del filtro de Outlook

class FechaJetTests(unittest.TestCase):
    def test_systemtime_propia_de_8_word(self):
        self.assertEqual([n for n, _ in calendario.SYSTEMTIME._fields_],
                         ["wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds"])
        self.assertEqual(ctypes.sizeof(calendario.SYSTEMTIME), 16)

    def test_usa_el_formato_regional_que_devuelve_windows(self):
        seen = {}

        def get_date(loc, flags, pst, fmt, buf, n, cal):
            st = pst._obj
            self.assertIsInstance(st, calendario.SYSTEMTIME)
            seen["date"] = (loc, flags, st.wYear, st.wMonth, st.wDay, st.wDayOfWeek, fmt, n)
            buf.value = "31/10/2026"
            return 11

        def get_time(loc, flags, pst, fmt, buf, n):
            st = pst._obj
            seen["time"] = (loc, flags, st.wHour, st.wMinute, st.wSecond)
            buf.value = "14:05"
            return 6
        with mock.patch.object(calendario, "WINDOWS", True), \
                mock.patch.object(calendario, "_nls", return_value=(get_date, get_time)):
            self.assertEqual(calendario.fecha_jet(datetime(2026, 10, 31, 14, 5, 42), locale="es-ES"),
                             "31/10/2026 14:05")
            self.assertEqual(seen["date"], ("es-ES", 0x1, 2026, 10, 31, 6, None, 80))  # DATE_SHORTDATE; sabado
            self.assertEqual(seen["time"], ("es-ES", 0x2, 14, 5, 0))  # TIME_NOSECONDS
            calendario.fecha_jet(datetime(2026, 11, 1, 9, 0))
            self.assertIsNone(seen["date"][0])  # sin locale: el del usuario (LOCALE_NAME_USER_DEFAULT)
            self.assertEqual(seen["date"][5], 0)  # domingo

    def test_si_windows_falla_formato_de_ee_uu(self):
        with mock.patch.object(calendario, "WINDOWS", True), \
                mock.patch.object(calendario, "_nls", return_value=(lambda *a: 0, lambda *a: 0)), \
                mock.patch.object(calendario.ctypes, "get_last_error", create=True, return_value=87):
            self.assertEqual(calendario.fecha_jet(datetime(2026, 10, 31, 14, 5)), "10/31/2026 14:05")


@unittest.skipUnless(WIN, "solo Windows (kernel32)")
class FechaJetWindowsRealTests(unittest.TestCase):
    DT = datetime(2026, 10, 31, 14, 5, 9)

    def test_formato_espanol(self):
        self.assertEqual(calendario.fecha_jet(self.DT, locale="es-ES"), "31/10/2026 14:05")

    def test_formato_de_ee_uu(self):
        self.assertEqual(calendario.fecha_jet(self.DT, locale="en-US"), "10/31/2026 2:05 PM")

    def test_formato_del_usuario(self):
        buf = ctypes.create_unicode_buffer(85)
        ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85)
        esperado = os.environ.get("NOTAS_LOCALE_ESPERADO")
        if esperado:  # el workflow windows-real hace Set-Culture es-ES antes de los tests
            self.assertEqual(buf.value, esperado)
        s = calendario.fecha_jet(self.DT)
        self.assertEqual(s, calendario.fecha_jet(self.DT, locale=buf.value))
        if buf.value == "es-ES":
            self.assertEqual(s, "31/10/2026 14:05")
        os.makedirs(ART, exist_ok=True)
        with open(os.path.join(ART, "fecha_jet.json"), "w", encoding="utf-8") as f:
            json.dump({"locale": buf.value, "fecha_jet": s}, f, ensure_ascii=False)


# ----------------------------------------------------------------------------- P1-08 · selector de carpetas

RUTAS = ("C:\\Users\\José\\OneDrive - Compañía\\Año 2026", "D:\\Proyectos\\Łódź · Ærø")


def fake_tk(ruta):
    """Un tkinter de mentira para el hijo de link_pick: askdirectory devuelve `ruta` sin abrir nada."""
    return ("import sys, types\n"
            "tk = types.ModuleType('tkinter'); fd = types.ModuleType('tkinter.filedialog')\n"
            "class _Tk:\n"
            "    def withdraw(self): pass\n"
            "    def attributes(self, *a): pass\n"
            f"tk.Tk = _Tk; tk.filedialog = fd; fd.askdirectory = lambda **k: {ruta!r}\n"
            "sys.modules['tkinter'] = tk; sys.modules['tkinter.filedialog'] = fd\n")


class SelectorCarpetasUtf8Tests(unittest.TestCase):
    """Con pythonw (el acceso directo) la salida del hijo iba en cp1252: se simula con PYTHONIOENCODING=cp1252."""

    def pick(self, ruta):
        real_run = subprocess.run

        def run(args, **kw):
            args = list(args)
            i = args.index("-c")
            args[i + 1] = fake_tk(ruta) + args[i + 1]
            return real_run(args, **kw)
        app = server.App.__new__(server.App)
        with mock.patch.object(server.subprocess, "run", run), \
                mock.patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}):
            os.environ.pop("PYTHONUTF8", None)
            return app.link_pick({"title": "Carpeta «Año»"})["path"]

    def test_ruta_con_tildes_enes_y_letras_fuera_de_cp1252(self):
        for ruta in RUTAS:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.pick(ruta), os.path.normpath(ruta))

    def test_cancelar_devuelve_vacio(self):
        self.assertEqual(self.pick(""), "")


# ----------------------------------------------------------------------------- P1-09 y P2-35 · ventana propia

class FakeNetEvent:
    """Un evento .NET de mentira (FormClosing/FormClosed) que admite += como los de pythonnet."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, args):
        for h in self.handlers:
            h(self, args)
        return args


class FakeArgs:
    def __init__(self, reason, cancel=False):
        self.CloseReason, self.Cancel = reason, cancel


class FakeForm:
    """La Form de WinForms de pywebview: su manejador de FormClosing va primero y cancela si events.closing devuelve
    False (pywebview 6.2.1, winforms.py on_closing)."""

    def __init__(self, pv_window):
        self.FormClosing, self.FormClosed = FakeNetEvent(), FakeNetEvent()
        self.FormClosing += lambda s, a: setattr(a, "Cancel", True) if pv_window.events.closing.fire_any_false() \
            else None
        self.Handle = None


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        return [h() for h in self.handlers]

    def fire_any_false(self):
        return any(v is False for v in self.fire())


class FakeWin:
    def __init__(self, title="t", url="u", **kw):
        self.title, self.url, self.kw = title, url, kw
        self.events = type("E", (), {})()
        self.events.closing, self.events.shown, self.events.before_show = FakeEvent(), FakeEvent(), FakeEvent()
        self.calls, self.js_result = [], "no"
        self.native = FakeForm(self)

    def evaluate_js(self, script):
        self.calls.append("js")
        return self.js_result

    def hide(self):
        self.calls.append("hide")

    def show(self):
        self.calls.append("show")

    def destroy(self):
        self.calls.append("destroy")


class FakeWebview:
    def __init__(self, motor="edgechromium"):
        self.motor, self.created, self.started, self.win = motor, 0, [], None

    def initialize(self):
        if self.motor is None:
            raise RuntimeError("pythonnet no carga")
        return type("G", (), {"renderer": self.motor})()

    def create_window(self, title, url, **kw):
        self.created += 1
        self.win = FakeWin(title, url, **kw)
        return self.win

    def start(self, **kw):
        self.started.append(kw)
        self.win.events.before_show.fire()  # pywebview: en el hilo de la ventana, antes de ensenarla
        self.win.events.shown.fire()


class VentanaFinDeSesionTests(unittest.TestCase):
    def setUp(self):
        # nada de esto puede llamar al shutdown de verdad (os._exit) aunque un test se olvide de poner su doble
        self._hook = mock.patch.object(window, "_END_SESSION", [])
        self._hook.start()
        self.wv = FakeWebview()
        self.w = window.Window("http://127.0.0.1:8765/", tempfile.mkdtemp(prefix="notas-win-"), "x.ico",
                               webview=self.wv)
        self.ends = []
        self.w.on_end_session = self.ends.append
        with mock.patch.object(window.Window, "_find_hwnd", return_value=None):
            self.assertTrue(self.w.run())
        self.form = self.wv.win.native
        self.assertTrue(self.w.native)  # enganchado en before_show

    def tearDown(self):
        self._hook.stop()

    def close(self, reason):
        args = self.form.FormClosing.fire(FakeArgs(reason))
        time.sleep(0.2)
        return args

    def test_la_x_sigue_ocultando(self):
        a = self.close("UserClosing")
        self.assertTrue(a.Cancel)
        self.assertIn("js", self.wv.win.calls)  # se pregunta a la pagina (aviso de segundo plano) y se oculta
        self.assertIn("hide", self.wv.win.calls)
        self.assertFalse(self.w.quitting)

    def test_apagar_windows_no_se_cancela(self):
        for reason in ("WindowsShutDown", "CloseReason.WindowsShutDown", "1"):
            with self.subTest(reason=reason):
                a = self.close(reason)
                self.assertFalse(a.Cancel)  # WM_QUERYENDSESSION devuelve TRUE: Windows puede apagar
                self.assertEqual(self.wv.win.calls, [])  # ni aviso de segundo plano ni ocultar
                self.assertFalse(self.w.quitting)  # aun no: el apagado se puede cancelar

    def test_fin_de_sesion_apaga_limpio_una_vez(self):
        self.close("WindowsShutDown")
        self.form.FormClosed.fire(FakeArgs("WindowsShutDown"))
        self.form.FormClosed.fire(FakeArgs("WindowsShutDown"))
        self.assertEqual(self.ends, ["Windows cierra la sesión o se apaga"])
        self.assertTrue(self.w.quitting)

    def test_apagado_cancelado_y_luego_la_x(self):
        self.assertFalse(self.close("WindowsShutDown").Cancel)  # WM_QUERYENDSESSION...
        # ...y WM_ENDSESSION con FALSE: WinForms no cierra (no hay FormClosed) y la app sigue como estaba
        a = self.close("UserClosing")
        self.assertTrue(a.Cancel)
        self.assertIn("hide", self.wv.win.calls)
        self.assertEqual(self.ends, [])

    def test_cerrar_desde_fuera_y_application_exit(self):
        self.assertFalse(self.close("TaskManagerClosing").Cancel)
        self.assertFalse(self.close("ApplicationExitCall").Cancel)
        self.assertEqual(self.wv.win.calls, [])
        self.form.FormClosed.fire(FakeArgs("TaskManagerClosing"))
        self.assertEqual(self.ends, ["Windows ha pedido cerrar"])

    def test_apagar_desde_ajustes_no_repite_el_apagado(self):
        self.w.destroy()
        self.assertFalse(self.close("UserClosing").Cancel)
        self.form.FormClosed.fire(FakeArgs("UserClosing"))
        self.assertEqual(self.ends, [])

    def test_sin_doble_lo_registrado_por_server(self):
        hecho = []
        self.w.on_end_session = None
        window.Window.set_end_session(hecho.append)
        self.form.FormClosed.fire(FakeArgs("WindowsShutDown"))
        self.assertEqual(hecho, ["Windows cierra la sesión o se apaga"])

    def test_server_registra_su_apagado(self):
        self._hook.stop()
        try:
            self.assertIs(window._END_SESSION[0], server.shutdown)
        finally:
            self._hook.start()

    def test_close_reason(self):
        for v, name in (("WindowsShutDown", "WindowsShutDown"), ("CloseReason.TaskManagerClosing",
                                                                 "TaskManagerClosing"),
                        (1, "WindowsShutDown"), ("3", "UserClosing"), (None, "")):
            self.assertEqual(window.close_reason(v), name)


class VentanaMotorTests(unittest.TestCase):
    """P2-35: si pywebview fuera a usar MSHTML (sin el runtime de WebView2), no se abre: server.py abre Edge."""

    def make(self, motor):
        wv = FakeWebview(motor)
        return wv, window.Window("http://127.0.0.1:8765/", tempfile.mkdtemp(prefix="notas-win-"), "x.ico", webview=wv)

    def test_sin_webview2_no_abre_la_ventana(self):
        for motor in ("mshtml", "cef"):
            with self.subTest(motor=motor):
                wv, w = self.make(motor)
                with mock.patch.object(window, "WINDOWS", True):
                    self.assertFalse(w.run())
                self.assertEqual(wv.created, 0)
                self.assertEqual(wv.started, [])

    def test_con_webview2_o_sin_saberlo_si(self):
        for motor in ("edgechromium", None):  # None: initialize() falla y decide start(), como antes
            with self.subTest(motor=motor):
                wv, w = self.make(motor)
                with mock.patch.object(window, "WINDOWS", True), \
                        mock.patch.object(window.Window, "_find_hwnd", return_value=None), \
                        mock.patch.object(window.Window, "_hook_native", return_value=False):
                    self.assertTrue(w.run())
                self.assertEqual(wv.created, 1)

    def test_fuera_de_windows_no_se_mira(self):
        wv, w = self.make("cocoa")
        with mock.patch.object(window, "WINDOWS", False), \
                mock.patch.object(window.Window, "_find_hwnd", return_value=None):
            self.assertTrue(w.run())


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


WM_QUERYENDSESSION, WM_ENDSESSION, SMTO_ABORTIFHUNG = 0x11, 0x16, 0x2
SIN_ARREGLO = ("import runpy, sys\n"
               f"sys.path.insert(0, {APP!r})\n"
               "import window\n"
               "window.Window._hook_native = lambda self: False\n"
               f"runpy.run_path({os.path.join(APP, 'server.py')!r}, run_name='__main__')\n")


@unittest.skipUnless(WIN and os.environ.get("NOTAS_VENTANA_REAL"), "solo Windows con NOTAS_VENTANA_REAL=1")
class VentanaRealTests(unittest.TestCase):
    """La app de verdad (server.py con pywebview y WebView2) ante los mensajes con los que Windows apaga."""

    def setUp(self):
        try:
            import webview  # noqa: F401
        except ImportError:
            self.skipTest("sin pywebview")
        self.data = tempfile.mkdtemp(prefix="notas-ventana-")
        self.port = free_port()
        self.log = os.path.join(self.data, "server.log")
        self.out = open(os.path.join(self.data, "consola.txt"), "wb")
        self.p = None

    def start(self, code=None, **env):
        cmd = ["-c", code] if code else [os.path.join(APP, "server.py")]
        self.p = subprocess.Popen([sys.executable, *cmd, "--port", str(self.port), "--data", self.data], cwd=APP,
                                  stdout=self.out, stderr=subprocess.STDOUT, env=dict(os.environ, **env))

    def tearDown(self):
        if self.p is not None and self.p.poll() is None:
            self.p.kill()
            self.p.wait(15)
        self.out.close()
        os.makedirs(ART, exist_ok=True)
        for name in ("server.log", "consola.txt"):
            src = os.path.join(self.data, name)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(ART, f"{self.id().rsplit('.', 1)[-1]}.{name}"))

    def read_log(self):
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def wait_log(self, pattern, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            m = re.search(pattern, self.read_log())
            if m:
                return m
            if self.p.poll() is not None:
                break
            time.sleep(0.5)
        self.fail(f"sin «{pattern}» en server.log:\n{self.read_log()[-3000:]}")

    def send(self, hwnd, msg, wparam, lparam=0, timeout_ms=15000):
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        f = u.SendMessageTimeoutW
        f.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                      ctypes.POINTER(ctypes.c_size_t)]
        f.restype = ctypes.c_size_t
        res = ctypes.c_size_t(0)
        ok = f(hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(res))
        return bool(ok), res.value

    def is_window(self, hwnd):
        from ctypes import wintypes
        u = ctypes.WinDLL("user32")
        u.IsWindow.argtypes = [wintypes.HWND]
        return bool(u.IsWindow(hwnd))

    def test_control_sin_el_arreglo_la_app_impide_apagar(self):
        # la misma app sin los manejadores propios de FormClosing (como en 5c55e54): pywebview cancela el cierre y
        # WM_QUERYENDSESSION devuelve FALSE. Demuestra que la prueba de abajo detecta el defecto.
        self.start(code=SIN_ARREGLO)
        hwnd = int(self.wait_log(r"ventana abierta \(WebView2; hwnd (\d+)\)").group(1))
        time.sleep(3)
        ok, res = self.send(hwnd, WM_QUERYENDSESSION, 0, 0)
        self.assertTrue(ok)
        self.assertEqual(res, 0)

    def test_windows_puede_apagar_y_la_app_se_apaga_limpia(self):
        self.start()
        m = self.wait_log(r"ventana abierta \(WebView2; hwnd (\d+)\)")
        hwnd = int(m.group(1))
        time.sleep(3)  # que la pagina termine de cargar
        # 1) apagado que otra app cancela: WM_QUERYENDSESSION dice que si y WM_ENDSESSION(FALSE) no cierra nada
        ok, res = self.send(hwnd, WM_QUERYENDSESSION, 0, 0)
        self.assertTrue(ok)
        self.assertEqual(res, 1, "la ventana impide apagar Windows")
        self.send(hwnd, WM_ENDSESSION, 0, 0)
        time.sleep(2)
        self.assertIsNone(self.p.poll())
        self.assertTrue(self.is_window(hwnd))
        log = self.read_log()
        self.assertNotIn("ventana oculta", log)  # ni se oculta ni salta el aviso de «sigue en segundo plano»
        self.assertNotIn("apagando (", log)
        # 2) con la ventana oculta (cerrada a segundo plano) tampoco lo impide
        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 0)  # SW_HIDE
        time.sleep(0.5)
        ok, res = self.send(hwnd, WM_QUERYENDSESSION, 0, 0x80000000)  # ENDSESSION_LOGOFF: cerrar sesion
        self.assertEqual((ok, res), (True, 1))
        # 3) la sesion se cierra de verdad: apagado limpio y fin del proceso
        self.send(hwnd, WM_ENDSESSION, 1, 0x80000000)
        try:
            rc = self.p.wait(30)
        except subprocess.TimeoutExpired:
            self.fail("la app no ha terminado tras WM_ENDSESSION:\n" + self.read_log()[-3000:])
        log = self.read_log()
        self.assertEqual(rc, 0, log[-3000:])
        self.assertIn("Windows cierra la sesión o se apaga: apagado limpio", log)
        fin = re.search(r"(?m)^\S+ \S+ \w apagado$", log)  # la última línea de shutdown()
        self.assertTrue(fin, log[-3000:])
        self.assertLess(log.index("apagando (Windows cierra la sesión o se apaga)"), fin.start())

    def edge_app_pids(self):
        """Procesos de Edge abiertos en modo app con la URL de esta copia (open_ui)."""
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like "
              f"'*--app=http://127.0.0.1:{self.port}*' }} | ForEach-Object {{ $_.ProcessId }}")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=60)
        return [int(x) for x in r.stdout.split() if x.isdigit()]

    def test_sin_webview2_abre_edge(self):
        # PYWEBVIEW_GUI=mshtml hace que pywebview elija MSHTML, como en un PC sin el runtime de WebView2
        self.start(PYWEBVIEW_GUI="mshtml")
        self.wait_log(r"la ventana propia necesita WebView2 y pywebview usaría «mshtml»")
        pids, end = [], time.time() + 45
        try:
            while not pids and time.time() < end:
                time.sleep(1)
                pids = self.edge_app_pids()
            self.assertTrue(pids, "no se ha abierto Edge en modo app:\n" + self.read_log()[-2000:])
            time.sleep(2)
            self.assertNotIn("ventana abierta", self.read_log())  # nunca la ventana con el motor de IE
        finally:
            self.p.kill()
            for pid in pids:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60)


# ----------------------------------------------------------------------------- P1-12 · correcciones de la transcripción

def edit_block():
    """Las funciones de edición y guardado de la transcripción de app.js (de syncEdits a startEdit, y finishEdit)."""
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        src = f.read()
    a, b = src.index("function syncEdits() {"), src.index("function startEdit() {")
    i = src.index("async function finishEdit() {")
    return src[a:b] + src[i:src.index("\n}\n", i) + 3]


HARNESS = r"""
const out = { api: [], fetch: [], toasts: [] };
let apiFails = 0, apiDelay = 0;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const S = { tx: null, editing: false, dirty: false, saveTimer: 0,
            audios: new Map([['r/a.wav', { path: 'r/a.wav', title: 'Kick-off' }]]) };
const $ = () => null, $$ = () => [];
const esc = s => String(s), fullTitle = a => a.title, renderTxSection = () => { };
const toast = (html, kind, fn) => out.toasts.push({ html, kind: kind || '', fn });
async function api(path, body) {
  const call = { path, n: body.segments.length, first: body.segments[0] && body.segments[0].text, t0: Date.now() };
  out.api.push(call);
  if (apiDelay) await sleep(apiDelay);
  call.t1 = Date.now();
  if (apiFails > 0) { apiFails--; throw new Error('sin conexión'); }
  return { tx_mtime: 99, live: false };
}
globalThis.fetch = (url, opt) => {
  const bytes = new TextEncoder().encode(opt.body).length;
  out.fetch.push({ url, keepalive: !!opt.keepalive, bytes });
  if (opt.keepalive && bytes > 65536) return Promise.reject(new TypeError('Failed to fetch'));  // Chromium
  return Promise.resolve({ ok: true, json: async () => ({ tx_mtime: 99 }) });
};
function segs(n, len) {
  return Array.from({ length: n }, (_, i) =>
    ({ start: i, end: i + 1, text: ('Corrección número ' + i + ' · ').padEnd(len, 'ñ') }));
}
function edit(n, len) { S.tx = { path: 'r/a.wav', segs: segs(n, len), mtime: 1 }; S.editing = true; S.dirty = true; }
const pending = () => typeof txPending === 'undefined' ? -1 : txPending.size;
"""


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class TranscripcionNoSePierdeTests(unittest.TestCase):
    def run_js(self, scenario):
        code = HARNESS + edit_block().replace("TX_RETRY_MS = 1000", "TX_RETRY_MS = 5") + \
            "\n(async () => {\n" + scenario + "\nfor (const t of out.toasts) delete t.fn;\n" \
            "console.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(3); });\n"
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_cambiar_de_reunion_guarda_una_transcripcion_larga_entera(self):
        out = self.run_js("edit(1500, 90); flushEdits(); await sleep(50);"
                          "out.pending = pending(); out.editing = S.editing;")
        saves = [c for c in out["api"] if c["path"] == "/api/transcript/save"]
        self.assertEqual([c["n"] for c in saves], [1500])  # todo el texto, con un fetch normal (sin keepalive)
        self.assertEqual([f for f in out["fetch"] if f["keepalive"]], [])
        self.assertEqual(out["pending"], 0)
        self.assertFalse(out["editing"])
        self.assertEqual(out["toasts"], [])

    def test_si_falla_reintenta_y_avisa_con_reintentar(self):
        out = self.run_js("apiFails = 3; edit(3, 20); flushEdits(); await sleep(150);"
                          "out.pending1 = pending(); const t = out.toasts.find(t => t.kind === 'err');"
                          "out.retry = !!(t && t.fn); if (t && t.fn) await t.fn(); out.pending2 = pending();")
        self.assertEqual(len(out["api"]), 4)  # 3 intentos y el «reintentar» del aviso
        errs = [t["html"] for t in out["toasts"] if t["kind"] == "err"]
        self.assertEqual(len(errs), 1)
        self.assertIn("No se han podido guardar tus correcciones de «Kick-off»", errs[0])
        self.assertIn("reintentar", errs[0])
        self.assertTrue(out["retry"])
        self.assertEqual((out["pending1"], out["pending2"]), (1, 0))

    def test_al_cerrar_keepalive_solo_si_cabe(self):
        out = self.run_js("apiDelay = 30; edit(3, 20); out.small = flushTxOnUnload();"
                          "edit(1500, 90); out.big = flushTxOnUnload(); await sleep(100);")
        self.assertEqual(out["small"], 0)
        self.assertEqual(out["big"], 1)  # no cabe en keepalive: la pagina pide confirmar antes de cerrarse
        kept = [f for f in out["fetch"] if f["keepalive"]]
        self.assertEqual(len(kept), 1)
        self.assertLessEqual(kept[0]["bytes"], 60 * 1024)

    def test_listo_no_dice_guardado_si_no_se_guardo(self):
        out = self.run_js("apiFails = 1; edit(3, 20); await finishEdit();"
                          "out.editing = S.editing; out.dirty = S.dirty;")
        self.assertTrue(out["editing"])
        self.assertTrue(out["dirty"])
        self.assertFalse(any("Cambios guardados" in t["html"] for t in out["toasts"]))
        self.assertTrue(any(t["kind"] == "err" for t in out["toasts"]))

    def test_una_version_vieja_no_pisa_a_la_nueva(self):
        out = self.run_js("apiFails = 1; apiDelay = 30; edit(3, 20); const first = saveEdits();"
                          "S.tx.segs[0].text = 'nuevo'; S.dirty = true; flushEdits();"
                          "await first; await sleep(150); out.pending = pending();")
        self.assertEqual([c["first"] for c in out["api"]][1:], ["nuevo"])  # la vieja no se reenvia
        self.assertEqual(len(out["api"]), 2)
        self.assertGreaterEqual(out["api"][1]["t0"], out["api"][0]["t1"])  # en orden, una detras de otra
        self.assertEqual(out["pending"], 0)
        self.assertEqual([t for t in out["toasts"] if t["kind"] == "err"], [])

    def test_la_recarga_automatica_espera_al_guardado(self):
        out = self.run_js("apiFails = 99; edit(3, 20); out.r1 = await txSavedForReload();"
                          "apiFails = 0; out.r2 = await txSavedForReload(); out.pending = pending();")
        self.assertFalse(out["r1"])
        self.assertTrue(out["r2"])
        self.assertEqual(out["pending"], 0)


class TranscripcionTextoTests(unittest.TestCase):
    def test_sin_keepalive_al_cambiar_de_reunion(self):
        block = edit_block()
        fn = block[block.index("function flushEdits() {"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertNotIn("keepalive", fn)
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("&& await txSavedForReload()) return location.reload()", src)
        self.assertIn("if (flushTxOnUnload()) { e.preventDefault(); e.returnValue = ''; }", src)
        i = src.index("async function loadTranscript(a) {")
        load = src[i:src.index("\n}\n", i)]
        # al volver a la misma reunion, primero termina el guardado en curso y luego se lee el texto
        self.assertLess(load.index("await txInflight.get(want)"), load.index("api('/api/transcript?path="))


# ------------------------------------------------------------------------ P2-32 · «Avisarme si estoy en una llamada»

class FakeProc:
    spawned = []

    def __init__(self, cmd, **kw):
        self.cmd, self.alive = cmd, True
        FakeProc.spawned.append(self)

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False


class InterruptorDeLlamadasTests(unittest.TestCase):
    def setUp(self):
        FakeProc.spawned = []
        self.tmp = tempfile.mkdtemp(prefix="notas-llamada-")
        a = self.a = server.App.__new__(server.App)
        a.settings = server.Store(os.path.join(self.tmp, "settings.json"), server.DEFAULT_SETTINGS)
        a.rec, a.meeting, a.vigia = None, None, calendario.Vigia()
        a._vocab_terms = lambda: []
        self.toasts = []
        self.patches = [mock.patch.object(calendario.subprocess, "Popen", FakeProc),
                        mock.patch.object(server, "mic_meeting_app", lambda: "Teams"),
                        mock.patch.object(server, "notify", lambda t, b: self.toasts.append(t)),
                        mock.patch.object(calendario, "reunion_actual", lambda *a, **k: {"sugerencia": {}})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_desmarcado_no_avisa_de_ninguna_forma(self):
        self.a.settings.update(lambda d: d.update(meeting_detect=False, rec_aviso=True))
        n = None
        for _ in range(3):
            n = self.a._meeting_step(n)
        self.assertEqual(FakeProc.spawned, [])  # ni aviso flotante...
        self.assertEqual(self.toasts, [])        # ...ni toast...
        self.assertIsNone(self.a.meeting)        # ...ni chip (/api/rec/status manda meeting = None)

    def test_desmarcar_cierra_el_aviso_abierto(self):
        n = self.a._meeting_step(None)
        self.assertEqual(len(FakeProc.spawned), 1)
        self.assertEqual(self.a.meeting, "Teams")
        self.a.settings.update(lambda d: d.update(meeting_detect=False))
        self.a._meeting_step(n)
        self.assertFalse(FakeProc.spawned[0].alive)
        self.assertIsNone(self.a.meeting)

    def test_marcado_sigue_como_antes(self):
        self.a.settings.update(lambda d: d.update(meeting_detect=True, rec_aviso=False))
        n = self.a._meeting_step(None)
        self.a._meeting_step(n)
        self.assertEqual(self.toasts, ["¿Quieres tomar notas de la sesión?"])  # una vez por llamada
        self.assertEqual(FakeProc.spawned, [])

    def test_la_interfaz_quita_el_chip_al_momento(self):
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("async function pollRec() {")
        fn = src[i:src.index("\n}\n", i)]
        self.assertIn("if (S.config && S.config.meeting_detect === false) S.rec.meeting = null;", fn)
        self.assertLess(fn.index("S.rec.meeting = null"), fn.index("renderMeetChip()"))


if __name__ == "__main__":
    unittest.main()
