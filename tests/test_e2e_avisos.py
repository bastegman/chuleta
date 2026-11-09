# -*- coding: utf-8 -*-
"""E2E de los avisos de la franja del directo (B6, puntos 1-3; msg 1378) con la app de verdad: el servidor como lo
arranca el acceso directo, la grabadora real con la tarjeta de sonido falsa de tests/fake_soundcard (el micro calla a
los 6 s: FAKESC_MIC_MUDO), el directo en un Groq falso en 127.0.0.1 y la interfaz en Chromium (Playwright):

1. el micro se calla: la franja dice «El micro no recibe sonido desde hace …» (con el minuto acortado a 4 s,
   CHULETA_PRUEBAS_AVISOS) y la grabación sigue;
2. lo último suena a portugués con la reunión en «Solo español» (el Groq falso responde language «portuguese»): la
   franja lo propone, «Cambiar» pasa la reunión a portugués al momento (las peticiones siguientes van con pt) y el
   aviso se va; en otra grabación, «No» lo quita y no vuelve;
3. la llamada de Teams termina (CHULETA_PRUEBAS_TEAMS: el registro y la ventana de Teams, de mentira): «La llamada
   ha terminado: paro en N s · No terminar»; «No terminar» la deja seguir; otra llamada que termina, sin
   tocar nada, para la grabación como «Parar» (el WAV queda cerrado y el directo termina) y lo dice.

Pesado (~1,5 min): solo con CHULETA_E2E_CARPETAS=1, que pone ui-smoke.yml (Ubuntu y Windows). Deja capturas y
server.log en NOTAS_ARTEFACTOS si existe."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import REPO

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from test_e2e_procesado import CLAVE, puerto_libre  # nunca una clave real en la CI

ACTIVO = os.environ.get("CHULETA_E2E_CARPETAS") == "1"
FAKE = os.path.join(REPO, "tests", "fake_soundcard")
NOMBRES = {"pt": "portuguese", "en": "english", "es": "spanish"}
# el minuto, 4 s; confirmar el fin, 1 s; «paro en 8 s»; llamadas desde 3 s que cubran el 30 % (la regla del 60 % y los
# 2 min, con sus casos, está en test_directo_avisos); 6 s de voz para proponer el idioma
TIEMPOS = {"AVISO_S": 4, "CONFIRMA_S": 1, "PARA_EN_S": 8, "CUBRE": 0.3, "CUBRE_MIN_S": 3, "SEGUIDOS_VOZ_S": 6}


class GroqOye:
    """Groq falso: /audio/transcriptions devuelve «Texto en <idioma pedido> n.º k» y, en language, `oye` (lo que
    «suena», si se fija) o el idioma pedido (lo que hace una API que no detecta con el idioma fijado)."""

    def __init__(self):
        self.peticiones, self.n, self.oye = [], 0, None
        g = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if (self.headers.get("Authorization") or "") != f"Bearer {CLAVE}":
                    out, code = {"error": {"message": "Invalid API Key"}}, 401
                elif self.path.endswith("/audio/transcriptions"):
                    m = re.search(rb'name="language"\r\n\r\n([^\r]*)', body)
                    lang = m.group(1).decode() if m else ""
                    g.n += 1
                    g.peticiones.append(("tx", lang, time.time()))
                    texto = f" Texto en {lang or 'auto'} numero {g.n}."
                    out, code = {"text": texto, "language": g.oye or NOMBRES.get(lang, "english"), "duration": 3.0,
                                 "segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": texto, "avg_logprob": -0.1,
                                               "no_speech_prob": 0.01, "compression_ratio": 1.2}]}, 200
                else:
                    out, code = {"choices": [{"message": {"content": "## Resumen\nPrueba."}}],
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


@unittest.skipUnless(ACTIVO, "E2E pesado: solo con CHULETA_E2E_CARPETAS=1 (ui-smoke.yml)")
@unittest.skipUnless(sync_playwright, "playwright no instalado")
class AvisosDirectoE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tenca-e2e-avisos-")
        cls.art = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(cls.tmp, "artefactos")
        os.makedirs(cls.art, exist_ok=True)
        cls.root = os.path.join(cls.tmp, "Tenca")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(cls.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(cls.root, "Alfa"))
        cls.groq = GroqOye()
        cls.teams = os.path.join(cls.tmp, "teams.json")
        cls.poner_teams("libre", False)
        cls.datos = os.path.join(cls.tmp, "datos")
        cls.port = puerto_libre()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=FAKE, CHULETA_PRUEBAS_URL_GROQ=cls.groq.base,
                   HF_HUB_CACHE=os.path.join(cls.tmp, "hf-vacia"), FAKESC_MIC_MUDO="6",
                   CHULETA_PRUEBAS_AVISOS=json.dumps(TIEMPOS), CHULETA_PRUEBAS_TEAMS=cls.teams)
        cls.salida = open(os.path.join(cls.tmp, "salida.log"), "w", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(cls.root, "_app", "server.py"), "--port",
                                     str(cls.port), "--data", cls.datos, "--no-browser"],
                                    cwd=cls.root, stdout=cls.salida, stderr=subprocess.STDOUT, env=env)
        cls.base = f"http://127.0.0.1:{cls.port}"
        token_file = os.path.join(cls.datos, "sesion.token")
        fin, cls.token = time.time() + 90, None
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
        cls.ctx = cls.browser.new_context(bypass_csp=True, viewport={"width": 1440, "height": 900})
        cls.pg = cls.ctx.new_page()
        cls.errores = []
        cls.pg.on("pageerror", lambda e: cls.errores.append(f"pageerror: {e}"))
        cls.pg.goto(f"{cls.base}/?t={cls.token}", wait_until="networkidle")
        cls.pg.wait_for_selector("#main h1", timeout=20000)
        cls.pg.evaluate("""() => { window.__toasts = [];
            new MutationObserver(() => document.querySelectorAll('#toasts .toast')
                .forEach(t => window.__toasts.push(t.innerText)))
                .observe(document.querySelector('#toasts'), {childList: true, subtree: true}); }""")

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
            for n in ("server.log", "recorder.log"):
                if os.path.isfile(os.path.join(cls.datos, n)):
                    shutil.copy(os.path.join(cls.datos, n), os.path.join(cls.art, "e2e-avisos-" + n))
        except Exception:
            pass
        try:
            cls.groq.httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def poner_teams(cls, mic, ventana):
        tmp = cls.teams + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"mic": mic, "ventana": ventana}, f)
        os.replace(tmp, cls.teams)

    def api(self, path, body=None):
        return self.pg.evaluate("([p, b]) => api(p, b === null ? undefined : b)", [path, body])

    def foto(self, nombre):
        try:
            self.pg.screenshot(path=os.path.join(self.art, f"e2e-avisos-{nombre}.png"), full_page=False)
        except Exception:
            pass

    def log(self):
        with open(os.path.join(self.datos, "server.log"), encoding="utf-8", errors="replace") as f:
            return f.read()

    def franja(self):
        return self.pg.evaluate("() => (document.querySelector('[data-lh-avisos]') || {}).innerText || ''")

    def esperar(self, cond, s, que):
        fin = time.time() + s
        while time.time() < fin:
            if cond():
                return time.time()
            time.sleep(0.25)
        self.foto("x-" + re.sub(r"\W+", "-", que)[:40])
        self.fail(f"{que}: no en {s} s. Franja: {self.franja()!r}; log: {self.log()[-1500:]}")

    def grabar(self, nombre, idioma="es"):
        self.api("/api/settings", {"procesado": "api", "cloud": {"provider": "groq", "api_key": CLAVE},
                                   "preset": "nube", "live_preset": "nube", "live_final_pass": False,
                                   "summary": {"auto": False}})
        self.pg.evaluate("async () => setConfig(await api('/api/config'))")
        r = self.api("/api/rec/start", {"mic": "default", "sys": "all", "folder": "Alfa", "name": nombre,
                                        "live": True, "preset": "nube", "language": idioma})
        rel = r["path"]
        self.pg.evaluate("async p => { await refreshTree(true); go('a', p); }", rel)
        self.pg.wait_for_selector("#lHero [data-lh-avisos]", state="attached", timeout=20000)
        return rel

    def parar(self):
        if self.pg.evaluate("!!(S.rec && S.rec.recording)"):
            self.pg.click("#lHero [data-act='rec-stop']")
            self.pg.click("#modal [data-m='ok']")
        self.esperar(lambda: not any(j.get("live") and j["status"] == "running" for j in self.api("/api/jobs")["jobs"]),
                     60, "el directo termina")

    def test_1_micro_callado_e_idioma_propuesto(self):
        pg = self.pg
        self.groq.oye = "portuguese"  # suena a portugués aunque la reunión esté en «Solo español»
        rel = self.grabar("Avisos idioma")
        # el micro calla a los 6 s: con el minuto acortado a 4 s, el aviso sale hacia los 10 s
        t0 = time.time()
        self.esperar(lambda: "El micro no recibe sonido desde hace 1 min" in self.franja(), 30, "aviso del micro")
        print(f"\n   aviso del micro a los {time.time() - t0:.1f} s de grabar (micro mudo desde los 6 s)", flush=True)
        self.assertIn("Tu voz no se está grabando", self.franja())
        self.assertTrue(pg.evaluate("S.rec.recording"))  # avisa, no para
        # el idioma: varios trozos seguidos que suenan a portugués
        self.esperar(lambda: "Lo último suena a portugués" in self.franja(), 40, "idioma propuesto")
        self.foto("1-micro-e-idioma")
        n_antes = len(self.groq.peticiones)
        self.assertTrue(all(p[1] == "es" for p in self.groq.peticiones), self.groq.peticiones)
        t1 = time.time()
        pg.click("[data-aviso='idioma'] [data-aviso-act='idioma-cambiar']")
        pg.wait_for_function("() => (window.__toasts || []).some(t => t.includes('Idioma cambiado'))", timeout=10000)
        self.assertEqual(pg.eval_on_selector("#lHero select[data-prop='idioma']", "s => s.value"), "pt")
        self.esperar(lambda: any(p[1] == "pt" for p in self.groq.peticiones[n_antes:]), 15, "peticiones en pt")
        print(f"   «Cambiar»: la siguiente petición va en pt a los {time.time() - t1:.1f} s", flush=True)
        self.esperar(lambda: "Lo último suena" not in self.franja(), 10, "el aviso del idioma se va")
        self.assertIn("idioma pt desde ya", self.log())
        self.assertIn("lo último suena a pt (fijado: es); se propone pt", self.log())
        self.parar()
        # «No»: en otra grabación, el aviso se quita y no vuelve
        rel2 = self.grabar("Avisos no")
        self.esperar(lambda: "Lo último suena a portugués" in self.franja(), 40, "idioma propuesto (2)")
        pg.click("[data-aviso='idioma'] [data-aviso-act='idioma-no']")
        self.esperar(lambda: "Lo último suena" not in self.franja(), 5, "el aviso se va con «No»")
        n = len(self.groq.peticiones)
        self.esperar(lambda: len(self.groq.peticiones) >= n + 3, 30, "tres trozos más")
        self.assertNotIn("Lo último suena", self.franja())
        live = [j for j in self.api("/api/jobs")["jobs"] if j["path"] == rel2 and j.get("live")][-1]
        self.assertIsNone((live.get("directo") or {}).get("propuesta"))
        self.assertIn("no se propone más pt en esta grabación", self.log())
        self.parar()
        self.assertNotEqual(rel, rel2)
        self.groq.oye = None
        self.assertEqual(self.errores, [])

    def test_2_fin_de_la_llamada(self):
        pg = self.pg
        self.poner_teams("uso", True)
        rel = self.grabar("Avisos llamada")
        time.sleep(20)  # la llamada cubre la grabación (y el vigilante, cada 8 s, la ve al menos dos veces)
        self.poner_teams("libre", False)
        t0 = time.time()
        self.esperar(lambda: "La llamada ha terminado: paro en" in self.franja(), 30, "aviso de fin de llamada")
        print(f"\n   «paro en N s» a los {time.time() - t0:.1f} s de colgar: {self.franja()!r}", flush=True)
        self.foto("2-fin-de-llamada")
        self.assertTrue(pg.evaluate("(window.__toasts || []).some(t => t.includes('La llamada ha terminado'))"))
        pg.click("[data-aviso='fin'] [data-aviso-act='rec-seguir']")
        self.esperar(lambda: "La llamada ha terminado" not in self.franja(), 5, "«No terminar» quita el aviso")
        time.sleep(12)  # más que los 8 s de la cuenta atrás: sigue grabando
        self.assertTrue(pg.evaluate("S.rec.recording"), "«No terminar» no ha seguido")
        # otra llamada en la misma grabación; esta vez no se toca nada
        self.poner_teams("uso", True)
        time.sleep(20)
        self.poner_teams("libre", False)
        self.esperar(lambda: "La llamada ha terminado: paro en" in self.franja(), 30, "aviso de la segunda llamada")
        t1 = time.time()
        self.esperar(lambda: not pg.evaluate("!!(S.rec && S.rec.recording)"), 30, "para sola")
        print(f"   parada sola a los {time.time() - t1:.1f} s del aviso", flush=True)
        pg.wait_for_function("() => (window.__toasts || []).some(t => t.includes('grabación parada y guardada'))",
                             timeout=10000)
        self.assertIn("parada al terminar la llamada de Teams", self.log())
        self.esperar(lambda: not any(j.get("live") and j["status"] == "running" for j in self.api("/api/jobs")["jobs"]),
                     60, "el directo termina")
        with open(os.path.join(self.root, *rel.split("/")), "rb") as f:
            cab = f.read(44)
        self.assertNotEqual(cab[4:8], b"\xff\xff\xff\xff")  # el WAV quedó cerrado, como con «Parar»
        self.foto("2-parada")
        self.poner_teams("libre", False)
        self.assertEqual(self.errores, [])


if __name__ == "__main__":
    unittest.main()
