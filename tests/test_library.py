# -*- coding: utf-8 -*-
"""Rutas seguras, carpetas vinculadas, árbol de carpetas (scan + Library) y vocabulario."""
import os
import unittest

from support import TempTree, server, srt_text


def flatten(tree):
    out = {}
    stack = [tree]
    while stack:
        n = stack.pop()
        out[n["path"]] = n
        stack.extend(n["folders"])
    return out


class SafeTests(TempTree):
    def test_safe_dentro_de_root(self):
        self.write("Proyecto/a.txt", "x")
        self.assertEqual(server.safe("Proyecto/a.txt", "file"), os.path.join(self.root, "Proyecto", "a.txt"))
        self.assertEqual(server.safe(""), self.root)
        self.assertEqual(server.safe("\\Proyecto\\"), os.path.join(self.root, "Proyecto"))

    def test_safe_no_deja_salir(self):
        with self.assertRaises(server.ApiError) as cm:
            server.safe("../fuera.txt")
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(server.ApiError) as cm:
            server.safe("no-existe.txt", "file")
        self.assertEqual(cm.exception.code, 404)

    def test_links(self):
        ext = os.path.join(self.tmp, "OneDrive", "Proyecto")
        os.makedirs(os.path.join(ext, "sub"))
        lid = server.link_id(ext)
        server.LINKS[lid] = {"name": "Proyecto", "path": ext}
        rel = server.rel_of(os.path.join(ext, "sub", "a.wav"))
        self.assertEqual(rel, f":{lid}/sub/a.wav")
        self.assertEqual(server.safe(rel), os.path.join(ext, "sub", "a.wav"))
        self.assertEqual(server.rel_of(ext), ":" + lid)
        self.assertTrue(server.is_link_root(":" + lid))
        self.assertFalse(server.is_link_root(rel))
        with self.assertRaises(server.ApiError):
            server.safe(f":{lid}/../../x")
        with self.assertRaises(server.ApiError):
            server.safe(":zzzzzz/a")  # ya no vinculada
        self.assertEqual(server.rel_of(os.path.join(self.root, "x.wav")), "x.wav")


class ScanTests(TempTree):
    def setUp(self):
        super().setUp()
        self.wav("Reuniones/2026-10-30 10-00-00 - Kick-off.wav", seconds=120)
        self.write("Reuniones/2026-10-30 10-00-00 - Kick-off.srt", srt_text([(0, 1, "hola"), (1, 115, "adiós")]))
        self.write("Reuniones/2026-10-30 10-00-00 - Kick-off.txt", "hola\nadiós\n")
        self.write("Reuniones/2026-10-30 10-00-00 - Kick-off.md",
                   "---\ntitulo: Arranque\nworkstream: Legal\n---\n\nnotas\n")
        self.write("Reuniones/2026-10-30 10-00-00 - Kick-off.resumen.md", "---\nmodelo: m\n---\n\n## Resumen\n")
        self.wav("Reuniones/2026-10-29 09-00-00.wav", seconds=600)  # sin transcribir
        self.write("Reuniones/2026-10-29 09-00-00.srt", srt_text([(0, 1, "x"), (1, 30, "y")]))  # a medias
        self.write("Documentos/acta.md", "# acta\n")
        self.write("Documentos/.oculto.txt", "no")
        self.write("Documentos/~$temporal.txt", "no")
        self.write("_app/server.py", "# no es un documento\n")
        self.write("_app/static/x.txt", "no")

    def test_scan_agrupa_audios_sidecars_y_docs(self):
        tree, flat = server.scan()
        nodes = flatten(tree)
        self.assertEqual(sorted(nodes), ["", "Documentos", "Reuniones"])  # _app fuera
        a = {x["name"]: x for x in flat}
        k = a["2026-10-30 10-00-00 - Kick-off.wav"]
        self.assertEqual((k["txt"], k["srt"], k["has_summary"], k["label"], k["date"]),
                         (True, True, True, "Kick-off", "2026-10-30T10:00:00"))
        self.assertEqual(k["folder"], "Reuniones")
        self.assertEqual(nodes["Reuniones"]["docs"], [])  # los sidecars no son documentos
        self.assertEqual([d["name"] for d in nodes["Documentos"]["docs"]], ["acta.md"])
        self.assertEqual([x["name"] for x in nodes["Reuniones"]["audios"]],
                         ["2026-10-29 09-00-00.wav", "2026-10-30 10-00-00 - Kick-off.wav"])  # por fecha

    def test_library_refresh_props_duracion_y_partial(self):
        lib = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        tree, version = lib.refresh()
        self.assertEqual(len(version), 12)
        k = lib.find("Reuniones/2026-10-30 10-00-00 - Kick-off.wav")
        self.assertEqual(k["props"], {"titulo": "Arranque", "workstream": "Legal"})
        self.assertTrue(k["has_notes"])
        self.assertEqual(k["duration"], 120.0)
        self.assertEqual(k["tx_end"], 115.0)
        self.assertFalse(k["partial"])
        p = lib.find("Reuniones/2026-10-29 09-00-00.wav")
        self.assertEqual((p["duration"], p["tx_end"]), (600.0, 30.0))
        self.assertTrue(p["partial"])
        # misma versión si no cambia nada; el payload público no lleva rutas absolutas
        self.assertEqual(lib.refresh()[1], version)
        pub = server.strip_private(tree)
        self.assertNotIn("_abs", pub["folders"][1]["audios"][0])
        # marcada como completa: deja de estar "a medias" y la versión del árbol cambia
        server.COMPLETE.set("Reuniones/2026-10-29 09-00-00.wav")
        self.assertFalse(server.is_partial(p))
        self.assertNotEqual(lib.refresh()[1], version)

    def test_grabacion_en_curso(self):
        self.wav("Reuniones/2026-10-30 12-00-00.wav", seconds=30, open_header=True)
        lib = server.Library(server.Durations(os.path.join(self.data_dir, "durations.json")))
        lib.refresh()
        a = lib.find("Reuniones/2026-10-30 12-00-00.wav")
        self.assertTrue(a["recording"])
        self.assertEqual(a["duration"], 30.0)
        self.assertFalse(a["partial"])

    def test_carpeta_vinculada_en_el_arbol(self):
        ext = os.path.join(self.tmp, "Externa")
        os.makedirs(ext)
        with open(os.path.join(ext, "2026-01-01 10-00-00.wav"), "wb") as f:
            f.write(b"RIFF")
        server.LINKS[server.link_id(ext)] = {"name": "Externa", "path": ext}
        tree, flat = server.scan()
        self.assertEqual(tree["folders"][0]["name"], "Externa")
        self.assertEqual(tree["folders"][0]["linked"], ext)
        self.assertFalse(tree["folders"][0]["missing"])
        self.assertTrue(any(a["path"].startswith(":") for a in flat))


class VocabTests(TempTree):
    def test_list_vocab_y_find(self):
        self.write("Alfa/Vocabulario/General.txt", "# descripción\nAcme, Beta\n")
        self.write("Alfa/Vocabulario/Legal.txt", "Contrato marco, SLA\n")
        self.write("Vocabulario/General.txt", "raíz\n")
        vs = server.list_vocab()
        self.assertEqual([(v["project"], v["name"]) for v in vs],
                         [("Alfa", "General"), ("Alfa", "Legal"), (server.APP_NAME, "General")])
        v = server.find_vocab("Alfa", "legal")
        self.assertEqual(v["id"], "Alfa/Vocabulario/Legal.txt")
        self.assertEqual(server.read_vocab(v["_abs"]), ("", ["Contrato marco", "SLA"]))
        self.assertEqual(server.read_vocab(vs[0]["_abs"]), ("descripción", ["Acme", "Beta"]))
        self.assertIsNone(server.find_vocab("Otro", "General"))  # ambiguo: hay dos "General"
        info = server.vocab_info(v, "es")
        self.assertEqual((info["terms"], info["dropped"]), (2, []))
        self.assertNotIn("_abs", info)

    def test_vocab_for(self):
        self.write("Alfa/Vocabulario/Legal.txt", "Contrato marco, SLA\n")
        audio = self.wav("Alfa/2026-10-30 10-00-00.wav")
        self.write("Alfa/2026-10-30 10-00-00.md", "---\nproyecto: Alfa\nworkstream: Legal\n---\n")
        self.assertEqual(server.vocab_for(audio, {"prompt": "general"}), "Contrato marco, SLA")
        otro = self.wav("Alfa/2026-10-30 11-00-00.wav")
        self.assertEqual(server.vocab_for(otro, {"prompt": "general"}), "general")


if __name__ == "__main__":
    unittest.main()
