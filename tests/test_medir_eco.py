# -*- coding: utf-8 -*-
"""scripts/medir_eco.py: con WAV sinteticos de respuesta conocida (copia fija, sin copia, retardo que cambia)."""
import os
import shutil
import sys
import tempfile
import unittest
import wave

import numpy as np

from support import REPO
from test_eco import delay, voz

sys.path.insert(0, os.path.join(REPO, "scripts"))
import medir_eco  # noqa: E402


class MedirEcoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-medir-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def wav(self, x, name="r.wav"):
        p = os.path.join(self.tmp, name)
        with wave.open(p, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
        return p

    def run_tool(self, x):
        return medir_eco.main([self.wav(x), "--json"])

    def test_copia_fija_digital(self):
        v = voz(20, seed=1)
        res = self.run_tool(v + delay(v, 120, 0.9))
        self.assertIn("FIJO de 120 ms", res["veredicto"])
        self.assertIn("casi identica", res["veredicto"])
        self.assertAlmostEqual(res["copia_db_max"], 20 * np.log10(0.9), delta=1.5)

    def test_sin_copia(self):
        res = self.run_tool(voz(20, seed=1) + voz(20, f0=190, seed=4))
        self.assertIn("no hay voces dobles", res["veredicto"])

    def test_retardo_que_cambia(self):
        v = voz(20, seed=2)
        x = v.copy()
        for k, ms in enumerate((80, 380, 700, 1000)):  # cada corte la desplazaba (C)
            a, b = k * 5 * 48000, (k + 1) * 5 * 48000
            x[a:b] += delay(v, ms, 0.8)[a:b]
        res = self.run_tool(x)
        self.assertIn("CAMBIA", res["veredicto"])

    def test_nivel_de_la_copia(self):
        self.assertAlmostEqual(medir_eco.copy_level_db(0.5 * 0.9 / (1 + 0.81) * 2), 20 * np.log10(0.9), delta=0.2)
        self.assertIsNone(medir_eco.copy_level_db(0.0))


if __name__ == "__main__":
    unittest.main()
