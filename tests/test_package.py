# -*- coding: utf-8 -*-
"""Paquete instalable (build_package), plantillas de _app/export y coherencia de requirements/instalador."""
import importlib.util
import io
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
        """La app es agnóstica: nada de clientes, empresas ni personas en ningún fichero del repo (código, tests,
        scripts, workflows, CHANGELOG, README y LEEME)."""
        # La lista es privada (los clientes de cada usuario) y nunca se escribe en el repo: expresiones regulares
        # separadas por comas (o una por línea) en la variable de entorno NOTAS_PALABRAS_VETADAS, sin distinguir
        # mayúsculas y con ^ y $ en cada línea. Sin ella, el test se salta, salvo con NOTAS_EXIGIR_VETADAS=1, que lo
        # hace fallar (así una ruta mal escrita no lo deja saltado entre los demás). En la CI, tests.yml pone la lista
        # solo en el paso de este test, desde el secreto de Actions del mismo nombre, y no la exige: sin el secreto, el
        # test se salta y, con NOTAS_AVISAR_SIN_VETADAS, deja él mismo el ::warning del resumen del run
        # (AVISO_SIN_VETADAS), y el paso sale en verde. En local, con la lista en un fichero fuera del repo (escrita en
        # la orden quedaría en el historial):
        #   macOS/Linux:
        #     NOTAS_EXIGIR_VETADAS=1 NOTAS_PALABRAS_VETADAS="$(cat ~/chuleta-vetadas.txt)" .venv/bin/python tests/run.py
        #   Windows (PowerShell, desde la raíz del repo):
        #     $env:NOTAS_EXIGIR_VETADAS = "1"
        #     $env:NOTAS_PALABRAS_VETADAS = Get-Content -Raw "$HOME\chuleta-vetadas.txt"; python tests\run.py
        # Revisa lo que git tiene en el índice (un fichero nuevo cuenta desde su `git add`); sin git, la raíz y _app,
        # tests, scripts, .github y design. Al fallar da fichero, línea y número de patrón, nunca el texto encontrado:
        # el log de la CI de un repo público es público.
        patrones = palabras_vetadas()
        if not patrones:
            if vetadas_exigidas():
                self.fail("NOTAS_PALABRAS_VETADAS falta o está vacía y NOTAS_EXIGIR_VETADAS la exige: revisa la ruta "
                          "del fichero con la lista (en la CI, el secreto de Actions del mismo nombre); sin ella no se "
                          "revisa nada")
            if avisar_sin_vetadas():  # el paso de tests.yml: que se vea en el resumen del run, no solo en su log
                print(AVISO_SIN_VETADAS, flush=True)
            self.skipTest("NOTAS_PALABRAS_VETADAS sin definir")
        hits = buscar_palabras(patrones, ficheros_del_repo())
        if hits:
            lineas = [f"  {rel}:{n} (patrón {k})" for rel, n, k in hits[:50]]
            if len(hits) > 50:
                lineas.append(f"  … y {len(hits) - 50} más")
            cuantas = "1 coincidencia" if len(hits) == 1 else f"{len(hits)} coincidencias"
            self.fail(f"{cuantas} con NOTAS_PALABRAS_VETADAS (sin el texto):\n" + "\n".join(lineas))

    def test_agnosticismo_mira_todo_el_repo_y_calla_lo_que_encuentra(self):
        """El test de agnosticismo revisa también CHANGELOG, tests, scripts y workflows, y al fallar no repite el texto
        encontrado. Se prueba con patrones inventados que casan con textos fijos de esos ficheros, nunca con la lista
        real."""
        marcas = [("CHANGELOG.md", r"^# Cambios"), ("README.md", r"^# Chuleta"), ("LEEME.txt", r"^chuleta · grabar"),
                  ("tests/run.py", r"MIN_TESTS = \d+"), ("scripts/build_zip.py", r"def version_string\("),
                  (".github/workflows/tests.yml", r"^name: tests\b")]
        encontrados = []
        for rel, patron in marcas:  # si cambia uno de esos ficheros, hay que cambiar su marca
            m = re.search(patron, self.read(*rel.split("/")).decode("utf-8-sig"), re.I)
            self.assertTrue(m, f"{patron!r} ya no casa con {rel}")
            encontrados.append(m.group(0))
        lista = ",".join(p for _, p in marcas[:3]) + "\n" + "\n".join(p for _, p in marcas[3:])  # comas y líneas
        resultado = unittest.TestResult()
        with mock.patch.dict(os.environ, {"NOTAS_PALABRAS_VETADAS": lista}):
            TemplatesTests("test_sin_rastro_de_clientes").run(resultado)
        self.assertEqual((resultado.testsRun, resultado.skipped, resultado.errors), (1, [], []))
        self.assertEqual(len(resultado.failures), 1)
        informe = resultado.failures[0][1]
        for k, (rel, _) in enumerate(marcas, 1):
            self.assertRegex(informe, r"(?m)^  " + re.escape(rel) + r":\d+ \(patrón " + str(k) + r"\)$")
        for texto in encontrados:
            self.assertNotIn(texto, informe)

    def test_agnosticismo_sin_git(self):
        """Sin git (una copia descargada sin .git) recorre los ficheros de la raíz del repo y _app, tests, scripts,
        .github y design, sin __pycache__, Vocabulario, binarios ni las carpetas de reuniones."""
        root = tempfile.mkdtemp(prefix="chuleta-agnostico-")
        ficheros = {"README.md": "marca", "CHANGELOG.md": "", "LEEME.txt": "", "_app/a.py": "", "_app/static/b.js": "",
                    "tests/c.py": "x\nmarca", "scripts/e2e/d.ps1": "", ".github/workflows/e.yml": "marca y marca",
                    "design/f.svg": "", "_app/__pycache__/g.py": "marca", "_app/Vocabulario/h.txt": "marca",
                    "Proyecto Alfa/reunion.md": "marca", "reunion suelta.srt": "marca"}
        for rel, texto in ficheros.items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
                f.write(texto)
        with open(os.path.join(root, "design", "logo.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\0\0marca")
        rels = ficheros_del_repo(root)
        self.assertEqual(set(rels), {"README.md", "CHANGELOG.md", "LEEME.txt", "_app/a.py", "_app/static/b.js",
                                     "tests/c.py", "scripts/e2e/d.ps1", ".github/workflows/e.yml", "design/f.svg",
                                     "design/logo.png"})
        self.assertEqual(buscar_palabras(["MARCA"], rels, root),
                         [(".github/workflows/e.yml", 1, 1), ("README.md", 1, 1), ("tests/c.py", 2, 1)])
        with self.assertRaises(ValueError) as cm:  # un patrón roto se dice por su número, sin enseñarlo
            buscar_palabras(["ok", "(sin cerrar"], rels, root)
        self.assertNotIn("sin cerrar", str(cm.exception))
        self.assertIn("patrón 2", str(cm.exception))

    def test_agnosticismo_exigido_sin_lista_falla(self):
        """Con NOTAS_EXIGIR_VETADAS (en local, para no dar por revisado lo que se ha saltado) una lista que falta o está
        vacía hace fallar el test en vez de saltarlo; sin ella, como en la CI, se salta."""
        casos = (({"NOTAS_EXIGIR_VETADAS": "true"}, "falla"),
                 ({"NOTAS_EXIGIR_VETADAS": "1", "NOTAS_PALABRAS_VETADAS": " , \n "}, "falla"),
                 ({"NOTAS_EXIGIR_VETADAS": "false", "NOTAS_PALABRAS_VETADAS": ""}, "salta"),  # la CI sin el secreto
                 ({}, "salta"))  # en local, sin la lista
        for env, esperado in casos:
            with self.subTest(env=env):
                entorno = {k: v for k, v in os.environ.items()
                           if k not in ("NOTAS_EXIGIR_VETADAS", "NOTAS_PALABRAS_VETADAS")}
                entorno.update(env)
                resultado = unittest.TestResult()
                with mock.patch.dict(os.environ, entorno, clear=True):
                    TemplatesTests("test_sin_rastro_de_clientes").run(resultado)
                self.assertEqual((resultado.testsRun, resultado.errors), (1, []))
                if esperado == "falla":
                    self.assertEqual((len(resultado.failures), resultado.skipped), (1, []))
                    self.assertIn("NOTAS_EXIGIR_VETADAS", resultado.failures[0][1])
                else:
                    self.assertEqual((resultado.failures, len(resultado.skipped)), ([], 1))

    def test_agnosticismo_sin_lista_avisa_si_se_pide(self):
        """Con NOTAS_AVISAR_SIN_VETADAS (el paso de tests.yml), una lista sin ningún patrón deja en la salida estándar,
        en una línea, el ::warning que GitHub enseña en el resumen del run, y el test se salta. Lo decide la misma
        palabras_vetadas() que lee la lista, también con blancos que no son ASCII (str.strip() quita \\x1c o el espacio
        duro, y bash con [[:space:]] no los veía). Sin ella (en local y en los jobs de la matriz) se salta callado."""
        for lista in (None, "", " , \n\t,", "\x1c,\xa0\n\u202f\x85"):
            for avisar in ("1", None):
                with self.subTest(lista=lista, avisar=avisar):
                    entorno = {k: v for k, v in os.environ.items() if not k.startswith("NOTAS_")}
                    if lista is not None:
                        entorno["NOTAS_PALABRAS_VETADAS"] = lista
                    if avisar:
                        entorno["NOTAS_AVISAR_SIN_VETADAS"] = avisar
                    resultado = unittest.TestResult()
                    with mock.patch.dict(os.environ, entorno, clear=True), \
                            mock.patch("sys.stdout", new_callable=io.StringIO) as salida:
                        TemplatesTests("test_sin_rastro_de_clientes").run(resultado)
                    self.assertEqual((resultado.testsRun, resultado.failures, resultado.errors, len(resultado.skipped)),
                                     (1, [], [], 1))
                    self.assertEqual(salida.getvalue(), AVISO_SIN_VETADAS + "\n" if avisar else "")
        self.assertTrue(AVISO_SIN_VETADAS.startswith("::warning title=Agnosticismo sin ejecutar::"))
        self.assertNotRegex(AVISO_SIN_VETADAS, "[%\r\n]")  # GitHub cortaría el mensaje (o habría que escaparlos)
        for texto in ("no se ha ejecutado", 'NOTAS_PALABRAS_VETADAS="$(cat ~/chuleta-vetadas.txt)" .venv/bin/python '
                                            "tests/run.py"):
            self.assertIn(texto, AVISO_SIN_VETADAS)

    def test_agnosticismo_ancla_por_linea(self):
        """^ y $ de un patrón marcan el principio y el fin de cada línea, no solo los del fichero, también en los
        ficheros con CRLF (.bat, .ps1 y LEEME.txt)."""
        root = tempfile.mkdtemp(prefix="chuleta-agnostico-")
        rels = ["a.txt", "b.bat", "c.md"]
        for rel, datos in zip(rels, (b"uno\nmarca\n", b"uno\r\nmarca\r\nfin\r\n", b"no marca\n")):
            with open(os.path.join(root, rel), "wb") as f:
                f.write(datos)
        self.assertEqual(buscar_palabras(["^marca$"], rels, root), [("a.txt", 2, 1), ("b.bat", 2, 1)])
        self.assertEqual(buscar_palabras(["marca$"], rels, root), [("a.txt", 2, 1), ("b.bat", 2, 1), ("c.md", 1, 1)])


def palabras_vetadas():
    """La lista privada de NOTAS_PALABRAS_VETADAS: expresiones regulares separadas por comas o por saltos de línea."""
    return [p.strip() for p in re.split(r"[,\n]", os.environ.get("NOTAS_PALABRAS_VETADAS", "")) if p.strip()]


def vetadas_exigidas():
    """NOTAS_EXIGIR_VETADAS («true» o «1»): sin lista, el test de agnosticismo falla en vez de saltarse. Es para
    ejecutarlo en local: tests.yml no la pone (sin el secreto, el test avisa y no falla). Para exigir el secreto en la
    CI iría en ese paso, solo en los push del repo propio (un fork y Dependabot no reciben los secretos), y habría que
    cambiar también AgnosticismoCITests (tests/test_ci.py)."""
    return os.environ.get("NOTAS_EXIGIR_VETADAS", "").strip().lower() in ("true", "1")


AVISO_SIN_VETADAS = (
    "::warning title=Agnosticismo sin ejecutar::El test de agnosticismo no se ha ejecutado: no ha llegado la lista "
    "del secreto NOTAS_PALABRAS_VETADAS o no trae ningún patrón (no existe o está vacío en Settings -> Secrets and "
    "variables -> Actions, o el run es de un fork o de Dependabot, que no reciben los secretos). En local, con la "
    "lista en un fichero fuera del repo: "
    'NOTAS_EXIGIR_VETADAS=1 NOTAS_PALABRAS_VETADAS="$(cat ~/chuleta-vetadas.txt)" .venv/bin/python tests/run.py '
    "(en Windows, ver test_sin_rastro_de_clientes en tests/test_package.py)")


def avisar_sin_vetadas():
    """NOTAS_AVISAR_SIN_VETADAS («true» o «1»), solo en el paso de tests.yml: sin lista, el test de agnosticismo deja
    AVISO_SIN_VETADAS en la salida estándar (GitHub lo enseña en el resumen del run) antes de saltarse."""
    return os.environ.get("NOTAS_AVISAR_SIN_VETADAS", "").strip().lower() in ("true", "1")


_RAIZ_SIN_GIT = ("CHANGELOG.md", "README.md", "LEEME.txt", "LICENSE", "INSTALAR.bat", "ABRIR CHULETA.bat",
                 "make_icons.py", "requirements.txt", "requirements-dev.txt", "setup.cfg", ".gitignore",
                 ".gitattributes")


def ficheros_del_repo(root=REPO):
    """Lo que publica el repo, relativo a `root` y con «/»: `git ls-files`. Sin git, los ficheros conocidos de la raíz
    y todo _app, tests, scripts, .github y design, sin __pycache__ ni Vocabulario (las carpetas de reuniones que haya
    en la raíz no son del repo)."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # p. ej. GIT_DIR desde un hook de git
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, env=env, timeout=60)
        if r.returncode == 0 and r.stdout.strip(b"\0"):
            return sorted(os.fsdecode(p) for p in r.stdout.split(b"\0") if p)
    except (OSError, subprocess.SubprocessError):
        pass
    rels = [n for n in _RAIZ_SIN_GIT if os.path.isfile(os.path.join(root, n))]
    for top in ("_app", "tests", "scripts", ".github", "design"):
        for d, dirs, names in os.walk(os.path.join(root, top)):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", "Vocabulario")]
            rels += [os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/") for n in names]
    return sorted(rels)


def buscar_palabras(patrones, rels, root=REPO):
    """[(fichero, línea, n.º de patrón)] de cada línea donde empieza una coincidencia de un patrón (sin distinguir
    mayúsculas; ^ y $ son el principio y el fin de cada línea), saltando los binarios. Nunca devuelve el texto
    encontrado: es justo lo que no puede acabar en un log público."""
    import bisect
    regs = []
    for k, p in enumerate(patrones, 1):
        try:
            regs.append(re.compile(p, re.I | re.M))
        except re.error:
            raise ValueError(f"el patrón {k} de NOTAS_PALABRAS_VETADAS no es una expresión regular válida") from None
    hits = []
    for rel in rels:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue  # en el índice de git pero borrado de la copia de trabajo
        with open(path, "rb") as f:
            data = f.read()
        if b"\0" in data[:8192]:
            continue  # binario (iconos, imágenes)
        text = data.decode("utf-8-sig", "replace").replace("\r\n", "\n")  # con CRLF, el \r no deja casar $
        saltos = None
        for k, rx in enumerate(regs, 1):
            inicios = [m.start() for m in rx.finditer(text)]
            if inicios:
                if saltos is None:
                    saltos = [m.start() for m in re.finditer("\n", text)]
                hits += [(rel, n, k) for n in sorted({bisect.bisect_left(saltos, i) + 1 for i in inicios})]
    return hits


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
