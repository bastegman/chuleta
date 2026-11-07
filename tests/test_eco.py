# -*- coding: utf-8 -*-
"""Voces dobles (backlog §14, msgs 1154 y 1156): copias entre salidas, el micro en una salida y el eco de los
altavoces en el micro (_app/eco.py). Señales sinteticas parecidas a la voz y NO periodicas: con una voz periodica
(silabas a ritmo fijo) hay picos iguales en varios retardos y nada se confirma, que no es lo que pasa con voz real."""
import unittest

import numpy as np

from support import APP  # noqa: F401  (prepara sys.path)
import eco  # noqa: E402

SR = eco.SR


def voz(seconds, f0=120.0, seed=0, on=None):
    """Voz sintetica: armonicos de un tono que deriva + ruido, silabas de 80-350 ms con pausas de 40-250 ms."""
    r = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    drift = np.cumsum(r.standard_normal(n // 480 + 2))[:, None].repeat(480, 1).ravel()[:n]
    drift = (drift - drift.mean()) / (np.abs(drift).max() + 1e-9)
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.15 * np.sin(2 * np.pi * 0.37 * t + r.uniform(0, 6)) + 0.1 * drift)) / SR
    x = 0.6 * sum(np.sin(k * phase + r.uniform(0, 6)) / k for k in range(1, 12)) / 3 + 0.4 * r.standard_normal(n)
    env, i = np.zeros(n), 0
    while i < n:
        a = int(r.uniform(0.08, 0.35) * SR)
        env[i:i + a] = 1
        i += a + int(r.uniform(0.04, 0.25) * SR)
    x = x * np.convolve(env, np.ones(480) / 480, mode="same") * 0.1
    if on is not None:
        x = x * on(t)
    return np.repeat(x.astype(np.float32)[:, None], 2, axis=1)


def delay(x, ms, gain=1.0):
    d = int(ms * SR / 1000)
    y = np.zeros_like(x)
    y[d:] = x[:len(x) - d] * gain
    return y


def blocks(fn, *sigs, blk=2401):
    """Como el escritor: trozos de tamaño cualquiera (2401 no es multiplo de 6 ni de 480)."""
    return [fn(*[s[i:i + blk] for s in sigs]) for i in range(0, len(sigs[0]), blk)]


def lvl(x, a, b):
    return eco.db(x[int(a * SR):int(b * SR)])


class XcorrTests(unittest.TestCase):
    def test_signo_y_valor(self):
        a = voz(2, seed=1)[:, 0][::6]
        b = np.zeros_like(a)
        b[400:] = a[:-400]  # b va 50 ms detras (a 8 kHz)
        lags, r = eco.xcorr(a, b, 4800)
        lag, c = eco.peak(lags, r, -4800, 4800)
        self.assertEqual(lag, 400)
        self.assertGreater(c, 0.99)  # normalizado por el solape: una copia exacta da ~1 aunque falte el principio
        self.assertEqual(eco.peak(*eco.xcorr(b, a, 4800), -4800, 4800)[0], -400)

    def test_silencio(self):
        lags, r = eco.xcorr(np.zeros(16000), voz(2)[:, 0][::6], 4800)
        self.assertEqual(float(np.abs(r).max()), 0.0)

    def test_ring_con_trozos_cualquiera(self):
        """Trocear en tamaños cualquiera (como el escritor) da lo mismo que filtrar y diezmar todo de una vez."""
        x = voz(3, seed=2)
        rg = eco.Ring(2.0)
        blocks(rg.push, x, blk=2401)
        m = x.mean(axis=1)
        ref = np.convolve(np.concatenate([np.zeros(eco._TAPS - 1, np.float32), m]), eco._H, mode="valid")[::6]
        np.testing.assert_allclose(rg.buf, ref[-rg.n:], atol=1e-5)
        self.assertEqual(rg.count, len(ref))


class DuplicatesTests(unittest.TestCase):
    def test_copia_con_80_ms_no_se_suma(self):
        v = voz(10, seed=1)
        logs = []
        d = eco.Duplicates(log=logs.append)
        mix = np.concatenate(blocks(lambda a, b: d.feed({"A": a, "B": b}), v, delay(v, 80)))
        self.assertEqual(d.muted, {"B": ("A", 640)})  # 80 ms a 8 kHz
        self.assertIn("B = A con 80 ms", logs[0])
        np.testing.assert_allclose(mix[-4 * SR:], v[-4 * SR:], atol=1e-6)  # una sola voz
        self.assertEqual(d.note({"A": "Altavoces", "B": "Espejo"}), {"B": "igual que Altavoces: no se suma"})

    def test_salidas_distintas_se_suman(self):
        a, b = voz(10, seed=1), voz(10, f0=200, seed=7)
        d = eco.Duplicates()
        mix = np.concatenate(blocks(lambda x, y: d.feed({"A": x, "B": y}), a, b))
        self.assertEqual(d.muted, {})
        np.testing.assert_allclose(mix, a + b, atol=1e-6)

    def test_si_la_copia_lleva_otra_cosa_vuelve(self):
        v = voz(8, seed=1)
        d = eco.Duplicates()
        blocks(lambda a, b: d.feed({"A": a, "B": b}), v, delay(v, 80))
        self.assertIn("B", d.muted)
        otra = voz(6, f0=220, seed=9)
        blocks(lambda a, b: d.feed({"A": a, "B": b}), np.zeros_like(otra), otra)  # A calla, B suena sola
        self.assertEqual(d.muted, {})

    def test_salida_que_desaparece(self):
        v = voz(8, seed=1)
        d = eco.Duplicates()
        blocks(lambda a, b: d.feed({"A": a, "B": b}), v, delay(v, 80))
        out = d.feed({"B": v[:2400]})  # se ha quitado A: B ya no es copia de nadie
        self.assertEqual(d.muted, {})
        np.testing.assert_allclose(out, v[:2400])


class GateTests(unittest.TestCase):
    def eco_micro(self, mic, sysx):
        g = eco.Gate("eco", 0.25, 0.003)
        out = blocks(lambda a, b: g(a, b), mic, sysx)
        return g, np.concatenate([o[0] for o in out]), [o[1] for o in out]

    def test_eco_de_40_ms_baja_al_menos_12_db_y_la_voz_sola_pasa(self):
        ref = voz(14, seed=2, on=lambda t: (t < 10).astype(float))
        mine = voz(14, f0=180, seed=3, on=lambda t: ((t > 10.5) | ((t > 7) & (t < 9))).astype(float)) * 2
        x = delay(ref, 40, 0.1) + mine  # eco a -20 dB
        g, y, _ = self.eco_micro(x, ref)
        st = g.status()
        self.assertTrue(st["activa"])
        self.assertEqual(st["retardo_ms"], 40)
        self.assertAlmostEqual(st["atenuacion_db"], 20.0, delta=1.5)
        self.assertLessEqual(lvl(y, 5, 7) - lvl(x, 5, 7), -12.0)  # (c) solo eco
        self.assertAlmostEqual(lvl(y, 11, 14), lvl(x, 11, 14), delta=0.05)  # (d) su voz sola, intacta
        self.assertGreater(lvl(y, 7.2, 8.8) - lvl(x, 7.2, 8.8), -2.0)  # hablando encima: casi intacta

    def test_eco_de_sala_filtrado_tambien(self):
        ref = voz(12, seed=4)
        room = np.zeros_like(ref)
        for ms, g in ((25, 0.06), (32, 0.04), (45, 0.03), (70, 0.02), (110, 0.01)):  # reflexiones
            room += delay(ref, ms, g)
        k = np.ones(8) / 8  # altavoz de portatil: sin agudos
        room = np.stack([np.convolve(room[:, c], k, mode="same") for c in (0, 1)], axis=1).astype(np.float32)
        g, y, _ = self.eco_micro(room, ref)
        self.assertTrue(g.status()["activa"])
        self.assertLessEqual(lvl(y, 8, 12) - lvl(room, 8, 12), -10.0)

    def test_sin_eco_auriculares_no_toca_nada(self):
        ref, mine = voz(12, seed=5), voz(12, f0=170, seed=6) * 2
        g, y, busy = self.eco_micro(mine, ref)
        self.assertFalse(g.status()["activa"])
        np.testing.assert_array_equal(y, mine)
        self.assertTrue(any(busy))  # la reunion suena: el nivel del micro no se reestima entonces

    def test_se_desactiva_al_quitarse_el_eco(self):
        ref = voz(40, seed=2)
        x = delay(ref, 40, 0.1)
        x[int(10 * SR):] = voz(30, f0=190, seed=8)[:len(x) - int(10 * SR)]  # a los 10 s: auriculares
        g, y, _ = self.eco_micro(x, ref)
        self.assertFalse(g.status()["activa"])
        self.assertAlmostEqual(lvl(y, 35, 40), lvl(x, 35, 40), delta=0.05)

    def test_el_micro_en_una_salida(self):
        mic = voz(12, f0=150, seed=4, on=lambda t: ((t % 4) < 2).astype(float))
        other = voz(12, seed=5, on=lambda t: ((t % 4) >= 2.5).astype(float))
        sysx = other + delay(mic, 30, 0.7)
        g = eco.Gate("micro en la salida", 0.6, 0.0)
        y = np.concatenate([o[0] for o in blocks(lambda a, b: g(a, b), sysx, mic)])
        self.assertTrue(g.status()["activa"])
        self.assertLessEqual(lvl(y, 8.1, 9.9) - lvl(sysx, 8.1, 9.9), -12.0)  # la copia de su voz
        self.assertAlmostEqual(lvl(y, 10.6, 11.9), lvl(sysx, 10.6, 11.9), delta=0.05)  # lo demas

    def test_copia_del_micro_cercana_no_activa_la_puerta_de_eco(self):
        """Con el micro en la salida a 5 ms, los picos laterales de la voz (casi periodica) caen en el lado del «eco»:
        si se activara, se comeria su voz."""
        mic = voz(12, f0=150, seed=4)
        g, y, _ = self.eco_micro(mic, delay(mic, 5, 0.9))
        self.assertFalse(g.status()["activa"])
        np.testing.assert_array_equal(y, mic)


if __name__ == "__main__":
    unittest.main()
