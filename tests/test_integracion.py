# -*- coding: utf-8 -*-
"""Lo que solo se puede probar con varias correcciones de la auditoría final juntas (la rama «correcciones»): cada
rama trae sus tests, pero estos caminos cruzan dos de ellas. Todo con dobles: sin red, sin Windows."""
import os
import queue
import threading
import time
import types
import unittest
from unittest import mock

from support import TempTree, bare_jobs, engines, server, srt_text  # noqa: F401  (support prepara sys.path)
from test_fix_perfil import NOTE_KEYS_PUBLICADAS, parse_publicado  # noqa: E402  (ayudantes, no tests)
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


# ----------------------------------------------------------------------------- fix-privacidad + fix-perfil

class ParticipantesSugeridosCompatTests(TempTree):
    """fix-perfil garantiza que las versiones publicadas (012bb24 y 5c55e54) leen la cabecera que escribe esta: solo
    aceptan una cabecera con las claves que conocen y, con una nueva, la toman entera por notas (y al guardar la
    entierran en ellas). fix-privacidad guarda el origen de los participantes que sugieren Outlook o Teams al grabar,
    y con una clave propia (participantes_origen) esas versiones ya no leían la cabecera de una reunión grabada con
    sugerencias. El origen va detrás de los nombres, «Ana, Luis (sugeridos por Outlook, sin confirmar)»: las publicadas
    ven los nombres con la nota, y esta, los nombres y su origen."""

    REL = "Alfa/r.wav"

    def app(self):
        a = server.App.__new__(server.App)
        a.jobs = bare_jobs([])
        a.jobs.voice_vectors = {}
        a.jobs.refresh_prompt = a.jobs.schedule_outputs = lambda rel, delay=4.0: None
        a.library = server.Library(server.Durations(os.path.join(self.data_dir, "d.json")))
        a.settings, a.notes_lock, a.rec = settings_store(self.tmp), threading.Lock(), None
        self.wav(self.REL)
        return a

    def md(self):
        return server.read_text(os.path.join(self.root, self.REL[:-4] + ".md"))

    def publicadas(self):
        return {rev: parse_publicado(rev)(self.md()) for rev in NOTE_KEYS_PUBLICADAS}

    def test_las_publicadas_leen_una_reunion_con_participantes_sugeridos(self):
        a = self.app()
        sugeridos = {"participantes": "Ana Pérez,  Luis Gómez", "participantes_origen": "outlook"}
        a.notes_save({"path": self.REL, "props": dict(sugeridos, estado="Pendiente")})  # como rec_start
        a.notes_save({"path": self.REL, "body": "notas\n"})
        for rev, (props, body) in self.publicadas().items():
            self.assertEqual((props, body), ({"estado": "Pendiente", "participantes": "Ana Pérez, Luis Gómez "
                                              "(sugeridos por Outlook, sin confirmar)"}, "notas\n"), rev)
        props = server.parse_notes(self.md())[0]
        self.assertEqual(props, {"estado": "Pendiente", "participantes": "Ana Pérez, Luis Gómez",
                                 "participantes_origen": "outlook"})
        self.assertEqual(server.participantes_confirmados(props), "")  # siguen sin salir del PC
        a.notes_save({"path": self.REL, "props": {"participantes": "Ana Pérez, Luis Gómez"}})  # «Confirmar»
        for rev, (props, _) in self.publicadas().items():
            self.assertEqual(props["participantes"], "Ana Pérez, Luis Gómez", rev)
        self.assertEqual(server.participantes_confirmados(server.parse_notes(self.md())[0]), "Ana Pérez, Luis Gómez")

    def test_la_clave_propia_se_sigue_leyendo_y_al_guardar_pasa_a_la_nota(self):
        a = self.app()
        self.write(self.REL[:-4] + ".md", "---\nparticipantes: Ana\nparticipantes_origen: teams\n---\n\nx\n")
        self.assertEqual(server.parse_notes(self.md())[0], {"participantes": "Ana", "participantes_origen": "teams"})
        a.notes_save({"path": self.REL, "props": {"estado": "Revisada"}})
        self.assertEqual(self.md(), "---\nestado: Revisada\nparticipantes: Ana (sugeridos por Teams, sin confirmar)\n"
                                    "---\n\nx\n")
        for rev, (props, body) in self.publicadas().items():
            self.assertEqual(body, "x\n", rev)

    def test_lo_que_guarde_una_publicada(self):
        """Una publicada reescribe la cabecera con la nota tal cual (siguen sugeridos) o, si se le quita, con los
        nombres solos: editados a mano, ya son del usuario."""
        self.app()
        self.write(self.REL[:-4] + ".md", "---\nestado: Enviada\nparticipantes: Ana, Luis (sugeridos por Outlook, sin "
                                          "confirmar)\n---\n\nnotas\n")
        props = server.parse_notes(self.md())[0]
        self.assertEqual((props["participantes"], props["participantes_origen"]), ("Ana, Luis", "outlook"))
        self.write(self.REL[:-4] + ".md", "---\nestado: Enviada\nparticipantes: Ana, Luis\n---\n\nnotas\n")
        self.assertNotIn("participantes_origen", server.parse_notes(self.md())[0])

    def test_sin_nombres_no_hay_origen(self):
        props, _ = server.parse_notes("---\nparticipantes: (sugeridos por Teams, sin confirmar)\n---\n\nx\n")
        self.assertNotIn("participantes_origen", props)
        props, _ = server.parse_notes("---\nparticipantes: Ana\nparticipantes_origen: cualquiera\n---\n\nx\n")
        self.assertEqual(props, {"participantes": "Ana"})  # como notes_save: solo outlook o teams


if __name__ == "__main__":
    unittest.main()
