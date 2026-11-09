# -*- coding: utf-8 -*-
"""P2 del renombrado (09/11/2026): Tenca instalada encima de Chuleta conserva la carpeta «Chuleta» (decisión 1 del
renombrado). La app enseña las rutas con ese nombre, el de verdad, el que se ve en el Explorador: en la tarjeta de
grabar, en el aviso de Ctrl+Alt+R, en el directo, en Ajustes › Proyectos y en los avisos de mover. Y el proyecto de las
listas de la raíz se llama como su carpeta, igual que cualquier otro proyecto: sus documentos «por proyecto» siguen en
la subcarpeta «Chuleta» de siempre, y los ajustes que 4553b9c pasó a «Tenca» vuelven a «Chuleta»."""
import os
import threading
import types
import unittest
from unittest import mock

from support import TempTree, bare_jobs, patched_paths, server  # noqa: F401  (support prepara sys.path)
import salidas  # noqa: E402
from test_perfil import settings_store  # noqa: E402


class RaizConOtroNombreTests(TempTree):
    ROOT_NAME = "Chuleta"

    def setUp(self):
        super().setUp()
        self.write("Vocabulario/General.txt", "SLA\n")
        self.write("Alfa/Vocabulario/General.txt", "API, Kubernetes\n")
        os.makedirs(os.path.join(self.root, "Alfa", "Reuniones"))
        self.sug = {"name": "Comité", "vocab": "", "source": "teams", "participants": []}

    def app(self, **st):
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, **st)
        a.rec_lock, a.notes_lock, a.toggle_lock, a.rec, a.monitor = (threading.Lock(), threading.Lock(),
                                                                     threading.Lock(), None, None)
        a.library = types.SimpleNamespace(at=1, flat=[])
        a.jobs = bare_jobs([])
        a.jobs.settings, a.jobs.started = a.settings, []
        a.jobs.start_live = lambda rel, preset: a.jobs.started.append((rel, preset))
        a.jobs.refresh_prompt = lambda rel: None
        a.jobs.schedule_outputs = lambda rel: None
        a._vocab_terms = lambda: []
        sug = self.sug
        a.vigia = types.SimpleNamespace(sugerencia=lambda leer: dict(sug))
        return a

    @staticmethod
    def carpeta(p):
        return next(x for x in p["lineas"] if x["clave"] == "carpeta")

    def test_el_nombre_de_la_carpeta_y_el_de_la_app(self):
        self.assertEqual((server.root_name(), server.APP_NAME), ("Chuleta", "Tenca"))
        with patched_paths(ROOT=os.sep):  # una raíz sin nombre (la de una unidad): el de la app
            self.assertEqual(server.root_name(), "Tenca")
        with patched_paths(ROOT=os.path.join(self.tmp, "Reuniones de Ana")):  # ni el de la app ni uno anterior
            self.assertEqual(server.root_name(), "Reuniones de Ana")
            self.assertTrue(server._is_app_name("reuniones de ana"))
            self.assertEqual(server.ruta_txt("Alfa"), "Reuniones de Ana\\Alfa\\")

    def test_rutas(self):
        self.assertEqual(server.ruta_txt(""), "Chuleta\\")
        self.assertEqual(server.ruta_txt("Alfa/Reuniones"), "Chuleta\\Alfa\\Reuniones\\")
        self.assertIs(server.display_rel, server.ruta_txt)  # los avisos de mover y el directo, igual que la tarjeta
        server.LINKS["abc"] = {"name": "Alfa en OneDrive", "path": os.path.join(self.tmp, "od")}
        self.assertEqual(server.ruta_txt(":abc/Actas"), "Alfa en OneDrive\\Actas\\")

    def test_tarjeta_y_ajustes_proyectos(self):
        a = self.app(perfil={"folder": None}, rec_folder="", carpeta_por_reunion=False)
        p = a.grabar_plan_api({})
        self.assertEqual(self.carpeta(p)["valor"], "Chuleta\\")
        p = a.grabar_plan_api({"vocab": "Alfa/Vocabulario/General.txt"})
        self.assertTrue(self.carpeta(p)["valor"].startswith("Chuleta\\Alfa\\"), self.carpeta(p))
        car = a._carpetas_de_proyectos(a.settings.get())
        rutas = [x["ruta"] for x in car["proyectos"].values()] + [x["ruta"] for x in car["workstreams"].values()]
        self.assertTrue(rutas and all(r.startswith("Chuleta\\") for r in rutas), rutas)
        self.assertNotIn("Tenca\\", repr(car))

    def test_ctrl_alt_r(self):
        a = self.app(project_folders={"Alfa": "Alfa/Reuniones"})
        self.sug.update(vocab="Alfa/Vocabulario/General.txt")
        a.rec_start = lambda b: {"path": "Alfa/Reuniones/2026-11-09 Comité/x.wav", "workstream": None,
                                 "carpeta": "Alfa/Reuniones/2026-11-09 Comité"}
        with mock.patch.object(server, "cue"), mock.patch.object(server, "notify") as nt:
            a.rec_toggle({"source": "hotkey"})
        titulo, texto = nt.call_args[0][:2]
        self.assertEqual(titulo, "● Grabando en Tenca")  # la app se llama Tenca
        self.assertTrue(texto.endswith(" · se guarda en Chuleta\\Alfa\\Reuniones\\2026-11-09 Comité\\"), texto)

    def test_proyecto_de_la_raiz_y_sus_documentos(self):
        raiz = next(v for v in server.list_vocab() if v["id"] == "Vocabulario/General.txt")
        self.assertEqual(raiz["project"], "Chuleta")
        for n in ("Chuleta", "chuleta", "Tenca", "Notas"):  # reuniones y ajustes guardados con cualquiera de ellos
            self.assertTrue(server._is_app_name(n), n)
        self.assertFalse(server._is_app_name("Alfa"))
        # la reunión se graba con el proyecto de su lista (rec_start: props = {"proyecto": v["project"], …}) y sus
        # documentos «por proyecto» van a la subcarpeta de ese nombre: la de siempre
        out = os.path.join(self.tmp, "Documentos")
        doc = {"props": {"proyecto": raiz["project"], "workstream": raiz["name"]}, "stem": "2026-11-09 Comité"}
        folder, _ = salidas.dest_path({"path": out, "format": "md", "by_project": True}, doc)
        self.assertEqual(folder, os.path.join(out, "Chuleta"))
        with open(server.__file__, encoding="utf-8") as f:
            self.assertIn('props = {"proyecto": v["project"], "workstream": v["name"], "vocabulario": v["id"]}',
                          f.read())

    def test_ajustes_que_dejo_4553b9c(self):
        """El estado que deja la versión anterior (4553b9c): carpeta e idioma del proyecto de la raíz guardados como
        «Tenca». Al arrancar vuelven a «Chuleta», una vez, y la tarjeta los usa."""
        a = self.app(project_folders={"Tenca": "Alfa/Reuniones"}, project_langs={"Tenca": "en"}, nombre_v="Tenca")
        with mock.patch.object(server, "log") as lg:
            self.assertEqual(a._migrar_nombre_raiz(), [("project_folders", "Tenca"), ("project_langs", "Tenca")])
        self.assertIn("pasa a «Chuleta», el nombre de su carpeta", lg.call_args[0][0])
        st = a.settings.get()
        self.assertEqual((st["project_folders"], st["project_langs"], st["nombre_v"]),
                         ({"Chuleta": "Alfa/Reuniones"}, {"Chuleta": "en"}, "Chuleta"))
        p = a.grabar_plan_api({"vocab": "Vocabulario/General.txt"})
        self.assertEqual((self.carpeta(p)["rel"], self.carpeta(p)["origen"]), ("Alfa/Reuniones", "proyecto"))
        self.assertEqual(next(x for x in p["lineas"] if x["clave"] == "idioma")["code"], "en")
        self.assertEqual(a._migrar_nombre_raiz(), [])  # una vez

    def test_ajustes_que_dejo_chuleta(self):
        a = self.app(project_folders={"Chuleta": "Alfa/Reuniones"})
        self.assertEqual(a._migrar_nombre_raiz(), [])
        self.assertEqual(a.settings.get()["project_folders"], {"Chuleta": "Alfa/Reuniones"})
        self.assertEqual(a.settings.get()["nombre_v"], "Chuleta")


if __name__ == "__main__":
    unittest.main()
