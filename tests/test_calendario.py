# -*- coding: utf-8 -*-
"""Calendario de Outlook (fix-calendario, 07/11/2026): por qué «no funcionaba» y su arreglo, con un COM falso que
imita lo que hace el de verdad (pywin32 y Outlook). Nada de aquí necesita Windows ni pywin32; lo que solo se puede
medir en Windows (pywin32 real, fechas COM, procesos, registro) está en tests/test_calendario_windows.py.

- Horas: pywin32 pega la zona UTC a las fechas COM sin convertirlas (PyTime.cpp de pywin32 312) y Outlook las da en
  hora local; convertirlas desplazaba las citas el desfase de Madrid (+1 h en invierno, +2 en verano) y la reunión en
  curso quedaba fuera: «Outlook no tiene ninguna reunión a esta hora» con Outlook conectado.
- Lo protegido (organizador, asistentes, cuerpo) abre el aviso de seguridad de Outlook o falla según el antivirus y la
  directiva: solo de la cita elegida, y si pregunta, lo demás llega igual y no se vuelve a preguntar solo.
- COM en el hilo que consulta (CoInitialize/CoUninitialize, también si falla), periódicas, filtro de fechas, estado
  en una frase (/api/calendario/estado) y lo que va a server.log (sin asuntos ni nombres)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest import mock

from support import server  # noqa: F401  (prepara sys.path con _app)
import calendario  # noqa: E402
from test_reunion import FakeApp, FakeItem, FakeItems, FakeNamespace, reset_calendario  # noqa: E402

NOW = datetime(2026, 11, 7, 10, 30)
FMT = "%d/%m/%Y %H:%M"
DIA = (lambda d: d.strftime(FMT))


class UTCPegada(datetime):
    """Como pywintypes.datetime: la hora de Outlook (local) con la zona UTC pegada, sin convertir."""


def pywin32_fecha(d):
    return UTCPegada(d.year, d.month, d.day, d.hour, d.minute, d.second, tzinfo=timezone.utc)


class ZonaMadrid:
    """TZ=Europe/Madrid en este proceso (POSIX): así la conversión equivocada se nota también aquí."""

    def __enter__(self):
        self.prev = os.environ.get("TZ")
        if hasattr(time, "tzset"):
            os.environ["TZ"] = "Europe/Madrid"
            time.tzset()
        return self

    def __exit__(self, *a):
        if hasattr(time, "tzset"):
            if self.prev is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = self.prev
            time.tzset()


def consulta(items, now=NOW, fmt=DIA, **kw):
    log = []
    fake = FakeApp(items, log)
    out = calendario.outlook_candidates(now=now, session=lambda: fake, fmt=fmt, **kw)
    return out, log


def esperar_hilo_com():
    """Que termine la consulta COM que haya quedado en marcha (un aviso «contestado» al final de un test)."""
    for _ in range(300):
        if not calendario._COM_LOCK.locked():
            return
        time.sleep(0.01)


class Base(unittest.TestCase):
    def setUp(self):
        esperar_hilo_com()
        reset_calendario()
        self.addCleanup(reset_calendario)
        self.addCleanup(esperar_hilo_com)


# ----------------------------------------------------------------------------- 1. las horas (la causa principal)

class HorasTests(Base):
    def test_la_hora_de_outlook_no_se_convierte(self):
        """10:00 en Outlook es 10:00 en Chuleta, aunque pywin32 diga que es UTC."""
        with ZonaMadrid():
            for d in (datetime(2026, 11, 7, 10, 0), datetime(2026, 7, 7, 10, 0)):  # invierno (+1) y verano (+2)
                self.assertEqual(calendario._local(pywin32_fecha(d)), d)
                if hasattr(time, "tzset"):  # control: lo que hacía antes sí la movía
                    self.assertNotEqual(pywin32_fecha(d).astimezone().replace(tzinfo=None), d)

    def test_la_reunion_en_curso_ya_no_se_descarta(self):
        """El síntoma: con la hora movida +1 h, la reunión de 10:00 a 11:00 a las 10:30 «empezaba» a las 11:00, fuera
        de la ventana [ahora−5, ahora+10]: «Outlook no tiene ninguna reunión a esta hora»."""
        it = FakeItem("Comité", pywin32_fecha(datetime(2026, 11, 7, 10, 0)),
                      pywin32_fecha(datetime(2026, 11, 7, 11, 0)))
        with ZonaMadrid():
            m, reason = calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertIsNone(reason)
        self.assertEqual((m["subject"], m["start"], m["end"], m["is_now"]),
                         ("Comité", "2026-11-07T10:00", "2026-11-07T11:00", True))

    def test_las_horas_cuadran_con_windows(self):
        """«Probar» compara Start (local) con StartUTC: el mismo desfase que Windows a esa hora."""
        with ZonaMadrid():
            if not hasattr(time, "tzset"):
                self.skipTest("sin time.tzset (Windows): lo mide test_calendario_windows")
            it = FakeItem("x", pywin32_fecha(datetime(2026, 11, 7, 10, 0)), pywin32_fecha(datetime(2026, 11, 7, 11, 0)))
            it.StartUTC = pywin32_fecha(datetime(2026, 11, 7, 9, 0))
            self.assertEqual(calendario._comprobar_horas(it), {"cuadran": True, "outlook": 60, "windows": 60})
            it.StartUTC = pywin32_fecha(datetime(2026, 11, 7, 10, 0))  # Outlook en otra zona que Windows
            self.assertFalse(calendario._comprobar_horas(it)["cuadran"])


# ----------------------------------------------------------------------------- 2. COM en el hilo que consulta

class FakeComError(Exception):
    """pywintypes.com_error: (hresult, strerror, excepinfo, argerror), también como atributos."""

    def __init__(self, hresult, strerror="", excepinfo=None, argerror=None):
        super().__init__(hresult, strerror, excepinfo, argerror)
        self.hresult, self.strerror, self.excepinfo, self.argerror = hresult, strerror, excepinfo, argerror


class FakeUnknown:
    def __init__(self, app, log):
        self.app, self.log = app, log

    def QueryInterface(self, iid):
        self.log.append(("QueryInterface", iid))
        return self


def fake_pywin32(app, active=None, iid_error=None):
    """pythoncom, pywintypes y win32com.client(.dynamic) falsos con las reglas de COM que importan: GetActiveObject
    sin CoInitialize en ESE hilo da CO_E_NOTINITIALIZED; `active`, errores de GetActiveObject en orden."""
    st = {"init": {}, "calls": [], "active": list(active or [])}

    def tid():
        return threading.get_ident()

    pythoncom = types.ModuleType("pythoncom")
    pythoncom.com_error = FakeComError
    pythoncom.IID_IDispatch = "IID_IDispatch"

    def CoInitialize():
        st["init"][tid()] = st["init"].get(tid(), 0) + 1
        st["calls"].append(("CoInitialize", tid()))

    def CoUninitialize():
        st["init"][tid()] = st["init"].get(tid(), 0) - 1
        st["calls"].append(("CoUninitialize", tid()))

    def GetActiveObject(clsid):
        if not st["init"].get(tid()):
            raise FakeComError(calendario.CO_E_NOTINITIALIZED, "CoInitialize has not been called.")
        st["calls"].append(("GetActiveObject", tid(), clsid))
        if st["active"]:
            e = st["active"].pop(0)
            if e is not None:
                raise e
        return FakeUnknown(app, st["calls"])

    pythoncom.CoInitialize, pythoncom.CoUninitialize = CoInitialize, CoUninitialize
    pythoncom.GetActiveObject = GetActiveObject
    pywintypes = types.ModuleType("pywintypes")
    pywintypes.com_error = FakeComError

    def IID(s):
        st["calls"].append(("IID", s))
        if iid_error:
            raise iid_error
        return "{0006F03A-0000-0000-C000-000000000046}"

    pywintypes.IID = IID
    win32com, client, dynamic = (types.ModuleType("win32com"), types.ModuleType("win32com.client"),
                                 types.ModuleType("win32com.client.dynamic"))

    def Dispatch(disp):
        st["calls"].append(("dynamic.Dispatch", tid()))
        return disp.app

    dynamic.Dispatch = Dispatch
    client.dynamic, win32com.client = dynamic, client
    client.Dispatch = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Dispatch con caché gen_py: no se usa"))
    mods = {"pythoncom": pythoncom, "pywintypes": pywintypes, "win32com": win32com, "win32com.client": client,
            "win32com.client.dynamic": dynamic}
    return mods, st


class ComHiloTests(Base):
    def real(self, app, abierto=False, **kw):
        """outlook_candidates por el camino de verdad (_conectar), con el pywin32 falso y «en Windows»."""
        mods, st = fake_pywin32(app, **kw)
        with mock.patch.dict(sys.modules, mods), mock.patch.object(calendario, "WINDOWS", True), \
                mock.patch.object(calendario, "_outlook_abierto", lambda: abierto), \
                mock.patch.object(calendario.time, "sleep", lambda s: None):
            out = calendario.outlook_candidates(now=NOW, fmt=DIA)
        return out, st

    def test_el_doble_es_fiel_sin_coinitialize_falla(self):
        """Control: el COM falso exige CoInitialize en el hilo, como el de verdad."""
        mods, _ = fake_pywin32(FakeApp([], []))
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(FakeComError) as cm:
                calendario._conectar()  # desde este hilo, sin CoInitialize
        self.assertEqual(cm.exception.hresult, calendario.CO_E_NOTINITIALIZED)

    def test_coinitialize_y_couninitialize_en_el_hilo_que_consulta(self):
        it = FakeItem("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30))
        (cands, reason), st = self.real(FakeApp([it], []))
        self.assertIsNone(reason)
        self.assertEqual(cands[0]["subject"], "Comité")
        hilos = {c[1] for c in st["calls"] if c[0] in ("CoInitialize", "GetActiveObject", "CoUninitialize",
                                                       "dynamic.Dispatch")}
        self.assertEqual(len(hilos), 1)
        self.assertNotIn(threading.get_ident(), hilos)  # nunca en el hilo del servidor
        nombres = [c[0] for c in st["calls"] if c[0] != "QueryInterface"]
        self.assertEqual(nombres, ["CoInitialize", "IID", "GetActiveObject", "dynamic.Dispatch", "CoUninitialize"])
        self.assertEqual(sum(st["init"].values()), 0)

    def test_couninitialize_tambien_si_falla(self):
        (cands, reason), st = self.real(FakeApp([], []), active=[FakeComError(calendario.MK_E_UNAVAILABLE, "x")])
        self.assertIsNone(cands)
        self.assertIn("está cerrado", reason)
        self.assertEqual([c[0] for c in st["calls"]][-1], "CoUninitialize")
        self.assertEqual(sum(st["init"].values()), 0)

    def test_sin_outlook_clasico(self):
        (cands, reason), _ = self.real(FakeApp([], []), iid_error=FakeComError(calendario.CO_E_CLASSSTRING, "x"))
        self.assertIsNone(cands)
        self.assertIn("no tiene el Outlook clásico", reason)

    def test_recien_abierto_se_reintenta_una_vez(self):
        it = FakeItem("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30))
        (cands, reason), st = self.real(FakeApp([it], []), abierto=True,
                                        active=[FakeComError(calendario.MK_E_UNAVAILABLE, "x"), None])
        self.assertIsNone(reason)
        self.assertEqual(len([c for c in st["calls"] if c[0] == "GetActiveObject"]), 2)

    def test_abierto_pero_sin_conexion_es_otro_nivel_de_permisos(self):
        e = FakeComError(calendario.MK_E_UNAVAILABLE, "x")
        (cands, reason), st = self.real(FakeApp([], []), abierto=True, active=[e, e])
        self.assertIsNone(cands)
        self.assertIn("administrador", reason)
        (cands, reason), st = self.real(FakeApp([], []), abierto=False, active=[e, e])
        self.assertIn("está cerrado", reason)
        self.assertEqual(len([c for c in st["calls"] if c[0] == "GetActiveObject"]), 1)  # cerrado: sin reintento

    def test_la_excepcion_no_guarda_objetos_com(self):
        """La traza de la excepción del hilo COM guarda sus marcos (y con ellos objetos de Outlook): se suelta."""
        def session():
            raise FakeComError(calendario.RPC_E_CALL_REJECTED, "ocupado")
        caja = calendario._en_com(lambda app, caja: None, 2, session)
        self.assertIsNone(caja["err"].__traceback__)


class ClasificarTests(Base):
    def test_cada_error_su_estado(self):
        casos = [(calendario.MK_E_UNAVAILABLE, None, "cerrado"), (calendario.MK_E_UNAVAILABLE, True, "sin_permiso"),
                 (calendario.CO_E_SERVER_EXEC_FAILURE, True, "sin_permiso"),
                 (calendario.RPC_E_DISCONNECTED, True, "cerrado"), (calendario.CO_E_CLASSSTRING, None, "sin_outlook"),
                 (calendario.REGDB_E_CLASSNOTREG, None, "sin_outlook"),
                 (calendario.RPC_E_CALL_REJECTED, None, "ocupado"),
                 (calendario.RPC_E_SERVERCALL_RETRYLATER, None, "ocupado"),
                 (calendario.E_ACCESSDENIED, None, "sin_permiso"), (calendario.TYPE_E_LIBNOTREGISTERED, None, "error"),
                 (calendario.DISP_E_EXCEPTION, None, "error")]
        for hr, abierto, esperado in casos:
            with self.subTest(hex(hr & 0xFFFFFFFF)):
                self.assertEqual(calendario.clasificar(FakeComError(hr, "x"), abierto)[0], esperado)
        self.assertEqual(calendario.clasificar(ImportError("No module named 'pythoncom'"))[0], "sin_pywin32")

    def test_el_codigo_de_dentro_de_disp_e_exception(self):
        """Outlook envuelve sus errores en DISP_E_EXCEPTION: el de verdad va en excepinfo[5]."""
        e = FakeComError(calendario.DISP_E_EXCEPTION, "Exception occurred.",
                         (4096, "Microsoft Outlook", "The operation failed.", None, 0, calendario.E_ACCESSDENIED), None)
        self.assertEqual(calendario.clasificar(e)[0], "sin_permiso")
        e = FakeComError(calendario.DISP_E_EXCEPTION, "Exception occurred.",
                         (4096, "Microsoft Outlook", "Cannot parse condition.", None, 0, -2147352567), None)
        estado, frase = calendario.clasificar(e)
        self.assertEqual(estado, "error")
        self.assertIn("Cannot parse condition", frase)


# ----------------------------------------------------------------------------- 3. el filtro de fechas

class ItemsConFormato(FakeItems):
    """Restrict que solo entiende las fechas con `entiende` (y, con `al_reves`, lee día y mes cambiados)."""

    def __init__(self, items, log, entiende, al_reves=None, otro_dia=None):
        super().__init__(items, log)
        self.entiende, self.al_reves, self.otro_dia = entiende, al_reves, otro_dia

    def Restrict(self, flt):
        self._log.append(("Restrict", flt))
        if self.al_reves and self.al_reves.search(flt):
            return FakeItems(list(self.otro_dia), self._log)
        if not self.entiende.search(flt):
            raise FakeComError(calendario.DISP_E_EXCEPTION, "Exception occurred.",
                               (4096, "Microsoft Outlook", f"Cannot parse condition. Error at \"{flt[:20]}\".", None, 0,
                                calendario.DISP_E_EXCEPTION), None)
        return FakeItems(list(self._items), self._log)


class FiltroTests(Base):
    def app(self, items, **kw):
        log = []

        class NS(FakeNamespace):
            def GetDefaultFolder(s, n):
                return types.SimpleNamespace(Items=ItemsConFormato(list(items), log, **kw))

        return types.SimpleNamespace(GetNamespace=lambda name: NS(items, log)), log

    def test_si_outlook_no_entiende_la_fecha_regional_se_prueba_otra(self):
        it = FakeItem("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30))
        app, log = self.app([it], entiende=re.compile(r"'\d{4}-\d\d-\d\d \d\d:\d\d'"))
        fmts = (("regional", lambda d: d.strftime("%d.%m.%Y %H.%M")),) + calendario.FORMATOS[1:]
        with mock.patch.object(calendario, "FORMATOS", fmts):
            cands, reason = calendario.outlook_candidates(now=NOW, session=lambda: app)
        self.assertIsNone(reason)
        self.assertEqual(cands[0]["subject"], "Comité")
        self.assertEqual([f for k, f in log if k == "Restrict"],
                         ["[Start] <= '07.11.2026 10.40' AND [End] >= '07.11.2026 10.25'",
                          "[Start] <= '2026-11-07 10:40' AND [End] >= '2026-11-07 10:25'"])

    def test_si_lee_dia_y_mes_al_reves_se_nota_y_se_prueba_otra(self):
        """Un formato que Outlook entiende mal no da error: da citas de otro día. Si todas caen fuera de la hora
        pedida, el formato no valía."""
        bien = FakeItem("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30))
        otro = FakeItem("Del 11 de julio", datetime(2026, 7, 11, 10, 0), datetime(2026, 7, 11, 11, 0))
        app, log = self.app([bien], entiende=re.compile("."), al_reves=re.compile(r"'07/11/2026"), otro_dia=[otro])
        fmts = (("regional", lambda d: d.strftime("%d/%m/%Y %H:%M")),) + calendario.FORMATOS[1:]
        with mock.patch.object(calendario, "FORMATOS", fmts):
            cands, reason = calendario.outlook_candidates(now=NOW, session=lambda: app)
        self.assertEqual([m["subject"] for m in cands], ["Comité"])
        self.assertEqual(len([k for k, _ in log if k == "Restrict"]), 2)

    def test_ninguno_vale_y_se_dice(self):
        app, _ = self.app([], entiende=re.compile("NUNCA"))
        cands, reason = calendario.outlook_candidates(now=NOW, session=lambda: app)
        self.assertIsNone(cands)
        self.assertIn("Cannot parse condition", reason)

    def test_un_dia_sin_citas_no_prueba_mas_formatos(self):
        app, log = self.app([], entiende=re.compile("."))
        self.assertEqual(calendario.outlook_candidates(now=NOW, session=lambda: app), ([], None))
        self.assertEqual(len([k for k, _ in log if k == "Restrict"]), 1)


# ----------------------------------------------------------------------------- 4. citas periódicas

class SerieItems(FakeItems):
    """Lo documentado (Items.IncludeRecurrences): las repeticiones solo salen si se ordena por Start, LUEGO se pone
    IncludeRecurrences y LUEGO se filtra; y la colección de una serie sin fin no se acaba nunca."""

    def __init__(self, serie_inicio, log, duracion=timedelta(minutes=30), cada=timedelta(days=1)):
        super().__init__([], log)
        self.inicio, self.duracion, self.cada, self.ordenado, self.orden_ok = serie_inicio, duracion, cada, False, False

    def Sort(self, prop, desc=False):
        self._log.append(("Sort", prop))
        self.ordenado, self.orden_ok = prop == "[Start]" and not desc, False

    @FakeItems.IncludeRecurrences.setter
    def IncludeRecurrences(self, v):
        self._log.append(("IncludeRecurrences", v))
        self._include, self.orden_ok = v, bool(v and self.ordenado)

    def Restrict(self, flt):
        self._log.append(("Restrict", flt))
        maestro = FakeItem("Daily", self.inicio, self.inicio + self.duracion, recurring=True)
        if not self.orden_ok:
            return FakeItems([maestro], self._log)  # sin repeticiones: solo la cita original (de hace meses)
        outer = self

        class Infinita(FakeItems):
            def GetFirst(s):
                s._n = 0
                return s.GetNext()

            def GetNext(s):
                d = outer.inicio + outer.cada * s._n
                s._n += 1
                return FakeItem("Daily", d, d + outer.duracion, recurring=True)

        return Infinita([], self._log)


class PeriodicasTests(Base):
    def test_la_repeticion_de_hoy_sale_y_el_bucle_acaba(self):
        """La serie empezó hace 40 días a las 10:15 y no tiene fin: la de hoy se encuentra, y el recorrido se corta en
        MAX_CITAS aunque la colección no se acabe."""
        log = []
        inicio = datetime(2026, 9, 28, 10, 15)

        class NS(FakeNamespace):
            def GetDefaultFolder(s, n):
                return types.SimpleNamespace(Items=SerieItems(inicio, log))

        app = types.SimpleNamespace(GetNamespace=lambda name: NS([], log))
        with mock.patch.object(calendario, "MAX_CITAS", 60):
            cands, reason = calendario.outlook_candidates(now=NOW, session=lambda: app, fmt=DIA)
        self.assertIsNone(reason)
        self.assertEqual([(m["start"], m["recurring"]) for m in cands], [("2026-11-07T10:15", True)])
        self.assertEqual([k for k, _ in log], ["Sort", "IncludeRecurrences", "Restrict"])

    def test_el_doble_es_fiel_en_otro_orden_no_hay_repeticiones(self):
        """Control: si se pusiera IncludeRecurrences antes de ordenar, la de hoy no saldría."""
        log = []
        items = SerieItems(datetime(2026, 9, 28, 10, 15), log)
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        sel = items.Restrict("x")
        self.assertEqual(sel.GetFirst().Start, datetime(2026, 9, 28, 10, 15))
        self.assertIsNone(sel.GetNext())


# ----------------------------------------------------------------------------- 5. lo protegido (aviso de Outlook)

class Vigilado(FakeItem):
    """Cuenta los accesos a lo protegido; `aviso` (Event) hace que leer el organizador espere, como el aviso de
    seguridad de Outlook hasta que el usuario contesta; `deniega`, que falle."""

    def __init__(self, *a, aviso=None, deniega=False, **k):
        super().__init__(*a, **k)
        self._accesos, self._aviso, self._deniega = [], aviso, deniega
        self.StartUTC = self.Start - timedelta(hours=1)  # no protegido (lo usa «Probar» para comparar las horas)

    def __getattribute__(self, name):
        if name in ("Organizer", "Recipients", "Body", "RequiredAttendees", "OptionalAttendees"):
            object.__getattribute__(self, "_accesos").append(name)
            if object.__getattribute__(self, "_deniega"):
                raise FakeComError(calendario.E_ABORT, "Operation aborted")
            aviso = object.__getattribute__(self, "_aviso")
            if aviso is not None and name == "Organizer":
                aviso.wait(10)
        return super().__getattribute__(name)


class ProtegidasTests(Base):
    def test_solo_se_lee_lo_protegido_de_la_elegida(self):
        a = Vigilado("En curso", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30), required=("Luis Pérez",))
        b = Vigilado("Luego", NOW + timedelta(minutes=8), NOW + timedelta(minutes=40), required=("Eva",))
        m, reason = calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([a, b], []), fmt=DIA)
        self.assertEqual((m["subject"], m["attendees"], m["organizer"]),
                         ("En curso", ["Luis Pérez"], "Ana Organizadora"))
        self.assertEqual(b._accesos, [])
        self.assertEqual(calendario.ASISTENTES["estado"], "si")

    def test_si_outlook_pregunta_llega_lo_basico_y_no_se_vuelve_a_preguntar_solo(self):
        aviso = threading.Event()
        it = Vigilado("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30), aviso=aviso)
        t0 = time.monotonic()
        with mock.patch.object(calendario, "LENTO", 0.3):
            m, reason = calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertLess(time.monotonic() - t0, 2)  # no espera los 8 s: el nombre y la hora ya estaban
        self.assertIsNone(reason)
        self.assertEqual((m["subject"], m["attendees"]), ("Comité", []))
        self.assertIn("asistentes", m["blocked"])
        self.assertEqual(calendario.ASISTENTES["estado"], "pregunta")
        # mientras el aviso sigue abierto, la consulta siguiente lo dice
        m2, reason2 = calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertIsNone(m2)
        self.assertIn("te está preguntando", reason2)
        aviso.set()  # el usuario contesta «Permitir»
        for _ in range(100):
            if not calendario._COM_LOCK.locked():
                break
            time.sleep(0.02)
        it._accesos.clear()
        m3, _ = calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertEqual(m3["subject"], "Comité")
        self.assertEqual(it._accesos, [])  # solo (sin «Probar») ya no se le pregunta
        self.assertEqual(calendario.ASISTENTES["estado"], "pregunta")

    def test_si_deniega_se_apunta_y_no_se_insiste(self):
        it = Vigilado("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30), deniega=True)
        m, _ = calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertEqual(m["subject"], "Comité")
        self.assertEqual(it._accesos, ["Organizer"])  # al primer fallo se para
        self.assertEqual(calendario.ASISTENTES["estado"], "deniega")
        it._accesos.clear()
        calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertEqual(it._accesos, [])

    def test_probar_lo_fuerza_y_la_directiva_manda(self):
        calendario.ASISTENTES["estado"] = "pregunta"
        it = Vigilado("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30), required=("Luis Pérez",))
        m, _ = calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA, protegidas=True)
        self.assertEqual(m["attendees"], ["Luis Pérez"])
        self.assertEqual(calendario.ASISTENTES["estado"], "si")  # ya no pregunta: vuelve a leerlos solo
        it._accesos.clear()
        with mock.patch.object(calendario, "politica_asistentes", lambda: "deniega"):
            calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertEqual(it._accesos, [])

    def test_al_cambiar_se_guarda(self):
        vistos = []
        with mock.patch.object(calendario, "al_cambiar", vistos.append):
            it = Vigilado("Comité", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30), deniega=True)
            calendario.outlook_current_meeting(now=NOW, session=lambda: FakeApp([it], []), fmt=DIA)
        self.assertEqual(vistos, ["deniega"])


# ----------------------------------------------------------------------------- 6. el estado en una frase

ENV = {"windows": True, "pywin32": True, "instalado": True, "abierto": True, "nuevo_abierto": False,
       "nuevo_activado": False, "politica": None, "python_bits": 64, "office_bits": "x64", "admin": False}


def agenda(*horas):
    """Citas de hoy: (inicio, fin) en horas («9:30» → 9.5)."""
    out = []
    for i, (a, b) in enumerate(horas):
        d0 = datetime(2026, 11, 7) + timedelta(hours=a)
        it = FakeItem(f"Reunión secreta {i}", d0, datetime(2026, 11, 7) + timedelta(hours=b),
                      organizer="Ana Organizadora", required=("Luis Pérez",))
        it.StartUTC = d0 - timedelta(hours=1)
        out.append(it)
    return out


class EstadoTests(Base):
    def estado(self, activado=True, probar=False, env=None, items=None, session=None, now=NOW, cache=False, **kw):
        if not cache:
            calendario._ESTADO_CACHE.clear()
        env = dict(ENV, **(env or {}))
        if session is None and items is not None:
            def session():
                return FakeApp(list(items), [])
        with mock.patch.object(calendario, "log", lambda m: self.logs.append(m)):
            return calendario.estado(activado, probar=probar, now=now, entorno=env, session=session, fmt=DIA, **kw)

    def setUp(self):
        super().setUp()
        self.logs = []

    def test_contrato(self):
        r = self.estado(items=agenda((11, 12)))
        self.assertLessEqual({"nivel", "estado", "frase", "accion"}, set(r))
        self.assertEqual(json.loads(json.dumps(r)), r)  # JSON tal cual

    def test_conectado_con_la_proxima(self):
        r = self.estado(items=agenda((8, 9), (11, 12)))
        self.assertEqual((r["nivel"], r["estado"], r["accion"]), (2, "conectado", None))
        self.assertEqual(r["frase"], "Outlook clásico: conectado · próxima reunión 11:00")
        self.assertEqual(r["proxima"], {"hora": "11:00", "fin": "12:00", "ahora": False})

    def test_conectado_en_curso_y_sin_mas(self):
        r = self.estado(items=agenda((10, 11), (11.5, 12)))
        self.assertEqual(r["frase"], "Outlook clásico: conectado · reunión en curso hasta las 11:00")
        self.assertTrue(r["proxima"]["ahora"])
        r = self.estado(items=agenda((8, 9)))
        self.assertEqual(r["frase"], "Outlook clásico: conectado · hoy no tienes más reuniones")
        self.assertEqual((r["estado"], r["nivel"]), ("conectado", 2))  # sin reunión ahora, conectado no es un aviso

    def test_apagado_ofrece_activar(self):
        r = self.estado(activado=False, items=agenda((11, 12)))
        self.assertEqual((r["estado"], r["nivel"]), ("apagado", 1))
        self.assertEqual(r["accion"], {"id": "activar", "texto": "Activar"})
        self.assertIn("Tienes el Outlook clásico", r["frase"])
        r = self.estado(activado=False, env={"instalado": None}, items=[])
        self.assertNotIn("Tienes el Outlook clásico", r["frase"])  # no se sabe: no se afirma

    def test_probar_con_la_casilla_apagada_dice_que_funciona(self):
        r = self.estado(activado=False, probar=True, items=agenda((11, 12)))
        self.assertEqual((r["estado"], r["accion"]["id"]), ("apagado", "activar"))
        self.assertIn("responde (próxima reunión 11:00)", r["frase"])
        self.assertTrue(r["pasos"])

    def test_sin_outlook_nuevo_cerrado_y_pywin32(self):
        casos = [({"instalado": False}, "sin_outlook", None), ({"instalado": False, "nuevo_abierto": True},
                                                               "outlook_nuevo", None),
                 ({"abierto": False}, "cerrado", "probar"), ({"abierto": False, "nuevo_activado": True},
                                                             "outlook_nuevo", None),
                 ({"pywin32": False}, "sin_pywin32", None)]
        for env, estado, accion in casos:
            with self.subTest(env):
                calendario._ESTADO_CACHE.clear()
                r = self.estado(env=env, session=lambda: self.fail("no tenía que hablar con Outlook"))
                self.assertEqual((r["estado"], r["nivel"]), (estado, 1))
                self.assertEqual((r["accion"] or {}).get("id"), accion)
        r = self.estado(env={"windows": False})
        self.assertEqual((r["estado"], r["nivel"]), ("no_windows", 0))

    def test_errores_de_com(self):
        def lanza(hr):
            def session():
                raise FakeComError(hr, "x")
            return session
        casos = [(calendario.CO_E_CLASSSTRING, {}, "sin_outlook"),
                 (calendario.CO_E_CLASSSTRING, {"nuevo_activado": True}, "outlook_nuevo"),
                 (calendario.RPC_E_CALL_REJECTED, {}, "ocupado"), (calendario.E_ACCESSDENIED, {}, "sin_permiso")]
        for hr, env, estado in casos:
            with self.subTest(estado):
                r = self.estado(probar=True, env=env, session=lanza(hr))
                self.assertEqual(r["estado"], estado)
                self.assertEqual(r["pasos"][-1]["id"], "conexion")
        with mock.patch.object(calendario, "_outlook_abierto", lambda: True):
            r = self.estado(probar=True, session=lanza(calendario.MK_E_UNAVAILABLE))
        self.assertEqual((r["estado"], r["accion"]["id"]), ("sin_permiso", "probar"))

    def test_outlook_colgado(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def session():
            gate.wait(5)
            return FakeApp([], [])
        r = self.estado(probar=True, session=session, timeout=0.3)
        self.assertEqual((r["estado"], r["accion"]["id"]), ("ocupado", "probar"))
        gate.set()
        for _ in range(100):
            if not calendario._COM_LOCK.locked():
                break
            time.sleep(0.02)

    def test_probar_los_asistentes(self):
        r = self.estado(probar=True, items=agenda((11, 12)))
        self.assertEqual(r["asistentes"], "si")
        self.assertIn({"id": "asistentes", "ok": True, "texto": "Outlook deja leer los asistentes"}, r["pasos"])
        calendario._ESTADO_CACHE.clear()
        items = [Vigilado("Comité", datetime(2026, 11, 7, 11), datetime(2026, 11, 7, 12), deniega=True)]
        r = self.estado(probar=True, items=items)
        self.assertEqual(r["asistentes"], "deniega")
        self.assertIn("sin asistentes: tu empresa no deja", r["frase"])
        r = self.estado(items=agenda((11, 12)))  # sin «Probar» no los lee: se queda lo sabido
        self.assertIn("sin asistentes", r["frase"])

    def test_probar_con_aviso_de_outlook(self):
        aviso = threading.Event()
        self.addCleanup(aviso.set)
        items = [Vigilado("Comité", datetime(2026, 11, 7, 11), datetime(2026, 11, 7, 12), aviso=aviso)]
        r = self.estado(probar=True, items=items, timeout=0.5)
        self.assertEqual((r["estado"], r["asistentes"]), ("conectado", "pregunta"))
        self.assertIn("Outlook pide permiso", r["frase"])
        aviso.set()

    def test_sin_probar_no_toca_lo_protegido_ni_repite_en_un_minuto(self):
        items = [Vigilado("Comité", datetime(2026, 11, 7, 11), datetime(2026, 11, 7, 12))]
        n = []

        def session():
            n.append(1)
            return FakeApp(list(items), [])
        self.estado(session=session)
        self.estado(session=session, cache=True)
        self.assertEqual((len(n), items[0]._accesos), (1, []))
        self.estado(session=session, probar=True, cache=True)
        self.assertEqual(len(n), 2)

    def test_las_horas_en_los_pasos(self):
        with ZonaMadrid():
            if not hasattr(time, "tzset"):
                self.skipTest("sin time.tzset")
            r = self.estado(probar=True, items=agenda((11, 12)))
        self.assertIn({"id": "horas", "ok": True, "texto": "Las horas de Outlook cuadran con las de Windows"},
                      r["pasos"])

    def test_nada_de_asuntos_ni_nombres_en_la_frase_los_pasos_ni_server_log(self):
        for activado, probar in ((True, False), (True, True), (False, True)):
            calendario._ESTADO_CACHE.clear()
            r = self.estado(activado=activado, probar=probar, items=agenda((10, 11), (11.5, 12)))
            texto = json.dumps(r, ensure_ascii=False) + "\n".join(self.logs)
            for s in ("secreta", "Ana", "Luis", "Organizadora"):
                self.assertNotIn(s, texto)

    def test_server_log_con_plantilla(self):
        """Lo que escribe el calendario sale en «Copiar diagnóstico» con su parte técnica (plantillas DIAG_LINES)."""
        self.estado(probar=True, items=agenda((11, 12)))
        self.estado(probar=True, session=lambda: (_ for _ in ()).throw(FakeComError(calendario.E_ACCESSDENIED, "x")))
        self.assertTrue(self.logs)
        for linea in self.logs:
            with self.subTest(linea):
                self.assertTrue(any(rx.fullmatch(linea) for rx in server._DIAG_RX), linea)
                self.assertNotIn("<texto>", server.diag_line(linea))  # solo códigos: sale entero
        entorno = [x for x in self.logs if "entorno" in x][0]
        self.assertEqual(server.diag_line(entorno), entorno)  # todo técnico: sale entero


# ----------------------------------------------------------------------------- 7. el servidor y la interfaz

class StubApp:
    def __getattr__(self, name):
        return lambda *a, **k: {"stub": name}

    def calendario_estado(self, q):
        return {"nivel": 1, "estado": "apagado", "frase": "x", "accion": {"id": "activar", "texto": "Activar"},
                "probar": q.get("probar")}

    def calendario_activar(self, b):
        return {"nivel": 2, "estado": "conectado", "frase": "y", "accion": None, "activar": b.get("activar")}


class RutaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls._app, cls._port, cls._token = server.Handler.app, server.PORT, server.Handler.token
        server.Handler.app, server.PORT = StubApp(), cls.port
        server.Handler.token = "prueba-calendario"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.Handler.app, server.PORT, server.Handler.token = cls._app, cls._port, cls._token

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={server.TOKEN_HEADER: server.Handler.token, "X-Audios": "1",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def test_rutas(self):
        self.assertEqual(self.call("/api/calendario/estado")["estado"], "apagado")
        self.assertEqual(self.call("/api/calendario/estado?probar=1")["probar"], "1")
        self.assertEqual(self.call("/api/calendario/activar", {"activar": True})["activar"], True)


class AppTests(Base):
    def app(self):
        tmp = tempfile.mkdtemp(prefix="notas-cal-")
        self.addCleanup(shutil.rmtree, tmp, True)
        a = server.App.__new__(server.App)
        a.settings = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
        return a

    def test_activar_guarda_y_prueba(self):
        a, vistos = self.app(), []
        with mock.patch.object(calendario, "estado", lambda on, probar=False: vistos.append((on, probar)) or {}):
            a.calendario_activar({"activar": True})
            self.assertIs(a.settings.get()["outlook_on"], True)
            a.calendario_estado({})
            a.calendario_estado({"probar": "1"})
            a.calendario_activar({"activar": "no"})  # solo True enciende
            self.assertIs(a.settings.get()["outlook_on"], False)
        self.assertEqual(vistos, [(True, True), (True, False), (True, True), (False, False)])

    def test_lo_sabido_de_los_asistentes_se_guarda(self):
        self.assertEqual(server.DEFAULT_SETTINGS["outlook_asistentes"], "")
        a = self.app()
        calendario.ASISTENTES["estado"] = a.settings.get().get("outlook_asistentes") or None
        with mock.patch.object(calendario, "al_cambiar",
                               lambda v: a.settings.update(lambda d: d.update(outlook_asistentes=v))), \
                mock.patch.object(calendario, "log"):
            calendario._marcar_asistentes("pregunta")
        self.assertEqual(a.settings.get()["outlook_asistentes"], "pregunta")

    def test_el_init_de_la_app_lo_carga(self):
        src = open(os.path.join(os.path.dirname(calendario.__file__), "server.py"), encoding="utf-8").read()
        self.assertIn('calendario.ASISTENTES["estado"] = self.settings.get().get("outlook_asistentes") or None', src)
        self.assertIn("calendario.al_cambiar = lambda v: self.settings.update(", src)


STATIC = os.path.join(os.path.dirname(calendario.__file__), "static")


class FrontendTests(unittest.TestCase):
    def js(self):
        with open(os.path.join(STATIC, "reunion.js"), encoding="utf-8") as f:
            return f.read()

    def test_probar_usa_el_estado_y_activar_guarda(self):
        js = self.js()
        for s in ("/api/calendario/estado?probar=1", "'/api/calendario/activar', { activar: true }", 'id="rnEstado"',
                  "api('/api/calendario/estado')", "data-rn-accion"):
            self.assertIn(s, js, s)

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_la_frase_con_su_color_y_su_boton(self):
        src = self.js()
        defs = ("const esc = s => String(s ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"
                "'\"':'&quot;',\"'\":'&#39;'}[c]));\n")
        defs += re.search(r"^const RN_AVISO = .*?;$", src, re.M).group(0) + "\n"
        defs += re.search(r"^function reunionEstadoHtml\(.*?^}", src, re.S | re.M).group(0)
        code = defs + """
console.log(reunionEstadoHtml({estado: 'conectado', frase: 'Outlook clásico: conectado · próxima reunión 10:00',
  accion: null}));
console.log(reunionEstadoHtml({estado: 'apagado', frase: 'Tienes <b>', accion: {id: 'activar', texto: 'Activar'}}));
console.log(reunionEstadoHtml({estado: 'sin_pywin32', frase: 'Falta', accion: null,
  pasos: [{id: 'a', ok: true, texto: 'uno'}, {id: 'b', ok: false, texto: 'dos'}, {id: 'c', ok: null, texto: 'tres'}]}));
console.log(JSON.stringify(reunionEstadoHtml(null)));
"""
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(lines[0], '<span style="color:var(--ok)">Outlook clásico: conectado · próxima reunión '
                                   '10:00</span>')
        self.assertEqual(lines[1], '<span>Tienes &lt;b&gt;</span> <button class="btn sm" type="button" '
                                   'data-rn-accion="activar">Activar</button>')
        self.assertEqual(lines[2], '<span style="color:var(--warn)">Falta</span><ul style="margin:6px 0 0;'
                                   'padding-left:18px"><li>✓ uno</li><li>✖ dos</li><li>· tres</li></ul>')
        self.assertEqual(lines[3], '""')


if __name__ == "__main__":
    unittest.main()
