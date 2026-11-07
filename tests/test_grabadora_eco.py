# -*- coding: utf-8 -*-
"""La grabadora real (recorder.py) con un soundcard falso que el test escribe en un directorio temporal: la reunion
sale por dos salidas (la segunda, «espejo» de la primera con 80 ms), el micro lleva su voz y el eco de los altavoces
(0,1 a 30 ms) y la primera salida pierde 300 ms a los 4 s (reabrir el dispositivo). Asi se encontro y se mide el
arreglo del 07/11/2026: sin el, el fichero llevaba la reunion dos veces a 380 ms (80 del espejo + 300 del corte)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from support import APP, REPO

sys.path.insert(0, os.path.join(REPO, "scripts"))
import medir_eco  # noqa: E402

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_soundcard")  # soundcard.py falso


class GrabadoraConEcoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuleta-eco-")
        cls.wav = os.path.join(cls.tmp, "r.wav")
        env = dict(os.environ, PYTHONPATH=FAKE, FAKESC_GAP="4.0-4.3", PYTHONIOENCODING="utf-8")
        p = subprocess.Popen([sys.executable, "-u", os.path.join(APP, "recorder.py"), cls.wav, "--mic", "default",
                              "--sys", "all"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, cwd=APP)
        # se lee mientras graba, como RecorderProcess: en Windows la tuberia tiene poco bufer y, sin leer, la
        # grabadora se queda parada al escribir su estado (medido en la CI del 07/11/2026: 0,4-1,8 s «perdidos»)
        out, err = [], []
        readers = [threading.Thread(target=lambda f=f, b=b: b.extend(f), daemon=True)
                   for f, b in ((p.stdout, out), (p.stderr, err))]
        for t in readers:
            t.start()
        time.sleep(13)
        p.stdin.write(b"stop\n")
        p.stdin.flush()
        p.wait(timeout=60)
        for t in readers:
            t.join(timeout=10)
        cls.rc = p.returncode
        cls.log = b"".join(err).decode("utf-8", "replace")
        lines = [json.loads(l) for l in b"".join(out).decode("utf-8", "replace").splitlines() if l.strip()]
        cls.last = [l for l in lines if "mic" in l][-1]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ms(self, pattern):
        m = re.search(pattern, self.log)
        self.assertIsNotNone(m, self.log)
        return float(m.group(1))

    def test_detecta_y_lo_dice(self):
        self.assertEqual(self.rc, 0, self.log)
        # los relojes de los dispositivos falsos dan ±2 ms
        self.assertAlmostEqual(self.ms(r"salida duplicada: spk2 = spk1 con (\d+) ms"), 80, delta=2)
        self.assertAlmostEqual(self.ms(r"eco de los altavoces en el micro: copia detectada con (\d+) ms"), 30, delta=2)
        corte = r"cortes de audio recolocados en su sitio \(s\): \{'mic': [\d.]+, 'sys': ([\d.]+)"
        self.assertAlmostEqual(self.ms(corte), 0.3, delta=0.02)
        self.assertIn("Espejo del driver (igual que Altavoces: no se suma)", self.last["sys"]["name"])
        self.assertIn("se oye la reunión por el micro (eco de", self.last["mic"]["note"])  # se avisa, no se toca
        self.assertTrue(self.last["eco"]["micro"]["activa"])

    def test_el_fichero_ya_no_lleva_la_copia(self):
        x, dur = medir_eco.read_wav(self.wav)
        self.assertGreater(dur, 10)
        tail = medir_eco.windows(x[-int(5 * 8000):], 5.0)[0]  # los ultimos 5 s, ya detectado todo
        self.assertTrue(tail["parecido"] is None or tail["parecido"] < medir_eco.UMBRAL, tail)


if __name__ == "__main__":
    unittest.main()
