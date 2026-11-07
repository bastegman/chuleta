# -*- coding: utf-8 -*-
"""Configuración en tres pasos (07/11/2026, Steve): errores en una frase (explicar.py), la línea del modo y
«Preparando…» de la interfaz (configurar.js, con node) y, más abajo, «Probar» (probar.py)."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from support import APP, REPO, bare_jobs, engines, server  # noqa: F401  (support prepara sys.path)
from test_perfil import settings_store  # noqa: E402  (ayudante, no tests)

import explicar  # noqa: E402
import probar  # noqa: E402

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
        self.assertEqual(self.accion(engines.cloud_http_error(R(), {"provider": "groq"}, model="x")), "procesado")

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


class AppJsTests(unittest.TestCase):
    def test_nada_tapa_la_funcion_api(self):
        """El 07/11/2026 un «const api = …» en openSettings() tapó la función api() en todo Ajustes («Aplicar ahora»,
        «Probar conexión», «Comprobar»… decían «api is not a function»)."""
        for f in ("app.js", "configurar.js"):  # (modelos.js tiene uno en modelosHtml(), que no llama a api())
            with open(os.path.join(APP, "static", f), encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotRegex(src, r"\b(const|let|var)\s+api\b", f)
            self.assertNotRegex(src, r"\(\s*api\s*[,)=]", f)  # ni como parámetro


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
        r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
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


class ProbarTests(unittest.TestCase):
    """probar.Prueba con dobles: el orden, los estados, el silencio cuando no hay voz y el primer fallo."""

    def correr(self, modo, prov=None, **over):
        llamadas = []

        def cuenta(report, cancelled):
            llamadas.append("cuenta")

        def modelos(report, cancelled):
            llamadas.append("modelos")
            report("Descargando · 50 %")

        def grabar(segundos, report, cancelled):
            llamadas.append(("grabar", segundos))
            return "voz.wav", True, None

        def silencio():
            llamadas.append("silencio")
            return "sil.wav"

        def transcribir(wav, report, cancelled):
            llamadas.append(("transcribir", wav))
            return "hola esto es una prueba"

        def resumir(texto, report, cancelled):
            llamadas.append(("resumir", texto))
            return "Un saludo de prueba."
        hacer = {"cuenta": cuenta, "modelos": modelos, "grabar": grabar, "silencio": silencio,
                 "transcribir": transcribir, "resumir": resumir}
        hacer.update(over)
        nombres = {"transcribe": f"con {prov}" if prov else "en este PC",
                   "resume": prov or "Claude (tu cuenta)", "proveedor": prov}
        fin = []
        p = probar.Prueba(modo, hacer, nombres, al_terminar=fin.append)
        p.start()
        p.join(10)
        self.assertEqual(fin, [p])
        return p.public(), llamadas

    def test_con_claude_todo_bien(self):
        r, ll = self.correr("agente")
        # con Claude, la cuenta primero: sin sesión se sabe antes de bajar 2 GB
        self.assertEqual([x["id"] for x in r["pasos"]], ["cuenta", "modelos", "micro", "transcribir", "resumir"])
        self.assertEqual(ll[0], "cuenta")
        self.assertEqual([x["estado"] for x in r["pasos"]], ["ok"] * 5)
        self.assertEqual([x["texto"] for x in r["pasos"]], [
            "Claude Code con tu cuenta", "Modelos de transcripción en este PC", "Grabar 3 s del micro",
            "Transcribir en este PC", "Resumir con Claude (tu cuenta)"])
        self.assertTrue(r["listo"] and r["terminada"])
        self.assertIsNone(r["fallo"])
        self.assertEqual(r["resultado"], "Listo. Tus reuniones se transcriben en este PC y las resume Claude con tu "
                                         "cuenta.")
        self.assertEqual(r["pasos"][3]["detalle"], "«hola esto es una prueba»")
        self.assertEqual(r["pasos"][4]["detalle"], "Claude ha contestado: «Un saludo de prueba.»")
        self.assertIn(("resumir", "hola esto es una prueba"), ll)  # resume lo que se ha dicho
        self.assertNotIn("silencio", ll)

    def test_sin_voz_prueba_la_cadena_con_silencio_y_lo_dice(self):
        r, ll = self.correr("local", grabar=lambda s, rep, c: ("voz.wav", False, None),
                            transcribir=lambda w, rep, c: "")
        self.assertEqual([x["id"] for x in r["pasos"]], ["modelos", "micro", "transcribir"])
        self.assertEqual(r["pasos"][1]["estado"], "aviso")
        self.assertEqual(r["pasos"][1]["detalle"], "No se ha oído voz: sigo la prueba sin voz (1 s de silencio).")
        self.assertEqual(r["pasos"][2]["detalle"], "Funciona (con silencio no sale texto)")
        self.assertIn("silencio", ll)
        self.assertTrue(r["listo"])
        # «Listo», pero diciendo que no se oyó la voz (revisión: un «Listo» a secas con el micro roto engaña)
        self.assertEqual(r["resultado"], "Listo. Tus reuniones se transcriben en este PC. No se resumen con IA."
                                         + probar.OJO_MICRO)

    def test_micro_que_no_arranca(self):
        vistos = []
        r, ll = self.correr("api", prov="Groq", grabar=lambda s, rep, c: (None, False, "sin micrófono"),
                            transcribir=lambda w, rep, c: vistos.append(w) or "")
        self.assertEqual([x["id"] for x in r["pasos"]], ["micro", "transcribir", "resumir"])
        self.assertEqual(r["pasos"][0]["detalle"], "No he podido usar el micro: sigo la prueba sin voz (1 s de "
                                                   "silencio).")  # el error crudo, solo al log
        self.assertEqual(vistos, ["sil.wav"])
        self.assertIn(("resumir", probar.TEXTO_MUESTRA), ll)  # sin voz, se resume la frase de muestra
        self.assertEqual(r["pasos"][2]["texto"], "Resumir con Groq")
        self.assertEqual(r["pasos"][2]["detalle"], "Groq ha contestado: «Un saludo de prueba.»")
        self.assertEqual(r["resultado"],
                         "Listo. Tus reuniones se transcriben y se resumen con Groq." + probar.OJO_MICRO)

    def test_voz_sin_texto_avisa_del_idioma(self):
        r, _ = self.correr("local", transcribir=lambda w, rep, c: "  ")
        self.assertEqual(r["pasos"][2]["estado"], "aviso")
        self.assertIn("revisa el idioma en Ajustes → Grabar", r["pasos"][2]["detalle"])

    def test_el_primer_fallo_para_con_su_frase_y_su_boton(self):
        def roto(w, rep, c):
            raise engines.EngineError("No module named 'av'")
        r, ll = self.correr("agente", transcribir=roto)
        self.assertEqual([x["estado"] for x in r["pasos"]], ["ok", "ok", "ok", "error", "espera"])
        self.assertFalse(r["listo"])
        self.assertEqual(r["fallo"]["paso"], "transcribir")
        self.assertIn("vuelve a ejecutar INSTALAR.bat", r["fallo"]["frase"])
        self.assertEqual(r["fallo"]["accion"]["tipo"], "diagnostico")
        self.assertFalse([x for x in ll if isinstance(x, tuple) and x[0] == "resumir"])

        def sin_sesion(t, rep, c):
            raise engines.EngineError(engines.ClaudeCode()._why(1, "Not logged in · Please run /login"))
        r, _ = self.correr("agente", resumir=sin_sesion)
        self.assertEqual(r["fallo"]["accion"]["tipo"], "abrir_powershell")
        self.assertEqual(r["pasos"][4]["detalle"], r["fallo"]["frase"])

    def test_cancelar(self):
        empezado = threading.Event()

        def lento(report, cancelled):
            empezado.set()
            while not cancelled():
                time.sleep(0.02)
            raise probar.Cancelada()
        p = probar.Prueba("local", {"modelos": lento}, {})
        p.start()
        self.assertTrue(empezado.wait(5))
        r = p.public()
        self.assertEqual(r["pasos"][0]["estado"], "en_curso")
        p.cancelar = True
        p.join(5)
        r = p.public()
        self.assertEqual(r["fallo"]["frase"], "Prueba cancelada.")
        self.assertFalse(r["listo"])


class ProbarServidorTests(unittest.TestCase):
    """/api/probar: la configuración candidata no se guarda; la del asistente queda como última prueba al guardar."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-probar-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def app(self, **st):
        a = server.App.__new__(server.App)
        st.setdefault("cloud", dict(server.DEFAULT_SETTINGS["cloud"]))
        a.settings = settings_store(tempfile.mkdtemp(dir=self.tmp), **st)
        a.jobs = bare_jobs([])
        a.jobs.settings = a.settings
        a.jobs.cloud = types.SimpleNamespace(keys=types.SimpleNamespace(public=lambda *x, **k: None))
        a.hotkey = types.SimpleNamespace(ok=True, error=None)
        a.rec = a.monitor = None
        a.rec_lock = threading.Lock()
        return a

    def test_candidata_sin_guardar_y_la_clave_enmascarada_no_cuenta(self):
        a = self.app(procesado="local", cloud=dict(server.DEFAULT_SETTINGS["cloud"], api_key="gsk_guardada"))
        st, cand = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "groq",
                                                                             "api_key": "gsk_…ada"}}})
        self.assertTrue(cand)
        self.assertEqual((st["procesado"], st["cloud"]["api_key"]), ("api", "gsk_guardada"))
        st, _ = a._candidata({"candidata": {"procesado": "agente", "agente": {"tipo": "claude", "modelo": "opus"},
                                            "cloud": {"api_key": "gsk_nueva"}}})
        self.assertEqual((st["agente"]["modelo"], st["cloud"]["api_key"]), ("opus", "gsk_nueva"))
        self.assertEqual(a.settings.get()["procesado"], "local")  # nada guardado
        self.assertEqual(a.settings.get()["cloud"]["api_key"], "gsk_guardada")
        st, cand = a._candidata({})
        self.assertFalse(cand)

    def test_motores_de_la_prueba(self):
        a = self.app()
        directo, modelos = a._prueba_motores({"live_preset": "nube", "preset": "nube"})
        self.assertEqual((directo["id"], modelos), ("medium", ["medium", "small"]))
        directo, modelos = a._prueba_motores({"live_preset": "rapido", "preset": "rapido"})
        self.assertEqual((directo["id"], modelos), ("rapido", ["small"]))

    def test_empezar_estado_y_ultima_prueba(self):
        a = self.app(procesado="local")
        hecho = {"cuenta": lambda rep, c: None, "modelos": lambda rep, c: None,
                 "grabar": lambda s, rep, c: (None, False, "sin micro"),
                 "silencio": lambda: "s.wav", "transcribir": lambda w, rep, c: "", "resumir": lambda t, rep, c: "ok"}
        with mock.patch.object(server.App, "_prueba_hacer", return_value=(hecho, {"proveedor": None})), \
                mock.patch.object(server, "log"):
            pid = a.probar_empezar({"candidata": {"procesado": "agente"}})["id"]
            a.pruebas[pid].join(5)
            r = a.probar_estado({"id": pid})
            self.assertTrue(r["listo"] and r["candidata"])
            self.assertEqual(r["modo"], "agente")
            self.assertIsNone(a.settings.get().get("ultima_prueba"))  # candidata: no se guarda nada
            self.assertEqual(a.settings.get()["procesado"], "local")
            # el asistente guarda con el id de la prueba: queda como última prueba de ese modo
            with mock.patch.object(a, "_descargar_modelo_del_directo", return_value=None), \
                    mock.patch.object(engines.ClaudeCode, "find", return_value="/x/claude"):
                c = a.save_settings({"procesado": "agente", "prueba": pid})
            self.assertEqual(c["procesado"], "agente")
            self.assertTrue(c["ultima_prueba"]["ok"])
            self.assertEqual(c["ultima_prueba"]["modo"], "agente")
            # la de lo guardado (sin candidata) se guarda sola al acabar
            pid2 = a.probar_empezar({})["id"]
            a.pruebas[pid2].join(5)
            self.assertEqual(a.settings.get()["ultima_prueba"]["modo"], "agente")
            with mock.patch.object(engines.ClaudeCode, "find", return_value=None):
                c = a.save_settings({"procesado": "local"})
            self.assertIsNone(c["ultima_prueba"])  # es de otro modo: no se enseña
        with self.assertRaises(server.ApiError):
            a.probar_estado({"id": "nada"})

    def test_la_prueba_solo_vale_para_lo_que_se_probo_y_una_nueva_cancela_la_anterior(self):
        a = self.app(procesado="api", cloud=dict(server.DEFAULT_SETTINGS["cloud"], api_key="gsk_vieja"))
        sigue = threading.Event()

        def lento(s, rep, c):
            while not c() and not sigue.is_set():
                time.sleep(0.02)
            return None, False, None
        hecho = {"grabar": lento, "silencio": lambda: "s.wav", "transcribir": lambda w, rep, c: "",
                 "resumir": lambda t, rep, c: "ok"}
        with mock.patch.object(server.App, "_prueba_hacer", return_value=(hecho, {"proveedor": "Groq"})), \
                mock.patch.object(server, "log"):
            p1 = a.probar_empezar({"candidata": {"procesado": "api", "cloud": {"api_key": "gsk_nueva"}}})["id"]
            p2 = a.probar_empezar({"candidata": {"procesado": "api", "cloud": {"api_key": "gsk_nueva"}}})["id"]
            self.assertNotEqual(p1, p2)
            sigue.set()  # la segunda ya puede grabar (la primera, cancelada, ha soltado el micro)
            a.pruebas[p2].join(5)
            self.assertEqual(a.probar_estado({"id": p1})["fallo"]["frase"], "Prueba cancelada.")
            self.assertTrue(a.probar_estado({"id": p2})["listo"])
            # se guarda con OTRA clave: la prueba no vale para eso
            c = a.save_settings({"procesado": "api", "cloud": {"api_key": "gsk_otra"}, "prueba": p2})
            self.assertIsNone(c["ultima_prueba"])
            c = a.save_settings({"procesado": "api", "cloud": {"api_key": "gsk_nueva"}, "prueba": p2})
            self.assertTrue(c["ultima_prueba"]["ok"])

    def test_grabando_no_se_prueba(self):
        a = self.app()
        a.rec = types.SimpleNamespace(alive=lambda: True)
        with self.assertRaises(server.ApiError) as c:
            a.probar_empezar({})
        self.assertIn("Estás grabando", c.exception.msg)

    def test_abrir_powershell_fuera_de_windows(self):
        if os.name == "nt":
            self.skipTest("en Windows abriría una ventana")
        with self.assertRaises(server.ApiError):
            self.app().abrir_powershell({})


if __name__ == "__main__":
    unittest.main()
