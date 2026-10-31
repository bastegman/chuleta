# -*- coding: utf-8 -*-
"""Banco de rendimiento: mide lo que limita la transcripcion por nuestro lado, con dobles donde hace falta.

    python scripts/rendimiento_bench.py                 # todo lo que se pueda medir aqui
    python scripts/rendimiento_bench.py red worker      # solo esas medidas
    python scripts/rendimiento_bench.py --json antes.json --minutes 60 --audios 1000

Medidas (cada una imprime su tabla y, con --json, se guarda para comparar antes/despues):
  red         coste de abrir conexion con api.groq.com en cada peticion frente a reutilizarla (keep-alive).
              Sin clave: la API responde 401 y eso basta para medir DNS + TCP + TLS. Necesita red.
  worker      arranque en frio del proceso de Whisper (proceso + carga del modelo) frente a una peticion con el
              modelo ya cargado, y la memoria (RSS) que ocupa. Necesita faster-whisper; usa los modelos ya
              descargados (small, medium) o, si no hay ninguno, tiny (lo dice).
  audio       una hora de audio sintetico: decodificar a 16 kHz mono, buscar silencios, cortar y escribir FLAC, y el
              tiempo total de CloudEngine.transcribe con una API falsa (si la conversion se solapa con la subida).
  biblioteca  una biblioteca de --audios reuniones de 1 s con sus .txt/.srt/.md: scan(), Library.refresh(),
              /api/tree por HTTP (primer pintado y sondeos siguientes).

No lee .env ni manda ninguna peticion autenticada a ningun sitio.
"""
import argparse
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "_app"))

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
RESULTS = {}


def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def ms(x):
    return "-" if x is None else f"{x * 1000:.0f} ms"


def row(name, xs, unit="ms"):
    """Guarda n/p50/p95/min y lo imprime en una linea."""
    f = ms if unit == "ms" else (lambda v: "-" if v is None else f"{v:.2f} s")
    d = {"n": len(xs), "p50": pct(xs, 0.5), "p95": pct(xs, 0.95), "min": min(xs) if xs else None}
    print(f"  {name:<58} n={d['n']:<3} p50 {f(d['p50']):>9}  p95 {f(d['p95']):>9}  min {f(d['min']):>9}")
    return d


def section(title):
    print(f"\n== {title}")


def wav_silence_1s():
    """1 s de silencio WAV 16 kHz mono (el cuerpo de una peticion pequena, como un trozo del directo)."""
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0" * 32000)
    return b.getvalue()


# ----------------------------------------------------------------------------- red

def bench_red(reps):
    import requests
    section(f"red: {reps} peticiones a api.groq.com sin clave (401), conexion nueva frente a reutilizada")
    body = wav_silence_1s()
    data = {"model": "whisper-large-v3-turbo", "response_format": "verbose_json"}

    def one(post):
        t = time.perf_counter()
        try:
            r = post(GROQ_URL, data=data, files={"file": ("t.wav", io.BytesIO(body), "audio/wav")}, timeout=(10, 30))
            code = r.status_code
        except requests.RequestException as e:
            print(f"  sin red ({e.__class__.__name__}): se omite esta medida")
            return None, None
        return time.perf_counter() - t, code
    fresh, codes = [], set()
    for _ in range(reps):
        dt, code = one(requests.post)
        if dt is None:
            return
        fresh.append(dt)
        codes.add(code)
    s = requests.Session()
    s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4))
    one(s.post)  # la primera abre la conexion: no cuenta
    reused = []
    for _ in range(reps):
        dt, code = one(s.post)
        reused.append(dt)
        codes.add(code)
    try:
        import engines
        http = getattr(engines, "HTTP", None)
    except ImportError:
        http = None
    app = []
    if http is not None:  # la sesion que usa la app (misma politica de adaptador y timeouts)
        one(http.session().post)
        for _ in range(reps):
            dt, code = one(http.session().post)
            app.append(dt)
    a = row("conexion nueva en cada peticion (requests.post)", fresh)
    b = row("conexion reutilizada (requests.Session, keep-alive)", reused)
    c = row("sesion de la app (engines.HTTP)", app) if app else None
    print(f"  codigos HTTP vistos: {sorted(codes)} · ahorro p50 por peticion: {ms(a['p50'] - b['p50'])}")
    RESULTS["red"] = {"fresh": a, "session": b, "app": c, "codes": sorted(codes)}


# ----------------------------------------------------------------------------- worker

def speech_like(seconds, sr=16000):
    """Audio que la VAD toma por voz: tonos modulados con pausas (sin descargar nada)."""
    import numpy as np
    t = np.arange(int(seconds * sr)) / sr
    env = (np.sin(2 * np.pi * 3.0 * t) > -0.3).astype(np.float32)  # silabas de ~0,2 s
    y = 0.3 * env * (np.sin(2 * np.pi * 140 * t) + 0.5 * np.sin(2 * np.pi * 280 * t)
                     + 0.2 * np.sin(2 * np.pi * 2400 * t))
    return y.astype(np.float32)


def rss_mb(pid):
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True).stdout
            return float(out.strip().split('","')[-1].strip('"').split()[0].replace(".", "").replace(",", "")) / 1024
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout
        return int(out.strip()) / 1024
    except (ValueError, OSError, IndexError):
        return None


def bench_worker(models, threads):
    import engines
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        section("worker: faster-whisper no esta instalado en este Python: se omite")
        return
    cached = [m for m in models if engines.model_cached(m)]
    if not cached:
        if engines.model_cached("tiny"):
            cached = ["tiny"]
            print("  (ningun modelo de la app descargado: se mide con tiny, que es ~10x mas pequeno que small)")
        else:
            section("worker: ningun modelo descargado (ni tiny): se omite")
            return
    section(f"worker: arranque en frio frente a caliente ({', '.join(cached)}; {threads} hilos)")
    log_dir = tempfile.mkdtemp(prefix="notas-bench-worker-")
    audio = speech_like(3.0)
    out = {}
    for name in cached:
        w = engines.WorkerClient(f"bench-{name}", log_dir)
        cold = []
        for _ in range(2):  # la 1a lee el modelo del disco; la 2a ya lo tiene la cache del sistema
            w.kill()
            t = time.perf_counter()
            w.ensure(name, threads)
            cold.append(time.perf_counter() - t)
        warm = []
        for _ in range(3):
            t = time.perf_counter()
            w.request({"op": "window", "model": name, "threads": threads, "language": "es", "beam": 1},
                      audio=audio, timeout=600)
            warm.append(time.perf_counter() - t)
        rss = rss_mb(w.p.pid)
        w.kill()
        print(f"  {name:<10} frio (proceso + modelo) {cold[0]:6.2f} s / {cold[1]:6.2f} s (2a vez) · "
              f"trozo de 3 s con el modelo cargado p50 {pct(warm, 0.5):.2f} s · RSS {rss and round(rss)} MB")
        out[name] = {"cold_disk": cold[0], "cold_cached": cold[1], "warm": warm, "rss_mb": rss}
    shutil.rmtree(log_dir, ignore_errors=True)
    RESULTS["worker"] = out


# ----------------------------------------------------------------------------- audio

def synth_hour(path, minutes, rate=48000, channels=2):
    """WAV 16 bit como el de la grabadora (48 kHz estereo): frases de tono modulado y pausas, por bloques."""
    import numpy as np
    rng = np.random.default_rng(7)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        block = rate * 10  # 10 s por bloque
        for i in range(int(minutes * 6)):
            t = np.arange(block) / rate
            talk = np.zeros(block, np.float32)
            pos = 0
            while pos < block:  # frase de 2-7 s + pausa de 0,3-1,5 s
                n = int(rng.uniform(2, 7) * rate)
                env = (np.sin(2 * np.pi * 3.5 * t[:n]) > -0.4).astype(np.float32)
                f0 = rng.uniform(110, 220)
                frase = 0.25 * env * (np.sin(2 * np.pi * f0 * t[:n]) + 0.4 * np.sin(2 * np.pi * 2 * f0 * t[:n]))
                talk[pos:pos + n] = frase[:block - pos]
                pos += n + int(rng.uniform(0.3, 1.5) * rate)
            y = talk + 0.002 * rng.standard_normal(block).astype(np.float32)
            pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
            w.writeframes(np.repeat(pcm[:, None], channels, axis=1).tobytes())


class FakeCloud:
    """CloudEngine con la peticion sustituida: tarda api_s por parte (lo que tarda Groq con 10 min), sin red."""

    def __init__(self, engines, tmp, api_s):
        self.api_s = api_s
        self.eng = engines.CloudEngine(tmp)
        self.sent = []
        self.eng._post = self._post

    def _post(self, cfg, chunk, language, prompt, report, cancelled, context=None, model=None, timeout=300,
              timing=None):
        self.sent.append((time.perf_counter(), os.path.getsize(chunk)))
        time.sleep(self.api_s)
        return {"text": "x", "segments": [{"start": 0, "end": 1, "text": "x"}], "language": "es"}


def bench_audio(minutes, api_s, parallel):
    import engines
    try:
        import av  # noqa: F401
    except ImportError:
        section("audio: PyAV (av) no esta instalado en este Python: se omite")
        return
    section(f"audio: {minutes} min sinteticos (48 kHz estereo), API falsa de {api_s} s por parte, {parallel} hilos")
    tmp = tempfile.mkdtemp(prefix="notas-bench-audio-")
    try:
        wav = os.path.join(tmp, "hora.wav")
        t = time.perf_counter()
        synth_hour(wav, minutes)
        print(f"  WAV generado: {os.path.getsize(wav) / 1e6:.0f} MB en {time.perf_counter() - t:.1f} s")
        t = time.perf_counter()
        y = engines.decode_16k(wav)
        t_dec = time.perf_counter() - t
        t = time.perf_counter()
        sil = engines.find_silences(y)
        t_sil = time.perf_counter() - t
        dur = len(y) / 16000
        parts = engines.CloudEngine._cut_points(dur, sil, 600)
        t = time.perf_counter()
        paths = engines.CloudEngine._extract_parts(y, parts, tmp)
        t_flac = time.perf_counter() - t
        flac_mb = sum(os.path.getsize(p) for p in paths) / 1e6
        print(f"  decodificar a 16 kHz mono {t_dec:6.2f} s · silencios {t_sil:5.2f} s ({len(sil)}) · "
              f"{len(parts)} partes · FLAC {t_flac:6.2f} s ({flac_mb:.0f} MB)")
        del y
        fake = FakeCloud(engines, tmp, api_s)
        cfg = {"provider": "groq", "api_key": "x", "model": "whisper-large-v3", "chunk_minutes": 10,
               "parallel": parallel}
        stages = []
        t0 = time.perf_counter()
        fake.eng.transcribe(wav, dur, cfg, "es", "", "bench", lambda **k: stages.append((time.perf_counter() - t0, k)),
                            lambda: False)
        total = time.perf_counter() - t0
        first = fake.sent[0][0] - t0 if fake.sent else None
        ideal = t_dec + t_sil + api_s * (-(-len(parts) // parallel))
        print(f"  CloudEngine.transcribe de principio a fin {total:6.2f} s · primera subida a los {first:.2f} s · "
              f"ideal sin esperas evitables ~{ideal:.1f} s")
        RESULTS["audio"] = {"minutes": minutes, "decode": t_dec, "silences": t_sil, "flac": t_flac, "parts": len(parts),
                            "flac_mb": flac_mb, "total": total, "first_upload": first, "ideal": ideal}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------- biblioteca

def make_library(root, n_audios, folders=25):
    """n_audios WAV de 1 s (16 kHz mono) con .txt, .srt y .md repartidos en carpetas de proyecto."""
    head = (b"RIFF" + struct.pack("<I", 36 + 32000) + b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", 32000))
    data = head + b"\0" * 32000
    srt = "1\n00:00:00,000 --> 00:00:01,000\nHola\n\n"
    for i in range(n_audios):
        d = os.path.join(root, f"Proyecto {i % folders:02d}", "Reuniones")
        os.makedirs(d, exist_ok=True)
        stem = os.path.join(d, f"Reunión {i:04d} 2026-{1 + i % 12:02d}-{1 + i % 28:02d} 10-{i % 60:02d}-00")
        with open(stem + ".wav", "wb") as f:
            f.write(data)
        with open(stem + ".srt", "w", encoding="utf-8") as f:
            f.write(srt)
        with open(stem + ".txt", "w", encoding="utf-8") as f:
            f.write("Hola\n")
        with open(stem + ".md", "w", encoding="utf-8") as f:
            f.write("---\ntitulo: Reunión\nestado: hecha\n---\nNotas\n")
        if i % 3 == 0:
            with open(stem + ".resumen.md", "w", encoding="utf-8") as f:
                f.write("## Resumen\nx\n")


def bench_biblioteca(n_audios, reps):
    import server
    from http.client import HTTPConnection
    section(f"biblioteca: {n_audios} reuniones con .txt/.srt/.md en 25 proyectos")
    tmp = tempfile.mkdtemp(prefix="notas-bench-lib-")
    root, data_dir = os.path.join(tmp, "Notas"), os.path.join(tmp, "datos")
    os.makedirs(os.path.join(root, "_app", "static"))
    os.makedirs(data_dir)
    t = time.perf_counter()
    make_library(root, n_audios)
    print(f"  biblioteca creada en {time.perf_counter() - t:.1f} s")
    saved = {k: getattr(server, k) for k in ("ROOT", "APP_DIR", "DATA_DIR", "STATIC_DIR", "COMPLETE")}
    links = dict(server.LINKS)
    server.LINKS.clear()
    server.ROOT, server.APP_DIR, server.DATA_DIR = root, os.path.join(root, "_app"), data_dir
    server.STATIC_DIR = os.path.join(root, "_app", "static")
    server.COMPLETE = server.Complete(os.path.join(data_dir, "completas.json"))
    out = {}
    try:
        scans = []
        for _ in range(reps):
            t = time.perf_counter()
            server.scan()
            scans.append(time.perf_counter() - t)
        out["scan"] = row("scan() (recorrer carpetas y stat de cada fichero)", scans)
        lib = server.Library(server.Durations(os.path.join(data_dir, "durations.json")))
        t = time.perf_counter()
        lib.refresh()
        out["refresh_first"] = time.perf_counter() - t
        print(f"  {'Library.refresh() la primera vez (duraciones sin cache)':<58} {ms(out['refresh_first']):>14}")
        time.sleep(2.2)  # que ninguna carpeta cuente como "recien modificada"
        lib.refresh()  # las duraciones ya en cache
        polls = []
        for _ in range(reps):
            lib.at -= 2  # como un sondeo pasado max_age (1 s en /api/tree) sin que nada haya cambiado
            t = time.perf_counter()
            lib.refresh(max_age=1.0)
            polls.append(time.perf_counter() - t)
        out["refresh_poll"] = row("Library.refresh() en cada sondeo (nada ha cambiado)", polls)
        forced = []
        for _ in range(reps):
            lib.at = 0  # los endpoints que escriben invalidan asi: escaneo completo
            t = time.perf_counter()
            lib.refresh()
            forced.append(time.perf_counter() - t)
        out["refresh_forced"] = row("Library.refresh() tras un cambio (at = 0: escaneo completo)", forced)
        same = []
        for _ in range(reps):  # el camino que deberia ser gratis: nada cambio y no ha pasado max_age
            t = time.perf_counter()
            lib.refresh(max_age=60)
            same.append(time.perf_counter() - t)
        out["refresh_same"] = row("Library.refresh(max_age=60) (cache en memoria)", same)
        # /api/tree de verdad, por HTTP: primer pintado (sin version) y sondeos con ?v=
        app = server.App.__new__(server.App)
        app.library = lib
        server.Handler.app = app
        srv = server.Server(("127.0.0.1", 0), server.Handler)
        port = srv.server_address[1]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            def get(path):
                c = HTTPConnection("127.0.0.1", port, timeout=30)
                c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
                r = c.getresponse()
                body = r.read()
                c.close()
                return r.status, body
            saved_port = server.PORT
            server.PORT = port
            firsts, sames, fulls = [], [], []
            for _ in range(reps):
                lib.at, lib.tree = 0, None
                t = time.perf_counter()
                st, body = get("/api/tree")
                firsts.append(time.perf_counter() - t)
            version = json.loads(body)["version"]
            size = len(body)
            time.sleep(2.2)
            for _ in range(reps):
                lib.at -= 2  # sondeo de la pagina (cada 6 s) sin que nada haya cambiado
                t = time.perf_counter()
                get(f"/api/tree?v={version}")
                sames.append(time.perf_counter() - t)
            for i in range(reps):  # un cambio real entre sondeo y sondeo: reunion nueva
                with open(os.path.join(root, "Proyecto 00", "Reuniones", f"nueva {i}.txt"), "w") as f:
                    f.write("x")
                lib.at -= 2
                t = time.perf_counter()
                get(f"/api/tree?v={version}")
                fulls.append(time.perf_counter() - t)
            server.PORT = saved_port
            out["tree_first"] = row("GET /api/tree primer pintado (sin cache en memoria)", firsts)
            out["tree_same"] = row("GET /api/tree?v=<igual> en cada sondeo (6 s), sin cambios", sames)
            out["tree_full"] = row("GET /api/tree tras un cambio en una carpeta (arbol entero)", fulls)
            out["tree_bytes"] = size
            print(f"  respuesta del arbol entero: {size / 1e6:.2f} MB")
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        for k, v in saved.items():
            setattr(server, k, v)
        server.LINKS.update(links)
        server.Handler.app = None
        shutil.rmtree(tmp, ignore_errors=True)
    RESULTS["biblioteca"] = out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("medidas", nargs="*", choices=["red", "worker", "audio", "biblioteca"], default=[])
    ap.add_argument("--reps", type=int, default=20, help="peticiones por variante en 'red' y sondeos en 'biblioteca'")
    ap.add_argument("--models", default="small,medium", help="modelos a medir en 'worker' (de los descargados)")
    ap.add_argument("--threads", type=int, default=max(1, min(8, (os.cpu_count() or 4) // 2)))
    ap.add_argument("--minutes", type=float, default=60, help="minutos de audio sintetico en 'audio'")
    ap.add_argument("--api-s", type=float, default=10.0, help="segundos que tarda la API falsa por parte de 10 min")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--audios", type=int, default=1000, help="reuniones en 'biblioteca'")
    ap.add_argument("--json", help="guardar los resultados en este fichero")
    a = ap.parse_args()
    which = a.medidas or ["red", "worker", "audio", "biblioteca"]
    print(f"Python {sys.version.split()[0]} · {sys.platform} · {os.cpu_count()} CPUs · medidas: {', '.join(which)}")
    if "red" in which:
        bench_red(a.reps)
    if "worker" in which:
        bench_worker([m.strip() for m in a.models.split(",") if m.strip()], a.threads)
    if "audio" in which:
        bench_audio(a.minutes, a.api_s, a.parallel)
    if "biblioteca" in which:
        bench_biblioteca(a.audios, a.reps)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(RESULTS, f, indent=1, ensure_ascii=False)
        print(f"\nresultados en {a.json}")


if __name__ == "__main__":
    main()
