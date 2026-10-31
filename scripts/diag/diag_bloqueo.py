# -*- coding: utf-8 -*-
"""Solo diagnostico (workflow diag-bloqueo, Windows): reproduce el cuelgue de la API con una reunion en la carpeta.

Monta <USERPROFILE>/Chuleta con el _app de un commit y una reunion falsa, arranca server.py (pythonw con ventana,
python --no-browser sin ventana, o sin ventana y con la pagina cargada en Edge headless), sondea /api/ping,
/api/config, /api/tree, /api/vocab y /api/jobs (un hilo por ruta, cada 1 s) durante --seconds y, en cuanto una
peticion pasa de 5 s, vuelca las pilas de todos los hilos con py-spy (Python con --locals y nativo), las ventanas del
proceso y una captura de pantalla. Al final pide POST /api/apagar y mira si el proceso termina. Todo va a --out.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTES = ("/api/ping", "/api/config", "/api/tree", "/api/vocab", "/api/jobs")
STUCK_S = 5.0


def write(path, text, bom=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="\r\n") as f:
        f.write(text + "\n")


def make_meeting(root, kind):
    """Como la reunion falsa del E2E (lookfeel_e2e.ps1, fase r): Set-Content -Encoding UTF8 de PowerShell 5.1 escribe
    con BOM. kind: completa (audio, .txt, .md, .resumen.md, Vocabulario y marca de Internet en el audio, como en las
    fases f..u3), completa-sinmarca, solo-wav, hablantes (audio + .hablantes.json), vocab (solo la lista), ninguna."""
    d = os.path.join(root, "Proyecto Alfa")
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, "2026-10-31 10-00-00 Reunion de prueba")
    if kind in ("completa", "completa-sinmarca", "solo-wav", "hablantes"):
        w = wave.open(base + ".wav", "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(32000))
        w.close()
    if kind in ("completa", "completa-sinmarca"):
        write(base + ".txt", "Transcripcion de prueba: hola.")
        write(base + ".md", "# Notas de prueba")
        write(base + ".resumen.md", "Resumen de prueba")
    if kind in ("completa", "completa-sinmarca", "vocab"):
        write(os.path.join(d, "Vocabulario", "Tema.txt"), "Alfa")
    if kind == "completa":
        with open(base + ".wav:Zone.Identifier", "w", encoding="ascii") as f:
            f.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    if kind == "hablantes":
        side = {"version": 1, "tramos": [[0.0, 0.5, "H1"], [0.5, 1.0, "H2"]],
                "hablantes": {"H1": {"persona": None}, "H2": {"persona": None}}}
        write(base + ".hablantes.json", json.dumps(side), bom=False)
    return sorted(os.listdir(d))


class Probe:
    def __init__(self, port, data_dir, log):
        self.base, self.data_dir, self.log = f"http://127.0.0.1:{port}", data_dir, log
        self.samples, self.inflight, self.lock, self.stop = [], {}, threading.Lock(), threading.Event()
        self.t0 = time.time()

    def headers(self, post=False):
        h = {}
        if post:
            h["X-Audios"] = "1"
            h["Content-Type"] = "application/json"
        tf = os.path.join(self.data_dir, "sesion.token")
        try:
            with open(tf, "r", encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                h["X-Chuleta-Token"] = tok
        except OSError:
            pass
        return h

    def get(self, route, timeout):
        req = urllib.request.Request(self.base + route, headers=self.headers())
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return time.time() - t, r.status, len(body), body
        except urllib.error.HTTPError as e:
            return time.time() - t, e.code, 0, b""
        except Exception as e:
            return time.time() - t, f"{e.__class__.__name__}: {e}", 0, b""

    def post(self, route, timeout):
        req = urllib.request.Request(self.base + route, data=b"{}", method="POST", headers=self.headers(True))
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return time.time() - t, r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return time.time() - t, e.code, ""
        except Exception as e:
            return time.time() - t, f"{e.__class__.__name__}: {e}", ""

    def loop(self, route):
        while not self.stop.is_set():
            start = time.time()
            with self.lock:
                self.inflight[route] = start
            lat, status, size, _ = self.get(route, timeout=20)
            with self.lock:
                self.inflight.pop(route, None)
                self.samples.append({"t": round(start - self.t0, 2), "route": route, "s": round(lat, 3),
                                     "status": status, "bytes": size})
            self.stop.wait(1.0)

    def stuck(self):
        """(ruta, segundos) de la peticion en curso mas antigua si pasa de STUCK_S."""
        now = time.time()
        with self.lock:
            worst = max(self.inflight.items(), key=lambda kv: now - kv[1], default=None)
        if worst and now - worst[1] > STUCK_S:
            return worst[0], now - worst[1]
        return None


def windows_of(pid):
    """Ventanas de nivel superior del proceso (titulo, clase, visible): un MessageBox es la clase #32770."""
    if sys.platform != "win32":
        return []
    import ctypes
    import ctypes.wintypes as wt
    user32, out = ctypes.windll.user32, []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            t, c = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, t, 256)
            user32.GetClassNameW(hwnd, c, 256)
            out.append({"hwnd": hwnd, "title": t.value, "class": c.value,
                        "visible": bool(user32.IsWindowVisible(hwnd))})
        return True
    user32.EnumWindows(proc(cb), 0)
    return out


def screenshot(path):
    ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
          "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
          "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height; $g=[System.Drawing.Graphics]::FromImage($i); "
          "$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size); $i.Save('%s'); $g.Dispose(); $i.Dispose()" % path)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)


def dump(pid, out, tag, log):
    """py-spy: pilas de Python de todos los hilos (con variables locales) y pilas nativas."""
    for kind, extra in (("py", ["--locals"]), ("native", ["--native"])):
        path = os.path.join(out, f"pyspy-{tag}-{kind}.txt")
        try:
            r = subprocess.run(["py-spy", "dump", "--pid", str(pid)] + extra, capture_output=True, text=True,
                               timeout=90, encoding="utf-8", errors="replace")
            text = (r.stdout or "") + ("\n--- stderr ---\n" + r.stderr if r.stderr else "")
        except Exception as e:
            text = f"py-spy fallo: {e!r}"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        log(f"volcado {tag}/{kind}: {len(text)} caracteres")
    try:
        with open(os.path.join(out, f"ventanas-{tag}.json"), "w", encoding="utf-8") as f:
            json.dump(windows_of(pid), f, ensure_ascii=False, indent=1)
        if sys.platform == "win32":
            screenshot(os.path.join(out, f"pantalla-{tag}.png"))
    except Exception as e:
        log(f"ventanas/captura fallo: {e!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="carpeta que contiene _app (checkout del commit a probar)")
    ap.add_argument("--modo", choices=("ventana", "sin-ventana", "edge"), required=True)
    ap.add_argument("--reunion", default="completa")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    a.app, a.out = os.path.abspath(a.app), os.path.abspath(a.out)  # el servidor arranca con otro cwd
    os.makedirs(a.out, exist_ok=True)
    logf = open(os.path.join(a.out, "diag.log"), "a", encoding="utf-8")

    def log(msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    root = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Chuleta")
    data = os.path.join(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or "/tmp", "chuleta-diag-data")
    for p in (root, data):
        shutil.rmtree(p, ignore_errors=True)
    shutil.copytree(os.path.join(a.app, "_app"), os.path.join(root, "_app"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    files = make_meeting(root, a.reunion) if a.reunion != "ninguna" else []
    log(f"carpeta {root} · datos {data} · reunion {a.reunion}: {files}")
    app_dir = os.path.join(root, "_app")
    exe_dir = os.path.dirname(sys.executable)
    exe = os.path.join(exe_dir, "pythonw.exe" if a.modo == "ventana" else "python.exe")
    if not os.path.exists(exe):  # fuera de Windows (prueba local del propio script)
        exe = sys.executable
    fh = os.path.join(a.out, "faulthandler.txt")
    cmd = [exe, os.path.join(HERE, "diag_launch.py"), app_dir, fh, "20", "--data", data, "--port", str(a.port)]
    if a.modo != "ventana":
        cmd.append("--no-browser")
    out_f = open(os.path.join(a.out, "server-stdout.txt"), "w", encoding="utf-8")
    p = subprocess.Popen(cmd, cwd=app_dir, stdout=out_f, stderr=subprocess.STDOUT)
    log(f"servidor pid {p.pid}: {os.path.basename(exe)} ({a.modo})")
    probe = Probe(a.port, data, log)
    t_ready = None
    for _ in range(180):
        lat, status, _, _ = probe.get("/api/ping", timeout=2)
        if status == 200:
            t_ready = time.time()
            break
        if p.poll() is not None:
            break
        time.sleep(0.5)
    log(f"ping: {'listo en %.1f s' % (t_ready - probe.t0) if t_ready else 'NUNCA'} (proceso vivo: {p.poll() is None})")
    edge = None
    if t_ready and a.modo == "edge":
        cands = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
        exe_edge = next((x for x in cands if os.path.exists(x)), None)
        prof = os.path.join(data, "edge-profile")
        if exe_edge:
            edge = subprocess.Popen([exe_edge, "--headless=new", "--disable-gpu", f"--user-data-dir={prof}",
                                     "--no-first-run", f"http://127.0.0.1:{a.port}/"])
            log(f"edge headless pid {edge.pid}")
        else:
            log("sin Edge")
    dumps, last_dump = 0, 0.0
    if t_ready:
        probe.t0 = time.time()
        threads = [threading.Thread(target=probe.loop, args=(r,), daemon=True) for r in ROUTES]
        for t in threads:
            t.start()
        end = time.time() + a.seconds
        first_stuck = None
        while time.time() < end and p.poll() is None:
            s = probe.stuck()
            if s and first_stuck is None:
                first_stuck = (round(time.time() - probe.t0, 1), s[0], round(s[1], 1))
                log(f"ATASCO: {s[0]} lleva {s[1]:.1f} s (t={first_stuck[0]})")
            if s and dumps < 3 and time.time() - last_dump > 45:
                dumps += 1
                last_dump = time.time()
                dump(p.pid, a.out, f"atasco{dumps}", log)
            time.sleep(0.5)
        if dumps == 0 and p.poll() is None:
            dump(p.pid, a.out, "final", log)
        probe.stop.set()
    alive_before = p.poll() is None
    lat, status, body = probe.post("/api/apagar", timeout=15)
    log(f"POST /api/apagar: {status} en {lat:.1f} s {body[:200]}")
    try:
        p.wait(20)
        exited = True
    except subprocess.TimeoutExpired:
        exited = False
        dump(p.pid, a.out, "no-apaga", log)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    log(f"proceso terminado tras apagar: {exited} (rc {p.poll()})")
    if edge is not None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(edge.pid)], capture_output=True)
    time.sleep(1)
    for name in ("server.log",):
        src = os.path.join(data, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(a.out, name))
    summary = {"modo": a.modo, "reunion": a.reunion, "ping_listo": bool(t_ready), "vivo_al_final": alive_before,
               "apagar": {"status": status, "s": round(lat, 2), "termina_en_20s": exited}, "rutas": {}}
    for r in ROUTES:
        ss = [x for x in probe.samples if x["route"] == r]
        ok = [x["s"] for x in ss if x["status"] == 200]
        summary["rutas"][r] = {"n": len(ss), "ok": len(ok), "max_s": max([x["s"] for x in ss], default=None),
                               "lentas_2s": sum(1 for x in ss if x["s"] >= 2 or x["status"] != 200),
                               "fallos": sorted({str(x["status"]) for x in ss if x["status"] != 200})}
    crit = [summary["rutas"][r] for r in ("/api/config", "/api/tree")]
    covered = max((x["t"] + x["s"] for x in probe.samples), default=0)
    summary["cubre_s"] = round(covered, 1)
    summary["veredicto"] = "PASA" if (t_ready and all(c["n"] > 0 and c["lentas_2s"] == 0 for c in crit)
                                      and covered >= a.seconds - 5 and status == 200 and exited) else "FALLA"
    with open(os.path.join(a.out, "muestras.json"), "w", encoding="utf-8") as f:
        json.dump(probe.samples, f)
    with open(os.path.join(a.out, "resumen.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    log("RESUMEN " + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
