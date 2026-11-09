# -*- coding: utf-8 -*-
"""Reunión en curso: de dónde salen el nombre, el tema y los participantes que la app sugiere al grabar.

Dos niveles, los dos locales y de solo lectura:
  1. El título de la ventana de REUNIÓN de Teams (EnumWindows): no activa nada y vale en cualquier PC.
  2. Outlook clásico de escritorio por COM (pywin32, opcional): la cita del calendario que coincide con «ahora»
     (asunto, horas, organizador, asistentes, categorías). Solo si se enciende en Ajustes («Leer mi calendario»).
     `estado()` lo resume en una frase para Ajustes y «Probar» (GET /api/calendario/estado): {nivel, estado, frase,
     accion}, con la causa si no funciona (casilla apagada, Outlook nuevo o cerrado, sin pywin32, permisos…).
La lectura no sale del PC. Los asistentes sugeridos van a la cabecera del .md de la grabación con su origen
(participantes_origen) y, mientras el usuario no los confirma o los edita, tampoco salen de ahí: ni al resumen con IA
(que va a la nube) ni a los destinos de salida (server.participantes_confirmados). Confirmados, sí.

Fuera de Windows (y en los tests) cada lectura devuelve None con su motivo; la lógica pura (parse_title,
pick_meeting_window, elegir_cita, suggest, decidir_aviso) se prueba con dobles.
"""
import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from common import APP_NAME, BAD_NAME, WINDOWS, fold, log, log_exc

# ----------------------------------------------------------------------------- nivel 1: ventana de Teams

TEAMS_EXES = ("ms-teams.exe", "teams.exe", "msteams.exe")
# «Reunión semanal | Microsoft Teams», «… - Microsoft Teams», «… | Microsoft Teams (trabajo o escuela)»
_SUFIJO = re.compile(r"\s*[|\-–—·]\s*microsoft teams(?:\s+classic|\s+cl[aá]sico|\s*\([^)]*\))?\s*$", re.I)
_CONTADOR = re.compile(r"^\(\d+\)\s*")  # «(2) Chat | Microsoft Teams»: avisos sin leer
# primera pestaña de la ventana principal (ES / EN / PT) y otras ventanas de Teams que no son una reunión
VENTANAS_PRINCIPALES = frozenset(fold(x) for x in (
    "Chat", "Equipos", "Calendario", "Llamadas", "Actividad", "Archivos", "Aplicaciones", "OneDrive", "Más", "Tareas",
    "Planner", "Copilot", "Comunidades", "Notificaciones", "Configuración", "Ajustes", "Aprobaciones", "Turnos",
    "Viva", "Viva Engage", "Viva Connections", "Viva Insights", "Reservas", "Formularios", "Pizarra", "Loop",
    "Teams", "Calendar", "Calls", "Activity", "Files", "Apps", "More", "Tasks", "Settings", "Approvals", "Shifts",
    "Communities", "Notifications", "Bookings", "Forms", "Whiteboard", "People", "Personas", "Pessoas",
    "Atividade", "Conversa", "Conversas", "Equipes", "Equipas", "Calendário", "Chamadas", "Arquivos", "Ficheiros",
    "Aplicativos", "Aplicações", "Mais", "Tarefas", "Configurações", "Definições", "Aprovações", "Quadro",
))


def parse_title(title):
    """Título limpio de una ventana de REUNIÓN de Teams, o None si es la ventana principal u otra cosa.

    Exige el sufijo «| Microsoft Teams» (así no se cuela ninguna otra ventana del proceso) y descarta las
    pestañas de la ventana principal («Chat | …», «Calendario | …», «Microsoft Teams» a secas…).
    """
    t = _CONTADOR.sub("", " ".join((title or "").split()))
    m = _SUFIJO.search(t)
    if not m:
        return None
    core = t[:m.start()].strip(" -|·–—")
    parts = [p.strip() for p in re.split(r"\s\|\s", core) if p.strip()]
    if not parts or fold(parts[0]) in VENTANAS_PRINCIPALES:
        return None
    return " | ".join(parts)


def pick_meeting_window(windows, exe_names=TEAMS_EXES):
    """Entre (título, exe[, hwnd]) en orden Z (la primera es la de más arriba), la primera reunión de Teams."""
    exes = {e.lower() for e in exe_names}
    for w in windows:
        title, exe = w[0], (w[1] or "")
        if exe.replace("\\", "/").rsplit("/", 1)[-1].lower() in exes:  # ruta de Windows también en los tests (macOS)
            t = parse_title(title)
            if t:
                return t
    return None


def _ventanas():
    """(título, exe, hwnd) de las ventanas visibles con título, en orden Z (solo Windows)."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [PROC, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    out, exes = [], {}

    def exe_of(pid):
        if pid not in exes:
            exes[pid] = ""
            h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                try:
                    n = wintypes.DWORD(32768)
                    buf = ctypes.create_unicode_buffer(n.value)
                    if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                        exes[pid] = buf.value
                finally:
                    kernel32.CloseHandle(h)
        return exes[pid]

    @PROC
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if "teams" not in buf.value.lower():  # sin el sufijo no es una reunión: no hace falta abrir el proceso
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((buf.value, exe_of(pid.value), hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return out


def teams_meeting_title(exe_names=TEAMS_EXES):
    """Título de la ventana de reunión de Teams que está más arriba, o None (también fuera de Windows)."""
    if not WINDOWS:
        return None
    try:
        return pick_meeting_window(_ventanas(), exe_names)
    except Exception:
        log_exc()
        return None


# ----------------------------------------------------------------------------- nivel 2: Outlook clásico (COM)
#
# Lo que hay que saber de Outlook por COM (fuentes y mediciones: CHANGELOG y tests/test_calendario.py):
# - Solo el Outlook clásico (outlook.exe) tiene modelo de objetos; el nuevo (olk.exe) no.
# - pywin32 devuelve las fechas COM con la zona UTC pegada pero SIN convertirlas (PyTime.cpp de pywin32 312:
#   PyWinObject_FromDATE → PyWinObject_FromSYSTEMTIME, «SYSTEMTIME structures explicitly use UTC»), y Start/End de
#   Outlook van en hora local: se quita la etiqueta, no se convierte. Convertir (astimezone) sumaba el desfase
#   (+1 h en Madrid en invierno) y la reunión en curso quedaba fuera de la ventana: «no tiene ninguna reunión».
# - Organizer, RequiredAttendees, OptionalAttendees, Body y Recipients están protegidos (Object Model Guard): para un
#   programa de fuera, con un antivirus que Outlook no da por bueno o con la directiva de la empresa, leerlos abre el
#   aviso «Un programa está intentando obtener acceso…» (la llamada se queda esperando) o falla. Asunto, horas, lugar
#   y categorías no lo están: se leen primero, y lo protegido solo de la cita elegida.
# - Con citas periódicas: Sort("[Start]") → IncludeRecurrences = True → Restrict, en ese orden; nunca Count.
# - La fecha del filtro va en el formato regional del usuario y sin segundos; si Outlook no lo entiende (error, o
#   devuelve citas que no tocan la hora pedida), se prueba con ISO y con el de EE. UU.

OL_FOLDER_CALENDAR = 9
OL_REQUIRED, OL_OPTIONAL = 1, 2  # Recipient.Type (0 = organizador, 3 = recurso)
CANCELADAS = (5, 7)  # MeetingStatus: olMeetingCanceled, olMeetingReceivedAndCanceled
MAX_CITAS = 50  # con IncludeRecurrences el Count no vale y la colección puede no tener fin
MAX_CITAS_DIA = 200  # la agenda de hoy (estado y «Probar»)
MAX_DESTINATARIOS = 200  # una convocatoria enorme no se recorre entera
PROGID = "Outlook.Application"
LENTO = 2.0  # s: leer los asistentes tarda más que esto cuando Outlook ha preguntado (aviso de seguridad)
ESPERA_OTRA = 1.5  # s que se espera a que acabe otra consulta en marcha
_TEAMS_LOC = re.compile(r"microsoft teams|teams\.microsoft\.com|teams\.live\.com", re.I)
_TEAMS_URL = re.compile(r"https://(?:teams\.microsoft\.com|teams\.live\.com|[\w.-]*teams\.cloud\.microsoft)/", re.I)


def _hr(x):
    """HRESULT con signo, como lo da pywin32 (0x800401E3 → -2147221021)."""
    return x - (1 << 32) if x & 0x80000000 else x


MK_E_UNAVAILABLE = _hr(0x800401E3)  # GetActiveObject: Outlook no está en la tabla de objetos activos
CO_E_CLASSSTRING = _hr(0x800401F3)  # el ProgID Outlook.Application no existe (sin Outlook clásico; medido en CI)
REGDB_E_CLASSNOTREG = _hr(0x80040154)
CO_E_SERVER_EXEC_FAILURE = _hr(0x80080005)
CO_E_NOTINITIALIZED = _hr(0x800401F0)  # COM sin CoInitialize en el hilo
RPC_E_CALL_REJECTED = _hr(0x80010001)
RPC_E_SERVERCALL_RETRYLATER = _hr(0x8001010A)
RPC_E_DISCONNECTED = _hr(0x80010108)
RPC_S_SERVER_UNAVAILABLE = _hr(0x800706BA)
E_ACCESSDENIED = _hr(0x80070005)
E_ABORT = _hr(0x80004004)
DISP_E_EXCEPTION = _hr(0x80020009)
TYPE_E_LIBNOTREGISTERED = _hr(0x8002801D)

# lo que se le dice al usuario en cada estado (una frase, sin asuntos ni nombres)
FRASES = {
    "apagado": f"Tienes el Outlook clásico: activa el calendario y {APP_NAME} sugerirá el nombre y los asistentes de "
               "cada reunión.",
    "apagado_sin_saber": f"Activa el calendario de Outlook y {APP_NAME} sugerirá el nombre y los asistentes de cada "
                         "reunión.",
    "cerrado": f"Outlook clásico está cerrado: ábrelo y {APP_NAME} leerá tu calendario.",
    "outlook_nuevo": f"Usas el Outlook nuevo, que no deja leer tu agenda a otras aplicaciones: {APP_NAME} saca el "
                     "nombre de la reunión de la ventana de Teams.",
    "sin_outlook": f"Este PC no tiene el Outlook clásico: {APP_NAME} saca el nombre de la reunión de la ventana de "
                   "Teams.",
    "sin_pywin32": f"Falta pywin32, la librería con la que {APP_NAME} lee Outlook: vuelve a ejecutar INSTALAR.bat.",
    "sin_permiso": f"Outlook está abierto pero no deja conectarse a {APP_NAME}: espera unos segundos y vuelve a "
                   "probar; si sigue igual, cierra Outlook y ábrelo normal (no como administrador).",
    "esperando_permiso": f"Outlook te está preguntando si dejas que {APP_NAME} lea tu calendario: contesta en su "
                         "ventana («Permitir») y vuelve a probar.",
    "ocupado": "Outlook no responde (¿tiene una ventana o un aviso abiertos?): ciérralos y vuelve a probar.",
    "no_windows": "El calendario de Outlook solo se lee en Windows.",
    "libreria": "Office no tiene bien registrado Outlook para otras aplicaciones: repara Office desde «Aplicaciones "
                "instaladas» y vuelve a probar.",
}
_ERRORES = {  # HRESULT → estado
    MK_E_UNAVAILABLE: "cerrado", CO_E_SERVER_EXEC_FAILURE: "cerrado", RPC_E_DISCONNECTED: "cerrado",
    RPC_S_SERVER_UNAVAILABLE: "cerrado", CO_E_CLASSSTRING: "sin_outlook", REGDB_E_CLASSNOTREG: "sin_outlook",
    RPC_E_CALL_REJECTED: "ocupado", RPC_E_SERVERCALL_RETRYLATER: "ocupado", E_ACCESSDENIED: "sin_permiso",
    E_ABORT: "sin_permiso",
}
_ACCION = {"activar": {"id": "activar", "texto": "Activar"}, "probar": {"id": "probar", "texto": "Probar"}}

_COM_LOCK = threading.Lock()  # una consulta a la vez: si una se cuelga, las siguientes no se amontonan
_COM_FASE = {"fase": None, "desde": 0.0}  # qué hace la consulta en marcha (para decir por qué no contesta)
# ¿Outlook deja leer los asistentes sin preguntar? None = sin comprobar; "si"; "pregunta" (sale su aviso: solos no se
# leen más, solo con «Probar»); "deniega" (la empresa no deja). Lo guarda el servidor en settings.json (al_cambiar).
ASISTENTES = {"estado": None}
al_cambiar = None  # server.App: guarda ASISTENTES["estado"] en los ajustes


def _marcar_asistentes(v):
    if v not in ("si", "pregunta", "deniega") or ASISTENTES["estado"] == v:
        return
    ASISTENTES["estado"] = v
    log(f"   calendario: asistentes {v}")
    _ESTADO_CACHE.clear()
    if al_cambiar:
        try:
            al_cambiar(v)
        except Exception:
            log_exc()


class SYSTEMTIME(ctypes.Structure):
    """SYSTEMTIME de Win32: 8 WORD. `ctypes.wintypes` no la trae (con `wintypes.SYSTEMTIME` el AttributeError caía en
    el `except` y el filtro de Outlook iba siempre con el formato de EE. UU.)."""
    _fields_ = [(n, ctypes.c_ushort) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute",
                                               "wSecond", "wMilliseconds")]


_NLS = []  # (GetDateFormatEx, GetTimeFormatEx) con sus prototipos, en un kernel32 propio (no se tocan los de windll)


def _nls():
    if not _NLS:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fd, ft = k32.GetDateFormatEx, k32.GetTimeFormatEx
        fd.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(SYSTEMTIME), ctypes.c_wchar_p,
                       ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p]
        ft.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(SYSTEMTIME), ctypes.c_wchar_p,
                       ctypes.c_wchar_p, ctypes.c_int]
        fd.restype = ft.restype = ctypes.c_int
        _NLS.append((fd, ft))
    return _NLS[0]


def fecha_jet(dt, locale=None):
    """La fecha como la espera el filtro de Outlook: formato regional corto del usuario y hora SIN segundos
    (docs: «Filtering Items Using a Date-time Comparison»), p. ej. «31/10/2026 14:05» con el formato español.
    `locale` («es-ES»…): otro formato regional en vez del del usuario (None). Fuera de Windows, el de EE. UU."""
    dt = dt.replace(second=0, microsecond=0)
    if WINDOWS:
        try:
            get_date, get_time = _nls()
            st = SYSTEMTIME(dt.year, dt.month, dt.isoweekday() % 7, dt.day, dt.hour, dt.minute, 0, 0)
            d, t = ctypes.create_unicode_buffer(80), ctypes.create_unicode_buffer(80)
            if (get_date(locale, 0x1, ctypes.byref(st), None, d, 80, None)  # DATE_SHORTDATE
                    and get_time(locale, 0x2, ctypes.byref(st), None, t, 80)):  # TIME_NOSECONDS
                return f"{d.value} {t.value}"
            log(f"   Outlook: GetDateFormatEx/GetTimeFormatEx han fallado (error {ctypes.get_last_error()}): "
                "el filtro va con el formato de EE. UU.")
        except Exception:
            log_exc()
    return dt.strftime("%m/%d/%Y %H:%M")


def fecha_iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def fecha_eeuu(dt):
    return dt.strftime("%m/%d/%Y %I:%M %p")


# el primero es el que piden los docs; los otros dos, solo si Outlook no entiende el primero
FORMATOS = (("regional", fecha_jet), ("iso", fecha_iso), ("eeuu", fecha_eeuu))


# --- lo que se sabe sin hablar con Outlook (registro, procesos, pywin32): ni pide permisos ni tarda

def _reg(root, path, name="", vista=0):
    """Un valor del registro (name="" = el predeterminado), o None si no está (solo Windows)."""
    import winreg
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | vista) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _procesos():
    """Los ejecutables en marcha (nombres en minúsculas), o None si no se pueden leer (solo Windows)."""
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snap, first, nxt, close = (k32.CreateToolhelp32Snapshot, k32.Process32FirstW, k32.Process32NextW,
                               k32.CloseHandle)
    snap.restype, snap.argtypes = wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]
    first.argtypes = nxt.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    first.restype = nxt.restype = wintypes.BOOL
    close.argtypes = [wintypes.HANDLE]
    h = snap(0x2, 0)  # TH32CS_SNAPPROCESS
    if not h or h == wintypes.HANDLE(-1).value:
        return None
    try:
        e, out = PROCESSENTRY32W(), set()
        e.dwSize = ctypes.sizeof(e)
        ok = first(h, ctypes.byref(e))
        while ok:
            out.add(e.szExeFile.lower())
            ok = nxt(h, ctypes.byref(e))
        return frozenset(out)
    finally:
        close(h)


def _outlook_abierto():
    try:
        p = _procesos() if WINDOWS else None
    except Exception:
        log_exc()
        return None
    return None if p is None else "outlook.exe" in p


_POLITICA = []  # (cuándo, valor): la directiva no cambia mientras la app está abierta; se relee cada 10 min


def politica_asistentes():
    """Lo que dice la directiva de la empresa sobre los programas que leen direcciones en Outlook (Learn, «results for
    advanced diagnostic indicate offline scan was forced»: HKCU\\Software\\Policies\\Microsoft\\Office\\16.0\\Outlook
    \\Security, AdminSecurityMode = 3 y PromptOOMAddressInformationAccess / PromptOOMAddressBookAccess: 0 = denegar,
    1 = preguntar, 2 = aprobar): "deniega", "pregunta", "aprueba" o None (sin directiva: depende del antivirus)."""
    if _POLITICA and time.monotonic() - _POLITICA[0][0] < 600:
        return _POLITICA[0][1]
    v = None
    if WINDOWS:
        try:
            import winreg
            base = r"Software\Policies\Microsoft\Office\16.0\Outlook\Security"
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                if _reg(root, base, "AdminSecurityMode") != 3:
                    continue
                vals = [x for x in (_reg(root, base, "PromptOOMAddressInformationAccess"),
                                    _reg(root, base, "PromptOOMAddressBookAccess")) if isinstance(x, int)]
                if vals:
                    v = {0: "deniega", 1: "pregunta", 2: "aprueba"}.get(min(vals))
                    break
        except Exception:
            log_exc()
    _POLITICA[:] = [(time.monotonic(), v)]
    return v


def _bits_office():
    import winreg
    for path, name in ((r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration", "Platform"),
                       (r"SOFTWARE\Microsoft\Office\16.0\Outlook", "Bitness")):
        for vista in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            v = _reg(winreg.HKEY_LOCAL_MACHINE, path, name, vista)
            if v in ("x86", "x64", "arm64"):
                return v
    return None


def entorno_outlook():
    """Lo que se puede saber de Outlook en este PC sin hablar con él. Fuera de Windows, {"windows": False}."""
    if not WINDOWS:
        return {"windows": False}
    import importlib.util
    import struct
    import winreg
    e = {"windows": True, "python_bits": struct.calcsize("P") * 8}
    # sin importarlo: importar pythoncom inicializa COM en el hilo que lo importa (aquí, el de la petición HTTP). Si
    # está pero no carga (una DLL), lo dice la consulta, en su hilo: ImportError → «sin_pywin32»
    try:
        e["pywin32"] = all(importlib.util.find_spec(m) is not None for m in ("pythoncom", "pywintypes", "win32com"))
    except (ImportError, ValueError):
        e["pywin32"] = False
    try:
        e["instalado"] = bool(_reg(winreg.HKEY_CLASSES_ROOT, PROGID + r"\CLSID"))
        procs = _procesos()
        e["abierto"] = None if procs is None else "outlook.exe" in procs
        e["nuevo_abierto"] = None if procs is None else "olk.exe" in procs
        e["nuevo_activado"] = _reg(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\16.0\Outlook\Preferences",
                                   "UseNewOutlook") == 1
        e["office_bits"] = _bits_office()
        e["admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        log_exc()
    e["politica"] = politica_asistentes()
    return e


# --- la conversación con Outlook (en un hilo propio, con COM inicializado y con tope de tiempo)

def _codigo(e):
    """El HRESULT de una excepción COM; con DISP_E_EXCEPTION, el de dentro (excepinfo[5]) si lo trae."""
    hr = getattr(e, "hresult", None)
    if hr is None and getattr(e, "args", None) and isinstance(e.args[0], int):
        hr = e.args[0]
    if hr is None:
        return None
    exc = getattr(e, "excepinfo", None)
    if exc is None and len(getattr(e, "args", ())) > 2:
        exc = e.args[2]
    if hr == DISP_E_EXCEPTION and isinstance(exc, (tuple, list)) and len(exc) > 5 and exc[5]:
        return exc[5]
    return hr


_ENTRE_COMILLAS = re.compile(r"\"[^\"]*\"|'[^']*'|«[^»]*»|“[^”]*”")


def _texto(e):
    """El texto de un error de Outlook, sin lo que va entre comillas (ahí iría un asunto o un nombre) y corto."""
    exc = getattr(e, "excepinfo", None)
    if exc is None and len(getattr(e, "args", ())) > 2:
        exc = e.args[2]
    desc = exc[2] if isinstance(exc, (tuple, list)) and len(exc) > 2 and exc[2] else None
    texto = _ENTRE_COMILLAS.sub("«…»", str(desc or getattr(e, "strerror", None) or e))
    return " ".join(texto.split())[:160]


def clasificar(e, abierto=None):
    """(estado, frase) de lo que ha fallado al hablar con Outlook. `abierto`: si outlook.exe está en marcha (None = no
    se sabe): abierto y «no está en la tabla de objetos activos» es otro nivel de permisos, no Outlook cerrado."""
    if isinstance(e, ImportError):
        return "sin_pywin32", FRASES["sin_pywin32"]
    hr = _codigo(e)
    if hr is None:
        return "error", f"No se pudo leer Outlook ({e.__class__.__name__}: {_texto(e)})."
    if hr == TYPE_E_LIBNOTREGISTERED:
        return "error", f"{FRASES['libreria']} (0x{hr & 0xFFFFFFFF:08x})"
    estado = _ERRORES.get(hr)
    if estado == "cerrado" and abierto and hr in (MK_E_UNAVAILABLE, CO_E_SERVER_EXEC_FAILURE):
        estado = "sin_permiso"
    if estado:
        return estado, FRASES[estado]
    return "error", f"Outlook ha devuelto un error (0x{hr & 0xFFFFFFFF:08x}: {_texto(e)})."


def _conectar():
    """El Outlook clásico que ya está abierto, por IDispatch y sin la caché gen_py de pywin32 (una caché a medio
    escribir de otro programa rompe el Dispatch con caché). No lo arranca: sin Outlook, MK_E_UNAVAILABLE. Si está
    abierto pero aún no se ha registrado (recién arrancado), se espera un poco y se reintenta una vez."""
    import pythoncom
    import pywintypes
    import win32com.client.dynamic
    clsid = pywintypes.IID(PROGID)  # CO_E_CLASSSTRING si el Outlook clásico no está instalado
    for intento in (1, 2):
        try:
            unk = pythoncom.GetActiveObject(clsid)
            break
        except pythoncom.com_error as e:
            if intento == 2 or _codigo(e) != MK_E_UNAVAILABLE or not _outlook_abierto():
                raise
            time.sleep(1.5)
    return win32com.client.dynamic.Dispatch(unk.QueryInterface(pythoncom.IID_IDispatch))


def _sin_traza(e):
    """La excepción sin su traza: la traza guarda los marcos del hilo COM, y con ellos objetos de Outlook que se
    soltarían después de CoUninitialize, desde otro hilo."""
    e.__traceback__ = e.__context__ = e.__cause__ = None
    return e


def _en_com(trabajo, timeout, session=None, espera_protegidas=None, espera_otra=ESPERA_OTRA):
    """trabajo(app, caja) en un hilo propio con COM inicializado (STA) y tope de tiempo → la caja con lo que haya dado
    tiempo a leer: "cands" (lo no protegido, ya listo), "fin" (ha terminado), "err" (la excepción), "fase"
    ("conectar", "leer", "protegidas"). Si la consulta anterior sigue colgada, None. Nunca lanza.

    `espera_protegidas`: cuánto se espera a lo protegido (asistentes) una vez leído lo demás; si Outlook pregunta, la
    llamada se queda esperando a que el usuario conteste y se devuelve lo que ya hay (el hilo sigue y lo apunta)."""
    # otra consulta en marcha (el vigilante y Ajustes a la vez) suele acabar en menos de un segundo: se la espera un
    # poco antes de decir que Outlook no contesta; colgada (un aviso sin contestar), no se amontona otro hilo
    if not _COM_LOCK.acquire(timeout=min(espera_otra, timeout)):
        return None
    caja, fase1, listo = {"fase": "conectar", "fin": False}, threading.Event(), threading.Event()
    caja["_fase1"] = fase1
    _COM_FASE.update(fase="conectar", desde=time.monotonic())

    def run():
        com = False
        try:
            if session is None:
                # cada hilo que habla con COM lo inicializa una vez y lo cierra al final. Importar pythoncom por primera
                # vez ya llama a CoInitializeEx en el hilo que importa (PythonCOM.cpp de pywin32 312): ese no repite
                ya = "pythoncom" in sys.modules
                import pythoncom
                if ya:
                    pythoncom.CoInitialize()
                com = True
            app = (session or _conectar)()
            try:
                caja["fase"] = _COM_FASE["fase"] = "leer"
                trabajo(app, caja)
            finally:
                app = None  # noqa: F841 (suelta Outlook antes de CoUninitialize)
        except BaseException as e:  # noqa: B902 (se convierte en estado; nada escapa del hilo)
            caja["err"] = _sin_traza(e)
        finally:
            if com:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
            caja["fin"] = True
            _COM_FASE.update(fase=None, desde=0.0)
            _COM_LOCK.release()
            fase1.set()
            listo.set()

    try:
        threading.Thread(target=run, daemon=True, name="outlook-com").start()
    except RuntimeError:  # «can't start new thread»: el candado no se queda cogido para siempre
        log_exc()
        _COM_FASE.update(fase=None, desde=0.0)
        _COM_LOCK.release()
        return None
    t0 = time.monotonic()
    fase1.wait(timeout)
    if not listo.is_set() and caja.get("fase") == "protegidas":
        resto = max(0.0, timeout - (time.monotonic() - t0))
        listo.wait(resto if espera_protegidas is None else min(resto, espera_protegidas))
    return caja


def _local(d):
    """datetime naif con la hora local de Outlook. pywin32 entrega las fechas COM con la zona UTC pegada pero sin
    convertirlas (la hora es la que guarda Outlook, local): se quita la etiqueta. Los dobles de los tests, naifs."""
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, d.hour, d.minute, d.second)


def _nombre(s):
    """Un nombre para mostrar: sin comillas ni direcciones de correo (si solo hay correo, nada)."""
    s = " ".join(str(s or "").replace('"', "").replace("'", "").split())
    if "@" in s and " " not in s:
        return ""
    return re.sub(r"\s*<[^>]*@[^>]*>", "", s).strip()


def _asistentes(item, blocked):
    """Nombres (sin correos) de los asistentes obligatorios y opcionales; el organizador va aparte."""
    names = []
    try:
        rs = item.Recipients
        for i in range(1, min(int(rs.Count), MAX_DESTINATARIOS) + 1):
            r = rs.Item(i)
            if int(getattr(r, "Type", OL_REQUIRED)) in (OL_REQUIRED, OL_OPTIONAL):
                names.append(_nombre(r.Name))
    except Exception:
        try:
            for field in ("RequiredAttendees", "OptionalAttendees"):
                names += [_nombre(x) for x in str(getattr(item, field, "") or "").split(";")]
        except Exception:
            blocked.append("asistentes")
    out, seen = [], set()
    for n in names:
        if n and fold(n) not in seen:
            seen.add(fold(n))
            out.append(n)
    return out[:50]


def _leer_cita(item, now, lo, hi):
    """Lo no protegido de una cita: dict; "fuera" si no toca [lo, hi]; None si no cuenta (todo el día, cancelada,
    sin horas). Organizador, asistentes y cuerpo, aparte (_leer_protegidas), y solo de la cita elegida."""
    blocked = []

    def g(name, default=""):
        try:
            v = getattr(item, name)
            return default if v is None else v
        except Exception:
            blocked.append(name)
            return default

    if g("AllDayEvent", False) or int(g("MeetingStatus", 0) or 0) in CANCELADAS:
        return None
    start, end = _local(g("Start", None)), _local(g("End", None))
    if not start or not end:
        return None
    if start > hi or end < lo:  # el filtro ya lo hace: si no, el formato de la fecha no era el que Outlook entiende
        return "fuera"
    location = " ".join(str(g("Location")).split())
    return {
        "subject": " ".join(str(g("Subject")).split()),
        "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
        "organizer": "", "attendees": [],
        "categories": [c.strip() for c in re.split(r"[;,]", str(g("Categories"))) if c.strip()],
        "location": location, "is_teams": bool(_TEAMS_LOC.search(location)), "recurring": bool(g("IsRecurring", False)),
        "is_now": start <= now <= end, "blocked": sorted(set(blocked)),
        "_start": start, "_end": end,
        "_overlap": (min(end, hi) - max(start, lo)).total_seconds(), "_dist": abs((start - now).total_seconds()),
    }


def _leer_protegidas(item, m):
    """Organizador, asistentes y (si el lugar no dice Teams) el enlace de Teams del cuerpo de la cita elegida: lo que
    protege el Object Model Guard. Al primer fallo se para (cada intento puede ser otro aviso). → lo bloqueado."""
    blocked = []
    try:
        m["organizer"] = _nombre(getattr(item, "Organizer") or "")
    except Exception:
        blocked += ["Organizer", "asistentes"]
    if not blocked:
        m["attendees"] = _asistentes(item, blocked)
    if not blocked and not m["is_teams"]:
        try:
            m["is_teams"] = bool(_TEAMS_URL.search(str(getattr(item, "Body") or "")))
        except Exception:
            blocked.append("Body")
    m["blocked"] = sorted(set(m["blocked"]) | set(blocked))
    return blocked


def _filtro(lo, hi, fmt):
    return f"[Start] <= '{fmt(hi)}' AND [End] >= '{fmt(lo)}'"


def _recorrer(sel, now, lo, hi, max_citas):
    out, objs, fuera, n = [], [], 0, 0
    item = sel.GetFirst()
    while item is not None and n < max_citas:
        n += 1
        try:
            m = _leer_cita(item, now, lo, hi)
            if m == "fuera":
                fuera += 1
            elif m:
                out.append(m)
                objs.append(item)
        except Exception:
            log_exc()
        item = sel.GetNext()
    return out, objs, fuera, n


def _candidatas(app, now, lo, hi, fmts, max_citas):
    """Las citas que tocan [lo, hi] (lo no protegido), sus objetos COM y el formato de fecha que ha valido."""
    ns = app.GetNamespace("MAPI")
    items = ns.GetDefaultFolder(OL_FOLDER_CALENDAR).Items
    items.Sort("[Start]")  # orden obligatorio (docs de IncludeRecurrences): ordenar, incluir repeticiones, filtrar
    items.IncludeRecurrences = True
    primero = None
    for nombre, fmt in fmts:
        try:
            sel = items.Restrict(_filtro(lo, hi, fmt))
        except Exception as e:  # Outlook no entiende la fecha (o falla): se prueba el siguiente formato
            primero = primero or _sin_traza(e)
            if len(fmts) > 1:
                log(f"   calendario: el formato de fecha {nombre} no le vale a Outlook ({_texto(e)} "
                    f"{_codigo(e) or ''})")
            continue
        out, objs, fuera, n = _recorrer(sel, now, lo, hi, max_citas)
        if out or not fuera:
            return out, objs, nombre
        log(f"   calendario: con el formato de fecha {nombre}, Outlook da {fuera} citas fuera de la hora pedida")
    if primero is not None:
        raise primero
    return [], [], None


def _copias(cands):
    return [dict(m, attendees=list(m["attendees"]), blocked=list(m["blocked"]), categories=list(m["categories"]))
            for m in cands]


def elegir_cita(cands, teams_title=None):
    """La cita que mejor encaja con «ahora»: si el título de la ventana de Teams coincide con un asunto, esa;
    si no, la que está en curso, la de más solape, la de Teams, la más cercana."""
    if not cands:
        return None
    if teams_title:
        tt = fold(teams_title)
        for m in cands:
            s = fold(m["subject"])
            if s and (s == tt or s in tt or tt in s):
                return m
    return max(cands, key=lambda m: (m["is_now"], m["_overlap"], m["is_teams"], -m["_dist"]))


def _protegidas(caja, cands, objs, i):
    """Lee lo protegido de cands[i] y apunta si Outlook deja leerlo sin preguntar (ASISTENTES)."""
    caja["elegida"] = i
    caja["fase"] = _COM_FASE["fase"] = "protegidas"
    caja["fase_desde"] = t = time.monotonic()
    caja["_fase1"].set()
    fallos = _leer_protegidas(objs[i], cands[i])
    dt = time.monotonic() - t
    # lo decide lo que ha tardado SU lectura (un aviso espera al usuario), no lo que tardó lo demás
    caja["asistentes"] = "deniega" if fallos else ("pregunta" if dt >= LENTO else "si")
    caja["asistentes_ms"] = int(dt * 1000)
    caja["cands"] = _copias(cands)
    _marcar_asistentes(caja["asistentes"])


def _leer_protegidas_ahora(protegidas):
    """¿Se lee lo protegido? True/False lo fuerzan («Probar» / nunca). None (lo que hace la app sola, p. ej. en mitad
    de una llamada): solo si ya se sabe que Outlook no pregunta («Probar» o «Activar» lo vieron, o la directiva de la
    empresa lo aprueba). Sin comprobar, no: el aviso de Outlook saldría sin que nadie lo espere."""
    if protegidas is not None:
        return bool(protegidas)
    politica = politica_asistentes()
    if politica in ("pregunta", "deniega"):
        return False
    return ASISTENTES["estado"] == "si" or (ASISTENTES["estado"] is None and politica == "aprueba")


def _sin_respuesta(timeout):
    if _COM_FASE["fase"] == "protegidas":
        return "esperando_permiso", FRASES["esperando_permiso"]
    return "ocupado", f"Outlook no ha respondido en {timeout:g} s (¿tiene una ventana o un aviso abiertos?)."


def _leer_caja(caja, timeout):
    """(cands, None) o (None, (estado, frase)) de lo que ha devuelto _en_com."""
    if caja is None:
        if _COM_FASE["fase"] == "protegidas":
            return None, ("esperando_permiso", FRASES["esperando_permiso"])
        return None, ("ocupado", "Outlook sigue sin responder a la consulta anterior; vuelve a probar en un momento.")
    if "err" in caja:
        return None, clasificar(caja["err"], _outlook_abierto() if _codigo(caja["err"]) is not None else None)
    cands = caja.get("cands")
    if cands is None:  # sin lo básico: colgado conectando o leyendo
        return None, _sin_respuesta(timeout)
    if not caja["fin"] and caja.get("fase") == "protegidas":  # los asistentes no llegan
        # tanto (LENTO es lo que se ha esperado aquí; 50 ms de margen: en Windows el reloj va a saltos de ~16 ms y la
        # espera puede volver un poco antes), solo con el aviso de Outlook delante
        if time.monotonic() - caja.get("fase_desde", 0.0) >= LENTO - 0.05:
            _marcar_asistentes("pregunta")
        i = caja.get("elegida")
        cands = [dict(m, blocked=sorted(set(m["blocked"]) | {"asistentes"})) if j == i else m
                 for j, m in enumerate(cands)]
    return cands, None


def outlook_candidates(now=None, window=(10, 5), timeout=8.0, session=None, fmt=None, teams_title=None,
                       protegidas=None):
    """Citas del calendario que tocan la ventana [ahora−5 min, ahora+10 min] → (lista, motivo).

    `window` = (minutos hacia delante, minutos hacia atrás). La consulta COM corre en un hilo con tope: si Outlook
    se cuelga (o pide permiso y nadie contesta) se devuelve (None, motivo) y la app sigue. Lo protegido (organizador,
    asistentes) solo se lee de la cita elegida (con `teams_title`) y si Outlook no pregunta (`protegidas`, None =
    según lo visto). Nunca lanza."""
    if not WINDOWS and session is None:
        return None, "La lectura de Outlook solo funciona en Windows."
    now = now or datetime.now()
    lo, hi = now - timedelta(minutes=window[1]), now + timedelta(minutes=window[0])
    fmts = (("dado", fmt),) if fmt else FORMATOS
    leer = _leer_protegidas_ahora(protegidas)

    def trabajo(app, caja):
        cands, objs, caja["formato"] = _candidatas(app, now, lo, hi, fmts, MAX_CITAS)
        caja["cands"] = _copias(cands)
        m = elegir_cita(cands, teams_title)
        if m is not None and leer:
            _protegidas(caja, cands, objs, cands.index(m))

    caja = _en_com(trabajo, timeout, session, espera_protegidas=None if protegidas else LENTO)
    cands, fallo = _leer_caja(caja, timeout)
    return (cands, None) if fallo is None else (None, fallo[1])


def outlook_current_meeting(now=None, window=(10, 5), timeout=8.0, session=None, fmt=None, teams_title=None,
                            protegidas=None):
    """(cita, motivo): la reunión de Outlook que coincide con ahora, o None y por qué (sin Outlook, nuevo Outlook,
    COM bloqueado, aviso de seguridad rechazado, tiempo agotado, o simplemente ninguna a esta hora)."""
    cands, reason = outlook_candidates(now, window, timeout, session, fmt, teams_title, protegidas)
    if cands is None:
        return None, reason
    m = elegir_cita(cands, teams_title)
    if not m:
        return None, "Outlook no tiene ninguna reunión a esta hora."
    return {k: v for k, v in m.items() if not k.startswith("_")}, None


# --- el estado del calendario en una frase (Ajustes › Calendario y Teams, «Probar»)

_ESTADO_CACHE = {}  # activado → (cuándo, estado): sin «Probar», una lectura por minuto como mucho
CACHE_S = 60


def _hora(d):
    return f"{d.hour}:{d.minute:02d}"


def _comprobar_horas(item):
    """¿Outlook (Start frente a StartUTC) y Windows llevan el mismo desfase con UTC a esa hora? Si no, las horas que
    lee la app no serían las que enseña Outlook."""
    s, u = _local(getattr(item, "Start")), _local(getattr(item, "StartUTC"))
    outlook = round((s - u).total_seconds() / 60)
    windows = round(s.astimezone().utcoffset().total_seconds() / 60)
    return {"cuadran": outlook == windows, "outlook": outlook, "windows": windows}


def agenda_hoy(now=None, timeout=4.0, session=None, fmt=None, protegidas=False, horas=False):
    """La agenda de hoy del Outlook clásico, sin asistentes → (datos, None) o (None, (estado, frase)).

    datos: {"citas", "periodicas", "ahora" (la que está en curso), "proxima" (la siguiente que empieza), "formato",
    "horas" (con `horas`), "asistentes" (con `protegidas`: si Outlook deja leerlos sin preguntar)}. Sin asuntos."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    lo = datetime(now.year, now.month, now.day)
    hi = lo + timedelta(days=1) - timedelta(minutes=1)
    fmts = (("dado", fmt),) if fmt else FORMATOS

    def trabajo(app, caja):
        cands, objs, caja["formato"] = _candidatas(app, now, lo, hi, fmts, MAX_CITAS_DIA)
        caja["cands"] = _copias(cands)
        if horas and objs:
            try:
                caja["horas"] = _comprobar_horas(objs[0])
            except Exception:
                log_exc()
        if protegidas and cands:
            prox = [m for m in cands if m["is_now"] or m["_start"] >= now]
            i = cands.index(min(prox, key=lambda m: m["_start"])) if prox else 0
            _protegidas(caja, cands, objs, i)

    caja = _en_com(trabajo, timeout, session, espera_protegidas=None,
                   espera_otra=10.0 if protegidas else ESPERA_OTRA)
    cands, fallo = _leer_caja(caja, timeout)
    if fallo:
        return None, fallo
    en_curso = [m for m in cands if m["is_now"]]
    siguientes = [m for m in cands if m["_start"] > now]
    resumen = (lambda m: {"inicio": _hora(m["_start"]), "fin": _hora(m["_end"])} if m else None)
    datos = {"citas": len(cands), "periodicas": sum(1 for m in cands if m["recurring"]),
             "ahora": resumen(min(en_curso, key=lambda m: m["_end"]) if en_curso else None),
             "proxima": resumen(min(siguientes, key=lambda m: m["_start"]) if siguientes else None),
             "formato": caja.get("formato"), "horas": caja.get("horas"),
             "asistentes": (caja.get("asistentes") if caja.get("fin")
                            else (ASISTENTES["estado"] if protegidas else None)),
             "asistentes_ms": caja.get("asistentes_ms")}
    return datos, None


def _frase_agenda(d):
    if d["ahora"]:
        return f"reunión en curso hasta las {d['ahora']['fin']}"
    if d["proxima"]:
        return f"próxima reunión {d['proxima']['inicio']}"
    return "hoy no tienes más reuniones"


def _sufijo_asistentes(v, politica=None):
    """(texto, acción) de los asistentes tras «conectado»: la empresa solo se nombra si lo dice su directiva."""
    if v == "deniega" and politica == "deniega":
        return " · sin asistentes: tu empresa no deja que otras aplicaciones los lean", None
    if v == "deniega":
        return " · sin asistentes: Outlook no ha dejado leerlos (pulsa «Probar» y, si pregunta, «Permitir»)", "probar"
    if v == "pregunta":
        return " · sin asistentes: Outlook pide permiso para leerlos (se leen al pulsar «Probar»)", "probar"
    if v is None:
        return " · pulsa «Probar» para que lea también los asistentes", "probar"
    return "", None


def estado(activado, probar=False, now=None, entorno=None, session=None, fmt=None, timeout=None):
    """El estado del calendario en una frase: {nivel, estado, frase, accion} (+ activado, asistentes, proxima,
    comprobado y, con `probar`, pasos). Ver el contrato en docs/ (CHANGELOG) y en el docstring del módulo.

    Sin `probar` es barato: no consulta Outlook si la casilla está apagada o Outlook cerrado, no lee asistentes (no
    puede abrir el aviso de seguridad) y guarda la lectura un minuto. Con `probar`, consulta aunque la casilla esté
    apagada, espera hasta 30 s (si Outlook pregunta, da tiempo a contestar) y lista los pasos. Nunca lanza."""
    activado = bool(activado)
    c = _ESTADO_CACHE.get(activado)
    if not probar and c and time.monotonic() - c[0] < CACHE_S:
        return dict(c[1])
    t0 = time.monotonic()
    try:
        r = _estado(activado, probar, now or datetime.now(), entorno, session, fmt, timeout)
    except Exception:  # nunca deja Ajustes sin respuesta
        log_exc()
        r = {"nivel": 1, "estado": "error", "frase": "No se ha podido comprobar el calendario (detalle en server.log).",
             "accion": dict(_ACCION["probar"]), "activado": activado, "asistentes": ASISTENTES["estado"],
             "proxima": None, "comprobado": datetime.now().isoformat(timespec="seconds")}
    ms = int((time.monotonic() - t0) * 1000)
    diag = r.pop("_diag", None) or {}
    if probar:
        hechos = ", ".join(f"{p['id']}_{'si' if p['ok'] else ('no' if p['ok'] is False else 'info')}"
                           for p in r.get("pasos") or [])
        log(f"   calendario: probar · {r['estado']} · {hechos} ({ms} ms)")
        log(f"   calendario: entorno · Python {diag.get('python_bits') or 0} bits · Office "
            f"{diag.get('office_bits') or 'desconocido'} · directiva {diag.get('politica') or 'ninguna'} · fecha "
            f"{diag.get('formato') or 'sin_dato'} · horas {diag.get('horas') or 'sin_dato'} · asistentes "
            f"{r.get('asistentes') or 'sin_comprobar'}")
    elif c is None or c[1].get("estado") != r["estado"]:  # solo los cambios (Ajustes puede preguntar a menudo)
        log(f"   calendario: {r['estado']} ({ms} ms)")
    _ESTADO_CACHE.clear()
    _ESTADO_CACHE[activado] = (time.monotonic(), {k: v for k, v in r.items() if k != "pasos"})
    return r


def _estado(activado, probar, now, env, session, fmt, timeout):
    pasos = []

    def paso(id_, ok, texto):
        pasos.append({"id": id_, "ok": ok, "texto": texto})

    r = {"nivel": 1, "estado": None, "frase": "", "accion": None, "activado": activado,
         "asistentes": ASISTENTES["estado"], "proxima": None, "comprobado": now.isoformat(timespec="seconds")}
    if env is None:
        env = {"windows": True, "pywin32": True, "instalado": True, "abierto": True} if session else entorno_outlook()

    r["_diag"] = diag = {"python_bits": env.get("python_bits"), "office_bits": env.get("office_bits"),
                         "politica": env.get("politica")}

    def fin(estado_, frase, accion=None, nivel=1):
        r.update(estado=estado_, frase=frase, nivel=nivel, accion=dict(_ACCION[accion]) if accion else None)
        if probar:
            r["pasos"] = pasos
        return r

    if not env.get("windows"):
        paso("windows", False, "Solo en Windows")
        return fin("no_windows", FRASES["no_windows"], nivel=0)
    pywin32, instalado, abierto = env.get("pywin32"), env.get("instalado"), env.get("abierto")
    nuevo = bool(env.get("nuevo_activado") or env.get("nuevo_abierto"))
    politica = env.get("politica")
    paso("pywin32", bool(pywin32), "pywin32 instalada" if pywin32 else "Falta pywin32 (vuelve a ejecutar INSTALAR.bat)")
    paso("outlook_clasico", instalado, "Outlook clásico instalado" if instalado else
         ("Outlook clásico: no instalado" if instalado is False else "Outlook clásico: no se sabe si está instalado"))
    if nuevo:
        paso("outlook_nuevo", None, "Outlook nuevo activado" if env.get("nuevo_activado") else "Outlook nuevo abierto")
    paso("abierto", abierto, {True: "Outlook clásico abierto", False: "Outlook clásico cerrado"}.get(
        abierto, "No se sabe si Outlook está abierto"))
    if politica:
        paso("politica", None, {"deniega": "Directiva de la empresa: deniega a otras aplicaciones leer los asistentes",
                                "pregunta": "Directiva de la empresa: Outlook pregunta antes de dar los asistentes",
                                "aprueba": "Directiva de la empresa: deja leer los asistentes"}[politica])
    if env.get("admin"):
        paso("admin", None, f"{APP_NAME} está abierta como administrador")
    paso("activado", activado,
         f"Calendario activado en {APP_NAME}" if activado else f"Calendario apagado en {APP_NAME}")

    # sin «Probar», lo que se sabe sin hablar con Outlook decide (no se le molesta en balde)
    if not probar:
        if instalado is False:
            return fin("outlook_nuevo" if nuevo else "sin_outlook", FRASES["outlook_nuevo" if nuevo else "sin_outlook"])
        if not activado:
            return fin("apagado", FRASES["apagado" if instalado else "apagado_sin_saber"], "activar")
        if not pywin32:
            return fin("sin_pywin32", FRASES["sin_pywin32"])
        if abierto is False:
            if nuevo:
                return fin("outlook_nuevo", FRASES["outlook_nuevo"])
            return fin("cerrado", FRASES["cerrado"], "probar")
    elif not pywin32:
        if instalado is False:
            return fin("outlook_nuevo" if nuevo else "sin_outlook", FRASES["outlook_nuevo" if nuevo else "sin_outlook"])
        return fin("sin_pywin32", FRASES["sin_pywin32"])

    datos, fallo = agenda_hoy(now, timeout or (30.0 if probar else 4.0), session, fmt, protegidas=probar, horas=probar)
    if fallo:
        estado_, frase = fallo
        if estado_ in ("cerrado", "sin_outlook") and nuevo:
            estado_, frase = "outlook_nuevo", FRASES["outlook_nuevo"]
        paso("conexion", False, frase)
        reintentable = estado_ in ("cerrado", "sin_permiso", "esperando_permiso", "ocupado", "error")
        return fin(estado_, frase, "probar" if reintentable else None)
    paso("conexion", True, "Conectado al Outlook clásico")
    diag["formato"] = datos.get("formato")
    if datos.get("horas"):
        diag["horas"] = "cuadran" if datos["horas"]["cuadran"] else "no_cuadran"
    paso("agenda", True, f"Hoy: {datos['citas']} {'cita' if datos['citas'] == 1 else 'citas'}"
         f" ({datos['periodicas']} {'periódica' if datos['periodicas'] == 1 else 'periódicas'})")
    if datos.get("formato") and datos["formato"] not in ("regional", "dado"):
        paso("fecha", None, f"Outlook no entiende el formato de fecha de Windows: se usa el {datos['formato']}")
    if datos.get("horas"):
        h = datos["horas"]
        paso("horas", h["cuadran"], "Las horas de Outlook cuadran con las de Windows" if h["cuadran"] else
             f"Las horas no cuadran: Outlook va a {h['outlook']:+d} min de UTC y Windows a {h['windows']:+d}")
    asist = datos.get("asistentes") or ASISTENTES["estado"]
    if asist == "si" and politica in ("pregunta", "deniega"):  # la app sola no los leerá: la frase lo dice
        asist = politica
    elif asist is None and politica == "aprueba":  # la empresa los deja leer: la app los leerá sola
        asist = "si"
    if probar:
        textos = {"si": "Outlook deja leer los asistentes", "pregunta": "Outlook pregunta antes de dar los asistentes",
                  "deniega": "Outlook no deja leer los asistentes"}
        paso("asistentes", {"si": True, "deniega": False}.get(asist),
             textos.get(asist, "Hoy no hay reuniones para comprobar los asistentes"))
    r["asistentes"] = asist
    if datos["ahora"]:
        r["proxima"] = {"hora": datos["ahora"]["inicio"], "fin": datos["ahora"]["fin"], "ahora": True}
    elif datos["proxima"]:
        r["proxima"] = {"hora": datos["proxima"]["inicio"], "fin": datos["proxima"]["fin"], "ahora": False}
    agenda = _frase_agenda(datos)
    if not activado:
        return fin("apagado", f"Outlook clásico responde ({agenda}), pero el calendario está apagado: actívalo para "
                              f"que {APP_NAME} lo use al grabar.", "activar")
    sufijo, accion = _sufijo_asistentes(asist, politica)
    return fin("conectado", f"Outlook clásico: conectado · {agenda}{sufijo}", accion, nivel=2)


# ----------------------------------------------------------------------------- sugerencia (nombre, tema, participantes)

_GENERICOS = frozenset(("general", "notas", "reunion", "reunión", "meeting", "proyecto", "equipo", "team"))


def nombre_archivo(s, limit=80):
    """Un nombre de grabación válido en Windows a partir de un asunto (quita \\ / : * ? \" < > | y recorta)."""
    s = BAD_NAME.sub(" ", str(s or ""))
    s = " ".join(s.split()).strip(" .-–—")
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" .-–—") or s[:limit]
    return s


def _puntos(terms, hay):
    n = 0
    for term in terms:
        t = fold(term).strip()
        if len(t) >= 3 and re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", hay):
            n += 2 if " " in t else 1
    return n


def join_participants(names):
    """Los participantes en una linea (la propiedad «Participantes» del .md): con «, » o, si algun nombre lleva coma
    (el directorio de una empresa en Outlook: «Apellido, Nombre»), con «; », que es como los separa Outlook. Si no,
    «Stegmaier, Bernardo, García, Ana» eran cuatro personas para quien lo lee y para la pista de quien habla
    (voices.count_participants; revision de la integracion del 08/11/2026)."""
    names = [n for n in (names or []) if n]
    return ("; " if any("," in n for n in names) else ", ").join(names)


def suggest(meeting, vocab_lists, min_score=2):
    """Nombre, tema y participantes sugeridos para la grabación.

    `meeting`: {subject, organizer, attendees, categories} (de Outlook, o solo el título de Teams).
    `vocab_lists`: [{id, project, name, terms: [...]}] (las listas <proyecto>/Vocabulario/<tema>.txt).
    El tema es la lista con más términos (y nombre de proyecto/tema) presentes en asunto + categorías +
    organizador + asistentes; un término suelto no basta (umbral `min_score`), para no sugerir a ciegas.
    """
    meeting = meeting or {}
    subject = " ".join(str(meeting.get("subject") or "").split())
    attendees = [a for a in (meeting.get("attendees") or []) if a]
    organizer = str(meeting.get("organizer") or "").strip()
    categories = [c for c in (meeting.get("categories") or []) if c]
    hay = " " + fold(" ".join([subject] + categories + [organizer] + attendees)) + " "
    best, best_key = None, None
    for i, v in enumerate(vocab_lists or []):
        score = _puntos(v.get("terms") or [], hay)
        for key in (v.get("project"), v.get("name")):
            k = fold(key or "").strip()
            if len(k) >= 3 and k not in _GENERICOS and re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", hay):
                score += 2
        key = (score, fold(v.get("name") or "") != "general", -i)
        if score >= min_score and (best_key is None or key > best_key):
            best, best_key = v, key
    participants, seen = [], set()
    for n in [organizer] + attendees:
        n = " ".join(str(n).split())
        if n and fold(n) not in seen:
            seen.add(fold(n))
            participants.append(n)
    return {"name": nombre_archivo(subject), "vocab": best["id"] if best else "",
            "vocab_label": f"{best['project']} · {best['name']}" if best else "",
            "score": best_key[0] if best else 0, "participants": participants[:30]}


def reunion_actual(settings, vocab_lists, outlook=None, now=None, timeout=8.0, teams=None, outlook_fn=None):
    """Lo que la app ve ahora mismo: {teams, outlook, sugerencia, reason}.

    Outlook se consulta si el ajuste `outlook_on` está encendido o se fuerza con `outlook=True` (botón Probar);
    `reason` explica por qué no hay cita (o va vacío). `teams`/`outlook_fn` admiten dobles en los tests.
    """
    titulo = teams() if teams is not None else teams_meeting_title()
    usar = bool((settings or {}).get("outlook_on")) if outlook is None else bool(outlook)
    cita, reason = None, None
    if usar:
        fn = outlook_fn or outlook_current_meeting
        cita, reason = fn(now=now, timeout=timeout, teams_title=titulo)
    meeting = cita or ({"subject": titulo} if titulo else None)
    sug = suggest(meeting, vocab_lists) if meeting else {}
    if sug:
        sug["source"] = "outlook" if cita else "teams"
    return {"teams": {"title": titulo} if titulo else None, "outlook": cita, "sugerencia": sug, "reason": reason}


# ----------------------------------------------------------------------------- aviso flotante (vigilante del servidor)

def decidir_aviso(app, recording, activado, abierto, avisado):
    """(abrir, avisado): el aviso flotante se abre UNA vez por llamada, nunca si ya se graba, si el ajuste está
    apagado o si ya hay uno abierto. `avisado` recuerda la llamada ya avisada y se olvida cuando termina."""
    if not app:
        return False, None
    if recording or not activado or abierto or avisado == app:
        return False, avisado
    return True, app


class Vigia:
    """Estado que el vigilante de reuniones del servidor comparte con la API y la grabadora: la última lectura
    (así Ctrl+Alt+R y el aviso no esperan a Outlook) y el proceso del aviso flotante."""

    def __init__(self):
        self.ultimo, self.ultimo_at, self.proc, self.avisado = None, 0.0, None, None
        self.lock = threading.Lock()
        self.historial = []  # [(hora, participantes)] de las ultimas lecturas: participantes_recientes

    def recordar(self, r):
        with self.lock:
            self.ultimo, self.ultimo_at = r, time.time()
            p = list(((r or {}).get("sugerencia") or {}).get("participants") or [])
            if p:  # la misma lista que la anterior (el vigilante relee cada minuto): solo se renueva su hora
                h = [x for x in self.historial if x[1] != p]
                self.historial = (h + [(self.ultimo_at, p)])[-20:]
        return r

    def participantes_recientes(self, max_age=3600):
        """Las listas de participantes de las lecturas de la ultima hora. La que leyo el dialogo Grabar puede no ser ya
        la ultima: el vigilante relee cada minuto durante la llamada y olvida la lectura al colgar (tic)."""
        ahora = time.time()
        with self.lock:
            return [list(p) for t, p in self.historial if ahora - t <= max_age]

    def sugerencia(self, leer, max_age=90):
        """La sugerencia de la última lectura si es reciente; si no, `leer()` (rápida: solo Teams)."""
        with self.lock:
            r = self.ultimo if self.ultimo and time.time() - self.ultimo_at <= max_age else None
        if r is None:
            try:
                r = self.recordar(leer())
            except Exception:
                log_exc()
                return {}
        return dict(r.get("sugerencia") or {})

    def abierto(self):
        return bool(self.proc and self.proc.poll() is None)

    def cerrar_aviso(self):
        if self.abierto():
            try:
                self.proc.terminate()
            except OSError:
                pass
        self.proc = None

    def abrir_aviso(self, puerto, app_dir, python=None, creationflags=0, segundos=20):
        """Lanza aviso.py (proceso aparte, como el selector de carpetas) con lo que se sabe de la reunión."""
        sug = dict((self.ultimo or {}).get("sugerencia") or {})
        titulo = ((self.ultimo or {}).get("teams") or {}).get("title") or sug.get("name") or ""
        cmd = [python or sys.executable, os.path.join(app_dir, "aviso.py"), "--puerto", str(puerto),
               "--titulo", titulo, "--nombre", sug.get("name") or "", "--vocab", sug.get("vocab") or "",
               "--tema", sug.get("vocab_label") or "", "--segundos", str(segundos)]
        try:
            self.proc = subprocess.Popen(cmd, cwd=app_dir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, creationflags=creationflags)
            return True
        except OSError:
            log_exc()
            return False

    def tic(self, app, recording, settings, leer, puerto, app_dir, creationflags=0, cada=60):
        """Un paso del vigilante (cada ~8 s): lee la reunión mientras dure la llamada, abre el aviso cuando toca
        y lo cierra si la llamada termina o ya se graba. Devuelve True solo en el paso en que abre el aviso."""
        if app and (self.ultimo is None or time.time() - self.ultimo_at > cada):
            try:
                self.recordar(leer())
            except Exception:
                log_exc()
        abrir, self.avisado = decidir_aviso(app, recording, settings.get("rec_aviso", True), self.abierto(),
                                            self.avisado)
        if abrir:
            return self.abrir_aviso(puerto, app_dir, creationflags=creationflags)
        if (not app or recording) and self.abierto():
            self.cerrar_aviso()
        if not app:
            with self.lock:
                self.ultimo = None
        return False
