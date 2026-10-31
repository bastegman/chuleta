# -*- coding: utf-8 -*-
"""Sonda (solo Windows, para un runner de GitHub Actions): ¿se puede abrir la interfaz de Notas en una ventana PROPIA
con pywebview (WebView2) en vez de `msedge --app`, y qué AUMID e iconos lleva ese HWND?

    python scripts/lookfeel_webview_probe.py SALIDA

Arranca `_app/server.py --no-browser` en el puerto 8767, fija SetCurrentProcessExplicitAppUserModelID("Bernardo.Notas"),
abre `webview.create_window("Notas", URL + "?t=<token>")` (con el token de la sesión, como la app), localiza su HWND
(mismo pid, título «Notas»), mide el AUMID y los iconos WM_GETICON/GCLP_HICON antes y después de ponerle
`static/notas.ico` con WM_SETICON (ctypes), guarda los iconos como PNG, cierra la ventana y escribe SALIDA/webview.json
y SALIDA/webview.md. Cualquier excepción queda anotada con su texto exacto: si la ventana no se puede crear, eso es el
resultado.
"""
import ctypes
import json
import os
import platform
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import lookfeel_spike as spike  # noqa: E402  (las funciones Win32 de medición)

PORT = 8767
URL = f"http://127.0.0.1:{PORT}/"
APP_ID = "Bernardo.Notas"
ICO = os.path.join(REPO, "_app", "static", "notas.ico")
user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32


def set_icons(hwnd):
    """WM_SETICON con el .ico: ICON_BIG (256 → Windows lo reduce) e ICON_SMALL (16); y el icono de la clase."""
    out = {}
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_uint]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    user32.SetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    big = user32.LoadImageW(None, ICO, 1, 256, 256, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
    small = user32.LoadImageW(None, ICO, 1, 16, 16, 0x10)
    out["loaded"] = {"big": bool(big), "small": bool(small), "err": ctypes.GetLastError()}
    if big:
        user32.SendMessageW(hwnd, 0x80, 1, big)  # WM_SETICON ICON_BIG
        user32.SetClassLongPtrW(hwnd, -14, big)  # GCLP_HICON
    if small:
        user32.SendMessageW(hwnd, 0x80, 0, small)  # ICON_SMALL
        user32.SetClassLongPtrW(hwnd, -34, small)  # GCLP_HICONSM
    return out


def find_own_window(title, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        for hwnd, t, cls, pid in spike.windows():
            if pid == os.getpid() and t.strip() == title:
                return hwnd, cls
        time.sleep(0.3)
    return None, None


def main(argv):
    out_dir = os.path.abspath(argv[1] if len(argv) > 1 else "spike-out")
    os.makedirs(out_dir, exist_ok=True)
    rec = {"python": sys.version.split()[0], "arch": platform.machine(), "steps": []}

    def step(name, **kw):
        rec["steps"].append({"step": name, **kw})
        print(f"-- {name}: {json.dumps(kw, ensure_ascii=False, default=str)}", flush=True)

    rec["env"] = {k: os.environ.get(k) for k in ("PYTHONNET_RUNTIME", "DOTNET_ROOT", "NOTAS_PROBE_NETFX")}
    if os.environ.get("NOTAS_PROBE_NETFX") == "1":  # hipotesis: pythonnet carga coreclr en vez de .NET Framework
        try:
            import pythonnet
            pythonnet.load("netfx")
            step("pythonnet.load('netfx')", ok=True)
        except Exception as e:
            step("pythonnet.load('netfx')", ok=False, error=repr(e))
    try:
        import clr  # noqa: F401
        from System import Environment
        from System.Runtime.InteropServices import RuntimeInformation
        step("runtime .NET", version=str(Environment.Version), framework=str(RuntimeInformation.FrameworkDescription))
    except Exception as e:
        step("runtime .NET", ok=False, error=repr(e))
    try:
        import pythonnet
        step("pythonnet.get_runtime_info", info=str(pythonnet.get_runtime_info()))
    except Exception as e:
        step("pythonnet.get_runtime_info", ok=False, error=repr(e))
    try:
        import webview
        from importlib.metadata import version as _v
        rec["pywebview"] = _v("pywebview")
        step("import webview", ok=True, version=rec["pywebview"])
    except Exception as e:
        step("import webview", ok=False, error=repr(e), trace=traceback.format_exc())
        webview = None
    for mod in ("clr", "clr_loader", "pythonnet", "proxy_tools", "bottle"):
        try:
            m = __import__(mod)
            step("import " + mod, ok=True, version=getattr(m, "__version__", "?"))
        except Exception as e:
            step("import " + mod, ok=False, error=repr(e))
    hr = shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_ID))
    step("SetCurrentProcessExplicitAppUserModelID", hr=hr, app_id=APP_ID)

    data = os.path.join(out_dir, "webview-data")
    log = open(os.path.join(out_dir, "webview-server.log"), "wb")
    srv = subprocess.Popen([sys.executable, os.path.join(REPO, "_app", "server.py"), "--no-browser",
                            "--port", str(PORT), "--data", data], stdout=log, stderr=subprocess.STDOUT)
    try:
        spike.URL = URL  # wait_ping y ui_url del spike miran esta URL
        if not spike.wait_ping(data, 40):
            step("servidor", ok=False, error="sin ping en 40 s")
            return finish(rec, out_dir)
        step("servidor", ok=True, url=URL)
        if webview is None:
            return finish(rec, out_dir)
        result = {}

        def on_start(window):
            try:
                hwnd, cls = find_own_window("Notas")
                result["hwnd"], result["class"] = hwnd, cls
                if not hwnd:
                    result["error"] = "no se encontró la ventana propia titulada «Notas» en 30 s"
                    result["own_windows"] = [(h, t, c) for h, t, c, p in spike.windows() if p == os.getpid()]
                    return
                time.sleep(4)  # que WebView2 cargue la página
                result["aumid"] = spike.aumid(hwnd)
                result["appusermodel"] = spike.appusermodel_props(hwnd)
                result["icons_default"] = spike.window_icons(hwnd, out_dir, "webview-default")
                native = getattr(window, "native", None)
                result["native_type"] = type(native).__name__ if native is not None else None
                result["set_icons"] = set_icons(hwnd)
                time.sleep(1)
                result["icons_after_seticon"] = spike.window_icons(hwnd, out_dir, "webview-seticon")
                result["aumid_after"] = spike.aumid(hwnd)
                try:  # la via "oficial" del backend WinForms: window.native.Icon (pythonnet, clr ya cargado)
                    from System.Drawing import Icon
                    native.Icon = Icon(ICO)
                    time.sleep(1)
                    result["icons_after_native_icon"] = spike.window_icons(hwnd, out_dir, "webview-nativeicon")
                    result["native_icon"] = "ok"
                except Exception as e:
                    result["native_icon"] = repr(e)
                try:
                    result["title_after"] = window.title
                except Exception as e:
                    result["title_after"] = repr(e)
                # ocultar y volver a mostrar (lo que haria "cerrar = ocultar" + /api/mostrar)
                try:
                    window.hide()
                    time.sleep(1)
                    result["hidden_visible"] = bool(user32.IsWindowVisible(hwnd))
                    window.show()
                    time.sleep(1)
                    result["shown_visible"] = bool(user32.IsWindowVisible(hwnd))
                except Exception as e:
                    result["hide_show"] = repr(e)
            except Exception as e:
                result["error"] = repr(e)
                result["trace"] = traceback.format_exc()
            finally:
                try:
                    window.destroy()
                except Exception as e:
                    result["destroy"] = repr(e)

        def watchdog():  # si algo se queda colgado, que el job no espere 6 h
            time.sleep(120)
            os._exit(3)
        threading.Thread(target=watchdog, daemon=True).start()
        t0 = time.time()
        try:
            window = webview.create_window("Notas", spike.ui_url(data), width=1200, height=800)
            webview.start(on_start, window)  # en Windows elige WebView2 (edgechromium) si esta; si no, mshtml
            step("webview.start", ok=True, seconds=round(time.time() - t0, 1),
                 gui=str(getattr(webview, "guilib", None)), renderer=str(getattr(webview, "renderer", None)))
        except Exception as e:
            step("webview.start", ok=False, error=repr(e), trace=traceback.format_exc(),
                 seconds=round(time.time() - t0, 1))
        rec["window"] = result
        return finish(rec, out_dir)
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()


def finish(rec, out_dir):
    with open(os.path.join(out_dir, "webview.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1, default=str)
    lines = [f"# pywebview · Python {rec['python']} {rec.get('arch')} · pywebview {rec.get('pywebview')}", ""]
    for s in rec["steps"]:
        lines.append("- " + json.dumps(s, ensure_ascii=False, default=str)[:600])
    w = rec.get("window") or {}
    lines += ["", "## ventana", f"- hwnd {w.get('hwnd')} clase {w.get('class')} native {w.get('native_type')}",
              f"- AUMID: {w.get('aumid')!r} (tras WM_SETICON {w.get('aumid_after')!r})",
              f"- hide/show: oculta visible={w.get('hidden_visible')} · mostrada visible={w.get('shown_visible')} "
              f"{w.get('hide_show', '')}", f"- native.Icon: {w.get('native_icon')}"]
    for when in ("icons_default", "icons_after_seticon", "icons_after_native_icon"):
        for name, info in (w.get(when) or {}).items():
            lines.append(f"- {when} {name}: {info}")
    if w.get("error"):
        lines.append(f"- ERROR: {w['error']}")
        lines.append("```\n" + (w.get("trace") or "") + "\n```")
    with open(os.path.join(out_dir, "webview.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
