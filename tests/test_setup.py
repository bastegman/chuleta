# -*- coding: utf-8 -*-
"""Setup.exe, fase 1 (ítem 8 del backlog; msg 1296): scripts/setup/tenca.iss (Inno Setup que envuelve instalar.ps1),
scripts/build_setup.py (prepara el contenido del zip y llama a ISCC) e instalar.ps1 -DesdeSetup. Lo de Windows de
verdad (compilar con ISCC, instalar, el asistente, desinstalar, encima de Chuleta) lo prueba setup-exe.yml con
scripts/e2e/setup_e2e.ps1; aquí, lo que se puede comprobar en cualquier sistema."""
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from support import REPO, server
from test_desinstalar import INSTALAR, function_body, powershell, ps_text

sys.path.insert(0, os.path.join(REPO, "scripts"))
import build_setup  # noqa: E402

ISS = os.path.join(REPO, "scripts", "setup", "tenca.iss")


def iss_text():
    with open(ISS, "rb") as f:
        raw = f.read()
    return raw, raw.decode("utf-8-sig")


def directivas(text):
    """[Setup] como {clave: valor} (sin comentarios)."""
    sec = text.split("[Setup]", 1)[1].split("\n[", 1)[0]
    out = {}
    for ln in sec.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith(";") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class VersionTests(unittest.TestCase):
    def test_version_num(self):
        self.assertEqual(build_setup.version_num("2026-11-09 · abc1234"), "2026.11.9.0")
        self.assertEqual(build_setup.version_num("2026-01-02"), "2026.1.2.0")
        self.assertEqual(build_setup.version_num("e2e-chuleta 2242e63"), "0.0.0.0")
        self.assertEqual(build_setup.version_num(""), "0.0.0.0")


class PrepararTests(unittest.TestCase):
    """El Setup lleva exactamente lo que lleva el zip (server.build_package), con la misma versión."""

    def test_mismo_contenido_que_el_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = os.path.join(tmp, "p")
            n = build_setup.preparar(carpeta, "2026-11-09 · abc1234")
            z = os.path.join(tmp, "x.zip")
            server.build_package(z, [], version="2026-11-09 · abc1234")
            with zipfile.ZipFile(z) as zf:
                del_zip = sorted(x for x in zf.namelist() if not x.endswith("/"))
                ver_zip = zf.read(f"{server.PKG_ROOT}/_app/version.txt")
            preparados = sorted(os.path.relpath(os.path.join(d, f), carpeta).replace(os.sep, "/")
                                for d, _, fs in os.walk(carpeta) for f in fs)
            self.assertEqual(preparados, del_zip)
            self.assertEqual(n, len(del_zip))
            for f in ("Tenca/INSTALAR.bat", "Tenca/_app/instalar.ps1", "Tenca/_app/desinstalar.ps1",
                      "Tenca/_app/static/tenca.ico", "Tenca/_app/static/tenca.png"):
                self.assertIn(f, preparados)
            with open(os.path.join(carpeta, "Tenca", "_app", "version.txt"), "rb") as f:
                self.assertEqual(f.read(), ver_zip)
            self.assertIn(b"2026-11-09 \xc2\xb7 abc1234", ver_zip)

    def test_carpeta_con_restos_no(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "viejo.txt"), "w") as f:
                f.write("x")
            with self.assertRaises(SystemExit):
                build_setup.preparar(tmp, "2026-11-09")

    def test_orden_de_iscc(self):
        o = build_setup.orden_iscc("ISCC.exe", "C:\\f", "2026-11-09 · abc1234", "C:\\out", "Tenca - Setup")
        self.assertEqual(o[0], "ISCC.exe")
        self.assertIn("/DFuente=C:\\f", o)
        self.assertIn("/DVersion=2026-11-09 · abc1234", o)
        self.assertIn("/DVersionNum=2026.11.9.0", o)
        self.assertIn("/DSalida=C:\\out", o)
        self.assertIn("/DNombre=Tenca - Setup", o)
        self.assertEqual(o[-1], ISS)

    def test_sin_iscc_lo_dice(self):
        with mock.patch.object(build_setup, "buscar_iscc", return_value=None), \
                mock.patch.object(build_setup.build_zip, "write_version"):
            self.assertEqual(build_setup.main(["x", os.path.join(tempfile.gettempdir(), "no.exe")]), 2)

    def test_preparar_por_la_linea_de_ordenes(self):
        """--preparar no toca el repo (_app/version.txt, que leen otros tests: con «prueba» dentro fallaba
        test_package)."""
        ver = os.path.join(REPO, "_app", "version.txt")
        antes = open(ver, "rb").read() if os.path.isfile(ver) else None
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "build_setup.py"), "--version",
                                "2026-11-09 · prueba", "--preparar", os.path.join(tmp, "p")],
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ISCC.exe /Q", r.stdout)
            self.assertIn('"/DVersion=2026-11-09 · prueba"', r.stdout)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "p", "Tenca", "_app", "instalar.ps1")))
        self.assertEqual(open(ver, "rb").read() if os.path.isfile(ver) else None, antes)


class IssTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.text = iss_text()
        self.d = directivas(self.text)

    def test_utf8_con_bom(self):
        # Inno lee como ANSI un .iss sin BOM: «», tildes y el · de la versión saldrían mal en el asistente
        self.assertTrue(self.raw.startswith(b"\xef\xbb\xbf"))

    def test_por_usuario_sin_carpeta_ni_desinstalador_propios(self):
        """Inno solo deja los ficheros en {tmp}: carpeta, entrada de «Aplicaciones instaladas» y desinstalador son los
        de instalar.ps1 (una sola entrada, «Tenca», con desinstalar.ps1)."""
        for k, v in {"PrivilegesRequired": "lowest", "CreateAppDir": "no", "Uninstallable": "no",
                     "DisableWelcomePage": "no", "DisableReadyPage": "yes", "DisableFinishedPage": "yes",
                     "DisableProgramGroupPage": "yes", "AppName": "Tenca", "MinVersion": "10.0"}.items():
            self.assertEqual(self.d.get(k), v, k)
        self.assertIn("modern", self.d["WizardStyle"].split())
        self.assertIn('DestDir: "{tmp}\\Tenca"', self.text)
        self.assertNotIn("{app}", self.text.split("[Code]")[0])
        self.assertNotIn("[Icons]", self.text)  # los accesos directos los hace instalar.ps1 (con el AppUserModelID)
        self.assertNotIn("[Registry]", self.text)
        self.assertNotIn("[UninstallRun]", self.text)

    def test_lanza_instalar_ps1(self):
        code = self.text.split("[Code]", 1)[1]
        self.assertIn("{tmp}\\Tenca\\_app\\instalar.ps1", code)
        self.assertIn("-NoProfile -ExecutionPolicy Bypass -File", code)
        self.assertIn("-DesdeSetup", code)
        self.assertRegex(code, r"if WizardSilent then\s+begin[^.]*-NoLaunch[^.]*SW_HIDE")  # en silencio, sin abrirla
        self.assertIn("{sysnative}\\WindowsPowerShell\\v1.0\\powershell.exe", code)  # el de 64 bits, como INSTALAR.bat
        self.assertIn("ewWaitUntilTerminated", code)
        self.assertIn("function GetCustomSetupExitCode: Integer;", code)
        self.assertRegex(code, r"if CurPageID = wpWelcome then\s+WizardForm\.NextButton\.Caption := "
                               r"SetupMessage\(msgButtonInstall\)")

    def test_comentarios_del_codigo_sin_llaves(self):
        """En Pascal un comentario { … } acaba en la primera llave de cierre: «{sys}» dentro de uno dejaba el resto como
        código y ISCC no compilaba. Fuera de las cadenas, el código no lleva llaves (comentarios con //)."""
        code = re.sub(r"'[^']*'", "''", self.text.split("[Code]", 1)[1])
        self.assertEqual([ln for ln in code.splitlines() if "{" in ln or "}" in ln], [])

    def test_iconos_e_imagenes(self):
        self.assertEqual(self.d["SetupIconFile"], "{#Fuente}\\Tenca\\_app\\static\\tenca.ico")
        self.assertEqual(self.d["WizardSmallImageFile"], "{#Fuente}\\Tenca\\_app\\static\\tenca.png")
        tamanos = []
        for nombre in self.d["WizardImageFile"].split(","):
            with open(os.path.join(os.path.dirname(ISS), nombre.strip()), "rb") as f:
                cab = f.read(24)
            self.assertEqual(cab[:8], b"\x89PNG\r\n\x1a\n", nombre)
            tamanos.append(struct.unpack(">II", cab[16:24]))
        self.assertEqual(tamanos, [(240, 459), (480, 918)])  # proporción 164:314 de Inno, al 100 % y al 200 %

    def test_textos_del_asistente(self):
        msgs = self.text.split("[Messages]", 1)[1].split("\n[", 1)[0]
        self.assertIn("SetupWindowTitle=Instalar %1", msgs)
        self.assertIn("sin permisos de administrador", msgs)
        self.assertIn("tus reuniones, voces y ajustes no se tocan", msgs)
        self.assertIn("{#Version}", msgs)
        self.assertIn('MessagesFile: "compiler:Languages\\Spanish.isl"', self.text)

    def test_sin_firma_no_se_esquiva(self):
        """Sin certificado: nada de trucos para saltarse SmartScreen (ni SignTool con un certificado propio)."""
        self.assertNotIn("SignTool", self.text)
        self.assertIn("SmartScreen", self.text)


class DesdeSetupTests(unittest.TestCase):
    """instalar.ps1 -DesdeSetup: no habla de la carpeta descomprimida (es la temporal del Setup), nunca se instala en
    ella y sale con 1 si falla también en modo ventana (Setup.exe lo devuelve)."""

    def setUp(self):
        self.ps1 = ps_text(INSTALAR)

    def test_parametro_y_textos(self):
        self.assertIn("[switch]$DesdeSetup)", self.ps1)
        self.assertIn("exit $(if ($DesdeSetup) { 1 } else { 0 })", function_body(self.ps1, "Fail"))
        self.assertIn('if ($DesdeSetup) { Say "    Actualizada." Green }', self.ps1)
        self.assertIn("$borrable = -not $DesdeSetup -and $origen", self.ps1)
        self.assertIn('"    Copiada desde el instalador"', self.ps1)
        i = self.ps1.index("if (-not $dest -and $DesdeSetup)")
        self.assertLess(i, self.ps1.index('se instala aquí ($root)."'))  # antes de instalarse en la carpeta temporal
        self.assertIn("Fail ", self.ps1[i:i + 300])

    @unittest.skipUnless(powershell(), "sin PowerShell (CHULETA_PWSH, powershell.exe o pwsh)")
    def test_codigo_de_salida_de_fail(self):
        """Fail en modo ventana: 0 con INSTALAR.bat (el .bat haría pause), 1 desde el Setup; por consola, siempre 1."""
        def rc(desde, ventana):
            script = ("$ast = [System.Management.Automation.Language.Parser]::ParseFile('" + INSTALAR.replace("'", "''")
                      + "', [ref]$null, [ref]$null); foreach ($d in $ast.FindAll({ param($a) $a -is "
                      "[System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) { "
                      "if ($d.Name -eq 'Fail') { . ([scriptblock]::Create($d.Extent.Text)) } }; "
                      "function Log-File($t) {}; function Finish-Ui($ok, $t) { Write-Output 'VENTANA' }; "
                      f"$DesdeSetup = ${'true' if desde else 'false'}; "
                      f"$script:ui = {'@{}' if ventana else '$null'}; Fail 'algo'")
            r = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            return r.returncode, "VENTANA" in r.stdout
        self.assertEqual(rc(False, True), (0, True))
        self.assertEqual(rc(True, True), (1, True))
        self.assertEqual(rc(False, False), (1, False))
        self.assertEqual(rc(True, False), (1, False))


class FlujoTests(unittest.TestCase):
    def test_workflow(self):
        with open(os.path.join(REPO, ".github", "workflows", "setup-exe.yml"), encoding="utf-8") as f:
            wf = f.read()
        self.assertIn("python scripts\\build_setup.py", wf)
        self.assertIn("scripts\\e2e\\setup_e2e.ps1", wf)
        self.assertIn("name: Tenca-Setup", wf)
        self.assertIn("shell: powershell", wf)  # 5.1, como en el PC del usuario
        self.assertRegex(wf, r"-Chuleta \"[0-9a-f]{40}\"")

    def test_e2e_utf8_con_bom(self):
        # PowerShell 5.1 lee como ANSI un .ps1 sin BOM: «» y tildes saldrían mal y algunos bytes UTF-8 (0x91-0x94) los
        # tomaría por comillas
        with open(os.path.join(REPO, "scripts", "e2e", "setup_e2e.ps1"), "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(re.search(rb"(?<!\r)\n", raw))

    def test_el_zip_no_lleva_el_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = os.path.join(tmp, "x.zip")
            server.build_package(z, [], version="v")
            with zipfile.ZipFile(z) as zf:
                self.assertFalse([n for n in zf.namelist() if "setup" in n.lower() or n.endswith(".iss")])


if __name__ == "__main__":
    unittest.main()
