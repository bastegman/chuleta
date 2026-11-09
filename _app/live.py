# -*- coding: utf-8 -*-
"""
Transcripcion en directo de una grabacion de OBS que todavia se esta escribiendo.

OBS deja los tamanos de la cabecera WAV a 0xFFFFFFFF mientras graba y los corrige al parar.
Mientras tanto, cada pocos segundos se lee lo nuevo, se pasa a 16 kHz mono, se corta en una
pausa (VAD) y se transcribe ese trozo. El .txt/.srt junto al audio se reescribe tras cada trozo,
asi que en la app se va viendo el texto casi al momento.

En la nube (Groq) el directo va en trozos de 3-6 s cortados en la primera pausa, solo con la voz
(sin los silencios de los extremos) y al ritmo que permite el cupo gratuito del dia: con menos
peticiones restantes, trozos mas largos; con muy pocas, el directo se pausa (la grabacion sigue y
lo que falte se transcribe despues en trozos grandes, mas el repaso final). Si la API va mas lenta que
la reunion (un cupo por minuto bajo, la red), lo pendiente se junta en peticiones mas grandes hasta
ponerse al dia, nunca de mas de CATCHUP_MAX_S (por debajo de los 25 MB por archivo de las APIs).
"""
import os
import struct
import threading
import time
from time import perf_counter  # aparte de `time`: los tests sustituyen live.time por un reloj falso

import numpy as np

import directo_avisos
import engines
from common import log

SR = 16000
OPEN = 0xFFFFFFFF
STALLED_S = 45          # sin crecer este tiempo y con la cabecera abierta = OBS se cerro sin finalizar
LIVE_MIN_S = 20         # en directo en local: trozos de al menos ~20 s (cortados en una pausa)
LIVE_MAX_S = 45
CLOUD_MIN_S = 8         # (historico) ventana de la nube antes del chunker del directo; la usa choose_cut(cloud=True)
CLOUD_MAX_S = 15
POLL_S = 0.5            # cada cuanto se mira si la grabacion ha crecido
CONTEXT_SEGS = 3        # frases anteriores que se le pasan a Whisper para enlazar con el trozo nuevo
BACKLOG_MIN_S = 120     # poniendose al dia (grabacion ya empezada): trozos grandes, mas eficientes
BACKLOG_MAX_S = 300

# --- directo en la nube: ventanas cortas, solo voz, al ritmo del cupo
DIRECT_PACE = (3, 6)        # ventana objetivo (min, max) en segundos; el KeyPool la alarga si queda poco cupo
DIRECT_OVERLAP_S = 0.5      # corte forzado (nadie calla en 6 s): la ventana siguiente repite 0,5 s y se deduplica
DIRECT_PAD_S = 0.2          # margen alrededor de la voz al recortar los silencios de los extremos
DIRECT_FUSE_X = 2           # con retraso acumulado, los trozos pendientes se fusionan hasta 2 x la ventana maxima
# ...y con mas retraso que eso (la API va mas lenta que la reunion) se junta todo lo pendiente en una peticion, hasta
# 10 min: un WAV de 16 kHz mono de 16 bit son 32 KB/s, 19,2 MB en 10 min, por debajo de los 25 MB por archivo de las
# APIs de transcripcion. Es tambien el tope del ultimo trozo al terminar (antes se mandaba todo el retraso de golpe)
CATCHUP_MAX_S = 600
DIRECT_MIN_VOICE_S = 1.0    # menos voz que esto en un trozo: ruido o un golpe; no se manda (Whisper inventaria)
TRAIL_SILENCE_S = 0.5       # silencio al final del buffer que ya cuenta como pausa
TRANSLATE_EVERY_S = 12      # traduccion del directo: cada 10-15 s, lo ultimo que se dijo
TRANSLATE_MAX_CHARS = 1500  # por peticion (~500 tokens: cabe de sobra en 8.000 tokens/min)
TRANSLATE_BACK_S = 30       # solo lo dicho en los ultimos 30 s: al activarla a mitad de reunion (o si se queda atras)
TRANSLATE_FINAL_S = 10      # al terminar el directo, el ultimo paso: como mucho 10 s EN TOTAL (esperas y peticion)
TRANSLATE_LOCK_S = 2        # ...de ellos, como mucho 2 s esperando a que el hilo de la traduccion acabe la suya
TRANSLATE_FINAL_ATTEMPTS = 3  # ...e intentos dentro del plazo (otra clave tras un 401, max_tokens tras un 400)
TRANSLATION_PUBLIC = 60     # ultimos bloques traducidos que viajan en el estado del trabajo
TRANSLATE_TO = ("es", "en")  # destinos de la traduccion del directo (msg 1152): espanol o ingles

# --- cambiar el idioma en pleno directo (msgs 1150-1151: «funcionó, pero tardó»)
REDO_BACK_S = 60            # se rehace en el idioma nuevo lo escrito en el ultimo minuto (desde una frase entera)
SWITCH_BEHIND_CLOUD_S = 30  # con mas retraso que esto (nube / local) el aviso dice «va poniéndose al día»
SWITCH_BEHIND_LOCAL_S = 90
SWITCH_RELOAD_S = 5         # en local, cancelar el trozo en vuelo es matar el proceso de Whisper: recargar medium ~5 s


def seg_key(start):
    """Una frase por su inicio, como la identifican las correcciones a mano del directo (server.seg_key)."""
    return round(int(round(float(start) * 1000)) / 1000, 2)


def redo_start(segs, edits=None, back_s=REDO_BACK_S):
    """Desde que frase se rehace lo ya escrito al cambiar el idioma: la primera que empieza en los ultimos back_s
    segundos del texto, pero nunca una corregida a mano ni las anteriores a ella (la correccion es sobre ese texto y se
    perderia). Devuelve el indice (len(segs): nada que rehacer)."""
    if not segs:
        return 0
    since = segs[-1][1] - back_s
    k = next((i for i, s in enumerate(segs) if s[0] >= since - 1e-6), len(segs))
    if edits:
        for i in range(len(segs) - 1, k - 1, -1):
            if seg_key(segs[i][0]) in edits:
                return i + 1
    return k


def redo_from(segs, k):
    """Segundo desde el que se relee el audio para rehacer segs[k:]: el inicio de esa frase o, si la anterior acaba
    antes (un hueco), el final de la anterior, para no perder el arranque de una palabra que Whisper situo tarde."""
    if k <= 0:
        return float(segs[0][0]) if segs else 0.0
    return float(min(segs[k][0], segs[k - 1][1]))


def back_of(removed, end_s, eps=0.05):
    """Si el trozo que rehacia lo ultimo no sale: las frases de antes que vuelven. Solo las que acaban antes del corte
    (end_s): la que lo cruza la transcribe otra vez el trozo siguiente, que empieza ahi, y volveria repetida."""
    return [s for s in removed if s[1] <= end_s + eps]


def _round_eta(s):
    s = max(1.0, float(s))
    return int(-(-s // 1)) if s < 10 else int(5 * round(s / 5))


def lang_switch_info(d, segs, edits, new, old):
    """Lo que dice el aviso al cambiar el idioma de una reunion en pleno directo (Jobs.refresh_prompt). d: el estado
    que publica run() (job.directo); segs: lo escrito (job.raw); edits: las correcciones a mano; new/old: el idioma
    («»/None = detectar) despues y antes. Devuelve {sin_cambio} o {idioma, estado, eta_s, rehace_s, detras_s}:
      - estado «al_dia»: el trozo en vuelo se cancela y se repite; eta_s, cuando sale el texto nuevo (nube: dos idas a
        la API; local: lo que tarda Whisper en lo que se rehace, mas recargarse si estaba en marcha);
      - «poniendose»: el directo va por detras de la grabacion (detras_s); el idioma vale ya, pero se vera al
        alcanzarla;
      - «pausa»: la nube esta en pausa por cupo; se aplicara al reanudarse o al terminar.
    rehace_s: segundos de texto que se rehacen en el idioma nuevo (0 si ya estaba en ese idioma o no hay texto)."""
    new, old, d = new or None, old or None, d or {}
    if new == old:
        return {"sin_cambio": True}
    segs = list(segs or [])
    k = len(segs) if new is not None and new == d.get("lang") else redo_start(segs, edits)
    k = max(k, int(d.get("protegidas") or 0))  # lo retomado de una sesion anterior no se rehace (run)
    rehace = segs[-1][1] - segs[k][0] if k < len(segs) else 0.0
    cloud, behind = bool(d.get("cloud")), max(0.0, float(d.get("behind_s") or 0.0))
    if d.get("paused"):
        estado = "pausa"
    elif d.get("caught_up") is False or behind > (SWITCH_BEHIND_CLOUD_S if cloud else SWITCH_BEHIND_LOCAL_S):
        estado = "poniendose"
    else:
        estado = "al_dia"
    eta = None
    if estado == "al_dia":
        if cloud:
            net = (((d.get("latency") or {}).get("phases") or {}).get("net") or {}).get("p50")
            eta = 1.0 + 2 * (net if net else 1.5)
        else:
            eta = (rehace + behind) / max(0.1, float(d.get("speed") or 1.0)) + SWITCH_RELOAD_S
        eta = _round_eta(eta)
    return {"idioma": new or "", "estado": estado, "eta_s": eta, "rehace_s": int(round(rehace)),
            "detras_s": int(round(behind))}


def wav_info(path):
    """Cabecera WAV. data_size es None mientras la grabacion sigue abierta."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    riff = struct.unpack("<I", head[4:8])[0]
    off, info = 12, {}
    while off + 8 <= len(head):
        cid, n = head[off:off + 4], struct.unpack("<I", head[off + 4:off + 8])[0]
        if cid == b"fmt " and off + 24 <= len(head):
            tag, ch, rate, br, ba, bits = struct.unpack("<HHIIHH", head[off + 8:off + 24])
            if tag == 0xFFFE and off + 34 <= len(head):  # WAVE_FORMAT_EXTENSIBLE
                tag = struct.unpack("<H", head[off + 32:off + 34])[0]
            info.update(tag=tag, channels=ch, rate=rate, byte_rate=br, block_align=ba, bits=bits)
        elif cid == b"data":
            if "rate" not in info or not info["byte_rate"]:
                return None
            info["data_off"] = off + 8
            info["data_size"] = None if n in (OPEN, 0) or riff == OPEN else n
            return info
        if n == OPEN:
            return None
        off += 8 + n + (n & 1)
    return None


def to_mono(raw, info):
    bits, tag, ch = info["bits"], info["tag"], info["channels"]
    if tag == 3 and bits == 32:
        x = np.frombuffer(raw, "<f4").astype(np.float32)
    elif bits == 16:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif bits == 32:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    elif bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        x = (((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8) >> 8).astype(np.float32) / 8388608.0
    else:
        raise engines.EngineError(f"Formato WAV no soportado ({bits} bits)")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x


class Resampler:
    """Remuestreo continuo (conserva el estado entre bloques: sin cortes audibles)."""

    def __init__(self, rate):
        self.rate, self.r = rate, None
        if rate != SR:
            import av
            self.av = av
            self.r = av.AudioResampler(format="flt", layout="mono", rate=SR)

    def _out(self, frames):
        parts = [f.to_ndarray().reshape(-1) for f in frames]
        return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)

    def __call__(self, x):
        if not self.r or not len(x):
            return x.astype(np.float32)
        fr = self.av.AudioFrame.from_ndarray(np.ascontiguousarray(x, np.float32).reshape(1, -1),
                                             format="flt", layout="mono")
        fr.sample_rate = self.rate
        return self._out(self.r.resample(fr))

    def flush(self):
        return self._out(self.r.resample(None)) if self.r else np.zeros(0, np.float32)


_VAD = {"fn": None, "retry_at": 0.0, "warned": False}
VAD_RETRY_S = 600  # si la VAD de Whisper no se pudo importar, se vuelve a intentar pasado este tiempo


def simple_speech(buf, frame_s=0.03, min_gap_s=0.3):
    """Detector sencillo de voz por energia, en el formato de get_speech_timestamps ([{"start", "end"}] en
    muestras): tramos cuyo RMS por tramo de 30 ms supera el 30 % del nivel alto del propio bufer (percentil 90), con
    los huecos de menos de 300 ms unidos. Vale para cortar en pausas cuando no esta la VAD de Whisper."""
    n = int(SR * frame_s)
    if n <= 0 or len(buf) < n:
        return [{"start": 0, "end": len(buf)}] if len(buf) else []
    m = len(buf) // n
    rms = np.sqrt(np.mean(np.asarray(buf[:m * n], np.float32).reshape(m, n) ** 2, axis=1))
    th = max(0.005, 0.3 * float(np.percentile(rms, 90)))
    out, start = [], None
    for i, loud in enumerate(rms > th):
        if loud and start is None:
            start = i
        elif not loud and start is not None:
            out.append({"start": start * n, "end": i * n})
            start = None
    if start is not None:
        out.append({"start": start * n, "end": len(buf)})
    merged = []
    for seg in out:
        if merged and seg["start"] - merged[-1]["end"] < min_gap_s * SR:
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(dict(seg))
    return merged


def speech(buf):
    """Tramos con voz de `buf` (muestras a SR). La VAD de Whisper si se puede importar; si no (no instalada, o la
    importacion falla o la pisa otro hilo: el 02/11/2026 salio un KeyError de importlib y tumbo el directo), el
    detector sencillo, avisando una vez y reintentando la importacion mas tarde. Se importa aqui, no al cargar el
    modulo, para no retrasar el arranque; nunca desde un hilo que atienda a la interfaz."""
    fn = _VAD["fn"]
    if fn is None or (fn is simple_speech and time.time() >= _VAD["retry_at"]):
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps

            def fn(b):
                return get_speech_timestamps(b, VadOptions(min_silence_duration_ms=300))
            _VAD["warned"] = False
        except Exception as e:
            fn = simple_speech
            _VAD["retry_at"] = time.time() + VAD_RETRY_S
            if not _VAD["warned"]:
                _VAD["warned"] = True
                log(f"   la VAD de Whisper no está disponible ({e.__class__.__name__}: {e}): el directo corta las "
                    "pausas con el detector sencillo")
        _VAD["fn"] = fn
    return fn(buf)


def choose_cut(buf, final, catching_up, cloud=False):
    """Muestras a transcribir ahora (0 = esperar a tener mas). Siempre corta en una pausa.
    (Directo en local y "ponerse al dia"; el directo en la nube usa direct_cut.)"""
    n = len(buf)
    if final:
        return n, None
    lo, hi = ((BACKLOG_MIN_S, BACKLOG_MAX_S) if catching_up
              else (CLOUD_MIN_S, CLOUD_MAX_S) if cloud else (LIVE_MIN_S, LIVE_MAX_S))
    if n < lo * SR:
        return 0, None
    ts = speech(buf)
    if not ts:
        return n, ts  # todo silencio: se descarta sin llamar a Whisper
    pauses = [(a["end"] + b["start"]) // 2 for a, b in zip(ts, ts[1:])]
    if n - ts[-1]["end"] > SR // 2:
        pauses.append((ts[-1]["end"] + n) // 2)
    pauses = [p for p in pauses if p >= 0.6 * lo * SR]
    if pauses:
        return max(pauses), ts
    if n >= hi * SR:  # nadie ha parado de hablar en 45 s: se corta igualmente
        return n - SR, ts
    return 0, None


# ----------------------------------------------------------------------------- chunker del directo en la nube

def pauses_of(ts, n):
    """Puntos de corte (muestras) del buffer: el centro de cada pausa entre dos tramos de voz y, si el buffer
    acaba en >= 0,5 s de silencio, el centro de esa cola."""
    cuts = [(a["end"] + b["start"]) // 2 for a, b in zip(ts, ts[1:])]
    if ts and n - ts[-1]["end"] > TRAIL_SILENCE_S * SR:
        cuts.append((ts[-1]["end"] + n) // 2)
    return cuts


def behind(buf, pace=DIRECT_PACE):
    """El directo va con retraso: lo leido y aun sin transcribir pasa de 2 x la ventana maxima (la API, o el cupo
    por minuto, va mas lenta que la reunion). Entonces direct_cut junta todo lo pendiente (hasta CATCHUP_MAX_S)."""
    return len(buf) > DIRECT_FUSE_X * pace[1] * SR


def direct_cut(buf, ts, pace=DIRECT_PACE, catch_up=False):
    """Donde cortar el directo en la nube. Devuelve (cut, overlap) en muestras; cut = 0 es "esperar".

    La ventana se mide desde el primer tramo de voz (el silencio inicial se recorta luego y no cuenta):
      - con una pausa a >= min_s de ahi, se corta en la ULTIMA pausa que no pase de 2 x max_s (si el bucle
        venia con retraso y hay varios trozos pendientes, se fusionan en una peticion); con catch_up (mas retraso
        que eso: behind), en la ultima que no pase de CATCHUP_MAX_S, para ponerse al dia;
      - sin pausa y con >= max_s de audio desde la voz, corte forzado en max_s (con catch_up, en lo que haya, hasta
        CATCHUP_MAX_S) con 0,5 s de solape (la ventana siguiente empieza 0,5 s antes y dedupe_overlap quita lo que
        acabe dentro del solape);
      - sin voz (ts vacio), todo el buffer se descarta sin mandarlo: (n, 0).
    Nunca devuelve un trozo de mas de CATCHUP_MAX_S desde la voz: el audio que se manda no pasa de ~19 MB."""
    n = len(buf)
    if not ts:
        return n, 0
    lo, hi = pace
    base = ts[0]["start"]
    limit = (CATCHUP_MAX_S if catch_up else DIRECT_FUSE_X * hi) * SR
    good = [p for p in pauses_of(ts, n) if p - base >= lo * SR]
    if good:
        near = [p for p in good if p - base <= limit]
        cut = near[-1] if near else good[0]
        if cut - base <= CATCHUP_MAX_S * SR:
            return cut, 0
    if n - base >= hi * SR:
        span = min(n - base, CATCHUP_MAX_S * SR) if catch_up else int(hi * SR)
        return base + int(span), int(DIRECT_OVERLAP_S * SR)
    return 0, 0


def capped_cut(buf, ts, limit_s=CATCHUP_MAX_S):
    """Ultimo trozo al terminar, en la nube: lo que queda, pero nunca mas de limit_s (el bucle sigue con el resto).
    ts: la VAD de buf[:limit_s]. Se corta en la ultima pausa de la segunda mitad; si no la hay, en el tope con el
    solape de siempre. Devuelve (cut, overlap) en muestras."""
    n, lim = len(buf), int(limit_s * SR)
    if n <= lim:
        return n, 0
    cuts = [p for p in pauses_of(ts or [], lim) if lim // 2 <= p <= lim]
    if cuts:
        return cuts[-1], 0
    return lim, int(DIRECT_OVERLAP_S * SR)


def trim_voice(window, ts, pad=DIRECT_PAD_S):
    """Solo la voz de la ventana, con un margen: (audio, desplazamiento en muestras, segundos de voz)."""
    cut = len(window)
    inside = [(s["start"], min(s["end"], cut)) for s in ts if s["start"] < cut]
    if not inside:
        return window[:0], 0, 0.0
    a = max(0, inside[0][0] - int(pad * SR))
    b = min(cut, inside[-1][1] + int(pad * SR))
    return window[a:b], a, sum(e - s for s, e in inside) / SR


def dedupe_overlap(segs, until, eps=0.05):
    """Tras un corte forzado con solape: las frases que ACABAN dentro del solape ya salieron en la ventana
    anterior y se quitan; la que cruza el limite se queda (es casi toda nueva)."""
    if until is None:
        return list(segs)
    return [s for s in segs if s[1] > until + eps]


def stitch(prev, new, dedupe_until, language):
    """Cose el trozo nuevo con el anterior: dedupe por tiempos (solape) + el filtro de ecos de siempre."""
    new = dedupe_overlap(new, dedupe_until)
    return engines.clean_segments(prev + new, None, language)[len(prev):]


PHASES = ("prep", "wait", "net", "infer", "paint")  # por trozo: preparacion, espera por cupo, red, inferencia, pintado
PHASE_LABELS = {"prep": "preparación", "wait": "cupo", "net": "red", "infer": "inferencia", "paint": "pintado"}


def fmt_phases(p):
    """' · preparación 0,05 s · red 1,10 s (inferencia 0,80 s) · pintado 0,01 s' para el log (solo lo que se midio)."""
    out = []
    for k in PHASES:
        v = (p or {}).get(k)
        if v is None or k == "infer":
            continue
        s = f"{PHASE_LABELS[k]} {v:.2f} s"
        if k == "net" and p.get("infer") is not None:
            s += f" (inferencia {p['infer']:.2f} s)"
        out.append(s)
    return "".join(" · " + s for s in out)


class Latency:
    """Latencia del directo: del final del audio de cada trozo (cuando ya estaba en el fichero) al texto escrito.
    marks: (segundos grabados, hora de pared en que el fichero llegaba hasta ahi), una por lectura.
    Ademas, por fases (PHASES), para saber si el cuello esta en la red/Groq o en este lado."""

    def __init__(self, keep=400):
        self.marks, self.samples, self.keep = [], [], keep
        self.phases = {k: [] for k in PHASES}

    def seen(self, recorded_s, now):
        if not self.marks or recorded_s > self.marks[-1][0]:
            self.marks.append((recorded_s, now))
            if len(self.marks) > 4 * self.keep:
                del self.marks[:-2 * self.keep]

    def wall_of(self, rec_s):
        for r, w in self.marks:
            if r >= rec_s - 1e-6:
                return w
        return None

    def add(self, rec_end_s, now, phases=None):
        w = self.wall_of(rec_end_s)
        if w is None:
            return None
        lat = max(0.0, now - w)
        self.samples.append(lat)
        if len(self.samples) > self.keep:
            del self.samples[:-self.keep]
        for k in PHASES:
            v = (phases or {}).get(k)
            if v is not None:
                xs = self.phases[k]
                xs.append(float(v))
                if len(xs) > self.keep:
                    del xs[:-self.keep]
        return lat

    @staticmethod
    def _q(xs, p):
        s = sorted(xs)
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]

    def stats(self):
        if not self.samples:
            return {"n": 0, "p50": None, "p95": None, "last": None, "phases": {}}
        ph = {k: {"p50": round(self._q(xs, 0.5), 2), "p95": round(self._q(xs, 0.95), 2)}
              for k, xs in self.phases.items() if xs}
        return {"n": len(self.samples), "p50": round(self._q(self.samples, 0.5), 2),
                "p95": round(self._q(self.samples, 0.95), 2), "last": round(self.samples[-1], 2), "phases": ph}


def fmt_wait(secs):
    secs = int(max(0, secs or 0))
    if secs >= 3600:
        return f"{secs // 3600} h {secs % 3600 // 60:02d} min"
    return f"{secs // 60} min" if secs >= 120 else f"{secs} s"


def no_key_message(why):
    """Texto del estado cuando el directo en la nube no puede mandar nada (p. ej. se ha quitado la API key)."""
    return (f"Directo en pausa: {why.rstrip('.')}. La grabación sigue; en cuanto vuelva a haber clave se retoma "
            "y lo que falte se transcribe al terminar.")


def pause_message(q):
    """Texto del estado cuando el directo se pausa por falta de cupo."""
    if q.get("blocked_for"):
        head = f"Groq pide esperar {fmt_wait(q['blocked_for'])}"
    elif q.get("remaining") is not None:
        head = f"quedan {q['remaining']} peticiones de Groq hoy"
        if q.get("reset_in"):
            head += f" (se renuevan en {fmt_wait(q['reset_in'])})"
    else:
        head = "sin cupo en Groq"
    return (f"Directo en pausa: {head}. La grabación sigue; lo que falte se transcribe al terminar "
            "y el repaso final se hace igual.")


def translate_to(job):
    """Destino de la traduccion del directo de ese trabajo: «es» o «en» (job.translate_to; sin el, espanol)."""
    to = getattr(job, "translate_to", None)
    return to if to in TRANSLATE_TO else "es"


class LiveTranslator:
    """Hilo que traduce lo ultimo que se ha transcrito, cada TRANSLATE_EVERY_S, mientras job.translate, al idioma de
    job.translate_to (espanol o ingles, msg 1152). get_segs() devuelve (generacion, lista de frases); si la generacion
    cambia (rehacer desde el principio) o cambia el destino, empieza de cero. Lo traducido se acumula en
    job.translation (en memoria: es una ayuda para seguir la reunion). Solo se traduce lo dicho en los ultimos
    TRANSLATE_BACK_S: activada a mitad de reunion empieza por ahi, no por el minuto 0, y si se queda atras (un trozo
    grande al ponerse al dia) salta a lo ultimo. Al cambiar el idioma en pleno directo, rewind() quita lo traducido de
    las frases que se rehacen.

    epoch cuenta esos reinicios: una traduccion que estaba en vuelo cuando cambio (sobre frases que ya no estan, o a
    otro idioma) no se publica. done, epoch, to y job.translation van con `state` (un candado corto: el bucle del
    directo rebobina sin esperar a la peticion en vuelo); `lock` es el de una pasada entera (step)."""

    def __init__(self, job, opts, get_segs, log, every=TRANSLATE_EVERY_S):
        self.job, self.opts, self.get_segs, self.log, self.every = job, opts, get_segs, log, every
        self.done, self.gen, self.stop_flag, self.error, self.busy = 0, None, False, None, False
        self.epoch, self.to = 0, None
        self.stop_at = 0.0  # cuando se llamo a stop()
        self.lock = threading.Lock()  # step() lo llama el hilo y, al final, el bucle del directo
        self.state = threading.Lock()
        self.after_stop = None  # lo que el hilo traduzca despues de stop() (lo que se llevo): run() lo publica igual
        self.thread = threading.Thread(target=self._loop, daemon=True, name="live-translate")

    def start(self):
        self.thread.start()

    def stop(self):
        """Fin de la sesion: el hilo no empieza nada mas. Lo que tenga en marcha (lo que se llevo) aun puede acabar
        durante TRANSLATE_FINAL_S, tambien si le toca esperar (un 429, un 503), y se publica igual (after_stop)."""
        self.stop_flag, self.stop_at = True, time.time()

    def _cancelled(self):
        """Para las peticiones: cancelar a mano corta en el acto; tras stop(), pasado TRANSLATE_FINAL_S."""
        return bool(self.job.cancel) or (self.stop_flag and time.time() - self.stop_at > TRANSLATE_FINAL_S)

    def rewind(self, kept, t):
        """Cambio de idioma en pleno directo: las frases desde la len(kept) se rehacen (empiezan en t o despues).
        Los bloques traducidos que llegan hasta ellas se quitan (se volveran a traducir con el texto nuevo) y lo
        que este en vuelo no se publica. kept: las frases que se quedan."""
        with self.state:
            self.epoch += 1
            self.job.translation[:] = [b for b in self.job.translation if b["end"] <= t + 1e-6]
            last = self.job.translation[-1]["end"] if self.job.translation else None
            covered = 0 if last is None else next((i for i, s in enumerate(kept) if s[1] > last + 1e-6), len(kept))
            self.done = min(self.done, covered)

    def pending(self):
        """Lo que toca traducir ahora (con `state`)."""
        gen, segs = self.get_segs()
        to = translate_to(self.job)
        if gen != self.gen or to != self.to:  # rehacer desde el principio, o el otro destino: de cero
            if self.to is not None or gen != self.gen:
                self.epoch += 1
            self.gen, self.to, self.done = gen, to, 0
            self.job.translation.clear()
        self.done = min(self.done, len(segs))
        if segs:  # lo anterior a los ultimos TRANSLATE_BACK_S ya no ayuda a seguir la reunion: se salta
            since = segs[-1][1] - TRANSLATE_BACK_S
            while self.done < len(segs) and segs[self.done][1] < since:
                self.done += 1
        segs = segs[self.done:]
        take, chars = [], 0
        for s in segs:
            if take and chars + len(s[2]) > TRANSLATE_MAX_CHARS:
                break
            take.append(s)
            chars += len(s[2]) + 1
        return take

    def step(self, cancelled=None, final=False):
        """Una pasada: traduce lo pendiente (si hay). Devuelve True si tradujo algo. final: el ultimo paso al terminar
        el directo, que no puede retener el final de la sesion: como mucho TRANSLATE_FINAL_S en total. Si el hilo esta
        a mitad de una peticion se le espera como mucho TRANSLATE_LOCK_S (lo que se llevo lo traduce el y se publica
        aunque llegue tarde: after_stop), y lo que quede del plazo es el de la peticion (Summarizer._chat, budget:
        una espera que no cabe no se hace, y conectar y leer tienen como tope lo que quede)."""
        t0 = time.time()
        if not self.lock.acquire(timeout=TRANSLATE_LOCK_S if final else -1):
            return False
        try:
            with self.state:
                take = self.pending()
                epoch, to = self.epoch, self.to
            if not take or not getattr(self.job, "translate", False):
                return False
            text = " ".join(t for _, _, t in take)
            self.busy = True
            kw = ({"budget": max(0.0, TRANSLATE_FINAL_S - (time.time() - t0)), "attempts": TRANSLATE_FINAL_ATTEMPTS}
                  if final else {})
            try:
                out, _ = self.opts["summarizer"].translate(self.opts["cloud_cfg"], text, lambda **k: None,
                                                           cancelled or self._cancelled, to=to, **kw)
            except engines.Cancelled:
                return False
            except engines.EngineError as e:
                self.error = str(e)
                self.log(f"   traducción del directo: {e}")
                return False
            finally:
                self.busy = False
            self.error = None
            with self.state:
                if epoch != self.epoch:  # mientras tanto cambio el idioma de la reunion o el destino: no vale
                    return False
                self.job.translation.append({"start": round(float(take[0][0]), 2),
                                             "end": round(float(take[-1][1]), 2), "text": out})
                self.done += len(take)
            if self.stop_flag and self.after_stop:  # la sesion ya termino: que se vea igual
                self.after_stop()
            return True
        finally:
            self.lock.release()

    def _loop(self):
        last = 0.0
        while not (self.stop_flag or self.job.cancel):
            # al cambiar el destino no se espera a la siguiente vuelta: lo ultimo, ya en el idioma nuevo
            otro = self.to is not None and translate_to(self.job) != self.to
            if getattr(self.job, "translate", False) and (time.time() - last >= self.every or otro):
                try:
                    did = self.step()
                except Exception as e:  # nunca tumba el directo
                    self.log(f"   traducción del directo: {e.__class__.__name__}: {e}")
                    did = False
                # con exito y mas pendiente, el siguiente en ~2 s; si no (o tras un error), al ritmo normal
                last = last + 2.0 if did and self._more() else time.time()
            time.sleep(0.25)

    def _more(self):
        with self.state:  # pending() avanza lo que se salta: con el candado, como step()
            return bool(self.pending())


def run(job, path, opts, write, log):
    """Bucle de la sesion en directo.

    job.preset puede cambiar durante la sesion (se aplica desde el siguiente trozo) y job.restart
    pide rehacerlo todo desde el principio. opts: language, prompt, about (contexto libre de la reunion),
    threads, worker (WorkerClient propio), cloud, cloud_cfg, tmp_dir, resume (segmentos ya escritos por una
    sesion anterior), summarizer (traduccion del directo). language, prompt y about se releen en cada trozo:
    si se cambian en la pagina de la reunion, valen desde el siguiente. cloud_cfg_fn (opcional): devuelve los
    ajustes de la nube vigentes y se llama en cada vuelta (una clave pegada, cambiada o quitada en Ajustes vale
    desde el siguiente trozo; sin el, cloud_cfg tal cual).

    El idioma, ademas, vale al momento (msgs 1150-1151): el trozo en vuelo se cancela (en local se mata el proceso de
    Whisper; una peticion a la nube que ya vuelve se descarta) y lo escrito en el ultimo minuto se rehace en el idioma
    nuevo (redo_start: desde una frase entera y nunca una corregida a mano). Hasta que llega el texto nuevo se sigue
    viendo el de antes; si el trozo que lo rehace falla, vuelve el de antes.

    En la nube un trozo que falla (tambien al ponerse al dia o el ultimo) no tumba la sesion: se anota en el log y
    en job.live_error, se salta y la grabacion sigue; el repaso final lo recupera al terminar."""
    info = None
    for _ in range(60):  # OBS puede tardar un instante en escribir la cabecera
        info = wav_info(path)
        if info or job.cancel:
            break
        time.sleep(1)
    if not info:
        raise engines.EngineError("No se reconoce la cabecera WAV de la grabación.")
    block, br, off = info["block_align"], info["byte_rate"], info["data_off"]
    fixed_lang = opts.get("language") or None  # si no se fija en Ajustes, se detecta en cada trozo
    lang = fixed_lang
    worker = opts["worker"]
    segs = list(opts.get("resume") or [])
    st = {"gen": 0}
    keys = getattr(opts.get("cloud"), "keys", None)
    cfg = opts.get("cloud_cfg") or {}
    latency = Latency()
    translator = None

    def fresh_cfg():
        """Los ajustes de la nube vigentes (no una copia del arranque): el trozo, la traduccion y el estado los usan."""
        fn = opts.get("cloud_cfg_fn")
        if fn is not None:
            try:
                opts["cloud_cfg"] = fn() or {}
            except Exception as e:  # nunca tumba el directo: se sigue con los ultimos que se leyeron
                log(f"   directo: no se pudieron releer los ajustes de la nube ({e.__class__.__name__})")
        return opts.get("cloud_cfg") or {}

    def reset(from_t):
        st["pos"] = off + int(from_t * br) // block * block
        st["t0"] = (st["pos"] - off) / br
        st["buf"] = np.zeros(0, np.float32)
        st["resample"] = Resampler(info["rate"])
        st["dedupe_until"] = None
        st["flushed"] = False
        job.transcribed = st["t0"]

    reset(segs[-1][1] if segs else 0.0)  # retoma donde lo dejo una sesion anterior
    st["protegidas"] = len(segs)  # lo retomado no se rehace al cambiar el idioma (switch_language)
    used = [fixed_lang]  # idiomas de la reunion en esta sesion (job.idiomas_directo)
    # «Lo ultimo suena a …» (B6): con un idioma fijo, en que idioma suenan los trozos; rechazos: los de esta grabacion
    propuesta = job.propuesta = directo_avisos.Propuesta(opts.get("rechazos"))
    last_size, last_growth = -1, time.time()

    def stage(s):
        job.stage = s

    def lang_changed():
        """El idioma de la reunion ha cambiado (Jobs.refresh_prompt actualiza opts) y aun no se ha aplicado."""
        return (opts.get("language") or None) != fixed_lang

    def lang_cuts():
        """El cambio de idioma deja sin valor el trozo en vuelo: no si el nuevo es justo el que se estaba detectando
        (con «Detectar» ya salia en ese idioma; cortarlo en local seria matar Whisper y repetir hasta 5 min de audio
        para nada)."""
        want = opts.get("language") or None
        return want != fixed_lang and not (want is not None and want == lang)

    def stop():
        """Lo que corta las peticiones de un trozo: detener el directo o cambiar el idioma (se repite con el nuevo)."""
        return bool(job.cancel) or lang_cuts()

    def switch_language():
        """Aplica el idioma nuevo ya: rebobina a lo escrito en el ultimo minuto (sin borrarlo de la pantalla hasta
        que llegue lo nuevo) y relee el audio desde ahi; el siguiente trozo lo rehace en el idioma nuevo."""
        nonlocal fixed_lang, lang
        want, before = opts.get("language") or None, lang
        fixed_lang, lang = want, (want or lang)
        propuesta.oir(want, [])  # con el idioma nuevo, lo propuesto (si lo habia) ya no vale: fuera de la franja
        if want not in used:
            used.append(want)
            job.idiomas_directo = list(used)  # el repaso final no fuerza uno solo si la reunion cambio (server)
        redo = st.get("redo")
        if redo is not None:  # dos cambios seguidos: se rehace desde el mismo sitio, sin ir mas atras
            reset(redo["from"])
            log(f"   directo: idioma {want or 'detectar'} desde ya (se sigue rehaciendo desde {redo['from']:.1f} s)")
            return
        k = len(segs) if want is not None and want == before else redo_start(segs, getattr(job, "edits", None))
        # lo de una sesion anterior (la app se reinicio a mitad) no: sus correcciones ya van en el texto, sin constar
        k = max(k, st.get("protegidas", 0))
        if k >= len(segs):  # nada que rehacer (sin texto, ya en ese idioma o todo corregido a mano)
            log(f"   directo: idioma {want or 'detectar'} desde ya")
            return
        t_from, removed = redo_from(segs, k), segs[k:]
        del segs[k:]
        st["redo"] = {"from": t_from, "removed": removed, "until": removed[-1][1]}
        if translator is not None:
            translator.rewind(list(segs), t_from)
        reset(t_from)
        log(f"   directo: idioma {want or 'detectar'} desde ya; se rehacen en él los últimos "
            f"{removed[-1][1] - removed[0][0]:.0f} s ({len(removed)} frases, desde {t_from:.1f} s)")
        stage("Cambiando el idioma: se rehace lo último…")

    def publish(pace=None, paused=False):
        """Estado del directo para la interfaz (cupo, ventana, latencia, traduccion) y para el aviso al cambiar el
        idioma (lang_switch_info: idioma del texto, retraso, si esta al dia y la velocidad del modelo local)."""
        if not hasattr(job, "translation"):
            return
        tr = list(job.translation)
        on_cloud = bool(keys) and (engines.PRESETS_BY_ID.get(job.preset) or {}).get("engine") == "cloud"
        lm = engines.CloudEngine.live_model(cfg) if on_cloud else None
        # la traduccion, con el modelo y el proveedor reales; sin servicio de chat (Azure) no se ofrece
        why = engines.chat_missing(cfg, "La traducción del directo") if on_cloud else None
        job.directo = {"cloud": on_cloud, "quota": keys.public(cfg, model=lm) if on_cloud else None,
                       "pace": list(pace) if pace else None, "paused": bool(paused), "latency": latency.stats(),
                       "translate": bool(getattr(job, "translate", False)), "translation": tr[-TRANSLATION_PUBLIC:],
                       "translate_to": translate_to(job), "translation_epoch": translator.epoch if translator else 0,
                       "translation_n": len(tr), "translate_error": translator.error if translator else None,
                       "translating": bool(translator and translator.busy),
                       "translate_ok": on_cloud and not why, "translate_why": why,
                       "translate_model": engines.Summarizer.translate_model(cfg) if on_cloud else None,
                       "provider": (engines.PROVIDERS.get(cfg.get("provider", "groq")) or {}).get("label")
                       if on_cloud else None,
                       "live_model": lm,
                       "lang": lang, "caught_up": bool(st.get("caught_up")), "redo": st.get("redo") is not None,
                       "protegidas": st.get("protegidas", 0),
                       "behind_s": round(max(0.0, (job.recorded or 0.0) - (job.transcribed or 0.0)), 1),
                       "speed": (engines.PRESETS_BY_ID.get(job.preset) or {}).get("live_speed"),
                       "propuesta": propuesta.publica()}

    def oir():
        """Si este trozo tiene que decir en que idioma suena (Propuesta): idioma fijo, no una mezcla, y al dia
        (poniendose al dia, con trozos de minutos, no: ahi no hay nada que proponer a tiempo y se ahorra el coste)."""
        return bool(fixed_lang and not engines.lang_pair(fixed_lang) and st.get("caught_up"))

    def transcribe(window, offset, timing=None, voice=0.0):
        nonlocal lang
        st["oido"] = []  # (idioma, prob, segundos de voz) de este trozo, para la Propuesta
        preset = engines.PRESETS_BY_ID.get(job.preset) or engines.PRESETS_BY_ID["medium"]
        prev = segs[-1:]  # para no repetir la ultima frase del trozo anterior
        context = " ".join(t for _, _, t in segs[-CONTEXT_SEGS:])  # para enlazar con lo que se estaba diciendo
        about = opts.get("about") or None
        tm = timing if timing is not None else {}
        if preset["engine"] == "cloud":
            out, detected = opts["cloud"].transcribe_array(opts["cloud_cfg"], window, fixed_lang, opts.get("prompt"),
                                                           opts["tmp_dir"], job.id, stage, stop,
                                                           context=context,
                                                           model=engines.CloudEngine.live_model(opts["cloud_cfg"]),
                                                           about=about, timing=tm)
            # con un idioma fijo, el texto sale en ese idioma (se le pide a la API) aunque ella diga que suena a otro:
            # si lang tomara el que dice, «Cambiar» a ese idioma no rehario nada (switch_language: «ya estaba en él»)
            lang = fixed_lang or engines.LANG_CODES.get(str(detected or "").lower(), lang)
            if detected and oir():  # lo que dice la API (sin coste: viene en la respuesta); sin probabilidad
                st["oido"] = [(engines.LANG_CODES.get(str(detected).lower()), None, voice)]
            out = [(a + offset, b + offset, t) for a, b, t in out]
            return stitch(prev, out, st["dedupe_until"], lang)
        secs = len(window) / SR
        req = {"op": "window", "model": preset["model"], "threads": opts["threads"], "language": fixed_lang,
               "prev_language": lang, "prompt": opts.get("prompt"), "context": context, "beam": 1, "about": about,
               "oir": oir()}
        for attempt in range(2):
            try:
                worker.ensure(preset["model"], opts["threads"], lambda m: stage(m.get("stage") or job.stage),
                              lambda: job.cancel)
                stage("Transcribiendo en directo…" if st["caught_up"] else "Poniéndose al día con lo ya grabado…")
                t_req = perf_counter()
                # cambiar el idioma corta tambien este trozo: WorkerClient mata el proceso y se recarga (~2-5 s), en
                # vez de esperar a un trozo de 20-45 s (o de minutos, poniendose al dia) en el idioma de antes
                res = worker.request(req, audio=window, timeout=120 + 4 * secs, cancelled=stop)
                tm["infer"] = perf_counter() - t_req  # en local la "red" es el proceso de Whisper
                break
            except engines.WorkerTimeout as e:  # el motor se colgo: proceso nuevo y otro intento
                log(f"   {e} ({secs:.0f} s de audio, intento {attempt + 1})")
                stage("Whisper no respondía: reiniciándolo…")
        else:
            log("   se salta un trozo que Whisper no consigue transcribir")
            return []
        if res["language"] != lang:
            log(f"   idioma: {res['language']}" + (f" ({res['prob']:.0%})" if res.get("prob") else ""))
        lang = res["language"]
        st["oido"] = [(str(x[0]), x[1], voice) for x in (res.get("oido") or []) if x and x[0]]
        out = [(a + offset, b + offset, t) for a, b, t in res["segments"]]
        return engines.clean_segments(prev + out, res.get("prompt"), lang)[len(prev):]

    st["caught_up"] = False
    paused_logged = False
    job.live_error = None  # ultimo trozo que no se pudo transcribir (en la nube), para el estado final
    try:
        while True:
            if job.cancel:
                raise engines.Cancelled()
            cfg = fresh_cfg()
            if job.restart:
                job.restart = False
                segs = []
                st["gen"] += 1
                getattr(job, "edits", {}).clear()  # las correcciones eran sobre el texto de antes
                st["redo"], st["protegidas"] = None, 0
                reset(0.0)
                log(f"   rehaciendo desde el principio con {job.preset}")
            if lang_changed():  # cambiado en la pagina de la reunion: vale ya, tambien para lo ultimo escrito
                switch_language()
            cloud = (engines.PRESETS_BY_ID.get(job.preset) or {}).get("engine") == "cloud"
            if cloud and keys and getattr(job, "translate", False) and translator is None \
                    and opts.get("summarizer") is not None:
                translator = LiveTranslator(job, opts, lambda: (st["gen"], list(segs)), log)
                translator.start()
            pos, t0, buf, resample = st["pos"], st["t0"], st["buf"], st["resample"]
            hdr = wav_info(path) or info
            size = os.path.getsize(path)
            now = time.time()
            if size != last_size:
                last_size, last_growth = size, now
            closed = hdr["data_size"] is not None
            stalled = not closed and now - last_growth > STALLED_S
            end = off + hdr["data_size"] if closed else size
            end = pos + max(0, end - pos) // block * block
            job.recording = not (closed or stalled)
            job.recorded = (end - off) / br
            latency.seen(job.recorded, now)
            t_iter = perf_counter()  # preparacion de esta vuelta: leer, remuestrear, VAD, recortar
            # directo en la nube: ventana segun el cupo del dia; sin cupo, se deja de leer (lo que llegue
            # se transcribira al terminar en trozos grandes) y se avisa
            pace = None
            if cloud and keys:
                lm, why = engines.CloudEngine.live_model(cfg), None
                try:
                    pace = keys.live_pace(cfg, model=lm)
                except engines.EngineError as e:  # sin API key (se quito en Ajustes): en pausa, sin mandar nada
                    why = str(e)
                if pace is None and job.recording:
                    msg = pause_message(keys.public(cfg, model=lm)) if why is None else no_key_message(why)
                    if not paused_logged:
                        log("   " + msg)
                        paused_logged = True
                    stage(msg)
                    publish(None, paused=True)
                    time.sleep(POLL_S)
                    continue
                if paused_logged:
                    log("   directo reanudado: vuelve a haber cupo")
                    paused_logged = False
            if end > pos:
                n = min(end - pos, BACKLOG_MAX_S * br) // block * block
                with open(path, "rb") as f:
                    f.seek(pos)
                    raw = f.read(n)
                raw = raw[:len(raw) // block * block]
                pos += len(raw)
                buf = np.concatenate([buf, resample(to_mono(raw, info))])
            caught_up = pos >= end
            st["caught_up"] = caught_up and len(buf) < 60 * SR  # solo para el texto de estado
            final = (closed or stalled) and caught_up
            if final and not st["flushed"]:
                buf = np.concatenate([buf, resample.flush()])
                st["flushed"] = True
            st.update(pos=pos, buf=buf)
            directo = cloud and caught_up and not final
            overlap = 0
            if directo:
                p = pace or DIRECT_PACE
                if len(buf) < p[0] * SR:
                    cut, ts = 0, None
                else:
                    ts = speech(buf)
                    # con retraso (la API va mas lenta que la reunion), todo lo pendiente en una peticion: se pone
                    # al dia en vez de acumular minutos que al final saldrian de golpe
                    cut, overlap = direct_cut(buf, ts, p, catch_up=behind(buf, p))
            elif final and cloud:  # lo que queda al terminar, en trozos de como mucho CATCHUP_MAX_S
                head = buf[:int(CATCHUP_MAX_S * SR)]
                cut, overlap = capped_cut(buf, speech(head) if len(buf) > len(head) else None)
                ts = None
            else:
                cut, ts = choose_cut(buf, final, catching_up=not caught_up, cloud=cloud)
            if cut:
                window = buf[:cut]
                if ts is None:
                    ts = speech(window)
                if directo:
                    audio, a, voice = trim_voice(window, ts)  # solo voz: menos bytes y sin silencios que inventar
                else:
                    voice = sum(min(s["end"], cut) - s["start"] for s in ts if s["start"] < cut) / SR
                    audio, a = window, 0
                redo = st.get("redo")  # este trozo rehace lo ultimo en el idioma nuevo (switch_language)
                new = None
                if voice >= DIRECT_MIN_VOICE_S:  # menos de 1 s de voz: musica/ruido, Whisper solo inventaria
                    tm = {"prep": perf_counter() - t_iter}
                    try:
                        new = transcribe(audio, t0 + a / SR, tm, voice)
                    except engines.Cancelled:
                        if job.cancel or not lang_changed():
                            raise
                        # el idioma cambio con el trozo en vuelo: no se avanza; la vuelta siguiente lo aplica y
                        # repite este audio (y lo ultimo escrito) con el nuevo
                        log("   directo: el trozo en curso se repite con el idioma nuevo")
                        continue
                    except Exception as e:
                        if not cloud:  # en local, como siempre
                            raise
                        # en la nube la sesion no se cae por un trozo (tampoco al ponerse al dia ni el ultimo): se
                        # salta, la grabacion sigue y el repaso final lo recupera; si Groq ha bloqueado la clave, la
                        # siguiente vuelta entra en pausa. Tambien un fallo inesperado (una respuesta rara del proxy)
                        err = str(e) if isinstance(e, engines.EngineError) else f"{e.__class__.__name__}: {e}"
                        new, job.live_error = [], err
                        if err != st.get("last_error"):
                            st["last_error"] = err
                            log(f"   trozo sin transcribir ({err}); se sigue con el siguiente")
                        # err ya es una frase con lo que ha pasado y que hacer (engines.cloud_http_error…)
                        stage(f"Sin respuesta de la nube: {err.rstrip('.')}. Se sigue con el siguiente trozo; el "
                              "repaso final recupera el texto.")
                    if job.restart:  # se pidio rehacer mientras tanto: este trozo ya no vale
                        continue
                    if lang_cuts():  # cambio el idioma con la peticion en vuelo (a la nube no se le corta): fuera
                        log("   directo: el idioma ha cambiado durante el trozo; se repite con el nuevo")
                        continue
                    try:
                        antes = propuesta.publica()
                        propuesta.oir(fixed_lang, st.get("oido"))
                        ahora = propuesta.publica()
                        if ahora and ahora != antes:
                            log(f"   directo: lo último suena a {ahora['idioma']} (fijado: {fixed_lang}); se propone "
                                f"{ahora['cambiar_a']}")
                    except Exception as e:  # un aviso nunca tumba el directo
                        log(f"   directo: idioma propuesto: {e.__class__.__name__}: {e}")
                    if redo is not None and new:
                        tocadas = getattr(job, "edits", None) or {}
                        if any(seg_key(s[0]) in tocadas for s in redo["removed"]):
                            # corregiste a mano lo que se estaba rehaciendo, antes de que llegara lo nuevo: mandan tus
                            # correcciones. Lo de antes se queda tal cual (es lo que hay en pantalla y en el archivo) y
                            # se relee desde donde acababa, ya en el idioma nuevo: Whisper no corta justo ahi, asi que
                            # quedarse con parte de este trozo dejaria un hueco o un repetido (revision 2)
                            segs.extend(redo["removed"])
                            st["redo"] = None
                            reset(redo["until"])
                            log(f"   directo: lo que se rehacía se corrigió a mano entretanto; se queda lo de antes "
                                f"({len(redo['removed'])} frases) y el idioma nuevo vale desde {redo['until']:.1f} s")
                            continue
                    t_paint = perf_counter()
                    if new:
                        segs.extend(new)
                        write(segs)
                        job.rev += 1
                    if directo:
                        tm["paint"] = perf_counter() - t_paint
                        lat = latency.add(t0 + (a + len(audio)) / SR, time.time(), tm)
                        if lat is not None:
                            log(f"   trozo de {len(audio) / SR:.1f} s ({voice:.1f} s de voz, {len(new)} frases): "
                                f"texto a los {lat:.1f} s{fmt_phases(tm)}")
                if redo is not None:
                    st["redo"] = None
                    end_s = t0 + cut / SR
                    if new:
                        log(f"   directo: rehecho en {lang or 'el idioma detectado'} lo último ({len(redo['removed'])} "
                            f"frases antes, {len(new)} ahora)")
                    else:  # no ha salido (un fallo de la nube, o nada): vuelve lo de antes, mejor que un hueco
                        back = back_of(redo["removed"], end_s)
                        segs.extend(back)  # (ya estaba en pantalla y en el archivo: no hay que escribir nada)
                        desde = back[-1][1] if back else redo["from"]
                        if back:
                            log(f"   directo: no se ha podido rehacer lo último en el idioma nuevo; se queda el texto "
                                f"de antes ({len(back)} frases)")
                        # lo que va detras se relee desde ahi (si no, se perderia el trozo de la frase que cruzaba el
                        # corte); ya no es «rehacer»: si vuelve a fallar, se salta como cualquier trozo
                        reset(desde)
                        publish(pace if directo else None)
                        continue
                st["dedupe_until"] = (t0 + cut / SR) if overlap else None
                st["buf"] = buf[cut - overlap:]
                st["t0"] = t0 + (cut - overlap) / SR
                job.transcribed = st["t0"]
                job.progress = min(1.0, st["t0"] / job.recorded) if job.recorded else 0.0
            publish(pace if directo else None)
            if final and not (cut and len(st["buf"])):  # al terminar, hasta que no quede nada (trozos con tope)
                break
            if not cut:
                stage("Escuchando la grabación…" if job.recording else "Terminando…")
                if caught_up:
                    time.sleep(POLL_S)
    except BaseException:
        if translator:
            translator.stop()
        raise
    if translator:
        if getattr(job, "translate", False):  # lo ultimo que quedo sin traducir: como mucho TRANSLATE_FINAL_S
            try:
                translator.step(final=True)
            except Exception:
                pass
        translator.after_stop = lambda: publish(None)  # lo que se llevo el hilo, si llega despues, se ve igual
        translator.stop()
    publish(None)
    job.recording = False
    job.duration = job.recorded
    return segs, lang
