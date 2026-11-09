# -*- coding: utf-8 -*-
"""Instalacion «de empresa»: carpeta propia, entrada de «Aplicaciones instaladas» y desinstalador.

instalar.ps1 y desinstalar.ps1 se comprueban por texto en cualquier sistema; el parseo, con PowerShell si lo hay
(powershell.exe en Windows; en macOS/Linux, pwsh o la variable CHULETA_PWSH). En Windows, ademas, el desinstalador se
ejecuta de verdad sobre una carpeta temporal (TEMP y LOCALAPPDATA tambien temporales, otro puerto) y se comprueba que
quita la app y deja las reuniones byte a byte."""
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile

from support import REPO, server

WINDOWS = sys.platform == "win32"
INSTALAR = os.path.join(REPO, "_app", "instalar.ps1")
DESINSTALAR = os.path.join(REPO, "_app", "desinstalar.ps1")
# todo lo que es de una reunion (audio, texto, notas, resumen, hablantes) y los datos en JSON: nada de esto se borra
MEETING_EXT = set(server.AUDIO_EXT) | set(server.TEXT_EXT) | {".json", ".srt", ".md"} | {
    os.path.splitext(e)[1] for e in server.SIDE_EXTS}


def read(path):
    with open(path, "rb") as f:
        return f.read()


def powershell():
    """El PowerShell con el que parsear: CHULETA_PWSH, powershell.exe (Windows) o pwsh; None si no hay."""
    exe = os.environ.get("CHULETA_PWSH")
    if exe and os.path.isfile(exe):
        return exe
    for name in (("powershell.exe", "powershell", "pwsh") if WINDOWS else ("pwsh",)):
        found = shutil.which(name)
        if found:
            return found
    return None


def ps_text(path):
    return read(path).decode("utf-8-sig").replace("\r\n", "\n")


def function_body(text, name):
    """El texto de `function name { ... }` (por llaves equilibradas; basta para estos scripts)."""
    i = text.index(f"function {name}")
    j = text.index("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    raise AssertionError(f"sin cierre: {name}")


class FicherosTests(unittest.TestCase):
    def test_bom_y_crlf(self):
        for path in (INSTALAR, DESINSTALAR):
            b = read(path)
            self.assertTrue(b.startswith(b"\xef\xbb\xbf"), path)  # PowerShell 5.1 leeria las tildes como ANSI
            self.assertFalse(re.search(rb"(?<!\r)\n", b), path)  # todo CRLF
            self.assertTrue(b.endswith(b"\r\n"), path)

    def test_el_paquete_lleva_el_desinstalador(self):
        tmp = tempfile.mkdtemp(prefix="chuleta-pkg-des-")
        dest = os.path.join(tmp, "Tenca - instalador.zip")
        server.build_package(dest)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            self.assertIn("Tenca/_app/desinstalar.ps1", names)
            b = z.read("Tenca/_app/desinstalar.ps1")
        self.assertTrue(b.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(re.search(rb"(?<!\r)\n", b))
        self.assertEqual(b, read(DESINSTALAR))  # el del zip es el del repo, sin cambios
        shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(powershell(), "sin PowerShell (CHULETA_PWSH, powershell.exe o pwsh)")
    def test_parsean_sin_errores(self):
        files = [INSTALAR, DESINSTALAR, os.path.join(REPO, "scripts", "e2e", "lookfeel_e2e.ps1")]
        # las rutas van dentro del texto de -Command (lo que sigue a -Command se concatena: $args llegaria vacio)
        lista = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
        script = ("$n = 0; $k = 0; foreach ($f in @(" + lista + ")) { $k++; $t = $null; $e = $null; "
                  "$null = [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e); "
                  "foreach ($x in $e) { Write-Output ($f + ':' + $x.Extent.StartLineNumber + ': ' + $x.Message) }; "
                  "$n += $e.Count }; Write-Output \"ficheros=$k errores=$n\"")
        r = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        self.assertIn(f"ficheros={len(files)} errores=0", r.stdout, r.stdout + r.stderr)


E2E = os.path.join(REPO, "scripts", "e2e", "lookfeel_e2e.ps1")


class VariablesPowerShellTests(unittest.TestCase):
    """PowerShell no distingue mayúsculas en los nombres de variable. En el E2E, `$base` (la ruta de la reunión falsa,
    fase r) era la misma variable que `$Base` (la dirección de la API): desde esa fase todas sus peticiones iban a
    «...\\Reunion de prueba\\api\\config» (System.Net.WebException: Could not find a part of the path) y parecía que
    la app no contestaba, cuando la misma app contestaba en milisegundos a un vigía en Python (runs 41341410510 y
    41344499739)."""

    SCRIPTS = (INSTALAR, DESINSTALAR, E2E, os.path.join(REPO, "scripts", "e2e", "capturas.ps1"))

    def test_ninguna_variable_del_script_con_dos_grafias(self):
        """Las del nivel del script (param, asignaciones y Set-Variable sin sangría): una sola grafía cada una."""
        for path in self.SCRIPTS:
            with self.subTest(os.path.basename(path)):
                texto = ps_text(path)
                m = re.search(r"^param\((.*?)\)\s*$", texto, re.S | re.M)
                nombres = re.findall(r"\$(\w+)", m.group(1)) if m else []
                nombres += re.findall(r"^\$(?:script:)?(\w+)\s*=", texto, re.M)
                nombres += re.findall(r"^Set-Variable\s+-Name\s+(\w+)", texto, re.M)
                grafias = {}
                for n in nombres:
                    grafias.setdefault(n.lower(), set()).add(n)
                self.assertEqual({k: sorted(v) for k, v in grafias.items() if len(v) > 1}, {})

    def test_la_direccion_de_la_api_no_se_puede_pisar(self):
        """$Base es constante: si otra asignación la pisa, PowerShell da un error en esa línea en vez de seguir con
        otra dirección."""
        texto = ps_text(E2E)
        self.assertRegex(texto, r'(?m)^Set-Variable -Name Base -Option Constant -Value "http://127\.0\.0\.1:\$Port"')
        self.assertNotRegex(texto, r"(?mi)^\s*\$base\s*=")


class InstaladorEmpresaTests(unittest.TestCase):
    def setUp(self):
        self.ps1 = ps_text(INSTALAR)

    def test_entrada_de_aplicaciones_instaladas(self):
        """Nombres de la clave Uninstall de Windows (learn.microsoft.com, «Uninstall Registry Key») con su tipo:
        REG_SZ los textos, REG_DWORD EstimatedSize (KB, ARPSIZE), NoModify y NoRepair."""
        self.assertIn('$UninstallBase = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall"', self.ps1)
        self.assertIn('$UninstallKey = "$UninstallBase\\Tenca"', self.ps1)
        body = function_body(self.ps1, "Register-App")
        pairs = dict(re.findall(r'(\w+) = @\([^;]*?, "(String|DWord)"\)', body))
        self.assertEqual(pairs, {
            "DisplayName": "String", "DisplayVersion": "String", "Publisher": "String", "DisplayIcon": "String",
            "InstallLocation": "String", "InstallDate": "String", "EstimatedSize": "DWord", "NoModify": "DWord",
            "NoRepair": "DWord", "URLInfoAbout": "String", "UninstallString": "String",
            "QuietUninstallString": "String"})
        self.assertIn("""'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + """
                      """(Join-Path $appDir "desinstalar.ps1") + '"'""", body)
        self.assertIn('QuietUninstallString = @("$cmd -Silencioso", "String")', body)
        self.assertIn('Get-Date -Format "yyyyMMdd"', body)
        self.assertIn('Join-Path $appDir "static\\tenca.ico"', body)
        self.assertIn("[Math]::Ceiling([double]$bytes / 1024)", body)  # KB
        self.assertIn('if (-not $version) { $version = Get-Date -Format "yyyy-MM-dd" }', body)  # sin version.txt
        self.assertIn('$Publisher = "Bernardo Stegmaier"', self.ps1)
        m = re.search(r'\$RepoUrl = "([^"]+)"', self.ps1)
        self.assertEqual(m.group(1), server.REPO_URL)
        # un fallo (politica de TI) solo se avisa: la app funciona igual
        self.assertIn("la app funciona igual", body)
        self.assertIn("return $false", body)

    def test_deteccion_por_orden(self):
        body = function_body(self.ps1, "Find-Instalacion")
        a = body.index("InstallLocation")
        b = body.index("foreach ($n in @(@($AppName) + $LegacyNames))")
        c = body.index("Join-Path $script:UserHome $f")
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertIn('how = "Aplicaciones instaladas"', body)
        self.assertIn('how = "acceso directo $n.lnk ($dir)"', body)
        self.assertIn("Test-Instalacion $loc", body)  # solo si sigue teniendo _app\server.py
        self.assertIn('$FolderName = "Tenca"', self.ps1)
        self.assertIn('Join-Path $dir "_app\\server.py"', function_body(self.ps1, "Test-Instalacion"))

    def test_carpeta_propia(self):
        self.assertIn('[string]$Destino = ""', self.ps1)
        self.assertIn('Step "Instalando en $dest..."', self.ps1)
        self.assertIn("Copy-AppFiles $app $root (Join-Path $dest \"_app\") $dest", self.ps1)
        self.assertIn("La carpeta descomprimida ya se puede borrar.", self.ps1)
        self.assertIn('Say "  Instalada en $root" Green', self.ps1)
        self.assertIn("ya no hace falta: puedes borrarla.", self.ps1)
        nueva = function_body(self.ps1, "Get-CarpetaNueva")
        self.assertIn('"$FolderName ($i)"', nueva)
        libre = function_body(self.ps1, "Test-CarpetaDeLaApp")
        self.assertIn("LEEME.txt", libre)  # una carpeta que fue de la app (el desinstalador deja el LEEME)
        copia = function_body(self.ps1, "Copy-AppFiles")
        for n in ('"LEEME.txt", "LICENSE", "INSTALAR.bat", $Launcher', '"Vocabulario"', '"static", "export"',
                  "if (-not (Test-Path -LiteralPath $to)) { Copy-Item"):  # las listas que ya hay no se pisan
            self.assertIn(n, copia)
        # una carpeta ya instalada (python.txt) o la que ya es la de la app no se copian
        self.assertIn('Test-Path -LiteralPath (Join-Path $app "python.txt")', self.ps1)
        self.assertIn("-not (Same-Path $dest $root)", self.ps1)
        self.assertIn("$Sistema = -not $NoShortcut -and -not $Desktop", self.ps1)
        # el instalador solo quita lo que sobra con un nombre anterior (accesos directos, .bat de la raiz y la entrada
        # antigua; test_tenca.InstaladorTextoTests) y no mueve nada: las grabaciones se quedan donde estan
        self.assertEqual(self.ps1.count("Remove-Item"), 2)
        self.assertNotIn("Move-Item", self.ps1)

    def test_p2_16_solo_desbloquea_el_paquete(self):
        """La marca de Internet (Zone.Identifier) solo se quita a los ficheros del paquete, nunca a las reuniones."""
        self.assertEqual(self.ps1.count("Unblock-File"), 2)  # las dos de Unblock-AppFiles
        body = function_body(self.ps1, "Unblock-AppFiles")
        self.assertIn("Get-ChildItem -LiteralPath $appDir -Recurse -File", body)
        self.assertIn('@("INSTALAR.bat", $Launcher, "LEEME.txt", "LICENSE")', body)
        self.assertFalse(re.search(r"Get-ChildItem -LiteralPath \$(root|toRoot|rootDir)\b[^\n]*Unblock", self.ps1))
        self.assertIn("Unblock-AppFiles $app $root", self.ps1)
        self.assertIn("Unblock-AppFiles $toApp $toRoot", function_body(self.ps1, "Copy-AppFiles"))

    def test_p2_36_pywebview_opcional(self):
        core = re.search(r'\$packages = @\(([^)]*)\)', self.ps1).group(1)
        opt = re.search(r'\$optional = @\(([^)]*)\)', self.ps1).group(1)
        self.assertNotIn("pywebview", core)
        self.assertIn('"pywebview==6.2.1"', opt)
        self.assertIn("Fail (Describe-PipFailure $log $pyVer $pipCfg)", self.ps1)

    def test_p2_37_licencia_al_actualizar(self):
        copia = function_body(self.ps1, "Copy-AppFiles")
        self.assertIn('@("LEEME.txt", "LICENSE", "INSTALAR.bat", $Launcher)', copia)

    def test_registro_tras_los_accesos_directos(self):
        i = self.ps1.index('Step "Creando el acceso directo')
        j = self.ps1.index("$null = Register-App $app $root $verRaw")
        k = self.ps1.index('Say "  Listo$ver." Green')
        self.assertLess(i, j)
        self.assertLess(j, k)


def run_ps_functions(ps1, names, body):
    """Ejecuta `body` con solo las funciones `names` de `ps1` (sacadas por AST: el script entero no se ejecuta)."""
    quoted = ", ".join("'" + n + "'" for n in names)
    script = ("$ast = [System.Management.Automation.Language.Parser]::ParseFile('" + ps1.replace("'", "''") +
              "', [ref]$null, [ref]$null); "
              "foreach ($d in $ast.FindAll({ param($a) $a -is "
              "[System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) { "
              "if (@(" + quoted + ") -contains $d.Name) { . ([scriptblock]::Create($d.Extent.Text)) } }; " + body)
    r = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return r.stdout + r.stderr


@unittest.skipUnless(powershell(), "sin PowerShell (CHULETA_PWSH, powershell.exe o pwsh)")
class DiagnosticoPipTests(unittest.TestCase):
    """Describe-PipFailure (P2-36): nombra el paquete y la causa; nunca culpa a Python si no es la version, nunca
    ensena credenciales de la URL del repositorio."""

    def describe(self, log, py_ver="3.13", cfg=""):
        q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
        out = run_ps_functions(INSTALAR, ["Describe-PipFailure"],
                               f"$MaxPy = 313; Write-Output ('R=' + "
                               f"(Describe-PipFailure {q(log)} {q(py_ver)} {q(cfg)}))")
        m = re.search(r"R=(.*)", out)
        self.assertTrue(m, out)
        return m.group(1)

    def test_only_binary(self):
        log = ("ERROR: Could not find a version that satisfies the requirement proxy_tools (from versions: none)\n"
               "ERROR: No matching distribution found for proxy_tools")
        r = self.describe(log, cfg="global.only-binary=':all:'")
        self.assertIn("proxy_tools", r)
        self.assertIn("only-binary", r)
        self.assertNotIn("Instala Python 3.13", r)

    def test_python_demasiado_nuevo(self):
        r = self.describe("ERROR: No matching distribution found for ctranslate2", py_ver="3.14")
        self.assertIn("ctranslate2 todavía no está publicada para Python 3.14", r)

    def test_repositorio_de_la_empresa_sin_credenciales(self):
        r = self.describe("ERROR: No matching distribution found for faster-whisper==1.2.1",
                          cfg="global.index-url='https://usuario:CLAVE-SECRETA@pypi.empresa.example/simple'")
        self.assertIn("faster-whisper", r)
        self.assertIn("pypi.empresa.example", r)
        self.assertNotIn("CLAVE-SECRETA", r)
        self.assertNotIn("usuario", r)

    def test_la_red_antes_que_el_no_encuentra(self):
        # sin red, pip tambien acaba en "Could not find a version": la causa es la red, no Python
        log = ("WARNING: Retrying ... ProxyError('Cannot connect to proxy.')\n"
               "ERROR: Could not find a version that satisfies the requirement faster-whisper==1.2.1 "
               "(from versions: none)")
        r = self.describe(log)
        self.assertTrue(r.startswith("No se han podido descargar las librerías (faster-whisper)"), r)
        r = self.describe("ERROR: ProxyError: fallo simulado (NOTAS_INSTALADOR_SIMULAR_FALLO=pip)")
        self.assertTrue(r.startswith("No se han podido descargar las librerías: parece la red"), r)

    def test_sin_mas_pistas(self):
        r = self.describe("ERROR: No matching distribution found for soundcard")
        self.assertIn("pip no encuentra soundcard para Python 3.13", r)


@unittest.skipUnless(powershell(), "sin PowerShell (CHULETA_PWSH, powershell.exe o pwsh)")
class CarpetaNuevaTests(unittest.TestCase):
    """Get-CarpetaNueva / Test-CarpetaLibre de instalar.ps1, de verdad, sobre un perfil de usuario temporal. La carpeta
    propia es %USERPROFILE%\\Tenca; la de antes del renombrado (%USERPROFILE%\\Chuleta), si es de la app, se sigue
    usando: ahi estan las reuniones."""

    def nueva(self, prepare):
        home = os.path.realpath(tempfile.mkdtemp(prefix="tenca-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        prepare(home)
        out = run_ps_functions(INSTALAR, ["Test-Instalacion", "Test-CarpetaDeLaApp", "Test-CarpetaLibre",
                                          "Get-CarpetaNueva"],
                               "$FolderName = 'Tenca'; $LegacyFolders = @('Chuleta'); "
                               "$script:UserHome = '" + home.replace("'", "''") + "'; "
                               "Write-Output ('R=' + (Get-CarpetaNueva))")
        m = re.search(r"R=(.*)", out)
        self.assertTrue(m, out)
        return os.path.basename(m.group(1).strip())

    @staticmethod
    def write(path, data="x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)

    def test_perfil_sin_la_app(self):
        self.assertEqual(self.nueva(lambda h: None), "Tenca")

    def test_carpeta_vacia(self):
        self.assertEqual(self.nueva(lambda h: os.makedirs(os.path.join(h, "Tenca"))), "Tenca")

    def test_carpeta_ajena(self):
        # «tenca» (un pájaro) y «chuleta» son palabras corrientes: una carpeta con apuntes no es de la app y no se usa
        self.assertEqual(self.nueva(lambda h: self.write(os.path.join(h, "Tenca", "apuntes.txt"))), "Tenca (2)")

    def test_dos_ajenas(self):
        def prep(h):
            self.write(os.path.join(h, "Tenca", "apuntes.txt"))
            self.write(os.path.join(h, "Tenca (2)", "otra.txt"))
        self.assertEqual(self.nueva(prep), "Tenca (3)")

    def test_un_fichero_con_ese_nombre(self):
        self.assertEqual(self.nueva(lambda h: self.write(os.path.join(h, "Tenca"))), "Tenca (2)")

    def test_desinstalada_con_reuniones(self):
        # lo que deja el desinstalador: LEEME.txt de la app y las reuniones -> se vuelve a la misma carpeta
        def prep(h):
            self.write(os.path.join(h, "Tenca", "LEEME.txt"), "\ufeffTENCA · grabar, transcribir y resumir")
            self.write(os.path.join(h, "Tenca", "Proyecto Alfa", "reunion.wav"))
        self.assertEqual(self.nueva(prep), "Tenca")

    def test_chuleta_desinstalada_con_reuniones(self):
        # Chuleta desinstalada (su LEEME y sus reuniones en %USERPROFILE%\\Chuleta): Tenca vuelve a esa carpeta
        def prep(h):
            self.write(os.path.join(h, "Chuleta", "LEEME.txt"), "\ufeffCHULETA · grabar, transcribir y resumir")
            self.write(os.path.join(h, "Chuleta", "Proyecto Alfa", "reunion.wav"))
        self.assertEqual(self.nueva(prep), "Chuleta")

    def test_tenca_antes_que_chuleta(self):
        def prep(h):
            self.write(os.path.join(h, "Chuleta", "LEEME.txt"), "\ufeffCHULETA · grabar, transcribir y resumir")
            self.write(os.path.join(h, "Tenca", "_app", "server.py"))
        self.assertEqual(self.nueva(prep), "Tenca")

    def test_chuleta_ajena_no_se_usa(self):
        # una carpeta «Chuleta» con apuntes (sin LEEME de la app): no es de la app
        self.assertEqual(self.nueva(lambda h: self.write(os.path.join(h, "Chuleta", "apuntes.txt"))), "Tenca")

    def test_leeme_ajeno(self):
        def prep(h):
            self.write(os.path.join(h, "Tenca", "LEEME.txt"), "Mis tencas de examen")
            self.write(os.path.join(h, "Chuleta", "LEEME.txt"), "Mis chuletas de examen")
        self.assertEqual(self.nueva(prep), "Tenca (2)")

    def test_instalada(self):
        self.assertEqual(self.nueva(lambda h: self.write(os.path.join(h, "Tenca", "_app", "server.py"))), "Tenca")


class DesinstaladorTextoTests(unittest.TestCase):
    def setUp(self):
        self.ps1 = ps_text(DESINSTALAR)

    def test_textos_y_opciones(self):
        for s in ("[switch]$Silencioso", "[switch]$BorrarDatos", "[int]$AutoCerrar", "[switch]$AutoConfirmar",
                  "Se quitará la aplicación $AppName de este PC.",
                  "Tus reuniones, transcripciones y notas en $Root NO se borran.",
                  "Borrar también mis ajustes, claves y voces guardadas (%LOCALAPPDATA%\\AudiosApp)",
                  "datos biométricos", '"Desinstalar"', '"Cancelar"', '"Abrir la carpeta"',
                  "$AppName se ha desinstalado. Tus reuniones siguen en $Root", "tenca-desinstalar.log",
                  '$h["X-Audios"] = "1"', "/api/apagar", "-eq 409", "-NoNewWindow", "-Relanzado",
                  "static\\tenca.png", "static\\tenca.ico"):
            self.assertTrue(s in self.ps1, s)
        self.assertTrue("$chk.Checked = [bool]$BorrarDatos" in self.ps1)  # desmarcada salvo -BorrarDatos
        # Image.FromFile dejaria tenca.png abierto y _app sin borrar: las imagenes se cargan en memoria
        self.assertFalse("]::FromFile(" in self.ps1)
        self.assertIn("::FromStream(", self.ps1)
        self.assertIn("$btnCancel.Focus()", self.ps1)  # por defecto, lo que no borra nada

    def test_nunca_borra_reuniones(self):
        """Cada Remove-Item va sobre algo de la app: un acceso directo, la clave, ficheros de _app filtrados por
        Test-AppFile, carpetas vacias de _app, el propio _app vacio, los .bat de la raiz, AudiosApp (solo con la
        casilla) o la copia en %TEMP%. Ninguno sobre la raiz ni con comodines."""
        allowed = {"$lnk", "$key", "$file.FullName", "$self", "$launcher", "$PSCommandPath"}
        lines = [ln for ln in self.ps1.split("\n") if "Remove-Item" in ln and not ln.lstrip().startswith("#")]
        self.assertGreater(len(lines), 5)
        for ln in lines:
            targets = re.findall(r"Remove-Item\s+-LiteralPath\s+(\$[\w.]+)", ln)
            self.assertEqual(len(targets), ln.count("Remove-Item"), ln)  # siempre -LiteralPath (sin comodines)
            for t in targets:
                self.assertIn(t, allowed, ln)
            self.assertNotIn("$Root", ln)
        # carpetas: solo [IO.Directory]::Delete no recursivo (vacias) y AudiosApp con el recursivo (no entra en enlaces)
        deletes = re.findall(r"\[IO\.Directory\]::Delete\(([^)]*)\)", self.ps1)
        self.assertEqual(sorted(deletes), sorted(["$dir", "$App", "$Datos, $true"]), deletes)
        # lo que se borra de _app: por carpeta, solo extensiones de codigo y tres .txt por nombre exacto
        folders = re.search(r"\$AppFolders = \[ordered\]@\{(.*?)\n\}", self.ps1, re.S).group(1)
        per_folder = dict(re.findall(r'"([^"]+)"\s*=\s*@\(([^)]*)\)', folders))
        self.assertEqual(set(per_folder), {".", "static", "export", "__pycache__"})
        exts = set()
        for v in per_folder.values():
            exts |= set(re.findall(r'"(\.[a-z0-9]+)"', v))
        self.assertTrue(exts)
        self.assertFalse(exts & MEETING_EXT, exts & MEETING_EXT)
        txt = re.search(r"\$CodeTxt = @\(([^)]*)\)", self.ps1).group(1)
        self.assertEqual(set(re.findall(r'"([^"]+)"', txt)), {"version.txt", "python.txt", "export\\LEEME.txt"})
        launchers = re.search(r"\$RootLaunchers = @\(([^)]*)\)", self.ps1).group(1)
        self.assertTrue(all(n.endswith(".bat") for n in re.findall(r'"([^"]+)"', launchers)))
        # $file solo sale de _app y pasa por Test-AppFile; $dir solo son carpetas vacias de _app
        body = function_body(self.ps1, "Remove-AppFolder")
        recorre = r"(Get-ChildItem|Remove-Item)[^\n]*-Recurse"  # 5.1 entra en los enlaces: nada se recorre a ciegas
        self.assertFalse(re.search(recorre, body))
        self.assertIn("Get-ChildItem -LiteralPath $dir -File -Force", body)
        self.assertIn("if (Test-Enlace $d) { $ajenos += $dir; continue }", body)
        self.assertIn("(-not (Test-Enlace $file))", body)
        self.assertFalse(re.search(recorre, function_body(self.ps1, "Remove-Datos")))
        self.assertNotIn("Remove-Item", function_body(self.ps1, "Remove-Datos"))
        self.assertIn("$launcher = Join-Path $Root $n", function_body(self.ps1, "Remove-Launchers"))
        # AudiosApp solo con la casilla / -BorrarDatos, y solo si se llama asi
        self.assertIn("[IO.Directory]::Delete($Datos, $true)", function_body(self.ps1, "Remove-Datos"))
        self.assertIn('(Split-Path -Leaf $Datos) -ne "AudiosApp"', function_body(self.ps1, "Remove-Datos"))
        inv = function_body(self.ps1, "Invoke-Desinstalar")
        self.assertRegex(inv, r"if \(\$borrarDatos\) \{\s+Say [^\n]+\n\s+Remove-Datos")
        self.assertEqual(self.ps1.count("Remove-Datos"), 2)  # la definicion y esa llamada
        # solo se trabaja sobre una carpeta llamada _app con su desinstalador dentro
        self.assertIn('(Split-Path -Leaf $App) -ne "_app"', self.ps1)

    def test_grabando_no_toca_nada(self):
        inv = function_body(self.ps1, "Invoke-Desinstalar")
        i = inv.index('if ($st -eq "grabando")')
        j = inv.index('if ($w -ne "ok")')  # tambien si queda la grabadora viva o un proceso que no se deja cerrar
        for paso in ("Remove-Shortcuts", "Remove-Registro", "Remove-AppFolder", "Remove-Launchers", "Remove-Datos",
                     "Remove-Desinstalador"):
            self.assertLess(i, inv.index(paso), paso)
            self.assertLess(j, inv.index(paso), paso)
        self.assertIn('if ($resultado -eq "grabando") { Exit-Desinstalar 2 }', self.ps1)
        # todas las salidas de la copia relanzada borran la copia de %TEMP% (tambien al cancelar)
        tail = self.ps1[self.ps1.index("# --- 4. comprobacion"):]
        self.assertFalse(re.search(r"^\s*exit\b|\{ exit\b", tail, re.M), "salida sin Exit-Desinstalar")
        gone = function_body(self.ps1, "Wait-AppGone")  # la grabadora nunca se cierra a la fuerza
        self.assertLess(gone.index("recorder\\.py"), gone.index("Stop-Process"))
        # sin ventana y sin -Silencioso no se desinstala sin preguntar
        self.assertIn("if (-not $gui) {", self.ps1)

    def test_api_con_token_y_plan_b(self):
        """Contrato P1-02: si existe AudiosApp\\sesion.token, cada llamada lleva X-Chuleta-Token; un 401/403 es
        «la API no la ha apagado» (plan B: cerrar su proceso), nunca un exito, y el plan B nunca con una grabadora
        viva."""
        for text in (self.ps1, ps_text(INSTALAR)):
            heads = function_body(text, "Get-ApiHeaders")
            self.assertIn('sesion.token"', heads)
            self.assertIn('$h["X-Chuleta-Token"] = $tok', heads)
            self.assertIn('if ($post) { $h["X-Audios"] = "1" }', heads)
            self.assertNotIn("Log", heads)  # el token no va a ningun registro
            self.assertNotIn("Say", heads)
            self.assertIn("Headers = (Get-ApiHeaders $post)", function_body(text, "Invoke-Api"))
            self.assertFalse(re.search(r"Invoke-RestMethod\s+\"http", text))  # todas pasan por Invoke-Api
            self.assertIn("$ping.code -eq 401 -or $ping.code -eq 403", text.replace("$running.code", "$ping.code"))
            self.assertIn("'server\\.py'", text)  # sin respuesta a tiempo pero con su server.py escuchando: abierta
        stop = function_body(self.ps1, "Stop-App")
        self.assertIn('if ($ap.code -eq 200) { return "ok" }', stop)
        self.assertLess(stop.index("Test-Grabadora"), stop.index("Stop-Process"))
        self.assertIn("-LocalAddress 127.0.0.1", function_body(self.ps1, "Get-ListenerPid"))
        self.assertIn("^(pythonw?|py)$", function_body(self.ps1, "Get-ListenerPid"))  # nunca otro programa

    def test_la_entrada_y_el_desinstalador_se_quitan_al_final(self):
        """Si algo falla, la entrada de Aplicaciones instaladas y el desinstalador se quedan: se puede reintentar."""
        body = function_body(self.ps1, "Remove-AppFolder")
        self.assertIn("if (Same-Path $file.FullName $self) { continue }", body)
        self.assertNotIn("Remove-Item -LiteralPath $self", body)
        inv = function_body(self.ps1, "Invoke-Desinstalar")
        orden = [inv.index(s) for s in ("Remove-Shortcuts", "Remove-AppFolder", "Remove-Launchers",
                                        'if ($script:fallos.Count -gt 0) { return "incompleta" }', "Remove-Registro",
                                        "Remove-Desinstalador")]
        self.assertEqual(orden, sorted(orden))
        self.assertIn("Remove-Item -LiteralPath $self", function_body(self.ps1, "Remove-Desinstalador"))


@unittest.skipUnless(WINDOWS and shutil.which("powershell.exe"), "solo en Windows (PowerShell 5.1)")
class RegistroWindowsTests(unittest.TestCase):
    """Register-App de verdad, en una clave de prueba cuyo padre no existe (como ...\\Uninstall en un perfil nuevo:
    el E2E lo vio fallar con New-Item). Se lee con winreg y se borra al terminar."""

    def test_crea_la_clave_y_sus_tipos(self):
        import winreg
        base = "Software\\TencaTest-%d" % os.getpid()
        key = base + "\\Uninstall\\Tenca"

        def borrar(path):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as k:
                    while True:
                        try:
                            sub = winreg.EnumKey(k, 0)
                        except OSError:
                            break
                        borrar(path + "\\" + sub)
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass
        self.addCleanup(borrar, base)
        root = os.path.realpath(tempfile.mkdtemp(prefix="chuleta-reg-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        app = os.path.join(root, "_app")
        os.makedirs(app)
        with open(os.path.join(app, "server.py"), "wb") as f:
            f.write(b"x" * 5000)
        q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
        out = run_ps_functions(INSTALAR, ["Register-App", "Remove-RegistroAntiguo", "Same-Path", "Test-Instalacion"],
                               "$UninstallKey = " + q("HKCU:\\" + key) + "; $AppName = 'Tenca'; "
                               "$LegacyUninstallKeys = @(" + q("HKCU:\\" + base + "\\Uninstall\\Chuleta") + "); "
                               "$Publisher = 'Bernardo Stegmaier'; $RepoUrl = 'https://github.com/bastegman/tenca'; "
                               "function Say($t, $c) { Write-Host ('SAY ' + $t) }; "
                               "Write-Output ('R=' + (Register-App " + q(app) + " " + q(root) +
                               " '2026-10-31 abc1234'))")
        self.assertIn("R=True", out, out)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            got = {}
            i = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(k, i)
                except OSError:
                    break
                got[name] = (value, kind)
                i += 1
        sz, dw = winreg.REG_SZ, winreg.REG_DWORD
        cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%s"' % os.path.join(
            app, "desinstalar.ps1")
        esperado = {"DisplayName": ("Tenca", sz), "DisplayVersion": ("2026-10-31 abc1234", sz),
                    "Publisher": ("Bernardo Stegmaier", sz), "InstallLocation": (root, sz),
                    "DisplayIcon": (os.path.join(app, "static", "tenca.ico"), sz), "NoModify": (1, dw),
                    "NoRepair": (1, dw), "URLInfoAbout": ("https://github.com/bastegman/tenca", sz),
                    "UninstallString": (cmd, sz), "QuietUninstallString": (cmd + " -Silencioso", sz),
                    "EstimatedSize": (5, dw)}
        for name, val in esperado.items():
            self.assertEqual(got.get(name), val, name)
        self.assertRegex(got["InstallDate"][0], r"^\d{8}$")
        self.assertEqual(got["InstallDate"][1], sz)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@unittest.skipUnless(WINDOWS and shutil.which("powershell.exe"), "solo en Windows (PowerShell 5.1)")
class DesinstaladorWindowsTests(unittest.TestCase):
    """Ejecuta _app\\desinstalar.ps1 -Silencioso de verdad sobre una instalacion falsa en una carpeta temporal.
    No toca nada del PC: TEMP y LOCALAPPDATA van a la carpeta temporal, el puerto es otro (si Chuleta esta abierta no
    se apaga) y los accesos directos y la clave de registro solo se borran si apuntan a la carpeta temporal."""

    def make_tree(self, extra_in_app=False):
        # ruta larga: en el runner TEMP es C:\Users\RUNNER~1\... (8.3) y el desinstalador usa la larga
        base = os.path.realpath(tempfile.mkdtemp(prefix="chuleta-des-"))
        root = os.path.join(base, "Chuleta")
        app = os.path.join(root, "_app")
        os.makedirs(os.path.join(app, "static"))
        os.makedirs(os.path.join(app, "export"))
        os.makedirs(os.path.join(app, "__pycache__"))
        shutil.copy(DESINSTALAR, os.path.join(app, "desinstalar.ps1"))
        for rel in ("server.py", "common.py", "Tenca.bat", "instalar.ps1", "version.txt", "python.txt",
                    "static\\app.js", "static\\app.css", "static\\index.html", "static\\logo.svg",
                    "export\\LEEME.txt", "export\\INSTALAR.bat", "__pycache__\\server.cpython-313.pyc"):
            with open(os.path.join(app, rel), "w", encoding="utf-8") as f:
                f.write("x\n")
        for src in ("tenca.ico", "tenca.png"):
            shutil.copy(os.path.join(REPO, "_app", "static", src), os.path.join(app, "static", src))
        for name, body in (("INSTALAR.bat", '@echo off\r\npowershell -File "%~dp0_app\\instalar.ps1"\r\n'),
                           ("ABRIR TENCA.bat", '@echo off\r\ncall "%~dp0_app\\Tenca.bat"\r\n'),
                           ("LEEME.txt", "﻿CHULETA · manual\r\n"), ("LICENSE", "MIT\n")):
            with open(os.path.join(root, name), "w", encoding="utf-8", newline="") as f:
                f.write(body)
        meeting = os.path.join(root, "Proyecto Alfa")
        os.makedirs(os.path.join(meeting, "Vocabulario"))
        files = {}
        for name, data in (("2026-10-31 10-00-00.wav", b"RIFF" + bytes(64)), ("2026-10-31 10-00-00.txt", b"hola"),
                           ("2026-10-31 10-00-00.srt", b"1\n"), ("2026-10-31 10-00-00.md", b"# notas"),
                           ("2026-10-31 10-00-00.resumen.md", b"resumen"),
                           ("2026-10-31 10-00-00.hablantes.json", b"{}"), ("Vocabulario\\Tema.txt", b"Alfa")):
            p = os.path.join(meeting, name)
            with open(p, "wb") as f:
                f.write(data)
            files[p] = sha(p)
        loose = os.path.join(root, "nota suelta.md")  # una nota en la raiz, fuera de cualquier proyecto
        with open(loose, "wb") as f:
            f.write(b"suelta")
        files[loose] = sha(loose)
        alien = None
        if extra_in_app:  # algo que el usuario guardo dentro de _app: no es de la app y se queda
            alien = os.path.join(app, "mis notas.md")
            with open(alien, "wb") as f:
                f.write(b"mio")
            files[alien] = sha(alien)
        local = os.path.join(base, "local")
        datos = os.path.join(local, "AudiosApp")
        os.makedirs(os.path.join(datos, "webview"))
        for n in ("settings.json", "voces.json", "server.log"):
            with open(os.path.join(datos, n), "w", encoding="utf-8") as f:
                f.write("{}")
        temp = os.path.join(base, "temp")
        os.makedirs(temp)
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return root, app, datos, temp, local, files, alien

    def run_uninstaller(self, app, temp, local, *flags):
        env = dict(os.environ, TEMP=temp, TMP=temp, LOCALAPPDATA=local)
        return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               os.path.join(app, "desinstalar.ps1"), "-Silencioso", "-Port", str(free_port()), *flags],
                              env=env, capture_output=True, text=True, errors="replace", timeout=180)

    def log(self, temp):
        p = os.path.join(temp, "tenca-desinstalar.log")
        return open(p, encoding="utf-8-sig").read() if os.path.exists(p) else ""

    def test_quita_la_app_y_deja_las_reuniones(self):
        root, app, datos, temp, local, files, alien = self.make_tree(extra_in_app=True)
        r = self.run_uninstaller(app, temp, local)
        log = self.log(temp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + log)
        for p, h in files.items():  # reuniones, vocabulario, la nota suelta y lo ajeno de _app: byte a byte
            self.assertTrue(os.path.exists(p), p)
            self.assertEqual(sha(p), h, p)
        self.assertEqual(os.listdir(app), ["mis notas.md"])  # de _app solo queda lo que no es de la app
        self.assertFalse(os.path.exists(os.path.join(root, "INSTALAR.bat")))
        self.assertFalse(os.path.exists(os.path.join(root, "ABRIR TENCA.bat")))
        self.assertTrue(os.path.exists(os.path.join(root, "LEEME.txt")))
        self.assertTrue(os.path.exists(os.path.join(root, "LICENSE")))
        self.assertTrue(os.path.exists(os.path.join(datos, "voces.json")))  # sin -BorrarDatos, AudiosApp se queda
        self.assertIn("relanzo:", log)
        self.assertIn("se queda (no es de la app):", log)
        self.assertIn(f"se ha desinstalado. Tus reuniones siguen en {root}".lower(), log.lower())
        self.assertEqual([n for n in os.listdir(temp) if n.endswith(".ps1")], [])  # la copia de %TEMP% se borra

    def test_no_entra_en_enlaces(self):
        root, app, datos, temp, local, files, _ = self.make_tree()
        fuera = os.path.join(os.path.dirname(root), "fuera de la app")
        os.makedirs(os.path.join(fuera, "sub"))
        victimas = {}
        for rel in ("foto.png", "script.py", "sub\\pagina.html", "notas.md"):
            q = os.path.join(fuera, rel)
            with open(q, "wb") as f:
                f.write(b"del usuario")
            victimas[q] = sha(q)
        # junction dentro de _app\static y otra en AudiosApp (no hacen falta permisos de administrador)
        for link in (os.path.join(app, "static", "enlace"), os.path.join(datos, "modelos")):
            r = subprocess.run(["cmd", "/c", "mklink", "/J", link, fuera], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_uninstaller(app, temp, local, "-BorrarDatos")
        log = self.log(temp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + log)
        for q, h in victimas.items():  # el destino de los enlaces, intacto
            self.assertTrue(os.path.exists(q), q + "\n" + log)
            self.assertEqual(sha(q), h, q)
        for p, h in files.items():
            self.assertEqual(sha(p), h, p)
        self.assertFalse(os.path.exists(datos), log)  # AudiosApp borrada (el enlace, no su destino)
        self.assertIn("se queda (no es de la app):", log)  # el enlace de static se queda y se dice

    def test_borrar_datos(self):
        root, app, datos, temp, local, files, _ = self.make_tree()
        r = self.run_uninstaller(app, temp, local, "-BorrarDatos")
        log = self.log(temp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + log)
        self.assertFalse(os.path.exists(app), log)  # sin nada ajeno, _app desaparece entera (con el desinstalador)
        self.assertFalse(os.path.exists(datos), log)
        for p, h in files.items():
            self.assertEqual(sha(p), h, p)

    def test_no_es_una_carpeta_de_chuleta(self):
        base = tempfile.mkdtemp(prefix="chuleta-des-no-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        other = os.path.join(base, "otra")
        os.makedirs(other)
        shutil.copy(DESINSTALAR, os.path.join(other, "desinstalar.ps1"))
        keep = os.path.join(other, "server.py")
        with open(keep, "w") as f:
            f.write("x")
        temp = os.path.join(base, "temp")
        os.makedirs(temp)
        r = self.run_uninstaller(other, temp, base)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(keep))  # una carpeta que no se llama _app no se toca


if __name__ == "__main__":
    unittest.main()
