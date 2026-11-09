# -*- coding: utf-8 -*-
"""Ocultar Tenca al compartir pantalla, en Windows de verdad (workflow windows-real, NOTAS_VENTANA_REAL=1): la ventana
propia de la app (window.Window, pywebview sobre WebView2) con una página entera de color magenta.

1. Al enseñarse, con el ajuste encendido, GetWindowDisplayAffinity dice WDA_EXCLUDEFROMCAPTURE (0x11).
2. Una captura de la pantalla (BitBlt del escritorio, lo mismo que usan las capturas clásicas) donde está la ventana
   NO tiene el magenta: WebView2 pinta en una ventana hija de otro proceso y la marca va en la de nivel superior; esto
   es lo que la research dejó «falta probarlo con WebView2».
3. Apagado desde otro hilo (como Ajustes, por Invoke en el hilo de la ventana): marca 0 y la captura SÍ tiene el
   magenta (la prueba sirve: si no se viera aquí, la captura no demostraría nada y el test falla).
4. Encendido otra vez: 0x11 y sin magenta.

Las tres capturas (.bmp) y los números quedan en NOTAS_ARTEFACTOS (ocultar_captura_*.bmp, ocultar_captura.json)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from support import APP

WIN = sys.platform == "win32"
ART = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(tempfile.gettempdir(), "notas-artefactos")

PROBE = r'''
import ctypes, http.server, json, os, sys, tempfile, threading, time
from ctypes import wintypes
import numpy as np

app_dir, out, art = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, app_dir)
import window  # la de la app

PAGE = (b"<!doctype html><meta charset='utf-8'><title>t</title><style>html,body{margin:0;height:100%;"
        b"background:#ff00ff}</style><body></body>")


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
u, g = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
u.GetDC.argtypes, u.GetDC.restype = [wintypes.HWND], wintypes.HDC
u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           wintypes.UINT]
u.SetForegroundWindow.argtypes = [wintypes.HWND]
g.CreateCompatibleDC.argtypes, g.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
g.SelectObject.argtypes, g.SelectObject.restype = [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ
g.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int,
                     ctypes.c_int, wintypes.DWORD]
g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                        wintypes.UINT]
g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
g.DeleteDC.argtypes = [wintypes.HDC]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def zona(hwnd):
    """El area de cliente en coordenadas de pantalla, recortada a la pantalla y con 10 px de margen."""
    r, p = wintypes.RECT(), wintypes.POINT(0, 0)
    u.GetClientRect(hwnd, ctypes.byref(r))
    u.ClientToScreen(hwnd, ctypes.byref(p))
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    left, top = max(0, p.x) + 10, max(0, p.y) + 10
    right, bottom = min(sw, p.x + r.right) - 10, min(sh, p.y + r.bottom) - 10
    return left, top, right, bottom


def captura(rect, nombre):
    l, t, r, b = rect
    w, h = r - l, b - t
    sdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    old = g.SelectObject(mdc, bmp)
    ok = g.BitBlt(mdc, 0, 0, w, h, sdc, l, t, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT
    bi = BIH(40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    lines = g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.SelectObject(mdc, old)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(None, sdc)
    px = np.frombuffer(buf.raw, np.uint8).reshape(h, w, 4)
    magenta = (px[..., 2] > 200) & (px[..., 1] < 60) & (px[..., 0] > 200)  # BGRA
    with open(os.path.join(art, f"ocultar_captura_{nombre}.bmp"), "wb") as f:
        size = 54 + w * h * 4
        f.write(b"BM" + size.to_bytes(4, "little") + b"\0\0\0\0" + (54).to_bytes(4, "little"))
        f.write(bytes(bi))
        f.write(buf.raw)
    return {"bitblt": bool(ok), "lineas": int(lines), "magenta": round(float(magenta.mean()), 4), "rect": rect}


w = window.Window(f"http://127.0.0.1:{srv.server_address[1]}/", tempfile.mkdtemp(prefix="tenca-captura-datos-"),
                  os.path.join(app_dir, "static", "tenca.ico"), hide_from_capture=True)
res = {"build": window.windows_build(), "soporte": window.capture_support()}


def driver():
    try:
        fin = time.time() + 120
        while time.time() < fin and not (w.shown_once and w.hwnd):
            time.sleep(0.2)
        res["ensenada"] = bool(w.shown_once and w.hwnd)
        loaded = getattr(getattr(w.w, "events", None), "loaded", None)
        while time.time() < fin and loaded is not None and not loaded.is_set():
            time.sleep(0.2)
        hwnd = w.hwnd
        # arriba del todo y dentro de la pantalla, para que la captura vea lo que haya en ese sitio
        u.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 800, 600, 0x0040)  # HWND_TOPMOST, SWP_SHOWWINDOW
        u.SetForegroundWindow(hwnd)
        time.sleep(3)
        rect = zona(hwnd)
        res["estado_inicial"] = w.capture_state()
        res["marca_al_abrir"] = window.get_display_affinity(hwnd)
        res["con_marca"] = captura(rect, "1_con_marca")
        res["quitar"] = w.set_capture(False)  # desde este hilo: por Invoke, como Ajustes
        time.sleep(1.5)
        res["marca_quitada"] = window.get_display_affinity(hwnd)
        res["sin_marca"] = captura(rect, "2_sin_marca")
        res["poner"] = w.set_capture(True)
        time.sleep(1.5)
        res["marca_puesta"] = window.get_display_affinity(hwnd)
        res["otra_vez_con_marca"] = captura(rect, "3_otra_vez_con_marca")
        res["estado_final"] = w.capture_state()
    except Exception as e:
        import traceback
        res["error"] = traceback.format_exc()
    finally:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        w.destroy()


threading.Thread(target=driver, daemon=True).start()
res["run"] = w.run()
'''


@unittest.skipUnless(WIN and os.environ.get("NOTAS_VENTANA_REAL"), "solo Windows con NOTAS_VENTANA_REAL=1")
class OcultarAlCompartirWindowsTests(unittest.TestCase):
    def test_la_ventana_real_no_sale_en_una_captura(self):
        try:
            import webview  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as e:
            self.skipTest(f"sin {e.name}")
        os.makedirs(ART, exist_ok=True)
        out = os.path.join(tempfile.mkdtemp(prefix="tenca-captura-"), "resultado.json")
        p = subprocess.run([sys.executable, "-c", PROBE, APP, out, ART], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        self.assertTrue(os.path.isfile(out), f"sin resultado (rc {p.returncode}):\n{p.stdout[-2000:]}\n"
                                             f"{p.stderr[-3000:]}")
        with open(out, encoding="utf-8") as f:
            res = json.load(f)
        with open(os.path.join(ART, "ocultar_captura.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(f"\n   ocultar al compartir (build {res.get('build')}): marca {res.get('marca_al_abrir')} → "
              f"{res.get('marca_quitada')} → {res.get('marca_puesta')}; magenta en la captura "
              f"{(res.get('con_marca') or {}).get('magenta')} / {(res.get('sin_marca') or {}).get('magenta')} / "
              f"{(res.get('otra_vez_con_marca') or {}).get('magenta')}", file=sys.stderr)
        self.assertNotIn("error", res, res.get("error"))
        self.assertTrue(res.get("ensenada"), res)
        self.assertEqual(res["soporte"], [True, None], res)  # el runner es Windows Server 2025 (build 26100)
        self.assertEqual(res["marca_al_abrir"], 0x11, res)
        self.assertEqual(res["estado_inicial"]["aplicado"], True, res)
        # sin la marca la página se ve en la captura: si no, la captura no demostraría nada
        self.assertGreater(res["sin_marca"]["magenta"], 0.5, f"la captura no ve la ventana ni sin la marca: {res}")
        self.assertEqual(res["marca_quitada"], 0, res)
        # con la marca, ni rastro del contenido de WebView2
        self.assertLess(res["con_marca"]["magenta"], 0.02, res)
        self.assertEqual(res["marca_puesta"], 0x11, res)
        self.assertLess(res["otra_vez_con_marca"]["magenta"], 0.02, res)
        self.assertTrue(res["quitar"] and res["poner"], res)
        self.assertTrue(res.get("run"), res)


if __name__ == "__main__":
    unittest.main()
