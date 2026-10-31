# Capturador de pantalla para el E2E en el runner de Windows: una captura de toda la pantalla cada N segundos
# (System.Drawing CopyFromScreen) en la carpeta indicada, con la hora en el nombre; descarta las identicas a la anterior.
# Termina a los -Minutos o cuando aparece el fichero STOP en la carpeta.
param([string]$Dir, [int]$Intervalo = 5, [int]$Minutos = 30)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
New-Item -ItemType Directory -Force $Dir | Out-Null
$fin = (Get-Date).AddMinutes($Minutos)
$prev = ""
$md5 = [System.Security.Cryptography.MD5]::Create()
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
Add-Content -LiteralPath (Join-Path $Dir "pantalla.txt") -Value ("pantalla: " + $b.Width + "x" + $b.Height + " en " + $b.X + "," + $b.Y)
while ((Get-Date) -lt $fin) {
    if (Test-Path -LiteralPath (Join-Path $Dir "STOP")) { break }
    try {
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
        $g.Dispose()
        $tmp = Join-Path $Dir "_tmp.png"
        $bmp.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        $hash = [BitConverter]::ToString($md5.ComputeHash([System.IO.File]::ReadAllBytes($tmp)))
        if ($hash -ne $prev) {
            Move-Item -LiteralPath $tmp -Destination (Join-Path $Dir ((Get-Date -Format "HH-mm-ss") + ".png")) -Force
            $prev = $hash
        } else {
            Remove-Item -LiteralPath $tmp -Force
        }
    } catch {
        Add-Content -LiteralPath (Join-Path $Dir "capturas-error.txt") -Value ((Get-Date -Format "HH:mm:ss") + " " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $Intervalo
}
