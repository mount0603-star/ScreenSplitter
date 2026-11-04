@echo off
rem Open the desktop AdminTool (default profile only).
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
start "ScreenSplitter Admin" "%PYW%" "%~dp0admin.py"
endlocal
