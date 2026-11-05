@echo off
rem ScreenSplitter source-mode updater. Site data (config.json, profiles\, logs) is untouched.
rem   update.bat         fetch origin/main; if newer: stop Players/WebServer, reset, restart
rem   update.bat check   only report commits; force: also drop local edits
rem Public repo: no credentials needed (setup_update.bat only if made private again).
rem Run by POST /api/update with stdout in update.log (no console: no timeout). cmd
rem reads batch files by byte offset, so this file copies itself to %TEMP% and runs the copy.
setlocal EnableDelayedExpansion
if not defined SS_UPDATE_ROOT (
    set "SS_UPDATE_ROOT=%~dp0"
    if not defined TEMP set "TEMP=%~dp0"
    copy /y "%~f0" "!TEMP!\ScreenSplitter_update.bat" >nul
    if errorlevel 1 (echo Cannot copy update.bat to !TEMP! & exit /b 5)
    call "!TEMP!\ScreenSplitter_update.bat" %*
    exit /b !ERRORLEVEL!
)
set "ROOT=%SS_UPDATE_ROOT%"
cd /d "%ROOT%"
set "BRANCH=main"
set "GIT=%ROOT%git\cmd\git.exe"
if not exist "%GIT%" set "GIT=git"
set "PY=%ROOT%python\python.exe"
if not exist "%PY%" set "PY=%ROOT%venv\Scripts\python.exe"
rem safe.directory: accept a checkout owned by another Windows account.
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
    echo Fetch failed. Check the network ^(github.com:443^); for a private repository also the
    echo stored credentials ^(setup_update.bat^).
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
ping -n 6 127.0.0.1 >nul
echo Stopping any remaining Player / WebView processes from this folder ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = [regex]::Escape('%ROOT%'); Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $root -and $_.CommandLine -match '(player|webview_player)\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output ('Stopped PID ' + $_.ProcessId + ' ' + $_.Name) }"
call "%ROOT%kill_webserver.bat"
ping -n 3 127.0.0.1 >nul

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
rem Redirect to NUL so the restarted WebServer does not inherit update.log as its stdout.
call "%ROOT%run_webserver.bat" >nul 2>&1
start "" "%ROOT%run_player.bat" >nul 2>&1
echo Done.
endlocal
exit /b 0
