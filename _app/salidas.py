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
linea del .txt, docProps/core.xml del .docx) y se recuerda el hash de lo escrito (registro en %LOCALAPPDATA%): un
fichero ajeno, o uno nuestro que alguien haya cambiado en el destino, se deja como esta y se avisa en el log.
"""
import hashlib
import json
import os
import re
import threading
import time
import zipfile
from xml.sax.saxutils import escape

from common import ApiError, clean_name

FORMATS = ("md", "txt", "docx")
FORMAT_LABELS = {"md": "Markdown (.md)", "txt": "Texto (.txt)", "docx": "Word (.docx)"}
MARK = "generado_por"  # marca de origen: clave de la cabecera .md, primera linea del .txt y keywords del .docx
MAX_DESTINOS = 10
_HEAD_BYTES = 4096


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


def check_destinos(value, app_dir=None):
    """Valida lo que llega de la interfaz y lo devuelve normalizado (sin repetidos)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError("Destinos de salida no válidos.")
    out, seen = [], set()
    for d in value:
        if not isinstance(d, dict):
            raise ApiError("Destino de salida no válido.")
        path = os.path.normpath(os.path.expandvars(str(d.get("path") or "").strip().strip('"')))
        if not path or not os.path.isabs(path) or not os.path.isdir(path):
            raise ApiError("Esa carpeta no existe: " + (path or "(vacía)"))
        if os.path.splitdrive(path)[1] in ("", os.sep):
            raise ApiError("Elige una carpeta, no una unidad entera.")
        if app_dir and os.path.normcase(path).startswith(os.path.normcase(app_dir)):
            raise ApiError("Esa carpeta es la de la propia app.")
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
    """destino -> sha256 de lo que escribimos la ultima vez (%LOCALAPPDATA%\\AudiosApp\\salidas.json)."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, dest):
        with self.lock:
            return self.data.get(os.path.normcase(dest))

    def set(self, dest, sha):
        with self.lock:
            self.data[os.path.normcase(dest)] = sha
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=0)
            os.replace(tmp, self.path)


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


def dest_path(destino, doc):
    """Carpeta final (con la subcarpeta del proyecto si el destino lo pide y la reunion tiene proyecto) y fichero."""
    folder = destino["path"]
    project = (doc.get("props") or {}).get("proyecto")
    if destino.get("by_project") and project:
        try:
            folder = os.path.join(folder, clean_name(project))
        except ApiError:
            pass
    return folder, os.path.join(folder, f"{doc.get('stem')}.{destino['format']}")


def write_one(doc, destino, registro):
    """Escribe el documento en un destino. Devuelve (ruta, accion): escrito | igual | ajeno | cambiado | sin-carpeta.
    ajeno = existe y no lleva nuestra marca; cambiado = es nuestro pero alguien lo cambio en el destino."""
    fmt = destino["format"]
    folder, dest = dest_path(destino, doc)
    if not os.path.isdir(destino["path"]):
        return dest, "sin-carpeta"
    data = render(doc, fmt)
    sha = hashlib.sha256(data).hexdigest()
    if os.path.isfile(dest):
        have = sha256_of(dest)
        if have == sha:
            registro.set(dest, sha)
            return dest, "igual"
        if not is_ours(dest, fmt):
            return dest, "ajeno"
        before = registro.get(dest)
        if before is not None and before != have:
            return dest, "cambiado"
    os.makedirs(folder, exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    registro.set(dest, sha)
    return dest, "escrito"


def write_all(doc, destinos, registro, log=None):
    """El documento en todos los destinos. Devuelve [(ruta, accion)] y lo resume en el log."""
    done = []
    for d in destinos_de(destinos):
        try:
            done.append(write_one(doc, d, registro))
        except OSError as e:
            done.append((d["path"], f"error: {e.__class__.__name__}"))
    if log and done:
        escritos = [p for p, k in done if k == "escrito"]
        avisos = [f"{os.path.basename(p)}: {k}" for p, k in done if k not in ("escrito", "igual")]
        if escritos or avisos:
            log(f"   {doc.get('stem')}: documento escrito en {len(escritos)} destino(s)"
                + (f" · sin tocar: {'; '.join(avisos)}" if avisos else ""))
    return done
