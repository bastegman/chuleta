# -*- coding: utf-8 -*-
"""Voces dobles en la grabadora propia (recorder.py): copias repetidas y eco. numpy puro, se prueba sin dispositivos.

La grabadora suma el micro y lo que suena en el PC. Una voz acaba dos veces en el fichero por tres caminos:
  A) dos salidas con la misma señal en «todas las salidas» (un dispositivo virtual o el «espejo» de un driver), o una
     salida que lleva el micro («Escuchar este dispositivo», tono lateral): se suma una copia retrasada;
  B) eco: con altavoces, el micro recoge lo que suena unas decenas de ms despues;
  C) fuentes desalineadas tras un corte de audio (lo arregla la linea de tiempo comun de recorder.Source).
Duplicates quita la copia entre salidas (A). Gate atenua la copia en el micro (B) o la del micro en la salida (A), pero
solo cuando la correlacion cruzada ha demostrado esa copia, en ese sentido y con su retardo: sin copia no toca nada.
Todo se calcula sobre lo mismo que se va a escribir (las partes que el escritor toma a la vez de cada fuente).
"""
import math
from collections import deque

import numpy as np

SR = 48000
DEC = 6                   # se correlaciona a 8 kHz (media de cada 6 muestras: basta para la voz)
SR_C = SR // DEC
WIN_S, HOP_S = 2.0, 0.5   # ventana que se compara y cada cuanto (medido: con 1 s y 3 pruebas tardaba ~8 s)
MAX_LAG_S = 0.6           # retardo maximo que se busca entre dos copias
SILENT_DB = -50.0         # por debajo, lo que suena (las salidas) es silencio
FLOOR_DB = -75.0          # el micro en crudo es flojo (su eco, hasta -55 dBFS): solo se descarta el silencio


# filtro antes de bajar a 8 kHz (sinc con ventana de Hamming, 3,4 kHz): sin el, dos copias desplazadas una fraccion
# de muestra (cada dispositivo tiene su reloj) dejaban de parecerse por el aliasing de los agudos (medido: 0,84-0,92
# en vez de ~1)
_TAPS = 49
_H = (np.sinc(2 * 3400 / SR * (np.arange(_TAPS) - (_TAPS - 1) / 2)) * np.hamming(_TAPS)).astype(np.float32)
_H /= _H.sum()


def db(x):
    """Nivel RMS en dBFS (-100 en silencio)."""
    if not len(x):
        return -100.0
    r = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return 20 * math.log10(r) if r > 1e-5 else -100.0


class Ring:
    """Los ultimos WIN_S segundos de una señal a 8 kHz y mono, alimentada con trozos de cualquier tamaño a 48 kHz:
    filtro paso bajo con su estado y la fase del diezmado entre llamadas (si no, las señales se desalinearian una
    muestra cada pocas llamadas)."""

    def __init__(self, seconds=WIN_S):
        self.n = int(seconds * SR_C)
        self.buf = np.zeros(self.n, np.float32)
        self.tail = np.zeros(_TAPS - 1, np.float32)
        self.phase = 0
        self.count = 0  # muestras (a 8 kHz) recibidas en total

    def push(self, x):
        m = (x.mean(axis=1) if x.ndim == 2 else x).astype(np.float32)
        if not len(m):
            return
        full = np.concatenate([self.tail, m])
        self.tail = full[-(_TAPS - 1):]
        f = np.convolve(full, _H, mode="valid")  # una salida por muestra de m
        idx = np.arange(self.phase, len(f), DEC)
        self.phase = int(idx[-1] + DEC - len(f)) if len(idx) else self.phase - len(f)
        if not len(idx):
            return
        y = f[idx]
        if len(y) >= self.n:
            self.buf[:] = y[-self.n:]
        else:
            self.buf[:-len(y)] = self.buf[len(y):]
            self.buf[-len(y):] = y
        self.count += len(y)

    def full(self):
        return self.count >= self.n

    def level(self):
        return db(self.buf)


def xcorr(a, b, max_lag):
    """Correlacion cruzada normalizada (por el solape de cada retardo) de dos señales de igual longitud.
    Devuelve (lags, r): r[i] corresponde al retardo lags[i]; retardo > 0 = `b` va detras de `a` (b(t) ≈ a(t - lag))."""
    n = len(a)
    a = np.asarray(a, np.float64) - float(np.mean(a))
    b = np.asarray(b, np.float64) - float(np.mean(b))
    size = 1 << int(math.ceil(math.log2(max(2, 2 * n))))
    c = np.fft.irfft(np.conj(np.fft.rfft(a, size)) * np.fft.rfft(b, size), size)  # c[k] = sum a[t] b[t + k]
    lags = np.arange(-max_lag, max_lag + 1)
    raw = c[lags % size]
    ca, cb = np.concatenate([[0.0], np.cumsum(a * a)]), np.concatenate([[0.0], np.cumsum(b * b)])
    # solape de cada retardo: k >= 0 → a[0:n-k] con b[k:n]; k < 0 → a[-k:n] con b[0:n+k]
    k = np.abs(lags)
    ea = np.where(lags >= 0, ca[n - k], ca[n] - ca[k])
    eb = np.where(lags >= 0, cb[n] - cb[k], cb[n - k])
    den = np.sqrt(np.maximum(ea * eb, 1e-18))
    r = np.where((ea > 1e-12) & (eb > 1e-12) & (k < n * 0.6), raw / den, 0.0)
    return lags, r


def peak(lags, r, lo, hi):
    """Mayor |r| con retardo en [lo, hi] (muestras a 8 kHz): (retardo, valor con su signo) o (None, 0.0)."""
    sel = (lags >= lo) & (lags <= hi)
    if not sel.any():
        return None, 0.0
    i = int(np.argmax(np.abs(r[sel])))
    return int(lags[sel][i]), float(r[sel][i])


class Duplicates:
    """«Todas las salidas»: una salida que es SOLO una copia retrasada de otra no se suma (se sigue leyendo, para no
    acumular retraso). Copia = correlacion >= NCC con el mismo retardo (±LAG_TOL) en CONFIRM ventanas seguidas y, sobre
    todo, que al quitarle la mejor copia de la otra le quede menos de PURE de su energia (-17 dB): si lleva ademas
    otra cosa (una voz o un sonido solo suyo), se sigue sumando (refutador del 07/11/2026: silenciarla borraba eso).
    Se queda la mas fuerte (a igualdad, ±3 dB, la que suena antes). En cada analisis se vuelve a comprobar: en cuanto
    lleva otra cosa (o suena ella sola), vuelve a sumarse. Como mucho PAIRS parejas por analisis (coste acotado con
    muchas salidas)."""

    NCC, CONFIRM, LAG_TOL, PURE, IMPURE, PAIRS = 0.7, 2, 16, 0.02, 0.05, 2  # PURE: lo propio, por debajo de -17 dB

    def __init__(self, log=None):
        self.rings, self.muted, self.cand, self.rr, self.gain = {}, {}, {}, 0, {}
        self.since, self.log, self.mixed = 0, log or (lambda msg: None), set()

    def feed(self, parts):
        """parts: {id: (n, 2)} tomados a la vez. Devuelve la suma de las que se suman."""
        n = 0
        for i, x in parts.items():
            self.rings.setdefault(i, Ring()).push(x)
            n = len(x)
        for i in list(self.rings):
            if i not in parts:  # salida que ya no esta
                self.rings.pop(i)
                self.muted.pop(i, None)
                self.muted = {j: m for j, m in self.muted.items() if m[0] != i}
        self.since += n
        if self.since >= HOP_S * SR:
            self.since = 0
            self.analyze()
        out = np.zeros((n, 2), np.float32)
        for i, x in parts.items():
            g0 = self.gain.get(i, 1.0)
            g1 = 0.0 if i in self.muted else 1.0
            self.gain[i] = g1
            if g0 == g1 == 0.0:
                continue
            if g0 == g1:
                out += x
            else:  # quitar o volver a sumar una copia con un fundido de 10 ms, sin clic
                k = min(n, SR // 100)
                ramp = np.ones((n, 1), np.float32) * g1
                ramp[:k, 0] = np.linspace(g0, g1, k, dtype=np.float32)
                out += x * ramp
        return out

    @staticmethod
    def residual(lead, follow, lag, pre=4, post=27, seconds=0.5):
        """Parte de la energia de `follow` que NO explica `lead` retrasada `lag` (follow(t) ≈ Σ h_k·lead(t - lag - k),
        k = -pre..post): ~0 si es solo una copia, ~1 si no tiene nada que ver, None si lo mirado de `follow` es
        silencio (en las pausas no se decide nada: si no, una copia se soltaba en cada pausa de la reunion). Un
        filtro corto ajustado por minimos
        cuadrados y no una sola ganancia: cada dispositivo tiene su reloj, el retardo no es un numero entero de
        muestras y con una ganancia sola una copia exacta dejaba un 10-30 % «propio». Asimetrico (lo que una
        ecualizacion añade suena despues, no antes) y sobre lo ultimo (`seconds`): con relojes que se separan
        200 ppm, en 2 s el retardo cambia 3 muestras y ningun filtro fijo lo sigue (refutador, 2.ª ronda: con 5
        coeficientes sobre 2 s, 6-18 % «propio» con 208 ppm o con +9 dB de ecualizacion). Con contenido que no es copia,
        32 coeficientes sobre ~4.000 muestras solo explican ~1 % por azar."""
        k = int(seconds * SR_C) + abs(lag) + pre + post
        lead, follow = lead[-k:], follow[-k:]
        n = len(lead)
        lo, hi = max(0, lag + post), min(n, n + lag - pre)
        if hi - lo < SR_C // 4:
            return None
        f = follow[lo:hi].astype(np.float64)
        ef = float(f @ f)
        if ef <= (hi - lo) * 1e-6:  # lo mirado de `follow` es silencio (< -60 dBFS): no se puede saber (None)
            return None
        a = np.stack([lead[lo - lag - j:hi - lag - j] for j in range(-pre, post + 1)], axis=1).astype(np.float64)
        h = np.linalg.lstsq(a, f, rcond=None)[0]
        res = f - a @ h
        return float(res @ res) / ef

    def analyze(self):
        ids = sorted(i for i, rg in self.rings.items() if rg.full())
        lvl = {i: self.rings[i].level() for i in ids}
        max_lag = int(MAX_LAG_S * SR_C)
        for j, (lead, lag) in list(self.muted.items()):  # ¿sigue siendo solo una copia?
            if j not in lvl or lvl[j] <= SILENT_DB:
                continue
            r = 1.0
            if lead in lvl:  # el retardo puede moverse (cada fuente corrige su reloj): se busca cerca, sobre lo
                near = int(0.03 * SR_C)  # mismo que mira el residuo (lo ultimo), y se apunta
                tail = int(0.5 * SR_C) + max_lag
                a_t, b_t = self.rings[lead].buf[-tail:], self.rings[j].buf[-tail:]
                lags, rr = xcorr(a_t, b_t, max_lag)
                cands = {lag, peak(lags, rr, lag - near, lag + near)[0] or lag}
                scored = [(self.residual(self.rings[lead].buf, self.rings[j].buf, c), c) for c in cands]
                scored = [(x, c) for x, c in scored if x is not None]
                if not scored:
                    continue
                r, lag = min(scored)
                self.muted[j] = (lead, lag)
            if r > self.IMPURE:
                self.muted.pop(j)
                self.log(f"salida {j}: ya no es solo una copia de {lead}: se vuelve a sumar")
        pairs = [(i, j) for x, i in enumerate(ids) for j in ids[x + 1:]
                 if lvl[i] > SILENT_DB and lvl[j] > SILENT_DB and i not in self.muted and j not in self.muted]
        if not pairs:
            return
        start = self.rr % len(pairs)
        self.rr += self.PAIRS
        for i, j in (pairs[start:] + pairs[:start])[:self.PAIRS]:
            lags, r = xcorr(self.rings[i].buf, self.rings[j].buf, max_lag)
            lag, c = peak(lags, r, -max_lag, max_lag)
            key = (i, j)
            if lag is None or c < self.NCC:
                self.cand.pop(key, None)
                continue
            prev = self.cand.get(key)
            count = prev[1] + 1 if prev and abs(prev[0] - lag) <= self.LAG_TOL else 1
            self.cand[key] = (lag, count)
            if count < self.CONFIRM:
                continue
            # se queda la mas fuerte; a igualdad (±3 dB), la que suena antes (j va `lag` detras de i)
            if abs(lvl[i] - lvl[j]) > 3:
                keep, drop, rel = (i, j, lag) if lvl[i] > lvl[j] else (j, i, -lag)
            else:
                keep, drop, rel = (i, j, lag) if lag >= 0 else (j, i, -lag)
            res = self.residual(self.rings[keep].buf, self.rings[drop].buf, rel)
            if res is None:
                continue
            if res <= self.PURE:
                self.muted[drop] = (keep, rel)
                self.mixed.discard(drop)
                self.log(f"salida duplicada: {drop} = {keep} con {abs(rel) * 1000 // SR_C} ms de retraso "
                         f"(parecido {c:.2f}, le queda {res * 100:.0f} % propio): no se suma")
            elif drop not in self.mixed:
                self.mixed.add(drop)
                self.log(f"salida {drop}: lleva lo mismo que {keep} con {abs(rel) * 1000 // SR_C} ms y ademas otra "
                         f"cosa ({res * 100:.0f} % propio): se sigue sumando")

    def note(self, names):
        """Para el medidor: «X (igual que Y: no se suma)»."""
        return {j: f"igual que {names.get(lead, lead)}: no se suma" for j, (lead, _) in self.muted.items()}


class Gate:
    """Atenua `x` mientras sea solo la copia de `ref`: el eco de los altavoces en el micro (ref = lo que suena) o la
    copia del micro en una salida (ref = el micro). Se activa solo cuando la correlacion demuestra que `x` lleva a
    `ref` retrasada (entre min_lag y MAX_LAG_S, |r| >= c_min en CONFIRM ventanas) y se desactiva tras OFF_S segundos de
    `ref` sonando sin esa prueba (p. ej. se han puesto auriculares). Mientras esta activa, en cada trozo de 10 ms:
    si `x` no pasa de lo que daria la copia (nivel de `ref` hace `lag`, menos la atenuacion medida del camino, mas
    MARGIN_DB), se baja lo bastante para dejar la copia ~32 dB por debajo del original (entre DUCK_MIN y DUCK_MAX dB,
    segun la atenuacion medida del camino); si pasa (alguien habla encima), no se toca."""

    FRAME = SR // 100
    DUCK_MIN, DUCK_MAX, MARGIN_DB, HOLD = 14.0, 20.0, 6.0, 20  # HOLD: 200 ms de cola (reverberacion) tras `ref`
    CONFIRM, OFF_S, OFF_S_AVISO = 2, 20.0, 8.0  # solo avisando, el aviso se quita antes (p. ej. al ponerse cascos)

    def __init__(self, name, c_min, min_lag_s, log=None, act=True):
        """act=False: solo mide y avisa (detecta la copia, su retardo y su atenuacion) sin tocar el audio."""
        self.name, self.c_min, self.min_lag, self.act = name, c_min, int(min_lag_s * SR_C), act
        self.rx, self.rr = Ring(), Ring()
        keep = int(MAX_LAG_S * 100) + self.HOLD + 2  # niveles de ref por trozo de 10 ms: retardo maximo + cola
        self.hist = deque([-100.0] * keep, maxlen=keep)
        self.erls = deque(maxlen=600)  # (nivel de ref en su sitio - nivel de x) con ref sonando: 6 s
        self.erl, self.lag, self.enabled, self.cand, self.off = 10.0, None, False, None, 0.0
        self.gain, self.since, self.ducked = 1.0, 0, 0.0
        self.log = log or (lambda msg: None)

    def analyze(self):
        if not (self.rx.full() and self.rr.full()) or self.rr.level() <= SILENT_DB:
            return
        m = int(MAX_LAG_S * SR_C)
        lags, r = xcorr(self.rr.buf, self.rx.buf, m)
        lag, c = peak(lags, r, self.min_lag, m)
        # el otro sentido (x va DELANTE de ref): una copia en ese sentido da picos laterales en este (la voz es casi
        # periodica: 5-12 ms) y no debe activar esta puerta; solo cuenta si este lado domina
        other = peak(lags, r, -m, -max(1, self.min_lag))[1]
        found = lag is not None and abs(c) >= self.c_min and abs(c) >= abs(other) and self.rx.level() > FLOOR_DB
        if found:
            same = self.cand and abs(self.cand[0] - lag) <= 16
            self.cand = (lag, self.cand[1] + 1 if same else 1)
            if not self.enabled:
                self.lag = lag  # desde la primera prueba: asi se mide la atenuacion del camino antes de activar
            if self.cand[1] >= self.CONFIRM:
                if not self.enabled:
                    self.log(f"{self.name}: copia detectada con {lag * 1000 // SR_C} ms (parecido {abs(c):.2f})"
                             + (": se atenua" if self.act else ": solo se avisa, sin tocar el audio"))
                self.enabled, self.lag, self.off = True, lag, 0.0
        else:
            self.cand = None
            if self.enabled:
                self.off += HOP_S
                if self.off >= (self.OFF_S if self.act else self.OFF_S_AVISO):
                    self.enabled = False
                    self.log(f"{self.name}: ya no se ve la copia" + ("" if not self.act else ": deja de atenuar"))
        if len(self.erls) >= 50:
            self.erl = min(60.0, max(-20.0, float(np.percentile(self.erls, 80))))

    def __call__(self, x, ref):
        """x, ref: (n, 2) alineados. Devuelve (x, ref_sono): x atenuado donde es copia; ref_sono = `ref` ha sonado en
        este trozo o en su cola (para no subir el nivel del micro con el eco)."""
        self.rx.push(x)
        self.rr.push(ref)
        self.since += len(x)
        if self.since >= HOP_S * SR:
            self.since = 0
            self.analyze()
        d = int(round((self.lag or 0) / (SR_C / 100)))  # retardo en trozos de 10 ms
        out = x
        sono = False
        for a in range(0, len(x), self.FRAME):
            b = min(len(x), a + self.FRAME)
            lx, lr = db(x[a:b]), db(ref[a:b])
            self.hist.append(lr)
            h = list(self.hist)
            src = max(h[len(h) - 1 - d - self.HOLD:len(h) - d]) if self.lag is not None else max(h[-self.HOLD - 1:])
            active = src > SILENT_DB
            sono = sono or active
            target = 1.0
            if active and self.lag is not None:
                if h[len(h) - 1 - d] > SILENT_DB:
                    self.erls.append(h[len(h) - 1 - d] - lx)
                if self.act and self.enabled and lx < src - self.erl + self.MARGIN_DB:
                    target = 10 ** (-self.duck_db() / 20)
            if target < self.gain:   # bajar rapido (un trozo); subir algo mas despacio (~50 ms)
                g = target
            else:
                g = min(target, self.gain * 10 ** (self.DUCK_MAX / 20 / 5))
            if g != 1.0 or self.gain != 1.0:
                if out is x:
                    out = x.copy()
                out[a:b] *= np.linspace(self.gain, g, b - a, dtype=np.float32)[:, None]
            if g < 1.0:
                self.ducked += (b - a) / SR
            self.gain = g
        return out, sono

    def duck_db(self):
        """Cuanto se baja la copia: la deja ~32 dB por debajo de lo que la produce."""
        return min(self.DUCK_MAX, max(self.DUCK_MIN, 32.0 - self.erl))

    def status(self):
        return {"activa": self.enabled, "retardo_ms": None if self.lag is None else self.lag * 1000 // SR_C,
                "atenuacion_db": round(self.erl, 1), "bajado_s": round(self.ducked, 1)}
