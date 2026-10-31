# -*- coding: utf-8 -*-
"""Reunión en curso: de dónde salen el nombre, el tema y los participantes que la app sugiere al grabar.

Dos niveles, los dos locales y de solo lectura:
  1. El título de la ventana de REUNIÓN de Teams (EnumWindows): no activa nada y vale en cualquier PC.
  2. Outlook clásico de escritorio por COM (pywin32, opcional): la cita del calendario que coincide con «ahora»
     (asunto, horas, organizador, asistentes, categorías). Solo si se enciende en Ajustes («Leer mi calendario»).
La lectura no sale del PC. Los asistentes sugeridos van a la cabecera del .md de la grabación con su origen
(participantes_origen) y, mientras el usuario no los confirma o los edita, tampoco salen de ahí: ni al resumen con IA
(que va a la nube) ni a los destinos de salida (server.participantes_confirmados). Confirmados, sí.

Fuera de Windows (y en los tests) cada lectura devuelve None con su motivo; la lógica pura (parse_title,
pick_meeting_window, elegir_cita, suggest, decidir_aviso) se prueba con dobles.
"""
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from common import BAD_NAME, WINDOWS, fold, log_exc

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

OL_FOLDER_CALENDAR = 9
OL_REQUIRED, OL_OPTIONAL = 1, 2  # Recipient.Type (0 = organizador, 3 = recurso)
CANCELADAS = (5, 7)  # MeetingStatus: olMeetingCanceled, olMeetingReceivedAndCanceled
MAX_CITAS = 50  # con IncludeRecurrences el Count no vale y la colección puede no tener fin
_TEAMS_LOC = re.compile(r"microsoft teams|teams\.microsoft\.com|teams\.live\.com", re.I)
_TEAMS_URL = re.compile(r"https://(?:teams\.microsoft\.com|teams\.live\.com|[\w.-]*teams\.cloud\.microsoft)/", re.I)
_SIN_OUTLOOK = "Outlook clásico no está abierto (el nuevo Outlook no deja leer el calendario desde otras aplicaciones)."
_HRESULT = {
    -2147221021: _SIN_OUTLOOK,  # MK_E_UNAVAILABLE: GetActiveObject sin Outlook en marcha
    -2146959355: _SIN_OUTLOOK,  # CO_E_SERVER_EXEC_FAILURE
    # CO_E_CLASSSTRING: el ProgID Outlook.Application no existe en el registro (sin Office clásico)
    -2147221005: "Outlook clásico no está instalado en este PC (el nuevo Outlook no deja leer el calendario desde "
                 "otras aplicaciones).",
    -2147418111: "Outlook está ocupado (cierra sus cuadros de diálogo y vuelve a probar).",
    -2147417846: "Outlook está ocupado (cierra sus cuadros de diálogo y vuelve a probar).",
    -2147024891: "Outlook no ha dado permiso (aviso de seguridad rechazado, o Outlook abierto como administrador).",
    -2147467260: "Se ha cancelado el aviso de seguridad de Outlook.",
}
_COM_LOCK = threading.Lock()  # una consulta a la vez: si una se cuelga, las siguientes no se amontonan


def fecha_jet(dt):
    """La fecha como la espera el filtro de Outlook: formato regional corto del usuario y hora SIN segundos
    (docs: «Filtering Items Using a Date-time Comparison»). Fuera de Windows, el formato de EE. UU."""
    dt = dt.replace(second=0, microsecond=0)
    if WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            st = wintypes.SYSTEMTIME(dt.year, dt.month, dt.isoweekday() % 7, dt.day, dt.hour, dt.minute, 0, 0)
            d, t = ctypes.create_unicode_buffer(80), ctypes.create_unicode_buffer(80)
            if (k32.GetDateFormatEx(None, 0x1, ctypes.byref(st), None, d, 80, None)  # DATE_SHORTDATE
                    and k32.GetTimeFormatEx(None, 0x2, ctypes.byref(st), None, t, 80)):  # TIME_NOSECONDS
                return f"{d.value} {t.value}"
        except Exception:
            log_exc()
    return dt.strftime("%m/%d/%Y %H:%M")


def _sesion_outlook():
    """Outlook.Application ya abierto (no lo arranca: si no está, se dice). Requiere pywin32."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    return win32com.client.GetActiveObject("Outlook.Application")


def _local(d):
    """datetime naif en hora local (pywintypes.datetime lleva zona; los dobles no)."""
    if d is None:
        return None
    if getattr(d, "tzinfo", None) is not None:
        d = d.astimezone().replace(tzinfo=None)
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
        for i in range(1, int(rs.Count) + 1):
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
    """Lo que la app necesita de una cita de Outlook, o None si no cuenta (todo el día, cancelada, fuera de hora)."""
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
    if not start or not end or start > hi or end < lo:  # el filtro ya lo hace; por si el formato regional falla
        return None
    location = " ".join(str(g("Location")).split())
    is_teams = bool(_TEAMS_LOC.search(location))
    if not is_teams:
        is_teams = bool(_TEAMS_URL.search(str(g("Body"))))
    organizer = _nombre(g("Organizer"))
    return {
        "subject": " ".join(str(g("Subject")).split()),
        "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
        "organizer": organizer, "attendees": _asistentes(item, blocked),
        "categories": [c.strip() for c in re.split(r"[;,]", str(g("Categories"))) if c.strip()],
        "location": location, "is_teams": is_teams, "recurring": bool(g("IsRecurring", False)),
        "is_now": start <= now <= end, "blocked": sorted(set(blocked)),
        "_overlap": (min(end, hi) - max(start, lo)).total_seconds(), "_dist": abs((start - now).total_seconds()),
    }


def _consulta(app, now, lo, hi, fmt):
    ns = app.GetNamespace("MAPI")
    items = ns.GetDefaultFolder(OL_FOLDER_CALENDAR).Items
    items.Sort("[Start]")  # orden obligatorio (docs de IncludeRecurrences): ordenar, incluir repeticiones, filtrar
    items.IncludeRecurrences = True
    filtro = f"[Start] <= '{fmt(hi)}' AND [End] >= '{fmt(lo)}'"
    sel = items.Restrict(filtro)
    out, item, n = [], sel.GetFirst(), 0
    while item is not None and n < MAX_CITAS:
        n += 1
        try:
            m = _leer_cita(item, now, lo, hi)
            if m:
                out.append(m)
        except Exception:
            log_exc()
        item = sel.GetNext()
    return out


def _motivo(e):
    if isinstance(e, ImportError):
        return "Falta la librería pywin32: vuelve a ejecutar INSTALAR.bat (o «pip install pywin32»)."
    hr = getattr(e, "hresult", None)
    if hr is not None:
        if hr in _HRESULT:
            return _HRESULT[hr]
        texto = getattr(e, "strerror", None) or str(e)
        return f"Outlook ha devuelto un error ({hr & 0xFFFFFFFF:#010x}): {texto}"
    return f"No se pudo leer Outlook ({e.__class__.__name__}: {e})"


def outlook_candidates(now=None, window=(10, 5), timeout=8.0, session=None, fmt=None):
    """Citas del calendario que tocan la ventana [ahora−5 min, ahora+10 min] → (lista, motivo).

    `window` = (minutos hacia delante, minutos hacia atrás). La consulta COM corre en un hilo con tope: si Outlook
    se cuelga (o pide permiso y nadie contesta) se devuelve (None, motivo) y la app sigue. Nunca lanza.
    """
    if not WINDOWS and session is None:
        return None, "La lectura de Outlook solo funciona en Windows."
    now = now or datetime.now()
    lo, hi = now - timedelta(minutes=window[1]), now + timedelta(minutes=window[0])
    session, fmt = session or _sesion_outlook, fmt or fecha_jet
    if not _COM_LOCK.acquire(blocking=False):
        return None, "Outlook sigue sin responder a la consulta anterior; vuelve a probar en un momento."
    box = {}

    def run():
        try:
            app = session()
            try:
                box["ok"] = _consulta(app, now, lo, hi, fmt)
            finally:
                del app
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        except BaseException as e:  # noqa: B902 (se convierte en motivo; nada escapa)
            box["err"] = e
        finally:
            _COM_LOCK.release()

    t = threading.Thread(target=run, daemon=True, name="outlook-com")
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, f"Outlook no ha respondido en {timeout:g} s (¿tiene algún cuadro de diálogo abierto?)"
    if "err" in box:
        return None, _motivo(box["err"])
    return box["ok"], None


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


def outlook_current_meeting(now=None, window=(10, 5), timeout=8.0, session=None, fmt=None, teams_title=None):
    """(cita, motivo): la reunión de Outlook que coincide con ahora, o None y por qué (sin Outlook, nuevo Outlook,
    COM bloqueado, aviso de seguridad rechazado, tiempo agotado, o simplemente ninguna a esta hora)."""
    cands, reason = outlook_candidates(now, window, timeout, session, fmt)
    if cands is None:
        return None, reason
    m = elegir_cita(cands, teams_title)
    if not m:
        return None, "Outlook no tiene ninguna reunión a esta hora."
    return {k: v for k, v in m.items() if not k.startswith("_")}, None


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

    def recordar(self, r):
        with self.lock:
            self.ultimo, self.ultimo_at = r, time.time()
        return r

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
