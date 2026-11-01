# -*- coding: utf-8 -*-
"""
Destinos de salida: cada reunion, como documento, en cero, una o varias carpetas (un vault de Obsidian, la carpeta
del proyecto en OneDrive, una biblioteca de SharePoint sincronizada…), en el formato de cada destino:

  - md:   un solo <stem>.md con cabecera de propiedades (YAML), resumen, notas y transcripcion con minutos
  - txt:  <stem>.txt, texto plano con las mismas secciones
  - docx: <stem>.docx hecho con la libreria estandar (zip + document.xml minimo con titulos y parrafos)

Se escribe al terminar la transcripcion y el resumen y se vuelve a escribir al guardar cambios en las notas o en la
transcripcion (server.py decide cuando). El original no se mueve ni se toca: el audio y sus sidecars siguen juntos.
Solo se sobrescribe un fichero que genero la propia app: lleva la marca «generado_por» (cabecera del .md, primera
linea del .txt, docProps/core.xml del .docx) y se recuerda el hash de lo escrito y de que reunion es (registro en
%LOCALAPPDATA%): un fichero ajeno, uno nuestro que alguien haya cambiado en el destino, o uno con la marca que no
consta en el registro (otro PC, una reinstalacion) y no coincide con lo que se va a escribir, se deja como esta y se
avisa. Si el nombre <stem>.<fmt> ya es de otra reunion (o de un fichero ajeno), el documento va a
<stem> (<6 hex>).<fmt>, estable para esa reunion. Nunca se escribe encima de un sidecar de una grabacion
(<stem>.md, .txt, .srt, .resumen.md, .hablantes.*) ni en una carpeta de la app (check_destinos y `blocked`).
"""
import hashlib
import io
import json
import os
import re
import threading
import time
import zipfile
from xml.sax.saxutils import escape, unescape

from common import APP_NAME, ApiError, clean_name

FORMATS = ("md", "txt", "docx")
FORMAT_LABELS = {"md": "Markdown (.md)", "txt": "Texto (.txt)", "docx": "Word (.docx)"}
MARK = "generado_por"  # marca de origen: clave de la cabecera .md, primera linea del .txt y keywords del .docx
MAX_DESTINOS = 10
_HEAD_BYTES = 4096
# lo que la app trata como grabacion y lo que acompana a cada una (como AUDIO_EXT y SIDE_EXTS de server.py; un test
# comprueba que no se desincronicen): un documento con uno de esos nombres junto a un audio pasaria por sus notas
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".aac", ".wma", ".opus", ".mkv", ".mov", ".webm")
SIDECAR_TAILS = (".txt", ".srt", ".md", ".resumen.md", ".hablantes.json", ".hablantes.txt")
REPLACE_ATTEMPTS = 20  # Word u OneDrive pueden tener el documento abierto un instante (como common.write_bytes)


def inside(p, base):
    """p es base o esta dentro de base (sin distinguir mayusculas en Windows)."""
    p, base = os.path.normcase(os.path.normpath(p)), os.path.normcase(os.path.normpath(base))
    return p == base or p.startswith(base.rstrip(os.sep) + os.sep)


def first_audio(folder):
    """Nombre de la primera grabacion (audio o video que la app trataria como reunion) de esa carpeta, o None."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    return next((n for n in names if os.path.splitext(n)[1].lower() in AUDIO_EXT
                 and os.path.isfile(os.path.join(folder, n))), None)


def sidecar_of(path):
    """El audio del que `path` seria un sidecar en su carpeta (<stem>.md, .txt, .srt, .resumen.md, .hablantes.*), o
    None. Escribir ahi pisaria (o se haria pasar por) las notas, la transcripcion o el resumen de esa grabacion."""
    folder, name = os.path.split(path)
    try:
        audios = {os.path.splitext(n)[0].lower(): n for n in os.listdir(folder)
                  if os.path.splitext(n)[1].lower() in AUDIO_EXT}
    except OSError:
        return None
    low = name.lower()
    for tail in SIDECAR_TAILS:
        if low.endswith(tail) and low[:-len(tail)] in audios:
            return os.path.join(folder, audios[low[:-len(tail)]])
    return None


def mark_line(app_name):
    return (f"{MARK}: {app_name} (desde la app; se vuelve a generar al cambiar las notas, el resumen o la "
            "transcripción: los cambios hechos aquí se pierden)")


# ----------------------------------------------------------------------------- ajustes

def destinos_de(value):
    """Normaliza la lista guardada en los ajustes: [{"path", "format", "by_project"}] (lo raro se descarta)."""
    out = []
    for d in value if isinstance(value, list) else []:
        if not isinstance(d, dict) or not str(d.get("path") or "").strip():
            continue
        fmt = str(d.get("format") or "md").lower()
        out.append({"path": str(d["path"]).strip(), "format": fmt if fmt in FORMATS else "md",
                    "by_project": bool(d.get("by_project"))})
    return out


def check_destinos(value, app_dir=None, roots=(), known=()):
    """Valida lo que llega de la interfaz y lo devuelve normalizado (sin repetidos).
    Un destino nuevo tiene que existir y no puede ser la carpeta de la app ni estar dentro de `roots` (la raiz de la
    app y las carpetas vinculadas, que es donde viven las grabaciones) ni tener grabaciones: alli el documento
    <grabacion>.md chocaria con las notas de esa grabacion. Uno que ya estaba guardado (`known`) se acepta aunque ahora
    no este disponible (sin VPN, disco desconectado): si no, no se podria guardar ningun otro ajuste."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError("Destinos de salida no válidos.")
    known = {os.path.normcase(os.path.normpath(str(k))) for k in known or ()}
    out, seen = [], set()
    for d in value:
        if not isinstance(d, dict):
            raise ApiError("Destino de salida no válido.")
        path = os.path.normpath(os.path.expandvars(str(d.get("path") or "").strip().strip('"')))
        old = os.path.normcase(path) in known
        if not path or not os.path.isabs(path) or not (old or os.path.isdir(path)):
            raise ApiError("Esa carpeta no existe: " + (path or "(vacía)"))
        if os.path.splitdrive(path)[1] in ("", os.sep):
            raise ApiError("Elige una carpeta, no una unidad entera.")
        if app_dir and inside(path, app_dir):
            raise ApiError("Esa carpeta es la de la propia app.")
        if not old:
            if any(r and inside(path, r) for r in roots):
                raise ApiError(f"Esa carpeta es de {APP_NAME} (su carpeta o una carpeta vinculada), que es donde se "
                               "guardan las grabaciones: ahí cada documento chocaría con las notas de su reunión. "
                               f"Elige una carpeta fuera de {APP_NAME} para los documentos.")
            audio = first_audio(path)
            if audio:
                raise ApiError(f"Esa carpeta tiene grabaciones («{audio}»): los documentos chocarían con sus notas. "
                               "Elige otra carpeta para los documentos.")
        fmt = str(d.get("format") or "md").lower()
        if fmt not in FORMATS:
            raise ApiError("Formato desconocido: " + fmt)
        key = (os.path.normcase(path), fmt)
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": path, "format": fmt, "by_project": bool(d.get("by_project"))})
    if len(out) > MAX_DESTINOS:
        raise ApiError(f"Como mucho {MAX_DESTINOS} destinos de salida.")
    return out


# ----------------------------------------------------------------------------- registro de lo escrito

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class Registro:
    """destino -> {"sha": sha256 de lo que escribimos la ultima vez, "rel": la reunion (ruta interna) de la que es,
    "path": la ruta tal cual} (%LOCALAPPDATA%\\AudiosApp\\salidas.json). Un registro antiguo (destino -> sha) se lee
    igual y pasa al formato nuevo en la siguiente escritura de ese documento."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.data = {}
        for k, v in (data.items() if isinstance(data, dict) else []):
            if isinstance(v, str):
                self.data[k] = {"sha": v}
            elif isinstance(v, dict) and isinstance(v.get("sha"), str):
                self.data[k] = v

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=0)
        os.replace(tmp, self.path)

    def get(self, dest):
        with self.lock:
            return (self.data.get(os.path.normcase(dest)) or {}).get("sha")

    def rel_of(self, dest):
        """La reunion de la que es ese documento (None si no consta o es un registro antiguo)."""
        with self.lock:
            return (self.data.get(os.path.normcase(dest)) or {}).get("rel")

    def entries(self, rel):
        """[(ruta, sha)] de los documentos registrados de esa reunion."""
        with self.lock:
            return [(e.get("path") or k, e["sha"]) for k, e in self.data.items() if e.get("rel") == rel]

    def set(self, dest, sha, rel=None):
        with self.lock:
            e = dict(self.data.get(os.path.normcase(dest)) or {}, sha=sha, path=dest)
            if rel:
                e["rel"] = rel
            self.data[os.path.normcase(dest)] = e
            self._save()

    def forget(self, dests):
        with self.lock:
            hit = [os.path.normcase(d) for d in dests if os.path.normcase(d) in self.data]
            for k in hit:
                del self.data[k]
            if hit:
                self._save()

    def moved(self, old, new, folder=False):
        """La reunion `old` (o, con folder, todas las de esa carpeta) pasa a llamarse `new`: sus documentos siguen
        siendo suyos (si no, la siguiente escritura los tomaria por de otra reunion y escribiria uno nuevo al lado)."""
        with self.lock:
            n = 0
            for e in self.data.values():
                r = e.get("rel")
                if r == old or (folder and r and r.startswith(old + "/")):
                    e["rel"], n = new + r[len(old):], n + 1
            if n:
                self._save()
            return n


# ----------------------------------------------------------------------------- el documento

def _stamp(t):
    t = int(t or 0)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def transcript_lines(segments, width=260):
    """Lineas de ~260 caracteres con su minuto (y quien habla, si viene): como las que lee el resumen."""
    out, cur, t0, who = [], "", None, None

    def flush():
        out.append((f"[{_stamp(t0)}] " if t0 is not None else "") + (f"{who}: " if who else "") + cur)
    for s in segments or []:
        w = s.get("who")
        if cur and w != who:
            flush()
            cur, t0 = "", None
        if t0 is None:
            t0, who = s.get("start"), w
        cur = (cur + " " + str(s.get("text") or "")).strip()
        if len(cur) >= width:
            flush()
            cur, t0 = "", None
    if cur:
        flush()
    return out


def _yaml(v):
    """Un valor de la cabecera .md: entre comillas (un titulo con «:» o «#» rompe el YAML de Obsidian)."""
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yaml(x) for x in v) + "]"
    s = " ".join(str(v).split())
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _props(doc):
    """Pares (clave, valor) de la cabecera, en orden y solo los que tienen algo."""
    p = doc.get("props") or {}
    pairs = [("titulo", doc.get("title")), ("fecha", doc.get("date")), ("duracion", doc.get("duration")),
             ("proyecto", p.get("proyecto")), ("tema", p.get("workstream")),
             ("participantes", p.get("participantes")), ("etiquetas", p.get("etiquetas")), ("estado", p.get("estado")),
             ("idioma", p.get("idioma")), ("contexto", p.get("contexto")), ("origen", doc.get("source")),
             ("resumen_modelo", doc.get("summary_model"))]
    return [(k, v) for k, v in pairs if v]


_HEADING = re.compile(r"^(#{1,6})(\s+)", re.M)


def _demote(md, floor=3):
    """Los titulos del resumen y de las notas, por debajo de los «##» del documento (el mas alto pasa a ser «###»)."""
    levels = [len(m.group(1)) for m in _HEADING.finditer(md or "")]
    shift = max(0, floor - min(levels)) if levels else 0
    return _HEADING.sub(lambda m: "#" * min(6, len(m.group(1)) + shift) + m.group(2), md or "") if shift else (md or "")


def render_md(doc):
    app = doc.get("app_name") or "la app"
    head = [f"{k}: {_yaml(v)}" for k, v in _props(doc)]
    head.append(f"{MARK}: {_yaml(mark_line(app).partition(': ')[2])}")
    head.append(f"generado: {_yaml(doc.get('generated') or time.strftime('%Y-%m-%d %H:%M'))}")
    parts = ["---\n" + "\n".join(head) + "\n---\n", f"# {doc.get('title') or doc.get('stem')}\n"]
    parts.append("## Resumen\n\n" + (_demote(doc.get("summary")).strip() or "*Sin resumen todavía.*") + "\n")
    parts.append("## Notas\n\n" + (_demote(doc.get("notes")).strip() or "*Sin notas.*") + "\n")
    lines = transcript_lines(doc.get("segments"))
    parts.append("## Transcripción\n\n" + ("\n\n".join(lines) if lines else "*Sin transcripción todavía.*") + "\n")
    return "\n".join(parts)


def render_txt(doc):
    app = doc.get("app_name") or "la app"
    out = [doc.get("title") or doc.get("stem") or "", mark_line(app),
           " · ".join(f"{k.capitalize()}: {', '.join(v) if isinstance(v, list) else v}" for k, v in _props(doc)
                      if k not in ("titulo", "contexto", "resumen_modelo")), ""]

    def section(name, body):
        out.extend([name, "=" * len(name), body.strip() if body and body.strip() else "(nada)", ""])
    section("RESUMEN", _plain(doc.get("summary") or ""))
    section("NOTAS", _plain(doc.get("notes") or ""))
    section("TRANSCRIPCIÓN", "\n".join(transcript_lines(doc.get("segments"))))
    return "\n".join(out).rstrip("\n") + "\n"


def _plain(md):
    """Markdown de las notas a texto llano: sin almohadillas de titulo ni marcadores de lista/tarea/cita."""
    out = []
    for l in (md or "").splitlines():
        l = re.sub(r"^\s*#{1,6}\s+", "", l)
        bullet = "  - " if re.match(r"^\s*([-*]|\d+[.)])\s", l) else ""
        l = re.sub(r"^\s*(?:[-*]\s+\[[ xX]\]\s+|[-*]\s+|\d+[.)]\s+|>\s*)", bullet, l)
        out.append(l.rstrip())
    return "\n".join(out)


# --- docx: zip + XML minimo (sin dependencias). Word, LibreOffice y el visor de OneDrive lo abren.
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                  '<Default Extension="xml" ContentType="application/xml"/>'
                  '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                  'wordprocessingml.document.main+xml"/>'
                  '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                  'wordprocessingml.styles+xml"/>'
                  '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.'
                  'core-properties+xml"/>'
                  '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                  'extended-properties+xml"/></Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
         'officeDocument" Target="word/document.xml"/>'
         '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
         'core-properties" Target="docProps/core.xml"/>'
         '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
         'extended-properties" Target="docProps/app.xml"/></Relationships>')
_DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
             'styles" Target="styles.xml"/></Relationships>')
_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:styles {_W}>'
           '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
           '<w:sz w:val="22"/><w:lang w:val="es-ES"/></w:rPr></w:rPrDefault>'
           '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
           '</w:docDefaults>'
           '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
           '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
           '<w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>'
           '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
           '<w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>'
           '<w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style>'
           '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
           '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr>'
           '<w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
           '<w:style w:type="paragraph" w:styleId="Meta"><w:name w:val="Meta"/><w:basedOn w:val="Normal"/>'
           '<w:rPr><w:color w:val="666666"/><w:sz w:val="18"/></w:rPr></w:style></w:styles>')


def _para(text, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    runs = []
    for i, part in enumerate(str(text).split("\n")):
        if i:
            runs.append("<w:r><w:br/></w:r>")
        runs.append(f'<w:r><w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def _md_paras(md, style=None):
    """Parrafos a partir del Markdown de las notas o del resumen: titulos (## …) con su estilo, listas con viñeta."""
    out, cur = [], []

    def flush():
        if cur:
            out.append(_para(" ".join(cur), style))
            cur.clear()
    for l in (md or "").splitlines():
        s = l.strip()
        if not s:
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush()
            out.append(_para(m.group(2), "Heading2"))
            continue
        m = re.match(r"^(?:[-*]\s+\[[ xX]\]\s+|[-*]\s+|\d+[.)]\s+|>\s*)(.*)$", s)
        if m:
            flush()
            out.append(_para("• " + m.group(1), style))
            continue
        cur.append(s)
    flush()
    return out


def render_docx(doc):
    app = doc.get("app_name") or "la app"
    body = [_para(doc.get("title") or doc.get("stem") or "", "Title")]
    meta = " · ".join(f"{k.capitalize()}: {', '.join(v) if isinstance(v, list) else v}" for k, v in _props(doc)
                      if k not in ("titulo", "contexto", "resumen_modelo"))
    if meta:
        body.append(_para(meta, "Meta"))
    body.append(_para(mark_line(app), "Meta"))
    body.append(_para("Resumen", "Heading1"))
    body.extend(_md_paras(doc.get("summary")) or [_para("Sin resumen todavía.")])
    body.append(_para("Notas", "Heading1"))
    body.extend(_md_paras(doc.get("notes")) or [_para("Sin notas.")])
    body.append(_para("Transcripción", "Heading1"))
    lines = transcript_lines(doc.get("segments"))
    body.extend([_para(l) for l in lines] or [_para("Sin transcripción todavía.")])
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document {_W}><w:body>{"".join(body)}'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1417" w:right="1417" w:bottom="1417" '
                'w:left="1417" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>')
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(str(doc.get("title") or doc.get("stem") or ""))}</dc:title>'
            f'<dc:creator>{escape(app)}</dc:creator><cp:keywords>{escape(MARK + ": " + app)}</cp:keywords>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    appxml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
              'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
              f'<Application>{escape(app)}</Application></Properties>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", appxml)
    return buf.getvalue()


def render(doc, fmt):
    """Bytes del documento en ese formato (.md sin BOM, que confunde a los lectores de YAML como Obsidian; .txt con
    BOM, como los sidecars, para el Bloc de notas)."""
    if fmt == "docx":
        return render_docx(doc)
    if fmt == "txt":
        return ("﻿" + render_txt(doc)).encode("utf-8")
    return render_md(doc).encode("utf-8")


# ----------------------------------------------------------------------------- escribir sin pisar

def is_ours(path, fmt):
    """True si el fichero lleva nuestra marca de origen (lo genero la app, en cualquier version o PC)."""
    try:
        if fmt == "docx":
            with zipfile.ZipFile(path) as z:
                return MARK + ":" in z.read("docProps/core.xml").decode("utf-8", "replace")
        with open(path, "rb") as f:
            head = f.read(_HEAD_BYTES).decode("utf-8", "replace").lstrip("﻿")
        if fmt == "txt":
            return any(l.startswith(MARK + ":") for l in head.splitlines()[:3])
        m = re.match(r"---\r?\n(.*?)\r?\n---", head, re.S)
        return bool(m) and any(l.startswith(MARK + ":") for l in m.group(1).splitlines())
    except (OSError, zipfile.BadZipFile, KeyError):
        return False


def origin_of(path, fmt):
    """La reunion de origen que dice el propio documento: `origen:` de la cabecera del .md o «Origen: …» de la linea de
    propiedades del .txt y del .docx (la que pinta render_docx en word/document.xml). None si no lo dice."""
    if fmt == "docx":
        try:
            lines = _docx_lines(path)
        except Exception:  # un .docx roto o a medio subir: no dice nada
            return None
        return next((o for o in map(_origin_in, lines[1:]) if o), None)
    try:
        with open(path, "rb") as f:
            head = f.read(_HEAD_BYTES).decode("utf-8", "replace").lstrip("﻿")
    except OSError:
        return None
    if fmt == "md":
        m = re.match(r"---\r?\n(.*?)\r?\n---", head, re.S)
        for l in (m.group(1).splitlines() if m else []):
            if l.startswith("origen:"):
                v = l.partition(":")[2].strip()
                if len(v) >= 2 and v[0] == v[-1] == '"':
                    v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                return v or None
    elif fmt == "txt":
        return next((o for o in map(_origin_in, head.splitlines()[1:4]) if o), None)
    return None


_W_P = re.compile(r"<w:p\b[^>]*>(.*?)</w:p>", re.S)
_W_T = re.compile(r"<w:t\b[^>]*>([^<]*)</w:t>")


def _origin_in(line):
    """La ruta de «Origen: …» en la linea de propiedades del .txt o del .docx. Es el ultimo campo de esa linea: una
    ruta que lleve « · » tambien se lee entera."""
    if line.startswith("Origen: "):
        return line[len("Origen: "):].strip() or None
    i = line.find(" · Origen: ")
    return (line[i + len(" · Origen: "):].strip() or None) if i >= 0 else None


def _docx_lines(path, n=6):
    """El texto de los primeros parrafos de un .docx (titulo, propiedades, marca…), tambien si Word lo ha vuelto a
    guardar con el texto partido en varios trozos."""
    with zipfile.ZipFile(path) as z, z.open("word/document.xml") as f:
        xml = f.read(1 << 16).decode("utf-8", "replace")
    out = []
    for m in _W_P.finditer(xml):
        out.append(unescape("".join(_W_T.findall(m.group(1))), {"&quot;": '"', "&apos;": "'"}))
        if len(out) == n:
            break
    return out


_GENERADO = re.compile(rb"^generado: .*\r?\n", re.M)


def _fingerprint(data, fmt):
    """Lo que cuenta para saber si dos documentos dicen lo mismo: sin la fecha de generacion (cabecera del .md) ni las
    fechas del .docx (docProps/core.xml), que cambian en cada escritura aunque la reunion no haya cambiado."""
    if fmt == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return z.read("word/document.xml")
        except (zipfile.BadZipFile, KeyError, OSError):
            return data
    if fmt == "md":
        m = re.match(rb"---\r?\n.*?\r?\n---", data, re.S)
        if m:
            return _GENERADO.sub(b"", m.group(0)) + data[m.end():]
    return data


def same_content(path, data, fmt):
    """True si el documento que hay en `path` dice lo mismo que `data` (salvo las fechas de generacion)."""
    try:
        with open(path, "rb") as f:
            return _fingerprint(f.read(), fmt) == _fingerprint(data, fmt)
    except OSError:
        return False


_LOCKS, _LOCKS_LOCK = {}, threading.Lock()


def _lock_for(folder):
    """Un cerrojo por carpeta de destino: el temporizador de las notas y el trabajo que acaba de transcribir o resumir
    pueden escribir la misma reunion a la vez (y dos reuniones pueden querer el mismo nombre)."""
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(os.path.normcase(os.path.normpath(folder)), threading.Lock())


def alt_name(folder, stem, rel, fmt):
    """<stem> (<6 hex>).<fmt>: el nombre propio de una reunion cuando <stem>.<fmt> ya es de otra (o de un fichero
    ajeno). Sale de la ruta de la reunion: siempre el mismo para ella."""
    h = hashlib.sha1((rel or stem or "").encode("utf-8")).hexdigest()[:6]
    return os.path.join(folder, f"{stem} ({h}).{fmt}")


MAX_ALT = 10  # nombres propios que se prueban para una reunion (alt_for)


def alt_for(registro, folder, stem, rel, fmt):
    """El nombre propio de una reunion cuando <stem>.<fmt> no le vale: alt_name, salvo que ese ya sea de otra reunion
    (la que estuvo antes en esta misma ruta y se movio conservando su documento) o de un fichero ajeno; entonces el de
    la siguiente semilla. Devuelve uno libre o uno que ya es de esta reunion (si los MAX_ALT estan cogidos, el ultimo:
    write_one no escribe encima del documento de otra reunion)."""
    for n in range(MAX_ALT):
        alt = alt_name(folder, stem, f"{rel}#{n}" if n else rel, fmt)
        if not os.path.isfile(alt):
            return alt
        owner = registro.rel_of(alt)
        if owner == rel or (owner is None and is_ours(alt, fmt) and origin_of(alt, fmt) in (None, rel)):
            return alt
    return alt


def _own_doc(entries, folder, stem, fmt, first):
    """De los documentos que el registro da por de esta reunion, el que sigue en esta carpeta con su nombre
    (<stem>.<fmt> o <stem> (<6 hex>).<fmt>); con varios, primero los de `first` (el normal y el propio de ahora)."""
    name = re.compile(re.escape(stem) + r"( \([0-9a-f]{6}\))?\." + re.escape(fmt), re.I if os.name == "nt" else 0)
    here = os.path.normcase(os.path.normpath(folder))
    first = [os.path.normcase(p) for p in first]
    hits = [p for p, _ in entries if os.path.normcase(os.path.normpath(os.path.dirname(p))) == here
            and name.fullmatch(os.path.basename(p)) and os.path.isfile(p)]
    hits.sort(key=lambda p: (first.index(os.path.normcase(p)) if os.path.normcase(p) in first else len(first), p))
    return hits[0] if hits else None


def dest_path(destino, doc, registro=None):
    """Carpeta final (con la subcarpeta del proyecto si el destino lo pide y la reunion tiene proyecto) y fichero.
    Con el registro: el documento que ya es de esta reunion si sigue ahi, tambien el que tenia antes de moverla o de
    renombrar su carpeta (Registro.moved lo sigue, aunque alt_name, que sale de la ruta, ya no sea el mismo); si no,
    <stem>.<fmt> salvo que ese nombre sea de otra reunion (registro u origen que dice el propio documento) o de un
    fichero ajeno: entonces alt_for."""
    folder = destino["path"]
    project = (doc.get("props") or {}).get("proyecto")
    if destino.get("by_project") and project:
        try:
            folder = os.path.join(folder, clean_name(project))
        except ApiError:
            pass
    fmt, stem, rel = destino["format"], doc.get("stem"), doc.get("source")
    plain = os.path.join(folder, f"{stem}.{fmt}")
    if registro is None or not rel:
        return folder, plain
    own = _own_doc(registro.entries(rel), folder, stem, fmt, (plain, alt_name(folder, stem, rel, fmt)))
    if own:
        return folder, own
    alt = alt_for(registro, folder, stem, rel, fmt)
    if os.path.isfile(alt):
        return folder, alt  # su nombre propio ya esta ahi aunque el registro no lo sepa (otro PC, reinstalacion)
    if not os.path.isfile(plain):
        return folder, plain
    owner = registro.rel_of(plain)
    if owner:
        return folder, plain if owner == rel else alt
    if not is_ours(plain, fmt):
        return folder, alt  # un fichero tuyo con ese nombre: se queda como esta y el documento va al lado
    origin = origin_of(plain, fmt)
    return folder, alt if origin and origin != rel else plain


def _replace(dest, data):
    """Escribe en un .tmp propio (dos escrituras a la vez no comparten fichero) y lo pone en su sitio; si el documento
    esta abierto (Word, OneDrive subiendolo) se reintenta un rato. El .tmp nunca se queda en el destino."""
    tmp = f"{dest}.{os.getpid()}-{threading.get_ident()}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, dest)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.25)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def write_one(doc, destino, registro, blocked=None):
    """Escribe el documento en un destino. Devuelve (ruta, accion): escrito | igual | ajeno | cambiado | sin-carpeta |
    junto-al-audio | dentro-de-la-app. ajeno = existe y no lleva nuestra marca; cambiado = es nuestro pero alguien lo
    cambio en el destino, o lleva la marca sin constar en el registro (otro PC, reinstalacion) y no dice lo mismo;
    junto-al-audio = ese nombre es el de un sidecar de una grabacion de esa carpeta; dentro-de-la-app = `blocked`
    (server.py: la carpeta de la app, su raiz o una vinculada) dice que ahi no se escribe."""
    fmt = destino["format"]
    if not os.path.isdir(destino["path"]):
        return dest_path(destino, doc)[1], "sin-carpeta"
    with _lock_for(destino["path"]):
        folder, plain = dest_path(destino, doc)
        if blocked and blocked(folder):
            return plain, "dentro-de-la-app"
        if sidecar_of(plain):  # en esa carpeta hay una grabacion con ese nombre: ahi no va ningun documento suyo
            return plain, "junto-al-audio"
        folder, dest = dest_path(destino, doc, registro)
        if dest != plain and sidecar_of(dest):
            return dest, "junto-al-audio"
        data = render(doc, fmt)
        sha = hashlib.sha256(data).hexdigest()
        rel = doc.get("source")
        if os.path.isfile(dest):
            have = sha256_of(dest)
            if have == sha:
                registro.set(dest, sha, rel)
                return dest, "igual"
            if not is_ours(dest, fmt):
                return dest, "ajeno"
            before, owner = registro.get(dest), registro.rel_of(dest)
            if same_content(dest, data, fmt):  # solo cambian las fechas de generacion: no se toca
                registro.set(dest, have, rel)
                return dest, "igual"
            if before is None or before != have or (owner and rel and owner != rel):
                return dest, "cambiado"  # tambien si es de otra reunion (alt_for sin nombre libre): nunca encima
        os.makedirs(folder, exist_ok=True)
        _replace(dest, data)
        registro.set(dest, sha, rel)
        return dest, "escrito"


def write_all(doc, destinos, registro, log=None, blocked=None):
    """El documento en todos los destinos. Devuelve [(ruta, accion)] y lo resume en el log."""
    done = []
    for d in destinos_de(destinos):
        try:
            done.append(write_one(doc, d, registro, blocked))
        except OSError as e:
            done.append((dest_path(d, doc)[1], f"error: {e.__class__.__name__}"))
    if log and done:
        escritos = [p for p, k in done if k == "escrito"]
        avisos = [f"{os.path.basename(p)}: {k}" for p, k in done if k not in ("escrito", "igual")]
        if escritos or avisos:
            log(f"   {doc.get('stem')}: documento escrito en {len(escritos)} destino(s)"
                + (f" · sin tocar: {'; '.join(avisos)}" if avisos else ""))
    return done
