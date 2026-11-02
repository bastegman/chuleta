# -*- coding: utf-8 -*-
"""«Dónde se procesan tus reuniones» (decisión de producto del 02/11/2026): solo en este PC, con la cuenta de Claude
del usuario a través de Claude Code (`claude -p`), o con una API externa; las dos últimas con su aviso de que el
contenido de la reunión sale del PC. Todo con dobles: sin Claude Code de verdad, sin red, sin Windows."""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from support import APP, TempTree, bare_jobs, engines, server  # noqa: F401  (support prepara sys.path)
from test_perfil import settings_store  # noqa: E402  (ayudante, no tests)

STATIC = os.path.join(APP, "static")
KEY = "gsk_" + "p9" * 12
CLOUD = dict(server.DEFAULT_SETTINGS["cloud"], api_key=KEY)
OK_JSON = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "## Resumen\nHola.",
                      "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 10}}, "usage": {"input_tokens": 10}})


def static(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def read_app(name):
    with open(os.path.join(APP, name), encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------- el ajuste y su inferencia

class ProcesadoDeTests(unittest.TestCase):
    def test_sin_decidir_api_si_hay_key_y_si_no_local(self):
        self.assertEqual(server.procesado_de({"cloud": {"api_key": KEY}}), "api")
        self.assertEqual(server.procesado_de({"cloud": {"api_key": "  "}}), "local")
        self.assertEqual(server.procesado_de({}), "local")
        self.assertEqual(server.procesado_de({"procesado": None, "cloud": CLOUD}), "api")

    def test_decidido_manda_aunque_haya_key(self):
        for p in server.PROCESADOS:
            self.assertEqual(server.procesado_de({"procesado": p, "cloud": CLOUD}), p)
        self.assertEqual(server.procesado_de({"procesado": "otro", "cloud": {}}), "local")

    def test_agente_de_vuelve_a_claude_y_al_modelo_recomendado(self):
        cls, a = server.agente_de({"agente": {"tipo": "codex", "modelo": "gpt"}})
        self.assertIs(cls, engines.ClaudeCode)
        self.assertEqual(a, {"tipo": "claude", "modelo": "sonnet"})
        cls, a = server.agente_de({"agente": {"tipo": "claude", "modelo": "opus"}})
        self.assertEqual(a["modelo"], "opus")
        self.assertEqual(server.agente_de({})[1], {"tipo": "claude", "modelo": "sonnet"})

    def test_los_ajustes_por_defecto_no_deciden(self):
        """None hasta que arranca la app: asi una instalacion anterior con API key sigue en la nube."""
        self.assertIsNone(server.DEFAULT_SETTINGS["procesado"])
        self.assertEqual(server.DEFAULT_SETTINGS["agente"], {"tipo": "claude", "modelo": "sonnet"})
        self.assertIn("if self.settings.get().get(\"procesado\") not in PROCESADOS:", read_app("server.py"))


# ----------------------------------------------------------------------------- Claude Code (engines)

class ClaudeCodeTests(unittest.TestCase):
    def test_find_en_el_path_o_en_las_carpetas_de_windows(self):
        with mock.patch.object(engines.shutil, "which", return_value="/usr/local/bin/claude"):
            self.assertEqual(engines.ClaudeCode.find(), "/usr/local/bin/claude")
        with tempfile.TemporaryDirectory() as home:
            exe = os.path.join(home, ".local", "bin", "claude.exe")
            os.makedirs(os.path.dirname(exe))
            open(exe, "w").close()
            with mock.patch.object(engines.shutil, "which", return_value=None), \
                    mock.patch.dict(os.environ, {"USERPROFILE": home, "APPDATA": os.path.join(home, "nada")}):
                self.assertEqual(engines.ClaudeCode.find(), exe)
            with mock.patch.object(engines.shutil, "which", return_value=None), \
                    mock.patch.dict(os.environ, {"USERPROFILE": os.path.join(home, "otra"), "APPDATA": home}):
                self.assertIsNone(engines.ClaudeCode.find())

    def test_parse_del_json_de_claude(self):
        ag = engines.ClaudeCode()
        self.assertEqual(ag._parse(0, OK_JSON, "", "sonnet"), ("## Resumen\nHola.", "claude-sonnet-5-5"))
        with self.assertRaises(engines.EngineError) as c:
            ag._parse(0, json.dumps({"type": "result", "is_error": True, "result": "Rate limit"}), "", "sonnet")
        self.assertIn("Rate limit", str(c.exception))
        with self.assertRaises(engines.EngineError) as c:
            ag._parse(1, "", "Not logged in. Please run /login", "sonnet")
        self.assertIn("sin sesión iniciada", str(c.exception))
        self.assertIn("escribe claude", str(c.exception))
        with self.assertRaises(engines.EngineError) as c:
            ag._parse(0, json.dumps({"type": "result", "is_error": False, "result": ""}), "", "sonnet")
        self.assertIn("no ha devuelto texto", str(c.exception))
        with self.assertRaises(engines.EngineError) as c:
            ag._parse(2, "basura", "boom", "sonnet")
        self.assertIn("código 2", str(c.exception))

    def test_chat_lanza_claude_sin_herramientas_y_con_las_instrucciones_por_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            ag = engines.ClaudeCode(settings_dir=d)
            seen = {}

            def run(cmd, stdin_text=None, timeout=0, cancelled=None):
                seen.update(cmd=cmd, stdin=stdin_text, timeout=timeout)
                return 0, OK_JSON, ""
            with mock.patch.object(engines.ClaudeCode, "run", side_effect=run), \
                    mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"):
                stages = []
                text, model = ag.chat("SISTEMA", "USUARIO largo", 2500, lambda **k: stages.append(k), lambda: False,
                                      model="haiku")
            self.assertEqual((text, model), ("## Resumen\nHola.", "claude-sonnet-5-5"))
            cmd = seen["cmd"]
            self.assertEqual(cmd[0], "/x/claude")
            self.assertEqual(cmd[1:5], ["-p", "--output-format", "json", "--tools"])
            self.assertEqual(cmd[5], "")  # sin herramientas: solo texto
            for flag in ("--no-session-persistence", "--strict-mcp-config", "--settings", "--model"):
                self.assertIn(flag, cmd)
            self.assertNotIn("--bare", cmd)  # --bare no usa la sesion iniciada del usuario
            self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")
            self.assertEqual(cmd[-1], engines.ClaudeCode.PROMPT)
            self.assertTrue(seen["stdin"].startswith("INSTRUCCIONES:\nSISTEMA\n\nUSUARIO largo"))
            self.assertEqual(seen["timeout"], engines.AGENTE_TIMEOUT_S)
            with open(cmd[cmd.index("--settings") + 1], encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"disableAllHooks": True})
            self.assertTrue(any("Claude Code (haiku)" in s.get("stage", "") for s in stages))

    def test_chat_sin_claude_instalado(self):
        with mock.patch.object(engines.ClaudeCode, "find", return_value=None):
            with self.assertRaises(engines.EngineError) as c:
                engines.ClaudeCode().chat("s", "u", 10, lambda **k: None, lambda: False)
        self.assertIn("No encuentro Claude Code", str(c.exception))

    def test_run_se_cancela_y_tiene_tope(self):
        """Un proceso de verdad que duerme: cancelar lo mata enseguida y el tope de tiempo tambien."""
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        t0 = time.time()
        with self.assertRaises(engines.Cancelled):
            engines.AgenteLocal.run(cmd, "x", timeout=30, cancelled=lambda: time.time() - t0 > 0.7)
        self.assertLess(time.time() - t0, 10)
        t0 = time.time()
        with self.assertRaises(engines.EngineError) as c:
            engines.ClaudeCode.run(cmd, "x", timeout=1.0)
        self.assertLess(time.time() - t0, 10)
        self.assertIn("no ha contestado en 1 s", str(c.exception))
        code, out, err = engines.AgenteLocal.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                                                 "hola")
        self.assertEqual((code, out.strip(), err), (0, "HOLA", ""))

    def test_check_version_y_peticion_minima(self):
        ag = engines.ClaudeCode()
        with mock.patch.object(engines.ClaudeCode, "version", return_value="2.1.314 (Claude Code)"), \
                mock.patch.object(engines.ClaudeCode, "chat", return_value=("OK", "claude-sonnet-5-5")) as chat:
            version, secs = ag.check("/x/claude")
        self.assertEqual(version, "2.1.314 (Claude Code)")
        self.assertEqual(chat.call_args.kwargs.get("path"), "/x/claude")
        self.assertEqual(chat.call_args.kwargs.get("timeout"), engines.AGENTE_CHECK_S)
        with mock.patch.object(engines.ClaudeCode, "version", return_value="2.1.314 (Claude Code)"), \
                mock.patch.object(engines.ClaudeCode, "chat", return_value=("Hola, ¿qué tal?", "m")):
            with self.assertRaises(engines.EngineError) as c:
                ag.check("/x/claude")
        self.assertIn("algo inesperado", str(c.exception))

    def test_version_falla_con_mensaje(self):
        with mock.patch.object(engines.ClaudeCode, "run", return_value=(1, "", "no such file")):
            with self.assertRaises(engines.EngineError) as c:
                engines.ClaudeCode.version("/x/claude")
        self.assertIn("--version", str(c.exception))
        with mock.patch.object(engines.ClaudeCode, "run", return_value=(0, "2.1.314 (Claude Code)\n", "")):
            self.assertEqual(engines.ClaudeCode.version("/x/claude"), "2.1.314 (Claude Code)")


class FakeAgente(engines.AgenteLocal):
    tipo, label, MODELO = "falso", "Agente falso", "m1"
    MODELOS = (("m1", "uno"),)

    def __init__(self, settings_dir=None):
        super().__init__(settings_dir)
        self.calls = []

    def chat(self, system, user, max_tokens, report, cancelled, model=None, timeout=None, path=None):
        self.calls.append((system, user, max_tokens, model, timeout))
        return f"RESUMEN {len(self.calls)}", "modelo-falso"


class SummarizerConAgenteTests(unittest.TestCase):
    SEGS = [{"start": i * 10.0, "end": i * 10.0 + 9.0, "text": f"frase número {i} de la reunión con bastante texto"}
            for i in range(500)]
    META = {"title": "Comité", "date": "2026-11-02 10:00", "duration": "60 min", "participants": "", "notes": "",
            "extra": "", "about": ""}

    def test_una_sola_llamada_aunque_la_reunion_sea_larga(self):
        """Con la API de chat esta reunion iria por partes (CHUNK_CHARS); el agente tiene contexto de sobra."""
        ag = FakeAgente()
        s = engines.Summarizer(agente=ag)
        text = "\n".join(s.lines(self.SEGS))
        self.assertGreater(len(text), engines.Summarizer.SINGLE_CHARS)
        out, model = s.summarize(self.SEGS, self.META, {"agente_modelo": "m1", "summary_lang": "es"},
                                 lambda **k: None, lambda: False)
        self.assertEqual((out, model), ("RESUMEN 1", "modelo-falso"))
        self.assertEqual(len(ag.calls), 1)
        system, user, max_tokens, model, timeout = ag.calls[0]
        self.assertIn("resume reuniones de trabajo", system)
        self.assertIn("TRANSCRIPCIÓN:", user)
        self.assertEqual((max_tokens, model, timeout), (2500, "m1", None))

    def test_sin_agente_sigue_siendo_la_api_de_chat(self):
        s = engines.Summarizer(keys=engines.KeyPool())
        self.assertIsNone(s.agente)
        self.assertEqual((s.SINGLE_CHARS, s.CHUNK_CHARS),
                         (engines.Summarizer.SINGLE_CHARS, engines.Summarizer.CHUNK_CHARS))


# ----------------------------------------------------------------------------- Jobs: que se puede y que no

class JobsProcesadoTests(TempTree):
    def jobs(self, **st):
        j = bare_jobs([])
        j.settings = settings_store(tempfile.mkdtemp(dir=self.tmp), cloud=dict(CLOUD), **st)
        j.library = types.SimpleNamespace(find=lambda rel: {"date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                            "duration": 2.0}, at=1)
        j.summarizer = "api"
        j.enviados = []
        return j

    def test_summary_missing_por_modo(self):
        self.assertIn("Dónde se procesan", self.jobs(procesado="local").summary_missing())
        with mock.patch.object(engines.ClaudeCode, "find", return_value=None):
            self.assertIn("No encuentro Claude Code", self.jobs(procesado="agente").summary_missing())
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"):
            j = self.jobs(procesado="agente")
            self.assertIsNone(j.summary_missing())
            self.assertTrue(j.summary_ready())
        self.assertIsNone(self.jobs(procesado="api").summary_missing())
        sin_key = self.jobs(procesado="api")
        sin_key.settings.update(lambda d: d["cloud"].update(api_key=""))
        self.assertIn("API key", sin_key.summary_missing())

    def test_submit_summary_dice_por_que_no(self):
        j = self.jobs(procesado="local")
        self.wav("Alfa/r.wav")
        with self.assertRaises(server.ApiError) as c:
            j.submit_summary("Alfa/r.wav")
        self.assertIn("Dónde se procesan", c.exception.msg)

    def test_la_nube_solo_con_api(self):
        self.wav("Alfa/r.wav")
        for modo in ("local", "agente"):
            j = self.jobs(procesado=modo)
            with self.assertRaises(server.ApiError) as c:
                j.submit(["Alfa/r.wav"], "nube", "")
            self.assertEqual(c.exception.msg, server.NUBE_OFF)
            with self.assertRaises(server.ApiError):
                j._check_live_preset("nube")
        j = self.jobs(procesado="api")
        j._cloud_ready = lambda: True
        j.queues = {"cloud": types.SimpleNamespace(put=lambda job: None)}
        self.assertEqual(len(j.submit(["Alfa/r.wav"], "nube", "")), 1)

    def test_el_directo_en_nube_vuelve_a_local_sin_api(self):
        self.wav("Alfa/r.wav")
        j = self.jobs(procesado="agente", live_preset="nube")
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "log"):
            j.start_live("Alfa/r.wav", "nube")
        self.assertEqual(j.list[-1].preset, "medium")

    def test_con_el_agente_se_resume_tambien_lo_transcrito_en_local(self):
        """Elegir «con tu cuenta de Claude» (con su aviso) es el consentimiento: no hace falta la casilla de la API."""
        self.wav("Alfa/r.wav")
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"):
            j = self.jobs(procesado="agente")
            j.submit_summary = lambda rel, auto=False: j.enviados.append(rel)
            j.auto_summary("Alfa/r.wav")
            self.assertEqual(j.enviados, ["Alfa/r.wav"])
            j = self.jobs(procesado="api")  # con la API, como siempre: lo local no sale sin la casilla
            j.submit_summary = lambda rel, auto=False: j.enviados.append(rel)
            with mock.patch.object(server, "log"):
                j.auto_summary("Alfa/r.wav")
            self.assertEqual(j.enviados, [])

    def test_summarizer_segun_el_modo(self):
        j = self.jobs(procesado="api")
        self.assertEqual(j._summarizer(j.settings.get()), "api")
        j = self.jobs(procesado="agente")
        s = j._summarizer(j.settings.get())
        self.assertIsInstance(s.agente, engines.ClaudeCode)
        self.assertEqual(s.agente.settings_dir, server.DATA_DIR)
        self.assertIs(j._summarizer(j.settings.get()), s)  # se reutiliza


# ----------------------------------------------------------------------------- la API: config, ajustes y comprobar

class ApiProcesadoTests(TempTree):
    def app(self, **st):
        a = server.App.__new__(server.App)
        a.settings = settings_store(tempfile.mkdtemp(dir=self.tmp), cloud=dict(server.DEFAULT_SETTINGS["cloud"]), **st)
        a.jobs = bare_jobs([])
        a.jobs.settings = a.settings
        a.jobs.cloud = None
        a.hotkey = types.SimpleNamespace(ok=True, error=None)
        a.rec = a.monitor = None
        a.rec_lock = threading.Lock()
        return a

    def test_config_lleva_el_modo_y_el_agente(self):
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"):
            c = self.app(procesado="agente", agente_version="2.1.314 (Claude Code)", agente_comprobado=123.0).config({})
        self.assertEqual(c["procesado"], "agente")
        self.assertEqual(c["agente"], {"tipo": "claude", "modelo": "sonnet", "label": "Claude Code",
                                       "modelos": [list(m) for m in engines.ClaudeCode.MODELOS],
                                       "encontrado": True, "ruta": "/x/claude", "version": "2.1.314 (Claude Code)",
                                       "comprobado": 123.0})
        self.assertEqual([p["id"] for p in c["presets"] if p["engine"] == "cloud"], [])  # sin API no se ofrece la nube
        with mock.patch.object(engines.ClaudeCode, "find", return_value=None):
            c = self.app(procesado="api").config({})
        self.assertEqual(c["procesado"], "api")
        self.assertEqual([p["id"] for p in c["presets"] if p["engine"] == "cloud"], ["nube"])
        self.assertEqual((c["agente"]["encontrado"], c["agente"]["version"]), (False, None))

    def test_guardar_el_modo_devuelve_los_motores_a_local(self):
        a = self.app(procesado="api", preset="nube", live_preset="nube")
        c = a.save_settings({"procesado": "local", "agente": {"tipo": "claude", "modelo": "opus"}})
        st = a.settings.get()
        self.assertEqual((st["procesado"], st["preset"], st["live_preset"]), ("local", "rapido", "medium"))
        self.assertEqual(st["agente"], {"tipo": "claude", "modelo": "opus"})
        self.assertEqual(c["procesado"], "local")
        a.save_settings({"procesado": "raro", "agente": {"tipo": "codex", "modelo": "gpt-5"}})
        self.assertEqual((a.settings.get()["procesado"], a.settings.get()["agente"]),
                         ("local", {"tipo": "claude", "modelo": "sonnet"}))

    def test_la_primera_key_solo_pone_la_nube_por_defecto_con_api(self):
        a = self.app(procesado="agente")
        a.save_settings({"cloud": {"api_key": KEY}})
        self.assertEqual((a.settings.get()["preset"], a.settings.get()["live_preset"]), ("rapido", "medium"))
        a = self.app(procesado="api")
        a.save_settings({"cloud": {"api_key": KEY}})
        self.assertEqual((a.settings.get()["preset"], a.settings.get()["live_preset"]), ("nube", "nube"))

    def test_comprobar_el_agente(self):
        a = self.app(procesado="agente")
        with mock.patch.object(engines.ClaudeCode, "find", return_value=None):
            with self.assertRaises(server.ApiError) as c:
                a.agente_comprobar({})
        self.assertIn("No encuentro Claude Code", c.exception.msg)
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"), \
                mock.patch.object(engines.ClaudeCode, "check", return_value=("2.1.314 (Claude Code)", 3.4)), \
                mock.patch.object(server, "log") as log:
            r = a.agente_comprobar({})
        self.assertEqual(r, {"ok": True, "version": "2.1.314 (Claude Code)", "seconds": 3.4, "ruta": "/x/claude"})
        st = a.settings.get()
        self.assertEqual(st["agente_version"], "2.1.314 (Claude Code)")
        self.assertGreater(st["agente_comprobado"], time.time() - 60)
        self.assertEqual(log.call_args[0][0], "   agente: Claude Code 2.1.314 comprobado (3.4 s)")
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"), \
                mock.patch.object(engines.ClaudeCode, "check", side_effect=engines.EngineError("sin sesión")), \
                mock.patch.object(server, "log"):
            with self.assertRaises(server.ApiError) as c:
                a.agente_comprobar({})
        self.assertEqual(c.exception.msg, "sin sesión")

    def test_la_ruta_existe_y_el_diagnostico_dice_donde(self):
        src = read_app("server.py")
        self.assertIn('"/api/agente/comprobar": a.agente_comprobar', src)
        a = self.app(procesado="agente", agente_version="2.1.314 (Claude Code)")
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"), \
                mock.patch.object(server, "perfil_de", return_value={"outputs": []}):
            texto = a.diagnostico({})["texto"]
        self.assertIn("Dónde se procesa: agente (Claude Code 2.1.314)", texto)
        for modo, esperado in (("local", "solo en este PC"), ("api", "API externa")):
            with mock.patch.object(server, "perfil_de", return_value={"outputs": []}):
                self.assertIn(f"Dónde se procesa: {esperado}", self.app(procesado=modo).diagnostico({})["texto"])

    def test_las_lineas_de_log_del_agente_son_tecnicas(self):
        self.assertEqual(server.diag_line("   agente: Claude Code 2.1.314 comprobado (3.4 s)"),
                         "   agente: Claude Code 2.1.314 comprobado (3.4 s)")
        self.assertEqual(server.diag_line("   agente: Claude Code: Claude Code ha fallado (código 1): boom"),
                         "   agente: Claude Code: <texto>")


# ----------------------------------------------------------------------------- la interfaz

class InterfazProcesadoTests(unittest.TestCase):
    def test_ajustes_ofrece_las_tres_opciones_con_sus_avisos(self):
        app = static("app.js")
        self.assertIn("function procesadoFieldsetHtml()", app)
        self.assertIn("${procesadoFieldsetHtml()}", app)
        self.assertIn('name="proc" value="${v}"', app)
        for v in ("local", "agente", "api"):
            self.assertIn(f"${{opt('{v}', ", app)
        self.assertIn("Solo en este PC", app)
        self.assertIn("Nada sale de él", app)
        self.assertIn("se envía a Anthropic con esa cuenta", app)
        self.assertIn("salen del PC hacia el proveedor", app)
        self.assertIn("En algunas reuniones puede ser un bloqueante", app)
        self.assertLess(app.index("${procesadoFieldsetHtml()}"), app.index("Transcripción en la nube</legend>"))

    def test_paso_a_paso_de_claude_code_y_comprobar(self):
        app = static("app.js")
        self.assertIn("irm https://claude.ai/install.ps1 | iex", app)
        self.assertIn("winget install Anthropic.ClaudeCode", app)
        self.assertIn("escribe <code>claude</code>", app)
        self.assertIn('id="agCheck"', app)
        self.assertIn("api('/api/agente/comprobar', {})", app)
        self.assertIn('id="agModelo"', app)
        self.assertIn("procesado: $('input[name=\"proc\"]:checked', d)?.value", app)
        self.assertIn("agente: { tipo: S.config.agente?.tipo || 'claude', modelo: $('#agModelo', d)?.value }", app)
        self.assertIn('$(\'#procApi\', d).hidden = v !== \'api\';', app)
        self.assertIn('$(\'#procAgente\', d).hidden = v !== \'agente\';', app)

    def test_la_nube_y_el_resumen_con_api_solo_se_ven_con_api(self):
        app = static("app.js")
        i = app.index('<div id="procApi"')
        self.assertLess(i, app.index("Transcripción en la nube</legend>"))
        self.assertLess(app.index("Transcripción en la nube</legend>"), app.index("Resumen con IA</legend>"))
        self.assertIn("S.config.procesado === 'api' ? '' : ' hidden'", app)

    def test_textos_segun_el_modo(self):
        app, notas = static("app.js"), static("notas.js")
        self.assertIn("S.config?.procesado === 'agente' ? `${S.config.agente?.label || 'Claude Code'} (tu cuenta)`",
                      app)
        self.assertIn("Elegir dónde se procesa (Ajustes)", notas)
        self.assertIn("S.config.procesado === 'api' ? S.config.cloud?.has_key : true", notas)
        css = static("app.css")
        for sel in (".procs {", ".proc.on {", "ol.pasos {"):
            self.assertIn(sel, css)


if __name__ == "__main__":
    unittest.main()
