# -*- coding: utf-8 -*-
"""E2E de «Dónde se procesan tus reuniones» con lo que hace Bernardo (07/11/2026, msgs 1169-1170): elegir el modo en
Ajustes, que el Whisper del PC se descargue con su progreso y cargue, transcribir una grabación en directo y un WAV
corto, y comprobar que lo que la interfaz enseña junto a «Modelo» cuadra con el modo elegido.

- «Solo en este PC»: Whisper medium de verdad (1,5 GB desde Hugging Face, con su progreso en MB y %).
- «Con tu cuenta de Claude»: transcribe el mismo Whisper; «Comprobar» y el resumen, con un Claude Code falso en el PATH.
- «API externa» (Groq): contra un Groq falso en 127.0.0.1 que imita su API (cabeceras de cupo incluidas). Nunca una
  clave real en la CI.

La voz se sintetiza en el momento (SAPI en Windows, `say` en macOS): no hay audio en el repo. Pesado (descarga 1,5 GB,
faster-whisper, Playwright con Chromium): solo con CHULETA_E2E_PROCESADO=1, que pone `e2e-procesado.yml`. Deja
capturas y server.log en NOTAS_ARTEFACTOS si existe."""
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import REPO

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ACTIVO = os.environ.get("CHULETA_E2E_PROCESADO") == "1"
FRASE = ("Hello, this is a test of the meeting notes application. We are checking that the transcription works on "
         "this computer.")
GROQ_TEXTO = " This is the fake Groq transcription."
CLAVE = "gsk_prueba_e2e_" + "0" * 24  # falsa: solo la ve el Groq de 127.0.0.1


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def voz(path, text):
    """Un WAV 16 kHz mono 16 bits con `text` dicho por la voz del sistema."""
    if sys.platform == "win32":
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
              "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
              "[System.Speech.AudioFormat.AudioChannel]::Mono);"
              f" $s.SetOutputToWaveFile('{path}', $f); $s.Speak('{text}'); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=120)
    elif shutil.which("say") and shutil.which("afconvert"):
        aiff = path + ".aiff"
        voces = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        v = next((n for n in ("Samantha", "Alex", "Daniel", "Karen") if re.search(rf"^{n}\s", voces, re.M)), None)
        subprocess.run(["say", "-o", aiff] + (["-v", v] if v else []) + [text], check=True, timeout=120)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, path], check=True,
                       timeout=60)
        os.remove(aiff)
    else:
        raise unittest.SkipTest("sin voz del sistema para sintetizar el audio de prueba")


def pcm_de(path):
    with wave.open(path, "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), path
        return w.readframes(w.getnframes())


class GrabacionEnCurso:
    """Como OBS o la grabadora: WAV con la cabecera abierta (0xFFFFFFFF) que crece a trozos de 0,5 s, al ritmo real,
    y sigue abierto (con silencio) hasta cerrar(): entonces se escriben sus tamaños, como al parar de grabar. Así el
    vigilante de la app (cada 3 s) la ve «grabándose» y arranca el directo solo."""

    def __init__(self, path, pcm):
        self.path, self.pcm, self.fin = path, pcm, threading.Event()
        self.hilo = threading.Thread(target=self._run, daemon=True)
        self.hilo.start()

    def _run(self):
        with open(self.path, "wb") as f:
            fmt = struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)  # PCM 16 kHz mono 16 bits
            abierta = struct.pack("<I", 0xFFFFFFFF)
            f.write(b"RIFF" + abierta + b"WAVEfmt " + fmt + b"data" + abierta)
            f.flush()
            n, datos = 0, self.pcm + bytes(32000)  # la voz y 1 s de silencio
            for i in range(0, len(datos), 16000):
                f.write(datos[i:i + 16000])
                f.flush()
                n += len(datos[i:i + 16000])
                time.sleep(0.5)
            tope = time.time() + 60
            while not self.fin.is_set() and time.time() < tope:  # silencio hasta que la prueba diga «parar»
                f.write(bytes(16000))
                f.flush()
                n += 16000
                time.sleep(0.5)
            f.seek(4)
            f.write(struct.pack("<I", 36 + n))
            f.seek(40)
            f.write(struct.pack("<I", n))

    def cerrar(self):
        self.fin.set()
        self.hilo.join(30)


class GroqFalso:
    """Imita /openai/v1/audio/transcriptions y /chat/completions de Groq: verbose_json, cabeceras de cupo y 401 si la
    clave no es la de la prueba. Apunta cada petición (ruta, modelo, clave)."""

    def __init__(self):
        self.peticiones = []
        g = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                auth = self.headers.get("Authorization") or ""
                m = re.search(rb'name="model"\r\n\r\n([^\r]*)', body)
                modelo = m.group(1).decode() if m else None
                if self.path.endswith("/chat/completions"):
                    try:
                        modelo = json.loads(body).get("model")
                    except ValueError:
                        pass
                g.peticiones.append((self.path, modelo, auth == f"Bearer {CLAVE}"))
                if auth != f"Bearer {CLAVE}":
                    out, code = {"error": {"message": "Invalid API Key", "type": "invalid_request_error"}}, 401
                elif self.path.endswith("/audio/transcriptions"):
                    out, code = {"text": GROQ_TEXTO, "language": "english", "duration": 3.0,
                                 "segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": GROQ_TEXTO,
                                               "avg_logprob": -0.1, "no_speech_prob": 0.01,
                                               "compression_ratio": 1.2}]}, 200
                else:
                    out, code = {"choices": [{"message": {"content": "## Resumen\nResumen de prueba de Groq."}}],
                                 "usage": {"total_tokens": 10}}, 200
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("x-ratelimit-remaining-requests", "1999")
                self.send_header("x-ratelimit-limit-requests", "2000")
                self.send_header("x-ratelimit-reset-requests", "43.2s")
                self.end_headers()
                self.wfile.write(data)

        class Srv(ThreadingHTTPServer):
            daemon_threads = True

        self.httpd = Srv(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/openai/v1"


CLAUDE_FALSO = r'''# Claude Code falso para la E2E: --version y `-p` con la entrada estandar
import json, sys
if "--version" in sys.argv:
    print("2.1.999 (Claude Code)")
    sys.exit(0)
entrada = sys.stdin.read()
res = "OK" if "la palabra OK" in entrada else "## Resumen\nResumen de prueba hecho por un Claude Code falso."
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": res,
                  "modelUsage": {"claude-sonnet-prueba": {"inputTokens": 10}}}))
'''


@unittest.skipUnless(ACTIVO, "E2E pesado: solo con CHULETA_E2E_PROCESADO=1 (e2e-procesado.yml)")
@unittest.skipUnless(sync_playwright, "playwright no instalado")
class TresModosE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuleta-e2e-")
        cls.art = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(cls.tmp, "artefactos")
        os.makedirs(cls.art, exist_ok=True)
        cls.root = os.path.join(cls.tmp, "Chuleta")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(cls.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(cls.root, "Pruebas"))
        cls.voz = os.path.join(cls.tmp, "voz.wav")
        voz(cls.voz, FRASE)
        cls.pcm = pcm_de(cls.voz)
        cls.wav = "Pruebas/" + time.strftime("%Y-%m-%d %H-%M-%S") + " - Prueba corta.wav"
        shutil.copy(cls.voz, os.path.join(cls.root, cls.wav))
        cls.groq = GroqFalso()
        bin_dir = os.path.join(cls.tmp, "bin")
        os.makedirs(bin_dir)
        script = os.path.join(bin_dir, "claude_falso.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(CLAUDE_FALSO)
        if sys.platform == "win32":
            with open(os.path.join(bin_dir, "claude.cmd"), "w", encoding="ascii") as f:
                f.write(f'@"{sys.executable}" "{script}" %*\r\n')
        else:
            p = os.path.join(bin_dir, "claude")
            with open(p, "w", encoding="utf-8") as f:
                f.write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
            os.chmod(p, 0o755)
        cls.datos = os.path.join(cls.tmp, "datos")
        cls.port = puerto_libre()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", CHULETA_PRUEBAS_URL_GROQ=cls.groq.base,
                   PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""),
                   HF_HUB_CACHE=os.path.join(cls.tmp, "hf-vacia"))  # nada descargado de antes: se baja de verdad
        cls.salida = open(os.path.join(cls.tmp, "salida.log"), "w", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(cls.root, "_app", "server.py"), "--port",
                                     str(cls.port), "--data", cls.datos, "--no-browser"],
                                    cwd=cls.root, stdout=cls.salida, stderr=subprocess.STDOUT, env=env)
        cls.base = f"http://127.0.0.1:{cls.port}"
        token_file = os.path.join(cls.datos, "sesion.token")
        fin = time.time() + 90
        cls.token = None
        while time.time() < fin and cls.proc.poll() is None:
            if os.path.isfile(token_file):
                with open(token_file, encoding="utf-8") as f:
                    cls.token = f.read().strip()
                try:
                    req = urllib.request.Request(cls.base + "/api/ping", headers={"X-Chuleta-Token": cls.token})
                    if json.load(urllib.request.urlopen(req, timeout=2)).get("app") == "audios":
                        break
                except Exception:
                    pass
            time.sleep(0.3)
        else:
            cls.tearDownClass()
            raise AssertionError("el servidor de prueba no arrancó (ver salida.log)")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.ctx = cls.browser.new_context(bypass_csp=True, viewport={"width": 1366, "height": 900})
        cls.pg = cls.ctx.new_page()
        cls.errores = []
        cls.pg.on("pageerror", lambda e: cls.errores.append(f"pageerror: {e}"))
        cls.pg.goto(f"{cls.base}/?t={cls.token}", wait_until="networkidle")
        cls.pg.wait_for_selector("#main h1", timeout=20000)

    @classmethod
    def tearDownClass(cls):
        for attr in ("ctx", "browser"):
            try:
                getattr(cls, attr).close()
            except Exception:
                pass
        try:
            cls.pw.stop()
        except Exception:
            pass
        if getattr(cls, "proc", None) and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(15)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        try:
            cls.salida.close()
            for n in ("server.log", "worker-live.log", "worker-batch.log"):
                if os.path.isfile(os.path.join(cls.datos, n)):
                    shutil.copy(os.path.join(cls.datos, n), os.path.join(cls.art, "e2e-procesado-" + n))
            shutil.copy(os.path.join(cls.tmp, "salida.log"), os.path.join(cls.art, "e2e-procesado-salida.log"))
        except Exception:
            pass
        try:
            cls.groq.httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # --- ayudantes
    def api(self, path, body=None):
        return self.pg.evaluate("([p, b]) => api(p, b === null ? undefined : b)", [path, body])

    def foto(self, nombre):
        try:
            self.pg.screenshot(path=os.path.join(self.art, f"e2e-procesado-{nombre}.png"), full_page=True)
        except Exception:
            pass

    def ajustes(self):
        if self.pg.evaluate("!document.querySelector('#modal').classList.contains('hidden')"):
            self.pg.keyboard.press("Escape")
            self.pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=10000)
        self.pg.click("#btnSettings")
        self.pg.wait_for_selector("#modal:not(.hidden) h3:has-text('Ajustes')", timeout=10000)
        # Ajustes abre en «Al grabar» desde el rediseño B2 (07/11/2026): lo de aquí está en Procesado
        self.pg.click(".aj-nav [data-sec='procesado']")
        self.pg.wait_for_selector("#modal input[name='proc'][value='local']", state="visible", timeout=10000)

    def elegir(self, modo):
        """Elige el modo en «Dónde se procesan tus reuniones»: elegirlo solo no cambia nada (se dice que falta
        aplicarlo) y «Aplicar ahora» lo aplica sin bajar hasta «Guardar»."""
        antes = self.api("/api/config")["procesado"]
        self.pg.check(f"#modal input[name='proc'][value='{modo}']")
        if antes != modo:
            self.pg.wait_for_selector("#procAplicar", timeout=10000)
            self.assertIn("aún no está aplicado", self.pg.inner_text("#procGuardado"))
            self.assertEqual(self.api("/api/config")["procesado"], antes)  # un clic de prueba no cambia nada
            self.pg.click("#procAplicar")
            self.pg.wait_for_function("document.querySelector('#procGuardado').innerText.includes('Aplicado')",
                                      timeout=20000)
        self.assertEqual(self.api("/api/config")["procesado"], modo)

    def abrir(self, rel):
        self.pg.evaluate("refreshTree(true)")
        self.pg.wait_for_function("p => S.audios.has(p)", arg=rel, timeout=30000)
        self.pg.evaluate("p => go('a', p)", rel)

    def texto(self, rel):
        segs = self.api("/api/transcript?path=" + urllib.request.quote(rel))["segments"]
        return " ".join(s["text"] for s in segs).lower()

    def esperar_texto(self, rel, palabra, tope):
        fin, t = time.time() + tope, ""
        while time.time() < fin:
            t = self.texto(rel)
            if palabra in t:
                return t
            time.sleep(1)
        self.fail(f"sin «{palabra}» en {tope} s; texto: {t!r}; trabajos: {self.api('/api/jobs')['jobs'][-3:]}")

    def directo(self, nombre):
        """Una grabación en curso que la app pasa sola a transcribir en directo (live_auto); devuelve su ruta y el
        texto que se ve junto a «Modelo» mientras transcribe, y las etapas que se han visto."""
        rel = "Pruebas/" + time.strftime("%Y-%m-%d %H-%M-%S") + f" - {nombre}.wav"
        grab = GrabacionEnCurso(os.path.join(self.root, rel), self.pcm)
        self.abrir(rel)
        # los modelos del directo van detrás de «Cambiar» (la línea del modo se ve; ellos, en el DOM)
        self.pg.wait_for_selector(".lh-label .motor", state="attached", timeout=30000)
        etapas, motor, j = set(), None, []
        fin = time.time() + 30
        while time.time() < fin:  # el directo arranca solo en ~3 s; se mira lo que enseña la cabecera
            j = [x for x in self.api("/api/jobs")["jobs"] if x["path"] == rel and x.get("live")]
            if j:
                motor = self.pg.text_content(".lh-label .motor")  # el texto, no su aspecto (.lh-label va en mayúsculas)
                etapas.add(j[-1]["stage"])
                break
            time.sleep(0.5)
        time.sleep(len(self.pcm) / 32000 + 1.5)  # que llegue toda la voz antes de parar
        grab.cerrar()
        self.assertTrue(j, "el directo no ha arrancado solo")
        return rel, motor, j[-1], etapas

    # --- los tres modos, en orden
    def test_1_solo_en_este_pc(self):
        pg = self.pg
        self.ajustes()
        self.elegir("api")    # partir de otro modo: así «Solo en este PC» es un cambio, como al elegirlo
        self.elegir("local")
        pg.wait_for_selector("#procModelos:not([hidden]) [data-model='medium']", timeout=15000)
        visto, pcts, fin = None, set(), time.time() + 900  # medium: 1.531 MB; progreso en MB y % hasta «descargado»
        while time.time() < fin:
            fila = pg.inner_text("#mdLista [data-model='medium']")
            m = re.search(r"(\d[\d.]*) de 1\.531 MB \((\d+) %\)", fila)
            if m:
                pcts.add(int(m.group(2)))
                if not visto or (len(pcts) == 2 and int(m.group(2)) > 0):
                    pg.locator("#procModelos").scroll_into_view_if_needed()
                    self.foto("1-descargando-medium")
                visto = fila
            if "✓ descargado" in fila:
                break
            if "✖" in fila:
                self.fail(f"la descarga de medium ha fallado: {fila}")
            time.sleep(0.2)
        self.assertIn("✓ descargado", pg.inner_text("#mdLista [data-model='medium']"))
        self.assertTrue(visto, "no se ha visto el progreso en MB y %")
        # progreso de verdad: el % avanza (antes, un «Descargando…» fijo hasta acabar)
        self.assertGreaterEqual(len(pcts), 2, sorted(pcts))
        self.assertGreater(max(pcts), 0, sorted(pcts))
        print(f"\n   progreso visto en Ajustes: {sorted(pcts)} % · último {visto.splitlines()[-1]!r}", flush=True)
        self.foto("1-ajustes-local")
        pg.keyboard.press("Escape")
        rel, motor, job, etapas = self.directo("Directo local")
        self.foto("1-directo-local")
        self.assertEqual(motor, "en este PC · Whisper medium")
        self.assertNotIn("groq", pg.text_content(".lh-label").lower())
        self.assertEqual((job["preset"], job["engine"]), ("medium", "live"))
        t = self.esperar_texto(rel, "test", 300)
        print(f"   directo local: {t!r}", flush=True)
        self.assertFalse([p for p in self.groq.peticiones], "en local no puede salir nada hacia Groq")
        self.idioma_en_pleno_directo_local()

    def idioma_en_pleno_directo_local(self):
        """Ítem 12 con el Whisper medium de verdad (msgs 1150-1151): con el directo en marcha y lo dicho ya escrito
        en inglés, la reunión pasa a «Solo español»: el aviso trae el estado y lo ya escrito se rehace en español (se
        relee ese audio y Whisper lo transcribe con el idioma nuevo), sin esperar al final de la grabación."""
        rel = "Pruebas/" + time.strftime("%Y-%m-%d %H-%M-%S") + " - Directo idioma.wav"
        grab = GrabacionEnCurso(os.path.join(self.root, rel), self.pcm)
        log = os.path.join(self.datos, "server.log")
        try:
            self.abrir(rel)
            antes = self.esperar_texto(rel, "test", 300)
            t0 = time.time()
            r = self.api("/api/notes", {"path": rel, "props": {"idioma": "es"}})
            info = (r.get("directo") or {}).get("idioma") or {}
            print(f"   directo local, idioma -> es: aviso {info}", flush=True)
            self.assertIn(info.get("estado"), ("al_dia", "poniendose"), r)
            self.assertGreater(info.get("rehace_s", 0), 0, r)
            fin, hecho = time.time() + 300, ""
            while time.time() < fin and not hecho:
                with open(log, encoding="utf-8", errors="replace") as f:
                    hecho = next((x for x in f.read().splitlines()
                                  if "rehecho en es lo último" in x
                                  or "no se ha podido rehacer lo último" in x), "")
                time.sleep(1)
            despues = self.texto(rel)
            print(f"   rehecho a los {time.time() - t0:.0f} s: {despues!a} (antes {antes!a}) | {hecho.strip()!a}",
                  flush=True)  # !a: la consola de la CI de Windows es cp1252
            self.assertIn("rehecho en es lo último", hecho)
            self.assertNotEqual(despues, antes)
        finally:
            grab.cerrar()

    def test_2_con_tu_cuenta_de_claude(self):
        pg = self.pg
        self.ajustes()
        self.elegir("agente")
        pg.click("#agCheck")
        pg.wait_for_function("document.querySelector('#agRes').innerText.includes('✓')", timeout=90000)
        self.foto("2-ajustes-claude")
        pg.keyboard.press("Escape")
        # la grabación corta, con el botón Transcribir y el motor «Clásico» (medium, ya descargado)
        self.abrir(self.wav)
        pg.wait_for_selector("[data-act='tx-go']", timeout=20000)
        pg.click("button.preset[data-preset='medium']")
        pg.click("[data-act='tx-go']")
        t = self.esperar_texto(self.wav, "test", 300)
        print(f"\n   cola con Claude (Whisper en el PC): {t!r}", flush=True)
        # resumen automático con el Claude Code falso (la reunión es de hoy)
        fin = time.time() + 120
        while time.time() < fin and not self.api("/api/summary?path=" + urllib.request.quote(self.wav)).get("exists"):
            time.sleep(1)
        s = self.api("/api/summary?path=" + urllib.request.quote(self.wav))
        self.assertTrue(s.get("exists"), s)
        self.assertIn("Claude Code falso", json.dumps(s, ensure_ascii=False))
        rel, motor, job, _ = self.directo("Directo con Claude")
        self.foto("2-directo-claude")
        self.assertEqual(motor, "en este PC · Whisper medium")  # Claude no transcribe: Whisper en el PC
        # la línea del modo (configurar.js, 07/11/2026) dice dónde se transcribe y quién resume
        self.assertIn("Se transcribe en este PC (Normal) y al terminar la resume Claude con tu cuenta",
                      pg.inner_text("#lHero [data-modo-linea]"))
        self.assertNotIn("groq", pg.text_content(".lh-label").lower())
        self.esperar_texto(rel, "test", 300)
        self.assertFalse([p for p in self.groq.peticiones], "con Claude no puede salir nada hacia Groq")

    def test_3_api_externa_groq(self):
        pg = self.pg
        self.ajustes()
        self.elegir("api")
        pg.select_option("#cProv", "groq")
        pg.fill("#cKey", CLAVE)
        pg.click("#cTest")
        pg.wait_for_function("document.querySelector('#cTestRes').innerText.includes('Funciona')", timeout=60000)
        self.foto("3-ajustes-groq")
        pg.click("#modal [data-m='ok']")
        pg.wait_for_function("document.querySelector('#modal').classList.contains('hidden')", timeout=10000)
        c = self.api("/api/config")
        self.assertEqual((c["procesado"], c["live_preset"], c["preset"]), ("api", "nube", "nube"))
        rel, motor, job, _ = self.directo("Directo Groq")
        self.foto("3-directo-groq")
        self.assertEqual(motor, "en la nube · Groq")
        self.assertEqual(job["preset"], "nube")
        t = self.esperar_texto(rel, "fake groq", 120)
        print(f"\n   directo en Groq (falso): {t!r}", flush=True)
        modelos = {m for p, m, ok in self.groq.peticiones if p.endswith("/audio/transcriptions") and ok}
        self.assertIn("whisper-large-v3-turbo", modelos)  # el directo, con el modelo rápido
        self.assertTrue(all(ok for _, _, ok in self.groq.peticiones), self.groq.peticiones)

    def test_4_sin_errores_de_javascript(self):
        self.assertEqual(self.errores, [])


if __name__ == "__main__":
    unittest.main()
