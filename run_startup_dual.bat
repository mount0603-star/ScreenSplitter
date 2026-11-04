@echo off
setlocal

set "APPDIR=D:\ScreenSplitter"
if exist "%~dp0player.py" set "APPDIR=%~dp0"

cd /d "%APPDIR%"

set "PYC=%APPDIR%\venv\Scripts\python.exe"
set "PYW=%APPDIR%\venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%PYC%"

rem Let Windows finish restoring display topology after login.
timeout /t 15 /nobreak >nul

rem Start Web/API server for admin, logs, and remote control.
start "ScreenSplitter Web API" /min "%PYC%" "%APPDIR%\web_server.py"

timeout /t 5 /nobreak >nul

rem Launch one Player per profile. Player has per-profile mutex protection.
start "ScreenSplitter Player default" "%PYW%" "%APPDIR%\player.py" --profile default

for /d %%D in ("%APPDIR%\profiles\*") do (
    if exist "%%~fD\config.json" (
        timeout /t 2 /nobreak >nul
        start "ScreenSplitter Player %%~nxD" "%PYW%" "%APPDIR%\player.py" --profile "%%~nxD"
    )
)

exit /b 0
