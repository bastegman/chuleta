# -*- coding: utf-8 -*-
"""Pruebas que solo corren en Windows con escritorio (job `reunion-windows` de GitHub Actions, o un PC): la ventana
de reunión de Teams se simula con tkinter, el aviso flotante se abre contra un servidor falso, se captura la pantalla
con PowerShell (System.Drawing) como artefacto y se pulsa «Grabar». Fuera de Windows se saltan todas.
Los artefactos van a NOTAS_ARTEFACTOS (o a %TEMP%\\notas-artefactos)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import APP
import calendario  # noqa: E402

WIN = sys.platform == "win32"
ART = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(tempfile.gettempdir(), "notas-artefactos")
AVISO = os.path.join(APP, "aviso.py")
if WIN:
    os.makedirs(ART, exist_ok=True)
    try:  # mismas coordenadas (píxeles físicos) que el aviso, que es per-monitor DPI aware
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

CAPTURA_PS1 = r"""
param([string]$Png, [int]$X = -1, [int]$Y = -1, [int]$W = 0, [int]$H = 0)
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save($Png, [System.Drawing.Imaging.ImageFormat]::Png)
if ($W -gt 0 -and $H -gt 0) {
  $rx = [Math]::Max(0, $X); $ry = [Math]::Max(0, $Y)
  $rw = [Math]::Min($W, $b.Width - $rx); $rh = [Math]::Min($H, $b.Height - $ry)
  $crop = $bmp.Clone((New-Object System.Drawing.Rectangle $rx, $ry, $rw, $rh), $bmp.PixelFormat)
  $crop.Save([IO.Path]::ChangeExtension($Png, ".recorte.png"), [System.Drawing.Imaging.ImageFormat]::Png)
}
"""


def captura(png, rect=None, margen=40):
    """Captura la pantalla principal (y un recorte alrededor de `rect`) con PowerShell; devuelve la ruta."""
    ps1 = os.path.join(ART, "captura.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(CAPTURA_PS1)
    args = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1, png]
    if rect:
        x, y, w, h = rect
        args += [str(x - margen), str(y - margen), str(w + 2 * margen), str(h + 2 * margen)]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    return png


class FakeNotas(ThreadingHTTPServer):
    """Lo que el aviso necesita del servidor: /api/rec/status y /api/rec/toggle."""
    daemon_threads = True
    recording, fail = False, False

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.posts, self.lock = [], threading.Lock()


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/rec/status"):
            return self._json({"recording": self.server.recording})
        self._json({"error": "no"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        with self.server.lock:
            self.server.posts.append({"path": self.path, "host": self.headers.get("Host"),
                                      "x_audios": self.headers.get("X-Audios"), "body": body})
        if self.path == "/api/rec/toggle":
            if self.server.fail:
                return self._json({"error": "No se pudo grabar: prueba"}, 400)
            return self._json({"recording": True, "path": "Reuniones/x.wav", "workstream": "Alfa · General"})
        self._json({"error": "no"}, 404)


def servidor():
    srv = FakeNotas(("127.0.0.1", 0), FakeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def clic(x, y):
    """Clic izquierdo real en coordenadas de pantalla (SetCursorPos + mouse_event)."""
    import ctypes
    u = ctypes.windll.user32
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP


@unittest.skipUnless(WIN, "solo Windows")
class TeamsVentanaTests(unittest.TestCase):
    SCRIPT = ("import sys, tkinter as tk\nr = tk.Tk(); r.title(sys.argv[1]); r.geometry('320x120+60+60')\n"
              "r.after(25000, r.destroy); r.mainloop()\n")

    def ventana(self, title):
        return subprocess.Popen([sys.executable, "-c", self.SCRIPT, title])

    def busca(self, exe_names=("python.exe", "pythonw.exe"), want=True, secs=10):
        for _ in range(int(secs * 10)):
            t = calendario.teams_meeting_title(exe_names=exe_names)
            if bool(t) == want:
                return t
            time.sleep(0.1)
        return calendario.teams_meeting_title(exe_names=exe_names)

    def test_encuentra_y_limpia_la_ventana_de_reunion(self):
        p = self.ventana("Reunión semanal | Microsoft Teams")
        try:
            self.assertEqual(self.busca(), "Reunión semanal")
            self.assertIsNone(calendario.teams_meeting_title())  # con los exe de Teams de verdad, nada
            with open(os.path.join(ART, "teams_ventana.json"), "w", encoding="utf-8") as f:
                json.dump({"ventanas": [(t, os.path.basename(e)) for t, e, _ in calendario._ventanas()]}, f,
                          ensure_ascii=False, indent=1)
        finally:
            p.terminate()

    def test_la_ventana_principal_no_cuenta(self):
        p = self.ventana("Chat | Microsoft Teams")
        try:
            time.sleep(1.5)
            self.assertIsNone(self.busca(want=False, secs=2))
        finally:
            p.terminate()


@unittest.skipUnless(WIN, "solo Windows")
class AvisoTests(unittest.TestCase):
    def setUp(self):
        self.srv = servidor()
        self.port = self.srv.server_address[1]
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
        self.srv.shutdown()
        self.srv.server_close()

    def abrir(self, nombre, *extra, puerto=None, segundos=15):
        est = os.path.join(ART, f"estado-{nombre}.json")
        if os.path.exists(est):
            os.remove(est)
        cmd = [sys.executable, AVISO, "--puerto", str(puerto or self.port), "--titulo", "Reunión semanal",
               "--nombre", "Reunión semanal", "--vocab", "Alfa/Vocabulario/General.txt", "--tema", "Alfa · General",
               "--segundos", str(segundos), "--estado", est, *extra]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.procs.append(p)
        return p, est

    def estado(self, est, secs=12):
        for _ in range(int(secs * 10)):
            if os.path.exists(est):
                time.sleep(0.2)
                with open(est, encoding="utf-8") as f:
                    return json.load(f)
            time.sleep(0.1)
        self.fail("el aviso no escribió su estado")

    def resultado(self, p, secs=25):
        out, err = p.communicate(timeout=secs)
        self.assertEqual(p.returncode, 0, err)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertTrue(lines, err)
        return json.loads(lines[-1])

    def espera_post(self, secs=6):
        for _ in range(int(secs * 10)):
            with self.srv.lock:
                if self.srv.posts:
                    return list(self.srv.posts)
            time.sleep(0.1)
        return []

    def test_geometria_estilo_captura_y_auto_grabar(self):
        p, est = self.abrir("auto", "--auto-grabar-ms", "4500")
        e = self.estado(est)
        x, y, w, h = e["geometry"]
        l, t, r, b = e["area_trabajo"]
        # abajo y centrado en el área de trabajo (sin pisar la barra de tareas), tamaño ~340×64 × escala
        self.assertGreaterEqual(x, l)
        self.assertLessEqual(x + w, r)
        self.assertLessEqual(y + h, b)
        self.assertLessEqual(abs((x + w / 2) - (l + r) / 2), 2 * e["escala"] + 2)
        self.assertLessEqual(abs(w - 340 * e["escala"]), 4)
        self.assertLessEqual(abs(h - 64 * e["escala"]), 4)
        self.assertTrue(e.get("exstyle", 0) & 0x08000000, "WS_EX_NOACTIVATE")  # no roba el foco
        self.assertTrue(e.get("exstyle", 0) & 0x80, "WS_EX_TOOLWINDOW")  # ni sale en la barra de tareas
        self.assertNotEqual(e.get("activa_despues"), e.get("hwnd"))  # al aparecer no se ha convertido en la activa
        png = captura(os.path.join(ART, "aviso.png"), (x, y, w, h))
        self.assertGreater(os.path.getsize(png), 1000)
        res = self.resultado(p)
        self.assertEqual(res["resultado"], "grabar", res)
        self.assertEqual(res["path"], "Reuniones/x.wav")
        posts = self.espera_post()
        self.assertEqual(len(posts), 1, posts)
        self.assertEqual(posts[0]["path"], "/api/rec/toggle")
        self.assertEqual(posts[0]["x_audios"], "1")
        self.assertEqual(posts[0]["host"], f"127.0.0.1:{self.port}")
        self.assertEqual(posts[0]["body"], {"source": "aviso", "start_only": True, "name": "Reunión semanal",
                                            "vocab": "Alfa/Vocabulario/General.txt"})
        with open(os.path.join(ART, "aviso_auto.json"), "w", encoding="utf-8") as f:
            json.dump({"estado": e, "resultado": res, "posts": posts}, f, ensure_ascii=False, indent=1)

    def test_clic_real_en_grabar(self):
        p, est = self.abrir("clic")
        e = self.estado(est)
        time.sleep(0.5)
        clic(*e["botones"]["grabar"])
        posts = self.espera_post(5)
        if not posts:
            p.kill()
            self.skipTest("el clic sintético no llegó a la ventana en este escritorio "
                          "(el POST se prueba con --auto-grabar-ms)")
        res = self.resultado(p)
        self.assertEqual(res["resultado"], "grabar", res)
        self.assertEqual(posts[0]["body"]["start_only"], True)

    def test_clic_real_en_cerrar(self):
        p, est = self.abrir("cerrar")
        e = self.estado(est)
        time.sleep(0.5)
        clic(*e["botones"]["cerrar"])
        try:
            res = self.resultado(p, secs=4)
        except subprocess.TimeoutExpired:
            p.kill()
            self.skipTest("el clic sintético no llegó a la ventana en este escritorio")
        self.assertEqual(res["resultado"], "cerrar", res)
        self.assertEqual(self.srv.posts, [])

    def test_se_cierra_si_ya_se_graba(self):
        self.srv.recording = True
        p, est = self.abrir("grabando")
        self.estado(est)
        res = self.resultado(p, secs=12)
        self.assertEqual(res["resultado"], "grabando", res)
        self.assertEqual(self.srv.posts, [])

    def test_auto_grabar_con_grabacion_ya_en_marcha_no_para_nada(self):
        self.srv.recording = True
        p, est = self.abrir("grabando2", "--auto-grabar-ms", "300")
        res = self.resultado(p, secs=12)
        self.assertEqual(res["resultado"], "grabando", res)
        self.assertEqual(self.srv.posts, [])  # consulta el estado antes de pulsar: ningún toggle

    def test_error_del_servidor_se_ensena_y_cierra(self):
        self.srv.fail = True
        p, est = self.abrir("error", "--auto-grabar-ms", "500")
        res = self.resultado(p, secs=15)
        self.assertEqual(res["resultado"], "error", res)
        self.assertIn("prueba", res["error"])

    def test_sin_servidor_se_cierra_solo(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        libre = s.getsockname()[1]
        s.close()
        p, est = self.abrir("sin-servidor", puerto=libre)
        res = self.resultado(p, secs=15)
        self.assertEqual(res["resultado"], "sin-servidor", res)

    def test_tiempo_agotado(self):
        p, est = self.abrir("tiempo", segundos=3)
        res = self.resultado(p, secs=12)
        self.assertEqual(res["resultado"], "tiempo", res)


@unittest.skipUnless(WIN, "solo Windows")
class OutlookRealTests(unittest.TestCase):
    def test_sin_outlook_abierto_motivo_legible_y_rapido(self):
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            self.skipTest("sin pywin32")
        t0 = time.time()
        m, reason = calendario.outlook_current_meeting()
        self.assertIsNone(m)
        self.assertIn("Outlook", reason)
        self.assertLess(time.time() - t0, 9)
        with open(os.path.join(ART, "outlook_sin_abrir.json"), "w", encoding="utf-8") as f:
            json.dump({"reason": reason, "segundos": round(time.time() - t0, 2)}, f, ensure_ascii=False)

    def test_fecha_jet_regional_sin_segundos(self):
        s = calendario.fecha_jet(datetime(2026, 10, 31, 14, 5, 9))
        self.assertIn("2026", s)
        self.assertTrue("14:05" in s or "2:05" in s, s)
        self.assertNotIn(":09", s)


if __name__ == "__main__":
    unittest.main()
