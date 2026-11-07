# -*- coding: utf-8 -*-
"""Directo (cabecera WAV, elección del corte) y grabadora (nivelador, limitador, cola de muestras)."""
import os
import struct
import tempfile
import unittest
from unittest import mock

import numpy as np

from support import live, wav_bytes  # noqa: F401  (support prepara sys.path)
import recorder  # noqa: E402


class WavInfoTests(unittest.TestCase):
    def _info(self, data):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
        try:
            return live.wav_info(f.name)
        finally:
            os.unlink(f.name)

    def test_cerrado(self):
        info = self._info(wav_bytes(seconds=1.0))
        self.assertEqual((info["channels"], info["rate"], info["byte_rate"], info["bits"]), (2, 48000, 192000, 16))
        self.assertEqual((info["data_off"], info["data_size"]), (44, 192000))

    def test_abierto(self):
        info = self._info(wav_bytes(seconds=1.0, open_header=True))
        self.assertIsNone(info["data_size"])
        self.assertEqual(info["data_off"], 44)

    def test_no_wav(self):
        self.assertIsNone(self._info(b"ID3" + b"\0" * 100))
        self.assertIsNone(live.wav_info("/no/existe.wav"))

    def test_extensible(self):
        # WAVE_FORMAT_EXTENSIBLE (0xFFFE) con subformato PCM: el tag real va en los 2 bytes de la posicion 24
        fmt = struct.pack("<HHIIHH", 0xFFFE, 2, 48000, 192000, 4, 16) + struct.pack("<HHIH", 22, 16, 3, 1) + b"\0" * 14
        head = b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        head += b"data" + struct.pack("<I", 0xFFFFFFFF)
        info = self._info(head + b"\0" * 400)
        self.assertEqual(info["tag"], 1)
        self.assertIsNone(info["data_size"])


class ChooseCutTests(unittest.TestCase):
    def test_espera_si_hay_poco(self):
        buf = np.zeros(5 * live.SR, np.float32)
        self.assertEqual(live.choose_cut(buf, final=False, catching_up=False), (0, None))

    def test_final_lo_manda_todo(self):
        buf = np.zeros(3 * live.SR, np.float32)
        self.assertEqual(live.choose_cut(buf, final=True, catching_up=False), (3 * live.SR, None))

    def test_corta_en_una_pausa(self):
        sr = live.SR
        buf = np.zeros(30 * sr, np.float32)
        ts = [{"start": 0, "end": 10 * sr}, {"start": 12 * sr, "end": 20 * sr}, {"start": 21 * sr, "end": 30 * sr}]
        with mock.patch.object(live, "speech", return_value=ts):
            cut, got = live.choose_cut(buf, final=False, catching_up=False)
        self.assertEqual(got, ts)
        self.assertEqual(cut, (20 * sr + 21 * sr) // 2)  # la última pausa que pasa de 0,6 × 20 s
        ts[-1]["end"] = 29 * sr  # si al final hay más de 0,5 s de silencio, esa cola también es una pausa
        with mock.patch.object(live, "speech", return_value=ts):
            self.assertEqual(live.choose_cut(buf, final=False, catching_up=False)[0], (29 * sr + 30 * sr) // 2)

    def test_nube_corta_antes(self):
        sr = live.SR
        buf = np.zeros(10 * sr, np.float32)
        ts = [{"start": 0, "end": 6 * sr}, {"start": 7 * sr, "end": 10 * sr}]
        with mock.patch.object(live, "speech", return_value=ts):
            self.assertEqual(live.choose_cut(buf, False, False, cloud=True)[0], (6 * sr + 7 * sr) // 2)
            self.assertEqual(live.choose_cut(buf, False, False, cloud=False)[0], 0)  # en local aún no llega a 20 s

    def test_sin_pausa_corta_al_maximo(self):
        sr = live.SR
        buf = np.zeros(live.LIVE_MAX_S * sr, np.float32)
        with mock.patch.object(live, "speech", return_value=[{"start": 0, "end": len(buf)}]):
            self.assertEqual(live.choose_cut(buf, False, False)[0], len(buf) - sr)

    def test_todo_silencio_se_descarta(self):
        buf = np.zeros(25 * live.SR, np.float32)
        with mock.patch.object(live, "speech", return_value=[]):
            self.assertEqual(live.choose_cut(buf, False, False), (len(buf), []))


class ToMonoTests(unittest.TestCase):
    def test_16_bits_estereo(self):
        raw = struct.pack("<4h", 16384, -16384, 32767, 32767)
        x = live.to_mono(raw, {"bits": 16, "tag": 1, "channels": 2})
        np.testing.assert_allclose(x, [0.0, 32767 / 32768], atol=1e-6)

    def test_no_soportado(self):
        with self.assertRaises(Exception):
            live.to_mono(b"\0" * 8, {"bits": 8, "tag": 1, "channels": 1})


class LevelerTests(unittest.TestCase):
    def test_sube_el_micro_flojo_hacia_el_objetivo(self):
        lev = recorder.Leveler(*recorder.GAIN_RANGE["mic"])
        x = (np.random.default_rng(0).standard_normal((2400, 2)) * 0.01).astype(np.float32)  # ~ -40 dBFS
        for _ in range(30):
            lev(x.copy())
        db_gain = 20 * np.log10(lev.gain)
        self.assertGreater(db_gain, 10)
        self.assertLessEqual(db_gain, recorder.GAIN_RANGE["mic"][1] + 1e-6)

    def test_silencio_no_cambia_la_ganancia(self):
        lev = recorder.Leveler(-6.0, 18.0)
        out = lev(np.zeros((480, 2), np.float32))
        self.assertEqual(lev.gain, 1.0)
        self.assertEqual(out.shape, (480, 2))
        self.assertEqual(len(lev(np.zeros((0, 2), np.float32))), 0)

    def test_soft_limit(self):
        x = np.array([0.5, 0.9, 1.5, -1.5], np.float32)
        y = recorder.soft_limit(x.copy())
        self.assertEqual(y[0], 0.5)
        self.assertTrue(0.8 < y[1] < 0.9)
        self.assertTrue(np.all(np.abs(y) <= 1.0))
        self.assertEqual(y[2], -y[3])


class SourceTimelineTests(unittest.TestCase):
    """Linea de tiempo comun (07/11/2026): cada bloque va a su posicion y el escritor toma por posicion. Antes, tras un
    corte una fuente quedaba adelantada para siempre (medido: el eco de 30 ms pasaba a 330 ms)."""

    def _src(self):
        return recorder.Source("sys", "default")  # sin start(): solo la cola

    def blk(self, v, n=480):
        return np.full((n, 2), v, np.float32)

    def feed(self, s, start, count, v=1.0, n=480, late=0):
        """count bloques seguidos de n muestras desde la posicion start; `late` = llegan con ese retraso."""
        for i in range(count):
            s.push(self.blk(v, n), end=start + (i + 1) * n + late)
        return start + count * n

    def test_rellena_con_silencio(self):
        out = self._src().take_window(0, 100)
        self.assertEqual(out.shape, (100, 2))
        self.assertFalse(out.any())

    def test_consume_por_ventanas(self):
        s = self._src()
        self.feed(s, 0, 3, v=1.0)
        out = s.take_window(0, 1000)
        self.assertEqual(out[:, 0].sum(), 1000)
        out = s.take_window(1000, 1000)  # 440 reales + silencio
        self.assertEqual(out[:, 0].sum(), 440)
        self.assertEqual(s.frames, 0)

    def test_corte_confirmado_se_recoloca(self):
        s = self._src()
        pos = self.feed(s, 0, 20)  # 20 bloques seguidos (9.600 muestras)
        gap = int(0.3 * recorder.SR)  # se pierden 300 ms (reabrir el dispositivo)
        self.feed(s, pos + gap, s.CONFIRM + 3, v=2.0)  # lo que llega despues, en su hora
        self.assertAlmostEqual(s.gap_s, 0.3, places=2)
        out = s.take_window(0, pos + gap + 480 * 4)
        self.assertTrue((out[:pos, 0] == 1).all())
        self.assertFalse(out[pos:pos + gap].any())  # el corte, como silencio
        self.assertTrue((out[pos + gap:, 0] == 2).all())  # y lo de despues en su sitio, no 300 ms antes

    def test_un_retraso_al_leer_no_es_un_corte(self):
        """soundcard da un paquete por llamada: si el hilo se retrasa 90 ms, el primero llega tarde y los demas
        atrasados enseguida, seguidos. Nada se pierde: no se mueve nada."""
        s = self._src()
        pos = self.feed(s, 0, 20)
        late = int(0.09 * recorder.SR)
        s.push(self.blk(3.0), end=pos + 480 + late)  # el primero, tarde
        for i in range(2, 30):  # los atrasados, uno tras otro casi a la vez; luego al ritmo normal
            s.push(self.blk(3.0), end=pos + 480 + late + i * 10 if i < 10 else pos + i * 480 + 480)
        self.assertEqual(s.gap_s, 0.0)
        out = s.take_window(0, pos + 480 * 29)
        self.assertTrue((out[:pos, 0] == 1).all())
        self.assertTrue((out[pos:, 0] == 3).all())

    def test_reloj_rapido_se_recoloca_hacia_atras(self):
        s = self._src()
        pos = self.feed(s, 0, 20)
        early = int(0.08 * recorder.SR)
        self.feed(s, pos - early, s.CONFIRM + 2, v=2.0)  # llegan antes de lo que tocaria
        self.assertAlmostEqual(s.gap_s, -0.08, places=2)

    def test_lo_que_llega_tarde_cuenta_como_perdido(self):
        s = self._src()
        s.take_window(0, 4800)  # ese tramo ya esta escrito
        s.push(self.blk(1.0), end=4800)  # y llega ahora
        s.take_window(4800, 480)
        self.assertEqual(s.lost, 480)

    def test_tope_de_memoria(self):
        s = self._src()
        n = int(recorder.MAX_BUF_S * recorder.SR)
        self.feed(s, 0, 3, n=n)
        self.assertLessEqual(s.frames, n)
        self.assertGreater(s.lost, 0)


class AllOutputsTests(unittest.TestCase):
    def outputs(self, dups):
        class Fake:
            def __init__(self, v, name):
                self.v, self.name = v, name

            def take_window(self, w0, n):
                return np.full((n, 2), self.v, np.float32)

            def status(self):
                return {"name": self.name, "db": -20.0, "error": None, "note": None, "lost": 0.0}
        a = recorder.AllOutputs.__new__(recorder.AllOutputs)
        a.sources, a.scanned, a.logs = {"x": Fake(0.1, "Altavoces"), "y": Fake(0.2, "Espejo")}, 1e18, []
        a.log, a.dups = a.logs.append, dups
        return a

    def test_si_falla_la_deteccion_se_suman_todas(self):
        class Rota:
            def feed(self, parts):
                raise ValueError("rota")
        a = self.outputs(Rota())
        out = a.take_window(0, 480)
        np.testing.assert_allclose(out, 0.3, rtol=1e-5)  # la grabacion sigue: todas sumadas
        self.assertIsNone(a.dups)
        self.assertIn("se desactiva (se suman todas)", a.logs[0])
        self.assertEqual(a.status()["name"], "Altavoces + Espejo")

    def test_la_copia_sale_en_el_medidor(self):
        import eco
        d = eco.Duplicates()
        d.muted = {"y": ("x", 640)}
        a = self.outputs(d)
        np.testing.assert_allclose(a.take_window(0, 480), 0.1, rtol=1e-5)
        self.assertEqual(a.status()["name"], "Altavoces + Espejo (igual que Altavoces: no se suma)")


class LevelerUpdateTests(unittest.TestCase):
    def test_sin_actualizar_no_sube_con_el_eco(self):
        lev = recorder.Leveler(*recorder.GAIN_RANGE["mic"])
        x = (np.random.default_rng(0).standard_normal((2400, 2)) * 0.01).astype(np.float32)
        for _ in range(30):
            lev(x.copy(), update=False)  # mientras suena la reunion: el eco no cuenta como su voz
        self.assertIsNone(lev.level)
        self.assertEqual(lev.gain, 1.0)


if __name__ == "__main__":
    unittest.main()
