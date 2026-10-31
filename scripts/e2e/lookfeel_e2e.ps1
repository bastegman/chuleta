# E2E del instalador y del arranque en un runner windows-latest (PowerShell 5.1, el mismo que lanza INSTALAR.bat).
# Lo mismo que hara una persona: zip -> descomprimir -> INSTALAR.bat (modo ventana, con autocierre) -> la app abre su
# ventana -> acceso directo otra vez -> Apagar; despues una segunda instalacion desde otra carpeta (actualiza la
# primera) y una instalacion que falla a proposito (pantalla de error). Capturas de pantalla cada 5 s en paralelo.
# Deja en -Out: capturas/, server.log, instalar.log, iconos del HWND y resumen.json (comprobacion -> ok/valor).
# Sale con 1 si falla alguna comprobacion de las fases d-f.
param([string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)), [string]$Out = "")

$ErrorActionPreference = "Continue"
if (-not $Out) { $Out = Join-Path $Repo "e2e-out" }
New-Item -ItemType Directory -Force $Out | Out-Null
$script:checks = New-Object System.Collections.Specialized.OrderedDictionary
$script:gated = @{}
$Port = 8765
$Base = "http://127.0.0.1:$Port"
$python = (Get-Command python).Source
$logDir = Join-Path $env:LOCALAPPDATA "AudiosApp"

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
function Wait-Http($url, $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        try { return Invoke-RestMethod $url -TimeoutSec 3 } catch { Start-Sleep -Milliseconds 500 }
    }
    return $null
}
function Get-Listener { try { Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop } catch { $null } }
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
function Ventana($cmd, $tag) {
    $argv = @((Join-Path $Repo "scripts\e2e\ventana.py"), $cmd, "Notas")
    if ($cmd -eq "icons") { $argv += @((Join-Path $Out "iconos"), $tag) }
    $raw = & $python @argv 2>&1
    try { return ($raw | Out-String | ConvertFrom-Json) } catch { return @{ hwnd = $null; error = ($raw | Out-String) } }
}
function Tail-Log($name, $n = 40) {
    $f = Join-Path $logDir $name
    if (Test-Path -LiteralPath $f) { return ((Get-Content -LiteralPath $f -Tail $n) -join "`n") }
    return "(no existe $f)"
}
function Copy-Logs($suffix) {
    foreach ($n in "server.log", "instalar.log") {
        $f = Join-Path $logDir $n
        if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination (Join-Path $Out ($n -replace "\.log$", "-$suffix.log")) -Force }
    }
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
$zip = Join-Path $Out "Notas - instalador.zip"
& $python (Join-Path $Repo "scripts\build_zip.py") $zip
Check "a.zip" (Test-Path -LiteralPath $zip) $zip
$test1 = Join-Path $env:USERPROFILE "Descargas-test"
Expand-Archive -LiteralPath $zip -DestinationPath $test1 -Force
$dir1 = Join-Path $test1 "Notas"
Check "a.extraido" (Test-Path -LiteralPath (Join-Path $dir1 "INSTALAR.bat")) $dir1

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
$rc = Run-Installer $dir1 "c.instalador" 15
Copy-Logs "c"
$ilog = Tail-Log "instalar.log" 400
Check "c.exitcode_0" ($rc -eq 0) $rc
Check "c.modo_ventana" ($ilog -match "modo: ventana") (($ilog -split "`n" | Select-String "modo:") -join "; ")
Check "c.sin_winget" (-not ($ilog -match "winget install")) ($ilog -match "No hay Python")
Check "c.listo" ($ilog -match "Listo") (($ilog -split "`n" | Select-String "Listo" | Select-Object -First 1) -join "")
Check "c.autocierre" ($ilog -match "autocierre tras") ""

# ---- d) accesos directos, python.txt, librerias, zombis
$desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Notas.lnk"
$prog = Join-Path ([Environment]::GetFolderPath("Programs")) "Notas.lnk"
$pyTxt = Join-Path $dir1 "_app\python.txt"
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
    Check "d.lnk.$label.workdir" ($l.WorkingDirectory -ieq (Join-Path $dir1 "_app")) $l.WorkingDirectory
    Check "d.lnk.$label.icon" ($l.IconLocation -like "*static\notas.ico*") $l.IconLocation
    Check "d.lnk.$label.aumid" ($l.AppUserModelID -eq "Bernardo.Chuleta") $l.AppUserModelID
}
$imp = & $pyApp -c "import webview, faster_whisper, soundcard, requests, truststore; from importlib.metadata import version; print('pywebview', version('pywebview'), 'faster-whisper', faster_whisper.__version__)" 2>&1
Check "d.import_librerias" ($LASTEXITCODE -eq 0) ($imp -join " ")
$zombis = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*instalar.ps1*" -or $_.CommandLine -like "*INSTALAR.bat*" })
Check "d.sin_zombis" ($zombis.Count -eq 0) (($zombis | ForEach-Object { "$($_.Name) $($_.ProcessId)" }) -join ", ")

# ---- e) la app lanzada por el instalador
$cfg = Wait-Http "$Base/api/config" 90
Check "e.api_config" ($null -ne $cfg) $(if ($cfg) { "app_name=$($cfg.app_name) window=$($cfg.window) version=$($cfg.version)" } else { "sin respuesta en 90 s" })
Check "e.config_window_true" ($cfg -and $cfg.window -eq $true) "$($cfg.window)"
Start-Sleep -Seconds 6  # que la ventana termine de pintar (y una captura mas)
$lis = @(Get-Listener)
Check "e.un_servidor" ($lis.Count -eq 1) ("listeners en ${Port}: " + $lis.Count)
$appPid = $(if ($lis.Count -ge 1) { $lis[0].OwningProcess } else { 0 })
$proc = $(if ($appPid) { Get-Process -Id $appPid -ErrorAction SilentlyContinue } else { $null })
Check "e.proceso_pythonw" ($proc -and $proc.Name -ieq "pythonw") $(if ($proc) { "$($proc.Name) pid $appPid" } else { "sin proceso" })
$w = Ventana "icons" "app"
Check "e.ventana_visible" ($w.hwnd -and $w.visible) ("hwnd=$($w.hwnd) clase=$($w.class) visible=$($w.visible) pid=$($w.pid)")
Check "e.titulo" ($w.title -eq "Notas") "$($w.title)"
Check "e.pid_ventana_es_el_servidor" ($w.pid -eq $appPid) "$($w.pid) vs $appPid"
Note "e.iconos_hwnd" ($(if ($w.icons) { ($w.icons.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value.px)" }) -join " " } else { "" }))
Check "e.icon_big_256" ($w.icons -and $w.icons.ICON_BIG.px -eq "256x256") "$($w.icons.ICON_BIG.px)"
$slog = Tail-Log "server.log" 200
Check "e.log_aumid" ($slog -match "AppUserModelID Bernardo\.Chuleta: fijado") ""
Check "e.log_ventana" ($slog -match "ventana abierta \(WebView2") (($slog -split "`n" | Select-String "ventana abierta") -join "")
# ocultar (como al pulsar la X y «Entendido») y volver a abrir con el acceso directo
try { $r = Invoke-RestMethod "$Base/api/ocultar" -Method Post -ContentType "application/json" -Body "{}" -Headers @{ "X-Audios" = "1" } -TimeoutSec 5 } catch { $r = $null }
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
try { $r = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers @{ "X-Audios" = "1" } -TimeoutSec 5 } catch { $r = @{ error = $_.Exception.Message } }
$gone = Wait-Process-Gone $appPid 5
Check "e.apagar_responde" ($r -and $r.ok -eq $true) ("$($r | ConvertTo-Json -Compress)")
Check "e.apagar_proceso_muere_5s" $gone "pid $appPid"
Start-Sleep -Seconds 1
$slog = Tail-Log "server.log" 60
Check "e.log_apagado" ($slog -match "(?m)^.*\bapagado$") (($slog -split "`n" | Select-String "apag") -join " | ")
Copy-Logs "e"

# ---- f) segunda instalacion desde otra carpeta: actualiza la primera
$test2 = Join-Path $env:USERPROFILE "Otra-carpeta"
Expand-Archive -LiteralPath $zip -DestinationPath $test2 -Force
$dir2 = Join-Path $test2 "Notas"
Set-Content -LiteralPath (Join-Path $dir1 "LEEME.txt") -Value "VIEJO" -Encoding UTF8  # marca: la actualizacion debe pisarlo
Set-Content -LiteralPath (Join-Path $dir1 "_app\static\marca-vieja.txt") -Value "x" -Encoding UTF8
$rc2 = Run-Installer $dir2 "f.instalador" 15
Copy-Logs "f"
$ilog2 = Tail-Log "instalar.log" 200
Check "f.exitcode_0" ($rc2 -eq 0) $rc2
Check "f.detecta_anterior" ($ilog2 -match "Ya ten") (($ilog2 -split "`n" | Select-String "Ya ten" | Select-Object -First 1) -join "")
Check "f.leeme_actualizado" ((Get-Content -LiteralPath (Join-Path $dir1 "LEEME.txt") -Raw) -notmatch "^VIEJO") ""
Check "f.sin_python_txt_en_nueva" (-not (Test-Path -LiteralPath (Join-Path $dir2 "_app\python.txt"))) ""
Check "f.window_py_copiado" ((Get-FileHash (Join-Path $dir1 "_app\window.py")).Hash -eq (Get-FileHash (Join-Path $dir2 "_app\window.py")).Hash) ""
$l2 = Get-Lnk $desk
Check "f.lnk_sigue_en_la_vieja" ($l2.WorkingDirectory -ieq (Join-Path $dir1 "_app")) $l2.WorkingDirectory
$cfg2 = Wait-Http "$Base/api/config" 90
Check "f.app_arranca_desde_la_vieja" ($cfg2 -and $cfg2.root -ieq $dir1) "root=$($cfg2.root)"
$lisf = @(Get-Listener)
if ($lisf.Count -ge 1) {
    $pidf = $lisf[0].OwningProcess
    try { $null = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers @{ "X-Audios" = "1" } -TimeoutSec 5 } catch {}
    Check "f.apagar" (Wait-Process-Gone $pidf 5) "pid $pidf"
}

# ---- g) modo fallo: pip simulado que falla (pantalla de error con «Copiar el detalle»)
$test3 = Join-Path $env:USERPROFILE "Fallo-test"
Expand-Archive -LiteralPath $zip -DestinationPath $test3 -Force
$dir3 = Join-Path $test3 "Notas"
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = "pip"
$env:NOTAS_INSTALADOR_AUTOCERRAR = "20"
$rc3 = Run-Installer $dir3 "g.instalador" 10
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = ""
Copy-Logs "g"
$ilog3 = Tail-Log "instalar.log" 120
Check "g.exitcode_0_en_ventana" ($rc3 -eq 0) $rc3 $false
Check "g.error_proxy_mostrado" ($ilog3 -match "X No se han podido descargar") (($ilog3 -split "`n" | Select-String "^X " | Select-Object -First 1) -join "") $false
Check "g.autocierre" ($ilog3 -match "autocierre tras 20") "" $false
$lisg = @(Get-Listener)
Check "g.no_lanza_la_app" ($lisg.Count -eq 0) ("listeners=" + $lisg.Count + " " + (Describe-Listeners)) $false
Note "g.server_log_cola" ((Tail-Log "server.log" 6) -replace "`n", " | ")

# ---- h) cierre: no dejar ningun servidor vivo en el runner
$lish = @(Get-Listener)
if ($lish.Count -ge 1) {
    Note "h.servidor_restante" (Describe-Listeners)
    try { $null = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers @{ "X-Audios" = "1" } -TimeoutSec 5 } catch {}
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
