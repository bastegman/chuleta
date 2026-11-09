# -*- coding: utf-8 -*-
"""Calendario en Windows de verdad (fix-calendario): pywin32 real, fechas COM, procesos y registro. Los runners de
GitHub no tienen Office: lo que se mide aquí es todo lo que no es Outlook (que pywin32 se importa y habla COM, que
pywin32 entrega las fechas COM con la hora tal cual y la zona UTC pegada, que calendario.estado llega a COM y dice
«sin_outlook» deprisa). Fuera de Windows se saltan todas.

Variables del workflow: NOTAS_EXIGE_PYWIN32=1 (reunion-windows, windows-real: ahí pywin32 se instala, así que si no se
importa es un fallo, no un salto) y NOTAS_TZ_PRUEBA=1 (windows-real pone la zona de Madrid: el desfase no es 0 y la
conversión equivocada de antes se notaría). Artefactos en NOTAS_ARTEFACTOS."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

from support import APP  # noqa: F401  (prepara sys.path con _app)
import calendario  # noqa: E402

WIN = sys.platform == "win32"
ART = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(tempfile.gettempdir(), "notas-artefactos")
EXIGE = os.environ.get("NOTAS_EXIGE_PYWIN32") == "1"


def hay_pywin32():
    try:
        import pythoncom  # noqa: F401
        import pywintypes  # noqa: F401
        import win32com.client  # noqa: F401
        import win32com.client.dynamic  # noqa: F401
        return True
    except ImportError:
        return False


def artefacto(nombre, datos):
    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, nombre), "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1, default=str)


def reset():
    calendario.ASISTENTES["estado"] = None
    calendario._ESTADO_CACHE.clear()
    calendario._POLITICA[:] = []


@unittest.skipUnless(WIN, "solo Windows")
class Pywin32RealTests(unittest.TestCase):
    def setUp(self):
        reset()
        if not hay_pywin32():
            if EXIGE:
                self.fail("pywin32 no se importa y este workflow lo instala (NOTAS_EXIGE_PYWIN32=1)")
            self.skipTest("sin pywin32 (tests.yml no lo instala)")

    def test_se_importa_y_habla_com(self):
        import pythoncom
        import win32com.client.dynamic
        from importlib.metadata import version
        pythoncom.CoInitialize()
        try:
            d = win32com.client.dynamic.Dispatch("Scripting.Dictionary")
            d.Add("k", 41)
            self.assertEqual(d.Item("k") + 1, 42)
        finally:
            d = None
            pythoncom.CoUninitialize()
        artefacto("pywin32.json", {"version": version("pywin32"), "python": sys.version})

    def test_fecha_com_hora_tal_cual_con_utc_pegada(self):
        """La causa principal: pywin32 (PyTime.cpp, PyWinObject_FromDATE → FromSYSTEMTIME) pega UTC a las fechas COM
        sin convertirlas. FileSystemObject da la hora de modificación de un archivo en hora local, como Outlook da
        Start/End: calendario._local tiene que dar la misma hora que os.path.getmtime en hora local."""
        import pythoncom
        import win32com.client.dynamic
        fd, path = tempfile.mkstemp(prefix="notas-fecha-")
        os.close(fd)
        self.addCleanup(os.remove, path)
        pythoncom.CoInitialize()
        try:
            fso = win32com.client.dynamic.Dispatch("Scripting.FileSystemObject")
            v = fso.GetFile(path).DateLastModified
        finally:
            fso = None
            pythoncom.CoUninitialize()
        local = datetime.fromtimestamp(os.path.getmtime(path)).replace(microsecond=0)
        offset = datetime.now().astimezone().utcoffset()
        antes = v.astimezone().replace(tzinfo=None)  # lo que hacía calendario._local hasta cf24597
        artefacto("fecha_com.json", {"pywin32": repr(v), "tzinfo": repr(v.tzinfo), "local": local.isoformat(),
                                     "ahora_local": calendario._local(v).isoformat(), "antes": antes.isoformat(),
                                     "desfase_windows_min": offset.total_seconds() / 60,
                                     "zona": time.tzname})
        self.assertIsNotNone(v.tzinfo)
        self.assertEqual(v.utcoffset(), timedelta(0))  # «UTC»…
        self.assertLessEqual(abs((calendario._local(v) - local).total_seconds()), 2)  # …pero con la hora local
        if os.environ.get("NOTAS_TZ_PRUEBA") == "1":
            self.assertNotEqual(offset, timedelta(0), "el workflow tenía que poner una zona con desfase (Madrid)")
        if offset:
            self.assertGreaterEqual(abs((antes - local).total_seconds()), 59 * 60)  # antes se movía el desfase

    def test_estado_real_sin_outlook(self):
        t0 = time.monotonic()
        r = calendario.estado(True, probar=True)
        dt = time.monotonic() - t0
        artefacto("estado_probar.json", dict(r, segundos=round(dt, 2)))
        self.assertEqual((r["estado"], r["nivel"]), ("sin_outlook", 1), r)
        self.assertIn("no tiene el Outlook clásico", r["frase"])
        self.assertLess(dt, 10)
        ids = [p["id"] for p in r["pasos"]]
        self.assertEqual(ids[:2], ["pywin32", "outlook_clasico"])
        self.assertIn("conexion", ids)  # con «Probar» se habla COM aunque el registro ya lo diga
        calendario._ESTADO_CACHE.clear()
        self.assertEqual(calendario.estado(True)["estado"], "sin_outlook")  # sin «Probar», basta el registro
        self.assertEqual(calendario.estado(False)["estado"], "sin_outlook")  # sin Outlook no se ofrece activarlo

    def test_agenda_real_sin_outlook(self):
        """La agenda de un mes de Hoy (B7) por el camino de verdad (pywin32 y el hilo COM): sin Outlook, «sin_outlook»
        deprisa; y lo que el hilo de la agenda sabe sin hablar con Outlook (sin_leer) dice lo mismo."""
        t0 = time.monotonic()
        datos, fallo = calendario.agenda()
        dt = time.monotonic() - t0
        artefacto("agenda.json", {"fallo": fallo, "segundos": round(dt, 2)})
        self.assertEqual((datos, fallo[0]), (None, "sin_outlook"))
        self.assertIn("no tiene el Outlook clásico", fallo[1])
        self.assertLess(dt, 10)
        self.assertEqual(calendario.sin_leer(calendario.entorno_outlook())[0], "sin_outlook")

    def test_reunion_actual_real_sin_outlook(self):
        t0 = time.monotonic()
        m, reason = calendario.outlook_current_meeting()
        self.assertIsNone(m)
        self.assertIn("no tiene el Outlook clásico", reason)
        self.assertLess(time.monotonic() - t0, 9)


@unittest.skipUnless(WIN, "solo Windows")
class EntornoRealTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_procesos(self):
        p = calendario._procesos()
        self.assertIsInstance(p, frozenset)
        self.assertIn(os.path.basename(sys.executable).lower(), p)
        self.assertIs(calendario._outlook_abierto(), "outlook.exe" in p)

    def test_entorno_sin_office(self):
        e = calendario.entorno_outlook()
        artefacto("entorno.json", e)
        self.assertTrue(e["windows"])
        self.assertEqual(e["pywin32"], hay_pywin32())
        self.assertIs(e["instalado"], False, "¿el runner trae Office? entonces este test ya no mide lo mismo")
        self.assertIs(e["abierto"], False)
        self.assertIs(e["nuevo_abierto"], False)
        self.assertIs(e["nuevo_activado"], False)
        self.assertIsNone(e["politica"])
        self.assertIn(e["python_bits"], (32, 64))
        self.assertIsInstance(e["admin"], bool)

    def test_sin_pywin32_el_estado_no_se_equivoca(self):
        """En tests.yml no hay pywin32: sin Outlook, lo que importa es eso (no «instala pywin32»)."""
        if hay_pywin32():
            self.skipTest("con pywin32")
        self.assertEqual(calendario.estado(True)["estado"], "sin_outlook")
        calendario._ESTADO_CACHE.clear()
        self.assertEqual(calendario.estado(True, probar=True)["estado"], "sin_outlook")
        self.assertIs(calendario.entorno_outlook()["pywin32"], False)

    def test_la_directiva_se_lee_del_registro(self):
        """Learn («results for advanced diagnostic indicate offline scan was forced»): AdminSecurityMode = 3 y
        PromptOOMAddressInformationAccess 0/1/2. Se escribe en una copia de HKCU de prueba (y se borra)."""
        import winreg
        base = r"Software\Policies\Microsoft\Office\16.0\Outlook\Security"
        try:
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, base)
        except OSError as e:
            self.skipTest(f"no se puede escribir en HKCU\\Software\\Policies ({e})")
        self.addCleanup(self._borrar, winreg, base)
        with k:
            winreg.SetValueEx(k, "AdminSecurityMode", 0, winreg.REG_DWORD, 3)
            for v, esperado in ((0, "deniega"), (1, "pregunta"), (2, "aprueba")):
                winreg.SetValueEx(k, "PromptOOMAddressInformationAccess", 0, winreg.REG_DWORD, v)
                calendario._POLITICA[:] = []
                self.assertEqual(calendario.politica_asistentes(), esperado)
            winreg.SetValueEx(k, "AdminSecurityMode", 0, winreg.REG_DWORD, 0)
            calendario._POLITICA[:] = []
            self.assertIsNone(calendario.politica_asistentes())

    @staticmethod
    def _borrar(winreg, base):
        calendario._POLITICA[:] = []
        partes = base.split("\\")
        for i in range(len(partes), 3, -1):  # hasta Software\Policies\Microsoft (que ya existía)
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, "\\".join(partes[:i]))
            except OSError:
                break


if __name__ == "__main__":
    unittest.main()
