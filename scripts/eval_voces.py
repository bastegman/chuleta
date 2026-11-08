# -*- coding: utf-8 -*-
"""¿Cuántas personas ve «Quién habla» y cuánto se equivoca? Mide la diarización de la app contra reuniones reales con
los hablantes anotados (p. ej. el AMI Meeting Corpus, CC BY 4.0: el audio Mix-Headset y su RTTM).

    python scripts/eval_voces.py --data DATA_DIR REUNION.wav REUNION.rttm [más pares…] [--cache DIR] [--json]

DATA_DIR es el de la app (con models/voces/). Hace falta sherpa-onnx. Lo caro (la diarización en bruto de sherpa-onnx)
se guarda en --cache por reunión y umbral de clustering; el posproceso de la app (voices.consolidate: unir, tope por
participantes, absorber breves) se aplica encima con sus parámetros, sin volver a diarizar. Sus decisiones no dependen
del extractor de huellas, así que lo medido es lo que hace la app. --crudo mide sin posproceso (lo de antes del 08/11).

Por reunión:
- `hablantes`: grupos que la barra de voces cuenta (sin los «breves»), frente a `reales`;
- `der`: tasa de error de diarización por tramas de 10 ms, con la mejor correspondencia grupo-persona (sin collar),
  y sus partes (no detectado, falsa alarma, confusión);
- `pureza` (cuánto de cada grupo es de una sola persona) y `cobertura` (cuánto de cada persona cae en su grupo);
- `partes`: en cuántos grupos se parte cada persona (aquellos de los que es la voz mayoritaria, con ≥ 5 s suyos).
No escribe nada fuera de --cache. Los audios no van al repositorio.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_app"))
import voices  # noqa: E402

STEP = 0.01


def read_rttm(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if len(p) >= 8 and p[0] == "SPEAKER":
                a = float(p[3])
                out.append((a, a + float(p[4]), p[7]))
    return out


def masks(spans, n):
    """{etiqueta: bool[n]} por tramas de STEP."""
    out = {}
    for a, b, k in spans:
        m = out.setdefault(k, np.zeros(n, bool))
        m[max(0, int(round(a / STEP))):max(0, int(round(b / STEP)))] = True
    return out


def assign(overlap):
    """Correspondencia que maximiza el solape (Hungarian si hay scipy; si no, voraz)."""
    try:
        from scipy.optimize import linear_sum_assignment
        r, c = linear_sum_assignment(-overlap)
        return list(zip(r.tolist(), c.tolist()))
    except ImportError:
        pairs, used_r, used_c = [], set(), set()
        for idx in np.argsort(-overlap, axis=None):
            i, j = divmod(int(idx), overlap.shape[1])
            if i not in used_r and j not in used_c and overlap[i, j] > 0:
                pairs.append((i, j))
                used_r.add(i)
                used_c.add(j)
        return pairs


def score(ref_spans, tramos, hablantes):
    """Métricas de una diarización (tramos [{inicio, fin, h}]) contra la referencia [(a, b, quien)]."""
    end = max([b for _, b, _ in ref_spans] + [float(t["fin"]) for t in tramos] + [1.0])
    n = int(np.ceil(end / STEP)) + 1
    ref = masks(ref_spans, n)
    hyp = masks([(float(t["inicio"]), float(t["fin"]), t["h"]) for t in tramos], n)
    rk, hk = sorted(ref), sorted(hyp)
    R = np.array([ref[k] for k in rk]) if rk else np.zeros((0, n), bool)
    H = np.array([hyp[k] for k in hk]) if hk else np.zeros((0, n), bool)
    O = (R.astype(np.int32) @ H.T.astype(np.int32)) if len(hk) else np.zeros((len(rk), 0))
    pairs = assign(O.astype(float)) if len(hk) else []
    nr, nh = R.sum(0), H.sum(0)
    nc = np.zeros(n, np.int32)
    for i, j in pairs:
        nc += (R[i] & H[j]).astype(np.int32)
    tot = max(1, int(nr.sum()))
    miss, fa = int(np.maximum(0, nr - nh).sum()), int(np.maximum(0, nh - nr).sum())
    conf = int((np.minimum(nr, nh) - nc).sum())
    # En cuántos grupos queda partida cada persona: los grupos de los que es la voz mayoritaria y en los que habla
    # >= 5 s (con voz solapada, el grupo de otro también lleva algo de la suya: eso no es partirla).
    owner = O.argmax(0) if len(hk) else np.zeros(0, int)
    parts = {k: int(sum(1 for j in range(len(hk)) if owner[j] == i and O[i, j] * STEP >= 5.0))
             for i, k in enumerate(rk)}
    shown = [h for h, info in hablantes.items() if not info.get("breve")]
    hyp_tot = max(1, int(H.sum()))  # tramas de cada grupo (los grupos pueden solaparse)
    return {"reales": len(rk), "hablantes": len(shown), "grupos": len(hk),
            "der": round((miss + fa + conf) / tot, 3), "no_detectado": round(miss / tot, 3),
            "falsa_alarma": round(fa / tot, 3), "confusion": round(conf / tot, 3),
            "pureza": round(float(O.max(0).sum()) / hyp_tot, 3) if len(hk) else 0.0,
            "cobertura": round(float(O.max(1).sum()) / tot, 3) if len(hk) else 0.0,
            "partes_media": round(float(np.mean(list(parts.values()))), 2) if parts else 0.0,
            "partes_max": max(parts.values()) if parts else 0}


def diarize_cached(wav, data, cache, threads, **kw):
    """voices.diarize sobre el WAV (con caché por reunión y argumentos)."""
    key = os.path.splitext(os.path.basename(wav))[0] + "".join(f"_{k}{v}" for k, v in sorted(kw.items()))
    path = os.path.join(cache, key + ".json") if cache else None
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    res = voices.diarize(voices.load_audio_16k(wav), data, threads=threads, consolidar=False, **kw)
    if path:
        os.makedirs(cache, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(res, f)
    return res


def evaluate(pares, data, cache=None, threads=2, umbral_grupos=voices.UMBRAL_GRUPOS, crudo=False, **post):
    """{reunion: métricas} y la media, para una configuración."""
    rows = {}
    for wav, rttm in pares:
        res = diarize_cached(wav, data, cache, threads, umbral_grupos=umbral_grupos)
        if not crudo:
            res = voices.consolidate(res, **post)
        rows[os.path.basename(wav)] = score(read_rttm(rttm), res["tramos"], res["hablantes"])
    tot = {k: round(float(np.mean([r[k] for r in rows.values()])), 3)
           for k in ("der", "confusion", "pureza", "cobertura", "partes_media")}
    tot["hablantes"] = sum(r["hablantes"] for r in rows.values())
    tot["reales"] = sum(r["reales"] for r in rows.values())
    tot["error_n"] = sum(abs(r["hablantes"] - r["reales"]) for r in rows.values())
    return {"reuniones": rows, "media": tot}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diarización de la app contra reuniones con hablantes anotados")
    ap.add_argument("pares", nargs="+", help="REUNION.wav REUNION.rttm …")
    ap.add_argument("--data", required=True, help="DATA_DIR de la app (models/voces)")
    ap.add_argument("--cache", help="carpeta para guardar las diarizaciones en bruto")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--umbral-grupos", type=float, default=voices.UMBRAL_GRUPOS)
    ap.add_argument("--fusion", type=float, default=voices.FUSION)
    ap.add_argument("--absorbe", type=float, default=voices.ABSORBE_MIN)
    ap.add_argument("--tope-min", type=float, default=voices.TOPE_MIN)
    ap.add_argument("--fusion-grandes", type=float, default=voices.FUSION_GRANDES)
    ap.add_argument("--grande-s", type=float, default=voices.GRANDE_S)
    ap.add_argument("--participantes", type=int, help="pista: cuántos participantes tiene cada reunión")
    ap.add_argument("--crudo", action="store_true", help="sin el posproceso de la app")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if len(a.pares) % 2:
        raise SystemExit("Pares WAV RTTM")
    pares = list(zip(a.pares[::2], a.pares[1::2]))
    out = evaluate(pares, a.data, a.cache, a.threads, a.umbral_grupos, a.crudo, fusion=a.fusion,
                   absorbe_min=a.absorbe, tope_min=a.tope_min, participantes=a.participantes,
                   fusion_grandes=a.fusion_grandes, grande_s=a.grande_s)
    out["config"] = {"umbral_grupos": a.umbral_grupos, "crudo": a.crudo, "fusion": a.fusion, "absorbe": a.absorbe,
                     "tope_min": a.tope_min, "participantes": a.participantes, "fusion_grandes": a.fusion_grandes,
                     "grande_s": a.grande_s}
    print(json.dumps(out, ensure_ascii=False, indent=1 if a.json else None))
    return out


if __name__ == "__main__":
    main()
