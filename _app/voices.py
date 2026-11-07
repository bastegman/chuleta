# -*- coding: utf-8 -*-
"""Quien habla: diarizacion en local (sherpa-onnx: segmentacion pyannote 3.0 + huellas TitaNet small), memoria de
personas entre reuniones y casado con las frases del .srt.

  - Funcion OPCIONAL (apagada por defecto). Si esta apagada no se importa sherpa_onnx, no se descarga nada y no se
    crea ningun sidecar. Los modelos (~47 MB) se bajan a DATA_DIR/models/voces/ al activarla.
  - Por reunion: <audio>.hablantes.json junto al audio (tramos + hablantes h1, h2… + casado con el .srt), SIN vectores.
  - Global: DATA_DIR/voces.json cifrado con DPAPI (fuera de Windows, en claro): personas con hasta 5 huellas
    (vectores de 192) cada una. Una huella solo se guarda cuando el usuario pulsa «Recordar» con consentimiento.
  - «Tu» no usa biometria: sale de la actividad del microfono durante la grabacion propia (micro{paso, activo}).
  - La diarizacion corre en un SUBPROCESO de prioridad baja (modo CLI de este mismo fichero), que escribe el sidecar
    y devuelve las huellas por stdout en JSON (como worker.py):

        python voices.py --diarize AUDIO --out SIDECAR [--niveles FICHERO] [--data DATA_DIR] [--threads 2]
        python voices.py --fingerprint h2 --diarize AUDIO --out SIDECAR     (recalcular la huella de un hablante)

Umbrales (spike del 30/10/2026): coseno >= 0,55 y segundo candidato a >= 0,10 → misma persona; 0,40-0,55 (o dos
candidatos a < 0,10) → provisional, se pide confirmar; < 0,40 → persona nueva.
"""
import argparse
import hashlib
import json
import math
import os
import sys
import tarfile
import threading
import time
import unicodedata
import uuid

import numpy as np

from common import log, protect, read_text, unprotect, write_bytes

MODEL_ID = "pyannote-seg-3.0 + nemo_en_titanet_small"
DIM = 192
UMBRAL, DUDOSO, MARGEN = 0.55, 0.40, 0.10
MAX_HUELLAS = 5          # por persona: al anadir la sexta se descarta la mas antigua
MIN_HUELLA_S = 20.0      # voz acumulada para proponer guardar una huella
MIN_MATCH_S = 5.0        # voz acumulada para intentar emparejar (entre 5 y 20 s: provisional)
BREVE_S = 10.0           # clusteres con menos voz: «(breve)», no generan persona
BLOQUE_S = 1800.0        # audio mas largo se diariza por bloques de <= 30 min enlazados por huella
HUELLA_MAX_S = 90.0      # voz que se concatena para una huella (los tramos mas largos primero)
SR = 16000
MIC_PASO, MIC_DB, MIC_SOBRE_SYS_DB = 0.25, -40.0, 6.0
# La grabadora estampa cada nivel con lo que YA ha escrito del WAV, pero el pico es de lo que acaba de llegar del
# dispositivo, que se escribe LAT (1 s) despues (recorder.py): la linea de tiempo del micro va adelantada respecto al
# audio. Se corrige al leerla; una linea de tiempo ya alineada lleva "desfase": 0. Medido: tests/test_fix_voces.py.
MIC_DESFASE_S = 0.875
MIC_NO_ES_TU = 0.2       # grabacion propia: un hablante que casi nunca coincide con el micro activo no es el usuario
SIDECAR_EXT = ".hablantes.json"
IDLE_PRIORITY = getattr(__import__("subprocess"), "IDLE_PRIORITY_CLASS", 0)

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
# sha256 calculados el 30/10/2026 sobre los ficheros descargados de esos URLs; el de TitaNet coincide con el
# checksum.txt del release speaker-recongition-models (el release de segmentacion no publica checksum.txt).
MODELS = (
    {"name": "segmentation.onnx", "size": 6958444, "member": "sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
     "url": RELEASES + "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
     "sha256": "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"},
    {"name": "nemo_en_titanet_small.onnx", "size": 40257283,
     "url": RELEASES + "speaker-recongition-models/nemo_en_titanet_small.onnx",
     "sha256": "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e"},
)


class VoicesError(Exception):
    pass


# ----------------------------------------------------------------------------- disponibilidad y modelos

def enabled(settings):
    return bool((settings or {}).get("voices_enabled"))


_AVAILABLE = {}


def available():
    """(True, None) si sherpa_onnx importa en ESTE Python; si no, (False, motivo). Se prueba en un subproceso (asi
    el servidor no carga las DLL de onnxruntime) y se recuerda. No se llama si la opcion esta apagada."""
    if not _AVAILABLE:
        import subprocess
        try:
            r = subprocess.run([sys.executable, "-c", "import sherpa_onnx; print(sherpa_onnx.__version__)"],
                               capture_output=True, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                _AVAILABLE.update(ok=True, why=None)
            else:  # ImportError o un fallo de DLL en Windows: la ultima linea del error
                err = r.stderr.decode("utf-8", "replace").strip().splitlines()
                _AVAILABLE.update(ok=False, why=(err[-1] if err else f"salida {r.returncode}")[:300])
        except (OSError, subprocess.SubprocessError) as e:
            _AVAILABLE.update(ok=False, why=f"{e.__class__.__name__}: {e}")
    return _AVAILABLE["ok"], _AVAILABLE["why"]


def models_dir(data_dir):
    return os.path.join(data_dir, "models", "voces")


def model_paths(data_dir):
    d = models_dir(data_dir)
    return {"seg": os.path.join(d, MODELS[0]["name"]), "emb": os.path.join(d, MODELS[1]["name"])}


def models_ready(data_dir):
    return all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in model_paths(data_dir).values())


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_models(data_dir, progress=None, cancelled=None):
    """Descarga (una vez) los dos modelos a DATA_DIR/models/voces/ comprobando el sha256 anclado arriba."""
    progress = progress or (lambda **k: None)
    cancelled = cancelled or (lambda: False)
    d = models_dir(data_dir)
    os.makedirs(d, exist_ok=True)
    total = sum(m["size"] for m in MODELS)
    done = 0
    for m in MODELS:
        target = os.path.join(d, m["name"])
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            done += m["size"]
            continue
        import requests
        part = os.path.join(d, os.path.basename(m["url"]) + ".part")
        progress(stage=f"Descargando modelo de voces ({m['name']})…", frac=done / total)
        with requests.get(m["url"], stream=True, timeout=60) as r:
            if r.status_code != 200:
                raise VoicesError(f"No se pudo descargar {m['url']} (HTTP {r.status_code})")
            got = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    if cancelled():
                        f.close()
                        os.remove(part)
                        raise VoicesError("cancelado")
                    f.write(chunk)
                    got += len(chunk)
                    progress(frac=(done + min(got, m["size"])) / total)
        digest = _sha256(part)
        if digest != m["sha256"]:
            os.remove(part)
            raise VoicesError(f"El modelo descargado no coincide con el esperado (sha256 {digest[:12]}…): "
                              "no se usa. Prueba otra vez mas tarde.")
        if m.get("member"):  # tarball: solo el model.onnx fp32 (el int8 anade clusteres espurios)
            with tarfile.open(part, "r:bz2") as t:
                member = t.getmember(m["member"])
                with t.extractfile(member) as src, open(target + ".part", "wb") as dst:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        dst.write(chunk)
            os.replace(target + ".part", target)
            os.remove(part)
        else:
            os.replace(part, target)
        done += m["size"]
        progress(frac=done / total)
    progress(stage="Modelos de voces listos", frac=1.0)
    return model_paths(data_dir)


# ----------------------------------------------------------------------------- audio

def load_audio_16k(path, cancelled=None):
    """int16 16 kHz mono. Con PyAV (engines.decode_16k) cualquier formato; sin el, solo WAV PCM 16 bit (desarrollo)."""
    try:
        import engines
        return engines.decode_16k(path, cancelled=cancelled or (lambda: False))
    except ImportError:
        pass
    import wave
    with wave.open(path, "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise VoicesError("Sin PyAV solo se leen WAV de 16 bit")
    x = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        if sr % SR == 0:  # 48 kHz → 16 kHz: media de cada 3 muestras (filtro tosco, vale para diarizar)
            k = sr // SR
            x = x[:len(x) // k * k].reshape(-1, k).mean(axis=1)
        else:
            x = np.interp(np.arange(0, len(x), sr / SR), np.arange(len(x)), x)
    return np.ascontiguousarray(np.asarray(x).astype(np.int16))


def _f32(samples16):
    return np.ascontiguousarray(np.asarray(samples16, dtype=np.float32) / 32768.0)


# ----------------------------------------------------------------------------- diarizacion y huellas (sherpa-onnx)

def _sherpa():
    try:
        import sherpa_onnx
    except Exception as e:  # ImportError o un fallo de DLL en Windows
        raise VoicesError(f"sherpa-onnx no esta disponible: {e.__class__.__name__}: {e}")
    return sherpa_onnx


def _diarizer(so, paths, threads=2, shift=0.5):
    cfg = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=paths["seg"], window_shift_ratio=shift),
            num_threads=threads),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=paths["emb"], num_threads=threads),
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    if not cfg.validate():
        raise VoicesError("Configuracion de diarizacion no valida (¿faltan los modelos en models/voces?)")
    return so.OfflineSpeakerDiarization(cfg)


def _extractor(so, paths, threads=2):
    cfg = so.SpeakerEmbeddingExtractorConfig(model=paths["emb"], num_threads=threads)
    if not cfg.validate():
        raise VoicesError("Modelo de huellas de voz no valido")
    return so.SpeakerEmbeddingExtractor(cfg)


def embed(ex, samples_f32):
    """Vector L2-normalizado (192) de un trozo de voz float32 a 16 kHz, o None si es demasiado corto."""
    if len(samples_f32) < SR:  # menos de 1 s: no es fiable
        return None
    s = ex.create_stream()
    s.accept_waveform(SR, samples_f32)
    s.input_finished()
    if not ex.is_ready(s):
        return None
    v = np.asarray(ex.compute(s), dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else None


def fingerprint(ex, samples16, tramos, max_s=HUELLA_MAX_S):
    """Huella de un hablante: sus tramos mas largos concatenados (hasta max_s) → embed. (vector|None, segundos)."""
    tramos = sorted(tramos, key=lambda t: -(t[1] - t[0]))
    parts, acc = [], 0.0
    for a, b in tramos:
        if acc >= max_s:
            break
        take = min(b - a, max_s - acc)
        parts.append(samples16[int(a * SR):int((a + take) * SR)])
        acc += take
    if not parts:
        return None, 0.0
    return embed(ex, _f32(np.concatenate(parts))), sum(b - a for a, b in tramos)


def cosine(a, b):
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na > 0 and nb > 0 else 0.0


def mixed(huellas, umbral=DUDOSO):
    """Alguna huella de la persona no se parece a ninguna de las otras (coseno < umbral, lo que el emparejado
    llamaria «otra persona»): puede llevar la voz de alguien mas (p. ej. por el fallo de antes del 07/11/2026)."""
    vs = [h["vector"] for h in huellas or [] if h.get("vector")]
    if len(vs) < 2:
        return False
    return any(max(cosine(a, b) for j, b in enumerate(vs) if j != i) < umbral for i, a in enumerate(vs))


def diarize(samples16, data_dir, threads=2, progress=None, cancelled=None, shift=0.5):
    """Devuelve {"tramos": [{inicio, fin, h}], "hablantes": {h: {segundos, breve, …}}, "huellas": {h: [floats]},
    "duracion_s"}. Audio > 30 min: por bloques, enlazando los clusteres de cada bloque por huella (coseno >= umbral)."""
    progress = progress or (lambda **k: None)
    cancelled = cancelled or (lambda: False)
    so = _sherpa()
    paths = model_paths(data_dir)
    if not models_ready(data_dir):
        raise VoicesError("Faltan los modelos de voces (activa la opcion en Ajustes para descargarlos)")
    sd, ex = _diarizer(so, paths, threads, shift), _extractor(so, paths, threads)
    if sd.sample_rate != SR:
        raise VoicesError(f"El modelo espera {sd.sample_rate} Hz")
    total_s = len(samples16) / SR
    n_blocks = max(1, int(math.ceil(total_s / BLOQUE_S)))
    block = int(math.ceil(len(samples16) / n_blocks))
    speakers = []  # globales: {"vec", "vecs": [(vec, s)], "segundos", "tramos": [(a, b)]}
    for bi in range(n_blocks):
        off = bi * block
        part = samples16[off:off + block]
        if not len(part):
            continue
        t_off = off / SR

        def cb(done, n, _bi=bi):
            where = f"bloque {_bi + 1}/{n_blocks}" if n_blocks > 1 else "escuchando"
            progress(stage=f"Identificando voces… ({where})", frac=0.05 + 0.85 * (_bi + done / max(1, n)) / n_blocks)
            return 1 if cancelled() else 0

        res = sd.process(_f32(part), cb)
        if cancelled():
            raise VoicesError("cancelado")
        local = {}
        for s in res.sort_by_start_time():
            local.setdefault(int(s.speaker), []).append((round(s.start + t_off, 3), round(s.end + t_off, 3)))
        for spk in sorted(local):
            if cancelled():
                raise VoicesError("cancelado")
            vec, secs = fingerprint(ex, samples16, local[spk])
            target = None
            if bi and vec is not None:  # bloque 2 en adelante: ¿es alguien del bloque anterior?
                best = max(((cosine(vec, g["vec"]), g) for g in speakers if g["vec"] is not None),
                           key=lambda x: x[0], default=(0.0, None))
                if best[0] >= UMBRAL:
                    target = best[1]
            if target is None:
                target = {"vec": None, "vecs": [], "segundos": 0.0, "tramos": []}
                speakers.append(target)
            target["tramos"] += local[spk]
            target["segundos"] += secs
            if vec is not None:
                target["vecs"].append((vec, secs))
                mean = sum(v * max(s, 1.0) for v, s in target["vecs"])
                n = float(np.linalg.norm(mean))
                target["vec"] = mean / n if n > 0 else None
    order = sorted(range(len(speakers)), key=lambda i: -speakers[i]["segundos"])
    tramos, hablantes, huellas = [], {}, {}
    for k, i in enumerate(order, 1):
        h, g = f"h{k}", speakers[i]
        tramos += [{"inicio": round(a, 2), "fin": round(b, 2), "h": h} for a, b in g["tramos"]]
        hablantes[h] = {"persona": None, "score": None, "segundos": round(g["segundos"], 1),
                        "provisional": False, "breve": g["segundos"] < BREVE_S}
        if g["vec"] is not None:
            huellas[h] = [round(float(x), 5) for x in g["vec"]]
    tramos.sort(key=lambda t: (t["inicio"], t["fin"]))
    progress(stage="Voces identificadas", frac=0.95)
    return {"tramos": tramos, "hablantes": hablantes, "huellas": huellas, "duracion_s": round(total_s, 1)}


# ----------------------------------------------------------------------------- emparejado, casado y «Tu» (numpy puro)

def match(vector, personas, umbral=UMBRAL, dudoso=DUDOSO, margen=MARGEN):
    """Coseno maximo contra todas las huellas de cada persona.
    >= umbral y 2.º candidato a >= margen → persona; dudoso..umbral, o dos candidatos a < margen → provisional (¿X?);
    < dudoso → None (persona nueva)."""
    scores = []
    for p in personas or []:
        best = max((cosine(vector, h["vector"]) for h in p.get("huellas") or [] if h.get("vector")), default=None)
        if best is not None:
            scores.append((best, p["id"]))
    scores.sort(key=lambda x: -x[0])
    out = {"persona": None, "score": None, "provisional": False, "segunda": None, "score2": None}
    if not scores:
        return out
    s1, id1 = scores[0]
    s2, id2 = scores[1] if len(scores) > 1 else (None, None)
    close = s2 is not None and s1 - s2 < margen
    out.update(score=round(s1, 3), score2=round(s2, 3) if s2 is not None else None)
    if s1 >= umbral and not close:
        out.update(persona=id1)
    elif s1 >= dudoso:
        out.update(persona=id1, provisional=True, segunda=id2 if close else None)
    return out


def _bounds(f):
    if isinstance(f, dict):
        return f.get("start"), f.get("end")
    return f[0], f[1]


def assign_srt(frases, tramos):
    """Hablante de cada frase por solape: mayoritario si cubre >= 50 %; «varios» si dos cubren >= 35 % cada uno;
    None si ningun tramo la toca. Devuelve una lista alineada con frases."""
    tr = sorted(((float(t["inicio"]), float(t["fin"]), t["h"]) for t in tramos), key=lambda x: x[0])
    max_end = []
    m = -1e18
    for a, b, _ in tr:
        m = max(m, b)
        max_end.append(m)
    out = [None] * len(frases)
    order = sorted(range(len(frases)), key=lambda i: (_bounds(frases[i])[0] is None, _bounds(frases[i])[0] or 0))
    j0 = 0
    for i in order:
        a, b = _bounds(frases[i])
        if a is None or b is None or b <= a:
            continue
        a, b = float(a), float(b)
        while j0 < len(tr) and max_end[j0] <= a:
            j0 += 1
        cover, j = {}, j0
        while j < len(tr) and tr[j][0] < b:
            ov = min(b, tr[j][1]) - max(a, tr[j][0])
            if ov > 0:
                cover[tr[j][2]] = cover.get(tr[j][2], 0.0) + ov
            j += 1
        if not cover:
            continue
        ranked = sorted(cover.items(), key=lambda kv: -kv[1])
        dur = b - a
        if ranked[0][1] / dur >= 0.5:
            out[i] = ranked[0][0]
        elif len(ranked) > 1 and ranked[0][1] / dur >= 0.35 and ranked[1][1] / dur >= 0.35:
            out[i] = "varios"
        else:
            out[i] = ranked[0][0]
    return out


def mic_timeline(levels, paso=MIC_PASO):
    """Serie del micro de la grabadora propia → {"paso", "activo": "0110…"} (1 = micro activo en ese paso).
    levels: iterable de (t, mic_db, sys_db|None). Activo si mic > -40 dB y mic > sys + 6 dB. Sin biometria."""
    bits = bytearray()
    for t, mic, sysdb in levels:
        if mic is None or t is None:
            continue
        i = int(float(t) / paso)
        if i >= len(bits):
            bits.extend(b"0" * (i + 1 - len(bits)))
        if mic > MIC_DB and (sysdb is None or mic > sysdb + MIC_SOBRE_SYS_DB):
            bits[i] = ord("1")
    return {"paso": paso, "activo": bits.decode("ascii")}


def close_gaps(bits):
    """Un paso suelto sin actividad entre dos activos cuenta como activo: los niveles llegan cada ~0,28 s y los pasos
    son de 0,25 s, asi que ~1 de cada 6 pasos se queda sin nivel (medido: 9 de 49) aunque se este hablando."""
    if "101" not in bits:
        return bits
    b = bytearray(bits, "ascii")
    for i in range(1, len(b) - 1):
        if b[i] == 48 and b[i - 1] == 49 and b[i + 1] == 49:
            b[i] = 49
    return b.decode("ascii")


def mic_ratios(tramos, micro, skip=()):
    """Fraccion de la voz de cada hablante con el micro activo, {h: 0..1}; {} sin linea de tiempo. Lo que el desfase
    deja fuera (antes del primer paso o justo despues del ultimo) no cuenta; mas alla del final cuenta como inactivo."""
    if not micro or not isinstance(micro.get("activo"), str) or not micro["activo"]:
        return {}
    act = close_gaps(micro["activo"])
    try:  # un sidecar tocado a mano o danado no impide identificar
        paso, shift = float(micro.get("paso") or MIC_PASO), float(micro.get("desfase", MIC_DESFASE_S) or 0.0)
    except (TypeError, ValueError):
        paso, shift = MIC_PASO, MIC_DESFASE_S
    paso = paso if math.isfinite(paso) and paso > 0 else MIC_PASO
    shift = min(max(0.0, shift), 10.0) if math.isfinite(shift) else MIC_DESFASE_S
    unknown_end = len(act) + int(math.ceil(shift / paso))
    tot, on = {}, {}
    for t in tramos:
        h = t["h"]
        if h in skip:
            continue
        a, b = float(t["inicio"]) - shift, float(t["fin"]) - shift
        for i in range(max(0, int(math.floor(a / paso))), int(math.ceil(b / paso))):
            if len(act) <= i < unknown_end:
                continue
            tot[h] = tot.get(h, 0) + 1
            if i < len(act) and act[i] == "1":
                on[h] = on.get(h, 0) + 1
    return {h: on.get(h, 0) / n for h, n in tot.items() if n}


def pick_user(ratios):
    """El hablante que coincide >= 60 % con el micro activo (y el siguiente <= 30 %), o None."""
    ranked = sorted(((r, h) for h, r in ratios.items()), reverse=True)
    if ranked and ranked[0][0] >= 0.6 and (len(ranked) < 2 or ranked[1][0] <= 0.3):
        return ranked[0][1]
    return None


def mark_user(tramos, micro, skip=()):
    """Hablante que coincide >= 60 % con el micro activo (y el siguiente <= 30 %), o None."""
    return pick_user(mic_ratios(tramos, micro, skip))


# ----------------------------------------------------------------------------- voces.json (personas y huellas)

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def name_key(nombre):
    """Clave para comparar nombres de personas: sin tildes, sin mayusculas y con los espacios normalizados."""
    s = unicodedata.normalize("NFKD", " ".join(str(nombre or "").split()))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


class Voices:
    """DATA_DIR/voces.json: personas con sus huellas. Cifrado con DPAPI (ambito usuario) en Windows; escritura
    atomica con copia .bak (que se elimina al olvidar o fusionar: ninguna copia con huellas sobrevive a un borrado).
    Las huellas solo entran por remember(), que exige el consentimiento."""

    EMPTY = {"version": 1, "modelo": MODEL_ID, "dim": DIM, "umbral": UMBRAL, "contador": 0, "personas": []}

    def __init__(self, path):
        self.path, self.lock, self.error = path, threading.RLock(), None
        self.data = json.loads(json.dumps(self.EMPTY))
        if not os.path.isfile(path):
            return
        try:
            raw = read_text(path).strip()
            text = unprotect(raw).decode("utf-8") if raw.startswith(("dpapi:", "plain:")) else raw
            saved = json.loads(text)
            if not isinstance(saved, dict) or not isinstance(saved.get("personas"), list):
                raise ValueError("formato desconocido")
            self.data.update(saved)
        except Exception as e:  # no se pisa: se aparta y se empieza de cero
            self.error = f"{e.__class__.__name__}: {e}"
            log(f"   voces.json no se pudo leer ({self.error}); se aparta como voces.json.ilegible")
            try:
                os.replace(path, path + ".ilegible")
            except OSError:
                pass

    def save(self, backup=True):
        """backup=False al olvidar o fusionar: el .bak (y un .ilegible apartado) guardarian huellas ya borradas."""
        with self.lock:
            body = protect(json.dumps(self.data, ensure_ascii=False).encode("utf-8"))
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
            if backup and os.path.isfile(self.path):
                try:
                    os.replace(self.path, self.path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, self.path)
            if not backup:
                for suffix in (".bak", ".ilegible"):
                    try:
                        os.remove(self.path + suffix)
                    except OSError:
                        pass

    # --- lectura
    def personas(self):
        with self.lock:
            return [json.loads(json.dumps(p)) for p in self.data["personas"]]

    def public(self):
        """Para la interfaz: sin vectores."""
        out = []
        for p in self.personas():
            fechas = [h.get("fecha") or "" for h in p.get("huellas") or []]
            out.append({"id": p["id"], "nombre": p["nombre"], "creada": p.get("creada"),
                        "es_usuario": bool(p.get("es_usuario")), "consentimiento": p.get("consentimiento"),
                        "huellas": len(p.get("huellas") or []), "ultima": max(fechas) if fechas else None,
                        "mezcla": mixed(p.get("huellas")),
                        "reuniones": sorted({h.get("reunion") for h in p.get("huellas") or [] if h.get("reunion")})})
        return out

    def names(self):
        with self.lock:
            return {p["id"]: p["nombre"] for p in self.data["personas"]}

    def find(self, pid):
        with self.lock:
            return next((p for p in self.data["personas"] if p["id"] == pid), None)

    def user(self):
        with self.lock:
            return next((p for p in self.data["personas"] if p.get("es_usuario")), None)

    def by_name(self, nombre):
        """La persona que ya se llama asi (sin distinguir mayusculas, tildes ni espacios de mas), o None. Asi elegir
        «Ana» de la lista asigna la Ana que ya existe en vez de crear otra. Nunca devuelve la del usuario: un compañero
        puede llamarse como el, y antes del 07/11/2026 la persona del usuario pudo quedarse con el nombre de otra
        (el fallo del msg 1170); el usuario se marca con «Soy yo»."""
        key = name_key(nombre)
        if not key:
            return None
        with self.lock:
            hit = next((p for p in self.data["personas"]
                        if not p.get("es_usuario") and name_key(p.get("nombre")) == key), None)
            return json.loads(json.dumps(hit)) if hit else None

    # --- escritura
    def create(self, nombre=None, es_usuario=False, consentimiento=None):
        with self.lock:
            self.data["contador"] = int(self.data.get("contador") or 0) + 1
            nombre = " ".join((nombre or "").split()) or f"Persona {self.data['contador']}"
            p = {"id": f"p_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}", "nombre": nombre, "creada": _now(),
                 "es_usuario": bool(es_usuario), "consentimiento": consentimiento, "huellas": []}
            if es_usuario:
                for q in self.data["personas"]:
                    q["es_usuario"] = False
            self.data["personas"].append(p)
            self.save()
            return json.loads(json.dumps(p))

    def remember(self, pid, vector, segundos, reunion, consentimiento):
        """Anade una huella (vector de DIM) a la persona. Sin consentimiento no se guarda nada."""
        if not consentimiento:
            raise ValueError("Falta el consentimiento para guardar la huella de voz")
        vector = [round(float(x), 5) for x in vector]
        if len(vector) != int(self.data.get("dim") or DIM):
            raise ValueError(f"La huella no tiene {DIM} valores")
        with self.lock:
            p = self.find(pid)
            if not p:
                raise KeyError(pid)
            p.setdefault("huellas", []).append({"vector": vector, "fecha": _now(), "reunion": reunion or "",
                                                "segundos": round(float(segundos or 0), 1)})
            p["huellas"].sort(key=lambda h: h.get("fecha") or "")
            del p["huellas"][:-MAX_HUELLAS]
            if not p.get("consentimiento"):
                p["consentimiento"] = consentimiento if isinstance(consentimiento, str) else _now()
            self.save()
            return len(p["huellas"])

    def drop_similar(self, pid, vector, umbral=0.8):
        """Quita de la persona las huellas casi iguales a `vector` (coseno >= umbral: la misma voz por el mismo canal da
        0,97-0,99 y dos personas distintas rara vez pasan de 0,8) y devuelve cuantas. Para la del
        usuario cuando dice que una voz que la app tomaba por la suya es de otra persona: esa huella es la de esa
        persona (antes del 07/11/2026 se guardaba asi por error). Sin copia .bak de lo quitado."""
        with self.lock:
            p = self.find(pid)
            if not p or vector is None:
                return 0
            keep = [h for h in p.get("huellas") or [] if not h.get("vector") or cosine(h["vector"], vector) < umbral]
            n = len(p.get("huellas") or []) - len(keep)
            if n:
                p["huellas"] = keep
                self.save(backup=False)
            return n

    def rename(self, pid, nombre):
        nombre = " ".join((nombre or "").split())
        if not nombre:
            raise ValueError("El nombre no puede estar vacio")
        with self.lock:
            p = self.find(pid)
            if not p:
                raise KeyError(pid)
            p["nombre"] = nombre[:80]
            self.save()

    def set_user(self, pid, flag=True):
        with self.lock:
            p = self.find(pid)
            if not p:
                raise KeyError(pid)
            if flag:
                for q in self.data["personas"]:
                    q["es_usuario"] = False
            p["es_usuario"] = bool(flag)
            self.save()

    def merge(self, keep, other):
        """Dos «Personas» eran la misma: las huellas de other pasan a keep (las 5 mas recientes) y other desaparece.
        Devuelve el id eliminado (para reescribir los sidecars que lo citen)."""
        if keep == other:
            return None
        with self.lock:
            a, b = self.find(keep), self.find(other)
            if not a or not b:
                raise KeyError(other if a else keep)
            if a.get("es_usuario") or b.get("es_usuario"):  # dentro de otra la haria «Tu» con sus huellas; al reves,
                raise ValueError("Tu persona no se puede fundir con otra")  # tu persona se quedaria con otra voz
            both = (a.get("huellas") or []) + (b.get("huellas") or [])
            a["huellas"] = sorted(both, key=lambda h: h.get("fecha") or "")
            del a["huellas"][:-MAX_HUELLAS]
            a["es_usuario"] = bool(a.get("es_usuario") or b.get("es_usuario"))
            a["consentimiento"] = a.get("consentimiento") or b.get("consentimiento")
            self.data["personas"] = [p for p in self.data["personas"] if p["id"] != other]
            self.save(backup=False)
            return other

    def forget(self, pid):
        with self.lock:
            before = len(self.data["personas"])
            self.data["personas"] = [p for p in self.data["personas"] if p["id"] != pid]
            if len(self.data["personas"]) == before:
                raise KeyError(pid)
            self.save(backup=False)

    def forget_all(self):
        with self.lock:
            self.data["personas"] = []
            self.save(backup=False)


# ----------------------------------------------------------------------------- sidecar <audio>.hablantes.json

def sidecar_path(audio_abs):
    return os.path.splitext(audio_abs)[0] + SIDECAR_EXT


def read_sidecar(path):
    try:
        data = json.loads(read_text(path))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _strip_vectors(obj):
    if isinstance(obj, dict):
        return {k: _strip_vectors(v) for k, v in obj.items() if k not in ("huellas", "vector", "vectores", "huella")}
    if isinstance(obj, list):
        return [_strip_vectors(x) for x in obj]
    return obj


def write_sidecar(path, data):
    """Junto al audio (OneDrive incluido): se escribe en el sitio, como el .txt y el .srt, y NUNCA con vectores."""
    data = _strip_vectors(data)
    data.setdefault("version", 1)
    write_bytes(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return data


def write_micro(path, micro):
    """Al parar la grabadora propia: guarda la linea de tiempo del micro (sin tramos; no es biometria)."""
    data = read_sidecar(path) or {"version": 1}
    data["micro"] = micro
    return write_sidecar(path, data)


def identify(data, huellas, voices, micro=None):
    """Completa hablantes{persona, score, provisional, origen} contra voces.json y «tu» por el micro. Modifica data.
    origen: "huella" (emparejada con voces.json), "micro" (el usuario, por el micro) o "manual" (assign()).

    Cuando el micro y la huella no cuadran no se decide solo, se pregunta (provisional): el micro dice «tu» y la
    huella dice otra persona; o la huella dice «usuario» y ese hablante casi nunca habla por el micro."""
    personas = voices.personas() if voices else []
    u = voices.user() if voices else None
    uid = u["id"] if u else None
    u_mixed = bool(u and mixed(u.get("huellas")))  # puede llevar la voz de otra persona: no se decide por huella
    hablantes = data.setdefault("hablantes", {})
    for h, info in hablantes.items():
        info.setdefault("persona", None)
        info.setdefault("score", None)
        info.setdefault("provisional", False)
        secs = float(info.get("segundos") or 0)
        if info.get("breve") or secs < MIN_MATCH_S or h not in huellas:
            continue
        m = match(huellas[h], personas)
        info.update(persona=m["persona"], score=m["score"], provisional=bool(m["provisional"]) or secs < MIN_HUELLA_S,
                    segunda=m["segunda"], origen="huella" if m["persona"] else None)
    micro = micro or data.get("micro")
    if micro:
        data["micro"] = micro
    breve = {h for h, i in hablantes.items() if i.get("breve")}
    ratios = mic_ratios(data.get("tramos", []), micro, skip=breve) if micro else {}
    if micro:
        data["tu"] = pick_user(ratios)
    tu = data.get("tu")
    for h, info in hablantes.items():
        if info.get("origen") != "huella" or not info.get("persona"):
            continue
        if info["persona"] == uid:  # salvo que el micro lo confirme (h == tu)
            if h != tu and (u_mixed or (h in ratios and ratios[h] <= MIC_NO_ES_TU)):
                info["provisional"] = True
        elif h == tu:
            info["provisional"] = True
    if tu and uid:
        info = hablantes.get(tu)
        if info and not info.get("persona"):  # el micro ya dice que eres tu: no hace falta huella
            info.update(persona=uid, provisional=False, origen="micro")
    return data


def assign(data, h, pid, usuario=None):
    """El usuario dice quien es el hablante h en esta reunion (pid, o None = nadie). Modifica data y mantiene «tu»:
    si pid es el usuario, h pasa a ser «tu» y deja de serlo quien lo era solo por el micro; si no, h deja de ser
    «tu». Nunca toca voces.json."""
    hablantes = data.get("hablantes") or {}
    info = hablantes.get(h)
    if info is None:
        raise KeyError(h)
    old_tu = data.get("tu")
    info.update(persona=pid, provisional=False, origen="manual")
    if pid and pid == usuario:
        data["tu"] = h
        for k, other in hablantes.items():  # sin «origen» (sidecar de antes del 07/11/2026): el que era «tu»
            by_micro = other.get("origen") == "micro" or (not other.get("origen") and k == old_tu)
            if k != h and other.get("persona") == usuario and by_micro:
                other.update(persona=None, provisional=False, origen=None)
    elif data.get("tu") == h:
        data["tu"] = None
    return data


def carry_manual(old, new, usuario=None, known=None):
    """Volver a identificar no borra lo que el usuario ya dijo: cada hablante nuevo hereda la decision manual del
    hablante antiguo con el que mas coincide en el tiempo (si cubre al menos la mitad de su voz), salvo que esa
    persona ya no exista (`known`: ids que siguen en voces.json). Modifica new."""
    olds = {h: i for h, i in (old.get("hablantes") or {}).items() if i.get("origen") == "manual"}
    if not olds or not new.get("tramos"):
        return new
    step = 0.1  # mascaras de 0,1 s: lineal en la duracion (dos horas son 72.000 casillas por hablante)
    end = min(max(float(t["fin"]) for t in (old.get("tramos") or []) + new["tramos"]), 12 * 3600.0)
    n = int(math.ceil(end / step)) + 1

    def mask(tramos, h):
        m = np.zeros(n, bool)
        for t in tramos:
            if t.get("h") == h:
                m[max(0, int(float(t["inicio"]) / step)):max(0, int(math.ceil(float(t["fin"]) / step)))] = True
        return m
    old_masks = {k: mask(old.get("tramos") or [], k) for k in olds}
    for h in list(new.get("hablantes") or {}):
        mine = mask(new["tramos"], h)
        total = int(mine.sum())
        if not total:
            continue
        best, cover = max(((k, int((mine & m).sum())) for k, m in old_masks.items()), key=lambda x: x[1])
        if cover / total >= 0.5:
            pid = olds[best].get("persona")
            if pid and known is not None and pid not in known:
                continue
            assign(new, h, pid, usuario)
    return new


def refresh_frases(data, segs):
    """Casado con el .srt (clave: inicio de la frase, como seg_key en server.py). Barato: se rehace al leer."""
    who = assign_srt(segs, data.get("tramos", []))
    data["frases"] = [{"inicio": round(float(s["start"]), 2), "h": w} for s, w in zip(segs, who)
                      if s.get("start") is not None]
    return who


def label(h, data, names, for_llm=False, usuario=None):
    """Etiqueta de un hablante. Manda la persona asignada (a mano, por huella o por el micro): su nombre, o «Tú» si es
    la del usuario (`usuario` = su id; en el resumen, su nombre o «Yo»), con «¿…?» si es provisional. Sin persona,
    «Tú» si el micro lo dice; si no, «(breve)» o «Persona N». También «varios» y None."""
    if h is None:
        return None
    if h == "varios":
        return "varios"
    info = (data.get("hablantes") or {}).get(h) or {}
    pid = info.get("persona")
    name = names.get(pid) if pid else None  # None tambien si la persona se olvido
    doubt = bool(info.get("provisional"))
    if name is not None and (name or pid == usuario):
        if pid == usuario:
            shown = (name or "Yo") if for_llm else "Tú"
        else:
            shown = name
        return f"¿{shown}?" if doubt else shown
    if data.get("tu") == h:
        return "Yo" if for_llm else "Tú"
    if info.get("breve"):
        return "(breve)"
    n = h[1:] if h.startswith("h") and h[1:].isdigit() else h
    return f"Persona {n}"


def is_user(h, data, names, usuario=None):
    """El hablante h es el usuario en esta reunion: su persona es la del usuario o, sin persona, el micro lo dice."""
    info = (data.get("hablantes") or {}).get(h) or {}
    pid = info.get("persona")
    if pid and pid in names:
        return pid == usuario
    return data.get("tu") == h


def export_text(segs, who, data, names, usuario=None):
    """<audio>.hablantes.txt: un parrafo por turno, con la etiqueta delante y el minuto del primer segmento."""
    out, cur, cur_h, t0 = [], [], object(), None
    for s, w in zip(segs, who):
        if w != cur_h and cur:
            out.append((cur_h, t0, " ".join(cur)))
            cur = []
        if not cur:
            cur_h, t0 = w, s.get("start")
        cur.append(s["text"])
    if cur:
        out.append((cur_h, t0, " ".join(cur)))
    lines = []
    for h, t, text in out:
        stamp = "" if t is None else f"[{int(t) // 60}:{int(t) % 60:02d}] "
        who_ = label(h, data, names, usuario=usuario)
        lines.append(f"{stamp}{who_ + ': ' if who_ else ''}{text}")
    return "\n\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- CLI (subproceso)

def main(argv=None):
    ap = argparse.ArgumentParser(description="Diarizacion en local (subproceso de la app)")
    ap.add_argument("--diarize", required=True, metavar="AUDIO")
    ap.add_argument("--out", required=True, metavar="SIDECAR")
    ap.add_argument("--niveles", metavar="FICHERO", help="JSON {paso, activo} de la linea de tiempo del micro")
    ap.add_argument("--data", default=os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                                                   "AudiosApp"))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--fingerprint", metavar="H", help="solo recalcular la huella de ese hablante del sidecar")
    a = ap.parse_args(argv)
    proto = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # cualquier print de las librerias nativas va a stderr y no rompe el protocolo

    def send(obj):
        proto.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        proto.flush()

    def progress(stage=None, frac=None):
        msg = {"type": "progress"}
        if stage is not None:
            msg["stage"] = stage
        if frac is not None:
            msg["frac"] = round(float(frac), 4)
        send(msg)

    try:
        progress(stage="Leyendo audio…", frac=0.01)
        samples = load_audio_16k(a.diarize)
        if a.fingerprint:
            data = read_sidecar(a.out) or {}
            tramos = [(float(t["inicio"]), float(t["fin"])) for t in data.get("tramos", [])
                      if t.get("h") == a.fingerprint]
            if not tramos:
                raise VoicesError(f"El sidecar no tiene tramos de {a.fingerprint}")
            so = _sherpa()
            vec, secs = fingerprint(_extractor(so, model_paths(a.data), a.threads), samples, tramos)
            if vec is None:
                raise VoicesError("Voz insuficiente para una huella")
            send({"type": "result", "huellas": {a.fingerprint: [round(float(x), 5) for x in vec]}, "segundos": secs})
            return 0
        res = diarize(samples, a.data, a.threads, progress)
        micro = None
        if a.niveles:
            try:
                micro = json.loads(read_text(a.niveles))
            except (OSError, ValueError):
                micro = None
        old = read_sidecar(a.out) or {}
        data = {"version": 1, "modelo": MODEL_ID, "creado": _now(), "duracion_s": res["duracion_s"],
                "tramos": res["tramos"], "hablantes": res["hablantes"], "tu": None, "frases": [],
                "micro": micro or old.get("micro")}
        breve = {h for h, i in data["hablantes"].items() if i.get("breve")}
        data["tu"] = mark_user(data["tramos"], data["micro"], skip=breve) if data["micro"] else None
        write_sidecar(a.out, data)
        send({"type": "result", "sidecar": a.out, "huellas": res["huellas"], "hablantes": len(res["hablantes"]),
              "duracion_s": res["duracion_s"]})
        return 0
    except Exception as e:
        send({"type": "error", "error": str(e) or e.__class__.__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
