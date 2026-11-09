# -*- coding: utf-8 -*-
"""Cambiar el idioma en pleno directo (ítem 12, msgs 1150-1151: «funcionó, pero tardó») y la traducción del directo a
español o inglés (ítem 13a, msg 1152).

- Lo puro: desde qué frase se rehace (redo_start, nunca una corregida a mano), desde qué segundo (redo_from) y lo que
  dice el aviso (lang_switch_info: al día con su ~N s, poniéndose al día, en pausa).
- Sesiones enteras de live.run con el reloj falso de test_directo: el idioma cambia con el directo parado entre trozos,
  con un trozo de la nube en vuelo (se descarta y se repite), con un trozo local en vuelo (se cancela: el proceso de
  Whisper se mataría), dos veces seguidas, y cuando el trozo que rehace falla (vuelve lo de antes).
- El traductor: rebobinar quita lo traducido de lo que se rehace, una traducción en vuelo de antes no se publica, y al
  cambiar el destino se vuelve a traducir lo último al idioma nuevo (con su prompt).
- El servidor: refresh_prompt y notes_save devuelven el aviso; /api/live/traducir con «to», validado y recordado por
  reunión; el destino por defecto es el idioma del resumen si es es/en.
Todo con dobles: sin red, sin faster-whisper."""
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
from unittest import mock

from support import TempTree, bare_jobs, engines, live, server  # noqa: F401  (support prepara sys.path)
import plan  # noqa: E402
from test_directo import (CFG2, Clock, DirectoJob, FakeCloud, FakeRecorder, FakeRequests, FakeResponse,  # noqa: E402
                          FakeSummarizer, FakeWorker, energy_vad, synth)
from test_frontend import js_defs, read  # noqa: E402

SR = live.SR


# ----------------------------------------------------------------------------- lo puro

class RedoStartTests(unittest.TestCase):
    segs = [(0.0, 3.0, "a"), (3.5, 7.0, "b"), (30.0, 34.0, "c"), (50.0, 55.0, "d"), (70.0, 80.0, "e")]

    def test_la_primera_frase_del_ultimo_minuto(self):
        # el texto acaba en 80 s: se rehace desde la primera frase que empieza en 20 s o despues
        self.assertEqual(live.redo_start(self.segs), 2)
        self.assertEqual(live.redo_start(self.segs, back_s=30), 3)
        self.assertEqual(live.redo_start(self.segs[:2]), 0)  # todo cabe en el minuto
        self.assertEqual(live.redo_start([]), 0)

    def test_nunca_una_corregida_a_mano_ni_las_anteriores(self):
        edits = {live.seg_key(50.0): "corregida"}
        self.assertEqual(live.redo_start(self.segs, edits), 4)  # solo lo de despues de la corregida
        edits = {live.seg_key(70.0): ""}  # la ultima, borrada a mano: nada que rehacer
        self.assertEqual(live.redo_start(self.segs, edits), len(self.segs))
        edits = {live.seg_key(3.5): "x"}  # una corregida fuera del minuto no cambia nada
        self.assertEqual(live.redo_start(self.segs, edits), 2)

    def test_seg_key_es_el_del_servidor(self):
        for s in (0.0, 1.2345, 12.0049, 3599.999, 7.005):
            self.assertEqual(live.seg_key(s), server.seg_key(s), s)

    def test_lo_que_vuelve_si_el_trozo_que_rehace_falla(self):
        removed = [(10.0, 13.0, "a"), (14.0, 18.5, "b"), (19.0, 22.0, "c")]
        self.assertEqual(live.back_of(removed, 30.0), removed)
        # el corte cae dentro de «b»: vuelve «a»; «b» la transcribe el trozo siguiente (que empieza en el corte)
        self.assertEqual(live.back_of(removed, 16.0), removed[:1])
        self.assertEqual(live.back_of(removed, 18.52), removed[:2])  # un redondeo en el borde no la deja fuera
        self.assertEqual(live.back_of(removed, 5.0), [])

    def test_desde_que_segundo(self):
        self.assertEqual(live.redo_from(self.segs, 2), 7.0)  # hueco antes: desde el final de la anterior
        solape = [(0.0, 4.2, "a"), (4.0, 6.0, "b")]
        self.assertEqual(live.redo_from(solape, 1), 4.0)  # solape: desde su inicio
        self.assertEqual(live.redo_from(self.segs, 0), 0.0)
        self.assertEqual(live.redo_from([], 0), 0.0)


class LangSwitchInfoTests(unittest.TestCase):
    segs = [(i * 4.0, i * 4.0 + 3.5, f"f{i}") for i in range(30)]  # 0 .. 119,5 s

    def d(self, **kw):
        base = {"cloud": True, "lang": "en", "behind_s": 4.0, "caught_up": True, "paused": False,
                "latency": {"phases": {"net": {"p50": 1.2}}}, "speed": None}
        base.update(kw)
        return base

    def test_sin_cambio(self):
        self.assertEqual(live.lang_switch_info(self.d(), self.segs, {}, "pt", "pt"), {"sin_cambio": True})
        self.assertEqual(live.lang_switch_info(self.d(), self.segs, {}, "", None), {"sin_cambio": True})

    def test_al_dia_en_la_nube(self):
        i = live.lang_switch_info(self.d(), self.segs, {}, "pt", "en")
        self.assertEqual((i["estado"], i["idioma"], i["detras_s"]), ("al_dia", "pt", 4))
        self.assertEqual(i["eta_s"], 4)  # 1 + 2 x 1,2 s de red, hacia arriba
        self.assertTrue(56 <= i["rehace_s"] <= 60, i)
        # sin medir aun la red: 1 + 2 x 1,5
        self.assertEqual(live.lang_switch_info(self.d(latency={}), self.segs, {}, "pt", "en")["eta_s"], 4)

    def test_en_local_cuenta_lo_que_tarda_whisper(self):
        i = live.lang_switch_info(self.d(cloud=False, speed=2.3, behind_s=20.0), self.segs, {}, "pt", "en")
        # (59,5 s rehechos + 20 s por detras) / 2,3 + 5 s de recargar el modelo = ~40 s
        self.assertEqual((i["estado"], i["eta_s"]), ("al_dia", 40))

    def test_poniendose_al_dia_y_en_pausa(self):
        self.assertEqual(live.lang_switch_info(self.d(behind_s=45.0), self.segs, {}, "pt", "en")["estado"],
                         "poniendose")
        i = live.lang_switch_info(self.d(cloud=False, caught_up=False, behind_s=200.0), self.segs, {}, "pt", "en")
        self.assertEqual((i["estado"], i["eta_s"], i["detras_s"]), ("poniendose", None, 200))
        self.assertEqual(live.lang_switch_info(self.d(paused=True), self.segs, {}, "pt", "en")["estado"], "pausa")
        # sin estado publicado aun (el directo acaba de empezar): al dia, con la red por defecto
        self.assertEqual(live.lang_switch_info(None, [], {}, "pt", "en"),
                         {"idioma": "pt", "estado": "al_dia", "eta_s": 5, "rehace_s": 0, "detras_s": 0})

    def test_lo_que_se_rehace(self):
        # ya escrito en ese idioma (detectado): no se rehace nada
        self.assertEqual(live.lang_switch_info(self.d(lang="pt"), self.segs, {}, "pt", None)["rehace_s"], 0)
        # a «detectar»: si se rehace (no se sabe en que idioma se hablo)
        self.assertGreater(live.lang_switch_info(self.d(), self.segs, {}, None, "en")["rehace_s"], 50)
        # una corregida a mano en el minuto: solo lo de despues
        edits = {live.seg_key(112.0): "corregida"}
        self.assertEqual(live.lang_switch_info(self.d(), self.segs, edits, "pt", "en")["rehace_s"], 4)


# ----------------------------------------------------------------------------- sesiones con el reloj falso

class LangCloud(FakeCloud):
    """Nube falsa que escribe el idioma pedido en el texto («[pt] trozo 3») y, si se le dice, cambia el idioma de la
    reunión en `opts` a mitad de una petición (on_call: {nº de llamada: idioma}), como Jobs.refresh_prompt."""

    def __init__(self, rec, opts=None, on_call=None, fail_on=()):
        super().__init__(rec, fail_on=fail_on)
        self.opts, self.on_call, self.stops = opts, dict(on_call or {}), []

    def transcribe_array(self, cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw):
        n = len(self.calls) + 1
        if n in self.on_call:
            self.opts["language"] = self.on_call[n]
        out, det = super().transcribe_array(cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw)
        self.stops.append(bool(cancelled()))  # con el idioma cambiado, el trozo se daria por cortado
        secs = len(audio) / SR
        return [(0.0, secs, f"[{language}] trozo {n}")], {"pt": "portuguese", "en": "english"}.get(language, det)


class CancellableWorker(FakeWorker):
    """Whisper local falso que tarda `infer_s` de reloj falso por ventana y mira cancelled() cada 0,25 s, como
    WorkerClient.request (que entonces mata el proceso). on_call: {nº de ventana: (segundo de inferencia, idioma)}."""

    def __init__(self, rec, opts, on_call=None, infer_s=4.0):
        super().__init__(rec)
        self.opts, self.on_call, self.infer_s, self.reqs, self.cancelled_at = opts, dict(on_call or {}), infer_s, [], []

    def request(self, req, audio=None, on_progress=None, timeout=None, idle_timeout=None, cancelled=None):
        n = len(self.reqs) + 1
        self.reqs.append(dict(req, secs=len(audio) / SR))
        when, lang = self.on_call.get(n, (None, None))
        t = 0.0
        while t < self.infer_s:
            if when is not None and t >= when and self.opts.get("language") != lang:
                self.opts["language"] = lang
            if cancelled and cancelled():
                self.cancelled_at.append((n, t))
                raise engines.Cancelled()
            self.rec.sleep(0.25)
            t += 0.25
        secs = len(audio) / SR
        return {"language": req["language"] or "es", "prompt": None,
                "segments": [(0.0, secs, f"[{req['language']}] local {n}")]}


class TimedRecorder(FakeRecorder):
    """FakeRecorder con sucesos a una hora del reloj falso (segundos desde el inicio): cambia el idioma en opts
    mientras el directo espera entre trozos."""

    def __init__(self, path, y, events=()):
        super().__init__(path, y)
        self.events = sorted(events)

    def sleep(self, dt):
        super().sleep(dt)
        while self.events and self.t - self.t0 >= self.events[0][0]:
            self.events.pop(0)[1]()


SPANS = [(0.0, 3.4), (4.0, 7.6), (8.2, 11.3), (11.9, 15.0), (16.0, 19.2), (20.0, 23.1), (24.0, 27.0),
         (28.0, 31.0)]


class LiveSwitchTests(unittest.TestCase):
    def session(self, spans, total, preset="nube", cloud_kw=None, worker_kw=None, events=(), translate=False,
                edits=None):
        tmp = tempfile.mkdtemp(prefix="notas-idioma-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "reunion.wav")
        opts = {"preset": preset, "language": None, "prompt": None, "about": None, "threads": 1, "tmp_dir": tmp,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "resume": [], "summarizer": FakeSummarizer()}
        rec = TimedRecorder(path, synth(spans, total), [(t, (lambda v=v: opts.update(language=v))) for t, v in events])
        cloud = LangCloud(rec, opts, **(cloud_kw or {}))
        worker = CancellableWorker(rec, opts, **(worker_kw or {}))
        opts.update(cloud=cloud, worker=worker)
        job = DirectoJob(preset=preset)
        job.translate = translate
        if edits:
            job.edits = edits
        writes, logs = [], []
        fake_time = types.SimpleNamespace(time=rec.time, sleep=rec.sleep)
        with mock.patch.object(live, "time", fake_time), mock.patch.object(live, "speech", energy_vad):
            rec.sleep(0.6)
            segs, lang = live.run(job, path, opts, lambda s: writes.append(list(s)), logs.append)
        return types.SimpleNamespace(segs=segs, lang=lang, cloud=cloud, worker=worker, job=job, writes=writes,
                                     logs=logs, opts=opts)

    def test_parado_entre_trozos_rehace_el_ultimo_minuto_al_momento(self):
        # el idioma pasa a portugués a los 26 s, entre dos trozos (el sexto vuelve a los 24,9 s y el séptimo sale a
        # los ~27,7 s): lo escrito hasta ahí (todo dentro del minuto) se rehace en una sola petición, al momento, y lo
        # nuevo ya sale en portugués
        r = self.session(SPANS, 34.0, events=[(26.0, "pt")])
        langs = [c["language"] for c in r.cloud.calls]
        k = langs.index("pt")
        self.assertTrue(all(x is None for x in langs[:k]) and all(x == "pt" for x in langs[k:]), langs)
        redo = r.cloud.calls[k]
        self.assertLess(redo["at"] - 1000.0, 26.0 + 0.6)  # en el siguiente sondeo (0,5 s), no en el siguiente trozo
        self.assertFalse(any("durante el trozo" in x for x in r.logs), r.logs)  # no habia nada en vuelo
        self.assertGreater(redo["secs"], 20.0)  # lo de antes (desde el primer segundo) en una peticion
        self.assertTrue(all(t.startswith("[pt]") for _, _, t in r.segs), r.segs)  # no queda nada en el de antes
        starts = [a for a, _, _ in r.segs]
        self.assertEqual(starts, sorted(starts))
        # lo viejo sigue en pantalla hasta que llega lo nuevo: ninguna escritura sin lo de antes ni lo de ahora
        for w in r.writes:
            self.assertTrue(w and (w[0][2].startswith("[None]") or w[0][2].startswith("[pt]")), w)
        before = [w for w in r.writes if w[0][2].startswith("[None]")]
        self.assertEqual(len(before[-1]), langs.index("pt"))  # la ultima con lo de antes lo tenia todo
        self.assertTrue(any("idioma pt desde ya; se rehacen en él los últimos" in x for x in r.logs), r.logs)
        self.assertTrue(any("rehecho en pt lo último" in x for x in r.logs), r.logs)
        self.assertEqual((r.lang, r.job.directo["lang"]), ("pt", "pt"))
        # la latencia se mide desde el final del audio de cada trozo: el que rehace acaba en lo ultimo grabado, asi
        # que no la dispara aunque empiece un minuto atras
        self.assertLess(r.job.directo["latency"]["p95"], 5.0)

    def test_con_un_trozo_de_la_nube_en_vuelo_se_descarta_y_se_repite(self):
        r = self.session(SPANS, 34.0, cloud_kw={"on_call": {3: "pt"}})
        langs = [c["language"] for c in r.cloud.calls]
        self.assertEqual(langs[:4], [None, None, None, "pt"], langs)
        self.assertEqual(r.cloud.stops[2], True)  # el tercero, con el idioma ya cambiado: se habria cortado
        self.assertFalse(any("trozo 3" in t for _, _, t in r.segs))  # su texto no se escribe
        self.assertGreater(r.cloud.calls[3]["secs"], r.cloud.calls[2]["secs"] + 5)  # se repite con lo de antes
        self.assertTrue(all(t.startswith("[pt]") for _, _, t in r.segs), r.segs)
        self.assertTrue(any("el idioma ha cambiado durante el trozo" in x for x in r.logs), r.logs)

    def test_si_el_trozo_que_rehace_falla_vuelve_lo_de_antes(self):
        r = self.session(SPANS, 34.0, cloud_kw={"on_call": {4: "pt"}, "fail_on": (5,)})
        self.assertEqual([c["language"] for c in r.cloud.calls][3:5], [None, "pt"])
        old = [t for _, _, t in r.segs if t.startswith("[None]")]
        self.assertEqual(len(old), 3, r.segs)  # los tres de antes, de vuelta (mejor que un hueco)
        self.assertTrue(all(t.startswith("[pt]") for _, _, t in r.segs[3:]), r.segs)
        self.assertTrue(any("se queda el texto de antes (3 frases)" in x for x in r.logs), r.logs)

    def test_dos_cambios_seguidos_no_retroceden_mas(self):
        # pt con el cuarto en vuelo; con el que rehace (pt) en vuelo, en: se repite desde el mismo sitio, en ingles
        r = self.session(SPANS, 34.0, cloud_kw={"on_call": {4: "pt", 5: "en"}})
        calls = r.cloud.calls
        self.assertEqual([c["language"] for c in calls][3:6], [None, "pt", "en"])
        self.assertAlmostEqual(calls[5]["secs"], calls[4]["secs"], delta=0.6)
        self.assertTrue(all(t.startswith("[en]") for _, _, t in r.segs), r.segs)
        self.assertTrue(any("se sigue rehaciendo desde" in x for x in r.logs), r.logs)

    def test_corregida_a_mano_no_se_rehace(self):
        # la segunda frase (4,0 s) corregida a mano: se rehace solo desde la tercera
        r = self.session(SPANS, 34.0, events=[(16.5, "pt")], edits={live.seg_key(3.8): "corregida"})
        texts = [t for _, _, t in r.segs]
        self.assertTrue(texts[0].startswith("[None]") and texts[1].startswith("[None]"), texts)
        self.assertTrue(all(t.startswith("[pt]") for t in texts[2:]), texts)

    def test_en_local_el_trozo_en_vuelo_se_cancela(self):
        spans = SPANS + [(32.0, 35.0), (36.0, 39.0), (40.0, 43.0), (44.0, 47.0), (48.0, 51.0)]
        r = self.session(spans, 56.0, preset="medium", worker_kw={"on_call": {2: (1.0, "pt")}, "infer_s": 4.0})
        self.assertEqual(r.worker.cancelled_at, [(2, 1.0)])  # cortado al segundo, no al acabar los 4 s
        langs = [q["language"] for q in r.worker.reqs]
        self.assertEqual(langs[:3], [None, None, "pt"], langs)
        self.assertGreater(r.worker.reqs[2]["secs"], r.worker.reqs[1]["secs"])  # lo de antes y el cortado
        self.assertTrue(all("[pt]" in t for _, _, t in r.segs), r.segs)
        self.assertTrue(any("el trozo en curso se repite con el idioma nuevo" in x for x in r.logs), r.logs)

    def test_detener_sigue_deteniendo(self):
        # cancelar a mano no se confunde con un cambio de idioma: la sesion termina como siempre
        tmp = tempfile.mkdtemp(prefix="notas-idioma-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "r.wav")
        opts = {"preset": "medium", "language": None, "prompt": None, "about": None, "threads": 1, "tmp_dir": tmp,
                "cloud_cfg": {}, "resume": [], "summarizer": None}
        rec = TimedRecorder(path, synth(SPANS, 34.0))
        job = DirectoJob(preset="medium")
        worker = CancellableWorker(rec, opts)
        orig = worker.request

        def request(*a, **kw):
            job.cancel = True
            return orig(*a, **kw)
        worker.request = request
        opts.update(worker=worker, cloud=LangCloud(rec, opts))
        with mock.patch.object(live, "time", types.SimpleNamespace(time=rec.time, sleep=rec.sleep)), \
                mock.patch.object(live, "speech", energy_vad):
            rec.sleep(0.6)
            with self.assertRaises(engines.Cancelled):
                live.run(job, path, opts, lambda s: None, lambda m: None)

    def test_al_idioma_que_ya_se_detectaba_no_se_corta_nada(self):
        # con «Detectar», Whisper dice «es»; fijar «es» con una ventana local en vuelo no la mata (repetir hasta 5 min
        # de audio para nada, revisión P2-4) ni rehace lo escrito
        spans = SPANS + [(32.0, 35.0), (36.0, 39.0), (40.0, 43.0), (44.0, 47.0), (48.0, 51.0)]
        r = self.session(spans, 56.0, preset="medium", worker_kw={"on_call": {2: (1.0, "es")}, "infer_s": 4.0})
        self.assertEqual(r.worker.cancelled_at, [])
        self.assertFalse(any("se rehacen" in x or "se repite" in x for x in r.logs), r.logs)
        self.assertTrue(any("idioma es desde ya" in x for x in r.logs), r.logs)
        self.assertEqual([q["language"] for q in r.worker.reqs][:3], [None, None, "es"])

    def test_lo_retomado_tras_reiniciar_no_se_rehace(self):
        # la app se reinició a mitad de reunión: lo de antes vuelve del .srt con las correcciones ya dentro, sin
        # constar como tales (revisión P2-1); al cambiar el idioma solo se rehace lo de esta sesión
        tmp = tempfile.mkdtemp(prefix="notas-idioma-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "reunion.wav")
        resume = [(0.0, 3.4, "retomada uno"), (4.0, 7.6, "retomada dos")]
        opts = {"preset": "nube", "language": None, "prompt": None, "about": None, "threads": 1, "tmp_dir": tmp,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "resume": list(resume),
                "summarizer": FakeSummarizer()}
        rec = TimedRecorder(path, synth(SPANS, 34.0), [(26.0, lambda: opts.update(language="pt"))])
        opts.update(cloud=LangCloud(rec, opts), worker=CancellableWorker(rec, opts))
        job = DirectoJob(preset="nube")
        logs = []
        with mock.patch.object(live, "time", types.SimpleNamespace(time=rec.time, sleep=rec.sleep)), \
                mock.patch.object(live, "speech", energy_vad):
            rec.sleep(0.6)
            segs, _ = live.run(job, path, opts, lambda s: None, logs.append)
        self.assertEqual(segs[:2], resume)
        self.assertTrue(any("se rehacen en él" in x for x in logs), logs)
        self.assertTrue(all(t.startswith("[pt]") for _, _, t in segs[2:]), segs)
        self.assertEqual(job.directo["protegidas"], 2)

    def test_idiomas_usados_para_el_repaso_final(self):
        r = self.session(SPANS, 34.0, events=[(10.0, "en"), (26.0, "pt")])
        self.assertEqual(r.job.idiomas_directo, [None, "en", "pt"])
        r = self.session(SPANS, 34.0)
        self.assertFalse(hasattr(r.job, "idiomas_directo"))

    def test_sin_cambio_de_idioma_todo_igual(self):
        r = self.session(SPANS, 34.0)
        self.assertTrue(all(c["language"] is None for c in r.cloud.calls))
        self.assertFalse(any("idioma" in x for x in r.logs), r.logs)
        self.assertEqual(len(r.segs), len(r.cloud.calls))
        self.assertFalse(r.job.directo["redo"])

    def test_el_destino_va_en_el_estado_publicado(self):
        r = self.session([(0.0, 3.4), (4.0, 7.0)], 9.0, translate=True)
        self.assertEqual(r.job.directo["translate_to"], "es")
        self.assertIn("translation_epoch", r.job.directo)

    def test_la_traduccion_se_rehace_con_el_texto_nuevo(self):
        r = self.session(SPANS, 34.0, events=[(26.0, "pt")], translate=True)
        texts = [b["text"] for b in r.job.translation]
        self.assertTrue(texts, r.job.translation)
        self.assertFalse(any("[None]" in t for t in texts), texts)  # lo traducido de antes se quito
        self.assertTrue(texts[-1].startswith("ES: ") and "[pt]" in texts[-1], texts)


# ----------------------------------------------------------------------------- corregir a mano y cambiar el idioma

class MultiCloud(LangCloud):
    """Como Whisper: varias frases por trozo (una cada ~4 s, la primera a 0,3 s del inicio del audio)."""

    def transcribe_array(self, cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw):
        _, det = super().transcribe_array(cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw)
        secs, n = len(audio) / SR, len(self.calls)
        out, t, i = [], 0.3, 0
        while t < secs - 0.5:
            out.append((t, min(secs, t + 3.5), f"[{language}] t{n}.{i}"))
            t, i = t + 4.0, i + 1
        return out, det


class VozCloud(LangCloud):
    """Nube falsa «como Whisper» de verdad: una frase por cada tramo de voz que oye, con sus tiempos (Whisper no corta
    donde cortaba la vez anterior). antes(n, lenguaje, job): un gancho al empezar cada petición."""

    def __init__(self, rec, opts, antes=None, **kw):
        super().__init__(rec, opts, **kw)
        self.antes, self.job = antes, None

    def transcribe_array(self, cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw):
        if self.antes:
            self.antes(len(self.calls) + 1, language, self.job)
        _, det = super().transcribe_array(cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw)
        n = len(self.calls)
        return [(t["start"] / SR, t["end"] / SR, f"[{language}] v{n}.{i}")
                for i, t in enumerate(energy_vad(audio))], det


def huecos_y_repetidos(out, spans=SPANS, total=None):
    """(tramos de voz sin ningún texto, pares de frases que se pisan más de 0,5 s)."""
    sin = [(a, b) for a, b in spans if (total is None or b <= total)
           and not any(s <= a + 0.3 and e >= b - 0.3 for s, e, _ in out)]
    pisan = [(x[2], y[2]) for x, y in zip(out, out[1:]) if y[0] < x[1] - 0.5]
    return sin, pisan


class EdicionEnDirectoTests(TempTree):
    """Revisión de idioma-directo (P1): corrigiendo en «Editar» y cambiando el idioma, lo rehecho no le llegaba al
    editor y, al guardar, se daba por borrado (y la corrección se perdía). Ahora el escritor del directo sube
    text_gen al rehacer, /api/transcript la da con el texto y lo que se guarda sobre una generación anterior no toca lo
    rehecho (rehecho_desde)."""

    def sesion(self, cloud_kw=None, events=()):
        path = os.path.join(self.root, "Alfa", "reunion.wav")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        opts = {"preset": "nube", "language": None, "prompt": None, "about": None, "threads": 1, "tmp_dir": self.tmp,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "resume": [], "summarizer": FakeSummarizer()}
        rec = TimedRecorder(path, synth(SPANS, 34.0), [(t, (lambda v=v: opts.update(language=v))) for t, v in events])
        cloud = MultiCloud(rec, opts, **(cloud_kw or {}))
        opts.update(cloud=cloud, worker=CancellableWorker(rec, opts))
        job = server.Job("Alfa/reunion.wav", "reunion.wav", "nube", None)
        job.live, job.status, job.out = True, "running", server.LiveOutputs(path)
        snaps = []
        escribe = server.live_writer(job)

        def writer(segs):
            escribe(segs)
            snaps.append((job.text_gen, list(job.raw)))
        logs = []
        with mock.patch.object(live, "time", types.SimpleNamespace(time=rec.time, sleep=rec.sleep)), \
                mock.patch.object(live, "speech", energy_vad), mock.patch.object(server, "log"):
            rec.sleep(0.6)
            live.run(job, path, opts, writer, logs.append)
        return job, snaps, path, logs

    def app(self, job):
        a = server.App.__new__(server.App)
        a.library = types.SimpleNamespace(at=1)
        a.transcripts = server.Transcripts()
        a.jobs = types.SimpleNamespace(active_for=lambda rel, kinds=("transcribe",): job)
        return a

    def test_guardar_sobre_el_texto_de_antes_no_borra_lo_rehecho(self):
        job, snaps, path, _ = self.sesion(events=[(26.0, "pt")])
        self.assertEqual(len(job.rewrites), 1)  # una vez: el cambio de idioma
        gen0 = snaps[0][0]  # la generación con que empezó
        self.assertGreater(job.text_gen, gen0)
        corte = job.rewrites[0][1]
        antes = [s for g, s in snaps if g == gen0][-1]  # lo que tenía el editor (la primera generación)
        self.assertTrue(all(t.startswith("[None]") for _, _, t in antes))
        ed = [list(s) for s in antes] + [list(s) for s in job.raw if s[0] > antes[-1][0] + 0.005]  # appendLive viejo
        ed[0][2] = "corregida a mano"  # la primera frase, que se ha rehecho
        with mock.patch.object(server, "log"):
            r = self.app(job)._save_live_edits(job, [tuple(x) for x in ed], os.path.splitext(path)[0], gen=gen0)
        self.assertEqual(r["rehecho_desde"], corte)
        self.assertEqual(job.edits, {})  # ni «frase borrada» en lo rehecho ni la corrección sobre lo que ya no existe
        with open(os.path.splitext(path)[0] + ".txt", encoding="utf-8-sig") as f:
            txt = f.read().split("\n")
        self.assertEqual([x for x in txt if x], [t for _, _, t in job.raw])
        self.assertTrue(all("[pt]" in x for x in txt if x), txt)
        # con la generación de ahora (el editor ya recargado), una corrección vale como siempre
        nuevo = [list(s) for s in job.raw]
        nuevo[1][2] = "bien corregida"
        with mock.patch.object(server, "log"):
            r = self.app(job)._save_live_edits(job, [tuple(x) for x in nuevo], os.path.splitext(path)[0],
                                               gen=job.text_gen)
        self.assertIsNone(r["rehecho_desde"])
        self.assertEqual(list(job.edits.values()), ["bien corregida"])

    def test_sin_generacion_como_antes(self):
        # una interfaz anterior no manda «gen»: el comportamiento de siempre (borrar lo que no está y es anterior)
        job, snaps, path, _ = self.sesion()
        self.assertEqual(job.rewrites, [])
        ed = [list(s) for s in job.raw][1:]  # la primera, borrada a mano
        with mock.patch.object(server, "log"):
            r = self.app(job)._save_live_edits(job, [tuple(x) for x in ed], os.path.splitext(path)[0])
        self.assertIsNone(r["rehecho_desde"])
        self.assertEqual(list(job.edits.values()), [""])

    def test_el_texto_va_con_su_generacion(self):
        job, snaps, path, _ = self.sesion(events=[(26.0, "pt")])
        out = self.app(job).transcript({"path": "Alfa/reunion.wav"})
        self.assertEqual(out["text_gen"], job.text_gen)
        self.assertGreater(job.text_gen, snaps[0][0])
        self.assertEqual([s["text"] for s in out["segments"]], [t for _, _, t in job.raw])
        job.status = "done"
        a = self.app(None)
        self.assertNotIn("text_gen", a.transcript({"path": "Alfa/reunion.wav"}))  # sin directo, nada

    def sesion_voz(self, antes=None, fail_on=(), on_call=None):
        path = os.path.join(self.root, "Alfa", "reunion.wav")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        opts = {"preset": "nube", "language": None, "prompt": None, "about": None, "threads": 1, "tmp_dir": self.tmp,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "resume": [], "summarizer": FakeSummarizer()}
        rec = TimedRecorder(path, synth(SPANS, 34.0))
        cloud = VozCloud(rec, opts, antes=antes, on_call=on_call or {7: "pt"}, fail_on=fail_on)
        opts.update(cloud=cloud, worker=CancellableWorker(rec, opts))
        job = server.Job("Alfa/reunion.wav", "reunion.wav", "nube", None)
        job.live, job.status, job.out = True, "running", server.LiveOutputs(path)
        cloud.job = job
        logs = []
        with mock.patch.object(live, "time", types.SimpleNamespace(time=rec.time, sleep=rec.sleep)), \
                mock.patch.object(live, "speech", energy_vad), mock.patch.object(server, "log"):
            rec.sleep(0.6)
            live.run(job, path, opts, server.live_writer(job), logs.append)
        return job, cloud, logs

    def test_corregida_mientras_se_rehace_sin_huecos_ni_repetidos(self):
        # revisión 2 (P1): al mandar la corrección, quedarse con parte del trozo que rehacía dejaba un hueco (la frase
        # nueva que cruzaba donde acababa lo de antes) o, si ese trozo acababa antes, frases repetidas
        hecho = []

        def corrige(n, language, job):
            if language == "pt" and not hecho:
                with job.edit_lock:
                    job.edits[server.seg_key(job.raw[1][0])] = "corregida mientras tanto"
                hecho.append(n)
        job, cloud, logs = self.sesion_voz(antes=corrige)
        self.assertTrue(hecho)
        self.assertTrue(any("se corrigió a mano entretanto" in x for x in logs), logs)
        out = server.apply_edits(job.raw, job.edits)
        self.assertIn("corregida mientras tanto", [t for _, _, t in out])
        self.assertEqual(huecos_y_repetidos(out), ([], []), out)
        self.assertEqual([a for a, _, _ in out], sorted(a for a, _, _ in out))

    def test_si_el_trozo_que_rehace_falla_sin_huecos(self):
        # revisión 2 (P2-3): vuelve lo de antes que acaba antes del corte y lo de detrás se relee desde ahí
        job, cloud, logs = self.sesion_voz(fail_on=(8,))
        self.assertEqual([c["language"] for c in cloud.calls][6:8], [None, "pt"])
        out = server.apply_edits(job.raw, job.edits)
        self.assertEqual(huecos_y_repetidos(out), ([], []), out)
        self.assertTrue(any("se queda el texto de antes" in x for x in logs), logs)

    def test_con_voz_de_verdad_tambien(self):
        job, cloud, logs = self.sesion_voz()
        out = server.apply_edits(job.raw, job.edits)
        self.assertEqual(huecos_y_repetidos(out), ([], []), out)
        self.assertTrue(all(t.startswith("[pt]") for _, _, t in out), out)

    def test_la_generacion_de_otro_directo_no_tira_correcciones(self):
        # revisión 2 (P2-1): «Pausar transcripción» y otra vez «Transcribir en directo» sin salir de «Editar»: el
        # editor manda la generación del directo anterior; el nuevo empezó con el texto del archivo
        job1, _, _ = self.sesion_voz()
        self.assertEqual(len(job1.rewrites), 1)
        job2 = server.Job("Alfa/reunion.wav", "reunion.wav", "nube", None)
        job2.live, job2.status = True, "running"
        job2.out = server.LiveOutputs(os.path.join(self.root, "Alfa", "reunion.wav"))
        server.live_writer(job2)(list(job1.raw))  # retoma con lo escrito
        self.assertGreater(job2.text_gen, job1.text_gen)
        ed = [list(x) for x in job2.raw]
        ed[0][2] = "corregida sobre lo retomado"
        with mock.patch.object(server, "log"):
            r = self.app(job2)._save_live_edits(job2, [tuple(x) for x in ed],
                                                os.path.join(self.root, "Alfa", "reunion"), gen=job1.text_gen)
        self.assertIsNone(r["rehecho_desde"])
        self.assertEqual(list(job2.edits.values()), ["corregida sobre lo retomado"])

    def test_corregida_mientras_se_rehace_manda_la_correccion(self):
        # se corrige a mano una de las frases que se están rehaciendo, después del cambio de idioma y antes de que
        # llegue lo nuevo: vuelve lo de antes (con la corrección) y el idioma nuevo vale desde donde acababa
        path_holder = {}

        class CorrigeCloud(MultiCloud):
            def transcribe_array(self, cfg, audio, language, *a, **kw):
                if language == "pt" and not path_holder.get("hecho"):  # la petición que rehace: se corrige ahora
                    j = path_holder["job"]
                    with j.edit_lock:
                        j.edits[server.seg_key(j.raw[1][0])] = "corregida mientras tanto"
                    path_holder["hecho"] = True
                return super().transcribe_array(cfg, audio, language, *a, **kw)
        path = os.path.join(self.root, "Alfa", "reunion.wav")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        opts = {"preset": "nube", "language": None, "prompt": None, "about": None, "threads": 1, "tmp_dir": self.tmp,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "resume": [], "summarizer": FakeSummarizer()}
        rec = TimedRecorder(path, synth(SPANS, 34.0), [(26.0, lambda: opts.update(language="pt"))])
        opts.update(cloud=CorrigeCloud(rec, opts), worker=CancellableWorker(rec, opts))
        job = server.Job("Alfa/reunion.wav", "reunion.wav", "nube", None)
        job.live, job.status, job.out = True, "running", server.LiveOutputs(path)
        path_holder["job"] = job
        logs = []
        with mock.patch.object(live, "time", types.SimpleNamespace(time=rec.time, sleep=rec.sleep)), \
                mock.patch.object(live, "speech", energy_vad), mock.patch.object(server, "log"):
            rec.sleep(0.6)
            segs, _ = live.run(job, path, opts, server.live_writer(job), logs.append)
        self.assertTrue(path_holder.get("hecho"))
        self.assertTrue(any("se corrigió a mano entretanto" in x for x in logs), logs)
        out = server.apply_edits(job.raw, job.edits)
        texts = [t for _, _, t in out]
        self.assertIn("corregida mientras tanto", texts)  # la corrección no se pierde
        k = texts.index("corregida mientras tanto")
        self.assertTrue(all(t.startswith("[None]") for t in texts[:k]), texts)
        self.assertTrue(any(t.startswith("[pt]") for t in texts[k + 1:]), texts)  # lo nuevo, ya en portugués
        starts = [a for a, _, _ in out]
        self.assertEqual(starts, sorted(starts))


# ----------------------------------------------------------------------------- el traductor

class TranslatorTests(unittest.TestCase):
    def tr(self, segs, summ=None, to=None):
        job = DirectoJob(preset="nube")
        job.translate = True
        if to:
            job.translate_to = to
        state = {"segs": list(segs)}
        summ = summ or FakeSummarizer()
        t = live.LiveTranslator(job, {"summarizer": summ, "cloud_cfg": CFG2}, lambda: (0, list(state["segs"])),
                                lambda m: None)
        return job, state, summ, t

    def test_rebobinar_quita_lo_traducido_de_lo_que_se_rehace(self):
        segs = [(0.0, 2.0, "a"), (2.0, 4.0, "b"), (4.0, 6.0, "c")]
        job, state, summ, t = self.tr(segs[:2])
        self.assertTrue(t.step())
        state["segs"] = list(segs)
        self.assertTrue(t.step())
        self.assertEqual([(b["start"], b["end"]) for b in job.translation], [(0.0, 4.0), (4.0, 6.0)])
        # se rehace desde 4,0 s: el bloque de c se va; el de a+b se queda y no se vuelve a traducir
        t.rewind(segs[:2], 4.0)
        state["segs"] = segs[:2] + [(4.0, 6.5, "c2")]
        self.assertEqual([b["text"] for b in job.translation], ["ES: a b"])
        self.assertTrue(t.step())
        self.assertEqual(summ.calls[-1], "c2")
        # desde 1,0 s: el bloque a+b toca lo rehecho y se va entero; se vuelve a traducir desde a
        t.rewind([], 0.0)
        state["segs"] = [(0.0, 2.0, "a2")]
        self.assertEqual(job.translation, [])
        self.assertTrue(t.step())
        self.assertEqual(summ.calls[-1], "a2")

    def test_lo_que_estaba_en_vuelo_al_rebobinar_no_se_publica(self):
        segs = [(0.0, 2.0, "vieja")]

        class Summ(FakeSummarizer):
            def translate(self, *a, **kw):
                tr.rewind([], 0.0)  # el bucle del directo rebobina mientras la peticion esta en vuelo
                return super().translate(*a, **kw)
        job, state, summ, tr = self.tr(segs, Summ())
        self.assertFalse(tr.step())
        self.assertEqual(job.translation, [])
        self.assertEqual(tr.done, 0)

    def test_al_otro_destino_se_traduce_de_nuevo_lo_ultimo(self):
        segs = [(0.0, 2.0, "Bom dia."), (2.0, 4.0, "Obrigado.")]
        job, state, summ, t = self.tr(segs)
        self.assertTrue(t.step())
        self.assertEqual(summ.kw["to"], "es")
        epoch = t.epoch
        job.translate_to = "en"
        self.assertTrue(t.step())
        self.assertEqual(summ.kw["to"], "en")
        self.assertEqual(len(job.translation), 1)  # lo de antes (en español) se quitó
        self.assertEqual(summ.calls[-1], "Bom dia. Obrigado.")
        self.assertGreater(t.epoch, epoch)
        job.translate_to = "fr"  # uno que no vale: español
        self.assertTrue(t.step())
        self.assertEqual(summ.kw["to"], "es")


class TranslateEngineTests(unittest.TestCase):
    def run_translate(self, to):
        s = engines.Summarizer()
        s.keys.clock = Clock()
        body = {"choices": [{"message": {"content": " Good morning. "}}]}
        fake = FakeRequests([FakeResponse(200, {}, body)])
        with mock.patch.dict(sys.modules, {"requests": fake}), mock.patch.object(engines.time, "sleep", lambda s: 0):
            out = s.translate(CFG2, "Bom dia.", lambda **k: None, lambda: False, to=to)
        return out, fake.calls[0][1]["json"]["messages"][0]["content"]

    def test_al_ingles_con_su_prompt_en_ingles(self):
        out, system = self.run_translate("en")
        self.assertEqual(out[0], "Good morning.")
        self.assertTrue(system.startswith("You are a simultaneous interpreter"))
        self.assertIn("Translate into English", system)
        self.assertIn("already in English, copy it as is", system)
        _, system = self.run_translate("es")
        self.assertIn("Traduce al español", system)

    def test_otro_destino_falla_claro(self):
        s = engines.Summarizer()
        with self.assertRaises(engines.EngineError) as cm:
            s.translate(CFG2, "Bom dia.", lambda **k: None, lambda: False, to="fr")
        self.assertIn("solo al español o al inglés", str(cm.exception))
        self.assertEqual(set(engines.Summarizer.TRANSLATE_SYSTEMS), set(live.TRANSLATE_TO))


# ----------------------------------------------------------------------------- servidor

class ServidorIdiomaTests(TempTree):
    def setUp(self):
        super().setUp()
        self.settings = server.Store(os.path.join(self.tmp, "s.json"), server.DEFAULT_SETTINGS)
        self.settings.update(lambda d: d["cloud"].update(api_key="gsk_main_000000000"))

    def live_job(self, rel, opts=None):
        job = server.Job(rel, os.path.basename(rel), "nube", None)
        job.live, job.status = True, "running"
        job.opts = dict({"language": "en", "prompt": "", "about": ""}, **(opts or {}))
        job.raw = [(i * 4.0, i * 4.0 + 3.5, f"f{i}") for i in range(10)]
        job.directo = {"cloud": True, "lang": "en", "behind_s": 3.0, "caught_up": True, "paused": False,
                       "latency": {"phases": {"net": {"p50": 0.8}}}}
        return job

    def app(self, jobs):
        a = server.App.__new__(server.App)
        a.settings, a.notes_lock = self.settings, threading.Lock()
        a.library = types.SimpleNamespace(at=1)
        a.jobs = bare_jobs(jobs)
        a.jobs.settings = self.settings
        a.jobs.schedule_outputs = lambda rel, delay=4.0: None
        return a

    def test_notes_save_devuelve_cuando_se_vera_el_idioma(self):
        rel = "Alfa/2026-11-09 10-00-00.wav"
        self.wav(rel)
        job = self.live_job(rel)
        a = self.app([job])
        with mock.patch.object(server, "log"):
            out = a.notes_save({"path": rel, "props": {"idioma": "pt"}})
        self.assertEqual(job.opts["language"], "pt")
        self.assertEqual(out["directo"]["idioma"], {"idioma": "pt", "estado": "al_dia", "eta_s": 3, "rehace_s": 40,
                                                    "detras_s": 3})
        with mock.patch.object(server, "log"):
            out = a.notes_save({"path": rel, "props": {"idioma": "pt"}})  # el mismo otra vez
        self.assertEqual(out["directo"]["idioma"], {"sin_cambio": True})
        with mock.patch.object(server, "log"):
            out = a.notes_save({"path": rel, "props": {"contexto": "otro"}})  # el contexto no lleva aviso de idioma
        self.assertNotIn("directo", out)
        a2 = self.app([])  # sin directo: nada
        out = a2.notes_save({"path": rel, "props": {"idioma": "es"}})
        self.assertNotIn("directo", out)

    def test_traducir_con_destino_validado_y_recordado(self):
        rel = "Alfa/r.wav"
        job = self.live_job(rel)
        a = self.app([job])
        out = a.live_translate({"id": job.id, "on": True, "to": "en"})
        self.assertEqual((job.translate, job.translate_to), (True, "en"))
        self.assertEqual(server.traduccion_destinos().get(rel), "en")
        self.assertTrue(out["live"])
        with self.assertRaises(server.ApiError):
            a.live_translate({"id": job.id, "on": True, "to": "fr"})
        self.assertEqual(job.translate_to, "en")  # nada cambio a medias
        a.live_translate({"id": job.id, "on": False, "to": "es"})  # apagada tambien se recuerda
        self.assertEqual((job.translate, job.translate_to), (False, "es"))
        with open(os.path.join(self.data_dir, "traduccion_destino.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {rel: "es"})
        a.live_translate({"id": job.id, "on": True})  # sin «to» (una interfaz anterior): se queda el que habia
        self.assertEqual(job.translate_to, "es")

    def test_destino_por_defecto(self):
        reg = server.traduccion_destinos()
        self.assertEqual(server.traduccion_destino("x.wav", {"summary_lang": "en"}), "en")
        self.assertEqual(server.traduccion_destino("x.wav", {"summary_lang": "same"}), "es")
        self.assertEqual(server.traduccion_destino("x.wav", {}), "es")
        reg.set("x.wav", "en")
        self.assertEqual(server.traduccion_destino("x.wav", {"summary_lang": "es"}), "en")  # el de la reunion manda
        reg.set("x.wav", "zz")  # basura: no se guarda
        self.assertEqual(reg.get("x.wav"), "en")
        # se relee del disco (otra instancia, p. ej. tras reiniciar) y guarda solo las ultimas
        again = server.TraduccionDestino(os.path.join(self.data_dir, "traduccion_destino.json"))
        self.assertEqual(again.get("x.wav"), "en")
        with mock.patch.object(server.TraduccionDestino, "MAX", 3):
            for i in range(5):
                again.set(f"r{i}.wav", "es")
        self.assertEqual(list(again.data), ["r2.wav", "r3.wav", "r4.wav"])
        # un fichero roto no tumba nada
        p = os.path.join(self.tmp, "roto.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{no es json")
        self.assertEqual(server.TraduccionDestino(p).data, {})

    def test_repaso_final_con_los_idiomas_del_directo(self):
        # msg 1150: empezó en inglés y pasó al portugués; el repaso final no pasa al portugués lo dicho en inglés
        self.assertIsNone(server.idioma_repaso(None))
        self.assertIsNone(server.idioma_repaso(["pt"]))  # no cambió: el de siempre
        self.assertEqual(server.idioma_repaso(["en", "pt"]), "en-pt")
        self.assertEqual(server.idioma_repaso(["pt", "es"]), "es-pt")
        self.assertEqual(server.idioma_repaso(["es-en", "en"]), "es-en")
        self.assertEqual(server.idioma_repaso(["en", "de"]), "")  # sin mezcla para ese par: detectar
        self.assertEqual(server.idioma_repaso([None, "pt"]), "")  # se detectaba: detectar
        self.assertEqual(server.idioma_repaso(["es-en", "pt"]), "")  # tres idiomas: detectar
        jobs = bare_jobs([])
        jobs.settings = types.SimpleNamespace(get=lambda: {"live_final_pass": True})
        jobs._cloud_ready = lambda: None
        nuevo = types.SimpleNamespace()
        jobs._queue_final_pass = lambda *a, **k: nuevo.__dict__.update(k) or nuevo
        job = types.SimpleNamespace(preset="nube", recorded=60.0, edits={}, path="r.wav", name="r", language="pt",
                                    local_part=False, idiomas_directo=["en", "pt"])
        with mock.patch.object(server, "backup_dir", lambda rel: os.path.join(self.tmp, "no")), \
                mock.patch.object(server, "log"):
            self.assertTrue(jobs._final_pass(job))
        self.assertEqual(nuevo.idioma_repaso, "en-pt")
        with open(server.__file__, encoding="utf-8") as f:
            self.assertIn("job.language = job.idioma_repaso or None", f.read())

    def test_start_live_pone_el_destino_de_la_reunion(self):
        with open(server.__file__, encoding="utf-8") as f:
            src = f.read()
        i = src.index("    def start_live(self, rel, preset=None, auto=False):")
        self.assertIn("job.translate_to = traduccion_destino(rel, st)", src[i:i + 2500])

    def test_refresh_prompt_sin_campos_del_trabajo(self):
        # un trabajo sin raw, edits ni directo (los dobles de otros tests): no revienta
        j = types.SimpleNamespace(path="a.wav", live=True, status="running", opts={"language": None}, name="a")
        jobs = bare_jobs([j])
        jobs.settings = types.SimpleNamespace(get=lambda: {})
        with mock.patch.object(server, "meeting_setup", return_value={"prompt": "", "about": "", "language": "pt"}), \
                mock.patch.object(server, "safe", lambda rel, must=None: rel), mock.patch.object(server, "log"):
            info = jobs.refresh_prompt("a.wav")
        self.assertEqual(info["estado"], "al_dia")
        self.assertEqual(info["idioma"], "pt")


# ----------------------------------------------------------------------------- la tarjeta de grabar

class NotaIdiomaFijoTests(unittest.TestCase):
    def linea(self, lang):
        p = plan.resolver({"language": lang}, [], pedido={"cambios": {}})
        return next(x for x in p["lineas"] if x["clave"] == "idioma")

    def test_solo_con_un_idioma_fijo(self):
        for code in ("pt", "en", "es"):
            self.assertEqual(self.linea(code).get("nota"), plan.NOTA_IDIOMA_FIJO, code)
        for code in ("", "auto", "es-en", "en-pt"):
            self.assertNotIn("nota", self.linea(code), code)
        self.assertIn("se traduce a este", plan.NOTA_IDIOMA_FIJO)
        self.assertIn("una mezcla o «Detectar»", plan.NOTA_IDIOMA_FIJO)

    def test_la_tarjeta_la_ensena(self):
        js = read("grabar.js")
        self.assertIn('l.nota ? `<span class="gr-nota">${esc(l.nota)}</span>`', js)
        self.assertIn(".gr-nota {", read("app.css"))


# ----------------------------------------------------------------------------- interfaz (node)

@unittest.skipUnless(shutil.which("node"), "node no instalado")
class InterfazTextosTests(unittest.TestCase):
    def node(self, defs, code):
        r = subprocess.run(["node", "-e", defs + "\n" + code], capture_output=True, text=True, encoding="utf-8",
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n").splitlines()

    def test_aviso_al_cambiar_el_idioma_en_directo(self):
        defs = js_defs(read("perfil.js"), ["perfilSavedText", "perfilSegTxt", "perfilIdiomaDirectoText"])
        out = self.node(defs, """
const i = (o) => Object.assign({idioma: 'pt', estado: 'al_dia', eta_s: 4, rehace_s: 58, detras_s: 3}, o);
console.log(perfilSavedText('idioma', true, true, i({})));
console.log(perfilSavedText('idioma', true, true, i({rehace_s: 0})));
console.log(perfilSavedText('idioma', true, true, i({estado: 'poniendose', eta_s: null, detras_s: 200})));
console.log(perfilSavedText('idioma', true, true, i({estado: 'pausa', eta_s: null})));
console.log(perfilSavedText('idioma', true, true, {sin_cambio: true}));
console.log(perfilSavedText('idioma', true, true));
console.log(perfilSavedText('idioma', false, true, i({})));
console.log(perfilSavedText('contexto', true, true, i({})));""")
        self.assertEqual(out[0], "Idioma cambiado: el directo lo usa ya y el texto nuevo sale en ~4 s; rehace en él "
                                 "los últimos 58 s ya escritos")
        self.assertEqual(out[1], "Idioma cambiado: el directo lo usa ya y el texto nuevo sale en ~4 s")
        self.assertEqual(out[2], "Idioma cambiado ya, pero el directo va poniéndose al día (~3 min por detrás): verás "
                                 "el idioma nuevo cuando alcance lo grabado; rehace en él los últimos 58 s ya escritos")
        self.assertTrue(out[3].startswith("Idioma cambiado. El directo está en pausa por cupo"), out[3])
        self.assertEqual(out[4], "Idioma guardado: el directo ya usaba ese idioma")
        self.assertEqual(out[5], "Idioma: el directo lo usa desde el siguiente trozo")  # servidor anterior, sin info
        self.assertEqual(out[6], "Idioma guardado · se aplicará si la vuelves a transcribir (y al resumen)")
        self.assertEqual(out[7], "Contexto: el directo lo usa desde el siguiente trozo")

    def test_notas_pasa_la_respuesta_al_aviso(self):
        notas = read("notas.js")
        self.assertIn("perfilSavedText('idioma', !!liveJob(a), !!(a.txt || a.srt), r.directo?.idioma)", notas)
        self.assertRegex(notas, r"async function saveProps\(a, patch\) \{[\s\S]*?return r;\n  \} catch")

    def test_traducir_al_espanol_o_al_ingles(self):
        js = read("directo.js")
        m = re.search(r"^const DIRECTO_TRAD_TO = .*$", js, re.M)
        defs = m.group(0) + "\n" + js_defs(js, ["directoTradTo", "directoTradNombre"])
        out = self.node(defs, """
console.log(directoTradTo({translate_to: 'en'}), directoTradTo({translate_to: 'es'}), directoTradTo({}),
  directoTradTo(null), directoTradTo({translate_to: 'fr'}));
console.log(directoTradNombre('en'), directoTradNombre('es'), directoTradNombre('zz'));""")
        self.assertEqual(out, ["en es es es es", "inglés español español"])
        # la casilla y el destino mandan {id, on, to}; la cabecera de la columna dice el destino
        self.assertIn("api('/api/live/traducir', { id, on, to })", js)
        self.assertIn("Traducción al ${directoTradNombre(to)}", js)
        self.assertIn("document.activeElement.id === 'directoTradTo'", js)  # no se repinta a mitad de elegir


if __name__ == "__main__":
    unittest.main()
