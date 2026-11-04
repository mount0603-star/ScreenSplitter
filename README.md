# ScreenSplitter

Windows 多分區看板輪播系統。四個元件：

| 元件 | 檔案 | 說明 |
| --- | --- | --- |
| WebServer / WebAdmin | `web_server.py`、`templates/web_admin.html` | Flask，TCP 5000，網頁編輯器、Player 控制與診斷 API |
| Player | `player.py` | PySide6 主視窗，分區、輪播、IPC、螢幕恢復、鬧鐘、排程重啟 |
| WebViewPlayer | `webview_player.py` | Microsoft Edge WebView2 host，顯示網頁與 HTML5 影片 |
| AdminTool | `admin.py` | 本機 Qt 桌面編輯器（只支援 default Profile） |

完整使用說明與交接文件在 `docs/usage.html`，安裝版的開始選單與 WebAdmin 頁首的「使用說明」開的是同一份。

## 原始碼模式執行

1. 安裝 Python 3.11.9（64 位元，勾選 Add to PATH）。
2. 執行 `deploy_setup.bat` 建立 `venv` 並安裝 `requirements.txt`。`requirements.txt` 把直接與間接相依全部釘死在這台驗證過的版本，每台機器裝出來的套件組合完全相同；要升級套件時改這個檔案，再跑測試與打包驗證。
3. 執行 `run_startup_dual.bat` 啟動 WebServer 與每個 Profile 的 Player；或分別用 `run_web_admin.bat`、`run_player.bat`、`run_admin.bat`。
4. 瀏覽器開 `http://127.0.0.1:5000/`。

現場資料（`config.json`、`profiles/`、`web_profile/`、各種 log）與打包產物都不進 git，清單見 `.gitignore`。在原始碼目錄可寫的情況下，這些資料會直接放在同一個資料夾（Portable Mode）。

遠端存取限制（選用）：在資料目錄建立 `api_access.json`，詳見手冊第 15 節。

## 測試與打包

```bat
venv\Scripts\python.exe -m unittest discover -s tests -v
venv\Scripts\python.exe build_release.py
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup_script.iss
```

`build_release.py` 產出 `build\ScreenSplitter_Dist`，Inno Setup 產出 `build\ScreenSplitter_Setup.exe`。版本號在 `setup_script.iss` 的 `MyAppVersion` 與手冊標頭，發版時一併更新。
