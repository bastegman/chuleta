# -*- coding: utf-8 -*-
"""
Grabadora: el audio del ordenador (lo que suena por altavoces o cascos: Teams, Zoom, el
navegador…) mas el microfono, mezclados, como hace OBS.

Se ejecuta en un proceso aparte que lanza el servidor. Escribe un WAV 48 kHz estereo 16 bits
con la cabecera abierta (tamanos 0xFFFFFFFF) mientras graba, igual que OBS: asi el resto de la
app lo trata como una grabacion en curso y lo transcribe en directo. Al parar (orden "stop" o si
el servidor se cierra) corrige la cabecera y queda un WAV normal.

  python recorder.py --list                        dispositivos (JSON)
  python recorder.py --monitor --mic ID --sys ID   solo niveles, sin grabar (para probar)
  python recorder.py OUT.wav --mic ID --sys ID     grabar ("none" = sin esa fuente)

  --mic default  sigue al micro predeterminado de Windows (aunque cambie a mitad de grabacion)
  --sys all      todas las salidas a la vez (recomendado)   --sys default  la salida predeterminada

Por stdout sale una linea JSON de estado cada 0,25 s; por stdin se le manda "stop".
"""
import argparse
import json
import math
import struct
import sys
import threading
import time
import warnings
from collections import deque

SR = 48000
# margen para que lleguen los paquetes de las dos fuentes antes de mezclar: con Teams y el portatil
# cargados un paquete puede llegar tarde; con 1 s de margen casi nunca hay que rellenar con silencio
LAT = 1.0
MAX_BACKLOG = 1.5   # si una fuente se adelanta mas que esto (relojes distintos), se descarta lo sobrante

# Igualar niveles: el micro suele llegar mucho mas flojo que el audio de la reunion. Cada fuente se
# lleva poco a poco hacia el mismo volumen de voz, solo mientras hay sonido (en los silencios no se
# sube el ruido) y con limites. Rango de ganancia en dB por fuente.
TARGET_DB = -22.0
GATE_DB = -50.0
GAIN_RANGE = {"mic": (-6.0, 18.0), "sys": (-9.0, 9.0)}


class Leveler:
    """Ganancia automatica suave para una fuente (bloques de ~50 ms)."""

    def __init__(self, lo, hi):
        self.lo, self.hi = lo, hi
        self.level = None   # volumen de voz estimado (dBFS)
        self.gain = 1.0     # ganancia aplicada (lineal)

    def __call__(self, x):
        import numpy as np
        if not len(x):
            return x
        rms = float(np.sqrt(np.mean(x * x))) + 1e-12
        db = 20 * math.log10(rms)
        # hay sonido (y no es el ruido de fondo de las pausas, mucho mas flojo que la voz):
        # se actualiza la estimacion (sube rapido, baja despacio)
        if db > GATE_DB and (self.level is None or db > self.level - 15):
            if self.level is None:
                self.level = db
            else:
                k = 0.25 if db > self.level else 0.03
                self.level += k * (db - self.level)
        target = self.gain
        if self.level is not None:
            g_db = min(max(TARGET_DB - self.level, self.lo), self.hi)
            target = 10 ** (g_db / 20)
        # rampa dentro del bloque: sin saltos audibles
        ramp = np.linspace(self.gain, target, len(x), dtype=np.float32)[:, None]
        self.gain = target
        return x * ramp


def soft_limit(x, knee=0.8):
    """Por encima de 0,8 se curva suavemente hacia 1 en vez de recortar en seco (sin distorsion dura)."""
    import numpy as np
    a = np.abs(x)
    over = a > knee
    if over.any():
        x[over] = np.sign(x[over]) * (knee + (1 - knee) * np.tanh((a[over] - knee) / (1 - knee)))
    return x


warnings.filterwarnings("ignore")  # soundcard avisa de "discontinuidades" cada vez que hay silencio


def wav_header():
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, SR, SR * 4, 4, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))


def list_devices():
    import soundcard as sc
    dm, ds = sc.default_microphone(), sc.default_speaker()
    return {"mics": [{"id": m.id, "name": m.name, "default": m.id == dm.id} for m in sc.all_microphones()],
            "outputs": [{"id": s.id, "name": s.name, "default": s.id == ds.id} for s in sc.all_speakers()]}


class Source(threading.Thread):
    """Captura continua de un dispositivo a una cola en memoria (48 kHz, estereo float32).

    dev_id "default" sigue al dispositivo predeterminado de Windows aunque cambie a mitad de la
    grabacion (conectar o quitar los cascos). Con un dispositivo concreto: si se desconecta, pasa
    al predeterminado y vuelve al elegido cuando reaparece (salvo fallback=False)."""

    CHECK_S = 2.0  # cada cuanto se mira si Windows ha cambiado de dispositivo

    def __init__(self, kind, dev_id, fallback=True):
        super().__init__(daemon=True)
        self.kind, self.dev_id, self.fallback = kind, dev_id, fallback
        self.buf, self.frames, self.lock = deque(), 0, threading.Lock()
        self.peak, self.error, self.name, self.note, self.stopping = 0.0, None, None, None, False
        self.lost = 0  # muestras que llegaron demasiado tarde y no se pudieron usar (PC muy cargado)

    def _pick(self):  # (no llamarlo _target: Thread ya usa ese nombre)
        """Dispositivo que toca usar ahora mismo (cambia si Windows cambia el predeterminado)."""
        import soundcard as sc
        devs = sc.all_microphones() if self.kind == "mic" else sc.all_speakers()
        if self.dev_id not in (None, "", "default"):
            hit = next((d for d in devs if d.id == self.dev_id), None)
            if hit or not self.fallback:
                self.note = None if hit else "desconectado"
                return hit
            self.note = "el elegido no está conectado: uso el predeterminado"
        else:
            self.note = None
        return sc.default_microphone() if self.kind == "mic" else sc.default_speaker()

    def run(self):
        import numpy as np
        import soundcard as sc
        while not self.stopping:
            try:
                target = self._pick()
                if target is None:  # dispositivo concreto que ya no esta (y sin plan B)
                    self.name, self.error = None, "desconectado"
                    time.sleep(self.CHECK_S)
                    continue
                dev = target if self.kind == "mic" else sc.get_microphone(id=str(target.id), include_loopback=True)
                self.name = target.name
                with dev.recorder(samplerate=SR, channels=1 if self.kind == "mic" else 2, blocksize=480) as r:
                    self.error, checked = None, time.time()
                    while not self.stopping:
                        d = r.record(numframes=None)
                        if time.time() - checked > self.CHECK_S:
                            checked = time.time()
                            now = self._pick()
                            if now is None or now.id != target.id:
                                break  # se han conectado/quitado los cascos: se reabre con el nuevo
                        if not len(d):
                            time.sleep(0.005)
                            continue
                        d = np.asarray(d, np.float32)
                        if d.ndim == 1:
                            d = d[:, None]
                        d = np.repeat(d, 2, axis=1) if d.shape[1] == 1 else d[:, :2]
                        with self.lock:
                            self.buf.append(d)
                            self.frames += len(d)
                            self.peak = max(self.peak, float(np.abs(d).max()))
            except Exception as e:  # dispositivo desconectado a mitad: se reintenta
                self.error = f"{e.__class__.__name__}: {e}"[:200]
                time.sleep(1.0)

    def take(self, n):
        """n muestras; rellena con silencio si no han llegado y recorta si la fuente va adelantada."""
        import numpy as np
        out, got = [], 0
        with self.lock:
            while self.buf and got < n:
                c = self.buf[0]
                k = min(n - got, len(c))
                out.append(c[:k])
                got += k
                if k == len(c):
                    self.buf.popleft()
                else:
                    self.buf[0] = c[k:]
            self.frames -= got
            while self.frames > MAX_BACKLOG * SR and self.buf:
                k = len(self.buf.popleft())
                self.frames -= k
                self.lost += k
        if got < n:
            out.append(np.zeros((n - got, 2), np.float32))
        return np.concatenate(out) if len(out) > 1 else out[0]

    def status(self):
        with self.lock:
            p, self.peak = self.peak, 0.0
        return {"name": self.name, "db": round(20 * math.log10(p), 1) if p > 1e-5 else -100,
                "error": self.error, "note": self.note, "lost": round(self.lost / SR, 2)}


class AllOutputs:
    """Lo que suena en TODAS las salidas a la vez (cascos, altavoces…): capta la reunion salga
    por donde salga, y si se conecta otro dispositivo a mitad de la grabacion se incorpora."""

    SCAN_S = 3.0

    def __init__(self):
        self.sources, self.scanned = {}, 0.0
        self.scan()

    def scan(self):
        import soundcard as sc
        self.scanned = time.time()
        try:
            ids = {s.id for s in sc.all_speakers()}
        except Exception:
            return
        for i in ids - set(self.sources):
            s = Source("sys", i, fallback=False)  # sin plan B: si no, se duplicaria el predeterminado
            s.start()
            self.sources[i] = s
        for i in set(self.sources) - ids:
            self.sources.pop(i).stopping = True

    def take(self, n):
        import numpy as np
        if time.time() - self.scanned > self.SCAN_S:
            self.scan()
        mix = np.zeros((n, 2), np.float32)
        for s in list(self.sources.values()):
            mix += s.take(n)
        return mix

    def status(self):
        sts = [s.status() for s in list(self.sources.values())]
        live = [x for x in sts if x["name"] and not x["error"]]
        loud = max(live, key=lambda x: x["db"], default=None)
        return {"name": " + ".join(x["name"] for x in live) or None,
                "db": loud["db"] if loud else -100,
                "error": None if live else "ninguna salida de audio disponible",
                "note": f"suena por: {loud['name']}" if loud and loud["db"] > -60 else None,
                "lost": round(sum(x.get("lost", 0) for x in sts), 2)}

    @property
    def stopping(self):
        return False

    @stopping.setter
    def stopping(self, v):
        for s in self.sources.values():
            s.stopping = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--monitor", action="store_true")
    ap.add_argument("--mic", default="default")
    ap.add_argument("--sys", default="default")
    a = ap.parse_args()
    out = sys.stdout
    try:
        out.reconfigure(encoding="utf-8")  # nombres de dispositivo con ® y acentos
    except (AttributeError, ValueError):
        pass

    def emit(obj):
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    if a.list:
        emit(list_devices())
        return
    import numpy as np
    import soundcard  # noqa: F401  (al importarse reactiva sus avisos: se vuelven a silenciar)
    warnings.filterwarnings("ignore")

    sources = {}
    if a.mic != "none":
        sources["mic"] = Source("mic", a.mic)
        sources["mic"].start()
    if a.sys == "all":
        sources["sys"] = AllOutputs()
    elif a.sys != "none":
        sources["sys"] = Source("sys", a.sys)
        sources["sys"].start()

    stop = threading.Event()
    muted = {"mic": False}
    levelers = {k: Leveler(*GAIN_RANGE[k]) for k in sources}

    def read_stdin():  # "stop" o que el servidor se cierre (fin de stdin): se para y se cierra bien
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "stop":
                break
            if cmd in ("mute", "unmute"):  # silenciar el micro a mitad de grabacion (se graba silencio)
                muted["mic"] = cmd == "mute"
        stop.set()

    threading.Thread(target=read_stdin, daemon=True).start()

    f = None
    if a.out and not a.monitor:
        f = open(a.out, "wb")
        f.write(wav_header())
        f.flush()
    t0, written, last = time.perf_counter(), 0, 0.0
    try:
        while True:
            now = time.perf_counter()
            final = stop.is_set()
            target = int(max(0.0, now - t0 - (0 if final else LAT)) * SR)
            n = target - written
            if n > 0:
                mix = np.zeros((n, 2), np.float32)
                for k, s in sources.items():
                    part = s.take(n)  # se vacia igual aunque este silenciado, para no acumular retraso
                    if not (k == "mic" and muted["mic"]):
                        mix += levelers[k](part)  # micro y reunion a un volumen parecido
                soft_limit(mix)
                np.clip(mix, -1.0, 1.0, out=mix)
                if f:
                    f.write((mix * 32767).astype("<i2").tobytes())
                    f.flush()
                written += n
            if now - last >= 0.25 or final:
                last = now
                emit({"t": round(written / SR, 2), "mic_muted": muted["mic"],
                      **{k: s.status() for k, s in sources.items()}})
            if final:
                break
            time.sleep(0.05)
    finally:
        for s in sources.values():
            s.stopping = True
        lost = {k: s.status().get("lost", 0) for k, s in sources.items()}
        if any(lost.values()):  # queda en recorder.log
            sys.stderr.write(f"audio perdido por llegar tarde (PC cargado): {lost}\n")
        if f:
            size = f.tell()
            f.seek(4)
            f.write(struct.pack("<I", size - 8))
            f.seek(40)
            f.write(struct.pack("<I", size - 44))
            f.close()
        emit({"done": True, "t": round(written / SR, 2)})


if __name__ == "__main__":
    main()
