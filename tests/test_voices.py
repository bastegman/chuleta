# -*- coding: utf-8 -*-
"""«Quién habla» sin sherpa-onnx: emparejado, casado con el .srt, «Tú», voces.json, sidecar, DPAPI y Store."""
import json
import os
import types
import unittest
from unittest import mock

import numpy as np

from support import FakeJob, TempTree, bare_jobs, engines, server, srt_text

import common  # noqa: E402
import voices  # noqa: E402


def unit(i, dim=voices.DIM):
    v = np.zeros(dim, np.float32)
    v[i] = 1.0
    return v


def mix(a, b, cos_a):
    """Vector con coseno cos_a respecto a `a` (unitario) y el resto sobre `b` (ortogonal)."""
    return a * cos_a + b * float(np.sqrt(1 - cos_a ** 2))


def persona(pid, *vectors, nombre=None):
    return {"id": pid, "nombre": nombre or pid, "huellas": [{"vector": list(map(float, v))} for v in vectors]}


class MatchTests(unittest.TestCase):
    e1, e2, e3 = unit(0), unit(1), unit(2)
    personas = [persona("p1", e1), persona("p2", e2)]

    def test_misma_persona(self):
        m = voices.match(self.e1, self.personas)
        self.assertEqual((m["persona"], m["provisional"]), ("p1", False))
        self.assertAlmostEqual(m["score"], 1.0, places=3)

    def test_zona_dudosa_es_provisional(self):
        m = voices.match(mix(self.e1, self.e3, 0.47), self.personas)
        self.assertEqual((m["persona"], m["provisional"]), ("p1", True))
        m = voices.match(mix(self.e1, self.e3, 0.56), self.personas)
        self.assertEqual((m["persona"], m["provisional"]), ("p1", False))

    def test_por_debajo_de_040_es_nueva(self):
        m = voices.match(mix(self.e1, self.e3, 0.3), self.personas)
        self.assertIsNone(m["persona"])
        self.assertAlmostEqual(m["score"], 0.3, places=2)

    def test_dos_candidatos_cerca_pide_confirmar(self):
        m = voices.match(mix(self.e1, self.e2, 0.72), self.personas)  # 0,72 con p1 y 0,69 con p2
        self.assertEqual((m["persona"], m["provisional"], m["segunda"]), ("p1", True, "p2"))

    def test_varias_huellas_por_persona_y_sin_personas(self):
        p = persona("p1", self.e2, self.e1)  # la segunda huella es la que casa
        self.assertEqual(voices.match(self.e1, [p])["persona"], "p1")
        self.assertIsNone(voices.match(self.e1, [])["persona"])
        self.assertIsNone(voices.match(self.e1, [{"id": "x", "huellas": []}])["persona"])


class AssignSrtTests(unittest.TestCase):
    tramos = [{"inicio": 0, "fin": 10, "h": "h1"}, {"inicio": 10, "fin": 20, "h": "h2"}]

    def test_mayoritario_varios_y_none(self):
        frases = [{"start": 2, "end": 8}, {"start": 7.5, "end": 13}, {"start": 30, "end": 35},
                  {"start": None, "end": None}]
        self.assertEqual(voices.assign_srt(frases, self.tramos), ["h1", "h2", None, None])
        tr = [{"inicio": 0, "fin": 7.5, "h": "h1"}, {"inicio": 8.5, "fin": 16, "h": "h2"}]
        self.assertEqual(voices.assign_srt([{"start": 4, "end": 12}], tr), ["varios"])  # 44 % y 44 %
        self.assertEqual(voices.assign_srt([{"start": 6, "end": 9}], tr), ["h1"])  # 50 % justo
        self.assertEqual(voices.assign_srt([(2, 8)], self.tramos), ["h1"])  # tuplas tambien

    def test_sin_tramos_y_desordenadas(self):
        self.assertEqual(voices.assign_srt([{"start": 1, "end": 2}], []), [None])
        frases = [{"start": 12, "end": 14}, {"start": 1, "end": 3}]  # desordenadas: la salida va alineada
        self.assertEqual(voices.assign_srt(frases, self.tramos), ["h2", "h1"])

    def test_tramo_largo_antes_de_cortos(self):
        tr = [{"inicio": 0, "fin": 100, "h": "h1"}, {"inicio": 5, "fin": 6, "h": "h2"},
              {"inicio": 7, "fin": 8, "h": "h2"}]
        self.assertEqual(voices.assign_srt([{"start": 50, "end": 60}], tr), ["h1"])


class MarkUserTests(unittest.TestCase):
    tramos = [{"inicio": 0, "fin": 10, "h": "h1"}, {"inicio": 10, "fin": 20, "h": "h2"}]

    def test_60_30(self):
        micro = {"paso": 0.25, "activo": "1" * 40 + "0" * 40}  # micro activo los 10 primeros segundos
        self.assertEqual(voices.mark_user(self.tramos, micro), "h1")
        micro = {"paso": 0.25, "activo": "1" * 80}  # activo todo el rato: no discrimina
        self.assertIsNone(voices.mark_user(self.tramos, micro))
        micro = {"paso": 0.25, "activo": "1" * 24 + "0" * 16 + "1" * 12 + "0" * 28}  # 60 % / 30 %
        self.assertEqual(voices.mark_user(self.tramos, micro), "h1")
        micro = {"paso": 0.25, "activo": "1" * 20 + "0" * 60}  # 50 %: no basta
        self.assertIsNone(voices.mark_user(self.tramos, micro))
        self.assertIsNone(voices.mark_user(self.tramos, None))
        self.assertIsNone(voices.mark_user(self.tramos, {"paso": 0.25, "activo": "1" * 40}, skip=("h1",)))

    def test_mic_timeline(self):
        tl = voices.mic_timeline([(0.0, -30, -50), (0.25, -30, -30), (0.5, -50, -80), (1.0, -20, None)])
        self.assertEqual(tl, {"paso": 0.25, "activo": "10001"})
        self.assertEqual(voices.mic_timeline([])["activo"], "")


class VoicesStoreTests(TempTree):
    def store(self):
        return voices.Voices(os.path.join(self.data_dir, "voces.json"))

    def test_crear_recordar_tope_y_persistencia(self):
        v = self.store()
        p = v.create()
        self.assertEqual(p["nombre"], "Persona 1")
        with self.assertRaises(ValueError):
            v.remember(p["id"], unit(0), 30, "r.wav", None)  # sin consentimiento no se guarda
        for i in range(6):
            v.remember(p["id"], unit(i), 30 + i, f"r{i}.wav", "2026-10-30T10:00:00")
        self.assertEqual(len(v.find(p["id"])["huellas"]), voices.MAX_HUELLAS)
        self.assertEqual([h["reunion"] for h in v.find(p["id"])["huellas"]], [f"r{i}.wav" for i in range(1, 6)])
        with open(v.path, encoding="utf-8") as f:
            raw = f.read()
        self.assertTrue(raw.startswith("dpapi:" if common.WINDOWS else "plain:"), raw[:10])
        self.assertTrue(os.path.isfile(v.path + ".bak"))
        again = self.store()
        self.assertEqual([q["nombre"] for q in again.personas()], ["Persona 1"])
        self.assertEqual(again.public()[0]["huellas"], 5)
        self.assertNotIn("vector", json.dumps(again.public()))

    def test_olvidar_y_fusionar_no_dejan_copias_con_huellas(self):
        v = self.store()
        p = v.create("Persona 1")
        v.remember(p["id"], unit(0), 30, "r.wav", "2026-10-30T10:00:00")
        q = v.create("Persona 2")  # segundo guardado: el .bak ya lleva la huella
        self.assertTrue(os.path.isfile(v.path + ".bak"))
        with open(v.path + ".ilegible", "w", encoding="utf-8") as f:
            f.write("x")
        v.forget(p["id"])
        self.assertFalse(os.path.exists(v.path + ".bak"))
        self.assertFalse(os.path.exists(v.path + ".ilegible"))
        r = v.create("Persona 3")
        v.remember(r["id"], unit(1), 20, "s.wav", "2026-10-30T10:00:00")
        self.assertTrue(os.path.isfile(v.path + ".bak"))  # los guardados normales siguen con copia
        v.merge(q["id"], r["id"])
        self.assertFalse(os.path.exists(v.path + ".bak"))
        v.forget_all()
        self.assertFalse(os.path.exists(v.path + ".bak"))
        self.assertEqual(self.store().personas(), [])

    def test_rename_merge_forget(self):
        v = self.store()
        a, b = v.create("Ana"), v.create("Persona X")
        v.remember(a["id"], unit(0), 20, "a.wav", True)
        v.remember(b["id"], unit(1), 20, "b.wav", True)
        v.rename(b["id"], "  Bea   López ")
        self.assertEqual(v.find(b["id"])["nombre"], "Bea López")
        with self.assertRaises(ValueError):
            v.rename(a["id"], "   ")
        self.assertEqual(v.merge(a["id"], b["id"]), b["id"])
        self.assertIsNone(v.find(b["id"]))
        self.assertEqual(len(v.find(a["id"])["huellas"]), 2)
        v.set_user(a["id"])
        self.assertEqual(v.user()["id"], a["id"])
        c = v.create("Yo", es_usuario=True)
        self.assertEqual(v.user()["id"], c["id"])  # solo un usuario
        v.forget(a["id"])
        with self.assertRaises(KeyError):
            v.forget(a["id"])
        self.assertEqual(v.names(), {c["id"]: "Yo"})
        v.forget_all()
        self.assertEqual(self.store().personas(), [])

    def test_fichero_ilegible_se_aparta(self):
        p = os.path.join(self.data_dir, "voces.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("plain:QUJD")  # base64 de "ABC": no es JSON
        v = voices.Voices(p)
        self.assertTrue(v.error)
        self.assertTrue(os.path.isfile(p + ".ilegible"))
        self.assertEqual(v.personas(), [])


class SidecarTests(TempTree):
    def test_sin_vectores_y_micro(self):
        p = os.path.join(self.root, "r.hablantes.json")
        data = {"tramos": [{"inicio": 0, "fin": 1, "h": "h1"}], "hablantes": {"h1": {"segundos": 1, "vector": [1, 2]}},
                "huellas": {"h1": [0.1] * 192}}
        voices.write_sidecar(p, data)
        raw = voices.read_sidecar(p)
        self.assertNotIn("huellas", raw)
        self.assertNotIn("vector", raw["hablantes"]["h1"])
        self.assertEqual(raw["version"], 1)
        voices.write_micro(p, {"paso": 0.25, "activo": "1"})
        raw = voices.read_sidecar(p)
        self.assertEqual((raw["micro"]["activo"], len(raw["tramos"])), ("1", 1))
        self.assertEqual(voices.sidecar_path(os.path.join(self.root, "a", "x.m4a")),
                         os.path.join(self.root, "a", "x.hablantes.json"))

    def test_identify_y_label(self):
        v = voices.Voices(os.path.join(self.data_dir, "voces.json"))
        ana = v.create("Ana")
        v.remember(ana["id"], unit(0), 30, "a.wav", True)
        yo = v.create("Yo", es_usuario=True)
        data = {"tramos": [{"inicio": 0, "fin": 30, "h": "h1"}, {"inicio": 30, "fin": 37, "h": "h2"},
                           {"inicio": 37, "fin": 40, "h": "h3"}, {"inicio": 40, "fin": 60, "h": "h4"}],
                "hablantes": {"h1": {"segundos": 30, "breve": False}, "h2": {"segundos": 7, "breve": False},
                              "h3": {"segundos": 3, "breve": True}, "h4": {"segundos": 20, "breve": False}},
                "micro": {"paso": 1, "activo": "0" * 40 + "1" * 20}}
        huellas = {"h1": list(unit(0)), "h2": list(unit(0)), "h3": list(unit(0)), "h4": list(unit(5))}
        voices.identify(data, huellas, v)
        hs = data["hablantes"]
        self.assertEqual((hs["h1"]["persona"], hs["h1"]["provisional"]), (ana["id"], False))
        self.assertEqual((hs["h2"]["persona"], hs["h2"]["provisional"]), (ana["id"], True))  # < 20 s: provisional
        self.assertIsNone(hs["h3"]["persona"])  # breve: ni se intenta
        self.assertEqual(data["tu"], "h4")
        self.assertEqual(hs["h4"]["persona"], yo["id"])  # el micro ya dice que soy yo
        names = v.names()
        hs_ = ("h1", "h2", "h3", "h4", "varios", None)
        self.assertEqual([voices.label(h, data, names, usuario=yo["id"]) for h in hs_],
                         ["Ana", "¿Ana?", "(breve)", "Tú", "varios", None])
        self.assertEqual(voices.label("h4", data, names, for_llm=True, usuario=yo["id"]), "Yo")
        self.assertEqual(hs["h4"]["origen"], "micro")
        self.assertEqual((hs["h1"]["origen"], hs["h3"].get("origen")), ("huella", None))
        self.assertEqual(voices.label("h7", {"hablantes": {"h7": {}}}, {}), "Persona 7")
        segs = [{"start": 0, "end": 4, "text": "hola"}, {"start": 5, "end": 9, "text": "qué tal"},
                {"start": 31, "end": 35, "text": "bien"}]
        who = voices.refresh_frases(data, segs)
        self.assertEqual(who, ["h1", "h1", "h2"])
        self.assertEqual(data["frases"], [{"inicio": 0.0, "h": "h1"}, {"inicio": 5.0, "h": "h1"},
                                          {"inicio": 31.0, "h": "h2"}])
        txt = voices.export_text(segs, who, data, names)
        self.assertEqual(txt, "[0:00] Ana: hola qué tal\n\n[0:31] ¿Ana?: bien\n")


class DiarizeBlocksTests(unittest.TestCase):
    """diarize() por bloques con sherpa-onnx simulado: los clusteres de bloques distintos se enlazan por huella."""

    def test_enlace_de_bloques(self):
        seg = lambda a, b, k: types.SimpleNamespace(start=a, end=b, speaker=k)  # noqa: E731
        calls = []

        class Result:
            def __init__(self, segs):
                self.segs = segs

            def sort_by_start_time(self):
                return self.segs

        class SD:
            sample_rate = voices.SR

            def process(self, samples, cb=None):
                calls.append(len(samples))
                cb(1, 2)
                cb(2, 2)
                # bloque 1: hablante 0 de 0-4 s, hablante 1 de 4-10 s; bloque 2: hablante 0 (= el 1 de antes) y 1 nuevo
                return Result([seg(0, 4, 0), seg(4, 10, 1)] if len(calls) == 1 else [seg(0, 6, 0), seg(6, 10, 1)])

        vecs = {(1, 0): unit(0), (1, 1): unit(1), (2, 0): unit(1), (2, 1): unit(2)}

        def fake_fp(ex, samples16, tramos, max_s=90.0):
            k = (1, 0) if tramos[0][0] < 4 else (1, 1) if tramos[0][0] < 10 else (2, 0) if tramos[0][0] < 16 else (2, 1)
            return vecs[k], sum(b - a for a, b in tramos)
        samples = np.zeros(20 * voices.SR, np.int16)
        with mock.patch.object(voices, "_sherpa", return_value=None), \
                mock.patch.object(voices, "_diarizer", return_value=SD()), \
                mock.patch.object(voices, "_extractor", return_value=None), \
                mock.patch.object(voices, "models_ready", return_value=True), \
                mock.patch.object(voices, "fingerprint", fake_fp), \
                mock.patch.object(voices, "BLOQUE_S", 10.0):
            progress = []
            res = voices.diarize(samples, "/nonexistent", progress=lambda **k: progress.append(k))
        self.assertEqual(calls, [10 * voices.SR, 10 * voices.SR])
        self.assertEqual(len(res["hablantes"]), 3)  # 4 clusteres locales → 3 personas
        self.assertEqual(res["duracion_s"], 20.0)
        by_h = {t["h"]: [] for t in res["tramos"]}
        for t in res["tramos"]:
            by_h[t["h"]].append((t["inicio"], t["fin"]))
        self.assertEqual(by_h["h1"], [(4.0, 10.0), (10.0, 16.0)])  # el enlazado, el que mas habla
        self.assertEqual(res["hablantes"]["h1"]["segundos"], 12.0)
        self.assertFalse(res["hablantes"]["h1"]["breve"])
        self.assertTrue(res["hablantes"]["h3"]["breve"])  # 4 s
        self.assertEqual(set(res["huellas"]), {"h1", "h2", "h3"})
        self.assertTrue(any(p.get("frac", 0) > 0.5 for p in progress))


class DpapiTests(unittest.TestCase):
    def test_roundtrip_y_prefijo(self):
        t = common.protect(b"secreto \xc3\xb1")
        self.assertTrue(common.is_protected(t))
        self.assertTrue(t.startswith("dpapi:" if common.WINDOWS else "plain:"))
        self.assertEqual(common.unprotect(t), "secreto ñ".encode("utf-8"))
        self.assertEqual(common.unprotect(common.protect("texto")), b"texto")
        self.assertFalse(common.is_protected("sk-abc"))
        with self.assertRaises(ValueError):
            common.unprotect("sk-abc")

    def test_si_dpapi_falla_se_guarda_plain_y_no_rompe_los_ajustes(self):
        with mock.patch.object(common, "WINDOWS", True), \
                mock.patch.object(common, "_dpapi", side_effect=OSError("DPAPI: CryptProtectData fallo (13)")):
            t = common.protect(b"clave")
        self.assertTrue(t.startswith("plain:"))
        self.assertEqual(common.unprotect(t), b"clave")

    @unittest.skipIf(common.WINDOWS, "fuera de Windows")
    def test_dpapi_no_se_descifra_fuera_de_windows(self):
        with self.assertRaises(OSError):
            common.unprotect("dpapi:QUJD")

    @unittest.skipUnless(common.WINDOWS, "solo en Windows")
    def test_dpapi_real(self):
        t = common.protect(b"clave")
        self.assertTrue(t.startswith("dpapi:"))
        self.assertNotIn("clave", t)
        self.assertEqual(common.unprotect(t), b"clave")
        with self.assertRaises(OSError):
            common.unprotect("dpapi:QUJD")


class StoreSecretsTests(TempTree):
    def test_migracion_transparente(self):
        p = os.path.join(self.data_dir, "settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"cloud": {"api_key": "sk-test-1234567890abcdef", "provider": "groq"}}, f)
        st = server.Store(p, server.DEFAULT_SETTINGS)
        self.assertEqual(st.get()["cloud"]["api_key"], "sk-test-1234567890abcdef")
        self.assertEqual(st.lost, [])
        st.update(lambda d: None)
        with open(p, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertTrue(common.is_protected(saved["cloud"]["api_key"]))
        self.assertNotIn("sk-test", json.dumps(saved))
        again = server.Store(p, server.DEFAULT_SETTINGS)
        self.assertEqual(again.get()["cloud"]["api_key"], "sk-test-1234567890abcdef")
        self.assertEqual(server.masked(again.get()["cloud"]["api_key"]), "sk-t…cdef")
        again.update(lambda d: d["cloud"].update(api_key=""))
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cloud"]["api_key"], "")

    def test_si_no_descifra_queda_vacia_y_se_avisa(self):
        p = os.path.join(self.data_dir, "settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"cloud": {"api_key": "dpapi:QUJD"}}, f)
        st = server.Store(p, server.DEFAULT_SETTINGS)
        self.assertEqual(st.get()["cloud"]["api_key"], "")
        self.assertEqual(st.lost, ["cloud.api_key"])


class SideExtsTests(TempTree):
    def app(self):
        app = server.App.__new__(server.App)
        app.jobs = bare_jobs([])
        app.jobs.voice_vectors = {"r.wav": {"h1": [1.0]}}
        app.library = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        app.settings = server.Store(os.path.join(self.data_dir, "settings.json"), server.DEFAULT_SETTINGS)
        app.rec = None
        return app

    def test_mover_y_borrar_llevan_el_sidecar(self):
        self.assertIn(voices.SIDECAR_EXT, server.SIDE_EXTS)
        self.wav("r.wav")
        self.write("r.srt", srt_text([(0.0, 1.0, "hola")]))
        self.write("r.hablantes.json", json.dumps({"version": 1, "tramos": [{"inicio": 0, "fin": 1, "h": "h1"}],
                                                   "hablantes": {"h1": {"segundos": 1}}}))
        self.write("r.hablantes.txt", "hola")
        os.makedirs(os.path.join(self.root, "Dest"))
        app = self.app()
        r = app.move({"path": "r.wav", "folder": "Dest", "name": "nuevo"})
        self.assertEqual(r["path"], "Dest/nuevo.wav")
        for e in (".wav", ".srt", ".hablantes.json", ".hablantes.txt"):
            self.assertTrue(os.path.isfile(os.path.join(self.root, "Dest", "nuevo" + e)), e)
        self.assertIn("Dest/nuevo.wav", app.jobs.voice_vectors)
        app.library.refresh()
        a = app.library.find("Dest/nuevo.wav")
        self.assertTrue(a["has_speakers"])
        self.assertEqual(a["speakers"]["n"], 1)
        tree, _ = app.library.refresh()
        self.assertEqual([d["name"] for d in tree["folders"][0]["docs"]], [])  # el .hablantes.txt no es un documento
        app.delete({"path": "Dest/nuevo.wav"})
        self.assertEqual(os.listdir(os.path.join(self.root, "Dest")), [])
        self.assertNotIn("Dest/nuevo.wav", app.jobs.voice_vectors)

    def test_solo_micro_no_cuenta_como_hablantes(self):
        self.wav("r.wav")
        self.write("r.hablantes.json", json.dumps({"version": 1, "micro": {"paso": 0.25, "activo": "1"}}))
        lib = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        lib.refresh()
        a = lib.find("r.wav")
        self.assertFalse(a["has_speakers"])
        self.assertIsNone(a["speakers"])


class JobsVoicesTests(unittest.TestCase):
    def test_kind_voces_y_auto_voices_apagado(self):
        j = server.Job("a.wav", "a.wav", "voces", None)
        self.assertEqual((j.kind, j.engine, j.then_summary), ("voces", "voces", False))
        jobs = bare_jobs([FakeJob("a.wav", kind="voces", status="running")])
        jobs.settings = mock.Mock()
        jobs.settings.get.return_value = {"voices_enabled": False}
        self.assertFalse(jobs.auto_voices("a.wav"))
        self.assertIs(jobs.active_for("a.wav", kinds=("voces",)), jobs.list[0])
        self.assertIsNone(jobs.active_for("a.wav"))  # un trabajo de voces no bloquea editar ni transcribir
        jobs.settings.get.return_value = {"voices_enabled": True}
        self.assertTrue(jobs.auto_voices("a.wav"))  # ya en marcha: se le encarga el resumen al acabar
        self.assertTrue(jobs.list[0].then_summary)


class SummarizerNamesTests(unittest.TestCase):
    def test_lines_con_hablantes(self):
        segs = [{"start": 0, "text": "hola a todos", "who": "Ana"}, {"start": 3, "text": "seguimos", "who": "Ana"},
                {"start": 65, "text": "gracias", "who": "Persona 3"}, {"start": 70, "text": "fin", "who": None}]
        self.assertEqual(engines.Summarizer.lines(segs),
                         ["[0:00] Ana: hola a todos seguimos", "[1:05] Persona 3: gracias", "[1:10] fin"])
        plain = [{"start": 0, "text": "a"}, {"start": 1, "text": "b"}]
        self.assertEqual(engines.Summarizer.lines(plain), ["[0:00] a b"])


class RecorderLevelsTests(unittest.TestCase):
    def test_note_levels_y_timeline(self):
        from recording import RecorderProcess
        r = RecorderProcess.__new__(RecorderProcess)
        r.path, r.levels = "x.wav", []
        r.note_levels({"t": 0.0, "mic": {"db": -20}, "sys": {"db": -60}})
        r.note_levels({"t": 0.25, "mic": {"db": -20}, "sys": {"db": -60}, "mic_muted": True})  # silenciado: no cuenta
        r.note_levels({"t": 0.5, "sys": {"db": -60}})  # sin micro
        r.note_levels({"done": True})
        self.assertEqual(r.timeline(), {"paso": 0.25, "activo": "1"})
        m = RecorderProcess.__new__(RecorderProcess)
        m.path, m.levels = None, []  # medidor (sin grabar): no se guarda nada
        m.note_levels({"t": 0.0, "mic": {"db": -20}})
        self.assertIsNone(m.timeline())


if __name__ == "__main__":
    unittest.main()
