# -*- coding: utf-8 -*-
"""msg 1242 (08-nov-2026): «a una persona la cataloga como 10 personas distintas a lo largo de la reunion».

sherpa-onnx parte a cada persona en varios grupos; voices.consolidate los une al terminar (huellas parecidas, breves
al mas parecido, numero de participantes como pista). Medido en reuniones reales con scripts/eval_voces.py; aqui,
sin sherpa-onnx, la logica con huellas sinteticas."""
import json
import os
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
    """Corregir los hablantes (unir, reasignar) mientras la reunion se mueve (B1c: se mueve sola al quedar libre, justo
    cuando se corrigen las voces): la correccion espera al movimiento y va a la ruta nueva, como las notas. Antes se
    escribia en la ruta vieja: la reunion movida no la llevaba y quedaba un sidecar huerfano (revision de la integracion
    del 08/11/2026)."""

    def corregir_mientras_se_mueve(self, funcion, corregir):
        """Mientras `corregir()` esta dentro de voices.<funcion> (entre leer el sidecar y escribirlo: lo que tarde el
        disco u OneDrive), se pide mover la reunion a Dest. Devuelve el sidecar de la reunion movida."""
        self.identificar(micro_en=None)
        os.makedirs(os.path.join(self.root, "Dest"))
        self.app.notes_lock = threading.Lock()
        en_medio, seguir, res, real = threading.Event(), threading.Event(), {}, getattr(voices, funcion)

        def despacio(*a, **k):
            en_medio.set()
            seguir.wait(5)
            return real(*a, **k)

        def hilo():
            try:
                res["ok"] = corregir()
            except Exception as e:  # noqa: BLE001
                res["error"] = repr(e)
        with mock.patch.object(voices, funcion, despacio), mock.patch.object(server, "log"):
            t = threading.Thread(target=hilo)
            t.start()
            self.assertTrue(en_medio.wait(5))
            m = threading.Thread(target=lambda: res.update(movida=self.app._move_now(
                os.path.join(self.root, "r.wav"), os.path.join(self.root, "Dest"))))
            m.start()
            m.join(0.5)
            espero = m.is_alive()
            seguir.set()
            t.join(5)
            m.join(5)
        self.assertTrue(espero, f"el movimiento no espera a la correccion: {res}")
        self.assertNotIn("error", res, res)
        self.assertFalse(os.path.exists(self.sidecar()), "sidecar huerfano en la ruta vieja")
        return voices.read_sidecar(os.path.join(self.root, "Dest", "r.hablantes.json"))

    def test_unir(self):
        side = self.corregir_mientras_se_mueve(
            "join_speakers", lambda: self.app.speakers_join({"path": "r.wav", "h": "h3", "con": "h1"}))
        self.assertEqual(set(side["hablantes"]), {"h1", "h2"})

    def test_reasignar(self):
        ana = self.v.create("Ana")["id"]
        side = self.corregir_mientras_se_mueve(
            "assign", lambda: self.app.voices_reassign({"path": "r.wav", "h": "h3", "persona": ana}))
        self.assertEqual(side["hablantes"]["h3"]["persona"], ana)


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
