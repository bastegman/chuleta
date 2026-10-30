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


class SourceTakeTests(unittest.TestCase):
    def _src(self):
        return recorder.Source("mic", "default")  # sin start(): solo la cola

    def test_rellena_con_silencio(self):
        s = self._src()
        out = s.take(100)
        self.assertEqual(out.shape, (100, 2))
        self.assertFalse(out.any())

    def test_consume_por_trozos(self):
        s = self._src()
        a = np.ones((60, 2), np.float32)
        with s.lock:
            s.buf.append(a)
            s.buf.append(a * 2)
            s.frames += 120
        out = s.take(80)
        self.assertEqual(out.shape, (80, 2))
        self.assertEqual(out[:60].sum(), 120)
        self.assertEqual(out[60:].sum(), 80)
        self.assertEqual(s.frames, 40)
        out = s.take(100)  # 40 reales + 60 de silencio
        self.assertEqual(out.sum(), 160)
        self.assertEqual(s.frames, 0)

    def test_recorta_el_retraso(self):
        s = self._src()
        n = int(recorder.MAX_BACKLOG * recorder.SR)
        with s.lock:
            for _ in range(4):
                s.buf.append(np.zeros((n, 2), np.float32))
            s.frames = 4 * n
        s.take(10)
        self.assertLessEqual(s.frames, n)
        self.assertGreater(s.lost, 0)


if __name__ == "__main__":
    unittest.main()
