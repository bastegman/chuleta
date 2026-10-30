# -*- coding: utf-8 -*-
"""Paquete instalable (build_package), plantillas de _app/export y coherencia de requirements/instalador."""
import os
import re
import tempfile
import time
import unittest
import zipfile

from support import REPO, server


class BuildPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="notas-pkg-")
        cls.dest = os.path.join(cls.tmp, "Notas - instalador.zip")
        cls.n = server.build_package(cls.dest)
        cls.zip = zipfile.ZipFile(cls.dest)
        cls.names = set(cls.zip.namelist())

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()

    def test_layout(self):
        for n in ("Notas/INSTALAR.bat", "Notas/ABRIR NOTAS.bat", "Notas/LEEME.txt", "Notas/_app/server.py",
                  "Notas/_app/engines.py", "Notas/_app/worker.py", "Notas/_app/live.py", "Notas/_app/recorder.py",
                  "Notas/_app/Notas.bat", "Notas/_app/instalar.ps1", "Notas/_app/static/index.html",
                  "Notas/_app/static/app.js", "Notas/_app/static/notas.js", "Notas/_app/static/app.css",
                  "Notas/_app/static/notas.ico"):
            self.assertIn(n, self.names)
        self.assertEqual(self.n, len(self.names))
        self.assertTrue(all(n.startswith("Notas/") for n in self.names))
        self.assertFalse(any("Vocabulario" in n or n.endswith((".wav", ".pyc")) or "__pycache__" in n
                             for n in self.names))
        self.assertFalse(any("tests/" in n or n.endswith("README.md") for n in self.names))
        self.assertIn("Notas/LICENSE", self.names)  # MIT: el aviso acompaña a cada copia

    def test_crlf_bom_y_fecha(self):
        leeme = self.zip.read("Notas/LEEME.txt")
        self.assertTrue(leeme.startswith("﻿".encode("utf-8")))
        self.assertIn(b"\r\n", leeme)
        self.assertNotIn(b"{FECHA}", leeme)
        self.assertIn(time.strftime("%d/%m/%Y").encode(), leeme.split(b"\r\n")[0])
        ps1 = self.zip.read("Notas/_app/instalar.ps1")
        self.assertTrue(ps1.startswith("﻿".encode("utf-8")))
        self.assertNotIn(b"\n\n\r", ps1)
        self.assertFalse(re.search(rb"(?<!\r)\n", ps1))  # todo CRLF
        bat = self.zip.read("Notas/INSTALAR.bat")
        self.assertFalse(bat.startswith("﻿".encode("utf-8")))  # cmd.exe no quiere BOM
        self.assertFalse(re.search(rb"(?<!\r)\n", bat))
        py = self.zip.read("Notas/_app/server.py")
        self.assertNotIn(b"\r\n", py)  # el código va tal cual

    def test_extra(self):
        dest = os.path.join(self.tmp, "con-extra.zip")
        extra = os.path.join(self.tmp, "Legal.txt")
        with open(extra, "w", encoding="utf-8") as f:
            f.write("a, b\n")
        server.build_package(dest, [(extra, "Notas/Alfa/Vocabulario/Legal.txt")])
        with zipfile.ZipFile(dest) as z:
            self.assertEqual(z.read("Notas/Alfa/Vocabulario/Legal.txt"), "﻿a, b\r\n".encode("utf-8"))


class TemplatesTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(REPO, *parts), "rb") as f:
            return f.read()

    def test_raiz_y_export_coinciden(self):
        for name in ("INSTALAR.bat", "ABRIR NOTAS.bat"):
            self.assertEqual(self.read(name), self.read("_app", "export", name), name)
        root, tpl = self.read("LEEME.txt"), self.read("_app", "export", "LEEME.txt")
        m = re.search(rb"versi\xc3\xb3n del (\d\d/\d\d/\d{4})", root)
        self.assertTrue(m, "la 1.ª línea de LEEME.txt lleva «(versión del dd/mm/aaaa)»")
        self.assertEqual(tpl.replace(b"{FECHA}", m.group(1)), root)
        self.assertEqual(tpl.count(b"{FECHA}"), 1)

    def test_requirements_igual_que_el_instalador(self):
        ps1 = self.read("_app", "instalar.ps1").decode("utf-8-sig")
        pkgs = []
        for var in ("packages", "optional"):  # el core y la librería opcional de voces se instalan por separado
            m = re.search(r'\$' + var + r' = @\(([^)]*)\)', ps1)
            pkgs += re.findall(r'"([^"]+)"', m.group(1))
        pkgs.sort()
        req = sorted(l.strip() for l in self.read("requirements.txt").decode("utf-8").splitlines()
                     if l.strip() and not l.startswith("#"))
        self.assertEqual(pkgs, req)

    def test_sin_rastro_de_clientes(self):
        """La app es agnóstica: nada de clientes, empresas ni personas en código, textos ni manual."""
        # La lista de palabras es privada (clientes de cada usuario): expresiones regulares separadas por comas
        # en la variable de entorno NOTAS_PALABRAS_VETADAS; sin ella, el test se salta.
        words = [w.strip() for w in os.environ.get("NOTAS_PALABRAS_VETADAS", "").split(",") if w.strip()]
        if not words:
            self.skipTest("NOTAS_PALABRAS_VETADAS sin definir")
        bad = re.compile("|".join(words), re.I)
        files = ["LEEME.txt", "README.md", "INSTALAR.bat", "ABRIR NOTAS.bat"]
        for d in ("_app", os.path.join("_app", "static"), os.path.join("_app", "export")):
            files += [os.path.join(d, n) for n in os.listdir(os.path.join(REPO, d))
                      if n.endswith((".py", ".js", ".html", ".css", ".ps1", ".bat", ".txt"))]
        for rel in files:
            text = self.read(rel).decode("utf-8-sig", "replace")
            self.assertIsNone(bad.search(text), rel)


class BuildZipScriptTests(unittest.TestCase):
    def test_script_genera_el_zip_instalable(self):
        import subprocess
        import sys
        dest = os.path.join(tempfile.mkdtemp(prefix="notas-dist-"), "Notas - instalador.zip")
        r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "build_zip.py"), dest],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            self.assertIn("Notas/INSTALAR.bat", names)
            self.assertIn("Notas/ABRIR NOTAS.bat", names)
            self.assertIn("Notas/_app/server.py", names)
            self.assertIn("Notas/_app/static/app.js", names)
            self.assertIn("Notas/_app/export/LEEME.txt", names)
            self.assertFalse(any("Vocabulario" in n for n in names))
            leeme = z.read("Notas/LEEME.txt")
            self.assertIn(time.strftime("%d/%m/%Y").encode(), leeme.split(b"\r\n")[0])
            self.assertNotIn(b"{FECHA}", leeme)
            self.assertEqual(z.testzip(), None)


if __name__ == "__main__":
    unittest.main()
