# -*- coding: utf-8 -*-
"""E2E del idioma y la traducción en pleno directo (ítems 12 y 13a; msgs 1150-1152) con la app de verdad: el servidor
como lo arranca el acceso directo, la grabadora real con la tarjeta de sonido falsa de tests/fake_soundcard, el
directo en un Groq falso en 127.0.0.1 que escribe en el texto el idioma que le piden («Texto en pt») y traduce según
el prompt («EN: …», «ES: …»), y la interfaz en Chromium (Playwright):

1. graba en inglés («Solo inglés»): el texto sale «en en»;
2. en la cabecera del directo, idioma → Portugués: el aviso dice en cuánto se verá y que rehace lo último; en unos
   segundos (no al siguiente trozo, ni al terminar) lo ya escrito del último minuto pasa a portugués y lo nuevo también;
3. «Traducir al ▾ inglés»: la columna dice «Traducción al inglés» y trae «EN: …»; al cambiar a español, «ES: …» y
   desaparece lo de inglés; el destino queda recordado para la reunión.

Pesado (~40 s): solo con CHULETA_E2E_CARPETAS=1, que pone ui-smoke.yml (Ubuntu y Windows). Deja capturas y server.log
en NOTAS_ARTEFACTOS si existe."""
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


class GroqIdiomas:
    """Groq falso: /audio/transcriptions devuelve «Texto en <idioma pedido> n.º k» (verbose_json, con las cabeceras de
    cupo) y /chat/completions traduce según el prompt del sistema (inglés: «EN: …»; español: «ES: …»)."""

    def __init__(self):
        self.peticiones, self.n = [], 0
        g = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                ok = (self.headers.get("Authorization") or "") == f"Bearer {CLAVE}"
                if not ok:
                    out, code = {"error": {"message": "Invalid API Key"}}, 401
                elif self.path.endswith("/audio/transcriptions"):
                    m = re.search(rb'name="language"\r\n\r\n([^\r]*)', body)
                    lang = m.group(1).decode() if m else ""
                    g.n += 1
                    g.peticiones.append(("tx", lang, time.time()))
                    texto = f" Texto en {lang or 'auto'} numero {g.n}."
                    out, code = {"text": texto, "language": NOMBRES.get(lang, "english"), "duration": 3.0,
                                 "segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": texto, "avg_logprob": -0.1,
                                               "no_speech_prob": 0.01, "compression_ratio": 1.2}]}, 200
                else:
                    msgs = json.loads(body).get("messages") or [{}, {}]
                    system, user = msgs[0].get("content") or "", msgs[-1].get("content") or ""
                    if "simultaneous interpreter" in system:
                        txt = "EN: " + user
                    elif "traductor simultáneo" in system:
                        txt = "ES: " + user
                    else:
                        txt = "## Resumen\nResumen de prueba."
                    g.peticiones.append(("chat", txt[:3], time.time()))
                    out, code = {"choices": [{"message": {"content": txt}}], "usage": {"total_tokens": 10}}, 200
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
class IdiomaEnDirectoE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tenca-e2e-idioma-")
        cls.art = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(cls.tmp, "artefactos")
        os.makedirs(cls.art, exist_ok=True)
        cls.root = os.path.join(cls.tmp, "Tenca")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(cls.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(cls.root, "Alfa", "Vocabulario"))
        with open(os.path.join(cls.root, "Alfa", "Vocabulario", "General.txt"), "w", encoding="utf-8") as f:
            f.write("término uno, término dos\n")
        cls.groq = GroqIdiomas()
        cls.datos = os.path.join(cls.tmp, "datos")
        cls.port = puerto_libre()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=FAKE, CHULETA_PRUEBAS_URL_GROQ=cls.groq.base,
                   HF_HUB_CACHE=os.path.join(cls.tmp, "hf-vacia"))
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
                    shutil.copy(os.path.join(cls.datos, n), os.path.join(cls.art, "e2e-idioma-" + n))
        except Exception:
            pass
        try:
            cls.groq.httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def api(self, path, body=None):
        return self.pg.evaluate("([p, b]) => api(p, b === null ? undefined : b)", [path, body])

    def foto(self, nombre):
        try:
            self.pg.screenshot(path=os.path.join(self.art, f"e2e-idioma-{nombre}.png"), full_page=True)
        except Exception:
            pass

    def log(self):
        with open(os.path.join(self.datos, "server.log"), encoding="utf-8", errors="replace") as f:
            return f.read()

    def texto(self):
        return self.pg.evaluate("() => (document.querySelector('#txBody') || {}).innerText || ''")

    def esperar(self, cond, s, que):
        fin = time.time() + s
        while time.time() < fin:
            if cond():
                return time.time()
            time.sleep(0.25)
        self.foto("x-" + re.sub(r"\W+", "-", que)[:40])
        self.fail(f"{que}: no en {s} s. Texto: {self.texto()[-600:]!r}; Groq: {self.groq.peticiones[-6:]}")

    def test_idioma_y_traduccion_en_pleno_directo(self):
        pg = self.pg
        self.api("/api/settings", {"procesado": "api", "cloud": {"provider": "groq", "api_key": CLAVE},
                                   "preset": "nube", "live_preset": "nube", "live_final_pass": False,
                                   "summary": {"auto": False}})
        pg.evaluate("async () => setConfig(await api('/api/config'))")
        r = self.api("/api/rec/start", {"mic": "default", "sys": "all", "folder": "Alfa", "name": "Idiomas",
                                        "live": True, "preset": "nube", "vocab": "Alfa/Vocabulario/General.txt",
                                        "language": "en"})
        rel = r["path"]
        pg.evaluate("async p => { await refreshTree(true); go('a', p); }", rel)
        pg.wait_for_selector("#lHero select[data-prop='idioma']", timeout=20000)
        # 1. en inglés: dos trozos
        self.esperar(lambda: self.texto().count("Texto en en") >= 2, 25, "dos trozos en inglés")
        self.foto("1-ingles")
        pg.evaluate("""() => { window.__toasts = [];
            new MutationObserver(() => document.querySelectorAll('#toasts .toast')
                .forEach(t => window.__toasts.push(t.innerText)))
                .observe(document.querySelector('#toasts'), {childList: true, subtree: true}); }""")
        # 2. a portugués desde la cabecera del directo
        t0 = time.time()
        pg.select_option("#lHero select[data-prop='idioma']", "pt")
        pg.wait_for_function("() => (window.__toasts || []).some(t => t.includes('Idioma cambiado'))", timeout=10000)
        aviso = next(t for t in pg.evaluate("window.__toasts") if "Idioma cambiado" in t)
        print(f"\n   aviso: {aviso}", flush=True)
        self.assertRegex(aviso, r"el texto nuevo sale en ~\d+ s; rehace en él los últimos \d+ s ya escritos")
        t_pt = self.esperar(lambda: "Texto en pt" in self.texto() and "Texto en en" not in self.texto(), 15,
                            "lo escrito pasa a portugués")
        tarda = t_pt - t0
        self.foto("2-portugues")
        print(f"   portugués en pantalla a los {tarda:.1f} s del cambio (sin nada en inglés)", flush=True)
        self.assertLess(tarda, 10.0)  # «funcionó, pero tardó»: ahora en segundos
        log = self.log()
        self.assertIn("idioma pt desde ya; se rehacen en él los últimos", log)
        self.assertIn("rehecho en pt lo último", log)
        with open(os.path.splitext(os.path.join(self.root, *rel.split("/")))[0] + ".txt", encoding="utf-8") as f:
            txt = f.read()
        self.assertNotIn("Texto en en", txt)  # también en el archivo, no solo en la pantalla
        self.assertIn("Texto en pt", txt)
        # 3. traducción al inglés y luego al español
        pg.wait_for_selector("#directoTradChk", timeout=10000)
        pg.select_option("#directoTradTo", "en")
        pg.check("#directoTradChk")
        pg.wait_for_function("() => /Traducción al inglés/.test((document.querySelector('#directoTrad') || {})"
                             ".innerText || '')", timeout=10000)
        self.esperar(lambda: "EN: " in pg.evaluate("() => (document.querySelector('#directoTrad') || {}).innerText "
                                                   "|| ''"), 20, "traducción al inglés")
        self.foto("3-traduccion-ingles")
        t1 = time.time()
        pg.select_option("#directoTradTo", "es")
        trad = "() => (document.querySelector('#directoTrad') || {}).innerText || ''"
        t_es = self.esperar(lambda: "Traducción al español" in pg.evaluate(trad) and "ES: " in pg.evaluate(trad)
                            and "EN: " not in pg.evaluate(trad), 15, "traducción al español")
        print(f"   traducción al español a los {t_es - t1:.1f} s del cambio", flush=True)
        self.foto("4-traduccion-espanol")
        with open(os.path.join(self.datos, "traduccion_destino.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {rel: "es"})
        tr_es = [p for p in self.groq.peticiones if p[0] == "chat" and p[1] == "ES:"]
        tr_en = [p for p in self.groq.peticiones if p[0] == "chat" and p[1] == "EN:"]
        self.assertTrue(tr_en and tr_es, self.groq.peticiones)
        # parar
        pg.click("#lHero [data-act='rec-stop']")
        pg.click("#modal [data-m='ok']")
        self.esperar(lambda: not any(j.get("live") and j["status"] == "running" for j in self.api("/api/jobs")["jobs"]),
                     60, "el directo termina")
        self.assertEqual(self.errores, [])
        print(f"   peticiones a Groq: {len(self.groq.peticiones)} "
              f"({sum(1 for p in self.groq.peticiones if p[0] == 'tx')} de transcripción)", flush=True)

    def test_corrigiendo_y_cambiando_el_idioma_no_se_pierde_nada(self):
        """Revisión de idioma-directo (P1): con el texto en «Editar», cambiar el idioma no deja el editor con el texto
        de antes ni, al guardar, da por borrado lo rehecho. El editor pasa al texto nuevo sin salir de «Editar» y una
        corrección hecha después se guarda sobre él."""
        pg = self.pg
        self.api("/api/settings", {"procesado": "api", "cloud": {"provider": "groq", "api_key": CLAVE},
                                   "preset": "nube", "live_preset": "nube", "live_final_pass": False,
                                   "summary": {"auto": False}})
        pg.evaluate("async () => setConfig(await api('/api/config'))")
        r = self.api("/api/rec/start", {"mic": "default", "sys": "all", "folder": "Alfa", "name": "Editando",
                                        "live": True, "preset": "nube", "vocab": "Alfa/Vocabulario/General.txt",
                                        "language": "en"})
        rel = r["path"]
        txt = os.path.splitext(os.path.join(self.root, *rel.split("/")))[0] + ".txt"
        pg.evaluate("async p => { await refreshTree(true); go('a', p); }", rel)
        pg.wait_for_selector("#lHero select[data-prop='idioma']", timeout=20000)
        self.esperar(lambda: self.texto().count("Texto en en") >= 2, 25, "dos trozos en inglés")
        pg.click("[data-act='tx-edit']")
        pg.wait_for_function("S.editing === true", timeout=5000)
        gen0 = pg.evaluate("S.tx.gen")
        pg.select_option("#lHero select[data-prop='idioma']", "pt")
        # el editor pasa al texto rehecho (portugués) sin salir de «Editar»
        self.esperar(lambda: pg.evaluate("S.editing && S.tx.gen !== null && S.tx.gen !== " + json.dumps(gen0)
                                         + " && S.tx.segs.every(s => !s.text.includes('Texto en en'))"), 20,
                     "el editor con el texto nuevo")
        self.foto("5-editando-tras-el-cambio")
        self.assertTrue(pg.evaluate("S.editing"))
        # una corrección sobre el texto nuevo se guarda y no borra nada
        n_antes = pg.evaluate("S.tx.segs.length")
        pg.evaluate("""() => { const el = document.querySelector('#txBody .s[data-i="0"]');
            el.focus(); el.innerText = 'Corregida a mano'; el.dispatchEvent(new Event('input', {bubbles: true})); }""")
        pg.evaluate("saveEdits()")

        def leer():
            with open(txt, encoding="utf-8-sig") as f:
                return f.read()
        self.esperar(lambda: "Corregida a mano" in leer(), 10, "la corrección guardada")
        lineas = [x for x in leer().splitlines() if x.strip()]
        print(f"\n   tras corregir: {lineas}", flush=True)
        self.assertNotIn("Texto en en", " ".join(lineas))
        self.assertGreaterEqual(len(lineas), n_antes)  # nada de lo rehecho se ha dado por borrado
        live = [j for j in self.api("/api/jobs")["jobs"] if j["path"] == rel and j.get("live")][-1]
        self.assertEqual(live["status"], "running")
        pg.click("[data-act='tx-done']")  # «Listo»: sale de «Editar» guardando
        pg.wait_for_function("S.editing === false", timeout=10000)
        pg.click("#lHero [data-act='rec-stop']")
        pg.click("#modal [data-m='ok']")
        self.esperar(lambda: not any(j.get("live") and j["status"] == "running" for j in self.api("/api/jobs")["jobs"]),
                     60, "el directo termina")
        self.assertEqual(self.errores, [])


if __name__ == "__main__":
    unittest.main()
