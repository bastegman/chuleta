# E2E del Setup.exe (fase 1, ítem 8 del backlog) en un Windows de verdad (el runner de GitHub), con PowerShell 5.1 como
# en el PC del usuario. Lo lanza .github/workflows/setup-exe.yml despues de compilarlo (scripts/build_setup.py):
#   a) instalacion nueva en silencio (/VERYSILENT): codigo 0, carpeta propia, accesos directos con el AppUserModelID,
#      una sola entrada «Tenca» en Aplicaciones instaladas (la de instalar.ps1, con su desinstalador; ninguna de Inno) y
#      sin abrir la app; despues se abre desde el acceso directo del menu Inicio y se apaga
#   w) como el usuario: doble clic en Setup.exe, la bienvenida (captura), «Instalar» pulsado con UI Automation, la
#      ventana de instalar.ps1 (captura; se cierra sola como si se pulsara «Abrir»): actualiza la de antes en su sitio,
#      sin hablar de una carpeta descomprimida, y abre la app
#   u) desinstalar con su orden de Aplicaciones instaladas (-Silencioso): fuera la app, la entrada y los accesos; la
#      reunion se queda
#   v) la Chuleta de verdad (-Chuleta <sha>, la de main que tiene Bernardo), instalada con SU INSTALAR.bat, con una
#      reunion, un ajuste y «Arrancar con Windows»; y Setup.exe en silencio encima: la actualiza en su carpeta (que se
#      sigue llamando Chuleta), sin nada con el nombre viejo, una sola entrada, reunion y ajuste intactos, y la app
#      ensena la carpeta con su nombre («Se guarda en Chuleta\...»). Despues se desinstala
#   g) fallo: pip simulado que falla (NOTAS_INSTALADOR_SIMULAR_FALLO=pip) -> Setup.exe sale con 1
# Cada comprobacion queda en resumen.json; capturas y registros en -Out. Sale con 1 si falla alguna con puerta.
param([Parameter(Mandatory = $true)][string]$Setup, [string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
      [string]$Out = "", [string]$Chuleta = "")

$ErrorActionPreference = "Continue"
if (-not $Out) { $Out = Join-Path $Repo "setup-e2e-out" }
New-Item -ItemType Directory -Force $Out | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Out "capturas") | Out-Null
$script:checks = New-Object System.Collections.Specialized.OrderedDictionary
$script:gated = @{}
$Port = 8765
Set-Variable -Name Base -Option Constant -Value "http://127.0.0.1:$Port"
$App = "Tenca"
$Legacy = "Chuleta"
$python = (Get-Command python).Source
$logDir = Join-Path $env:LOCALAPPDATA "AudiosApp"
$inst = Join-Path $env:USERPROFILE $App
$vInst = Join-Path $env:USERPROFILE $Legacy
$UninstallBase = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
$UninstallKey = "$UninstallBase\$App"
$LegacyKey = "$UninstallBase\$Legacy"
$desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "$App.lnk"
$prog = Join-Path ([Environment]::GetFolderPath("Programs")) "$App.lnk"
$deskLegacy = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Legacy.lnk"
$progLegacy = Join-Path ([Environment]::GetFolderPath("Programs")) "$Legacy.lnk"
$startupDir = [Environment]::GetFolderPath("Startup")
$startup = Join-Path $startupDir "$App.lnk"
$startupLegacy = Join-Path $startupDir "$Legacy.lnk"

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
function Api-Headers($post) {
    $h = @{}
    if ($post) { $h["X-Audios"] = "1" }
    $tf = Join-Path $logDir "sesion.token"
    if (Test-Path -LiteralPath $tf -PathType Leaf) { $tok = [IO.File]::ReadAllText($tf).Trim(); if ($tok) { $h["X-Chuleta-Token"] = $tok } }
    return $h
}
function Wait-Http($url, $seconds) {
    $t0 = Get-Date
    $end = $t0.AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        try {
            $left = [int][Math]::Max(1, [Math]::Min(30, ($end - (Get-Date)).TotalSeconds))
            $r = Invoke-RestMethod $url -Headers (Api-Headers $false) -TimeoutSec $left
            $script:ultimaEspera = [Math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
            return $r
        } catch { Start-Sleep -Milliseconds 500 }
    }
    $script:ultimaEspera = $seconds
    return $null
}
function Get-Listener {
    try {
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Where-Object { $_.OwningProcess -and (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue) }
    } catch { }
}
function Wait-Process-Gone($procId, $seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}
function Apagar-App($label) {
    $lis = @(Get-Listener)
    if ($lis.Count -ge 1) {
        $p = $lis[0].OwningProcess
        try { $null = Invoke-RestMethod "$Base/api/apagar" -Method Post -ContentType "application/json" -Body "{}" -Headers (Api-Headers $true) -TimeoutSec 30 } catch {}
        Check "$label.apagar" (Wait-Process-Gone $p 20) "pid $p"
    }
}
function Last-Run-Log($n = 400) {
    $f = Join-Path $logDir "instalar.log"
    if (-not (Test-Path -LiteralPath $f)) { return "(no existe $f)" }
    $lines = @(Get-Content -LiteralPath $f -Encoding UTF8)
    if ($lines.Count -eq 0) { return "" }
    $start = 0
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -like "==== *instalar.ps1*") { $start = $i } }
    return (($lines[$start..($lines.Count - 1)] | Select-Object -Last $n) -join "`n")
}
function Get-Registro($key = $UninstallKey) {
    $o = [ordered]@{}
    if (-not (Test-Path -LiteralPath $key)) { return $null }
    $k = Get-Item -LiteralPath $key
    foreach ($n in $k.GetValueNames()) { $o[$n] = $k.GetValue($n) }
    return $o
}
function Get-Paquetes {
    # las entradas de Aplicaciones instaladas con el nombre de la app o el anterior (Get-Package, en un job con tope)
    $j = Start-Job -ScriptBlock { param($a, $b) @(Get-Package -Name $a, $b -ProviderName Programs -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name) $($_.Version)" }) } -ArgumentList $App, $Legacy
    if (Wait-Job $j -Timeout 90) { $r = @(Receive-Job $j -ErrorAction SilentlyContinue) } else { $r = @("sin respuesta en 90 s"); Stop-Job $j }
    Remove-Job $j -Force -ErrorAction SilentlyContinue
    return $r
}
function Inno-Restos {
    # lo que dejaria un instalador de Inno por su cuenta: su entrada (<AppId>_is1) y su desinstalador (unins000.exe)
    $out = @(Get-ChildItem -LiteralPath (Get-Item $UninstallBase).PSPath -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like "*_is1" } | ForEach-Object { $_.PSChildName })
    foreach ($d in @($inst, $vInst, $env:LOCALAPPDATA)) {
        if (Test-Path -LiteralPath $d) { $out += @(Get-ChildItem -LiteralPath $d -Filter "unins*.exe" -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) }
    }
    $pf = Join-Path $env:LOCALAPPDATA "Programs\Tenca"
    if (Test-Path -LiteralPath $pf) { $out += $pf }
    return $out
}
function Run-Setup($label, $argumentos, $timeoutMin) {
    $t0 = Get-Date
    $log = Join-Path $Out "setup-$label.log"
    $p = Start-Process -FilePath $Setup -ArgumentList (@($argumentos) + "/LOG=`"$log`"") -PassThru
    $null = $p.Handle
    $ok = $p.WaitForExit($timeoutMin * 60000)
    if (-not $ok) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Check "$label.termina" $false "Setup.exe no termino en $timeoutMin min"
        return $null
    }
    Note "$label.segundos" ([int]((Get-Date) - $t0).TotalSeconds)
    Note "$label.exitcode" $p.ExitCode
    return $p.ExitCode
}
function Setup-Log($label) {
    $f = Join-Path $Out "setup-$label.log"
    if (Test-Path -LiteralPath $f) { return (Get-Content -LiteralPath $f -Raw -Encoding UTF8) }
    return ""
}
function Capture-Screen($path) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
        $g.Dispose()
        $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        return $true
    } catch { return $false }
}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Botones($ventana) {
    $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    return @($ventana.FindAll([System.Windows.Automation.TreeScope]::Descendants, $c))
}
function Find-Boton($ventana, $nombre) {
    foreach ($b in (Botones $ventana)) { if (("$($b.Current.Name)" -replace "&", "") -eq $nombre) { return $b } }
    return $null
}
function Find-VentanaConBoton($titulo, $boton, $seconds) {
    # la ventana de primer nivel cuyo titulo empieza por $titulo y que tiene el boton $boton (UI Automation). Inno
    # tiene ademas una ventana de aplicacion escondida con el mismo titulo: por eso se busca por el boton
    $end = (Get-Date).AddSeconds($seconds)
    $raiz = [System.Windows.Automation.AutomationElement]::RootElement
    while ((Get-Date) -lt $end) {
        foreach ($w in @($raiz.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition))) {
            try { if ("$($w.Current.Name)".StartsWith($titulo) -and (Find-Boton $w $boton)) { return $w } } catch {}
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}
function Textos($ventana) {
    # los textos que ensena una ventana (para comprobar la bienvenida sin depender de la captura)
    $out = @()
    foreach ($e in @($ventana.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition))) {
        try { if ($e.Current.Name) { $out += "$($e.Current.Name)" } } catch {}
    }
    return ($out -join " | ")
}
# lector del System.AppUserModel.ID de un .lnk (el mismo que lookfeel_e2e.ps1)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace SetupE2E {
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
function Get-Lnk($path) {
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $aumid = ""
    try { $aumid = [SetupE2E.Lnk]::GetAppId($path) } catch { $aumid = "error: " + $_.Exception.Message }
    return @{ TargetPath = $sc.TargetPath; Arguments = $sc.Arguments; WorkingDirectory = $sc.WorkingDirectory
              IconLocation = $sc.IconLocation; AppUserModelID = $aumid }
}
function Abrir-Desde($lnkPath) {
    $l = Get-Lnk $lnkPath
    $null = Start-Process -FilePath $l.TargetPath -ArgumentList $l.Arguments -WorkingDirectory $l.WorkingDirectory -PassThru
}

Note "entorno.os" ((Get-CimInstance Win32_OperatingSystem).Caption + " " + (Get-CimInstance Win32_OperatingSystem).Version)
Note "entorno.powershell" "$($PSVersionTable.PSVersion)"
Note "entorno.interactivo" ([Environment]::UserInteractive)
$setupInfo = Get-Item -LiteralPath $Setup
Check "s.setup_existe" ($setupInfo -and $setupInfo.Length -gt 500000) "$($setupInfo.FullName) $($setupInfo.Length) bytes"
Note "s.sha256" (Get-FileHash -LiteralPath $Setup -Algorithm SHA256).Hash
$verSetup = "$($setupInfo.VersionInfo.ProductVersion)".Trim()  # el recurso de versión viene relleno de espacios
Note "s.version_del_exe" "$verSetup · $($setupInfo.VersionInfo.FileVersion)"
Check "s.sin_firma" ((Get-AuthenticodeSignature -LiteralPath $Setup).Status -ne "Valid") (Get-AuthenticodeSignature -LiteralPath $Setup).Status $false

# ---- a) instalacion nueva en silencio
$env:NOTAS_INSTALADOR_AUTOCERRAR = ""
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = ""
$rca = Run-Setup "a" @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") 30
$loga = Last-Run-Log
$loga | Set-Content -LiteralPath (Join-Path $Out "instalar-a.log") -Encoding UTF8
Check "a.exitcode_0" ($rca -eq 0) $rca
Check "a.desde_setup" ($loga -match "instalar\.ps1 desde .*\\Tenca\\_app" -and $loga -match "modo: consola") (($loga -split "`n" | Select-Object -First 3) -join " | ")
Check "a.carpeta_propia" (Test-Path -LiteralPath (Join-Path $inst "_app\server.py")) $inst
$faltan = @("LEEME.txt", "LICENSE", "INSTALAR.bat", "ABRIR TENCA.bat", "_app\desinstalar.ps1", "_app\instalar.ps1", "_app\version.txt", "_app\Tenca.bat", "_app\static\tenca.ico", "_app\static\manrope.woff2", "_app\static\fraunces.woff2", "_app\static\fuentes-OFL.txt", "_app\static\logo.svg") | Where-Object { -not (Test-Path -LiteralPath (Join-Path $inst $_)) }
Check "a.copiados" (@($faltan).Count -eq 0) ("faltan: " + ($faltan -join ", "))
$ver = $(if (Test-Path -LiteralPath (Join-Path $inst "_app\version.txt")) { (Get-Content -LiteralPath (Join-Path $inst "_app\version.txt") -Encoding UTF8 -Raw).Trim() } else { "" })
Check "a.version" ($ver -match "^\d{4}-\d{2}-\d{2} . [0-9a-f]{7,}$" -and $verSetup -eq $ver) "instalada=$ver setup=$verSetup"
$reg = Get-Registro
Check "a.registro" ($reg -and $reg["DisplayName"] -eq $App -and $reg["DisplayVersion"] -eq $ver -and $reg["InstallLocation"] -ieq $inst -and "$($reg['UninstallString'])" -like "*desinstalar.ps1*" -and "$($reg['DisplayIcon'])" -like "*tenca.ico*") $(if ($reg) { "$($reg['DisplayName']) · $($reg['DisplayVersion']) · $($reg['InstallLocation']) · $($reg['UninstallString'])" } else { "sin clave" })
$pka = @(Get-Paquetes)  # con una sola, la función la devuelve suelta (y [0] seria su primera letra)
Check "a.aplicaciones_instaladas_una" ($pka.Count -eq 1 -and "$($pka[0])" -like "$App *") ($pka -join "; ")
$restos = Inno-Restos
Check "a.nada_de_inno" (@($restos).Count -eq 0) ($restos -join ", ")
$la = $(if (Test-Path -LiteralPath $prog) { Get-Lnk $prog } else { $null })
Check "a.accesos_directos" ((Test-Path -LiteralPath $desk) -and $la -and $la.WorkingDirectory -ieq (Join-Path $inst "_app") -and $la.AppUserModelID -eq "Bernardo.Chuleta") $(if ($la) { "$($la.TargetPath) | $($la.WorkingDirectory) | $($la.AppUserModelID)" } else { "sin $prog" })
Check "a.no_abre_la_app" (@(Get-Listener).Count -eq 0) "en silencio, -NoLaunch"
Abrir-Desde $prog
$cfga = Wait-Http "$Base/api/config" 150
Note "a.api_tarda_s" $script:ultimaEspera
Check "a.app_abre" ($cfga -and $cfga.app_name -eq $App -and $cfga.root -ieq $inst -and $cfga.root_name -eq $App -and $cfga.version -eq $ver) "app_name=$($cfga.app_name) root=$($cfga.root) root_name=$($cfga.root_name) version=$($cfga.version)"
Apagar-App "a"
# una reunion en la carpeta propia (se compara en w y u)
$reu = Join-Path $inst "Proyecto Alfa"
New-Item -ItemType Directory -Force $reu | Out-Null
$wav = Join-Path $reu "2026-11-09 10-00-00 Comite.wav"
& $python -c "import sys, wave; w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(32000)); w.close()" $wav
Set-Content -LiteralPath ($wav -replace "\.wav$", ".txt") -Value "Transcripcion del comite." -Encoding UTF8
function Get-Reunion($d) { return ((@(Get-ChildItem -LiteralPath $d -Recurse -File -ErrorAction SilentlyContinue | Sort-Object FullName) | ForEach-Object { $_.Name + "=" + (Get-FileHash -LiteralPath $_.FullName).Hash }) -join ";") }
$reunion0 = Get-Reunion $reu

# ---- w) como el usuario: doble clic, bienvenida, «Instalar», la ventana del instalador
$env:NOTAS_INSTALADOR_AUTOCERRAR = "6"  # la ventana de instalar.ps1 se cierra sola a los 6 s, como si se pulsara «Abrir»
$tw = Get-Date
$logw = Join-Path $Out "setup-w.log"
$pw = Start-Process -FilePath $Setup -ArgumentList "/LOG=`"$logw`"" -PassThru
$null = $pw.Handle
$bienvenida = Find-VentanaConBoton "Instalar $App" "Instalar" 90
Check "w.bienvenida" ($bienvenida -ne $null) $(if ($bienvenida) { $bienvenida.Current.Name } else { "sin ventana «Instalar $App» con el boton «Instalar» en 90 s" })
Start-Sleep -Seconds 2
$null = Capture-Screen (Join-Path $Out "capturas\w1-bienvenida.png")
if ($bienvenida) {
    $tb = Textos $bienvenida
    Note "w.textos" $tb
    Check "w.textos_de_la_bienvenida" ($tb -match "graba, transcribe y resume" -and $tb -match "sin permisos de administrador" -and $tb -match [regex]::Escape($ver)) ""
    $boton = Find-Boton $bienvenida "Instalar"
    Check "w.boton_instalar" ($boton -ne $null) ((Botones $bienvenida | ForEach-Object { $_.Current.Name }) -join ", ")
    if ($boton) { $boton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
}
# la del instalador se llama igual que la del Setup («Instalar Tenca»): se distingue por el proceso (powershell)
$psv = $null
$end = (Get-Date).AddSeconds(120)
while (-not $psv -and (Get-Date) -lt $end) {
    $psv = @(Get-Process powershell -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "Instalar $App*" })[0]
    if (-not $psv) { Start-Sleep -Milliseconds 500 }
}
Check "w.ventana_del_instalador" ($psv -ne $null) $(if ($psv) { "pid $($psv.Id): $($psv.MainWindowTitle)" } else { "sin ventana de instalar.ps1 en 120 s" })
Start-Sleep -Seconds 3
$null = Capture-Screen (Join-Path $Out "capturas\w2-instalador.png")
$okw = $pw.WaitForExit(20 * 60000)
if (-not $okw) { Stop-Process -Id $pw.Id -Force -ErrorAction SilentlyContinue }
Check "w.exitcode_0" ($okw -and $pw.ExitCode -eq 0) $(if ($okw) { $pw.ExitCode } else { "no termino en 20 min" })
Note "w.segundos" ([int]((Get-Date) - $tw).TotalSeconds)
$logwp = Last-Run-Log
$logwp | Set-Content -LiteralPath (Join-Path $Out "instalar-w.log") -Encoding UTF8
Check "w.modo_ventana" ($logwp -match "modo: ventana") (($logwp -split "`n" | Select-String "modo:") -join "")
Check "w.actualiza_la_de_antes" ($logwp -match ("Ya ten.as $App en " + [regex]::Escape($inst))) (($logwp -split "`n" | Select-String "Ya ten" | Select-Object -First 1) -join "")
Check "w.sin_carpeta_descomprimida" (-not ($logwp -match "descomprim")) (($logwp -split "`n" | Select-String "descomprim" | Select-Object -First 2) -join " | ")
Check "w.sin_carpeta_temporal_a_la_vista" ($logwp -match "Desde Setup\.exe" -and -not ($logwp -match "Carpeta: .*\\is-[^\\]+\.tmp")) (($logwp -split "`n" | Select-String "Carpeta:|Desde Setup" | Select-Object -First 2) -join " | ")
Check "w.sin_carpeta_2" (-not (Test-Path -LiteralPath (Join-Path $env:USERPROFILE "$App (2)"))) ""
$cfgw = Wait-Http "$Base/api/config" 150
Check "w.abre_la_app" ($cfgw -and $cfgw.root -ieq $inst) "root=$($cfgw.root)"
$null = Capture-Screen (Join-Path $Out "capturas\w3-app.png")
Check "w.reunion_intacta" ((Get-Reunion $reu) -eq $reunion0) ""
Check "w.una_entrada" (@(Get-Paquetes).Count -eq 1) ((Get-Paquetes) -join "; ")
Check "w.nada_de_inno" (@(Inno-Restos).Count -eq 0) ((Inno-Restos) -join ", ")
Check "w.sin_mensaje_de_error" (-not ((Setup-Log "w") -match "No se ha podido instalar")) ""
Apagar-App "w"
$env:NOTAS_INSTALADOR_AUTOCERRAR = ""

# ---- u) desinstalar con la orden de Aplicaciones instaladas (la de instalar.ps1)
$regu = Get-Registro
$orden = "$($regu['QuietUninstallString'])"
Check "u.orden_silenciosa" ($orden -like "*desinstalar.ps1*-Silencioso*") $orden
$pu = Start-Process -FilePath "cmd.exe" -ArgumentList "/c $orden" -PassThru -WindowStyle Hidden
$null = $pu.Handle
$oku = $pu.WaitForExit(180000)
Check "u.exitcode_0" ($oku -and $pu.ExitCode -eq 0) $(if ($oku) { $pu.ExitCode } else { "no termino en 180 s" })
Check "u.sin_app" (-not (Test-Path -LiteralPath (Join-Path $inst "_app"))) ""
Check "u.sin_entrada" (-not (Test-Path -LiteralPath $UninstallKey)) ""
Check "u.sin_accesos" (-not (Test-Path -LiteralPath $desk) -and -not (Test-Path -LiteralPath $prog)) ""
Check "u.reunion_se_queda" ((Get-Reunion $reu) -eq $reunion0) ""

# ---- v) la Chuleta de verdad y Setup.exe encima
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
    $env:NOTAS_INSTALADOR_AUTOCERRAR = "10"
    $pc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$(Join-Path (Join-Path $vTest $Legacy) 'INSTALAR.bat')`"" -WorkingDirectory (Join-Path $vTest $Legacy) -PassThru
    $null = $pc.Handle
    $okc = $pc.WaitForExit(25 * 60000)
    $env:NOTAS_INSTALADOR_AUTOCERRAR = ""
    Check "v.chuleta_instalada" ($okc -and $pc.ExitCode -eq 0 -and (Test-Path -LiteralPath (Join-Path $vInst "_app\server.py")) -and (Test-Path -LiteralPath $LegacyKey) -and (Test-Path -LiteralPath $deskLegacy)) "rc=$($pc.ExitCode)"
    $cfgc = Wait-Http "$Base/api/config" 150
    Check "v.chuleta_abierta" ($cfgc -and $cfgc.app_name -eq $Legacy) "app_name=$($cfgc.app_name)"
    try { $ar = Invoke-RestMethod "$Base/api/inicio-windows" -Method Post -ContentType "application/json" -Body '{"activado": true}' -Headers (Api-Headers $true) -TimeoutSec 15 } catch { $ar = $null }
    Check "v.chuleta_arranque" ($ar -and $ar.activado -eq $true -and (Test-Path -LiteralPath $startupLegacy)) ""
    $marca = "TENCA-SETUP-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    try { $null = Invoke-RestMethod "$Base/api/settings" -Method Post -ContentType "application/json" -Body (@{ summary = @{ extra = $marca } } | ConvertTo-Json -Compress) -Headers (Api-Headers $true) -TimeoutSec 15 } catch {}
    $ajustes = Join-Path $logDir "settings.json"
    Check "v.chuleta_ajuste" ((Test-Path -LiteralPath $ajustes) -and ((Get-Content -LiteralPath $ajustes -Raw -Encoding UTF8) -match $marca)) ""
    $vReu = Join-Path $vInst "Proyecto Beta"
    New-Item -ItemType Directory -Force $vReu | Out-Null
    $vWav = Join-Path $vReu "2026-11-09 10-00-00 Reunion de Chuleta.wav"
    & $python -c "import sys, wave; w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(32000)); w.close()" $vWav
    Set-Content -LiteralPath ($vWav -replace "\.wav$", ".md") -Value "---`nproyecto: Proyecto Beta`n---`n`nNotas de Chuleta" -Encoding UTF8
    $vReunion0 = Get-Reunion $vReu
    Start-Sleep -Seconds 3
    # Setup.exe en silencio con la Chuleta abierta: instalar.ps1 la cierra
    $rcv = Run-Setup "v" @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") 30
    $logv = Last-Run-Log
    $logv | Set-Content -LiteralPath (Join-Path $Out "instalar-v.log") -Encoding UTF8
    Check "v.exitcode_0" ($rcv -eq 0) $rcv
    Check "v.actualiza_la_de_chuleta" ($logv -match ("Ya ten.as $Legacy \(ahora se llama $App\) en " + [regex]::Escape($vInst))) (($logv -split "`n" | Select-String "Ya ten" | Select-Object -First 1) -join "")
    Check "v.no_instala_en_otra" (-not (Test-Path -LiteralPath (Join-Path $inst "_app"))) ""
    Check "v.sin_nombre_viejo" (-not (Test-Path -LiteralPath $deskLegacy) -and -not (Test-Path -LiteralPath $progLegacy) -and -not (Test-Path -LiteralPath $startupLegacy) -and -not (Test-Path -LiteralPath $LegacyKey) -and -not (Test-Path -LiteralPath (Join-Path $vInst "ABRIR CHULETA.bat"))) ""
    Check "v.accesos_tenca" ((Test-Path -LiteralPath $desk) -and (Test-Path -LiteralPath $prog) -and (Test-Path -LiteralPath $startup)) ""
    $pkv = @(Get-Paquetes)
    Check "v.una_entrada" ($pkv.Count -eq 1 -and "$($pkv[0])" -like "$App *") ($pkv -join "; ")
    Check "v.nada_de_inno" (@(Inno-Restos).Count -eq 0) ((Inno-Restos) -join ", ")
    Check "v.reunion_intacta" ((Get-Reunion $vReu) -eq $vReunion0) ""
    Check "v.ajuste_intacto" ((Get-Content -LiteralPath $ajustes -Raw -Encoding UTF8) -match $marca) ""
    Check "v.aviso_renombrada" ($logv -match "$Legacy ahora se llama ${App}: tus reuniones, voces y ajustes siguen donde estaban") ""
    Abrir-Desde $prog
    $cfgv = Wait-Http "$Base/api/config" 150
    Check "v.app_es_tenca_en_chuleta" ($cfgv -and $cfgv.app_name -eq $App -and $cfgv.root -ieq $vInst -and $cfgv.root_name -eq (Split-Path -Leaf $vInst)) "app_name=$($cfgv.app_name) root=$($cfgv.root) root_name=$($cfgv.root_name)"
    try { $planv = Invoke-RestMethod "$Base/api/grabar/plan" -Method Post -ContentType "application/json" -Body "{}" -Headers (Api-Headers $true) -TimeoutSec 30 } catch { $planv = $null }
    $carv = $(if ($planv) { @($planv.lineas | Where-Object { $_.clave -eq "carpeta" })[0].valor } else { "" })
    Check "v.se_guarda_en_la_carpeta_real" ("$carv" -like "$(Split-Path -Leaf $vInst)\*") "$carv"
    Apagar-App "v"
    $regv = Get-Registro
    $pv = Start-Process -FilePath "cmd.exe" -ArgumentList "/c $($regv['QuietUninstallString'])" -PassThru -WindowStyle Hidden
    $null = $pv.Handle
    Check "v.desinstalada" ($pv.WaitForExit(180000) -and $pv.ExitCode -eq 0 -and -not (Test-Path -LiteralPath (Join-Path $vInst "_app")) -and -not (Test-Path -LiteralPath $UninstallKey)) ""
    Check "v.reunion_tras_desinstalar" ((Get-Reunion $vReu) -eq $vReunion0) ""
}

# ---- g) fallo: pip simulado -> Setup.exe en silencio sale con 1 (no da por buena una instalacion rota)
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = "pip"
$rcg = Run-Setup "g" @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") 20
$env:NOTAS_INSTALADOR_SIMULAR_FALLO = ""
$logg = Last-Run-Log
$logg | Set-Content -LiteralPath (Join-Path $Out "instalar-g.log") -Encoding UTF8
Check "g.exitcode_1" ($rcg -eq 1) $rcg
Check "g.log_del_fallo" ($logg -match "X ") (($logg -split "`n" | Select-String "^X " | Select-Object -First 1) -join "")
Check "g.setup_lo_registra" ((Setup-Log "g") -match "instalar\.ps1 ha terminado con el c.digo 1") ""

# ---- h) cierre
$lish = @(Get-Listener)
if ($lish.Count -ge 1) { Apagar-App "h" }
foreach ($n in "server.log", "instalar.log") {
    $f = Join-Path $logDir $n
    if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination (Join-Path $Out $n) -Force }
}
Save-Summary
$fallos = @($script:checks.Keys | Where-Object { -not $script:checks[$_].ok -and $script:gated[$_] })
Write-Host ""
Write-Host ("Comprobaciones: " + $script:checks.Count + " · fallos con puerta: " + $fallos.Count + " " + ($fallos -join ", "))
if ($fallos.Count -gt 0) { exit 1 }
exit 0
