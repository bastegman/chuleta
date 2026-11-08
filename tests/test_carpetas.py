# -*- coding: utf-8 -*-
"""Carpeta principal por proyecto o workstream y una carpeta por reunion (B1b del rediseño; backlog 11a′ y 11b, msgs
1148 y 1158): donde se graba (la regla, rec_folder_origin), la carpeta «AAAA-MM-DD Nombre» que crea rec_start, como la
reconoce la app (meeting_dir_matches, own_meeting_dir, scan), mover y renombrar con su carpeta (o todo o nada) y que lo
grabado antes se queda donde esta. Con dobles: sin grabadora, sin red, sin Windows."""
import json
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from support import TempTree, bare_jobs, server, srt_text  # noqa: F401  (support prepara sys.path)


def settings_store(tmp, **extra):
    st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
    if extra:
        st.update(lambda d: d.update(extra))
    return st


class FakeRec:
    """RecorderProcess de mentira: crea el WAV con su cabecera y «sigue vivo» (dead=True: no arranca)."""
    dead = False

    def __init__(self, path, mic, sys_dev, app_dir, data_dir):
        self.path, self.status = path, {}
        if not self.dead:
            with open(path, "wb") as f:
                f.write(b"\0" * 44)

    def alive(self):
        return not self.dead

    def stop(self, timeout=None):
        return 1.0

    def timeline(self):
        return None


class DeadRec(FakeRec):
    dead = True


def make_app(t, **st):
    """Una App con lo que usan rec_start, move, delete, rename_folder y las carpetas (TempTree `t`)."""
    a = server.App.__new__(server.App)
    a.settings = settings_store(t.tmp, **st)
    a.rec_lock, a.notes_lock, a.rec, a.monitor = threading.Lock(), threading.Lock(), None, None
    a.library = server.Library(server.Durations(os.path.join(t.data_dir, "durations.json")))
    a.jobs = bare_jobs([])
    a.jobs.settings, a.jobs.voice_vectors, a.jobs.started = a.settings, {}, []
    a.jobs.start_live = lambda rel, preset: a.jobs.started.append((rel, preset))
    a.jobs.refresh_prompt = lambda rel: None
    return a


def rec(a, t, **b):
    body = dict({"mic": "default", "sys": "all", "folder": "Alfa", "live": True, "preset": "medium", "name": "Comité",
                 "vocab": ""}, **b)
    a.rec = None  # cada grabacion es nueva (la anterior «termino»)
    with mock.patch.object(server, "RecorderProcess", b.pop("_rec", None) or FakeRec), \
            mock.patch.object(server, "log"):
        return a.rec_start(body)


TODAY = time.strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------- la regla: donde se graba

class CarpetaPrincipalTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("Alfa/Vocabulario/General.txt", "API, SLA\n")
        self.write("Alfa/Vocabulario/Infra.txt", "Kubernetes\n")
        self.write("Beta/Vocabulario/Legal.txt", "Contrato\n")
        self.write("Vocabulario/Varios.txt", "Chuleta\n")  # listas de la raiz: el proyecto es la app
        for d in ("Alfa/Reuniones", "Alfa/Infra", "Otra", "Fija"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        self.alfa = server.find_vocab(None, None, "Alfa/Vocabulario/General.txt")
        self.infra = server.find_vocab(None, None, "Alfa/Vocabulario/Infra.txt")
        self.raiz = server.find_vocab(None, None, "Vocabulario/Varios.txt")

    def test_sin_asignar_es_la_carpeta_del_proyecto_en_el_arbol(self):
        st = {"rec_folder": "Otra"}
        self.assertEqual(server.rec_folder_origin(self.alfa, st), ("Alfa", "carpeta_proyecto"))
        self.assertEqual(server.rec_folder_origin(self.raiz, st), ("", "carpeta_proyecto"))  # la raiz
        self.assertEqual(server.project_home(self.alfa), "Alfa")

    def test_workstream_antes_que_proyecto_y_proyecto_antes_que_perfil(self):
        st = {"rec_folder": "Otra", "perfil": {"folder": "Fija"},
              "project_folders": {"Alfa": "Alfa/Reuniones"},
              "workstream_folders": {"Alfa/Vocabulario/Infra.txt": "Alfa/Infra"}}
        self.assertEqual(server.rec_folder_origin(self.infra, st), ("Alfa/Infra", "workstream"))
        self.assertEqual(server.rec_folder_origin(self.alfa, st), ("Alfa/Reuniones", "proyecto"))
        # con tema, la carpeta del proyecto gana a la fija del perfil (msg 1158); sin tema, la del perfil
        self.assertEqual(server.rec_folder_origin(dict(self.alfa, project="Gamma", id="x"), st)[1], "carpeta_proyecto")
        self.assertEqual(server.rec_folder_origin(None, st), ("Fija", "perfil"))
        self.assertEqual(server.rec_folder_origin(None, {"rec_folder": "Otra"}), ("Otra", "ultimo"))
        self.assertEqual(server.rec_folder_origin(None, {}), ("", "raiz"))
        a = server.App.__new__(server.App)
        self.assertEqual(a.rec_folder_for(self.alfa, st), "Alfa/Reuniones")  # la firma de siempre

    def test_lo_asignado_que_ya_no_existe_cae_al_siguiente(self):
        st = {"project_folders": {"Alfa": "Alfa/Borrada"}, "workstream_folders": {self.infra["id"]: "_app"}}
        self.assertEqual(server.rec_folder_origin(self.infra, st), ("Alfa", "carpeta_proyecto"))

    def test_los_project_folders_de_antes_se_quedan_como_asignacion(self):
        # hasta el 07/11/2026 era «la ultima carpeta elegida para ese proyecto»: sigue mandando
        st = {"project_folders": {"Alfa": "Otra"}}
        self.assertEqual(server.rec_folder_origin(self.alfa, st), ("Otra", "proyecto"))

    def test_lista_fuera_del_arbol_no_tiene_carpeta_propia(self):
        fuera = tempfile.mkdtemp(prefix="vocab-conectada-")
        try:
            v = {"id": ":v1/Varios.txt", "project": "Conectado", "name": "Varios",
                 "_abs": os.path.join(fuera, "Conectado", "Vocabulario", "Varios.txt")}
            self.assertIsNone(server.project_home(v))
            self.assertEqual(server.rec_folder_origin(v, {"rec_folder": "Otra"}), ("Otra", "ultimo"))
            self.assertEqual(server.rec_folder_origin(v, {"perfil": {"folder": "Fija"}}), ("Fija", "perfil"))
        finally:
            shutil.rmtree(fuera, ignore_errors=True)

    def test_asignar_y_quitar_por_la_api(self):
        a = make_app(self)
        with mock.patch.object(server, "log"):
            r = a.carpeta_principal({"project": "Alfa", "folder": "Alfa/Reuniones"})
            self.assertEqual(r["project_folders"]["Alfa"], "Alfa/Reuniones")
            self.assertEqual(r["project_folders"]["Beta"], "Beta")  # sin asignar: la suya
            a.carpeta_principal({"vocab": self.infra["id"], "folder": "Alfa\\Infra\\"})
            st = a.settings.get()
            self.assertEqual(st["workstream_folders"], {self.infra["id"]: "Alfa/Infra"})
            self.assertEqual(server.rec_folder_origin(self.infra, st), ("Alfa/Infra", "workstream"))
            a.carpeta_principal({"project": "Alfa", "folder": None})
            self.assertNotIn("Alfa", a.settings.get()["project_folders"])
            for bad in ({"project": "Alfa", "folder": "NoExiste"}, {"project": "Alfa", "folder": "_app"},
                        {"folder": "Otra"}, {"vocab": "Zeta/Vocabulario/X.txt", "folder": "Otra"}):
                with self.assertRaises(server.ApiError, msg=bad):
                    a.carpeta_principal(bad)

    def test_renombrar_carpetas_y_listas_las_sigue(self):
        a = make_app(self, project_folders={"Alfa": "Alfa/Reuniones"},
                     workstream_folders={"Alfa/Vocabulario/Infra.txt": "Alfa/Infra"})
        with mock.patch.object(server, "log"):
            a.rename_folder({"path": "Alfa", "name": "Alfa 2026"})
        st = a.settings.get()
        self.assertEqual(st["project_folders"], {"Alfa 2026": "Alfa 2026/Reuniones"})
        self.assertEqual(st["workstream_folders"], {"Alfa 2026/Vocabulario/Infra.txt": "Alfa 2026/Infra"})
        d = {"workstream_folders": {"A/Vocabulario/X.txt": "A"}}
        server.settings_moved(d, "A/Vocabulario/X.txt", "A/Vocabulario/Y.txt")  # lista renombrada (vocab_save)
        self.assertEqual(d["workstream_folders"], {"A/Vocabulario/Y.txt": "A"})

    def test_quitar_una_vinculada_y_borrar_una_lista_olvidan_su_carpeta(self):
        ext = os.path.join(self.tmp, "Ext")
        os.makedirs(os.path.join(ext, "Reuniones"))
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "Ext", "path": ext}
        a = make_app(self, workstream_folders={"Alfa/Vocabulario/Infra.txt": f":{lid}/Reuniones",
                                               "Beta/Vocabulario/Legal.txt": "Beta"})
        a._save_links = lambda: None
        with mock.patch.object(server, "log"):
            a.link_remove({"path": ":" + lid})
            self.assertEqual(a.settings.get()["workstream_folders"], {"Beta/Vocabulario/Legal.txt": "Beta"})
            a.vocab_delete({"id": "Beta/Vocabulario/Legal.txt"})
        self.assertEqual(a.settings.get()["workstream_folders"], {})

    def test_config_y_perfil_dicen_la_misma_carpeta(self):
        a = make_app(self, project_folders={"Beta": "Otra"}, rec_folder="Otra")
        res = server.project_folders_resolved(a.settings.get())
        self.assertEqual(res, {"Alfa": "Alfa", "Beta": "Otra", server.APP_NAME: ""})
        r = server.resolve_perfil(a.settings.get(), {"vocab": self.alfa["id"]},
                                  auto_folder=lambda vid: server.rec_folder_origin(
                                      server.find_vocab(None, None, vid) if vid else None, a.settings.get()))
        self.assertEqual((r["folder"], r["origen"]["folder"]), ("Alfa", "carpeta_proyecto"))


class ProyectoDeUnaCarpetaConectadaTests(TempTree):
    """Carpeta por reunion x vocabulario desde una carpeta conectada (revision de la integracion del 08/11/2026): un
    proyecto cuyas listas vienen de una carpeta conectada no tiene carpeta en el arbol (project_home None). Su carpeta
    principal es la que se eligio a mano para el; si no, Ctrl+Alt+R y el aviso de Teams grababan en la ultima usada, que
    puede ser la de otro proyecto."""

    def setUp(self):
        super().setUp()
        self.write("Alfa/Vocabulario/General.txt", "API, SLA\n")
        for d in ("Cliente B", "Cliente B 2"):
            os.makedirs(os.path.join(self.root, d))
        ext = os.path.join(self.tmp, "OneDrive", "Listas")
        os.makedirs(os.path.join(ext, "Cliente B", "Vocabulario"))
        with open(os.path.join(ext, "Cliente B", "Vocabulario", "General.txt"), "w", encoding="utf-8") as f:
            f.write("Contrato, Hito\n")
        server.VOCAB_LINKS[server.link_id(ext)] = {"name": "Listas", "path": ext}
        vs = {v["project"]: v for v in server.list_vocab()}
        self.b, self.alfa = vs["Cliente B"], vs["Alfa"]
        self.assertTrue(self.b["id"].startswith(server.VLINK))
        self.assertIsNone(server.project_home(self.b))
        self.a = make_app(self)

    def test_la_elegida_a_mano_es_su_carpeta_principal(self):
        rec(self.a, self, folder="Cliente B", vocab=self.b["id"], name="Kickoff")
        rec(self.a, self, folder="Alfa", vocab=self.alfa["id"], name="Comité")  # otro proyecto, por la regla
        st = self.a.settings.get()
        self.assertEqual(st["rec_folder"], "Alfa")
        self.assertEqual(st["project_folders"], {"Cliente B": "Cliente B"})  # Alfa tiene la suya: no se apunta
        self.assertEqual(server.rec_folder_origin(self.b, st), ("Cliente B", "proyecto"))
        self.assertEqual(server.rec_folder_origin(self.alfa, st), ("Alfa", "carpeta_proyecto"))
        self.assertEqual(server.project_folders_resolved(st)["Cliente B"], "Cliente B")  # el dialogo dice lo mismo
        rec(self.a, self, folder="Cliente B 2", vocab=self.b["id"], name="Seguimiento")  # otra a mano: la nueva
        self.assertEqual(server.rec_folder_origin(self.b, self.a.settings.get()), ("Cliente B 2", "proyecto"))

    def test_la_que_dio_la_regla_no_se_apunta(self):
        rec(self.a, self, folder="Alfa", vocab=self.alfa["id"], name="Comité")
        carpeta, origen = server.rec_folder_origin(self.b, self.a.settings.get())
        self.assertEqual((carpeta, origen), ("Alfa", "ultimo"))
        rec(self.a, self, folder=carpeta, vocab=self.b["id"], name="Kickoff")  # Ctrl+Alt+R: la de Alfa, sin elegir
        self.assertEqual(self.a.settings.get()["project_folders"], {})  # no se queda la de otro proyecto

    def test_un_proyecto_del_arbol_sigue_sin_apuntarse(self):
        rec(self.a, self, folder="Cliente B", vocab=self.alfa["id"], name="Comité")  # Alfa, a mano en otra carpeta
        st = self.a.settings.get()
        self.assertEqual(st["project_folders"], {})
        self.assertEqual(server.rec_folder_origin(self.alfa, st), ("Alfa", "carpeta_proyecto"))

    def test_config_no_lee_las_carpetas_conectadas(self):
        """/api/config (project_folders_resolved) no recorre las carpetas conectadas: pueden estar en la red."""
        rec(self.a, self, folder="Cliente B", vocab=self.b["id"], name="Kickoff")
        real, vistos, ext = os.scandir, [], os.path.join(self.tmp, "OneDrive")

        def espia(p="."):
            if os.fspath(p).startswith(ext):
                vistos.append(p)
            return real(p)
        with mock.patch.object(server.os, "scandir", espia):
            res = server.project_folders_resolved(self.a.settings.get())
        self.assertEqual(vistos, [])
        self.assertEqual((res["Cliente B"], res["Alfa"]), ("Cliente B", "Alfa"))

    def test_quitar_la_carpeta_de_listas_quita_la_carpeta_de_sus_workstreams(self):
        ws = {self.b["id"]: "Cliente B", self.alfa["id"]: "Alfa"}
        self.a.settings.update(lambda d: d.update(workstream_folders=ws))
        with mock.patch.object(server, "log"), mock.patch.object(self.a, "_save_vocab_links"):
            self.a.vocab_link_remove({"rel": self.b["conectada"]})
        self.assertEqual(self.a.settings.get()["workstream_folders"], {self.alfa["id"]: "Alfa"})


class CarpetaDeListasNuncaEsDestinoTests(TempTree):
    """Una carpeta de listas conectada no es destino de grabacion (VLINK), tampoco si queda dentro de una vinculada; y
    una lista conectada de un proyecto que tambien esta en el arbol graba en la carpeta de ese proyecto (revision de la
    integracion del 08/11/2026: project_home tomaba la carpeta de listas por la del proyecto y las reuniones caian
    dentro, donde luego no se puede volver a conectar)."""

    def setUp(self):
        super().setUp()
        self.a = make_app(self)
        self.cliente = os.path.join(self.tmp, "OneDrive", "Cliente")

    def conectar_y_vincular(self, lista):
        """Conecta Cliente/Acme como carpeta de listas y luego vincula Cliente (la que la contiene) al arbol."""
        acme = os.path.join(self.cliente, "Acme")
        os.makedirs(os.path.dirname(os.path.join(acme, lista)))
        with open(os.path.join(acme, lista), "w", encoding="utf-8") as f:
            f.write("Kubernetes, VPN\n")
        with mock.patch.object(server, "log"), mock.patch.object(self.a, "_save_vocab_links"), \
                mock.patch.object(self.a, "_save_links"):
            self.a.vocab_link_add({"path": acme})
            self.a.link_add({"path": self.cliente})
        v = next(v for v in server.list_vocab() if v["project"] == "Acme")
        return acme, v

    def no_graba_dentro(self, acme, v):
        st = self.a.settings.get()
        self.assertIsNone(server.project_home(v))
        carpeta, origen = server.rec_folder_origin(v, st)
        self.assertNotEqual(origen, "carpeta_proyecto")
        rec(self.a, self, folder=carpeta, vocab=v["id"], name="Acme semanal")  # lo que haria Ctrl+Alt+R
        self.assertFalse(any(e.is_dir() for e in os.scandir(acme) if e.name != "Vocabulario"), os.listdir(acme))

    def test_listas_sueltas(self):
        self.no_graba_dentro(*self.conectar_y_vincular("Infra.txt"))

    def test_listas_en_su_carpeta_vocabulario(self):
        self.no_graba_dentro(*self.conectar_y_vincular(os.path.join("Vocabulario", "Infra.txt")))

    def test_proyecto_tambien_en_el_arbol(self):
        self.write("Acme/Vocabulario/General.txt", "API, SLA\n")
        os.makedirs(os.path.join(self.root, "Otra"))
        ext = os.path.join(self.tmp, "OneDrive", "Listas")
        os.makedirs(os.path.join(ext, "Acme", "Vocabulario"))
        with open(os.path.join(ext, "Acme", "Vocabulario", "Infra.txt"), "w", encoding="utf-8") as f:
            f.write("Kubernetes\n")
        server.VOCAB_LINKS[server.link_id(ext)] = {"name": "Listas", "path": ext}
        vs = [v for v in server.list_vocab() if v["project"] == "Acme"]
        arbol, conectada = (next(v for v in vs if bool(v.get("conectada")) is c) for c in (False, True))
        st = self.a.settings.get()
        self.assertEqual(server.rec_folder_origin(conectada, st), ("Acme", "carpeta_proyecto"))
        rec(self.a, self, folder="Otra", vocab=conectada["id"], name="Una vez en otra")  # a mano, una vez
        st = self.a.settings.get()
        self.assertEqual(st["project_folders"], {})  # no cambia la carpeta del proyecto
        self.assertEqual(server.rec_folder_origin(arbol, st), ("Acme", "carpeta_proyecto"))
        self.assertEqual(server.rec_folder_origin(conectada, st), ("Acme", "carpeta_proyecto"))


# ----------------------------------------------------------------------------- la carpeta de cada reunion

class NombreDeLaCarpetaTests(unittest.TestCase):
    STEM = "2026-11-07 10-05-09 - Comité de seguimiento"

    def test_nombre(self):
        lt = time.strptime("2026-11-07 10-05-09", "%Y-%m-%d %H-%M-%S")
        self.assertEqual(server.meeting_dir_name("Comité de seguimiento", lt), "2026-11-07 Comité de seguimiento")
        self.assertEqual(server.meeting_dir_name("", lt), "2026-11-07 10-05")
        largo = "Comité de dirección del programa de transformación digital y plan estratégico 2027 con todos"
        n = server.meeting_dir_name(largo, lt)
        self.assertLessEqual(len(n), 11 + server.MEETING_DIR_MAX)
        self.assertTrue(largo.startswith(n[11:]) and largo[len(n) - 11] == " ")  # cortado por una palabra


class GrabadoraLentaTests(TempTree):
    """La grabadora tarda en crear el WAV (PC cargado): rec_start la espera hasta REC_ARRANQUE_S y la reunion lleva su
    cabecera. Con 3 s fijos, el E2E de carpetas fallo asi con la maquina cargada (revision de la integracion del
    08/11/2026)."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.root, "Alfa"))
        self.a = make_app(self)

    def grabar(self, retraso, espera):
        hilos = []

        class Lenta(FakeRec):
            def __init__(rec_self, path, *args):
                rec_self.path, rec_self.status = path, {}

                def crear():
                    with open(path, "wb") as f:
                        f.write(b"\0" * 44)
                hilos.append(threading.Timer(retraso, crear))
                hilos[-1].start()
        with mock.patch.object(server, "REC_ARRANQUE_S", espera), mock.patch.object(server, "log_exc"):
            r = rec(self.a, self, _rec=Lenta, name="Lenta", contexto="Seguimiento semanal")
        for t in hilos:  # el WAV llega igual (tarde): antes de mirar y de borrar la carpeta del test
            t.join()
        wav = os.path.join(self.root, *r["path"].split("/"))
        self.assertTrue(os.path.isfile(wav))
        return r, os.path.isfile(wav[:-4] + ".md")

    def test_la_espera(self):
        r, md = self.grabar(0.4, 3.0)
        self.assertEqual((r["aviso"], md), (None, True))

    def test_sin_esperar_lo_bastante_se_queda_sin_cabecera(self):
        r, md = self.grabar(0.4, 0.1)  # control: el test ve el fallo
        self.assertIn("sin cabecera de notas", r["aviso"] or "")
        self.assertFalse(md)

    def test_valor(self):
        self.assertGreaterEqual(server.REC_ARRANQUE_S, 10)


class CarpetaPorReunionTests(TempTree):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.root, "Alfa"))
        self.a = make_app(self)

    def files(self, rel):
        return sorted(os.listdir(os.path.join(self.root, rel)))

    def test_graba_dentro_de_su_carpeta(self):
        r = rec(self.a, self, name="Comité de seguimiento")
        carpeta = f"Alfa/{TODAY} Comité de seguimiento"
        self.assertEqual(r["carpeta"], carpeta)
        self.assertEqual(r["carpeta_principal"], "Alfa")
        self.assertTrue(r["path"].startswith(carpeta + "/" + TODAY + " "))
        self.assertTrue(r["path"].endswith(" - Comité de seguimiento.wav"))
        self.assertEqual(self.a.jobs.started[0][0], r["path"])  # el directo, sobre esa ruta
        st = self.a.settings.get()
        self.assertEqual(st["rec_folder"], "Alfa")  # la principal, no la de la reunion
        self.assertEqual(st["project_folders"], {})  # grabar ya no asigna la carpeta del proyecto
        self.assertEqual(server.own_meeting_dir(server.safe(r["path"])), server.safe(carpeta))

    def test_sin_nombre_fecha_y_hora(self):
        r = rec(self.a, self, name="")
        self.assertRegex(r["carpeta"], r"^Alfa/\d{4}-\d{2}-\d{2} \d{2}-\d{2}$")
        self.assertTrue(server.own_meeting_dir(server.safe(r["path"])))

    def test_dos_el_mismo_dia_con_el_mismo_nombre(self):
        r1 = rec(self.a, self)
        time.sleep(1.05)  # otro segundo: otro nombre de archivo
        r2 = rec(self.a, self)
        self.assertEqual((r1["carpeta"], r2["carpeta"]), (f"Alfa/{TODAY} Comité", f"Alfa/{TODAY} Comité (2)"))
        self.assertTrue(server.own_meeting_dir(server.safe(r2["path"])))

    def test_una_carpeta_vacia_con_ese_nombre_se_usa(self):
        os.makedirs(os.path.join(self.root, "Alfa", f"{TODAY} Comité"))
        self.assertEqual(rec(self.a, self)["carpeta"], f"Alfa/{TODAY} Comité")

    def test_apagada_graba_en_la_principal(self):
        self.a.settings.update(lambda d: d.update(carpeta_por_reunion=False))
        r = rec(self.a, self)
        self.assertEqual((r["carpeta"], os.path.dirname(r["path"])), ("Alfa", "Alfa"))

    def test_si_la_grabadora_no_arranca_no_queda_su_carpeta(self):
        with self.assertRaises(server.ApiError):
            rec(self.a, self, _rec=DeadRec)
        self.assertEqual(self.files("Alfa"), [])

    def test_ruta_larga_de_windows_se_recorta(self):
        nombre = "Comité de dirección del programa de transformación digital y del plan estratégico"
        stem_len = len("2026-11-07 10-00-00 - ") + len(nombre)
        longest = stem_len + len(server.voices.SIDECAR_EXT) + 6
        tope = len(os.path.join(self.root, "Alfa")) + 1 + len(TODAY) + 1 + 24 + longest  # caben 24 del nombre
        with mock.patch.object(server, "WINDOWS", True), mock.patch.object(server, "WIN_PATH_MAX", tope):
            r = rec(self.a, self, name=nombre)
        carpeta = os.path.basename(r["carpeta"])
        self.assertTrue(carpeta.startswith(TODAY + " Comité de"), carpeta)
        self.assertLessEqual(len(carpeta), len(TODAY) + 1 + 24)
        self.assertLessEqual(len(server.safe(r["path"])[:-4] + server.voices.SIDECAR_EXT), tope)
        self.assertTrue(server.own_meeting_dir(server.safe(r["path"])))
        time.sleep(1.05)
        with mock.patch.object(server, "WINDOWS", True), mock.patch.object(server, "WIN_PATH_MAX", 40):
            r = rec(self.a, self, name=nombre)  # ni «AAAA-MM-DD HH-MM» cabe: a la principal, como antes
        self.assertEqual((r["carpeta"], os.path.dirname(r["path"])), ("Alfa", "Alfa"))

    def test_carpeta_principal_de_solo_lectura_se_dice(self):
        with mock.patch.object(server.os, "makedirs", side_effect=PermissionError(13, "denegado")):
            with self.assertRaises(server.ApiError) as cm:
                rec(self.a, self)
        self.assertIn("No se puede crear la carpeta de la reunión", cm.exception.msg)
        self.assertIsNone(self.a.rec)

    def test_vista_previa_de_la_tarjeta_y_lo_que_crea_rec_start(self):
        st = self.a.settings.get()
        d = server.rec_destino(None, dict(st, rec_folder="Alfa"), "Comité")
        self.assertEqual(d, {"carpeta": "Alfa", "origen": "ultimo", "reunion": f"Alfa/{TODAY} Comité",
                             "por_reunion": True})
        rec(self.a, self)
        self.assertEqual(server.rec_destino(None, dict(st, rec_folder="Alfa"), "Comité")["reunion"],
                         f"Alfa/{TODAY} Comité (2)")
        self.assertEqual(server.rec_destino(None, dict(st, carpeta_por_reunion=False, rec_folder="Alfa"),
                                            "Comité")["reunion"], "Alfa")
        self.assertEqual(server.rec_destino(None, dict(st, rec_folder="Alfa"), 'Mal: "nombre"')["reunion"],
                         f"Alfa/{TODAY} Mal nombre")

    def test_lo_grabado_antes_se_queda_donde_esta(self):
        vieja = "Alfa/2026-10-01 10-00-00 - Vieja.wav"
        self.wav(vieja)
        self.write("Alfa/2026-10-01 10-00-00 - Vieja.txt", "hola\n")
        rec(self.a, self)
        self.assertTrue(os.path.isfile(os.path.join(self.root, vieja)))
        self.assertIsNone(server.own_meeting_dir(server.safe(vieja)))
        tree, _ = server.scan()
        alfa = next(f for f in tree["folders"] if f["path"] == "Alfa")
        self.assertEqual([x["name"] for x in alfa["audios"]], [os.path.basename(vieja)])
        self.assertNotIn("por_reunion", alfa)
        self.assertTrue(alfa["folders"][0]["por_reunion"])
        self.assertTrue(server.strip_private(tree)["folders"][0]["folders"][0]["por_reunion"])


class ReconocerSuCarpetaTests(TempTree):
    STEM = "2026-11-07 10-05-09 - Comité"

    def test_solo_las_que_creo_la_app_con_una_grabacion_en_todo_su_arbol(self):
        p = self.wav(f"Alfa/2026-11-07 Comité/{self.STEM}.wav")
        self.assertIsNone(server.own_meeting_dir(p))  # se llama como ella, pero no la creo la app
        server.carpetas_reunion().add("Alfa/2026-11-07 Comité")
        self.assertEqual(server.own_meeting_dir(p), os.path.dirname(p))
        self.wav(f"Alfa/2026-11-07 Comité/Anexos/{self.STEM} (otra).wav")  # otra reunion dentro: ya no es solo suya
        self.assertIsNone(server.own_meeting_dir(p))
        server.carpetas_reunion().add("")
        self.assertIsNone(server.own_meeting_dir(self.wav(f"{self.STEM}.wav")))  # la raiz, nunca
        ext = os.path.join(self.tmp, "Ext")
        os.makedirs(ext)
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "x", "path": ext}
        server.carpetas_reunion().add(":" + lid)
        q = os.path.join(ext, self.STEM + ".wav")
        shutil.copy(p, q)
        self.assertIsNone(server.own_meeting_dir(q))  # ni la raiz de una vinculada

    def test_una_carpeta_tuya_de_antes_que_se_llama_como_la_reunion(self):
        """La de quien ya se hacia una carpeta por reunion a mano (P0-2 del refutador): mover o renombrar la reunion
        mueve sus archivos, como antes; la carpeta, su presentacion y sus subcarpetas se quedan."""
        d = "Clientes/2026-10-01 Comité de seguimiento"
        stem = "2026-10-01 10-00-00 - Comité de seguimiento semanal"
        self.wav(f"{d}/{stem}.wav")
        self.write(f"{d}/{stem}.txt", "x\n")
        for n in ("presentación.pptx", "notas.md", "Material/contrato.pdf"):
            self.write(f"{d}/{n}", "x")
        os.makedirs(os.path.join(self.root, "Beta"))
        a = make_app(self)
        with mock.patch.object(server, "log"):
            new = a.move({"path": f"{d}/{stem}.wav", "folder": "Beta"})["path"]
            self.assertEqual(new, f"Beta/{stem}.wav")
            self.assertEqual(sorted(os.listdir(os.path.join(self.root, d))),
                             ["Material", "notas.md", "presentación.pptx"])
            a.move({"path": new, "folder": "Clientes/2026-10-01 Comité de seguimiento",
                    "name": "2026-10-01 10-00-00 - Kick-off"})
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "Clientes"))), ["2026-10-01 Comité de seguimiento"])
        server.ALIASES.clear()

    def test_scan_la_pliega_solo_si_es_suya_y_no_hay_nada_mas(self):
        for x in "ABCD":
            self.wav(f"{x}/2026-11-07 Comité/{self.STEM}.wav")
            if x != "D":
                server.carpetas_reunion().add(f"{x}/2026-11-07 Comité")
        self.write(f"A/2026-11-07 Comité/{self.STEM}.txt", "hola\n")
        self.write(f"A/2026-11-07 Comité/{self.STEM}.resumen.md", "r\n")
        self.write("B/2026-11-07 Comité/acta.md", "acta\n")  # un documento suyo: se ve la carpeta
        os.makedirs(os.path.join(self.root, "C", "2026-11-07 Comité", "Anexos"))
        tree, _ = server.scan()
        marca = {f["path"]: f["folders"][0].get("por_reunion", False) for f in tree["folders"]}
        self.assertEqual(marca, {"A": True, "B": False, "C": False, "D": False})  # D: no la creo la app

    def test_en_una_vinculada_se_ve_aunque_quede_un_nivel_mas_honda(self):
        ext = os.path.join(self.tmp, "OneDrive")
        hondo = os.path.join(ext, "a", "b", "c", "Principal")  # la principal, en el ultimo nivel que se mira (4)
        os.makedirs(hondo)
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "OneDrive", "path": ext}
        a = make_app(self)
        r = rec(a, self, folder=f":{lid}/a/b/c/Principal")
        _, flat = server.scan()
        self.assertIn(r["path"], [x["path"] for x in flat])
        self.assertTrue(r["path"].startswith(f":{lid}/a/b/c/Principal/{TODAY} Comité/"))


class MigracionTests(TempTree):
    def test_la_ultima_carpeta_de_un_proyecto_que_era_la_de_una_reunion(self):
        self.wav("Alfa/2026-11-03 Comité/2026-11-03 10-00-00 - Comité.wav")  # la carpeta de una reunion, a mano
        self.wav("Gamma/2026-10-01 Kick-off/2026-10-01 09-00-00 - Kick-off.wav")
        os.makedirs(os.path.join(self.root, "Beta", "Reuniones"))
        for n in ("2025-09-01 10-00-00 - A.wav", "2025-10-02 10-00-00 - B.wav"):  # un proyecto que empieza por fecha
            self.wav(f"Clientes/2025-09-01 Proyecto Iberdrola/{n}")
        os.makedirs(os.path.join(self.root, "Clientes", "2026-10-30 Kickoff", "Grabaciones"))  # sin audio dentro
        d = {"project_folders": {"Alfa": "Alfa/2026-11-03 Comité", "Beta": "Beta/Reuniones",
                                 "Gamma": "Gamma/2026-10-01 Kick-off", "Delta": ":ab12cd",
                                 "Iberdrola": "Clientes/2025-09-01 Proyecto Iberdrola",
                                 "Eta": "Clientes/2026-10-30 Kickoff"},
             "rec_folder": "Alfa/2026-11-03 Comité"}
        cambios = server.migrate_main_folders(d)
        self.assertEqual(d["project_folders"], {"Alfa": "Alfa", "Beta": "Beta/Reuniones", "Gamma": "Gamma",
                                                "Delta": ":ab12cd",
                                                "Iberdrola": "Clientes/2025-09-01 Proyecto Iberdrola",
                                                "Eta": "Clientes/2026-10-30 Kickoff"})
        self.assertEqual(d["rec_folder"], "Alfa")
        self.assertEqual(d["carpetas_v"], 1)
        self.assertEqual([c[0] for c in cambios], ["Alfa", "Gamma", "la última"])

    def test_al_arrancar_una_vez(self):
        self.wav("Alfa/2026-11-03 Comité/2026-11-03 10-00-00 - Comité.wav")
        with open(os.path.join(self.data_dir, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"project_folders": {"Alfa": "Alfa/2026-11-03 Comité"}}, f)
        st = server.Store(os.path.join(self.data_dir, "settings.json"), server.DEFAULT_SETTINGS)
        self.assertNotIn("carpetas_v", st.get())
        st.update(server.migrate_main_folders)
        self.assertEqual(server.Store(os.path.join(self.data_dir, "settings.json"), server.DEFAULT_SETTINGS).get()
                         ["project_folders"], {"Alfa": "Alfa"})
        with open(server.__file__, encoding="utf-8") as f:
            src = f.read()
        # en App.__init__, una vez y despues de cargar las vinculadas (sus rutas internas)
        donde = src.index('        self._load_links()\n        if not self.settings.get().get("carpetas_v"):')
        self.assertLess(donde, src.index("    def rec_toggle(self, b):"))


class SettingsRotoTests(unittest.TestCase):
    """De un settings.json roto se rescatan tambien las carpetas principales asignadas, la carpeta por reunion apagada
    y la marca de su migracion: no hay donde volver a ponerlas (revision de la integracion del 08/11/2026)."""

    def store(self, text):
        tmp = tempfile.mkdtemp(prefix="notas-carpetas-roto-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with mock.patch.object(server, "log"):
            return server.Store(path, server.DEFAULT_SETTINGS)

    def test_se_rescatan(self):
        st = self.store('{"project_folders": {"Alfa": "Clientes/Alfa"}, "workstream_folders": {"Alfa/Vocabulario/'
                        'Infra.txt": "Alfa/Infra"}, "carpeta_por_reunion": false, "carpetas_v": 1, "language": "es", '
                        'ROTO')
        d = st.get()
        self.assertEqual(d["project_folders"], {"Alfa": "Clientes/Alfa"})
        self.assertEqual(d["workstream_folders"], {"Alfa/Vocabulario/Infra.txt": "Alfa/Infra"})
        self.assertIs(d["carpeta_por_reunion"], False)
        self.assertEqual(d["carpetas_v"], 1)
        self.assertEqual(d["language"], "")  # lo demas, por defecto
        self.assertEqual(st.broken["recuperado"],
                         ["2 carpetas principales asignadas", "la carpeta por reunión apagada"])

    def test_lo_que_no_vale_no_se_rescata(self):
        st = self.store('{"project_folders": {"Alfa": 3}, "workstream_folders": [], "carpeta_por_reunion": true, '
                        '"carpetas_v": true, ROTO')
        d = st.get()
        self.assertEqual((d["project_folders"], d["workstream_folders"], d["carpeta_por_reunion"]), ({}, {}, True))
        self.assertNotIn("carpetas_v", d)
        self.assertEqual(st.broken["recuperado"], [])


# ----------------------------------------------------------------------------- mover y renombrar con su carpeta

class MoverConSuCarpetaTests(TempTree):
    STEM = "2026-11-07 10-05-09 - Comité"
    DIR = "Alfa/2026-11-07 Comité"

    def setUp(self):
        super().setUp()
        self.wav(f"{self.DIR}/{self.STEM}.wav")
        for e, t in ((".txt", "hola\n"), (".srt", srt_text([(0.0, 1.0, "hola")])), (".md", "---\nestado: x\n---\n"),
                     (".resumen.md", "r\n")):
            self.write(f"{self.DIR}/{self.STEM}{e}", t)
        self.write(f"{self.DIR}/acta.pdf", "pdf")
        os.makedirs(os.path.join(self.root, "Beta"))
        server.carpetas_reunion().add(self.DIR)  # la creo la app (rec_start)
        self.a = make_app(self, favorites=[{"kind": "f", "path": self.DIR}])
        self.rel = f"{self.DIR}/{self.STEM}.wav"
        server.COMPLETE.set(self.rel)
        server.temas().set(self.rel, "Alfa/Vocabulario/General.txt")

    def tearDown(self):
        server.ALIASES.clear()
        super().tearDown()

    def move(self, **b):
        with mock.patch.object(server, "log"):
            return self.a.move(dict({"path": self.rel}, **b))["path"]

    def test_se_mueve_la_carpeta_entera(self):
        new = self.move(folder="Beta")
        self.assertEqual(new, f"Beta/2026-11-07 Comité/{self.STEM}.wav")
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "Beta", "2026-11-07 Comité"))),
                         sorted([self.STEM + e for e in (".wav", ".txt", ".srt", ".md", ".resumen.md")] + ["acta.pdf"]))
        self.assertFalse(os.path.exists(os.path.join(self.root, self.DIR)))  # la vieja no se queda vacia
        self.assertIn(new, server.COMPLETE)
        self.assertIn("Beta/2026-11-07 Comité", server.carpetas_reunion())  # sigue siendo suya
        self.assertNotIn(self.DIR, server.carpetas_reunion())
        self.assertEqual(server.temas().get(new), "Alfa/Vocabulario/General.txt")
        self.assertEqual(self.a.settings.get()["favorites"], [{"kind": "f", "path": "Beta/2026-11-07 Comité"}])
        self.assertEqual(server.alias_of(self.rel), new)  # unas notas que llegan con la ruta vieja van a la nueva

    def test_renombrarla_renombra_su_carpeta(self):
        new = self.move(folder=self.DIR, name="2026-11-07 10-05-09 - Comité de dirección")
        self.assertEqual(new, "Alfa/2026-11-07 Comité de dirección/2026-11-07 10-05-09 - Comité de dirección.wav")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Alfa", "2026-11-07 Comité de dirección", "acta.pdf")))
        self.assertTrue(os.path.isfile(server.safe(new[:-4] + ".resumen.md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, self.DIR)))
        # a la carpeta padre, con otro nombre: igual
        new2 = self.move(path=new, folder="Alfa", name="2026-11-07 10-05-09 - Kick-off")
        self.assertEqual(new2, "Alfa/2026-11-07 Kick-off/2026-11-07 10-05-09 - Kick-off.wav")

    def test_si_ya_hay_otra_asi_en_el_destino(self):
        self.wav(f"Beta/2026-11-07 Comité/{self.STEM}.wav")
        new = self.move(folder="Beta")
        self.assertEqual(new, f"Beta/2026-11-07 Comité (2)/{self.STEM}.wav")

    def test_renombrar_nunca_pisa_un_documento_suyo(self):
        # P0-1 del refutador: un documento de la carpeta que se llama como acabaria uno de la reunion renombrada
        acta = os.path.join(self.root, self.DIR, "2026-11-07 10-05-09 - Acta.md")
        with open(acta, "w", encoding="utf-8") as f:
            f.write("mi acta")
        with self.assertRaises(server.ApiError) as cm:
            self.move(folder=self.DIR, name="2026-11-07 10-05-09 - Acta")
        self.assertIn("Ya existe", cm.exception.msg)
        with open(acta, encoding="utf-8") as f:
            self.assertEqual(f.read(), "mi acta")
        self.assertTrue(os.path.isfile(server.safe(self.rel)))  # nada se ha movido

    def test_renombrar_con_una_fecha_que_no_existe(self):
        new = self.move(folder=self.DIR, name="2026-13-45 10-05-09 - Comité")
        self.assertEqual(new, f"{self.DIR}/2026-13-45 10-05-09 - Comité.wav")  # la carpeta se queda como estaba

    def test_a_la_carpeta_que_contiene_la_suya_la_saca(self):
        new = self.move(folder="Alfa")  # se ve como carpeta (tiene acta.pdf): sacarla de ella
        self.assertEqual(new, f"Alfa/{self.STEM}.wav")
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, self.DIR))), ["acta.pdf"])  # lo demas, alli
        self.assertNotIn(self.DIR, server.carpetas_reunion())

    def test_una_subcarpeta_con_algo_abierto_no_se_copia_a_medias(self):
        # P1-5: en Windows, renombrar una carpeta con un archivo abierto dentro falla; shutil.move la copiaba entera y
        # la borraba a medias. Ahora os.rename: falla sin tocar nada, se reintenta y se deshace lo movido
        self.write(f"{self.DIR}/Anexos/plan.xlsx", "x")
        real = os.rename

        def rename(src, dst):
            if src.endswith("Anexos"):
                raise PermissionError(5, "Acceso denegado")
            return real(src, dst)
        with mock.patch.object(server.os, "rename", rename), mock.patch.object(server.time, "sleep"):
            with self.assertRaises(server.ApiError):
                self.move(folder="Beta")
        self.assertEqual(os.listdir(os.path.join(self.root, "Beta")), [])  # sin carpeta fantasma
        self.assertTrue(os.path.isfile(os.path.join(self.root, self.DIR, "Anexos", "plan.xlsx")))
        self.assertTrue(os.path.isfile(server.safe(self.rel)))

    def test_a_otra_unidad_copia_los_archivos_y_no_las_carpetas(self):
        real = os.rename

        def rename(src, dst):  # como entre C: y D:
            if "Beta" in dst:
                raise OSError(server.errno.EXDEV, "otra unidad")
            return real(src, dst)
        with mock.patch.object(server.os, "rename", rename):
            new = self.move(folder="Beta")
        self.assertTrue(os.path.isfile(server.safe(new)))
        self.assertFalse(os.path.exists(os.path.join(self.root, self.DIR)))
        self.wav(f"Gamma/2026-11-07 Kick-off/{self.STEM}.wav")  # con una subcarpeta, entre unidades no
        self.write("Gamma/2026-11-07 Kick-off/Anexos/a.txt", "x")
        server.carpetas_reunion().add("Gamma/2026-11-07 Kick-off")
        with mock.patch.object(server.os, "rename", rename):
            with self.assertRaises(server.ApiError) as cm:
                self.move(path=f"Gamma/2026-11-07 Kick-off/{self.STEM}.wav", folder="Beta")
        self.assertIn("otra unidad", cm.exception.msg)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Gamma", "2026-11-07 Kick-off", self.STEM + ".wav")))

    def test_dentro_de_su_propia_carpeta_no(self):
        os.makedirs(os.path.join(self.root, self.DIR, "Sub"))
        with self.assertRaises(server.ApiError):
            self.move(folder=self.DIR + "/Sub")

    def test_una_reunion_de_antes_mueve_solo_sus_archivos(self):
        self.wav("Gamma/2026-10-01 10-00-00 - Vieja.wav")
        self.write("Gamma/2026-10-01 10-00-00 - Vieja.txt", "x\n")
        self.write("Gamma/otra.md", "x\n")
        new = self.move(path="Gamma/2026-10-01 10-00-00 - Vieja.wav", folder="Beta")
        self.assertEqual(new, "Beta/2026-10-01 10-00-00 - Vieja.wav")
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "Gamma"))), ["otra.md"])

    def test_si_un_archivo_esta_en_uso_no_se_mueve_nada(self):
        real, n = server.move_one, {"i": 0}

        def flaky(src, dst):
            n["i"] += 1
            if n["i"] >= 3 and src.endswith(".srt"):
                raise PermissionError(32, "en uso")
            return real(src, dst)
        with mock.patch.object(server, "move_one", flaky), mock.patch.object(server.time, "sleep"):
            with self.assertRaises(server.ApiError) as cm:
                self.move(folder="Beta")
        self.assertIn("en uso", cm.exception.msg)
        self.assertEqual(len(os.listdir(os.path.join(self.root, self.DIR))), 6)  # todo de vuelta en su sitio
        self.assertEqual(os.listdir(os.path.join(self.root, "Beta")), [])  # y sin carpeta a medias
        self.assertIn(self.rel, server.COMPLETE)

    def test_borrarla_quita_su_carpeta_si_se_queda_vacia(self):
        os.remove(os.path.join(self.root, self.DIR, "acta.pdf"))
        self.a.jobs.forget_outputs = lambda rel: 0
        with mock.patch.object(server, "log"):
            self.a.delete({"path": self.rel})
        self.assertFalse(os.path.exists(os.path.join(self.root, self.DIR)))

    def test_mientras_se_resume_no_se_mueve(self):
        self.a.jobs.list.append(types.SimpleNamespace(path=self.rel, kind="summary", status="running"))
        with mock.patch.object(server, "log"):
            r = self.a.move({"path": self.rel, "folder": "Beta"})  # se apunta: se mueve al terminar (B1c)
        self.assertEqual((r["path"], r["pendiente"]["folder"]), (self.rel, "Beta"))
        self.assertTrue(os.path.isfile(server.safe(self.rel)))
        with self.assertRaises(server.ApiError) as cm:
            self.move(folder="Beta", name="2026-11-07 10-05-09 - Otro")
        self.assertIn("resumiendo", cm.exception.msg)
        self.assertNotIn("OBS", cm.exception.msg)


# ----------------------------------------------------------------------------- la interfaz: la carpeta se ve como ella

@unittest.skipUnless(shutil.which("node"), "node no instalado")
class ArbolPlegadoTests(unittest.TestCase):
    def test_la_carpeta_de_una_reunion_se_ve_como_la_reunion(self):
        import subprocess
        from test_frontend import js_defs, read
        defs = js_defs(read("app.js"), ["subFolders", "ownAudios"])
        code = defs + """
const reu = {name: '2026-11-07 Comité', por_reunion: true, folders: [],
             audios: [{name: 'b.wav', date: '2026-11-07T10:00:00'}]};
const otra = {name: 'Anexos', folders: [], audios: []};
const f = {folders: [otra, reu], audios: [{name: 'a.wav', date: '2026-10-01T10:00:00'}, {name: 'c.wav', date: null}]};
console.log(JSON.stringify([subFolders(f).map(x => x.name), ownAudios(f).map(a => a.name)]));"""
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [["Anexos"], ["c.wav", "a.wav", "b.wav"]])

    def test_los_destinos_no_ofrecen_la_carpeta_de_una_reunion(self):
        from test_frontend import read
        src = read("app.js")
        self.assertIn("subFolders(n).forEach(f => w(f, d + 1))", src)  # flatFolders: Grabar, Mover…
        self.assertIn("if (S.folders.get(path)?.por_reunion) parts.pop();", src)  # la miga no la repite


if __name__ == "__main__":
    unittest.main()
