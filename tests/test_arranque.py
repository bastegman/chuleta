# -*- coding: utf-8 -*-
"""Lo que falló el 02/11/2026 en el PC de Bernardo al probar la Entrega 2 (server.log de las 10:22-10:36):

- la interfaz 8 minutos con los botones muertos: /api/config importaba faster_whisper (model_cached) a la vez que el
  hilo del directo, y la importación tardó eso en su PC tras la actualización (cerrojo de importación compartido);
- ese directo lo arrancó un WAV del 23/10 que quedó con la cabecera abierta: al arrancar se tomaba por grabación en
  curso;
- el aviso flotante «Grabar» y el chip «en llamada» saltaron sin reunión por una entrada del registro de privacidad;
- y, de paso, el toast decía «1 min» para 41 s, y una versión anterior aún abierta se quedaba con el puerto.
"""
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

import numpy as np

from support import REPO, TempTree, server, engines, live
import recording  # noqa: E402

STATIC = os.path.join(REPO, "_app", "static")


def read_static(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def read_app(name):
    with open(os.path.join(REPO, "_app", name), encoding="utf-8") as f:
        return f.read()


def filetime(secs):
    """FILETIME (centenas de ns desde 1601) de un instante en segundos de epoch."""
    return int((secs + 11644473600) * 10_000_000)


# ------------------------------------------------------------------------ micro en uso: entradas rancias y procesos

class MicroEnUsoTests(unittest.TestCase):
    def setUp(self):
        recording._mic_logged.clear()
        self.now = 1_800_000_000.0
        quiet = mock.patch.object(recording, "log", lambda *a: None)
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_en_uso_solo_si_empezo_hace_poco_y_no_termino(self):
        self.assertTrue(recording.mic_in_use(filetime(self.now - 60), 0, self.now))
        self.assertFalse(recording.mic_in_use(filetime(self.now - 60), filetime(self.now - 10), self.now))  # termino
        self.assertFalse(recording.mic_in_use(0, 0, self.now))
        self.assertFalse(recording.mic_in_use(filetime(self.now - 13 * 3600), 0, self.now))  # rancia: del viernes
        self.assertFalse(recording.mic_in_use(filetime(self.now + 3600), 0, self.now))  # reloj raro: en el futuro
        self.assertFalse(recording.mic_in_use("x", 0, self.now))

    def test_exes_de_cada_entrada(self):
        self.assertEqual(recording.entry_exes("C:#Users#yo#AppData#Local#Microsoft#Teams#current#Teams.exe"),
                         ("teams.exe",))
        self.assertEqual(recording.entry_exes("MSTeams_8wekyb3d8bbwe"), recording.PACKAGED_EXES["msteams"])
        self.assertEqual(recording.entry_exes("Microsoft.SkypeApp_kzf8qxf38zg5c"), recording.PACKAGED_EXES["skype"])
        self.assertEqual(recording.entry_exes("Desconocida_123"), ())

    def test_scan_cuenta_la_llamada_real(self):
        entradas = [("Microsoft.WindowsCamera_8wekyb3d8bbwe", filetime(self.now - 5), 0),  # no es de reuniones
                    ("MSTeams_8wekyb3d8bbwe", filetime(self.now - 120), 0)]
        self.assertEqual(recording.mic_scan(entradas, self.now, images={"ms-teams.exe", "explorer.exe"}), "Teams")

    def test_scan_descarta_rancias_y_procesos_cerrados(self):
        logs = []
        with mock.patch.object(recording, "log", logs.append):  # por encima del silencio de setUp
            rancia = [("MSTeams_8wekyb3d8bbwe", filetime(self.now - 3 * 86400), 0)]
            self.assertIsNone(recording.mic_scan(rancia, self.now, images={"ms-teams.exe"}))
            cerrada = [("MSTeams_8wekyb3d8bbwe", filetime(self.now - 120), 0)]
            self.assertIsNone(recording.mic_scan(cerrada, self.now, images={"explorer.exe"}))
            self.assertIsNone(recording.mic_scan(cerrada, self.now, images={"explorer.exe"}))  # repetida: un log
        self.assertEqual(len(logs), 2)
        self.assertIn("rancia", logs[0])
        self.assertIn("no está en marcha", logs[1])

    def test_scan_sin_saber_los_procesos_se_fia_de_la_fecha(self):
        """running_images() devuelve None cuando no se puede saber (fuera de Windows, o tasklist falla): entonces
        basta la fecha. En Windows la CI tiene tasklist de verdad, asi que se sustituye."""
        reciente = [("C:#Program Files#Zoom#bin#Zoom.exe", filetime(self.now - 30), 0)]
        with mock.patch.object(recording, "running_images", lambda: None):
            self.assertEqual(recording.mic_scan(reciente, self.now), "Zoom")

    def test_navegador_cuenta_si_esta_abierto(self):
        e = [("C:#Program Files (x86)#Microsoft#Edge#Application#msedge.exe", filetime(self.now - 30), 0)]
        self.assertEqual(recording.mic_scan(e, self.now, images={"msedge.exe"}), "el navegador")
        recording._mic_logged.clear()
        self.assertIsNone(recording.mic_scan(e, self.now, images={"chrome.exe"}))

    def test_fuera_de_windows_no_hay_nada(self):
        if not recording.WINDOWS:
            self.assertIsNone(recording.mic_meeting_app())
            self.assertIsNone(recording.running_images())


# ------------------------------------------------------------------------ /api/config sin importar faster_whisper

class ModeloEnCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-hf-")
        engines._MODEL_CACHED.clear()
        self.addCleanup(engines._MODEL_CACHED.clear)
        import shutil
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def modelo(self, repo):
        d = os.path.join(self.tmp, "models--" + repo.replace("/", "--"), "snapshots", "abc123")
        os.makedirs(d)
        with open(os.path.join(d, "model.bin"), "wb") as f:
            f.write(b"x")

    def test_sin_faster_whisper_importable(self):
        """Antes importaba faster_whisper.utils (y huggingface_hub): con la importacion lenta o rota, /api/config no
        contestaba. Ahora ni lo intenta: sys.modules envenenado lo demostraria con un ImportError."""
        self.modelo("Systran/faster-whisper-small")
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": self.tmp}), \
                mock.patch.dict(sys.modules, {"faster_whisper": None, "faster_whisper.utils": None,
                                              "huggingface_hub": None, "huggingface_hub.constants": None}):
            self.assertEqual(engines.hf_hub_cache(), self.tmp)
            self.assertTrue(engines.model_cached("small"))
            self.assertFalse(engines.model_cached("medium"))
            self.assertFalse(engines.model_cached("large-v3-turbo"))

    def test_otro_dueno_del_repositorio(self):
        self.modelo("alguien/faster-whisper-medium")
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": self.tmp}):
            self.assertTrue(engines.model_cached("medium"))

    def test_resultado_guardado_un_rato(self):
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": self.tmp}):
            self.assertFalse(engines.model_cached("small"))
            self.modelo("Systran/faster-whisper-small")
            self.assertFalse(engines.model_cached("small"))  # dentro del TTL: el mismo
            engines._MODEL_CACHED.clear()
            self.assertTrue(engines.model_cached("small"))

    def test_hf_home(self):
        with mock.patch.dict(os.environ, {"HF_HOME": self.tmp}, clear=False):
            os.environ.pop("HF_HUB_CACHE", None)
            os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
            self.assertEqual(engines.hf_hub_cache(), os.path.join(self.tmp, "hub"))

    def test_config_no_toca_faster_whisper(self):
        src = read_app("engines.py")
        i = src.index("def model_cached(")
        cuerpo = src[i:src.index("\nclass WorkerTimeout", i)]
        self.assertNotIn("faster_whisper", cuerpo.replace("faster-whisper", ""))
        self.assertNotIn("huggingface_hub", cuerpo)

    def test_contar_tokens_no_espera_a_la_importacion(self):
        """count_tokens (/api/vocab/check) estima mientras el tokenizador se carga en otro hilo."""
        with mock.patch.dict(engines._TOKENIZER, {}, clear=True), \
                mock.patch.object(engines.threading, "Thread") as T:
            self.assertGreater(engines.count_tokens("hola mundo"), 0)
            T.assert_called_once()
            self.assertIsNone(engines._TOKENIZER["t"])


# ------------------------------------------------------------------------ el directo sin la VAD de Whisper

class VadDelDirectoTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(live._VAD)
        live._VAD.update(fn=None, retry_at=0.0, warned=False)
        self.addCleanup(live._VAD.update, self.saved)

    def tono(self):
        b = np.zeros(live.SR * 3, np.float32)
        b[live.SR:2 * live.SR] = np.sin(np.arange(live.SR) * 0.1) * 0.3
        return b

    def test_detector_sencillo(self):
        ts = live.simple_speech(self.tono())
        self.assertEqual(len(ts), 1)
        self.assertAlmostEqual(ts[0]["start"] / live.SR, 1.0, delta=0.05)
        self.assertAlmostEqual(ts[0]["end"] / live.SR, 2.0, delta=0.05)
        self.assertEqual(live.simple_speech(np.zeros(0, np.float32)), [])
        self.assertEqual(live.simple_speech(np.zeros(10, np.float32)), [{"start": 0, "end": 10}])

    def test_sin_faster_whisper_se_usa_el_sencillo_y_se_avisa_una_vez(self):
        logs = []
        with mock.patch.dict(sys.modules, {"faster_whisper": None, "faster_whisper.vad": None}), \
                mock.patch.object(live, "log", logs.append):
            ts = live.speech(self.tono())
            live.speech(self.tono())
        self.assertEqual(len(ts), 1)
        self.assertIs(live._VAD["fn"], live.simple_speech)
        self.assertEqual(len(logs), 1)
        self.assertIn("detector sencillo", logs[0])

    def test_una_importacion_pisada_no_tumba_el_directo(self):
        """02/11/2026: KeyError 'faster_whisper' desde importlib (otro hilo la quito de sys.modules): no es un
        ImportError y antes subia hasta _run_live."""
        import builtins
        real = builtins.__import__

        def roto(name, *a, **k):
            if name.startswith("faster_whisper"):
                raise KeyError("faster_whisper")
            return real(name, *a, **k)
        with mock.patch.object(builtins, "__import__", roto), mock.patch.object(live, "log", lambda *a: None):
            self.assertEqual(len(live.speech(self.tono())), 1)


# ------------------------------------------------------------------------ un WAV viejo a medio cerrar no es un directo

class GrabacionAbiertaViejaTests(TempTree):
    def test_la_fecha_manda_la_primera_vez(self):
        viejo = self.wav("Reuniones/2026-10-23 19-06-05.wav", seconds=5, open_header=True)
        hace = time.time() - 10 * 86400
        os.utime(viejo, (hace, hace))
        self.wav("Reuniones/2026-11-02 10-35-18.wav", seconds=5, open_header=True)  # recien escrito
        lib = server.Library(server.Durations(os.path.join(self.data_dir, "d.json")))
        lib.refresh()
        por = {a["name"]: a for a in lib.flat}
        self.assertFalse(por["2026-10-23 19-06-05.wav"]["recording"])
        self.assertTrue(por["2026-11-02 10-35-18.wav"]["recording"])
        # y si el viejo crece, vuelve a ser una grabacion
        with open(viejo, "ab") as f:
            f.write(b"\0" * 4800)
        lib.refresh()
        self.assertTrue({a["name"]: a for a in lib.flat}["2026-10-23 19-06-05.wav"]["recording"])


# ------------------------------------------------------------------------ peticiones lentas en el log

class PeticionLentaTests(unittest.TestCase):
    def test_solo_las_que_pasan_del_tope(self):
        logs = []
        with mock.patch.object(server, "log", logs.append):
            server.slow_request("GET", "/api/config", 0.4)
            server.slow_request("GET", "/api/config", 480.2)
            server.slow_request("POST", "/api/rec/start", 2.0)
        self.assertEqual(logs, ["   petición lenta: GET /api/config 480.2 s",
                                "   petición lenta: POST /api/rec/start 2.0 s"])

    def test_el_despacho_la_mide(self):
        src = read_app("server.py")
        self.assertIn('slow_request("GET", u.path, time.time() - t0)', src)
        self.assertIn("slow_request('POST', u.path, time.time() - t0)", src)


# ------------------------------------------------------------------------ otra version de la app con el puerto

class OtraVersionTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="chuleta-datos-")
        server.write_token_file(self.data, "tok") if hasattr(server, "write_token_file") else \
            server.write_token(self.data, "tok")
        self.paths = mock.patch.object(server, "DATA_DIR", self.data)
        self.paths.start()
        self.addCleanup(self.paths.stop)
        import shutil
        self.addCleanup(shutil.rmtree, self.data, True)

    def test_probe_distingue_la_version(self):
        with mock.patch.object(server, "_local_request", return_value={"app": "audios", "version": server.VERSION}):
            self.assertEqual(server.probe_running(), "app")
            self.assertTrue(server.already_running())
        with mock.patch.object(server, "_local_request", return_value={"app": "audios"}):  # anterior al 02/11/2026
            self.assertEqual(server.probe_running(), "otra_version")
            self.assertFalse(server.already_running())
        with mock.patch.object(server, "_local_request", return_value={"app": "audios", "version": "otra"}):
            self.assertEqual(server.probe_running(), "otra_version")
        with mock.patch.object(server, "_local_request", return_value={"hello": 1}):
            self.assertEqual(server.probe_running(), "otro")
        err = urllib.error.HTTPError("u", 403, "forbidden", {}, io.BytesIO(b""))
        with mock.patch.object(server, "_local_request", side_effect=err):
            self.assertEqual(server.probe_running(), "otro")

    def test_el_ping_lleva_la_version(self):
        src = read_app("server.py")
        self.assertIn('"/api/ping": lambda q: {"app": "audios", "version": VERSION, "pid": os.getpid()}', src)

    def retirar(self, respuestas, puerto_libre, windows=True, pid=4242, python=True):
        llamadas, corridos = [], []

        def local(path, token, data=None, timeout=1.5):
            llamadas.append((path, data))
            r = respuestas.get(path)
            if isinstance(r, Exception):
                raise r
            return r or {}
        libre = iter(puerto_libre)
        with mock.patch.object(server, "_local_request", local), \
                mock.patch.object(server, "_wait_port_free", lambda *a, **k: next(libre)), \
                mock.patch.object(server, "WINDOWS", windows), \
                mock.patch.object(server, "_port_owner_pid", lambda port: pid), \
                mock.patch.object(server, "_is_python_process", lambda p: python), \
                mock.patch.object(server, "probe_running", lambda *a, **k: "otra_version"), \
                mock.patch.object(server.subprocess, "run", lambda cmd, **k: corridos.append(cmd)), \
                mock.patch.object(server, "log", lambda *a: None):
            return server.retire_other_version(wait=0.2), llamadas, corridos

    def test_se_le_pide_que_se_cierre(self):
        estado, llamadas, corridos = self.retirar({"api/ping": {"app": "audios", "version": "vieja"},
                                                   "api/rec/status": {"recording": False}, "api/apagar": {"ok": True}},
                                                  [True])
        self.assertEqual(estado, "libre")
        self.assertEqual([p for p, _ in llamadas], ["api/ping", "api/rec/status", "api/apagar"])
        self.assertEqual(corridos, [])

    def test_sin_api_apagar_se_termina_el_proceso_en_windows(self):
        """Entrega 1 (30/10): sin /api/apagar (404). Se termina su pythonw.exe, nunca si graba."""
        err = urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b""))
        estado, llamadas, corridos = self.retirar({"api/ping": {"app": "audios"},
                                                   "api/rec/status": {"recording": False}, "api/apagar": err},
                                                  [False, True])
        self.assertEqual(estado, "libre")
        self.assertEqual(len(corridos), 1)
        self.assertEqual(corridos[0][:3], ["taskkill", "/PID", "4242"])

    def test_grabando_no_se_toca(self):
        estado, llamadas, corridos = self.retirar({"api/ping": {"app": "audios"},
                                                   "api/rec/status": {"recording": True}}, [False, False])
        self.assertEqual(estado, "grabando")
        self.assertNotIn("api/apagar", [p for p, _ in llamadas])
        self.assertEqual(corridos, [])

    def test_fuera_de_windows_no_se_mata_nada(self):
        err = urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b""))
        estado, _, corridos = self.retirar({"api/ping": {"app": "audios"}, "api/rec/status": {"recording": False},
                                            "api/apagar": err}, [False, False], windows=False)
        self.assertEqual(estado, "otra_version")
        self.assertEqual(corridos, [])

    def test_otro_proceso_en_el_puerto_no_se_mata(self):
        err = urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b""))
        estado, _, corridos = self.retirar({"api/ping": {"app": "audios"}, "api/rec/status": {"recording": False},
                                            "api/apagar": err}, [False, False], python=False)
        self.assertEqual(corridos, [])

    def test_netstat_y_tasklist(self):
        salida = ("  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       1234\n"
                  "  TCP    127.0.0.1:8766         0.0.0.0:0              LISTENING       99\n")
        with mock.patch.object(server.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, salida, "")):
            self.assertEqual(server._port_owner_pid(8765), 1234)
            self.assertEqual(server._port_owner_pid(8767), None)
        tl = '"pythonw.exe","1234","Console","1","80.000 KB"\n'
        with mock.patch.object(server.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, tl, "")):
            self.assertTrue(server._is_python_process(1234))
        tl = '"msedge.exe","1234","Console","1","80.000 KB"\n'
        with mock.patch.object(server.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, tl, "")):
            self.assertFalse(server._is_python_process(1234))

    def run_main(self, estados, retirar):
        argv = ["server.py", "--port", "8799", "--data", self.data]
        srv = mock.MagicMock()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(server, "App") as App, \
                mock.patch.object(server, "Window"), mock.patch.object(server, "setup_logging"), \
                mock.patch.object(server, "fatal", side_effect=SystemExit(1)) as fatal, \
                mock.patch.object(server, "set_app_id", return_value=False), \
                mock.patch.object(server, "probe_running", side_effect=estados), \
                mock.patch.object(server, "retire_other_version", return_value=retirar) as retire, \
                mock.patch.object(server, "reserve_port", return_value=srv) as reserve, \
                mock.patch.object(server, "start_session"), \
                mock.patch.object(server, "PORT", server.PORT), mock.patch.object(server, "URL", server.URL), \
                mock.patch.object(server, "SERVER", None), mock.patch.object(server, "WINDOW", None), \
                mock.patch.object(server.Handler, "app", None), \
                mock.patch.object(server, "open_ui"), mock.patch.dict(os.environ), \
                mock.patch.object(server, "log", lambda *a: None):
            try:
                server.main()
                exited = False
            except SystemExit:
                exited = True
        return App, fatal, retire, reserve, exited

    def test_main_retira_la_otra_version_y_arranca(self):
        App, fatal, retire, reserve, exited = self.run_main(["otra_version"], "libre")
        retire.assert_called_once()
        reserve.assert_called_once()
        App.assert_called_once()
        fatal.assert_not_called()

    def test_main_si_la_otra_graba_lo_dice_y_no_arranca(self):
        App, fatal, retire, reserve, exited = self.run_main(["otra_version"], "grabando")
        self.assertTrue(exited)
        self.assertIn("está grabando", fatal.call_args[0][0])
        App.assert_not_called()
        reserve.assert_not_called()

    def test_reserve_port_no_espera_a_otra_version(self):
        with mock.patch.object(server, "Server", side_effect=OSError(48, "en uso")), \
                mock.patch.object(server, "probe_running", return_value="otra_version"), \
                mock.patch.object(server, "fatal", side_effect=SystemExit(1)) as fatal, \
                mock.patch.object(server, "log", lambda *a: None):
            t = time.time()
            with self.assertRaises(SystemExit):
                server.reserve_port(wait=30)
        self.assertLess(time.time() - t, 5)
        self.assertIn("otra versión", fatal.call_args[0][0])


# ------------------------------------------------------------------------ la interfaz no se queda muda

class InterfazNoMudaTests(unittest.TestCase):
    def test_version_anterior_y_servidor_lento_se_dicen(self):
        js = read_static("app.js")
        self.assertIn("function otraVersion(c)", js)
        self.assertIn("if (otraVersion(S.config)) { main.innerHTML = otraVersionHtml(S.config); return; }", js)
        self.assertIn("function esperandoHtml(seg)", js)
        self.assertIn("tarda en contestar", js)
        self.assertIn("window.addEventListener('error'", js)
        self.assertIn("window.addEventListener('unhandledrejection'", js)

    def test_los_botones_esperan_a_la_configuracion(self):
        js = read_static("app.js")
        # ⚙, ● Grabar y «Subir audio» (09/11/2026)
        self.assertEqual(js.count("if (!S.config) return toast('La app aún está arrancando"), 3)
        i = js.index("function route() {")
        self.assertIn("if (!S.config) return;", js[i:i + 200])

    def test_el_toast_de_la_grabacion_dice_segundos(self):
        src = read_app("server.py")
        self.assertIn("f'{int(seg)} s' if seg < 60 else f'{round(seg / 60)} min'", src)


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------------ una grabacion arrancada no se deja a medias

class FakeRec:
    """RecorderProcess de mentira: crea el WAV con su cabecera y «sigue vivo»."""

    def __init__(self, path, mic, sys_dev, app_dir, data_dir):
        self.path, self.status = path, {}
        with open(path, "wb") as f:
            f.write(b"\0" * 44)

    def alive(self):
        return True


class GrabacionArrancadaTests(TempTree):
    """02/11/2026, 10:35: la primera pulsacion de Ctrl+Alt+R arranco la grabadora, algo fallo justo despues y el aviso
    fue «No se pudo grabar», con la grabacion en marcha sin decirlo (el vigilante arranco el directo «automatico» 7 s
    despues y la segunda pulsacion la paro). Lo que falle tras arrancar la grabadora se anota y se avisa, pero la
    grabacion sigue y se registra «Grabando»."""

    def app(self):
        from test_perfil import settings_store
        import types
        import threading
        from support import bare_jobs
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp)
        a.rec_lock, a.notes_lock, a.rec, a.monitor = threading.Lock(), threading.Lock(), None, None
        a.library = types.SimpleNamespace(at=1)
        a.jobs = bare_jobs([])
        a.jobs.settings, a.jobs.started = a.settings, []
        a.jobs.start_live = lambda rel, preset: a.jobs.started.append((rel, preset))
        return a

    def start(self, a, logs, **b):
        os.makedirs(os.path.join(self.root, "Alfa"), exist_ok=True)
        body = dict({"mic": "default", "sys": "all", "folder": "Alfa", "live": True, "preset": "medium", "name": "x",
                     "vocab": ""}, **b)
        with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log", logs.append), \
                mock.patch.object(server, "log_exc", lambda: logs.append("traceback")):
            return a.rec_start(body)

    def test_fallo_al_escribir_la_cabecera_no_para_la_grabacion(self):
        a, logs = self.app(), []
        with mock.patch.object(server.App, "notes_save", lambda self, b: (_ for _ in ()).throw(
                server.ApiError("No existe el archivo: x", 404))):
            r = self.start(a, logs, contexto="Comité")  # con contexto hay cabecera que escribir
        self.assertTrue(a.rec.alive())
        self.assertIn("sin cabecera de notas (No existe el archivo: x)", r["aviso"])
        self.assertEqual(len(a.jobs.started), 1)  # el directo arranca igual
        self.assertTrue(any(l.startswith("● Grabando: ") and "aviso: sin cabecera" in l for l in logs), logs)
        self.assertIn("traceback", logs)
        self.assertIn(r["path"], a.jobs.claimed)  # sigue reclamada: el vigilante no arranca otro directo

    def test_fallo_del_directo_deja_la_grabacion_y_suelta_la_reclamacion(self):
        a, logs = self.app(), []

        def roto(rel, preset):
            raise server.ApiError("Ese audio ya se está transcribiendo.")
        a.jobs.start_live = roto
        r = self.start(a, logs)
        self.assertTrue(a.rec.alive())
        self.assertIn("el directo no ha arrancado (Ese audio ya se está transcribiendo.)", r["aviso"])
        self.assertNotIn(r["path"], a.jobs.claimed)  # que lo intente el vigilante
        self.assertTrue(any(l.startswith("● Grabando: ") for l in logs), logs)

    def test_sin_fallos_no_hay_aviso(self):
        a, logs = self.app(), []
        r = self.start(a, logs)
        self.assertIsNone(r["aviso"])
        self.assertTrue(any(l.startswith("● Grabando: ") and "aviso" not in l for l in logs), logs)

    def test_la_grabadora_que_no_arranca_sigue_siendo_error(self):
        a, logs = self.app(), []

        class Muerta(FakeRec):
            def alive(self):
                return False
        os.makedirs(os.path.join(self.root, "Alfa"), exist_ok=True)
        with mock.patch.object(server, "RecorderProcess", Muerta), mock.patch.object(server, "log", logs.append):
            with self.assertRaises(server.ApiError) as cm:
                a.rec_start({"mic": "default", "sys": "all", "folder": "Alfa", "live": True, "name": "x", "vocab": ""})
        self.assertIn("no ha podido arrancar", cm.exception.msg)
        self.assertFalse(any(l.startswith("● Grabando") for l in logs))

    def test_el_atajo_deja_el_error_en_el_log(self):
        src = read_app("server.py")
        self.assertIn('log(f"   no se pudo grabar (Ctrl+Alt+R): {e.msg}")', src)
        self.assertIn('''(f" · {r['aviso']}" if r.get("aviso") else "")''', src)
