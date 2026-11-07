# -*- coding: utf-8 -*-
"""Errores en una frase: qué ha pasado, qué hacer y, si hay uno, el botón que lo hace (07/11/2026).

Bernardo se perdía en la configuración entre errores como «HTTP 401: …», «ConnectionError» o «No module named …»
(msg 1169). Esta función los convierte en lo que ve la interfaz: la Cola, los avisos, «Comprobar» y «Probar».
- Las frases que ya dan engines.connection_error y engines.cloud_http_error (descarga de modelos y nube) se dejan
  como están y solo se les añade el botón. No se duplican aquí.
- Las de Claude Code, la instalación y el proxy que contesta con HTML tienen aquí su frase.
- El texto técnico original se conserva en «tecnico» (para server.log y el diagnóstico).
Sin dependencias: se prueba sola (tests/test_ux_configurar.py)."""
import re

# acción -> etiqueta del botón (la interfaz decide qué hace cada una)
ACCIONES = {
    "reintentar": "Probar otra vez",
    "abrir_powershell": "Abrir PowerShell",
    "abrir_url": "Abrir {host}",
    "asistente": "Configurar Chuleta",
    "avanzado": "Ir a Avanzado",
    "procesado": "Revisar en Ajustes",
    "diagnostico": "Copiar diagnóstico",
    "reintentar_descarga": "Reintentar",
}

# Mensajes con los que Claude Code avisa de que el plan ha llegado a su límite (salen del CLI por stdout con código 1;
# la lista es la que usa el daemon de OpenClaude, sacada del propio CLI). En minúsculas.
LIMITE_CLAUDE = ("you've hit your", "you've reached your", "you're out of usage credits", "you're out of extra usage",
                 "requires usage credits", "your org is out of usage", "usage limit reached")
INSTALACION = ("no module named", "importerror", "dll load failed", "modulenotfounderror")
CLAUDE_FALLO = ("claude code ha fallado", "no responde a --version", "ha contestado algo inesperado",
                "no se ha podido lanzar claude code", "no ha devuelto texto")


def _accion(tipo, url=None, host=None):
    a = {"tipo": tipo, "etiqueta": ACCIONES[tipo].format(host=host or "")}
    if url:
        a["url"] = url
    return a


def _detalle(texto, n=120):
    """La primera línea útil del error técnico (sin el «Claude Code ha fallado (código 1):» de delante)."""
    t = texto.split(": ", 1)[1] if ": " in texto else texto
    t = " ".join(t.strip().splitlines()[0].split()) if t.strip() else ""
    return t if len(t) <= n else t[:n - 1] + "…"


def explicar(err, proveedor=None):
    """{frase, accion, tecnico} de un error (excepción o texto). accion: None o {tipo, etiqueta[, url]}.
    proveedor: nombre del proveedor de la nube («Groq»…), para las frases que lo necesitan."""
    tecnico = str(err or "").strip()
    if isinstance(err, BaseException) and not tecnico:
        tecnico = err.__class__.__name__
    names = {c.__name__ for c in type(err).__mro__} if isinstance(err, BaseException) else set()
    low = tecnico.lower()
    out = {"frase": tecnico, "accion": None, "tecnico": tecnico}
    if not tecnico:
        return out

    # --- Claude Code
    if any(k in low for k in LIMITE_CLAUDE) and ("limit" in low or "credits" in low or "usage" in low):
        m = re.search(r"resets? ([^\n·|.]+)", tecnico, re.I)
        cuando = f" (se libera: {m.group(1).strip()})" if m else ""
        out["frase"] = (f"Tu plan de Claude ha llegado a su límite de uso{cuando}: podrás resumir cuando se libere, y "
                        "mientras tanto se sigue transcribiendo.")
        return out
    if "sin sesión iniciada" in low or "please run /login" in low or "not logged in" in low:
        out["frase"] = ("Claude Code no tiene la sesión iniciada: en PowerShell escribe claude, entra con tu cuenta y "
                        "vuelve a probar.")
        out["accion"] = _accion("abrir_powershell")
        return out
    if "no encuentro claude code" in low:
        out["frase"] = ("No encuentro Claude Code en este PC: instálalo (paso 2 de «Configurar Chuleta») y vuelve a "
                        "probar.")
        out["accion"] = _accion("asistente")
        return out
    m = re.search(r"claude code no ha contestado en (\d+)", low)
    if m:
        out["frase"] = f"Claude no ha contestado en {m.group(1)} s: mira si tienes internet y prueba otra vez."
        out["accion"] = _accion("reintentar")
        return out
    if any(k in low for k in CLAUDE_FALLO):
        det = _detalle(tecnico)
        out["frase"] = ("Claude Code ha dado un error" + (f" ({det})" if det else "")
                        + ": si se repite, copia el diagnóstico en Ajustes → Ayuda.")
        out["accion"] = _accion("diagnostico")
        return out

    # --- instalación de la app (lo que instala INSTALAR.bat)
    if names & {"ImportError", "ModuleNotFoundError"} or any(k in low for k in INSTALACION):
        out["frase"] = ("Falta una pieza de la instalación: vuelve a ejecutar INSTALAR.bat (tus grabaciones no se "
                        "tocan) y prueba otra vez.")
        out["accion"] = _accion("diagnostico")
        return out

    # --- un proxy o un portal de la red contesta con una página en vez de la API (engines.json_body)
    if "respondió sin json" in low:
        donde = f"de {proveedor}" if proveedor else "del servicio"
        out["frase"] = (f"La red de tu empresa contesta en lugar {donde}: prueba fuera de la VPN o pide a IT que lo "
                        "permita.")
        out["accion"] = _accion("reintentar")
        return out

    # --- frases que ya vienen hechas (engines.connection_error, engines.cloud_http_error, WhisperModels): el botón
    if "huggingface" in low or ("modelo" in low and ("descarg" in low or "espacio" in low)):
        out["accion"] = _accion("reintentar_descarga")
        return out
    m = re.search(r"desde ((?:console|platform)\.[a-z.]+/[\w/-]*keys)", tecnico)
    if m:  # 401: la clave no vale -> la página de claves del proveedor
        out["accion"] = _accion("abrir_url", url="https://" + m.group(1), host=m.group(1).split("/")[0])
        return out
    if "no encuentra el modelo" in low:  # el modelo y la dirección están en Ajustes › Procesado (o en el paso 2)
        out["accion"] = _accion("procesado")
        return out
    if "minutos por trozo" in low:
        out["accion"] = _accion("avanzado")
        return out
    if any(k in low for k in ("vuelve a probar", "está fallando", "pide esperar", "comprueba la conexión",
                              "otra red", "no contesta")):
        out["accion"] = _accion("reintentar")
    return out
