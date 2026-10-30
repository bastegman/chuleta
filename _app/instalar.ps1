# Instalador de Notas (lo lanza INSTALAR.bat). No necesita permisos de administrador:
#   1. Python 3.10+ (si no hay, lo instala con winget solo para este usuario)
#   2. Librerias: faster-whisper (Whisper local + PyAV para leer audio), soundcard (grabadora), requests
#   3. Accesos directos "Notas" en el escritorio y en el menu Inicio
# Se puede repetir sin problema. Para probarlo sin tocar nada: -NoShortcut / -NoLaunch, y -Port / -Desktop
# (otro puerto y otra carpeta de escritorio, como si fuera otro PC).
param([switch]$NoShortcut, [switch]$NoLaunch, [int]$Port = 8765, [string]$Desktop = "")

$ErrorActionPreference = "Continue"
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $app
$packages = @("faster-whisper==1.2.1", "soundcard>=0.4.3", "requests", "truststore")

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Step($text) { Write-Host ""; Write-Host "  > $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host ""
    Write-Host "  X  $text" -ForegroundColor Red
    exit 1
}

function Find-Python {
    # devuelve la ruta de un python.exe 3.10+ que funcione (o $null)
    $probe = "import sys; print(sys.executable); print(sys.version_info[0] * 100 + sys.version_info[1])"
    $tries = @(@("py", "-3"), @("python"), @("python3"))
    foreach ($dir in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python*", "${env:ProgramFiles(x86)}\Python*")) {
        Get-ChildItem -Path $dir -Filter python.exe -Recurse -Depth 1 -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | ForEach-Object { $tries += , @($_.FullName) }
    }
    foreach ($t in $tries) {
        $exe = $t[0]
        $rest = @($t | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try { $out = & $exe @rest -c $probe 2>$null } catch { continue }
        if ($LASTEXITCODE -ne 0 -or $out.Count -lt 2) { continue }
        if ([int]$out[1] -ge 310 -and $out[0] -notlike "*WindowsApps*") { return $out[0].Trim() }
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
        New-Item -ItemType Directory -Force (Join-Path $old "static") | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $app "static") -File |
            ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $old "static") -Force }
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

Step "Buscando Python 3.10 o superior..."
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
& $py -m pip install --disable-pip-version-check --no-warn-script-location @packages
if ($LASTEXITCODE -ne 0) {
    Say "    Reintentando en la carpeta de tu usuario (--user)..." Yellow
    & $py -m pip install --user --disable-pip-version-check --no-warn-script-location @packages
}
& $py -c "import faster_whisper, soundcard, requests, av, numpy; print('    faster-whisper', faster_whisper.__version__, '| PyAV', av.__version__)"
if ($LASTEXITCODE -ne 0) {
    Fail ("No se han podido instalar las librerias. Si estas en la red de la empresa puede ser el proxy: " +
          "prueba con otra red (p. ej. el movil) o pide a IT acceso a pypi.org, y vuelve a abrir INSTALAR.bat.")
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
