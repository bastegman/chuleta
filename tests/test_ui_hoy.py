# -*- coding: utf-8 -*-
"""Hoy (rediseño B7, msgs 1380 y 1390) en un navegador real (Playwright + Chromium), con la app de verdad.

- Biblioteca vacía: «Graba tu primera reunión» con las tres formas de grabar y la de subir un audio; sin Outlook, la
  agenda dice por qué en una línea y la pantalla funciona igual.
- Biblioteca llena y una agenda de Outlook falsa (CHULETA_PRUEBAS_OUTLOOK=tests/fake_outlook/agenda.json: el hilo y la
  caché de la agenda de verdad, leyendo citas de un fichero): la reunión de ahora sale de la agenda, con el proyecto de
  la última vez que se grabó y «La última vez (…): 3 tareas abiertas»; «Para revisar» con su botón por fila; «Esta
  semana»; la agenda de un mes por día, sin canceladas, de todo el día ni lo de más de 31 días; y «Grabar» manda a
  /api/rec/start el plan que se ve, con el idioma cambiado en el chip.
Capturas a 1500×920 y 900×600 en NOTAS_ARTEFACTOS (ui-smoke.yml las sube). Se salta sin Playwright.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timedelta

from support import REPO

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - sin Playwright el test se salta
    sync_playwright = None

AGENDA = os.path.join(REPO, "tests", "fake_outlook", "agenda.json")
SIN_DISPOSITIVOS = ("/api/rec/devices",)  # sin soundcard (como en test_ui_smoke): la tarjeta de grabar lo pide


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wav(path, minutos):
    """Un WAV pequeño que dice durar `minutos` (byte_rate de 10: la duración sale de la cabecera, como en la app)."""
    data = int(minutos * 60 * 10)
    with open(path, "wb") as f:
        f.write(b"RIFF" + (36 + data).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
                + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
                + (10).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
                + b"data" + data.to_bytes(4, "little") + b"\0" * data)


def reunion(root, carpeta, cuando, nombre, minutos, md=None, txt=False, resumen=None, hablantes=None):
    d = os.path.join(root, carpeta) if carpeta else root
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, f"{cuando:%Y-%m-%d %H-%M-%S} - {nombre}")
    wav(base + ".wav", minutos)
    if md:
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write("---\n" + "".join(f"{k}: {v}\n" for k, v in md.items()) + "---\n")
    if txt:
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write("Hola.\n")
    if resumen:
        with open(base + ".resumen.md", "w", encoding="utf-8") as f:
            f.write(resumen)
    if hablantes:
        with open(base + ".hablantes.json", "w", encoding="utf-8") as f:
            json.dump(hablantes, f)
    return base


class Servidor:
    """La app de verdad en un puerto libre, con su raíz y su carpeta de datos (y ajustes de partida)."""

    def __init__(self, preparar, ajustes=None, env=None):
        self.tmp = tempfile.mkdtemp(prefix="tenca-hoy-")
        self.root = os.path.join(self.tmp, "Tenca")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(self.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        preparar(self.root)
        self.datos = os.path.join(self.tmp, "datos")
        os.makedirs(self.datos)
        if ajustes:
            with open(os.path.join(self.datos, "settings.json"), "w", encoding="utf-8") as f:
                json.dump(ajustes, f)
        self.port = puerto_libre()
        self.log = open(os.path.join(self.tmp, "salida.log"), "w", encoding="utf-8")
        e = dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {}))
        self.proc = subprocess.Popen([sys.executable, os.path.join(self.root, "_app", "server.py"), "--port",
                                      str(self.port), "--data", self.datos, "--no-browser"],
                                     cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT, env=e)
        self.base = f"http://127.0.0.1:{self.port}"
        token_file, fin, self.token = os.path.join(self.datos, "sesion.token"), time.time() + 60, None
        while time.time() < fin and self.proc.poll() is None:
            if os.path.isfile(token_file):
                with open(token_file, encoding="utf-8") as f:
                    self.token = f.read().strip()
                try:
                    req = urllib.request.Request(self.base + "/api/ping", headers={"X-Chuleta-Token": self.token})
                    if json.load(urllib.request.urlopen(req, timeout=2)).get("app") == "audios":
                        return
                except Exception:
                    pass
            time.sleep(0.3)
        self.cerrar()
        raise unittest.SkipTest("el servidor de prueba no arrancó")

    def api(self, path):
        req = urllib.request.Request(self.base + path, headers={"X-Chuleta-Token": self.token})
        return json.load(urllib.request.urlopen(req, timeout=10))

    def cerrar(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


@unittest.skipUnless(sync_playwright, "playwright no instalado")
class Base(unittest.TestCase):
    AJUSTES, ENV = None, None

    @classmethod
    def preparar(cls, root):
        pass

    @classmethod
    def setUpClass(cls):
        cls.srv = Servidor(cls.preparar, cls.AJUSTES, cls.ENV)
        cls.art = os.environ.get("NOTAS_ARTEFACTOS")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        for cerrar in (lambda: cls.browser.close(), lambda: cls.pw.stop(), lambda: cls.srv.cerrar()):
            try:
                cerrar()
            except Exception:
                pass

    def pagina(self, ancho=1500, alto=920, elegido=True):
        ctx = self.browser.new_context(bypass_csp=True, viewport={"width": ancho, "height": alto})
        if elegido:  # ya se eligió dónde procesar: sin la tarjeta de bienvenida encima
            ctx.add_init_script("localStorage.setItem('procElegido', '1'); localStorage.setItem('avisoProbar', '1')")
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        self.errores, self.red = [], []
        pg.on("console", lambda m: self.errores.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        pg.on("response", lambda r: self.red.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        pg.goto(f"{self.srv.base}/?t={self.srv.token}", wait_until="networkidle")
        pg.wait_for_selector("#main h1:has-text('Hoy')", timeout=15000)
        return pg

    def captura(self, pg, nombre):
        if self.art:
            os.makedirs(self.art, exist_ok=True)
            pg.screenshot(path=os.path.join(self.art, f"hoy_{nombre}.png"), full_page=True)

    def sin_errores(self, permitidas=()):
        red = [r for r in self.red if not any(p in r for p in SIN_DISPOSITIVOS + tuple(permitidas))]
        err = [e for e in self.errores if not (not red and "Failed to load resource" in e)]
        self.assertEqual((err, red), ([], []))


class HoyVacioTests(Base):
    """Instalación nueva: sin reuniones y con el calendario apagado (como viene)."""

    def test_estado_vacio_y_sin_outlook(self):
        for ancho, alto, elegido in ((1500, 920, False), (900, 600, True)):
            pg = self.pagina(ancho, alto, elegido)
            pg.wait_for_selector(".hoy-vacio h2:has-text('Graba tu primera reunión')", timeout=5000)
            vacio = pg.inner_text(".hoy-vacio")
            for t in ("Grabar", "Ctrl", "Alt", "R", "desde cualquier programa", "Teams", "arrastra aquí un audio"):
                self.assertIn(t, vacio)
            # la agenda dice por qué no hay agenda, en una línea (apagado, o no es Windows, o sin Outlook clásico)
            pg.wait_for_selector("#hoyAgenda .ag-estado", timeout=10000)
            linea = pg.inner_text("#hoyAgenda .ag-estado")
            self.assertTrue(any(t in linea for t in ("Activa el calendario de Outlook", "solo se lee en Windows",
                                                     "no tiene el Outlook clásico", "Outlook nuevo")), linea)
            # sin reunión ahora ni reuniones grabadas (y /api/hoy ya ha contestado: la agenda se ha pintado), la tarjeta
            # de ahora no sale: lo dice «Graba tu primera reunión»
            self.assertEqual(pg.inner_text("#hoyAhora").strip(), "")
            self.assertEqual(pg.locator(".hoy-revisar, .hoy-semana").count(), 0)
            self.captura(pg, f"vacia_{ancho}x{alto}")
            # «Grabar» del estado vacío abre la tarjeta de grabar (sin reunión, para ponerle nombre)
            pg.click(".hoy-vacio [data-act='hoy-tarjeta']")
            pg.wait_for_selector("#modal:not(.hidden) .dialog.gr", timeout=5000)
            pg.keyboard.press("Escape")
            self.sin_errores()


class HoyLlenoTests(Base):
    """Una semana de reuniones y la agenda de Outlook (falsa) de un mes, con una reunión en curso."""
    AJUSTES = {"outlook_on": True}
    ENV = {"CHULETA_PRUEBAS_OUTLOOK": AGENDA}

    @classmethod
    def preparar(cls, root):
        for proy, ws in (("Alfa", "General"), ("Alfa", "Infraestructura"), ("Beta", "General")):
            os.makedirs(os.path.join(root, proy, "Vocabulario"), exist_ok=True)
            with open(os.path.join(root, proy, "Vocabulario", ws + ".txt"), "w", encoding="utf-8") as f:
                f.write(f"{proy} {ws}, término\n")
        now = datetime.now().replace(second=0, microsecond=0)
        alfa = {"proyecto": "Alfa", "workstream": "General"}
        cls.ultima = now - timedelta(days=7)
        reunion(root, "Alfa", cls.ultima, "Comité de seguimiento", 52, dict(alfa, titulo="Comité de seguimiento",
                participantes="Ana, Luis, Carmen"), txt=True,
                resumen="## Resumen\nTodo bien.\n## Próximos pasos\n- [ ] Ana: presupuesto\n- [ ] Luis: informe\n"
                        "- [x] Carmen: sala\n- [ ] Yo: acta\n")
        reunion(root, "Beta", now - timedelta(days=1, hours=1), "Revisión de presupuesto", 40,
                {"proyecto": "Beta", "workstream": "General", "titulo": "Revisión de presupuesto"}, txt=True,
                resumen="## Resumen\nOk.\n", hablantes={"tramos": [[0, 5, "A"]], "tu": "C",
                                                        "hablantes": {"A": {}, "B": {}, "C": {}}})
        reunion(root, "Beta", now - timedelta(days=2), "Entrevista equipo", 2,
                {"proyecto": "Beta", "workstream": "General", "titulo": "Entrevista equipo"})
        reunion(root, "", now - timedelta(hours=2), "", 12, txt=True)  # sin proyecto ni nombre
        reunion(root, "Alfa", now - timedelta(minutes=30), "Migración de servidores", 38,
                {"proyecto": "Alfa", "workstream": "Infraestructura", "titulo": "Migración de servidores"},
                txt=True, resumen="## Resumen\nHecho.\n")

    def esperar_agenda(self, pg):
        pg.wait_for_selector("#hoyAgenda .ag-cita", timeout=30000)
        pg.wait_for_selector("#hoyAhora .ha-titulo:has-text('Comité de seguimiento')", timeout=30000)

    def test_hoy_con_agenda_y_reuniones(self):
        pg = self.pagina()
        self.esperar_agenda(pg)
        # la reunión de ahora: de la agenda, con el proyecto de la última vez y sus tareas abiertas
        ahora = pg.inner_text("#hoyAhora")
        self.assertRegex(ahora, r"(?i)ahora · \d\d:\d\d–\d\d:\d\d")
        self.assertIn("La última vez (", ahora)
        self.assertIn("3 tareas abiertas", ahora)
        self.assertIn("ver resumen", ahora)
        self.assertIn("como la última «Comité de seguimiento»", ahora)
        proy = pg.eval_on_selector("[data-hoy='vocab']", "s => s.options[s.selectedIndex].text")
        self.assertEqual(proy, "Alfa › General")  # el chip cerrado dice el proyecto y el workstream
        # para revisar: cada una con su botón
        filas = pg.locator(".hoy-revisar .hr-fila")
        textos = [filas.nth(i).inner_text() for i in range(filas.count())]
        botones = {t.split("\n")[1]: t.split("\n")[-1] for t in textos}
        self.assertEqual(sorted(botones.values()), ["Elegir proyecto", "Ponles nombre", "Transcribir"], textos)
        self.assertTrue(any("2 voces sin nombre" in t for t in textos), textos)
        # esta semana: lo que no pide nada (la de hoy con resumen; la de hace 7 días ya no es de esta semana)
        semana = pg.inner_text(".hoy-semana")
        self.assertIn("Migración de servidores", semana)
        self.assertIn("Resumen", semana)
        self.assertNotIn("Comité de seguimiento", semana)
        # la agenda: por día, la de ahora marcada, sin canceladas, de todo el día ni lo de más de un mes
        ag = pg.inner_text("#hoyAgenda")
        for t in ("AHORA", "MAÑANA", "Daily de Infraestructura", "Kick-off Proyecto Beta", "Sala 3 · Torre Picasso"):
            self.assertIn(t, ag.upper() if t.isupper() else ag)
        for t in ("Cancelada", "Vacaciones", "40 días"):
            self.assertNotIn(t, ag)
        self.assertIn("Ver el resto del mes", ag)
        self.assertNotIn("Cierre trimestral", ag)
        self.captura(pg, "llena_1500x920")
        pg.click("[data-act='hoy-agenda-todo']")
        pg.wait_for_selector("#hoyAgenda :text('Cierre trimestral')", timeout=5000)
        # cambiar el idioma en el chip pide el plan otra vez; «Grabar» manda ese plan a /api/rec/start
        enviado = []

        def start(route):
            enviado.append(json.loads(route.request.post_data))
            route.fulfill(status=400, content_type="application/json",
                          body=json.dumps({"error": "Prueba: no se graba"}))
        pg.route("**/api/rec/start", start)
        with pg.expect_response(lambda r: "/api/grabar/plan" in r.url):
            pg.select_option("[data-hoy='idioma']", "pt")
        pg.wait_for_function("document.querySelector('[data-hoy=idioma]').value === 'pt'", timeout=5000)
        pg.click("[data-act='hoy-grabar']")
        pg.wait_for_function("document.querySelector('#toasts').textContent.includes('Prueba: no se graba')",
                             timeout=5000)
        self.assertEqual(len(enviado), 1)
        cuerpo = enviado[0]
        self.assertEqual((cuerpo["name"], cuerpo["language"], cuerpo["plan"]), ("Comité de seguimiento", "pt", True))
        self.assertTrue(cuerpo["vocab"])
        self.assertIn("Comité de seguimiento", cuerpo["resumen"])
        self.sin_errores(permitidas=("/api/rec/start",))  # el 400 de la prueba (no se graba de verdad)

    def test_estrecha_y_acciones(self):
        pg = self.pagina(900, 600)
        self.esperar_agenda(pg)
        self.captura(pg, "llena_900x600")
        # una sola columna: la reunión de ahora, lo que pide algo de ti, la agenda y esta semana
        y = [pg.eval_on_selector(s, "e => e.getBoundingClientRect().top") for s in
             ("#hoyAhora", ".hoy-revisar", "#hoyAgenda", ".hoy-semana")]
        self.assertEqual(y, sorted(y))
        # «Elegir proyecto»: un diálogo con los proyectos; «Ponles nombre» abre la reunión (no la fila con otro clic)
        pg.click(".hoy-revisar [data-k='proyecto']")
        pg.wait_for_selector("#modal:not(.hidden) #hoyProy", timeout=5000)
        pg.keyboard.press("Escape")
        pg.click(".hoy-revisar [data-k='voces']")
        pg.wait_for_function("location.hash.startsWith('#/a/') && location.hash.includes('Revisi')", timeout=5000)
        pg.click("[data-nav='home']")
        pg.wait_for_selector("#hoyAhora .ha-titulo:has-text('Comité de seguimiento')", timeout=10000)
        # «ver resumen» de la última vez abre esa reunión
        pg.click("[data-act='hoy-ultima']")
        pg.wait_for_function("location.hash.startsWith('#/a/') && location.hash.includes('Comit')", timeout=5000)
        # Calendario (donde se buscaban «las siguientes reuniones», msg 1380): las citas de Outlook que vienen
        pg.click("[data-nav='c']")
        pg.wait_for_selector(".cday .cev.outlook:has-text('Comité de seguimiento')", timeout=10000)
        self.captura(pg, "calendario_900x600")
        self.sin_errores()

    def test_preparar_desde_la_agenda(self):
        """«Preparar» una cita que aún no ha empezado: proyecto, idioma y contexto, guardados en el servidor; la fila
        lo dice y «Cambiar» lo reabre con lo elegido. (Que se usa al grabarla lo prueba test_hoy, con la hora.)"""
        pg = self.pagina()
        self.esperar_agenda(pg)
        fila = pg.locator(".ag-cita", has_text="Kick-off Proyecto Beta")
        fila.locator("[data-act='hoy-preparar']").click()
        pg.wait_for_selector("#modal:not(.hidden) h3:has-text('Preparar «Kick-off Proyecto Beta»')", timeout=5000)
        self.assertEqual(pg.locator("#modal [data-act='hoy-preparar'], #modal [data-m='quitar']").count(), 0)
        beta = pg.eval_on_selector("#hpVocab", "s => [...s.options].find(o => o.text === 'Beta › General').value")
        pg.select_option("#hpVocab", beta)
        pg.select_option("#hpIdioma", "en")
        pg.fill("#hpCtx", "Arranque con el cliente")
        pg.click("#modal [data-m='ok']")
        pg.wait_for_selector(".ag-cita:has-text('Kick-off Proyecto Beta') .ag-prep-txt", timeout=10000)
        self.assertEqual(fila.locator(".ag-prep-txt").inner_text().strip(),
                         "Preparada · Beta › General · Solo inglés · con contexto")
        self.captura(pg, "preparada_1500x920")
        fila.locator("[data-act='hoy-preparar']").click()  # ahora dice «Cambiar» y trae lo elegido
        pg.wait_for_selector("#modal:not(.hidden) [data-m='quitar']", timeout=5000)
        self.assertEqual((pg.input_value("#hpVocab"), pg.input_value("#hpIdioma"), pg.input_value("#hpCtx")),
                         (beta, "en", "Arranque con el cliente"))
        pg.click("#modal [data-m='quitar']")
        pg.wait_for_function("!document.querySelector('.ag-cita .ag-prep-txt')", timeout=10000)
        self.sin_errores()

    def test_api_hoy(self):
        """El contrato de /api/hoy con la agenda leída: la agenda pública no lleva asistentes ni nada protegido."""
        fin = time.time() + 30
        while True:
            r = self.srv.api("/api/hoy")
            if r["agenda"]["estado"] == "ok" or time.time() > fin:
                break
            time.sleep(0.5)
        self.assertEqual(r["agenda"]["estado"], "ok", r["agenda"])
        self.assertEqual(r["agenda"]["dias"], 31)
        self.assertEqual(r["ahora"]["fuente"], "outlook")
        self.assertEqual(r["plan"]["nombre"]["valor"], "Comité de seguimiento")
        self.assertEqual(r["plan"]["proyecto"]["origen"], "historial")
        self.assertEqual(r["ultima"]["tareas"], 3)
        claves = set().union(*(c.keys() for c in r["agenda"]["citas"]))
        self.assertEqual(claves, {"subject", "start", "end", "location", "is_teams", "recurring", "categories"})
        asuntos = [c["subject"] for c in r["agenda"]["citas"]]
        self.assertNotIn("Cancelada: no sale", asuntos)
        self.assertNotIn("Dentro de 40 días: no sale", asuntos)
        self.assertEqual(asuntos.count("Comité de seguimiento"), 2)
        self.assertEqual(asuntos, [c["subject"] for c in sorted(r["agenda"]["citas"], key=lambda c: c["start"])])


if __name__ == "__main__":
    unittest.main()
