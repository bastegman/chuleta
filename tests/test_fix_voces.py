# -*- coding: utf-8 -*-
"""S6 (msg 1170, 06-nov-2026): «a veces pongo un nombre de quien ha dicho qué, y asume que soy yo».

Poner nombre a un hablante (o fusionarlo) no puede convertirlo en «Tú» ni tocar la persona del usuario: ni
renombrarla, ni guardarle la huella de otra voz, ni hacer «usuario» a otra persona. Y un «Tú» mal puesto por la
heuristica del micro se tiene que poder corregir. Sin sherpa-onnx: sidecars y huellas sinteticas."""
import json
import os
import shutil
import subprocess
import unittest

import numpy as np

from support import TempTree, bare_jobs, server, srt_text
from test_frontend import js_defs, read

import voices  # noqa: E402

RECORDAR = "2026-11-07T10:00:00"


def unit(i, dim=voices.DIM):
    v = np.zeros(dim, np.float32)
    v[i] = 1.0
    return v


def mix(a, b, cos_a):
    """Vector con coseno cos_a respecto a `a` (unitario) y el resto sobre `b` (ortogonal)."""
    return a * cos_a + b * float(np.sqrt(1 - cos_a ** 2))


def voices_app(t):
    """Un App sin hilos con lo que usan las rutas de voces: ajustes (voces activadas), biblioteca y Jobs."""
    app = server.App.__new__(server.App)
    app.settings = server.Store(os.path.join(t.data_dir, "settings.json"), server.DEFAULT_SETTINGS)
    app.settings.update(lambda d: d.update(voices_enabled=True))
    app.library = server.Library(server.Durations(os.path.join(t.data_dir, "durations.json")))
    jobs = bare_jobs([])
    jobs.settings, jobs.library = app.settings, app.library
    jobs._voices = voices.Voices(os.path.join(t.data_dir, "voces.json"))
    jobs.voice_vectors = {}
    jobs.scheduled = []
    jobs.schedule_outputs = lambda rel, delay=4.0: jobs.scheduled.append(rel)
    app.jobs = jobs
    return app


class Reunion(TempTree):
    """Una reunion «r.wav» con tres hablantes: h1 y h3 sin reconocer, y h2, al que el micro marca como «Tú».
    huellas: h1 → e3, h2 → e4, h3 → e5 (ortogonales entre si y a la del usuario, e0)."""

    HUELLAS = {"h1": unit(3), "h2": unit(4), "h3": unit(5)}

    def setUp(self):
        super().setUp()
        self.app = voices_app(self)
        self.v = self.app.jobs.voices
        self.wav("r.wav", seconds=0.5)
        self.write("r.srt", srt_text([(0.5, 9.5, "frase de h1"), (10.5, 19.5, "frase de h2"),
                                      (20.5, 29.5, "otra de h1"), (30.5, 39.5, "frase de h3")]))

    def usuario(self, nombre="Bernardo"):
        """La persona del usuario, ya con una huella suya (de «Soy yo» + «Recordar» en otra reunion)."""
        yo = self.v.create(nombre, es_usuario=True, consentimiento=RECORDAR)
        self.v.remember(yo["id"], unit(0), 60, "antes.wav", RECORDAR)
        return yo["id"]

    def identificar(self, micro_en="h2", huellas=None):
        """Lo que hace _run_voices al terminar el subproceso: sidecar + identify + huellas en memoria."""
        tramos = [{"inicio": 0, "fin": 10, "h": "h1"}, {"inicio": 10, "fin": 20, "h": "h2"},
                  {"inicio": 20, "fin": 30, "h": "h1"}, {"inicio": 30, "fin": 40, "h": "h3"}]
        bits = "".join("1" if micro_en and t["h"] == micro_en else "0" for t in tramos for _ in range(40))
        data = {"version": 1, "tramos": tramos,
                "hablantes": {"h1": {"segundos": 20.0, "breve": False}, "h2": {"segundos": 20.0, "breve": False},
                              "h3": {"segundos": 20.0, "breve": False}},
                "micro": {"paso": 0.25, "activo": bits, "desfase": 0} if micro_en else None}
        huellas = {h: list(map(float, v)) for h, v in (huellas or self.HUELLAS).items()}
        voices.identify(data, huellas, self.v)
        voices.write_sidecar(self.sidecar(), data)
        self.app.jobs.voice_vectors["r.wav"] = huellas
        return data

    def sidecar(self):
        return os.path.join(self.root, "r.hablantes.json")

    def leer(self):
        return voices.read_sidecar(self.sidecar())

    def etiquetas(self):
        data = self.app.jobs.speakers_for(os.path.join(self.root, "r.wav"))
        return {h: (i["label"], i["usuario"]) for h, i in data["hablantes"].items()}, data

    def persona(self, nombre):
        return next((p for p in self.v.personas() if p["nombre"] == nombre), None)


class NombrarAlHablanteTuTests(Reunion):
    """El micro marca como «Tú» a otra persona (eco por altavoces, reunion hibrida, un compañero al lado…)."""

    def test_reproduccion_peticion_de_la_interfaz_anterior(self):
        """Lo que mandaba voces.js al escribir «Ana» en la etiqueta «Tú» (`id: cur.id`, porque el hablante ya era
        del usuario por el micro): renombraba al usuario y le guardaba la voz de Ana."""
        yo = self.usuario()
        data = self.identificar()
        self.assertEqual((data["tu"], data["hablantes"]["h2"]["persona"]), ("h2", yo))  # punto de partida
        self.app.voices_person({"id": yo, "path": "r.wav", "h": "h2", "nombre": "Ana",
                                "recordar": True, "consentimiento": True})
        self.assertEqual(self.v.find(yo)["nombre"], "Bernardo")  # el usuario no cambia de nombre
        self.assertEqual(len(self.v.find(yo)["huellas"]), 1)  # ni se le guarda la voz de otra persona
        self.assertTrue(self.v.find(yo)["es_usuario"])
        ana = self.persona("Ana")
        self.assertIsNotNone(ana)
        self.assertFalse(ana["es_usuario"])
        self.assertEqual([h["vector"][4] for h in ana["huellas"]], [1.0])  # la huella de h2 va a Ana
        side = self.leer()
        self.assertEqual(side["hablantes"]["h2"]["persona"], ana["id"])
        self.assertIsNone(side["tu"])  # el micro se equivocaba con h2: deja de ser «Tú»
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h2"], ("Ana", False))

    def test_sin_persona_de_usuario_el_nombre_gana_al_micro(self):
        """Sin persona «Tú» guardada: el nombre se ponia, pero la etiqueta seguia diciendo «Tú»."""
        self.identificar()
        self.app.voices_person({"path": "r.wav", "h": "h2", "nombre": "Ana"})
        labels, data = self.etiquetas()
        self.assertEqual(labels["h2"], ("Ana", False))
        self.assertIsNone(data["tu"])
        self.assertEqual(voices.label("h2", data, self.v.names(), for_llm=True), "Ana")  # resumen y pantalla, igual

    def test_no_soy_yo(self):
        yo = self.usuario()
        self.identificar()
        r = self.app.voices_reassign({"path": "r.wav", "h": "h2", "persona": None})
        self.assertIsNone(r["tu"])
        self.assertEqual((r["hablantes"]["h2"]["label"], r["hablantes"]["h2"]["usuario"]), ("Persona 2", False))
        self.assertEqual(self.v.find(yo)["nombre"], "Bernardo")

    def test_fusionar_desde_tu_no_hace_usuario_a_otra_persona(self):
        """«Fusionar con… Ana» en la etiqueta «Tú» fundia la persona del usuario DENTRO de Ana: Ana pasaba a ser
        «usuario» y se quedaba con sus huellas."""
        yo = self.usuario()
        ana = self.v.create("Ana")["id"]
        self.v.remember(ana, unit(7), 30, "otra.wav", RECORDAR)
        self.identificar()
        with self.assertRaises(server.ApiError):
            self.app.voices_merge({"id_a": ana, "id_b": yo})
        self.assertTrue(self.v.find(yo)["es_usuario"])
        self.assertFalse(self.v.find(ana)["es_usuario"])
        self.assertEqual(len(self.v.find(ana)["huellas"]), 1)
        # lo que hace ahora la interfaz en ese caso: reasignar el hablante, sin fusionar personas
        r = self.app.voices_reassign({"path": "r.wav", "h": "h2", "persona": ana})
        self.assertEqual((r["hablantes"]["h2"]["label"], r["tu"]), ("Ana", None))
        self.assertEqual(len(self.v.personas()), 2)

    def test_soy_yo_mueve_el_tu_y_no_renombra(self):
        """«Soy yo» en el hablante bueno (h1) cuando el micro marco a h2: el «Tú» pasa a h1, h2 vuelve a ser
        «Persona 2» y el nombre que hubiera en el cuadro (el de otra persona) no renombra al usuario."""
        yo = self.usuario()
        self.identificar()
        self.app.voices_person({"id": yo, "path": "r.wav", "h": "h1", "es_usuario": True, "nombre": "Ana",
                                "recordar": False, "consentimiento": False})
        self.assertEqual(self.v.find(yo)["nombre"], "Bernardo")
        labels, data = self.etiquetas()
        self.assertEqual(data["tu"], "h1")
        self.assertEqual(labels["h1"], ("Tú", True))
        self.assertEqual(labels["h2"], ("Persona 2", False))
        self.assertIsNone(data["hablantes"]["h2"]["persona"])

    def test_soy_yo_sin_persona_crea_la_del_usuario(self):
        self.identificar(micro_en=None)
        r = self.app.voices_person({"path": "r.wav", "h": "h3", "es_usuario": True, "nombre": "Ana",
                                    "recordar": True, "consentimiento": True})
        yo = self.v.user()  # el cuadro puede traer el nombre de otra persona: «Yo» (se cambia en Ajustes)
        self.assertEqual((yo["id"], yo["nombre"], len(yo["huellas"])), (r["id"], "Yo", 1))
        labels, data = self.etiquetas()
        self.assertEqual((data["tu"], labels["h3"]), ("h3", ("Tú", True)))


class NombrarOtrosHablantesTests(Reunion):
    def test_reconocido_por_huella_como_usuario(self):
        """La huella confunde a h3 con el usuario (coseno 0,6: sale «Tú»); al escribir su nombre no se renombra al
        usuario ni se le toca su huella (no se parece lo bastante a la de h3 como para ser la misma voz)."""
        yo = self.usuario()
        self.identificar(micro_en=None, huellas=dict(self.HUELLAS, h3=mix(unit(0), unit(6), 0.6)))
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h3"], ("Tú", True))
        self.app.voices_person({"id": yo, "path": "r.wav", "h": "h3", "nombre": "Carlos", "recordar": True,
                                "consentimiento": True})
        self.assertEqual((self.v.find(yo)["nombre"], len(self.v.find(yo)["huellas"])), ("Bernardo", 1))
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h3"], ("Carlos", False))

    def test_huella_de_usuario_sin_micro_activo_se_pregunta(self):
        """Grabacion propia: la huella dice «usuario» pero ese hablante nunca habla por el micro → «¿Tú?»."""
        self.usuario()
        data = self.identificar(micro_en="h2", huellas=dict(self.HUELLAS, h3=unit(0)))
        self.assertTrue(data["hablantes"]["h3"]["provisional"])
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h3"], ("¿Tú?", True))

    def test_micro_contra_huella_de_otra_persona_se_pregunta(self):
        """El micro dice «Tú» y la huella dice Ana: no se decide solo; sale «¿Ana?» para confirmar."""
        ana = self.v.create("Ana")["id"]
        self.v.remember(ana, unit(4), 30, "otra.wav", RECORDAR)
        data = self.identificar()
        self.assertEqual(data["hablantes"]["h2"]["persona"], ana)
        self.assertTrue(data["hablantes"]["h2"]["provisional"])
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h2"], ("¿Ana?", False))

    def test_nombre_que_ya_existe_no_duplica(self):
        """Elegir «Ana» de la lista asignaba una Ana NUEVA (dos Anas: siempre «¿Ana?» por candidatos a < 0,10)."""
        ana = self.v.create("Ana")["id"]
        self.v.remember(ana, unit(7), 30, "otra.wav", RECORDAR)
        self.identificar(micro_en=None)
        r = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "  ána ", "recordar": True,
                                    "consentimiento": True})
        self.assertEqual(r["id"], ana)
        self.assertEqual([p["nombre"] for p in self.v.personas()], ["Ana"])
        self.assertEqual(len(self.v.find(ana)["huellas"]), 2)
        self.assertEqual(self.leer()["hablantes"]["h1"]["persona"], ana)

    def test_nombre_igual_al_del_usuario_es_otra_persona(self):
        """Refutador P1-a: un compañero que se llama como el usuario no es «Tú» (el usuario se marca con «Soy yo»)."""
        yo = self.usuario()
        self.identificar()
        r = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "bernardo", "recordar": True,
                                    "consentimiento": True})
        self.assertNotEqual(r["id"], yo)
        labels, data = self.etiquetas()
        self.assertEqual((labels["h1"], labels["h2"], data["tu"]), (("bernardo", False), ("Tú", True), "h2"))
        self.assertEqual(len(self.v.find(yo)["huellas"]), 1)

    def test_corregir_el_nombre_solo_si_se_pide(self):
        """Corregir una errata («Javi» → «Javier») renombra en todas las reuniones solo con `renombrar`; sin el,
        escribir otro nombre es decir que ese hablante es otra persona (no se pisa el nombre de la primera)."""
        self.identificar(micro_en=None)
        javi = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Javi"})["id"]
        self.app.voices_person({"id": javi, "path": "r.wav", "h": "h1", "nombre": "Javier", "renombrar": True})
        self.assertEqual(self.v.find(javi)["nombre"], "Javier")
        self.assertEqual(len(self.v.personas()), 1)
        r = self.app.voices_person({"id": javi, "path": "r.wav", "h": "h1", "nombre": "Luis"})
        self.assertNotEqual(r["id"], javi)
        self.assertEqual(self.v.find(javi)["nombre"], "Javier")
        self.assertEqual(self.leer()["hablantes"]["h1"]["persona"], r["id"])

    def test_renombrar_nunca_toca_al_usuario_desde_un_hablante(self):
        yo = self.usuario()
        self.identificar()
        self.app.voices_person({"id": yo, "path": "r.wav", "h": "h2", "nombre": "Ana", "renombrar": True})
        self.assertEqual(self.v.find(yo)["nombre"], "Bernardo")
        self.assertEqual(self.leer()["hablantes"]["h2"]["persona"], self.persona("Ana")["id"])

    def test_ajustes_sigue_renombrando_por_id(self):
        """Ajustes › Voces (sin reunion): el nombre de una persona, también el del usuario, se cambia por su id."""
        yo = self.usuario()
        self.app.voices_person({"id": yo, "nombre": "Bernar"})
        self.assertEqual(self.v.find(yo)["nombre"], "Bernar")


class EtiquetaTests(unittest.TestCase):
    """label(): la persona asignada manda sobre la heuristica del micro; «Tú» es la persona del usuario."""
    names = {"pu": "Bernardo", "pa": "Ana"}

    def data(self, **h2):
        return {"tu": "h2", "hablantes": {"h1": {}, "h2": dict({"segundos": 30}, **h2)}}

    def test_persona_gana_al_micro(self):
        self.assertEqual(voices.label("h2", self.data(persona="pa"), self.names, usuario="pu"), "Ana")
        self.assertEqual(voices.label("h2", self.data(persona="pa"), self.names, for_llm=True, usuario="pu"), "Ana")

    def test_usuario_es_tu(self):
        d = {"tu": None, "hablantes": {"h1": {"persona": "pu"}}}
        self.assertEqual(voices.label("h1", d, self.names, usuario="pu"), "Tú")
        self.assertEqual(voices.label("h1", d, self.names, for_llm=True, usuario="pu"), "Bernardo")
        d["hablantes"]["h1"]["provisional"] = True
        self.assertEqual(voices.label("h1", d, self.names, usuario="pu"), "¿Tú?")

    def test_micro_sin_persona(self):
        self.assertEqual(voices.label("h2", self.data(), self.names, usuario="pu"), "Tú")
        self.assertEqual(voices.label("h2", self.data(), {}, for_llm=True), "Yo")

    def test_persona_olvidada_vuelve_al_micro_o_a_persona_n(self):
        self.assertEqual(voices.label("h2", self.data(persona="px"), self.names, usuario="pu"), "Tú")
        self.assertEqual(voices.label("h1", {"hablantes": {"h1": {"persona": "px"}}}, self.names), "Persona 1")


class MergeUsuarioTests(TempTree):
    def test_el_modelo_no_funde_al_usuario_en_otra_persona(self):
        v = voices.Voices(os.path.join(self.data_dir, "voces.json"))
        yo = v.create("Bernardo", es_usuario=True)["id"]
        ana = v.create("Ana")["id"]
        with self.assertRaises(ValueError):
            v.merge(ana, yo)
        with self.assertRaises(ValueError):  # refutador P1-d: al reves, el usuario se quedaria con la voz de Ana
            v.merge(yo, ana)
        self.assertEqual((v.user()["id"], len(v.personas())), (yo, 2))


class LineaDeTiempoDelMicroTests(unittest.TestCase):
    """S5: la linea de tiempo del micro va ~0,875 s adelantada y con huecos (medido con recorder.py real y un
    soundcard falso: pitidos en el WAV en 3,0-4,0 / 6,0-6,6 / 8,0-10,0 s; niveles activos en 2,0-3,25 / 5,25-6,0 /
    7,0-9,25 con 9 de 49 pasos sin nivel)."""
    # la linea de tiempo tal cual salio de esa medida (49 pasos de 0,25 s)
    MEDIDA = "".join("1" if k in {8, 9, 11, 12, 21, 22, 23, 28, *range(30, 37)} else "0" for k in range(49))
    TRAMOS = [{"inicio": 3.0, "fin": 4.0, "h": "U"}, {"inicio": 4.0, "fin": 6.0, "h": "O"},
              {"inicio": 6.0, "fin": 6.6, "h": "U"}, {"inicio": 6.6, "fin": 8.0, "h": "O"},
              {"inicio": 8.0, "fin": 10.0, "h": "U"}, {"inicio": 10.0, "fin": 12.0, "h": "O"}]

    def test_desfase_es_el_de_la_grabadora(self):
        import recorder
        self.assertAlmostEqual(voices.MIC_DESFASE_S, recorder.LAT - voices.MIC_PASO / 2)

    def test_cerrar_huecos_de_un_paso(self):
        self.assertEqual(voices.close_gaps("1101011001"), "1111111001")
        self.assertEqual(voices.close_gaps("0110"), "0110")
        self.assertEqual(voices.close_gaps(""), "")

    def test_la_medida_real_da_tu(self):
        micro = {"paso": 0.25, "activo": self.MEDIDA}  # sin "desfase": las de las grabaciones ya hechas
        r = voices.mic_ratios(self.TRAMOS, micro)
        self.assertGreater(r["U"], 0.9)
        self.assertLessEqual(r["O"], 0.2)
        self.assertEqual(voices.mark_user(self.TRAMOS, micro), "U")
        sin_corregir = voices.mic_ratios(self.TRAMOS, dict(micro, desfase=0))
        self.assertLess(sin_corregir["U"], 0.6)  # como se leia en main: ningun «Tú»
        self.assertIsNone(voices.mark_user(self.TRAMOS, dict(micro, desfase=0)))

    def test_lo_que_el_desfase_deja_fuera_no_cuenta(self):
        micro = {"paso": 0.25, "activo": "1" * 8}  # 2 s de linea de tiempo; el ultimo 0,875 s de audio queda fuera
        self.assertEqual(voices.mic_ratios([{"inicio": 0.0, "fin": 2.875, "h": "U"}], micro), {"U": 1.0})
        r = voices.mic_ratios([{"inicio": 0.0, "fin": 2.0, "h": "U"}, {"inicio": 4.0, "fin": 6.0, "h": "O"}], micro)
        self.assertEqual(r["O"], 0.0)  # mas alla del final: inactivo, como antes


class VolverAIdentificarTests(Reunion):
    """«Volver a identificar» reescribia el sidecar desde cero: se perdian los nombres puestos sin «Recordar» y
    volvia el «Tú» que el usuario ya habia quitado."""

    def nuevo(self):
        """Lo que escribe el subproceso al rediarizar: los mismos tiempos con las etiquetas cambiadas."""
        return {"version": 1, "tramos": [{"inicio": 0, "fin": 10, "h": "h2"}, {"inicio": 10, "fin": 20, "h": "h1"},
                                         {"inicio": 20, "fin": 30, "h": "h2"}, {"inicio": 30, "fin": 40, "h": "h3"}],
                "hablantes": {"h1": {"segundos": 20.0, "breve": False}, "h2": {"segundos": 20.0, "breve": False},
                              "h3": {"segundos": 20.0, "breve": False}},
                "micro": self.leer()["micro"], "tu": None}

    def test_carry_manual(self):
        self.usuario()
        self.identificar()
        ana = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})["id"]
        self.app.voices_reassign({"path": "r.wav", "h": "h2", "persona": None})  # «No soy yo»
        old, new = self.leer(), self.nuevo()
        huellas = {"h1": list(map(float, unit(4))), "h2": list(map(float, unit(3))), "h3": list(map(float, unit(5)))}
        voices.identify(new, huellas, self.v)
        self.assertEqual(new["tu"], "h1")  # el micro vuelve a decir que h1 (el antiguo h2) eres tu
        voices.carry_manual(old, new, usuario=self.v.user()["id"], known=self.v.names())
        self.assertEqual((new["hablantes"]["h2"]["persona"], new["hablantes"]["h2"]["origen"]), (ana, "manual"))
        self.assertIsNone(new["hablantes"]["h1"]["persona"])
        self.assertIsNone(new["tu"])  # lo que se quito a mano no vuelve
        self.assertIsNone(new["hablantes"]["h3"]["persona"])  # sin decision manual: lo que diga la identificacion

    def test_persona_olvidada_no_se_hereda(self):
        self.identificar(micro_en=None)
        ana = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})["id"]
        old, new = self.leer(), self.nuevo()
        self.v.forget(ana)
        voices.carry_manual(old, new, known=self.v.names())
        self.assertIsNone(new["hablantes"]["h2"].get("persona"))

    def test_run_voices_de_principio_a_fin(self):
        """Por _run_voices con el subproceso simulado: hereda lo manual y no usa las huellas viejas en memoria."""
        from unittest import mock
        self.usuario()
        self.identificar()
        ana = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})["id"]
        jobs = self.app.jobs
        seen = {}

        def fake_subprocess(job, args, idle_timeout=1800):
            seen["vectors_before"] = dict(jobs.voice_vectors)
            voices.write_sidecar(args[args.index("--out") + 1], self.nuevo())
            return {"huellas": {"h1": list(map(float, unit(4))), "h2": list(map(float, unit(3)))}}
        jobs._voices_subprocess = fake_subprocess
        jobs.auto_summary = lambda rel: None
        job = server.Job("r.wav", "r.wav", "voces", None)
        with mock.patch.object(voices, "models_ready", return_value=True):
            jobs._run_voices(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertNotIn("r.wav", seen["vectors_before"])  # las de la identificacion anterior ya no valen
        side = self.leer()
        self.assertEqual(side["hablantes"]["h2"]["persona"], ana)
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h2"], ("Ana", False))
        self.assertEqual(labels["h1"], ("Tú", True))  # el micro, ahora sobre el hablante bueno

    def test_si_falla_tras_rediarizar_queda_como_estaba(self):
        """Cancelar (o un error) despues de que el subproceso reescriba el sidecar no borra lo dicho a mano."""
        from unittest import mock
        self.identificar(micro_en=None)
        ana = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana"})["id"]
        before = self.leer()
        jobs = self.app.jobs

        def fake_subprocess(job, args, idle_timeout=1800):
            voices.write_sidecar(args[args.index("--out") + 1], self.nuevo())
            raise server.engines.Cancelled()
        jobs._voices_subprocess = fake_subprocess
        jobs.auto_summary = lambda rel: None
        job = server.Job("r.wav", "r.wav", "voces", None)
        with mock.patch.object(voices, "models_ready", return_value=True):
            jobs._run_voices(job)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(self.leer(), before)
        self.assertEqual(self.leer()["hablantes"]["h1"]["persona"], ana)


class DatosDeAntesDelArregloTests(Reunion):
    """Hallazgos del refutador de contexto limpio (07/11/2026) con lo que dejo main en voces.json y en los sidecars."""

    def contaminado(self):
        """Lo que dejaba main al escribir «Ana» con «Recordar» en un «Tú» del usuario: su persona llamada «Ana» y con
        la huella de Ana (e4) junto a la suya (e0)."""
        yo = self.usuario()
        self.v.rename(yo, "Ana")
        self.v.remember(yo, unit(4), 20, "r.wav", RECORDAR)
        return yo

    def test_p0_escribir_ana_con_la_persona_contaminada(self):
        yo = self.contaminado()
        self.assertTrue(next(p for p in self.v.public() if p["id"] == yo)["mezcla"])  # Ajustes lo marca (⚠)
        # h2 es la voz de Ana (e4): la huella dice «usuario», pero su persona esta mezclada → se pregunta
        data = self.identificar(micro_en="h1", huellas=dict(self.HUELLAS, h1=unit(0)))
        self.assertEqual((data["hablantes"]["h2"]["persona"], data["hablantes"]["h2"]["provisional"]), (yo, True))
        self.app.voices_person({"path": "r.wav", "h": "h2", "nombre": "Ana", "recordar": True,
                                "consentimiento": True})
        labels, data = self.etiquetas()
        self.assertEqual((labels["h2"], labels["h1"]), (("Ana", False), ("Tú", True)))
        huellas = self.v.find(yo)["huellas"]  # la de Ana sale de su persona (era la voz de h2) y no entra otra
        self.assertEqual([int(np.argmax(h["vector"])) for h in huellas], [0])
        self.assertFalse(next(p for p in self.v.public() if p["id"] == yo)["mezcla"])
        ana = next(p for p in self.v.personas() if p["nombre"] == "Ana" and not p["es_usuario"])
        self.assertEqual(len(ana["huellas"]), 1)

    def test_p0_presencial_con_la_persona_contaminada_no_dice_tu_sin_preguntar(self):
        yo = self.contaminado()
        data = self.identificar(micro_en=None, huellas=dict(self.HUELLAS, h1=unit(0), h2=unit(4)))
        for h in ("h1", "h2"):
            self.assertEqual((data["hablantes"][h]["persona"], data["hablantes"][h]["provisional"]), (yo, True))
        labels, _ = self.etiquetas()
        self.assertEqual((labels["h1"][0], labels["h2"][0]), ("¿Tú?", "¿Tú?"))
        self.assertEqual(self.app.voices_forget({"id": yo})["personas"], [])  # el remedio: olvidarla

    def test_p0_corregir_limpia_la_huella_ajena_del_usuario(self):
        """Refutador P0 (empate permanente): si la app tomaba a h3 por el usuario porque su persona guardaba esa voz,
        decir quien es (o «No soy yo») quita esa huella de su persona; la suya se queda."""
        yo = self.usuario()
        self.v.remember(yo, unit(5), 20, "r.wav", RECORDAR)  # la voz de h3 guardada como suya (main)
        self.identificar(micro_en=None)
        self.assertEqual(self.etiquetas()[0]["h3"][0], "¿Tú?")  # persona mezclada: pregunta
        self.app.voices_reassign({"path": "r.wav", "h": "h3", "persona": None})  # «No soy yo»
        self.assertEqual([int(np.argmax(h["vector"])) for h in self.v.find(yo)["huellas"]], [0])
        other = self.v.create("Luis")["id"]
        self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Luis"})  # h1 no era el usuario: nada
        self.assertEqual(len(self.v.find(yo)["huellas"]), 1)
        self.assertTrue(self.v.find(other))

    def test_recordar_exige_la_identificacion_que_se_ve(self):
        data = self.identificar(micro_en=None)
        data["creado"] = "2026-11-07T12:00:00"
        voices.write_sidecar(self.sidecar(), data)
        with self.assertRaises(server.ApiError):  # una peticion sin `creado` (interfaz anterior en una ventana vieja)
            self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana", "recordar": True,
                                    "consentimiento": True})
        r = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana", "recordar": True,
                                    "consentimiento": True, "creado": "2026-11-07T12:00:00"})
        self.assertEqual(r["huellas"], 1)

    def test_p1b_corregir_al_nombre_del_usuario_no_da_tu(self):
        self.usuario()
        self.identificar(micro_en=None)
        javi = self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Javi"})["id"]
        self.app.voices_person({"id": javi, "path": "r.wav", "h": "h1", "nombre": "Bernardo", "renombrar": True})
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h1"], ("Bernardo", False))
        self.assertEqual(self.v.find(javi)["nombre"], "Bernardo")

    def sidecar_de_main(self, yo, **extra):
        """Sidecar como lo dejaba main: sin «origen»; h2 del usuario por el micro."""
        data = {"version": 1, "creado": "2026-11-05T10:00:00",
                "tramos": [{"inicio": 0, "fin": 10, "h": "h1"}, {"inicio": 10, "fin": 20, "h": "h2"},
                           {"inicio": 20, "fin": 30, "h": "h1"}, {"inicio": 30, "fin": 40, "h": "h3"}],
                "hablantes": {"h1": {"segundos": 20.0, "persona": None, "score": 0.1, "provisional": False},
                              "h2": {"segundos": 20.0, "persona": yo, "score": None, "provisional": False},
                              "h3": {"segundos": 20.0, "persona": None, "score": None, "provisional": False}},
                "tu": "h2", "micro": {"paso": 0.25, "activo": "0" * 40 + "1" * 40 + "0" * 80}}
        for h, persona in extra.items():
            data["hablantes"][h]["persona"] = persona
        voices.write_sidecar(self.sidecar(), data)
        self.app.jobs.voice_vectors["r.wav"] = {h: list(map(float, v)) for h, v in self.HUELLAS.items()}

    def test_p1c_soy_yo_en_un_sidecar_de_main(self):
        yo = self.usuario()
        self.sidecar_de_main(yo)
        self.app.voices_person({"path": "r.wav", "h": "h1", "es_usuario": True})
        labels, data = self.etiquetas()
        self.assertEqual((labels["h1"], labels["h2"], data["tu"]), (("Tú", True), ("Persona 2", False), "h1"))

    def test_p1d_no_se_funde_a_otra_dentro_del_usuario(self):
        yo = self.usuario()
        ana = self.v.create("Ana")["id"]
        with self.assertRaises(server.ApiError):
            self.app.voices_merge({"id_a": yo, "id_b": ana})
        self.assertEqual((len(self.v.find(yo)["huellas"]), bool(self.v.find(ana))), (1, True))

    def test_p2_1_volver_a_identificar_conserva_nombres_de_main(self):
        from unittest import mock
        yo = self.usuario()
        luis = self.v.create("Luis")["id"]  # sin huellas: en main solo pudo ponerse a mano
        self.sidecar_de_main(yo, h3=luis)
        jobs = self.app.jobs

        def fake_subprocess(job, args, idle_timeout=1800):
            new = voices.read_sidecar(self.sidecar())
            for i in new["hablantes"].values():
                i.update(persona=None, score=None, provisional=False)
            new["creado"], new["tu"] = "2026-11-07T12:00:00", None
            voices.write_sidecar(args[args.index("--out") + 1], new)
            return {"huellas": {h: list(map(float, v)) for h, v in self.HUELLAS.items()}}
        jobs._voices_subprocess, jobs.auto_summary = fake_subprocess, (lambda rel: None)
        job = server.Job("r.wav", "r.wav", "voces", None)
        with mock.patch.object(voices, "models_ready", return_value=True):
            jobs._run_voices(job)
        self.assertEqual(job.status, "done", job.error)
        labels, _ = self.etiquetas()
        self.assertEqual(labels["h3"], ("Luis", False))

    def test_p2_2_no_se_corrige_mientras_se_identifica(self):
        self.identificar(micro_en=None)
        self.app.jobs.list.append(server.Job("r.wav", "r.wav", "voces", None))
        self.app.jobs.list[-1].status = "running"
        n = len(self.v.personas())
        for b in ({"path": "r.wav", "h": "h1", "nombre": "Ana"}, {"path": "r.wav", "h": "h1", "es_usuario": True}):
            with self.assertRaises(server.ApiError):
                self.app.voices_person(b)
        with self.assertRaises(server.ApiError):
            self.app.voices_reassign({"path": "r.wav", "h": "h1", "persona": None})
        self.assertEqual(len(self.v.personas()), n)  # no se crea a nadie

    def test_p2_3_primera_identificacion_cancelada_vuelve_a_solo_el_micro(self):
        from unittest import mock
        micro = {"paso": 0.25, "activo": "1" * 40}
        voices.write_micro(self.sidecar(), micro)
        jobs = self.app.jobs

        def fake_subprocess(job, args, idle_timeout=1800):
            voices.write_sidecar(args[args.index("--out") + 1], {"version": 1, "tramos": [{"inicio": 0, "fin": 10,
                                                                 "h": "h1"}], "hablantes": {"h1": {"segundos": 10}}})
            raise server.engines.Cancelled()
        jobs._voices_subprocess, jobs.auto_summary = fake_subprocess, (lambda rel: None)
        job = server.Job("r.wav", "r.wav", "voces", None)
        with mock.patch.object(voices, "models_ready", return_value=True):
            jobs._run_voices(job)
        self.assertEqual(self.leer(), {"version": 1, "micro": micro})

    def test_p2_4_etiqueta_abierta_sobre_otra_identificacion(self):
        data = self.identificar(micro_en=None)
        data["creado"] = "2026-11-07T12:00:00"
        voices.write_sidecar(self.sidecar(), data)
        n = len(self.v.personas())
        with self.assertRaises(server.ApiError):
            self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana", "creado": "2026-11-07T11:00:00",
                                    "recordar": True, "consentimiento": True})
        with self.assertRaises(server.ApiError):
            self.app.voices_reassign({"path": "r.wav", "h": "h1", "persona": None, "creado": "2026-11-07T11:00:00"})
        self.assertEqual(len(self.v.personas()), n)
        self.app.voices_person({"path": "r.wav", "h": "h1", "nombre": "Ana", "creado": "2026-11-07T12:00:00"})
        self.assertEqual(self.etiquetas()[0]["h1"], ("Ana", False))

    def test_p2_5_linea_de_tiempo_malformada(self):
        tramos = [{"inicio": 0, "fin": 10, "h": "h1"}]
        for micro in ({"paso": "x", "activo": "1" * 40}, {"paso": float("nan"), "activo": "1" * 40},
                      {"paso": 0.25, "activo": "1" * 40, "desfase": "abc"},
                      {"paso": 0.25, "activo": "1" * 40, "desfase": float("inf")},
                      {"paso": -1, "activo": "1" * 40}, {"paso": 0.25, "activo": 7}, {"paso": 0.25, "activo": None}):
            r = voices.mic_ratios(tramos, micro)  # no revienta
            self.assertTrue(r == {} or 0.0 <= r["h1"] <= 1.0, micro)


def node(code):
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class PopoverTests(unittest.TestCase):
    """Lo que manda la etiqueta de un hablante (voces.js, funciones puras)."""

    def run_js(self, code):
        defs = js_defs(read("voces.js"), ["spkNameBody", "spkMeBody", "spkPickBody"])
        return [json.loads(x) for x in node(defs + "\nconst J = x => console.log(JSON.stringify(x));\n" + code)]

    def test_guardar_nombre(self):
        out = self.run_js("""const a = {path: 'r.wav'};
const yo = {id: 'pu', nombre: 'Bernardo', es_usuario: true}, ana = {id: 'pa', nombre: 'Ana', es_usuario: false};
J(spkNameBody(a, 'h2', yo, 'Ana', {recordar: true, consentimiento: true}));
J(spkNameBody(a, 'h1', ana, 'Anna', {corregir: true}));
J(spkNameBody(a, 'h1', ana, 'Luis', {}));
J(spkNameBody(a, 'h1', yo, 'Luis', {corregir: true}));
J(spkNameBody(a, 'h3', null, 'Luis', {}));""")
        self.assertEqual(out[0], {"renombrar": False, "path": "r.wav", "h": "h2", "nombre": "Ana", "recordar": True,
                                  "consentimiento": True})  # sin id: nunca el del usuario
        self.assertEqual((out[1]["id"], out[1]["renombrar"]), ("pa", True))  # corregir una errata, solo si se marca
        self.assertEqual((out[2]["id"], out[2]["renombrar"]), ("pa", False))
        self.assertEqual((out[3].get("id"), out[3]["renombrar"]), (None, False))  # al usuario no se le renombra
        self.assertNotIn("id", out[4])

    def test_soy_yo_no_lleva_el_nombre_de_otra_persona(self):
        out = self.run_js("""const a = {path: 'r.wav'};
J(spkMeBody(a, 'h1', {usuario: 'pu', creado: 'c1'}, false));
J(spkMeBody(a, 'h1', {usuario: null, creado: 'c1'}, true));""")
        self.assertEqual([o.get("nombre") for o in out], [None, None])
        self.assertEqual([(o["es_usuario"], o["recordar"], o["consentimiento"], o["creado"]) for o in out],
                         [(True, False, False, "c1"), (True, True, True, "c1")])

    def test_es_o_fusionar(self):
        out = self.run_js("""const a = {path: 'r.wav'};
J(spkPickBody(a, 'h2', {persona: 'pu', usuario: true}, 'pa'));
J(spkPickBody(a, 'h2', {persona: null, usuario: true}, 'pa'));
J(spkPickBody(a, 'h3', {persona: null}, 'pa'));
J(spkPickBody(a, 'h1', {persona: 'pa', usuario: false}, 'pb'));
J(spkPickBody(a, 'h3', {persona: null}, 'pa', 'c1'));""")
        self.assertEqual([o[0] for o in out], ["/api/voces/reasignar"] * 3 + ["/api/voces/fusionar",
                                                                              "/api/voces/reasignar"])
        self.assertEqual(out[0][1], {"path": "r.wav", "h": "h2", "persona": "pa"})  # «Tú» → es Ana: se reasigna
        self.assertEqual(out[3][1], {"id_a": "pb", "id_b": "pa"})
        self.assertEqual(out[4][1]["creado"], "c1")

    def test_textos(self):
        js = read("voces.js")
        for s in ("No soy yo", "Es la misma persona: solo corrijo el nombre", "spkNameBody(a, h, cur, nombre",
                  "spkMeBody(a, h, s, remember())", "spkPickBody(a, h, i, target, s.creado)", "'Es…'",
                  "!(merge && p.es_usuario)", "persona: null, creado: s.creado", "x.usuario && !x.provisional"):
            self.assertIn(s, js, s)
        self.assertNotIn("cur && !i.provisional ? cur.id", js)  # la decision que renombraba al usuario


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class CopiarTextoTests(unittest.TestCase):
    """§15 del backlog (msg 1160): «Copiar texto» lleva quien habla, como la pantalla."""

    def run_js(self, setup):
        defs = js_defs(read("app.js"), ["paragraphs", "whoFor", "transcriptText"])
        defs += "\n" + js_defs(read("voces.js"), ["spkInfo", "spkLabel"])
        code = "let S = {};\n" + defs + "\n" + setup + "\nconsole.log(JSON.stringify(transcriptText()));"
        return "\n".join(node(code))

    SEGS = ("[{start: 0, end: 1, text: 'Hola.'}, {start: 1, end: 2, text: 'Qué tal.'}, {start: 2, end: 3, "
            "text: 'Bien.'}, {start: 3, end: 4, text: 'Vale.'}, {start: 4, end: 5, text: 'A la vez.'}, "
            "{start: 5, end: 6, text: 'Ruido.'}, {start: 12, end: 13, text: 'Sigo.'}]")

    def test_con_hablantes(self):
        out = self.run_js(f"""S.tx = {{path: 'r.wav', kind: 'srt', segs: {self.SEGS}}};
S.spk = {{path: 'r.wav', exists: true, who: ['h1', 'h1', 'h2', 'h1', 'varios', null, null],
  hablantes: {{h1: {{label: 'Ana'}}, h2: {{label: 'Tú'}}, h3: {{label: '¿Luis?'}}}}}};""")
        self.assertEqual(json.loads(out), "Ana: Hola. Qué tal.\n\nTú: Bien.\n\nAna: Vale.\n\nvarios: A la vez."
                                          "\n\nRuido.\n\nSigo.")

    def test_mismo_hablante_tras_una_pausa_no_repite_el_nombre(self):
        out = self.run_js(f"""S.tx = {{path: 'r.wav', kind: 'srt', segs: {self.SEGS}.slice(5)}};
S.spk = {{path: 'r.wav', exists: true, who: ['h3', 'h3'], hablantes: {{h3: {{label: '¿Luis?'}}}}}};""")
        self.assertEqual(json.loads(out), "¿Luis?: Ruido.\n\nSigo.")

    def test_sin_voces_igual_que_antes(self):
        segs = f"{self.SEGS}"
        for spk in ("null", "{path: 'otra.wav', exists: true, who: ['h1'], hablantes: {}}",
                    "{path: 'r.wav', exists: true, who: ['h1'], hablantes: {}}"):  # otra reunion / longitud distinta
            out = self.run_js(f"S.tx = {{path: 'r.wav', kind: 'srt', segs: {segs}}};\nS.spk = {spk};")
            self.assertEqual(json.loads(out), "Hola. Qué tal. Bien. Vale. A la vez. Ruido.\n\nSigo.")
        out = self.run_js("S.tx = {path: 'r.wav', kind: 'txt', segs: [{start: null, end: null, text: 'a'}]};"
                          "S.spk = {path: 'r.wav', exists: true, who: ['h1'], hablantes: {h1: {label: 'Ana'}}};")
        self.assertEqual(json.loads(out), "a")  # .txt: sin hablantes

    def test_segmento_vacio_sin_dobles_espacios(self):
        out = self.run_js("""S.tx = {path: 'r.wav', kind: 'srt', segs: [{start: 0, end: 1, text: ''},
  {start: 1, end: 2, text: 'Hola.'}]};
S.spk = {path: 'r.wav', exists: true, who: ['h1', 'h1'], hablantes: {h1: {label: 'Ana'}}};""")
        self.assertEqual(json.loads(out), "Ana: Hola.")

    def test_el_boton_usa_transcript_text(self):
        app = read("app.js")
        self.assertIn("a === 'copy-tx') copy(transcriptText(), whoFor(S.tx) ? 'Transcripción copiada (con hablantes)'",
                      app)
        self.assertIn("const who = whoFor(tx);", app)  # pantalla y boton, el mismo casado


if __name__ == "__main__":
    unittest.main()
