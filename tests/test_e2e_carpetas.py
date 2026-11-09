# -*- coding: utf-8 -*-
"""E2E de «cambiar la carpeta en pleno directo» (B1c, msg 1198) con la app de verdad: el servidor arrancado como lo
arranca el acceso directo, la grabadora real (`recorder.py`) con la tarjeta de sonido falsa de tests/fake_soundcard
(en la CI no hay dispositivos de audio), el directo, el repaso final y el resumen en un Groq falso en 127.0.0.1, y la
interfaz en Chromium (Playwright):

1. graba 33 s en el proyecto Alfa: la grabación va a su carpeta «AAAA-MM-DD Comité de seguimiento» (B1b);
2. en la cabecera del directo, «Se guarda en … · Cambiar» → Beta: «Se moverá a <raíz>\\Beta\\… al terminar · Deshacer»;
3. para la grabación: la reunión no se mueve hasta que acaban el directo, el repaso final y el resumen (D4b);
4. entonces se mueve la carpeta entera (audio, texto, subtítulos, notas, resumen y voces), la vieja desaparece, la
   página pasa sola a la ruta nueva y avisa «Movida a …».

Pesado (≈1 min): solo con CHULETA_E2E_CARPETAS=1, que pone ui-smoke.yml (Ubuntu y Windows). Deja capturas y
server.log en NOTAS_ARTEFACTOS si existe."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from support import REPO

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from test_e2e_procesado import CLAVE, GroqFalso, puerto_libre  # Groq falso (nunca una clave real en la CI)

ACTIVO = os.environ.get("CHULETA_E2E_CARPETAS") == "1"
FAKE = os.path.join(REPO, "tests", "fake_soundcard")
NOMBRE = "Comité de seguimiento"
GRABAR_S = 33  # más de 30 s: con el directo en la nube, así hay repaso final


@unittest.skipUnless(ACTIVO, "E2E pesado: solo con CHULETA_E2E_CARPETAS=1 (ui-smoke.yml)")
@unittest.skipUnless(sync_playwright, "playwright no instalado")
class CarpetaEnDirectoE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuleta-e2e-carpetas-")
        cls.art = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(cls.tmp, "artefactos")
        os.makedirs(cls.art, exist_ok=True)
        cls.root = os.path.join(cls.tmp, "Chuleta")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(cls.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        for proyecto, lista in (("Alfa", "General"), ("Beta", "Legal")):
            os.makedirs(os.path.join(cls.root, proyecto, "Vocabulario"))
            with open(os.path.join(cls.root, proyecto, "Vocabulario", lista + ".txt"), "w", encoding="utf-8") as f:
                f.write("término uno, término dos\n")
        cls.groq = GroqFalso()
        cls.datos = os.path.join(cls.tmp, "datos")
        cls.port = puerto_libre()
        # sin modelos de Whisper descargados (como en la CI): nada que precargar al arrancar, que en un PC con medium en
        # cache tiene la CPU ocupada mientras arranca la grabadora
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
            for n in ("server.log", "recorder.log"):
                if os.path.isfile(os.path.join(cls.datos, n)):
                    shutil.copy(os.path.join(cls.datos, n), os.path.join(cls.art, "e2e-carpetas-" + n))
            shutil.copy(os.path.join(cls.tmp, "salida.log"), os.path.join(cls.art, "e2e-carpetas-salida.log"))
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
            self.pg.screenshot(path=os.path.join(self.art, f"e2e-carpetas-{nombre}.png"), full_page=True)
        except Exception:
            pass

    def log(self):
        with open(os.path.join(self.datos, "server.log"), encoding="utf-8", errors="replace") as f:
            return f.read()

    def test_grabar_cambiar_la_carpeta_en_directo_y_al_terminar_se_mueve_todo(self):
        pg, hoy = self.pg, time.strftime("%Y-%m-%d")
        self.api("/api/settings", {"procesado": "api", "cloud": {"provider": "groq", "api_key": CLAVE},
                                   "preset": "nube", "live_preset": "nube", "live_final_pass": True})
        pg.evaluate("async () => setConfig(await api('/api/config'))")
        # 1. grabar en el proyecto Alfa: su carpeta «AAAA-MM-DD Comité de seguimiento» (B1b)
        t0 = time.time()
        r = self.api("/api/rec/start", {"mic": "default", "sys": "all", "folder": "Alfa", "name": NOMBRE,
                                        "live": True, "preset": "nube", "vocab": "Alfa/Vocabulario/General.txt",
                                        "language": "en"})
        rel, vieja = r["path"], f"Alfa/{hoy} {NOMBRE}"
        self.assertEqual(r["carpeta"], vieja)
        stem = os.path.splitext(os.path.basename(rel))[0]
        pg.evaluate("async p => { await refreshTree(true); go('a', p); }", rel)
        pg.wait_for_selector(".live-hero .dest-line", timeout=20000)
        # la raíz por su nombre en el disco: esta carpeta se llama «Chuleta» (instalada encima), aunque la app sea Tenca
        self.assertIn(f"Chuleta\\Alfa\\{hoy} {NOMBRE}\\", pg.inner_text(".live-hero .dest-line"))
        self.foto("1-se-guarda-en")
        # 2. «Cambiar» → Beta, en pleno directo
        pg.click(".live-hero [data-dest-act='cambiar']")
        pg.wait_for_selector(".dest-pop [data-dest-folder='Beta']", timeout=10000)
        self.foto("2-elegir")
        pg.click(".dest-pop [data-dest-folder='Beta']")
        pg.wait_for_selector(".live-hero .dest-line.pend", timeout=15000)
        linea = pg.inner_text(".live-hero .dest-line.pend")
        self.assertIn("Se moverá a", linea)
        self.assertIn(f"Chuleta\\Beta\\{hoy} {NOMBRE}\\", linea)
        self.foto("3-se-movera")
        m = self.api("/api/jobs")["movimientos"]
        self.assertEqual(m["pendientes"][rel]["folder"], "Beta")
        live = [j for j in self.api("/api/jobs")["jobs"] if j["path"] == rel and j.get("live")]
        self.assertTrue(live and live[-1]["preset"] == "nube", live)
        # 3. que grabe lo suficiente para el repaso final; la reunión sigue en Alfa mientras tanto
        while time.time() - t0 < GRABAR_S:
            time.sleep(1)
        self.assertTrue(os.path.isfile(os.path.join(self.root, *rel.split("/"))))
        pg.evaluate("""() => { window.__toasts = [];
            new MutationObserver(() => document.querySelectorAll('#toasts .toast')
                .forEach(t => window.__toasts.push(t.innerText)))
                .observe(document.querySelector('#toasts'), {childList: true}); }""")
        pg.click(".live-hero [data-act='rec-stop']")
        pg.click("#modal [data-m='ok']")
        # 4. se mueve cuando acaba todo: directo, repaso final y resumen
        fin, m = time.time() + 180, {}
        while time.time() < fin:
            m = self.api("/api/jobs")["movimientos"]
            if not m["pendientes"] and any(h["de"] == rel for h in m["hechos"]):
                break
            time.sleep(1)
        else:
            self.foto("x-sin-mover")
            self.fail(f"no se ha movido en 180 s: {m}; trabajos: {self.api('/api/jobs')['jobs'][-4:]}")
        hecho = next(h for h in m["hechos"] if h["de"] == rel)
        nueva = f"Beta/{hoy} {NOMBRE}"
        self.assertEqual((hecho["error"], hecho["carpeta"]), (None, nueva))
        self.assertEqual(hecho["a"], f"{nueva}/{stem}.wav")
        dentro = sorted(os.listdir(os.path.join(self.root, "Beta", f"{hoy} {NOMBRE}")))
        print(f"\n   en {nueva}: {dentro}", flush=True)
        for e in (".wav", ".txt", ".srt", ".md", ".resumen.md"):
            self.assertIn(stem + e, dentro)
        self.assertFalse(os.path.exists(os.path.join(self.root, "Alfa", f"{hoy} {NOMBRE}")))  # la vieja, fuera
        with open(os.path.join(self.root, "Beta", f"{hoy} {NOMBRE}", stem + ".resumen.md"), encoding="utf-8") as f:
            self.assertIn("Resumen de prueba de Groq", f.read())
        # el orden (D4b): el resumen se escribió antes de mover; y el repaso final, antes del resumen
        log = self.log()
        movida = log.index(f"↪ {rel}")
        self.assertLess(log.index("resumen listo"), movida)
        self.assertLess(log.index("repaso final de la grabación entera"), log.index("resumen listo"))
        self.assertIn(f"📁 {rel}: se moverá a Beta al terminar", log)
        # la página sigue a la reunión y lo avisa
        pg.wait_for_function("p => S.route.path === p", arg=hecho["a"], timeout=20000)
        pg.wait_for_function("() => (window.__toasts || []).some(t => t.includes('Movida a'))", timeout=20000)
        self.assertIn(f"\\Beta\\{hoy} {NOMBRE}\\", " ".join(pg.evaluate("window.__toasts")))
        self.foto("4-movida")
        self.assertEqual(self.errores, [])
        print(f"   grabado {GRABAR_S} s; movida a {nueva} tras el resumen; peticiones a Groq: "
              f"{len(self.groq.peticiones)}", flush=True)


if __name__ == "__main__":
    unittest.main()
