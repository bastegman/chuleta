# -*- coding: utf-8 -*-
"""Hoy (rediseño B7, msgs 1380 y 1390): la agenda de Outlook de un mes, la reunión de ahora que sale de ella, la
caché en segundo plano, /api/hoy y las funciones puras de hoy.js. Con el COM falso de test_reunion/test_calendario:
nada de aquí necesita Windows (lo de Windows de verdad, en test_calendario_windows).

El B2 del 09/11/2026 («conecta bien al parecer, pero no veo las siguientes reuniones», msg 1380): la lectura COM era
correcta (su «Probar» daba agenda, horas y asistentes bien), pero solo pedía HOY (agenda_hoy, 00:00-23:59) y de esa
solo enseñaba una frase («próxima reunión 12:00»): ninguna pantalla listaba las citas. AgendaB2Tests lo deja escrito."""
import json
import os
import re
import shutil
import subprocess
import threading
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

from support import REPO, TempTree, bare_jobs, server  # noqa: F401  (support prepara sys.path)
import calendario  # noqa: E402
import plan  # noqa: E402
from test_calendario import Base, SerieItems, pywin32_fecha  # noqa: E402
from test_perfil import settings_store  # noqa: E402
from test_reunion import FakeApp, FakeItem, FakeNamespace  # noqa: E402

NOW = datetime(2026, 11, 9, 10, 30)  # lunes
DIA = (lambda d: d.strftime("%d/%m/%Y %H:%M"))
PROTEGIDAS = {"Organizer", "RequiredAttendees", "OptionalAttendees", "Recipients", "Body", "RTFBody", "Resources",
              "PropertyAccessor"}


class Contada(FakeItem):
    """Apunta cada acceso a lo protegido (que abriría el aviso de seguridad de Outlook)."""
    accesos = []

    def __getattribute__(self, name):
        if name in PROTEGIDAS:
            Contada.accesos.append(name)
        return super().__getattribute__(name)


def cita(asunto, ini, minutos=60, **kw):
    return Contada(asunto, pywin32_fecha(ini), pywin32_fecha(ini + timedelta(minutes=minutos)), **kw)


def leer(items, now=NOW, **kw):
    log = []
    fake = FakeApp(items, log)
    out = calendario.agenda(now=now, session=lambda: fake, fmt=DIA, **kw)
    return out, log


# ----------------------------------------------------------------------------- 1. la agenda de un mes

class AgendaTests(Base):
    def setUp(self):
        super().setUp()
        Contada.accesos = []

    def test_un_mes_por_hora_sin_lo_protegido(self):
        items = [cita("Mañana", NOW + timedelta(days=1)), cita("Ahora", NOW - timedelta(minutes=20), recurring=True),
                 cita("Dentro de 30 días", datetime(2026, 12, 9, 9, 0)),
                 cita("Dentro de 31 días", datetime(2026, 12, 10, 9, 0)),
                 cita("Esta mañana, ya pasada", datetime(2026, 11, 9, 8, 0)), cita("Ayer", NOW - timedelta(days=1)),
                 cita("Cancelada", NOW + timedelta(days=2), status=5), cita("Todo el día", NOW, all_day=True),
                 cita("Mañana, sala", NOW + timedelta(days=1, hours=2), location="Sala 3", categories="Alfa; Beta")]
        (datos, fallo), log = leer(items)
        self.assertIsNone(fallo)
        # desde hoy a las 00:00 hasta el día 31 a las 23:59, con el orden de las periódicas
        self.assertEqual([k for k, _ in log][-3:], ["Sort", "IncludeRecurrences", "Restrict"])
        self.assertEqual(log[-1][1], "[Start] <= '09/12/2026 23:59' AND [End] >= '09/11/2026 00:00'")
        self.assertEqual([c["subject"] for c in datos["citas"]],
                         ["Esta mañana, ya pasada", "Ahora", "Mañana", "Mañana, sala", "Dentro de 30 días"])
        self.assertEqual(set(datos["citas"][0]), {"subject", "start", "end", "location", "is_teams", "recurring",
                                                  "categories"})
        sala = datos["citas"][3]
        self.assertEqual((sala["location"], sala["is_teams"], sala["categories"]), ("Sala 3", False, ["Alfa", "Beta"]))
        self.assertEqual((datos["citas"][1]["recurring"], datos["citas"][1]["is_teams"]), (True, True))
        self.assertEqual(datos["citas"][1]["start"], "2026-11-09T10:10")  # la hora de Outlook, sin convertir
        self.assertEqual((datos["formato"], datos["cortada"]), ("dado", False))
        self.assertEqual(Contada.accesos, [])  # ni organizador ni asistentes: no puede salir el aviso de Outlook

    def test_una_serie_sin_fin_da_sus_repeticiones_del_mes_y_acaba(self):
        log = []

        class NS(FakeNamespace):
            def GetDefaultFolder(s, n):
                return types.SimpleNamespace(Items=SerieItems(datetime(2026, 9, 28, 9, 30), log))

        app = types.SimpleNamespace(GetNamespace=lambda name: NS([], log))
        datos, fallo = calendario.agenda(now=NOW, session=lambda: app, fmt=DIA, max_citas=200)
        self.assertIsNone(fallo)
        self.assertEqual(len(datos["citas"]), 31)  # del 9-nov al 9-dic, una al día
        self.assertEqual((datos["citas"][0]["start"], datos["citas"][-1]["start"]),
                         ("2026-11-09T09:30", "2026-12-09T09:30"))
        self.assertTrue(all(c["recurring"] for c in datos["citas"]))
        self.assertTrue(datos["cortada"])  # el doble no se acaba nunca: se corta en el tope y se dice

    def test_errores_y_fuera_de_windows(self):
        with mock.patch.object(calendario, "WINDOWS", False):
            self.assertEqual(calendario.agenda(now=NOW)[1][0], "no_windows")
        e = Exception(calendario.CO_E_CLASSSTRING, "Invalid class string", None, None)
        e.hresult = calendario.CO_E_CLASSSTRING
        datos, fallo = calendario.agenda(now=NOW, session=lambda: (_ for _ in ()).throw(e), fmt=DIA)
        self.assertEqual((datos, fallo[0]), (None, "sin_outlook"))

    def test_cortada_solo_si_quedaban_mas(self):
        """Revisión 1: con exactamente el tope no está cortada; con una más, sí."""
        tres = [cita(f"C{i}", NOW + timedelta(hours=i)) for i in range(3)]
        (datos, _), _ = leer(tres, max_citas=3)
        self.assertEqual((len(datos["citas"]), datos["cortada"]), (3, False))
        (datos, _), _ = leer(tres + [cita("C3", NOW + timedelta(hours=4))], max_citas=3)
        self.assertEqual((len(datos["citas"]), datos["cortada"]), (3, True))

    def test_outlook_ocupado_con_otra_consulta(self):
        """El candado COM cogido (una consulta colgada): no se amontona otro hilo, se dice que no contesta."""
        self.assertTrue(calendario._COM_LOCK.acquire(timeout=1))
        try:
            datos, fallo = calendario.agenda(now=NOW, session=lambda: FakeApp([], []), fmt=DIA, espera_otra=0.05)
        finally:
            calendario._COM_LOCK.release()
        self.assertEqual((datos, fallo[0]), (None, "ocupado"))


class AgendaB2Tests(Base):
    def test_b2_probar_solo_miraba_hoy_y_la_agenda_mira_el_mes(self):
        """La causa del B2: lo que había (agenda_hoy, lo que usa «Probar») solo lee hoy y solo da cifras y la próxima;
        la agenda de Hoy lee el mes y da cada cita."""
        items = [FakeItem("Hoy", NOW + timedelta(hours=1), NOW + timedelta(hours=2)),
                 FakeItem("Mañana", NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1)),
                 FakeItem("La semana que viene", NOW + timedelta(days=7), NOW + timedelta(days=7, hours=1))]
        hoy, fallo = calendario.agenda_hoy(now=NOW, session=lambda: FakeApp(list(items), []), fmt=DIA)
        self.assertIsNone(fallo)
        self.assertEqual((hoy["citas"], hoy["proxima"]["inicio"]), (1, "11:30"))
        self.assertNotIn("citas_lista", hoy)  # nada que listar: solo la cifra
        mes, _ = calendario.agenda(now=NOW, session=lambda: FakeApp(list(items), []), fmt=DIA)
        self.assertEqual([c["subject"] for c in mes["citas"]], ["Hoy", "Mañana", "La semana que viene"])


# ----------------------------------------------------------------------------- 2. la reunión de ahora

def publica(asunto, ini, minutos=60, **kw):
    return dict({"subject": asunto, "start": ini.isoformat(timespec="minutes"),
                 "end": (ini + timedelta(minutes=minutos)).isoformat(timespec="minutes"), "location": "",
                 "is_teams": False, "recurring": False, "categories": []}, **kw)


LISTAS = [{"id": "Alfa/Vocabulario/General.txt", "project": "Alfa", "name": "General", "terms": ["comité"]}]


class AhoraTests(unittest.TestCase):
    def test_la_ventana_de_ahora(self):
        citas = [publica("Ya acabó", NOW - timedelta(minutes=70)),
                 publica("Acabó hace 3 min", NOW - timedelta(minutes=63)),
                 publica("En 8 min", NOW + timedelta(minutes=8)), publica("En 20 min", NOW + timedelta(minutes=20))]
        self.assertEqual(calendario.cita_ahora(citas, NOW)["subject"], "En 8 min")  # la más cercana entre las dos
        self.assertEqual(calendario.cita_ahora(citas[:1] + citas[3:], NOW), None)
        en_curso = publica("En curso", NOW - timedelta(minutes=30))
        self.assertEqual(calendario.cita_ahora(citas + [en_curso], NOW)["subject"], "En curso")
        self.assertTrue(calendario.cita_ahora([en_curso], NOW)["is_now"])
        # el título de Teams manda sobre la que está en curso
        self.assertEqual(calendario.cita_ahora(citas + [en_curso], NOW, "En 8 min | Microsoft Teams")["subject"],
                         "En 8 min")
        self.assertIsNone(calendario.cita_ahora([{"subject": "rota", "start": "x", "end": None}], NOW))

    def test_sin_teams_la_cita_de_la_agenda_con_su_proyecto(self):
        pedidas = []

        def listas():
            pedidas.append(1)
            return LISTAS
        sug, info = calendario.reunion_de_ahora({}, [publica("Comité Alfa", NOW - timedelta(minutes=30),
                                                             recurring=True)], listas, NOW)
        self.assertEqual((sug["name"], sug["source"], sug["vocab"], sug["participants"]),
                         ("Comité Alfa", "outlook", "Alfa/Vocabulario/General.txt", []))
        self.assertEqual(info, {"titulo": "Comité Alfa", "fuente": "outlook", "teams": False,
                                "inicio": "2026-11-09T10:00", "fin": "2026-11-09T11:00", "en_curso": True,
                                "periodica": True, "asunto": "Comité Alfa"})
        self.assertEqual(pedidas, [1])

    def test_teams_manda_y_las_horas_solo_si_es_la_misma(self):
        lect = {"teams": {"title": "Comité Alfa"}, "sugerencia": {"name": "Comité Alfa", "source": "teams"}}
        citas = [publica("Comité Alfa", NOW - timedelta(minutes=30))]
        sug, info = calendario.reunion_de_ahora(lect, citas, lambda: self.fail("no hacen falta las listas"), NOW)
        self.assertEqual((sug["source"], info["teams"], info["inicio"], info["en_curso"]),
                         ("teams", True, "2026-11-09T10:00", True))
        _, info = calendario.reunion_de_ahora(lect, [publica("Otra cosa", NOW - timedelta(minutes=30))], [], NOW)
        self.assertEqual((info["titulo"], info["inicio"], info["fin"]), ("Comité Alfa", None, None))

    def test_la_cita_que_vio_el_vigilante_en_la_llamada(self):
        lect = {"teams": None, "outlook": publica("Comité", NOW - timedelta(minutes=5)),
                "sugerencia": {"name": "Comité", "source": "outlook", "participants": ["Ana"]}}
        sug, info = calendario.reunion_de_ahora(lect, [], [], NOW)
        self.assertEqual((sug["participants"], info["inicio"]), (["Ana"], "2026-11-09T10:25"))

    def test_a_punto_de_empezar_y_nada(self):
        _, info = calendario.reunion_de_ahora({}, [publica("Luego", NOW + timedelta(minutes=8))], [], NOW)
        self.assertFalse(info["en_curso"])
        self.assertEqual(calendario.reunion_de_ahora({}, [], [], NOW), ({}, None))
        self.assertEqual(calendario.reunion_de_ahora(None, [publica("  ", NOW)], [], NOW), ({}, None))

    def test_sin_leer(self):
        f = calendario.sin_leer
        base = {"windows": True, "pywin32": True, "instalado": True, "abierto": True}
        self.assertIsNone(f(base))
        self.assertEqual(f({"windows": False})[0], "no_windows")
        self.assertEqual(f(dict(base, pywin32=False))[0], "sin_pywin32")
        self.assertEqual(f(dict(base, instalado=False))[0], "sin_outlook")
        self.assertEqual(f(dict(base, instalado=False, nuevo_activado=True))[0], "outlook_nuevo")
        self.assertEqual(f(dict(base, abierto=False))[0], "cerrado")
        self.assertEqual(f(dict(base, abierto=False, nuevo_abierto=True))[0], "outlook_nuevo")
        self.assertIsNone(f(dict(base, abierto=None)))  # no se sabe: se prueba


# ----------------------------------------------------------------------------- 3. la caché en segundo plano

class Reloj:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        p = mock.patch.object(calendario, "log", self.logs.append)
        p.start()
        self.addCleanup(p.stop)

    def test_lectura_buena_luego_fallo_y_lo_que_se_dice(self):
        respuestas = [({"citas": [publica("A", NOW)], "formato": "regional", "cortada": False}, None),
                      (None, ("cerrado", calendario.FRASES["cerrado"]))]
        reloj = Reloj()
        ag = calendario.Agenda(leer=lambda now=None, **kw: respuestas.pop(0), reloj=reloj)
        self.assertEqual(ag.publica(True)["estado"], "leyendo")
        self.assertTrue(ag.toca())
        self.assertTrue(ag.refrescar())
        r = ag.publica(True)
        self.assertEqual((r["estado"], [c["subject"] for c in r["citas"]], r["dias"]), ("ok", ["A"], 31))
        self.assertFalse(ag.toca())
        reloj.t += calendario.AGENDA_CADA
        self.assertTrue(ag.toca())
        self.assertFalse(ag.refrescar())
        r = ag.publica(True)  # la de antes se queda, con su hora y por qué no se ha renovado
        self.assertEqual((r["estado"], len(r["citas"]), r["accion"]["id"]), ("cerrado", 1, "leer"))
        self.assertRegex(r["frase"], r"^Outlook clásico está cerrado: .* Lo de abajo es de las \d+:\d\d\.$")
        self.assertEqual(ag.citas(), r["citas"])
        r["citas"][0]["preparada"] = {"idioma": "pt"}  # /api/hoy anota las suyas: la caché no cambia
        ag.citas()[0]["otra"] = 1
        self.assertEqual(set(ag.publica(True)["citas"][0]), {"subject", "start", "end", "location", "is_teams",
                                                             "recurring", "categories"})
        # server.log: solo cifras y estados, y cada cosa una vez
        self.assertEqual([re.sub(r"\(\d+ ms\)", "(N ms)", x) for x in self.logs],
                         ["   calendario: agenda · 1 citas (N ms)", "   calendario: agenda · cerrado (N ms)"])
        for linea in self.logs:
            self.assertTrue(any(rx.fullmatch(linea) for rx in server._DIAG_RX), linea)
            self.assertNotIn("<texto>", server.diag_line(linea))

    def test_apagado_y_sin_outlook(self):
        ag = calendario.Agenda(leer=lambda **kw: self.fail("apagado no se lee"))
        r = ag.publica(False)
        self.assertEqual((r["estado"], r["accion"]["id"], r["citas"]), ("apagado", "ajustes", []))
        self.assertIn("próximo mes", r["frase"])
        r = ag.publica(False, ("sin_outlook", calendario.FRASES["sin_outlook"]))
        self.assertEqual((r["estado"], r["accion"]), ("sin_outlook", None))
        r = ag.publica(False, ("apagado", "lo de Ajustes"))
        self.assertEqual(r["frase"], calendario.FRASES_AGENDA["apagado"])

    def test_anotar_sin_leer_y_olvidar(self):
        ag = calendario.Agenda(leer=lambda **kw: ({"citas": [publica("A", NOW)], "cortada": True}, None))
        ag.anotar(("sin_pywin32", calendario.FRASES["sin_pywin32"]))
        r = ag.publica(True)
        self.assertEqual((r["estado"], r["accion"]), ("sin_pywin32", None))  # reinstalar no es «volver a leer»
        ag.anotar(("sin_pywin32", calendario.FRASES["sin_pywin32"]))
        self.assertEqual(len(self.logs), 1)  # lo mismo otra vez: no se repite en server.log
        ag.refrescar()
        self.assertTrue(ag.publica(True)["cortada"])
        self.assertIn("(cortada)", self.logs[-1])
        ag.olvidar()
        self.assertEqual((ag.citas(), ag.publica(True)["estado"]), ([], "leyendo"))

    def test_una_lectura_a_la_vez_y_un_fallo_raro_no_mata_el_hilo(self):
        ag = calendario.Agenda(leer=lambda **kw: 1 / 0)
        with mock.patch.object(calendario, "log_exc"):
            self.assertFalse(ag.refrescar())
        self.assertEqual(ag.publica(True)["estado"], "error")
        ag.leyendo = True
        self.assertFalse(ag.refrescar())
        self.assertFalse(ag.toca(0))

    def test_el_outlook_falso_de_las_pruebas_de_la_interfaz(self):
        datos, fallo = calendario.agenda(session=calendario.sesion_de_prueba(
            os.path.join(REPO, "tests", "fake_outlook", "agenda.json")))
        self.assertIsNone(fallo)
        asuntos = [c["subject"] for c in datos["citas"]]
        self.assertIn("Comité de seguimiento", asuntos)
        self.assertFalse([a for a in asuntos if "no sale" in a])


# ----------------------------------------------------------------------------- 4. tareas abiertas

class ConPreparadaTests(unittest.TestCase):
    def test_lo_de_la_tarjeta_manda(self):
        prep = {"vocab": "v1", "idioma": "pt", "contexto": "c"}
        self.assertEqual(plan.con_preparada({}, prep), {"vocab": "v1", "cambios": {"idioma": "pt", "contexto": "c"},
                                                        "preparada": ["vocab", "idioma", "contexto"]})
        p = plan.con_preparada({"vocab": "v2", "cambios": {"idioma": "en"}}, prep)
        self.assertEqual(p, {"vocab": "v2", "cambios": {"idioma": "en", "contexto": "c"}, "preparada": ["contexto"]})
        self.assertEqual(plan.con_preparada({"cambios": {}}, {}), {"cambios": {}})
        pedido = {"cambios": {}}
        plan.con_preparada(pedido, prep)
        self.assertEqual(pedido, {"cambios": {}})  # no toca el pedido que recibe


class TareasTests(unittest.TestCase):
    def test_tareas_abiertas(self):
        t = ("## Próximos pasos\n- [ ] Ana: presupuesto [12:00]\n- [x] Luis: hecho\n* [ ] Yo: acta\n1. [ ] Otra\n"
             "  - [ ] sangrada\n- [ ]\n- [] mal escrita\nTexto - [ ] en medio\n")
        self.assertEqual(plan.tareas_abiertas(t), 4)
        self.assertEqual(plan.tareas_abiertas("## Próximos pasos\nNinguno\n"), 0)
        self.assertEqual(plan.tareas_abiertas(None), 0)


# ----------------------------------------------------------------------------- 5. el servidor

class ServidorTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("Alfa/Vocabulario/General.txt", "comité, API\n")
        self.lect = {}

    def app(self, **st):
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, **st)
        a.rec_lock, a.notes_lock, a.toggle_lock, a.rec, a.monitor = (threading.Lock(), threading.Lock(),
                                                                     threading.Lock(), None, None)
        a.library = types.SimpleNamespace(at=1, flat=[])
        a.jobs = bare_jobs([])
        a.jobs.settings = a.settings
        a._vocab_terms = lambda: [{"id": "Alfa/Vocabulario/General.txt", "project": "Alfa", "name": "General",
                                   "terms": ["comité", "API"]}]
        a.vigia = calendario.Vigia()
        a.vigia.lectura = lambda leer, max_age=90, recordar=True: dict(self.lect)
        a.agenda = calendario.Agenda(leer=lambda **kw: ({"citas": self.citas, "cortada": False}, None))
        a.agenda_sesion, a.meeting = None, None
        now = datetime.now().replace(second=0, microsecond=0)
        self.citas = [publica("Comité de seguimiento", now - timedelta(minutes=20)),
                      publica("Mañana", now + timedelta(days=1))]
        a.agenda.refrescar()
        return a

    def test_api_hoy_y_ctrl_alt_r_con_la_misma_reunion(self):
        a = self.app(outlook_on=True)
        a.library.flat = [
            {"path": "Alfa/vieja.wav", "label": "Comité de seguimiento", "date": "2026-10-30T10:00:00",
             "props": {"vocabulario": "Alfa/Vocabulario/General.txt", "proyecto": "Alfa", "workstream": "General"},
             "_sum": self.write("Alfa/vieja.resumen.md", "- [ ] Ana: x\n- [ ] Luis: y\n- [x] z\n")},
            {"path": "Alfa/mas-vieja.wav", "label": "Comité de seguimiento", "date": "2026-10-23T10:00:00",
             "props": {}, "_sum": None}]
        r = a.hoy({})
        self.assertEqual((r["ahora"]["titulo"], r["ahora"]["fuente"], r["ahora"]["en_curso"]),
                         ("Comité de seguimiento", "outlook", True))
        self.assertEqual(r["plan"]["nombre"], {"valor": "Comité de seguimiento", "origen": "outlook",
                                               "texto": "de Outlook"})
        self.assertEqual(r["plan"]["proyecto"]["origen"], "historial")
        self.assertEqual(r["ultima"], {"path": "Alfa/vieja.wav", "fecha": "2026-10-30T10:00:00",
                                       "fecha_txt": "vie 30 oct", "resumen": True, "tareas": 2})
        self.assertEqual([c["subject"] for c in r["agenda"]["citas"]], ["Comité de seguimiento", "Mañana"])
        # la tarjeta (grabar_plan_api sin nada) y Ctrl+Alt+R usan la misma reunión de ahora
        self.assertEqual(a.grabar_plan_api({})["rec"], r["plan"]["rec"])
        vistos = []
        a.rec_start = lambda b: vistos.append(b) or {"path": "x.wav", "workstream": None}
        with mock.patch.object(server, "cue"), mock.patch.object(server, "notify"):
            a.rec_toggle({"source": "hotkey"})
        self.assertEqual({k: v for k, v in vistos[0].items() if k != "resumen"}, r["plan"]["rec"])

    def test_la_reunion_de_ahora_y_la_lista_salen_de_la_misma_foto(self):
        """Si el hilo renueva la agenda en mitad de /api/hoy, la reunión de ahora no puede salir de otra lectura que la
        lista (en ui-smoke de Ubuntu salía «agenda ok» con ahora = None)."""
        a = self.app(outlook_on=True)
        a.agenda.citas = lambda: []  # lo que habría dado una lectura de la caché a destiempo
        r = a.hoy({})
        self.assertEqual(r["ahora"]["titulo"], "Comité de seguimiento")

    def test_con_el_calendario_apagado_no_se_usa_la_agenda(self):
        a = self.app(outlook_on=False)
        with mock.patch.object(calendario, "estado", lambda on: {"estado": "apagado", "frase": "x"}):
            r = a.hoy({})
        self.assertIsNone(r["ahora"])
        self.assertEqual((r["agenda"]["estado"], r["agenda"]["citas"]), ("apagado", []))
        self.assertEqual(r["plan"]["nombre"]["valor"], "")

    def test_teams_manda_y_hoy_no_guarda_su_lectura(self):
        a = self.app(outlook_on=True)
        self.lect = {"teams": {"title": "Otra reunión"}, "sugerencia": {"name": "Otra reunión", "source": "teams"}}
        r = a.hoy({})
        self.assertEqual((r["ahora"]["titulo"], r["ahora"]["teams"], r["ahora"]["inicio"]),
                         ("Otra reunión", True, None))
        a2 = self.app(outlook_on=True)
        a2.vigia = calendario.Vigia()
        with mock.patch.object(calendario, "reunion_actual", lambda *x, **k: {"sugerencia": {}, "teams": None}):
            a2.hoy({})
            self.assertIsNone(a2.vigia.ultimo)  # Hoy pregunta cada 30 s: no tapa la lectura con Outlook del vigilante
            a2.grabar_plan_api({})
            self.assertIsNotNone(a2.vigia.ultimo)  # la tarjeta sí, como antes

    def test_un_vigilante_que_solo_da_la_sugerencia(self):
        """Los dobles de otros tests (y de otras ramas) solo tienen `sugerencia`: siguen valiendo."""
        a = self.app(outlook_on=True)
        a.vigia = types.SimpleNamespace(sugerencia=lambda leer: {"name": "De Teams", "source": "teams"})
        self.assertEqual(a._reunion_ahora()[0]["name"], "De Teams")

    def test_la_ultima_vez_sin_la_que_se_graba(self):
        a = self.app(outlook_on=True)
        a.library.flat = [{"path": "Alfa/hoy.wav", "label": "Comité de seguimiento", "date": "2026-11-09T10:00:00",
                           "props": {}, "_sum": None, "recording": True},
                          {"path": "Alfa/x.wav", "label": "", "date": "2026-11-02T10:00:00",
                           "props": {"titulo": "comité  de SEGUIMIENTO"}, "_sum": None}]
        self.assertEqual(a._ultima_vez("Comité de seguimiento"),
                         {"path": "Alfa/x.wav", "fecha": "2026-11-02T10:00:00", "fecha_txt": "lun 2 nov",
                          "resumen": False, "tareas": None})
        a.library.flat[1]["_sum"] = os.path.join(self.tmp, "no-existe.resumen.md")
        self.assertIsNone(a._ultima_vez("Comité de seguimiento")["tareas"])  # el resumen se ha ido: sin cifra
        self.assertIsNone(a._ultima_vez(""))
        self.assertIsNone(a._ultima_vez("Otra"))

    def test_el_hilo_de_la_agenda(self):
        a = self.app(outlook_on=True)
        llamadas = []
        a.agenda = calendario.Agenda(leer=lambda **kw: llamadas.append(kw) or ({"citas": []}, None))
        env = {"windows": True, "pywin32": True, "instalado": True, "abierto": True}
        with mock.patch.object(calendario, "log"):
            a._agenda_paso(entorno=env)
            self.assertEqual(len(llamadas), 1)
            a._agenda_paso(entorno=env)  # no toca aún
            self.assertEqual(len(llamadas), 1)
            a._agenda_paso(forzado=True, entorno=env)  # Hoy la ha pedido
            self.assertEqual(len(llamadas), 2)
            a.meeting = "Teams"  # en una llamada, con agenda ya leída: no se relee
            a._agenda_paso(forzado=True, entorno=env)
            self.assertEqual(len(llamadas), 2)
            a.meeting = None
            a._agenda_paso(forzado=True, entorno=dict(env, abierto=False))  # cerrado: sin hablar con Outlook
            self.assertEqual((len(llamadas), a.agenda.publica(True)["estado"]), (2, "cerrado"))
            a.agenda_sesion = "sesion-de-prueba"
            a._agenda_paso(forzado=True)
            self.assertEqual(llamadas[-1]["session"], "sesion-de-prueba")
            a.settings.update(lambda d: d.update(outlook_on=False))  # apagado: se olvida lo leído
            a._agenda_paso(forzado=True)
            self.assertEqual(a.agenda.citas(), [])
            self.assertEqual(a.agenda.publica(True)["estado"], "leyendo")

    def test_hoy_pide_la_agenda_al_abrirla_si_es_vieja_y_el_sondeo_no(self):
        """Revisión 1: con el sondeo de 30 s pidiendo cada vez que tenía más de 2 min, Outlook se releía cada ~2 min con
        Hoy a la vista. Ahora solo al abrir Hoy (abrir=1) si es vieja, o con «Actualizar» (refrescar=1)."""
        a = self.app(outlook_on=True)
        a.agenda.despertar.clear()
        a.hoy({"abrir": "1"})
        self.assertFalse(a.agenda.despertar.is_set())  # recién leída
        a.hoy({"refrescar": "1"})
        self.assertTrue(a.agenda.despertar.is_set())
        a.agenda.despertar.clear()
        a.agenda.intento -= calendario.AGENDA_VIEJA
        a.hoy({})
        self.assertFalse(a.agenda.despertar.is_set())  # el sondeo no relee
        a.hoy({"abrir": "1"})
        self.assertTrue(a.agenda.despertar.is_set())
        self.assertTrue(a.hoy({})["agenda"]["comprobada"])

    def test_una_agenda_vieja_se_ensena_pero_no_pone_nombre(self):
        """Revisión 1: con Outlook cerrado horas, una cita movida o cancelada seguiría nombrando las grabaciones."""
        a = self.app(outlook_on=True)
        self.assertEqual(a.hoy({})["ahora"]["titulo"], "Comité de seguimiento")
        a.agenda.leida -= calendario.AGENDA_FRESCA + 1
        r = a.hoy({})
        self.assertIsNone(r["ahora"])
        self.assertEqual(len(r["agenda"]["citas"]), 2)
        self.assertEqual(a._reunion_ahora()[0], {})

    def test_preparada_con_signos_en_el_asunto(self):
        """Revisión 1 (P1): «Comité: Q4» de Outlook es «Comité Q4» en la reunión de ahora (nombre_archivo)."""
        a = self.app(outlook_on=True)
        ini = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=20)
        largo = "RE: Alfa | Sync / Q4 «presupuesto» " + "x" * 90
        for asunto in ("Comité: Q4", largo):
            self.citas[:] = [publica(asunto, ini)]
            a.agenda.refrescar()
            a.hoy_preparar({"asunto": asunto, "inicio": ini.isoformat(timespec="minutes"),
                            "fin": (ini + timedelta(minutes=60)).isoformat(timespec="minutes"), "idioma": "pt"})
            r = a.hoy({})
            self.assertEqual(r["agenda"]["citas"][0]["preparada"], {"idioma": "pt"}, asunto)
            self.assertEqual(r["ahora"]["asunto"], asunto)  # la agenda marca «Ahora» por el asunto de Outlook
            idioma = {x["clave"]: x for x in r["plan"]["lineas"]}["idioma"]
            self.assertEqual((idioma["code"], idioma["origen"]), ("pt", "preparada"), asunto)

    def test_preparadas_raras_en_settings_no_tumban_hoy(self):
        a = self.app(outlook_on=True)
        base = {"asunto": "X", "inicio": "2030-01-01T10:00", "fin": "2030-01-01T11:00", "idioma": "pt"}
        for raro in ("texto", ["lista"], {"k": "no es un dict"}, None):
            a.settings.update(lambda d: d.update(preparadas=raro))
            self.assertIn("agenda", a.hoy({}))
            a.hoy_preparar(base)
            self.assertEqual(list(a.settings.get()["preparadas"].values()), [base])

    def test_el_hilo_no_relee_grabando_ni_en_llamada(self):
        """Grabando, o en una llamada aunque «Avisarme si estoy en una llamada» esté apagado (meeting None, pero el
        registro de privacidad dice que hay llamada: meeting_prev), con agenda ya leída no se relee."""
        a = self.app(outlook_on=True)
        llamadas = []
        a.agenda.leer = lambda **kw: llamadas.append(1) or ({"citas": []}, None)
        env = {"windows": True, "pywin32": True, "instalado": True, "abierto": True}
        with mock.patch.object(calendario, "log"):
            a.rec = types.SimpleNamespace(alive=lambda: True)
            a._agenda_paso(forzado=True, entorno=env)
            a.rec, a.meeting_prev = None, "Teams"
            a._agenda_paso(forzado=True, entorno=env)
            self.assertEqual(llamadas, [])
            a.meeting_prev = None
            a._agenda_paso(forzado=True, entorno=env)
            self.assertEqual(llamadas, [1])

    def test_activar_prueba_antes_de_pedir_la_agenda(self):
        """Revisión 1: «Activar» lanza «Probar» con el usuario delante; si la agenda se pidiera antes, competirían por
        el candado de Outlook y «Probar» podría decir «ocupado»."""
        a = self.app(outlook_on=False)
        orden = []
        a.agenda.pedir = lambda: orden.append("agenda")
        with mock.patch.object(calendario, "estado", lambda on, probar=False: orden.append("probar") or {}), \
                mock.patch.object(server, "log"):
            a.calendario_activar({"activar": True})
        self.assertEqual(orden, ["probar", "agenda"])

    def test_preparar_una_cita_y_se_usa_al_grabarla(self):
        a = self.app(outlook_on=True)
        ini = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=20)  # la de ahora (self.citas)
        cita = {"asunto": "Comité de seguimiento", "inicio": ini.isoformat(timespec="minutes"),
                "fin": (ini + timedelta(minutes=60)).isoformat(timespec="minutes")}
        r = a.hoy_preparar(dict(cita, vocab="Alfa/Vocabulario/General.txt", idioma="pt",
                                contexto="  Presupuesto   Q4 "))
        self.assertEqual(r["preparada"], dict(cita, vocab="Alfa/Vocabulario/General.txt", idioma="pt",
                                              contexto="Presupuesto Q4"))
        h = a.hoy({})
        self.assertEqual(h["agenda"]["citas"][0]["preparada"], {"vocab": "Alfa/Vocabulario/General.txt",
                                                                "idioma": "pt", "contexto": "Presupuesto Q4"})
        self.assertNotIn("preparada", h["agenda"]["citas"][1])
        lineas = {x["clave"]: x for x in h["plan"]["lineas"]}
        self.assertEqual((h["plan"]["proyecto"]["origen"], h["plan"]["proyecto"]["motivo"]),
                         ("preparada", "preparado en Hoy"))
        self.assertEqual((lineas["idioma"]["code"], lineas["idioma"]["origen"], lineas["idioma"]["origen_txt"]),
                         ("pt", "preparada", "preparado en Hoy"))
        self.assertEqual((lineas["contexto"]["valor"], lineas["contexto"]["origen"]), ("Presupuesto Q4", "preparada"))
        # lo cambiado en los chips de Hoy o en la tarjeta manda sobre lo preparado
        otro = {x["clave"]: x for x in a.grabar_plan_api({"cambios": {"idioma": "en"}})["lineas"]}
        self.assertEqual((otro["idioma"]["code"], otro["idioma"]["origen"]), ("en", "reunion"))
        # Ctrl+Alt+R graba con lo preparado
        vistos = []
        a.rec_start = lambda b: vistos.append(b) or {"path": "x.wav", "workstream": None}
        with mock.patch.object(server, "cue"), mock.patch.object(server, "notify"):
            a.rec_toggle({"source": "hotkey"})
        self.assertEqual((vistos[0]["language"], vistos[0]["vocab"], vistos[0]["contexto"]),
                         ("pt", "Alfa/Vocabulario/General.txt", "Presupuesto Q4"))
        # de 10 min antes a 5 min después, y solo esa reunión
        self.assertIsNotNone(a._preparada("comité  de SEGUIMIENTO", now=ini - timedelta(minutes=10)))
        self.assertIsNone(a._preparada("Comité de seguimiento", now=ini - timedelta(minutes=11)))
        self.assertIsNone(a._preparada("Comité de seguimiento", now=ini + timedelta(minutes=66)))
        self.assertIsNone(a._preparada("Otra", now=ini))
        # quitar (o guardar sin nada que usar) la deja como siempre
        a.hoy_preparar(dict(cita, quitar=True, vocab="x"))
        self.assertEqual(a.settings.get()["preparadas"], {})
        a.hoy_preparar(dict(cita, contexto="   "))
        self.assertEqual(a.settings.get()["preparadas"], {})
        self.assertNotEqual(a.hoy({})["plan"]["proyecto"]["origen"], "preparada")

    def test_preparar_valida_y_olvida_lo_viejo(self):
        a = self.app(outlook_on=True)
        base = {"asunto": "X", "inicio": "2026-11-10T10:00", "fin": "2026-11-10T11:00"}
        for malo, texto in ((dict(base, asunto=" "), "asunto"), (dict(base, inicio="ayer"), "hora"),
                            (dict(base, idioma="portugués"), "Idioma"), (dict(base, vocab="Zeta/Vocabulario/No.txt"),
                                                                         "proyecto")):
            with self.assertRaises(server.ApiError) as cm:
                a.hoy_preparar(malo)
            self.assertIn(texto, cm.exception.msg)
        vieja = {"asunto": "V", "inicio": "2020-01-01T10:00", "fin": "2020-01-01T11:00", "idioma": "es"}
        a.settings.update(lambda d: d.update(preparadas={"vieja": vieja}))
        hoy = datetime.now() + timedelta(days=1)
        a.hoy_preparar(dict(base, inicio=hoy.isoformat(timespec="minutes"),
                            fin=(hoy + timedelta(hours=1)).isoformat(timespec="minutes"), idioma="auto", vocab=""))
        prep = a.settings.get()["preparadas"]
        self.assertEqual(len(prep), 1)
        self.assertEqual({k: v for k, v in list(prep.values())[0].items() if k in ("idioma", "vocab")},
                         {"idioma": "", "vocab": ""})

    def test_ruta_y_voces_sin_nombre(self):
        with open(server.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"/api/hoy": a.hoy', src)
        self.assertIn('threading.Thread(target=self._agenda_watch, daemon=True, name="agenda-outlook").start()', src)
        lib = server.Library.__new__(server.Library)
        lib.speakers = {}
        p = self.write("x.hablantes.json", json.dumps({"tramos": [[0, 1, "A"]], "tu": "C", "hablantes": {
            "A": {}, "B": {"breve": True}, "C": {}, "D": {"persona": "p1"},
            "E": {"persona": "p2", "provisional": True}}}))
        info = lib._speakers({"_spk": p, "spk_mtime": 1})
        self.assertEqual((info["n"], info["known"], info["doubt"], info["sin_nombre"]), (4, 1, 1, 1))


# ----------------------------------------------------------------------------- 6. hoy.js (node)

STATIC = os.path.join(REPO, "_app", "static")
JS_STUBS = r"""
const S = {jobs: [], vocab: [{id: 'v1', project: 'Alfa', name: 'General'}], audios: new Map(), route: {kind: 'x'}};
const main = {addEventListener() {}}, document = {hidden: false}, addEventListener = () => {};
const setInterval = () => 0;
const $ = () => null, $$ = () => [], esc = s => String(s ?? ''), I = new Proxy({}, {get: () => ''});
const pad = n => String(n).padStart(2, '0'), nfc = s => s;
const MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const aDate = a => a.date ? new Date(a.date) : null, titleOf = a => (a.props && a.props.titulo) || '';
const iconOf = () => '', isLive = a => !!a.live, jobFor = p => S.jobs.find(j => j.path === p && j.activo);
const statusOf = a => a.st, estDuration = a => a.dur || 60, fmtClock = s => `${Math.floor(s / 60)}:${pad(s % 60)}`;
const fmtDur = s => `${Math.round(s / 60)} min`;
"""


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class HoyJsTests(unittest.TestCase):
    def node(self, code):
        with open(os.path.join(STATIC, "hoy.js"), encoding="utf-8") as f:
            src = f.read()
        r = subprocess.run(["node", "-e", JS_STUBS + src + "\n" + code], capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_dias(self):
        out = self.node("""const now = new Date(2026, 10, 9, 10, 30);  // lunes 9 nov
          const dia = d => hoyDiaTxt(new Date(2026, 10, 9 + d, 23, 59), now, d > 1);
          console.log(JSON.stringify([0, -1, 1, -2, 3, 9].map(dia)
            .concat([hoyFechaLarga(now), hoyFaltan(8), hoyFaltan(75), hoyFaltan(120), hoyFaltan(0.5)])));""")
        self.assertEqual(out, ["Hoy", "Ayer", "Mañana", "Sáb 7", "Jue 12 nov", "Mié 18 nov", "lunes 9 de noviembre",
                               "en 8 min", "en 1 h 15 min", "en 2 h", "ya"])

    def test_para_revisar(self):
        out = self.node("""const now = new Date(2026, 10, 9, 10, 30);
          S.jobs = [{path: 'c', activo: true}, {path: 'f', kind: 'summary', status: 'error'}];
          const A = [
            {path: 'a', date: '2026-11-09T09:00:00', st: 'pending', props: {workstream: 'General'}, dur: 120},
            {path: 'b', date: '2026-11-08T09:00:00', st: 'done', has_summary: true, props: {workstream: 'G'},
             speakers: {sin_nombre: 2}},
            {path: 'c', date: '2026-11-09T08:00:00', st: 'pending', props: {}},
            {path: 'd', date: '2026-11-07T09:00:00', st: 'done', props: {}},
            {path: 'e', date: '2026-11-06T09:00:00', st: 'error', props: {}},
            {path: 'f', date: '2026-11-05T09:00:00', st: 'done', props: {workstream: 'G'}},
            {path: 'g', date: '2026-11-04T09:00:00', st: 'done', props: {workstream: 'G'}, has_summary: true},
            {path: 'h', date: '2026-10-01T09:00:00', st: 'pending', props: {}},
            {path: 'i', date: '2026-11-09T07:00:00', st: 'partial', tx_end: 125, props: {workstream: 'G'}},
            {path: 'j', date: '2026-11-09T06:00:00', st: 'pending', props: {}, live: true}];
          const fila = r => [r.a.path, r.accion.k, r.motivos.map(m => m.texto).join(' · ')];
          console.log(JSON.stringify(hoyRevisar(A, now).map(fila)));""")
        self.assertEqual(out, [
            ["a", "transcribir", "Grabada · sin transcribir (2 min)"],
            ["i", "continuar", "La transcripción se cortó en 2:05"],
            ["b", "voces", "Resumen listo · 2 voces sin nombre"],
            ["d", "proyecto", "¿De qué proyecto es?"],
            ["e", "transcribir", "No se pudo transcribir · ¿De qué proyecto es?"],
            ["f", "resumir", "No se pudo resumir"],
        ])  # c (en la cola), g (nada que hacer), h (hace más de 30 días) y j (en directo) no salen

    def test_esta_semana_y_agenda(self):
        out = self.node("""const now = new Date(2026, 10, 11, 10, 30);  // miércoles
          const A = [{path: 'lun', date: '2026-11-09T09:00:00'}, {path: 'mie', date: '2026-11-11T09:00:00'},
            {path: 'mie2', date: '2026-11-11T08:00:00'}, {path: 'dom', date: '2026-11-08T09:00:00'},
            {path: 'fuera', date: '2026-11-10T09:00:00'}];
          const sem = hoySemana(A, now, new Set(['fuera'])).map(d => [d.dia, d.audios.map(a => a.path)]);
          const citas = [{subject: 'Ya pasó', start: '2026-11-11T08:00', end: '2026-11-11T09:00'},
            {subject: 'Ahora', start: '2026-11-11T10:00', end: '2026-11-11T11:00'},
            {subject: 'Mañana', start: '2026-11-12T09:00', end: '2026-11-12T10:00'},
            {subject: 'Rota', start: 'x', end: 'y'},
            {subject: 'Luego', start: '2026-11-20T09:00', end: '2026-11-20T10:00'}];
          const ag = hoyAgendaDias(citas, now, {inicio: '2026-11-11T10:00', titulo: 'Ahora'})
            .map(d => [d.dia, d.citas.map(c => c.subject + (c.ahora ? '*' : ''))]);
          console.log(JSON.stringify([sem, ag]));""")
        self.assertEqual(out, [[["Hoy", ["mie", "mie2"]], ["Lun 9", ["lun"]]],
                               [["Hoy", ["Ahora*"]], ["Mañana", ["Mañana"]], ["Vie 20 nov", ["Luego"]]]])

    def test_la_carga_y_los_ganchos(self):
        html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
        app = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
        self.assertLess(html.index('<script src="/app.js">'), html.index('<script src="/hoy.js">'))
        self.assertIn('<link rel="stylesheet" href="/hoy.css">', html)
        self.assertIn("if (typeof viewHoy === 'function') return viewHoy();", app)
        self.assertIn("</svg>Hoy</a>", html)


if __name__ == "__main__":
    unittest.main()
