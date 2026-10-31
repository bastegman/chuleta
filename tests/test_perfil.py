# -*- coding: utf-8 -*-
"""Perfil de reunión: ajustes por defecto compatibles, precedencias (elegido > perfil > reunión en curso > último),
idioma y contexto en la cabecera del .md (también al grabar), build_prompt con contexto libre (el vocabulario tiene
prioridad al recortar), relectura por trozo en live.run, copia de textos a otra carpeta (sin pisar), importación de
listas y las rutas /api/perfil y /api/vocab/import. Todo con dobles: sin red, sin Windows."""
import io
import json
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock

from support import TempTree, bare_jobs, engines, live, server, srt_text  # noqa: F401  (support prepara sys.path)
import calendario  # noqa: E402
import salidas  # noqa: E402
from test_directo import (DirectoJob, FakeCloud, FakeRecorder, FakeSummarizer, FakeWorker,  # noqa: E402
                          energy_vad, synth)


def settings_store(tmp=None, **extra):
    tmp = tmp or tempfile.mkdtemp(prefix="notas-perfil-")
    st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
    if extra:
        st.update(lambda d: d.update(extra))
    return st


# ----------------------------------------------------------------------------- ajustes y precedencias

class AjustesPerfilTests(unittest.TestCase):
    def test_claves_por_defecto_en_automatico(self):
        self.assertEqual(server.DEFAULT_SETTINGS["perfil"],
                         {"folder": None, "vocab": None, "context": "", "outputs": []})
        self.assertEqual(server.PERFIL_KEYS, ("folder", "vocab", "context", "outputs"))

    def test_una_instalacion_anterior_no_cambia(self):
        """settings.json sin "perfil": todo sigue en automatico (ultima carpeta, ultimo tema, idioma general)."""
        tmp = tempfile.mkdtemp(prefix="notas-perfil-")
        with open(os.path.join(tmp, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"language": "es-en", "rec_vocab": "Alfa/Vocabulario/General.txt", "rec_folder": "Alfa"}, f)
        st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS).get()
        self.assertEqual(server.perfil_de(st), {"folder": None, "vocab": None, "context": "", "outputs": [],
                                                "language": "es-en"})
        self.assertEqual(server.perfil_vocab(st), "Alfa/Vocabulario/General.txt")  # el ultimo elegido
        self.assertEqual(server.perfil_vocab(st, "Beta/Vocabulario/X.txt"), "Beta/Vocabulario/X.txt")  # la reunion
        r = server.resolve_perfil(st, {"vocab": "Beta/Vocabulario/X.txt", "name": "Comité"})
        self.assertEqual((r["vocab"], r["folder"], r["language"], r["name"]),
                         ("Beta/Vocabulario/X.txt", "Alfa", "es-en", "Comité"))
        self.assertEqual(r["origen"], {"vocab": "reunion", "folder": "ultimo", "language": "perfil",
                                       "context": "perfil", "name": "reunion", "participantes": "ultimo"})

    def test_idioma_de(self):
        st = {"language": "pt"}
        self.assertEqual(server.idioma_de({}, st), "pt")                   # sin clave: el del perfil
        self.assertEqual(server.idioma_de({"idioma": "auto"}, st), "")     # detectar
        self.assertEqual(server.idioma_de({"idioma": "ES-EN"}, st), "es-en")
        self.assertEqual(server.idioma_de({"idioma": "<x>"}, st), "pt")    # basura: el del perfil
        self.assertEqual(server.idioma_de({"idioma": "en"}, {}), "en")


class PrecedenciasTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("Alfa/Vocabulario/General.txt", "API, SLA\n")
        self.write("Beta/Vocabulario/Legal.txt", "Contrato marco\n")
        os.makedirs(os.path.join(self.root, "Alfa", "Reuniones"))
        os.makedirs(os.path.join(self.root, "Otra"))
        self.sug = {"vocab": "Beta/Vocabulario/Legal.txt", "name": "Comité semanal", "participants": ["Ana", "Luis"]}

    def test_el_perfil_fijo_gana_a_la_reunion_y_lo_elegido_gana_a_todo(self):
        st = {"language": "es", "rec_vocab": "Alfa/Vocabulario/General.txt", "rec_folder": "Otra",
              "perfil": {"folder": "Alfa/Reuniones", "vocab": "", "context": "Reuniones del proyecto Alfa"}}
        r = server.resolve_perfil(st, self.sug)
        # el perfil fija carpeta, tema (sin vocabulario) y contexto; la reunion solo aporta lo que el perfil deja libre
        self.assertEqual((r["folder"], r["vocab"], r["context"]), ("Alfa/Reuniones", "", "Reuniones del proyecto Alfa"))
        self.assertEqual((r["name"], r["participantes"]), ("Comité semanal", "Ana, Luis"))
        self.assertEqual(r["origen"]["vocab"], "perfil")
        self.assertEqual(r["origen"]["name"], "reunion")
        self.assertEqual(server.perfil_vocab(st, self.sug["vocab"]), "")
        # lo elegido en el dialogo para esa reunion manda sobre el perfil
        r = server.resolve_perfil(st, self.sug, {"vocab": "Beta/Vocabulario/Legal.txt", "folder": "Otra",
                                                 "language": "en-pt", "context": "", "name": "Otro nombre"})
        self.assertEqual((r["vocab"], r["folder"], r["language"], r["context"], r["name"]),
                         ("Beta/Vocabulario/Legal.txt", "Otra", "en-pt", "", "Otro nombre"))
        self.assertTrue(all(v == "elegido" for k, v in r["origen"].items() if k != "participantes"))

    def test_carpeta_fija_que_ya_no_existe_vuelve_a_la_automatica(self):
        st = {"rec_folder": "Otra", "perfil": {"folder": "Alfa/Borrada"}}
        self.assertIsNone(server.perfil_folder(st))
        self.assertEqual(server.resolve_perfil(st)["origen"]["folder"], "ultimo")
        self.assertEqual(server.resolve_perfil(st)["folder"], "Otra")
        a = server.App.__new__(server.App)
        self.assertEqual(a.rec_folder_for(None, st), "Otra")
        self.assertEqual(a.rec_folder_for(None, {"rec_folder": "Otra", "perfil": {"folder": "Alfa/Reuniones"}}),
                         "Alfa/Reuniones")
        self.assertEqual(a.rec_folder_for(None, {"rec_folder": "Otra", "perfil": {"folder": ""}}), "")  # la raiz, fija

    def test_check_perfil_valida(self):
        dest = os.path.join(self.tmp, "docs")
        os.makedirs(dest)
        ok = server.check_perfil({"folder": "Alfa/Reuniones", "vocab": "Alfa/Vocabulario/General.txt",
                                  "context": "  dos   frases  ", "language": "ES-PT",
                                  "outputs": [{"path": dest, "format": "docx", "by_project": 1},
                                              {"path": dest, "format": "docx"}, {"path": dest}]})
        self.assertEqual(ok, {"folder": "Alfa/Reuniones", "vocab": "Alfa/Vocabulario/General.txt",
                              "context": "dos frases", "language": "es-pt",
                              "outputs": [{"path": dest, "format": "docx", "by_project": True},
                                          {"path": dest, "format": "md", "by_project": False}]})  # sin repetidos
        self.assertEqual(server.check_perfil({"folder": None, "vocab": None}), {"folder": None, "vocab": None})
        self.assertEqual(server.check_perfil({"context": "x"}), {"context": "x"})  # parcial: solo lo que viene
        self.assertEqual(server.check_perfil({"outputs": None}), {"outputs": []})
        for bad in ({"folder": "NoExiste"}, {"folder": "_app"}, {"vocab": "Zeta/Vocabulario/X.txt"},
                    {"language": "xx-yy-zz"}, {"outputs": "x"}, {"outputs": [{"path": os.path.join(self.tmp, "no")}]},
                    {"outputs": [{"path": dest, "format": "pdf"}]}, {"outputs": [{"path": "relativa"}]},
                    {"outputs": [{"path": self.app_dir}]}):
            with self.assertRaises(server.ApiError, msg=bad):
                server.check_perfil(bad)
        d = {"perfil": {"folder": "Otra"}, "language": "es"}
        server.update_perfil(d, server.check_perfil({"context": "c", "language": "pt"}))
        self.assertEqual(d, {"perfil": {"folder": "Otra", "vocab": None, "context": "c", "outputs": []},
                             "language": "pt"})

    def test_save_settings_con_perfil(self):
        a = server.App.__new__(server.App)
        a.settings = settings_store()
        a.config = lambda q: {}
        a.save_settings({"perfil": {"folder": "Alfa/Reuniones", "vocab": None, "context": "ctx", "outputs": []},
                         "preset": "nube", "language": "pt"})
        st = a.settings.get()
        self.assertEqual(st["perfil"], {"folder": "Alfa/Reuniones", "vocab": None, "context": "ctx", "outputs": []})
        self.assertEqual((st["preset"], st["language"]), ("nube", "pt"))
        with self.assertRaises(server.ApiError):
            a.save_settings({"perfil": {"folder": "NoExiste"}, "language": "en"})
        self.assertEqual(a.settings.get()["language"], "pt")  # nada se guardo a medias


# ----------------------------------------------------------------------------- cabecera del .md

class NotesPerfilTests(TempTree):
    def app(self):
        a = server.App.__new__(server.App)
        a.settings, a.notes_lock = settings_store(self.tmp), threading.Lock()
        a.library = types.SimpleNamespace(at=1)
        a.jobs = types.SimpleNamespace(refreshed=[], scheduled=[])
        a.jobs.refresh_prompt = a.jobs.refreshed.append
        a.jobs.schedule_outputs = lambda rel, delay=4.0: a.jobs.scheduled.append(rel)
        return a

    def test_roundtrip_de_las_claves_nuevas(self):
        props = {"proyecto": "Alfa", "workstream": "General", "idioma": "es-pt", "contexto": "Comité semanal"}
        text = server.dump_notes(props, "notas")
        self.assertIn("idioma: es-pt\ncontexto: Comité semanal\n", text)
        self.assertEqual(server.parse_notes(text), (props, "notas\n"))
        self.assertEqual(server.parse_notes("---\nproyecto: Alfa\n---\n")[0], {"proyecto": "Alfa"})  # sin las nuevas

    def test_notes_save_valida_y_refresca_el_directo(self):
        a = self.app()
        audio = self.wav("Alfa/2026-10-31 10-00-00.wav")
        md = os.path.splitext(audio)[0] + ".md"
        rel = "Alfa/2026-10-31 10-00-00.wav"
        a.notes_save({"path": rel, "props": {"idioma": "ES-PT", "contexto": "  de   qué  va ", "estado": "Revisada"}})
        self.assertEqual(server.parse_notes(server.read_text(md))[0],
                         {"estado": "Revisada", "idioma": "es-pt", "contexto": "de qué va"})
        self.assertEqual(a.jobs.refreshed, [rel])
        a.notes_save({"path": rel, "props": {"estado": "Enviada"}})  # el estado no toca el directo
        self.assertEqual(a.jobs.refreshed, [rel])
        a.notes_save({"path": rel, "props": {"idioma": "<script>"}})  # basura: se quita la clave
        self.assertNotIn("idioma", server.parse_notes(server.read_text(md))[0])
        a.notes_save({"path": rel, "props": {"idioma": "auto", "contexto": "x" * 600}})
        p = server.parse_notes(server.read_text(md))[0]
        self.assertEqual((p["idioma"], len(p["contexto"])), ("auto", engines.ABOUT_CHARS))
        self.assertEqual(a.jobs.refreshed, [rel, rel, rel])
        self.assertEqual(a.jobs.scheduled, [rel] * 4)  # cada guardado reescribe los destinos de salida

    def test_meeting_setup(self):
        self.write("Alfa/Vocabulario/General.txt", "API, SLA\n")
        audio = self.wav("Alfa/r.wav")
        st = {"language": "pt", "prompt": "general"}
        self.assertEqual(server.meeting_setup(audio, st), {"prompt": "general", "about": "", "language": "pt"})
        self.write("Alfa/r.md", "---\nproyecto: Alfa\nworkstream: General\nidioma: auto\ncontexto: Comité\n---\n")
        self.assertEqual(server.meeting_setup(audio, st), {"prompt": "API, SLA", "about": "Comité", "language": ""})
        self.write("Alfa/r.md", "---\nidioma: en-pt\n---\n")
        self.assertEqual(server.meeting_setup(audio, st)["language"], "en-pt")


class FakeRec:
    """RecorderProcess de mentira: crea el WAV con su cabecera y «sigue vivo»."""

    def __init__(self, path, mic, sys_dev, app_dir, data_dir):
        self.path, self.status = path, {}
        with open(path, "wb") as f:
            f.write(b"\0" * 44)

    def alive(self):
        return True


class RecStartPerfilTests(TempTree):
    """rec_start escribe en el .md el idioma y el contexto de esa reunion y ya no cambia el idioma general al elegir
    otro para una grabacion."""

    def app(self, **st):
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, **st)
        a.rec_lock, a.notes_lock, a.rec, a.monitor = threading.Lock(), threading.Lock(), None, None
        a.library = types.SimpleNamespace(at=1)
        a.jobs = bare_jobs([])
        a.jobs.settings, a.jobs.started = a.settings, []
        a.jobs.start_live = lambda rel, preset: a.jobs.started.append((rel, preset))
        return a

    def start(self, a, **b):
        os.makedirs(os.path.join(self.root, "Alfa"), exist_ok=True)
        body = dict({"mic": "default", "sys": "all", "folder": "Alfa", "live": True, "preset": "medium", "name": "x",
                     "vocab": ""}, **b)
        a.rec = None  # cada start es una grabacion nueva (la anterior «termino»)
        with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log"):
            r = a.rec_start(body)
        md = os.path.splitext(server.safe(r["path"]))[0] + ".md"
        return r, (server.parse_notes(server.read_text(md))[0] if os.path.isfile(md) else {})

    def test_idioma_y_contexto_de_la_reunion_al_md(self):
        a = self.app(language="es", perfil={"context": "Reuniones del proyecto Alfa"})
        _, props = self.start(a, language="es-pt", contexto="  Comité   de seguimiento ")
        self.assertEqual(props, {"idioma": "es-pt", "contexto": "Comité de seguimiento"})
        self.assertEqual(a.settings.get()["language"], "es")  # el idioma de ESTA reunion no cambia el general
        self.assertEqual(len(a.jobs.started), 1)
        self.assertEqual(server.meeting_setup(server.safe(a.jobs.started[0][0]), a.settings.get())["language"], "es-pt")

    def test_detectar_se_guarda_como_auto_y_sin_contexto_vale_el_del_perfil(self):
        a = self.app(language="pt", perfil={"context": "Reuniones del proyecto Alfa"})
        _, props = self.start(a, language="")
        self.assertEqual(props, {"idioma": "auto", "contexto": "Reuniones del proyecto Alfa"})
        self.assertEqual(server.idioma_de(props, a.settings.get()), "")
        _, props = self.start(a, language="pt", contexto="", name="y")  # contexto vaciado a proposito: ninguno
        self.assertEqual(props, {"idioma": "pt"})


# ----------------------------------------------------------------------------- prompt de Whisper

class PromptAboutTests(unittest.TestCase):
    count = staticmethod(lambda t: len(t.split()))  # 1 token por palabra

    def test_el_contexto_va_tras_el_vocabulario_y_antes_de_lo_ultimo_dicho(self):
        prompt, dropped = engines.build_prompt("Estilo.", "a, b, c", count=self.count, limit=20,
                                               about="uno dos tres cuatro", context="lo último")
        self.assertEqual(prompt, "Estilo. a, b, c uno dos tres cuatro lo último")
        self.assertEqual(dropped, [])

    def test_al_recortar_cede_primero_el_contexto_y_el_vocabulario_se_queda(self):
        p, d = engines.build_prompt("Estilo.", "a, b, c", count=self.count, limit=6, about="uno dos tres cuatro")
        self.assertEqual((p, d), ("Estilo. a, b, c uno dos", []))
        p, d = engines.build_prompt("Estilo.", "a, b, c", count=self.count, limit=4, about="uno dos tres cuatro")
        self.assertEqual((p, d), ("Estilo. a, b, c", []))  # el contexto desaparece antes que un solo termino
        p, d = engines.build_prompt("Estilo.", "a, b, c", count=self.count, limit=3, about="uno dos tres cuatro")
        self.assertEqual((p, d), ("Estilo. a, b", ["c"]))  # sin contexto y aun asi no cabe: termino fuera
        self.assertEqual(engines.build_prompt("", "", count=self.count, about="solo contexto"), ("solo contexto", []))

    def test_el_contexto_tiene_su_propio_tope(self):
        about = " ".join(f"p{i}" for i in range(300))
        p, _ = engines.build_prompt("", "", count=self.count, limit=1000, about=about)
        self.assertEqual(len(p.split()), engines.ABOUT_TOKENS)
        p, _ = engines.build_prompt("", "", count=self.count, limit=1000, about="x" * 2000)
        self.assertEqual(len(p), engines.ABOUT_CHARS)

    def test_los_tests_de_siempre_no_cambian(self):
        prompt, dropped = engines.build_prompt("Hola qué tal.", "uno, dos, tres, cuatro", count=self.count, limit=5)
        self.assertEqual((prompt, dropped), ("Hola qué tal. uno, dos", ["tres", "cuatro"]))

    def test_mezclas_de_idiomas(self):
        self.assertEqual(engines.lang_pair("es-pt"), ("es", "pt"))
        self.assertEqual(engines.lang_pair("en-pt"), ("en", "pt"))
        self.assertEqual(engines.lang_pair("es-en"), ("es", "en"))
        self.assertIsNone(engines.lang_pair("pt"))
        self.assertIsNone(engines.lang_pair(""))
        self.assertEqual(engines.style_for("es-en"), engines.STYLE_PROMPT["mix"])
        self.assertEqual(engines.style_for("es-pt"), engines.STYLE_PROMPT["es-pt"])
        self.assertEqual(engines.style_for(""), "")
        lang, prompt = engines.CloudEngine.prompt_for("es-pt", "API", about="Comité")
        self.assertIsNone(lang)  # la API no tiene mezclas: detecta + frase mezclada
        self.assertTrue(prompt.startswith(engines.STYLE_PROMPT["es-pt"]))
        self.assertTrue(prompt.endswith("API Comité"))
        self.assertEqual(engines.CloudEngine.prompt_for("pt", "API"), ("pt", "API"))
        # el tokenizador real (si lo hay) o la estimacion: las frases nuevas caben de sobra
        for k in ("es-pt", "en-pt"):
            self.assertLess(engines.count_tokens(engines.STYLE_PROMPT[k]), 60)

    def test_resumen_con_contexto(self):
        s = engines.Summarizer.__new__(engines.Summarizer)
        seen = {}

        def chat(cfg, system, user, max_tokens, report, cancelled, **k):
            seen["user"] = user
            return "## Resumen", "m"
        s._chat = chat
        meta = {"title": "T", "date": "d", "duration": "1 min", "participants": "", "notes": "", "extra": "",
                "about": "  Comité semanal  con el cliente "}
        s.summarize([{"start": 0, "end": 1, "text": "hola"}], meta, {"summary_lang": "es"}, lambda **k: None,
                    lambda: False)
        self.assertIn("Contexto indicado por el usuario: Comité semanal con el cliente.", seen["user"])


# ----------------------------------------------------------------------------- directo: relectura por trozo

class SwitchingCloud(FakeCloud):
    """Tras el primer trozo cambia idioma, contexto y vocabulario en opts (como hace Jobs.refresh_prompt)."""

    def __init__(self, rec, opts):
        super().__init__(rec)
        self.opts = opts

    def transcribe_array(self, *a, **kw):
        out = super().transcribe_array(*a, **kw)
        if len(self.calls) == 1:
            self.opts.update(language="pt", about="Comité semanal", prompt="API, SLA")
        return out


class SwitchingWorker(FakeWorker):
    def __init__(self, rec, opts):
        super().__init__(rec)
        self.opts, self.reqs = opts, []

    def request(self, req, audio=None, **kw):
        self.reqs.append(dict(req))
        out = super().request(req, audio=audio, **kw)
        if len(self.reqs) == 1:
            self.opts.update(language="pt", about="Comité semanal")
        return out


class LiveRereadTests(unittest.TestCase):
    # en la nube bastan 17 s (trozos de 3-6 s); en local las ventanas son de 20-45 s: 46 s para tener dos
    spans = [(0.0, 3.4), (4.0, 7.6), (8.2, 11.3), (11.9, 15.0)]
    spans_local = spans + [(20.0, 23.0), (24.0, 27.0), (28.0, 31.0), (32.0, 35.0), (40.0, 43.0)]

    def run_live(self, preset):
        tmp = tempfile.mkdtemp(prefix="notas-perfil-live-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "reunion.wav")
        local = preset != "nube"
        rec = FakeRecorder(path, synth(self.spans_local if local else self.spans, 46.0 if local else 17.0))
        opts = {"preset": preset, "language": None, "prompt": None, "about": None, "threads": 1,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "tmp_dir": tmp, "resume": [],
                "summarizer": FakeSummarizer()}
        cloud, worker = SwitchingCloud(rec, opts), SwitchingWorker(rec, opts)
        opts.update(cloud=cloud, worker=worker)
        job = DirectoJob(preset=preset)
        logs = []
        fake_time = types.SimpleNamespace(time=rec.time, sleep=rec.sleep)
        with mock.patch.object(live, "time", fake_time), mock.patch.object(live, "speech", energy_vad):
            rec.sleep(0.6)
            segs, lang = live.run(job, path, opts, lambda s: None, logs.append)
        return segs, cloud, worker, logs

    def test_en_la_nube_el_siguiente_trozo_lleva_el_idioma_el_contexto_y_el_vocabulario_nuevos(self):
        segs, cloud, _, logs = self.run_live("nube")
        self.assertGreaterEqual(len(cloud.calls), 3)
        first, second = cloud.calls[0], cloud.calls[1]
        self.assertEqual((first["language"], first["about"], first["prompt"]), (None, None, None))
        self.assertEqual((second["language"], second["about"], second["prompt"]), ("pt", "Comité semanal", "API, SLA"))
        self.assertTrue(all(c["language"] == "pt" for c in cloud.calls[1:]))
        self.assertTrue(any("idioma pt desde el siguiente trozo" in l for l in logs), logs)
        self.assertEqual(len(segs), len(cloud.calls))

    def test_en_local_tambien(self):
        _, _, worker, logs = self.run_live("medium")
        self.assertGreaterEqual(len(worker.reqs), 2)
        self.assertEqual((worker.reqs[0]["language"], worker.reqs[0]["about"]), (None, None))
        self.assertEqual((worker.reqs[1]["language"], worker.reqs[1]["about"]), ("pt", "Comité semanal"))

    def test_refresh_prompt_actualiza_opts_del_directo(self):
        with mock.patch.object(server, "meeting_setup", return_value={"prompt": "P", "about": "A", "language": "pt"}), \
                mock.patch.object(server, "safe", lambda rel, must=None: rel), mock.patch.object(server, "log"):
            j = types.SimpleNamespace(path="a.wav", live=True, status="running", opts={"language": None}, name="a")
            jobs = bare_jobs([j])
            jobs.settings = types.SimpleNamespace(get=lambda: {})
            jobs.refresh_prompt("a.wav")
        self.assertEqual(j.opts, {"language": "pt", "prompt": "P", "about": "A"})


# ----------------------------------------------------------------------------- destinos de salida

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
STEM = "2026-10-31 10-00-00 - Semanal"


def sample_doc(**kw):
    doc = {"stem": STEM, "title": "Kick-off: fase 2 #3", "date": "2026-10-31 10:00", "duration": "45 min",
           "props": {"proyecto": "Alfa", "workstream": "General", "participantes": "Ana, Luis",
                     "etiquetas": ["a", "b c"], "idioma": "es-pt"},
           "source": "Alfa/" + STEM + ".wav", "summary_model": "m",
           "summary": "## Resumen\n\nTres frases.\n\n## Decisiones\n- una [0:12]\n",
           "notes": "# Orden del día\n\n- [ ] tarea\n- punto\n", "app_name": "Chuleta", "generated": "2026-10-31 11:02",
           "segments": [{"start": 0.0, "end": 2.0, "text": "Hola a todos."},
                        {"start": 61.5, "end": 70.0, "text": "Empezamos.", "who": "Ana"}]}
    doc.update(kw)
    return doc


class SalidasRenderTests(unittest.TestCase):
    def test_markdown_un_documento_con_cabecera_y_secciones(self):
        md = salidas.render_md(sample_doc())
        self.assertTrue(md.startswith("---\n"))
        self.assertIn('titulo: "Kick-off: fase 2 #3"', md)  # entre comillas: «:» y «#» romperian el YAML de Obsidian
        self.assertIn('etiquetas: ["a", "b c"]', md)
        self.assertIn('idioma: "es-pt"', md)
        self.assertIn('origen: "Alfa/' + STEM + '.wav"', md)
        self.assertIn(f'{salidas.MARK}: "Chuleta (desde la app;', md)
        self.assertIn('generado: "2026-10-31 11:02"', md)
        # secciones en orden; los titulos del resumen y de las notas quedan por debajo (### …)
        self.assertLess(md.index("# Kick-off: fase 2 #3"), md.index("## Resumen"))
        self.assertLess(md.index("## Resumen"), md.index("## Notas"))
        self.assertLess(md.index("## Notas"), md.index("## Transcripción"))
        self.assertIn("### Resumen\n\nTres frases.\n\n### Decisiones\n- una [0:12]", md)
        self.assertIn("### Orden del día\n\n- [ ] tarea", md)
        self.assertIn("[0:00] Hola a todos.\n\n[1:01] Ana: Empezamos.", md)
        self.assertEqual(md.count("\n## Resumen\n"), 1)
        vacio = salidas.render_md(sample_doc(summary="", notes="", segments=[]))
        for t in ("*Sin resumen todavía.*", "*Sin notas.*", "*Sin transcripción todavía.*"):
            self.assertIn(t, vacio)
        self.assertFalse(salidas.render(sample_doc(), "md").startswith(b"\xef\xbb\xbf"))  # sin BOM (YAML)

    def test_texto_llano(self):
        txt = salidas.render_txt(sample_doc())
        lines = txt.splitlines()
        self.assertEqual(lines[0], "Kick-off: fase 2 #3")
        self.assertTrue(lines[1].startswith(f"{salidas.MARK}: Chuleta"))
        self.assertIn("Proyecto: Alfa · Tema: General · Participantes: Ana, Luis", lines[2])
        self.assertIn("RESUMEN\n=======\nResumen\n\nTres frases.\n\nDecisiones\n  - una [0:12]", txt)
        self.assertIn("NOTAS\n=====\nOrden del día\n\n  - tarea\n  - punto", txt)
        self.assertIn("TRANSCRIPCIÓN\n=============\n[0:00] Hola a todos.\n[1:01] Ana: Empezamos.", txt)
        self.assertTrue(salidas.render(sample_doc(), "txt").startswith("\ufeff".encode("utf-8")))  # BOM, como los .txt

    def test_docx_valido(self):
        z = zipfile.ZipFile(io.BytesIO(salidas.render_docx(sample_doc())))
        self.assertIsNone(z.testzip())
        names = set(z.namelist())
        for n in ("[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels", "word/document.xml",
                  "word/styles.xml", "docProps/core.xml", "docProps/app.xml"):
            self.assertIn(n, names)
        for n in names:
            ET.fromstring(z.read(n))  # todo XML bien formado
        root = ET.fromstring(z.read("word/document.xml"))
        paras = root.findall(f".//{W}p")
        texts = ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in paras]
        styles = [p.find(f"{W}pPr/{W}pStyle").get(f"{W}val") if p.find(f"{W}pPr/{W}pStyle") is not None else None
                  for p in paras]
        self.assertEqual((texts[0], styles[0]), ("Kick-off: fase 2 #3", "Title"))
        self.assertEqual([t for t, st in zip(texts, styles) if st == "Heading1"], ["Resumen", "Notas", "Transcripción"])
        self.assertIn(("Decisiones", "Heading2"), list(zip(texts, styles)))
        self.assertIn("• una [0:12]", texts)
        self.assertIn("[1:01] Ana: Empezamos.", texts)
        self.assertTrue(any(t.startswith(salidas.MARK + ": Chuleta") for t in texts))
        core = z.read("docProps/core.xml").decode("utf-8")
        self.assertIn("<dc:creator>Chuleta</dc:creator>", core)
        self.assertIn(f"<cp:keywords>{salidas.MARK}: Chuleta</cp:keywords>", core)
        self.assertIn("<Application>Chuleta</Application>", z.read("docProps/app.xml").decode("utf-8"))
        self.assertIn('PartName="/word/document.xml"', z.read("[Content_Types].xml").decode("utf-8"))
        self.assertIn('w:styleId="Heading1"', z.read("word/styles.xml").decode("utf-8"))
        # & < > y saltos de linea no rompen el XML
        z2 = zipfile.ZipFile(io.BytesIO(salidas.render_docx(sample_doc(title="A & B <c>", notes="l1\nl2\n\nl3"))))
        doc2 = z2.read("word/document.xml")
        ET.fromstring(doc2)
        self.assertIn(b"A &amp; B &lt;c&gt;", doc2)
        self.assertIn(b">l1 l2<", doc2)  # lineas seguidas = un parrafo; una linea en blanco separa parrafos
        self.assertIn(b">l3<", doc2)

    def test_marca_de_origen(self):
        tmp = tempfile.mkdtemp(prefix="notas-salidas-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for fmt in salidas.FORMATS:
            p = os.path.join(tmp, "x." + fmt)
            with open(p, "wb") as f:
                f.write(salidas.render(sample_doc(), fmt))
            self.assertTrue(salidas.is_ours(p, fmt), fmt)
        ajenos = {"md": b"---\ntitulo: x\n---\nhola", "txt": "\ufeffhola\nmundo\n".encode("utf-8"),
                  "docx": b"PK\x03\x04no es un zip"}
        for fmt, body in ajenos.items():
            p = os.path.join(tmp, "ajeno." + fmt)
            with open(p, "wb") as f:
                f.write(body)
            self.assertFalse(salidas.is_ours(p, fmt), fmt)
        self.assertFalse(salidas.is_ours(os.path.join(tmp, "no-existe.md"), "md"))
        p = os.path.join(tmp, "cuerpo.md")  # la marca en el cuerpo, no en la cabecera: no cuenta
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"---\ntitulo: x\n---\n{salidas.MARK}: y\n")
        self.assertFalse(salidas.is_ours(p, "md"))

    def test_lineas_de_la_transcripcion_y_destinos(self):
        lines = salidas.transcript_lines([{"start": None, "text": "a"}, {"start": None, "text": "b"}])
        self.assertEqual(lines, ["a b"])  # un .txt sin tiempos: sin minutos
        lines = salidas.transcript_lines([{"start": 0, "text": "x " * 150}, {"start": 30, "text": "y"}])
        self.assertEqual(len(lines), 2)  # ~260 caracteres por linea
        self.assertTrue(lines[1].startswith("[0:30] "))
        raw = [{"path": " /a ", "format": "PDF"}, {"path": ""}, "x", {"path": "/b", "format": "docx", "by_project": 1}]
        self.assertEqual(salidas.destinos_de(raw), [{"path": "/a", "format": "md", "by_project": False},
                                                    {"path": "/b", "format": "docx", "by_project": True}])
        self.assertEqual(salidas.destinos_de(None), [])


class SalidasWriteTests(TempTree):
    def setUp(self):
        super().setUp()
        self.d1, self.d2, self.d3 = (os.path.join(self.tmp, n) for n in ("vault", "onedrive", "sharepoint"))
        for d in (self.d1, self.d2, self.d3):
            os.makedirs(d)
        self.reg = salidas.Registro(os.path.join(self.data_dir, "salidas.json"))
        self.destinos = [{"path": self.d1, "format": "md", "by_project": False},
                         {"path": self.d2, "format": "txt", "by_project": True},
                         {"path": self.d3, "format": "docx", "by_project": False}]

    def read(self, path):
        with open(path, encoding="utf-8-sig") as f:
            return f.read()

    def test_n_destinos_con_subcarpeta_por_proyecto_y_reescritura(self):
        done = salidas.write_all(sample_doc(), self.destinos, self.reg)
        self.assertEqual([k for _, k in done], ["escrito"] * 3)
        self.assertTrue(os.path.isfile(os.path.join(self.d1, STEM + ".md")))
        self.assertTrue(os.path.isfile(os.path.join(self.d2, "Alfa", STEM + ".txt")))  # subcarpeta por proyecto
        self.assertTrue(os.path.isfile(os.path.join(self.d3, STEM + ".docx")))
        self.assertEqual([k for _, k in salidas.write_all(sample_doc(), self.destinos, self.reg)], ["igual"] * 3)
        done = salidas.write_all(sample_doc(notes="otras notas"), self.destinos, self.reg)  # se edito: se reescribe
        self.assertEqual([k for _, k in done], ["escrito"] * 3)
        self.assertIn("otras notas", self.read(os.path.join(self.d1, STEM + ".md")))
        self.assertIn("otras notas", self.read(os.path.join(self.d2, "Alfa", STEM + ".txt")))
        salidas.write_all(sample_doc(stem="otra", props={}), self.destinos, self.reg)  # sin proyecto: en la carpeta
        self.assertTrue(os.path.isfile(os.path.join(self.d2, "otra.txt")))
        logs = []
        salidas.write_all(sample_doc(notes="n3"), self.destinos, self.reg, logs.append)
        self.assertEqual(len(logs), 1)
        self.assertIn("documento escrito en 3 destino(s)", logs[0])
        self.assertEqual(salidas.write_all(sample_doc(), [], self.reg), [])

    def test_no_pisa_lo_ajeno_ni_lo_nuestro_cambiado_en_el_destino(self):
        ajeno = os.path.join(self.d1, STEM + ".md")
        with open(ajeno, "w", encoding="utf-8") as f:
            f.write("# mis notas de Obsidian\n")
        done = salidas.write_all(sample_doc(), [self.destinos[0]], self.reg)
        self.assertEqual(done[0][1], "ajeno")
        self.assertEqual(self.read(ajeno), "# mis notas de Obsidian\n")
        # nuestro, pero editado en el destino (sigue llevando la marca): cambiado, no se toca
        salidas.write_all(sample_doc(), [self.destinos[1]], self.reg)
        mio = os.path.join(self.d2, "Alfa", STEM + ".txt")
        with open(mio, "a", encoding="utf-8") as f:
            f.write("apunte a mano\n")
        self.assertTrue(salidas.is_ours(mio, "txt"))
        done = salidas.write_all(sample_doc(notes="n2"), [self.destinos[1]], self.reg)
        self.assertEqual(done[0][1], "cambiado")
        self.assertIn("apunte a mano", self.read(mio))
        # nuestro por la marca pero sin registro (otro PC, reinstalacion): la marca basta y se sobrescribe
        reg2 = salidas.Registro(os.path.join(self.data_dir, "otro.json"))
        x = os.path.join(self.d1, "x.md")
        with open(x, "w", encoding="utf-8") as f:
            f.write(salidas.render_md(sample_doc(stem="x")) + "\neditado\n")
        done = salidas.write_all(sample_doc(stem="x", notes="n2"), [self.destinos[0]], reg2)
        self.assertEqual(done[0][1], "escrito")
        self.assertNotIn("editado", self.read(x))
        # carpeta desaparecida: se anota, sin excepcion
        done = salidas.write_all(sample_doc(), [{"path": os.path.join(self.tmp, "no"), "format": "md"}], self.reg)
        self.assertEqual(done[0][1], "sin-carpeta")
        logs = []
        salidas.write_all(sample_doc(notes="n3"), [self.destinos[0], self.destinos[1]], self.reg, logs.append)
        self.assertIn("sin tocar", logs[0])
        self.assertIn("ajeno", logs[0])
        self.assertIn("cambiado", logs[0])


class JobsOutputsTests(TempTree):
    def setUp(self):
        super().setUp()
        self.dest = os.path.join(self.tmp, "vault")
        os.makedirs(self.dest)
        self.rel = "Alfa/" + STEM + ".wav"
        self.wav(self.rel)
        self.write("Alfa/" + STEM + ".srt", srt_text([(0.0, 2.0, "Hola a todos."), (2.0, 4.0, "Empezamos.")]))
        self.write("Alfa/" + STEM + ".md", "---\ntitulo: Kick-off\nproyecto: Alfa\nworkstream: General\n---\n\n"
                                           "## Orden del día\n\n- punto\n")
        self.write("Alfa/" + STEM + ".resumen.md", "---\nmodelo: m1\ngenerado: 2026-10-31 11:00\n---\n\n"
                                                   "## Resumen\n\nTres frases.\n")
        self.jobs = bare_jobs([])
        destino = {"path": self.dest, "format": "md", "by_project": True}
        self.jobs.settings = settings_store(self.tmp, perfil={"outputs": [destino]})
        self.jobs.library = types.SimpleNamespace(find=lambda rel: {"date": "2026-10-31T10:00:00", "duration": 125.0})

    def test_documento_con_resumen_notas_y_transcripcion(self):
        with mock.patch.object(server, "log"):
            done = self.jobs.write_outputs(self.rel)
        self.assertEqual([k for _, k in done], ["escrito"])
        p = os.path.join(self.dest, "Alfa", STEM + ".md")
        with open(p, encoding="utf-8") as f:
            md = f.read()
        self.assertIn('titulo: "Kick-off"', md)
        self.assertIn('fecha: "2026-10-31 10:00"', md)
        self.assertIn('duracion: "2 min"', md)
        self.assertIn('resumen_modelo: "m1"', md)
        self.assertNotIn("modelo: m1\ngenerado", md)  # la cabecera del .resumen.md no se copia
        self.assertIn("### Resumen\n\nTres frases.", md)
        self.assertIn("### Orden del día\n\n- punto", md)
        self.assertIn("[0:00] Hola a todos. Empezamos.", md)
        self.assertIn(f'{server.APP_NAME} (desde la app', md)
        self.assertTrue(os.path.isfile(os.path.join(self.data_dir, "salidas.json")))
        # sin transcripcion no se escribe nada; sin destinos, tampoco
        self.wav("Alfa/2026-10-31 11-00-00.wav")
        self.assertEqual(self.jobs.write_outputs("Alfa/2026-10-31 11-00-00.wav"), [])
        self.jobs.settings.update(lambda d: d.update(perfil={"outputs": []}))
        self.assertEqual(self.jobs.write_outputs(self.rel), [])

    def test_reescritura_agrupada_al_editar(self):
        with mock.patch.object(server, "log"):
            for _ in range(3):
                self.jobs.schedule_outputs(self.rel, delay=0.15)
            self.assertEqual(len(self.jobs._salidas_timers), 1)  # tres guardados seguidos: una sola escritura
            timer = self.jobs._salidas_timers[self.rel]
            timer.join(10)  # sin espera fija: en un runner cargado la escritura puede pasar del segundo
            self.assertFalse(timer.is_alive())
            self.assertEqual(self.jobs._salidas_timers, {})
            self.assertTrue(os.path.isfile(os.path.join(self.dest, "Alfa", STEM + ".md")))
            self.assertEqual([k for _, k in self.jobs.schedule_outputs(self.rel, delay=0)], ["igual"])  # inmediato

    def test_un_temporizador_vencido_no_borra_al_nuevo(self):
        """Si se guarda justo cuando vence la espera anterior, el temporizador nuevo no debe perderse de la lista."""
        with mock.patch.object(server, "log"):
            viejo, nuevo = object(), object()
            self.jobs._salidas_timers = {self.rel: nuevo}
            self.jobs._fire_outputs(self.rel, viejo)  # vence el viejo cuando ya se ha programado el nuevo
            self.assertIs(self.jobs._salidas_timers.get(self.rel), nuevo)
            self.jobs._fire_outputs(self.rel, nuevo)
            self.assertEqual(self.jobs._salidas_timers, {})

    def test_los_ganchos_del_servidor(self):
        """Se escribe al terminar la transcripcion y el resumen, y se reescribe al guardar notas o transcripcion."""
        src = open(server.__file__, encoding="utf-8").read()
        after = src[src.index("def _after_transcribe"):src.index("def document_for")]
        self.assertIn("self.write_outputs(rel)", after)
        summ = src[src.index("def _run_summary"):src.index("def _cloud_ready")]
        self.assertLess(summ.index('save_text(base + ".resumen.md"'), summ.index("self.write_outputs(job.path)"))
        self.assertNotIn("write_outputs", src[src.index("def _run_live"):src.index("def submit(")])  # no a medias
        self.assertIn("self.write_outputs(job.path)", src[src.index("if job.final_pass:  # se queda el texto"):])
        notes = src[src.index("    def notes_save"):src.index("    def tags")]
        self.assertIn("self.jobs.schedule_outputs(rel_of(abs_path))", notes)
        tx = src[src.index("    def save_transcript"):src.index("    def _save_live_edits")]
        self.assertIn("self.jobs.schedule_outputs(rel)", tx)


# ----------------------------------------------------------------------------- importar listas

class ImportVocabTests(TempTree):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.tmp, "otro")
        os.makedirs(os.path.join(self.src, "Beta", "Vocabulario"))
        os.makedirs(os.path.join(self.src, "Alfa", "Vocabulario"))
        os.makedirs(os.path.join(self.src, "_app", "Vocabulario"))  # carpetas de sistema: no cuentan
        os.makedirs(os.path.join(self.src, "Sin listas"))
        listas = (("Beta/Vocabulario/Legal.txt", "Contrato marco\n"), ("Beta/Vocabulario/General.txt", "Beta\n"),
                  ("Beta/Vocabulario/notas.md", "no es una lista\n"), ("Alfa/Vocabulario/Nuevo.txt", "N\n"),
                  ("_app/Vocabulario/no.txt", "x\n"))
        for rel, body in listas:
            with open(os.path.join(self.src, rel), "w", encoding="utf-8") as f:
                f.write(body)
        self.write("Alfa/Vocabulario/General.txt", "API, SLA\n")  # Alfa ya existe aqui

    def listas(self):
        return sorted((v["project"], v["name"]) for v in server.list_vocab())

    def test_importa_proyectos_sin_pisar(self):
        with mock.patch.object(server, "log"):
            r = server.import_vocab(self.src)
        self.assertEqual(r, {"copiadas": 3, "omitidas": 0, "proyectos": ["Alfa", "Beta"]})
        self.assertEqual(self.listas(),
                         [("Alfa", "General"), ("Alfa", "Nuevo"), ("Beta", "General"), ("Beta", "Legal")])
        self.assertFalse(os.path.exists(os.path.join(self.root, "_app", "Vocabulario")))
        self.write("Alfa/Vocabulario/Nuevo.txt", "mío, cambiado\n")
        with mock.patch.object(server, "log"):
            r = server.import_vocab(self.src)
        self.assertEqual((r["copiadas"], r["omitidas"]), (0, 3))
        self.assertEqual(server.read_text(os.path.join(self.root, "Alfa", "Vocabulario", "Nuevo.txt")),
                         "mío, cambiado\n")

    def test_un_proyecto_o_una_carpeta_vocabulario_suelta(self):
        with mock.patch.object(server, "log"):
            r = server.import_vocab(os.path.join(self.src, "Beta"))
            self.assertEqual(r["proyectos"], ["Beta"])
            r = server.import_vocab(os.path.join(self.src, "Beta", "Vocabulario"), project="Gamma")
            self.assertEqual((r["copiadas"], r["proyectos"]), (2, ["Gamma"]))
            r = server.import_vocab(os.path.join(self.src, "Beta", "Vocabulario"))  # sin proyecto: la carpeta padre
            self.assertEqual((r["copiadas"], r["omitidas"], r["proyectos"]), (0, 2, ["Beta"]))
        self.assertIn(("Gamma", "Legal"), self.listas())

    @unittest.skipIf(os.name == "nt", "en Windows no se pueden crear nombres con ':'")
    def test_nombres_no_validos_se_omiten(self):
        with open(os.path.join(self.src, "Beta", "Vocabulario", "mal:nombre.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        os.makedirs(os.path.join(self.src, "pro?yecto", "Vocabulario"))
        with open(os.path.join(self.src, "pro?yecto", "Vocabulario", "A.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        with mock.patch.object(server, "log"):
            r = server.import_vocab(self.src)
        self.assertEqual((r["copiadas"], r["omitidas"], r["proyectos"]), (3, 2, ["Alfa", "Beta"]))
        names = os.listdir(os.path.join(self.root, "Beta", "Vocabulario"))
        self.assertFalse(any(":" in n or "?" in n for n in names))

    def test_errores(self):
        for path in (os.path.join(self.tmp, "no-existe"), os.path.join(self.src, "Sin listas"), "relativa", "",
                     os.path.join(self.root, "_app")):
            with self.assertRaises(server.ApiError, msg=path):
                server.import_vocab(path)


# ----------------------------------------------------------------------------- rutas

class RutasPerfilTests(TempTree):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls._app, cls._port = server.Handler.app, server.PORT
        server.PORT = cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.Handler.app, server.PORT = cls._app, cls._port

    def setUp(self):
        super().setUp()
        self.write("Alfa/Vocabulario/General.txt", "API, SLA\n")
        os.makedirs(os.path.join(self.root, "Alfa", "Reuniones"))
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, language="es")
        a.vigia = calendario.Vigia()
        a.library = types.SimpleNamespace(at=1)
        server.Handler.app = a
        self.app = a

    def call(self, path, body=None, headers=True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST" if data else "GET")
        if data and headers:
            req.add_header("X-Audios", "1")
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def test_get_y_post_perfil(self):
        r = self.call("/api/perfil")
        self.assertEqual(set(r), {"perfil", "resuelto"})
        self.assertEqual(r["perfil"]["language"], "es")
        self.assertEqual(r["resuelto"]["origen"]["folder"], "ultimo")
        r = self.call("/api/perfil", {"folder": "Alfa/Reuniones", "context": "ctx", "language": "es-pt"})
        self.assertEqual((r["perfil"]["folder"], r["perfil"]["context"], r["perfil"]["language"]),
                         ("Alfa/Reuniones", "ctx", "es-pt"))
        self.assertEqual(self.app.settings.get()["language"], "es-pt")
        r = self.call("/api/perfil")
        self.assertEqual((r["resuelto"]["folder"], r["resuelto"]["origen"]["folder"]), ("Alfa/Reuniones", "perfil"))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call("/api/perfil", {"folder": "NoExiste"})
        self.assertEqual(cm.exception.code, 404)  # safe(): la carpeta no existe
        self.assertIn("No existe la carpeta", json.load(cm.exception)["error"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call("/api/perfil", {"context": "x"}, headers=False)  # sin X-Audios: prohibido
        self.assertEqual(cm.exception.code, 403)

    def test_post_vocab_import(self):
        src = os.path.join(self.tmp, "otro", "Beta", "Vocabulario")
        os.makedirs(src)
        with open(os.path.join(src, "Legal.txt"), "w", encoding="utf-8") as f:
            f.write("Contrato marco\n")
        with mock.patch.object(server, "log"):
            r = self.call("/api/vocab/import", {"path": os.path.join(self.tmp, "otro")})
        self.assertEqual(r, {"copiadas": 1, "omitidas": 0, "proyectos": ["Beta"]})
        self.assertEqual(self.app.library.at, 0)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call("/api/vocab/import", {"path": os.path.join(self.tmp, "nada")})
        self.assertEqual(cm.exception.code, 400)


class ConfigPerfilTests(unittest.TestCase):
    def test_config_lleva_el_perfil(self):
        src = open(server.__file__, encoding="utf-8").read()
        cfg = src[src.index("    def config(self, q):"):src.index("    def tree(self, q):")]
        self.assertIn('"perfil": perfil_de(st)', cfg)
        # y las rutas nuevas estan registradas
        self.assertIn('"/api/perfil": a.perfil_get', src)
        self.assertIn('"/api/perfil": a.perfil_save', src)
        self.assertIn('"/api/vocab/import": a.vocab_import', src)
        self.assertLess(time.time(), time.time() + 1)


if __name__ == "__main__":
    unittest.main()
