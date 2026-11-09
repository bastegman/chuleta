# -*- coding: utf-8 -*-
"""fix-subir-memoria (09/11/2026, msg 1378): al subir un audio viejo de 55 min, «Quién habla» se quedó sin memoria en el
portátil de Bernardo («bad allocation» en /decoder/_pooling/Concat_4 de TitaNet) y la interfaz enseñó la traza de ONNX
dos veces, una como «No se pudo transcribir» con la transcripción hecha.

- Al modelo de huellas nunca le llegan más de HUELLA_VENTANA_S de una vez (antes, 90 s: +808 MB por huella).
- Una sesión de onnxruntime por bloque y por fase: sus memorias no se suman.
- El audio de las voces, en disco (np.memmap) y no en la memoria del proceso.
- decode_16k con tope total de relleno de silencio y de duración (AUDIO_MAX_S); al subir, se rechaza antes de guardar.
- El espectrograma de faster-whisper sin batched («Normal»), por bloques; la conversión a float32, con una copia.
- explicar: el error de memoria en una frase; la interfaz separa el aviso de las voces del de la transcripción.
"""
import io
import os
import sys
import time
import types
import unittest
from unittest import mock

import numpy as np

from support import TempTree, engines, server

import explicar  # noqa: E402
import voices  # noqa: E402

SR = voices.SR


def leer(*partes):
    with open(os.path.join(os.path.dirname(engines.__file__), *partes), encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------- dobles de sherpa-onnx

class Stream:
    def __init__(self, llamadas):
        self.x, self.llamadas = np.zeros(0, np.float32), llamadas

    def accept_waveform(self, sr, x):
        assert sr == SR
        self.x = np.concatenate([self.x, np.asarray(x, np.float32)])

    def input_finished(self):
        self.llamadas.append(len(self.x))


class Extractor:
    """SpeakerEmbeddingExtractor falso: apunta cuántas muestras recibe cada huella y devuelve un vector que depende
    del nivel del trozo (para comprobar la media ponderada)."""

    def __init__(self):
        self.llamadas = []

    def create_stream(self):
        return Stream(self.llamadas)

    def is_ready(self, s):
        return True

    def compute(self, s):
        v = np.zeros(voices.DIM, np.float32)
        v[0], v[1] = 1.0, float(np.mean(s.x))  # el nivel del trozo cambia la dirección del vector
        return v.tolist()


class HuellaPorVentanasTests(unittest.TestCase):
    def test_al_modelo_nunca_le_llegan_mas_de_la_ventana(self):
        ex = Extractor()
        v = voices.embed(ex, np.zeros(90 * SR, np.float32))
        self.assertIsNotNone(v)
        self.assertEqual(ex.llamadas, [30 * SR] * 3)  # antes: [90 * SR], +808 MB de una vez
        ex = Extractor()
        voices.embed(ex, np.zeros(31 * SR, np.float32))
        self.assertEqual(ex.llamadas, [int(15.5 * SR)] * 2)  # ventanas iguales: ninguna de menos de la mitad
        ex = Extractor()
        voices.embed(ex, np.zeros(30 * SR, np.float32))
        self.assertEqual(ex.llamadas, [30 * SR])  # hasta la ventana, como antes: una sola
        self.assertIsNone(voices.embed(Extractor(), np.zeros(SR - 1, np.float32)))  # menos de 1 s, nada

    def test_la_huella_larga_es_la_media_ponderada_de_sus_ventanas(self):
        x = np.concatenate([np.full(40 * SR, 0.5, np.float32), np.full(20 * SR, -0.5, np.float32)])
        ex = Extractor()
        v = voices.embed(ex, x)  # 60 s → dos ventanas de 30 s: niveles 0,5 y (10 s a 0,5 + 20 s a -0,5)
        a = voices._unit(np.array([1.0, 0.5] + [0] * (voices.DIM - 2), np.float32))
        b = voices._unit(np.array([1.0, (10 * 0.5 - 20 * 0.5) / 30] + [0] * (voices.DIM - 2), np.float32))
        esperado = voices._unit(a * 30 + b * 30)
        self.assertAlmostEqual(voices.cosine(v, esperado), 1.0, places=5)
        self.assertAlmostEqual(float(np.linalg.norm(v)), 1.0, places=5)

    def test_fingerprint_de_90_s_por_ventanas(self):
        ex = Extractor()
        samples = np.zeros(200 * SR, np.int16)
        vec, secs = voices.fingerprint(ex, samples, [(0, 50), (60, 110), (120, 125)])
        self.assertIsNotNone(vec)
        self.assertEqual(secs, 105)
        self.assertLessEqual(max(ex.llamadas), voices.HUELLA_VENTANA_S * SR)
        self.assertEqual(sum(ex.llamadas), 90 * SR)  # la misma voz que antes (HUELLA_MAX_S), solo que por partes

    def test_f32_una_copia_y_mismo_resultado(self):
        y = np.array([-32768, -1, 0, 1, 12345, 32767], np.int16)
        self.assertTrue(np.array_equal(voices._f32(y), np.asarray(y, np.float32) / 32768.0))
        self.assertEqual(voices._f32(y).dtype, np.float32)


class SesionesTests(unittest.TestCase):
    def test_nunca_hay_dos_sesiones_de_onnx_vivas_a_la_vez(self):
        """Cada sesión de onnxruntime se queda con la memoria que ha llegado a usar: el diarizador y el extractor
        juntos, más el del bloque siguiente, se sumaban. Ahora se crean por bloque y fase y se sueltan al terminar."""
        vivas, maximo = [0], [0]

        class Sesion:
            def __init__(self):
                vivas[0] += 1
                maximo[0] = max(maximo[0], vivas[0])

            def __del__(self):
                vivas[0] -= 1

        seg = lambda a, b, s: types.SimpleNamespace(start=a, end=b, speaker=s)  # noqa: E731

        class SD(Sesion):
            sample_rate = SR

            def process(self, x, cb=None):
                cb(1, 1)
                return types.SimpleNamespace(sort_by_start_time=lambda: [seg(0, 4, 0), seg(4, 10, 1)])

        class EX(Sesion, Extractor):
            def __init__(self):
                Sesion.__init__(self)
                Extractor.__init__(self)

        samples = (np.random.default_rng(0).standard_normal(20 * SR) * 3000).astype(np.int16)
        with mock.patch.object(voices, "_sherpa", return_value=None), \
                mock.patch.object(voices, "_diarizer", side_effect=lambda *a, **k: SD()), \
                mock.patch.object(voices, "_extractor", side_effect=lambda *a, **k: EX()), \
                mock.patch.object(voices, "models_ready", return_value=True), \
                mock.patch.object(voices, "BLOQUE_S", 10.0):
            res = voices.diarize(samples, "/nonexistent")
        self.assertTrue(res["tramos"])
        self.assertEqual(maximo[0], 1)  # antes: 2 (diarizador y extractor creados juntos y vivos hasta el final)
        self.assertEqual(vivas[0], 0)


class HilosTests(unittest.TestCase):
    def test_la_mitad_entre_2_y_4(self):
        self.assertEqual([voices.hilos(n) for n in (0, 1, 2, 4, 6, 8, 12, 16, 64)],
                         [2, 2, 2, 2, 3, 4, 4, 4, 4])
        self.assertIn(voices.hilos(), (2, 3, 4))

    def test_el_servidor_los_usa(self):
        self.assertIn('"--threads", str(voices.hilos())', leer("server.py"))


# ----------------------------------------------------------------------------- decode_16k con dobles de PyAV

class FFmpegError(Exception):
    pass


def fake_av(paquetes, frame=1600):
    """PyAV falso: cada paquete es True (se lee: `frame` muestras de valor 1) o un float (no se lee y dice durar
    eso)."""
    class Stream:
        type = "audio"
        codec_context = types.SimpleNamespace(frame_size=0, sample_rate=SR)

        def decode(self, pk):
            if pk.malo:
                raise FFmpegError("x")
            return [types.SimpleNamespace(to_ndarray=lambda: np.ones((1, frame), np.int16))]

    class Container:
        streams = [Stream()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def demux(self, s):
            return iter([types.SimpleNamespace(malo=False) if p is True
                         else types.SimpleNamespace(malo=True, duration=p, time_base=1) for p in paquetes])

    return types.SimpleNamespace(open=lambda path: Container(),
                                 AudioResampler=lambda **kw: types.SimpleNamespace(
                                     resample=lambda fr: [fr] if fr is not None else []),
                                 error=types.SimpleNamespace(FFmpegError=FFmpegError))


class DecodeTopesTests(unittest.TestCase):
    def test_el_relleno_de_silencio_tiene_tope_total(self):
        """Miles de paquetes ilegibles que dicen durar 2 s (lo más por paquete) pedían horas de silencio: 2.000 eran
        más de una hora y, en int16, 128 MB por hora sin que hubiera audio. Ahora, hasta SILENCIO_LIBRE_S y, después,
        nunca más silencio que audio leído; el audio legible que venga detrás se sigue leyendo."""
        with mock.patch.dict(sys.modules, {"av": fake_av([True] + [2.0] * 2000 + [True])}), \
                mock.patch.object(engines, "SILENCIO_LIBRE_S", 10.0):
            y = engines.decode_16k("x.m4a")
        self.assertEqual(len(y), 1600 + 10 * SR + 1600)  # antes: 4.000 s de silencio
        self.assertEqual((y[0], y[1600], y[-1]), (1, 0, 1))

    def test_relleno_legitimo_hasta_lo_leido(self):
        # 30 s leídos y 20 s de paquetes malos: todo cabe (no supera lo leído) aunque pase de SILENCIO_LIBRE_S
        paquetes = [True] * 300 + [0.1] * 200 + [True]
        with mock.patch.dict(sys.modules, {"av": fake_av(paquetes)}), \
                mock.patch.object(engines, "SILENCIO_LIBRE_S", 1.0):
            y = engines.decode_16k("x.amr")
        self.assertEqual(len(y), 301 * 1600 + 200 * 1600)

    def test_mas_largo_que_el_tope_es_un_error_claro(self):
        with mock.patch.dict(sys.modules, {"av": fake_av([True] * 20)}), \
                mock.patch.object(engines, "AUDIO_MAX_S", 1.0):
            with self.assertRaises(engines.EngineError) as cm:
                engines.decode_16k("x.mp3")
        self.assertIn("córtalo en partes", str(cm.exception))
        with mock.patch.dict(sys.modules, {"av": fake_av([True] * 20)}), \
                mock.patch.object(engines, "AUDIO_MAX_S", 1.0):
            with self.assertRaises(engines.EngineError):  # también si continúa desde un segundo (start)
                engines.decode_16k("x.mp3", start=0.5)

    def test_destino_escribe_en_disco_lo_mismo(self):
        paquetes = [True, 0.05, True, True]
        with mock.patch.dict(sys.modules, {"av": fake_av(paquetes)}):
            y = engines.decode_16k("x.m4a", start=0.05)
            f = io.BytesIO()
            n = engines.decode_16k("x.m4a", start=0.05, destino=f)
        self.assertEqual(n, len(y))
        self.assertTrue(np.array_equal(np.frombuffer(f.getvalue(), "<i2"), y))

    def test_disco_lleno_es_un_error_claro(self):
        class Lleno:
            def write(self, b):
                raise OSError(28, "No space left on device")

        with mock.patch.dict(sys.modules, {"av": fake_av([True, True])}):
            with self.assertRaises(engines.EngineError) as cm:  # antes de esto, «WAV cortado»: se seguía a medias
                engines.decode_16k("x.m4a", destino=Lleno())
        self.assertIn("libera espacio", str(cm.exception))

    def test_a_float32_bit_a_bit(self):
        y = (np.random.default_rng(1).integers(-32768, 32768, 10000)).astype(np.int16)
        a, b = engines.a_float32(y), y.astype(np.float32) / 32768.0
        self.assertEqual(a.dtype, np.float32)
        self.assertTrue(np.array_equal(a, b))


class VocesEnDiscoTests(TempTree):
    def test_load_audio_en_disco_es_un_memmap_igual(self):
        raw = os.path.join(self.tmp, "a.pcm")
        with mock.patch.dict(sys.modules, {"av": fake_av([True, 0.05, True])}):
            y = voices.load_audio_16k("x.m4a")
            m = voices.load_audio_16k("x.m4a", en_disco=raw)
        self.assertIsInstance(m, np.memmap)
        self.assertTrue(np.array_equal(np.asarray(m), y))
        del m

    def test_main_borra_su_archivo_temporal_tambien_si_falla(self):
        creados = []

        def load(path, cancelled=None, en_disco=None):
            creados.append(en_disco)
            with open(en_disco, "wb") as f:
                f.write(b"\0\0" * SR)
            return np.memmap(en_disco, dtype="<i2", mode="r")

        def falla(samples, *a, **k):  # funciones, no Mock: un Mock se queda con el memmap en call_args
            assert len(samples) == SR
            raise voices.VoicesError("x")

        def bien(samples, *a, **k):
            assert len(samples) == SR
            return {"tramos": [], "hablantes": {}, "huellas": {}, "duracion_s": 1.0}

        out = os.path.join(self.tmp, "r.hablantes.json")
        for diarize, rc in ((falla, 1), (bien, 0)):
            with mock.patch.object(voices, "load_audio_16k", load), \
                    mock.patch.object(voices, "diarize", diarize), \
                    mock.patch.object(os, "dup2"), mock.patch.object(os, "fdopen", return_value=io.BytesIO()):
                self.assertEqual(voices.main(["--diarize", "x.m4a", "--out", out, "--data", self.data_dir]), rc)
        self.assertEqual(len(creados), 2)
        for p in creados:
            self.assertEqual(os.path.dirname(p), os.path.join(self.data_dir, "tmp"))  # la que vacía el servidor
            self.assertFalse(os.path.exists(p), p)

    def test_los_de_otras_ejecuciones_viejos_se_borran_y_los_recientes_no(self):
        d = os.path.join(self.data_dir, "tmp")
        os.makedirs(d)
        viejo, reciente, otro = (os.path.join(d, n) for n in ("voces-1-a.pcm", "voces-2-b.pcm", "trozo.flac"))
        for p in (viejo, reciente, otro):
            with open(p, "wb") as f:
                f.write(b"x")
        hace = time.time() - voices.TMP_VIEJO_S - 60
        os.utime(viejo, (hace, hace))
        os.utime(otro, (hace, hace))
        voices._borrar_tmp(os.path.join(d, "voces-3-c.pcm"))
        self.assertFalse(os.path.exists(viejo))
        self.assertTrue(os.path.exists(reciente))  # puede ser de un «Recalcular huella» en marcha
        self.assertTrue(os.path.exists(otro))  # lo de la nube no es suyo


# ----------------------------------------------------------------------------- espectrograma por bloques

def features_referencia(x, mel_filters, n_fft=400, hop=160, padding=160):
    """FeatureExtractor.__call__ de faster-whisper 1.2.1, de una vez (lo que FeaturesPorBloques tiene que dar)."""
    w = np.pad(x.astype(np.float32), (0, padding))
    w = np.pad(w, (n_fft // 2, n_fft // 2), mode="reflect")
    n = 1 + (len(w) - n_fft) // hop
    fr = np.lib.stride_tricks.as_strided(w, (n, n_fft), (hop * w.strides[0], w.strides[0]))
    st = np.fft.rfft(fr * np.hanning(n_fft + 1)[:-1].astype("float32"), n=n_fft, axis=-1).astype("complex64").T
    mel = mel_filters @ (np.abs(st[..., :-1]) ** 2)
    log = np.log10(np.clip(mel, 1e-10, None))
    log = np.maximum(log, log.max() - 8.0)
    return (log + 4.0) / 4.0


class FakeFE:
    sampling_rate, n_fft, hop_length = SR, 400, 160

    def __init__(self, mels):
        self.mel_filters = np.abs(np.random.default_rng(mels).standard_normal((mels, 201))).astype(np.float32) / 50
        self.n_samples, self.nb_max_frames, self.llamadas = 480000, 3000, 0

    def __call__(self, waveform, padding=160, chunk_length=None):
        self.llamadas += 1
        return features_referencia(waveform, self.mel_filters, padding=padding)


class FeaturesPorBloquesTests(unittest.TestCase):
    def test_mismo_resultado_que_de_una_vez(self):
        rng = np.random.default_rng(2)
        for mels in (80, 128):
            for secs in (20.0, 61.37, 125.0):
                x = (rng.standard_normal(int(secs * SR)) * 0.1).astype(np.float32)
                fe = FakeFE(mels)
                with mock.patch.object(engines, "FEATURES_BLOQUE_S", 7.0):
                    b = engines.FeaturesPorBloques(fe)(x, chunk_length=30)
                a = features_referencia(x, fe.mel_filters)
                self.assertEqual(fe.llamadas, 0)  # por bloques, sin el original
                self.assertEqual(a.shape, b.shape)
                self.assertEqual(b.dtype, np.float32)
                self.assertLess(float(np.abs(a - b).max()), 1e-6, (mels, secs))

    def test_corto_va_al_original_y_lo_demas_pasa_al_original(self):
        fe = FakeFE(80)
        f = engines.FeaturesPorBloques(fe)
        f(np.zeros(SR, np.float32))
        self.assertEqual(fe.llamadas, 1)
        self.assertEqual(f.hop_length, 160)
        with mock.patch.object(engines, "FEATURES_BLOQUE_S", 1.0):
            f(np.zeros(5 * SR, np.float32), chunk_length=20)  # como el original: chunk_length fija n_samples
        self.assertEqual((fe.n_samples, fe.nb_max_frames), (20 * SR, 20 * SR // 160))
        f.n_samples = 123
        self.assertEqual(fe.n_samples, 123)

    @unittest.skipUnless(__import__("importlib").util.find_spec("faster_whisper"), "faster-whisper no instalado")
    def test_igual_que_faster_whisper_de_verdad(self):  # pragma: no cover - solo con faster-whisper
        from faster_whisper.feature_extractor import FeatureExtractor
        x = (np.random.default_rng(3).standard_normal(130 * SR) * 0.1).astype(np.float32)
        for mels in (80, 128):
            a = FeatureExtractor(feature_size=mels)(x, chunk_length=30)
            with mock.patch.object(engines, "FEATURES_BLOQUE_S", 7.0):
                b = engines.FeaturesPorBloques(FeatureExtractor(feature_size=mels))(x, chunk_length=30)
            self.assertLess(float(np.abs(a - b).max()), 1e-6)

    def test_el_worker_lo_usa(self):
        src = leer("worker.py")
        self.assertIn("m.feature_extractor = engines.FeaturesPorBloques(m.feature_extractor)", src)
        self.assertIn("engines.a_float32(engines.decode_16k(", src)
        self.assertNotIn(".astype(np.float32) / 32768.0", src)


# ----------------------------------------------------------------------------- el aviso

ONNX = ("Non-zero status code returned while running Concat node. Name:'/decoder/_pooling/Concat_4' Status "
        "Message: bad allocation")


class AvisoTests(unittest.TestCase):
    def test_sin_memoria_en_una_frase(self):
        for err in (ONNX, MemoryError("Unable to allocate 1.2 GiB for an array"), "std::bad_alloc",
                    "RuntimeError: Failed to allocate memory for requested buffer of size 123"):
            r = explicar.explicar(err)
            self.assertIn("memoria", r["frase"], err)
            for crudo in ("Concat", "Non-zero", "allocate", "bad_alloc", "bad allocation"):
                self.assertNotIn(crudo, r["frase"])
            self.assertEqual(r["accion"]["tipo"], "reintentar")
        self.assertEqual(explicar.explicar(ONNX)["tecnico"], ONNX)  # el técnico, para el log y el diagnóstico

    def test_otro_fallo_de_onnx_sin_traza(self):
        r = explicar.explicar("Non-zero status code returned while running Where node. Name:'/encoder/x/Where_1'")
        self.assertNotIn("Where", r["frase"])
        self.assertEqual(r["accion"]["tipo"], "diagnostico")

    def test_el_trabajo_de_voces_lleva_la_frase(self):
        j = server.Job("Reuniones/a.m4a", "a.m4a", "voces", None)
        j.status, j.error = "error", ONNX
        d = j.public()
        self.assertEqual(d["error"], ONNX)
        self.assertIn("memoria", d["aviso"]["frase"])


class SubirDemasiadoLargoTests(TempTree):
    def test_se_rechaza_antes_de_guardar(self):
        os.makedirs(os.path.join(self.root, "Proyecto"))
        datos = b"\0" * 1000
        handler = types.SimpleNamespace(headers={"Content-Length": str(len(datos))}, rfile=io.BytesIO(datos),
                                        body_read=False)
        app = types.SimpleNamespace(settings=types.SimpleNamespace(get=lambda: {}))
        with mock.patch.object(engines, "probar_audio", return_value=engines.AUDIO_MAX_S + 60):
            with self.assertRaises(server.ApiError) as cm:
                server.App.subir(app, handler, {"folder": "Proyecto", "archivo": "viejo.m4a", "name": "Viejo"})
        self.assertIn("«viejo.m4a» dura más de 8 horas", cm.exception.msg)
        self.assertEqual(os.listdir(os.path.join(self.root, "Proyecto")), [])  # ni el audio ni su carpeta


# ----------------------------------------------------------------------------- la interfaz (código)

class InterfazTests(unittest.TestCase):
    def test_el_error_de_las_voces_no_es_de_la_transcripcion(self):
        src = leer("static", "app.js")
        render = src[src.index("function renderAudioJob"):src.index("function updateJobLine")]
        self.assertIn("lastTxJobFor(a.path)", render)
        self.assertNotIn("lastJobFor(a.path)", render)
        estado = src[src.index("function statusOf"):src.index("function statusPill")]
        self.assertIn("lastTxJobFor(a.path)", estado)
        voz = leer("static", "voces.js")
        self.assertIn("last.aviso?.frase || last.error", voz)


if __name__ == "__main__":
    unittest.main()
