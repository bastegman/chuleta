# -*- coding: utf-8 -*-
"""
Motores de transcripcion.

  LocalEngine  -> faster-whisper en la CPU del portatil (modo por lotes = mucho mas rapido)
  CloudEngine  -> API compatible con OpenAI: Groq, OpenAI o Azure OpenAI.
                  El audio se convierte a FLAC 16 kHz mono, se corta en trozos por silencios
                  y los trozos se transcriben en paralelo.

Ambos devuelven una lista de segmentos (inicio, fin, texto).
"""
import glob
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from time import perf_counter  # aparte de `time`: los tests le ponen un sleep falso
from concurrent.futures import ThreadPoolExecutor, as_completed

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Whisper en local con prioridad baja: Teams, Outlook… van primero y el portatil no da tirones
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


class Cancelled(Exception):
    pass


class EngineError(Exception):
    pass


# ----------------------------------------------------------------------------- conexiones HTTP (keep-alive)
CONNECT_TIMEOUT = 10  # s hasta tener la conexion (DNS + TCP + TLS); la lectura lleva su propio tope en cada peticion
BUDGET_MIN_S = 3.0  # con plazo (Summarizer._chat, budget): si despues no quedarian 3 s, ni se espera ni se pide nada
BUDGET_SLACK_S = 0.5  # una espera, ademas, deja este margen: al despertar siempre se pasa algo, y no espera en balde


class Http:
    """Una requests.Session por hilo, con keep-alive. En el directo sale una peticion cada 3-6 s y, sin sesion,
    cada una abre conexion nueva (DNS + TCP + TLS) y la paga entera. Session no es segura entre hilos, asi que
    cada hilo (cola, directo, traduccion, resumen) tiene la suya. El adaptador no reintenta nada: los reintentos
    los deciden _post y _chat, que saben de cupo y de retry-after. Si `requests` no trae Session (los tests lo
    sustituyen por un doble que solo tiene post), se usa requests.post tal cual."""

    POOL = 4  # conexiones que guarda cada sesion (un hilo tiene como mucho una en vuelo; con margen)

    def __init__(self):
        self.local = threading.local()

    def session(self):
        import requests
        if not hasattr(requests, "Session"):
            return requests
        s = getattr(self.local, "session", None)
        if s is None or getattr(self.local, "module", None) is not requests:
            s = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=self.POOL, max_retries=0)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            self.local.session, self.local.module = s, requests
        return s

    def post(self, url, read_timeout, connect_timeout=None, **kw):
        """POST con timeouts por fase: connect_timeout (por defecto CONNECT_TIMEOUT) para conectar y read_timeout para
        la respuesta."""
        return self.session().post(url, timeout=(CONNECT_TIMEOUT if connect_timeout is None else connect_timeout,
                                                 read_timeout), **kw)


HTTP = Http()


def processing_s(headers):
    """Tiempo de proceso que declara el servidor, si lo declara (p. ej. openai-processing-ms), en segundos."""
    for k, v in (headers or {}).items():
        k = str(k).lower()
        if "processing" in k and k.endswith("-ms"):
            try:
                return float(v) / 1000
            except (TypeError, ValueError):
                pass
    return None


# speed = veces tiempo real medidas en un portatil reciente (Core Ultra 7 265U, 2025); la app las va
# ajustando con cada transcripcion real.
# En directo todos van por lotes y beam 1: live_speed es lo que rinden asi (medido salvo large-v3).
PRESETS = [
    {"id": "rapido", "icon": "⚡", "label": "Rápido", "engine": "local",
     "model": "small", "batched": True, "beam": 1, "speed": 7.7, "live_label": "Rápido · small", "live_speed": 7.7,
     "hint": "Lo mejor en un portátil reciente: ~8 min por hora de audio y bien puntuado. "
             "Puede fallar en nombres propios."},
    {"id": "turbo", "icon": "⚖", "label": "Preciso", "engine": "local",
     "model": "large-v3-turbo", "batched": True, "beam": 1, "speed": 2.3, "live_label": "Preciso · turbo",
     "live_speed": 2.3,
     "live_note": "En directo a veces se salta el arranque de una frase; para el directo, mejor Normal.",
     "hint": "Más fino con nombres, siglas y acentos. ~3 veces más lento que Rápido (~25 min por hora)."},
    {"id": "medium", "icon": "•", "label": "Clásico", "engine": "local",
     "model": "medium", "batched": False, "beam": 5, "speed": 0.9, "live_label": "Normal · medium",
     "live_speed": 2.3, "live_note": "Recomendado para el directo: preciso y va al día.",
     "hint": "El que usabas hasta ahora: ~1 minuto por minuto de audio."},
    {"id": "calidad", "icon": "🎯", "label": "Máxima", "engine": "local",
     "model": "large-v3", "batched": True, "beam": 5, "speed": 0.5, "live_label": "Máxima · large-v3",
     "live_speed": 0.8,
     "hint": "large-v3 completo. Muy lento en un portátil normal (~2 h por hora de audio); "
             "solo para audios difíciles."},
    {"id": "nube", "icon": "☁", "label": "Nube", "engine": "cloud", "speed": 60.0, "live_label": "Nube",
     "live_speed": 30.0,
     "hint": "Whisper large-v3 en servidores con GPU: una reunión de 1 h en ~1 minuto."},
]
PRESETS_BY_ID = {p["id"]: p for p in PRESETS}

PROVIDERS = {
    "groq": {"label": "Groq", "url": "https://api.groq.com/openai/v1/audio/transcriptions",
             "models": ["whisper-large-v3-turbo", "whisper-large-v3"],
             "keys": "https://console.groq.com/keys"},
    "openai": {"label": "OpenAI", "url": "https://api.openai.com/v1/audio/transcriptions",
               "models": ["whisper-1"], "keys": "https://platform.openai.com/api-keys"},
    "azure": {"label": "Azure OpenAI", "url": "", "models": [], "keys": ""},
    # pasarela interna, proxy de empresa, LiteLLM, vLLM... cualquier API con el formato de OpenAI
    "custom": {"label": "Otra (compatible con OpenAI)", "url": "", "models": ["whisper-large-v3"], "keys": ""},
}
LIVE_MODEL_DEFAULT = "whisper-large-v3-turbo"  # directo en Groq: ~1 s por trozo; el repaso final usa cloud.model
# directo en la nube: pocos intentos por trozo y esperas cortas (un trozo que no sale se salta y lo recupera el repaso
# final, en vez de retener el directo minutos) y la lectura segun lo largo que sea el trozo: 20 s + 0,2 s por segundo
# de audio (los de ponerse al dia llegan a 10 min y la API tarda mas en contestarlos)
LIVE_ATTEMPTS = 2
LIVE_MAX_WAIT_S = 30.0
LIVE_READ_S = 20.0
LIVE_READ_PER_S = 0.2


def base_url(cfg):
    """URL base (…/v1) del proveedor 'custom'; admite que se pegue la URL completa de un endpoint."""
    u = (cfg.get("base_url") or "").strip().rstrip("/")
    for tail in ("/audio/transcriptions", "/chat/completions"):
        if u.endswith(tail):
            u = u[:-len(tail)]
    return u


# Frases que Whisper "se inventa" en silencios (vienen de subtitulos de YouTube en su entrenamiento)
HALLUCINATION = re.compile(
    r"amara\.org|subt[ií]tulos (realizados|por|creados)|suscr[ií]bete|gracias por ver|"
    r"thanks for watching|legendas pela comunidade|obrigad[oa] por assistir", re.I)


# En modo por lotes Whisper no ve el texto anterior y tiende a escribir en minusculas y sin
# puntuacion. Una frase de contexto bien puntuada en el idioma de la reunion lo corrige.
STYLE_PROMPT = {
    "es": "Hola, ¿qué tal? Bueno, vale, empezamos con la reunión.",
    "en": "Hello, how are you? Okay, so let's start the meeting.",
    "pt": "Olá, tudo bem? Então, vamos começar a reunião.",
    "fr": "Bonjour, comment ça va ? Bon, on commence la réunion.",
    "it": "Ciao, come stai? Allora, iniziamo la riunione.",
    "de": "Hallo, wie geht's? Also, fangen wir mit dem Meeting an.",
    "ca": "Hola, què tal? Bé, comencem la reunió.",
    # espanol + ingles mezclados (spanglish): ensena a Whisper a dejar cada palabra en su idioma
    # en vez de traducirla ("el deadline del workstream", no "la fecha límite del flujo de trabajo")
    "mix": "Vale, perfecto. So, el deadline del workstream es el viernes, ¿no? Okay, let's move on con el roadmap.",
    # otras mezclas de dos idiomas (reuniones con hablantes de cada uno): misma idea, cada frase en su idioma.
    # Sin validar aun con audio real; si una reunion es solo en un idioma, fijarlo es mas preciso.
    "es-pt": "Vale, perfecto. Então vamos começar com a agenda de hoje. ¿Os parece bien? Sim, pode seguir.",
    "en-pt": "Okay, perfect. Então vamos começar com a agenda de hoje. Does that work for everyone? Sim, pode seguir.",
}
PAIRS = {"es-en": ("es", "en"), "es-pt": ("es", "pt"), "en-pt": ("en", "pt")}  # mezclas admitidas: "xx-yy"


def lang_pair(language):
    """("es", "en") para una mezcla de dos idiomas ("es-en"), None para un idioma fijo o la deteccion."""
    return PAIRS.get(language or "")


def style_for(language):
    """Frase de estilo para ese idioma o mezcla ("" si se detecta: el motor pone la suya al saber el idioma)."""
    if language == "es-en":
        return STYLE_PROMPT["mix"]
    return STYLE_PROMPT.get(language or "", "")


# Groq/OpenAI devuelven el idioma con su nombre en ingles
LANG_CODES = {"spanish": "es", "english": "en", "portuguese": "pt", "french": "fr", "italian": "it",
              "german": "de", "catalan": "ca", "es": "es", "en": "en", "pt": "pt", "fr": "fr", "it": "it",
              "de": "de", "ca": "ca"}


# --- prompt = frase de estilo + vocabulario
# Whisper solo lee los ultimos 223 tokens del prompt (faster-whisper, Groq y OpenAI igual) y si se pasa
# corta por el PRINCIPIO, que es la frase de estilo (la que da puntuacion y spanglish). Groq ademas no
# admite mas de 896 BYTES (la ñ y las tildes ocupan 2).
PROMPT_TOKENS = 223
CLOUD_PROMPT_BYTES = 860
_TOKENIZER = {}


def _load_tokenizer():
    try:
        import glob
        from tokenizers import Tokenizer
        files = glob.glob(os.path.join(hf_hub_cache(), "models--*whisper*", "snapshots", "*", "tokenizer.json"))
        if MODELS is not None:  # los que descarga la app (DATA_DIR/models/whisper/<modelo>)
            files += glob.glob(os.path.join(glob.escape(MODELS.root), "*", "tokenizer.json"))
        if files:
            _TOKENIZER["t"] = Tokenizer.from_file(sorted(files)[-1])
    except Exception:
        pass


def _whisper_tokenizer(wait=False):
    """Tokenizador de algun modelo ya descargado, para contar tokens sin cargar Whisper. La primera vez se carga en
    un hilo aparte (importar `tokenizers` puede tardar) y mientras tanto count_tokens estima: una peticion de la
    interfaz (/api/vocab/check) no espera a una importacion. `wait` la carga en el propio hilo (tests)."""
    if "t" not in _TOKENIZER:
        _TOKENIZER["t"] = None
        if wait:
            _load_tokenizer()
        else:
            threading.Thread(target=_load_tokenizer, daemon=True, name="tokenizer").start()
    return _TOKENIZER["t"]


def count_tokens(text):
    text = (text or "").strip()
    if not text:
        return 0
    tok = _whisper_tokenizer()
    if tok:
        return len(tok.encode(" " + text, add_special_tokens=False).ids)
    return -(-len(text.encode("utf-8")) * 10 // 26)  # sin modelo descargado: ~2,6 bytes por token (por lo alto)


def split_terms(vocab):
    return [t.strip() for t in re.split(r"[,;\n]+", vocab or "") if t.strip()]


CONTEXT_TOKENS = 60  # en directo: lo ultimo que se dijo en el trozo anterior (al final del prompt)
ABOUT_TOKENS = 100   # contexto libre de la reunion (de que va, quien participa): tope propio dentro del prompt
ABOUT_CHARS = 500


def _largest(n, ok):
    """Mayor k en [0, n] con ok(k) (ok es monotona: si cabe k, cabe k-1). Busqueda binaria: contar tokens cuesta."""
    if ok(n):
        return n
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid - 1)
    return lo


def build_prompt(style, vocab, count=count_tokens, max_bytes=None, limit=PROMPT_TOKENS, context=None, about=None):
    """Frase de estilo + vocabulario + contexto libre de la reunion (+ en directo, lo ultimo que se dijo) sin
    pasarse de lo que lee Whisper. Si no cabe, primero se recorta el contexto libre por el final (palabra a
    palabra, hasta quitarlo) y solo despues se quitan los ULTIMOS terminos del vocabulario; la frase de estilo
    no se toca. Lo ultimo dicho va al final, pegado al audio, como haria Whisper con el texto anterior: asi
    enlaza frases cortadas entre trozos y mantiene nombres y mayusculas. Devuelve (prompt, terminos que no caben)."""
    vocab = " ".join((vocab or "").split())
    terms, sep = split_terms(vocab), ", "
    if len(terms) <= 1:  # texto libre, sin comas: se recorta por palabras
        terms, sep = vocab.split(), " "
    ctx = " ".join((context or "").split())[-400:]  # ~60 tokens son unos 250 caracteres: se recorta antes de contar
    while ctx and count(ctx) > CONTEXT_TOKENS:  # se queda con el final
        ctx = ctx.split(" ", 1)[1] if " " in ctx else ""
    if max_bytes:
        ctx = ctx.encode("utf-8")[-max_bytes // 4:].decode("utf-8", "ignore").lstrip()
    words = " ".join((about or "").split())[:ABOUT_CHARS].split()
    words = words[:_largest(len(words), lambda k: count(" ".join(words[:k])) <= ABOUT_TOKENS)]

    def join(n, w=None):
        w = len(words) if w is None else w
        return " ".join(p for p in ((style or "").strip(), sep.join(terms[:n]), " ".join(words[:w]), ctx) if p)

    def fits(n, w=None):
        t = join(n, w)
        return count(t) <= limit and (not max_bytes or len(t.encode("utf-8")) <= max_bytes)

    n = len(terms)
    if not fits(n):
        words = words[:_largest(len(words), lambda w: fits(n, w))]  # primero cede el contexto libre...
        if not fits(n):
            n = _largest(n, fits)  # ...y si ni asi cabe, los ultimos terminos del vocabulario
    return join(n) or None, terms[n:]


def parse_verbose(res, offset, end):
    """Segmentos de una respuesta verbose_json, desplazados al tiempo real de la grabacion."""
    segs = []
    for s in res.get("segments") or []:
        # descarta lo que el propio modelo marca como "no es voz"
        if s.get("no_speech_prob", 0) > 0.85 and s.get("avg_logprob", 0) < -0.8:
            continue
        segs.append((offset + float(s["start"]), offset + float(s["end"]), s.get("text", "")))
    if not segs and (res.get("text") or "").strip():
        segs.append((offset, end, res["text"]))
    return segs


def write_wav16(path, audio):
    """WAV 16 kHz mono (para mandar un trozo a la nube sin lanzar ffmpeg)."""
    import wave
    import numpy as np
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)


LATIN = {"es", "en", "pt", "fr", "it", "de", "ca"}
# Sobre musica o ruido Whisper a veces "escribe subtitulos japoneses": ー…ー, 《…》, 「…」
CJK = re.compile(r"[　-〿぀-ヿ一-鿿＀-￯《》「」【】]")


def whisper_ok(s):
    """Filtro con las metricas del propio Whisper (mismo criterio que usa OpenAI para el silencio)."""
    if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
        return False
    return s.compression_ratio <= 2.4  # por encima: bucle de repeticion


def _vocab_echo(low, terms):
    """En silencios Whisper a veces 'transcribe' el vocabulario: 'Acme, Alfa, Beta, Gamma, Delta…'."""
    items = [i.strip(" .") for i in low.split(",") if i.strip(" .")]
    return len(items) >= 5 and sum(i in terms for i in items) >= 0.8 * len(items)


def clean_segments(segs, prompt=None, language=None):
    echo = {s.strip().lower() for s in re.split(r"(?<=[.?!])\s+", prompt or "") if len(s.strip()) > 8}
    terms = {t.lower() for t in split_terms(prompt)} if (prompt or "").count(",") >= 4 else set()
    out = []
    for start, end, text in segs:
        text = (text or "").strip()
        low = text.lower()
        if not text or (len(text) < 100 and HALLUCINATION.search(text)) or low in echo:
            continue
        if terms and _vocab_echo(low, terms):
            continue
        if (language in LATIN or language is None) and CJK.search(text):
            continue
        if out and len(text) < 60 and low == out[-1][2].lower():  # "I'm sorry" x7
            continue
        out.append((float(start), float(end), text))
    return out


# --- audio sin ffmpeg: PyAV ya viene con faster-whisper, asi la app no necesita instalar nada mas

def media_duration(path):
    """Duracion de cualquier audio o video (segundos), leyendo solo la cabecera."""
    try:
        import av
        with av.open(path) as c:
            if c.duration:
                return c.duration / 1_000_000
            s = next((s for s in c.streams if s.type == "audio"), None)
            if s is not None and s.duration and s.time_base:
                return float(s.duration * s.time_base)
    except Exception:
        return None
    return None


def _valid_frames(frames, av):
    """Los fotogramas que se pueden decodificar: uno danado a mitad se salta (como hace faster-whisper), en vez de
    cortar ahi el audio sin decirlo."""
    it = iter(frames)
    while True:
        try:
            yield next(it)
        except StopIteration:
            return
        except av.error.InvalidDataError:
            continue


def decode_16k(path, start=0.0, cancelled=lambda: False):
    """Cualquier audio/video a int16 16 kHz mono, frame a frame (la mitad de memoria que float32)."""
    import av
    import numpy as np
    skip, parts = int(start * 16000), []
    try:
        with av.open(path) as c:
            s = next((s for s in c.streams if s.type == "audio"), None)
            if s is None:
                raise EngineError("El archivo no tiene pista de audio.")
            rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
            frames = _valid_frames(c.decode(s), av)
            for fr in itertools.chain(frames, [None]):  # perezoso: no carga el archivo entero antes
                if cancelled():
                    raise Cancelled()
                for o in rs.resample(fr):
                    a = o.to_ndarray().reshape(-1)
                    if skip:  # continuar desde ese segundo
                        cut = min(skip, len(a))
                        a, skip = a[cut:], skip - cut
                    if len(a):
                        parts.append(a.copy())
    except (EngineError, Cancelled):
        raise
    except Exception as e:
        if not parts:
            raise EngineError(f"No se pudo leer el audio ({e.__class__.__name__}: {e})")
        # WAV cortado (p. ej. grabacion interrumpida): se usa lo que se pudo leer
    return np.concatenate(parts) if parts else np.zeros(0, np.int16)


def find_silences(y, sr=16000, thresh_db=-35.0, min_s=0.4):
    """Tramos de silencio (para cortar entre frases y no partir palabras)."""
    import numpy as np
    hop = sr // 20  # ventanas de 50 ms
    n = len(y) // hop
    if not n:
        return []
    scale = 32768.0 if y.dtype == np.int16 else 1.0
    rms = np.empty(n, np.float32)
    for i in range(0, n, 20000):  # por bloques: sin copiar el audio entero a float
        b = y[i * hop:min(n, i + 20000) * hop].astype(np.float32) / scale
        rms[i:i + len(b) // hop] = np.sqrt((b.reshape(-1, hop) ** 2).mean(axis=1))
    quiet = (20 * np.log10(rms + 1e-9) < thresh_db).astype(np.int8)
    edges = np.diff(np.concatenate([[0], quiet, [0]]))
    starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
    step = hop / sr
    return [(float(a * step), float(b * step)) for a, b in zip(starts, ends) if (b - a) * step >= min_s]


def write_flac(path, y, sr=16000):
    """FLAC 16 kHz mono sin perdida (la mitad que un WAV: se sube antes)."""
    import av
    import numpy as np
    pcm = y if y.dtype == np.int16 else (np.clip(y, -1, 1) * 32767).astype(np.int16)
    pcm = np.ascontiguousarray(pcm)
    with av.open(path, "w", format="flac") as out:
        st = out.add_stream("flac", rate=sr)
        st.codec_context.layout = "mono"
        st.codec_context.format = "s16"
        fr = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        fr.sample_rate = sr
        for pk in st.encode(fr):
            out.mux(pk)
        for pk in st.encode(None):
            out.mux(pk)


# ----------------------------------------------------------------------------- local

# Repositorios de Hugging Face de los modelos de los presets (lo que trae faster_whisper.utils._MODELS). Fijos aqui
# porque /api/config pregunta si estan descargados y NO puede importar faster_whisper para saberlo: el 02/11/2026 esa
# importacion, lenta tras una actualizacion y compartida por el cerrojo de importacion con el hilo del directo, tuvo
# /api/config 8 minutos sin contestar y la interfaz muerta (los botones no hacian nada).
HF_REPOS = {"tiny": "Systran/faster-whisper-tiny", "base": "Systran/faster-whisper-base",
            "small": "Systran/faster-whisper-small", "medium": "Systran/faster-whisper-medium",
            "large-v2": "Systran/faster-whisper-large-v2", "large-v3": "Systran/faster-whisper-large-v3",
            "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            "distil-large-v3": "Systran/faster-distil-whisper-large-v3"}
_MODEL_CACHED = {}  # modelo -> (hasta cuando vale, resultado): /api/config lo pide en cada carga de la interfaz
MODEL_CACHED_TTL_S = 30.0


def hf_hub_cache():
    """La cache de Hugging Face tal como la resuelve huggingface_hub.constants, sin importarlo: HF_HUB_CACHE (o
    HUGGINGFACE_HUB_CACHE), si no HF_HOME/hub, si no ~/.cache/huggingface/hub."""
    p = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if not p:
        home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
        p = os.path.join(home, "hub")
    return os.path.expanduser(p)


def model_cached(name, ttl=None):
    """True si el modelo ya esta en este PC: en la carpeta propia (MODELS) o en la cache de Hugging Face de una version
    anterior. Sin importar nada (HF_REPOS, hf_hub_cache) y con el resultado guardado un rato (MODEL_CACHED_TTL_S; al
    terminar una descarga se olvida)."""
    now = time.time()
    hit = _MODEL_CACHED.get(name)
    if hit and hit[0] > now:
        return hit[1]
    if MODELS is not None:
        found = MODELS.path(name) is not None
    else:
        found = hf_snapshot(name, need=("model.bin",)) is not None
    _MODEL_CACHED[name] = (now + (MODEL_CACHED_TTL_S if ttl is None else ttl), found)
    return found


def hf_snapshot(name, need=("model.bin", "config.json", "tokenizer.json")):
    """Carpeta de la cache de Hugging Face con ese modelo (lo descargo faster-whisper en una version anterior), o None.
    Por defecto, solo si tiene lo que faster-whisper necesita para cargarlo sin red: model.bin, config.json,
    tokenizer.json y el vocabulario. Sin importar la libreria de Hugging Face."""
    cache = hf_hub_cache()
    dirs = ["models--" + HF_REPOS[name].replace("/", "--")] if name in HF_REPOS else []
    try:  # por si el repositorio cambia de dueno: cualquier carpeta de la cache que acabe en el nombre del modelo
        dirs += [d for d in os.listdir(cache) if d.startswith("models--") and d.endswith("faster-whisper-" + name)]
    except OSError:
        pass
    for d in dict.fromkeys(dirs):
        snaps = os.path.join(cache, d, "snapshots")
        try:
            subs = [os.path.join(snaps, x) for x in os.listdir(snaps)]
        except OSError:
            continue
        for p in sorted(subs, key=lambda x: os.path.getmtime(x) if os.path.isdir(x) else 0, reverse=True):
            if all(os.path.isfile(os.path.join(p, f)) for f in need) \
                    and (len(need) < 3 or glob.glob(os.path.join(glob.escape(p), "vocabulary.*"))):
                return p
    return None


# ----------------------------------------------------------------------------- modelos de Whisper en este PC
# Hasta el 07/11/2026 los descargaba faster-whisper dentro de worker.py la primera vez que hacian falta: sin progreso
# (la interfaz decia «Descargando modelo medium (solo la primera vez)…» durante minutos: medium son 1,5 GB; medido en
# un Mac: tiny, 75 MB, 33 s con ese unico mensaje), con 1 h de tope por intento y desde un proceso sin truststore (los
# certificados de Windows), asi que en una red que inspecciona HTTPS podia fallar aunque Groq funcionara. Ahora los
# descarga el servidor con requests (certificados de Windows y proxies, como Groq) por HTTP simple: progreso en MB y %,
# reanudacion, tamano y sha256 comprobados, a DATA_DIR/models/whisper/<modelo>. worker.py solo carga carpetas locales
# y nunca toca la red. Lo que ya descargaron versiones anteriores (cache de Hugging Face) se usa sin descargar nada.
# Revision fija (commit) y huella del model.bin leidas de la API de Hugging Face el 07/11/2026.
WHISPER_URL = "https://huggingface.co/{repo}/resolve/{rev}/{file}"
_TOK = ("tokenizer.json", 2203239, None)
_VOC = ("vocabulary.txt", 459861, None)
WHISPER_MODELS = {
    "tiny": ("Systran/faster-whisper-tiny", "d90ca5fe260221311c53c58e660288d3deb8d356", (
        ("config.json", 2249, None),
        ("model.bin", 75538270, "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"), _TOK, _VOC)),
    "small": ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120", (
        ("config.json", 2370, None),
        ("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"), _TOK, _VOC)),
    "medium": ("Systran/faster-whisper-medium", "08e178d48790749d25932bbc082711ddcfdfbc4f", (
        ("config.json", 2257, None),
        ("model.bin", 1527906378, "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae"), _TOK, _VOC)),
    "large-v3": ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478", (
        ("config.json", 2394, None),
        ("model.bin", 3087284237, "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"),
        ("preprocessor_config.json", 340, None), ("tokenizer.json", 2480617, None),
        ("vocabulary.json", 1068114, None))),
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf", (
        ("config.json", 2263, None),
        ("model.bin", 1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
        ("preprocessor_config.json", 340, None), ("tokenizer.json", 2710337, None),
        ("vocabulary.json", 1068114, None))),
}
WHISPER_HOST = "huggingface.co"
DOWNLOAD_STALL_S = 60     # sin recibir nada en 60 s, la conexion se da por parada (se reintenta desde donde iba)
DOWNLOAD_RETRIES = 3      # intentos seguidos sin avanzar antes de rendirse (cada uno sigue donde se quedo)
DOWNLOAD_SPARE = 200e6    # espacio libre que se deja de margen, ademas de lo que falta por bajar
DOWNLOAD_BLOCK = 1 << 20  # 1 MB por escritura
LOAD_TIMEOUT_S = 600      # cargar un modelo ya descargado (el large-v3, 3 GB, con el antivirus mirandolo: minutos)
MODELS = None             # WhisperModels del servidor (DATA_DIR/models/whisper); sin el, como antes (tests, worker)


def fmt_mb(n):
    """1531 -> «1.531» (MB, con punto de miles como en espanol)."""
    return f"{n / 1e6:,.0f}".replace(",", ".")


def fmt_left(secs):
    if secs is None:
        return ""
    secs = max(0, int(secs))
    if secs >= 3600:
        return f"{secs // 3600} h {secs % 3600 // 60:02d} min"
    if secs >= 90:
        return f"{round(secs / 60)} min"
    return f"{max(5, round(secs / 5) * 5)} s"


def connection_error(e, host, what="la conexión"):
    """Una frase con lo que ha pasado al hablar con `host` y que hacer, a partir de la excepcion de requests (sin
    trazas). what: «la descarga», «la conexión»… Sirve para la descarga de modelos y para la nube. Mira los nombres de
    las clases (no importa requests: los tests lo sustituyen por un doble)."""
    names = {c.__name__ for c in type(e).__mro__}
    low = str(e).lower()
    if "SSLError" in names or "certificate verify failed" in low:
        return (f"La conexión segura con {host} ha fallado (no se acepta su certificado). En la red de una empresa "
                f"suele ser el proxy que revisa el tráfico: prueba con otra red o pide a tu IT que permita {host}.")
    if "ProxyError" in names:
        return (f"El proxy de la red no deja pasar {what} con {host}. Prueba con otra red o pide a tu IT que lo "
                "permita.")
    if "ConnectTimeout" in names:
        return f"{host} no contesta (más de {CONNECT_TIMEOUT} s para conectar): comprueba la conexión a internet."
    if "ReadTimeout" in names or "timed out" in low or "read timeout" in low:
        return f"{host} ha dejado de enviar datos: comprueba la conexión a internet y vuelve a probar."
    if any(k in low for k in ("getaddrinfo", "name or service", "nodename", "name resolution", "11001")):
        return f"No se encuentra {host} (sin internet o sin DNS): comprueba la conexión y vuelve a probar."
    if "ConnectionError" in names:
        return (f"Se ha cortado {what} con {host} ({e.__class__.__name__}): comprueba la conexión a internet y "
                "vuelve a probar.")
    return f"Ha fallado {what} con {host} ({e.__class__.__name__}): vuelve a probar."


class WhisperModels:
    """Los modelos de Whisper de este PC: donde estan y su descarga. Una descarga por modelo a la vez, en su hilo; la
    cola, el directo y Ajustes la comparten (si ya esta en marcha, esperan a esa). state() es para la interfaz: MB
    bajados y totales, %, velocidad, tiempo que queda y, si fallo, una frase con lo que paso y que hacer. Si el
    trabajo que esperaba se cancela, la descarga sigue: el modelo servira la proxima vez."""

    def __init__(self, root, log=None, http=None, url=WHISPER_URL, models=None):
        self.root, self.url = root, url
        self.log = log or (lambda m: None)
        self.http = http  # requests o un doble con get(url, headers, stream, timeout) (tests)
        self.models = models or WHISPER_MODELS
        self.lock = threading.Lock()
        self.jobs = {}  # modelo -> estado de su ultima descarga
        self.user_cancelled = set()  # cancelados en Ajustes: no se vuelven a empezar solos
        self.sleep = time.sleep  # los tests la sustituyen

    # --- donde estan
    def size(self, name):
        m = self.models.get(name)
        return sum(s for _, s, _ in m[2]) if m else None

    def own_dir(self, name):
        return os.path.join(self.root, name)

    def complete(self, d, name):
        """La carpeta propia tiene todos los ficheros del modelo con su tamano exacto."""
        m = self.models.get(name)
        if not m:
            return False
        for f, size, _ in m[2]:
            try:
                if os.path.getsize(os.path.join(d, f)) != size:
                    return False
            except OSError:
                return False
        return True

    def path(self, name):
        """Carpeta con el modelo completo, o None: la propia (DATA_DIR/models/whisper/<modelo>) o, si una version
        anterior lo descargo con faster-whisper, la de la cache de Hugging Face."""
        d = self.own_dir(name)
        if self.complete(d, name):
            return d
        return hf_snapshot(name)

    # --- descarga
    def start(self, name):
        """Empieza la descarga en segundo plano si no esta ya en el PC ni en marcha. Devuelve su estado (dict vivo).
        Si se estaba cancelando (el hilo aun no lo ha visto), sigue: «Descargar» justo despues de «Cancelar» vale."""
        if name not in self.models:
            raise EngineError(f"No sé descargar el modelo «{name}».")
        with self.lock:
            self.user_cancelled.discard(name)
            j = self.jobs.get(name)
            if j and j["running"]:
                j["cancel"] = False
                return j
            j = {"running": True, "done": 0, "total": self.size(name), "error": None, "cancel": False,
                 "samples": [], "finished": None}
            self.jobs[name] = j
        threading.Thread(target=self._run, args=(name, j), daemon=True, name=f"descarga-{name}").start()
        return j

    def cancel(self, name):
        """«Cancelar» en Ajustes. Queda apuntado: nada la vuelve a empezar por su cuenta (ni elegir el modo ni
        «Guardar»); si una transcripcion necesita el modelo, o se pulsa «Descargar», si."""
        with self.lock:
            j = self.jobs.get(name)
            if j and j["running"]:
                j["cancel"] = True
                self.user_cancelled.add(name)
                return True
        return False

    def cancelled_by_user(self, name):
        with self.lock:
            return name in self.user_cancelled

    def ensure(self, name, report=None, cancelled=None):
        """Carpeta del modelo: si falta, lo descarga (o espera a la descarga en marcha) contando el progreso con
        report({"stage": …}). Cancelled si se cancela el trabajo que espera (la descarga sigue). EngineError con la
        frase de lo que paso si no se ha podido. None si es un modelo sin datos para descargarlo."""
        p = self.path(name)
        if p or name not in self.models:
            return p
        j = self.start(name)
        last = None
        while True:
            with self.lock:
                running, err = j["running"], j["error"]
            if not running:
                break
            if cancelled and cancelled():
                raise Cancelled()
            msg = self.progress_text(name)
            if report and msg != last:
                report({"stage": msg})
                last = msg
            self.sleep(0.5)
        p = self.path(name)
        if p:
            return p
        raise EngineError(err or f"No se ha podido descargar el modelo {name}.")

    def state(self, name):
        """Para la interfaz (y el texto de progreso de la cola y el directo)."""
        ready = self.path(name) is not None
        size = self.size(name) or 0
        with self.lock:
            j = self.jobs.get(name) or {}
            done, total, samples = j.get("done") or 0, j.get("total") or size, list(j.get("samples") or [])
            running, error = bool(j.get("running")), j.get("error")
        speed = None
        if len(samples) >= 2 and samples[-1][0] > samples[0][0]:
            speed = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0])
        eta = (total - done) / speed if speed and speed > 0 and running else None
        return {"model": name, "ready": ready, "size_mb": round(size / 1e6), "downloading": running,
                "done_mb": round(done / 1e6), "total_mb": round(total / 1e6),
                "frac": round(done / total, 4) if total and (running or error) else (1.0 if ready else 0.0),
                "speed_mbs": round(speed / 1e6, 1) if speed else None, "eta_s": round(eta) if eta else None,
                "error": None if ready or running else error}

    def progress_text(self, name):
        s = self.state(name)
        if s["done_mb"] <= 0:
            return f"Descargando el modelo {name} ({fmt_mb(s['total_mb'] * 1e6)} MB, solo la primera vez)…"
        t = (f"Descargando el modelo {name} (solo la primera vez): {fmt_mb(s['done_mb'] * 1e6)} de "
             f"{fmt_mb(s['total_mb'] * 1e6)} MB ({int((s['frac'] or 0) * 100)} %)")
        return t + (f" · quedan ~{fmt_left(s['eta_s'])}" if s["eta_s"] else "")

    def _progress(self, j, done):
        now = time.time()
        with self.lock:
            j["done"] = done
            j["samples"] = [x for x in j["samples"] if now - x[0] <= 10][-200:] + [(now, done)]

    def _run(self, name, j):
        repo, rev, files = self.models[name]
        dest = self.own_dir(name)
        t0 = time.time()
        try:
            os.makedirs(dest, exist_ok=True)
            have = sum(self._have(dest, f, s) for f, s, _ in files)
            self._progress(j, have)
            free = shutil.disk_usage(dest).free
            need = self.size(name) - have
            if free < need + DOWNLOAD_SPARE:
                raise EngineError(f"No hay espacio en el disco para el modelo {name}: hacen falta {fmt_mb(need)} MB y "
                                  f"quedan {fmt_mb(free)} MB libres. Libera espacio y vuelve a probar.")
            self.log(f"modelo {name}: descargando {fmt_mb(need)} MB de {WHISPER_HOST}"
                     + (f" (ya había {fmt_mb(have)} MB)" if have else ""))
            while True:
                try:
                    base = 0
                    for f, size, sha in files:
                        self._file(j, name, repo, rev, dest, f, size, sha, base)
                        base += size
                    break
                except Cancelled:
                    with self.lock:
                        if j["cancel"]:
                            raise
                    # se pulso «Descargar» mientras se cancelaba: sigue donde iba
            self.log(f"modelo {name} descargado en {time.time() - t0:.0f} s")
        except Cancelled:
            j["error"] = "Descarga cancelada. Cuando vuelvas a pulsar «Descargar», sigue donde se quedó."
            self.log(f"modelo {name}: descarga cancelada")
        except EngineError as e:
            j["error"] = str(e)
            self.log(f"modelo {name}: no se pudo descargar: {e}")
        except OSError as e:  # el disco: lleno, sin permiso, antivirus…
            j["error"] = (f"No se ha podido guardar el modelo {name} en el disco ({e.__class__.__name__}: "
                          f"{e.strerror or e}). Comprueba que hay espacio y vuelve a probar.")
            self.log(f"modelo {name}: error de disco: {e.__class__.__name__}: {e}")
        except Exception as e:  # nunca se queda «descargando» para siempre: lo inesperado tambien se dice
            j["error"] = f"No se ha podido descargar el modelo {name} ({e.__class__.__name__}). Vuelve a probar."
            self.log(f"modelo {name}: error inesperado en la descarga: {e.__class__.__name__}: {e}")
        finally:
            with self.lock:
                j["running"], j["finished"] = False, time.time()
            _MODEL_CACHED.pop(name, None)

    @staticmethod
    def _have(dest, f, size):
        """Bytes que ya estan: el fichero entero si tiene su tamano, o lo que haya de su .part (reanudar)."""
        for p in (os.path.join(dest, f), os.path.join(dest, f + ".part")):
            try:
                n = os.path.getsize(p)
            except OSError:
                continue
            if p.endswith(".part"):
                return min(n, size)
            if n == size:
                return n
        return 0

    def _file(self, j, name, repo, rev, dest, fname, size, sha, base):
        import requests
        http = self.http or requests
        final = os.path.join(dest, fname)
        if os.path.isfile(final) and os.path.getsize(final) == size:
            return
        part = final + ".part"
        h = hashlib.sha256() if sha else None
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if have > size:
            os.remove(part)
            have = 0
        if h and have:  # se sigue donde se quedo: la huella empieza por lo que ya habia
            with open(part, "rb") as f:
                for block in iter(lambda: f.read(4 << 20), b""):
                    h.update(block)
        url = self.url.format(repo=repo, rev=rev, file=fname)
        fails, why, best = 0, None, have  # best: lo mas lejos que se ha llegado (solo pasarlo cuenta como avanzar)
        while have < size:
            if j["cancel"]:
                raise Cancelled()
            if fails:
                if fails > DOWNLOAD_RETRIES:
                    raise EngineError(f"No se ha podido descargar el modelo {name}. {why}")
                self.log(f"modelo {name}: {fname}: reintento {fails} desde {fmt_mb(have)} MB ({why})")
                self.sleep(min(30, 2 * 3 ** (fails - 1)))
            headers = {"Range": f"bytes={have}-"} if have else {}
            try:
                r = http.get(url, headers=headers, stream=True, timeout=(CONNECT_TIMEOUT, DOWNLOAD_STALL_S))
            except requests.RequestException as e:
                fails, why = fails + 1, connection_error(e, WHISPER_HOST, "la descarga")
                continue
            antes = best
            try:
                code = r.status_code
                if code == 416 and have:  # lo pedido empieza al final: el .part ya esta entero
                    break
                if code in (429, 500, 502, 503, 504):
                    fails, why = fails + 1, f"{WHISPER_HOST} no responde bien ahora mismo (HTTP {code})."
                    continue
                if code not in (200, 206):
                    raise EngineError(self._http_error(name, code))
                if "text/html" in (r.headers.get("content-type") or "").lower():
                    raise EngineError(f"No se ha podido descargar el modelo {name}: la red ha devuelto una página web "
                                      "en lugar del modelo (¿un portal de acceso o el proxy de la empresa?). Prueba "
                                      "con otra red.")
                if code == 200 and have:  # el servidor no ha hecho caso del Range: se empieza ese fichero de cero
                    have = 0
                    h = hashlib.sha256() if sha else None
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(DOWNLOAD_BLOCK):
                        if j["cancel"]:
                            raise Cancelled()
                        if not chunk:
                            continue
                        f.write(chunk)
                        if h:
                            h.update(chunk)
                        have += len(chunk)
                        if have > best:  # avanza de verdad: los intentos cuentan desde aqui
                            best, fails = have, 0
                        self._progress(j, base + have)
                if have < size and best == antes:  # respuesta vacia o corta sin pasar de donde ya se estaba
                    fails, why = fails + 1, f"{WHISPER_HOST} corta la descarga sin enviar datos nuevos."
            except requests.RequestException as e:  # cortada o parada DOWNLOAD_STALL_S: se sigue desde donde iba
                # si avanzo, fails volvio a 0 dentro: este es el primer reintento desde ahi
                fails, why = fails + 1, connection_error(e, WHISPER_HOST, "la descarga")
            finally:
                r.close()
            if have > size:
                break
        if have != size:
            try:
                os.remove(part)
            except OSError:
                pass
            raise EngineError(f"El modelo {name} se ha descargado incompleto o de más ({fmt_mb(have)} de "
                              f"{fmt_mb(size)} MB): vuelve a probar.")
        if h and h.hexdigest() != sha:
            os.remove(part)
            raise EngineError(f"El modelo {name} se ha descargado dañado (no coincide su huella): vuelve a probar.")
        for i in range(10):  # en Windows el antivirus puede tener el fichero abierto un momento al cerrarlo
            try:
                os.replace(part, final)
                break
            except PermissionError:
                if i == 9:
                    raise
                self.sleep(0.5)

    @staticmethod
    def _http_error(name, code):
        if code in (401, 403, 407, 451):
            return (f"{WHISPER_HOST} ha rechazado la descarga del modelo {name} (HTTP {code}). En la red de una "
                    "empresa suele ser el proxy que la bloquea: prueba con otra red o pide a tu IT que permita "
                    f"{WHISPER_HOST}.")
        if code == 404:
            return f"{WHISPER_HOST} ya no tiene el modelo {name} (HTTP 404): hay que actualizar la app."
        return f"{WHISPER_HOST} ha contestado HTTP {code} al descargar el modelo {name}: vuelve a probar más tarde."


class WorkerTimeout(EngineError):
    pass


class WorkerClient:
    """Whisper en un proceso aparte (worker.py), vigilado.

    El motor nativo de Whisper se ha llegado a quedar colgado dentro de generate() sin volver.
    En otro proceso se puede matar y relanzar sin tumbar la app; y cancelar es instantaneo."""

    IDLE_S = 300  # sin trabajo 5 min: se cierra y libera la RAM del modelo (0,4-3,4 GB)

    def __init__(self, name, log_dir, idle_s=None):
        """idle_s: segundos sin trabajo antes de cerrar el proceso (numero o funcion que lo devuelve: el ajuste
        worker_keepalive_min puede cambiar en caliente). None = IDLE_S."""
        self.name, self.log_dir, self.idle_s = name, log_dir, idle_s
        self.p, self.q, self.key = None, None, None
        self.lock = threading.Lock()
        self.clock = time.time  # los tests ponen un reloj falso
        self.last_used = self.clock()
        threading.Thread(target=self._janitor, daemon=True, name=f"janitor-{name}").start()

    def idle_limit(self):
        v = self.idle_s() if callable(self.idle_s) else self.idle_s
        return float(v) if v else float(self.IDLE_S)

    def _idle_check(self):
        """Cierra el proceso si lleva mas de idle_limit() sin trabajo. Devuelve True si lo ha cerrado."""
        if self.alive() and self.clock() - self.last_used > self.idle_limit() and self.lock.acquire(blocking=False):
            try:
                self.kill()
                return True
            finally:
                self.lock.release()
        return False

    def _janitor(self):
        while True:
            time.sleep(30)
            self._idle_check()

    def alive(self):
        return self.p is not None and self.p.poll() is None

    def kill(self):
        if self.p is not None:
            try:
                self.p.kill()
            except OSError:
                pass
        self.p, self.key = None, None

    def _spawn(self):
        import queue
        import sys
        self.kill()
        os.makedirs(self.log_dir, exist_ok=True)
        err = open(os.path.join(self.log_dir, f"worker-{self.name}.log"), "ab")
        here = os.path.dirname(os.path.abspath(__file__))
        # con los modelos a cargo del servidor (MODELS), Whisper no baja nada por su cuenta: si un modelo faltara, mejor
        # un error claro que una descarga de 1,5 GB sin progreso desde un proceso sin los certificados de Windows
        env = dict(os.environ, HF_HUB_OFFLINE="1") if MODELS is not None else None
        self.p = subprocess.Popen([sys.executable, "-u", os.path.join(here, "worker.py")], cwd=here,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                  creationflags=NO_WINDOW | BELOW_NORMAL, env=env)
        err.close()
        self.q = queue.Queue()
        threading.Thread(target=self._reader, args=(self.p, self.q), daemon=True).start()

    @staticmethod
    def _reader(p, q):
        import json
        for line in p.stdout:
            try:
                q.put(json.loads(line))
            except ValueError:
                pass
        q.put(None)  # el proceso ha terminado

    def request(self, req, audio=None, on_progress=None, timeout=None, idle_timeout=None, cancelled=None):
        """Manda una peticion y espera el resultado. Si tarda demasiado (o sin dar senales de vida
        durante idle_timeout) o se cancela, mata el proceso."""
        import json
        import queue
        if req.get("model") and not req.get("model_path") and MODELS is not None:  # la carpeta local: no baja nada
            req = dict(req, model_path=MODELS.path(req["model"]))
        with self.lock:
            payload = audio.astype("<f4").tobytes() if audio is not None else b""
            msg = (json.dumps(dict(req, n=len(payload) // 4)) + "\n").encode("utf-8")
            for attempt in range(2):
                if not self.alive():
                    self._spawn()
                try:
                    self.p.stdin.write(msg)
                    if payload:
                        self.p.stdin.write(payload)
                    self.p.stdin.flush()
                    break
                except OSError:  # el proceso habia muerto: se relanza una vez
                    self.kill()
                    if attempt:
                        raise EngineError("No se pudo arrancar el proceso de Whisper.")
            start = last = time.time()
            while True:
                if cancelled and cancelled():
                    self.kill()
                    raise Cancelled()
                try:
                    m = self.q.get(timeout=0.25)
                except queue.Empty:
                    now = time.time()
                    if (timeout and now - start > timeout) or (idle_timeout and now - last > idle_timeout):
                        self.kill()
                        raise WorkerTimeout(f"Whisper no respondió en {now - start:.0f} s; se ha reiniciado.")
                    continue
                if m is None:
                    self.kill()
                    raise EngineError("El proceso de Whisper se cerró de golpe "
                                      f"(detalles en %LOCALAPPDATA%\\AudiosApp\\worker-{self.name}.log).")
                last = time.time()
                if m.get("type") == "progress":
                    if on_progress:
                        on_progress(m)
                    continue
                if m.get("type") == "error":
                    raise EngineError(m.get("error") or "error en Whisper")
                if req.get("model"):
                    self.key = (req["model"], int(req.get("threads") or 0))
                self.last_used = self.clock()
                return m

    def ensure(self, model, threads, on_progress=None, cancelled=None):
        """Deja el modelo cargado. Si no esta en este PC, antes lo descarga MODELS con progreso real en on_progress
        (MB y %) o espera a la descarga que ya este en marcha; si no se puede, EngineError con la causa."""
        if self.alive() and self.key == (model, int(threads)):
            return False
        path = MODELS.ensure(model, on_progress, cancelled) if MODELS is not None else None
        if on_progress:
            on_progress({"stage": f"Cargando el modelo {model}…"})
        self.request({"op": "load", "model": model, "threads": threads, "model_path": path}, on_progress=on_progress,
                     timeout=LOAD_TIMEOUT_S if MODELS is not None else 3600, cancelled=cancelled)
        return True


class LocalEngine:
    def __init__(self, log_dir, name="batch", idle_s=None):
        self.worker = WorkerClient(name, log_dir, idle_s=idle_s)

    def transcribe(self, path, preset, language, prompt, threads, report, cancelled, start=0.0, about=None):
        """start > 0: solo desde ese segundo (continuar una transcripcion a medias). about: contexto libre."""
        def progress(m):
            report(frac=m.get("frac"), stage=m.get("stage"), transcribing=bool(m.get("transcribing")))

        self.worker.ensure(preset["model"], threads, progress, cancelled)
        # sin noticias en 20 min = colgado (en modo por lotes llega progreso cada pocos minutos como mucho)
        res = self.worker.request({"op": "file", "model": preset["model"], "threads": threads, "path": path,
                                   "batched": preset["batched"], "beam": preset["beam"], "start": start,
                                   "language": language or None, "prompt": prompt or None, "about": about or None},
                                  on_progress=progress, idle_timeout=1200, cancelled=cancelled)
        segs = [tuple(s) for s in res["segments"]]
        return clean_segments(segs, res.get("prompt"), res.get("language")), res.get("language"), res.get("duration")


# ----------------------------------------------------------------------------- cupo y claves (Groq)
# El plan gratuito de Groq limita por organizacion (= por clave) y por modelo: cada Whisper 20 peticiones/min y
# 2.000/dia; chat (gpt-oss) 30/min, 1.000/dia y 8.000 tokens/min. Cada respuesta trae x-ratelimit-remaining-requests
# (lo que queda HOY), x-ratelimit-reset-requests ("2m59.56s") y, en un 429, retry-after (segundos).
# El KeyPool guarda eso por clave, familia y modelo (el directo con -turbo y el repaso final con whisper-large-v3 no
# se descuentan uno al otro), reparte las peticiones entre la clave principal y las adicionales y le dice al directo
# que ventana usar segun lo que queda del dia. Nunca escribe una clave en el log.
RATE_SPACING = {"whisper": 3.0, "chat": 2.0}   # 20 y 30 peticiones/min: >= 3 s / 2 s entre dos de la misma clave
ROTATE_BELOW = 20          # con menos peticiones restantes hoy, se pasa a la siguiente clave
ROTATE_RETRY_AFTER = 60    # un 429 con retry-after mayor bloquea la clave hasta entonces y se pasa a la siguiente
INVALID_KEY_S = 3600       # una clave que responde 401/403 no se vuelve a usar en 1 h (o hasta «Probar conexión»)
RPM_MAX_SPACING = 30.0     # con un limite por minuto declarado, el hueco entre dos peticiones no pasa de 30 s
LIVE_PACES = ((600, (3, 6)), (300, (6, 10)), (60, (10, 15)))  # peticiones restantes -> ventana del directo (s)
LIVE_PAUSE_BELOW = 60      # con menos en TODAS las claves, el directo se pausa (el repaso final sigue)
DAILY_PROVIDERS = ("groq",)  # cabeceras de peticiones por DIA: la politica de ventana del directo solo vale ahi
# claves adicionales: cada proveedor tiene su lista (las de Groq siguen en extra_keys). Pertenecen al proveedor con el
# que se guardaron y solo se mandan a el: al cambiar de proveedor no salen hacia otro servicio.
EXTRA_KEYS_FIELDS = {"groq": "extra_keys", "openai": "extra_keys_openai", "azure": "extra_keys_azure",
                     "custom": "extra_keys_custom"}


def extra_keys_field(provider):
    """Campo de cloud con las claves adicionales de ese proveedor; None si no es un proveedor conocido."""
    return EXTRA_KEYS_FIELDS.get(provider or "groq")


def all_extra_keys(cfg):
    """Las claves adicionales de todos los proveedores (para enmascararlas en el diagnostico)."""
    return [k for f in EXTRA_KEYS_FIELDS.values() for k in (cfg.get(f) or []) if isinstance(k, str) and k]


def cloud_keys(cfg):
    """La API key principal y las adicionales DEL PROVEEDOR CONFIGURADO, sin vacias ni repetidas, en ese orden.
    Las que se guardaron con otro proveedor no se usan ni se envian."""
    field = extra_keys_field(cfg.get("provider", "groq"))
    out = []
    for k in [cfg.get("api_key")] + list((cfg.get(field) if field else None) or []):
        k = (k or "").strip() if isinstance(k, str) else ""
        if k and k not in out:
            out.append(k)
    return out


def daily_quota(cfg):
    return cfg.get("provider", "groq") in DAILY_PROVIDERS


class KeyPool:
    """Estado de cupo por (clave, familia, modelo) y eleccion de la clave para cada peticion. model=None cuando no se
    sabe (Azure: lo fija el deployment). Una clave que responde 401/403 queda marcada como no valida (en todas las
    familias y modelos) durante INVALID_KEY_S y se pasa a la siguiente."""

    FAMILIES = ("whisper", "chat")

    def __init__(self, log=None):
        self.lock = threading.Lock()
        self.state = {}       # (clave, familia, modelo) -> dict
        self.active = {}      # (familia, modelo) -> clave activa
        self.invalid = {}     # clave -> hasta cuando no se usa (respondio 401/403)
        self.log = log or (lambda m: None)
        self.headers_logged = False
        self.clock = time.time  # los tests y el banco ponen un reloj falso

    def _st(self, key, family, model=None):
        st = self.state.get((key, family, model))
        if st is None:
            st = self.state[(key, family, model)] = {"remaining": None, "limit": None, "reset_at": None,
                                                     "retry_after": None, "blocked_until": 0.0, "last_request": 0.0,
                                                     "not_before": 0.0, "used": 0}
        if st["reset_at"] and self.clock() >= st["reset_at"]:  # el dia de Groq se renovo: se olvida lo gastado
            st.update(remaining=None, reset_at=None)
        return st

    @staticmethod
    def _label(keys, key):
        return f"clave {keys.index(key) + 1}/{len(keys)}" if key in keys else "clave"

    def _usable(self, key, st, now, need):
        return (self.invalid.get(key, 0.0) <= now and st["blocked_until"] <= now
                and (st["remaining"] is None or st["remaining"] >= need))

    def _free_at(self, key, st, now):
        free = now
        if st["blocked_until"] > now:
            free = st["blocked_until"]
        elif st["remaining"] is not None and st["reset_at"]:
            free = st["reset_at"]
        return max(free, self.invalid.get(key, 0.0))

    def pick(self, cfg, family, need=ROTATE_BELOW, model=None):
        """Clave para la siguiente peticion: la activa mientras sea valida, le queden >= need peticiones y no este
        bloqueada; si no, la siguiente que sirva; si ninguna sirve, la que antes se libere (la peticion fallara con
        un 429 que dira cuanto esperar, o con el 401 de la clave)."""
        keys = cloud_keys(cfg)
        if not keys:
            raise EngineError("Falta la API key de la nube (Ajustes ⚙).")
        with self.lock:
            now = self.clock()
            slot = (family, model)
            cur = self.active.get(slot)
            order = keys if cur not in keys else keys[keys.index(cur):] + keys[:keys.index(cur)]
            chosen = next((k for k in order if self._usable(k, self._st(k, family, model), now, need)), None)
            if chosen is None:
                chosen = min(keys, key=lambda k: self._free_at(k, self._st(k, family, model), now))
            if chosen != cur and cur is not None and len(keys) > 1:
                st = self._st(cur, family, model)
                why = ("no válida" if self.invalid.get(cur, 0.0) > now else
                       "bloqueada" if st["blocked_until"] > now else
                       f"quedan {st['remaining']} peticiones hoy" if st["remaining"] is not None else "sin uso")
                self.log(f"   {family}{f' ({model})' if model else ''}: {self._label(keys, cur)} {why} → "
                         f"{self._label(keys, chosen)}")
            self.active[slot] = chosen
            return chosen

    def reserve(self, key, family, model=None, per_minute=False):
        """Reserva un hueco para una peticion con esa clave y devuelve los segundos que hay que esperar antes de
        mandarla (ritmo por minuto y, tras un 429 corto, el retry-after que pidio el servicio). Con varias
        peticiones a la vez cada una recibe el hueco siguiente. per_minute: el proveedor declara su limite por minuto
        en x-ratelimit-limit-requests (OpenAI, Azure, una pasarela; en Groq es por dia): no se pasa de el (un hueco de
        60/limite s, como mucho RPM_MAX_SPACING) y, si no queda ninguna y se renueva en menos de un minuto, se
        espera."""
        with self.lock:
            st, now = self._st(key, family, model), self.clock()
            spacing = RATE_SPACING.get(family, 0.0)
            if per_minute and st["limit"]:
                spacing = max(spacing, min(RPM_MAX_SPACING, 60.0 / st["limit"]))
            slot = max(now, st["last_request"] + spacing, st["not_before"])
            if per_minute and st["remaining"] == 0 and st["reset_at"] and st["reset_at"] - now <= 60:
                slot = max(slot, st["reset_at"])  # el minuto se renueva enseguida (un cupo por dia no se espera)
            st["last_request"] = slot
            return slot - now

    def note(self, key, family, status, headers, model=None):
        """Aprende de la respuesta: cabeceras x-ratelimit-*, retry-after y si la clave es valida. Devuelve una copia
        del estado."""
        h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        rl = {k: v for k, v in h.items() if k.startswith("x-ratelimit-")}
        # y las de tiempos, si las hay (openai-processing-ms…): dicen cuanto de la latencia es inferencia
        tm = {k: v for k, v in h.items() if any(w in k for w in ("processing", "timing", "latency"))}
        with self.lock:
            st, now = self._st(key, family, model), self.clock()
            st["used"] += 1
            if rl and not self.headers_logged:  # la primera vez, todas: para saber que cabeceras manda el servicio
                self.headers_logged = True
                self.log("   cabeceras de cupo del servicio: " + ", ".join(f"{k}={v}" for k, v in sorted(rl.items()))
                         + ("; de tiempos: " + ", ".join(f"{k}={v}" for k, v in sorted(tm.items())) if tm else
                            "; sin cabeceras de tiempo de proceso"))
            try:
                if "x-ratelimit-remaining-requests" in rl:
                    st["remaining"] = int(float(rl["x-ratelimit-remaining-requests"]))
                if "x-ratelimit-limit-requests" in rl:
                    st["limit"] = int(float(rl["x-ratelimit-limit-requests"]))
            except ValueError:
                pass
            if "x-ratelimit-reset-requests" in rl:
                secs = _duration_s(rl["x-ratelimit-reset-requests"])
                if secs > 0:
                    st["reset_at"] = now + secs
            if status in (401, 403):  # clave no valida (o sin permiso): se deja de usar y se pasa a la siguiente
                self.invalid[key] = now + INVALID_KEY_S
            elif status == 429:
                ra = _duration_s(h.get("retry-after", "")) or None
                st["retry_after"] = ra
                if ra and ra > ROTATE_RETRY_AFTER:
                    st["blocked_until"] = now + ra
                elif ra:  # 429 corto: la siguiente peticion con esta clave, no antes de lo que pide
                    st["not_before"] = max(st["not_before"], now + ra)
            elif status is not None and 200 <= int(status) < 300:
                self.invalid.pop(key, None)
                if st["remaining"] is not None:
                    st["blocked_until"] = 0.0
            return dict(st)

    def blocked(self, key, family, model=None):
        with self.lock:
            return self._st(key, family, model)["blocked_until"] > self.clock()

    def is_invalid(self, key):
        with self.lock:
            return self.invalid.get(key, 0.0) > self.clock()

    def forget_invalid(self):
        """«Probar conexión» vuelve a probar todas las claves (pudo arreglarse en la consola del proveedor)."""
        with self.lock:
            self.invalid.clear()

    def live_pace(self, cfg, model=None):
        """Ventana (min_s, max_s) del directo segun las peticiones que quedan hoy en la mejor clave para el modelo
        del directo; None = pausa. Con un proveedor sin cupo diario (OpenAI, Azure, pasarela propia) siempre la
        ventana corta."""
        if not daily_quota(cfg):
            return LIVE_PACES[0][1]
        key = self.pick(cfg, "whisper", need=LIVE_PAUSE_BELOW, model=model)  # se prefiere una clave con cupo
        with self.lock:
            st, now = self._st(key, "whisper", model), self.clock()
            if st["blocked_until"] > now:
                return None
            rem = st["remaining"]
        if rem is None:
            return LIVE_PACES[0][1]
        for floor, pace in LIVE_PACES:
            if rem >= floor:
                return pace
        return None

    def public(self, cfg, family="whisper", model=None):
        """Para la interfaz: lo que queda hoy en la clave activa (o la principal) para ese modelo, cuando se renueva,
        cuantas claves hay y cuantas no son validas. Nunca las claves."""
        keys = cloud_keys(cfg)
        with self.lock:
            now = self.clock()
            slot = (family, model)
            key = self.active.get(slot) if self.active.get(slot) in keys else (keys[0] if keys else None)
            st = self._st(key, family, model) if key else None
            others = [self._st(k, family, model) for k in keys if k != key]
            out = {"keys": len(keys), "active": (keys.index(key) + 1) if key else 0, "daily": daily_quota(cfg),
                   "invalid": sum(1 for k in keys if self.invalid.get(k, 0.0) > now),
                   "remaining": st["remaining"] if st else None, "limit": st["limit"] if st else None,
                   "reset_in": max(0.0, st["reset_at"] - now) if st and st["reset_at"] else None,
                   "blocked_for": max(0.0, st["blocked_until"] - now) if st and st["blocked_until"] > now else 0.0,
                   "used": st["used"] if st else 0,
                   # suma de lo que queda en las demas claves (las que aun no se han usado cuentan como "sin dato")
                   "others_remaining": sum(o["remaining"] for o in others if o["remaining"] is not None),
                   "others_unknown": sum(1 for o in others if o["remaining"] is None)}
        return out


# ----------------------------------------------------------------------------- nube

class CloudEngine:
    def __init__(self, tmp_root, log=None, keys=None):
        self.tmp_root = tmp_root
        self.keys = keys or KeyPool(log)

    def purge_tmp(self):
        """Borra lo que haya en tmp_root (trozos FLAC/WAV de la reunion que se estaban subiendo): al arrancar quedan los
        de una sesion que se corto (apagado, actualizacion, cuelgue) y al apagar, los de las subidas en marcha. Lo que
        siga abierto (Windows no deja borrarlo) se queda para la proxima vez. Devuelve cuantas entradas ha borrado."""
        try:
            entries = list(os.scandir(self.tmp_root))
        except OSError:
            return 0
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    shutil.rmtree(e.path, ignore_errors=True)
                else:
                    os.remove(e.path)
            except OSError:
                pass
        return sum(1 for e in entries if not os.path.lexists(e.path))

    @staticmethod
    def endpoint(cfg, key=None, model=None):
        """(url, cabeceras, modelo). key: una clave concreta del KeyPool (por defecto la principal);
        model: modelo distinto del configurado (el directo usa cloud.live_model)."""
        provider = cfg.get("provider", "groq")
        key = (key or cfg.get("api_key") or "").strip()
        if not key:
            raise EngineError("Falta la API key de la nube (Ajustes ⚙).")
        if provider == "azure":
            base = (cfg.get("azure_endpoint") or "").strip().rstrip("/")
            dep = (cfg.get("azure_deployment") or "").strip()
            if not base or not dep:
                raise EngineError("Faltan el endpoint o el deployment de Azure (Ajustes ⚙).")
            ver = (cfg.get("azure_api_version") or "2024-06-01").strip()
            url = f"{base}/openai/deployments/{dep}/audio/transcriptions?api-version={ver}"
            return url, {"api-key": key}, None
        if provider == "custom":
            base = base_url(cfg)
            if not base.startswith(("http://", "https://")):
                raise EngineError("Falta la URL base de la API (Ajustes ⚙), p. ej. https://api.groq.com/openai/v1")
            return base + "/audio/transcriptions", {"Authorization": f"Bearer {key}"}, \
                (model or cfg.get("model") or "").strip() or "whisper-large-v3"
        p = PROVIDERS.get(provider) or PROVIDERS["groq"]
        model = model or cfg.get("model") or p["models"][0]
        return p["url"], {"Authorization": f"Bearer {key}"}, model

    @staticmethod
    def live_model(cfg):
        """Modelo del directo en la nube. Groq: cloud.live_model (whisper-large-v3-turbo por defecto: mas rapido por
        trozo). «Otra (compatible)»: cloud.live_model_custom, el que escriba el usuario para su pasarela; vacio = el
        de la cola (cloud.model), porque no se puede dar por hecho que la pasarela tenga los modelos de Groq.
        OpenAI (whisper-1) y Azure (lo fija el deployment): el de siempre (None). El repaso final y la cola siguen
        con cloud.model."""
        provider = cfg.get("provider", "groq")
        if provider == "custom":
            return (cfg.get("live_model_custom") or "").strip() or None
        if provider != "groq":
            return None
        return (cfg.get("live_model") or "").strip() or LIVE_MODEL_DEFAULT

    @staticmethod
    def _extract_parts(y, parts, work):
        paths = []
        for i, (a, b) in enumerate(parts):
            paths.append(os.path.join(work, f"part_{i:03d}.flac"))
            write_flac(paths[-1], y[int(a * 16000):int(b * 16000)])
        return paths

    @staticmethod
    def _cut_points(duration, silences, chunk_s):
        """Trozos de ~chunk_s segundos, cortando en el silencio mas cercano para no partir palabras."""
        mids = [(a + b) / 2 for a, b in silences]
        cuts, target = [0.0], chunk_s
        while target < duration - 60:
            near = [m for m in mids if m > cuts[-1] + 60 and abs(m - target) <= 45]
            cut = min(near, key=lambda m: abs(m - target)) if near else target
            cuts.append(cut)
            target = cut + chunk_s
        cuts.append(duration)
        return list(zip(cuts[:-1], cuts[1:]))

    @staticmethod
    def prompt_for(language, vocab, context=None, about=None):
        """(idioma para la API, prompt). Las APIs no tienen "spanglish" ni otras mezclas: deteccion automatica +
        frase de estilo mezclada. about: contexto libre de la reunion (va detras del vocabulario)."""
        if lang_pair(language):
            return None, build_prompt(style_for(language), vocab, max_bytes=CLOUD_PROMPT_BYTES, context=context,
                                      about=about)[0]
        return language or None, build_prompt("", vocab, max_bytes=CLOUD_PROMPT_BYTES, context=context, about=about)[0]

    @staticmethod
    def _wait(secs, cancelled):
        end = time.time() + secs
        while time.time() < end:
            if cancelled():
                raise Cancelled()
            time.sleep(min(0.25, max(0.0, end - time.time())))

    def _post(self, cfg, chunk, language, prompt, report, cancelled, context=None, model=None, timeout=300,
              about=None, timing=None, attempts=6, max_wait=120.0, only_key=None):
        """Una peticion de transcripcion. La clave la elige el KeyPool (rotacion), que tambien marca el ritmo
        (>= 3 s entre peticiones de la misma clave) y aprende de las cabeceras de cupo de la respuesta.
        timeout: segundos de lectura (la conexion tiene su tope aparte, CONNECT_TIMEOUT). timing: si se pasa un
        dict, se anotan los segundos de espera por cupo/reintentos ("wait"), de red de la peticion que respondio
        ("net": conexion + subida + inferencia + bajada) y, si el servidor lo declara, de inferencia ("infer").
        attempts y max_wait: intentos y espera maxima entre ellos (el directo usa pocos: un trozo que no sale se salta
        y lo recupera el repaso final). only_key: solo esa clave, sin rotar («Probar conexión» prueba cada una)."""
        import requests
        _, _, qm = self.endpoint(cfg, key=only_key, model=model)  # valida la configuracion; qm: modelo del cupo
        data = {"response_format": "verbose_json", "temperature": "0"}
        language, prompt = self.prompt_for(language, prompt, context, about)
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        tm = timing if timing is not None else {}
        tm.setdefault("wait", 0.0)
        last, family = None, "whisper"
        for attempt in range(attempts):
            if cancelled():
                raise Cancelled()
            key = only_key or self.keys.pick(cfg, family, model=qm)
            url, headers, mdl = self.endpoint(cfg, key=key, model=model)
            if mdl:
                data["model"] = mdl
            pause = self.keys.reserve(key, family, qm, per_minute=not daily_quota(cfg))  # sin pasar del limite/min
            tm["wait"] += pause
            self._wait(pause, cancelled)
            try:
                with open(chunk, "rb") as fh:
                    mime = "audio/wav" if chunk.lower().endswith(".wav") else "audio/flac"
                    t0 = perf_counter()
                    r = HTTP.post(url, timeout, headers=headers, data=data,
                                  files={"file": (os.path.basename(chunk), fh, mime)})
                    tm["net"] = perf_counter() - t0
            except requests.RequestException as e:  # una frase con lo que paso y que hacer (antes: «sin conexion»)
                last = connection_error(e, url_host(url), "la conexión")
                wait = 2 ** attempt
            else:
                self.keys.note(key, family, r.status_code, r.headers, qm)
                if r.status_code == 200:
                    tm["infer"] = processing_s(r.headers)
                    return json_body(r)
                last = cloud_http_error(r, cfg, data.get("model"))
                others = not only_key and len(cloud_keys(cfg)) > 1
                if r.status_code in (401, 403):  # clave no valida: queda marcada y, si hay otra, se sigue con ella
                    if others and self.keys.pick(cfg, family, model=qm) != key:
                        report(stage="Una de las claves no es válida: se sigue con la siguiente…")
                        continue
                    raise EngineError(last)
                if r.status_code not in (408, 409, 429, 500, 502, 503, 504):
                    raise EngineError(last)
                wait = _duration_s(r.headers.get("retry-after", "")) or 3 * 2 ** attempt
                if r.status_code == 429 and self.keys.blocked(key, family, qm):  # retry-after largo (cupo del dia)
                    if others and self.keys.pick(cfg, family, model=qm) != key:
                        report(stage="Esa cuenta de Groq ha agotado su cupo: se sigue con la siguiente clave…")
                        continue  # otra clave disponible: sin esperar
                    # sin alternativa: fallar ya, con el tiempo que pide (esperar aqui bloquearia el directo)
                    raise EngineError(f"Groq pide esperar {int(wait // 60)} min ({last})")
            if attempt == attempts - 1:  # tras el ultimo intento no se espera: se falla ya
                break
            wait = min(max(wait, 1.0), max_wait)
            report(stage=f"{last} Reintento en {wait:.0f} s…")
            tm["wait"] += wait
            self._wait(wait, cancelled)
        raise EngineError(last or "la API no respondió")

    def transcribe(self, path, duration, cfg, language, prompt, job_id, report, cancelled, start=0.0, about=None):
        self.endpoint(cfg)  # valida la configuracion antes de convertir nada
        work = os.path.join(self.tmp_root, job_id)
        os.makedirs(work, exist_ok=True)
        try:
            report(stage="Preparando audio (16 kHz mono)…", frac=0.02)
            y = decode_16k(path, start, cancelled)  # PyAV, sin ffmpeg
            silences = find_silences(y)
            duration = len(y) / 16000
            if duration < 0.5:
                raise EngineError("El audio está vacío.")
            if cancelled():
                raise Cancelled()
            chunk_s = max(60, int(float(cfg.get("chunk_minutes") or 10) * 60))
            parts = self._cut_points(duration, silences, chunk_s)
            n = len(parts)
            if cancelled():
                raise Cancelled()
            report(stage=f"Transcribiendo en la nube (0/{n} partes)…", frac=0.12, transcribing=True)
            abort = threading.Event()  # si falla un trozo, paran todos

            def stop():
                return cancelled() or abort.is_set()

            buf, encoded, enc_lock = {"y": y}, [0], threading.Lock()
            del y  # buf lo suelta en cuanto este codificada la ultima parte: no se retiene durante la subida

            def do_part(i, a, b):
                # cada hilo escribe el FLAC de su parte justo antes de subirla: la primera subida empieza en cuanto
                # esta su parte (no cuando estan las n) y las demas se codifican mientras otras ya suben
                path = os.path.join(work, f"part_{i:03d}.flac")
                try:
                    write_flac(path, buf["y"][int(a * 16000):int(b * 16000)])
                    with enc_lock:
                        encoded[0] += 1
                        if encoded[0] == n:
                            buf["y"] = None
                    if stop():
                        raise Cancelled()
                    res = self._post(cfg, path, language, prompt, report, stop, about=about)
                finally:  # subido, fallido o cancelado: el trozo de la reunion no se queda en disco
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                return i, parse_verbose(res, a, b), res.get("language")

            results, langs, done = {}, [], 0
            workers = max(1, min(int(cfg.get("parallel") or 3), 8))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(do_part, i, a, b) for i, (a, b) in enumerate(parts)]
                try:
                    for f in as_completed(futures):
                        i, segs, lang = f.result()
                        results[i] = segs
                        if lang:
                            langs.append(lang)
                        done += 1
                        report(stage=f"Transcribiendo en la nube ({done}/{n} partes)…",
                               frac=0.12 + 0.88 * done / n)
                except BaseException:
                    abort.set()
                    for f in futures:
                        f.cancel()
                    raise
            segs = [(a + start, b + start, t) for i in range(n) for a, b, t in results[i]]
            return (clean_segments(segs, self.prompt_for(language, prompt, about=about)[1]),
                    (langs[0] if langs else None), duration)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def transcribe_array(self, cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, context=None,
                         model=None, about=None, timing=None):
        """Un trozo ya en memoria (16 kHz mono), para el modo en directo. context: lo ultimo que se dijo;
        model: el del directo (cloud.live_model), distinto del de la cola y el repaso final; about: contexto
        libre de la reunion; timing: dict donde anotar los tiempos por fase (ver _post; aqui se suma a "prep"
        lo que tarda en escribir el WAV)."""
        os.makedirs(tmp_dir, exist_ok=True)
        f = os.path.join(tmp_dir, f"live_{job_id}.wav")
        t0 = perf_counter()
        write_wav16(f, audio)
        if timing is not None:
            timing["prep"] = timing.get("prep", 0.0) + perf_counter() - t0
        secs = len(audio) / 16000
        try:
            # pocos intentos y esperas cortas: si la red se atasca, mejor fallar y seguir con el siguiente trozo (el
            # repaso final recupera el texto) que retener el directo minutos. La lectura, segun lo largo del trozo
            # (los de ponerse al dia llegan a 10 min y la API tarda mas en contestar)
            res = self._post(cfg, f, language, prompt, lambda **k: stage(k.get("stage", "")), cancelled, context,
                             model=model, timeout=LIVE_READ_S + secs * LIVE_READ_PER_S, about=about, timing=timing,
                             attempts=LIVE_ATTEMPTS, max_wait=LIVE_MAX_WAIT_S)
        finally:
            try:
                os.remove(f)
            except OSError:
                pass
        # con el contexto en el filtro de ecos: si Whisper repite la frase anterior, se quita
        full = self.prompt_for(language, prompt, context, about)[1]
        return clean_segments(parse_verbose(res, 0.0, secs), full), res.get("language")

    def test(self, cfg):
        """Manda 1 s de silencio con CADA clave (la principal y las adicionales de este proveedor) para comprobar que
        el endpoint y todas las claves funcionan. Devuelve lo que tardo la principal; si una adicional falla, lo
        dice (cual y por que) con un EngineError, para que no se quede sin saber que no vale."""
        work = os.path.join(self.tmp_root, "test")
        os.makedirs(work, exist_ok=True)
        try:
            import numpy as np
            f = os.path.join(work, "silencio.flac")
            write_flac(f, np.zeros(16000, np.float32))
            keys = cloud_keys(cfg)
            self.keys.forget_invalid()  # se vuelven a probar todas
            secs, bad = None, []
            for i, k in enumerate(keys or [None]):
                t0 = time.time()
                try:
                    self._post(cfg, f, None, None, lambda **kw: None, lambda: False, only_key=k, attempts=2,
                               max_wait=10.0)
                except EngineError as e:
                    if i == 0:  # la principal: el error de siempre
                        raise
                    if self.keys.is_invalid(k):  # 401/403; una sin cupo hoy (429) sigue siendo valida
                        bad.append((i, str(e)))
                    continue
                if i == 0:
                    secs = time.time() - t0
            if bad:
                t = f"{secs:.1f}".replace(".", ",")
                if len(bad) == 1:
                    raise EngineError(f"La API key principal funciona ({t} s), pero la clave adicional {bad[0][0]} no "
                                      f"es válida ({bad[0][1]}). Quítala o cámbiala en «Claves adicionales».")
                raise EngineError(f"La API key principal funciona ({t} s), pero no son válidas las claves adicionales "
                                  + " y ".join(f"{n} ({err})" for n, err in bad)
                                  + ". Quítalas o cámbialas en «Claves adicionales».")
            return secs
        finally:
            shutil.rmtree(work, ignore_errors=True)


def api_error(r):
    try:
        j = r.json()
        msg = j.get("error", {}).get("message") if isinstance(j.get("error"), dict) else j.get("error")
        msg = msg or j.get("message")
    except (ValueError, AttributeError):
        msg = None
    if not msg:
        text = str(getattr(r, "text", "") or "")
        if "<html" in text[:500].lower() or "<!doctype" in text[:200].lower():  # el proxy o un portal, no la API
            msg = "la red ha devuelto una página web en lugar de la API (¿un proxy o un portal de acceso?)"
        else:
            msg = " ".join(text.split())[:200]
    return f"HTTP {r.status_code}: {msg}"


def url_host(url):
    """El nombre del servidor, nunca el usuario o la clave que pudiera llevar la URL (https://token@pasarela/...)."""
    from urllib.parse import urlsplit
    try:
        return urlsplit(url or "").hostname or "el servicio"
    except ValueError:
        return "el servicio"


def cloud_http_error(r, cfg, model=None):
    """Una frase para un error HTTP de la API de transcripcion: lo que ha pasado (con el «HTTP NNN: mensaje» del
    servicio, que el diagnostico sabe leer) y que hacer. Hasta el 07/11/2026 solo salia el «HTTP NNN: …»."""
    base = api_error(r)
    p = PROVIDERS.get((cfg or {}).get("provider", "groq")) or {}
    prov = p.get("label") or "El servicio"
    code = r.status_code
    if code == 401:
        donde = f" desde {p['keys'].replace('https://', '')}" if p.get("keys") else ""
        return f"{prov} no acepta la API key ({base}). Cópiala de nuevo{donde} y pulsa «Probar conexión»."
    if code == 403:
        return (f"{prov} ha rechazado la petición ({base}). Si la clave es correcta y estás en la red de una empresa, "
                "puede que su proxy bloquee el servicio: prueba con otra red.")
    if code == 404:
        return f"{prov} no encuentra el modelo o la dirección ({base}). Revisa el modelo en Ajustes" + (
            f" (ahora «{model}»)." if model else ".")
    if code == 413:
        return f"El trozo de audio es demasiado grande para {prov} ({base}): baja «Minutos por trozo» en Ajustes."
    if code in (500, 502, 503, 504):
        return f"{prov} está fallando ahora mismo ({base}): vuelve a probar en unos minutos."
    if code == 429:
        return f"{prov} pide esperar ({base})."
    return f"{prov} no acepta la petición ({base})."


def json_body(r):
    """El objeto JSON de una respuesta 200. Un proxy o un portal de acceso de la red de la empresa puede contestar
    200 con una pagina HTML: eso es un EngineError claro (el directo lo salta y sigue), no un ValueError suelto."""
    try:
        j = r.json()
    except ValueError:
        j = None
    if not isinstance(j, dict):
        head = " ".join(str(getattr(r, "text", "") or "").split())[:120]
        raise EngineError("El servicio respondió sin JSON (¿un proxy o un portal de acceso de la red?)"
                          + (f": {head}" if head else "."))
    return j


# ----------------------------------------------------------------------------- resumen con IA

SUMMARY_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b",
                  "qwen/qwen3.8-27b"]  # TODO verificar en console.groq.com/docs/models que este id existe
CHAT_URLS = {"groq": "https://api.groq.com/openai/v1/chat/completions",
             "openai": "https://api.openai.com/v1/chat/completions"}
SUMMARY_LANGS = {"es": "español", "en": "inglés", "same": "el mismo idioma en que se habla en la reunión"}
# cabeceras del resumen (en el idioma elegido; con "same" van en español y se pide traducirlas)
SUMMARY_HEADINGS = {
    "es": {"summary": "Resumen", "points": "Puntos clave", "decisions": "Decisiones", "next": "Próximos pasos",
           "people": "Participantes", "none_clear": "Ninguna clara", "none": "Ninguno"},
    "en": {"summary": "Summary", "points": "Key points", "decisions": "Decisions", "next": "Next steps",
           "people": "Participants", "none_clear": "None clear", "none": "None"},
}


def chat_url(cfg):
    """Endpoint de chat del mismo proveedor que la transcripcion (None si no lo hay, p. ej. Azure)."""
    if cfg.get("provider") == "custom":
        base = base_url(cfg)
        return base + "/chat/completions" if base.startswith(("http://", "https://")) else None
    return CHAT_URLS.get(cfg.get("provider", "groq"))


def chat_missing(cfg, what="El resumen con IA"):
    """Por que no se puede usar el chat (resumen, traduccion del directo) con esta configuracion; None si se puede.
    Con Azure OpenAI solo se configura el deployment de Whisper: el chat aun no esta implementado."""
    if chat_url(cfg) and cloud_keys(cfg):
        return None
    if cfg.get("provider") == "azure":
        return (f"{what} aún no funciona con Azure OpenAI (con Azure solo se transcribe). Para usarlo, elige Groq, "
                "OpenAI u otra API compatible con OpenAI en Ajustes ⚙ → Transcripción en la nube.")
    if cfg.get("provider") == "custom" and not chat_url(cfg):
        return f"{what} necesita la URL base de la API (Ajustes ⚙ → Transcripción en la nube)."
    return f"{what} necesita una API key de la nube (Ajustes ⚙ → Transcripción en la nube)."


def _duration_s(v):
    """'6.08s', '2m59.5s', '1h2m', '750ms' (cabeceras de limites de Groq) o '120' (retry-after) -> segundos."""
    s = str(v or "").strip().lower()
    total, num, i = 0.0, "", 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit() or ch == ".":
            num += ch
        elif ch == "m" and s[i:i + 2] == "ms" and num:
            total += float(num) / 1000
            num, i = "", i + 1
        elif ch in "hms" and num:
            total += float(num) * {"h": 3600, "m": 60, "s": 1}[ch]
            num = ""
        i += 1
    try:
        return total + (float(num) if num else 0.0)
    except ValueError:
        return total


def _stamp(t):
    t = int(t or 0)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


# ----------------------------------------------------------------------------- agente de IA instalado en el PC
# Resumen con la cuenta del propio usuario a traves de un agente de linea de comandos que ya tiene instalado y con la
# sesion iniciada (Claude Code hoy; Codex CLI y Gemini CLI, previstos). La app no ve ni guarda credenciales: lanza el
# programa con el texto por la entrada estandar y recoge la respuesta. El texto de la reunion sale del PC hacia el
# proveedor del agente, bajo la cuenta (personal o de empresa) con la que el usuario inicio sesion: Ajustes lo avisa.
AGENTE_TIMEOUT_S = 600   # un resumen de una reunion larga, en una sola llamada
AGENTE_CHECK_S = 120     # «Comprobar»: la version y una peticion minima (la primera vez el programa arranca lento)
AGENTE_VERSION_S = 30
AGENTE_KILL_S = 5        # tras matarlo, cuanto se espera a que suelte las tuberias
AGENTE_PIPE_GRACE_S = 2  # tras salir, cuanto se espera a lo que quede en sus tuberias (no a un hijo que las herede)
VERSION_RX = re.compile(r"\d+(?:\.\d+)+")
_NT = os.name == "nt"


def windows_path():
    """El PATH tal como esta ahora en el registro (usuario + sistema) mas el heredado: un instalador (winget, npm, el
    nativo) cambia el PATH y el servidor, que sigue en segundo plano desde antes, no se entera. None fuera de
    Windows."""
    if not _NT:
        return None
    partes = []
    try:
        import winreg
        for root, sub in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, sub) as k:
                    v, _ = winreg.QueryValueEx(k, "Path")
                partes.append(os.path.expandvars(str(v)))
            except OSError:
                pass
    except ImportError:
        pass
    partes.append(os.environ.get("PATH", ""))
    return os.pathsep.join(x for x in partes if x)


def windows_env(base=None):
    """El entorno de `base` (os.environ si no se da) con las variables que hay AHORA en el registro (las de sistema y,
    encima, las de usuario) y el PATH de windows_path(). Una PowerShell nueva las ve; el servidor, que arrancó antes,
    no (07/11/2026, cuenta de empresa): el proxy y el certificado de la empresa (HTTPS_PROXY, NODE_EXTRA_CA_CERTS),
    CLAUDE_CONFIG_DIR, CLAUDE_CODE_GIT_BASH_PATH o las de Bedrock y las pasarelas, puestas con setx o por IT después
    de arrancar Chuleta. Las que el registro no tiene se quedan como estaban. Fuera de Windows, `base` tal cual."""
    env = dict(os.environ if base is None else base)
    if not _NT:
        return env
    try:
        import winreg
    except ImportError:
        return env
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, r"Environment")):
        try:
            with winreg.OpenKey(root, sub) as k:
                i = 0
                while True:
                    try:
                        name, v, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if isinstance(name, str) and name and isinstance(v, str) and name.upper() != "PATH":
                        env[name.upper()] = os.path.expandvars(v)
        except OSError:
            pass
    env["PATH"] = windows_path() or env.get("PATH", "")
    return env


def _version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s))


class AgenteLocal:
    """Base de los agentes: donde esta el ejecutable, su version y una llamada de chat (system + user -> texto).
    SINGLE_CHARS y CHUNK_CHARS: contexto grande, una reunion de 3 h cabe en una llamada (los usa Summarizer)."""
    tipo = label = ""
    exe_names = ()
    win_paths = ()      # (variable de entorno, ruta relativa con comodines) de sus instaladores en Windows
    # la copia que trae la extension de un editor (respaldo si no hay programa suelto): id de la extension y ruta del
    # ejecutable dentro de su carpeta
    ext_id = ""
    ext_bin = ()
    # carpetas de extensiones de cada editor, dentro del perfil del usuario, y su nombre para la interfaz
    EDITORES = ((".vscode", "VS Code"), (".vscode-insiders", "VS Code Insiders"), (".cursor", "Cursor"),
                (".windsurf", "Windsurf"), (".vscode-oss", "VSCodium"))
    MODELOS = ()        # (id, etiqueta)
    MODELO = ""
    SINGLE_CHARS = 400_000
    CHUNK_CHARS = 200_000
    PROMPT = ("Sigue las INSTRUCCIONES que vienen por la entrada estandar y aplicalas al texto que las sigue. "
              "Responde solo con el resultado pedido.")

    def __init__(self, settings_dir=None):
        self.settings_dir = settings_dir

    @classmethod
    def find(cls):
        """Ruta del ejecutable o None. En Windows, primero con extension (.exe antes que .cmd: el .cmd de npm pasa
        por cmd.exe, y el `claude` sin extension es un script de bash), con el PATH del registro ademas del heredado,
        y despues las carpetas de sus instaladores (nativo, winget, npm)."""
        names = list(cls.exe_names)
        if _NT:
            names = [n + ext for n in cls.exe_names for ext in (".exe", ".cmd")] + names
        for path in ((None, windows_path()) if _NT else (None,)):
            for n in names:
                p = shutil.which(n, path=path)
                if p:
                    return p
        for var, rel in cls.win_paths:
            base = os.environ.get(var)
            if not base:
                continue
            for p in sorted(glob.glob(os.path.join(base, rel)), reverse=True):
                if os.path.isfile(p):
                    return p
        return cls.find_extension()

    @classmethod
    def _home(cls):
        return os.environ.get("USERPROFILE") or os.path.expanduser("~")

    @classmethod
    def extension_dirs(cls):
        """[(carpeta de extensiones, editor)]: las de cada editor en el perfil y la de VS Code portable."""
        home = cls._home()
        dirs = [(os.path.join(home, d, "extensions"), name) for d, name in cls.EDITORES]
        portable = os.environ.get("VSCODE_PORTABLE")
        if portable:
            dirs.append((os.path.join(portable, "extensions"), "VS Code"))
        return dirs

    @classmethod
    def find_extension(cls):
        """La copia del programa que trae la extension del editor (07/11/2026: Bernardo usa Claude Code en VS Code con
        la cuenta de su empresa y puede no tener el programa suelto; la extension no lo pone en el PATH). Carpeta
        «<id>-<version>[-<plataforma>]» (p. ej. anthropic.claude-code-2.1.319-win32-x64, con el ejecutable en
        resources\\native-binary\\claude.exe: paquete win32-x64 de Open VSX, 07/11/2026). La version mas alta de
        todos los editores; sin las que el editor ya ha dado de baja (su fichero .obsolete). None si no hay."""
        if not cls.ext_id or not cls.ext_bin:
            return None
        rx = re.compile(re.escape(cls.ext_id.lower()) + r"-(\d+(?:\.\d+)+)(?:-[a-z0-9_-]+)?$")
        best = None
        for d, _ in cls.extension_dirs():
            try:
                names = os.listdir(d)
            except OSError:
                continue
            try:
                with open(os.path.join(d, ".obsolete"), encoding="utf-8") as f:
                    obsolete = {str(k).lower() for k, v in (json.load(f) or {}).items() if v}
            except (OSError, ValueError, AttributeError):
                obsolete = set()
            for n in names:
                m = rx.match(n.lower())
                if not m or n.lower() in obsolete:
                    continue
                p = os.path.join(d, n, *cls.ext_bin)
                if os.path.isfile(p):
                    key = _version_tuple(m.group(1))
                    if best is None or key > best[0]:
                        best = (key, p)
        return best[1] if best else None

    @classmethod
    def origen(cls, path):
        """De donde sale `path`, para Ajustes y el log: «Claude Code de VS Code» si es la copia de una extension,
        si no la etiqueta («Claude Code»)."""
        p = os.path.normcase(os.path.abspath(str(path or "")))
        for d, name in cls.extension_dirs():
            if cls.ext_id and p.startswith(os.path.normcase(os.path.abspath(d)) + os.sep):
                return f"{cls.label} de {name}"
        return cls.label

    @staticmethod
    def _kill(proc):
        """Mata el proceso y sus hijos: con el .cmd de npm, claude.exe es nieto de cmd.exe y proc.kill() solo mataria
        a cmd.exe (el nieto seguiria con las tuberias abiertas y communicate() no volveria)."""
        try:
            if _NT:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                               creationflags=NO_WINDOW)
            else:
                proc.kill()
        except Exception:
            pass

    log = None  # el servidor pone aqui su log (lo que no se ve en la interfaz: tiempos, hijos con la salida abierta)

    @classmethod
    def run(cls, cmd, stdin_text=None, timeout=AGENTE_TIMEOUT_S, cancelled=lambda: False, cwd=None, env=None):
        """Lanza el programa sin ventana, le pasa stdin_text y devuelve (codigo, stdout, stderr). Se puede cancelar
        (se mata con sus hijos) y tiene tope de tiempo. La entrada se escribe desde un hilo propio: communicate() con
        reintentos no sigue escribiendo en Python anterior a 3.13.10, y asi tampoco se bloquea mientras escribe.
        Se espera a que SALGA el proceso, no a que se cierren sus tuberias (07/11/2026): communicate() esperaba al fin
        de stdout y stderr, y en Windows un hijo que las herede y siga vivo (el actualizador de Claude Code, por
        ejemplo) lo alarga hasta que ese hijo acaba. Al salir, AGENTE_PIPE_GRACE_S para recoger lo que quede."""
        t0 = time.time()
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=NO_WINDOW, cwd=cwd, env=env)
        except OSError as e:
            raise EngineError(f"No se ha podido lanzar {cls.label} ({e.__class__.__name__}: {e})")
        data = (stdin_text or "").encode("utf-8")
        w, proc.stdin = proc.stdin, None

        def feed():
            try:
                w.write(data)
                w.close()
            except (OSError, ValueError):
                pass
        threading.Thread(target=feed, daemon=True, name="agente-stdin").start()
        bufs = {"out": [], "err": []}

        def drain(f, key):
            try:
                for chunk in iter(lambda: f.read1(65536), b""):
                    bufs[key].append(chunk)
            except (OSError, ValueError):
                pass
        readers = [threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True, name="agente-stdout"),
                   threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True, name="agente-stderr")]
        for r in readers:
            r.start()
        end = time.time() + timeout
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if not cancelled() and time.time() <= end:
                    continue
                cls._kill(proc)
                try:
                    proc.wait(timeout=AGENTE_KILL_S)
                except subprocess.TimeoutExpired:
                    pass
                if cancelled():
                    raise Cancelled()
                raise EngineError(f"{cls.label} no ha contestado en {timeout:.0f} s")
        salida = time.time() - t0
        plazo = time.time() + AGENTE_PIPE_GRACE_S  # uno para las dos tuberias, no uno detras de otro
        for r in readers:
            r.join(max(0.0, plazo - time.time()))
        if any(r.is_alive() for r in readers) and cls.log:  # un hijo sigue con la salida abierta: no se le espera
            cls.log(f"   agente: {cls.label} salió en {salida:.1f} s, pero un proceso hijo seguía con su salida "
                    f"abierta; no se le espera ({os.path.basename(str(cmd[0]))} {' '.join(map(str, cmd[1:2]))})")
        out, err = b"".join(bufs["out"]), b"".join(bufs["err"])
        return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")

    @staticmethod
    def _env():
        return dict(os.environ)

    @staticmethod
    def argv0(path):
        """Lo que hay que lanzar para `path`. Un .cmd de npm pasa por cmd.exe, que rompe una ruta con «&» o «%» (medido
        en Windows el 07/11/2026: «'100%' is not recognized as an internal or external command») y no es de fiar con un
        argumento vacio (--tools ""): se lanza lo mismo que lanza el .cmd, node con su .js o el .exe, sin cmd.exe. Si
        el .cmd no es de los de npm, tal cual."""
        if not str(path).lower().endswith((".cmd", ".bat")):
            return [path]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(20000)
        except OSError:
            return [path]
        d = os.path.dirname(path)
        # «"%dp0%\node_modules\@anthropic-ai\claude-code\cli.js" %*» (o …\bin\claude.exe): el ultimo es el programa
        for rel in reversed(re.findall(r'"%~?dp0%?\\?([^"%]+)"', text)):
            full = os.path.normpath(os.path.join(d, rel.replace("\\", "/").lstrip("/")))
            low = full.lower()
            if os.path.basename(low) == "node.exe" or not low.endswith((".js", ".mjs", ".cjs", ".exe")):
                continue
            if not os.path.isfile(full):
                return [path]
            if low.endswith(".exe"):
                return [full]
            node = os.path.join(d, "node.exe")
            node = node if os.path.isfile(node) else shutil.which("node")
            return [node, full] if node else [path]
        return [path]

    @classmethod
    def version(cls, path):
        """«2.1.314» (lo que parece una version en la primera linea de --version; si no, esa linea)."""
        code, out, err = cls.run(cls.argv0(path) + ["--version"], timeout=AGENTE_VERSION_S, env=cls._env())
        lines = (out or err).strip().splitlines()
        if code != 0 or not lines:
            detail = (err or out).strip()[:160]
            raise EngineError(f"{cls.label} no responde a --version" + (f": {detail}" if detail else ""))
        m = VERSION_RX.search(lines[0])
        return m.group(0) if m else lines[0][:40]

    def chat(self, system, user, max_tokens, report, cancelled, model=None, timeout=None, path=None):
        raise NotImplementedError

    def check(self, path, model=None, cancelled=lambda: False):
        """«Comprobar» en Ajustes: la version y una peticion minima con la cuenta del usuario y el modelo elegido.
        Devuelve (version, segundos)."""
        t0 = time.time()
        self.tiempos = {}  # lo que tarda cada paso, para el log (la primera vez en Windows puede ser mucho)
        version = self.version(path)
        self.tiempos["version_s"] = round(time.time() - t0, 1)
        t1 = time.time()
        try:
            out, self.modelo_usado = self.chat("Responde exactamente con la palabra OK, sin nada más.", "OK", 10,
                                               lambda **k: None, cancelled, model=model, timeout=AGENTE_CHECK_S,
                                               path=path)
        finally:
            self.tiempos["peticion_s"] = round(time.time() - t1, 1)
        if "OK" not in out.upper():
            raise EngineError(f"{self.label} ha contestado algo inesperado: {out.strip()[:80]!r}")
        return version, time.time() - t0


class ClaudeCode(AgenteLocal):
    """Claude Code (`claude -p`): salida en JSON (result, is_error, modelUsage), sin herramientas, sin guardar la
    sesion, sin servidores MCP ni hooks del usuario. `--bare` no vale: ese modo no usa la sesion iniciada (solo API
    key) y aqui lo que se quiere es la cuenta del usuario. Las instrucciones van por la entrada estandar, no como
    argumento: el .cmd de npm en Windows pasa por cmd.exe y estropea los textos largos con comillas."""
    tipo, label = "claude", "Claude Code"
    exe_names = ("claude",)
    win_paths = (("USERPROFILE", os.path.join(".local", "bin", "claude.exe")),  # instalador nativo
                 ("LOCALAPPDATA", os.path.join("Microsoft", "WinGet", "Links", "claude.exe")),  # winget (enlace)
                 ("LOCALAPPDATA", os.path.join("Microsoft", "WinGet", "Packages", "Anthropic.ClaudeCode_*",
                                               "claude.exe")),  # winget sin enlace (sin modo desarrollador)
                 ("APPDATA", os.path.join("npm", "claude.cmd")))  # npm
    ext_id = "anthropic.claude-code"  # la extension de VS Code (y Cursor, Windsurf…) trae su propia copia
    ext_bin = ("resources", "native-binary", "claude.exe" if _NT else "claude")
    MODELOS = (("haiku", "Haiku · el más rápido"), ("sonnet", "Sonnet · equilibrado (recomendado)"),
               ("opus", "Opus · el más capaz, más lento"))
    MODELO = "sonnet"
    SESION = ("login", "log in", "not authenticated", "authentication required", "sign in", "auth token", "oauth",
              "invalid api key", "sesión")
    # antes que SESION (07/11/2026): un proxy que pide credenciales («407 Proxy Authentication Required») contiene
    # «authentication required» y se decia «sin sesion iniciada»: volver a iniciar sesion no lo arregla
    RED = ("proxy", " 407", "connection error", "connection refused", "econnrefused", "econnreset", "etimedout",
           "enotfound", "getaddrinfo", "certificate", "self signed", "self-signed", "unable to get local issuer",
           "unable to verify", "network error", "socket hang up", "temporary network issue",
           "check your connection", "unable to connect to api")
    # Los textos de aqui en adelante son los de Claude Code 2.1.319 (sus cadenas, leidas en el binario sin ejecutarlo,
    # y code.claude.com/docs/en/errors, 07/11/2026), salvo los que ya habia.
    # La organizacion (Team/Enterprise, o la de Console de una API key) no deja: pedir acceso a su IT. Nunca se
    # sugiere la cuenta personal: es la reunion de su trabajo
    POLITICA = ("organization has disabled", "disabled by your organization", "not allowed by your organization",
                "your organization does not allow", "blocked by your organization", "organization policy",
                "not allowed for this organization", "organization has been disabled", "disabled organization",
                "allowed by your organization", "your organization requires", "your organization's policy",
                "forceloginorguuid", "administrator policy requires")
    # sesion que no se puede renovar: caducada, revocada o con el tiempo maximo que fija la empresa
    CADUCADA = ("login expired", "token revoked", "reached its time limit", "token has expired", "oauth token expired",
                "session expired")
    # la renueva otro proceso a la vez (VS Code abierto, por ejemplo): esperar, no iniciar sesion otra vez
    RENOVANDO = ("another claude code process is refreshing",)
    DISCO = ("could not refresh your login: the disk",)
    AYUDANTE = ("apikeyhelper script is failing", "re-authenticate with your provider")
    # el modelo elegido no esta para esta cuenta (pasarela, Bedrock o la empresa): se repite sin --model
    MODELO_KO = ("issue with the selected model", "is not available on your", "model_not_found", "invalid model name",
                 "model identifier is invalid", "restricts model selection", "block the default model",
                 "is restricted by your organization's settings")
    GIT_BASH = ("git-bash", "git bash")

    def _dir(self):
        return self.settings_dir or tempfile.gettempdir()

    def _settings_file(self):
        """`--settings` con un fichero: solo desactiva los hooks del usuario (no tienen por que correr en un resumen,
        y uno de UserPromptSubmit recibiria la reunion entera). Se reescribe si no dice exactamente eso: un fichero
        corrupto se ignoraria en silencio y los hooks volverian a correr."""
        p = os.path.join(self._dir(), "agente-claude-settings.json")
        want = json.dumps({"disableAllHooks": True})
        try:
            with open(p, encoding="utf-8") as f:
                cur = f.read()
        except OSError:
            cur = None
        if cur != want:
            os.makedirs(self._dir(), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(want)
        return p

    def _cwd(self):
        """Una carpeta vacia: sin CLAUDE.md ni .claude/ de ningun proyecto que cambien el resumen."""
        d = os.path.join(self._dir(), "agente-cwd")
        os.makedirs(d, exist_ok=True)
        return d

    CLAVES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    @staticmethod
    def _pasarela(env):
        """La URL de la pasarela de la empresa (ANTHROPIC_BASE_URL que no es la API de Anthropic), o None."""
        url = (env.get("ANTHROPIC_BASE_URL") or "").strip()
        host = re.sub(r"^[a-z]+://", "", url.lower()).split("/")[0].split(":")[0]
        return url if url and host not in ("api.anthropic.com", "") else None

    @staticmethod
    def _env():
        """El entorno del usuario tal como lo veria una PowerShell nueva (windows_env: lo puesto en el registro despues
        de arrancar Chuleta tambien cuenta), con dos cambios solo para estos procesos (la instalacion de Claude Code
        del usuario no cambia):
        - Sin ANTHROPIC_API_KEY ni ANTHROPIC_AUTH_TOKEN: con -p la clave del entorno se usaria siempre, y el gasto iria
          a la API y no a la cuenta del usuario, que es lo que prometen los textos de Ajustes; con una cuenta de empresa
          que exige su login (forceLoginOrgUUID), Claude Code ademas se niega a arrancar con ellas. Se quedan si hay una
          pasarela de la empresa (ANTHROPIC_BASE_URL propia, 07/11/2026): ahi son la credencial de la empresa.
        - Sin trafico no esencial (07/11/2026; CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, code.claude.com/docs/en/
          env-vars): ni actualizacion automatica, ni telemetria, ni comprobaciones de disponibilidad. No toca lo de la
          empresa: los ajustes que manda la organizacion (server-managed settings) no estan en esa lista y su descarga
          solo se salta con un CLAUDE_CODE_USE_* o un ANTHROPIC_BASE_URL propio (docs/en/server-managed-settings)."""
        env = windows_env()
        if not ClaudeCode._pasarela(env):
            for k in ClaudeCode.CLAVES:
                env.pop(k, None)
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        return env

    def chat(self, system, user, max_tokens, report, cancelled, model=None, timeout=None, path=None):
        path = path or self.find()
        if not path:
            raise EngineError(f"No encuentro {self.label} en este PC. Sigue el paso a paso de Ajustes y pulsa "
                              "«Comprobar».")
        model = model or self.MODELO
        self.origen_ruta = self.origen(path)
        self.aviso = None  # si el modelo elegido no estaba y se ha usado el de su Claude Code (lo enseña «Comprobar»)

        def cmd(m):
            return self.argv0(path) + ["-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                                       "--strict-mcp-config", "--settings", self._settings_file()] + (
                                           ["--model", m] if m else []) + [self.PROMPT]
        report(stage=f"Resumiendo con {self.label} ({model})…")
        stdin_text, total = f"INSTRUCCIONES:\n{system}\n\n{user}", timeout or AGENTE_TIMEOUT_S
        t0 = time.time()
        code, out, err = self.run(cmd(model), stdin_text, timeout=total, cancelled=cancelled, cwd=self._cwd(),
                                  env=self._env())
        fallo = self._texto_error(out, err)
        if fallo is not None and any(k in fallo.lower() for k in self.MODELO_KO):
            # el modelo elegido no esta para esta cuenta (lo restringe la empresa, o una pasarela o Bedrock lo llaman
            # de otra forma): otra vez sin --model, con el de su Claude Code, que es el que fija su empresa
            log = type(self).log  # por la clase: desde la instancia, la funcion log del servidor recibiria self
            if log:
                log(f"   agente: {self.label} no puede usar «{model}» con esta cuenta "
                    f"({' '.join(fallo.split())[:120]}); se repite con su modelo predeterminado")
            report(stage=f"Resumiendo con {self.label} (su modelo predeterminado)…")
            code2, out2, err2 = self.run(cmd(None), stdin_text, timeout=max(30.0, total - (time.time() - t0)),
                                         cancelled=cancelled, cwd=self._cwd(), env=self._env())
            if self._texto_error(out2, err2) is None:
                text, used = self._parse(code2, out2, err2, None)
                self.aviso = (f"El modelo elegido ({model}) no está disponible con esta cuenta: se ha usado el "
                              f"predeterminado de tu {self.label} ({used}).")
                return text, used
        return self._parse(code, out, err, model)

    @staticmethod
    def _resultado(out):
        """(salida sin espacios, el dict del resultado o None)."""
        s, d = out.strip(), None
        if s.startswith(("{", "[")):
            try:
                j = json.loads(s)
            except ValueError:
                j = None
            if isinstance(j, list):  # «verbose» del usuario: la salida son todos los mensajes; el resultado va al final
                j = next((x for x in reversed(j) if isinstance(x, dict) and x.get("type") == "result"), None)
            d = j if isinstance(j, dict) else None
        return s, d

    def _texto_error(self, out, err):
        """El texto del fallo (stderr o el del resultado; nunca la salida entera, que lleva la reunion), o None si la
        salida es un resultado bueno."""
        s, d = self._resultado(out)
        if d is None:
            return err.strip() or ("" if s.startswith(("{", "[")) else s)
        if d.get("is_error") or d.get("type") != "result":
            return str(d.get("result") or d.get("error") or err or "").strip()
        return None

    def _parse(self, code, out, err, model):
        fallo = self._texto_error(out, err)
        if fallo is not None:
            raise EngineError(self._why(code, fallo))
        d = self._resultado(out)[1]
        text = (d.get("result") or "").strip()
        if not text:
            raise EngineError(f"{self.label} no ha devuelto texto")
        used = next(iter((d.get("modelUsage") or {}).keys()), None) or model or "predeterminado"
        return text, used

    def _de_editor(self):
        """La copia de una extension (VS Code…), o None: entonces no hay `claude` en PowerShell y la sesion se inicia
        en el panel de Claude Code del editor (comparten la sesion: %USERPROFILE%\\.claude\\.credentials.json)."""
        o = getattr(self, "origen_ruta", None) or ""
        return o[len(self.label) + 4:] if o.startswith(self.label + " de ") else None

    def _why(self, code, text):
        """Mensaje para Ajustes. text: solo stderr o el texto del resultado (nunca la salida entera, que lleva la
        reunion y podria contener cualquier palabra)."""
        low = text.lower()
        detalle = " ".join(text.split())[:160]
        # lo que dice Claude Code antes del « · » con lo que hay que hacer (en ingles y para su terminal; sin
        # «Please run /login», que explicar.py lee como «sin sesion» y cambiaria la frase entera)
        corto = " ".join(text.split("·")[0].split())[:120]
        editor = self._de_editor()
        entrar = (f"abre {editor} y, en el panel de {self.label}, vuelve a iniciar sesión con tu cuenta" if editor else
                  "en PowerShell escribe claude y luego /login, entra con tu cuenta")
        # «vuelve a probar» y no «pulsa Comprobar»: salen igual en Ajustes («Comprobar») y en el asistente («Probar»);
        # explicar.py les pone su boton (Abrir PowerShell / Probar otra vez)
        if any(k in low for k in self.RENOVANDO):
            return (f"{self.label} está renovando tu sesión en otra ventana (por ejemplo, en VS Code): espera un "
                    "minuto y vuelve a probar.")
        if any(k in low for k in self.DISCO):
            return (f"{self.label} no puede renovar tu sesión porque el disco donde guarda su configuración está lleno "
                    "(iniciar sesión otra vez no lo arregla): libera espacio y vuelve a probar.")
        if any(k in low for k in self.MODELO_KO):
            return (f"{self.label} no puede usar el modelo elegido con esta cuenta ({corto}): elige otro modelo en "
                    "Ajustes o pide a tu IT que lo active, y vuelve a probar.")
        if any(k in low for k in self.POLITICA):
            if "api key authentication" in low:  # la organizacion solo admite su login (claude.ai, con SSO)
                return (f"Tu organización no deja usar {self.label} con esta cuenta ({corto}): no admite API keys; "
                        f"{entrar} de Claude (la de la empresa) y vuelve a probar.")
            return (f"Tu organización no deja usar {self.label} con esta cuenta ({detalle}): pide acceso a quien "
                    "gestiona Claude en tu empresa (IT) y vuelve a probar.")
        if any(k in low for k in self.CADUCADA):
            return (f"La sesión de {self.label} ha caducado ({corto}): {entrar} (la de tu empresa, si es la que "
                    "usas) y vuelve a probar.")
        if any(k in low for k in self.AYUDANTE):
            return (f"El programa de tu empresa que da las credenciales a {self.label} (apiKeyHelper) está fallando "
                    f"({corto}): vuelve a identificarte como te indique tu IT y vuelve a probar.")
        if any(k in low for k in self.RED):
            return (f"{self.label} no puede conectar con Anthropic desde esta red ({detalle}). En la red de una "
                    "empresa suele ser el proxy: prueba con otra red o pide a tu IT que permita api.anthropic.com y "
                    "claude.ai, y vuelve a probar.")
        if any(k in low for k in self.GIT_BASH):
            return (f"{self.label} necesita Git para Windows (Git Bash) en este PC ({detalle}): en PowerShell escribe "
                    "winget install --id Git.Git -e (o descárgalo de git-scm.com) y vuelve a probar.")
        if any(k in low for k in self.SESION):
            if editor:  # sin «sin sesion iniciada»: explicar.py la cambiaria por la de PowerShell, que aqui no vale
                return (f"{self.label} de {editor} está instalado pero no tiene la sesión abierta: abre {editor} y, en "
                        f"el panel de {self.label}, inicia sesión con tu cuenta (la de tu empresa, si es la que usas); "
                        "luego vuelve a probar.")
            return (f"{self.label} está instalado pero sin sesión iniciada: abre una terminal, escribe claude e inicia "
                    "sesión con tu cuenta (personal o de empresa); luego vuelve a pulsar «Comprobar».")
        return f"{self.label} ha fallado" + (f" (código {code})" if code else "") + (f": {text[:200]}" if text else "")


AGENTES = {"claude": ClaudeCode}


class Summarizer:
    """Resumen estilo Notion AI Meeting Notes con un modelo de chat (Groq u OpenAI).

    El plan gratuito de Groq permite ~8K tokens por minuto y una reunion de 1 h son ~15-20K: se
    resume por partes (apuntes de cada trozo) y luego se juntan en el resumen final, respetando el
    ritmo que marca Groq en sus cabeceras de limites."""

    CHUNK_CHARS = 11000    # ~3K tokens por trozo + respuesta: cabe en 8K por minuto
    SINGLE_CHARS = 15000   # reuniones cortas: una sola llamada
    TRANSLATE_MODEL = "openai/gpt-oss-20b"  # traduccion del directo: rapido y barato en cupo

    def __init__(self, keys=None, agente=None):
        self.remaining, self.reset_at = None, 0.0
        self.keys = keys or KeyPool()  # compartido con CloudEngine: la rotacion de claves es la misma
        self.agente = agente  # AgenteLocal: el resumen con la cuenta del usuario, en vez de la API de chat
        self.SINGLE_CHARS = agente.SINGLE_CHARS if agente else Summarizer.SINGLE_CHARS
        self.CHUNK_CHARS = agente.CHUNK_CHARS if agente else Summarizer.CHUNK_CHARS

    @staticmethod
    def lines(segments, width=260):
        """Transcripcion compacta: frases juntadas en lineas de ~260 caracteres con su minuto. Si las frases traen
        quien habla ("who"), cada cambio de hablante abre linea y esta lleva su nombre: "[12:34] Javier: …"."""
        out, cur, t0, who = [], "", None, None

        def flush():
            out.append((f"[{_stamp(t0)}] " if t0 is not None else "") + (f"{who}: " if who else "") + cur)
        for s in segments:
            w = s.get("who")
            if cur and w != who:
                flush()
                cur, t0 = "", None
            if t0 is None:
                t0, who = s.get("start"), w
            cur = (cur + " " + s["text"]).strip()
            if len(cur) >= width:
                flush()
                cur, t0 = "", None
        if cur:
            flush()
        return out

    def _chat(self, cfg, system, user, max_tokens, report, cancelled, model=None, temperature=0.2, timeout=180,
              attempts=8, budget=None):
        """(texto, modelo). attempts: intentos como mucho. budget: segundos que puede durar todo, esperas y peticiones
        incluidas (el ultimo paso de la traduccion del directo). Con el, una espera que no deja BUDGET_MIN_S para la
        peticion, y BUDGET_SLACK_S de margen, no se hace (se falla ya), y cada peticion lleva como topes lo que quede:
        un tercio para conectar (como mucho CONNECT_TIMEOUT) y el resto, como mucho timeout, para la respuesta."""
        if self.agente is not None:  # con el agente del PC: una llamada, sin cupos ni claves
            return self.agente.chat(system, user, max_tokens, report, cancelled,
                                    model=model or cfg.get("agente_modelo"), timeout=None)
        import requests
        provider = cfg.get("provider", "groq")
        url = chat_url(cfg)
        missing = chat_missing(cfg)
        if missing:
            raise EngineError(missing)
        end = None if budget is None else time.time() + budget
        model = model or cfg.get("summary_model") or (SUMMARY_MODELS[0] if provider in ("groq", "custom")
                                                      else "gpt-4o-mini")
        body = {"model": model, "temperature": temperature, "max_completion_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"  # razona poco: gasta menos cupo y tarda menos
        need = (len(system) + len(user)) // 3 + max_tokens
        family = "chat"
        last = None

        def pause(secs):
            """Espera secs (se puede cancelar). Con plazo, si despues no quedarian BUDGET_MIN_S para la peticion y el
            margen BUDGET_SLACK_S (al despertar se pasa algo: sin el, una espera justa acababa sin peticion), no espera:
            False. Sin nada que esperar, True: lo que quede lo mira la peticion."""
            if secs <= 0:
                return True
            if end is not None and time.time() + secs + BUDGET_MIN_S + BUDGET_SLACK_S > end:
                return False
            self._sleep(secs, cancelled)
            return True
        for attempt in range(attempts):
            if cancelled():
                raise Cancelled()
            # al ritmo de Groq: si no queda cupo de tokens en este minuto, se espera a que se libere
            if self.remaining is not None and self.remaining < need and time.time() < self.reset_at:
                wait = self.reset_at - time.time() + 1
                report(stage=f"Esperando al límite por minuto de Groq ({wait:.0f} s)…")
                if not pause(wait):
                    last = f"hay que esperar {wait:.0f} s al límite por minuto"
                    break
            key = self.keys.pick(cfg, family, model=model)
            wait = self.keys.reserve(key, family, model, per_minute=not daily_quota(cfg))
            if not pause(wait):
                last = f"hay que esperar {wait:.0f} s al límite por minuto" if wait >= 1 else last
                break
            connect, read = None, timeout
            if end is not None:  # con plazo: la peticion, con lo que quede (si no quedan BUDGET_MIN_S, ni se manda)
                left = end - time.time()
                if left < BUDGET_MIN_S:
                    break
                connect = min(CONNECT_TIMEOUT, left / 3)
                read = min(timeout, left - connect)
            try:
                r = HTTP.post(url, read, connect_timeout=connect, json=body, headers={"Authorization": f"Bearer {key}"})
            except requests.RequestException as e:
                last = f"sin conexión con {provider} ({e.__class__.__name__})"
                if attempt < attempts - 1:
                    report(stage=f"Sin conexión con {provider} ({e.__class__.__name__}), reintentando…")
                    if not pause(3 * 2 ** attempt):
                        break
                continue
            self.keys.note(key, family, r.status_code, r.headers, model)
            if "x-ratelimit-remaining-tokens" in r.headers:
                try:
                    self.remaining = int(r.headers["x-ratelimit-remaining-tokens"])
                    self.reset_at = time.time() + _duration_s(r.headers.get("x-ratelimit-reset-tokens"))
                except ValueError:
                    pass
            if r.status_code == 200:
                choices = json_body(r).get("choices")
                msg = (choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict)
                       else {}).get("message") or {}
                text = (msg.get("content") or "").strip() if isinstance(msg, dict) else ""
                if text:
                    return text, model
                raise EngineError("El modelo devolvió una respuesta vacía.")
            others = len(cloud_keys(cfg)) > 1
            if r.status_code in (401, 403):  # clave no valida: queda marcada y, si hay otra, se sigue con ella
                if others and self.keys.pick(cfg, family, model=model) != key:
                    report(stage="Una de las claves no es válida: se sigue con la siguiente…")
                    continue
                raise EngineError(api_error(r))
            if r.status_code == 429:
                wait = _duration_s(r.headers.get("retry-after", "")) or 20
                if self.keys.blocked(key, family, model) and others and self.keys.pick(cfg, family, model=model) != key:
                    report(stage="Esa cuenta de Groq ha agotado su cupo: se sigue con la siguiente clave…")
                    continue
                if wait > 300:  # limite diario de tokens: no tiene sentido esperar aqui
                    raise EngineError(f"Límite diario de Groq alcanzado (se libera en ~{wait / 3600:.1f} h). "
                                      "Vuelve a pulsar «Resumir» más tarde.")
                last = api_error(r)
                if attempt < attempts - 1:
                    report(stage=f"Groq pide esperar {wait:.0f} s (límite por minuto)…")
                    if not pause(wait + 1):
                        break
                continue
            if r.status_code in (500, 502, 503):
                last = api_error(r)
                if attempt < attempts - 1 and not pause(5):
                    break
                continue
            if r.status_code == 400 and "max_completion_tokens" in body and "max_completion_tokens" in (r.text or ""):
                body["max_tokens"] = body.pop("max_completion_tokens")  # pasarela antigua: nombre anterior
                report(stage="La API no admite max_completion_tokens: reintentando con max_tokens…")
                continue
            raise EngineError(api_error(r))
        how = " a tiempo" if end is not None else ": el servicio no respondió tras varios intentos"
        raise EngineError(f"{'La traducción' if system == self.TRANSLATE_SYSTEM else 'El resumen'} no ha podido "
                          f"hacerse{how}" + (f" ({last})." if last else "."))

    @staticmethod
    def _sleep(secs, cancelled):
        end = time.time() + secs
        while time.time() < end:
            if cancelled():
                raise Cancelled()
            time.sleep(min(0.25, max(0.0, end - time.time())))

    TRANSLATE_SYSTEM = (
        "Eres un traductor simultáneo de reuniones de trabajo. Traduce al español, con fidelidad y sin resumir, "
        "comentar ni completar, el fragmento de transcripción que te pasen (suele venir en portugués o inglés; "
        "si una frase ya está en español, cópiala tal cual). Conserva nombres propios, siglas, cifras, fechas y "
        "términos técnicos. El fragmento puede empezar o acabar a mitad de frase: tradúcelo igual, sin inventar "
        "el resto. Devuelve solo la traducción, sin comillas ni notas.")

    @classmethod
    def translate_model(cls, cfg):
        """Modelo de la traduccion del directo: gpt-oss-20b en Groq (rapido y barato en cupo), gpt-4o-mini en OpenAI
        y, en una pasarela propia, el modelo del resumen (el modelo de chat que se configura para ella: no se da por
        hecho que tenga los de Groq). None si no hay chat (Azure OpenAI)."""
        if not chat_url(cfg):
            return None
        provider = cfg.get("provider", "groq")
        if provider == "custom":
            return (cfg.get("summary_model") or "").strip() or cls.TRANSLATE_MODEL
        return cls.TRANSLATE_MODEL if provider == "groq" else "gpt-4o-mini"

    def translate(self, cfg, text, report, cancelled, model=None, timeout=60, attempts=8, budget=None):
        """Traduce al espanol un trozo del directo con un modelo de chat rapido (translate_model). Devuelve (texto,
        modelo). Comparte claves y rotacion con la transcripcion. timeout, attempts y budget (ver _chat): el ultimo
        paso, al terminar el directo, tiene un plazo para no retener el final de la sesion."""
        text = " ".join((text or "").split())
        model = model or self.translate_model(cfg)
        if not text:
            return "", model or self.TRANSLATE_MODEL
        missing = chat_missing(cfg, "La traducción del directo")
        if missing:
            raise EngineError(missing)
        max_tokens = min(1200, 120 + len(text) // 2)  # el espanol ocupa ~1 token por 3-4 caracteres; con margen
        return self._chat(cfg, self.TRANSLATE_SYSTEM, text, max_tokens, report, cancelled, model=model,
                          temperature=0.1, timeout=timeout, attempts=attempts, budget=budget)

    def summarize(self, segments, meta, cfg, report, cancelled):
        """meta: title, date, duration, participants, notes, lang, extra. Devuelve (markdown, modelo).
        Todo lo de meta va en el prompt al proveedor: participants son solo los que el usuario escribio o confirmo
        (los sugeridos por Outlook/Teams se quedan en el PC hasta entonces: server.participantes_confirmados)."""
        code = cfg.get("summary_lang") or "es"
        lang = SUMMARY_LANGS.get(code, SUMMARY_LANGS["es"])
        h = SUMMARY_HEADINGS.get(code, SUMMARY_HEADINGS["es"])
        system = ("Eres un asistente que resume reuniones de trabajo. Usa solo lo que aparece "
                  "en el texto, sin inventar nombres, cifras ni acuerdos. Escribe en " + lang + ". Cuando cites "
                  "un momento usa exactamente el formato [mm:ss] o [h:mm:ss] de la transcripción.")
        head = f"Reunión «{meta.get('title')}» ({meta.get('date')}, {meta.get('duration')})."
        if meta.get("participants"):
            head += f" Participantes indicados: {meta['participants']}."
        if (meta.get("about") or "").strip():  # contexto libre de la reunion (perfil o pagina de la reunion)
            head += f" Contexto indicado por el usuario: {' '.join(meta['about'].split())[:ABOUT_CHARS]}"
            head += "" if head.endswith((".", "!", "?")) else "."
        notes = (meta.get("notes") or "").strip()
        if notes:
            head += f"\n\nNotas que tomó el usuario (orden del día, contexto; tenlas en cuenta):\n{notes[:3000]}"
        final_format = (
            "Escribe el resumen en Markdown con exactamente estas secciones"
            + (" (traduce también los títulos de sección a ese idioma)" if code == "same" else "") + ":\n"
            f"## {h['summary']}\n(3 a 5 frases: de qué trató y a qué se llegó)\n"
            f"## {h['points']}\n- punto concreto [mm:ss]  (máximo 8, agrupa los parecidos)\n"
            f"## {h['decisions']}\n- decisión [mm:ss]  (si no hubo, escribe «{h['none_clear']}»)\n"
            f"## {h['next']}\n- [ ] Responsable: tarea — fecha si se dijo [mm:ss]  (si no hay, «{h['none']}»)\n"
            f"## {h['people']}\n- Nombre (rol o empresa si se deduce)\n"
            "Nada de saludos, problemas de audio ni relleno."
            + ("\nLas frases llevan delante quién habla («Nombre:»; «Persona 2» es alguien sin identificar y «Yo» es "
               "el usuario que toma las notas): usa esos nombres como responsables en los próximos pasos y en la lista "
               "de participantes, sin inventar otros." if meta.get("speakers") else "")
            + (f"\nInstrucciones extra: {meta['extra']}" if meta.get("extra") else ""))
        lines = self.lines(segments)
        text = "\n".join(lines)
        if not text.strip():
            raise EngineError("No hay transcripción que resumir.")
        if len(text) <= self.SINGLE_CHARS:  # reunion corta: de una vez
            report(stage="Resumiendo…", frac=0.2)
            out, model = self._chat(cfg, system, f"{head}\n\n{final_format}\n\nTRANSCRIPCIÓN:\n{text}", 2500,
                                    report, cancelled)
            return out, model
        chunks, cur = [], []
        for l in lines:  # trozos por lineas completas
            if cur and sum(len(x) + 1 for x in cur) + len(l) > self.CHUNK_CHARS:
                chunks.append(cur)
                cur = []
            cur.append(l)
        if cur:
            chunks.append(cur)
        notes_parts, model = [], None
        for i, ch in enumerate(chunks, 1):
            report(stage=f"Leyendo la reunión por partes ({i}/{len(chunks)})…",
                   frac=0.05 + 0.75 * (i - 1) / len(chunks))
            span = f"{ch[0][1:ch[0].find(']')]} – {ch[-1][1:ch[-1].find(']')]}" if ch[0].startswith("[") else ""
            prompt = (f"{head}\n\nFragmento {i} de {len(chunks)} de la transcripción ({span}). "
                      "Extrae en viñetas cortas, cada una con su minuto [mm:ss]:\nPUNTOS: temas e ideas importantes\n"
                      "DECISIONES: lo que se decidió\n"
                      "ACCIONES: tareas, con responsable y fecha si se dicen\nPERSONAS: quién habla o se menciona "
                      "(personas y empresas)\nSi una sección no tiene nada, escribe «—». Ignora saludos y relleno."
                      f"\n\nTRANSCRIPCIÓN:\n" + "\n".join(ch))
            part, model = self._chat(cfg, system, prompt, 1200, report, cancelled)
            notes_parts.append(f"### Parte {i} ({span})\n{part}")
        report(stage="Juntando el resumen final…", frac=0.85)
        out, model = self._chat(cfg, system, f"{head}\n\nApuntes extraídos de cada parte de la reunión:\n\n"
                                + "\n\n".join(notes_parts) + f"\n\n{final_format}", 2500, report, cancelled)
        return out, model
