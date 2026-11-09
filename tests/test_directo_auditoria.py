# -*- coding: utf-8 -*-
"""Directo en la nube, arreglos de la auditoria final (P1-05, P1-14, P1-15, P1-16, P2-09 y P2-18 a P2-25).

Claves adicionales por proveedor, ponerse al dia sin mandar nunca mas de 25 MB, el modelo del directo con una pasarela
propia, trozos que fallan sin tumbar la sesion, el repaso final al cancelar o apagar, claves no validas, respuestas 200
que no son JSON, los ajustes vigentes en cada trozo, la traduccion (ultimos 30 s, ultimo paso corto, modelo y proveedor
reales), cupos por modelo y el resumen con Azure. Todo con dobles: sin red, sin faster-whisper ni PyAV."""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

from support import TempTree, bare_jobs, engines, live, server, wav_bytes
from test_directo import (CFG2, K1, K2, TURBO, Clock, DirectoJob, FakeCloud, FakeRecorder, FakeRequests,
                          FakeResponse, FakeSummarizer, FakeWorker, energy_vad, pool, synth, ts)
from test_frontend import js_defs, read

SR = live.SR
OK = {"text": "hola", "segments": [{"start": 0, "end": 1, "text": "hola"}], "language": "spanish"}
CHAT_OK = {"choices": [{"message": {"content": "Buenos días."}}]}
AZURE = {"provider": "azure", "api_key": "az-principal-0000000000000000",
         "azure_endpoint": "https://r.openai.azure.com", "azure_deployment": "whisper"}


class HtmlResponse(FakeResponse):
    """200 con la pagina de un portal de acceso (el proxy de la red de la empresa): json() falla como en requests."""

    def __init__(self, status=200):
        super().__init__(status, {}, {})
        self.text = "<html><body>  Inicie sesión en el portal de la red  </body></html>"

    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


class Net:
    """`requests` falso y sin esperas reales: las esperas de _post (_wait) y de _chat (_sleep) solo se anotan."""

    def __init__(self, responses):
        self.fake, self.waits = FakeRequests(responses), []

    def __enter__(self):
        rec = staticmethod(lambda secs, cancelled: self.waits.append(secs))
        self._ps = [mock.patch.dict(sys.modules, {"requests": self.fake}),
                    mock.patch.object(engines.CloudEngine, "_wait", rec),
                    mock.patch.object(engines.Summarizer, "_sleep", rec)]
        for p in self._ps:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._ps):
            p.stop()

    def auths(self):
        return [kw["headers"].get("Authorization") or kw["headers"].get("api-key") for _, kw in self.fake.calls]


def chunk_file(test):
    tmp = tempfile.mkdtemp(prefix="notas-aud-")
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    p = os.path.join(tmp, "t.wav")
    with open(p, "wb") as f:
        f.write(wav_bytes(0.2, rate=16000, channels=1))
    return tmp, p


def post(eng, cfg, chunk, **kw):
    return eng._post(cfg, chunk, None, None, lambda **k: None, lambda: False, **kw)


def phrases(total, every=4.0, voice=3.4):
    """Frases de `voice` s separadas por pausas, durante `total` s."""
    return [(i * every, i * every + voice) for i in range(int(total // every))]


class KeyCheckingCloud(FakeCloud):
    """Como la de verdad: sin API key en los ajustes que recibe, falla antes de mandar nada (no cuenta como envio)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent_at = []

    def transcribe_array(self, cfg, audio, *a, **k):
        if not (cfg.get("api_key") or "").strip():
            raise engines.EngineError("Falta la API key de la nube (Ajustes ⚙).")
        self.sent_at.append(self.rec.time())
        return super().transcribe_array(cfg, audio, *a, **k)


class OddCloud(FakeCloud):
    """El segundo trozo revienta con algo que no es un EngineError (una respuesta rara de un proxy)."""

    def transcribe_array(self, *a, **k):
        out = super().transcribe_array(*a, **k)
        if len(self.calls) == 2:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return out


def run_session(spans, total, latency=0.8, remaining=None, translate=False, fail_on=(), cfg=None, cfg_fn=None,
                cloud_cls=FakeCloud, summ=None, pre=0.6):
    """Una sesion de live.run con el reloj falso de test_directo (la grabacion crece mientras el directo duerme).
    pre: segundos ya grabados cuando arranca el directo (con mas de 300 s, empieza poniendose al dia)."""
    tmp = tempfile.mkdtemp(prefix="notas-aud-run-")
    try:
        path = os.path.join(tmp, "reunion.wav")
        rec = FakeRecorder(path, synth(spans, total))
        cloud = cloud_cls(rec, latency, remaining, fail_on)
        job = DirectoJob(preset="nube")
        job.translate = translate
        summ = summ or FakeSummarizer()
        opts = {"preset": "nube", "language": None, "prompt": None, "threads": 1, "worker": FakeWorker(rec),
                "cloud": cloud, "cloud_cfg": cfg or {"provider": "groq", "api_key": "k1"}, "tmp_dir": tmp,
                "resume": [], "summarizer": summ}
        if cfg_fn is not None:
            opts["cloud_cfg_fn"] = lambda: cfg_fn(rec)
        logs = []
        fake_time = types.SimpleNamespace(time=rec.time, sleep=rec.sleep)
        with mock.patch.object(live, "time", fake_time), mock.patch.object(live, "speech", energy_vad):
            rec.sleep(pre)  # ya hay algo grabado cuando arranca el directo
            segs, lang = live.run(job, path, opts, lambda s: None, logs.append)
        return types.SimpleNamespace(segs=segs, lang=lang, cloud=cloud, job=job, logs=logs, rec=rec, summ=summ)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------- P1-05: claves de un proveedor

class ClavesPorProveedorTests(unittest.TestCase):
    def test_las_de_groq_no_salen_hacia_azure(self):
        """El caso de la auditoria: con Azure y claves adicionales de Groq guardadas, la segunda peticion salia hacia
        …openai.azure.com con la clave de Groq (Azure tambien manda x-ratelimit-remaining-requests)."""
        tmp, chunk = chunk_file(self)
        cfg = dict(AZURE, extra_keys=[K2])
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        low = {"x-ratelimit-remaining-requests": "5"}
        with Net([FakeResponse(200, low, OK), FakeResponse(200, low, OK)]) as net:
            for _ in range(2):
                eng.keys.clock.t += 10
                post(eng, cfg, chunk)
        self.assertEqual(net.auths(), [AZURE["api_key"]] * 2)
        self.assertNotIn(K2, json.dumps([kw["headers"] for _, kw in net.fake.calls]))
        self.assertEqual(engines.cloud_keys(cfg), [AZURE["api_key"]])

    def test_cada_proveedor_solo_las_suyas(self):
        cfg = {"api_key": "main", "extra_keys": ["g1"], "extra_keys_openai": ["o1"], "extra_keys_azure": ["a1"],
               "extra_keys_custom": ["c1", "main"]}
        self.assertEqual(engines.cloud_keys(dict(cfg, provider="groq")), ["main", "g1"])
        self.assertEqual(engines.cloud_keys(cfg), ["main", "g1"])  # sin proveedor = Groq, como siempre
        self.assertEqual(engines.cloud_keys(dict(cfg, provider="openai")), ["main", "o1"])
        self.assertEqual(engines.cloud_keys(dict(cfg, provider="azure")), ["main", "a1"])
        self.assertEqual(engines.cloud_keys(dict(cfg, provider="custom")), ["main", "c1"])
        self.assertEqual(engines.cloud_keys(dict(cfg, provider="otra-cosa")), ["main"])
        self.assertEqual(sorted(engines.all_extra_keys(cfg)), ["a1", "c1", "g1", "main", "o1"])


class ClavesPorProveedorAjustesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notas-claves-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = server.App.__new__(server.App)
        self.app.settings = server.Store(os.path.join(self.tmp, "settings.json"), server.DEFAULT_SETTINGS)
        self.app.config = lambda q: {}
        self.app.settings.update(lambda d: d["cloud"].update(api_key="gsk_main_000000000"))

    def cloud(self):
        return self.app.settings.get()["cloud"]

    def test_al_cambiar_de_proveedor_no_pasan_al_nuevo(self):
        groq = ["gsk_a_000000001", "gsk_b_000000002"]  # mascaras distintas (gsk_…0001 y gsk_…0002)
        self.app.save_settings({"cloud": {"extra_keys": groq}})
        masks = [server.masked(k) for k in groq]
        # la interfaz de antes reenviaba las mascaras de Groq al pasar a Azure: no se convierten en claves de Azure
        self.app.save_settings({"cloud": {"provider": "azure", "azure_endpoint": "https://r.openai.azure.com",
                                          "azure_deployment": "whisper", "extra_keys": masks}})
        c = self.cloud()
        self.assertEqual(c["extra_keys"], groq)  # se conservan, con Groq
        self.assertEqual(c["extra_keys_azure"], [])
        self.assertEqual(engines.cloud_keys(c), ["gsk_main_000000000"])
        # las de Azure se guardan con Azure; al volver a Groq, las de Groq
        self.app.save_settings({"cloud": {"extra_keys_by": {"azure": ["az-extra-0000000000"], "groq": masks}}})
        self.assertEqual(engines.cloud_keys(self.cloud()), ["gsk_main_000000000", "az-extra-0000000000"])
        self.app.save_settings({"cloud": {"provider": "groq"}})
        self.assertEqual(engines.cloud_keys(self.cloud()), ["gsk_main_000000000"] + groq)
        # una mascara de otro proveedor no se acepta como clave
        self.app.save_settings({"cloud": {"extra_keys_by": {"openai": [server.masked("az-extra-0000000000")]}}})
        self.assertEqual(self.cloud()["extra_keys_openai"], [])

    def test_cifradas_en_disco_y_enmascaradas_en_la_api(self):
        self.app.save_settings({"cloud": {"extra_keys_by": {"custom": ["clave-de-la-pasarela-0001"]}}})
        with open(self.app.settings.path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("clave-de-la-pasarela-0001", raw)  # cifrada como las de Groq
        again = server.Store(self.app.settings.path, server.DEFAULT_SETTINGS)
        self.assertEqual(again.get()["cloud"]["extra_keys_custom"], ["clave-de-la-pasarela-0001"])
        self.app.jobs = types.SimpleNamespace(cloud=engines.CloudEngine(self.tmp), summary_ready=lambda: True)
        self.app.hotkey = types.SimpleNamespace(ok=False, error=None)
        with mock.patch.object(server, "ROOT", self.tmp):
            cfg = server.App.config(self.app, {})
        self.assertNotIn("clave-de-la-pasarela-0001", json.dumps(cfg))
        self.assertEqual(cfg["cloud"]["extra_keys_by"]["custom"], [server.masked("clave-de-la-pasarela-0001")])
        self.assertEqual(cfg["cloud"]["extra_keys"], [])  # el proveedor es Groq: las de la pasarela no son suyas


class DiagnosticoClavesTests(TempTree):
    def test_enmascara_las_adicionales_de_todos_los_proveedores(self):
        """scrub recibe las claves adicionales de todos los proveedores, no solo las de Groq. Se mira lo que recibe y
        no solo el texto: con la lista blanca de fix-privacidad, una linea que no es de la app ya sale como «<texto>»,
        pero scrub sigue siendo lo que enmascara las claves en lo que si sale."""
        app = server.App.__new__(server.App)
        app.settings = server.Store(os.path.join(self.data_dir, "settings.json"), server.DEFAULT_SETTINGS)
        azk = "0123456789abcdef0123456789abcdef"  # con pinta de clave de Azure: el patron gsk_/sk- no la ve
        app.settings.update(lambda d: d["cloud"].update(extra_keys_azure=[azk]))
        with open(os.path.join(self.data_dir, "server.log"), "w", encoding="utf-8") as f:
            f.write(f"una línea con {azk} dentro\n")
        seen, scrub = [], server.scrub
        with mock.patch.object(server, "scrub", lambda text, keys=(): seen.append(list(keys)) or scrub(text, keys)):
            out = app.diagnostico({})["texto"]
        self.assertNotIn(azk, out)
        self.assertTrue(seen)
        self.assertTrue(all(azk in keys for keys in seen), seen)


@unittest.skipUnless(hasattr(server.Store, "_rescue"), "el rescate de un settings.json roto llega con fix-privacidad: "
                     "este test vigila la fusion")
class AjustesRotosClavesPorProveedorTests(unittest.TestCase):
    """settings.json cortado: el rescate (Store.RESCUE, que lleva todos los SECRETS) recupera tambien las claves
    adicionales de OpenAI, Azure y la pasarela, no solo las de Groq, y el aviso las cuenta todas."""

    def test_se_rescatan_las_de_todos_los_proveedores(self):
        tmp = tempfile.mkdtemp(prefix="chuleta-ajustes-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "settings.json")
        extra = {"extra_keys": ["gsk_" + "g" * 24], "extra_keys_openai": ["sk-" + "o" * 24, "sk-" + "p" * 24],
                 "extra_keys_azure": ["a" * 32], "extra_keys_custom": ["clave-de-la-pasarela-0001"]}
        self.assertEqual(sorted(extra), sorted(engines.EXTRA_KEYS_FIELDS.values()))
        cloud = {"provider": "openai", "api_key": server.protect(("sk-" + "m" * 24).encode())}
        cloud.update({f: [server.protect(k.encode()) for k in v] for f, v in extra.items()})
        text = json.dumps({"language": "pt", "cloud": cloud, "zzz": "x" * 50}, indent=1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text[:-20])  # corte de luz a mitad de escritura
        with mock.patch.object(server, "log"):
            st = server.Store(path, server.DEFAULT_SETTINGS)
        c = st.get()["cloud"]
        self.assertEqual(c["api_key"], "sk-" + "m" * 24)
        self.assertEqual({f: c[f] for f in extra}, extra)
        self.assertEqual(st.broken["recuperado"], ["la API key", "5 claves adicionales"])


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class InterfazJsTests(unittest.TestCase):
    PRE = ("const S = {config: {providers: {groq: {label: 'Groq'}, openai: {label: 'OpenAI'}, "
           "azure: {label: 'Azure OpenAI'}, custom: {label: 'Otra (compatible con OpenAI)'}}}};\n")

    def run_js(self, code):
        defs = js_defs(read("directo.js"), ["directoProvName", "directoOwnerText", "directoTradLabel"])
        r = subprocess.run(["node", "-e", self.PRE + defs + "\n" + code], capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_dice_de_quien_son_las_claves(self):  # P1-05
        out = self.run_js("console.log(directoOwnerText('azure', {groq: [{mask: 'gsk_…0000', value: ''}, "
                          "{mask: '', value: 'gsk_nueva'}], azure: [], openai: [{mask: '', value: '  '}]}))")
        self.assertIn("Estas claves son solo de Azure OpenAI", out)
        self.assertIn("no se envían a ningún otro proveedor", out)
        self.assertIn("Tienes 2 claves adicionales de Groq: se conservan y solo se usan con Groq", out)
        self.assertNotIn("de OpenAI", out)  # una fila vacia no cuenta

    def test_traduccion_con_el_modelo_y_el_proveedor_reales(self):  # P2-24
        out = self.run_js("console.log([directoTradLabel({translate_model: 'openai/gpt-oss-20b', provider: 'Groq'}), "
                          "directoTradLabel({translate_model: 'gpt-4o-mini', provider: 'OpenAI'}), "
                          "JSON.stringify(directoTradLabel({translate_model: null, provider: 'Azure OpenAI'}))]"
                          ".join('|'))")
        self.assertEqual(out, 'gpt-oss-20b en Groq|gpt-4o-mini en OpenAI|""')


class InterfazTextosTests(unittest.TestCase):
    def test_claves_y_modelo_por_proveedor(self):  # P1-05 y P1-15
        js = read("directo.js")
        self.assertIn("extra_keys_by", js)  # cada proveedor, sus claves
        self.assertIn("directoOwnerText(cur, state)", js)
        self.assertNotIn("Pega aquí la API key de otra cuenta de Groq'", js)  # el proveedor, no Groq fijo
        self.assertIn('id="cLiveModelCustom"', js)  # «Otra (compatible)»: su modelo del directo
        self.assertIn("out.live_model_custom", js)

    def test_traduccion_sin_groq_fijo_y_apagada_sin_chat(self):  # P2-24
        js = read("directo.js")
        self.assertNotIn("con gpt-oss-20b en Groq.", js)  # ni en el title de la casilla
        self.assertNotIn('<span class="muted">gpt-oss-20b en Groq', js)  # ni en la cabecera de la columna
        self.assertIn("d.translate_ok !== false", js)
        self.assertIn('data-id="${esc(j.id)}" disabled', js)
        self.assertIn("d.translate_why", js)

    def test_resumen_con_azure_no_manda_a_configurar(self):  # P2-25
        notas = read("notas.js")
        self.assertIn("S.config?.cloud?.provider === 'azure' && !s.ready", notas)
        self.assertIn("El resumen con IA aún no funciona con Azure OpenAI", notas)
        self.assertIn(": azure ? azureNote", notas)


# ----------------------------------------------------------------------------- P1-14: ponerse al dia

class PonerseAlDiaTests(unittest.TestCase):
    def buf(self, secs):
        return np.zeros(int(secs * SR), np.float32)

    def test_con_retraso_junta_todo_lo_pendiente_hasta_10_min(self):
        b = self.buf(200.2)
        self.assertTrue(live.behind(b))
        self.assertFalse(live.behind(self.buf(11.0)))  # al dia: como siempre (como mucho dos ventanas)
        cut, ov = live.direct_cut(b, ts(*phrases(200.0)), live.DIRECT_PACE, catch_up=True)
        self.assertEqual(ov, 0)
        self.assertGreater(cut, 190 * SR)  # casi todo de una vez, en la ultima pausa
        cut, ov = live.direct_cut(self.buf(1000.2), ts(*phrases(1000.0)), live.DIRECT_PACE, catch_up=True)
        self.assertLessEqual(cut, live.CATCHUP_MAX_S * SR)  # nunca mas de 10 min (19,2 MB en WAV)
        self.assertGreater(cut, (live.CATCHUP_MAX_S - 5) * SR)
        # sin pausas, corte forzado en lo que haya (con el tope) y el solape de siempre
        self.assertEqual(live.direct_cut(self.buf(700), ts((0, 700)), live.DIRECT_PACE, catch_up=True),
                         (live.CATCHUP_MAX_S * SR, int(live.DIRECT_OVERLAP_S * SR)))
        self.assertEqual(live.direct_cut(self.buf(40), ts((0, 40)), live.DIRECT_PACE, catch_up=True),
                         (40 * SR, int(live.DIRECT_OVERLAP_S * SR)))
        self.assertLess(live.CATCHUP_MAX_S * SR * 2, 25 * 1000 * 1000)  # WAV de 16 kHz mono 16 bit

    def test_el_final_sale_en_trozos_de_como_mucho_10_min(self):
        lim = live.CATCHUP_MAX_S * SR
        head = [{"start": s["start"], "end": min(s["end"], lim)} for s in ts(*phrases(900.0)) if s["start"] < lim]
        cut, ov = live.capped_cut(self.buf(900), head)
        self.assertLessEqual(cut, lim)
        self.assertGreater(cut, lim - 5 * SR)
        self.assertEqual(ov, 0)
        self.assertEqual(live.capped_cut(self.buf(30), None), (30 * SR, 0))
        self.assertEqual(live.capped_cut(self.buf(700), []), (lim, int(live.DIRECT_OVERLAP_S * SR)))

    def test_una_api_lenta_no_acumula_retraso_ni_manda_mas_de_25_mb(self):
        """La API tarda 60 s por peticion (un cupo de pocas peticiones por minuto): antes cada peticion se llevaba como
        mucho 12 s de audio, el retraso crecia sin tope y al terminar salia todo de golpe (aqui, ~14 min: >25 MB)."""
        r = run_session(phrases(1080.0), 1080.0, latency=60.0)
        secs = [c["secs"] for c in r.cloud.calls]
        self.assertLessEqual(max(secs), 601)  # 10 min (live.CATCHUP_MAX_S)
        self.assertLess(max(secs) * 2 * SR, 25 * 1000 * 1000)
        self.assertLess(secs[-1], 130)  # al terminar queda lo de la ultima peticion, no todo el retraso
        self.assertTrue(any(s > 40 for s in secs))  # se ha puesto al dia juntando lo pendiente
        self.assertLess(r.rec.time() - (r.rec.t0 + 1080.0), 200)  # y termina poco despues de la grabacion
        self.assertGreater(len(r.segs), 10)


class SinPasarDelLimitePorMinutoTests(unittest.TestCase):  # P1-14: ponerse al dia sin pasar de las RPM
    def test_con_un_limite_por_minuto_declarado_no_se_pasa(self):
        tmp, chunk = chunk_file(self)
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        hdr = {"x-ratelimit-limit-requests": "3", "x-ratelimit-remaining-requests": "2",
               "x-ratelimit-reset-requests": "20s"}
        with Net([FakeResponse(200, hdr, OK), FakeResponse(200, hdr, OK)]) as net:
            post(eng, AZURE, chunk)
            post(eng, AZURE, chunk)  # en el mismo instante del reloj falso: hay que esperar el hueco de 60/3 s
        self.assertGreaterEqual(max(net.waits), 19.9)

    def test_sin_ninguna_espera_a_que_se_renueve_el_minuto_pero_no_un_cupo_por_dia(self):
        p = pool()
        p.note("k", "whisper", 200, {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "45s"})
        self.assertAlmostEqual(p.reserve("k", "whisper", per_minute=True), 45.0)
        q = pool()  # en Groq el limite y la renovacion son del dia: ni hueco de 60/2000 s ni esperar horas
        q.note("k", "whisper", 200, {"x-ratelimit-limit-requests": "2000", "x-ratelimit-remaining-requests": "0",
                                     "x-ratelimit-reset-requests": "5h"})
        self.assertEqual(q.reserve("k", "whisper", per_minute=True), 0.0)
        self.assertEqual(q.reserve("k", "whisper"), 3.0)


# ----------------------------------------------------------------------------- P1-15: modelo con una pasarela

class ModeloDirectoPasarelaTests(unittest.TestCase):
    def test_con_otra_compatible_el_de_la_pasarela_o_el_general(self):
        tmp, chunk = chunk_file(self)
        # como lo guardaba la app: live_model con turbo por defecto para todos los proveedores
        cfg = dict(server.DEFAULT_SETTINGS["cloud"], provider="custom", base_url="https://gw.empresa.example/v1",
                   api_key="clave-gw-000000000000", model="mi-whisper")
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        with Net([FakeResponse(200, {}, OK), FakeResponse(200, {}, OK)]) as net:
            post(eng, cfg, chunk, model=engines.CloudEngine.live_model(cfg))
            eng.keys.clock.t += 10
            cfg2 = dict(cfg, live_model_custom="rapido-v1")
            post(eng, cfg2, chunk, model=engines.CloudEngine.live_model(cfg2))
        self.assertEqual([kw["data"]["model"] for _, kw in net.fake.calls], ["mi-whisper", "rapido-v1"])
        self.assertEqual(engines.CloudEngine.live_model(dict(cfg, provider="groq")), "whisper-large-v3-turbo")

    def test_ajustes_guardan_el_de_la_pasarela(self):
        tmp = tempfile.mkdtemp(prefix="notas-lm-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        app = server.App.__new__(server.App)
        app.settings = server.Store(os.path.join(tmp, "s.json"), server.DEFAULT_SETTINGS)
        app.config = lambda q: {}
        self.assertEqual(server.DEFAULT_SETTINGS["cloud"]["live_model_custom"], "")
        app.save_settings({"cloud": {"live_model_custom": " rapido-v1 "}})
        self.assertEqual(app.settings.get()["cloud"]["live_model_custom"], "rapido-v1")


# ----------------------------------------------------------------------------- P1-16 y P2-19: trozos que fallan

class TrozosQueFallanTests(unittest.TestCase):
    def test_el_ultimo_trozo_falla_y_la_sesion_acaba_bien(self):
        # la ultima frase llega hasta el final de la grabacion: la manda el ultimo trozo (al terminar)
        r = run_session([(0.0, 3.4), (4.0, 7.6), (8.2, 13.0)], 13.0, fail_on=(3,))
        self.assertEqual(len(r.cloud.calls), 3)
        self.assertEqual(len(r.segs), 2)
        self.assertIn("HTTP 500", r.job.live_error)
        self.assertTrue(any("trozo sin transcribir" in l for l in r.logs))

    def test_al_ponerse_al_dia_tampoco_tumba_la_sesion(self):
        # el directo arranca con 5,5 min ya grabados: se pone al dia en trozos grandes y el primero falla
        r = run_session(phrases(330.0), 330.0, fail_on=(1,), pre=331.0)
        self.assertGreaterEqual(len(r.cloud.calls), 2)
        self.assertGreater(r.cloud.calls[0]["secs"], 60)  # era un trozo de ponerse al dia
        self.assertGreaterEqual(len(r.segs), 1)  # los siguientes si salen
        self.assertIn("HTTP 500", r.job.live_error)

    def test_si_fallan_todos_devuelve_vacio_sin_excepcion(self):
        r = run_session([(0.0, 3.4), (4.0, 7.6), (8.2, 13.0)], 13.0, fail_on=range(1, 50))
        self.assertEqual(r.segs, [])
        self.assertGreaterEqual(len(r.cloud.calls), 3)
        self.assertIn("HTTP 500", r.job.live_error)

    def test_un_fallo_que_no_es_engine_error_tampoco(self):  # P2-19
        r = run_session([(0.0, 3.4), (4.0, 7.6), (8.2, 11.3)], 13.0, cloud_cls=OddCloud)
        self.assertEqual(len(r.cloud.calls), 3)
        self.assertEqual(len(r.segs), 2)
        self.assertIn("ValueError", r.job.live_error)

    def test_el_directo_reintenta_poco(self):
        tmp, _ = chunk_file(self)
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        cfg = {"provider": "groq", "api_key": K1}
        with Net([FakeResponse(500, {}, {}), FakeResponse(500, {}, {}), FakeResponse(200, {}, OK)]) as net:
            with self.assertRaises(engines.EngineError):
                eng.transcribe_array(cfg, np.zeros(SR, np.float32), None, None, tmp, "j", lambda s: None,
                                     lambda: False, model=TURBO)
        self.assertEqual(len(net.fake.calls), engines.LIVE_ATTEMPTS)
        self.assertLessEqual(engines.LIVE_ATTEMPTS, 2)
        self.assertLessEqual(sum(net.waits), engines.LIVE_MAX_WAIT_S)
        self.assertEqual(net.fake.calls[0][1]["timeout"][1], engines.LIVE_READ_S + engines.LIVE_READ_PER_S)


class LiveJobs(TempTree):
    """Un Jobs sin hilos con lo que usan _run_live, _run, _final_pass y compania; la reunion r.wav de 2 s."""

    def setUp(self):
        super().setUp()
        self.audio = self.wav("r.wav", seconds=2.0)

    def jobs(self):
        j = bare_jobs([])
        j.settings = server.Store(os.path.join(self.data_dir, "settings.json"), server.DEFAULT_SETTINGS)
        j.settings.update(lambda d: d["cloud"].update(api_key="gsk_main_000000000"))
        j.queues = {k: queue.Queue() for k in ("local", "cloud", "resumen", "voces")}
        j.library = types.SimpleNamespace(at=1, find=lambda rel: None, refresh=lambda **k: None)
        j.cloud = engines.CloudEngine(os.path.join(self.data_dir, "tmp"))
        j.summarizer = engines.Summarizer(keys=j.cloud.keys)
        j.local, j.live_worker = None, None
        j.after = []
        j._after_transcribe = lambda rel: j.after.append(rel)
        return j

    def live_job(self):
        job = server.Job("r.wav", "r.wav", "nube", None)
        job.engine, job.live, job.recording, job.recorded = "live", True, False, 120.0
        return job

    def final_passes(self, jobs):
        return [j for j in jobs.list if j.final_pass]

    def run_live(self, jobs, fake_run):
        job = self.live_job()
        job.recording = True
        with mock.patch.object(server.live, "run", fake_run):
            jobs._run_live(job, self.audio, jobs.settings.get())
        return job


class RepasoTrasErroresTests(LiveJobs):  # P1-16: el repaso final, el resumen y los destinos se hacen igual
    def test_sin_texto_del_directo_se_encola_el_repaso_final(self):
        jobs = self.jobs()

        def fake_run(j, path, opts, write, log):
            j.recorded, j.recording, j.live_error = 120.0, False, "HTTP 500: se cayó"
            return [], None
        job = self.run_live(jobs, fake_run)
        self.assertEqual(len(self.final_passes(jobs)), 1)
        self.assertEqual(job.status, "done")
        self.assertIn("repaso final", job.stage)

    def test_un_error_con_la_grabacion_ya_terminada_tambien(self):
        jobs = self.jobs()

        def fake_run(j, path, opts, write, log):
            j.recorded, j.recording = 120.0, False
            raise engines.EngineError("algo inesperado al final")
        job = self.run_live(jobs, fake_run)
        self.assertEqual(job.status, "error")
        self.assertEqual(len(self.final_passes(jobs)), 1)

    def test_con_la_grabacion_en_marcha_no_hay_repaso(self):
        jobs = self.jobs()

        def fake_run(j, path, opts, write, log):
            j.recorded = 120.0  # sigue grabando: un repaso ahora leeria un fichero a medias
            raise engines.EngineError("No se reconoce la cabecera WAV de la grabación.")
        self.run_live(jobs, fake_run)
        self.assertEqual(self.final_passes(jobs), [])

    def test_lee_los_ajustes_vigentes_en_cada_trozo(self):  # P2-20
        jobs = self.jobs()
        seen = {}

        def fake_run(j, path, opts, write, log):
            jobs.settings.update(lambda d: d["cloud"].update(api_key="gsk_otra_000000000"))
            seen["cfg"] = opts["cloud_cfg_fn"]()
            j.recorded, j.recording = 5.0, False
            return [(0.0, 1.0, "hola")], "es"
        self.run_live(jobs, fake_run)
        self.assertEqual(seen["cfg"]["api_key"], "gsk_otra_000000000")
        self.assertEqual(seen["cfg"]["summary_model"], server.DEFAULT_SETTINGS["summary_model"])


class RepasoPendienteTests(LiveJobs):  # P2-09
    def test_cancelar_el_repaso_en_cola_hace_destinos_y_resumen_con_el_texto_del_directo(self):
        jobs = self.jobs()
        self.assertTrue(jobs._final_pass(self.live_job()))
        fp = self.final_passes(jobs)[0]
        self.assertTrue(jobs.cancel(fp.id))
        end = time.time() + 5
        while not jobs.after and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(jobs.after, ["r.wav"])
        self.assertEqual(fp.status, "cancelled")

    def test_cancelar_el_repaso_en_marcha_tambien(self):
        jobs = self.jobs()

        def cancelled(*a, **k):
            raise engines.Cancelled()
        jobs.cloud.transcribe = cancelled
        jobs._final_pass(self.live_job())
        fp = self.final_passes(jobs)[0]
        jobs._run(fp)
        self.assertEqual(fp.status, "cancelled")
        self.assertEqual(jobs.after, ["r.wav"])

    def test_apagar_con_el_repaso_en_cola_lo_retoma_al_arrancar(self):
        jobs = self.jobs()
        jobs._final_pass(self.live_job())
        fp = self.final_passes(jobs)[0]
        jobs.shutdown()
        self.assertEqual(fp.status, "cancelled")
        self.assertEqual(jobs.after, [])  # al apagar no se da por bueno el texto del directo
        again = self.jobs()  # el proximo arranque, con el mismo DATA_DIR
        again._resume_final_passes(delay=0)
        queued = [j for j in self.final_passes(again) if j.status == "queued"]
        self.assertEqual([j.path for j in queued], ["r.wav"])
        self.assertEqual(again.queues["cloud"].qsize(), 1)
        self.assertEqual(again.after, [])

    def test_apagar_con_el_repaso_en_marcha_tambien_lo_deja_pendiente(self):
        jobs = self.jobs()
        jobs._final_pass(self.live_job())
        fp = self.final_passes(jobs)[0]

        def cancelled(*a, **k):
            jobs.shutdown()  # se apaga a mitad del repaso
            raise engines.Cancelled()
        jobs.cloud.transcribe = cancelled
        jobs._run(fp)
        self.assertEqual(jobs.after, [])
        again = self.jobs()
        again._resume_final_passes(delay=0)
        self.assertEqual(len(self.final_passes(again)), 1)

    def test_terminado_ya_no_queda_pendiente_y_si_ya_no_se_puede_se_resume_con_el_directo(self):
        jobs = self.jobs()
        jobs.cloud.transcribe = lambda *a, **k: ([(0.0, 1.0, "hola")], "es", 2.0)
        jobs._final_pass(self.live_job())
        jobs._run(self.final_passes(jobs)[0])
        self.assertEqual(jobs.after, ["r.wav"])
        again = self.jobs()
        again._resume_final_passes(delay=0)
        self.assertEqual(again.list, [])  # no se repite
        # pendiente pero ya sin nube: destinos y resumen con el texto del directo, y se olvida
        jobs._final_pass(self.live_job())
        jobs.shutdown()
        third = self.jobs()
        third.settings.update(lambda d: d["cloud"].update(api_key=""))
        third._resume_final_passes(delay=0)
        self.assertEqual(third.list, [])
        self.assertEqual(third.after, ["r.wav"])
        self.assertEqual(third._pending().data, {})


class RepasoConTramoLocalTests(LiveJobs):
    """local_part: si el directo tuvo algun trozo en local (lo marca fix-privacidad en start_live y set_live_preset).
    Si el repaso final no acaba, el texto que queda es el del directo, y con algo en local el resumen automatico no lo
    sube a la nube sin permiso: el dato viaja con el repaso (_queue_final_pass, con un valor por defecto) y queda
    apuntado con el en repasos_pendientes.json, para el que se retome al arrancar. Sin el dato, como local."""

    def pending_file(self):
        with open(os.path.join(self.data_dir, "repasos_pendientes.json"), encoding="utf-8") as f:
            return json.load(f)

    def live_job(self, local_part=None):
        job = super().live_job()
        if local_part is not None:
            job.local_part = local_part
        return job

    def test_el_repaso_lleva_lo_que_diga_su_directo_y_queda_apuntado(self):
        for local_part in (False, True):
            jobs = self.jobs()
            self.assertTrue(jobs._final_pass(self.live_job(local_part)))
            self.assertIs(self.final_passes(jobs)[-1].local_part, local_part)
            self.assertEqual(self.pending_file(), {"r.wav": {"local_part": local_part}})
            jobs._final_pass_over("r.wav")
            self.assertEqual(self.pending_file(), {})

    def test_sin_el_dato_cuenta_como_local(self):
        jobs = self.jobs()
        jobs._final_pass(self.live_job())  # un directo que no dice nada
        jobs._queue_final_pass("r.wav", "r.wav", None)  # y una llamada sin el parametro
        self.assertEqual([j.local_part for j in self.final_passes(jobs)], [True, True])
        self.assertEqual(self.pending_file(), {"r.wav": {"local_part": True}})

    def test_el_repaso_retomado_al_arrancar_conserva_lo_apuntado(self):
        for local_part in (False, True):
            jobs = self.jobs()
            jobs._final_pass(self.live_job(local_part))
            jobs.shutdown()
            again = self.jobs()  # el proximo arranque, con el mismo DATA_DIR
            again._resume_final_passes(delay=0)
            queued = [j for j in self.final_passes(again) if j.status == "queued"]
            self.assertEqual([j.local_part for j in queued], [local_part])
            self.assertEqual(self.pending_file(), {"r.wav": {"local_part": local_part}})
            again._final_pass_over("r.wav")

    def test_un_pendiente_del_formato_anterior_se_retoma_como_local(self):
        with open(os.path.join(self.data_dir, "repasos_pendientes.json"), "w", encoding="utf-8") as f:
            json.dump(["r.wav"], f)  # antes solo se apuntaban las rutas
        jobs = self.jobs()
        jobs._resume_final_passes(delay=0)
        self.assertEqual([j.local_part for j in self.final_passes(jobs)], [True])
        self.assertEqual(self.pending_file(), {"r.wav": {"local_part": True}})


# ----------------------------------------------------------------------------- P2-18: claves no validas

class ClaveNoValidaTests(unittest.TestCase):
    def test_401_pasa_a_la_siguiente_y_la_deja_de_usar(self):
        tmp, chunk = chunk_file(self)
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        bad = FakeResponse(401, {}, {"error": {"message": "Invalid API Key"}})
        with Net([bad, FakeResponse(200, {}, OK), FakeResponse(200, {}, OK)]) as net:
            post(eng, CFG2, chunk)
            eng.keys.clock.t += 10
            post(eng, CFG2, chunk)
        self.assertEqual(net.auths(), [f"Bearer {K1}", f"Bearer {K2}", f"Bearer {K2}"])
        self.assertTrue(eng.keys.is_invalid(K1))
        self.assertEqual(eng.keys.public(CFG2, model=TURBO)["invalid"], 1)
        eng.keys.clock.t += engines.INVALID_KEY_S + 1
        self.assertFalse(eng.keys.is_invalid(K1))

    def test_sin_otra_clave_falla_con_el_401(self):
        tmp, chunk = chunk_file(self)
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        with Net([FakeResponse(401, {}, {"error": {"message": "Invalid API Key"}})]) as net:
            with self.assertRaises(engines.EngineError) as cm:
                post(eng, {"provider": "groq", "api_key": K1}, chunk)
        self.assertIn("401", str(cm.exception))
        self.assertEqual(len(net.fake.calls), 1)

    def test_probar_prueba_cada_clave(self):
        tmp, _ = chunk_file(self)
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()

        def flac(path, y, sr=16000):
            with open(path, "wb") as f:
                f.write(b"fLaC")
        with mock.patch.object(engines, "write_flac", flac):
            with Net([FakeResponse(200, {}, OK), FakeResponse(401, {}, {"error": {"message": "Invalid API Key"}})]) \
                    as net:
                with self.assertRaises(engines.EngineError) as cm:
                    eng.test(CFG2)
            self.assertEqual(net.auths(), [f"Bearer {K1}", f"Bearer {K2}"])
            self.assertIn("La API key principal funciona", str(cm.exception))
            self.assertIn("la clave adicional 1 no es válida", str(cm.exception))
            self.assertNotIn(K2, str(cm.exception))
            # con todas bien, lo de siempre: lo que tardo la principal
            with Net([FakeResponse(200, {}, OK), FakeResponse(200, {}, OK)]):
                eng.keys.clock.t += 10
                self.assertGreaterEqual(eng.test(CFG2), 0.0)
            # una adicional sin cupo hoy (429) sigue siendo valida: no se pide quitarla
            with Net([FakeResponse(200, {}, OK), FakeResponse(429, {"retry-after": "3600"}, {})]):
                eng.keys.clock.t += 10
                self.assertGreaterEqual(eng.test(CFG2), 0.0)

    def test_el_chat_tambien_pasa_a_la_siguiente(self):
        s = engines.Summarizer()
        s.keys.clock = Clock()
        with Net([FakeResponse(401, {}, {"error": {"message": "Invalid API Key"}}), FakeResponse(200, {}, CHAT_OK)]) \
                as net:
            out = s.translate(CFG2, "Bom dia.", lambda **k: None, lambda: False)
        self.assertEqual(out[0], "Buenos días.")
        self.assertEqual(net.auths(), [f"Bearer {K1}", f"Bearer {K2}"])


# ----------------------------------------------------------------------------- P2-19: 200 que no es JSON

class SinJsonTests(unittest.TestCase):
    def test_transcripcion(self):
        tmp, chunk = chunk_file(self)
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        with Net([HtmlResponse()]):
            with self.assertRaises(engines.EngineError) as cm:
                post(eng, {"provider": "groq", "api_key": K1}, chunk)
        self.assertIn("sin JSON", str(cm.exception))
        self.assertIn("Inicie sesión en el portal", str(cm.exception))

    def test_chat(self):
        s = engines.Summarizer()
        s.keys.clock = Clock()
        with Net([HtmlResponse()]):
            with self.assertRaises(engines.EngineError) as cm:
                s.translate(CFG2, "Bom dia.", lambda **k: None, lambda: False)
        self.assertIn("sin JSON", str(cm.exception))
        with Net([FakeResponse(200, {}, ["no", "es", "un", "objeto"])]):
            with self.assertRaises(engines.EngineError):
                s.translate(CFG2, "Bom dia.", lambda **k: None, lambda: False)


# ----------------------------------------------------------------------------- P2-20: ajustes vigentes

class AjustesVigentesTests(unittest.TestCase):
    def test_pegar_la_clave_a_mitad_no_tumba_el_directo(self):
        # empezo sin clave y se paso a la nube tras pegarla: antes live.run seguia con la copia del arranque
        r = run_session([(0.0, 3.4), (4.0, 7.6), (8.2, 11.3)], 13.0, cfg={"provider": "groq", "api_key": ""},
                        cfg_fn=lambda rec: {"provider": "groq", "api_key": "k1" if rec.time() > 1004.0 else ""},
                        cloud_cls=KeyCheckingCloud)
        self.assertGreaterEqual(len(r.segs), 2)
        self.assertTrue(any("Directo en pausa: Falta la API key" in s for s in r.job.stages))

    def test_quitar_la_clave_deja_de_mandar(self):
        spans = [(0.0, 3.4), (4.0, 7.6), (8.2, 11.6), (12.2, 15.6), (16.2, 19.6)]
        r = run_session(spans, 22.0, cloud_cls=KeyCheckingCloud,
                        cfg_fn=lambda rec: {"provider": "groq", "api_key": "k1" if rec.time() < 1008.0 else ""})
        self.assertTrue(r.cloud.sent_at)
        self.assertTrue(all(t < 1008.0 + 1.0 for t in r.cloud.sent_at), r.cloud.sent_at)


# ----------------------------------------------------------------------------- P2-21 y P2-22: traduccion

class TraduccionTests(unittest.TestCase):
    def tr(self, segs, summ=None):
        job = DirectoJob(preset="nube")
        job.translate = True
        summ = summ or FakeSummarizer()
        return job, summ, live.LiveTranslator(job, {"summarizer": summ, "cloud_cfg": CFG2}, lambda: (0, segs),
                                              lambda m: None)

    def test_activada_a_mitad_empieza_por_los_ultimos_30_s(self):
        segs = [(i * 5.0, i * 5.0 + 4.5, f"frase{i}") for i in range(120)]  # 10 min dichos antes de activarla
        job, summ, tr = self.tr(segs)
        self.assertTrue(tr.step())
        self.assertGreaterEqual(job.translation[0]["start"], segs[-1][1] - 30 - 5)  # live.TRANSLATE_BACK_S
        self.assertNotIn("frase0 ", summ.calls[0] + " ")
        self.assertIn("frase119", summ.calls[0])

    def test_el_ultimo_paso_no_retiene_el_final(self):
        job, summ, tr = self.tr([(0.0, 2.0, "Bom dia.")])
        tr.lock.acquire()  # el hilo esta a mitad de una peticion
        t0 = time.monotonic()
        with mock.patch.object(live, "TRANSLATE_LOCK_S", 0.2):
            self.assertFalse(tr.step(final=True))  # no se espera a que acabe (lo que se llevo lo traduce el)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertLessEqual(live.TRANSLATE_LOCK_S, 3)
        tr.lock.release()
        self.assertTrue(tr.step(final=True))  # sin nadie: con plazo (todo incluido) y pocos intentos
        self.assertEqual(sorted(summ.kw), ["attempts", "budget", "to"])  # to: el destino (msg 1152), aquí el de siempre
        self.assertEqual(summ.kw["to"], "es")
        self.assertEqual(summ.kw["attempts"], live.TRANSLATE_FINAL_ATTEMPTS)
        self.assertLessEqual(summ.kw["budget"], live.TRANSLATE_FINAL_S)
        self.assertGreater(summ.kw["budget"], live.TRANSLATE_FINAL_S - 1)  # sin esperar al hilo, el plazo entero
        self.assertLessEqual(live.TRANSLATE_FINAL_S, 15)

    def test_translate_hace_un_solo_intento_corto(self):
        s = engines.Summarizer()
        s.keys.clock = Clock()
        with Net([FakeResponse(500, {}, {}), FakeResponse(200, {}, CHAT_OK)]) as net:
            with self.assertRaises(engines.EngineError):
                s.translate(CFG2, "Bom dia.", lambda **k: None, lambda: False, timeout=12, attempts=1)
        self.assertEqual(len(net.fake.calls), 1)
        self.assertEqual(net.fake.calls[0][1]["timeout"], (engines.CONNECT_TIMEOUT, 12))

    def test_run_hace_el_ultimo_paso_con_plazo(self):
        """live.run, no solo step: al terminar, lo que quedo sin traducir sale con el plazo del ultimo paso (sin el
        hilo, que aqui no traduce nada, todo lo pendiente llega a ese paso)."""
        calls = []

        class Summ(FakeSummarizer):
            def translate(self, *a, **kw):
                calls.append(dict(kw))
                return super().translate(*a, **kw)
        with mock.patch.object(live.LiveTranslator, "_loop", lambda tr: None):
            r = run_session([(0.0, 3.4), (4.0, 7.0)], 9.0, translate=True, summ=Summ())
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(sorted(calls[0]), ["attempts", "budget", "to"])
        self.assertEqual(calls[0]["to"], "es")
        self.assertEqual(calls[0]["attempts"], live.TRANSLATE_FINAL_ATTEMPTS)
        self.assertTrue(0 < calls[0]["budget"] <= live.TRANSLATE_FINAL_S)
        self.assertEqual(len(r.job.translation), 1)
        self.assertEqual(r.job.directo["translation_n"], 1)

    def test_run_no_espera_al_hilo_que_tiene_el_candado(self):
        """El hilo esta a mitad de una peticion que no acaba: live.run vuelve en TRANSLATE_LOCK_S, no cuando acaba
        el."""
        held, release = threading.Event(), threading.Event()

        def busy(tr):
            with tr.lock:
                held.set()
                release.wait(8)

        class WaitCloud(FakeCloud):
            def transcribe_array(self, *a, **k):
                held.wait(5)  # el primer trozo sale con el candado ya cogido por el hilo
                return super().transcribe_array(*a, **k)
        summ = FakeSummarizer()
        t0 = time.monotonic()
        try:
            with mock.patch.object(live.LiveTranslator, "_loop", busy), \
                    mock.patch.object(live, "TRANSLATE_LOCK_S", 0.5):
                r = run_session([(0.0, 3.4), (4.0, 7.0)], 9.0, translate=True, summ=summ, cloud_cls=WaitCloud)
            took = time.monotonic() - t0
        finally:
            release.set()
        self.assertTrue(held.is_set())
        self.assertLess(took, 3.0)
        self.assertEqual(summ.calls, [])
        self.assertGreaterEqual(len(r.segs), 1)

    def test_lo_que_se_llevo_el_hilo_se_publica_aunque_llegue_tarde(self):
        job, summ, tr = self.tr([(0.0, 2.0, "Bom dia.")])
        seen = []
        tr.after_stop = lambda: seen.append(len(job.translation))
        tr.stop()  # la sesion termino mientras el hilo traducia
        self.assertTrue(tr.step())
        self.assertEqual(seen, [1])

    def test_lo_que_se_llevo_el_hilo_acaba_aunque_le_toque_esperar(self):
        """La sesion termina mientras la peticion del hilo espera (un 503 y sus 5 s): no se cancela por eso, tiene
        TRANSLATE_FINAL_S para acabar. Cancelar a mano corta en el acto, y el bucle no empieza nada mas."""
        tr = None

        class Summ(FakeSummarizer):
            def translate(self, cfg, text, report, cancelled, **kw):
                tr.stop()  # aqui, a mitad de la espera de la peticion
                if cancelled():  # lo que mira Summarizer._sleep
                    raise engines.Cancelled()
                return super().translate(cfg, text, report, cancelled, **kw)
        job, summ, tr = self.tr([(0.0, 2.0, "Bom dia.")], summ=Summ())
        seen = []
        tr.after_stop = lambda: seen.append(len(job.translation))
        self.assertTrue(tr.step())
        self.assertEqual(seen, [1])
        later = time.time() + live.TRANSLATE_FINAL_S + 1
        with mock.patch.object(live, "time", types.SimpleNamespace(time=lambda: later)):
            self.assertTrue(tr._cancelled())  # pasado el plazo, si
        job.cancel = True
        tr.stop_at = time.time()
        self.assertTrue(tr._cancelled())
        loop = threading.Thread(target=tr._loop)
        job.cancel = False
        loop.start()
        loop.join(2)
        self.assertFalse(loop.is_alive())


class RelojFalso:
    """Reloj de engines (time.time y time.sleep): las esperas y lo que tarda cada peticion lo hacen avanzar. late: lo
    que se pasa cada time.sleep al despertar, como el de verdad (que nunca vuelve justo a tiempo)."""

    def __init__(self, t=1000.0, late=0.0):
        self.t, self.late = t, late

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += max(0.0, dt) + (self.late if dt > 0 else 0.0)

    monotonic = perf_counter = time


class RedLenta(FakeRequests):
    """`requests` falso que tarda: cada paso es (segundos, respuesta) o (None, None) = el servidor no contesta, y vence
    el tope de la peticion (conectar y leer: lo peor)."""

    class Timeout(FakeRequests.RequestException):
        pass

    def __init__(self, clock, steps):
        super().__init__([])
        self.clock, self.steps = clock, list(steps)

    def post(self, url, **kw):
        self.calls.append((url, kw))
        took, resp = self.steps.pop(0)
        if resp is None:
            self.clock.sleep(sum(kw["timeout"]))
            raise self.Timeout("read timed out")
        self.clock.sleep(took)
        return resp


class UltimoPasoConPlazoTests(unittest.TestCase):  # P2-22: el plazo del ultimo paso es el total, no solo las esperas
    BUDGET = 10.0

    def final(self, steps, prep=None, cfg=CFG2, late=0.0):
        clock = RelojFalso(late=late)
        s = engines.Summarizer()
        s.keys.clock = clock.time
        if prep:
            prep(s, clock)
        net = RedLenta(clock, steps)
        t0 = clock.t
        with mock.patch.dict(sys.modules, {"requests": net}), mock.patch.object(engines, "time", clock):
            try:
                out = s.translate(cfg, "Bom dia.", lambda **k: None, lambda: False, budget=self.BUDGET,
                                  attempts=live.TRANSLATE_FINAL_ATTEMPTS)
            except engines.EngineError as e:
                out = e
        return out, clock.t - t0, net

    def check_timeouts(self, net, took):
        for _, kw in net.calls:
            connect, read = kw["timeout"]
            self.assertLessEqual(connect, engines.CONNECT_TIMEOUT)
            self.assertLessEqual(connect + read, self.BUDGET + 1e-6)
        self.assertLessEqual(took, self.BUDGET + 1e-6)

    def test_un_servidor_que_no_contesta_no_pasa_del_plazo(self):
        out, took, net = self.final([(None, None)])
        self.assertIsInstance(out, engines.EngineError)
        self.assertIn("a tiempo", str(out))
        self.assertEqual(len(net.calls), 1)
        self.check_timeouts(net, took)

    def test_una_espera_que_no_cabe_no_se_hace(self):
        """El caso medido: 9 s de cupo por minuto y despues un servidor que no contesta (20 s antes del arreglo)."""
        def prep(s, clock):
            s.remaining, s.reset_at = 0, clock.t + 9
        out, took, net = self.final([(None, None)], prep)
        self.assertIsInstance(out, engines.EngineError)
        self.assertIn("límite por minuto", str(out))
        self.assertEqual(net.calls, [])
        self.assertLess(took, 1)

    def test_una_espera_que_cabe_deja_a_la_peticion_lo_que_queda(self):
        def prep(s, clock):
            s.remaining, s.reset_at = 0, clock.t + 3
        out, took, net = self.final([(None, None)], prep)
        self.assertIsInstance(out, engines.EngineError)
        self.assertEqual(len(net.calls), 1)
        connect, read = net.calls[0][1]["timeout"]
        self.assertLessEqual(4 + connect + read, self.BUDGET + 1e-6)  # 4 s de espera + la peticion
        self.check_timeouts(net, took)

    def test_una_espera_justa_no_se_hace_en_balde(self):
        """Al despertar siempre se pasa algo: una espera que dejaba justo BUDGET_MIN_S acababa sin peticion (7 s de
        cupo por minuto con 10 s de plazo: 7 s esperando y nada). Si espera, pide; si no cabe con margen, falla ya."""
        for wait in (5.0, 6.0, 6.5, 6.6, 7.0, 7.4):
            def prep(s, clock, wait=wait):
                s.remaining, s.reset_at = 0, clock.t + wait - 1  # Summarizer espera reset_at - ahora + 1
            out, took, net = self.final([(0.2, FakeResponse(200, {}, CHAT_OK))], prep, late=0.01)
            if wait + engines.BUDGET_MIN_S + engines.BUDGET_SLACK_S <= self.BUDGET:
                self.assertGreaterEqual(took, wait, wait)  # espero...
                self.assertEqual(len(net.calls), 1, wait)  # ...y pidio
                self.assertEqual(out, ("Buenos días.", "openai/gpt-oss-20b"))
            else:
                self.assertLess(took, 1, wait)  # ni espero
                self.assertEqual(net.calls, [], wait)
                self.assertIn("límite por minuto", str(out))

    def test_lo_que_se_espera_al_hilo_sale_del_plazo(self):
        """step(final=True) entero, con el Summarizer de verdad: el hilo suelta el candado al cabo de 1 s y el servidor
        no contesta. La espera al hilo y la peticion juntas no pasan de TRANSLATE_FINAL_S (eran 11 s: la peticion
        llevaba el plazo entero)."""
        clock = RelojFalso()
        s = engines.Summarizer()
        s.keys.clock = clock.time
        job = DirectoJob(preset="nube")
        job.translate = True
        tr = live.LiveTranslator(job, {"summarizer": s, "cloud_cfg": CFG2}, lambda: (0, [(0.0, 2.0, "Bom dia.")]),
                                 lambda m: None)
        net = RedLenta(clock, [(None, None)] * live.TRANSLATE_FINAL_ATTEMPTS)
        tr.lock.acquire()  # el hilo, a mitad de la suya
        threading.Timer(1.0, tr.lock.release).start()
        t0 = time.monotonic()
        with mock.patch.dict(sys.modules, {"requests": net}), mock.patch.object(engines, "time", clock):
            self.assertFalse(tr.step(final=True))
        waited = time.monotonic() - t0  # de verdad: el candado (la peticion va con el reloj falso)
        self.assertGreaterEqual(waited, 0.9)
        self.assertEqual(len(net.calls), 1)
        self.assertIn("a tiempo", tr.error)
        self.assertLessEqual(waited + (clock.t - 1000.0), live.TRANSLATE_FINAL_S + 0.3)

    def test_429_y_503_en_bucle_dentro_del_plazo(self):
        out, took, net = self.final([(0.2, FakeResponse(429, {"retry-after": "20"}, {}))])
        self.assertIsInstance(out, engines.EngineError)
        self.assertEqual(len(net.calls), 1)  # 20 s de retry-after no caben: ni se espera
        self.check_timeouts(net, took)
        out, took, net = self.final([(0.5, FakeResponse(503, {}, {}))] * 3)
        self.assertIsInstance(out, engines.EngineError)
        self.assertLessEqual(len(net.calls), 2)
        self.check_timeouts(net, took)

    def test_dentro_del_plazo_otra_clave_tras_un_401(self):
        out, took, net = self.final([(0.2, FakeResponse(401, {}, {"error": {"message": "invalid api key"}})),
                                     (0.3, FakeResponse(200, {}, CHAT_OK))])
        self.assertEqual(out, ("Buenos días.", "openai/gpt-oss-20b"))
        self.assertEqual([kw["headers"]["Authorization"] for _, kw in net.calls], [f"Bearer {K1}", f"Bearer {K2}"])
        self.check_timeouts(net, took)

    def test_sin_plazo_como_siempre(self):
        s = engines.Summarizer()
        s.keys.clock = Clock()
        with Net([FakeResponse(200, {}, CHAT_OK)]) as net:
            s.translate(CFG2, "Bom dia.", lambda **k: None, lambda: False)
        self.assertEqual(net.fake.calls[0][1]["timeout"], (engines.CONNECT_TIMEOUT, 60))


# ----------------------------------------------------------------------------- P2-23: cupo por modelo

class CupoPorModeloTests(unittest.TestCase):
    def test_turbo_y_large_v3_no_se_descuentan_uno_al_otro(self):
        p = pool()
        p.note(K1, "whisper", 200, {"x-ratelimit-remaining-requests": "10"}, "whisper-large-v3-turbo")
        self.assertEqual(p.pick(CFG2, "whisper", model="whisper-large-v3-turbo"), K2)  # el del directo: rota
        self.assertEqual(p.pick(CFG2, "whisper", model="whisper-large-v3"), K1)  # el del repaso final: sigue
        self.assertEqual(p.public(CFG2, model="whisper-large-v3-turbo")["active"], 2)
        self.assertIsNone(p.public(CFG2, model="whisper-large-v3")["remaining"])
        p.note(K1, "whisper", 429, {"retry-after": "600"}, "whisper-large-v3")
        self.assertTrue(p.blocked(K1, "whisper", "whisper-large-v3"))
        self.assertFalse(p.blocked(K1, "whisper", "whisper-large-v3-turbo"))
        cfg = {"provider": "groq", "api_key": K1}
        self.assertIsNone(p.live_pace(cfg, model="whisper-large-v3"))
        p.note(K1, "whisper", 200, {"x-ratelimit-remaining-requests": "1500"}, "whisper-large-v3-turbo")
        self.assertEqual(p.live_pace(cfg, model="whisper-large-v3-turbo"), (3, 6))

    def test_el_directo_y_la_cola_llevan_cupos_separados(self):
        tmp, chunk = chunk_file(self)
        eng = engines.CloudEngine(tmp)
        eng.keys.clock = Clock()
        cfg = dict(CFG2, model="whisper-large-v3")
        with Net([FakeResponse(200, {"x-ratelimit-remaining-requests": "10"}, OK), FakeResponse(200, {}, OK)]) as net:
            post(eng, cfg, chunk, model=TURBO)  # el directo deja a la clave 1 casi sin cupo de -turbo
            eng.keys.clock.t += 10
            post(eng, cfg, chunk)  # el repaso final (whisper-large-v3) sigue con ella
        self.assertEqual(net.auths(), [f"Bearer {K1}", f"Bearer {K1}"])

    def test_un_429_corto_marca_cuando_se_puede_volver_a_pedir(self):
        p = pool()
        p.note(K1, "whisper", 429, {"retry-after": "20"}, TURBO)
        self.assertFalse(p.blocked(K1, "whisper", TURBO))
        self.assertAlmostEqual(p.reserve(K1, "whisper", TURBO), 20.0)


# ----------------------------------------------------------------------------- P2-24 y P2-25: chat y Azure

class TraduccionProveedorTests(unittest.TestCase):
    def test_modelo_segun_el_proveedor(self):
        tm = engines.Summarizer.translate_model
        self.assertEqual(tm({"provider": "groq"}), "openai/gpt-oss-20b")
        self.assertEqual(tm({"provider": "openai"}), "gpt-4o-mini")
        self.assertIsNone(tm({"provider": "azure"}))
        self.assertEqual(tm({"provider": "custom", "base_url": "https://gw.example/v1", "summary_model": "mi-chat"}),
                         "mi-chat")

    def test_con_azure_la_traduccion_dice_lo_suyo(self):
        with self.assertRaises(engines.EngineError) as cm:
            engines.Summarizer().translate(AZURE, "Bom dia.", lambda **k: None, lambda: False)
        self.assertIn("La traducción del directo aún no funciona con Azure OpenAI", str(cm.exception))
        self.assertNotIn("resumen", str(cm.exception).lower())

    def test_el_estado_del_directo_lo_dice(self):
        r = run_session([(0.0, 3.4), (4.0, 7.0)], 9.0)
        d = r.job.directo
        self.assertEqual((d["translate_ok"], d["translate_model"], d["provider"]), (True, "openai/gpt-oss-20b", "Groq"))
        r = run_session([(0.0, 3.4), (4.0, 7.0)], 9.0, cfg=AZURE)
        d = r.job.directo
        self.assertFalse(d["translate_ok"])
        self.assertIn("Azure OpenAI", d["translate_why"])
        self.assertIsNone(d["translate_model"])

    def test_la_api_no_la_enciende_sin_chat(self):
        tmp = tempfile.mkdtemp(prefix="notas-tr-az-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        job = server.Job("a.wav", "a.wav", "nube", None)
        job.live, job.status = True, "running"
        app = server.App.__new__(server.App)
        app.jobs = bare_jobs([job])
        app.settings = server.Store(os.path.join(tmp, "s.json"), server.DEFAULT_SETTINGS)
        app.settings.update(lambda d: d["cloud"].update(**AZURE))
        with self.assertRaises(server.ApiError) as cm:
            app.live_translate({"id": job.id, "on": True})
        self.assertIn("Azure OpenAI", cm.exception.msg)
        self.assertFalse(job.translate)
        app.live_translate({"id": job.id, "on": False})  # apagarla siempre se puede


class ResumenAzureTests(TempTree):
    def test_el_resumen_dice_que_no_funciona_con_azure(self):
        with self.assertRaises(engines.EngineError) as cm:
            engines.Summarizer()._chat(AZURE, "s", "u", 10, lambda **k: None, lambda: False)
        self.assertIn("El resumen con IA aún no funciona con Azure OpenAI", str(cm.exception))
        jobs = bare_jobs([])
        jobs.settings = server.Store(os.path.join(self.data_dir, "s.json"), server.DEFAULT_SETTINGS)
        jobs.settings.update(lambda d: d["cloud"].update(**AZURE))
        self.write("r.srt", "1\n00:00:00,000 --> 00:00:01,000\nhola\n")
        self.wav("r.wav", seconds=1.0)
        with self.assertRaises(server.ApiError) as cm:
            jobs.submit_summary("r.wav")
        self.assertIn("Azure OpenAI", cm.exception.msg)
        self.assertNotIn("API key", cm.exception.msg)


if __name__ == "__main__":
    unittest.main()
