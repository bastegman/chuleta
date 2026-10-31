# -*- coding: utf-8 -*-
"""Privacidad (auditoría del 31/10/2026): el resumen automático no sube a la nube lo transcrito en local sin
consentimiento (P1-03); los participantes sugeridos por Outlook/Teams se guardan con su origen y no van al resumen
ni a los destinos hasta confirmarlos (P1-04, P2-12); «Copiar diagnóstico» sin nombres ni rutas personales (P1-06,
P2-11); los trozos de audio temporales de la nube no se quedan en disco (P2-14); .gitignore con las reuniones de la
raíz de desarrollo fuera (P2-15) y settings.json mal formado sin perder la API key, las carpetas vinculadas ni los
destinos (P2-17). Todo con dobles: sin red, sin Windows."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from support import REPO, TempTree, bare_jobs, engines, server, srt_text  # noqa: F401  (support prepara sys.path)
import salidas  # noqa: E402
from test_perfil import FakeRec, settings_store  # noqa: E402  (ayudantes, no tests)

STATIC = os.path.join(REPO, "_app", "static")
KEY = "gsk_" + "k1" * 12
CLOUD = dict(server.DEFAULT_SETTINGS["cloud"], api_key=KEY)
NOW = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731  (una reunion de ahora: entra en los 3 dias


def static(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------- P1-03: resumen automatico

class ResumenAutomaticoTests(TempTree):
    """auto_summary solo sube a la nube lo que ya se transcribio en la nube, salvo consentimiento en Ajustes."""

    def jobs(self, *list_, **st):
        j = bare_jobs(list_)
        j.settings = settings_store(self.tmp, cloud=CLOUD, **st)
        j.library = types.SimpleNamespace(find=lambda rel: {"date": NOW()}, at=1)
        j.enviados = []
        j.submit_summary = lambda rel, auto=False: j.enviados.append(rel)
        return j

    def job(self, preset, live=False, local_part=False, status="running"):
        jb = server.Job("Alfa/r.wav", "r.wav", preset, None)
        jb.status = status
        if live:
            jb.engine, jb.live, jb.local_part = "live", True, local_part
        return jb

    def test_transcrita_en_local_no_se_resume_sola(self):
        j = self.jobs(self.job("rapido"))
        with mock.patch.object(server, "log") as log:
            j._after_transcribe("Alfa/r.wav")
        self.assertEqual(j.enviados, [])
        self.assertIn("transcrita en local", log.call_args[0][0])

    def test_transcrita_en_la_nube_si(self):
        j = self.jobs(self.job("nube"))
        j._after_transcribe("Alfa/r.wav")
        self.assertEqual(j.enviados, ["Alfa/r.wav"])

    def test_con_el_consentimiento_de_ajustes_tambien_lo_local(self):
        j = self.jobs(self.job("medium"), summary_auto_local=True)
        j._after_transcribe("Alfa/r.wav")
        self.assertEqual(j.enviados, ["Alfa/r.wav"])

    def test_apagado_no_se_resume_nada(self):
        j = self.jobs(self.job("nube"), summary_auto=False, summary_auto_local=True)
        j._after_transcribe("Alfa/r.wav")
        self.assertEqual(j.enviados, [])

    def test_directo_entero_en_la_nube_si_y_con_un_tramo_local_no(self):
        j = self.jobs(self.job("nube", live=True))
        j._after_transcribe("Alfa/r.wav")
        self.assertEqual(j.enviados, ["Alfa/r.wav"])
        j = self.jobs(self.job("nube", live=True, local_part=True))
        with mock.patch.object(server, "log"):
            j._after_transcribe("Alfa/r.wav")
        self.assertEqual(j.enviados, [])

    def test_repaso_final(self):
        """El repaso final sube la grabacion entera: si acaba, cuenta como nube; si falla o se cancela, el texto que
        queda es el del directo (y cuenta lo que fue el directo)."""
        for local_part, status, esperado in ((True, "running", True), (False, "error", True), (True, "error", False),
                                             (True, "cancelled", False)):
            fp = self.job("nube", status=status)
            fp.final_pass, fp.local_part = True, local_part
            j = self.jobs(self.job("rapido", status="done"), fp)
            self.assertIs(j._tx_in_cloud("Alfa/r.wav"), esperado, (local_part, status))
        directo = self.job("nube", live=True, local_part=True)
        j = self.jobs(directo)
        j.settings.update(lambda d: d.update(live_final_pass=True))
        j.queues = {"cloud": types.SimpleNamespace(put=lambda job: None)}
        directo.recorded = 60
        with mock.patch.object(server, "log"):
            self.assertTrue(j._final_pass(directo))
        self.assertTrue(j.list[-1].final_pass)
        self.assertTrue(j.list[-1].local_part)

    def test_tras_las_voces_vale_como_se_transcribio(self):
        """Con «Quién habla» el resumen llega al acabar las voces (_run_voices llama a auto_summary sin más)."""
        for cloud, esperado in ((False, []), (True, ["Alfa/r.wav"])):
            j = self.jobs()
            j.auto_voices = lambda rel: True  # se queda en cola para identificar voces
            j._after_transcribe("Alfa/r.wav", cloud=cloud)
            self.assertEqual(j.enviados, [])
            with mock.patch.object(server, "log"):
                j.auto_summary("Alfa/r.wav")  # lo que hace _run_voices al terminar
            self.assertEqual(j.enviados, esperado)

    def test_sin_trabajo_conocido_cuenta_como_local(self):
        j = self.jobs()
        with mock.patch.object(server, "log"):
            j.auto_summary("Alfa/r.wav")
        self.assertEqual(j.enviados, [])
        j.auto_summary("Alfa/r.wav", cloud=True)
        self.assertEqual(j.enviados, ["Alfa/r.wav"])

    def test_el_directo_apunta_si_algun_tramo_fue_en_local(self):
        self.wav("Alfa/r.wav")
        j = self.jobs()
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "log"):
            j.start_live("Alfa/r.wav", "medium")
            self.assertTrue(j.list[-1].local_part)
            j.list[-1].status = "done"
            j.start_live("Alfa/r.wav", "nube")
            job = j.list[-1]
            self.assertFalse(job.local_part)
            self.assertTrue(j._tx_in_cloud("Alfa/r.wav"))
            j.set_live_preset(job.id, "rapido")  # se pasa a local a mitad de reunion
        self.assertTrue(job.local_part)
        self.assertFalse(j._tx_in_cloud("Alfa/r.wav"))

    def test_ajuste_por_defecto_apagado_y_se_guarda(self):
        self.assertIs(server.DEFAULT_SETTINGS["summary_auto_local"], False)
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp)
        a.config = lambda q: {}
        a.save_settings({"summary": {"auto": True, "local": True}})
        self.assertIs(a.settings.get()["summary_auto_local"], True)
        a.save_settings({"summary": {"local": 0}})
        self.assertIs(a.settings.get()["summary_auto_local"], False)
        self.assertIn('"local": bool(st.get("summary_auto_local"))', open(server.__file__, encoding="utf-8").read())

    def test_casilla_y_aviso_en_ajustes(self):
        app = static("app.js")
        for s in ('id="sAutoLocal"', "Resumir también las reuniones transcritas en local (envía el texto a "
                  "${esc(provName())})", "local: $('#sAutoLocal', d).checked", 'id="sAutoLocalAviso"',
                  "la política de tu empresa y el acuerdo con tu cliente", "transcribir en la nube (reuniones"):
            self.assertIn(s, app, s)


# ----------------------------------------------------------------------------- P1-04 / P2-12: participantes

class ParticipantesAlGrabarTests(TempTree):
    def app(self):
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp)
        a.rec_lock, a.notes_lock, a.rec, a.monitor = threading.Lock(), threading.Lock(), None, None
        a.toggle_lock = threading.Lock()
        a.library = types.SimpleNamespace(at=1)
        a.jobs = bare_jobs([])
        a.jobs.settings = a.settings
        a.jobs.start_live = lambda rel, preset: None
        a.jobs.refresh_prompt = lambda rel: None
        a.jobs.schedule_outputs = lambda rel: None
        a._vocab_terms = lambda: []
        a.vigia = types.SimpleNamespace(sugerencia=lambda leer: {"name": "Comité", "vocab": "", "source": "outlook",
                                                                 "participants": ["Ana Pérez", "Luis Gómez"]})
        return a

    def start(self, a, **b):
        os.makedirs(os.path.join(self.root, "Alfa"), exist_ok=True)
        a.rec = None
        with mock.patch.object(server, "RecorderProcess", FakeRec), mock.patch.object(server, "log"):
            r = a.rec_start(dict({"mic": "default", "sys": "all", "folder": "Alfa", "live": False}, **b))
        md = os.path.splitext(server.safe(r["path"]))[0] + ".md"
        return r, (server.parse_notes(server.read_text(md))[0] if os.path.isfile(md) else {})

    def test_el_dialogo_manda_el_origen(self):
        a = self.app()
        _, props = self.start(a, name="x", vocab="", participantes="Ana Pérez,  Luis Gómez", sugerido="outlook")
        self.assertEqual(props, {"participantes": "Ana Pérez, Luis Gómez", "participantes_origen": "outlook"})
        _, props = self.start(a, name="y", vocab="", participantes="Eva", sugerido="")  # escritos por el usuario
        self.assertEqual(props, {"participantes": "Eva"})
        _, props = self.start(a, name="z", vocab="", participantes="Eva", sugerido="otro")  # origen desconocido
        self.assertEqual(props, {"participantes": "Eva"})

    def test_sin_nombre_ni_tema_la_reunion_en_curso_lleva_su_origen(self):
        _, props = self.start(self.app())
        self.assertEqual(props, {"participantes": "Ana Pérez, Luis Gómez", "participantes_origen": "outlook"})

    def test_ctrl_alt_r_pasa_el_origen(self):
        a = self.app()
        seen = []
        a.rec_start = lambda b: seen.append(b) or {"path": "Alfa/x.wav", "workstream": None}
        a.rec_folder_for = lambda v, st: "Alfa"
        with mock.patch.object(server, "cue"), mock.patch.object(server, "notify"):
            a.rec_toggle({"source": "hotkey"})
        self.assertEqual((seen[0]["participantes"], seen[0]["sugerido"]), ("Ana Pérez, Luis Gómez", "outlook"))


class ParticipantesConfirmadosTests(TempTree):
    REL = "Alfa/2026-10-31 10-00-00 - Kick-off.wav"

    def setUp(self):
        super().setUp()
        self.wav(self.REL)
        self.base = os.path.splitext(os.path.join(self.root, self.REL))[0]
        self.write(self.REL[:-4] + ".srt", srt_text([(0.0, 2.0, "Hola a todos."), (2.0, 4.0, "Empezamos.")]))
        self.write(self.REL[:-4] + ".md", "---\ntitulo: Kick-off\netiquetas: [secreta]\nestado: Revisada\n"
                                          "participantes: Ana Pérez, Luis Gómez\nparticipantes_origen: outlook\n"
                                          "proyecto: Alfa\nworkstream: Legal\nidioma: es\n"
                                          "contexto: Comité del cliente\n---\n\nOrden del día: presupuesto\n")
        self.app = server.App.__new__(server.App)
        self.app.notes_lock = threading.Lock()
        self.app.library = types.SimpleNamespace(at=1)
        self.app.jobs = types.SimpleNamespace(refresh_prompt=lambda rel: None, schedule_outputs=lambda rel: None)
        self.jobs = bare_jobs([])
        self.jobs.settings = settings_store(self.tmp, cloud=CLOUD)
        self.jobs.library = types.SimpleNamespace(find=lambda rel: {"date": "2026-10-31T10:00:00", "duration": 125.0},
                                                  at=1)

    def props(self):
        return server.parse_notes(server.read_text(self.base + ".md"))[0]

    def prompt(self):
        """Lo que se manda al proveedor para resumir esta reunion (todas las peticiones de chat juntas)."""
        sent = []

        def chat(cfg, system, user, max_tokens, report, cancelled, **k):
            sent.append(system + "\n" + user)
            return "## Resumen\n\nTres frases.", "m1"
        self.jobs.summarizer = engines.Summarizer()
        self.jobs.summarizer._chat = chat
        self.jobs.write_outputs = lambda rel: []
        job = server.Job(self.REL, os.path.basename(self.REL), "resumen", None)
        with mock.patch.object(server, "log"):
            self.jobs._run_summary(job)
        self.assertEqual(job.status, "done", job.error)
        return "\n".join(sent)

    def test_cabecera_con_el_origen(self):
        self.assertEqual(server.dump_notes({"participantes": "Ana", "participantes_origen": "outlook"}, "x"),
                         "---\nparticipantes: Ana\nparticipantes_origen: outlook\n---\n\nx\n")
        self.assertEqual(self.props()["participantes_origen"], "outlook")
        self.assertEqual(server.participantes_confirmados(self.props()), "")
        self.assertEqual(server.participantes_confirmados({"participantes": "Ana"}), "Ana")

    def test_que_campos_del_md_entran_en_el_prompt(self):
        """Titulo, contexto, notas y transcripcion; los participantes solo confirmados; nunca etiquetas, estado,
        proyecto, tema, idioma ni el origen."""
        p = self.prompt()
        for s in ("Reunión «Kick-off» (2026-10-31 10:00, 2 min).", "Contexto indicado por el usuario: Comité del "
                  "cliente.", "Orden del día: presupuesto", "Hola a todos."):
            self.assertIn(s, p)
        for s in ("Ana Pérez", "Luis Gómez", "Participantes indicados", "secreta", "Revisada", "Legal", "outlook"):
            self.assertNotIn(s, p)
        self.app.notes_save({"path": self.REL, "props": {"participantes": "Ana Pérez, Luis Gómez"}})  # «Confirmar»
        self.assertNotIn("participantes_origen", self.props())
        self.assertIn("Participantes indicados: Ana Pérez, Luis Gómez.", self.prompt())

    def test_destinos_de_salida_sin_los_sugeridos(self):
        doc = self.jobs.document_for(self.REL)
        self.assertNotIn("participantes_origen", doc["props"])
        self.assertFalse(doc["props"].get("participantes"))
        for render in (salidas.render_md, salidas.render_txt):
            self.assertNotIn("Ana Pérez", render(doc))
        self.app.notes_save({"path": self.REL, "props": {"participantes": "Ana Pérez"}})  # editados: del usuario
        doc = self.jobs.document_for(self.REL)
        self.assertEqual(doc["props"]["participantes"], "Ana Pérez")
        self.assertIn('participantes: "Ana Pérez"', salidas.render_md(doc))

    def test_guardar_otra_cosa_no_los_confirma(self):
        self.app.notes_save({"path": self.REL, "props": {"estado": "Enviada"}})
        self.assertEqual(self.props()["participantes_origen"], "outlook")
        self.app.notes_save({"path": self.REL, "body": "otras notas"})
        self.assertEqual(self.props()["participantes_origen"], "outlook")
        self.app.notes_save({"path": self.REL, "props": {"participantes": "Ana", "participantes_origen": "teams"}})
        self.assertEqual(self.props()["participantes_origen"], "teams")  # quien sabe el origen puede mantenerlo
        self.app.notes_save({"path": self.REL, "props": {"participantes_origen": "cualquiera"}})
        self.assertNotIn("participantes_origen", self.props())  # solo outlook o teams
        self.app.notes_save({"path": self.REL, "props": {"participantes": "Ana", "participantes_origen": "outlook"}})
        self.app.notes_save({"path": self.REL, "props": {"participantes": ""}})  # vaciados: sin origen que valga
        self.assertNotIn("participantes_origen", self.props())
        self.assertNotIn("participantes", self.props())


class ParticipantesTextosTests(unittest.TestCase):
    def test_la_interfaz_dice_la_verdad(self):
        js, notas = static("reunion.js"), static("notas.js")
        self.assertNotIn("Todo se queda en este PC", js)
        self.assertNotIn("(irán a las notas de la reunión)`", js)
        for s in ("como participantes <b>sugeridos</b>: no van al resumen con IA ni a los destinos de salida hasta "
                  "que los confirmes o los edites", "no se envían al resumen ni a los destinos hasta que los confirmes",
                  "function reunionParticipantesHtml(a)", "function reunionParticipantesGuardados(a)",
                  "data-rn-confirmar", "saveProps(a, { participantes:"):
            self.assertIn(s, js, s)
        self.assertNotRegex(js, r"[\U0001F300-\U0001FAFF\u2600-\u26FF]")
        self.assertEqual(notas.count("${window.reunionParticipantesHtml?.(a) || ''}"), 1)
        self.assertEqual(notas.count(".then(() => window.reunionParticipantesGuardados?.(a))"), 1)
        cal = open(os.path.join(REPO, "_app", "calendario.py"), encoding="utf-8").read()
        self.assertNotIn("Nada sale del PC", cal)
        self.assertIn("participantes_origen", cal)

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_fila_de_sugeridos(self):
        src = static("reunion.js")
        fn = re.search(r"^function reunionParticipantesHtml\(.*?^}", src, re.S | re.M).group(0)
        code = "const I = {check: '[v]'};\n" + fn + """
console.log(JSON.stringify([reunionParticipantesHtml({props: {participantes: 'Ana', participantes_origen: 'outlook'}}),
  reunionParticipantesHtml({props: {participantes: 'Ana'}}), reunionParticipantesHtml({props: {}})]));
"""
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        con, sin, vacio = json.loads(r.stdout)
        self.assertIn("Sugeridos por Outlook, sin confirmar", con)
        self.assertIn("data-rn-confirmar", con)
        self.assertEqual((sin, vacio), ("", ""))


# ----------------------------------------------------------------------------- P1-06 / P2-11: diagnostico

class DiagnosticoTests(TempTree):
    MEET = "Clientes ACME/2026-10-31 10-10-10 - Comite ACME.wav"

    def setUp(self):
        super().setUp()
        self.wav(self.MEET)
        self.write(self.MEET[:-4] + ".md", "---\ntitulo: Kick-off Zafiro\netiquetas: [Confidencial]\n"
                                           "participantes: Ana Perez, Luis Gomez\nproyecto: Proyecto Zeta\n"
                                           "workstream: Fusiones\n---\n\nnotas\n")
        self.write("Proyecto Zeta/Vocabulario/Fusiones.txt", "Zafiro\n")
        self.write("Clientes ACME/Propuesta Orion.md", "doc suelto\n")
        self.link = os.path.join(self.tmp, "OneDrive - Contoso SA", "Proyecto Faro")
        os.makedirs(self.link)
        server.LINKS["l1"] = {"name": "Faro", "path": self.link}
        self.app = server.App.__new__(server.App)
        self.app.settings = settings_store(self.tmp, cloud=CLOUD, perfil={"outputs": [
            {"path": os.path.join(self.tmp, "Vault Nebula"), "format": "md", "by_project": False}]})
        self.app.library = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        self.app.jobs = bare_jobs([])
        self.app.vigia = types.SimpleNamespace(ultimo={
            "teams": {"title": "Comite ACME"},
            "outlook": {"subject": "Comite ACME", "organizer": "Eva Sanz", "attendees": ["Iker Mora", "Alba Ruiz"]},
            "sugerencia": {"name": "Comite ACME"}})
        meet_abs = os.path.join(self.root, self.MEET)
        lines = [
            f"● Grabando: {self.MEET} [Proyecto Zeta · Fusiones]",
            f"▶ {os.path.basename(self.MEET)}  [Nube]",
            "✔ Kick-off Zafiro  resumen listo (12 s, m1)",
            "✖ 2026-10-30 09-00-00 - Reunion borrada con Bravo.wav  sin conexión",
            f"arranque · http://127.0.0.1:8765/ · carpeta {self.root} · datos {self.data_dir} · Python 3.13",
            f'Traceback: File "{os.path.join(self.root, "_app", "server.py")}", line 10, in x',
            'File "C:\\Users\\bob.smith\\AppData\\Local\\Programs\\Python\\Python313\\Lib\\json.py", line 1',
            "carpeta vinculada: C:\\Users\\bob.smith\\OneDrive - Initech\\Otra",
            f"🔗 carpeta vinculada: {self.link}",
            "   2026-10-31 10-10-10 - Comite ACME: documento escrito en 1 destino(s) · "
            + os.path.join(self.tmp, "Vault Nebula"),
            "🗣 huella guardada: Marta Olvidada (3 huellas)",
            "aviso de ana.perez@contoso.com y Eva Sanz con Iker Mora",
            "\\\\fileserver01\\finanzas\\informe.docx",
            f"no se pudo leer {meet_abs}: PermissionError",
            "/Users/carlos/Library/x y /home/carlos/x",
            "Faro · Propuesta Orion · Confidencial · Comite ACME",
            f"petición con {KEY}",
            "ventana http://127.0.0.1:8765/?t=AbCdEfGh0123456789_-xyzXYZ · X-Chuleta-Token: QwErTy0123456789abcdEF",
        ]
        with open(os.path.join(self.data_dir, "server.log"), "w", encoding="utf-8") as f:
            for i, l in enumerate(lines):
                f.write(f"2026-10-31 10:00:{i:02d} I {l}\n")

    def texto(self):
        with mock.patch.object(server, "log"):
            return self.app.diagnostico({})["texto"]

    def test_sin_nombres_ni_rutas_personales(self):
        t = self.texto()
        for s in ("ACME", "Comite", "Zafiro", "Ana Perez", "Luis Gomez", "Proyecto Zeta", "Fusiones", "Confidencial",
                  "Propuesta Orion", "Faro", "Contoso", "Initech", "Nebula", "bob.smith", "carlos", "Eva Sanz",
                  "Iker Mora", "Bravo", "Marta Olvidada", "ana.perez", "fileserver01", "finanzas", self.root,
                  self.data_dir, self.tmp, KEY, "AbCdEfGh0123456789", "QwErTy0123456789"):
            self.assertNotIn(s, t, s)

    def test_marcadores_que_dejan_entender_el_registro(self):
        t = self.texto()
        app = server.APP_NAME
        for s in ("● Grabando: <carpeta 1>/<reunión 1>.wav [<proyecto 1> · <tema 1>]", "▶ <reunión 1>.wav  [Nube]",
                  "✔ <reunión 1>  resumen listo", "✖ 2026-10-30 09-00-00 - <nombre>.wav  sin conexión",
                  f"carpeta <carpeta de {app}> · datos <datos> · Python", f"<carpeta de {app}>",
                  "C:\\Users\\<usuario>\\AppData\\Local\\Programs\\Python\\Python313", "OneDrive - <empresa>",
                  "🔗 carpeta vinculada: <vinculada 1>", "<destino 1>", "huella guardada: <persona> (3 huellas)",
                  "aviso de <correo> y <persona", "\\\\<servidor>\\<recurso>\\informe.docx", "/Users/<usuario>/",
                  "/home/<usuario>/", f"Carpeta de {app}: <carpeta de {app}> (en OneDrive: no", "Datos: <datos>",
                  "Carpetas vinculadas: 1 · destinos de salida: 1", server.masked(KEY), "server.log (últimas 30",
                  "8765/?t=<token> · X-Chuleta-Token: <token>"):
            self.assertIn(s, t, s)
        self.assertIn(os.path.join("_app", "server.py"), t)  # la ruta del codigo dentro de la app se queda

    def test_pseudonymize(self):
        f = server.pseudonymize
        self.assertEqual(f("nada que ver"), "nada que ver")
        names = [("Ana", "persona", "ana"), ("Ana Pérez", "persona", "ana p"), ("Alfa", "carpeta", "Alfa")]
        self.assertEqual(f("Ana Pérez y Ana y Ana Pérez en Alfa/x; Alfabeto", names=names),
                         "<persona 1> y <persona 2> y <persona 1> en <carpeta 1>/x; Alfabeto")
        self.assertEqual(f("C:/DATOS/Chuleta/x y c:\\datos\\chuleta\\y", paths=[("C:\\Datos\\Chuleta", "<app>", None)]),
                         "<app>/x y <app>\\y")
        self.assertEqual(f("<app> y app", names=[("app", "carpeta", 1)]), "<app> y <carpeta 1>")  # marcador intacto
        self.assertEqual(f("Reunio\u0301n Alfa", names=[("Reunión Alfa", "reunión", 1)]), "<reunión 1>")  # NFD
        self.assertEqual(f("bob en C:\\Users\\bob\\x", names=[("bob", "<usuario>", None)]),
                         "<usuario> en C:\\Users\\<usuario>\\x")
        # la ruta y el nombre de una carpeta vinculada llevan el mismo marcador, por orden de aparicion
        paths = [("/v/g", "vinculada", ":g"), ("/v/b", "vinculada", ":b")]
        names = [("Beta", "vinculada", ":b"), ("Gamma", "vinculada", ":g")]
        self.assertEqual(f("Beta en /v/b; Gamma en /v/g y otra vez Beta", paths=paths, names=names),
                         "<vinculada 1> en <vinculada 1>; <vinculada 2> en <vinculada 2> y otra vez <vinculada 1>")

    def test_aviso_junto_al_boton(self):
        app = static("app.js")
        i = app.index('id="aDiag"')
        cerca = app[i:i + 900]
        self.assertIn("revísalo antes de pegarlo y no lo publiques en un sitio público", cerca)
        self.assertIn("&lt;reunión 1&gt;", cerca)
        self.assertIn("'Diagnóstico copiado: revísalo antes de pegarlo donde pidas ayuda'", app)


# ----------------------------------------------------------------------------- P2-14: trozos de audio temporales

class TemporalesNubeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-tmp-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def files(self):
        return sorted(os.path.relpath(os.path.join(d, n), self.tmp) for d, _, ns in os.walk(self.tmp) for n in ns)

    def test_cada_trozo_se_borra_en_cuanto_se_sube(self):
        import numpy as np
        eng = engines.CloudEngine(self.tmp)
        seen = []

        def post(cfg, chunk, language, prompt, report, cancelled, **k):
            seen.append(self.files())
            return {"segments": [{"start": 0, "end": 1, "text": f"Frase {len(seen)}."}], "language": "es"}
        eng._post = post
        cfg = dict(CLOUD, chunk_minutes=1, parallel=1)
        with mock.patch.object(engines, "decode_16k", lambda p, s=0.0, c=None: np.zeros(200 * 16000, np.float32)), \
                mock.patch.object(engines, "write_flac", lambda p, y, sr=16000: open(p, "wb").close()):
            segs, _, _ = eng.transcribe("x.wav", 200, cfg, "es", None, "j1", lambda **k: None, lambda: False)
        self.assertEqual(len(seen), 2)  # dos partes
        self.assertEqual(seen, [[os.path.join("j1", "part_000.flac")], [os.path.join("j1", "part_001.flac")]])
        self.assertEqual(self.files(), [])
        self.assertEqual(len(segs), 2)

    def test_al_fallar_o_cancelar_no_queda_ningun_trozo(self):
        import numpy as np
        eng = engines.CloudEngine(self.tmp)
        for exc in (engines.EngineError("401"), engines.Cancelled()):
            def post(cfg, chunk, language, prompt, report, cancelled, exc=exc, **k):
                raise exc
            eng._post = post
            with mock.patch.object(engines, "decode_16k", lambda p, s=0.0, c=None: np.zeros(200 * 16000, np.float32)), \
                    mock.patch.object(engines, "write_flac", lambda p, y, sr=16000: open(p, "wb").close()):
                with self.assertRaises(type(exc)):
                    eng.transcribe("x.wav", 200, dict(CLOUD, chunk_minutes=1, parallel=2), "es", None, "j2",
                                   lambda **k: None, lambda: False)
            self.assertEqual(self.files(), [])

    def orphans(self):
        os.makedirs(os.path.join(self.tmp, "a1b2c3"))
        for rel in (os.path.join("a1b2c3", "part_000.flac"), "live_x.wav"):
            with open(os.path.join(self.tmp, rel), "wb") as f:
                f.write(b"audio de la reunion")

    def test_purge_tmp(self):
        self.orphans()
        eng = engines.CloudEngine(self.tmp)
        self.assertEqual(eng.purge_tmp(), 2)
        self.assertEqual(os.listdir(self.tmp), [])
        self.assertEqual(eng.purge_tmp(), 0)
        self.assertEqual(engines.CloudEngine(os.path.join(self.tmp, "no-existe")).purge_tmp(), 0)

    def test_al_arrancar_se_borran_los_huerfanos(self):
        data = tempfile.mkdtemp(prefix="chuleta-datos-")
        self.addCleanup(shutil.rmtree, data, ignore_errors=True)
        self.tmp = os.path.join(data, "tmp")
        os.makedirs(self.tmp)
        self.orphans()
        with mock.patch.object(server, "DATA_DIR", data), mock.patch.object(server.threading, "Thread"), \
                mock.patch.object(server, "log") as log:
            server.Jobs(settings_store(data), types.SimpleNamespace())
        self.assertEqual(os.listdir(self.tmp), [])
        self.assertIn("trozos de audio temporales", log.call_args_list[0][0][0])

    def test_al_apagar_tambien(self):
        self.orphans()
        j = bare_jobs([])
        j.live_worker, j.local = None, types.SimpleNamespace(worker=None)
        j.cloud = engines.CloudEngine(self.tmp)
        j.shutdown()
        self.assertEqual(os.listdir(self.tmp), [])
        j = bare_jobs([])  # un Jobs sin motor de nube (dobles de otros tests) no falla
        j.live_worker, j.local = None, types.SimpleNamespace(worker=None)
        j.shutdown()


# ----------------------------------------------------------------------------- P2-15: .gitignore

@unittest.skipUnless(shutil.which("git"), "git no instalado")
class GitignoreTests(unittest.TestCase):
    def git(self, *args):
        return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, encoding="utf-8",
                              timeout=60)

    def setUp(self):
        if self.git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            self.skipTest("no es una copia de trabajo de git")

    def test_las_reuniones_de_la_raiz_de_desarrollo_no_van_al_repo(self):
        for p in ("Proyecto Alfa/notas.md", "Proyecto Alfa/2026-10-31 10-10-10 - Comite.txt", "suelto.txt",
                  "suelto.md", "2026-10-31 10-10-10.md", "2026-10-31 10-10-10.resumen.md",
                  "tests/2026-10-31 10-10-10 - x.srt", "tests/2026-10-31 10-10-10 - x.md",
                  "design/2026-10-31_10-10-10.txt", "x.hablantes.json", "tests/a.hablantes.txt",
                  "Proyecto Alfa/Vocabulario/General.txt", "_app/python.txt", "_app/version.txt"):
            self.assertEqual(self.git("check-ignore", "--no-index", "-q", p).returncode, 0, p)

    def test_lo_del_proyecto_si(self):
        self.assertEqual(self.git("ls-files", "-ci", "--exclude-standard").stdout.strip(), "")  # nada versionado cae
        for p in ("README.md", "CHANGELOG.md", "LEEME.txt", "LICENSE", "requirements.txt", "setup.cfg",
                  "ABRIR CHULETA.bat", "INSTALAR.bat", "_app/server.py", "_app/static/app.js", "_app/export/LEEME.txt",
                  "tests/test_nuevo.py", "scripts/e2e/nuevo.ps1", ".github/workflows/nuevo.yml", "design/x.svg"):
            self.assertEqual(self.git("check-ignore", "--no-index", "-q", p).returncode, 1, p)


# ----------------------------------------------------------------------------- P2-17: settings.json mal formado

class AjustesRotosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chuleta-ajustes-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "settings.json")
        self.saved = {"language": "pt", "rec_folder": "Alfa",
                      "cloud": {"provider": "groq", "api_key": server.protect(KEY.encode()),
                                "extra_keys": [server.protect(b"gsk_" + b"e" * 24)]},
                      "links": [{"name": "Proyecto", "path": os.path.join(self.tmp, "Proyecto")}],
                      "perfil": {"context": "Comité", "outputs": [{"path": os.path.join(self.tmp, "vault"),
                                                                   "format": "md", "by_project": False}]}}

    def write(self, data):
        with open(self.path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))

    def store(self):
        with mock.patch.object(server, "log") as log:
            st = server.Store(self.path, server.DEFAULT_SETTINGS)
        return st, " ".join(str(c[0][0]) for c in log.call_args_list)

    def copias(self):
        return sorted(n for n in os.listdir(self.tmp) if n.startswith("settings.json.roto-"))

    def test_cortado_se_copia_y_se_rescata_lo_importante(self):
        text = json.dumps(self.saved, ensure_ascii=False, indent=1)
        text = text.replace('"language": "pt"', '"language": "pt",,')  # una coma de mas (editado a mano)
        self.write(text)
        st, logs = self.store()
        self.assertEqual(len(self.copias()), 1)
        with open(os.path.join(self.tmp, self.copias()[0]), "rb") as f:
            self.assertEqual(f.read(), text.encode("utf-8"))  # la copia es el fichero tal cual
        d = st.get()
        self.assertEqual(d["cloud"]["api_key"], KEY)
        self.assertEqual(d["cloud"]["extra_keys"], ["gsk_" + "e" * 24])
        self.assertEqual(d["links"], self.saved["links"])
        self.assertEqual(d["perfil"]["outputs"], self.saved["perfil"]["outputs"])
        self.assertEqual(d["language"], "")  # lo demas, por defecto
        self.assertEqual(st.broken["copia"], self.copias()[0])
        self.assertEqual(st.broken["recuperado"], ["la API key", "1 clave adicional", "1 carpeta vinculada",
                                                   "1 destino de salida"])
        self.assertIn("settings.json no se puede leer", logs)
        self.assertNotIn(KEY, logs)
        # el primer guardado (automatico o en Ajustes) no pisa la clave ni las carpetas con vacios
        st.update(lambda x: x.update(rec_folder="Beta"))
        with open(self.path, encoding="utf-8") as f:
            nuevo = json.load(f)
        self.assertTrue(server.is_protected(nuevo["cloud"]["api_key"]))
        self.assertEqual(server.unprotect(nuevo["cloud"]["api_key"]).decode(), KEY)
        self.assertEqual((nuevo["links"], nuevo["perfil"]["outputs"]),
                         (self.saved["links"], self.saved["perfil"]["outputs"]))
        self.assertEqual(len(self.copias()), 1)
        st2, _ = self.store()
        self.assertIsNone(st2.broken)

    def test_cortado_a_mitad(self):
        text = json.dumps(dict(self.saved, zzz="x" * 50), ensure_ascii=False)
        self.write(text[:-20])  # corte de luz a mitad de escritura
        st, _ = self.store()
        self.assertEqual(st.get()["cloud"]["api_key"], KEY)
        self.assertEqual(len(st.get()["links"]), 1)
        self.assertEqual(len(self.copias()), 1)

    def test_con_bom_se_lee_bien(self):
        self.write("\ufeff" + json.dumps(self.saved))  # el Bloc de notas puede guardarlo asi
        st, _ = self.store()
        self.assertIsNone(st.broken)
        self.assertEqual((st.get()["language"], st.get()["cloud"]["api_key"]), ("pt", KEY))
        self.assertEqual(self.copias(), [])

    def test_no_es_un_objeto_o_una_seccion_no_lo_es(self):
        for text in ("[]", "null", json.dumps({"language": "pt", "cloud": None}), b"\xff\xfe{}"):
            for n in self.copias():
                os.remove(os.path.join(self.tmp, n))
            self.write(text)
            st, _ = self.store()
            self.assertIsNotNone(st.broken, text)
            self.assertEqual(len(self.copias()), 1, text)
            self.assertIsInstance(st.get()["cloud"], dict)
            self.assertEqual(st.get()["cloud"]["provider"], "groq")

    def test_sin_copia_no_se_escribe_encima(self):
        self.write("{ roto")
        with mock.patch.object(server.shutil, "copy2", side_effect=OSError("disco lleno")):
            st, logs = self.store()
        self.assertTrue(st.frozen)
        self.assertIsNone(st.broken["copia"])
        self.assertIn("no se escribirá encima", logs)
        st.update(lambda x: x.update(rec_folder="Beta"))
        self.assertEqual(st.get()["rec_folder"], "Beta")  # en memoria, si
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ roto")  # en disco, el original intacto

    def test_sin_fichero_no_hay_aviso_y_se_escribe_con_fsync(self):
        st, logs = self.store()
        self.assertIsNone(st.broken)
        self.assertEqual(logs, "")
        with mock.patch.object(server.os, "fsync", wraps=os.fsync) as fsync:
            st.update(lambda x: x.update(rec_folder="Beta"))
        fsync.assert_called_once()
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_la_interfaz_avisa(self):
        self.write("{ roto")
        st, _ = self.store()
        a = server.App.__new__(server.App)
        a.settings = st
        a.jobs = bare_jobs([])
        a.jobs.settings = st
        a.jobs.cloud = types.SimpleNamespace(keys=types.SimpleNamespace(public=lambda c: None))
        a.hotkey = types.SimpleNamespace(ok=True, error=None)
        with mock.patch.object(engines, "model_cached", lambda m: False):
            cfg = a.config({})
        self.assertEqual(cfg["settings_broken"]["copia"], self.copias()[0])
        app = static("app.js")
        for s in ("function settingsBrokenHtml(b)", "if (S.config.settings_broken) toast(settingsBrokenHtml(",
                  "${S.config.settings_broken ? `<div class=\"notice\">${settingsBrokenHtml(S.config.settings_broken)}",
                  "No se han podido leer tus ajustes: settings.json estaba dañado."):
            self.assertIn(s, app, s)


if __name__ == "__main__":
    unittest.main()
