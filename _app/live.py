# -*- coding: utf-8 -*-
"""
Transcripcion en directo de una grabacion de OBS que todavia se esta escribiendo.

OBS deja los tamanos de la cabecera WAV a 0xFFFFFFFF mientras graba y los corrige al parar.
Mientras tanto, cada pocos segundos se lee lo nuevo, se pasa a 16 kHz mono, se corta en una
pausa (VAD) y se transcribe ese trozo. El .txt/.srt junto al audio se reescribe tras cada trozo,
asi que en la app se va viendo el texto casi al momento.
"""
import os
import struct
import time

import numpy as np

import engines

SR = 16000
OPEN = 0xFFFFFFFF
STALLED_S = 45          # sin crecer este tiempo y con la cabecera abierta = OBS se cerro sin finalizar
LIVE_MIN_S = 20         # en directo en local: trozos de al menos ~20 s (cortados en una pausa)
LIVE_MAX_S = 45
CLOUD_MIN_S = 8         # en la nube cada trozo tarda ~1-2 s: trozos cortos = el texto llega en ~5-8 s
CLOUD_MAX_S = 15        # (Groq admite 20 peticiones/min: con >= 5 s por trozo sobra)
POLL_S = 0.5            # cada cuanto se mira si la grabacion ha crecido
CONTEXT_SEGS = 3        # frases anteriores que se le pasan a Whisper para enlazar con el trozo nuevo
BACKLOG_MIN_S = 120     # poniendose al dia (grabacion ya empezada): trozos grandes, mas eficientes
BACKLOG_MAX_S = 300


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
    """Muestras a transcribir ahora (0 = esperar a tener mas). Siempre corta en una pausa."""
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


def run(job, path, opts, write, log):
    """Bucle de la sesion en directo.

    job.preset puede cambiar durante la sesion (se aplica desde el siguiente trozo) y job.restart
    pide rehacerlo todo desde el principio. opts: language, prompt, threads, worker (WorkerClient
    propio), cloud, cloud_cfg, tmp_dir, resume (segmentos ya escritos por una sesion anterior)."""
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
    st = {}

    def reset(from_t):
        st["pos"] = off + int(from_t * br) // block * block
        st["t0"] = (st["pos"] - off) / br
        st["buf"] = np.zeros(0, np.float32)
        st["resample"] = Resampler(info["rate"])
        job.transcribed = st["t0"]

    reset(segs[-1][1] if segs else 0.0)  # retoma donde lo dejo una sesion anterior
    last_size, last_growth = -1, time.time()

    def stage(s):
        job.stage = s

    def transcribe(window, offset):
        nonlocal lang
        preset = engines.PRESETS_BY_ID.get(job.preset) or engines.PRESETS_BY_ID["medium"]
        prev = segs[-1:]  # para no repetir la ultima frase del trozo anterior
        context = " ".join(t for _, _, t in segs[-CONTEXT_SEGS:])  # para enlazar con lo que se estaba diciendo
        if preset["engine"] == "cloud":
            out, detected = opts["cloud"].transcribe_array(opts["cloud_cfg"], window, fixed_lang, opts.get("prompt"),
                                                           opts["tmp_dir"], job.id, stage, lambda: job.cancel,
                                                           context=context)
            lang = engines.LANG_CODES.get(str(detected or "").lower(), lang)
            out = [(a + offset, b + offset, t) for a, b, t in out]
            return engines.clean_segments(prev + out, None, lang)[len(prev):]
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
    while True:
        if job.cancel:
            raise engines.Cancelled()
        if job.restart:
            job.restart = False
            segs = []
            getattr(job, "edits", {}).clear()  # las correcciones eran sobre el texto de antes
            reset(0.0)
            log(f"   rehaciendo desde el principio con {job.preset}")
        pos, t0, buf, resample = st["pos"], st["t0"], st["buf"], st["resample"]
        hdr = wav_info(path) or info
        size = os.path.getsize(path)
        if size != last_size:
            last_size, last_growth = size, time.time()
        closed = hdr["data_size"] is not None
        stalled = not closed and time.time() - last_growth > STALLED_S
        end = off + hdr["data_size"] if closed else size
        end = pos + max(0, end - pos) // block * block
        job.recording = not (closed or stalled)
        job.recorded = (end - off) / br
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
        cloud = (engines.PRESETS_BY_ID.get(job.preset) or {}).get("engine") == "cloud"
        cut, ts = choose_cut(buf, final, catching_up=not caught_up, cloud=cloud)
        if cut:
            window = buf[:cut]
            if ts is None:
                ts = speech(window)
            voice = sum(min(s["end"], cut) - s["start"] for s in ts if s["start"] < cut) / SR
            if voice >= 1.0:  # menos de 1 s de voz: musica/ruido, Whisper solo inventaria
                new = transcribe(window, t0)
                if job.restart:  # se pidio rehacer mientras tanto: este trozo ya no vale
                    continue
                if new:
                    segs.extend(new)
                    write(segs)
                    job.rev += 1
            st["buf"] = buf[cut:]
            st["t0"] = t0 + cut / SR
            job.transcribed = st["t0"]
            job.progress = min(1.0, st["t0"] / job.recorded) if job.recorded else 0.0
        if final:
            break
        if not cut:
            stage("Escuchando la grabación…" if job.recording else "Terminando…")
            if caught_up:
                time.sleep(POLL_S)
    job.recording = False
    job.duration = job.recorded
    return segs, lang
