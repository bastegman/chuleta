# -*- coding: utf-8 -*-
"""Icono en la bandeja del sistema (backlog del 7-8/11/2026, ítem 2; Bernardo, msg 1143: «que Chuleta quedase abierta
como una app aquí corriendo aunque le ponga salir, solo se pueda cerrar si la cierro desde ahí al salir»).

En cualquier sistema, con un doble de WinForms: el menú (Abrir, Grabar o Parar, Arrancar con Windows, Salir), el clic,
el texto del icono, el globo, el Dispose al apagar y la caída limpia a lo de antes si el icono no se puede poner; la
ventana (cerrar = ocultar sin preguntar a la página y, la primera vez, el globo); Salir = el apagado de Ajustes; y
Arrancar con Windows (copia del acceso directo del menú Inicio en la carpeta Inicio). Con la bandeja de verdad (WinForms
y WebView2): test_auditoria_windows.VentanaRealTests en windows-real.
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from support import APP, server
import bandeja  # noqa: E402
import window  # noqa: E402


class Ev:
    def __init__(self):
        self.h = []

    def __iadd__(self, fn):
        self.h.append(fn)
        return self

    def fire(self, *a):
        for f in list(self.h):
            f(*a)


class Item:
    def __init__(self, text):
        self.Text, self.Click, self.Font, self.Checked, self.Enabled = text, Ev(), "normal", False, True


class Items(list):
    def Add(self, x):
        self.append(x)


class Menu:
    def __init__(self):
        self.Items, self.Opening, self.disposed = Items(), Ev(), False

    def Dispose(self):
        self.disposed = True


class Icon:
    falla = False

    def __init__(self):
        if Icon.falla:
            raise RuntimeError("sin bandeja")
        self.Icon = self.Text = self.ContextMenuStrip = None
        self.Visible, self.MouseClick, self.balloons, self.disposed = False, Ev(), [], False

    def ShowBalloonTip(self, ms, titulo, texto, tipo):
        self.balloons.append((titulo, texto))

    def Dispose(self):
        self.disposed = True


class Form:
    """BeginInvoke ejecuta en el acto (como si el hilo de la ventana estuviera libre); con `colgado`, nunca."""

    InvokeRequired = True

    def __init__(self, colgado=False):
        self.colgado, self.llamadas = colgado, 0

    def BeginInvoke(self, fn):
        self.llamadas += 1
        if not self.colgado:
            fn()


def wf():
    ns = type("WF", (), {})()
    ns.Action = lambda f: f
    ns.Font = lambda base, estilo: f"negrita({base})"
    ns.FontStyle = type("FS", (), {"Bold": 1})
    ns.Icon = lambda path: ("icono", path)
    ns.BindingFlags = type("BF", (), {"NonPublic": 1, "Instance": 2})
    ns.ContextMenuStrip, ns.NotifyIcon, ns.ToolStripMenuItem = Menu, Icon, Item
    ns.ToolStripSeparator = lambda: "-"
    ns.MouseButtons = type("MB", (), {"Left": "L", "Right": "R"})
    ns.ToolTipIcon = type("TTI", (), {"Info": "info"})
    return ns


def esperar(cond, timeout=3.0):
    fin = time.time() + timeout
    while time.time() < fin and not cond():
        time.sleep(0.01)
    return cond()


class BandejaTests(unittest.TestCase):
    def setUp(self):
        Icon.falla = False
        self.hechos = []
        self.estado = {"grabando": None, "trabajos": 0}
        self.inicio = [False]
        acciones = {"abrir": lambda: self.hechos.append("abrir"), "grabar": lambda: self.hechos.append("grabar"),
                    "salir": lambda: self.hechos.append("salir"),
                    "inicio": (lambda: self.inicio[0], lambda v: self.inicio.__setitem__(0, v))}
        self.b = bandeja.Bandeja("chuleta.ico", acciones, lambda: self.estado, wf=wf())
        self.addCleanup(self.b.close)

    def textos(self):
        return [x if isinstance(x, str) else x.Text for x in self.b.menu.Items]

    def test_crea_el_icono_con_su_menu(self):
        with mock.patch.object(bandeja, "log") as log:
            self.assertTrue(self.b.create(Form()))
        self.assertTrue(self.b.alive)
        self.assertTrue(self.b.icon.Visible)
        self.assertEqual(self.b.icon.Text, server.APP_NAME)
        self.assertEqual(self.b.icon.Icon, ("icono", "chuleta.ico"))
        self.assertEqual(self.textos(), [f"Abrir {server.APP_NAME}", "Grabar  (Ctrl+Alt+R)", "-",
                                         "Arrancar con Windows", "-", "Salir"])
        self.assertTrue(self.b.items["abrir"].Font.startswith("negrita"))
        self.assertIs(self.b.icon.ContextMenuStrip, self.b.menu)
        self.assertIn("icono en la bandeja (hwnd None)", [c[0][0] for c in log.call_args_list])

    def test_sin_arrancar_con_windows_no_sale_en_el_menu(self):
        b = bandeja.Bandeja("x.ico", {"abrir": None, "grabar": None, "salir": None}, lambda: {}, wf=wf())
        self.addCleanup(b.close)
        self.assertTrue(b.create(Form()))
        self.assertEqual([x if isinstance(x, str) else x.Text for x in b.menu.Items],
                         [f"Abrir {server.APP_NAME}", "Grabar  (Ctrl+Alt+R)", "-", "Salir"])

    def test_el_menu_y_el_clic_llaman_a_su_accion_fuera_del_hilo_de_la_ventana(self):
        self.b.create(Form())
        hilos = []
        self.b.acciones["abrir"] = lambda: hilos.append(threading.current_thread().name)
        self.b.items["abrir"].Click.fire(None, None)
        self.b.items["grabar"].Click.fire(None, None)
        self.b.items["salir"].Click.fire(None, None)
        self.assertTrue(esperar(lambda: hilos and {"grabar", "salir"} <= set(self.hechos)))
        self.assertEqual(hilos, ["bandeja-abrir"])
        self.b.icon.MouseClick.fire(None, type("A", (), {"Button": "R"})())  # el derecho es el menú (WinForms)
        self.b.icon.MouseClick.fire(None, type("A", (), {"Button": "L"})())
        self.assertTrue(esperar(lambda: len(hilos) == 2))

    def test_el_menu_se_pone_al_dia_al_abrirse(self):
        self.b.create(Form())
        self.estado["grabando"] = 75.0
        self.inicio[0] = True
        self.b.menu.Opening.fire(None, None)
        self.assertEqual(self.b.items["grabar"].Text, "Parar la grabación  (Ctrl+Alt+R)")
        self.assertTrue(self.b.items["inicio"].Checked)
        self.b.acciones["inicio"] = (lambda: None, lambda v: None)  # sin acceso directo instalado: no se puede
        self.b.menu.Opening.fire(None, None)
        self.assertFalse(self.b.items["inicio"].Enabled)

    def test_arrancar_con_windows_cambia_al_pulsarlo(self):
        self.b.create(Form())
        self.b.items["inicio"].Click.fire(None, None)
        self.assertTrue(esperar(lambda: self.inicio[0] is True))
        self.b.items["inicio"].Click.fire(None, None)
        self.assertTrue(esperar(lambda: self.inicio[0] is False))

    def test_texto_del_icono(self):
        t = bandeja.tooltip
        app = server.APP_NAME
        self.assertEqual(t({}), app)
        self.assertEqual(t({"grabando": 754.6}), f"{app} · grabando 12:34")
        self.assertEqual(t({"grabando": 3725, "trabajos": 1}), f"{app} · grabando 1:02:05 · 1 trabajo en marcha")
        self.assertEqual(t({"trabajos": 3}), f"{app} · 3 trabajos en marcha")
        self.assertLessEqual(len(t({"grabando": 10 ** 9, "trabajos": 10 ** 9})), bandeja.TEXT_MAX)

    def test_update_solo_si_cambia_y_por_el_hilo_de_la_ventana(self):
        f = Form()
        self.b.create(f)
        self.estado["trabajos"] = 2
        self.b.update()
        self.assertEqual(self.b.icon.Text, f"{server.APP_NAME} · 2 trabajos en marcha")
        n = f.llamadas
        self.b.update()
        self.assertEqual(f.llamadas, n)  # igual: no se vuelve a pedir

    def test_globo(self):
        self.b.create(Form())
        self.assertTrue(self.b.balloon("t", "x"))
        self.assertEqual(self.b.icon.balloons, [("t", "x")])

    def test_close_quita_el_icono_una_vez(self):
        self.b.create(Form())
        icon, menu = self.b.icon, self.b.menu
        with mock.patch.object(bandeja, "log") as log:
            self.b.close()
            self.b.close()
        self.assertFalse(icon.Visible)
        self.assertTrue(icon.disposed and menu.disposed)
        self.assertFalse(self.b.alive)
        self.assertEqual([c[0][0] for c in log.call_args_list], ["icono de la bandeja retirado"])
        self.assertFalse(self.b.balloon("t", "x"))  # ya no hay icono

    def test_close_con_el_hilo_de_la_ventana_colgado_no_se_queda_esperando(self):
        self.b.create(Form(colgado=True))
        icon = self.b.icon
        t = time.time()
        with mock.patch.object(bandeja, "log"):
            self.b.close(wait=0.3)
        self.assertLess(time.time() - t, 2)
        self.assertTrue(icon.disposed)

    def test_si_falla_no_hay_icono_y_se_dice(self):
        Icon.falla = True
        with mock.patch.object(bandeja, "log") as log, mock.patch.object(bandeja, "log_exc"):
            self.assertFalse(self.b.create(Form()))
        self.assertFalse(self.b.alive)
        self.assertIn("sin icono en la bandeja (RuntimeError)", log.call_args_list[0][0][0])
        self.b.close()  # sin icono: nada que quitar, sin errores

    def test_una_accion_que_falla_no_rompe_nada(self):
        self.b.create(Form())
        self.b.acciones["salir"] = mock.Mock(side_effect=RuntimeError("x"))
        with mock.patch.object(bandeja, "log_exc") as log_exc:
            self.b.items["salir"].Click.fire(None, None)
            self.assertTrue(esperar(lambda: log_exc.called))


# ----------------------------------------------------------------------------- la ventana con icono (window.py)

class FakeTray:
    def __init__(self, ok=True):
        self.ok, self.alive, self.created, self.closed, self.balloons = ok, False, [], 0, []

    def create(self, form):
        self.created.append(form)
        self.alive = self.ok
        return self.ok

    def balloon(self, titulo, texto):
        self.balloons.append(titulo)
        return True

    def close(self):
        self.closed += 1
        self.alive = False


class NativeForm:
    InvokeRequired = False

    def __init__(self):
        self.FormClosing, self.FormClosed = Ev(), Ev()


class FakeWin:
    def __init__(self):
        self.native, self.calls = NativeForm(), []
        self.events = type("E", (), {})()

    def evaluate_js(self, js):
        self.calls.append("js")
        return "no"

    def hide(self):
        self.calls.append("hide")

    def destroy(self):
        self.calls.append("destroy")


class VentanaConBandejaTests(unittest.TestCase):
    def make(self, tray):
        self.data = tempfile.mkdtemp(prefix="chuleta-bandeja-")
        w = window.Window("http://127.0.0.1:8765/", self.data, os.path.join(APP, "static", "chuleta.ico"),
                          webview=object(), tray=tray)
        w.w, w.alive = FakeWin(), True
        return w

    def test_el_icono_se_pone_en_el_hilo_de_la_ventana_antes_de_ensenarla(self):
        tray = FakeTray()
        w = self.make(tray)
        self.assertTrue(w._hook_native())
        self.assertEqual(tray.created, [w.w.native])
        self.assertTrue(w.has_tray)

    def test_cerrar_oculta_sin_preguntar_a_la_pagina_y_avisa_la_primera_vez(self):
        tray = FakeTray()
        w = self.make(tray)
        w._hook_native()
        with mock.patch.object(window, "log"):
            w._ask_page_then_hide()
            w._ask_page_then_hide()
        self.assertEqual(w.w.calls, ["hide", "hide"])  # sin «js»: el aviso de la página no sale
        self.assertEqual(tray.balloons, [f"{server.APP_NAME} sigue aquí"])  # solo la primera vez
        with open(os.path.join(self.data, "bandeja.json"), encoding="utf-8") as f:
            self.assertIn("aviso_cerrar", json.load(f))

    def test_sin_icono_como_antes(self):
        tray = FakeTray(ok=False)
        w = self.make(tray)
        w._hook_native()
        self.assertFalse(w.has_tray)
        w._ask_page_then_hide()
        self.assertEqual(w.w.calls, ["js", "hide"])  # la página decide (su aviso de «segundo plano»)

    def test_apagar_y_fin_de_sesion_quitan_el_icono(self):
        tray = FakeTray()
        w = self.make(tray)
        w._hook_native()
        w.destroy()
        self.assertEqual(tray.closed, 1)
        self.assertLess(0, len(w.w.calls))
        tray2 = FakeTray()
        w2 = self.make(tray2)
        w2._hook_native()
        w2.on_end_session = lambda motivo: None
        with mock.patch.object(window, "log"):
            w2._end_session("WindowsShutDown")
        self.assertEqual(tray2.closed, 1)


# ----------------------------------------------------------------------------- server.py: Salir y Arrancar con Windows

class Job:
    def __init__(self, status):
        self.status = status


class SalirTests(unittest.TestCase):
    def app(self, grabando=False, jobs=()):
        a = mock.Mock()
        a.rec = mock.Mock() if grabando else None
        if grabando:
            a.rec.alive.return_value = True
            a.rec.started = time.time() - 10
        a.jobs.lock = threading.Lock()
        a.jobs.list = [Job(s) for s in jobs]
        return a

    def salir(self, app, si=True):
        with mock.patch.object(server.Handler, "app", app), mock.patch.object(server, "tray_balloon") as globo, \
                mock.patch.object(server, "ask_yes_no", return_value=si) as pregunta, \
                mock.patch.object(server, "log"):
            server.tray_salir()
        return globo, pregunta

    def test_salir_es_el_apagado_de_ajustes(self):
        a = self.app()
        _, pregunta = self.salir(a)
        a.apagar.assert_called_once_with({"origen": "bandeja"})
        pregunta.assert_not_called()  # sin trabajos, sin pregunta

    def test_grabando_no_sale(self):
        a = self.app(grabando=True)
        globo, _ = self.salir(a)
        a.apagar.assert_not_called()
        self.assertIn("grabando", globo.call_args[0][0])

    def test_con_trabajos_pregunta(self):
        a = self.app(jobs=("running", "done", "queued"))
        _, pregunta = self.salir(a, si=False)
        a.apagar.assert_not_called()
        self.assertIn("2 trabajos en marcha", pregunta.call_args[0][1])
        a = self.app(jobs=("running",))
        self.salir(a, si=True)
        a.apagar.assert_called_once()

    def test_apagar_dice_desde_donde(self):
        a = server.App.__new__(server.App)
        a.rec, a.jobs = None, mock.Mock(lock=threading.Lock(), list=[])
        lines = []
        with mock.patch.object(server, "schedule_shutdown") as sched, mock.patch.object(server, "log", lines.append):
            self.assertEqual(a.apagar({"origen": "bandeja"}), {"ok": True, "activos": 0})
            a.apagar({})
        self.assertEqual([c[0][0] for c in sched.call_args_list], ["bandeja > Salir", "Ajustes > Apagar"])
        self.assertIn("apagar pedido desde la bandeja (0 trabajos en cola o en marcha)", lines)
        self.assertIn("apagar pedido desde la interfaz (0 trabajos en cola o en marcha)", lines)

    def test_estado_del_icono(self):
        with mock.patch.object(server.Handler, "app", self.app(grabando=True, jobs=("running", "error"))):
            e = server.tray_estado()
        self.assertGreaterEqual(e["grabando"], 9)
        self.assertEqual(e["trabajos"], 1)
        with mock.patch.object(server.Handler, "app", None):
            self.assertEqual(server.tray_estado(), {"grabando": None, "trabajos": 0})

    def test_sin_windows_no_hay_icono(self):
        with mock.patch.object(server, "WINDOWS", False):
            self.assertIsNone(server.make_tray("x.ico"))
        with mock.patch.object(server, "WINDOWS", True):
            t = server.make_tray("x.ico")
        self.assertIsInstance(t, bandeja.Bandeja)
        self.assertEqual(set(t.acciones), {"abrir", "grabar", "salir", "inicio"})


class ArrancarConWindowsTests(unittest.TestCase):
    def setUp(self):
        self.appdata = tempfile.mkdtemp(prefix="chuleta-appdata-")
        self.addCleanup(shutil.rmtree, self.appdata, True)
        self.progs = os.path.join(self.appdata, "Microsoft", "Windows", "Start Menu", "Programs")
        for p in (mock.patch.object(server, "WINDOWS", True), mock.patch.dict(os.environ, {"APPDATA": self.appdata}),
                  mock.patch.object(server, "log")):
            p.start()
            self.addCleanup(p.stop)
        self.origen = os.path.join(self.progs, f"{server.APP_NAME}.lnk")
        self.destino = os.path.join(self.progs, "Startup", f"{server.APP_NAME}.lnk")

    def lnk(self, contenido=b"lnk-1"):
        os.makedirs(self.progs, exist_ok=True)
        with open(self.origen, "wb") as f:
            f.write(contenido)

    def test_sin_acceso_directo_instalado_no_se_puede(self):
        self.assertIsNone(server.inicio_windows_leer())
        self.assertIsNone(server.inicio_windows_poner(True))
        self.assertFalse(os.path.exists(self.destino))

    def test_activar_y_desactivar(self):
        self.lnk()
        self.assertIs(server.inicio_windows_leer(), False)
        self.assertIs(server.inicio_windows_poner(True), True)
        with open(self.destino, "rb") as f:
            self.assertEqual(f.read(), b"lnk-1")  # el mismo acceso directo: destino, iconos y AppUserModelID
        self.assertIs(server.inicio_windows_poner(False), False)
        self.assertFalse(os.path.exists(self.destino))
        self.assertIs(server.inicio_windows_poner(False), False)  # ya quitado: sin errores

    def test_al_actualizar_la_copia_se_pone_al_dia(self):
        self.lnk(b"lnk-1")
        server.inicio_windows_poner(True)
        self.lnk(b"lnk-2")  # INSTALAR.bat rehace el del menú Inicio
        server.inicio_windows_al_dia()
        with open(self.destino, "rb") as f:
            self.assertEqual(f.read(), b"lnk-2")

    def test_desactivado_no_se_crea_al_arrancar(self):
        self.lnk()
        server.inicio_windows_al_dia()
        self.assertFalse(os.path.exists(self.destino))

    def test_fuera_de_windows_nada(self):
        with mock.patch.object(server, "WINDOWS", False):
            self.assertIsNone(server.inicio_windows_leer())


if __name__ == "__main__":
    unittest.main()
