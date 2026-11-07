# -*- coding: utf-8 -*-
"""Plan de grabación: lo que se va a usar al grabar y de dónde sale cada valor (rediseño B1, 07/11/2026).

Un solo cálculo para la tarjeta de Grabar, Ctrl+Alt+R, el aviso flotante de Teams, Inicio y Ajustes › Al grabar
(POST /api/grabar/plan). Antes, la regla estaba escrita dos veces (perfil.js para el diálogo y rec_toggle para el
atajo) y nada obligaba a que dieran lo mismo. Ahora lo que se ve en la tarjeta es el cuerpo exacto de /api/rec/start.

Capas, de más a menos fuerte: esta reunión (lo que se cambie en la tarjeta) › workstream › proyecto › Ajustes.
«La última vez» ya no es un valor por defecto escondido: solo propone el proyecto, y siempre con su motivo.

El proyecto (D2 del rediseño), en este orden: lo elegido en la tarjeta › el tema fijo de Ajustes (perfil.vocab, de
versiones anteriores) › la misma reunión grabada antes (historial local) › las palabras del proyecto en el título
(calendario.suggest) › la última grabación.

Lógica pura: el servidor le pasa ajustes, listas de vocabulario, la sugerencia de la reunión en curso, el historial y
la carpeta que decide rec_folder_for (la frontera con la rama de carpetas: aquí no se decide dónde se guarda).
"""
from datetime import datetime

from common import APP_NAME, fold

# etiquetas cortas de los idiomas (las largas, con su explicación, están en app.js: LANGS)
IDIOMAS = {"es-en": "Español + inglés", "": "Detectar el idioma", "auto": "Detectar el idioma", "es": "Español",
           "en": "Inglés", "pt": "Portugués", "fr": "Francés", "it": "Italiano", "de": "Alemán", "ca": "Catalán",
           "es-pt": "Español + portugués", "en-pt": "Inglés + portugués"}
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
CLAVES_CAMBIOS = ("carpeta", "idioma", "contexto", "mic", "sys", "live", "preset")
ALCANCES = ("reunion", "proyecto", "workstream", "ajustes")


def idioma_txt(code):
    code = (code or "").strip().lower()
    return IDIOMAS.get(code, code or IDIOMAS[""])


def fecha_corta(iso):
    """«vie 30 oct» a partir de «2026-10-30T10:00:00» (vacío si no se entiende)."""
    try:
        d = datetime.fromisoformat(str(iso or "")[:19])
    except ValueError:
        return ""
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"


def norm_titulo(s):
    return " ".join(fold(s or "").split())


def ruta_txt(rel, links=None):
    """Cómo se enseña una carpeta interna: «Chuleta\\Proyecto Alfa\\Reuniones\\» o, en una vinculada,
    «<su nombre>\\sub\\». `links`: id -> nombre de las vinculadas."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if rel.startswith(":"):
        lid, _, rest = rel[1:].partition("/")
        head = (links or {}).get(lid) or "Carpeta vinculada"
        parts = [head] + [p for p in rest.split("/") if p]
    else:
        parts = [APP_NAME] + [p for p in rel.split("/") if p]
    return "\\".join(parts) + "\\"


# ----------------------------------------------------------------------------- historial

def historial_de(flat, listas=None, limite=600):
    """Las reuniones de la biblioteca como historial, de la más reciente a la más antigua: {titulo, fecha, vocab,
    proyecto, workstream}. El título es el de su .md o el nombre del archivo sin la fecha. Sin el id de su lista en
    este PC (grabada en otro, o de antes), la lista sale de los nombres de su cabecera si solo una se llama así."""
    por_nombre = {}
    for v in listas or []:
        por_nombre.setdefault((fold(v["project"]), fold(v["name"])), []).append(v["id"])
    out = []
    for a in flat or []:
        props = a.get("props") or {}
        titulo = " ".join(str(props.get("titulo") or a.get("label") or "").split())
        if not titulo:
            continue
        vid = props.get("vocabulario") or ""
        if not vid and props.get("workstream"):
            ids = por_nombre.get((fold(props.get("proyecto")), fold(props.get("workstream")))) or []
            vid = ids[0] if len(ids) == 1 else ""
        out.append({"titulo": titulo, "fecha": a.get("date") or "", "vocab": vid,
                    "proyecto": props.get("proyecto") or "", "workstream": props.get("workstream") or ""})
    out.sort(key=lambda h: h["fecha"], reverse=True)
    return out[:limite]


def recientes(historial, listas_por_id, limite=40):
    """Reuniones distintas por título para el autocompletado, de la más reciente: {titulo, n, fecha, vocab,
    proyecto_txt}. El proyecto es el de la última vez que se grabó con proyecto."""
    vistos, out = {}, []
    for h in historial or []:
        k = norm_titulo(h["titulo"])
        r = vistos.get(k)
        if r is None:
            r = vistos[k] = {"titulo": h["titulo"], "n": 0, "fecha": h["fecha"], "vocab": "", "proyecto_txt": ""}
            out.append(r)
        r["n"] += 1
        if not r["vocab"] and h["vocab"] in listas_por_id:
            v = listas_por_id[h["vocab"]]
            r["vocab"], r["proyecto_txt"] = v["id"], proyecto_txt(v)
    return out[:limite]


def proyecto_txt(v):
    return f"{v['project']} › {v['name']}" if v else "Sin proyecto"


# ----------------------------------------------------------------------------- el plan

def _linea(clave, etiqueta, valor, origen, origen_txt, **extra):
    return dict({"clave": clave, "etiqueta": etiqueta, "valor": valor, "origen": origen, "origen_txt": origen_txt},
                **extra)


def resolver(st, listas, sug=None, historial=None, pedido=None, carpeta_de=None, carpeta_origen=None,
             sugerir_titulo=None, motores=None, links=None, carpeta_reunion=None):
    """El plan efectivo de una grabación nueva.

    st: ajustes. listas: [{id, project, name, n}] (n = términos). sug: la reunión en curso (calendario.suggest:
    name, vocab, source, participants). historial: historial_de(). pedido: lo que trae la tarjeta, {nombre?, vocab?,
    participantes?, cambios: {carpeta?, idioma?, contexto?, mic?, sys?, live?, preset?}}; una clave presente es
    «elegido para esta reunión». carpeta_de(v): la carpeta donde se guarda (rec_folder_for, sin la de la reunión).
    carpeta_origen(v, rel): (origen, texto) de esa carpeta. sugerir_titulo(nombre): calendario.suggest sobre un
    nombre escrito. motores: {presets: {id: {...}}, procesado, agente, proveedor, resumen_auto, resumen_local}.
    links: id -> nombre de las carpetas vinculadas. carpeta_reunion(base, nombre): la carpeta de la reunión dentro
    de la base, o None (rama de carpetas; sin ella, la grabación va en la base).
    """
    st, sug, pedido = st or {}, dict(sug or {}), dict(pedido or {})
    cambios = {k: v for k, v in (pedido.get("cambios") or {}).items() if k in CLAVES_CAMBIOS}
    por_id = {v["id"]: v for v in listas or []}
    historial = historial or []
    motores = motores or {}

    # --- nombre: lo escrito › Teams/Outlook › nada
    if "nombre" in pedido:
        nombre = " ".join(str(pedido.get("nombre") or "").split())[:120]
        n_origen = "elegido" if nombre else ""
        if nombre and norm_titulo(nombre) == norm_titulo(sug.get("name")):
            n_origen = sug.get("source") or "teams"  # el de Teams sin tocar
    elif sug.get("name"):
        nombre, n_origen = " ".join(str(sug["name"]).split())[:120], sug.get("source") or "teams"
    else:
        nombre, n_origen = "", ""
    n_txt = {"teams": "de Teams", "outlook": "de Outlook", "elegido": "escrito por ti"}.get(n_origen, "")
    if n_origen == "elegido" and any(norm_titulo(h["titulo"]) == norm_titulo(nombre) for h in historial):
        n_origen, n_txt = "historial", "de tus reuniones"

    # --- proyecto › workstream (la lista de vocabulario)
    vid, p_origen, motivo = None, "ninguno", ""
    if "vocab" in pedido:
        want = str(pedido.get("vocab") or "")
        if not want or want in por_id:
            vid, p_origen, motivo = want, "elegido", "elegido por ti" if want else "sin proyecto, elegido por ti"
    if vid is None:
        fijo = (st.get("perfil") or {}).get("vocab")
        if isinstance(fijo, str) and (fijo == "" or fijo in por_id):
            vid, p_origen, motivo = fijo, "ajustes", "fijado en Ajustes"
    if vid is None and nombre:
        k = norm_titulo(nombre)
        h = next((h for h in historial if norm_titulo(h["titulo"]) == k and h["vocab"] in por_id), None)
        if h:
            f = fecha_corta(h["fecha"])
            vid, p_origen = h["vocab"], "historial"
            motivo = f"como la última «{h['titulo']}»" + (f" ({f})" if f else "")
    if vid is None:
        tv = ""
        if n_origen in ("teams", "outlook") and sug.get("vocab") in por_id:
            tv = sug["vocab"]
        elif nombre and sugerir_titulo:
            try:
                tv = (sugerir_titulo(nombre) or {}).get("vocab") or ""
            except Exception:
                tv = ""
        if tv in por_id:
            vid, p_origen = tv, "titulo"
            motivo = "por el título de la reunión" if n_origen != "outlook" else "por la cita de Outlook"
    if vid is None:
        last = st.get("rec_vocab") or ""
        if last in por_id:
            h = next((h for h in historial if h["vocab"] == last), None)
            f = fecha_corta(h["fecha"]) if h else ""
            vid, p_origen, motivo = last, "ultima", "como la última grabación" + (f" ({f})" if f else "")
    if vid is None:
        vid, p_origen, motivo = "", "ninguno", "ninguna reunión anterior ni el título lo dicen"
    v = por_id.get(vid) if vid else None

    lineas = []

    # --- carpeta: esta reunión › rec_folder_for (workstream › proyecto › Ajustes › la última usada)
    if "carpeta" in cambios:
        base = str(cambios["carpeta"] or "").replace("\\", "/").strip("/")
        c_origen, c_txt = "reunion", "cambiada para esta reunión"
    else:
        base = carpeta_de(v) if carpeta_de else ""
        c_origen, c_txt = carpeta_origen(v, base) if carpeta_origen else ("ajustes", "Ajustes › Al grabar")
    sub = carpeta_reunion(base, nombre) if carpeta_reunion else None
    lineas.append(_linea("carpeta", "Se guarda en", ruta_txt(sub if sub is not None else base, links), c_origen, c_txt,
                         rel=base, sub=sub))

    # --- idioma: esta reunión › workstream › proyecto › Ajustes
    if "idioma" in cambios:
        lang, l_origen, l_txt = str(cambios["idioma"] or "").strip().lower(), "reunion", "cambiado para esta reunión"
    elif v and v["id"] in (st.get("workstream_langs") or {}):
        lang, l_origen, l_txt = st["workstream_langs"][v["id"]], "workstream", "del workstream"
    elif v and v["project"] in (st.get("project_langs") or {}):
        lang, l_origen, l_txt = st["project_langs"][v["project"]], "proyecto", f"de {v['project']}"
    else:
        lang, l_origen, l_txt = st.get("language") or "", "ajustes", "Ajustes › Al grabar"
    lang = "" if lang == "auto" else lang
    lineas.append(_linea("idioma", "Idioma", idioma_txt(lang), l_origen, l_txt, code=lang))

    # --- vocabulario: el del workstream o, sin proyecto, el general de Ajustes
    if v:
        n = v.get("n")
        lineas.append(_linea("vocab", "Vocabulario", f"{v['name']} de {v['project']}"
                             + (f" · {n} {'término' if n == 1 else 'términos'}" if n is not None else ""),
                             "workstream", "del workstream", id=v["id"]))
    else:
        general = [t for t in str(st.get("prompt") or "").replace(";", ",").split(",") if t.strip()]
        lineas.append(_linea("vocab", "Vocabulario", f"El general · {len(general)} términos" if general else "Ninguno",
                             "ajustes", "Ajustes › Proyectos", id=""))

    # --- contexto: esta reunión › Ajustes
    if "contexto" in cambios:
        ctx, x_origen, x_txt = str(cambios["contexto"] or ""), "reunion", "cambiado para esta reunión"
    else:
        ctx = str((st.get("perfil") or {}).get("context") or "")
        x_origen, x_txt = "ajustes", "Ajustes › Al grabar"
    ctx = " ".join(ctx.split())[:500]
    lineas.append(_linea("contexto", "Contexto", ctx, x_origen, x_txt))

    # --- transcribe: en directo o solo grabar, con qué motor
    presets = motores.get("presets") or {}
    live = bool(cambios["live"]) if "live" in cambios else st.get("rec_directo", True) is not False
    preset = str(cambios.get("preset") or st.get("live_preset") or "medium")
    if preset not in presets and presets:
        preset = "medium" if "medium" in presets else next(iter(presets))
    t_origen = "reunion" if ("live" in cambios or "preset" in cambios) else "ajustes"
    t_txt = "cambiado para esta reunión" if t_origen == "reunion" else "Ajustes › Al grabar"
    # con «Solo grabar» transcribe la cola al terminar, con el motor de después (Ajustes › Procesado)
    p_tx = presets.get(preset) if live else presets.get(st.get("preset") or "rapido")
    lineas.append(_linea("transcribe", "Transcribe", transcribe_txt(live, p_tx or {}, motores),
                         t_origen, t_txt, live=live, preset=preset))

    # --- resume: lo decide Ajustes › Procesado (no hay cambio por reunión)
    lineas.append(_linea("resume", "Resume", resume_txt(p_tx or {}, motores), "procesado", "Ajustes › Procesado"))

    # --- audio: micrófono y audio del PC
    mic = str(cambios["mic"]) if "mic" in cambios else (st.get("rec_mic") or "default")
    sys_dev = str(cambios["sys"]) if "sys" in cambios else (st.get("rec_sys") or "all")
    a_origen = "reunion" if ("mic" in cambios or "sys" in cambios) else "ajustes"
    lineas.append(_linea("audio", "Audio", audio_txt(mic, sys_dev), a_origen,
                         "cambiado para esta reunión" if a_origen == "reunion" else "Ajustes › Al grabar",
                         mic=mic, sys=sys_dev))

    # --- participantes: los de la cita de Outlook (sin confirmar) salvo que la tarjeta los quite
    if "participantes" in pedido:
        part, sugerido = " ".join(str(pedido.get("participantes") or "").split()), ""
    else:
        part = ", ".join(sug.get("participants") or []) if n_origen in ("teams", "outlook") else ""
        sugerido = (sug.get("source") or "") if part else ""

    rec = {"name": nombre, "vocab": vid, "folder": base, "language": lang, "contexto": ctx, "mic": mic,
           "sys": sys_dev, "live": live, "preset": preset, "participantes": part, "sugerido": sugerido, "plan": True}
    proyecto = {"vocab": vid, "texto": proyecto_txt(v), "proyecto": v["project"] if v else "",
                "workstream": v["name"] if v else "", "origen": p_origen, "motivo": motivo}
    plan = {"nombre": {"valor": nombre, "origen": n_origen, "texto": n_txt}, "proyecto": proyecto,
            "lineas": lineas, "rec": rec, "fuente": sug.get("source") or "",
            # la reunión en curso tal cual: la tarjeta la propone («Teams dice …») si ya has escrito otra cosa
            "sugerencia": {"nombre": " ".join(str(sug.get("name") or "").split())[:120],
                           "fuente": sug.get("source") or ""}}
    plan["resumen"] = resumen(plan)
    return plan


def transcribe_txt(live, p, motores):
    nube = p.get("engine") == "cloud"
    donde = f"{motores.get('proveedor') or 'la nube'}" if nube else "Este PC"
    nombre = "" if nube else (p.get("live_label") or p.get("label") or "").split(" · ")[0]
    return f"{donde}, {'en directo' if live else 'al terminar'}" + (f" · {nombre}" if nombre else "")


def resume_txt(p, motores):
    modo = motores.get("procesado") or "local"
    if modo == "local":
        return "No se resume: todo se queda en este PC"
    quien = f"{motores.get('agente') or 'Claude'} con tu cuenta" if modo == "agente" else (
        motores.get("proveedor") or "la nube")
    nube = p.get("engine") == "cloud"
    auto = motores.get("resumen_auto", True) and (modo != "api" or nube or motores.get("resumen_local"))
    return f"{quien}, al terminar" if auto else f"{quien}, cuando lo pidas"


def audio_txt(mic, sys_dev):
    m = {"none": "Sin micrófono", "default": "Tu micro"}.get(mic, "Un micro fijo")
    s = {"none": "sin el audio del PC", "all": "todo lo que suena en el PC", "default": "la salida del PC"}.get(
        sys_dev, "una salida fija")
    return f"{m} · {s}"


def resumen(plan):
    """Una línea para el aviso de Ctrl+Alt+R, el toast y el chip: «Comité → Chuleta\\Alfa\\… · Español + inglés»."""
    by = {l["clave"]: l for l in plan["lineas"]}
    nombre = plan["nombre"]["valor"] or "Sin nombre"
    proy = plan["proyecto"]
    donde = proy["texto"] if proy["vocab"] else by["carpeta"]["valor"]
    return f"{nombre} → {donde} · {by['idioma']['valor']}"
