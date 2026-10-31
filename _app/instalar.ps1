# Instalador de Notas (lo lanza INSTALAR.bat). No necesita permisos de administrador:
#   1. Python de 3.10 a 3.13 (si no hay, instala 3.13 con winget solo para este usuario)
#   2. Librerias: faster-whisper (Whisper local + PyAV para leer audio), soundcard (grabadora), requests,
#      sherpa-onnx (quien habla) y pywin32 (calendario de Outlook), opcionales: si no cargan, la app funciona igual
#   3. Accesos directos "Notas" en el escritorio y en el menu Inicio
# Se puede repetir sin problema. Para probarlo sin tocar nada: -NoShortcut / -NoLaunch, y -Port / -Desktop
# (otro puerto y otra carpeta de escritorio, como si fuera otro PC).
param([switch]$NoShortcut, [switch]$NoLaunch, [int]$Port = 8765, [string]$Desktop = "")

$ErrorActionPreference = "Continue"
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $app
$packages = @("faster-whisper==1.2.1", "soundcard>=0.4.3", "requests", "truststore")
# opcionales: se instalan una a una y aparte del resto; si alguna falla, solo se avisa (la app funciona sin ella)
$optional = @("sherpa-onnx==2.0.1", "pywin32==312")  # quien habla / calendario de Outlook
$MaxPy = 313  # el Python mas nuevo con faster-whisper/ctranslate2 publicados (3.14 aun no tiene wheels)

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Step($text) { Write-Host ""; Write-Host "  > $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host ""
    Write-Host "  X  $text" -ForegroundColor Red
    exit 1
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

Write-Host ""
Write-Host "  ==========================================================" -ForegroundColor DarkCyan
Write-Host "    NOTAS  ·  grabar, transcribir y resumir reuniones" -ForegroundColor White
Write-Host "  ==========================================================" -ForegroundColor DarkCyan
Say "    Carpeta: $root"

# archivos descargados de Teams/correo: sin esto Windows pregunta cada vez al abrirlos
Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

# --- si Notas esta abierto se cierra: si no, al terminar se volveria a ver la version vieja (sigue en marcha)
Step "Comprobando si Notas está abierto..."
$running = $null
try { $running = Invoke-RestMethod "http://127.0.0.1:$Port/api/ping" -TimeoutSec 2 } catch {}
if ($running -and $running.app -eq "audios") {
    $st = $null
    try { $st = Invoke-RestMethod "http://127.0.0.1:$Port/api/rec/status" -TimeoutSec 2 } catch {}
    if ($st -and $st.recording) { Fail "Notas está grabando ahora mismo. Para la grabación y vuelve a abrir INSTALAR.bat." }
    $owner = $null
    try { $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess } catch {}
    if (-not $owner) {
        $m = netstat -ano -p tcp | Select-String ":$Port\s+\S+\s+LISTENING\s+(\d+)" | Select-Object -First 1
        if ($m) { $owner = [int]$m.Matches[0].Groups[1].Value }
    }
    if (-not $owner) { Fail "Notas está abierto y no he podido cerrarlo: cierra su ventana negra y vuelve a abrir INSTALAR.bat." }
    $ids = @($owner)
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
    for ($k = 0; $k -lt 2 -and $p; $k++) {  # tambien el "py" y la ventana negra que lo lanzaron
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
        if ($p -and ($p.Name -eq "py.exe" -or ($p.Name -eq "cmd.exe" -and $p.CommandLine -like "*Notas.bat*"))) { $ids += $p.ProcessId }
    }
    $ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
    Say "    Cerrada la versión que estaba abierta (al terminar se abre la nueva)" Green
} else {
    Say "    No estaba abierto"
}

# --- si ya estaba instalado en otra carpeta (p. ej. el zip nuevo se ha descomprimido en Descargas),
# se actualiza aquella: ahi estan sus grabaciones, y el acceso directo ya apunta a ella
if (-not $NoShortcut -or $Desktop) {
    $old = $null
    try {
        $lnkPath = Join-Path $(if ($Desktop) { $Desktop } else { [Environment]::GetFolderPath("Desktop") }) "Notas.lnk"
        if (Test-Path -LiteralPath $lnkPath) {
            $oldApp = Split-Path -Parent (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath).TargetPath
            if ($oldApp -and (Test-Path -LiteralPath (Join-Path $oldApp "server.py")) -and
                ((Resolve-Path -LiteralPath $oldApp).Path -ne (Resolve-Path -LiteralPath $app).Path)) { $old = $oldApp }
        }
    } catch {}
    if ($old) {
        $oldRoot = Split-Path -Parent $old
        Step "Ya tenías Notas en $oldRoot : actualizo esa (con tus grabaciones)..."
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
        & winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
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
if ($LASTEXITCODE -ne 0) { & $py -m ensurepip --upgrade }
function Pip-Install($extra, $list) {
    # pip escribe los errores por stderr: se ensenan y se guardan para distinguir despues que ha fallado
    $script:pipOut = @()
    if (-not $extra) { $extra = @() }
    if (-not $list) { $list = $packages }
    & $py -m pip install @extra --disable-pip-version-check --no-warn-script-location @list 2>&1 |
        ForEach-Object { $s = "$_"; Write-Host "    $s"; $script:pipOut += $s }
    return $LASTEXITCODE
}
$userMode = $null
$rc = Pip-Install
if ($rc -ne 0) {
    Say "    Reintentando en la carpeta de tu usuario (--user)..." Yellow
    $userMode = "--user"
    $rc = Pip-Install $userMode
}
& $py -c "import faster_whisper, soundcard, requests, av, numpy; print('    faster-whisper', faster_whisper.__version__, '| PyAV', av.__version__)"
$coreOk = ($LASTEXITCODE -eq 0)
# sherpa-onnx es opcional (quien habla): se instala aparte para que un fallo suyo no deje sin librerias al resto,
# y si no importa solo se avisa: la app sigue funcionando sin esa opcion
if ($coreOk) {
    Step "Instalando las librerias opcionales (quien habla, calendario de Outlook)..."
    foreach ($opt in $optional) { $null = Pip-Install $userMode @($opt) }
}
& $py -c "import sherpa_onnx; print('    sherpa-onnx', sherpa_onnx.__version__, '(quien habla: disponible)')" 2>$null
if ($LASTEXITCODE -ne 0) { Say "    sherpa-onnx no esta disponible: la opcion Voces (quien habla) no se podra activar en este PC" Yellow }
& $py -c "import win32com.client; print('    pywin32 (calendario de Outlook: disponible)')" 2>$null
if ($LASTEXITCODE -ne 0) { Say "    pywin32 no esta disponible: no se podra leer el calendario de Outlook (el resto funciona)" Yellow }
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

if (-not $NoShortcut) {
    Step "Creando el acceso directo 'Notas'..."
    $ws = New-Object -ComObject WScript.Shell
    $places = if ($Desktop) { @($Desktop) } else { @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs")) }  # escritorio y menu Inicio
    foreach ($dir in $places) {
        try {
            $lnk = $ws.CreateShortcut((Join-Path $dir "Notas.lnk"))
            $lnk.TargetPath = Join-Path $app "Notas.bat"
            $lnk.WorkingDirectory = $app
            $lnk.IconLocation = (Join-Path $app "static\notas.ico") + ",0"
            $lnk.WindowStyle = 7  # la ventana del motor, minimizada
            $lnk.Description = "Notas: grabar, transcribir y resumir reuniones"
            $lnk.Save()
            Say "    $dir\Notas.lnk" Green
        } catch {
            Say "    No se pudo crear en $dir (usa ABRIR NOTAS.bat)" Yellow
        }
    }
}

Write-Host ""
$ver = "$(Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\LEEME.txt') -TotalCount 1 -ErrorAction SilentlyContinue)"
$ver = if ($ver -match '\((vers[^)]*)\)') { " ($($Matches[1]))" } else { "" }
Write-Host "  Listo$ver." -ForegroundColor Green
Write-Host "  Si es la primera vez: Ajustes (rueda dentada) > Transcripcion en la nube > tu API key (Groq u otro proveedor)." -ForegroundColor Green
Write-Host "  Instrucciones completas en LEEME.txt" -ForegroundColor Green
if (-not $NoLaunch) {
    Start-Process -FilePath (Join-Path $app "Notas.bat") -WorkingDirectory $app -WindowStyle Minimized
}
