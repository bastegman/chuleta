# -*- coding: utf-8 -*-
"""Lo que solo se puede probar con varias correcciones de la auditoría final juntas (la rama «correcciones»): cada
rama trae sus tests, pero estos caminos cruzan dos de ellas. Todo con dobles: sin red, sin Windows."""
import queue
import time
import types
import unittest
from unittest import mock

from support import TempTree, bare_jobs, engines, server, srt_text  # noqa: F401  (support prepara sys.path)
from test_perfil import settings_store  # noqa: E402  (ayudante, no tests)

KEY = "gsk_" + "k1" * 12
CLOUD = dict(server.DEFAULT_SETTINGS["cloud"], api_key=KEY)
NOW = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731  (una reunion de ahora: entra en los 3 dias)
TEXTO = [(0.0, 1.0, "hola"), (1.0, 1.9, "adiós")]


def falla(*a, **k):
    raise engines.EngineError("HTTP 500: el servidor no contesta")


def cancelado(*a, **k):
    raise engines.Cancelled()


def termina(*a, **k):
    return list(TEXTO), "es", 2.0


# ----------------------------------------------------------------------------- fix-privacidad + fix-directo

class RepasoRetomadoTests(TempTree):
    """La reunión recuerda si su texto se transcribió entero en la nube (fix-privacidad: CloudText, en DATA_DIR) y el
    repaso final que quedó en cola al apagar se retoma al arrancar (fix-directo: repasos_pendientes.json). El camino
    entero, sin atajos: el directo (con un tramo en local o entero en la nube), su repaso final en cola, Ajustes >
    Apagar, otro arranque (otra lista de trabajos, el mismo DATA_DIR), _resume_final_passes y el repaso retomado
    ejecutado por _run. Si falla, se cancela o ya no se puede hacer, queda el texto del directo: con un tramo en local,
    el resumen automático no lo sube a la nube sin el permiso de Ajustes. Si termina, la grabación entera se ha
    transcrito en la nube y se resume."""

    REL = "Alfa/r.wav"

    def jobs(self, **st):
        j = bare_jobs([])
        j.settings = settings_store(self.tmp, cloud=CLOUD, **st)
        j.queues = {k: queue.Queue() for k in ("local", "cloud", "resumen", "voces")}
        j.library = types.SimpleNamespace(find=lambda rel: {"date": NOW(), "duration": 2.0}, at=1)
        j.local, j.live_worker = None, None
        j.cloud = types.SimpleNamespace(purge_tmp=lambda: None, transcribe=termina)
        j.enviados = []
        j.submit_summary = lambda rel, auto=False: j.enviados.append(rel)
        return j

    def directo_y_apagar(self, *presets):
        """Primer arranque: el directo con esos modelos (el primero al empezar, los demás a mitad de reunión), su texto,
        el repaso final en cola y Ajustes > Apagar con el repaso aún en cola."""
        self.wav(self.REL)
        j = self.jobs()
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "log"):
            j.start_live(self.REL, presets[0])
            job = j.list[-1]
            for p in presets[1:]:
                j.set_live_preset(job.id, p)
        self.write(self.REL[:-4] + ".srt", srt_text(TEXTO))  # lo que escribio el directo
        job.status, job.recording, job.recorded = "done", False, 120.0
        with mock.patch.object(server, "log"):
            self.assertTrue(j._final_pass(job))
            j.shutdown()
        self.assertEqual([x.status for x in j.list if x.final_pass], ["cancelled"])
        return job

    def arrancar(self, **st):
        """Segundo arranque: _resume_final_passes con lo que quedó en DATA_DIR."""
        j = self.jobs(**st)
        with mock.patch.object(server, "log"):
            j._resume_final_passes(delay=0)
        return j

    def repasar(self, motor, **st):
        """Segundo arranque y el repaso retomado, ejecutado por _run con ese motor de la nube."""
        j = self.arrancar(**st)
        repaso = [x for x in j.list if x.final_pass]
        self.assertEqual([(x.path, x.status) for x in repaso], [(self.REL, "queued")])
        j.cloud.transcribe = motor
        with mock.patch.object(server, "log"):
            j._run(repaso[0])
        self.assertEqual(j._pending().data, {})  # ya no queda pendiente para el siguiente arranque
        return j, repaso[0]

    def test_tramo_local_el_repaso_retomado_falla(self):
        self.directo_y_apagar("medium", "nube")
        j, repaso = self.repasar(falla)
        self.assertEqual(repaso.status, "error")
        self.assertFalse(j._tx_in_cloud(self.REL))
        self.assertEqual(j.enviados, [])

    def test_tramo_local_el_repaso_retomado_se_cancela(self):
        self.directo_y_apagar("medium", "nube")
        j, repaso = self.repasar(cancelado)
        self.assertEqual(repaso.status, "cancelled")
        self.assertEqual(j.enviados, [])

    def test_tramo_local_el_repaso_ya_no_se_puede_hacer(self):
        """Al volver a abrir, el repaso final está apagado en Ajustes: no se retoma y se resume (o no) con el texto
        del directo."""
        self.directo_y_apagar("medium", "nube")
        j = self.arrancar(live_final_pass=False)
        self.assertEqual(j.list, [])
        self.assertEqual(j.enviados, [])

    def test_tramo_local_con_el_permiso_de_ajustes(self):
        self.directo_y_apagar("medium", "nube")
        j, _ = self.repasar(falla, summary_auto_local=True)
        self.assertEqual(j.enviados, [self.REL])

    def test_tramo_local_el_repaso_retomado_termina(self):
        """El repaso transcribe la grabación entera en la nube: el texto que queda ya no tiene nada en local."""
        self.directo_y_apagar("medium", "nube")
        j, repaso = self.repasar(termina)
        self.assertEqual(repaso.status, "done")
        self.assertTrue(j._tx_in_cloud(self.REL))
        self.assertEqual(j.enviados, [self.REL])

    def test_entero_en_la_nube_el_repaso_retomado_falla(self):
        """Sin nada en local, el texto del directo que queda ya era de la nube: se resume."""
        self.directo_y_apagar("nube")
        j, _ = self.repasar(falla)
        self.assertEqual(j.enviados, [self.REL])

    def test_entero_en_la_nube_el_repaso_retomado_se_cancela(self):
        self.directo_y_apagar("nube")
        j, _ = self.repasar(cancelado)
        self.assertEqual(j.enviados, [self.REL])

    def test_entero_en_la_nube_el_repaso_ya_no_se_puede_hacer(self):
        self.directo_y_apagar("nube")
        self.assertEqual(self.arrancar(live_final_pass=False).enviados, [self.REL])

    def test_en_la_nube_y_luego_en_local(self):
        """Empieza en la nube y pasa a un modelo local a mitad: también es un tramo en local."""
        self.directo_y_apagar("nube", "rapido", "nube")
        j, _ = self.repasar(falla)
        self.assertEqual(j.enviados, [])


if __name__ == "__main__":
    unittest.main()
