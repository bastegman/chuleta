# -*- coding: utf-8 -*-
"""Utilidades puras compartidas por los modulos de la app: errores de la API, texto, SRT, cabecera WAV y el log
(consola + %LOCALAPPDATA%\\AudiosApp\\server.log). No dependen de ROOT ni de los ajustes.
"""
import base64
import logging
import os
import re
import struct
import sys
import time
import traceback
import unicodedata

WINDOWS = sys.platform == "win32"  # grabadora, atajo global, papelera, avisos y Explorador son solo de Windows
# Nombre visible de la app («Notas» hasta el 31/10/2026, «Chuleta» hasta el 09/11/2026, «Tenca» desde entonces):
# titulo de la ventana, mensajes, carpeta raiz y nombre del zip instalable, accesos directos. Centralizado aqui. Lo
# que NO cambia con el, por compatibilidad con las instalaciones y los datos existentes: la carpeta _app, DATA_DIR
# (AudiosApp), la cabecera X-Audios, las claves de los .md, la pestana «Notas» (que es la funcion de tomar notas, no
# la marca) y los identificadores internos de abajo (APP_ID, TOKEN_HEADER, variables de entorno CHULETA_*).
APP_NAME = "Tenca"
# nombres que tuvo la app, del mas reciente al mas antiguo: las listas de vocabulario de la raiz se llamaban asi como
# proyecto, y las reuniones grabadas entonces lo llevan en la cabecera de su .md (server.find_vocab los acepta como
# alias de la raiz). La carpeta Inicio de Windows puede tener aun el acceso directo de «Arrancar con Windows» con el
# nombre anterior (server.inicio_windows_*: se migra al de ahora).
APP_FORMER_NAMES = ("Chuleta", "Notas")
# AppUserModelID: identifica la ventana en la barra de tareas (agrupa, icono, anclar). La misma cadena va en el
# acceso directo que crea instalar.ps1 (System.AppUserModel.ID del .lnk), que es lo que Windows ancla. No lo ve el
# usuario y NO cambia con el nombre: si cambiara, la ventana de Tenca saldria como un boton aparte junto al anclaje
# «Chuleta» que ya tenga hecho (dos iconos), y las notificaciones perderian su ajuste.
APP_ID = "Bernardo.Chuleta"
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


def default_threads():
    return max(4, min(os.cpu_count() or 8, 12))


def fold(s):
    """minusculas y sin acentos, para buscar."""
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def natural_key(name):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", fold(name))]


def clean_name(name):
    name = unicodedata.normalize("NFC", (name or "").strip()).rstrip(". ")
    if not name or BAD_NAME.search(name) or name in (".", ".."):
        raise ApiError('Nombre no válido (no puede llevar \\ / : * ? " < > |)')
    return name


def read_text(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def write_bytes(path, data, append=False):
    """Escribe SIN borrar el archivo: si ya existe, se sustituye (o amplia) su contenido en el sitio.
    Antes se escribia un .tmp y se renombraba encima, y el Explorador y OneDrive veian el .txt y el
    .srt desaparecer y volver a aparecer cada vez (en directo, cada 20-40 s)."""
    for attempt in range(20):  # OneDrive puede tener el archivo abierto un instante mientras lo sube
        try:
            mode = "ab" if append else ("r+b" if os.path.exists(path) else "wb")
            with open(path, mode) as f:
                f.write(data)
                if not append:
                    f.truncate()
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def save_text(path, text, encoding):
    write_bytes(path, text.encode(encoding))


def fmt_srt(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text):
    segs = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = SRT_TIME.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + int(m.group(4).ljust(3, "0")) / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + int(m.group(8).ljust(3, "0")) / 1000
                txt = " ".join(l.strip() for l in lines[i + 1:] if l.strip())
                if txt:
                    segs.append({"start": round(start, 2), "end": round(end, 2), "text": txt})
                break
    return segs


def wav_duration(path, size):
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
                return None
            byte_rate = None
            while True:
                ch = f.read(8)
                if len(ch) < 8:
                    return None
                cid, n = ch[:4], struct.unpack("<I", ch[4:])[0]
                if cid == b"fmt ":
                    fmt = f.read(n)
                    byte_rate = struct.unpack("<I", fmt[8:12])[0]
                    if n % 2:
                        f.read(1)
                elif cid == b"data":
                    if n in (0, 0xFFFFFFFF) or f.tell() + n > size:
                        n = size - f.tell()  # grabacion sin cerrar o RF64
                    return n / byte_rate if byte_rate else None
                else:
                    f.seek(n + (n & 1), 1)
    except (OSError, struct.error):
        return None


# ----------------------------------------------------------------------------- log

LOGGER = logging.getLogger("notas")


def has_console():
    """False con pythonw.exe (el acceso directo): sys.stdout y sys.stderr son None y no hay donde imprimir."""
    return sys.stdout is not None


def setup_logging(data_dir):
    """La consola (si la hay) y, ademas, %LOCALAPPDATA%\\AudiosApp\\server.log con rotacion (3 x 1 MB).
    Con pythonw.exe no hay consola (sys.stdout es None): solo el fichero, que es el registro completo."""
    import logging.handlers
    for h in list(LOGGER.handlers):
        LOGGER.removeHandler(h)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    stream = sys.stdout
    if stream is not None:
        try:  # consola cp1252/cp850 (Windows): los emojis del log no deben tumbar el logging («--- Logging error ---»)
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
        console = logging.StreamHandler(stream)
        console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        LOGGER.addHandler(console)
    if not data_dir:
        return
    try:
        os.makedirs(data_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(os.path.join(data_dir, "server.log"), maxBytes=1_000_000,
                                                  backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(message)s", "%Y-%m-%d %H:%M:%S"))
        LOGGER.addHandler(fh)
    except OSError as e:
        LOGGER.warning("sin server.log (%s)", e)


def log(msg):
    try:
        if not LOGGER.handlers:
            setup_logging(None)  # solo consola hasta que main() diga donde va el fichero
        LOGGER.info(msg)
    except Exception:
        pass


def log_exc():
    """La traza de la excepcion en curso, a la consola y a server.log (antes: traceback.print_exc())."""
    try:
        LOGGER.error(traceback.format_exc().rstrip())
    except Exception:
        if has_console():
            traceback.print_exc()


def alert(title, text):
    """Error que el usuario tiene que ver aunque no haya consola (pythonw.exe): al log siempre y, en Windows sin
    consola, un MessageBox (MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST). Con consola, el texto ya sale por ella."""
    try:
        LOGGER.error("%s: %s", title, text)
    except Exception:
        pass
    if WINDOWS and not has_console():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x10000 | 0x40000)
        except Exception:
            pass


# ----------------------------------------------------------------------------- sesion local (token) y loopback
# Token de sesion: server.py genera uno en cada arranque y lo deja en DATA_DIR\sesion.token (UTF-8, sin salto de
# linea; lo borra al apagarse). /api/* y /media lo exigen en la cabecera X-Chuleta-Token o en la cookie que recibe la
# interfaz al abrir /?t=<token>: en un Windows con varias sesiones (escritorio remoto, AVD, Citrix) el 127.0.0.1 es de
# todos y, sin el, otro usuario podria leer las reuniones o manejar la grabadora. Lo leen el acceso directo (la copia
# que se abre cuando la app ya corre), el aviso flotante, el instalador y las pruebas.
TOKEN_FILE = "sesion.token"
# no cambia con el nombre: el instalador nuevo apaga la version que este abierta (POST /api/apagar) con esta cabecera,
# y una Chuleta abierta solo acepta X-Chuleta-Token. No lo ve el usuario
TOKEN_HEADER = "X-Chuleta-Token"


def data_dir_default():
    """La carpeta de datos de la copia que lanzo este proceso: CHULETA_DATOS (server.py la deja en el entorno para sus
    procesos hijos, tambien con --data) o %LOCALAPPDATA%\\AudiosApp."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get("CHULETA_DATOS") or os.path.join(base, "AudiosApp")


def read_token(data_dir=None):
    """El token de la sesion que corre, o "" si no hay fichero (la app no esta abierta, o es una version anterior)."""
    try:
        with open(os.path.join(data_dir or data_dir_default(), TOKEN_FILE), encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return ""


def local_opener():
    """urllib SIN proxy, para hablar con la propia app en 127.0.0.1. urlopen toma el proxy del sistema (el del registro
    o HTTP_PROXY) y la excepcion <local> no cubre 127.0.0.1: con un proxy fijo de empresa, el acceso directo no
    encontraba la app abierta (y arrancaba otra copia) y el «Grabar» del aviso flotante fallaba."""
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ----------------------------------------------------------------------------- secretos (DPAPI)
# API key de la nube y voces.json: cifrados con la Data Protection API de Windows (ambito usuario: solo esta cuenta
# de Windows en este PC los descifra). Formato en disco: "dpapi:<base64>". Fuera de Windows no hay DPAPI: se guarda
# "plain:<base64>" y se avisa en el log (solo desarrollo).
_PLAIN_WARNED = []


def _blob():
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.c_void_p)]
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    for fn in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
        fn.restype = wt.BOOL
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wt.LPCWSTR, ctypes.POINTER(DATA_BLOB),
                                         ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
                                           ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return ctypes, DATA_BLOB, crypt32, kernel32


def _dpapi(data, encrypt):
    ctypes, DATA_BLOB, crypt32, kernel32 = _blob()
    buf = ctypes.create_string_buffer(data, len(data))
    inp = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))
    out = DATA_BLOB()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN: nunca un dialogo (es un servidor sin ventana); ambito usuario
    if encrypt:
        ok = crypt32.CryptProtectData(ctypes.byref(inp), APP_NAME, None, None, None, flags, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, flags, ctypes.byref(out))
    if not ok:
        fn = "CryptProtectData" if encrypt else "CryptUnprotectData"
        raise OSError(f"DPAPI: {fn} fallo ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def is_protected(text):
    return isinstance(text, str) and text.startswith(("dpapi:", "plain:"))


def protect(data):
    """bytes -> "dpapi:<base64>" (Windows, ambito usuario) o "plain:<base64>" (otros sistemas, con aviso)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if WINDOWS:
        try:
            return "dpapi:" + base64.b64encode(_dpapi(data, True)).decode("ascii")
        except (OSError, AttributeError) as e:  # DPAPI no disponible: mejor guardar que dejar la app sin ajustes
            if not _PLAIN_WARNED:
                _PLAIN_WARNED.append(True)
                log(f"   DPAPI no disponible ({e}): los secretos se guardan en claro (plain:base64)")
            return "plain:" + base64.b64encode(data).decode("ascii")
    if not _PLAIN_WARNED:
        _PLAIN_WARNED.append(True)
        log("   sin DPAPI fuera de Windows: los secretos se guardan en claro (plain:base64)")
    return "plain:" + base64.b64encode(data).decode("ascii")


def unprotect(text):
    """Inverso de protect. ValueError si el formato no es el nuestro; OSError si Windows no puede descifrarlo
    (otro usuario, otro PC o fichero danado)."""
    if not is_protected(text):
        raise ValueError("no es un secreto protegido")
    kind, _, b64 = text.partition(":")
    raw = base64.b64decode(b64.strip())
    if kind == "dpapi":
        if not WINDOWS:
            raise OSError("este secreto se cifro con DPAPI en Windows y aqui no se puede descifrar")
        return _dpapi(raw, False)
    return raw
