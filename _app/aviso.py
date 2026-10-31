# -*- coding: utf-8 -*-
"""Aviso flotante «● Grabar / ✕» que la app abre, en un proceso aparte (como el selector de carpetas), al detectar
una llamada. Ventana sin borde, siempre visible, abajo en el centro del monitor principal (respetando la barra de
tareas), que NO roba el foco (WS_EX_NOACTIVATE: puedes seguir escribiendo en la llamada). Se cierra sola a los
20 s, al pulsar ✕, al empezar a grabar (también con Ctrl+Alt+R) o si la app se cierra.

    python aviso.py --puerto 8765 [--titulo "Reunión semanal"] [--nombre "Reunión semanal"] [--vocab ID]
                    [--tema "Alfa · General"] [--segundos 20]
                    [--estado fichero.json] [--auto-grabar-ms N]        (estos dos, para las pruebas)

«Grabar» hace POST http://127.0.0.1:<puerto>/api/rec/toggle (cabecera X-Audios: 1, la que exige el servidor) con el
nombre y el tema sugeridos; si ya se estaba grabando no toca nada. Al salir escribe una línea JSON en stdout:
{"resultado": "grabar" | "cerrar" | "tiempo" | "grabando" | "sin-servidor" | "error", ...}.
"""
import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.request

WINDOWS = sys.platform == "win32"
ANCHO, ALTO = 340, 64  # px a 96 ppp; se escala con el monitor
# la paleta de app.css (tema oscuro): neutros cálidos, texto claro y el acento burdeos de la app en «Grabar»
C = {"fondo": "#1c1b1f", "texto": "#ebe8e5", "suave": "#a09a95", "boton": "#be123c", "boton_hover": "#9f1239",
     "boton_texto": "#ffffff", "rojo": "#fb7185", "ok": "#4ade80", "err": "#f87171"}


def limpiar_texto(s, limite=200):
    """Texto apto para una etiqueta de Tk: sin caracteres fuera del BMP (emoji de los títulos de Teams, que Tk
    puede rechazar con TclError), sin saltos de línea ni controles, y acotado."""
    s = "".join(ch for ch in str(s or "") if ord(ch) <= 0xFFFF and (ch.isprintable() or ch == " "))
    return " ".join(s.split())[:limite]


def dpi_aware():
    """Antes de crear la ventana: nítida en pantallas con escala (per-monitor v1; si no, system aware)."""
    if not WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def area_trabajo(root):
    """(izquierda, arriba, derecha, abajo) del área de trabajo del monitor principal (sin barra de tareas)."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):  # SPI_GETWORKAREA
            return r.left, r.top, r.right, r.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def ventana_activa():
    if not WINDOWS:
        return 0
    import ctypes
    ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def estilo_windows(root, x, y):
    """Solo Windows: la ventana no se activa (ni al mostrarse ni al pulsarla), no sale en la barra de tareas,
    queda por encima, con sombra y esquinas redondeadas en Windows 11. Devuelve {hwnd, exstyle}."""
    if not WINDOWS:
        return {}
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetParent.restype, u.GetParent.argtypes = wintypes.HWND, [wintypes.HWND]
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT]
    hwnd = u.GetParent(root.winfo_id())  # con overrideredirect, el marco WS_POPUP que envuelve a Tk
    if not hwnd:
        return {}
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = -20, 0x80, 0x8, 0x08000000
    ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(ex & 0xFFFFFFFF).value)
    try:  # sombra (como los menús) y esquinas redondeadas (DWMWA_WINDOW_CORNER_PREFERENCE, solo Windows 11)
        u.GetClassLongPtrW.restype, u.GetClassLongPtrW.argtypes = ctypes.c_size_t, [wintypes.HWND, ctypes.c_int]
        u.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_size_t]
        u.SetClassLongPtrW(hwnd, -26, u.GetClassLongPtrW(hwnd, -26) | 0x20000)  # GCL_STYLE | CS_DROPSHADOW
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass
    # SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED, encima de todo (HWND_TOPMOST = -1)
    u.SetWindowPos(hwnd, wintypes.HWND(-1), x, y, 0, 0, 0x1 | 0x10 | 0x40 | 0x20)
    return {"hwnd": int(hwnd), "exstyle": u.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF}


def devolver_foco(anterior, propio):
    """Tk activa la ventana al mostrarla aunque lleve WS_EX_NOACTIVATE (ShowWindow(SW_SHOWNORMAL) no respeta ese
    estilo, que solo evita la activación por clic). Si nos hemos quedado con el foco, se devuelve a la ventana que lo
    tenía (la llamada en la que estabas): en ese instante somos el proceso activo y Windows lo permite."""
    if not WINDOWS or not anterior or not propio:
        return None
    import ctypes
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = ctypes.c_void_p
    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    u.IsWindow.argtypes = [ctypes.c_void_p]
    if int(u.GetForegroundWindow() or 0) != int(propio):
        return True  # no la hemos robado
    if not u.IsWindow(ctypes.c_void_p(anterior)):
        return False
    u.SetForegroundWindow(ctypes.c_void_p(anterior))
    return int(u.GetForegroundWindow() or 0) == int(anterior)


class Aviso:
    def __init__(self, args, activa_antes=0):
        import tkinter as tk
        import tkinter.font as tkfont
        self.args, self.tk = args, tk
        self.resultado, self.enviando, self.fallos, self.info = None, False, 0, {}
        self.activa_antes = activa_antes  # la ventana activa ANTES de crear la nuestra (para medir que no la robamos)
        self.url = f"http://127.0.0.1:{args.puerto}"
        root = self.root = tk.Tk()
        root.withdraw()
        root.title("Aviso de llamada")
        try:  # el icono de la app (no se ve sin barra de título, pero sí en herramientas que listan ventanas)
            root.iconbitmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "notas.ico"))
        except Exception:
            pass
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=C["fondo"])
        s = self.s = max(1.0, root.winfo_fpixels("1i") / 96.0)
        fams = set(tkfont.families())
        fam = "Segoe UI" if "Segoe UI" in fams else tkfont.nametofont("TkDefaultFont").actual("family")
        semi = "Segoe UI Semibold" if "Segoe UI Semibold" in fams else fam
        f_tit = tkfont.Font(family=semi, size=10, weight="normal" if semi != fam else "bold")
        f_sub, f_x = tkfont.Font(family=fam, size=8), tkfont.Font(family=fam, size=11)
        self.f_sub = f_sub  # para recortar los mensajes de estado (cget('font') solo da el nombre)
        f_btn, f_dot = tkfont.Font(family=semi, size=10), tkfont.Font(family=fam, size=12)
        px = int(14 * s)
        cont = tk.Frame(root, bg=C["fondo"], padx=px, pady=int(9 * s))
        cont.pack(fill="both", expand=True)
        tk.Label(cont, text="●", fg=C["rojo"], bg=C["fondo"], font=f_dot).pack(side="left", padx=(0, int(9 * s)))
        # botones a la derecha (antes que los textos, para que el título se recorte y no los empuje)
        self.cerrar_btn = tk.Label(cont, text="✕", fg=C["suave"], bg=C["fondo"], font=f_x, padx=int(6 * s),
                                   cursor="hand2")
        self.cerrar_btn.pack(side="right")
        self.grabar_btn = tk.Frame(cont, bg=C["boton"], padx=int(12 * s), pady=int(5 * s), cursor="hand2")
        self.grabar_btn.pack(side="right", padx=(int(8 * s), int(4 * s)))
        partes = [tk.Label(self.grabar_btn, text="●", fg=C["boton_texto"], bg=C["boton"], font=f_sub, cursor="hand2"),
                  tk.Label(self.grabar_btn, text="Grabar", fg=C["boton_texto"], bg=C["boton"], font=f_btn,
                           cursor="hand2")]
        partes[0].pack(side="left", padx=(0, int(5 * s)))
        partes[1].pack(side="left")
        textos = tk.Frame(cont, bg=C["fondo"])
        textos.pack(side="left", fill="both", expand=True)
        self.l_tit = tk.Label(textos, fg=C["texto"], bg=C["fondo"], font=f_tit, anchor="w")
        self.l_tit.pack(fill="x")
        self.l_sub = tk.Label(textos, fg=C["suave"], bg=C["fondo"], font=f_sub, anchor="w")
        self.l_sub.pack(fill="x")
        ancho_txt = int((ANCHO - 14 * 2 - 12 - 9 - 92 - 8 - 4 - 28) * s)  # lo que queda para el texto
        titulo, tema = limpiar_texto(args.titulo), limpiar_texto(args.tema)
        self.l_tit.configure(text=self._encajar(f_tit, titulo or "Llamada en curso", ancho_txt))
        self.sub(self._encajar(f_sub, ("Tema: " + tema) if tema else "Pulsa Grabar para tomar notas", ancho_txt))
        for w in [self.grabar_btn] + partes:
            w.bind("<Button-1>", lambda e: self.grabar())
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))
        self.cerrar_btn.bind("<Button-1>", lambda e: self.cerrar("cerrar"))
        self.cerrar_btn.bind("<Enter>", lambda e: self.cerrar_btn.configure(fg=C["texto"]))
        self.cerrar_btn.bind("<Leave>", lambda e: self.cerrar_btn.configure(fg=C["suave"]))
        self.w, self.h = int(ANCHO * s), int(ALTO * s)
        l, t, r, b = area_trabajo(root)
        self.x, self.y = l + (r - l - self.w) // 2, b - self.h - int(24 * s)
        self.area = [l, t, r, b]
        root.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")

    def _encajar(self, font, text, max_px):
        text = " ".join(str(text or "").split())
        if font.measure(text) <= max_px:
            return text
        while len(text) > 1 and font.measure(text + "…") > max_px:
            text = text[:-1].rstrip()
        return text + "…"

    def _hover(self, on):
        bg = C["boton_hover"] if on else C["boton"]
        self.grabar_btn.configure(bg=bg)
        for w in self.grabar_btn.winfo_children():
            w.configure(bg=bg)

    def sub(self, text, color=None):
        self.l_sub.configure(text=text, fg=color or C["suave"])

    # --- acciones
    def _pedir(self, path, body=None, timeout=15):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json", "X-Audios": "1"} if data else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # Host: 127.0.0.1:<puerto>, como exige el servidor
            return json.load(r)

    def grabar(self):
        if self.enviando or self.resultado:
            return
        self.enviando = True
        self.sub("Empezando a grabar…")
        threading.Thread(target=self._post, daemon=True).start()

    def _post(self):
        try:
            if self._pedir("/api/rec/status").get("recording"):  # alguien se adelantó (Ctrl+Alt+R): no se toca
                return self.root.after(0, lambda: self.cerrar("grabando"))
            r = self._pedir("/api/rec/toggle", {"source": "aviso", "start_only": True, "name": self.args.nombre or "",
                                                "vocab": self.args.vocab or ""})
            self.root.after(0, lambda: self._ok(r))
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error") or f"HTTP {e.code}"
            except Exception:
                msg = f"HTTP {e.code}"
            self.root.after(0, lambda m=msg: self._error(m))
        except Exception as e:
            msg = f"No se pudo hablar con la app ({e.__class__.__name__})"
            self.root.after(0, lambda m=msg: self._error(m))

    def _ok(self, r):
        self.resultado = "grabar"
        self.datos = {"path": r.get("path"), "workstream": r.get("workstream")}
        self.sub("Grabando · Ctrl+Alt+R para parar", C["ok"])
        self.root.after(1200, lambda: self.cerrar("grabar"))

    def _error(self, msg):
        self.resultado, self.datos = "error", {"error": msg}
        self.sub(self._encajar(self.f_sub, limpiar_texto(msg), int(220 * self.s)), C["err"])
        self.root.after(5000, lambda: self.cerrar("error"))

    def sondear(self):
        """Cada 2 s: si ya se graba (Ctrl+Alt+R) o la app se ha cerrado, el aviso sobra."""
        if self.resultado or self.enviando:
            return

        def go():
            try:
                rec = self._pedir("/api/rec/status", timeout=2).get("recording")
                self.fallos = 0
                if rec:
                    self.root.after(0, lambda: self.cerrar("grabando"))
            except Exception:
                self.fallos += 1
                if self.fallos >= 2:
                    self.root.after(0, lambda: self.cerrar("sin-servidor"))
        threading.Thread(target=go, daemon=True).start()
        self.root.after(2000, self.sondear)

    def cerrar(self, resultado):
        if self.resultado is None or resultado == self.resultado:
            self.resultado = resultado
        try:
            self.root.destroy()
        except Exception:
            pass

    def escribir_estado(self):
        """Para las pruebas: dónde está la ventana y sus botones (coordenadas de pantalla)."""
        r = self.root

        def centro(w):
            return [w.winfo_rootx() + w.winfo_width() // 2, w.winfo_rooty() + w.winfo_height() // 2]
        est = {"geometry": [r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height()],
               "pedido": [self.x, self.y, self.w, self.h], "area_trabajo": self.area, "escala": self.s,
               "botones": {"grabar": centro(self.grabar_btn), "cerrar": centro(self.cerrar_btn)},
               "activa_antes": self.activa_antes, "activa_despues": ventana_activa(), **self.info}
        with open(self.args.estado, "w", encoding="utf-8") as f:
            json.dump(est, f)

    def run(self):
        a, root = self.args, self.root
        self.datos = {}
        root.update_idletasks()
        self.info = estilo_windows(root, self.x, self.y)  # antes de mostrarla: así no se activa al aparecer
        root.deiconify()
        root.update_idletasks()
        self.info = estilo_windows(root, self.x, self.y) or self.info  # por si Tk rehízo el marco al mostrarla
        self.info["foco_devuelto"] = devolver_foco(self.activa_antes, self.info.get("hwnd"))
        root.after(max(1, int(a.segundos * 1000)), lambda: self.cerrar("tiempo"))
        root.after(2000, self.sondear)
        if a.auto_grabar_ms:
            root.after(a.auto_grabar_ms, self.grabar)
        if a.estado:
            root.after(400, self.escribir_estado)
        root.mainloop()
        return dict({"resultado": self.resultado or "tiempo"}, **self.datos)


def main(argv=None):
    p = argparse.ArgumentParser(description="Aviso flotante de la app al detectar una llamada")
    p.add_argument("--puerto", type=int, default=8765)
    p.add_argument("--titulo", default="")
    p.add_argument("--nombre", default="")
    p.add_argument("--vocab", default="")
    p.add_argument("--tema", default="")
    p.add_argument("--segundos", type=float, default=20)
    p.add_argument("--estado", default="")
    p.add_argument("--auto-grabar-ms", dest="auto_grabar_ms", type=int, default=0)
    args = p.parse_args(argv)
    dpi_aware()
    activa = ventana_activa()  # antes de que exista nuestra ventana
    try:
        aviso = Aviso(args, activa)
    except Exception as e:  # sin tkinter o sin escritorio: se dice y se sale sin ruido
        print(json.dumps({"resultado": "error", "error": f"{e.__class__.__name__}: {e}"}), flush=True)
        return 2
    print(json.dumps(aviso.run(), ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
