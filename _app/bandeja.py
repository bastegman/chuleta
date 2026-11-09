# -*- coding: utf-8 -*-
"""Icono de la app en la bandeja del sistema (la zona de notificacion de Windows, junto al reloj) mientras el proceso
vive, con la ventana abierta u oculta (Bernardo, 02/11/2026, msg 1143, cuando la app se llamaba Chuleta: que quedase
abierta como una app aquí corriendo aunque le ponga salir, y que solo se pueda cerrar desde ahí).

  - Clic en el icono: abre la ventana. Clic derecho: Abrir, Grabar o Parar (Ctrl+Alt+R), Arrancar con Windows y Salir.
    Salir es lo mismo que Ajustes > Apagar (server.tray_salir).
  - Sin dependencias nuevas: System.Windows.Forms.NotifyIcon y ContextMenuStrip por pythonnet, que ya carga pywebview
    (WinForms, netfx). Se crea en el hilo de la ventana (window.py lo llama desde before_show) y solo se toca desde
    ahi: los demas hilos piden con BeginInvoke. Las acciones del menu van a un hilo aparte: el de la ventana no espera.
  - Si algo falla al crearlo, no hay icono y la app sigue como antes (al cerrar la ventana avisa la pagina); al log.
  - Se retira (Dispose) al apagar: si no, Windows deja un icono fantasma hasta que se pasa el raton por encima.
  - Windows pone los iconos nuevos en el panel de ocultos (^): no se puede forzar por API; se arrastra una vez.
"""
import threading

from common import APP_NAME, log, log_exc

TEXT_MAX = 63  # NotifyIcon.Text en .NET Framework: mas de 63 caracteres lanza ArgumentOutOfRangeException
WM_TRAYMOUSEMESSAGE = 0x0400 + 1024  # WM_USER + 1024: el mensaje con que el icono recibe el raton (NotifyIcon.cs)
REFRESH_S = 2.0  # cada cuanto se actualiza el texto del icono (grabando, trabajos)


def winforms():
    """Lo que hace falta de WinForms y System.Drawing (pythonnet), como un espacio de nombres. Solo Windows."""
    import clr  # pythonnet: ya cargado por pywebview con netfx
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    import System
    from System.Drawing import Font, FontStyle, Icon
    from System.Reflection import BindingFlags
    from System.Windows.Forms import (ContextMenuStrip, MouseButtons, NotifyIcon, SystemInformation,
                                      ToolStripMenuItem, ToolStripSeparator, ToolTipIcon)
    ns = type("WinForms", (), {})()
    for k, v in dict(Action=System.Action, Font=Font, FontStyle=FontStyle, Icon=Icon, BindingFlags=BindingFlags,
                     ContextMenuStrip=ContextMenuStrip, MouseButtons=MouseButtons, NotifyIcon=NotifyIcon,
                     SystemInformation=SystemInformation, ToolStripMenuItem=ToolStripMenuItem,
                     ToolStripSeparator=ToolStripSeparator, ToolTipIcon=ToolTipIcon).items():
        setattr(ns, k, v)
    return ns


def tooltip(estado):
    """El texto del icono: «Tenca», «Tenca · grabando 12:34», «Tenca · 2 trabajos en marcha»..."""
    partes = [APP_NAME]
    s = estado.get("grabando")
    if s is not None:
        s = max(0, int(s))
        partes.append(f"grabando {s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600
                      else f"grabando {s // 60}:{s % 60:02d}")
    n = estado.get("trabajos") or 0
    if n:
        partes.append(f"{n} trabajo{'s' if n != 1 else ''} en marcha")
    return " · ".join(partes)[:TEXT_MAX]


class Bandeja:
    """El icono y su menu. create() en el hilo de la ventana; balloon(), update() y close() desde cualquier hilo.

    acciones: {"abrir": fn(), "grabar": fn(), "salir": fn(), "inicio": (leer() -> bool | None, poner(bool)) o None};
    estado(): {"grabando": segundos o None, "trabajos": n}. `wf`: el espacio de nombres de winforms() (los tests ponen
    un doble)."""

    def __init__(self, ico_path, acciones, estado, wf=None):
        self.ico_path, self.acciones, self.estado, self._wf = ico_path, acciones, estado, wf
        self.icon = self.menu = self.form = None
        self.items = {}
        self.hwnd = None
        self.alive = False
        self._text = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # --- en el hilo de la ventana
    def create(self, form):
        """Pone el icono en la bandeja. True si queda puesto; si no, False y al log (la app sigue como antes). Si ya se
        ha pedido quitarlo (close(): la app se apaga mientras arranca), no lo pone: luego nadie lo quitaria."""
        if self._stop.is_set():
            return False
        try:
            wf = self._wf or winforms()
            self._wf = wf
            icon = self.icon = wf.NotifyIcon()  # en self en cuanto existe: si algo falla despues, _dispose lo quita
            try:  # el tamano pequeno del .ico (16 px a 100 %): sin el, WinForms reduce el grande y sale borroso
                icon.Icon = wf.Icon(self.ico_path, wf.SystemInformation.SmallIconSize)
            except Exception:
                icon.Icon = wf.Icon(self.ico_path)
            icon.Text = APP_NAME
            menu = self.menu = wf.ContextMenuStrip()
            abrir = wf.ToolStripMenuItem(f"Abrir {APP_NAME}")
            abrir.Font = wf.Font(abrir.Font, wf.FontStyle.Bold)  # la accion del clic, en negrita como en Windows
            grabar = wf.ToolStripMenuItem("Grabar  (Ctrl+Alt+R)")
            inicio = wf.ToolStripMenuItem("Arrancar con Windows")
            salir = wf.ToolStripMenuItem("Salir")
            self.items = {"abrir": abrir, "grabar": grabar, "inicio": inicio, "salir": salir}
            for k in ("abrir", "grabar", "salir"):
                self.items[k].Click += self._handler(k)
            inicio.Click += self._handler("inicio")
            menu.Items.Add(abrir)
            menu.Items.Add(grabar)
            menu.Items.Add(wf.ToolStripSeparator())
            if self.acciones.get("inicio"):
                menu.Items.Add(inicio)
                menu.Items.Add(wf.ToolStripSeparator())
            menu.Items.Add(salir)
            menu.Opening += self._on_opening
            icon.ContextMenuStrip = menu
            icon.MouseClick += self._on_click
            icon.BalloonTipClicked += self._handler("abrir")  # clic en el globo: la ventana
            icon.Visible = True
            self.form = form
            self.alive = True
            self.hwnd = self._native_hwnd()
        except Exception as e:
            log(f"   sin icono en la bandeja ({e.__class__.__name__}): cerrar la ventana la deja en segundo plano "
                "como antes")
            log_exc()
            self._dispose()
            return False
        log(f"icono en la bandeja (hwnd {self.hwnd})")
        threading.Thread(target=self._refresh_loop, daemon=True, name="bandeja").start()
        return True

    def _native_hwnd(self):
        """La ventana oculta con la que NotifyIcon recibe el raton (campo privado «window» de .NET Framework). Solo
        para el log y las pruebas; None si no se encuentra."""
        try:
            wf = self._wf
            try:
                flags = wf.BindingFlags.NonPublic | wf.BindingFlags.Instance
            except TypeError:  # por si pythonnet no combina enums de .NET con |
                import clr
                from System import Enum
                flags = Enum.ToObject(clr.GetClrType(wf.BindingFlags), 36)  # NonPublic (32) | Instance (4)
            f = self.icon.GetType().GetField("window", flags)
            return int(f.GetValue(self.icon).Handle.ToInt64()) or None
        except Exception:
            return None

    def _handler(self, nombre):
        return lambda sender, args: self._run(nombre)

    def _on_click(self, sender, args):
        try:
            if args.Button == self._wf.MouseButtons.Left:  # el derecho abre el menu (lo hace WinForms)
                self._run("abrir")
        except Exception:
            log_exc()

    def _on_opening(self, sender, args):
        """Justo antes de ensenar el menu: el texto de Grabar o Parar y la marca de Arrancar con Windows."""
        try:
            e = self.estado() or {}
            self.items["grabar"].Text = ("Parar la grabación  (Ctrl+Alt+R)" if e.get("grabando") is not None
                                         else "Grabar  (Ctrl+Alt+R)")
            inicio = self.acciones.get("inicio")
            if inicio:
                v = inicio[0]()
                self.items["inicio"].Checked = bool(v)
                self.items["inicio"].Enabled = v is not None  # None: no se puede (sin acceso directo instalado)
        except Exception:
            log_exc()

    def _run(self, nombre):
        threading.Thread(target=self._act, args=(nombre,), daemon=True, name=f"bandeja-{nombre}").start()

    def _act(self, nombre):
        try:
            if nombre == "inicio":
                leer, poner = self.acciones["inicio"]
                poner(not leer())
            else:
                self.acciones[nombre]()
        except Exception:
            log_exc()

    # --- desde cualquier hilo
    def _invoke(self, fn):
        """fn() en el hilo de la ventana sin esperarla (BeginInvoke). False si ya no hay icono. fn no puede lanzar: en
        el hilo de la ventana, una excepcion sin capturar saca el dialogo de WinForms «excepcion no controlada»."""
        def seguro():
            try:
                if self.icon is not None:  # se pudo quitar entre la peticion y su turno (close() con el hilo ocupado)
                    fn()
            except Exception:
                log_exc()
        with self._lock:
            if not (self.alive and self.form is not None):
                return False
            try:
                self.form.BeginInvoke(self._wf.Action(seguro))
                return True
            except Exception:
                log_exc()
                return False

    def update(self):
        """El texto del icono segun el estado (grabando, trabajos): solo si ha cambiado."""
        try:
            text = tooltip(self.estado() or {})
        except Exception:
            log_exc()
            return
        if text != self._text and self._invoke(lambda: setattr(self.icon, "Text", text)):
            self._text = text

    def _refresh_loop(self):
        while not self._stop.wait(REFRESH_S):
            self.update()

    def balloon(self, titulo, texto, segundos=8):
        """El globo del icono (en Windows 11 sale como una notificacion de la app)."""
        def show():
            self.icon.ShowBalloonTip(int(segundos * 1000), titulo, texto, self._wf.ToolTipIcon.Info)
        return self._invoke(show)

    def close(self, wait=5.0):
        """Quita el icono (Dispose en el hilo de la ventana y lo espera hasta `wait` s; desde el propio hilo, al
        momento). Sin esto Windows deja un icono fantasma hasta que se pasa el raton por encima. Antes de create(),
        hace que create() ya no lo ponga."""
        self._stop.set()
        with self._lock:
            if not self.alive:
                return
            self.alive = False
            form = self.form
        hecho = threading.Event()

        def dispose():
            self._dispose()
            hecho.set()
        try:
            if form is not None and getattr(form, "InvokeRequired", False):
                form.BeginInvoke(self._wf.Action(dispose))
                if not hecho.wait(wait):
                    log("   icono de la bandeja: el hilo de la ventana no contesta; se quita desde aquí")
                    self._dispose()
            else:
                dispose()
        except Exception:
            log_exc()
            self._dispose()
        log("icono de la bandeja retirado")

    def _dispose(self):
        icon, menu = self.icon, self.menu
        self.icon = self.menu = None
        for obj, visible in ((icon, True), (menu, False)):
            if obj is None:
                continue
            try:
                if visible:
                    obj.Visible = False
                obj.Dispose()
            except Exception:
                pass
