# -*- coding: utf-8 -*-
"""Ocultar Tenca al compartir pantalla (ítem 18; msg 1242, de la research de competencia: Minutes lo hace por defecto).

window.py pone SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) en la ventana propia, en el hilo de la ventana, antes
de enseñarla; Ajustes › Al grabar lo apaga y lo enciende al momento (POST /api/ocultar-captura). En un Windows anterior
a 10 2004 (build 19041) no se aplica (lo pintaría en negro) y se dice. Aquí, con dobles (sin Windows); la ventana real
con WebView2, su marca y capturas de pantalla, en test_ocultar_captura_windows (workflow windows-real)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from support import APP, server  # noqa: F401  (support prepara sys.path)
import window  # noqa: E402
from test_frontend import js_defs, read  # noqa: E402

ICO = os.path.join(APP, "static", "tenca.ico")


class Ev:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class Handle:
    def __init__(self, v):
        self.v = v

    def ToInt64(self):
        return self.v


class Form:
    """La Form de WinForms que pywebview deja en window.native. invoke=True: llamar desde otro hilo (InvokeRequired)."""

    def __init__(self, hwnd=4242, invoke=False):
        self.FormClosing, self.FormClosed = Ev(), Ev()
        self.Handle, self.InvokeRequired, self.invoked = Handle(hwnd), invoke, 0

    def Invoke(self, action):
        self.invoked += 1
        action()


class W:
    def __init__(self, form):
        self.native, self.events = form, types.SimpleNamespace()


class SoporteTests(unittest.TestCase):
    def test_desde_windows_10_2004(self):
        self.assertEqual(window.capture_support(build=19041, windows=True), (True, None))
        self.assertEqual(window.capture_support(build=26100, windows=True), (True, None))
        ok, motivo = window.capture_support(build=19045 - 1000, windows=True)
        self.assertFalse(ok)
        self.assertIn("Windows 10 versión 2004", motivo)
        self.assertIn("18045", motivo)
        self.assertEqual(window.capture_support(build=26100, windows=False), (False, "Solo en Windows."))
        self.assertEqual((window.WDA_NONE, window.WDA_EXCLUDEFROMCAPTURE, window.CAPTURE_MIN_BUILD), (0, 0x11, 19041))


class VentanaMarcaTests(unittest.TestCase):
    def make(self, pref=True, form=None):
        self.tmp = tempfile.mkdtemp(prefix="tenca-captura-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        w = window.Window("http://127.0.0.1:8765/", self.tmp, ICO, webview=object(), hide_from_capture=pref)
        w.w, w.alive = W(form or Form()), True
        return w

    def patches(self, ok=True, build=26100):
        calls = []

        def setaff(hwnd, on):
            calls.append((hwnd, on, threading.current_thread().name))
            return (True, 0) if ok else (False, 5)
        return calls, (mock.patch.object(window, "set_display_affinity", setaff),
                       mock.patch.object(window, "capture_support",
                                         lambda: (True, None) if build >= window.CAPTURE_MIN_BUILD
                                         else (False, "viejo")),
                       mock.patch.object(window, "log"))

    def test_se_pone_antes_de_ensenarla_con_el_ajuste(self):
        w = self.make(pref=lambda: True)
        calls, ps = self.patches()
        with ps[0], ps[1], ps[2]:
            self.assertTrue(w._hook_native())  # before_show: hilo de la ventana, antes de browser.Show()
        self.assertEqual([c[:2] for c in calls], [(4242, True)])
        self.assertEqual((w.capture_applied, w.capture_error), (True, None))
        # en shown ya no se vuelve a poner
        with ps[0], ps[1], ps[2], mock.patch.object(w, "_find_hwnd", lambda: 4242):
            w._on_shown()
        self.assertEqual(len(calls), 1)

    def test_apagado_no_se_toca_nada_al_abrir(self):
        w = self.make(pref=False)
        calls, ps = self.patches()
        with ps[0], ps[1], ps[2]:
            w._hook_native()
        self.assertEqual(calls, [])
        self.assertIs(w.capture_applied, False)

    def test_sin_before_show_se_pone_en_shown(self):
        w = self.make(pref=True)
        w.native = True  # como si los manejadores ya estuvieran: _on_shown no llama a _hook_native
        calls, ps = self.patches()
        with ps[0], ps[1], ps[2], mock.patch.object(w, "_find_hwnd", lambda: 4242):
            w._on_shown()
        self.assertEqual([c[:2] for c in calls], [(4242, True)])

    def test_desde_otro_hilo_va_por_invoke(self):
        form = Form(invoke=True)
        w = self.make(pref=True, form=form)
        calls, ps = self.patches()
        fake_system = types.ModuleType("System")
        fake_system.Action = lambda f: f
        with ps[0], ps[1], ps[2], mock.patch.dict(sys.modules, {"System": fake_system}):
            self.assertTrue(w.set_capture(False))
            self.assertTrue(w.set_capture(True))
        self.assertEqual(form.invoked, 2)
        self.assertEqual([c[:2] for c in calls], [(4242, False), (4242, True)])
        self.assertTrue(w.capture_applied)

    def test_windows_viejo_no_se_aplica_y_se_dice(self):
        w = self.make(pref=True)
        calls, ps = self.patches(build=18363)
        with ps[0], ps[1], ps[2]:
            w._hook_native()
            st = w.capture_state()
        self.assertEqual(calls, [])  # ni se intenta (WDA_MONITOR la pintaria en negro)
        self.assertEqual((w.capture_applied, w.capture_error), (False, "viejo"))
        self.assertFalse(st["soportado"])

    def test_si_windows_no_la_acepta_queda_el_error(self):
        w = self.make(pref=True)
        calls, ps = self.patches(ok=False)
        with ps[0], ps[1], ps[2]:
            self.assertFalse(w.set_capture(True))
        self.assertEqual(w.capture_applied, False)
        self.assertIn("error 5", w.capture_error)

    def test_apagando_no_se_toca(self):
        w = self.make(pref=True)
        w.quitting = True
        calls, ps = self.patches()
        with ps[0], ps[1], ps[2]:
            self.assertFalse(w.set_capture(True))
        self.assertEqual(calls, [])

    def test_un_ajuste_que_falla_al_leerse_no_tumba_la_ventana(self):
        def roto():
            raise RuntimeError("x")
        w = self.make(pref=roto)
        calls, ps = self.patches()
        with ps[0], ps[1], ps[2], mock.patch.object(window, "log_exc"):
            self.assertTrue(w._hook_native())
        self.assertEqual(calls, [])


class ServidorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tenca-captura-srv-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = server.App.__new__(server.App)
        self.app.settings = server.Store(os.path.join(self.tmp, "s.json"), server.DEFAULT_SETTINGS)

    def test_por_defecto_activado(self):
        self.assertIs(server.DEFAULT_SETTINGS["ocultar_al_compartir"], True)
        self.assertTrue(server.ocultar_al_compartir({}))
        self.assertFalse(server.ocultar_al_compartir({"ocultar_al_compartir": False}))

    def test_estado_sin_ventana_propia(self):
        with mock.patch.object(server, "WINDOW", None), \
                mock.patch.object(window, "capture_support", lambda: (True, None)):
            st = server.captura_estado({})
        self.assertEqual((st["activado"], st["soportado"]), (True, False))
        self.assertIn("ventana propia", st["motivo"])

    def test_post_guarda_y_aplica_al_momento(self):
        win = types.SimpleNamespace(calls=[], capture_applied=True, capture_error=None)
        win.set_capture = lambda on: win.calls.append(on) or True
        with mock.patch.object(server, "WINDOW", win), mock.patch.object(server, "log"), \
                mock.patch.object(window, "capture_support", lambda: (True, None)):
            st = self.app.ocultar_captura({"activado": False})
            self.assertIs(self.app.settings.get()["ocultar_al_compartir"], False)
            self.assertEqual(win.calls, [False])
            self.assertEqual((st["activado"], st["soportado"], st["aplicado"]), (False, True, True))
            self.app.ocultar_captura({"activado": "si"})  # solo True de verdad enciende
            self.assertIs(self.app.settings.get()["ocultar_al_compartir"], False)
            self.app.ocultar_captura({})  # sin «activado»: solo dice cómo está
            self.assertEqual(win.calls, [False, False])
            self.app.ocultar_captura({"activado": True})
            self.assertIs(self.app.settings.get()["ocultar_al_compartir"], True)

    def test_rutas_y_config(self):
        src = read_server()
        self.assertIn('"/api/ocultar-captura": lambda q: captura_estado(a.settings.get())}', src)  # GET: solo lee
        self.assertIn('"/api/ocultar-captura": a.ocultar_captura}', src)
        self.assertIn('"ocultar_captura": captura_estado(st),', src)
        self.assertIn("hide_from_capture=lambda: ocultar_al_compartir(Handler.app.settings.get())", src)


class AvisoFlotanteTests(unittest.TestCase):
    """El aviso «● Grabar / ✕» (aviso.py, otro proceso) sale al empezar una llamada: con el ajuste, tampoco se ve al
    compartir pantalla."""

    def lanzados(self, st):
        import calendario
        cmds = []

        class P:
            def __init__(self, cmd, **kw):
                cmds.append(cmd)

            def poll(self):
                return None
        v = calendario.Vigia()
        with mock.patch.object(calendario.subprocess, "Popen", P):
            self.assertTrue(v.tic("Teams", False, st, lambda: {}, 8765, "/app"))
        return cmds[0]

    def test_el_ajuste_llega_al_aviso(self):
        self.assertIn("--ocultar-captura", self.lanzados({"rec_aviso": True}))  # por defecto, sí
        self.assertNotIn("--ocultar-captura", self.lanzados({"rec_aviso": True, "ocultar_al_compartir": False}))

    def test_el_aviso_lo_entiende(self):
        import aviso
        with open(aviso.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('p.add_argument("--ocultar-captura", dest="ocultar_captura", action="store_true")', src)
        self.assertIn("self.info = estilo_windows(root, self.x, self.y, ocultar)", src)
        with mock.patch.object(window, "capture_support", lambda: (False, "viejo")):
            self.assertIsNone(aviso.ocultar_de_capturas(123))  # sin soporte: ni se intenta
        calls = []
        with mock.patch.object(window, "capture_support", lambda: (True, None)), \
                mock.patch.object(window, "set_display_affinity", lambda h, on: calls.append((h, on)) or (True, 0)), \
                mock.patch.object(window, "get_display_affinity", lambda h: 0x11):
            self.assertEqual(aviso.ocultar_de_capturas(123), 0x11)
        self.assertEqual(calls, [(123, True)])


def read_server():
    with open(server.__file__, encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class AjustesTests(unittest.TestCase):
    def run_js(self, code):
        app = read("app.js")
        defs = js_defs(app, ["esc"]) + "\nconst APP_NAME = 'Tenca'; const I = {monitor: '[M]'};\n"
        defs += js_defs(read("ajustes.js"), ["ajOcultarHtml", "ajOcultarAyuda"])
        r = subprocess.run(["node", "-e", defs + "\n" + code], capture_output=True, text=True, encoding="utf-8",
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n").splitlines()

    def test_interruptor(self):
        out = [json.loads(x) for x in self.run_js("""
const v = h => console.log(JSON.stringify(h));
v(ajOcultarHtml({activado: true, soportado: true}));
v(ajOcultarHtml({activado: false, soportado: true}));
v(ajOcultarHtml({activado: true, soportado: false, motivo: 'Solo en Windows.'}));
v(ajOcultarHtml({activado: true, soportado: true, error: 'Windows no lo ha aceptado (error 5).'}));
v(ajOcultarHtml(undefined));""")]
        self.assertIn('id="agOcultar" data-se-guarda-solo checked>', out[0])
        self.assertIn("Ocultar Tenca al compartir pantalla", out[0])
        self.assertIn("(también su aviso «Grabar»)", out[0])
        self.assertIn("Tú la sigues viendo. Tampoco sale en tus capturas de pantalla: para enseñar Tenca en una demo "
                      "o hacerle una captura, quítalo (vale al momento).", out[0])
        self.assertIn('id="agOcultar" data-se-guarda-solo>', out[1])
        self.assertIn("disabled", out[2])
        self.assertIn('<div class="help warn-txt" id="agOcultarNota">Solo en Windows.</div>', out[2])
        self.assertNotIn("checked", out[2])
        self.assertIn("Windows no lo ha aplicado: Windows no lo ha aceptado (error 5).", out[3])
        self.assertEqual(out[4], "")

    def test_se_aplica_al_marcarlo(self):
        js = read("ajustes.js")
        self.assertIn("${ajOcultarHtml((S.config || {}).ocultar_captura)}", js)  # en Calendario y Teams
        self.assertIn("api('/api/ocultar-captura', { activado: on })", js)
        self.assertIn("function bindAjCalendario(d) {\n  bindAjOcultar(d);", js)


if __name__ == "__main__":
    unittest.main()
