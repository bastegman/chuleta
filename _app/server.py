# -*- coding: utf-8 -*-
"""
Tenca: app local para ordenar, escuchar, leer y transcribir las grabaciones de reuniones.

  - Servidor HTTP de la libreria estandar en http://127.0.0.1:8765 (solo accesible desde este PC)
  - Interfaz en _app/static (HTML + JS sin compilar)
  - Transcripcion local (faster-whisper) o en la nube (engines.py)
  - Las transcripciones se guardan como siempre: <audio>.txt y <audio>.srt junto al audio

Arranque: acceso directo del escritorio (pythonw.exe server.py, sin consola; _app/Tenca.bat lo arranca con consola
para ver los mensajes). La interfaz se abre en una ventana propia (window.py, WebView2) o, si no se puede, en Edge.
"""
import atexit
import contextlib
import copy
import errno
import hashlib
import hmac
import itertools
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
from collections import Counter
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import aprender
import calendario
import engines
import explicar
import live
import plan as grabar_plan
import probar
import salidas
import voices
from common import (APP_ID, APP_NAME, ApiError, BAD_NAME, LOGGER, SRT_TIME, WINDOWS,  # noqa: F401 (re-exportados)
                    alert, clean_name, default_threads, fmt_srt, fold, is_protected, log, log_exc, natural_key,
                    parse_srt, protect, read_text, save_text, setup_logging, unprotect, wav_duration, write_bytes)
from common import TOKEN_FILE, TOKEN_HEADER, local_opener, read_token  # sesion local (P1-02) y loopback sin proxy
from recording import GlobalHotkey, RecorderProcess, cue, mic_meeting_app, notify
from bandeja import Bandeja
import window as window_mod
from window import Window, set_app_id

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
ROOT = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AudiosApp")


def root_name():
    """Como se llama de verdad la carpeta de la app (ROOT) en el disco: «Tenca» en una instalacion nueva y «Chuleta» en
    una instalada encima de Chuleta, que no renombra la carpeta de las reuniones (decision 1 del renombrado,
    09/11/2026). Es lo que se ensena como raiz de una ruta («Chuleta\\Proyecto Alfa\\…», lo que se ve en el
    Explorador) y el nombre del proyecto de las listas de la raiz, como el de cualquier otro proyecto es el de su
    carpeta. Se calcula cada vez: los tests cambian ROOT."""
    return unicodedata.normalize("NFC", os.path.basename(os.path.normpath(ROOT)) or ROOT.rstrip("\\/") or APP_NAME)


HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}/"
SERVER, WINDOW = None, None  # los pone main(): el servidor HTTP y la ventana propia (None si se abre en Edge)

# los del movil y de los mensajeros tambien (09/11/2026, «Subir audio»; todos probados con el PyAV de la app): .oga de
# Telegram, .amr/.3gp/.3gpp de los Android antiguos, .3ga de grabadoras de llamadas, .caf/.qta/.aif(f) de Apple y el
# .waptt de los WhatsApp antiguos (un Ogg Opus con otro nombre)
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".aac", ".wma", ".opus", ".mkv", ".mov", ".webm",
             ".oga", ".amr", ".3gp", ".3gpp", ".3ga", ".caf", ".qta", ".aif", ".aiff", ".waptt"}
TEXT_EXT = {".txt", ".md", ".srt"}
# lo que acompana a cada audio (se mueve y borra con el): texto, subtitulos, notas, resumen, hablantes y su exportacion
SIDE_EXTS = (".txt", ".srt", ".md", ".resumen.md", voices.SIDECAR_EXT, ".hablantes.txt")
MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4",
        ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aac": "audio/aac",
        ".webm": "audio/webm", ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".wma": "audio/x-ms-wma",
        ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".oga": "audio/ogg", ".waptt": "audio/ogg", ".amr": "audio/amr", ".3gp": "audio/3gpp",
        ".3gpp": "audio/3gpp", ".3ga": "audio/3gpp", ".caf": "audio/x-caf", ".qta": "video/quicktime",
        ".aif": "audio/aiff", ".aiff": "audio/aiff",
        ".woff2": "font/woff2",  # las tipografías de la marca (static/*.woff2)
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
# Atributos de OneDrive "solo en linea": leer el archivo dispararia la descarga
CLOUD_ONLY = 0x400000 | 0x1000
LIVE_PRESETS = tuple(p["id"] for p in engines.PRESETS)  # en directo: por lotes y beam 1, cambia el modelo
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _-](\d{2})-(\d{2})-(\d{2})")


VERSION_FILE = os.path.join(APP_DIR, "version.txt")  # lo escribe scripts/build_zip.py: «AAAA-MM-DD · sha corto»
REPO_URL = "https://github.com/bastegman/tenca"  # el repo se llamaba «chuleta» (GitHub redirige)
# Acerca de > «invitar al autor a un café»: la pagina de donaciones de PayPal del autor (abre el navegador del sistema)
DONAR_URL = "https://www.paypal.com/donate?business=bdo.st.f%40gmail.com&currency_code=EUR"
PKG_ROOT = APP_NAME  # carpeta raiz del zip instalable (Tenca/INSTALAR.bat, Tenca/_app/...)

# Acerca de > el tributo a la tenca (Mimus thenca): «Historia natural y biologia reproductiva de la Tenca (Mimus thenca)
# en Chile central», Boletin del Museo Nacional de Historia Natural de Chile 61: 43-53 (2012). El DOI es del Museo
# (prefijo 10.54830 en Crossref) y sobrevive a un cambio de su web
TENCA_URL = "https://doi.org/10.54830/bmnhn.v61.2012.161"


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


# Carpetas de listas conectadas (vocabulario desde una carpeta, msg 1148): sus listas .txt se leen en vivo, sin copiar
# nada. Van aparte de LINKS: no salen en el arbol de reuniones ni como destino de grabacion, y safe() no las resuelve
# (ahi no se graba, ni se mueve, ni se transcribe nada). Las rutas internas de sus listas empiezan por ":v<id>" (el id
# de LINKS es hexadecimal: nunca empieza por "v", asi que no chocan).
VLINK = LINK + "v"
VOCAB_LINKS = {}  # id -> {"name", "path"} (ajuste "vocab_links"; se cargan al arrancar)


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
    if VOCAB_LINKS and not _inside(abs_path, ROOT):  # una lista de una carpeta conectada
        for vid, l in list(VOCAB_LINKS.items()):
            if _inside(abs_path, l["path"]):
                rel = os.path.relpath(abs_path, l["path"])
                return VLINK + vid + ("" if rel == "." else "/" + rel.replace(os.sep, "/"))
    rel = os.path.relpath(abs_path, ROOT)
    return "" if rel == "." else rel.replace(os.sep, "/")


def vocab_link_path(rel):
    """Ruta absoluta de `rel` (":v<id>/…") dentro de su carpeta de listas conectada (para abrirla en el Explorador)."""
    rel = (rel or "").replace("\\", "/").strip("/")
    vid, _, rest = rel[len(VLINK):].partition("/")
    l = VOCAB_LINKS.get(vid) if rel.startswith(VLINK) else None
    if not l:
        raise ApiError("Esa carpeta de listas ya no está conectada.", 404)
    p = os.path.normpath(os.path.join(l["path"], rest))
    if not _inside(p, l["path"]):
        raise ApiError(f"Ruta fuera de las carpetas de {APP_NAME}", 403)
    return p


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

EXPORT_DIR = os.path.join(APP_DIR, "export")  # INSTALAR.bat, ABRIR TENCA.bat y LEEME.txt (van a la raiz)


def export_code():
    """Todo el codigo de _app (.py, .bat y .ps1; sin subcarpetas ni python.txt): un modulo nuevo entra solo."""
    return sorted(n for n in os.listdir(APP_DIR)
                  if n.endswith((".py", ".bat", ".ps1")) and os.path.isfile(os.path.join(APP_DIR, n))
                  and not de_un_nombre_anterior(n))


def de_un_nombre_anterior(name):
    """Un fichero que la app tenia con un nombre anterior (el «ABRIR ….bat» de la raiz, el .bat de _app y los
    iconos). El instalador no borra nada de _app al actualizar, asi que en una copia instalada encima de Chuleta siguen
    ahi; el paquete de «Compartir» no los lleva (saldria el nombre viejo, y dos lanzadores). notas.js no es uno: es la
    funcion de tomar notas."""
    from common import APP_FORMER_NAMES
    viejos = {x.casefold() for n in APP_FORMER_NAMES
              for x in (f"ABRIR {n}.bat", f"{n}.bat", f"{n}.ico", f"{n}.png")}
    return name.casefold() in viejos


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
    files += [(e.path, root + "_app/static/" + e.name) for e in os.scandir(STATIC_DIR)
              if e.is_file() and not de_un_nombre_anterior(e.name)]
    templates = [e for e in os.scandir(EXPORT_DIR) if e.is_file() and not de_un_nombre_anterior(e.name)]
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
                      + cuantos(len(dig(("vocab_links",)) or []), "carpeta de listas", "carpetas de listas")
                      + cuantos(len(dig(("perfil", "outputs")) or []), "destino de salida", "destinos de salida")
                      + cuantos(len(dig(("project_folders",)) or {}) + len(dig(("workstream_folders",)) or {}),
                                "carpeta principal asignada", "carpetas principales asignadas")
                      + (["la carpeta por reunión apagada"] if dig(("carpeta_por_reunion",)) is False else []))
        self.broken = {"copia": os.path.basename(copia) if copia else None, "error": why, "recuperado": recuperado}
        log(f"   settings.json no se puede leer ({why})"
            + (f": copia en {os.path.basename(copia)}" if copia else "")
            + f"; recuperado: {', '.join(recuperado) or 'nada'}; lo demás vuelve a los valores por defecto")
        return saved

    # de un settings.json roto, ademas de los SECRETS. Las carpetas principales asignadas (y si la carpeta por reunion
    # esta apagada, con su marca de migracion) tambien: no hay donde volver a ponerlas en la interfaz (revision de la
    # integracion del 08/11/2026)
    RESCUE = (("links",), ("vocab_links",), ("perfil", "outputs"), ("project_folders",), ("workstream_folders",),
              ("carpeta_por_reunion",), ("carpetas_v",), ("nombre_v",), ("aprendido",))

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
                elif keys in (("project_folders",), ("workstream_folders",)):  # {proyecto o id de lista: carpeta}
                    ok = isinstance(v, dict) and bool(v) and all(isinstance(a, str) and isinstance(b, str)
                                                                 for a, b in v.items())
                elif keys == ("carpeta_por_reunion",):  # encendida es lo de por defecto
                    ok = v is False
                elif keys == ("carpetas_v",):  # la migracion ya hecha no se repite
                    ok = isinstance(v, int) and not isinstance(v, bool) and v >= 1
                elif keys == ("nombre_v",):  # la del nombre de la app (migrate_app_name), tampoco
                    ok = isinstance(v, str) and bool(v)
                elif keys == ("aprendido",):  # lo que aprendiste a corregir: no hay de donde volver a sacarlo
                    ok = isinstance(v, dict) and any(aprendido_de({"aprendido": v}).values())
                else:  # links, vocab_links y outputs: listas de {"path": …}
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
    "vocab_links": [],        # carpetas de listas conectadas: [{"name", "path"}] (se leen en vivo; VOCAB_LINKS)
    "rec_folder": "",         # ultima carpeta donde se grabo (para las reuniones sin tema)
    # carpeta principal asignada (rec_folder_origin): proyecto -> carpeta y, para un workstream que quiera la suya, id
    # de su lista -> carpeta. Sin asignar, la del proyecto en el arbol (la que tiene su Vocabulario). Hasta el
    # 07/11/2026 project_folders era «la ultima elegida» para ese proyecto: lo que habia se queda como asignacion
    "project_folders": {},
    "workstream_folders": {},
    # una carpeta por reunion, «AAAA-MM-DD Nombre», dentro de la principal (msg 1158): el audio y sus textos van dentro
    "carpeta_por_reunion": True,
    # plan de grabacion (plan.py, 07/11/2026): idioma por proyecto y por workstream (id de su lista), y si al grabar se
    # transcribe en directo por defecto (Ajustes › Al grabar; «Solo grabar» en la tarjeta vale para esa reunion)
    "project_langs": {},
    "workstream_langs": {},
    "rec_directo": True,
    "favorites": [],          # [{"kind": "a"|"f", "path": ...}] (estilo Notion)
    "folder_icons": {},       # carpeta -> emoji
    "meeting_detect": True,   # avisar si Teams/Zoom/… usa el micro y no se esta grabando
    # que la ventana no salga al compartir pantalla ni en capturas (window.py, msg 1242); se aplica al momento
    "ocultar_al_compartir": True,
    "outlook_on": False,      # leer la cita en curso de Outlook clasico (COM, pywin32): nombre, tema y asistentes
    # si Outlook deja leer los asistentes sin preguntar ("si"), pregunta ("pregunta": solos no se leen, solo con
    # «Probar») o no deja ("deniega"); "" = sin comprobar. Lo apunta calendario.py (Object Model Guard)
    "outlook_asistentes": "",
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
    # aprender de tus correcciones (aprender.py): reglas «lo que escribio Whisper → lo correcto» que aceptaste, por
    # proyecto, y lo que dijiste que no (aprendido_de lo valida)
    "aprendido": {"reglas": [], "no": [], "no_reglas": []},
    "voices_enabled": False,  # quien habla (diarizacion local + huellas de voz): opcional, apagado por defecto
    # perfil de reunion por defecto (Ajustes › Al grabar, y «Usarlo en: todas» en la tarjeta de grabar).
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


def proveedor_elegido(st):
    """Si ya hay un proveedor de la nube elegido: «API externa» elegida, o una clave, una direccion propia o una de
    Azure guardadas. En una instalacion nueva no lo hay, y la interfaz no da ninguno por elegido (Bernardo, msg 1386:
    «Lo que no quiero es sugerir Groq a un nuevo instalador»). cloud.provider sigue siendo "groq" por defecto en los
    ajustes, para no cambiar nada a quien ya lo usa sin haberlo guardado nunca."""
    c = st.get("cloud") or {}
    # un proveedor que no es el de por defecto solo puede venir de una eleccion (aunque aun no tenga clave)
    return (procesado_de(st) == "api" or c.get("provider") not in ("groq", "", None)
            or any(str(c.get(k) or "").strip() for k in ("api_key", "base_url", "azure_endpoint"))
            or any(c.get(f) for f in engines.EXTRA_KEYS_FIELDS.values()))


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
                    # el prefijo de las grabadoras y los móviles (AUDIO_, VID-, REC…) va delante de la fecha; el
                    # nombre que pone la app, detrás: solo se quita delante. Antes se quitaba de todo, y «… - Audio
                    # de WhatsApp» quedaba en «de WhatsApp» y «Recursos humanos», en «ursos humanos» (09/11/2026)
                    antes = re.sub(r"^(AUDIO|VID|REC)$", "", stem[:m.start()].strip(" -_·"), flags=re.I)  # solo
                    antes = re.sub(r"^(AUDIO|VID|REC)[-_ ]+", "", antes)  # delante de un nombre, en mayúsculas
                    label = (antes + " " + stem[m.end():]).strip(" -_·")
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
        if rel and os.path.normcase(rel) in mias and not is_link_root(rel):
            node["mia"] = True  # la creo la app para una reunion (la interfaz: «Se guarda en», donde esta)
            if not node["folders"] and not node["docs"] and len(node["audios"]) == 1:
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
                        "tu": bool(d.get("tu")),
                        # sin nombre: ni persona ni «Tú» (Hoy › Para revisar, «2 voces sin nombre»)
                        "sin_nombre": sum(1 for h, i in hs.items() if not i.get("breve") and not i.get("persona")
                                          and h != d.get("tu"))}
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


class TraduccionDestino:
    """Ultimo destino de la traduccion del directo («es» o «en») elegido en cada reunion (ruta interna del audio), en
    DATA_DIR/traduccion_destino.json: si la app se reinicia a mitad de reunion, o la apagas y la vuelves a encender,
    sigue al mismo idioma. No va en la cabecera del .md: las versiones anteriores toman una clave que no conocen por
    notas (Temas). Solo se usa durante el directo, asi que no sigue a la reunion si se mueve despues. Guarda las
    MAX ultimas."""

    MAX = 300

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.data = ({k: v for k, v in data.items() if isinstance(k, str) and v in live.TRANSLATE_TO}
                     if isinstance(data, dict) else {})

    def get(self, rel):
        return self.data.get(rel)

    def set(self, rel, to):
        if to not in live.TRANSLATE_TO:
            return
        with self.lock:
            if self.data.get(rel) == to and list(self.data)[-1:] == [rel]:
                return
            self.data.pop(rel, None)
            self.data[rel] = to  # al final: los mas recientes son los que se quedan
            while len(self.data) > self.MAX:
                self.data.pop(next(iter(self.data)))
            try:
                tmp = f"{self.path}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError:  # no poder recordarlo no impide traducir
                log_exc()


_TRAD, _TRAD_LOCK = {}, threading.Lock()


def traduccion_destinos():
    """El almacen TraduccionDestino de este PC (uno por DATA_DIR, que los tests cambian)."""
    path = os.path.join(DATA_DIR, "traduccion_destino.json")
    with _TRAD_LOCK:
        if path not in _TRAD:
            _TRAD[path] = TraduccionDestino(path)
        return _TRAD[path]


def traduccion_destino(rel, st):
    """A que idioma se traduce el directo de una reunion: el ultimo elegido en ella; si no, el idioma del resumen de
    Ajustes si es español o inglés; si no («el de la reunión»), español (msg 1152)."""
    to = traduccion_destinos().get(rel)
    if to in live.TRANSLATE_TO:
        return to
    lang = (st or {}).get("summary_lang") or "es"
    return lang if lang in live.TRANSLATE_TO else "es"


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
    extra = {k: node[k] for k in ("linked", "missing", "por_reunion", "mia") if k in node}
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
# Los textos de «Participantes» que la app ha escrito desde Outlook/Teams en esta sesion (calendario.join_participants:
# «Apellido, Nombre; …»). Solo con ellos la pista de quien habla cuenta cada trozo entre «;» como una persona
# (voices.count_participants sugeridos=True), tambien despues de «Confirmar» (que quita participantes_origen sin cambiar
# el texto). Editado a mano (en la app o fuera), el texto ya no esta aqui y se cuenta como texto libre, de mas. Solo en
# memoria: tras reiniciar la app, tambien como texto libre.
TEXTOS_SUGERIDOS = set()
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
VOCAB_MAX_BYTES = 64 * 1024  # una .txt de una carpeta conectada mas grande no es una lista (Whisper lee ~45 terminos)


def _decode_list(raw):
    """Texto de una lista: UTF-8 (con o sin BOM), UTF-16 con BOM (Excel «Texto Unicode», el Bloc de notas «Unicode») o,
    linea a linea, la pagina de codigos de Windows donde no es UTF-8 (cp1252: el Bloc de notas antiguo y Excel «Texto
    delimitado por tabulaciones»). Antes todo se leia como UTF-8 y «José» llegaba a Whisper como «Jos�». Por lineas:
    un byte suelto (o un fichero leido a medio guardar) solo cambia su linea; con BOM de UTF-8, esa linea lleva «�»."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    bom = raw.startswith(b"\xef\xbb\xbf")
    out = []
    for line in (raw[3:] if bom else raw).split(b"\n"):
        try:
            out.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(line.decode("utf-8", errors="replace") if bom else line.decode("cp1252", errors="replace"))
    return "\n".join(out)


def read_vocab(path):
    desc, terms = [], []
    with open(path, "rb") as f:
        text = _decode_list(f.read())
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            desc.append(s.lstrip("#").strip())
        elif s:
            terms += engines.split_terms(s.replace("\t", ","))  # columnas de Excel separadas por tabuladores
    return " ".join(d for d in desc if d), terms


def _one_per_line(path):
    """La lista esta escrita con un termino por linea (sin comas, puntos y comas ni tabuladores) y tiene mas de uno."""
    with open(path, "rb") as f:
        lines = [s.strip() for s in _decode_list(f.read()).splitlines()]
    lines = [s for s in lines if s and not s.startswith("#")]
    return len(lines) > 1 and not any(re.search(r"[,;\t]", s) for s in lines)


def vocab_link_name(path):
    """Nombre de proyecto de una carpeta de listas conectada: el de la carpeta o, si se llama Vocabulario, el de la
    carpeta que la contiene (la del proyecto, como al importar)."""
    path = os.path.normpath(path)
    name = os.path.basename(path)
    if fold(name) == VOCAB_FOLDER and os.path.basename(os.path.dirname(path)):
        name = os.path.basename(os.path.dirname(path))
    return unicodedata.normalize("NFC", name)


def _vocab_link_ok(f, base, audio_stems, avisos):
    """Una .txt de una carpeta conectada vale como lista si no es oculta (._x.txt de un USB de Mac), ni la transcripcion
    de una grabacion de al lado (<audio>.txt, <audio>.hablantes.txt), ni esta solo en OneDrive (leerla la descargaria:
    se salta con aviso), ni pesa mas de VOCAB_MAX_BYTES (no es una lista: tambien con aviso)."""
    if f.name.startswith((".", "~")):
        return False
    stem = f.name[:-4].lower()
    if stem in audio_stems or (stem.endswith(".hablantes") and stem[:-len(".hablantes")] in audio_stems):
        return False
    try:
        st = f.stat()
    except OSError:
        return False
    rel = os.path.relpath(f.path, base).replace(os.sep, "\\")
    if getattr(st, "st_file_attributes", 0) & CLOUD_ONLY:
        avisos.append({"lista": rel, "motivo": "nube"})
        return False
    if st.st_size > VOCAB_MAX_BYTES:
        avisos.append({"lista": rel, "motivo": "grande", "kb": -(-st.st_size // 1024)})
        return False
    # un documento de reunion de los destinos de salida (lleva la marca «generado_por» arriba): un destino con
    # «subcarpeta por proyecto» que contiene la carpeta conectada los escribiria ahi
    return not salidas.is_ours(f.path, "txt")


def _list_vocab(avisos=None, conectadas=True):
    """list_vocab y, en `avisos` (id de carpeta conectada -> [{"lista", "motivo"}]), las listas que se saltan de las
    carpetas conectadas. conectadas=False: solo las del arbol (sin leer las carpetas conectadas, que pueden estar en
    OneDrive o en la red)."""
    out, seen = [], set()

    def norm(p):
        return os.path.normcase(os.path.normpath(p))

    vlinks = list(VOCAB_LINKS.items())
    # el proyecto de las listas de la raiz se llama como su carpeta, igual que todos (root_name: «Chuleta» si se instalo
    # encima de Chuleta); asi sus reuniones y sus documentos «por proyecto» siguen con el nombre de antes (09/11/2026)
    top_names = {norm(ROOT): root_name(), **{norm(l["path"]): l["name"] for l in LINKS.values()}}
    top_names.update({norm(l["path"]): l["name"] for _, l in vlinks if norm(l["path"]) not in top_names})

    def add(f, project, d, extra):
        key = norm(f.path)
        if key in seen:  # una carpeta conectada que contiene otra de la app: la lista ya esta, con su id de siempre
            return
        seen.add(key)
        out.append(dict({"id": rel_of(f.path), "project": project, "folder": rel_of(d),
                         "name": unicodedata.normalize("NFC", f.name[:-4]), "_abs": f.path}, **extra))

    def txts(d, ok):
        entries = list(os.scandir(d))
        files = [f for f in entries if f.is_file() and f.name.lower().endswith(".txt")]
        if ok:  # carpeta conectada: fuera las transcripciones de las grabaciones de al lado y lo que no es una lista
            stems = {s.lower() for s, x in (os.path.splitext(e.name) for e in entries) if x.lower() in AUDIO_EXT}
            files = [f for f in files if ok(f, stems)]
        return sorted(files, key=lambda f: (fold(f.name) != "general.txt", natural_key(f.name)))

    def walk(d, depth, max_depth=3, ok=None, extra=None):
        try:
            entries = sorted(os.scandir(d), key=lambda e: natural_key(e.name))
        except OSError:
            return
        for e in entries:
            if not e.is_dir() or e.name.startswith((".", "_")) or in_app_dir(e.path):
                continue
            if fold(e.name) != VOCAB_FOLDER:
                if depth < max_depth:
                    walk(e.path, depth + 1, max_depth, ok, extra)
                continue
            project = unicodedata.normalize("NFC", top_names.get(norm(d)) or os.path.basename(d))
            try:
                files = txts(e.path, ok)
            except OSError:
                continue
            for f in files:
                add(f, project, d, extra or {})

    walk(ROOT, 0)
    for l in LINKS.values():  # tambien las carpetas vinculadas (p. ej. la del proyecto en OneDrive)
        walk(l["path"], 0)
    # y las carpetas de listas conectadas, en tres formas: <carpeta>\*.txt sueltas y <carpeta>\Vocabulario\*.txt (del
    # proyecto que da su nombre la carpeta: vocab_link_name) y <carpeta>\<Proyecto>\Vocabulario\*.txt
    for vid, l in (vlinks if conectadas else ()):
        base, warn = l["path"], [] if avisos is None else avisos.setdefault(vid, [])
        ok = lambda f, stems, base=base, warn=warn: _vocab_link_ok(f, base, stems, warn)  # noqa: E731
        extra = {"conectada": VLINK + vid}
        try:
            loose = txts(base, ok)
        except OSError:
            continue  # la carpeta ya no esta (renombrada, sin VPN, disco desconectado): sin listas
        first = len(out)
        for f in loose:
            add(f, unicodedata.normalize("NFC", l["name"]), base, dict(extra, _dir=base))
        walk(base, 0, max_depth=1, ok=ok, extra=extra)
        # dentro de una misma carpeta conectada, un tema por proyecto: Infra.txt suelta y Vocabulario\Infra.txt se
        # llamarian igual y, por nombres, no se sabria cual usar (find_vocab daria ninguna). Vale la primera
        seen_names, keep = set(), out[:first]
        for v in out[first:]:
            key = (fold(v["project"]), fold(v["name"]))
            if key in seen_names:
                warn.append({"lista": os.path.relpath(v["_abs"], base).replace(os.sep, "\\"), "motivo": "repetida"})
                continue
            seen_names.add(key)
            keep.append(v)
        out[:] = keep
    return out


def list_vocab():
    return _list_vocab()


def vocab_links_estado(vs=None, avisos=None):
    """Para la interfaz: cada carpeta de listas conectada con su ruta interna, nombre, ruta, si existe, cuantas listas
    da y lo que se salta (avisos). `vs` y `avisos`: los de una llamada a _list_vocab ya hecha."""
    if vs is None:
        avisos = {}
        vs = _list_vocab(avisos)
    out = []
    for vid, l in list(VOCAB_LINKS.items()):
        rel = VLINK + vid
        out.append({"rel": rel, "name": l["name"], "path": l["path"], "existe": os.path.isdir(l["path"]),
                    "listas": sum(1 for v in vs if v.get("conectada") == rel), "avisos": (avisos or {}).get(vid, [])})
    return out


def _project_dir(v):
    """La carpeta del proyecto de una lista: la que contiene su carpeta Vocabulario (o, con las listas sueltas de una
    carpeta conectada, esa carpeta: si no, seria la de encima y podria contener reuniones de otro proyecto)."""
    return v.get("_dir") or os.path.dirname(os.path.dirname(v["_abs"]))


def root_names():
    """Los nombres del proyecto de las listas de la raiz: el de su carpeta (root_name, con el que se llama ahora), el
    de la app y los que tuvo («Chuleta», «Notas»: reuniones y ajustes guardados con ellos). Tambien para la interfaz
    (/api/config: notas.js, deProyecto)."""
    from common import APP_FORMER_NAMES
    return list(dict.fromkeys((root_name(), APP_NAME) + tuple(APP_FORMER_NAMES)))


def _is_app_name(project):
    """El proyecto de las listas de la raiz con cualquiera de sus nombres (root_names)."""
    return fold(project or "") in {fold(a) for a in root_names()}


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
        hit = _closest(named, near)
        own = [v for v in named if not v.get("conectada")]
        # si no, la de la app o la vinculada si solo hay una: conectar una carpeta con las mismas listas (las que se
        # importaron de ella, por ejemplo) no deja sin vocabulario a las reuniones que ya lo tenian
        return hit or (own[0] if len(own) == 1 and len(own) < len(named) else None)
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


def idioma_repaso(usados):
    """Idioma del repaso final de un directo en el que se cambió el idioma de la reunión (msg 1150: empezó en inglés y
    pasó al portugués). Con el idioma del final para toda la grabación, lo dicho antes se pasaría a ese idioma; con
    los dos que se usaron, la mezcla de esos dos si existe (en + pt → «en-pt») y si no, detectar (""). None: no
    cambió (el de siempre, el de la reunión al terminar). usados: los idiomas fijados en la sesión, en orden (None =
    detectar)."""
    usados = list(usados or [])
    if len(usados) < 2:
        return None
    sueltos = set()
    for u in usados:
        if not u:
            return ""  # en algún momento se detectaba: detectar
        sueltos.update(engines.PAIRS.get(u, (u,)))
    if len(sueltos) == 1:
        return next(iter(sueltos))
    if len(sueltos) == 2:
        return next((code for code, par in engines.PAIRS.items() if set(par) == sueltos), "")
    return ""


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
         ("reunion", calendario.join_participants(sug.get("participants")) or None), ("ultimo", ""))
    return dict(out, origen=origen)


def check_perfil(b, known=None, actual=None):
    """Valida lo que llega para el perfil (solo las claves presentes) y lo devuelve normalizado. `known`: los destinos
    de salida ya guardados (se aceptan aunque ahora no esten disponibles; los nuevos tienen que existir). `actual`: el
    perfil guardado; su carpeta y su lista, sin cambiar, se aceptan aunque ahora no se encuentren (una vinculada
    desconectada, OneDrive sin conexion): Ajustes las devuelve tal cual con cualquier «Guardar» y no deben perderse ni
    impedir guardar lo demas (revision 2 de B2, P1-3)."""
    out, actual = {}, actual or {}
    if "folder" in b:
        rel = b["folder"]
        if rel is not None:
            rel = str(rel).replace("\\", "/").strip("/")
            if rel != actual.get("folder"):
                folder = safe(rel, "dir")
                if in_app_dir(folder):
                    raise ApiError("No se puede grabar ahí")
        out["folder"] = rel
    if "vocab" in b:
        vid = b["vocab"]
        if vid is not None:
            vid = str(vid).strip()
            if vid and vid != actual.get("vocab") and not find_vocab(None, None, vid):
                raise ApiError("Ese workstream ya no existe.")
        out["vocab"] = vid
    if "context" in b:
        out["context"] = " ".join(str(b["context"] or "").split())[:engines.ABOUT_CHARS]
    if "outputs" in b:
        roots = [ROOT] + [l["path"] for l in LINKS.values()]  # donde viven las grabaciones: ahi no van documentos
        old = [d["path"] for d in salidas.destinos_de(known)]
        out["outputs"] = salidas.check_destinos(b["outputs"], APP_DIR, roots, old)
        for o in out["outputs"]:  # ni en una carpeta de listas conectada ni encima de una (con «subcarpeta por
            # proyecto», los documentos caerian dentro): sus .txt se tomarian por listas
            if any(_inside(o["path"], k) and _inside(k, o["path"]) for k in old):
                continue  # ya estaba guardado: se acepta (si no, no se podria guardar ningun otro ajuste)
            for l in list(VOCAB_LINKS.values()):
                if _inside(o["path"], l["path"]):
                    raise ApiError(f"Esa carpeta es la de tus listas de vocabulario («{l['name']}»): los documentos "
                                   "de las reuniones se tomarían por listas. Elige otra carpeta para los documentos.")
                if _inside(l["path"], o["path"]):
                    raise ApiError(f"Dentro de esa carpeta está la de tus listas de vocabulario («{l['name']}»): con "
                                   "«subcarpeta por proyecto», los documentos de las reuniones podrían caer en ella. "
                                   "Elige otra carpeta para los documentos.")
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


def _en_carpeta_de_listas(d):
    """`d` esta en una carpeta de listas conectada: de las carpetas que la contienen (la raiz, las vinculadas y las de
    listas), la mas interior es de listas. Una vinculada dentro de una de listas es del arbol (el usuario la vinculo
    para grabar); una de listas dentro de una vinculada, no."""
    best = (-1, False)
    for path, listas in ([(ROOT, False)] + [(l["path"], False) for l in LINKS.values()]
                         + [(l["path"], True) for l in VOCAB_LINKS.values()]):
        n = len(os.path.normcase(os.path.normpath(path)))
        if n > best[0] and _inside(d, path):
            best = (n, listas)
    return best[1]


def project_home(v, vs=None):
    """Ruta interna de la carpeta del proyecto de una lista (la que contiene su carpeta Vocabulario), si esta en el
    arbol de la app (su raiz o una vinculada); None si no. Una carpeta de vocabulario conectada nunca es destino de
    grabacion (VLINK), tampoco si queda dentro de una vinculada (_en_carpeta_de_listas): una lista de alli usa la
    carpeta de su proyecto en el arbol, la de otra lista suya que si este (`vs`: las de list_vocab, si ya se tienen),
    y si no hay, ninguna."""
    if not v or not v.get("_abs"):
        return None
    d = _project_dir(v)
    if v.get("conectada") or _en_carpeta_de_listas(d):
        for o in (list_vocab() if vs is None else vs):
            if not o.get("conectada") and o.get("_abs") and _of_project(o, v["project"]):
                home = project_home(o, ())
                if home is not None:
                    return home
        return None
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


def rec_folder_origin(v, st, vs=None):
    """(carpeta principal, origen) de una grabacion nueva con el tema `v` (una lista de list_vocab, o None): la asignada
    a su workstream («workstream»), la asignada a su proyecto («proyecto») o la carpeta del proyecto en el arbol
    («carpeta_proyecto»); sin tema (o si ninguna existe ya), la fija del perfil («perfil»), la ultima usada («ultimo») o
    la raiz («raiz»). Es la unica regla: la usan Ctrl+Alt+R, el aviso de Teams, el dialogo y /api/perfil. `vs`: las
    listas de list_vocab, si ya se tienen (project_home)."""
    if v:  # en orden y solo hasta la primera que sirva (project_home de una lista conectada recorre las listas)
        for origin, get in (("workstream", lambda: (st.get("workstream_folders") or {}).get(v.get("id"))),
                            ("proyecto", lambda: (st.get("project_folders") or {}).get(v.get("project"))),
                            ("carpeta_proyecto", lambda: project_home(v, vs))):
            rel = get()
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
    interfaz. Solo con las listas del arbol: las de una carpeta conectada no tienen carpeta propia (project_home) y la
    asignada a su proyecto sale igual en el segundo paso. Asi /api/config no lee las carpetas conectadas, que pueden
    estar en la red o sin sincronizar (revision de la integracion del 08/11/2026)."""
    out, vs = {}, _list_vocab(conectadas=False)
    for v in vs:
        if v["project"] not in out:
            # sin id: la del proyecto, no la de un workstream
            rel, origin = rec_folder_origin(dict(v, id=None), st, vs)
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


# «Subir audio»: lo que se dice cuando no vale un archivo (la lista entera está en AUDIO_EXT)
FORMATOS_SUBIR = "m4a, mp3, opus, ogg, wav, aac, webm, flac, amr o un vídeo mp4 o mov"
SUBIR_FECHA_MIN = 946684800  # 01/01/2000: antes de eso, la fecha del archivo no es la de la reunión


def fecha_subida(v):
    """La fecha de un audio subido, en segundos: la de la tarjeta («AAAA-MM-DDTHH:MM», hora local) o la del archivo
    (File.lastModified del navegador, en milisegundos). Si no hay o no es razonable (antes de 2000 o de mañana en
    adelante), ahora."""
    ahora, t = time.time(), None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        t = float(v)
    elif isinstance(v, str) and v.strip():
        x = v.strip()
        try:
            t = float(x)
        except ValueError:
            for fmt, n in (("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%dT%H:%M", 16)):
                try:
                    t = time.mktime(time.strptime(x[:n], fmt))
                    break
                except (ValueError, OverflowError):
                    continue
    if t is not None and t > 1e11:  # milisegundos
        t /= 1000
    if t is None or t != t or not (SUBIR_FECHA_MIN <= t <= ahora + 86400):
        return ahora
    return t


SUBIENDO = ".subiendo-"  # lo que se está recibiendo en «Subir audio» (el árbol no enseña lo que empieza por punto)
SUBIENDO_VIEJO_S = 6 * 3600


def limpiar_subidas(folder):
    """Borra de `folder` lo que quedó a medias de una subida de hace horas (la app se cerró o el PC se apagó mientras
    llegaba): en Windows el punto no lo oculta del Explorador, y en OneDrive se sincronizaría."""
    try:
        viejo = time.time() - SUBIENDO_VIEJO_S
        for e in os.scandir(folder):
            if e.name.startswith(SUBIENDO) and e.is_file(follow_symlinks=False) and e.stat().st_mtime < viejo:
                try:
                    os.remove(e.path)
                    log(f"   borrado un resto de subida: {rel_of(e.path)}")
                except OSError:
                    pass
    except OSError:
        pass


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


def ruta_txt(rel):
    """Una ruta interna como la ve el usuario, igual en la tarjeta de Grabar, en el aviso de Ctrl+Alt+R, en el directo y
    en los avisos de mover: «Chuleta\\Proyecto Alfa\\2026-11-07 Comité\\», con la raiz por el nombre que tiene de
    verdad en el disco (root_name) y una vinculada por el suyo (plan.ruta_txt)."""
    return grabar_plan.ruta_txt(rel, {k: l["name"] for k, l in LINKS.items()}, root_name())


display_rel = ruta_txt  # el nombre de antes (rama de carpetas): era «\\Proyecto Alfa\\…», sin la raiz


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
    except OSError as e:
        try:
            os.remove(t)
        except OSError:
            pass
        # sin reintentar aqui: cada reintento copiaria otra vez el audio entero a la otra unidad
        raise ApiError("El archivo está en uso (¿OneDrive sincronizando o abierto en otro programa?). Prueba otra vez "
                       f"en unos segundos ({e.__class__.__name__}).")


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
        incompleto = False
        for f, t in reversed(done):  # deshacer, tambien con reintentos (OneDrive coge lo recien llegado)
            for attempt in range(12):
                try:
                    move_one(t, f)
                    break
                except (OSError, ApiError):
                    if attempt == 11:
                        log_exc()
                        incompleto = True
                    else:
                        time.sleep(0.25)
        if isinstance(e, ApiError):
            err = e
        elif isinstance(e, PermissionError):
            err = ApiError("El archivo está en uso (¿OneDrive sincronizando o abierto en otro programa?). "
                           "Prueba otra vez en unos segundos.")
        else:
            err = ApiError(f"No se ha podido mover ({e.__class__.__name__}).")
        err.incompleto = incompleto  # algo se quedo en el destino: el siguiente intento termina alli
        raise err


SIN_AUDIO = "Sin micrófono y sin el audio del PC no se graba nada: deja al menos uno de los dos."


def ajustes_previstos(st, a):
    """Los ajustes con lo que Ajustes › Al grabar tiene escrito y sin guardar (solo para enseñar el plan; no se guarda
    nada). Se aceptan las claves de esa sección, validadas como al guardar; lo que no vale se ignora."""
    st = copy.deepcopy(st)
    lang = str(a.get("language") or "").strip().lower() if "language" in a else None
    if lang is not None and (not lang or IDIOMA_RE.match(lang)):
        st["language"] = lang
    for k in ("rec_mic", "rec_sys"):
        if isinstance(a.get(k), str) and a[k].strip():
            st[k] = a[k].strip()[:300]
    if "rec_directo" in a:
        st["rec_directo"] = bool(a["rec_directo"])
    if a.get("live_preset") in LIVE_PRESETS:
        st["live_preset"] = a["live_preset"]
    if a.get("preset") in engines.PRESETS_BY_ID:  # Procesado › Motores: el de después («Solo grabar»)
        st["preset"] = a["preset"]
    if a.get("procesado") in PROCESADOS:  # la opción marcada en Procesado, aplicada o no: «Guardar» también la aplica
        st["procesado"] = a["procesado"]
    s = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    for k, clave in (("auto", "summary_auto"), ("local", "summary_auto_local")):
        if k in s:
            st[clave] = bool(s[k])
    if isinstance(a.get("prompt"), str):  # el vocabulario de las reuniones sin proyecto (Proyectos)
        st["prompt"] = a["prompt"]
    if "carpeta_por_reunion" in a:  # rama de carpetas: una carpeta por reunión
        st["carpeta_por_reunion"] = a["carpeta_por_reunion"] is not False
    if isinstance(a.get("perfil"), dict):
        try:
            claves = ("folder", "vocab", "context")
            # con el perfil guardado como referencia: su carpeta o su lista, aunque ahora no se encuentren, no tiran el
            # resto de lo escrito (el contexto) como «no vale» (revisión 3 de B2)
            update_perfil(st, check_perfil({k: x for k, x in a["perfil"].items() if k in claves}, actual=perfil_de(st)))
        except ApiError:
            pass
    return st


def _con_o_sin(d, k, val):
    """Copia del diccionario `d` con `k` = `val`, o sin `k` si `val` es None (vuelve a heredar)."""
    out = dict(d or {})
    if val is None:
        out.pop(k, None)
    else:
        out[k] = val
    return out


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


def _una_reunion_de_ese_dia(rel):
    """La carpeta `rel` («AAAA-MM-DD …») es la de una sola reunion de ese dia: dentro hay un solo audio y su nombre
    empieza por esa fecha. Una carpeta de proyecto que empieza por una fecha («2025-09-01 Proyecto X») tiene otras."""
    try:
        names = os.listdir(safe(rel, "dir"))
    except (ApiError, OSError):
        return False
    audios = [n for n in names if os.path.splitext(n)[1].lower() in AUDIO_EXT]
    return len(audios) == 1 and audios[0][:10] == rel.rpartition("/")[2][:10]


def migrate_main_folders(d):
    """Una vez (carpetas_v), al llegar la carpeta por reunion: la carpeta de cada proyecto que apuntaba la app era
    «la ultima usada», y la de quien ya se hacia una carpeta por reunion a mano es la de una reunion
    («AAAA-MM-DD …»): las nuevas acabarian dentro de ella. Esa pasa a ser su carpeta padre; lo demas se queda como
    asignacion. Igual con la ultima carpeta (rec_folder). Devuelve [(proyecto o «la última», vieja, nueva)]."""
    out = []

    def fix(rel, who):
        if isinstance(rel, str) and MEETING_LIKE_RE.match(rel.rpartition("/")[2]) and not is_link_root(rel) \
                and _una_reunion_de_ese_dia(rel):
            new = rel.rpartition("/")[0]
            out.append((who, rel, new))
            return new
        return rel
    d["project_folders"] = {k: fix(v, k) for k, v in (d.get("project_folders") or {}).items()}
    if d.get("rec_folder"):
        d["rec_folder"] = fix(d["rec_folder"], "la última")
    d["carpetas_v"] = 1
    return out


def migrate_app_name(d, raiz=None):
    """Lo que los ajustes guardan por el nombre del proyecto (la carpeta asignada, project_folders, y el idioma,
    project_langs) lleva, para las listas de la raiz, el nombre con el que lo buscaba la version anterior: el que dejo
    aqui la ultima vez (nombre_v; «Tenca» con la de 4553b9c, que lo llamaba como la app) o, sin marca, «Chuleta»
    (APP_FORMER_NAMES[0], el nombre de la app en todas las de antes). Ahora se llama como su carpeta en el disco
    (root_name, `raiz`): «Chuleta» en una instalacion encima de Chuleta, «Tenca» en una nueva. Pasa a ese nombre si aun
    no tiene uno, para que la carpeta y el idioma asignados sigan valiendo. Uno mas antiguo («Notas») no: ya no se usaba
    y puede ser un proyecto de verdad. Una vez por nombre: si despues se quita la asignacion de la raiz, no vuelve.
    Devuelve [(clave, nombre anterior)]."""
    from common import APP_FORMER_NAMES
    raiz = raiz or root_name()
    out, antes = [], d.get("nombre_v")
    if antes == raiz:
        return out
    antes = antes if isinstance(antes, str) and antes else APP_FORMER_NAMES[0]
    for key in ("project_folders", "project_langs"):
        m = d.get(key)
        if not isinstance(m, dict) or raiz in m:
            continue
        # el de ahora con otras mayusculas (una carpeta «tenca»: las busquedas son exactas) o, si no, el de antes
        k = next((k for k in m if fold(k) == fold(raiz)), None)
        if k is None and fold(antes) != fold(raiz):
            k = next((k for k in m if fold(k) == fold(antes)), None)
        if k is not None:
            m = dict(m)
            m[raiz] = m.pop(k)
            d[key] = m
            out.append((key, k))
    d["nombre_v"] = raiz
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
    if project and isinstance(d.get("aprendido"), dict):  # las reglas aprendidas son de un proyecto, por su nombre
        for r in d["aprendido"].get("reglas") or []:
            if isinstance(r, dict) and isinstance(r.get("proyecto"), str) and fold(r["proyecto"]) == fold(project[0]):
                r["proyecto"] = project[1]
    _idiomas_moved(d, old, new, folder, project)


def _idiomas_moved(d, old, new, folder, project):
    """Plan de grabacion: el idioma de cada workstream (por el id de su lista) y de cada proyecto (por su nombre)
    siguen a un tema o a una carpeta renombrados."""
    if d.get("workstream_langs"):
        wl = {}
        for vid, lang in d["workstream_langs"].items():
            m = moved_rel(vid, old, new, folder)
            wl[vid if m is None else m] = lang
        d["workstream_langs"] = wl
    pl = d.get("project_langs")
    if pl and project and project[0] in pl and project[1] not in pl:
        pl = dict(pl)
        pl[project[1]] = pl.pop(project[0])
        d["project_langs"] = pl


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


# ----------------------------------------------------------------------------- carpetas de listas conectadas

def check_vocab_link(path, outputs=()):
    """Valida una carpeta para conectarla como carpeta de listas y la devuelve normalizada. No vale: la de la app (ni
    una que la contenga o contenga tu usuario: demasiado grande), una de dentro de la app o de una carpeta vinculada
    (sus listas <Proyecto>\\Vocabulario\\*.txt ya se leen solas), una que se solape con otra ya conectada, la de un
    destino de salida (`outputs`: ahi se escriben las reuniones como documentos, que se tomarian por listas) ni una
    con grabaciones (sus .txt son transcripciones)."""
    path = os.path.normpath(os.path.expandvars((path or "").strip().strip('"')))
    if not os.path.isabs(path) or not os.path.isdir(path):
        raise ApiError("Esa carpeta no existe: " + path)
    if os.path.splitdrive(path)[1] in ("", os.sep):
        raise ApiError("Elige una carpeta, no una unidad entera.")
    if in_app_dir(path):
        raise ApiError("Esa carpeta es la de la propia app.")
    if _inside(ROOT, path) or _inside(os.path.expanduser("~"), path):
        raise ApiError(f"Esa carpeta es demasiado grande (contiene la de {APP_NAME} o tu usuario): elige la carpeta "
                       "donde tienes las listas.")
    if _inside(path, ROOT):
        raise ApiError(f"Esa carpeta ya está dentro de {APP_NAME}: las listas de <Proyecto>\\Vocabulario\\ se leen "
                       "solas, sin conectarla.")
    for l in list(LINKS.values()):
        if _inside(path, l["path"]):
            raise ApiError(f"Esa carpeta está dentro de «{l['name']}», que ya está vinculada: pon las listas en "
                           f"«{l['name']}\\Vocabulario» y se leen solas.")
    vid = link_id(path)
    for other, l in list(VOCAB_LINKS.items()):
        if other != vid and (_inside(path, l["path"]) or _inside(l["path"], path)):
            raise ApiError(f"Se solapa con la carpeta de listas «{l['name']}», que ya está conectada.")
    for d in outputs or ():
        if _inside(path, d):
            raise ApiError("En esa carpeta se guardan los documentos de las reuniones (destinos de salida): se "
                           "tomarían por listas. Elige la carpeta donde tienes solo las listas.")
    audio = salidas.first_audio(path)
    if audio:
        raise ApiError(f"Esa carpeta tiene grabaciones («{audio}»): sus .txt son transcripciones. Elige la carpeta "
                       "donde tienes solo las listas.")
    return path


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
        raise ApiError("En esa carpeta no hay listas de vocabulario (<Proyecto>\\Vocabulario\\<Workstream>.txt).")
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


def copia_original(rel, base, auto=False):
    """La copia de lo que transcribio Whisper (si aun no hay): la hacen la primera correccion a mano y el primer cambio
    que Tenca aprende de ti (auto). Devuelve si la ha hecho ahora."""
    bdir = backup_dir(rel)
    if os.path.isdir(bdir):
        return False
    os.makedirs(bdir)
    for ext in (".srt", ".txt"):
        if os.path.isfile(base + ext):
            shutil.copyfile(base + ext, os.path.join(bdir, "original" + ext))
    with open(os.path.join(bdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(dict({"path": rel, "at": time.time()}, **({"auto": True} if auto else {})), f, ensure_ascii=False)
    return True


# ----------------------------------------------------------------------------- aprender de tus correcciones
# (aprender.py, item 17, 09/11/2026). En los ajustes ("aprendido"): las reglas que aceptaste, {"de", "a",
# "proyecto", "at"}, que se aplican al terminar cada transcripcion de ese proyecto ("" = las reuniones sin proyecto), y
# lo que dijiste que no ("no": terminos; "no_reglas": [de, a], en aprender.normal). Lo que Tenca cambio en cada reunion
# va en su copia del original (originales/<md5>/aprendido.json): se mueve, se restaura y se borra con ella.
APRENDIDO_FILE = "aprendido.json"


def aprendido_de(st):
    """El ajuste "aprendido", validado (uno escrito a mano o de otra version no rompe nada: lo que no vale se
    ignora)."""
    a = st.get("aprendido") if isinstance(st.get("aprendido"), dict) else {}
    txt = lambda x: isinstance(x, str) and bool(x.strip())  # noqa: E731
    reglas = [dict(r, proyecto=r.get("proyecto") if isinstance(r.get("proyecto"), str) else "")
              for r in a.get("reglas") or [] if isinstance(r, dict) and txt(r.get("de")) and txt(r.get("a"))]
    no = [x for x in a.get("no") or [] if txt(x)]  # como se escribió (se comparan con aprender.normal)
    no_reglas = [list(x) for x in a.get("no_reglas") or [] if isinstance(x, list) and len(x) == 2 and all(map(txt, x))]
    return {"reglas": reglas, "no": no, "no_reglas": no_reglas}


def mismo_proyecto(a, b):
    """Dos nombres del mismo proyecto: iguales sin tildes ni mayusculas, o los dos de la raiz (root_names)."""
    return fold(a or "") == fold(b or "") or (bool(a) and bool(b) and _is_app_name(a) and _is_app_name(b))


def lista_de_reunion(abs_audio, props=None):
    """La lista del tema de la reunion (una de list_vocab) o None: sin tema, Whisper recibe el vocabulario general."""
    props = md_props(abs_audio) if props is None else props
    if not props.get("workstream"):
        return None
    try:
        vid = temas().get(rel_of(abs_audio))
        return find_vocab(props.get("proyecto"), props.get("workstream"), vid, near=abs_audio)
    except OSError:
        return None


def proyecto_de_reunion(abs_audio, props=None):
    """El proyecto de la reunion para las reglas aprendidas: el de su lista o, si ya no esta, el de su cabecera ("" =
    sin proyecto)."""
    props = md_props(abs_audio) if props is None else props
    v = lista_de_reunion(abs_audio, props)
    return v["project"] if v else (props.get("proyecto") or "")


def original_de(rel):
    """Lo que escribio Whisper (la copia de originales) como [{"start", "text"}], o None si no hay copia."""
    bdir = backup_dir(rel)
    for ext in (".srt", ".txt"):
        p = os.path.join(bdir, "original" + ext)
        if os.path.isfile(p):
            text = read_text(p)
            if ext == ".srt":
                return parse_srt(text)
            return [{"start": None, "end": None, "text": l.strip()} for l in text.splitlines() if l.strip()]
    return None


def registro_aprendido(rel):
    """Lo que Tenca ha cambiado en esta reunion: {"cambios": [{"start", "de", "a", "pos", "texto"}], "deshechas":
    [normal(de)], "respondidas": [normal(termino)]} (las reglas que deshiciste aqui no se vuelven a aplicar en ella, y
    lo que ya anadiste desde ella no se vuelve a proponer en ella, aunque fuera a otra lista)."""
    try:
        with open(os.path.join(backup_dir(rel), APRENDIDO_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)  # noqa: E731
    cambios = [c for c in data.get("cambios") or [] if isinstance(c, dict) and num(c.get("start"))
               and isinstance(c.get("de"), str) and isinstance(c.get("a"), str) and c["a"]
               and (c.get("pos") is None or (isinstance(c["pos"], int) and c["pos"] >= 0))
               and (c.get("texto") is None or isinstance(c["texto"], str))]
    return {"cambios": cambios, "deshechas": [x for x in data.get("deshechas") or [] if isinstance(x, str)],
            "respondidas": [x for x in data.get("respondidas") or [] if isinstance(x, str)]}


def guardar_registro(rel, reg):
    p = os.path.join(backup_dir(rel), APRENDIDO_FILE)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False)
    os.replace(tmp, p)


def _escribir_bloques(base, bloques):
    """El .srt (con sus tiempos tal cual) y el .txt de una transcripcion cambiada por aprender.py."""
    save_text(base + ".srt", aprender.srt_texto(bloques), "utf-8")
    save_text(base + ".txt", "\n".join(b["text"] for b in bloques) + "\n", "utf-8-sig")


def aplicar_aprendido(rel, st):
    """Al terminar una transcripcion (Jobs._after_transcribe, antes de los destinos, las voces y el resumen): las reglas
    que aceptaste para el proyecto de la reunion, solo en las frases que nadie ha tocado (las que siguen como las
    escribio Whisper, o las nuevas de una transcripcion que continua) y nunca las que deshiciste en esta reunion. La
    primera vez se guarda lo de Whisper (Restaurar original) y cada cambio queda apuntado para marcarlo y deshacerlo.
    Devuelve cuantos cambios ha hecho."""
    todas = aprendido_de(st)["reglas"]
    if not todas:
        return 0
    with App._tx_save_lock:  # no a la vez que una correccion a mano o un «Deshacer» de esta u otra reunion
        return _aplicar_aprendido(rel, todas)


def _aplicar_aprendido(rel, todas):
    abs_path = safe(rel, "file")
    proyecto = proyecto_de_reunion(abs_path)
    # las de su proyecto y que sigan siendo seguras (una escrita a mano en settings.json o de una version anterior,
    # como «plan» → «Plan», no se aplica)
    reglas = [r for r in todas if mismo_proyecto(r["proyecto"], proyecto) and aprender.regla_valida(r["de"], r["a"])]
    base = os.path.splitext(abs_path)[0]
    if not reglas or not os.path.isfile(base + ".srt"):
        return 0
    bloques = aprender.srt_bloques(read_text(base + ".srt"))
    orig = original_de(rel)
    antes = {aprender.clave(s["start"]): s["text"] for s in orig or [] if s.get("start") is not None}
    reg = registro_aprendido(rel)
    deshechas, nuevos = set(reg["deshechas"]), []
    repes = aprender.repetidos(bloques)  # dos frases con el mismo inicio: no se sabria marcar ni deshacer cada una
    for b in bloques:
        o = antes.get(b["start"])
        if (o is not None and o != b["text"]) or b["start"] in repes:
            continue  # una frase que has corregido tu (o que ya cambio Tenca): no se toca
        texto, hechos = aprender.aplicar(b["text"], reglas, deshechas)
        if hechos:
            b["text"] = texto
            nuevos += [dict(h, start=b["start"], texto=texto) for h in hechos]
    if not nuevos:
        return 0
    copia_original(rel, base, auto=True)
    reg["cambios"] += nuevos
    guardar_registro(rel, reg)  # antes que el texto: si esto falla, no queda ningun cambio sin su marca
    _escribir_bloques(base, bloques)
    n = len(nuevos)
    log(f"✦ {os.path.basename(abs_path)}: {n} cambio{'s' if n != 1 else ''} con lo aprendido de tus correcciones")
    return n


def _por_inicio(cambios):
    out = {}
    for c in cambios:
        out.setdefault(aprender.clave(c["start"]), []).append(c)
    return out


def aprendido_publico(rel, segments):
    """Para la transcripcion en la interfaz: lo que cambio Tenca, {"marcas": [{"start", "pos", "len", "de", "a"}] (las
    que aun se encuentran en el texto) y "reglas": [{"de", "a", "veces"}] (para «Deshacer»)}, o None si nada."""
    reg = registro_aprendido(rel)
    if not reg["cambios"]:
        return None
    textos = {aprender.clave(s["start"]): s["text"] for s in segments if s.get("start") is not None}
    orig = {aprender.clave(s["start"]): s["text"] for s in original_de(rel) or [] if s.get("start") is not None}
    marcas, reglas = [], {}
    for start, es in _por_inicio(reg["cambios"]).items():
        texto = textos.get(start)
        pos = aprender.posiciones(texto, es, orig.get(start)) if texto is not None else [None] * len(es)
        for e, p in zip(es, pos):
            if p is None:
                continue
            # en unidades UTF-16, como cuenta JavaScript (un emoji delante movia el subrayado)
            marcas.append({"start": e["start"], "pos": _utf16(texto[:p]), "len": _utf16(e["a"]), "de": e["de"],
                           "a": e["a"]})
            r = reglas.setdefault((aprender.normal(e["de"]), e["a"]), {"formas": Counter(), "a": e["a"], "veces": 0})
            r["formas"][e["de"]] += 1
            r["veces"] += 1
    if not marcas:
        return None
    return {"marcas": marcas, "reglas": [{"de": r["formas"].most_common(1)[0][0], "a": r["a"], "veces": r["veces"]}
                                         for r in reglas.values()]}


def _utf16(s):
    return len(s.encode("utf-16-le")) // 2


def deshacer_aprendido(rel, de, a):
    """«Deshacer» de una regla en una reunion: vuelve a poner lo que escribio Whisper donde Tenca puso `a` en lugar de
    `de` (las frases que has cambiado despues y ya no se sabe donde esta, no) y no la vuelve a aplicar en esta
    reunion. Devuelve (deshechos, no deshechos)."""
    abs_path = safe(rel, "file")
    base = os.path.splitext(abs_path)[0]
    reg = registro_aprendido(rel)
    k = aprender.normal(de)

    def quitar(e):
        return aprender.normal(e["de"]) == k and e["a"] == a
    if not k or not any(quitar(e) for e in reg["cambios"]):
        raise ApiError("Ese cambio ya no está en esta transcripción.")
    if not os.path.isfile(base + ".srt"):
        raise ApiError("No se encuentra la transcripción (.srt) de esta reunión.")
    bloques = aprender.srt_bloques(read_text(base + ".srt"))
    por_inicio = {b["start"]: b for b in bloques}
    orig = {aprender.clave(s["start"]): s["text"] for s in original_de(rel) or [] if s.get("start") is not None}
    hechos = saltados = 0
    quedan = []
    for start, es in _por_inicio(reg["cambios"]).items():
        b = por_inicio.get(start)
        if not any(quitar(e) for e in es):
            quedan += es
            continue
        if b is None:  # la frase ya no esta (la borraste)
            saltados += sum(1 for e in es if quitar(e))
            quedan += [e for e in es if not quitar(e)]
            continue
        texto, q, n, s = aprender.deshacer(b["text"], es, quitar, orig.get(start))
        b["text"], hechos, saltados = texto, hechos + n, saltados + s
        quedan += q
    if hechos:
        _escribir_bloques(base, bloques)
    reg["cambios"] = quedan
    if k not in reg["deshechas"]:
        reg["deshechas"].append(k)
    guardar_registro(rel, reg)
    log(f"↺ {os.path.basename(abs_path)}: {hechos} cambio{'s' if hechos != 1 else ''} aprendido"
        f"{'s' if hechos != 1 else ''} deshecho{'s' if hechos != 1 else ''}")
    return hechos, saltados


def _nombre_lista(v):
    return os.path.basename(v["_abs"])


def motivo_lista(e, v):
    """Por que no se ha podido escribir en una lista, en una frase y con que hacer."""
    nombre, carpeta = _nombre_lista(v), os.path.dirname(v["_abs"])
    if isinstance(e, FileNotFoundError) or not os.path.isdir(carpeta):
        return (f"No encuentro la carpeta de «{nombre}»: ¿sin conexión o con el disco desconectado? Vuelve a probar "
                "cuando esté, o elige otra lista.")
    if isinstance(e, PermissionError):
        if getattr(e, "winerror", None) in (32, 33):
            return f"«{nombre}» está abierta en otro programa. Ciérrala y vuelve a probar."
        return (f"No puedo escribir en «{nombre}»: es de solo lectura (quizá es una carpeta compartida). Añádelo tú en "
                "el archivo, o elige otra lista.")
    return f"No se ha podido escribir en «{nombre}» ({e.__class__.__name__}). Vuelve a probar, o elige otra lista."


def anadir_a_lista(v, termino):
    """Añade `termino` al final de la lista `v` (una de list_vocab, tambien de una carpeta conectada) sin tocarle nada
    mas (aprender.anadir_termino). Devuelve si ya estaba. ApiError con el motivo si no se puede."""
    path = v["_abs"]
    try:
        if aprender.normal(termino) in {aprender.normal(t) for t in read_vocab(path)[1]}:
            return True
        info = os.stat(path)
        if getattr(info, "st_file_attributes", 0) & CLOUD_ONLY:
            raise ApiError(f"«{_nombre_lista(v)}» solo está en OneDrive, no en este PC: ábrela una vez para que se "
                           "descargue y vuelve a probar.")
        if info.st_size > VOCAB_MAX_BYTES:
            raise ApiError(f"«{_nombre_lista(v)}» es demasiado grande para ser una lista. Elige otra.")
        if not os.access(path, os.W_OK):
            raise PermissionError(errno.EACCES, "solo lectura", path)
        with open(path, "rb") as f:
            raw = f.read()
        nuevo = aprender.anadir_termino(raw, termino)
        write_bytes(path, nuevo)
    except aprender.NoSePuede:
        raise ApiError(f"«{_nombre_lista(v)}» está guardada en un formato que no sé tocar sin estropearla. Añádelo tú "
                       "en el archivo, o elige otra lista.")
    except OSError as e:
        raise ApiError(motivo_lista(e, v))
    return False


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


TEXT_GEN = itertools.count(1)  # generaciones del texto en directo (Job.text_gen); next() es atómico con el GIL


def live_writer(job):
    """El escritor del directo (live.run lo llama tras cada trozo con todas las frases): guarda lo que dio Whisper
    (job.raw) y escribe el .txt y el .srt con las correcciones a mano. Si lo que llega cambia frases ya escritas (el
    directo rehace: cambio de idioma, «rehacer desde el principio»), sube job.text_gen y apunta desde qué segundo
    (job.rewrites), con el mismo candado con que /api/transcript lee el texto y su generación."""
    def writer(segs):
        with job.edit_lock:
            segs, old = list(segs), job.raw
            if old and segs[:len(old)] != old:  # se rehace lo ya escrito: desde la primera frase que cambia
                i = next((n for n, (x, y) in enumerate(zip(segs, old)) if x != y), min(len(segs), len(old)))
                desde = [float(v[i][0]) for v in (old, segs) if i < len(v) and v[i][0] is not None]
                job.text_gen = next(TEXT_GEN)
                job.rewrites.append((job.text_gen, min(desde) if desde else 0.0))
            job.raw = segs
            try:
                job.out.write(apply_edits(job.raw, job.edits))
            except OSError as e:  # se reintenta con el siguiente trozo
                log(f"   no se pudo escribir la transcripción todavía ({e.__class__.__name__})")
    return writer


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
        self.translate_to = "es"  # destino de la traduccion: «es» o «en» (start_live pone el de la reunion)
        # correcciones a mano durante el directo: {inicio de la frase: texto}, sobre lo que da Whisper (raw)
        self.edits, self.raw, self.out, self.edit_lock = {}, [], None, threading.Lock()
        # cada vez que el directo rehace texto ya escrito (cambio de idioma, «rehacer desde el principio») cambia
        # text_gen y se apunta desde qué segundo (rewrites): lo que se corrige sobre un texto anterior no toca lo
        # rehecho. Sale de un contador de todo el proceso: el directo siguiente de la misma reunión (pausar y volver a
        # transcribir) no repite números de este (revisión 2)
        self.text_gen, self.rewrites = next(TEXT_GEN), []

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
        job.translate_to = traduccion_destino(rel, st)  # el ultimo de esta reunion o el idioma del resumen (msg 1152)
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
        idioma = idioma_repaso(getattr(job, "idiomas_directo", None))
        if idioma is not None:
            log(f"   {job.name}: el idioma cambió en el directo; el repaso final, con "
                f"{idioma or 'detección'} (no solo el del final)")
        self._queue_final_pass(job.path, job.name, job.language, local_part=getattr(job, "local_part", True),
                               idioma_repaso=idioma)
        return True

    def _queue_final_pass(self, rel, name, language, local_part=True, idioma_repaso=None):
        """El repaso final de esa reunion a la cola: el del directo que acaba de terminar o, al arrancar, el que quedo
        apuntado (_resume_final_passes). Queda apuntado en repasos_pendientes.json hasta que termine: si se apaga
        antes, se retoma al arrancar. local_part: si su directo tuvo algun trozo en local (sin el dato, si: lo seguro).
        Va con el repaso (j.local_part) y se apunta con el. Si el repaso no acaba, el texto que queda es el del directo,
        y el resumen automatico mira CloudText (apuntado al empezar el directo): lo transcrito en local no sale del PC
        sin permiso, tampoco con un repaso retomado tras reiniciar (tests/test_integracion.py)."""
        j = Job(rel, name, "nube", language)
        j.final_pass, j.stage, j.local_part = True, "En cola: repaso final en la nube", local_part is not False
        # el idioma cambió en pleno directo (idioma_repaso): antes de entrar en la cola, que el hilo lo vea. No se
        # apunta en repasos_pendientes.json: uno retomado tras reiniciar usa el de la reunión al terminar
        j.idioma_repaso = idioma_repaso
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
        self._hold(rel)  # hasta encadenar voces y resumen (lo llama tambien un hilo suelto: cancelar, arrancar)
        try:
            try:
                self._pending().set(rel, False)
            except OSError:
                log_exc()
            if keep_live_text:
                log("   se mantiene el texto del directo")
                self._after_transcribe(rel)
        finally:
            self._release(rel)

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
        lee en el siguiente trozo (vocabulario y contexto libre); el idioma, al momento (cancela el trozo en vuelo y
        rehace lo ultimo). Devuelve, para el aviso de la pagina, lo que va a pasar con el idioma en ese directo
        (live.lang_switch_info) o None si no hay directo."""
        with self.lock:
            jobs = [j for j in self.list if j.path == rel and j.live and j.status == "running" and j.opts]
        info = None
        for j in jobs:
            antes = j.opts.get("language") or None
            j.opts.update(meeting_setup(safe(rel), self.settings.get()))
            log(f"   directo {j.name}: vocabulario, idioma y contexto actualizados")
            if info is None:
                with getattr(j, "edit_lock", contextlib.nullcontext()):
                    escrito, edits = list(getattr(j, "raw", None) or []), dict(getattr(j, "edits", None) or {})
                info = live.lang_switch_info(getattr(j, "directo", None), escrito, edits,
                                             j.opts.get("language") or None, antes)
        return info

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

        writer = live_writer(job)

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
                or "El resumen necesita una API key de la nube (Ajustes ⚙ → Procesado).")

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
        lleve los nombres; si no, el resumen directamente. Antes, el documento en los destinos de salida y, antes de
        todo, lo que aprendiste a corregir (aplicar_aprendido): asi el resumen y los documentos ya lo llevan bien."""
        self._aprendido(rel)
        self.write_outputs(rel)
        if not self.auto_voices(rel):
            self.auto_summary(rel)

    def _aprendido(self, rel):
        """aplicar_aprendido, nunca a costa de lo demas: si falla, la reunion sigue con sus destinos y su resumen."""
        try:
            aplicar_aprendido(rel, self.settings.get())
        except Exception:
            log_exc()

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
                              "--threads", str(voices.hilos())], stdout=subprocess.PIPE, stderr=err, cwd=APP_DIR,
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
            borrar_pcm_voces(p.pid)

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

    def _participant_hint(self, abs_path, old):
        """Cuantas personas nombra «Participantes» del .md: pista para no partir a una persona en varias
        (voices.consolidate). Solo si los sugirio Teams/Outlook o si aun no se habia identificado nunca: despues,
        _suggest_participants pudo rellenarlo solo con las voces reconocidas, y seria un tope demasiado bajo."""
        md = os.path.splitext(abs_path)[0] + ".md"
        try:
            props = parse_notes(read_text(md))[0] if os.path.isfile(md) else {}
        except (OSError, ValueError):
            return 0
        if old.get("tramos") and props.get("participantes_origen") not in PARTICIPANTES_ORIGENES:  # la grabadora
            # deja un sidecar con solo «micro»: eso aun no es una identificacion
            return 0
        texto = props.get("participantes")  # exacto solo si es el texto que escribio la app (no basta el origen: el
        # .md puede editarse fuera y conservar la nota «sugeridos por…»)
        return voices.count_participants(texto, sugeridos=" ".join(str(texto or "").split()) in TEXTOS_SUGERIDOS)

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
            n = self._participant_hint(abs_path, old)
            res = self._voices_subprocess(job, ["--diarize", abs_path, "--out", side,
                                                *(["--participantes", str(n)] if n else [])])
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
            if job.final_pass and getattr(job, "idioma_repaso", None) is not None:  # cambió en pleno directo
                job.language = job.idioma_repaso or None
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
                self._aprendido(job.path)  # como en _after_transcribe, antes de soltar la reunion (_final_pass_over)
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
    "   {k}: el proyecto de la raíz se llamaba «{t}»; pasa a «{t}», el nombre de su carpeta",  # migrate_app_name
    "   carpeta principal de {t}⟦ (workstream)⟧: {t}",
    "↺ {t}: transcripción original restaurada",
    "✦ {t}: {n} cambio⟦s⟧ con lo aprendido de tus correcciones",
    "↺ {t}: {n} cambio⟦s⟧ aprendido⟦s⟧ deshecho⟦s⟧",
    "✦ aprendido: {t} en {t} ({n} regla⟦s⟧)",
    "✦ regla olvidada: {t}",
    "✦ diccionario: {t}",
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
    # calendario (fix-calendario): estados, pasos de «Probar» y entorno, sin asuntos ni nombres (la genérica, la
    # última de las suyas: si no, se come las de «probar»)
    "   calendario: probar · {s} · {s} ({n} ms)",
    "   calendario: entorno · Python {n} bits · Office {s} · directiva {s} · fecha {s} · horas {s} · asistentes {s}",
    "   calendario: asistentes {s}",
    "   calendario: activado desde Ajustes",
    "   calendario: apagado desde Ajustes",
    "   calendario: el formato de fecha {s} no le vale a Outlook ({e})",
    "   calendario: con el formato de fecha {s}, Outlook da {n} citas fuera de la hora pedida",
    "   calendario: agenda · {n} citas⟦ (cortada)⟧ ({n} ms)",
    "   hoy: reunión preparada ({n} cosas)",
    "   hoy: reunión sin preparar ({n} cosas)",
    "   calendario: agenda · {s} ({n} ms)",
    "   calendario: {s} ({n} ms)",
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
    "arrancar con Windows: sigue activado, ahora como «{t}»",  # la copia con el nombre anterior de la app
    "arrancar con Windows: quitado el acceso directo antiguo «{t}»",
    "arrancar con Windows: desactivado en Aplicaciones de inicio, como lo tenía «{t}»",  # _startup_aprobado_copiar
    "arrancar con Windows: activado en Aplicaciones de inicio, como lo tenía «{t}»",
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
    "s": lambda v: all(re.fullmatch(r"[a-z][a-z0-9_]{1,40}", x) for x in v.split(", ")),  # códigos (calendario.py)
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


# Lo que rec_start espera a que la grabadora cree el WAV con su cabecera antes de escribir la del .md y arrancar el
# directo. Eran 3 s: con el PC cargado la grabadora tardo mas (medido en la revision de la integracion del 08/11/2026)
# y la reunion se quedaba sin cabecera ni directo («No existe el archivo»). Se sale en cuanto existe.
REC_ARRANQUE_S = 10.0


class App:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        global COMPLETE
        COMPLETE = Complete(os.path.join(DATA_DIR, "completas.json"))
        self.settings = Store(os.path.join(DATA_DIR, "settings.json"), DEFAULT_SETTINGS)
        if self.settings.get().get("procesado") not in PROCESADOS:  # ajustes de antes: se decide una vez y se guarda
            self.settings.update(lambda d: d.update(procesado=procesado_de(d)))
        self._aviso_resumen_local()
        self._load_links()
        if not self.settings.get().get("carpetas_v"):  # la carpeta por reunion (07/11/2026): una vez
            cambios = []
            self.settings.update(lambda d: cambios.extend(migrate_main_folders(d)))
            for k, old, new in cambios:
                log(f"   carpeta principal de {k}: era la de una reunión ({old}); pasa a {new or 'la raíz'}")
        self._migrar_nombre_raiz()
        self._load_vocab_links()
        self.library = Library(Durations(os.path.join(DATA_DIR, "durations.json")))
        self.transcripts = Transcripts()
        self.jobs = Jobs(self.settings, self.library)
        # cambiar la carpeta en pleno directo (msg 1198): se mueve cuando la reunion queda libre (al acabar su ultimo
        # trabajo o en la vuelta de 3 s, que tambien hace los que quedaron de antes de un cierre)
        self.jobs.on_idle, self.jobs.on_tick = self._al_quedar_libre, self._tick_movimientos
        # grabadora propia: una grabacion y, mientras la ventana de "Grabar" esta abierta, un medidor
        self.rec, self.monitor, self.monitor_seen = None, None, 0.0
        self.rec_lock, self.devices_cache = threading.Lock(), (0.0, None)
        self.toggle_lock = threading.Lock()
        self.notes_lock, self.meeting, self.meeting_prev = threading.Lock(), None, None
        self.voices_dl = None  # descarga de los modelos de voces en curso (progreso para la interfaz)
        self.vigia = calendario.Vigia()  # reunión en curso (Teams/Outlook): sugerencias y aviso flotante
        # lo que ya se sabe de los asistentes de Outlook (si pregunta, no se le vuelve a preguntar solo al arrancar)
        calendario.ASISTENTES["estado"] = self.settings.get().get("outlook_asistentes") or None
        calendario.al_cambiar = lambda v: self.settings.update(lambda d: d.update(outlook_asistentes=v))
        # la agenda de Outlook de un mes para Hoy (B7), en segundo plano y en memoria; con CHULETA_PRUEBAS_OUTLOOK
        # (pruebas de la interfaz) sale de un fichero de citas en vez de Outlook
        self.agenda = calendario.Agenda()
        prueba = os.environ.get("CHULETA_PRUEBAS_OUTLOOK")
        self.agenda_sesion = calendario.sesion_de_prueba(prueba) if prueba else None
        threading.Thread(target=self._agenda_watch, daemon=True, name="agenda-outlook").start()
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
                # el mismo plan que enseña la tarjeta de Grabar (plan.py): la reunión en curso (Teams/Outlook, sin
                # esperar a COM: calendario.py) o la que vio el aviso flotante al abrirse, y de ahí proyecto,
                # carpeta, idioma, vocabulario, motor y dispositivos, cada uno de su capa
                sug = self._reunion_ahora(st)[0]
                if b.get("name") or b.get("vocab"):
                    sug = dict(sug, name=b.get("name") or sug.get("name") or "",
                               vocab=b.get("vocab") or sug.get("vocab") or "")
                p = self.plan_grabar({}, st=st, sug=sug)
                body = dict(p["rec"], resumen=p["resumen"])
                if b.get("participantes"):
                    body.update(participantes=b["participantes"], sugerido=b.get("sugerido") or sug.get("source") or "")
                cue("start")  # antes de empezar: asi el pitido no queda grabado
                r = self.rec_start(body)
                # lo que avisa el plan (la carpeta, un proyecto sin carpeta propia, la API key), también aquí y
                # delante: el aviso de Windows corta lo que no cabe
                del_plan = "".join(f"{a} · " for a in dict.fromkeys(x["aviso"] for x in p["lineas"] if x.get("aviso")))
                # con proyecto, el resumen dice el proyecto y no la carpeta: la de la reunión, también (sin tarjeta,
                # es lo único que dice dónde se guarda; revisión de la fusión de ux-grabar), escrita como en la
                # tarjeta y al final: repite el nombre de la reunión y, si el aviso no cabe, es lo que se corta
                donde = (f" · se guarda en {ruta_txt(r['carpeta'])}"
                         if p["proyecto"].get("vocab") and r.get("carpeta") is not None else "")
                notify(f"● Grabando en {APP_NAME}", del_plan + p["resumen"] + " · Ctrl+Alt+R para parar"
                       + (f" · {r['aviso']}" if r.get("aviso") else "") + donde)
                return dict(r, recording=True, plan=p)
            except ApiError as e:
                cue("error")
                log(f"   no se pudo grabar (Ctrl+Alt+R): {e.msg}")  # el toast se va; el log se queda
                notify("No se pudo grabar", e.msg)
                raise

    def _migrar_nombre_raiz(self):
        """Al arrancar, despues de cargar las vinculadas: los ajustes del proyecto de la raiz pasan al nombre de su
        carpeta (migrate_app_name), una vez por nombre. Devuelve [(clave, nombre anterior)]."""
        renombrados, raiz = [], root_name()
        if self.settings.get().get("nombre_v") != raiz:
            self.settings.update(lambda d: renombrados.extend(migrate_app_name(d, raiz)))
        for k, viejo in renombrados:
            log(f"   {k}: el proyecto de la raíz se llamaba «{viejo}»; pasa a «{raiz}», el nombre de su carpeta")
        return renombrados

    # --- plan de grabacion (plan.py): un solo calculo para la tarjeta, Ctrl+Alt+R, el aviso flotante e Inicio
    def _motores(self, st):
        mode = procesado_de(st)
        cls = agente_de(st)[0]
        return {"presets": {p["id"]: p for p in engines.PRESETS if not (p["engine"] == "cloud" and mode != "api")},
                "procesado": mode, "agente": "Claude" if cls.tipo == "claude" else cls.label,
                "proveedor": (engines.PROVIDERS.get((st.get("cloud") or {}).get("provider") or "groq") or {}).get(
                    "label") or "la nube",
                "resumen_auto": st.get("summary_auto", True) is not False,
                "resumen_local": bool(st.get("summary_auto_local")),
                "sin_clave": not ((st.get("cloud") or {}).get("api_key") or "").strip()}

    # orígenes de la regla de carpetas (rama `carpetas`, rec_folder_origin) -> (origen, texto) de la tarjeta
    _ORIGENES_REGLA = {"workstream": "workstream", "proyecto": "proyecto", "carpeta_proyecto": "proyecto",
                       "perfil": "ajustes", "ultimo": "ultima", "raiz": "ajustes"}

    def _carpeta_origen(self, st, full):
        """(origen, texto) de la carpeta que ha decidido rec_folder_for. Con la regla de la rama de carpetas
        (rec_folder_origin(v, st) -> (carpeta, origen), de módulo), manda la suya si da esa misma carpeta; si no (o
        sin ella), se reconoce comparando con lo que hay en los ajustes."""
        regla = globals().get("rec_folder_origin")

        def texto(origen, v, rel):
            if origen == "workstream":
                return "carpeta del workstream"
            if origen == "proyecto":
                return f"carpeta de {v['project']}"
            if origen == "ultima":
                return "la última usada"
            return f"la carpeta de {APP_NAME}" if not rel else "Ajustes › Al grabar"

        def origen(v, rel):
            rel = rel or ""
            if callable(regla):
                try:
                    r_rel, o = regla(full.get(v["id"]) if v else None, st, full.values())
                except Exception:
                    log_exc()
                else:
                    o = self._ORIGENES_REGLA.get(o, "ajustes")
                    if (r_rel or "") == rel and (v or o not in ("workstream", "proyecto")):
                        return o, texto(o, v, rel)
            if v:
                wf = (st.get("workstream_folders") or {}).get(v["id"])
                if wf is not None and wf == rel:
                    return "workstream", texto("workstream", v, rel)
                pf = (st.get("project_folders") or {}).get(v["project"])
                if (pf is not None and pf == rel) or (v["id"] in full and rel == rel_of(_project_dir(full[v["id"]]))):
                    return "proyecto", texto("proyecto", v, rel)
            fija = perfil_folder(st)
            if fija is not None and fija == rel:
                return "ajustes", texto("ajustes", v, rel)
            if rel and rel == (st.get("rec_folder") or ""):
                return "ultima", texto("ultima", v, rel)
            return "ajustes", texto("ajustes", v, rel)
        return origen

    def _carpeta_plan(self, v, st, full):
        """La carpeta base de una grabación nueva: la que decide rec_folder_for. Con proyecto, si esa no es suya (la
        fija de Ajustes, que es la de las reuniones sin proyecto, o la última usada, que puede ser la de otro cliente),
        la del proyecto: la del workstream, la asignada al proyecto o, sin asignar, la carpeta del proyecto en el árbol
        (la que contiene su Vocabulario; 11a′ del rediseño, msg 1158). Con la regla de la rama de carpetas
        (rec_folder_origin), su rec_folder_for ya lo decide así y esto no hace nada: una sola regla."""
        fv = full.get(v["id"]) if v else None
        # con las listas ya leídas (full): si no, cada lista de una carpeta conectada las volvía a leer todas
        # (project_home), y Ajustes › Proyectos las leía una vez por lista (revisión de la fusión de ux-grabar)
        rel = self.rec_folder_for(fv, st, full.values())
        if not fv or callable(globals().get("rec_folder_origin")):
            return rel
        # el mismo orden que la regla de la rama de carpetas: la del workstream, la asignada al proyecto, la del
        # proyecto en el árbol; sin ninguna, la de rec_folder_for (la fija de Ajustes o la última usada)
        for cand in ((st.get("workstream_folders") or {}).get(v["id"]),
                     (st.get("project_folders") or {}).get(v["project"]), rel_of(_project_dir(fv))):
            if cand is None:
                continue
            try:
                if not in_app_dir(safe(cand, "dir")):
                    return cand
            except ApiError:
                continue
        return rel  # sin carpeta propia en la app (p. ej. listas de una carpeta conectada): lo de siempre, a la vista

    def plan_grabar(self, pedido, st=None, sug=None, plantilla=False, cuando=None):
        """El plan de una grabación nueva con el origen de cada valor (ver plan.py). `pedido`: lo elegido en la
        tarjeta; `sug`: la reunión en curso (si no se da, la última lectura del vigilante o Teams al momento).
        `cuando`: la fecha de la reunión (segundos) para su carpeta «AAAA-MM-DD Nombre»; por defecto, ahora (al subir
        un audio, la del audio)."""
        st = st or self.settings.get()
        if pedido.get("subir"):
            sug = {}  # un audio que ya existe: ni Teams ni Outlook
        if sug is None:
            sug = self._reunion_ahora(st)[0]
        if not pedido.get("subir") and not plantilla:  # lo preparado en Hoy para esta reunión, si es ahora
            prep = self._preparada(pedido["nombre"] if "nombre" in pedido else sug.get("name"), st)
            if prep:
                pedido = grabar_plan.con_preparada(pedido, prep)
        full, listas = {}, []
        for v in list_vocab():
            try:
                terms = read_vocab(v["_abs"])[1]
            except OSError:
                terms = None
            full[v["id"]] = v
            listas.append({"id": v["id"], "project": v["project"], "name": v["name"],
                           "n": len(terms) if terms is not None else None, "terms": terms or []})
        lib = getattr(self, "library", None)
        if lib is not None and hasattr(lib, "refresh") and getattr(lib, "tree", None) is None:
            try:
                lib.refresh(max_age=60)  # Ctrl+Alt+R nada más arrancar: el historial, aunque no se haya abierto la app
            except Exception:
                log_exc()
        hist = grabar_plan.historial_de(list(getattr(lib, "flat", None) or []), listas)
        p = grabar_plan.resolver(
            st, listas, sug, hist, pedido,
            carpeta_de=lambda v: self._carpeta_plan(v, st, full),
            carpeta_origen=self._carpeta_origen(st, full),
            sugerir_titulo=lambda nombre: calendario.suggest({"subject": nombre}, listas),
            motores=self._motores(st), links={k: l["name"] for k, l in LINKS.items()},
            carpeta_reunion=self._carpeta_reunion(st, plantilla, cuando), raiz=root_name())
        # la carpeta de Ajustes (la de las reuniones sin proyecto) guardada que ahora no se encuentra (una vinculada
        # desconectada): se graba en otra y se dice por qué en su línea (revisión 3 de B2)
        fija = perfil_de(st)["folder"]
        car = next((x for x in p["lineas"] if x["clave"] == "carpeta"), None)
        if fija is not None and perfil_folder(st) is None and car and car["origen"] in ("ajustes", "ultima") \
                and not car.get("aviso"):
            ruta = ruta_txt(fija)
            car["aviso"] = f"No se encuentra la carpeta de las reuniones sin proyecto ({ruta}): se guarda aquí."
        # un proyecto sin carpeta propia (sus listas vienen de una carpeta conectada y no se le ha asignado ninguna)
        # graba en la de Ajustes o en la última usada, que puede ser la de otro cliente: se dice cómo fijarle una
        proy = p["proyecto"].get("proyecto")
        if proy and car and car["origen"] in ("ajustes", "ultima") and not car.get("aviso"):
            car["aviso"] = (f"{proy} no tiene carpeta propia: asígnale una en Ajustes › Proyectos o, al grabar, con "
                            f"«Cambiar» y «Todas las de {proy}».")
        # un nombre escrito con \ / : * ? " < > | (p. ej. «Comité: Q4») no vale como nombre de archivo y rec_start lo
        # rechazaría al empezar: se graba sin esos signos, como la carpeta de la reunión (rec_destino), y se dice
        n = p["rec"].get("name") or ""
        if n:
            try:
                clean_name(n)
            except ApiError:
                p["rec"]["name"] = p["nombre"]["archivo"] = calendario.nombre_archivo(n)
        return p

    PLANTILLA_REUNION = "AAAA-MM-DD Nombre de la reunión"

    @classmethod
    def _carpeta_reunion(cls, st, plantilla=False, cuando=None):
        """La carpeta «AAAA-MM-DD Nombre» que creará rec_start dentro de la base, o None. Con la rama de carpetas
        (B1b): su meeting_dir_preview, con el nombre como lo limpia rec_start (rec_destino hace lo mismo). Sin ella,
        None: la grabación va en la base. plantilla (Ajustes › Al grabar, sin reunión): sin nombre, el patrón y no
        «AAAA-MM-DD HH-MM» de ahora, que cambiaría cada minuto."""
        previa = globals().get("meeting_dir_preview")
        if not callable(previa):
            return None

        def reunion(base, nombre):
            if plantilla and not nombre:
                try:
                    activa = previa(base or "", "", st) != (base or "")
                except (ApiError, OSError):
                    return None
                return f"{base}/{cls.PLANTILLA_REUNION}".lstrip("/") if activa else None
            label = ""
            if nombre:
                try:
                    label = clean_name(nombre)
                except ApiError:
                    label = calendario.nombre_archivo(nombre)
            try:
                sub = previa(base or "", label, st, **({} if cuando is None else {"when": cuando}))
            except (ApiError, OSError):
                return None
            return sub if sub and sub != (base or "") else None
        return reunion

    def _carpetas_de_proyectos(self, st):
        """Ajustes › Proyectos: la carpeta principal de cada proyecto y de cada workstream con su origen, con el mismo
        cálculo que el plan (lo que dice la tabla es lo que se usará al grabar). `sin_asignar`: la que tendría el
        proyecto si se le quita la suya (para la opción «Sin asignar» al editarla)."""
        full = {v["id"]: v for v in list_vocab()}

        def una(v, s):
            rel = self._carpeta_plan(v, s, full)
            o, t = self._carpeta_origen(s, full)(v, rel)
            return {"rel": rel or "", "ruta": ruta_txt(rel), "origen": o, "texto": t}
        sin_ws = dict(st, workstream_folders={})
        proyectos, workstreams = {}, {}
        for v in full.values():
            workstreams[v["id"]] = una(v, st)
            if v["project"] not in proyectos:
                pf = {k: x for k, x in (st.get("project_folders") or {}).items() if k != v["project"]}
                proyectos[v["project"]] = dict(una(v, sin_ws), sin_asignar=una(v, dict(sin_ws, project_folders=pf)))
        return {"proyectos": proyectos, "workstreams": workstreams}

    def grabar_plan_api(self, b):
        """POST /api/grabar/plan {nombre?, vocab?, participantes?, cambios?, recientes?}: lo que se va a usar al grabar,
        línea a línea con su origen, y el cuerpo exacto de /api/rec/start (rec). Con recientes, también las reuniones
        recientes para el autocompletado del nombre."""
        pedido = {k: b[k] for k in ("nombre", "vocab", "participantes") if k in b}
        if isinstance(b.get("cambios"), dict):
            pedido["cambios"] = b["cambios"]
        cuando = None
        if b.get("subir"):  # «Subir audio»: el plan de subir uno que ya existe (ver plan.resolver)
            pedido.update(subir=True, archivo=str(b.get("archivo") or ""), varios=bool(b.get("varios")))
            cuando = fecha_subida(b.get("fecha"))
        st = self.settings.get()
        if isinstance(b.get("ajustes"), dict):  # Ajustes › Al grabar: cómo quedaría con lo que aún no se ha guardado
            st = ajustes_previstos(st, b["ajustes"])
        p = self.plan_grabar(pedido, st=st, plantilla=isinstance(b.get("ajustes"), dict), cuando=cuando)
        if b.get("recientes"):
            vs = list_vocab()
            p["recientes"] = grabar_plan.recientes(grabar_plan.historial_de(
                list(getattr(getattr(self, "library", None), "flat", None) or []), vs), {v["id"]: v for v in vs})
        if b.get("proyectos"):  # Ajustes › Proyectos: la carpeta de cada uno, con lo que aún no se ha guardado
            p["carpetas"] = self._carpetas_de_proyectos(st)
        return p

    def grabar_recordar(self, b):
        """POST /api/grabar/recordar {clave, valor, alcance, vocab?}: «Cambiar» de la tarjeta (o de Ajustes) con «Usarlo
        en» las del proyecto, las del workstream o todas (Ajustes). «Solo esta reunión» no guarda nada: va en el
        pedido del plan. valor None en el proyecto o el workstream = quitarlo (vuelve a heredar)."""
        clave, alcance, valor = b.get("clave"), b.get("alcance"), b.get("valor")
        v = find_vocab(None, None, b.get("vocab")) if b.get("vocab") else None
        if alcance not in ("proyecto", "workstream", "ajustes"):
            raise ApiError("¿Para qué reuniones? Elige las del proyecto o todas.")
        if alcance != "ajustes" and not v:
            raise ApiError("Elige primero el proyecto de la reunión.")
        sitio = {"proyecto": "project", "workstream": "workstream"}.get(alcance)
        if clave == "idioma":
            lang = None if valor is None else str(valor or "").strip().lower()
            if lang and not IDIOMA_RE.match(lang):
                raise ApiError("Idioma no válido: " + lang)
            lang = "" if lang == "auto" else lang
            if alcance == "ajustes":
                cambios = {"language": lang or ""}
            else:
                k = v["id"] if sitio == "workstream" else v["project"]
                cambios = {sitio + "_langs": lambda cur: _con_o_sin(cur, k, lang)}
        elif clave == "carpeta":
            rel = None if valor is None else str(valor or "").replace("\\", "/").strip("/")
            if rel is not None and in_app_dir(safe(rel, "dir")):
                raise ApiError("No se puede grabar ahí")
            if alcance == "ajustes":
                cambios = {"perfil": {"folder": rel}}
            elif alcance == "workstream":  # Ajustes › Proyectos: la de un workstream (None: la del proyecto)
                cambios = {"workstream_folders": lambda cur: _con_o_sin(cur, v["id"], rel)}
            else:
                cambios = {"project_folders": lambda cur: _con_o_sin(cur, v["project"], rel)}
        elif clave in ("mic", "sys", "live", "preset", "contexto") and alcance == "ajustes":
            if clave == "preset" and valor not in LIVE_PRESETS:
                raise ApiError("Ese motor no existe.")
            otro = self.settings.get().get("rec_sys" if clave == "mic" else "rec_mic")
            if clave in ("mic", "sys") and valor == "none" and otro == "none":
                raise ApiError(SIN_AUDIO)
            if clave == "contexto":
                cambios = {"perfil": check_perfil({"context": valor})}
            elif clave == "live":
                cambios = {"rec_directo": bool(valor)}
            else:
                key = {"mic": "rec_mic", "sys": "rec_sys", "preset": "live_preset"}[clave]
                cambios = {key: str(valor or "")[:300] or ("all" if clave == "sys" else "default")}
        else:
            raise ApiError("Eso no se puede guardar así.")

        def upd(d):
            for k, x in cambios.items():
                if k == "perfil":
                    update_perfil(d, x)
                else:  # los diccionarios por proyecto, sobre lo guardado en ese momento (dentro del cerrojo)
                    d[k] = x(d.get(k)) if callable(x) else x
        self.settings.update(upd)
        log(f"   al grabar: {clave} para " + ("todas (Ajustes)" if alcance == "ajustes" else alcance))
        return {"ok": True}

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
            out.update(path=rel_of(r.path), started=r.started, resumen=getattr(self, "rec_resumen", ""), **r.status)
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
                sug = self._reunion_ahora()[0]
                b = dict(b, name=sug.get("name") or "", vocab=sug.get("vocab") or "",
                         participantes=b.get("participantes") or calendario.join_participants(sug.get("participants")),
                         sugerido=b.get("sugerido") or sug.get("source") or "")
            folder = safe(b.get("folder", ""), "dir")  # la carpeta principal (rec_folder_for)
            if in_app_dir(folder):
                raise ApiError("No se puede grabar ahí")
            try:  # la que da la regla con su tema: si es esta, la carpeta no se eligio a mano (_tema_movido)
                vb = find_vocab(None, None, b["vocab"]) if b.get("vocab") else None
                por_regla = _norm_dir(safe(rec_folder_origin(vb, self.settings.get())[0], "dir")) == _norm_dir(folder)
            except (ApiError, OSError):
                por_regla = False
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
            if por_regla:
                self.__dict__.setdefault("_por_regla", set()).add(rel_of(path))
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
                fin = time.monotonic() + REC_ARRANQUE_S
                while time.monotonic() < fin:  # espera a que exista el archivo con su cabecera
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
                    completo = " ".join(str(b["participantes"]).split())
                    props["participantes"] = completo[:500]
                    # sin confirmar: ni el resumen en la nube ni los destinos de salida los llevan hasta que el
                    # usuario los confirme o los edite en la pagina de la reunion (participantes_confirmados)
                    origen = str(b.get("sugerido") or "").strip().lower()
                    if origen in PARTICIPANTES_ORIGENES:
                        props["participantes_origen"] = origen
                        # para la pista de quien habla: solo si es, entero, el texto que la app escribe con una
                        # sugerencia reciente (dialogo, Ctrl+Alt+R, aviso); lo que traiga otro programa, o cortado, no
                        vigia = getattr(self, "vigia", None)
                        ult = (getattr(vigia, "ultimo", None) or {}).get("sugerencia") or {}
                        recientes = getattr(vigia, "participantes_recientes", None)
                        listas = [ult.get("participants")] + (recientes() if callable(recientes) else [])
                        if len(completo) <= 500 and any(
                                completo == " ".join(calendario.join_participants(p).split()) for p in listas if p):
                            if len(TEXTOS_SUGERIDOS) > 500:
                                TEXTOS_SUGERIDOS.clear()
                            TEXTOS_SUGERIDOS.add(completo)
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

                # fuera de upd, que corre con el candado de los ajustes: project_home de una lista conectada lee las
                # carpetas de listas (OneDrive, la red) y mientras tanto nadie podria leer los ajustes
                try:  # con la grabadora ya en marcha nada de esto la para (02/11/2026): un fallo, al log y aviso
                    sin_casa = bool(v) and not por_regla and project_home(v) is None
                except Exception as e:
                    log_exc()
                    sin_casa = False
                    avisos.append(f"sin mirar la carpeta del proyecto ({e})")

                def upd(d):
                    if "vocab" in b:
                        d["rec_vocab"] = v["id"] if v else ""
                    # la proxima vez (y con Ctrl+Alt+R) se graba en la misma carpeta. Desde el plan, solo la de una
                    # reunion sin proyecto y si no se eligio «solo esta reunion»: la carpeta de un proyecto no pasa a
                    # ser «la ultima usada» de las reuniones sin proyecto (revision de B1)
                    if not b.get("plan") or (not v and b.get("recordar_carpeta", True)):
                        d["rec_folder"] = rel_of(folder)
                    # si el proyecto no tiene «la suya» en el arbol (solo tiene listas de una carpeta de vocabulario
                    # conectada: project_home), la elegida a mano pasa a ser su carpeta principal (9f49590). Tambien
                    # desde el plan: una eleccion explicita manda (decision de Claudia, 09/11/2026); pero no si en la
                    # tarjeta se eligio «solo esta reunion» (recordar_carpeta), que tambien es explicita
                    if sin_casa and b.get("recordar_carpeta", True):
                        d["project_folders"] = dict(d.get("project_folders") or {}, **{v["project"]: rel_of(folder)})
                    if b.get("plan"):
                        # desde el plan (tarjeta, Ctrl+Alt+R, aviso): lo demas de esta reunion no pasa a ser el
                        # predeterminado; eso se guarda a proposito con «Usarlo en» (D1)
                        return
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
        self.rec_resumen = " ".join(str(b.get("resumen") or "").split())[:300]  # lo que dice el plan (Inicio, toasts)
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
        Explorador, o el repositorio, la pagina de PayPal («Invitar al autor a un cafe») o la de la tenca en el
        navegador."""
        que = b.get("que")
        if que == "datos":
            os.makedirs(DATA_DIR, exist_ok=True)
            open_dir(DATA_DIR)
        elif que == "repo":
            webbrowser.open(REPO_URL)
        elif que == "donar":
            webbrowser.open(DONAR_URL)
        elif que == "tenca":
            webbrowser.open(TENCA_URL)
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
        cloud["elegido"] = proveedor_elegido(st)  # sin elegir (instalacion nueva): la interfaz no preselecciona ninguno
        cloud["has_key"] = bool(cloud.get("api_key"))
        cloud["api_key"] = masked(cloud.get("api_key"))
        # claves adicionales: enmascaradas (nunca en claro) y por proveedor, cada una con el suyo; en extra_keys, las
        # del proveedor configurado (las unicas que se usan)
        by = {p: [masked(k) for k in (cloud.pop(f, None) or []) if k] for p, f in engines.EXTRA_KEYS_FIELDS.items()}
        cloud["extra_keys_by"], cloud["extra_keys"] = by, by.get(cloud.get("provider") or "groq", [])
        cloud["quota"] = (self.jobs.cloud.keys.public(st["cloud"], model=engines.CloudEngine.live_model(st["cloud"]))
                          if cloud["has_key"] and getattr(self.jobs, "cloud", None) is not None else None)
        return {"root": ROOT, "root_name": root_name(), "root_names": root_names(), "version": VERSION,
                "app_name": APP_NAME, "window": WINDOW is not None,
                "acerca": {"licencia": license_name(), "repo": REPO_URL, "datos": DATA_DIR, "donar": DONAR_URL,
                           "tenca": TENCA_URL},
                "presets": presets, "providers": engines.PROVIDERS, "cloud": cloud,
                "audio_ext": sorted(AUDIO_EXT),  # «Subir audio»: lo que se ofrece en el selector y se sube al soltar
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
                "vocab_links": self._vocab_links_public(),  # carpetas de listas conectadas (no son de grabaciones)
                # carpeta principal de cada proyecto, la asignada o la suya en el arbol (rec_folder_origin); las
                # asignadas tal cual, aparte
                "rec_folder": st.get("rec_folder") or "", "project_folders": project_folders_resolved(st),
                "project_folders_asignadas": dict(st.get("project_folders") or {}),
                "workstream_folders": dict(st.get("workstream_folders") or {}),
                "carpeta_por_reunion": por_reunion(st),
                "project_langs": st.get("project_langs") or {}, "workstream_langs": st.get("workstream_langs") or {},
                "rec_directo": st.get("rec_directo", True) is not False, "rec_mic": st.get("rec_mic") or "default",
                "rec_sys": st.get("rec_sys") or "all",
                "meeting_detect": bool(st.get("meeting_detect", True)),
                "ocultar_captura": captura_estado(st),
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
        activo = getattr(getattr(self, "jobs", None), "active_for", None)
        job = activo(rel_of(abs_path)) if activo is not None else None
        if job is not None and job.live and getattr(job, "edit_lock", None) is not None:
            # en directo, el texto y su generación a la vez (el escritor del directo los cambia con este candado): el
            # editor manda la generación al guardar y lo que se haya rehecho después no se toca (_save_live_edits)
            with job.edit_lock:
                out = public_transcript(self.transcripts.load(os.path.splitext(abs_path)[0]))
                out["text_gen"] = getattr(job, "text_gen", 0)
        else:
            out = public_transcript(self.transcripts.load(os.path.splitext(abs_path)[0]))
        out["has_backup"] = os.path.isdir(backup_dir(rel_of(abs_path)))
        if out["has_backup"] and out["kind"] == "srt":  # lo que cambio Tenca con lo que aprendio (marcas y Deshacer)
            try:
                out["aprendido"] = aprendido_publico(rel_of(abs_path), out["segments"])
            except OSError:
                log_exc()
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
        with self._move_lock():
            return {"id": self.jobs.submit_summary(alias_of(b.get("path")))}

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
        tema = bool({"proyecto", "workstream", "vocabulario"} & set(b.get("props") or {})) and not b.get("_al_grabar")
        with self.notes_lock:
            # dentro del candado: si la reunion se acaba de mover (se mueve con el cogido), su ruta nueva
            abs_path = safe(alias_of(b.get("path")), "file")
            md = os.path.splitext(abs_path)[0] + ".md"
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
        directo = None
        if {"proyecto", "workstream", "idioma", "contexto", "vocabulario"} & set(b.get("props") or {}):
            directo = self.jobs.refresh_prompt(rel_of(abs_path))  # el idioma, ya; lo demas, en el siguiente trozo
        self.jobs.schedule_outputs(rel_of(abs_path))  # destinos de salida: el documento con las notas nuevas
        self.library.at = 0
        out = {"props": with_theme_id(props, rel_of(abs_path)),
               "mtime": os.path.getmtime(md) if os.path.isfile(md) else 0}
        if directo is not None and "idioma" in (b.get("props") or {}):
            out["directo"] = {"idioma": directo}  # el aviso dice cuándo se verá el idioma nuevo (perfilSavedText)
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
        with self._move_lock():
            return self._tema_movido_locked(abs_path, antes, props)

    def _tema_movido_locked(self, abs_path, antes, props):
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
        if not auto and (_norm_dir(old_main) != cur or rel not in self.__dict__.get("_por_regla", ())):
            return None  # estaba en una carpeta elegida a mano (o no salio de la regla al grabar): no la cambia
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
            before = self._vocab_ids() if VOCAB_LINKS else {}  # una carpeta de listas conectada puede quedar dentro
            LINKS[lid] = {"name": clean_name(b.get("name") or os.path.basename(path)), "path": path}
            self._save_links()
            log(f"🔗 carpeta vinculada: {path}")
            if before:
                self._vocab_ids_moved(before)
        return {"rel": LINK + lid, "name": LINKS[lid]["name"]}

    def link_remove(self, b):
        with self._move_lock():
            return self._link_remove(b)

    def _link_remove(self, b):
        """Deja de mostrarla en la app. No borra ni mueve nada de la carpeta."""
        rel = b.get("path") or ""
        lid = rel[len(LINK):].split("/")[0] if rel.startswith(LINK) else ""
        if lid not in LINKS:
            raise ApiError("Esa carpeta no está vinculada.")
        busy = any(j["path"].startswith(LINK + lid) and j["status"] in ("queued", "running")
                   for j in self.jobs.snapshot())
        if busy or (self.rec and self.rec.alive() and rel_of(self.rec.path).startswith(LINK + lid)):
            raise ApiError("Se está grabando o transcribiendo algo de esa carpeta; espera a que termine.")
        before = self._vocab_ids() if VOCAB_LINKS else {}
        l = LINKS.pop(lid)
        self._save_links()
        if before:
            self._vocab_ids_moved(before)
        self.settings.update(lambda d: d.update(
            favorites=[f for f in d.get("favorites", []) if not f["path"].startswith(LINK + lid)],
            rec_folder="" if (d.get("rec_folder") or "").startswith(LINK + lid) else d.get("rec_folder", ""),
            project_folders={k: v for k, v in (d.get("project_folders") or {}).items()
                             if not v.startswith(LINK + lid)},
            workstream_folders={k: v for k, v in (d.get("workstream_folders") or {}).items()
                                if not v.startswith(LINK + lid) and not k.startswith(LINK + lid)}))
        log(f"🔗 carpeta desvinculada (no se ha tocado nada): {l['path']}")
        return {"ok": True}

    def rec_folder_for(self, v, st, vs=None):
        """La carpeta principal donde se graba una reunion con el tema `v` (la regla, en rec_folder_origin). Dentro,
        rec_start crea la carpeta de la reunion (carpeta por reunion); para saber cual sera, rec_destino. `vs`: las
        listas de list_vocab, si ya se tienen."""
        return rec_folder_origin(v, st, vs)[0]

    def carpeta_principal(self, b):
        """POST /api/proyecto/carpeta {project, vocab?, folder}: asigna la carpeta principal de un proyecto (o, con
        `vocab`, la de ese workstream: el id de su lista). folder None = sin asignar (vuelve a la del proyecto en el
        arbol). Devuelve las carpetas principales de cada proyecto (como /api/config)."""
        project, vid, rel = str(b.get("project") or "").strip(), str(b.get("vocab") or "").strip(), b.get("folder")
        if vid:
            v = find_vocab(None, None, vid)
            if not v:
                raise ApiError("Ese workstream ya no existe.")
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
        items, avisos = [], {}
        vs = _list_vocab(avisos)
        for v in vs:
            try:
                items.append(vocab_info(v, lang))
            except OSError:
                pass  # archivo a medio guardar o solo en OneDrive sin conexion
        return {"items": items, "last": st.get("rec_vocab") or "", "general": bool(st.get("prompt")),
                "carpetas": vocab_links_estado(vs, avisos)}  # las carpetas de listas conectadas

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
        if old and old.get("conectada") and fold(old["project"]) != fold(project):
            # sacarla de la carpeta conectada seria borrar alli el archivo del usuario (os.remove, sin papelera)
            raise ApiError(f"«{old['name']}» está en tu carpeta de listas conectada: su proyecto («{old['project']}») "
                           "sale del nombre de su carpeta y aquí no se cambia. Cámbialo en el Explorador, o crea el "
                           "tema en el otro proyecto con «Nuevo».")
        same = next((v for v in list_vocab() if fold(v["project"]) == fold(project)), None)
        if old and fold(old["project"]) == fold(project):
            # mismo proyecto: en su misma carpeta (la conectada, si es de una). Antes se iba a la del primer proyecto
            # con ese nombre, y con dos que se llaman igual la lista se mudaba de carpeta al guardarla
            same = old
        # proyecto que ya existe: en su carpeta de vocabulario; si es nuevo, <raiz>/<proyecto>/Vocabulario
        vdir = os.path.dirname(same["_abs"]) if same else os.path.join(ROOT, project, "Vocabulario")
        path = os.path.join(vdir, name + ".txt")
        moved = old and os.path.normcase(old["_abs"]) != os.path.normcase(path)
        if (not old or moved) and os.path.exists(path):
            raise ApiError(f"Ya hay un workstream «{name}» en {project}.")
        per_line = False
        if old:
            try:
                if not moved and read_vocab(old["_abs"]) == (desc, terms):
                    return {"id": old["id"]}  # sin cambios: no se reescribe (la lista puede ser un fichero suyo)
                per_line = _one_per_line(old["_abs"])
            except OSError:
                pass
        os.makedirs(vdir, exist_ok=True)
        # como estaba escrita: un termino por linea si la lista era asi (las de las carpetas conectadas suelen serlo)
        save_text(path, (f"# {desc}\n" if desc else "") + ("\n" if per_line else ", ").join(terms) + "\n", "utf-8")
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

    # --- carpetas de listas conectadas (se leen en vivo: nada se copia)
    def _load_vocab_links(self):
        VOCAB_LINKS.clear()
        for l in self.settings.get().get("vocab_links") or []:
            if isinstance(l, dict) and isinstance(l.get("path"), str) and l["path"].strip():
                VOCAB_LINKS[link_id(l["path"])] = {"name": l.get("name") or vocab_link_name(l["path"]),
                                                   "path": l["path"]}

    def _save_vocab_links(self):
        self.settings.update(lambda d: d.update(vocab_links=[dict(l) for l in list(VOCAB_LINKS.values())]))

    @staticmethod
    def _vocab_ids():
        """El id de cada lista por su ruta: para seguir a las que cambian de id al vincular o desvincular carpetas."""
        try:
            return {os.path.normcase(v["_abs"]): v["id"] for v in list_vocab()}
        except OSError:
            return {}

    def _vocab_ids_moved(self, before):
        """Listas que han cambiado de id (una carpeta conectada que queda dentro de una vinculada, o al reves): el tema
        fijo del perfil, el ultimo elegido y el de cada reunion las siguen. Si no, Ctrl+Alt+R grababa sin vocabulario
        (el id guardado ya no era el de ninguna lista)."""
        after = self._vocab_ids()
        pares = [(old, after[k]) for k, old in before.items() if k in after and after[k] != old]
        if not pares:
            return

        def upd(d):
            for old, new in pares:
                settings_moved(d, old, new)
        self.settings.update(upd)
        for old, new in pares:
            temas().moved(old, new, folder=True)  # solo cambian los valores: ninguna reunion tiene esa ruta
        log(f"   {len(pares)} listas de vocabulario con ruta interna nueva: el perfil y los temas las siguen")

    @staticmethod
    def _vocab_links_public():
        return [{"rel": VLINK + k, "name": l["name"], "path": l["path"]} for k, l in list(VOCAB_LINKS.items())]

    def vocab_link_add(self, b):
        """POST /api/vocab/link/add {path, name?}: conecta una carpeta de listas (check_vocab_link). Sus .txt se leen en
        vivo al grabar y al transcribir; nada se copia. Devuelve cuantas listas da y lo que se salta. Sin ninguna .txt
        que valga como lista (ni con aviso), no se conecta: seguramente no es la carpeta que se queria elegir."""
        path = check_vocab_link(b.get("path"), [d["path"] for d in perfil_de(self.settings.get())["outputs"]])
        vid = link_id(path)
        ya = vid in VOCAB_LINKS
        if not ya:
            name = clean_name(b.get("name") or vocab_link_name(path))
            VOCAB_LINKS[vid] = {"name": name, "path": path}
            estado = next(e for e in vocab_links_estado() if e["rel"] == VLINK + vid)
            if not estado["listas"] and not estado["avisos"]:
                VOCAB_LINKS.pop(vid, None)
                raise ApiError("En esa carpeta no hay listas: archivos .txt con los términos separados por comas o uno "
                               "por línea, sueltos o en <Proyecto>\\Vocabulario\\.")
            self._save_vocab_links()
            log(f"🔤 carpeta de listas conectada: {path} ({estado['listas']} listas)")
        estado = next(e for e in vocab_links_estado() if e["rel"] == VLINK + vid)
        return dict(estado, ya=ya, vocab_links=self._vocab_links_public())

    def vocab_link_remove(self, b):
        """POST /api/vocab/link/remove {rel}: deja de leer esa carpeta. No borra ni mueve nada de ella. El tema fijo del
        perfil y el ultimo elegido, si eran de alli, vuelven a «automatico» y a ninguno, y sus workstreams pierden la
        carpeta principal asignada (como al borrar un tema o desvincular una carpeta) y el idioma asignado."""
        rel = str(b.get("rel") or b.get("path") or "")
        vid = rel[len(VLINK):].split("/")[0] if rel.startswith(VLINK) else ""
        if vid not in VOCAB_LINKS:
            raise ApiError("Esa carpeta de listas no está conectada.")
        l = VOCAB_LINKS.pop(vid)
        self._save_vocab_links()
        mine = lambda x: isinstance(x, str) and x.startswith(VLINK + vid + "/")  # noqa: E731

        def upd(d):
            if mine(d.get("rec_vocab")):
                d["rec_vocab"] = ""
            if mine((d.get("perfil") or {}).get("vocab")):
                d["perfil"] = dict(d["perfil"], vocab=None)
            for clave in ("workstream_folders", "workstream_langs"):
                if any(mine(k) for k in (d.get(clave) or {})):
                    d[clave] = {k: x for k, x in d[clave].items() if not mine(k)}
        self.settings.update(upd)
        log(f"🔤 carpeta de listas desconectada (no se ha tocado nada): {l['path']}")
        return {"ok": True, "vocab_links": self._vocab_links_public()}

    # --- perfil de reunion
    def perfil_get(self, q):
        """GET /api/perfil: el perfil por defecto y lo que se aplicaria ahora mismo a una grabacion (con la reunion
        en curso, si la hay), y los avisos de los destinos de salida (documentos que no se han podido escribir)."""
        st = self.settings.get()
        sug = self._reunion_ahora(st)[0]
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
        actual = perfil_de(self.settings.get())
        cambios = check_perfil(b, actual["outputs"], actual)
        self.settings.update(lambda d: update_perfil(d, cambios))
        log("   perfil de reunión guardado: " + ", ".join(sorted(cambios)))
        return {"perfil": perfil_de(self.settings.get())}

    def vocab_delete(self, b):
        v = find_vocab(None, None, b.get("id"))
        if not v:
            raise ApiError("Ese workstream ya no existe.")
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
        with self._move_lock():  # no a la vez que se mueve (dejaria archivos sueltos); despues, su ruta nueva
            return self._delete(dict(b, path=alias_of(b.get("path"))))

    def _delete(self, b):
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

    def subir(self, handler, q):
        """POST /api/subir?folder&name&fecha&vocab&language&contexto&preset&archivo (09/11/2026, msg 1296: «poder subir
        un audio, independiente de donde venga»). El cuerpo es el archivo tal cual (el del móvil, un audio de WhatsApp,
        otra grabadora); lo demás es lo que da POST /api/grabar/plan con subir. Entra como una grabación hecha con la
        tarjeta: «AAAA-MM-DD HH-MM-SS - Nombre» con la fecha del audio, en su carpeta de reunión dentro de la principal,
        con el proyecto, el idioma y el contexto en su .md, y a la cola de transcripción. Antes de guardarlo se
        comprueba con la misma cadena que la cola (engines.probar_audio) que se puede transcribir: si no, no se guarda
        nada y se dice por qué."""
        original = os.path.basename(str(q.get("archivo") or "").replace("\\", "/")) or "El archivo"
        ext = os.path.splitext(original)[1].lower()
        if ext not in AUDIO_EXT:
            raise ApiError(f"«{original}» no es un audio que {APP_NAME} sepa leer. Sirven {FORMATOS_SUBIR}.")
        folder = safe(q.get("folder", ""), "dir")
        if in_app_dir(folder):
            raise ApiError("No se puede guardar ahí")
        st = self.settings.get()
        cuando = fecha_subida(q.get("fecha"))
        lt = time.localtime(cuando)
        nombre = " ".join(str(q.get("name") or "").split())[:120]
        label = ""
        if nombre:
            try:
                label = clean_name(nombre)
            except ApiError:
                label = calendario.nombre_archivo(nombre)
        stem = time.strftime("%Y-%m-%d %H-%M-%S", lt) + (f" - {label}" if label else "")
        # se recibe en un archivo oculto de la carpeta principal (el árbol no enseña los que empiezan por punto) y con
        # su extensión, para que PyAV lo reconozca al comprobarlo
        remaining = int(handler.headers.get("Content-Length") or 0)
        if remaining < 0:
            raise ApiError("Cuerpo no válido")
        limpiar_subidas(folder)
        tmp = os.path.join(folder, f"{SUBIENDO}{secrets.token_hex(4)}{ext}")
        total, guardado, mdir_nueva = remaining, False, None
        try:  # lo recibido a medias (disco lleno, la ventana se cierra a mitad) no se queda en la carpeta
            with open(tmp, "wb") as f:
                while remaining > 0:
                    chunk = handler.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            if remaining <= 0:
                handler.body_read = True
            if remaining > 0:
                raise ApiError("La subida se cortó.")
            consejo = " Si en el móvil se oye bien, vuelve a pasarlo al PC o conviértelo a mp3."
            if not total:
                raise ApiError(f"«{original}» está vacío.{consejo}")
            try:
                dur = engines.probar_audio(tmp)
            except engines.EngineError as e:
                raise ApiError(f"«{original}» {e}.{consejo}")
            if dur and dur > engines.AUDIO_MAX_S:  # la cola no lo podría decodificar entero (fix-subir-memoria)
                raise ApiError(f"«{original}» {engines.DEMASIADO_LARGO}.")
            # su carpeta «AAAA-MM-DD Nombre», como al grabar (con la fecha del audio, no la de hoy)
            mname = fit_meeting_dir(folder, label, lt, stem) if por_reunion(st) else None
            mdir = free_dir(folder, mname) if mname else None
            dest = mdir or folder
            base, n = stem, 2
            # ni otro audio ni sus notas con ese nombre (dos audios con el mismo nombre compartirían el .txt y el .md)
            while any(os.path.exists(os.path.join(dest, base + e)) for e in AUDIO_EXT | set(SIDE_EXTS)):
                base, n = f"{stem} ({n})", n + 1
            target = os.path.join(dest, base + ext)
            if mdir:
                try:
                    if not os.path.isdir(mdir):
                        os.makedirs(mdir)
                        mdir_nueva = mdir
                except OSError as e:
                    raise ApiError(f"No se puede crear la carpeta de la reunión en {display_rel(rel_of(folder))} "
                                   f"({e.__class__.__name__}).")
                carpetas_reunion().add(rel_of(mdir))
            for intento in range(20):  # Defender u OneDrive pueden tener el archivo abierto un instante (WinError 32)
                try:
                    os.replace(tmp, target)
                    break
                except PermissionError:
                    if intento == 19:
                        raise
                    time.sleep(0.1)
            guardado = True
        except OSError as e:  # sin permiso de escritura, el disco lleno… (una ApiError no es OSError: pasa tal cual)
            raise ApiError(f"No se ha podido guardar en {display_rel(rel_of(folder))}: "
                           f"{e.strerror or e.__class__.__name__}.")
        finally:
            if not guardado:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                if mdir_nueva:  # la carpeta de la reunión creada para él, si se ha quedado vacía
                    try:
                        os.rmdir(mdir_nueva)
                        carpetas_reunion().discard(rel_of(mdir_nueva))
                    except OSError:
                        pass
        try:  # la fecha del audio también como la de modificación (la que enseña el Explorador)
            os.utime(target, (cuando, cuando))
        except OSError:
            pass
        rel, avisos, props = rel_of(target), [], {}
        v = None
        if q.get("vocab"):
            try:
                v = find_vocab(None, None, q["vocab"])
            except (ApiError, OSError):
                v = None
        if v:
            props.update(proyecto=v["project"], workstream=v["name"], vocabulario=v["id"])
        if "language" in q:
            lang = str(q.get("language") or "").strip().lower()
            props["idioma"] = lang if lang and IDIOMA_RE.match(lang) else "auto"
        ctx = q.get("contexto") if "contexto" in q else perfil_de(st)["context"]
        ctx = " ".join(str(ctx or "").split())[:engines.ABOUT_CHARS]
        if ctx:
            props["contexto"] = ctx
        if props:
            try:
                self.notes_save({"path": rel, "props": props, "_al_grabar": True})
            except Exception as e:
                log_exc()
                avisos.append(f"sin cabecera de notas ({e.msg if isinstance(e, ApiError) else e})")
        self.library.at = 0
        job = None
        preset = str(q.get("preset") or st.get("preset") or "rapido")
        try:  # el idioma de la reunión es el de su .md (None: el de cada reunión, como «Transcribir los pendientes»)
            job = (self.jobs.submit([rel], preset, None) or [None])[0]
        except ApiError as e:
            avisos.append(f"no se ha puesto a transcribir: {e.msg}")
        log(f"⤒ subido: {rel} ({original}, {total} bytes" + (f", {dur:.0f} s" if dur else "") + ")"
            + (f" [{v['project']} · {v['name']}]" if v else "") + (f" · aviso: {'; '.join(avisos)}" if avisos else ""))
        return {"path": rel, "job": job, "carpeta": rel_of(dest), "duracion": dur, "aviso": "; ".join(avisos) or None}

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
        """Listas de vocabulario con sus términos: con ellas calendario.suggest elige el tema de la reunión. La que no
        se puede leer (a medio sincronizar en OneDrive, sin VPN, borrada entre listar y leer) se salta, como en
        /api/vocab: si no, se perdía la sugerencia entera, con el nombre y los participantes de Outlook (revisión de la
        integración del 08/11/2026)."""
        out = []
        for v in list_vocab():
            try:
                out.append(dict(v, terms=read_vocab(v["_abs"])[1]))
            except OSError:
                continue
        return out

    def reunion_actual(self, q):
        """GET /api/reunion/actual: ventana de Teams, cita de Outlook (si está activado, o ?outlook=1 para probar)
        y la sugerencia de nombre, tema y participantes. Lo usan el botón «Probar» y el diálogo Grabar."""
        # ?outlook=0 (la línea de Teams en Ajustes): solo Teams, sin despertar a Outlook por COM, y sin guardarlo como
        # la última lectura (la tarjeta y Ctrl+Alt+R usarían una sin la cita)
        outlook = {"1": True, "0": False}.get(str(q.get("outlook") or ""))
        r = calendario.reunion_actual(self.settings.get(), self._vocab_terms(), outlook=outlook)
        return r if outlook is False else self.vigia.recordar(r)

    def calendario_estado(self, q):
        """GET /api/calendario/estado[?probar=1]: el calendario en una frase, {nivel, estado, frase, accion} (contrato
        en calendario.estado). Sin probar no pide permisos ni abre avisos de Outlook; con probar, lo comprueba todo."""
        return calendario.estado(self.settings.get().get("outlook_on"), probar=q.get("probar") in ("1", "true", "si"))

    def calendario_activar(self, b):
        """POST /api/calendario/activar {"activar": true|false}: enciende o apaga la lectura del calendario y devuelve
        el estado (al encender, el de «Probar»: si Outlook pregunta, el usuario está delante para contestar)."""
        on = b.get("activar") is True
        self.settings.update(lambda d: d.update(outlook_on=on))
        log(f"   calendario: {'activado' if on else 'apagado'} desde Ajustes")
        r = calendario.estado(on, probar=on)  # primero «Probar», con el usuario delante por si Outlook pregunta
        ag = getattr(self, "agenda", None)
        if ag is not None:
            ag.pedir()  # después, la agenda de Hoy (o fuera, si se apaga): las dos a la vez competirían por Outlook
        return r

    # --- la pantalla Hoy (B7) y la agenda de Outlook de un mes
    def _reunion_ahora(self, st=None, recordar=True, citas=None):
        """(sugerencia, info) de la reunión de ahora: la última lectura del vigilante (o la ventana de Teams al momento)
        y, si no da nombre, la cita de la agenda de Outlook ya leída que encaja con ahora (calendario.reunion_de_ahora).
        La usan el plan de grabar (tarjeta, Ctrl+Alt+R, aviso, Hoy) y /api/hoy: una sola reunión de ahora para todos.
        `recordar=False` (Hoy, que pregunta cada 30 s): la lectura al momento no se guarda como la del vigilante, que
        durante una llamada la quiere con Outlook. `citas`: la agenda que ya se tiene (la misma que se enseña)."""
        st = st or self.settings.get()

        def leer():
            return calendario.reunion_actual(st, self._vocab_terms(), outlook=False)
        if hasattr(self.vigia, "lectura"):
            lect = self.vigia.lectura(leer, recordar=recordar)
        else:  # un vigilante que solo da la sugerencia (los dobles de los tests)
            lect = {"sugerencia": self.vigia.sugerencia(leer)}
        if citas is None:
            ag = getattr(self, "agenda", None)
            citas = ag.citas() if ag is not None and st.get("outlook_on") and ag.fresca() else []
        return calendario.reunion_de_ahora(lect, citas, self._vocab_terms)

    def _ultima_vez(self, nombre):
        """La última vez que se grabó la reunión que se llama así («La última vez (vie 30 oct): 3 tareas abiertas»): la
        más reciente de la biblioteca con ese título (el de su .md o el del archivo), sin la que se está grabando, con
        las tareas sin hacer de su resumen (None sin resumen)."""
        k = grabar_plan.norm_titulo(nombre)
        if not k:
            return None
        grabando = rel_of(self.rec.path) if getattr(self, "rec", None) and self.rec.alive() else None
        best = None
        for a in list(getattr(getattr(self, "library", None), "flat", None) or []):
            if a.get("recording") or a.get("path") == grabando or not a.get("date"):
                continue
            titulo = (a.get("props") or {}).get("titulo") or a.get("label") or ""
            if grabar_plan.norm_titulo(titulo) == k and (best is None or a["date"] > best["date"]):
                best = a
        if best is None:
            return None
        tareas = None
        if best.get("_sum"):
            try:
                tareas = grabar_plan.tareas_abiertas(read_text(best["_sum"]))
            except OSError:
                tareas = None
        return {"path": best["path"], "fecha": best["date"], "fecha_txt": grabar_plan.fecha_corta(best["date"]),
                "resumen": bool(best.get("_sum")), "tareas": tareas}

    PREPARADA_ANTES, PREPARADA_DESPUES = 10, 5  # minutos: lo preparado vale de 10 min antes a 5 min después

    @staticmethod
    def _nombre_preparada(s):
        """El asunto como lo deja la sugerencia (calendario.nombre_archivo: sin \\ / : * ? " < > | y recortado) y
        normalizado: «Comité: Q4» de Outlook es «Comité Q4» en la reunión de ahora."""
        return grabar_plan.norm_titulo(calendario.nombre_archivo(s))

    @classmethod
    def _clave_preparada(cls, asunto, inicio):
        return f"{inicio}|{cls._nombre_preparada(asunto)}"

    def _preparadas(self, st=None, dict_=False):
        """Lo preparado en settings.json (un fichero tocado a mano no tumba Hoy: lo que no sea un dict, fuera)."""
        # (desde update, con el dict que se está cambiando: settings.get() ahí se quedaría esperando al candado)
        d = (st if st is not None else self.settings.get()).get("preparadas")
        d = {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}
        return d if dict_ else list(d.values())

    def _preparada(self, nombre, st=None, now=None):
        """Lo preparado en Hoy («Preparar» en la agenda: {vocab?, idioma?, contexto?}) para la reunión que se llama así
        y es ahora (de 10 min antes de empezar a 5 min después de acabar), o None."""
        k = self._nombre_preparada(nombre)
        if not k:
            return None
        now = now or datetime.now()
        for p in self._preparadas(st):
            try:
                ini, fin = datetime.fromisoformat(p["inicio"]), datetime.fromisoformat(p["fin"])
            except (KeyError, TypeError, ValueError):
                continue
            if (self._nombre_preparada(p.get("asunto")) == k
                    and ini - timedelta(minutes=self.PREPARADA_ANTES) <= now <= fin + timedelta(
                        minutes=self.PREPARADA_DESPUES)):
                return {x: p[x] for x in ("vocab", "idioma", "contexto") if x in p}
        return None

    def hoy_preparar(self, b):
        """POST /api/hoy/preparar {asunto, inicio, fin, vocab?, idioma?, contexto?} (o quitar: true): «Preparar» una
        cita de la agenda. Lo que se dé (null = lo de siempre) se usa al grabarla, con «Grabar», la tarjeta,
        Ctrl+Alt+R o el aviso (plan_grabar). Se guarda en settings.json; lo de citas que acabaron hace más de un día,
        fuera."""
        asunto = " ".join(str(b.get("asunto") or "").split())[:200]
        try:
            ini = datetime.fromisoformat(str(b.get("inicio") or "")[:16])
            fin = datetime.fromisoformat(str(b.get("fin") or "")[:16])
        except ValueError:
            raise ApiError("Esa cita no tiene una hora válida.")
        if not asunto:
            raise ApiError("Esa cita no tiene asunto: no se puede preparar.")
        entrada = {"asunto": asunto, "inicio": ini.isoformat(timespec="minutes"),
                   "fin": fin.isoformat(timespec="minutes")}
        if not b.get("quitar"):
            if b.get("vocab") is not None:
                vid = str(b["vocab"])
                if vid and not find_vocab(None, None, vid):
                    raise ApiError("Ese proyecto ya no está: elige otro.")
                entrada["vocab"] = vid
            if b.get("idioma") is not None:
                lang = str(b["idioma"]).strip().lower()
                if lang and not IDIOMA_RE.match(lang):
                    raise ApiError("Idioma no válido: " + lang)
                entrada["idioma"] = "" if lang == "auto" else lang
            ctx = " ".join(str(b.get("contexto") or "").split())[:500]
            if ctx:
                entrada["contexto"] = ctx
        guardar = len(entrada) > 3  # sin nada que usar, es quitarla
        clave = self._clave_preparada(asunto, entrada["inicio"])
        limite = (datetime.now() - timedelta(days=1)).isoformat(timespec="minutes")

        def upd(d):
            prep = {k: v for k, v in self._preparadas(d, dict_=True).items()
                    if k != clave and str(v.get("fin") or "") >= limite}
            if guardar:
                prep[clave] = entrada
            d["preparadas"] = dict(sorted(prep.items(), key=lambda kv: kv[1].get("inicio") or "")[-200:])
        self.settings.update(upd)
        log(f"   hoy: reunión {'preparada' if guardar else 'sin preparar'} ({len(entrada) - 3} cosas)")
        return {"ok": True, "preparada": entrada if guardar else None}

    def hoy(self, q):
        """GET /api/hoy[?refrescar=1]: la pantalla Hoy sin esperar a Outlook. La reunión de ahora (título, de dónde
        sale y, si la agenda la tiene, sus horas) con su plan de grabar (plan.py, el mismo que la tarjeta y Ctrl+Alt+R:
        «Grabar» manda plan.rec a /api/rec/start), la última vez que se grabó esa reunión y la agenda del próximo mes,
        leída en segundo plano (_agenda_watch). Si la agenda es vieja (o refrescar=1), se pide otra lectura sin
        esperarla."""
        st = self.settings.get()
        activado = bool(st.get("outlook_on"))
        ag = getattr(self, "agenda", None)
        if ag is not None and activado and (q.get("refrescar") in ("1", "true") or (
                q.get("abrir") in ("1", "true") and ag.toca(calendario.AGENDA_VIEJA))):
            ag.pedir()  # el sondeo de cada 30 s no relee: cada lectura ocupa Outlook (y el candado COM)
        apagado = None
        if not activado:  # sin hablar con Outlook: apagado, Outlook nuevo, sin Outlook o no es Windows
            e = calendario.estado(False)
            apagado = (e["estado"], e["frase"])
        # una sola foto de la agenda para la reunión de ahora y la lista (el hilo puede estar renovándola)
        agenda = ag.publica(activado, apagado) if ag is not None else None
        fresca = ag is not None and ag.fresca()
        sug, info = self._reunion_ahora(st, recordar=False, citas=[dict(c) for c in agenda["citas"]]
                                        if agenda and activado and fresca else [])
        plan = self.plan_grabar({}, st=st, sug=sug)
        nombre = plan["nombre"]["valor"]
        prep = self._preparadas(st, dict_=True)
        for c in (agenda or {}).get("citas") or []:  # lo preparado de cada cita («Preparada · Alfa › General»)
            p = prep.get(self._clave_preparada(c.get("subject"), c.get("start")))
            if p:
                c["preparada"] = {x: p[x] for x in ("vocab", "idioma", "contexto") if x in p}
        return {"ahora": info, "plan": plan, "ultima": self._ultima_vez(nombre) if nombre else None, "agenda": agenda}

    def _agenda_watch(self):
        """Hilo de la agenda de Outlook (B7): la lee al poco de arrancar, cada AGENDA_CADA s y cuando Hoy la pide."""
        ag = self.agenda
        forzado = ag.despertar.wait(5)  # que el arranque no compita con Outlook
        while True:
            ag.despertar.clear()
            try:
                self._agenda_paso(forzado)
            except Exception:
                log_exc()
            forzado = ag.despertar.wait(60)

    def _agenda_paso(self, forzado=False, entorno=None):
        """Una vuelta del hilo de la agenda. Con el calendario apagado, se olvida lo leído. Mientras se graba o hay una
        llamada no se relee (Outlook es de la reunión y el vigilante lo consulta cada minuto), salvo que aún no haya
        nada. Lo que se sabe sin hablar con Outlook (cerrado, sin pywin32, Outlook nuevo…) se anota sin consultarlo."""
        ag, st = self.agenda, self.settings.get()
        if not st.get("outlook_on"):
            if ag.datos is not None or ag.fallo is not None:
                ag.olvidar()
            return
        if not (forzado or ag.toca()):
            return
        # en llamada: la lectura del registro de privacidad (meeting_prev), también con «Avisarme si estoy en una
        # llamada» apagado, que deja meeting en None
        ocupado = bool(getattr(self, "rec", None) and self.rec.alive()) or bool(
            getattr(self, "meeting", None) or getattr(self, "meeting_prev", None))
        if ocupado and ag.datos is not None:
            return
        sesion = getattr(self, "agenda_sesion", None)
        if sesion is not None:  # pruebas de la interfaz (CHULETA_PRUEBAS_OUTLOOK)
            ag.refrescar(session=sesion)
            return
        fallo = calendario.sin_leer(entorno or calendario.entorno_outlook())
        if fallo:
            ag.anotar(fallo)
            return
        ag.refrescar()

    # --- edicion de transcripciones
    _tx_save_lock = threading.Lock()

    def save_transcript(self, b):
        """Guarda lo corregido a mano. La primera vez copia lo que escribio Whisper (para poder volver). De uno en uno:
        al cerrar la pagina el mismo texto llega dos veces a la vez (keepalive y el fetch normal), y el segundo podia
        escribir lo corregido antes de que el primero copiara el original."""
        with self._tx_save_lock:  # dentro: si la reunion se acaba de mover, su ruta nueva (_move_now lo coge)
            return self._save_transcript(dict(b, path=alias_of(b.get("path"))))

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
            gen = b.get("gen")
            return self._save_live_edits(job, segs, base, gen if isinstance(gen, int) and not isinstance(gen, bool)
                                         else None)
        if job:
            raise ApiError("Se está haciendo el repaso final (1–2 min por hora de reunión); "
                           "podrás editar en cuanto termine."
                           if job.final_pass else "Se está transcribiendo; podrás editar cuando termine.")
        if not segs:
            raise ApiError("La transcripción no puede quedar vacía.")
        copia_original(rel, base)
        if os.path.isfile(base + ".srt") and all(a is not None and e is not None for a, e, _ in segs):
            write_outputs(abs_path, [(float(a), float(e), t) for a, e, t in segs])
        else:  # transcripcion solo en .txt (sin tiempos)
            save_text(base + ".txt", "\n".join(t for _, _, t in segs) + "\n", "utf-8-sig")
        self.library.at = 0
        self.jobs.schedule_outputs(rel)  # destinos de salida: el documento con la transcripcion corregida
        mt = max((os.path.getmtime(base + e) for e in (".txt", ".srt") if os.path.isfile(base + e)), default=0)
        return {"ok": True, "tx_mtime": mt, "count": len(segs)}

    def _save_live_edits(self, job, segs, base, gen=None):
        """Corregir mientras se transcribe en directo: se guardan las diferencias con lo que dio Whisper
        y se aplican al escribir cada trozo nuevo, asi el directo no pisa lo corregido. gen: la generación del texto
        que tenía el editor (/api/transcript → text_gen). Si desde entonces el directo ha rehecho texto ya escrito
        (cambio de idioma), lo que va desde ahí (rehecho_desde) no se toca: ni se corrige con el texto de antes ni se
        da por borrado (antes salía como «frase borrada» y se perdía lo rehecho)."""
        if not job.out:
            raise ApiError("El directo aún está empezando; prueba en unos segundos.")
        sent = {seg_key(a): t for a, _, t in segs if a is not None}
        last = max(sent) if sent else -1.0  # lo que llego despues aun no lo veias: no se toca
        with job.edit_lock:
            corte = None
            if gen is not None and gen != getattr(job, "text_gen", 0):
                # lo rehecho despues de la version del editor; ninguno (era la de otro directo de la reunion, que
                # empezo con el texto del archivo): como siempre
                corte = min((t for g, t in getattr(job, "rewrites", []) if g > gen), default=None)
            for a, _, t in job.raw:
                k = seg_key(a)
                if corte is not None and a >= corte - 1e-6:
                    continue  # rehecho despues de que el editor cargara su texto
                if k in sent:
                    if sent[k] != " ".join(t.split()):
                        job.edits[k] = sent[k]
                    else:
                        job.edits.pop(k, None)
                elif k <= last:
                    job.edits[k] = ""  # la dejaste vacia: frase borrada
            job.out.write(apply_edits(job.raw, job.edits))
            gen_ahora = getattr(job, "text_gen", 0)
        self.library.at = 0
        mt = max((os.path.getmtime(base + e) for e in (".txt", ".srt") if os.path.isfile(base + e)), default=0)
        return {"ok": True, "tx_mtime": mt, "count": len(segs), "live": True, "text_gen": gen_ahora,
                "rehecho_desde": corte}

    def restore_transcript(self, b):
        """«Restaurar el original». Como save_transcript, con el candado que coge _move_now: si la reunion se esta
        moviendo (B1c), espera y restaura en la ruta nueva; si no, dejaba el original en la carpeta vieja, borraba la
        copia y respondia «ok» (revision de la integracion del 08/11/2026)."""
        with self._tx_save_lock:
            return self._restore_transcript(dict(b, path=alias_of(b.get("path"))))

    def _restore_transcript(self, b):
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

    # --- aprender de tus correcciones (aprender.py)
    def _propuestas(self, abs_path):
        """Lo que se puede aprender de lo que corregiste en esta reunion (aprender.propuestas), con las listas donde ya
        esta cada termino, la lista de la reunion (la que recibe Whisper; "" = el vocabulario general) y todas las
        listas para elegir otra. Nada mientras se transcribe ni sin copia del original."""
        rel = rel_of(abs_path)
        vacio = {"propuestas": [], "lista": "", "proyecto": "", "listas": []}
        if self.jobs.active_for(rel):
            return vacio
        orig = original_de(rel)
        data = self.transcripts.load(os.path.splitext(abs_path)[0]) if orig else None
        if not orig or not data:
            return vacio
        apr = aprendido_de(self.settings.get())
        reg = registro_aprendido(rel)
        hechos = {(aprender.normal(c["de"]), aprender.normal(c["a"])) for c in reg["cambios"]}
        no = {aprender.normal(x) for x in apr["no"]} | set(reg["respondidas"])
        ps = aprender.propuestas(orig, data["segments"], hechos, no, [tuple(x) for x in apr["no_reglas"]])
        if not ps:
            return vacio
        props = md_props(abs_path)
        v = lista_de_reunion(abs_path, props)
        listas = self._vocab_terms()
        general = {aprender.normal(t) for t in engines.split_terms(self.settings.get().get("prompt") or "")}
        for p in ps:
            k = aprender.normal(p["termino"])
            p["en"] = [l["id"] for l in listas if k in {aprender.normal(t) for t in l["terms"]}]
            p["en_general"] = k in general
            for x in p["antes"]:  # la regla que ya tienes, en que proyectos (la interfaz no la ofrece otra vez alli)
                kd = aprender.normal(x["texto"])
                x["reglas_en"] = [r["proyecto"] for r in apr["reglas"]
                                  if aprender.normal(r["de"]) == kd and aprender.normal(r["a"]) == k]
        # el proyecto de la reunion (aunque su lista no este): si la propuesta va al vocabulario general, la casilla
        # de la regla viene sin marcar, porque valdria para las reuniones sin proyecto y no para las de este
        out = {"propuestas": ps, "lista": v["id"] if v else "", "proyecto": v["project"] if v else "",
               "proyecto_reunion": proyecto_de_reunion(abs_path, props),
               "listas": [{"id": l["id"], "project": l["project"], "name": l["name"],
                           "conectada": bool(l.get("conectada"))} for l in listas]}
        guardada = temas().get(rel) or ""
        lid = guardada[len(VLINK):].split("/")[0] if guardada.startswith(VLINK) else ""
        sin_carpeta = [k for k, l in list(VOCAB_LINKS.items()) if not os.path.isdir(l["path"])
                       and (k == lid or fold(l["name"]) == fold(props.get("proyecto") or ""))]
        if not v and props.get("workstream") and sin_carpeta:
            # su lista esta en una carpeta conectada que ahora no se encuentra (VPN, red, disco): no se elige otra por
            # el; si nunca tuvo lista o se borro, como siempre: el vocabulario general, que es el que uso Whisper
            out["lista"] = None
            out["falta"] = {"proyecto": props.get("proyecto") or "", "workstream": props["workstream"]}
        return out

    def aprender_get(self, q):
        """GET /api/aprender?path=: las propuestas de esa reunion (ver _propuestas)."""
        return self._propuestas(safe(q.get("path"), "file"))

    def aprender_anadir(self, b):
        """POST /api/aprender/anadir {path, termino, lista ("" = vocabulario general), reglas: [lo que escribio
        Whisper]}: tu sí. El termino va al final de la lista elegida (tambien en una carpeta conectada, sin tocarle nada
        mas) y, de las reglas seguras de esa propuesta, las que marcaste se aplican desde la siguiente transcripcion del
        proyecto de esa lista; las que desmarcaste no se vuelven a proponer. Solo vale lo que se esta proponiendo ahora
        en esa reunion: nada se aprende que no hayas visto."""
        abs_path = safe(b.get("path"), "file")
        termino = aprender.termino_valido(b.get("termino"))
        p = next((x for x in self._propuestas(abs_path)["propuestas"]
                  if termino and aprender.normal(x["termino"]) == aprender.normal(termino)), None)
        if not p:
            raise ApiError("Esa propuesta ya no está: puede que hayas vuelto a cambiar el texto. Recarga la reunión.")
        termino = p["termino"]
        quiere = {aprender.normal(x) for x in b.get("reglas") or [] if isinstance(x, str)}
        seguras = [x for x in p["antes"] if x["regla"]]
        reglas = [x for x in seguras if aprender.normal(x["texto"]) in quiere]
        if len(reglas) != len(quiere):
            raise ApiError("Esa corrección ya no se puede aprender. Recarga la reunión.")
        lista = str(b.get("lista") or "")
        v = find_vocab(None, None, lista) if lista else None
        if lista and not v:
            vid = lista[len(VLINK):].split("/")[0] if lista.startswith(VLINK) else ""
            if vid in VOCAB_LINKS and not os.path.isdir(VOCAB_LINKS[vid]["path"]):
                raise ApiError(f"No encuentro tu carpeta de listas «{VOCAB_LINKS[vid]['name']}»: ¿sin conexión o con "
                               "el disco desconectado? Vuelve a probar cuando esté, o elige otra lista.")
            raise ApiError("Esa lista ya no existe. Elige otra.")
        lang = self.settings.get().get("language") or ""
        if v:
            ya = anadir_a_lista(v, termino)
            try:
                cabe = termino not in vocab_info(v, lang)["dropped"]
            except OSError:
                cabe = True
        else:
            general = self.settings.get().get("prompt") or ""
            ya = aprender.normal(termino) in {aprender.normal(t) for t in engines.split_terms(general)}
            if not ya:
                general = aprender.anadir_texto(general, termino)
                self.settings.update(lambda d: d.update(prompt=general))
            cabe = termino not in vocab_fit(general, lang)["dropped"]
        proyecto, ahora = v["project"] if v else "", time.time()
        try:  # desde esta reunion ya no se vuelve a proponer (aunque haya ido a otra lista que la de la reunion)
            with self._tx_save_lock:
                reg = registro_aprendido(rel_of(abs_path))
                k = aprender.normal(termino)
                if k not in reg["respondidas"] and os.path.isdir(backup_dir(rel_of(abs_path))):
                    reg["respondidas"].append(k)
                    guardar_registro(rel_of(abs_path), reg)
        except OSError:
            log_exc()
        if reglas:
            def upd(d):
                a = aprendido_de(d)
                for x in reglas:  # una por lo que escribio Whisper y proyecto: la nueva sustituye a la vieja
                    kd = aprender.normal(x["texto"])
                    a["reglas"] = [r for r in a["reglas"] if not (aprender.normal(r["de"]) == kd
                                                                  and mismo_proyecto(r["proyecto"], proyecto))]
                    a["reglas"].append({"de": x["texto"], "a": termino, "proyecto": proyecto, "at": ahora})
                d["aprendido"] = a
            # la casilla desmarcada no se apunta como un «no» para siempre (no se veria en ningun sitio): en esta
            # reunion ya no se pregunta (respondidas) y, si lo vuelves a corregir en otra, se vuelve a ofrecer
            self.settings.update(upd)
        self.library.at = 0
        n = len(reglas)
        log(f"✦ aprendido: {termino} en {(v['project'] + ' · ' + v['name']) if v else 'el vocabulario general'} "
            f"({n} regla{'s' if n != 1 else ''})")
        return {"ya_estaba": ya, "cabe": cabe, "reglas": n,
                "lista": {k: v[k] for k in ("id", "project", "name")} if v else None,
                "prompt": None if v else self.settings.get().get("prompt") or ""}

    def aprender_no(self, b):
        """POST /api/aprender/no {termino}: «No»: ese termino no se vuelve a proponer (en ninguna reunion)."""
        termino = aprender.termino_valido(b.get("termino"))
        if not termino:
            raise ApiError("Falta el término.")
        k = aprender.normal(termino)

        def upd(d):
            a = aprendido_de(d)
            if k not in {aprender.normal(x) for x in a["no"]}:
                a["no"].append(termino)
            d["aprendido"] = a
        self.settings.update(upd)
        return {"ok": True}

    def aprender_deshacer(self, b):
        """POST /api/aprender/deshacer {path, de, a}: «Deshacer» de lo que cambio Tenca en esa reunion
        (deshacer_aprendido). Con el candado de las correcciones a mano, como Restaurar original."""
        with self._tx_save_lock:
            abs_path = safe(b.get("path"), "file")
            rel = rel_of(abs_path)
            if self.jobs.active_for(rel):
                raise ApiError("Se está transcribiendo; espera a que termine.")
            de = str(b.get("de") or "")
            hechos, saltados = deshacer_aprendido(rel, de, str(b.get("a") or ""))
        self.library.at = 0
        if hechos:
            self.jobs.schedule_outputs(rel)  # destinos de salida: el documento vuelve a lo que escribio Whisper
        # la regla sigue para las proximas reuniones: la interfaz ofrece olvidarla (su proyecto, para aprender_olvidar)
        proyecto = proyecto_de_reunion(abs_path)
        r = next((r for r in aprendido_de(self.settings.get())["reglas"] if aprender.normal(r["de"]) ==
                  aprender.normal(de) and mismo_proyecto(r["proyecto"], proyecto)), None)
        return {"deshechos": hechos, "no_deshechos": saltados, "regla": {"proyecto": r["proyecto"]} if r else None}

    def _reglas_publicas(self):
        return [{k: r[k] for k in ("de", "a", "proyecto")} for r in aprendido_de(self.settings.get())["reglas"]]

    def aprender_reglas(self, q):
        """GET /api/aprender/reglas: las reglas aprendidas y lo que dijiste que no se propusiera (Ajustes ›
        Proyectos)."""
        return {"reglas": self._reglas_publicas(), "no": aprendido_de(self.settings.get())["no"]}

    def aprender_regla(self, b):
        """POST /api/aprender/regla {antes: {de, proyecto} | null, de, a, proyecto}: cambiar una entrada del diccionario
        personal desde Ajustes (lo que escribio Whisper, lo correcto o el proyecto). Lo ya cambiado en reuniones
        anteriores no se toca: cada una sigue con su «Deshacer»."""
        de, a = aprender.termino_valido(b.get("de")), aprender.termino_valido(b.get("a"))
        if not de or not a:
            raise ApiError("Escribe lo que pone Whisper y lo correcto (sin comas ni punto y coma).")
        if de == a:
            raise ApiError("Lo que pone Whisper y lo correcto son iguales: no habría nada que cambiar.")
        toks = [t for t, _, _ in aprender.tokens(de)]
        if not toks or len(toks) > aprender.MAX_REGLA:
            raise ApiError("Lo que pone Whisper tiene que ser una palabra, o como mucho tres.")
        if all(aprender.fold(t) in aprender.COMUNES or aprender.fold(t) in aprender.ENLACES for t in toks):
            raise ApiError(f"«{de}» es una palabra muy corriente: cambiaría muchas frases que están bien. Pon algo más "
                           "concreto.")
        if len(aprender.letras(de)) < 3:
            raise ApiError("Lo que pone Whisper tiene que tener al menos 3 letras: algo más corto saldría en muchas "
                           "frases que están bien.")
        if not aprender.regla_valida(de, a):  # solo mayúsculas o tildes en una palabra corriente («plan» → «Plan»)
            if de.lower() != a.lower():  # cambian tildes (y quizá mayúsculas), no solo mayúsculas
                raise ApiError(f"Solo cambian las tildes de «{de}»: no hace falta en el diccionario, y cambiaría "
                               "frases que están bien.")
            raise ApiError(f"Cambiar «{de}» por «{a}» tocaría frases que están bien: si solo cambian las mayúsculas, "
                           "vale para siglas (KPI) o nombres como iPhone. Pon algo más concreto.")
        proyecto = str(b.get("proyecto") or "")
        antes = b.get("antes") if isinstance(b.get("antes"), dict) else None
        conocidos = {v["project"] for v in list_vocab()} | {""}
        if antes:
            conocidos.add(str(antes.get("proyecto") or ""))
        if not any(mismo_proyecto(proyecto, p) for p in conocidos):
            raise ApiError("Ese proyecto ya no existe. Elige otro.")
        proyecto = next((p for p in sorted(conocidos) if mismo_proyecto(proyecto, p)), proyecto)  # como se llama
        k, ka = aprender.normal(de), aprender.normal(str((antes or {}).get("de") or ""))
        pa = str((antes or {}).get("proyecto") or "")
        err = []

        def upd(d):
            ap = aprendido_de(d)
            es_la = lambda r: antes and aprender.normal(r["de"]) == ka and mismo_proyecto(r["proyecto"], pa)  # noqa
            if any(not es_la(r) and aprender.normal(r["de"]) == k and mismo_proyecto(r["proyecto"], proyecto)
                   for r in ap["reglas"]):
                err.append(f"Ya tienes «{de}» en tu diccionario para ese proyecto.")
                return
            if antes and not any(es_la(r) for r in ap["reglas"]):
                err.append("Esa entrada ya no está en tu diccionario.")
                return
            ap["reglas"] = [r for r in ap["reglas"] if not es_la(r)]
            ap["reglas"].append({"de": de, "a": a, "proyecto": proyecto, "at": time.time()})
            d["aprendido"] = ap
        self.settings.update(upd)
        if err:
            raise ApiError(err[0])
        log(f"✦ diccionario: {de} → {a}")
        return {"reglas": self._reglas_publicas(), "no": aprendido_de(self.settings.get())["no"]}

    def aprender_olvidar(self, b):
        """POST /api/aprender/olvidar {de, proyecto}: la regla deja de aplicarse en las proximas reuniones (lo ya
        cambiado se queda, con su Deshacer). {no: termino}: ese termino se puede volver a proponer."""
        k, proyecto = aprender.normal(str(b.get("de") or "")), str(b.get("proyecto") or "")
        kno = aprender.normal(str(b.get("no") or ""))  # {"no": termino}: que se pueda volver a proponer
        quitadas = []

        def upd(d):
            a = aprendido_de(d)
            if kno:
                a["no"] = [x for x in a["no"] if aprender.normal(x) != kno]
            else:
                quitadas[:] = [r for r in a["reglas"] if aprender.normal(r["de"]) == k
                               and mismo_proyecto(r["proyecto"], proyecto)]
                a["reglas"] = [r for r in a["reglas"] if r not in quitadas]
            d["aprendido"] = a
        self.settings.update(upd)
        if quitadas:
            log(f"✦ regla olvidada: {quitadas[0]['de']} → {quitadas[0]['a']}")
        return {"reglas": self._reglas_publicas(), "no": aprendido_de(self.settings.get())["no"]}

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
        with self._move_lock():  # una reunion que se esta moviendo: se espera y se encola con su ruta nueva
            return self._transcribe(b)

    def _transcribe(self, b):
        paths = [alias_of(p) for p in (b.get("paths") or ([b["path"]] if b.get("path") else []))]
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
        """Traducción del directo en la nube, por reunión (apagada por defecto): {id, on, to}. to: «es» o «en» (msg
        1152); se recuerda para esa reunión (traduccion_destino), también con la traducción apagada. live.py hace el
        resto: con otro destino, vuelve a traducir lo último al idioma nuevo."""
        job = self.jobs._live_job(b.get("id"))
        to = b.get("to")
        if to is not None and to not in live.TRANSLATE_TO:
            raise ApiError("La traducción del directo solo puede ser al español o al inglés.")
        if b.get("on"):  # sin servicio de chat (Azure OpenAI) no hay traduccion: se dice, no falla a medias
            why = engines.chat_missing(self.settings.get()["cloud"], "La traducción del directo")
            if why:
                raise ApiError(why)
        if to is not None:
            job.translate_to = to
            traduccion_destinos().set(job.path, to)
        job.translate = bool(b.get("on"))
        if isinstance(job.directo, dict):  # que la respuesta ya lo refleje (live.py lo vuelve a publicar en <1 s)
            job.directo = dict(job.directo, translate=job.translate, translate_to=live.translate_to(job))
        return job.public()

    def ocultar_captura(self, b):
        """POST {activado}: guarda «Ocultar Tenca al compartir pantalla» y lo aplica ya a la ventana (Ajustes › Al
        grabar; sin «Guardar», como «Arrancar con Windows»). Sin «activado», solo dice cómo está (como el GET)."""
        if isinstance(b, dict) and "activado" in b:
            on = b.get("activado") is True
            self.settings.update(lambda d: d.update(ocultar_al_compartir=on))
            if WINDOW is not None:
                WINDOW.set_capture(on)
            log(f"ajustes: ocultar {APP_NAME} al compartir pantalla {'activado' if on else 'desactivado'}")
        return captura_estado(self.settings.get())

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
        with self._write_locks():  # unas notas o una correccion que llegan ahora esperan y van a la ruta nueva
            try:
                move_files(moves)
            except ApiError:
                if made:
                    try:
                        os.rmdir(new_dir)
                    except OSError:
                        pass
                raise
            return self._after_move(src, moves, own, new_dir, fuera, dest_dir)

    def _write_locks(self):
        """Los candados de quien escribe en los archivos de una reunion desde la interfaz (notas_save,
        save_transcript): mientras se mueve, esperan; al soltarlos, su ruta ya lleva a la nueva (ALIASES)."""
        stack = contextlib.ExitStack()
        if getattr(self, "notes_lock", None) is not None:
            stack.enter_context(self.notes_lock)
        stack.enter_context(self._tx_save_lock)
        return stack

    def _after_move(self, src, moves, own, new_dir, fuera, dest_dir):
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
    MOV_INTENTOS = 12  # ~20 min: despues se deja donde esta y se dice

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
        folder = b.get("folder")
        if b.get("folder_abs"):
            p = os.path.normpath(os.path.expandvars(str(b["folder_abs"]).strip().strip('"')))
            inside = _inside(p, ROOT) or any(_inside(p, l["path"]) for l in LINKS.values())
            folder = rel_of(p) if inside and os.path.isdir(p) else self.link_add({"path": p})["rel"]
        with self._move_lock():  # con un movimiento en marcha se espera: despues, la ruta nueva (y ya no hay nada)
            src = safe(alias_of(b.get("path")), "file")
            if os.path.splitext(src)[1].lower() not in AUDIO_EXT:
                raise ApiError("Solo se puede cambiar la carpeta de una reunión.")
            rel = rel_of(src)
            if folder is None:
                if self._movs().pop(rel) is not None:
                    log(f"📁 {rel}: ya no se mueve (deshecho)")
                return {"path": rel, "pendiente": None}
            return self._destino_pendiente(rel, str(folder).replace("\\", "/").strip("/"))

    def _destino_pendiente(self, rel, folder_rel):
        with self._move_lock():
            return self._destino_pendiente_locked(rel, folder_rel)

    def _destino_pendiente_locked(self, rel, folder_rel):
        src = safe(alias_of(rel), "file")
        rel = rel_of(src)
        dest = safe(folder_rel, "dir")
        if in_app_dir(dest):
            raise ApiError("No se puede mover ahí")
        own = own_meeting_dir(src)
        if own and _norm_dir(dest) == _norm_dir(own):  # su propia carpeta (se ve como carpeta si tiene algo mas)
            dest = os.path.dirname(own)
        if _norm_dir(dest) == _norm_dir(self._donde_esta(src)):  # la carpeta donde ya esta: nada que mover
            if self._movs().pop(rel) is not None:
                log(f"📁 {rel}: ya no se mueve (deshecho)")
            return {"path": rel, "pendiente": None}
        if own and _inside(dest, own):
            raise ApiError("No se puede mover una reunión dentro de su propia carpeta.")
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

    def _tick_movimientos(self):
        """Jobs.on_tick (cada 3 s): si hay algo apuntado que ya toca, se mueve en otro hilo; el vigilante de OBS no
        espera a un movimiento lento (una copia entre unidades)."""
        now = time.time()
        if not any(e.get("next", 0) <= now for _, e in self._movs().items()):
            return
        t = self.__dict__.get("_mover_hilo")
        if t is None or not t.is_alive():
            t = threading.Thread(target=self._aplicar_movimientos, daemon=True, name="mover")
            self.__dict__["_mover_hilo"] = t
            t.start()

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
            for rel, _ in pend:
                e = movs.get(rel)  # con el candado: un «Deshacer» o un cambio de justo antes manda
                if not e or e.get("next", 0) > time.time() or self._ocupada(rel):
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
        if src is None and e.get("nuevo"):  # la app se cerro a medio mover: el audio (el ultimo) ya esta alli
            try:
                moved = safe(e["nuevo"], "file")
            except ApiError:
                moved = None
            if moved:
                movs.pop(rel)
                if target:  # su carpeta: la vieja, si se ha quedado vacia, fuera; el registro y sus referencias
                    old_dir = rel.rpartition("/")[0]
                    try:
                        os.rmdir(safe(old_dir, "dir"))
                    except (ApiError, OSError):
                        pass
                    carpetas_reunion().discard(old_dir)
                    carpetas_reunion().add(target)
                    self._rename_refs(old_dir, target, folder=True)
                    self.settings.update(lambda d: settings_moved(d, old_dir, target, folder=True))
                self._moved_refs(rel, e["nuevo"])
                self._nota_movida(rel, e["nuevo"])
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
        # a donde va (la carpeta de la reunion y su audio), apuntado mientras se mueve: si la app se cierra a medias,
        # el siguiente intento termina ahi. Un intento que falla lo deshace todo (move_files) y lo olvida
        if own:
            target = target or rel_of(free_dir(dest, os.path.basename(own), mine=own))
            e["target"] = target if _norm_dir(safe(target)) != _norm_dir(own) else None
        e["nuevo"] = rel_of(os.path.join(safe(target) if own else dest, os.path.basename(src)))
        movs.set(rel, e)
        try:
            new = self._move_now(src, dest, target=safe(target) if own else None)
        except ApiError as err:
            if not getattr(err, "incompleto", False):
                e.pop("target", None)
            e.pop("nuevo", None)
            n = int(e.get("intentos") or 0) + 1
            if n > self.MOV_INTENTOS:  # un error que no cambia (otra unidad, ruta larga, sin permiso): se deja
                movs.pop(rel)
                msg = f"No se ha podido mover la reunión a {display_rel(e['to'])}: {err.msg} Se queda donde está."
                log(f"   movimiento pendiente de {rel}: {err.msg} Se deja tras {n - 1} intentos")
                self._nota_movida(rel, None, msg)
                notify("No se ha movido la reunión", msg)
                return 0
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
        with self._move_lock():  # no a la vez que se mueve una reunion (la carpeta podria ser su destino)
            return self._rename_folder(b)

    def _rename_folder(self, b):
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
        rel = str(b.get("path") or "")
        p = vocab_link_path(rel) if rel.replace("\\", "/").strip("/").startswith(VLINK) else safe(rel)
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
        actual = perfil_de(self.settings.get())
        perfil = (check_perfil(b["perfil"], actual["outputs"], actual)  # valida antes de guardar
                  if isinstance(b.get("perfil"), dict) else {})
        antes = procesado_de(self.settings.get())
        cur = self.settings.get()
        audio = [(b[k].strip() or defecto) if isinstance(b.get(k), str) else (cur.get(k) or defecto)
                 for k, defecto in (("rec_mic", "default"), ("rec_sys", "all"))]
        if audio == ["none", "none"]:
            raise ApiError(SIN_AUDIO)

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
            if "rec_directo" in b:  # Ajustes › Al grabar: texto en directo (la tarjeta lo cambia por reunion)
                d["rec_directo"] = bool(b["rec_directo"])
            for k, defecto in (("rec_mic", "default"), ("rec_sys", "all")):
                if isinstance(b.get(k), str):
                    d[k] = b[k].strip()[:300] or defecto
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
                if k == "provider" and c.get(k) not in engines.PROVIDERS:
                    continue  # «Elige el proveedor…» sin elegir: no se guarda vacio (el motor tomaria Groq)
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
                if prov not in engines.PROVIDERS:
                    continue  # sin proveedor elegido no hay de quien: no se guardan como claves de Groq
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
        origen = cls.origen(ruta)  # «Claude Code de VS Code» si es la copia de la extension (07/11/2026)
        log(f"   agente: comprobando {origen} ({modelo})…" + (f" · {ruta}" if origen != cls.label else ""))
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
        aviso = getattr(ag, "aviso", None)
        if aviso:
            log(f"   agente: {aviso}")
        log(f"   agente: {cls.label} {version} comprobado ({secs:.1f} s)")
        return {"ok": True, "version": version, "seconds": round(secs, 1), "ruta": ruta, "origen": origen,
                "modelo_usado": getattr(ag, "modelo_usado", None), "aviso": aviso}

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

    # --- «Probar» del asistente «Configurar» de la app (probar.py): prueba real con lo que se elige, sin guardar
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
        elegido = proveedor_elegido(self.settings.get()) or cl.get("provider") in engines.PROVIDERS
        st["cloud"] = dict(st.get("cloud") or {})
        for k in ("provider", "model", "base_url", "azure_endpoint", "azure_deployment", "azure_api_version"):
            if k == "provider" and cl.get(k) not in engines.PROVIDERS:
                continue  # como al guardar: un proveedor vacio o desconocido no se prueba como si fuera Groq
            if k in cl:
                st["cloud"][k] = (cl[k] or "").strip()
        key = (cl.get("api_key") or "").strip()
        if key and "…" not in key and "•" not in key:
            st["cloud"]["api_key"] = key
        if st.get("procesado") == "api" and not elegido:
            st["cloud"]["provider"] = ""  # el motor lo rechaza con «Elige el proveedor…»: nada sale hacia Groq
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
        ruta = cls.find()
        return {"encontrado": bool(ruta), "label": cls.label, "origen": cls.origen(ruta) if ruta else None}

    def _powershell_env(self):
        """El entorno de la ventana de «Abrir PowerShell»: el de una PowerShell nueva (engines.windows_env) y, delante
        en su PATH, la carpeta del Claude Code que encuentra la app (08/11/2026). El instalador nativo no lo pone en el
        PATH («…\\.local\\bin is not in your PATH», medido en Windows) y la copia de VS Code tampoco: así, en esa
        ventana, `claude` (y su inicio de sesión en el navegador) funciona siempre. La app no inicia sesión por nadie:
        el usuario escribe claude y entra en la página de Claude."""
        env = engines.windows_env()
        cls, _ = agente_de(self.settings.get())
        ruta = cls.find()
        # y la carpeta del instalador nativo aunque aún no exista: el paso 1 instala en esa misma ventana y el 2
        # escribe claude en ella
        dirs = [os.path.dirname(os.path.abspath(ruta))] if ruta else []
        nativo = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), ".local", "bin")
        if os.path.normcase(nativo) not in map(os.path.normcase, dirs):
            dirs.append(nativo)
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
        return env

    def abrir_powershell(self, b):
        """«Abrir PowerShell» del asistente: una ventana vacía, sin ejecutar nada (con Claude Code en su PATH)."""
        if os.name != "nt":
            raise ApiError("Este botón solo funciona en Windows: abre una terminal y sigue los pasos.")
        subprocess.Popen(["powershell.exe", "-NoExit", "-NoLogo"], cwd=os.path.expanduser("~"),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), env=self._powershell_env())
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

    def _voices_lock(self):
        """Quien corrige los hablantes de una reunion desde la interfaz coge el candado de las notas: mientras la
        reunion se mueve (B1c, _move_now) espera y, al soltarlo, alias_of ya da su ruta nueva. Sin el, la correccion
        se escribia en la ruta vieja y se perdia (revision de la integracion del 08/11/2026)."""
        lock = getattr(self, "notes_lock", None)
        return lock if lock is not None else contextlib.nullcontext()

    def _speaker_update(self, abs_path, h, fn, creado=None, then=None):
        """Aplica fn(info, data) al hablante h y guarda el sidecar; con `then`, then(ruta) tambien dentro del candado
        (la respuesta: si se calculara fuera, un movimiento que empieza justo despues la dejaria a medias). Devuelve
        (la ruta de la reunion, la nueva si se acaba de mover; lo que devuelva then)."""
        with self._voices_lock():
            abs_path = safe(alias_of(rel_of(abs_path)), "file")
            data = self._speaker_check(abs_path, h, creado)
            fn(data["hablantes"][h], data)
            voices.write_sidecar(voices.sidecar_path(abs_path), data)
            self.library.at = 0
            self.jobs.schedule_outputs(rel_of(abs_path))  # destinos de salida: el documento lleva quien habla
            return abs_path, then(abs_path) if then else None

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
        nombre = " ".join(str(b.get("nombre") or "").split())[:80]
        if pid and not v.find(pid):
            raise ApiError("Esa persona ya no existe.")
        if b.get("recordar"):  # antes de cambiar nada: sin consentimiento no se toca ni la persona ni la reunion
            if not consent:
                raise ApiError("Para guardar la huella hace falta marcar la casilla de consentimiento.")
            if not (path and h):
                raise ApiError("Falta de qué reunión y hablante tomar la huella.")
        abs_path = None
        if path and not h:
            safe(path, "file")  # como siempre: una reunion que ya no existe se dice
        if path and h:
            with self._voices_lock():  # si la reunion se esta moviendo (B1c), espera y lee la de la ruta nueva
                abs_path = safe(alias_of(path), "file")
                side = self._speaker_check(abs_path, h, b.get("creado"))  # antes de crear a nadie
            if b.get("recordar") and side.get("creado") and not b.get("creado"):  # guardar una huella: solo de la
                raise ApiError("Vuelve a abrir la etiqueta para guardar la huella.")  # identificacion que se ve
            before = side["hablantes"][h].get("persona")
            pid = self._persona_for_speaker(v, b, nombre, consent)
            uid = (v.user() or {}).get("id")
            abs_path = self._speaker_update(abs_path, h, lambda info, d: voices.assign(d, h, pid, uid),
                                            b.get("creado"))[0]
            # fuera del candado (puede calcular una huella en un subproceso), con la ruta de ahora
            self._not_the_user(alias_of(rel_of(abs_path)), h, before, pid)
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
            rel = alias_of(rel_of(abs_path))  # la ruta de ahora: la reunion puede haberse movido entretanto
            vec, secs = self.jobs.speaker_vector(rel, h)
            data = voices.read_sidecar(voices.sidecar_path(safe(rel, "file"))) or {}
            secs = secs or (data.get("hablantes", {}).get(h) or {}).get("segundos") or 0
            remembered = v.remember(pid, vec, secs, os.path.basename(abs_path), time.strftime("%Y-%m-%dT%H:%M:%S"))
            log(f"🗣 huella guardada: {v.find(pid)['nombre']} ({remembered} huellas)")
        return {"id": pid, "huellas": remembered, "personas": v.public()}

    def voices_merge(self, b):
        """«Fusionar» dos personas (keep se queda, other desaparece). Primero se reescriben los sidecars que citan a
        other y solo despues se borra: si una reunion no se puede escribir (OneDrive o el antivirus con el fichero
        abierto), other sigue existiendo, se dice cuantas faltan y «Fusionar» se puede repetir (antes la persona se
        borraba primero y esas reuniones se quedaban con un id que ya no existe; revision de la integracion del
        08/11/2026)."""
        v = self.jobs.voices
        keep, other = b.get("id_a"), b.get("id_b")
        a_, b_ = v.find(keep), v.find(other)
        if keep != other and (not a_ or not b_):
            raise ApiError("Alguna de las dos personas ya no existe.")
        if keep != other and (a_.get("es_usuario") or b_.get("es_usuario")):
            raise ApiError("Tu persona no se fusiona con otras: si ese hablante no eres tú, ponle su nombre o pulsa "
                           "«No soy yo»; si eres tú, «Soy yo».")
        n, fallidas, hechas = 0, [], []
        if keep != other:
            self.library.refresh(max_age=5)
            for a in list(self.library.flat):  # sin mirar a["_spk"]: en pleno movimiento el sidecar ya esta en la nueva
                with self._voices_lock():  # como las correcciones: si se esta moviendo, espera y va a la ruta nueva
                    try:
                        abs_path = safe(alias_of(a["path"]), "file")
                    except ApiError:
                        continue  # ya no esta
                    side = voices.sidecar_path(abs_path)
                    if not os.path.isfile(side):
                        continue
                    try:  # que no se pueda LEER (OneDrive sin descargar, el antivirus) tambien es una que falta
                        data = json.loads(voices.read_text(side))
                    except OSError as e:
                        fallidas.append(f"{display_rel(rel_of(abs_path))} ({e.__class__.__name__})")
                        continue
                    except ValueError:  # roto: no es de nadie (como read_sidecar)
                        continue
                    if not isinstance(data, dict) or not any(
                            i.get("persona") == other for i in (data.get("hablantes") or {}).values()):
                        continue
                    cambiados = [h for h, i in data["hablantes"].items() if i.get("persona") == other]
                    for h in cambiados:
                        data["hablantes"][h]["persona"] = keep
                    try:  # una reunion con el fichero abierto no deja las demas a medias
                        voices.write_sidecar(side, data)
                    except OSError as e:
                        fallidas.append(f"{display_rel(rel_of(abs_path))} ({e.__class__.__name__})")
                        continue
                    hechas.append((a["path"], cambiados))
                    self.jobs.schedule_outputs(rel_of(abs_path))  # destinos de salida: con el nombre que queda
                n += 1
        self.library.at = 0
        if fallidas:
            log(f"🗣 fusión a medias ({n} reuniones actualizadas); sin actualizar: {', '.join(fallidas)}")
            raise ApiError(f"{len(fallidas)} {'reunión no se ha' if len(fallidas) == 1 else 'reuniones no se han'} "
                           "podido actualizar (¿abiertas en OneDrive o en otro programa?). Las dos personas siguen "
                           "ahí: ciérralas y vuelve a fusionar.")
        try:
            v.merge(keep, other)
        except (KeyError, ValueError) as e:  # cambio entretanto (otra pestaña)
            hay_keep, hay_other = v.find(keep) is not None, v.find(other) is not None
            if not hay_other:  # other ya no esta (olvidada o fusionada con otra): deshacer la dejaria huerfana
                raise ApiError("Alguna de las dos personas ya no existe."
                               + (" Sus reuniones ya muestran a la que se queda." if hay_keep else ""))
            sin_deshacer = self._merge_deshacer(hechas, keep, other)
            aviso = ("Alguna de las dos personas ya no existe." if isinstance(e, KeyError) else
                     "Tu persona no se fusiona con otras: si ese hablante no eres tú, ponle su nombre o pulsa "
                     "«No soy yo»; si eres tú, «Soy yo».")
            if not sin_deshacer:
                aviso += " No se ha cambiado ninguna reunión."
            elif hay_keep:
                aviso += (f" {sin_deshacer} {'reunión se ha' if sin_deshacer == 1 else 'reuniones se han'} quedado con "
                          "la persona elegida (¿abiertas en otro programa?).")
            raise ApiError(aviso)
        log(f"🗣 personas fusionadas ({n} reuniones actualizadas)")
        return {"id": keep, "reuniones": n, "personas": v.public()}

    def _merge_deshacer(self, hechas, keep, other):
        """Vuelve a poner `other` en los hablantes a los que voices_merge acababa de poner `keep` (hechas: [(rel,
        [h…])]); los que ya eran de keep se quedan. Devuelve cuantas reuniones no se han podido deshacer."""
        malas = 0
        for rel, hs in hechas:
            with self._voices_lock():
                try:
                    abs_path = safe(alias_of(rel), "file")
                except ApiError:
                    continue  # la reunion ya no esta (borrada entretanto): no hay nada que deshacer
                try:
                    side = voices.sidecar_path(abs_path)
                    data = json.loads(voices.read_text(side))
                    for h in hs:
                        if (data.get("hablantes") or {}).get(h, {}).get("persona") == keep:
                            data["hablantes"][h]["persona"] = other
                    voices.write_sidecar(side, data)
                    self.jobs.schedule_outputs(rel_of(abs_path))  # los documentos, otra vez con el nombre de antes
                except (OSError, ValueError, AttributeError):
                    log_exc()
                    malas += 1
        return malas

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
        pid, h = b.get("persona") or None, b.get("h")
        v = self.jobs.voices
        if pid and not v.find(pid):
            raise ApiError("Esa persona ya no existe.")
        uid = (v.user() or {}).get("id")
        with self._voices_lock():  # si la reunion se esta moviendo (B1c), espera y lee la de la ruta nueva
            abs_path = safe(alias_of(b.get("path")), "file")
            before = (self._speaker_check(abs_path, h, b.get("creado"))["hablantes"][h]).get("persona")
        abs_path, out = self._speaker_update(abs_path, h, lambda info, d: voices.assign(d, h, pid, uid),
                                             b.get("creado"), then=lambda p: self.speakers_get({"path": rel_of(p)}))
        # fuera del candado (puede calcular una huella en un subproceso), con la ruta de ahora
        self._not_the_user(alias_of(rel_of(abs_path)), h, before, pid)
        return out

    def speakers_join(self, b):
        """«Es el mismo que…» (msg 1242): dos hablantes de esta reunion son la misma voz (la app la partio en dos)."""
        h, con, v = b.get("h"), b.get("con"), self.jobs.voices
        with self._voices_lock():  # dentro: si la reunion se acaba de mover (B1c), su ruta nueva
            abs_path = safe(alias_of(b.get("path")), "file")
            data = self._speaker_check(abs_path, h, b.get("creado"))
            if not con or con == h or con not in (data.get("hablantes") or {}):
                raise ApiError("Elige otro hablante de esta reunión.")
            try:
                voices.join_speakers(data, h, con, usuario=(v.user() or {}).get("id"), names=v.names())
            except ValueError as e:
                raise ApiError(str(e))
            voices.write_sidecar(voices.sidecar_path(abs_path), data)
            rel = rel_of(abs_path)
            for k in (h, con):  # la huella en memoria de `con` ya no es la de toda su voz: «Recordar» la recalcula
                (self.jobs.voice_vectors.get(rel) or {}).pop(k, None)
            self.library.at = 0
            self.jobs.schedule_outputs(rel)
            return self.speakers_get({"path": rel})  # dentro: un movimiento que empezara ahora la dejaria a medias

    def voices_export(self, b):
        with self._voices_lock():  # escribe junto al audio: si la reunion se esta moviendo, espera y va a la nueva
            abs_path = safe(alias_of(b.get("path")), "file")
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
        import tempfile
        dest = os.path.join(desktop_dir(), f"{APP_NAME} - instalador.zip")
        vocab, seen, tmp = [], set(), None
        # las de carpetas vinculadas o conectadas (":id/…", ":v<id>/…"), como <raiz>/<proyecto>/Vocabulario/<tema>.txt
        for v in (list_vocab() if b.get("vocab") else []):
            arc = PKG_ROOT + "/" + (v["id"] if not v["id"].startswith(LINK)
                                    else f"{v['project']}/Vocabulario/{v['name']}.txt")
            if arc.lower() in seen:  # dos listas con el mismo proyecto y tema: una sola en el zip (la primera)
                continue
            src = v["_abs"]
            try:
                with open(src, "rb") as f:
                    raw = f.read()
                raw.decode("utf-8")
            except UnicodeDecodeError:  # cp1252 o UTF-16 (de una carpeta conectada): en el zip, en UTF-8
                tmp = tmp or tempfile.mkdtemp(prefix="tenca-listas-")
                src = os.path.join(tmp, f"{len(vocab)}.txt")
                with open(src, "w", encoding="utf-8", newline="") as f:  # sin newline="", Windows dobla los \r\n
                    f.write(_decode_list(raw))
            except OSError:
                continue
            seen.add(arc.lower())
            vocab.append((src, arc))
        try:
            n = build_package(dest, vocab)
        except OSError as e:
            raise ApiError(f"No se pudo crear el paquete: {e}")
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
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
                  "/api/reunion/actual": a.reunion_actual, "/api/calendario/estado": a.calendario_estado,
                  "/api/hoy": a.hoy,
                  "/api/perfil": a.perfil_get,
                  "/api/diagnostico": a.diagnostico, "/api/modelos": a.modelos_state, "/api/probar": a.probar_estado,
                  "/api/agente/estado": a.agente_estado,
                  "/api/aprender": a.aprender_get, "/api/aprender/reglas": a.aprender_reglas,
                  "/api/inicio-windows": lambda q: {"activado": inicio_windows_leer()},
                  "/api/ocultar-captura": lambda q: captura_estado(a.settings.get())}
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
                  "/api/grabar/plan": a.grabar_plan_api, "/api/grabar/recordar": a.grabar_recordar,
                  "/api/hoy/preparar": a.hoy_preparar,
                  "/api/summarize": a.summarize,
                  "/api/favorite": a.favorite, "/api/folder-icon": a.folder_icon, "/api/delete": a.delete,
                  "/api/transcript/save": a.save_transcript, "/api/transcript/restore": a.restore_transcript,
                  "/api/aprender/anadir": a.aprender_anadir, "/api/aprender/no": a.aprender_no,
                  "/api/aprender/deshacer": a.aprender_deshacer, "/api/aprender/olvidar": a.aprender_olvidar,
                  "/api/aprender/regla": a.aprender_regla,
                  "/api/export": a.export_package, "/api/link/pick": a.link_pick, "/api/link/add": a.link_add,
                  "/api/link/remove": a.link_remove, "/api/vocab/save": a.vocab_save,
                  "/api/vocab/delete": a.vocab_delete, "/api/vocab/check": a.vocab_check,
                  "/api/vocab/import": a.vocab_import, "/api/perfil": a.perfil_save,
                  "/api/proyecto/carpeta": a.carpeta_principal, "/api/reunion/destino": a.destino,
                  "/api/vocab/link/add": a.vocab_link_add, "/api/vocab/link/remove": a.vocab_link_remove,
                  "/api/voces/activar": a.voices_toggle, "/api/voces/identificar": a.voices_identify,
                  "/api/voces/persona": a.voices_person, "/api/voces/fusionar": a.voices_merge,
                  "/api/voces/olvidar": a.voices_forget, "/api/voces/reasignar": a.voices_reassign,
                  "/api/hablantes/unir": a.speakers_join,
                  "/api/voces/exportar": a.voices_export,
                  "/api/apagar": a.apagar, "/api/mostrar": a.mostrar, "/api/ocultar": a.ocultar,
                  "/api/acerca/abrir": a.acerca_abrir, "/api/calendario/activar": a.calendario_activar,
                  "/api/inicio-windows": lambda b: {"activado": inicio_windows_poner(b.get("activado") is True)},
                  "/api/ocultar-captura": a.ocultar_captura}
        try:
            u = urlparse(self.path)
            if self.headers.get("Transfer-Encoding"):  # sin Content-Length no se sabe donde acaba el cuerpo (P0-1)
                raise ApiError("Cuerpo no válido: falta Content-Length", 411)
            if u.path == "/api/import":  # el cuerpo es el archivo tal cual (puede ser de 1 GB): se copia a trozos
                r = a.import_file(self, {k: v[0] for k, v in parse_qs(u.query).items()})
                self.body_read = True  # import_file lo ha leido entero (si se corta, lanza ApiError)
                return self._json(r)
            if u.path == "/api/subir":  # igual, pero subir marca body_read en cuanto lo ha leído entero: un audio
                # que no se puede leer se contesta al momento, sin esperar a vaciar un cuerpo que ya no queda
                q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                return self._json(a.subir(self, q))
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


def borrar_pcm_voces(pid):
    """El audio decodificado de un subproceso de voces (voices.main lo borra al acabar, pero uno cancelado o que muere
    no llega a su finally: ~115 MB por hora de audio hasta el siguiente arranque; revisión de fix-subir-memoria)."""
    d = os.path.join(DATA_DIR, "tmp")
    try:
        nombres = [e.name for e in os.scandir(d) if e.name.startswith(f"voces-{pid}-") and e.name.endswith(".pcm")]
    except OSError:
        return
    for n in nombres:
        try:
            os.remove(os.path.join(d, n))
        except OSError:
            pass


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


def _lnk_cadenas(data):
    """Las cadenas de un acceso directo (.lnk, formato MS-SHLLINK): {"workdir", "args", "icon", ...} de su StringData
    (detras de la cabecera de 76 bytes, la lista de IDs y LinkInfo, segun LinkFlags), o None si no es un .lnk que se
    entienda."""
    if len(data) < 76 or data[:4] != b"\x4c\x00\x00\x00":
        return None
    flags = struct.unpack_from("<I", data, 20)[0]
    pos, out = 76, {}
    try:
        if flags & 0x1:  # HasLinkTargetIDList: tamano (2 bytes) y la lista
            pos += 2 + struct.unpack_from("<H", data, pos)[0]
        if flags & 0x2:  # HasLinkInfo: su tamano (4 bytes) lo incluye
            pos += struct.unpack_from("<I", data, pos)[0]
        uni = bool(flags & 0x80)  # IsUnicode
        for bit, key in ((0x4, "name"), (0x8, "relpath"), (0x10, "workdir"), (0x20, "args"), (0x40, "icon")):
            if flags & bit:
                n = struct.unpack_from("<H", data, pos)[0] * (2 if uni else 1)
                raw = data[pos + 2:pos + 2 + n]
                if len(raw) < n:
                    return None
                out[key] = raw.decode("utf-16-le" if uni else "latin-1", "replace")
                pos += 2 + n
    except struct.error:
        return None
    return out


def _lnk_apunta_a(path, carpeta):
    """¿El acceso directo `path` es de esta copia de la app (`carpeta`, su _app)? Lo es si su carpeta de trabajo es
    esa, o si los argumentos («…\\_app\\server.py») o el icono («…\\_app\\static\\…», a veces con
    %USERPROFILE%) llevan una ruta de dentro de ella, en sus cadenas de .lnk (_lnk_cadenas). Si no, se busca en el
    fichero entero (UTF-16LE, en las dos alineaciones) la carpeta seguida de la barra: sin la barra, «…\\_app» casaba
    tambien con «…\\_app2», otra carpeta. Sin mayusculas. Sin poder leerlo, no lo es."""
    try:
        with open(path, "rb") as f:
            data = f.read(1 << 16)
    except OSError:
        return False

    def norm(p):
        p = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m[1], m[0]), p or "")  # %USERPROFILE%… del icono
        return os.path.normcase(os.path.normpath(p.strip().strip('"'))).casefold() if p.strip() else ""

    base = norm(carpeta)
    dentro = os.path.join(base, "")
    cads = _lnk_cadenas(data)
    if cads is not None:
        if norm(cads.get("workdir")) == base:
            return True
        if any(dentro in norm(cads.get(k)) for k in ("args", "icon")):
            return True
    return any(dentro in os.path.normcase(data[i:].decode("utf-16-le", "ignore")).casefold() for i in (0, 1))


def _startup_antiguos(destino):
    """Las copias de «Arrancar con Windows» con el nombre anterior de la app («Chuleta.lnk») que haya en la carpeta
    Inicio de Windows y sean de esta copia (llevan la ruta de su _app): siguen abriendola al entrar y no deben quedar
    junto a la de ahora (se abriria dos veces). instalar.ps1 ya las migra; esto cubre lo que quede (una instalacion que
    no pudo). Un acceso directo con ese nombre que no es de esta copia no se cuenta ni se toca."""
    from common import ARRANQUE_ANTIGUOS
    carpeta = os.path.dirname(destino)
    return [p for p in (os.path.join(carpeta, f"{n}.lnk") for n in ARRANQUE_ANTIGUOS)
            if os.path.isfile(p) and _lnk_apunta_a(p, APP_DIR)]


STARTUP_APPROVED = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\StartupFolder"


def _startup_aprobado_copiar(desde, hacia, clave=STARTUP_APPROVED):
    """Aplicaciones de inicio (Administrador de tareas) guarda si se desactivo un acceso directo de la carpeta Inicio
    por su nombre de fichero (HKCU\\<clave>, REG_BINARY; sin valor = activado). Al pasar «Chuleta.lnk» a «Tenca.lnk» se
    copia su valor, como Copy-ArranqueAprobado del instalador: lo desactivado sigue desactivado aunque la migracion la
    haga la app (un instalador que no pudo). El del nombre viejo se deja. Sin valor o sin registro, nada."""
    if not WINDOWS:
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            v, tipo = winreg.QueryValueEx(k, desde)
            if tipo != winreg.REG_BINARY or not v:
                return
            winreg.SetValueEx(k, hacia, 0, winreg.REG_BINARY, v)
        log(f"arrancar con Windows: {'desactivado' if v[0] == 3 else 'activado'} en Aplicaciones de inicio, como lo "
            f"tenía «{desde[:-4]}»")
    except (ImportError, OSError):
        pass


def _startup_quitar(rutas):
    for p in rutas:
        try:
            os.remove(p)
            log(f"arrancar con Windows: quitado el acceso directo antiguo «{os.path.basename(p)[:-4]}»")
        except OSError as e:
            log(f"   arrancar con Windows: {e}")


def ocultar_al_compartir(st):
    """El ajuste «Ocultar Tenca al compartir pantalla» (por defecto, sí)."""
    return (st or {}).get("ocultar_al_compartir", True) is not False


def captura_estado(st):
    """Para Ajustes: {activado (el ajuste), soportado, motivo (por qué no se puede), aplicado (lo que tiene la ventana
    ahora), error}. Sin la ventana propia (se abrió en Edge, o con --no-browser) no se puede: esa ventana no es de este
    proceso."""
    ok, motivo = window_mod.capture_support()
    out = {"activado": ocultar_al_compartir(st), "soportado": ok, "motivo": motivo, "aplicado": None, "error": None}
    if ok and WINDOW is None:
        out.update(soportado=False, motivo=f"Solo con la ventana propia de {APP_NAME}: ahora se ha abierto en el "
                   "navegador y esa ventana no es suya.")
    elif WINDOW is not None:
        out.update(aplicado=WINDOW.capture_applied, error=WINDOW.capture_error)
    return out


def inicio_windows_leer():
    """«Arrancar con Windows»: True o False; None si no se puede (fuera de Windows, o sin el acceso directo del menu
    Inicio, que es el que se copia: una copia descomprimida sin instalar). Tambien True con una copia que aun lleve un
    nombre anterior de la app: sigue abriendola al entrar en Windows."""
    origen, destino = _startup_lnks()
    if not origen:
        return None
    if os.path.isfile(destino) or _startup_antiguos(destino):
        return True
    return False if os.path.isfile(origen) else None


def inicio_windows_poner(activado):
    """Copia (o quita) el acceso directo del menu Inicio en la carpeta Inicio de Windows (shell:startup): mismo destino,
    iconos y AppUserModelID, sin registro ni permisos de administrador. Las copias con un nombre anterior de la app se
    quitan en los dos casos (nunca dos). Devuelve el estado que queda."""
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
    if activado and not os.path.isfile(destino):
        return inicio_windows_leer()  # sin el de Inicio no hay copia nueva: las antiguas siguen siendo lo unico que hay
    _startup_quitar(_startup_antiguos(destino))
    return inicio_windows_leer()


def inicio_windows_al_dia():
    """Al arrancar: si esta activado y el acceso directo del menu Inicio ha cambiado (lo rehace INSTALAR.bat al
    actualizar), la copia de la carpeta Inicio se pone igual. Si la copia aun lleva un nombre anterior de la app
    («Chuleta.lnk»), pasa a ser «<APP_NAME>.lnk» (sigue activado) y la antigua se quita."""
    origen, destino = _startup_lnks()
    try:
        antiguos = _startup_antiguos(destino) if destino else []
        if origen and os.path.isfile(origen) and (antiguos and not os.path.isfile(destino)):
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(origen, destino)
            log(f"arrancar con Windows: sigue activado, ahora como «{APP_NAME}»")
            _startup_aprobado_copiar(os.path.basename(antiguos[0]), os.path.basename(destino))
        if origen and os.path.isfile(destino) and os.path.isfile(origen):
            with open(origen, "rb") as a, open(destino, "rb") as b:
                if a.read() != b.read():
                    shutil.copy2(origen, destino)
                    log("arrancar con Windows: acceso directo actualizado")
        if antiguos and os.path.isfile(destino):
            _startup_quitar(antiguos)
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
    ico = os.path.join(STATIC_DIR, "tenca.ico")
    # ocultarse al compartir pantalla: lo lee del ajuste al abrir la ventana (y Ajustes lo cambia al momento)
    WINDOW = None if no_browser else Window(ui_url(), DATA_DIR, ico,
                                            hide_from_capture=lambda: ocultar_al_compartir(Handler.app.settings.get()),
                                            tray=make_tray(ico))
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
