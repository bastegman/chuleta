# -*- coding: utf-8 -*-
"""«Copiar para correo» en WebView2, el motor de la ventana de Chuleta en Windows (en Chromium ya lo prueba
test_ui_smoke): con el mismo código de app.js y notas.js, un clic de verdad en el botón tiene que dejar en el
portapapeles de Windows los dos formatos, «HTML Format» (lo que pega Outlook con formato) y CF_UNICODETEXT (el texto
limpio de reserva).

La página la sirve un servidor local en 127.0.0.1 (como la app: el portapapeles asíncrono solo existe en un contexto
seguro), en una ventana de pywebview. El clic va con el ratón de Windows (SetCursorPos + mouse_event) en el centro de la
ventana, que es todo botón; si el runner no acepta clics, se prueba con un clic por script y se dice cuál valió. Lo
leído del portapapeles queda en los artefactos (portapapeles_webview2.json).

Solo en Windows con pywebview y NOTAS_VENTANA_REAL=1 (workflow windows-real)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from test_copiar_correo import RESUMEN
from test_frontend import js_defs, read

WIN = sys.platform == "win32"
ART = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(tempfile.gettempdir(), "notas-artefactos")
TITLE = "Probar portapapeles WebView2"
INFO = {"title": "Comité de seguimiento", "when": "viernes 6 nov, 10:00", "people": "Ana, Luis"}

# lo que corre en el proceso de la ventana (pywebview necesita su hilo principal): sirve la pagina, la abre, pulsa el
# boton y escribe en argv[2] lo leido del portapapeles
PROBE = r'''
import ctypes, http.server, json, sys, threading, time
from ctypes import wintypes
import webview

page, out = open(sys.argv[1], encoding="utf-8").read().encode("utf-8"), sys.argv[2]
done = {}

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        done.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
u.OpenClipboard.argtypes = [wintypes.HWND]
u.GetClipboardData.argtypes = [wintypes.UINT]
u.GetClipboardData.restype = wintypes.HANDLE
u.EnumClipboardFormats.argtypes = [wintypes.UINT]
u.EnumClipboardFormats.restype = wintypes.UINT
u.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
u.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
u.RegisterClipboardFormatW.restype = wintypes.UINT
u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u.FindWindowW.restype = wintypes.HWND
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.SetForegroundWindow.argtypes = [wintypes.HWND]
k.GlobalLock.argtypes = [wintypes.HGLOBAL]
k.GlobalLock.restype = wintypes.LPVOID
k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
k.GlobalSize.argtypes = [wintypes.HGLOBAL]
k.GlobalSize.restype = ctypes.c_size_t

def abrir():
    for _ in range(50):
        if u.OpenClipboard(None):
            return True
        time.sleep(0.1)
    return False

def vaciar():
    if abrir():
        u.EmptyClipboard()
        u.CloseClipboard()

def leer():
    if not abrir():
        return {"error": "OpenClipboard"}
    try:
        fmts, f = [], 0
        while True:
            f = u.EnumClipboardFormats(f)
            if not f:
                break
            buf = ctypes.create_unicode_buffer(256)
            fmts.append(buf.value if u.GetClipboardFormatNameW(f, buf, 256) else str(f))

        def data(fmt):
            h = u.GetClipboardData(fmt)
            if not h:
                return None
            p = k.GlobalLock(h)
            try:
                return ctypes.string_at(p, k.GlobalSize(h))
            finally:
                k.GlobalUnlock(h)
        html, text = data(u.RegisterClipboardFormatW("HTML Format")), data(13)  # 13 = CF_UNICODETEXT
        return {"formats": fmts, "html": html.decode("utf-8", "replace").split("\x00")[0] if html else None,
                "text": text.decode("utf-16-le", "replace").split("\x00")[0] if text else None}
    finally:
        u.CloseClipboard()

def esperar(s):
    fin = time.time() + s
    while time.time() < fin and not done:
        time.sleep(0.2)

def driver(w):
    res = {}
    try:
        fin = time.time() + 60  # loaded.is_set() (no wait(): que no dependa de la version de pywebview)
        while time.time() < fin and not w.events.loaded.is_set():
            time.sleep(0.2)
        res["cargada"] = w.events.loaded.is_set()
        time.sleep(2)
        res["pagina"] = w.evaluate_js("JSON.stringify({seguro: window.isSecureContext, item: !!window.ClipboardItem,"
                                      " write: !!(navigator.clipboard && navigator.clipboard.write),"
                                      " ua: navigator.userAgent})")
        hwnd = u.FindWindowW(None, sys.argv[3])
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        res["ventana"] = [r.left, r.top, r.right, r.bottom]
        for via in ("clic", "script"):
            vaciar()
            done.clear()
            if via == "clic":
                u.SetForegroundWindow(hwnd)
                time.sleep(0.5)
                u.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                u.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
                u.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
            else:
                w.evaluate_js("document.getElementById('b').click()")
            esperar(15)
            res[via] = dict(done, **leer())
            if done.get("m") == "ok":
                break
    except Exception as e:
        res["error"] = repr(e)
    finally:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        w.destroy()

w = webview.create_window(sys.argv[3], f"http://127.0.0.1:{srv.server_address[1]}/", width=640, height=420)
webview.start(driver, w)
'''


def pagina():
    """La página de prueba: las mismas funciones de app.js y notas.js, un botón que ocupa toda la ventana y un toast
    que avisa al servidor de prueba con el mensaje («ok», «ok (sin formato)» o «No se pudo copiar»)."""
    app, notas = read("app.js"), read("notas.js")
    start = app.index("async function copyRich(")
    rich = app[start:app.index("\n}\n", start) + 3]
    defs = "\n".join([js_defs(app, ["esc", "nfc", "pad", "MES", "DIA", "aDate"]), rich,
                      js_defs(notas, ["ACTA_GENTE", "ACTA_FONT", "actaInline", "actaItem", "actaCorreo", "actaInfo"])])
    return ("<!doctype html><meta charset=\"utf-8\"><title>probe</title>"
            "<style>html,body{margin:0;height:100%}button{width:100%;height:100%;font-size:32px}</style>"
            "<button id=\"b\">Copiar para correo</button><script>\n" + defs + "\n"
            "const toast = (m, k) => fetch('/', {method: 'POST', body: JSON.stringify({m, k: k || ''})});\n"
            f"const SUM = {json.dumps(RESUMEN)}, INFO = {json.dumps(INFO, ensure_ascii=False)};\n"
            "document.getElementById('b').onclick = () => { const r = actaCorreo(SUM, INFO); "
            "copyRich(r.html, r.text, 'ok'); };\n</script>")


@unittest.skipUnless(WIN and os.environ.get("NOTAS_VENTANA_REAL"), "solo Windows con NOTAS_VENTANA_REAL=1")
class PortapapelesWebView2Tests(unittest.TestCase):
    def test_copiar_para_correo_deja_html_y_texto(self):
        try:
            import webview  # noqa: F401
        except ImportError:
            self.skipTest("sin pywebview")
        tmp = tempfile.mkdtemp(prefix="notas-portapapeles-")
        page, out = os.path.join(tmp, "pagina.html"), os.path.join(tmp, "resultado.json")
        with open(page, "w", encoding="utf-8") as f:
            f.write(pagina())
        p = subprocess.run([sys.executable, "-c", PROBE, page, out, TITLE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=240)
        self.assertTrue(os.path.isfile(out), f"sin resultado (rc {p.returncode}):\n{p.stdout[-2000:]}\n"
                                             f"{p.stderr[-3000:]}")
        with open(out, encoding="utf-8") as f:
            res = json.load(f)
        os.makedirs(ART, exist_ok=True)
        with open(os.path.join(ART, "portapapeles_webview2.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        self.assertNotIn("error", res, res)
        via = "clic" if (res.get("clic") or {}).get("m") == "ok" else "script"
        r = res.get(via) or {}
        self.assertEqual(r.get("m"), "ok", f"WebView2 no escribió el HTML (sin ClipboardItem o rechazado): {res}")
        self.assertIn("HTML Format", r["formats"], res)
        self.assertIn("13", r["formats"], res)  # CF_UNICODETEXT (los formatos estándar salen por número)
        html, text = r["html"], r["text"]
        self.assertIn("StartFragment", html)  # la cabecera del formato HTML de Windows
        self.assertIn("<b>Luis</b>", html)
        self.assertIn("Comité de seguimiento", html)
        self.assertTrue(text.startswith("Comité de seguimiento · viernes 6 nov, 10:00 · Ana, Luis"), text)
        for marca in ("##", "[ ]", "**", "[1:12]"):
            self.assertNotIn(marca, html + text, marca)
        print(f"\n   portapapeles WebView2: HTML + texto con un {via} "
              f"({json.loads(res.get('pagina') or '{}').get('ua', '')})", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
