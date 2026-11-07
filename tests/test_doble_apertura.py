# -*- coding: utf-8 -*-
"""Doble apertura al arrancar (backlog del 7-8/11/2026, ítem 1; Bernardo, msg 1143: «se me abrían como 2 veces la
app»). Dos lanzamientos seguidos (doble clic, o el acceso directo otra vez porque la ventana tarda: en su PC, 31 s
entre «arranque» y «ventana abierta» el 02/11) no pueden dejar dos ventanas.

Aquí, en cualquier sistema: dos server.py de verdad con un pywebview falso que reproduce lo que importa del 6.2.1
(`show()` espera al evento `shown` hasta 20 s; `shown` llama a sus manejadores en otro hilo) y con tiempos simulados
para lo que en Windows tarda (importar pythonnet y pywebview, `initialize()` y crear la ventana de WebView2). open_ui()
no abre Edge: lo anota. En windows-real, lo mismo con la ventana de verdad (test_auditoria_windows.VentanaRealTests).
"""
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from support import APP, REPO, server
import window  # noqa: E402

FAKE_WEBVIEW = r'''
"""pywebview falso para test_doble_apertura (lo que importa del 6.2.1). Tiempos por variables de entorno."""
import os
import threading
import time


def _d(name):
    try:
        return float(os.environ.get(name) or 0)
    except ValueError:
        return 0.0


def _rec(what):
    path = os.environ.get("CHULETA_FALSO_EVENTOS")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {os.getpid()} {what}\n")


time.sleep(_d("CHULETA_FALSO_IMPORTAR"))  # en Windows: pythonnet.load("netfx") + import webview, antes de servir


class Event:
    """Como webview.event.Event: += manejadores; set() los llama en otro hilo (en el mismo si sync) y luego marca."""

    def __init__(self, sync=False):
        self._items, self._sync, self._ev = [], sync, threading.Event()

    def __iadd__(self, fn):
        self._items.append(fn)
        return self

    def set(self):
        def run():
            for fn in list(self._items):
                try:
                    fn()
                except Exception:
                    pass
        if self._items:
            if self._sync:
                run()
            else:
                threading.Thread(target=run, daemon=True).start()
        self._ev.set()

    def is_set(self):
        return self._ev.is_set()

    def wait(self, timeout=None):
        return self._ev.wait(timeout)


class _Events:
    pass


class Window:
    def __init__(self, title, url):
        self.title, self.url, self.native = title, url, None
        self.events = _Events()
        self.events.closing, self.events.shown, self.events.loaded = Event(), Event(), Event()
        self.events.before_show = Event(sync=True)
        self._closed = threading.Event()

    def _shown_call(self):  # el decorador _shown_call de pywebview: espera a «shown» hasta 20 s
        if not self.events.shown.wait(20):
            raise RuntimeError("Main window failed to start")

    def show(self):
        self._shown_call()
        _rec("show")

    def hide(self):
        self._shown_call()
        _rec("hide")

    def destroy(self):
        self._shown_call()
        _rec("destroy")
        self._closed.set()

    def evaluate_js(self, js):
        return "no"


windows = []


def create_window(title, url, **kw):
    time.sleep(_d("CHULETA_FALSO_CREAR"))  # en Windows, window.renderer() -> initialize(), antes de que alive sea True
    w = Window(title, url)
    windows.append(w)
    return w


def start(icon=None, **kw):
    w = windows[0]
    time.sleep(_d("CHULETA_FALSO_MOSTRAR"))  # WebView2: entorno, proceso del navegador y la Form
    w.events.before_show.set()
    _rec("ventana")
    w.events.shown.set()
    w._closed.wait()
'''

LAUNCHER = r'''
import os, sys, time
sys.path[:0] = [os.environ["CHULETA_FALSO_DIR"], os.environ["CHULETA_FALSO_APP"]]
import server


def open_ui(token=None):  # en vez de abrir Edge, se anota (con el pid de quien lo abre)
    with open(os.environ["CHULETA_FALSO_EVENTOS"], "a", encoding="utf-8") as f:
        f.write(f"{time.time():.3f} {os.getpid()} edge\n")


server.open_ui = open_ui
if os.environ.get("CHULETA_FALSO_PING_LENTO"):  # la app abierta pero ocupada: su primer /api/ping tarda
    import threading
    _get, _n, _lk = server.Handler.do_GET, [0], threading.Lock()

    def do_GET(self):
        if self.path.startswith("/api/ping"):
            with _lk:
                _n[0] += 1
                primero = _n[0] == 1
            if primero:
                time.sleep(float(os.environ["CHULETA_FALSO_PING_LENTO"]))
        return _get(self)
    server.Handler.do_GET = do_GET
if os.environ.get("CHULETA_FALSO_PORT_WAIT"):
    server.PORT_WAIT = float(os.environ["CHULETA_FALSO_PORT_WAIT"])
sys.argv = [os.path.join(os.environ["CHULETA_FALSO_APP"], "server.py"), "--port", os.environ["CHULETA_FALSO_PUERTO"],
            "--data", os.environ["CHULETA_FALSO_DATOS"]]
server.main()
'''


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Arranques:
    """Una carpeta de la app (copia de _app), sus datos, un puerto y los lanzamientos de server.py contra ellos."""

    def __init__(self, base, app_dir, fake_dir, **tiempos):
        self.dir = tempfile.mkdtemp(prefix="arranques-", dir=base)
        self.datos = os.path.join(self.dir, "datos")
        self.eventos = os.path.join(self.dir, "eventos.txt")
        self.port = puerto_libre()
        self.env = dict(os.environ, PYTHONIOENCODING="utf-8", CHULETA_FALSO_DIR=fake_dir, CHULETA_FALSO_APP=app_dir,
                        CHULETA_FALSO_EVENTOS=self.eventos, CHULETA_FALSO_PUERTO=str(self.port),
                        CHULETA_FALSO_DATOS=self.datos)
        self.env.pop("CHULETA_DATOS", None)
        for k, v in tiempos.items():
            self.env[f"CHULETA_FALSO_{k.upper()}"] = str(v)
        self.procs = []

    def lanzar(self, **extra):
        out = open(os.path.join(self.dir, f"consola{len(self.procs)}.txt"), "wb")
        env = dict(self.env, **{k.upper(): str(v) for k, v in extra.items()})
        p = subprocess.Popen([sys.executable, "-c", LAUNCHER], cwd=self.env["CHULETA_FALSO_APP"], stdout=out,
                             stderr=subprocess.STDOUT, env=env)
        p.t0, p.out = time.time(), out
        self.procs.append(p)
        return p

    def ocultar(self):
        """POST /api/ocultar con el token, como la página al cerrar la ventana con la X."""
        import json
        import urllib.request
        with open(os.path.join(self.datos, "sesion.token"), encoding="utf-8") as f:
            token = f.read().strip()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/ocultar", data=b"{}", method="POST",
                                     headers={"X-Chuleta-Token": token, "X-Audios": "1",
                                              "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=10) as r:
            return json.load(r)

    def eventos_de(self, que):
        try:
            with open(self.eventos, encoding="utf-8") as f:
                return [line.split() for line in f if line.split()[2:3] == [que]]
        except OSError:
            return []

    def log(self):
        try:
            with open(os.path.join(self.datos, "server.log"), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def esperar(self, cond, timeout):
        fin = time.time() + timeout
        while time.time() < fin:
            if cond():
                return True
            time.sleep(0.1)
        return cond()

    def cerrar(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    pass
            p.out.close()


class DobleAperturaTests(unittest.TestCase):
    """Dos lanzamientos seguidos con tiempos de un PC lento: una ventana y ningún Edge, se lance cuando se lance el
    segundo. Los escenarios corren a la vez (cada uno con su carpeta de datos y su puerto)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuleta-doble-")
        cls.app_dir = os.path.join(cls.tmp, "Chuleta", "_app")
        shutil.copytree(os.path.join(REPO, "_app"), cls.app_dir, ignore=shutil.ignore_patterns("__pycache__"))
        cls.fake_dir = os.path.join(cls.tmp, "falso")
        os.makedirs(os.path.join(cls.fake_dir, "webview"))
        with open(os.path.join(cls.fake_dir, "webview", "__init__.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_WEBVIEW)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def escenario(self, nombre, retraso, tiempos, extra=None, segunda_vez=False, ocultar=False, extra1=None):
        """Lanza la primera copia, espera `retraso` s y lanza la segunda. Devuelve lo que pasó."""
        a = Arranques(self.tmp, self.app_dir, self.fake_dir, **tiempos)
        r = {"nombre": nombre, "a": a}
        try:
            p1 = a.lanzar(**dict(extra or {}, **(extra1 or {})))
            if segunda_vez:  # la ventana ya está abierta: el acceso directo la vuelve a enseñar
                a.esperar(lambda: a.eventos_de("ventana"), 60)
                time.sleep(1.0)
                if ocultar:  # cerrada con la X: oculta, la app sigue en segundo plano
                    r["ocultar"] = a.ocultar()
            time.sleep(retraso)
            p2 = a.lanzar(**(extra or {}))
            # una de las dos se queda (la primera, salvo si se lanzan a la vez) y la otra termina
            a.esperar(lambda: p1.poll() is not None or p2.poll() is not None, 60)
            r["t2"] = time.time() - p2.t0
            sale = p2 if p2.poll() is not None or p1.poll() is None else p1
            r["sale"] = "segunda" if sale is p2 else "primera"
            r["rc2"] = sale.poll()
            a.esperar(lambda: a.eventos_de("ventana") or (p1.poll() is not None and p2.poll() is not None), 60)
            time.sleep(1.5)  # lo que llegue tarde (un Edge de la primera copia al terminar su ventana)
            r["vivo1"] = (p1.poll() is None) != (p2.poll() is None)  # exactamente una sigue viva
            r["ventanas"] = a.eventos_de("ventana")
            r["edge"] = a.eventos_de("edge")
            r["show"] = a.eventos_de("show")
            r["hide"] = a.eventos_de("hide")
            r["log"] = a.log()
        finally:
            a.cerrar()
        return r

    def correr(self, escenarios):
        res = [None] * len(escenarios)

        def uno(i, kw):
            try:
                res[i] = self.escenario(**kw)
            except Exception as e:  # pragma: no cover - se ve en el assert
                res[i] = {"nombre": kw["nombre"], "error": repr(e)}
        hilos = [threading.Thread(target=uno, args=(i, kw)) for i, kw in enumerate(escenarios)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(180)
        return res

    def comprobar(self, r, show=False):
        datos = {k: v for k, v in r.items() if k not in ("log", "a")}
        msg = f"{r['nombre']}: {datos}\n{r.get('log', '')[-3000:]}"
        self.assertNotIn("error", r, msg)
        self.assertTrue(r["vivo1"], msg)
        self.assertEqual(len(r["ventanas"]), 1, msg)
        self.assertEqual(r["edge"], [], msg)  # ningún Edge, ni de la primera copia ni de la segunda
        self.assertEqual(r["rc2"], 0, msg)
        self.assertLess(r["t2"], 15, msg)  # la segunda copia no se queda esperando (ni acaba en un aviso de error)
        if show:
            self.assertEqual(len(r["show"]), 1, msg)

    def test_segundo_lanzamiento_durante_el_arranque(self):
        lento = {"crear": 3, "mostrar": 2}  # en el PC de Bernardo: 31 s entre «arranque» y «ventana abierta»
        res = self.correr([
            dict(nombre="a la vez", retraso=0.0, tiempos=lento),
            dict(nombre="antes de que la ventana exista", retraso=1.5, tiempos=lento),
            dict(nombre="ventana creada pero sin enseñar", retraso=4.0,
                 tiempos={"crear": 3, "mostrar": 9}),
            dict(nombre="la primera aún no sirve (importa pywebview)", retraso=1.0,
                 tiempos={"importar": 6, "crear": 1, "mostrar": 1}, extra={"chuleta_falso_port_wait": 3}),
            dict(nombre="ya abierta", retraso=0.0, tiempos={"crear": 0.5, "mostrar": 0.5}, segunda_vez=True),
            # revisión independiente (P1-1): abierta, oculta con la X y su primer ping tarda más que el sondeo (1,5 s)
            dict(nombre="ya abierta, oculta y ocupada", retraso=0.0, tiempos={"crear": 0.5, "mostrar": 0.5},
                 segunda_vez=True, ocultar=True, extra1={"chuleta_falso_ping_lento": 2.5}),
        ])
        for r in res:
            with self.subTest(r["nombre"]):
                self.comprobar(r, show=r["nombre"].startswith("ya abierta"))
                if "oculta" in r["nombre"]:
                    self.assertEqual(r.get("ocultar"), {"ok": True})
                    self.assertEqual(len(r["hide"]), 1)
                    self.assertIn("ya estaba abierta", r["log"])


# ----------------------------------------------------------------------------- la ventana «en camino» (window.py)


class FakeEvt:
    """Como webview.event.Event, en síncrono: += manejadores; set() los llama y marca; is_set()."""

    def __init__(self):
        self.handlers, self._set = [], False

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def set(self):
        self._set = True
        for h in self.handlers:
            h()

    def is_set(self):
        return self._set


class FakeW:
    def __init__(self, closed=None):
        self.events = type("E", (), {})()
        self.events.closing, self.events.shown = FakeEvt(), FakeEvt()
        self.native, self.calls, self.closed = None, [], closed or threading.Event()

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def destroy(self):
        self.calls.append("destroy")
        self.closed.set()

    def evaluate_js(self, js):
        return "no"


class FakeWv:
    """start() espera a que el test diga «ya» (la ventana creada, sin enseñar) y luego la enseña hasta destroy()."""

    def __init__(self):
        self.win, self.go, self.started = None, threading.Event(), threading.Event()

    def create_window(self, title, url, **kw):
        self.win = FakeW()
        return self.win

    def start(self, **kw):
        self.started.set()
        self.go.wait(10)
        self.win.events.shown.set()
        self.win.closed.wait(10)


def esperar(cond, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin and not cond():
        time.sleep(0.02)
    return cond()


class VentanaEnCaminoTests(unittest.TestCase):
    def make(self, wv):
        return window.Window("http://127.0.0.1:8765/", tempfile.mkdtemp(prefix="chuleta-win-"),
                             os.path.join(APP, "static", "chuleta.ico"), webview=wv)

    def test_en_camino_hasta_que_se_ensena_y_entonces_al_frente(self):
        wv = FakeWv()
        w = self.make(wv)
        self.assertTrue(w.pending)
        self.assertEqual(w.request_show(), "pending")  # antes de crearla (en Windows, initialize)
        t = threading.Thread(target=w.run, daemon=True)
        with mock.patch.object(window.Window, "_to_front") as front:
            t.start()
            self.assertTrue(wv.started.wait(5))
            self.assertTrue(w.alive)
            self.assertTrue(w.pending)
            self.assertEqual(w.request_show(), "pending")  # creada y sin enseñar: nada de esperar a «shown»
            self.assertNotIn("show", wv.win.calls)
            wv.go.set()
            self.assertTrue(esperar(lambda: not w.pending))
            front.assert_called_once()  # la pidieron mientras se abría: al enseñarse, delante
            self.assertFalse(w.want_front)
            self.assertEqual(w.request_show(), "shown")
            self.assertIn("show", wv.win.calls)
            w.destroy()
            t.join(5)
        self.assertFalse(w.alive)
        self.assertFalse(w.pending)
        self.assertIsNone(w.request_show())  # cerrada: Edge (lo decide server.mostrar)

    def test_sin_peticiones_no_se_trae_al_frente(self):
        wv = FakeWv()
        w = self.make(wv)
        t = threading.Thread(target=w.run, daemon=True)
        with mock.patch.object(window.Window, "_to_front") as front:
            t.start()
            wv.started.wait(5)
            wv.go.set()
            self.assertTrue(esperar(lambda: not w.pending))
            front.assert_not_called()
            w.destroy()
            t.join(5)

    def test_si_no_se_puede_abrir_deja_de_estar_en_camino(self):
        wv = FakeWv()
        wv.create_window = mock.Mock(side_effect=RuntimeError("sin WebView2"))
        w = self.make(wv)
        self.assertFalse(w.run())
        self.assertFalse(w.pending)
        self.assertIsNone(w.request_show())

    def test_sin_pywebview_nunca_esta_en_camino(self):
        with mock.patch.object(window, "import_webview", return_value=None):
            w = window.Window("http://127.0.0.1:8765/", tempfile.mkdtemp(), "x.ico")
        self.assertFalse(w.pending)
        self.assertIsNone(w.request_show())

    def test_show_justo_despues_de_shown_no_abre_edge(self):
        """pywebview (event.py) lanza el hilo de los manejadores de «shown» ANTES de marcar el evento: si alguien pide
        la ventana justo cuando _on_shown la da por enseñada, el evento aún puede no estar marcado. No es «perdida»."""
        w = self.make(FakeWv())
        w.w, w.alive = FakeW(), True
        w._on_shown()  # el manejador ya ha corrido; el evento de pywebview, todavía no
        self.assertFalse(w.w.events.shown.is_set())
        self.assertEqual(w.request_show(), "shown")
        self.assertEqual(w.w.calls, ["show"])

    def test_en_camino_caduca_y_show_no_espera_a_shown(self):
        """Una ventana que no llega a enseñarse en PENDING_MAX s se da por perdida (entonces Edge), y show() no
        llama a pywebview (su show() esperaría a «shown» hasta 20 s y la otra copia se cansa a los 5)."""
        w = self.make(FakeWv())
        w.w, w.alive = FakeW(), True
        w._pending_since = time.monotonic() - window.PENDING_MAX - 1
        self.assertFalse(w.pending)
        self.assertIsNone(w.request_show())
        self.assertEqual(w.w.calls, [])
        w.w.events.shown.set()
        self.assertEqual(w.request_show(), "shown")
        self.assertEqual(w.w.calls, ["show"])


# ----------------------------------------------------------------------------- server.py: Edge una vez, instancia única


class EdgeUnaVezTests(unittest.TestCase):
    def abrir(self):
        p = [mock.patch.object(server, "_UI_OPENED", {"inicio": 0.0, "ultimo": 0.0}),
             mock.patch.object(server.subprocess, "Popen"), mock.patch.object(server.webbrowser, "open"),
             mock.patch.object(server, "log")]
        m = [x.start() for x in p]
        for x in p:
            self.addCleanup(x.stop)
        self.abiertos = lambda: m[1].call_count + m[2].call_count
        return m[3]

    def test_el_edge_del_arranque_y_el_del_acceso_directo_son_uno(self):
        log = self.abrir()
        self.assertTrue(server.open_ui(inicio=True))
        self.assertFalse(server.open_ui())  # el acceso directo mientras se abre
        self.assertEqual(self.abiertos(), 1)
        self.assertIn("no se abre otro", log.call_args[0][0])

    def test_en_cualquier_orden(self):
        self.abrir()
        self.assertTrue(server.open_ui())  # /api/mostrar se atiende antes que el Timer del arranque
        self.assertFalse(server.open_ui(inicio=True))
        self.assertEqual(self.abiertos(), 1)

    def test_despues_del_arranque_cada_peticion_abre_el_suyo(self):
        """Revisión independiente (P2-2): Edge cerrado al rato y el acceso directo otra vez: tiene que abrirse."""
        self.abrir()
        self.assertTrue(server.open_ui(inicio=True))
        server._UI_OPENED["inicio"] -= server.UI_REOPEN_GAP + 1
        server._UI_OPENED["ultimo"] -= server.UI_REOPEN_GAP + 1
        self.assertTrue(server.open_ui())
        self.assertTrue(server.open_ui())  # aunque se acabe de abrir otro: no es el del arranque
        self.assertEqual(self.abiertos(), 3)


HOLD = ("import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import server\n"
        "print(server.instance_lock(sys.argv[2], int(sys.argv[3])), flush=True)\n"
        "sys.stdin.read()\n")


class InstanciaUnicaTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="chuleta-instancia-")
        p = mock.patch.object(server, "_INSTANCE", [])
        p.start()
        self.addCleanup(p.stop)

    def hold(self, port):
        p = subprocess.Popen([sys.executable, "-c", HOLD, APP, self.data, str(port)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(lambda: p.stdin.closed or p.stdin.close())
        self.addCleanup(lambda: p.poll() is None and (p.kill() or p.wait(15)))
        self.assertEqual(p.stdout.readline().strip(), "True")
        return p

    def close_fds(self):
        for fd in server._INSTANCE:
            os.close(fd)
        server._INSTANCE[:] = []

    def test_una_sola_copia_por_carpeta_de_datos_y_puerto(self):
        otra = self.hold(8765)
        t = time.time()
        self.assertIs(server.instance_lock(self.data, 8765), False)  # al instante, sin esperar a ningún puerto
        self.assertLess(time.time() - t, 1)
        self.assertIs(server.instance_lock(self.data, 8766), True)  # otro puerto: otra copia (--port, pruebas)
        self.assertFalse(os.get_inheritable(server._INSTANCE[0]))  # los procesos hijos no se la quedan
        self.close_fds()
        otra.stdin.close()  # la otra copia termina: el sistema suelta la marca
        otra.wait(15)
        self.assertIs(server.instance_lock(self.data, 8765, wait=5), True)
        self.close_fds()

    def test_la_marca_se_suelta_aunque_maten_el_proceso(self):
        otra = self.hold(8765)
        otra.kill()
        otra.wait(15)
        self.assertIs(server.instance_lock(self.data, 8765, wait=5), True)
        self.close_fds()

    def test_quien_tiene_la_marca(self):
        otra = self.hold(8765)
        pid, hace = server.instance_holder(self.data, 8765)
        self.assertEqual(pid, otra.pid)
        self.assertLess(hace, 60)
        self.assertEqual(server.instance_holder(self.data, 8799), (None, None))  # nadie

    def test_fatal_suelta_la_marca(self):
        """Mientras el aviso de error está abierto, otro lanzamiento no tiene que esperar a esta copia."""
        self.assertIs(server.instance_lock(self.data, 8765), True)
        with mock.patch.object(server, "alert"), self.assertRaises(SystemExit):
            server.fatal("prueba")
        self.assertEqual(server._INSTANCE, [])
        self.hold(8765)  # otra copia la toma

    def test_sin_carpeta_de_datos_sigue_como_antes(self):
        fichero = os.path.join(self.data, "no-es-carpeta")
        with open(fichero, "w") as f:
            f.write("x")
        with mock.patch.object(server, "log"):
            self.assertIsNone(server.instance_lock(fichero, 8765))


class RetirarSoloOtraVersionTests(unittest.TestCase):
    """retire_other_version nunca apaga ni mata una copia de esta misma versión (en una actualización con doble clic,
    el puerto puede pasar a la copia hermana entre la pregunta y la orden)."""

    def retirar(self, ping, estado_puerto="otra_version", duenio=4242):
        llamadas, corridos = [], []

        def local(path, token, data=None, timeout=1.5):
            llamadas.append(path)
            if path == "api/ping":
                return ping
            if path == "api/rec/status":
                return {"recording": False}
            raise OSError("sin respuesta")
        with mock.patch.object(server, "_local_request", local), mock.patch.object(server, "WINDOWS", True), \
                mock.patch.object(server, "_wait_port_free", return_value=False), \
                mock.patch.object(server, "probe_running", return_value=estado_puerto), \
                mock.patch.object(server, "_port_owner_pid", return_value=duenio), \
                mock.patch.object(server, "_is_python_process", return_value=True), \
                mock.patch.object(server.subprocess, "run", lambda argv, **k: corridos.append(argv)), \
                mock.patch.object(server, "log", lambda *a: None):
            estado = server.retire_other_version(token="t", wait=0.2)
        return estado, llamadas, corridos

    def test_misma_version_no_se_toca(self):
        estado, llamadas, corridos = self.retirar({"app": "audios", "version": server.VERSION, "pid": 4242})
        self.assertEqual(estado, "app")
        self.assertEqual(llamadas, ["api/ping"])  # ni rec/status ni apagar
        self.assertEqual(corridos, [])

    def test_no_mata_si_el_puerto_ya_es_de_esta_version(self):
        estado, _, corridos = self.retirar({"app": "audios", "version": "vieja"}, estado_puerto="app")
        self.assertEqual(corridos, [])

    def test_no_mata_otro_pid_que_el_de_la_vieja(self):
        _, _, corridos = self.retirar({"app": "audios", "version": "vieja", "pid": 1111}, duenio=4242)
        self.assertEqual(corridos, [])
        _, _, corridos = self.retirar({"app": "audios", "version": "vieja", "pid": 4242}, duenio=4242)
        self.assertEqual([c[:3] for c in corridos], [["taskkill", "/PID", "4242"]])


class SegundaCopiaTests(unittest.TestCase):
    """main() con la marca de instancia: lo que hace la copia que sobra según lo que vea en el puerto."""

    @staticmethod
    def secuencia(valores):
        """Devuelve los valores en orden y, acabados, repite el último (main() puede volver a preguntar)."""
        it, ultimo = iter(valores), [valores[-1]]

        def f(*a, **k):
            ultimo[0] = next(it, ultimo[0])
            return ultimo[0]
        return f

    def run_main(self, instancia, estados, retirar="libre", mostrar=None, reserve=None, holder=(None, None)):
        argv = ["server.py", "--port", "8797", "--data", tempfile.mkdtemp(prefix="chuleta-datos-")]
        srv, lines, m = mock.MagicMock(), [], {}
        patches = [mock.patch.object(sys, "argv", argv), mock.patch.object(server, "Window"),
                   mock.patch.object(server, "setup_logging"), mock.patch.object(server, "start_session"),
                   mock.patch.object(server, "set_app_id", return_value=False),
                   mock.patch.object(server, "probe_running", side_effect=self.secuencia(estados)),
                   mock.patch.object(server, "INSTANCE_WAIT", 1.2),
                   mock.patch.object(server, "instance_holder", return_value=holder),
                   mock.patch.object(server, "PORT", server.PORT), mock.patch.object(server, "URL", server.URL),
                   mock.patch.object(server, "DATA_DIR", server.DATA_DIR), mock.patch.object(server, "SERVER", None),
                   mock.patch.object(server, "WINDOW", None), mock.patch.object(server.Handler, "app", None),
                   mock.patch.dict(os.environ), mock.patch.object(server, "log", lines.append),
                   mock.patch.object(server.engines, "MODELS", None)]  # main() lo pone: que no pase a otros tests
        nombrados = {"App": mock.patch.object(server, "App"),
                     "fatal": mock.patch.object(server, "fatal", side_effect=SystemExit(1)),
                     "lock": mock.patch.object(server, "instance_lock", side_effect=self.secuencia(instancia)),
                     "alert": mock.patch.object(server, "alert"),
                     "retire": mock.patch.object(server, "retire_other_version", return_value=retirar),
                     "reserve": mock.patch.object(server, "reserve_port", **(reserve or {"return_value": srv})),
                     "ask": mock.patch.object(server, "ask_running_to_show", return_value=mostrar),
                     "open_ui": mock.patch.object(server, "open_ui")}
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            for k, p in nombrados.items():
                m[k] = stack.enter_context(p)
            try:
                server.main()
                m["exited"] = False
            except SystemExit:
                m["exited"] = True
        m["log"] = "\n".join(lines)
        return m

    def test_otra_copia_que_no_contesta_sale_sin_avisar(self):
        for estado in ("arrancando", "libre", "otro"):
            with self.subTest(estado):
                r = self.run_main([False], [estado])
                self.assertFalse(r["exited"])
                for k in ("App", "reserve", "ask", "open_ui", "fatal"):
                    r[k].assert_not_called()
                self.assertIn(f"no contesta ({estado}): si se está abriendo, su ventana saldrá sola", r["log"])

    def test_otra_copia_abierta_le_pide_la_ventana(self):
        r = self.run_main([False], ["app"], mostrar={"ok": True, "pending": True})
        r["ask"].assert_called_once()
        r["open_ui"].assert_not_called()
        r["App"].assert_not_called()
        r["reserve"].assert_not_called()
        self.assertIn("ya estaba abierta", r["log"])
        self.assertIn("aún se está abriendo: su ventana saldrá sola", r["log"])

    def test_si_la_otra_no_contesta_a_mostrar_edge_desde_aqui(self):
        r = self.run_main([False], ["app"], mostrar=None)
        r["open_ui"].assert_called_once()
        r["App"].assert_not_called()

    def test_otra_version_con_marca_se_retira_y_se_arranca(self):
        with mock.patch.object(server, "_holder_is_port_owner", return_value=True):  # la marca es de la vieja
            r = self.run_main([False, True], ["otra_version"], retirar="libre")
        r["retire"].assert_called_once()
        self.assertEqual(r["lock"].call_args_list[1], mock.call(mock.ANY, mock.ANY, wait=5.0))
        r["reserve"].assert_called_once()
        r["App"].assert_called_once()
        r["ask"].assert_not_called()

    def test_otra_version_que_no_se_cierra_acaba_en_el_aviso_de_siempre(self):
        with mock.patch.object(server, "_holder_is_port_owner", return_value=True):
            r = self.run_main([False], ["otra_version"], retirar="otra_version",
                              reserve={"side_effect": SystemExit(1)})
        self.assertTrue(r["exited"])
        r["reserve"].assert_called_once()  # reserve_port da el aviso «otra versión que no se ha podido cerrar»
        r["App"].assert_not_called()
        r["ask"].assert_not_called()

    def test_la_segunda_espera_a_que_la_primera_conteste_y_le_pide_la_ventana(self):
        r = self.run_main([False], ["arrancando", "arrancando", "app"], mostrar={"ok": True, "pending": True})
        r["ask"].assert_called_once()
        r["App"].assert_not_called()
        r["open_ui"].assert_not_called()

    def test_apagar_y_volver_a_abrir_enseguida_arranca(self):
        """La copia que se está cerrando aún tiene la marca (y ya no contesta): en cuanto la suelta, esta arranca."""
        r = self.run_main([False, False, True], ["libre"])
        r["reserve"].assert_called_once()
        r["App"].assert_called_once()
        r["ask"].assert_not_called()

    def test_otra_copia_colgada_se_avisa(self):
        r = self.run_main([False], ["arrancando"], holder=(4321, 600))
        r["alert"].assert_called_once()
        self.assertIn("pid 4321", r["alert"].call_args[0][1])
        self.assertIn("la otra copia: pid 4321, desde hace 600 s", r["log"])
        r["App"].assert_not_called()
        r = self.run_main([False], ["arrancando"], holder=(4321, 20))  # arrancando de verdad: sin aviso
        r["alert"].assert_not_called()

    def test_actualizacion_con_doble_clic_la_retira_la_hermana(self):
        """Revisión independiente (P2-3): la versión vieja no tiene la marca; la tiene la otra copia nueva, que la
        está retirando. Esta no la retira otra vez: espera a que conteste y le pide la ventana."""
        with mock.patch.object(server, "_holder_is_port_owner", return_value=False):
            r = self.run_main([False], ["otra_version", "otra_version", "app"], mostrar={"ok": True, "pending": True})
        r["retire"].assert_not_called()
        r["ask"].assert_called_once()
        r["App"].assert_not_called()

    def test_la_version_vieja_con_la_marca_se_retira_sin_esperar(self):
        with mock.patch.object(server, "_holder_is_port_owner", return_value=True):
            r = self.run_main([False, True], ["otra_version"], retirar="libre")
        r["retire"].assert_called_once()
        r["App"].assert_called_once()

    def test_sin_marca_de_instancia_como_antes(self):
        r = self.run_main([None], ["arrancando"])
        r["reserve"].assert_called_once()
        r["App"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
