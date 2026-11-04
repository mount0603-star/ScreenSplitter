@echo off
rem ScreenSplitter source-mode updater.
rem   update.bat          fetch origin/main; if newer: stop Players and WebServer, reset the
rem                       checkout to origin/main, install changed requirements, restart.
rem   update.bat check    only report the installed and available commits (changes nothing).
rem   update.bat force    also discard local modifications to tracked files.
rem Site data (config.json, profiles\, web_profile\, logs) is untracked and never touched.
rem First-time setup on a new computer: run setup_update.bat once to store the GitHub token.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%~dp0"
set "BRANCH=main"
set "GIT=%ROOT%git\cmd\git.exe"
if not exist "%GIT%" set "GIT=git"
set "PY=%ROOT%python\python.exe"
if not exist "%PY%" set "PY=%ROOT%venv\Scripts\python.exe"
rem Accept the checkout even when another Windows account owns the folder.
set "GIT_CONFIG_COUNT=1"
set "GIT_CONFIG_KEY_0=safe.directory"
set "GIT_CONFIG_VALUE_0=*"
set "GIT_TERMINAL_PROMPT=1"

if not exist "%ROOT%.git" (
    echo This folder is not a git checkout: %ROOT%
    exit /b 1
)
if not exist "%PY%" (
    echo Python runtime not found: expected python\python.exe or venv\Scripts\python.exe
    exit /b 1
)

for /f "usebackq delims=" %%H in (`"%GIT%" rev-parse HEAD`) do set "CUR=%%H"
echo Installed commit : !CUR:~0,12!
if exist "%ROOT%VERSION" for /f "usebackq delims=" %%V in ("%ROOT%VERSION") do echo Installed version: %%V

echo Fetching origin/%BRANCH% ...
"%GIT%" fetch --depth 1 origin %BRANCH%
if errorlevel 1 (
    echo Fetch failed. Check the network and the stored credentials ^(setup_update.bat^).
    exit /b 3
)
for /f "usebackq delims=" %%H in (`"%GIT%" rev-parse origin/%BRANCH%`) do set "NEW=%%H"
echo Available commit : !NEW:~0,12!
if /i "%~1"=="check" exit /b 0
if "%CUR%"=="%NEW%" (
    echo Already up to date.
    exit /b 0
)

set "DIRTY="
for /f "usebackq delims=" %%L in (`"%GIT%" status --porcelain --untracked-files=no`) do set "DIRTY=1"
if defined DIRTY if /i not "%~1"=="force" (
    echo Local modifications exist in tracked files. They would be lost by the update:
    "%GIT%" status --short --untracked-files=no
    echo Commit and push them from the development machine, or run "update.bat force" to discard them.
    exit /b 2
)

echo Stopping Players through the WebServer API ...
"%PY%" -c "import json,urllib.request; r=urllib.request.Request('http://127.0.0.1:5000/api/cmd?scope=all', data=json.dumps({'cmd':'EXIT','scope':'all'}).encode(), headers={'Content-Type':'application/json'}); print(urllib.request.urlopen(r, timeout=10).read().decode('utf-8', 'replace')[:400])" 2>nul
timeout /t 5 /nobreak >nul
echo Stopping any remaining Player / WebView processes from this folder ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = [regex]::Escape('%ROOT%'); Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $root -and $_.CommandLine -match '(player|webview_player)\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output ('Stopped PID ' + $_.ProcessId + ' ' + $_.Name) }"
call "%ROOT%kill_webserver.bat"
timeout /t 2 /nobreak >nul

echo Updating checkout to origin/%BRANCH% ...
"%GIT%" reset --hard origin/%BRANCH%
if errorlevel 1 (
    echo git reset failed; nothing was restarted.
    exit /b 4
)

set "REQ_CHANGED="
for /f "usebackq delims=" %%L in (`"%GIT%" diff --name-only %CUR% %NEW% -- requirements.txt`) do set "REQ_CHANGED=1"
if defined REQ_CHANGED (
    echo requirements.txt changed; installing packages ^(needs internet^) ...
    "%PY%" -m pip install --no-warn-script-location -r "%ROOT%requirements.txt"
    if errorlevel 1 echo WARNING: pip install failed; the programs may not start until it succeeds.
    rem The portable runtime loads pywin32's DLLs from beside python.exe (no site.py bootstrap).
    if exist "%ROOT%python\Lib\site-packages\pywin32_system32\*.dll" copy /y "%ROOT%python\Lib\site-packages\pywin32_system32\*.dll" "%ROOT%python\" >nul
)

if exist "%ROOT%VERSION" for /f "usebackq delims=" %%V in ("%ROOT%VERSION") do echo Updated to version %%V ^(!NEW:~0,12!^)
echo Restarting WebServer and Players ...
call "%ROOT%run_webserver.bat"
start "" "%ROOT%run_player.bat"
echo Done.
endlocal
exit /b 0
