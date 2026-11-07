# -*- coding: utf-8 -*-
"""«Con tu cuenta de Claude» con la cuenta de la empresa (07/11/2026, msgs 1224-1228: Bernardo entra en Claude con el
SSO de su empresa y usa Claude Code en VS Code). Lo que se prueba aquí:

- Chuleta encuentra la copia de Claude Code que trae la extensión de VS Code (la extensión no pone `claude` en el PATH)
  y dice de dónde salió.
- Lanza Claude Code con el entorno del usuario (lo puesto en el registro después de arrancar Chuleta también cuenta),
  sin las claves de API del entorno salvo con una pasarela de la empresa.
- Si el modelo elegido no está para esa cuenta, repite con el predeterminado de su Claude Code.
- Qué frase ve el usuario con cada error de empresa: sesión caducada, renovándose en otra ventana, organización que no
  deja, sin sesión en la copia de VS Code.

Los «claude» falsos son procesos de verdad (Python) que imitan la salida JSON de `claude -p`. Los textos de error son
los de Claude Code 2.1.319 (cadenas del binario y code.claude.com/docs/en/errors, 07/11/2026); el envoltorio
«API Error: 403 {…}» de uno de ellos es imitado."""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from support import engines, server  # noqa: F401  (support prepara sys.path)
from test_fix_claude_windows import ServidorComprobarTests  # noqa: E402  (ayudante: app())

FAKE = r'''
import json, os, sys
argv = sys.argv[1:]
if "--version" in argv:
    print("2.1.319 (Claude Code)")
    sys.exit(0)
stdin = sys.stdin.read()
env = os.environ
with open(env["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "stdin": len(stdin),
                        "env": {k: env.get(k) for k in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "CLAUDE_CONFIG_DIR",
                                                         "ANTHROPIC_BASE_URL", "NODE_EXTRA_CA_CERTS", "HTTPS_PROXY")},
                        "claves": [k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if env.get(k)]}) + "\n")
caso = env.get("FAKE_CASO", "ok")
ERRORES = {
    "org_suscripcion": "Your organization has disabled Claude subscription access for Claude Code \u00b7 Use an "
                       "Anthropic API key instead, or ask your admin to enable access",
    "oauth_org": 'API Error: 403 {"type":"error","error":{"type":"permission_error","message":"OAuth authentication '
                 'is currently not allowed for this organization."}}',
    "api_key_org": "Your organization has disabled API key authentication \u00b7 Unset the apiKeyHelper setting and "
                   "run /login to sign in with your claude.ai account",
    "caducada": "Login expired \u00b7 Please run /login",
    "revocada": "OAuth token revoked \u00b7 Please run /login",
    "limite_tiempo": "Failed to authenticate: this login reached its time limit and cannot be renewed. Run `claude "
                     "auth login` to sign in again, or use `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN for "
                     "unattended jobs",
    "renovando": "Could not refresh your login because another Claude Code process is refreshing it (or exited "
                 "mid-refresh) \u00b7 Try again in a minute; if it keeps happening, close other Claude Code windows "
                 "or sign in again with /login",
    "sin_sesion": "Not logged in \u00b7 Please run /login",
    "tls": "Unable to connect to API: SSL certificate verification failed (UNABLE_TO_GET_ISSUER_CERT_LOCALLY). The "
           "certificates the server sent don't lead to a trusted authority.",
    "ajustes_org": "Couldn't load your organization's settings. Check your connection, then restart Claude Code.",
}
MODELO = ("There's an issue with the selected model (claude-sonnet-5-5). It may not exist or you may not have access "
          "to it. Run /model to pick a different model.")


def ok(modelo):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "OK",
                      "modelUsage": {modelo: {"inputTokens": 3, "outputTokens": 1}}}))
    sys.exit(0)


def error(texto):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": texto}))
    sys.exit(1)


if caso == "ok":
    ok("claude-sonnet-5-5")
if caso == "modelo":  # la empresa no tiene el elegido: sin --model, el suyo
    error(MODELO) if "--model" in argv else ok("claude-opus-5-5")
if caso == "modelo_siempre":
    error(MODELO)
if caso == "pasarela":  # pasarela de la empresa con su certificado: sin su token, sin sesión
    if env.get("ANTHROPIC_BASE_URL") and env.get("ANTHROPIC_AUTH_TOKEN") and env.get("NODE_EXTRA_CA_CERTS"):
        ok("empresa-sonnet")
    error(ERRORES["sin_sesion"])
error(ERRORES[caso])
'''


class ConFalso(unittest.TestCase):
    """Un «claude» falso de verdad (proceso Python) en `self.exe`; `self.log` con lo que recibió cada llamada."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-empresa-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.exe = self.falso(os.path.join(self.tmp, "bin", "claude"))
        self.log = os.path.join(self.tmp, "llamadas.jsonl")
        p = mock.patch.object(engines.ClaudeCode, "argv0", staticmethod(lambda path: [sys.executable, path]))
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def falso(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(FAKE)
        return path

    def llamadas(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                return [json.loads(x) for x in f]
        except OSError:
            return []

    def chat(self, caso, path=None, extra_env=None, model=None):
        env = {"FAKE_CASO": caso, "FAKE_LOG": self.log, **(extra_env or {})}
        with mock.patch.dict(os.environ, env):
            ag = engines.ClaudeCode(settings_dir=os.path.join(self.tmp, "datos"))
            out = ag.chat("Resume.", "texto de la reunión", 50, lambda **k: None, lambda: False, model=model,
                          timeout=60, path=path or self.exe)
        return ag, out

    def error(self, caso, path=None, extra_env=None):
        with self.assertRaises(engines.EngineError) as c:
            self.chat(caso, path=path, extra_env=extra_env)
        return str(c.exception)


class SesionDeEmpresaTests(ConFalso):
    def test_sesion_de_empresa_con_el_entorno_del_usuario(self):
        # la sesión vive en el perfil del usuario (%USERPROFILE%\.claude, o CLAUDE_CONFIG_DIR si lo tiene): Chuleta
        # no la toca y le pasa el mismo entorno; las claves de API del entorno no (irían a la API, no a su cuenta)
        ag, (text, used) = self.chat("ok", extra_env={"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, ".claude-empresa"),
                                                      "ANTHROPIC_API_KEY": "sk-ant-personal",
                                                      "HTTPS_PROXY": "http://proxy.empresa:8080"})
        self.assertEqual((text, used), ("OK", "claude-sonnet-5-5"))
        (c,) = self.llamadas()
        self.assertEqual(c["argv"][c["argv"].index("--model") + 1], "sonnet")
        self.assertEqual(c["env"]["CLAUDE_CONFIG_DIR"], os.path.join(self.tmp, ".claude-empresa"))
        self.assertEqual(c["env"]["HTTPS_PROXY"], "http://proxy.empresa:8080")
        self.assertEqual(c["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
        self.assertEqual(c["claves"], [])
        self.assertGreater(c["stdin"], 0)
        self.assertIsNone(ag.aviso)

    def test_check_con_la_cuenta_de_empresa(self):
        with mock.patch.dict(os.environ, {"FAKE_CASO": "ok", "FAKE_LOG": self.log}):
            ag = engines.ClaudeCode(settings_dir=os.path.join(self.tmp, "datos"))
            version, _ = ag.check(self.exe, model="opus")
        self.assertEqual((version, ag.modelo_usado), ("2.1.319", "claude-sonnet-5-5"))

    def test_caducada_o_revocada(self):
        for caso in ("caducada", "revocada", "limite_tiempo"):
            m = self.error(caso)
            self.assertTrue(m.startswith("La sesión de Claude Code ha caducado ("), m)
            self.assertIn("en PowerShell escribe claude y luego /login", m)
            self.assertIn("la de tu empresa", m)
            self.assertNotIn("Please run /login", m)  # explicar.py la cambiaría por «sin sesión iniciada»
            self.assertTrue(m.endswith("y vuelve a probar."), m)

    def test_otro_proceso_renovando_la_sesion(self):
        m = self.error("renovando")
        self.assertEqual(m, "Claude Code está renovando tu sesión en otra ventana (por ejemplo, en VS Code): espera "
                            "un minuto y vuelve a probar.")

    def test_la_organizacion_no_deja(self):
        for caso in ("org_suscripcion", "oauth_org"):
            m = self.error(caso)
            self.assertTrue(m.startswith("Tu organización no deja usar Claude Code con esta cuenta ("), m)
            self.assertIn("pide acceso a quien gestiona Claude en tu empresa (IT)", m)
            self.assertNotIn("personal", m)  # nunca la cuenta personal para las reuniones del trabajo
        m = self.error("api_key_org")
        self.assertIn("no admite API keys", m)
        self.assertIn("en PowerShell escribe claude y luego /login", m)

    def test_red_y_certificado_de_empresa(self):
        for caso in ("tls", "ajustes_org"):
            self.assertIn("no puede conectar con Anthropic desde esta red", self.error(caso), caso)

    def test_sin_sesion_sigue_igual(self):
        self.assertIn("sin sesión iniciada", self.error("sin_sesion"))


class ModeloDeEmpresaTests(ConFalso):
    def test_sin_el_modelo_elegido_usa_el_de_su_claude_code(self):
        avisos = []
        with mock.patch.object(engines.AgenteLocal, "log", avisos.append):
            ag, (text, used) = self.chat("modelo", model="sonnet")
        self.assertEqual((text, used), ("OK", "claude-opus-5-5"))
        primera, segunda = self.llamadas()
        self.assertIn("--model", primera["argv"])
        self.assertNotIn("--model", segunda["argv"])
        self.assertEqual(segunda["argv"][-1], engines.ClaudeCode.PROMPT)
        self.assertIn("se ha usado el predeterminado de tu Claude Code (claude-opus-5-5)", ag.aviso)
        self.assertTrue(any("no puede usar «sonnet»" in a for a in avisos), avisos)

    def test_con_el_log_del_servidor(self):
        # el del servidor es una función normal (server.log(msg)): desde la instancia recibiría self y fallaría
        lineas = []

        def log(msg):
            lineas.append(msg)
        with mock.patch.object(engines.AgenteLocal, "log", log):
            _, (text, used) = self.chat("modelo")
        self.assertEqual((text, used), ("OK", "claude-opus-5-5"))
        self.assertEqual(len(lineas), 1, lineas)

    def test_si_tampoco_hay_predeterminado_lo_dice(self):
        m = self.error("modelo_siempre")
        self.assertTrue(m.startswith("Claude Code no puede usar el modelo elegido con esta cuenta ("), m)
        self.assertEqual(len(self.llamadas()), 2)  # una vez con --model y otra sin él, no más

    def test_otros_errores_no_repiten(self):
        self.error("caducada")
        self.assertEqual(len(self.llamadas()), 1)


class PasarelaTests(ConFalso):
    def test_pasarela_con_certificado_propio_conserva_su_token(self):
        _, (text, used) = self.chat("pasarela", extra_env={
            "ANTHROPIC_BASE_URL": "https://llm.empresa.example/anthropic", "ANTHROPIC_AUTH_TOKEN": "gw-token",
            "NODE_EXTRA_CA_CERTS": os.path.join(self.tmp, "empresa-ca.pem")})
        self.assertEqual((text, used), ("OK", "empresa-sonnet"))
        self.assertEqual(self.llamadas()[0]["claves"], ["ANTHROPIC_AUTH_TOKEN"])

    def test_la_api_de_anthropic_no_es_una_pasarela(self):
        self.error("pasarela", extra_env={"ANTHROPIC_BASE_URL": "https://api.anthropic.com",
                                          "ANTHROPIC_AUTH_TOKEN": "x", "NODE_EXTRA_CA_CERTS": "c.pem"})
        self.assertEqual(self.llamadas()[0]["claves"], [])


def _arbol_vscode(home, carpetas, editor=".vscode", obsolete=None):
    """Carpetas de extensión como las deja VS Code al instalar el paquete (extension/ → la carpeta de la versión)."""
    base = os.path.join(home, editor, "extensions")
    rutas = {}
    for nombre, con_binario in carpetas:
        d = os.path.join(base, nombre, *engines.ClaudeCode.ext_bin[:-1])
        os.makedirs(d, exist_ok=True)
        if con_binario:
            p = os.path.join(d, engines.ClaudeCode.ext_bin[-1])
            ConFalso.falso(p)
            rutas[nombre] = p
    if obsolete:
        with open(os.path.join(base, ".obsolete"), "w", encoding="utf-8") as f:
            json.dump({n: True for n in obsolete}, f)
    return rutas


class VSCodeTests(ConFalso):
    """La extensión trae su copia en resources/native-binary/claude.exe (paquete win32-x64 2.1.319 de Open VSX) y no
    pone `claude` en el PATH (code.claude.com/docs/en/vs-code)."""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "perfil")
        for p in (mock.patch.dict(os.environ, {"USERPROFILE": self.home}),
                  mock.patch.object(engines.shutil, "which", return_value=None),
                  mock.patch.object(engines.ClaudeCode, "win_paths", ())):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("VSCODE_PORTABLE", None)

    def test_sin_programa_suelto_usa_la_copia_de_vscode_mas_nueva(self):
        rutas = _arbol_vscode(self.home, [("anthropic.claude-code-2.1.99-win32-x64", True),
                                          ("anthropic.claude-code-2.1.319-win32-x64", True),
                                          ("anthropic.claude-code-2.1.400-win32-x64", True),   # dada de baja
                                          ("anthropic.claude-code-2.1.500-win32-x64", False),  # a medio instalar
                                          ("otra.extension-9.9.9", False)],
                              obsolete=["anthropic.claude-code-2.1.400-win32-x64"])
        _arbol_vscode(self.home, [("anthropic.claude-code-2.1.300-win32-x64", True)], editor=".cursor")
        p = engines.ClaudeCode.find()
        self.assertEqual(p, rutas["anthropic.claude-code-2.1.319-win32-x64"])  # 2.1.319 > 2.1.99 (no por texto)
        self.assertEqual(engines.ClaudeCode.origen(p), "Claude Code de VS Code")

    def test_cursor_y_mayusculas(self):
        rutas = _arbol_vscode(self.home, [("Anthropic.claude-code-2.1.319", True)], editor=".cursor")
        p = engines.ClaudeCode.find()
        self.assertEqual(p, rutas["Anthropic.claude-code-2.1.319"])
        self.assertEqual(engines.ClaudeCode.origen(p), "Claude Code de Cursor")

    def test_el_programa_suelto_va_primero(self):
        _arbol_vscode(self.home, [("anthropic.claude-code-2.1.319-win32-x64", True)])
        with mock.patch.object(engines.shutil, "which", return_value="/usr/local/bin/claude"):
            self.assertEqual(engines.ClaudeCode.find(), "/usr/local/bin/claude")
        self.assertEqual(engines.ClaudeCode.origen("/usr/local/bin/claude"), "Claude Code")

    def test_sin_nada_no_encuentra(self):
        self.assertIsNone(engines.ClaudeCode.find())

    def test_sin_sesion_en_la_copia_de_vscode_dice_que_se_entra_en_vscode(self):
        import explicar
        rutas = _arbol_vscode(self.home, [("anthropic.claude-code-2.1.319-win32-x64", True)])
        p = rutas["anthropic.claude-code-2.1.319-win32-x64"]
        for caso, trozo in (("sin_sesion", "no tiene la sesión abierta: abre VS Code y, en el panel de Claude Code, "
                                           "inicia sesión con tu cuenta"),
                            ("caducada", "abre VS Code y, en el panel de Claude Code, vuelve a iniciar sesión")):
            m = self.error(caso, path=p)
            self.assertIn(trozo, m)
            self.assertNotIn("PowerShell", m)  # no hay `claude` en el PATH con solo la extensión
            self.assertEqual(explicar.explicar(engines.EngineError(m))["frase"], m)  # explicar.py la deja tal cual

    def test_con_la_sesion_de_vscode_funciona(self):
        rutas = _arbol_vscode(self.home, [("anthropic.claude-code-2.1.319-win32-x64", True)])
        _, (text, _) = self.chat("ok", path=rutas["anthropic.claude-code-2.1.319-win32-x64"])
        self.assertEqual(text, "OK")


class RegistroTests(unittest.TestCase):
    """Lo puesto en el registro después de arrancar Chuleta (setx, IT) llega a Claude Code, como a una PowerShell
    nueva: un winreg falso con valores de sistema y de usuario."""
    REG = {"HKLM": {"Path": r"C:\Windows\system32", "HTTPS_PROXY": "http://proxy-sistema:3128", "ComSpec": "cmd"},
           "HKCU": {"Path": r"C:\Users\b\AppData\Local\Programs\Git\cmd", "HTTPS_PROXY": "http://proxy.empresa:8080",
                    "NODE_EXTRA_CA_CERTS": r"C:\certs\empresa.pem", "CLAUDE_CONFIG_DIR": r"C:\Users\b\.claude",
                    "ANTHROPIC_API_KEY": "sk-ant-registro"}}

    def winreg(self, reg):
        class Key:
            def __init__(self, vals):
                self.vals = vals

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def OpenKey(root, sub):
            if root not in reg:
                raise OSError("no existe")
            return Key(reg[root])

        def EnumValue(k, i):
            items = list(k.vals.items())
            if i >= len(items):
                raise OSError("no hay más")
            return items[i][0], items[i][1], 1

        def QueryValueEx(k, name):
            for n, v in k.vals.items():
                if n.upper() == name.upper():
                    return v, 2
            raise OSError("no existe")
        return types.SimpleNamespace(HKEY_LOCAL_MACHINE="HKLM", HKEY_CURRENT_USER="HKCU", OpenKey=OpenKey,
                                     EnumValue=EnumValue, QueryValueEx=QueryValueEx)

    def env(self, reg, base):
        with mock.patch.object(engines, "_NT", True), mock.patch.dict(sys.modules, {"winreg": self.winreg(reg)}), \
                mock.patch.dict(os.environ, base, clear=True):
            return engines.windows_env(), engines.ClaudeCode._env()

    def test_lo_del_registro_llega_y_el_usuario_manda(self):
        env, cc = self.env(self.REG, {"PATH": "/heredado", "OTRA": "1", "HTTPS_PROXY": "http://viejo:1"})
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy.empresa:8080")  # usuario sobre sistema y sobre lo heredado
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], r"C:\certs\empresa.pem")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], r"C:\Users\b\.claude")
        self.assertEqual(env["COMSPEC"], "cmd")
        self.assertEqual(env["OTRA"], "1")  # lo que el registro no tiene, como estaba
        for trozo in (r"Git\cmd", "system32", "/heredado"):
            self.assertIn(trozo, env["PATH"])
        self.assertNotIn("ANTHROPIC_API_KEY", cc)  # aunque venga del registro
        self.assertEqual(cc["NODE_EXTRA_CA_CERTS"], r"C:\certs\empresa.pem")

    def test_sin_registro_lo_heredado(self):
        env, _ = self.env({}, {"PATH": "/heredado", "OTRA": "1"})
        self.assertEqual((env["OTRA"], env["PATH"]), ("1", "/heredado"))

    def test_fuera_de_windows_tal_cual(self):
        with mock.patch.object(engines, "_NT", False), mock.patch.dict(os.environ, {"OTRA": "2"}):
            self.assertEqual(engines.windows_env()["OTRA"], "2")


class ServidorTests(ServidorComprobarTests):
    def test_comprobar_dice_de_donde_salio_y_el_aviso_del_modelo(self):
        def check(self_, path, model=None, cancelled=None):
            self_.tiempos, self_.modelo_usado = {"version_s": 0.1, "peticion_s": 1.0}, "claude-opus-5-5"
            self_.aviso = "El modelo elegido (sonnet) no está disponible con esta cuenta: se ha usado el predeterminado"
            return "2.1.319", 1.1
        ruta = os.path.join("C:", "Users", "b", ".vscode", "extensions", "anthropic.claude-code-2.1.319-win32-x64",
                            "resources", "native-binary", "claude.exe")
        a = self.app(procesado="agente")
        with mock.patch.object(engines.ClaudeCode, "find", return_value=ruta), \
                mock.patch.object(engines.ClaudeCode, "origen", return_value="Claude Code de VS Code"), \
                mock.patch.object(engines.ClaudeCode, "check", check), mock.patch.object(server, "log") as log:
            r = a.agente_comprobar({"tipo": "claude", "modelo": "sonnet"})
            e = a.agente_estado({})
        self.assertEqual((r["origen"], r["modelo_usado"]), ("Claude Code de VS Code", "claude-opus-5-5"))
        self.assertTrue(r["aviso"].startswith("El modelo elegido (sonnet)"))
        self.assertEqual((e["encontrado"], e["origen"]), (True, "Claude Code de VS Code"))
        lineas = [c[0][0] for c in log.call_args_list]
        self.assertTrue(lineas[0].startswith("   agente: comprobando Claude Code de VS Code (sonnet)… · "), lineas[0])
        self.assertIn("   agente: El modelo elegido (sonnet) no está disponible con esta cuenta: se ha usado el "
                      "predeterminado", lineas)


if __name__ == "__main__":
    unittest.main()
