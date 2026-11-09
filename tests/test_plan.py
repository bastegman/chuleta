# -*- coding: utf-8 -*-
"""Plan de grabación (plan.py y /api/grabar/plan, rediseño B1, 07/11/2026): un solo cálculo para la tarjeta de Grabar,
Ctrl+Alt+R y el aviso flotante. Capas (esta reunión › workstream › proyecto › Ajustes), el origen de cada valor, el
proyecto por la misma reunión grabada antes (D2), «Usarlo en» (D1) y la prueba de paridad: Ctrl+Alt+R graba con
exactamente lo que enseña la tarjeta para la misma reunión."""
import os
import threading
import types
import unittest
from unittest import mock

from support import TempTree, bare_jobs, server  # noqa: F401  (support prepara sys.path)
import plan  # noqa: E402
from test_perfil import FakeRec, settings_store  # noqa: E402

LISTAS = [{"id": "Alfa/Vocabulario/General.txt", "project": "Alfa", "name": "General", "n": 41},
          {"id": "Alfa/Vocabulario/Infra.txt", "project": "Alfa", "name": "Infra", "n": 28},
          {"id": "Beta/Vocabulario/General.txt", "project": "Beta", "name": "General", "n": 12}]
A, AI, B = (x["id"] for x in LISTAS)
HIST = [{"titulo": "Comité de seguimiento", "fecha": "2026-10-30T10:00:00", "vocab": AI, "proyecto": "Alfa",
         "workstream": "Infra"},
        {"titulo": "Daily", "fecha": "2026-10-29T09:00:00", "vocab": "", "proyecto": "", "workstream": ""},
        {"titulo": "Comité de seguimiento", "fecha": "2026-10-23T10:00:00", "vocab": B, "proyecto": "Beta",
         "workstream": "General"}]
PRESETS = {"medium": {"id": "medium", "engine": "local", "label": "Clásico", "live_label": "Normal · medium"},
           "rapido": {"id": "rapido", "engine": "local", "label": "Rápido", "live_label": "Rápido · small"}}
MOTORES = {"presets": PRESETS, "procesado": "agente", "agente": "Claude", "proveedor": "Groq", "resumen_auto": True}


def resolver(st=None, sug=None, hist=HIST, pedido=None, carpeta="Alfa/Reuniones", **kw):
    return plan.resolver(st or {}, LISTAS, sug, hist, pedido, carpeta_de=lambda v: carpeta,
                         motores=MOTORES, **kw)


def lineas(p):
    return {l["clave"]: l for l in p["lineas"]}


class CapasTests(unittest.TestCase):
    def test_idioma_reunion_workstream_proyecto_ajustes(self):
        st = {"language": "es-en", "project_langs": {"Alfa": "es-pt"}, "workstream_langs": {AI: "en"}}
        cases = [({"vocab": A}, "es-pt", "proyecto", "de Alfa"),
                 ({"vocab": AI}, "en", "workstream", "del workstream"),
                 ({"vocab": B}, "es-en", "ajustes", "Ajustes › Al grabar"),
                 ({"vocab": AI, "cambios": {"idioma": "pt"}}, "pt", "reunion", "cambiado para esta reunión")]
        for pedido, code, origen, txt in cases:
            l = lineas(resolver(st, pedido=pedido))["idioma"]
            self.assertEqual((l["code"], l["origen"], l["origen_txt"]), (code, origen, txt), pedido)
        self.assertEqual(lineas(resolver({"language": "es-pt"}, pedido={"vocab": A}))["idioma"]["valor"],
                         "Español + portugués")

    def test_vocabulario_del_workstream_o_el_general(self):
        l = lineas(resolver(pedido={"vocab": A}))["vocab"]
        self.assertEqual((l["valor"], l["origen"]), ("General de Alfa · 41 términos", "workstream"))
        l = lineas(resolver({"prompt": "uno, dos; tres"}, pedido={"vocab": ""}))["vocab"]
        self.assertEqual((l["valor"], l["origen"], l["origen_txt"]),
                         ("El general · 3 términos", "ajustes", "Ajustes › Proyectos"))
        self.assertEqual(lineas(resolver(pedido={"vocab": ""}))["vocab"]["valor"], "Ninguno")

    def test_carpeta_la_decide_rec_folder_for_y_la_reunion_la_cambia(self):
        vistos = []
        p = plan.resolver({}, LISTAS, {}, [], {"vocab": AI}, carpeta_de=lambda v: vistos.append(v) or "Alfa/R",
                          carpeta_origen=lambda v, rel: ("proyecto", "carpeta de " + v["project"]),
                          links={"abc": "OneDrive Alfa"})
        self.assertEqual(vistos[0]["id"], AI)  # la lista elegida, entera
        l = lineas(p)["carpeta"]
        self.assertEqual((l["rel"], l["valor"], l["origen_txt"]), ("Alfa/R", "Chuleta\\Alfa\\R\\", "carpeta de Alfa"))
        p = resolver(pedido={"vocab": AI, "cambios": {"carpeta": ":abc/Actas"}}, links={"abc": "OneDrive Alfa"})
        l = lineas(p)["carpeta"]
        self.assertEqual((l["rel"], l["valor"], l["origen"]), (":abc/Actas", "OneDrive Alfa\\Actas\\", "reunion"))
        self.assertEqual((p["rec"]["folder"], p["rec"]["recordar_carpeta"]), (":abc/Actas", False))

    def test_carpeta_de_la_reunion_si_la_rama_de_carpetas_la_da(self):
        p = resolver(pedido={"nombre": "Comité", "vocab": A},
                     carpeta_reunion=lambda base, nombre: f"{base}/2026-11-07 {nombre}")
        l = lineas(p)["carpeta"]
        self.assertEqual(l["valor"], "Chuleta\\Alfa\\Reuniones\\2026-11-07 Comité\\")
        self.assertEqual(p["rec"]["folder"], "Alfa/Reuniones")  # rec_start crea la de la reunión en la base

    def test_transcribe_resume_y_audio(self):
        st = {"live_preset": "medium", "preset": "rapido", "rec_mic": "none", "rec_sys": "all"}
        b = lineas(resolver(st, pedido={"vocab": A}))
        self.assertEqual(b["transcribe"]["valor"], "Este PC, en directo · Normal")
        # el motor se elige en Ajustes › Procesado (revisión de B2: decía «Ajustes › Al grabar»)
        self.assertEqual((b["transcribe"]["origen"], b["transcribe"]["origen_txt"]),
                         ("procesado", "Ajustes › Procesado"))
        self.assertNotIn("aviso", b["audio"])
        self.assertEqual(b["resume"]["valor"], "Claude con tu cuenta, al terminar")
        self.assertEqual((b["audio"]["valor"], b["audio"]["origen"]),
                         ("Sin micrófono · todo lo que suena en el PC", "ajustes"))
        b = lineas(resolver(st, pedido={"vocab": A, "cambios": {"live": False, "mic": "default"}}))
        self.assertEqual(b["transcribe"]["valor"], "Este PC, al terminar · Rápido")  # el motor de la cola
        self.assertEqual((b["transcribe"]["origen"], b["audio"]["origen"]), ("reunion", "reunion"))
        b = lineas(resolver(dict(st, rec_mic="none", rec_sys="none"), pedido={"vocab": A}))
        self.assertIn("Así no se graba nada", b["audio"]["aviso"])
        local = dict(MOTORES, procesado="local")
        b = lineas(plan.resolver({}, LISTAS, {}, [], {}, motores=local))
        self.assertEqual(b["resume"]["valor"], "No se resume: todo se queda en este PC")
        api = dict(MOTORES, procesado="api", resumen_local=False)
        b = lineas(plan.resolver({}, LISTAS, {}, [], {}, motores=api))
        self.assertEqual(b["resume"]["valor"], "Groq, cuando lo pidas")  # transcrito en el PC: sin consentimiento
        # «Texto en directo» apagado en Al grabar: «al terminar» lo decide esa regla, no Procesado (revisión 2, P2-7)
        b = lineas(resolver(dict(st, rec_directo=False), pedido={"vocab": A}))
        self.assertEqual((b["transcribe"]["valor"], b["transcribe"]["origen"], b["transcribe"]["origen_txt"]),
                         ("Este PC, al terminar · Rápido", "ajustes", "Ajustes › Al grabar"))

    def test_participantes_con_coma_en_el_nombre(self):
        """Outlook de empresa: «Apellido, Nombre». Se unen con «; » para no contar el doble, como
        calendario.join_participants (rama fix-calendario); y dan lo mismo que ella, si está."""
        sug = {"name": "Comité", "source": "outlook", "vocab": "",
               "participants": ["Stegmaier, Bernardo", "García, Ana"]}
        self.assertEqual(resolver(sug=sug, pedido={})["rec"]["participantes"], "Stegmaier, Bernardo; García, Ana")
        sug["participants"] = ["Ana", "", "Luis"]
        self.assertEqual(resolver(sug=sug, pedido={})["rec"]["participantes"], "Ana, Luis")
        import calendario
        unir = getattr(calendario, "join_participants", None)
        for nombres in (["Stegmaier, Bernardo", "García, Ana"], ["Ana", "Luis"], ["Ana", ""], [], None):
            if unir:
                self.assertEqual(plan.unir_participantes(nombres), unir(nombres), nombres)

    def test_los_idiomas_se_llaman_igual_que_en_los_desplegables(self):
        """«Portugués» en el plan y «Português» en el desplegable eran dos nombres del mismo idioma (revisión 2,
        P2-5), y «Inglés» y «Solo inglés» (revisión 3): todos, iguales que en app.js (LANGS), que solo añade una
        aclaración entre paréntesis («(mezcla)», «(spanglish)»)."""
        import json
        import re
        with open(os.path.join(os.path.dirname(plan.__file__), "static", "app.js"), encoding="utf-8") as fh:
            m = re.search(r"^const LANGS = (\[.*?\]\]);$", fh.read(), re.S | re.M)
        langs = dict(json.loads(m.group(1).replace("'", '"')))
        self.assertEqual(set(langs), set(plan.IDIOMAS) - {"auto"})
        for code, txt in langs.items():
            self.assertEqual(plan.IDIOMAS[code], re.sub(r" \(.*\)$", "", txt), code)

    def test_un_motor_que_no_se_ofrece_no_se_promete(self):
        p = resolver({"live_preset": "nube"}, pedido={})
        self.assertEqual(p["rec"]["preset"], "medium")


class ProyectoTests(unittest.TestCase):
    """D2: elegido › fijo en Ajustes › la misma reunión grabada antes › el título › la última grabación."""

    def test_la_misma_reunion_grabada_antes_gana_al_titulo(self):
        p = resolver(sug={"name": "Comité de seguimiento", "vocab": B, "source": "teams"})
        self.assertEqual((p["proyecto"]["vocab"], p["proyecto"]["origen"]), (AI, "historial"))
        self.assertEqual(p["proyecto"]["motivo"], "como la última «Comité de seguimiento» (vie 30 oct)")
        self.assertEqual(p["proyecto"]["texto"], "Alfa › Infra")
        self.assertEqual((p["nombre"]["valor"], p["nombre"]["origen"], p["nombre"]["texto"]),
                         ("Comité de seguimiento", "teams", "de Teams"))

    def test_el_titulo_si_no_hay_historial(self):
        p = resolver(sug={"name": "Kick-off Beta", "vocab": B, "source": "outlook"})
        self.assertEqual((p["proyecto"]["vocab"], p["proyecto"]["origen"], p["proyecto"]["motivo"]),
                         (B, "titulo", "por la cita de Outlook"))
        vistos = []
        p = resolver(pedido={"nombre": "Revisión Beta"},
                     sugerir_titulo=lambda n: vistos.append(n) or {"vocab": B})
        self.assertEqual((vistos, p["proyecto"]["origen"], p["nombre"]["origen"]),
                         (["Revisión Beta"], "titulo", "elegido"))

    def test_la_ultima_grabacion_y_si_no_nada(self):
        p = resolver({"rec_vocab": B})
        self.assertEqual((p["proyecto"]["vocab"], p["proyecto"]["origen"]), (B, "ultima"))
        self.assertEqual(p["proyecto"]["motivo"], "como la última grabación (vie 23 oct)")
        p = resolver({"rec_vocab": "Borrada/Vocabulario/X.txt"})
        self.assertEqual((p["proyecto"]["vocab"], p["proyecto"]["origen"], p["proyecto"]["texto"]),
                         ("", "ninguno", "Sin proyecto"))

    def test_lo_elegido_y_el_fijo_de_ajustes(self):
        sug = {"name": "Comité de seguimiento", "source": "teams"}
        p = resolver({"perfil": {"vocab": B}}, sug=sug)  # la misma reunión grabada antes manda sobre el fijo
        self.assertEqual((p["proyecto"]["vocab"], p["proyecto"]["origen"]), (AI, "historial"))
        p = resolver({"perfil": {"vocab": B}}, sug={"name": "Otra cosa", "source": "teams"})
        self.assertEqual((p["proyecto"]["vocab"], p["proyecto"]["origen"]), (B, "ajustes"))
        p = resolver({"perfil": {"vocab": B}}, sug=sug, pedido={"vocab": ""})
        self.assertEqual((p["proyecto"]["vocab"], p["proyecto"]["origen"]), ("", "elegido"))
        p = resolver({"language": "es"}, pedido={"vocab": A, "cambios": {"idioma": "xx-yy-zz"}})  # no vale: no se usa
        self.assertEqual((lineas(p)["idioma"]["code"], lineas(p)["idioma"]["origen"]), ("es", "ajustes"))
        p = resolver(sug=sug, pedido={"vocab": "No/Existe.txt"})  # una lista que ya no está: como si no se eligiera
        self.assertEqual(p["proyecto"]["origen"], "historial")

    def test_nombre_escrito_de_tus_reuniones(self):
        p = resolver(pedido={"nombre": "  comite   de SEGUIMIENTO "})
        self.assertEqual((p["nombre"]["valor"], p["nombre"]["origen"], p["proyecto"]["vocab"]),
                         ("comite de SEGUIMIENTO", "historial", AI))
        p = resolver(sug={"name": "Comité de seguimiento", "source": "teams"},
                     pedido={"nombre": "Comité de seguimiento"})  # el de Teams, sin tocar
        self.assertEqual(p["nombre"]["origen"], "teams")


class RecTests(unittest.TestCase):
    def test_el_cuerpo_de_rec_start_es_lo_que_se_ve(self):
        sug = {"name": "Comité de seguimiento", "vocab": B, "source": "outlook", "participants": ["Ana", "Luis"]}
        st = {"language": "es", "project_langs": {"Alfa": "en-pt"}, "rec_mic": "m1", "rec_sys": "default",
              "live_preset": "rapido", "perfil": {"context": "Seguimiento semanal"}}
        p = resolver(st, sug=sug)
        b = lineas(p)
        self.assertEqual(p["rec"], {"name": "Comité de seguimiento", "vocab": AI, "folder": "Alfa/Reuniones",
                                    "language": "en-pt", "contexto": "Seguimiento semanal", "mic": "m1",
                                    "sys": "default", "live": True, "preset": "rapido",
                                    "participantes": "Ana, Luis", "sugerido": "outlook", "plan": True,
                                    "recordar_carpeta": True})
        self.assertEqual((b["idioma"]["code"], b["carpeta"]["rel"], b["audio"]["mic"], b["transcribe"]["preset"]),
                         (p["rec"]["language"], p["rec"]["folder"], p["rec"]["mic"], p["rec"]["preset"]))
        self.assertEqual(p["resumen"], "Comité de seguimiento → Alfa › Infra · Inglés + portugués")
        p = resolver(st, sug=sug, pedido={"participantes": ""})  # «Quitar» los asistentes sugeridos
        self.assertEqual((p["rec"]["participantes"], p["rec"]["sugerido"]), ("", ""))
        p = resolver(st, sug=sug, pedido={"nombre": "Otra cosa"})  # otra reunión: sus asistentes no
        self.assertEqual(p["rec"]["participantes"], "")


class HistorialTests(unittest.TestCase):
    def test_historial_y_recientes(self):
        flat = [{"label": "Comité", "date": "2026-10-30T10:00:00", "props": {"vocabulario": A, "proyecto": "Alfa"}},
                {"label": "Sin título no", "date": "2026-10-31T10:00:00", "props": {"titulo": "Daily"}},
                {"label": "", "date": "2026-11-01T10:00:00", "props": {}},
                {"label": "comité", "date": "2026-11-02T10:00:00", "props": {}}]
        h = plan.historial_de(flat)
        self.assertEqual([x["titulo"] for x in h], ["comité", "Daily", "Comité"])
        r = plan.recientes(h, {v["id"]: v for v in LISTAS})
        self.assertEqual([(x["titulo"], x["n"], x["vocab"], x["proyecto_txt"]) for x in r],
                         [("comité", 2, A, "Alfa › General"), ("Daily", 1, "", "")])
        # sin el id de su lista en este PC: por los nombres de su cabecera, si solo una se llama así
        otro = [{"label": "Kick-off", "date": "2026-11-03T10:00:00",
                 "props": {"proyecto": "beta", "workstream": "GENERAL"}},
                {"label": "Raro", "date": "2026-11-04T10:00:00",
                 "props": {"proyecto": "Nadie", "workstream": "General"}}]
        h = plan.historial_de(otro, LISTAS)
        self.assertEqual([x["vocab"] for x in h], ["", B])

    def test_textos(self):
        self.assertEqual(plan.fecha_corta("2026-11-07T22:00:00"), "sáb 7 nov")
        self.assertEqual(plan.fecha_corta("nada"), "")
        self.assertEqual(plan.ruta_txt(""), "Chuleta\\")
        self.assertEqual(plan.ruta_txt(":x"), "Carpeta vinculada\\")
        self.assertEqual(plan.idioma_txt("auto"), "Detectar el idioma")


# ----------------------------------------------------------------------------- el servidor

class ServidorPlanTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("Alfa/Vocabulario/General.txt", "API, SLA, Kubernetes\n")
        self.write("Beta/Vocabulario/Legal.txt", "contrato, cláusula\n")
        os.makedirs(os.path.join(self.root, "Alfa", "Reuniones"))
        self.sug = {"name": "Comité de seguimiento", "vocab": "", "source": "teams", "participants": []}

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

    def test_paridad_ctrl_alt_r_y_tarjeta(self):
        """Para la misma reunión, Ctrl+Alt+R graba con el cuerpo exacto que la tarjeta enseña (y manda) sin tocar."""
        a = self.app(project_folders={"Alfa": "Alfa/Reuniones"}, project_langs={"Alfa": "es-pt"},
                     rec_mic="m1", live_preset="rapido")
        a.library.flat = [{"label": "Comité de seguimiento", "date": "2026-10-30T10:00:00",
                           "props": {"vocabulario": "Alfa/Vocabulario/General.txt", "proyecto": "Alfa"}}]
        tarjeta = a.grabar_plan_api({})
        vistos = []
        a.rec_start = lambda b: vistos.append(b) or {"path": "Alfa/Reuniones/x.wav", "workstream": None}
        with mock.patch.object(server, "cue"), mock.patch.object(server, "notify") as nt:
            r = a.rec_toggle({"source": "hotkey"})
        self.assertEqual({k: x for k, x in vistos[0].items() if k != "resumen"}, tarjeta["rec"])
        self.assertEqual(vistos[0]["resumen"], tarjeta["resumen"])
        self.assertEqual(tarjeta["rec"]["folder"], "Alfa/Reuniones")
        self.assertEqual(tarjeta["rec"]["language"], "es-pt")
        self.assertEqual(tarjeta["proyecto"]["origen"], "historial")
        self.assertIn("Comité de seguimiento → Alfa › General · Español + portugués", nt.call_args[0][1])
        self.assertTrue(r["recording"])
        # el aviso flotante manda nombre y tema de la reunión que vio: el mismo plan
        vistos.clear()
        with mock.patch.object(server, "cue"), mock.patch.object(server, "notify"):
            a.rec_toggle({"source": "aviso", "start_only": True, "name": "Comité de seguimiento",
                          "vocab": "Beta/Vocabulario/Legal.txt"})
        self.assertEqual(vistos[0]["vocab"], "Alfa/Vocabulario/General.txt")  # la misma reunión grabada antes manda

    def test_rec_start_desde_el_plan_no_aprende_ni_pisa_lo_predeterminado(self):
        a = self.app(rec_mic="default", live_preset="medium", project_folders={})
        p = a.grabar_plan_api({"vocab": "Alfa/Vocabulario/General.txt",
                               "cambios": {"carpeta": "Alfa/Reuniones", "mic": "none", "preset": "rapido"}})
        with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log"):
            r = a.rec_start(dict(p["rec"], resumen=p["resumen"]))
        st = a.settings.get()
        self.assertEqual((st["rec_mic"], st["live_preset"], st["project_folders"]), ("default", "medium", {}))
        # la carpeta era de solo esta reunión (y de un proyecto): no pasa a ser «la última usada» (revisión de B1)
        self.assertEqual((st["rec_folder"], st["rec_vocab"]), ("", "Alfa/Vocabulario/General.txt"))
        self.assertTrue(r["path"].startswith("Alfa/Reuniones/"))
        self.assertEqual(a.jobs.started[0][1], "rapido")
        self.assertEqual(a.rec_resumen, p["resumen"])
        md = os.path.splitext(server.safe(r["path"]))[0] + ".md"
        props = server.parse_notes(server.read_text(md))[0]
        self.assertEqual((props["proyecto"], props["workstream"]), ("Alfa", "General"))

    def test_la_carpeta_de_una_reunion_no_se_cuela_en_las_siguientes(self):
        """Revisión de B1: «solo esta reunión» no pasa a ser «la última usada»; la carpeta fija de Ajustes es la de
        las reuniones sin proyecto (no tapa la del proyecto); un proyecto sin carpeta asignada usa la suya del árbol,
        no la última usada de otro proyecto."""
        os.makedirs(os.path.join(self.root, "Beta", "Actas"))
        os.makedirs(os.path.join(self.root, "Sueltas"))
        a = self.app(rec_folder="Beta/Actas")
        A, L = "Alfa/Vocabulario/General.txt", "Beta/Vocabulario/Legal.txt"
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]["rel"], "Alfa")  # su carpeta, no Beta/Actas
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]["origen_txt"], "carpeta de Alfa")
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": ""}))["carpeta"]["origen"], "ultima")  # sin proyecto
        a.grabar_recordar({"clave": "carpeta", "valor": "Sueltas", "alcance": "ajustes"})
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": ""}))["carpeta"]["rel"], "Sueltas")
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]["rel"], "Alfa")  # la fija no tapa Alfa
        a.grabar_recordar({"clave": "carpeta", "valor": "Alfa/Reuniones", "alcance": "proyecto", "vocab": A})
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]["rel"], "Alfa/Reuniones")
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": L}))["carpeta"]["rel"], "Beta")
        # solo esta reunión: graba ahí y no cambia «la última usada»
        p = a.grabar_plan_api({"vocab": "", "cambios": {"carpeta": "Beta/Actas"}})
        with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log"):
            r = a.rec_start(dict(p["rec"], name="Una"))
        self.assertTrue(r["path"].startswith("Beta/Actas/"))
        self.assertEqual(a.settings.get()["rec_folder"], "Beta/Actas")  # (el de antes: no lo ha tocado)
        a.settings.update(lambda d: d.update(rec_folder=""))
        a.rec = None
        p = a.grabar_plan_api({"vocab": "", "cambios": {"carpeta": "Beta/Actas"}})
        with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log"):
            a.rec_start(dict(p["rec"], name="Otra"))
        self.assertEqual(a.settings.get()["rec_folder"], "")

    def regla_de_carpetas(self):
        """La regla de la rama `carpetas` con su misma forma (rec_folder_origin y meeting_dir_preview, de módulo; su
        rec_folder_for la llama): para probar aquí que el plan la usa tal cual cuando está."""
        def origin(v, st):
            if v and v["project"] == "Alfa":
                return "Alfa/Reuniones", "carpeta_proyecto"
            return "Sueltas", "perfil"

        def preview(rel, label="", st=None, when=None):
            return rel if st is not None and st.get("carpeta_por_reunion") is False else \
                f"{rel}/2026-11-08 {label or '10-00'}"
        return [mock.patch.object(server, "rec_folder_origin", origin, create=True),
                mock.patch.object(server, "meeting_dir_preview", preview, create=True),
                mock.patch.object(server.App, "rec_folder_for", lambda s, v, st: origin(v, st)[0])]

    def test_con_la_regla_de_la_rama_de_carpetas(self):
        """Contrato con `carpetas` (B1b y B1c): su regla decide la carpeta y su origen (sin el arreglo 11a′ de aquí,
        que ella ya hace), y la tarjeta enseña la carpeta de la reunión que creará rec_start, con el nombre limpio
        como lo limpia rec_start. rec.folder sigue siendo la principal."""
        os.makedirs(os.path.join(self.root, "Beta", "Actas"))
        a = self.app(rec_folder="Beta/Actas")
        A = "Alfa/Vocabulario/General.txt"
        for p in self.regla_de_carpetas():
            p.start()
            self.addCleanup(p.stop)
        car = lineas(a.grabar_plan_api({"vocab": A, "nombre": "Comité: Q4"}))["carpeta"]
        self.assertEqual((car["rel"], car["origen"], car["origen_txt"]),
                         ("Alfa/Reuniones", "proyecto", "carpeta de Alfa"))
        self.assertEqual(car["sub"], "Alfa/Reuniones/2026-11-08 " + server.calendario.nombre_archivo("Comité: Q4"))
        self.assertIn("2026-11-08", car["valor"])
        p = a.grabar_plan_api({"vocab": A, "nombre": "Comité"})
        self.assertEqual(p["rec"]["folder"], "Alfa/Reuniones")
        sin = lineas(a.grabar_plan_api({"vocab": ""}))["carpeta"]
        self.assertEqual((sin["rel"], sin["origen"], sin["origen_txt"]),
                         ("Sueltas", "ajustes", "Ajustes › Al grabar"))
        # cambiada para esta reunión: la carpeta de la reunión, dentro de la elegida
        p = a.grabar_plan_api({"vocab": A, "nombre": "Comité", "cambios": {"carpeta": "Beta/Actas"}})
        self.assertEqual((lineas(p)["carpeta"]["sub"], p["rec"]["folder"]),
                         ("Beta/Actas/2026-11-08 Comité", "Beta/Actas"))
        # sin carpeta por reunión, la principal tal cual
        a.settings.update(lambda d: d.update(carpeta_por_reunion=False))
        self.assertIsNone(lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]["sub"])

    @unittest.skipUnless(hasattr(server, "rec_destino"), "solo con la rama de carpetas fusionada")
    def test_contrato_rec_destino(self):
        """Tras fusionar `carpetas`: la tarjeta dice la misma carpeta (principal y de la reunión) que rec_destino."""
        a = self.app()
        A = "Alfa/Vocabulario/General.txt"
        car = lineas(a.grabar_plan_api({"vocab": A, "nombre": "Comité"}))["carpeta"]
        d = server.rec_destino(server.find_vocab(None, None, A), a.settings.get(), "Comité")
        self.assertEqual((car["rel"], car["sub"] or car["rel"]), (d["carpeta"], d["reunion"]))

    def test_carpetas_de_proyectos_con_el_mismo_calculo(self):
        """Ajustes › Proyectos (B2): la carpeta de cada proyecto y workstream con su origen, del mismo cálculo que la
        tarjeta; la asignada, la del árbol si no hay, y la que tendría sin asignar."""
        os.makedirs(os.path.join(self.root, "Beta", "Actas"))
        a = self.app(rec_folder="Beta/Actas")
        A, L = "Alfa/Vocabulario/General.txt", "Beta/Vocabulario/Legal.txt"
        c = a.grabar_plan_api({"vocab": "", "proyectos": True})["carpetas"]
        self.assertEqual((c["proyectos"]["Alfa"]["rel"], c["proyectos"]["Alfa"]["origen"]), ("Alfa", "proyecto"))
        self.assertEqual(c["proyectos"]["Alfa"]["sin_asignar"]["rel"], "Alfa")
        self.assertEqual((c["workstreams"][A]["rel"], c["workstreams"][A]["origen"]), ("Alfa", "proyecto"))
        self.assertEqual(c["proyectos"]["Alfa"]["rel"], lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]["rel"])
        a.grabar_recordar({"clave": "carpeta", "valor": "Alfa/Reuniones", "alcance": "proyecto", "vocab": A})
        c = a.grabar_plan_api({"vocab": "", "proyectos": True})["carpetas"]
        self.assertEqual(c["proyectos"]["Alfa"]["rel"], "Alfa/Reuniones")
        self.assertEqual(c["proyectos"]["Alfa"]["sin_asignar"]["rel"], "Alfa")  # al quitarla, la suya del árbol
        self.assertEqual(c["proyectos"]["Beta"]["rel"], "Beta")
        self.assertEqual(c["workstreams"][L]["origen"], "proyecto")
        self.assertTrue(c["proyectos"]["Alfa"]["ruta"].endswith("Alfa\\Reuniones\\"), c["proyectos"]["Alfa"]["ruta"])
        # con la regla de la rama de carpetas, lo que diga ella
        for p in self.regla_de_carpetas():
            p.start()
            self.addCleanup(p.stop)
        c = a.grabar_plan_api({"vocab": "", "proyectos": True})["carpetas"]
        self.assertEqual((c["proyectos"]["Alfa"]["rel"], c["proyectos"]["Beta"]["origen"]),
                         ("Alfa/Reuniones", "ajustes"))

    def test_sin_microfono_y_sin_audio_del_pc_no_se_guarda(self):
        """Revisión de B2: «Sin micrófono» y «Sin el audio del PC» a la vez no graban nada (rec_start lo rechaza al
        empezar): no se guardan así, ni desde Ajustes ni con «Usarlo en: todas»."""
        a = self.app(rec_mic="none", rec_sys="all")
        with self.assertRaises(server.ApiError) as c:
            a.save_settings({"rec_sys": "none"})
        self.assertIn("no se graba nada", c.exception.msg)
        with self.assertRaises(server.ApiError):
            a.save_settings({"rec_mic": "none", "rec_sys": "none"})
        with self.assertRaises(server.ApiError):
            a.grabar_recordar({"clave": "sys", "valor": "none", "alcance": "ajustes"})
        self.assertEqual(a.settings.get()["rec_sys"], "all")
        a.grabar_recordar({"clave": "mic", "valor": "default", "alcance": "ajustes"})
        a.grabar_recordar({"clave": "sys", "valor": "none", "alcance": "ajustes"})  # con micrófono, sí
        self.assertEqual((a.settings.get()["rec_mic"], a.settings.get()["rec_sys"]), ("default", "none"))

    def test_ajustes_lo_no_guardado_de_otras_secciones_y_la_plantilla(self):
        """Revisión de B2: «Así se graba ahora» con lo que aún no se ha guardado en otras secciones (motores, resumir
        al terminar, vocabulario general, carpeta por reunión), y sin reunión la carpeta de la reunión como patrón."""
        a = self.app(live_preset="medium", preset="rapido", summary_auto=True, procesado="agente")
        A = "Alfa/Vocabulario/General.txt"
        resume = lineas(a.grabar_plan_api({"nombre": "", "vocab": "", "ajustes": {}}))["resume"]["valor"]
        self.assertIn("al terminar", resume)
        aj = {"live_preset": "rapido", "summary": {"auto": False}, "prompt": "SLA, KPI"}
        b = lineas(a.grabar_plan_api({"nombre": "", "vocab": "", "ajustes": aj}))
        self.assertEqual(b["transcribe"]["valor"], "Este PC, en directo · Rápido")
        self.assertIn("cuando lo pidas", b["resume"]["valor"])
        self.assertEqual(b["vocab"]["valor"], "El general · 2 términos")
        for p in self.regla_de_carpetas():
            p.start()
            self.addCleanup(p.stop)
        car = lineas(a.grabar_plan_api({"nombre": "", "vocab": A, "ajustes": {}}))["carpeta"]
        self.assertEqual(car["sub"], "Alfa/Reuniones/" + server.App.PLANTILLA_REUNION)
        self.assertIn("AAAA-MM-DD Nombre de la reunión", car["valor"])
        apagada = {"nombre": "", "vocab": A, "ajustes": {"carpeta_por_reunion": False}}
        car = lineas(a.grabar_plan_api(apagada))["carpeta"]
        self.assertIsNone(car["sub"])  # apagada sin guardar: ya no
        car = lineas(a.grabar_plan_api({"nombre": "", "vocab": A}))["carpeta"]  # la tarjeta: la de ahora
        self.assertEqual(car["sub"], "Alfa/Reuniones/2026-11-08 10-00")

    def test_carpeta_de_un_workstream(self):
        """Ajustes › Proyectos: la carpeta principal de un workstream (la regla de la rama de carpetas la usa antes que
        la del proyecto; sin ella, aquí también)."""
        os.makedirs(os.path.join(self.root, "Alfa", "Infra"))
        a = self.app(project_folders={"Alfa": "Alfa/Reuniones"})
        A = "Alfa/Vocabulario/General.txt"
        a.grabar_recordar({"clave": "carpeta", "valor": "Alfa/Infra", "alcance": "workstream", "vocab": A})
        self.assertEqual(a.settings.get()["workstream_folders"], {A: "Alfa/Infra"})
        car = lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]
        self.assertEqual((car["rel"], car["origen"], car["origen_txt"]),
                         ("Alfa/Infra", "workstream", "carpeta del workstream"))
        c = a.grabar_plan_api({"vocab": "", "proyectos": True})["carpetas"]
        self.assertEqual((c["workstreams"][A]["origen"], c["proyectos"]["Alfa"]["rel"]),
                         ("workstream", "Alfa/Reuniones"))
        a.hotkey = types.SimpleNamespace(ok=True, error=None)
        self.assertEqual(a.config({})["workstream_folders"], {A: "Alfa/Infra"})
        a.grabar_recordar({"clave": "carpeta", "valor": None, "alcance": "workstream", "vocab": A})
        self.assertEqual(lineas(a.grabar_plan_api({"vocab": A}))["carpeta"]["rel"], "Alfa/Reuniones")

    def test_nombre_con_signos_que_no_admite_un_archivo(self):
        """«Comité: Q4» escrito en la tarjeta: rec_start rechazaba el nombre («Nombre no válido») al empezar. El plan lo
        manda ya sin esos signos (como la carpeta de la reunión) y lo dice; Ctrl+Alt+R, igual."""
        a = self.app()
        p = a.grabar_plan_api({"nombre": "Comité: Q4 | Alfa?", "vocab": ""})
        self.assertEqual((p["rec"]["name"], p["nombre"]["archivo"]), ("Comité Q4 Alfa", "Comité Q4 Alfa"))
        self.assertEqual(p["nombre"]["valor"], "Comité: Q4 | Alfa?")  # lo escrito se queda en la caja
        with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log"):
            r = a.rec_start(dict(p["rec"], resumen=p["resumen"]))
        self.assertTrue(r["path"].endswith(" - Comité Q4 Alfa.wav"), r["path"])
        self.assertNotIn("archivo", a.grabar_plan_api({"nombre": "Comité Q4", "vocab": ""})["nombre"])

    def test_ctrl_alt_r_con_asistentes_sin_origen_toma_el_de_la_reunion(self):
        a = self.app()
        self.sug.update(source="outlook", participants=["Ana"])
        vistos = []
        a.rec_start = lambda b: vistos.append(b) or {"path": "x.wav", "workstream": None}
        with mock.patch.object(server, "cue"), mock.patch.object(server, "notify"):
            a.rec_toggle({"source": "otra-app", "participantes": "Eva"})
        self.assertEqual((vistos[0]["participantes"], vistos[0]["sugerido"]), ("Eva", "outlook"))

    def test_usarlo_en_el_proyecto_o_en_ajustes(self):
        a = self.app(language="es-en")
        A = "Alfa/Vocabulario/General.txt"
        pedido = {"vocab": A}
        a.grabar_recordar({"clave": "idioma", "valor": "pt", "alcance": "proyecto", "vocab": A})
        l = lineas(a.grabar_plan_api(pedido))["idioma"]
        self.assertEqual((l["code"], l["origen_txt"]), ("pt", "de Alfa"))
        a.grabar_recordar({"clave": "idioma", "valor": "en", "alcance": "workstream", "vocab": A})
        self.assertEqual(lineas(a.grabar_plan_api(pedido))["idioma"]["origen"], "workstream")
        a.grabar_recordar({"clave": "idioma", "valor": None, "alcance": "workstream", "vocab": A})  # quitar
        a.grabar_recordar({"clave": "idioma", "valor": None, "alcance": "proyecto", "vocab": A})
        l = lineas(a.grabar_plan_api(pedido))["idioma"]
        self.assertEqual((l["code"], l["origen"]), ("es-en", "ajustes"))
        a.grabar_recordar({"clave": "idioma", "valor": "de", "alcance": "ajustes"})
        self.assertEqual(a.settings.get()["language"], "de")
        a.grabar_recordar({"clave": "carpeta", "valor": "Alfa/Reuniones", "alcance": "proyecto", "vocab": A})
        l = lineas(a.grabar_plan_api(pedido))["carpeta"]
        self.assertEqual((l["rel"], l["origen"], l["origen_txt"]), ("Alfa/Reuniones", "proyecto", "carpeta de Alfa"))
        for clave, valor, key, esperado in (("mic", "m2", "rec_mic", "m2"), ("sys", "", "rec_sys", "all"),
                                            ("live", 0, "rec_directo", False),
                                            ("preset", "rapido", "live_preset", "rapido")):
            a.grabar_recordar({"clave": clave, "valor": valor, "alcance": "ajustes"})
            self.assertEqual(a.settings.get()[key], esperado, clave)
        for mal in ({"clave": "idioma", "valor": "xx-yy-zz", "alcance": "ajustes"},
                    {"clave": "idioma", "valor": "pt", "alcance": "proyecto"},
                    {"clave": "mic", "valor": "x", "alcance": "proyecto", "vocab": A},
                    {"clave": "carpeta", "valor": "No/Existe", "alcance": "ajustes"},
                    {"clave": "preset", "valor": "inventado", "alcance": "ajustes"},
                    {"clave": "idioma", "valor": "pt", "alcance": "reunion"}):
            with self.assertRaises(server.ApiError, msg=mal):
                a.grabar_recordar(mal)

    def test_ajustes_sin_guardar_se_ven_en_el_plan(self):
        """B2: «Así se graba ahora» enseña lo que quedará con lo escrito en Ajustes › Al grabar, sin guardarlo."""
        a = self.app(language="es", rec_mic="default")
        antes = a.settings.get()
        aj = {"language": "pt", "rec_mic": "none", "rec_directo": False, "perfil": {"context": "X"}}
        p = a.grabar_plan_api({"nombre": "", "vocab": "", "ajustes": aj})
        b = lineas(p)
        self.assertEqual((b["idioma"]["code"], b["audio"]["mic"], b["transcribe"]["live"], b["contexto"]["valor"]),
                         ("pt", "none", False, "X"))
        self.assertEqual(b["idioma"]["origen"], "ajustes")
        self.assertEqual(a.settings.get(), antes)  # no se ha guardado nada
        p = a.grabar_plan_api({"vocab": "", "ajustes": {"language": "xx-yy-zz", "perfil": {"folder": "No/Existe"}}})
        self.assertEqual(lineas(p)["idioma"]["code"], "es")  # lo que no vale se ignora

    def test_el_modo_marcado_en_procesado_aunque_no_se_haya_aplicado(self):
        """Revisión 2 de B2, P1-2: «Guardar» aplica la opción marcada en «Dónde se procesan»; el plan la enseña ya."""
        a = self.app(procesado="local", summary_auto=True)

        def resume(aj):
            return lineas(a.grabar_plan_api({"nombre": "", "vocab": "", "ajustes": aj}))["resume"]["valor"]
        self.assertEqual(resume({}), "No se resume: todo se queda en este PC")
        self.assertEqual(resume({"procesado": "agente"}), "Claude con tu cuenta, al terminar")
        self.assertEqual(resume({"procesado": "nada"}), "No se resume: todo se queda en este PC")  # no vale: se ignora
        self.assertEqual(a.settings.get()["procesado"], "local")

    def test_carpetas_que_no_se_encuentran_o_que_faltan_se_dicen(self):
        """Revisión 3 de B2: con la carpeta de Ajustes guardada y perdida, el plan sigue con el resto de lo escrito (el
        contexto) y dice por qué graba en otra; un proyecto sin carpeta propia (listas de una carpeta conectada, sin
        asignar) dice dónde asignársela; con «API externa» y sin clave, lo dice en Transcribe y Resume."""
        a = self.app(perfil={"folder": "Fuera/Clientes"})
        aj = {"perfil": {"folder": "Fuera/Clientes", "context": "Comité"}}
        b = lineas(a.grabar_plan_api({"nombre": "", "vocab": "", "ajustes": aj}))
        self.assertEqual(b["contexto"]["valor"], "Comité")
        self.assertIn("No se encuentra la carpeta de Ajustes (Chuleta\\Fuera\\Clientes\\)", b["carpeta"]["aviso"])

        def origin(v, st):
            return ("Alfa/Reuniones", "carpeta_proyecto") if v and v["project"] == "Alfa" else ("", "ultimo")
        with mock.patch.object(server, "rec_folder_origin", origin, create=True), \
                mock.patch.object(server.App, "rec_folder_for", lambda s, v, st: origin(v, st)[0]):
            sin = self.app(perfil={"folder": None})
            b = lineas(sin.grabar_plan_api({"nombre": "", "vocab": "Beta/Vocabulario/Legal.txt"}))
            self.assertEqual(b["carpeta"]["aviso"],
                             "Beta no tiene carpeta propia: asígnale una en Ajustes › Proyectos o con «Cambiar» "
                             "al grabar.")
            b = lineas(sin.grabar_plan_api({"nombre": "", "vocab": "Alfa/Vocabulario/General.txt"}))
            self.assertNotIn("aviso", b["carpeta"])
        b = lineas(self.app(procesado="api", perfil={"folder": None}).grabar_plan_api({"nombre": "", "vocab": ""}))
        self.assertEqual(b["resume"].get("aviso"), "Falta la API key: ponla en Ajustes › Procesado.")
        con_clave = self.app(procesado="api", cloud={"provider": "groq", "api_key": "x"})
        self.assertNotIn("aviso", lineas(con_clave.grabar_plan_api({"nombre": "", "vocab": ""}))["resume"])

    def test_perfil_guardado_que_ahora_no_se_encuentra(self):
        """Revisión 2 de B2, P1-3: la carpeta o la lista del perfil que ahora no se encuentran (una vinculada
        desconectada, OneDrive sin conexión) vuelven tal cual con cualquier «Guardar»: ni se pierden ni impiden guardar
        lo demás. Una nueva que no existe, sí se rechaza."""
        actual = {"folder": "Fuera/Clientes", "vocab": "Omega/Vocabulario/Gen.txt"}
        self.assertEqual(server.check_perfil(dict(actual), actual=actual), actual)
        nuevas = (({"folder": "Otra/No"}, "No existe la carpeta"),
                  ({"vocab": "Omega/Vocabulario/X.txt"}, "ya no existe"))
        for b, msg in nuevas:
            with self.assertRaises(server.ApiError) as e:
                server.check_perfil(b, actual=actual)
            self.assertIn(msg, e.exception.msg)
        self.assertEqual(server.check_perfil({"folder": None, "vocab": None}, actual=actual),
                         {"folder": None, "vocab": None})

    def test_renombrar_un_tema_o_un_proyecto_conserva_su_idioma(self):
        d = {"workstream_langs": {"Alfa/Vocabulario/General.txt": "pt"}, "project_langs": {"Alfa": "en"}}
        server.settings_moved(d, "Alfa", "Alfa2", folder=True, project=("Alfa", "Alfa2"))
        self.assertEqual(d["workstream_langs"], {"Alfa2/Vocabulario/General.txt": "pt"})
        self.assertEqual(d["project_langs"], {"Alfa2": "en"})

    def test_lineas_y_recientes(self):
        a = self.app()
        self.assertEqual(server.DEFAULT_SETTINGS["rec_directo"], True)
        p = a.grabar_plan_api({"recientes": True})
        self.assertEqual(p["recientes"], [])
        self.assertEqual([l["clave"] for l in p["lineas"]],
                         ["carpeta", "idioma", "vocab", "contexto", "transcribe", "resume", "audio"])


class RutasPlanTests(unittest.TestCase):
    def test_rutas_en_el_servidor(self):
        with open(server.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"/api/grabar/plan": a.grabar_plan_api', src)
        self.assertIn('"/api/grabar/recordar": a.grabar_recordar', src)
        self.assertEqual(src.count("self.plan_grabar("), 2)  # la tarjeta (grabar_plan_api) y Ctrl+Alt+R


if __name__ == "__main__":
    unittest.main()
