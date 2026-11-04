import os
import shutil
import subprocess
import sys
import json
import tempfile
from pathlib import Path
import PyInstaller.__main__

def configure_build_environment():
    """Do not let unrelated PATH tools supply same-named Qt dependencies."""
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    paths = [Path(sys.executable).parent, Path(sys.base_prefix),
             windows / "System32", windows, windows / "System32/Wbem"]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(str(path) for path in paths))


def verify_player_runtime(target_dir):
    """Require the shipped Player to load Qt and create an off-screen widget."""
    with tempfile.TemporaryDirectory(prefix="ScreenSplitter-build-check-") as temporary:
        output = Path(temporary) / "result.json"
        exe = Path(target_dir).resolve() / "Player" / "Player.exe"
        subprocess.run([str(exe), "--runtime-check-output", str(output)],
                       cwd=str(exe.parent), check=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result = json.loads(output.read_text(encoding="utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Player runtime check failed: {result}")
        print(f"Packaged Player runtime verified: {result}", flush=True)


def clean_build():
    """皜??撱箇蔭瑼? (build 鞈?憭?"""
    root = Path(__file__).resolve().parent
    target = (root / "build").resolve()
    if Path.cwd().resolve() != root or target.parent != root:
        raise RuntimeError("Build cleanup must stay inside the ScreenSplitter source directory")
    print(f"Cleaning generated build directory: {target}")
    if target.exists():
        shutil.rmtree(target)

def build_exe(script_name, name, console=False):
    # This helper is not heavily used in main, main calls run directly.
    # We will update main calls below instead.
    pass

def post_process():
    """
    撱箇蔭敺???
    1. ?渡?瑼?蝯?嚗? EXE 蝘餃??唳?蝯??澆?鞈?憭?(ScreenSplitter_Dist)??
    2. 銴ˊ敹???皞? (templates, static)??
    3. 撱箇??嫣噶???甈⊥? (.bat)??
    """
    print("Post-processing...")
    for component in ("Player", "AdminTool"):
        # Qt 6.10 uses Windows' unversioned ICU API. A bundled third-party ICU
        # (e.g. Poppler's *_78 exports) shadows it and breaks QtWidgets startup.
        bad_icu = Path("build") / "dist" / component / "_internal" / "icuuc.dll"
        if bad_icu.exists():
            raise RuntimeError(f"Unexpected bundled ICU library: {bad_icu}; rebuild with a clean PATH")
    manual_src = Path("docs") / "usage.html"
    if not manual_src.is_file():
        raise RuntimeError(f"Required offline user manual missing: {manual_src}")
    dist_dir = os.path.join("build", "dist")
    target_dir = os.path.join("build", "ScreenSplitter_Dist")
    
    # 皜征?格?鞈?憭?
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    missing_components = []
    
    # 1. 銴ˊ Admin 撌亙 (獢????
    src = os.path.join(dist_dir, "AdminTool")
    if os.path.exists(src):
        shutil.copytree(src, os.path.join(target_dir, "AdminTool"))
    else:
        missing_components.append("AdminTool")
    
    # 2. 銴ˊ Player (?剜?其蜓蝔?)
    src = os.path.join(dist_dir, "Player")
    if os.path.exists(src):
        shutil.copytree(src, os.path.join(target_dir, "Player"))
        # Copy Music if exists
        # This ensures portability (if base_dir check is used)
        if os.path.exists("music"):
             dest_music = os.path.join(target_dir, "Player", "music")
             shutil.copytree("music", dest_music, dirs_exist_ok=True)
             print(f"Copied music to {dest_music}")
        
        # [New] Copy WebViewPlayer to Player/WebViewPlayer
        src_wv = os.path.join(dist_dir, "WebViewPlayer")
        if os.path.exists(src_wv):
             dest_wv = os.path.join(target_dir, "Player", "WebViewPlayer")
             shutil.copytree(src_wv, dest_wv, dirs_exist_ok=True)
             print(f"Copied WebViewPlayer to {dest_wv}")
        else:
             missing_components.append("WebViewPlayer")

        # [New] Copy VLC bundle into Player\VLC
        src_vlc = os.path.join(os.getcwd(), "VLC")
        if os.path.exists(src_vlc):
            dest_vlc = os.path.join(target_dir, "Player", "VLC")
            shutil.copytree(src_vlc, dest_vlc, dirs_exist_ok=True)
            print(f"Copied VLC to {dest_vlc}")
        else:
            print("WARNING: VLC folder missing (expected ./VLC)")

    else:
        missing_components.append("Player")
    
    # 3. 銴ˊ WebServer (蝬脤?????
    # 瘜冽?: ??撘Ⅳ src 霈瘝??身嚗??航???批?舐?頝臬?嚗?隢??府閬???WebServer
    src_web = os.path.join(dist_dir, "WebServer") # 靽格迤霈?迂隞交?蝣?
    if os.path.exists(src_web):
        shutil.copytree(src_web, os.path.join(target_dir, "WebServer"))
        
        # ??銴ˊ templates/static 蝣箔???鞈?瑼 EXE ????
        # (閫?捱 PyInstaller????瘝??脣?楝敺隤斤???)
        try:
            shutil.copytree("templates", os.path.join(target_dir, "WebServer", "templates"), dirs_exist_ok=True)
            shutil.copytree("static", os.path.join(target_dir, "WebServer", "static"), dirs_exist_ok=True)
        except Exception as e:
            print(f"Warning copying assets: {e}")
    else:
        missing_components.append("WebServer")

    if missing_components:
        raise RuntimeError(f"Required build outputs missing: {', '.join(missing_components)}")

    manual_dir = Path(target_dir) / "docs"
    manual_dir.mkdir()
    shutil.copy2(manual_src, manual_dir / "usage.html")
    version_file = Path("VERSION")
    if version_file.is_file():
        shutil.copy2(version_file, Path(target_dir) / "VERSION")
    
    # 4. 撱箇??寧?????單 (.bat)
    create_bat(target_dir, "run_admin.bat", r'start "" "AdminTool\AdminTool.exe"')
    
    # Start one Player per saved profile. A clean EXIT must stay closed; only a
    # non-zero crash is restarted by the per-profile watchdog.
    player_script = """@echo off
setlocal
cd /d "%~dp0"

if not "%~1"=="" goto run_profile

rem Let Windows restore the complete display topology after sign-in.
timeout /t 20 /nobreak >nul
start "ScreenSplitter Player default" /min "%ComSpec%" /d /c call "%~f0" default
for /d %%D in ("%~dp0profiles\\*") do (
    if exist "%%~fD\\config.json" start "ScreenSplitter Player %%~nxD" /min "%ComSpec%" /d /c call "%~f0" "%%~nxD"
)
exit /b 0

:run_profile
"Player\\Player.exe" --profile "%~1"
set "PLAYER_EXIT=%ERRORLEVEL%"
if "%PLAYER_EXIT%"=="0" exit /b 0
timeout /t 5 /nobreak >nul
goto run_profile
"""
    create_bat_content(target_dir, "run_player.bat", player_script)
    
    create_bat(target_dir, "run_webserver.bat", r'start "" "WebServer\WebServer.exe"')
    create_bat(target_dir, "kill_webserver.bat", r'taskkill /F /IM WebServer.exe /T')
    verify_player_runtime(target_dir)
    
    print(f"Build Complete! Output folder: {os.path.abspath(target_dir)}")

def create_bat_content(folder, name, content):
    """撱箇? .bat ?寞活瑼?(?湔撖怠?批捆)"""
    path = os.path.join(folder, name)
    with open(path, "w", encoding="big5") as f:
        f.write(content)

def create_bat(folder, name, cmd):
    """撱箇?蝪∪??.bat"""
    create_bat_content(folder, name, f'@echo off\ncd /d "%~dp0"\n{cmd}\n')

if __name__ == "__main__":
    try:
        os.chdir(Path(__file__).resolve().parent)
        configure_build_environment()
        # 皜??獢?
        clean_build()
        
        # 閮剖?頝臬??
        work_path = os.path.join("build", "temp")
        dist_path = os.path.join("build", "dist")
        spec_path = os.path.join("build", "specs")
        
        # PyInstaller ??
        # --onedir: ?Ｙ????冗 (?銝 exe)嚗???敹?
        # --noconfirm: 銝岷??西???
        common_args = [
            '--workpath', work_path,
            '--distpath', dist_path,
            '--specpath', spec_path,
            '--onedir', '--noconfirm'
        ]
        
        # Icon Absolute Path (Fix for PyInstaller spec relative path issue)
        base_icon = os.path.abspath("app.ico")
        
        # Check for specific icons (admin.ico / player.ico)
        admin_icon = os.path.abspath("admin.ico")
        if not os.path.exists(admin_icon): admin_icon = base_icon
        
        player_icon = os.path.abspath("player.ico")
        if not os.path.exists(player_icon): player_icon = base_icon
        
        if not os.path.exists(base_icon):
             print(f"WARNING: Main Icon verify failed at {base_icon}")
        
        # 撱箇蔭 Admin (獢敺) - Use admin_icon
        print("Building AdminTool...")
        PyInstaller.__main__.run([
            'admin.py', '--name=AdminTool', '--noconsole',
            f'--icon={admin_icon}',
            f'--add-data={admin_icon};.',
            '--hidden-import=PySide6.QtXml',
            '--hidden-import=config_manager',
        ] + common_args)
        
        # 撱箇蔭 Player (?剜?? - Use player_icon
        print("Building Player...")
        PyInstaller.__main__.run([
            'player.py', '--name=Player', '--noconsole',
            f'--icon={player_icon}',
            f'--add-data={player_icon};.',

            '--hidden-import=PySide6.QtWebEngineCore',
            '--hidden-import=config_manager',
        ] + common_args)
        
        # 撱箇蔭 WebServer (蝬脤?敺) - Use player_icon
        print("Building WebServer...")
        try:
            # 雿輻蝯?頝臬?隞仿?楝敺圾?隤?
            templates_src = os.path.abspath("templates")
            static_src = os.path.abspath("static")
            
            # --add-data: ?鞈?瑼?
            PyInstaller.__main__.run([
                'web_server.py', '--name=WebServer', '--noconsole',
                f'--icon={player_icon}',
                f'--add-data={player_icon};.',
                f'--add-data={templates_src};templates',
                f'--add-data={static_src};static',
                '--hidden-import=config_manager',

            ] + common_args)
        except Exception as e:
             print(f"WebServer Build Error (check data paths): {e}")
             raise

        # [New] Build WebViewPlayer (Standalone EXE for WebView2)
        print("Building WebViewPlayer...")
        PyInstaller.__main__.run([
            'webview_player.py', '--name=WebViewPlayer', '--noconsole',
            # No icon needed strictly, but can reuse player icon
            f'--icon={player_icon}',
            f'--add-data={player_icon};.',
        ] + common_args)
        
        # ?瑁?敺???
        post_process()

    except Exception as e:
        print(f"BUILD FAILED: {e}")
        raise

