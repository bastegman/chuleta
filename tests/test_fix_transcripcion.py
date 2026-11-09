# -*- coding: utf-8 -*-
"""Transcribir en el PC de Bernardo con los tres modos (07/11/2026, msgs 1169-1170): «Descargando modelo medium» sin
progreso y sin terminar (S4), el modo de Claude sin transcribir (S2: transcribe Whisper en el PC, así que es S4),
«Modelo» que parecía Groq (S3) y los fallos de conexión con Groq dichos a medias (S1).

- WhisperModels: descarga de verdad (requests) contra un servidor HTTP local que sirve con Range y falla a propósito:
  progreso, reanudar, servidor que ignora Range, huella mal, 403, página HTML, conexión parada, sin espacio, cancelar,
  dos que esperan la misma descarga y lo que ya bajó faster-whisper en versiones anteriores (caché de Hugging Face).
- WorkerClient: descarga antes de cargar y le pasa a worker.py la carpeta local; worker.py, sin red (HF_HUB_OFFLINE).
- Servidor: /api/modelos, descargar y cancelar, y al elegir un modo que transcribe en el PC empieza la descarga.
- Nube: cada error, una frase con lo que ha pasado y qué hacer.
- Interfaz: modelos.js cargado y con sus ganchos protegidos; sus funciones puras con node."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from support import APP, TempTree, bare_jobs, engines, server, wav_bytes  # noqa: F401  (prepara sys.path)
from test_perfil import settings_store  # noqa: E402  (ayudante, no tests)

try:
    import requests  # noqa: F401
except ImportError:  # pragma: no cover - la CI lo instala
    requests = None

STATIC = os.path.join(APP, "static")


def static(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def read_app(name):
    with open(os.path.join(APP, name), encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------- un Hugging Face de juguete

class Servidor:
    """HTTP en 127.0.0.1 con los ficheros de un modelo, Range incluido. `modo[fichero]` es una lista de lo que hace
    cada petición a ese fichero, por orden: "403", "404", "html", "500", "sin_range" (contesta 200 entero),
    "dañado" (bytes cambiados), "corta:N" (manda N bytes y cierra) o "para:N" (manda N bytes y se calla)."""

    def __init__(self, files, modo=None, para_s=2.0):
        self.files, self.modo, self.para_s = files, modo or {}, para_s
        self.peticiones = []
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                name = self.path.rsplit("/", 1)[-1]
                rng = self.headers.get("Range")
                srv.peticiones.append((name, rng))
                b = srv.modo.get(name, []).pop(0) if srv.modo.get(name) else None
                if b in ("403", "404", "500"):
                    body = json.dumps({"error": "no"}).encode()
                    self.send_response(int(b))
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if b == "vacio":  # 200 sin cuerpo (un proxy que no deja pasar y no dice nada)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if b == "html":
                    body = b"<html><body>Portal de acceso</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                data = srv.files.get(name)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                if b and b.startswith("rcorta:"):  # ignora Range (200 entero) y corta en N: un proxy que no reanuda
                    rng, b = None, "corta:" + b.split(":")[1]
                if rng and b != "sin_range":
                    start = int(rng.split("=")[1].split("-")[0])
                body = data[start:]
                if b == "dañado":
                    body = bytes(len(body))
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                if b and b.split(":")[0] in ("corta", "para"):
                    n = int(b.split(":")[1])
                    self.wfile.write(body[:n])
                    self.wfile.flush()
                    if b.startswith("para"):
                        time.sleep(srv.para_s)
                    self.close_connection = True
                    return
                self.wfile.write(body)

        class Srv(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):  # las conexiones cortadas a proposito, sin trazas
                pass
        self.httpd = Srv(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/{{repo}}/resolve/{{rev}}/{{file}}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ficheros(mb=3):
    """Un modelo de juguete: model.bin de `mb` MB (con huella) y los tres pequeños."""
    return {"config.json": b'{"x": 1}', "model.bin": os.urandom(int(mb * 1e6)),
            "tokenizer.json": b'{"t": 1}' * 100, "vocabulary.txt": b"a\nb\n" * 50}


def catalogo(files):
    return {"fake": ("org/fake", "rev1", tuple(
        (n, len(d), hashlib.sha256(d).hexdigest() if n == "model.bin" else None) for n, d in files.items()))}


@unittest.skipUnless(requests, "requests no instalado")
class DescargaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-modelos-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.files = ficheros()
        self.logs = []
        engines._MODEL_CACHED.clear()

    def gestor(self, modo=None, para_s=2.0, **kw):
        self.srv = Servidor(self.files, modo, para_s)
        self.addCleanup(self.srv.close)
        m = engines.WhisperModels(os.path.join(self.tmp, "models", "whisper"), log=self.logs.append, url=self.srv.url,
                                  models=catalogo(self.files), **kw)
        m.sleep = lambda s: time.sleep(min(s, 0.02))  # los reintentos esperan 2, 6, 18 s: aqui, nada
        return m

    def esperar(self, m, j, tope=30):
        fin = time.time() + tope
        while j["running"] and time.time() < fin:
            time.sleep(0.02)
        self.assertFalse(j["running"], "la descarga no ha terminado")

    def test_descarga_con_progreso_y_huella(self):
        m = self.gestor()
        self.assertIsNone(m.path("fake"))
        st = m.state("fake")
        self.assertEqual((st["ready"], st["downloading"], st["size_mb"]), (False, False, 3))
        vistos = []
        p = m.ensure("fake", report=vistos.append)
        self.assertEqual(p, os.path.join(self.tmp, "models", "whisper", "fake"))
        for n, d in self.files.items():
            with open(os.path.join(p, n), "rb") as f:
                self.assertEqual(f.read(), d, n)
        self.assertFalse([x for x in os.listdir(p) if x.endswith(".part")])
        st = m.state("fake")
        self.assertEqual((st["ready"], st["downloading"], st["error"], st["frac"]), (True, False, None, 1.0))
        self.assertTrue(vistos and all("Descargando el modelo fake" in v["stage"] for v in vistos), vistos)
        self.assertTrue(any("modelo fake descargado" in x for x in self.logs), self.logs)
        # y una segunda vez no se pide nada
        n = len(self.srv.peticiones)
        self.assertEqual(m.ensure("fake"), p)
        self.assertEqual(len(self.srv.peticiones), n)

    def test_texto_de_progreso_en_mb_y_porcentaje(self):
        m = self.gestor()
        j = {"running": True, "done": 312_000_000, "total": 1_531_000_000, "error": None, "cancel": False,
             "samples": [(time.time() - 10, 212_000_000), (time.time(), 312_000_000)], "finished": None}
        m.jobs["fake"] = j
        t = m.progress_text("fake")
        self.assertIn("312 de 1.531 MB (20 %)", t)
        self.assertIn("quedan ~2 min", t)  # 1.219 MB a 10 MB/s
        st = m.state("fake")
        self.assertEqual((st["speed_mbs"], st["downloading"]), (10.0, True))

    def test_reanuda_desde_el_part(self):
        m = self.gestor()
        d = os.path.join(self.tmp, "models", "whisper", "fake")
        os.makedirs(d)
        with open(os.path.join(d, "model.bin.part"), "wb") as f:
            f.write(self.files["model.bin"][:1_000_000])
        self.assertTrue(m.ensure("fake"))
        rangos = [r for n, r in self.srv.peticiones if n == "model.bin"]
        self.assertEqual(rangos, ["bytes=1000000-"])
        with open(os.path.join(d, "model.bin"), "rb") as f:
            self.assertEqual(f.read(), self.files["model.bin"])

    def test_servidor_que_ignora_range_empieza_de_cero(self):
        m = self.gestor({"model.bin": ["sin_range"]})
        d = os.path.join(self.tmp, "models", "whisper", "fake")
        os.makedirs(d)
        with open(os.path.join(d, "model.bin.part"), "wb") as f:
            f.write(b"basura" * 1000)  # lo que hubiera: no vale si el servidor manda el fichero entero
        m.ensure("fake")
        with open(os.path.join(d, "model.bin"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), hashlib.sha256(self.files["model.bin"]).hexdigest())

    def test_huella_mal_se_borra_y_se_dice(self):
        m = self.gestor({"model.bin": ["dañado"]})
        with self.assertRaises(engines.EngineError) as c:
            m.ensure("fake")
        self.assertIn("dañado", str(c.exception))
        d = os.path.join(self.tmp, "models", "whisper", "fake")
        self.assertFalse(os.path.exists(os.path.join(d, "model.bin")))
        self.assertFalse(os.path.exists(os.path.join(d, "model.bin.part")))
        self.assertIn("dañado", m.state("fake")["error"])
        # Reintentar: ahora sale bien
        self.assertTrue(m.ensure("fake"))

    def test_403_del_proxy(self):
        m = self.gestor({"config.json": ["403"]})
        with self.assertRaises(engines.EngineError) as c:
            m.ensure("fake")
        msg = str(c.exception)
        self.assertIn("HTTP 403", msg)
        self.assertIn("proxy", msg)
        self.assertNotIn("Traceback", msg)

    def test_404_y_500(self):
        m = self.gestor({"config.json": ["404"]})
        with self.assertRaises(engines.EngineError) as c:
            m.ensure("fake")
        self.assertIn("HTTP 404", str(c.exception))
        m = self.gestor({"config.json": ["500", "500"]})  # dos 500 seguidos: se reintenta y sale
        self.assertTrue(m.ensure("fake"))

    def test_pagina_html_en_lugar_del_modelo(self):
        m = self.gestor({"model.bin": ["html"]})
        with self.assertRaises(engines.EngineError) as c:
            m.ensure("fake")
        self.assertIn("página web", str(c.exception))

    def test_conexion_cortada_sigue_donde_iba(self):
        m = self.gestor({"model.bin": ["corta:1500000"]})
        self.assertTrue(m.ensure("fake"))
        rangos = [r for n, r in self.srv.peticiones if n == "model.bin"]
        self.assertEqual(rangos[0], None)
        self.assertTrue(rangos[1].startswith("bytes=1"), rangos)  # sigue desde lo que llegó, no desde 0

    def test_conexion_parada_se_detecta_y_se_reintenta(self):
        m = self.gestor({"model.bin": ["para:500000"]}, para_s=2.0)
        with mock.patch.object(engines, "DOWNLOAD_STALL_S", 0.5):
            t0 = time.time()
            self.assertTrue(m.ensure("fake"))
        self.assertLess(time.time() - t0, 10)  # no se queda «descargando» indefinidamente
        self.assertTrue(any("reintento 1" in x for x in self.logs), self.logs)

    def test_siempre_parada_falla_con_una_frase(self):
        m = self.gestor({"model.bin": ["para:0"] * 6}, para_s=1.5)
        with mock.patch.object(engines, "DOWNLOAD_STALL_S", 0.3):
            with self.assertRaises(engines.EngineError) as c:
                m.ensure("fake")
        msg = str(c.exception)
        self.assertIn("No se ha podido descargar el modelo fake", msg)
        self.assertIn("ha dejado de enviar datos", msg)

    def test_sin_servidor(self):
        m = self.gestor()
        self.srv.close()
        with self.assertRaises(engines.EngineError) as c:
            m.ensure("fake")
        msg = str(c.exception)
        self.assertIn("No se ha podido descargar el modelo fake", msg)
        self.assertIn("127.0.0.1", msg.replace(engines.WHISPER_HOST, "127.0.0.1"))  # el host, no una traza
        self.assertNotIn("Traceback", msg)

    def test_sin_espacio(self):
        m = self.gestor()
        with mock.patch.object(engines.shutil, "disk_usage", return_value=types.SimpleNamespace(free=1_000_000)):
            with self.assertRaises(engines.EngineError) as c:
                m.ensure("fake")
        self.assertIn("No hay espacio", str(c.exception))
        self.assertEqual(self.srv.peticiones, [])

    def test_cancelar(self):
        m = self.gestor({"model.bin": ["para:200000"]}, para_s=3.0)
        j = m.start("fake")
        time.sleep(0.3)
        self.assertTrue(m.cancel("fake"))
        self.esperar(m, j)
        self.assertIn("cancelada", m.state("fake")["error"])
        self.assertFalse(m.cancel("fake"))  # ya no hay nada en marcha

    def test_quien_espera_se_cancela_y_la_descarga_sigue(self):
        m = self.gestor({"model.bin": ["para:200000"]}, para_s=1.0)
        with mock.patch.object(engines, "DOWNLOAD_STALL_S", 0.5):
            with self.assertRaises(engines.Cancelled):
                m.ensure("fake", cancelled=lambda: True)
            self.esperar(m, m.jobs["fake"])
        self.assertIsNotNone(m.path("fake"))

    def test_dos_que_esperan_comparten_la_descarga(self):
        m = self.gestor()
        out = []
        ts = [threading.Thread(target=lambda: out.append(m.ensure("fake"))) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(len(out), 2)
        self.assertEqual(len([n for n, _ in self.srv.peticiones if n == "model.bin"]), 1)

    def test_respuesta_vacia_no_es_un_bucle_infinito(self):
        """Revisión del 07/11: un 200 sin cuerpo se repetía sin esperar y sin fin (31.694 peticiones en 1,5 s)."""
        m = self.gestor({"model.bin": ["vacio"] * 50})
        with self.assertRaises(engines.EngineError) as c:
            m.ensure("fake")
        self.assertIn("sin enviar datos nuevos", str(c.exception))
        n = len([1 for f, _ in self.srv.peticiones if f == "model.bin"])
        self.assertEqual(n, engines.DOWNLOAD_RETRIES + 1)

    def test_proxy_que_no_reanuda_y_corta_no_es_un_bucle_infinito(self):
        m = self.gestor({"model.bin": ["rcorta:800000"] * 50})
        with self.assertRaises(engines.EngineError) as c:
            m.ensure("fake")
        self.assertIn("No se ha podido descargar el modelo fake", str(c.exception))
        n = len([1 for f, _ in self.srv.peticiones if f == "model.bin"])
        self.assertLessEqual(n, engines.DOWNLOAD_RETRIES + 2)

    def test_descargar_justo_despues_de_cancelar(self):
        m = self.gestor({"model.bin": ["para:200000"]}, para_s=1.0)
        with mock.patch.object(engines, "DOWNLOAD_STALL_S", 0.5):
            j = m.start("fake")
            time.sleep(0.2)
            self.assertTrue(m.cancel("fake"))
            self.assertTrue(m.cancelled_by_user("fake"))
            self.assertIs(m.start("fake"), j)  # la misma descarga, que sigue
            self.assertFalse(m.cancelled_by_user("fake"))
            self.esperar(m, j)
        st = m.state("fake")
        self.assertEqual((st["ready"], st["error"]), (True, None))

    def test_modelo_sin_catalogo(self):
        m = self.gestor()
        self.assertIsNone(m.ensure("otro"))  # sin datos para bajarlo: como antes (que lo intente faster-whisper)
        with self.assertRaises(engines.EngineError):
            m.start("otro")


# ----------------------------------------------------------------------------- lo que ya estaba en el PC

class CacheHuggingFaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-hf-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = mock.patch.dict(os.environ, {"HF_HUB_CACHE": os.path.join(self.tmp, "hub")})
        self.env.start()
        self.addCleanup(self.env.stop)
        engines._MODEL_CACHED.clear()
        self.addCleanup(engines._MODEL_CACHED.clear)

    def snapshot(self, repo, files, layout="symlink"):
        """Como lo deja huggingface_hub: blobs + snapshots/<commit>/<fichero> (enlace, o copia sin enlaces)."""
        base = os.path.join(self.tmp, "hub", "models--" + repo.replace("/", "--"))
        snap = os.path.join(base, "snapshots", "abc123")
        os.makedirs(snap)
        os.makedirs(os.path.join(base, "blobs"))
        for n in files:
            blob = os.path.join(base, "blobs", hashlib.sha1(n.encode()).hexdigest())
            with open(blob, "wb") as f:
                f.write(b"x")
            if layout == "symlink" and hasattr(os, "symlink") and sys.platform != "win32":
                os.symlink(os.path.relpath(blob, snap), os.path.join(snap, n))
            else:
                shutil.copy(blob, os.path.join(snap, n))
        return snap

    def test_lo_de_una_version_anterior_se_usa_sin_descargar(self):
        snap = self.snapshot("Systran/faster-whisper-medium",
                             ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"])
        m = engines.WhisperModels(os.path.join(self.tmp, "propios"))
        self.assertEqual(m.path("medium"), snap)
        self.assertTrue(m.state("medium")["ready"])
        with mock.patch.object(m, "start", side_effect=AssertionError("no debe descargar")):
            self.assertEqual(m.ensure("medium"), snap)

    def test_sin_tokenizer_no_vale(self):
        """Sin tokenizer.json faster-whisper lo pediría a la red: no cuenta como descargado."""
        self.snapshot("Systran/faster-whisper-small", ["config.json", "model.bin", "vocabulary.txt"])
        m = engines.WhisperModels(os.path.join(self.tmp, "propios"))
        self.assertIsNone(m.path("small"))

    def test_copia_sin_enlaces(self):
        snap = self.snapshot("Systran/faster-whisper-small",
                             ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"], layout="copia")
        self.assertEqual(engines.WhisperModels(os.path.join(self.tmp, "p")).path("small"), snap)

    def test_model_cached_mira_el_gestor(self):
        m = engines.WhisperModels(os.path.join(self.tmp, "propios"))
        with mock.patch.object(engines, "MODELS", m):
            self.assertFalse(engines.model_cached("tiny"))
            d = m.own_dir("tiny")
            os.makedirs(d)
            for n, size, _ in engines.WHISPER_MODELS["tiny"][2]:
                with open(os.path.join(d, n), "wb") as f:
                    f.truncate(size)
            engines._MODEL_CACHED.clear()
            self.assertTrue(engines.model_cached("tiny"))
            with open(os.path.join(d, "model.bin"), "ab") as f:  # un tamaño que no es el suyo: incompleto
                f.write(b"x")
            engines._MODEL_CACHED.clear()
            self.assertFalse(engines.model_cached("tiny"))

    def test_catalogo_cubre_los_modelos_de_los_motores_locales(self):
        for p in engines.PRESETS:
            if p["engine"] == "local":
                self.assertIn(p["model"], engines.WHISPER_MODELS, p["id"])
        for name, (repo, rev, files) in engines.WHISPER_MODELS.items():
            self.assertRegex(rev, r"^[0-9a-f]{40}$", name)
            names = [f for f, _, _ in files]
            for need in ("config.json", "model.bin", "tokenizer.json"):
                self.assertIn(need, names, name)
            self.assertTrue(any(n.startswith("vocabulary.") for n in names), name)
            sha = dict((f, h) for f, _, h in files)["model.bin"]
            self.assertRegex(sha, r"^[0-9a-f]{64}$", name)
        self.assertEqual(engines.WHISPER_MODELS["medium"][2][1][1], 1527906378)  # medium: 1,5 GB


# ----------------------------------------------------------------------------- Whisper en su proceso

class WorkerClientTests(unittest.TestCase):
    def test_descarga_antes_de_cargar_y_le_pasa_la_carpeta(self):
        fake = mock.Mock()

        def bajar(name, report, cancelled):
            report({"stage": "Descargando el modelo x: 1 de 2 MB"})
            return "/datos/models/whisper/x"
        fake.ensure.side_effect = bajar
        w = engines.WorkerClient.__new__(engines.WorkerClient)
        w.p, w.key, w.lock = None, None, threading.Lock()
        pedidas, etapas = [], []
        w.request = lambda req, **kw: pedidas.append((req, kw)) or {}
        with mock.patch.object(engines, "MODELS", fake):
            self.assertTrue(w.ensure("x", 4, on_progress=etapas.append))
        self.assertEqual(pedidas[0][0], {"op": "load", "model": "x", "threads": 4,
                                         "model_path": "/datos/models/whisper/x"})
        self.assertEqual(pedidas[0][1]["timeout"], engines.LOAD_TIMEOUT_S)  # ya no 1 h: descargar va aparte
        self.assertEqual([e["stage"] for e in etapas], ["Descargando el modelo x: 1 de 2 MB", "Cargando el modelo x…"])

    def test_si_la_descarga_falla_no_se_carga_nada(self):
        fake = mock.Mock()
        fake.ensure.side_effect = engines.EngineError("huggingface.co ha rechazado la descarga (HTTP 403)")
        w = engines.WorkerClient.__new__(engines.WorkerClient)
        w.p, w.key, w.lock = None, None, threading.Lock()
        w.request = mock.Mock()
        with mock.patch.object(engines, "MODELS", fake):
            with self.assertRaises(engines.EngineError) as c:
                w.ensure("x", 4)
        self.assertIn("HTTP 403", str(c.exception))
        w.request.assert_not_called()

    def test_cada_peticion_lleva_la_carpeta_y_el_proceso_no_toca_la_red(self):
        fake = mock.Mock()
        fake.path.return_value = "/datos/models/whisper/small"
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        w = engines.WorkerClient("prueba", tmp)
        self.addCleanup(w.kill)
        enviado = {}

        class P:
            pid = 1

            def __init__(self):
                self.stdin = self

            def poll(self):
                return None

            def write(self, b):
                enviado.setdefault("msg", b)

            def flush(self):
                pass

            def kill(self):
                pass
        w.p, w.q = P(), __import__("queue").Queue()
        w.q.put({"type": "result", "segments": []})
        with mock.patch.object(engines, "MODELS", fake):
            w.request({"op": "window", "model": "small", "threads": 2})
        self.assertEqual(json.loads(enviado["msg"].decode())["model_path"], "/datos/models/whisper/small")
        # en «file», path es el audio y la carpeta del modelo va aparte (si el proceso se relanzara sin modelo, no
        # intentaría cargar la reunión como si fuera un modelo)
        enviado.clear()
        w.q.put({"type": "result", "segments": []})
        with mock.patch.object(engines, "MODELS", fake):
            w.request({"op": "file", "model": "small", "threads": 2, "path": "/reuniones/a.wav"})
        msg = json.loads(enviado["msg"].decode())
        self.assertEqual((msg["path"], msg["model_path"]), ("/reuniones/a.wav", "/datos/models/whisper/small"))
        # el proceso de Whisper, con HF_HUB_OFFLINE: si faltara un modelo, error claro y no una descarga a ciegas
        with mock.patch.object(engines, "MODELS", fake), mock.patch.object(engines.subprocess, "Popen") as popen:
            popen.return_value.stdout = []
            w._spawn()
        self.assertEqual(popen.call_args.kwargs["env"]["HF_HUB_OFFLINE"], "1")
        with mock.patch.object(engines, "MODELS", None), mock.patch.object(engines.subprocess, "Popen") as popen:
            popen.return_value.stdout = []
            w._spawn()
        self.assertIsNone(popen.call_args.kwargs["env"])  # sin gestor (tests, otras herramientas): como antes

    def test_worker_py_carga_la_carpeta_que_le_mandan(self):
        src = read_app("worker.py")
        self.assertIn('WhisperModel(req.get("model_path") or key[0]', src)
        self.assertNotIn("Descargando modelo", src)  # descargar ya no es cosa suya

    def test_worker_py_no_usa_decode_audio_de_faster_whisper(self):
        """faster-whisper 1.2.1 llama a av.open(..., metadata_errors=...) y PyAV 19 (29/09/2026) ya no lo admite: la
        cola en local fallaba con TypeError en cualquier instalación nueva (lo cazó la E2E). worker.py lee el audio con
        engines.decode_16k (mismo remuestreo) y la E2E de Windows lo ejerce con el PyAV que instala pip."""
        src = read_app("worker.py")
        codigo = "\n".join(line.split("#")[0] for line in src.splitlines())
        self.assertNotIn("decode_audio", codigo)
        self.assertIn('engines.decode_16k(req["path"], offset)', codigo)
        self.assertNotIn("metadata_errors", read_app("engines.py"))


class AudioYUrlTests(unittest.TestCase):
    def test_fotograma_danado_se_salta(self):
        """decode_16k no corta el audio en el primer fotograma dañado (faster-whisper los saltaba)."""
        class InvalidDataError(Exception):
            pass
        av = types.SimpleNamespace(error=types.SimpleNamespace(InvalidDataError=InvalidDataError))

        class Gen:  # un iterador que, como el de PyAV, sigue después de un fotograma dañado
            def __init__(self):
                self.seq = iter([1, InvalidDataError("x"), 2, InvalidDataError("y"), 3])

            def __iter__(self):
                return self

            def __next__(self):
                v = next(self.seq)
                if isinstance(v, Exception):
                    raise v
                return v
        self.assertEqual(list(engines._valid_frames(Gen(), av)), [1, 2, 3])

    def test_host_sin_credenciales(self):
        self.assertEqual(engines.url_host("https://token123@pasarela.empresa/v1/audio"), "pasarela.empresa")
        self.assertEqual(engines.url_host("https://u:clave@x.y:8443/v1"), "x.y")
        self.assertEqual(engines.url_host("https://api.groq.com/openai/v1/audio/transcriptions"), "api.groq.com")
        self.assertEqual(engines.url_host(""), "el servicio")


# ----------------------------------------------------------------------------- servidor

class FakeModels:
    def __init__(self, ready=()):
        self.ready, self.started, self.cancelled = set(ready), [], []
        self.root = "/datos/models/whisper"

    def path(self, name):
        return f"{self.root}/{name}" if name in self.ready else None

    def start(self, name):
        self.started.append(name)
        return {}

    def cancel(self, name):
        self.cancelled.append(name)
        return True

    def cancelled_by_user(self, name):
        return name in self.cancelled

    def state(self, name):
        return {"model": name, "ready": name in self.ready, "size_mb": 1531, "downloading": name in self.started,
                "done_mb": 0, "total_mb": 1531, "frac": 0.0, "speed_mbs": None, "eta_s": None, "error": None}


class ServidorModelosTests(TempTree):
    def app(self, **st):
        a = server.App.__new__(server.App)
        st.setdefault("cloud", dict(server.DEFAULT_SETTINGS["cloud"]))
        a.settings = settings_store(tempfile.mkdtemp(dir=self.tmp), **st)
        a.jobs = bare_jobs([])
        a.jobs.settings = a.settings
        a.jobs.cloud = None
        a.jobs.nube_apagada = lambda: None
        a.hotkey = types.SimpleNamespace(ok=True, error=None)
        a.rec = a.monitor = None
        a.rec_lock = threading.Lock()
        return a

    def test_estado_de_los_modelos(self):
        fake = FakeModels(ready={"small"})
        with mock.patch.object(engines, "MODELS", fake):
            r = self.app(procesado="agente", preset="rapido", live_preset="medium").modelos_state()
        por = {m["model"]: m for m in r["modelos"]}
        self.assertEqual(set(por), {"small", "large-v3-turbo", "medium", "large-v3"})
        self.assertEqual((por["medium"]["uso"], por["small"]["uso"]), (["directo"], ["cola"]))
        self.assertEqual((por["small"]["ready"], por["medium"]["ready"]), (True, False))
        self.assertEqual((r["procesado"], r["carpeta"]), ("agente", "/datos/models/whisper"))

    def test_descargar_y_cancelar(self):
        fake = FakeModels()
        a = self.app(procesado="local")
        with mock.patch.object(engines, "MODELS", fake), mock.patch.object(server, "log"):
            a.modelos_descargar({"model": "medium"})
            a.modelos_cancelar({"model": "medium"})
            with self.assertRaises(server.ApiError):
                a.modelos_descargar({"model": "../../etc"})
        self.assertEqual((fake.started, fake.cancelled), (["medium"], ["medium"]))

    def test_elegir_un_modo_local_empieza_a_bajar_el_modelo_del_directo(self):
        fake = FakeModels()
        a = self.app(procesado="api", preset="nube", live_preset="nube")
        with mock.patch.object(engines, "MODELS", fake), mock.patch.object(server, "log"):
            c = a.save_settings({"procesado": "agente"})
        self.assertEqual(fake.started, ["medium"])  # el directo pasa a medium y su modelo empieza a bajar
        self.assertEqual(c["descarga"], "medium")
        self.assertEqual(c["procesado"], "agente")
        # «Guardar» con el mismo modo no la vuelve a empezar (revisión del 07/11: relanzaba una descarga cancelada)
        with mock.patch.object(engines, "MODELS", fake), mock.patch.object(server, "log"):
            c = a.save_settings({"procesado": "agente"})
        self.assertEqual((fake.started, c["descarga"]), (["medium"], None))
        # cancelada en Ajustes: cambiar de modo tampoco la empieza sola
        fake3 = FakeModels()
        fake3.cancelled.append("medium")
        b = self.app(procesado="api", preset="nube", live_preset="nube")
        with mock.patch.object(engines, "MODELS", fake3), mock.patch.object(server, "log"):
            c = b.save_settings({"procesado": "local"})
        self.assertEqual((fake3.started, c["descarga"]), ([], None))
        # ya descargado, o con API externa: nada
        fake2 = FakeModels(ready={"medium"})
        with mock.patch.object(engines, "MODELS", fake2), mock.patch.object(server, "log"):
            c = a.save_settings({"procesado": "local"})
            self.assertIsNone(c["descarga"])
            c = a.save_settings({"procesado": "api"})
        self.assertEqual((fake2.started, c["descarga"]), ([], None))

    def test_a_api_con_clave_los_motores_pasan_a_la_nube(self):
        """La E2E lo cazó: con la clave ya puesta, elegir «API externa» dejaba el directo y la cola en Whisper local."""
        a = self.app(procesado="agente", preset="rapido", live_preset="medium",
                     cloud=dict(server.DEFAULT_SETTINGS["cloud"], api_key="gsk_" + "k" * 30))
        with mock.patch.object(engines, "MODELS", FakeModels()), mock.patch.object(server, "log"):
            a.save_settings({"procesado": "api"})
            st = a.settings.get()
            self.assertEqual((st["procesado"], st["preset"], st["live_preset"]), ("api", "nube", "nube"))
            a.save_settings({"procesado": "local"})  # y de vuelta, a local, como siempre
            st = a.settings.get()
            self.assertEqual((st["preset"], st["live_preset"]), ("rapido", "medium"))
            # lo que se elija en la misma petición manda
            a.save_settings({"procesado": "api", "live_preset": "medium"})
            st = a.settings.get()
            self.assertEqual((st["preset"], st["live_preset"]), ("nube", "medium"))
        # ida y vuelta (revisión del 07/11): con la API, la cola en «turbo» (local, a propósito) y el directo en la
        # nube; a Claude y de vuelta a la API: vuelven los de antes, no la nube en todo
        t = self.app(procesado="api", preset="turbo", live_preset="nube",
                     cloud=dict(server.DEFAULT_SETTINGS["cloud"], api_key="gsk_" + "k" * 30))
        with mock.patch.object(engines, "MODELS", FakeModels()), mock.patch.object(server, "log"):
            t.save_settings({"procesado": "agente"})
            self.assertEqual((t.settings.get()["preset"], t.settings.get()["live_preset"]), ("turbo", "medium"))
            t.save_settings({"procesado": "api"})
        self.assertEqual((t.settings.get()["preset"], t.settings.get()["live_preset"]), ("turbo", "nube"))
        # sin clave, a API externa: los motores siguen donde estaban (sin clave no hay nube que usar)
        b = self.app(procesado="local", preset="rapido", live_preset="medium")
        with mock.patch.object(engines, "MODELS", FakeModels()), mock.patch.object(server, "log"):
            b.save_settings({"procesado": "api"})
        self.assertEqual((b.settings.get()["preset"], b.settings.get()["live_preset"]), ("rapido", "medium"))

    def test_config_dice_cuanto_pesa_cada_modelo(self):
        with mock.patch.object(engines, "MODELS", FakeModels()):
            c = self.app(procesado="local").config({})
        por = {p["id"]: p for p in c["presets"]}
        self.assertEqual(por["medium"]["size_mb"], 1531)
        self.assertEqual(por["rapido"]["size_mb"], 486)
        self.assertFalse(por["medium"]["cached"])

    def test_rutas_registradas(self):
        src = read_app("server.py")
        for r in ('"/api/modelos": a.modelos_state', '"/api/modelos/descargar": a.modelos_descargar',
                  '"/api/modelos/cancelar": a.modelos_cancelar'):
            self.assertIn(r, src)
        # el gestor se crea en main(), antes de App() (la precarga y /api/config ya lo usan)
        i = src.index("def main():")
        self.assertLess(src.index("engines.MODELS = engines.WhisperModels(", i), src.index("Handler.app = App()", i))


# ----------------------------------------------------------------------------- nube: errores en una frase

class FakeResp:
    def __init__(self, status, body=None, text=None, headers=None):
        self.status_code, self.headers = status, headers or {}
        self._body = body
        self.text = text if text is not None else json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("sin json")
        return self._body


class ErroresNubeTests(unittest.TestCase):
    CFG = {"provider": "groq", "api_key": "gsk_" + "z" * 20, "model": "whisper-large-v3"}

    def test_errores_http(self):
        e = engines.cloud_http_error(FakeResp(401, {"error": {"message": "Invalid API Key"}}), self.CFG)
        self.assertEqual(e, "Groq no acepta la API key (HTTP 401: Invalid API Key). Cópiala de nuevo desde "
                            "console.groq.com/keys y pulsa «Probar conexión».")
        e = engines.cloud_http_error(FakeResp(403, text="<!DOCTYPE html><html>Blocked by policy</html>"), self.CFG)
        self.assertIn("HTTP 403: la red ha devuelto una página web", e)
        self.assertIn("proxy", e)
        self.assertNotIn("<html", e)
        e = engines.cloud_http_error(FakeResp(404, {"error": {"message": "model not found"}}), self.CFG,
                                     "whisper-large-v9")
        self.assertIn("«whisper-large-v9»", e)
        self.assertIn("Minutos por trozo", engines.cloud_http_error(FakeResp(413, {"error": "too large"}), self.CFG))
        self.assertIn("vuelve a probar en unos minutos", engines.cloud_http_error(FakeResp(503, {}), self.CFG))

    def test_errores_de_conexion(self):
        class RequestException(Exception):
            pass

        class ConnectionError(RequestException):
            pass

        class SSLError(ConnectionError):
            pass

        class ProxyError(ConnectionError):
            pass

        class ConnectTimeout(ConnectionError):
            pass
        h = "api.groq.com"
        self.assertIn("certificado", engines.connection_error(SSLError("CERTIFICATE_VERIFY_FAILED"), h))
        self.assertIn("proxy", engines.connection_error(ProxyError("407"), h))
        self.assertIn("no contesta", engines.connection_error(ConnectTimeout("x"), h))
        self.assertIn("No se encuentra api.groq.com",
                      engines.connection_error(ConnectionError("getaddrinfo failed"), h))
        self.assertIn("Se ha cortado", engines.connection_error(ConnectionError("reset by peer"), h))
        for e in (SSLError("x"), ProxyError("x"), ConnectionError("x")):
            self.assertTrue(engines.connection_error(e, h).endswith("."), e)

    def test_probar_conexion_con_ssl_roto_dice_que_pasa(self):
        """Lo que ve Bernardo al pulsar «Probar conexión» si la red de la empresa rompe el TLS (antes: «sin conexión
        con el servicio (SSLError)»)."""
        class FakeRequests(types.ModuleType):
            class RequestException(Exception):
                pass

            def __init__(self):
                super().__init__("requests")
                self.calls = 0

            def post(self, url, **kw):
                self.calls += 1

                class SSLError(self.RequestException):
                    pass
                raise SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        chunk = os.path.join(tmp, "silencio.wav")
        with open(chunk, "wb") as f:
            f.write(wav_bytes(1.0, rate=16000, channels=1))
        eng = engines.CloudEngine(tmp)
        fake = FakeRequests()
        with mock.patch.dict(sys.modules, {"requests": fake}), mock.patch.object(engines.time, "sleep", lambda s: 0):
            with self.assertRaises(engines.EngineError) as c:  # lo mismo que hace «Probar conexión» (CloudEngine.test)
                eng._post(self.CFG, chunk, None, None, lambda **k: None, lambda: False, attempts=2, max_wait=10.0)
        msg = str(c.exception)
        self.assertTrue(msg.startswith("La conexión segura con api.groq.com ha fallado"), msg)
        self.assertEqual(fake.calls, 2)


# ----------------------------------------------------------------------------- interfaz

class InterfazModelosTests(unittest.TestCase):
    def test_modelos_js_cargado_despues_de_app_js(self):
        html = static("index.html")
        self.assertIn('<script src="/modelos.js"></script>', html)
        self.assertLess(html.index('<script src="/app.js">'), html.index('<script src="/modelos.js">'))

    def test_ganchos_protegidos(self):
        app = static("app.js")
        # (motorNotaHtml era la reserva del diálogo Grabar de antes; la tarjeta de grabar.js no la usa: 07/11/2026)
        for fn in ("modelosHtml", "bindModelos", "motorHtml"):
            self.assertRegex(app, rf"typeof {fn} === 'function'", fn)
        # «Dónde se procesan»: elegir no aplica (un clic de prueba no pasa un directo de la nube al PC); el cambio
        # pendiente se dice, con «Aplicar ahora», y al cerrar Ajustes sin aplicarlo se avisa
        self.assertIn("api('/api/settings', { procesado: v })", app)
        self.assertIn('id="procGuardado"', app)
        self.assertIn('id="procAplicar"', app)
        self.assertIn("pero aún no está aplicado", app)
        self.assertIn("No se ha aplicado ${procNombre[v]}", app)
        self.assertIn("if (c.descarga) toast(", app)
        # el directo dice dónde transcribe y, si falló, por qué
        self.assertIn("motorHtml(sel)", app)
        self.assertIn("No se ha podido transcribir en directo.", app)

    def test_textos_de_la_verdad(self):
        js = static("modelos.js")
        self.assertIn("Claude no transcribe, solo resume", js)
        self.assertIn("solo hace el resumen al terminar", js)
        self.assertNotIn("innerHTML = r", js.replace("innerHTML = ms", ""))  # nada del servidor sin esc()


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class ModelosJsTests(unittest.TestCase):
    def run_js(self, code):
        src = static("modelos.js")
        i = src.index("/* ---------------------------------------------------------------- helpers puros")
        j = src.index("/* ---------------------------------------------------------------- motor visible")
        r = subprocess.run(["node", "-e", src[i:j] + "\n" + code], capture_output=True, text=True, encoding="utf-8",
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_mb_y_tiempo(self):
        self.assertEqual(self.run_js("console.log([modelosFmtMb(1531), modelosFmtMb(78), modelosFmtMb(3091.4), "
                                     "modelosFmtLeft(250), modelosFmtLeft(42), modelosFmtLeft(3725)].join('|'))"),
                         "1.531|78|3.091|4 min|40 s|1 h 02 min")

    def test_estado_y_motor(self):
        out = self.run_js("""
          console.log(modeloEstadoTexto({downloading: true, done_mb: 312, total_mb: 1531, frac: 0.2038, eta_s: 250}));
          console.log(modeloEstadoTexto({ready: true}));
          console.log(modeloEstadoTexto({error: 'huggingface.co ha rechazado la descarga (HTTP 403).'}));
          console.log(modeloEstadoTexto({size_mb: 1531}));
          const cfg = {providers: {groq: {label: 'Groq'}}, cloud: {provider: 'groq'}};
          console.log(motorDe({engine: 'cloud'}, cfg).texto);
          console.log(motorDe({engine: 'local', model: 'medium'}, cfg).texto);
        """).splitlines()
        self.assertEqual(out, ["descargando: 312 de 1.531 MB (20 %) · quedan ~4 min", "✓ descargado",
                               "✖ huggingface.co ha rechazado la descarga (HTTP 403).",
                               "sin descargar · 1.531 MB (se descarga una sola vez)",
                               "en la nube · Groq", "en este PC · Whisper medium"])


if __name__ == "__main__":
    unittest.main()
