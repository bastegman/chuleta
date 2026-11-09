# -*- coding: utf-8 -*-
"""msg 1242 (08-nov-2026): «a una persona la cataloga como 10 personas distintas a lo largo de la reunion».

sherpa-onnx parte a cada persona en varios grupos; voices.consolidate los une al terminar (huellas parecidas, breves
al mas parecido, numero de participantes como pista). Medido en reuniones reales con scripts/eval_voces.py; aqui,
sin sherpa-onnx, la logica con huellas sinteticas."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.request
from unittest import mock

import numpy as np

from support import APP, REPO, server
from test_fix_voces import Reunion, mix, node, unit
from test_frontend import js_defs, read

import voices  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "scripts"))
import eval_voces  # noqa: E402


def diar(grupos):
    """Salida de diarize a partir de {h: (tramos [(a, b)], huella|None)}."""
    tramos, hablantes, huellas = [], {}, {}
    for h, (tr, v) in grupos.items():
        tramos += [{"inicio": a, "fin": b, "h": h} for a, b in tr]
        secs = sum(b - a for a, b in tr)
        hablantes[h] = {"persona": None, "score": None, "segundos": secs, "provisional": False,
                        "breve": secs < voices.BREVE_S}
        if v is not None:
            huellas[h] = [float(x) for x in v]
    return {"tramos": sorted(tramos, key=lambda t: t["inicio"]), "hablantes": hablantes, "huellas": huellas,
            "duracion_s": 100.0}


P = {"fusion": 0.6, "absorbe_min": 0.3, "tope_min": 0.3}  # el mecanismo, con independencia de lo calibrado


def quien(res, t):
    """Hablante del tramo que contiene el instante t."""
    return next(x["h"] for x in res["tramos"] if x["inicio"] <= t < x["fin"])


class ConsolidateTests(unittest.TestCase):

    def test_une_los_grupos_de_la_misma_voz(self):
        res = voices.consolidate(diar({"h1": ([(0, 30)], unit(0)), "h2": ([(30, 50)], mix(unit(0), unit(1), 0.8)),
                                       "h3": ([(50, 75)], unit(2))}), **P)
        self.assertEqual(len(res["hablantes"]), 2)
        self.assertEqual(quien(res, 10), quien(res, 40))  # la misma persona en los dos grupos
        self.assertNotEqual(quien(res, 10), quien(res, 60))
        self.assertEqual(quien(res, 10), "h1")  # h1 = el que mas habla (50 s)
        self.assertEqual(res["hablantes"]["h1"]["segundos"], 50.0)
        self.assertEqual(res["duracion_s"], 100.0)

    def test_no_une_voces_distintas(self):
        res = voices.consolidate(diar({"h1": ([(0, 30)], unit(0)), "h2": ([(30, 50)], mix(unit(0), unit(1), 0.5)),
                                       "h3": ([(50, 75)], unit(2))}), **P)
        self.assertEqual(len(res["hablantes"]), 3)

    def test_una_persona_partida_en_muchos_grupos_vuelve_a_ser_una(self):
        """El caso del msg 1242: diez grupos de la misma voz (con ruido) y otra persona."""
        rng = np.random.default_rng(7)
        base = unit(0)
        grupos = {}
        for i in range(10):
            ruido = rng.normal(size=voices.DIM).astype(np.float32)
            ruido -= ruido.dot(base) * base
            ruido /= np.linalg.norm(ruido)
            grupos[f"h{i + 1}"] = ([(i * 20, i * 20 + 15)], mix(base, ruido, 0.9))
        grupos["h11"] = ([(200, 260)], unit(5))
        res = voices.consolidate(diar(grupos), **P)
        visibles = [h for h, i in res["hablantes"].items() if not i["breve"]]
        self.assertEqual(len(visibles), 2)
        self.assertEqual(len({quien(res, i * 20 + 1) for i in range(10)}), 1)

    def test_dos_grupos_grandes_necesitan_parecerse_mas(self):
        """Dos grupos con mucha voz tienen huellas fiables: a 0,72 son dos personas (medido: dos hombres distintos de
        57 y 70 s), aunque un grupo pequeño con ese parecido se una al grande."""
        g = {"h1": ([(0, 60)], unit(0)), "h2": ([(60, 100)], mix(unit(0), unit(1), 0.72))}
        self.assertEqual(len(voices.consolidate(diar(g), fusion=0.7, fusion_grandes=0.75, grande_s=30)["hablantes"]), 2)
        g["h2"] = ([(60, 80)], mix(unit(0), unit(1), 0.72))  # 20 s
        self.assertEqual(len(voices.consolidate(diar(g), fusion=0.7, fusion_grandes=0.75, grande_s=30)["hablantes"]), 1)
        g["h2"] = ([(60, 100)], mix(unit(0), unit(1), 0.72))  # tampoco con la pista de participantes
        res = voices.consolidate(diar(g), fusion=0.7, fusion_grandes=0.75, grande_s=30, participantes=1, tope_min=0.65)
        self.assertEqual(len(res["hablantes"]), 2)

    def test_un_breve_pasa_al_mas_parecido_sin_mover_su_huella(self):
        res = voices.consolidate(diar({"h1": ([(0, 30)], unit(0)), "h2": ([(30, 34)], mix(unit(0), unit(1), 0.5)),
                                       "h3": ([(40, 70)], unit(2))}), **P)
        self.assertEqual(len(res["hablantes"]), 2)
        self.assertEqual(quien(res, 32), quien(res, 10))
        self.assertAlmostEqual(voices.cosine(res["huellas"][quien(res, 10)], unit(0)), 1.0, places=5)

    def test_un_breve_absorbido_no_entra_en_la_huella_recalculada(self):
        """Con el extractor (lo que hace la app): la huella se recalcula solo con lo unido por fusion, no con los
        tramos de un breve absorbido, que puede ser de otra voz."""
        g = diar({"h1": ([(0, 20)], unit(0)), "h1b": ([(40, 60)], mix(unit(0), unit(1), 0.9)),
                  "h2": ([(30, 35)], mix(unit(0), unit(3), 0.5)), "h3": ([(70, 110)], unit(2))})
        with mock.patch.object(voices, "fingerprint", return_value=(unit(7), 40.0)) as fp:
            res = voices.consolidate(g, samples16=np.zeros(16000, np.int16), ex=object(), **P)
        fp.assert_called_once()
        self.assertEqual(fp.call_args[0][2], [(0, 20), (40, 60)])  # sin (30, 35) del breve
        self.assertEqual(quien(res, 32), quien(res, 10))  # pero su voz si pasa al visible
        g = diar({"h1": ([(0, 20)], unit(0)), "h2": ([(30, 35)], mix(unit(0), unit(1), 0.5))})
        with mock.patch.object(voices, "fingerprint", return_value=(unit(7), 40.0)) as fp:
            res = voices.consolidate(g, samples16=np.zeros(16000, np.int16), ex=object(), **P)
        fp.assert_not_called()  # solo absorbio: su huella es la de antes
        self.assertAlmostEqual(voices.cosine(res["huellas"]["h1"], unit(0)), 1.0, places=5)

    def test_un_breve_de_otra_voz_sigue_breve(self):
        res = voices.consolidate(diar({"h1": ([(0, 30)], unit(0)), "h2": ([(30, 34)], mix(unit(0), unit(1), 0.1)),
                                       "h3": ([(40, 70)], unit(2))}), **P)
        self.assertEqual(len(res["hablantes"]), 3)
        self.assertTrue(res["hablantes"][quien(res, 32)]["breve"])

    def test_participantes_como_pista_sin_forzar(self):
        """Cuatro voces visibles, dos de ellas algo parecidas (0,45: por debajo de FUSION). Con 2 participantes
        nombrados (+ HOLGURA = 3) se unen esas dos; nunca las que no se parecen (< TOPE_MIN)."""
        g = {"h1": ([(0, 30)], unit(0)), "h2": ([(30, 55)], unit(1)), "h3": ([(55, 75)], unit(2)),
             "h4": ([(75, 90)], mix(unit(2), unit(3), 0.45))}
        self.assertEqual(len(voices.consolidate(diar(g), **P)["hablantes"]), 4)  # sin pista: cuatro
        res = voices.consolidate(diar(g), participantes=2, **P)
        self.assertEqual(len(res["hablantes"]), 3)
        self.assertEqual(quien(res, 60), quien(res, 80))
        res = voices.consolidate(diar(g), participantes=1, **P)  # tope 2, pero el resto no se parece: no se fuerza
        self.assertEqual(len(res["hablantes"]), 3)

    def test_grupo_sin_huella_no_se_toca(self):
        res = voices.consolidate(diar({"h1": ([(0, 30)], unit(0)), "h2": ([(30, 30.5)], None)}), **P)
        self.assertEqual(len(res["hablantes"]), 2)
        self.assertNotIn(quien(res, 30.2), res["huellas"])

    def test_tramos_que_se_pisan_se_juntan(self):
        res = voices.consolidate(diar({"h1": ([(0, 30)], unit(0)), "h2": ([(20, 40)], mix(unit(0), unit(1), 0.9))}),
                                 **P)
        self.assertEqual([(t["inicio"], t["fin"]) for t in res["tramos"]], [(0, 40)])
        self.assertEqual(res["hablantes"]["h1"]["segundos"], 40.0)

    def test_idempotente(self):
        g = diar({"h1": ([(0, 30)], unit(0)), "h2": ([(30, 50)], mix(unit(0), unit(1), 0.8)),
                  "h3": ([(50, 53)], mix(unit(2), unit(1), 0.6)), "h4": ([(60, 80)], unit(2))})
        una = voices.consolidate(g, **P)
        self.assertEqual(voices.consolidate(una, **P)["tramos"], una["tramos"])

    def test_vacio(self):
        res = voices.consolidate({"tramos": [], "hablantes": {}, "huellas": {}, "duracion_s": 0})
        self.assertEqual((res["tramos"], res["hablantes"]), ([], {}))

    def test_con_extractor_recalcula_la_huella_de_los_unidos(self):
        g = diar({"h1": ([(0, 30)], unit(0)), "h2": ([(30, 50)], mix(unit(0), unit(1), 0.8)),
                  "h3": ([(50, 75)], unit(2))})
        with mock.patch.object(voices, "fingerprint", return_value=(unit(7), 50.0)) as fp:
            res = voices.consolidate(g, samples16=np.zeros(16000, np.int16), ex=object(), **P)
        fp.assert_called_once()  # solo el grupo que ha cambiado
        self.assertAlmostEqual(voices.cosine(res["huellas"]["h1"], unit(7)), 1.0, places=5)
        self.assertAlmostEqual(voices.cosine(res["huellas"]["h2"], unit(2)), 1.0, places=5)


class CountParticipantsTests(unittest.TestCase):

    def test_formas(self):
        for text, n in (("Ana, Luis y Pedro", 3), ("", 0), (None, 0), ("Ana; ana", 1), ("Ana and Bob & Carl", 3),
                        ("Ana/Luis", 2), ("Ana e Inés", 2), ("Maya Ybarra", 1), ("Ana,\nLuis", 2)):
            self.assertEqual(voices.count_participants(text), n, text)

    def test_sugeridos_por_outlook(self):
        """Lo que escribe la app con los de Outlook (join_participants): «Apellido, Nombre» es una persona."""
        for text, n in (("Stegmaier, Bernardo; García López, Ana", 2), ("Stegmaier, Bernardo;\nGarcía, Ana; Luis", 3),
                        ("Ana, Luis", 2), ("Ortega y Gasset, José; Ana", 2), ("", 0),
                        # una entrada que nombra a dos (una pareja, un buzon compartido): dos, nunca una (RF5)
                        ("Pérez, Ana y Luis; García, Pedro", 3), ("Ana y Luis Pérez, Pedro García", 3),
                        ("Equipo Ventas / Marketing, Ana López", 3),
                        # en mayusculas, sin nombre, con parentesis o con dos comas (RF6)
                        ("PÉREZ, ANA Y LUIS; GARCÍA, PEDRO", 3), ("Smith, John And Jane; Doe, Mary", 3),
                        ("Pérez,; García, Pedro", 2), ("Pérez, Luis (Ventas, Madrid); García, Ana", 2),
                        ("Smith, John, Jr.; Doe, Mary", 2),
                        # homonimos que solo distingue el parentesis, y dos nombres tras la coma (RF7)
                        ("García, Ana (Madrid); García, Ana (Barcelona)", 2), ("López, Ana; López, Ana (Madrid)", 2),
                        ("Ana López (Ventas), Ana López (RRHH)", 2), ("García, Ana, Luis; Pérez, Juan", 3),
                        # lo de dentro del parentesis no son nombres (departamento, ciudad)
                        ("Pérez, Luis (Ventas y Marketing); García, Ana", 2)):
            self.assertEqual(voices.count_participants(text, sugeridos=True), n, text)

    def test_editado_a_mano_nunca_de_menos(self):
        """Texto libre (lo que el usuario edita, mezclando con los de Outlook): nunca cuenta de menos."""
        for text, real in (("García, Ana; Luis Pérez, Pedro", 3), ("García, Ana; luis@cliente.com, Pedro", 3),
                           ("García, Ana; Pérez, Luis; Pedro, Marta", 4), ("Ana; Luis, Pedro", 3),
                           ("Ana, Luis; Pedro", 3), ("García, Ana; Pérez, Luis, Pedro", 3),
                           ("Ana; Luis, Pedro y Marta", 4), ("ANA Y LUIS", 2), ("Ana AND Luis", 2)):
            self.assertGreaterEqual(voices.count_participants(text), real, text)


class ParticipantesDeOutlookTests(unittest.TestCase):
    """fix-calendario x fix-sobresegmentacion (revision de la integracion del 08/11/2026): Outlook da los nombres del
    directorio de la empresa como «Apellido, Nombre»; unidos con «, » eran el doble de personas en la pista."""

    def test_de_la_cita_a_la_pista(self):
        import calendario
        sug = calendario.suggest({"subject": "Comité", "organizer": "Stegmaier, Bernardo",
                                  "attendees": ["García López, Ana", "Stegmaier, Bernardo"]}, [])
        texto = calendario.join_participants(sug["participants"])
        self.assertEqual(texto, "Stegmaier, Bernardo; García López, Ana")
        self.assertEqual(voices.count_participants(texto, sugeridos=True), 2)

    def test_sin_comas_como_siempre(self):
        import calendario
        self.assertEqual(calendario.join_participants(["Ana", "", "Luis"]), "Ana, Luis")
        self.assertEqual(calendario.join_participants(None), "")

    def test_nadie_une_los_participantes_con_comas(self):
        """Ningun camino del servidor une a mano los participantes de la sugerencia: todos con join_participants. Si una
        rama nueva trae su propio camino (p. ej. el plan de ux-grabar, plan.py), este test lo señala."""
        malos, buenos = [], 0
        for n in sorted(os.listdir(APP)):
            if n.endswith(".py"):
                with open(os.path.join(APP, n), encoding="utf-8") as f:
                    src = f.read()
                malos += [f"{n}: {m.group(0)}"
                          for m in re.finditer(r"""["'][,;] ?["']\.join\([^)\n]*participants""", src)]
                buenos += src.count("calendario.join_participants(")
        self.assertEqual(malos, [], "usar calendario.join_participants")
        self.assertGreaterEqual(buenos, 3)

    def test_la_interfaz_no_parte_ni_une_por_comas(self):
        """Lo mismo en la interfaz: ni unir los de la sugerencia con «, » ni partir «Participantes» por comas (con
        «Apellido, Nombre; …» serian el doble). Si ux-grabar trae grabar.js con su propio recuento, este test lo
        señala."""
        static, malos = os.path.join(APP, "static"), []
        for n in sorted(os.listdir(static)):
            if n.endswith(".js"):
                with open(os.path.join(static, n), encoding="utf-8") as f:
                    src = f.read()
                malos += [f"{n}: {m.group(0)}" for m in re.finditer(
                    r"participants\b[^;\n]*\.join\(\s*['\"], ?['\"]\s*\)"
                    r"|participantes\b[^;\n]*\.split\(\s*(?:['\"], ?['\"]|/[^/\n]*,[^/\n]*/)", src)]
        self.assertEqual(malos, [], "separar con «; » si algún nombre lleva coma, como calendario.join_participants")

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_js(self):
        """El dialogo Grabar (reunionPrefill) une igual."""
        m = re.search(r"^  d\.dataset\.rnParticipantes = (.+);$", read("reunion.js"), re.M)
        self.assertIsNotNone(m, "reunionPrefill ya no une los participantes: si el dialogo nuevo (ux-grabar) los une "
                                "en otro sitio, que use calendario.join_participants (plan.py) y probar alli lo mismo")
        out = node(f"""const f = s => {m.group(1)};
console.log(JSON.stringify([f({{participants: ['Stegmaier, Bernardo', 'García, Ana']}}),
  f({{participants: ['Ana', 'Luis']}}), f({{}})]));
""")
        self.assertEqual(json.loads(out[0]), ["Stegmaier, Bernardo; García, Ana", "Ana, Luis", ""])


class PistaDeParticipantesTests(Reunion):
    """_run_voices pasa al subproceso cuantas personas nombra «Participantes», solo cuando es fiable."""

    def correr(self, props=None):
        if props is not None:
            self.write("r.md", server.dump_notes(props, "notas"))
        jobs, seen = self.app.jobs, {}

        def fake_subprocess(job, args, idle_timeout=1800):
            seen["args"] = list(args)
            voices.write_sidecar(args[args.index("--out") + 1], {
                "version": 1, "tramos": [{"inicio": 0, "fin": 20, "h": "h1"}],
                "hablantes": {"h1": {"segundos": 20.0, "breve": False}}, "micro": None, "tu": None})
            return {"huellas": {"h1": list(map(float, unit(3)))}}
        jobs._voices_subprocess = fake_subprocess
        jobs.auto_summary = lambda rel: None
        job = server.Job("r.wav", "r.wav", "voces", None)
        with mock.patch.object(voices, "models_ready", return_value=True):
            jobs._run_voices(job)
        self.assertEqual(job.status, "done", job.error)
        a = seen["args"]
        return int(a[a.index("--participantes") + 1]) if "--participantes" in a else None

    def test_primera_vez_con_participantes(self):
        self.assertEqual(self.correr({"participantes": "Ana, Luis y Pedro"}), 3)

    def test_sin_participantes(self):
        self.assertIsNone(self.correr({"titulo": "x"}))
        self.assertIsNone(self.correr())

    def test_con_el_sidecar_que_deja_la_grabadora(self):
        """Al parar, la grabadora escribe un sidecar con solo la linea de tiempo del micro: sigue siendo la primera."""
        voices.write_sidecar(self.sidecar(), {"version": 1, "micro": {"paso": 0.25, "activo": "0101", "desfase": 0}})
        self.assertEqual(self.correr({"participantes": "Ana, Luis y Pedro"}), 3)

    def test_al_reidentificar_no_se_fia_de_lo_que_relleno_la_app(self):
        """Tras identificar, _suggest_participants pudo dejar solo los reconocidos: no vale como tope."""
        self.identificar(micro_en=None)
        self.assertIsNone(self.correr({"participantes": "Ana"}))

    def test_los_de_outlook_con_apellido_y_nombre(self):
        """El texto que escribio la app desde Outlook: «Apellido, Nombre» cuenta uno."""
        texto = "Stegmaier, Bernardo; García López, Ana"
        with mock.patch.object(server, "TEXTOS_SUGERIDOS", {texto}):
            self.assertEqual(self.correr({"participantes": texto, "participantes_origen": "outlook"}), 2)

    def test_con_origen_pero_editado_fuera(self):
        """Con la nota «sugeridos por…» pero un texto que la app no escribio (el .md editado fuera): texto libre."""
        for texto, real in (("García, Ana; Luis Pérez, Pedro", 3), ("Ana; Luis y Pedro", 3), ("Ana, Luis y Pedro", 3)):
            self.tearDown()
            self.setUp()  # y despues el set de la app: TempTree lo vacia al entrar (RF5)
            with mock.patch.object(server, "TEXTOS_SUGERIDOS", {"García, Ana; Luis Pérez", "Ana; Luis"}):
                self.assertGreaterEqual(self.correr({"participantes": texto, "participantes_origen": "outlook"}), real,
                                        texto)

    def test_editados_a_mano_del_lado_prudente(self):
        """Un texto que la app no escribio (editado a mano, o tras reiniciar) es texto libre: de mas, nunca de menos."""
        with mock.patch.object(server, "TEXTOS_SUGERIDOS", set()):
            self.assertEqual(self.correr({"participantes": "Stegmaier, Bernardo; García López, Ana"}), 4)

    def test_confirmados_sin_tocar(self):
        """«Confirmar» quita el origen pero no cambia el texto que escribio la app: se siguen contando bien."""
        texto = "Stegmaier, Bernardo; García López, Ana"
        with mock.patch.object(server, "TEXTOS_SUGERIDOS", {texto}):
            self.assertEqual(self.correr({"participantes": texto}), 2)

    def test_rec_start_apunta_solo_el_texto_de_la_app(self):
        """rec_start apunta el texto solo si es, entero, el que la app escribe con la ultima sugerencia del vigilante
        (dialogo Grabar, Ctrl+Alt+R): ni lo escrito a mano, ni lo que traiga otro programa (aunque herede el origen),
        ni una lista cortada a 500 caracteres (contada exacta daria de menos)."""
        from test_carpetas import FakeRec, make_app, rec
        os.makedirs(os.path.join(self.root, "Alfa"))
        a = make_app(self)
        nombres = ["Stegmaier, Bernardo", "García López, Ana"]
        texto = "Stegmaier, Bernardo; García López, Ana"
        largos = [f"Apellido{n:02d} Segundo, Nombre{n:02d}" for n in range(30)]
        a.vigia = types.SimpleNamespace(ultimo={"sugerencia": {"source": "outlook", "participants": nombres}},
                                        sugerencia=lambda fn: {"source": "outlook", "participants": nombres})
        a.toggle_lock, a.rec_folder_for = threading.Lock(), lambda v, st: "Alfa"
        with mock.patch.object(server, "TEXTOS_SUGERIDOS", set()) as vistos, \
                mock.patch.object(server, "cue", lambda *x: None), mock.patch.object(server, "notify", lambda *x: None):
            rec(a, self, participantes=texto, sugerido="outlook")  # el dialogo, con la sugerencia
            self.assertEqual(vistos, {texto})
            rec(a, self, participantes="Ana, Luis", name="Otra")  # sin origen: escrito a mano
            rec(a, self, participantes="Ana, Luis", name="Otra 2", sugerido="outlook")  # con origen, otro texto
            with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log"):
                a.rec = None  # otra app, sin nombre ni tema y con sus participantes: hereda el origen
                r = a.rec_start({"mic": "default", "sys": "all", "folder": "Alfa",
                                 "participantes": "Pérez, Luis, Pedro"})
                self.assertEqual(r["participantes"], "Pérez, Luis, Pedro")
                a.rec = None  # /api/rec/toggle con participantes propios (RF4)
                r = a.rec_toggle({"participantes": "Ana, Luis y Pedro"})
                self.assertTrue(r.get("recording"), r)
            a.vigia.ultimo["sugerencia"]["participants"] = largos  # una cita enorme: se corta a 500
            r = rec(a, self, participantes=server.calendario.join_participants(largos), name="Larga",
                    sugerido="outlook")
            self.assertEqual(len(r["participantes"]), 500)
        self.assertEqual(vistos, {texto})

    def test_el_dialogo_con_el_vigilante_de_verdad(self):
        """El dialogo leyo la sugerencia y, antes de pulsar Grabar, el vigilante la olvido (sin llamada) o la releyo
        sin Outlook: el texto se apunta igual (lecturas de la ultima hora, Vigia.participantes_recientes; RF5)."""
        from test_carpetas import make_app, rec
        os.makedirs(os.path.join(self.root, "Alfa"))
        a, nombres = make_app(self), ["Stegmaier, Bernardo", "García López, Ana"]
        texto = server.calendario.join_participants(nombres)
        a.vigia = server.calendario.Vigia()
        a.vigia.recordar({"sugerencia": {"source": "outlook", "participants": nombres}})  # /api/reunion/actual
        a.vigia.tic(None, False, {}, lambda: {}, 0, self.app_dir)  # sin llamada: olvida la lectura
        self.assertIsNone(a.vigia.ultimo)
        a.vigia.recordar({"sugerencia": {"source": "teams", "participants": []}})  # relectura, solo Teams
        with mock.patch.object(server, "TEXTOS_SUGERIDOS", set()) as vistos:
            rec(a, self, participantes=texto, sugerido="outlook")
        self.assertEqual(vistos, {texto})
        a.vigia.historial = [(t - 7200, p) for t, p in a.vigia.historial]  # de hace dos horas: ya no
        with mock.patch.object(server, "TEXTOS_SUGERIDOS", set()) as vistos:
            rec(a, self, participantes=texto, sugerido="outlook", name="Otra")
        self.assertEqual(vistos, set())

    def test_el_historial_no_se_llena_de_repetidos(self):
        """El vigilante relee cada minuto: la misma lista solo renueva su hora y no echa a las anteriores (RF6)."""
        vg = server.calendario.Vigia()
        vg.recordar({"sugerencia": {"participants": ["Pérez, Ana"]}})
        for _ in range(30):
            vg.recordar({"sugerencia": {"participants": ["García, Luis"]}})
        self.assertEqual(vg.participantes_recientes(), [["Pérez, Ana"], ["García, Luis"]])

    def test_releer_renueva_la_hora(self):
        """Una lista vista hace casi una hora y releida ahora sigue valiendo otra hora (RF7)."""
        vg, ahora = server.calendario.Vigia(), [1000.0]
        with mock.patch.object(server.calendario.time, "time", lambda: ahora[0]):
            vg.recordar({"sugerencia": {"participants": ["Pérez, Ana"]}})
            ahora[0] += 3500
            vg.recordar({"sugerencia": {"participants": ["Pérez, Ana"]}})
            ahora[0] += 200
            self.assertEqual(vg.participantes_recientes(), [["Pérez, Ana"]])

    def test_de_grabar_a_confirmar(self):
        """La cadena entera: grabar con la sugerencia de Outlook → el .md → «Confirmar» (el mismo texto, como
        reunion.js) → la pista de la primera identificacion. Editado (quitar a uno), ya cuenta como texto libre."""
        from test_carpetas import make_app, rec
        os.makedirs(os.path.join(self.root, "Alfa"))
        nombres = ["Stegmaier, Bernardo", "García López, Ana", "Ortega y Gasset, José"]
        texto = server.calendario.join_participants(nombres)
        resultados = []
        with mock.patch.object(server, "TEXTOS_SUGERIDOS", set()):
            for n, editar in enumerate((None, lambda t: t.rsplit("; ", 1)[0])):
                a = make_app(self)
                a.vigia = types.SimpleNamespace(ultimo={"sugerencia": {"participants": nombres}})
                a.jobs.schedule_outputs = lambda rel, delay=4.0: None
                r = rec(a, self, participantes=texto, sugerido="outlook", name=f"R{n}")
                wav = os.path.join(self.root, *r["path"].split("/"))
                antes = a.jobs._participant_hint(wav, {})
                campo = server.parse_notes(server.read_text(wav[:-4] + ".md"))[0]["participantes"].strip()
                with mock.patch.object(server, "log"):
                    a.notes_save({"path": r["path"], "props": {"participantes": editar(campo) if editar else campo}})
                props = server.parse_notes(server.read_text(wav[:-4] + ".md"))[0]
                resultados.append((antes, a.jobs._participant_hint(wav, {}), props.get("participantes_origen")))
        self.assertEqual(resultados, [(3, 3, None), (3, 4, None)])  # editado: «Stegmaier, Bernardo; García López, Ana»

    def test_al_reidentificar_si_los_sugirio_la_reunion(self):
        self.identificar(micro_en=None)
        self.assertEqual(self.correr({"participantes": "Ana, Luis", "participantes_origen": "teams"}), 2)


class UnirHablantesTests(Reunion):
    """«Es el mismo que…» desde la etiqueta: dos hablantes de esta reunion son la misma voz."""

    def unir(self, h, con, creado=None):
        return self.app.speakers_join({"path": "r.wav", "h": h, "con": con, "creado": creado})

    def test_dos_sin_nombre(self):
        self.identificar(micro_en=None)
        self.unir("h3", "h1")
        side = self.leer()
        self.assertEqual(set(side["hablantes"]), {"h1", "h2"})
        self.assertEqual(sorted((t["inicio"], t["fin"]) for t in side["tramos"] if t["h"] == "h1"),
                         [(0, 10), (20, 30), (30, 40)])
        self.assertEqual(side["hablantes"]["h1"]["segundos"], 30.0)
        self.assertEqual(self.app.jobs.scheduled, ["r.wav"])  # los destinos de salida se rehacen

    def test_toma_el_nombre_del_que_lo_tenia(self):
        self.identificar(micro_en=None)
        ana = self.app.voices_person({"path": "r.wav", "h": "h3", "nombre": "Ana"})["id"]
        self.unir("h3", "h1")
        self.assertEqual(self.leer()["hablantes"]["h1"]["persona"], ana)
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h1"], ("Ana", False))

    def test_dos_personas_distintas_no_se_unen(self):
        self.identificar(micro_en=None)
        self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})
        self.app.voices_person({"path": "r.wav", "h": "h3", "nombre": "Luis"})
        before = self.leer()
        with self.assertRaises(server.ApiError):
            self.unir("h3", "h1")
        self.assertEqual(self.leer(), before)

    def test_tu_por_el_micro_pasa_al_unido(self):
        self.identificar(micro_en="h2")
        self.unir("h2", "h1")
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h1"], ("Tú", True))
        self.assertEqual(self.leer()["tu"], "h1")

    def test_tu_y_otra_persona_no_se_unen(self):
        self.usuario()
        self.identificar(micro_en=None)
        self.app.voices_person({"path": "r.wav", "h": "h2", "es_usuario": True})  # «Soy yo»
        self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})
        with self.assertRaises(server.ApiError):
            self.unir("h2", "h1")

    def test_lo_que_dice_el_micro_o_lo_provisional_no_impide_unir(self):
        """«Tú» solo por el micro, o un «¿Tú?» provisional, no es una decision: unirlo a Ana dice que es Ana."""
        uid = self.usuario()
        self.identificar(micro_en="h2")
        ana = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})["id"]
        self.unir("h2", "h1")
        side = self.leer()
        self.assertEqual((side["hablantes"]["h1"]["persona"], side["tu"]), (ana, None))
        self.identificar(micro_en=None)
        data = self.leer()
        data["hablantes"]["h2"].update(persona=uid, provisional=True)
        voices.write_sidecar(self.sidecar(), data)
        self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})
        self.unir("h2", "h1")
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h1"], ("Ana", False))

    def test_la_huella_en_memoria_se_descarta(self):
        self.identificar(micro_en=None)
        self.unir("h3", "h1")
        self.assertEqual(set(self.app.jobs.voice_vectors["r.wav"]), {"h2"})

    def test_otra_identificacion_o_el_mismo(self):
        self.identificar(micro_en=None)
        data = self.leer()
        data["creado"] = "2026-11-08T00:00:00"
        voices.write_sidecar(self.sidecar(), data)
        with self.assertRaises(server.ApiError):
            self.unir("h3", "h1", creado="2026-11-07T00:00:00")
        for con in ("h3", "h9", None):
            with self.assertRaises(server.ApiError):
                self.unir("h3", con)

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_js(self):
        defs = js_defs(read("voces.js"), ["fmtSecs", "spkJoinBody", "spkJoinOptions"])
        out = node(defs + """
const J = x => console.log(JSON.stringify(x));
J(spkJoinBody({ path: 'r.wav' }, 'h3', 'h1', 'c1'));
J(spkJoinOptions({ h1: { segundos: 20 }, h2: { segundos: 75 }, h3: { segundos: 4 }, varios: {} }, 'h3', k => 'H' + k));
""")
        body, opts = [json.loads(x) for x in out]
        self.assertEqual(body, {"path": "r.wav", "h": "h3", "con": "h1", "creado": "c1"})
        self.assertEqual(opts, [{"h": "h2", "text": "Hh2 · 1 min 15 s"}, {"h": "h1", "text": "Hh1 · 20 s"}])


# ----------------------------------------------------------------------------- el cableado, sin sherpa-onnx
# Revision de la integracion del 08/11/2026: quitar la llamada a consolidate en diarize, la pista en el subproceso o la
# ruta /api/hablantes/unir no hacia caer ningun test (consolidate y speakers_join se probaban sueltos, y el unico test
# de diarize de verdad necesita sherpa-onnx, que no instala ninguna CI). La interfaz, en test_ui_voces.

class _Tramo:
    def __init__(self, start, end, speaker):
        self.start, self.end, self.speaker = start, end, speaker


class DiarizeConsolidaTests(unittest.TestCase):
    """diarize() con un sherpa de mentira: tres grupos, dos de ellos de la misma voz (coseno 1)."""

    HUELLAS = {0.0: unit(0), 30.0: unit(0), 50.0: unit(2)}  # por el inicio del primer tramo de cada grupo

    def diarize(self, **kw):
        grupos = [_Tramo(0.0, 30.0, 0), _Tramo(30.0, 50.0, 1), _Tramo(50.0, 75.0, 2)]
        sd = types.SimpleNamespace(sample_rate=voices.SR, process=lambda samples, cb: types.SimpleNamespace(
            sort_by_start_time=lambda: list(grupos)))

        def huella(ex, samples16, tramos, max_s=None):
            return self.HUELLAS[tramos[0][0]], sum(b - a for a, b in tramos)
        with mock.patch.object(voices, "_sherpa", return_value=object()), \
                mock.patch.object(voices, "models_ready", return_value=True), \
                mock.patch.object(voices, "model_paths", return_value={}), \
                mock.patch.object(voices, "_diarizer", return_value=sd), \
                mock.patch.object(voices, "_extractor", return_value=object()), \
                mock.patch.object(voices, "fingerprint", side_effect=huella), \
                mock.patch.object(voices, "consolidate", wraps=voices.consolidate) as consolidate:
            res = voices.diarize(np.zeros(75 * voices.SR, np.int16), "datos", **kw)
        return res, consolidate

    def test_une_por_dentro(self):
        res, consolidate = self.diarize()
        self.assertEqual(consolidate.call_count, 1)
        self.assertEqual(sorted(res["hablantes"]), ["h1", "h2"])  # la voz partida en dos, otra vez una
        self.assertEqual(res["hablantes"]["h1"]["segundos"], 50.0)

    def test_sin_consolidar_salen_los_grupos_de_sherpa(self):
        res, consolidate = self.diarize(consolidar=False)
        consolidate.assert_not_called()
        self.assertEqual(len(res["hablantes"]), 3)

    def test_la_pista_llega_a_consolidate(self):
        consolidate = self.diarize(participantes=4)[1]
        self.assertEqual(consolidate.call_args.kwargs.get("participantes"), 4)


class SubprocesoPistaTests(unittest.TestCase):
    """voices.py --diarize … --participantes N (lo que lanza el servidor) entrega N a diarize."""

    def correr(self, *extra):
        tmp = tempfile.mkdtemp(prefix="chuleta-pista-")
        self.addCleanup(shutil.rmtree, tmp, True)
        visto = os.path.join(tmp, "visto.json")
        doble = os.path.join(tmp, "doble.py")
        with open(doble, "w", encoding="utf-8") as f:
            f.write(f"""import json, sys
sys.path.insert(0, {APP!r})
import numpy as np
import voices
voices.load_audio_16k = lambda path, cancelled=None: np.zeros(16000, np.int16)
def diarize(samples, data_dir, threads=2, progress=None, **kw):
    json.dump(kw, open({visto!r}, "w"))
    return {{"tramos": [], "hablantes": {{}}, "huellas": {{}}, "duracion_s": 1.0}}
voices.diarize = diarize
sys.exit(voices.main(sys.argv[1:]))
""")
        r = subprocess.run([sys.executable, doble, "--diarize", os.path.join(tmp, "r.wav"), "--out",
                            os.path.join(tmp, "r.hablantes.json"), "--data", tmp, *extra],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(visto, encoding="utf-8") as f:
            return json.load(f)

    def test_con_pista(self):
        self.assertEqual(self.correr("--participantes", "3").get("participantes"), 3)

    def test_sin_pista(self):
        self.assertIsNone(self.correr().get("participantes"))


class RutaUnirTests(unittest.TestCase):
    """POST /api/hablantes/unir llega a App.speakers_join (sin la ruta, «Es el mismo que…» daba 404)."""

    @classmethod
    def setUpClass(cls):
        class Doble:
            def __getattr__(self, name):
                return lambda b=None: {"metodo": name, "cuerpo": b}
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls._antes = server.Handler.app, server.PORT, server.Handler.token
        server.Handler.app, server.PORT, server.Handler.token = Doble(), cls.port, "prueba-unir"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.Handler.app, server.PORT, server.Handler.token = cls._antes

    def test_ruta(self):
        body = {"path": "r.wav", "h": "h3", "con": "h1", "creado": "c1"}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/hablantes/unir", data=json.dumps(body).encode(),
                                     headers={server.TOKEN_HEADER: "prueba-unir", "X-Audios": "1",
                                              "Content-Type": "application/json"})
        with mock.patch.dict(server.ALIASES, clear=True), urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(json.load(r), {"metodo": "speakers_join", "cuerpo": body})


class CorregirMientrasSeMueveTests(Reunion):
    """Corregir los hablantes (unir, reasignar, nombre) mientras la reunion se mueve (B1c: se mueve sola al quedar
    libre, justo cuando se corrigen las voces): en los dos ordenes, la correccion y el movimiento no se pisan y la
    correccion acaba en la reunion movida, como las notas. Antes se escribia en la ruta vieja (sidecar huerfano) o se
    leia de alli con el sidecar ya movido («Ese hablante ya no está…») (revision de la integracion del 08/11/2026)."""

    UNIR = staticmethod(lambda app, ana: app.speakers_join({"path": "r.wav", "h": "h3", "con": "h1"}))
    REASIGNAR = staticmethod(lambda app, ana: app.voices_reassign({"path": "r.wav", "h": "h3", "persona": ana}))
    NOMBRE = staticmethod(lambda app, ana: app.voices_person({"path": "r.wav", "h": "h3", "nombre": "Ana"}))

    def preparar(self):
        self.identificar(micro_en=None)
        os.makedirs(os.path.join(self.root, "Dest"))
        self.app.notes_lock = threading.Lock()
        return self.v.create("Ana")["id"]

    def mover(self, res):
        res["movida"] = self.app._move_now(os.path.join(self.root, "r.wav"), os.path.join(self.root, "Dest"))

    def corregir(self, fn, ana, res):
        try:
            res["ok"] = fn(self.app, ana)
        except Exception as e:  # noqa: BLE001
            res["error"] = repr(e)

    def resultado(self, res):
        self.assertNotIn("error", res, res)
        self.assertEqual(res.get("movida"), "Dest/r.wav", res)
        self.assertFalse(os.path.exists(self.sidecar()), "sidecar huerfano en la ruta vieja")
        return voices.read_sidecar(os.path.join(self.root, "Dest", "r.hablantes.json"))

    def correccion_primero(self, fn, funcion):
        """La correccion ya esta dentro de voices.<funcion> (entre leer el sidecar y escribirlo) y llega el movimiento:
        espera a que termine."""
        ana, en_medio, seguir, res = self.preparar(), threading.Event(), threading.Event(), {}
        real = getattr(voices, funcion)

        def despacio(*a, **k):
            en_medio.set()
            seguir.wait(5)
            return real(*a, **k)
        with mock.patch.object(voices, funcion, despacio), mock.patch.object(server, "log"):
            t = threading.Thread(target=self.corregir, args=(fn, ana, res))
            t.start()
            self.assertTrue(en_medio.wait(5))
            m = threading.Thread(target=self.mover, args=(res,))
            m.start()
            m.join(0.5)
            espero = m.is_alive()
            seguir.set()
            t.join(5)
            m.join(5)
        self.assertTrue(espero, f"el movimiento no espera a la correccion: {res}")
        return ana, self.resultado(res)

    def movimiento_primero(self, fn, antes=None):
        """El movimiento ya ha llevado el sidecar a Dest y esta copiando el audio (el ultimo; entre unidades tarda) y
        llega la correccion: espera y va a la ruta nueva."""
        ana, en_medio, seguir, res, real = self.preparar(), threading.Event(), threading.Event(), {}, server.move_one
        if antes:
            antes(ana)

        def despacio(f, t):
            if f.endswith(".wav"):
                en_medio.set()
                seguir.wait(5)
            return real(f, t)
        with mock.patch.object(server, "move_one", despacio), mock.patch.object(server, "log"):
            m = threading.Thread(target=self.mover, args=(res,))
            m.start()
            self.assertTrue(en_medio.wait(5))
            t = threading.Thread(target=self.corregir, args=(fn, ana, res))
            t.start()
            t.join(0.5)
            espero = t.is_alive()
            seguir.set()
            m.join(5)
            t.join(5)
        self.assertTrue(espero, f"la correccion no espera al movimiento: {res}")
        return ana, self.resultado(res)

    def test_unir_con_la_correccion_primero(self):
        self.assertEqual(set(self.correccion_primero(self.UNIR, "join_speakers")[1]["hablantes"]), {"h1", "h2"})

    def test_unir_con_el_movimiento_primero(self):
        self.assertEqual(set(self.movimiento_primero(self.UNIR)[1]["hablantes"]), {"h1", "h2"})

    def test_reasignar_con_la_correccion_primero(self):
        ana, side = self.correccion_primero(self.REASIGNAR, "assign")
        self.assertEqual(side["hablantes"]["h3"]["persona"], ana)

    def test_reasignar_con_el_movimiento_primero(self):
        ana, side = self.movimiento_primero(self.REASIGNAR)
        self.assertEqual(side["hablantes"]["h3"]["persona"], ana)

    def test_nombre_con_la_correccion_primero(self):
        ana, side = self.correccion_primero(self.NOMBRE, "assign")
        self.assertEqual(side["hablantes"]["h3"]["persona"], ana)

    def test_nombre_con_el_movimiento_primero(self):
        ana, side = self.movimiento_primero(self.NOMBRE)
        self.assertEqual(side["hablantes"]["h3"]["persona"], ana)

    def test_fusionar_personas_con_el_movimiento_primero(self):
        """«Fusionar con…»: la reunion donde sale la persona que desaparece se esta moviendo."""
        def con_luis(ana):
            self.app.voices_reassign({"path": "r.wav", "h": "h3", "persona": self.v.create("Luis")["id"]})

        def fusionar(app, ana):
            return app.voices_merge({"id_a": ana, "id_b": self.v.by_name("Luis")["id"]})
        ana, side = self.movimiento_primero(fusionar, antes=con_luis)
        self.assertEqual(side["hablantes"]["h3"]["persona"], ana)

    def test_la_respuesta_se_calcula_con_el_candado(self):
        """La respuesta de unir y reasignar (speakers_get) sale con el candado cogido: fuera, un movimiento que
        empezara justo al soltarlo la dejaba a medias (FileNotFoundError, un 500 aunque la correccion se guardo)."""
        ana, cogido, real = self.preparar(), [], self.app.speakers_get

        def espia(q):
            cogido.append(self.app.notes_lock.locked())
            return real(q)
        with mock.patch.object(self.app, "speakers_get", espia), mock.patch.object(server, "log"):
            self.app.voices_reassign({"path": "r.wav", "h": "h3", "persona": ana})
            self.app.speakers_join({"path": "r.wav", "h": "h3", "con": "h1"})
        self.assertEqual(cogido, [True, True])

    def test_exportar_con_el_movimiento_primero(self):
        """«Exportar» escribe .hablantes.txt junto al audio: con un movimiento en curso, espera y va a la nueva."""
        self.movimiento_primero(lambda app, ana: app.voices_export({"path": "r.wav"}))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Dest", "r.hablantes.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "r.hablantes.txt")))

    def test_restaurar_el_original_con_el_movimiento_primero(self):
        """«Restaurar el original» de la transcripcion (escribe .srt/.txt junto al audio): espera al movimiento y
        restaura en la ruta nueva (RF5: dejaba el original en la vieja, borraba la copia y decia «ok»)."""
        def editar(ana):
            self.app._tx_save_lock = threading.Lock()
            self.app.jobs.active_for = lambda rel, kinds=None: None
            with mock.patch.object(server, "log"):
                self.app.save_transcript({"path": "r.wav", "segments": [{"start": 0.5, "end": 9.5, "text": "EDITADO"}]})
            self.assertTrue(os.path.isdir(server.backup_dir("r.wav")))
        self.movimiento_primero(lambda app, ana: app.restore_transcript({"path": "r.wav"}), antes=editar)
        with open(os.path.join(self.root, "Dest", "r.srt"), encoding="utf-8-sig") as f:
            srt = f.read()
        self.assertIn("frase de h1", srt)
        self.assertNotIn("EDITADO", srt)
        self.assertFalse(os.path.exists(os.path.join(self.root, "r.srt")), "el original, huerfano en la ruta vieja")
        self.assertFalse(os.path.isdir(server.backup_dir("Dest/r.wav")))

    def test_recordar_con_el_movimiento_en_medio(self):
        """«Recordar» toma la huella con la ruta de ahora: la reunion se mueve justo despues de guardar el nombre."""
        ana, res, real = self.preparar(), {}, self.app._not_the_user

        def y_se_mueve(*a, **k):
            self.mover(res)
            return real(*a, **k)
        with mock.patch.object(self.app, "_not_the_user", y_se_mueve), mock.patch.object(server, "log"):
            r = self.app.voices_person({"path": "r.wav", "h": "h3", "nombre": "Ana", "recordar": True,
                                        "consentimiento": True})
        self.assertEqual(res.get("movida"), "Dest/r.wav")
        self.assertEqual((r["id"], r["huellas"]), (ana, 1))

    def test_nombre_con_el_movimiento_en_medio(self):
        """Se mueve entera entre la comprobacion y la escritura (mientras se decide quien es): va a la ruta nueva."""
        ana, res, real = self.preparar(), {}, self.app._persona_for_speaker

        def y_se_mueve(*a, **k):
            self.mover(res)
            return real(*a, **k)
        with mock.patch.object(self.app, "_persona_for_speaker", y_se_mueve), mock.patch.object(server, "log"):
            self.corregir(self.NOMBRE, ana, res)
        self.assertEqual(self.resultado(res)["hablantes"]["h3"]["persona"], ana)


class FusionarPersonasTests(Reunion):
    """«Fusionar con…» (el cuadro de un hablante) con una reunion cuyo sidecar no se puede escribir (OneDrive, el
    antivirus): no borra a la persona hasta haber reescrito todas, dice cuantas faltan y se puede repetir (revision de
    la integracion del 08/11/2026: antes la borraba primero y esas reuniones se quedaban con un id que no existia)."""

    def test_una_reunion_bloqueada_y_luego_libre(self):
        self.identificar(micro_en=None)
        luis, ana = self.v.create("Luis")["id"], self.v.create("Ana")["id"]
        self.app.voices_reassign({"path": "r.wav", "h": "h3", "persona": luis})
        self.wav("s.wav", seconds=0.5)
        s_side = os.path.join(self.root, "s.hablantes.json")
        voices.write_sidecar(s_side, dict(self.leer()))
        real = voices.write_sidecar

        def bloqueado(path, data):
            if path.endswith("s.hablantes.json"):
                raise PermissionError(13, "en uso")
            return real(path, data)
        with mock.patch.object(voices, "write_sidecar", bloqueado), mock.patch.object(server, "log"):
            with self.assertRaises(server.ApiError) as e:
                self.app.voices_merge({"id_a": ana, "id_b": luis})
        self.assertIn("1 reunión no se ha podido actualizar", str(e.exception.msg))
        self.assertIsNotNone(self.v.find(luis))  # sigue: se puede repetir
        self.assertEqual(self.leer()["hablantes"]["h3"]["persona"], ana)
        self.assertEqual(voices.read_sidecar(s_side)["hablantes"]["h3"]["persona"], luis)
        with mock.patch.object(server, "log"):
            r = self.app.voices_merge({"id_a": ana, "id_b": luis})  # ya libre
        self.assertEqual(r["reuniones"], 1)
        self.assertIsNone(self.v.find(luis))
        self.assertEqual(voices.read_sidecar(s_side)["hablantes"]["h3"]["persona"], ana)


class FusionarPersonasMasTests(Reunion):
    """«Fusionar», mas casos (RF7): una reunion que no se puede LEER tambien falta; y si la fusion falla al final (otra
    pestaña cambio a una de las dos), se deshace lo reescrito y se dice."""

    def preparar(self):
        self.identificar(micro_en=None)
        luis, ana = self.v.create("Luis")["id"], self.v.create("Ana")["id"]
        self.app.voices_reassign({"path": "r.wav", "h": "h3", "persona": luis})
        self.app.voices_reassign({"path": "r.wav", "h": "h1", "persona": ana})  # ya era de Ana: no se toca
        return luis, ana

    def test_no_se_puede_leer(self):
        luis, ana = self.preparar()
        real = voices.read_text

        def bloqueado(path, *a, **k):
            if path.endswith("r.hablantes.json"):
                raise PermissionError(13, "solo en línea")
            return real(path, *a, **k)
        with mock.patch.object(voices, "read_text", bloqueado), mock.patch.object(server, "log"):
            with self.assertRaises(server.ApiError):
                self.app.voices_merge({"id_a": ana, "id_b": luis})
        self.assertIsNotNone(self.v.find(luis))
        self.assertEqual(self.leer()["hablantes"]["h3"]["persona"], luis)

    def test_si_la_otra_desaparece_entretanto_no_se_deshace(self):
        """Otra pestaña olvida a la que desaparece mientras se reescriben las reuniones: deshacer las dejaria con un id
        que ya no existe; se quedan con la que se queda (RF8)."""
        luis, ana = self.preparar()
        real = self.v.merge

        def olvidada_entretanto(keep, other):
            self.v.forget(other)
            return real(keep, other)
        with mock.patch.object(self.v, "merge", olvidada_entretanto), mock.patch.object(server, "log"):
            with self.assertRaises(server.ApiError) as e:
                self.app.voices_merge({"id_a": ana, "id_b": luis})
        self.assertIn("ya muestran a la que se queda", e.exception.msg)
        self.assertEqual(self.leer()["hablantes"]["h3"]["persona"], ana)

    def test_una_persona_ya_borrada_no_se_fusiona(self):
        luis, ana = self.preparar()
        self.v.forget(luis)
        with self.assertRaises(server.ApiError):
            self.app.voices_merge({"id_a": ana, "id_b": luis})
        self.assertEqual(self.leer()["hablantes"]["h3"]["persona"], luis)  # sin tocar ninguna reunion

    def test_falla_al_final_y_se_deshace(self):
        luis, ana = self.preparar()
        with mock.patch.object(self.v, "merge", side_effect=ValueError("ahora es tu persona")), \
                mock.patch.object(server, "log"):
            with self.assertRaises(server.ApiError) as e:
                self.app.voices_merge({"id_a": ana, "id_b": luis})
        self.assertIn("No se ha cambiado ninguna reunión", e.exception.msg)
        hs = self.leer()["hablantes"]
        self.assertEqual((hs["h3"]["persona"], hs["h1"]["persona"]), (luis, ana))
        self.assertIsNotNone(self.v.find(luis))

    def test_si_no_se_puede_deshacer_lo_dice(self):
        luis, ana = self.preparar()
        real = voices.write_sidecar
        n = [0]

        def segunda_falla(path, data):  # la primera escritura (fusionar) va; la del deshacer, no
            n[0] += 1
            if n[0] > 1:
                raise PermissionError(13, "en uso")
            return real(path, data)
        with mock.patch.object(self.v, "merge", side_effect=ValueError("ahora es tu persona")), \
                mock.patch.object(voices, "write_sidecar", segunda_falla), mock.patch.object(server, "log"), \
                mock.patch.object(server, "log_exc"):
            with self.assertRaises(server.ApiError) as e:
                self.app.voices_merge({"id_a": ana, "id_b": luis})
        self.assertIn("1 reunión se ha quedado con la persona elegida", e.exception.msg)


class EvalScoreTests(unittest.TestCase):
    """La medida de scripts/eval_voces.py (DER por tramas, pureza, cobertura, en cuantos grupos se parte cada uno)."""

    def test_perfecta(self):
        ref = [(0, 10, "A"), (10, 20, "B")]
        r = eval_voces.score(ref, [{"inicio": 0, "fin": 10, "h": "h1"}, {"inicio": 10, "fin": 20, "h": "h2"}],
                             {"h1": {}, "h2": {}})
        self.assertEqual((r["der"], r["pureza"], r["cobertura"], r["partes_max"], r["hablantes"]), (0, 1, 1, 1, 2))

    def test_una_persona_en_dos_grupos(self):
        ref = [(0, 10, "A"), (10, 20, "B")]
        hyp = [{"inicio": 0, "fin": 5, "h": "h1"}, {"inicio": 5, "fin": 10, "h": "h2"},
               {"inicio": 10, "fin": 20, "h": "h3"}]
        r = eval_voces.score(ref, hyp, {"h1": {}, "h2": {}, "h3": {}})
        self.assertEqual((r["partes_max"], r["partes_media"], r["hablantes"]), (2, 1.5, 3))
        self.assertAlmostEqual(r["confusion"], 0.25)
        self.assertAlmostEqual(r["der"], 0.25)
        self.assertEqual(r["pureza"], 1.0)
        self.assertAlmostEqual(r["cobertura"], 0.75)

    def test_no_detectado_y_falsa_alarma(self):
        r = eval_voces.score([(0, 10, "A")], [{"inicio": 5, "fin": 15, "h": "h1"}], {"h1": {"breve": True}})
        self.assertEqual((r["no_detectado"], r["falsa_alarma"], r["confusion"], r["hablantes"]), (0.5, 0.5, 0, 0))

    def test_solape_no_cuenta_como_partir(self):
        """Mientras A y B hablan a la vez, el grupo de A lleva voz de B: B no esta «partido»."""
        ref = [(0, 20, "A"), (10, 30, "B")]
        hyp = [{"inicio": 0, "fin": 20, "h": "h1"}, {"inicio": 20, "fin": 30, "h": "h2"}]
        r = eval_voces.score(ref, hyp, {"h1": {}, "h2": {}})
        self.assertEqual(r["partes_max"], 1)


if __name__ == "__main__":
    unittest.main()
