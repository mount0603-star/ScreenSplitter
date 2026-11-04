@echo off
rem One-time setup for update.bat on a new computer: let git remember how to reach the
rem private GitHub repository so later fetches never prompt.
rem   - With the bundled MinGit, Git Credential Manager is used: a GitHub sign-in window
rem     opens (browser login, or paste a fine-grained token with read-only "Contents"
rem     permission). The credential is kept in this Windows user's Credential Manager.
rem   - Without it, git's "store" helper asks for user name + token on the console and
rem     saves them in %USERPROFILE%\.git-credentials.
setlocal
cd /d "%~dp0"
set "GIT=%~dp0git\cmd\git.exe"
if not exist "%GIT%" set "GIT=git"
set "GIT_CONFIG_COUNT=1"
set "GIT_CONFIG_KEY_0=safe.directory"
set "GIT_CONFIG_VALUE_0=*"
set "GIT_TERMINAL_PROMPT=1"
set "HELPER=store"
if exist "%~dp0git\ucrt64\bin\git-credential-manager.exe" set "HELPER=manager"
if exist "%~dp0git\mingw64\bin\git-credential-manager.exe" set "HELPER=manager"
"%GIT%" config credential.helper %HELPER%
echo Credential helper: %HELPER%
"%GIT%" remote -v
echo.
"%GIT%" fetch --depth 1 origin main
if errorlevel 1 (
    echo Fetch failed. Check the sign-in, the token permission and the network, then run this script again.
    pause
    exit /b 1
)
echo.
echo OK: credentials stored. "update.bat check" shows whether a newer version is available.
pause
endlocal
