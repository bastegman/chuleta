# -*- coding: utf-8 -*-
"""Paquete instalable (build_package), plantillas de _app/export y coherencia de requirements/instalador."""
import importlib.util
import os
import re
import tempfile
import time
import unittest
import zipfile
from unittest import mock

from support import REPO, server

VERSION_RE = rb"\d{4}-\d{2}-\d{2}(?: \xc2\xb7 [0-9a-f]{7,12})?"  # «2026-10-31 · abc1234» (sin git, solo la fecha)


def load_build_zip():
    spec = importlib.util.spec_from_file_location("build_zip", os.path.join(REPO, "scripts", "build_zip.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BuildPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chuleta-pkg-")
        cls.dest = os.path.join(cls.tmp, "Chuleta - instalador.zip")
        cls.n = server.build_package(cls.dest)
        cls.zip = zipfile.ZipFile(cls.dest)
        cls.names = set(cls.zip.namelist())

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()

    def test_layout(self):
        for n in ("Chuleta/INSTALAR.bat", "Chuleta/ABRIR CHULETA.bat", "Chuleta/LEEME.txt", "Chuleta/LICENSE",
                  "Chuleta/_app/server.py", "Chuleta/_app/engines.py", "Chuleta/_app/worker.py", "Chuleta/_app/live.py",
                  "Chuleta/_app/recorder.py", "Chuleta/_app/Chuleta.bat", "Chuleta/_app/instalar.ps1",
                  "Chuleta/_app/version.txt", "Chuleta/_app/static/index.html", "Chuleta/_app/static/app.js",
                  "Chuleta/_app/static/notas.js", "Chuleta/_app/static/app.css", "Chuleta/_app/static/chuleta.ico",
                  "Chuleta/_app/static/chuleta.png", "Chuleta/_app/calendario.py", "Chuleta/_app/aviso.py",
                  "Chuleta/_app/static/reunion.js", "Chuleta/_app/export/ABRIR CHULETA.bat"):
            self.assertIn(n, self.names)
        self.assertEqual(self.n, len(self.names))
        self.assertTrue(all(n.startswith("Chuleta/") for n in self.names))
        self.assertFalse(any("Notas" in n for n in self.names))  # nada del nombre anterior (notas.js es la función)
        self.assertFalse(any("Vocabulario" in n or n.endswith((".wav", ".pyc")) or "__pycache__" in n
                             for n in self.names))
        self.assertFalse(any("tests/" in n or n.endswith("README.md") for n in self.names))
        self.assertIn("Chuleta/LICENSE", self.names)  # MIT: el aviso acompaña a cada copia

    def test_crlf_bom_y_version(self):
        leeme = self.zip.read("Chuleta/LEEME.txt")
        self.assertTrue(leeme.startswith("﻿".encode("utf-8")))
        self.assertIn(b"\r\n", leeme)
        self.assertNotIn(b"{FECHA}", leeme)
        version = self.zip.read("Chuleta/_app/version.txt")
        self.assertFalse(version.startswith("﻿".encode("utf-8")))  # sin BOM: PowerShell 5 lo lee con -Encoding UTF8
        self.assertRegex(version, rb"^" + VERSION_RE + rb"\r\n$")
        self.assertIn(version.strip(), leeme.split(b"\r\n")[0])  # la 1.ª línea del LEEME lleva la misma versión
        ps1 = self.zip.read("Chuleta/_app/instalar.ps1")
        self.assertTrue(ps1.startswith("﻿".encode("utf-8")))
        self.assertNotIn(b"\n\n\r", ps1)
        self.assertFalse(re.search(rb"(?<!\r)\n", ps1))  # todo CRLF
        bat = self.zip.read("Chuleta/INSTALAR.bat")
        self.assertFalse(bat.startswith("﻿".encode("utf-8")))  # cmd.exe no quiere BOM
        self.assertFalse(re.search(rb"(?<!\r)\n", bat))
        py = self.zip.read("Chuleta/_app/server.py")
        self.assertNotIn(b"\r\n", py)  # el código va tal cual

    def test_version_explicita(self):
        """build_zip.py pasa la versión calculada del commit: va a _app/version.txt y al {FECHA} del LEEME."""
        dest = os.path.join(self.tmp, "con-version.zip")
        n = server.build_package(dest, version="2026-01-02 · abc1234")
        with zipfile.ZipFile(dest) as z:
            self.assertEqual(n, len(z.namelist()))
            self.assertEqual(z.read("Chuleta/_app/version.txt"), "2026-01-02 · abc1234\r\n".encode("utf-8"))
            primera = z.read("Chuleta/LEEME.txt").split(b"\r\n")[0].decode("utf-8-sig")
            self.assertIn("(versión 2026-01-02 · abc1234)", primera)
            self.assertIn(b"{FECHA}", z.read("Chuleta/_app/export/LEEME.txt"))  # la plantilla sigue con el marcador

    def test_version_por_defecto(self):
        """Sin versión explícita: la de _app/version.txt de esta copia (una copia instalada que vuelve a exportar) o,
        si no la tiene, la fecha de hoy."""
        with mock.patch.object(server, "version_file", return_value=None):
            self.assertEqual(server.package_version(), time.strftime("%Y-%m-%d"))
        with mock.patch.object(server, "version_file", return_value="2026-03-04 · 0123abc"):
            self.assertEqual(server.package_version(), "2026-03-04 · 0123abc")

    def test_extra(self):
        dest = os.path.join(self.tmp, "con-extra.zip")
        extra = os.path.join(self.tmp, "Legal.txt")
        with open(extra, "w", encoding="utf-8") as f:
            f.write("a, b\n")
        server.build_package(dest, [(extra, "Chuleta/Alfa/Vocabulario/Legal.txt")])
        with zipfile.ZipFile(dest) as z:
            self.assertEqual(z.read("Chuleta/Alfa/Vocabulario/Legal.txt"), "﻿a, b\r\n".encode("utf-8"))


class TemplatesTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(REPO, *parts), "rb") as f:
            return f.read()

    def test_raiz_y_export_coinciden(self):
        for name in ("INSTALAR.bat", "ABRIR CHULETA.bat"):
            self.assertEqual(self.read(name), self.read("_app", "export", name), name)
        root, tpl = self.read("LEEME.txt"), self.read("_app", "export", "LEEME.txt")
        m = re.search(rb"\(versi\xc3\xb3n (" + VERSION_RE + rb")\)", root.split(b"\r\n")[0])
        self.assertTrue(m, "la 1.ª línea de LEEME.txt lleva «(versión AAAA-MM-DD)» o «(versión AAAA-MM-DD · sha)»")
        self.assertEqual(tpl.replace(b"{FECHA}", m.group(1)), root)
        self.assertEqual(tpl.count(b"{FECHA}"), 1)
        self.assertIn(b"(versi\xc3\xb3n {FECHA})", tpl.split(b"\r\n")[0])
        for n in ("ABRIR NOTAS.bat", os.path.join("_app", "Notas.bat"),
                  os.path.join("_app", "export", "ABRIR NOTAS.bat"),
                  os.path.join("_app", "static", "notas.ico"), os.path.join("_app", "static", "notas.png")):
            self.assertFalse(os.path.exists(os.path.join(REPO, n)), n)  # renombrados con git mv

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
        files = ["LEEME.txt", "README.md", "INSTALAR.bat", "ABRIR CHULETA.bat"]
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
        dest = os.path.join(tempfile.mkdtemp(prefix="chuleta-dist-"), "Chuleta - instalador.zip")
        r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "build_zip.py"), dest],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            self.assertIn("Chuleta/INSTALAR.bat", names)
            self.assertIn("Chuleta/ABRIR CHULETA.bat", names)
            self.assertIn("Chuleta/LEEME.txt", names)
            self.assertIn("Chuleta/LICENSE", names)
            self.assertIn("Chuleta/_app/server.py", names)
            self.assertIn("Chuleta/_app/version.txt", names)
            self.assertIn("Chuleta/_app/static/app.js", names)
            self.assertIn("Chuleta/_app/export/LEEME.txt", names)
            self.assertFalse(any("Vocabulario" in n for n in names))
            version = z.read("Chuleta/_app/version.txt").strip()
            self.assertRegex(version, rb"^" + VERSION_RE + rb"$")
            leeme = z.read("Chuleta/LEEME.txt")
            self.assertIn(version, leeme.split(b"\r\n")[0])
            self.assertNotIn(b"{FECHA}", leeme)
            self.assertEqual(z.testzip(), None)
        self.assertIn("versión " + version.decode("utf-8"), r.stdout)
        with open(os.path.join(REPO, "_app", "version.txt"), "rb") as f:  # también en el repo (server.py lo enseña)
            self.assertEqual(f.read().strip(), version)

    def test_version_string(self):
        """«AAAA-MM-DD · sha» con git; sin git (o sin commits), la fecha de hoy a secas."""
        bz = load_build_zip()
        with mock.patch.object(bz, "git", side_effect=lambda *a: {"log": "2026-05-06", "rev-parse": "abc1234"}[a[0]]):
            self.assertEqual(bz.version_string(), "2026-05-06 · abc1234")
        with mock.patch.object(bz, "git", return_value=""):
            self.assertEqual(bz.version_string(), time.strftime("%Y-%m-%d"))
        if os.path.isdir(os.path.join(REPO, ".git")) or os.path.isfile(os.path.join(REPO, ".git")):
            self.assertRegex(bz.version_string(), r"^\d{4}-\d{2}-\d{2} · [0-9a-f]{7,12}$")

    def test_vocab_solo_si_se_pide(self):
        import subprocess
        import sys
        folder = tempfile.mkdtemp(prefix="chuleta-vocab-")
        os.makedirs(os.path.join(folder, "Proyecto Alfa", "Vocabulario"))
        os.makedirs(os.path.join(folder, "_app", "Vocabulario"))  # carpetas de sistema: no cuentan
        with open(os.path.join(folder, "Proyecto Alfa", "Vocabulario", "General.txt"), "w", encoding="utf-8") as f:
            f.write("# siglas\nAPI, SLA\n")
        with open(os.path.join(folder, "_app", "Vocabulario", "no.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        dest = os.path.join(folder, "privado.zip")
        r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "build_zip.py"), dest, "--vocab", folder],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            self.assertIn("Chuleta/Proyecto Alfa/Vocabulario/General.txt", names)
            self.assertEqual([n for n in names if "Vocabulario" in n],
                             ["Chuleta/Proyecto Alfa/Vocabulario/General.txt"])
            body = z.read("Chuleta/Proyecto Alfa/Vocabulario/General.txt")  # build_package deja los .txt con BOM y CRLF
            self.assertEqual(body.decode("utf-8-sig").splitlines(), ["# siglas", "API, SLA"])
        self.assertIn("1 listas de vocabulario", r.stdout)


if __name__ == "__main__":
    unittest.main()
