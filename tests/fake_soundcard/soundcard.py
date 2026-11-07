"""soundcard falso para tests/test_grabadora_eco.py (no es un test): una reunion por dos salidas (spk2 = spk1 con
80 ms: «espejo») y el micro con la voz del usuario mas el eco de los altavoces (0,1 a 30 ms). FAKESC_GAP="a-b": spk1
pierde lo de esa ventana (un corte)."""
import os
import time

import numpy as np

T0 = time.perf_counter()
SR = 48000
DUR = 30


from vozsint import voz  # noqa: E402

MEET = voz(DUR, 120, 1, lambda t: ((t % 5) < 3).astype(float))
MINE = voz(DUR, 180, 2, lambda t: (((t % 5) >= 3.2) & ((t % 5) < 4.8)).astype(float)) * 2


def _at(sig, wall):
    i = np.clip((wall * SR).astype(np.int64), 0, len(sig) - 1)
    return np.where((wall >= 0) & (wall * SR < len(sig)), sig[i], 0)


def _gaps():
    out = []
    for part in (os.environ.get("FAKESC_GAP") or "").split(","):
        if part.strip():
            a, b = part.split("-")
            out.append((float(a), float(b)))
    return out


class _Rec:
    def __init__(self, dev, channels):
        self.dev, self.ch, self.start, self.given = dev, channels, time.perf_counter(), 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def record(self, numframes=None):
        time.sleep(0.01)
        due = int((time.perf_counter() - self.start) * SR) - self.given
        if due <= 0:
            return np.zeros((0, self.ch), np.float32)
        wall = self.start - T0 + (self.given + np.arange(due)) / SR
        self.given += due
        for a, b in self.dev.gaps:
            wall = wall[~((wall >= a) & (wall < b))]
        x = self.dev.signal(wall).astype(np.float32)
        return np.repeat(x[:, None], self.ch, axis=1)


class _Dev:
    def __init__(self, id, name, signal, gaps=()):
        self.id, self.name, self.signal, self.gaps = id, name, signal, list(gaps)

    def recorder(self, samplerate, channels=1, blocksize=None):
        return _Rec(self, channels)


_MIC = _Dev("mic1", "Micro", lambda w: _at(MINE, w) + 0.1 * _at(MEET, w - 0.03))
_S1 = _Dev("spk1", "Altavoces", lambda w: _at(MEET, w), _gaps())
_S2 = _Dev("spk2", "Espejo del driver", lambda w: _at(MEET, w - 0.08))


def all_microphones(include_loopback=False):
    return [_MIC]


def all_speakers():
    return [_S1, _S2]


def default_microphone():
    return _MIC


def default_speaker():
    return _S1


def get_microphone(id, include_loopback=False):
    return {"spk1": _S1, "spk2": _S2}.get(str(id), _MIC)
