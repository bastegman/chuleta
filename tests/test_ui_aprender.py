# -*- coding: utf-8 -*-
"""«Aprender de tus correcciones» en un navegador de verdad (Playwright + Chromium), como lo usa Bernardo: corrige una
transcripción, pulsa «Listo» y encima del texto le sale «¿Añadir «Orvalia» al vocabulario de Finanzas?»; con un clic el
término va a la lista (también a una carpeta de listas conectada, o le dice por qué no puede) y, si lo deja marcado,
Tenca lo cambia en las próximas reuniones del proyecto: subrayado, con «Deshacer» y con la pregunta de si dejar de
hacerlo. En ui-smoke.yml (Ubuntu y Windows). La regla aplicada al transcribir va contra un Groq falso en 127.0.0.1
(test_e2e_procesado.GroqFalso; nunca una clave real) y necesita PyAV, como la cola de verdad."""
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request

from support import REPO, srt_text

try:
    import av  # noqa: F401
except ImportError:  # pragma: no cover
    av = None
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

FIN = "Cliente Alfa/Vocabulario/Finanzas.txt"
SEGS = ["Buenos días, empezamos el comité de finanzas.",
        "El primer punto es el cierre con orbalia, que va con retraso.",
        "Vale, y el tesa con ellos se firma la semana que viene.",
        "Ella dice que orbalia quiere ver el plan antes del viernes.",
        "Lo q dijimos ayer sigue en pie, con el cliente al tanto.",
        "Perfecto, cerramos aquí."]


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wav_bytes(segundos=2.0, tono=True):
    import math
    import struct
    n = int(16000 * segundos)
    data = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * i / 16000)) if tono else 0)
                    for i in range(n))
    return (b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
            + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
            + b"data" + len(data).to_bytes(4, "little") + data)


@unittest.skipUnless(sync_playwright, "playwright no instalado")
class AprenderEnLaInterfazTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.groq = None
        if av is not None:
            from test_e2e_procesado import GroqFalso
            cls.groq = GroqFalso()
        cls.tmp = tempfile.mkdtemp(prefix="tenca-aprender-ui-")
        cls.root = os.path.join(cls.tmp, "Tenca")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(cls.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        cls.escribir(FIN, "# Comité de finanzas\nSAP, S/4HANA, cierre trimestral\n")
        cls.escribir("Cliente Alfa/Vocabulario/Operaciones.txt", "WMS, almacén central\n")
        cls.ext = os.path.join(cls.tmp, "OneDrive", "Listas compartidas")
        os.makedirs(cls.ext)
        with open(os.path.join(cls.ext, "Legal.txt"), "wb") as f:
            f.write(b"Contrato marco\r\nNDA\r\n")
        cls.datos = os.path.join(cls.tmp, "datos")
        os.makedirs(cls.datos)
        regla = {"de": "fake Groq", "a": "FakeGroq", "proyecto": "Cliente Alfa", "at": 1}
        with open(os.path.join(cls.datos, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"vocab_links": [{"name": "Listas compartidas", "path": cls.ext}],
                       "aprendido": {"reglas": [regla], "no": [], "no_reglas": []}}, f)
        cls.port = puerto_libre()
        cls.log = open(os.path.join(cls.tmp, "salida.log"), "w", encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8",
                   CHULETA_PRUEBAS_URL_MODELOS="http://127.0.0.1:9/{repo}/resolve/{rev}/{file}")
        if cls.groq:
            env["CHULETA_PRUEBAS_URL_GROQ"] = cls.groq.base
        cls.proc = subprocess.Popen([sys.executable, os.path.join(cls.root, "_app", "server.py"), "--port",
                                     str(cls.port), "--data", cls.datos, "--no-browser"],
                                    cwd=cls.root, stdout=cls.log, stderr=subprocess.STDOUT, env=env)
        cls.base = f"http://127.0.0.1:{cls.port}"
        token_file, fin, cls.token = os.path.join(cls.datos, "sesion.token"), time.time() + 60, None
        while time.time() < fin and cls.proc.poll() is None:
            if os.path.isfile(token_file):
                with open(token_file, encoding="utf-8") as f:
                    cls.token = f.read().strip()
                try:
                    if cls.get("/api/ping").get("app") == "audios":
                        break
                except Exception:
                    pass
            time.sleep(0.3)
        else:
            cls.tearDownClass()
            raise unittest.SkipTest("el servidor de prueba no arrancó (ver salida.log)")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.art = os.environ.get("NOTAS_ARTEFACTOS")

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
        if getattr(cls, "groq", None):
            cls.groq.httpd.shutdown()
        try:
            cls.log.close()
        except Exception:
            pass
        os.chmod(os.path.join(cls.ext, "Legal.txt"), stat.S_IREAD | stat.S_IWRITE)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # --- utilidades
    @classmethod
    def escribir(cls, rel, texto, encoding="utf-8"):
        p = os.path.join(cls.root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding=encoding, newline="") as f:
            f.write(texto)
        return p

    @classmethod
    def get(cls, path):
        req = urllib.request.Request(cls.base + path, headers={"X-Chuleta-Token": cls.token})
        return json.load(urllib.request.urlopen(req, timeout=10))

    @classmethod
    def post(cls, path, body):
        req = urllib.request.Request(cls.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Chuleta-Token": cls.token, "X-Audios": "1",
                                              "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=20))

    def reunion(self, nombre, textos=SEGS, proyecto="Cliente Alfa", workstream="Finanzas"):
        rel = f"Cliente Alfa/2026-11-09 10-00-00 - {nombre}.wav"
        base = os.path.join(self.root, "Cliente Alfa", f"2026-11-09 10-00-00 - {nombre}")
        with open(base + ".wav", "wb") as f:
            f.write(wav_bytes(1.0, tono=False))
        self.escribir(os.path.splitext(rel)[0] + ".srt",
                      srt_text([(i * 5.0, i * 5.0 + 4.5, t) for i, t in enumerate(textos)]))
        self.escribir(os.path.splitext(rel)[0] + ".txt", "\n".join(textos) + "\n", "utf-8-sig")
        if proyecto:
            self.escribir(os.path.splitext(rel)[0] + ".md",
                          f"---\nproyecto: {proyecto}\nworkstream: {workstream}\n---\n")
        return rel, base

    def pagina(self, ancho=1280, alto=860, oscuro=False):
        ctx = self.browser.new_context(bypass_csp=True, viewport={"width": ancho, "height": alto},
                                       color_scheme="dark" if oscuro else "light")
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        self.errores = []
        pg.on("console", lambda m: self.errores.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        pg.goto(f"{self.base}/?t={self.token}", wait_until="load")
        pg.wait_for_selector("#main h1", timeout=15000)
        return pg

    def abrir(self, pg, rel):
        pg.evaluate("() => refreshTree(true)")
        pg.wait_for_function("p => S.audios.has(p)", arg=rel, timeout=10000)
        pg.evaluate("p => go('a', p)", rel)
        pg.wait_for_selector("#txBody .s", timeout=10000)

    def captura(self, pg, nombre, sel=None):
        if self.art:  # sin los avisos de abajo ni el ratón encima de un botón: lo que se quiere enseñar
            os.makedirs(self.art, exist_ok=True)
            pg.evaluate("() => document.querySelectorAll('#toasts > *').forEach(t => t.remove())")
            pg.mouse.move(0, 0)
            (pg.locator(sel) if sel else pg).screenshot(path=os.path.join(self.art, f"aprender_{nombre}.png"))

    def sin_errores(self):
        # /api/rec/devices contesta 400 sin soundcard (lo piden otras pantallas, no esta)
        self.assertEqual([e for e in self.errores if "Failed to load resource" not in e], [])

    def reemplazar(self, pg, de, a):
        """Lo que hace él: buscar, «Cambiar lo buscado por», «Reemplazar todo»."""
        pg.fill("#tq", de)
        pg.wait_for_function("t => S.tq === t", arg=de, timeout=5000)
        pg.fill("#trep", a)
        pg.click("[data-act='tx-replace-all']")

    def corregir(self, pg, *cambios):
        pg.click("[data-act='tx-edit']")
        for de, a in cambios:
            self.reemplazar(pg, de, a)
        pg.wait_for_selector("#saveState:has-text('Guardado')", timeout=10000)
        self.assertEqual(pg.locator(".apr-card").count(), 0)  # mientras corrige, nada
        pg.fill("#tq", "")
        pg.wait_for_function("() => S.tq === ''", timeout=5000)
        pg.click("[data-act='tx-done']")
        pg.wait_for_selector(".apr-card", timeout=10000)

    def leer(self, rel):
        with open(os.path.join(self.root, *rel.split("/")), "rb") as f:
            return f.read()

    def reglas(self):
        return [(r["de"], r["a"], r["proyecto"]) for r in self.get("/api/aprender/reglas")["reglas"]]

    # --- los recorridos
    def test_corregir_proponer_anadir_y_no(self):
        rel, base = self.reunion("Comité de finanzas")
        pg = self.pagina()
        self.abrir(pg, rel)
        self.assertEqual(pg.locator(".apr-card").count(), 0)  # sin corregir, nada
        self.corregir(pg, ("orbalia", "Orvalia"), ("tesa", "TSA"), ("Lo q", "Lo que"), ("el cliente", "Juan"))
        card = pg.locator(".apr-card")
        self.assertEqual(card.locator(".apr-tit").text_content(), "¿Añadir estas palabras al vocabulario de Finanzas?")
        self.assertEqual(card.locator(".apr-term").all_text_contents(), ["Orvalia", "TSA"])  # ni «Lo que» ni «Juan»
        f0 = card.locator(".apr-fila[data-k='0']")
        self.assertIn("Whisper escribió «orbalia» (2 veces)", f0.text_content())
        # marcada: Bernardo quiere que lo que confirma valga también en las futuras (msg 1390); la casilla lo dice
        self.assertTrue(f0.locator("input[type=checkbox]").is_checked())
        self.assertIn("Y en las próximas reuniones de Cliente Alfa, cambiar «orbalia» por «Orvalia» (se guarda en tu "
                      "diccionario)", f0.locator(".apr-regla").text_content())
        self.assertEqual(pg.input_value("#aprLista"), "Cliente Alfa/Vocabulario/Finanzas.txt")
        self.assertFalse(pg.evaluate("!!document.activeElement.closest('#aprBox')"))  # no roba el foco
        pg.mouse.move(0, 0)
        self.captura(pg, "01_propuesta", ".apr-card")
        card.locator(".apr-fila[data-k='1'] input[type=checkbox]").uncheck()  # la de TSA, desmarcada
        pg.select_option("#aprLista", "Cliente Alfa/Vocabulario/Operaciones.txt")  # repinta la tarjeta
        pg.select_option("#aprLista", FIN)
        self.assertFalse(card.locator(".apr-fila[data-k='1'] input[type=checkbox]").is_checked())  # no se pierde
        f0.locator("[data-act='apr-si']").click()
        pg.wait_for_selector(".apr-fila.hecha", timeout=10000)
        self.assertFalse(card.locator(".apr-fila:not(.hecha) input[type=checkbox]").is_checked())  # tampoco aquí
        self.assertIn("«Orvalia» añadido a Finanzas (Cliente Alfa). En tu diccionario: «orbalia» → «Orvalia».",
                      card.locator(".apr-fila.hecha").text_content())
        self.assertEqual(self.leer(FIN), b"# Comit\xc3\xa9 de finanzas\nSAP, S/4HANA, cierre trimestral, Orvalia\n")
        self.assertIn(("orbalia", "Orvalia", "Cliente Alfa"), self.reglas())
        # con una sola pendiente, la pregunta es solo esa
        self.assertEqual(card.locator(".apr-tit").text_content(), "¿Añadir «TSA» al vocabulario de Finanzas?")
        card.locator(".apr-fila:not(.hecha) [data-act='apr-no']").click()
        pg.wait_for_function("() => !document.querySelector('.apr-fila:not(.hecha)')", timeout=5000)
        self.assertEqual(card.locator(".apr-tit").text_content(), "Listo")
        self.captura(pg, "02_listo", ".apr-card")
        self.assertNotIn(b"TSA", self.leer(FIN))
        # al volver a la reunión ya no hay nada que preguntar
        pg.evaluate("() => go('home')")
        self.abrir(pg, rel)
        pg.wait_for_timeout(500)
        self.assertEqual(pg.locator(".apr-card").count(), 0)
        # Ajustes › Proyectos: el diccionario personal, para ver, cambiar y quitar
        pg.evaluate("() => openSettings({sec: 'proyectos'})")
        pg.wait_for_selector("#sAprendido table", timeout=10000)
        self.assertIn("Tu diccionario personal", pg.locator("#sAprendido > label").text_content())
        fila = pg.locator("#sAprendido tr", has_text="«orbalia» → Orvalia")
        self.assertIn("Cliente Alfa", fila.text_content())
        pg.locator("#sAprendido").scroll_into_view_if_needed()
        self.captura(pg, "03_ajustes")
        fila.locator("[data-apr-editar]").click()
        self.assertEqual(pg.evaluate("document.activeElement.id"), "aprDicA")
        pg.fill("#aprDicDe", "el")  # una palabra corriente: no, y dice por qué, sin perder lo escrito
        pg.click("[data-apr-guardar]")
        pg.wait_for_selector("#sAprendido .apr-err", timeout=5000)
        self.assertIn("muy corriente", pg.locator("#sAprendido .apr-err").text_content())
        self.assertEqual(pg.input_value("#aprDicDe"), "el")
        self.captura(pg, "03b_cambiar_error", "#sAprendido")
        pg.fill("#aprDicDe", "orbalia")
        pg.fill("#aprDicA", "Orvalia SA")
        pg.keyboard.press("Enter")  # Intro guarda
        pg.wait_for_selector("#sAprendido tr:has-text('«orbalia» → Orvalia SA')", timeout=5000)
        self.assertIn(("orbalia", "Orvalia SA", "Cliente Alfa"), self.reglas())
        self.assertFalse(pg.locator("#modal").is_hidden())  # Intro en la fila no cierra Ajustes
        pg.locator("#sAprendido tr", has_text="«orbalia» → Orvalia SA").locator("[data-apr-editar]").click()
        pg.keyboard.press("Escape")  # Escape cancela la fila, y Ajustes sigue abierto
        pg.wait_for_function("() => !document.querySelector('#aprDicDe')", timeout=5000)
        self.assertFalse(pg.locator("#modal").is_hidden())
        fila = pg.locator("#sAprendido tr", has_text="«orbalia» → Orvalia SA")
        fila.locator("[data-apr-olvidar]").click()
        pg.wait_for_function("() => !document.querySelector('#sAprendido').textContent.includes('orbalia')",
                             timeout=5000)
        self.assertNotIn(("orbalia", "Orvalia SA", "Cliente Alfa"), self.reglas())
        # y lo que dijo que no, con su «quitar»
        no = pg.locator("#sAprendido .apr-no-lista")
        self.assertIn("No te propone añadir: «TSA»", no.text_content())
        no.locator("[data-apr-sino]").click()
        pg.wait_for_function("() => !document.querySelector('#sAprendido .apr-no-lista')", timeout=5000)
        self.assertEqual(self.get("/api/aprender/reglas")["no"], [])
        self.post("/api/aprender/no", {"termino": "TSA"})  # como estaba, para los demás tests
        self.sin_errores()

    def test_lista_conectada_que_no_se_puede_escribir(self):
        rel, base = self.reunion("Contrato", [s.replace("tesa", "dpo") for s in SEGS])  # (TSA: dicho que no arriba)
        # ya está en el diccionario para Cliente Alfa: con su lista (Finanzas) no se ofrece otra vez; con la de otro
        # proyecto, sí (el filtro reglas_en de aprQue, en el navegador)
        self.post("/api/aprender/regla", {"de": "dpo", "a": "DPO", "proyecto": "Cliente Alfa"})
        pg = self.pagina()
        self.abrir(pg, rel)
        self.corregir(pg, ("dpo", "DPO"))
        self.assertEqual(pg.locator(".apr-card .apr-regla").count(), 0)
        # al vocabulario general, la regla valdría para las reuniones sin proyecto, no para Cliente Alfa: sin marcar
        pg.select_option("#aprLista", "")
        self.assertIn("en las próximas reuniones sin proyecto", pg.locator(".apr-card .apr-regla").text_content())
        self.assertFalse(pg.locator(".apr-card .apr-regla input").is_checked())
        legal = pg.evaluate("S.apr.data.listas.find(l => l.name === 'Legal').id")
        pg.select_option("#aprLista", legal)
        card = pg.locator(".apr-card")
        self.assertEqual(card.locator(".apr-tit").text_content(), "¿Añadir «DPO» al vocabulario de Legal?")
        self.assertIn("está en tu carpeta de listas: se añade allí", card.locator(".apr-dest").text_content())
        self.assertIn("en las próximas reuniones de Listas compartidas", card.locator(".apr-regla").text_content())
        lista = os.path.join(self.ext, "Legal.txt")
        os.chmod(lista, stat.S_IREAD)
        try:
            card.locator("[data-act='apr-si']").click()
            pg.wait_for_selector(".apr-err", timeout=10000)
        finally:
            os.chmod(lista, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(card.locator(".apr-err").get_attribute("role"), "alert")
        self.assertIn("No puedo escribir en «Legal.txt»: es de solo lectura", card.locator(".apr-err").text_content())
        self.captura(pg, "04_solo_lectura", ".apr-card")
        self.assertNotIn(("dpo", "DPO", "Listas compartidas"), self.reglas())
        card.locator(".apr-regla input").check()
        card.locator("[data-act='apr-si']").click()  # con permiso, otra vez: entra en su línea, con su CRLF
        pg.wait_for_selector(".apr-fila.hecha", timeout=10000)
        with open(lista, "rb") as f:
            self.assertEqual(f.read(), b"Contrato marco\r\nNDA\r\nDPO\r\n")
        self.assertIn(("dpo", "DPO", "Listas compartidas"), self.reglas())
        self.post("/api/aprender/olvidar", {"de": "dpo", "proyecto": "Listas compartidas"})
        self.post("/api/aprender/olvidar", {"de": "dpo", "proyecto": "Cliente Alfa"})
        self.sin_errores()

    def test_la_lista_de_la_reunion_sin_conexion(self):
        """Su lista está en la carpeta conectada y esta no está (VPN, disco): lo dice y no elige otra por su cuenta."""
        rel, base = self.reunion("Sin conexión", [s.replace("tesa", "gdpr") for s in SEGS],
                                 proyecto="Listas compartidas", workstream="Legal")
        pg = self.pagina()
        self.abrir(pg, rel)
        pg.click("[data-act='tx-edit']")
        self.reemplazar(pg, "gdpr", "GDPR")
        pg.wait_for_selector("#saveState:has-text('Guardado')", timeout=10000)
        fuera = self.ext + " (sin conexión)"
        os.rename(self.ext, fuera)
        try:
            pg.fill("#tq", "")
            pg.click("[data-act='tx-done']")
            card = pg.locator(".apr-card")
            card.wait_for(timeout=10000)
            self.assertIn("No encuentro la lista «Legal» de Listas compartidas de esta reunión",
                          card.locator(".apr-falta").text_content())
            self.assertEqual(card.locator(".apr-tit").text_content(), "¿Añadir «GDPR» al vocabulario?")
            self.assertTrue(card.locator("[data-act='apr-si']").is_disabled())
            self.assertEqual(pg.input_value("#aprLista"), "-")
            self.captura(pg, "08_sin_conexion", ".apr-card")
        finally:
            os.rename(fuera, self.ext)
        # vuelve la conexión: al recargar, su lista otra vez, sin tener que elegirla
        pg.evaluate("p => aprCargar(S.audios.get(p))", rel)
        pg.wait_for_function("() => document.querySelector('#aprLista').value.endsWith('/Legal.txt')", timeout=5000)
        self.assertTrue(card.locator("[data-act='apr-si']").is_enabled())
        self.assertEqual(card.locator(".apr-falta").count(), 0)
        pg.select_option("#aprLista", FIN)
        self.assertEqual(card.locator(".apr-tit").text_content(), "¿Añadir «GDPR» al vocabulario de Finanzas?")
        self.sin_errores()

    def test_ahora_no_teclado_y_ventana_estrecha(self):
        rel, base = self.reunion("Seguimiento", [s.replace("orbalia", "kubernetis") for s in SEGS])
        pg = self.pagina(ancho=600, alto=900)
        self.abrir(pg, rel)
        self.corregir(pg, ("kubernetis", "Kubernetes"))
        card = pg.locator(".apr-card")
        # sin desbordar a lo ancho, con los botones a la vista
        self.assertTrue(pg.evaluate("() => { const c = document.querySelector('.apr-card'); "
                                    "return c.scrollWidth <= c.clientWidth + 1 }"))
        card.scroll_into_view_if_needed()
        self.assertTrue(card.locator("[data-act='apr-si']").is_visible())
        self.captura(pg, "05_estrecha", ".apr-card")
        # la barra espaciadora, en un botón de la tarjeta, lo pulsa (no reproduce el audio)
        card.locator("[data-act='apr-cerrar']").focus()
        pg.keyboard.press(" ")
        pg.wait_for_function("() => !document.querySelector('.apr-card')", timeout=5000)
        self.assertTrue(pg.evaluate("() => document.querySelector('#audio').paused"))
        self.assertIn(("kubernetis", "Kubernetes"), [(p["antes"][0]["texto"], p["termino"])
                                                     for p in self.get("/api/aprender?path=" + urllib.parse.quote(rel))
                                                     ["propuestas"]])  # «Ahora no» no es un no
        self.sin_errores()

    @unittest.skipUnless(av, "PyAV no instalado (la cola de verdad lo necesita)")
    def test_regla_aplicada_al_transcribir_subrayada_y_deshacer(self):
        """De punta a punta: la regla aprendida «fake Groq» → «FakeGroq» (Cliente Alfa) se aplica al transcribir con
        el Groq falso una reunión de ese proyecto, y no la de otro; se ve subrayada, se deshace y se olvida."""
        from test_e2e_procesado import CLAVE
        rel, base = self.reunion("Con Groq", ["x"])
        for e in (".srt", ".txt"):
            os.remove(base + e)
        with open(base + ".wav", "wb") as f:
            f.write(wav_bytes(3.0))
        otra = "Otro/2026-11-10 11-00-00 - Otra cosa.wav"
        os.makedirs(os.path.join(self.root, "Otro"), exist_ok=True)
        with open(os.path.join(self.root, *otra.split("/")), "wb") as f:
            f.write(wav_bytes(3.0))
        self.post("/api/settings", {"procesado": "api", "cloud": {"provider": "groq", "api_key": CLAVE},
                                    "summary": {"auto": False}})
        self.post("/api/transcribe", {"paths": [rel, otra], "preset": "nube", "language": "en"})
        fin, js = time.time() + 120, []
        while time.time() < fin:
            js = [j for j in self.get("/api/jobs")["jobs"] if j["path"] in (rel, otra)]
            if len(js) == 2 and all(j["status"] in ("done", "error", "cancelled") for j in js):
                break
            time.sleep(0.5)
        self.assertEqual([j["status"] for j in js], ["done", "done"], js)
        self.assertIn(b"This is the FakeGroq transcription.", self.leer(os.path.splitext(rel)[0] + ".srt"))
        self.assertIn(b"This is the fake Groq transcription.", self.leer(os.path.splitext(otra)[0] + ".srt"))
        pg = self.pagina()
        self.abrir(pg, rel)
        pg.wait_for_selector(".apr-bar", timeout=10000)
        self.assertIn("Corregido con tu diccionario personal", pg.locator(".apr-bar").text_content())
        self.assertIn("«fake Groq» → FakeGroq", pg.locator(".apr-bar").text_content())
        w = pg.locator("#txBody .apr-w")
        self.assertEqual(w.count(), 1)
        self.assertEqual(w.text_content(), "FakeGroq")
        self.assertEqual(w.get_attribute("title"), "Lo cambió Tenca: Whisper escribió «fake Groq»")
        self.assertEqual(pg.locator(".apr-card").count(), 0)  # lo que cambió Tenca no se propone como tuyo
        # la búsqueda cuenta igual con el subrayado: «fake groq» son dos trozos pegados, un solo resaltado (como
        # hlTerms sin subrayado), con lo que cambió Tenca dentro
        pg.fill("#tq", "fake groq")
        pg.wait_for_function("() => S.tq === 'fake groq'", timeout=5000)
        self.assertEqual(pg.locator("#txBody mark").count(), 1)
        self.assertEqual(pg.locator("#tqn").text_content().split("/")[-1], "1")
        self.assertEqual(pg.locator("#txBody mark .apr-w").text_content(), "FakeGroq")
        self.assertEqual(pg.evaluate("() => (hlTerms(S.tx.segs[0].text, termsOf(S.tq)).match(/<mark>/g) || []).length"),
                         1)
        pg.fill("#tq", "")
        pg.wait_for_function("() => S.tq === ''", timeout=5000)
        pg.locator("#aTabs").scroll_into_view_if_needed()
        self.captura(pg, "06_aplicada")
        # al editar, sin subrayado ni barra (el texto es tuyo); al terminar, vuelven
        pg.click("[data-act='tx-edit']")
        self.assertEqual(pg.locator(".apr-bar, #txBody .apr-w").count(), 0)
        pg.click("[data-act='tx-done']")
        pg.wait_for_selector(".apr-bar", timeout=5000)
        pg.click("[data-act='apr-deshacer']")
        pg.wait_for_selector(".apr-bar:has-text('¿Quitarlo también de tu diccionario')", timeout=10000)
        self.assertEqual(pg.locator("#txBody .apr-w").count(), 0)
        self.assertIn("This is the fake Groq transcription.", pg.locator("#txBody").text_content())
        self.assertIn(b"This is the fake Groq transcription.", self.leer(os.path.splitext(rel)[0] + ".srt"))
        self.assertEqual(pg.evaluate("document.activeElement.dataset.act"), "apr-olvidar")
        self.captura(pg, "07_deshecha", ".apr-bar")
        self.assertIn(("fake Groq", "FakeGroq", "Cliente Alfa"), self.reglas())  # deshacer no la olvida
        pg.click("[data-act='apr-olvidar']")
        pg.wait_for_selector(".apr-bar:has-text('ya no está en tu diccionario')", timeout=5000)
        self.assertNotIn(("fake Groq", "FakeGroq", "Cliente Alfa"), self.reglas())
        self.sin_errores()


if __name__ == "__main__":
    unittest.main()
