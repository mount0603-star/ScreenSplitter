@echo off
rem Start the ScreenSplitter WebServer (WebAdmin on TCP 5000) in production mode:
rem no console window, output in <DATA_DIR>\web_server.log, Players whose profile has
rem "Web autorun" enabled are started if offline.
rem Uses the portable runtime (python\) when present, otherwise the development venv.
setlocal
cd /d "%~dp0"
set "PYW=%~dp0python\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo Python runtime not found: expected python\pythonw.exe or venv\Scripts\pythonw.exe
    pause
    exit /b 1
)
start "ScreenSplitter WebServer" "%PYW%" "%~dp0web_server.py" --production
endlocal
