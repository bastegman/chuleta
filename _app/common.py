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


def setup_logging(data_dir):
    """La consola de siempre y, ademas, %LOCALAPPDATA%\\AudiosApp\\server.log con rotacion (3 x 1 MB)."""
    import logging.handlers
    for h in list(LOGGER.handlers):
        LOGGER.removeHandler(h)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    console = logging.StreamHandler(sys.stdout)
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
        traceback.print_exc()


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
        ok = crypt32.CryptProtectData(ctypes.byref(inp), "Notas", None, None, None, flags, ctypes.byref(out))
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
        return "dpapi:" + base64.b64encode(_dpapi(data, True)).decode("ascii")
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
