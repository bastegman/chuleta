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
    """auto_summary solo sube a la nube el texto que se transcribio entero en la nube, salvo consentimiento en
    Ajustes. El origen lo apunta cada transcripcion al escribir (CloudText, en DATA_DIR), no el trabajo: asi vale
    igual para un repaso final retomado tras reiniciar (fix-directo lo crea con _queue_final_pass, sin el directo)."""

    REL = "Alfa/r.wav"

    def jobs(self, *list_, **st):
        j = bare_jobs(list_)
        j.settings = settings_store(self.tmp, cloud=CLOUD, **st)
        j.library = types.SimpleNamespace(find=lambda rel: {"date": NOW(), "duration": 2.0}, at=1)
        j.enviados = []
        j.submit_summary = lambda rel, auto=False: j.enviados.append(rel)
        return j

    def transcribir(self, j, preset, start=None, final_pass=False):
        """Jobs._run de verdad con un motor de mentira (local o nube): escribe el texto y llega al resumen."""
        if not os.path.isfile(os.path.join(self.root, self.REL)):
            self.wav(self.REL)
        jb = server.Job(self.REL, "r.wav", preset, None)
        jb.resume_from, jb.final_pass = start, final_pass
        motor = types.SimpleNamespace(transcribe=lambda *a, **k: ([(0.0, 1.0, "hola"), (1.0, 1.9, "adiós")], "es",
                                                                  2.0))
        j.local = j.cloud = motor
        with j.lock:
            j.list.append(jb)
        with mock.patch.object(server, "log") as log:
            j._run(jb)
        self.assertEqual(jb.status, "done", jb.error)
        jb.logged = [c[0][0] for c in log.call_args_list]
        return jb

    def directo(self, j, preset):
        if not os.path.isfile(os.path.join(self.root, self.REL)):
            self.wav(self.REL)
        with mock.patch.object(server.threading, "Thread"), mock.patch.object(server, "log"):
            j.start_live(self.REL, preset)
        job = j.list[-1]
        job.status = "done"
        return job

    def test_transcrita_en_local_no_se_resume_sola(self):
        j = self.jobs()
        jb = self.transcribir(j, "rapido")
        self.assertEqual(j.enviados, [])
        self.assertTrue(any("transcrita en local, sin resumen automático" in m for m in jb.logged), jb.logged)

    def test_transcrita_en_la_nube_si(self):
        j = self.jobs()
        self.transcribir(j, "nube")
        self.assertEqual(j.enviados, [self.REL])

    def test_con_el_consentimiento_de_ajustes_tambien_lo_local(self):
        j = self.jobs(summary_auto_local=True)
        self.transcribir(j, "medium")
        self.assertEqual(j.enviados, [self.REL])

    def test_apagado_no_se_resume_nada(self):
        j = self.jobs(summary_auto=False, summary_auto_local=True)
        self.transcribir(j, "nube")
        self.assertEqual(j.enviados, [])

    def test_directo_entero_en_la_nube_si_y_con_un_tramo_local_no(self):
        j = self.jobs()
        self.directo(j, "nube")
        j._after_transcribe(self.REL)
        self.assertEqual(j.enviados, [self.REL])
        j = self.jobs()
        job = self.directo(j, "nube")
        job.status = "running"
        with mock.patch.object(server, "log"):
            j.set_live_preset(job.id, "rapido")  # se pasa a local a mitad de reunion
            j._after_transcribe(self.REL)
        self.assertTrue(job.local_part)
        self.assertEqual(j.enviados, [])

    def test_repaso_final_retomado_tras_reiniciar(self):
        """El escenario de la revisión: directo con un tramo local que acaba en la nube, se apaga con el repaso final
        en cola y, al volver a abrir, el repaso retomado (un Job nuevo, sin nada del directo) falla o se cancela: el
        texto que queda es el del directo y no sube a la nube sin permiso."""
        j = self.jobs()
        job = self.directo(j, "medium")
        job.status = "running"
        with mock.patch.object(server, "log"):
            j.set_live_preset(job.id, "nube")
        for status in ("error", "cancelled"):
            otra = self.jobs()  # la app vuelve a arrancar: otra lista de trabajos, el mismo DATA_DIR
            repaso = server.Job(self.REL, "r.wav", "nube", None)  # como _queue_final_pass(rel, name, language)
            repaso.final_pass, repaso.status = True, status
            otra.list.append(repaso)
            with mock.patch.object(server, "log"):
                otra.auto_summary(self.REL)
            self.assertEqual(otra.enviados, [], status)
        # si el directo fue entero en la nube, el texto que queda también lo es
        j = self.jobs()
        self.directo(j, "nube")
        otra = self.jobs()
        otra.auto_summary(self.REL)
        self.assertEqual(otra.enviados, [self.REL])

    def test_repaso_final_terminado_cuenta_como_nube(self):
        j = self.jobs()
        self.directo(j, "medium")
        self.transcribir(j, "nube", final_pass=True)  # la grabacion entera, otra vez, en la nube
        self.assertEqual(j.enviados, [self.REL])

    def test_tras_las_voces_vale_como_se_transcribio(self):
        """Con «Quién habla» el resumen llega al acabar las voces (_run_voices llama a auto_summary sin más)."""
        for preset, esperado in (("rapido", []), ("nube", [self.REL])):
            j = self.jobs()
            j.auto_voices = lambda rel: True  # se queda en cola para identificar voces
            self.transcribir(j, preset)
            self.assertEqual(j.enviados, [])
            with mock.patch.object(server, "log"):
                j.auto_summary(self.REL)  # lo que hace _run_voices al terminar
            self.assertEqual(j.enviados, esperado)

    def test_sin_dato_cuenta_como_local(self):
        j = self.jobs()
        with mock.patch.object(server, "log"):
            j.auto_summary(self.REL)
        self.assertEqual(j.enviados, [])

    def test_continuar_una_a_medias(self):
        """Continuar desde donde se quedó: en la nube solo si también lo fue lo de antes."""
        for antes, despues, esperado in (("rapido", "nube", []), ("nube", "nube", [self.REL]),
                                         ("nube", "rapido", [])):
            j = self.jobs()
            self.transcribir(j, antes)
            j.enviados.clear()
            self.transcribir(j, despues, start=1.0)
            self.assertEqual(j.enviados, esperado, (antes, despues))

    def test_retomar_un_directo_tras_reiniciar(self):
        """La app se reinicia a mitad de grabación: lo que ya había escrito cuenta como lo que fuera."""
        srt = os.path.join(self.root, self.REL[:-4] + ".srt")
        for primero, local in (("medium", True), ("nube", False)):
            if os.path.exists(srt):
                os.remove(srt)
            self.directo(self.jobs(), primero)
            self.write(self.REL[:-4] + ".srt", srt_text([(0.0, 1.0, "hola")]))  # lo escrito antes de cortarse
            otra = self.jobs()  # la app vuelve a arrancar y retoma el directo en la nube
            self.assertIs(self.directo(otra, "nube").local_part, local, primero)
            self.assertIs(otra._tx_in_cloud(self.REL), not local, primero)

    def test_un_solo_registro_aunque_lo_pidan_dos_hilos(self):
        j = self.jobs()
        real, creados = server.CloudText, []

        def lento(path):
            time.sleep(0.05)  # dos hilos dentro de la creacion a la vez
            creados.append(path)
            return real(path)
        with mock.patch.object(server, "CloudText", side_effect=lento):
            hilos = [threading.Thread(target=j._cloud_text) for _ in range(4)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join()
        self.assertEqual(len(creados), 1)

    def test_el_origen_se_guarda_en_datos_y_se_olvida(self):
        path = os.path.join(self.data_dir, "transcritas_en_la_nube.json")
        reg = server.CloudText(path)
        reg.set("a.wav", True)
        reg.set("b.wav", True)
        reg.set("b.wav", False)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)), ["a.wav"])
        self.assertIn("a.wav", server.CloudText(path))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"vieja.wav": time.time() - 8 * 86400, "a.wav": time.time()}, f)
        reg = server.CloudText(path)
        reg.set("c.wav", True)
        self.assertEqual(sorted(reg.data), ["a.wav", "c.wav"])  # la de hace 8 dias ya no hace falta
        with open(path, "w", encoding="utf-8") as f:
            f.write("[roto")
        self.assertEqual(server.CloudText(path).data, {})

    def app(self, j):
        """Una App con lo que usan move, rename_folder y delete; sus trabajos son j (el mismo CloudText)."""
        a = server.App.__new__(server.App)
        a.jobs, a.rec, a.settings = j, None, j.settings
        j.voice_vectors = {}
        a.library = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        return a

    def test_mover_renombrar_y_borrar_llevan_el_origen(self):
        """El caso de la revisión: A se transcribe en la nube y se mueve a otra carpeta; otra reunión, transcrita en
        local y a medias, acaba con el nombre que tenía A y se continúa en la nube: no sube sin permiso. El origen va
        con la reunión al moverla o renombrarla (ella o su carpeta) y se olvida al borrarla."""
        j = self.jobs()
        a = self.app(j)
        self.transcribir(j, "nube")  # A = Alfa/r.wav, entera en la nube
        os.makedirs(os.path.join(self.root, "Beta"))
        with mock.patch.object(server, "log"):
            self.assertEqual(a.move({"path": self.REL, "folder": "Beta"})["path"], "Beta/r.wav")
        self.assertTrue(j._tx_in_cloud("Beta/r.wav"))
        self.assertFalse(j._tx_in_cloud(self.REL))
        self.wav("Gamma/c.wav")  # transcrita en local y a medias, se mueve a Alfa con el nombre de A
        self.write("Gamma/c.srt", srt_text([(0.0, 1.0, "texto transcrito en local")]))
        with mock.patch.object(server, "log"):
            a.move({"path": "Gamma/c.wav", "folder": "Alfa", "name": "r"})
        self.assertFalse(j._tx_in_cloud(self.REL))
        j.enviados.clear()
        self.transcribir(j, "nube", start=1.0)  # «continuar» desde donde acaba, en la nube
        self.assertEqual(j.enviados, [])
        # una entrada que se quedó en un nombre (su reunión se borró fuera de la app) no pasa a la que llega ahí
        j._cloud_text().set("Alfa/x.wav", True)
        self.wav("Gamma/d.wav")
        with mock.patch.object(server, "log"):
            a.move({"path": "Gamma/d.wav", "folder": "Alfa", "name": "x"})
        self.assertFalse(j._tx_in_cloud("Alfa/x.wav"))
        # renombrar la reunión y luego su carpeta: el origen la sigue, también tras reiniciar (vive en DATA_DIR)
        with mock.patch.object(server, "log"):
            a.move({"path": "Beta/r.wav", "folder": "Beta", "name": "s"})
            self.assertTrue(j._tx_in_cloud("Beta/s.wav"))
            a.rename_folder({"path": "Beta", "name": "Delta"})
        self.assertTrue(j._tx_in_cloud("Delta/s.wav"))
        self.assertFalse(any(j._tx_in_cloud(p) for p in ("Beta/r.wav", "Beta/s.wav")))
        self.assertTrue(self.jobs()._tx_in_cloud("Delta/s.wav"))
        # borrarla lo olvida: otra que acabe con ese nombre no lo hereda
        with mock.patch.object(server, "log"):
            a.delete({"path": "Delta/s.wav"})
        self.assertFalse(j._tx_in_cloud("Delta/s.wav"))
        self.assertFalse(self.jobs()._tx_in_cloud("Delta/s.wav"))

    def test_otro_audio_con_el_mismo_nombre_no_hereda(self):
        """Igual si los archivos se mueven fuera de la app (en el Explorador): la entrada se queda en la ruta vieja,
        pero lleva la identidad del audio, y el que llega luego a ese nombre es otro."""
        j = self.jobs()
        self.transcribir(j, "nube")
        base = os.path.join(self.root, self.REL[:-4])
        os.makedirs(os.path.join(self.root, "Beta"))
        for e in (".wav", ".srt", ".txt"):
            if os.path.exists(base + e):
                shutil.move(base + e, os.path.join(self.root, "Beta", "r" + e))
        self.wav(self.REL)  # otra reunión, transcrita en local y a medias, con el nombre que tenía A
        self.write(self.REL[:-4] + ".srt", srt_text([(0.0, 1.0, "texto transcrito en local")]))
        self.assertFalse(j._tx_in_cloud(self.REL))
        j.enviados.clear()
        self.transcribir(j, "nube", start=1.0)  # «continuar» en la nube
        self.assertEqual(j.enviados, [])

    def test_caduca_al_consultar(self):
        """Una entrada de hace más de 7 días ya no cuenta aunque no se haya escrito nada después (antes solo se podaba
        al escribir otra)."""
        path = os.path.join(self.data_dir, "transcritas_en_la_nube.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"vieja.wav": time.time() - 8 * 86400, "nueva.wav": time.time() - 6 * 86400}, f)
        reg = server.CloudText(path)
        self.assertNotIn("vieja.wav", reg)
        self.assertIn("nueva.wav", reg)
        self.assertFalse(self.jobs()._tx_in_cloud("vieja.wav"))

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

    def test_casilla_y_lo_que_se_envia(self):
        app = static("app.js")
        for s in ('id="sAutoLocal"', "Resumir también las reuniones transcritas en local (las envía a "
                  "${esc(provName())})", "local: $('#sAutoLocal', d).checked", 'id="sAutoLocalAviso"',
                  "la política de tu empresa y el acuerdo con tu cliente", "transcribir en la nube (reuniones",
                  "De cada reunión se envía a <b>${esc(provName())}</b>: el título (o el nombre del archivo), la fecha "
                  "y la duración, la transcripción (con quién habla, si se sabe), tus notas, el contexto, los "
                  "participantes confirmados y tus instrucciones para el resumen. El audio no."):
            self.assertIn(s, app, s)
        self.assertNotIn("(envía el texto a ${esc(provName())})", app)
        # lo que dice la lista es lo que lleva meta en _run_summary (y nada más)
        with open(server.__file__, encoding="utf-8") as f:
            src = f.read()
        meta = re.search(r"meta = \{(.*?)\}\n", src, re.S).group(1)
        self.assertEqual(re.findall(r'"(\w+)":', meta),
                         ["title", "date", "duration", "participants", "notes", "extra", "about"])


class AvisoResumenLocalTests(TempTree):
    """Tras actualizar, quien tenía el resumen automático con la nube y transcribe en local deja de recibir esos
    resúmenes: la interfaz se lo dice una vez, en Inicio, con un enlace al ajuste."""

    def app(self, saved=None):
        path = os.path.join(self.data_dir, "settings.json")
        if saved is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(saved, f)
        a = server.App.__new__(server.App)
        a.settings = server.Store(path, server.DEFAULT_SETTINGS)
        a.config = lambda q: {}
        with mock.patch.object(server, "log"):
            a._aviso_resumen_local()
        return a

    def aviso(self, a):
        return a.settings.get()["aviso_resumen_local"]

    def test_quien_actualiza_y_le_afecta(self):
        antes = {"cloud": {"api_key": KEY}, "preset": "rapido", "live_preset": "nube"}
        self.assertIs(self.aviso(self.app(antes)), True)
        self.assertIs(self.aviso(self.app()), True)  # ya decidido: el siguiente arranque no lo cambia
        # tambien si la cola y el directo van en la nube pero ya transcribió en local
        os.remove(os.path.join(self.data_dir, "settings.json"))
        nube = {"cloud": {"api_key": KEY}, "preset": "nube", "live_preset": "nube", "speed": {"medium": 1.2}}
        self.assertIs(self.aviso(self.app(nube)), True)

    def test_a_quien_no_le_afecta_no_se_le_avisa(self):
        for saved in ({"preset": "rapido"},  # sin API key: el resumen automático no hacía nada
                      {"cloud": {"api_key": KEY}, "preset": "rapido", "summary_auto": False},
                      {"cloud": {"api_key": KEY}, "preset": "nube", "live_preset": "nube", "speed": {"nube": 9}},
                      {"cloud": {"api_key": KEY}, "preset": "rapido", "summary_auto_local": True}):
            os.path.isfile(os.path.join(self.data_dir, "settings.json")) and os.remove(
                os.path.join(self.data_dir, "settings.json"))
            self.assertIs(self.aviso(self.app(saved)), False, saved)

    def test_instalacion_nueva_sin_aviso(self):
        a = self.app()  # no habia settings.json
        self.assertIs(self.aviso(a), False)
        with open(a.settings.path, encoding="utf-8") as f:
            self.assertIn('"aviso_resumen_local": false', f.read())

    def test_se_cierra_y_no_vuelve(self):
        a = self.app({"cloud": {"api_key": KEY}, "preset": "rapido"})
        a.save_settings({"summary": {"local": False}})  # guardar Ajustes sin marcarla no lo cierra
        self.assertIs(self.aviso(a), True)
        a.save_settings({"summary": {"aviso_visto": True}})
        self.assertIs(self.aviso(a), False)
        a.settings.update(lambda d: d.update(aviso_resumen_local=True))
        a.save_settings({"summary": {"local": True}})  # activarlo tambien lo cierra
        self.assertIs(self.aviso(a), False)
        with open(server.__file__, encoding="utf-8") as f:
            self.assertIn('"aviso_local": bool(st.get("aviso_resumen_local"))', f.read())

    def test_la_interfaz_lo_ensena_en_inicio(self):
        app = static("app.js")
        self.assertIn("${avisoResumenLocalHtml()}\n    <h1>Tus notas de reunión</h1>", app)
        self.assertIn("api('/api/settings', { summary: { aviso_visto: true } })", app)
        self.assertIn("else if (a === 'aviso-sum-ok' || a === 'aviso-sum-ajuste')", app)

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_texto_del_aviso(self):
        src = static("app.js")
        fn = re.search(r"^function avisoResumenLocalHtml\(.*?^}", src, re.S | re.M).group(0)
        code = ("const I = {info: '(i)'}; const esc = s => String(s); const provName = () => 'Groq';\n"
                "let S = {config: {summary: {aviso_local: true}, procesado: 'api'}};\n" + fn + """
const con = avisoResumenLocalHtml();
S.config.procesado = 'agente'; const agente = avisoResumenLocalHtml();  // habla de la API: con el agente no aplica
S.config.procesado = 'api'; S.config.summary.aviso_local = false;
console.log(JSON.stringify([con, avisoResumenLocalHtml(), agente]));
""")
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        con, sin, agente = json.loads(r.stdout)
        self.assertEqual(agente, "")
        self.assertIn("El resumen automático ya no envía a <b>Groq</b> las reuniones transcritas en local. "
                      '<a href="#" data-act="aviso-sum-ajuste">Actívalo en Ajustes</a> si quieres.', con)
        self.assertIn('data-act="aviso-sum-ok">Entendido</button>', con)
        self.assertEqual(sin, "")


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
        # el origen va detras de los nombres, no en una clave propia: las versiones publicadas solo aceptan las claves
        # que conocen (fix-perfil; tests/test_integracion.py). La de setUp, con la clave, se sigue leyendo
        self.assertEqual(server.dump_notes({"participantes": "Ana", "participantes_origen": "outlook"}, "x"),
                         "---\nparticipantes: Ana (sugeridos por Outlook, sin confirmar)\n---\n\nx\n")
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
        # el asunto de la cita sí sale (como nombre de la grabación y título del resumen y de los destinos)
        self.assertNotIn("no se envía a ningún sitio", js)
        self.assertIn("El nombre que se sugiere al grabar (el asunto de la cita o el título de la reunión de Teams), "
                      "si lo aceptas, es el nombre de la grabación: va como título al resumen con IA y a los destinos "
                      "de salida.", js)
        self.assertNotIn("(irán a las notas de la reunión)`", js)
        for s in ("como participantes <b>sugeridos</b>: no van al resumen con IA ni a los destinos de salida hasta "
                  "que los confirmes o los edites", "function reunionParticipantesHtml(a)",
                  "function reunionParticipantesGuardados(a)", "data-rn-confirmar", "saveProps(a, { participantes:"):
            self.assertIn(s, js, s)
        # al grabar, la tarjeta (grabar.js, 07/11/2026) lo dice con los asistentes de la cita, y deja quitarlos
        self.assertIn("Irán a las notas de la reunión como sugeridos: no se envían al resumen ni a los destinos hasta "
                      "que los confirmes.", static("grabar.js"))
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
    """Lista blanca: del registro solo sale lo fijo de cada mensaje de la app, con sus cifras, modelos y códigos de
    error; lo demás va como <texto>. Un server.log como el de verdad, con títulos, rutas y nombres, también de lo que
    la app ya no conoce (importado y borrado, tema a la papelera, carpeta desvinculada, persona olvidada)."""

    U = "C:\\Users\\bob.smith\\OneDrive - Initech\\Chuleta"
    LOG = [  # (linea de server.log, lo que tiene que quedar)
        ("I ⤓ importado: Clientes Omega/Entrevista CEO Omega.m4a", "I ⤓ importado: <texto>"),
        ("I ▶ Entrevista CEO Omega.m4a  [Nube]", "I ▶ <texto>  [Nube]"),
        ("I ✔ Entrevista CEO Omega.m4a  3.2 min  (18.5x tiempo real)",
         "I ✔ <texto>  3.2 min  (18.5x tiempo real)"),
        ("I 🗑 Clientes Omega/Entrevista CEO Omega.m4a → papelera (3 archivos)", "I 🗑 <texto> → papelera (3 archivos)"),
        ("I 🗑 vocabulario a la papelera: Proyecto Delta · Due Diligence Omega",
         "I 🗑 vocabulario a la papelera: <texto>"),
        ("I ● Grabando: Clientes Omega/2026-10-29 09-00-00 - Comité Fusión Omega.wav [Proyecto Delta · Due Diligence "
         "Omega]", "I ● Grabando: <texto> [<texto> · <texto>]"),
        ("I 🔗 carpeta desvinculada (no se ha tocado nada): D:\\Clientes\\Banco Omega",
         "I 🔗 carpeta desvinculada (no se ha tocado nada): <texto>"),
        ("I 🔤 vocabulario importado de D:\\Backup\\Banco Omega: 3 listas copiadas, 1 omitidas (Proyecto Delta, "
         "Proyecto Kappa)", "I 🔤 vocabulario importado de <texto>: 3 listas copiadas, 1 omitidas (<texto>)"),
        ("I ✔ Comité Fusión Omega  resumen listo (12 s, openai/gpt-oss-120b)",
         "I ✔ <texto>  resumen listo (12 s, openai/gpt-oss-120b)"),
        ("I ↪ Clientes Omega/Junta Omega.m4a  →  Clientes Omega/Despido Gamma.m4a", "I ↪ <texto>  →  <texto>"),
        ("I 🗣 huella guardada: Marta Olvidada (3 huellas)", "I 🗣 huella guardada: <texto> (3 huellas)"),
        (f"I arranque · http://127.0.0.1:8765/ · carpeta {U} · datos C:\\Users\\bob.smith\\AppData\\Local\\AudiosApp "
         "· Python 3.13.1", "I arranque · http://127.0.0.1:8765/ · carpeta <texto> · datos <texto> · Python 3.13.1"),
        ("I ✖ 2026-10-30 09-00-00 - Reunión con Bravo.wav  HTTP 401: Invalid API Key provided: gsk_abc",
         "I ✖ <texto>  <texto> (HTTP 401)"),
        ("I ✖ Comité Fusión Omega  resumen: HTTP 429: Rate limit reached in organization `org_omega` "
         "(rate_limit_exceeded)", "I ✖ <texto>  resumen: <texto> (HTTP 429, rate_limit_exceeded)"),
        ("E Traceback (most recent call last):", "E Traceback (most recent call last):"),
        (f'  File "{U}\\_app\\server.py", line 2100, in _run', '  File "server.py", line 2100, in _run'),
        ("    segs, lang, dur = self.local.transcribe(abs_path, preset, job.language, vocab,", "    <texto>"),
        ("PermissionError: [WinError 32] El proceso no tiene acceso al archivo: "
         f"'{U}\\Clientes Omega\\Junta Omega.m4a'", "PermissionError: <texto> ([WinError 32])"),
        ("I    reunión detectada (Teams): aviso flotante abierto",
         "I    reunión detectada (Teams): aviso flotante abierto"),
        ("I    2026-10-31 10-10-10 - Comite ACME: documento escrito en 1 destino(s) · sin tocar: Comite ACME.md: "
         "cambiado a mano", "I    <texto>: documento escrito en 1 destino(s) · sin tocar: <texto>"),
        ("I    perfil de reunión guardado: idioma, workstream", "I    perfil de reunión guardado: idioma, workstream"),
        ("I modelo small precargado para la cola en 2.1 s", "I modelo small precargado para la cola en 2.1 s"),
        ("I    directo Junta Omega.wav: modelo nube", "I    directo <texto>: modelo nube"),
        ("I ✔ Junta Omega.wav  voces: 3 personas · 1 reconocida (40 s)",
         "I ✔ <texto>  voces: 3 personas · 1 reconocida (40 s)"),
        ("I    Junta Omega.wav: transcrita en local, sin resumen automático (no sale del PC; se puede resumir a mano)",
         "I    <texto>: transcrita en local, sin resumen automático (no sale del PC; se puede resumir a mano)"),
        # lo que no es de la app (o que la app escribe sin plantilla): <texto>, con el icono si lo hay
        ("I aviso de ana.perez@contoso.com y Eva Sanz con Iker Mora", "I <texto>"),
        ("I \\\\fileserver01\\finanzas\\informe Omega.docx", "I <texto>"),
        ("I ventana http://127.0.0.1:8765/?t=AbCdEfGh0123456789_-xyzXYZ · X-Chuleta-Token: QwErTy0123456789abcdEF",
         "I <texto>"),
        (f"I petición con {KEY}", "I <texto>"),
        ("I 🔗 Proyecto Faro (OneDrive - Contoso SA)", "I 🔗 <texto>"),
    ]
    SECRETOS = ("Omega", "Delta", "Kappa", "Due Diligence", "Entrevista", "CEO", "Comité", "Fusión", "Junta", "Despido",
                "Gamma", "Bravo", "Marta", "Olvidada", "bob.smith", "Initech", "Banco", "Clientes", "Backup", "ACME",
                "Comite", "Contoso", "ana.perez", "Eva Sanz", "Iker Mora", "fileserver01", "finanzas", "informe",
                "org_omega", "Faro", "Nebula", KEY, "AbCdEfGh0123456789", "QwErTy0123456789", "C:\\Users", "D:\\",
                "gsk_abc", "Invalid API Key", "El proceso no tiene acceso")

    def setUp(self):
        super().setUp()
        self.assertEqual(len(self.LOG), 30)  # diagnostico lee las 30 ultimas
        # una biblioteca con nombres que coinciden con las etiquetas del propio diagnostico (P1-06, punto 3)
        self.wav("Datos/2026-10-31 10-10-10 - Python.wav")
        self.write("Datos/2026-10-31 10-10-10 - Python.md",
                   "---\ntitulo: Chuleta\netiquetas: [Datos, Nube]\nproyecto: Python\nworkstream: Transcripción\n---\n")
        link = os.path.join(self.tmp, "OneDrive - Contoso SA", "Proyecto Faro")
        os.makedirs(link)
        server.LINKS["l1"] = {"name": "Faro", "path": link}
        self.addCleanup(server.LINKS.pop, "l1", None)
        self.app = server.App.__new__(server.App)
        self.app.settings = settings_store(self.tmp, cloud=dict(CLOUD, model="whisper-large-v3-turbo"), preset="nube",
                                           perfil={"outputs": [{"path": os.path.join(self.tmp, "Vault Nebula"),
                                                                "format": "md", "by_project": False}]})
        with open(os.path.join(self.data_dir, "server.log"), "w", encoding="utf-8") as f:
            for i, (line, _) in enumerate(self.LOG):
                f.write((line if line.startswith(" ") or not line[1:2] == " " else
                         f"2026-10-31 10:00:{i:02d} {line}") + "\n")

    def texto(self):
        with mock.patch.object(server, "log"):
            return self.app.diagnostico({})["texto"]

    def test_un_registro_real_sin_nombres_ni_rutas(self):
        t = self.texto()
        for s in self.SECRETOS + (self.root, self.data_dir, self.tmp):
            self.assertNotIn(s, t, s)

    def test_lo_tecnico_se_queda(self):
        t = self.texto()
        lines = t.split("\n")
        app = server.APP_NAME
        self.assertEqual(lines[:9], [
            f"{app} · versión {server.VERSION}", f"Python {server.sys.version.split()[0]} · "
            f"{server.platform.platform()}", "Dónde se procesa: API externa",
            "Transcripción: nube (groq) · modelo whisper-large-v3-turbo · directo: medium",
            f"Carpeta de {app}: en OneDrive: no · en una unidad de red: no · con tildes u otros caracteres no ASCII: "
            f"no · {len(server.ROOT)} caracteres",
            "Datos: en otra carpeta", "Carpetas vinculadas: 1 · destinos de salida: 1", "",
            "server.log (últimas 30 líneas; lo que no es técnico va como <texto>):"])
        salida = [l.split(" ", 2)[2] if l[:4].isdigit() else l for l in lines[9:]]  # sin la fecha y la hora
        self.assertEqual(salida, [esperado for _, esperado in self.LOG])
        self.assertTrue(all(l.startswith("2026-10-31 10:00:") for l, (orig, _) in zip(lines[9:], self.LOG)
                            if orig[1:2] == " " and not orig.startswith(" ")))

    def test_ningun_hueco_deja_pasar_texto(self):
        """Cada plantilla con un dato confidencial en cada hueco (con la forma del hueco, pero sin ser técnico): no
        sale nada de él y el texto fijo de la plantilla se queda tal cual."""
        dato = {"n": "12", "c": "OmegaKwyjibo", "y": "C:\\Users\\omega\\Kwyjibo Omega.txt",
                "u": "http://omega.kwyjibo:8765/", "v": "omega.kwyjibo"}
        for tpl in server.DIAG_LINES:
            for opcional in (True, False):
                base = re.sub(r"⟦(.*?)⟧", r"\1" if opcional else "", tpl)
                linea = re.sub(r"\{([a-z])\}", lambda m: dato.get(m.group(1), "Omega Kwyjibo · C:\\Users\\omega"),
                               base)
                self.assertIsNotNone(server._diag_rx(tpl).fullmatch(linea), tpl)
                out = server.diag_line(linea)
                self.assertNotRegex(out, r"(?i)omega|kwyjibo|users", (tpl, out))
                self.assertEqual(out, re.sub(r"\{([a-z])\}", lambda m: "12" if m.group(1) == "n" else "<texto>",
                                             base), tpl)  # ninguna plantilla anterior se la come

    def test_sin_plantilla(self):
        f = server.diag_line
        self.assertEqual(f("2026-10-31 10:00:00 I 🔗 Proyecto Faro"), "2026-10-31 10:00:00 I 🔗 <texto>")
        self.assertEqual(f("   Reunión Omega: algo nuevo [WinError 5] y HTTP 503 y otra vez HTTP 503"),
                         "   <texto> ([WinError 5], HTTP 503)")
        self.assertEqual(f("2026-10-31 10:00:00 I ●"), "2026-10-31 10:00:00 I ●")
        self.assertEqual((f(""), f("Omega"), f("Omega: [Errno 13] x")),
                         ("", "<texto>", "<texto>: <texto> ([Errno 13])"))
        # el recuadro del arranque (main) y lo que escribe common.py fuera de Windows
        app = server.APP_NAME
        apagar = f"  Apagar: Ajustes > Apagar {app} (con consola, también cerrándola o Ctrl+C)."
        dpapi = "   sin DPAPI fuera de Windows: los secretos se guardan en claro (plain:base64)"
        self.assertEqual([f(l) for l in ("=" * 64, f"  {app.upper()}  ·  http://127.0.0.1:8765/",
                                         "  Carpeta: C:\\Users\\omega\\Chuleta", apagar, dpapi)],
                         ["=" * 64, f"  {app.upper()}  ·  http://127.0.0.1:8765/", "  Carpeta: <texto>", apagar, dpapi])
        self.assertEqual(server.diag_codes("ConnectionResetError, 0x80040154 y -2147221005"),
                         " (ConnectionResetError, 0x80040154, -2147221005)")

    def test_codigos_de_una_lista_cerrada(self):
        """De un texto libre solo salen las excepciones que existen (las de Python, las de la app y las de las
        librerías), no cualquier palabra con su forma: un trozo de un nombre de carpeta o de archivo no sale."""
        f, h = server.diag_line, "2026-10-31 10:00:00 E "
        self.assertEqual(f(h + "✖ Junta Omega.m4a  PermissionError: [Errno 13] 'D:\\Clientes\\EarlyWarning Plan\\"
                           "SmartExit.m4a'"), h + "✖ <texto>  <texto> (PermissionError, [Errno 13])")
        self.assertEqual(f(h + "✖ Junta Omega  resumen: no se pudo con OmegaTimeout ni KrakenError: HTTP 500"),
                         h + "✖ <texto>  resumen: <texto> (HTTP 500)")
        self.assertEqual(f("Clientes_Omega.FusionError: algo"), "<texto>: <texto>")
        self.assertEqual(f("Omega.Kraken.DealError"), "<texto>")
        self.assertEqual(f("engines.EngineError: No se pudo leer el audio de Omega"), "engines.EngineError: <texto>")
        self.assertEqual(f("requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.groq.com', port=443)"),
                         "requests.exceptions.ReadTimeout: <texto>")
        self.assertEqual(server.diag_codes("error y Exception · sin conexión (ConnectionError) · WorkerTimeout · "
                                           "Omega.PermissionError · KeyboardInterrupt · SystemExit"),
                         " (ConnectionError, WorkerTimeout, PermissionError, KeyboardInterrupt)")

    def test_lineas_largas_sin_atascarse(self):
        """Una línea enorme en server.log (el HTML de un error, o una rara) no bloquea «Copiar diagnóstico»: con
        varios huecos de texto libre, ver si encajaba en su plantilla costaba segundos (21 s con 8 KB)."""
        h = "2026-10-31 10:00:00 I "
        lineas = [h + "● Grabando: " + "a [b · c] " * n for n in (190, 800)]
        lineas += [h + "arranque · " + " · ".join(["carpeta x · datos y"] * n) for n in (90, 400)]
        self.assertLess(len(lineas[0]) - len(h), server.DIAG_MAX)  # esta, por debajo del tope: con su plantilla
        t = time.perf_counter()
        out = [server.diag_line(l) for l in lineas]
        self.assertLess(time.perf_counter() - t, 0.5)
        self.assertEqual(out, [h + "● Grabando: <texto>", h + "● <texto>", h + "<texto>", h + "<texto>"])
        largo = h + "● Grabando: Clientes/" + "x" * 1500 + ".wav [Proyecto · Tema]"
        self.assertEqual(server.diag_line(largo), h + "● Grabando: <texto> [<texto> · <texto>]")

    def test_motor_y_modelo_propios_no_salen(self):
        """Una pasarela de empresa (Azure, otra compatible) puede llevar el nombre del cliente en el modelo."""
        self.app.settings.update(lambda d: d["cloud"].update(provider="azure", model="omega-whisper-prod"))
        self.assertIn("Transcripción: nube (azure) · modelo <texto> · directo: medium", self.texto())
        self.app.settings.update(lambda d: d.update(preset="rapido", live_preset="omega"))
        self.assertIn("Transcripción: local (rapido) · modelo small · directo: <texto>", self.texto())

    def test_aviso_junto_al_boton(self):
        app = static("app.js")
        i = app.index('id="aDiag"')
        cerca = app[i:i + 1400]
        self.assertIn("Solo lleva datos técnicos", cerca)
        self.assertIn("van como &lt;texto&gt;, y las claves no van", cerca)
        self.assertIn("El registro completo (<code>server.log</code>) sí lleva nombres y rutas: no lo compartas tal "
                      "cual", cerca)
        self.assertIn("no lo publiques en un sitio público", cerca)
        self.assertNotIn("cambiados por marcadores", app)
        self.assertIn("'Diagnóstico copiado: revísalo antes de pegarlo donde pidas ayuda'", app)
        self.assertFalse(hasattr(server, "pseudonymize"))  # nada de lista negra: lo que no esta en la lista, no sale


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

    def test_se_rescatan_todos_los_secretos(self):
        """Un secreto que se añada a SECRETS (en fix-directo, las claves adicionales de cada proveedor) también se
        rescata y se cuenta: el rescate lleva todos los SECRETS, no una lista aparte que haya que ampliar a mano."""
        class ConOtro(server.Store):
            SECRETS = server.Store.SECRETS + (("cloud", "extra_keys_otro"),)
        otras = ["sk-" + "o" * 24, "sk-" + "p" * 24]
        saved = json.loads(json.dumps(self.saved))
        saved["cloud"]["extra_keys_otro"] = [server.protect(k.encode()) for k in otras]
        self.write(json.dumps(dict(saved, zzz="x" * 50), ensure_ascii=False)[:-20])  # cortado a mitad
        with mock.patch.object(server, "log"):
            st = ConOtro(self.path, server.DEFAULT_SETTINGS)
        self.assertEqual(st.get()["cloud"].get("extra_keys_otro"), otras)
        self.assertEqual(st.broken["recuperado"], ["la API key", "3 claves adicionales", "1 carpeta vinculada",
                                                   "1 destino de salida"])

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
