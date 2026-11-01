# -*- coding: utf-8 -*-
"""Arreglos de la auditoría final del 31/10/2026, grupo «perfil»: perfil de reunión, temas de vocabulario y destinos
de salida. Cada clase dice qué hallazgo cubre (P1-10, P1-11/P2-01, P2-02…). Con dobles y carpetas temporales: sin red
y sin Windows. Los de la interfaz se ejecutan con node si está instalado."""
import json
import os
import re
import shutil
import subprocess
import threading
import types
import unittest
import zipfile
from unittest import mock

from support import REPO, TempTree, bare_jobs, engines, server, srt_text, wav_bytes  # noqa: F401  (prepara sys.path)
import salidas  # noqa: E402
from test_frontend import js_defs, read  # noqa: E402

STEM = "2026-10-31 10-00-00 - Semanal"


def settings_store(tmp, **extra):
    st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
    if extra:
        st.update(lambda d: d.update(extra))
    return st


def doc(**kw):
    d = {"stem": STEM, "title": "Semanal", "date": "2026-10-31 10:00", "duration": "45 min",
         "props": {"proyecto": "Alfa"}, "source": "Alfa/" + STEM + ".wav", "summary": "", "summary_model": None,
         "notes": "notas", "app_name": "Chuleta", "segments": [{"start": 0.0, "end": 2.0, "text": "Hola."}]}
    d.update(kw)
    return d


def jobs_with(tmp, data_dir, outputs, library=None):
    """Un Jobs sin hilos con ajustes (destinos de salida) y una biblioteca de mentira."""
    j = bare_jobs([])
    j.settings = settings_store(tmp, perfil={"outputs": outputs})
    j.library = library or types.SimpleNamespace(find=lambda rel: {"date": "2026-10-31T10:00:00", "duration": 60.0},
                                                 at=1)
    j.voice_vectors = {}
    return j


def meeting(t, rel, notes="notas"):
    """Una reunion con su transcripcion y sus notas en el ROOT temporal de `t` (un TempTree)."""
    t.wav(rel)
    base = os.path.splitext(rel)[0]
    t.write(base + ".srt", srt_text([(0.0, 2.0, "Hola a todos.")]))
    t.write(base + ".md", f"---\ntitulo: Semanal\n---\n\n{notes}\n")


# ----------------------------------------------------------------------------- P1-10

class IdiomaDelPerfilTests(TempTree):
    """P1-10: transcribir una reunión con otro idioma no cambia el idioma por defecto del perfil."""

    def test_transcribir_no_cambia_el_idioma_del_perfil(self):
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, language="es", preset="rapido")
        seen = []
        a.jobs = types.SimpleNamespace(
            submit=lambda paths, preset, language, resume=False: seen.append((paths, preset, language)) or ["j1"])
        self.assertEqual(a.transcribe({"paths": ["x.wav"], "preset": "nube", "language": "pt"}), {"added": ["j1"]})
        st = a.settings.get()
        self.assertEqual(st["language"], "es")  # el del perfil sigue igual (antes pasaba a "pt")
        self.assertEqual(st["preset"], "nube")  # el motor si se recuerda
        self.assertEqual(seen, [(["x.wav"], "nube", "pt")])
        a.transcribe({"paths": ["x.wav"], "preset": "rapido", "language": ""})  # «Detectar» tampoco lo cambia
        self.assertEqual(a.settings.get()["language"], "es")


# ----------------------------------------------------------------------------- P1-11 / P2-01

class DestinosEnLaAppTests(TempTree):
    """P1-11 (y P2-01): un destino no puede ser una carpeta de la app ni tener grabaciones, y nunca se escribe encima
    del sidecar de una grabación aunque la validación falle."""

    def setUp(self):
        super().setUp()
        self.ext = os.path.join(self.tmp, "OneDrive", "Proyecto")  # vinculada
        os.makedirs(os.path.join(self.ext, "Docs"))
        server.LINKS[server.link_id(self.ext)] = {"name": "Proyecto", "path": self.ext}
        os.makedirs(os.path.join(self.root, "Alfa", "Docs"))
        self.fuera = os.path.join(self.tmp, "Vault")
        os.makedirs(self.fuera)

    def test_check_perfil_rechaza_la_app_las_vinculadas_y_las_carpetas_con_grabaciones(self):
        for bad in (self.root, os.path.join(self.root, "Alfa"), os.path.join(self.root, "Alfa", "Docs"), self.ext,
                    os.path.join(self.ext, "Docs")):
            with self.assertRaises(server.ApiError, msg=bad) as cm:
                server.check_perfil({"outputs": [{"path": bad}]})
            self.assertIn(server.APP_NAME, cm.exception.msg)
            self.assertIn("grabaciones", cm.exception.msg)
        obs = os.path.join(self.tmp, "OBS")
        os.makedirs(obs)
        with open(os.path.join(obs, STEM + ".mkv"), "wb") as f:
            f.write(b"x")
        with self.assertRaises(server.ApiError) as cm:
            server.check_perfil({"outputs": [{"path": obs}]})
        self.assertIn("tiene grabaciones", cm.exception.msg)
        self.assertIn(STEM + ".mkv", cm.exception.msg)
        ok = server.check_perfil({"outputs": [{"path": self.fuera, "format": "docx"}]})
        self.assertEqual(ok["outputs"], [{"path": self.fuera, "format": "docx", "by_project": False}])
        # una carpeta que solo empieza igual que _app no es la de la app
        hermana = self.app_dir + "2"
        os.makedirs(hermana)
        self.assertEqual(salidas.check_destinos([{"path": hermana}], self.app_dir)[0]["path"], hermana)

    def test_nunca_se_escribe_encima_de_un_sidecar(self):
        """Validación saltada (un destino guardado con una versión anterior): ni las notas ni el .txt de la grabación
        se tocan, y a un audio sin notas (OBS, importado) no se le crean."""
        reg = salidas.Registro(os.path.join(self.data_dir, "salidas.json"))
        d = os.path.join(self.tmp, "Grabaciones")
        os.makedirs(d)
        with open(os.path.join(d, STEM + ".wav"), "wb") as f:
            f.write(b"RIFF")
        notas = os.path.join(d, STEM + ".md")
        with open(notas, "w", encoding="utf-8") as f:
            f.write("---\ntitulo: mis notas\n---\n\nnotas de verdad\n")
        for fmt in ("md", "txt"):
            done = salidas.write_all(doc(), [{"path": d, "format": fmt}], reg)
            self.assertEqual(done[0], (os.path.join(d, STEM + "." + fmt), "junto-al-audio"), fmt)
        with open(notas, encoding="utf-8") as f:
            self.assertIn("notas de verdad", f.read())
        self.assertFalse(os.path.exists(os.path.join(d, STEM + ".txt")))
        self.assertEqual(salidas.write_all(doc(), [{"path": d, "format": "docx"}], reg)[0][1], "escrito")  # no choca
        self.assertEqual(salidas.sidecar_of(os.path.join(d, STEM + ".resumen.md")), os.path.join(d, STEM + ".wav"))
        self.assertIsNone(salidas.sidecar_of(os.path.join(d, "otro.md")))

    def test_las_listas_de_extensiones_siguen_a_las_del_servidor(self):
        self.assertEqual(set(salidas.AUDIO_EXT), server.AUDIO_EXT)
        self.assertTrue(set(server.SIDE_EXTS) <= set(salidas.SIDECAR_TAILS))

    def test_un_destino_guardado_dentro_de_la_app_no_se_escribe_y_se_avisa(self):
        meeting(self, "Alfa/" + STEM + ".wav")
        dentro = os.path.join(self.root, "Alfa", "Docs")
        j = jobs_with(self.tmp, self.data_dir, [{"path": dentro, "format": "md"}, {"path": self.fuera, "format": "md"}])
        with mock.patch.object(server, "log"):
            done = j.write_outputs("Alfa/" + STEM + ".wav")
        self.assertEqual([k for _, k in done], ["dentro-de-la-app", "escrito"])
        self.assertEqual(os.listdir(dentro), [])
        self.assertTrue(os.path.isfile(os.path.join(self.fuera, STEM + ".md")))
        avisos = j.outputs_warnings()
        self.assertEqual([(a["accion"], a["reunion"]) for a in avisos], [("dentro-de-la-app", "Alfa/" + STEM + ".wav")])

    def test_comprobar_un_destino_sin_guardar(self):
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp)
        r = a.perfil_save({"comprobar": True, "outputs": [{"path": self.fuera + os.sep, "format": "md"}]})
        self.assertEqual(r, {"outputs": [{"path": self.fuera, "format": "md", "by_project": False}]})
        self.assertEqual(a.settings.get()["perfil"]["outputs"], [])  # no se ha guardado nada
        with self.assertRaises(server.ApiError):
            a.perfil_save({"comprobar": True, "outputs": [{"path": self.ext}]})
        js = read("perfil.js")
        self.assertIn("api('/api/perfil', { comprobar: true, outputs: [{ path, format: 'md' }] })", js)


# ----------------------------------------------------------------------------- P2-02

class SinRegistroTests(TempTree):
    """P2-02: sin entrada en salidas.json (otro PC, reinstalación, JSON perdido), un documento con la marca es
    «cambiado» salvo que diga lo mismo que lo que se va a escribir."""

    def setUp(self):
        super().setUp()
        self.d = os.path.join(self.tmp, "vault")
        os.makedirs(self.d)

    def reg(self, name):
        return salidas.Registro(os.path.join(self.data_dir, name))

    def test_marcado_y_editado_no_se_pisa(self):
        x = os.path.join(self.d, STEM + ".md")
        with open(x, "wb") as f:
            f.write(salidas.render(doc(), "md") + "editado a mano en el vault\n".encode("utf-8"))
        self.assertEqual(salidas.write_all(doc(notes="nuevas"), [{"path": self.d, "format": "md"}], self.reg("a.json"))
                         [0][1], "cambiado")
        with open(x, encoding="utf-8") as f:
            self.assertIn("editado a mano en el vault", f.read())

    def test_lo_mismo_salvo_la_fecha_de_generacion_se_adopta(self):
        for fmt in salidas.FORMATS:
            x = os.path.join(self.d, f"{STEM}.{fmt}")
            with open(x, "wb") as f:
                f.write(salidas.render(doc(generated="2020-01-01 00:00"), fmt))
            if fmt == "docx":  # otras fechas en docProps/core.xml
                with zipfile.ZipFile(x) as z:
                    self.assertIn(b"<dcterms:created", z.read("docProps/core.xml"))
            reg = self.reg(fmt + ".json")
            with mock.patch.object(salidas.time, "strftime", return_value="2030-12-31T23:59:59Z"):
                done = salidas.write_all(doc(), [{"path": self.d, "format": fmt}], reg)
            self.assertEqual(done[0][1], "igual", fmt)
            self.assertEqual(reg.get(x), salidas.sha256_of(x))  # queda registrado: a partir de ahi se actualiza
            self.assertEqual(salidas.write_all(doc(notes="otras"), [{"path": self.d, "format": fmt}], reg)[0][1],
                             "escrito", fmt)


# ----------------------------------------------------------------------------- P2-03

class NombresUnicosTests(TempTree):
    """P2-03: dos reuniones con el mismo nombre de fichero no se pisan el documento en el destino."""

    def setUp(self):
        super().setUp()
        self.d = os.path.join(self.tmp, "vault")
        os.makedirs(self.d)
        self.reg = salidas.Registro(os.path.join(self.data_dir, "salidas.json"))
        self.dest = [{"path": self.d, "format": "md"}]

    def test_dos_reuniones_con_el_mismo_nombre(self):
        a = doc(stem="Kickoff", source="Alfa/Kickoff.m4a", notes="las de Alfa")
        b = doc(stem="Kickoff", source="Beta/Kickoff.m4a", notes="las de Beta")
        pa = salidas.write_all(a, self.dest, self.reg)[0][0]
        pb = salidas.write_all(b, self.dest, self.reg)[0][0]
        self.assertEqual(pa, os.path.join(self.d, "Kickoff.md"))
        self.assertEqual(pb, salidas.alt_name(self.d, "Kickoff", "Beta/Kickoff.m4a", "md"))
        self.assertRegex(os.path.basename(pb), r"^Kickoff \([0-9a-f]{6}\)\.md$")
        # cada una sigue escribiendo en el suyo
        self.assertEqual(salidas.write_all(dict(a, notes="Alfa 2"), self.dest, self.reg)[0], (pa, "escrito"))
        self.assertEqual(salidas.write_all(dict(b, notes="Beta 2"), self.dest, self.reg)[0], (pb, "escrito"))
        for p, txt in ((pa, "Alfa 2"), (pb, "Beta 2")):
            with open(p, encoding="utf-8") as f:
                self.assertIn(txt, f.read())
        self.assertEqual(self.reg.rel_of(pa), "Alfa/Kickoff.m4a")
        # sin registro (otro PC, reinstalacion) cada una sigue encontrando el suyo, tambien en .docx
        reg2 = salidas.Registro(os.path.join(self.data_dir, "otro.json"))
        self.assertEqual(salidas.write_all(dict(b, notes="Beta 2"), self.dest, reg2)[0], (pb, "igual"))
        self.assertEqual(salidas.write_all(dict(a, notes="Alfa 2"), self.dest, reg2)[0], (pa, "igual"))
        dx = [{"path": self.d, "format": "docx"}]
        qa, qb = (salidas.write_all(m, dx, self.reg)[0][0] for m in (a, b))
        self.assertNotEqual(qa, qb)
        reg3 = salidas.Registro(os.path.join(self.data_dir, "tercero.json"))
        self.assertEqual(salidas.write_all(b, dx, reg3)[0], (qb, "igual"))
        # aunque el de Alfa desaparezca, Beta se queda con su nombre (estable)
        os.remove(pa)
        self.assertEqual(salidas.write_all(dict(b, notes="Beta 3"), self.dest, self.reg)[0], (pb, "escrito"))

    def test_sin_registro_lo_dice_el_propio_documento(self):
        """Un documento de otra reunión que no consta en este registro (otro PC): su `origen:` lo delata."""
        with open(os.path.join(self.d, "Kickoff.md"), "wb") as f:
            f.write(salidas.render(doc(stem="Kickoff", source="Otra/Kickoff.m4a"), "md"))
        b = doc(stem="Kickoff", source="Beta/Kickoff.m4a")
        self.assertEqual(salidas.origin_of(os.path.join(self.d, "Kickoff.md"), "md"), "Otra/Kickoff.m4a")
        done = salidas.write_all(b, self.dest, self.reg)
        self.assertEqual(done[0], (salidas.alt_name(self.d, "Kickoff", "Beta/Kickoff.m4a", "md"), "escrito"))
        txt = os.path.join(self.d, "Kickoff.txt")
        with open(txt, "wb") as f:
            f.write(salidas.render(doc(stem="Kickoff", source="Otra/Kickoff.m4a"), "txt"))
        self.assertEqual(salidas.origin_of(txt, "txt"), "Otra/Kickoff.m4a")

    def test_registro_antiguo(self):
        p = os.path.join(self.data_dir, "viejo.json")
        x = os.path.join(self.d, STEM + ".md")
        with open(x, "wb") as f:
            f.write(salidas.render(doc(), "md"))
        with open(p, "w", encoding="utf-8") as f:
            json.dump({os.path.normcase(x): salidas.sha256_of(x)}, f)  # formato de antes: destino -> sha
        reg = salidas.Registro(p)
        self.assertEqual(reg.get(x), salidas.sha256_of(x))
        self.assertEqual(salidas.write_all(doc(notes="n2"), self.dest, reg)[0], (x, "escrito"))
        self.assertEqual(reg.rel_of(x), doc()["source"])
        self.assertEqual(reg.moved("Alfa", "Gamma", folder=True), 1)
        self.assertEqual(reg.rel_of(x), "Gamma/" + STEM + ".wav")

    def old_registry(self, fmt, notes):
        """El documento de Alfa ya escrito y un registro del formato de antes (destino -> sha, sin la reunión)."""
        x = os.path.join(self.d, "Kickoff." + fmt)
        with open(x, "wb") as f:
            f.write(salidas.render(doc(stem="Kickoff", source="Alfa/Kickoff.m4a", notes=notes), fmt))
        p = os.path.join(self.data_dir, f"viejo-{fmt}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({os.path.normcase(x): salidas.sha256_of(x)}, f)
        return x, salidas.Registro(p)

    def body(self, path, fmt):
        if fmt == "docx":
            with zipfile.ZipFile(path) as z:
                return z.read("word/document.xml").decode("utf-8")
        with open(path, encoding="utf-8-sig") as f:
            return f.read()

    def test_registro_antiguo_y_otra_reunion_con_el_mismo_nombre(self):
        """Con el registro de antes, la segunda reunión con el mismo nombre no escribe encima del documento de la
        primera: el propio documento dice de qué reunión es, también el .docx (antes solo el .md y el .txt)."""
        for fmt in ("md", "txt", "docx"):
            x, reg = self.old_registry(fmt, "NOTAS_ALFA")
            b = doc(stem="Kickoff", source="Beta/Kickoff.m4a", notes="NOTAS_BETA")
            done = salidas.write_all(b, [{"path": self.d, "format": fmt}], reg)
            self.assertEqual(done, [(salidas.alt_name(self.d, "Kickoff", "Beta/Kickoff.m4a", fmt), "escrito")], fmt)
            self.assertIn("NOTAS_ALFA", self.body(x, fmt), fmt)
            self.assertNotIn("NOTAS_BETA", self.body(x, fmt), fmt)
            # y la propia Alfa sigue escribiendo en el suyo
            a = doc(stem="Kickoff", source="Alfa/Kickoff.m4a", notes="ALFA_2")
            self.assertEqual(salidas.write_all(a, [{"path": self.d, "format": fmt}], reg), [(x, "escrito")], fmt)

    def test_sin_registro_un_docx_de_otra_reunion(self):
        """Sin registro (otro PC) y con el .docx de otra reunión en el destino, el documento va a su nombre propio en
        vez de quedarse sin escribir («cambiado»)."""
        x = os.path.join(self.d, "Kickoff.docx")
        with open(x, "wb") as f:
            f.write(salidas.render(doc(stem="Kickoff", source="Otra/Kickoff · 2.m4a", notes="de la otra"), "docx"))
        self.assertEqual(salidas.origin_of(x, "docx"), "Otra/Kickoff · 2.m4a")  # la ruta entera, con « · »
        b = doc(stem="Kickoff", source="Beta/Kickoff.m4a")
        done = salidas.write_all(b, [{"path": self.d, "format": "docx"}], self.reg)
        self.assertEqual(done, [(salidas.alt_name(self.d, "Kickoff", "Beta/Kickoff.m4a", "docx"), "escrito")])
        self.assertIn("de la otra", self.body(x, "docx"))
        # guardado otra vez por Word (atributos en cada párrafo y el texto en varios trozos): se sigue leyendo
        with zipfile.ZipFile(x) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        xml = parts["word/document.xml"].decode("utf-8").replace("<w:p>", '<w:p w:rsidR="00A1B2C3">')
        xml = xml.replace("Origen: Otra/", 'Ori</w:t></w:r><w:r><w:t xml:space="preserve">gen: Otra/', 1)
        parts["word/document.xml"] = xml.encode("utf-8")
        with zipfile.ZipFile(x, "w") as z:
            for n, data in parts.items():
                z.writestr(n, data)
        self.assertEqual(salidas.origin_of(x, "docx"), "Otra/Kickoff · 2.m4a")
        self.assertIsNone(salidas.origin_of(os.path.join(self.d, "no-existe.docx"), "docx"))

    def test_mover_una_reunion_que_ya_tiene_nombre_propio(self):
        """La reunión que escribe «Kickoff (<hash>)» porque «Kickoff» es de otra sigue escribiendo en ese mismo
        documento al moverla o al renombrar su carpeta, aunque el hash, que sale de la ruta, cambie. También sin el
        registro (otro PC, reinstalación): antes escribía otro «Kickoff (<hash nuevo>)» al lado."""
        for fmt in ("md", "docx"):
            for folder in (False, True):
                d = os.path.join(self.tmp, f"vault-{fmt}-{folder}")
                os.makedirs(d)
                reg = salidas.Registro(os.path.join(self.data_dir, f"r-{fmt}-{folder}.json"))
                dest = [{"path": d, "format": fmt}]
                a = doc(stem="Kickoff", source="Alfa/Kickoff.m4a", notes="ALFA")
                b = doc(stem="Kickoff", source="Beta/Kickoff.m4a", notes="BETA")
                salidas.write_all(a, dest, reg)
                pb = salidas.write_all(b, dest, reg)[0][0]
                jobs = bare_jobs([])
                jobs._registro = reg
                if folder:  # renombrar la carpeta Beta (server._folder_moved)
                    jobs.outputs_moved("Beta", "Beta 2", folder=True)
                    new = dict(b, source="Beta 2/Kickoff.m4a", notes="BETA 2")
                else:  # mover la reunión (server.move)
                    jobs.outputs_moved("Beta/Kickoff.m4a", "Gamma/Kickoff.m4a")
                    new = dict(b, source="Gamma/Kickoff.m4a", notes="BETA 2")
                self.assertEqual(salidas.write_all(new, dest, reg), [(pb, "escrito")], (fmt, folder))
                self.assertEqual(sorted(os.listdir(d)), sorted(["Kickoff." + fmt, os.path.basename(pb)]))
                self.assertEqual(reg.entries(new["source"]), [(pb, salidas.sha256_of(pb))])
                self.assertIn("BETA 2", self.body(pb, fmt))
                # una reunión nueva en la ruta de antes no se queda con ese documento: tiene el suyo
                otra = doc(stem="Kickoff", source="Beta/Kickoff.m4a", notes="NUEVA")
                po, accion = salidas.write_all(otra, dest, reg)[0]
                self.assertEqual(accion, "escrito")
                self.assertNotIn(po, (pb, os.path.join(d, "Kickoff." + fmt)))
                self.assertRegex(os.path.basename(po), r"^Kickoff \([0-9a-f]{6}\)\." + fmt + "$")
                self.assertIn("BETA 2", self.body(pb, fmt))
                self.assertIn("NUEVA", self.body(po, fmt))
                # y cada una sigue en el suyo, también sin registro (otro PC, reinstalación): la movida también, por
                # el origen que dice su documento, aunque su nombre propio salga de la ruta de antes
                self.assertEqual(salidas.write_all(new, dest, reg), [(pb, "igual")])
                self.assertEqual(salidas.write_all(otra, dest, reg), [(po, "igual")])
                otro_pc = salidas.Registro(os.path.join(self.data_dir, f"otro-{fmt}-{folder}.json"))
                self.assertEqual(salidas.write_all(new, dest, otro_pc), [(pb, "igual")], (fmt, folder))
                self.assertEqual(salidas.write_all(otra, dest, otro_pc), [(po, "igual")])
                self.assertEqual(sorted(os.listdir(d)), sorted(["Kickoff." + fmt] + [os.path.basename(p)
                                                                                     for p in (pb, po)]))
                self.assertEqual(otro_pc.rel_of(pb), new["source"])
                reinstalada = salidas.Registro(os.path.join(self.data_dir, f"nuevo-{fmt}-{folder}.json"))
                self.assertEqual(salidas.write_all(dict(new, notes="BETA 3"), dest, reinstalada), [(pb, "cambiado")])
                self.assertEqual(len(os.listdir(d)), 3)  # ni un documento nuevo al lado

    def test_sin_nombre_libre_nunca_encima_de_otra_reunion(self):
        """Si todos los nombres propios que se prueban (alt_for) ya son de otras reuniones, el documento se queda sin
        escribir («cambiado», con su aviso) en vez de ir encima del de otra reunión."""
        a = doc(stem="Kickoff", source="Alfa/Kickoff.m4a", notes="ALFA")
        b = doc(stem="Kickoff", source="Beta/Kickoff.m4a", notes="BETA")
        salidas.write_all(a, self.dest, self.reg)
        pb = salidas.write_all(b, self.dest, self.reg)[0][0]
        self.reg.moved("Beta/Kickoff.m4a", "Gamma/Kickoff.m4a")  # Beta se va con su documento
        nueva = doc(stem="Kickoff", source="Beta/Kickoff.m4a", notes="NUEVA")  # y otra reunión ocupa su ruta
        with mock.patch.object(salidas, "MAX_ALT", 1):  # su único nombre propio es el de Beta
            self.assertEqual(salidas.write_all(nueva, self.dest, self.reg), [(pb, "cambiado")])
        self.assertIn("BETA", self.body(pb, "md"))
        self.assertNotIn("NUEVA", self.body(pb, "md"))
        self.assertEqual(self.reg.rel_of(pb), "Gamma/Kickoff.m4a")


# ----------------------------------------------------------------------------- P2-04 / P2-05

class EscrituraSeguraTests(TempTree):
    def setUp(self):
        super().setUp()
        self.d = os.path.join(self.tmp, "vault")
        os.makedirs(self.d)
        self.reg = salidas.Registro(os.path.join(self.data_dir, "salidas.json"))
        self.dest = [{"path": self.d, "format": "docx"}]

    def test_si_no_se_puede_sustituir_no_queda_el_tmp(self):
        """P2-04: con el documento abierto en Word, os.replace falla: se reintenta y el .tmp no se queda."""
        real = os.replace
        with mock.patch.object(salidas.os, "replace", side_effect=PermissionError("en uso")), \
                mock.patch.object(salidas.time, "sleep"):
            done = salidas.write_all(doc(), self.dest, self.reg)
        self.assertEqual(done[0][1], "error: PermissionError")
        self.assertEqual(os.listdir(self.d), [])
        fallos = [PermissionError("en uso")] * 3

        def replace(a, b):
            if fallos:
                raise fallos.pop()
            return real(a, b)
        with mock.patch.object(salidas.os, "replace", side_effect=replace), mock.patch.object(salidas.time, "sleep"):
            done = salidas.write_all(doc(), self.dest, self.reg)
        self.assertEqual(done[0][1], "escrito")  # a la cuarta
        self.assertEqual(os.listdir(self.d), [STEM + ".docx"])

    def test_dos_escrituras_a_la_vez(self):
        """P2-05: el temporizador de las notas y el trabajo que acaba de resumir escriben la misma reunión a la vez: el
        documento sale entero, ninguna falla y el registro casa con lo que hay en disco."""
        real = os.replace
        barrera = threading.Barrier(2, timeout=1.0)

        def replace(a, b):  # las dos llegan a la vez a poner su .tmp en su sitio (sin cerrojo, comparten el .tmp)
            try:
                barrera.wait()
            except threading.BrokenBarrierError:
                pass
            return real(a, b)
        res = []

        def escribe(n):
            res.extend(salidas.write_all(doc(notes=f"v{n}"), self.dest, self.reg))
        with mock.patch.object(salidas.os, "replace", side_effect=replace):
            ts = [threading.Thread(target=escribe, args=(n,)) for n in (1, 2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(10)
        self.assertEqual(sorted(k for _, k in res), ["escrito", "escrito"], res)
        p = os.path.join(self.d, STEM + ".docx")
        with zipfile.ZipFile(p) as z:
            self.assertIsNone(z.testzip())
        self.assertEqual(self.reg.get(p), salidas.sha256_of(p))
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith(".tmp")], [])
        self.assertEqual(salidas.write_all(doc(notes="v3"), self.dest, self.reg)[0][1], "escrito")  # no «cambiado»


# ----------------------------------------------------------------------------- P2-06

class DestinoNoDisponibleTests(TempTree):
    """P2-06: un destino guardado que ahora no está (sin VPN, disco desconectado) no impide guardar los ajustes."""

    def test_guardar_ajustes_con_un_destino_desconectado(self):
        d = os.path.join(self.tmp, "SharePoint")
        os.makedirs(d)
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, perfil={"outputs": [{"path": d, "format": "md", "by_project": False}]})
        a.config = lambda q: {}
        shutil.rmtree(d)  # se desconecta
        a.save_settings({"perfil": {"folder": None, "vocab": None, "context": "",
                                    "outputs": [{"path": d, "format": "docx"}]}, "language": "pt"})
        st = a.settings.get()
        self.assertEqual((st["language"], st["perfil"]["outputs"][0]["format"]), ("pt", "docx"))
        with self.assertRaises(server.ApiError):  # uno nuevo que no existe sigue sin valer
            a.save_settings({"perfil": {"outputs": [{"path": d}, {"path": os.path.join(self.tmp, "no")}]}})
        a.perfil_save({"outputs": [{"path": d, "format": "md"}]})  # y lo mismo por /api/perfil
        self.assertEqual(a.settings.get()["perfil"]["outputs"][0]["format"], "md")


# ----------------------------------------------------------------------------- P2-07

class ApagarTests(TempTree):
    """P2-07: al apagar, las reescrituras que esperaban su temporizador se hacen antes de terminar el proceso."""

    def test_flush_outputs_escribe_lo_pendiente(self):
        rel = "Alfa/" + STEM + ".wav"
        meeting(self, rel)
        dest = os.path.join(self.tmp, "vault")
        os.makedirs(dest)
        j = jobs_with(self.tmp, self.data_dir, [{"path": dest, "format": "md"}])
        with mock.patch.object(server, "log"):
            j.schedule_outputs(rel, delay=60)  # el temporizador no vencería antes de apagar
            self.assertFalse(os.listdir(dest))
            self.assertEqual(j.flush_outputs(), [rel])
        self.assertEqual(os.listdir(dest), [STEM + ".md"])
        self.assertEqual(j._salidas_timers, {})

    def test_shutdown_prepare_vacia_los_destinos_antes_de_parar_los_trabajos(self):
        a = server.App.__new__(server.App)
        a.rec_lock, a.monitor, a.rec = threading.Lock(), None, None
        calls = []
        a.jobs = types.SimpleNamespace(flush_outputs=lambda: calls.append("flush"),
                                       shutdown=lambda: calls.append("stop"))
        a.shutdown_prepare()
        self.assertEqual(calls, ["flush", "stop"])


# ----------------------------------------------------------------------------- P2-08

class ReescrituraTests(TempTree):
    """P2-08: «Restaurar original», «Quién habla» y los cambios de hablantes reescriben el documento."""

    def app(self):
        a = server.App.__new__(server.App)
        a.library = types.SimpleNamespace(at=1)
        a.jobs = bare_jobs([])
        a.jobs.scheduled = []
        a.jobs.schedule_outputs = lambda rel, delay=4.0: a.jobs.scheduled.append(rel)
        return a

    def test_restaurar_el_original(self):
        rel = "r.wav"
        self.wav(rel)
        self.write("r.srt", srt_text([(0.0, 1.0, "corregido")]))
        bdir = server.backup_dir(rel)
        os.makedirs(bdir)
        with open(os.path.join(bdir, "original.srt"), "w", encoding="utf-8") as f:
            f.write(srt_text([(0.0, 1.0, "original")]))
        a = self.app()
        with mock.patch.object(server, "log"):
            a.restore_transcript({"path": rel})
        self.assertEqual(a.jobs.scheduled, [rel])

    def test_cambiar_un_hablante(self):
        self.wav("r.wav")
        self.write("r.hablantes.json", json.dumps({"version": 1, "tramos": [{"inicio": 0, "fin": 1, "h": "h1"}],
                                                   "hablantes": {"h1": {"segundos": 1}}}))
        a = self.app()
        a._speaker_update(server.safe("r.wav"), "h1", lambda info, d: info.update(persona="p1"))
        self.assertEqual(a.jobs.scheduled, ["r.wav"])

    def test_las_personas_reescriben_sus_reuniones(self):
        for n, persona in (("a", "p1"), ("b", "p2"), ("c", None)):
            self.wav(n + ".wav")
            self.write(n + ".hablantes.json", json.dumps(
                {"version": 1, "tramos": [{"inicio": 0, "fin": 1, "h": "h1"}],
                 "hablantes": {"h1": {"segundos": 1, **({"persona": persona} if persona else {})}}}))
        j = jobs_with(self.tmp, self.data_dir, [{"path": self.tmp, "format": "md"}],
                      library=server.Library(server.Durations(os.path.join(self.data_dir, "d.json"))))
        seen = []
        j.schedule_outputs = lambda rel, delay=4.0: seen.append(rel)
        self.assertEqual(j.outputs_for_persona("p1"), 1)
        self.assertEqual(seen, ["a.wav"])
        self.assertEqual(j.outputs_for_persona(None), 2)  # olvidar todas: las de cualquier persona reconocida
        j.settings.update(lambda d: d.update(perfil={"outputs": []}))
        self.assertEqual(j.outputs_for_persona(None), 0)  # sin destinos no se recorre nada

    def test_los_ganchos(self):
        src = read_server()
        voces = src[src.index("    def _run_voices"):src.index("    def cancel(self, job_id)")]
        self.assertIn("self.schedule_outputs(job.path)", voces)
        person = src[src.index("    def voices_person"):src.index("    def voices_merge")]
        self.assertIn("self.jobs.outputs_for_persona(pid)", person)
        merge = src[src.index("    def voices_merge"):src.index("    def voices_forget")]
        self.assertIn('self.jobs.schedule_outputs(a["path"])', merge)
        forget = src[src.index("    def voices_forget"):src.index("    def voices_reassign")]
        self.assertIn("self.jobs.outputs_for_persona(None)", forget)
        self.assertIn('self.jobs.outputs_for_persona(b.get("id"))', forget)


def read_server():
    with open(server.__file__, encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------- P2-10

class BorrarTests(TempTree):
    """P2-10: al borrar una reunión, sus documentos de los destinos que nadie ha cambiado van a la papelera."""

    def test_borrar_lleva_las_copias_intactas(self):
        rel = "Alfa/" + STEM + ".wav"
        meeting(self, rel)
        d1, d2 = os.path.join(self.tmp, "vault"), os.path.join(self.tmp, "sharepoint")
        os.makedirs(d1)
        os.makedirs(d2)
        a = server.App.__new__(server.App)
        a.library = server.Library(server.Durations(os.path.join(self.data_dir, "d.json")))
        a.jobs = jobs_with(self.tmp, self.data_dir, [{"path": d1, "format": "md"}, {"path": d2, "format": "txt"}],
                           library=a.library)
        a.settings, a.rec = a.jobs.settings, None
        with mock.patch.object(server, "log"):
            done = a.jobs.write_outputs(rel)
            self.assertEqual([k for _, k in done], ["escrito", "escrito"])
            editado = done[1][0]
            with open(editado, "a", encoding="utf-8") as f:
                f.write("apunte\n")
            r = a.delete({"path": rel})
        self.assertEqual(r["copias"], 1)
        self.assertFalse(os.path.exists(done[0][0]))  # a la papelera
        self.assertTrue(os.path.isfile(editado))  # el que se cambio alli se queda
        self.assertEqual(a.jobs.registro.entries(rel), [])
        if not server.WINDOWS:  # fuera de Windows la «papelera» es DATA_DIR/papelera (en Windows, la de verdad)
            papelera = os.path.join(self.data_dir, "papelera")
            self.assertTrue(any(STEM + ".md" in fs for _, _, fs in os.walk(papelera)))

    def test_el_dialogo_lo_dice(self):
        js = read("notas.js")
        dlg = js[js.index("async function deleteItem"):js.index("const dropFolder")]
        self.assertIn("S.config.perfil?.outputs", dlg)
        self.assertIn("destinos de salida que nadie haya cambiado", dlg)
        self.assertIn("r.copias", dlg)


# ----------------------------------------------------------------------------- P2-26

# parse_notes de las versiones publicadas, copiado de `git show 5c55e54:_app/server.py`: el de la del 30-oct (012bb24)
# es el mismo y solo cambia NOTE_KEYS (sin idioma ni contexto). Solo aceptan una cabecera con claves que conocen; si no,
# la toman entera por notas. test_la_copia_es_la_publicada lo comprueba contra git cuando el clon tiene la historia.
PARSE_NOTES_PUBLICADO = r'''def parse_notes(text):
    lines = text.replace("\r\n", "\n").split("\n")
    props, body = {}, text
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        # solo es cabecera si todo lo de dentro es "clave: valor" conocido (si no, era un separador)
        if end and all(not l.strip() or l.partition(":")[0].strip().lower() in NOTE_KEYS for l in lines[1:end]):
            for line in lines[1:end]:
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip()
                if k in NOTE_KEYS and v:
                    props[k] = [t.strip() for t in v.strip("[]").split(",") if t.strip()] if k == "etiquetas" else v
            body = "\n".join(lines[end + 1:]).lstrip("\n")
    return props, body
'''
NOTE_KEYS_PUBLICADAS = {
    "012bb24": ("titulo", "icono", "etiquetas", "estado", "participantes", "proyecto", "workstream"),
    "5c55e54": ("titulo", "icono", "etiquetas", "estado", "participantes", "proyecto", "workstream", "idioma",
                "contexto"),
}


def parse_publicado(rev):
    ns = {"NOTE_KEYS": NOTE_KEYS_PUBLICADAS[rev]}
    exec(PARSE_NOTES_PUBLICADO, ns)
    return ns["parse_notes"]


class TemaPorIdTests(TempTree):
    """P2-26: el tema de cada reunión se resuelve por id (y por la ruta de la reunión), no solo por nombre."""

    def test_reuniones_grabadas_con_el_nombre_anterior_de_la_app(self):
        self.write("Vocabulario/General.txt", "raíz, uno\n")
        self.write("Beta/Vocabulario/General.txt", "beta, dos\n")
        audio = self.wav("Reuniones/r.wav")
        self.write("Reuniones/r.md", "---\nproyecto: Notas\nworkstream: General\n---\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "raíz, uno")

    def test_nunca_la_lista_de_otro_proyecto(self):
        self.write("Beta/Vocabulario/General.txt", "beta, dos\n")
        audio = self.wav("Alfa/r.wav")
        self.write("Alfa/r.md", "---\nproyecto: Alfa\nworkstream: General\n---\n")  # la lista de Alfa ya no existe
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "general")
        self.assertIsNone(server.find_vocab("Alfa", "General"))
        self.assertEqual(server.find_vocab("", "General")["project"], "Beta")  # sin proyecto, como antes

    def links(self):
        a, b = os.path.join(self.tmp, "A", "Proyecto"), os.path.join(self.tmp, "B", "Proyecto")
        for p, t in ((a, "de A"), (b, "de B")):
            os.makedirs(os.path.join(p, "Vocabulario"))
            with open(os.path.join(p, "Vocabulario", "General.txt"), "w", encoding="utf-8") as f:
                f.write(t + "\n")
        server.LINKS[server.link_id(a)] = {"name": "Proyecto", "path": a}
        server.LINKS[server.link_id(b)] = {"name": "Proyecto", "path": b}
        return a, b

    def test_dos_carpetas_vinculadas_que_se_llaman_igual(self):
        a, b = self.links()
        audio = os.path.join(b, "r.wav")
        with open(audio, "wb") as f:
            f.write(b"RIFF")
        with open(os.path.join(b, "r.md"), "w", encoding="utf-8") as f:
            f.write("---\nproyecto: Proyecto\nworkstream: General\n---\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "de B")  # antes, la primera: «de A»
        fuera = self.wav("r.wav")  # fuera de las dos: no se adivina
        self.write("r.md", "---\nproyecto: Proyecto\nworkstream: General\n---\n")
        self.assertEqual(server.vocab_for(fuera, {"prompt": "general"}), "general")
        bid = f":{server.link_id(b)}/Vocabulario/General.txt"
        server.temas().set("r.wav", bid)  # el id que guarda este PC al elegir la de B
        self.assertEqual(server.vocab_for(fuera, {"prompt": "general"}), "de B")  # con el id, sin dudas
        # el id solo desempata: si otra versión u otro PC cambia el tema en la cabecera, mandan los nombres
        self.write("Otro/Vocabulario/General.txt", "otro\n")
        self.write("r.md", "---\nproyecto: Otro\nworkstream: General\n---\n")
        self.assertEqual(server.vocab_for(fuera, {"prompt": "general"}), "otro")
        self.write("r.md", "---\nproyecto: Proyecto\n---\n")  # y sin tema, ninguna lista aunque quede el id
        self.assertEqual(server.vocab_for(fuera, {"prompt": "general"}), "general")

    def app(self):
        a = server.App.__new__(server.App)
        a.settings, a.notes_lock = settings_store(self.tmp), threading.Lock()
        a.library = types.SimpleNamespace(at=1)
        a.jobs = types.SimpleNamespace(refresh_prompt=lambda rel: None, schedule_outputs=lambda rel, delay=4.0: None)
        return a

    def test_al_grabar_y_al_guardar_queda_el_id(self):
        """El id de la lista va al almacén de este PC (temas()), no a la cabecera del .md, que solo lleva los nombres;
        la interfaz lo recibe en props.vocabulario."""
        _, b = self.links()
        bid = f":{server.link_id(b)}/Vocabulario/General.txt"
        self.write("Vocabulario/General.txt", "raíz\n")
        a = self.app()
        audio, md = self.wav("r.wav"), os.path.join(self.root, "r.md")
        r = a.notes_save({"path": "r.wav", "props": {"vocabulario": bid}})  # elegido por id
        self.assertNotIn("vocabulario", server.read_text(md))
        self.assertEqual(server.parse_notes(server.read_text(md))[0], {"proyecto": "Proyecto", "workstream": "General"})
        self.assertEqual(server.temas().get("r.wav"), bid)
        self.assertEqual(r["props"]["vocabulario"], bid)
        self.assertEqual(a.notes_get({"path": "r.wav"})["props"],
                         {"proyecto": "Proyecto", "workstream": "General", "vocabulario": bid})
        # una reunion de antes, con el nombre anterior de la app: guardar las notas u otra propiedad no le toca la
        # cabecera; el tema se sigue encontrando por nombres
        server.temas().set("r.wav", None)
        self.write("r.md", "---\nproyecto: Notas\nworkstream: General\n---\n\nnotas\n")
        a.notes_save({"path": "r.wav", "body": "notas nuevas"})  # el autoguardado de las notas
        self.assertEqual(server.read_text(md), "---\nproyecto: Notas\nworkstream: General\n---\n\nnotas nuevas\n")
        a.notes_save({"path": "r.wav", "props": {"estado": "Revisada"}})
        p = server.parse_notes(server.read_text(md))[0]
        self.assertEqual(p, {"proyecto": "Notas", "workstream": "General", "estado": "Revisada"})
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "raíz")
        # al elegir el tema, si (tambien por nombres): el nombre de ahora del proyecto y el id
        a.notes_save({"path": "r.wav", "props": {"proyecto": "Notas", "workstream": "General"}})
        p = server.parse_notes(server.read_text(md))[0]
        self.assertEqual((p["proyecto"], server.temas().get("r.wav")), (server.APP_NAME, "Vocabulario/General.txt"))
        a.notes_save({"path": "r.wav", "props": {"proyecto": "", "workstream": ""}})  # sin tema: sin id
        self.assertIsNone(server.temas().get("r.wav"))
        self.assertNotIn("vocabulario", a.notes_get({"path": "r.wav"})["props"])
        src = read_server()
        rec = src[src.index("    def rec_start"):src.index("    def rec_mic")]
        self.assertIn('"vocabulario": v["id"]} if v else {}', rec)

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_la_pagina_de_la_reunion_elige_por_id(self):
        """Con dos carpetas vinculadas que se llaman igual, lo que la página manda al elegir la lista de B (wsPatch)
        hace que Whisper use la de B, aunque la reunión esté en A o fuera de las dos; y el selector marca la de B."""
        src = read("notas.js")
        handler = src[src.index("e.target.dataset.prop !== 'workstream'"):]
        self.assertIn("saveProps(a, wsPatch(v))", handler[:handler.index("});")])  # el selector manda el id
        a_dir, b_dir = self.links()
        aid, bid = (f":{server.link_id(p)}/Vocabulario/General.txt" for p in (a_dir, b_dir))
        vocab = [{"id": i, "project": "Proyecto", "name": "General"} for i in (aid, bid)]
        js = js_defs(read("app.js"), ["nfc", "foldMap", "fold"]) + "\n" + js_function(read("notas.js"), "vocabOf")
        js += js_defs(read("notas.js"), ["wsPatch"]) + f"\nconst S = {{vocab: {json.dumps(vocab)}}};\n"

        def node(code):
            r = subprocess.run(["node", "-e", js + code], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()
        patch = json.loads(node("console.log(JSON.stringify(wsPatch(S.vocab[1])));"))
        self.assertEqual(json.loads(node("console.log(JSON.stringify(wsPatch(undefined)));")), {"vocabulario": ""})
        app = self.app()
        dentro = os.path.join(a_dir, "r.wav")
        with open(dentro, "wb") as f:
            f.write(b"RIFF")
        fuera = self.wav("r.wav")
        # en A, grabada con la lista de A y en la página se elige la de B; fuera de las dos, grabada con B y en la
        # página se vuelve a elegir B (antes, las dos acababan en la de A o en el vocabulario general)
        for audio, grabada in ((dentro, aid), (fuera, bid)):
            rel = server.rel_of(audio)
            app.notes_save({"path": rel, "props": {"proyecto": "Proyecto", "workstream": "General",
                                                   "vocabulario": grabada}})  # como rec_start
            app.notes_save({"path": rel, "props": patch})
            self.assertNotIn("vocabulario", server.read_text(os.path.splitext(audio)[0] + ".md"))
            props = app.notes_get({"path": rel})["props"]  # lo que recibe la página
            self.assertEqual(props.get("vocabulario"), bid, rel)
            self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "de B", rel)
            elegida = node(f"console.log(vocabOf({{props: {json.dumps(props)}}}).id);")
            self.assertEqual(elegida, bid, rel)  # antes, la primera con ese proyecto: la de A
        app.notes_save({"path": server.rel_of(dentro), "props": {"vocabulario": ""}})  # «Ninguno»
        self.assertEqual(server.vocab_for(dentro, {"prompt": "general"}), "general")

    def test_por_nombres_se_queda_la_lista_que_ya_tenia(self):
        """Quien todavía mande el tema por nombres (una página abierta antes de actualizar) no le quita a la reunión
        la lista que ya tenía si los nombres son los suyos; si son los de otro proyecto, sí cambia."""
        _, b_dir = self.links()
        bid = f":{server.link_id(b_dir)}/Vocabulario/General.txt"
        self.write("Otro/Vocabulario/General.txt", "otro\n")
        app, fuera = self.app(), self.wav("r.wav")
        app.notes_save({"path": "r.wav", "props": {"vocabulario": bid}})
        app.notes_save({"path": "r.wav", "props": {"proyecto": "Proyecto", "workstream": "General"}})
        self.assertEqual(server.vocab_for(fuera, {"prompt": "general"}), "de B")  # antes: el general
        app.notes_save({"path": "r.wav", "props": {"proyecto": "Otro", "workstream": "General"}})
        self.assertEqual(server.vocab_for(fuera, {"prompt": "general"}), "otro")

    def test_proyecto_renombrado(self):
        """La carpeta del proyecto se renombra (también desde el Explorador): sus reuniones conservan el nombre viejo
        en la cabecera y siguen usando la lista de ese tema de la carpeta en la que están, no el vocabulario general."""
        self.write("Gamma/Vocabulario/Kick.txt", "gamma, uno\n")
        self.write("Vocabulario/Kick.txt", "raíz\n")  # la de la raíz solo vale con su nombre
        audio = self.wav("Gamma/Reuniones/r.wav")
        self.write("Gamma/Reuniones/r.md", "---\nproyecto: Beta\nworkstream: Kick\n---\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "gamma, uno")
        # el id que guardó este PC antes de renombrarla está con la ruta de antes de la reunión, que ya no es la suya
        server.temas().set("Beta/Reuniones/r.wav", "Beta/Vocabulario/Kick.txt")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "gamma, uno")
        fuera = self.wav("Reuniones/r.wav")  # fuera de su carpeta no hay de dónde sacarla
        self.write("Reuniones/r.md", "---\nproyecto: Beta\nworkstream: Kick\n---\n")
        self.assertEqual(server.vocab_for(fuera, {"prompt": "general"}), "general")
        # una carpeta vinculada con listas que en este PC tiene otro nombre: sus listas lo toman
        ext = os.path.join(self.tmp, "Ext")
        os.makedirs(os.path.join(ext, "Vocabulario"))
        with open(os.path.join(ext, "Vocabulario", "Comité.txt"), "w", encoding="utf-8") as f:
            f.write("externo\n")
        server.LINKS[server.link_id(ext)] = {"name": "Cliente", "path": ext}  # en el otro PC, «Externo»
        dentro = os.path.join(ext, "r.wav")
        with open(dentro, "wb") as f:
            f.write(b"RIFF")
        with open(os.path.join(ext, "r.md"), "w", encoding="utf-8") as f:
            f.write("---\nproyecto: Externo\nworkstream: Comité\n---\n")
        self.assertEqual(server.vocab_for(dentro, {"prompt": "general"}), "externo")
        # pero si el proyecto existe (con otras listas), nunca la de otro: la regla de siempre
        self.write("Beta/Vocabulario/Otro.txt", "beta\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "general")

    def test_proyecto_renombrado_nunca_la_lista_de_otro_cliente(self):
        """Regresión del recurso de arriba: con el proyecto de la cabecera renombrado fuera de la app, una reunión
        guardada en la carpeta de otro proyecto que tiene un tema con ese nombre usaba la lista de ese otro cliente
        (5c55e54 daba el vocabulario general). La carpeta de la reunión solo vale si su lista es la única de ese tema
        fuera de la raíz, si es la del proyecto más cercano a la reunión y si la reunión no tiene guardado el id de
        otra lista."""
        self.write("Beta/Vocabulario/Kick.txt", "beta\n")
        self.write("Delta/Vocabulario/Kick.txt", "delta (otro cliente)\n")
        audio = self.wav("Delta/Actas/r.wav")
        self.write("Delta/Actas/r.md", "---\nproyecto: Beta\nworkstream: Kick\n---\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "beta")
        os.rename(os.path.join(self.root, "Beta"), os.path.join(self.root, "Gamma"))  # desde el Explorador
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "general")
        # con dos listas de ese tema, la de dentro de la carpeta renombrada tampoco: está igual que la de Delta si la
        # renombrada hubiera sido Delta
        dentro = self.wav("Gamma/r2.wav")
        self.write("Gamma/r2.md", "---\nproyecto: Beta\nworkstream: Kick\n---\n")
        self.assertEqual(server.vocab_for(dentro, {"prompt": "general"}), "general")
        guardada = os.path.join(self.tmp, "vocabulario de Delta")
        shutil.move(os.path.join(self.root, "Delta", "Vocabulario"), guardada)
        self.assertEqual(server.vocab_for(dentro, {"prompt": "general"}), "beta")  # ya es la única
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "general")  # y Delta no es su carpeta
        # con Beta borrado, sin id no se distingue de haber renombrado Beta a Delta (como en 5c55e54); con el id que
        # guardó este PC al grabarla, sí: ya estaba en Delta cuando su lista estaba en Beta
        shutil.move(guardada, os.path.join(self.root, "Delta", "Vocabulario"))
        shutil.move(os.path.join(self.root, "Gamma"), os.path.join(self.tmp, "Gamma borrada"))
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "delta (otro cliente)")
        server.temas().set("Delta/Actas/r.wav", "Beta/Vocabulario/Kick.txt")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "general")
        # un proyecto dentro de la carpeta de otro: el que cuenta es el más cercano a la reunión
        self.write("Delta/Sigma/Vocabulario/Otro.txt", "sigma\n")
        sub = self.wav("Delta/Sigma/Actas/r3.wav")
        self.write("Delta/Sigma/Actas/r3.md", "---\nproyecto: Beta\nworkstream: Kick\n---\n")
        self.assertEqual(server.vocab_for(sub, {"prompt": "general"}), "general")

    def test_la_copia_es_la_publicada(self):
        """PARSE_NOTES_PUBLICADO y NOTE_KEYS_PUBLICADAS son los de esas versiones (en la CI, que clona sin historia,
        se salta)."""
        for rev, keys in NOTE_KEYS_PUBLICADAS.items():
            try:
                r = subprocess.run(["git", "-C", REPO, "show", f"{rev}:_app/server.py"], capture_output=True,
                                   text=True, encoding="utf-8", timeout=30)
            except OSError:
                self.skipTest("sin git")
            if r.returncode:
                self.skipTest(f"el clon no tiene {rev}")
            code = r.stdout
            i = code.index("def parse_notes(text):")
            end = "    return props, body\n"
            self.assertEqual(code[i:code.index(end, i) + len(end)], PARSE_NOTES_PUBLICADO, rev)
            j = code.index("NOTE_KEYS = ")
            ns = {}
            exec(code[j:code.index(")", j) + 1], ns)
            self.assertEqual(ns["NOTE_KEYS"], keys, rev)

    def test_lo_que_escribe_esta_version_lo_leen_las_publicadas(self):
        """Regresión de P2-26: con la clave `vocabulario` en la cabecera, la versión publicada (5c55e54) tomaba la
        cabecera entera por notas, y la reescribía dentro de ellas al guardar. El id va ahora a temas() y la cabecera
        solo lleva claves que esa versión conoce: al grabar con tema, idioma, contexto y participantes, al elegir el
        tema en la página, al cambiar estado, etiquetas o título y en el autoguardado de las notas. La del 30-oct
        (012bb24) no conoce idioma ni contexto, que son de 5c55e54 (P2-29); sin ellos también lee bien esta."""
        _, b_dir = self.links()
        bid = f":{server.link_id(b_dir)}/Vocabulario/General.txt"
        app = self.app()
        self.wav("r1.wav")
        app.notes_save({"path": "r1.wav", "props": {"proyecto": "Proyecto", "workstream": "General", "vocabulario": bid,
                                                    "idioma": "es", "contexto": "Semanal de seguimiento",
                                                    "participantes": "Ana, Luis"}})  # como rec_start
        app.notes_save({"path": "r1.wav", "body": "## Apuntes\n\n- uno\n"})
        app.notes_save({"path": "r1.wav", "props": {"vocabulario": bid}})  # el tema, otra vez desde la página
        app.notes_save({"path": "r1.wav", "props": {"estado": "Revisada", "etiquetas": ["kick", "q4"]}})
        app.notes_save({"path": "r1.wav", "body": "## Apuntes\n\n- uno\n- dos\n"})
        esperado = {"proyecto": "Proyecto", "workstream": "General", "idioma": "es", "participantes": "Ana, Luis",
                    "contexto": "Semanal de seguimiento", "estado": "Revisada", "etiquetas": ["kick", "q4"]}
        text = server.read_text(os.path.join(self.root, "r1.md"))
        self.assertEqual(parse_publicado("5c55e54")(text), (esperado, "## Apuntes\n\n- uno\n- dos\n"))
        self.assertEqual(server.vocab_for(os.path.join(self.root, "r1.wav"), {"prompt": "general"}), "de B")
        self.wav("r2.wav")  # sin idioma ni contexto (Ctrl+Alt+R sin idioma elegido, una reunión importada)
        app.notes_save({"path": "r2.wav", "props": {"vocabulario": bid}})
        app.notes_save({"path": "r2.wav", "props": {"estado": "Pendiente", "titulo": "Kick-off"}})
        app.notes_save({"path": "r2.wav", "body": "notas\n"})
        text = server.read_text(os.path.join(self.root, "r2.md"))
        for rev in NOTE_KEYS_PUBLICADAS:
            self.assertEqual(parse_publicado(rev)(text), ({"proyecto": "Proyecto", "workstream": "General",
                                                          "estado": "Pendiente", "titulo": "Kick-off"}, "notas\n"), rev)

    def test_el_id_sigue_a_la_reunion_y_se_olvida_al_borrarla(self):
        _, b_dir = self.links()
        bid = f":{server.link_id(b_dir)}/Vocabulario/General.txt"
        self.wav("r.wav")
        os.makedirs(os.path.join(self.root, "Dest"))
        app = server.App.__new__(server.App)
        app.jobs = bare_jobs([])
        app.jobs.voice_vectors = {}
        app.jobs.refresh_prompt = app.jobs.schedule_outputs = lambda rel, delay=4.0: None
        app.library = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        app.settings, app.notes_lock, app.rec = settings_store(self.tmp), threading.Lock(), None
        app.notes_save({"path": "r.wav", "props": {"vocabulario": bid}})
        with mock.patch.object(server, "log"):
            self.assertEqual(app.move({"path": "r.wav", "folder": "Dest", "name": "nuevo"})["path"], "Dest/nuevo.wav")
        self.assertEqual((server.temas().get("r.wav"), server.temas().get("Dest/nuevo.wav")), (None, bid))
        self.assertEqual(server.vocab_for(os.path.join(self.root, "Dest", "nuevo.wav"), {"prompt": "general"}), "de B")
        app.library.refresh()
        self.assertEqual(app.library.find("Dest/nuevo.wav")["props"]["vocabulario"], bid)  # lo que ve la biblioteca
        self.assertEqual(server.Temas(os.path.join(self.data_dir, "temas.json")).data, {"Dest/nuevo.wav": bid})
        with mock.patch.object(server, "log"):
            app.delete({"path": "Dest/nuevo.wav"})
        self.assertIsNone(server.temas().get("Dest/nuevo.wav"))


# ----------------------------------------------------------------------------- P2-27

class PerfilSigueAlRenombrarTests(TempTree):
    """P2-27: renombrar o borrar el tema o la carpeta fijos del perfil no deja a Ctrl+Alt+R sin vocabulario ni
    grabando en la raíz."""

    def app(self, **st):
        a = server.App.__new__(server.App)
        a.settings, a.notes_lock = settings_store(self.tmp, **st), threading.Lock()
        a.library = server.Library(server.Durations(os.path.join(self.data_dir, "d.json")))
        a.jobs = bare_jobs([])
        a.jobs.voice_vectors = {}
        a.rec = None
        return a

    def props(self, rel):
        return server.parse_notes(server.read_text(os.path.join(self.root, rel)))[0]

    def test_renombrar_la_carpeta_de_un_proyecto_lleva_sus_reuniones(self):
        """Regresión de P2-26: con el proyecto renombrado, sus reuniones (también las grabadas fuera de su carpeta) se
        quedaban con el vocabulario general, porque ya no se toma la lista de otro proyecto. Ahora su cabecera lo sigue,
        sin añadir claves (una versión anterior la sigue leyendo), y el id de su lista, en temas(), también."""
        self.write("Beta/Vocabulario/Kick.txt", "beta, uno\n")
        self.write("Otro/Vocabulario/Kick.txt", "otro\n")
        heads = {"Reuniones/r1": "proyecto: Beta\nworkstream: Kick",
                 "Reuniones/r2": "proyecto: Beta\nworkstream: Kick",
                 "Beta/Sub/r3": "estado: Revisada\nproyecto: Beta\nworkstream: Kick",
                 "Reuniones/r4": "proyecto: Otro\nworkstream: Kick",
                 "Reuniones/r5": "estado: Revisada"}
        for base, head in heads.items():
            self.wav(base + ".wav")
            self.write(base + ".md", f"---\n{head}\n---\n\nnotas\n")
        server.temas().set("Reuniones/r2.wav", "Beta/Vocabulario/Kick.txt")
        before = {b: server.read_text(os.path.join(self.root, b + ".md")) for b in ("Reuniones/r4", "Reuniones/r5")}
        a = self.app()
        with mock.patch.object(server, "log"):
            self.assertEqual(a.rename_folder({"path": "Beta", "name": "Gamma"}), {"path": "Gamma"})
        self.assertEqual(self.props("Reuniones/r1.md"), {"proyecto": "Gamma", "workstream": "Kick"})
        self.assertEqual(self.props("Reuniones/r2.md"), {"proyecto": "Gamma", "workstream": "Kick"})
        self.assertEqual(server.temas().get("Reuniones/r2.wav"), "Gamma/Vocabulario/Kick.txt")
        self.assertEqual(self.props("Gamma/Sub/r3.md"),
                         {"estado": "Revisada", "proyecto": "Gamma", "workstream": "Kick"})
        for b, text in before.items():
            self.assertEqual(server.read_text(os.path.join(self.root, b + ".md")), text)  # sin tocar
        for rel in ("Reuniones/r1.wav", "Reuniones/r2.wav", "Gamma/Sub/r3.wav"):
            self.assertEqual(server.vocab_for(os.path.join(self.root, rel), {"prompt": "general"}), "beta, uno", rel)
        self.assertEqual(server.vocab_for(os.path.join(self.root, "Reuniones/r4.wav"), {"prompt": "general"}), "otro")

    def test_si_otro_proyecto_se_sigue_llamando_asi(self):
        """Si queda otro proyecto con el nombre viejo, una reunión sin id solo lo cambia si está dentro de la carpeta
        renombrada (la de fuera puede ser de ese otro); con el id, siempre."""
        self.write("Beta/Vocabulario/Kick.txt", "beta, uno\n")
        ext = os.path.join(self.tmp, "Ext")
        os.makedirs(os.path.join(ext, "Vocabulario"))
        with open(os.path.join(ext, "Vocabulario", "Kick.txt"), "w", encoding="utf-8") as f:
            f.write("ext\n")
        server.LINKS[server.link_id(ext)] = {"name": "Beta", "path": ext}
        for base in ("Reuniones/r1", "Beta/r2", "Reuniones/r3", "Reuniones/r4"):
            self.wav(base + ".wav")
            self.write(base + ".md", "---\nproyecto: Beta\nworkstream: Kick\n---\n")
        server.temas().set("Reuniones/r3.wav", "Beta/Vocabulario/Kick.txt")
        server.temas().set("Reuniones/r4.wav", f":{server.link_id(ext)}/Vocabulario/Kick.txt")  # la del otro
        with mock.patch.object(server, "log"):
            self.app().rename_folder({"path": "Beta", "name": "Gamma"})
        self.assertEqual(self.props("Reuniones/r1.md")["proyecto"], "Beta")
        self.assertEqual(self.props("Gamma/r2.md")["proyecto"], "Gamma")
        self.assertEqual(self.props("Reuniones/r3.md")["proyecto"], "Gamma")
        self.assertEqual(self.props("Reuniones/r4.md")["proyecto"], "Beta")
        self.assertEqual(server.temas().get("Reuniones/r3.wav"), "Gamma/Vocabulario/Kick.txt")
        for r, txt in (("r3", "beta, uno"), ("r4", "ext"), ("r1", "ext")):  # r1: sin id, el Beta que queda
            self.assertEqual(server.vocab_for(os.path.join(self.root, "Reuniones", r + ".wav"), {"prompt": "general"}),
                             txt, r)

    def test_renombrar_con_el_nombre_de_otro_proyecto(self):
        """Renombrar en la app un proyecto con el nombre que ya tiene otro (una vinculada «Gamma» con el mismo tema).
        Las reuniones sin id de los dos se quedan con su lista: el id va a temas(). Antes, con dos «Gamma», la de Beta
        de fuera de las dos carpetas y la de la vinculada de fuera pasaban al vocabulario general, y la de la vinculada
        guardada en la carpeta de Beta tomaba la lista de Beta."""
        self.write("Beta/Vocabulario/Kick.txt", "beta\n")
        ext = os.path.join(self.tmp, "Ext")
        os.makedirs(os.path.join(ext, "Vocabulario"))
        with open(os.path.join(ext, "Vocabulario", "Kick.txt"), "w", encoding="utf-8") as f:
            f.write("gamma vinculada\n")
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "Gamma", "path": ext}
        heads = {"Reuniones/r1": "Beta", "Beta/r2": "Beta", "Reuniones/r3": "Gamma", "Beta/r4": "Gamma"}
        for base, p in heads.items():
            self.wav(base + ".wav")
            self.write(base + ".md", f"---\nproyecto: {p}\nworkstream: Kick\n---\n")
        antes = {"Reuniones/r1": "beta", "Gamma/r2": "beta", "Reuniones/r3": "gamma vinculada",
                 "Gamma/r4": "gamma vinculada"}
        for base, txt in antes.items():
            audio = os.path.join(self.root, base.replace("Gamma/", "Beta/") + ".wav")
            self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), txt, base)
        with mock.patch.object(server, "log"):
            self.assertEqual(self.app().rename_folder({"path": "Beta", "name": "Gamma"}), {"path": "Gamma"})
        self.assertEqual(self.props("Reuniones/r1.md"), {"proyecto": "Gamma", "workstream": "Kick"})
        self.assertEqual(server.temas().get("Reuniones/r1.wav"), "Gamma/Vocabulario/Kick.txt")
        self.assertEqual(server.temas().get("Reuniones/r3.wav"), f":{lid}/Vocabulario/Kick.txt")
        self.assertEqual(server.temas().get("Gamma/r4.wav"), f":{lid}/Vocabulario/Kick.txt")
        for base, txt in antes.items():
            self.assertEqual(server.vocab_for(os.path.join(self.root, base + ".wav"), {"prompt": "general"}), txt, base)

    def test_renombrar_una_vinculada_lleva_sus_reuniones(self):
        ext = os.path.join(self.tmp, "Ext")
        os.makedirs(os.path.join(ext, "Vocabulario"))
        with open(os.path.join(ext, "Vocabulario", "Kick.txt"), "w", encoding="utf-8") as f:
            f.write("externo\n")
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "Externo", "path": ext}
        with open(os.path.join(ext, "r1.wav"), "wb") as f:
            f.write(wav_bytes(1.0))
        with open(os.path.join(ext, "r1.md"), "w", encoding="utf-8") as f:
            f.write("---\nproyecto: Externo\nworkstream: Kick\n---\n")
        self.wav("r2.wav")  # fuera de la vinculada
        self.write("r2.md", "---\nproyecto: Externo\nworkstream: Kick\n---\n")
        self.wav("r3.wav")
        self.write("r3.md", "---\nproyecto: Externo\nworkstream: Kick\n---\n")
        server.temas().set("r3.wav", f":{lid}/Vocabulario/Kick.txt")
        a = self.app()
        a._save_links = lambda: None
        with mock.patch.object(server, "log"):
            a.rename_folder({"path": ":" + lid, "name": "Cliente"})
        with open(os.path.join(ext, "r1.md"), encoding="utf-8") as f:
            self.assertEqual(server.parse_notes(f.read())[0], {"proyecto": "Cliente", "workstream": "Kick"})
        self.assertEqual(self.props("r2.md"), {"proyecto": "Cliente", "workstream": "Kick"})
        self.assertEqual(self.props("r3.md")["proyecto"], "Cliente")
        for audio in (os.path.join(ext, "r1.wav"), os.path.join(self.root, "r2.wav"),
                      os.path.join(self.root, "r3.wav")):
            self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "externo", audio)

    def test_renombrar_y_borrar_el_tema(self):
        self.write("Alfa/Vocabulario/General.txt", "API\n")
        old = "Alfa/Vocabulario/General.txt"
        a = self.app(rec_vocab=old, perfil={"vocab": old})
        with mock.patch.object(server, "log"):
            r = a.vocab_save({"id": old, "project": "Alfa", "name": "Comité", "terms": "API"})
        st = a.settings.get()
        self.assertEqual((st["perfil"]["vocab"], st["rec_vocab"]), (r["id"], r["id"]))
        self.assertEqual(r["id"], "Alfa/Vocabulario/Comité.txt")
        with mock.patch.object(server, "log"):
            a.vocab_delete({"id": r["id"]})
        st = a.settings.get()
        self.assertIsNone(st["perfil"]["vocab"])  # vuelve a «automático», no a «sin vocabulario»
        self.assertEqual(st["rec_vocab"], "")

    def test_un_tema_fijo_que_ya_no_existe_es_automatico(self):
        st = {"rec_vocab": "Beta/Vocabulario/X.txt", "perfil": {"vocab": "Alfa/Vocabulario/Borrada.txt"}}
        self.write("Beta/Vocabulario/X.txt", "x\n")
        self.assertEqual(server.perfil_vocab(st), "Beta/Vocabulario/X.txt")
        self.assertEqual(server.perfil_vocab(st, "Beta/Vocabulario/X.txt"), "Beta/Vocabulario/X.txt")
        self.assertEqual(server.resolve_perfil(st)["origen"]["vocab"], "ultimo")
        self.assertEqual(server.perfil_vocab({"perfil": {"vocab": ""}}, "Beta/Vocabulario/X.txt"), "")  # fijo: ninguno

    def test_renombrar_la_carpeta(self):
        self.write("Alfa/Vocabulario/General.txt", "API\n")
        os.makedirs(os.path.join(self.root, "Alfa", "Reuniones"))
        a = self.app(rec_folder="Alfa/Reuniones", rec_vocab="Alfa/Vocabulario/General.txt",
                     project_folders={"Alfa": "Alfa/Reuniones", "Beta": "Beta"},
                     perfil={"folder": "Alfa/Reuniones", "vocab": "Alfa/Vocabulario/General.txt"})
        self.assertEqual(a.rename_folder({"path": "Alfa", "name": "Alfa 2026"}), {"path": "Alfa 2026"})
        st = a.settings.get()
        self.assertEqual((st["perfil"]["folder"], st["perfil"]["vocab"]),
                         ("Alfa 2026/Reuniones", "Alfa 2026/Vocabulario/General.txt"))
        self.assertEqual((st["rec_folder"], st["rec_vocab"]),
                         ("Alfa 2026/Reuniones", "Alfa 2026/Vocabulario/General.txt"))
        self.assertEqual(st["project_folders"], {"Alfa 2026": "Alfa 2026/Reuniones", "Beta": "Beta"})
        self.assertEqual(server.perfil_folder(st), "Alfa 2026/Reuniones")
        self.assertEqual(server.perfil_vocab(st), "Alfa 2026/Vocabulario/General.txt")

    def test_renombrar_una_vinculada_con_listas(self):
        ext = os.path.join(self.tmp, "Ext")
        os.makedirs(os.path.join(ext, "Vocabulario"))
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "Externo", "path": ext}
        a = self.app(project_folders={"Externo": ":" + lid})
        a._save_links = lambda: None
        a.rename_folder({"path": ":" + lid, "name": "Cliente"})
        self.assertEqual(a.settings.get()["project_folders"], {"Cliente": ":" + lid})


# ----------------------------------------------------------------------------- P2-28

class FakeLocal:
    def __init__(self):
        self.langs = []

    def transcribe(self, abs_path, preset, language, vocab, threads, report, cancel, start=0.0, about=None):
        self.langs.append(language)
        return [(0.0, 1.0, "hola")], language, 1.0


class IdiomaDeCadaReunionTests(TempTree):
    """P2-28: «Transcribir todos los pendientes» usa por defecto el idioma de cada reunión."""

    def run_job(self, rel, language):
        jobs = bare_jobs([])
        jobs.settings = settings_store(self.tmp, language="es")
        jobs.library = types.SimpleNamespace(find=lambda r: {"duration": 1.0}, at=1)
        jobs.local, jobs.queues = FakeLocal(), {"local": types.SimpleNamespace(put=lambda j: None)}
        jobs.voice_vectors = {}
        with mock.patch.object(server, "log"):
            jobs.submit([rel], "rapido", language)
            job = jobs.list[-1]
            jobs._run(job)
        self.assertEqual(job.status, "done", job.error)
        return jobs.local.langs[-1]

    def test_cada_una_con_el_suyo(self):
        self.wav("a.wav")
        self.write("a.md", "---\nidioma: pt\n---\n")
        self.wav("b.wav")  # sin idioma propio: el del perfil
        self.wav("c.wav")
        self.write("c.md", "---\nidioma: auto\n---\n")  # detectar
        self.assertEqual(self.run_job("a.wav", None), "pt")
        self.assertEqual(self.run_job("b.wav", None), "es")
        self.assertIsNone(self.run_job("c.wav", None))
        self.assertEqual(self.run_job("a.wav", "en"), "en")  # uno elegido para todas manda
        self.assertIsNone(self.run_job("a.wav", ""))  # «Detectar» elegido a mano, también

    def test_la_caja_en_lote(self):
        app = read("app.js")
        self.assertIn("const TX_EACH = '__cada';", app)
        self.assertIn("[[TX_EACH, 'El de cada reunión (o el del perfil)']].concat(LANGS)", app)
        self.assertIn("lang === TX_EACH ? null : lang", app)
        self.assertNotIn("S.selLang = sel ? sel.value : '';", app)  # lo elegido en una caja no vale para las demás


# ----------------------------------------------------------------------------- P2-29

class CabeceraTolerante(unittest.TestCase):
    """P2-29: una clave que no conocemos (de una versión más nueva) no entierra la cabecera en las notas."""

    def test_claves_desconocidas_se_conservan(self):
        text = "---\ntitulo: Kick-off\nclave_nueva: algo: con dos puntos\nidioma: pt\n---\n\nnotas\n"
        props, body = server.parse_notes(text)
        self.assertEqual(props, {"titulo": "Kick-off", "clave_nueva": "algo: con dos puntos", "idioma": "pt"})
        self.assertEqual(body, "notas\n")
        out = server.dump_notes(dict(props, estado="Hecha"), body)
        self.assertEqual(out, "---\ntitulo: Kick-off\nestado: Hecha\nidioma: pt\nclave_nueva: algo: con dos puntos\n"
                              "---\n\nnotas\n")
        # lo que no es una cabecera sigue siendo texto
        for sep in ("---\nfoo: bar\n---\ntexto", "---\nUna frase: con dos puntos\n---\n", "---\nsin dos puntos\n---\n"):
            self.assertEqual(server.parse_notes(sep), ({}, sep), sep)


# ----------------------------------------------------------------------------- P2-31

class RenombrarCarpetaTests(TempTree):
    """P2-31: renombrar una carpeta no deja huérfanas las copias del original ni pierde «completa», las huellas en
    memoria ni los documentos de los destinos."""

    def test_todo_sigue_a_la_carpeta(self):
        self.wav("Alfa/Sub/r.wav")
        old_b = server.backup_dir("Alfa/Sub/r.wav")
        os.makedirs(old_b)
        server.COMPLETE.set("Alfa/Sub/r.wav")
        server.COMPLETE.set("Otra/r.wav")
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp)
        a.library = types.SimpleNamespace(at=1)
        a.jobs = bare_jobs([])
        a.jobs.voice_vectors = {"Alfa/Sub/r.wav": {"h1": [1.0]}}
        a.rec = None
        x = os.path.join(self.tmp, "vault", "r.md")
        a.jobs.registro.set(x, "sha", "Alfa/Sub/r.wav")
        a.rename_folder({"path": "Alfa", "name": "Gamma"})
        self.assertFalse(os.path.exists(old_b))
        self.assertTrue(os.path.isdir(server.backup_dir("Gamma/Sub/r.wav")))
        self.assertIn("Gamma/Sub/r.wav", server.COMPLETE)
        self.assertNotIn("Alfa/Sub/r.wav", server.COMPLETE)
        self.assertIn("Otra/r.wav", server.COMPLETE)
        self.assertEqual(a.jobs.voice_vectors, {"Gamma/Sub/r.wav": {"h1": [1.0]}})
        self.assertEqual(a.jobs.registro.rel_of(x), "Gamma/Sub/r.wav")


# ----------------------------------------------------------------------------- P2-33 / P2-34

class TextosTests(unittest.TestCase):
    def test_el_atajo_usa_el_perfil(self):
        """P2-33: el diálogo Grabar no dice que Ctrl+Alt+R use «esta misma configuración»."""
        app = read("app.js")
        self.assertNotIn("con esta misma configuración", app)
        self.assertIn("con el perfil de reunión por defecto (carpeta, idioma, tema y contexto)", app)

    def test_la_carpeta_de_la_app_no_se_llama_audios(self):
        """P2-34: la interfaz nombra la carpeta de la app con APP_NAME, no «Audios»."""
        app = read("app.js")
        for s in ("Otra carpeta (fuera de Audios)", "Carpetas fuera de Audios", "dentro de la carpeta Audios"):
            self.assertNotIn(s, app)
        self.assertIn("Otra carpeta (fuera de ${esc(APP_NAME)})…", app)
        self.assertIn("Carpetas fuera de ${esc(APP_NAME)}", app)


# ----------------------------------------------------------------------------- interfaz con node

def js_function(src, name):
    """Una función (también async) de un fichero JS, hasta su llave de cierre en la columna 0."""
    m = re.search(rf"^(async )?function {name}\(", src, re.M)
    assert m, name
    return src[m.start():src.index("\n}\n", m.start()) + 3]


@unittest.skipUnless(shutil.which("node"), "node no instalado")
class InterfazNodeTests(unittest.TestCase):
    def node(self, code):
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().splitlines()

    def test_idioma_de_la_caja_sin_lo_ultimo_elegido(self):
        """P1-10: la caja «Transcribir» de una reunión sin idioma propio ofrece el del perfil, no el último elegido."""
        defs = js_function(read("perfil.js"), "perfilLangOf")
        out = self.node(defs + "\nconst S = {selLang: 'pt', config: {language: 'es'}};\n"
                        "console.log(perfilLangOf({props: {}}), perfilLangOf({props: {idioma: 'en'}}),"
                        " JSON.stringify(perfilLangOf({props: {idioma: 'auto'}})));")
        self.assertEqual(out, ['es en ""'])

    def test_avisos_de_los_destinos(self):
        """P1-11: Ajustes enseña lo que no se ha escrito y qué hacer."""
        js = read("perfil.js")
        defs = js_defs(read("app.js"), ["esc"]) + "\nconst APP_NAME = 'Chuleta';\n"
        defs += js_function(js, "perfilAvisoText") + js_function(js, "perfilAvisosHtml")
        out = self.node(defs + """
console.log(JSON.stringify(perfilAvisosHtml([])));
const h = perfilAvisosHtml([{path: 'C:\\\\Vault\\\\a<b>.md', accion: 'cambiado'},
  {path: 'x.md', accion: 'dentro-de-la-app'}, {path: 'y.docx', accion: 'error: PermissionError'}]);
console.log((h.match(/<li>/g) || []).length, h.includes('a&lt;b&gt;.md'), h.includes('Bórralo allí'),
  h.includes('de Chuleta o de una carpeta vinculada'), h.includes('abierto en Word'));""")
        self.assertEqual(out, ['""', "3 true true true true"])

    def test_la_sugerencia_no_pisa_lo_que_eliges(self):
        """P2-30: si mientras se consulta la reunión en curso eliges tú el tema o la carpeta, la sugerencia de
        Teams/Outlook no los cambia."""
        fake = r"""
class El {
  constructor(id, value = '') { this.id = id; this.value = value; this.dataset = {}; this.ls = []; this.options = []; }
  q() { return new El('x'); }
  addEventListener(t, f) { if (t === 'change') this.ls.push(f); }
  dispatchEvent(e) { if (this.onchange) this.onchange(e); this.ls.forEach(f => f(e)); return true; }
  closest() { return null; }
  after() { }
}
const document = { body: { contains: () => true }, createElement: () => new El('nota') };
const esc = s => String(s);
let S = { route: { kind: 'home' } };
let pendiente;
const api = () => new Promise(r => { pendiente = r; });
function dialogo() {
  const els = { rName: new El('rName'), rVocab: new El('rVocab', ''), rProj: new El('rProj', ''),
                rFolder: new El('rFolder', 'Raiz'), rForm: new El('rForm') };
  els.rVocab.options = [{ value: '' }, { value: 'A/Vocabulario/G.txt' }, { value: 'B/Vocabulario/X.txt' }];
  els.rVocab.onchange = () => { els.rFolder.value = 'Carpeta de ' + els.rVocab.value; };  // applyVocab de app.js
  const d = new El('d');
  d.q = sel => els[sel.replace('#', '')] || null;
  return [d, els];
}
const $ = (sel, root) => root.q(sel);
const sug = { sugerencia: { source: 'teams', name: 'Comité', vocab: 'B/Vocabulario/X.txt', vocab_label: 'B · X',
                            participants: [] } };
const usuario = (el, v) => { el.value = v; el.dispatchEvent({ type: 'change', isTrusted: true }); };
(async () => {
  let [d, e] = dialogo();                                  // 1. nadie toca nada: se aplica la sugerencia
  let p = reunionPrefill(d); pendiente(sug); await p;
  console.log(e.rVocab.value, e.rFolder.value, e.rName.value);
  [d, e] = dialogo();                                      // 2. eliges tú el tema mientras se consulta
  p = reunionPrefill(d); usuario(e.rVocab, 'A/Vocabulario/G.txt'); pendiente(sug); await p;
  console.log(e.rVocab.value, e.rFolder.value);
  [d, e] = dialogo();                                      // 3. eliges tú la carpeta: el tema sí, la carpeta no
  p = reunionPrefill(d); usuario(e.rFolder, 'Mía'); pendiente(sug); await p;
  console.log(e.rVocab.value, e.rFolder.value);
  [d, e] = dialogo(); S = { route: { kind: 'f' } };        // 4. abierto desde una carpeta: esa se queda
  p = reunionPrefill(d); pendiente(sug); await p;
  console.log(e.rVocab.value, e.rFolder.value);
})();
"""
        out = self.node(fake + js_function(read("reunion.js"), "reunionPrefill"))
        self.assertEqual(out, ["B/Vocabulario/X.txt Carpeta de B/Vocabulario/X.txt Comité",
                               "A/Vocabulario/G.txt Carpeta de A/Vocabulario/G.txt",
                               "B/Vocabulario/X.txt Mía",
                               "B/Vocabulario/X.txt Raiz"])


if __name__ == "__main__":
    unittest.main()
