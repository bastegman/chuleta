# Instalador de Notas (lo lanza INSTALAR.bat). No necesita permisos de administrador:
#   1. Python de 3.10 a 3.13 (si no hay, instala 3.13 con winget solo para este usuario)
#   2. Librerias: faster-whisper (Whisper local + PyAV para leer audio), soundcard (grabadora), requests,
#      pywebview (la ventana de la app sobre WebView2; sin ella se abre en Edge),
#      sherpa-onnx (quien habla) y pywin32 (calendario de Outlook), opcionales: si no cargan, la app funciona igual
#   3. Accesos directos "Notas" en el escritorio y en el menu Inicio: apuntan a pythonw.exe (sin ventana de consola)
#      y llevan el AppUserModelID de la app, para que la barra de tareas agrupe y ancle la ventana como algo propio
# Se puede repetir sin problema. Para probarlo sin tocar nada: -NoShortcut / -NoLaunch, y -Port / -Desktop
# (otro puerto y otra carpeta de escritorio, como si fuera otro PC). -AppName cambia el nombre visible (el de
# common.APP_NAME); las carpetas y los .bat no cambian.
# Con escritorio abre su propia ventana (WinForms: pasos, barra y detalle) y esconde la consola; -Consola, -NoShortcut o
# -NoLaunch (pruebas, CI) lo dejan como consola de siempre.
param([switch]$NoShortcut, [switch]$NoLaunch, [int]$Port = 8765, [string]$Desktop = "", [string]$AppName = "Notas",
      [switch]$Consola)

$ErrorActionPreference = "Continue"
trap {
    # error inesperado (no un Fail): en modo ventana se ensena ahi, porque la consola esta escondida; en consola se propaga
    if ($script:ui -and -not $script:ui.done) { Finish-Ui $false "Error inesperado: $($_.Exception.Message)"; exit 0 }
    break
}
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $app
$packages = @("faster-whisper==1.2.1", "soundcard>=0.4.3", "requests", "truststore", "pywebview==6.2.1")
# opcionales: se instalan una a una y aparte del resto; si alguna falla, solo se avisa (la app funciona sin ella)
$optional = @("sherpa-onnx==2.0.1", "pywin32==312")  # quien habla / calendario de Outlook
$MaxPy = 313  # el Python mas nuevo con faster-whisper/ctranslate2 publicados (3.14 aun no tiene wheels)
$AppId = "Bernardo.Notas"  # = common.APP_ID: la ventana lo fija en el proceso y el acceso directo lo lleva en el .lnk

# --- presentacion: consola y, si hay ventana ($script:ui), tambien la ventana. La logica de instalacion no depende de esto.
$script:ui = $null
function Ui-Log($text) {
    if (-not $script:ui) { return }
    $script:ui.log.AppendText((("$text" -replace "`r", "").TrimEnd()) + "`r`n")
    [System.Windows.Forms.Application]::DoEvents()
}
function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color; Ui-Log $text }
function Step($text) {
    Write-Host ""; Write-Host "  > $text" -ForegroundColor Cyan
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
    while ($ui.form.Visible) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 50 }
}
function Fail($text) {
    Write-Host ""
    Write-Host "  X  $text" -ForegroundColor Red
    if ($script:ui) { Finish-Ui $false $text; exit 0 }  # el error ya se ha visto en la ventana: el .bat no tiene que parar
    exit 1
}
function Hide-Console {
    # la consola de INSTALAR.bat se esconde mientras la ventana esta abierta (INSTALAR.bat solo hace pause si fallamos)
    try {
        if (-not ("NotasLnk.Win" -as [type])) {
            Add-Type -Namespace NotasLnk -Name Win -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);'
        }
        $h = [NotasLnk.Win]::GetConsoleWindow()
        if ($h -ne [IntPtr]::Zero) { [NotasLnk.Win]::ShowWindow($h, 0) | Out-Null }
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
        try { $f.Icon = New-Object System.Drawing.Icon((Join-Path $app "static\notas.ico")) } catch {}
        $logo = New-Object System.Windows.Forms.PictureBox
        $logo.Location = New-Object System.Drawing.Point(24, 20)
        $logo.Size = New-Object System.Drawing.Size(64, 64)
        $logo.SizeMode = "Zoom"
        try { $logo.Image = [System.Drawing.Image]::FromFile((Join-Path $app "static\notas.png")) } catch {}
        $title = New-Object System.Windows.Forms.Label
        $title.Text = $AppName
        $title.Font = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold)
        $title.Location = New-Object System.Drawing.Point(100, 18)
        $title.AutoSize = $true
        $sub = New-Object System.Windows.Forms.Label
        $sub.Text = "grabar, transcribir y resumir reuniones  -  instalacion (sin permisos de administrador)"
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
        $status.Location = New-Object System.Drawing.Point(24, 474)
        $status.Size = New-Object System.Drawing.Size(712, 60)
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
        $f.Add_FormClosing({ param($s, $e) if (-not $script:ui.done) { $e.Cancel = $true; $script:ui.status.Text = "Espera a que termine la instalacion..." } })
        $script:ui = @{ form = $f; steps = $steps; log = $log; bar = $bar; status = $status; btnOpen = $btnOpen; btnClose = $btnClose
                        btnCopy = $btnCopy; current = $null; n = 0; total = 6; done = $false; abrir = $false
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
        if (-not ("NotasLnk.PropertyStore" -as [type])) {
            Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace NotasLnk {
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
        [NotasLnk.PropertyStore]::SetAppId($path, $id)
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
        Say "    Solo hay un Python mas nuevo que 3.$($MaxPy - 300) ($newer): si fallan las librerias, instala Python 3.13." Yellow
        return $newer
    }
    return $null
}

$Gui = $false
if (-not ($Consola -or $NoShortcut -or $NoLaunch) -and [Environment]::UserInteractive) { $Gui = New-InstallerWindow }

Write-Host ""
Write-Host "  ==========================================================" -ForegroundColor DarkCyan
Write-Host "    $($AppName.ToUpper())  ·  grabar, transcribir y resumir reuniones" -ForegroundColor White
Write-Host "  ==========================================================" -ForegroundColor DarkCyan
Say "    Carpeta: $root"

# archivos descargados de Teams/correo: sin esto Windows pregunta cada vez al abrirlos
Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

# --- si la app esta abierta se cierra: si no, al terminar se volveria a ver la version vieja (sigue en marcha)
Step "Comprobando si $AppName está abierto..."
$running = $null
try { $running = Invoke-RestMethod "http://127.0.0.1:$Port/api/ping" -TimeoutSec 2 } catch {}
if ($running -and $running.app -eq "audios") {
    $st = $null
    try { $st = Invoke-RestMethod "http://127.0.0.1:$Port/api/rec/status" -TimeoutSec 2 } catch {}
    if ($st -and $st.recording) { Fail "$AppName está grabando ahora mismo. Para la grabación y vuelve a abrir INSTALAR.bat." }
    # primero por las buenas: POST /api/apagar (versiones desde el 31/10/2026); si no contesta, como antes
    $apagado = $false
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:$Port/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers @{ "X-Audios" = "1" } -TimeoutSec 5
        for ($k = 0; $k -lt 24 -and -not $apagado; $k++) {
            Start-Sleep -Milliseconds 250
            try { $null = Invoke-RestMethod "http://127.0.0.1:$Port/api/ping" -TimeoutSec 1 } catch { $apagado = $true }
        }
    } catch {}
    $owner = $null
    if ($apagado) { $owner = -1 }
    try { $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess } catch {}
    if (-not $owner) {
        $m = netstat -ano -p tcp | Select-String ":$Port\s+\S+\s+LISTENING\s+(\d+)" | Select-Object -First 1
        if ($m) { $owner = [int]$m.Matches[0].Groups[1].Value }
    }
    if (-not $owner) { Fail "$AppName está abierto y no he podido cerrarlo: ciérralo (Ajustes > Apagar, o su ventana de consola) y vuelve a abrir INSTALAR.bat." }
    if ($owner -gt 0) {
        $ids = @($owner)
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
        for ($k = 0; $k -lt 2 -and $p; $k++) {  # tambien el "py" y la ventana de consola que lo lanzaron (instalaciones antiguas)
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
            if ($p -and ($p.Name -eq "py.exe" -or ($p.Name -eq "cmd.exe" -and $p.CommandLine -like "*Notas.bat*"))) { $ids += $p.ProcessId }
        }
        $ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    Say "    Cerrada la versión que estaba abierta (al terminar se abre la nueva)" Green
} else {
    Say "    No estaba abierto"
}

# --- si ya estaba instalado en otra carpeta (p. ej. el zip nuevo se ha descomprimido en Descargas),
# se actualiza aquella: ahi estan sus grabaciones, y el acceso directo ya apunta a ella
if (-not $NoShortcut -or $Desktop) {
    $old = $null
    try {
        $lnkPath = Join-Path $(if ($Desktop) { $Desktop } else { [Environment]::GetFolderPath("Desktop") }) "$AppName.lnk"
        if (Test-Path -LiteralPath $lnkPath) {
            $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath)
            # el acceso directo apunta a pythonw.exe (desde el 31/10/2026) o a Notas.bat: la carpeta de la app es
            # WorkingDirectory en los dos casos (y la del destino, en los antiguos)
            $oldApp = $sc.WorkingDirectory
            if (-not $oldApp -or -not (Test-Path -LiteralPath (Join-Path $oldApp "server.py"))) { $oldApp = Split-Path -Parent $sc.TargetPath }
            if ($oldApp -and (Test-Path -LiteralPath (Join-Path $oldApp "server.py")) -and
                ((Resolve-Path -LiteralPath $oldApp).Path -ne (Resolve-Path -LiteralPath $app).Path)) { $old = $oldApp }
        }
    } catch {}
    if ($old) {
        $oldRoot = Split-Path -Parent $old
        Step "Ya tenías $AppName en $oldRoot : actualizo esa (con tus grabaciones)..."
        Get-ChildItem -LiteralPath $app -File | Where-Object { $_.Extension -in ".py", ".bat", ".ps1" } |
            ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $old -Force }
        foreach ($sub in @("static", "export")) {  # export: plantillas del instalador (para "Compartir Notas")
            New-Item -ItemType Directory -Force (Join-Path $old $sub) | Out-Null
            Get-ChildItem -LiteralPath (Join-Path $app $sub) -File -ErrorAction SilentlyContinue |
                ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $old $sub) -Force }
        }
        # el manual y los .bat de la raiz tambien se renuevan en la carpeta de siempre
        foreach ($n in @("LEEME.txt", "INSTALAR.bat", "ABRIR NOTAS.bat")) {
            if (Test-Path -LiteralPath (Join-Path $root $n)) {
                Copy-Item -LiteralPath (Join-Path $root $n) -Destination (Join-Path $oldRoot $n) -Force
            }
        }
        # listas de vocabulario que traiga el paquete, sin pisar las que ya tengas
        Get-ChildItem -LiteralPath $root -Directory -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "Vocabulario" -and $_.FullName -notlike "$app*" } | ForEach-Object {
                $dst = Join-Path $oldRoot $_.FullName.Substring($root.Length).TrimStart('\')
                New-Item -ItemType Directory -Force $dst | Out-Null
                Get-ChildItem -LiteralPath $_.FullName -File | ForEach-Object {
                    $to = Join-Path $dst $_.Name
                    if (-not (Test-Path -LiteralPath $to)) { Copy-Item -LiteralPath $_.FullName -Destination $to }
                }
            }
        Get-ChildItem -LiteralPath $old -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
        Say "    Actualizada. La carpeta que acabas de descomprimir ya no hace falta: puedes borrarla." Green
        $app, $root = $old, $oldRoot
    }
}

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
        Fail ("No he podido instalar Python automaticamente. Instala la ultima version desde la pagina que se acaba de abrir " +
              "(marca 'Add python.exe to PATH') y vuelve a abrir INSTALAR.bat.")
    }
}
Say "    $py" Green

Step "Instalando librerias (la primera vez tarda unos minutos)..."
& $py -m pip --version *> $null
if ($LASTEXITCODE -ne 0) { & $py -m ensurepip --upgrade 2>&1 | ForEach-Object { Say "    $_" } }
function Pip-Install($extra, $list) {
    # pip escribe los errores por stderr: se ensenan y se guardan para distinguir despues que ha fallado
    $script:pipOut = @()
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
if ($coreOk) { Say ($v -join "`n") Green } else { Say ($v -join "`n") Yellow }
# sherpa-onnx es opcional (quien habla): se instala aparte para que un fallo suyo no deje sin librerias al resto,
# y si no importa solo se avisa: la app sigue funcionando sin esa opcion
if ($coreOk) {
    Step "Instalando las librerias opcionales (quien habla, calendario de Outlook)..."
    foreach ($opt in $optional) { $null = Pip-Install $userMode @($opt) }
}
$v = & $py -c "import sherpa_onnx; print('    sherpa-onnx', sherpa_onnx.__version__, '(quien habla: disponible)')" 2>$null
if ($LASTEXITCODE -eq 0) { Say ($v -join "`n") } else { Say "    sherpa-onnx no esta disponible: la opcion Voces (quien habla) no se podra activar en este PC" Yellow }
$v = & $py -c "import win32com.client; print('    pywin32 (calendario de Outlook: disponible)')" 2>$null
if ($LASTEXITCODE -eq 0) { Say ($v -join "`n") } else { Say "    pywin32 no esta disponible: no se podra leer el calendario de Outlook (el resto funciona)" Yellow }
# la ventana propia tampoco bloquea: sin pywebview (o sin WebView2) la app se abre en Edge como siempre
$v = & $py -c "import webview; from importlib.metadata import version; print('    pywebview', version('pywebview'), '(ventana propia: disponible)')" 2>$null
if ($LASTEXITCODE -eq 0) { Say ($v -join "`n") } else { Say "    pywebview no esta disponible: la app se abrira en una ventana de Edge" Yellow }
if (-not $coreOk) {
    $log = ($script:pipOut -join "`n")
    $pyVer = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($log -match "No matching distribution|Could not find a version that satisfies|is not a supported wheel") {
        Fail ("Alguna libreria (faster-whisper / ctranslate2) todavia no esta publicada para Python $pyVer. " +
              "Instala Python 3.13 (winget install -e --id Python.Python.3.13 --scope user, o desde python.org) " +
              "y vuelve a abrir INSTALAR.bat.")
    }
    if ($log -match "ProxyError|SSLError|CERTIFICATE_VERIFY|Could not fetch URL|Temporary failure|timed out|Max retries exceeded|Network is unreachable|Connection (refused|reset|aborted)") {
        Fail ("No se han podido descargar las librerias: parece la red o el proxy de la empresa. Prueba con otra red " +
              "(p. ej. compartiendo datos del movil) o pide a IT acceso a pypi.org, y vuelve a abrir INSTALAR.bat.")
    }
    Fail ("No se han podido instalar las librerias (Python $pyVer). Revisa los mensajes de arriba: en la red de la " +
          "empresa puede ser el proxy; con un Python muy nuevo, instala Python 3.13 y vuelve a abrir INSTALAR.bat.")
}
# el lanzador usa este Python (si hay varios instalados, el de las librerias)
[IO.File]::WriteAllText((Join-Path $app "python.txt"), $py, (New-Object Text.UTF8Encoding $false))
# pythonw.exe (junto a python.exe): el mismo interprete sin ventana de consola; el log completo queda en server.log
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
$sinConsola = Test-Path -LiteralPath $pyw
if (-not $sinConsola) { Say "    No hay pythonw.exe junto a $py : el acceso directo abrirá una ventana de consola (Notas.bat)" Yellow }

if (-not $NoShortcut) {
    Step "Creando el acceso directo '$AppName'..."
    $ws = New-Object -ComObject WScript.Shell
    $places = if ($Desktop) { @($Desktop) } else { @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs")) }  # escritorio y menu Inicio
    foreach ($dir in $places) {
        try {
            $lnkPath = Join-Path $dir "$AppName.lnk"
            $lnk = $ws.CreateShortcut($lnkPath)
            if ($sinConsola) {
                $lnk.TargetPath = $pyw
                $lnk.Arguments = '"' + (Join-Path $app "server.py") + '"'
                $lnk.WindowStyle = 1
            } else {
                $lnk.TargetPath = Join-Path $app "Notas.bat"
                $lnk.Arguments = ""
                $lnk.WindowStyle = 7  # la ventana de consola, minimizada
            }
            $lnk.WorkingDirectory = $app
            $lnk.IconLocation = (Join-Path $app "static\notas.ico") + ",0"
            $lnk.Description = "$AppName`: grabar, transcribir y resumir reuniones"
            $lnk.Save()
            $null = Set-ShortcutAppId $lnkPath $AppId
            Say "    $lnkPath" Green
        } catch {
            Say "    No se pudo crear en $dir (usa ABRIR NOTAS.bat)" Yellow
        }
    }
}

Write-Host ""
$ver = "$(Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\LEEME.txt') -TotalCount 1 -ErrorAction SilentlyContinue)"
$ver = if ($ver -match '\((vers[^)]*)\)') { " ($($Matches[1]))" } else { "" }
Say "  Listo$ver." Green
Say "  Si es la primera vez: Ajustes (rueda dentada) > Transcripcion en la nube > tu API key (Groq u otro proveedor)." Green
Say "  Para apagar $AppName del todo: Ajustes > Apagar $AppName (cerrar la ventana la deja en segundo plano)." Green
Say "  Instrucciones completas en LEEME.txt" Green
$abrir = -not $NoLaunch
if ($Gui) {
    Finish-Ui $true "Listo$ver. Si es la primera vez: Ajustes > Transcripcion en la nube > tu API key. Para apagar $AppName del todo: Ajustes > Apagar $AppName."
    $abrir = $script:ui.abrir
}
if ($abrir) {
    if ($sinConsola) { Start-Process -FilePath $pyw -ArgumentList "server.py" -WorkingDirectory $app }
    else { Start-Process -FilePath (Join-Path $app "Notas.bat") -WorkingDirectory $app -WindowStyle Minimized }
}
if (-not $Gui -and -not ($NoShortcut -or $NoLaunch) -and [Environment]::UserInteractive) {
    Write-Host ""
    Write-Host "  Pulsa una tecla para cerrar esta ventana." -ForegroundColor DarkGray
    try { $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") } catch {}
}
exit 0
