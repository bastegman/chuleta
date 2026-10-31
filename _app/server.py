# -*- coding: utf-8 -*-
"""
Notas: app local para ordenar, escuchar, leer y transcribir las grabaciones de reuniones.

  - Servidor HTTP de la libreria estandar en http://127.0.0.1:8765 (solo accesible desde este PC)
  - Interfaz en _app/static (HTML + JS sin compilar)
  - Transcripcion local (faster-whisper) o en la nube (engines.py)
  - Las transcripciones se guardan como siempre: <audio>.txt y <audio>.srt junto al audio

Arranque: acceso directo Notas del escritorio (lanza _app/Notas.bat).
"""
import copy
import hashlib
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import engines
import live
import voices
from common import (ApiError, BAD_NAME, LOGGER, SRT_TIME, WINDOWS, clean_name,  # noqa: F401 (re-exportados)
                    default_threads, fmt_srt, fold, is_protected, log, log_exc, natural_key, parse_srt, protect,
                    read_text, save_text, setup_logging, unprotect, wav_duration, write_bytes)
from recording import GlobalHotkey, RecorderProcess, cue, mic_meeting_app, notify

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
ROOT = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AudiosApp")
HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}/"

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


def code_mtime():
    """Fecha del codigo: cambia si se actualiza la app (la ventana abierta lo ve y se recarga sola)."""
    files = [os.path.join(APP_DIR, n) for n in os.listdir(APP_DIR) if n.endswith(".py")]
    files += [os.path.join(STATIC_DIR, n) for n in os.listdir(STATIC_DIR)]
    return max(os.path.getmtime(f) for f in files)


BUILD = format(int(code_mtime()), "x")
VERSION = time.strftime("%d/%m/%Y %H:%M", time.localtime(code_mtime()))  # se ve en Ajustes


# ----------------------------------------------------------------------------- utilidades

# Carpetas vinculadas: cualquier carpeta del PC (p. ej. la del proyecto en OneDrive) que se usa como
# si estuviera dentro de Notas. Sus rutas internas empiezan por ":<id>" (":" no puede ir en un nombre
# de archivo de Windows, asi que nunca choca con una carpeta de verdad).
LINK = ":"
LINKS = {}  # id -> {"name", "path"} (se cargan de los ajustes al arrancar)


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
    """Ruta absoluta dentro de la carpeta de Notas o de una carpeta vinculada (no deja salir de ahi)."""
    rel = (rel or "").replace("\\", "/").strip("/")
    base = ROOT
    if rel.startswith(LINK):
        lid, _, rel = rel[len(LINK):].partition("/")
        if lid not in LINKS:
            raise ApiError("Esa carpeta ya no está vinculada a Notas", 404)
        base = LINKS[lid]["path"]
    p = os.path.normpath(os.path.join(base, rel))
    if not _inside(p, base):
        raise ApiError("Ruta fuera de las carpetas de Notas", 403)
    if must == "file" and not os.path.isfile(p):
        raise ApiError("No existe el archivo: " + rel, 404)
    if must == "dir" and not os.path.isdir(p):
        raise ApiError("No existe la carpeta: " + rel, 404)
    return p


def in_app_dir(p):
    a = os.path.normcase(APP_DIR)
    return os.path.normcase(p) == a or os.path.normcase(p).startswith(a + os.sep)


# ----------------------------------------------------------------------------- paquete para otra persona

EXPORT_DIR = os.path.join(APP_DIR, "export")  # INSTALAR.bat, ABRIR NOTAS.bat y LEEME.txt (van a la raiz)


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


def build_package(dest, extra=()):
    """Zip con la app lista para instalar en otro PC. Lleva solo el codigo, el instalador, el LEEME y la licencia:
    ni grabaciones, ni notas, ni ajustes, ni la API key (eso vive en DATA_DIR, que no se toca).
    extra: [(archivo, ruta en el zip)] opcional, p. ej. las listas de vocabulario por workstream."""
    import zipfile

    def text(path, keep_marker=False):  # .bat/.ps1 con CRLF; BOM en .ps1 (PowerShell 5 lo leeria como ANSI) y .txt
        t = read_text(path).replace("\r\n", "\n").replace("\n", "\r\n")
        if not keep_marker:
            t = t.replace("{FECHA}", time.strftime("%d/%m/%Y"))
        return (("\ufeff" if path.endswith((".ps1", ".txt")) else "") + t).encode("utf-8")

    files = [(os.path.join(APP_DIR, n), "Notas/_app/" + n) for n in export_code()]
    files += [(e.path, "Notas/_app/static/" + e.name) for e in os.scandir(STATIC_DIR) if e.is_file()]
    templates = [e for e in os.scandir(EXPORT_DIR) if e.is_file()]
    files += [(e.path, "Notas/" + e.name) for e in templates]
    # las plantillas van tambien dentro de _app/export (con su {FECHA}): la copia instalada podra volver a exportar
    files += [(e.path, "Notas/_app/export/" + e.name) for e in templates]
    lic = os.path.join(os.path.dirname(APP_DIR), "LICENSE")  # MIT: el aviso acompana a cada copia
    if os.path.isfile(lic):
        files.append((lic, "Notas/LICENSE"))
    files += list(extra)
    tmp = dest + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in sorted(files, key=lambda f: f[1]):
            if src.endswith((".bat", ".ps1", ".txt")):
                z.writestr(arc, text(src, keep_marker=arc.startswith("Notas/_app/export/")))
            else:
                z.write(src, arc)
    os.replace(tmp, dest)
    return len(files)


# ----------------------------------------------------------------------------- estado persistente

class Store:
    """JSON en %LOCALAPPDATA%\\AudiosApp (fuera de OneDrive: la API key no se sincroniza).
    Los SECRETS se guardan cifrados con DPAPI (common.protect); en memoria y en get() van en claro. Un fichero
    antiguo con la clave en claro se lee igual y queda cifrado en el primer guardado (migracion transparente).
    Si no se puede descifrar (otro usuario de Windows, otro PC), la clave queda vacia y se avisa en el log."""

    SECRETS = (("cloud", "api_key"), ("cloud", "extra_keys"))  # un texto o una lista de textos

    def __init__(self, path, defaults):
        self.path, self.lock = path, threading.Lock()
        self.data = copy.deepcopy(defaults)
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        except (OSError, ValueError):
            pass
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
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
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
    "rec_folder": "",         # ultima carpeta donde se grabo (sin workstream)
    "project_folders": {},    # proyecto -> carpeta donde se graban sus reuniones (la ultima elegida)
    "favorites": [],          # [{"kind": "a"|"f", "path": ...}] (estilo Notion)
    "folder_icons": {},       # carpeta -> emoji
    "meeting_detect": True,   # avisar si Teams/Zoom/… usa el micro y no se esta grabando
    "summary_auto": True,     # resumen con IA al terminar de transcribir una reunion reciente
    "summary_model": "openai/gpt-oss-120b",
    "summary_lang": "es",     # es / en / same (el de la reunion)
    "summary_extra": "",     # instrucciones extra para el resumen
    "voices_enabled": False,  # quien habla (diarizacion local + huellas de voz): opcional, apagado por defecto
    # nube: whisper-large-v3 (el mas preciso; -turbo es mas rapido y algo menos fino)
    "cloud": {"provider": "groq", "api_key": "", "model": "whisper-large-v3", "base_url": "",
              "azure_endpoint": "", "azure_deployment": "", "azure_api_version": "2024-06-01",
              "chunk_minutes": 10, "parallel": 3,
              # directo en la nube: modelo rapido por trozo y claves de otras cuentas de Groq (rotacion de cupo)
              "live_model": engines.LIVE_MODEL_DEFAULT, "extra_keys": []},
    "speed": {},
}


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

def scan():
    """Arbol de carpetas con audios (+ estado de transcripcion) y documentos sueltos."""
    flat = []

    def walk(abs_dir, rel, depth=0, max_depth=99):
        node = {"name": os.path.basename(abs_dir) if rel else "Notas", "path": rel,
                "folders": [], "audios": [], "docs": []}
        try:
            entries = list(os.scandir(abs_dir))
        except OSError:
            return node
        files = {}
        for e in entries:
            if e.name.startswith((".", "~$")):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    # las carpetas Vocabulario no son reuniones ni documentos: las lee list_vocab
                    if not in_app_dir(e.path) and depth < max_depth and fold(e.name) != VOCAB_FOLDER:
                        node["folders"].append(walk(e.path, f"{rel}/{e.name}" if rel else e.name, depth + 1, max_depth))
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
    def __init__(self, durations):
        self.durations = durations
        self.lock = threading.Lock()
        self.tree, self.flat, self.version, self.at = None, [], "", 0
        self.pending_durations = False
        self.growth = {}  # ruta -> (tamano, cuando cambio): OBS no actualiza la fecha mientras graba
        self.ends = {}    # .srt -> (fecha, segundo en que acaba): para detectar transcripciones a medias
        self.notes = {}   # .md -> (fecha, propiedades, tiene texto)
        self.speakers = {}  # .hablantes.json -> (fecha, resumen {n, known, doubt, tu} o None si solo lleva el micro)

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
            self.growth[a["path"]] = (a["size"], time.time())
        a["recording"] = time.time() - self.growth[a["path"]][1] < live.STALLED_S
        return round(max(0, a["size"] - info["data_off"]) / info["byte_rate"], 1)

    def refresh(self, max_age=0.0):
        with self.lock:
            if self.tree is not None and time.time() - self.at < max_age:
                return self.tree, self.version
            tree, flat = scan()
            missing = False
            for a in flat:
                a["recording"] = False
                a["tx_end"] = self._tx_end(a)
                a["props"], a["has_notes"] = self._props(a)
                a["speakers"] = self._speakers(a)
                a["has_speakers"] = bool(a["speakers"])
                open_dur = self._recording(a)
                if open_dur is not None:  # sin cachear: cambia cada pocos segundos
                    a["duration"] = open_dur
                    a["partial"] = False
                    continue
                # duraciones: las rapidas (cabecera WAV) ya; las demas (PyAV) en segundo plano
                quick = a["ext"] == ".wav" or a["cloud"]
                a["duration"] = self.durations.get(a["_abs"], a["path"], a["size"], a["mtime"], a["cloud"],
                                                   a["_srt"], compute=quick)
                missing |= a["duration"] is None and not quick
                a["partial"] = is_partial(a)
            self.durations.flush()
            payload = json.dumps(strip_private(tree), ensure_ascii=False, sort_keys=True)
            self.tree, self.flat, self.at = tree, flat, time.time()
            self.version = hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]
        if missing and not self.pending_durations:
            self.pending_durations = True
            threading.Thread(target=self._fill_durations, daemon=True).start()
        return tree, self.version

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


def is_partial(a):
    """Transcripcion que se quedo a medias: el .srt acaba bastante antes que el audio."""
    d, end = a.get("duration"), a.get("tx_end")
    return bool(a.get("srt") and d and end is not None and not a.get("recording") and d - end > max(60, 0.05 * d)
                and not (COMPLETE and a["path"] in COMPLETE))


def strip_private(node):
    extra = {k: node[k] for k in ("linked", "missing") if k in node}  # carpetas vinculadas (fuera de Audios)
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
NOTE_KEYS = ("titulo", "icono", "etiquetas", "estado", "participantes", "proyecto", "workstream")


def parse_notes(text):
    lines = text.replace("\r\n", "\n").split("\n")
    props, body = {}, text
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        # solo es cabecera si todo lo de dentro es "clave: valor" conocido (si no, era un separador)
        if end and all(not l.strip() or l.partition(":")[0].strip().lower() in NOTE_KEYS for l in lines[1:end]):
            for line in lines[1:end]:
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip()
                if k in NOTE_KEYS and v:
                    props[k] = [t.strip() for t in v.strip("[]").split(",") if t.strip()] if k == "etiquetas" else v
            body = "\n".join(lines[end + 1:]).lstrip("\n")
    return props, body


def dump_notes(props, body):
    head = []
    for k in NOTE_KEYS:
        v = props.get(k)
        if v:
            v = "[" + ", ".join(v) + "]" if k == "etiquetas" else " ".join(str(v).split())
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

    top_names = {norm(ROOT): "Notas", **{norm(l["path"]): l["name"] for l in LINKS.values()}}

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


def find_vocab(project, name, id_=None):
    vs = list_vocab()
    if id_:
        return next((v for v in vs if v["id"] == id_), None)
    if not name:
        return None
    same = [v for v in vs if fold(v["name"]) == fold(name)]
    return next((v for v in same if fold(v["project"]) == fold(project or "")), same[0] if len(same) == 1 else None)


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


def vocab_for(abs_audio, st):
    """Vocabulario para Whisper: el del workstream de la reunion o, si no tiene, el general de Ajustes."""
    md = os.path.splitext(abs_audio)[0] + ".md"
    try:
        props = parse_notes(read_text(md))[0] if os.path.isfile(md) else {}
        v = find_vocab(props.get("proyecto"), props.get("workstream"))
        if v:
            return ", ".join(read_vocab(v["_abs"])[1])
    except OSError:
        pass
    return st.get("prompt") or ""


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
        return d


class Jobs:
    def __init__(self, settings, library):
        self.settings, self.library = settings, library
        self.list, self.lock = [], threading.Lock()
        self.claimed = set()  # grabaciones de la propia app: el directo lo arranca quien graba, no el vigilante
        self.queues = {"local": queue.Queue(), "cloud": queue.Queue()}
        # Whisper corre en procesos aparte (worker.py): uno para la cola y otro para el directo,
        # cada uno con su modelo en memoria
        self.local = engines.LocalEngine(DATA_DIR, "batch")
        self.live_worker = engines.WorkerClient("live", DATA_DIR)
        self.cloud = engines.CloudEngine(os.path.join(DATA_DIR, "tmp"), log=log)
        self.summarizer = engines.Summarizer(keys=self.cloud.keys)  # en la nube (Groq); mismas claves y cupo
        self.queues["resumen"] = queue.Queue()
        self.queues["voces"] = queue.Queue()  # quien habla: un subproceso de prioridad baja, de uno en uno
        self._voices, self.voice_vectors = None, {}  # voces.json (se abre al primer uso) y huellas por reunion
        for kind in self.queues:
            threading.Thread(target=self._worker, args=(kind,), daemon=True, name=f"worker-{kind}").start()
        threading.Thread(target=self._watch_recordings, daemon=True, name="live-watch").start()

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

    # --- en directo
    def _watch_recordings(self):
        """Arranca la transcripcion en directo en cuanto aparece una grabacion de OBS en curso."""
        while True:
            time.sleep(3)
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
        if preset == "nube":
            try:
                engines.CloudEngine.endpoint(st["cloud"])
            except engines.EngineError:
                preset = "medium"  # sin API key: en local
        abs_path = safe(rel, "file")
        if self.active_for(rel):
            raise ApiError("Ese audio ya se está transcribiendo.")
        job = Job(rel, os.path.basename(rel), preset, st.get("language") or None)
        job.engine, job.live, job.recording = "live", True, True
        job.status, job.started, job.stage = "running", time.time(), "Empezando en directo…"
        COMPLETE.set(rel, False)  # hasta que termine bien, no se da por completa
        with self.lock:
            self.list.append(job)
        threading.Thread(target=self._run_live, args=(job, abs_path, st), daemon=True, name="live").start()
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
        j = Job(job.path, job.name, "nube", job.language)
        j.final_pass, j.stage = True, "En cola: repaso final en la nube"
        with self.lock:
            self.list.append(j)
        self.queues[j.engine].put(j)
        log(f"↻ {job.name}  repaso final de la grabación entera en la nube")
        return True

    def refresh_prompt(self, rel):
        """Cambio el workstream de una reunion: si se esta transcribiendo en directo, nuevo vocabulario ya."""
        with self.lock:
            jobs = [j for j in self.list if j.path == rel and j.live and j.status == "running" and j.opts]
        for j in jobs:
            j.opts["prompt"] = vocab_for(safe(rel), self.settings.get())
            log(f"   directo {j.name}: vocabulario actualizado")

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
            self._cloud_ready()

    def set_live_preset(self, job_id, preset, restart=False):
        """Cambia el modelo del directo: desde el siguiente trozo, o rehaciendo todo desde el principio."""
        job = self._live_job(job_id)
        if preset:
            self._check_live_preset(preset)
            job.preset = preset
            self.settings.update(lambda d: d.update(live_preset=preset))
        if restart:
            job.restart = True
            job.stage = "Rehaciendo desde el principio…"
        log(f"   directo {job.name}: {'rehacer con' if restart else 'modelo'} {job.preset}")
        return job.public()

    def _run_live(self, job, abs_path, st):
        resume = []
        srt = os.path.splitext(abs_path)[0] + ".srt"
        if os.path.isfile(srt):  # la app se reinicio a mitad de grabacion: se retoma, no se repite
            try:
                resume = [(s["start"], s["end"], s["text"]) for s in parse_srt(read_text(srt))]
            except OSError:
                resume = []
        opts = {"preset": job.preset, "language": job.language, "prompt": vocab_for(abs_path, st),
                "threads": int(st.get("threads") or default_threads()),
                "worker": self.live_worker, "cloud": self.cloud, "cloud_cfg": st["cloud"],
                "tmp_dir": os.path.join(DATA_DIR, "tmp"), "resume": resume, "summarizer": self.summarizer}
        job.opts = opts  # live.run lee el prompt en cada trozo: si cambias el workstream, vale desde el siguiente
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
            if segs:
                writer(segs)
                if job.edits:  # lo que transcribio Whisper, para poder "Restaurar original" despues
                    save_original(job.path, segs)
                COMPLETE.set(job.path)  # llego hasta el final (si se corta antes, queda "a medias")
                if not self._final_pass(job):  # si hay repaso final, el resumen se hace al acabar este
                    self._after_transcribe(job.path)
                job.status, job.stage, job.progress = "done", "Hecho", 1.0
                log(f"✔ {job.name}  directo terminado ({job.recorded / 60:.0f} min grabados)")
            else:
                job.status, job.stage, job.error = "error", "Error", "No se ha detectado voz en la grabación."
        except engines.Cancelled:
            job.status, job.stage = "cancelled", "Directo detenido"
            log(f"■ {job.name}  directo detenido")
        except Exception as e:
            job.status, job.stage, job.error = "error", "Error", str(e) or e.__class__.__name__
            log(f"✖ {job.name}  {job.error}")
            log_exc()
        finally:
            job.recording = False
            job.finished = time.time()
            self.library.at = 0

    def submit(self, paths, preset, language, resume=False):
        """resume: las que se quedaron a medias se continuan desde donde acaba el texto (el resto, enteras)."""
        if preset not in engines.PRESETS_BY_ID:
            raise ApiError("Velocidad desconocida: " + str(preset))
        if engines.PRESETS_BY_ID[preset]["engine"] == "cloud":
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
            if resume and a and a.get("partial"):
                job.resume_from = a["tx_end"]
            with self.lock:
                self.list.append(job)
            self.queues[job.engine].put(job)
            added.append(job.id)
        return added

    # --- resumen con IA (Groq)
    def summary_ready(self):
        c = self.settings.get()["cloud"]
        return bool(engines.chat_url(c)) and bool((c.get("api_key") or "").strip())

    def submit_summary(self, rel, auto=False):
        abs_path = safe(rel, "file")
        if not self.summary_ready():
            raise ApiError("El resumen necesita una API key de la nube (Ajustes ⚙ → Transcripción en la nube).")
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

    def auto_summary(self, rel):
        """Al terminar de transcribir: resumen automatico si es una reunion reciente (las antiguas se
        resumen a mano, para no gastar de golpe el cupo diario de Groq)."""
        st = self.settings.get()
        if not st.get("summary_auto", True) or not self.summary_ready():
            return
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
        try:
            self.submit_summary(rel, auto=True)
        except ApiError:
            pass

    def _run_summary(self, job):
        job.status, job.started, job.stage = "running", time.time(), "Preparando el resumen…"
        try:
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
                    "participants": props.get("participantes", ""), "notes": notes,
                    "extra": st.get("summary_extra", "")}
            segments = self._with_speakers(abs_path, data["segments"], meta)
            cfg = dict(st["cloud"], summary_model=st.get("summary_model"), summary_lang=st.get("summary_lang"))

            def report(frac=None, stage=None, **k):
                if stage:
                    job.stage = stage
                if frac is not None:
                    job.progress = max(job.progress, frac)

            text, model = self.summarizer.summarize(segments, meta, cfg, report, lambda: job.cancel)
            head = f"---\nmodelo: {model}\ngenerado: {time.strftime('%Y-%m-%d %H:%M')}\n---\n\n"
            save_text(base + ".resumen.md", head + text.strip() + "\n", "utf-8")
            job.status, job.progress, job.stage = "done", 1.0, "Resumen listo"
            log(f"✔ {job.name}  resumen listo ({(time.time() - job.started):.0f} s, {model})")
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
        try:
            engines.CloudEngine.endpoint(self.settings.get()["cloud"])
            return True
        except engines.EngineError as e:
            raise ApiError(str(e))

    # --- quien habla (voces): subproceso de prioridad baja al terminar cada transcripcion
    def voices_on(self):
        return voices.enabled(self.settings.get())

    def _after_transcribe(self, rel):
        """Transcripcion terminada: primero las voces (si estan activas) y el resumen al acabar estas, para que
        lleve los nombres; si no, el resumen directamente."""
        if not self.auto_voices(rel):
            self.auto_summary(rel)

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
        for h, info in data.get("hablantes", {}).items():
            info["nombre"] = names.get(info.get("persona")) if info.get("persona") else None
            info["label"] = voices.label(h, data, names)
            info["usuario"] = data.get("tu") == h
            if info.get("persona") and info["persona"] not in names:  # persona olvidada
                info["persona"], info["nombre"], info["provisional"] = None, None, False
                info["label"] = voices.label(h, data, names)
        data["_who"] = who
        return data

    def _with_speakers(self, abs_path, segments, meta):
        """Para el resumen: cada frase con quien la dice (nombre, «Yo» o «Persona N») si hay diarizacion."""
        data = self.speakers_for(abs_path)
        if not data or not data.get("_who"):
            return segments
        names = self.voices.names()
        out = [dict(s, who=voices.label(w, data, names, for_llm=True)) for s, w in zip(segments, data["_who"])]
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
            res = self._voices_subprocess(job, ["--diarize", abs_path, "--out", side])
            huellas = res.get("huellas") or {}
            data = voices.read_sidecar(side)
            if not data or not data.get("tramos"):
                raise engines.EngineError("No se ha detectado voz en la grabación.")
            voices.identify(data, huellas, self.voices)
            srt = Transcripts().load(os.path.splitext(abs_path)[0])
            if srt and srt["kind"] == "srt":
                voices.refresh_frases(data, srt["segments"])
            voices.write_sidecar(side, data)
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
            self.library.at = 0
            if job.then_summary:
                self.auto_summary(job.path)

    def cancel(self, job_id):
        with self.lock:
            for j in self.list:
                if j.id == job_id and j.status in ("queued", "running"):
                    j.cancel = True
                    if j.status == "queued":
                        j.status, j.stage, j.finished = "cancelled", "Cancelado", time.time()
                    else:
                        j.stage = "Cancelando…"
                    return True
        return False

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
            vocab = vocab_for(abs_path, st)  # el del workstream de la reunion, o el general
            if preset["engine"] == "local":
                segs, lang, dur = self.local.transcribe(abs_path, preset, job.language, vocab,
                                                        int(st.get("threads") or default_threads()),
                                                        report, lambda: job.cancel, start=start)
            else:
                segs, lang, dur = self.cloud.transcribe(abs_path, job.duration, st["cloud"], job.language,
                                                        vocab, job.id, report, lambda: job.cancel,
                                                        start=start)
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
            COMPLETE.set(job.path)  # aunque el texto acabe antes que el audio, esta completa
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
        except Exception as e:
            job.status, job.stage, job.error = "error", "Error", str(e) or e.__class__.__name__
            log(f"✖ {job.name}  {job.error}")
            if not isinstance(e, (engines.EngineError, ApiError)):
                log_exc()
            if job.final_pass:  # se queda el texto del directo, y con el se resume
                log("   se mantiene el texto del directo")
                self.auto_summary(job.path)
        finally:
            job.finished = time.time()
            self.library.at = 0


# ----------------------------------------------------------------------------- API


def masked(key):
    return (key[:4] + "…" + key[-4:]) if key and len(key) > 12 else ("••••" if key else "")


class App:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        global COMPLETE
        COMPLETE = Complete(os.path.join(DATA_DIR, "completas.json"))
        self.settings = Store(os.path.join(DATA_DIR, "settings.json"), DEFAULT_SETTINGS)
        self._load_links()
        self.library = Library(Durations(os.path.join(DATA_DIR, "durations.json")))
        self.transcripts = Transcripts()
        self.jobs = Jobs(self.settings, self.library)
        # grabadora propia: una grabacion y, mientras la ventana de "Grabar" esta abierta, un medidor
        self.rec, self.monitor, self.monitor_seen = None, None, 0.0
        self.rec_lock, self.devices_cache = threading.Lock(), (0.0, None)
        self.toggle_lock = threading.Lock()
        self.notes_lock, self.meeting = threading.Lock(), None
        self.voices_dl = None  # descarga de los modelos de voces en curso (progreso para la interfaz)
        threading.Thread(target=self._meeting_watch, daemon=True, name="meeting-watch").start()
        threading.Thread(target=self._monitor_watchdog, daemon=True).start()
        self.hotkey = GlobalHotkey(lambda: self.rec_toggle({"source": "hotkey"}))
        self.hotkey.start()

    def rec_toggle(self, b):
        """Ctrl+Alt+R (o el mismo atajo dentro de la app): empieza con la ultima configuracion o para."""
        with self.toggle_lock:
            try:
                if self.rec and self.rec.alive():
                    r = self.rec_stop({})
                    cue("stop")
                    notify("■ Grabación guardada",
                           f"{max(1, round(r['seconds'] / 60))} min · la transcripción termina sola")
                    return dict(r, recording=False)
                st = self.settings.get()
                # el ultimo workstream elegido (y se graba en la carpeta de su proyecto)
                v = find_vocab(None, None, st.get("rec_vocab")) if st.get("rec_vocab") else None
                cue("start")  # antes de empezar: asi el pitido no queda grabado
                r = self.rec_start({"mic": st.get("rec_mic") or "default", "sys": st.get("rec_sys") or "all",
                                    "live": True, "preset": st.get("live_preset") or "medium",
                                    "language": st.get("language") or "", "folder": self.rec_folder_for(v, st),
                                    "vocab": v["id"] if v else ""})
                notify("● Grabando en Notas", (f"{r['workstream']} · " if r.get("workstream") else "")
                       + "Pulsa Ctrl+Alt+R otra vez para parar")
                return dict(r, recording=True)
            except ApiError as e:
                cue("error")
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
            folder = safe(b.get("folder", ""), "dir")
            if in_app_dir(folder):
                raise ApiError("No se puede grabar ahí")
            name = time.strftime("%Y-%m-%d %H-%M-%S") + (f" - {clean_name(b['name'])}" if b.get("name") else "")
            path = os.path.join(folder, name + ".wav")
            if os.path.exists(path):
                raise ApiError("Ya existe un archivo con ese nombre.")
            if self.monitor:
                self.monitor.stop(timeout=2)
                self.monitor = None
            rel = rel_of(path)
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
                preset = b.get("preset") or self.settings.get().get("live_preset") or "medium"
                v = find_vocab(None, None, b["vocab"]) if b.get("vocab") else None
                if v:  # antes de arrancar el directo, para que ya use ese vocabulario
                    self.notes_save({"path": rel, "props": {"proyecto": v["project"], "workstream": v["name"]}})

                def upd(d):
                    if "vocab" in b:
                        d["rec_vocab"] = v["id"] if v else ""
                    # la proxima vez (y con Ctrl+Alt+R) se graba en la misma carpeta
                    d["rec_folder"] = rel_of(folder)
                    if v:
                        d["project_folders"] = dict(d.get("project_folders") or {}, **{v["project"]: rel_of(folder)})
                    # "solo grabar" y "sin micro" son para una grabacion concreta: no se quedan como
                    # predeterminado (la siguiente, y Ctrl+Alt+R, vuelven a transcribir y a grabar el micro)
                    if mic != "none":
                        d["rec_mic"] = mic
                    d.update(rec_sys=sys_dev, live_preset=preset if preset in LIVE_PRESETS else "medium")
                    if "language" in b:  # el directo usa el idioma guardado
                        d["language"] = b.get("language") or ""
                self.settings.update(upd)
                self.library.at = 0
                if b.get("live"):
                    self.jobs.start_live(rel, preset)
                started = True
            finally:
                if not started:
                    self.jobs.claimed.discard(rel)
        log(f"● Grabando: {rel}" + (f" [{v['project']} · {v['name']}]" if v else "")
            + ("" if b.get("live") else " (solo grabar, sin transcribir)"))
        return {"path": rel, "workstream": f"{v['project']} · {v['name']}" if v else None}

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
        return {"path": rel_of(r.path), "seconds": secs}

    # --- GET
    def config(self, q):
        st = self.settings.get()
        presets = []
        for p in engines.PRESETS:
            d = {k: v for k, v in p.items()}
            d["speed"] = st["speed"].get(p["id"], p["speed"])
            d["cached"] = p["engine"] == "cloud" or engines.model_cached(p["model"])
            presets.append(d)
        cloud = dict(st["cloud"])
        cloud["has_key"] = bool(cloud.get("api_key"))
        cloud["api_key"] = masked(cloud.get("api_key"))
        cloud["extra_keys"] = [masked(k) for k in (cloud.get("extra_keys") or []) if k]  # nunca en claro
        cloud["quota"] = self.jobs.cloud.keys.public(st["cloud"]) if cloud["has_key"] else None
        return {"root": ROOT, "version": VERSION, "presets": presets, "providers": engines.PROVIDERS, "cloud": cloud,
                "preset": st["preset"], "language": st["language"], "threads": st["threads"],
                "prompt": st["prompt"], "cpu_count": os.cpu_count(),
                "live_auto": bool(st.get("live_auto")), "live_preset": st.get("live_preset") or "medium",
                "live_final_pass": bool(st.get("live_final_pass", True)),
                "hotkey": {"ok": self.hotkey.ok, "error": self.hotkey.error, "keys": "Ctrl+Alt+R"},
                "favorites": st.get("favorites") or [], "folder_icons": st.get("folder_icons") or {},
                "links": [{"rel": LINK + k, "name": l["name"], "path": l["path"]} for k, l in LINKS.items()],
                "rec_folder": st.get("rec_folder") or "", "project_folders": st.get("project_folders") or {},
                "meeting_detect": bool(st.get("meeting_detect", True)),
                "summary": {"auto": bool(st.get("summary_auto", True)), "model": st.get("summary_model"),
                            "lang": st.get("summary_lang") or "es", "extra": st.get("summary_extra") or "",
                            "models": engines.SUMMARY_MODELS, "ready": self.jobs.summary_ready()},
                "voices": {"enabled": bool(st.get("voices_enabled")), "models_ready": voices.models_ready(DATA_DIR)},
                "lost_secrets": list(getattr(self.settings, "lost", []))}

    def tree(self, q):
        tree, version = self.library.refresh(max_age=1.0)
        if q.get("v") == version:
            return {"version": version, "same": True}
        return {"version": version, "tree": strip_private(tree)}

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
        return {"props": props, "body": body, "mtime": os.path.getmtime(md)}

    def notes_save(self, b):
        """Guarda las notas y/o propiedades (solo lo que venga en la peticion)."""
        abs_path = safe(b.get("path"), "file")
        md = os.path.splitext(abs_path)[0] + ".md"
        with self.notes_lock:
            props, body = parse_notes(read_text(md)) if os.path.isfile(md) else ({}, "")
            for k, v in (b.get("props") or {}).items():
                if k in NOTE_KEYS:
                    if k == "etiquetas":
                        v = [" ".join(str(t).split()) for t in (v or []) if str(t).strip()]
                    props[k] = v
            if "body" in b:
                body = b.get("body") or ""
            text = dump_notes(props, body)
            if text:
                save_text(md, text, "utf-8")
            elif os.path.isfile(md):
                os.remove(md)  # sin notas ni propiedades: no se deja un archivo vacio
        if {"proyecto", "workstream"} & set(b.get("props") or {}):
            self.jobs.refresh_prompt(rel_of(abs_path))
        self.library.at = 0
        return {"props": props, "mtime": os.path.getmtime(md) if os.path.isfile(md) else 0}

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
        """Abre el selector de carpetas de Windows (en este PC) y devuelve la que elijas."""
        script = ("import sys, tkinter as tk\nfrom tkinter import filedialog\n"
                  "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
                  "p = filedialog.askdirectory(parent=r, title=sys.argv[1], mustexist=True,"
                  " initialdir=sys.argv[2] or None)\nsys.stdout.write(p or '')\n")
        start = os.path.dirname(ROOT) if not LINKS else os.path.dirname(next(iter(LINKS.values()))["path"])
        try:
            r = subprocess.run([sys.executable, "-c", script, "Carpeta donde guardar las grabaciones", start],
                               capture_output=True, timeout=900, creationflags=engines.NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as e:
            raise ApiError(f"No se pudo abrir el selector de carpetas ({e.__class__.__name__}); pega la ruta a mano.")
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
            raise ApiError("Esa carpeta ya está dentro de Notas: no hace falta vincularla.")
        if _inside(ROOT, path) or _inside(os.path.expanduser("~"), path):
            raise ApiError("Esa carpeta es demasiado grande (contiene la de Notas o tu usuario): "
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
        """Deja de mostrarla en Notas. No borra ni mueve nada de la carpeta."""
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
                             if not v.startswith(LINK + lid)}))
        log(f"🔗 carpeta desvinculada (no se ha tocado nada): {l['path']}")
        return {"ok": True}

    def rec_folder_for(self, v, st):
        """Donde se graba: la ultima carpeta usada para ese proyecto o, si no hay, la ultima usada."""
        for rel in ((st.get("project_folders") or {}).get(v["project"]) if v else None, st.get("rec_folder")):
            if rel is None:
                continue
            try:
                safe(rel, "dir")
                return rel
            except ApiError:
                continue
        return ""

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
        # proyecto que ya existe: en su carpeta de vocabulario; si es nuevo, Notas/<proyecto>/Vocabulario
        vdir = os.path.dirname(same["_abs"]) if same else os.path.join(ROOT, project, "Vocabulario")
        path = os.path.join(vdir, name + ".txt")
        moved = old and os.path.normcase(old["_abs"]) != os.path.normcase(path)
        if (not old or moved) and os.path.exists(path):
            raise ApiError(f"Ya hay un tema «{name}» en {project}.")
        os.makedirs(vdir, exist_ok=True)
        save_text(path, (f"# {desc}\n" if desc else "") + ", ".join(terms) + "\n", "utf-8")
        if moved:  # se ha renombrado (o cambiado de proyecto): fuera el archivo anterior
            os.remove(old["_abs"])
        self.library.at = 0
        log(f"🔤 vocabulario guardado: {project} · {name} ({len(terms)} términos)")
        return {"id": rel_of(path)}

    def vocab_delete(self, b):
        v = find_vocab(None, None, b.get("id"))
        if not v:
            raise ApiError("Ese tema ya no existe.")
        to_recycle_bin([v["_abs"]])  # a la papelera: se puede recuperar
        self.settings.update(lambda d: d.update(rec_vocab="" if d.get("rec_vocab") == v["id"]
                                                else d.get("rec_vocab", "")))
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
        if ext.lower() in AUDIO_EXT:
            group += [stem + e for e in SIDE_EXTS if os.path.isfile(stem + e)]
        for attempt in range(8):  # el reproductor puede tener el audio abierto un instante
            try:
                to_recycle_bin(group)
                break
            except ApiError:
                if attempt == 7:
                    raise
                time.sleep(0.3)
        COMPLETE.set(rel, False)
        shutil.rmtree(backup_dir(rel), ignore_errors=True)
        self.jobs.voice_vectors.pop(rel, None)
        self.settings.update(lambda d: d.update(favorites=[f for f in d.get("favorites", []) if f["path"] != rel]))
        self.library.at = 0
        log(f"🗑 {rel} → papelera ({len(group)} archivos)")
        return {"ok": True, "count": len(group)}

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
                app = mic_meeting_app()
                self.meeting = app
                recording = bool(self.rec and self.rec.alive())
                if app and not recording and notified != app and self.settings.get().get("meeting_detect", True):
                    notified = app
                    notify("¿Quieres tomar notas de la sesión?", "Pulsa Ctrl+Alt+R para empezar a tomar notas.")
                    log(f"   reunión detectada ({app}): aviso enviado")
                if not app:
                    notified = None
            except Exception:
                pass

    # --- edicion de transcripciones
    def save_transcript(self, b):
        """Guarda lo corregido a mano. La primera vez copia lo que escribio Whisper (para poder volver)."""
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
        return {"jobs": self.jobs.snapshot()}

    # --- POST
    def transcribe(self, b):
        paths = b.get("paths") or ([b["path"]] if b.get("path") else [])
        preset = b.get("preset") or self.settings.get()["preset"]
        ids = self.jobs.submit(paths, preset, b.get("language"), resume=bool(b.get("resume")))
        self.settings.update(lambda d: d.update(preset=preset, language=b.get("language") or ""))
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
        job.translate = bool(b.get("on"))
        return job.public()

    def clear_jobs(self, b):
        self.jobs.clear()
        return {"ok": True}

    def move(self, b):
        src = safe(b.get("path"), "file")
        dest_dir = safe(b.get("folder", ""), "dir")
        if in_app_dir(dest_dir):
            raise ApiError("No se puede mover ahí")
        a = self.library.find(b.get("path"))
        if a and a.get("recording"):
            raise ApiError("OBS sigue grabando ese audio; muévelo cuando pare la grabación.")
        if self.jobs.active_for(b.get("path")):
            raise ApiError("Ese audio se está transcribiendo; espera a que termine.")
        if self.jobs.active_for(b.get("path"), kinds=("summary",)):  # el .resumen.md se escribiria en la ruta vieja
            raise ApiError("Se está resumiendo esta reunión; muévela cuando termine (o cancela el resumen en la cola).")
        stem, ext = os.path.splitext(os.path.basename(src))
        new_stem = clean_name(b["name"]) if b.get("name") else stem
        group = [src]
        if ext.lower() in AUDIO_EXT:
            for e in SIDE_EXTS:
                sib = os.path.join(os.path.dirname(src), stem + e)
                if os.path.isfile(sib):
                    group.append(sib)
        moves = []
        for f in group:
            # lo que va detras del nombre (.txt, .srt, .md, .resumen.md): asi .resumen.md no acaba en .md
            target = os.path.join(dest_dir, new_stem + os.path.basename(f)[len(stem):])
            if os.path.normcase(target) != os.path.normcase(f) and os.path.exists(target):
                raise ApiError("Ya existe un archivo con ese nombre en el destino: " + os.path.basename(target))
            moves.append((f, target))
        for f, target in moves:
            if f == target:
                continue
            for attempt in range(12):  # el reproductor u OneDrive pueden tenerlo abierto un instante
                try:
                    shutil.move(f, target)
                    break
                except PermissionError:
                    if attempt == 11:
                        raise ApiError("El archivo está en uso (¿OneDrive sincronizando o abierto en otro programa?). "
                                       "Prueba otra vez en unos segundos.")
                    time.sleep(0.25)
        self.library.at = 0
        COMPLETE.moved(rel_of(src), rel_of(moves[0][1]))
        if rel_of(src) in self.jobs.voice_vectors:
            self.jobs.voice_vectors[rel_of(moves[0][1])] = self.jobs.voice_vectors.pop(rel_of(src))
        self._rename_refs(rel_of(src), rel_of(moves[0][1]))
        old_b, new_b = backup_dir(rel_of(src)), backup_dir(rel_of(moves[0][1]))
        if os.path.isdir(old_b) and not os.path.exists(new_b):  # la copia del original viaja con el audio
            os.replace(old_b, new_b)
        log(f"↪ {rel_of(src)}  →  {rel_of(moves[0][1])}")
        return {"path": rel_of(moves[0][1])}

    def mkdir(self, b):
        parent = safe(b.get("parent", ""), "dir")
        p = os.path.join(parent, clean_name(b.get("name")))
        if os.path.exists(p):
            raise ApiError("Ya existe esa carpeta")
        os.makedirs(p)
        self.library.at = 0
        return {"path": rel_of(p)}

    def rename_folder(self, b):
        if is_link_root(b.get("path")):  # carpeta vinculada: solo cambia el nombre que se ve en Notas
            lid = b["path"].strip("/")[len(LINK):]
            if lid not in LINKS:
                raise ApiError("Esa carpeta ya no está vinculada.")
            LINKS[lid]["name"] = clean_name(b.get("name"))
            self._save_links()
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
        self._rename_refs(rel_of(src), rel_of(dst), folder=True)
        self.library.at = 0
        return {"path": rel_of(dst)}

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
        def upd(d):
            for k in ("preset", "language", "prompt"):
                if k in b:
                    d[k] = b[k] or ""
            s = b.get("summary") or {}
            if "auto" in s:
                d["summary_auto"] = bool(s["auto"])
            if s.get("model"):
                d["summary_model"] = s["model"].strip()
            if s.get("lang") in engines.SUMMARY_LANGS:
                d["summary_lang"] = s["lang"]
            if "extra" in s:
                d["summary_extra"] = (s["extra"] or "").strip()[:500]
            if "meeting_detect" in b:
                d["meeting_detect"] = bool(b["meeting_detect"])
            if "live_auto" in b:
                d["live_auto"] = bool(b["live_auto"])
            if "live_final_pass" in b:
                d["live_final_pass"] = bool(b["live_final_pass"])
            if b.get("live_preset") in LIVE_PRESETS:
                d["live_preset"] = b["live_preset"]
            if "threads" in b:
                d["threads"] = max(1, min(int(b["threads"] or default_threads()), os.cpu_count() or 16))
            c = b.get("cloud") or {}
            for k in ("provider", "model", "base_url", "azure_endpoint", "azure_deployment", "azure_api_version"):
                if k in c:
                    d["cloud"][k] = (c[k] or "").strip()
            # 20 min de FLAC 16 kHz ~ 22 MB: por debajo del limite de 25 MB por archivo de las APIs
            for k, lo, hi in (("chunk_minutes", 2, 20), ("parallel", 1, 8)):
                if k in c:
                    d["cloud"][k] = max(lo, min(int(c[k] or lo), hi))
            if c.get("api_key") and "…" not in c["api_key"] and "•" not in c["api_key"]:
                if not d["cloud"].get("api_key"):  # primera key: la nube pasa a ser lo predeterminado
                    d["preset"] = d["live_preset"] = "nube"
                d["cloud"]["api_key"] = c["api_key"].strip()
            if c.get("clear_key"):
                d["cloud"]["api_key"] = ""
            if "live_model" in c:
                d["cloud"]["live_model"] = (c["live_model"] or "").strip()[:80]
            if isinstance(c.get("extra_keys"), list):  # las ya guardadas vuelven enmascaradas: se conservan
                old = {masked(k): k for k in d["cloud"].get("extra_keys") or []}
                new = [old.get(k.strip(), k.strip()) for k in c["extra_keys"] if isinstance(k, str) and k.strip()]
                d["cloud"]["extra_keys"] = [k for k in new if "…" not in k and "•" not in k][:10]
        self.settings.update(upd)
        return self.config({})

    def test_cloud(self, b):
        try:
            secs = self.jobs.cloud.test(self.settings.get()["cloud"])
        except engines.EngineError as e:
            raise ApiError(str(e))
        return {"ok": True, "seconds": round(secs, 2)}

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

    def _speaker_update(self, abs_path, h, fn):
        side = voices.sidecar_path(abs_path)
        data = voices.read_sidecar(side)
        if not data or h not in (data.get("hablantes") or {}):
            raise ApiError("Ese hablante ya no está en esta reunión (¿se volvió a identificar?).")
        fn(data["hablantes"][h], data)
        voices.write_sidecar(side, data)
        self.library.at = 0
        return data

    def voices_person(self, b):
        """Nombre, «Soy yo» y «Recordar» de un hablante (de una reunion) o de una persona ya guardada."""
        v = self.jobs.voices
        if b.get("recordar") and not self.jobs.voices_on():
            raise ApiError("Activa «Voces» en Ajustes ⚙ para guardar huellas.")
        pid, path, h = b.get("id"), b.get("path"), b.get("h")
        consent = b.get("consentimiento")
        abs_path = safe(path, "file") if path else None
        nombre = " ".join(str(b.get("nombre") or "").split())[:80]
        if pid and not v.find(pid):
            raise ApiError("Esa persona ya no existe.")
        if not pid:
            if not nombre and not b.get("es_usuario"):
                raise ApiError("Escribe un nombre.")
            when = time.strftime("%Y-%m-%dT%H:%M:%S") if consent else None
            pid = v.create(nombre or "Yo", bool(b.get("es_usuario")), when)["id"]
        else:
            if nombre and nombre != v.find(pid)["nombre"]:
                v.rename(pid, nombre)
            if b.get("es_usuario") is not None:
                v.set_user(pid, bool(b.get("es_usuario")))
        if abs_path and h:
            self._speaker_update(abs_path, h, lambda info, d: info.update(persona=pid, provisional=False))
        remembered = None
        if b.get("recordar"):
            if not consent:
                raise ApiError("Para guardar la huella hace falta marcar la casilla de consentimiento.")
            if not (abs_path and h):
                raise ApiError("Falta de qué reunión y hablante tomar la huella.")
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
                n += 1
        self.library.at = 0
        log(f"🗣 personas fusionadas ({n} reuniones actualizadas)")
        return {"id": keep, "reuniones": n, "personas": v.public()}

    def voices_forget(self, b):
        v = self.jobs.voices
        if b.get("all"):
            v.forget_all()
            log("🗣 todas las huellas olvidadas")
        else:
            try:
                v.forget(b.get("id"))
            except KeyError:
                raise ApiError("Esa persona ya no existe.")
            log("🗣 persona olvidada")
        self.library.at = 0
        return {"personas": v.public()}

    def voices_reassign(self, b):
        abs_path = safe(b.get("path"), "file")
        pid = b.get("persona") or None
        if pid and not self.jobs.voices.find(pid):
            raise ApiError("Esa persona ya no existe.")
        self._speaker_update(abs_path, b.get("h"), lambda info, d: info.update(persona=pid, provisional=False))
        return self.speakers_get({"path": b.get("path")})

    def voices_export(self, b):
        abs_path = safe(b.get("path"), "file")
        data = self.jobs.speakers_for(abs_path)
        if not data or not data.get("_who"):
            raise ApiError("Esta reunión no tiene hablantes identificados (o no tiene .srt).")
        segs = Transcripts().load(os.path.splitext(abs_path)[0])["segments"]
        out = os.path.splitext(abs_path)[0] + ".hablantes.txt"
        save_text(out, voices.export_text(segs, data["_who"], data, self.jobs.voices.names()), "utf-8-sig")
        self.library.at = 0
        return {"path": rel_of(out), "ext": ".hablantes.txt"}

    def export_package(self, b):
        dest = os.path.join(desktop_dir(), "Notas - instalador.zip")
        vocab = []
        if b.get("vocab"):  # las de carpetas vinculadas (":id/…") van como Notas/<proyecto>/Vocabulario/<tema>.txt
            vocab = [(v["_abs"], "Notas/" + (v["id"] if not v["id"].startswith(LINK)
                                             else f"{v['project']}/Vocabulario/{v['name']}.txt")) for v in list_vocab()]
        try:
            n = build_package(dest, vocab)
        except OSError as e:
            raise ApiError(f"No se pudo crear el paquete: {e}")
        if WINDOWS:
            subprocess.Popen(f'explorer /select,"{dest}"')
        return {"ok": True, "path": dest, "files": n, "size": os.path.getsize(dest), "vocab": len(vocab)}


# ----------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Notas/1.0"
    app = None

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        a = self.app
        routes = {"/api/ping": lambda q: {"app": "audios"}, "/api/config": a.config, "/api/tree": a.tree,
                  "/api/transcript": a.transcript, "/api/doc": a.doc, "/api/search": a.search,
                  "/api/jobs": a.jobs_list, "/api/rec/devices": a.rec_devices, "/api/rec/status": a.rec_status,
                  "/api/notes": a.notes_get, "/api/tags": a.tags, "/api/summary": a.summary_get,
                  "/api/vocab": a.vocab, "/api/voces": a.voices_state, "/api/hablantes": a.speakers_get}
        try:
            if u.path in routes:
                return self._json(routes[u.path](q))
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
        if not self._host_ok() or self.headers.get("X-Audios") != "1":
            return self._send(403, b"forbidden", "text/plain")
        a = self.app
        routes = {"/api/transcribe": a.transcribe, "/api/cancel": a.cancel, "/api/jobs/clear": a.clear_jobs,
                  "/api/move": a.move, "/api/mkdir": a.mkdir, "/api/rename-folder": a.rename_folder,
                  "/api/reveal": a.reveal, "/api/open": a.open_file, "/api/settings": a.save_settings,
                  "/api/cloud/test": a.test_cloud, "/api/live": a.start_live, "/api/live/model": a.live_model,
                  "/api/live/traducir": a.live_translate,
                  "/api/rec/monitor": a.rec_monitor, "/api/rec/start": a.rec_start, "/api/rec/stop": a.rec_stop,
                  "/api/rec/toggle": a.rec_toggle, "/api/rec/mic": a.rec_mic, "/api/notes": a.notes_save,
                  "/api/summarize": a.summarize,
                  "/api/favorite": a.favorite, "/api/folder-icon": a.folder_icon, "/api/delete": a.delete,
                  "/api/transcript/save": a.save_transcript, "/api/transcript/restore": a.restore_transcript,
                  "/api/export": a.export_package, "/api/link/pick": a.link_pick, "/api/link/add": a.link_add,
                  "/api/link/remove": a.link_remove, "/api/vocab/save": a.vocab_save,
                  "/api/vocab/delete": a.vocab_delete, "/api/vocab/check": a.vocab_check,
                  "/api/voces/activar": a.voices_toggle, "/api/voces/identificar": a.voices_identify,
                  "/api/voces/persona": a.voices_person, "/api/voces/fusionar": a.voices_merge,
                  "/api/voces/olvidar": a.voices_forget, "/api/voces/reasignar": a.voices_reassign,
                  "/api/voces/exportar": a.voices_export}
        try:
            u = urlparse(self.path)
            if u.path == "/api/import":  # el cuerpo es el archivo tal cual (puede ser de 1 GB): se copia a trozos
                return self._json(a.import_file(self, {k: v[0] for k, v in parse_qs(u.query).items()}))
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(body, dict):
                raise ApiError("Cuerpo no válido: se esperaba un objeto JSON")
            route = routes.get(u.path)
            if not route:
                raise ApiError("No encontrado", 404)
            self._json(route(body))
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
        self._send(200, body, MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))

    def _media(self, p):
        """Audio con soporte de Range: permite saltar a cualquier minuto al instante."""
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
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
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


def open_ui():
    for exe in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        exe = os.path.expandvars(exe)
        if os.path.exists(exe):
            subprocess.Popen([exe, f"--app={URL}", "--window-size=1500,920"])
            return
    webbrowser.open(URL)


def already_running():
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=1.5) as r:
            return json.load(r).get("app") == "audios"
    except Exception:
        return False


def arg(name, default=None):
    """--port 8766 / --data C:\\ruta (para probar una segunda copia sin tocar la principal)."""
    if name in sys.argv[:-1]:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def main():
    global PORT, URL, DATA_DIR
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
    if already_running():
        print("La app ya estaba abierta; abriendo ventana…")
        if not no_browser:
            open_ui()
        return
    try:
        import truststore  # usa los certificados de Windows (proxies corporativos), si esta instalado
        truststore.inject_into_ssl()
    except Exception:
        pass
    Handler.app = App()
    Handler.app.library.refresh()
    threading.Thread(target=Handler.app.transcripts.warm, args=(Handler.app.library,), daemon=True).start()
    srv = Server((HOST, PORT), Handler)
    print("=" * 64)
    print("  NOTAS  ·  " + URL)
    print("  Carpeta: " + ROOT)
    print("  Deja esta ventana abierta mientras uses la app (ciérrala para apagarla).")
    print("=" * 64, flush=True)
    log(f"arranque · {URL} · carpeta {ROOT} · datos {DATA_DIR} · Python {sys.version.split()[0]}")
    if not no_browser:
        threading.Timer(0.3, open_ui).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
