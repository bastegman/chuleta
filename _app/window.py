# -*- coding: utf-8 -*-
"""Ventana propia de la app (pywebview sobre WebView2, el motor de Edge que trae Windows 10/11) en vez de abrir
`msedge --app=`: la barra de tareas ve un proceso nuestro, con nuestro icono y nuestro AppUserModelID, y no una
ventana mas de Edge. Si pywebview no esta instalado o no puede crear la ventana, server.py abre Edge como siempre.

  - pywebview exige el hilo principal: server.py mueve el servidor HTTP a un hilo y bloquea aqui en run().
  - Cerrar la ventana la OCULTA: el servidor sigue (Ctrl+Alt+R y las grabaciones en curso siguen funcionando) y el
    acceso directo, si el servidor ya responde, pide POST /api/mostrar para volver a ensenarla.
  - Apagar de verdad: Ajustes > Apagar (POST /api/apagar), que llama a destroy().
"""
import os
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


class Window:
    """La ventana de la app. run() en el hilo principal; show() y destroy() valen desde cualquier hilo."""

    def __init__(self, url, data_dir, ico_path, webview=None):
        self.url, self.data_dir, self.ico_path = url, data_dir, ico_path
        self.webview = webview if webview is not None else import_webview()
        self.w, self.hwnd, self.quitting, self.alive = None, None, False, False

    @property
    def available(self):
        return self.webview is not None

    def run(self):
        """Bloquea hasta que destroy() cierre la ventana (o hasta que no se pueda crear). True si llego a abrirse."""
        wv = self.webview
        try:
            self.w = wv.create_window(APP_NAME, self.url, width=1500, height=920, min_size=(900, 600),
                                      text_select=True, zoomable=True)
            self.w.events.closing += self._on_closing
            self.w.events.shown += self._on_shown
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

    # --- eventos (hilo de la ventana)
    def _on_shown(self):
        try:
            self.hwnd = self._find_hwnd()
            if self.hwnd:
                set_window_icons(self.hwnd, self.ico_path)
            log(f"ventana abierta ({'WebView2' if WINDOWS else 'pywebview'}; hwnd {self.hwnd})")
        except Exception:
            log_exc()

    def _on_closing(self):
        """Cerrar = ocultar: devolver False cancela el cierre. Solo destroy() (apagar) deja cerrar. Antes de ocultar se
        le pregunta a la pagina (onWindowClosing): la primera vez avisa de que la app sigue en segundo plano y es ella
        quien pide POST /api/ocultar al cerrar el aviso."""
        if self.quitting:
            return True
        threading.Thread(target=self._ask_page_then_hide, daemon=True).start()  # fuera del propio evento de cierre
        return False

    def _ask_page_then_hide(self):
        time.sleep(0.05)
        try:
            r = self.w.evaluate_js('typeof onWindowClosing === "function" ? onWindowClosing() : "no"')
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
    def show(self):
        """Vuelve a ensenar la ventana (POST /api/mostrar). False si ya no hay ventana (entonces, el navegador)."""
        if not (self.alive and self.w):
            return False
        try:
            self.w.show()  # Show() + Activate()
            if WINDOWS and self.hwnd:
                import ctypes
                user32 = ctypes.windll.user32
                if user32.IsIconic(self.hwnd):
                    user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(self.hwnd)  # si Windows no lo permite, parpadea en la barra
            return True
        except Exception:
            log_exc()
            return False

    def destroy(self):
        """Cierra de verdad (apagar): run() vuelve en el hilo principal."""
        self.quitting = True
        if self.alive and self.w:
            try:
                self.w.destroy()
            except Exception:
                log_exc()
