# -*- coding: utf-8 -*-
"""Perfil de reunión: ajustes por defecto compatibles, precedencias (elegido > perfil > reunión en curso > último),
idioma y contexto en la cabecera del .md (también al grabar), build_prompt con contexto libre (el vocabulario tiene
prioridad al recortar), relectura por trozo en live.run, copia de textos a otra carpeta (sin pisar), importación de
listas y las rutas /api/perfil y /api/vocab/import. Todo con dobles: sin red, sin Windows."""
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
from unittest import mock

from support import TempTree, bare_jobs, engines, live, server  # noqa: F401  (support prepara sys.path)
import calendario  # noqa: E402
from test_directo import DirectoJob, FakeCloud, FakeRecorder, FakeSummarizer, FakeWorker, energy_vad, synth  # noqa: E402


def settings_store(tmp=None, **extra):
    tmp = tmp or tempfile.mkdtemp(prefix="notas-perfil-")
    st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
    if extra:
        st.update(lambda d: d.update(extra))
    return st


# ----------------------------------------------------------------------------- ajustes y precedencias

class AjustesPerfilTests(unittest.TestCase):
    def test_claves_por_defecto_en_automatico(self):
        self.assertEqual(server.DEFAULT_SETTINGS["perfil"], {"folder": None, "vocab": None, "context": "", "copy_to": ""})
        self.assertEqual(server.PERFIL_KEYS, ("folder", "vocab", "context", "copy_to"))

    def test_una_instalacion_anterior_no_cambia(self):
        """settings.json sin "perfil": todo sigue en automatico (ultima carpeta, ultimo tema, idioma general)."""
        tmp = tempfile.mkdtemp(prefix="notas-perfil-")
        with open(os.path.join(tmp, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"language": "es-en", "rec_vocab": "Alfa/Vocabulario/General.txt", "rec_folder": "Alfa"}, f)
        st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS).get()
        self.assertEqual(server.perfil_de(st), {"folder": None, "vocab": None, "context": "", "copy_to": "",
                                                "language": "es-en"})
        self.assertEqual(server.perfil_vocab(st), "Alfa/Vocabulario/General.txt")  # el ultimo elegido
        self.assertEqual(server.perfil_vocab(st, "Beta/Vocabulario/X.txt"), "Beta/Vocabulario/X.txt")  # la reunion
        r = server.resolve_perfil(st, {"vocab": "Beta/Vocabulario/X.txt", "name": "Comité"})
        self.assertEqual((r["vocab"], r["folder"], r["language"], r["name"]),
                         ("Beta/Vocabulario/X.txt", "Alfa", "es-en", "Comité"))
        self.assertEqual(r["origen"], {"vocab": "reunion", "folder": "ultimo", "language": "perfil",
                                       "context": "perfil", "name": "reunion", "participantes": "ultimo",
                                       "copy_to": "perfil"})

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
        self.assertTrue(all(v == "elegido" for k, v in r["origen"].items() if k not in ("participantes", "copy_to")))

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
        ok = server.check_perfil({"folder": "Alfa/Reuniones", "vocab": "Alfa/Vocabulario/General.txt",
                                  "context": "  dos   frases  ", "copy_to": "", "language": "ES-PT"})
        self.assertEqual(ok, {"folder": "Alfa/Reuniones", "vocab": "Alfa/Vocabulario/General.txt",
                              "context": "dos frases", "copy_to": "", "language": "es-pt"})
        self.assertEqual(server.check_perfil({"folder": None, "vocab": None}), {"folder": None, "vocab": None})
        self.assertEqual(server.check_perfil({"context": "x"}), {"context": "x"})  # parcial: solo lo que viene
        for bad in ({"folder": "NoExiste"}, {"folder": "_app"}, {"vocab": "Zeta/Vocabulario/X.txt"},
                    {"copy_to": os.path.join(self.tmp, "no")}, {"language": "xx-yy-zz"}, {"copy_to": "relativa"}):
            with self.assertRaises(server.ApiError, msg=bad):
                server.check_perfil(bad)
        d = {"perfil": {"folder": "Otra"}, "language": "es"}
        server.update_perfil(d, server.check_perfil({"context": "c", "language": "pt"}))
        self.assertEqual(d, {"perfil": {"folder": "Otra", "vocab": None, "context": "c", "copy_to": ""}, "language": "pt"})

    def test_save_settings_con_perfil(self):
        a = server.App.__new__(server.App)
        a.settings = settings_store()
        a.config = lambda q: {}
        a.save_settings({"perfil": {"folder": "Alfa/Reuniones", "vocab": None, "context": "ctx", "copy_to": ""},
                         "preset": "nube", "language": "pt"})
        st = a.settings.get()
        self.assertEqual(st["perfil"], {"folder": "Alfa/Reuniones", "vocab": None, "context": "ctx", "copy_to": ""})
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
        a.jobs = types.SimpleNamespace(refreshed=[])
        a.jobs.refresh_prompt = a.jobs.refreshed.append
        return a

    def test_roundtrip_de_las_claves_nuevas(self):
        props = {"proyecto": "Alfa", "workstream": "General", "idioma": "es-pt", "contexto": "Comité semanal",
                 "copiar_a": r"C:\Proyecto\Textos"}
        text = server.dump_notes(props, "notas")
        self.assertIn("idioma: es-pt\ncontexto: Comité semanal\ncopiar_a: C:\\Proyecto\\Textos\n", text)
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
    """rec_start escribe en el .md el idioma y el contexto de esa reunion (y copiar_a si el perfil lo pide) y ya no
    cambia el idioma general al elegir otro para una grabacion."""

    def app(self, **st):
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, **st)
        a.rec_lock, a.notes_lock, a.rec, a.monitor = threading.Lock(), threading.Lock(), None, None
        a.library = types.SimpleNamespace(at=1)
        a.jobs = bare_jobs([])
        a.jobs.started = []
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
        a = self.app(language="es", perfil={"context": "Reuniones del proyecto Alfa", "copy_to": ""})
        _, props = self.start(a, language="es-pt", contexto="  Comité   de seguimiento ")
        self.assertEqual(props, {"idioma": "es-pt", "contexto": "Comité de seguimiento"})
        self.assertEqual(a.settings.get()["language"], "es")  # el idioma de ESTA reunion no cambia el general
        self.assertEqual(len(a.jobs.started), 1)
        self.assertEqual(server.meeting_setup(server.safe(a.jobs.started[0][0]), a.settings.get())["language"], "es-pt")

    def test_detectar_se_guarda_como_auto_y_sin_contexto_vale_el_del_perfil(self):
        a = self.app(language="pt", perfil={"context": "Reuniones del proyecto Alfa", "copy_to": ""})
        _, props = self.start(a, language="")
        self.assertEqual(props, {"idioma": "auto", "contexto": "Reuniones del proyecto Alfa"})
        self.assertEqual(server.idioma_de(props, a.settings.get()), "")
        _, props = self.start(a, language="pt", contexto="", name="y")  # contexto vaciado a proposito: ninguno
        self.assertEqual(props, {"idioma": "pt"})

    def test_copiar_a_solo_si_el_perfil_lo_pide_y_la_carpeta_existe(self):
        dest = os.path.join(self.tmp, "textos")
        os.makedirs(dest)
        a = self.app(perfil={"copy_to": dest})
        _, props = self.start(a, language="es")
        self.assertEqual(props["copiar_a"], dest)
        a = self.app(perfil={"copy_to": os.path.join(self.tmp, "no-existe")})
        _, props = self.start(a, language="es", name="z")
        self.assertNotIn("copiar_a", props)


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


# ----------------------------------------------------------------------------- copia de textos a otra carpeta

class CopyTextsTests(TempTree):
    def setUp(self):
        super().setUp()
        self.audio = self.wav("Alfa/2026-10-31 10-00-00.wav")
        self.write("Alfa/2026-10-31 10-00-00.txt", "texto\n")
        self.write("Alfa/2026-10-31 10-00-00.md", "---\ntitulo: T\n---\n")
        self.dest = os.path.join(self.tmp, "textos")
        os.makedirs(self.dest)
        self.copias = server.Copias(os.path.join(self.data_dir, "copias.json"))

    def read(self, name):
        with open(os.path.join(self.dest, name), encoding="utf-8-sig") as f:
            return f.read()

    def test_copia_actualiza_lo_suyo_y_no_pisa_lo_ajeno(self):
        done = server.copy_texts(self.audio, self.dest, self.copias)
        self.assertEqual(done, [(".txt", "copiado"), (".md", "copiado"), (".resumen.md", "sin")])
        self.assertEqual(self.read("2026-10-31 10-00-00.txt"), "texto\n")
        self.assertEqual(server.copy_texts(self.audio, self.dest, self.copias)[0], (".txt", "igual"))
        self.write("Alfa/2026-10-31 10-00-00.txt", "texto corregido\n")  # repaso final: nuestra copia se actualiza
        self.write("Alfa/2026-10-31 10-00-00.resumen.md", "## Resumen\n")
        done = server.copy_texts(self.audio, self.dest, self.copias)
        self.assertEqual(done, [(".txt", "copiado"), (".md", "igual"), (".resumen.md", "copiado")])
        self.assertEqual(self.read("2026-10-31 10-00-00.txt"), "texto corregido\n")
        # alguien edita la copia en el destino: nunca se pisa, aunque el original cambie otra vez
        with open(os.path.join(self.dest, "2026-10-31 10-00-00.txt"), "w", encoding="utf-8") as f:
            f.write("editado a mano\n")
        self.write("Alfa/2026-10-31 10-00-00.txt", "otra versión\n")
        done = server.copy_texts(self.audio, self.dest, self.copias)
        self.assertEqual(done[0], (".txt", "omitido"))
        self.assertEqual(self.read("2026-10-31 10-00-00.txt"), "editado a mano\n")
        # el registro sobrevive a un reinicio
        again = server.Copias(os.path.join(self.data_dir, "copias.json"))
        self.assertEqual(again.get(os.path.join(self.dest, "2026-10-31 10-00-00.md")),
                         server.sha256_of(os.path.join(self.root, "Alfa", "2026-10-31 10-00-00.md")))
        # un fichero ajeno que ya estaba en el destino tampoco se toca
        with open(os.path.join(self.dest, "2026-10-31 11-00-00.txt"), "w", encoding="utf-8") as f:
            f.write("de otro\n")
        otro = self.wav("Alfa/2026-10-31 11-00-00.wav")
        self.write("Alfa/2026-10-31 11-00-00.txt", "mío\n")
        self.assertEqual(server.copy_texts(otro, self.dest, self.copias)[0], (".txt", "omitido"))
        self.assertEqual(self.read("2026-10-31 11-00-00.txt"), "de otro\n")
        # la propia carpeta de la grabacion: nada que copiar
        self.assertEqual(server.copy_texts(self.audio, os.path.dirname(self.audio), self.copias), [])

    def test_jobs_copy_texts_lee_copiar_a_del_md(self):
        jobs = bare_jobs([])
        rel = "Alfa/2026-10-31 10-00-00.wav"
        with mock.patch.object(server, "log") as logm:
            self.assertIsNone(jobs.copy_texts(rel))  # sin copiar_a: nada
            self.write("Alfa/2026-10-31 10-00-00.md", f"---\ncopiar_a: {self.dest}\n---\n")
            done = jobs.copy_texts(rel)
            self.assertEqual([k for _, k in done], ["copiado", "copiado", "sin"])
            self.assertTrue(os.path.isfile(os.path.join(self.dest, "2026-10-31 10-00-00.md")))
            self.assertTrue(any("textos copiados" in str(c.args[0]) for c in logm.call_args_list))
            self.write("Alfa/2026-10-31 10-00-00.md", f"---\ncopiar_a: {os.path.join(self.tmp, 'ya-no')}\n---\n")
            self.assertIsNone(jobs.copy_texts(rel))  # carpeta no disponible: se avisa y no falla
            self.assertTrue(any("no está disponible" in str(c.args[0]) for c in logm.call_args_list))
        self.assertTrue(os.path.isfile(os.path.join(self.data_dir, "copias.json")))

    def test_se_copia_al_terminar_la_transcripcion_y_el_resumen(self):
        """_after_transcribe y el final de _run_summary llaman a copy_texts; la copia nunca se hace antes."""
        src = open(server.__file__, encoding="utf-8").read()
        after = src[src.index("def _after_transcribe"):src.index("def copy_texts(self")]
        self.assertIn("self.copy_texts(rel)", after)
        summ = src[src.index("def _run_summary"):src.index("def _cloud_ready")]
        self.assertIn("self.copy_texts(job.path)", summ)
        self.assertLess(summ.index('save_text(base + ".resumen.md"'), summ.index("self.copy_texts(job.path)"))
        live_run = src[src.index("def _run_live"):src.index("def submit(")]
        self.assertNotIn("copy_texts", live_run)  # en directo no se copia a medias
        self.assertIn("self.copy_texts(job.path)", src[src.index("if job.final_pass:  # se queda el texto"):])


# ----------------------------------------------------------------------------- importar listas

class ImportVocabTests(TempTree):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.tmp, "otro")
        os.makedirs(os.path.join(self.src, "Beta", "Vocabulario"))
        os.makedirs(os.path.join(self.src, "Alfa", "Vocabulario"))
        os.makedirs(os.path.join(self.src, "_app", "Vocabulario"))  # carpetas de sistema: no cuentan
        os.makedirs(os.path.join(self.src, "Sin listas"))
        for rel, body in (("Beta/Vocabulario/Legal.txt", "Contrato marco\n"), ("Beta/Vocabulario/General.txt", "Beta\n"),
                          ("Beta/Vocabulario/notas.md", "no es una lista\n"), ("Alfa/Vocabulario/Nuevo.txt", "N\n"),
                          ("_app/Vocabulario/no.txt", "x\n")):
            with open(os.path.join(self.src, rel), "w", encoding="utf-8") as f:
                f.write(body)
        self.write("Alfa/Vocabulario/General.txt", "API, SLA\n")  # Alfa ya existe aqui

    def listas(self):
        return sorted((v["project"], v["name"]) for v in server.list_vocab())

    def test_importa_proyectos_sin_pisar(self):
        with mock.patch.object(server, "log"):
            r = server.import_vocab(self.src)
        self.assertEqual(r, {"copiadas": 3, "omitidas": 0, "proyectos": ["Alfa", "Beta"]})
        self.assertEqual(self.listas(), [("Alfa", "General"), ("Alfa", "Nuevo"), ("Beta", "General"), ("Beta", "Legal")])
        self.assertFalse(os.path.exists(os.path.join(self.root, "_app", "Vocabulario")))
        self.write("Alfa/Vocabulario/Nuevo.txt", "mío, cambiado\n")
        with mock.patch.object(server, "log"):
            r = server.import_vocab(self.src)
        self.assertEqual((r["copiadas"], r["omitidas"]), (0, 3))
        self.assertEqual(server.read_text(os.path.join(self.root, "Alfa", "Vocabulario", "Nuevo.txt")), "mío, cambiado\n")

    def test_un_proyecto_o_una_carpeta_vocabulario_suelta(self):
        with mock.patch.object(server, "log"):
            r = server.import_vocab(os.path.join(self.src, "Beta"))
            self.assertEqual(r["proyectos"], ["Beta"])
            r = server.import_vocab(os.path.join(self.src, "Beta", "Vocabulario"), project="Gamma")
            self.assertEqual((r["copiadas"], r["proyectos"]), (2, ["Gamma"]))
            r = server.import_vocab(os.path.join(self.src, "Beta", "Vocabulario"))  # sin proyecto: el de la carpeta padre
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
        self.assertFalse(any(":" in n or "?" in n for n in os.listdir(os.path.join(self.root, "Beta", "Vocabulario"))))

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
