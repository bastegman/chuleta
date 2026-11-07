# -*- coding: utf-8 -*-
"""Configuración en tres pasos (07/11/2026, Steve): errores en una frase (explicar.py), la línea del modo y
«Preparando…» de la interfaz (configurar.js, con node) y, más abajo, «Probar» (probar.py)."""
import json
import os
import shutil
import subprocess
import unittest

from support import APP, REPO, engines, server  # noqa: F401  (support prepara sys.path)

import explicar  # noqa: E402

NODE = shutil.which("node")


class ExplicarTests(unittest.TestCase):
    def frase(self, err, **kw):
        return explicar.explicar(err, **kw)["frase"]

    def accion(self, err, **kw):
        return (explicar.explicar(err, **kw)["accion"] or {}).get("tipo")

    def test_claude_sin_sesion_da_el_paso_y_el_boton(self):
        e = engines.ClaudeCode()._why(1, "Not logged in · Please run /login")
        r = explicar.explicar(e)
        self.assertEqual(r["frase"], "Claude Code no tiene la sesión iniciada: en PowerShell escribe claude, entra "
                                     "con tu cuenta y vuelve a probar.")
        self.assertEqual(r["accion"]["tipo"], "abrir_powershell")
        self.assertIn("sin sesión iniciada", r["tecnico"])  # el original queda para el log y el diagnóstico

    def test_claude_no_instalado_lleva_al_asistente(self):
        self.assertIn("No encuentro Claude Code en este PC: instálalo", self.frase(
            "No encuentro Claude Code en este PC. Sigue el paso a paso de Ajustes y pulsa «Comprobar»."))
        self.assertEqual(self.accion("No encuentro Claude Code en este PC. Haz los pasos 1 y 2"), "asistente")

    def test_claude_limite_del_plan_con_la_hora(self):
        for txt in ("Claude Code ha fallado (código 1): You've hit your session limit · resets 7pm (Europe/Madrid)",
                    "Claude Code ha fallado (código 1): You've reached your Opus limit. resets Nov 9 at 6pm"):
            f = self.frase(txt)
            self.assertTrue(f.startswith("Tu plan de Claude ha llegado a su límite de uso (se libera: "), f)
            self.assertIn("se sigue transcribiendo", f)
        self.assertIn("límite de uso:", self.frase("Claude Code ha fallado: You're out of usage credits."))

    def test_claude_no_contesta_y_otros_fallos(self):
        self.assertEqual(self.frase("Claude Code no ha contestado en 120 s"),
                         "Claude no ha contestado en 120 s: mira si tienes internet y prueba otra vez.")
        f = self.frase("Claude Code ha fallado (código 2): boom\nmás detalle")
        self.assertEqual(f, "Claude Code ha dado un error (boom): si se repite, copia el diagnóstico en "
                            "Ajustes → Ayuda.")
        self.assertEqual(self.accion("Claude Code ha fallado (código 2): boom"), "diagnostico")

    def test_falta_una_pieza_de_la_instalacion(self):
        for err in (ModuleNotFoundError("No module named 'av'"), "No module named 'faster_whisper'",
                    ImportError("DLL load failed while importing _ctranslate2")):
            self.assertIn("vuelve a ejecutar INSTALAR.bat (tus grabaciones no se tocan)", self.frase(err))

    def test_proxy_que_contesta_con_html(self):
        txt = "El servicio respondió sin JSON (¿un proxy o un portal de acceso de la red?): <html>"
        self.assertEqual(self.frase(txt, proveedor="Groq"), "La red de tu empresa contesta en lugar de Groq: prueba "
                                                            "fuera de la VPN o pide a IT que lo permita.")
        self.assertIn("en lugar del servicio", self.frase(txt))

    def test_las_frases_de_engines_se_quedan_y_ganan_boton(self):
        """connection_error y cloud_http_error (fix-transcripcion) ya dicen qué hacer: no se reescriben."""
        class R:
            status_code = 401
            text = ""

            def json(self):
                return {"error": {"message": "Invalid API Key"}}
        f401 = engines.cloud_http_error(R(), {"provider": "groq"})
        r = explicar.explicar(f401)
        self.assertEqual(r["frase"], f401)
        self.assertEqual(r["accion"]["tipo"], "abrir_url")
        self.assertEqual(r["accion"]["url"], "https://console.groq.com/keys")
        self.assertEqual(r["accion"]["etiqueta"], "Abrir console.groq.com")
        R.status_code = 503
        self.assertEqual(self.accion(engines.cloud_http_error(R(), {"provider": "groq"})), "reintentar")
        R.status_code = 404
        self.assertEqual(self.accion(engines.cloud_http_error(R(), {"provider": "groq"}, model="x")), "avanzado")

        class SSLError(Exception):
            pass
        f = engines.connection_error(SSLError("certificate verify failed"), "huggingface.co", "la descarga")
        self.assertEqual(self.frase(f), f)
        self.assertEqual(self.accion(f), "reintentar_descarga")
        f = engines.connection_error(SSLError("certificate verify failed"), "api.groq.com")
        self.assertEqual(self.accion(f), "reintentar")
        self.assertEqual(self.accion("No hay espacio en el disco para el modelo medium: hacen falta 1.531 MB"),
                         "reintentar_descarga")

    def test_vacio_y_desconocido(self):
        self.assertEqual(explicar.explicar(None), {"frase": "", "accion": None, "tecnico": ""})
        self.assertEqual(explicar.explicar("algo raro"), {"frase": "algo raro", "accion": None, "tecnico": "algo raro"})

    def test_job_publico_lleva_el_aviso(self):
        j = server.Job("a.wav", "a.wav", "rapido", "es")
        self.assertNotIn("aviso", j.public())
        j.error = "No module named 'av'"
        self.assertIn("INSTALAR.bat", j.public()["aviso"]["frase"])
        self.assertEqual(j.public()["error"], "No module named 'av'")


@unittest.skipUnless(NODE, "node no está instalado")
class ConfigurarJsTests(unittest.TestCase):
    """Las funciones puras de configurar.js con node (sin navegador)."""

    def run_js(self, body):
        with open(os.path.join(APP, "static", "configurar.js"), encoding="utf-8") as f:
            src = f.read()
        # solo la parte pura: hasta el marcador (lo de debajo toca el DOM)
        src = src.split("/* ---8<--- DOM")[0]
        code = ("const S = { config: null, presetsById: {}, modelos: {} };\n"
                "const esc = s => String(s ?? '');\n" + src + "\n" + body)
        r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    PRESETS = ("{rapido: {id: 'rapido', engine: 'local', model: 'small', label: 'Rápido'}, "
               "medium: {id: 'medium', engine: 'local', model: 'medium', label: 'Clásico'}, "
               "nube: {id: 'nube', engine: 'cloud', label: 'Nube'}}")

    def test_linea_del_modo_en_los_tres_modos(self):
        out = self.run_js(f"""
S.presetsById = {self.PRESETS};
const c = m => ({{procesado: m, agente: {{label: 'Claude Code'}}, providers: {{groq: {{label: 'Groq'}}}},
                  cloud: {{provider: 'groq'}}}});
S.config = c('agente'); const a = modoLinea('medium');
S.config = c('local'); const l = modoLinea('medium');
S.config = c('api'); const n = modoLinea('nube');
console.log(JSON.stringify([a, l, n, modoNombre('agente'), modeloNombre('medium'), modeloNombre('small')]));""")
        self.assertEqual(out, [
            "Se transcribe en este PC (Normal) y al terminar la resume Claude con tu cuenta",
            "Se transcribe en este PC (Normal). Sin resumen con IA",
            "Se transcribe con Groq y al terminar la resume Groq",
            "Con tu cuenta de Claude", "Normal", "Rápido"])

    def test_preparando_con_porcentaje_y_sin_al_dia(self):
        out = self.run_js(f"""
S.presetsById = {self.PRESETS};
S.modelos = {{medium: {{model: 'medium', downloading: true, done_mb: 510, total_mb: 1531, frac: 0.333, eta_s: 180}}}};
const j = {{status: 'running', preset: 'medium', stage: 'Descargando el modelo medium (solo la primera vez)…'}};
const k = {{status: 'running', preset: 'medium', stage: 'Cargando el modelo medium…'}};
const x = {{status: 'running', preset: 'medium', stage: 'Transcribiendo…'}};
console.log(JSON.stringify([prepEstado(j), prepEstado(k), prepEstado(x), prepCola(j), prepTexto(prepEstado(j))]));""")
        dl, carga, nada, cola, texto = out
        self.assertEqual(dl["tipo"], "descarga")
        self.assertEqual(dl["pct"], 33)
        self.assertEqual(carga["tipo"], "carga")
        self.assertIsNone(nada)
        self.assertEqual(cola, "Preparando: descargando el modelo Normal · 33 %")
        self.assertEqual(texto, "Descargando el modelo Normal (1,5 GB, solo la primera vez) · 510 MB de 1,5 GB · "
                                "quedan unos 3 min. La grabación sigue y no se pierde nada: cuando acabe la descarga, "
                                "el texto empieza desde el principio.")


if __name__ == "__main__":
    unittest.main()
