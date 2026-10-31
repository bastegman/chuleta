# -*- coding: utf-8 -*-
"""Directo en la nube: chunker (pausas, voz continua, silencio, fusion), cupo y ritmo por clave (KeyPool),
rotacion de claves, cabeceras de Groq, dedupe del solape, latencia, traduccion y una sesion entera de live.run con
un reloj falso y una grabacion que crece. Todo con dobles: sin red, sin faster-whisper (la VAD es por energia)."""
import json
import os
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

from support import engines, live, server, wav_bytes, FakeJob  # noqa: F401  (support prepara sys.path)

SR = live.SR


def ts(*spans):
    """Tramos de voz en segundos -> lista de la VAD en muestras."""
    return [{"start": int(a * SR), "end": int(b * SR)} for a, b in spans]


# ----------------------------------------------------------------------------- chunker

class DirectCutTests(unittest.TestCase):
    def buf(self, secs):
        return np.zeros(int(secs * SR), np.float32)

    def test_espera_hasta_tener_3_s_de_voz(self):
        self.assertEqual(live.direct_cut(self.buf(2.5), ts((0, 2.5))), (0, 0))
        self.assertEqual(live.direct_cut(self.buf(5.0), ts((0, 5.0))), (0, 0))  # voz seguida, aun no son 6 s

    def test_corta_en_la_primera_pausa_a_partir_de_3_s(self):
        cut, ov = live.direct_cut(self.buf(5.0), ts((0, 3.2), (3.6, 5.0)))
        self.assertEqual((cut, ov), (int(3.4 * SR), 0))

    def test_una_pausa_antes_de_3_s_no_vale(self):
        self.assertEqual(live.direct_cut(self.buf(4.0), ts((0, 1.5), (1.9, 4.0))), (0, 0))

    def test_sin_pausa_corta_a_6_s_con_solape(self):
        cut, ov = live.direct_cut(self.buf(6.5), ts((0, 6.5)))
        self.assertEqual((cut, ov), (6 * SR, SR // 2))

    def test_el_silencio_inicial_no_cuenta_para_la_ventana(self):
        # 2 s de silencio, voz de 2,0 a 5,5 y 0,7 s de cola: la pausa de la cola esta a 3,85 s del arranque de la voz
        cut, ov = live.direct_cut(self.buf(6.2), ts((2.0, 5.5)))
        self.assertEqual((cut, ov), (int(5.85 * SR), 0))
        # y la ventana maxima tambien se mide desde la voz: 2 s de silencio + 5 s de voz seguida = esperar
        self.assertEqual(live.direct_cut(self.buf(7.0), ts((2.0, 7.0))), (0, 0))
        self.assertEqual(live.direct_cut(self.buf(8.2), ts((2.0, 8.2))), (8 * SR, SR // 2))

    def test_todo_silencio_se_descarta_sin_enviar(self):
        self.assertEqual(live.direct_cut(self.buf(4.0), []), (4 * SR, 0))

    def test_con_retraso_los_trozos_pendientes_se_fusionan(self):
        # el bucle vuelve con 11 s acumulados y tres frases: una sola peticion hasta la ultima pausa
        cut, ov = live.direct_cut(self.buf(11.2), ts((0, 3.5), (3.9, 7.0), (7.4, 10.5)))
        self.assertEqual((cut, ov), (int(10.85 * SR), 0))
        # con solo 0,5 s de cola todavia no cuenta como pausa (hace falta mas de 0,5 s): la ultima pausa real
        self.assertEqual(live.direct_cut(self.buf(11.0), ts((0, 3.5), (3.9, 7.0), (7.4, 10.5)))[0], int(7.2 * SR))

    def test_la_fusion_no_pasa_de_dos_ventanas_maximas(self):
        cut, _ = live.direct_cut(self.buf(20.0), ts((0, 3.5), (3.9, 7.0), (7.4, 12.8), (13.2, 18.8), (19.2, 20.0)))
        self.assertEqual(cut, int(7.2 * SR))  # 13,0 s ya pasa de 2 x 6 s

    def test_la_politica_de_cupo_alarga_la_ventana(self):
        pace = (10, 15)
        self.assertEqual(live.direct_cut(self.buf(8.0), ts((0, 3.5), (3.9, 8.0)), pace), (0, 0))
        cut, ov = live.direct_cut(self.buf(12.0), ts((0, 3.5), (3.9, 11.0)), pace)
        self.assertEqual((cut, ov), (int(11.5 * SR), 0))
        cut, ov = live.direct_cut(self.buf(15.5), ts((0, 15.5)), pace)
        self.assertEqual((cut, ov), (15 * SR, SR // 2))

    def test_choose_cut_de_siempre_no_cambia(self):
        # ponerse al dia (120-300 s) y el directo en local (20-45 s) siguen con choose_cut
        buf = np.zeros(130 * SR, np.float32)
        with mock.patch.object(live, "speech", return_value=ts((0, 100), (101, 130))):
            self.assertEqual(live.choose_cut(buf, False, catching_up=True, cloud=True)[0], int(100.5 * SR))
        with mock.patch.object(live, "speech", return_value=ts((0, 130))):
            self.assertEqual(live.choose_cut(buf, False, catching_up=True, cloud=True)[0], 0)
        with mock.patch.object(live, "speech", return_value=ts((0, 300))):
            self.assertEqual(live.choose_cut(np.zeros(300 * SR, np.float32), False, True, True)[0], 299 * SR)
        self.assertEqual((live.BACKLOG_MIN_S, live.BACKLOG_MAX_S, live.LIVE_MIN_S, live.LIVE_MAX_S), (120, 300, 20, 45))


class TrimDedupeTests(unittest.TestCase):
    def test_solo_voz_con_margen(self):
        w = np.arange(6 * SR, dtype=np.float32)
        audio, a, voice = live.trim_voice(w, ts((1.0, 2.0), (3.0, 5.0)))
        self.assertEqual((a, len(audio)), (int(0.8 * SR), int(4.4 * SR)))
        self.assertEqual(audio[0], w[a])
        self.assertAlmostEqual(voice, 3.0)
        self.assertEqual(live.trim_voice(w, [])[2], 0.0)
        audio, a, _ = live.trim_voice(w, ts((0.05, 5.95)))  # el margen no se sale de la ventana
        self.assertEqual((a, len(audio)), (0, 6 * SR))

    def test_dedupe_del_solape(self):
        segs = [(5.5, 5.9, "ya dicha"), (5.7, 6.4, "cruza el límite"), (6.1, 7.0, "nueva")]
        self.assertEqual(live.dedupe_overlap(segs, 6.0), segs[1:])
        self.assertEqual(live.dedupe_overlap(segs, None), segs)

    def test_stitch_quita_solape_y_ecos(self):
        prev = [(3.0, 5.9, "Vale, perfecto.")]
        new = [(5.5, 5.95, "Vale, perfecto."), (6.0, 7.5, "Vale, perfecto."), (7.5, 9.0, "Seguimos con el plan.")]
        self.assertEqual(live.stitch(prev, new, 6.0, "es"), [(7.5, 9.0, "Seguimos con el plan.")])


class LatencyTests(unittest.TestCase):
    def test_del_final_del_audio_al_texto(self):
        lat = live.Latency()
        for rec, wall in ((3.0, 100.0), (3.5, 100.5), (4.0, 101.0)):
            lat.seen(rec, wall)
        self.assertIsNone(lat.add(9.0, 105.0))  # audio que aun no se ha visto en el fichero
        self.assertAlmostEqual(lat.add(3.7, 102.3), 1.3)
        self.assertAlmostEqual(lat.add(3.5, 101.0), 0.5)
        s = lat.stats()
        self.assertEqual((s["n"], s["p50"], s["p95"], s["last"]), (2, 0.5, 1.3, 0.5))
        self.assertEqual(live.Latency().stats()["n"], 0)

    def test_textos(self):
        self.assertEqual(live.fmt_wait(11520), "3 h 12 min")
        self.assertEqual(live.fmt_wait(1500), "25 min")
        self.assertEqual(live.fmt_wait(40), "40 s")
        msg = live.pause_message({"remaining": 42, "reset_in": 3600, "blocked_for": 0})
        self.assertIn("quedan 42 peticiones de Groq hoy", msg)
        self.assertIn("1 h 00 min", msg)
        self.assertIn("repaso final", msg)
        self.assertIn("Groq pide esperar 2 min", live.pause_message({"blocked_for": 120}))


# ----------------------------------------------------------------------------- cupo y claves

class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def pool(log=None, t=1000.0):
    p = engines.KeyPool(log)
    p.clock = Clock(t)
    return p


CFG2 = {"provider": "groq", "api_key": "gsk_primera_0000000000", "extra_keys": ["gsk_segunda_1111111111"]}
K1, K2 = CFG2["api_key"], CFG2["extra_keys"][0]


class DurationTests(unittest.TestCase):
    def test_formatos_de_groq(self):
        d = engines._duration_s
        self.assertAlmostEqual(d("2m59.56s"), 179.56)
        self.assertAlmostEqual(d("7.66s"), 7.66)
        self.assertAlmostEqual(d("1h2m"), 3720)
        self.assertAlmostEqual(d("750ms"), 0.75)
        self.assertAlmostEqual(d("120"), 120)
        self.assertEqual(d(""), 0.0)
        self.assertEqual(d(None), 0.0)


class KeyPoolTests(unittest.TestCase):
    def test_claves_sin_vacias_ni_repetidas(self):
        cfg = {"api_key": " a ", "extra_keys": ["", "a", "b", None, " b"]}
        self.assertEqual(engines.cloud_keys(cfg), ["a", "b"])
        self.assertEqual(engines.cloud_keys({"api_key": ""}), [])
        with self.assertRaises(engines.EngineError):
            pool().pick({"api_key": ""}, "whisper")

    def test_cabeceras_y_rotacion_por_pocas_peticiones(self):
        lines = []
        p = pool(lines.append)
        self.assertEqual(p.pick(CFG2, "whisper"), K1)
        st = p.note(K1, "whisper", 200, {"X-RateLimit-Limit-Requests": "2000", "x-ratelimit-remaining-requests": "19",
                                         "x-ratelimit-reset-requests": "2m59.56s",
                                         "x-ratelimit-limit-audio-seconds": "7200"})
        self.assertEqual((st["remaining"], st["limit"]), (19, 2000))
        self.assertAlmostEqual(st["reset_at"], 1000 + 179.56)
        self.assertEqual(p.pick(CFG2, "whisper"), K2)  # por debajo de 20: siguiente clave
        self.assertEqual(p.pick(CFG2, "chat"), K1)     # el cupo de chat es otro
        self.assertTrue(any("cabeceras de cupo" in l and "audio-seconds" in l for l in lines))
        self.assertEqual(sum("cabeceras de cupo" in l for l in lines), 1)
        p.note(K2, "whisper", 200, {"x-ratelimit-remaining-requests": "1500"})
        self.assertEqual(sum("cabeceras de cupo" in l for l in lines), 1)  # solo la primera vez
        self.assertTrue(any("clave 1/2" in l and "clave 2/2" in l for l in lines))
        for l in lines:  # nunca la clave en el log
            self.assertNotIn(K1, l)
            self.assertNotIn(K2, l)

    def test_rotacion_por_429_largo_y_no_por_429_corto(self):
        p = pool()
        p.pick(CFG2, "whisper")
        p.note(K1, "whisper", 429, {"retry-after": "30"})
        self.assertFalse(p.blocked(K1, "whisper"))
        self.assertEqual(p.pick(CFG2, "whisper"), K1)
        p.note(K1, "whisper", 429, {"retry-after": "120"})
        self.assertTrue(p.blocked(K1, "whisper"))
        self.assertEqual(p.pick(CFG2, "whisper"), K2)
        p.clock.t += 121
        self.assertFalse(p.blocked(K1, "whisper"))
        self.assertEqual(p.pick(CFG2, "whisper"), K2)  # la activa sigue sirviendo: no se vuelve a saltar

    def test_todas_agotadas_elige_la_que_antes_se_libera(self):
        p = pool()
        p.note(K1, "whisper", 200, {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2h"})
        p.note(K2, "whisper", 200, {"x-ratelimit-remaining-requests": "3", "x-ratelimit-reset-requests": "30m"})
        self.assertEqual(p.pick(CFG2, "whisper"), K2)
        p.clock.t += 1801  # el dia de la segunda se renovo: se olvida lo gastado
        self.assertEqual(p.pick(CFG2, "whisper"), K2)
        self.assertIsNone(p.public(CFG2)["remaining"])

    def test_ritmo_por_clave(self):
        p = pool()
        self.assertEqual(p.reserve(K1, "whisper"), 0.0)
        self.assertEqual(p.reserve(K1, "whisper"), 3.0)
        p.clock.t += 1
        self.assertEqual(p.reserve(K1, "whisper"), 5.0)
        self.assertEqual(p.reserve(K2, "whisper"), 0.0)  # otra clave, otro ritmo
        self.assertEqual(p.reserve(K1, "chat"), 0.0)
        self.assertEqual(p.reserve(K1, "chat"), 2.0)

    def test_ventana_del_directo_segun_lo_que_queda(self):
        p = pool()
        cfg = {"provider": "groq", "api_key": K1}
        self.assertEqual(p.live_pace(cfg), (3, 6))
        for rem, want in ((1000, (3, 6)), (600, (3, 6)), (599, (6, 10)), (300, (6, 10)), (299, (10, 15)),
                          (60, (10, 15)), (59, None), (0, None)):
            p.note(K1, "whisper", 200, {"x-ratelimit-remaining-requests": str(rem)})
            self.assertEqual(p.live_pace(cfg), want, rem)
        # con una segunda clave fresca el directo no se pausa: se pasa a ella
        self.assertEqual(p.live_pace(CFG2), (3, 6))
        self.assertEqual(p.pick(CFG2, "whisper"), K2)
        # un 429 largo tambien pausa si no hay otra
        q = pool()
        q.note(K1, "whisper", 429, {"retry-after": "600"})
        self.assertIsNone(q.live_pace(cfg))
        self.assertAlmostEqual(q.public(cfg)["blocked_for"], 600)

    def test_sin_cupo_diario_siempre_ventana_corta(self):
        p = pool()
        cfg = {"provider": "openai", "api_key": "sk-x"}
        p.note("sk-x", "whisper", 200, {"x-ratelimit-remaining-requests": "5"})
        self.assertEqual(p.live_pace(cfg), (3, 6))
        self.assertFalse(p.public(cfg)["daily"])

    def test_public_no_lleva_claves(self):
        p = pool()
        p.pick(CFG2, "whisper")
        p.note(K1, "whisper", 200, {"x-ratelimit-remaining-requests": "1200", "x-ratelimit-reset-requests": "1h"})
        pub = p.public(CFG2)
        self.assertEqual((pub["keys"], pub["active"], pub["remaining"], pub["others_unknown"]), (2, 1, 1200, 1))
        self.assertAlmostEqual(pub["reset_in"], 3600)
        self.assertNotIn(K1, json.dumps(pub))
        self.assertNotIn(K2, json.dumps(pub))


class FakeResponse:
    def __init__(self, status, headers=None, body=None):
        self.status_code, self.headers = status, headers or {}
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeRequests(types.ModuleType):
    """Sustituto de `requests` (engines lo importa dentro de cada funcion): devuelve respuestas de una cola."""

    class RequestException(Exception):
        pass

    def __init__(self, responses):
        super().__init__("requests")
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return self.responses.pop(0)


class CloudPostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notas-directo-")
        self.chunk = os.path.join(self.tmp, "t.wav")
        with open(self.chunk, "wb") as f:
            f.write(wav_bytes(0.2, rate=16000, channels=1))

    def _run(self, responses, cfg, fn):
        fake = FakeRequests(responses)
        with mock.patch.dict(sys.modules, {"requests": fake}):
            with mock.patch.object(engines.time, "sleep", lambda s: None):
                out = fn()
        return fake, out

    def test_429_largo_pasa_a_la_siguiente_clave_sin_esperar(self):
        eng = engines.CloudEngine(self.tmp)
        eng.keys.clock = Clock()
        ok = {"text": "hola", "segments": [{"start": 0, "end": 1, "text": "hola"}], "language": "spanish"}
        fake, res = self._run([FakeResponse(429, {"retry-after": "3600"}, {"error": {"message": "daily limit"}}),
                               FakeResponse(200, {"x-ratelimit-remaining-requests": "1999"}, ok)],
                              CFG2, lambda: eng._post(CFG2, self.chunk, None, None, lambda **k: None, lambda: False))
        self.assertEqual(res["text"], "hola")
        auths = [kw["headers"]["Authorization"] for _, kw in fake.calls]
        self.assertEqual(auths, [f"Bearer {K1}", f"Bearer {K2}"])
        self.assertTrue(eng.keys.blocked(K1, "whisper"))
        self.assertEqual(eng.keys.public(CFG2)["active"], 2)

    def test_el_directo_usa_su_modelo_y_la_cola_el_de_siempre(self):
        eng = engines.CloudEngine(self.tmp)
        eng.keys.clock = Clock()
        cfg = {"provider": "groq", "api_key": K1, "model": "whisper-large-v3"}
        ok = {"text": "ok", "segments": [], "language": "spanish"}
        fake, _ = self._run([FakeResponse(200, {}, ok), FakeResponse(200, {}, ok)], cfg, lambda: (
            eng._post(cfg, self.chunk, None, None, lambda **k: None, lambda: False,
                      model=engines.CloudEngine.live_model(cfg)),
            eng._post(cfg, self.chunk, None, None, lambda **k: None, lambda: False)))
        self.assertEqual([kw["data"]["model"] for _, kw in fake.calls], ["whisper-large-v3-turbo", "whisper-large-v3"])
        self.assertEqual(engines.CloudEngine.live_model({"provider": "groq", "live_model": " whisper-large-v3 "}),
                         "whisper-large-v3")
        self.assertIsNone(engines.CloudEngine.live_model({"provider": "azure", "live_model": "x"}))
        self.assertIsNone(engines.CloudEngine.live_model({"provider": "openai"}))

    def test_traduccion_con_gpt_oss_20b(self):
        s = engines.Summarizer()
        s.keys.clock = Clock()
        body = {"choices": [{"message": {"content": " Buenos días a todos. "}}]}
        fake, out = self._run([FakeResponse(200, {"x-ratelimit-remaining-tokens": "7000"}, body)], CFG2,
                              lambda: s.translate(CFG2, "Bom dia a todos.", lambda **k: None, lambda: False))
        self.assertEqual(out, ("Buenos días a todos.", "openai/gpt-oss-20b"))
        url, kw = fake.calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual((kw["json"]["model"], kw["json"]["temperature"], kw["json"]["reasoning_effort"]),
                         ("openai/gpt-oss-20b", 0.1, "low"))
        self.assertIn("Conserva nombres propios", kw["json"]["messages"][0]["content"])
        self.assertEqual(kw["json"]["messages"][1]["content"], "Bom dia a todos.")
        self.assertEqual(s.keys.public(CFG2, "chat")["used"], 1)
        self.assertEqual(s.translate(CFG2, "  ", lambda **k: None, lambda: False)[0], "")


# ----------------------------------------------------------------------------- ajustes: claves adicionales

class StoreSecretsTests(unittest.TestCase):
    def test_lista_de_claves_protegida_en_disco_y_legible_en_memoria(self):
        tmp = tempfile.mkdtemp(prefix="notas-store-")
        path = os.path.join(tmp, "settings.json")
        st = server.Store(path, server.DEFAULT_SETTINGS)
        st.update(lambda d: d["cloud"].update(api_key="gsk_main_000000000",
                                              extra_keys=["gsk_a_000000000", "gsk_b_000000000"]))
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("gsk_a_000000000", raw)
        self.assertNotIn("gsk_main_000000000", raw)
        self.assertEqual(json.loads(raw)["cloud"]["extra_keys"][0][:6], "plain:")
        again = server.Store(path, server.DEFAULT_SETTINGS)
        self.assertEqual(again.get()["cloud"]["extra_keys"], ["gsk_a_000000000", "gsk_b_000000000"])
        self.assertEqual(again.get()["cloud"]["api_key"], "gsk_main_000000000")
        # una entrada que no se puede descifrar se descarta y se anota en lost
        data = json.loads(raw)
        data["cloud"]["extra_keys"][1] = "dpapi:AAAA"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        broken = server.Store(path, server.DEFAULT_SETTINGS)
        if not server.WINDOWS:
            self.assertEqual(broken.get()["cloud"]["extra_keys"], ["gsk_a_000000000"])
            self.assertEqual(broken.lost, ["cloud.extra_keys"])


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="notas-settings-")
        self.app = server.App.__new__(server.App)
        self.app.settings = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
        self.app.config = lambda q: {}
        self.app.settings.update(lambda d: d["cloud"].update(api_key="gsk_main_000000000"))

    def keys(self):
        return self.app.settings.get()["cloud"]["extra_keys"]

    def test_altas_bajas_y_enmascaradas(self):
        self.app.save_settings({"cloud": {"extra_keys": [" gsk_a_000000000 ", "gsk_b_000000000", ""]}})
        self.assertEqual(self.keys(), ["gsk_a_000000000", "gsk_b_000000000"])
        m = [server.masked(k) for k in self.keys()]
        self.assertEqual(m[0], "gsk_…0000")
        # la interfaz devuelve las guardadas enmascaradas (se conservan), quita una y anade otra
        self.app.save_settings({"cloud": {"extra_keys": [m[1], "gsk_c_000000000", "gsk_…zzzz"]}})
        self.assertEqual(self.keys(), ["gsk_b_000000000", "gsk_c_000000000"])
        self.app.save_settings({"cloud": {"extra_keys": []}})
        self.assertEqual(self.keys(), [])
        self.app.save_settings({"cloud": {"live_model": " whisper-large-v3 "}})
        self.assertEqual(self.app.settings.get()["cloud"]["live_model"], "whisper-large-v3")
        self.assertEqual(server.DEFAULT_SETTINGS["cloud"]["live_model"], "whisper-large-v3-turbo")

    def test_config_enmascara_las_claves_adicionales(self):
        self.app.save_settings({"cloud": {"extra_keys": ["gsk_a_000000000"]}})
        st = self.app.settings.get()
        jobs = types.SimpleNamespace(cloud=engines.CloudEngine(tempfile.mkdtemp()), summary_ready=lambda: True)
        self.app.jobs = jobs
        self.app.hotkey = types.SimpleNamespace(ok=False, error=None)
        with mock.patch.object(server, "ROOT", tempfile.mkdtemp()):
            cfg = server.App.config(self.app, {})
        self.assertEqual(cfg["cloud"]["extra_keys"], ["gsk_…0000"])
        self.assertEqual(cfg["cloud"]["api_key"], server.masked(st["cloud"]["api_key"]))
        self.assertEqual(cfg["cloud"]["quota"]["keys"], 2)
        self.assertNotIn("gsk_a_000000000", json.dumps(cfg))


class LiveTranslateApiTests(unittest.TestCase):
    def test_enciende_y_apaga_por_reunion(self):
        job = server.Job("a.wav", "a.wav", "nube", None)
        job.live, job.status = True, "running"
        jobs = server.Jobs.__new__(server.Jobs)
        jobs.list, jobs.lock = [job], __import__("threading").Lock()
        app = server.App.__new__(server.App)
        app.jobs = jobs
        self.assertFalse(job.translate)
        out = app.live_translate({"id": job.id, "on": True})
        self.assertTrue(job.translate)
        self.assertTrue(out["live"])
        self.assertIn("directo", out)
        app.live_translate({"id": job.id, "on": False})
        self.assertFalse(job.translate)
        with self.assertRaises(server.ApiError):
            app.live_translate({"id": "nadie", "on": True})
        with open(server.__file__, encoding="utf-8") as f:
            self.assertIn("/api/live/traducir", f.read())


# ----------------------------------------------------------------------------- traduccion del directo

class DirectoJob(FakeJob):
    def __init__(self, **kw):
        self.stages = []
        super().__init__(**kw)
        self.translate, self.translation, self.directo = False, [], None

    @property
    def stage(self):
        return self.stages[-1] if self.stages else ""

    @stage.setter
    def stage(self, v):
        self.stages.append(v)


class FakeSummarizer:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def translate(self, cfg, text, report, cancelled, model=None):
        self.calls.append(text)
        if self.fail:
            raise engines.EngineError("sin cupo")
        return "ES: " + text, "openai/gpt-oss-20b"


class LiveTranslatorTests(unittest.TestCase):
    def test_traduce_lo_pendiente_y_reinicia_con_la_generacion(self):
        job = DirectoJob(preset="nube")
        job.translate = True
        state = {"gen": 0, "segs": [(0.0, 2.0, "Bom dia."), (2.0, 4.0, "Vamos começar.")]}
        summ = FakeSummarizer()
        tr = live.LiveTranslator(job, {"summarizer": summ, "cloud_cfg": CFG2},
                                 lambda: (state["gen"], list(state["segs"])), lambda m: None)
        self.assertTrue(tr.step())
        self.assertEqual(job.translation, [{"start": 0.0, "end": 4.0, "text": "ES: Bom dia. Vamos começar."}])
        self.assertFalse(tr.step())  # nada nuevo
        state["segs"].append((4.0, 5.0, "Obrigado."))
        self.assertTrue(tr.step())
        self.assertEqual(len(job.translation), 2)
        state.update(gen=1, segs=[(0.0, 1.0, "Olá.")])  # rehacer desde el principio
        self.assertTrue(tr.step())
        self.assertEqual([b["text"] for b in job.translation], ["ES: Olá."])
        job.translate = False
        state["segs"].append((1.0, 2.0, "x"))
        self.assertFalse(tr.step())

    def test_trozos_largos_se_parten_y_los_errores_no_tumban(self):
        job = DirectoJob(preset="nube")
        job.translate = True
        segs = [(i, i + 1, "palabra " * 60) for i in range(6)]  # ~480 caracteres cada uno
        summ = FakeSummarizer()
        tr = live.LiveTranslator(job, {"summarizer": summ, "cloud_cfg": CFG2}, lambda: (0, segs), lambda m: None)
        self.assertTrue(tr.step())
        self.assertLessEqual(len(summ.calls[0]), live.TRANSLATE_MAX_CHARS + 10)
        self.assertEqual(tr.done, 3)
        bad = live.LiveTranslator(job, {"summarizer": FakeSummarizer(fail=True), "cloud_cfg": CFG2}, lambda: (0, segs),
                                  lambda m: None)
        self.assertFalse(bad.step())
        self.assertEqual(bad.error, "sin cupo")


# ----------------------------------------------------------------------------- una sesion entera con reloj falso

def energy_vad(buf, hop=320, thresh=0.02, gap_frames=15):
    """VAD por energia (20 ms): sustituye a Silero en los tests. Une tramos separados por < 0,3 s."""
    n = len(buf) // hop
    if not n:
        return []
    rms = np.sqrt((buf[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    on = rms > thresh
    out = []
    i = 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if out and i - out[-1]["end"] // hop < gap_frames:
                out[-1]["end"] = j * hop
            else:
                out.append({"start": i * hop, "end": j * hop})
            i = j
        else:
            i += 1
    return out


def synth(spans, total):
    """Audio de prueba: "voz" (ruido a 0,2) en esos tramos (s) y silencio en el resto; total en segundos."""
    y = np.zeros(int(total * SR), np.float32)
    rng = np.random.default_rng(1)
    for a, b in spans:
        y[int(a * SR):int(b * SR)] = rng.standard_normal(int(b * SR) - int(a * SR)).astype(np.float32) * 0.2
    return y


class FakeRecorder:
    """Reloj falso + "OBS": cada vez que el directo duerme, el reloj avanza y la grabacion crece en el fichero
    (cabecera abierta hasta que se escribe el ultimo byte)."""

    def __init__(self, path, y):
        self.path, self.t, self.t0 = path, 1000.0, 1000.0
        self.pcm = (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()
        self.written, self.closed = 0, False
        with open(path, "wb") as f:
            f.write(wav_bytes(0, open_header=True, rate=SR, channels=1, data=b""))

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt
        want = min(len(self.pcm), int((self.t - self.t0) * SR) * 2)
        if want > self.written:
            with open(self.path, "ab") as f:
                f.write(self.pcm[self.written:want])
            self.written = want
        if self.written >= len(self.pcm) and not self.closed:
            self.closed = True
            with open(self.path, "r+b") as f:
                f.seek(4)
                f.write(struct.pack("<I", 36 + len(self.pcm)))
                f.seek(40)
                f.write(struct.pack("<I", len(self.pcm)))


class FakeCloud:
    """CloudEngine de mentira: cada trozo tarda `latency` s de reloj falso y devuelve una frase con su duracion."""

    def __init__(self, rec, latency=0.8, remaining=None):
        self.rec, self.latency, self.calls = rec, latency, []
        self.keys = engines.KeyPool()
        self.keys.clock = rec.time
        if remaining is not None:
            self.keys.note("k1", "whisper", 200,
                           {"x-ratelimit-remaining-requests": str(remaining), "x-ratelimit-reset-requests": "5h"})

    def transcribe_array(self, cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, context=None,
                         model=None):
        secs = len(audio) / SR
        self.calls.append({"secs": secs, "model": model, "at": self.rec.time()})
        self.rec.sleep(self.latency)
        return [(0.0, secs, f"trozo {len(self.calls)} de {secs:.1f} s")], "spanish"


class RunDirectoTests(unittest.TestCase):
    def run_live(self, spans, total, latency=0.8, remaining=None, translate=False):
        tmp = tempfile.mkdtemp(prefix="notas-run-")
        path = os.path.join(tmp, "reunion.wav")
        rec = FakeRecorder(path, synth(spans, total))
        cloud = FakeCloud(rec, latency, remaining)
        job = DirectoJob(preset="nube")
        job.translate = translate
        summ = FakeSummarizer()
        opts = {"preset": "nube", "language": None, "prompt": None, "threads": 1, "worker": None, "cloud": cloud,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "tmp_dir": tmp, "resume": [], "summarizer": summ}
        writes, logs = [], []
        fake_time = types.SimpleNamespace(time=rec.time, sleep=rec.sleep)
        with mock.patch.object(live, "time", fake_time), mock.patch.object(live, "speech", energy_vad):
            rec.sleep(0.6)  # ya hay algo grabado cuando arranca el directo
            segs, lang = live.run(job, path, opts, lambda s: writes.append(list(s)), logs.append)
        return segs, lang, cloud, job, writes, logs, summ

    def test_trozos_cortos_en_las_pausas_y_latencia_medida(self):
        # frases de 3-4 s con pausas de 0,5-0,7 s, luego 10 s seguidos (corte forzado con solape), luego silencio
        spans = [(0.0, 3.4), (4.0, 7.6), (8.2, 11.3), (11.9, 21.9), (22.6, 24.0)]
        segs, lang, cloud, job, writes, logs, _ = self.run_live(spans, 26.0)
        self.assertEqual(lang, "es")
        secs = [c["secs"] for c in cloud.calls]
        self.assertGreaterEqual(len(secs), 5, secs)
        self.assertTrue(all(s <= 6.5 for s in secs[:-1]), secs)  # ventanas de 3-6 s (+ margenes)
        self.assertTrue(all(c["model"] == "whisper-large-v3-turbo" for c in cloud.calls))
        # el primer trozo sale en cuanto se detecta la pausa tras los 3 s de voz, no a los 8 s de antes
        self.assertLess(cloud.calls[0]["at"] - 1000.0, 5.5)
        # frases en orden y sin solapes (el dedupe por tiempos quita lo repetido del corte forzado)
        starts = [a for a, _, _ in segs]
        self.assertEqual(starts, sorted(starts))
        for (a1, b1, _), (a2, b2, _) in zip(segs, segs[1:]):
            self.assertGreaterEqual(a2, b1 - 0.6)
        d = job.directo
        self.assertTrue(d["cloud"])
        self.assertGreaterEqual(d["latency"]["n"], 4)
        self.assertLess(d["latency"]["p50"], 2.5)  # 0,8 s de API + el sondeo de 0,5 s + la pausa de la VAD
        self.assertEqual(d["quota"]["keys"], 1)
        self.assertTrue(any("texto a los" in l for l in logs))
        self.assertEqual(len(writes), len(segs))
        self.assertAlmostEqual(job.duration, 26.0, places=1)

    def test_el_silencio_no_se_envia(self):
        segs, _, cloud, job, _, _, _ = self.run_live([(0.0, 3.5), (12.0, 15.5)], 20.0)
        self.assertEqual(len(cloud.calls), 2)  # 8,5 s de silencio en medio: ni una peticion
        self.assertTrue(all(c["secs"] <= 4.0 for c in cloud.calls), [c["secs"] for c in cloud.calls])
        self.assertEqual(len(segs), 2)

    def test_sin_cupo_se_pausa_y_al_terminar_transcribe_lo_que_falta(self):
        spans = [(0.0, 3.4), (4.0, 7.6), (8.2, 11.3)]
        segs, _, cloud, job, _, logs, _ = self.run_live(spans, 13.0, remaining=40)
        self.assertTrue(any("Directo en pausa" in l and "quedan 40 peticiones" in l for l in logs))
        self.assertTrue(any("Directo en pausa" in s for s in job.stages))
        self.assertEqual(len(cloud.calls), 1)  # todo en un trozo al cerrar la grabacion (ponerse al dia / final)
        self.assertGreater(cloud.calls[0]["at"], 1000.0 + 12.5)
        self.assertEqual(len(segs), 1)
        self.assertTrue(job.directo["paused"] is False)

    def test_traduccion_al_final_de_la_sesion(self):
        segs, _, cloud, job, _, _, summ = self.run_live([(0.0, 3.4), (4.0, 7.0)], 9.0, translate=True)
        self.assertGreaterEqual(len(segs), 1)
        self.assertGreaterEqual(len(job.translation), 1)
        self.assertTrue(job.translation[-1]["text"].startswith("ES: "))
        self.assertTrue(job.directo["translate"])
        self.assertEqual(job.directo["translation_n"], len(job.translation))


class PublicStateTests(unittest.TestCase):
    def test_job_public_lleva_el_estado_del_directo(self):
        job = server.Job("a.wav", "a.wav", "nube", None)
        job.live = True
        job.directo = {"paused": True}
        self.assertEqual(job.public()["directo"], {"paused": True})
        self.assertEqual(server.Job("b.wav", "b.wav", "rapido", None).public().get("directo"), None)


if __name__ == "__main__":
    unittest.main()
