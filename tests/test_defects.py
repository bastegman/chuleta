# -*- coding: utf-8 -*-
"""Un test por defecto corregido (numeración de la ficha de arquitectura del 30/10/2026 y del CHANGELOG).
Cada uno fallaba antes de su corrección."""
import os
import re
import sys
import time
import types
import unittest
import zipfile
from unittest import mock

import numpy as np

from support import REPO, FakeJob, TempTree, bare_jobs, engines, server


class Defecto2ColaResumen(unittest.TestCase):
    """active_for no debe confundir un resumen con una transcripción: mientras se resume se puede editar,
    mover (no), borrar (no) y volver a transcribir."""

    def test_active_for_ignora_resumenes(self):
        s = FakeJob("a.wav", kind="summary", status="running")
        jobs = bare_jobs([s])
        self.assertIsNone(jobs.active_for("a.wav"))
        self.assertIs(jobs.active_for("a.wav", kinds=("summary",)), s)
        t = FakeJob("a.wav", kind="transcribe", status="queued")
        jobs.list.append(t)
        self.assertIs(jobs.active_for("a.wav"), t)

    def _app(self, jobs):
        app = server.App.__new__(server.App)
        app.jobs, app.rec = jobs, None
        app.library = mock.Mock()
        app.library.find.return_value = None
        return app

    def test_borrar_y_mover_avisan_del_resumen(self):
        app = self._app(bare_jobs([FakeJob("a.wav", kind="summary", status="running")]))
        with mock.patch.object(server, "safe", return_value=os.path.join(server.ROOT, "a.wav")):
            with self.assertRaises(server.ApiError) as cm:
                app.delete({"path": "a.wav"})
            self.assertIn("resum", cm.exception.msg.lower())
            # mover mientras se resume ya no falla (07/11/2026, cambiar la carpeta en pleno directo): se apunta y se
            # mueve al terminar el resumen; lo que no puede pasar es moverla ahora (el .resumen.md iria a la ruta vieja)
            app._move_now = mock.Mock(side_effect=AssertionError("se ha movido mientras se resumia"))
            app._destino_pendiente = mock.Mock(return_value={"path": "a.wav", "pendiente": {"folder": ""}})
            self.assertEqual(app.move({"path": "a.wav", "folder": ""})["pendiente"], {"folder": ""})
            self.assertIn("resum", app._ocupada("a.wav").lower())
            with self.assertRaises(server.ApiError) as cm:  # renombrarla, cuando termine
                app.move({"path": "a.wav", "folder": "", "name": "b"})
            self.assertIn("resum", cm.exception.msg.lower())

    def test_guardar_transcripcion_mientras_se_resume(self):
        app = self._app(bare_jobs([FakeJob("a.wav", kind="summary", status="running")]))
        with mock.patch.object(server, "safe", return_value=os.path.join(server.ROOT, "a.wav")):
            with self.assertRaises(server.ApiError) as cm:
                app.save_transcript({"path": "a.wav", "segments": []})
        # llega al guardado (no la bloquea el resumen)
        self.assertEqual(cm.exception.msg, "La transcripción no puede quedar vacía.")


class Defecto3PaqueteConExport(unittest.TestCase):
    def test_el_zip_lleva_las_plantillas_y_todo_el_codigo(self):
        import tempfile
        dest = os.path.join(tempfile.mkdtemp(prefix="notas-pkg-"), "p.zip")
        server.build_package(dest)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            for n in ("INSTALAR.bat", "ABRIR CHULETA.bat", "LEEME.txt"):
                self.assertIn("Chuleta/_app/export/" + n, names)
            tpl = z.read("Chuleta/_app/export/LEEME.txt")
            self.assertIn(b"{FECHA}", tpl)  # la plantilla conserva el marcador
            self.assertTrue(tpl.startswith("\ufeff".encode("utf-8")))
            self.assertNotIn(b"{FECHA}", z.read("Chuleta/LEEME.txt"))
            code = {n for n in os.listdir(os.path.join(REPO, "_app")) if n.endswith((".py", ".bat", ".ps1"))}
            self.assertTrue(code >= {"server.py", "engines.py", "worker.py", "live.py", "recorder.py"})
            in_app = {n[len("Chuleta/_app/"):] for n in names if n.count("/") == 2 and n.startswith("Chuleta/_app/")}
            self.assertEqual(in_app, code | {"version.txt"})  # version.txt lo escribe build_package
        self.assertEqual(sorted(server.export_code()), sorted(code))


class Defecto4DecodePerezoso(unittest.TestCase):
    """decode_16k debe consumir los frames según los remuestrea (no materializar el archivo entero antes)."""

    def test_no_consume_todo_el_generador(self):
        pulled = []

        class Frame:
            def __init__(self, i):
                self.i = i

            def to_ndarray(self):
                return np.zeros((1, 160), np.int16)

        def decode(stream):
            for i in range(50):
                pulled.append(i)
                yield Frame(i)

        class Container:
            streams = [types.SimpleNamespace(type="audio")]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def decode(self, s):
                return decode(s)

        class Resampler:
            def __init__(self, **kw):
                pass

            def resample(self, fr):
                return [fr] if fr is not None else []

        fake_av = types.SimpleNamespace(open=lambda path: Container(), AudioResampler=Resampler)
        calls = {"n": 0}

        def cancelled():
            calls["n"] += 1
            return calls["n"] > 3  # cancela tras tres frames

        with mock.patch.dict(sys.modules, {"av": fake_av}):
            with self.assertRaises(engines.Cancelled):
                engines.decode_16k("x.wav", cancelled=cancelled)
        self.assertLess(len(pulled), 50, "se decodificó todo el audio antes de remuestrear nada")

    def test_decodifica_y_salta_el_inicio(self):
        class Frame:
            def to_ndarray(self):
                return np.arange(1600, dtype=np.int16).reshape(1, -1)

        class Container:
            streams = [types.SimpleNamespace(type="audio")]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def decode(self, s):
                return iter([Frame(), Frame()])

        class Resampler:
            def __init__(self, **kw):
                pass

            def resample(self, fr):
                return [fr] if fr is not None else []

        fake_av = types.SimpleNamespace(open=lambda path: Container(), AudioResampler=Resampler)
        with mock.patch.dict(sys.modules, {"av": fake_av}):
            y = engines.decode_16k("x.wav", start=0.1)  # salta 1600 muestras: queda el segundo frame
        self.assertEqual(len(y), 1600)
        self.assertEqual(y[0], 0)


class Defecto5VocabularioOculto(TempTree):
    def test_el_arbol_y_la_busqueda_no_ensenan_vocabulario(self):
        self.write("Alfa/Vocabulario/Legal.txt", "Acme, Beta\n")
        self.write("Alfa/acta.txt", "acta\n")
        tree, flat = server.scan()
        alfa = tree["folders"][0]
        self.assertEqual(alfa["name"], "Alfa")
        self.assertEqual([f["name"] for f in alfa["folders"]], [])
        self.assertEqual([d["name"] for d in alfa["docs"]], ["acta.txt"])
        self.assertEqual([v["name"] for v in server.list_vocab()], ["Legal"])  # pero se sigue leyendo


class Defecto6Menores(TempTree):
    def test_rec_live_ya_no_existe(self):
        self.assertNotIn("rec_live", server.DEFAULT_SETTINGS)

    def test_list_vocab_recorre_carpetas_vinculadas(self):
        ext = os.path.join(self.tmp, "Externa")
        os.makedirs(os.path.join(ext, "Vocabulario"))
        with open(os.path.join(ext, "Vocabulario", "Infra.txt"), "w", encoding="utf-8") as f:
            f.write("a, b\n")
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "Proyecto externo", "path": ext}
        vs = server.list_vocab()
        self.assertEqual([(v["project"], v["name"], v["id"]) for v in vs],
                         [("Proyecto externo", "Infra", f":{lid}/Vocabulario/Infra.txt")])
        self.assertEqual(server.find_vocab("Proyecto externo", "Infra")["id"], vs[0]["id"])

    def test_resumen_en_ingles_con_cabeceras_en_ingles(self):
        s = engines.Summarizer()
        seen = {}

        def fake_chat(cfg, system, user, max_tokens, report, cancelled):
            seen["system"], seen["user"] = system, user
            return "## Summary\nok", "m"

        segs, meta = [{"start": 0, "end": 1, "text": "hello"}], {"title": "t", "date": "d", "duration": "1 min"}
        with mock.patch.object(s, "_chat", fake_chat):
            s.summarize(segs, meta, {"summary_lang": "en"}, lambda **k: None, lambda: False)
            self.assertIn("## Next steps", seen["user"])
            self.assertNotIn("## Próximos pasos", seen["user"])
            self.assertIn("inglés", seen["system"])
            s.summarize(segs, meta, {"summary_lang": "es"}, lambda **k: None, lambda: False)
            self.assertIn("## Próximos pasos", seen["user"])

    def test_rename_folder_con_grabacion_dentro(self):
        os.makedirs(os.path.join(self.root, "Reuniones"))
        app = server.App.__new__(server.App)
        app.jobs = bare_jobs([])
        app.rec = mock.Mock()
        app.rec.alive.return_value = True
        app.rec.path = os.path.join(self.root, "Reuniones", "2026-10-30 10-00-00.wav")
        with self.assertRaises(server.ApiError) as cm:
            app.rename_folder({"path": "Reuniones", "name": "Otra"})
        self.assertIn("grabando", cm.exception.msg.lower())
        self.assertTrue(os.path.isdir(os.path.join(self.root, "Reuniones")))

    def test_max_completion_tokens_reintenta_con_max_tokens(self):
        bodies = []

        class Resp:
            def __init__(self, code, payload, text=""):
                self.status_code, self._p, self.text, self.headers = code, payload, text, {}

            def json(self):
                return self._p

        def post(url, json=None, timeout=None, headers=None):
            bodies.append(dict(json))
            if len(bodies) == 1:
                return Resp(400, {"error": {"message": "Unrecognized request argument: max_completion_tokens"}},
                            "max_completion_tokens")
            return Resp(200, {"choices": [{"message": {"content": "listo"}}]})

        fake_requests = types.SimpleNamespace(post=post, RequestException=Exception)
        s = engines.Summarizer()
        cfg = {"provider": "custom", "base_url": "https://pasarela.local/v1", "api_key": "k", "summary_model": "m"}
        with mock.patch.dict(sys.modules, {"requests": fake_requests}):
            text, model = s._chat(cfg, "sys", "user", 100, lambda **k: None, lambda: False)
        self.assertEqual((text, model), ("listo", "m"))
        self.assertEqual(len(bodies), 2)
        self.assertIn("max_completion_tokens", bodies[0])
        self.assertNotIn("max_completion_tokens", bodies[1])
        self.assertEqual(bodies[1]["max_tokens"], 100)

    def test_otro_400_no_se_reintenta(self):
        class Resp:
            status_code, text, headers = 400, "model not found", {}

            def json(self):
                return {"error": {"message": "model not found"}}

        calls = []
        fake_requests = types.SimpleNamespace(post=lambda *a, **k: calls.append(1) or Resp(),
                                              RequestException=Exception)
        with mock.patch.dict(sys.modules, {"requests": fake_requests}):
            with self.assertRaises(engines.EngineError):
                engines.Summarizer()._chat({"provider": "groq", "api_key": "k"}, "s", "u", 10, lambda **k: None,
                                           lambda: False)
        self.assertEqual(len(calls), 1)


class Defecto7Configuracion(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(REPO, *parts), "rb") as f:
            return f.read().decode("utf-8-sig")

    def test_modelo_de_nube_por_defecto_el_mas_preciso(self):
        self.assertEqual(server.DEFAULT_SETTINGS["cloud"]["model"], "whisper-large-v3")
        self.assertIn("whisper-large-v3", engines.PROVIDERS["groq"]["models"])
        self.assertIn("whisper-large-v3", self.read("LEEME.txt"))

    def test_modelo_de_resumen_dudoso_marcado(self):
        src = self.read("_app", "engines.py")
        self.assertRegex(src, r"qwen/qwen3\.8-27b.*\n?.*TODO verificar")

    def test_instalador_prefiere_python_con_wheels(self):
        ps1 = self.read("_app", "instalar.ps1")
        order = [m.start() for m in re.finditer(r'@\("py", "-3\.1[0-3]"\)|@\("python"\)', ps1)]
        self.assertEqual(len(order), 5, "py -3.13, -3.12, -3.11, -3.10 y python, en ese orden")
        self.assertLess(ps1.find('"-3.13"'), ps1.find('"-3.12"'))
        self.assertLess(ps1.find('"-3.10"'), ps1.find('@("python")'))
        self.assertIn("No matching distribution", ps1)  # distingue "sin wheels" de "proxy"
        self.assertIn("ProxyError", ps1)
        # la actualizacion (y la instalacion en carpeta propia) copian con Copy-AppFiles: el manual nuevo y export
        update = ps1[ps1.find("actualizo esa"):ps1.find("Buscando Python")]
        self.assertIn("Copy-AppFiles $app $root $old $oldRoot", update)
        copia = ps1[ps1.find("function Copy-AppFiles"):ps1.find("function Read-AppVersion")]
        self.assertIn("LEEME.txt", copia)
        self.assertIn('"export"', copia)


class LogServidor(unittest.TestCase):
    def test_setup_logging_escribe_en_el_data_dir(self):
        import logging
        import tempfile
        d = tempfile.mkdtemp(prefix="notas-log-")
        server.setup_logging(d)
        server.log("hola desde el test")
        for h in logging.getLogger("notas").handlers:
            h.flush()
        with open(os.path.join(d, "server.log"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("hola desde el test", text)
        self.assertRegex(text, r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")
        self.assertTrue(server.WINDOWS is (sys.platform == "win32"))
        _ = time  # (el formato de fecha de arriba viene de time.strftime en log())


if __name__ == "__main__":
    unittest.main()
