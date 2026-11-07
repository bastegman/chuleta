# -*- coding: utf-8 -*-
"""El fallo del msg 1170 en un navegador real (Playwright + Chromium): una reunion en la que el micro marco como «Tú»
a otra persona; clic en «Tú», escribir «Ana», Guardar → la etiqueta dice «Ana» y la persona del usuario no cambia.
Tambien «No soy yo» y «Copiar texto» con hablantes. Se salta sin Playwright (la CI lo instala en `ui-smoke.yml`)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from support import REPO, srt_text, wav_bytes
from test_ui_smoke import PERMITIDAS, puerto_libre

import voices  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - sin Playwright el test se salta
    sync_playwright = None

TRAMOS = [{"inicio": 0, "fin": 10, "h": "h1"}, {"inicio": 10, "fin": 20, "h": "h2"},
          {"inicio": 20, "fin": 30, "h": "h1"}]


@unittest.skipUnless(sync_playwright, "playwright no instalado")
class VocesEnNavegadorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuleta-ui-voces-")
        cls.root = os.path.join(cls.tmp, "Chuleta")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(cls.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        cls.datos = os.path.join(cls.tmp, "datos")
        os.makedirs(cls.datos)
        with open(os.path.join(cls.datos, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"voices_enabled": True}, f)
        v = voices.Voices(os.path.join(cls.datos, "voces.json"))  # se cifra igual que en el servidor (mismo usuario)
        cls.yo = v.create("Bernardo", es_usuario=True)["id"]
        cls.port = puerto_libre()
        cls.log = open(os.path.join(cls.tmp, "salida.log"), "w", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(cls.root, "_app", "server.py"), "--port",
                                     str(cls.port), "--data", cls.datos, "--no-browser"], cwd=cls.root,
                                    stdout=cls.log, stderr=subprocess.STDOUT,
                                    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        cls.base = f"http://127.0.0.1:{cls.port}"
        token_file, fin, cls.token = os.path.join(cls.datos, "sesion.token"), time.time() + 60, None
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
            raise unittest.SkipTest("el servidor de prueba no arrancó (ver salida.log)")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        for attr in ("browser", "pw"):
            try:
                getattr(cls, attr).stop() if attr == "pw" else getattr(cls, attr).close()
            except Exception:
                pass
        if getattr(cls, "proc", None) and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        try:
            cls.log.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def reunion(self, nombre):
        """Reunion con tres frases; el micro marco a h2 (otra persona) como «Tú», y h2 es del usuario por el micro."""
        base = os.path.join(self.root, nombre)
        with open(base + ".wav", "wb") as f:
            f.write(wav_bytes(1.0))
        with open(base + ".srt", "w", encoding="utf-8") as f:
            f.write(srt_text([(0.5, 9.5, "Hola a todos."), (10.5, 19.5, "Yo no soy Bernardo."),
                              (20.5, 29.5, "Seguimos.")]))
        voices.write_sidecar(base + voices.SIDECAR_EXT, {
            "version": 1, "tramos": TRAMOS, "tu": "h2", "micro": {"paso": 0.25, "activo": "0" * 40 + "1" * 40,
                                                                  "desfase": 0},
            "hablantes": {"h1": {"segundos": 20.0, "persona": None},
                          "h2": {"segundos": 10.0, "persona": self.yo, "origen": "micro"}}})
        return nombre + ".wav"

    def api(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Chuleta-Token": self.token, "X-Audios": "1",
                                              "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=10))

    def abrir(self, rel):
        ctx = self.browser.new_context(bypass_csp=True)
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        errores, red = [], []
        pg.on("console", lambda m: errores.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
        pg.on("response", lambda r: red.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        pg.goto(f"{self.base}/?t={self.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1", timeout=15000)
        pg.evaluate("p => { location.hash = '#/a/' + encodeURIComponent(p); }", rel)
        pg.wait_for_selector("#txBody .spk[data-spk='h2']", timeout=15000)
        return pg, errores, red

    def limpio(self, errores, red):
        red = [r for r in red if not any(p in r for p in PERMITIDAS)]
        return [e for e in errores if not (not red and "Failed to load resource" in e)], red

    def chip(self, pg, h):
        return pg.inner_text(f"#txBody .spk[data-spk='{h}']")

    def test_nombrar_al_tu_equivocado(self):
        rel = self.reunion("R nombre")
        pg, errores, red = self.abrir(rel)
        self.assertEqual(self.chip(pg, "h2"), "Tú")
        pg.click("#txBody .spk[data-spk='h2']")
        pg.wait_for_selector(".spk-pop", timeout=5000)
        pg.fill("#spName", "Ana")
        pg.click(".spk-pop [data-sa='name']")
        time.sleep(1.5)  # la peticion y el repintado
        st = self.api("/api/voces")
        names = {p["id"]: (p["nombre"], p["es_usuario"]) for p in st["personas"]}
        self.assertEqual(names[self.yo], ("Bernardo", True))  # el usuario sigue siendo el y con su nombre
        pg.wait_for_function("document.querySelector(\"#txBody .spk[data-spk='h2']\").innerText === 'Ana'",
                             timeout=10000)
        self.assertIn(("Ana", False), names.values())
        texto = pg.evaluate("transcriptText()")  # lo que deja «Copiar texto» en el portapapeles
        self.assertEqual(texto, "Persona 1: Hola a todos.\n\nAna: Yo no soy Bernardo.\n\nPersona 1: Seguimos.")
        self.assertEqual(self.limpio(errores, red), ([], []))

    def test_no_soy_yo(self):
        rel = self.reunion("R no soy yo")
        pg, errores, red = self.abrir(rel)
        pg.click("#txBody .spk[data-spk='h2']")
        pg.wait_for_selector(".spk-pop [data-sa='notme']", timeout=5000)
        self.assertIn("No soy yo", pg.inner_text(".spk-pop [data-sa='notme']"))
        pg.click(".spk-pop [data-sa='notme']")
        pg.wait_for_function("document.querySelector(\"#txBody .spk[data-spk='h2']\").innerText === 'Persona 2'",
                             timeout=10000)
        self.assertIsNone(self.api("/api/hablantes?path=" + urllib.request.quote(rel))["tu"])
        self.assertEqual(self.limpio(errores, red), ([], []))


if __name__ == "__main__":
    unittest.main()
