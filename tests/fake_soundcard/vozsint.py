"""Voz sintetica NO periodica: tono que deriva (no hay retardo en el que se repita), silabas de duracion aleatoria."""
import numpy as np

SR = 48000


def voz(seconds, f0=120.0, seed=0, on=None, sr=SR):
    r = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    drift = np.cumsum(r.standard_normal(n // 480 + 2))[:, None].repeat(480, 1).ravel()[:n]
    drift = (drift - drift.mean()) / (np.abs(drift).max() + 1e-9)
    f = f0 * (1 + 0.15 * np.sin(2 * np.pi * 0.37 * t + r.uniform(0, 6)) + 0.1 * drift)
    phase = 2 * np.pi * np.cumsum(f) / sr
    harm = sum(np.sin(k * phase + r.uniform(0, 6)) / k for k in range(1, 12))
    x = 0.6 * harm / 3 + 0.4 * r.standard_normal(n)
    env = np.zeros(n)
    i = 0
    while i < n:  # silabas de 80-350 ms con pausas de 40-250 ms
        a = int(r.uniform(0.08, 0.35) * sr)
        env[i:i + a] = 1
        i += a + int(r.uniform(0.04, 0.25) * sr)
    env = np.convolve(env, np.ones(480) / 480, mode="same")
    x = x * env * 0.1
    if on is not None:
        x = x * on(t)
    return x.astype(np.float32)
