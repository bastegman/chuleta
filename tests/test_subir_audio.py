# -*- coding: utf-8 -*-
"""«Subir audio» (09/11/2026, msg 1296 de Bernardo: «poder subir un audio, independiente de donde venga»).

- Los formatos del móvil y de los mensajeros se leen con la cadena de la cola (engines.decode_16k, PyAV): los audios
  de `audio_formatos/` (señal sintética de tres pitidos, ver generar.py) se decodifican enteros y en su sitio.
- Un paquete que no se puede decodificar ya no corta el audio: se cambia por silencio (antes, un AMR de un Android
  antiguo se quedaba en 11,8 s de 53, y un m4a con un trozo dañado, en la mitad, sin decir nada).
- El plan de la tarjeta al subir (plan.resolver con `subir`) y POST /api/subir de punta a punta contra el servidor de
  verdad: dónde se guarda, con qué nombre y fecha, su .md y la cola; y los archivos que no valen, con su motivo.
- La interfaz en Chromium: el botón «Subir audio», el selector de archivos, la tarjeta, soltar archivos, varios a la
  vez y los errores, sin errores de JavaScript.

Lo que necesita PyAV o Playwright se salta sin ellos; ui-smoke.yml instala los dos y falla si algo se salta.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.error
from unittest import mock
import urllib.parse
import urllib.request
import wave

from support import REPO, engines, patched_paths, server

import plan as grabar_plan

try:
    import av  # noqa: F401
    import numpy as np
except ImportError:  # pragma: no cover - sin PyAV (el job «tests» de la CI) se saltan los de formatos
    av = np = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

FIX = os.path.join(REPO, "tests", "audio_formatos")
PITIDOS = ((0.28, 0.42), (0.78, 0.92), (1.28, 1.42))  # dentro de los tres pitidos de generar.py
SILENCIOS = ((0.55, 0.65), (1.05, 1.15))
# (archivo de audio_formatos, extensiones con las que llega: el contenido manda, pero la extensión la tiene que aceptar)
FORMATOS = [
    ("iphone_notas_de_voz.m4a", (".m4a",)),  # Notas de voz del iPhone (AAC, codificador de Apple)
    ("iphone_sin_perdida.m4a", (".m4a",)),  # Notas de voz «sin pérdida» (ALAC)
    ("android_grabadora.m4a", (".m4a", ".3ga")),  # Grabadora de Android; .3ga, las grabadoras de llamadas
    ("whatsapp.opus", (".opus", ".ogg", ".oga", ".waptt")),  # WhatsApp (móvil .opus, escritorio .ogg), Telegram .oga
    ("vorbis.ogg", (".ogg",)),
    ("mp3.mp3", (".mp3",)),
    ("adts.aac", (".aac",)),
    ("navegador.webm", (".webm",)),
    ("flac.flac", (".flac",)),
    ("wma.wma", (".wma",)),
    ("apple.caf", (".caf",)),
    ("aiff.aiff", (".aiff", ".aif")),
    ("video.mp4", (".mp4", ".mov", ".qta")),  # vídeo del móvil; .qta, las Notas de voz con capas
]


def puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def leer(archivo):
    with open(os.path.join(FIX, archivo), "rb") as f:
        return f.read()


def rms(y, a, b):
    seg = y[int(a * 16000):int(b * 16000)].astype("float32") / 32768
    return float((seg ** 2).mean() ** 0.5) if len(seg) else -1.0


def wav24(path, segundos=1.6):
    """Un WAV de 24 bits con los mismos pitidos (los WAV no hace falta guardarlos: se escriben aquí)."""
    from math import pi, sin
    frames = bytearray()
    for i in range(int(segundos * 16000)):
        t, v = i / 16000, 0.0
        for (a, b), f in zip(((0.2, 0.5), (0.7, 1.0), (1.2, 1.5)), (440, 880, 660)):
            if a <= t < b:
                v = 0.5 * min(1.0, (t - a) / 0.01, (b - t) / 0.01) * sin(2 * pi * f * t)
        frames += int(v * 8388607).to_bytes(3, "little", signed=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(16000)
        w.writeframes(bytes(frames))


def danado(src, dst, desde=0.45, n=400):
    """Copia con `n` bytes cambiados a partir de esa fracción del archivo (un trozo dañado a mitad)."""
    with open(src, "rb") as f:
        b = bytearray(f.read())
    i = int(len(b) * desde)
    for k in range(i, min(len(b), i + n)):
        b[k] = (b[k] * 7 + 13) & 0xFF
    with open(dst, "wb") as f:
        f.write(b)


# ----------------------------------------------------------------------------- formatos (PyAV de verdad)

@unittest.skipUnless(av, "PyAV no instalado")
class FormatosTests(unittest.TestCase):
    """Lo que de verdad lee la cadena de la cola: cada formato, entero y en su sitio."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tenca-formatos-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def comprobar(self, path, nombre):
        dur = engines.probar_audio(path)
        self.assertIsNotNone(dur, nombre)
        self.assertAlmostEqual(dur, 1.6, delta=0.25, msg=nombre)
        y = engines.decode_16k(path)
        self.assertAlmostEqual(len(y) / 16000, 1.6, delta=0.1, msg=nombre)
        for a, b in PITIDOS:  # los pitidos suenan donde tienen que sonar
            self.assertGreater(rms(y, a, b), 0.2, f"{nombre}: pitido {a}-{b} s")
        for a, b in SILENCIOS:
            self.assertLess(rms(y, a, b), 0.05, f"{nombre}: silencio {a}-{b} s")

    def test_cada_formato_se_lee_entero_y_en_su_sitio(self):
        vistos = set()
        for archivo, exts in FORMATOS:
            for ext in exts:
                self.assertIn(ext, server.AUDIO_EXT, f"{ext} no se acepta al subir")
                vistos.add(ext)
                dst = os.path.join(self.tmp, os.path.splitext(archivo)[0] + ext)
                shutil.copyfile(os.path.join(FIX, archivo), dst)
                with self.subTest(archivo=archivo, ext=ext):
                    self.comprobar(dst, os.path.basename(dst))
        p = os.path.join(self.tmp, "grabadora.wav")
        wav24(p)
        self.comprobar(p, "WAV de 24 bits")
        vistos.add(".wav")
        # lo pedido (msg 1296): m4a, opus/ogg, mp3, wav, aac, webm, flac y mp4, y más
        self.assertTrue({".m4a", ".opus", ".ogg", ".mp3", ".wav", ".aac", ".webm", ".flac", ".mp4"} <= vistos)

    def test_amr_que_empieza_en_silencio_se_acepta(self):
        """Un AMR con DTX empieza con segundos de tramas SID y NO_DATA que FFmpeg no decodifica: se acepta al subirlo,
        porque la cola lo transcribe (revisión de f40e6b7: se rechazaba al contar 50 paquetes malos)."""
        import random
        rnd = random.Random(7)
        voz = lambda: bytes([0x3C]) + bytes(rnd.randrange(256) for _ in range(31))  # noqa: E731
        sid = lambda: bytes([0x44]) + bytes(rnd.randrange(256) for _ in range(5))  # noqa: E731
        sin_datos = bytes([0x7C])  # FT 15, NO_DATA
        for nombre, inicio, n in (("sid.amr", b"".join(sid() for _ in range(60)), 60),
                                  ("dtx.amr", (sid() + sin_datos * 7) * 10, 80)):
            p = os.path.join(self.tmp, nombre)
            with open(p, "wb") as f:
                f.write(b"#!AMR\n" + inicio + b"".join(voz() for _ in range(250)))
            with self.subTest(nombre=nombre):
                self.assertIsNotNone(engines.probar_audio(p))
                self.assertAlmostEqual(len(engines.decode_16k(p)) / 16000, (n + 250) * 0.02, delta=0.1)
        p = os.path.join(self.tmp, "solo_sid.amr")  # todo silencio de tramas: no hay nada que transcribir
        with open(p, "wb") as f:
            f.write(b"#!AMR\n" + b"".join(sid() for _ in range(100)))
        with self.assertRaises(engines.EngineError):
            engines.probar_audio(p)

    def test_amr_de_un_android_antiguo_entero(self):
        """Un AMR con tramas SID (FFmpeg no las implementa: «patches welcome»): antes el audio acababa en la primera.
        105 tramas de 20 ms = 2,1 s; con el generador de c.decode() salía 1,0 s."""
        for ext in (".3gp", ".3gpp"):
            dst = os.path.join(self.tmp, "android" + ext)
            shutil.copyfile(os.path.join(FIX, "android_antiguo.3gp"), dst)
            self.assertIn(ext, server.AUDIO_EXT)
            self.assertIsNotNone(engines.probar_audio(dst))
            y = engines.decode_16k(dst)
            self.assertAlmostEqual(len(y) / 16000, 2.1, delta=0.05)
        # el mismo AMR sin contenedor (.amr), tal como lo escribe generar.py
        amr = os.path.join(self.tmp, "nota.amr")
        with av.open(os.path.join(FIX, "android_antiguo.3gp")) as c, av.open(amr, "w", format="amr") as out:
            s = c.streams.audio[0]
            o = out.add_stream_from_template(s)
            for pk in c.demux(s):
                if pk.dts is None:
                    continue
                pk.stream = o
                out.mux(pk)
        self.assertIn(".amr", server.AUDIO_EXT)
        self.assertAlmostEqual(len(engines.decode_16k(amr)) / 16000, 2.1, delta=0.05)

    def test_amr_con_un_minuto_de_tramas_no_data_sigue_en_su_sitio(self):
        """fix-subir-memoria, revisión 2 (P1): con el relleno acotado por la duración de la cabecera, un .amr crudo
        perdía su línea de tiempo, porque FFmpeg la estima por el bitrate y con DTX se queda muy corta. Aquí: 2,1 s de
        voz, 3.000 tramas NO_DATA (1 byte, 20 ms, que FFmpeg no decodifica) y la voz otra vez = 64,2 s; FFmpeg estima
        6,5 s y así salían 18,6 s. Las tramas de un códec se rellenan siempre: el texto y las voces casan con el
        audio."""
        uno = os.path.join(self.tmp, "uno.amr")
        with av.open(os.path.join(FIX, "android_antiguo.3gp")) as c, av.open(uno, "w", format="amr") as out:
            s = c.streams.audio[0]
            o = out.add_stream_from_template(s)
            for pk in c.demux(s):
                if pk.dts is None:
                    continue
                pk.stream = o
                out.mux(pk)
        with open(uno, "rb") as f:
            voz = f.read()[len(b"#!AMR\n"):]
        amr = os.path.join(self.tmp, "dtx.amr")
        with open(amr, "wb") as f:
            f.write(b"#!AMR\n" + voz + b"\x7c" * 3000 + voz)
        y = engines.decode_16k(amr)
        self.assertAlmostEqual(len(y) / 16000, 2.1 + 60 + 2.1, delta=0.05)
        with mock.patch.object(engines, "SILENCIO_LIBRE_S", 1.0):  # ni con el tope total mínimo: son tramas
            self.assertAlmostEqual(len(engines.decode_16k(amr)) / 16000, 64.2, delta=0.05)

    def test_un_trozo_danado_a_mitad_no_corta_el_audio(self):
        """Antes, el primer paquete dañado terminaba el audio sin decirlo (medido con estos: mp3 0,71 s de 1,6,
        m4a 0,79, aac 0,77). Ahora llega hasta el final, como ffmpeg. En un .aac (ADTS) el trozo dañado se lo come el
        demultiplexor y no se puede rellenar: queda lo mismo que con ffmpeg (1,16 s), no más."""
        for archivo, minimo in (("mp3.mp3", 1.45), ("android_grabadora.m4a", 1.45), ("adts.aac", 1.1)):
            dst = os.path.join(self.tmp, "danado_" + archivo)
            danado(os.path.join(FIX, archivo), dst, n=200)
            with self.subTest(archivo=archivo):
                y = engines.decode_16k(dst)
                self.assertGreater(len(y) / 16000, minimo, archivo)
                if minimo > 1.42:
                    self.assertGreater(rms(y, *PITIDOS[2]), 0.2, f"{archivo}: el último pitido sigue ahí")

    def test_lo_que_no_se_puede_leer_dice_por_que(self):
        casos = {"video_sin_audio.mp4": "no tiene sonido: es un vídeo sin pista de audio"}
        for archivo, motivo in casos.items():
            with self.assertRaises(engines.EngineError) as cm:
                engines.probar_audio(os.path.join(FIX, archivo))
            self.assertEqual(str(cm.exception), motivo)
        roto = os.path.join(self.tmp, "roto.m4a")
        with open(roto, "wb") as f:
            f.write(os.urandom(20000))
        with self.assertRaises(engines.EngineError) as cm:
            engines.probar_audio(roto)
        self.assertEqual(str(cm.exception), engines.NO_ES_AUDIO)
        vacio = os.path.join(self.tmp, "vacio.mp3")
        open(vacio, "wb").close()
        with self.assertRaises(engines.EngineError):
            engines.probar_audio(vacio)


class PaquetesTests(unittest.TestCase):
    """decode_16k paquete a paquete, con dobles (sin PyAV): un paquete malo es silencio de su duración y se sigue."""

    def test_un_paquete_malo_es_silencio_y_se_sigue(self):
        np_ = __import__("numpy")

        class FFmpegError(Exception):
            pass

        class Frame:
            def __init__(self, v):
                self.v = v

            def to_ndarray(self):
                return np_.full((1, 1600), self.v, np_.int16)

        class Stream:
            type = "audio"
            codec_context = types.SimpleNamespace(frame_size=0, sample_rate=16000)

            def decode(self, pk):
                if pk.malo:
                    raise FFmpegError("patches welcome")
                return [Frame(pk.v)]

        pk = lambda v, malo=False: types.SimpleNamespace(v=v, malo=malo, duration=1600, time_base=1 / 16000)  # noqa

        class Container:
            streams = [Stream()]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def demux(self, s):
                return iter([pk(1), pk(2, True), pk(3)])

        class Resampler:
            def __init__(self, **kw):
                pass

            def resample(self, fr):
                return [fr] if fr is not None else []

        fake_av = types.SimpleNamespace(open=lambda path: Container(), AudioResampler=Resampler,
                                        error=types.SimpleNamespace(FFmpegError=FFmpegError))
        with mock.patch.dict(sys.modules, {"av": fake_av}):
            y = engines.decode_16k("x.m4a")
        self.assertEqual(len(y), 4800)  # 0,1 s + 0,1 s de silencio + 0,1 s: el tercero sigue en su sitio
        self.assertEqual((y[0], y[1600], y[3200]), (1, 0, 3))

    def test_una_duracion_rota_no_pide_horas_de_silencio(self):
        class FFmpegError(Exception):
            pass

        class Stream:
            type = "audio"
            codec_context = types.SimpleNamespace(frame_size=0, sample_rate=16000)

            def decode(self, pk):
                if pk.malo:
                    raise FFmpegError("x")
                return [types.SimpleNamespace(to_ndarray=lambda: __import__("numpy").ones((1, 160), "int16"))]

        class Container:
            streams = [Stream()]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def demux(self, s):  # un paquete malo que dice durar 10 horas
                malo = types.SimpleNamespace(malo=True, duration=36000, time_base=1)
                return iter([types.SimpleNamespace(malo=False), malo])

        fake_av = types.SimpleNamespace(open=lambda path: Container(),
                                        AudioResampler=lambda **kw: types.SimpleNamespace(
                                            resample=lambda fr: [fr] if fr is not None else []),
                                        error=types.SimpleNamespace(FFmpegError=FFmpegError))
        with mock.patch.dict(sys.modules, {"av": fake_av}):
            y = engines.decode_16k("x.m4a")
        self.assertEqual(len(y), 160 + int(engines.SILENCIO_MAX_S * 16000))

    def test_si_no_se_puede_leer_nada_es_un_error_y_no_silencio(self):
        class FFmpegError(Exception):
            pass

        class Stream:
            type = "audio"
            codec_context = types.SimpleNamespace(frame_size=160, sample_rate=8000)

            def decode(self, pk):
                raise FFmpegError("x")

        class Container:
            streams = [Stream()]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def demux(self, s):
                return iter([types.SimpleNamespace(duration=None, time_base=None)] * 5)

        fake_av = types.SimpleNamespace(open=lambda path: Container(),
                                        AudioResampler=lambda **kw: types.SimpleNamespace(resample=lambda fr: []),
                                        error=types.SimpleNamespace(FFmpegError=FFmpegError))
        with mock.patch.dict(sys.modules, {"av": fake_av}):
            with self.assertRaises(engines.EngineError):
                engines.decode_16k("x.amr")


# ----------------------------------------------------------------------------- plan y fecha (lógica pura)

class PlanSubirTests(unittest.TestCase):
    LISTAS = [{"id": "p1", "project": "Alfa", "name": "General", "n": 3}]
    MOTORES = {"presets": {p["id"]: p for p in engines.PRESETS if p["engine"] == "local"}, "procesado": "local"}

    def plan(self, pedido, st=None, **kw):
        st = dict({"preset": "turbo", "live_preset": "medium", "language": "es-en"}, **(st or {}))
        return grabar_plan.resolver(st, self.LISTAS, {"name": "Reunión de Teams", "source": "teams"}, [],
                                    pedido, carpeta_de=lambda v: "Alfa" if v else "", motores=self.MOTORES, **kw)

    def test_sin_audio_con_la_cola_y_sin_teams(self):
        p = self.plan({"subir": True, "nombre": "Nota de voz 12", "archivo": "Nota de voz 12"})
        claves = [x["clave"] for x in p["lineas"]]
        self.assertNotIn("audio", claves)
        self.assertEqual(claves, ["carpeta", "idioma", "vocab", "contexto", "transcribe", "resume"])
        t = next(x for x in p["lineas"] if x["clave"] == "transcribe")
        self.assertEqual((t["preset"], t["live"]), ("turbo", False))  # el de la cola (Ajustes › Procesado)
        self.assertIn("nada más subirlo", t["valor"])
        self.assertEqual(p["nombre"], {"valor": "Nota de voz 12", "origen": "archivo",
                                       "texto": "del nombre del archivo"})
        self.assertEqual(p["fuente"], "")  # Teams no cuenta: el audio ya existe
        self.assertEqual(set(p["rec"]), {"name", "vocab", "folder", "language", "contexto", "preset", "plan"})

    def test_lo_cambiado_y_lo_que_no_existe(self):
        p = self.plan({"subir": True, "nombre": "Comité", "cambios": {"preset": "rapido"}})
        t = next(x for x in p["lineas"] if x["clave"] == "transcribe")
        self.assertEqual((t["preset"], t["origen"]), ("rapido", "reunion"))
        self.assertEqual(p["nombre"]["origen"], "elegido")
        p = self.plan({"subir": True}, st={"preset": "nube"})  # la nube apagada: el rápido
        self.assertEqual(p["rec"]["preset"], "rapido")

    def test_grabar_sigue_igual(self):
        p = self.plan({"nombre": "Comité"})
        self.assertIn("audio", [x["clave"] for x in p["lineas"]])
        self.assertEqual(p["rec"]["preset"], "medium")
        self.assertIn("mic", p["rec"])

    def test_varios_una_carpeta_por_audio(self):
        reunion = lambda base, nombre: f"{base}/2026-11-09 16-00"  # noqa: E731
        p = self.plan({"subir": True, "varios": True}, carpeta_reunion=reunion)
        c = next(x for x in p["lineas"] if x["clave"] == "carpeta")
        self.assertTrue(c["valor"].endswith("· cada audio en su carpeta"), c["valor"])
        p = self.plan({"subir": True, "nombre": "Comité"}, carpeta_reunion=reunion)
        self.assertIn("2026-11-09 16-00", next(x for x in p["lineas"] if x["clave"] == "carpeta")["valor"])


@unittest.skipUnless(shutil.which("git") and os.path.exists(os.path.join(REPO, ".git")), "sin copia de trabajo de git")
class GitignoreFixturesTests(unittest.TestCase):
    """Los audios de prueba van al repo; los de una reunión, ni siquiera dentro de tests/audio_formatos."""

    def ignorado(self, p):
        r = subprocess.run(["git", "-C", REPO, "check-ignore", "--no-index", "-q", p], capture_output=True, timeout=60)
        return r.returncode == 0

    def test_fixtures_si_reuniones_no(self):
        for n in os.listdir(FIX):
            self.assertFalse(self.ignorado(f"tests/audio_formatos/{n}"), n)
        for p in ("tests/audio_formatos/2026-11-08 10-15-32 - Comite.m4a", "tests/otra.m4a", "tests/nota.amr",
                  "tests/Proyecto/voz.3gp", "_app/static/voz.caf", "tests/x.waptt",
                  "Proyecto/2026-11-08 Comite/2026-11-08 10-15-32 - Comite.opus", "nota.amr.txt",
                  "tests/audio_formatos/.DS_Store", "tests/audio_formatos/x.hablantes.json"):
            self.assertTrue(self.ignorado(p), p)


class EtiquetaTests(unittest.TestCase):
    """El árbol quita el prefijo de las grabadoras (AUDIO_, VID-, REC) pero no la primera palabra de un nombre."""

    def test_prefijos_y_nombres(self):
        tmp = tempfile.mkdtemp(prefix="tenca-etiqueta-")
        self.addCleanup(shutil.rmtree, tmp, True)
        nombres = {"2026-11-08 10-15-32 - Mensaje de voz de WhatsApp.ogg": "Mensaje de voz de WhatsApp",
                   "2026-11-08 10-15-32 - Audio de la junta.m4a": "Audio de la junta",
                   "2026-11-08 10-15-32 - Recursos humanos.m4a": "Recursos humanos",
                   "2026-11-08 10-15-32 - Videollamada.mp4": "Videollamada",
                   "AUDIO_2026-11-08_10-15-32.m4a": "", "REC 2026-11-08 10-15-32.wav": "",
                   "audio_2026-11-08_10-15-32.ogg": "", "Rec-2026-11-08-10-15-32.mp3": "",
                   "2026-11-08 10-15-32 - Audio.m4a": "Audio", "2026-11-08 10-15-32 - Rec.mp3": "Rec",
                   "REC_Comité 2026-11-08 10-15-32.wav": "Comité", "2026-11-08 10-15-32 - VID 12.mp4": "VID 12",
                   "Audio de WhatsApp 2026-11-08 10-15-32.ogg": "Audio de WhatsApp",
                   "Rec semanal 2026-11-08 10-15-32.m4a": "Rec semanal",
                   "VID-2026-11-08-10-15-32 Comité.mp4": "Comité"}
        for n in nombres:
            open(os.path.join(tmp, n), "wb").close()
        with patched_paths(ROOT=tmp):
            flat = server.scan()[1]
        got = {a["name"]: a.get("label") for a in flat}
        self.assertEqual(got, nombres)


class FechaSubidaTests(unittest.TestCase):
    def test_formatos_y_limites(self):
        t = time.mktime((2026, 11, 8, 10, 15, 0, 0, 0, -1))
        self.assertEqual(server.fecha_subida(str(int(t * 1000))), t)  # File.lastModified (ms)
        self.assertEqual(server.fecha_subida(t), t)
        self.assertEqual(server.fecha_subida("2026-11-08T10:15"), t)  # <input type="datetime-local">
        self.assertEqual(server.fecha_subida("2026-11-08T10:15:00"), t)
        for malo in ("", None, "ayer", "1999-01-01T00:00", str((time.time() + 5 * 86400) * 1000), True, float("nan")):
            self.assertAlmostEqual(server.fecha_subida(malo), time.time(), delta=5, msg=repr(malo))


# ----------------------------------------------------------------------------- el servidor de verdad

class Servidor:
    """server.py en un proceso aparte sobre una carpeta temporal (como test_ui_smoke)."""

    def __init__(self, ajustes=None, env=None):
        self.tmp = tempfile.mkdtemp(prefix="tenca-subir-")
        self.root = os.path.join(self.tmp, "Tenca")
        shutil.copytree(os.path.join(REPO, "_app"), os.path.join(self.root, "_app"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(self.root, "Proyecto", "Vocabulario"))
        with open(os.path.join(self.root, "Proyecto", "Vocabulario", "General.txt"), "w", encoding="utf-8") as f:
            f.write("término uno, término dos\n")
        self.datos = os.path.join(self.tmp, "datos")
        if ajustes:
            os.makedirs(self.datos)
            with open(os.path.join(self.datos, "settings.json"), "w", encoding="utf-8") as f:
                json.dump(ajustes, f)
        self.port = puerto_libre()
        self.log = open(os.path.join(self.tmp, "salida.log"), "w", encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8",
                   CHULETA_PRUEBAS_URL_MODELOS="http://127.0.0.1:9/{repo}/resolve/{rev}/{file}", **(env or {}))
        self.proc = subprocess.Popen([sys.executable, os.path.join(self.root, "_app", "server.py"), "--port",
                                      str(self.port), "--data", self.datos, "--no-browser"],
                                     cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT, env=env)
        self.base = f"http://127.0.0.1:{self.port}"
        token_file, fin, self.token = os.path.join(self.datos, "sesion.token"), time.time() + 60, None
        while time.time() < fin and self.proc.poll() is None:
            if os.path.isfile(token_file):
                with open(token_file, encoding="utf-8") as f:
                    self.token = f.read().strip()
                try:
                    if self.get("/api/ping").get("app") == "audios":
                        return
                except Exception:
                    pass
            time.sleep(0.3)
        self.cerrar()
        raise unittest.SkipTest("el servidor de prueba no arrancó")

    def cabeceras(self, extra=None):
        return dict({"X-Chuleta-Token": self.token, "X-Audios": "1"}, **(extra or {}))

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers=self.cabeceras())
        return json.load(urllib.request.urlopen(req, timeout=10))

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers=self.cabeceras({"Content-Type": "application/json"}))
        return json.load(urllib.request.urlopen(req, timeout=20))

    def subir(self, datos, **q):
        """POST /api/subir; devuelve (código, json, segundos)."""
        url = self.base + "/api/subir?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, data=datos, method="POST", headers=self.cabeceras())
        t0 = time.time()
        try:
            r = urllib.request.urlopen(req, timeout=30)
            return r.status, json.load(r), time.time() - t0
        except urllib.error.HTTPError as e:
            return e.code, json.load(e), time.time() - t0

    def cerrar(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


@unittest.skipUnless(av, "PyAV no instalado (sin él el servidor no comprueba el audio)")
class SubirEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Servidor()

    @classmethod
    def tearDownClass(cls):
        cls.s.cerrar()

    def leer(self, archivo):
        return leer(archivo)

    def ocultos(self):
        return [os.path.join(d, f) for d, _, fs in os.walk(self.s.root) for f in fs if f.startswith(".subiendo")]

    def test_un_audio_de_whatsapp_entra_como_una_reunion(self):
        vocab = next(v for v in self.s.get("/api/vocab")["items"] if v["project"] == "Proyecto")["id"]
        cuando = time.mktime((2026, 11, 8, 10, 15, 32, 0, 0, -1))
        code, r, _ = self.s.subir(self.leer("whatsapp.opus"), folder="Proyecto", name="Llamada con Ana",
                                  fecha=str(int(cuando * 1000)), vocab=vocab, language="es",
                                  contexto="Revisión del contrato", preset="rapido", archivo="PTT-20261108-WA0003.opus")
        self.assertEqual(code, 200, r)
        self.assertEqual(r["path"],
                         "Proyecto/2026-11-08 Llamada con Ana/2026-11-08 10-15-32 - Llamada con Ana.opus")
        self.assertEqual(r["carpeta"], "Proyecto/2026-11-08 Llamada con Ana")
        self.assertAlmostEqual(r["duracion"], 1.6, delta=0.2)
        self.assertIsNone(r["aviso"])
        p = os.path.join(self.s.root, *r["path"].split("/"))
        with open(p, "rb") as f:
            self.assertEqual(f.read(), self.leer("whatsapp.opus"))  # el archivo, tal cual
        self.assertAlmostEqual(os.path.getmtime(p), cuando, delta=1)  # con la fecha del audio
        with open(os.path.splitext(p)[0] + ".md", encoding="utf-8") as f:
            md = f.read()
        self.assertIn("idioma: es", md)
        self.assertIn("contexto: Revisión del contrato", md)
        self.assertIn("proyecto: Proyecto", md)
        self.assertIn("workstream: General", md)
        jobs = self.s.get("/api/jobs")["jobs"]
        self.assertTrue(any(j["path"] == r["path"] and j["id"] == r["job"] for j in jobs), jobs)
        self.assertEqual(self.ocultos(), [])
        # el mismo otra vez: su propia carpeta, nunca el .txt de otro
        code, r2, _ = self.s.subir(self.leer("whatsapp.opus"), folder="Proyecto", name="Llamada con Ana",
                                   fecha=str(int(cuando * 1000)), archivo="otra.opus", preset="rapido")
        self.assertEqual(code, 200, r2)
        self.assertEqual(r2["carpeta"], "Proyecto/2026-11-08 Llamada con Ana (2)")

    def test_lo_que_no_vale_no_se_guarda_y_dice_por_que(self):
        antes = sorted(os.listdir(os.path.join(self.s.root, "Proyecto")))
        casos = [
            (b"%PDF-1.4 hola", "acta.pdf", "«acta.pdf» no es un audio que Tenca sepa leer"),
            (os.urandom(30000), "Nota de voz 3.m4a", "«Nota de voz 3.m4a» no parece un audio, o está dañado."),
            (b"", "vacio.mp3", "«vacio.mp3» está vacío."),
            (self.leer("video_sin_audio.mp4"), "pantalla.mp4", "«pantalla.mp4» no tiene sonido: es un vídeo sin pista"),
        ]
        for datos, nombre, texto in casos:
            with self.subTest(nombre=nombre):
                code, r, seg = self.s.subir(datos, folder="Proyecto", name="X", archivo=nombre)
                self.assertEqual(code, 400, r)
                self.assertIn(texto, r["error"])
                self.assertLess(seg, 4)  # contesta al momento (no espera a vaciar el cuerpo)
        self.assertEqual(sorted(os.listdir(os.path.join(self.s.root, "Proyecto"))), antes)
        self.assertEqual(self.ocultos(), [])
        # la conexión sigue sirviendo después de un rechazo
        self.assertEqual(self.s.get("/api/ping").get("app"), "audios")

    def test_content_length_negativo(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.s.port, timeout=10)
        self.addCleanup(c.close)
        c.putrequest("POST", "/api/subir?folder=&archivo=a.mp3")
        for k, v in self.s.cabeceras({"Content-Length": "-5"}).items():
            c.putheader(k, v)
        c.endheaders()
        r = c.getresponse()
        self.assertEqual(r.status, 400)
        self.assertIn("Cuerpo no válido", json.loads(r.read())["error"])

    def test_restos_viejos_de_una_subida_se_borran(self):
        """Lo que quedó a medias (la app se cerró mientras llegaba) se borra al subir otro a esa carpeta, si es
        viejo."""
        carpeta = os.path.join(self.s.root, "Proyecto")
        viejo, nuevo = os.path.join(carpeta, ".subiendo-viejo.m4a"), os.path.join(carpeta, ".subiendo-nuevo.m4a")
        for p in (viejo, nuevo):
            with open(p, "wb") as f:
                f.write(b"x")
        os.utime(viejo, (time.time() - 7 * 3600,) * 2)
        self.addCleanup(lambda: os.path.exists(nuevo) and os.remove(nuevo))
        code, r, _ = self.s.subir(self.leer("mp3.mp3"), folder="Proyecto", name="Restos", archivo="r.mp3")
        self.assertEqual(code, 200, r)
        self.assertFalse(os.path.exists(viejo))
        self.assertTrue(os.path.exists(nuevo))  # el de otra subida en marcha, no

    def test_sin_origen_ni_token_no_entra(self):
        req = urllib.request.Request(self.s.base + "/api/subir?folder=&archivo=a.mp3", data=b"x", method="POST",
                                     headers={"X-Chuleta-Token": self.s.token})  # sin X-Audios
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)

    def test_el_plan_de_subir(self):
        cuando = time.mktime((2026, 10, 30, 9, 0, 0, 0, 0, -1))
        p = self.s.post("/api/grabar/plan", {"subir": True, "nombre": "Comité de prueba", "archivo": "x",
                                             "fecha": int(cuando * 1000), "recientes": True})
        self.assertNotIn("audio", [x["clave"] for x in p["lineas"]])
        car = next(x for x in p["lineas"] if x["clave"] == "carpeta")
        self.assertIn("2026-10-30 Comité de prueba", car["valor"])  # la carpeta, con la fecha del audio
        self.assertIn("preset", p["rec"])
        self.assertIn(".opus", self.s.get("/api/config")["audio_ext"])


@unittest.skipUnless(av, "PyAV no instalado")
class SinCarpetaPorReunionTests(unittest.TestCase):
    def test_mismo_nombre_en_la_misma_carpeta(self):
        s = Servidor({"carpeta_por_reunion": False})
        self.addCleanup(s.cerrar)
        datos = leer("mp3.mp3")
        fecha = str(int(time.mktime((2026, 11, 8, 10, 15, 0, 0, 0, -1)) * 1000))
        r1 = s.subir(datos, folder="", name="Comité", fecha=fecha, archivo="a.mp3")[1]
        r2 = s.subir(leer("whatsapp.opus"), folder="", name="Comité", fecha=fecha,
                     archivo="b.opus")[1]
        self.assertEqual(r1["path"], "2026-11-08 10-15-00 - Comité.mp3")
        self.assertEqual(r2["path"], "2026-11-08 10-15-00 - Comité (2).opus")  # no comparte el .txt del otro


@unittest.skipUnless(av, "PyAV no instalado")
class SubirYTranscribirTests(unittest.TestCase):
    """De punta a punta con la cola de verdad: cada formato subido se decodifica (PyAV), se pasa a FLAC y va a un Groq
    falso en 127.0.0.1 (nunca una clave real), y su .txt queda junto al audio. En ui-smoke.yml corre también en
    Windows: es la prueba de que allí se leen los formatos del móvil."""

    def test_cada_formato_subido_acaba_transcrito(self):
        from test_e2e_procesado import CLAVE, GROQ_TEXTO, GroqFalso
        groq = GroqFalso()
        self.addCleanup(groq.httpd.shutdown)
        s = Servidor(env={"CHULETA_PRUEBAS_URL_GROQ": groq.base})
        self.addCleanup(s.cerrar)
        s.post("/api/settings", {"procesado": "api", "cloud": {"provider": "groq", "api_key": CLAVE}, "preset": "nube",
                                 "summary_auto": False})
        subidos = {}
        for archivo, exts in FORMATOS:
            nombre = os.path.splitext(archivo)[0] + exts[-1]  # con la extensión más rara de cada uno
            code, r, _ = s.subir(leer(archivo), folder="Proyecto", name=os.path.splitext(archivo)[0],
                                 archivo=nombre, preset="nube", language="es")
            self.assertEqual(code, 200, (nombre, r))
            self.assertIsNotNone(r["job"], nombre)
            subidos[r["path"]] = nombre
        fin, estados = time.time() + 180, {}
        while time.time() < fin:
            estados = {j["path"]: (j["status"], j.get("error")) for j in s.get("/api/jobs")["jobs"]
                       if j["path"] in subidos}
            if len(estados) == len(subidos) and all(e[0] in ("done", "error", "cancelled") for e in estados.values()):
                break
            time.sleep(0.5)
        malos = {subidos[p]: e for p, e in estados.items() if e[0] != "done"}
        self.assertEqual(malos, {}, f"sin terminar o con error: {malos}")
        self.assertEqual(len(estados), len(subidos))
        for rel in subidos:
            with open(os.path.join(s.root, *os.path.splitext(rel)[0].split("/")) + ".txt", encoding="utf-8-sig") as f:
                self.assertIn(GROQ_TEXTO.strip(), f.read(), subidos[rel])
        self.assertGreaterEqual(sum(1 for p in groq.peticiones if p[0].endswith("/audio/transcriptions")),
                                len(subidos))


# ----------------------------------------------------------------------------- la interfaz (Chromium)

@unittest.skipUnless(sync_playwright and av, "playwright o PyAV no instalados")
class SubirEnLaInterfazTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Servidor()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.art = os.environ.get("NOTAS_ARTEFACTOS")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.s.cerrar()

    def pagina(self, ancho=1400):
        ctx = self.browser.new_context(bypass_csp=True, viewport={"width": ancho, "height": 900})
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        self.errores = []
        pg.on("console", lambda m: self.errores.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        pg.goto(f"{self.s.base}/?t={self.s.token}", wait_until="load")
        pg.wait_for_selector("#main h1:has-text('Tus notas')", timeout=15000)
        return pg

    def captura(self, pg, nombre):
        if self.art:
            os.makedirs(self.art, exist_ok=True)
            pg.screenshot(path=os.path.join(self.art, f"subir_{nombre}.png"))

    def sin_errores(self):
        # /api/rec/devices contesta 400 sin soundcard (no lo pide la tarjeta de subir, pero sí otras pantallas)
        self.assertEqual([e for e in self.errores if "Failed to load resource" not in e], [])

    def elegir(self, pg, boton, *archivos):
        with pg.expect_file_chooser() as fc:
            pg.click(boton)
        self.assertTrue(fc.value.is_multiple())
        fc.value.set_files(list(archivos))
        pg.wait_for_selector("#modal:not(.hidden) .dialog.sb [data-fila='carpeta'] .gr-v code", timeout=10000)

    def test_boton_selector_tarjeta_y_cola(self):
        pg = self.pagina()
        b = pg.locator("#btnSubir")
        self.assertTrue(b.is_visible())
        self.assertEqual(b.get_attribute("aria-label"), "Subir audio")
        acc = pg.evaluate("document.querySelector('#sbInput')?.accept || ''")
        self.assertEqual(acc, "")  # el selector se crea al pulsar
        src = os.path.join(self.s.tmp, "WhatsApp Ptt 2026-11-08 at 10.15.32.ogg")
        shutil.copyfile(os.path.join(FIX, "whatsapp.opus"), src)
        self.elegir(pg, "#btnSubir", src)
        acc = pg.evaluate("document.querySelector('#sbInput').accept")
        for ext in (".m4a", ".opus", ".ogg", ".mp3", ".wav", ".aac", ".webm", ".flac", ".mp4", ".amr"):
            self.assertIn(ext, acc.split(","))
        d = pg.locator(".dialog.sb")
        self.assertIn("Subir audio", d.locator(".gr-kicker").text_content())
        self.assertIn("WhatsApp Ptt 2026-11-08 at 10.15.32.ogg", d.locator(".sb-archivo").inner_text())
        self.assertEqual(pg.input_value("#grNombre"), "Mensaje de voz de WhatsApp")
        self.assertEqual(pg.input_value("#sbFecha"), "2026-11-08T10:15")
        self.assertIn("del nombre del archivo", pg.inner_text("#sbFechaOrigen"))
        self.assertEqual(d.locator("[data-fila='audio']").count(), 0)
        self.assertIn("Así se va a subir", d.text_content())
        self.assertIn("nada más subirlo", d.locator("[data-fila='transcribe'] .gr-v").inner_text())
        self.assertIn("2026-11-08 Mensaje de voz de WhatsApp", d.locator("[data-fila='carpeta'] .gr-v").inner_text())
        # el nombre que pones y la fecha que cambias: la carpeta de la reunión los sigue
        pg.fill("#grNombre", "Llamada con Ana")
        pg.fill("#sbFecha", "2026-11-07T18:30")
        pg.dispatch_event("#sbFecha", "change")
        pg.wait_for_function("document.querySelector(\"[data-fila='carpeta'] .gr-v\").innerText"
                             ".includes('2026-11-07 Llamada con Ana')", timeout=5000)
        self.assertIn("cambiada por ti", pg.inner_text("#sbFechaOrigen"))
        # el modelo: los de la cola, sin alcance (el de siempre está en Ajustes › Procesado)
        d.locator("[data-gr-cambiar='transcribe']").click()
        self.assertEqual(d.locator(".gr-panel[data-panel='transcribe'] .gr-alcance").count(), 0)
        self.assertGreater(d.locator("#grSelPreset option").count(), 1)
        d.locator("[data-gr-listo='transcribe']").click()
        self.captura(pg, "tarjeta")
        pg.focus("#grNombre")
        pg.keyboard.press("Enter")  # Intro (fuera de un botón o un panel) sube, como en la de grabar
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('Subido')", timeout=15000)
        self.assertTrue(pg.locator("#modal").is_hidden())
        pg.wait_for_function("location.hash.startsWith('#/a/')", timeout=5000)
        ruta = urllib.parse.unquote(pg.evaluate("location.hash.slice(4)"))
        self.assertTrue(ruta.endswith("2026-11-07 18-30-00 - Llamada con Ana.ogg"), ruta)
        self.assertTrue(os.path.isfile(os.path.join(self.s.root, *ruta.split("/"))))
        self.assertTrue(any(j["path"] == ruta for j in self.s.get("/api/jobs")["jobs"]))
        self.captura(pg, "despues")
        self.sin_errores()

    def test_un_archivo_que_no_se_puede_leer_se_queda_en_la_tarjeta(self):
        pg = self.pagina()
        roto = os.path.join(self.s.tmp, "Nueva grabación 7.m4a")
        with open(roto, "wb") as f:
            f.write(os.urandom(40000))
        self.elegir(pg, "#btnSubir", roto)
        self.assertEqual(pg.input_value("#grNombre"), "Nueva grabación 7")
        self.assertIn("de cuándo se guardó el archivo", pg.inner_text("#sbFechaOrigen"))
        pg.click("#rGo")
        pg.wait_for_selector("#sbError:not(.hidden)", timeout=10000)
        self.assertIn("«Nueva grabación 7.m4a» no parece un audio, o está dañado.", pg.inner_text("#sbError"))
        self.assertTrue(pg.locator("#modal .dialog.sb").is_visible())
        self.assertFalse(pg.locator("#rGo").is_disabled())
        self.assertEqual(pg.evaluate("document.activeElement.id"), "grNombre")  # Intro reintenta
        self.captura(pg, "error")
        self.sin_errores()

    def test_lo_que_no_es_audio_no_abre_la_tarjeta(self):
        pg = self.pagina()
        pdf = os.path.join(self.s.tmp, "acta.pdf")
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4")
        with pg.expect_file_chooser() as fc:
            pg.click("#btnSubir")
        fc.value.set_files(pdf)
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('no es un audio')", timeout=5000)
        self.assertTrue(pg.locator("#modal").is_hidden())
        self.sin_errores()

    def test_varios_a_la_vez_y_desde_una_carpeta(self):
        pg = self.pagina()
        pg.evaluate("location.hash = '#/f/Proyecto'")
        pg.wait_for_selector("#main [data-act='subir']", timeout=5000)
        a, b = os.path.join(self.s.tmp, "uno.mp3"), os.path.join(self.s.tmp, "dos.m4a")
        shutil.copyfile(os.path.join(FIX, "mp3.mp3"), a)
        shutil.copyfile(os.path.join(FIX, "iphone_notas_de_voz.m4a"), b)
        self.elegir(pg, "#main [data-act='subir']", a, b)
        d = pg.locator(".dialog.sb")
        self.assertEqual(d.locator(".sb-item").count(), 2)
        self.assertEqual(pg.locator("#grNombre").count(), 0)  # cada uno con su nombre
        self.assertEqual(pg.locator("#sbFecha").count(), 0)
        self.assertIn("Proyecto", d.locator("[data-fila='carpeta'] .gr-v").inner_text())
        self.assertIn("cada audio en su carpeta", d.locator("[data-fila='carpeta'] .gr-v").inner_text())
        self.assertEqual(d.locator("[data-fila='carpeta'] .gr-origen").inner_text(), "cambiada para estas reuniones")
        self.assertIn("los 2", pg.inner_text("#rGo"))
        self.captura(pg, "varios")
        pg.click("#rGo")
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('2 audios subidos a Proyecto')",
                             timeout=15000)
        subidos = [os.path.relpath(os.path.join(dp, f), self.s.root) for dp, _, fs in
                   os.walk(os.path.join(self.s.root, "Proyecto")) for f in fs
                   if f.endswith((" - uno.mp3", " - dos.m4a"))]
        self.assertEqual(len(subidos), 2, subidos)
        self.sin_errores()

    def test_soltar_un_audio_abre_la_tarjeta(self):
        pg = self.pagina()
        datos = list(leer("whatsapp.opus"))
        pg.evaluate("""bytes => {
            const dt = new DataTransfer();
            dt.items.add(new File([new Uint8Array(bytes)], 'PTT-20261108-WA0003.opus', { type: 'audio/ogg' }));
            dt.items.add(new File(['notas'], 'notas.txt', { type: 'text/plain' }));
            dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, cancelable: true }));
            dispatchEvent(new DragEvent('drop', { dataTransfer: dt, cancelable: true }));
        }""", datos)
        pg.wait_for_selector("#modal:not(.hidden) .dialog.sb [data-fila='carpeta'] .gr-v code", timeout=10000)
        self.assertEqual(pg.input_value("#grNombre"), "Mensaje de voz de WhatsApp")
        self.assertTrue(pg.input_value("#sbFecha").startswith("2026-11-08T"))
        # el .txt va a la carpeta como siempre (sin tarjeta)
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('notas.txt')", timeout=10000)
        self.sin_errores()

    def test_nombres_y_fechas_que_propone_el_archivo(self):
        pg = self.pagina()
        casos = pg.evaluate("""() => {
            const f = (n, t) => { const r = sbDesdeArchivo(new File([''], n, { lastModified: t || 0 }));
                                  return [r.nombre, r.origen, new Date(r.fecha).toISOString().slice(0, 10)]; };
            return { wa: f('WhatsApp Ptt 2026-11-08 at 10.15.32.ogg'), ptt: f('PTT-20261108-WA0003.opus', Date.now()),
                     tenca: f('2026-10-30 10-00-00 - Comité de prueba.m4a'), audio: f('AUDIO_20231215_143022.m4a'),
                     rec: f('REC_2026-11-08 10-15.mp3'), rec2: f('Recording 3.m4a', Date.now()),
                     graba: f('Grabación 12.m4a', Date.now()), voice: f('Voice 001.m4a', Date.now()),
                     nueva: f('Nueva grabación 12.m4a', Date.now()), calle: f('Calle Mayor 3.m4a', Date.now()) }; }""")
        self.assertEqual(casos["wa"][:2], ["Mensaje de voz de WhatsApp", "nombre"])
        self.assertEqual(casos["ptt"][:2], ["Mensaje de voz de WhatsApp", "nombre"])
        self.assertEqual(casos["tenca"][:2], ["Comité de prueba", "nombre"])
        for k in ("audio", "rec", "rec2", "graba", "voice"):  # lo que pone la grabadora: sin nombre
            self.assertEqual(casos[k][0], "", k)
        self.assertEqual(casos["audio"][2], "2023-12-15")
        self.assertEqual(casos["nueva"][:2], ["Nueva grabación 12", "guardado"])
        self.assertEqual(casos["calle"][0], "Calle Mayor 3")
        self.sin_errores()

    def test_cancelar_a_mitad_no_guarda_nada(self):
        pg = self.pagina()
        cdp = pg.context.new_cdp_session(pg)  # la subida, a 150 KB/s: da tiempo a cancelar
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1,
                                                      "uploadThroughput": 150 * 1024})
        grande = os.path.join(self.s.tmp, "Entrevista larga.wav")
        wav24(grande, segundos=40)  # ~1,9 MB
        self.elegir(pg, "#btnSubir", grande)
        # un panel abierto con un cambio: mientras sube, su «Quitar el cambio» no hace nada (revisión 3)
        pg.click(".dialog.sb [data-gr-cambiar='idioma']")
        pg.select_option("#grSelIdioma", "en")
        pg.wait_for_function("G && G.pedido.cambios.idioma === 'en'", timeout=5000)
        pg.click(".dialog.sb [data-gr-listo='idioma']")
        pg.wait_for_selector(".dialog.sb [data-fila='idioma'].cambiado", timeout=5000)
        pg.click(".dialog.sb [data-gr-cambiar='idioma']")  # ahora con «Quitar el cambio»
        pg.wait_for_selector(".dialog.sb [data-gr-quitar='idioma']", timeout=5000)
        pg.click("#rGo")
        pg.wait_for_function("/Subiendo… [1-9]/.test(document.querySelector('#rGo').innerText)", timeout=15000)
        pg.click(".dialog.sb [data-gr-quitar='idioma']")
        self.assertEqual(pg.evaluate("G.pedido.cambios.idioma"), "en")
        # mientras sube, la tarjeta no se toca: ni otro archivo, ni otro proyecto, ni soltar más (revisión 2)
        for sel in ("[data-sb-otro]", "#grProy", "#grNombre", "#sbFecha", "[data-gr-cambiar='carpeta']"):
            self.assertTrue(pg.locator(f".dialog.sb {sel}").is_disabled(), sel)
        self.assertFalse(pg.locator(".dialog.sb [data-m='no']").is_disabled())
        pg.evaluate("""() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'otra.m4a'));
            dispatchEvent(new DragEvent('drop', { dataTransfer: dt, cancelable: true })); }""")
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('Espera a que termine')",
                             timeout=5000)
        self.assertIn("Entrevista larga.wav", pg.inner_text(".dialog.sb .sb-archivo"))  # la misma tarjeta
        pg.click(".dialog.sb [data-m='no']")
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('Subida cancelada')", timeout=5000)
        time.sleep(2)
        self.assertFalse(pg.evaluate("document.querySelector('#toasts').innerText.includes('Subido')"))
        self.assertFalse(pg.evaluate("location.hash.startsWith('#/a/')"))
        guardados = [f for _, _, fs in os.walk(self.s.root) for f in fs if "Entrevista larga" in f]
        self.assertEqual(guardados, [])
        fin = time.time() + 10  # el servidor borra lo que estaba recibiendo en cuanto ve la conexión cortada
        while time.time() < fin and [f for _, _, fs in os.walk(self.s.root) for f in fs if f.startswith(".subiendo")]:
            time.sleep(0.2)
        self.assertEqual([f for _, _, fs in os.walk(self.s.root) for f in fs if f.startswith(".subiendo")], [])
        self.sin_errores()

    def test_si_uno_falla_la_tarjeta_sigue_con_ese(self):
        pg = self.pagina()
        bueno, roto = os.path.join(self.s.tmp, "bueno.mp3"), os.path.join(self.s.tmp, "roto.m4a")
        shutil.copyfile(os.path.join(FIX, "mp3.mp3"), bueno)
        with open(roto, "wb") as f:
            f.write(os.urandom(30000))
        self.elegir(pg, "#btnSubir", bueno, roto)
        self.assertEqual(pg.evaluate("document.activeElement.id"), "rGo")  # varios: Intro sube
        pg.keyboard.press("Enter")
        pg.wait_for_selector("#sbError:not(.hidden)", timeout=15000)
        self.assertIn("«roto.m4a» no parece un audio", pg.inner_text("#sbError"))
        self.assertIn("roto.m4a", pg.inner_text(".dialog.sb .sb-archivo"))  # solo el que ha fallado
        self.assertEqual(pg.locator(".dialog.sb .sb-item").count(), 0)
        self.assertIn("Subido", pg.inner_text("#toasts"))
        self.assertTrue(any(f.endswith(" - bueno.mp3") for _, _, fs in os.walk(self.s.root) for f in fs))
        self.captura(pg, "fallo_parcial")
        self.sin_errores()

    def test_soltar_con_grabar_abierta_no_la_rompe(self):
        pg = self.pagina()
        pg.click("#btnRec")
        pg.wait_for_selector("#modal:not(.hidden) .dialog.gr", timeout=5000)
        pg.evaluate("""() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'nota.m4a'));
            dispatchEvent(new DragEvent('drop', { dataTransfer: dt, cancelable: true })); }""")
        pg.wait_for_function("document.querySelector('#toasts').innerText.includes('cierra antes esta ventana')",
                             timeout=5000)
        self.assertEqual(pg.locator("#modal .dialog.sb").count(), 0)
        self.assertTrue(pg.evaluate("recDialog"))  # la tarjeta de grabar sigue con su limpieza
        pg.keyboard.press("Escape")
        pg.wait_for_function("!recDialog", timeout=5000)

    def test_elegir_otro_conserva_el_proyecto(self):
        pg = self.pagina()
        a, b = os.path.join(self.s.tmp, "primero.mp3"), os.path.join(self.s.tmp, "segundo.m4a")
        shutil.copyfile(os.path.join(FIX, "mp3.mp3"), a)
        shutil.copyfile(os.path.join(FIX, "iphone_notas_de_voz.m4a"), b)
        self.elegir(pg, "#btnSubir", a)
        vocab = pg.eval_on_selector("#grProy", "s => [...s.options].find(o => o.textContent.includes('General')).value")
        pg.select_option("#grProy", vocab)
        pg.wait_for_function(f"document.querySelector('#grProy').value === {json.dumps(vocab)}", timeout=5000)
        with pg.expect_file_chooser() as fc:
            pg.click("[data-sb-otro]")
        fc.value.set_files(b)
        pg.wait_for_function("document.querySelector('.dialog.sb .sb-archivo').innerText.includes('segundo.m4a')",
                             timeout=10000)
        pg.wait_for_selector(".dialog.sb [data-fila='carpeta'] .gr-v code", timeout=10000)
        self.assertEqual(pg.input_value("#grProy"), vocab)
        self.assertEqual(pg.input_value("#grNombre"), "segundo")
        self.sin_errores()

    def test_ventana_estrecha_solo_el_icono(self):
        pg = self.pagina(ancho=900)
        b = pg.locator("#btnSubir")
        self.assertTrue(b.is_visible())
        self.assertFalse(pg.locator("#btnSubir span").is_visible())
        caja = b.bounding_box()
        self.assertLessEqual(caja["width"], 40)
        self.assertLessEqual(pg.evaluate("document.querySelector('.topbar').scrollWidth"), 900)
        self.captura(pg, "estrecha")
        self.sin_errores()


if __name__ == "__main__":
    unittest.main()
