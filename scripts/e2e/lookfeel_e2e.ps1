# E2E del instalador, del arranque y del desinstalador en un runner windows-latest (PowerShell 5.1, el mismo que lanza
# INSTALAR.bat). Lo mismo que hara una persona: zip -> descomprimir en Descargas-test -> INSTALAR.bat (modo ventana,
# con autocierre) -> se instala en %USERPROFILE%\Tenca con su entrada de "Aplicaciones instaladas" -> la app abre su
# ventana -> acceso directo otra vez -> Apagar; una reunion falsa en Tenca\Proyecto Alfa; segunda instalacion desde
# otra carpeta (la encuentra por "Aplicaciones instaladas" y la actualiza en su sitio); tercera disfrazada de Chuleta (el
# nombre anterior: su entrada de "Aplicaciones instaladas", Chuleta.lnk en el escritorio, en Inicio y en la carpeta
# Inicio de Windows, ABRIR CHULETA.bat y un Notas.lnk roto): la encuentra por la entrada antigua y no deja nada con el
# nombre viejo; cuarta solo con los accesos directos Chuleta.lnk (la encuentra por ellos); quinta con Tenca.lnk y un
# Chuleta.lnk de la misma instalacion (queda uno) y otro de otra copia (se queda); una instalacion que falla a
# proposito (pantalla de error). Despues el desinstalador: ventana cancelada (no toca nada), con la app "grabando" (no
# toca nada), -Silencioso con la app abierta (quita accesos directos, entrada y _app; la reunion sigue igual) y, tras
# reinstalar en la misma carpeta, ventana con "Borrar tambien mis ajustes" (AudiosApp borrada; la reunion sigue igual).
# Por ultimo (fase v), la Chuleta de verdad (-Chuleta <sha>, la de main que tiene Bernardo) instalada con su propio
# instalador, abierta, con "Arrancar con Windows" y una reunion y un ajuste suyos, y Tenca instalada encima: la cierra,
# la actualiza en su carpeta, no queda nada con el nombre viejo ni duplicado, la reunion y los ajustes siguen; y se
# desinstala. Capturas de pantalla cada 5 s en paralelo y de la ventana del desinstalador.
# Deja en -Out: capturas/, desinstalar/, server.log, instalar.log, desinstalar.log, iconos del HWND, registro.txt y
# resumen.json (comprobacion -> ok/valor). Sale con 1 si falla alguna comprobacion con puerta.
param([string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)), [string]$Out = "", [string]$Chuleta = "")

$ErrorActionPreference = "Continue"
if (-not $Out) { $Out = Join-Path $Repo "e2e-out" }
New-Item -ItemType Directory -Force $Out | Out-Null
$script:checks = New-Object System.Collections.Specialized.OrderedDictionary
$script:gated = @{}
$Port = 8765
# constante: PowerShell no distingue mayusculas y una asignacion a $base en otra fase la pisaba (todas las
# peticiones iban despues a una ruta de disco); asi la que lo intente da error en esa linea
Set-Variable -Name Base -Option Constant -Value "http://127.0.0.1:$Port"
$App = "Tenca"        # common.APP_NAME: titulo de la ventana, carpeta raiz del zip, accesos directos
$Legacy = "Chuleta"   # nombre anterior: el instalador reconoce sus accesos directos, su entrada y sus .bat y los migra
$Legacy2 = "Notas"    # el de antes de Chuleta: sus accesos directos tambien se reconocen
$python = (Get-Command python).Source
$logDir = Join-Path $env:LOCALAPPDATA "AudiosApp"
$tokenFile = Join-Path $logDir "sesion.token"

function Check($name, $ok, $valor, $gate = $true) {
    $script:checks[$name] = @{ ok = [bool]$ok; valor = "$valor" }
    $script:gated[$name] = $gate
    $mark = $(if ($ok) { "OK  " } else { "FAIL" })
    Write-Host ("[$mark] $name : $valor")
}
function Note($name, $valor) { Check $name $true $valor $false }
function Save-Summary {
    $o = New-Object System.Collections.Specialized.OrderedDictionary
    foreach ($k in $script:checks.Keys) { $o[$k] = $script:checks[$k] }
    ($o | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath (Join-Path $Out "resumen.json") -Encoding UTF8
}
function Http-Status($url) {
    # codigo HTTP de un GET sin token (403 esperado) o 0 si no contesta
    try { return [int](Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 5).StatusCode } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return 0 }
    }
}
function Api-Headers($post) {
    # como el instalador: X-Audios en los POST y X-Chuleta-Token si la app ha escrito AudiosApp\sesion.token
    $h = @{}
    if ($post) { $h["X-Audios"] = "1" }
    $tf = Join-Path $logDir "sesion.token"
    if (Test-Path -LiteralPath $tf -PathType Leaf) { $tok = [IO.File]::ReadAllText($tf).Trim(); if ($tok) { $h["X-Chuleta-Token"] = $tok } }
    return $h
}
function Wait-Http($url, $seconds) {
    # cada intento espera hasta 30 s: en los runs 1 y 2, con la reunion falsa creada, la API tardaba mas de 3 s en
    # contestar en Windows (en macOS, sin ventana, 20 ms). Lo que tarda queda en $script:ultimaEspera
    $t0 = Get-Date
    $end = $t0.AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        try {
            $left = [int][Math]::Max(1, [Math]::Min(30, ($end - (Get-Date)).TotalSeconds))  # sin pasarse del total
            $r = Invoke-RestMethod $url -Headers (Api-Headers $false) -TimeoutSec $left
            $script:ultimaEspera = [Math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
            return $r
        } catch { Start-Sleep -Milliseconds 500 }
    }
    $script:ultimaEspera = [Math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    return $null
}
function Get-Listener {
    # solo los sockets con un proceso vivo detras: tras os._exit el socket puede seguir unos segundos en la tabla TCP
    # sin dueno (OwningProcess vacio o 0), y eso no es un servidor. Sin ninguno no devuelve nada (ni $null: @($null)
    # cuenta 1 y acababa en Get-Process -Id $null)
    try {
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Where-Object { $_.OwningProcess -and (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue) }
    } catch { }
}
function Describe-Listeners {
    # quien escucha en el puerto: nombre, pid, hora de arranque y linea de comandos (para entender un servidor inesperado)
    $out = @()
    foreach ($c in @(Get-Listener)) {
        $w = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
        $out += "pid=$($c.OwningProcess) " + $(if ($w) { "$($w.Name) desde $($w.CreationDate) :: $($w.CommandLine)" } else { "(sin proceso)" })
    }
    return ($out -join " || ")
}
function Wait-Process-Gone($procId, $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}
function Run-Installer($dir, $label, $timeoutMin) {
    # como el usuario: doble clic en INSTALAR.bat (cmd -> powershell 5.1 -> instalar.ps1 en modo ventana)
    $bat = Join-Path $dir "INSTALAR.bat"
    $t0 = Get-Date
    $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$bat`"" -WorkingDirectory $dir -PassThru
    $end = $t0.AddMinutes($timeoutMin)
    while (-not $p.HasExited -and (Get-Date) -lt $end) { Start-Sleep -Seconds 2 }
    if (-not $p.HasExited) {
        Check "$label.termina" $false "INSTALAR.bat no termino en $timeoutMin min (se mata)"
        Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*instalar.ps1*" -or $_.CommandLine -like "*INSTALAR.bat*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        return $null
    }
    $secs = [int]((Get-Date) - $t0).TotalSeconds
    Note "$label.segundos" $secs
    Note "$label.exitcode" $p.ExitCode
    return $p.ExitCode
}
function Get-Lnk($path) {
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $aumid = ""
    try { $aumid = [E2E.Lnk]::GetAppId($path) } catch { $aumid = "error: " + $_.Exception.Message }
    return @{ TargetPath = $sc.TargetPath; Arguments = $sc.Arguments; WorkingDirectory = $sc.WorkingDirectory
              IconLocation = $sc.IconLocation; Description = $sc.Description; AppUserModelID = $aumid }
}
function Ventana($cmd, $tag, $title = "") {
    if (-not $title) { $title = $App }
    $argv = @((Join-Path $Repo "scripts\e2e\ventana.py"), $cmd, $title)
    if ($cmd -eq "icons") { $argv += @((Join-Path $Out "iconos"), $tag) }
    $raw = & $python @argv 2>&1
    try { return ($raw | Out-String | ConvertFrom-Json) } catch { return @{ hwnd = $null; error = ($raw | Out-String) } }
}
function Tail-Log($name, $n = 40) {
    $f = Join-Path $logDir $name
    if (Test-Path -LiteralPath $f) { return ((Get-Content -LiteralPath $f -Tail $n) -join "`n") }
    return "(no existe $f)"
}
function Last-Run-Log($n = 400) {
    # solo lo que escribio la ULTIMA ejecucion de instalar.ps1: el fichero acumula todas las instalaciones (cada una
    # empieza por "==== fecha instalar.ps1 desde ..."), y una comprobacion de la fase f2 leia lineas de la fase f
    $f = Join-Path $logDir "instalar.log"
    if (-not (Test-Path -LiteralPath $f)) { return "(no existe $f)" }
    $lines = @(Get-Content -LiteralPath $f)
    if ($lines.Count -eq 0) { return "" }
    $start = 0
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -like "==== *instalar.ps1*") { $start = $i } }
    return (($lines[$start..($lines.Count - 1)] | Select-Object -Last $n) -join "`n")
}
function Copy-Logs($suffix) {
    foreach ($n in "server.log", "instalar.log") {
        $f = Join-Path $logDir $n
        if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination (Join-Path $Out ($n -replace "\.log$", "-$suffix.log")) -Force }
    }
    $d = Join-Path $env:TEMP "tenca-desinstalar.log"
    if (Test-Path -LiteralPath $d) { Copy-Item -LiteralPath $d -Destination (Join-Path $Out "desinstalar.log") -Force }
}

function Capture-Screen($path) {
    # una captura de toda la pantalla (como capturas.ps1) en $path
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
        $g.Dispose()
        New-Item -ItemType Directory -Force (Split-Path -Parent $path) | Out-Null
        $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        return $true
    } catch { return $false }
}
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$App"
$LegacyKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$Legacy"
function Get-Registro($key = $UninstallKey) {
    # valores de la entrada de "Aplicaciones instaladas": nombre -> @{ valor; tipo }
    $o = [ordered]@{}
    if (-not (Test-Path -LiteralPath $key)) { return $null }
    $k = Get-Item -LiteralPath $key
    foreach ($n in $k.GetValueNames()) { $o[$n] = @{ valor = $k.GetValue($n); tipo = "$($k.GetValueKind($n))" } }
    return $o
}
function Run-Orden($cmdline, $label, $timeoutSec) {
    # una orden de "Aplicaciones instaladas" (UninstallString / QuietUninstallString) tal cual, por cmd /c
    $t0 = Get-Date
    $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c $cmdline" -PassThru -WindowStyle Hidden
    $null = $p.Handle
    $ok = $p.WaitForExit($timeoutSec * 1000)
    if (-not $ok) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Check "$label.termina" $false "no termino en $timeoutSec s"; return $null }
    Note "$label.segundos" ([int]((Get-Date) - $t0).TotalSeconds)
    Note "$label.exitcode" $p.ExitCode
    return $p.ExitCode
}
function Last-Uninstall-Log {
    # solo la ultima ejecucion del desinstalador (cada una empieza por "==== fecha desinstalar.ps1 desde ...")
    $f = Join-Path $env:TEMP "tenca-desinstalar.log"
    if (-not (Test-Path -LiteralPath $f)) { return "(no existe $f)" }
    $lines = @(Get-Content -LiteralPath $f -Encoding UTF8)
    $start = 0
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -like "==== *desinstalar.ps1*") { $start = $i } }
    if ($lines.Count -eq 0) { return "" }
    return (($lines[$start..($lines.Count - 1)]) -join "`n")
}
function Get-Reunion {
    # sha256 de cada fichero de la reunion falsa (y su lista de vocabulario): ruta relativa -> hash
    $o = [ordered]@{}
    foreach ($f in @(Get-ChildItem -LiteralPath $reunionDir -Recurse -File -ErrorAction SilentlyContinue | Sort-Object FullName)) {
        $o[$f.FullName.Substring($reunionDir.Length)] = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
    }
    return (($o.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ";")
}
function Diag-Lenta($label) {
    # si la API tarda: CPU de los procesos de la app en 10 s y estado de las conexiones al puerto (para saber si el
    # servidor esta ocupado o las peticiones se quedan en cola). Solo nombre, pid y el script, nunca la linea entera
    $antes = @{}
    $procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^(pythonw?|msedgewebview2)\.exe$" })
    foreach ($p in $procs) { $antes[$p.ProcessId] = ($p.KernelModeTime + $p.UserModeTime) }
    Start-Sleep -Seconds 10
    $out = @()
    foreach ($p in @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^(pythonw?)\.exe$" })) {
        $cpu = [Math]::Round((($p.KernelModeTime + $p.UserModeTime) - [double]$antes[$p.ProcessId]) / 1e7, 1)
        $script = [regex]::Match("$($p.CommandLine)", "[\w-]+\.py").Value
        $out += "$($p.Name) $($p.ProcessId) $script cpu10s=$cpu hilos=$($p.ThreadCount)"
    }
    $wv = @($procs | Where-Object { $_.Name -eq "msedgewebview2.exe" }).Count
    $con = @(Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Group-Object State | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ","
    Note "$label.diag" (($out -join " | ") + " || webview2=$wv || puerto: $con")
}
function Wait-Tree($text, $seconds) {
    # el arbol de la app (/api/tree) en JSON en cuanto contiene $text (la biblioteca se lee en segundo plano al arrancar)
    $end = (Get-Date).AddSeconds($seconds)
    $json = ""
    while ((Get-Date) -lt $end) {
        try { $json = Invoke-RestMethod "$Base/api/tree" -Headers (Api-Headers $false) -TimeoutSec 30 | ConvertTo-Json -Depth 30 -Compress } catch { $json = "" }
        if ($json -match [regex]::Escape($text)) { return $json }
        Start-Sleep -Seconds 1
    }
    return $json
}
function Wait-Window($title, $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        $w = Ventana "find" "" $title
        if ($w.hwnd -and $w.visible) { return $w }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

# lector del System.AppUserModel.ID de un .lnk (IPropertyStore, solo lectura)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace E2E {
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  public struct PropertyKey { public Guid fmtid; public uint pid; public PropertyKey(Guid f, uint p) { fmtid = f; pid = p; } }
  [StructLayout(LayoutKind.Explicit)]
  public struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; [FieldOffset(16)] public IntPtr p2; }
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore {
    void GetCount(out uint cProps);
    void GetAt(uint iProp, out PropertyKey pkey);
    void GetValue(ref PropertyKey key, out PropVariant pv);
    void SetValue(ref PropertyKey key, ref PropVariant pv);
    void Commit();
  }
  public static class Lnk {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern IPropertyStore SHGetPropertyStoreFromParsingName(string path, IntPtr pbc, int flags, ref Guid riid);
    [DllImport("ole32.dll", PreserveSig = false)] static extern void PropVariantClear(ref PropVariant pv);
    public static string GetAppId(string lnk) {
      Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
      IPropertyStore ps = SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 0, ref iid);
      PropertyKey key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
      PropVariant pv;
      try {
        ps.GetValue(ref key, out pv);
        string s = (pv.vt == 31) ? Marshal.PtrToStringUni(pv.p) : "";
        PropVariantClear(ref pv);
        return s;
      } finally { Marshal.ReleaseComObject(ps); }
    }
  }
}
"@

Note "entorno.os" ((Get-CimInstance Win32_OperatingSystem).Caption + " " + (Get-CimInstance Win32_OperatingSystem).Version)
Note "entorno.powershell" "$($PSVersionTable.PSVersion)"
Note "entorno.python" "$python"
Note "entorno.interactivo" ([Environment]::UserInteractive)
Note "entorno.py_launcher" ([bool](Get-Command py -ErrorAction SilentlyContinue))

# ---- a) zip y carpeta de prueba
$zip = Join-Path $Out "$App - instalador.zip"
$buildOut = & $python (Join-Path $Repo "scripts\build_zip.py") $zip 2>&1
Check "a.zip" (Test-Path -LiteralPath $zip) ($buildOut -join " ")
$test1 = Join-Path $env:USERPROFILE "Descargas-test"
Expand-Archive -LiteralPath $zip -DestinationPath $test1 -Force
$dir1 = Join-Path $test1 $App
Check "a.extraido" (Test-Path -LiteralPath (Join-Path $dir1 "INSTALAR.bat")) $dir1
Check "a.sin_nombre_anterior" (-not (Test-Path -LiteralPath (Join-Path $test1 $Legacy)) -and -not (Test-Path -LiteralPath (Join-Path $dir1 "ABRIR CHULETA.bat")) -and -not (Test-Path -LiteralPath (Join-Path $dir1 "_app\Chuleta.bat")) -and -not (Test-Path -LiteralPath (Join-Path $dir1 "_app\static\chuleta.ico")) -and (Test-Path -LiteralPath (Join-Path $dir1 "ABRIR TENCA.bat")) -and (Test-Path -LiteralPath (Join-Path $dir1 "_app\Tenca.bat"))) ""
$verTxt = Join-Path $dir1 "_app\version.txt"
$verZip = $(if (Test-Path -LiteralPath $verTxt) { (Get-Content -LiteralPath $verTxt -Encoding UTF8 -Raw).Trim() } else { "" })
Check "a.version_txt" ($verZip -match "^\d{4}-\d{2}-\d{2} . [0-9a-f]{7,}$") $verZip
Check "a.leeme_lleva_version" ((Get-Content -LiteralPath (Join-Path $dir1 "LEEME.txt") -TotalCount 1) -like "*$verZip*") ((Get-Content -LiteralPath (Join-Path $dir1 "LEEME.txt") -TotalCount 1))

# ---- b) capturas en paralelo
$capDir = Join-Path $Out "capturas"
$cap = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Repo "scripts\e2e\capturas.ps1"), "-Dir", $capDir, "-Intervalo", "5" -PassThru
Note "b.capturador_pid" $cap.Id
Start-Sleep -Seconds 6
Note "b.primera_captura" ((Get-ChildItem $capDir -Filter *.png -ErrorAction SilentlyContinue | Measure-Object).Count)

# ---- c) INSTALAR.bat sin flags, modo ventana, autocierre 15 s
if (Test-Path -LiteralPath (Join-Path $logDir "instalar.log")) { Remove-Item -LiteralPath (Join-Path $logDir "instalar.log") -Force }
$env:NOTAS_INSTALADOR_AUTOCERRAR = "15"
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = ""
$rc = Run-Installer $dir1 "c.instalador" 25  # pip real (faster-whisper, sherpa-onnx, pywebview, pywin32): 5-14 min segun el runner
# doble apertura (Bernardo, msg 1143: "se me abrian como 2 veces la app"): el instalador acaba de abrir la app (como
# "Abrir"; aqui, el autocierre) y el usuario pulsa ademas el acceso directo mientras arranca. Se comprueba en e)
$deskDoble = Join-Path ([Environment]::GetFolderPath("Desktop")) "$App.lnk"
$doble = $null
if (Test-Path -LiteralPath $deskDoble) {
    $ldoble = Get-Lnk $deskDoble
    $doble = Start-Process -FilePath $ldoble.TargetPath -ArgumentList $ldoble.Arguments -WorkingDirectory $ldoble.WorkingDirectory -PassThru
    $null = $doble.Handle  # sin el handle, PowerShell 5.1 no guarda ExitCode cuando el proceso termina
}
Copy-Logs "c"
$ilog = Last-Run-Log 400
Check "c.exitcode_0" ($rc -eq 0) $rc
Check "c.modo_ventana" ($ilog -match "modo: ventana") (($ilog -split "`n" | Select-String "modo:") -join "; ")
Check "c.sin_winget" (-not ($ilog -match "winget install")) ($ilog -match "No hay Python")
Check "c.listo" ($ilog -match "Listo") (($ilog -split "`n" | Select-String "Listo" | Select-Object -First 1) -join "")
Check "c.listo_con_version" ($ilog -match "Listo \(versi.n \d{4}-\d{2}-\d{2} . [0-9a-f]{7,}\)") ""
Check "c.sin_lnk_que_migrar" (-not ($ilog -match "pasa a llamarse")) ""
Check "c.autocierre" ($ilog -match "autocierre tras") ""
$inst = Join-Path $env:USERPROFILE $App  # la carpeta propia
Check "c.instala_en_carpeta_propia" (Test-Path -LiteralPath (Join-Path $inst "_app\server.py")) $inst
Check "c.log_instalando_en" ($ilog -match ("Instalando en " + [regex]::Escape($inst))) (($ilog -split "`n" | Select-String "Instalando en" | Select-Object -First 1) -join "")
Check "c.log_donde_quedo" ($ilog -match ("Instalada en " + [regex]::Escape($inst))) (($ilog -split "`n" | Select-String "Instalada en" | Select-Object -First 1) -join "")
Check "c.log_descomprimida_borrable" ($ilog -match "descomprimiste .* ya no hace falta") (($ilog -split "`n" | Select-String "descomprimiste" | Select-Object -First 1) -join "")
$faltan = @("LEEME.txt", "LICENSE", "INSTALAR.bat", "ABRIR TENCA.bat", "_app\desinstalar.ps1", "_app\instalar.ps1", "_app\version.txt", "_app\static\tenca.ico", "_app\Tenca.bat", "_app\export\LEEME.txt") | Where-Object { -not (Test-Path -LiteralPath (Join-Path $inst $_)) }
Check "c.copiados" ($faltan.Count -eq 0) ("faltan: " + ($faltan -join ", "))
Check "c.version_copiada" (((Get-Content -LiteralPath (Join-Path $inst "_app\version.txt") -Encoding UTF8 -Raw).Trim()) -eq $verZip) ""
Check "c.descomprimida_intacta" ((Test-Path -LiteralPath (Join-Path $dir1 "_app\server.py")) -and -not (Test-Path -LiteralPath (Join-Path $dir1 "_app\python.txt"))) $dir1
Check "c.sin_carpeta_2" (-not (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "$App (2)"))) ""

# ---- d) accesos directos, python.txt, librerias, zombis
$desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "$App.lnk"
$prog = Join-Path ([Environment]::GetFolderPath("Programs")) "$App.lnk"
$deskLegacy = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Legacy.lnk"
$progLegacy = Join-Path ([Environment]::GetFolderPath("Programs")) "$Legacy.lnk"
$deskLegacy2 = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Legacy2.lnk"
$startupDir = [Environment]::GetFolderPath("Startup")  # "Arrancar con Windows": la copia del acceso directo de Inicio
$startup = Join-Path $startupDir "$App.lnk"
$startupLegacy = Join-Path $startupDir "$Legacy.lnk"
$pyTxt = Join-Path $inst "_app\python.txt"
Check "d.python_txt" (Test-Path -LiteralPath $pyTxt) $(if (Test-Path -LiteralPath $pyTxt) { Get-Content -LiteralPath $pyTxt -Raw } else { "" })
$pyApp = $(if (Test-Path -LiteralPath $pyTxt) { (Get-Content -LiteralPath $pyTxt -Raw).Trim() } else { $python })
$pyw = Join-Path (Split-Path -Parent $pyApp) "pythonw.exe"
foreach ($pair in @(@("escritorio", $desk), @("programas", $prog))) {
    $label, $path = $pair[0], $pair[1]
    if (-not (Test-Path -LiteralPath $path)) { Check "d.lnk.$label" $false "no existe $path"; continue }
    $l = Get-Lnk $path
    Note "d.lnk.$label.props" (($l.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " | ")
    Check "d.lnk.$label.target_pythonw" ($l.TargetPath -ieq $pyw) $l.TargetPath
    Check "d.lnk.$label.arguments" ($l.Arguments -like "*server.py*") $l.Arguments
    Check "d.lnk.$label.workdir" ($l.WorkingDirectory -ieq (Join-Path $inst "_app")) $l.WorkingDirectory
    Check "d.lnk.$label.icon" ($l.IconLocation -like "*static\tenca.ico*") $l.IconLocation
    Check "d.lnk.$label.description" ($l.Description -eq "${App}: grabar, transcribir y resumir reuniones") $l.Description
    Check "d.lnk.$label.aumid" ($l.AppUserModelID -eq "Bernardo.Chuleta") $l.AppUserModelID
}
Check "d.sin_lnk_antiguo" (-not (Test-Path -LiteralPath $deskLegacy) -and -not (Test-Path -LiteralPath $progLegacy)) ""
Check "d.sin_arranque" (-not (Test-Path -LiteralPath $startup) -and -not (Test-Path -LiteralPath $startupLegacy)) ""  # apagado por defecto
# "Aplicaciones instaladas": la clave, sus valores y sus tipos (se vuelcan a registro.txt)
$reg = Get-Registro
Check "d.registro.existe" ($null -ne $reg) $UninstallKey
Check "d.registro.sin_antigua" (-not (Test-Path -LiteralPath $LegacyKey)) $LegacyKey
if ($reg) {
    ($reg.GetEnumerator() | ForEach-Object { "$($_.Key) [$($_.Value.tipo)] = $($_.Value.valor)" }) | Set-Content -LiteralPath (Join-Path $Out "registro.txt") -Encoding UTF8
    Note "d.registro.volcado" (($reg.GetEnumerator() | ForEach-Object { "$($_.Key)[$($_.Value.tipo)]=$($_.Value.valor)" }) -join " | ")
    $cmdDes = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $inst "_app\desinstalar.ps1") + '"'
    $kbApp = [int][Math]::Ceiling([double](Get-ChildItem -LiteralPath (Join-Path $inst "_app") -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1024)
    $espera = [ordered]@{
        DisplayName = @("String", $App); DisplayVersion = @("String", $verZip); Publisher = @("String", "Bernardo Stegmaier")
        DisplayIcon = @("String", (Join-Path $inst "_app\static\tenca.ico")); InstallLocation = @("String", $inst)
        InstallDate = @("String", (Get-Date -Format "yyyyMMdd")); NoModify = @("DWord", 1); NoRepair = @("DWord", 1)
        URLInfoAbout = @("String", "https://github.com/bastegman/tenca"); UninstallString = @("String", $cmdDes)
        QuietUninstallString = @("String", "$cmdDes -Silencioso")
    }
    foreach ($n in $espera.Keys) {
        $v = $reg[$n]
        Check "d.registro.$n" ($v -and $v.tipo -eq $espera[$n][0] -and "$($v.valor)" -eq "$($espera[$n][1])") $(if ($v) { "[$($v.tipo)] $($v.valor)" } else { "no existe" })
    }
    $es = $reg["EstimatedSize"]
    # KB de _app: el instalador lo midio antes de que la app escribiera __pycache__, asi que puede ser algo menor
    Check "d.registro.EstimatedSize" ($es -and $es.tipo -eq "DWord" -and [int]$es.valor -gt 100 -and [int]$es.valor -le $kbApp) $(if ($es) { "[$($es.tipo)] $($es.valor) KB (ahora _app ocupa $kbApp KB)" } else { "no existe" })
}
# lo que ve Windows: el proveedor "Programs" de PackageManagement lee las mismas claves Uninstall que el Panel de control
# (en un job con tope de 90 s: si PackageManagement pidiera algo, no cuelga el E2E)
$jp = Start-Job -ScriptBlock { param($n) Get-Package -Name $n -ProviderName Programs -ErrorAction Stop | ForEach-Object { "$($_.Name) $($_.Version) [$($_.ProviderName)]" } } -ArgumentList $App
if (Wait-Job $jp -Timeout 90) { $pk = @(Receive-Job $jp -ErrorAction SilentlyContinue 2>&1) } else { $pk = @("sin respuesta en 90 s"); Stop-Job $jp }
Remove-Job $jp -Force -ErrorAction SilentlyContinue
Note "d.get_package" (($pk | ForEach-Object { "$_" }) -join "; ")
# y como lo ve una persona: Panel de control > Programas y caracteristicas, y Configuracion > Aplicaciones instaladas
try {
    Start-Process -FilePath "control.exe" -ArgumentList "appwiz.cpl" | Out-Null
    Start-Sleep -Seconds 7
    Note "d.captura_programas" (Capture-Screen (Join-Path $Out "desinstalar\programas-y-caracteristicas.png"))
    (New-Object -ComObject Shell.Application).Windows() | Where-Object { "$($_.LocationURL)$($_.LocationName)" -match "Programs|Programas|appwiz|::\{7B81BE6A" } | ForEach-Object { $_.Quit() }
} catch { Note "d.captura_programas_error" $_.Exception.Message }
try {
    Start-Process "ms-settings:appsfeatures" | Out-Null
    Start-Sleep -Seconds 8
    Note "d.captura_configuracion" (Capture-Screen (Join-Path $Out "desinstalar\configuracion-aplicaciones.png"))
    Get-Process -Name "SystemSettings" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
} catch { Note "d.captura_configuracion_error" $_.Exception.Message }
$imp = & $pyApp -c "import webview, faster_whisper, soundcard, requests, truststore; from importlib.metadata import version; print('pywebview', version('pywebview'), 'faster-whisper', faster_whisper.__version__)" 2>&1
Check "d.import_librerias" ($LASTEXITCODE -eq 0) ($imp -join " ")
# calendario de Outlook (fix-calendario): pywin32, que instala INSTALAR.bat como opcional, se importa con el Python de la
# app y calendario.estado habla COM de verdad (el runner no tiene Office: "sin_outlook", nivel 1, en pocos segundos)
$appDir = Join-Path $inst "_app"
$env:NOTAS_E2E_APPDIR = $appDir
$impW = & $pyApp -c "import os, sys, time; import pythoncom, pywintypes, win32com.client; sys.path.insert(0, os.environ['NOTAS_E2E_APPDIR']); import calendario; t = time.monotonic(); r = calendario.estado(True, probar=True); print('pywin32', r['estado'], r['nivel'], round(time.monotonic() - t, 1), 's')" 2>&1
Check "d.import_pywin32_y_calendario" ($LASTEXITCODE -eq 0 -and (($impW -join " ") -match "pywin32 sin_outlook 1 ")) ($impW -join " ")
$zombis = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*instalar.ps1*" -or $_.CommandLine -like "*INSTALAR.bat*" })
Check "d.sin_zombis" ($zombis.Count -eq 0) (($zombis | ForEach-Object { "$($_.Name) $($_.ProcessId)" }) -join ", ")

# ---- e) la app lanzada por el instalador
$cfg = Wait-Http "$Base/api/config" 90
Note "e.api_tarda_s" $script:ultimaEspera
Check "e.api_config" ($null -ne $cfg) $(if ($cfg) { "app_name=$($cfg.app_name) window=$($cfg.window) version=$($cfg.version)" } else { "sin respuesta en 90 s" })
Check "e.config_window_true" ($cfg -and $cfg.window -eq $true) "$($cfg.window)"
Check "e.config_app_name" ($cfg -and $cfg.app_name -eq $App) "$($cfg.app_name)"
Check "e.root_carpeta_propia" ($cfg -and $cfg.root -ieq $inst) "root=$($cfg.root)"
Check "e.config_version_del_zip" ($cfg -and $cfg.version -eq $verZip) "$($cfg.version) vs $verZip"
Check "e.config_acerca" ($cfg -and $cfg.acerca.repo -eq "https://github.com/bastegman/tenca" -and $cfg.acerca.licencia -eq "MIT" -and $cfg.acerca.datos -ieq $logDir) ("repo=$($cfg.acerca.repo) licencia=$($cfg.acerca.licencia) datos=$($cfg.acerca.datos)")
try { $diag = Invoke-RestMethod "$Base/api/diagnostico" -Headers (Api-Headers $false) -TimeoutSec 10 } catch { $diag = $null }
Check "e.diagnostico" ($diag -and $diag.texto -match "^$App . versi.n " -and $diag.texto -match "server\.log") $(if ($diag) { ($diag.texto -split "`n" | Select-Object -First 3) -join " | " } else { "sin respuesta" })
Check "e.server_header" (((Invoke-WebRequest "$Base/api/ping" -UseBasicParsing -Headers (Api-Headers $false) -TimeoutSec 5).Headers["Server"]) -like "$App/*") ((Invoke-WebRequest "$Base/api/ping" -UseBasicParsing -Headers (Api-Headers $false) -TimeoutSec 5).Headers["Server"])
$tok = $(if (Test-Path -LiteralPath $tokenFile) { (Get-Content -LiteralPath $tokenFile -Raw).Trim() } else { "" })
Check "e.token_sesion" ($tok.Length -ge 40) ("longitud " + $tok.Length)
Start-Sleep -Seconds 6  # que la ventana termine de pintar (y una captura mas)
$lis = @(Get-Listener)
Check "e.un_servidor" ($lis.Count -eq 1) ("listeners en ${Port}: " + $lis.Count)
$appPid = $(if ($lis.Count -ge 1) { $lis[0].OwningProcess } else { 0 })
$proc = $(if ($appPid) { Get-Process -Id $appPid -ErrorAction SilentlyContinue } else { $null })
Check "e.proceso_pythonw" ($proc -and $proc.Name -ieq "pythonw") $(if ($proc) { "$($proc.Name) pid $appPid" } else { "sin proceso" })
$w = Ventana "icons" "app"
Check "e.ventana_visible" ($w.hwnd -and $w.visible) ("hwnd=$($w.hwnd) clase=$($w.class) visible=$($w.visible) pid=$($w.pid)")
Check "e.titulo" ($w.title -eq $App) "$($w.title)"
# la segunda copia (el acceso directo pulsado durante el arranque) termina sola, sin aviso de error, y no deja Edge
$dobleSalio = [bool]($doble -and $doble.WaitForExit(30000))
Check "e.doble_apertura_segunda_termina" ($dobleSalio -and $doble.ExitCode -eq 0) $(if ($doble) { "pid $($doble.Id) exit=$(if ($dobleSalio) { $doble.ExitCode } else { 'sigue viva' })" } else { "sin acceso directo" })
$edgeApp = @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Where-Object { "$($_.CommandLine)" -like "*--app=http://127.0.0.1:$Port*" })
Check "e.doble_apertura_sin_edge" ($edgeApp.Count -eq 0) ("msedge --app: " + $edgeApp.Count)
$slogDoble = Tail-Log "server.log" 200
Note "e.doble_apertura_log" (($slogDoble -split "`n" | Select-String "abriendo|ya estaba abierta|cookie entregada") -join " | ")
Check "e.doble_apertura_sin_aviso" (-not ($slogDoble -match "no ha podido arrancar")) (($slogDoble -split "`n" | Select-String "no ha podido arrancar") -join " | ")
Check "e.pid_ventana_es_el_servidor" ($w.pid -eq $appPid) "$($w.pid) vs $appPid"
Note "e.iconos_hwnd" ($(if ($w.icons) { ($w.icons.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value.px)" }) -join " " } else { "" }))
Check "e.icon_big_256" ($w.icons -and $w.icons.ICON_BIG.px -eq "256x256") "$($w.icons.ICON_BIG.px)"
$slog = Tail-Log "server.log" 200
Check "e.log_aumid" ($slog -match "AppUserModelID Bernardo\.Chuleta: fijado") ""
Check "e.log_ventana" ($slog -match "ventana abierta \(WebView2") (($slog -split "`n" | Select-String "ventana abierta") -join "")
# icono en la bandeja (item 2, msg 1143): se pone con la ventana propia y se quita al apagar (sin icono fantasma)
Check "e.log_bandeja" ($slog -match "icono en la bandeja \(hwnd \d+\)") (($slog -split "`n" | Select-String "bandeja") -join " | ")
# la ventana abre /?t=<token>: el servidor le da la cookie de sesion y la interfaz habla con la API sin ningun 403
Check "e.log_cookie_interfaz" ($slog -match "cookie entregada a la interfaz") (($slog -split "`n" | Select-String "sesi.n:") -join " | ")
Check "e.interfaz_sin_403" (-not ($slog -match "403 sin token de sesi.n \(navegador\)")) (($slog -split "`n" | Select-String "403 sin token") -join " | ")
$sinToken = Http-Status "$Base/api/config"
Check "e.api_sin_token_403" ($sinToken -eq 403) "HTTP $sinToken"
# ocultar (como al pulsar la X y «Entendido») y volver a abrir con el acceso directo
try { $r = Invoke-RestMethod "$Base/api/ocultar" -Method Post -ContentType "application/json" -Body "{}" -Headers (Api-Headers $true) -TimeoutSec 5 } catch { $r = $null }
Start-Sleep -Seconds 2
$w2 = Ventana "find"
Check "e.ocultar" ($r -and $r.ok -eq $true -and $w2.hwnd -and -not $w2.visible) ("ok=$($r.ok) visible=$($w2.visible)")
$l = Get-Lnk $desk
$again = Start-Process -FilePath $l.TargetPath -ArgumentList $l.Arguments -WorkingDirectory $l.WorkingDirectory -PassThru
$salio = $again.WaitForExit(30000)
Start-Sleep -Seconds 3
Check "e.acceso_directo_termina" $salio ("pid " + $again.Id + " exit=" + $(if ($salio) { $again.ExitCode } else { "sigue vivo" }))
$lis2 = @(Get-Listener)
$w3 = Ventana "find"
Check "e.acceso_directo_no_duplica" ($lis2.Count -eq 1 -and $lis2[0].OwningProcess -eq $appPid) ("listeners=" + $lis2.Count + " pid=" + $(if ($lis2.Count) { $lis2[0].OwningProcess } else { "" }))
Check "e.acceso_directo_muestra" ($w3.hwnd -and $w3.visible) ("visible=$($w3.visible)")
$slog = Tail-Log "server.log" 200
Check "e.log_ya_abierta" ($slog -match "ya estaba abierta") ""
Start-Sleep -Seconds 5
# apagar
try { $r = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers (Api-Headers $true) -TimeoutSec 5 } catch { $r = @{ error = $_.Exception.Message } }
$gone = Wait-Process-Gone $appPid 5
Check "e.apagar_responde" ($r -and $r.ok -eq $true) ("$($r | ConvertTo-Json -Compress)")
Check "e.apagar_proceso_muere_5s" $gone "pid $appPid"
Check "e.apagar_borra_token" (-not (Test-Path -LiteralPath $tokenFile)) $tokenFile
Start-Sleep -Seconds 1
$slog = Tail-Log "server.log" 60
Check "e.log_apagado" ($slog -match "(?m)^.*\bapagado$") (($slog -split "`n" | Select-String "apag") -join " | ")
Check "e.log_bandeja_retirada" ($slog -match "icono de la bandeja retirado") (($slog -split "`n" | Select-String "bandeja") -join " | ")
Copy-Logs "e"

# ---- r) una reunion falsa en la carpeta propia: audio + transcripcion + notas + resumen y una lista de vocabulario.
# Ninguna instalacion ni desinstalacion la puede tocar (se compara su sha256 en cada fase)
$reunionDir = Join-Path $inst "Proyecto Alfa"
New-Item -ItemType Directory -Force (Join-Path $reunionDir "Vocabulario") | Out-Null
$wav = Join-Path $reunionDir "2026-10-31 10-00-00 Reunion de prueba.wav"
& $python -c "import sys, wave; w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(32000)); w.close()" $wav
$reunionBase = $wav.Substring(0, $wav.Length - 4)  # no $base: seria $Base (la API)
Set-Content -LiteralPath "$reunionBase.txt" -Value "Transcripcion de prueba: hola." -Encoding UTF8
Set-Content -LiteralPath "$reunionBase.md" -Value "# Notas de prueba" -Encoding UTF8
Set-Content -LiteralPath "$reunionBase.resumen.md" -Value "Resumen de prueba" -Encoding UTF8
Set-Content -LiteralPath (Join-Path $reunionDir "Vocabulario\Tema.txt") -Value "Alfa" -Encoding UTF8
$reunion0 = Get-Reunion
Check "r.creada" ((Test-Path -LiteralPath $wav) -and ($reunion0 -split ";").Count -eq 5) $reunion0

# ---- f) segunda instalacion desde otra carpeta: la encuentra por "Aplicaciones instaladas" y la actualiza en su sitio
# (con la reunion), sin copiar nada a la carpeta nueva ni moverla
$test2 = Join-Path $env:USERPROFILE "Otra-carpeta"
Expand-Archive -LiteralPath $zip -DestinationPath $test2 -Force
$dir2 = Join-Path $test2 $App
Set-Content -LiteralPath (Join-Path $inst "LEEME.txt") -Value "VIEJO" -Encoding UTF8  # marca: la actualizacion debe pisarlo
Set-Content -LiteralPath (Join-Path $inst "_app\version.txt") -Value "2000-01-01 . 0000000" -Encoding UTF8  # version vieja: debe pisarse
Remove-Item -LiteralPath (Join-Path $inst "LICENSE") -Force  # P2-37: la actualizacion debe volver a ponerla
# P2-16: marca de Internet (Zone.Identifier, zona 3) en ficheros del zip "descargado" y en la reunion del usuario
$motw = @("[ZoneTransfer]", "ZoneId=3")
foreach ($f in @((Join-Path $dir2 "_app\window.py"), (Join-Path $dir2 "LEEME.txt"), $wav)) { Set-Content -LiteralPath $f -Stream Zone.Identifier -Value $motw }
Check "f.preparada_marca" ((Get-Item -LiteralPath $wav -Stream Zone.Identifier -ErrorAction SilentlyContinue) -and (Get-Item -LiteralPath (Join-Path $dir2 "_app\window.py") -Stream Zone.Identifier -ErrorAction SilentlyContinue)) ""
$rc2 = Run-Installer $dir2 "f.instalador" 15
Copy-Logs "f"
$ilog2 = Last-Run-Log 400
Check "f.exitcode_0" ($rc2 -eq 0) $rc2
Check "f.detecta_por_registro" ($ilog2 -match "Encontrada por: Aplicaciones instaladas") (($ilog2 -split "`n" | Select-String "Encontrada por" | Select-Object -First 1) -join "")
Check "f.actualiza_la_instalada" ($ilog2 -match ("Ya ten.as $App en " + [regex]::Escape($inst))) (($ilog2 -split "`n" | Select-String "Ya ten" | Select-Object -First 1) -join "")
Check "f.no_instala_en_otra" (-not ($ilog2 -match "Instalando en ")) ""
Check "f.leeme_actualizado" ((Get-Content -LiteralPath (Join-Path $inst "LEEME.txt") -Raw) -notmatch "^VIEJO") ""
Check "f.version_txt_actualizado" (((Get-Content -LiteralPath (Join-Path $inst "_app\version.txt") -Encoding UTF8 -Raw).Trim()) -eq $verZip) ((Get-Content -LiteralPath (Join-Path $inst "_app\version.txt") -Encoding UTF8 -Raw).Trim())
Check "f.sin_python_txt_en_nueva" (-not (Test-Path -LiteralPath (Join-Path $dir2 "_app\python.txt"))) ""
Check "f.window_py_copiado" ((Get-FileHash (Join-Path $inst "_app\window.py")).Hash -eq (Get-FileHash (Join-Path $dir2 "_app\window.py")).Hash) ""
Check "f.desinstalar_copiado" ((Get-FileHash (Join-Path $inst "_app\desinstalar.ps1")).Hash -eq (Get-FileHash (Join-Path $dir2 "_app\desinstalar.ps1")).Hash) ""
Check "f.reunion_intacta" ((Get-Reunion) -eq $reunion0) ""
Check "f.licencia_copiada" (Test-Path -LiteralPath (Join-Path $inst "LICENSE")) ""
Check "f.paquete_sin_marca" (-not (Get-Item -LiteralPath (Join-Path $inst "_app\window.py") -Stream Zone.Identifier -ErrorAction SilentlyContinue) -and -not (Get-Item -LiteralPath (Join-Path $inst "LEEME.txt") -Stream Zone.Identifier -ErrorAction SilentlyContinue)) ""
Check "f.reunion_conserva_marca" ($null -ne (Get-Item -LiteralPath $wav -Stream Zone.Identifier -ErrorAction SilentlyContinue)) $wav
$regf = Get-Registro
Check "f.registro_misma_carpeta" ($regf -and $regf["InstallLocation"].valor -ieq $inst -and $regf["DisplayVersion"].valor -eq $verZip) $(if ($regf) { "$($regf['InstallLocation'].valor) $($regf['DisplayVersion'].valor)" } else { "sin clave" })
$cfg2 = Wait-Http "$Base/api/config" 150
Note "f.api_tarda_s" $script:ultimaEspera
if ($script:ultimaEspera -gt 5) { Diag-Lenta "f" }
Check "f.app_arranca_desde_la_instalada" ($cfg2 -and $cfg2.root -ieq $inst) "root=$($cfg2.root)"
Check "f.app_version_nueva" ($cfg2 -and $cfg2.version -eq $verZip) "$($cfg2.version)"
$tree = Wait-Tree "Reunion de prueba" 40
Check "f.app_ve_la_reunion" ($tree -match "Proyecto Alfa" -and $tree -match "Reunion de prueba") $(if ($tree) { "$($tree.Length) caracteres" } else { "sin respuesta" })
Start-Sleep -Seconds 4
$lisf = @(Get-Listener)
if ($lisf.Count -ge 1) {
    $pidf = $lisf[0].OwningProcess
    try { $null = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers (Api-Headers $true) -TimeoutSec 30 } catch {}
    Check "f.apagar" (Wait-Process-Gone $pidf 20) "pid $pidf"
}

# ---- fn) tercera instalacion, disfrazada de Chuleta (el nombre anterior), como la dejaria su instalador: su entrada
# de "Aplicaciones instaladas" (no la de Tenca), Chuleta.lnk en el escritorio, en Inicio y en la carpeta Inicio de
# Windows ("Arrancar con Windows"), ABRIR CHULETA.bat y _app\Chuleta.bat, y un Notas.lnk que ya no abre nada. Debe
# encontrarla por la entrada antigua, actualizarla en su sitio y no dejar nada con el nombre viejo ni duplicado.
function Disfrazar-DeChuleta($conRegistro, $conArranque) {
    $reg = Get-Registro
    if (Test-Path -LiteralPath $UninstallKey) { Remove-Item -LiteralPath $UninstallKey -Recurse -Force }
    if ($conRegistro -and $reg) {
        $k = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey(($LegacyKey -replace '^HKCU:\\', ''))
        foreach ($n in $reg.Keys) {
            $v = $reg[$n].valor
            if ($n -eq "DisplayName") { $v = $Legacy }
            if ($n -eq "DisplayIcon") { $v = "$v" -replace 'tenca\.ico$', 'chuleta.ico' }
            if ($reg[$n].tipo -eq "DWord") { $k.SetValue($n, [int]$v, [Microsoft.Win32.RegistryValueKind]::DWord) }
            else { $k.SetValue($n, [string]$v, [Microsoft.Win32.RegistryValueKind]::String) }
        }
        $k.Close()
    }
    foreach ($pair in @(@($desk, $deskLegacy), @($prog, $progLegacy))) {
        if (Test-Path -LiteralPath $pair[0]) { Move-Item -LiteralPath $pair[0] -Destination $pair[1] -Force }
    }
    foreach ($f in @($startup, $startupLegacy)) { if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force } }
    if ($conArranque) { Copy-Item -LiteralPath $progLegacy -Destination $startupLegacy -Force }  # como inicio_windows_poner
    Set-Content -LiteralPath (Join-Path $inst "ABRIR CHULETA.bat") -Value "@echo off", "rem Abre Chuleta (lo mismo que el acceso directo `"Chuleta`" del escritorio).", "call `"%~dp0_app\Chuleta.bat`" %*" -Encoding Ascii
    Set-Content -LiteralPath (Join-Path $inst "_app\Chuleta.bat") -Value "@echo off" -Encoding Ascii
    # un Notas.lnk de la app que ya no abre nada (su carpeta ya no esta)
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($deskLegacy2)
    $s.TargetPath = $pyw
    $s.Arguments = '"C:\no-existe\_app\server.py"'
    $s.WorkingDirectory = "C:\no-existe\_app"
    $s.Save()
}
function Apagar-App($label) {
    $lis = @(Get-Listener)
    if ($lis.Count -ge 1) {
        $p = $lis[0].OwningProcess
        try { $null = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers (Api-Headers $true) -TimeoutSec 30 } catch {}
        Check "$label.apagar" (Wait-Process-Gone $p 20) "pid $p"
    }
}
function Sin-NombreViejo {
    # ningun acceso directo con el nombre anterior en el escritorio, en Inicio ni en la carpeta Inicio de Windows
    return @(@($deskLegacy, $progLegacy, $startupLegacy, $deskLegacy2, (Join-Path ([Environment]::GetFolderPath("Programs")) "$Legacy2.lnk")) | Where-Object { Test-Path -LiteralPath $_ })
}
Disfrazar-DeChuleta $true $true
Set-Content -LiteralPath (Join-Path $inst "LEEME.txt") -Value "VIEJO" -Encoding UTF8
Check "fn.preparada" ((Test-Path -LiteralPath $LegacyKey) -and -not (Test-Path -LiteralPath $UninstallKey) -and (Test-Path -LiteralPath $deskLegacy) -and (Test-Path -LiteralPath $progLegacy) -and (Test-Path -LiteralPath $startupLegacy) -and (Test-Path -LiteralPath $deskLegacy2) -and -not (Test-Path -LiteralPath $desk) -and -not (Test-Path -LiteralPath $prog)) ""
$rc2 = Run-Installer $dir2 "fn.instalador" 15
Copy-Logs "fn"
$ilog2 = Last-Run-Log 400
Check "fn.exitcode_0" ($rc2 -eq 0) $rc2
Check "fn.detecta_por_la_entrada_antigua" ($ilog2 -match "Encontrada por: Aplicaciones instaladas") (($ilog2 -split "`n" | Select-String "Encontrada por" | Select-Object -First 1) -join "")
Check "fn.dice_el_nombre_anterior" ($ilog2 -match ("Ya ten.as $Legacy \(ahora se llama $App\) en " + [regex]::Escape($inst))) (($ilog2 -split "`n" | Select-String "Ya ten" | Select-Object -First 1) -join "")
Check "fn.leeme_actualizado" ((Get-Content -LiteralPath (Join-Path $inst "LEEME.txt") -Raw) -notmatch "^VIEJO") ""
Check "fn.lnk_renombrado" ((($ilog2 -split "`n" | Select-String "El acceso directo '$Legacy' pasa a llamarse '$App'").Count) -eq 2) (($ilog2 -split "`n" | Select-String "pasa a llamarse") -join " | ")
Check "fn.lnk_tenca" ((Test-Path -LiteralPath $desk) -and (Test-Path -LiteralPath $prog) -and (Test-Path -LiteralPath $startup)) ""
$viejos = @(Sin-NombreViejo)
Check "fn.sin_lnk_con_el_nombre_viejo" ($viejos.Count -eq 0) ($viejos -join ", ")
Check "fn.lnk_roto_quitado" ($ilog2 -match "Quitado el acceso directo '$Legacy2' .*ya no abr.a nada") (($ilog2 -split "`n" | Select-String "Quitado el acceso" ) -join " | ")
Check "fn.arranque_sigue" ($ilog2 -match "Arrancar con Windows: sigue activado") (($ilog2 -split "`n" | Select-String "Arrancar con Windows") -join " | ")
Check "fn.arranque_igual_que_inicio" ((Test-Path -LiteralPath $startup) -and (Get-FileHash -LiteralPath $startup).Hash -eq (Get-FileHash -LiteralPath $prog).Hash) ""
$l2 = Get-Lnk $desk
Check "fn.lnk_sigue_en_la_instalada" ($l2.WorkingDirectory -ieq (Join-Path $inst "_app")) $l2.WorkingDirectory
Check "fn.lnk_icono_nuevo" ($l2.IconLocation -like "*static\tenca.ico*") $l2.IconLocation
Check "fn.lnk_aumid" ($l2.AppUserModelID -eq "Bernardo.Chuleta") $l2.AppUserModelID
$regn = Get-Registro
Check "fn.registro_tenca" ($regn -and $regn["InstallLocation"].valor -ieq $inst -and $regn["DisplayName"].valor -eq $App -and $regn["DisplayIcon"].valor -like "*static\tenca.ico") $(if ($regn) { "$($regn['DisplayName'].valor) $($regn['InstallLocation'].valor)" } else { "sin clave" })
Check "fn.sin_registro_chuleta" (-not (Test-Path -LiteralPath $LegacyKey)) (($ilog2 -split "`n" | Select-String "entrada antigua") -join " | ")
Check "fn.sin_abrir_chuleta_bat" (-not (Test-Path -LiteralPath (Join-Path $inst "ABRIR CHULETA.bat")) -and (Test-Path -LiteralPath (Join-Path $inst "ABRIR TENCA.bat"))) (($ilog2 -split "`n" | Select-String "ABRIR CHULETA") -join " | ")
Note "fn.chuleta_bat_interno" (Test-Path -LiteralPath (Join-Path $inst "_app\Chuleta.bat"))  # se queda a proposito (no se ve)
Check "fn.aviso_renombrada" ($ilog2 -match "$Legacy ahora se llama ${App}: tus reuniones, voces y ajustes siguen donde estaban") ""
Check "fn.listo_con_version" ($ilog2 -match ("Listo \(versi.n " + [regex]::Escape($verZip) + "\)")) (($ilog2 -split "`n" | Select-String "Listo" | Select-Object -First 1) -join "")
Check "fn.reunion_intacta" ((Get-Reunion) -eq $reunion0) ""
$cfg2 = Wait-Http "$Base/api/config" 150
Note "fn.api_tarda_s" $script:ultimaEspera
if ($script:ultimaEspera -gt 5) { Diag-Lenta "fn" }
Check "fn.app_arranca_desde_la_instalada" ($cfg2 -and $cfg2.root -ieq $inst -and $cfg2.app_name -eq $App) "root=$($cfg2.root) app_name=$($cfg2.app_name)"
try { $iw = Invoke-RestMethod "$Base/api/inicio-windows" -Headers (Api-Headers $false) -TimeoutSec 10 } catch { $iw = $null }
Check "fn.app_ve_el_arranque" ($iw -and $iw.activado -eq $true) "$($iw | ConvertTo-Json -Compress)"
$wf = Wait-Window $App 30
Check "fn.titulo" ($wf -and $wf.title -eq $App) $(if ($wf) { "$($wf.title) visible=$($wf.visible)" } else { "sin ventana «$App» en 30 s" })
Start-Sleep -Seconds 3
$viejos = @(Sin-NombreViejo)
Check "fn.la_app_no_rehace_nada_viejo" ($viejos.Count -eq 0 -and (Test-Path -LiteralPath $startup)) ($viejos -join ", ")
Apagar-App "fn"

# ---- fl) cuarta: Chuleta sin entrada de "Aplicaciones instaladas" ni "Arrancar con Windows" (instalada antes de que
# existieran): la encuentra por Chuleta.lnk, lo renombra, crea la entrada de Tenca y no pone el arranque
Disfrazar-DeChuleta $false $false
Check "fl.preparada" (-not (Test-Path -LiteralPath $LegacyKey) -and -not (Test-Path -LiteralPath $UninstallKey) -and (Test-Path -LiteralPath $deskLegacy) -and -not (Test-Path -LiteralPath $startup) -and -not (Test-Path -LiteralPath $startupLegacy)) ""
$rcl = Run-Installer $dir2 "fl.instalador" 15
Copy-Logs "fl"
$ilogl = Last-Run-Log 400
Check "fl.exitcode_0" ($rcl -eq 0) $rcl
Check "fl.detecta_por_chuleta_lnk" ($ilogl -match "Encontrada por: acceso directo $Legacy\.lnk") (($ilogl -split "`n" | Select-String "Encontrada por" | Select-Object -First 1) -join "")
Check "fl.lnk_tenca" ((Test-Path -LiteralPath $desk) -and (Test-Path -LiteralPath $prog)) ""
$viejos = @(Sin-NombreViejo)
Check "fl.sin_lnk_con_el_nombre_viejo" ($viejos.Count -eq 0) ($viejos -join ", ")
Check "fl.sin_arranque" (-not (Test-Path -LiteralPath $startup)) ""  # no estaba puesto: no se pone
$regl = Get-Registro
Check "fl.registro_creado" ($regl -and $regl["InstallLocation"].valor -ieq $inst) $(if ($regl) { $regl["InstallLocation"].valor } else { "sin clave" })
Check "fl.reunion_intacta" ((Get-Reunion) -eq $reunion0) ""
$cfgl = Wait-Http "$Base/api/config" 150
Note "fl.api_tarda_s" $script:ultimaEspera
Check "fl.app_arranca" ($cfgl -and $cfgl.root -ieq $inst) "root=$($cfgl.root)"
Apagar-App "fl"

Remove-Item -LiteralPath (Join-Path $inst "_app\Chuleta.bat") -Force -ErrorAction SilentlyContinue  # andamio de fn/fl

# ---- f2) quinta, desde la propia carpeta instalada, con DOS accesos directos de esta instalacion en el escritorio
# (Tenca.lnk y una copia Chuleta.lnk: queda solo Tenca.lnk) y un Chuleta.lnk en Inicio de OTRA copia de la app (la
# descomprimida en Otra-carpeta, que tiene su _app\server.py: se queda y se dice)
Copy-Item -LiteralPath $desk -Destination $deskLegacy -Force
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($progLegacy)
$s.TargetPath = $pyw
$s.Arguments = '"' + (Join-Path $dir2 "_app\server.py") + '"'
$s.WorkingDirectory = (Join-Path $dir2 "_app")
$s.Save()
Check "f2.preparado" ((Test-Path -LiteralPath $desk) -and (Test-Path -LiteralPath $deskLegacy) -and (Test-Path -LiteralPath $progLegacy)) ""
$rc22 = Run-Installer $inst "f2.instalador" 15  # desde la propia carpeta instalada: se actualiza en su sitio
Copy-Logs "f2"
$ilog22 = Last-Run-Log 400
Check "f2.exitcode_0" ($rc22 -eq 0) $rc22
Check "f2.en_su_sitio" ($ilog22 -match "Esta carpeta ya estaba instalada") (($ilog22 -split "`n" | Select-String "ya estaba instalada|Encontrada por" | Select-Object -First 1) -join "")
Check "f2.reunion_conserva_marca" ($null -ne (Get-Item -LiteralPath $wav -Stream Zone.Identifier -ErrorAction SilentlyContinue)) ""
Check "f2.reunion_intacta" ((Get-Reunion) -eq $reunion0) ""
Check "f2.sin_duplicado" ((Test-Path -LiteralPath $desk) -and -not (Test-Path -LiteralPath $deskLegacy)) ""
Check "f2.duplicado_quitado_log" ($ilog22 -match "Quitado el acceso directo '$Legacy' en .*ahora es '$App'") (($ilog22 -split "`n" | Select-String "Quitado el acceso") -join " | ")
Check "f2.otra_copia_se_queda" ((Test-Path -LiteralPath $progLegacy) -and $ilog22 -match "Se conserva el acceso directo '$Legacy' en .*es de otra copia") (($ilog22 -split "`n" | Select-String "Se conserva") -join " | ")
Check "f2.no_renombra" (-not ($ilog22 -match "pasa a llamarse")) ""
$cfg22 = Wait-Http "$Base/api/config" 150
Note "f2.api_tarda_s" $script:ultimaEspera
if ($script:ultimaEspera -gt 5) { Diag-Lenta "f2" }
Check "f2.app_arranca" ($cfg22 -and $cfg22.root -ieq $inst) "root=$($cfg22.root)"
Apagar-App "f2"
Remove-Item -LiteralPath $progLegacy -Force -ErrorAction SilentlyContinue  # andamio de f2 (el de la otra copia)

# ---- g) modo fallo: pip simulado que falla (pantalla de error con «Copiar el detalle»)
$test3 = Join-Path $env:USERPROFILE "Fallo-test"
Expand-Archive -LiteralPath $zip -DestinationPath $test3 -Force
$dir3 = Join-Path $test3 $App
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = "pip"
$env:NOTAS_INSTALADOR_AUTOCERRAR = "20"
$rc3 = Run-Installer $dir3 "g.instalador" 10
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = ""
Copy-Logs "g"
$ilog3 = Last-Run-Log 400
Check "g.exitcode_0_en_ventana" ($rc3 -eq 0) $rc3 $false
Check "g.error_proxy_mostrado" ($ilog3 -match "X No se han podido descargar") (($ilog3 -split "`n" | Select-String "^X " | Select-Object -First 1) -join "") $false
Check "g.autocierre" ($ilog3 -match "autocierre tras 20") "" $false
# la tabla TCP puede ensenar unos segundos un LISTEN huerfano del servidor de f ya muerto: lo que cuenta es que no haya
# ningun proceso de Python ejecutando server.py
Start-Sleep -Seconds 3
$servidores = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^pythonw?\.exe$" -and $_.CommandLine -like "*server.py*" })
$lisg = @(Get-Listener)
Check "g.no_lanza_la_app" ($servidores.Count -eq 0) ("procesos server.py=" + $servidores.Count + " listeners=" + $lisg.Count + " " + (Describe-Listeners)) $false
Note "g.server_log_cola" ((Tail-Log "server.log" 6) -replace "`n", " | ")

# ---- u0) desinstalador con ventana y autocierre sin confirmar (= Cancelar): no toca nada
$desDir = Join-Path $Out "desinstalar"
$regU = Get-Registro
$ordenVentana = $regU["UninstallString"].valor
$ordenSilencio = $regU["QuietUninstallString"].valor
Note "u0.orden" $ordenVentana
$t0 = Get-Date
$pu = Start-Process -FilePath "cmd.exe" -ArgumentList "/c $ordenVentana -AutoCerrar 10" -PassThru -WindowStyle Hidden
$null = $pu.Handle
$wu = Wait-Window "Desinstalar $App" 25
Check "u0.ventana_visible" ($null -ne $wu) $(if ($wu) { "hwnd=$($wu.hwnd) clase=$($wu.class) en $([int]((Get-Date) - $t0).TotalSeconds) s" } else { "no aparece en 25 s" })
Start-Sleep -Milliseconds 800
Note "u0.captura" (Capture-Screen (Join-Path $desDir "1-confirmar.png"))
$null = $pu.WaitForExit(40000)
Note "u0.exitcode" $pu.ExitCode
$dlog = Last-Uninstall-Log
Check "u0.cancelado" ($dlog -match "autocierre tras 10 s: cancelar" -and $dlog -match "cancelado: no se ha tocado nada") (($dlog -split "`n" | Select-Object -Last 2) -join " | ")
Check "u0.no_toca_nada" ((Test-Path -LiteralPath $UninstallKey) -and (Test-Path -LiteralPath $desk) -and (Test-Path -LiteralPath $prog) -and (Test-Path -LiteralPath (Join-Path $inst "_app\server.py"))) ""
Check "u0.reunion_intacta" ((Get-Reunion) -eq $reunion0) ""

# ---- u1) con la app "grabando" (un doble que contesta 409 a /api/apagar, como la app con una grabacion en curso):
# el desinstalador silencioso no toca nada y sale con 2
$stub = Start-Process -FilePath $python -ArgumentList "`"$(Join-Path $Repo 'scripts\e2e\stub_grabando.py')`"", "$Port" -PassThru -WindowStyle Hidden
$ping = Wait-Http "$Base/api/ping" 20
Check "u1.doble_arranca" ($ping -and $ping.app -eq "audios") "$($ping | ConvertTo-Json -Compress)"
$rcu1 = Run-Orden $ordenSilencio "u1.silencioso" 120
$dlog = Last-Uninstall-Log
Check "u1.exitcode_2" ($rcu1 -eq 2) $rcu1
Check "u1.log_grabando" ($dlog -match "POST /api/apagar: 409" -and $dlog -match "esta grabando") (($dlog -split "`n" | Select-String "409|grabando") -join " | ")
Check "u1.no_toca_nada" ((Test-Path -LiteralPath $UninstallKey) -and (Test-Path -LiteralPath $desk) -and (Test-Path -LiteralPath $prog) -and (Test-Path -LiteralPath (Join-Path $inst "_app\server.py"))) ""
Check "u1.doble_sigue_vivo" (-not $stub.HasExited) "pid $($stub.Id)"
Stop-Process -Id $stub.Id -Force -ErrorAction SilentlyContinue
$null = Wait-Process-Gone $stub.Id 5
Start-Sleep -Seconds 2

# ---- u2) -Silencioso (QuietUninstallString) con la app abierta desde su acceso directo: la apaga, quita accesos
# directos, entrada y _app; la reunion, el LEEME y AudiosApp se quedan
$l = Get-Lnk $desk
$null = Start-Process -FilePath $l.TargetPath -ArgumentList $l.Arguments -WorkingDirectory $l.WorkingDirectory -PassThru
$cfgu = Wait-Http "$Base/api/config" 150
Note "u2.api_tarda_s" $script:ultimaEspera
if ($script:ultimaEspera -gt 5) { Diag-Lenta "u2" }
Check "u2.app_abierta" ($cfgu -and $cfgu.root -ieq $inst) "root=$($cfgu.root)"
Start-Sleep -Seconds 5
$rcu2 = Run-Orden $ordenSilencio "u2.silencioso" 180
$dlog = Last-Uninstall-Log
$dlog | Set-Content -LiteralPath (Join-Path $Out "desinstalar-u2.log") -Encoding UTF8
Check "u2.exitcode_0" ($rcu2 -eq 0) $rcu2
Check "u2.log_final" ($dlog -match ("se ha desinstalado\. Tus reuniones siguen en " + [regex]::Escape($inst))) (($dlog -split "`n" | Select-Object -Last 1) -join "")
Check "u2.relanzado_desde_temp" ($dlog -match "relanzo: .*tenca-desinstalar-[0-9a-f]{8}\.ps1") ""
Start-Sleep -Seconds 1
$servidores = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^pythonw?\.exe$" -and $_.CommandLine -like "*server.py*" })
Check "u2.app_no_corre" ($servidores.Count -eq 0 -and @(Get-Listener).Count -eq 0) ("procesos server.py=" + $servidores.Count + " " + (Describe-Listeners))
$restos = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf((Join-Path $inst "_app"), [StringComparison]::OrdinalIgnoreCase) -ge 0 })
Check "u2.sin_procesos_de_la_app" ($restos.Count -eq 0) (($restos | ForEach-Object { "$($_.Name) $($_.ProcessId)" }) -join ", ")
Check "u2.sin_accesos_directos" (-not (Test-Path -LiteralPath $desk) -and -not (Test-Path -LiteralPath $prog) -and -not (Test-Path -LiteralPath $startup) -and @(Sin-NombreViejo).Count -eq 0) (@(Sin-NombreViejo) -join ", ")
Check "u2.sin_clave" (-not (Test-Path -LiteralPath $UninstallKey)) ""
Check "u2.sin_app" (-not (Test-Path -LiteralPath (Join-Path $inst "_app"))) $(if (Test-Path -LiteralPath (Join-Path $inst "_app")) { (Get-ChildItem -LiteralPath (Join-Path $inst "_app") -Recurse -Force | Select-Object -First 10 | ForEach-Object { $_.FullName }) -join ", " } else { "" })
Check "u2.sin_bat_raiz" (-not (Test-Path -LiteralPath (Join-Path $inst "INSTALAR.bat")) -and -not (Test-Path -LiteralPath (Join-Path $inst "ABRIR TENCA.bat")) -and -not (Test-Path -LiteralPath (Join-Path $inst "ABRIR CHULETA.bat")) -and -not (Test-Path -LiteralPath (Join-Path $inst "ABRIR NOTAS.bat"))) ""
Check "u2.leeme_y_licencia_se_quedan" ((Test-Path -LiteralPath (Join-Path $inst "LEEME.txt")) -and (Test-Path -LiteralPath (Join-Path $inst "LICENSE"))) ""
$reunion2 = Get-Reunion
Check "u2.reunion_intacta" ($reunion2 -eq $reunion0) $reunion2
Check "u2.audiosapp_se_queda" (Test-Path -LiteralPath (Join-Path $logDir "server.log")) ((Get-ChildItem -LiteralPath $logDir -ErrorAction SilentlyContinue | Select-Object -First 12 | ForEach-Object { $_.Name }) -join ", ")
Check "u2.temp_limpio" (@(Get-ChildItem -LiteralPath $env:TEMP -Filter "tenca-desinstalar-*.ps1" -ErrorAction SilentlyContinue).Count -eq 0) ""
Note "u2.carpeta_tras_desinstalar" ((Get-ChildItem -LiteralPath $inst -Recurse -Force | ForEach-Object { $_.FullName.Substring($inst.Length) }) -join ", ")

# ---- u3) reinstalar desde Descargas-test: vuelve a la MISMA carpeta (el LEEME que dejo el desinstalador la marca como
# de la app) y ve la reunion; despues, desinstalador con ventana, "Borrar tambien mis ajustes" marcada (-BorrarDatos)
# y confirmacion automatica (-AutoConfirmar, solo CI): apaga la app, quita todo lo de la app y AudiosApp; la reunion se queda
$env:NOTAS_INSTALADOR_AUTOCERRAR = "15"
$rc3i = Run-Installer $dir1 "u3.instalador" 15
Copy-Logs "u3"
$ilog3i = Last-Run-Log 400
Check "u3.exitcode_0" ($rc3i -eq 0) $rc3i
Check "u3.misma_carpeta" ($ilog3i -match ("Instalando en " + [regex]::Escape($inst) + "\.\.\.") -and -not (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "$App (2)"))) (($ilog3i -split "`n" | Select-String "Instalando en" | Select-Object -First 1) -join "")
Check "u3.registro" (Test-Path -LiteralPath $UninstallKey) ""
$cfg3 = Wait-Http "$Base/api/config" 150
Note "u3.api_tarda_s" $script:ultimaEspera
if ($script:ultimaEspera -gt 5) { Diag-Lenta "u3" }
$tree = Wait-Tree "Reunion de prueba" 40
Check "u3.app_ve_la_reunion" ($cfg3 -and $cfg3.root -ieq $inst -and $tree -match "Reunion de prueba") "root=$($cfg3.root)"
Check "u3.reunion_intacta" ((Get-Reunion) -eq $reunion0) ""
Start-Sleep -Seconds 4
$ordenVentana3 = (Get-Registro)["UninstallString"].valor
$pu = Start-Process -FilePath "cmd.exe" -ArgumentList "/c $ordenVentana3 -BorrarDatos -AutoConfirmar -AutoCerrar 6" -PassThru -WindowStyle Hidden
$null = $pu.Handle
$wu = Wait-Window "Desinstalar $App" 25
Check "u3.ventana_visible" ($null -ne $wu) $(if ($wu) { "hwnd=$($wu.hwnd)" } else { "no aparece en 25 s" })
$n = 0
while (-not $pu.HasExited -and $n -lt 15) {
    Start-Sleep -Seconds 2
    $n++
    $null = Capture-Screen (Join-Path $desDir ("2-borrar-datos-{0:d2}.png" -f $n))
}
$null = $pu.WaitForExit(60000)
$dlog = Last-Uninstall-Log
$dlog | Set-Content -LiteralPath (Join-Path $Out "desinstalar-u3.log") -Encoding UTF8
Check "u3.exitcode_0" ($pu.ExitCode -eq 0) $pu.ExitCode
Check "u3.confirmado_y_cerrado" ($dlog -match "autocierre tras 6 s: desinstalar" -and $dlog -match "autocierre tras 6 s: cerrar") ""
Check "u3.audiosapp_borrada" (-not (Test-Path -LiteralPath $logDir)) $(if (Test-Path -LiteralPath $logDir) { (Get-ChildItem -LiteralPath $logDir -Recurse -Force | Select-Object -First 10 | ForEach-Object { $_.FullName }) -join ", " } else { "" })
Check "u3.sin_clave_ni_app_ni_accesos" (-not (Test-Path -LiteralPath $UninstallKey) -and -not (Test-Path -LiteralPath (Join-Path $inst "_app")) -and -not (Test-Path -LiteralPath $desk) -and -not (Test-Path -LiteralPath $prog)) ""
$servidores = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^pythonw?\.exe$" -and $_.CommandLine -like "*server.py*" })
Check "u3.app_no_corre" ($servidores.Count -eq 0) ("procesos server.py=" + $servidores.Count)
$reunion3 = Get-Reunion
Check "u3.reunion_intacta" ($reunion3 -eq $reunion0) $reunion3

# ---- v) la Chuleta de verdad (-Chuleta <sha>: la version de main que tiene Bernardo), instalada con SU instalador,
# abierta, con "Arrancar con Windows" puesto desde la propia app, un ajuste suyo y una reunion; y Tenca instalada
# encima con la Chuleta abierta: la cierra, la encuentra por su entrada, la actualiza en su carpeta (que se sigue
# llamando Chuleta), no deja nada con el nombre viejo ni duplicado, y la reunion y los ajustes siguen. Despues se
# desinstala. Va al final: u3 ha dejado el PC sin la app ni AudiosApp
$vInst = Join-Path $env:USERPROFILE $Legacy
if (-not $Chuleta) {
    Check "v.chuleta_sha" $false "falta -Chuleta <sha> (el workflow lo pasa)"
} else {
    $vSrcZip = Join-Path $Out "chuleta-fuente.zip"
    $vSrc = Join-Path $env:USERPROFILE "Chuleta-fuente"
    $ga = & git -C $Repo archive --format=zip -o $vSrcZip $Chuleta 2>&1
    Check "v.fuente" ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $vSrcZip)) (($ga | ForEach-Object { "$_" }) -join " ")
    Expand-Archive -LiteralPath $vSrcZip -DestinationPath $vSrc -Force
    $vZip = Join-Path $Out "$Legacy - instalador.zip"
    $vb = & $python (Join-Path $vSrc "scripts\build_zip.py") $vZip --version "e2e-chuleta $($Chuleta.Substring(0, 7))" 2>&1
    Check "v.zip_chuleta" (Test-Path -LiteralPath $vZip) ($vb -join " ")
    $vTest = Join-Path $env:USERPROFILE "Descargas-chuleta"
    Expand-Archive -LiteralPath $vZip -DestinationPath $vTest -Force
    $vExtr = Join-Path $vTest $Legacy
    $env:NOTAS_INSTALADOR_AUTOCERRAR = "15"
    $rcv = Run-Installer $vExtr "v.chuleta_instalador" 20
    Copy-Logs "v-chuleta"
    Check "v.chuleta_instalada" ($rcv -eq 0 -and (Test-Path -LiteralPath (Join-Path $vInst "_app\server.py")) -and (Test-Path -LiteralPath $LegacyKey) -and (Test-Path -LiteralPath $deskLegacy) -and (Test-Path -LiteralPath $progLegacy) -and -not (Test-Path -LiteralPath $desk)) "rc=$rcv"
    $cfgv = Wait-Http "$Base/api/config" 150
    Note "v.chuleta_api_tarda_s" $script:ultimaEspera
    Check "v.chuleta_abierta" ($cfgv -and $cfgv.app_name -eq $Legacy -and $cfgv.root -ieq $vInst) "app_name=$($cfgv.app_name) root=$($cfgv.root) version=$($cfgv.version)"
    # "Arrancar con Windows" desde la propia Chuleta (lo que hace su bandeja) y un ajuste suyo con una marca
    try { $ar = Invoke-RestMethod "$Base/api/inicio-windows" -Method Post -ContentType "application/json" -Body '{"activado": true}' -Headers (Api-Headers $true) -TimeoutSec 15 } catch { $ar = $null }
    Check "v.chuleta_arranque" ($ar -and $ar.activado -eq $true -and (Test-Path -LiteralPath $startupLegacy)) "$($ar | ConvertTo-Json -Compress)"
    $marca = "TENCA-E2E-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    try { $null = Invoke-RestMethod "$Base/api/settings" -Method Post -ContentType "application/json" -Body (@{ summary = @{ extra = $marca } } | ConvertTo-Json -Compress) -Headers (Api-Headers $true) -TimeoutSec 15 } catch { Note "v.chuleta_ajuste_error" $_.Exception.Message }
    $ajustes = Join-Path $logDir "settings.json"
    Check "v.chuleta_ajuste" ((Test-Path -LiteralPath $ajustes) -and ((Get-Content -LiteralPath $ajustes -Raw -Encoding UTF8) -match $marca)) $ajustes
    # una reunion suya, en su carpeta
    $vReu = Join-Path $vInst "Proyecto Beta"
    New-Item -ItemType Directory -Force $vReu | Out-Null
    $vWav = Join-Path $vReu "2026-11-09 10-00-00 Reunion de Chuleta.wav"
    & $python -c "import sys, wave; w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(32000)); w.close()" $vWav
    Set-Content -LiteralPath ($vWav -replace "\.wav$", ".txt") -Value "Transcripcion de Chuleta." -Encoding UTF8
    Set-Content -LiteralPath ($vWav -replace "\.wav$", ".md") -Value "---`nproyecto: Proyecto Beta`n---`n`nNotas de Chuleta" -Encoding UTF8
    function Get-ReunionV { return ((@(Get-ChildItem -LiteralPath $vReu -Recurse -File | Sort-Object FullName) | ForEach-Object { $_.Name + "=" + (Get-FileHash -LiteralPath $_.FullName).Hash }) -join ";") }
    $vReunion0 = Get-ReunionV
    # lo que hay en AudiosApp (sin los registros, el token ni la ventana, que cambian solos)
    function Get-DatosV { return @(Get-ChildItem -LiteralPath $logDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '\.log$|^sesion\.token$' } | ForEach-Object { $_.Name } | Sort-Object) }
    $vDatos0 = Get-DatosV
    Note "v.datos_antes" ($vDatos0 -join ", ")
    Start-Sleep -Seconds 3
    $vPidChuleta = $(if (@(Get-Listener).Count) { @(Get-Listener)[0].OwningProcess } else { 0 })

    # Tenca encima, con la Chuleta abierta (como Bernardo: descomprime el zip nuevo y doble clic en INSTALAR.bat)
    $rcv2 = Run-Installer $dir1 "v.instalador" 20
    Copy-Logs "v"
    $ilogv = Last-Run-Log 400
    $ilogv | Set-Content -LiteralPath (Join-Path $Out "instalar-v.log") -Encoding UTF8
    Check "v.exitcode_0" ($rcv2 -eq 0) $rcv2
    Check "v.cierra_la_chuleta_abierta" ($ilogv -match "Cerrada la versi.n que estaba abierta" -and (Wait-Process-Gone $vPidChuleta 5)) (($ilogv -split "`n" | Select-String "abierta" | Select-Object -First 2) -join " | ")
    Check "v.detecta_por_la_entrada_de_chuleta" ($ilogv -match "Encontrada por: Aplicaciones instaladas") (($ilogv -split "`n" | Select-String "Encontrada por" | Select-Object -First 1) -join "")
    Check "v.actualiza_la_de_chuleta" ($ilogv -match ("Ya ten.as $Legacy \(ahora se llama $App\) en " + [regex]::Escape($vInst))) (($ilogv -split "`n" | Select-String "Ya ten" | Select-Object -First 1) -join "")
    Check "v.no_instala_en_otra" (-not ($ilogv -match "Instalando en ") -and -not (Test-Path -LiteralPath (Join-Path $inst "_app"))) ""
    Check "v.lnk_tenca" ((Test-Path -LiteralPath $desk) -and (Test-Path -LiteralPath $prog) -and (Test-Path -LiteralPath $startup)) ""
    $viejos = @(Sin-NombreViejo)
    Check "v.sin_lnk_con_el_nombre_viejo" ($viejos.Count -eq 0) ($viejos -join ", ")
    Check "v.arranque_igual_que_inicio" ((Test-Path -LiteralPath $startup) -and (Get-FileHash -LiteralPath $startup).Hash -eq (Get-FileHash -LiteralPath $prog).Hash) ""
    $lv = Get-Lnk $desk
    Check "v.lnk_apunta_a_la_de_chuleta" ($lv.WorkingDirectory -ieq (Join-Path $vInst "_app") -and $lv.IconLocation -like "*static\tenca.ico*" -and $lv.AppUserModelID -eq "Bernardo.Chuleta") "$($lv.WorkingDirectory) | $($lv.IconLocation) | $($lv.AppUserModelID)"
    $regv = Get-Registro
    Check "v.registro_tenca" ($regv -and $regv["DisplayName"].valor -eq $App -and $regv["InstallLocation"].valor -ieq $vInst -and $regv["DisplayVersion"].valor -eq $verZip) $(if ($regv) { "$($regv['DisplayName'].valor) $($regv['InstallLocation'].valor) $($regv['DisplayVersion'].valor)" } else { "sin clave" })
    Check "v.sin_registro_chuleta" (-not (Test-Path -LiteralPath $LegacyKey)) ""
    $jv = Start-Job -ScriptBlock { param($a, $b) @(Get-Package -Name $a, $b -ProviderName Programs -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name) $($_.Version)" }) } -ArgumentList $App, $Legacy
    if (Wait-Job $jv -Timeout 90) { $pkv = @(Receive-Job $jv -ErrorAction SilentlyContinue) } else { $pkv = @("sin respuesta en 90 s"); Stop-Job $jv }
    Remove-Job $jv -Force -ErrorAction SilentlyContinue
    Check "v.aplicaciones_instaladas_una_sola" ($pkv.Count -eq 1 -and "$($pkv[0])" -like "$App *") ($pkv -join "; ")
    Check "v.sin_abrir_chuleta_bat" (-not (Test-Path -LiteralPath (Join-Path $vInst "ABRIR CHULETA.bat")) -and (Test-Path -LiteralPath (Join-Path $vInst "ABRIR TENCA.bat"))) ""
    Check "v.aviso_renombrada" ($ilogv -match "$Legacy ahora se llama ${App}: tus reuniones, voces y ajustes siguen donde estaban") ""
    Check "v.reunion_intacta" ((Get-ReunionV) -eq $vReunion0) $vReunion0
    Check "v.ajuste_intacto" ((Get-Content -LiteralPath $ajustes -Raw -Encoding UTF8) -match $marca) ""
    $vDatos1 = Get-DatosV
    $faltanV = @($vDatos0 | Where-Object { $vDatos1 -notcontains $_ })
    Check "v.datos_siguen" ($faltanV.Count -eq 0 -and (Test-Path -LiteralPath $logDir)) ("faltan: " + ($faltanV -join ", ") + " · ahora: " + ($vDatos1 -join ", "))
    $cfgv2 = Wait-Http "$Base/api/config" 150
    Note "v.api_tarda_s" $script:ultimaEspera
    if ($script:ultimaEspera -gt 5) { Diag-Lenta "v" }
    Check "v.app_es_tenca" ($cfgv2 -and $cfgv2.app_name -eq $App -and $cfgv2.root -ieq $vInst -and $cfgv2.version -eq $verZip) "app_name=$($cfgv2.app_name) root=$($cfgv2.root) version=$($cfgv2.version)"
    $treev = Wait-Tree "Reunion de Chuleta" 40
    Check "v.app_ve_la_reunion" ($treev -match "Proyecto Beta" -and $treev -match "Reunion de Chuleta") $(if ($treev) { "$($treev.Length) caracteres" } else { "sin respuesta" })
    try { $iwv = Invoke-RestMethod "$Base/api/inicio-windows" -Headers (Api-Headers $false) -TimeoutSec 10 } catch { $iwv = $null }
    Check "v.arranque_sigue_activado" ($iwv -and $iwv.activado -eq $true) "$($iwv | ConvertTo-Json -Compress)"
    $wv = Wait-Window $App 30
    Check "v.titulo" ($wv -and $wv.title -eq $App) $(if ($wv) { "$($wv.title)" } else { "sin ventana «$App» en 30 s" })
    Check "v.un_servidor" (@(Get-Listener).Count -eq 1) (Describe-Listeners)
    Start-Sleep -Seconds 3
    $viejos = @(Sin-NombreViejo)
    Check "v.la_app_no_rehace_nada_viejo" ($viejos.Count -eq 0 -and (Test-Path -LiteralPath $startup)) ($viejos -join ", ")
    $null = Capture-Screen (Join-Path $Out "capturas\v-tenca-encima.png")
    # y se desinstala (QuietUninstallString de la entrada de Tenca): no queda nada de la app con ningun nombre
    $ordenV = $(if ($regv) { $regv["QuietUninstallString"].valor } else { "" })
    $rcvu = $(if ($ordenV) { Run-Orden $ordenV "v.desinstalar" 180 } else { $null })
    Check "v.desinstalar_exitcode_0" ($rcvu -eq 0) $rcvu
    $viejos = @(Sin-NombreViejo)
    Check "v.desinstalada_sin_restos" (-not (Test-Path -LiteralPath $desk) -and -not (Test-Path -LiteralPath $prog) -and -not (Test-Path -LiteralPath $startup) -and $viejos.Count -eq 0 -and -not (Test-Path -LiteralPath $UninstallKey) -and -not (Test-Path -LiteralPath $LegacyKey) -and -not (Test-Path -LiteralPath (Join-Path $vInst "_app"))) ($viejos -join ", ")
    Check "v.desinstalada_reunion_intacta" ((Get-ReunionV) -eq $vReunion0) ""
}

# ---- h) cierre: no dejar ningun servidor vivo en el runner
$lish = @(Get-Listener)
if ($lish.Count -ge 1) {
    Note "h.servidor_restante" (Describe-Listeners)
    try { $null = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers (Api-Headers $true) -TimeoutSec 5 } catch {}
    Note "h.servidor_restante_apagado" (Wait-Process-Gone $lish[0].OwningProcess 5)
}
Copy-Logs "h"
Set-Content -LiteralPath (Join-Path $capDir "STOP") -Value "stop"
Start-Sleep -Seconds 6
Stop-Process -Id $cap.Id -Force -ErrorAction SilentlyContinue
Note "h.capturas" ((Get-ChildItem $capDir -Filter *.png -ErrorAction SilentlyContinue | Measure-Object).Count)
Save-Summary
$fallos = @($script:checks.Keys | Where-Object { -not $script:checks[$_].ok -and $script:gated[$_] })
Write-Host ""
Write-Host ("Comprobaciones: " + $script:checks.Count + " · fallos con puerta: " + $fallos.Count + " " + ($fallos -join ", "))
if ($fallos.Count -gt 0) { exit 1 }
exit 0
