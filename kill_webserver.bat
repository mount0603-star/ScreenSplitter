@echo off
rem Stop the WebServer started from THIS folder (source mode: pythonw.exe web_server.py)
rem and, for an old packaged install, WebServer.exe. Players are not touched.
setlocal
set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = [regex]::Escape('%ROOT%'); Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $root -and $_.CommandLine -match 'web_server\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ('Stopped WebServer PID ' + $_.ProcessId) }"
taskkill /F /IM WebServer.exe /T >nul 2>&1
endlocal
