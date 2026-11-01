# -*- coding: utf-8 -*-
"""Solo diagnostico (workflow diag-bloqueo, trabajo e2e): corre en segundo plano mientras el E2E de instalacion
(lookfeel_e2e.ps1) instala y arranca la app de verdad. Cada 3 s busca quien escucha en 127.0.0.1:<puerto> y le pide
GET /api/config; si no contesta en 8 s, vuelca las pilas de ese proceso con py-spy (Python con --locals y nativo),
sus ventanas y una captura de pantalla (como mucho 3 volcados por proceso, separados 40 s)."""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diag_bloqueo import dump  # noqa: E402


def listener(port):
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    for line in out.splitlines():
        m = re.match(r"\s*TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
        if m and int(m.group(1)) == port:
            return int(m.group(2))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=40)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)
    os.makedirs(a.out, exist_ok=True)
    logf = open(os.path.join(a.out, "vigia.log"), "a", encoding="utf-8")

    def log(msg):
        logf.write(time.strftime("%H:%M:%S ") + msg + "\n")
        logf.flush()

    token = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AudiosApp", "sesion.token")
    dumps, end = {}, time.time() + a.minutes * 60
    while time.time() < end:
        pid = listener(a.port)
        if pid:
            h = {}
            try:
                with open(token, encoding="utf-8") as f:
                    h["X-Chuleta-Token"] = f.read().strip()
            except OSError:
                pass
            t = time.time()
            try:
                with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{a.port}/api/config", headers=h),
                                            timeout=8) as r:
                    r.read()
                    res = f"{r.status}"
            except Exception as e:
                res = f"{e.__class__.__name__}: {e}"
            lat = time.time() - t
            log(f"pid {pid} /api/config {res} {lat:.2f} s")
            n, last = dumps.get(pid, (0, 0.0))
            if lat >= 8 and n < 3 and time.time() - last > 40:
                dumps[pid] = (n + 1, time.time())
                tag = f"{time.strftime('%H%M%S')}-pid{pid}"
                log(f"ATASCO: volcado {tag}")
                dump(pid, a.out, tag, log)
                with open(os.path.join(a.out, f"netstat-{tag}.txt"), "w", encoding="utf-8") as f:
                    f.write(subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout)
        time.sleep(3)
    log("fin")
    json.dump({k: v[0] for k, v in dumps.items()}, open(os.path.join(a.out, "volcados.json"), "w"))


if __name__ == "__main__":
    main()
