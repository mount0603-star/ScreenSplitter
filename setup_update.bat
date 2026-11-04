@echo off
rem One-time setup for update.bat on a new computer: store a read-only GitHub token so
rem the private repository can be fetched without prompting again.
rem You will be asked for a user name (your GitHub account) and a password: paste a
rem fine-grained personal access token with read-only "Contents" permission for the
rem ScreenSplitter repository. Git stores it in %USERPROFILE%\.git-credentials.
setlocal
cd /d "%~dp0"
set "GIT=%~dp0git\cmd\git.exe"
if not exist "%GIT%" set "GIT=git"
set "GIT_CONFIG_COUNT=1"
set "GIT_CONFIG_KEY_0=safe.directory"
set "GIT_CONFIG_VALUE_0=*"
set "GIT_TERMINAL_PROMPT=1"
"%GIT%" config credential.helper store
"%GIT%" remote -v
echo.
"%GIT%" fetch --depth 1 origin main
if errorlevel 1 (
    echo Fetch failed. Check the token and the network, then run this script again.
    pause
    exit /b 1
)
echo.
echo OK: credentials stored. "update.bat check" shows whether a newer version is available.
pause
endlocal
