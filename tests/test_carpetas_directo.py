# -*- coding: utf-8 -*-
"""Cambiar la carpeta en pleno directo (B1c del rediseño, msg 1198): la grabacion sigue donde empezo, el destino queda
apuntado (sobrevive a un cierre de la app) y la reunion entera se mueve cuando ya no queda nada en marcha (grabacion,
directo, repaso final, voces y resumen: D4b), con reintentos si un archivo esta en uso, un alias de la ruta vieja para
lo que llegue tarde y «Deshacer» mientras no se ha movido. Con dobles: sin grabadora, sin red, sin Windows."""
import json
import os
import queue
import shutil
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from support import TempTree, bare_jobs, server, srt_text  # noqa: F401  (support prepara sys.path)
from test_carpetas import TODAY, make_app, rec


def quiet():
    """Sin log ni avisos de Windows."""
    return mock.patch.multiple(server, log=mock.DEFAULT, notify=mock.DEFAULT)


class PendienteTests(TempTree):
    def setUp(self):
        super().setUp()
        for d in ("Alfa", "Beta"):
            os.makedirs(os.path.join(self.root, d))
        self.a = make_app(self)
        r = rec(self.a, self, name="Comité")  # grabando (FakeRec «sigue vivo»)
        self.rel, self.dir = r["path"], r["carpeta"]
        self.base = server.safe(self.rel)[:-4]
        for e, t in ((".txt", "hola\n"), (".srt", srt_text([(0.0, 1.0, "hola")])),
                     (".md", "---\nestado: x\n---\nnotas\n")):
            with open(self.base + e, "w", encoding="utf-8") as f:
                f.write(t)
        self.stem = os.path.basename(self.base)
        self.nuevo_dir = f"Beta/{TODAY} Comité"

    def tearDown(self):
        server.ALIASES.clear()
        super().tearDown()

    def destino(self, folder, **kw):
        with quiet():
            return self.a.destino(dict({"path": self.rel, "folder": folder}, **kw))

    def aplicar(self):
        with quiet():
            return self.a._aplicar_movimientos()

    def parar(self):
        self.a.rec = None  # como rec_stop: la grabadora ya no escribe

    def en_beta(self):
        d = os.path.join(self.root, self.nuevo_dir)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_en_pleno_directo_se_apunta_y_no_se_mueve(self):
        r = self.destino("Beta")
        self.assertEqual(r["path"], self.rel)
        self.assertEqual(r["pendiente"]["destino"], self.nuevo_dir)
        self.assertEqual(r["pendiente"]["display"], f"\\Beta\\{TODAY} Comité\\")
        self.assertEqual(self.aplicar(), 0)  # sigue grabando
        self.assertTrue(os.path.isfile(server.safe(self.rel)))
        with open(os.path.join(self.data_dir, "movimientos_pendientes.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)[self.rel]["to"], "Beta")
        self.assertEqual(list(self.a.movimientos_publicos()["pendientes"]), [self.rel])

    def test_al_quedar_libre_se_mueve_la_reunion_entera(self):
        self.destino("Beta")
        self.parar()
        self.assertEqual(self.aplicar(), 1)
        self.assertEqual(self.en_beta(), sorted(self.stem + e for e in (".wav", ".txt", ".srt", ".md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, self.dir)))
        new = f"{self.nuevo_dir}/{self.stem}.wav"
        self.assertEqual(server.alias_of(self.rel), new)
        hechos = self.a.movimientos_publicos()
        self.assertEqual(hechos["pendientes"], {})
        self.assertEqual([(h["de"], h["a"], h["carpeta"], h["error"]) for h in hechos["hechos"]],
                         [(self.rel, new, self.nuevo_dir, None)])
        self.assertEqual(self.aplicar(), 0)  # una vez

    def test_espera_al_ultimo_trabajo_resumen_incluido(self):
        self.destino("Beta")
        self.parar()
        j = self.a.jobs
        for kind in ("transcribe", "voces", "summary"):  # directo/repaso, voces y resumen: en cola o en marcha
            job = types.SimpleNamespace(path=self.rel, kind=kind, status="queued")
            j.list.append(job)
            self.assertEqual(self.aplicar(), 0, kind)
            job.status = "done"
        j._hold(self.rel)  # un trabajo que ya acabo pero aun no ha soltado (encadenando el siguiente)
        self.assertEqual(self.aplicar(), 0)
        j.held.clear()
        j._pending().set(self.rel)  # repaso final pendiente de un cierre: se retoma al arrancar
        self.assertEqual(self.aplicar(), 0)
        j._pending().set(self.rel, False)
        self.assertEqual(self.aplicar(), 1)

    def test_sobrevive_a_un_cierre_de_la_app(self):
        self.destino("Beta")
        otra = make_app(self)  # otra sesion: lee lo apuntado en DATA_DIR
        self.assertEqual(otra.movimientos_publicos()["pendientes"][self.rel]["folder"], "Beta")
        with quiet():
            self.assertEqual(otra._aplicar_movimientos(), 1)
        self.assertIn(self.stem + ".wav", self.en_beta())

    def test_deshacer_y_elegir_la_de_ahora(self):
        self.destino("Beta")
        self.assertEqual(self.destino(None), {"path": self.rel, "pendiente": None})
        self.destino("Beta")
        self.assertIsNone(self.destino("Alfa")["pendiente"])  # la carpeta donde ya esta = deshacer
        self.parar()
        self.assertEqual(self.aplicar(), 0)
        self.assertTrue(os.path.isfile(server.safe(self.rel)))

    def test_si_un_archivo_esta_en_uso_se_reintenta_con_espera(self):
        self.destino("Beta")
        self.parar()
        real = server.move_files
        fallos = [server.ApiError("El archivo está en uso (¿OneDrive…?).")]

        def flaky(moves):
            if fallos:
                raise fallos.pop()
            return real(moves)
        with mock.patch.object(server, "move_files", flaky):
            self.assertEqual(self.aplicar(), 0)
            e = self.a._movs().get(self.rel)
            self.assertEqual((e["intentos"], "target" in e), (1, False))
            self.assertIn("en uso", self.a.movimientos_publicos()["pendientes"][self.rel]["error"])
            self.assertEqual(self.aplicar(), 0)  # antes de la espera, no
            self.a._movs().set(self.rel, dict(e, next=0))
            self.assertEqual(self.aplicar(), 1)
        self.assertIn(self.stem + ".wav", self.en_beta())

    def test_si_en_el_destino_ya_hay_otra_igual(self):
        self.write(f"{self.nuevo_dir}/2026-11-07 09-00-00 - Comité.wav", "otra")
        self.destino("Beta")
        self.parar()
        self.aplicar()
        self.assertTrue(os.path.isfile(os.path.join(self.root, f"{self.nuevo_dir} (2)", self.stem + ".wav")))
        self.assertEqual(self.en_beta(), ["2026-11-07 09-00-00 - Comité.wav"])  # la otra, intacta

    def test_la_carpeta_de_destino_ya_no_existe(self):
        self.destino("Beta")
        shutil.rmtree(os.path.join(self.root, "Beta"))
        self.parar()
        self.assertEqual(self.aplicar(), 0)
        self.assertTrue(os.path.isfile(server.safe(self.rel)))  # se queda donde estaba
        m = self.a.movimientos_publicos()
        self.assertEqual(m["pendientes"], {})
        self.assertIn("ya no existe", m["hechos"][-1]["error"])

    def test_borrarla_olvida_su_movimiento(self):
        self.destino("Beta")
        self.parar()
        self.a.jobs.forget_outputs = lambda rel: 0
        with quiet():
            self.a.delete({"path": self.rel})
        self.assertIsNone(self.a._movs().get(self.rel))

    def test_mover_desde_el_arbol_mientras_se_graba(self):
        with quiet():
            r = self.a.move({"path": self.rel, "folder": "Beta"})
            self.assertEqual((r["path"], r["pendiente"]["folder"]), (self.rel, "Beta"))  # sin el aviso de OBS
            with self.assertRaises(server.ApiError) as cm:
                self.a.move({"path": self.rel, "folder": "Beta", "name": "otro nombre"})
        self.assertIn("podrás renombrarla", cm.exception.msg)
        self.parar()
        self.aplicar()
        self.assertIn(self.stem + ".wav", self.en_beta())

    def test_renombrar_la_carpeta_de_destino_mientras_tanto(self):
        self.destino("Beta")
        with quiet():
            self.a.rename_folder({"path": "Beta", "name": "Beta 2026"})
        self.assertEqual(self.a._movs().get(self.rel)["to"], "Beta 2026")
        self.parar()
        self.aplicar()
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Beta 2026", f"{TODAY} Comité", self.stem + ".wav")))

    def test_unas_notas_que_llegan_justo_despues_van_a_la_nueva(self):
        self.destino("Beta")
        self.parar()
        self.aplicar()
        path = server.alias_of(self.rel)  # lo que hace Handler con la ruta de la peticion
        with quiet():
            self.a.notes_save({"path": path, "body": "notas de después\n"})
        self.assertFalse(os.path.exists(self.base + ".md"))  # no se recrea en la ruta vieja
        with open(os.path.join(self.root, self.nuevo_dir, self.stem + ".md"), encoding="utf-8") as f:
            self.assertIn("notas de después", f.read())

    def test_otra_carpeta_de_fuera_se_vincula(self):
        fuera = os.path.join(self.tmp, "OneDrive", "Proyecto Gamma")
        os.makedirs(fuera)
        self.a._save_links = lambda: None
        r = self.destino(None, folder_abs=fuera)
        lid = server.link_id(fuera)
        self.assertEqual(r["pendiente"]["folder"], ":" + lid)
        self.parar()
        self.aplicar()
        self.assertTrue(os.path.isfile(os.path.join(fuera, f"{TODAY} Comité", self.stem + ".wav")))

    def test_dentro_de_su_propia_carpeta_no(self):
        os.makedirs(os.path.join(self.root, self.dir, "Sub"))
        with self.assertRaises(server.ApiError):
            self.destino(self.dir + "/Sub")

    def test_la_app_se_cerro_a_medio_mover(self):
        # el audio va el ultimo: si ya esta en la carpeta nueva, lo demas tambien; faltan las referencias
        self.destino("Beta")
        self.parar()
        e = self.a._movs().get(self.rel)
        self.a._movs().set(self.rel, dict(e, target=self.nuevo_dir))
        shutil.move(os.path.join(self.root, self.dir), os.path.join(self.root, self.nuevo_dir))
        server.COMPLETE.set(self.rel)
        self.assertEqual(self.aplicar(), 1)
        new = f"{self.nuevo_dir}/{self.stem}.wav"
        self.assertIn(new, server.COMPLETE)
        self.assertEqual(self.a.movimientos_publicos()["hechos"][-1]["a"], new)


class TemaEnDirectoTests(TempTree):
    """Cambiar el proyecto (el tema) en pleno directo hace lo mismo que «Cambiar» la carpeta (mock 01b, nota 3)."""

    def setUp(self):
        super().setUp()
        self.write("Alfa/Vocabulario/General.txt", "API\n")
        self.write("Beta/Vocabulario/Legal.txt", "Contrato\n")
        os.makedirs(os.path.join(self.root, "Otra"))
        self.a = make_app(self)

    def tearDown(self):
        server.ALIASES.clear()
        super().tearDown()

    def tema(self, rel, vid):
        with quiet():
            return self.a.notes_save({"path": rel, "props": {"vocabulario": vid}})

    def test_al_proyecto_nuevo_y_de_vuelta(self):
        r = rec(self.a, self, folder="Alfa", vocab="Alfa/Vocabulario/General.txt")
        self.assertIsNone(self.a._movs().get(r["path"]))  # grabar con su tema no apunta nada
        p = self.tema(r["path"], "Beta/Vocabulario/Legal.txt")["pendiente"]
        self.assertEqual((p["folder"], p["destino"], p["por"]), ("Beta", f"Beta/{TODAY} Comité", "tema"))
        self.assertIsNone(self.tema(r["path"], "Alfa/Vocabulario/General.txt")["pendiente"])  # de vuelta: nada
        self.assertIsNone(self.a._movs().get(r["path"]))
        self.tema(r["path"], "Beta/Vocabulario/Legal.txt")
        self.a.rec = None
        with quiet():
            self.assertEqual(self.a._aplicar_movimientos(), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Beta", f"{TODAY} Comité",
                                                    os.path.basename(r["path"]))))

    def test_en_una_carpeta_elegida_a_mano_el_tema_no_la_mueve(self):
        r = rec(self.a, self, folder="Otra", vocab="Alfa/Vocabulario/General.txt")
        self.assertIsNone(self.tema(r["path"], "Beta/Vocabulario/Legal.txt")["pendiente"])
        self.assertIsNone(self.a._movs().get(r["path"]))

    def test_una_carpeta_elegida_con_cambiar_manda(self):
        r = rec(self.a, self, folder="Alfa", vocab="Alfa/Vocabulario/General.txt")
        with quiet():
            self.a.destino({"path": r["path"], "folder": "Otra"})
        self.tema(r["path"], "Beta/Vocabulario/Legal.txt")
        self.assertEqual(self.a._movs().get(r["path"])["to"], "Otra")

    def test_fuera_del_directo_el_tema_no_mueve(self):
        r = rec(self.a, self, folder="Alfa", vocab="Alfa/Vocabulario/General.txt")
        self.a.rec = None
        self.assertIsNone(self.tema(r["path"], "Beta/Vocabulario/Legal.txt")["pendiente"])
        self.assertIsNone(self.a._movs().get(r["path"]))


class SoloGrabarTests(TempTree):
    def test_al_parar_se_mueve_ya(self):
        for d in ("Alfa", "Beta"):
            os.makedirs(os.path.join(self.root, d))
        a = make_app(self)
        r = rec(a, self, name="Comité", live=False)
        with quiet():
            a.destino({"path": r["path"], "folder": "Beta"})
            a.rec_stop({})
            for t in [t for t in threading.enumerate() if t.name == "mover"]:
                t.join(10)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Beta", f"{TODAY} Comité",
                                                    os.path.basename(r["path"]))))
        server.ALIASES.clear()


class ReunionDeAntesTests(TempTree):
    def test_sin_carpeta_propia_se_mueven_sus_archivos(self):
        rel = "Alfa/2026-10-01 10-00-00 - Vieja.wav"
        self.wav(rel)
        self.write("Alfa/2026-10-01 10-00-00 - Vieja.txt", "x\n")
        os.makedirs(os.path.join(self.root, "Beta"))
        a = make_app(self)
        a.jobs.list.append(types.SimpleNamespace(path=rel, kind="summary", status="running"))
        with quiet():
            r = a.destino({"path": rel, "folder": "Beta"})
            self.assertEqual(r["pendiente"]["destino"], "Beta")
            a.jobs.list[0].status = "done"
            self.assertEqual(a._aplicar_movimientos(), 1)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "Beta"))),
                         ["2026-10-01 10-00-00 - Vieja.txt", "2026-10-01 10-00-00 - Vieja.wav"])
        server.ALIASES.clear()

    def test_libre_se_mueve_ya(self):
        rel = "Alfa/2026-10-01 10-00-00 - Vieja.wav"
        self.wav(rel)
        os.makedirs(os.path.join(self.root, "Beta"))
        a = make_app(self)
        with quiet():
            r = a.destino({"path": rel, "folder": "Beta"})
        self.assertEqual((r["path"], r["movida"]), ("Beta/2026-10-01 10-00-00 - Vieja.wav", True))
        server.ALIASES.clear()


class ColaTests(unittest.TestCase):
    """Jobs: lo que encadena un trabajo al acabar ya esta en cola cuando lo suelta, y on_idle llega una vez."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-cola-")
        self.ctx = mock.patch.object(server, "DATA_DIR", self.tmp)
        self.ctx.start()

    def tearDown(self):
        self.ctx.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hold_cubre_el_hueco_entre_voces_y_resumen(self):
        j = bare_jobs([])
        j.queues = {"voces": queue.Queue(), "resumen": queue.Queue()}
        idle, busy_seen = [], []
        j.on_idle = idle.append
        voces = server.Job("a.wav", "a.wav", "voces", None)
        resumen = server.Job("a.wav", "a.wav", "resumen", None)

        def run_voices(job):  # como _run_voices: «done» antes de encadenar el resumen (en su finally)
            job.status = "done"
            busy_seen.append(j.busy_for("a.wav"))
            with j.lock:
                j.list.append(resumen)
            j.queues["resumen"].put(resumen)

        def run_summary(job):
            job.status = "done"
            busy_seen.append(j.busy_for("a.wav"))
        j._run_voices, j._run_summary = run_voices, run_summary
        j.list.append(voces)
        j.queues["voces"].put(voces)
        for kind in ("voces", "resumen"):
            threading.Thread(target=j._worker, args=(kind,), daemon=True).start()
        fin = time.time() + 5
        while not idle and time.time() < fin:
            time.sleep(0.02)
        self.assertEqual(idle, ["a.wav"])  # una vez, tras el resumen
        self.assertTrue(all(busy_seen), busy_seen)  # en el hueco seguia ocupada
        self.assertIsNone(j.busy_for("a.wav"))

    def test_el_directo_tambien(self):
        j = bare_jobs([])
        idle = []
        j.on_idle = idle.append
        j._hold("b.wav")
        self.assertEqual(j.busy_for("b.wav"), "Se está procesando esta reunión")
        job = types.SimpleNamespace(path="b.wav")
        j._run_live = lambda job, abs_path, st: None
        j._run_live_held(job, "x", {})
        self.assertEqual(idle, ["b.wav"])

    def test_la_vuelta_de_3_s_llama_a_on_tick(self):
        j = bare_jobs([])
        j.settings = types.SimpleNamespace(get=lambda: {"live_auto": False})
        ticks = []
        j.on_tick = lambda: ticks.append(1)

        class Stop(BaseException):
            pass
        calls = []

        def fake_sleep(s):
            calls.append(s)
            if len(calls) > 2:
                raise Stop()
        with mock.patch.object(server.time, "sleep", fake_sleep):
            with self.assertRaises(Stop):
                j._watch_recordings()
        self.assertEqual(ticks, [1, 1])  # aunque el directo automatico este apagado


if __name__ == "__main__":
    unittest.main()
