@echo off
cd /d "%~dp0"
echo Installing Flask...
"%~dp0venv\Scripts\pip" install flask
echo Starting Web Admin...
start http://127.0.0.1:5000
"%~dp0venv\Scripts\python" web_server.py
pause
