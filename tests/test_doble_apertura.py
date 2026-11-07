# -*- coding: utf-8 -*-
"""Doble apertura al arrancar (backlog del 7-8/11/2026, ítem 1; Bernardo, msg 1143: «se me abrían como 2 veces la
app»). Dos lanzamientos seguidos (doble clic, o el acceso directo otra vez porque la ventana tarda: en su PC, 31 s
entre «arranque» y «ventana abierta» el 02/11) no pueden dejar dos ventanas.

Aquí, en cualquier sistema: dos server.py de verdad con un pywebview falso que reproduce lo que importa del 6.2.1
(`show()` espera al evento `shown` hasta 20 s; `shown` llama a sus manejadores en otro hilo) y con tiempos simulados
para lo que en Windows tarda (importar pythonnet y pywebview, `initialize()` y crear la ventana de WebView2). open_ui()
no abre Edge: lo anota. En windows-real, lo mismo con la ventana de verdad (test_auditoria_windows.VentanaRealTests).
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from support import REPO

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

    def escenario(self, nombre, retraso, tiempos, extra=None, segunda_vez=False):
        """Lanza la primera copia, espera `retraso` s y lanza la segunda. Devuelve lo que pasó."""
        a = Arranques(self.tmp, self.app_dir, self.fake_dir, **tiempos)
        r = {"nombre": nombre, "a": a}
        try:
            p1 = a.lanzar(**(extra or {}))
            if segunda_vez:  # la ventana ya está abierta: el acceso directo la vuelve a enseñar
                a.esperar(lambda: a.eventos_de("ventana"), 60)
                time.sleep(1.0)
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
        ])
        for r in res:
            with self.subTest(r["nombre"]):
                self.comprobar(r, show=r["nombre"] == "ya abierta")


if __name__ == "__main__":
    unittest.main()
