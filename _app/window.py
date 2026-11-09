# -*- coding: utf-8 -*-
"""Ventana propia de la app (pywebview sobre WebView2, el motor de Edge que trae Windows 10/11) en vez de abrir
`msedge --app=`: la barra de tareas ve un proceso nuestro, con nuestro icono y nuestro AppUserModelID, y no una
ventana mas de Edge. Si pywebview no esta instalado o no puede crear la ventana, server.py abre Edge como siempre.

  - pywebview exige el hilo principal: server.py mueve el servidor HTTP a un hilo y bloquea aqui en run().
  - Cerrar la ventana la OCULTA: el servidor sigue (Ctrl+Alt+R y las grabaciones en curso siguen funcionando) y el
    acceso directo, si el servidor ya responde, pide POST /api/mostrar para volver a ensenarla.
  - Apagar de verdad: Ajustes > Apagar (POST /api/apagar), que llama a destroy().
  - Si quien cierra es Windows (apagar, reiniciar o cerrar la sesion) se deja cerrar y la app se apaga limpia
    (server.shutdown): ocultar ahi es lo que hacia que Windows dijera que la app impide apagar.
  - Sin el runtime de WebView2, pywebview usaria MSHTML (el motor de Internet Explorer): run() no abre la ventana y
    server.py abre Edge.
  - Con icono en la bandeja (bandeja.py; solo con la ventana propia): cerrar la oculta sin preguntar a la pagina y, la
    primera vez, un globo en el icono dice que sigue ahi; Salir del menu del icono (o Ajustes > Apagar) apaga. Si el
    icono no se puede poner, todo sigue como antes (el aviso de la pagina).
  - Desde que se crea Window hasta que la ventana se ensena (evento shown) esta «en camino» (pending): en un PC lento
    son decenas de segundos y el usuario vuelve a pulsar el acceso directo. Si en ese rato otra copia pide
    /api/mostrar, no se abre Edge (eran las «2 veces la app» del 02/11/2026): la ventana sale sola y se trae al frente.
  - Ocultarse al compartir pantalla (msg 1242, como Minutes): SetWindowDisplayAffinity con WDA_EXCLUDEFROMCAPTURE en la
    ventana de nivel superior, que tapa tambien lo que pinta WebView2 dentro (comprobado con capturas en windows-real).
    Teams, Zoom y las capturas de pantalla ven lo que hay detras; el usuario la sigue viendo. Ajuste
    «ocultar_al_compartir» (por defecto, si), que se aplica al momento. Necesita Windows 10 2004 (build 19041) o
    posterior: en uno anterior Windows la pintaria en negro (WDA_MONITOR), asi que no se aplica y se dice.
"""
import json
import os
import sys
import threading
import time

from common import APP_ID, APP_NAME, WINDOWS, log, log_exc

_ICONS = []  # HICON cargados con LoadImage: vivos mientras dure el proceso (Windows no los copia)


def set_app_id():
    """AppUserModelID del proceso, ANTES de crear ninguna ventana: asi la barra de tareas agrupa y ancla la app como
    algo propio (la misma cadena va en el acceso directo). Devuelve True si Windows lo acepto."""
    if not WINDOWS:
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_ID)) == 0
    except Exception:
        return False


def import_webview():
    """El modulo pywebview, o None si no esta. En Windows, pythonnet debe cargar .NET Framework (netfx), que es el
    que trae WinForms: si otro programa dejo PYTHONNET_RUNTIME=coreclr la ventana no se podria crear."""
    try:
        if WINDOWS:
            try:
                import pythonnet
                pythonnet.load("netfx")
            except Exception:
                pass
        import webview
        return webview
    except Exception:
        return None


def set_window_icons(hwnd, ico_path):
    """Icono grande (256, Windows lo reduce con calidad para la barra de tareas y Alt+Tab) y pequeno (16, barra de
    titulo) desde el .ico, por WM_SETICON y en la clase de la ventana. Solo Windows."""
    if not WINDOWS or not hwnd or not os.path.isfile(ico_path):
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_uint]
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
        set_class_long = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW  # Python de 32 bits
        set_class_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        ok = False
        for which, size, gclp in ((1, 256, -14), (0, 16, -34)):  # ICON_BIG/GCLP_HICON, ICON_SMALL/GCLP_HICONSM
            h = user32.LoadImageW(None, ico_path, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                _ICONS.append(h)
                user32.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON
                set_class_long(hwnd, gclp, h)
                ok = True
        return ok
    except Exception:
        log_exc()
        return False


WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
CAPTURE_MIN_BUILD = 19041  # Windows 10 version 2004: antes, WDA_EXCLUDEFROMCAPTURE se comporta como WDA_MONITOR


def windows_build():
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def capture_support(build=None, windows=None):
    """(soportado, motivo): si esta ventana se puede ocultar al compartir pantalla en este Windows."""
    if not (WINDOWS if windows is None else windows):
        return False, "Solo en Windows."
    build = windows_build() if build is None else build
    if build < CAPTURE_MIN_BUILD:
        return False, (f"Este Windows no lo admite (hace falta Windows 10 versión 2004 o posterior; este es el build "
                       f"{build}).")
    return True, None


def _user32():
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.SetWindowDisplayAffinity.restype = wintypes.BOOL
    u.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowDisplayAffinity.restype = wintypes.BOOL
    return u


def set_display_affinity(hwnd, on):
    """Pone (on) o quita la marca de «no capturar» en una ventana de nivel superior de este proceso. Devuelve (ok,
    codigo de error de Windows)."""
    import ctypes
    ok = bool(_user32().SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE))
    return ok, (0 if ok else ctypes.get_last_error())


def get_display_affinity(hwnd):
    """La marca que tiene la ventana (0 ninguna, 0x11 no capturar) o None si Windows no contesta."""
    import ctypes
    from ctypes import wintypes
    v = wintypes.DWORD()
    return int(v.value) if _user32().GetWindowDisplayAffinity(hwnd, ctypes.byref(v)) else None


def renderer(wv):
    """El motor con que pywebview pintara la ventana: «edgechromium» (WebView2) o, si falta el runtime de WebView2,
    «mshtml» (el de Internet Explorer: la interfaz sale en blanco). None si no se sabe (entonces decide start(), que
    fallaria igual). Prepara la parte grafica igual que start(), que luego la reutiliza."""
    init = getattr(wv, "initialize", None)
    if not callable(init):
        return None
    try:
        return getattr(init(), "renderer", None)
    except Exception:
        log_exc()
        return None


# CloseReason de WinForms con el que se deja cerrar de verdad: Windows apaga, reinicia o cierra la sesion
# (WM_QUERYENDSESSION y WM_ENDSESSION; tambien un instalador con Restart Manager) o un WM_CLOSE de fuera
# (Administrador de tareas, taskkill sin /F). El resto (la X, Alt+F4, la barra de tareas) es «cerrar = ocultar».
_REASONS = {0: "None", 1: "WindowsShutDown", 3: "UserClosing", 4: "TaskManagerClosing", 6: "ApplicationExitCall"}
QUIT_REASONS = ("WindowsShutDown", "TaskManagerClosing")
LET_CLOSE = QUIT_REASONS + ("ApplicationExitCall",)  # Application.Exit: la app ya se esta cerrando
END_SESSION_WAIT = 5.0  # s que el hilo de la ventana espera al apagado limpio mientras Windows espera la respuesta
_END_SESSION = []  # [fn]: el apagado limpio de server.py, shutdown(motivo) (Window.set_end_session)
# s que la ventana puede estar «en camino» sin ensenarse: pasado ese tiempo se da por perdida y /api/mostrar abre Edge
# (en el PC de Bernardo tardo 31 s el 02/11/2026; un arranque normal, 2-10 s)
PENDING_MAX = 120.0


def close_reason(v):
    """Nombre del CloseReason tal como llegue de pythonnet (enum, «CloseReason.X» o su numero)."""
    s = str(v if v is not None else "")
    return _REASONS.get(int(s), s) if s.isdigit() else s.rsplit(".", 1)[-1]


class Window:
    """La ventana de la app. run() en el hilo principal; show() y destroy() valen desde cualquier hilo."""

    def __init__(self, url, data_dir, ico_path, webview=None, tray=None, hide_from_capture=None):
        self.url, self.data_dir, self.ico_path = url, data_dir, ico_path
        # ocultarse al compartir pantalla: un bool o una funcion que lo lee del ajuste (server.py); None, no se toca
        self.hide_from_capture = hide_from_capture
        self.capture_applied, self.capture_error = None, None  # lo que hay puesto ahora (None: aun nada)
        self.tray = tray  # bandeja.Bandeja (aun sin crear) o None; se crea en el hilo de la ventana (_hook_native)
        self.tray_ok = False
        self._notice_lock = threading.Lock()  # dos X seguidas: un solo globo
        self.webview = webview if webview is not None else import_webview()
        self.w, self.hwnd, self.quitting, self.alive = None, None, False, False
        self.native = False  # True con los manejadores propios de FormClosing/FormClosed enganchados (WinForms)
        self.ended = False   # ya se ha lanzado el apagado por fin de sesion
        self.on_end_session = None  # si no, el registrado con set_end_session (los tests ponen aqui un doble)
        self._lock = threading.Lock()
        # «en camino» desde aqui hasta shown (o hasta que run() vuelva): ver pending
        self._pending_since = time.monotonic() if self.webview is not None else None
        self.want_front = False  # otra copia la ha pedido mientras se abria: al ensenarse, al frente
        self.shown_once = False  # _on_shown ha corrido (pywebview lanza su hilo ANTES de marcar events.shown)

    @property
    def pending(self):
        """La ventana propia se esta abriendo (aun no se ha ensenado) y no hace PENDING_MAX s que empezo."""
        t = self._pending_since
        return t is not None and time.monotonic() - t < PENDING_MAX

    @staticmethod
    def set_end_session(fn):
        """server.py registra aqui su apagado limpio, fn(motivo): lo llama la ventana cuando Windows apaga, reinicia o
        cierra la sesion, o pide cerrar la app."""
        _END_SESSION[:] = [fn]

    @property
    def available(self):
        return self.webview is not None

    def run(self):
        """Bloquea hasta que destroy() cierre la ventana (o hasta que no se pueda crear). True si llego a abrirse.
        False sin abrirla si pywebview no fuera a usar WebView2: server.py abre entonces Edge."""
        wv = self.webview
        try:
            motor = renderer(wv) if WINDOWS else None  # None: no se sabe (start() decide, como siempre)
            if motor is not None and motor != "edgechromium":
                log(f"la ventana propia necesita WebView2 y pywebview usaría «{motor}» (¿falta el runtime de "
                    "WebView2?): se abre en Edge")
                return False
            self.w = wv.create_window(APP_NAME, self.url, width=1500, height=920, min_size=(900, 600),
                                      text_select=True, zoomable=True)
            self.w.events.closing += self._on_closing
            self.w.events.shown += self._on_shown
            before_show = getattr(self.w.events, "before_show", None)  # en el hilo de la ventana, antes de ensenarla
            if before_show is not None:
                before_show += self._hook_native
            self.alive = True
            # cookies y localStorage de la interfaz (modo de la transcripcion, carpetas abiertas...) persisten en
            # DATA_DIR (fuera de OneDrive); private_mode los borraria en cada arranque
            kw = {"private_mode": False, "storage_path": os.path.join(self.data_dir, "webview")}
            try:
                wv.start(icon=self.ico_path, **kw)  # icon: el backend WinForms lo pone en Form.Icon
            except TypeError:  # pywebview antiguo sin `icon`
                wv.start(**kw)
            return True
        except Exception:
            log_exc()
            return False
        finally:
            self.alive = False
            with self._lock:
                self._pending_since = None  # se ha cerrado o no se ha podido abrir: ya no esta en camino
            self._close_tray()  # sin el bucle de mensajes de la ventana el icono ya no contestaria: fuera

    # --- eventos (hilo de la ventana)
    def _on_shown(self):
        try:
            self.hwnd = self._find_hwnd()
            if self.hwnd:
                set_window_icons(self.hwnd, self.ico_path)
            if not self.native:  # pywebview sin before_show
                self._hook_native()
            if self.capture_applied is None:  # sin before_show (o sin la Form de WinForms): ahora, con el hwnd
                self.set_capture(self._capture_pref(), quiet_off=True)
            log(f"ventana abierta ({'WebView2' if WINDOWS else 'pywebview'}; hwnd {self.hwnd})")
        except Exception:
            log_exc()
        finally:
            with self._lock:
                self.shown_once, self._pending_since = True, None
                front, self.want_front = self.want_front, False
            if front:  # el acceso directo se pulso otra vez mientras se abria
                log("   se ha vuelto a abrir mientras arrancaba: la ventana se trae al frente")
                try:
                    self._to_front()
                except Exception:
                    log_exc()

    def _hook_native(self):
        """Manejadores propios de FormClosing y FormClosed en la Form de WinForms, despues de los de pywebview: son los
        que saben POR QUE se cierra (CloseReason), que pywebview no pasa a events.closing. Asi Windows puede apagar,
        reiniciar o cerrar la sesion sin que la app lo impida. Ademas la app pide que Windows le avise antes que a
        los demas procesos (SetProcessShutdownParameters), para parar la grabadora antes de que la cierren a ella."""
        form = getattr(self.w, "native", None)
        if self.native or form is None or not hasattr(form, "FormClosing"):
            return False
        try:
            form.FormClosing += self._on_form_closing
            form.FormClosed += self._on_form_closed
            self.native = True
        except Exception:
            log_exc()
            return False
        self._create_tray(form)
        # antes de ensenarla: que ni el primer fotograma salga en una reunion en la que ya se esta compartiendo
        self.set_capture(self._capture_pref(), quiet_off=True)
        if WINDOWS:
            try:
                import ctypes
                ctypes.windll.kernel32.SetProcessShutdownParameters(0x3FF, 0)  # primero de las apps al apagar
            except Exception:
                log_exc()
        return True

    # --- ocultarse al compartir pantalla
    def _capture_pref(self):
        f = self.hide_from_capture
        try:
            return bool(f() if callable(f) else f)
        except Exception:
            log_exc()
            return False

    def capture_state(self):
        """{soportado, motivo, aplicado, error} para Ajustes."""
        ok, motivo = capture_support()
        return {"soportado": ok, "motivo": motivo, "aplicado": self.capture_applied, "error": self.capture_error}

    def set_capture(self, on, quiet_off=False):
        """Pone o quita la marca de «no capturar» en la ventana, desde cualquier hilo: en WinForms se hace en el hilo de
        la ventana (Invoke). quiet_off: al abrir, con el ajuste apagado, no hay nada que quitar. Devuelve True si queda
        como se pide."""
        form = getattr(self.w, "native", None) if self.w is not None else None
        try:
            if form is not None and getattr(form, "InvokeRequired", False):
                import System
                out = []
                form.Invoke(System.Action(lambda: out.append(self._set_capture_here(form, on, quiet_off))))
                return bool(out and out[0])
        except Exception:  # la ventana se esta cerrando: nada que marcar
            log_exc()
            return False
        return self._set_capture_here(form, on, quiet_off)

    def _set_capture_here(self, form, on, quiet_off):
        try:
            if self.quitting:
                return False
            if not on and quiet_off and not self.capture_applied:
                self.capture_applied = False
                return True
            ok_sup, motivo = capture_support()
            if not ok_sup:
                if on and self.capture_error != motivo:
                    log(f"ocultar {APP_NAME} al compartir pantalla: no se puede ({motivo})")
                self.capture_applied, self.capture_error = False, (motivo if on else None)
                return not on
            hwnd = None
            if form is not None:
                try:
                    hwnd = int(form.Handle.ToInt64())
                except Exception:
                    hwnd = None
            hwnd = hwnd or self.hwnd
            if not hwnd:
                return False
            ok, err = set_display_affinity(hwnd, on)
            if ok:
                self.capture_applied, self.capture_error = bool(on), None
                # (sin «ventana oculta»: es el texto de cerrar a segundo plano, que miran las pruebas de apagado)
                log(f"al compartir pantalla, la ventana {'no sale' if on else 'vuelve a salir'} (hwnd {hwnd})")
            else:
                self.capture_applied = False
                self.capture_error = f"Windows no lo ha aceptado (error {err})."
                log(f"ocultar {APP_NAME} al compartir pantalla: SetWindowDisplayAffinity falló (error {err})")
            return ok
        except Exception:
            log_exc()
            return False

    def _create_tray(self, form):
        """El icono de la bandeja, en el hilo de la ventana (su bucle de mensajes es el que lo atiende). Desde
        before_show ya se esta en el; desde shown (pywebview sin before_show), por Invoke."""
        if self.tray is None or self.tray_ok or self.quitting:  # apagando (p. ej. Apagar durante el arranque): no
            return
        try:
            if getattr(form, "InvokeRequired", False):
                import System
                form.Invoke(System.Action(lambda: self._create_tray(form)))
                return
            self.tray_ok = bool(self.tray.create(form))
        except Exception:
            log_exc()
            self.tray_ok = False

    @property
    def has_tray(self):
        return bool(self.tray_ok and self.tray is not None and self.tray.alive)

    def _on_closing(self):
        """Cerrar = ocultar: devolver False cancela el cierre. Solo destroy() (apagar) deja cerrar. Antes de ocultar se
        le pregunta a la pagina (onWindowClosing): la primera vez avisa de que la app sigue en segundo plano y es ella
        quien pide POST /api/ocultar al cerrar el aviso. Con los manejadores propios (WinForms) es _on_form_closing
        quien decide si se oculta o, si es Windows quien cierra, se deja cerrar."""
        if self.quitting:
            return True
        if not self.native:
            self._start_hide()
        return False

    def _on_form_closing(self, sender, args):
        """FormClosing, despues del de pywebview (que lo acaba de cancelar: cerrar = ocultar). Si quien cierra es
        Windows (WM_QUERYENDSESSION) o un WM_CLOSE de fuera, se deja cerrar: cancelar aqui es lo que hace que Windows
        diga «esta aplicación impide apagar». Sin ocultar ni avisar de que sigue en segundo plano."""
        try:
            motivo = close_reason(getattr(args, "CloseReason", None))
            if self.quitting or motivo in LET_CLOSE:
                args.Cancel = False
                if not self.quitting and motivo in QUIT_REASONS:
                    log(f"Windows pide cerrar la ventana ({motivo}): se deja cerrar")
            elif args.Cancel:
                self._start_hide()
        except Exception:
            log_exc()

    def _on_form_closed(self, sender, args):
        """FormClosed (con WM_ENDSESSION, cuando la sesion se cierra de verdad, o tras un WM_CLOSE de fuera): el mismo
        apagado limpio que Ajustes > Apagar, mientras Windows espera la respuesta. Si se cancela el apagado
        (WM_ENDSESSION con FALSE), no llega aqui y la app sigue como estaba."""
        try:
            motivo = close_reason(getattr(args, "CloseReason", None))
            if motivo in QUIT_REASONS and not self.quitting:
                self._end_session(motivo)
        except Exception:
            log_exc()

    def _end_session(self, motivo):
        """Apaga la app (on_end_session = server.shutdown) y espera como mucho END_SESSION_WAIT s: si no termina en
        ese tiempo, run() vuelve igual (quitting) y el proceso acaba."""
        if self.ended:
            return
        self.ended = self.quitting = True
        self._close_tray()  # aqui, en el hilo de la ventana: el apagado (otro hilo) esperaria a este, que espera
        texto = "Windows cierra la sesión o se apaga" if motivo == "WindowsShutDown" else "Windows ha pedido cerrar"
        log(f"{texto}: apagado limpio")
        fn = self.on_end_session or (_END_SESSION[0] if _END_SESSION else None)
        if not callable(fn):
            return
        t = threading.Thread(target=fn, args=(texto,), daemon=True, name="fin-de-sesion")
        t.start()
        t.join(END_SESSION_WAIT)

    def _start_hide(self):
        threading.Thread(target=self._ask_page_then_hide, daemon=True).start()  # fuera del propio evento de cierre

    def _ask_page_then_hide(self):
        time.sleep(0.05)
        if self.quitting:
            return
        if self.has_tray:  # con icono no hace falta el aviso de la pagina: se oculta y, la primera vez, un globo
            if self._hide():
                self._tray_notice()
            return
        js = 'typeof onWindowClosing === "function" ? onWindowClosing() : "no"'
        try:
            r = self.w.evaluate_js(js)
        except Exception:
            try:  # evaluate_js envuelve el script en un eval() que la CSP de la pagina puede bloquear; run_js no
                r = self.w.run_js(js)
            except Exception:
                r = None
        if r != "handled":
            self._hide()

    def hide(self):
        """Oculta la ventana (POST /api/ocultar, desde la pagina). False si no hay ventana."""
        if not (self.alive and self.w):
            return False
        return self._hide()

    def _hide(self):
        try:
            self.w.hide()
            log("ventana oculta: la app sigue en segundo plano (el acceso directo la vuelve a abrir)")
            return True
        except Exception:
            log_exc()
            return False

    def _tray_notice(self):
        """La primera vez que se cierra con la X (marca en DATA_DIR\bandeja.json): el globo del icono."""
        path = os.path.join(self.data_dir, "bandeja.json")
        with self._notice_lock:
            try:
                if os.path.isfile(path):
                    return False
                if not self.tray.balloon(f"{APP_NAME} sigue aquí",
                                         "Cerrar la ventana no la apaga: sigue en la bandeja, junto al reloj (si no "
                                         "ves el icono, en ^). Para salir: clic derecho en el icono > Salir."):
                    return False  # sin globo no se marca: la proxima vez se vuelve a intentar
                os.makedirs(self.data_dir, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"aviso_cerrar": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
                log("   primer cierre con la X: aviso en el icono de la bandeja")
                return True
            except OSError:
                log_exc()
                return False

    def _close_tray(self):
        if self.tray is not None:
            try:
                self.tray.close()
            except Exception:
                log_exc()

    def _find_hwnd(self):
        try:
            native = getattr(self.w, "native", None)  # backend WinForms: la Form
            if native is not None:
                return int(native.Handle.ToInt64())
        except Exception:
            pass
        if not WINDOWS:
            return None
        import ctypes
        import ctypes.wintypes as wt
        user32, found, pid = ctypes.windll.user32, [], os.getpid()
        enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def cb(hwnd, _):
            owner = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if buf.value == APP_NAME:
                    found.append(hwnd)
                    return False
            return True
        user32.EnumWindows(enum_proc(cb), 0)
        return found[0] if found else None

    # --- desde otros hilos
    def request_show(self):
        """POST /api/mostrar (el acceso directo, desde otra copia): "shown" si se ha ensenado; "pending" si aun se esta
        abriendo (sale sola y, al salir, se trae al frente: no hay que abrir nada mas); None si no hay ventana propia
        (entonces, Edge)."""
        with self._lock:
            if self.pending:
                self.want_front = True
                return "pending"
        return "shown" if self.show() else None

    def show(self):
        """Vuelve a ensenar la ventana. False si ya no hay ventana (entonces, el navegador) o si nunca llego a
        ensenarse: pywebview esperaria a «shown» hasta 20 s (_shown_call) y quien la pide se cansa a los 5."""
        if not (self.alive and self.w):
            return False
        shown = getattr(getattr(self.w, "events", None), "shown", None)
        if not self.shown_once and callable(getattr(shown, "is_set", None)) and not shown.is_set():
            log(f"la ventana propia no se ha enseñado en {PENDING_MAX:.0f} s: se abre en Edge")
            return False
        try:
            self.w.show()  # Show() + Activate()
            self._to_front()
            return True
        except Exception:
            log_exc()
            return False

    def _to_front(self):
        """Restaura si esta minimizada y la pone delante. Windows solo lo deja si este proceso tiene permiso: el acceso
        directo se lo cede (server.allow_foreground); si no, parpadea en la barra de tareas."""
        if WINDOWS and self.hwnd:
            import ctypes
            user32 = ctypes.windll.user32
            if user32.IsIconic(self.hwnd):
                user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(self.hwnd)

    def destroy(self):
        """Cierra de verdad (apagar): run() vuelve en el hilo principal. Antes, el icono de la bandeja."""
        self.quitting = True
        self._close_tray()
        if self.alive and self.w:
            try:
                self.w.destroy()
            except Exception:
                log_exc()
