# -*- coding: utf-8 -*-
"""«Comprobar» con Claude Code tardó minutos en el PC de Bernardo (07/11/2026, msgs 1185-1187). Lo que se arregla aquí:

- AgenteLocal.run espera a que SALGA el proceso, no a que se cierren sus tuberías: en Windows un hijo que las herede y
  siga vivo (el actualizador de Claude Code, por ejemplo) alargaba la llamada hasta que ese hijo acababa.
- Las llamadas a Claude Code van sin tráfico no esencial (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC): ni actualización
  automática, ni telemetría, ni comprobaciones de disponibilidad que en una red de empresa se esperan.
- «Comprobar» deja en server.log cuánto tardó cada paso y la interfaz enseña los segundos mientras espera.
Con procesos de verdad (un hijo que deja un nieto con su salida abierta) y dobles para Claude Code."""
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

NIETO = ("import subprocess, sys; "
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "  # hereda stdout y stderr
         "print('hecho', flush=True); sys.exit(0)")


class RunTests(unittest.TestCase):
    def test_no_espera_a_un_hijo_que_hereda_la_salida(self):
        avisos = []
        with mock.patch.object(engines.AgenteLocal, "log", avisos.append):
            t0 = time.time()
            code, out, err = engines.AgenteLocal.run([sys.executable, "-c", NIETO], "x", timeout=60)
            secs = time.time() - t0
        self.assertEqual((code, out.strip()), (0, "hecho"), err)
        # antes: communicate() esperaba al fin de stdout, es decir, a que el nieto acabara (10 s)
        self.assertLess(secs, engines.AGENTE_PIPE_GRACE_S + 4)
        self.assertTrue(any("seguía con su salida abierta" in a for a in avisos), avisos)

    def test_sin_hijos_no_hay_aviso_y_llega_toda_la_salida(self):
        avisos = []
        big = "import sys; sys.stdout.write('y' * 500000); sys.stderr.write('aviso')"
        with mock.patch.object(engines.AgenteLocal, "log", avisos.append):
            code, out, err = engines.AgenteLocal.run([sys.executable, "-c", big], "", timeout=60)
        self.assertEqual((code, len(out), err), (0, 500000, "aviso"))
        self.assertEqual(avisos, [])

    def test_sin_log_no_falla(self):
        with mock.patch.object(engines.AgenteLocal, "log", None):
            code, out, _ = engines.AgenteLocal.run([sys.executable, "-c", NIETO], "", timeout=60)
        self.assertEqual((code, out.strip()), (0, "hecho"))


SHIM_JS = ('@ECHO off\r\nIF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n'
           '  SET "_prog=node"\r\n)'
           '\r\nendLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
           '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*\r\n')
SHIM_EXE = '@ECHO off\r\n"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n'


class ShimNpmTests(unittest.TestCase):
    """Un .cmd de npm pasa por cmd.exe, que rompe una ruta con «&» o «%» (medido en Windows el 07/11/2026: «'100%' is
    not recognized…»): se lanza lo que lanza el .cmd, sin cmd.exe."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="npm Chu leta & 100% y 50% ")
        self.addCleanup(__import__("shutil").rmtree, self.d, ignore_errors=True)
        self.pkg = os.path.join(self.d, "node_modules", "@anthropic-ai", "claude-code")
        os.makedirs(os.path.join(self.pkg, "bin"))
        for n in ("cli.js", os.path.join("bin", "claude.exe")):
            open(os.path.join(self.pkg, n), "w").close()

    def shim(self, texto, nombre="claude.cmd"):
        p = os.path.join(self.d, nombre)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(texto)
        return p

    def test_shim_de_js_lanza_node_con_el_js(self):
        p = self.shim(SHIM_JS)
        with mock.patch.object(engines.shutil, "which", return_value="/usr/bin/node"):
            self.assertEqual(engines.AgenteLocal.argv0(p), ["/usr/bin/node", os.path.join(self.pkg, "cli.js")])
        open(os.path.join(self.d, "node.exe"), "w").close()  # el node que trae junto al .cmd, si lo hay, primero
        self.assertEqual(engines.AgenteLocal.argv0(p)[0], os.path.join(self.d, "node.exe"))

    def test_shim_de_exe_lanza_el_exe(self):
        self.assertEqual(engines.AgenteLocal.argv0(self.shim(SHIM_EXE)), [os.path.join(self.pkg, "bin", "claude.exe")])

    def test_lo_demas_tal_cual(self):
        self.assertEqual(engines.AgenteLocal.argv0("/x/claude"), ["/x/claude"])
        self.assertEqual(engines.AgenteLocal.argv0("C:\\x\\claude.exe"), ["C:\\x\\claude.exe"])
        raro = self.shim("@echo off\r\nclaude-real %*\r\n", "otro.cmd")
        self.assertEqual(engines.AgenteLocal.argv0(raro), [raro])
        os.remove(os.path.join(self.pkg, "cli.js"))  # el destino no existe: el .cmd tal cual
        p = self.shim(SHIM_JS)
        self.assertEqual(engines.AgenteLocal.argv0(p), [p])
        with mock.patch.object(engines.shutil, "which", return_value=None):  # sin node: el .cmd tal cual
            open(os.path.join(self.pkg, "cli.js"), "w").close()
            self.assertEqual(engines.AgenteLocal.argv0(p), [p])

    def test_version_y_chat_lanzan_lo_resuelto(self):
        p = self.shim(SHIM_EXE)
        exe = os.path.join(self.pkg, "bin", "claude.exe")
        seen = []

        def run(cmd, stdin_text=None, timeout=0, cancelled=None, cwd=None, env=None):
            seen.append(cmd)
            return (0, "2.1.999 (Claude Code)\n", "") if "--version" in cmd else (0, json.dumps(
                {"type": "result", "is_error": False, "result": "OK", "modelUsage": {"claude-x": {}}}), "")
        with mock.patch.object(engines.ClaudeCode, "run", side_effect=run):
            engines.ClaudeCode.version(p)
            engines.ClaudeCode(settings_dir=self.d).chat("s", "u", 10, lambda **k: None, lambda: False, path=p)
        self.assertEqual(seen[0], [exe, "--version"])
        self.assertEqual(seen[1][:3], [exe, "-p", "--output-format"])
        self.assertEqual(seen[1][seen[1].index("--tools") + 1], "")


class ErroresDeClaudeTests(unittest.TestCase):
    """Qué frase ve el usuario cuando Claude Code está pero falla: los textos imitan salidas de Claude Code (en
    windows-latest, sin sesión, la de verdad ya acaba en «sin sesión iniciada»: claude-windows.yml)."""

    def msg(self, text, json_result=False):
        ag = engines.ClaudeCode()
        out = json.dumps({"type": "result", "is_error": True, "result": text}) if json_result else ""
        with self.assertRaises(engines.EngineError) as c:
            ag._parse(1, out, "" if json_result else text, "sonnet")
        return str(c.exception)

    def test_proxy_407_no_es_sin_sesion(self):
        m = self.msg("API Error: 407 Proxy Authentication Required", json_result=True)
        self.assertIn("no puede conectar con Anthropic desde esta red", m)
        self.assertNotIn("sin sesión", m)

    def test_red(self):
        for t in ("API Error: Connection error. (unable to get local issuer certificate)",
                  "API Error: Connection error. (getaddrinfo ENOTFOUND api.anthropic.com)",
                  "Error: self signed certificate in certificate chain", "connect ETIMEDOUT 160.79.104.10:443"):
            self.assertIn("no puede conectar con Anthropic desde esta red", self.msg(t), t)

    def test_politica_de_empresa(self):
        m = self.msg("Your organization has disabled Claude Code for this account.", json_result=True)
        self.assertTrue(m.startswith("Tu organización no deja usar Claude Code con esta cuenta"), m)

    def test_git_bash(self):
        m = self.msg("Claude Code on Windows requires git-bash (https://git-scm.com/downloads/win).")
        self.assertIn("necesita Git para Windows (Git Bash)", m)

    def test_sin_sesion_sigue_igual(self):
        for t in ("Invalid API key · Please run /login", "Not logged in · Please run /login",
                  "OAuth token has expired. Please obtain a new token or refresh your existing token."):
            self.assertIn("sin sesión iniciada", self.msg(t, json_result=True), t)

    def test_lo_demas_tal_cual(self):
        self.assertEqual(self.msg("algo raro"), "Claude Code ha fallado (código 1): algo raro")


class ExplicarClaudeTests(unittest.TestCase):
    """Las tres frases de ClaudeCode._why salen igual por explicar.py (Steve, ux-configurar), con su botón: en la Cola,
    en «Probar» del asistente y en «Comprobar» de Ajustes."""
    CASOS = (("API Error: 407 Proxy Authentication Required", "no puede conectar con Anthropic", "reintentar"),
             ("API Error: Connection error. (unable to get local issuer certificate)",
              "no puede conectar con Anthropic", "reintentar"),
             ("Your organization has disabled Claude Code for this account.",
              "no deja usar Claude Code con esta cuenta", "abrir_powershell"),
             ("Claude Code on Windows requires git-bash (https://git-scm.com/downloads/win).",
              "necesita Git para Windows", "abrir_powershell"))

    def error(self, texto):
        with self.assertRaises(engines.EngineError) as c:
            engines.ClaudeCode()._parse(1, json.dumps({"type": "result", "is_error": True, "result": texto}), "", "s")
        return c.exception

    def test_frase_y_boton(self):
        import explicar
        for texto, trozo, accion in self.CASOS:
            e = self.error(texto)
            ex = explicar.explicar(e)
            self.assertEqual(ex["frase"], str(e), texto)  # la frase de engines, tal cual
            self.assertIn(trozo, ex["frase"], texto)
            self.assertEqual(ex["accion"]["tipo"], accion, texto)
            self.assertIn(ex["accion"]["etiqueta"], ("Probar otra vez", "Abrir PowerShell"))
            self.assertTrue(ex["frase"].endswith("y vuelve a probar."), ex["frase"])  # vale en Ajustes y en «Probar»
        # sin sesión sigue con la frase y el botón de Steve
        ex = explicar.explicar(self.error("Not logged in · Please run /login"))
        self.assertTrue(ex["frase"].startswith("Claude Code no tiene la sesión iniciada"), ex["frase"])
        self.assertEqual(ex["accion"]["tipo"], "abrir_powershell")

    def test_comprobar_del_servidor(self):
        a = ServidorComprobarTests.app(self)
        for texto, trozo, _ in self.CASOS:
            e = self.error(texto)

            def check(self_, path, model=None, cancelled=None, e=e):
                raise e
            with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"), \
                    mock.patch.object(engines.ClaudeCode, "check", check), mock.patch.object(server, "log"):
                with self.assertRaises(server.ApiError) as c:
                    a.agente_comprobar({})
            self.assertIn(trozo, c.exception.msg)

    def setUp(self):  # para ServidorComprobarTests.app
        self.tmp = tempfile.mkdtemp(prefix="chuleta-explicar-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)


class EntornoTests(unittest.TestCase):
    def test_sin_trafico_no_esencial_y_sin_claves(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-no", "OTRA": "1"}):
            os.environ.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)
            env = engines.ClaudeCode._env()
        self.assertEqual(env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["OTRA"], "1")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "true"}):
            self.assertEqual(engines.ClaudeCode._env()["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "true")  # el suyo

    def test_version_tambien_va_sin_trafico_no_esencial(self):
        seen = {}

        def run(cmd, stdin_text=None, timeout=0, cancelled=None, cwd=None, env=None):
            seen.update(cmd=cmd, env=env)
            return 0, "2.1.999 (Claude Code)\n", ""
        with mock.patch.object(engines.ClaudeCode, "run", side_effect=run):
            self.assertEqual(engines.ClaudeCode.version("/x/claude"), "2.1.999")
        self.assertEqual(seen["cmd"], ["/x/claude", "--version"])
        self.assertEqual(seen["env"].get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"), "1")

    def test_check_anota_lo_que_tarda_cada_paso(self):
        ag = engines.ClaudeCode()

        def chat(*a, **k):
            time.sleep(0.2)
            return "OK", "m"
        with mock.patch.object(engines.ClaudeCode, "version", return_value="2.1.999"), \
                mock.patch.object(engines.ClaudeCode, "chat", side_effect=chat):
            version, secs = ag.check("/x/claude")
        self.assertEqual(set(ag.tiempos), {"version_s", "peticion_s"})
        self.assertGreaterEqual(ag.tiempos["peticion_s"], 0.2)
        # si la petición falla, su tiempo también queda (para el log del error)
        ag2 = engines.ClaudeCode()
        with mock.patch.object(engines.ClaudeCode, "version", return_value="2.1.999"), \
                mock.patch.object(engines.ClaudeCode, "chat", side_effect=engines.EngineError("sin sesión")):
            with self.assertRaises(engines.EngineError):
                ag2.check("/x/claude")
        self.assertIn("peticion_s", ag2.tiempos)


class ServidorComprobarTests(TempTree):
    def app(self, **st):
        a = server.App.__new__(server.App)
        st.setdefault("cloud", dict(server.DEFAULT_SETTINGS["cloud"]))
        a.settings = settings_store(tempfile.mkdtemp(dir=self.tmp), **st)
        a.jobs = bare_jobs([])
        a.jobs.settings = a.settings
        a.hotkey = types.SimpleNamespace(ok=True, error=None)
        a.rec = a.monitor = None
        a.rec_lock = threading.Lock()
        return a

    def test_log_con_cada_paso(self):
        def check(self, path, model=None, cancelled=None):
            self.tiempos = {"version_s": 2.1, "peticion_s": 41.0}
            return "2.1.999", 43.1
        a = self.app(procesado="agente")
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"), \
                mock.patch.object(engines.ClaudeCode, "check", check), mock.patch.object(server, "log") as log:
            r = a.agente_comprobar({"tipo": "claude", "modelo": "sonnet"})
        lineas = [c[0][0] for c in log.call_args_list]
        self.assertEqual(r["version"], "2.1.999")
        self.assertEqual(lineas, ["   agente: comprobando Claude Code (sonnet)…",
                                  "   agente: --version 2.1 s · petición mínima 41.0 s · total 43.1 s",
                                  "   agente: Claude Code 2.1.999 comprobado (43.1 s)"])

    def test_log_del_error_con_lo_que_tardo(self):
        def check(self, path, model=None, cancelled=None):
            self.tiempos = {"version_s": 1.0, "peticion_s": 120.4}
            raise engines.EngineError("Claude Code no ha contestado en 120 s")
        a = self.app(procesado="agente")
        with mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"), \
                mock.patch.object(engines.ClaudeCode, "check", check), mock.patch.object(server, "log") as log:
            with self.assertRaises(server.ApiError):
                a.agente_comprobar({})
        ultima = log.call_args_list[-1][0][0]
        self.assertTrue(ultima.startswith("   agente: Claude Code: Claude Code no ha contestado en 120 s ("), ultima)
        self.assertIn("petición mínima 120.4 s", ultima)

    def test_el_log_de_los_agentes_se_conecta_al_arrancar(self):
        src = open(os.path.join(APP, "server.py"), encoding="utf-8").read()
        i = src.index("def main():")
        self.assertLess(src.index("engines.AgenteLocal.log = log", i), src.index("Handler.app = App()", i))


class InterfazTests(unittest.TestCase):
    def test_comprobar_cuenta_los_segundos(self):
        js = open(os.path.join(APP, "static", "app.js"), encoding="utf-8").read()
        i = js.index("$('#agCheck', d).onclick")
        bloque = js[i:js.index("$('#aDatos', d).onclick", i)]
        self.assertIn("Comprobando… ${seg()} s", bloque)
        self.assertIn("setInterval(tic, 1000)", bloque)
        self.assertIn("clearInterval(timer)", bloque)
        self.assertIn("btn.disabled = true", bloque)
        self.assertNotIn("medio minuto", bloque)


if __name__ == "__main__":
    unittest.main()
