# -*- coding: utf-8 -*-
"""Banco de pruebas del directo en la nube (chunker + ritmo + latencia), sin tocar Groq salvo que se pida.

    python scripts/directo_bench.py synth --lang es --out /tmp/es.wav     # audio sintetico con `say` (macOS)
    python scripts/directo_bench.py run /tmp/es.wav --latency 1.0        # reproduce el WAV por live.run con reloj falso
    python scripts/directo_bench.py run /tmp/es.wav --real               # Groq de verdad: GROQ_API_KEY del entorno

`run` hace crecer una copia del WAV como si OBS la estuviera grabando (cabecera abierta), con un reloj falso que
avanza cuando el directo duerme, y un transcriptor falso que tarda --latency segundos por trozo. Imprime la
distribucion de tamanos de trozo, cuanto silencio se ha mandado, la latencia (fin del audio del trozo -> texto
escrito) y el ritmo de peticiones. Con --real el reloj y la grabacion van en tiempo real y las peticiones van a Groq
(gasta cupo: unas 15 peticiones por minuto de audio con voz seguida).
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_app"))

import engines  # noqa: E402
import live  # noqa: E402

SR = live.SR

# Frases neutras de una reunion de trabajo (sin nombres de personas ni empresas).
SENTENCES = {
    "es": [
        "Buenos días a todos, gracias por conectaros.",
        "Empezamos con el repaso de la semana pasada y después vemos los riesgos del plan.",
        "El primer punto es el calendario de entregas del segundo trimestre.",
        "Sí.",
        "La propuesta es mover la fase de pruebas dos semanas, porque el equipo de datos todavía no tiene el entorno.",
        "Vale, ¿y eso afecta a la fecha de salida?",
        "No debería, siempre que la integración con el proveedor de pagos esté cerrada antes de mayo. Lo hemos "
        "revisado con ellos esta mañana y el contrato ya está firmado, así que el único punto abierto es la migración "
        "de los datos históricos, que depende de la limpieza de las tablas antiguas y de que el equipo de seguridad "
        "apruebe el acceso.",
        "Perfecto. Lo apunto como decisión.",
        "El segundo punto es el presupuesto: hay una desviación del ocho por ciento en licencias.",
        "¿Podemos verlo con detalle el jueves?",
        "Claro, mando la hoja esta tarde.",
        "Siguiente tema: la incidencia del lunes. Estuvo caído el acceso desde las nueve hasta las once y media.",
        "Ya está resuelta y tenemos un informe con la causa raíz; la pongo en el acta.",
        "Muy bien. Si no hay nada más, cerramos.",
        "Gracias, hasta el jueves.",
    ],
    "pt": [
        "Bom dia a todos, obrigada por estarem aqui.",
        "Vamos começar pela revisão da semana passada e depois vemos os riscos do plano.",
        "O primeiro ponto é o calendário de entregas do segundo trimestre.",
        "Sim.",
        "A proposta é adiar a fase de testes duas semanas, porque a equipa de dados ainda não tem o ambiente pronto.",
        "Está bem, e isso afeta a data de lançamento?",
        "Não deveria, desde que a integração com o fornecedor de pagamentos fique fechada antes de maio. Revimos isso "
        "com eles esta manhã e o contrato já está assinado, por isso o único ponto em aberto é a migração dos dados "
        "históricos, que depende da limpeza das tabelas antigas e da aprovação da equipa de segurança.",
        "Perfeito. Fica registado como decisão.",
        "O segundo ponto é o orçamento: há um desvio de oito por cento nas licenças.",
        "Podemos ver isso com detalhe na quinta-feira?",
        "Claro, envio a folha esta tarde.",
        "Próximo tema: o incidente de segunda-feira. O acesso esteve em baixo das nove às onze e meia.",
        "Já está resolvido e temos um relatório com a causa raiz; coloco na ata.",
        "Muito bem. Se não houver mais nada, terminamos.",
        "Obrigada, até quinta.",
    ],
}
VOICES = {"es": ["Mónica", "Paulina", "Eddy (Español (España))"],
          "pt": ["Joana", "Luciana", "Eddy (Portugués (Brasil))"]}
PAUSES = [0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.4, 2.2]  # entre frases: desde "casi seguido" hasta un silencio incomodo


def say_voices():
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.split("  ")[0].strip() for l in out.splitlines() if l.strip()]


def synth(lang, out, seed=7):
    """WAV 16 kHz mono con las frases de `lang` dichas por `say` y pausas realistas entre ellas (stdlib wave)."""
    if not shutil.which("say"):
        sys.exit("No hay `say` (solo macOS): pasa tu propio WAV a `run`.")
    voices = say_voices()
    voice = next((v for v in VOICES[lang] if v in voices), None)
    if not voice:
        sys.exit(f"No hay voz de `say` para {lang} (buscadas: {', '.join(VOICES[lang])}). Instálala en Ajustes "
                 "del sistema → Accesibilidad → Contenido hablado, o pasa tu propio WAV a `run`.")
    rng = np.random.default_rng(seed)
    pcm, spans, t = [], [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(SENTENCES[lang]):
            f = os.path.join(tmp, f"{i}.wav")
            subprocess.run(["say", "-v", voice, "-o", f, "--data-format=LEI16@16000", s], check=True, timeout=120)
            with wave.open(f) as w:
                assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SR), "say no dio 16 kHz mono"
                data = w.readframes(w.getnframes())
            gap = float(rng.choice(PAUSES))
            pcm.append(b"\0\0" * int(gap * SR))
            t += gap
            spans.append((round(t, 2), round(t + len(data) / 2 / SR, 2)))
            pcm.append(data)
            t += len(data) / 2 / SR
        pcm.append(b"\0\0" * int(1.5 * SR))
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(pcm))
    total = sum(len(p) for p in pcm) / 2 / SR
    print(f"{out}: {total:.1f} s, {len(spans)} frases con la voz «{voice}», voz {sum(b - a for a, b in spans):.1f} s")
    return out


# ----------------------------------------------------------------------------- reproduccion por live.run

def read_wav16(path):
    with wave.open(path) as w:
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        sys.exit("El banco espera un WAV de 16 bits.")
    y = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        y = y.reshape(-1, ch).mean(axis=1)
    if rate != SR:  # remuestreo lineal: vale para el banco
        x = np.arange(0, len(y), rate / SR)
        y = np.interp(x, np.arange(len(y)), y).astype(np.float32)
    return y


def wav_header(data_size, open_header):
    n = 0xFFFFFFFF if open_header else data_size
    riff = 0xFFFFFFFF if open_header else 36 + data_size
    return (b"RIFF" + struct.pack("<I", riff) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
            + b"data" + struct.pack("<I", n))


class Recorder:
    """La grabacion que crece: con reloj falso avanza cuando el directo duerme; con --real, un hilo escribe en
    tiempo real. La cabecera queda abierta hasta el ultimo byte."""

    def __init__(self, path, y, real=False, lead=0.6):
        self.path, self.real = path, real
        self.pcm = (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()
        self.t0 = time.time() if real else 1000.0
        self.t = self.t0
        self.written, self.closed, self.lock = 0, False, threading.Lock()
        with open(path, "wb") as f:
            f.write(wav_header(0, True))
        if real:
            threading.Thread(target=self._thread, daemon=True).start()
        else:
            self.sleep(lead)

    def now(self):
        return time.time() if self.real else self.t

    def _grow(self):
        want = min(len(self.pcm), int((self.now() - self.t0) * SR) * 2)
        with self.lock:
            if want > self.written:
                with open(self.path, "ab") as f:
                    f.write(self.pcm[self.written:want])
                self.written = want
            if self.written >= len(self.pcm) and not self.closed:
                self.closed = True
                with open(self.path, "r+b") as f:
                    f.write(wav_header(len(self.pcm), False))

    def _thread(self):
        while not self.closed:
            self._grow()
            time.sleep(0.1)

    def sleep(self, dt):  # reloj falso
        self.t += dt
        self._grow()


class FakeCloud:
    def __init__(self, rec, latency):
        self.rec, self.latency, self.calls = rec, latency, []
        self.keys = engines.KeyPool()
        self.keys.clock = rec.now

    def transcribe_array(self, cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, context=None,
                         model=None):
        secs = len(audio) / SR
        self.calls.append({"secs": secs, "at": self.rec.now(), "voice": None})
        self.rec.sleep(self.latency)
        return [(0.0, secs, f"trozo {len(self.calls)} ({secs:.1f} s)")], "spanish"


class Job:
    def __init__(self):
        self.id, self.preset, self.cancel, self.restart = "bench", "nube", False, False
        self.recording, self.recorded, self.transcribed, self.rev, self.progress = False, 0.0, 0.0, 0, 0.0
        self.stage, self.duration, self.edits = "", None, {}
        self.translate, self.translation, self.directo = False, [], None


def q(xs, p):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def fmt(x, d=1):
    return ("—" if x != x else f"{x:.{d}f}").replace(".", ",")


def run(path, latency, real, remaining, pace, verbose):
    y = read_wav16(path)
    vad_name = "Silero (faster-whisper)"
    try:
        full_ts = live.speech(y)
    except ImportError:
        vad_name = "energía (sin faster-whisper)"
        live.speech = energy_vad
        full_ts = live.speech(y)
    voice_total = sum(s["end"] - s["start"] for s in full_ts) / SR
    tmp = tempfile.mkdtemp(prefix="directo-bench-")
    grow = os.path.join(tmp, "grabacion.wav")
    rec = Recorder(grow, y, real=real)
    job = Job()
    if real:
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            sys.exit("--real necesita GROQ_API_KEY en el entorno (no se lee ningún .env).")
        cloud = engines.CloudEngine(tmp, log=lambda m: print(m))
        cfg = {"provider": "groq", "api_key": key, "model": "whisper-large-v3"}
        calls = []
        orig = cloud.transcribe_array

        def spy(cfg_, audio, *a, **k):
            calls.append({"secs": len(audio) / SR, "at": time.time()})
            return orig(cfg_, audio, *a, **k)
        cloud.transcribe_array = spy
        cloud.calls = calls
    else:
        cloud = FakeCloud(rec, latency)
        cfg = {"provider": "groq", "api_key": "clave-de-mentira"}
        if remaining is not None:
            cloud.keys.note(cfg["api_key"], "whisper", 200,
                            {"x-ratelimit-remaining-requests": str(remaining), "x-ratelimit-reset-requests": "6h"})
    if pace:
        engines.LIVE_PACES = ((0, tuple(pace)),)
    opts = {"preset": "nube", "language": None, "prompt": None, "threads": 1, "worker": None, "cloud": cloud,
            "cloud_cfg": cfg, "tmp_dir": tmp, "resume": []}
    logs, writes = [], []
    fake_time = None if real else types.SimpleNamespace(time=rec.now, sleep=rec.sleep)
    saved = live.time
    if fake_time:
        live.time = fake_time
    t_start = rec.now()
    try:
        segs, lang = live.run(job, grow, opts, lambda s: writes.append(len(s)), logs.append)
    finally:
        live.time = saved
        shutil.rmtree(tmp, ignore_errors=True)
    calls = cloud.calls
    secs = [c["secs"] for c in calls]
    forced = sum(1 for l in logs if "trozo de" in l and " 6.0 s" in l)
    lat = job.directo["latency"] if job.directo else {"n": 0}
    lats = [float(l.split("texto a los ")[1].split(" s")[0]) for l in logs if "texto a los " in l]
    total = len(y) / SR
    print(f"Audio: {os.path.basename(path)} · {fmt(total)} s · voz {fmt(voice_total)} s "
          f"({len(full_ts)} tramos, VAD {vad_name})")
    print(f"Chunker: ventana {pace[0] if pace else live.DIRECT_PACE[0]}–{pace[1] if pace else live.DIRECT_PACE[1]} s · "
          f"solape {live.DIRECT_OVERLAP_S} s · {'Groq real' if real else f'API falsa de {fmt(latency)} s por trozo'}"
          + (f" · cupo simulado: quedan {remaining}" if remaining is not None else ""))
    print(f"Peticiones: {len(calls)} · audio enviado {fmt(sum(secs))} s "
          f"({fmt(100 * sum(secs) / max(voice_total, 1e-9), 0)} % de la voz; silencio enviado ≈ "
          f"{fmt(max(0.0, sum(secs) - voice_total))} s) · frases escritas {len(segs)} · escrituras {len(writes)}")
    if secs:
        print(f"Tamaño de trozo: min {fmt(min(secs))} · p50 {fmt(q(secs, .5))} · p95 {fmt(q(secs, .95))} · "
              f"max {fmt(max(secs))} s · cortes forzados {forced}")
    if lats:
        print(f"Latencia (fin del audio del trozo → texto escrito): p50 {fmt(q(lats, .5))} s · "
              f"p95 {fmt(q(lats, .95))} s · max {fmt(max(lats))} s · n {len(lats)} "
              f"(estado del trabajo: p50 {fmt(lat.get('p50') or float('nan'))} s)")
    if calls and len(calls) > 1:
        span = calls[-1]["at"] - calls[0]["at"]
        per_h = 3600 * (len(calls) - 1) / span if span > 0 else float("nan")
        print(f"Ritmo: {len(calls)} peticiones en {fmt(rec.now() - t_start)} s de reunión → "
              f"~{fmt(per_h, 0)} peticiones/h de voz seguida → 2.000 al día duran ~{fmt(2000 / per_h, 1)} h "
              "a este ritmo (antes de que la política de cupo alargue la ventana)")
    if verbose:
        for i, c in enumerate(calls, 1):
            print(f"  {i:3d}. t={fmt(c['at'] - t_start)} s  trozo {fmt(c['secs'])} s")
        for l in logs:
            print("  " + l.strip())
    return 0


def energy_vad(buf, hop=320, thresh=0.01, gap_frames=15):
    n = len(buf) // hop
    if not n:
        return []
    rms = np.sqrt((buf[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    on = rms > thresh
    out, i = [], 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if out and i - out[-1]["end"] // hop < gap_frames:
                out[-1]["end"] = j * hop
            else:
                out.append({"start": i * hop, "end": j * hop})
            i = j
        else:
            i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synth", help="audio sintetico con say")
    s.add_argument("--lang", choices=sorted(SENTENCES), default="es")
    s.add_argument("--out", required=True)
    s.add_argument("--seed", type=int, default=7)
    r = sub.add_parser("run", help="reproducir un WAV por el chunker del directo")
    r.add_argument("wav")
    r.add_argument("--latency", type=float, default=1.0, help="segundos que tarda la API falsa por trozo (1,0)")
    r.add_argument("--remaining", type=int, help="simular que a la clave le quedan N peticiones hoy")
    r.add_argument("--pace", help="forzar la ventana, p. ej. 6,10")
    r.add_argument("--real", action="store_true", help="Groq de verdad con GROQ_API_KEY del entorno (tiempo real)")
    r.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    if a.cmd == "synth":
        synth(a.lang, a.out, a.seed)
        return 0
    pace = tuple(int(x) for x in a.pace.split(",")) if a.pace else None
    return run(a.wav, a.latency, a.real, a.remaining, pace, a.verbose)


if __name__ == "__main__":
    sys.exit(main())
