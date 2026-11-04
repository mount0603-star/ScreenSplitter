Add-Type -AssemblyName System.Drawing
$src = "d:\輪播程式\app.png"
$dst = "d:\輪播程式\app.ico"
try {
    $img = [System.Drawing.Bitmap]::FromFile($src)
    $handle = $img.GetHicon()
    $icon = [System.Drawing.Icon]::FromHandle($handle)
    $fs = [System.IO.File]::OpenWrite($dst)
    $icon.Save($fs)
    $fs.Close()
    $icon.Dispose()
    $img.Dispose()
    Write-Host "Success: Converted to $dst"
} catch {
    Write-Error "Failed: $_"
}
