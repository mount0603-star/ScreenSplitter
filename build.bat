@echo off
cd /d "%~dp0"
echo Starting Full Build Process...
"venv\Scripts\python" build_release.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build Failed! Please check the error message above.
    pause
    exit /b %errorlevel%
)

echo.
echo [SUCCESS] Build Complete! Files are ready in 'build/ScreenSplitter_Dist'.
echo You can now compile 'setup_script.iss' with Inno Setup.
pause
