# -*- coding: utf-8 -*-
"""fix-lento (10-11/11/2026, msgs 1479 y 1481: «cuando intento guardar los nombres de las voces, me peta y se tarda
mucho mucho en guardar. Funciona finalmente pero tarda minutos y no aparece nada»).

Medido en el Mac (memory/projects/notas_fix_lento_IMPL_LINUS_2026-11-10.md): «Guardar» con «Recordar» (también
«Confirmar», que lo trae marcado) calculaba la huella en un subproceso que decodificaba la reunión ENTERA, en
prioridad «en reposo» y sin enseñar nada: 6 s en una de 1 h en WAV y 40 s en una de 3 h en m4a en un Mac rápido;
minutos en un portátil ocupado. Ahora:
- la huella decodifica solo sus tramos (engines.decode_rangos_16k, voices.audio_huella), ~2 s sea cual sea la reunión;
- ese subproceso va por debajo de lo normal, no en reposo (la identificación de voces sigue en reposo);
- el cuadro del hablante dice «Guardando…» al momento y no se puede volver a pulsar mientras;
- con «API externa» no se precarga Whisper para la cola, y el directo dice en el log cuando cae al PC."""
import json
import os
import shutil
import tempfile
import time
import types
import unittest
import wave
from unittest import mock

from support import APP, TempTree, bare_jobs, engines, server  # noqa: F401  (support prepara sys.path)
from test_perfil import settings_store
from test_subir_audio import Servidor

import voices  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    import av
except ImportError:  # pragma: no cover
    av = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def senal(segundos, rate=16000, canales=1, semilla=3):
    """float32 [-1, 1] con algo distinto en cada momento (tonos que cambian cada 0,37 s y ruido): un tramo sacado
    del sitio equivocado no se parece al de verdad."""
    rng = np.random.default_rng(semilla)
    n = int(segundos * rate)
    t = np.arange(n) / rate
    f = 200 + 600 * rng.random(int(segundos / 0.37) + 2)[(t / 0.37).astype(int)]
    y = 0.3 * np.sin(2 * np.pi * np.cumsum(f) / rate) + 0.05 * rng.standard_normal(n)
    y = y.astype(np.float32)
    return np.repeat(y[:, None], canales, axis=1) if canales > 1 else y


def escribir_wav(path, segundos, rate=16000, canales=1):
    y = senal(segundos, rate, canales)
    with wave.open(path, "wb") as w:
        w.setnchannels(canales)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def escribir_av(path, segundos, codec, rate=16000, fmt=None, hueco=None):
    """Un audio comprimido con PyAV (flac, aac en m4a…). hueco=(segundo, s): los tiempos saltan esos segundos ahí
    (una grabación pausada o un remux con un corte), sin audio en medio."""
    import fractions
    y = senal(segundos, rate)
    with av.open(path, "w", format=fmt) as c:
        s = c.add_stream(codec, rate=rate)
        s.layout = "mono"
        paso, pts = 1024, 0
        for i in range(0, len(y), paso):
            trozo = y[i:i + paso]
            fr = av.AudioFrame.from_ndarray(trozo[None, :], format="flt", layout="mono")
            fr.sample_rate = rate
            if hueco and i >= hueco[0] * rate and pts < (hueco[0] + hueco[1]) * rate:
                pts += int(hueco[1] * rate)
            fr.pts, fr.time_base = pts, fractions.Fraction(1, rate)
            pts += len(trozo)
            for pk in s.encode(fr):
                c.mux(pk)
        for pk in s.encode(None):
            c.mux(pk)


def danar(path, donde=0.4, n=8000):
    """n bytes de basura en ese punto del archivo (un trozo dañado a mitad: el demuxer lo tira, a veces sin avisar)."""
    with open(path, "rb") as f:
        b = bytearray(f.read())
    k = int(len(b) * donde)
    b[k:k + n] = np.random.default_rng(5).integers(0, 256, n, dtype=np.uint8).tobytes()
    with open(path, "wb") as f:
        f.write(bytes(b))


def duracion(path):
    """La del sidecar: la de decode_16k, redondeada a 0,1 s (voices.diarize)."""
    return round(len(engines.decode_16k(path)) / 16000, 1)


def tiene_codificador(nombre):
    try:
        av.codec.Codec(nombre, "w")
        return True
    except Exception:
        return False


def desfase(ref, q, maximo=800):
    """Muestras de desfase entre dos trozos (correlación cruzada en ±maximo)."""
    n = min(len(ref), len(q))
    r_, q_ = ref[:n].astype(np.float64) - ref[:n].mean(), q[:n].astype(np.float64) - q[:n].mean()
    m = 1 << int(np.ceil(np.log2(2 * n)))
    cc = np.fft.irfft(np.fft.rfft(r_, m) * np.conj(np.fft.rfft(q_, m)), m)
    cc = np.concatenate([cc[-maximo:], cc[:maximo + 1]])
    return int(np.argmax(cc)) - maximo


def parecido(a, b, lag):
    """Correlación de dos trozos tras quitarles un desfase de `lag` muestras (en el sentido que sea)."""
    mejor = -1.0
    for k in (lag, -lag):
        x, y = (a[k:], b) if k >= 0 else (a, b[-k:])
        n = min(len(x), len(y))
        mejor = max(mejor, float(np.corrcoef(x[:n].astype(float), y[:n].astype(float))[0, 1]))
    return mejor


RANGOS = [(40.3, 52.9), (3.0, 4.5), (119.0, 120.0), (77.77, 80.0), (0.0, 1.2)]  # desordenados, uno al final


@unittest.skipUnless(np is not None and av is not None, "numpy y PyAV")
class DecodeRangosTests(unittest.TestCase):
    """Los tramos sueltos son los mismos que cortar la decodificación entera (decode_16k) por esos segundos."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fixlento-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def comparar(self, path, exacto=False):
        y = engines.decode_16k(path)
        partes = engines.decode_rangos_16k(path, RANGOS, round(len(y) / 16000, 1))
        self.assertEqual(len(partes), len(RANGOS))
        for (a, b), p in zip(RANGOS, partes):
            ref = y[int(round(a * 16000)):int(round(b * 16000))]
            with self.subTest(path=os.path.basename(path), tramo=(a, b)):
                self.assertLessEqual(abs(len(p) - len(ref)), 160)
                if exacto:
                    self.assertTrue(np.array_equal(p[:len(ref)], ref[:len(p)]))
                else:
                    self.assertEqual(desfase(ref, p), 0)
                    n = min(len(ref), len(p))
                    c = np.corrcoef(ref[200:n - 200].astype(float), p[200:n - 200].astype(float))[0, 1]
                    self.assertGreater(c, 0.98)

    def test_wav_16k_igual_muestra_a_muestra(self):
        p = os.path.join(self.tmp, "r.wav")
        escribir_wav(p, 120)
        self.comparar(p, exacto=True)

    def test_wav_48k_estereo(self):
        p = os.path.join(self.tmp, "r.wav")
        escribir_wav(p, 120, rate=48000, canales=2)
        self.comparar(p)

    def test_flac(self):
        p = os.path.join(self.tmp, "r.flac")
        escribir_av(p, 120, "flac")
        self.comparar(p)

    def test_m4a_aac(self):
        p = os.path.join(self.tmp, "r.m4a")
        escribir_av(p, 120, "aac", rate=44100, fmt="mp4")
        self.comparar(p)

    def test_flujos_sin_indice_no_se_saltan(self):
        """mp3, aac ADTS, amr…: el salto es por bytes y el tiempo, una estimación. Se decodifican enteros."""
        for ext in (".mp3", ".aac", ".amr", ".3ga", ".wma", ".aiff"):
            p = os.path.join(self.tmp, "r" + ext)
            escribir_wav(p, 3)  # el contenido da igual: no se llega a abrir
            with self.subTest(ext=ext), self.assertRaises(engines.SinSalto):
                engines.decode_rangos_16k(p, [(0.5, 1.0)], 3.0)

    def test_un_archivo_que_no_se_lee_no_revienta(self):
        p = os.path.join(self.tmp, "roto.m4a")
        with open(p, "wb") as f:
            f.write(b"\0" * 5000)
        with self.assertRaises(engines.SinSalto):
            engines.decode_rangos_16k(p, [(0.5, 1.0)], 3.0)

    def test_sin_duracion_no_se_salta(self):
        """Un sidecar sin duracion_s (o rara): sin con qué comprobar los tiempos, entera."""
        p = os.path.join(self.tmp, "r.wav")
        escribir_wav(p, 5)
        for d in (None, 0, "x", -1):
            with self.subTest(d=d), self.assertRaises(engines.SinSalto):
                engines.decode_rangos_16k(p, [(0.5, 1.0)], d)

    def test_hueco_en_los_tiempos(self):
        """Revisión de fix-lento (P0): con un hueco de 7 s en los tiempos (m4a, opus), decode_16k no lo rellena y lo de
        después no está donde dicen sus tiempos: por tramos, la huella salía de otro momento de la reunión."""
        casos = [("aac", "mp4", ".m4a", 44100)] + ([("libopus", "ogg", ".opus", 48000)]
                                                   if tiene_codificador("libopus") else [])
        for codec, fmt, ext, rate in casos:
            p = os.path.join(self.tmp, "hueco" + ext)
            escribir_av(p, 120, codec, rate=rate, fmt=fmt, hueco=(60.0, 7.0))
            with self.subTest(ext=ext), self.assertRaises(engines.SinSalto) as cm:
                engines.decode_rangos_16k(p, [(80.0, 82.0)], duracion(p))
            self.assertIn("no cuadran", str(cm.exception))

    def test_danado_a_mitad(self):
        """Un trozo dañado que el demuxer tira sin avisar (flac, opus): lo de después sale antes de tiempo."""
        casos = [("flac", "flac", ".flac", 16000)] + ([("libopus", "ogg", ".opus", 48000)]
                                                      if tiene_codificador("libopus") else [])
        for codec, fmt, ext, rate in casos:
            p = os.path.join(self.tmp, "roto" + ext)
            escribir_av(p, 120, codec, rate=rate, fmt=fmt)
            danar(p)
            with self.subTest(ext=ext), self.assertRaises(engines.SinSalto):
                engines.decode_rangos_16k(p, [(80.0, 82.0)], duracion(p))


@unittest.skipUnless(np is not None and av is not None, "numpy y PyAV")
class AudioHuellaTests(unittest.TestCase):
    """«Recordar»: la huella sale de los mismos tramos (los más largos, hasta 90 s), sin decodificar la reunión."""

    TRAMOS = [(1.0, 3.0), (10.0, 45.0), (50.0, 52.5), (60.0, 118.0), (5.0, 9.0)]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fixlento-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_tramos_huella_como_antes(self):
        """Los de siempre (fingerprint): los más largos primero, el último recortado a lo que falte hasta 90 s."""
        self.assertEqual(voices.tramos_huella(self.TRAMOS), [(60.0, 118.0), (10.0, 42.0)])
        self.assertEqual(voices.tramos_huella([]), [])

    def test_sin_decodificar_la_reunion(self):
        p = os.path.join(self.tmp, "reunion.wav")
        escribir_wav(p, 120)
        y = engines.decode_16k(p)
        esperado = np.concatenate([y[int(a * 16000):int(b * 16000)] for a, b in voices.tramos_huella(self.TRAMOS)])
        with mock.patch.object(voices, "load_audio_16k", side_effect=AssertionError("ha decodificado la reunión")):
            voz, por_tramos = voices.audio_huella(p, self.TRAMOS, duracion(p))
        self.assertTrue(por_tramos)
        self.assertLessEqual(abs(len(voz) - len(esperado)), 2)
        n = min(len(voz), len(esperado))
        self.assertTrue(np.array_equal(voz[:n - 2], esperado[:n - 2]))

    def test_formato_sin_indice_entera_como_antes(self):
        p = os.path.join(self.tmp, "reunion.mp3")
        escribir_wav(p, 120)  # FFmpeg mira el contenido: se lee como WAV, pero por la extensión no se salta
        y = engines.decode_16k(p)
        llamado = []
        real = voices.load_audio_16k

        def entera(path, cancelled=None, en_disco=None):
            llamado.append(path)
            return real(path, cancelled, en_disco)
        with mock.patch.object(voices, "load_audio_16k", side_effect=entera), mock.patch.object(voices, "log"):
            voz, por_tramos = voices.audio_huella(p, self.TRAMOS, duracion(p))
        self.assertFalse(por_tramos)
        self.assertEqual(llamado, [p])
        esperado = np.concatenate([y[int(a * 16000):int(b * 16000)] for a, b in voices.tramos_huella(self.TRAMOS)])
        self.assertTrue(np.array_equal(voz, esperado))

    def test_main_con_fingerprint_no_lee_la_reunion_entera(self):
        """voices.py --fingerprint (el subproceso de «Recordar»): con 0d5169b/de85236 empezaba por load_audio_16k."""
        with open(voices.__file__, encoding="utf-8") as f:
            src = f.read()
        main = src[src.index("def main(argv=None):"):]
        hasta = main[main.index('progress(stage="Leyendo audio…"'):]
        rama = hasta[hasta.index("        if a.fingerprint:"):]
        rama = rama[:rama.index("            return 0")]
        self.assertIn('audio_huella(a.diarize, tramos, data.get("duracion_s"), en_disco=tmp)', rama)
        self.assertNotIn("load_audio_16k", hasta[:hasta.index("        if a.fingerprint:")] + rama)

    def test_la_huella_sale_siempre_de_sus_segundos(self):
        """Lo que importa (es un dato biométrico de una persona concreta): por tramos o entera, la voz de la huella es
        la de esos segundos de decode_16k, la línea de tiempo con la que se diarizó. Con archivos limpios, con un hueco
        en los tiempos y dañados a mitad (m4a dañado: se lee por tramos y sigue en su sitio)."""
        casos = [("limpio.m4a", "aac", "mp4", 44100, None, False),
                 ("hueco.m4a", "aac", "mp4", 44100, (55.0, 7.0), False),
                 ("roto.flac", "flac", "flac", 16000, None, True), ("roto.m4a", "aac", "mp4", 44100, None, True)]
        if tiene_codificador("libopus"):
            casos += [("hueco.opus", "libopus", "ogg", 48000, (55.0, 7.0), False),
                      ("roto.opus", "libopus", "ogg", 48000, None, True)]
        for nombre, codec, fmt, rate, hueco, roto in casos:
            p = os.path.join(self.tmp, nombre)
            escribir_av(p, 120, codec, rate=rate, fmt=fmt, hueco=hueco)
            if roto:
                danar(p)
            y = engines.decode_16k(p)
            esperado = np.concatenate([y[int(a * 16000):int(b * 16000)] for a, b in voices.tramos_huella(self.TRAMOS)])
            with mock.patch.object(voices, "log"):
                voz, por_tramos = voices.audio_huella(p, self.TRAMOS, round(len(y) / 16000, 1))
            # tramo a tramo: como mucho 2 ms de desfase (tras un trozo dañado, decode_16k rellena con el silencio que
            # dicen sus paquetes y puede quedar ~1 ms: nada para una huella; la duración los separa de un desfase real)
            k = 0
            for a, b in voices.tramos_huella(self.TRAMOS):
                n = int(b * 16000) - int(a * 16000)
                ref, got = esperado[k:k + n], voz[k:k + n]
                k += n
                with self.subTest(nombre=nombre, por_tramos=por_tramos, tramo=(a, b)):
                    lag = desfase(ref, got, 4000)
                    self.assertLessEqual(abs(lag), 32)
                    self.assertGreater(parecido(ref, got, lag), 0.95)


class PrioridadTests(unittest.TestCase):
    """La huella de «Recordar» (sin trabajo: el usuario espera) por debajo de lo normal; las voces, en reposo."""

    def flags(self, job):
        visto = {}

        class P:
            pid = 4242
            stdout = iter([b'{"type": "result", "huellas": {}}\n'])

            def __init__(self, *a, **k):
                visto.update(k)

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass
        datos = tempfile.mkdtemp(prefix="fixlento-")
        self.addCleanup(shutil.rmtree, datos, True)
        with mock.patch.object(server.subprocess, "Popen", P), mock.patch.object(voices, "IDLE_PRIORITY", 0x40), \
                mock.patch.object(engines, "BELOW_NORMAL", 0x4000), mock.patch.object(engines, "NO_WINDOW", 0), \
                mock.patch.object(server, "DATA_DIR", datos):
            server.Jobs._voices_subprocess(types.SimpleNamespace(), job, ["--x"])
        return visto["creationflags"]

    def test_huella_por_debajo_de_lo_normal(self):
        self.assertEqual(self.flags(None), 0x4000)

    def test_identificar_voces_en_reposo(self):
        self.assertEqual(self.flags(types.SimpleNamespace(cancel=False, stage="", progress=0.0)), 0x40)


class PrecargaConApiTests(unittest.TestCase):
    """Con «API externa» no se deja cargado un Whisper que no hace falta (medium ~1 GB)."""

    def test_la_cola_en_el_pc_no_se_precarga_con_api(self):
        cached = lambda m: True  # noqa: E731
        st = {"procesado": "api", "live_preset": "nube", "preset": "rapido"}
        self.assertIsNone(server.preload_model(st, cached))
        self.assertIsNone(server.preload_model(dict(st, preset="nube"), cached))

    def test_el_directo_en_el_pc_si(self):
        """Si el directo es en el PC (elegido así en Motores), su primer trozo no espera a la carga."""
        cached = lambda m: True  # noqa: E731
        self.assertEqual(server.preload_model({"procesado": "api", "live_preset": "medium", "preset": "nube"},
                                              cached), ("live", "medium"))

    def test_sin_api_como_siempre(self):
        cached = lambda m: True  # noqa: E731
        for p in ("local", "agente"):
            with self.subTest(p=p):
                self.assertEqual(server.preload_model({"procesado": p, "live_preset": "nube", "preset": "rapido"},
                                                      cached), ("batch", "small"))


class DirectoCaeAlPcTests(TempTree):
    def test_se_dice_en_el_log(self):
        self.wav("Alfa/r.wav")
        j = bare_jobs([])
        j.settings = settings_store(tempfile.mkdtemp(dir=self.tmp), procesado="api", live_preset="nube",
                                    cloud=dict(server.DEFAULT_SETTINGS["cloud"], api_key=""))
        j.library = types.SimpleNamespace(find=lambda rel: None, at=1)
        lineas = []
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "log", lineas.append):
            j.start_live("Alfa/r.wav", "nube")
        self.assertEqual(j.list[-1].preset, "medium")
        caida = [x for x in lineas if "la nube no está lista" in x]
        self.assertEqual(len(caida), 1, lineas)
        self.assertIn("se transcribe en este PC con medium", caida[0])
        # y sale en «Copiar diagnóstico» con su parte técnica, no como <texto>
        self.assertIn("directo: la nube no está lista", server.diag_line(caida[0]))


def sidecar(segs):
    return {"version": 1, "modelo": "x", "creado": "2026-11-10T10:00:00", "duracion_s": 60.0,
            "tramos": [{"inicio": a, "fin": b, "h": h} for a, b, h in segs],
            "hablantes": {"h1": {"persona": None, "score": None, "segundos": 30.0, "provisional": False,
                                 "breve": False},
                          "h2": {"persona": None, "score": None, "segundos": 30.0, "provisional": False,
                                 "breve": False}},
            "tu": None, "frases": [], "micro": None}


@unittest.skipUnless(sync_playwright, "Playwright no instalado")
class GuardandoAlMomentoTests(unittest.TestCase):
    """Chromium contra el servidor de verdad, con /api/voces/persona retenido 2,5 s (lo que tarda una huella en un PC
    ocupado): a los 0,3 s del clic el cuadro ya dice «Guardando…» y no deja volver a pulsar; al acabar, el aviso. Con
    de85236 seguía diciendo «Guardar», sin nada más, hasta que acababa."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_guardar_nombre(self):
        s = Servidor()
        self.addCleanup(s.cerrar)
        d = os.path.join(s.root, "Proyecto", "2026-11-10 Comité")
        os.makedirs(d)
        base = os.path.join(d, "2026-11-10 10-00-00 - Comité")
        with wave.open(base + ".wav", "wb") as w:  # el audio da igual: sin «Recordar» no se lee
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * 16000 * 60)
        segs = [(k * 6.0, k * 6.0 + 5.5, "h1" if k % 2 == 0 else "h2") for k in range(10)]
        with open(base + ".srt", "w", encoding="utf-8") as f:
            for k, (a, b, _) in enumerate(segs, 1):
                f.write(f"{k}\n00:00:{int(a):02d},000 --> 00:00:{int(b):02d},500\nFrase número {k} de la reunión.\n\n")
        with open(base + ".hablantes.json", "w", encoding="utf-8") as f:
            json.dump(sidecar(segs), f)
        rel = "Proyecto/2026-11-10 Comité/2026-11-10 10-00-00 - Comité.wav"
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        errores = []
        pg.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
        pg.goto(f"{s.base}/?t={s.token}", wait_until="load")
        pg.wait_for_selector("#main h1", timeout=15000)
        pg.evaluate("p => go('a', p)", rel)
        pg.wait_for_selector("#aTabs [data-tab=tx]", timeout=15000)
        pg.click("#aTabs [data-tab=tx]")
        pg.wait_for_selector("#aTx .spk", timeout=15000)
        pg.locator("#aTx .spk").first.click()
        pg.fill(".spk-pop #spName", "Javier")
        # /api/voces/persona tarda 2,5 s en contestar (en la página, sin bloquear Playwright)
        pg.evaluate("""() => { const f = window.fetch; window.fetch = (u, o) => String(u).includes('/api/voces/persona')
            ? new Promise(r => setTimeout(r, 2500)).then(() => f(u, o)) : f(u, o); }""")
        pg.evaluate("""() => { window.__t = []; new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n =>
            window.__t.push(n.textContent)))).observe(document.querySelector('#toasts'), {childList: true}); }""")
        t0 = time.time()
        pg.click('.spk-pop [data-sa="name"]')
        time.sleep(0.3)
        boton = pg.text_content('.spk-pop [data-sa="name"]')
        estado = pg.text_content(".spk-pop .sp-estado")
        desactivado = pg.evaluate("() => document.querySelector('.spk-pop [data-sa=me]').disabled")
        self.assertEqual((boton, estado, desactivado), ("Guardando…", "Guardando…", True))
        # un clic fuera no lo cierra mientras guarda (se perdería el aviso y se podría volver a enviar desde otro)
        pg.mouse.click(1200, 880)
        self.assertEqual(pg.locator(".spk-pop[data-busy]").count(), 1)
        pg.wait_for_function("() => window.__t.length > 0", timeout=15000)
        self.assertGreater(time.time() - t0, 2.0)  # de verdad estaba retenido
        self.assertIn("Javier", pg.evaluate("() => window.__t[0]"))
        self.assertEqual(pg.locator(".spk-pop").count(), 0)
        self.assertEqual(errores, [])


if __name__ == "__main__":
    unittest.main()
