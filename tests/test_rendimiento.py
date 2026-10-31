# -*- coding: utf-8 -*-
"""Rendimiento: sesiones HTTP con keep-alive, el proceso de Whisper que sigue cargado, la precarga al arrancar, la
cache del arbol de la biblioteca y la latencia del directo por fases. Todo con dobles, sin red ni faster-whisper."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from support import TempTree, FakeJob, bare_jobs, engines, live, server, wav_bytes  # noqa: E402

import requests  # noqa: E402  (requirements-dev: la app lo usa de verdad; aqui se le monta un adaptador falso)

CFG = {"provider": "groq", "api_key": "gsk_test_1234567890"}


class FakeAdapter(requests.adapters.BaseAdapter):
    """Adaptador montado en la sesion del hilo: cuenta las peticiones que pasan por ella y guarda sus timeouts."""

    def __init__(self, body=None, headers=None):
        super().__init__()
        self.calls, self.body, self.headers = [], body or {"text": "hola", "segments": []}, headers or {}

    def send(self, request, **kw):
        self.calls.append((request.url, kw.get("timeout")))
        r = requests.Response()
        r.status_code, r._content = 200, json.dumps(self.body).encode("utf-8")
        r.headers.update({"x-ratelimit-remaining-requests": "1000", **self.headers})
        r.request = request
        return r

    def close(self):
        pass


class HttpSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notas-rend-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.chunk = os.path.join(self.tmp, "t.wav")
        with open(self.chunk, "wb") as f:
            f.write(wav_bytes(0.2, rate=16000, channels=1))

    def test_una_sesion_por_hilo_sin_reintentos_en_el_adaptador(self):
        http = engines.Http()
        s1, s2 = http.session(), http.session()
        self.assertIs(s1, s2)
        self.assertIsInstance(s1, requests.Session)
        other = []
        t = threading.Thread(target=lambda: other.append(http.session()))
        t.start()
        t.join()
        self.assertIsNot(other[0], s1)  # Session no es segura entre hilos: cada uno la suya
        ad = s1.get_adapter("https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertEqual(ad.max_retries.total, 0)  # los reintentos los deciden _post/_chat con el KeyPool
        self.assertEqual(ad._pool_maxsize, engines.Http.POOL)

    def test_post_del_directo_reutiliza_la_sesion_y_lleva_timeouts_por_fase(self):
        ad = FakeAdapter(headers={"openai-processing-ms": "812"})
        with mock.patch.object(engines, "HTTP", engines.Http()):
            engines.HTTP.session().mount("https://", ad)
            eng = engines.CloudEngine(self.tmp)
            clock = [1000.0]
            eng.keys.clock = lambda: clock[0]
            tm = {}
            res = eng._post(CFG, self.chunk, "es", None, lambda **k: None, lambda: False, timeout=60, timing=tm)
            clock[0] += 10
            eng._post(CFG, self.chunk, "es", None, lambda **k: None, lambda: False, timeout=60)
        self.assertEqual(res["text"], "hola")
        self.assertEqual(len(ad.calls), 2)  # las dos por el mismo adaptador = misma sesion (keep-alive)
        self.assertEqual(ad.calls[0][1], (engines.CONNECT_TIMEOUT, 60))
        self.assertEqual(tm["wait"], 0.0)
        self.assertGreaterEqual(tm["net"], 0.0)
        self.assertAlmostEqual(tm["infer"], 0.812)  # lo declara el servidor (openai-processing-ms)

    def test_chat_del_resumen_y_la_traduccion_van_por_la_misma_sesion(self):
        ad = FakeAdapter(body={"choices": [{"message": {"content": "Buenos días."}}]})
        with mock.patch.object(engines, "HTTP", engines.Http()):
            engines.HTTP.session().mount("https://", ad)
            summ = engines.Summarizer()
            out, model = summ.translate(CFG, "Bom dia.", lambda **k: None, lambda: False)
            summ.keys.clock = lambda: time.time() + 100  # otro hueco de ritmo sin esperar
            summ._chat(CFG, "sistema", "usuario", 50, lambda **k: None, lambda: False)
        self.assertEqual(out, "Buenos días.")
        self.assertEqual(len(ad.calls), 2)
        self.assertEqual(ad.calls[0][1], (engines.CONNECT_TIMEOUT, 60))   # traduccion: 60 s de lectura
        self.assertEqual(ad.calls[1][1], (engines.CONNECT_TIMEOUT, 180))  # resumen: 180 s

    def test_sin_session_en_requests_se_usa_post_a_secas(self):
        """Los dobles de `requests` de los otros tests solo tienen post: la app no debe exigir Session."""
        calls = []
        fake = types.SimpleNamespace(post=lambda url, **kw: calls.append(kw) or None)
        with mock.patch.dict(sys.modules, {"requests": fake}):
            engines.Http().post("https://x/y", 7, json={"a": 1})
        self.assertEqual(calls[0]["timeout"], (engines.CONNECT_TIMEOUT, 7))

    def test_processing_s(self):
        self.assertAlmostEqual(engines.processing_s({"OpenAI-Processing-Ms": "1234"}), 1.234)
        self.assertIsNone(engines.processing_s({"x-ratelimit-remaining-requests": "5"}))
        self.assertIsNone(engines.processing_s({"openai-processing-ms": "rapido"}))


class FakeProcess:
    def __init__(self):
        self.killed = False

    def poll(self):
        return 1 if self.killed else None

    def kill(self):
        self.killed = True


class FakeWorker:
    def __init__(self):
        self.calls, self.killed = [], False

    def ensure(self, model, threads, on_progress=None, cancelled=None):
        self.calls.append((model, int(threads)))
        return True

    def kill(self):
        self.killed = True


def worker_with_fake_process(idle_s):
    w = engines.WorkerClient("t", tempfile.mkdtemp(prefix="notas-rend-w-"), idle_s=idle_s)
    clock = [1000.0]
    w.clock = lambda: clock[0]
    w.last_used, w.p = 1000.0, FakeProcess()
    return w, clock


class KeepaliveTests(unittest.TestCase):
    def test_el_modelo_sigue_cargado_los_minutos_del_ajuste(self):
        st = {"worker_keepalive_min": 30}
        w, clock = worker_with_fake_process(lambda: server.keepalive_s(st))
        clock[0] = 1000 + 1799
        self.assertFalse(w._idle_check())
        self.assertTrue(w.alive())
        clock[0] = 1000 + 1801
        self.assertTrue(w._idle_check())
        self.assertFalse(w.alive())

    def test_el_ajuste_se_lee_en_caliente_y_cero_es_como_antes(self):
        st = {"worker_keepalive_min": 30}
        w, clock = worker_with_fake_process(lambda: server.keepalive_s(st))
        st["worker_keepalive_min"] = 0  # cambiado en Ajustes: vale desde ya
        self.assertEqual(w.idle_limit(), engines.WorkerClient.IDLE_S)
        clock[0] = 1000 + 301
        self.assertTrue(w._idle_check())

    def test_una_peticion_renueva_el_plazo(self):
        w, clock = worker_with_fake_process(lambda: 60)
        clock[0] = 1050
        w.last_used = w.clock()  # lo que hace request() al terminar
        clock[0] = 1100
        self.assertFalse(w._idle_check())

    def test_keepalive_s(self):
        self.assertEqual(server.keepalive_s({"worker_keepalive_min": 30}), 1800)
        self.assertEqual(server.keepalive_s({}), 1800)
        self.assertEqual(server.keepalive_s({"worker_keepalive_min": 0}), 300)
        self.assertEqual(server.keepalive_s({"worker_keepalive_min": "x"}), 1800)
        self.assertEqual(server.DEFAULT_SETTINGS["worker_keepalive_min"], 30)
        self.assertTrue(server.DEFAULT_SETTINGS["worker_preload"])


class PreloadTests(unittest.TestCase):
    def test_preload_model_elige_el_del_directo_si_es_local_y_esta_descargado(self):
        cached = lambda m: m in {"small", "medium"}  # noqa: E731
        self.assertEqual(server.preload_model({"live_preset": "medium", "preset": "rapido"}, cached),
                         ("live", "medium"))
        self.assertEqual(server.preload_model({"live_preset": "nube", "preset": "rapido"}, cached), ("batch", "small"))
        self.assertIsNone(server.preload_model({"live_preset": "nube", "preset": "nube"}, cached))
        self.assertIsNone(server.preload_model({"live_preset": "turbo", "preset": "calidad"}, cached))  # sin descargar
        self.assertIsNone(server.preload_model({"live_preset": "medium", "worker_preload": False}, cached))

    def test_jobs_preload_carga_en_el_worker_que_toca(self):
        j = bare_jobs([])
        j.settings = types.SimpleNamespace(get=lambda: {"live_preset": "medium", "preset": "rapido", "threads": 4})
        j.live_worker, j.local = FakeWorker(), types.SimpleNamespace(worker=FakeWorker())
        with mock.patch.object(server, "preload_model", lambda st, cached=None: ("live", "medium")):
            self.assertEqual(j.preload(), ("live", "medium"))
        self.assertEqual(j.live_worker.calls, [("medium", 4)])
        self.assertEqual(j.local.worker.calls, [])
        with mock.patch.object(server, "preload_model", lambda st, cached=None: None):
            self.assertIsNone(j.preload())
        self.assertEqual(j.live_worker.calls, [("medium", 4)])

    def test_apagar_mata_los_dos_procesos_de_whisper(self):
        queued, running = FakeJob(status="queued"), FakeJob(status="running")
        j = bare_jobs([queued, running])
        j.live_worker, j.local = FakeWorker(), types.SimpleNamespace(worker=FakeWorker())
        j.shutdown()
        self.assertTrue(j.live_worker.killed)
        self.assertTrue(j.local.worker.killed)
        self.assertEqual(queued.status, "cancelled")
        self.assertTrue(running.cancel)

    def test_save_settings_acota_el_ajuste(self):
        tmp = tempfile.mkdtemp(prefix="notas-rend-s-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        app = server.App.__new__(server.App)
        app.settings = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
        with mock.patch.object(server.App, "config", lambda self, q: {}):
            app.save_settings({"worker_keepalive_min": 5000, "worker_preload": False})
            self.assertEqual(app.settings.get()["worker_keepalive_min"], 720)
            self.assertFalse(app.settings.get()["worker_preload"])
            app.save_settings({"worker_keepalive_min": -3})
            self.assertEqual(app.settings.get()["worker_keepalive_min"], 0)


class LibraryCacheTests(TempTree):
    def setUp(self):
        super().setUp()
        self.wav("Reuniones/2026-10-30 10-00-00.wav")
        self.write("Reuniones/2026-10-30 10-00-00.txt", "hola\n")

    def age_dirs(self, seconds=10):
        """Las carpetas recien creadas cuentan como cambiadas (RECENT_S): se les pone una fecha vieja."""
        old = time.time() - seconds
        for d, _, _ in os.walk(self.root):
            os.utime(d, (old, old))

    def lib(self):
        self.age_dirs()
        lib = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        clock = [1000.0]
        lib.clock = lambda: clock[0]
        return lib, clock

    def test_un_sondeo_sin_cambios_no_vuelve_a_recorrer_el_disco(self):
        lib, clock = self.lib()
        _, v1 = lib.refresh()
        self.assertEqual(lib.scans, 1)
        for _ in range(3):
            clock[0] += 6
            _, v = lib.refresh(max_age=1.0)
            self.assertEqual(v, v1)
        self.assertEqual(lib.scans, 1)
        self.assertEqual(lib.find("Reuniones/2026-10-30 10-00-00.wav")["txt"], True)

    def test_un_fichero_nuevo_en_una_carpeta_se_detecta_en_el_siguiente_sondeo(self):
        lib, clock = self.lib()
        _, v1 = lib.refresh()
        self.wav("Reuniones/2026-10-30 11-00-00.wav")
        clock[0] += 6
        _, v2 = lib.refresh(max_age=1.0)
        self.assertEqual(lib.scans, 2)
        self.assertNotEqual(v1, v2)
        self.assertIsNotNone(lib.find("Reuniones/2026-10-30 11-00-00.wav"))

    def test_una_grabacion_abierta_que_crece_se_vuelve_a_mirar(self):
        p = self.wav("Reuniones/2026-10-30 12-00-00.wav", seconds=5, open_header=True)
        lib, clock = self.lib()
        lib.refresh()
        d1 = lib.find("Reuniones/2026-10-30 12-00-00.wav")["duration"]
        self.assertIn(p, lib.watch)
        with open(p, "ab") as f:  # OBS sigue escribiendo: cambia el fichero, no la carpeta
            f.write(b"\0" * (48000 * 4 * 5))
        clock[0] += 6
        lib.refresh(max_age=1.0)
        self.assertEqual(lib.scans, 2)
        self.assertGreater(lib.find("Reuniones/2026-10-30 12-00-00.wav")["duration"], d1)

    def test_at_cero_fuerza_el_escaneo_completo(self):
        lib, clock = self.lib()
        lib.refresh()
        lib.at = 0  # lo que hacen los endpoints que escriben (mover, borrar, notas…)
        lib.refresh(max_age=1.0)
        self.assertEqual(lib.scans, 2)

    def test_cada_treinta_segundos_se_repasa_todo_en_segundo_plano(self):
        lib, clock = self.lib()
        lib.refresh()
        clock[0] += server.Library.FULL_RESCAN_S + 1
        t = time.perf_counter()
        lib.refresh(max_age=1.0)  # vuelve enseguida con el arbol que hay
        self.assertLess(time.perf_counter() - t, 1.0)
        self.assertIsNotNone(lib.rescan_thread)
        lib.rescan_thread.join(10)
        self.assertEqual(lib.scans, 2)
        clock[0] += 6
        lib.refresh(max_age=1.0)
        self.assertEqual(lib.scans, 2)  # el plazo se renovo con el repaso

    def test_api_tree_devuelve_el_arbol_publico_sin_rutas_absolutas(self):
        lib, clock = self.lib()
        app = server.App.__new__(server.App)
        app.library = lib
        r = app.tree({})
        self.assertNotIn("_abs", json.dumps(r))
        self.assertEqual(r["tree"]["audios"], [])
        self.assertEqual(r["tree"]["folders"][0]["audios"][0]["name"], "2026-10-30 10-00-00.wav")
        clock[0] += 6
        self.assertEqual(app.tree({"v": r["version"]}), {"version": r["version"], "same": True})
        self.assertEqual(lib.scans, 1)


class LatencyPhasesTests(unittest.TestCase):
    def test_estadisticas_por_fase_y_texto_del_log(self):
        lat = live.Latency()
        lat.seen(10.0, 100.0)
        lat.add(10.0, 101.5, {"prep": 0.1, "wait": 0.0, "net": 1.2, "infer": None, "paint": 0.05})
        lat.seen(20.0, 110.0)
        lat.add(20.0, 112.0, {"prep": 0.3, "wait": 0.5, "net": 1.0, "infer": 0.8, "paint": 0.05})
        st = lat.stats()
        self.assertEqual((st["n"], st["p50"], st["p95"]), (2, 1.5, 2.0))
        self.assertEqual(st["phases"]["net"], {"p50": 1.0, "p95": 1.2})
        self.assertEqual(st["phases"]["infer"], {"p50": 0.8, "p95": 0.8})  # solo las muestras que lo traen
        self.assertEqual(set(st["phases"]), set(live.PHASES))
        txt = live.fmt_phases({"prep": 0.1, "wait": 0.0, "net": 1.2, "infer": None, "paint": 0.05})
        self.assertEqual(txt, " · preparación 0.10 s · cupo 0.00 s · red 1.20 s · pintado 0.05 s")
        self.assertIn("red 1.00 s (inferencia 0.80 s)", live.fmt_phases({"net": 1.0, "infer": 0.8}))
        self.assertEqual(live.Latency().stats()["phases"], {})


if __name__ == "__main__":
    unittest.main()
