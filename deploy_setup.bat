@echo off
cd /d "%~dp0"

echo 正在檢查 Python 環境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [錯誤] 找不到 python 指令，請確認該電腦已安裝 Python 並且有勾選 "Add Python to PATH"。
    pause
    exit /b
)

echo 偵測到環境路徑錯誤 (No Python at...)，正在修復...
echo 1. 清除舊的虛擬環境 (Deleting broken venv)...
if exist venv rmdir /s /q venv

echo 2. 建立新的虛擬環境 (Creating new venv)...
python -m venv venv

echo 3. 安裝必要套件 (Installing dependencies)...
"venv\Scripts\pip" install -r requirements.txt

echo ==========================================
echo  部署完成！(Setup Complete)
echo ==========================================
echo.
echo 現在您可以：
echo  1. 執行 run_admin.bat 直接開啟程式
echo  2. 執行 build.bat 在這台電腦重新打包 EXE
echo.
pause
