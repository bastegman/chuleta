# -*- coding: utf-8 -*-
"""
Proceso aparte que ejecuta Whisper (faster-whisper / CTranslate2).

El servidor le manda peticiones por stdin (una linea JSON + audio float32 opcional) y lee las
respuestas JSON por stdout. Si el motor nativo se cuelga (ha pasado: se queda dentro de
generate() sin volver) o hay que cancelar, el servidor mata este proceso y arranca otro, y la
app sigue funcionando.

Operaciones:
  load    {model, threads}                                         -> carga el modelo (queda en memoria)
  window  {model, threads, language, prev_language, prompt, beam} + audio 16 kHz -> segmentos del trozo
  file    {model, threads, path, batched, beam, language, prompt}  -> transcribe un archivo, con progreso
"""
import contextlib
import json
import os
import sys
import time


class _OnlyLanguages:
    """Envuelve el modelo nativo para que la deteccion de idioma solo elija entre los permitidos
    (p. ej. espanol o ingles: que un acento no lo convierta en gallego o portugues)."""

    def __init__(self, inner, langs):
        self._inner, self._tokens = inner, {f"<|{l}|>" for l in langs}

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def detect_language(self, *args, **kwargs):
        out = []
        for probs in self._inner.detect_language(*args, **kwargs):  # [(token, prob)] de mayor a menor
            keep = [p for p in probs if p[0] in self._tokens]
            out.append(keep + [p for p in probs if p[0] not in self._tokens] if keep else probs)
        return out


@contextlib.contextmanager
def only_languages(model, langs):
    if not langs:
        yield
        return
    inner = model.model
    model.model = _OnlyLanguages(inner, langs)
    try:
        yield
    finally:
        model.model = inner


def watch_parent(ppid):
    """Si el servidor muere (se cierra la ventana negra), este proceso se cierra aunque este a mitad
    de una transcripcion: si no, se queda huerfano gastando CPU y RAM para nadie."""
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, ppid)  # SYNCHRONIZE
    if h:
        k32.WaitForSingleObject(h, 0xFFFFFFFF)  # espera (sin gastar CPU) a que termine el servidor
        os._exit(0)


def main():
    import threading
    threading.Thread(target=watch_parent, args=(os.getppid(),), daemon=True).start()
    proto = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)  # cualquier print de las librerias va a stderr y no rompe el protocolo
    sys.stdout = sys.stderr
    inp = sys.stdin.buffer
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import numpy as np
    import engines

    state = {"key": None, "model": None}

    def send(obj):
        proto.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def model_for(req):
        key = (req["model"], int(req["threads"]))
        if state["key"] != key:
            state["model"] = state["key"] = None  # libera la RAM del anterior
            if not engines.model_cached(req["model"]):
                send({"type": "progress", "stage": f"Descargando modelo {req['model']} (solo la primera vez)…"})
            from faster_whisper import WhisperModel
            state["model"] = WhisperModel(key[0], device="cpu", compute_type="int8", cpu_threads=key[1])
            state["key"] = key
        return state["model"]

    def style(model, lang, vocab, context=None):
        """Frase de estilo + vocabulario (+ en directo, lo ultimo que se dijo), sin que se pierda la frase."""
        tok = model.hf_tokenizer

        def count(t):
            return len(tok.encode(" " + t.strip(), add_special_tokens=False).ids) if t.strip() else 0
        return engines.build_prompt(engines.STYLE_PROMPT.get(lang, "") if lang else "", vocab, count=count,
                                    context=context)[0]

    def detect(model, audio, **kw):
        try:
            return model.detect_language(audio, vad_filter=True, **kw)
        except ValueError:  # tramo sin voz: faster-whisper no tiene nada que concatenar
            return model.detect_language(audio, **kw)

    def lang_mode(value):
        """Idioma pedido -> (idioma fijo, deteccion en cada bloque, idiomas permitidos).
        "es-en" = espanol + ingles (spanglish): se detecta en cada bloque pero solo entre esos dos."""
        if value == "es-en":
            return None, True, ("es", "en")
        if not value:
            return None, True, None
        return value, False, None

    def window(req, audio):
        model = model_for(req)
        fixed, multi, allowed = lang_mode(req.get("language"))
        lang, prob = fixed, None
        with only_languages(model, allowed):
            if not lang:
                # idioma del trozo (para la frase de estilo); con poca voz la deteccion no es fiable
                # y se mantiene el anterior. Dentro del trozo, cada bloque detecta el suyo (multi).
                det, prob, _ = detect(model, audio)
                prev = req.get("prev_language")
                lang = det if (prob >= 0.6 or not prev or allowed) else prev
            prompt = style(model, "mix" if allowed else lang, req.get("prompt"), req.get("context"))
            from faster_whisper import BatchedInferencePipeline
            # bloques internos de 20 s en vez de 30: con 30 s medium/turbo se saltaban a veces el
            # principio del bloque (medido en una reunion real); con 20 s medium no pierde nada
            it, _ = BatchedInferencePipeline(model=model).transcribe(
                audio, language=lang, multilingual=multi, beam_size=int(req.get("beam") or 1), batch_size=8,
                without_timestamps=False, initial_prompt=prompt, chunk_length=int(req.get("chunk") or 20))
            segs = [(s.start, s.end, s.text) for s in it if engines.whisper_ok(s)]
        return {"segments": segs, "language": lang, "prob": prob, "prompt": prompt}

    def transcribe_file(req):
        model = model_for(req)
        send({"type": "progress", "stage": "Leyendo audio…"})
        from faster_whisper.audio import decode_audio
        audio = decode_audio(req["path"])
        offset = float(req.get("start") or 0)  # continuar una transcripcion que se quedo a medias
        if offset:
            audio = audio[int(offset * 16000):]
        fixed, multi, allowed = lang_mode(req.get("language"))
        lang = fixed
        with only_languages(model, allowed):  # el generador detecta al iterar: todo dentro del with
            if req.get("batched"):
                if not lang:
                    send({"type": "progress", "stage": "Detectando idioma…"})
                    # para la frase de estilo: 2 min a un 20 % de la reunion (el principio suele ser
                    # silencio o "¿se me oye?"); luego cada bloque de 30 s detecta el suyo
                    sr, n = 16000, len(audio)
                    start = int(n * 0.2) if n > sr * 600 else 0
                    lang, _, _ = detect(model, audio[start:start + sr * 120], language_detection_segments=2)
                prompt = style(model, "mix" if allowed else lang, req.get("prompt"))
                from faster_whisper import BatchedInferencePipeline
                segments, info = BatchedInferencePipeline(model=model).transcribe(
                    audio, batch_size=8, without_timestamps=False, language=lang, multilingual=multi,
                    beam_size=int(req.get("beam") or 1), initial_prompt=prompt)
            else:
                prompt = style(model, "mix" if allowed else None, req.get("prompt"))
                segments, info = model.transcribe(
                    audio, vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500),
                    language=lang, multilingual=multi, beam_size=int(req.get("beam") or 5), initial_prompt=prompt)
            duration = info.duration or len(audio) / 16000
            send({"type": "progress", "stage": "Transcribiendo", "frac": 0.0, "transcribing": True})
            out, last = [], 0.0
            for s in segments:
                if engines.whisper_ok(s):
                    out.append((s.start + offset, s.end + offset, s.text))
                if time.time() - last > 0.5:
                    last = time.time()
                    send({"type": "progress", "frac": min(s.end / duration, 1.0) if duration else 0})
        return {"segments": out, "language": lang or info.language, "duration": duration, "prompt": prompt}

    while True:
        line = inp.readline()
        if not line:
            return
        req = json.loads(line)
        n = int(req.get("n") or 0)
        audio = np.frombuffer(inp.read(n * 4), dtype="<f4") if n else None
        try:
            op = req["op"]
            if op == "load":
                model_for(req)
                send({"type": "result"})
            elif op == "window":
                send({"type": "result", **window(req, audio)})
            elif op == "file":
                send({"type": "result", **transcribe_file(req)})
            else:
                send({"type": "error", "error": f"operación desconocida: {op}"})
        except Exception as e:
            send({"type": "error", "error": f"{e.__class__.__name__}: {e}"})


if __name__ == "__main__":
    main()
