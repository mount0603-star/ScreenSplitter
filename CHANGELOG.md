# 版本紀錄

## 4.4.3 (2026-11-05)

**update.bat 更新自己時的錯位**
- 4.4.2 在主機上的第一次網頁更新成功，但 update.log 出現一行「'OOTkill_webserver.bat"' 不是內部或外部命令」與重複的 Updating checkout：cmd 依位元組位置逐行讀批次檔，`git reset` 改寫了正在執行的 update.bat，之後的行就錯位。這次只是多跑一次無害的指令，換個改法可能漏掉重啟。
- `update.bat` 現在先把自己複製到 `%TEMP%\ScreenSplitter_update.bat` 再執行複本，之後任何對 update.bat 的修改都能安全套用。4.4.3 的檔案在 `git reset` 那一行之後與 4.4.2 位元組完全相同（由產生腳本驗證），所以 4.4.2 的舊腳本升級到 4.4.3 時也不會錯位。
- 新增測試 `tests/test_update_bat.py`；手冊第 12、14 節補充說明。程式碼其餘部分與 4.4.1 相同。

## 4.4.2 (2026-11-05)

**主機免登入 GitHub**
- GitHub repo 改為公開：輪播主機查版本與更新不再需要任何 GitHub 帳號或憑證，裝好即可從輪播控制台更新；`setup_update.bat` 只在 repo 改回私有時才需要。手冊第 12、14 節與 README 配合修改。
- 手冊移除現場 IP 與螢幕型號等站點細節（第 14 節歷史案例、第 15 節範例），並說明徽章「檢查失敗」的判讀。
- 程式碼與 4.4.1 相同；update.bat 只改了 Fetch failed 的提示文字。

## 4.4.1 (2026-11-04)

**在輪播控制台更新主機**
- WebAdmin 頁首顯示主機目前版本與 commit；WebServer 每 10 分鐘（及開頁時）用隨包 git 向 GitHub 查 main 分支，有新版時顯示「有新版本 x.y.z」與「更新主機」按鈕。
- 按下後主機在背景執行 `update.bat`（輸出到 `update.log`），頁面顯示更新中、等 WebServer 重啟回來後顯示已更新；未完成時列出 update.log 末段。
- 新 API：`GET /api/update/status`（`?refresh=1` 強制重查）、`POST /api/update`（`{"force": true}` 等同 update.bat force）；受 api_access.json 保護並記入 command_audit.log。打包版回報 supported=false。
- `update.bat` 改用 ping 代替 timeout 等待，可在沒有主控台的情況下由 WebServer 啟動。

## 4.4.0 (2026-11-04)

**原始碼模式安裝包（新的部署方式）**
- 安裝包 `ScreenSplitter_Setup_Source` 內含一份 git 工作目錄、可攜式 Python 3.11.9（已裝好釘死版本的套件）與 MinGit；程式直接以原始碼執行，不再是 PyInstaller 打包的 EXE。安裝到原位置即可，設定、Profile、網站資料不受影響；舊的 `Player\`、`AdminTool\`、`WebServer\` 程式資料夾會被移除（自訂的 `Player\music` 會留下，請搬到 `music\`）。
- 之後的更新改用開始選單「更新 ScreenSplitter」或安裝目錄的 `update.bat`：取得 GitHub 上的最新版、關閉 Player 與 WebServer、更新程式、必要時安裝套件、重新啟動。`update.bat check` 只查詢不更動；`update.bat force` 丟棄主機上對程式檔的本地修改。新主機第一次先跑 `setup_update.bat` 登入 GitHub 一次（隨包 MinGit 內建 Git Credential Manager，憑證存在 Windows 認證管理員）。
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
