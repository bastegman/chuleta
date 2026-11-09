# Instalador de Tenca (lo lanza INSTALAR.bat). No necesita permisos de administrador:
#   1. Python de 3.10 a 3.13 (si no hay, instala 3.13 con winget solo para este usuario)
#   2. Librerias: faster-whisper (Whisper local + PyAV para leer audio), soundcard (grabadora), requests y truststore;
#      opcionales, cada una aparte (si no se instalan o no cargan, la app funciona igual): pywebview (la ventana de la
#      app sobre WebView2; sin ella se abre en Edge), sherpa-onnx (quien habla) y pywin32 (calendario de Outlook)
#   3. Accesos directos "Tenca" en el escritorio y en el menu Inicio: apuntan a pythonw.exe (sin ventana de consola)
#      y llevan el AppUserModelID de la app, para que la barra de tareas agrupe y ancle la ventana como algo propio.
#   4. Carpeta propia: si no hay instalacion anterior, la app se copia de la carpeta descomprimida (Descargas...) a
#      %USERPROFILE%\Tenca (fuera de Descargas y de OneDrive; si esa carpeta ya existe y no es de la app,
#      "Tenca (2)"...) y los accesos directos apuntan alli. -Destino <carpeta> elige otra (nueva o vacia).
#      Si ya estaba instalada (por este orden: la entrada de "Aplicaciones instaladas", el acceso directo del
#      escritorio o de Inicio, o la carpeta propia), se actualiza en su sitio: ahi estan sus grabaciones, que nunca se
#      mueven. La carpeta conserva su nombre aunque sea el anterior (%USERPROFILE%\Chuleta): renombrarla romperia lo
#      que apunta a ella (OBS, accesos del usuario, anclajes) y no se gana nada.
#   5. "Aplicaciones instaladas": HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Tenca (sin
#      administrador) con la version, el icono y la orden de _app\desinstalar.ps1, que quita la app y NO las reuniones.
#   6. Nombres anteriores de la app ("Chuleta" hasta el 09/11/2026, "Notas" hasta el 31/10/2026): al instalar encima
#      no queda nada con ellos. Los accesos directos que apuntan a esta instalacion, en el escritorio, en Inicio y en la
#      carpeta Inicio de Windows (la copia de "Arrancar con Windows"), pasan a llamarse "Tenca" (se renombra el primero
#      y se quitan los que sobren: nunca dos), su entrada de "Aplicaciones instaladas" se quita y sus "ABRIR ... .bat"
#      de la raiz tambien.
#      Lo que apunta a OTRA copia de la app se queda y se dice; un acceso directo que no es de la app no se toca.
#      Los anclados a la barra de tareas no se pueden cambiar desde un programa: se avisa de como volver a anclarla.
#      Los datos no se mueven: ajustes, claves y voces siguen en %LOCALAPPDATA%\AudiosApp, y reuniones y listas en su carpeta.
# Se puede repetir sin problema. Para probarlo sin tocar nada: -NoShortcut / -NoLaunch, y -Port / -Desktop
# (otro puerto y otra carpeta de escritorio, como si fuera otro PC: sin -Destino se instala donde esta descomprimido y
# no se lee ni se escribe la entrada de "Aplicaciones instaladas" ni la carpeta Inicio de Windows). -AppName cambia el
# nombre visible (el de common.APP_NAME); las carpetas, los .bat y la clave de registro no cambian.
# Con escritorio abre su propia ventana (WinForms: pasos, barra y detalle) y esconde la consola; -Consola, -NoShortcut o
# -NoLaunch (pruebas, CI) lo dejan como consola de siempre.
# Solo para CI: -AutoCerrar N (o la variable NOTAS_INSTALADOR_AUTOCERRAR=N) cierra la ventana sola N segundos despues de
# terminar, bien o mal; si termino bien, como si se hubiera pulsado "Abrir". NOTAS_INSTALADOR_SIMULAR_FALLO=pip ensaya la
# pantalla de error sin tocar la red. Ninguna de las dos se usa en un PC normal.
param([switch]$NoShortcut, [switch]$NoLaunch, [int]$Port = 8765, [string]$Desktop = "", [string]$AppName = "Tenca",
      [switch]$Consola, [int]$AutoCerrar = 0, [string]$Destino = "")

$ErrorActionPreference = "Continue"
if ($AutoCerrar -le 0 -and $env:NOTAS_INSTALADOR_AUTOCERRAR) { try { $AutoCerrar = [int]$env:NOTAS_INSTALADOR_AUTOCERRAR } catch {} }
# todo lo que se escribe (consola y ventana) queda tambien en %LOCALAPPDATA%\AudiosApp\instalar.log, junto a server.log
$script:logFile = $null
try {
    $d = Join-Path $env:LOCALAPPDATA "AudiosApp"
    New-Item -ItemType Directory -Force $d | Out-Null
    $script:logFile = Join-Path $d "instalar.log"
    Add-Content -LiteralPath $script:logFile -Value ("==== " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " instalar.ps1 desde " + $PSScriptRoot) -Encoding UTF8
} catch { $script:logFile = $null }
function Log-File($text) {
    if ($script:logFile) { try { Add-Content -LiteralPath $script:logFile -Value ("$text" -replace "`r", "") -Encoding UTF8 } catch {} }
}
trap {
    # error inesperado (no un Fail): en modo ventana se ensena ahi, porque la consola esta escondida; en consola se propaga
    if ($script:ui -and -not $script:ui.done) { Finish-Ui $false "Error inesperado: $($_.Exception.Message)"; exit 0 }
    break
}
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $app
$packages = @("faster-whisper==1.2.1", "soundcard>=0.4.3", "requests", "truststore")
# opcionales: se instalan una a una y aparte del resto; si alguna falla, solo se avisa (la app funciona sin ella).
# pywebview tambien: depende de proxy_tools, que solo se publica como codigo fuente, y con un pip "only-binary" o un
# repositorio de la empresa que solo tenga paquetes compilados no se instala; sin ella la app se abre en Edge
$optional = @("pywebview==6.2.1", "sherpa-onnx==2.0.1", "pywin32==312")  # ventana propia / quien habla / calendario de Outlook
$MaxPy = 313  # el Python mas nuevo con faster-whisper/ctranslate2 publicados (3.14 aun no tiene wheels)
# = common.APP_ID: la ventana lo fija en el proceso y el acceso directo lo lleva en el .lnk. No cambia con el nombre:
# con otro, la ventana saldria como un boton aparte junto al anclaje que ya tenga hecho el usuario (dos iconos)
$AppId = "Bernardo.Chuleta"
# nombres anteriores de la app (= common.APP_FORMER_NAMES, del mas reciente al mas antiguo): lo que queda con ellos
# se migra a $AppName (punto 6 de arriba)
$LegacyNames = @(@("Chuleta", "Notas") | Where-Object { $_ -ne $AppName })
$FolderName = "Tenca"  # carpeta propia en el perfil del usuario (no cambia con -AppName, como los .bat)
$LegacyArranque = @("Chuleta")  # = common.ARRANQUE_ANTIGUOS: el arranque con Windows nacio con Chuleta; un Notas.lnk de la carpeta Inicio es de otro programa
$LegacyFolders = @("Chuleta")  # la carpeta propia con el nombre anterior: si es de la app, se sigue usando
$Launcher = "ABRIR TENCA.bat"  # el .bat de la raiz que abre la app con consola (llama a _app\Tenca.bat)
$LegacyLaunchers = @("ABRIR CHULETA.bat", "ABRIR NOTAS.bat")  # los de los nombres anteriores: sobran
$UninstallBase = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
$UninstallKey = "$UninstallBase\Tenca"  # "Aplicaciones instaladas"
$LegacyUninstallKeys = @("$UninstallBase\Chuleta")  # la entrada del nombre anterior: se quita al instalar encima
$Publisher = "Bernardo Stegmaier"
$RepoUrl = "https://github.com/bastegman/tenca"  # = server.REPO_URL
# con -NoShortcut (probar sin tocar nada) o -Desktop (otro PC simulado) no se lee ni se escribe nada del sistema:
# ni la carpeta propia por defecto ni la entrada de "Aplicaciones instaladas"
$Sistema = -not $NoShortcut -and -not $Desktop

# --- presentacion: consola y, si hay ventana ($script:ui), tambien la ventana. La logica de instalacion no depende de esto.
$script:ui = $null
function Ui-Log($text) {
    if (-not $script:ui) { return }
    $script:ui.log.AppendText((("$text" -replace "`r", "").TrimEnd()) + "`r`n")
    [System.Windows.Forms.Application]::DoEvents()
}
function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color; Ui-Log $text; Log-File $text }
function Step($text) {
    Write-Host ""; Write-Host "  > $text" -ForegroundColor Cyan
    Log-File ""
    Log-File "> $text"
    if ($script:ui) {
        $ui = $script:ui
        if ($ui.current) { $ui.current.Text = [string][char]0x2713 + " " + $ui.current.Tag; $ui.current.ForeColor = $ui.verde }
        $l = New-Object System.Windows.Forms.Label
        $l.AutoSize = $true
        $l.MaximumSize = New-Object System.Drawing.Size(290, 0)
        $l.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 6)
        $l.Tag = $text
        $l.Text = [string][char]0x25B6 + " " + $text
        $l.ForeColor = $ui.azul
        $ui.steps.Controls.Add($l)
        $ui.current = $l
        $ui.n++
        $ui.bar.Value = [Math]::Min(95, [int](100 * $ui.n / $ui.total))
        $ui.status.Text = $text
        Ui-Log ""
        Ui-Log "> $text"
    }
}
function Finish-Ui($ok, $text) {
    # fin en modo ventana: marca el ultimo paso, ensena el resultado y espera a que el usuario cierre (o pulse Abrir)
    $ui = $script:ui
    if ($ui.current) {
        $ui.current.Text = [string][char]$(if ($ok) { 0x2713 } else { 0x2717 }) + " " + $ui.current.Tag
        $ui.current.ForeColor = $(if ($ok) { $ui.verde } else { $ui.rojo })
    }
    if ($ok) { $ui.bar.Value = 100 }
    $ui.status.Text = $text
    $ui.status.ForeColor = $(if ($ok) { $ui.verde } else { $ui.rojo })
    $ui.btnOpen.Enabled = $ok
    $ui.btnCopy.Visible = -not $ok
    $ui.done = $true
    Ui-Log $(if ($ok) { "Listo." } else { "X $text" })
    $ui.btnClose.Focus() | Out-Null
    $t0 = Get-Date
    while ($ui.form.Visible) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
        if ($AutoCerrar -gt 0 -and ((Get-Date) - $t0).TotalSeconds -ge $AutoCerrar) {  # solo CI
            if ($ok) { $ui.abrir = -not $NoLaunch }
            Log-File "autocierre tras $AutoCerrar s"
            $ui.form.Close()
        }
    }
}
function Fail($text) {
    Write-Host ""
    Write-Host "  X  $text" -ForegroundColor Red
    Log-File "X $text"
    if ($script:ui) { Finish-Ui $false $text; exit 0 }  # el error ya se ha visto en la ventana: el .bat no tiene que parar
    exit 1
}
function Hide-Console {
    # la consola de INSTALAR.bat se esconde mientras la ventana esta abierta (INSTALAR.bat solo hace pause si fallamos)
    try {
        if (-not ("TencaLnk.Win" -as [type])) {
            Add-Type -Namespace TencaLnk -Name Win -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);'
        }
        $h = [TencaLnk.Win]::GetConsoleWindow()
        if ($h -ne [IntPtr]::Zero) { [TencaLnk.Win]::ShowWindow($h, 0) | Out-Null }
    } catch {}
}
function New-InstallerWindow {
    # ventana del instalador (WinForms, sin dependencias): logo, nombre, pasos, detalle, barra y botones. Devuelve $true si se abrio.
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
    } catch { return $false }
    try {
        $f = New-Object System.Windows.Forms.Form
        $f.Text = "Instalar $AppName"
        $f.StartPosition = "CenterScreen"
        $f.FormBorderStyle = "FixedDialog"
        $f.MaximizeBox = $false
        $f.ClientSize = New-Object System.Drawing.Size(760, 600)
        $f.Font = New-Object System.Drawing.Font("Segoe UI", 10)
        $f.BackColor = [System.Drawing.Color]::White
        try { $f.Icon = New-Object System.Drawing.Icon((Join-Path $app "static\tenca.ico")) } catch {}
        $logo = New-Object System.Windows.Forms.PictureBox
        $logo.Location = New-Object System.Drawing.Point(24, 20)
        $logo.Size = New-Object System.Drawing.Size(64, 64)
        $logo.SizeMode = "Zoom"
        try { $logo.Image = [System.Drawing.Image]::FromFile((Join-Path $app "static\tenca.png")) } catch {}
        $title = New-Object System.Windows.Forms.Label
        $title.Text = $AppName
        $title.Font = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold)
        $title.Location = New-Object System.Drawing.Point(100, 18)
        $title.AutoSize = $true
        $sub = New-Object System.Windows.Forms.Label
        $sub.Text = "grabar, transcribir y resumir reuniones  -  instalación (sin permisos de administrador)"
        $sub.ForeColor = [System.Drawing.Color]::DimGray
        $sub.Location = New-Object System.Drawing.Point(104, 58)
        $sub.AutoSize = $true
        $steps = New-Object System.Windows.Forms.FlowLayoutPanel
        $steps.Location = New-Object System.Drawing.Point(24, 104)
        $steps.Size = New-Object System.Drawing.Size(300, 330)
        $steps.FlowDirection = "TopDown"
        $steps.WrapContents = $false
        $steps.AutoScroll = $true
        $log = New-Object System.Windows.Forms.TextBox
        $log.Multiline = $true
        $log.ReadOnly = $true
        $log.ScrollBars = "Vertical"
        $log.WordWrap = $true
        $log.Font = New-Object System.Drawing.Font("Consolas", 9)
        $log.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 250)
        $log.Location = New-Object System.Drawing.Point(340, 104)
        $log.Size = New-Object System.Drawing.Size(396, 330)
        $bar = New-Object System.Windows.Forms.ProgressBar
        $bar.Location = New-Object System.Drawing.Point(24, 450)
        $bar.Size = New-Object System.Drawing.Size(712, 14)
        $bar.Style = "Continuous"
        $bar.Maximum = 100
        $status = New-Object System.Windows.Forms.Label
        $status.Location = New-Object System.Drawing.Point(24, 472)
        $status.Size = New-Object System.Drawing.Size(712, 72)
        $status.Text = "Preparando..."
        $btnCopy = New-Object System.Windows.Forms.Button
        $btnCopy.Text = "Copiar el detalle"
        $btnCopy.Location = New-Object System.Drawing.Point(24, 548)
        $btnCopy.Size = New-Object System.Drawing.Size(150, 34)
        $btnCopy.Visible = $false
        $btnOpen = New-Object System.Windows.Forms.Button
        $btnOpen.Text = "Abrir $AppName"
        $btnOpen.Location = New-Object System.Drawing.Point(486, 548)
        $btnOpen.Size = New-Object System.Drawing.Size(130, 34)
        $btnOpen.Enabled = $false
        $btnClose = New-Object System.Windows.Forms.Button
        $btnClose.Text = "Cerrar"
        $btnClose.Location = New-Object System.Drawing.Point(626, 548)
        $btnClose.Size = New-Object System.Drawing.Size(110, 34)
        $f.Controls.AddRange(@($logo, $title, $sub, $steps, $log, $bar, $status, $btnCopy, $btnOpen, $btnClose))
        $btnClose.Add_Click({ $script:ui.form.Close() })
        $btnCopy.Add_Click({ try { [System.Windows.Forms.Clipboard]::SetText($script:ui.log.Text) } catch {} })
        $btnOpen.Add_Click({ $script:ui.abrir = $true; $script:ui.form.Close() })
        $f.Add_FormClosing({ param($s, $e) if (-not $script:ui.done) { $e.Cancel = $true; $script:ui.status.Text = "Espera a que termine la instalación..." } })
        $script:ui = @{ form = $f; steps = $steps; log = $log; bar = $bar; status = $status; btnOpen = $btnOpen; btnClose = $btnClose
                        btnCopy = $btnCopy; current = $null; n = 0; total = 7; done = $false; abrir = $false
                        verde = [System.Drawing.Color]::FromArgb(22, 128, 61); rojo = [System.Drawing.Color]::FromArgb(185, 28, 28)
                        azul = [System.Drawing.Color]::FromArgb(37, 99, 235) }
        $f.Show()
        $f.Activate()
        [System.Windows.Forms.Application]::DoEvents()
        Hide-Console
        return $true
    } catch {
        $script:ui = $null
        Write-Host "  (sin ventana: $($_.Exception.Message); sigo por consola)" -ForegroundColor Yellow
        return $false
    }
}

function Set-ShortcutAppId($path, $id) {
    # System.AppUserModel.ID del .lnk (IPropertyStore de la shell). Con el mismo id que fija server.py en su proceso,
    # Windows agrupa la ventana bajo este acceso directo y "Anclar a la barra de tareas" ancla el acceso directo
    # (icono y argumentos) y no pythonw.exe a secas. Si falla, el acceso directo funciona igual.
    try {
        if (-not ("TencaLnk.PropertyStore" -as [type])) {
            Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace TencaLnk {
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  public struct PropertyKey { public Guid fmtid; public uint pid; public PropertyKey(Guid f, uint p) { fmtid = f; pid = p; } }
  [StructLayout(LayoutKind.Explicit)]
  public struct PropVariant {
    [FieldOffset(0)] public ushort vt;
    [FieldOffset(8)] public IntPtr p;
    [FieldOffset(16)] public IntPtr p2;
  }
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore {
    void GetCount(out uint cProps);
    void GetAt(uint iProp, out PropertyKey pkey);
    void GetValue(ref PropertyKey key, out PropVariant pv);
    void SetValue(ref PropertyKey key, ref PropVariant pv);
    void Commit();
  }
  public static class PropertyStore {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern IPropertyStore SHGetPropertyStoreFromParsingName(string path, IntPtr pbc, int flags, ref Guid riid);
    [DllImport("ole32.dll", PreserveSig = false)]
    static extern void PropVariantClear(ref PropVariant pv);
    public static void SetAppId(string lnk, string appId) {
      Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
      IPropertyStore ps = SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref iid);
      PropertyKey key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
      PropVariant pv = new PropVariant();
      pv.vt = 31;
      pv.p = Marshal.StringToCoTaskMemUni(appId);
      try { ps.SetValue(ref key, ref pv); ps.Commit(); }
      finally { PropVariantClear(ref pv); Marshal.ReleaseComObject(ps); }
    }
  }
}
"@
        }
        [TencaLnk.PropertyStore]::SetAppId($path, $id)
        return $true
    } catch {
        Say "    (el acceso directo no lleva AppUserModelID: $($_.Exception.Message))" Yellow
        return $false
    }
}

function Find-Python {
    # devuelve la ruta de un python.exe que funcione, de 3.10 a 3.13 y probando primero el mas nuevo;
    # si solo hay uno mas nuevo (3.14+) lo devuelve con aviso: pip dira si aun no hay librerias para el
    $probe = "import sys; print(sys.executable); print(sys.version_info[0] * 100 + sys.version_info[1])"
    $tries = @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"), @("py", "-3"), @("python"), @("python3"))
    foreach ($dir in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python*", "${env:ProgramFiles(x86)}\Python*")) {
        Get-ChildItem -Path $dir -Filter python.exe -Recurse -Depth 1 -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | ForEach-Object { $tries += , @($_.FullName) }
    }
    $newer = $null
    foreach ($t in $tries) {
        $exe = $t[0]
        $rest = @($t | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try { $out = & $exe @rest -c $probe 2>$null } catch { continue }
        if ($LASTEXITCODE -ne 0 -or $out.Count -lt 2 -or $out[0] -like "*WindowsApps*") { continue }
        $v = [int]$out[1]
        if ($v -ge 310 -and $v -le $MaxPy) { return $out[0].Trim() }
        if ($v -gt $MaxPy -and -not $newer) { $newer = $out[0].Trim() }
    }
    if ($newer) {
        Say "    Solo hay un Python más nuevo que 3.$($MaxPy - 300) ($newer): si fallan las librerías, instala Python 3.13." Yellow
        return $newer
    }
    return $null
}

function Get-ApiHeaders($post) {
    # X-Audios en los POST y, si la app ha escrito su token de sesion (%LOCALAPPDATA%\AudiosApp\sesion.token, versiones
    # con la API protegida), X-Chuleta-Token. Sin el fichero, como siempre. El token no se escribe en ningun registro
    $h = @{}
    if ($post) { $h["X-Audios"] = "1" }
    try {
        $tf = Join-Path $env:LOCALAPPDATA "AudiosApp\sesion.token"
        if (Test-Path -LiteralPath $tf -PathType Leaf) {
            $tok = [IO.File]::ReadAllText($tf).Trim()
            if ($tok) { $h["X-Chuleta-Token"] = $tok }
        }
    } catch {}
    return $h
}

function Invoke-Api($path, $post = $false, $timeout = 2) {
    # @{ code; body }: 200 si contesta bien, el codigo HTTP si contesta con error (409 grabando, 401/403 sin token
    # valido...) y 0 si no contesta (apagada)
    $a = @{ Uri = "http://127.0.0.1:$Port$path"; TimeoutSec = $timeout; Headers = (Get-ApiHeaders $post) }
    if ($post) { $a.Method = "Post"; $a.ContentType = "application/json"; $a.Body = "{}" }
    try { return @{ code = 200; body = (Invoke-RestMethod @a) } }
    catch {
        $c = 0
        try { if ($_.Exception.Response) { $c = [int]$_.Exception.Response.StatusCode } } catch {}
        return @{ code = $c; body = $null }
    }
}

function Test-Grabadora {
    # la grabadora de la app (recorder.py) en marcha: con ella viva no se cierra nada a la fuerza (puede estar grabando)
    try {
        return (@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.Name -match '^pythonw?\.exe$' -and "$($_.CommandLine)" -match 'recorder\.py' }).Count -gt 0)
    } catch { return $false }
}

function Unblock-AppFiles($appDir, $rootDir) {
    # quita la marca de Internet (Zone.Identifier) solo a los ficheros del paquete: _app y los de la raiz que trae el
    # zip. Sin esto Windows pregunta al abrirlos; las reuniones y demas ficheros del usuario conservan su marca
    Get-ChildItem -LiteralPath $appDir -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
    foreach ($n in @("INSTALAR.bat", $Launcher, "LEEME.txt", "LICENSE")) {
        $f = Join-Path $rootDir $n
        if (Test-Path -LiteralPath $f -PathType Leaf) { Unblock-File -LiteralPath $f -ErrorAction SilentlyContinue }
    }
}

function Describe-PipFailure($log, $pyVer, $pipCfg) {
    # el error de las librerias principales con el paquete que fallo y la causa con lo que se sabe: la red o el proxy
    # (pip tambien dice "no encuentra" cuando no llega al repositorio), un Python sin paquetes publicados, un pip
    # "only-binary" o el repositorio de la empresa. De una URL solo se ensena el servidor (puede llevar credenciales)
    $names = @()
    foreach ($m in [regex]::Matches("$log", '(?:No matching distribution found for|Could not find a version that satisfies the requirement)\s+([A-Za-z0-9][A-Za-z0-9._\-]*)')) {
        if ($names -notcontains $m.Groups[1].Value) { $names += $m.Groups[1].Value }
    }
    $lista = $(if ($names.Count) { $names -join ", " } else { "Alguna librería" })
    $cuales = $(if ($names.Count) { " (" + ($names -join ", ") + ")" } else { "" })
    $verNum = 0
    if ("$pyVer" -match '^(\d+)\.(\d+)') { $verNum = [int]$Matches[1] * 100 + [int]$Matches[2] }
    if ("$log" -match "ProxyError|SSLError|CERTIFICATE_VERIFY|Could not fetch URL|Temporary failure|timed out|Max retries exceeded|Network is unreachable|Connection (refused|reset|aborted)") {
        return ("No se han podido descargar las librerías" + $cuales + ": parece la red o el proxy de la empresa. Prueba " +
                "con otra red (p. ej. compartiendo datos del móvil) o pide a IT acceso a pypi.org, y vuelve a abrir INSTALAR.bat.")
    }
    if ($verNum -gt $MaxPy -and ($names.Count -or "$log" -match "is not a supported wheel")) {
        return ("$lista todavía no está publicada para Python $pyVer. Instala Python 3.13 (winget install -e --id " +
                "Python.Python.3.13 --scope user, o desde python.org) y vuelve a abrir INSTALAR.bat.")
    }
    if ($names.Count -and "$pipCfg" -match 'only[-_]binary') {
        return ("No se ha podido instalar $lista`: la configuración de pip de este PC solo acepta paquetes ya compilados " +
                "(only-binary) y no los hay para Python $pyVer. Pide a IT que lo permita para esos paquetes y vuelve a abrir INSTALAR.bat.")
    }
    $idx = [regex]::Match("$pipCfg", "index-url\s*=\s*'?([^'\s]+)")
    if ($names.Count -and $idx.Success) {
        $hostName = "otro servidor"
        try { $hostName = ([uri]$idx.Groups[1].Value).Host } catch {}
        return ("No se ha podido instalar $lista`: el repositorio de paquetes configurado en este PC ($hostName) no lo tiene " +
                "para Python $pyVer. Pide a IT que lo añada, o prueba con otra red, y vuelve a abrir INSTALAR.bat.")
    }
    if ($names.Count) {
        return ("pip no encuentra $lista para Python $pyVer. Revisa los mensajes de arriba (en la red de la empresa puede " +
                "ser el proxy o un repositorio de paquetes propio) y vuelve a abrir INSTALAR.bat.")
    }
    return ("No se han podido instalar las librerías (Python $pyVer). Revisa los mensajes de arriba: en la red de la " +
            "empresa puede ser el proxy; con un Python muy nuevo, instala Python 3.13 y vuelve a abrir INSTALAR.bat.")
}

function Same-Path($a, $b) {
    # misma carpeta (sin distinguir mayusculas ni la barra final)
    if (-not $a -or -not $b) { return $false }
    try { return ([IO.Path]::GetFullPath("$a").TrimEnd('\') -ieq [IO.Path]::GetFullPath("$b").TrimEnd('\')) } catch { return $false }
}

function Test-Instalacion($dir) {
    # una carpeta de la app instalada (o descomprimida): <dir>\_app\server.py
    return [bool]($dir -and (Test-Path -LiteralPath (Join-Path $dir "_app\server.py")))
}

function Get-LnkInfo($lnkPath) {
    # a que apunta un acceso directo: @{ app = <carpeta _app> o ""; deLaApp = $true si es de la app (abre server.py, o un
    # lanzador .bat de la app) }. La carpeta: WorkingDirectory (pythonw.exe desde el 31/10/2026) o la del destino (los
    # antiguos apuntaban a _app\Notas.bat); vale si tiene server.py
    $r = @{ app = ""; deLaApp = $false }
    try { $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath) } catch { return $r }
    $wd, $tp, $ar = "$($sc.WorkingDirectory)", "$($sc.TargetPath)", "$($sc.Arguments)"
    $bats = @("Tenca.bat") + @($LegacyNames | ForEach-Object { "$_.bat" })
    $r.deLaApp = ($ar -match 'server\.py') -or ($tp -and ($bats -contains (Split-Path -Leaf $tp)))
    # tambien la carpeta del server.py de los argumentos: uno sin WorkingDirectory no se tomara por roto
    $deArgs = $(if ($ar -match '"?([^"]*server\.py)"?') { Split-Path -Parent $Matches[1] } else { "" })
    foreach ($d in @($wd, $(if ($tp) { Split-Path -Parent $tp } else { "" }), $deArgs)) {
        if ($d -and (Test-Path -LiteralPath (Join-Path $d "server.py"))) { $r.app = $d; break }
    }
    return $r
}

function Get-LnkAntiguos($dir, $appDir, $nombres = $LegacyNames) {
    # los accesos directos con un nombre anterior de la app en $dir que sobran: los que apuntan a $appDir, que
    # se migran, y los de la app que ya no apuntan a nada (una carpeta borrada). Cada uno: @{ path; name; tipo = "esta" o
    # "rota" }. Uno que apunta a otra copia de la app (otra carpeta con server.py) se queda y se dice; uno que no es de
    # la app (un documento que se llame igual) no se toca
    $out = @()
    foreach ($n in $nombres) {
        $p = Join-Path $dir "$n.lnk"
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { continue }
        $i = Get-LnkInfo $p
        if ($i.app -and (Same-Path $i.app $appDir)) { $out += @{ path = $p; name = $n; tipo = "esta" } }
        elseif ($i.app) { Say "    Se conserva el acceso directo '$n' en $dir : es de otra copia ($($i.app))." Yellow }
        elseif ($i.deLaApp) { $out += @{ path = $p; name = $n; tipo = "rota" } }
        else { Log-File "    se conserva $p (no es de la app)" }
    }
    return $out
}

function Remove-LnkAntiguos($lista, $dir) {
    # quita los accesos directos que sobran (Get-LnkAntiguos) y lo dice; si no se puede, se avisa y se sigue
    foreach ($x in @($lista)) {
        if (-not $x) { continue }
        try {
            Remove-Item -LiteralPath $x.path -Force -ErrorAction Stop
            if ($x.tipo -eq "rota") { Say "    Quitado el acceso directo '$($x.name)' en $dir : ya no abría nada." }
            else { Say "    Quitado el acceso directo '$($x.name)' en $dir : ahora es '$AppName'." }
        } catch { Say "    No se pudo quitar el acceso directo '$($x.name)' en $dir ($($_.Exception.Message)): bórralo a mano." Yellow }
    }
}

function Find-Instalacion {
    # la instalacion que ya hay en este PC: @{ root; app; how; antes } o $null (antes: el nombre anterior con el que se
    # ha encontrado, o ""). Por este orden:
    #   a) la entrada de "Aplicaciones instaladas" (InstallLocation), la de ahora o la del nombre anterior, si sigue
    #      teniendo _app\server.py
    #   b) el acceso directo de la app ("Tenca.lnk") o el de un nombre anterior ("Chuleta.lnk", "Notas.lnk"), en el
    #      escritorio y, si alli no hay ninguno, en el menu Inicio
    #   c) la carpeta propia: %USERPROFILE%\Tenca o la del nombre anterior (%USERPROFILE%\Chuleta)
    # a) y c) solo sin -NoShortcut ni -Desktop ($Sistema); b) tambien con -Desktop (su escritorio)
    if ($Sistema) {
        foreach ($k in @(@($UninstallKey) + $LegacyUninstallKeys)) {
            try {
                $loc = "$((Get-ItemProperty -LiteralPath $k -ErrorAction Stop).InstallLocation)".Trim()
                $antes = $(if ($k -eq $UninstallKey) { "" } else { Split-Path -Leaf $k })
                if (Test-Instalacion $loc) { return @{ root = $loc; app = (Join-Path $loc "_app"); how = "Aplicaciones instaladas"; antes = $antes } }
            } catch {}
        }
    }
    try {
        $dirs = @($(if ($Desktop) { $Desktop } else { [Environment]::GetFolderPath("Desktop") }))
        if (-not $Desktop) { $dirs += [Environment]::GetFolderPath("Programs") }
        foreach ($dir in $dirs) {
            foreach ($n in @(@($AppName) + $LegacyNames)) {
                $lnkPath = Join-Path $dir "$n.lnk"
                if (-not (Test-Path -LiteralPath $lnkPath)) { continue }
                # el acceso directo apunta a pythonw.exe (desde el 31/10/2026) o a Notas.bat: la carpeta de la app es
                # WorkingDirectory en los dos casos (y la del destino, en los antiguos)
                $i = Get-LnkInfo $lnkPath
                if ($i.app) {
                    $oldApp = (Resolve-Path -LiteralPath $i.app).Path
                    return @{ root = (Split-Path -Parent $oldApp); app = $oldApp; how = "acceso directo $n.lnk ($dir)"
                              antes = $(if ($n -eq $AppName) { "" } else { $n }) }
                }
            }
        }
    } catch {}
    if ($Sistema) {
        foreach ($f in @(@($FolderName) + $LegacyFolders)) {
            $own = Join-Path $script:UserHome $f
            if (Test-Instalacion $own) { return @{ root = $own; app = (Join-Path $own "_app"); how = "carpeta $own"; antes = "" } }
        }
    }
    return $null
}

function Test-CarpetaDeLaApp($dir) {
    # ya es de la app (con _app) o lo fue: el desinstalador deja el LEEME.txt junto a las reuniones, y su primera linea
    # empieza por el nombre de la app (el de ahora o uno anterior: "TENCA · ...", "CHULETA · ...")
    if (Test-Instalacion $dir) { return $true }
    $l1 = "$(Get-Content -LiteralPath (Join-Path $dir 'LEEME.txt') -TotalCount 1 -Encoding UTF8 -ErrorAction SilentlyContinue)".TrimStart([char]0xFEFF)
    foreach ($n in @(@($FolderName) + $LegacyFolders)) { if ($l1 -like "$($n.ToUpper()) *") { return $true } }
    return $false
}

function Test-CarpetaLibre($dir) {
    # vale como carpeta nueva: no existe, esta vacia, ya es de la app (con _app) o lo fue (el desinstalador deja el
    # LEEME.txt junto a las reuniones: al reinstalar se vuelve a la misma carpeta y se ven otra vez)
    if (-not (Test-Path -LiteralPath $dir)) { return $true }
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return $false }
    try { $first = Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop | Select-Object -First 1 }
    catch { return $false }  # no se puede listar: no se sabe que hay dentro, no se usa
    if (-not $first) { return $true }
    return (Test-CarpetaDeLaApp $dir)
}

function Test-TieneReuniones($rootDir, $appDir) {
    # grabaciones fuera de _app: una carpeta en uso, no un zip recien descomprimido (no se dice que se puede borrar)
    $ext = @(".wav", ".m4a", ".mp3", ".mp4", ".flac", ".ogg", ".opus", ".aac", ".wma", ".webm", ".mkv", ".mov",
        ".oga", ".amr", ".3gp", ".3gpp", ".3ga", ".caf", ".qta", ".aif", ".aiff", ".waptt")
    return [bool](Get-ChildItem -LiteralPath $rootDir -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $ext -contains $_.Extension.ToLowerInvariant() -and $_.FullName -notlike "$appDir*" } | Select-Object -First 1)
}

function Get-CarpetaNueva {
    # la carpeta propia que ya fue de la app (desinstalada: quedan alli sus reuniones con el LEEME), con el nombre de
    # ahora o con el anterior (%USERPROFILE%\Chuleta); si no hay, %USERPROFILE%\Tenca o, si esa ya tiene otras cosas,
    # "Tenca (2)", "Tenca (3)"... ($null si ninguna vale)
    foreach ($f in @(@($FolderName) + $LegacyFolders)) {
        $dir = Join-Path $script:UserHome $f
        if ((Test-Path -LiteralPath $dir -PathType Container) -and (Test-CarpetaDeLaApp $dir)) { return $dir }
    }
    for ($i = 1; $i -le 20; $i++) {
        $dir = Join-Path $script:UserHome $(if ($i -eq 1) { $FolderName } else { "$FolderName ($i)" })
        if (Test-CarpetaLibre $dir) { return $dir }
    }
    return $null
}

function Copy-AppFiles($fromApp, $fromRoot, $toApp, $toRoot) {
    # copia la app de la carpeta descomprimida a la de destino (nueva o la instalacion anterior): el codigo de _app,
    # static y export, la version, el manual, la licencia y los .bat de la raiz; las listas de vocabulario que traiga el
    # paquete, sin pisar las que ya haya. Las grabaciones de la carpeta de destino no se tocan.
    New-Item -ItemType Directory -Force $toApp | Out-Null
    Get-ChildItem -LiteralPath $fromApp -File | Where-Object { $_.Extension -in ".py", ".bat", ".ps1" } |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $toApp -Force }
    foreach ($sub in @("static", "export")) {  # export: plantillas del instalador (para "Compartir $AppName")
        New-Item -ItemType Directory -Force (Join-Path $toApp $sub) | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $fromApp $sub) -File -ErrorAction SilentlyContinue |
            ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $toApp $sub) -Force }
    }
    # la version del paquete (la ensena Ajustes > Acerca de), el manual y los .bat de la raiz tambien se renuevan
    if (Test-Path -LiteralPath (Join-Path $fromApp "version.txt")) { Copy-Item -LiteralPath (Join-Path $fromApp "version.txt") -Destination $toApp -Force }
    foreach ($n in @("LEEME.txt", "LICENSE", "INSTALAR.bat", $Launcher)) {
        if (Test-Path -LiteralPath (Join-Path $fromRoot $n)) {
            Copy-Item -LiteralPath (Join-Path $fromRoot $n) -Destination (Join-Path $toRoot $n) -Force
        }
    }
    # listas de vocabulario que traiga el paquete, sin pisar las que ya tengas
    Get-ChildItem -LiteralPath $fromRoot -Directory -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "Vocabulario" -and $_.FullName -notlike "$fromApp*" } | ForEach-Object {
            $dst = Join-Path $toRoot $_.FullName.Substring($fromRoot.Length).TrimStart('\')
            New-Item -ItemType Directory -Force $dst | Out-Null
            Get-ChildItem -LiteralPath $_.FullName -File | ForEach-Object {
                $to = Join-Path $dst $_.Name
                if (-not (Test-Path -LiteralPath $to)) { Copy-Item -LiteralPath $_.FullName -Destination $to }
            }
        }
    Unblock-AppFiles $toApp $toRoot
}

function Read-AppVersion($appDir, $rootDir) {
    # la version instalada: _app/version.txt (la escribe scripts/build_zip.py: fecha del commit · sha) o, si no, el LEEME
    $app = $appDir
    $ver = ""
    try { $ver = "$(Get-Content -LiteralPath (Join-Path $app 'version.txt') -TotalCount 1 -Encoding UTF8 -ErrorAction SilentlyContinue)".Trim() } catch {}
    if (-not $ver) {
        $l1 = "$(Get-Content -LiteralPath (Join-Path $rootDir 'LEEME.txt') -TotalCount 1 -ErrorAction SilentlyContinue)"
        if ($l1 -match '\(versi[^ )]*\s*(?:del\s+)?([^)]+)\)') { $ver = $Matches[1].Trim() }
    }
    return $ver
}

function Register-App($appDir, $rootDir, $version) {
    # entrada de "Aplicaciones instaladas" (Configuracion > Aplicaciones y el Panel de control) para este usuario:
    # HKCU, sin administrador. Nombres de los valores: los de la clave Uninstall de Windows (learn.microsoft.com,
    # "Uninstall Registry Key"); EstimatedSize en KB (ARPSIZE). Si una politica lo impide, la app funciona igual.
    try {
        if (-not $version) { $version = Get-Date -Format "yyyy-MM-dd" }
        $bytes = (Get-ChildItem -LiteralPath $appDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        $kb = [int][Math]::Ceiling([double]$bytes / 1024)
        $cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $appDir "desinstalar.ps1") + '"'
        $values = [ordered]@{
            DisplayName = @($AppName, "String"); DisplayVersion = @($version, "String"); Publisher = @($Publisher, "String")
            DisplayIcon = @((Join-Path $appDir "static\tenca.ico"), "String"); InstallLocation = @($rootDir, "String")
            InstallDate = @((Get-Date -Format "yyyyMMdd"), "String"); EstimatedSize = @($kb, "DWord")
            NoModify = @(1, "DWord"); NoRepair = @(1, "DWord"); URLInfoAbout = @($RepoUrl, "String")
            UninstallString = @($cmd, "String"); QuietUninstallString = @("$cmd -Silencioso", "String")
        }
        # CreateSubKey crea tambien ...\Uninstall si en este perfil aun no existe (un perfil nuevo no lo trae: New-Item
        # fallaba con "The registry key at the specified path does not exist") y abre la clave sin borrar nada
        $rk = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey(($UninstallKey -replace '^HKCU:\\', ''))
        try {
            foreach ($n in $values.Keys) {
                if ($values[$n][1] -eq "DWord") { $rk.SetValue($n, [int]$values[$n][0], [Microsoft.Win32.RegistryValueKind]::DWord) }
                else { $rk.SetValue($n, [string]$values[$n][0], [Microsoft.Win32.RegistryValueKind]::String) }
            }
        } finally { $rk.Close() }
        Say "    $AppName $version, en Aplicaciones instaladas (para quitarla: Configuración > Aplicaciones > $AppName > Desinstalar)" Green
    } catch {
        Say "    No se ha podido añadir a Aplicaciones instaladas ($($_.Exception.Message)): la app funciona igual; para quitarla, _app\desinstalar.ps1." Yellow
        return $false
    }
    Remove-RegistroAntiguo $rootDir
    return $true
}

function Remove-RegistroAntiguo($rootDir) {
    # la entrada de "Aplicaciones instaladas" con el nombre anterior ("Chuleta"), si es de la app (la puso este
    # instalador: Publisher = $Publisher): si es de esta carpeta, o de una que ya no tiene la app, sobra (si no,
    # saldrian dos: la nueva y la vieja). Solo despues de escribir la nueva: si eso falla, la vieja sigue sirviendo para
    # desinstalar. Si es de otra copia que sigue instalada, se queda y se dice; la de otro programa no se toca
    foreach ($k in $LegacyUninstallKeys) {
        if (-not (Test-Path -LiteralPath $k)) { continue }
        $n = Split-Path -Leaf $k
        $loc, $pub = "", ""
        try {
            $props = Get-ItemProperty -LiteralPath $k -ErrorAction Stop
            $loc, $pub = "$($props.InstallLocation)".Trim(), "$($props.Publisher)".Trim()
        } catch {}
        if ($pub -ne $Publisher) { Log-File "    se conserva la entrada $k (Publisher '$pub': no es de la app)"; continue }
        if ($loc -and -not (Same-Path $loc $rootDir) -and (Test-Instalacion $loc)) {
            Say "    Se conserva la entrada '$n' de Aplicaciones instaladas: es de otra copia ($loc)." Yellow
            continue
        }
        # DeleteSubKeyTree: sin preguntar aunque tuviera subclaves (el borrado de PowerShell sin -Recurse preguntaria)
        try {
            [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree(($k -replace '^HKCU:\\', ''), $false)
            Say "    Quitada de Aplicaciones instaladas la entrada antigua '$n' (ahora es '$AppName')."
            if (-not $script:antes -and (Same-Path $loc $rootDir)) { $script:antes = $n }
        } catch { Say "    No se pudo quitar la entrada antigua '$n' de Aplicaciones instaladas ($($_.Exception.Message))." Yellow }
    }
}

function Remove-LanzadoresAntiguos($rootDir) {
    # los .bat de la raiz con un nombre anterior ("ABRIR CHULETA.bat", "ABRIR NOTAS.bat") hacen lo mismo que $Launcher:
    # sobran. Solo si son de la app (pequenos y llaman a _app\): uno del usuario con ese nombre se queda. Los de dentro
    # de _app (_app\Chuleta.bat, _app\Notas.bat) se dejan: no se ven, y un anclaje antiguo puede apuntar a ellos
    if (-not (Test-Path -LiteralPath (Join-Path $rootDir $Launcher) -PathType Leaf)) { return }  # sin el nuevo, no
    foreach ($n in $LegacyLaunchers) {
        $f = Join-Path $rootDir $n
        if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { continue }
        $t = "$(Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue)"
        if ($t.Length -gt 4096 -or $t -notmatch '_app\\') { Say "    Se conserva $f (no es de la app)."; continue }
        try { Remove-Item -LiteralPath $f -Force -ErrorAction Stop; Say "    Quitado $n (ahora es $Launcher)." }
        catch { Say "    No se pudo quitar $f ($($_.Exception.Message))." Yellow }
    }
}

function Copy-ArranqueAprobado($desde, $hacia, $clave = $null) {
    # el Administrador de tareas (Aplicaciones de inicio) guarda si el usuario desactivo un acceso directo de la carpeta
    # Inicio por su nombre de fichero (HKCU ...\Explorer\StartupApproved\StartupFolder, REG_BINARY; sin valor =
    # activado). Al pasar de "$desde" a "$hacia" se copia su valor, para que lo desactivado siga desactivado. El del nombre
    # viejo se deja (sin su fichero no hace nada; si no se pudiera quitar el .lnk viejo, sigue como estaba). Sin valor,
    # nada. $clave: otra clave (los tests)
    if (-not $clave) { $clave = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder" }
    try {
        $v = (Get-ItemProperty -LiteralPath $clave -Name $desde -ErrorAction Stop).$desde
        if ($null -eq $v) { return }
        $null = New-ItemProperty -LiteralPath $clave -Name $hacia -Value ([byte[]]$v) -PropertyType Binary -Force -ErrorAction Stop
        Log-File "    StartupApproved: $desde -> $hacia ($(if ($v[0] -eq 3) { 'desactivado' } else { 'activado' }) en Aplicaciones de inicio)"
    } catch {}
}

function Update-Arranque($progLnk, $appDir, $startup = $null) {
    # "Arrancar con Windows" (icono de la bandeja) = una copia del acceso directo del menu Inicio en la carpeta Inicio
    # de Windows (server.inicio_windows_poner). Si estaba puesto, con el nombre de ahora o con uno anterior, queda UNA
    # copia, "$AppName.lnk", igual que la de Inicio (no dos: la app se abriria dos veces al entrar); las del nombre
    # anterior se quitan. Si no estaba puesto, no se pone. $startup: la carpeta Inicio de Windows (los tests dan otra)
    if (-not $startup) { $startup = [Environment]::GetFolderPath("Startup") }
    if (-not $startup -or -not (Test-Path -LiteralPath $startup -PathType Container)) { return }
    $nuevo = Join-Path $startup "$AppName.lnk"
    $antiguos = @(Get-LnkAntiguos $startup $appDir $LegacyArranque)
    $activo = (Test-Path -LiteralPath $nuevo) -or @($antiguos | Where-Object { $_.tipo -eq "esta" }).Count -gt 0
    if ($activo) {
        if (-not (Test-Path -LiteralPath $progLnk)) {
            Say "    Arrancar con Windows: sin el acceso directo del menú Inicio no se puede poner al día (se deja como estaba)." Yellow
            return
        }
        $habia = Test-Path -LiteralPath $nuevo
        try { Copy-Item -LiteralPath $progLnk -Destination $nuevo -Force -ErrorAction Stop }
        catch {
            Say "    Arrancar con Windows: no se pudo poner al día $nuevo ($($_.Exception.Message)); se deja como estaba." Yellow
            return
        }
        # si estaba desactivado en el Administrador de tareas con el nombre anterior, sigue desactivado
        $previo = @($antiguos | Where-Object { $_.tipo -eq "esta" }) | Select-Object -First 1
        if ($previo -and -not $habia) { Copy-ArranqueAprobado "$($previo.name).lnk" "$AppName.lnk" }
        Say "    Arrancar con Windows: sigue activado ($nuevo)." Green
    }
    Remove-LnkAntiguos $antiguos $startup
}

function Report-Anclajes($appDir) {
    # anclada a la barra de tareas con un nombre anterior: Windows no deja que un programa ancle, desancle ni renombre
    # un anclaje (es del usuario). Sigue funcionando (mismo AppUserModelID: la ventana se agrupa en el); solo se avisa
    # de como dejarla con el nombre nuevo. Devuelve los nombres encontrados
    $out = @()
    if (-not $env:APPDATA) { return $out }
    $dir = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
    foreach ($n in $LegacyNames) {
        $p = Join-Path $dir "$n.lnk"
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { continue }
        $i = Get-LnkInfo $p
        if ($i.app -and (Same-Path $i.app $appDir)) {
            Say ("    En la barra de tareas sigue anclada como '$n' (Windows no deja que un instalador cambie un anclaje). " +
                 "Para verla como '$AppName': clic derecho en ese icono > Desanclar de la barra de tareas; después, con " +
                 "$AppName abierta, clic derecho en su icono > Anclar a la barra de tareas.") Yellow
            $out += $n
        }
    }
    return $out
}

function Set-AccesoDirecto($dir, $appDir, $pyw, $sinConsola) {
    # "$AppName.lnk" en $dir (escritorio o menu Inicio), apuntando a pythonw.exe server.py de $appDir (o a Tenca.bat, si
    # no hay pythonw.exe), con su icono y su AppUserModelID. Los del nombre anterior ("Chuleta.lnk", "Notas.lnk") de esta
    # instalacion: si aun no hay "$AppName.lnk", el primero pasa a llamarse asi (se renombra: conserva su sitio en el
    # escritorio) y se actualiza como los demas; los que sobren se quitan despues de crear el nuevo: nunca quedan dos
    try {
        $lnkPath = Join-Path $dir "$AppName.lnk"
        $antiguos = @(Get-LnkAntiguos $dir $appDir)
        if (-not (Test-Path -LiteralPath $lnkPath)) {
            $primero = @($antiguos | Where-Object { $_.tipo -eq "esta" }) | Select-Object -First 1
            if ($primero) {
                try {
                    Rename-Item -LiteralPath $primero.path -NewName "$AppName.lnk" -ErrorAction Stop
                    Say "    El acceso directo '$($primero.name)' pasa a llamarse '$AppName' ($dir)."
                    $antiguos = @($antiguos | Where-Object { $_.path -ne $primero.path })
                    if (-not $script:antes) { $script:antes = $primero.name }
                } catch { Say "    No se pudo renombrar '$($primero.name).lnk' en $dir ($($_.Exception.Message)): se crea '$AppName' y se quita el antiguo." Yellow }
            }
        }
        $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath)
        if ($sinConsola) {
            $lnk.TargetPath = $pyw
            $lnk.Arguments = '"' + (Join-Path $appDir "server.py") + '"'
            $lnk.WindowStyle = 1
        } else {
            $lnk.TargetPath = Join-Path $appDir "Tenca.bat"
            $lnk.Arguments = ""
            $lnk.WindowStyle = 7  # la ventana de consola, minimizada
        }
        $lnk.WorkingDirectory = $appDir
        $lnk.IconLocation = (Join-Path $appDir "static\tenca.ico") + ",0"
        $lnk.Description = "$AppName`: grabar, transcribir y resumir reuniones"
        $lnk.Save()
        $null = Set-ShortcutAppId $lnkPath $AppId
        Say "    $lnkPath" Green
        # solo uno de esta instalacion dice que era la app con otro nombre (uno roto es de una copia que ya no esta)
        $deEsta = @($antiguos | Where-Object { $_.tipo -eq "esta" })
        if ($deEsta.Count -and -not $script:antes) { $script:antes = $deEsta[0].name }
        Remove-LnkAntiguos $antiguos $dir
    } catch {
        Say "    No se pudo crear en $dir (usa $Launcher)" Yellow
    }
}

$script:UserHome = $(if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath("UserProfile") })
$script:antes = ""  # el nombre anterior de la app si esta instalacion lo llevaba (se dice al final)
$script:anclados = @()  # anclajes de la barra de tareas con un nombre anterior (Report-Anclajes)
$Gui = $false
if (-not ($Consola -or $NoShortcut -or $NoLaunch) -and [Environment]::UserInteractive) { $Gui = New-InstallerWindow }
Log-File ("modo: " + $(if ($Gui) { "ventana" } else { "consola" }) + " · AutoCerrar=$AutoCerrar · PowerShell $($PSVersionTable.PSVersion)")

Write-Host ""
Write-Host "  ==========================================================" -ForegroundColor DarkCyan
Write-Host "    $($AppName.ToUpper())  ·  grabar, transcribir y resumir reuniones" -ForegroundColor White
Write-Host "  ==========================================================" -ForegroundColor DarkCyan
Say "    Carpeta: $root"

# el zip descargado de Teams/correo trae la marca de Internet: sin quitarla Windows pregunta cada vez al abrir sus
# ficheros. Solo los del paquete: lo demas de la carpeta (las reuniones) conserva la suya
Unblock-AppFiles $app $root

# --- si la app esta abierta se cierra: si no, al terminar se volveria a ver la version vieja (sigue en marcha)
Step "Comprobando si $AppName está abierta..."
$running = Invoke-Api "/api/ping" $false 5
# 401/403: contesta una version con la API protegida y no he podido usar su token: esta abierta igual. Sin respuesta a
# tiempo pero con un server.py de la app escuchando en el puerto: esta abierta y ocupada
$ocupada = $false
if ($running.code -eq 0) {
    try {
        $lpid = (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess
        $lp = Get-CimInstance Win32_Process -Filter "ProcessId=$lpid" -ErrorAction Stop
        $ocupada = [bool]($lp -and $lp.Name -match '^pythonw?\.exe$' -and "$($lp.CommandLine)" -match 'server\.py')
    } catch {}
}
if (($running.code -eq 200 -and $running.body.app -eq "audios") -or $running.code -eq 401 -or $running.code -eq 403 -or $ocupada) {
    $st = Invoke-Api "/api/rec/status" $false 2
    if ($st.code -eq 200 -and $st.body.recording) { Fail "$AppName está grabando ahora mismo. Para la grabación y vuelve a abrir INSTALAR.bat." }
    # primero por las buenas: POST /api/apagar (versiones desde el 31/10/2026; 409 = grabando); si no, como antes
    $apagado = $false
    $ap = Invoke-Api "/api/apagar" $true 20
    if ($ap.code -eq 409) { Fail "$AppName está grabando ahora mismo. Para la grabación y vuelve a abrir INSTALAR.bat." }
    if ($ap.code -eq 200) {
        for ($k = 0; $k -lt 24 -and -not $apagado; $k++) {
            Start-Sleep -Milliseconds 250
            if ((Invoke-Api "/api/ping" $false 1).code -eq 0) { $apagado = $true }
        }
    }
    $owner = $null
    if ($apagado) { $owner = -1 }
    try { $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess } catch {}
    if (-not $owner) {
        $m = netstat -ano -p tcp | Select-String ":$Port\s+\S+\s+LISTENING\s+(\d+)" | Select-Object -First 1
        if ($m) { $owner = [int]$m.Matches[0].Groups[1].Value }
    }
    if (-not $owner) { Fail "$AppName está abierta y no he podido cerrarla: ciérrala (Ajustes > Apagar, o su ventana de consola) y vuelve a abrir INSTALAR.bat." }
    if ($owner -gt 0) {
        # sin la API (version antigua, sin token valido o sin respuesta) se cierra su proceso; nunca con la grabadora
        # abierta, que puede estar grabando una reunion
        if (Test-Grabadora) { Fail "$AppName no se ha podido apagar y su grabadora sigue abierta (puede estar grabando): apágala desde Ajustes > Apagar $AppName y vuelve a abrir INSTALAR.bat." }
        $ids = @($owner)
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
        for ($k = 0; $k -lt 2 -and $p; $k++) {  # tambien el "py" y la ventana de consola que lo lanzaron (instalaciones antiguas)
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
            if ($p -and ($p.Name -eq "py.exe" -or ($p.Name -eq "cmd.exe" -and @(@("Tenca") + $LegacyNames | Where-Object { "$($p.CommandLine)" -like "*$_.bat*" }).Count -gt 0))) { $ids += $p.ProcessId }
        }
        $ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    Say "    Cerrada la versión que estaba abierta (al terminar se abre la nueva)" Green
} else {
    Say "    No estaba abierta"
}

# --- donde queda la app: si ya estaba instalada en otra carpeta (p. ej. el zip nuevo se ha descomprimido en Descargas),
# se actualiza aquella: ahi estan sus grabaciones, y el acceso directo ya apunta a ella. Si no, se copia a su carpeta
# propia (%USERPROFILE%\Tenca, o -Destino) y la descomprimida deja de hacer falta. Con un nombre anterior de la app
# (%USERPROFILE%\Chuleta), igual: la carpeta y lo que hay en ella no se mueven ni se renombran.
$origen = $null  # la carpeta descomprimida, si la app ha quedado en otra
$prev = $null
# python.txt lo escribe este instalador: si esta carpeta ya se instalo (quiza con sus grabaciones), se actualiza ella
$yaInstalada = Test-Path -LiteralPath (Join-Path $app "python.txt")
if (-not $yaInstalada -and (-not $NoShortcut -or $Desktop)) { $prev = Find-Instalacion }
if ($yaInstalada) {
    Say "    Esta carpeta ya estaba instalada: se actualiza en su sitio"
} elseif ($prev -and -not (Same-Path $prev.root $root)) {
    $old, $oldRoot = $prev.app, $prev.root
    if ($Destino -and -not (Same-Path $Destino $oldRoot)) { Say "    -Destino no se usa: ya está instalada en $oldRoot (para cambiarla de carpeta, desinstálala antes; tus reuniones no se borran)." Yellow }
    if ($prev.antes) {
        $script:antes = $prev.antes
        Step "Ya tenías $($prev.antes) (ahora se llama $AppName) en $oldRoot : actualizo esa (con tus grabaciones)..."
    } else { Step "Ya tenías $AppName en $oldRoot : actualizo esa (con tus grabaciones)..." }
    Say "    Encontrada por: $($prev.how)"
    Copy-AppFiles $app $root $old $oldRoot
    if (Test-TieneReuniones $root $app) { Say "    Actualizada. Ojo: $root tiene grabaciones; no la borres sin revisarla." Yellow }
    else { Say "    Actualizada. La carpeta que acabas de descomprimir ya no hace falta: puedes borrarla." Green }
    $origen = $root
    $app, $root = $old, $oldRoot
} elseif ($prev) {
    if ($prev.antes) { $script:antes = $prev.antes }
    Say "    Instalada en esta misma carpeta (encontrada por: $($prev.how)): se actualiza en su sitio"
} else {
    $dest = $null
    if ($Destino) {
        $dest = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destino)
        if (-not (Same-Path $dest $root) -and -not (Test-CarpetaLibre $dest)) {
            Fail "La carpeta $dest ya tiene otras cosas: elige con -Destino una carpeta nueva o vacía."
        }
    } elseif ($Sistema) {
        $dest = Get-CarpetaNueva
        if (-not $dest) { Say "    No encuentro una carpeta libre en $script:UserHome : se instala aquí ($root)." Yellow }
    }
    if ($dest -and -not (Same-Path $dest $root)) {
        Step "Instalando en $dest..."
        Copy-AppFiles $app $root (Join-Path $dest "_app") $dest
        Say "    Copiada desde $root" Green
        $origen = $root
        $app, $root = (Join-Path $dest "_app"), $dest
    }
}
# los "ABRIR ... .bat" de la raiz con un nombre anterior sobran (hacen lo mismo que el nuevo)
Remove-LanzadoresAntiguos $root

Step "Buscando Python (de 3.10 a 3.13)..."
$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "    No hay Python: lo instalo con winget (solo para tu usuario, unos 2 minutos)..."
        & winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements 2>&1 |
            ForEach-Object { Say "    $_" }
        $py = Find-Python
    }
    if (-not $py) {
        Start-Process "https://www.python.org/downloads/windows/"
        Fail ("No he podido instalar Python automáticamente. Instala la última versión desde la página que se acaba de abrir " +
              "(marca 'Add python.exe to PATH') y vuelve a abrir INSTALAR.bat.")
    }
}
Say "    $py" Green

Step "Instalando librerías (la primera vez tarda unos minutos)..."
& $py -m pip --version *> $null
if ($LASTEXITCODE -ne 0) { & $py -m ensurepip --upgrade 2>&1 | ForEach-Object { Say "    $_" } }
function Pip-Install($extra, $list) {
    # pip escribe los errores por stderr: se ensenan y se guardan para distinguir despues que ha fallado
    $script:pipOut = @()
    if ($env:NOTAS_INSTALADOR_SIMULAR_FALLO -eq "pip") {  # solo CI: la pantalla de error, sin tocar la red
        $script:pipOut = @("ERROR: ProxyError: fallo simulado (NOTAS_INSTALADOR_SIMULAR_FALLO=pip)")
        Say "    $($script:pipOut[0])" Yellow
        return 1
    }
    if (-not $extra) { $extra = @() }
    if (-not $list) { $list = $packages }
    & $py -m pip install @extra --disable-pip-version-check --no-warn-script-location @list 2>&1 |
        ForEach-Object { $s = "$_"; Say "    $s"; $script:pipOut += $s }
    return $LASTEXITCODE
}
$userMode = $null
$rc = Pip-Install
if ($rc -ne 0) {
    Say "    Reintentando en la carpeta de tu usuario (--user)..." Yellow
    $userMode = "--user"
    $rc = Pip-Install $userMode
}
$v = & $py -c "import faster_whisper, soundcard, requests, av, numpy; print('    faster-whisper', faster_whisper.__version__, '| PyAV', av.__version__)" 2>&1
$coreOk = ($LASTEXITCODE -eq 0)
if ($env:NOTAS_INSTALADOR_SIMULAR_FALLO -eq "pip") { $coreOk = $false }  # solo CI
if ($coreOk) { Say ($v -join "`n") Green } else { Say ($v -join "`n") Yellow }
# las opcionales (ventana propia, quien habla, calendario) se instalan aparte para que un fallo suyo no deje sin
# librerias al resto, y si no importan solo se avisa: la app sigue funcionando sin esa opcion
if ($coreOk) {
    Step "Instalando las librerías opcionales (ventana propia, quién habla, calendario de Outlook)..."
    foreach ($opt in $optional) { $null = Pip-Install $userMode @($opt) }
}
$v = & $py -c "import sherpa_onnx; print('    sherpa-onnx', sherpa_onnx.__version__, '(quien habla: disponible)')" 2>$null
if ($LASTEXITCODE -eq 0) { Say ($v -join "`n") } else { Say "    sherpa-onnx no está disponible: la opción Voces (quién habla) no se podrá activar en este PC" Yellow }
$v = & $py -c "import win32com.client; print('    pywin32 (calendario de Outlook: disponible)')" 2>$null
if ($LASTEXITCODE -eq 0) { Say ($v -join "`n") } else { Say "    pywin32 no está disponible: no se podrá leer el calendario de Outlook (el resto funciona)" Yellow }
# la ventana propia tampoco bloquea: sin pywebview (o sin WebView2) la app se abre en Edge como siempre
$v = & $py -c "import webview; from importlib.metadata import version; print('    pywebview', version('pywebview'), '(ventana propia: disponible)')" 2>$null
if ($LASTEXITCODE -eq 0) { Say ($v -join "`n") } else { Say "    pywebview no está disponible: la app se abrirá en una ventana de Edge" Yellow }
if (-not $coreOk) {
    $log = ($script:pipOut -join "`n")
    $pyVer = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
    # la configuracion de pip (only-binary, repositorio propio) explica muchos "no encuentra": solo se mira, no se ensena
    $pipCfg = ((& $py -m pip config list 2>$null) -join "`n")
    if ($env:PIP_ONLY_BINARY) { $pipCfg += "`nenv.only-binary='$env:PIP_ONLY_BINARY'" }
    if ($env:PIP_INDEX_URL) { $pipCfg += "`nenv.index-url='$env:PIP_INDEX_URL'" }
    Fail (Describe-PipFailure $log $pyVer $pipCfg)
}
# el lanzador usa este Python (si hay varios instalados, el de las librerias)
[IO.File]::WriteAllText((Join-Path $app "python.txt"), $py, (New-Object Text.UTF8Encoding $false))
# pythonw.exe (junto a python.exe): el mismo interprete sin ventana de consola; el log completo queda en server.log
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
$sinConsola = Test-Path -LiteralPath $pyw
if (-not $sinConsola) { Say "    No hay pythonw.exe junto a $py : el acceso directo abrirá una ventana de consola (Tenca.bat)" Yellow }

if (-not $NoShortcut) {
    Step "Creando el acceso directo '$AppName'..."
    $places = if ($Desktop) { @($Desktop) } else { @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs")) }  # escritorio y menu Inicio
    foreach ($dir in $places) { Set-AccesoDirecto $dir $app $pyw $sinConsola }
    if (-not $Desktop) {
        # "Arrancar con Windows" (carpeta Inicio de Windows) y los anclajes de la barra de tareas: solo los de verdad
        Update-Arranque (Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk") $app
        $script:anclados = @(Report-Anclajes $app)
    }
}

$verRaw = Read-AppVersion $app $root
if ($Sistema) {
    Step "Añadiendo $AppName a Aplicaciones instaladas..."
    $null = Register-App $app $root $verRaw
}

Write-Host ""
$ver = $verRaw
$ver = if ($ver) { " (versión $ver)" } else { "" }
Say "  Listo$ver." Green
Say "  Instalada en $root" Green
# instalada encima de la app con su nombre anterior: se dice que es la misma y que no se ha perdido nada
$renombrada = $(if ($script:antes) { "$($script:antes) ahora se llama ${AppName}: tus reuniones, voces y ajustes siguen donde estaban." } else { "" })
if ($renombrada) { Say "  $renombrada" Green }
$borrable = $origen -and -not (Test-TieneReuniones $origen (Join-Path $origen "_app"))
if ($borrable) { Say "  La carpeta que descomprimiste ($origen) ya no hace falta: puedes borrarla." Green }
Say "  Si es la primera vez: Ajustes (rueda dentada) > Dónde se procesan tus reuniones: solo en este PC, con tu cuenta de Claude o con una API." Green
Say "  Para apagar $AppName del todo: Ajustes > Apagar $AppName (cerrar la ventana la deja en segundo plano)." Green
Say "  Instrucciones completas en LEEME.txt" Green
$abrir = -not $NoLaunch
if ($Gui) {
    $borrable = if ($borrable) { " La carpeta descomprimida ya se puede borrar." } else { "" }
    $siguiente = $(if ($renombrada) { " $renombrada" } else { " Si es la primera vez: Ajustes > Dónde se procesan tus reuniones." })
    $anclaje = $(if ($script:anclados.Count) { " En la barra de tareas, desancla '$($script:anclados[0])' y ancla $AppName (cómo, en el detalle)." } else { "" })
    # la etiqueta tiene sitio para unas cuatro lineas: quien ya la tenia (renombrada) sabe apagarla
    $apagar = $(if ($renombrada) { "" } else { " Para apagar $AppName del todo: Ajustes > Apagar $AppName." })
    Finish-Ui $true "Listo$ver. Instalada en $root.$borrable$siguiente$anclaje$apagar"
    $abrir = $script:ui.abrir
}
if ($abrir) {
    if ($sinConsola) { Start-Process -FilePath $pyw -ArgumentList "server.py" -WorkingDirectory $app }
    else { Start-Process -FilePath (Join-Path $app "Tenca.bat") -WorkingDirectory $app -WindowStyle Minimized }
}
if (-not $Gui -and -not ($NoShortcut -or $NoLaunch) -and [Environment]::UserInteractive) {
    Write-Host ""
    Write-Host "  Pulsa una tecla para cerrar esta ventana." -ForegroundColor DarkGray
    try { $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") } catch {}
}
exit 0
