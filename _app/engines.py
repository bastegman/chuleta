# -*- coding: utf-8 -*-
"""
Motores de transcripcion.

  LocalEngine  -> faster-whisper en la CPU del portatil (modo por lotes = mucho mas rapido)
  CloudEngine  -> API compatible con OpenAI: Groq, OpenAI o Azure OpenAI.
                  El audio se convierte a FLAC 16 kHz mono, se corta en trozos por silencios
                  y los trozos se transcriben en paralelo.

Ambos devuelven una lista de segmentos (inicio, fin, texto).
"""
import itertools
import os
import re
import shutil
import subprocess
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


def _whisper_tokenizer():
    """Tokenizador de algun modelo ya descargado, para contar tokens sin cargar Whisper."""
    if "t" not in _TOKENIZER:
        _TOKENIZER["t"] = None
        try:
            import glob
            from huggingface_hub.constants import HF_HUB_CACHE
            from tokenizers import Tokenizer
            files = glob.glob(os.path.join(HF_HUB_CACHE, "models--*whisper*", "snapshots", "*", "tokenizer.json"))
            if files:
                _TOKENIZER["t"] = Tokenizer.from_file(sorted(files)[-1])
        except Exception:
            pass
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
            frames = c.decode(s)
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

def model_cached(name):
    """True si el modelo ya esta descargado en la cache de Hugging Face."""
    try:
        from faster_whisper.utils import _MODELS
        from huggingface_hub.constants import HF_HUB_CACHE
    except ImportError:
        return False
    repo = _MODELS.get(name, name)
    snaps = os.path.join(HF_HUB_CACHE, "models--" + repo.replace("/", "--"), "snapshots")
    try:
        return any(os.path.exists(os.path.join(snaps, s, "model.bin")) for s in os.listdir(snaps))
    except OSError:
        return False


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
        self.p = subprocess.Popen([sys.executable, "-u", os.path.join(here, "worker.py")], cwd=here,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                  creationflags=NO_WINDOW | BELOW_NORMAL)
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
        """Deja el modelo cargado (la primera vez puede descargarlo)."""
        if self.alive() and self.key == (model, int(threads)):
            return False
        if on_progress:
            on_progress({"stage": f"{'Cargando' if model_cached(model) else 'Descargando'} modelo {model}…"})
        self.request({"op": "load", "model": model, "threads": threads}, on_progress=on_progress,
                     timeout=3600, cancelled=cancelled)
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
            except requests.RequestException as e:
                last = f"sin conexión con el servicio ({e.__class__.__name__})"
                wait = 2 ** attempt
            else:
                self.keys.note(key, family, r.status_code, r.headers, qm)
                if r.status_code == 200:
                    tm["infer"] = processing_s(r.headers)
                    return json_body(r)
                last = api_error(r)
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
            report(stage=f"La API pide esperar ({last}). Reintento en {wait:.0f} s…")
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
    return f"HTTP {r.status_code}: {msg or r.text[:200]}"


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


class Summarizer:
    """Resumen estilo Notion AI Meeting Notes con un modelo de chat (Groq u OpenAI).

    El plan gratuito de Groq permite ~8K tokens por minuto y una reunion de 1 h son ~15-20K: se
    resume por partes (apuntes de cada trozo) y luego se juntan en el resumen final, respetando el
    ritmo que marca Groq en sus cabeceras de limites."""

    CHUNK_CHARS = 11000    # ~3K tokens por trozo + respuesta: cabe en 8K por minuto
    SINGLE_CHARS = 15000   # reuniones cortas: una sola llamada
    TRANSLATE_MODEL = "openai/gpt-oss-20b"  # traduccion del directo: rapido y barato en cupo

    def __init__(self, keys=None):
        self.remaining, self.reset_at = None, 0.0
        self.keys = keys or KeyPool()  # compartido con CloudEngine: la rotacion de claves es la misma

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
