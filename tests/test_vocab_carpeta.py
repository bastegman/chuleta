# -*- coding: utf-8 -*-
"""Vocabulario desde una carpeta conectada (msg 1148): las listas .txt de una carpeta del PC se leen en vivo, sin copiar
nada, en tres formas (<carpeta>\\*.txt sueltas, <carpeta>\\Vocabulario\\*.txt y
<carpeta>\\<Proyecto>\\Vocabulario\\*.txt). Ids con prefijo propio (":v<id>/…"), fuera del árbol de reuniones y de
safe(), filtros (transcripciones, ocultas, solo en OneDrive, demasiado grandes), codificaciones de Windows, validación
de la carpeta, las rutas /api/vocab/link/add y /remove, /api/vocab con «carpetas», guardar y borrar temas allí, y que
«Copiar listas de otra carpeta» sigue copiando. Todo con dobles: sin red, sin Windows. Los nombres son inventados."""
import json
import os
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

from support import TempTree, server  # noqa: F401  (support prepara sys.path)
import calendario  # noqa: E402


def settings_store(tmp, **extra):
    st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
    if extra:
        st.update(lambda d: d.update(extra))
    return st


def write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write(text)
    return path


class CarpetaTests(TempTree):
    """Una carpeta de listas fuera de la app (p. ej. en OneDrive), conectada a mano en VOCAB_LINKS."""

    def setUp(self):
        super().setUp()
        self.ext = os.path.join(self.tmp, "OneDrive", "Listas Whisper")
        os.makedirs(self.ext)

    def connect(self, path=None, name=None):
        path = path or self.ext
        vid = server.link_id(path)
        server.VOCAB_LINKS[vid] = {"name": name or server.vocab_link_name(path), "path": path}
        return server.VLINK + vid

    def pares(self, vs=None):
        return [(v["project"], v["name"]) for v in (server.list_vocab() if vs is None else vs)]


class TresFormasTests(CarpetaTests):
    def test_sueltas_con_el_nombre_de_la_carpeta_y_general_primero(self):
        write(os.path.join(self.ext, "Infra.txt"), "Kubernetes, VPN\n")
        write(os.path.join(self.ext, "General.txt"), "# del comité\nAcme\nZeta Corp\n")  # una por línea también vale
        write(os.path.join(self.ext, "notas.md"), "no es una lista\n")
        rel = self.connect()
        vs = server.list_vocab()
        self.assertEqual(self.pares(vs), [("Listas Whisper", "General"), ("Listas Whisper", "Infra")])
        self.assertEqual([v["id"] for v in vs], [rel + "/General.txt", rel + "/Infra.txt"])
        self.assertTrue(all(v["conectada"] == rel and v["folder"] == rel for v in vs))
        self.assertEqual(server.read_vocab(vs[0]["_abs"]), ("del comité", ["Acme", "Zeta Corp"]))

    def test_carpeta_vocabulario_y_proyectos_con_su_vocabulario(self):
        write(os.path.join(self.ext, "Vocabulario", "Legal.txt"), "Contrato marco\n")
        write(os.path.join(self.ext, "Proyecto Alfa", "Vocabulario", "General.txt"), "API, SLA\n")
        write(os.path.join(self.ext, "Proyecto Alfa", "Vocabulario", "Datos.txt"), "ETL\n")
        write(os.path.join(self.ext, "Proyecto Alfa", "Actas", "Vocabulario", "X.txt"), "muy hondo: no\n")
        write(os.path.join(self.ext, "_privado", "Vocabulario", "Y.txt"), "de sistema: no\n")
        rel = self.connect()
        vs = server.list_vocab()
        self.assertEqual(self.pares(vs), [("Proyecto Alfa", "General"), ("Proyecto Alfa", "Datos"),
                                          ("Listas Whisper", "Legal")])  # en el orden de las carpetas, como siempre
        self.assertEqual(vs[0]["id"], rel + "/Proyecto Alfa/Vocabulario/General.txt")
        self.assertEqual(vs[0]["folder"], rel + "/Proyecto Alfa")
        self.assertEqual(server._project_dir(vs[0]), os.path.join(self.ext, "Proyecto Alfa"))
        self.assertEqual(server._project_dir(vs[2]), self.ext)

    def test_una_carpeta_vocabulario_toma_el_nombre_de_la_de_encima(self):
        voc = os.path.join(self.tmp, "OneDrive", "Proyecto Beta", "Vocabulario")
        write(os.path.join(voc, "General.txt"), "Beta\n")
        self.assertEqual(server.vocab_link_name(voc), "Proyecto Beta")
        self.connect(voc)
        self.assertEqual(self.pares(), [("Proyecto Beta", "General")])

    def test_la_de_la_app_y_las_vinculadas_siguen_igual_y_van_antes(self):
        self.write("Alfa/Vocabulario/General.txt", "API\n")
        ext2 = os.path.join(self.tmp, "Vinculada")
        write(os.path.join(ext2, "Vocabulario", "Legal.txt"), "SLA\n")
        server.LINKS[server.link_id(ext2)] = {"name": "Cliente", "path": ext2}
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        self.connect()
        vs = server.list_vocab()
        self.assertEqual(self.pares(vs), [("Alfa", "General"), ("Cliente", "Legal"), ("Listas Whisper", "Infra")])
        self.assertEqual(vs[0]["id"], "Alfa/Vocabulario/General.txt")
        self.assertNotIn("conectada", vs[0])
        self.assertNotIn("conectada", vs[1])

    def test_una_que_contiene_una_vinculada_no_repite_sus_listas(self):
        dentro = os.path.join(self.ext, "Proyecto")
        write(os.path.join(dentro, "Vocabulario", "General.txt"), "API\n")
        lid = server.link_id(dentro)
        server.LINKS[lid] = {"name": "Proyecto", "path": dentro}
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        self.connect()
        vs = server.list_vocab()
        self.assertEqual(self.pares(vs), [("Proyecto", "General"), ("Listas Whisper", "Infra")])
        self.assertEqual(vs[0]["id"], f":{lid}/Vocabulario/General.txt")  # el de la vinculada, una sola vez

    def test_sin_carpeta_no_falla(self):
        self.connect(os.path.join(self.tmp, "ya no existe"))
        self.assertEqual(server.list_vocab(), [])
        e = server.vocab_links_estado()
        self.assertEqual((len(e), e[0]["existe"], e[0]["listas"]), (1, False, 0))


class IdsTests(CarpetaTests):
    def test_ids_con_prefijo_propio_y_fuera_de_safe(self):
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        rel = self.connect()
        self.assertRegex(rel, r"^:v[0-9a-f]{6}$")
        self.assertRegex(server.link_id(self.ext), r"^[0-9a-f]{6}$")  # el de LINKS es hexadecimal: nunca «v…»
        p = os.path.join(self.ext, "Infra.txt")
        self.assertEqual(server.rel_of(p), rel + "/Infra.txt")
        self.assertEqual(server.vocab_link_path(rel + "/Infra.txt"), p)
        self.assertEqual(server.vocab_link_path(rel), self.ext)
        with self.assertRaises(server.ApiError) as cm:
            server.safe(rel + "/Infra.txt")  # ahí no se graba ni se mueve nada
        self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(server.ApiError) as cm:
            server.vocab_link_path(rel + "/../../fuera.txt")
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(server.ApiError):
            server.vocab_link_path(":vzzzzzz/x.txt")
        self.assertEqual(server.rel_of(os.path.join(self.root, "x.wav")), "x.wav")  # lo de la app, igual

    def test_find_vocab_y_vocab_for_de_una_reunion(self):
        write(os.path.join(self.ext, "Infra.txt"), "Kubernetes, VPN\n")
        rel = self.connect()
        v = server.find_vocab(None, None, rel + "/Infra.txt")
        self.assertEqual((v["project"], v["name"]), ("Listas Whisper", "Infra"))
        self.assertEqual(server.find_vocab("Listas Whisper", "infra")["id"], rel + "/Infra.txt")
        audio = self.wav("Reuniones/2026-11-07 10-00-00.wav")
        self.write("Reuniones/2026-11-07 10-00-00.md", "---\nproyecto: Listas Whisper\nworkstream: Infra\n---\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "Kubernetes, VPN")

    def test_se_lee_en_vivo(self):
        p = write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        self.connect()
        audio = self.wav("R/2026-11-07 10-00-00.wav")
        self.write("R/2026-11-07 10-00-00.md", "---\nproyecto: Listas Whisper\nworkstream: Infra\n---\n")
        self.assertEqual(server.vocab_for(audio, {}), "VPN")
        write(p, "VPN, Proxy, Firewall\n")
        self.assertEqual(server.vocab_for(audio, {}), "VPN, Proxy, Firewall")  # sin caché: el siguiente trozo ya
        write(os.path.join(self.ext, "Nueva.txt"), "Z\n")
        self.assertIn(("Listas Whisper", "Nueva"), self.pares())

    def test_las_listas_sueltas_no_hacen_suyas_las_reuniones_de_la_carpeta_de_encima(self):
        """_project_dir de una lista suelta es la carpeta conectada, no la de encima: si no, una reunión guardada al
        lado (en OneDrive) se tomaría por de ese proyecto al desempatar dos proyectos que se llaman igual."""
        write(os.path.join(self.ext, "General.txt"), "A\n")
        self.connect(name="Alfa")
        self.write("Alfa/Vocabulario/General.txt", "B\n")
        near = os.path.join(self.tmp, "OneDrive", "Reuniones", "2026-11-07 10-00-00.wav")
        vs = [v for v in server.list_vocab() if v["name"] == "General"]
        self.assertEqual(server._project_dir(vs[1]), self.ext)
        self.assertIsNone(server._closest(vs, near))


class FiltrosTests(CarpetaTests):
    def test_ocultas_y_transcripciones_de_grabaciones_fuera(self):
        write(os.path.join(self.ext, "._Infra.txt"), "basura de un USB de Mac\n")
        write(os.path.join(self.ext, "~Infra.txt"), "temporal\n")
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        with open(os.path.join(self.ext, "2026-11-07 10-00-00.wav"), "wb") as f:
            f.write(b"RIFF")
        write(os.path.join(self.ext, "2026-11-07 10-00-00.txt"), "hola, qué tal\n")
        write(os.path.join(self.ext, "2026-11-07 10-00-00.hablantes.txt"), "Ana: hola\n")
        self.connect()
        self.assertEqual(self.pares(), [("Listas Whisper", "Infra")])

    def test_demasiado_grande_con_aviso(self):
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        write(os.path.join(self.ext, "Glosario.txt"), "x, " * (server.VOCAB_MAX_BYTES // 3 + 10))
        self.connect()
        avisos = {}
        vs = server._list_vocab(avisos)
        self.assertEqual(self.pares(vs), [("Listas Whisper", "Infra")])
        (aviso,) = next(iter(avisos.values()))
        self.assertEqual((aviso["lista"], aviso["motivo"]), ("Glosario.txt", "grande"))
        self.assertGreater(aviso["kb"], 64)
        e = server.vocab_links_estado()[0]
        self.assertEqual((e["listas"], [a["motivo"] for a in e["avisos"]]), (1, ["grande"]))

    def test_solo_en_onedrive_se_salta_sin_leerla(self):
        """Leer un fichero «solo en línea» lo descargaría (y sin conexión puede bloquear): se salta con aviso."""
        class Entry:
            def __init__(self, path, attrs, size=10):
                self.path, self.name, self._st = path, os.path.basename(path), types.SimpleNamespace(
                    st_file_attributes=attrs, st_size=size)

            def stat(self):
                return self._st
        avisos = []
        p = os.path.join(self.ext, "Sub", "Infra.txt")
        self.assertFalse(server._vocab_link_ok(Entry(p, 0x400000), self.ext, set(), avisos))
        self.assertEqual(avisos, [{"lista": "Sub\\Infra.txt", "motivo": "nube"}])
        self.assertTrue(server._vocab_link_ok(Entry(p, 0x20), self.ext, set(), avisos))  # archivo normal
        self.assertEqual(len(avisos), 1)

    def test_las_de_la_app_no_pasan_por_los_filtros(self):
        self.write("Alfa/Vocabulario/._Infra.txt", "x\n")  # como hasta ahora
        self.assertEqual(self.pares(), [("Alfa", "._Infra")])


class CodificacionesTests(CarpetaTests):
    def test_utf8_cp1252_utf16_y_tabuladores(self):
        cases = {"utf8.txt": ("José Peña, Zoë\n".encode("utf-8"), ["José Peña", "Zoë"]),
                 "bom.txt": ("\ufeffJosé Peña\n".encode("utf-8"), ["José Peña"]),
                 "ansi.txt": ("José Peña; Müller\r\n".encode("cp1252"), ["José Peña", "Müller"]),
                 "excel.txt": ("Nombre\tEmpresa\r\nJosé\tAcmé\r\n".encode("utf-16"),
                               ["Nombre", "Empresa", "José", "Acmé"]),
                 "mac.txt": (b"Uno\rDos\r", ["Uno", "Dos"])}
        for name, (raw, terms) in cases.items():
            p = os.path.join(self.ext, name)
            with open(p, "wb") as f:
                f.write(raw)
            self.assertEqual(server.read_vocab(p)[1], terms, name)

    def test_vocab_info_cuenta_los_terminos_bien_leidos(self):
        with open(os.path.join(self.ext, "Equipo.txt"), "wb") as f:
            f.write("Íñigo, Begoña\r\n".encode("cp1252"))
        self.connect()
        (v,) = server.list_vocab()
        info = server.vocab_info(v, "es")
        self.assertEqual((info["terms"], info["text"], info["conectada"]), (2, "Íñigo, Begoña", v["conectada"]))
        self.assertNotIn("_abs", info)


class CheckCarpetaTests(CarpetaTests):
    def test_una_carpeta_normal_vale(self):
        self.assertEqual(server.check_vocab_link(' "' + self.ext + '" '), self.ext)

    def test_lo_que_no_vale(self):
        os.makedirs(os.path.join(self.root, "Listas"))
        vinc = os.path.join(self.tmp, "Vinculada")
        os.makedirs(os.path.join(vinc, "Sub"))
        server.LINKS[server.link_id(vinc)] = {"name": "Cliente", "path": vinc}
        otra = os.path.join(self.tmp, "Otra")
        os.makedirs(os.path.join(otra, "Dentro"))
        self.connect(otra)
        grab = os.path.join(self.tmp, "Grabaciones")
        os.makedirs(grab)
        with open(os.path.join(grab, "a.m4a"), "wb") as f:
            f.write(b"x")
        docs = os.path.join(self.tmp, "Actas")
        os.makedirs(os.path.join(docs, "Proyecto"))
        cases = [(os.path.join(self.tmp, "no-existe"), "no existe"), ("relativa", "no existe"), ("", "no existe"),
                 (self.app_dir, "propia app"), (self.root, "demasiado grande"), (self.tmp, "demasiado grande"),
                 (os.path.join(self.root, "Listas"), "ya está dentro"), (os.path.join(vinc, "Sub"), "«Cliente»"),
                 (os.path.join(otra, "Dentro"), "Se solapa"), (grab, "grabaciones"),
                 (os.path.join(docs, "Proyecto"), "destinos de salida")]
        for path, msg in cases:
            with self.assertRaises(server.ApiError, msg=path) as cm:
                server.check_vocab_link(path, outputs=[docs])
            self.assertIn(msg, str(cm.exception), path)
        self.assertEqual(server.check_vocab_link(otra), otra)  # la misma ya conectada: no se solapa consigo

    def test_un_destino_de_salida_no_puede_ser_la_carpeta_de_listas(self):
        self.connect()
        with self.assertRaises(server.ApiError) as cm:
            server.check_perfil({"outputs": [{"path": self.ext, "format": "txt"}]})
        self.assertIn("tus listas de vocabulario", str(cm.exception))
        fuera = os.path.join(self.tmp, "Documentos")
        os.makedirs(fuera)
        self.assertEqual(server.check_perfil({"outputs": [{"path": fuera}]})["outputs"][0]["path"], fuera)
        # uno que ya estaba guardado se acepta (si no, no se podría guardar ningún otro ajuste)
        r = server.check_perfil({"outputs": [{"path": self.ext}]}, known=[{"path": self.ext, "format": "md"}])
        self.assertEqual(r["outputs"][0]["path"], self.ext)


class ArbolTests(CarpetaTests):
    def test_no_sale_en_el_arbol_ni_en_las_carpetas_de_grabar(self):
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        self.wav("Reuniones/2026-11-07 10-00-00.wav")
        self.connect()
        tree, flat = server.scan()

        def names(node):
            for f in node.get("folders") or []:
                yield f["name"]
                yield from names(f)
        self.assertNotIn("Listas Whisper", list(names(tree)))
        self.assertFalse(any(a["path"].startswith(":") for a in flat))
        self.assertEqual(len(flat), 1)


class RutasTests(CarpetaTests):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls._app, cls._port, cls._token = server.Handler.app, server.PORT, server.Handler.token
        server.PORT = cls.port
        server.Handler.token = "prueba-vocab-carpeta"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.Handler.app, server.PORT, server.Handler.token = cls._app, cls._port, cls._token

    def setUp(self):
        super().setUp()
        a = server.App.__new__(server.App)
        a.settings = settings_store(self.tmp, language="es")
        a.vigia = calendario.Vigia()
        a.library = types.SimpleNamespace(at=1)
        server.Handler.app = a
        self.app = a

    def call(self, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST" if data else "GET")
        req.add_header(server.TOKEN_HEADER, server.Handler.token)
        if data:
            req.add_header("X-Audios", "1")
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def error(self, path, body):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call(path, body)
        return cm.exception.code, json.load(cm.exception)["error"]

    def test_conectar_listar_y_quitar(self):
        write(os.path.join(self.ext, "General.txt"), "Acme, Zeta\n")
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        write(os.path.join(self.ext, "Glosario.txt"), "x" * (server.VOCAB_MAX_BYTES + 1))
        with mock.patch.object(server, "log"):
            r = self.call("/api/vocab/link/add", {"path": self.ext})
        rel = r["rel"]
        self.assertEqual((r["name"], r["listas"], r["ya"], r["existe"]), ("Listas Whisper", 2, False, True))
        self.assertEqual([a["motivo"] for a in r["avisos"]], ["grande"])
        self.assertEqual(r["vocab_links"], [{"rel": rel, "name": "Listas Whisper", "path": self.ext}])
        self.assertEqual(self.app.settings.get()["vocab_links"], [{"name": "Listas Whisper", "path": self.ext}])
        with mock.patch.object(server, "log"):
            self.assertTrue(self.call("/api/vocab/link/add", {"path": self.ext})["ya"])  # otra vez: nada cambia
        self.assertEqual(len(self.app.settings.get()["vocab_links"]), 1)
        v = self.call("/api/vocab?language=es")
        self.assertEqual([(i["project"], i["name"], i["terms"]) for i in v["items"]],
                         [("Listas Whisper", "General", 2), ("Listas Whisper", "Infra", 1)])
        self.assertTrue(all(i["conectada"] == rel for i in v["items"]))
        self.assertEqual([(c["rel"], c["listas"]) for c in v["carpetas"]], [(rel, 2)])
        # el perfil y el último tema apuntan a una de sus listas: al quitarla vuelven a automático / ninguno
        self.app.settings.update(lambda d: d.update(rec_vocab=rel + "/Infra.txt",
                                                    perfil=dict(d["perfil"], vocab=rel + "/General.txt")))
        with mock.patch.object(server, "log"):
            r = self.call("/api/vocab/link/remove", {"rel": rel})
        self.assertEqual((r["ok"], r["vocab_links"]), (True, []))
        st = self.app.settings.get()
        self.assertEqual((st["vocab_links"], st["rec_vocab"], st["perfil"]["vocab"]), ([], "", None))
        self.assertEqual(sorted(os.listdir(self.ext)), ["General.txt", "Glosario.txt", "Infra.txt"])  # nada borrado
        self.assertEqual(self.call("/api/vocab")["items"], [])
        self.assertEqual(self.error("/api/vocab/link/remove", {"rel": rel})[0], 400)

    def test_sin_listas_no_se_conecta(self):
        write(os.path.join(self.ext, "notas.md"), "x\n")
        code, msg = self.error("/api/vocab/link/add", {"path": self.ext})
        self.assertEqual(code, 400)
        self.assertIn("no hay listas", msg)
        self.assertEqual(server.VOCAB_LINKS, {})
        self.assertEqual(self.app.settings.get()["vocab_links"], [])

    def test_el_destino_de_salida_no_se_conecta(self):
        write(os.path.join(self.ext, "General.txt"), "A\n")
        self.app.settings.update(lambda d: d.update(perfil=dict(d["perfil"], outputs=[
            {"path": self.ext, "format": "txt", "by_project": False}])))
        code, msg = self.error("/api/vocab/link/add", {"path": self.ext})
        self.assertEqual(code, 400)
        self.assertIn("destinos de salida", msg)

    def test_config_y_carga_al_arrancar(self):
        write(os.path.join(self.ext, "General.txt"), "A\n")
        self.app.settings.update(lambda d: d.update(vocab_links=[{"name": "Mis listas", "path": self.ext},
                                                                 {"path": ""}, "basura"]))
        self.app._load_vocab_links()
        rel = server.VLINK + server.link_id(self.ext)
        self.assertEqual(server.VOCAB_LINKS, {rel[2:]: {"name": "Mis listas", "path": self.ext}})
        self.assertEqual(self.app._vocab_links_public(), [{"rel": rel, "name": "Mis listas", "path": self.ext}])
        self.assertEqual(self.pares(), [("Mis listas", "General")])
        with open(server.__file__, encoding="utf-8") as f:
            src = f.read()
        cfg = src[src.index("    def config(self, q):"):src.index("    def tree(self, q):")]
        self.assertIn('"vocab_links": self._vocab_links_public()', cfg)
        init = src[src.index("class App:"):src.index("    def rec_toggle(self, b):")]
        self.assertIn("self._load_vocab_links()", init)

    def test_reveal_abre_la_carpeta_conectada(self):
        write(os.path.join(self.ext, "General.txt"), "A\n")
        rel = self.connect()
        with mock.patch.object(server.subprocess, "Popen") as popen:
            self.call("/api/reveal", {"path": rel})
        self.assertIn(self.ext, " ".join(map(str, popen.call_args[0][0])) if isinstance(
            popen.call_args[0][0], list) else popen.call_args[0][0])
        with mock.patch.object(server.subprocess, "Popen"):
            self.assertEqual(self.error("/api/reveal", {"path": rel + "/../../x"})[0], 403)


class GuardarEnLaCarpetaTests(RutasTests):
    def test_editar_crear_y_borrar_un_tema_de_la_carpeta(self):
        p = write(os.path.join(self.ext, "Infra.txt"), "VPN\nProxy\n")
        self.write("Listas Whisper/Vocabulario/Infra.txt", "otro proyecto con el mismo nombre\n")
        rel = self.connect()
        with mock.patch.object(server, "log"):
            r = self.call("/api/vocab/save", {"id": rel + "/Infra.txt", "project": "Listas Whisper", "name": "Infra",
                                              "terms": "VPN, Proxy, Firewall"})
        self.assertEqual(r["id"], rel + "/Infra.txt")  # se queda en su carpeta (antes se mudaba al primer proyecto)
        self.assertEqual(server.read_vocab(p)[1], ["VPN", "Proxy", "Firewall"])
        self.assertEqual(server.read_text(os.path.join(self.root, "Listas Whisper", "Vocabulario", "Infra.txt")),
                         "otro proyecto con el mismo nombre\n")
        with mock.patch.object(server, "log"):
            r = self.call("/api/vocab/save", {"id": rel + "/Infra.txt", "project": "Listas Whisper", "name": "Redes",
                                              "terms": "VPN"})
        self.assertEqual(r["id"], rel + "/Redes.txt")
        self.assertEqual(sorted(os.listdir(self.ext)), ["Redes.txt"])
        with mock.patch.object(server, "log"), mock.patch.object(server, "to_recycle_bin") as bin_:
            self.call("/api/vocab/delete", {"id": rel + "/Redes.txt"})
        bin_.assert_called_once_with([os.path.join(self.ext, "Redes.txt")])


class CopiarSigueCopiandoTests(CarpetaTests):
    def test_importar_copia_a_la_app_y_no_toca_la_conectada(self):
        write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        self.connect()
        usb = os.path.join(self.tmp, "USB")
        write(os.path.join(usb, "Proyecto Gamma", "Vocabulario", "Legal.txt"), "Contrato\n")
        with mock.patch.object(server, "log"):
            r = server.import_vocab(usb)
        self.assertEqual(r, {"copiadas": 1, "omitidas": 0, "proyectos": ["Proyecto Gamma"]})
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Proyecto Gamma", "Vocabulario", "Legal.txt")))
        self.assertEqual(os.listdir(self.ext), ["Infra.txt"])


class RefutadorTests(RutasTests):
    """Lo que encontró la revisión independiente de f6d2bdd (07-nov)."""

    def test_cambiar_el_proyecto_de_una_conectada_no_la_saca_de_su_carpeta(self):
        p = write(os.path.join(self.ext, "Infra.txt"), "VPN\n")
        rel = self.connect()
        code, msg = self.error("/api/vocab/save", {"id": rel + "/Infra.txt", "project": "Cliente Acme",
                                                   "name": "Infra", "terms": "VPN, Proxy"})
        self.assertEqual(code, 400)
        self.assertIn("carpeta de listas conectada", msg)
        self.assertEqual(server.read_text(p), "VPN\n")  # el fichero del usuario, intacto
        self.assertFalse(os.path.exists(os.path.join(self.root, "Cliente Acme")))

    def test_guardar_sin_cambios_no_reescribe_y_uno_por_linea_se_conserva(self):
        p = os.path.join(self.ext, "Equipo.txt")
        raw = "# Equipo del cliente\r\nJosé Peña\r\nBegoña Ruiz\r\n".encode("cp1252")
        with open(p, "wb") as f:
            f.write(raw)
        rel = self.connect()
        body = {"id": rel + "/Equipo.txt", "project": "Listas Whisper", "name": "Equipo", "desc": "Equipo del cliente",
                "terms": "José Peña, Begoña Ruiz"}
        with mock.patch.object(server, "log"):
            self.assertEqual(self.call("/api/vocab/save", body)["id"], rel + "/Equipo.txt")
        with open(p, "rb") as f:
            self.assertEqual(f.read(), raw)  # sin cambios: ni codificación ni formato tocados
        with mock.patch.object(server, "log"):
            self.call("/api/vocab/save", dict(body, terms="José Peña, Begoña Ruiz, Íñigo"))
        self.assertEqual(server.read_text(p), "# Equipo del cliente\nJosé Peña\nBegoña Ruiz\nÍñigo\n")

    def test_un_destino_que_contiene_la_carpeta_tampoco_vale(self):
        acme = os.path.join(self.tmp, "Clientes", "Acme")
        write(os.path.join(acme, "General.txt"), "A\n")
        self.connect(acme)
        with self.assertRaises(server.ApiError) as cm:
            server.check_perfil({"outputs": [{"path": os.path.dirname(acme), "format": "txt", "by_project": True}]})
        self.assertIn("Dentro de esa carpeta", str(cm.exception))

    def test_los_documentos_de_los_destinos_no_son_listas(self):
        write(os.path.join(self.ext, "General.txt"), "A\n")
        write(os.path.join(self.ext, "2026-11-07 10-00-00 - Semanal.txt"),
              "Semanal\ngenerado_por: Chuleta (desde la app; ...)\nFecha: 2026-11-07\n\nRESUMEN\n=======\nAlgo\n")
        self.connect(name="Acme")
        self.assertEqual(self.pares(), [("Acme", "General")])

    def test_repetidas_en_la_misma_carpeta_vale_la_primera_con_aviso(self):
        write(os.path.join(self.ext, "Infra.txt"), "suelta\n")
        write(os.path.join(self.ext, "Vocabulario", "Infra.txt"), "en Vocabulario\n")
        rel = self.connect()
        avisos = {}
        vs = server._list_vocab(avisos)
        self.assertEqual([v["id"] for v in vs], [rel + "/Infra.txt"])
        self.assertEqual(avisos[rel[2:]], [{"lista": "Vocabulario\\Infra.txt", "motivo": "repetida"}])
        self.assertEqual(server.find_vocab("Listas Whisper", "Infra")["id"], rel + "/Infra.txt")

    def test_conectar_la_carpeta_de_la_que_se_importaron_no_deja_sin_vocabulario(self):
        self.write("Acme/Vocabulario/Infra.txt", "Acme, SAP\n")  # importada antes
        audio = self.wav("Reuniones/2026-11-07 10-00-00.wav")
        self.write("Reuniones/2026-11-07 10-00-00.md", "---\nproyecto: Acme\nworkstream: Infra\n---\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "Acme, SAP")
        write(os.path.join(self.ext, "Infra.txt"), "Acme, SAP\n")
        self.connect(name="Acme")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "Acme, SAP")  # antes: «general»

    def test_vincular_y_desvincular_la_carpeta_de_encima_mueve_los_ids(self):
        proyecto = os.path.join(self.tmp, "OneDrive", "Proyecto Alfa")
        listas = os.path.join(proyecto, "Listas")
        write(os.path.join(listas, "General.txt"), "API, SLA\n")
        rel = self.connect(listas)
        audio = self.wav("R/2026-11-07 10-00-00.wav")
        self.app.settings.update(lambda d: d.update(rec_vocab=rel + "/General.txt",
                                                    perfil=dict(d["perfil"], vocab=rel + "/General.txt")))
        server.temas().set(server.rel_of(audio), rel + "/General.txt")
        self.app.jobs = types.SimpleNamespace(snapshot=lambda: [])
        self.app.rec = None
        with mock.patch.object(server, "log"):
            r = self.call("/api/link/add", {"path": proyecto})
        nuevo = r["rel"] + "/Listas/General.txt"
        st = self.app.settings.get()
        self.assertEqual((st["rec_vocab"], st["perfil"]["vocab"]), (nuevo, nuevo))
        self.assertEqual(server.temas().get(server.rel_of(audio)), nuevo)
        self.assertEqual(server.perfil_vocab_fijo(st), nuevo)  # Ctrl+Alt+R sigue con su vocabulario
        with mock.patch.object(server, "log"):
            self.call("/api/link/remove", {"path": r["rel"]})
        st = self.app.settings.get()
        self.assertEqual((st["rec_vocab"], st["perfil"]["vocab"]), (rel + "/General.txt", rel + "/General.txt"))
        self.assertEqual(server.temas().get(server.rel_of(audio)), rel + "/General.txt")

    def test_exportar_en_utf8_y_sin_repetidas(self):
        import zipfile
        with open(os.path.join(self.ext, "Equipo.txt"), "wb") as f:
            f.write("José Peña, Begoña\r\n".encode("cp1252"))
        with open(os.path.join(self.ext, "Excel.txt"), "wb") as f:
            f.write("Íñigo\tAcme\r\n".encode("utf-16"))
        self.connect(name="Alfa")
        self.write("Alfa/Vocabulario/Equipo.txt", "de la app\n")  # mismo proyecto y tema: va la primera
        tmp_listas = os.path.join(self.tmp, "temporal de listas")
        os.makedirs(tmp_listas)
        with mock.patch.object(server, "desktop_dir", return_value=self.tmp), \
                mock.patch.object(server, "WINDOWS", False), mock.patch("tempfile.mkdtemp", return_value=tmp_listas):
            r = self.call("/api/export", {"vocab": True})
        self.assertFalse(os.path.exists(tmp_listas))  # las copias en UTF-8 se borran al terminar
        self.assertEqual(r["vocab"], 2)
        with zipfile.ZipFile(r["path"]) as z:
            names = [n for n in z.namelist() if "/Vocabulario/" in n]
            self.assertEqual(sorted(names), [f"{server.PKG_ROOT}/Alfa/Vocabulario/Equipo.txt",
                                             f"{server.PKG_ROOT}/Alfa/Vocabulario/Excel.txt"])
            equipo = z.read(f"{server.PKG_ROOT}/Alfa/Vocabulario/Equipo.txt").decode("utf-8-sig")
            excel = z.read(f"{server.PKG_ROOT}/Alfa/Vocabulario/Excel.txt").decode("utf-8-sig")
        self.assertEqual(equipo, "de la app\r\n")  # la de la app va primero (y es la que lee el otro PC)
        self.assertEqual(excel, "Íñigo\tAcme\r\n")


class SugerenciaConUnaListaIlegibleTests(CarpetaTests):
    """Con el calendario (fix-calendario), una lista de la carpeta conectada que no se puede leer en ese momento
    (OneDrive a medio sincronizar, sin VPN) se salta y la sugerencia de la reunión sigue: nombre, tema y participantes
    (revisión de la integración del 08/11/2026: tumbaba la sugerencia entera y /api/reunion/actual daba 500)."""

    def test_se_salta_la_lista(self):
        self.write("Alfa/Vocabulario/General.txt", "Alfa, Comité\n")
        write(os.path.join(self.ext, "Legal.txt"), "Contrato\n")
        self.connect()
        real = server.read_vocab

        def leer(path):
            if path.endswith("Legal.txt"):
                raise PermissionError(13, "Permission denied")
            return real(path)
        a = server.App.__new__(server.App)
        cita = {"subject": "Comité Alfa", "organizer": "Ana", "attendees": ["Luis"], "categories": []}
        with mock.patch.object(server, "read_vocab", leer):
            terms = a._vocab_terms()
            r = calendario.reunion_actual({"outlook_on": True}, terms, teams=lambda: None,
                                          outlook_fn=lambda **k: (cita, None))
        self.assertEqual([v["name"] for v in terms], ["General"])
        self.assertEqual(r["sugerencia"]["participants"], ["Ana", "Luis"])
        self.assertEqual(r["sugerencia"]["vocab"], "Alfa/Vocabulario/General.txt")


class LecturaPorLineasTests(CarpetaTests):
    def test_un_byte_suelto_solo_cambia_su_linea(self):
        p = os.path.join(self.ext, "a.txt")
        with open(p, "wb") as f:
            f.write("\ufeff# Equipo de Begoña\nJosé Peña\n".encode("utf-8") + b"M\xfcller\n")
        self.assertEqual(server.read_vocab(p), ("Equipo de Begoña", ["José Peña", "M\ufffdller"]))  # con BOM
        with open(p, "wb") as f:
            f.write("# Equipo de Begoña\nJosé Peña\n".encode("utf-8") + b"M\xfcller\n")
        self.assertEqual(server.read_vocab(p), ("Equipo de Begoña", ["José Peña", "Müller"]))  # sin BOM: cp1252

    def test_saltos_de_linea_de_siempre(self):
        p = os.path.join(self.ext, "b.txt")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("Acme\x0bZeta Corp\x0bSAP\n# desc\u2028Beta, Gamma\n")
        self.assertEqual(server.read_vocab(p), ("desc", ["Acme", "Zeta Corp", "SAP", "Beta", "Gamma"]))


class SettingsRotosTests(unittest.TestCase):
    def test_se_rescatan_las_carpetas_de_listas(self):
        tmp = tempfile.mkdtemp(prefix="notas-vocab-")
        path = os.path.join(tmp, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"links": [{"name": "A", "path": "C:\\\\A"}], "vocab_links": [{"name": "L", "path": "D:\\\\L"}], '
                    '"language": "es", ROTO')
        with mock.patch.object(server, "log"):
            st = server.Store(path, server.DEFAULT_SETTINGS)
        self.assertEqual(st.get()["vocab_links"], [{"name": "L", "path": "D:\\L"}])
        self.assertEqual(st.get()["links"], [{"name": "A", "path": "C:\\A"}])
        self.assertIn("1 carpeta de listas", st.broken["recuperado"])


if __name__ == "__main__":
    unittest.main()
