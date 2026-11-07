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

    def test_se_reconoce(self):
        f = server.meeting_dir_matches
        self.assertTrue(f("2026-11-07 Comité de seguimiento", self.STEM))
        self.assertTrue(f("2026-11-07 Comité de seguimiento (2)", self.STEM))
        self.assertTrue(f("2026-11-07 10-05", self.STEM))  # sin sitio para el nombre: la hora
        self.assertTrue(f("2026-11-07 10-05", "2026-11-07 10-05-09"))
        self.assertTrue(f("2026-11-07 comite de  seguimiento", self.STEM))  # acentos, mayusculas y espacios
        largo = "2026-11-07 10-05-09 - Comité de dirección del programa de transformación digital y plan 2027"
        self.assertTrue(f(server.meeting_dir_name(largo[22:], time.localtime()).replace(
            time.strftime("%Y-%m-%d"), "2026-11-07"), largo))
        for d, s in (("2026-11-08 Comité de seguimiento", self.STEM),  # otro dia
                     ("2026-11-07 Comité", self.STEM),               # un trozo corto: puede ser una carpeta tuya
                     ("2026-11-07 Comité de seguimiento semanal", self.STEM),
                     ("Comité de seguimiento", self.STEM),
                     ("2026-11-07 Comité de seguimiento", "Comité de seguimiento"),  # audio sin fecha
                     ("2026-11-07 10-06", self.STEM)):
            self.assertFalse(f(d, s), (d, s))


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
        hondo = "Alfa/" + "/".join(["Carpeta con un nombre bastante largo"] * 4)
        os.makedirs(os.path.join(self.root, hondo))
        nombre = "Comité de dirección del programa de transformación digital y del plan estratégico"
        with mock.patch.object(server, "WINDOWS", True), mock.patch.object(server, "WIN_PATH_MAX", 260):
            r = rec(self.a, self, folder=hondo, name=nombre)
        carpeta = os.path.basename(r["carpeta"])
        self.assertLess(len(carpeta), len(TODAY) + 1 + len(nombre))
        self.assertTrue(server.meeting_dir_matches(carpeta, os.path.splitext(os.path.basename(r["path"]))[0]))
        self.assertTrue(server.own_meeting_dir(server.safe(r["path"])))

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

    def test_solo_con_una_grabacion_dentro_y_fuera_de_las_raices(self):
        p = self.wav(f"Alfa/2026-11-07 Comité/{self.STEM}.wav")
        self.assertEqual(server.own_meeting_dir(p), os.path.dirname(p))
        self.wav(f"Alfa/2026-11-07 Comité/{self.STEM} (otra).wav")
        self.assertIsNone(server.own_meeting_dir(p))
        self.assertIsNone(server.own_meeting_dir(self.wav(f"{self.STEM}.wav")))  # en la raiz
        ext = os.path.join(self.tmp, "2026-11-07 Comité")  # una vinculada que se llama asi
        os.makedirs(ext)
        server.LINKS[server.link_id(ext)] = {"name": "x", "path": ext}
        q = os.path.join(ext, self.STEM + ".wav")
        shutil.copy(p, q)
        self.assertIsNone(server.own_meeting_dir(q))

    def test_scan_la_marca_solo_si_no_hay_nada_mas(self):
        self.wav(f"A/2026-11-07 Comité/{self.STEM}.wav")
        self.write(f"A/2026-11-07 Comité/{self.STEM}.txt", "hola\n")
        self.write(f"A/2026-11-07 Comité/{self.STEM}.resumen.md", "r\n")
        self.wav(f"B/2026-11-07 Comité/{self.STEM}.wav")
        self.write("B/2026-11-07 Comité/acta.md", "acta\n")  # un documento suyo: se ve la carpeta
        self.wav(f"C/2026-11-07 Comité/{self.STEM}.wav")
        os.makedirs(os.path.join(self.root, "C", "2026-11-07 Comité", "Anexos"))
        tree, _ = server.scan()
        marca = {f["path"]: f["folders"][0].get("por_reunion", False) for f in tree["folders"]}
        self.assertEqual(marca, {"A": True, "B": False, "C": False})


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
        real, n = shutil.move, {"i": 0}

        def flaky(src, dst):
            n["i"] += 1
            if n["i"] >= 3 and src.endswith(".srt"):
                raise PermissionError(32, "en uso")
            return real(src, dst)
        with mock.patch.object(server.shutil, "move", flaky), mock.patch.object(server.time, "sleep"):
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
        with self.assertRaises(server.ApiError) as cm:
            self.move(folder="Beta")
        self.assertIn("resumiendo", cm.exception.msg)
        self.assertNotIn("OBS", cm.exception.msg)


if __name__ == "__main__":
    unittest.main()


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
