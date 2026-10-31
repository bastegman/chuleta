# Desinstalador de Chuleta. Lo lanza "Aplicaciones instaladas" (Configuracion > Aplicaciones > Chuleta > Desinstalar)
# con la orden que deja instalar.ps1 en HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Chuleta. Sin permisos
# de administrador.
# Quita la aplicacion: los accesos directos de esta instalacion (escritorio e Inicio), su entrada de "Aplicaciones
# instaladas", la carpeta _app y los .bat de la raiz. NUNCA toca las reuniones: audios, transcripciones (.txt/.srt),
# notas (.md), resumenes, hablantes, carpetas de proyecto y listas de Vocabulario se quedan en la carpeta de la app
# (la de _app), igual que el LEEME.txt y la licencia. En _app solo borra lo que es de la app (codigo, iconos, version y
# python.txt); si alli hay algo mas, se queda y se dice.
#   -Silencioso   sin ventana (QuietUninstallString, CI). No borra %LOCALAPPDATA%\AudiosApp salvo con -BorrarDatos
#   -BorrarDatos  borra tambien ajustes, claves, huellas de voz y registros (%LOCALAPPDATA%\AudiosApp); con ventana,
#                 deja la casilla marcada
#   -Port         puerto de la app (8765)
# Solo para CI: -AutoCerrar N responde solo a los N segundos de cada pantalla (la de confirmar, como "Cancelar"; con
# -AutoConfirmar, como "Desinstalar"; la final, "Cerrar"). Windows nunca lanza el desinstalador con ellas.
# Para poder borrar _app, el script se copia a %TEMP% y se relanza desde alli (-Relanzado -App <carpeta _app>).
# Lo que hace queda en %TEMP%\chuleta-desinstalar.log. Codigo de salida: 0 desinstalada (o cancelado en la ventana),
# 1 incompleta o error, 2 la app esta grabando (no se ha tocado nada).
param([switch]$Silencioso, [switch]$BorrarDatos, [int]$Port = 8765, [int]$AutoCerrar = 0, [switch]$AutoConfirmar,
      [switch]$Relanzado, [string]$App = "")

$ErrorActionPreference = "Continue"
$AppName = "Chuleta"
$LegacyName = "Notas"  # nombre anterior de la app: su acceso directo, si apunta aqui, tambien sobra
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Chuleta"
$script:logFile = Join-Path $env:TEMP "chuleta-desinstalar.log"
function Log($text) {
    try { Add-Content -LiteralPath $script:logFile -Value ((Get-Date -Format "HH:mm:ss") + " " + ("$text" -replace "`r", "")) -Encoding UTF8 } catch {}
}

if (-not $App) { $App = Split-Path -Parent $PSCommandPath }
$App = [IO.Path]::GetFullPath($App).TrimEnd('\')
$Root = Split-Path -Parent $App
$Datos = $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "AudiosApp" } else { "" })

# --- 1. la copia de _app relanza la de %TEMP% y espera: una carpeta no se puede borrar mientras se ejecuta desde ella
if (-not $Relanzado) {
    try { Add-Content -LiteralPath $script:logFile -Value ("==== " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " desinstalar.ps1 desde " + $App) -Encoding UTF8 } catch {}
    try { Set-Location -LiteralPath $env:TEMP; [Environment]::CurrentDirectory = $env:TEMP } catch {}
    $tmp = Join-Path $env:TEMP ("chuleta-desinstalar-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".ps1")
    try { Copy-Item -LiteralPath $PSCommandPath -Destination $tmp -Force -ErrorAction Stop } catch {
        Log "X no se pudo copiar a $tmp : $($_.Exception.Message)"
        exit 1
    }
    $exe = (Get-Process -Id $PID).Path  # el mismo PowerShell (5.1 en un PC normal)
    if (-not $exe) { $exe = "powershell.exe" }
    $a = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$tmp`"", "-Relanzado", "-App", "`"$App`"", "-Port", "$Port")
    if ($Silencioso) { $a += "-Silencioso" }
    if ($BorrarDatos) { $a += "-BorrarDatos" }
    if ($AutoCerrar -gt 0) { $a += @("-AutoCerrar", "$AutoCerrar") }
    if ($AutoConfirmar) { $a += "-AutoConfirmar" }
    Log "relanzo: $tmp"
    # en la misma consola (-NoNewWindow): oculta si la lanzo "Aplicaciones instaladas" (-WindowStyle Hidden), y sin
    # STARTUPINFO de ventana oculta, que esconderia tambien la ventana del desinstalador
    $child = Start-Process -FilePath $exe -ArgumentList ($a -join " ") -WorkingDirectory $env:TEMP -NoNewWindow -PassThru
    $null = $child.Handle  # sin esto, en PowerShell 5.1 ExitCode sale vacio
    $child.WaitForExit()
    exit $child.ExitCode
}

Log "desinstalando $AppName de $Root (silencioso=$Silencioso borrar_datos=$BorrarDatos pid=$PID)"
# lo que la app trae en cada carpeta de _app y lo que genera alli (__pycache__, python.txt): solo eso se borra, carpeta
# por carpeta y sin recorrer nada a ciegas (Windows PowerShell 5.1 entra en los enlaces al recorrer: otra subcarpeta,
# un enlace o un fichero de otro tipo se quedan y se dicen). Ninguna extension de reunion (audio, .txt, .srt, .md,
# .json) esta en las listas; los .txt se borran solo por su nombre exacto
$AppFolders = [ordered]@{
    "."           = @(".py", ".ps1", ".bat")
    "static"      = @(".css", ".js", ".html", ".svg", ".ico", ".png")
    "export"      = @(".bat")
    "__pycache__" = @(".pyc")
}
$CodeTxt = @("version.txt", "python.txt", "export\LEEME.txt")
# los .bat de la raiz (sin _app no funcionan); el LEEME.txt y la licencia se quedan junto a las reuniones
$RootLaunchers = @("INSTALAR.bat", "ABRIR CHULETA.bat", "ABRIR NOTAS.bat")

# --- 2. presentacion: ventana (WinForms, como el instalador) o nada (-Silencioso). La logica no depende de esto.
$script:ui = $null
function Say($text) {
    Log $text
    if ($script:ui) { $script:ui.status.Text = $text; [System.Windows.Forms.Application]::DoEvents() }
}
function Load-Image($path) {
    # en memoria: Image.FromFile deja el fichero abierto y _app\static\chuleta.png no se podria borrar
    try { return [System.Drawing.Image]::FromStream((New-Object System.IO.MemoryStream(, [System.IO.File]::ReadAllBytes($path)))) } catch { return $null }
}
function New-UninstallWindow {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $f = New-Object System.Windows.Forms.Form
        $f.Text = "Desinstalar $AppName"
        $f.StartPosition = "CenterScreen"
        $f.FormBorderStyle = "FixedDialog"
        $f.MaximizeBox = $false
        $f.MinimizeBox = $false
        $f.ClientSize = New-Object System.Drawing.Size(640, 392)
        $f.Font = New-Object System.Drawing.Font("Segoe UI", 10)
        $f.BackColor = [System.Drawing.Color]::White
        try { $f.Icon = New-Object System.Drawing.Icon((New-Object System.IO.MemoryStream(, [System.IO.File]::ReadAllBytes((Join-Path $App "static\chuleta.ico"))))) } catch {}
        $logo = New-Object System.Windows.Forms.PictureBox
        $logo.Location = New-Object System.Drawing.Point(24, 20)
        $logo.Size = New-Object System.Drawing.Size(64, 64)
        $logo.SizeMode = "Zoom"
        $logo.Image = Load-Image (Join-Path $App "static\chuleta.png")
        $title = New-Object System.Windows.Forms.Label
        $title.Text = $AppName
        $title.Font = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold)
        $title.Location = New-Object System.Drawing.Point(100, 18)
        $title.AutoSize = $true
        $sub = New-Object System.Windows.Forms.Label
        $sub.Text = "desinstalar la aplicación de este PC"
        $sub.ForeColor = [System.Drawing.Color]::DimGray
        $sub.Location = New-Object System.Drawing.Point(104, 58)
        $sub.AutoSize = $true
        $msg = New-Object System.Windows.Forms.Label
        $msg.Text = "Se quitará la aplicación $AppName de este PC."
        $msg.Location = New-Object System.Drawing.Point(24, 104)
        $msg.AutoSize = $true
        $msg.MaximumSize = New-Object System.Drawing.Size(592, 0)
        $keep = New-Object System.Windows.Forms.Label
        $keep.Text = "Tus reuniones, transcripciones y notas en $Root NO se borran. Las carpetas vinculadas tampoco se tocan."
        $keep.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
        $keep.ForeColor = [System.Drawing.Color]::FromArgb(22, 128, 61)
        $keep.AutoSize = $true
        $keep.MaximumSize = New-Object System.Drawing.Size(592, 0)
        $chk = New-Object System.Windows.Forms.CheckBox
        $chk.Text = "Borrar también mis ajustes, claves y voces guardadas (%LOCALAPPDATA%\AudiosApp)"
        $chk.Left = 24
        $chk.AutoSize = $true
        $chk.Checked = [bool]$BorrarDatos
        $warn = New-Object System.Windows.Forms.Label
        $warn.Text = ("Las huellas de voz son datos biométricos: si las borras, se pierden para siempre y habría que volver a " +
                      "grabarlas con el consentimiento de cada persona. Las API keys habría que escribirlas otra vez, y se pierden " +
                      "las copias de las transcripciones de antes de editarlas a mano y la lista de carpetas vinculadas (las " +
                      "carpetas no se tocan). Desmarcada, todo eso se queda en este PC y una reinstalación lo vuelve a usar.")
        $warn.Font = New-Object System.Drawing.Font("Segoe UI", 9)
        $warn.ForeColor = [System.Drawing.Color]::DimGray
        $warn.Left = 42
        $warn.AutoSize = $true
        $warn.MaximumSize = New-Object System.Drawing.Size(574, 0)
        $status = New-Object System.Windows.Forms.Label
        $status.Left = 24
        $status.Size = New-Object System.Drawing.Size(592, 44)
        $status.Text = ""
        $btnOk = New-Object System.Windows.Forms.Button
        $btnOk.Text = "Desinstalar"
        $btnOk.Left = 366
        $btnOk.Size = New-Object System.Drawing.Size(130, 34)
        $btnCancel = New-Object System.Windows.Forms.Button
        $btnCancel.Text = "Cancelar"
        $btnCancel.Left = 506
        $btnCancel.Size = New-Object System.Drawing.Size(110, 34)
        $btnFolder = New-Object System.Windows.Forms.Button
        $btnFolder.Text = "Abrir la carpeta"
        $btnFolder.Left = 346
        $btnFolder.Size = New-Object System.Drawing.Size(150, 34)
        $btnFolder.Visible = $false
        $f.Controls.AddRange(@($logo, $title, $sub, $msg, $keep, $chk, $warn, $status, $btnOk, $btnCancel, $btnFolder))
        $f.AcceptButton = $null
        $f.CancelButton = $btnCancel
        $btnOk.Add_Click({ $script:ui.choice = "desinstalar" })
        $btnCancel.Add_Click({ if (-not $script:ui.busy) { $script:ui.choice = "cancelar"; $script:ui.form.Close() } })
        $btnFolder.Add_Click({ try { Start-Process -FilePath "explorer.exe" -ArgumentList "`"$Root`"" } catch {} })
        $f.Add_FormClosing({ param($s, $e) if ($script:ui.busy) { $e.Cancel = $true } elseif (-not $script:ui.choice) { $script:ui.choice = "cancelar" } })
        $script:ui = @{ form = $f; msg = $msg; keep = $keep; chk = $chk; warn = $warn; status = $status; btnOk = $btnOk
                        btnCancel = $btnCancel; btnFolder = $btnFolder; choice = $null; busy = $false }
        Layout-Ui
        $f.Show()
        $f.Activate()
        $btnCancel.Focus() | Out-Null  # por defecto, lo que no borra nada
        [System.Windows.Forms.Application]::DoEvents()
        return $true
    } catch {
        Log "sin ventana: $($_.Exception.Message)"
        $script:ui = $null
        return $false
    }
}
function Layout-Ui {
    # en columna, cada control debajo del anterior visible (las etiquetas crecen con rutas largas)
    $ui = $script:ui
    $y = $ui.msg.Bottom + 6
    foreach ($c in @($ui.keep, $ui.chk, $ui.warn)) {
        if (-not $c.Visible -and $ui.form.Visible) { continue }
        if ($c -eq $ui.chk) { $y += 8 }
        $c.Top = $y
        $y = $c.Bottom + 2
    }
    $ui.status.Top = $y + 10
    foreach ($b in @($ui.btnOk, $ui.btnCancel, $ui.btnFolder)) { $b.Top = $ui.status.Bottom + 8 }
    $ui.form.ClientSize = New-Object System.Drawing.Size(640, ($ui.btnCancel.Bottom + 18))
}
function Wait-Choice($auto) {
    # espera a que se pulse un boton o se cierre la ventana; con -AutoCerrar (CI), responde $auto a los N segundos
    $t0 = Get-Date
    while ($script:ui.form.Visible -and -not $script:ui.choice) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
        if ($AutoCerrar -gt 0 -and ((Get-Date) - $t0).TotalSeconds -ge $AutoCerrar) {
            Log "autocierre tras $AutoCerrar s: $auto"
            $script:ui.choice = $auto
        }
    }
    if (-not $script:ui.choice) { $script:ui.choice = "cancelar" }
    return $script:ui.choice
}

# --- 3. pasos
function Same-Path($a, $b) {
    if (-not $a -or -not $b) { return $false }
    try { return ([IO.Path]::GetFullPath("$a").TrimEnd('\') -ieq [IO.Path]::GetFullPath("$b").TrimEnd('\')) } catch { return $false }
}
function Get-ListenerPid {
    # el proceso que escucha en 127.0.0.1:<puerto>, solo si es Python (la app); cualquier otro programa no se toca
    $ids = @()
    try {
        $ids = @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction Stop | ForEach-Object { [int]$_.OwningProcess })
    } catch {
        try { $ids = @(netstat -ano -p tcp | Select-String "127\.0\.0\.1:$Port\s+\S+\s+LISTENING\s+(\d+)" | ForEach-Object { [int]$_.Matches[0].Groups[1].Value }) } catch {}
    }
    foreach ($id in $ids) {
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -match '^(pythonw?|py)$') { return $id }
    }
    return $null
}
function Get-ApiHeaders($post) {
    # X-Audios en los POST y, si la app ha escrito su token de sesion (AudiosApp\sesion.token, versiones con la API
    # protegida), X-Chuleta-Token. Sin el fichero, como siempre. El token no va a ningun registro
    $h = @{}
    if ($post) { $h["X-Audios"] = "1" }
    try {
        if ($Datos) {
            $tf = Join-Path $Datos "sesion.token"
            if (Test-Path -LiteralPath $tf -PathType Leaf) {
                $tok = [IO.File]::ReadAllText($tf).Trim()
                if ($tok) { $h["X-Chuleta-Token"] = $tok }
            }
        }
    } catch {}
    return $h
}
function Invoke-Api($path, $post = $false, $timeout = 2) {
    # @{ code; body }: 200 si contesta bien, el codigo HTTP si contesta con error (409 grabando, 401/403 sin token
    # valido...) y 0 si no contesta
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
    # alguna grabadora de Chuleta (recorder.py) en marcha: puede estar grabando una reunion
    try {
        return (@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.Name -match '^pythonw?\.exe$' -and "$($_.CommandLine)" -match 'recorder\.py' }).Count -gt 0)
    } catch { return $true }  # sin poder mirarlo, se supone que si
}
function Get-AppProcesses {
    # procesos de esta instalacion: el servidor (si Stop-App vio que era Chuleta) y los que llevan _app o la carpeta de
    # la ventana (AudiosApp\webview) en la linea de ordenes: worker.py, recorder.py, voices.py, el aviso y WebView2
    $marks = @($App)
    if ($Datos) { $marks += (Join-Path $Datos "webview") }
    $out = @()
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($p.ProcessId -eq $PID -or $p.Name -notmatch '^(pythonw?|py|msedgewebview2)\.exe$') { continue }
        $cl = "$($p.CommandLine)"
        foreach ($m in $marks) { if ($cl.IndexOf($m, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $out += $p; break } }
    }
    if ($script:appPid -and -not ($out | Where-Object { $_.ProcessId -eq $script:appPid })) {
        $out += @(Get-CimInstance Win32_Process -Filter "ProcessId=$($script:appPid)" -ErrorAction SilentlyContinue)
    }
    return @($out | Where-Object { $_ })
}
function Stop-App {
    # "no" (no estaba abierta), "ok" (apagada) o "grabando" (no se toca nada)
    $ping = Invoke-Api "/api/ping" $false 2
    # 401/403: contesta una version con la API protegida y no he podido usar su token: esta abierta igual
    if (-not (($ping.code -eq 200 -and $ping.body.app -eq "audios") -or $ping.code -eq 401 -or $ping.code -eq 403)) { return "no" }
    $script:appPid = Get-ListenerPid  # su proceso cuenta para Wait-AppGone
    $ap = Invoke-Api "/api/apagar" $true 5
    Log "POST /api/apagar: $($ap.code)"
    if ($ap.code -eq 409) { return "grabando" }
    if ($ap.code -eq 200) { return "ok" }
    # plan B (version sin /api/apagar, sin token valido o sin respuesta): se cierra su proceso, pero nunca si graba o si
    # hay una grabadora viva
    $st = Invoke-Api "/api/rec/status" $false 2
    if ($st.code -eq 200 -and $st.body.recording) { return "grabando" }
    if (Test-Grabadora) { Log "hay una grabadora (recorder.py) en marcha"; return "grabando" }
    if ($script:appPid) { Log "cierro el proceso $($script:appPid)"; Stop-Process -Id $script:appPid -Force -ErrorAction SilentlyContinue }
    return "ok"
}
function Wait-AppGone {
    # "ok", "grabando" o "procesos". Hasta 15 s a que terminen solos (los hijos mueren al quedarse sin padre); los que
    # queden se cierran, salvo la grabadora (recorder.py): si sigue viva (la app no contesta, u otro puerto) puede estar
    # grabando una reunion y no se toca nada
    $end = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $end) {
        $left = @(Get-AppProcesses)
        if ($left.Count -eq 0) { return "ok" }
        Start-Sleep -Milliseconds 400
    }
    $left = @(Get-AppProcesses)
    if (@($left | Where-Object { "$($_.CommandLine)" -match 'recorder\.py' }).Count -gt 0) {
        Log "sigue viva la grabadora (recorder.py): no se toca nada"
        return "grabando"
    }
    foreach ($p in $left) {
        Log "cierro $($p.Name) $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    if (@(Get-AppProcesses).Count -eq 0) { return "ok" }
    return "procesos"
}
function Remove-Shortcuts {
    # Chuleta.lnk (y Notas.lnk, el nombre anterior) en el escritorio y en Inicio, solo si apuntan a esta instalacion:
    # otra copia de Chuleta en otra carpeta conserva el suyo
    $n = 0
    $ws = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
        foreach ($name in @("$AppName.lnk", "$LegacyName.lnk")) {
            $lnk = Join-Path $dir $name
            if (-not (Test-Path -LiteralPath $lnk)) { continue }
            $sc = $ws.CreateShortcut($lnk)
            $wd, $tp, $ar = "$($sc.WorkingDirectory)", "$($sc.TargetPath)", "$($sc.Arguments)"
            $ours = (Same-Path $wd $App) -or ($tp -and (Same-Path (Split-Path -Parent $tp) $App)) -or
                    ($ar.IndexOf($App, [StringComparison]::OrdinalIgnoreCase) -ge 0)
            if (-not $ours) { Log "se conserva $lnk (apunta a $wd)"; continue }
            try { Remove-Item -LiteralPath $lnk -Force -ErrorAction Stop; Log "borrado $lnk"; $n++ }
            catch { Log "X no se pudo borrar $lnk : $($_.Exception.Message)"; $script:fallos += $lnk }
        }
    }
    return $n
}
function Remove-Registro {
    # la entrada de "Aplicaciones instaladas", si es la de esta carpeta
    if (-not (Test-Path -LiteralPath $UninstallKey)) { Log "sin entrada en Aplicaciones instaladas"; return }
    $loc = "$((Get-ItemProperty -LiteralPath $UninstallKey -ErrorAction SilentlyContinue).InstallLocation)"
    if ($loc -and -not (Same-Path $loc $Root)) { Log "la entrada de Aplicaciones instaladas es de $loc : se conserva"; return }
    try { Remove-Item -LiteralPath $UninstallKey -Recurse -Force -ErrorAction Stop; Log "borrada la entrada $UninstallKey" }
    catch { Log "X no se pudo borrar $UninstallKey : $($_.Exception.Message)"; $script:fallos += $UninstallKey }
}
function Test-Enlace($item) {
    # junction o enlace simbolico (los ficheros de OneDrive tambien son "reparse points", pero no enlaces)
    return ($item -and ($item.LinkType -eq "Junction" -or $item.LinkType -eq "SymbolicLink"))
}
function Remove-AppFolder {
    # _app, carpeta por carpeta ($AppFolders): todo lo de la app menos este desinstalador (lo borra Remove-Desinstalador
    # al final, si nada ha fallado: asi, si algo falla, "Aplicaciones instaladas" puede volver a lanzarlo)
    $self = Join-Path $App "desinstalar.ps1"
    $ajenos = @()
    foreach ($sub in @($AppFolders.Keys)) {
        $dir = $(if ($sub -eq ".") { $App } else { Join-Path $App $sub })
        $d = Get-Item -LiteralPath $dir -Force -ErrorAction SilentlyContinue
        if (-not $d) { continue }
        if (Test-Enlace $d) { $ajenos += $dir; continue }  # un enlace no se recorre
        foreach ($file in @(Get-ChildItem -LiteralPath $dir -File -Force -ErrorAction SilentlyContinue)) {
            if (Same-Path $file.FullName $self) { continue }
            $rel = $(if ($sub -eq ".") { $file.Name } else { "$sub\$($file.Name)" })
            $nuestro = (-not (Test-Enlace $file)) -and
                       (($AppFolders[$sub] -contains $file.Extension.ToLowerInvariant()) -or ($CodeTxt -contains $rel))
            if (-not $nuestro) { $ajenos += $file.FullName; continue }
            $ok = $false
            for ($k = 0; $k -lt 5 -and -not $ok; $k++) {
                try { Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop; $ok = $true } catch { Start-Sleep -Milliseconds 400 }
            }
            if (-not $ok) { Log "X no se pudo borrar $($file.FullName)"; $script:fallos += $file.FullName }
        }
        if ($sub -ne ".") {  # dentro de static/export/__pycache__ la app no tiene carpetas: las que haya se quedan
            foreach ($sd in @(Get-ChildItem -LiteralPath $dir -Directory -Force -ErrorAction SilentlyContinue)) { $ajenos += $sd.FullName }
        }
        if ($sub -ne "." -and -not (Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            try { [IO.Directory]::Delete($dir) } catch { Log "X no se pudo borrar $dir"; $script:fallos += $dir }  # vacia: no recursivo
        }
    }
    # otras carpetas de _app (no las trae la app): se quedan
    foreach ($e in @(Get-ChildItem -LiteralPath $App -Directory -Force -ErrorAction SilentlyContinue)) {
        if (@($AppFolders.Keys) -notcontains $e.Name) { $ajenos += $e.FullName }
    }
    foreach ($a in $ajenos) { Log "se queda (no es de la app): $a" }
    $script:ajenos = $ajenos
}
function Remove-Desinstalador {
    # lo ultimo, y solo si todo lo demas ha ido bien: este desinstalador y _app (si ha quedado vacia)
    $self = Join-Path $App "desinstalar.ps1"
    try { Remove-Item -LiteralPath $self -Force -ErrorAction Stop } catch { Log "X no se pudo borrar $self"; $script:fallos += $self; return }
    if (-not (Get-ChildItem -LiteralPath $App -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        try { [IO.Directory]::Delete($App); Log "borrada $App" } catch { Log "X no se pudo borrar $App"; $script:fallos += $App }
    }
}
function Remove-Launchers {
    foreach ($n in $RootLaunchers) {
        $launcher = Join-Path $Root $n
        if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { continue }
        # solo los de la app (llaman a _app\...), no un .bat del usuario con el mismo nombre
        $t = "$(Get-Content -LiteralPath $launcher -Raw -ErrorAction SilentlyContinue)"
        if ($t.Length -gt 4096 -or $t -notmatch '_app\\') { Log "se conserva $launcher (no es de la app)"; continue }
        try { Remove-Item -LiteralPath $launcher -Force -ErrorAction Stop; Log "borrado $launcher" }
        catch { Log "X no se pudo borrar $launcher"; $script:fallos += $launcher }
    }
}
function Remove-Datos {
    # %LOCALAPPDATA%\AudiosApp: ajustes, API keys (DPAPI), huellas de voz, modelos de voces, registros y la ventana
    if (-not $Datos -or (Split-Path -Leaf $Datos) -ne "AudiosApp") { Log "X carpeta de datos desconocida: $Datos"; return }
    if (-not (Test-Path -LiteralPath $Datos)) { Log "no hay $Datos"; return }
    # [IO.Directory]::Delete recursivo no entra en enlaces (junction/symlink): borra el enlace, no su destino
    # (learn.microsoft.com, Directory.Delete(String, Boolean)); el borrado recursivo de PowerShell 5.1 podria entrar
    $d = Get-Item -LiteralPath $Datos -Force -ErrorAction SilentlyContinue
    if (Test-Enlace $d) { Log "X $Datos es un enlace: no se borra"; $script:fallos += $Datos; return }
    $ok = $false
    for ($k = 0; $k -lt 5 -and -not $ok; $k++) {
        try { [IO.Directory]::Delete($Datos, $true); $ok = $true } catch { Start-Sleep -Milliseconds 600 }
    }
    if ($ok) { Log "borrada $Datos" } else { Log "X no se pudo borrar del todo $Datos"; $script:fallos += $Datos }
}
function Invoke-Desinstalar($borrarDatos) {
    $script:appPid = $null
    $script:fallos = @()
    $script:ajenos = @()
    Say "Cerrando $AppName si está abierta..."
    $st = Stop-App
    if ($st -eq "grabando") {
        Log "la app esta grabando: no se toca nada"
        return "grabando"
    }
    $w = Wait-AppGone
    if ($w -ne "ok") { Log "X sigue algun proceso de la app ($w): no se toca nada"; return $w }
    Say "Quitando los accesos directos..."
    $null = Remove-Shortcuts
    if ($borrarDatos) {
        Say "Borrando ajustes, claves y voces ($Datos)..."
        Remove-Datos
    }
    Say "Borrando la aplicación ($App)..."
    Remove-AppFolder
    Remove-Launchers
    # la entrada de "Aplicaciones instaladas" y el desinstalador, lo ultimo: si algo ha fallado se quedan y se puede
    # volver a desinstalar desde alli
    if ($script:fallos.Count -gt 0) { return "incompleta" }
    Say "Quitando $AppName de Aplicaciones instaladas..."
    Remove-Registro
    if ($script:fallos.Count -gt 0) { return "incompleta" }
    Remove-Desinstalador
    if ($script:fallos.Count -gt 0) { return "incompleta" }
    return "ok"
}

function Exit-Desinstalar($code) {
    # la copia de %TEMP% se borra sola al terminar, por cualquier camino (tambien al cancelar o si algo falla)
    if ($PSCommandPath -and (Split-Path -Leaf $PSCommandPath) -like "chuleta-desinstalar-*.ps1" -and (Same-Path (Split-Path -Parent $PSCommandPath) $env:TEMP)) {
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    }
    exit $code
}

# --- 4. comprobacion: solo se desinstala una carpeta _app con su desinstalador (nunca otra cosa)
if ((Split-Path -Leaf $App) -ne "_app" -or -not (Test-Path -LiteralPath (Join-Path $App "desinstalar.ps1"))) {
    Log "X $App no es la carpeta _app de ${AppName}: no se toca nada"
    if (-not $Silencioso -and $AutoCerrar -le 0) {  # en CI un MessageBox dejaria el job colgado
        try {
            Add-Type -AssemblyName System.Windows.Forms
            $null = [System.Windows.Forms.MessageBox]::Show("No encuentro $AppName en $App. No se ha tocado nada.", "Desinstalar $AppName")
        } catch {}
    }
    Exit-Desinstalar 1
}

$gui = $false
if (-not $Silencioso) {
    $gui = New-UninstallWindow
    if (-not $gui) {
        # sin ventana no se puede preguntar (PowerShell restringido por la empresa, sin escritorio...): no se toca nada
        Log "X no se ha podido abrir la ventana: no se ha tocado nada (sin preguntar no se desinstala; -Silencioso desde una consola)"
        Exit-Desinstalar 1
    }
}
$borrar = [bool]$BorrarDatos
$resultado = $null
if ($gui) {
    while ($true) {
        $choice = Wait-Choice $(if ($AutoConfirmar) { "desinstalar" } else { "cancelar" })
        if ($choice -ne "desinstalar") {
            Log "cancelado: no se ha tocado nada"
            $script:ui.form.Close()
            Exit-Desinstalar 0
        }
        $borrar = $script:ui.chk.Checked
        $script:ui.busy = $true
        $script:ui.btnOk.Enabled = $false
        $script:ui.btnCancel.Enabled = $false
        $script:ui.chk.Enabled = $false
        $script:ui.status.ForeColor = [System.Drawing.Color]::FromArgb(37, 99, 235)
        $resultado = Invoke-Desinstalar $borrar
        $script:ui.busy = $false
        if ($resultado -ne "grabando") { break }
        # grabando: no se ha tocado nada; se puede parar la grabacion y volver a pulsar Desinstalar
        $script:ui.status.ForeColor = [System.Drawing.Color]::FromArgb(185, 28, 28)
        $script:ui.status.Text = "$AppName está grabando ahora mismo. Para la grabación y vuelve a pulsar Desinstalar (no se ha tocado nada)."
        $script:ui.btnOk.Enabled = $true
        $script:ui.btnCancel.Enabled = $true
        $script:ui.chk.Enabled = $true
        $script:ui.choice = $null
        $AutoConfirmar = $false  # CI: tras el aviso, el autocierre cancela
    }
} else {
    $resultado = Invoke-Desinstalar $borrar
}

# --- 5. final
if ($resultado -eq "ok") {
    $final = "$AppName se ha desinstalado. Tus reuniones siguen en $Root"
    if ($script:ajenos.Count -gt 0) { $final += " (en _app quedan $($script:ajenos.Count) archivos que no son de la app)" }
} elseif ($resultado -eq "grabando") {
    $final = "$AppName está grabando ahora mismo: para la grabación y vuelve a desinstalar. No se ha tocado nada."
} elseif ($resultado -eq "procesos") {
    $final = "No he podido cerrar ${AppName}: apágala (Ajustes > Apagar $AppName) y vuelve a desinstalar. No se ha tocado nada."
} else {
    $final = "La desinstalación no ha terminado: no se ha podido quitar $($script:fallos -join ', '). Tus reuniones siguen en $Root. Cierra $AppName y vuelve a desinstalar (detalle en $script:logFile)."
}
Log $final
if ($gui) {
    $ui = $script:ui
    $ui.msg.Text = $final
    $ui.keep.Visible = $false
    $ui.chk.Visible = $false
    $ui.warn.Visible = $false
    $ui.status.Text = $(if ($resultado -eq "ok" -and $borrar) { "También se han borrado tus ajustes, claves y voces guardadas." } elseif ($resultado -eq "ok") { "Tus ajustes, claves y voces siguen en $Datos (una reinstalación los vuelve a usar)." } else { "" })
    $ui.status.ForeColor = $(if ($resultado -eq "ok") { [System.Drawing.Color]::DimGray } else { [System.Drawing.Color]::FromArgb(185, 28, 28) })
    $ui.msg.ForeColor = $(if ($resultado -eq "ok") { [System.Drawing.Color]::FromArgb(22, 128, 61) } else { [System.Drawing.Color]::FromArgb(185, 28, 28) })
    $ui.msg.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
    $ui.btnOk.Visible = $false
    $ui.btnFolder.Visible = $true
    $ui.btnCancel.Text = "Cerrar"
    $ui.btnCancel.Enabled = $true
    $ui.choice = $null
    Layout-Ui
    $ui.btnCancel.Focus() | Out-Null
    $null = Wait-Choice "cerrar"
    if ($ui.form.Visible) { $ui.form.Close() }
}
if ($resultado -eq "ok") { Exit-Desinstalar 0 }
if ($resultado -eq "grabando") { Exit-Desinstalar 2 }
Exit-Desinstalar 1
