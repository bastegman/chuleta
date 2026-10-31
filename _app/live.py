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
lo que falte se transcribe despues en trozos grandes, mas el repaso final).
"""
import os
import struct
import threading
import time

import numpy as np

import engines

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
DIRECT_MIN_VOICE_S = 1.0    # menos voz que esto en un trozo: ruido o un golpe; no se manda (Whisper inventaria)
TRAIL_SILENCE_S = 0.5       # silencio al final del buffer que ya cuenta como pausa
TRANSLATE_EVERY_S = 12      # traduccion del directo: cada 10-15 s, lo ultimo que se dijo
TRANSLATE_MAX_CHARS = 1500  # por peticion (~500 tokens: cabe de sobra en 8.000 tokens/min)
TRANSLATION_PUBLIC = 60     # ultimos bloques traducidos que viajan en el estado del trabajo


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


def speech(buf):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    return get_speech_timestamps(buf, VadOptions(min_silence_duration_ms=300))


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


def direct_cut(buf, ts, pace=DIRECT_PACE):
    """Donde cortar el directo en la nube. Devuelve (cut, overlap) en muestras; cut = 0 es "esperar".

    La ventana se mide desde el primer tramo de voz (el silencio inicial se recorta luego y no cuenta):
      - con una pausa a >= min_s de ahi, se corta en la ULTIMA pausa que no pase de 2 x max_s (si el bucle
        venia con retraso y hay varios trozos pendientes, se fusionan en una peticion);
      - sin pausa y con >= max_s de audio desde la voz, corte forzado en max_s con 0,5 s de solape (la ventana
        siguiente empieza 0,5 s antes y dedupe_overlap quita lo que acabe dentro del solape);
      - sin voz (ts vacio), todo el buffer se descarta sin mandarlo: (n, 0)."""
    n = len(buf)
    if not ts:
        return n, 0
    lo, hi = pace
    base = ts[0]["start"]
    good = [p for p in pauses_of(ts, n) if p - base >= lo * SR]
    if good:
        near = [p for p in good if p - base <= DIRECT_FUSE_X * hi * SR]
        return (near[-1] if near else good[0]), 0
    if n - base >= hi * SR:
        return base + int(hi * SR), int(DIRECT_OVERLAP_S * SR)
    return 0, 0


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


class Latency:
    """Latencia del directo: del final del audio de cada trozo (cuando ya estaba en el fichero) al texto escrito.
    marks: (segundos grabados, hora de pared en que el fichero llegaba hasta ahi), una por lectura."""

    def __init__(self, keep=400):
        self.marks, self.samples, self.keep = [], [], keep

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

    def add(self, rec_end_s, now):
        w = self.wall_of(rec_end_s)
        if w is None:
            return None
        lat = max(0.0, now - w)
        self.samples.append(lat)
        if len(self.samples) > self.keep:
            del self.samples[:-self.keep]
        return lat

    def stats(self):
        if not self.samples:
            return {"n": 0, "p50": None, "p95": None, "last": None}
        s = sorted(self.samples)
        q = lambda p: s[min(len(s) - 1, int(round(p * (len(s) - 1))))]  # noqa: E731
        return {"n": len(s), "p50": round(q(0.5), 2), "p95": round(q(0.95), 2), "last": round(self.samples[-1], 2)}


def fmt_wait(secs):
    secs = int(max(0, secs or 0))
    if secs >= 3600:
        return f"{secs // 3600} h {secs % 3600 // 60:02d} min"
    return f"{secs // 60} min" if secs >= 120 else f"{secs} s"


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


class LiveTranslator:
    """Hilo que traduce al espanol lo ultimo que se ha transcrito, cada TRANSLATE_EVERY_S, mientras job.translate.
    get_segs() devuelve (generacion, lista de frases); si la generacion cambia (rehacer desde el principio), empieza
    de cero. Lo traducido se acumula en job.translation (en memoria: es una ayuda para seguir la reunion)."""

    def __init__(self, job, opts, get_segs, log, every=TRANSLATE_EVERY_S):
        self.job, self.opts, self.get_segs, self.log, self.every = job, opts, get_segs, log, every
        self.done, self.gen, self.stop_flag, self.error, self.busy = 0, None, False, None, False
        self.lock = threading.Lock()  # step() lo llama el hilo y, al final, el bucle del directo
        self.thread = threading.Thread(target=self._loop, daemon=True, name="live-translate")

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_flag = True

    def _cancelled(self):
        return self.stop_flag or self.job.cancel

    def pending(self):
        gen, segs = self.get_segs()
        if gen != self.gen:
            self.gen, self.done = gen, 0
            self.job.translation.clear()
        segs = segs[self.done:]
        take, chars = [], 0
        for s in segs:
            if take and chars + len(s[2]) > TRANSLATE_MAX_CHARS:
                break
            take.append(s)
            chars += len(s[2]) + 1
        return take

    def step(self, cancelled=None):
        """Una pasada: traduce lo pendiente (si hay). Devuelve True si tradujo algo."""
        with self.lock:
            take = self.pending()
            if not take or not getattr(self.job, "translate", False):
                return False
            text = " ".join(t for _, _, t in take)
            self.busy = True
            try:
                out, _ = self.opts["summarizer"].translate(self.opts["cloud_cfg"], text, lambda **k: None,
                                                           cancelled or self._cancelled)
            except engines.Cancelled:
                return False
            except engines.EngineError as e:
                self.error = str(e)
                self.log(f"   traducción del directo: {e}")
                return False
            finally:
                self.busy = False
            self.error = None
            self.job.translation.append({"start": round(float(take[0][0]), 2), "end": round(float(take[-1][1]), 2),
                                         "text": out})
            self.done += len(take)
            return True

    def _loop(self):
        last = 0.0
        while not self._cancelled():
            if getattr(self.job, "translate", False) and time.time() - last >= self.every:
                try:
                    did = self.step()
                except Exception as e:  # nunca tumba el directo
                    self.log(f"   traducción del directo: {e.__class__.__name__}: {e}")
                    did = False
                # con exito y mas pendiente, el siguiente en ~2 s; si no (o tras un error), al ritmo normal
                last = last + 2.0 if did and self.pending() else time.time()
            time.sleep(0.25)


def run(job, path, opts, write, log):
    """Bucle de la sesion en directo.

    job.preset puede cambiar durante la sesion (se aplica desde el siguiente trozo) y job.restart
    pide rehacerlo todo desde el principio. opts: language, prompt, threads, worker (WorkerClient
    propio), cloud, cloud_cfg, tmp_dir, resume (segmentos ya escritos por una sesion anterior),
    summarizer (traduccion del directo)."""
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

    def reset(from_t):
        st["pos"] = off + int(from_t * br) // block * block
        st["t0"] = (st["pos"] - off) / br
        st["buf"] = np.zeros(0, np.float32)
        st["resample"] = Resampler(info["rate"])
        st["dedupe_until"] = None
        job.transcribed = st["t0"]

    reset(segs[-1][1] if segs else 0.0)  # retoma donde lo dejo una sesion anterior
    last_size, last_growth = -1, time.time()

    def stage(s):
        job.stage = s

    def publish(pace=None, paused=False):
        """Estado del directo para la interfaz (cupo, ventana, latencia, traduccion)."""
        if not hasattr(job, "translation"):
            return
        tr = list(job.translation)
        on_cloud = bool(keys) and (engines.PRESETS_BY_ID.get(job.preset) or {}).get("engine") == "cloud"
        job.directo = {"cloud": on_cloud, "quota": keys.public(cfg) if on_cloud else None,
                       "pace": list(pace) if pace else None, "paused": bool(paused), "latency": latency.stats(),
                       "translate": bool(getattr(job, "translate", False)), "translation": tr[-TRANSLATION_PUBLIC:],
                       "translation_n": len(tr), "translate_error": translator.error if translator else None,
                       "translating": bool(translator and translator.busy),
                       "live_model": engines.CloudEngine.live_model(cfg) if on_cloud else None}

    def transcribe(window, offset):
        nonlocal lang
        preset = engines.PRESETS_BY_ID.get(job.preset) or engines.PRESETS_BY_ID["medium"]
        prev = segs[-1:]  # para no repetir la ultima frase del trozo anterior
        context = " ".join(t for _, _, t in segs[-CONTEXT_SEGS:])  # para enlazar con lo que se estaba diciendo
        if preset["engine"] == "cloud":
            out, detected = opts["cloud"].transcribe_array(opts["cloud_cfg"], window, fixed_lang, opts.get("prompt"),
                                                           opts["tmp_dir"], job.id, stage, lambda: job.cancel,
                                                           context=context,
                                                           model=engines.CloudEngine.live_model(opts["cloud_cfg"]))
            lang = engines.LANG_CODES.get(str(detected or "").lower(), lang)
            out = [(a + offset, b + offset, t) for a, b, t in out]
            return stitch(prev, out, st["dedupe_until"], lang)
        secs = len(window) / SR
        req = {"op": "window", "model": preset["model"], "threads": opts["threads"], "language": fixed_lang,
               "prev_language": lang, "prompt": opts.get("prompt"), "context": context, "beam": 1}
        for attempt in range(2):
            try:
                worker.ensure(preset["model"], opts["threads"], lambda m: stage(m.get("stage") or job.stage),
                              lambda: job.cancel)
                stage("Transcribiendo en directo…" if st["caught_up"] else "Poniéndose al día con lo ya grabado…")
                res = worker.request(req, audio=window, timeout=120 + 4 * secs, cancelled=lambda: job.cancel)
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
        out = [(a + offset, b + offset, t) for a, b, t in res["segments"]]
        return engines.clean_segments(prev + out, res.get("prompt"), lang)[len(prev):]

    st["caught_up"] = False
    paused_logged = False
    try:
        while True:
            if job.cancel:
                raise engines.Cancelled()
            if job.restart:
                job.restart = False
                segs = []
                st["gen"] += 1
                getattr(job, "edits", {}).clear()  # las correcciones eran sobre el texto de antes
                reset(0.0)
                log(f"   rehaciendo desde el principio con {job.preset}")
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
            # directo en la nube: ventana segun el cupo del dia; sin cupo, se deja de leer (lo que llegue
            # se transcribira al terminar en trozos grandes) y se avisa
            pace = None
            if cloud and keys:
                pace = keys.live_pace(cfg)
                if pace is None and job.recording:
                    q = keys.public(cfg)
                    msg = pause_message(q)
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
            if final:
                buf = np.concatenate([buf, resample.flush()])
            st.update(pos=pos, buf=buf)
            directo = cloud and caught_up and not final
            overlap = 0
            if directo:
                lo = (pace or DIRECT_PACE)[0]
                if len(buf) < lo * SR:
                    cut, ts = 0, None
                else:
                    ts = speech(buf)
                    cut, overlap = direct_cut(buf, ts, pace or DIRECT_PACE)
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
                if voice >= DIRECT_MIN_VOICE_S:  # menos de 1 s de voz: musica/ruido, Whisper solo inventaria
                    try:
                        new = transcribe(audio, t0 + a / SR)
                    except engines.EngineError as e:
                        if not directo:
                            raise
                        # el directo no se cae por un trozo: se salta (el repaso final lo recupera) y, si Groq ha
                        # bloqueado la clave, la siguiente vuelta entra en pausa
                        new = []
                        if str(e) != st.get("last_error"):
                            st["last_error"] = str(e)
                            log(f"   trozo sin transcribir ({e}); se sigue con el siguiente")
                        stage(f"Sin respuesta de la nube ({e}). Se sigue con el siguiente trozo; el repaso final "
                              "recupera el texto.")
                    if job.restart:  # se pidio rehacer mientras tanto: este trozo ya no vale
                        continue
                    if new:
                        segs.extend(new)
                        write(segs)
                        job.rev += 1
                    if directo:
                        lat = latency.add(t0 + (a + len(audio)) / SR, time.time())
                        if lat is not None:
                            log(f"   trozo de {len(audio) / SR:.1f} s ({voice:.1f} s de voz, {len(new)} frases): "
                                f"texto a los {lat:.1f} s")
                st["dedupe_until"] = (t0 + cut / SR) if overlap else None
                st["buf"] = buf[cut - overlap:]
                st["t0"] = t0 + (cut - overlap) / SR
                job.transcribed = st["t0"]
                job.progress = min(1.0, st["t0"] / job.recorded) if job.recorded else 0.0
            publish(pace if directo else None)
            if final:
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
        if getattr(job, "translate", False):  # lo ultimo que quedo sin traducir
            try:
                translator.step(cancelled=lambda: job.cancel)
            except Exception:
                pass
        translator.stop()
    publish(None)
    job.recording = False
    job.duration = job.recorded
    return segs, lang
