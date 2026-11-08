# -*- coding: utf-8 -*-
"""Prueba de integración de «Quién habla» con sherpa-onnx de verdad. Se salta si falta sherpa_onnx, `say` (macOS)
o los modelos: apunta NOTAS_VOCES_DATA a un DATA_DIR con models/voces/ (segmentation.onnx y nemo_en_titanet_small.onnx).

    NOTAS_VOCES_DATA=/ruta/datos python tests/run.py
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import wave

import numpy as np

from support import APP  # noqa: F401  (prepara sys.path)

import voices  # noqa: E402

DATA = os.environ.get("NOTAS_VOCES_DATA", "")
HAVE_SHERPA = voices.available()[0] if DATA else False
HAVE_SAY = bool(shutil.which("say") and shutil.which("afconvert"))

TEXT = {
    "A": ["Buenos días a todos. Empezamos con el repaso del trimestre y la prioridad sigue siendo la versión dos del "
          "panel de clientes, que tiene que estar en producción antes del cierre de noviembre.",
          "Los clientes piloto han pedido tres cosas: filtros por fecha, un buscador más rápido y poder guardar vistas "
          "personalizadas, y me preocupa el soporte si lanzamos sin la documentación actualizada."],
    "B": ["Gracias. Desde finanzas, el presupuesto del trimestre está consumido en un sesenta y cuatro por ciento, en "
          "línea con lo previsto, aunque la partida de licencias de software ha crecido más de lo esperado.",
          "Antes de aprobar la segunda fase necesito ver una estimación de horas por tarea, porque sin eso no puedo "
          "reservar el dinero ni justificarlo ante el comité de la semana que viene."],
}
TEXT2 = {
    "A": ["Última cosa por mi parte: la encuesta de satisfacción sale el lunes y necesito que cada área me pase sus "
          "dos preguntas antes del viernes a mediodía, porque tiene que caber en una pantalla del móvil."],
    "B": ["Una pregunta para todos: la renovación del contrato de alojamiento vence en diciembre y el proveedor ha "
          "subido las tarifas un doce por ciento, así que conviene negociar antes de que llegue la factura."],
}


def say(voice, text, out_wav):
    aiff = out_wav[:-4] + ".aiff"
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True, capture_output=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, out_wav], check=True,
                   capture_output=True)
    with wave.open(out_wav, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def meeting(tmp, voices_, texts):
    """Turnos alternos de dos voces con 0,5 s de silencio: devuelve (int16, verdad [(inicio, fin, voz)])."""
    parts, truth, t = [], [], 0.0
    gap = np.zeros(int(0.5 * voices.SR), np.int16)
    for i in range(max(len(v) for v in texts.values())):
        for name, voice in voices_.items():
            if i >= len(texts[name]):
                continue
            x = say(voice, texts[name][i], os.path.join(tmp, f"{name}{i}.wav"))
            truth.append((t, t + len(x) / voices.SR, name))
            parts += [x, gap]
            t += (len(x) + len(gap)) / voices.SR
    return np.concatenate(parts), truth


@unittest.skipUnless(HAVE_SHERPA and HAVE_SAY and voices.models_ready(DATA),
                     "necesita sherpa-onnx, say y NOTAS_VOCES_DATA")
class VoicesIntegrationTests(unittest.TestCase):
    def test_dos_voces_y_reconocimiento_en_otra_reunion(self):
        tmp = tempfile.mkdtemp(prefix="notas-voces-")
        # Dos voces que el modelo distingue como a dos personas reales. Con «Mónica» y «Paulina» (las dos voces
        # femeninas de Apple), la huella de una se parecia 0,84 a la de la otra: por encima de casi todo lo medido
        # entre personas reales con scripts/eval_voces.py (el par mas alto, 0,78), y desde el 08/11/2026 la app las une.
        vv = {"A": "Mónica", "B": "Eddy (Español (España))"}
        try:
            subprocess.run(["say", "-v", vv["B"], "-o", os.path.join(tmp, "p.aiff"), "x"], check=True,
                           capture_output=True)
        except subprocess.CalledProcessError:
            vv["B"] = "Jorge"
        x1, truth1 = meeting(tmp, vv, TEXT)
        t0 = time.perf_counter()
        r1 = voices.diarize(x1, DATA, threads=2)
        t1 = time.perf_counter() - t0
        self.assertEqual(len(r1["hablantes"]), 2, r1["hablantes"])
        # cada turno de la verdad cae sobre un solo hablante, y los de la misma voz sobre el mismo
        who = voices.assign_srt([{"start": a, "end": b} for a, b, _ in truth1], r1["tramos"])
        by_voice = {}
        for (a, b, v), h in zip(truth1, who):
            by_voice.setdefault(v, set()).add(h)
        self.assertTrue(all(len(s) == 1 for s in by_voice.values()), by_voice)
        self.assertNotEqual(by_voice["A"], by_voice["B"])
        hA, hB = next(iter(by_voice["A"])), next(iter(by_voice["B"]))
        # segunda «reunión» con otros textos: la misma voz casa (>= 0,55) y la distinta no (< 0,40)
        x2, truth2 = meeting(tmp, vv, TEXT2)
        r2 = voices.diarize(x2, DATA, threads=2)
        who2 = voices.assign_srt([{"start": a, "end": b} for a, b, _ in truth2], r2["tramos"])
        h2A = who2[[v for _, _, v in truth2].index("A")]
        h2B = who2[[v for _, _, v in truth2].index("B")]
        personas = [{"id": "A", "huellas": [{"vector": r1["huellas"][hA]}]},
                    {"id": "B", "huellas": [{"vector": r1["huellas"][hB]}]}]
        mA, mB = voices.match(r2["huellas"][h2A], personas), voices.match(r2["huellas"][h2B], personas)
        self.assertEqual((mA["persona"], mA["provisional"]), ("A", False), mA)
        self.assertEqual((mB["persona"], mB["provisional"]), ("B", False), mB)
        self.assertGreaterEqual(min(mA["score"], mB["score"]), voices.UMBRAL)
        self.assertLess(voices.cosine(r2["huellas"][h2A], r1["huellas"][hB]), voices.DUDOSO)
        self.assertLess(voices.cosine(r2["huellas"][h2B], r1["huellas"][hA]), voices.DUDOSO)
        dur = len(x1) / voices.SR
        print(f"\n  integración voces: {dur:.0f} s de audio diarizados en {t1:.1f} s (RTF {t1 / dur:.3f}); "
              f"misma voz {mA['score']:.3f}/{mB['score']:.3f}, distinta "
              f"{voices.cosine(r2['huellas'][h2A], r1['huellas'][hB]):.3f}/"
              f"{voices.cosine(r2['huellas'][h2B], r1['huellas'][hA]):.3f}")


if __name__ == "__main__":
    unittest.main()
