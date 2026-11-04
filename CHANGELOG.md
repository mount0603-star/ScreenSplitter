# 版本紀錄

## 4.4.0 (2026-11-04)

**原始碼模式安裝包（新的部署方式）**
- 安裝包 `ScreenSplitter_Setup_Source` 內含一份 git 工作目錄、可攜式 Python 3.11.9（已裝好釘死版本的套件）與 MinGit；程式直接以原始碼執行，不再是 PyInstaller 打包的 EXE。安裝到原位置即可，設定、Profile、網站資料不受影響；舊的 `Player\`、`AdminTool\`、`WebServer\` 程式資料夾會被移除（自訂的 `Player\music` 會留下，請搬到 `music\`）。
- 之後的更新改用開始選單「更新 ScreenSplitter」或安裝目錄的 `update.bat`：取得 GitHub 上的最新版、關閉 Player 與 WebServer、更新程式、必要時安裝套件、重新啟動。`update.bat check` 只查詢不更動；`update.bat force` 丟棄主機上對程式檔的本地修改。新主機第一次先跑 `setup_update.bat` 存一次唯讀權杖。
- `run_webserver.bat`、`run_player.bat`、`run_admin.bat`、`kill_webserver.bat` 改為優先使用安裝目錄的 `python\`，沒有時才用開發用 `venv\`，開發與主機用同一組腳本。
- WebServer 新增 `--production` 參數（或環境變數 `SCREENSPLITTER_PRODUCTION=1`）：原始碼模式也會寫 `web_server.log`、自動拉起已啟用 Web 自啟的 Player、不開重載器。
- WebServer 的程序辨識、關閉清理與 `/api/processes` 現在也認得 `pythonw.exe player.py`／`webview_player.py`；單一 Profile 的程序快照不再列出以其他 `--profile` 啟動的 Player。
- 新增 `/api/version`：回報 `VERSION` 檔、原始碼 commit 與執行模式；`/api/profiles`、`/api/diagnostics` 也附上版本。
- 版本號改由 `VERSION` 檔統一提供（兩個 Inno 腳本與 API 都讀它）。

**建置**
- 新增 `build_source_release.py`：從 GitHub 淺層複製指定的分支或標籤，組裝可攜式 Python（離線 wheel 快取）、MinGit、VLC，並用可攜式直譯器驗證匯入、Player 離螢幕 Qt 檢查與全部單元測試；`--installer` 直接編譯 `setup_source.iss`。
- PyInstaller 流程（`build_release.py` + `setup_script.iss`）保留，供不便安裝原始碼模式的機器使用。

## 4.3.9 (2026-11-04)

**網頁縮放**
- 分區的「縮放 ZOOM」倍率（0.25–5，1 = 100%）現在真的會套用到 WebView2 網頁，共用 host 與獨立模式皆適用。共用視窗直接更新倍率；獨立模式只在倍率改變時重建該分區。影片、EXE、RDP 不受影響。
- 播放中可用 Ctrl + 滾輪暫時放大縮小網頁；例行的視窗定位不會把手動倍率改回去。重新導覽或重建視窗後回到設定倍率。
- 倍率在存檔與命令列之間統一取到小數四位，避免設定值位數過多時獨立 WebView 反覆重啟。

**穩定性**
- 獨立 WebView 程序改以執行檔／腳本名稱加上完全相同的 `--title` 比對，不再用子字串；Z1 不會誤殺 Z10，也不會動到其他 Profile 或共用 host。
- 黑白畫面健康檢查改為一次擷取 16×9 取樣，不再對螢幕做 144 次讀取；視窗最小化、超出桌面、檢查點被其他視窗遮住或擷取失敗時回報「無法判定」，不當成黑畫面。

**WebServer / 管理**
- 新增選用的遠端存取限制：資料目錄建立 `api_access.json`（權杖與／或允許 IP）才啟用，沒有該檔案時行為與舊版相同；本機永遠允許，被拒請求記入 `command_audit.log`。手冊第 15 節有設定方式。
- 原始碼模式的 WebServer 不再開啟 Werkzeug 互動式除錯器（自動重載保留）。
- WebAdmin 的 ZOOM 欄位加上範圍限制與說明。

**專案維護**
- 原始碼改以 git 管理（GitHub private repo），`.gitignore` 排除現場資料、紀錄與打包產物。
- `requirements.txt` 釘死全部直接與間接套件版本（Python 3.11.9、PySide6 6.10.1、PyInstaller 6.18.0 等），全新 venv 安裝可得到完全相同的組合。
- 新增 README、本檔與 37 個單元測試（共 84 個）。

**升級方式**：依手冊第 12 節，用 `ScreenSplitter_Setup_4.3.9.exe` 安裝到原位置；設定、Profile 與網站資料不受影響。

## 4.3.8 (2026-11-02)
- 離線使用說明 `docs/usage.html` 隨安裝包提供，WebAdmin 頁首、桌面 Admin 的「使用說明」與 F1、開始選單皆可開啟。
- 4.3.5 的 Qt ICU DLL 問題：建置時限制 PATH、拒收意外的 ICU 函式庫，並對打包後的 Player 做啟動驗證。

## 4.3.7 (2026-09-29)
- 指定螢幕缺席時等待、回來後恢復原位置與場景；WebView host 延遲開窗可被 hide/close 取消；overview 地圖唯讀診斷。
