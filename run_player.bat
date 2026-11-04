@echo off
rem Start one Player per profile: default plus every profiles\<name> that has a config.json.
rem Each profile runs under its own watchdog: a clean EXIT stays closed, a crash
rem (non-zero exit code) is restarted after 5 seconds.
rem   run_player.bat            start every profile (after a 20 s wait for the displays)
rem   run_player.bat screen2    start one profile immediately
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
if not "%~1"=="" goto run_profile

rem Let Windows restore the complete display topology after sign-in. (ping, not timeout:
rem timeout exits at once when there is no console, e.g. when update.bat started us.)
ping -n 21 127.0.0.1 >nul
start "ScreenSplitter Player default" /min "%ComSpec%" /d /c call "%~f0" default
for /d %%D in ("%~dp0profiles\*") do (
    if exist "%%~fD\config.json" start "ScreenSplitter Player %%~nxD" /min "%ComSpec%" /d /c call "%~f0" "%%~nxD"
)
exit /b 0

:run_profile
"%PYW%" "%~dp0player.py" --profile "%~1"
set "PLAYER_EXIT=%ERRORLEVEL%"
if "%PLAYER_EXIT%"=="0" exit /b 0
ping -n 6 127.0.0.1 >nul
goto run_profile
