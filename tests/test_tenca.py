# -*- coding: utf-8 -*-
"""Renombrado a Tenca (09/11/2026, Bernardo msg 1249): ninguna cadena que vea el usuario dice «Chuleta», y al instalar
encima de una Chuleta no queda nada con el nombre viejo (accesos directos, «Arrancar con Windows», la entrada de
«Aplicaciones instaladas», los .bat de la raíz) ni duplicados, sin mover los datos.

Por texto en cualquier sistema; las funciones del instalador, ejecutadas con PowerShell si lo hay (powershell.exe en
Windows; en macOS/Linux, pwsh o CHULETA_PWSH); las que crean accesos directos (.lnk, COM) o tocan el registro, solo en
Windows. El instalador entero, encima de una Chuleta de verdad (la de main 2242e63), lo prueba el E2E de Windows
(scripts/e2e/lookfeel_e2e.ps1, fase «v»)."""
import ast
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from support import APP, REPO, patched_paths, server
import common
from test_desinstalar import function_body, powershell, ps_text, run_ps_functions

WINDOWS = sys.platform == "win32"
INSTALAR = os.path.join(APP, "instalar.ps1")
DESINSTALAR = os.path.join(APP, "desinstalar.ps1")
VIEJO = re.compile(r"chuleta", re.I)
# identificadores internos que llevan el nombre anterior y NO cambian (no los ve el usuario; el porqué, en common.py):
# el AppUserModelID, la cabecera del token (el instalador nuevo apaga con ella la Chuleta abierta) y las variables de
# entorno de los procesos y de la CI
INTERNOS = re.compile(r"X-Chuleta-Token|Bernardo\.Chuleta|\bCHULETA_[A-Z0-9_*]*")
# PowerShell: las listas con los nombres anteriores, que el instalador y el desinstalador migran o quitan
PS_LEGADO = re.compile(r"^\s*\$(LegacyNames|LegacyFolders|LegacyLaunchers|LegacyUninstallKeys|RootLaunchers)\s*=")
# Python: las tuplas con los nombres anteriores (common.APP_FORMER_NAMES y el que tuvo «Arrancar con Windows»)
LEGADO_PY = ("APP_FORMER_NAMES", "ARRANQUE_ANTIGUOS")
# el aviso de la migración en el LEEME y el README (lo único que puede nombrar a Chuleta en un texto para el usuario)
AVISO_LEEME = ("cuando se llamaba Chuleta o Notas", "Si venías de Chuleta (el nombre anterior)",
               "C:\\Users\\<tu usuario>\\Chuleta)", "...\\Chuleta si venías de ahí")
AVISO_README = ("app, «Chuleta» y «Notas»", "Instalada encima de Chuleta, no queda nada con ese nombre",
                "su `ABRIR CHULETA.bat` se quitan", "(`%USERPROFILE%\\Chuleta`: renombrarla",
                "o `%USERPROFILE%\\Chuleta` si venía de ahí")


def py_cadenas(src):
    """(línea, texto) de cada cadena de un .py que no es un docstring (con las partes literales de los f-strings), sin
    las de `APP_FORMER_NAMES = (...)` y `ARRANQUE_ANTIGUOS = (...)` (son los nombres anteriores, a propósito)."""
    tree = ast.parse(src)
    docs, legado = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in LEGADO_PY for t in node.targets):
            legado.update(id(n) for n in ast.walk(node.value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs | legado]


def js_sin_comentarios(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    return re.sub(r"(?m)(^|[^:\\])//.*$", r"\1", text)


def ps1_sin_comentarios(text):
    """Cada línea sin su comentario (# fuera de comillas; no hay bloques <# #> en estos scripts)."""
    out = []
    for line in text.split("\n"):
        quote, i, cut = None, 0, len(line)
        while i < len(line):
            c = line[i]
            if quote:
                if c == "`" and quote == '"':
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "#" and (i == 0 or line[i - 1] in " \t;({"):
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return out


def restos(text):
    return VIEJO.search(INTERNOS.sub("", text))


class NombreTests(unittest.TestCase):
    def test_constantes(self):
        self.assertEqual(common.APP_NAME, "Tenca")
        self.assertEqual(common.APP_FORMER_NAMES, ("Chuleta", "Notas"))  # del más reciente al más antiguo
        self.assertEqual(common.APP_ID, "Bernardo.Chuleta")  # no cambia: los anclajes hechos lo llevan
        self.assertEqual(common.TOKEN_HEADER, "X-Chuleta-Token")  # no cambia: la Chuleta abierta solo acepta esta
        self.assertEqual(server.PKG_ROOT, "Tenca")
        self.assertEqual(server.Handler.server_version, "Tenca/1.0")
        self.assertEqual(server.REPO_URL, "https://github.com/bastegman/tenca")

    def test_proyecto_de_la_raiz_con_el_nombre_anterior(self):
        """Las reuniones y listas de la raíz grabadas con `proyecto: Chuleta` siguen siendo de la raíz."""
        for nombre in ("Tenca", "Chuleta", "chuleta", "Notas"):
            self.assertTrue(server._is_app_name(nombre), nombre)
        self.assertFalse(server._is_app_name("Proyecto Alfa"))

    def test_ficheros_renombrados(self):
        for rel in ("ABRIR TENCA.bat", "_app/export/ABRIR TENCA.bat", "_app/Tenca.bat", "_app/static/tenca.ico",
                    "_app/static/tenca.png"):
            self.assertTrue(os.path.isfile(os.path.join(REPO, rel)), rel)
        for rel in ("ABRIR CHULETA.bat", "_app/export/ABRIR CHULETA.bat", "_app/Chuleta.bat", "_app/static/chuleta.ico",
                    "_app/static/chuleta.png"):
            self.assertFalse(os.path.exists(os.path.join(REPO, rel)), rel)
        with open(os.path.join(REPO, "ABRIR TENCA.bat"), "rb") as f:
            self.assertIn(b'call "%~dp0_app\\Tenca.bat" %*', f.read())


class SinElNombreViejoTests(unittest.TestCase):
    """Ninguna cadena de usuario con «Chuleta» en lo que se instala (el zip) ni en el README: solo los identificadores
    internos (INTERNOS), las listas de nombres anteriores que se migran y el aviso de la migración."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tenca-pkg-")
        cls.dest = os.path.join(cls.tmp, "Tenca - instalador.zip")
        server.build_package(cls.dest, version="2026-11-09 · abc1234")
        cls.zip = zipfile.ZipFile(cls.dest)
        cls.names = cls.zip.namelist()

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_rutas_del_zip(self):
        self.assertTrue(all(n.startswith("Tenca/") for n in self.names))
        self.assertEqual([n for n in self.names if VIEJO.search(n)], [])

    def test_cadenas_de_cada_fichero(self):
        malos = []
        for n in self.names:
            data = self.zip.read(n)
            if n.endswith((".ico", ".png", ".svg")) or not VIEJO.search(data.decode("utf-8", "replace")):
                continue
            text = data.decode("utf-8-sig").replace("\r\n", "\n")
            ext = os.path.splitext(n)[1].lower()
            if ext == ".py":
                malos += [f"{n}:{ln}: {s[:80]!r}" for ln, s in py_cadenas(text) if restos(s)]
            elif ext in (".js", ".html", ".css"):
                malos += [f"{n}: {ln[:100]}" for ln in js_sin_comentarios(text).split("\n") if restos(ln)]
            elif ext == ".ps1":
                malos += [f"{n}:{i}: {ln[:100]}" for i, ln in enumerate(ps1_sin_comentarios(text), 1)
                          if restos(ln) and not PS_LEGADO.match(ln)]
            elif ext == ".bat":
                malos += [f"{n}: {ln}" for ln in text.split("\n")
                          if restos(ln) and not re.match(r"\s*(rem\b|::)", ln, re.I)]
            elif n.endswith("LEEME.txt"):
                plano = re.sub(r"\s+", " ", text)
                for frase in AVISO_LEEME:
                    plano = plano.replace(frase, "")
                malos += [f"{n}: …{plano[max(0, m.start() - 60):m.end() + 40]}…" for m in VIEJO.finditer(plano)]
            else:
                malos += [f"{n}: {ln[:100]}" for ln in text.split("\n") if restos(ln)]
        self.assertEqual(malos, [])

    def test_readme(self):
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as f:
            plano = re.sub(r"\s+", " ", f.read())
        for frase in AVISO_README:
            self.assertIn(frase, plano)
            plano = plano.replace(frase, "")
        plano = INTERNOS.sub("", plano)
        self.assertEqual([plano[max(0, m.start() - 60):m.end() + 40] for m in VIEJO.finditer(plano)], [])

    def test_el_escaner_ve_lo_que_debe(self):
        """Que el escáner no pase por no mirar: una cadena, un mensaje de PowerShell y un .bat con el nombre viejo caen;
        un comentario, un docstring y los identificadores internos, no."""
        self.assertEqual([s for _, s in py_cadenas('"""Chuleta."""\nx = "Abre Chuleta"  # Chuleta\n') if restos(s)],
                         ["Abre Chuleta"])
        self.assertEqual([s for _, s in py_cadenas('APP_FORMER_NAMES = ("Chuleta", "Notas")\n') if restos(s)], [])
        self.assertTrue(restos(ps1_sin_comentarios('Say "Chuleta lista" # comentario')[0]))
        self.assertFalse(restos(ps1_sin_comentarios('$h["X-Chuleta-Token"] = $t  # de Chuleta')[0]))
        self.assertFalse(restos(js_sin_comentarios("/* Chuleta */ const a = 1; // Chuleta")))
        self.assertTrue(restos(js_sin_comentarios("toast('Chuleta')")))


class InstaladorTextoTests(unittest.TestCase):
    """Lo que el instalador y el desinstalador saben del nombre anterior, por texto."""

    @classmethod
    def setUpClass(cls):
        cls.ps1 = ps_text(INSTALAR)
        cls.des = ps_text(DESINSTALAR)

    def lista(self, text, var):
        m = re.search(r"^\$" + var + r" = (.*)$", text, re.M)
        self.assertTrue(m, var)
        return re.findall(r'"([^"]+)"', m.group(1))

    def test_nombres_anteriores_iguales_que_common(self):
        self.assertEqual(tuple(self.lista(self.ps1, "LegacyNames")), common.APP_FORMER_NAMES)
        self.assertEqual(tuple(self.lista(self.des, "LegacyNames")), common.APP_FORMER_NAMES)
        self.assertIn('$LegacyNames = @(@("Chuleta", "Notas") | Where-Object { $_ -ne $AppName })', self.ps1)
        self.assertIn('$AppId = "%s"' % common.APP_ID, self.ps1)
        self.assertIn('[string]$AppName = "%s"' % common.APP_NAME, self.ps1)
        self.assertIn('$AppName = "%s"' % common.APP_NAME, self.des)

    def test_carpeta_lanzador_y_claves(self):
        self.assertIn('$FolderName = "Tenca"', self.ps1)
        self.assertEqual(self.lista(self.ps1, "LegacyFolders"), ["Chuleta"])
        self.assertIn('$Launcher = "ABRIR TENCA.bat"', self.ps1)
        self.assertEqual(self.lista(self.ps1, "LegacyLaunchers"), ["ABRIR CHULETA.bat", "ABRIR NOTAS.bat"])
        for text in (self.ps1, self.des):
            self.assertIn('$UninstallKey = "$UninstallBase\\Tenca"', text)
            self.assertIn('$LegacyUninstallKeys = @("$UninstallBase\\Chuleta")', text)
        self.assertEqual(self.lista(self.des, "RootLaunchers"),
                         ["INSTALAR.bat", "ABRIR TENCA.bat", "ABRIR CHULETA.bat", "ABRIR NOTAS.bat"])
        self.assertIn('"tenca-desinstalar.log"', self.des)
        self.assertIn('-like "tenca-desinstalar-*.ps1"', self.des)
        self.assertIn('("tenca-desinstalar-" + [guid]', self.des)

    def test_borra_solo_lo_que_sobra(self):
        """El instalador solo quita accesos directos antiguos (Remove-LnkAntiguos), los .bat antiguos de la raíz
        (Remove-LanzadoresAntiguos) y la entrada antigua de Aplicaciones instaladas (Remove-RegistroAntiguo); nunca
        recorre ni mueve nada, y renombra solo en Set-AccesoDirecto."""
        donde = {r"Remove-Item\b(?!Property)": ["Remove-LnkAntiguos", "Remove-LanzadoresAntiguos"],
                 r"DeleteSubKeyTree": ["Remove-RegistroAntiguo"], r"Rename-Item": ["Set-AccesoDirecto"],
                 r"Remove-ItemProperty": ["Copy-ArranqueAprobado"]}
        for orden, funciones in donde.items():
            dentro = sum(len(re.findall(orden, function_body(self.ps1, f))) for f in funciones)
            self.assertEqual(len(re.findall(orden, self.ps1)), dentro, orden)
            self.assertGreater(dentro, 0, orden)
        for ln in self.ps1.split("\n"):
            if re.search(r"Remove-Item\b(?!Property)", ln):
                self.assertRegex(ln, r"Remove-Item -LiteralPath \$(x\.path|f) -Force -ErrorAction Stop")
        self.assertNotRegex(self.ps1, r"Remove-Item[^\n]*-Recurse")
        self.assertNotIn("Move-Item", self.ps1)
        reg = function_body(self.ps1, "Remove-RegistroAntiguo")
        self.assertIn("foreach ($k in $LegacyUninstallKeys)", reg)
        self.assertIn("if ($pub -ne $Publisher) {", reg)  # la de otro programa con ese nombre no se toca

    def test_lanzador_antiguo_solo_si_es_de_la_app(self):
        body = function_body(self.ps1, "Remove-LanzadoresAntiguos")
        self.assertIn("if ($t.Length -gt 4096 -or $t -notmatch '_app\\\\')", body)
        self.assertIn("(Join-Path $rootDir $Launcher)", body)  # sin el nuevo en la raíz, no se quita el viejo
        self.assertIn("Remove-LanzadoresAntiguos $root\n\nStep \"Buscando Python", self.ps1)

    def test_orden(self):
        """La entrada antigua se quita después de escribir la nueva (si eso falla, la vieja sigue sirviendo); el
        arranque con Windows se migra después de crear el acceso directo de Inicio (es su copia) y antes de abrir la
        app."""
        reg = function_body(self.ps1, "Register-App")
        self.assertLess(reg.index("$rk.SetValue"), reg.index("Remove-RegistroAntiguo $rootDir"))
        self.assertLess(reg.index("return $false"), reg.index("Remove-RegistroAntiguo $rootDir"))
        i = self.ps1.index("foreach ($dir in $places) { Set-AccesoDirecto $dir $app $pyw $sinConsola }")
        j = self.ps1.index('Update-Arranque (Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk") $app')
        k = self.ps1.index("$null = Register-App $app $root $verRaw")
        m = self.ps1.index("if ($abrir) {")
        self.assertLess(i, j)
        self.assertLess(j, k)
        self.assertLess(k, m)
        # con -Desktop (otro PC simulado), nada del sistema
        self.assertIn("if (-not $Desktop) {\n        # \"Arrancar con Windows\"", self.ps1)

    def test_find_instalacion_mira_los_nombres_anteriores(self):
        body = function_body(self.ps1, "Find-Instalacion")
        self.assertIn("foreach ($k in @(@($UninstallKey) + $LegacyUninstallKeys))", body)
        self.assertIn("foreach ($n in @(@($AppName) + $LegacyNames))", body)
        self.assertIn("foreach ($f in @(@($FolderName) + $LegacyFolders))", body)

    def test_desinstalador_quita_los_antiguos(self):
        sc = function_body(self.des, "Remove-Shortcuts")
        self.assertIn('@(@("$AppName.lnk") + @($LegacyNames | ForEach-Object { "$_.lnk" }))', sc)
        self.assertIn('GetFolderPath("Startup")', sc)
        reg = function_body(self.des, "Remove-Registro")
        self.assertIn("foreach ($key in @(@($UninstallKey) + $LegacyUninstallKeys))", reg)
        self.assertIn("if ($loc -and -not (Same-Path $loc $Root))", reg)  # la de otra copia se queda
        # la del nombre anterior, solo si es de la app (su Publisher) y de esta carpeta
        self.assertIn('if ($key -ne $UninstallKey -and ("$($props.Publisher)" -ne "Bernardo Stegmaier" -or -not $loc))',
                      reg)

    def test_mensajes(self):
        self.assertIn('"$($script:antes) ahora se llama ${AppName}: tus reuniones, voces y ajustes siguen donde '
                      'estaban."', self.ps1)
        self.assertIn("Windows no deja que un instalador cambie un anclaje", function_body(self.ps1, "Report-Anclajes"))
        self.assertIn("User Pinned\\TaskBar", function_body(self.ps1, "Report-Anclajes"))


def lnk_de(carpeta, extra=b""):
    """Los bytes de un acceso directo de la app (como los que crea WScript.Shell: la carpeta de trabajo y los argumentos
    en UTF-16LE), con algo delante para que la ruta no empiece en un byte par."""
    return (b"L\x00\x00\x00\x01" + extra + os.path.join(carpeta, "server.py").encode("utf-16-le") + b"\x00\x00"
            + carpeta.encode("utf-16-le"))


class ArranqueConNombreAnteriorTests(unittest.TestCase):
    """server.inicio_windows_*: la copia de «Arrancar con Windows» con el nombre anterior (Chuleta.lnk) de ESTA copia de
    la app pasa a ser Tenca.lnk y nunca quedan las dos (la app se abriría dos veces al entrar en Windows); un acceso
    directo con ese nombre que no es de esta copia, o un Notas.lnk (en la época Notas no había arranque), no se toca."""

    def setUp(self):
        self.appdata = tempfile.mkdtemp(prefix="tenca-appdata-")
        self.addCleanup(shutil.rmtree, self.appdata, True)
        self.progs = os.path.join(self.appdata, "Microsoft", "Windows", "Start Menu", "Programs")
        self.startup = os.path.join(self.progs, "Startup")
        os.makedirs(self.startup)
        for p in (mock.patch.object(server, "WINDOWS", True), mock.patch.dict(os.environ, {"APPDATA": self.appdata}),
                  mock.patch.object(server, "log"), mock.patch.object(server, "shell_folder", return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        self.origen = os.path.join(self.progs, "Tenca.lnk")
        self.destino = os.path.join(self.startup, "Tenca.lnk")
        self.viejo = os.path.join(self.startup, "Chuleta.lnk")
        self.nuestro = lnk_de(server.APP_DIR)
        self.ajeno = lnk_de(os.path.join(self.appdata, "otra", "_app"))

    def write(self, path, data):
        with open(path, "wb") as f:
            f.write(data)

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_al_arrancar_la_antigua_pasa_a_ser_la_nueva(self):
        self.write(self.origen, b"lnk-tenca")
        self.write(self.viejo, self.nuestro)  # lo que dejó «Arrancar con Windows» de Chuleta (esta copia)
        self.assertIs(server.inicio_windows_leer(), True)  # sigue activado: la antigua abre la app al entrar
        server.inicio_windows_al_dia()
        self.assertEqual(self.read(self.destino), b"lnk-tenca")
        self.assertFalse(os.path.exists(self.viejo))
        self.assertIs(server.inicio_windows_leer(), True)

    def test_la_ruta_en_otra_alineacion_y_mayusculas(self):
        self.write(self.origen, b"lnk-tenca")
        self.write(self.viejo, lnk_de(server.APP_DIR.upper(), extra=b"x"))  # ruta en byte impar y en mayúsculas
        server.inicio_windows_al_dia()
        self.assertEqual(os.listdir(self.startup), ["Tenca.lnk"])

    def test_con_las_dos_queda_una(self):
        self.write(self.origen, b"lnk-tenca")
        self.write(self.destino, b"lnk-tenca")
        self.write(self.viejo, self.nuestro)
        server.inicio_windows_al_dia()
        self.assertEqual(sorted(os.listdir(self.startup)), ["Tenca.lnk"])

    def test_lo_ajeno_no_se_toca_ni_cuenta(self):
        """Revisión de 53d0103 (P0): un Notas.lnk de otro programa y un Chuleta.lnk de otra copia (u otro programa)
        en la carpeta Inicio ni se borran ni encienden «Arrancar con Windows»."""
        self.write(self.origen, b"lnk-tenca")
        notas = os.path.join(self.startup, "Notas.lnk")
        self.write(notas, self.nuestro)  # incluso con la ruta de la app: Notas nunca tuvo arranque
        self.write(self.viejo, self.ajeno)
        self.assertIs(server.inicio_windows_leer(), False)
        server.inicio_windows_al_dia()
        self.assertEqual(sorted(os.listdir(self.startup)), ["Chuleta.lnk", "Notas.lnk"])
        self.assertEqual(self.read(self.viejo), self.ajeno)
        self.assertIs(server.inicio_windows_poner(False), False)  # apagar tampoco los toca
        self.assertEqual(sorted(os.listdir(self.startup)), ["Chuleta.lnk", "Notas.lnk"])
        self.assertIs(server.inicio_windows_poner(True), True)
        self.assertEqual(sorted(os.listdir(self.startup)), ["Chuleta.lnk", "Notas.lnk", "Tenca.lnk"])

    def test_sin_el_de_inicio_no_se_toca_la_antigua(self):
        """Sin Tenca.lnk en el menú Inicio no hay de qué copiar: la antigua es lo único que la abre al entrar."""
        self.write(self.viejo, self.nuestro)
        server.inicio_windows_al_dia()
        self.assertTrue(os.path.exists(self.viejo))
        self.assertFalse(os.path.exists(self.destino))

    def test_desactivar_quita_tambien_la_antigua(self):
        self.write(self.origen, b"lnk-tenca")
        self.write(self.viejo, self.nuestro)
        self.assertIs(server.inicio_windows_poner(False), False)
        self.assertEqual(os.listdir(self.startup), [])

    def test_activar_deja_solo_la_nueva(self):
        self.write(self.origen, b"lnk-tenca")
        self.write(self.viejo, self.nuestro)
        self.assertIs(server.inicio_windows_poner(True), True)
        self.assertEqual(sorted(os.listdir(self.startup)), ["Tenca.lnk"])
        self.assertEqual(self.read(self.destino), b"lnk-tenca")

    def test_desactivado_sin_antiguas_no_crea_nada(self):
        self.write(self.origen, b"lnk-tenca")
        server.inicio_windows_al_dia()
        self.assertEqual(os.listdir(self.startup), [])

    def test_los_mensajes_tienen_plantilla(self):
        for msg in ("arrancar con Windows: sigue activado, ahora como «Tenca»",
                    "arrancar con Windows: quitado el acceso directo antiguo «Chuleta»",
                    "   project_folders: la carpeta de la app se llamaba «Chuleta»; pasa a «Tenca»"):
            self.assertNotIn("<texto>", server.diag_line(msg).replace("«<texto>»", ""), msg)


class AjustesDelProyectoDeLaRaizTests(unittest.TestCase):
    """Revisión de 53d0103 (P1): la carpeta y el idioma asignados al proyecto de las listas de la raíz se guardaban con
    el nombre de la app («Chuleta»); tras el renombrado pasan a «Tenca» y siguen valiendo."""

    def test_pasan_al_nombre_de_ahora(self):
        d = {"project_folders": {"Chuleta": "Reuniones raiz", "Alfa": "Alfa/R"}, "project_langs": {"chuleta": "en"}}
        self.assertEqual(server.migrate_app_name(d), [("project_folders", "Chuleta"), ("project_langs", "chuleta")])
        self.assertEqual(d["project_folders"], {"Tenca": "Reuniones raiz", "Alfa": "Alfa/R"})
        self.assertEqual(d["project_langs"], {"Tenca": "en"})
        self.assertEqual(server.migrate_app_name(d), [])  # otra vez: nada

    def test_no_pisa_lo_que_ya_tiene_el_nombre_nuevo(self):
        d = {"project_folders": {"Chuleta": "vieja", "Tenca": "nueva"}}
        self.assertEqual(server.migrate_app_name(d), [])
        self.assertEqual(d["project_folders"], {"Chuleta": "vieja", "Tenca": "nueva"})

    def test_el_mas_reciente_primero(self):
        d = {"project_folders": {"Notas": "de Notas", "Chuleta": "de Chuleta"}}
        server.migrate_app_name(d)
        self.assertEqual(d["project_folders"], {"Notas": "de Notas", "Tenca": "de Chuleta"})
        d = {"project_langs": {"Notas": "de"}, "otra": 1}
        server.migrate_app_name(d)
        self.assertEqual(d, {"project_langs": {"Tenca": "de"}, "otra": 1})

    def test_se_aplica_al_grabar(self):
        """La carpeta asignada al proyecto de la raíz vale otra vez para rec_folder_origin y el idioma para el plan."""
        import plan
        v = {"id": "Vocabulario/General.txt", "project": server.APP_NAME, "name": "General",
             "_abs": os.path.join(REPO, "Vocabulario", "General.txt")}
        st = {"project_folders": {"Chuleta": "Reuniones raiz"}, "project_langs": {"Chuleta": "en"}}
        server.migrate_app_name(st)
        with mock.patch.object(server, "_usable_dir", return_value=True):
            self.assertEqual(server.rec_folder_origin(v, st, [v]), ("Reuniones raiz", "proyecto"))
        self.assertEqual(st["project_langs"].get(v["project"]), "en")
        with open(plan.__file__, encoding="utf-8") as f:
            self.assertIn('elif v and v["project"] in (st.get("project_langs") or {}):', f.read())

    def test_el_arranque_lo_migra(self):
        src = open(os.path.join(APP, "server.py"), encoding="utf-8").read()
        init = src[src.index("class App:"):src.index("self.library = Library(")]
        self.assertIn("renombrados.extend(migrate_app_name(d))", init)
        self.assertLess(init.index("self._load_links()"), init.index("migrate_app_name"))


class CompartirSinLoViejoTests(unittest.TestCase):
    """Revisión de 53d0103 (P1): «Compartir Tenca» desde una copia instalada encima de Chuleta (el instalador no borra
    nada de _app) no lleva al zip ABRIR CHULETA.bat, Chuleta.bat ni chuleta.ico/png."""

    def test_el_zip_no_lleva_los_restos(self):
        tmp = tempfile.mkdtemp(prefix="tenca-restos-")
        self.addCleanup(shutil.rmtree, tmp, True)
        app = os.path.join(tmp, "_app")
        shutil.copytree(APP, app, ignore=shutil.ignore_patterns("__pycache__", "version.txt", "python.txt"))
        restos = [("Chuleta.bat",), ("Notas.bat",), ("static", "chuleta.ico"), ("static", "chuleta.png"),
                  ("static", "notas.ico"), ("export", "ABRIR CHULETA.bat"), ("export", "ABRIR NOTAS.bat")]
        for parts in restos:
            write(os.path.join(app, *parts), "@echo off\r\ncall \"%~dp0_app\\Chuleta.bat\" %*\r\n")
        dest = os.path.join(tmp, "Tenca - instalador.zip")
        with patched_paths(APP_DIR=app, STATIC_DIR=os.path.join(app, "static"), EXPORT_DIR=os.path.join(app, "export")):
            server.build_package(dest, version="2026-11-09 · abc1234")
        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
        self.assertEqual([n for n in names if VIEJO.search(n) or re.search(r"(?i)/notas\.(bat|ico|png)$", n)], [])
        for n in ("Tenca/ABRIR TENCA.bat", "Tenca/_app/Tenca.bat", "Tenca/_app/static/tenca.ico",
                  "Tenca/_app/static/notas.js", "Tenca/_app/export/ABRIR TENCA.bat"):
            self.assertIn(n, names)

    def test_que_cuenta_como_nombre_anterior(self):
        for n in ("ABRIR CHULETA.bat", "abrir chuleta.BAT", "Chuleta.bat", "chuleta.ico", "Notas.bat", "notas.png"):
            self.assertTrue(server.de_un_nombre_anterior(n), n)
        for n in ("notas.js", "ABRIR TENCA.bat", "Tenca.bat", "tenca.ico", "chuleta.txt", "server.py"):
            self.assertFalse(server.de_un_nombre_anterior(n), n)


def q(s):
    return "'" + s.replace("'", "''") + "'"


SAY = "function Say($t, $c) { Write-Host ('SAY ' + $t) }; function Log-File($t) { Write-Host ('LOG ' + $t) }; "


def write(path, data="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


@unittest.skipUnless(powershell(), "sin PowerShell (CHULETA_PWSH, powershell.exe o pwsh)")
class LanzadoresAntiguosTests(unittest.TestCase):
    """Remove-LanzadoresAntiguos, de verdad: quita «ABRIR CHULETA.bat» de la app y deja uno del usuario con ese
    nombre."""

    def run_en(self, root):
        return run_ps_functions(INSTALAR, ["Remove-LanzadoresAntiguos"],
                                SAY + "$Launcher = 'ABRIR TENCA.bat'; "
                                "$LegacyLaunchers = @('ABRIR CHULETA.bat', 'ABRIR NOTAS.bat'); "
                                "Remove-LanzadoresAntiguos " + q(root))

    def test_quita_los_de_la_app_y_deja_los_ajenos(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix="tenca-raiz-"))
        self.addCleanup(shutil.rmtree, root, True)
        write(os.path.join(root, "ABRIR TENCA.bat"), '@echo off\r\ncall "%~dp0_app\\Tenca.bat" %*\r\n')
        write(os.path.join(root, "ABRIR CHULETA.bat"), '@echo off\r\ncall "%~dp0_app\\Chuleta.bat" %*\r\n')
        write(os.path.join(root, "ABRIR NOTAS.bat"), "@echo off\r\nstart notepad mis-notas.txt\r\n")  # del usuario
        out = self.run_en(root)
        self.assertEqual(sorted(os.listdir(root)), ["ABRIR NOTAS.bat", "ABRIR TENCA.bat"], out)
        self.assertIn("SAY     Quitado ABRIR CHULETA.bat (ahora es ABRIR TENCA.bat).", out)
        self.assertIn("no es de la app", out)

    def test_sin_el_nuevo_no_quita_nada(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix="tenca-raiz-"))
        self.addCleanup(shutil.rmtree, root, True)
        write(os.path.join(root, "ABRIR CHULETA.bat"), '@echo off\r\ncall "%~dp0_app\\Chuleta.bat" %*\r\n')
        self.run_en(root)
        self.assertEqual(os.listdir(root), ["ABRIR CHULETA.bat"])


@unittest.skipUnless(powershell(), "sin PowerShell (CHULETA_PWSH, powershell.exe o pwsh)")
class EncontrarLaChuletaTests(unittest.TestCase):
    """Find-Instalacion encuentra la Chuleta por su carpeta propia (%USERPROFILE%\\Chuleta) aunque no tenga ni entrada
    ni accesos directos; las otras dos vías (registro y .lnk), en Windows (abajo) y en el E2E."""

    def test_por_la_carpeta_con_el_nombre_anterior(self):
        home = os.path.realpath(tempfile.mkdtemp(prefix="tenca-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        desk = os.path.join(home, "Escritorio")
        os.makedirs(desk)
        write(os.path.join(home, "Chuleta", "_app", "server.py"))
        out = run_ps_functions(
            INSTALAR, ["Test-Instalacion", "Get-LnkInfo", "Same-Path", "Find-Instalacion"],
            SAY + "$Sistema = $true; $Desktop = " + q(desk) + "; $script:UserHome = " + q(home) + "; "
            "$AppName = 'Tenca'; $LegacyNames = @('Chuleta', 'Notas'); $FolderName = 'Tenca'; "
            "$LegacyFolders = @('Chuleta'); $UninstallKey = 'HKCU:\\Software\\TencaNoExiste\\Tenca'; "
            "$LegacyUninstallKeys = @('HKCU:\\Software\\TencaNoExiste\\Chuleta'); "
            "$r = Find-Instalacion; Write-Output ('R=' + $r.root + '|' + $r.how)")
        m = re.search(r"R=(.*)\|(.*)", out)
        self.assertTrue(m, out)
        self.assertEqual(os.path.realpath(m.group(1).strip()), os.path.join(home, "Chuleta"))
        self.assertIn("carpeta", m.group(2))


@unittest.skipUnless(WINDOWS and shutil.which("powershell.exe"), "solo en Windows (PowerShell 5.1)")
class MigracionWindowsTests(unittest.TestCase):
    """Accesos directos (.lnk de verdad, por WScript.Shell), «Arrancar con Windows», anclajes y la entrada antigua del
    registro, con las funciones del instalador sobre carpetas y claves de prueba."""

    FUNCS = ["Same-Path", "Test-Instalacion", "Get-LnkInfo", "Get-LnkAntiguos", "Remove-LnkAntiguos",
             "Set-ShortcutAppId", "Set-AccesoDirecto", "Copy-ArranqueAprobado", "Update-Arranque", "Report-Anclajes",
             "Remove-RegistroAntiguo"]

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="tenca-mig-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.app = os.path.join(self.base, "Chuleta", "_app")  # la instalación (con el nombre anterior en la carpeta)
        self.otra = os.path.join(self.base, "Otra", "_app")  # otra copia viva de la app
        for d in (self.app, self.otra):
            write(os.path.join(d, "server.py"))
        self.pyw = os.path.join(self.base, "Python", "pythonw.exe")
        write(self.pyw)

    def ps(self, body):
        return run_ps_functions(INSTALAR, self.FUNCS,
                                SAY + "$AppName = 'Tenca'; $LegacyNames = @('Chuleta', 'Notas'); "
                                "$AppId = 'Bernardo.Chuleta'; $Launcher = 'ABRIR TENCA.bat'; $script:antes = ''; "
                                + body)

    def lnk(self, path, target, args="", wd=""):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = run_ps_functions(INSTALAR, [], "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + q(path)
                               + "); $s.TargetPath = " + q(target) + "; $s.Arguments = " + q(args) + "; "
                               "$s.WorkingDirectory = " + q(wd) + "; $s.Save()")
        self.assertTrue(os.path.isfile(path), out)

    def lnk_app(self, path, app_dir):
        self.lnk(path, self.pyw, '"' + os.path.join(app_dir, "server.py") + '"', app_dir)

    def info(self, path):
        out = run_ps_functions(INSTALAR, [], "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + q(path)
                               + "); Write-Output ('WD=' + $s.WorkingDirectory); "
                               "Write-Output ('IC=' + $s.IconLocation)")
        return dict(re.findall(r"^(WD|IC)=(.*)$", out, re.M))

    def test_escritorio_renombra_la_suya_y_respeta_lo_ajeno(self):
        desk = os.path.join(self.base, "Escritorio")
        self.lnk_app(os.path.join(desk, "Chuleta.lnk"), self.app)  # la de esta instalación: pasa a Tenca.lnk
        self.lnk(os.path.join(desk, "Notas.lnk"), self.pyw, '"C:\\no-existe\\_app\\server.py"', "C:\\no-existe\\_app")
        out = self.ps("Set-AccesoDirecto " + q(desk) + " " + q(self.app) + " " + q(self.pyw) + " $true; "
                      "Write-Output ('ANTES=' + $script:antes)")
        self.assertEqual(sorted(os.listdir(desk)), ["Tenca.lnk"], out)  # nada con el nombre viejo, sin duplicados
        self.assertIn("pasa a llamarse 'Tenca'", out)
        self.assertIn("ya no abría nada", out)  # Notas.lnk roto, de la app
        self.assertIn("ANTES=Chuleta", out)
        i = self.info(os.path.join(desk, "Tenca.lnk"))
        self.assertEqual(os.path.normcase(i["WD"]), os.path.normcase(self.app))
        self.assertTrue(i["IC"].lower().endswith("static\\tenca.ico,0"), i)

    def test_con_las_dos_queda_la_nueva(self):
        desk = os.path.join(self.base, "Escritorio")
        self.lnk_app(os.path.join(desk, "Tenca.lnk"), self.app)
        self.lnk_app(os.path.join(desk, "Chuleta.lnk"), self.app)
        out = self.ps("Set-AccesoDirecto " + q(desk) + " " + q(self.app) + " " + q(self.pyw) + " $true")
        self.assertEqual(sorted(os.listdir(desk)), ["Tenca.lnk"], out)
        self.assertIn("Quitado el acceso directo 'Chuleta'", out)

    def test_otra_copia_y_un_documento_se_quedan(self):
        desk = os.path.join(self.base, "Escritorio")
        self.lnk_app(os.path.join(desk, "Chuleta.lnk"), self.otra)  # otra instalación viva
        doc = os.path.join(self.base, "chuleta.txt")
        write(doc, "apuntes")
        self.lnk(os.path.join(desk, "Notas.lnk"), doc, "", self.base)  # un acceso directo a un documento
        out = self.ps("Set-AccesoDirecto " + q(desk) + " " + q(self.app) + " " + q(self.pyw) + " $true")
        self.assertEqual(sorted(os.listdir(desk)), ["Chuleta.lnk", "Notas.lnk", "Tenca.lnk"], out)
        self.assertIn("es de otra copia", out)

    def test_sin_carpeta_de_trabajo_no_es_roto(self):
        """Un Chuleta.lnk sin WorkingDirectory pero con el server.py de esta instalación en los argumentos es de esta
        instalación (se renombra), no uno roto (que se quitaría)."""
        desk = os.path.join(self.base, "Escritorio")
        self.lnk(os.path.join(desk, "Chuleta.lnk"), self.pyw, '"' + os.path.join(self.app, "server.py") + '"', "")
        out = self.ps("Set-AccesoDirecto " + q(desk) + " " + q(self.app) + " " + q(self.pyw) + " $true")
        self.assertEqual(sorted(os.listdir(desk)), ["Tenca.lnk"], out)
        self.assertIn("pasa a llamarse 'Tenca'", out)
        self.assertNotIn("ya no abría nada", out)

    def test_la_app_reconoce_su_lnk_de_verdad(self):
        """server._lnk_apunta_a con un .lnk de verdad (WScript.Shell): la ruta de la _app va en UTF-16LE."""
        lnk = os.path.join(self.base, "Inicio", "Chuleta.lnk")
        self.lnk_app(lnk, self.app)
        self.assertTrue(server._lnk_apunta_a(lnk, self.app))
        self.assertTrue(server._lnk_apunta_a(lnk, self.app.upper()))
        self.assertFalse(server._lnk_apunta_a(lnk, self.otra))

    def test_arranque_con_windows(self):
        progs, startup = os.path.join(self.base, "Programas"), os.path.join(self.base, "Inicio")
        self.lnk_app(os.path.join(progs, "Tenca.lnk"), self.app)
        self.lnk_app(os.path.join(startup, "Chuleta.lnk"), self.app)  # «Arrancar con Windows» de Chuleta
        out = self.ps("Update-Arranque " + q(os.path.join(progs, "Tenca.lnk")) + " " + q(self.app) + " "
                      + q(startup))
        self.assertEqual(os.listdir(startup), ["Tenca.lnk"], out)
        with open(os.path.join(progs, "Tenca.lnk"), "rb") as a, open(os.path.join(startup, "Tenca.lnk"), "rb") as b:
            self.assertEqual(a.read(), b.read())  # la misma copia que pone la app (inicio_windows_al_dia no la toca)
        self.assertIn("sigue activado", out)

    def test_arranque_apagado_no_se_pone(self):
        progs, startup = os.path.join(self.base, "Programas"), os.path.join(self.base, "Inicio")
        self.lnk_app(os.path.join(progs, "Tenca.lnk"), self.app)
        os.makedirs(startup)
        self.ps("Update-Arranque " + q(os.path.join(progs, "Tenca.lnk")) + " " + q(self.app) + " " + q(startup))
        self.assertEqual(os.listdir(startup), [])

    def test_anclaje_se_avisa_y_no_se_toca(self):
        appdata = os.path.join(self.base, "AppData")
        pin = os.path.join(appdata, "Microsoft", "Internet Explorer", "Quick Launch", "User Pinned", "TaskBar",
                           "Chuleta.lnk")
        self.lnk_app(pin, self.app)
        out = self.ps("$env:APPDATA = " + q(appdata) + "; $r = @(Report-Anclajes " + q(self.app) + "); "
                      "Write-Output ('R=' + ($r -join ','))")
        self.assertIn("R=Chuleta", out)
        self.assertIn("Desanclar de la barra de tareas", out)
        self.assertTrue(os.path.isfile(pin))

    def test_entrada_antigua_del_registro(self):
        import winreg
        base = "Software\\TencaTest-%d" % os.getpid()

        def borrar():
            for sub in ("Chuleta", ""):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + ("\\" + sub if sub else ""))
                except OSError:
                    pass
        self.addCleanup(borrar)
        root = os.path.dirname(self.app)

        def entrada(loc, pub="Bernardo Stegmaier"):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\Chuleta") as k:
                winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, loc)
                winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, pub)

        def existe():
            try:
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\Chuleta").Close()
                return True
            except OSError:
                return False
        cuerpo = ("$Publisher = 'Bernardo Stegmaier'; $LegacyUninstallKeys = @(" + q("HKCU:\\" + base + "\\Chuleta")
                  + "); Remove-RegistroAntiguo ")
        entrada(os.path.dirname(self.otra))  # de otra copia que sigue instalada: se queda
        out = self.ps(cuerpo + q(root))
        self.assertTrue(existe(), out)
        self.assertIn("es de otra copia", out)
        entrada(root)  # la de esta carpeta: sobra
        out = self.ps(cuerpo + q(root))
        self.assertFalse(existe(), out)
        self.assertIn("Quitada de Aplicaciones instaladas la entrada antigua 'Chuleta'", out)
        entrada(root, pub="Otra Empresa S.A.")  # otro programa que se llama igual: no se toca
        self.ps(cuerpo + q(root))
        self.assertTrue(existe())
        entrada(os.path.join(self.base, "ya-no-esta"))  # de una carpeta sin la app: sobra
        self.ps(cuerpo + q(root))
        self.assertFalse(existe())

    def test_arranque_desactivado_en_el_administrador_de_tareas_sigue_desactivado(self):
        """Revisión de 53d0103 (P2): Aplicaciones de inicio guarda «desactivado» por nombre de fichero
        (StartupApproved\\StartupFolder): el valor de Chuleta.lnk pasa a Tenca.lnk."""
        import winreg
        clave = "Software\\TencaTest-%d\\StartupFolder" % os.getpid()

        def borrar():
            for sub in (clave, clave.rpartition("\\")[0]):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                except OSError:
                    pass
        self.addCleanup(borrar)
        desactivado = bytes([3, 0, 0, 0]) + bytes(range(8))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, clave) as k:
            winreg.SetValueEx(k, "Chuleta.lnk", 0, winreg.REG_BINARY, desactivado)
        out = self.ps("Copy-ArranqueAprobado 'Chuleta.lnk' 'Tenca.lnk' " + q("HKCU:\\" + clave))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
            self.assertEqual(winreg.QueryValueEx(k, "Tenca.lnk")[0], desactivado, out)
            with self.assertRaises(FileNotFoundError):
                winreg.QueryValueEx(k, "Chuleta.lnk")
        self.ps("Copy-ArranqueAprobado 'Nada.lnk' 'Otra.lnk' " + q("HKCU:\\" + clave))  # sin valor: nada
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
            with self.assertRaises(FileNotFoundError):
                winreg.QueryValueEx(k, "Otra.lnk")


if __name__ == "__main__":
    unittest.main()
