# -*- coding: utf-8 -*-
"""¿Se oyen las voces dos veces en una grabacion? Busca la misma señal repetida dentro del propio fichero.

    python3 scripts/medir_eco.py GRABACION.wav [--ventana 5] [--json]

Lee un WAV PCM de 16 bits (el de Chuleta: 48 kHz estereo), lo pasa a mono a 8 kHz y, por ventanas de 5 s solapadas a
la mitad, calcula la autocorrelacion normalizada (eco.xcorr, la misma que usa la grabadora) entre 50 ms y 1,5 s. Por
debajo de 50 ms no se mira: el oido funde dos copias tan cercanas en una (no se oye «doble») y la voz, casi periodica
(el tono, 80-400 Hz), da ahi picos a varios periodos. Hace falta la copia en 2 ventanas y en un 25 % de las que
tienen sonido.

Por ventana: nivel y el pico mas alto (retardo y parecido 0-1; con copia y original iguales, ~0,5). Al final:
  - ningun pico >= UMBRAL en las ventanas con sonido: no hay voces dobles;
  - el mismo retardo (±2 ms) en la mayoria de las ventanas con copia: copia fija. Con parecido >= 0,35 es casi
    identica (digital: dos salidas sumadas, o una salida que lleva el micro; hipotesis A del backlog §14); mas baja,
    eco filtrado por altavoces y sala (B) o una copia digital mas floja;
  - retardos que cambian: eco que se mueve (B) o fuentes que se desalineaban tras cada corte (C, arreglado en la rama
    fix-eco: la linea de tiempo comun de recorder.Source).
El nivel de la copia sale de la altura del pico: x = s + a·s(t - τ) da r(τ) ≈ a / (1 + a²).
Sin dependencias fuera de numpy. No escribe nada: solo lee el WAV.
"""
import argparse
import json
import math
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_app"))
import eco  # noqa: E402

UMBRAL = 0.15      # pico que se considera copia
MIN_MS, MAX_MS = 50, 1500  # por debajo de ~50 ms el oido funde las dos copias (precedencia) y la voz da picos


def read_wav(path):
    """(mono float32 a 8 kHz, duracion en s). Solo PCM de 16 bits."""
    with wave.open(path, "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit("Solo WAV PCM de 16 bits (el que graba Chuleta)")
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    if ch > 1:
        x = x[:len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if sr % eco.SR_C == 0:
        k = sr // eco.SR_C
        x = x[:len(x) // k * k].reshape(-1, k).mean(axis=1)
    else:
        x = np.interp(np.arange(0, len(x), sr / eco.SR_C), np.arange(len(x)), x).astype(np.float32)
    return x, n / sr


def copy_level_db(r):
    """Nivel de la copia respecto al original a partir del pico r = a / (1 + a²) (r <= 0,5)."""
    r = min(abs(r), 0.4999)
    if r <= 1e-6:
        return None
    a = (1 - math.sqrt(1 - 4 * r * r)) / (2 * r)
    return round(20 * math.log10(a), 1)


def windows(x, seconds=5.0):
    """[{inicio_s, nivel_db, retardo_ms, parecido, copia_db}] por ventana (retardo None si no hay sonido)."""
    w, hop = int(seconds * eco.SR_C), int(seconds * eco.SR_C / 2)
    lo, hi = int(MIN_MS * eco.SR_C / 1000), int(MAX_MS * eco.SR_C / 1000)
    out = []
    for a in range(0, max(1, len(x) - w + 1), hop):
        seg = x[a:a + w]
        lvl = eco.db(seg)
        row = {"inicio_s": round(a / eco.SR_C, 1), "nivel_db": round(lvl, 1), "retardo_ms": None, "parecido": None,
               "copia_db": None}
        if lvl > eco.SILENT_DB and len(seg) > 2 * hi:
            lags, r = eco.xcorr(seg, seg, hi)
            lag, c = eco.peak(lags, r, lo, hi)
            if lag is not None:
                row.update(retardo_ms=round(lag * 1000 / eco.SR_C, 1), parecido=round(c, 3),
                           copia_db=copy_level_db(c) if c > 0 else None)
        out.append(row)
    return out


def verdict(rows):
    sound = [r for r in rows if r["retardo_ms"] is not None]
    hits = [r for r in sound if (r["parecido"] or 0) >= UMBRAL]
    if not sound:
        return {"veredicto": "sin sonido suficiente para medir", "ventanas_con_sonido": 0}
    if len(hits) < max(2, math.ceil(0.25 * len(sound))):  # un pico suelto: la voz, no una copia
        return {"veredicto": "no hay voces dobles (ningun pico >= %.2f en varias ventanas)" % UMBRAL,
                "ventanas_con_sonido": len(sound), "ventanas_con_copia": len(hits)}
    lags = sorted(r["retardo_ms"] for r in hits)
    med = lags[len(lags) // 2]
    fixed = [r for r in hits if abs(r["retardo_ms"] - med) <= 2.0]
    best = max(hits, key=lambda r: r["parecido"])
    out = {"ventanas_con_sonido": len(sound), "ventanas_con_copia": len(hits), "retardo_mediano_ms": med,
           "parecido_max": best["parecido"], "copia_db_max": best["copia_db"]}
    if len(fixed) >= max(2, math.ceil(0.6 * len(hits))):
        kind = ("copia casi identica (digital: dos salidas sumadas o una salida que lleva el micro; A)"
                if best["parecido"] >= 0.35 else "copia fija mas floja: eco de altavoces (B) o copia digital debil")
        out["veredicto"] = f"voces dobles con retardo FIJO de {med:.0f} ms: {kind}"
    else:
        out["veredicto"] = ("voces dobles con retardo que CAMBIA (%s ms): eco que se mueve (B) o fuentes que se "
                            "desalineaban tras cada corte (C)" % ", ".join(f"{v:.0f}" for v in lags))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Voces dobles en una grabacion (autocorrelacion por ventanas)")
    ap.add_argument("wav")
    ap.add_argument("--ventana", type=float, default=5.0, help="segundos por ventana (5)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    x, dur = read_wav(a.wav)
    rows = windows(x, a.ventana)
    res = dict(verdict(rows), duracion_s=round(dur, 1), ventanas=rows)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return res
    print(f"{os.path.basename(a.wav)}: {res['duracion_s']} s")
    for r in rows:
        extra = "" if r["retardo_ms"] is None else (f"  pico {r['parecido']:.2f} a {r['retardo_ms']:.0f} ms"
                                                    + (f" (copia {r['copia_db']} dB)" if r["copia_db"] else ""))
        print(f"  {r['inicio_s']:6.1f} s  {r['nivel_db']:6.1f} dB{extra}")
    print(res["veredicto"])
    return res


if __name__ == "__main__":
    main()
