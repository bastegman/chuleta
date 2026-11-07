# -*- coding: utf-8 -*-
"""
Chuleta: app local para ordenar, escuchar, leer y transcribir las grabaciones de reuniones.

  - Servidor HTTP de la libreria estandar en http://127.0.0.1:8765 (solo accesible desde este PC)
  - Interfaz en _app/static (HTML + JS sin compilar)
  - Transcripcion local (faster-whisper) o en la nube (engines.py)
  - Las transcripciones se guardan como siempre: <audio>.txt y <audio>.srt junto al audio

Arranque: acceso directo del escritorio (pythonw.exe server.py, sin consola; _app/Chuleta.bat lo arranca con consola
para ver los mensajes). La interfaz se abre en una ventana propia (window.py, WebView2) o, si no se puede, en Edge.
"""
import atexit
import copy
import errno
import hashlib
import hmac
import json
import logging
import os
import platform
import queue
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import calendario
import engines
import explicar
import live
import probar
import salidas
import voices
from common import (APP_ID, APP_NAME, ApiError, BAD_NAME, LOGGER, SRT_TIME, WINDOWS,  # noqa: F401 (re-exportados)
                    alert, clean_name, default_threads, fmt_srt, fold, is_protected, log, log_exc, natural_key,
                    parse_srt, protect, read_text, save_text, setup_logging, unprotect, wav_duration, write_bytes)
from common import TOKEN_FILE, TOKEN_HEADER, local_opener, read_token  # sesion local (P1-02) y loopback sin proxy
from recording import GlobalHotkey, RecorderProcess, cue, mic_meeting_app, notify
from bandeja import Bandeja
from window import Window, set_app_id

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
ROOT = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AudiosApp")
HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}/"
SERVER, WINDOW = None, None  # los pone main(): el servidor HTTP y la ventana propia (None si se abre en Edge)

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".aac", ".wma", ".opus", ".mkv", ".mov", ".webm"}
TEXT_EXT = {".txt", ".md", ".srt"}
# lo que acompana a cada audio (se mueve y borra con el): texto, subtitulos, notas, resumen, hablantes y su exportacion
SIDE_EXTS = (".txt", ".srt", ".md", ".resumen.md", voices.SIDECAR_EXT, ".hablantes.txt")
MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4",
        ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aac": "audio/aac",
        ".webm": "audio/webm", ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".wma": "audio/x-ms-wma",
        ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
# Atributos de OneDrive "solo en linea": leer el archivo dispararia la descarga
CLOUD_ONLY = 0x400000 | 0x1000
LIVE_PRESETS = tuple(p["id"] for p in engines.PRESETS)  # en directo: por lotes y beam 1, cambia el modelo
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _-](\d{2})-(\d{2})-(\d{2})")


VERSION_FILE = os.path.join(APP_DIR, "version.txt")  # lo escribe scripts/build_zip.py: «AAAA-MM-DD · sha corto»
REPO_URL = "https://github.com/bastegman/chuleta"
# Acerca de > «invitar al autor a un café»: la pagina de donaciones de PayPal del autor (abre el navegador del sistema)
DONAR_URL = "https://www.paypal.com/donate?business=bdo.st.f%40gmail.com&currency_code=EUR"
PKG_ROOT = APP_NAME  # carpeta raiz del zip instalable (Chuleta/INSTALAR.bat, Chuleta/_app/...)


def code_mtime():
    """Fecha del codigo: cambia si se actualiza la app (la ventana abierta lo ve y se recarga sola)."""
    files = [os.path.join(APP_DIR, n) for n in os.listdir(APP_DIR) if n.endswith(".py")]
    files += [os.path.join(STATIC_DIR, n) for n in os.listdir(STATIC_DIR)]
    return max(os.path.getmtime(f) for f in files)


def version_file():
    """Primera linea de _app/version.txt («2026-10-31 · abc1234», la escribe scripts/build_zip.py y viaja en el zip),
    o None si esta copia no la tiene (una copia de trabajo del repositorio)."""
    try:
        v = read_text(VERSION_FILE).strip().splitlines()[0].strip()
        return v or None
    except (OSError, IndexError):
        return None


def read_version():
    """La version que se ensena en Ajustes > Acerca de: la del zip si existe; si no, fecha y hora del codigo."""
    return version_file() or time.strftime("%Y-%m-%d %H:%M", time.localtime(code_mtime()))


BUILD = format(int(code_mtime()), "x")  # recarga automatica de la ventana al actualizar: sigue basada en el mtime
VERSION = read_version()


# ----------------------------------------------------------------------------- utilidades

# Carpetas vinculadas: cualquier carpeta del PC (p. ej. la del proyecto en OneDrive) que se usa como
# si estuviera dentro de la carpeta de la app. Sus rutas internas empiezan por ":<id>" (":" no puede ir en un nombre
# de archivo de Windows, asi que nunca choca con una carpeta de verdad).
LINK = ":"
LINKS = {}  # id -> {"name", "path"} (se cargan de los ajustes al arrancar)
# Reuniones movidas en esta sesion (ruta interna vieja -> nueva): una peticion que llegue con la vieja (la pagina que
# la tenia abierta, unas notas que se guardan justo al moverla) va a la nueva (alias_of, en Handler)
ALIASES, ALIASES_LOCK = {}, threading.Lock()


def alias_of(rel):
    """La ruta nueva de una reunion movida en esta sesion si `rel` ya no existe; si no, `rel` tal cual."""
    if not isinstance(rel, str) or not rel:
        return rel
    with ALIASES_LOCK:
        new = ALIASES.get(rel.replace("\\", "/").strip("/"))
    if not new:
        return rel
    try:
        if os.path.exists(safe(rel)):  # en la ruta vieja vuelve a haber algo: manda lo que hay
            return rel
    except ApiError:
        pass
    return new


def link_id(path):
    return hashlib.md5(os.path.normcase(os.path.normpath(path)).encode("utf-8")).hexdigest()[:6]


def _inside(p, base):
    p, base = os.path.normcase(os.path.normpath(p)), os.path.normcase(os.path.normpath(base))
    return p == base or p.startswith(base.rstrip(os.sep) + os.sep)


def rel_of(abs_path):
    for lid, l in LINKS.items():
        if _inside(abs_path, l["path"]):
            rel = os.path.relpath(abs_path, l["path"])
            return LINK + lid + ("" if rel == "." else "/" + rel.replace(os.sep, "/"))
    rel = os.path.relpath(abs_path, ROOT)
    return "" if rel == "." else rel.replace(os.sep, "/")


def is_link_root(rel):
    return (rel or "").startswith(LINK) and "/" not in rel.strip("/")


def safe(rel, must=None):
    """Ruta absoluta dentro de la carpeta de la app o de una carpeta vinculada (no deja salir de ahi)."""
    rel = (rel or "").replace("\\", "/").strip("/")
    base = ROOT
    if rel.startswith(LINK):
        lid, _, rel = rel[len(LINK):].partition("/")
        if lid not in LINKS:
            raise ApiError(f"Esa carpeta ya no está vinculada a {APP_NAME}", 404)
        base = LINKS[lid]["path"]
    p = os.path.normpath(os.path.join(base, rel))
    if not _inside(p, base):
        raise ApiError(f"Ruta fuera de las carpetas de {APP_NAME}", 403)
    if must == "file" and not os.path.isfile(p):
        raise ApiError("No existe el archivo: " + rel, 404)
    if must == "dir" and not os.path.isdir(p):
        raise ApiError("No existe la carpeta: " + rel, 404)
    return p


def in_app_dir(p):
    a = os.path.normcase(APP_DIR)
    return os.path.normcase(p) == a or os.path.normcase(p).startswith(a + os.sep)


def in_app_tree(p):
    """p esta en una carpeta de la app: la de su codigo, su raiz o una carpeta vinculada (donde viven las grabaciones).
    Ahi no se escriben documentos de los destinos de salida."""
    return in_app_dir(p) or _inside(p, ROOT) or any(_inside(p, l["path"]) for l in LINKS.values())


# ----------------------------------------------------------------------------- paquete para otra persona

EXPORT_DIR = os.path.join(APP_DIR, "export")  # INSTALAR.bat, ABRIR CHULETA.bat y LEEME.txt (van a la raiz)


def export_code():
    """Todo el codigo de _app (.py, .bat y .ps1; sin subcarpetas ni python.txt): un modulo nuevo entra solo."""
    return sorted(n for n in os.listdir(APP_DIR)
                  if n.endswith((".py", ".bat", ".ps1")) and os.path.isfile(os.path.join(APP_DIR, n)))


def desktop_dir():
    if WINDOWS:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and os.path.isdir(buf.value):
            return buf.value  # CSIDL_DESKTOPDIRECTORY (puede estar dentro de OneDrive)
    return os.path.join(os.path.expanduser("~"), "Desktop")


def package_version():
    """La version que lleva un zip generado desde esta copia: la de su _app/version.txt si lo tiene (una copia
    instalada que vuelve a exportar conserva la suya); si no, la fecha de hoy. scripts/build_zip.py la calcula del
    commit y la pasa explicitamente."""
    return version_file() or time.strftime("%Y-%m-%d")


def build_package(dest, extra=(), version=None):
    """Zip con la app lista para instalar en otro PC. Lleva solo el codigo, el instalador, el LEEME, la licencia y
    _app/version.txt: ni grabaciones, ni notas, ni ajustes, ni la API key (eso vive en DATA_DIR, que no se toca).
    extra: [(archivo, ruta en el zip)] opcional, p. ej. las listas de vocabulario por workstream.
    version: la cadena de _app/version.txt («AAAA-MM-DD · sha»), que sustituye tambien al {FECHA} del LEEME."""
    import zipfile
    version = (version or "").strip() or package_version()
    root = PKG_ROOT + "/"

    def text(path, keep_marker=False):  # .bat/.ps1 con CRLF; BOM en .ps1 (PowerShell 5 lo leeria como ANSI) y .txt
        t = read_text(path).replace("\r\n", "\n").replace("\n", "\r\n")
        if not keep_marker:
            t = t.replace("{FECHA}", version)
        return (("\ufeff" if path.endswith((".ps1", ".txt")) else "") + t).encode("utf-8")

    files = [(os.path.join(APP_DIR, n), root + "_app/" + n) for n in export_code()]
    files += [(e.path, root + "_app/static/" + e.name) for e in os.scandir(STATIC_DIR) if e.is_file()]
    templates = [e for e in os.scandir(EXPORT_DIR) if e.is_file()]
    files += [(e.path, root + e.name) for e in templates]
    # las plantillas van tambien dentro de _app/export (con su {FECHA}): la copia instalada podra volver a exportar
    files += [(e.path, root + "_app/export/" + e.name) for e in templates]
    lic = os.path.join(os.path.dirname(APP_DIR), "LICENSE")  # MIT: el aviso acompana a cada copia
    if os.path.isfile(lic):
        files.append((lic, root + "LICENSE"))
    files += list(extra)
    tmp = dest + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in sorted(files, key=lambda f: f[1]):
            if src.endswith((".bat", ".ps1", ".txt")):
                z.writestr(arc, text(src, keep_marker=arc.startswith(root + "_app/export/")))
            else:
                z.write(src, arc)
        z.writestr(root + "_app/version.txt", (version + "\r\n").encode("utf-8"))  # server.py lo ensena en Ajustes
    os.replace(tmp, dest)
    return len(files) + 1


# ----------------------------------------------------------------------------- estado persistente

class Store:
    """JSON en %LOCALAPPDATA%\\AudiosApp (fuera de OneDrive: la API key no se sincroniza).
    Los SECRETS se guardan cifrados con DPAPI (common.protect); en memoria y en get() van en claro. Un fichero
    antiguo con la clave en claro se lee igual y queda cifrado en el primer guardado (migracion transparente).
    Si no se puede descifrar (otro usuario de Windows, otro PC), la clave queda vacia y se avisa en el log.

    Un settings.json ilegible (JSON mal formado o cortado, o que no es un objeto) no se ignora en silencio, porque el
    primer guardado lo pisaria con los valores por defecto (API key, carpetas vinculadas y destinos vacios): se copia
    tal cual como settings.json.roto-<fecha>, se rescata de el lo que mas cuesta volver a poner (todos los SECRETS y
    RESCUE), se anota en el log y `broken` se lo cuenta a la interfaz. Si ni siquiera se puede hacer la copia, en toda
    la sesion no se escribe encima del original (`frozen`): los cambios se quedan en memoria."""

    # un texto o una lista de textos; las claves adicionales van por proveedor (engines.EXTRA_KEYS_FIELDS)
    SECRETS = (("cloud", "api_key"), ("cloud", "extra_keys"), ("cloud", "extra_keys_openai"),
               ("cloud", "extra_keys_azure"), ("cloud", "extra_keys_custom"))

    def __init__(self, path, defaults):
        self.path, self.lock = path, threading.Lock()
        self.data = copy.deepcopy(defaults)
        self.broken, self.frozen = None, False  # settings.json ilegible: {"copia", "error", "recuperado"}
        for k, v in self._load().items():
            if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                self.data[k].update(v)
            else:
                self.data[k] = v
        self.lost = []  # secretos que no se pudieron descifrar (la interfaz pide pegarlos otra vez)
        for keys in self.SECRETS:
            d = self.data
            for k in keys[:-1]:
                d = d.get(k) if isinstance(d, dict) else None
            if not isinstance(d, dict):
                continue
            v = d.get(keys[-1])
            items = v if isinstance(v, list) else [v]
            out = []
            for item in items:
                if not is_protected(item):
                    out.append(item)
                    continue
                try:
                    out.append(unprotect(item).decode("utf-8"))
                except (OSError, ValueError) as e:
                    out.append("")
                    if ".".join(keys) not in self.lost:
                        self.lost.append(".".join(keys))
                    log(f"   no se pudo descifrar {'.'.join(keys)} de settings.json ({e}): vuelve a pegarla en Ajustes")
            d[keys[-1]] = [x for x in out if x] if isinstance(v, list) else out[0]

    def _load(self):
        """Lo guardado ({} si no hay fichero). Con BOM tambien (el Bloc de notas lo pone). Si el fichero existe pero no
        se puede leer o no es un objeto JSON, lo que _broken haya podido rescatar."""
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return {}
        except OSError as e:  # existe pero no se deja leer: tampoco se pisa a ciegas
            return self._broken(None, e)
        try:
            saved = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(saved, dict):
                raise ValueError(f"no es un objeto JSON ({type(saved).__name__})")
            bad = [k for k, v in saved.items() if isinstance(self.data.get(k), dict) and not isinstance(v, dict)]
            if bad:  # p. ej. "cloud": null: con eso la app no arranca; lo demas si vale
                raise ValueError("secciones que no son un objeto: " + ", ".join(bad))
            return saved
        except ValueError as e:  # UnicodeDecodeError y JSONDecodeError incluidos
            return self._broken(raw, e)

    def _broken(self, raw, err):
        """settings.json ilegible: copia del fichero tal cual, rescate (los SECRETS y RESCUE), log y aviso para la
        interfaz."""
        why = f"{err.__class__.__name__}: {err}"[:200]
        copia = f"{self.path}.roto-{time.strftime('%Y-%m-%d_%H-%M-%S')}"
        n = 1
        while os.path.exists(copia):
            copia, n = f"{self.path}.roto-{time.strftime('%Y-%m-%d_%H-%M-%S')}-{n}", n + 1
        try:
            shutil.copy2(self.path, copia)
        except OSError as e:
            log(f"   no se pudo copiar settings.json ({e.__class__.__name__}): no se escribirá encima en esta sesión")
            copia, self.frozen = None, True
        saved = self._rescue(raw) if raw else {}

        def dig(keys):
            d = saved
            for k in keys:
                d = d.get(k) if isinstance(d, dict) else None
            return d

        def cuantos(n, uno, varios):
            return [f"{n} {uno if n == 1 else varios}"] if n else []
        extra = sum(len(dig(k) or []) for k in self.SECRETS if k != ("cloud", "api_key"))  # de todos los proveedores
        recuperado = ((["la API key"] if dig(("cloud", "api_key")) else [])
                      + cuantos(extra, "clave adicional", "claves adicionales")
                      + cuantos(len(dig(("links",)) or []), "carpeta vinculada", "carpetas vinculadas")
                      + cuantos(len(dig(("perfil", "outputs")) or []), "destino de salida", "destinos de salida"))
        self.broken = {"copia": os.path.basename(copia) if copia else None, "error": why, "recuperado": recuperado}
        log(f"   settings.json no se puede leer ({why})"
            + (f": copia en {os.path.basename(copia)}" if copia else "")
            + f"; recuperado: {', '.join(recuperado) or 'nada'}; lo demás vuelve a los valores por defecto")
        return saved

    RESCUE = (("links",), ("perfil", "outputs"))  # de un settings.json roto, ademas de todos los SECRETS

    @classmethod
    def _rescue(cls, raw):
        """De un settings.json roto, los SECRETS (todos: tambien los que se anadan) y lo de RESCUE que se pueda leer
        entero: cada valor se decodifica con raw_decode desde su clave, asi que vale aunque el fichero este cortado o
        mal escrito en otra parte."""
        text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
        dec, out = json.JSONDecoder(), {}
        for keys in cls.SECRETS + cls.RESCUE:
            for m in re.finditer('"' + re.escape(keys[-1]) + r'"\s*:\s*', text):
                try:
                    v, _ = dec.raw_decode(text, m.end())
                except ValueError:
                    continue
                if keys == ("cloud", "api_key"):
                    ok = isinstance(v, str) and bool(v.strip())
                elif keys in cls.SECRETS:  # las claves adicionales (las de cada proveedor): listas de textos
                    ok = isinstance(v, list) and bool(v) and all(isinstance(x, str) for x in v)
                else:  # links y outputs: listas de {"path": …}
                    ok = isinstance(v, list) and bool(v) and all(isinstance(x, dict) and isinstance(x.get("path"), str)
                                                                 for x in v)
                if ok:
                    d = out
                    for k in keys[:-1]:
                        d = d.setdefault(k, {})
                    d[keys[-1]] = v
                    break
        return out

    def get(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def update(self, fn):
        with self.lock:
            fn(self.data)
            out = copy.deepcopy(self.data)
            for keys in self.SECRETS:
                d = out
                for k in keys[:-1]:
                    d = d.get(k) if isinstance(d, dict) else None
                if isinstance(d, dict) and isinstance(d.get(keys[-1]), str) and d[keys[-1]]:
                    d[keys[-1]] = protect(d[keys[-1]])
                elif isinstance(d, dict) and isinstance(d.get(keys[-1]), list):
                    d[keys[-1]] = [protect(x) for x in d[keys[-1]] if isinstance(x, str) and x]
            if self.frozen:  # settings.json ilegible y sin copia: escribir encima perderia lo que tenga
                return
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())  # en disco antes de sustituir: un corte de luz no deja un settings.json vacio
            os.replace(tmp, self.path)


DEFAULT_SETTINGS = {
    "preset": "rapido",
    "language": "",  # "" = detectar; "es-en" = espanol + ingles (spanglish): idioma por bloque, solo entre esos dos
    "threads": default_threads(),
    "prompt": "",
    "live_auto": True,       # transcribir solo en cuanto OBS empieza a grabar
    "live_preset": "medium",  # "medium" (preciso), "rapido" (small) o "nube"
    "live_final_pass": True,  # directo en la nube: al terminar, repasar la grabacion entera (mejor texto)
    # grabadora propia: "default" sigue al micro de Windows (cascos incluidos); "all" graba todas las
    # salidas a la vez, asi da igual por donde saque Teams el audio. "none" = sin esa fuente
    "rec_mic": "default",
    "rec_sys": "all",
    "rec_vocab": "",          # ultimo workstream elegido al grabar (<proyecto>/Vocabulario/<workstream>.txt)
    "links": [],              # carpetas vinculadas: [{"name", "path"}] (fuera de Audios, p. ej. la del proyecto)
    "rec_folder": "",         # ultima carpeta donde se grabo (para las reuniones sin tema)
    # carpeta principal asignada (rec_folder_origin): proyecto -> carpeta y, para un workstream que quiera la suya, id
    # de su lista -> carpeta. Sin asignar, la del proyecto en el arbol (la que tiene su Vocabulario). Hasta el
    # 07/11/2026 project_folders era «la ultima elegida» para ese proyecto: lo que habia se queda como asignacion
    "project_folders": {},
    "workstream_folders": {},
    # una carpeta por reunion, «AAAA-MM-DD Nombre», dentro de la principal (msg 1158): el audio y sus textos van dentro
    "carpeta_por_reunion": True,
    "favorites": [],          # [{"kind": "a"|"f", "path": ...}] (estilo Notion)
    "folder_icons": {},       # carpeta -> emoji
    "meeting_detect": True,   # avisar si Teams/Zoom/… usa el micro y no se esta grabando
    "outlook_on": False,      # leer la cita en curso de Outlook clasico (COM, pywin32): nombre, tema y asistentes
    "rec_aviso": True,        # aviso flotante «Grabar / ✕» al detectar una llamada (calendario.py + aviso.py)
    "summary_auto": True,     # resumen con IA al terminar de transcribir una reunion reciente
    # ...tambien si se transcribio en local (consentimiento: el resumen manda el texto al proveedor de la nube). Sin
    # el, lo transcrito en local no sale del PC por el resumen automatico (Jobs.auto_summary)
    "summary_auto_local": False,
    # aviso unico al actualizar a la version que deja de subir lo local (App._aviso_resumen_local): None = sin decidir,
    # True = pendiente (la interfaz lo ensena en Inicio), False = visto o no aplica
    "aviso_resumen_local": None,
    "summary_model": "openai/gpt-oss-120b",
    "summary_lang": "es",     # es / en / same (el de la reunion)
    "summary_extra": "",     # instrucciones extra para el resumen
    # donde se procesan las reuniones: "local" (nada sale del PC), "agente" (resumen con la cuenta del usuario a
    # traves de Claude Code) o "api" (nube: transcripcion y resumen con API key). None = sin decidir: al arrancar
    # pasa a "api" si hay API key (instalaciones anteriores) y si no a "local" (App.__init__, procesado_de)
    "procesado": None,
    # el agente del PC: {"tipo": "claude", "modelo": "sonnet"} en cuanto se guarda desde Ajustes; None por defecto (asi
    # un "agente": null escrito a mano no da el fichero por roto: agente_de lo resuelve)
    "agente": None,
    "voices_enabled": False,  # quien habla (diarizacion local + huellas de voz): opcional, apagado por defecto
    # perfil de reunion por defecto (Ajustes > Perfil de reunion y «Guardar como perfil por defecto» al grabar).
    # None en folder/vocab = automatico, como siempre: la ultima carpeta usada (o la del proyecto del tema) y el tema
    # que sugiera la reunion en curso o el ultimo elegido. El idioma por defecto sigue siendo "language".
    "perfil": {"folder": None,    # carpeta de destino fija (ruta interna, "" = la raiz) o None
               "vocab": None,     # tema fijo (id <proyecto>/Vocabulario/<tema>.txt, "" = sin vocabulario) o None
               "context": "",     # contexto libre: de que van las reuniones y quien participa (Whisper y resumen)
               # destinos de salida: cada reunion como documento en estas carpetas (salidas.py), al terminar la
               # transcripcion y el resumen y al guardar cambios: [{"path", "format": md|txt|docx, "by_project"}]
               "outputs": []},
    # Whisper en local: minutos que el modelo sigue cargado tras la ultima reunion (0 = 5 min, como antes) y si se
    # precarga al arrancar el que usara el directo (solo si ya esta descargado; medido: 2-6 s de arranque en frio)
    "worker_keepalive_min": 30,
    "worker_preload": True,
    # nube: whisper-large-v3 (el mas preciso; -turbo es mas rapido y algo menos fino)
    "cloud": {"provider": "groq", "api_key": "", "model": "whisper-large-v3", "base_url": "",
              "azure_endpoint": "", "azure_deployment": "", "azure_api_version": "2024-06-01",
              "chunk_minutes": 10, "parallel": 3,
              # directo en la nube: modelo rapido por trozo y claves de otras cuentas de Groq (rotacion de cupo)
              "live_model": engines.LIVE_MODEL_DEFAULT, "extra_keys": [],
              # «Otra (compatible)»: modelo del directo propio ("" = el de la cola) y sus claves adicionales; las
              # claves adicionales de cada proveedor solo se usan con el (engines.EXTRA_KEYS_FIELDS)
              "live_model_custom": "", "extra_keys_openai": [], "extra_keys_azure": [], "extra_keys_custom": []},
    "speed": {},
}

PROCESADOS = ("local", "agente", "api")
PRUEBA_VOZ_DB = -35.0  # «Probar»: pico del micro (dB, cada 0,25 s) que cuenta como voz; supuesto, sin medir
NUBE_OFF = ("La transcripción en la nube está desactivada: en Ajustes → «Dónde se procesan tus reuniones» elige "
            "«API externa».")


def procesado_de(st):
    """Donde se procesan las reuniones. Sin decidir (ajustes de una version anterior): "api" si hay API key, si no
    "local"."""
    p = st.get("procesado")
    if p in PROCESADOS:
        return p
    return "api" if ((st.get("cloud") or {}).get("api_key") or "").strip() else "local"


def agente_de(st):
    """(clase del agente, {tipo, modelo}): el configurado, o Claude Code si no se reconoce."""
    a = st.get("agente") if isinstance(st.get("agente"), dict) else {}
    cls = engines.AGENTES.get(a.get("tipo")) or engines.AGENTES["claude"]
    modelo = a.get("modelo") if a.get("modelo") in dict(cls.MODELOS) else cls.MODELO
    return cls, {"tipo": cls.tipo, "modelo": modelo}


class Durations:
    def __init__(self, path):
        self.path, self.lock, self.dirty = path, threading.Lock(), False
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.cache = json.load(f)
        except (OSError, ValueError):
            self.cache = {}

    def has(self, rel, size, mtime):
        with self.lock:
            return f"{rel}|{size}|{int(mtime)}" in self.cache

    def get(self, abs_path, rel, size, mtime, cloud, srt_abs=None, compute=True):
        key = f"{rel}|{size}|{int(mtime)}"
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        if not compute:
            return None
        d = None
        if cloud:
            # no tocamos el audio (OneDrive lo descargaria); usamos el final del .srt si lo hay
            if srt_abs:
                try:
                    segs = parse_srt(read_text(srt_abs))
                    d = segs[-1]["end"] if segs else None
                except OSError:
                    d = None
        elif abs_path.lower().endswith(".wav"):
            d = wav_duration(abs_path, size)
        else:
            d = engines.media_duration(abs_path)
        if d:
            d = round(d, 1)
            with self.lock:
                self.cache[key] = d
                self.dirty = True
        return d

    def flush(self):
        with self.lock:
            if not self.dirty:
                return
            data, self.dirty = dict(self.cache), False
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.path)


# ----------------------------------------------------------------------------- arbol de carpetas

def scan(dirs=None):
    """Arbol de carpetas con audios (+ estado de transcripcion) y documentos sueltos.
    dirs: si se pasa un dict, se rellena con carpeta -> mtime_ns (Library lo usa para saber si hay que volver a
    escanear: la fecha de una carpeta cambia con cada alta, baja o renombrado dentro de ella)."""
    flat = []
    mias = carpetas_reunion().keys()  # las carpetas de reunion que creo la app (rutas internas en normcase)

    def walk(abs_dir, rel, depth=0, max_depth=99):
        node = {"name": os.path.basename(abs_dir) if rel else APP_NAME, "path": rel,
                "folders": [], "audios": [], "docs": []}
        try:
            if dirs is not None:
                dirs[abs_dir] = os.stat(abs_dir).st_mtime_ns
            entries = list(os.scandir(abs_dir))
        except OSError:
            return node
        files = {}
        for e in entries:
            if e.name.startswith((".", "~$")):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    # las carpetas Vocabulario no son reuniones ni documentos: las lee list_vocab. La de una reunion
                    # se mira aunque este un nivel por debajo del tope (la principal puede estar en el ultimo)
                    sub = f"{rel}/{e.name}" if rel else e.name
                    if not in_app_dir(e.path) and fold(e.name) != VOCAB_FOLDER and (
                            depth < max_depth or os.path.normcase(sub) in mias):
                        node["folders"].append(walk(e.path, sub, depth + 1, max_depth))
                elif e.is_file():
                    files[e.name.lower()] = e
            except OSError:
                continue
        audio_stems = {os.path.splitext(k)[0] for k in files if os.path.splitext(k)[1] in AUDIO_EXT}
        for low, e in files.items():
            stem_low, ext = os.path.splitext(low)
            frel = f"{rel}/{e.name}" if rel else e.name
            try:
                st = e.stat()
            except OSError:
                continue
            cloud = bool(getattr(st, "st_file_attributes", 0) & CLOUD_ONLY)
            if ext in AUDIO_EXT:
                stem = os.path.splitext(e.name)[0]
                txt, srt = files.get(stem_low + ".txt"), files.get(stem_low + ".srt")
                m = DATE_RE.search(stem)
                label = ""
                if m:
                    label = (stem[:m.start()] + " " + stem[m.end():]).strip(" -_·")
                    label = re.sub(r"^(AUDIO|VID|REC)[-_ ]*", "", label, flags=re.I)
                    label = re.sub(r"^_?\d{2,6}$", "", label)
                tx_mtime = 0
                for t in (txt, srt):
                    try:
                        tx_mtime = max(tx_mtime, t.stat().st_mtime) if t else tx_mtime
                    except OSError:
                        pass
                summ = files.get(stem_low + ".resumen.md")  # resumen con IA
                md = files.get(stem_low + ".md")  # notas y propiedades de la reunion
                spk = files.get(stem_low + voices.SIDECAR_EXT)  # quien habla
                try:
                    md_mtime = md.stat().st_mtime if md else 0
                    spk_mtime = spk.stat().st_mtime if spk else 0
                except OSError:
                    md_mtime, spk_mtime = 0, 0
                a = {"name": e.name, "path": frel, "stem": stem, "ext": ext, "size": st.st_size,
                     "mtime": st.st_mtime, "cloud": cloud, "txt": bool(txt), "srt": bool(srt), "tx_mtime": tx_mtime,
                     "date": "{}-{}-{}T{}:{}:{}".format(*m.groups()) if m else None,
                     "label": label, "folder": rel, "notes_mtime": md_mtime, "has_summary": bool(summ),
                     "spk_mtime": spk_mtime,
                     "_abs": e.path, "_srt": srt.path if srt else None, "_md": md.path if md else None,
                     "_sum": summ.path if summ else None, "_spk": spk.path if spk else None}
                node["audios"].append(a)
                flat.append(a)
            elif ext in TEXT_EXT and stem_low not in audio_stems and not any(
                    low.endswith(x) and low[:-len(x)] in audio_stems for x in (".resumen.md", ".hablantes.txt")):
                node["docs"].append({"name": e.name, "path": frel, "ext": ext, "size": st.st_size,
                                     "mtime": st.st_mtime, "cloud": cloud})
        node["folders"].sort(key=lambda n: natural_key(n["name"]))
        node["audios"].sort(key=lambda a: (a["date"] or "", natural_key(a["name"])))
        node["docs"].sort(key=lambda d: natural_key(d["name"]))
        # la carpeta de una reunion (la creo la app) con solo ella dentro: la interfaz la pinta como la reunion
        if rel and os.path.normcase(rel) in mias and not is_link_root(rel) and not node["folders"] \
                and not node["docs"] and len(node["audios"]) == 1:
            node["por_reunion"] = True
        return node

    tree = walk(ROOT, "")
    linked = []
    for lid, l in LINKS.items():  # arriba del todo, antes que las carpetas de dentro de Audios
        node = walk(l["path"], LINK + lid, max_depth=LINK_DEPTH)
        node.update(name=l["name"], linked=l["path"], missing=not os.path.isdir(l["path"]))
        linked.append(node)
    tree["folders"][:0] = sorted(linked, key=lambda n: natural_key(n["name"]))
    return tree, flat


LINK_DEPTH = 4  # en una carpeta de proyecto grande no se baja mas (el arbol se repasa cada pocos segundos)


class Library:
    """El arbol de reuniones, con cache. Un sondeo (cada 6 s desde la pagina) no vuelve a recorrer el disco si ninguna
    carpeta ha cambiado de fecha (un stat por carpeta) y ninguna grabacion abierta ha crecido; `at = 0` (lo ponen
    los endpoints que escriben) fuerza el escaneo completo. Cada FULL_RESCAN_S se repasa todo de todas formas, en
    segundo plano, por si un .md o .srt se edito desde fuera (eso no toca la fecha de la carpeta)."""

    FULL_RESCAN_S = 30
    RECENT_S = 2.0  # una carpeta modificada hace menos de esto se da por cambiada (discos con fechas de 1-2 s)

    def __init__(self, durations):
        self.durations = durations
        self.lock = threading.Lock()
        self.tree, self.flat, self.version, self.at = None, [], "", 0
        self.public = None  # el arbol sin campos privados, listo para /api/tree (se calcula una vez por escaneo)
        self.pending_durations = False
        self.growth = {}  # ruta -> (tamano, cuando cambio): OBS no actualiza la fecha mientras graba
        self.ends = {}    # .srt -> (fecha, segundo en que acaba): para detectar transcripciones a medias
        self.notes = {}   # .md -> (fecha, propiedades, tiene texto)
        self.speakers = {}  # .hablantes.json -> (fecha, resumen {n, known, doubt, tu} o None si solo lleva el micro)
        self.dirs = {}    # carpeta -> mtime_ns en el ultimo escaneo
        self.watch = {}   # grabaciones abiertas (cambian sin tocar su carpeta): ruta -> (tamano, fecha)
        self.full_at, self.full_wall, self.clock = 0.0, 0.0, time.time  # clock: los tests ponen un reloj falso
        self.rescan_thread = None
        self.scans = 0    # escaneos completos (para los tests y el banco)

    def _props(self, a):
        p = a["_md"]
        if not p:
            return {}, False
        hit = self.notes.get(p)
        if hit and hit[0] == a["notes_mtime"]:
            return hit[1], hit[2]
        try:
            props, body = parse_notes(read_text(p))
        except OSError:
            props, body = {}, ""
        self.notes[p] = (a["notes_mtime"], props, bool(body.strip()))
        return props, bool(body.strip())

    def _speakers(self, a):
        """Resumen del sidecar de voces (con cache): None si no hay diarizacion (p. ej. solo la serie del micro)."""
        p = a.get("_spk")
        if not p:
            return None
        hit = self.speakers.get(p)
        if hit and hit[0] == a["spk_mtime"]:
            return hit[1]
        info = None
        try:
            d = json.loads(read_text(p))
            hs = d.get("hablantes") or {}
            if d.get("tramos") and hs:
                real = [i for i in hs.values() if not i.get("breve")]
                info = {"n": len(real), "known": sum(1 for i in real if i.get("persona") and not i.get("provisional")),
                        "doubt": sum(1 for i in real if i.get("persona") and i.get("provisional")),
                        "tu": bool(d.get("tu"))}
        except (OSError, ValueError, AttributeError):
            info = None
        self.speakers[p] = (a["spk_mtime"], info)
        return info

    def _tx_end(self, a):
        """Hasta que segundo llega la transcripcion (lee solo el final del .srt, con cache)."""
        p = a["_srt"]
        if not p:
            return None
        hit = self.ends.get(p)
        if hit and hit[0] == a["tx_mtime"]:
            return hit[1]
        end = None
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - 4096))
                times = SRT_TIME.findall(f.read().decode("utf-8", "ignore"))
            if times:
                g = times[-1]
                end = round(int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000, 1)
        except OSError:
            pass
        self.ends[p] = (a["tx_mtime"], end)
        return end

    def _recording(self, a):
        """True/duracion si es un WAV de OBS aun abierto (cabecera a 0xFFFFFFFF) y creciendo."""
        if a["ext"] != ".wav" or a["cloud"] or self.durations.has(a["path"], a["size"], a["mtime"]):
            return None
        info = live.wav_info(a["_abs"])
        if not info or info["data_size"] is not None:
            return None
        prev = self.growth.get(a["path"])
        if not prev or prev[0] != a["size"]:
            # la primera vez que se ve (p. ej. al arrancar), lo ultimo que crecio fue en su fecha de modificacion: un
            # WAV que OBS dejo abierto hace dias no es una grabacion en curso (02/11/2026: arrancaba un directo, y un
            # repaso final en la nube, con cada arranque)
            self.growth[a["path"]] = (a["size"], time.time() if prev else min(time.time(), a["mtime"]))
        a["recording"] = time.time() - self.growth[a["path"]][1] < live.STALLED_S
        return round(max(0, a["size"] - info["data_off"]) / info["byte_rate"], 1)

    def _changed(self):
        """True si algo ha podido cambiar en disco desde el ultimo escaneo completo: la fecha de alguna carpeta (alta,
        baja o renombrado dentro), o el tamano/fecha de una grabacion abierta. Un stat por carpeta: milisegundos."""
        for d, m in self.dirs.items():
            try:
                st = os.stat(d)
            except OSError:
                return True
            if st.st_mtime_ns != m or st.st_mtime > self.full_wall - self.RECENT_S:
                return True
        for p, (size, mtime) in self.watch.items():
            try:
                st = os.stat(p)
            except OSError:
                return True
            if st.st_size != size or st.st_mtime != mtime:
                return True
        return False

    def _rescan(self, now):
        """Escaneo completo (con self.lock cogido)."""
        dirs = {}
        tree, flat = scan(dirs)
        missing, watch = False, {}
        for a in flat:
            a["recording"] = False
            a["tx_end"] = self._tx_end(a)
            props, a["has_notes"] = self._props(a)
            a["props"] = with_theme_id(props, a["path"])  # con el id de su tema en este PC (la cache no lo lleva)
            a["speakers"] = self._speakers(a)
            a["has_speakers"] = bool(a["speakers"])
            open_dur = self._recording(a)
            if open_dur is not None:  # sin cachear: cambia cada pocos segundos
                a["duration"] = open_dur
                a["partial"] = False
                watch[a["_abs"]] = (a["size"], a["mtime"])
                continue
            # duraciones: las rapidas (cabecera WAV) ya; las demas (PyAV) en segundo plano
            quick = a["ext"] == ".wav" or a["cloud"]
            a["duration"] = self.durations.get(a["_abs"], a["path"], a["size"], a["mtime"], a["cloud"],
                                               a["_srt"], compute=quick)
            missing |= a["duration"] is None and not quick
            a["partial"] = is_partial(a)
        self.durations.flush()
        public = strip_private(tree)
        payload = json.dumps(public, ensure_ascii=False, sort_keys=True)
        self.tree, self.flat, self.public, self.at = tree, flat, public, now
        self.version = hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]
        self.dirs, self.watch, self.full_at, self.full_wall = dirs, watch, now, time.time()
        self.scans += 1
        if missing and not self.pending_durations:
            self.pending_durations = True
            threading.Thread(target=self._fill_durations, daemon=True).start()

    def _rescan_later(self):
        """Escaneo completo en segundo plano (el sondeo que lo pide se lleva el arbol que ya hay, sin esperar)."""
        if self.rescan_thread is not None and self.rescan_thread.is_alive():
            return

        def run():
            with self.lock:
                self._rescan(self.clock())
        self.rescan_thread = threading.Thread(target=run, daemon=True, name="library-rescan")
        self.rescan_thread.start()

    def _refresh_locked(self, max_age):
        now = self.clock()
        if self.tree is not None and now - self.at < max_age:
            return
        if self.tree is not None and self.at and not self._changed():
            self.at = now  # nada ha cambiado: el mismo arbol
            if now - self.full_at >= self.FULL_RESCAN_S:
                self._rescan_later()
            return
        self._rescan(now)

    def refresh(self, max_age=0.0):
        with self.lock:
            self._refresh_locked(max_age)
            return self.tree, self.version

    def snapshot(self, max_age=0.0):
        """(arbol publico, version) para /api/tree, coherentes entre si."""
        with self.lock:
            self._refresh_locked(max_age)
            return self.public, self.version

    def _fill_durations(self):
        try:
            for a in list(self.flat):
                if a.get("duration") is None and not a["cloud"]:
                    self.durations.get(a["_abs"], a["path"], a["size"], a["mtime"], False, a["_srt"])
            self.durations.flush()
        finally:
            self.pending_durations = False
        with self.lock:
            self.at = 0  # fuerza re-escaneo en la proxima peticion

    def find(self, rel):
        self.refresh(max_age=5)
        for a in self.flat:
            if a["path"] == rel:
                return a
        return None


CLOUD_TEXT_LOCK = threading.Lock()


class CloudText:
    """Reuniones cuyo texto actual se transcribio entero en la nube: en la cola con la nube, en directo con todos sus
    trozos en la nube o con el repaso final. El resumen automatico solo sube a la nube estas sin el permiso de Ajustes
    (summary_auto_local). Lo apunta cada transcripcion al escribir el texto (Jobs._cloud_text) y vive en DATA_DIR, asi
    que no depende del trabajo que acaba de terminar ni del arranque: un repaso final retomado tras reiniciar que falla
    o se cancela deja el texto del directo, y cuenta lo que fue el directo. Lo que no esta cuenta como local. Sigue a la
    reunion si se mueve o se renombra (ella o su carpeta) y se borra con ella (moved, set), y cada entrada lleva la
    identidad del audio (ident): si en esa ruta hay ahora otro archivo (se movio o se borro fuera de la app y otra
    reunion llego con ese nombre), no cuenta. Caduca a los 7 dias (el resumen automatico, para las de los ultimos 3)."""

    KEEP_S = 7 * 86400

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data = {str(k): self._entry(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, TypeError, AttributeError, IndexError):
            self.data = {}

    @staticmethod
    def _entry(v):
        """[cuando, identidad del audio]; un numero suelto, sin identidad (0)."""
        t, ino = (v[0], v[1]) if isinstance(v, list) else (v, 0)
        return [float(t), int(ino)]

    @staticmethod
    def ident(rel):
        """La identidad del audio: st_ino, el indice del archivo en NTFS, que se conserva al moverlo o renombrarlo en la
        misma unidad y es otro en un archivo nuevo; 0 si no se sabe. Una entrada apuntada sin identidad vale por la
        ruta; una con identidad, solo si el audio que hay ahora en esa ruta es el mismo (__contains__)."""
        try:
            return os.stat(safe(rel, "file")).st_ino
        except (ApiError, OSError):
            return 0

    def __contains__(self, rel):
        e = self.data.get(rel)
        return (e is not None and time.time() - e[0] < self.KEEP_S  # caduca al consultar, no solo al escribir
                and (not e[1] or e[1] == self.ident(rel)))  # y tiene que ser el mismo audio

    def set(self, rel, cloud):
        now = time.time()
        with self.lock:
            data = {k: e for k, e in self.data.items() if k != rel and now - e[0] < self.KEEP_S}
            if cloud:
                data[rel] = [now, self.ident(rel)]
            self._save(data)

    def moved(self, old, new, folder=False):
        """La reunion `old` (con folder, la carpeta y lo que tiene dentro) pasa a ser `new`: lo apuntado va con ella, y
        lo que hubiera en la ruta nueva (de una reunion que ya no esta ahi) se olvida."""
        now = time.time()

        def dest(k):
            return new + k[len(old):] if k == old or (folder and k.startswith(old + "/")) else None
        with self.lock:
            vivas = {k: e for k, e in self.data.items() if now - e[0] < self.KEEP_S}
            data = {k: e for k, e in vivas.items()
                    if dest(k) is None and k != new and not (folder and k.startswith(new + "/"))}
            data.update({dest(k): e for k, e in vivas.items() if dest(k) is not None})
            self._save(data)

    def _save(self, data):
        """Con self.lock: los datos nuevos, en memoria y (si han cambiado) en disco."""
        if data == self.data:
            return
        self.data = data
        try:
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(self.path + ".tmp", self.path)
        except OSError as e:  # vale en memoria; sin el fichero, al reiniciar todo cuenta como local
            log(f"   no se pudo guardar {os.path.basename(self.path)} ({e.__class__.__name__})")
            try:
                os.remove(self.path)
            except OSError:
                pass


class Complete:
    """Audios cuya transcripcion se sabe completa aunque el texto acabe antes que el audio
    (p. ej. se siguio grabando en silencio al final). Asi no se marcan "a medias"."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data = set(json.load(f))
        except (OSError, ValueError):
            self.data = set()

    def __contains__(self, rel):
        return rel in self.data

    def _save(self):
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(sorted(self.data), f, ensure_ascii=False)
        os.replace(self.path + ".tmp", self.path)

    def set(self, rel, done=True):
        with self.lock:
            if done == (rel in self.data):
                return
            (self.data.add if done else self.data.discard)(rel)
            self._save()

    def moved(self, old, new):
        if old in self:
            self.set(old, False)
            self.set(new, True)


COMPLETE = None  # se crea al arrancar (necesita DATA_DIR)


class Temas:
    """El tema de cada reunion por el id de su lista (ruta interna del audio -> id de list_vocab), en este PC: con dos
    proyectos que se llaman igual, los nombres de la cabecera del .md (proyecto y workstream, que leen todas las
    versiones) no dicen cual es. No va en la cabecera: las versiones anteriores solo aceptan ahi las claves que
    conocen y, con una nueva, toman la cabecera entera por notas. El id solo vale mientras su lista se llame como dice
    la cabecera (si no, otra version u otro PC cambio el tema y mandan los nombres: find_vocab)."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.data = {k: v for k, v in data.items() if isinstance(v, str) and v} if isinstance(data, dict) else {}

    def get(self, rel):
        return self.data.get(rel)

    def _save(self):
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, self.path)

    def set(self, rel, vid):
        with self.lock:
            if self.data.get(rel) == (vid or None):
                return
            if vid:
                self.data[rel] = vid
            else:
                self.data.pop(rel, None)
            self._save()

    def moved(self, old, new, folder=False):
        """Una reunion (o, con folder, una carpeta) cambia de ruta: su tema la sigue; con folder, tambien el id de las
        listas que estan dentro."""
        with self.lock:
            out = {}
            for k, v in self.data.items():
                k2 = moved_rel(k, old, new, folder)
                v2 = moved_rel(v, old, new, folder=True) if folder else None
                out[k if k2 is None else k2] = v if v2 is None else v2
            if out != self.data:
                self.data = out
                self._save()


class Movimientos:
    """Reuniones que se moveran a otra carpeta en cuanto la app deje de escribir en ellas (cambiar la carpeta en pleno
    directo, msg 1198): ruta interna del audio -> {"to": la carpeta principal elegida, "at": cuando, y tras un intento
    fallido "target" (la carpeta de la reunion ya elegida: el siguiente va a la misma), "intentos", "next" y "error"}.
    En DATA_DIR/movimientos_pendientes.json: si la app se cierra antes, se hace al volver a abrirla."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.data = {k: v for k, v in data.items()
                     if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("to"), str)} \
            if isinstance(data, dict) else {}

    def _save(self):
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as e:  # vale en memoria; sin el fichero, un cierre de la app lo olvida
            log(f"   no se pudo guardar {os.path.basename(self.path)} ({e.__class__.__name__})")

    def get(self, rel):
        with self.lock:
            e = self.data.get(rel)
            return dict(e) if e else None

    def items(self):
        with self.lock:
            return [(k, dict(v)) for k, v in self.data.items()]

    def set(self, rel, entry):
        with self.lock:
            self.data[rel] = dict(entry)
            self._save()

    def pop(self, rel):
        with self.lock:
            e = self.data.pop(rel, None)
            if e is not None:
                self._save()
            return e

    def moved(self, old, new, folder=False):
        """Una carpeta (o una reunion) cambia de ruta: lo apuntado la sigue, la reunion y la carpeta de destino."""
        with self.lock:
            out = {}
            for k, v in self.data.items():
                k2 = moved_rel(k, old, new, folder)
                v = dict(v)
                for f in ("to", "target"):
                    m = moved_rel(v.get(f), old, new, folder=True)
                    if m is not None:
                        v[f] = m
                out[k if k2 is None else k2] = v
            if out != self.data:
                self.data = out
                self._save()


class CarpetasReunion:
    """Las carpetas de reunion que ha creado la app (carpeta por reunion), por su ruta interna. Solo estas son «suyas»
    (own_meeting_dir): una carpeta tuya que por casualidad se llama «AAAA-MM-DD Nombre», como las que algunos ya se
    hacian a mano, no se mueve ni se renombra con la reunion. En DATA_DIR/carpetas_reunion.json; siguen a los
    renombrados y movimientos que hace la app (moved). Sin el (otro PC, reinstalacion), la reunion se mueve como una
    de antes: sus archivos."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = []
        self.data = {os.path.normcase(r): r for r in data if isinstance(r, str) and r} if isinstance(data, list) else {}

    def _save(self):
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sorted(self.data.values()), f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            log(f"   no se pudo guardar {os.path.basename(self.path)} ({e.__class__.__name__})")

    def __contains__(self, rel):
        return isinstance(rel, str) and os.path.normcase(rel) in self.data

    def keys(self):
        with self.lock:
            return set(self.data)

    def add(self, rel):
        with self.lock:
            if self.data.get(os.path.normcase(rel)) != rel:
                self.data[os.path.normcase(rel)] = rel
                self._save()

    def discard(self, rel):
        with self.lock:
            if self.data.pop(os.path.normcase(rel or ""), None) is not None:
                self._save()

    def moved(self, old, new, folder=False):
        with self.lock:
            out = {}
            for r in self.data.values():
                m = moved_rel(r, old, new, folder)
                r = r if m is None else m
                out[os.path.normcase(r)] = r
            if out != self.data:
                self.data = out
                self._save()


_CARPETAS, _CARPETAS_LOCK = {}, threading.Lock()


def carpetas_reunion():
    """El registro CarpetasReunion de este DATA_DIR (los tests lo cambian)."""
    path = os.path.join(DATA_DIR, "carpetas_reunion.json")
    with _CARPETAS_LOCK:
        if path not in _CARPETAS:
            _CARPETAS[path] = CarpetasReunion(path)
        return _CARPETAS[path]


_TEMAS, _TEMAS_LOCK = {}, threading.Lock()


def temas():
    """El almacen Temas de este PC (DATA_DIR/temas.json; uno por DATA_DIR, que los tests cambian)."""
    path = os.path.join(DATA_DIR, "temas.json")
    with _TEMAS_LOCK:
        if path not in _TEMAS:
            _TEMAS[path] = Temas(path)
        return _TEMAS[path]


def is_partial(a):
    """Transcripcion que se quedo a medias: el .srt acaba bastante antes que el audio."""
    d, end = a.get("duration"), a.get("tx_end")
    return bool(a.get("srt") and d and end is not None and not a.get("recording") and d - end > max(60, 0.05 * d)
                and not (COMPLETE and a["path"] in COMPLETE))


def strip_private(node):
    # carpetas vinculadas (fuera de Audios) y la carpeta de una reunion (por_reunion, scan)
    extra = {k: node[k] for k in ("linked", "missing", "por_reunion") if k in node}
    return {"name": node["name"], "path": node["path"], **extra,
            "folders": [strip_private(f) for f in node["folders"]],
            "audios": [{k: v for k, v in a.items() if not k.startswith("_")} for a in node["audios"]],
            "docs": node["docs"]}


# ----------------------------------------------------------------------------- transcripciones

class Transcripts:
    """Lee .srt / .txt con cache por fecha de modificacion (para la busqueda global)."""

    def __init__(self):
        self.cache, self.lock = {}, threading.Lock()

    def load(self, base_abs):
        for ext in (".srt", ".txt"):
            p = base_abs + ext
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            with self.lock:
                hit = self.cache.get(p)
            if hit and hit[0] == mtime:
                return hit[1]
            text = read_text(p)
            if ext == ".srt":
                data = {"kind": "srt", "segments": parse_srt(text), "file": os.path.basename(p)}
            else:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                data = {"kind": "txt", "segments": [{"start": None, "end": None, "text": l} for l in lines],
                        "file": os.path.basename(p)}
            for s in data["segments"]:
                s["_f"] = fold(s["text"])
            with self.lock:
                self.cache[p] = (mtime, data)
            return data
        return None

    def doc_lines(self, abs_path):
        try:
            mtime = os.path.getmtime(abs_path)
        except OSError:
            return []
        with self.lock:
            hit = self.cache.get(abs_path)
        if hit and hit[0] == mtime:
            return hit[1]
        lines = [l.strip() for l in read_text(abs_path).splitlines() if l.strip()]
        data = [(l, fold(l)) for l in lines]
        with self.lock:
            self.cache[abs_path] = (mtime, data)
        return data

    def warm(self, library):
        """Precarga todas las transcripciones para que la primera busqueda sea instantanea."""
        for a in list(library.flat):
            if a["txt"] or a["srt"]:
                try:
                    self.load(os.path.splitext(a["_abs"])[0])
                except OSError:
                    pass


def public_transcript(data):
    if not data:
        return {"kind": None, "segments": []}
    return {"kind": data["kind"], "file": data["file"],
            "segments": [{k: v for k, v in s.items() if k != "_f"} for s in data["segments"]]}


# ----------------------------------------------------------------------------- notas (estilo Notion)
# <audio>.md junto al audio: cabecera con propiedades (como una pagina de base de datos de Notion)
# y debajo las notas en Markdown. Se mueve con el audio igual que el .txt y el .srt.
# idioma: codigo ("es", "pt", "es-en"…) o "auto" (detectar); sin la clave, el idioma del perfil (Ajustes).
# contexto: una o dos frases (de que va, quien participa) que reciben Whisper, tras el vocabulario, y el resumen.
# El id de la lista del tema no va aqui sino en temas() (ver Temas): las versiones anteriores solo aceptan las claves
# que conocen y, con una nueva, toman la cabecera entera por notas.
# participantes_origen: "outlook" o "teams" mientras los participantes son los que sugirio la reunion en curso al
# grabar y el usuario no los ha confirmado ni editado; mientras tanto no salen del PC (participantes_confirmados). En
# el .md no va como clave propia sino detras de los nombres (PARTICIPANTES_NOTA), por lo mismo que el id del tema.
NOTE_KEYS = ("titulo", "icono", "etiquetas", "estado", "participantes", "participantes_origen", "proyecto",
             "workstream", "idioma", "contexto")
IDIOMA_RE = re.compile(r"^(auto|[a-z]{2}(-[a-z]{2})?)$")
NOTE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")  # una clave de la cabecera (las que no conocemos se conservan)
ICON_BAD = re.compile(r"[<>&\"'\x00-\x1f\x7f]")
ICON_MAX = 8  # code points: cabe cualquier emoji compuesto (banderas, tonos de piel, familias con ZWJ)


def clean_icon(v):
    """El icono de una reunion es un emoji o un texto corto (como mucho 8 code points) sin < > & " ' ni controles; si
    no, ninguno. El .md puede venir de una carpeta vinculada compartida (OneDrive, SharePoint) y la interfaz pinta el
    icono como HTML: un valor con marcado ejecutaria codigo en la app (P0-2)."""
    v = " ".join(str(v or "").split())
    return v if len(v) <= ICON_MAX and not ICON_BAD.search(v) else ""


PARTICIPANTES_ORIGENES = ("outlook", "teams")
# «Ana, Luis (sugeridos por Outlook, sin confirmar)»: asi las versiones publicadas (012bb24, 5c55e54), que solo
# aceptan las claves que conocen, leen la cabecera y ensenan los nombres con la nota. La clave participantes_origen
# (como la escribia fix-privacidad antes de fusionarse con fix-perfil) se sigue leyendo; al guardar pasa a la nota.
PARTICIPANTES_NOTA = " (sugeridos por {}, sin confirmar)"
PARTICIPANTES_NOMBRE = {"outlook": "Outlook", "teams": "Teams"}
_PARTICIPANTES_NOTA_RE = re.compile(r"^(.*\S)\s*\(sugeridos por (Outlook|Teams), sin confirmar\)$")


def participantes_del_md(v):
    """(nombres, origen) de la linea «participantes» del .md: con la nota del final, su origen; sin ella, None."""
    m = _PARTICIPANTES_NOTA_RE.match(v or "")
    return (m.group(1), m.group(2).lower()) if m else (v, None)


def participantes_confirmados(props):
    """Los participantes que pueden ir al resumen en la nube y a los destinos de salida: los escritos o confirmados
    por el usuario. Los sugeridos por Outlook/Teams (con participantes_origen) se quedan en el .md hasta entonces."""
    props = props or {}
    return "" if props.get("participantes_origen") else (props.get("participantes") or "")


def parse_notes(text):
    lines = text.replace("\r\n", "\n").split("\n")
    props, body = {}, text
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        # es cabecera si todo lo de dentro es "clave: valor" y alguna clave es nuestra (si no, era un separador). Las
        # claves que no conocemos (las de una version mas nueva) se conservan tal cual: asi esta version no entierra
        # en las notas la cabecera que escriba otra
        keys = [l.partition(":")[0].strip().lower() for l in lines[1:end] if l.strip()] if end else []
        if end and all(":" in l for l in lines[1:end] if l.strip()) and all(NOTE_KEY_RE.match(k) for k in keys) \
                and (not keys or any(k in NOTE_KEYS for k in keys)):
            for line in lines[1:end]:
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip()
                if k == "icono":
                    v = clean_icon(v)
                elif k == "participantes":
                    v, origen = participantes_del_md(v)
                    if origen:
                        props["participantes_origen"] = origen
                elif k == "participantes_origen" and v not in PARTICIPANTES_ORIGENES:
                    v = ""  # como en notes_save: solo outlook o teams
                if k in NOTE_KEYS and v:
                    props[k] = [t.strip() for t in v.strip("[]").split(",") if t.strip()] if k == "etiquetas" else v
                elif v and k not in NOTE_KEYS:  # de una version mas nueva: se conserva tal cual (dump_notes la escribe)
                    props[k] = v
            body = "\n".join(lines[end + 1:]).lstrip("\n")
    return props, body


def dump_notes(props, body):
    head = []
    for k in list(NOTE_KEYS) + [k for k in props if k not in NOTE_KEYS and NOTE_KEY_RE.match(str(k))]:
        v = props.get(k)
        if v and k != "participantes_origen":  # el origen va detras de los participantes (PARTICIPANTES_NOTA)
            v = "[" + ", ".join(v) + "]" if k == "etiquetas" else " ".join(str(v).split())
            if k == "participantes" and props.get("participantes_origen") in PARTICIPANTES_NOMBRE:
                v += PARTICIPANTES_NOTA.format(PARTICIPANTES_NOMBRE[props["participantes_origen"]])
            head.append(f"{k}: {v}")
    out = ("---\n" + "\n".join(head) + "\n---\n\n" if head else "") + (body or "").strip("\n")
    return out + "\n" if out.strip() else ""


# ----------------------------------------------------------------------------- vocabulario por workstream
# <proyecto>/Vocabulario/<workstream>.txt: terminos separados por comas (las lineas que empiezan por #
# son la descripcion). Al grabar se elige el workstream: queda en la cabecera del .md de la reunion
# (proyecto + workstream) y Whisper recibe ese vocabulario en vez del general de Ajustes.
VOCAB_FOLDER = "vocabulario"


def read_vocab(path):
    desc, terms = [], []
    for line in read_text(path).splitlines():
        s = line.strip()
        if s.startswith("#"):
            desc.append(s.lstrip("#").strip())
        elif s:
            terms += engines.split_terms(s)
    return " ".join(d for d in desc if d), terms


def list_vocab():
    out = []

    def norm(p):
        return os.path.normcase(os.path.normpath(p))

    top_names = {norm(ROOT): APP_NAME, **{norm(l["path"]): l["name"] for l in LINKS.values()}}

    def walk(d, depth):
        try:
            entries = sorted(os.scandir(d), key=lambda e: natural_key(e.name))
        except OSError:
            return
        for e in entries:
            if not e.is_dir() or e.name.startswith((".", "_")) or in_app_dir(e.path):
                continue
            if fold(e.name) != VOCAB_FOLDER:
                if depth < 3:
                    walk(e.path, depth + 1)
                continue
            project = unicodedata.normalize("NFC", top_names.get(norm(d)) or os.path.basename(d))
            files = [f for f in os.scandir(e.path) if f.is_file() and f.name.lower().endswith(".txt")]
            for f in sorted(files, key=lambda f: (fold(f.name) != "general.txt", natural_key(f.name))):
                out.append({"id": rel_of(f.path), "project": project, "folder": rel_of(d),
                            "name": unicodedata.normalize("NFC", f.name[:-4]), "_abs": f.path})

    walk(ROOT, 0)
    for l in LINKS.values():  # tambien las carpetas vinculadas (p. ej. la del proyecto en OneDrive)
        walk(l["path"], 0)
    return out


def _project_dir(v):
    """La carpeta del proyecto de una lista: la que contiene su carpeta Vocabulario."""
    return os.path.dirname(os.path.dirname(v["_abs"]))


def _is_app_name(project):
    """El proyecto de las listas de la raiz: el nombre de la app o uno de los que tuvo («Notas»)."""
    from common import APP_FORMER_NAMES
    return fold(project or "") in {fold(a) for a in APP_FORMER_NAMES + (APP_NAME,)}


def _of_project(v, project):
    """La lista es del proyecto `project`: se llama asi o, con el nombre de la app (o uno antiguo), es de la raiz."""
    return fold(v["project"]) == fold(project or "") or (
        _is_app_name(project) and os.path.normcase(os.path.normpath(_project_dir(v)))
        == os.path.normcase(os.path.normpath(ROOT)))


def _project_known(project, vs):
    """Algun proyecto se llama `project`: el de la app (la raiz siempre existe) o el de alguna lista de `vs`."""
    return _is_app_name(project) or any(_of_project(v, project) for v in vs)


def _norm_dir(p):
    return os.path.normcase(os.path.normpath(p))


def _closest(lists, near):
    """De varias listas (proyectos que se llaman igual), la del proyecto que contiene la reunion `near` (el mas
    cercano); None si no hay una sola."""
    if not near:
        return None
    mine = sorted((v for v in lists if _inside(os.path.dirname(near), _project_dir(v))),
                  key=lambda v: -len(_norm_dir(_project_dir(v))))
    if mine and (len(mine) == 1 or len(_norm_dir(_project_dir(mine[0]))) > len(_norm_dir(_project_dir(mine[1])))):
        return mine[0]
    return None


def find_vocab(project, name, id_=None, near=None):
    """La lista de un tema. Por id (la ruta interna de la lista: estable aunque otro proyecto se llame igual), si se
    llama como dicen `project` y `name` (los nombres de la cabecera de la reunion; sin ellos, el id basta, y si ningun
    proyecto se llama ya como dice la cabecera, basta el tema: la vinculada tiene otro nombre en este PC, o el otro PC
    escribio el suyo); si no, por nombres: proyecto + tema (el proyecto de las listas de la raiz tambien con sus
    nombres antiguos). Si dos
    proyectos se llaman igual, `near` (la ruta del audio de la reunion) elige la del proyecto que la contiene; si sigue
    sin estar claro, ninguna. Con proyecto nunca se toma la lista de otro proyecto (antes, si solo habia un tema con
    ese nombre, se usaba aunque fuera de otro cliente).
    Si ningun proyecto se llama ya asi (se renombro su carpeta fuera de la app, o la vinculada tiene otro nombre en
    este PC), vale la lista de ese tema si es la unica fuera de la raiz y la reunion esta en la carpeta de su proyecto
    (la mas cercana de las que tienen listas): es su proyecto con el nombre nuevo. Con dos o mas no se sabe cual es,
    porque la reunion puede estar guardada en la carpeta de otro cliente. Con un id guardado que no es el de esa lista
    (si no, ya se habria devuelto), tampoco: la reunion ya tenia esa ruta cuando se eligio su lista, asi que no estaba
    dentro de la carpeta renombrada (la ruta de las de dentro ha cambiado con ella y no tienen id)."""
    vs = list_vocab()
    if id_:
        hit = next((v for v in vs if v["id"] == id_), None)
        if hit and (not name or (fold(hit["name"]) == fold(name) and (
                not project or _of_project(hit, project) or not _project_known(project, vs)))):
            return hit
        if not name:
            return None
    if not name:
        return None
    same = [v for v in vs if fold(v["name"]) == fold(name)]
    if not project:
        return same[0] if len(same) == 1 else None
    named = [v for v in same if _of_project(v, project)]
    if len(named) == 1:
        return named[0]
    if named:  # varios proyectos con ese nombre: el que contiene la reunion
        return _closest(named, near)
    if id_ or not near or _project_known(project, vs):
        return None
    root = _norm_dir(ROOT)  # la raiz no cuenta: las contiene todas y sus listas solo valen por su nombre
    cands = [v for v in same if _norm_dir(_project_dir(v)) != root]
    homes = {_norm_dir(_project_dir(v)) for v in vs} - {root}
    home = max((d for d in homes if _inside(os.path.dirname(near), d)), key=len, default=None)
    return cands[0] if len(cands) == 1 and home == _norm_dir(_project_dir(cands[0])) else None


def theme_vocab(props, abs_audio, changed=()):
    """El tema de una reunion cuando la peticion lo cambia (al grabar y en la pagina de la reunion): en `props` (la
    cabecera de su .md) quedan los nombres, proyecto y workstream, y el id de su lista va a temas() (ver Temas).
    Por id (`vocabulario` en la peticion; "" = sin tema), proyecto y tema salen de esa lista; por nombres, find_vocab
    la busca (y el proyecto pasa a llamarse como ahora: «Notas» es el de la raiz), salvo que la que ya tenia se llame
    asi: con dos proyectos que se llaman igual, los nombres no dicen cual. Al guardar las notas u otra propiedad no
    cambia nada (una reunion de antes se queda con su cabecera). `changed`: lo que trae la peticion (lo demas viene
    del .md)."""
    changed = changed or {}
    if not {"proyecto", "workstream", "vocabulario"} & set(changed):
        return
    rel, store = rel_of(abs_audio), temas()
    by_id = "vocabulario" in changed
    vid = str(changed.get("vocabulario") or "") if by_id else ""
    try:
        v = find_vocab(None, None, vid) if vid else None
        if by_id and (v or not vid):  # elegido por id: proyecto y tema salen de esa lista ("" = sin tema)
            props.pop("proyecto", None)
            props.pop("workstream", None)
        elif props.get("workstream"):  # por nombres (o un id que ya no existe: como si no hubiera venido)
            v = find_vocab(props.get("proyecto"), props["workstream"], store.get(rel), near=abs_audio)
        else:
            v = None
    except OSError:
        return
    if v:
        props.update(proyecto=v["project"], workstream=v["name"])
    try:
        store.set(rel, v["id"] if v else None)
    except OSError:
        log_exc()


def with_theme_id(props, rel):
    """Las propiedades de una reunion para la interfaz: con `vocabulario`, el id de la lista de su tema en este PC
    (temas()), aunque no este en el .md. La interfaz lo usa para marcar la lista elegida (vocabOf en notas.js)."""
    vid = temas().get(rel) if (props or {}).get("workstream") else None
    return dict(props, vocabulario=vid) if vid else props


def vocab_info(v, language):
    """Para la interfaz: cuantos terminos hay y cuales no le caben a Whisper junto a la frase de estilo."""
    desc, terms = read_vocab(v["_abs"])
    style = engines.STYLE_PROMPT["mix"] if language == "es-en" else engines.STYLE_PROMPT.get(language or "", "")
    text = ", ".join(terms)
    _, dropped = engines.build_prompt(style, text)
    return dict({k: x for k, x in v.items() if not k.startswith("_")}, desc=desc, terms=len(terms), text=text,
                tokens=engines.count_tokens(text), style_tokens=engines.count_tokens(style),
                limit=engines.PROMPT_TOKENS, dropped=dropped)


def vocab_fit(text, language):
    """Lo mismo que vocab_info pero para una lista que se esta escribiendo (aun sin guardar)."""
    style = engines.STYLE_PROMPT["mix"] if language == "es-en" else engines.STYLE_PROMPT.get(language or "", "")
    terms = engines.split_terms(text)
    joined = ", ".join(terms)
    return {"terms": len(terms), "tokens": engines.count_tokens(joined), "style_tokens": engines.count_tokens(style),
            "limit": engines.PROMPT_TOKENS, "dropped": engines.build_prompt(style, joined)[1]}


def md_props(abs_audio):
    """Propiedades de la cabecera del .md de una grabacion ({} si no hay o no se puede leer)."""
    md = os.path.splitext(abs_audio)[0] + ".md"
    try:
        return parse_notes(read_text(md))[0] if os.path.isfile(md) else {}
    except OSError:
        return {}


def vocab_for(abs_audio, st, props=None):
    """Vocabulario para Whisper: el del tema de la reunion o, si no tiene, el general de Ajustes."""
    props = md_props(abs_audio) if props is None else props
    try:
        vid = temas().get(rel_of(abs_audio)) if props.get("workstream") else None  # el id solo desempata
        v = find_vocab(props.get("proyecto"), props.get("workstream"), vid, near=abs_audio)
        if v:
            return ", ".join(read_vocab(v["_abs"])[1])
    except OSError:
        pass
    return st.get("prompt") or ""


# ----------------------------------------------------------------------------- perfil de reunion
# Lo que se aplica a cada grabacion: carpeta de destino, idioma, tema de vocabulario y contexto libre. El perfil
# por defecto vive en los ajustes ("perfil" + "language"); por reunion, idioma y contexto van en la cabecera del .md
# y el directo los relee en cada trozo (Jobs.refresh_prompt). Prioridad: lo elegido para esa reunion > lo que fija
# el perfil > la sugerencia de la reunion en curso (Teams/Outlook) > lo ultimo usado.
PERFIL_KEYS = ("folder", "vocab", "context", "outputs")


def perfil_de(st):
    """El perfil por defecto normalizado (con el idioma, que sigue en "language")."""
    p = dict(DEFAULT_SETTINGS["perfil"], **(st.get("perfil") or {}))
    return {"folder": p["folder"] if isinstance(p.get("folder"), str) else None,
            "vocab": p["vocab"] if isinstance(p.get("vocab"), str) else None,
            "context": " ".join(str(p.get("context") or "").split())[:engines.ABOUT_CHARS],
            "outputs": salidas.destinos_de(p.get("outputs")),
            "language": st.get("language") or ""}


def perfil_folder(st):
    """Carpeta fija del perfil si sigue existiendo; None si el perfil esta en automatico o la carpeta ya no esta."""
    rel = perfil_de(st)["folder"]
    if rel is None:
        return None
    try:
        safe(rel, "dir")
    except ApiError:
        return None
    return rel


def perfil_vocab_fijo(st):
    """Tema fijo del perfil ("" = sin vocabulario, tambien fijo) si su lista sigue existiendo; None si el perfil esta
    en automatico o la lista ya no esta (borrada o renombrada fuera de la app): entonces vale lo automatico, como con
    la carpeta (perfil_folder), y no una grabacion sin vocabulario."""
    vid = perfil_de(st)["vocab"]
    if not vid:
        return vid
    try:
        return vid if find_vocab(None, None, vid) else None
    except OSError:
        return None


def perfil_vocab(st, sugerido=None):
    """Tema de una grabacion nueva: el fijo del perfil (puede ser "" = sin vocabulario); si el perfil lo deja en
    automatico, el que sugiera la reunion en curso y, si no, el ultimo elegido."""
    fijo = perfil_vocab_fijo(st)
    return fijo if fijo is not None else (sugerido or st.get("rec_vocab") or "")


def idioma_de(props, st):
    """Idioma de una reunion: el de su .md ("auto" = detectar) o, si no lo fija, el del perfil (Ajustes)."""
    v = str((props or {}).get("idioma") or "").strip().lower()
    if v == "auto":
        return ""
    return v if IDIOMA_RE.match(v) else (st.get("language") or "")


def meeting_setup(abs_audio, st):
    """Lo que esta reunion le pide a Whisper: vocabulario del tema, contexto libre e idioma. Se lee al arrancar
    una transcripcion y, en directo, en cada trozo (refresh_prompt lo vuelve a leer al cambiarlo en la pagina)."""
    props = md_props(abs_audio)
    return {"prompt": vocab_for(abs_audio, st, props),
            "about": " ".join(str(props.get("contexto") or "").split())[:engines.ABOUT_CHARS],
            "language": idioma_de(props, st)}


def resolve_perfil(st, sug=None, elegido=None, auto_folder=None):
    """Lo que se aplica a una grabacion nueva y de donde sale cada campo ("origen": elegido / perfil / reunion /
    ultimo). `sug` es la sugerencia de la reunion en curso (calendario.suggest) y `elegido` lo que se cambio en el
    dialogo para esa reunion; `auto_folder(vocab_id)` calcula la carpeta automatica (la ultima usada)."""
    sug, elegido, p = dict(sug or {}), dict(elegido or {}), perfil_de(st)
    auto_folder = auto_folder or (lambda vid: st.get("rec_folder") or "")
    out, origen = {}, {}

    def pick(key, *cands):
        for origin, val in cands:
            if val is not None:
                out[key], origen[key] = val, origin
                return

    pick("vocab", ("elegido", elegido.get("vocab")), ("perfil", perfil_vocab_fijo(st)),
         ("reunion", sug.get("vocab") or None),
         ("ultimo", st.get("rec_vocab") or ""))
    auto = auto_folder(out["vocab"])
    if isinstance(auto, tuple):  # (carpeta, origen) de rec_folder_origin: la regla entera (proyecto antes que perfil)
        pick("folder", ("elegido", elegido.get("folder")), (auto[1], auto[0]))
    else:
        pick("folder", ("elegido", elegido.get("folder")), ("perfil", perfil_folder(st)), ("ultimo", auto or ""))
    pick("language", ("elegido", elegido.get("language")), ("perfil", p["language"]))
    pick("context", ("elegido", elegido.get("context")), ("perfil", p["context"]))
    pick("name", ("elegido", elegido.get("name") or None), ("reunion", sug.get("name") or None), ("ultimo", ""))
    pick("participantes", ("elegido", elegido.get("participantes") or None),
         ("reunion", ", ".join(sug.get("participants") or []) or None), ("ultimo", ""))
    return dict(out, origen=origen)


def check_perfil(b, known=None):
    """Valida lo que llega para el perfil (solo las claves presentes) y lo devuelve normalizado. `known`: los destinos
    de salida ya guardados (se aceptan aunque ahora no esten disponibles; los nuevos tienen que existir)."""
    out = {}
    if "folder" in b:
        rel = b["folder"]
        if rel is not None:
            rel = str(rel).replace("\\", "/").strip("/")
            folder = safe(rel, "dir")
            if in_app_dir(folder):
                raise ApiError("No se puede grabar ahí")
        out["folder"] = rel
    if "vocab" in b:
        vid = b["vocab"]
        if vid is not None:
            vid = str(vid).strip()
            if vid and not find_vocab(None, None, vid):
                raise ApiError("Ese tema de vocabulario ya no existe.")
        out["vocab"] = vid
    if "context" in b:
        out["context"] = " ".join(str(b["context"] or "").split())[:engines.ABOUT_CHARS]
    if "outputs" in b:
        roots = [ROOT] + [l["path"] for l in LINKS.values()]  # donde viven las grabaciones: ahi no van documentos
        out["outputs"] = salidas.check_destinos(b["outputs"], APP_DIR, roots,
                                                [d["path"] for d in salidas.destinos_de(known)])
    if "language" in b:
        lang = str(b["language"] or "").strip().lower()
        if lang and not IDIOMA_RE.match(lang):
            raise ApiError("Idioma no válido: " + lang)
        out["language"] = lang
    return out


def update_perfil(d, cambios):
    """Aplica a los ajustes `d` un perfil ya validado por check_perfil."""
    perfil = dict(DEFAULT_SETTINGS["perfil"], **(d.get("perfil") or {}))
    for k in PERFIL_KEYS:
        if k in cambios:
            perfil[k] = cambios[k]
    d["perfil"] = perfil
    if "language" in cambios:
        d["language"] = cambios["language"]


# ----------------------------------------------------------------------------- carpeta principal y carpeta por reunion
# Donde se graba (msgs 1148 y 1158): una carpeta por reunion, «AAAA-MM-DD Nombre», dentro de la carpeta principal de su
# workstream o de su proyecto. La principal se asigna (workstream_folders / project_folders); sin asignar, es la carpeta
# del proyecto en el arbol (la que tiene su carpeta Vocabulario). Sin tema: la fija del perfil y, si no, la ultima
# usada. Lo grabado antes se queda donde esta: solo lo nuevo va en su carpeta.
MEETING_DIR_MAX = 60  # caracteres del nombre de la reunion en el de su carpeta
MEETING_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (.+?)(?: \((\d{1,3})\))?$")
WIN_PATH_MAX = 250  # rutas de Windows sin «rutas largas»: 260 con el terminador; se deja margen


def project_home(v):
    """Ruta interna de la carpeta del proyecto de una lista (la que contiene su carpeta Vocabulario), si esta en el
    arbol de la app (su raiz o una vinculada); None si no (p. ej. una lista de una carpeta de vocabulario conectada)."""
    if not v or not v.get("_abs"):
        return None
    d = _project_dir(v)
    if in_app_dir(d) or not (_inside(d, ROOT) or any(_inside(d, l["path"]) for l in LINKS.values())):
        return None
    return rel_of(d)


def _usable_dir(rel):
    """`rel` es una carpeta que existe en el arbol y donde se puede grabar."""
    if not isinstance(rel, str):
        return False
    try:
        return not in_app_dir(safe(rel, "dir"))
    except ApiError:
        return False


def rec_folder_origin(v, st):
    """(carpeta principal, origen) de una grabacion nueva con el tema `v` (una lista de list_vocab, o None): la asignada
    a su workstream («workstream»), la asignada a su proyecto («proyecto») o la carpeta del proyecto en el arbol
    («carpeta_proyecto»); sin tema (o si ninguna existe ya), la fija del perfil («perfil»), la ultima usada («ultimo») o
    la raiz («raiz»). Es la unica regla: la usan Ctrl+Alt+R, el aviso de Teams, el dialogo y /api/perfil."""
    if v:
        for origin, rel in (("workstream", (st.get("workstream_folders") or {}).get(v.get("id"))),
                            ("proyecto", (st.get("project_folders") or {}).get(v.get("project"))),
                            ("carpeta_proyecto", project_home(v))):
            if rel is not None and _usable_dir(rel):
                return rel, origin
    fija = perfil_folder(st)
    if fija is not None:
        return fija, "perfil"
    rel = st.get("rec_folder")
    if rel and _usable_dir(rel):
        return rel, "ultimo"
    return "", "raiz"


def project_folders_resolved(st):
    """proyecto -> carpeta principal de cada proyecto con listas (la asignada o la suya en el arbol), para la
    interfaz."""
    out = {}
    for v in list_vocab():
        if v["project"] not in out:
            rel, origin = rec_folder_origin(dict(v, id=None), st)  # sin id: la del proyecto, no la de un workstream
            if origin in ("proyecto", "carpeta_proyecto"):
                out[v["project"]] = rel
    for name, rel in (st.get("project_folders") or {}).items():  # asignadas a un proyecto sin listas
        if name not in out and _usable_dir(rel):
            out[name] = rel
    return out


def por_reunion(st):
    """El interruptor general de la carpeta por reunion (encendido por defecto)."""
    return st.get("carpeta_por_reunion", True) is not False


def meeting_dir_name(label="", when=None, limit=MEETING_DIR_MAX):
    """Nombre de la carpeta de una reunion: «AAAA-MM-DD Nombre» o, sin nombre (o si no cabe), «AAAA-MM-DD HH-MM».
    `when`: segundos o un struct_time (por defecto, ahora)."""
    lt = time.localtime(when) if isinstance(when, (int, float)) else (when or time.localtime())
    name = calendario.nombre_archivo(label, limit=limit) if label and limit >= 12 else ""
    return time.strftime("%Y-%m-%d", lt) + " " + (name or time.strftime("%H-%M", lt))


def own_meeting_dir(abs_audio):
    """La carpeta de la reunion si es «suya»: la creo la app para ella (carpetas_reunion) y no hay otra grabacion en
    todo su arbol. Si no (lo grabado antes, una carpeta tuya, o una con otra reunion dentro), None: se mueve como una
    reunion de antes, sus archivos."""
    d = os.path.dirname(abs_audio)
    if in_app_dir(d) or _norm_dir(d) in {_norm_dir(ROOT)} | {_norm_dir(l["path"]) for l in LINKS.values()}:
        return None
    if rel_of(d) not in carpetas_reunion():
        return None
    n = 0
    try:
        for _, _, files in os.walk(d):
            n += sum(1 for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXT)
            if n > 1:
                return None
    except OSError:
        return None
    return d if n == 1 else None


def free_dir(parent, name, mine=None):
    """Ruta para la carpeta `name` dentro de `parent`: esa si no existe, esta vacia o es `mine` (la propia); si no,
    «name (2)», «name (3)»…"""
    for n in range(1, 500):
        p = os.path.join(parent, name if n == 1 else f"{name} ({n})")
        if mine and _norm_dir(p) == _norm_dir(mine):
            return p
        try:
            if not os.path.exists(p) or (os.path.isdir(p) and not os.listdir(p)):
                return p
        except OSError:
            continue
    raise ApiError("Hay demasiadas carpetas con ese nombre: " + name)


def fit_meeting_dir(parent, label, when, stem):
    """El nombre de la carpeta de la reunion que cabe en una ruta de Windows con su archivo mas largo dentro (el
    .hablantes.json): se recorta el nombre de la reunion y, si ni asi, «AAAA-MM-DD HH-MM». None si ni eso cabe: la
    grabacion va a la carpeta principal, como antes de la carpeta por reunion."""
    longest = len(stem) + len(voices.SIDECAR_EXT) + 6  # «\», « (n)» y margen
    limit = MEETING_DIR_MAX
    while True:
        name = meeting_dir_name(label, when, limit)
        if not WINDOWS or len(os.path.join(parent, name)) + longest <= WIN_PATH_MAX:
            return name
        if limit < 12:
            return None
        limit -= 8


def meeting_dir_preview(folder_rel, label="", st=None, when=None):
    """Ruta interna de la carpeta que tendria ahora una reunion grabada en `folder_rel` (sin crearla), o la propia
    carpeta si la carpeta por reunion esta apagada. Para ensenar «Se guarda en …» antes de grabar."""
    if st is not None and not por_reunion(st):
        return folder_rel
    parent = safe(folder_rel, "dir")
    lt = time.localtime(when) if isinstance(when, (int, float)) else (when or time.localtime())
    stem = time.strftime("%Y-%m-%d %H-%M-%S", lt) + (f" - {label}" if label else "")
    name = fit_meeting_dir(parent, label, lt, stem)
    return rel_of(free_dir(parent, name)) if name else folder_rel


def rec_destino(v, st, name="", when=None):
    """Lo que se usara para guardar una grabacion nueva: {"carpeta": la principal, "origen": de donde sale (ver
    rec_folder_origin), "reunion": la carpeta que se creara para ella (o la principal sin carpeta por reunion),
    "por_reunion"}. Para la tarjeta de grabar, Ctrl+Alt+R y Ajustes (la misma regla que rec_start)."""
    rel, origin = rec_folder_origin(v, st)
    label = ""
    if name:
        try:
            label = clean_name(name)
        except ApiError:
            label = calendario.nombre_archivo(name)
    try:
        reunion = meeting_dir_preview(rel, label, st, when)
    except ApiError:
        reunion = rel
    return {"carpeta": rel, "origen": origin, "reunion": reunion, "por_reunion": por_reunion(st)}


def display_rel(rel):
    """Una ruta interna como la ve el usuario: «\\Proyecto Alfa\\2026-11-07 Comité\\» (una vinculada, con su nombre)."""
    parts = [p for p in (rel or "").split("/") if p]
    if parts and parts[0].startswith(LINK):
        parts[0] = (LINKS.get(parts[0][len(LINK):]) or {}).get("name") or parts[0]
    return "\\" + "".join(p + "\\" for p in parts)


def move_one(f, t):
    """Mueve un archivo o una carpeta: en la misma unidad, os.rename (de una vez; con algo abierto dentro, falla sin
    tocar nada). Entre unidades, copia y borra, y solo archivos: shutil.move copiaria una carpeta entera y la borraria
    a medias si algo de dentro esta abierto (en Windows), y eso no se puede deshacer bien."""
    try:
        os.rename(f, t)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    if os.path.isdir(f):
        raise ApiError("La carpeta de la reunión tiene subcarpetas y el destino está en otra unidad: muévela desde el "
                       "Explorador.")
    shutil.copy2(f, t)
    try:
        os.remove(f)
    except OSError:
        try:
            os.remove(t)
        except OSError:
            pass
        raise


def move_files(moves):
    """Mueve cada (origen, destino) en orden (move_one), con reintentos si el archivo esta en uso un instante (el
    reproductor, OneDrive, el antivirus). Si uno no se puede, deshace los ya movidos y lanza ApiError: o se mueve todo o
    nada. Quien llama comprueba antes que ningun destino existe (en Windows y en el Mac os.rename no avisa igual)."""
    done = []
    try:
        for f, t in moves:
            if f == t:
                continue
            for attempt in range(12):
                try:
                    move_one(f, t)
                    break
                except PermissionError:
                    if attempt == 11:
                        raise
                    time.sleep(0.25)
            done.append((f, t))
    except (OSError, ApiError) as e:
        for f, t in reversed(done):
            try:
                move_one(t, f)
            except (OSError, ApiError):
                log_exc()
        if isinstance(e, ApiError):
            raise
        if isinstance(e, PermissionError):
            raise ApiError("El archivo está en uso (¿OneDrive sincronizando o abierto en otro programa?). "
                           "Prueba otra vez en unos segundos.")
        raise ApiError(f"No se ha podido mover ({e.__class__.__name__}).")


def moved_rel(p, old, new, folder=False):
    """La ruta interna `p` despues de que `old` pase a ser `new` (con folder, tambien lo que hay dentro); None si no le
    afecta."""
    if not isinstance(p, str) or not old:
        return None
    if p == old:
        return new
    if folder and p.startswith(old + "/"):
        return new + p[len(old):]
    return None


MEETING_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ _-]")


def migrate_main_folders(d):
    """Una vez (carpetas_v), al llegar la carpeta por reunion: la carpeta de cada proyecto que apuntaba la app era
    «la ultima usada», y la de quien ya se hacia una carpeta por reunion a mano es la de una reunion
    («AAAA-MM-DD …»): las nuevas acabarian dentro de ella. Esa pasa a ser su carpeta padre; lo demas se queda como
    asignacion. Igual con la ultima carpeta (rec_folder). Devuelve [(proyecto o «la última», vieja, nueva)]."""
    out = []

    def fix(rel, who):
        if isinstance(rel, str) and MEETING_LIKE_RE.match(rel.rpartition("/")[2]) \
                and not is_link_root(rel):
            new = rel.rpartition("/")[0]
            out.append((who, rel, new))
            return new
        return rel
    d["project_folders"] = {k: fix(v, k) for k, v in (d.get("project_folders") or {}).items()}
    if d.get("rec_folder"):
        d["rec_folder"] = fix(d["rec_folder"], "la última")
    d["carpetas_v"] = 1
    return out


def settings_moved(d, old, new, folder=False, project=None):
    """Los ajustes que guardan rutas internas siguen a una carpeta o a un tema renombrados: carpeta y tema fijos del
    perfil, ultima carpeta y ultimo tema al grabar y la carpeta de cada proyecto. Si no, Ctrl+Alt+R se quedaba sin
    vocabulario y grabando en la raiz. project=(nombre viejo, nuevo): el proyecto tambien cambia de nombre."""
    perfil = dict(d.get("perfil") or {})
    for k in ("folder", "vocab"):
        m = moved_rel(perfil.get(k), old, new, folder)
        if m is not None:
            perfil[k] = m
    if d.get("perfil") is not None or perfil:
        d["perfil"] = perfil
    for k in ("rec_folder", "rec_vocab"):
        m = moved_rel(d.get(k), old, new, folder)
        if m is not None:
            d[k] = m
    pf = {}
    for name, rel in (d.get("project_folders") or {}).items():
        m = moved_rel(rel, old, new, folder)
        pf[name] = rel if m is None else m
    if project and project[0] in pf and project[1] not in pf:
        pf[project[1]] = pf.pop(project[0])
    d["project_folders"] = pf
    if d.get("workstream_folders"):  # id de la lista -> carpeta: los dos son rutas internas
        wf = {}
        for vid, rel in d["workstream_folders"].items():
            k, m = moved_rel(vid, old, new, folder), moved_rel(rel, old, new, folder)
            wf[vid if k is None else k] = rel if m is None else m
        d["workstream_folders"] = wf


def theme_moved(props, vid, new, lists, project, ambiguous=False, inside=False):
    """Lo que cambia en la cabecera de una reunion cuando la carpeta de un proyecto (o la vinculada) pasa de llamarse
    project[0] a project[1] (`new`: su ruta interna, ya renombrada): si la cabecera dice el nombre viejo, el nuevo,
    siempre que la reunion sea de ese proyecto: el id de la lista de su tema (`vid`, de temas(), que ya lo ha seguido)
    esta dentro o, sin id, ningun otro proyecto se sigue llamando asi (`ambiguous` False) o la reunion esta dentro
    (`inside`). `lists`: id -> lista, ya renombrada. Solo cambia el valor de `proyecto`, que todas las versiones leen;
    {} si nada cambia."""
    if not project or not props.get("workstream") or fold(props.get("proyecto") or "") != fold(project[0]) \
            or props.get("proyecto") == project[1]:
        return {}
    v = lists.get(vid) if vid else None
    if v and fold(v["name"]) == fold(props["workstream"]):  # el id de su tema dice de que proyecto es
        return {"proyecto": project[1]} if moved_rel(vid, new, new, folder=True) is not None else {}
    return {"proyecto": project[1]} if inside or not ambiguous else {}


def has_vocab_dir(folder):
    """La carpeta es la de un proyecto: tiene una carpeta Vocabulario (sus listas toman de ella el nombre)."""
    try:
        with os.scandir(folder) as it:
            return any(e.is_dir() and fold(e.name) == VOCAB_FOLDER for e in it)
    except OSError:
        return False


# ----------------------------------------------------------------------------- importar listas de vocabulario

def import_vocab(src, project=None):
    """Copia a este PC las listas de vocabulario de otra carpeta: dentro de `src`, cada <proyecto>/Vocabulario/*.txt;
    o la propia `src` si es un proyecto (tiene una carpeta Vocabulario) o una carpeta Vocabulario suelta (va al
    proyecto `project` o, si no se da, al nombre de su carpeta padre). Cada lista va a la carpeta Vocabulario del
    proyecto (la que ya tenga; si es nuevo, <raiz>/<proyecto>/Vocabulario) y nunca se pisa una que exista.
    Devuelve {copiadas, omitidas, proyectos}."""
    src = os.path.normpath(os.path.expandvars((src or "").strip().strip('"')))
    if not os.path.isabs(src) or not os.path.isdir(src):
        raise ApiError("Esa carpeta no existe: " + src)
    if in_app_dir(src):
        raise ApiError("Esa carpeta es la de la propia app.")

    def vocab_dir(d):
        try:
            return next((e.path for e in os.scandir(d) if e.is_dir() and fold(e.name) == VOCAB_FOLDER), None)
        except OSError:
            return None

    found = []  # (proyecto, carpeta Vocabulario)
    if fold(os.path.basename(src)) == VOCAB_FOLDER:
        found.append((project or os.path.basename(os.path.dirname(src)), src))
    elif vocab_dir(src):
        found.append((project or os.path.basename(src), vocab_dir(src)))
    else:
        for e in sorted(os.scandir(src), key=lambda e: natural_key(e.name)):
            if e.is_dir() and not e.name.startswith((".", "_")) and vocab_dir(e.path):
                found.append((e.name, vocab_dir(e.path)))
    if not found:
        raise ApiError("En esa carpeta no hay listas de vocabulario (<Proyecto>\\Vocabulario\\<Tema>.txt).")
    existing = list_vocab()
    copied, skipped, projects = 0, 0, []
    for proj, folder in found:
        try:
            proj = clean_name(proj)
        except ApiError:
            skipped += 1
            continue
        same = next((v for v in existing if fold(v["project"]) == fold(proj)), None)
        vdir = os.path.dirname(same["_abs"]) if same else os.path.join(ROOT, proj, "Vocabulario")
        files = [f for f in os.scandir(folder) if f.is_file() and f.name.lower().endswith(".txt")]
        if not files:
            continue
        os.makedirs(vdir, exist_ok=True)
        projects.append(proj)
        for f in sorted(files, key=lambda f: natural_key(f.name)):
            try:
                name = clean_name(f.name[:-4])
            except ApiError:
                skipped += 1
                continue
            dest = os.path.join(vdir, name + ".txt")
            if os.path.exists(dest):
                skipped += 1
                continue
            shutil.copyfile(f.path, dest)
            copied += 1
    log(f"🔤 vocabulario importado de {src}: {copied} listas copiadas, {skipped} omitidas ({', '.join(projects)})")
    return {"copiadas": copied, "omitidas": skipped, "proyectos": projects}


def to_recycle_bin(paths):
    """A la papelera de Windows (se puede restaurar), no un borrado definitivo.
    Fuera de Windows (solo desarrollo) se mueven a DATA_DIR/papelera/<fecha>/."""
    if not WINDOWS:
        dest = os.path.join(DATA_DIR, "papelera", time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(dest, exist_ok=True)
        for p in paths:
            shutil.move(p, os.path.join(dest, os.path.basename(p)))
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

    buf = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    op = SHFILEOPSTRUCTW(None, 3, ctypes.cast(buf, ctypes.c_void_p), None,
                         0x40 | 0x10 | 0x4 | 0x400, False, None, None)  # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0 or op.fAnyOperationsAborted:
        raise ApiError("No se pudo mandar a la papelera (¿archivo en uso?).")


def backup_dir(rel):
    """Copia de lo que transcribio Whisper antes de editarlo a mano (fuera de OneDrive)."""
    return os.path.join(DATA_DIR, "originales", hashlib.md5(rel.encode("utf-8")).hexdigest())


def save_original(rel, segs):
    """Guarda lo que transcribio Whisper (si aun no hay copia), para poder restaurarlo."""
    bdir = backup_dir(rel)
    if os.path.isdir(bdir):
        return
    os.makedirs(bdir)
    write_outputs(os.path.join(bdir, "original.wav"), segs)
    with open(os.path.join(bdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"path": rel, "at": time.time()}, f, ensure_ascii=False)


def srt_block(i, a, b, t):
    return f"{i}\n{fmt_srt(a)} --> {fmt_srt(b)}\n{t}\n"


def write_outputs(audio_abs, segs):
    base = os.path.splitext(audio_abs)[0]
    txt = "\n".join(t for _, _, t in segs) + "\n"
    srt = "\n".join(srt_block(i, a, b, t) for i, (a, b, t) in enumerate(segs, 1))
    # utf-8-sig (con BOM) para que Bloc de notas / Excel muestren bien los acentos
    save_text(base + ".txt", txt, "utf-8-sig")
    save_text(base + ".srt", srt, "utf-8")


def seg_key(start):
    """Identifica una frase por su inicio tal como queda en el .srt (milisegundos, 2 decimales al leerlo)."""
    return round(int(round(float(start) * 1000)) / 1000, 2)


def apply_edits(segs, edits):
    """Correcciones hechas a mano durante el directo ("" = frase borrada) sobre lo que da Whisper."""
    if not edits:
        return segs
    out = []
    for a, b, t in segs:
        t = edits.get(seg_key(a), t)
        if t:
            out.append((a, b, t))
    return out


class LiveOutputs:
    """El .txt y el .srt del directo: lo nuevo se ANADE al final; solo si cambia algo de antes
    (una correccion, "rehacer desde el principio") se reescribe el archivo, y siempre en el sitio."""

    def __init__(self, audio_abs):
        self.base = os.path.splitext(audio_abs)[0]
        self.done, self.sizes = None, {}  # lo que ya esta en disco y el tamano que deberia tener
        self.lock = threading.Lock()

    def _same_on_disk(self):
        try:
            return all(os.path.getsize(self.base + e) == n for e, n in self.sizes.items())
        except OSError:
            return False  # lo han borrado o movido: se escribe entero

    def write(self, segs):
        segs = list(segs)
        with self.lock:
            d = self.done
            if d is not None and segs[:len(d)] == d and self._same_on_disk():
                new = segs[len(d):]
                if not new:
                    return
                txt = "".join(t + "\n" for _, _, t in new).encode("utf-8")
                # en el .srt cada bloque va separado del anterior por una linea en blanco
                srt = "".join(("\n" if d or k else "") + srt_block(len(d) + k + 1, a, b, t)
                              for k, (a, b, t) in enumerate(new)).encode("utf-8")
                write_bytes(self.base + ".txt", txt, append=True)
                write_bytes(self.base + ".srt", srt, append=True)
            else:
                write_outputs(self.base + ".wav", segs)  # (solo se usa para sacar la ruta base)
            self.done = segs
            self.sizes = {e: os.path.getsize(self.base + e) for e in (".txt", ".srt")}


# ----------------------------------------------------------------------------- cola de trabajos

class Job:
    def __init__(self, path, name, preset, language):
        self.id = uuid.uuid4().hex[:10]
        self.path, self.name, self.preset, self.language = path, name, preset, language
        self.engine = engines.PRESETS_BY_ID[preset]["engine"] if preset in engines.PRESETS_BY_ID else preset
        self.kind = "summary" if preset == "resumen" else "voces" if preset == "voces" else "transcribe"
        self.then_summary = False  # voces: al terminar, lanzar el resumen automatico (asi lleva los nombres)
        self.status, self.progress, self.stage = "queued", 0.0, "En cola"
        self.created, self.started, self.finished = time.time(), None, None
        self.t_transcribe = None
        self.error, self.duration, self.detected, self.eta = None, None, None, None
        self.cancel = False
        # solo en directo: segundos grabados / ya transcritos, y contador de reescrituras del .srt
        self.live, self.recording, self.recorded, self.transcribed, self.rev = False, False, 0.0, 0.0, 0
        self.restart = False
        self.resume_from = None  # continuar una transcripcion a medias desde este segundo
        self.final_pass = False  # repaso del archivo entero al terminar un directo en la nube
        self.opts = None  # solo en directo: opciones que live.run va leyendo (vocabulario incluido)
        self.translate, self.translation, self.directo = False, [], None  # directo en la nube (live.py publish)
        # correcciones a mano durante el directo: {inicio de la frase: texto}, sobre lo que da Whisper (raw)
        self.edits, self.raw, self.out, self.edit_lock = {}, [], None, threading.Lock()

    def public(self):
        now = time.time()
        d = {"id": self.id, "path": self.path, "name": self.name, "preset": self.preset,
             "engine": self.engine, "status": self.status, "progress": round(self.progress, 4),
             "stage": self.stage, "error": self.error, "duration": self.duration,
             "language": self.detected, "eta": self.eta, "created": self.created,
             "elapsed": ((self.finished or now) - self.started) if self.started else None,
             "resume_from": self.resume_from, "kind": self.kind, "final_pass": self.final_pass}
        if self.live:
            d.update(live=True, recording=self.recording, recorded=round(self.recorded, 1),
                     transcribed=round(self.transcribed, 1), rev=self.rev, directo=self.directo)
        if self.error:  # el error en una frase con qué hacer (explicar.py); «error» sigue siendo el técnico
            d["aviso"] = explicar.explicar(self.error)
        return d


def keepalive_s(st):
    """Segundos que un proceso de Whisper sigue cargado sin trabajo (ajuste worker_keepalive_min; 0 = 5 min, como
    antes de existir el ajuste). Mientras esta cargado ocupa su RAM (medido: small ~450 MB, medium ~1 GB)."""
    try:
        m = int(st.get("worker_keepalive_min", 30) or 0)
    except (TypeError, ValueError):
        m = 30
    return m * 60 if m > 0 else engines.WorkerClient.IDLE_S


def preload_model(st, cached=None):
    """(worker, modelo) que conviene dejar cargado al arrancar: el del directo si es local y ya esta descargado; si
    no, el de la cola. None si worker_preload esta apagado o no hay modelo en cache (descargar no es precargar)."""
    if not st.get("worker_preload", True):
        return None
    cached = cached or engines.model_cached
    for which, key in (("live", "live_preset"), ("batch", "preset")):
        p = engines.PRESETS_BY_ID.get(st.get(key) or "")
        if p and p["engine"] == "local" and cached(p["model"]):
            return which, p["model"]
    return None


def live_cloud_cfg(st):
    """Ajustes de la nube para el directo: los de cloud y el modelo del resumen (la traduccion lo usa con una
    pasarela propia). _run_live los vuelve a leer en cada trozo, no una copia del arranque."""
    return dict(st.get("cloud") or {}, summary_model=st.get("summary_model"))


class PendingPasses(Complete):
    """Reuniones con el repaso final pendiente (en cola o a medias), en DATA_DIR/repasos_pendientes.json: se apunta
    al ponerlo en cola y se quita al terminar (bien, con error o cancelado a mano). Lo que siga apuntado al arrancar
    se quedo a medias al apagar la app (o al cerrar la sesion de Windows) y se retoma.

    Con cada reunion va local_part: si su directo tuvo algun trozo en local (sin el dato, el formato anterior, una lista
    de rutas, se da por hecho que si). Si el repaso no acaba, el texto que queda es el del directo, y lo transcrito en
    local no sale del PC por el resumen automatico sin permiso. Quien lo decide es CloudText, que vive en DATA_DIR y
    sobrevive al reinicio igual que este fichero: local_part queda como constancia en el repaso (Job.local_part)."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, list):  # formato anterior: solo las rutas
            raw = {rel: {} for rel in raw if isinstance(rel, str)}
        self.data = {rel: v for rel, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}

    def _save(self):
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, sort_keys=True)
        os.replace(self.path + ".tmp", self.path)

    def set(self, rel, done=True, local_part=True):
        """done: apuntarla (con local_part) o, con False, quitarla."""
        with self.lock:
            if done:
                info = {"local_part": bool(local_part)}
                if self.data.get(rel) == info:
                    return
                self.data[rel] = info
            elif rel in self.data:
                del self.data[rel]
            else:
                return
            self._save()

    def local_part(self, rel):
        """Si el directo de esa reunion tuvo algun trozo en local (sin el dato, si)."""
        return (self.data.get(rel) or {}).get("local_part") is not False


_SALIDAS_LOCK = threading.Lock()  # temporizadores de la reescritura agrupada de los destinos de salida


class Jobs:
    def __init__(self, settings, library):
        self.settings, self.library = settings, library
        self.list, self.lock = [], threading.Lock()
        self.claimed = set()  # grabaciones de la propia app: el directo lo arranca quien graba, no el vigilante
        self.queues = {"local": queue.Queue(), "cloud": queue.Queue()}
        # Whisper corre en procesos aparte (worker.py): uno para la cola y otro para el directo,
        # cada uno con su modelo en memoria; siguen cargados worker_keepalive_min tras el ultimo trabajo
        keep = lambda: keepalive_s(self.settings.get())  # noqa: E731
        self.local = engines.LocalEngine(DATA_DIR, "batch", idle_s=keep)
        self.live_worker = engines.WorkerClient("live", DATA_DIR, idle_s=keep)
        self.cloud = engines.CloudEngine(os.path.join(DATA_DIR, "tmp"), log=log)
        if self.cloud.purge_tmp():  # trozos de audio de una sesion que se corto (apagado, actualizacion, cuelgue)
            log("   borrados los trozos de audio temporales que dejó la sesión anterior")
        self.summarizer = engines.Summarizer(keys=self.cloud.keys)  # en la nube (Groq); mismas claves y cupo
        self.summarizer_agente = None  # con el agente del PC (procesado "agente"): se crea al primer uso
        self.queues["resumen"] = queue.Queue()
        self.queues["voces"] = queue.Queue()  # quien habla: un subproceso de prioridad baja, de uno en uno
        self._voices, self.voice_vectors = None, {}  # voces.json (se abre al primer uso) y huellas por reunion
        self._registro, self._salidas_timers = None, {}  # salidas.json (documentos escritos) y reescrituras pendientes
        self.closing = False  # apagando: un repaso final cancelado queda pendiente para el proximo arranque
        # trabajos que se estan ejecutando o terminando por reunion (busy_for): lo que encadenan al acabar (voces,
        # resumen) ya esta en cola cuando se sueltan. on_idle(rel): la reunion ha quedado libre; on_tick(): cada 3 s
        # (App: los movimientos pendientes, cambiar la carpeta en pleno directo)
        self.held, self.on_idle, self.on_tick = {}, None, None
        for kind in self.queues:
            threading.Thread(target=self._worker, args=(kind,), daemon=True, name=f"worker-{kind}").start()
        threading.Thread(target=self._watch_recordings, daemon=True, name="live-watch").start()
        threading.Thread(target=self._resume_final_passes, daemon=True, name="repasos-pendientes").start()

    def shutdown(self):
        """Apagar: lo que esta en cola se cancela, lo que corre recibe la senal de cancelar y los procesos de Whisper
        (cola y directo) se matan. Los trozos ya escritos (.txt/.srt en directo) se quedan; los de audio que se
        estaban subiendo a la nube (DATA_DIR/tmp) se borran (lo que siga abierto, al arrancar la proxima vez)."""
        with self.lock:
            self.closing = True  # un repaso final en cola o a medias queda apuntado: se retoma al volver a abrir
            for j in self.list:
                if j.status == "queued":
                    j.status = "cancelled"
                elif j.status == "running":
                    j.cancel = True
        for w in (getattr(self.local, "worker", None), self.live_worker):
            try:
                if w is not None:
                    w.kill()
            except Exception:
                log_exc()
        try:
            if getattr(self, "cloud", None) is not None:
                self.cloud.purge_tmp()
        except Exception:
            log_exc()

    def preload(self):
        """Al arrancar: deja cargado el modelo local que va a usar el directo (o, si el directo es en la nube, el de
        la cola) en su proceso de prioridad baja, para que el primer trozo de la reunion no espere a la carga
        (medido: 2-6 s en frio). Devuelve (worker, modelo) o None. Pensado para un hilo aparte: no bloquea nada."""
        st = self.settings.get()
        pick = preload_model(st)
        if not pick:
            return None
        which, model = pick
        w = self.live_worker if which == "live" else self.local.worker
        t0 = time.time()
        try:
            if w.ensure(model, int(st.get("threads") or default_threads())):
                log(f"modelo {model} precargado para {'el directo' if which == 'live' else 'la cola'} "
                    f"en {time.time() - t0:.1f} s")
        except engines.EngineError as e:
            log(f"no se pudo precargar {model}: {e}")
        return pick

    @property
    def voices(self):
        if self._voices is None:
            self._voices = voices.Voices(os.path.join(DATA_DIR, "voces.json"))
        return self._voices

    def active_for(self, path, kinds=("transcribe",)):
        """Trabajo en cola o en marcha sobre ese audio. Por defecto solo transcripciones (directo incluido):
        un resumen en marcha no impide editar, restaurar ni volver a transcribir la reunion."""
        with self.lock:
            return next((j for j in self.list if j.path == path and j.kind in kinds
                         and j.status in ("queued", "running")), None)

    def _hold(self, rel):
        """Un trabajo de esa reunion empieza a ejecutarse (en su hilo): hasta _release, busy_for la da por ocupada."""
        with self.lock:
            held = self.__dict__.setdefault("held", {})
            held[rel] = held.get(rel, 0) + 1

    def _release(self, rel):
        """El trabajo ha terminado del todo (con lo que encadena ya en cola): si la reunion queda libre, on_idle."""
        with self.lock:
            held = self.__dict__.setdefault("held", {})
            n = held.get(rel, 0) - 1
            if n > 0:
                held[rel] = n
            else:
                held.pop(rel, None)
        cb = getattr(self, "on_idle", None)
        if n <= 0 and cb and not self.busy_for(rel):
            try:
                cb(rel)
            except Exception:
                log_exc()

    def busy_for(self, rel):
        """Por que la app va a escribir aun en los archivos de esa reunion (una frase), o None: un trabajo suyo en cola,
        en marcha o terminando (lo que encadena al acabar, voces y resumen, ya esta en cola cuando se suelta: hold),
        o su repaso final pendiente de un cierre de la app (se retoma al arrancar)."""
        kinds = {"summary": "Se está resumiendo esta reunión",
                 "voces": "Se están identificando las voces de esta reunión"}
        with self.lock:
            held = (getattr(self, "held", None) or {}).get(rel)
            j = next((j for j in self.list if j.path == rel and j.status in ("queued", "running")), None)
        if j:
            return kinds.get(j.kind, "Se está transcribiendo esta reunión")
        if held:
            return "Se está procesando esta reunión"
        try:
            if rel in self._pending().data:
                return "Falta el repaso final de esta reunión"
        except Exception:
            log_exc()
        return None

    # --- en directo
    def _watch_recordings(self):
        """Arranca la transcripcion en directo en cuanto aparece una grabacion de OBS en curso. En la misma vuelta, el
        resto de cosas de cada 3 s (on_tick: los movimientos pendientes de las reuniones que ya han quedado libres)."""
        while True:
            time.sleep(3)
            if getattr(self, "on_tick", None):
                try:
                    self.on_tick()
                except Exception:
                    log_exc()
            try:
                if not self.settings.get().get("live_auto"):
                    continue
                self.library.refresh(max_age=2.5)
                for a in list(self.library.flat):
                    if not a.get("recording"):
                        continue
                    with self.lock:  # una vez por grabacion (si el usuario la para, no se relanza)
                        seen = a["path"] in self.claimed or any(j.path == a["path"] and j.live for j in self.list)
                    if not seen and not self.active_for(a["path"]):
                        self.start_live(a["path"], auto=True)
            except Exception:
                log_exc()

    def start_live(self, rel, preset=None, auto=False):
        st = self.settings.get()
        preset = preset or st.get("live_preset") or "medium"
        if preset not in LIVE_PRESETS:
            preset = "medium"
        if preset == "nube" and procesado_de(st) != "api":
            preset = "medium"  # la nube esta desactivada en Ajustes: en local
        if preset == "nube":
            try:
                engines.CloudEngine.endpoint(st["cloud"])
            except engines.EngineError:
                preset = "medium"  # sin API key: en local
        abs_path = safe(rel, "file")
        if self.active_for(rel):
            raise ApiError("Ese audio ya se está transcribiendo.")
        job = Job(rel, os.path.basename(rel), preset, meeting_setup(abs_path, st)["language"] or None)
        job.engine, job.live, job.recording = "live", True, True
        # algun trozo en local (o, al retomar tras un reinicio, lo de antes sin constar que fuera en la nube): el
        # resumen automatico no lo sube a la nube sin permiso (CloudText, Jobs.auto_summary)
        retoma = os.path.isfile(os.path.splitext(abs_path)[0] + ".srt")
        job.local_part = (engines.PRESETS_BY_ID[preset]["engine"] != "cloud"
                          or (retoma and rel not in self._cloud_text()))
        self._cloud_text().set(rel, not job.local_part)
        job.status, job.started, job.stage = "running", time.time(), "Empezando en directo…"
        COMPLETE.set(rel, False)  # hasta que termine bien, no se da por completa
        with self.lock:
            self.list.append(job)
        self._hold(rel)  # hasta que el directo acabe con lo que encadene (repaso final, voces, resumen)
        threading.Thread(target=self._run_live_held, args=(job, abs_path, st), daemon=True, name="live").start()
        log(f"● {job.name}  transcripción en directo{' (automática)' if auto else ''}")
        return job.id

    def _final_pass(self, job):
        """Al terminar un directo en la nube se repasa la grabacion entera de una vez: Whisper ve
        mucho mas contexto que en trozos de 8-15 s y el texto queda mejor (1 h ~ 2 min en Groq).
        Mientras tanto se sigue viendo el del directo. No se hace si lo corregiste a mano."""
        st = self.settings.get()
        if not st.get("live_final_pass", True) or job.preset != "nube" or job.recorded < 30:
            return False
        if job.edits or os.path.isdir(backup_dir(job.path)):
            log(f"   {job.name}: sin repaso final (tiene correcciones a mano)")
            return False
        try:
            self._cloud_ready()
        except ApiError:
            return False
        # local_part del directo: si tuvo algun trozo en local (sin el dato, si: lo seguro)
        self._queue_final_pass(job.path, job.name, job.language, local_part=getattr(job, "local_part", True))
        return True

    def _queue_final_pass(self, rel, name, language, local_part=True):
        """El repaso final de esa reunion a la cola: el del directo que acaba de terminar o, al arrancar, el que quedo
        apuntado (_resume_final_passes). Queda apuntado en repasos_pendientes.json hasta que termine: si se apaga
        antes, se retoma al arrancar. local_part: si su directo tuvo algun trozo en local (sin el dato, si: lo seguro).
        Va con el repaso (j.local_part) y se apunta con el. Si el repaso no acaba, el texto que queda es el del directo,
        y el resumen automatico mira CloudText (apuntado al empezar el directo): lo transcrito en local no sale del PC
        sin permiso, tampoco con un repaso retomado tras reiniciar (tests/test_integracion.py)."""
        j = Job(rel, name, "nube", language)
        j.final_pass, j.stage, j.local_part = True, "En cola: repaso final en la nube", local_part is not False
        self._pending().set(rel, local_part=j.local_part)
        with self.lock:
            self.list.append(j)
        self.queues[j.engine].put(j)
        log(f"↻ {name}  repaso final de la grabación entera en la nube")
        return j

    def _pending(self):
        """Los repasos finales pendientes (PendingPasses), en el DATA_DIR vigente."""
        path = os.path.join(DATA_DIR, "repasos_pendientes.json")
        reg = getattr(self, "_pending_reg", None)
        if reg is None or reg.path != path:
            reg = self._pending_reg = PendingPasses(path)
        return reg

    def _final_pass_over(self, rel, keep_live_text=False):
        """El repaso final de esa reunion ya no esta pendiente. keep_live_text: no se hizo (cancelado a mano o ya no se
        puede hacer): se queda el texto del directo y con el se hacen los destinos y el resumen."""
        try:
            self._pending().set(rel, False)
        except OSError:
            log_exc()
        if keep_live_text:
            log("   se mantiene el texto del directo")
            self._after_transcribe(rel)

    def _resume_final_passes(self, delay=3.0):
        """Al arrancar: los repasos finales que quedaron en cola o a medias al apagar se vuelven a poner en cola, y al
        terminar se hacen los destinos y el resumen como siempre. Si ya no se puede (sin nube, el repaso apagado en
        Ajustes, la reunion corregida a mano), se hacen con el texto del directo; si la reunion ya no esta, se
        olvida."""
        time.sleep(delay)
        try:
            for rel in sorted(self._pending().data):
                try:
                    abs_path = safe(rel, "file")
                except ApiError:
                    log(f"   repaso final pendiente de {rel}: la reunión ya no está ahí, se olvida")
                    self._final_pass_over(rel)
                    continue
                if self.active_for(rel):
                    continue
                ok = self.settings.get().get("live_final_pass", True) and not os.path.isdir(backup_dir(rel))
                if ok:
                    try:
                        self._cloud_ready()
                    except ApiError:
                        ok = False
                if ok:
                    log(f"   {os.path.basename(abs_path)}: el repaso final se quedó pendiente al apagar; se retoma")
                    self._queue_final_pass(rel, os.path.basename(abs_path), None,
                                           local_part=self._pending().local_part(rel))
                else:
                    log(f"   {os.path.basename(abs_path)}: el repaso final pendiente ya no se puede hacer")
                    self._final_pass_over(rel, keep_live_text=True)
        except Exception:
            log_exc()

    def refresh_prompt(self, rel):
        """Cambio el tema, el idioma o el contexto de una reunion: si se esta transcribiendo en directo, live.run lo
        lee en el siguiente trozo (vocabulario, idioma y contexto libre)."""
        with self.lock:
            jobs = [j for j in self.list if j.path == rel and j.live and j.status == "running" and j.opts]
        for j in jobs:
            j.opts.update(meeting_setup(safe(rel), self.settings.get()))
            log(f"   directo {j.name}: vocabulario, idioma y contexto actualizados")

    def _live_job(self, job_id):
        with self.lock:
            job = next((j for j in self.list if j.id == job_id and j.live), None)
        if not job or job.status != "running":
            raise ApiError("Esa transcripción en directo ya no está en marcha.")
        return job

    def _check_live_preset(self, preset):
        if preset not in LIVE_PRESETS:
            raise ApiError("Modelo desconocido: " + str(preset))
        if engines.PRESETS_BY_ID[preset]["engine"] == "cloud":
            if procesado_de(self.settings.get()) != "api":
                raise ApiError(NUBE_OFF)
            self._cloud_ready()

    def set_live_preset(self, job_id, preset, restart=False):
        """Cambia el modelo del directo: desde el siguiente trozo, o rehaciendo todo desde el principio."""
        job = self._live_job(job_id)
        if preset:
            self._check_live_preset(preset)
            job.preset = preset
            if engines.PRESETS_BY_ID[preset]["engine"] != "cloud":
                job.local_part = True
                self._cloud_text().set(job.path, False)
            self.settings.update(lambda d: d.update(live_preset=preset))
        if restart:
            job.restart = True
            job.stage = "Rehaciendo desde el principio…"
        log(f"   directo {job.name}: {'rehacer con' if restart else 'modelo'} {job.preset}")
        return job.public()

    def _run_live_held(self, job, abs_path, st):
        try:
            self._run_live(job, abs_path, st)
        finally:
            self._release(job.path)

    def _run_live(self, job, abs_path, st):
        resume = []
        srt = os.path.splitext(abs_path)[0] + ".srt"
        if os.path.isfile(srt):  # la app se reinicio a mitad de grabacion: se retoma, no se repite
            try:
                resume = [(s["start"], s["end"], s["text"]) for s in parse_srt(read_text(srt))]
            except OSError:
                resume = []
        opts = {"preset": job.preset, **meeting_setup(abs_path, st),  # language, prompt (vocabulario) y about
                "threads": int(st.get("threads") or default_threads()),
                "worker": self.live_worker, "cloud": self.cloud, "cloud_cfg": live_cloud_cfg(st),
                # los ajustes de la nube vigentes en cada trozo (no una copia del arranque): una clave pegada, cambiada
                # o quitada en Ajustes vale desde el siguiente
                "cloud_cfg_fn": lambda: live_cloud_cfg(self.settings.get()),
                "tmp_dir": os.path.join(DATA_DIR, "tmp"), "resume": resume, "summarizer": self.summarizer}
        job.opts = opts  # live.run los lee en cada trozo: si cambias tema, idioma o contexto, vale desde el siguiente
        job.out = LiveOutputs(abs_path)
        if resume:  # lo que ya estaba escrito no se reescribe: lo nuevo se anade detras
            job.out.done, job.raw = list(resume), list(resume)
            job.out.sizes = {e: os.path.getsize(os.path.splitext(abs_path)[0] + e) for e in (".txt", ".srt")
                             if os.path.isfile(os.path.splitext(abs_path)[0] + e)}

        def writer(segs):
            with job.edit_lock:
                job.raw = list(segs)
                try:
                    job.out.write(apply_edits(job.raw, job.edits))
                except OSError as e:  # se reintenta con el siguiente trozo
                    log(f"   no se pudo escribir la transcripción todavía ({e.__class__.__name__})")

        try:
            segs, lang = live.run(job, abs_path, opts, writer, log)
            job.detected = lang
            err = getattr(job, "live_error", None)  # el ultimo trozo de la nube que no salio (live.run lo salta)
            if segs:
                writer(segs)
                if job.edits:  # lo que transcribio Whisper, para poder "Restaurar original" despues
                    save_original(job.path, segs)
                COMPLETE.set(job.path)  # llego hasta el final (si se corta antes, queda "a medias")
                if not self._final_pass(job):  # si hay repaso final, el resumen se hace al acabar este
                    self._after_transcribe(job.path)
                job.status, job.stage, job.progress = "done", "Hecho", 1.0
                log(f"✔ {job.name}  directo terminado ({job.recorded / 60:.0f} min grabados)")
            elif self._final_pass(job):  # sin texto del directo (p. ej. fallaron todos los trozos): lo hace el repaso
                job.status, job.progress = "done", 1.0
                job.stage = "Sin texto en directo: el repaso final transcribe la grabación entera"
                log(f"   {job.name}: el directo no dejó texto ({err or 'sin voz'}); lo transcribe el repaso final")
            else:
                job.status, job.stage = "error", "Error"
                job.error = (f"No se ha podido transcribir en directo ({err}). El audio se conserva: transcríbelo "
                             "desde la cola." if err else "No se ha detectado voz en la grabación.")
        except engines.Cancelled:
            job.status, job.stage = "cancelled", "Directo detenido"
            log(f"■ {job.name}  directo detenido")
        except Exception as e:
            job.status, job.stage, job.error = "error", "Error", str(e) or e.__class__.__name__
            log(f"✖ {job.name}  {job.error}")
            log_exc()
            # si la grabacion ya habia terminado, el repaso final (y tras el, los destinos y el resumen) se hace igual
            if not job.recording and self._final_pass(job):
                job.stage = "Error en el directo: el repaso final transcribe la grabación entera"
        finally:
            job.recording = False
            job.finished = time.time()
            self.library.at = 0

    def submit(self, paths, preset, language, resume=False):
        """resume: las que se quedaron a medias se continuan desde donde acaba el texto (el resto, enteras).
        language: "" = detectar, un codigo = ese idioma para todas, None = el de cada reunion (el de su .md o, si no
        lo fija, el del perfil; «Transcribir todos los pendientes»): lo decide _run con meeting_setup."""
        if preset not in engines.PRESETS_BY_ID:
            raise ApiError("Velocidad desconocida: " + str(preset))
        if engines.PRESETS_BY_ID[preset]["engine"] == "cloud":
            if procesado_de(self.settings.get()) != "api":
                raise ApiError(NUBE_OFF)
            self._cloud_ready()
        added = []
        for rel in paths:
            safe(rel, "file")
            if self.active_for(rel):
                continue
            a = self.library.find(rel)
            if a and a.get("recording"):
                raise ApiError("Se está grabando todavía: usa «Transcribir en directo».")
            job = Job(rel, os.path.basename(rel), preset, language or None)
            job.per_meeting = language is None
            if resume and a and a.get("partial"):
                job.resume_from = a["tx_end"]
            with self.lock:
                self.list.append(job)
            self.queues[job.engine].put(job)
            added.append(job.id)
        return added

    # --- resumen con IA: con la API de chat (procesado "api") o con el agente del PC (procesado "agente")
    def summary_missing(self):
        """Por que no se puede resumir ahora (None si se puede), segun donde se procesan las reuniones."""
        st = self.settings.get()
        mode = procesado_de(st)
        if mode == "local":
            return ("El resumen con IA está desactivado: en Ajustes → «Dónde se procesan tus reuniones» elige «Con tu "
                    "cuenta de Claude» o «API externa».")
        if mode == "agente":
            cls, _ = agente_de(st)
            if cls.find():
                return None
            return f"No encuentro {cls.label} en este PC: sigue el paso a paso de Ajustes y pulsa «Comprobar»."
        c = st["cloud"]
        if engines.chat_url(c) and (c.get("api_key") or "").strip():
            return None
        # con Azure OpenAI: que el resumen aun no funciona ahi (no "configura la nube")
        return (engines.chat_missing(c)
                or "El resumen necesita una API key de la nube (Ajustes ⚙ → Transcripción en la nube).")

    def summary_ready(self):
        return self.summary_missing() is None

    def _summarizer(self, st):
        """El Summarizer que toca: el de la API de chat o, con procesado "agente", uno con el agente del PC."""
        if procesado_de(st) != "agente":
            return self.summarizer
        cls, _ = agente_de(st)
        cur = getattr(self, "summarizer_agente", None)
        if cur is None or cur.agente.tipo != cls.tipo:
            cur = self.summarizer_agente = engines.Summarizer(agente=cls(settings_dir=DATA_DIR))
        return cur

    def submit_summary(self, rel, auto=False):
        abs_path = safe(rel, "file")
        missing = self.summary_missing()
        if missing:
            raise ApiError(missing)
        if not any(os.path.isfile(os.path.splitext(abs_path)[0] + e) for e in (".srt", ".txt")):
            raise ApiError("Primero hay que transcribir la reunión.")
        with self.lock:
            if any(j.path == rel and j.kind == "summary" and j.status in ("queued", "running") for j in self.list):
                raise ApiError("Ya se está resumiendo.")
            job = Job(rel, os.path.basename(rel), "resumen", None)
            job.stage = "En cola para resumir"
            self.list.append(job)
        self.queues["resumen"].put(job)
        log(f"✨ {job.name}  resumen{' (automático)' if auto else ''} en cola")
        return job.id

    def _cloud_text(self):
        """CloudText del DATA_DIR vigente, uno solo aunque lo pidan dos hilos a la vez (con dos, uno podria volver a
        escribir en disco un «en la nube» que el otro ya habia quitado)."""
        path = os.path.join(DATA_DIR, "transcritas_en_la_nube.json")
        with CLOUD_TEXT_LOCK:
            reg = getattr(self, "_cloud_reg", None)
            if reg is None or reg.path != path:
                reg = self._cloud_reg = CloudText(path)
        return reg

    def _tx_in_cloud(self, rel):
        """¿El texto que tiene ahora esa reunion se transcribio entero en la nube? Lo dice CloudText, que apunta cada
        transcripcion al escribir; sin dato, no."""
        return rel in self._cloud_text()

    def auto_summary(self, rel):
        """Al terminar de transcribir: resumen automatico si es una reunion reciente (las antiguas se
        resumen a mano, para no gastar de golpe el cupo diario de Groq). El resumen manda el texto, las notas y el
        contexto al proveedor de la nube: si el texto no se transcribio entero en la nube (_tx_in_cloud), solo con el
        consentimiento de Ajustes (summary_auto_local). A mano, siempre. Con el agente del PC (procesado "agente") el
        consentimiento es haber elegido esa opcion, con su aviso: todo lo transcrito en local se resume."""
        st = self.settings.get()
        if not st.get("summary_auto", True) or not self.summary_ready():
            return
        mode = procesado_de(st)
        a = self.library.find(rel)
        when = None
        if a and a.get("date"):
            try:
                when = time.mktime(time.strptime(a["date"], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                when = None
        when = when or (a or {}).get("mtime") or 0
        if time.time() - when > 3 * 86400:
            return
        if mode != "agente" and not self._tx_in_cloud(rel) and not st.get("summary_auto_local"):
            log(f"   {os.path.basename(rel)}: transcrita en local, sin resumen automático (no sale del PC; se puede "
                "resumir a mano)")
            return
        try:
            self.submit_summary(rel, auto=True)
        except ApiError:
            pass

    def _run_summary(self, job):
        job.status, job.started, job.stage = "running", time.time(), "Preparando el resumen…"
        try:
            missing = self.summary_missing()  # por si Ajustes cambio mientras esperaba en la cola
            if missing:
                raise engines.EngineError(missing)
            abs_path = safe(job.path, "file")
            base = os.path.splitext(abs_path)[0]
            data = Transcripts().load(base)
            if not data or not data["segments"]:
                raise engines.EngineError("No hay transcripción que resumir.")
            a = self.library.find(job.path) or {}
            props, notes = ({}, "")
            if os.path.isfile(base + ".md"):
                props, notes = parse_notes(read_text(base + ".md"))
            st = self.settings.get()
            d = a.get("duration") or 0
            meta = {"title": props.get("titulo") or os.path.splitext(job.name)[0],
                    "date": (a.get("date") or "").replace("T", " ")[:16] or "fecha desconocida",
                    "duration": f"{round(d / 60)} min" if d else "duración desconocida",
                    # los sugeridos por Outlook/Teams no salen del PC hasta que el usuario los confirma o edita
                    "participants": participantes_confirmados(props), "notes": notes,
                    "extra": st.get("summary_extra", ""), "about": props.get("contexto", "")}
            segments = self._with_speakers(abs_path, data["segments"], meta)
            cfg = dict(st["cloud"], summary_model=st.get("summary_model"), summary_lang=st.get("summary_lang"),
                       agente_modelo=agente_de(st)[1]["modelo"])

            def report(frac=None, stage=None, **k):
                if stage:
                    job.stage = stage
                if frac is not None:
                    job.progress = max(job.progress, frac)

            text, model = self._summarizer(st).summarize(segments, meta, cfg, report, lambda: job.cancel)
            head = f"---\nmodelo: {model}\ngenerado: {time.strftime('%Y-%m-%d %H:%M')}\n---\n\n"
            save_text(base + ".resumen.md", head + text.strip() + "\n", "utf-8")
            job.status, job.progress, job.stage = "done", 1.0, "Resumen listo"
            log(f"✔ {job.name}  resumen listo ({(time.time() - job.started):.0f} s, {model})")
            self.write_outputs(job.path)  # destinos de salida: el documento con el resumen
        except engines.Cancelled:
            job.status, job.stage = "cancelled", "Cancelado"
        except Exception as e:
            job.status, job.stage, job.error = "error", "Error", str(e) or e.__class__.__name__
            log(f"✖ {job.name}  resumen: {job.error}")
            if not isinstance(e, (engines.EngineError, ApiError)):
                log_exc()
        finally:
            job.finished = time.time()
            self.library.at = 0

    def _cloud_ready(self):
        """La nube se puede usar ahora: elegida en Ajustes («API externa») y con su endpoint. Lo comprueban tambien
        los trabajos al empezar (_run, _run_summary): lo que entro en cola con la nube no sale si se apago entre
        tanto."""
        st = self.settings.get()
        if procesado_de(st) != "api":
            raise ApiError(NUBE_OFF)
        try:
            engines.CloudEngine.endpoint(st["cloud"])
            return True
        except engines.EngineError as e:
            raise ApiError(str(e))

    def nube_apagada(self):
        """Ajustes deja de ser «API externa»: lo que estuviera en la nube no sigue. Los directos en nube pasan a local
        desde el siguiente trozo (como set_live_preset) y los trabajos de nube y los resumenes en cola se cancelan."""
        cambiados = []
        with self.lock:
            for j in self.list:
                en_nube = (engines.PRESETS_BY_ID.get(j.preset) or {}).get("engine") == "cloud"
                if j.status == "running" and j.live and en_nube:
                    j.preset, j.local_part = "medium", True
                    cambiados.append(j)
                elif j.status == "queued" and (j.engine == "cloud" or j.kind == "summary"):
                    j.status, j.stage = "cancelled", "Cancelado: la nube se ha desactivado en Ajustes"
                    j.finished = time.time()
        for j in cambiados:
            self._cloud_text().set(j.path, False)
            log(f"   directo {j.name}: modelo medium (la nube se ha desactivado en Ajustes)")
        return len(cambiados)

    # --- quien habla (voces): subproceso de prioridad baja al terminar cada transcripcion
    def voices_on(self):
        return voices.enabled(self.settings.get())

    def _after_transcribe(self, rel):
        """Transcripcion terminada: primero las voces (si estan activas) y el resumen al acabar estas, para que
        lleve los nombres; si no, el resumen directamente. Antes, el documento en los destinos de salida."""
        self.write_outputs(rel)
        if not self.auto_voices(rel):
            self.auto_summary(rel)

    # --- destinos de salida (salidas.py): la reunion como documento en otras carpetas
    def document_for(self, rel):
        """Lo que va al documento: titulo, fecha, duracion, propiedades, resumen (sin su cabecera), notas y
        transcripcion (con quien habla, si se sabe)."""
        abs_path = safe(rel, "file")
        base = os.path.splitext(abs_path)[0]
        props, notes = parse_notes(read_text(base + ".md")) if os.path.isfile(base + ".md") else ({}, "")
        # participantes sugeridos por Outlook/Teams sin confirmar: el destino (quiza sincronizado) no los lleva
        props = dict(props, participantes=participantes_confirmados(props))
        props.pop("participantes_origen", None)
        summary, model = "", None
        if os.path.isfile(base + ".resumen.md"):
            text = read_text(base + ".resumen.md")
            m = re.match(r"---\n(.*?)\n---\n", text, re.S)  # cabecera modelo/generado del .resumen.md
            if m:
                model = next((l.partition(":")[2].strip() for l in m.group(1).splitlines()
                              if l.startswith("modelo:")), None)
                text = text[m.end():]
            summary = text.strip()
        data = Transcripts().load(base)
        segments = data["segments"] if data else []
        if segments:
            try:  # con quien habla, si se sabe (no es imprescindible para el documento)
                segments = self._with_speakers(abs_path, segments, {})
            except Exception:
                log_exc()
        a = self.library.find(rel) or {}
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        when = (a.get("date") or "").replace("T", " ")[:16]
        if not when:
            m = DATE_RE.search(stem)
            when = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}" if m else ""
        d = a.get("duration") or 0
        return {"stem": stem, "title": props.get("titulo") or stem, "date": when,
                "duration": f"{round(d / 60)} min" if d else "", "props": props, "source": rel, "summary": summary,
                "summary_model": model, "notes": notes, "segments": segments, "app_name": APP_NAME}

    @property
    def registro(self):
        """salidas.json: que documento de cada destino es de que reunion (y su hash). Se abre al primer uso."""
        if getattr(self, "_registro", None) is None:
            self._registro = salidas.Registro(os.path.join(DATA_DIR, "salidas.json"))
        return self._registro

    def write_outputs(self, rel):
        """Escribe la reunion en los destinos de salida del perfil (si hay y la reunion ya tiene transcripcion).
        Nunca mueve ni toca el original; solo sobrescribe los documentos que genero la propia app (salidas.py). En una
        carpeta de la app (su raiz o una vinculada, donde viven las grabaciones) no se escribe nunca: un destino
        guardado antes de que eso se comprobara, o que haya quedado dentro al vincular una carpeta, solo da un aviso."""
        try:
            destinos = perfil_de(self.settings.get())["outputs"]
            if not destinos:
                return []
            base = os.path.splitext(safe(rel, "file"))[0]
            if not any(os.path.isfile(base + e) for e in (".srt", ".txt")):
                return []
            done = salidas.write_all(self.document_for(rel), destinos, self.registro, log, blocked=in_app_tree)
            self._note_outputs(rel, done)
            return done
        except Exception:
            log_exc()
            return []

    def _note_outputs(self, rel, done):
        """Lo que no se ha podido escribir queda como aviso para Ajustes (GET /api/perfil): los de esta reunion se
        sustituyen por los de esta escritura (que cubre todos los destinos), y se guardan como mucho 30."""
        with _SALIDAS_LOCK:
            avisos = getattr(self, "_salidas_avisos", None)
            if avisos is None:
                avisos = self._salidas_avisos = {}
            for key in [k for k, a in avisos.items() if a["reunion"] == rel]:
                del avisos[key]
            for path, accion in done:
                if accion not in ("escrito", "igual"):
                    avisos[os.path.normcase(path)] = {"path": path, "accion": accion, "reunion": rel, "at": time.time()}
            for key in sorted(avisos, key=lambda k: avisos[k]["at"])[:-30]:
                del avisos[key]

    def outputs_warnings(self):
        """Avisos de los destinos de salida, el mas reciente primero (solo de los destinos que siguen en el perfil)."""
        destinos = perfil_de(self.settings.get())["outputs"]
        with _SALIDAS_LOCK:
            avisos = list((getattr(self, "_salidas_avisos", None) or {}).values())
        avisos = [a for a in avisos if any(salidas.inside(a["path"], d["path"]) for d in destinos)]
        return sorted(avisos, key=lambda a: -a["at"])

    def flush_outputs(self, budget=15.0):
        """Al apagar: las reescrituras que esperaban su temporizador (schedule_outputs, 4 s) se hacen ya, y se espera a
        las que estan en marcha; si no, el ultimo cambio de las notas o de la transcripcion no llegaria a los destinos.
        Con un tope de `budget` segundos en total. Devuelve las reuniones escritas."""
        with _SALIDAS_LOCK:
            timers = getattr(self, "_salidas_timers", None) or {}
            pending = list(timers.items())
            timers.clear()
        t0, done = time.time(), []
        for rel, t in pending:
            t.cancel()
            if time.time() - t0 > budget:
                log(f"   destinos de salida: no ha dado tiempo a escribir {rel} al apagar")
                continue
            self.write_outputs(rel)
            done.append(rel)
        for t in threading.enumerate():  # las que ya vencieron y estan escribiendo
            if getattr(t, "salidas", False) and t is not threading.current_thread():
                t.join(max(0.0, budget - (time.time() - t0)))
        return done

    def outputs_moved(self, old, new, folder=False):
        """Una reunion (o una carpeta entera) cambia de ruta: sus documentos siguen siendo suyos en el registro y la
        reescritura que estuviera pendiente se hace con la ruta nueva."""
        try:
            self.registro.moved(old, new, folder)
        except OSError:
            log_exc()
        with _SALIDAS_LOCK:
            timers = getattr(self, "_salidas_timers", None) or {}
            moved = [(r, m) for r in list(timers) for m in [moved_rel(r, old, new, folder)] if m is not None]
            for r, _ in moved:
                timers.pop(r).cancel()
        for _, m in moved:
            self.schedule_outputs(m)

    def forget_outputs(self, rel):
        """La reunion se ha borrado: sus documentos de los destinos del perfil que siguen tal cual se escribieron van
        a la papelera; los que se cambiaron alli (o los de una carpeta que ya no es destino) se quedan. Devuelve cuantos
        se han mandado a la papelera."""
        with _SALIDAS_LOCK:
            t = (getattr(self, "_salidas_timers", None) or {}).pop(rel, None)
        if t:
            t.cancel()
        try:
            entries = self.registro.entries(rel)
        except OSError:
            return 0
        if not entries:
            return 0
        destinos = perfil_de(self.settings.get())["outputs"]
        intact = []
        for path, sha in entries:
            try:
                if any(salidas.inside(path, d["path"]) for d in destinos) and os.path.isfile(path) \
                        and salidas.sha256_of(path) == sha:
                    intact.append(path)
            except OSError:
                pass
        n = 0
        if intact:
            try:
                to_recycle_bin(intact)
                n = len(intact)
            except (ApiError, OSError) as e:
                log(f"   no se pudieron mandar a la papelera los documentos de {rel}: {getattr(e, 'msg', e)}")
                intact = []
        self.registro.forget([p for p, _ in entries])  # la reunion ya no existe: ninguno es de nadie
        return n

    def outputs_for_persona(self, pid=None):
        """Quien habla cambia de nombre (o se fusiona u olvida): los documentos de las reuniones donde sale (con None,
        las de cualquier persona reconocida) se reescriben, que llevan los nombres."""
        if not perfil_de(self.settings.get())["outputs"]:
            return 0
        self.library.refresh(max_age=5)
        n = 0
        for a in list(self.library.flat):
            data = voices.read_sidecar(a["_spk"]) if a.get("_spk") else None
            if data and any(i.get("persona") and (pid is None or i.get("persona") == pid)
                            for i in (data.get("hablantes") or {}).values()):
                self.schedule_outputs(a["path"])
                n += 1
        return n

    def schedule_outputs(self, rel, delay=4.0):
        """Reescritura al guardar cambios en las notas o la transcripcion: agrupada (se guarda mientras se escribe)."""
        if not perfil_de(self.settings.get())["outputs"]:
            return
        if delay <= 0:
            return self.write_outputs(rel)
        with _SALIDAS_LOCK:
            timers = getattr(self, "_salidas_timers", None)
            if timers is None:
                timers = self._salidas_timers = {}
            old = timers.pop(rel, None)
            if old:
                old.cancel()
            t = threading.Timer(delay, lambda: self._fire_outputs(rel, t))
            t.daemon, t.salidas = True, True  # flush_outputs las encuentra al apagar
            timers[rel] = t
        t.start()

    def _fire_outputs(self, rel, timer):
        """Vence la espera: se quita de la lista solo si sigue siendo el temporizador vigente (si entretanto se guardo
        otra vez, el nuevo ya ocupa su sitio y no hay que perderlo) y escribe."""
        with _SALIDAS_LOCK:
            timers = getattr(self, "_salidas_timers", {})
            if timers.get(rel) is timer:
                timers.pop(rel, None)
        self.write_outputs(rel)

    def auto_voices(self, rel):
        if not self.voices_on():
            return False
        with self.lock:
            cur = next((j for j in self.list if j.path == rel and j.kind == "voces"
                        and j.status in ("queued", "running")), None)
        if cur:
            cur.then_summary = True
            return True
        try:
            self.submit_voices(rel, auto=True, then_summary=True)
            return True
        except ApiError as e:
            log(f"   voces: {e.msg}")
            return False

    def submit_voices(self, rel, auto=False, then_summary=False):
        safe(rel, "file")
        if not self.voices_on():
            raise ApiError("Activa «Voces» en Ajustes ⚙ para identificar quién habla.")
        ok, why = voices.available()
        if not ok:
            raise ApiError("La librería de voces (sherpa-onnx) no está instalada o no carga: vuelve a ejecutar "
                           f"INSTALAR.bat ({why[:160]})")
        a = self.library.find(rel)
        if a and a.get("recording"):
            raise ApiError("Se está grabando todavía: las voces se identifican al terminar.")
        with self.lock:
            if any(j.path == rel and j.kind == "voces" and j.status in ("queued", "running") for j in self.list):
                raise ApiError("Ya se están identificando las voces de esta reunión.")
            job = Job(rel, os.path.basename(rel), "voces", None)
            job.stage, job.then_summary = "En cola para identificar voces", then_summary
            self.list.append(job)
        self.queues["voces"].put(job)
        log(f"🗣 {job.name}  voces{' (automático)' if auto else ''} en cola")
        return job.id

    def _voices_subprocess(self, job, args, idle_timeout=1800):
        """python voices.py … con prioridad baja; lee su progreso; cancelar = matar el proceso."""
        log_path = os.path.join(DATA_DIR, "voces.log")
        try:  # rotacion sencilla: el stderr de onnxruntime no debe crecer sin tope
            if os.path.getsize(log_path) > 1_000_000:
                os.replace(log_path, log_path + ".1")
        except OSError:
            pass
        err = open(log_path, "ab")
        p = subprocess.Popen([sys.executable, "-u", os.path.join(APP_DIR, "voices.py"), *args, "--data", DATA_DIR,
                              "--threads", "2"], stdout=subprocess.PIPE, stderr=err, cwd=APP_DIR,
                             creationflags=engines.NO_WINDOW | (voices.IDLE_PRIORITY or engines.BELOW_NORMAL))
        err.close()
        q = queue.Queue()

        def reader():
            for line in p.stdout:
                try:
                    q.put(json.loads(line))
                except ValueError:
                    pass
            q.put(None)
        threading.Thread(target=reader, daemon=True).start()
        last = time.time()
        try:
            while True:
                if job and job.cancel:
                    raise engines.Cancelled()
                try:
                    m = q.get(timeout=0.25)
                except queue.Empty:
                    if time.time() - last > idle_timeout:
                        raise engines.EngineError("La identificación de voces no dio señales de vida en "
                                                  f"{idle_timeout // 60} min; se ha cortado.")
                    continue
                last = time.time()
                if m is None:
                    raise engines.EngineError("El proceso de voces se cerró de golpe "
                                              "(detalles en %LOCALAPPDATA%\\AudiosApp\\voces.log).")
                if m.get("type") == "progress":
                    if job:
                        if m.get("stage"):
                            job.stage = m["stage"]
                        if m.get("frac") is not None:
                            job.progress = max(job.progress, min(float(m["frac"]), 0.99))
                elif m.get("type") == "error":
                    raise engines.EngineError(m.get("error") or "error al identificar voces")
                elif m.get("type") == "result":
                    return m
        finally:
            if p.poll() is None:
                p.kill()
            try:
                p.wait(timeout=5)
            except Exception:
                pass

    def speaker_vector(self, rel, h):
        """Huella de un hablante de una reunion: de la memoria del servidor o recalculada en un subproceso."""
        vec = (self.voice_vectors.get(rel) or {}).get(h)
        if vec:
            return vec, None
        abs_path = safe(rel, "file")
        args = ["--fingerprint", h, "--diarize", abs_path, "--out", voices.sidecar_path(abs_path)]
        m = self._voices_subprocess(None, args, idle_timeout=600)
        vec = (m.get("huellas") or {}).get(h)
        if not vec:
            raise ApiError("No se pudo calcular la huella de ese hablante.")
        self.voice_vectors.setdefault(rel, {})[h] = vec
        return vec, m.get("segundos")

    def speakers_for(self, abs_path, data=None):
        """Sidecar con las frases casadas con el .srt ACTUAL (barato) y los nombres resueltos; None si no hay."""
        data = data or voices.read_sidecar(voices.sidecar_path(abs_path))
        if not data or not data.get("tramos"):
            return None
        segs = Transcripts().load(os.path.splitext(abs_path)[0])
        who = voices.refresh_frases(data, segs["segments"]) if segs and segs["kind"] == "srt" else []
        names = self.voices.names()
        uid = (self.voices.user() or {}).get("id")
        for h, info in data.get("hablantes", {}).items():
            if info.get("persona") and info["persona"] not in names:  # persona olvidada
                info["persona"], info["provisional"] = None, False
            info["nombre"] = names.get(info.get("persona")) if info.get("persona") else None
            info["label"] = voices.label(h, data, names, usuario=uid)
            info["usuario"] = voices.is_user(h, data, names, usuario=uid)
        data["_who"], data["_usuario"] = who, uid
        return data

    def _with_speakers(self, abs_path, segments, meta):
        """Para el resumen: cada frase con quien la dice (nombre, «Yo» o «Persona N») si hay diarizacion."""
        data = self.speakers_for(abs_path)
        if not data or not data.get("_who"):
            return segments
        names = self.voices.names()
        out = [dict(s, who=voices.label(w, data, names, for_llm=True, usuario=data.get("_usuario")))
               for s, w in zip(segments, data["_who"])]
        meta["speakers"] = True
        known = [i["nombre"] for i in data["hablantes"].values()
                 if i.get("nombre") and not i.get("provisional") and not i.get("breve")]
        if known and not meta.get("participants"):
            meta["participants"] = ", ".join(dict.fromkeys(known))
        return out

    def _suggest_participants(self, abs_path, data):
        """Propiedad «Participantes» con los nombres reconocidos, solo si esta vacia (no se pisa lo escrito a mano)."""
        names = self.voices.names()
        known = [names[i["persona"]] for i in data.get("hablantes", {}).values()
                 if i.get("persona") in names and not i.get("provisional") and not i.get("breve")]
        if not known:
            return
        md = os.path.splitext(abs_path)[0] + ".md"
        props, body = parse_notes(read_text(md)) if os.path.isfile(md) else ({}, "")
        if props.get("participantes"):
            return
        props["participantes"] = ", ".join(dict.fromkeys(known))
        save_text(md, dump_notes(props, body), "utf-8")

    def _run_voices(self, job):
        job.status, job.started, job.stage = "running", time.time(), "Preparando…"
        restore = None  # (sidecar, contenido anterior): si algo falla tras rediarizar, se deja como estaba
        try:
            abs_path = safe(job.path, "file")

            def report(stage=None, frac=None):
                if stage:
                    job.stage = stage
                if frac is not None:
                    job.progress = max(job.progress, min(float(frac), 0.99))
            if not voices.models_ready(DATA_DIR):
                try:
                    voices.ensure_models(DATA_DIR, report, lambda: job.cancel)
                except voices.VoicesError as e:
                    if job.cancel:
                        raise engines.Cancelled()
                    raise engines.EngineError(str(e))
            side = voices.sidecar_path(abs_path)
            old = voices.read_sidecar(side) or {}  # lo que el usuario ya dijo de esta reunion (se conserva)
            restore = (side, json.loads(json.dumps(old))) if old else None
            uid = (self.voices.user() or {}).get("id")
            sin_huellas = {p["id"] for p in self.voices.personas() if not p.get("huellas")}
            for i in (old.get("hablantes") or {}).values():  # sidecar de antes del 07/11/2026, sin «origen»: una
                if not i.get("origen") and i.get("persona") in sin_huellas - {uid}:  # persona sin huellas solo
                    i["origen"] = "manual"  # pudo ponerse a mano
            # el subproceso reescribe el sidecar con hablantes nuevos (h1, h2… otra vez): las huellas en memoria de la
            # identificacion anterior ya no son de esos hablantes y «Recordar» guardaria la voz de otro
            self.voice_vectors.pop(job.path, None)
            res = self._voices_subprocess(job, ["--diarize", abs_path, "--out", side])
            huellas = res.get("huellas") or {}
            data = voices.read_sidecar(side)
            if not data or not data.get("tramos"):
                raise engines.EngineError("No se ha detectado voz en la grabación.")
            voices.identify(data, huellas, self.voices)
            voices.carry_manual(old, data, usuario=uid, known=self.voices.names())
            srt = Transcripts().load(os.path.splitext(abs_path)[0])
            if srt and srt["kind"] == "srt":
                voices.refresh_frases(data, srt["segments"])
            voices.write_sidecar(side, data)
            restore = None
            self.voice_vectors[job.path] = huellas
            try:
                self._suggest_participants(abs_path, data)
            except OSError:
                log_exc()
            real = [i for i in data["hablantes"].values() if not i.get("breve")]
            known = sum(1 for i in real if i.get("persona") and not i.get("provisional"))
            job.status, job.progress = "done", 1.0
            job.stage = (f"Voces: {len(real)} {'persona' if len(real) == 1 else 'personas'} · {known} "
                         f"reconocida{'s' if known != 1 else ''}")
            log(f"✔ {job.name}  {job.stage.lower()} ({time.time() - job.started:.0f} s)")
            self.schedule_outputs(job.path)  # destinos de salida: el documento con quien habla (y participantes)
        except engines.Cancelled:
            job.status, job.stage = "cancelled", "Cancelado"
            log(f"■ {job.name}  voces canceladas")
        except Exception as e:
            job.status, job.stage, job.error = "error", "Error", str(e) or e.__class__.__name__
            log(f"✖ {job.name}  voces: {job.error}")
            if not isinstance(e, (engines.EngineError, ApiError, voices.VoicesError)):
                log_exc()
        finally:
            job.finished = time.time()
            if restore:  # el subproceso pudo dejar un sidecar nuevo sin lo que el usuario ya habia dicho
                try:
                    voices.write_sidecar(*restore)
                except OSError:
                    log_exc()
            self.library.at = 0
            if job.then_summary:
                self.auto_summary(job.path)

    def cancel(self, job_id):
        queued_pass = None
        with self.lock:
            for j in self.list:
                if j.id == job_id and j.status in ("queued", "running"):
                    j.cancel = True
                    if j.status == "queued":
                        j.status, j.stage, j.finished = "cancelled", "Cancelado", time.time()
                        queued_pass = j.path if getattr(j, "final_pass", False) else None
                    else:
                        j.stage = "Cancelando…"
                    break
            else:
                return False
        if queued_pass:  # el repaso final en cola: se queda el texto del directo (los destinos y el resumen, con el)
            threading.Thread(target=self._final_pass_over, args=(queued_pass, True), daemon=True,
                             name="repaso-cancelado").start()
        return True

    def clear(self):
        with self.lock:
            self.list = [j for j in self.list if j.status in ("queued", "running")]

    def snapshot(self):
        with self.lock:
            return [j.public() for j in self.list[-60:]]

    def _worker(self, kind):
        q = self.queues[kind]
        while True:
            job = q.get()
            if job.status != "queued":
                continue
            self._hold(job.path)  # hasta que termine con lo que encadene (voces, resumen): la reunion esta ocupada
            try:
                if job.kind == "summary":
                    self._run_summary(job)
                elif job.kind == "voces":
                    self._run_voices(job)
                else:
                    self._run(job)
            except Exception as e:
                log_exc()
                job.status, job.stage, job.error = "error", "Error", str(e) or e.__class__.__name__
                job.finished = job.finished or time.time()
            finally:
                self._release(job.path)

    def _run(self, job):
        job.status, job.started, job.stage = "running", time.time(), "Empezando…"
        preset = engines.PRESETS_BY_ID[job.preset]
        st = self.settings.get()
        log(f"▶ {job.name}  [{preset['label']}]")

        def report(frac=None, stage=None, transcribing=False):
            if transcribing and job.t_transcribe is None:
                job.t_transcribe = time.time()
            if stage is not None:
                job.stage = ("Repaso final · " if job.final_pass else "") + stage
            if frac is not None:
                job.progress = max(job.progress, frac)
                if job.t_transcribe and job.progress > 0.03:
                    el = time.time() - job.t_transcribe
                    base = 0.12 if job.engine == "cloud" else 0.0
                    done = (job.progress - base) / (1 - base)
                    if done > 0.02:
                        job.eta = round(el * (1 - done) / done)

        try:
            abs_path = safe(job.path, "file")
            a = self.library.find(job.path)
            start = float(job.resume_from or 0)
            total = (a or {}).get("duration") or engines.media_duration(abs_path)
            job.duration = max(1.0, total - start) if total else None  # lo que queda por transcribir
            if start:
                log(f"   continuando desde {start / 60:.1f} min")
            ms = meeting_setup(abs_path, st)  # vocabulario del tema (o el general), contexto libre e idioma del .md
            vocab, about = ms["prompt"], ms["about"]
            # el repaso final usa el idioma que tenga la reunion al terminar (pudo cambiarse); en lote, «el de cada
            # reunion»: el de su .md o el del perfil
            if job.final_pass or getattr(job, "per_meeting", False):
                job.language = ms["language"] or None
            if preset["engine"] == "cloud":
                self._cloud_ready()  # ApiError si la nube se apago en Ajustes mientras esperaba en la cola
            if preset["engine"] == "local":
                segs, lang, dur = self.local.transcribe(abs_path, preset, job.language, vocab,
                                                        int(st.get("threads") or default_threads()),
                                                        report, lambda: job.cancel, start=start, about=about)
            else:
                segs, lang, dur = self.cloud.transcribe(abs_path, job.duration, st["cloud"], job.language,
                                                        vocab, job.id, report, lambda: job.cancel,
                                                        start=start, about=about)
            if job.cancel:
                raise engines.Cancelled()
            if start:
                # se anade detras de lo que ya habia (incluidas las correcciones a mano, que se respetan)
                base = os.path.splitext(abs_path)[0]
                old = [(s["start"], s["end"], s["text"]) for s in parse_srt(read_text(base + ".srt"))]
                new = [s for s in segs if s[1] > start + 0.3]
                write_outputs(abs_path, old + new)
                if not new:
                    log("   en lo que faltaba no hay voz: se da por completa")
            else:
                if not segs:
                    raise engines.EngineError("No se ha detectado voz en el audio.")
                write_outputs(abs_path, segs)
                shutil.rmtree(backup_dir(job.path), ignore_errors=True)  # el "original" es ahora este
            # en la nube solo si lo fue este trabajo y, al continuar una a medias, tambien lo de antes
            self._cloud_text().set(job.path, job.engine == "cloud" and (not start or job.path in self._cloud_text()))
            COMPLETE.set(job.path)  # aunque el texto acabe antes que el audio, esta completa
            if job.final_pass:
                self._final_pass_over(job.path)
            self._after_transcribe(job.path)
            job.duration, job.detected = dur or job.duration, lang
            job.status, job.progress, job.stage, job.eta = "done", 1.0, "Hecho", 0
            el = time.time() - (job.t_transcribe or job.started)
            if job.duration and el > 1:
                speed = job.duration / el

                def upd(d):
                    old = d.setdefault("speed", {}).get(job.preset)
                    d["speed"][job.preset] = round(speed if not old else 0.5 * old + 0.5 * speed, 2)
                self.settings.update(upd)
            log(f"✔ {job.name}  {(time.time() - job.started) / 60:.1f} min"
                + (f"  ({job.duration / max(el, 1):.1f}x tiempo real)" if job.duration else ""))
        except engines.Cancelled:
            job.status, job.stage = "cancelled", "Cancelado"
            log(f"■ {job.name}  cancelado")
            if job.final_pass:
                if getattr(self, "closing", False):  # al apagar: sigue pendiente y se retoma al volver a abrir
                    log("   el repaso final queda pendiente para el próximo arranque")
                else:  # cancelado a mano: se queda el texto del directo, y con el los destinos y el resumen
                    self._final_pass_over(job.path, keep_live_text=True)
        except Exception as e:
            job.status, job.stage, job.error = "error", "Error", str(e) or e.__class__.__name__
            log(f"✖ {job.name}  {job.error}")
            if not isinstance(e, (engines.EngineError, ApiError)):
                log_exc()
            if job.final_pass:  # se queda el texto del directo, y con el se resume
                self._final_pass_over(job.path)
                log("   se mantiene el texto del directo")
                self.write_outputs(job.path)
                self.auto_summary(job.path)
        finally:
            job.finished = time.time()
            self.library.at = 0


# ----------------------------------------------------------------------------- API


def masked(key):
    return (key[:4] + "…" + key[-4:]) if key and len(key) > 12 else ("••••" if key else "")


KEY_LIKE = re.compile(r"\b(?:gsk|sk)[-_][A-Za-z0-9_-]{12,}")  # Groq (gsk_...) y OpenAI (sk-...)


def scrub(text, keys=()):
    """Enmascara claves en un texto: las conocidas (la API key y las adicionales) y cualquier token con pinta de
    API key. Para el diagnostico que se copia desde Acerca de."""
    for k in sorted({k for k in keys if k}, key=len, reverse=True):
        text = text.replace(k, masked(k))
    return KEY_LIKE.sub(lambda m: masked(m.group(0)), text)


# ----------------------------------------------------------------------------- diagnostico: lista blanca
# «Copiar diagnostico» se pega donde se pide ayuda, a veces en un sitio publico. Lleva solo lo que se sabe tecnico:
# version, Python y sistema, motor y modelo, contadores y, de las ultimas lineas de server.log, lo fijo de los mensajes
# que escribe la propia app (DIAG_LINES) con sus cifras, modelos y codigos de error. Todo lo demas (nombres de
# reuniones, carpetas, proyectos, temas y personas, rutas, el texto de un error, lo que no se reconoce) va como
# «<texto>». No depende de lo que la app sepa de la biblioteca: lo ya borrado, renombrado u olvidado tampoco sale.
DIAG_FREE = "<texto>"
# Cada plantilla es un mensaje de log() con sus huecos. {t}: texto libre (siempre «<texto>»); {b}: lo mismo, dentro de
# unos corchetes; {e}: un error («<texto>» y sus codigos); {n}: una cifra; {m}: un modelo o una velocidad conocidos
# (DIAG_MODELS); {c}: una excepcion conocida (diag_exceptions); {k}: nombres de ajustes; {l}: un idioma; {a}: la app de
# la llamada; {u}: la URL local; {v}: una version; {y}: el .py de una traza (solo su nombre); {f}: la funcion de una
# traza. ⟦…⟧ es opcional y lo demas, literal. Gana la primera que encaja (las concretas van antes que las generales);
# sin plantilla, la sangria, el icono y «<texto>» (diag_line).
DIAG_LINES = (
    # trabajos de la cola, del directo, del resumen y de las voces: «<icono> <reunion>  <que>»
    "▶ {t}  [{m}]",
    "● Grabando: {t}⟦ [{b} · {b}]⟧⟦ (solo grabar, sin transcribir)⟧",
    "■ Grabación parada: {t} ({n} min)",
    "● {t}  transcripción en directo⟦ (automática)⟧",
    "↻ {t}  repaso final de la grabación entera en la nube",
    "✔ {t}  directo terminado ({n} min grabados)",
    "■ {t}  directo detenido",
    "✨ {t}  resumen⟦ (automático)⟧ en cola",
    "✔ {t}  resumen listo ({n} s, {m})",
    "✖ {t}  resumen: {e}",
    "🗣 {t}  voces⟦ (automático)⟧ en cola",
    "✔ {t}  voces: {n} persona⟦s⟧ · {n} reconocida⟦s⟧ ({n} s)",
    "■ {t}  voces canceladas",
    "✖ {t}  voces: {e}",
    "✔ {t}  {n} min⟦  ({n}x tiempo real)⟧",
    "■ {t}  cancelado",
    "✖ modelos de voces: {e}",
    "✖ {t}  {e}",
    "   {t}: sin repaso final (tiene correcciones a mano)",
    "   directo {t}: vocabulario, idioma y contexto actualizados",
    "   directo {t}: rehacer con {m}",
    "   directo {t}: modelo {m}",
    "   {t}: transcrita en local, sin resumen automático (no sale del PC; se puede resumir a mano)",
    "   continuando desde {n} min",
    "   en lo que faltaba no hay voz: se da por completa",
    "   se mantiene el texto del directo",
    "   no se pudo escribir la transcripción todavía ({c})",
    "   voces: {e}",
    "   {t}: documento escrito en {n} destino(s)⟦ · sin tocar: {t}⟧",
    # repaso final pendiente al apagar (fix-directo) y destinos de salida al apagar o borrar (fix-perfil)
    "   {t}: el repaso final se quedó pendiente al apagar; se retoma",
    "   {t}: el repaso final pendiente ya no se puede hacer",
    "   repaso final pendiente de {t}: la reunión ya no está ahí, se olvida",
    "   el repaso final queda pendiente para el próximo arranque",
    "   {t}: el directo no dejó texto ({e}); lo transcribe el repaso final",
    "   destinos de salida: no ha dado tiempo a escribir {t} al apagar",
    "   no se pudieron mandar a la papelera los documentos de {t}: {e}",
    # directo (live.py)
    "   directo: idioma {l} desde el siguiente trozo",
    "   idioma: {l}⟦ ({n}%)⟧",
    "   rehaciendo desde el principio con {m}",
    "   se salta un trozo que Whisper no consigue transcribir",
    "   directo reanudado: vuelve a haber cupo",
    "   trozo sin transcribir ({e}); se sigue con el siguiente",
    "   traducción del directo: {e}",
    "   trozo de {n} s ({n} s de voz, {n} frases): texto a los {n} s{t}",
    "   {e} ({n} s de audio, intento {n})",
    "   directo: no se pudieron releer los ajustes de la nube ({c})",
    # agente de IA del PC (Claude Code)
    "   agente: {m} {v} comprobado ({n} s)",
    "   agente: {m}: {e}",
    # biblioteca, carpetas, vocabulario y voces
    "⤓ importado: {t}",
    "🗑 vocabulario a la papelera: {t}",
    "🗑 {t} → papelera ({n} archivos⟦ y {n} documentos de los destinos⟧)",
    "   tema de {n} reuniones: siguen a la carpeta renombrada {t}",
    "↪ {t}  →  {t}",
    "   no se pudo quitar la carpeta vacía {t}",
    "📁 {t}: se moverá a {t} al terminar",
    "📁 {t}: ya no se mueve (deshecho)",
    "   movimiento pendiente de {t}: la reunión ya no está ahí, se olvida",
    "   movimiento pendiente de {t}: la carpeta {t} ya no existe, se queda donde está",
    "   movimiento pendiente de {t}: {e} Se reintenta en {n} s",
    "   carpeta principal de {t}: era la de una reunión ({t}); pasa a {t}",
    "   carpeta principal de {t}⟦ (workstream)⟧: {t}",
    "↺ {t}: transcripción original restaurada",
    "🔗 carpeta vinculada: {t}",
    "🔗 carpeta desvinculada (no se ha tocado nada): {t}",
    "🔤 vocabulario guardado: {t} ({n} términos)",
    "🔤 vocabulario importado de {t}: {n} listas copiadas, {n} omitidas ({t})",
    "   perfil de reunión guardado: {k}",
    "🗣 huella guardada: {t} ({n} huella⟦s⟧)",
    "🗣 personas fusionadas ({n} reuniones actualizadas)",
    "🗣 todas las huellas olvidadas",
    "🗣 persona olvidada",
    "🗣 voces activadas",
    "🗣 voces desactivadas",
    "   modelos de voces descargados",
    "   voces.json no se pudo leer ({e}); se aparta como voces.json.ilegible",
    # arranque, ajustes, grabadora, llamadas y apagado
    "arranque · {u} · carpeta {t} · datos {t} · Python {v}⟦ · pid {n}⟧",
    "=" * 64,  # el recuadro de main() con la URL, la carpeta y como apagar
    f"  {APP_NAME.upper()}  ·  {{u}}",
    "  Carpeta: {t}",
    f"  Apagar: Ajustes > Apagar {APP_NAME} (con consola, también cerrándola o Ctrl+C).",
    "   sin DPAPI fuera de Windows: los secretos se guardan en claro (plain:base64)",
    "modelo {m} precargado para el directo en {n} s",
    "modelo {m} precargado para la cola en {n} s",
    "no se pudo precargar {m}: {e}",
    "   borrados los trozos de audio temporales que dejó la sesión anterior",
    "   no se pudo descifrar {k} de settings.json ({e}): vuelve a pegarla en Ajustes",
    "   no se pudo copiar settings.json ({c}): no se escribirá encima en esta sesión",
    "   settings.json no se puede leer ({e})⟦: copia en {t}⟧; recuperado: {t}; lo demás vuelve a los valores por "
    "defecto",
    "   aviso pendiente: el resumen automático ya no sube a la nube lo transcrito en local",
    "   no se pudo guardar {t} ({c})",
    "   micro silenciado",
    "   micro reactivado",
    "   reunión detectada ({a}): aviso flotante abierto",
    "   reunión detectada ({a}): aviso enviado",
    "   Outlook: GetDateFormatEx/GetTimeFormatEx han fallado (error {n}): el filtro va con el formato de EE. UU.",
    "apagar pedido desde la interfaz ({n} trabajos en cola o en marcha)",
    "apagar pedido desde la bandeja ({n} trabajos en cola o en marcha)",
    # icono en la bandeja (bandeja.py) y arrancar con Windows
    "icono en la bandeja (hwnd {n})",
    "icono en la bandeja (hwnd None)",
    "   sin icono en la bandeja ({c}): cerrar la ventana la deja en segundo plano como antes",
    "icono de la bandeja retirado",
    "   icono de la bandeja: el hilo de la ventana no contesta; se quita desde aquí",
    "   primer cierre con la X: aviso en el icono de la bandeja",
    "   salir desde la bandeja: está grabando, no se sale",
    "   aviso: {t}",
    "   salir desde la bandeja: cancelado (trabajos en marcha)",
    "arrancar con Windows: activado",
    "arrancar con Windows: desactivado",
    "arrancar con Windows: acceso directo actualizado",
    "   arrancar con Windows: {e}",
    "ventana oculta: la app sigue en segundo plano (el acceso directo la vuelve a abrir)",
    "apagando ({t})",
    "apagado",
    "la app ya estaba abierta: se ensena su ventana",
    # doble apertura al arrancar (fix-doble-apertura): la segunda copia, la marca de instancia y la ventana en camino
    "la app ya estaba abierta: se enseña su ventana · pid {n}",
    "otra copia tiene la marca y no contesta (arrancando): si se está abriendo, su ventana saldrá sola · pid {n}",
    "otra copia tiene la marca y no contesta (libre): si se está abriendo, su ventana saldrá sola · pid {n}",
    "otra copia tiene la marca y no contesta (otro): si se está abriendo, su ventana saldrá sola · pid {n}",
    "otra copia tiene la marca y no contesta (hermana): si se está abriendo, su ventana saldrá sola · pid {n}",
    "   aún se está abriendo: su ventana saldrá sola",
    "   la otra copia: pid {n}, desde hace {n} s",
    "   no contesta a /api/mostrar: se abre en Edge desde aquí",
    "   /api/mostrar: {e}",
    "   Edge se ha abierto hace {n} s: no se abre otro",
    "   marca de instancia: no se puede abrir ({e})",
    "   marca de instancia: no se puede bloquear ({e})",
    "   marca de instancia: sin carpeta de datos ({e})",
    "   la versión anterior se ha cerrado pero su marca de instancia sigue: se continúa sin ella",
    "   se ha vuelto a abrir mientras arrancaba: la ventana se trae al frente",
    "la ventana propia no se ha enseñado en {n} s: se abre en Edge",
    "ventana abierta (WebView2; hwnd {n})",
    "ventana abierta (WebView2; hwnd None)",
    "AppUserModelID {t}: fijado",
    "AppUserModelID {t}: no disponible",
    "la ventana se ha cerrado sin apagar: la app sigue en segundo plano",
    "no se pudo abrir la ventana propia (detalle arriba): se abre en Edge",
    "la ventana propia necesita WebView2 y pywebview usaría «{t}» (¿falta el runtime de WebView2?): se abre en Edge",
    "Windows pide cerrar la ventana (WindowsShutDown): se deja cerrar",  # fix-windows: apagar o cerrar sesion
    "Windows pide cerrar la ventana (TaskManagerClosing): se deja cerrar",
    "Windows cierra la sesión o se apaga: apagado limpio",
    "Windows ha pedido cerrar: apagado limpio",
    # sesion de la API (fix-seguridad): el metodo y la ruta pedidos sin token, como texto
    "   403 sin token de sesión (navegador): {t} (solo el primero)",
    "   403 sin token de sesión (programa): {t} (solo el primero)",
    "   sesión: cookie entregada a la interfaz",
    "   sesión: /?t= con un token que no es el de esta sesión (sin cookie)",
    "puerto {n} ocupado ({t}): {e}",
    "  Interfaz: {u}?t=<el contenido de {t}>",
    "(no hay server.log)",
    # trazas (log_exc): el .py, la linea y la funcion; de la excepcion, su tipo y sus codigos
    "Traceback (most recent call last):",
    '  File "{y}", line {n}, in {f}',
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
    "{c}: {e}",
    "{c}",
)
DIAG_MODELS = frozenset(
    {x for p in engines.PRESETS for x in (p["id"], p["label"], p.get("live_label"), p.get("model")) if x}
    | set(engines.SUMMARY_MODELS) | {m for v in engines.PROVIDERS.values() for m in v.get("models") or []}
    | {engines.LIVE_MODEL_DEFAULT, getattr(engines.Summarizer, "TRANSLATE_MODEL", None), "gpt-4o-mini"}
    | {c.label for c in engines.AGENTES.values()} - {None})
# codigos de error que se pueden ensenar: HTTP de la nube (engines: «HTTP 401: …»), errno y WinError, HRESULT de Windows
# (COM de Outlook, WebView2), los codigos de error de las APIs compatibles con OpenAI y, de las palabras (exc), solo las
# excepciones conocidas (diag_codes)
DIAG_CODES = re.compile(
    r"\bHTTP ?[1-5]\d\d\b|\[(?:Errno|WinError) -?\d+\]|\b0x[0-9A-Fa-f]{8}\b|(?<!\d)-2147\d{6}\b"
    r"|\b(?:rate_limit_exceeded|invalid_api_key|insufficient_quota|model_not_found|context_length_exceeded"
    r"|request_too_large|model_decommissioned|invalid_request_error|authentication_error|permission_denied)\b"
    r"|(?<![\w.])(?P<exc>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
# lo que puede ser cada hueco (el resto: .+?); {b}, {u} y {v} no pasan de sus separadores, para no probar en balde
_DIAG_INNER = {"n": r"\d+(?:[.,]\d+)?", "c": r"[A-Za-z_][A-Za-z0-9_.]*", "e": r".*?", "b": r"[^\[\]]+?", "u": r"\S+",
               "v": r"\S+"}
_DIAG_HEAD = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z] ")  # «AAAA-MM-DD HH:MM:SS I » de server.log
# una linea mas larga (el HTML de un error, p. ej.) no se compara con las plantillas: con varios huecos de texto libre,
# ver si encaja costaria segundos y bloquearia «Copiar diagnostico». Sale como una sin plantilla (diag_line)
DIAG_MAX = 2000


def _diag_rx(template):
    """La plantilla como expresion regular: los literales, escapados; cada hueco, un grupo con su tipo delante."""
    out = []
    for i, tok in enumerate(re.split(r"(\{[a-z]\}|⟦|⟧)", template)):
        if tok == "⟦":
            out.append("(?:")
        elif tok == "⟧":
            out.append(")?")
        elif re.fullmatch(r"\{[a-z]\}", tok):
            out.append(f"(?P<{tok[1]}{i}>{_DIAG_INNER.get(tok[1], '.+?')})")
        else:
            out.append(re.escape(tok))
    return re.compile("".join(out))


_DIAG_RX = tuple(_diag_rx(t) for t in DIAG_LINES)


def diag_exceptions():
    """Las excepciones que el diagnostico ensena, lista cerrada: las clases de excepcion que existen en este proceso
    (las de Python, las de la app y las de las librerias cargadas; las de requests aunque esta sesion aun no haya usado
    la nube), por su nombre y con su modulo, como las escribe una traza. Lo que solo tiene su forma (un trozo de un
    nombre de carpeta o de archivo, «EarlyWarning» o «Omega.FusionError») no esta."""
    try:
        import requests.exceptions  # noqa: F401
    except ImportError:
        pass
    seen, todo = {BaseException}, [BaseException]
    while todo:
        for sub in type.__subclasses__(todo.pop()):
            if sub not in seen:
                seen.add(sub)
                todo.append(sub)
    names = set()
    for cls in seen:
        names |= {cls.__name__, cls.__qualname__}
        if cls.__module__ not in ("builtins", "__main__"):
            names.add(f"{cls.__module__}.{cls.__qualname__}")
    return names


def diag_codes(text):
    """« (HTTP 401, PermissionError)»: los codigos de error de un texto libre (sin repetir, 4 como mucho; nada si no
    hay). De las palabras, solo las excepciones conocidas (diag_exceptions), entera o la ultima parte de un nombre con
    puntos; no un nombre en minusculas sin modulo («error») ni Error, Exception o Warning a secas."""
    known, codes = diag_exceptions(), []
    for m in DIAG_CODES.finditer(text or ""):
        code = m.group(0)
        if m.group("exc") is not None:
            code = next((c for c in (code, code.rsplit(".", 1)[-1]) if c in known and ("." in c or c[0].isupper())
                         and c not in ("Error", "Exception", "Warning")), None)
        if code and code not in codes:
            codes.append(code)
            if len(codes) == 4:
                break
    return f" ({', '.join(codes)})" if codes else ""


def _diag_meeting_app(v):
    from recording import MEETING_APPS
    return v in {label for _, label in MEETING_APPS}


# lo que un hueco tiene que ser para ensenarse tal cual (los que no estan, {t}, nunca)
DIAG_CHECKS = {
    "m": lambda v: v in DIAG_MODELS,
    "c": lambda v: v in diag_exceptions(),
    "k": lambda v: all(re.fullmatch(r"[a-z_.]{2,40}", x) for x in v.split(", ")),
    "l": lambda v: bool(re.fullmatch(r"auto|detectar|[a-z]{2}(?:-[a-z]{2})?", v)),
    "a": _diag_meeting_app,
    "u": lambda v: bool(re.fullmatch(r"http://127\.0\.0\.1:\d{2,5}/", v)),
    "v": lambda v: bool(re.fullmatch(r"\d+(?:\.\d+){1,3}[a-z0-9]{0,6}", v)),
    "y": lambda v: bool(re.fullmatch(r"[A-Za-z0-9_.-]+\.pyw?", v)),
    "f": lambda v: bool(re.fullmatch(r"[A-Za-z_<][A-Za-z0-9_<>.]*", v)),
}


def _diag_value(kind, v):
    if kind == "n":
        return v
    if kind == "e":
        return DIAG_FREE + diag_codes(v) if v.strip() else v
    if kind == "y":  # de la ruta de una traza, solo el nombre del .py
        v = re.split(r"[\\/]", v)[-1]
    ok = DIAG_CHECKS.get(kind)
    return v if ok and ok(v) else DIAG_FREE


def diag_line(line):
    """Una linea de server.log para el diagnostico: la fecha y el nivel, y del mensaje lo de su plantilla
    (DIAG_LINES), con cada hueco segun su tipo. Sin plantilla (o con mas de DIAG_MAX caracteres): la sangria, el icono
    del principio (●, ✖, 🗣…) y «<texto>» con los codigos de error que lleve."""
    head = _DIAG_HEAD.match(line)
    head = head.group(0) if head else ""
    msg = line[len(head):]
    for rx in _DIAG_RX if len(msg) <= DIAG_MAX else ():
        m = rx.fullmatch(msg)
        if m:
            out, pos = [], 0
            for name in sorted((g for g in rx.groupindex if m.start(g) >= 0), key=m.start):
                out += [msg[pos:m.start(name)], _diag_value(name[0], m.group(name))]
                pos = m.end(name)
            return head + "".join(out) + msg[pos:]
    body = msg.lstrip(" ")
    indent, first = msg[:len(msg) - len(body)], body.split(" ", 1)[0]
    icon = first + " " if first and len(first) <= 2 and not re.search(r"\w", first) else ""
    rest = body[len(icon):]
    if not rest.strip():
        return head + indent + body.strip()
    return head + indent + icon + DIAG_FREE + diag_codes(rest)


def license_name():
    """Nombre de la licencia segun la primera linea de LICENSE («MIT License» -> «MIT»); vacio si no hay fichero."""
    try:
        first = read_text(os.path.join(ROOT, "LICENSE")).strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""
    return re.sub(r"\s+licen[cs]e$", "", first, flags=re.I)


def open_dir(path):
    """Abre una carpeta en el Explorador (o el Finder / el gestor de archivos)."""
    if WINDOWS:
        subprocess.Popen(["explorer", path])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


class App:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        global COMPLETE
        COMPLETE = Complete(os.path.join(DATA_DIR, "completas.json"))
        self.settings = Store(os.path.join(DATA_DIR, "settings.json"), DEFAULT_SETTINGS)
        if self.settings.get().get("procesado") not in PROCESADOS:  # ajustes de antes: se decide una vez y se guarda
            self.settings.update(lambda d: d.update(procesado=procesado_de(d)))
        if not self.settings.get().get("carpetas_v"):  # la carpeta por reunion (07/11/2026): una vez
            cambios = []
            self.settings.update(lambda d: cambios.extend(migrate_main_folders(d)))
            for k, old, new in cambios:
                log(f"   carpeta principal de {k}: era la de una reunión ({old}); pasa a {new or 'la raíz'}")
        self._aviso_resumen_local()
        self._load_links()
        self.library = Library(Durations(os.path.join(DATA_DIR, "durations.json")))
        self.transcripts = Transcripts()
        self.jobs = Jobs(self.settings, self.library)
        # cambiar la carpeta en pleno directo (msg 1198): se mueve cuando la reunion queda libre (al acabar su ultimo
        # trabajo o en la vuelta de 3 s, que tambien hace los que quedaron de antes de un cierre)
        self.jobs.on_idle, self.jobs.on_tick = self._al_quedar_libre, self._aplicar_movimientos
        # grabadora propia: una grabacion y, mientras la ventana de "Grabar" esta abierta, un medidor
        self.rec, self.monitor, self.monitor_seen = None, None, 0.0
        self.rec_lock, self.devices_cache = threading.Lock(), (0.0, None)
        self.toggle_lock = threading.Lock()
        self.notes_lock, self.meeting, self.meeting_prev = threading.Lock(), None, None
        self.voices_dl = None  # descarga de los modelos de voces en curso (progreso para la interfaz)
        self.vigia = calendario.Vigia()  # reunión en curso (Teams/Outlook): sugerencias y aviso flotante
        threading.Thread(target=self._meeting_watch, daemon=True, name="meeting-watch").start()
        threading.Thread(target=self._monitor_watchdog, daemon=True).start()
        self.hotkey = GlobalHotkey(lambda: self.rec_toggle({"source": "hotkey"}))
        self.hotkey.start()

    def rec_toggle(self, b):
        """Ctrl+Alt+R (o el mismo atajo dentro de la app): empieza con la ultima configuracion o para."""
        with self.toggle_lock:
            try:
                if self.rec and self.rec.alive():
                    if b.get("start_only"):  # el aviso flotante: si Ctrl+Alt+R se adelantó, no se para la grabación
                        return {"recording": True, "path": rel_of(self.rec.path)}
                    r = self.rec_stop({})
                    cue("stop")
                    seg = r["seconds"]  # en segundos si no llego al minuto: «1 min» tapaba una grabacion de 5 s
                    notify("■ Grabación guardada",
                           f"{f'{int(seg)} s' if seg < 60 else f'{round(seg / 60)} min'} · "
                           "la transcripción termina sola")
                    return dict(r, recording=False)
                st = self.settings.get()
                # nombre y tema: lo que mande el aviso flotante; si no, la reunión en curso (Teams/Outlook, sin
                # esperar a COM: calendario.py); si no, el ultimo tema elegido (y la carpeta de su proyecto)
                sug = self.vigia.sugerencia(lambda: calendario.reunion_actual(st, self._vocab_terms(), outlook=False))
                vid = perfil_vocab(st, b.get("vocab") or sug.get("vocab"))  # fijo en el perfil > reunion > ultimo
                v = find_vocab(None, None, vid) if vid else None
                cue("start")  # antes de empezar: asi el pitido no queda grabado
                r = self.rec_start({"mic": st.get("rec_mic") or "default", "sys": st.get("rec_sys") or "all",
                                    "live": True, "preset": st.get("live_preset") or "medium",
                                    "language": st.get("language") or "", "folder": self.rec_folder_for(v, st),
                                    "vocab": v["id"] if v else "", "name": b.get("name") or sug.get("name") or "",
                                    "participantes": b.get("participantes")
                                    or ", ".join(sug.get("participants") or []),
                                    "sugerido": b.get("sugerido") or sug.get("source") or ""})
                notify(f"● Grabando en {APP_NAME}", (f"{r['workstream']} · " if r.get("workstream") else "")
                       + (f"→ {display_rel(r['carpeta'])} · " if r.get("carpeta") is not None else "")
                       + "Pulsa Ctrl+Alt+R otra vez para parar" + (f" · {r['aviso']}" if r.get("aviso") else ""))
                return dict(r, recording=True)
            except ApiError as e:
                cue("error")
                log(f"   no se pudo grabar (Ctrl+Alt+R): {e.msg}")  # el toast se va; el log se queda
                notify("No se pudo grabar", e.msg)
                raise

    # --- grabadora
    def _monitor_watchdog(self):
        while True:  # si la ventana de "Grabar" se cierra sin avisar, el medidor se apaga solo
            time.sleep(2)
            m = self.monitor
            if m and time.time() - self.monitor_seen > 8:
                self.monitor = None
                m.stop(timeout=2)

    def rec_devices(self, q):
        at, cached = self.devices_cache
        if not cached or time.time() - at > 30 or q.get("refresh"):
            r = subprocess.run([sys.executable, os.path.join(APP_DIR, "recorder.py"), "--list"], cwd=APP_DIR,
                               capture_output=True, timeout=30, creationflags=engines.NO_WINDOW)
            try:
                cached = json.loads(r.stdout.decode("utf-8", "replace"))
            except ValueError:
                raise ApiError("No se pudieron leer los dispositivos de audio: "
                               + r.stderr.decode("utf-8", "replace")[-300:])
            self.devices_cache = (time.time(), cached)
        st = self.settings.get()
        mic, sys_dev = st.get("rec_mic") or "default", st.get("rec_sys") or "all"
        # si lo guardado ya no esta conectado, se propone el predeterminado (no un dispositivo fantasma)
        if mic not in ("default", "none") and not any(m["id"] == mic for m in cached["mics"]):
            mic = "default"
        if sys_dev not in ("all", "default", "none") and not any(o["id"] == sys_dev for o in cached["outputs"]):
            sys_dev = "all"
        return dict(cached, mic=mic, sys=sys_dev)

    def rec_status(self, q):
        r, m = self.rec, self.monitor
        if q.get("monitor"):
            self.monitor_seen = time.time()
        out = {"recording": bool(r and r.alive()), "monitor": bool(m and m.alive()), "meeting": self.meeting,
               "build": BUILD}
        if r and r.alive():
            out.update(path=rel_of(r.path), started=r.started, **r.status)
        elif m and m.alive():
            out.update(m.status)
        return out

    def rec_monitor(self, b):
        """Medidores de nivel para probar micro y audio del ordenador antes de grabar."""
        with self.rec_lock:
            old, self.monitor = self.monitor, None
            if old:
                old.stop(timeout=2)
            if b.get("stop") or (self.rec and self.rec.alive()):
                return {"ok": True}
            self.monitor_seen = time.time()
            self.monitor = RecorderProcess(None, b.get("mic"), b.get("sys"), APP_DIR, DATA_DIR)
        return {"ok": True}

    def rec_start(self, b):
        with self.rec_lock:
            if self.rec and self.rec.alive():
                raise ApiError("Ya hay una grabación en marcha.")
            mic, sys_dev = b.get("mic") or "default", b.get("sys") or "default"
            if mic == "none" and sys_dev == "none":
                raise ApiError("Elige al menos el micrófono o el audio del ordenador.")
            if "name" not in b and "vocab" not in b:  # sin nombre ni tema (otra app): la reunión en curso, si la hay
                sug = self.vigia.sugerencia(lambda: calendario.reunion_actual(self.settings.get(), self._vocab_terms(),
                                                                              outlook=False))
                b = dict(b, name=sug.get("name") or "", vocab=sug.get("vocab") or "",
                         participantes=b.get("participantes") or ", ".join(sug.get("participants") or []),
                         sugerido=b.get("sugerido") or sug.get("source") or "")
            folder = safe(b.get("folder", ""), "dir")  # la carpeta principal (rec_folder_for)
            if in_app_dir(folder):
                raise ApiError("No se puede grabar ahí")
            lt = time.localtime()
            label = clean_name(b["name"]) if b.get("name") else ""
            name = time.strftime("%Y-%m-%d %H-%M-%S", lt) + (f" - {label}" if label else "")
            # una carpeta por reunion, «AAAA-MM-DD Nombre», dentro de la principal (msg 1158); se crea al grabar
            mname = fit_meeting_dir(folder, label, lt, name) if por_reunion(self.settings.get()) else None
            mdir = free_dir(folder, mname) if mname else None
            path = os.path.join(mdir or folder, name + ".wav")
            if os.path.exists(path):
                raise ApiError("Ya existe un archivo con ese nombre.")
            made = mdir is not None and not os.path.isdir(mdir)
            if mdir:
                try:
                    os.makedirs(mdir, exist_ok=True)
                except OSError as e:  # sin permiso de escritura (p. ej. una carpeta de solo lectura): se dice
                    raise ApiError(f"No se puede crear la carpeta de la reunión en {display_rel(rel_of(folder))} "
                                   f"({e.__class__.__name__}).")
                carpetas_reunion().add(rel_of(mdir))
            if self.monitor:
                self.monitor.stop(timeout=2)
                self.monitor = None
            rel, props = rel_of(path), {}
            # la grabacion es de la app: mientras dure, el vigilante no arranca el directo por su cuenta
            # (si se eligio "solo grabar", no se transcribe hasta que tu lo pidas)
            self.jobs.claimed.add(rel)
            started = False
            try:
                self.rec = RecorderProcess(path, mic, sys_dev, APP_DIR, DATA_DIR)
                for _ in range(60):  # espera a que exista el archivo con su cabecera
                    if os.path.isfile(path) and os.path.getsize(path) >= 44 or not self.rec.alive():
                        break
                    time.sleep(0.05)
                if not self.rec.alive():
                    raise ApiError("La grabadora no ha podido arrancar (ver %LOCALAPPDATA%\\AudiosApp\\recorder.log).")
                # desde aqui la grabadora esta grabando: lo que falle ya no la para ni se calla (02/11/2026: un error
                # tras arrancarla aviso «No se pudo grabar» y dejo la grabacion en marcha sin decirlo, hasta que el
                # vigilante arranco el directo por su cuenta y la segunda pulsacion la paro)
                started = True
                avisos = []
                preset = b.get("preset") or self.settings.get().get("live_preset") or "medium"
                v = find_vocab(None, None, b["vocab"]) if b.get("vocab") else None
                props = {"proyecto": v["project"], "workstream": v["name"], "vocabulario": v["id"]} if v else {}
                if b.get("participantes"):  # sugeridos por Teams/Outlook: a la cabecera del .md, con su origen
                    props["participantes"] = " ".join(str(b["participantes"]).split())[:500]
                    # sin confirmar: ni el resumen en la nube ni los destinos de salida los llevan hasta que el
                    # usuario los confirme o los edite en la pagina de la reunion (participantes_confirmados)
                    origen = str(b.get("sugerido") or "").strip().lower()
                    if origen in PARTICIPANTES_ORIGENES:
                        props["participantes_origen"] = origen
                # perfil de la reunion, a la cabecera del .md: idioma y contexto (el directo, la cola y el resumen
                # los leen de ahi; se pueden cambiar en la pagina de la reunion) y la carpeta a la que copiar textos
                perfil = perfil_de(self.settings.get())
                if "language" in b:
                    lang = str(b.get("language") or "").strip().lower()
                    props["idioma"] = lang if lang and IDIOMA_RE.match(lang) else "auto"
                ctx = b.get("contexto") if "contexto" in b else perfil["context"]
                ctx = " ".join(str(ctx or "").split())[:engines.ABOUT_CHARS]
                if ctx:
                    props["contexto"] = ctx
                if props:  # antes de arrancar el directo, para que ya use ese vocabulario
                    try:
                        self.notes_save({"path": rel, "props": props, "_al_grabar": True})
                    except Exception as e:
                        log_exc()
                        avisos.append(f"sin cabecera de notas ({e.msg if isinstance(e, ApiError) else e})")

                def upd(d):
                    if "vocab" in b:
                        d["rec_vocab"] = v["id"] if v else ""
                    # la ultima carpeta principal usada: la de las reuniones sin tema (rec_folder_origin). La de un
                    # proyecto ya no se apunta al grabar: se asigna (/api/proyecto/carpeta) y, sin asignar, es la suya
                    d["rec_folder"] = rel_of(folder)
                    # "solo grabar" y "sin micro" son para una grabacion concreta: no se quedan como
                    # predeterminado (la siguiente, y Ctrl+Alt+R, vuelven a transcribir y a grabar el micro)
                    if mic != "none":
                        d["rec_mic"] = mic
                    d.update(rec_sys=sys_dev, live_preset=preset if preset in LIVE_PRESETS else "medium")
                    # el idioma elegido para ESTA reunion va en su .md; el ajuste general solo lo cambia el perfil
                try:
                    self.settings.update(upd)
                except Exception as e:
                    log_exc()
                    avisos.append(f"sin guardar los ajustes de la grabación ({e})")
                self.library.at = 0
                if b.get("live"):
                    try:
                        self.jobs.start_live(rel, preset)
                    except Exception as e:
                        log_exc()
                        avisos.append(f"el directo no ha arrancado ({e.msg if isinstance(e, ApiError) else e})")
                        self.jobs.claimed.discard(rel)  # que el vigilante lo intente por su cuenta
            finally:
                if not started:
                    self.jobs.claimed.discard(rel)
                    if made:  # la carpeta de la reunion recien creada, si la grabadora no llego a escribir nada
                        try:
                            os.rmdir(mdir)
                            carpetas_reunion().discard(rel_of(mdir))
                        except OSError:
                            pass
        log(f"● Grabando: {rel}" + (f" [{v['project']} · {v['name']}]" if v else "")
            + ("" if b.get("live") else " (solo grabar, sin transcribir)")
            + (f" · aviso: {'; '.join(avisos)}" if avisos else ""))
        return {"path": rel, "workstream": f"{v['project']} · {v['name']}" if v else None,
                "participantes": props.get("participantes"), "aviso": "; ".join(avisos) or None,
                "carpeta": rel_of(os.path.dirname(path)), "carpeta_principal": rel_of(folder)}

    def rec_mic(self, b):
        """Silenciar / reactivar mi micro a mitad de grabacion (se graba silencio en su lugar)."""
        r = self.rec
        if not r or not r.alive():
            raise ApiError("No hay ninguna grabación en marcha.")
        mute = bool(b.get("mute"))
        if not r.send("mute" if mute else "unmute"):
            raise ApiError("No se pudo hablar con la grabadora.")
        r.status["mic_muted"] = mute  # para que la interfaz lo vea al momento
        log(f"   micro {'silenciado' if mute else 'reactivado'}")
        return {"mic_muted": mute}

    def rec_stop(self, b):
        with self.rec_lock:
            r = self.rec
            if not r or not r.alive():
                raise ApiError("No hay ninguna grabación en marcha.")
            secs = r.stop()
            self.rec = None
            self.jobs.claimed.discard(rel_of(r.path))
        try:  # cuando hablo yo (micro por encima del audio del PC): sirve para etiquetar «Tu» sin biometria
            tl = r.timeline()
            if tl and os.path.isfile(r.path):
                voices.write_micro(voices.sidecar_path(r.path), tl)
        except Exception:
            log_exc()
        self.library.at = 0
        log(f"■ Grabación parada: {rel_of(r.path)} ({secs / 60:.1f} min)")
        if self._movs().get(rel_of(r.path)):  # «solo grabar» no deja trabajos: si tenia otra carpeta, se mueve ya
            threading.Thread(target=self._aplicar_movimientos, args=(rel_of(r.path),), daemon=True,
                             name="mover").start()
        return {"path": rel_of(r.path), "seconds": secs}

    def shutdown_prepare(self):
        """Antes de terminar el proceso: medidor y grabadora (si quedara alguna) y los trabajos."""
        with self.rec_lock:
            for p in (self.monitor, self.rec):
                if p and p.alive():
                    try:
                        p.stop(timeout=3)
                    except Exception:
                        log_exc()
            self.monitor = self.rec = None
        try:  # destinos de salida: las reescrituras pendientes (4 s) no se pierden al apagar
            self.jobs.flush_outputs()
        except Exception:
            log_exc()
        self.jobs.shutdown()

    def apagar(self, b):
        """Ajustes > Apagar y «Salir» del icono de la bandeja (tray_salir, con b["origen"] == "bandeja"): las unicas
        salidas explicitas cuando no hay consola, y la misma. Con una grabacion en curso se niega; si no, responde y el
        proceso termina al cabo de 0,5 s (schedule_shutdown)."""
        if self.rec and self.rec.alive():
            raise ApiError(f"Hay una grabación en curso. Para la grabación antes de apagar {APP_NAME}.", 409)
        with self.jobs.lock:
            activos = sum(1 for j in self.jobs.list if j.status in ("queued", "running"))
        bandeja = b.get("origen") == "bandeja"
        log(f"apagar pedido desde {'la bandeja' if bandeja else 'la interfaz'} ({activos} trabajos en cola o en "
            "marcha)")
        schedule_shutdown("bandeja > Salir" if bandeja else "Ajustes > Apagar")
        return {"ok": True, "activos": activos}

    def ocultar(self, b):
        """La pagina, tras el aviso de «sigue en segundo plano» (o sin el), pide ocultar la ventana propia."""
        return {"ok": bool(WINDOW is not None and WINDOW.hide())}

    def mostrar(self, b):
        """El acceso directo, al ver que la app ya corre, pide la ventana: la propia si existe. Si aun se esta abriendo
        (pending: en un PC lento, decenas de segundos), nada mas: sale sola y se trae al frente; abrir Edge ahi era la
        segunda ventana del 02/11/2026. Sin ventana propia, Edge (open_ui no lo repite si lo acaba de abrir)."""
        if WINDOW is not None:
            r = WINDOW.request_show()
            if r == "pending":
                return {"ok": True, "pending": True}
            if r:
                return {"ok": True}
        open_ui()
        return {"ok": True, "browser": True}

    def acerca_abrir(self, b):
        """Ajustes > Acerca de: abre la carpeta de datos (DATA_DIR: ajustes, API key cifrada, registros) en el
        Explorador, o el repositorio o la pagina de PayPal («Invitar al autor a un cafe») en el navegador."""
        que = b.get("que")
        if que == "datos":
            os.makedirs(DATA_DIR, exist_ok=True)
            open_dir(DATA_DIR)
        elif que == "repo":
            webbrowser.open(REPO_URL)
        elif que == "donar":
            webbrowser.open(DONAR_URL)
        elif que == "claves":  # el asistente: la página de claves del proveedor (solo las de engines.PROVIDERS)
            url = (engines.PROVIDERS.get(b.get("proveedor") or "groq") or {}).get("keys")
            if not url:
                raise ApiError("Ese proveedor no tiene página de claves: pídesela a quien te dio la API.")
            webbrowser.open(url)
        else:
            raise ApiError("No sé qué abrir")
        return {"ok": True}

    def diagnostico(self, q):
        """Texto para pedir ayuda (Acerca de > Copiar diagnostico), con lista blanca: version, Python y sistema, motor y
        modelo, como es la carpeta de la app (sin su ruta), contadores y las ultimas 30 lineas de server.log por
        diag_line (lo fijo de cada mensaje de la app, cifras y codigos de error; lo demas, «<texto>»). Se pega donde se
        pide ayuda, a veces en un sitio publico: ni nombres ni rutas ni texto libre, y las claves enmascaradas."""
        st = self.settings.get()
        cloud = st.get("cloud") or {}
        preset = next((p for p in engines.PRESETS if p["id"] == st.get("preset")), None)

        def tech(v):
            return v if v in DIAG_MODELS else DIAG_FREE
        if preset and preset["engine"] == "cloud":
            prov = cloud.get("provider") if cloud.get("provider") in engines.PROVIDERS else DIAG_FREE
            motor = f"nube ({prov}) · modelo {tech(cloud.get('model'))}"
        else:
            motor = f"local ({tech(st.get('preset'))}) · modelo {tech(preset['model']) if preset else DIAG_FREE}"
        si = lambda c: "sí" if c else "no"  # noqa: E731
        default = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AudiosApp")
        propia = os.path.normcase(os.path.abspath(DATA_DIR)) == os.path.normcase(os.path.abspath(default))
        datos = ("en la de siempre (%LOCALAPPDATA%\\AudiosApp)" if os.environ.get("LOCALAPPDATA") else
                 "en la de siempre (~/AudiosApp)") if propia else "en otra carpeta"
        en_red, no_ascii = ROOT.startswith(("\\\\", "//")), any(ord(c) > 127 for c in ROOT)
        try:
            lines = read_text(os.path.join(DATA_DIR, "server.log")).splitlines()[-30:]
        except OSError:
            lines = ["(no hay server.log)"]
        keys = [cloud.get("api_key"), *engines.all_extra_keys(cloud)]  # las adicionales de todos los proveedores
        mode = procesado_de(st)
        ver = engines.VERSION_RX.match(str(st.get("agente_version") or ""))
        donde = {"local": "solo en este PC", "api": "API externa"}.get(mode) or (
            f"agente ({agente_de(st)[0].label}" + (f" {ver.group(0)}" if ver else "") + ")")
        info = [f"{APP_NAME} · versión {VERSION}", f"Python {sys.version.split()[0]} · {platform.platform()}",
                f"Dónde se procesa: {donde}",
                f"Transcripción: {motor} · directo: {tech(st.get('live_preset') or 'medium')}",
                f"Carpeta de {APP_NAME}: en OneDrive: {si('onedrive' in ROOT.lower())} · en una unidad de red: "
                f"{si(en_red)} · con tildes u otros caracteres no ASCII: {si(no_ascii)} · {len(ROOT)} caracteres",
                f"Datos: {datos}",
                f"Carpetas vinculadas: {len(LINKS)} · destinos de salida: {len(perfil_de(st)['outputs'])}",
                "", f"server.log (últimas 30 líneas; lo que no es técnico va como {DIAG_FREE}):",
                *map(diag_line, lines)]
        return {"texto": scrub("\n".join(info), keys)}

    def _aviso_resumen_local(self):
        """Al arrancar: la primera vez sobre unos ajustes de una version anterior. Antes, el resumen automatico subia a
        la nube tambien lo transcrito en local; si a quien actualiza le pasaba (resumen automatico activado, nube con
        API key y transcripcion en local: el modelo de la cola o del directo, o reuniones ya transcritas asi), la
        interfaz se lo avisa una vez (config -> summary.aviso_local). En una instalacion nueva no hay que avisar."""
        st = self.settings.get()
        if st.get("aviso_resumen_local") is not None:
            return
        c = st.get("cloud") or {}

        def local(p):
            return (engines.PRESETS_BY_ID.get(p) or {}).get("engine") == "local"
        afecta = (os.path.isfile(self.settings.path) and st.get("summary_auto", True)
                  and not st.get("summary_auto_local") and bool(engines.chat_url(c))
                  and bool((c.get("api_key") or "").strip())
                  and (local(st.get("preset")) or local(st.get("live_preset") or "medium")
                       or any(local(p) for p in st.get("speed") or {})))
        self.settings.update(lambda d: d.update(aviso_resumen_local=bool(afecta)))
        if afecta:
            log("   aviso pendiente: el resumen automático ya no sube a la nube lo transcrito en local")

    # --- GET
    def _agente_info(self, st):
        """Para Ajustes: el agente configurado, si esta instalado, su version y cuando se comprobo."""
        cls, a = agente_de(st)
        ruta = cls.find()
        return {"tipo": a["tipo"], "modelo": a["modelo"], "label": cls.label,
                "modelos": [list(m) for m in cls.MODELOS], "encontrado": bool(ruta), "ruta": ruta,
                "version": st.get("agente_version") or None, "comprobado": st.get("agente_comprobado") or None}

    def config(self, q):
        st = self.settings.get()
        mode = procesado_de(st)
        presets = []
        for p in engines.PRESETS:
            if p["engine"] == "cloud" and mode != "api":  # la nube no se ofrece si no se ha elegido «API externa»
                continue
            d = {k: v for k, v in p.items()}
            d["speed"] = st["speed"].get(p["id"], p["speed"])
            d["cached"] = p["engine"] == "cloud" or engines.model_cached(p["model"])
            m = engines.WHISPER_MODELS.get(p.get("model"))
            d["size_mb"] = round(sum(s for _, s, _ in m[2]) / 1e6) if m else None  # lo que descarga la primera vez
            presets.append(d)
        cloud = dict(st["cloud"])
        cloud["has_key"] = bool(cloud.get("api_key"))
        cloud["api_key"] = masked(cloud.get("api_key"))
        # claves adicionales: enmascaradas (nunca en claro) y por proveedor, cada una con el suyo; en extra_keys, las
        # del proveedor configurado (las unicas que se usan)
        by = {p: [masked(k) for k in (cloud.pop(f, None) or []) if k] for p, f in engines.EXTRA_KEYS_FIELDS.items()}
        cloud["extra_keys_by"], cloud["extra_keys"] = by, by.get(cloud.get("provider") or "groq", [])
        cloud["quota"] = (self.jobs.cloud.keys.public(st["cloud"], model=engines.CloudEngine.live_model(st["cloud"]))
                          if cloud["has_key"] and getattr(self.jobs, "cloud", None) is not None else None)
        return {"root": ROOT, "version": VERSION, "app_name": APP_NAME, "window": WINDOW is not None,
                "acerca": {"licencia": license_name(), "repo": REPO_URL, "datos": DATA_DIR, "donar": DONAR_URL},
                "presets": presets, "providers": engines.PROVIDERS, "cloud": cloud,
                "procesado": mode, "agente": self._agente_info(st),
                "ultima_prueba": (st.get("ultima_prueba") if (st.get("ultima_prueba") or {}).get("modo") == mode
                                  else None),
                "preset": st["preset"], "language": st["language"], "threads": st["threads"],
                "prompt": st["prompt"], "cpu_count": os.cpu_count(),
                "live_auto": bool(st.get("live_auto")), "live_preset": st.get("live_preset") or "medium",
                "live_final_pass": bool(st.get("live_final_pass", True)),
                "hotkey": {"ok": self.hotkey.ok, "error": self.hotkey.error, "keys": "Ctrl+Alt+R"},
                "favorites": st.get("favorites") or [], "folder_icons": st.get("folder_icons") or {},
                "links": [{"rel": LINK + k, "name": l["name"], "path": l["path"]} for k, l in LINKS.items()],
                # carpeta principal de cada proyecto, la asignada o la suya en el arbol (rec_folder_origin); las
                # asignadas tal cual, aparte
                "rec_folder": st.get("rec_folder") or "", "project_folders": project_folders_resolved(st),
                "project_folders_asignadas": dict(st.get("project_folders") or {}),
                "workstream_folders": dict(st.get("workstream_folders") or {}),
                "carpeta_por_reunion": por_reunion(st),
                "meeting_detect": bool(st.get("meeting_detect", True)),
                "outlook_on": bool(st.get("outlook_on")), "rec_aviso": bool(st.get("rec_aviso", True)),
                "summary": {"auto": bool(st.get("summary_auto", True)), "local": bool(st.get("summary_auto_local")),
                            "aviso_local": bool(st.get("aviso_resumen_local")),  # una vez, tras actualizar
                            "model": st.get("summary_model"),
                            "lang": st.get("summary_lang") or "es", "extra": st.get("summary_extra") or "",
                            "models": engines.SUMMARY_MODELS, "ready": self.jobs.summary_ready()},
                "voices": {"enabled": bool(st.get("voices_enabled")), "models_ready": voices.models_ready(DATA_DIR)},
                "worker_keepalive_min": int(st.get("worker_keepalive_min", 30) or 0),
                "worker_preload": bool(st.get("worker_preload", True)),
                "lost_secrets": list(getattr(self.settings, "lost", [])),
                "settings_broken": getattr(self.settings, "broken", None),  # settings.json ilegible al arrancar
                "perfil": perfil_de(st)}

    def tree(self, q):
        public, version = self.library.snapshot(max_age=1.0)
        if q.get("v") == version:
            return {"version": version, "same": True}
        return {"version": version, "tree": public}

    def transcript(self, q):
        abs_path = safe(q.get("path"), "file")
        out = public_transcript(self.transcripts.load(os.path.splitext(abs_path)[0]))
        out["has_backup"] = os.path.isdir(backup_dir(rel_of(abs_path)))
        return out

    # --- resumen con IA
    def summary_get(self, q):
        abs_path = safe(q.get("path"), "file")
        p = os.path.splitext(abs_path)[0] + ".resumen.md"
        if not os.path.isfile(p):
            return {"exists": False, "ready": self.jobs.summary_ready()}
        text, meta = read_text(p), {}
        lines = text.split("\n")
        if lines and lines[0].strip() == "---":
            end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
            for l in lines[1:end]:
                k, _, v = l.partition(":")
                meta[k.strip()] = v.strip()
            text = "\n".join(lines[end + 1:]).strip()
        return {"exists": True, "text": text, "meta": meta, "ready": self.jobs.summary_ready()}

    def summarize(self, b):
        return {"id": self.jobs.submit_summary(b.get("path"))}

    # --- notas y propiedades (estilo Notion)
    def notes_get(self, q):
        abs_path = safe(q.get("path"), "file")
        md = os.path.splitext(abs_path)[0] + ".md"
        if not os.path.isfile(md):
            return {"props": {}, "body": "", "mtime": 0}
        props, body = parse_notes(read_text(md))
        return {"props": with_theme_id(props, rel_of(abs_path)), "body": body, "mtime": os.path.getmtime(md)}

    def notes_save(self, b):
        """Guarda las notas y/o propiedades (solo lo que venga en la peticion)."""
        abs_path = safe(b.get("path"), "file")
        md = os.path.splitext(abs_path)[0] + ".md"
        tema = bool({"proyecto", "workstream", "vocabulario"} & set(b.get("props") or {})) and not b.get("_al_grabar")
        with self.notes_lock:
            props, body = parse_notes(read_text(md)) if os.path.isfile(md) else ({}, "")
            antes = self._tema_de(props, abs_path) if tema else None
            for k, v in (b.get("props") or {}).items():
                if k in NOTE_KEYS:
                    if k == "etiquetas":
                        v = [" ".join(str(t).split()) for t in (v or []) if str(t).strip()]
                    elif k == "idioma":  # codigo o "auto"; vacio = se quita la clave (vuelve al del perfil)
                        v = str(v or "").strip().lower()
                        v = v if IDIOMA_RE.match(v) else ""
                    elif k == "contexto":
                        v = " ".join(str(v or "").split())[:engines.ABOUT_CHARS]
                    elif k == "icono":
                        v = clean_icon(v)
                    elif k == "participantes_origen":  # solo «sugeridos por»; vacio = confirmados
                        v = str(v or "").strip().lower()
                        v = v if v in PARTICIPANTES_ORIGENES else ""
                    props[k] = v
            # escribir (o confirmar tal cual) los participantes los hace del usuario: ya pueden ir al resumen y a los
            # destinos. Solo siguen «sugeridos» si quien los guarda dice de donde vienen (rec_start)
            if "participantes" in (b.get("props") or {}) and "participantes_origen" not in (b.get("props") or {}):
                props.pop("participantes_origen", None)
            if not props.get("participantes"):
                props.pop("participantes_origen", None)
            theme_vocab(props, abs_path, b.get("props") or {})  # el tema: nombres a la cabecera, id a temas()
            if "body" in b:
                body = b.get("body") or ""
            text = dump_notes(props, body)
            if text:
                save_text(md, text, "utf-8")
            elif os.path.isfile(md):
                os.remove(md)  # sin notas ni propiedades: no se deja un archivo vacio
        if {"proyecto", "workstream", "idioma", "contexto", "vocabulario"} & set(b.get("props") or {}):
            self.jobs.refresh_prompt(rel_of(abs_path))  # el directo lo usa desde el siguiente trozo
        self.jobs.schedule_outputs(rel_of(abs_path))  # destinos de salida: el documento con las notas nuevas
        self.library.at = 0
        out = {"props": with_theme_id(props, rel_of(abs_path)),
               "mtime": os.path.getmtime(md) if os.path.isfile(md) else 0}
        if tema:
            try:
                out["pendiente"] = self._tema_movido(abs_path, antes, props)
            except Exception:  # las notas ya estan guardadas: si esto falla, se anota y no se devuelve error
                log_exc()
        return out

    @staticmethod
    def _tema_de(props, abs_path):
        """La lista del tema que dice la cabecera de una reunion (o None)."""
        if not props.get("workstream"):
            return None
        try:
            return find_vocab(props.get("proyecto"), props["workstream"], temas().get(rel_of(abs_path)), near=abs_path)
        except OSError:
            return None

    def _tema_movido(self, abs_path, antes, props):
        """Cambiar el proyecto (el tema) en pleno directo hace lo mismo que «Cambiar» la carpeta (mock 01b, nota 3): si
        la reunion estaba en la carpeta principal que le tocaba por su tema (no en una elegida a mano), queda apuntada
        para moverse a la del tema nuevo cuando acabe todo. Fuera del directo, el tema no mueve nada. Lo apuntado asi
        sigue al tema mientras no se haya movido; una carpeta elegida con «Cambiar» manda. Devuelve lo pendiente."""
        rel = rel_of(abs_path)
        movs = self._movs()
        e = movs.get(rel)
        auto = bool(e and e.get("por") == "tema")
        if (e and not auto) or (not auto and not self._grabando(rel)):
            return None
        st = self.settings.get()
        cur = _norm_dir(self._donde_esta(abs_path))
        try:
            old_main = safe(rec_folder_origin(antes, st)[0], "dir")
            new_main = safe(rec_folder_origin(self._tema_de(props, abs_path), st)[0], "dir")
        except ApiError:
            return None
        if not auto and _norm_dir(old_main) != cur:
            return None  # estaba en una carpeta elegida a mano: el tema no la cambia
        if _norm_dir(new_main) == cur:
            if auto:
                movs.pop(rel)
                log(f"📁 {rel}: ya no se mueve (deshecho)")
            return None
        e = {"to": rel_of(new_main), "at": time.time(), "por": "tema"}
        movs.set(rel, e)
        log(f"📁 {rel}: se moverá a {e['to']} al terminar")
        return self._pendiente_publico(rel, e)

    def tags(self, q):
        self.library.refresh(max_age=5)
        seen = {}
        for a in self.library.flat:
            for t in (a.get("props") or {}).get("etiquetas") or []:
                seen[t] = seen.get(t, 0) + 1
        return {"tags": sorted(seen, key=lambda t: (-seen[t], fold(t)))}

    # --- carpetas vinculadas (fuera de Audios)
    def _load_links(self):
        LINKS.clear()
        for l in self.settings.get().get("links") or []:
            LINKS[link_id(l["path"])] = {"name": l.get("name") or os.path.basename(l["path"]), "path": l["path"]}

    def _save_links(self):
        self.settings.update(lambda d: d.update(links=[dict(l) for l in LINKS.values()]))
        self.library.at = 0

    def link_pick(self, b):
        """Abre el selector de carpetas de Windows (en este PC) y devuelve la que elijas. b.title: lo que dice la
        ventana (por defecto, la carpeta donde grabar; tambien se usa para la carpeta de copias y para importar)."""
        # la ruta vuelve en bytes UTF-8 escritos a mano: con pythonw (el acceso directo) la salida del hijo iria en
        # cp1252 y «José» o «Compañía» llegarian con «�» (o el hijo fallaria con «Ł»)
        script = ("import sys, tkinter as tk\nfrom tkinter import filedialog\n"
                  "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
                  "p = filedialog.askdirectory(parent=r, title=sys.argv[1], mustexist=True,"
                  " initialdir=sys.argv[2] or None)\n"
                  "sys.stdout.buffer.write((p or '').encode('utf-8', 'surrogatepass')); sys.stdout.flush()\n")
        start = os.path.dirname(ROOT) if not LINKS else os.path.dirname(next(iter(LINKS.values()))["path"])
        try:
            title = " ".join(str(b.get("title") or "").split())[:80] or "Carpeta donde guardar las grabaciones"
            r = subprocess.run([sys.executable, "-c", script, title, start],
                               capture_output=True, timeout=900, creationflags=engines.NO_WINDOW,
                               env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
        except (OSError, subprocess.SubprocessError) as e:
            raise ApiError(f"No se pudo abrir el selector de carpetas ({e.__class__.__name__}); pega la ruta a mano.")
        try:
            path = r.stdout.decode("utf-8", "surrogatepass").strip()
        except UnicodeDecodeError:
            path = r.stdout.decode("utf-8", "replace").strip()
        if r.returncode != 0 and not path:
            raise ApiError("No se pudo abrir el selector de carpetas; pega la ruta a mano.")
        return {"path": os.path.normpath(path) if path else ""}

    def link_add(self, b):
        path = os.path.normpath(os.path.expandvars((b.get("path") or "").strip().strip('"')))
        if not os.path.isabs(path) or not os.path.isdir(path):
            raise ApiError("Esa carpeta no existe: " + path)
        if os.path.splitdrive(path)[1] in ("", os.sep):
            raise ApiError("Elige una carpeta, no una unidad entera.")
        if _inside(path, ROOT):
            raise ApiError(f"Esa carpeta ya está dentro de {APP_NAME}: no hace falta vincularla.")
        if _inside(ROOT, path) or _inside(os.path.expanduser("~"), path):
            raise ApiError(f"Esa carpeta es demasiado grande (contiene la de {APP_NAME} o tu usuario): "
                           "elige una más concreta.")
        lid = link_id(path)
        for other, l in LINKS.items():
            if other != lid and (_inside(path, l["path"]) or _inside(l["path"], path)):
                raise ApiError(f"Se solapa con la carpeta ya vinculada «{l['name']}».")
        if lid not in LINKS:
            LINKS[lid] = {"name": clean_name(b.get("name") or os.path.basename(path)), "path": path}
            self._save_links()
            log(f"🔗 carpeta vinculada: {path}")
        return {"rel": LINK + lid, "name": LINKS[lid]["name"]}

    def link_remove(self, b):
        """Deja de mostrarla en la app. No borra ni mueve nada de la carpeta."""
        rel = b.get("path") or ""
        lid = rel[len(LINK):].split("/")[0] if rel.startswith(LINK) else ""
        if lid not in LINKS:
            raise ApiError("Esa carpeta no está vinculada.")
        busy = any(j["path"].startswith(LINK + lid) and j["status"] in ("queued", "running")
                   for j in self.jobs.snapshot())
        if busy or (self.rec and self.rec.alive() and rel_of(self.rec.path).startswith(LINK + lid)):
            raise ApiError("Se está grabando o transcribiendo algo de esa carpeta; espera a que termine.")
        l = LINKS.pop(lid)
        self._save_links()
        self.settings.update(lambda d: d.update(
            favorites=[f for f in d.get("favorites", []) if not f["path"].startswith(LINK + lid)],
            rec_folder="" if (d.get("rec_folder") or "").startswith(LINK + lid) else d.get("rec_folder", ""),
            project_folders={k: v for k, v in (d.get("project_folders") or {}).items()
                             if not v.startswith(LINK + lid)},
            workstream_folders={k: v for k, v in (d.get("workstream_folders") or {}).items()
                                if not v.startswith(LINK + lid) and not k.startswith(LINK + lid)}))
        log(f"🔗 carpeta desvinculada (no se ha tocado nada): {l['path']}")
        return {"ok": True}

    def rec_folder_for(self, v, st):
        """La carpeta principal donde se graba una reunion con el tema `v` (la regla, en rec_folder_origin). Dentro,
        rec_start crea la carpeta de la reunion (carpeta por reunion); para saber cual sera, rec_destino."""
        return rec_folder_origin(v, st)[0]

    def carpeta_principal(self, b):
        """POST /api/proyecto/carpeta {project, vocab?, folder}: asigna la carpeta principal de un proyecto (o, con
        `vocab`, la de ese workstream: el id de su lista). folder None = sin asignar (vuelve a la del proyecto en el
        arbol). Devuelve las carpetas principales de cada proyecto (como /api/config)."""
        project, vid, rel = str(b.get("project") or "").strip(), str(b.get("vocab") or "").strip(), b.get("folder")
        if vid:
            v = find_vocab(None, None, vid)
            if not v:
                raise ApiError("Ese tema de vocabulario ya no existe.")
            project = v["project"]
        if not project:
            raise ApiError("Falta el proyecto.")
        if rel is not None:
            rel = str(rel).replace("\\", "/").strip("/")
            if in_app_dir(safe(rel, "dir")):
                raise ApiError("No se puede grabar ahí")
        key, name = ("workstream_folders", vid) if vid else ("project_folders", project)

        def upd(d):
            m = dict(d.get(key) or {})
            if rel is None:
                m.pop(name, None)
            else:
                m[name] = rel
            d[key] = m
        self.settings.update(upd)
        log(f"   carpeta principal de {project}{' (workstream)' if vid else ''}: "
            + (rel if rel is not None else "la suya"))
        st = self.settings.get()
        return {"project_folders": project_folders_resolved(st),
                "workstream_folders": dict(st.get("workstream_folders") or {})}

    # --- vocabulario por workstream
    def vocab(self, q):
        st = self.settings.get()
        lang = q.get("language", st.get("language") or "")
        items = []
        for v in list_vocab():
            try:
                items.append(vocab_info(v, lang))
            except OSError:
                pass  # archivo a medio guardar o solo en OneDrive sin conexion
        return {"items": items, "last": st.get("rec_vocab") or "", "general": bool(st.get("prompt"))}

    def vocab_check(self, b):
        return vocab_fit(b.get("terms") or "", b.get("language", self.settings.get().get("language") or ""))

    def vocab_save(self, b):
        """Crear o editar un tema (y con el, un proyecto nuevo): <proyecto>/Vocabulario/<workstream>.txt."""
        project, name = clean_name(b.get("project")), clean_name(b.get("name"))
        terms = engines.split_terms(b.get("terms") or "")
        if not terms:
            raise ApiError("Escribe al menos un término (separados por comas).")
        desc = " ".join((b.get("desc") or "").split())
        old = find_vocab(None, None, b["id"]) if b.get("id") else None
        same = next((v for v in list_vocab() if fold(v["project"]) == fold(project)), None)
        # proyecto que ya existe: en su carpeta de vocabulario; si es nuevo, <raiz>/<proyecto>/Vocabulario
        vdir = os.path.dirname(same["_abs"]) if same else os.path.join(ROOT, project, "Vocabulario")
        path = os.path.join(vdir, name + ".txt")
        moved = old and os.path.normcase(old["_abs"]) != os.path.normcase(path)
        if (not old or moved) and os.path.exists(path):
            raise ApiError(f"Ya hay un tema «{name}» en {project}.")
        os.makedirs(vdir, exist_ok=True)
        save_text(path, (f"# {desc}\n" if desc else "") + ", ".join(terms) + "\n", "utf-8")
        if moved:  # se ha renombrado (o cambiado de proyecto): fuera el archivo anterior
            os.remove(old["_abs"])
            # el perfil y el ultimo tema elegido lo siguen (si no, Ctrl+Alt+R se quedaba sin vocabulario)
            self.settings.update(lambda d: settings_moved(d, old["id"], rel_of(path)))
        self.library.at = 0
        log(f"🔤 vocabulario guardado: {project} · {name} ({len(terms)} términos)")
        return {"id": rel_of(path)}

    def vocab_import(self, b):
        """POST /api/vocab/import {path, project?}: copia las listas de otra carpeta (ver import_vocab)."""
        r = import_vocab(b.get("path"), (b.get("project") or "").strip() or None)
        self.library.at = 0
        return r

    # --- perfil de reunion
    def perfil_get(self, q):
        """GET /api/perfil: el perfil por defecto y lo que se aplicaria ahora mismo a una grabacion (con la reunion
        en curso, si la hay), y los avisos de los destinos de salida (documentos que no se han podido escribir)."""
        st = self.settings.get()
        sug = self.vigia.sugerencia(lambda: calendario.reunion_actual(st, self._vocab_terms(), outlook=False))
        auto = lambda vid: rec_folder_origin(find_vocab(None, None, vid) if vid else None, st)  # noqa: E731
        avisos = getattr(getattr(self, "jobs", None), "outputs_warnings", None)  # sin cola (tests): ninguno
        return {"perfil": perfil_de(st), "resuelto": resolve_perfil(st, sug, auto_folder=auto),
                "avisos": avisos() if avisos else []}

    def perfil_save(self, b):
        """POST /api/perfil: guarda el perfil por defecto (solo las claves que vengan: folder, vocab, context,
        outputs, language). Lo usan Ajustes y «Guardar como perfil por defecto» del dialogo Grabar.
        Con "comprobar": solo valida los destinos de salida que vengan (Ajustes, al anadir una carpeta) y los devuelve
        normalizados, sin guardar nada: asi la interfaz rechaza al momento una carpeta que no vale."""
        if b.get("comprobar"):
            return {"outputs": check_perfil({"outputs": b.get("outputs") or []})["outputs"]}
        cambios = check_perfil(b, perfil_de(self.settings.get())["outputs"])
        self.settings.update(lambda d: update_perfil(d, cambios))
        log("   perfil de reunión guardado: " + ", ".join(sorted(cambios)))
        return {"perfil": perfil_de(self.settings.get())}

    def vocab_delete(self, b):
        v = find_vocab(None, None, b.get("id"))
        if not v:
            raise ApiError("Ese tema ya no existe.")
        to_recycle_bin([v["_abs"]])  # a la papelera: se puede recuperar

        def upd(d):  # el ultimo tema elegido y el fijo del perfil, si eran este: el perfil vuelve a «automatico»
            d["rec_vocab"] = "" if d.get("rec_vocab") == v["id"] else d.get("rec_vocab", "")
            if (d.get("perfil") or {}).get("vocab") == v["id"]:
                d["perfil"] = dict(d["perfil"], vocab=None)
            if v["id"] in (d.get("workstream_folders") or {}):  # su carpeta principal deja de estar asignada
                d["workstream_folders"] = {k: x for k, x in d["workstream_folders"].items() if k != v["id"]}
        self.settings.update(upd)
        self.library.at = 0
        log(f"🗑 vocabulario a la papelera: {v['project']} · {v['name']}")
        return {"ok": True}

    # --- favoritos e iconos de carpeta
    def favorite(self, b):
        item = {"kind": b.get("kind"), "path": b.get("path")}
        if item["kind"] not in ("a", "f", "d"):
            raise ApiError("Tipo no válido")

        def upd(d):
            favs = [f for f in d.get("favorites", []) if f != item]
            if b.get("on"):
                favs.append(item)
            d["favorites"] = favs
        self.settings.update(upd)
        return {"favorites": self.settings.get()["favorites"]}

    def folder_icon(self, b):
        safe(b.get("path"), "dir")
        icon = (b.get("icon") or "").strip()[:4]

        def upd(d):
            icons = dict(d.get("folder_icons", {}))
            if icon:
                icons[b["path"]] = icon
            else:
                icons.pop(b["path"], None)
            d["folder_icons"] = icons
        self.settings.update(upd)
        return {"ok": True}

    def _rename_refs(self, old, new, folder=False):
        """Favoritos e iconos siguen al audio/carpeta cuando se mueve o renombra."""
        def moved(p):
            if p == old:
                return new
            if folder and p.startswith(old + "/"):
                return new + p[len(old):]
            return p

        def upd(d):
            d["favorites"] = [dict(f, path=moved(f["path"])) for f in d.get("favorites", [])]
            d["folder_icons"] = {moved(k): v for k, v in d.get("folder_icons", {}).items()}
        self.settings.update(upd)

    # --- eliminar (a la papelera) e importar
    def delete(self, b):
        p = safe(b.get("path"), "file")
        rel = rel_of(p)
        a = self.library.find(rel)
        mine = self.rec and self.rec.alive() and os.path.normcase(self.rec.path) == os.path.normcase(p)
        if self.jobs.active_for(rel) or (a and a.get("recording")) or mine:
            raise ApiError("Se está grabando o transcribiendo; espera a que termine.")
        if self.jobs.active_for(rel, kinds=("summary",)):
            raise ApiError("Se está resumiendo esta reunión; espera a que termine (o cancela el resumen en la cola).")
        group = [p]
        stem, ext = os.path.splitext(p)
        own = None
        if ext.lower() in AUDIO_EXT:
            group += [stem + e for e in SIDE_EXTS if os.path.isfile(stem + e)]
            own = own_meeting_dir(p)
        for attempt in range(8):  # el reproductor puede tener el audio abierto un instante
            try:
                to_recycle_bin(group)
                break
            except ApiError:
                if attempt == 7:
                    raise
                time.sleep(0.3)
        if own:  # su carpeta (carpeta por reunion), si se ha quedado vacia
            try:
                os.rmdir(own)
                carpetas_reunion().discard(rel_of(own))
            except OSError:
                pass
        COMPLETE.set(rel, False)
        self._movs().pop(rel)  # un movimiento pendiente (cambiar la carpeta en pleno directo) ya no tiene a quien mover
        self.jobs._cloud_text().set(rel, False)  # otra reunion que acabe con este nombre no hereda su origen
        shutil.rmtree(backup_dir(rel), ignore_errors=True)
        self.jobs.voice_vectors.pop(rel, None)
        try:
            temas().set(rel, None)
        except OSError:
            log_exc()
        self.settings.update(lambda d: d.update(favorites=[f for f in d.get("favorites", []) if f["path"] != rel]))
        copias = 0
        if ext.lower() in AUDIO_EXT:  # sus documentos de los destinos de salida que nadie ha cambiado, tambien
            try:
                copias = self.jobs.forget_outputs(rel)
            except Exception:
                log_exc()
        self.library.at = 0
        log(f"🗑 {rel} → papelera ({len(group)} archivos" + (f" y {copias} documentos de los destinos" if copias else "")
            + ")")
        return {"ok": True, "count": len(group), "copias": copias}

    def import_file(self, handler, q):
        """Subir un audio (o .txt/.md) arrastrandolo a la ventana: se copia a la carpeta indicada."""
        folder = safe(q.get("folder", ""), "dir")
        if in_app_dir(folder):
            raise ApiError("No se puede guardar ahí")
        name = clean_name(os.path.basename(q.get("name") or ""))
        stem, ext = os.path.splitext(name)
        if ext.lower() not in AUDIO_EXT | TEXT_EXT:
            raise ApiError("Solo audios (wav, mp3, m4a…) o textos (.txt, .md, .srt)")
        target, n = os.path.join(folder, name), 2
        while os.path.exists(target):
            target, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
        remaining = int(handler.headers.get("Content-Length") or 0)
        tmp = target + ".subiendo"
        with open(tmp, "wb") as f:
            while remaining > 0:
                chunk = handler.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining > 0:
            os.remove(tmp)
            raise ApiError("La subida se cortó.")
        os.replace(tmp, target)
        self.library.at = 0
        log(f"⤓ importado: {rel_of(target)}")
        return {"path": rel_of(target)}

    def _meeting_watch(self):
        notified = None
        while True:
            time.sleep(8)
            try:
                notified = self._meeting_step(notified)
            except Exception:
                pass

    def _meeting_step(self, notified):
        """Un paso del vigilante de llamadas (cada 8 s); devuelve la llamada ya avisada. «Avisarme si estoy en una
        llamada» (meeting_detect) es el interruptor general: apagado, ni toast, ni aviso flotante (aunque «Aviso
        flotante» siga marcado), ni el chip de la interfaz, que se pinta con `meeting`."""
        leido = mic_meeting_app()
        # dos lecturas seguidas (16 s) antes de dar la llamada por buena: una lectura suelta del registro de privacidad
        # no abre el aviso flotante, ni manda el toast, ni pinta el chip (02/11/2026: saltaban al arrancar sin reunion)
        app, self.meeting_prev = (leido if leido == self.meeting_prev else None), leido
        recording = bool(self.rec and self.rec.alive())
        st = self.settings.get()
        detect = st.get("meeting_detect", True)
        self.meeting = app if detect else None
        if not detect:
            self.vigia.cerrar_aviso()  # si estaba abierto cuando se desmarco
        # aviso flotante «Grabar / ✕» (calendario.py + aviso.py): una vez por llamada y nunca si ya se graba;
        # mientras dure la llamada se lee la reunión (Teams y, si está activado, Outlook) para sugerir
        if self.vigia.tic(app, recording, st if detect else dict(st, rec_aviso=False),
                          lambda: calendario.reunion_actual(st, self._vocab_terms()),
                          PORT, APP_DIR, creationflags=engines.NO_WINDOW):
            notified = app  # el aviso flotante sustituye al toast de Windows en esa llamada
            log(f"   reunión detectada ({app}): aviso flotante abierto")
        if detect and app and not recording and notified != app:
            notified = app
            notify("¿Quieres tomar notas de la sesión?", "Pulsa Ctrl+Alt+R para empezar a tomar notas.")
            log(f"   reunión detectada ({app}): aviso enviado")
        if not app:
            notified = None
        return notified

    def _vocab_terms(self):
        """Listas de vocabulario con sus términos: con ellas calendario.suggest elige el tema de la reunión."""
        return [dict(v, terms=read_vocab(v["_abs"])[1]) for v in list_vocab()]

    def reunion_actual(self, q):
        """GET /api/reunion/actual: ventana de Teams, cita de Outlook (si está activado, o ?outlook=1 para probar)
        y la sugerencia de nombre, tema y participantes. Lo usan el botón «Probar» y el diálogo Grabar."""
        return self.vigia.recordar(calendario.reunion_actual(self.settings.get(), self._vocab_terms(),
                                                             outlook=True if q.get("outlook") else None))

    # --- edicion de transcripciones
    _tx_save_lock = threading.Lock()

    def save_transcript(self, b):
        """Guarda lo corregido a mano. La primera vez copia lo que escribio Whisper (para poder volver). De uno en uno:
        al cerrar la pagina el mismo texto llega dos veces a la vez (keepalive y el fetch normal), y el segundo podia
        escribir lo corregido antes de que el primero copiara el original."""
        with self._tx_save_lock:
            return self._save_transcript(b)

    def _save_transcript(self, b):
        abs_path = safe(b.get("path"), "file")
        rel, base = rel_of(abs_path), os.path.splitext(abs_path)[0]
        segs = []
        for s in b.get("segments") or []:
            text = " ".join(str(s.get("text") or "").split())
            if text:
                segs.append((s.get("start"), s.get("end"), text))
        job = self.jobs.active_for(rel)
        if job and job.live:
            return self._save_live_edits(job, segs, base)
        if job:
            raise ApiError("Se está haciendo el repaso final (1–2 min por hora de reunión); "
                           "podrás editar en cuanto termine."
                           if job.final_pass else "Se está transcribiendo; podrás editar cuando termine.")
        if not segs:
            raise ApiError("La transcripción no puede quedar vacía.")
        bdir = backup_dir(rel)
        if not os.path.isdir(bdir):
            os.makedirs(bdir)
            for ext in (".srt", ".txt"):
                if os.path.isfile(base + ext):
                    shutil.copyfile(base + ext, os.path.join(bdir, "original" + ext))
            with open(os.path.join(bdir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"path": rel, "at": time.time()}, f, ensure_ascii=False)
        if os.path.isfile(base + ".srt") and all(a is not None and e is not None for a, e, _ in segs):
            write_outputs(abs_path, [(float(a), float(e), t) for a, e, t in segs])
        else:  # transcripcion solo en .txt (sin tiempos)
            save_text(base + ".txt", "\n".join(t for _, _, t in segs) + "\n", "utf-8-sig")
        self.library.at = 0
        self.jobs.schedule_outputs(rel)  # destinos de salida: el documento con la transcripcion corregida
        mt = max((os.path.getmtime(base + e) for e in (".txt", ".srt") if os.path.isfile(base + e)), default=0)
        return {"ok": True, "tx_mtime": mt, "count": len(segs)}

    def _save_live_edits(self, job, segs, base):
        """Corregir mientras se transcribe en directo: se guardan las diferencias con lo que dio Whisper
        y se aplican al escribir cada trozo nuevo, asi el directo no pisa lo corregido."""
        if not job.out:
            raise ApiError("El directo aún está empezando; prueba en unos segundos.")
        sent = {seg_key(a): t for a, _, t in segs if a is not None}
        last = max(sent) if sent else -1.0  # lo que llego despues aun no lo veias: no se toca
        with job.edit_lock:
            for a, _, t in job.raw:
                k = seg_key(a)
                if k in sent:
                    if sent[k] != " ".join(t.split()):
                        job.edits[k] = sent[k]
                    else:
                        job.edits.pop(k, None)
                elif k <= last:
                    job.edits[k] = ""  # la dejaste vacia: frase borrada
            job.out.write(apply_edits(job.raw, job.edits))
        self.library.at = 0
        mt = max((os.path.getmtime(base + e) for e in (".txt", ".srt") if os.path.isfile(base + e)), default=0)
        return {"ok": True, "tx_mtime": mt, "count": len(segs), "live": True}

    def restore_transcript(self, b):
        abs_path = safe(b.get("path"), "file")
        rel, base = rel_of(abs_path), os.path.splitext(abs_path)[0]
        bdir = backup_dir(rel)
        if not os.path.isdir(bdir):
            raise ApiError("No hay copia del original de esta transcripción.")
        if self.jobs.active_for(rel):
            raise ApiError("Se está transcribiendo; espera a que termine.")
        for ext in (".srt", ".txt"):
            src = os.path.join(bdir, "original" + ext)
            if os.path.isfile(src):
                with open(src, "rb") as f:
                    write_bytes(base + ext, f.read())
        shutil.rmtree(bdir, ignore_errors=True)
        self.library.at = 0
        self.jobs.schedule_outputs(rel)  # destinos de salida: el documento vuelve a la transcripcion original
        log(f"↺ {rel}: transcripción original restaurada")
        return {"ok": True}

    def doc(self, q):
        abs_path = safe(q.get("path"), "file")
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in TEXT_EXT:
            raise ApiError("Tipo de archivo no soportado")
        text = read_text(abs_path)
        out = {"name": os.path.basename(abs_path), "ext": ext, "text": text}
        if ext == ".srt":
            out["segments"] = parse_srt(text)
        return out

    def search(self, q):
        query = fold(q.get("q", "")).strip()
        terms = [t for t in query.split() if t]
        if not terms or len(query) < 2:
            return {"results": []}
        self.library.refresh(max_age=5)
        results = []
        for a in self.library.flat:
            data = self.transcripts.load(os.path.splitext(a["_abs"])[0]) if (a["txt"] or a["srt"]) else None
            hits = [s for s in data["segments"] if all(t in s["_f"] for t in terms)] if data else []
            if a.get("_sum"):  # y en el resumen con IA
                try:
                    hits = [{"start": None, "text": "✨ " + l} for l, f in self.transcripts.doc_lines(a["_sum"])
                            if all(t in f for t in terms) and not l.startswith(("---", "modelo:", "generado:"))] + hits
                except OSError:
                    pass
            if a.get("_md"):  # tambien en las notas y propiedades (titulo, etiquetas, participantes)
                try:
                    hits = [{"start": None, "text": "📝 " + l} for l, f in self.transcripts.doc_lines(a["_md"])
                            if all(t in f for t in terms) and l.strip() != "---"] + hits
                except OSError:
                    pass
            in_name = all(t in fold(a["path"]) for t in terms)
            if hits or in_name:
                results.append({"kind": "audio", "path": a["path"], "name": a["name"], "folder": a["folder"],
                                "date": a["date"], "label": a["label"], "count": len(hits),
                                "hits": [{"start": s["start"], "text": s["text"]} for s in hits[:6]]})
        tree, _ = self.library.refresh(max_age=5)
        stack = [tree]
        while stack:
            node = stack.pop()
            stack.extend(node["folders"])
            for d in node["docs"]:
                if d["size"] > 3_000_000:
                    continue
                try:
                    lines = self.transcripts.doc_lines(safe(d["path"]))
                except OSError:
                    continue
                hits = [l for l, f in lines if all(t in f for t in terms)]
                if hits:
                    results.append({"kind": "doc", "path": d["path"], "name": d["name"], "folder": node["path"],
                                    "count": len(hits), "hits": [{"start": None, "text": l[:300]} for l in hits[:6]]})
        results.sort(key=lambda r: -r["count"])
        return {"results": results[:100], "total": sum(r["count"] for r in results)}

    def jobs_list(self, q):
        return {"jobs": self.jobs.snapshot(), "movimientos": self.movimientos_publicos()}

    # --- POST
    def transcribe(self, b):
        paths = b.get("paths") or ([b["path"]] if b.get("path") else [])
        preset = b.get("preset") or self.settings.get()["preset"]
        ids = self.jobs.submit(paths, preset, b.get("language"), resume=bool(b.get("resume")))
        # el idioma elegido es de estas reuniones: el del perfil (Ajustes) no cambia, como al grabar
        self.settings.update(lambda d: d.update(preset=preset))
        return {"added": ids}

    def cancel(self, b):
        return {"ok": self.jobs.cancel(b.get("id"))}

    def start_live(self, b):
        if b.get("preset"):
            self.jobs._check_live_preset(b["preset"])
            self.settings.update(lambda d: d.update(live_preset=b["preset"]))
        return {"id": self.jobs.start_live(b.get("path"), b.get("preset"))}

    def live_model(self, b):
        return self.jobs.set_live_preset(b.get("id"), b.get("preset"), bool(b.get("restart")))

    def live_translate(self, b):
        """Traducción al español del directo en la nube, por reunión (apagada por defecto); live.py hace el resto."""
        job = self.jobs._live_job(b.get("id"))
        if b.get("on"):  # sin servicio de chat (Azure OpenAI) no hay traduccion: se dice, no falla a medias
            why = engines.chat_missing(self.settings.get()["cloud"], "La traducción del directo")
            if why:
                raise ApiError(why)
        job.translate = bool(b.get("on"))
        if isinstance(job.directo, dict):  # que la respuesta ya lo refleje (live.py lo vuelve a publicar en <1 s)
            job.directo = dict(job.directo, translate=job.translate)
        return job.public()

    def clear_jobs(self, b):
        self.jobs.clear()
        return {"ok": True}

    def _move_lock(self):
        """Un solo movimiento de reuniones a la vez (el de la interfaz y los pendientes al terminar)."""
        lock = self.__dict__.get("move_lock")
        if lock is None:
            lock = self.__dict__.setdefault("move_lock", threading.RLock())
        return lock

    def _grabando(self, rel):
        """Esa reunion se esta grabando (con la grabadora de la app o con OBS)."""
        r = getattr(self, "rec", None)
        if r and r.alive() and os.path.normcase(rel_of(r.path)) == os.path.normcase(rel):
            return True
        find = getattr(getattr(self, "library", None), "find", None)
        a = find(rel) if find else None
        return bool(a and a.get("recording"))

    def _ocupada(self, rel):
        """Por que no se pueden mover ahora los archivos de esa reunion (una frase), o None: se esta grabando,
        transcribiendo (directo y repaso final incluidos), identificando sus voces o resumiendo, o tiene el repaso
        final pendiente de un cierre de la app. Con None, nada de la app va a escribir en sus archivos."""
        if self._grabando(rel):
            return "Se está grabando esta reunión"
        return self.jobs.busy_for(rel)

    def move(self, b):
        src = safe(b.get("path"), "file")
        dest_dir = safe(b.get("folder", ""), "dir")
        if in_app_dir(dest_dir):
            raise ApiError("No se puede mover ahí")
        stem, ext = os.path.splitext(os.path.basename(src))
        new_stem = clean_name(b["name"]) if b.get("name") else stem
        if ext.lower() in AUDIO_EXT:
            why = self._ocupada(rel_of(src))
            if why and new_stem != stem:
                raise ApiError(f"{why}: podrás renombrarla cuando termine.")
            if why:  # se mueve sola al terminar (como «Cambiar» en la cabecera del directo)
                return self._destino_pendiente(rel_of(src), rel_of(dest_dir))
        own = own_meeting_dir(src) if ext.lower() in AUDIO_EXT else None
        # a la carpeta que contiene la suya, sin renombrarla: sacarla de su carpeta (se ve como carpeta porque tiene
        # algo mas dentro; lo demas se queda alli)
        fuera = bool(own) and new_stem == stem and _norm_dir(dest_dir) == _norm_dir(os.path.dirname(own))
        with self._move_lock():
            new = self._move_now(src, dest_dir, new_stem, fuera=fuera)
        if ext.lower() in AUDIO_EXT:
            self._movs().pop(rel_of(src))  # movida a mano: lo que estuviera pendiente ya no vale
        return {"path": new}

    def _move_now(self, src, dest_dir, new_stem=None, target=None, fuera=False):
        """Mueve (o renombra) una reunion con todo lo suyo a `dest_dir` y devuelve la ruta interna nueva del audio.
        Si vive en su carpeta (la creo la app para ella: own_meeting_dir) se mueve la carpeta entera, con lo que tenga
        dentro: queda dentro de `dest_dir` con su nombre (el de la reunion renombrada, o « (2)» si ya hay otra asi) y,
        si `dest_dir` es su propia carpeta, solo se renombra. Si no (o con `fuera`: sacarla de su carpeta), el audio y
        sus archivos hermanos, como siempre. Nunca pisa nada y o todo o nada (move_files). `target`: la carpeta de la
        reunion ya elegida (un movimiento pendiente que se retoma tras un cierre)."""
        stem, ext = os.path.splitext(os.path.basename(src))
        new_stem = new_stem or stem
        audio = ext.lower() in AUDIO_EXT
        own = own_meeting_dir(src) if audio and not fuera else None
        if own and _norm_dir(dest_dir) == _norm_dir(own):
            dest_dir = os.path.dirname(own)  # su propia carpeta: se renombra en su sitio
        if own and _inside(dest_dir, own):
            raise ApiError("No se puede mover una reunión dentro de su propia carpeta.")

        def renamed(f):  # lo que va detras del nombre (.txt, .srt, .md, .resumen.md): asi .resumen.md no acaba en .md
            return new_stem + os.path.basename(f)[len(stem):]
        base = os.path.dirname(src)
        sibs = [os.path.join(base, stem + e) for e in SIDE_EXTS
                if audio and os.path.isfile(os.path.join(base, stem + e))]
        if own:
            if target:
                new_dir = target
            else:
                name = os.path.basename(own)
                m = DATE_RE.match(new_stem)
                if new_stem != stem and m:  # renombrada: su carpeta, con el nombre nuevo
                    try:
                        when = time.strptime(m.group(0)[:10] + " " + m.group(0)[11:], "%Y-%m-%d %H-%M-%S")
                        name = fit_meeting_dir(dest_dir, new_stem[m.end():].strip(" -_·"), when, new_stem) or name
                    except ValueError:  # una fecha que no existe (2026-13-45): la carpeta se queda como estaba
                        pass
                new_dir = free_dir(dest_dir, name, mine=own)
            mine = {os.path.normcase(f) for f in [src] + sibs}
            moves = [(os.path.join(own, n), os.path.join(new_dir, n)) for n in sorted(os.listdir(own))
                     if os.path.normcase(os.path.join(own, n)) not in mine]
        else:
            new_dir, moves = dest_dir, []
        # el audio el ultimo: si algo falla a medias, la reunion sigue en su ruta (move_files deshace lo movido)
        moves += [(f, os.path.join(new_dir, renamed(f))) for f in sibs] + [(src, os.path.join(new_dir, renamed(src)))]
        seen = {}
        for f, t in moves:  # nada se pisa: ni lo que ya hay en el destino ni dos archivos que acabarian con un nombre
            k = os.path.normcase(t)
            if (k != os.path.normcase(f) and os.path.exists(t)) or seen.get(k, f) != f:
                raise ApiError("Ya existe un archivo con ese nombre: " + os.path.basename(t))
            seen[k] = f
        made = own is not None and not os.path.isdir(new_dir)
        if made:
            try:
                os.makedirs(new_dir)
            except OSError as e:
                raise ApiError(f"No se puede crear la carpeta {display_rel(rel_of(new_dir))} ({e.__class__.__name__}).")
        try:
            move_files(moves)
        except ApiError:
            if made:
                try:
                    os.rmdir(new_dir)
                except OSError:
                    pass
            raise
        old, new = rel_of(src), rel_of(moves[-1][1])
        reg = carpetas_reunion()
        if own and _norm_dir(new_dir) != _norm_dir(own):  # la carpeta de la reunion se ha quedado vacia
            try:
                os.rmdir(own)
            except OSError:
                log(f"   no se pudo quitar la carpeta vacía {rel_of(own)}")
            old_dir, new_dir_rel = rel_of(own), rel_of(new_dir)
            reg.discard(old_dir)
            reg.add(new_dir_rel)
            self._rename_refs(old_dir, new_dir_rel, folder=True)  # favoritos e icono de la carpeta
            if getattr(self, "settings", None) is not None:
                self.settings.update(lambda d: settings_moved(d, old_dir, new_dir_rel, folder=True))
        elif fuera and os.path.dirname(src) != dest_dir:  # sacada de su carpeta: esa ya no es de ninguna reunion
            reg.discard(rel_of(os.path.dirname(src)))
            try:
                os.rmdir(os.path.dirname(src))
            except OSError:
                pass
        self._moved_refs(old, new)
        log(f"↪ {old}  →  {new}")
        return new

    def _moved_refs(self, old, new):
        """Lo que la app guarda por ruta interna sigue a la reunion movida o renombrada: «completa», el origen de su
        texto, sus huellas de voz en memoria, su tema, favoritos e iconos, sus documentos de los destinos de salida y la
        copia del original. Las peticiones que lleguen con la ruta vieja van a la nueva (ALIASES)."""
        if old == new:
            return
        self.library.at = 0
        COMPLETE.moved(old, new)
        self.jobs._cloud_text().moved(old, new)  # el origen de su texto va con ella
        vv = getattr(self.jobs, "voice_vectors", None)
        if vv is not None and old in vv:
            vv[new] = vv.pop(old)
        try:  # su tema la sigue
            temas().moved(old, new)
        except OSError:
            log_exc()
        self._rename_refs(old, new)
        self.jobs.outputs_moved(old, new)  # sus documentos de los destinos siguen siendo suyos
        old_b, new_b = backup_dir(old), backup_dir(new)
        if os.path.isdir(old_b) and not os.path.exists(new_b):  # la copia del original viaja con el audio
            os.replace(old_b, new_b)
        with ALIASES_LOCK:
            ALIASES[old] = new
            for k in [k for k, v in ALIASES.items() if v == old]:  # A -> B -> C: A va directo a C
                ALIASES[k] = new
            ALIASES.pop(new, None)
        if getattr(self.jobs, "settings", None) is not None:  # el documento de los destinos dice de donde sale
            self.jobs.schedule_outputs(new)

    # --- cambiar la carpeta en pleno directo (B1c, msg 1198): se mueve al terminar todo
    MOV_ESPERAS = (3, 10, 30, 60, 120)  # segundos entre reintentos si un archivo esta en uso (OneDrive, antivirus)

    def _movs(self):
        """Los movimientos pendientes (Movimientos) del DATA_DIR vigente."""
        path = os.path.join(DATA_DIR, "movimientos_pendientes.json")
        reg = self.__dict__.get("_movs_reg")
        if reg is None or reg.path != path:
            reg = self.__dict__["_movs_reg"] = Movimientos(path)
        return reg

    def _donde_esta(self, abs_audio):
        """La carpeta principal donde esta ahora la reunion: la que contiene su carpeta (si la tiene) o la suya."""
        own = own_meeting_dir(abs_audio)
        return os.path.dirname(own) if own else os.path.dirname(abs_audio)

    def _destino_final(self, abs_audio, to_rel, target=None):
        """Donde acabara la reunion si se mueve a `to_rel`: su carpeta dentro (con « (2)» si ya hay otra) o la propia
        carpeta (una reunion sin carpeta suya)."""
        if target:
            return target
        own = own_meeting_dir(abs_audio)
        try:
            dest = safe(to_rel, "dir")
        except ApiError:
            return to_rel
        return rel_of(free_dir(dest, os.path.basename(own), mine=own)) if own else rel_of(dest)

    def _pendiente_publico(self, rel, e):
        try:
            abs_audio = safe(rel, "file")
        except ApiError:
            abs_audio = None
        dest = self._destino_final(abs_audio, e["to"], e.get("target")) if abs_audio else e["to"]
        return {"folder": e["to"], "destino": dest, "display": display_rel(dest), "error": e.get("error"),
                "intentos": e.get("intentos", 0), "por": e.get("por") or "carpeta"}

    def destino(self, b):
        """POST /api/reunion/destino {path, folder | folder_abs | null}: la carpeta principal a la que mover esta
        reunion. Si se esta grabando o procesando, se apunta y se mueve sola en cuanto no quede nada en marcha (directo,
        repaso, voces y resumen); si no, ahora. folder null (o la carpeta donde ya esta) = Deshacer. folder_abs: una
        carpeta del PC (el selector de Windows); si esta fuera de la app, se vincula como «Conectar carpeta»."""
        src = safe(b.get("path"), "file")
        if os.path.splitext(src)[1].lower() not in AUDIO_EXT:
            raise ApiError("Solo se puede cambiar la carpeta de una reunión.")
        rel = rel_of(src)
        folder = b.get("folder")
        if b.get("folder_abs"):
            p = os.path.normpath(os.path.expandvars(str(b["folder_abs"]).strip().strip('"')))
            inside = _inside(p, ROOT) or any(_inside(p, l["path"]) for l in LINKS.values())
            folder = rel_of(p) if inside and os.path.isdir(p) else self.link_add({"path": p})["rel"]
        if folder is None:
            if self._movs().pop(rel) is not None:
                log(f"📁 {rel}: ya no se mueve (deshecho)")
            return {"path": rel, "pendiente": None}
        return self._destino_pendiente(rel, str(folder).replace("\\", "/").strip("/"))

    def _destino_pendiente(self, rel, folder_rel):
        src = safe(rel, "file")
        dest = safe(folder_rel, "dir")
        if in_app_dir(dest):
            raise ApiError("No se puede mover ahí")
        if _norm_dir(dest) == _norm_dir(self._donde_esta(src)):  # la carpeta donde ya esta: nada que mover
            if self._movs().pop(rel) is not None:
                log(f"📁 {rel}: ya no se mueve (deshecho)")
            return {"path": rel, "pendiente": None}
        own = own_meeting_dir(src)
        if own and _inside(dest, own):
            raise ApiError("No se puede mover una reunión dentro de su propia carpeta.")
        with self._move_lock():
            if not self._ocupada(rel):  # ya no se escribe en ella: se mueve ahora
                self._movs().pop(rel)
                new = self._move_now(src, dest)
                self._nota_movida(rel, new)
                return {"path": new, "pendiente": None, "movida": True}
            e = {"to": rel_of(dest), "at": time.time()}
            self._movs().set(rel, e)
        log(f"📁 {rel}: se moverá a {e['to']} al terminar")
        return {"path": rel, "pendiente": self._pendiente_publico(rel, e)}

    def _al_quedar_libre(self, rel):
        """Jobs.on_idle: la reunion ha terminado su ultimo trabajo. Si tiene un movimiento pendiente, se hace en otro
        hilo (no se para la cola mientras se mueve un audio grande)."""
        if self._movs().get(rel):
            threading.Thread(target=self._aplicar_movimientos, args=(rel,), daemon=True, name="mover").start()

    def _aplicar_movimientos(self, only=None):
        """Mueve las reuniones con un movimiento pendiente que ya han quedado libres (o solo `only`). Lo llaman el final
        de cada trabajo (on_idle), la parada de la grabacion y la vuelta de 3 s (on_tick), que tambien hace los que se
        quedaron de antes de un cierre de la app y reintenta los que fallaron (con espera: MOV_ESPERAS)."""
        movs = self._movs()
        pend = [(r, e) for r, e in movs.items() if only is None or r == only]
        if not pend:
            return 0
        lock = self._move_lock()
        if not lock.acquire(blocking=False):
            return 0  # otro hilo esta moviendo: la vuelta siguiente (3 s) lo vuelve a mirar
        n = 0
        try:
            for rel, e in pend:
                if e.get("next", 0) > time.time() or self._ocupada(rel):
                    continue
                n += self._mover_pendiente(rel, e)
        finally:
            lock.release()
        return n

    def _mover_pendiente(self, rel, e):
        movs = self._movs()
        try:
            src = safe(rel, "file")
        except ApiError:
            src = None
        target = e.get("target")
        if src is None and target:  # la app se cerro a medio mover: el audio ya esta en la suya y falta lo demas
            try:
                moved = os.path.join(safe(target, "dir"), os.path.basename(rel))
            except ApiError:
                moved = None
            if moved and os.path.isfile(moved):
                movs.pop(rel)
                carpetas_reunion().discard(rel.rpartition("/")[0])
                carpetas_reunion().add(target)
                self._moved_refs(rel, rel_of(moved))
                self._nota_movida(rel, rel_of(moved))
                return 1
        if src is None:
            movs.pop(rel)
            log(f"   movimiento pendiente de {rel}: la reunión ya no está ahí, se olvida")
            return 0
        try:
            dest = safe(e["to"], "dir")
            if in_app_dir(dest):
                raise ApiError("No se puede mover ahí")
        except ApiError:
            movs.pop(rel)
            msg = f"La carpeta {display_rel(e['to'])} ya no existe: la reunión se queda donde estaba."
            log(f"   movimiento pendiente de {rel}: la carpeta {e['to']} ya no existe, se queda donde está")
            self._nota_movida(rel, None, msg)
            notify("No se ha movido la reunión", msg)
            return 0
        own = own_meeting_dir(src)
        if own:
            # la carpeta de la reunion en el destino, apuntada mientras se mueve: si la app se cierra a medias, el
            # siguiente intento termina en la misma. Un intento que falla lo deshace todo (move_files) y la olvida
            target = target or rel_of(free_dir(dest, os.path.basename(own), mine=own))
            if _norm_dir(safe(target)) != _norm_dir(own):
                e["target"] = target
                movs.set(rel, e)
        try:
            new = self._move_now(src, dest, target=safe(target) if own else None)
        except ApiError as err:
            e.pop("target", None)
            n = int(e.get("intentos") or 0) + 1
            wait = self.MOV_ESPERAS[min(n, len(self.MOV_ESPERAS)) - 1]
            e.update(intentos=n, error=err.msg, next=time.time() + wait)
            movs.set(rel, e)
            if n == 1 or n % 10 == 0:
                log(f"   movimiento pendiente de {rel}: {err.msg} Se reintenta en {wait} s")
            if n == 5:
                notify("No se ha podido mover la reunión", f"{err.msg} Se sigue intentando.")
            return 0
        movs.pop(rel)
        self._nota_movida(rel, new)
        notify("Reunión movida", "→ " + display_rel(rel_of(os.path.dirname(safe(new)))))
        return 1

    def _nota_movida(self, old, new, error=None):
        """Lo que la interfaz avisa al terminar (y con lo que la pagina de la reunion pasa a su ruta nueva)."""
        notas = self.__dict__.setdefault("_movidas", [])
        carpeta = rel_of(os.path.dirname(safe(new))) if new else None
        notas.append({"de": old, "a": new, "carpeta": carpeta, "display": display_rel(carpeta) if new else None,
                      "error": error, "at": time.time()})
        del notas[:-20]

    def movimientos_publicos(self):
        """Para /api/jobs: los pendientes (ruta -> a donde, y el error del ultimo intento) y los hechos hace <10 min."""
        now = time.time()
        return {"pendientes": {r: self._pendiente_publico(r, e) for r, e in self._movs().items()},
                "hechos": [n for n in self.__dict__.get("_movidas", []) if now - n["at"] < 600]}

    def mkdir(self, b):
        parent = safe(b.get("parent", ""), "dir")
        p = os.path.join(parent, clean_name(b.get("name")))
        if os.path.exists(p):
            raise ApiError("Ya existe esa carpeta")
        os.makedirs(p)
        self.library.at = 0
        return {"path": rel_of(p)}

    def rename_folder(self, b):
        if is_link_root(b.get("path")):  # carpeta vinculada: solo cambia el nombre que se ve en la app
            lid = b["path"].strip("/")[len(LINK):]
            if lid not in LINKS:
                raise ApiError("Esa carpeta ya no está vinculada.")
            old_name, new_name = LINKS[lid]["name"], clean_name(b.get("name"))
            LINKS[lid]["name"] = new_name
            self._save_links()
            if has_vocab_dir(LINKS[lid]["path"]):  # sus listas toman el nombre de la carpeta vinculada
                self.settings.update(lambda d: settings_moved(d, "", "", project=(old_name, new_name)))
                try:  # y el proyecto de sus reuniones (el nombre ya ha cambiado: si esto falla se anota)
                    self._themes_moved(LINK + lid, (old_name, new_name))
                except Exception:
                    log_exc()
            return {"path": b["path"].strip("/")}
        src = safe(b.get("path"), "dir")
        if os.path.normcase(src) == os.path.normcase(ROOT) or in_app_dir(src):
            raise ApiError("Esa carpeta no se puede renombrar")
        rel = rel_of(src)
        if any(j["path"].startswith(rel + "/") and j["status"] in ("queued", "running") for j in self.jobs.snapshot()):
            raise ApiError("Hay audios de esta carpeta transcribiéndose; espera a que terminen.")
        if self.rec and self.rec.alive() and rel_of(self.rec.path).startswith(rel + "/"):
            raise ApiError("Se está grabando dentro de esta carpeta; para la grabación antes de renombrarla.")
        dst = os.path.join(os.path.dirname(src), clean_name(b.get("name")))
        if os.path.normcase(dst) != os.path.normcase(src) and os.path.exists(dst):
            raise ApiError("Ya existe una carpeta con ese nombre")
        os.rename(src, dst)
        self.jobs._cloud_text().moved(rel_of(src), rel_of(dst), folder=True)  # el origen del texto de sus reuniones
        self._rename_refs(rel_of(src), rel_of(dst), folder=True)
        try:  # la carpeta ya esta renombrada: si algo de esto falla se anota, pero no se devuelve error
            self._folder_moved(rel_of(src), rel_of(dst), (os.path.basename(src), os.path.basename(dst))
                               if has_vocab_dir(dst) else None)
        except Exception:
            log_exc()
        self.library.at = 0
        return {"path": rel_of(dst)}

    def _folder_moved(self, old, new, project=None):
        """Una carpeta renombrada: lo que la app guarda por ruta interna la sigue, como al mover una reunion (move):
        la copia del original de cada transcripcion corregida, el estado «completa», las huellas de voz en memoria, el
        tema de cada reunion (temas()), los documentos de los destinos de salida y los ajustes (carpeta y tema del
        perfil, ultima carpeta y ultimo tema, carpeta de cada proyecto; con `project`, el proyecto tambien cambia de
        nombre, tambien en la cabecera de sus reuniones: _themes_moved)."""
        dst = safe(new, "dir")
        for d, _, files in os.walk(dst):
            for n in files:
                if os.path.splitext(n)[1].lower() not in AUDIO_EXT:
                    continue
                r_new = rel_of(os.path.join(d, n))
                r_old = moved_rel(r_new, new, old, folder=True)
                if r_old is None:
                    continue
                old_b, new_b = backup_dir(r_old), backup_dir(r_new)
                if os.path.isdir(old_b) and not os.path.exists(new_b):
                    os.replace(old_b, new_b)
        for r in [r for r in list(COMPLETE.data) if moved_rel(r, old, new, folder=True)]:
            COMPLETE.moved(r, moved_rel(r, old, new, folder=True))
        vv = self.jobs.voice_vectors
        for r in [r for r in list(vv) if moved_rel(r, old, new, folder=True)]:
            vv[moved_rel(r, old, new, folder=True)] = vv.pop(r)
        try:  # el tema de sus reuniones y el id de sus listas
            temas().moved(old, new, folder=True)
        except OSError:
            log_exc()
        self.jobs.outputs_moved(old, new, folder=True)
        self.settings.update(lambda d: settings_moved(d, old, new, folder=True, project=project))
        self._movs().moved(old, new, folder=True)  # los movimientos pendientes: la reunion y la carpeta de destino
        carpetas_reunion().moved(old, new, folder=True)  # y las carpetas de reunion que hay dentro
        self._themes_moved(new, project)

    def _themes_moved(self, new, project=None):
        """Al renombrar la carpeta de un proyecto (o la vinculada, que da su nombre a las listas), las reuniones de ese
        proyecto lo siguen en la cabecera de su .md, esten donde esten (theme_moved): si no, al volver a transcribirlas
        se quedaban con el vocabulario general, porque nunca se toma la lista de otro proyecto. El id de las que lo
        tenian ya lo ha seguido temas(); las que no, se quedan con el de la lista de su tema en la carpeta renombrada.
        Si el nombre nuevo ya es el de otro proyecto (una vinculada que se llama igual), las reuniones de ese otro sin
        id se quedan con el de la suya. Si no, con dos proyectos que se llaman igual, los nombres de la cabecera ya no
        dicen cual es: las de fuera de las dos carpetas pasaban al vocabulario general y las del otro que estan dentro
        de la renombrada tomaban su lista. `new`: la ruta interna de la carpeta, ya renombrada. Devuelve cuantas
        cabeceras han cambiado."""
        if not project:
            return 0  # ningun proyecto cambia de nombre (el de una lista es el de la carpeta que tiene Vocabulario)
        lists = {v["id"]: v for v in list_vocab()}
        ambiguous = any(fold(v["project"]) == fold(project[0]) for v in lists.values())
        own, others = {}, {}  # tema -> ids: las listas del proyecto renombrado y las de otros que ya se llamaban asi

        def renamed(vid):
            return moved_rel(vid, new, new, folder=True) is not None

        for v in lists.values():
            if v["folder"] == new:
                own.setdefault(fold(v["name"]), []).append(v["id"])
            elif fold(v["project"]) == fold(project[1]) != fold(project[0]) and not renamed(v["id"]):
                others.setdefault(fold(v["name"]), []).append(v["id"])
        store = temas()
        self.library.at = 0
        self.library.refresh()
        n = 0
        for a in list(self.library.flat):
            if not a.get("_md") or not (a.get("props") or {}).get("workstream"):
                continue
            with self.notes_lock:
                try:
                    props, body = parse_notes(read_text(a["_md"]))
                    vid = store.get(a["path"])
                    ch = theme_moved(props, vid, new, lists, project, ambiguous, renamed(a["path"]))
                    ids = []
                    if ch:
                        save_text(a["_md"], dump_notes(dict(props, **ch), body), "utf-8")
                        n += 1
                        ids = own.get(fold(props["workstream"])) or []
                    elif others and fold(props.get("proyecto") or "") == fold(project[1]) and (
                            vid not in lists or renamed(vid)):  # su id, si lo tiene, era de una lista que se llamaba
                        ids = others.get(fold(props["workstream"])) or []  # distinto de su cabecera
                    if len(ids) == 1:
                        store.set(a["path"], ids[0])
                except OSError:
                    log_exc()
        if n:
            self.library.at = 0
            log(f"   tema de {n} reuniones: siguen a la carpeta renombrada {new}")
        return n

    def reveal(self, b):
        p = safe(b.get("path", ""))
        if WINDOWS:
            if os.path.isdir(p):
                subprocess.Popen(["explorer", p])
            else:
                subprocess.Popen(f'explorer /select,"{p}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p] if os.path.isfile(p) else ["open", p])
        else:
            subprocess.Popen(["xdg-open", p if os.path.isdir(p) else os.path.dirname(p)])
        return {"ok": True}

    def open_file(self, b):
        p = safe(b.get("path"))
        if b.get("ext"):
            p = os.path.splitext(p)[0] + b["ext"]
        ext = os.path.splitext(p)[1].lower()
        if not os.path.isfile(p) or ext not in TEXT_EXT | AUDIO_EXT:
            raise ApiError("No se puede abrir ese archivo")
        if WINDOWS:
            os.startfile(p)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", p])
        return {"ok": True}

    def save_settings(self, b):
        perfil = (check_perfil(b["perfil"], perfil_de(self.settings.get())["outputs"])  # valida antes de guardar
                  if isinstance(b.get("perfil"), dict) else {})
        antes = procesado_de(self.settings.get())

        def upd(d):
            for k in ("preset", "language", "prompt"):
                if k in b:
                    d[k] = b[k] or ""
            s = b.get("summary") or {}
            if "auto" in s:
                d["summary_auto"] = bool(s["auto"])
            if "local" in s:  # consentimiento: resumir solo tambien lo transcrito en local (manda el texto a la nube)
                d["summary_auto_local"] = bool(s["local"])
            if s.get("aviso_visto") or s.get("local"):  # el aviso de Inicio, cerrado (o ya no hace falta)
                d["aviso_resumen_local"] = False
            if s.get("model"):
                d["summary_model"] = s["model"].strip()
            if s.get("lang") in engines.SUMMARY_LANGS:
                d["summary_lang"] = s["lang"]
            if "extra" in s:
                d["summary_extra"] = (s["extra"] or "").strip()[:500]
            if "meeting_detect" in b:
                d["meeting_detect"] = bool(b["meeting_detect"])
            for k in ("outlook_on", "rec_aviso"):  # reuniones: calendario de Outlook y aviso flotante
                if k in b:
                    d[k] = bool(b[k])
            if "live_auto" in b:
                d["live_auto"] = bool(b["live_auto"])
            if "carpeta_por_reunion" in b:  # una carpeta por reunion (encendida por defecto)
                d["carpeta_por_reunion"] = b["carpeta_por_reunion"] is not False
            if "live_final_pass" in b:
                d["live_final_pass"] = bool(b["live_final_pass"])
            if b.get("live_preset") in LIVE_PRESETS:
                d["live_preset"] = b["live_preset"]
            if "threads" in b:
                d["threads"] = max(1, min(int(b["threads"] or default_threads()), os.cpu_count() or 16))
            if perfil:
                update_perfil(d, perfil)
            if "worker_keepalive_min" in b:  # 0 = como antes (5 min); tope 12 h
                d["worker_keepalive_min"] = max(0, min(int(b["worker_keepalive_min"] or 0), 720))
            if "worker_preload" in b:
                d["worker_preload"] = bool(b["worker_preload"])
            if b.get("procesado") in PROCESADOS:
                prev = procesado_de(d)
                d["procesado"] = b["procesado"]
                if d["procesado"] != "api":  # sin nube: los motores de la cola y del directo vuelven a ser locales
                    if prev == "api":  # los que se usaban con la API: si se vuelve a ella, vuelven (ida y vuelta)
                        d["motores_api"] = {k: d.get(k) for k in ("preset", "live_preset")}
                    for k, local in (("preset", "rapido"), ("live_preset", "medium")):
                        if (engines.PRESETS_BY_ID.get(d.get(k)) or {}).get("engine") == "cloud":
                            d[k] = local
                elif prev != "api" and (d["cloud"].get("api_key") or "").strip():
                    # y al reves (07/11/2026): a «API externa» con la clave ya puesta, los motores de cuando se uso la
                    # API (o la nube, como con la primera clave); si no, se elegia la API y se seguia transcribiendo con
                    # Whisper en el PC. Lo que se elija a la vez en esta misma peticion manda
                    antes_api = d.get("motores_api") if isinstance(d.get("motores_api"), dict) else {}
                    for k, validos in (("preset", engines.PRESETS_BY_ID), ("live_preset", LIVE_PRESETS)):
                        if k not in b:
                            d[k] = antes_api.get(k) if antes_api.get(k) in validos else "nube"
            ag = b.get("agente") if isinstance(b.get("agente"), dict) else None
            if ag:
                cur = d.get("agente") if isinstance(d.get("agente"), dict) else {}
                cls = (engines.AGENTES.get(ag.get("tipo")) or engines.AGENTES.get(cur.get("tipo"))
                       or engines.AGENTES["claude"])
                modelo = ag.get("modelo") if ag.get("modelo") in dict(cls.MODELOS) else cls.MODELO
                d["agente"] = {"tipo": cls.tipo, "modelo": modelo}
            c = b.get("cloud") or {}
            for k in ("provider", "model", "base_url", "azure_endpoint", "azure_deployment", "azure_api_version"):
                if k in c:
                    d["cloud"][k] = (c[k] or "").strip()
            # 20 min de FLAC 16 kHz ~ 22 MB: por debajo del limite de 25 MB por archivo de las APIs
            for k, lo, hi in (("chunk_minutes", 2, 20), ("parallel", 1, 8)):
                if k in c:
                    d["cloud"][k] = max(lo, min(int(c[k] or lo), hi))
            if c.get("api_key") and "…" not in c["api_key"] and "•" not in c["api_key"]:
                if not d["cloud"].get("api_key") and procesado_de(d) == "api":  # primera key: la nube, predeterminada
                    d["preset"] = d["live_preset"] = "nube"
                d["cloud"]["api_key"] = c["api_key"].strip()
            if c.get("clear_key"):
                d["cloud"]["api_key"] = ""
            for k in ("live_model", "live_model_custom"):  # el del directo en Groq y el de una pasarela propia
                if k in c:
                    d["cloud"][k] = (c[k] or "").strip()[:80]
            # claves adicionales: cada lista es de su proveedor (las de Groq, en extra_keys). La interfaz manda las de
            # cada proveedor en extra_keys_by; extra_keys a secas son las del proveedor que se guarda. Las ya guardadas
            # vuelven enmascaradas y se conservan, pero solo dentro de su proveedor: nunca pasan a otro
            lists = c.get("extra_keys_by") if isinstance(c.get("extra_keys_by"), dict) else {}
            if not lists and isinstance(c.get("extra_keys"), list):
                lists = {d["cloud"].get("provider") or "groq": c["extra_keys"]}
            for prov, incoming in lists.items():
                field = engines.extra_keys_field(prov)
                if not field or not isinstance(incoming, list):
                    continue
                old = {masked(k): k for k in d["cloud"].get(field) or []}
                new = [old.get(k.strip(), k.strip()) for k in incoming if isinstance(k, str) and k.strip()]
                d["cloud"][field] = [k for k in new if "…" not in k and "•" not in k][:10]
        self.settings.update(upd)
        p = (getattr(self, "pruebas", None) or {}).get(b.get("prueba")) if b.get("prueba") else None
        if p is not None and p.terminada and p.firma == self._firma(self.settings.get()):
            res = p.resumen()  # la del asistente: es de lo que se acaba de guardar
            self.settings.update(lambda d: d.update(ultima_prueba=res))
        ahora = procesado_de(self.settings.get())
        if ahora != antes:
            log(f"   dónde se procesan las reuniones: {antes} → {ahora}")
        if antes == "api" and ahora != "api":
            self.jobs.nube_apagada()  # lo que estuviera en la nube no sigue
        # recien elegido un modo que transcribe en este PC: el modelo del directo, ya, con su progreso en Ajustes (solo
        # al cambiar de modo: «Guardar» con el mismo no vuelve a empezar una descarga que se cancelo)
        descarga = self._descargar_modelo_del_directo() if ahora != antes and ahora in ("local", "agente") else None
        return dict(self.config({}), descarga=descarga)

    def agente_comprobar(self, b):
        """Ajustes → «Comprobar»: el agente esta instalado, responde a --version y contesta una peticion minima con la
        cuenta del usuario. Guarda la version y la hora para ensenarlas en Ajustes."""
        st = self.settings.get()
        cls, a = agente_de(dict(st, agente=b if isinstance(b, dict) and b.get("tipo") else st.get("agente")))
        modelo = b.get("modelo") if isinstance(b, dict) and b.get("modelo") in dict(cls.MODELOS) else a["modelo"]
        ruta = cls.find()
        if not ruta:
            raise ApiError(f"No encuentro {cls.label} en este PC. Haz los pasos 1 y 2 y vuelve a pulsar «Comprobar».")
        # lo que tarda cada paso, al log (07/11/2026: en el PC de Bernardo «Comprobar» tardó minutos sin decir en que)
        log(f"   agente: comprobando {cls.label} ({modelo})…")
        ag, t0 = cls(settings_dir=DATA_DIR), time.time()
        try:
            version, secs = ag.check(ruta, model=modelo)
        except engines.EngineError as e:
            log(f"   agente: {cls.label}: {e} ({self._agente_tiempos(ag, time.time() - t0)})")
            raise ApiError(explicar.explicar(e)["frase"])
        t = getattr(ag, "tiempos", None)
        if isinstance(t, dict) and t:
            log(f"   agente: {self._agente_tiempos(ag, secs)}")
        self.settings.update(lambda d: d.update(agente_version=version, agente_comprobado=time.time()))
        log(f"   agente: {cls.label} {version} comprobado ({secs:.1f} s)")
        return {"ok": True, "version": version, "seconds": round(secs, 1), "ruta": ruta}

    @staticmethod
    def _agente_tiempos(ag, total):
        """«--version 2.1 s · petición mínima 41.0 s · total 43.1 s» (lo que se sepa de cada paso)."""
        t = getattr(ag, "tiempos", None) or {}
        partes = [f"--version {t['version_s']} s" if "version_s" in t else None,
                  f"petición mínima {t['peticion_s']} s" if "peticion_s" in t else None, f"total {total:.1f} s"]
        return " · ".join(p for p in partes if p)

    def test_cloud(self, b):
        cloud = self.settings.get()["cloud"]
        try:
            secs = self.jobs.cloud.test(cloud)
        except engines.EngineError as e:
            prov = (engines.PROVIDERS.get(cloud.get("provider") or "groq") or {}).get("label")
            raise ApiError(explicar.explicar(e, proveedor=prov)["frase"])
        return {"ok": True, "seconds": round(secs, 2)}

    # --- «Probar» del asistente «Configurar Chuleta» (probar.py): prueba real con lo que se está eligiendo, sin guardar
    def _candidata(self, b):
        """(ajustes con lo elegido en el asistente encima, sin guardarlos; True si hay algo elegido). La clave nueva
        solo si es una de verdad (no la máscara que enseña Ajustes)."""
        st = copy.deepcopy(self.settings.get())
        c = (b or {}).get("candidata")
        if not isinstance(c, dict):
            return st, False
        if c.get("procesado") in PROCESADOS:
            st["procesado"] = c["procesado"]
        ag = c.get("agente")
        if isinstance(ag, dict):
            cls = engines.AGENTES.get(ag.get("tipo")) or agente_de(st)[0]
            st["agente"] = {"tipo": cls.tipo,
                            "modelo": ag.get("modelo") if ag.get("modelo") in dict(cls.MODELOS) else cls.MODELO}
        cl = c.get("cloud") if isinstance(c.get("cloud"), dict) else {}
        st["cloud"] = dict(st.get("cloud") or {})
        for k in ("provider", "model", "base_url", "azure_endpoint", "azure_deployment", "azure_api_version"):
            if k in cl:
                st["cloud"][k] = (cl[k] or "").strip()
        key = (cl.get("api_key") or "").strip()
        if key and "…" not in key and "•" not in key:
            st["cloud"]["api_key"] = key
        return st, True

    @staticmethod
    def _prueba_motores(st):
        """(preset del directo, [modelos de Whisper que hacen falta]) de esa configuración, sin «API externa» siempre
        en el PC: el del directo y el de la cola (los dos se descargan juntos, decisión de Claudia 07/11/2026)."""
        out = []
        for key, local in (("live_preset", "medium"), ("preset", "rapido")):
            p = engines.PRESETS_BY_ID.get(st.get(key) or local)
            if not p or p["engine"] != "local":
                p = engines.PRESETS_BY_ID[local]
            out.append(p)
        return out[0], list(dict.fromkeys(p["model"] for p in out))

    def _prueba_hacer(self, st):
        """Las funciones de verdad para probar.Prueba (micro, Whisper, nube, Claude) con esos ajustes."""
        modo = procesado_de(st)
        directo, modelos_pc = self._prueba_motores(st)
        tmp = os.path.join(DATA_DIR, "tmp")
        os.makedirs(tmp, exist_ok=True)

        def modelos(report, cancelled):
            if engines.MODELS is None:
                return
            names = [m for m in modelos_pc if m in engines.WHISPER_MODELS]
            for m in names:
                if engines.MODELS.path(m) is None:
                    engines.MODELS.start(m)
            while True:
                sts = [engines.MODELS.state(m) for m in names]
                if all(x["ready"] for x in sts):
                    return
                errs = [x["error"] for x in sts if x["error"] and not x["downloading"] and not x["ready"]
                        and "cancelada" not in x["error"].lower()]
                if errs:
                    raise engines.EngineError(errs[0])
                for m, x in zip(names, sts):
                    if not x["ready"] and not x["downloading"]:
                        engines.MODELS.start(m)  # sin empezar o cancelada en Ajustes: la prueba la necesita
                total = sum(x["total_mb"] or x["size_mb"] or 0 for x in sts)
                done = sum((x["total_mb"] or x["size_mb"] or 0) if x["ready"] else (x["done_mb"] or 0) for x in sts)
                eta = max((x["eta_s"] or 0) for x in sts)
                gb = lambda mb: f"{mb / 1000:.1f}".replace(".", ",")  # noqa: E731
                report(f"Descargando · {int(100 * done / total) if total else 0} % · {gb(done)} de {gb(total)} GB"
                       + (f" · quedan unos {engines.fmt_left(eta)}" if eta else ""))
                if cancelled():
                    raise probar.Cancelada()
                time.sleep(0.5)

        def grabar(segundos, report, cancelled):
            if self.rec and self.rec.alive():
                raise engines.EngineError("Estás grabando: prueba cuando termines la grabación.")
            with self.rec_lock:
                if self.monitor:
                    self.monitor.stop(timeout=2)
                    self.monitor = None
            path = os.path.join(tmp, "prueba-micro.wav")
            try:
                os.remove(path)
            except OSError:
                pass
            report("Prepárate: en un momento empieza a grabar…")
            try:
                # con «Sin micrófono» guardado, la prueba usa el predeterminado de Windows
                mic = st.get("rec_mic") if st.get("rec_mic") not in (None, "", "none") else "default"
                r = RecorderProcess(path, mic, "none", APP_DIR, DATA_DIR)
            except Exception as e:  # sin Python para lanzarlo o sin permisos: la cadena con silencio
                return None, False, e.__class__.__name__
            fin = time.time() + 10
            while time.time() < fin and r.alive() and r.status.get("t") is None:
                if cancelled():
                    r.stop(timeout=2)
                    raise probar.Cancelada()
                time.sleep(0.1)
            if r.status.get("t") is None:
                r.stop(timeout=2)
                mic = r.status.get("mic") if isinstance(r.status.get("mic"), dict) else {}
                log(f"   probar: el micro no ha arrancado ({mic.get('error') or 'sin estado'})")
                return None, False, mic.get("error") or "el micro no ha arrancado"
            t0, fin = r.status.get("t") or 0, time.time() + segundos + 8  # un grabador colgado no para la prueba
            while r.alive() and (r.status.get("t") or 0) - t0 < segundos and time.time() < fin:
                if cancelled():
                    r.stop(timeout=2)
                    raise probar.Cancelada()
                quedan = max(1, int(segundos - ((r.status.get("t") or 0) - t0) + 0.99))
                report(f"Habla ahora: di «{probar.FRASE}» · {quedan} s")
                time.sleep(0.1)
            r.stop(timeout=5)
            mics = [m for _, m, _ in r.levels if m is not None]
            voz = sum(1 for m in mics if m > PRUEBA_VOZ_DB) >= 3  # ≥ 0,75 s con voz (niveles cada 0,25 s)
            log(f"   probar: micro {len(mics)} lecturas, máx {max(mics) if mics else '-'} dB, "
                f"voz: {'sí' if voz else 'no'}")
            return (path if os.path.isfile(path) else None), voz, None

        def cuenta(report, cancelled):
            cls, a = agente_de(st)
            ruta = cls.find()
            if not ruta:
                raise engines.EngineError(f"No encuentro {cls.label} en este PC. Sigue el paso a paso y vuelve a "
                                          "probar.")
            report(f"Comprobando {cls.label} con tu cuenta…")
            cls(settings_dir=DATA_DIR).check(ruta, model=a["modelo"], cancelled=cancelled)

        def silencio():
            import wave
            path = os.path.join(tmp, "prueba-silencio.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\0\0" * 16000)
            return path

        def transcribir(wav, report, cancelled):
            def rep(frac=None, stage=None, **k):
                report(stage)
            if modo == "api":
                import numpy as np
                y = engines.decode_16k(wav).astype(np.float32) / 32768.0
                f = os.path.join(tmp, "prueba.flac")
                engines.write_flac(f, y)
                r = self.jobs.cloud._post(st["cloud"], f, "es", None, rep, cancelled, attempts=2, max_wait=10.0)
                return (r.get("text") or "").strip()
            if getattr(self, "local_prueba", None) is None:
                self.local_prueba = engines.LocalEngine(DATA_DIR, "prueba", idle_s=60)
            segs, _, _ = self.local_prueba.transcribe(wav, directo, "es", None, st.get("threads") or default_threads(),
                                                      rep, cancelled)
            return " ".join(t for _, _, t in segs).strip()

        def resumir(texto, report, cancelled):
            def rep(stage=None, **k):
                report(stage)
            sistema = "Resume en una sola frase corta, en español, lo que dice este texto."
            if modo == "agente":
                cls, a = agente_de(st)
                out, _ = cls(settings_dir=DATA_DIR).chat(sistema, texto, 200, rep, cancelled, model=a["modelo"],
                                                         timeout=engines.AGENTE_CHECK_S)
                return out
            out, _ = engines.Summarizer(keys=self.jobs.cloud.keys)._chat(
                dict(st["cloud"], summary_model=st.get("summary_model")), sistema, texto, 200, rep, cancelled,
                timeout=60, attempts=2)
            return out

        prov = (engines.PROVIDERS.get(st["cloud"].get("provider") or "groq") or {}).get("label") or "la API"
        nombres = {"transcribe": f"con {prov}" if modo == "api" else "en este PC",
                   "resume": "Claude (tu cuenta)" if modo == "agente" else prov,
                   "proveedor": prov if modo == "api" else None}
        return {"cuenta": cuenta, "modelos": modelos, "grabar": grabar, "silencio": silencio,
                "transcribir": transcribir, "resumir": resumir}, nombres

    @staticmethod
    def _firma(st):
        """Lo que hace distinta una configuración para «Probar» (la clave, como huella): una prueba solo vale para la
        configuración que se probó (revisión del 07/11/2026: un «Listo» viejo sobrevivía a cambiar la clave)."""
        c = st.get("cloud") or {}
        key = (c.get("api_key") or "").strip()
        return (procesado_de(st), (st.get("agente") or {}).get("modelo"), c.get("provider"), c.get("model"),
                c.get("base_url"), c.get("azure_endpoint"), c.get("azure_deployment"),
                hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key else "")

    def probar_empezar(self, b):
        """POST /api/probar {candidata?}: empieza una prueba (una a la vez) y devuelve su id."""
        if self.rec and self.rec.alive():
            raise ApiError("Estás grabando: prueba cuando termines la grabación.")
        with self.rec_lock:
            cur = getattr(self, "prueba", None)
            if cur is not None and cur.is_alive():  # otra configuración (o la misma otra vez): la de antes se cancela
                cur.cancelar = True
            st, candidata = self._candidata(b)
            hacer, nombres = self._prueba_hacer(st)
            p = probar.Prueba(procesado_de(st), hacer, nombres, candidata=candidata, al_terminar=self._prueba_fin,
                              espera=cur if cur is not None and cur.is_alive() else None)
            p.firma = self._firma(st)
            if not hasattr(self, "pruebas"):
                self.pruebas = {}
            self.pruebas = {k: v for k, v in self.pruebas.items() if v.is_alive() or time.time() - v.empezada < 3600}
            self.pruebas[p.id] = self.prueba = p
            p.start()
        log(f"   probar: {procesado_de(st)}{' (sin guardar)' if candidata else ''}")
        return {"id": p.id}

    def _prueba_fin(self, p):
        r = p.public()
        log(f"   probar: {'bien' if r['listo'] else 'falla'} · " + " · ".join(
            f"{x['id']} {x['estado']} ({x['segundos']} s)" for x in r["pasos"])
            + (f" · {r['fallo']['tecnico'] or r['fallo']['frase']}" if r["fallo"] else ""))
        if p.nota_micro:
            log(f"   probar: micro: {p.nota_micro}")
        if not p.candidata:  # la de lo guardado (Ajustes › Probar): queda como última prueba
            res = p.resumen()
            self.settings.update(lambda d: d.update(ultima_prueba=res))

    def probar_estado(self, q):
        p = (getattr(self, "pruebas", None) or {}).get(q.get("id"))
        if p is None:
            raise ApiError("No encuentro esa prueba: vuelve a pulsar «Probar».")
        return p.public()

    def probar_cancelar(self, b):
        p = (getattr(self, "pruebas", None) or {}).get((b or {}).get("id"))
        if p is not None:
            p.cancelar = True
        return {"ok": True}

    def agente_estado(self, q):
        """Para el paso 2 del asistente: si Claude Code ya está en este PC (se mira cada pocos segundos)."""
        cls, _ = agente_de(self.settings.get())
        return {"encontrado": bool(cls.find()), "label": cls.label}

    def abrir_powershell(self, b):
        """«Abrir PowerShell» del asistente: una ventana vacía, sin ejecutar nada."""
        if os.name != "nt":
            raise ApiError("Este botón solo funciona en Windows: abre una terminal y sigue los pasos.")
        subprocess.Popen(["powershell.exe", "-NoExit", "-NoLogo"], cwd=os.path.expanduser("~"),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return {"ok": True}

    # --- modelos de Whisper en este PC (engines.WhisperModels): estado, descarga con progreso y cancelar
    @staticmethod
    def _modelos_en_uso(st):
        """modelo -> ["directo", "cola"]: los que usan el motor del directo y el de la cola si son locales."""
        uso = {}
        for key, what, default in (("live_preset", "directo", "medium"), ("preset", "cola", "rapido")):
            p = engines.PRESETS_BY_ID.get(st.get(key) or default)
            if p and p["engine"] == "local":
                uso.setdefault(p["model"], []).append(what)
        return uso

    def modelos_state(self, q=None):
        st = self.settings.get()
        uso, out = self._modelos_en_uso(st), []
        for p in engines.PRESETS:
            if p["engine"] != "local":
                continue
            m = p["model"]
            if engines.MODELS is not None:
                s = engines.MODELS.state(m)
            else:  # sin el gestor (no deberia pasar con la app arrancada): solo si esta o no
                s = {"model": m, "ready": engines.model_cached(m), "size_mb": None, "downloading": False,
                     "done_mb": 0, "total_mb": None, "frac": None, "speed_mbs": None, "eta_s": None, "error": None}
            out.append(dict(s, preset=p["id"], label=p["label"], live_label=p["live_label"], uso=uso.get(m, [])))
        return {"modelos": out, "procesado": procesado_de(st),
                "carpeta": engines.MODELS.root if engines.MODELS is not None else None}

    def modelos_descargar(self, b):
        m = str((b or {}).get("model") or "")
        if engines.MODELS is None or m not in engines.WHISPER_MODELS:
            raise ApiError(f"No sé descargar el modelo «{m}».")
        if engines.MODELS.path(m) is None:
            engines.MODELS.start(m)
            log(f"   modelo {m}: descarga pedida desde Ajustes")
        return self.modelos_state()

    def modelos_cancelar(self, b):
        m = str((b or {}).get("model") or "")
        if engines.MODELS is not None and engines.MODELS.cancel(m):
            log(f"   modelo {m}: descarga cancelada desde Ajustes")
        return self.modelos_state()

    def _descargar_modelo_del_directo(self):
        """Al pasar a «Solo en este PC» o «Con tu cuenta de Claude»: si falta el modelo del directo, se empieza a
        descargar ya, con su progreso en Ajustes (antes se descargaba al empezar la primera reunion, sin progreso).
        Devuelve el modelo que se ha puesto a descargar, o None."""
        st = self.settings.get()
        p = engines.PRESETS_BY_ID.get(st.get("live_preset") or "medium")
        if engines.MODELS is None or not p or p["engine"] != "local" or p["model"] not in engines.WHISPER_MODELS:
            return None
        if engines.MODELS.path(p["model"]) is not None or engines.MODELS.cancelled_by_user(p["model"]):
            return None  # ya esta, o el usuario cancelo su descarga en Ajustes (la retoma con «Descargar»)
        engines.MODELS.start(p["model"])
        log(f"   modelo {p['model']}: descarga empezada al elegir dónde se procesan las reuniones")
        return p["model"]

    # --- quien habla (voces)
    def voices_state(self, q=None):
        st = self.settings.get()
        on = bool(st.get("voices_enabled"))
        ok, why = voices.available() if on else (None, None)
        dl = dict(self.voices_dl) if self.voices_dl else None
        return {"enabled": on, "available": ok, "why": why,
                "models": {"ready": voices.models_ready(DATA_DIR), "downloading": bool(dl and dl.get("running")),
                           "frac": (dl or {}).get("frac"), "stage": (dl or {}).get("stage"),
                           "error": (dl or {}).get("error")},
                "personas": self.jobs.voices.public() if on else [],
                "usuario": (self.jobs.voices.user() or {}).get("id") if on else None,
                "store_error": self.jobs.voices.error if on else None, "umbral": voices.UMBRAL}

    def _download_models(self):
        def run():
            def report(stage=None, frac=None):
                if stage:
                    self.voices_dl["stage"] = stage
                if frac is not None:
                    self.voices_dl["frac"] = round(float(frac), 3)
            try:
                voices.ensure_models(DATA_DIR, report)
                log("   modelos de voces descargados")
            except Exception as e:
                self.voices_dl["error"] = str(e) or e.__class__.__name__
                log(f"✖ modelos de voces: {self.voices_dl['error']}")
            finally:
                self.voices_dl["running"] = False
        if self.voices_dl and self.voices_dl.get("running"):
            return
        self.voices_dl = {"running": True, "frac": 0.0, "stage": "Descargando modelos de voces…", "error": None}
        threading.Thread(target=run, daemon=True, name="voices-models").start()

    def voices_toggle(self, b):
        on = bool(b.get("on"))
        if on:
            ok, why = voices.available()
            if not ok:  # no se activa: la opcion no serviria de nada en este PC
                raise ApiError("La librería de voces (sherpa-onnx) no está instalada o no carga en este PC: vuelve a "
                               f"ejecutar INSTALAR.bat. Detalle: {why[:200]}")
        self.settings.update(lambda d: d.update(voices_enabled=on))
        if on and not voices.models_ready(DATA_DIR):
            self._download_models()
        log(f"🗣 voces {'activadas' if on else 'desactivadas'}")
        return self.voices_state()

    def voices_identify(self, b):
        return {"id": self.jobs.submit_voices(b.get("path"))}

    def speakers_get(self, q):
        abs_path = safe(q.get("path"), "file")
        data = self.jobs.speakers_for(abs_path)
        job = self.jobs.active_for(rel_of(abs_path), kinds=("voces",))
        if not data:
            return {"exists": False, "job": job.public() if job else None, "enabled": self.jobs.voices_on()}
        real = [i for i in data["hablantes"].values() if not i.get("breve")]
        return {"exists": True, "enabled": self.jobs.voices_on(), "job": job.public() if job else None,
                "hablantes": data["hablantes"], "tu": data.get("tu"), "creado": data.get("creado"),
                "frases": data.get("frases", []), "who": data.get("_who", []),
                "n": len(real), "known": sum(1 for i in real if i.get("persona") and not i.get("provisional")),
                "personas": self.jobs.voices.public(), "usuario": (self.jobs.voices.user() or {}).get("id")}

    def _speaker_check(self, abs_path, h, creado=None, data=None):
        """Antes de corregir un hablante: que exista, que no se esten identificando las voces de esa reunion (lo que
        se dijera ahora se perderia) y que la etiqueta se abriera sobre esta identificacion y no sobre una anterior
        (`creado` del sidecar: con otra, «h2» puede ser otra voz y «Recordar» guardaria la huella de otro)."""
        if self.jobs.active_for(rel_of(abs_path), kinds=("voces",)):
            raise ApiError("Se están identificando las voces de esta reunión: espera a que termine para corregirlas.")
        data = data or voices.read_sidecar(voices.sidecar_path(abs_path))
        if not data or h not in (data.get("hablantes") or {}):
            raise ApiError("Ese hablante ya no está en esta reunión (¿se volvió a identificar?).")
        if creado and data.get("creado") and creado != data["creado"]:
            raise ApiError("Las voces de esta reunión se han vuelto a identificar: abre otra vez la etiqueta.")
        return data

    def _not_the_user(self, rel, h, before, target):
        """El usuario dice que el hablante h (que la app tomaba por el: `before`) es otra persona o nadie: se quitan de
        su persona las huellas casi iguales a esa voz (la de otra persona guardada como suya). Si no se puede calcular
        la huella de h, no se toca nada."""
        v = self.jobs.voices
        uid = (v.user() or {}).get("id")
        if not uid or before != uid or target == uid or not (v.find(uid) or {}).get("huellas") \
                or not self.jobs.voices_on():
            return 0
        try:
            vec, _ = self.jobs.speaker_vector(rel, h)
        except Exception:
            return 0
        n = v.drop_similar(uid, vec)
        if n:
            log(f"🗣 {n} huella{'s' if n != 1 else ''} quitada{'s' if n != 1 else ''} de tu persona: era la voz de "
                "otro hablante")
        return n

    def _speaker_update(self, abs_path, h, fn, creado=None):
        side = voices.sidecar_path(abs_path)
        data = self._speaker_check(abs_path, h, creado)
        fn(data["hablantes"][h], data)
        voices.write_sidecar(side, data)
        self.library.at = 0
        self.jobs.schedule_outputs(rel_of(abs_path))  # destinos de salida: el documento lleva quien habla
        return data

    def _persona_for_speaker(self, v, b, nombre, consent):
        """Quien es un hablante segun lo que se pide desde su etiqueta: la persona del usuario («Soy yo»; escribir
        su nombre nunca: un compañero puede llamarse igual), una que ya se llama asi, la que tenia con el nombre
        corregido (solo con `renombrar`, y nunca la del usuario) o una nueva. Escribir un nombre distinto NO renombra
        a nadie: dice que es otra persona."""
        when = time.strftime("%Y-%m-%dT%H:%M:%S") if consent else None
        user = v.user()
        if b.get("es_usuario"):  # «Soy yo» no usa el cuadro (puede traer el nombre de otra persona): «Yo», y el
            return user["id"] if user else v.create("Yo", True, when)["id"]  # nombre se cambia en Ajustes
        if not nombre:
            raise ApiError("Escribe un nombre.")
        same = v.by_name(nombre)
        cur = v.find(b.get("id")) if b.get("id") else None
        if b.get("renombrar") and cur and not cur.get("es_usuario") and (not same or same["id"] == cur["id"]):
            if nombre != cur["nombre"]:  # corregir una errata: el nombre nuevo sale en todas sus reuniones
                v.rename(cur["id"], nombre)
                self.jobs.outputs_for_persona(cur["id"])
            return cur["id"]
        if same:
            return same["id"]
        return v.create(nombre, False, when)["id"]

    def voices_person(self, b):
        """Nombre, «Soy yo» y «Recordar» de un hablante (de una reunion: path + h) o de una persona ya guardada
        (Ajustes › Voces: id). Desde un hablante decide QUIEN es ese hablante (_persona_for_speaker); la persona del
        usuario solo cambia de nombre desde Ajustes y solo recibe huellas de un hablante que es el."""
        v = self.jobs.voices
        if b.get("recordar") and not self.jobs.voices_on():
            raise ApiError("Activa «Voces» en Ajustes ⚙ para guardar huellas.")
        pid, path, h = b.get("id"), b.get("path"), b.get("h")
        consent = b.get("consentimiento")
        abs_path = safe(path, "file") if path else None
        nombre = " ".join(str(b.get("nombre") or "").split())[:80]
        if pid and not v.find(pid):
            raise ApiError("Esa persona ya no existe.")
        if b.get("recordar"):  # antes de cambiar nada: sin consentimiento no se toca ni la persona ni la reunion
            if not consent:
                raise ApiError("Para guardar la huella hace falta marcar la casilla de consentimiento.")
            if not (abs_path and h):
                raise ApiError("Falta de qué reunión y hablante tomar la huella.")
        if abs_path and h:
            side = self._speaker_check(abs_path, h, b.get("creado"))  # antes de crear a nadie
            if b.get("recordar") and side.get("creado") and not b.get("creado"):  # guardar una huella: solo de la
                raise ApiError("Vuelve a abrir la etiqueta para guardar la huella.")  # identificacion que se ve
            before = side["hablantes"][h].get("persona")
            pid = self._persona_for_speaker(v, b, nombre, consent)
            uid = (v.user() or {}).get("id")
            self._speaker_update(abs_path, h, lambda info, d: voices.assign(d, h, pid, uid), b.get("creado"))
            self._not_the_user(rel_of(abs_path), h, before, pid)
        elif not pid:
            if not nombre and not b.get("es_usuario"):
                raise ApiError("Escribe un nombre.")
            when = time.strftime("%Y-%m-%dT%H:%M:%S") if consent else None
            pid = v.create(nombre or "Yo", bool(b.get("es_usuario")), when)["id"]
        else:  # Ajustes › Voces: nombre y «Soy yo» de una persona por su id
            renamed = False
            if nombre and nombre != v.find(pid)["nombre"]:
                v.rename(pid, nombre)
                renamed = True
            if b.get("es_usuario") is not None:
                v.set_user(pid, bool(b.get("es_usuario")))
                renamed = True
            if renamed:  # destinos de salida: el nombre sale en los documentos de todas sus reuniones
                self.jobs.outputs_for_persona(pid)
        remembered = None
        if b.get("recordar"):
            vec, secs = self.jobs.speaker_vector(rel_of(abs_path), h)
            data = voices.read_sidecar(voices.sidecar_path(abs_path)) or {}
            secs = secs or (data.get("hablantes", {}).get(h) or {}).get("segundos") or 0
            remembered = v.remember(pid, vec, secs, os.path.basename(abs_path), time.strftime("%Y-%m-%dT%H:%M:%S"))
            log(f"🗣 huella guardada: {v.find(pid)['nombre']} ({remembered} huellas)")
        return {"id": pid, "huellas": remembered, "personas": v.public()}

    def voices_merge(self, b):
        v = self.jobs.voices
        keep, other = b.get("id_a"), b.get("id_b")
        try:
            removed = v.merge(keep, other)
        except KeyError:
            raise ApiError("Alguna de las dos personas ya no existe.")
        except ValueError:
            raise ApiError("Tu persona no se fusiona con otras: si ese hablante no eres tú, ponle su nombre o pulsa "
                           "«No soy yo»; si eres tú, «Soy yo».")
        n = 0
        if removed:
            self.library.refresh(max_age=5)
            for a in list(self.library.flat):
                if not a.get("_spk"):
                    continue
                data = voices.read_sidecar(a["_spk"])
                if not data or not any(i.get("persona") == removed for i in (data.get("hablantes") or {}).values()):
                    continue
                for i in data["hablantes"].values():
                    if i.get("persona") == removed:
                        i["persona"] = keep
                voices.write_sidecar(a["_spk"], data)
                self.jobs.schedule_outputs(a["path"])  # destinos de salida: el documento con el nombre que queda
                n += 1
        self.library.at = 0
        log(f"🗣 personas fusionadas ({n} reuniones actualizadas)")
        return {"id": keep, "reuniones": n, "personas": v.public()}

    def voices_forget(self, b):
        v = self.jobs.voices
        if b.get("all"):
            v.forget_all()
            log("🗣 todas las huellas olvidadas")
            self.jobs.outputs_for_persona(None)  # destinos de salida: los documentos ya no llevan esos nombres
        else:
            try:
                v.forget(b.get("id"))
            except KeyError:
                raise ApiError("Esa persona ya no existe.")
            log("🗣 persona olvidada")
            self.jobs.outputs_for_persona(b.get("id"))
        self.library.at = 0
        return {"personas": v.public()}

    def voices_reassign(self, b):
        """Este hablante es otra persona ya guardada (o nadie: «No es esta persona» / «No soy yo»)."""
        abs_path = safe(b.get("path"), "file")
        pid, h = b.get("persona") or None, b.get("h")
        v = self.jobs.voices
        if pid and not v.find(pid):
            raise ApiError("Esa persona ya no existe.")
        uid = (v.user() or {}).get("id")
        before = (self._speaker_check(abs_path, h, b.get("creado"))["hablantes"][h]).get("persona")
        self._speaker_update(abs_path, h, lambda info, d: voices.assign(d, h, pid, uid), b.get("creado"))
        self._not_the_user(rel_of(abs_path), h, before, pid)
        return self.speakers_get({"path": b.get("path")})

    def voices_export(self, b):
        abs_path = safe(b.get("path"), "file")
        data = self.jobs.speakers_for(abs_path)
        if not data or not data.get("_who"):
            raise ApiError("Esta reunión no tiene hablantes identificados (o no tiene .srt).")
        segs = Transcripts().load(os.path.splitext(abs_path)[0])["segments"]
        out = os.path.splitext(abs_path)[0] + ".hablantes.txt"
        save_text(out, voices.export_text(segs, data["_who"], data, self.jobs.voices.names(),
                                          usuario=data.get("_usuario")), "utf-8-sig")
        self.library.at = 0
        return {"path": rel_of(out), "ext": ".hablantes.txt"}

    def export_package(self, b):
        dest = os.path.join(desktop_dir(), f"{APP_NAME} - instalador.zip")
        vocab = []
        if b.get("vocab"):  # las de carpetas vinculadas (":id/…") van como <raiz>/<proyecto>/Vocabulario/<tema>.txt
            vocab = [(v["_abs"], PKG_ROOT + "/" + (v["id"] if not v["id"].startswith(LINK)
                                                   else f"{v['project']}/Vocabulario/{v['name']}.txt"))
                     for v in list_vocab()]
        try:
            n = build_package(dest, vocab)
        except OSError as e:
            raise ApiError(f"No se pudo crear el paquete: {e}")
        if WINDOWS:
            subprocess.Popen(f'explorer /select,"{dest}"')
        return {"ok": True, "path": dest, "files": n, "size": os.path.getsize(dest), "vocab": len(vocab)}


# ----------------------------------------------------------------------------- HTTP

# P0-2: la interfaz no lleva <script> en linea ni manejadores on…= (todo va en los .js), asi que un script colado en el
# HTML no llega a ejecutarse; los style="…" de las plantillas si hacen falta ('unsafe-inline' solo en estilos)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "media-src 'self'; connect-src 'self'; frame-ancestors 'none'")
SLOW_REQUEST_S = 2.0


def slow_request(method, path, dt):
    """Una peticion de la API que tarda mas de SLOW_REQUEST_S queda en el log con su duracion: asi se ve que la
    interfaz esperaba al servidor, y en que (02/11/2026: /api/config tardo 8 min por una importacion y nada lo
    decia)."""
    if dt >= SLOW_REQUEST_S:
        log(f"   petición lenta: {method} {path} {dt:.1f} s")


DRAIN_MAX = 1 << 20  # P0-1: un cuerpo sin leer de hasta 1 MiB se lee y se descarta antes de responder (el error llega
# entero y la conexion sigue); uno mayor (una subida rechazada) o sin Content-Length cierra la conexion


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"{APP_NAME}/1.0"
    # cada conexion es un hilo de ThreadingHTTPServer: una keep-alive ociosa (o que deja de leer o de mandar a medias)
    # se cierra a los 60 s, y un cliente que deja conexiones abiertas ya no acumula hilos. El plazo es por operacion
    # del socket, no lo que tarda la app en contestar (el selector de carpetas espera al usuario)
    timeout = 60
    app = None
    token = None  # P1-02: el de esta sesion (lo pone main()); sin el, /api/* y /media responden 403
    body_read = False  # P0-1: el cuerpo de la peticion en curso se ha leido entero
    denied_logged = set()  # el primer 403 sin token de un navegador y el de un programa van al log, los demas no

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _origin_ok(self):
        """P0-1: un POST que llega de una pagina web solo vale si es la propia interfaz. El navegador manda Origin en
        todo POST y Sec-Fetch-Site (same-origin, same-site, cross-site o none); los programas (el acceso directo, el
        aviso flotante, el instalador, las pruebas) no mandan ninguna de las dos."""
        origin = self.headers.get("Origin")
        if origin is not None and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def _cookie_name(self):
        return f"audios_sesion_{PORT}"  # con el puerto: las cookies no distinguen puertos (otra copia con --port)

    def _token_matches(self, given):
        tok = self.token
        return bool(tok and given) and hmac.compare_digest(str(given).encode("utf-8", "replace"), tok.encode("utf-8"))

    def _token_ok(self):
        """P1-02: la cabecera X-Chuleta-Token (programas) o la cookie de sesion (la interfaz, que la recibe al abrir
        /?t=<token>)."""
        if self._token_matches(self.headers.get(TOKEN_HEADER)):
            return True
        for raw in self.headers.get_all("Cookie") or []:
            for part in raw.split(";"):
                k, _, v = part.strip().partition("=")
                if k == self._cookie_name() and self._token_matches(v.strip()):
                    return True
        return False

    def _denied(self):
        quien = "navegador" if self.headers.get("Sec-Fetch-Mode") else "programa"
        if quien not in Handler.denied_logged:
            Handler.denied_logged.add(quien)
            log(f"   403 sin token de sesión ({quien}): {self.command} {urlparse(self.path).path} (solo el primero)")
        self._json({"error": f"Sesión no válida: vuelve a abrir {APP_NAME} con su acceso directo."}, 403)

    def _session_start(self, t):
        """GET /?t=<token> (la ventana propia y Edge abren esa direccion): si es el token de esta sesion, la cookie
        (HttpOnly, SameSite=Strict) y a "/", para que el token no se quede en la direccion; si no, a "/" sin cookie (la
        interfaz dira que hay que abrir la app con su acceso directo)."""
        extra = {"Location": "/"}
        if self._token_matches(t):
            extra["Set-Cookie"] = f"{self._cookie_name()}={self.token}; Path=/; HttpOnly; SameSite=Strict"
            log("   sesión: cookie entregada a la interfaz")
        else:
            log("   sesión: /?t= con un token que no es el de esta sesión (sin cookie)")
        self._send(303, b"", "text/plain; charset=utf-8", extra)

    def _close_if_unread(self):
        """P0-1: si la peticion traia un cuerpo que no se ha leido (un 403, un error antes de leerlo), se lee y se
        descarta (hasta DRAIN_MAX) o la conexion se cierra tras la respuesta. Con keep-alive, esos bytes se tomarian por
        la peticion siguiente, con sus propias cabeceras: asi una web podia colar un POST con Host y X-Audios dentro del
        cuerpo de otro."""
        te, cl = self.headers.get("Transfer-Encoding"), (self.headers.get("Content-Length") or "0").strip()
        if self.body_read or (not te and cl == "0"):
            return
        if not te and cl.isdigit() and int(cl) <= DRAIN_MAX:
            left = int(cl)
            try:
                self.connection.settimeout(5)
                while left > 0:
                    chunk = self.rfile.read(min(1 << 16, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.connection.settimeout(self.timeout)  # el de siempre (sin plazo, la conexion ociosa no se cierra)
            except OSError:
                left = -1
            if left == 0:
                self.body_read = True
                return
        self.send_header("Connection", "close")

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self._close_if_unread()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        self.body_read = False
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if "path" in q:  # una reunion que la app ha movido en esta sesion: a su ruta nueva
            q["path"] = alias_of(q["path"])
        if u.path in ("", "/") and "t" in q:
            return self._session_start(q["t"])
        if (u.path.startswith("/api/") or u.path == "/media") and not self._token_ok():
            return self._denied()
        a = self.app
        routes = {"/api/ping": lambda q: {"app": "audios", "version": VERSION, "pid": os.getpid()},
                  "/api/config": a.config, "/api/tree": a.tree,
                  "/api/transcript": a.transcript, "/api/doc": a.doc, "/api/search": a.search,
                  "/api/jobs": a.jobs_list, "/api/rec/devices": a.rec_devices, "/api/rec/status": a.rec_status,
                  "/api/notes": a.notes_get, "/api/tags": a.tags, "/api/summary": a.summary_get,
                  "/api/vocab": a.vocab, "/api/voces": a.voices_state, "/api/hablantes": a.speakers_get,
                  "/api/reunion/actual": a.reunion_actual, "/api/perfil": a.perfil_get,
                  "/api/diagnostico": a.diagnostico, "/api/modelos": a.modelos_state, "/api/probar": a.probar_estado,
                  "/api/agente/estado": a.agente_estado,
                  "/api/inicio-windows": lambda q: {"activado": inicio_windows_leer()}}
        try:
            if u.path in routes:
                t0 = time.time()
                try:
                    return self._json(routes[u.path](q))
                finally:
                    slow_request("GET", u.path, time.time() - t0)
            if u.path == "/media":
                return self._media(safe(q.get("path"), "file"))
            return self._static(u.path)
        except ApiError as e:
            self._json({"error": e.msg}, e.code)
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception as e:
            log_exc()
            self._json({"error": str(e)}, 500)

    do_HEAD = do_GET

    def do_POST(self):
        self.body_read = False
        if not self._host_ok() or not self._origin_ok() or self.headers.get("X-Audios") != "1":
            return self._send(403, b"forbidden", "text/plain")
        if not self._token_ok():
            return self._denied()
        a = self.app
        routes = {"/api/transcribe": a.transcribe, "/api/cancel": a.cancel, "/api/jobs/clear": a.clear_jobs,
                  "/api/move": a.move, "/api/mkdir": a.mkdir, "/api/rename-folder": a.rename_folder,
                  "/api/reveal": a.reveal, "/api/open": a.open_file, "/api/settings": a.save_settings,
                  "/api/cloud/test": a.test_cloud, "/api/live": a.start_live, "/api/live/model": a.live_model,
                  "/api/agente/comprobar": a.agente_comprobar,
                  "/api/modelos/descargar": a.modelos_descargar, "/api/modelos/cancelar": a.modelos_cancelar,
                  "/api/probar": a.probar_empezar, "/api/probar/cancelar": a.probar_cancelar,
                  "/api/abrir-powershell": a.abrir_powershell,
                  "/api/live/traducir": a.live_translate,
                  "/api/rec/monitor": a.rec_monitor, "/api/rec/start": a.rec_start, "/api/rec/stop": a.rec_stop,
                  "/api/rec/toggle": a.rec_toggle, "/api/rec/mic": a.rec_mic, "/api/notes": a.notes_save,
                  "/api/summarize": a.summarize,
                  "/api/favorite": a.favorite, "/api/folder-icon": a.folder_icon, "/api/delete": a.delete,
                  "/api/transcript/save": a.save_transcript, "/api/transcript/restore": a.restore_transcript,
                  "/api/export": a.export_package, "/api/link/pick": a.link_pick, "/api/link/add": a.link_add,
                  "/api/link/remove": a.link_remove, "/api/vocab/save": a.vocab_save,
                  "/api/vocab/delete": a.vocab_delete, "/api/vocab/check": a.vocab_check,
                  "/api/vocab/import": a.vocab_import, "/api/perfil": a.perfil_save,
                  "/api/proyecto/carpeta": a.carpeta_principal, "/api/reunion/destino": a.destino,
                  "/api/voces/activar": a.voices_toggle, "/api/voces/identificar": a.voices_identify,
                  "/api/voces/persona": a.voices_person, "/api/voces/fusionar": a.voices_merge,
                  "/api/voces/olvidar": a.voices_forget, "/api/voces/reasignar": a.voices_reassign,
                  "/api/voces/exportar": a.voices_export,
                  "/api/apagar": a.apagar, "/api/mostrar": a.mostrar, "/api/ocultar": a.ocultar,
                  "/api/acerca/abrir": a.acerca_abrir,
                  "/api/inicio-windows": lambda b: {"activado": inicio_windows_poner(b.get("activado") is True)}}
        try:
            u = urlparse(self.path)
            if self.headers.get("Transfer-Encoding"):  # sin Content-Length no se sabe donde acaba el cuerpo (P0-1)
                raise ApiError("Cuerpo no válido: falta Content-Length", 411)
            if u.path == "/api/import":  # el cuerpo es el archivo tal cual (puede ser de 1 GB): se copia a trozos
                r = a.import_file(self, {k: v[0] for k, v in parse_qs(u.query).items()})
                self.body_read = True  # import_file lo ha leido entero (si se corta, lanza ApiError)
                return self._json(r)
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0:
                raise ApiError("Cuerpo no válido")
            raw = self.rfile.read(n) if n else b""
            self.body_read = len(raw) == n
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ApiError("Cuerpo no válido: se esperaba un objeto JSON")
            if isinstance(body.get("path"), str):  # una reunion movida en esta sesion (unas notas que se guardan justo
                body["path"] = alias_of(body["path"])  # al moverla): a su ruta nueva
            route = routes.get(u.path)
            if not route:
                raise ApiError("No encontrado", 404)
            t0 = time.time()
            try:
                self._json(route(body))
            finally:
                slow_request('POST', u.path, time.time() - t0)
        except ApiError as e:
            self._json({"error": e.msg}, e.code)
        except Exception as e:
            log_exc()
            self._json({"error": str(e)}, 500)

    def _static(self, path):
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        p = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not p.startswith(STATIC_DIR + os.sep) or not os.path.isfile(p):
            return self._send(404, b"not found", "text/plain")
        with open(p, "rb") as f:
            body = f.read()
        if name == "index.html":  # <title> y marca: el nombre de la app vive en common.APP_NAME
            body = body.replace(b"{{APP_NAME}}", APP_NAME.encode("utf-8"))
        self._send(200, body, MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))

    def _media(self, p):
        """Audio con soporte de Range: permite saltar a cualquier minuto al instante. Solo audio y video: un .html, un
        .js o un .svg de una carpeta vinculada, servido desde este origen, se saltaria la CSP (P0-2)."""
        if os.path.splitext(p)[1].lower() not in AUDIO_EXT:
            raise ApiError("Solo se sirven audios", 404)
        size = os.path.getsize(p)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self._close_if_unread()
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        # P2-13: el audio de las reuniones no se queda en la cache de WebView2 (su perfil es persistente, en DATA_DIR)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self._close_if_unread()
        self.end_headers()
        if self.command == "HEAD":
            return
        # Grabacion de OBS en curso: la cabecera dice 0xFFFFFFFF; se sirve con el tamano de ahora
        # para que el navegador sepa cuanto dura lo grabado y se pueda escuchar
        patch = b""
        info = live.wav_info(p) if p.lower().endswith(".wav") else None
        if info and info["data_size"] is None and start < info["data_off"]:
            with open(p, "rb") as f:
                patch = bytearray(f.read(info["data_off"]))
            struct.pack_into("<I", patch, 4, size - 8)
            struct.pack_into("<I", patch, info["data_off"] - 4, size - info["data_off"])
        remaining = end - start + 1
        try:
            with open(p, "rb") as f:
                f.seek(start)
                pos = start
                while remaining > 0:
                    chunk = f.read(min(512 * 1024, remaining))
                    if not chunk:
                        break
                    if pos < len(patch):
                        k = min(len(patch) - pos, len(chunk))
                        chunk = patch[pos:pos + k] + chunk[k:]
                    self.wfile.write(chunk)
                    pos += len(chunk)
                    remaining -= len(chunk)
        except (ConnectionError, OSError):
            pass  # el navegador corta la conexion al saltar a otro punto: normal


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # en Windows SO_REUSEADDR permitiria dos servidores en el mismo puerto

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):  # el navegador cierra conexiones: normal
            super().handle_error(request, client_address)


def ui_url(token=None):
    """La direccion con la que se abre la interfaz: con ?t=<token> el servidor entrega la cookie de sesion y redirige a
    "/" (P1-02). Sin token (el de esta copia si no se pasa otro), la de siempre."""
    token = Handler.token if token is None else token
    return URL + (f"?t={token}" if token else "")


UI_REOPEN_GAP = 15.0  # s alrededor del Edge del arranque en los que no se abre otro: aun puede estar abriendose
_UI_OPENED = {"inicio": 0.0, "ultimo": 0.0}  # time.monotonic() del Edge del arranque y del ultimo (0: nunca)
_UI_LOCK = threading.Lock()


def open_ui(token=None, inicio=False):
    """La interfaz en Edge (modo app, sin barra de direcciones) o, sin Edge, en el navegador por defecto. Es el
    camino de siempre y el de reserva cuando no hay ventana propia (window.py). Con dos lanzamientos seguidos, el Edge
    del arranque (`inicio`) y el que pide el acceso directo serian dos ventanas: en UI_REOPEN_GAP s alrededor del
    primero no se abre otro, en ningun orden. Despues, cada peticion abre el suyo (Edge cerrado: hay que volver a
    abrirlo). True si lo abre."""
    with _UI_LOCK:
        ahora = time.monotonic()
        antes = _UI_OPENED["ultimo" if inicio else "inicio"]
        if antes and ahora - antes < UI_REOPEN_GAP:
            log(f"   Edge se ha abierto hace {ahora - antes:.0f} s: no se abre otro")
            return False
        _UI_OPENED["ultimo"] = ahora
        if inicio:
            _UI_OPENED["inicio"] = ahora
    url = ui_url(token)
    for exe in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        exe = os.path.expandvars(exe)
        if os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1500,920"])
            return True
    webbrowser.open(url)
    return True


def _local_request(path, token, data=None, timeout=1.5):
    """Peticion a la copia que corre en este puerto: sin proxy (P1-13) y con el token de DATA_DIR (P1-02)."""
    headers = {TOKEN_HEADER: token} if token else {}
    if data is not None:
        headers.update({"X-Audios": "1", "Content-Type": "application/json"})
    req = urllib.request.Request(URL + path, data=data, method="POST" if data is not None else "GET", headers=headers)
    with local_opener().open(req, timeout=timeout) as r:
        return json.load(r)


def probe_running(token=None, timeout=1.5):
    """Que hay en el puerto: "app" (esta app, y acepta nuestro token), "libre" (nadie escucha), "arrancando" (acepta la
    conexion pero no contesta: otra copia entre reservar el puerto y empezar a servir) u "otro" (otro programa, u otra
    sesion de Windows con su propio token)."""
    token = read_token(DATA_DIR) if token is None else token
    try:
        r = _local_request("api/ping", token, timeout=timeout)
    except urllib.error.HTTPError:
        return "otro"
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            return "libre"
        return "arrancando" if isinstance(e.reason, TimeoutError) else "otro"
    except TimeoutError:
        return "arrancando"
    except Exception:
        return "otro"
    if r.get("app") != "audios":
        return "otro"
    # "otra_version": nuestra app, pero de otra version (sin `version` en el ping, anterior al 02/11/2026, o distinta):
    # una actualizacion en el sitio con la anterior aun abierta, o un acceso directo viejo. Si se le cediera la ventana,
    # serviria los ficheros nuevos con la API vieja y los botones no harian nada (02/11/2026)
    return "app" if r.get("version") == VERSION else "otra_version"


def _wait_port_free(wait, token=None):
    """True si el puerto queda libre antes de `wait` s."""
    end = time.time() + wait
    while time.time() < end:
        if probe_running(token, timeout=1.0) == "libre":
            return True
        time.sleep(0.25)
    return probe_running(token, timeout=1.0) == "libre"


def _port_owner_pid(port):
    """PID que escucha en 127.0.0.1:port (netstat; solo Windows), o None."""
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=15,
                           creationflags=engines.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}") \
                and parts[1].rsplit(":", 1)[0] in ("127.0.0.1", "0.0.0.0") and parts[3].upper() == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def _is_python_process(pid):
    """El proceso es python.exe o pythonw.exe (tasklist; solo Windows): la app anterior, nunca otro programa."""
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                           timeout=15, creationflags=engines.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.startswith('"python.exe"') or line.startswith('"pythonw.exe"') for line in r.stdout.splitlines())


def retire_other_version(token=None, wait=10.0):
    """Otra version de la app tiene el puerto. Se le pide que se apague (POST /api/apagar, desde el 31/10/2026) y, si
    no sabe o no lo hace, en Windows se termina su proceso: nunca si esta grabando. Devuelve el estado final del
    puerto (probe_running) o "grabando"."""
    token = read_token(DATA_DIR) if token is None else token
    try:
        ping = _local_request("api/ping", token)
    except Exception:
        ping = {}
    if ping.get("version") == VERSION:  # entretanto el puerto lo ha tomado una copia de esta version: no se toca
        return "app"
    vieja, vieja_pid = ping.get("version") or ("anterior al 02/11/2026" if ping else "desconocida"), ping.get("pid")
    log(f"otra versión de {APP_NAME} ({vieja}) tiene el puerto {PORT}: se le pide que se cierre")
    try:
        if _local_request("api/rec/status", token).get("recording"):
            log("   está grabando: no se toca")
            return "grabando"
    except Exception:
        pass
    try:
        _local_request("api/apagar", token, b"{}", timeout=5)
    except Exception as e:  # 404 en las versiones sin /api/apagar, 409 si grabando, o sin respuesta
        log(f"   /api/apagar: {e}")
    if _wait_port_free(wait / 2, token):
        log("   cerrada")
        return "libre"
    if WINDOWS and probe_running(token) != "app":  # si el puerto es ya de una copia de esta version, no se mata
        pid = _port_owner_pid(PORT)
        if pid and pid != os.getpid() and (not vieja_pid or pid == vieja_pid) and _is_python_process(pid):
            log(f"   no se ha cerrado: se termina su proceso (pid {pid})")
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15,
                               creationflags=engines.NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                log_exc()
            if _wait_port_free(wait / 2, token):
                log("   cerrada")
                return "libre"
    estado = probe_running(token)
    log(f"   sigue ahí ({estado})")
    return estado


def already_running(token=None):
    """La app ya corre en este puerto y es la nuestra (contesta con el token de DATA_DIR): no la de otro usuario."""
    return probe_running(token) == "app"


def allow_foreground(pid=None):
    """Windows solo deja ponerse delante a quien tiene permiso: el acceso directo que se acaba de pulsar lo tiene y
    se lo cede a la copia que corre (`pid`; sin el, a cualquiera, ASFW_ANY), para que su ventana no se quede detras
    parpadeando en la barra de tareas. Solo Windows."""
    if not WINDOWS:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.AllowSetForegroundWindow.argtypes = [ctypes.c_ulong]
        return bool(user32.AllowSetForegroundWindow(int(pid) if pid else 0xFFFFFFFF))
    except Exception:
        return False


def ask_running_to_show(token=None):
    """Pide a la copia que ya corre que ensene su ventana (POST /api/mostrar) y le cede el primer plano. Devuelve su
    respuesta ({"ok": true} y, si su ventana aun se esta abriendo, "pending"; si ha abierto Edge, "browser") o None si
    no contesta que si: entonces se abre Edge desde aqui, como antes."""
    token = read_token(DATA_DIR) if token is None else token
    try:
        allow_foreground(_local_request("api/ping", token).get("pid"))
    except Exception:
        pass
    try:
        r = _local_request("api/mostrar", token, b"{}", timeout=5)
    except Exception as e:
        log(f"   /api/mostrar: {e}")
        return None
    return r if isinstance(r, dict) and r.get("ok") else None


INSTANCE_FILE = "instancia-{port}.lock"
_INSTANCE = []  # el descriptor del fichero bloqueado: abierto mientras viva el proceso (el sistema lo suelta al morir)
_LOCK_BYTE = 1 << 30  # en Windows se bloquea este byte, lejos del contenido (pid y hora), que las demas copias leen
INSTANCE_WAIT = 8.0  # s que una segunda copia espera a que la otra conteste o suelte la marca (si se esta cerrando)
INSTANCE_STUCK = 180.0  # s con la marca sin contestar a partir de los que se avisa de que la otra copia no responde


def _lock_once(path):
    """True: bloqueado por este proceso; False: lo tiene otro; None: no se ha podido comprobar."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)  # no heredable (PEP 446): recorder, worker… no lo retienen
    except OSError as e:
        log(f"   marca de instancia: no se puede abrir ({e})")
        return None
    try:
        if WINDOWS:
            import msvcrt
            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)  # msvcrt.locking bloquea desde la posicion actual (sin esperar)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if isinstance(e, BlockingIOError) or (WINDOWS and e.errno in (errno.EACCES, errno.EDEADLK)):
            return False
        log(f"   marca de instancia: no se puede bloquear ({e})")
        return None
    try:  # quien la tiene y desde cuando: lo lee una segunda copia para saber si esta se ha quedado colgada
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()} {int(time.time())}\n".encode("ascii"))
    except OSError:
        pass
    _INSTANCE[:] = [fd]
    return True


def instance_holder(data_dir, port):
    """(pid, segundos desde que tomo la marca) de la copia que la tiene, o (None, None) si no se sabe."""
    try:
        with open(os.path.join(data_dir, INSTANCE_FILE.format(port=port)), "rb") as f:
            pid, t = f.read(64).decode("ascii").split()[:2]
        return int(pid), max(0, int(time.time()) - int(t))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None


def release_instance():
    """Suelta la marca antes de tiempo (fatal: esta copia no va a arrancar; que la siguiente no la espere)."""
    for fd in _INSTANCE:
        try:
            os.close(fd)
        except OSError:
            pass
    _INSTANCE[:] = []


def instance_lock(data_dir, port, wait=0.0):
    """Instancia unica: DATA_DIR\\instancia-<puerto>.lock bloqueado mientras viva esta copia (lo suelta el sistema al
    terminar el proceso, tambien si se cuelga o lo matan). True: esta copia es la app; False: otra copia con la misma
    carpeta de datos y el mismo puerto sigue viva, arrancando o abierta (`wait` s esperando a que lo suelte); None: no
    se ha podido comprobar y se sigue como antes, solo por el puerto. Va antes que el puerto: un segundo lanzamiento
    lo sabe al instante, sin esperar a que la primera copia conteste (en un PC lento tarda decenas de segundos)."""
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError as e:
        log(f"   marca de instancia: sin carpeta de datos ({e})")
        return None
    path = os.path.join(data_dir, INSTANCE_FILE.format(port=port))
    end = time.monotonic() + wait
    while True:
        r = _lock_once(path)
        if r is not False or time.monotonic() >= end:
            return r
        time.sleep(0.25)


def wait_instance(estado, wait=None):
    """Otra copia tiene la marca y no contesta como la app (`estado`): arranca (doble clic en el acceso directo), se
    esta cerrando (Apagar y volver a abrir enseguida), tarda en contestar (abierta pero ocupada) o, en una
    actualizacion, esta retirando la version anterior ("hermana"). Hasta INSTANCE_WAIT s a que conteste (se le pedira
    la ventana) o suelte la marca (entonces arranca esta). Devuelve (instancia, estado) como en main()."""
    end = time.monotonic() + (INSTANCE_WAIT if wait is None else wait)
    while time.monotonic() < end:
        time.sleep(0.5)
        instancia = instance_lock(DATA_DIR, PORT)
        if instancia is not False:  # la ha soltado (o ya no se puede comprobar): esta copia sigue como un arranque
            return instancia, probe_running()
        estado = probe_running(timeout=1.5)
        if estado == "app":
            break
    return False, estado


def _holder_is_port_owner():
    """La marca la tiene quien contesta en el puerto (su /api/ping lleva su pid desde el 07/11/2026). Si no (la otra
    version es anterior a la marca), la tiene una copia hermana de esta version."""
    pid, _ = instance_holder(DATA_DIR, PORT)
    try:
        return bool(pid) and pid == _local_request("api/ping", read_token(DATA_DIR)).get("pid")
    except Exception:
        return False


def show_running(no_browser, estado="app"):
    """Esta copia sobra (la app ya corre, o se esta abriendo en otra copia): le pide su ventana y termina. Sin
    respuesta de la otra (`estado` distinto de "app": aun no sirve), nada: su ventana saldra sola al terminar de
    arrancar; si lleva mas de INSTANCE_STUCK s sin contestar, se avisa de que no responde. Si contesta pero no ensena
    nada (no deberia pasar con la misma version), Edge desde aqui, como antes."""
    if estado != "app":
        log(f"otra copia tiene la marca y no contesta ({estado}): si se está abriendo, su ventana saldrá sola · pid "
            f"{os.getpid()}")
        pid, hace = instance_holder(DATA_DIR, PORT)
        if pid:
            log(f"   la otra copia: pid {pid}, desde hace {hace} s")
        if pid and hace > INSTANCE_STUCK:
            alert(f"{APP_NAME} no responde",
                  f"{APP_NAME} ya está abierta (pid {pid}, desde hace {hace // 60} min) pero no contesta.\n\nSi su "
                  f"ventana no aparece, ciérrala en el Administrador de tareas (pythonw.exe, pid {pid}) y vuelve a "
                  f"abrir {APP_NAME}.")
        return
    log(f"la app ya estaba abierta: se enseña su ventana · pid {os.getpid()}")
    if no_browser:
        return
    r = ask_running_to_show()
    if r is None:
        log("   no contesta a /api/mostrar: se abre en Edge desde aquí")
        open_ui(read_token(DATA_DIR))
    elif r.get("pending"):
        log("   aún se está abriendo: su ventana saldrá sola")


PORT_WAIT = 20.0  # s que se espera a otra copia que arranca a la vez (doble clic en el acceso directo)


def reserve_port(wait=None):
    """P1-13: abre el puerto ANTES de crear App() (y sus hilos: atajo Ctrl+Alt+R, vigilante de reuniones, directo).
    Si ya lo tiene otra copia que esta arrancando a la vez, espera a que conteste y devuelve None (la llamada le pide
    su ventana); si lo tiene otro programa, el aviso de siempre y fuera."""
    end = time.time() + (PORT_WAIT if wait is None else wait)
    while True:
        try:
            return Server((HOST, PORT), Handler)
        except OSError as e:
            err = e
        estado = probe_running()
        if estado == "app":
            return None
        if estado in ("otro", "otra_version") or time.time() >= end:
            break
        time.sleep(0.3)  # "libre" (se acaba de soltar) o "arrancando": otra vuelta
    log(f"puerto {PORT} ocupado ({estado}): {err}")
    if estado == "otra_version":
        fatal(f"Hay abierta otra versión de {APP_NAME} que no se ha podido cerrar.\n\nCiérrala (su ventana de "
              f"consola o, en el Administrador de tareas, pythonw.exe) y vuelve a abrir {APP_NAME}.")
    fatal(f"No se puede usar el puerto {PORT}: lo tiene otro programa ({err}).\n\nCierra ese programa (o arranca "
          f"{APP_NAME} con --port 8766) y vuelve a abrir {APP_NAME}.")


def write_token(data_dir, token):
    """DATA_DIR\\sesion.token (UTF-8, sin salto de linea; DATA_DIR es privado del usuario)."""
    os.makedirs(data_dir, exist_ok=True)
    fd = os.open(os.path.join(data_dir, TOKEN_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(token)


def remove_token(data_dir, token):
    """Al apagar: borra sesion.token si sigue siendo el de esta sesion (no el de otra copia que arrancara despues)."""
    try:
        if token and read_token(data_dir) == token:
            os.remove(os.path.join(data_dir, TOKEN_FILE))
    except OSError:
        pass


def start_session(data_dir):
    """P1-02: un token nuevo en cada arranque. Lo exige el Handler en /api/* y /media, y lo leen de DATA_DIR el acceso
    directo, el aviso flotante, el instalador y las pruebas."""
    token = secrets.token_urlsafe(32)
    write_token(data_dir, token)
    Handler.token = token
    atexit.register(remove_token, data_dir, token)  # salidas normales (Ctrl+C); shutdown() lo borra antes de _exit
    return token


# ----------------------------------------------------------------------------- icono en la bandeja (bandeja.py)

def tray_estado():
    """Lo que dice el icono: segundos de la grabacion en curso (o None) y trabajos en cola o en marcha."""
    a = Handler.app
    if a is None:
        return {"grabando": None, "trabajos": 0}
    rec = a.rec
    with a.jobs.lock:
        n = sum(1 for j in a.jobs.list if j.status in ("queued", "running"))
    return {"grabando": time.time() - rec.started if rec and rec.alive() else None, "trabajos": n}


def tray_balloon(titulo, texto):
    w = WINDOW
    return bool(w is not None and w.tray is not None and w.tray.alive and w.tray.balloon(titulo, texto))


def tray_abrir():
    """Clic en el icono (o «Abrir»): la ventana propia, delante."""
    w = WINDOW
    if w is not None and w.request_show() is None:
        open_ui()


def tray_grabar():
    """«Grabar» / «Parar la grabación» del menu: lo mismo que Ctrl+Alt+R (los avisos los da rec_toggle)."""
    if Handler.app is not None:
        try:
            Handler.app.rec_toggle({"source": "bandeja"})
        except ApiError:
            pass  # rec_toggle ya lo ha dicho (toast) y anotado


def tell(titulo, texto):
    """Aviso de Windows (MessageBox, delante de todo) desde un hilo cualquiera; fuera de Windows, al log. Para lo que no
    puede quedarse en un globo de la bandeja (con «No molestar» Windows no los ensena)."""
    log(f"   aviso: {titulo}")
    if not WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x0 | 0x40 | 0x10000 | 0x40000)  # MB_OK, ICONINFORMATION
    except Exception:
        log_exc()


def ask_yes_no(titulo, texto):
    """Pregunta Si/No de Windows (MessageBox delante de todo), desde un hilo cualquiera. Fuera de Windows, si."""
    if not WINDOWS:
        return True
    try:
        import ctypes
        # MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST; IDYES = 6
        return ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x4 | 0x20 | 0x100 | 0x10000 | 0x40000) == 6
    except Exception:
        log_exc()
        return False


def tray_salir():
    """«Salir» del icono de la bandeja: el mismo apagado que Ajustes > Apagar (App.apagar). Grabando, no (un globo lo
    dice); con trabajos en marcha, la misma pregunta que la pagina antes de apagar."""
    a = Handler.app
    if a is None:
        return
    if a.rec and a.rec.alive():
        log("   salir desde la bandeja: está grabando, no se sale")
        tell(f"{APP_NAME} está grabando", "Para la grabación antes de salir (Ctrl+Alt+R o su botón Parar).")
        return
    n = tray_estado()["trabajos"]
    if n and not ask_yes_no(f"Salir de {APP_NAME}",
                            f"Hay {n} trabajo{'s' if n != 1 else ''} en marcha (transcripciones o resúmenes): se "
                            "cancelan (lo ya escrito se conserva) y podrás relanzarlos al volver.\n\n"
                            f"¿Salir de {APP_NAME}?"):
        log("   salir desde la bandeja: cancelado (trabajos en marcha)")
        return
    try:
        a.apagar({"origen": "bandeja"})
    except ApiError as e:  # ha empezado a grabar entretanto
        tell(APP_NAME, e.msg)


def shell_folder(csidl):
    """Carpeta especial de Windows (SHGetFolderPathW, la que respeta la redireccion por directiva de empresa; las mismas
    que [Environment]::GetFolderPath del instalador y el desinstalador), o None."""
    if not WINDOWS:
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        log_exc()
    return None


def _startup_lnks():
    """(acceso directo del menu Inicio que pone INSTALAR.bat, el de la carpeta Inicio de Windows) o (None, None)."""
    if not WINDOWS:
        return None, None
    appdata = os.environ.get("APPDATA")
    progs = shell_folder(0x02) or (appdata and os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs"))
    startup = shell_folder(0x07) or (progs and os.path.join(progs, "Startup"))  # CSIDL_PROGRAMS, CSIDL_STARTUP
    if not (progs and startup):
        return None, None
    return os.path.join(progs, f"{APP_NAME}.lnk"), os.path.join(startup, f"{APP_NAME}.lnk")


def inicio_windows_leer():
    """«Arrancar con Windows»: True o False; None si no se puede (fuera de Windows, o sin el acceso directo del menu
    Inicio, que es el que se copia: una copia descomprimida sin instalar)."""
    origen, destino = _startup_lnks()
    if not origen:
        return None
    if os.path.isfile(destino):
        return True
    return False if os.path.isfile(origen) else None


def inicio_windows_poner(activado):
    """Copia (o quita) el acceso directo del menu Inicio en la carpeta Inicio de Windows (shell:startup): mismo destino,
    iconos y AppUserModelID, sin registro ni permisos de administrador. Devuelve el estado que queda."""
    origen, destino = _startup_lnks()
    if not origen:
        return None
    try:
        if activado and os.path.isfile(origen):
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(origen, destino)
            log("arrancar con Windows: activado")
        elif not activado and os.path.isfile(destino):
            os.remove(destino)
            log("arrancar con Windows: desactivado")
    except OSError as e:
        log(f"   arrancar con Windows: {e}")
    return inicio_windows_leer()


def inicio_windows_al_dia():
    """Al arrancar: si esta activado y el acceso directo del menu Inicio ha cambiado (lo rehace INSTALAR.bat al
    actualizar), la copia de la carpeta Inicio se pone igual."""
    origen, destino = _startup_lnks()
    try:
        if origen and os.path.isfile(destino) and os.path.isfile(origen):
            with open(origen, "rb") as a, open(destino, "rb") as b:
                if a.read() != b.read():
                    shutil.copy2(origen, destino)
                    log("arrancar con Windows: acceso directo actualizado")
    except OSError as e:
        log(f"   arrancar con Windows: {e}")


def make_tray(ico_path):
    """El icono de la bandeja (solo Windows, con la ventana propia): bandeja.Bandeja, aun sin poner."""
    if not WINDOWS:
        return None
    return Bandeja(ico_path, {"abrir": tray_abrir, "grabar": tray_grabar, "salir": tray_salir,
                              "inicio": (inicio_windows_leer, inicio_windows_poner)}, tray_estado)


def shutdown(reason, exit_fn=os._exit):
    """Apagado ordenado (Ajustes > Apagar): medidor/grabadora, trabajos y procesos de Whisper, servidor HTTP, ventana
    y fin del proceso. recorder.py, worker.py y voices.py mueren solos al quedarse sin padre. No llamarlo desde el
    hilo que ejecuta serve_forever (SERVER.shutdown esperaria a si mismo)."""
    log(f"apagando ({reason})")
    try:
        if Handler.app is not None:
            Handler.app.shutdown_prepare()
    except Exception:
        log_exc()
    remove_token(DATA_DIR, Handler.token)  # os._exit no pasa por atexit
    try:  # el icono de la bandeja, antes de cerrar el log (window.destroy() lo repetiria sin efecto)
        if WINDOW is not None and getattr(WINDOW, "tray", None) is not None:
            WINDOW.tray.close()
    except Exception:
        log_exc()
    log("apagado")
    logging.shutdown()  # antes de parar el servidor: en cuanto serve_forever vuelve, el hilo principal sale solo
    try:
        if WINDOW is not None:
            WINDOW.destroy()
    except Exception:
        pass
    try:
        if SERVER is not None:
            SERVER.shutdown()
    except Exception:
        pass
    exit_fn(0)


def schedule_shutdown(reason, delay=0.5):
    """Termina el proceso al cabo de `delay` s: tiempo para que la respuesta de /api/apagar llegue a la pagina."""
    t = threading.Timer(delay, shutdown, args=(reason,))
    t.daemon = True
    t.start()
    return t


# Windows apaga, reinicia o cierra la sesion con la ventana propia abierta (o oculta): window.py deja cerrar y llama
# a este mismo apagado limpio mientras Windows espera
Window.set_end_session(shutdown)


def arg(name, default=None):
    """--port 8766 / --data C:\\ruta (para probar una segunda copia sin tocar la principal)."""
    if name in sys.argv[:-1]:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def fatal(text):
    """Error de arranque: al log, a la consola si la hay y, sin consola (pythonw.exe), un aviso de Windows."""
    release_instance()  # mientras el aviso esta abierto, otro lanzamiento no debe esperar a esta copia
    alert(f"{APP_NAME} no ha podido arrancar", text)
    sys.exit(1)


def main():
    global PORT, URL, DATA_DIR, SERVER, WINDOW
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    PORT = int(arg("--port", PORT))
    URL = f"http://{HOST}:{PORT}/"
    DATA_DIR = os.path.abspath(arg("--data", DATA_DIR))
    setup_logging(DATA_DIR)
    no_browser = "--no-browser" in sys.argv
    os.environ["CHULETA_DATOS"] = DATA_DIR  # el aviso flotante (proceso hijo) lee ahi sesion.token, tambien con --data
    # instancia unica, antes que el puerto: un segundo lanzamiento (doble clic, o el acceso directo otra vez porque la
    # ventana tarda) lo sabe al instante, aunque la primera copia aun no conteste
    instancia = instance_lock(DATA_DIR, PORT)
    estado = probe_running()
    if instancia is False and estado == "otra_version" and not _holder_is_port_owner():
        estado = "hermana"  # actualizacion con doble clic: la marca es de otra copia nueva, que retira la vieja
    if instancia is False and estado not in ("app", "otra_version"):  # la otra copia arranca, se cierra o tarda
        instancia, estado = wait_instance(estado)
    if estado == "otra_version":  # una version anterior seguia abierta (actualizacion en el sitio, acceso viejo)
        estado = retire_other_version()
        if estado == "grabando":
            fatal(f"Hay abierta una versión anterior de {APP_NAME} y está grabando.\n\nPara esa grabación "
                  f"(Ctrl+Alt+R o su botón Parar) y vuelve a abrir {APP_NAME}.")
        if instancia is False and estado == "libre":  # la marca era suya: el sistema la suelta al cerrarse
            instancia = instance_lock(DATA_DIR, PORT, wait=5.0)
            if instancia is False:
                log("   la versión anterior se ha cerrado pero su marca de instancia sigue: se continúa sin ella")
                instancia = None
    if instancia is False and estado != "otra_version":  # esta app ya corre o se esta abriendo en otra copia
        show_running(no_browser, estado)
        return
    srv = None if estado == "app" else reserve_port()  # el puerto, antes de App() (P1-13)
    if srv is None:
        show_running(no_browser)
        return
    try:
        start_session(DATA_DIR)
    except OSError as e:
        log_exc()
        fatal(f"Error al iniciar: no se puede escribir en {DATA_DIR} ({e}).")
    try:
        import truststore  # usa los certificados de Windows (proxies corporativos), si esta instalado
        truststore.inject_into_ssl()
    except Exception:
        pass
    # antes de cualquier ventana (la propia, el selector de carpetas o un aviso): la barra de tareas agrupa por este id
    log(f"AppUserModelID {APP_ID}: {'fijado' if set_app_id() else 'no disponible'}")
    # los modelos de Whisper los descarga el servidor (con truststore y los proxies, como Groq), con progreso real;
    # antes de App(): la precarga y /api/config ya miran ahi (engines.WhisperModels)
    engines.AgenteLocal.log = log  # tiempos y avisos de Claude Code (p. ej. un hijo que deja su salida abierta)
    engines.MODELS = engines.WhisperModels(os.path.join(DATA_DIR, "models", "whisper"), log=log,
                                           url=os.environ.get("CHULETA_PRUEBAS_URL_MODELOS") or engines.WHISPER_URL)
    if os.environ.get("CHULETA_PRUEBAS_URL_MODELOS"):
        log("PRUEBAS: los modelos de Whisper se descargan de una URL de pruebas (CHULETA_PRUEBAS_URL_MODELOS)")
    if os.environ.get("CHULETA_PRUEBAS_URL_GROQ"):  # E2E: un Groq falso en local; nunca una clave real en la CI
        base = os.environ["CHULETA_PRUEBAS_URL_GROQ"].rstrip("/")
        engines.PROVIDERS["groq"] = dict(engines.PROVIDERS["groq"], url=base + "/audio/transcriptions")
        engines.CHAT_URLS["groq"] = base + "/chat/completions"
        log("PRUEBAS: Groq va a una URL de pruebas (CHULETA_PRUEBAS_URL_GROQ)")
    try:
        Handler.app = App()
    except Exception as e:
        log_exc()
        fatal(f"Error al iniciar: {e.__class__.__name__}: {e}\n\nDetalle en {os.path.join(DATA_DIR, 'server.log')}")
    Handler.app.library.refresh()
    threading.Thread(target=Handler.app.transcripts.warm, args=(Handler.app.library,), daemon=True).start()
    # el modelo local del directo, cargado antes de que empiece la primera reunion (sin bloquear la interfaz)
    threading.Thread(target=Handler.app.jobs.preload, daemon=True, name="preload").start()
    SERVER = srv
    for line in ("=" * 64, f"  {APP_NAME.upper()}  ·  {URL}", f"  Carpeta: {ROOT}",
                 f"  Apagar: Ajustes > Apagar {APP_NAME} (con consola, también cerrándola o Ctrl+C).", "=" * 64):
        log(line)
    log(f"arranque · {URL} · carpeta {ROOT} · datos {DATA_DIR} · Python {sys.version.split()[0]} · pid "
        f"{os.getpid()}")  # el pid: con dos copias a la vez, las dos escriben en el mismo server.log
    if no_browser:  # para abrirla a mano (desarrollo): el token no va al log, que sale entero en el diagnostico
        log(f"  Interfaz: {URL}?t=<el contenido de {os.path.join(DATA_DIR, TOKEN_FILE)}>")
    ico = os.path.join(STATIC_DIR, "chuleta.ico")
    WINDOW = None if no_browser else Window(ui_url(), DATA_DIR, ico, tray=make_tray(ico))
    inicio_windows_al_dia()
    if WINDOW is not None and WINDOW.available:
        # la ventana propia exige el hilo principal: el servidor pasa a un hilo y aqui se bloquea hasta apagar
        threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
        if WINDOW.run():
            if WINDOW.quitting:
                return  # apagando: shutdown() termina el proceso
            log("la ventana se ha cerrado sin apagar: la app sigue en segundo plano")
        else:
            log("no se pudo abrir la ventana propia (detalle arriba): se abre en Edge")
            WINDOW = None
            open_ui(inicio=True)
        try:
            while True:  # el servidor sigue en su hilo; con consola, Ctrl+C sale
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return
    WINDOW = None
    if not no_browser:
        threading.Timer(0.3, open_ui, kwargs={"inicio": True}).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
