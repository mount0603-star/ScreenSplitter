import sys
import os
import json
import time
import datetime
import shutil
import subprocess
import psutil
import tempfile
import traceback
import ctypes
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# [Fix] Enable Autoplay, Disable Security, and Force GPU/WebGL for BIM/Video
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--autoplay-policy=no-user-gesture-required "
    "--check-for-update-interval=31536000 "
    "--disable-web-security "
    "--ignore-gpu-blocklist "
    "--enable-gpu-rasterization "
    "--enable-zero-copy "
    "--enable-features=VaapiVideoDecoder,VaapiVideoEncoder "
    "--enable-webgl "
    "--enable-accelerated-video-decode"
)

# [DEBUG] Force Unbuffered Output for Logging
class Unbuffered(object):
   def __init__(self, stream):
       self.stream = stream
       self.verbose_debug = os.environ.get("SCREENSPLITTER_VERBOSE_DEBUG", "").strip() == "1"
       self.suppressed_debug_newline = False
   def write(self, data):
       try:
           text = str(data)
           if not self.verbose_debug and "[DEBUG]" in text:
               self.suppressed_debug_newline = True
               return
           if self.suppressed_debug_newline and text in ("\n", "\r\n"):
               self.suppressed_debug_newline = False
               return
           self.suppressed_debug_newline = False
           self.stream.write(data)
           self.stream.flush()
       except: pass # Handle closed streams during shutdown
   def writelines(self, datas):
       try:
           self.stream.writelines(datas)
           self.stream.flush()
       except: pass
   def __getattr__(self, attr):
       return getattr(self.stream, attr)

if sys.stdout: sys.stdout = Unbuffered(sys.stdout)
if sys.stderr: sys.stderr = Unbuffered(sys.stderr)
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QFrame, QPushButton, QHBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings
from PySide6.QtCore import QUrl, QFileSystemWatcher, Qt, QTimer, QRect
import config_manager
import win32gui
import win32con
import win32process
import win32api
from PySide6.QtGui import QCursor, QMouseEvent, QAction, QIcon
from PySide6.QtCore import QEvent, QPoint


def _extract_cli_value(name, default=None):
    try:
        prefix = name + "="
        for idx, arg in enumerate(sys.argv[1:], start=1):
            if arg == name and idx + 1 < len(sys.argv):
                return sys.argv[idx + 1]
            if isinstance(arg, str) and arg.startswith(prefix):
                return arg.split("=", 1)[1]
    except Exception:
        pass
    return default


PLAYER_PROFILE_ID = config_manager.set_active_profile(
    _extract_cli_value("--profile", os.environ.get("SCREENSPLITTER_PROFILE", "default"))
)
CONTROL_PROFILE_ID = getattr(config_manager, "DEFAULT_PROFILE_ID", "default")


def _is_control_profile(profile_id):
    return config_manager.sanitize_profile_id(profile_id) == CONTROL_PROFILE_ID


def wait_for_previous_player(pid, created_at, timeout=60):
    """Wait before taking the profile mutex or opening its logs. No shell involved."""
    try:
        previous = psutil.Process(int(pid))
        if previous.create_time() == float(created_at):
            previous.wait(timeout=timeout)
    except psutil.NoSuchProcess:
        pass


def append_scene_cache_buster(url, token):
    """Return an HTTP(S) URL that is unique for one scene activation."""
    raw_url = str(url or "").strip()
    try:
        parts = urlsplit(raw_url)
        if parts.scheme.lower() not in ("http", "https"):
            return raw_url

        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key != "_screensplitter"
        ]
        query.append(("_screensplitter", str(token)))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    except (TypeError, ValueError):
        return raw_url


# Hidden advanced WebView host tuning (not exposed in WebAdmin UI)
WEBVIEW_HOST_RECOVERY_MODE = "restart"
WEBVIEW_HOST_ALLOW_FALLBACK = False
WEBVIEW_MISSING_TIMEOUT_SEC = 20.0
WEBVIEW_HOST_RESTART_COOLDOWN_SEC = 8.0
WEBVIEW_HOST_FALLBACK_COOLDOWN_SEC = 60.0
WEBVIEW_TAG_COOLDOWN_SEC = 10.0
WEBVIEW_RENDER_GRACE_SEC = 5.0
WEBVIEW_RENDER_CHECK_INTERVAL_SEC = 2.0
WEBVIEW_RENDER_FLAT_SAMPLE_LIMIT = 3
WEBVIEW_RENDER_RESTART_DELAY_SEC = 14.0

# [Fix] Safe window text read to avoid hangs on unresponsive windows
def safe_get_window_text(hwnd):
    try:
        # Skip hung windows to avoid blocking the UI thread
        if win32gui.IsHungAppWindow(hwnd):
            return ""
    except Exception:
        pass
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""

# [Refactor] QtMultimedia is optional. Some target PCs crash on multimedia plugin load.
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
except Exception:
    QMediaPlayer = None
    QAudioOutput = None

try:
    import winsound
except Exception:
    winsound = None

class IgnoreCertPage(QWebEnginePage):
    """
    Custom WebEnginePage to ignore SSL Certificate Errors
    (e.g. Self-signed or Expired certs)
    """
    def certificateError(self, error):
        print(f"[SSL Warning] Ignoring invalid cert for: {error.url()}")
        # Return True to indicate we choose to ignore the error and proceed
        return True

class PlayerControls(QWidget):
    """
    ?剜?冽?園??
    憿舐內 銝????怠?/銝????? ???瘚株?蝒?
    """
    def __init__(self, parent=None):
        super().__init__(None) # Make it top-level independent (?函?閬?嚗?鋡怎閬?鋆?)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground) # Required for rounded corners (?????)
        
        # High-Tech Style: Pill Shape, Dark Gradient, Neon Border (蝘????Ｘ見撘?
        self.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1a2e, stop:1 #050510);
                border: 2px solid #00f3ff; 
                border-radius: 50px; 
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(30, 15, 30, 15) # More horizontal padding for pill shape
        layout.setSpacing(15)
        
        self.btn_prev = QPushButton("⏮ 上一頁")
        self.btn_pause = QPushButton("⏸ 暫停/播放")
        self.btn_next = QPushButton("⏭ 下一頁")
        self.btn_exit = QPushButton("❌ 關閉播放")
        
        for btn in [self.btn_prev, self.btn_pause, self.btn_next, self.btn_exit]:
            btn.setStyleSheet("""
                QPushButton { 
                    color: #00f3ff; 
                    font-weight: bold; 
                    font-size: 20px; 
                    padding: 10px 20px; 
                    background: rgba(0, 243, 255, 0.1); 
                    border: 1px solid #00f3ff; 
                    border-radius: 8px;
                }
                QPushButton:hover { 
                    background-color: #00f3ff; 
                    color: #000;
                    /* box-shadow not supported in Qt stylesheet */
                }
                QPushButton:pressed {
                    background-color: #00bfcc;
                }
            """)
            btn.setCursor(Qt.PointingHandCursor)
            layout.addWidget(btn)
            
        self.hide()

class FloatingTrigger(QWidget):
    """
    ?豢筑?? (Assistive Touch)
    撟喳虜蝮桀撌虫?閫?暺?敺?急?園?踴?
    ?????Z-Order ?踹??格??嗡?閬???
    """
    def __init__(self, parent_player):
        # Set Parent to ensure we are "Owned" by Player. 
        # When Player goes to background, we follow.
        # But we must Set Qt.Window to be floating.
        # 閮剖??嗉?蝒 Player嚗Ⅱ靽?撅祆 Player ?黎蝯?(????Player 蝮桀?/?曉之)
        super().__init__(parent_player) 
        self.player = parent_player
        
        # Remove WindowStaysOnTopHint
        # Set Qt.Window to be top-level relative to desktop (but owned by parent)
        # 閮剖??箄?蝒芋撘?(Windows)嚗蝙?嗅隞交筑??嗉?蝒?銝?
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # Style: Semi-transparent circle
        self.setStyleSheet("""
            background-color: rgba(0, 243, 255, 0.2); 
            border: 1px solid rgba(255, 255, 255, 0.5); 
            border-radius: 20px; 
            color: white; 
            font-weight: bold;
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.label = QLabel("☰") # Menu Icon
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: rgba(255,255,255,0.8); font-size: 20px;")
        layout.addWidget(self.label)
        
        self.setFixedSize(60, 60)
        self.setCursor(Qt.PointingHandCursor)
        
        # Position: Bottom-Right by default (will be set by player)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.player.toggle_controls()
            
    def enterEvent(self, event):
        self.setStyleSheet("""
            background-color: rgba(0, 243, 255, 0.8); 
            border: 1px solid white; 
            border-radius: 20px;
        """)
        
    def leaveEvent(self, event):
        self.setStyleSheet("""
            background-color: rgba(0, 243, 255, 0.2); 
            border: 1px solid rgba(255, 255, 255, 0.5); 
            border-radius: 20px;
        """)

class PlayerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screen Player V4")
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        # Set Window Icon (Try player.ico first, then app.ico)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_candidates = ["player.ico", "app.ico"]
        for icon_name in icon_candidates:
            icon_path = os.path.join(base_dir, icon_name)
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
                break
        
        # Fullscreen / Frameless Logic
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        
        self.config = {}
        self.playlist = []
        self.current_scene_idx = 0
        
        # State
        self.zone_widgets = {} # map zone_id -> QWidget (Webview or Frame)
        self.media_players = {} # Keep references to players
        self.active_exe_paths = set() # Paths currently being displayed
        self.all_managed_paths = set() # All EXE paths across all scenes
        self.web_view_cache = {} # URL -> QWebEngineView (Cache for preloading)
        self.hwnd_first_seen = {} # HWND -> Timestamp (For sizing enforcement)
        self.hwnd_first_seen = {} # HWND -> Timestamp (For sizing enforcement)
        self.known_hwnds = set() # Set of HWNDs that we have already enforced at least once
        self.owned_hwnds = set() # Set of HWNDs we have set Owner for (Z-order fix)
        
        # We need a persistent map of {ExePath: ProcessName} to track windows
        # or just rely on path lookup.
        
        # IPC (??Web Admin ??)
        # ?孵?雿輻蝔???函??蝣箔?銝?雿蔭?瑁???撘?賭???
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))
            
        # Data Dir (Writer Target). Each profile has isolated IPC/log/WebView data.
        self.profile_id = config_manager.get_active_profile()
        self.control_profile_id = CONTROL_PROFILE_ID
        self.is_control_profile = _is_control_profile(self.profile_id)
        self.data_dir = config_manager.get_data_dir(self.profile_id)
        os.makedirs(self.data_dir, exist_ok=True)
            
        self.ipc_cmd_path = os.path.join(self.data_dir, "ipc_cmd.txt")
        self.ipc_status_path = os.path.join(self.data_dir, "ipc_status.json")
        self.log_path = os.path.join(self.data_dir, "player_debug.log") # Debug Log
        
        self.log_debug(f"Player Init. BaseDir: {self.base_dir}")
        self.log_debug(f"Profile: {self.profile_id}")
        self.log_debug(f"Control Profile: {self.control_profile_id} (is_control={self.is_control_profile})")
        self.log_debug(f"Data Dir: {self.data_dir}")
        self.log_debug(f"IPC Paths: {self.ipc_cmd_path}, {self.ipc_status_path}")
        
        self.paused = False
        
        # Self-Healing Cooldown (?脫迫?芣?靽桀儔????蝥???
        self.last_fix_time = 0 
        
        # Ensure clean state
        if os.path.exists(self.ipc_cmd_path):
            try: os.remove(self.ipc_cmd_path)
            except: pass

        self.scene_timer = QTimer()
        self.scene_timer.timeout.connect(self.next_scene)
        
        self.last_reload = time.time()
        # Watch USER config in Data Dir
        config_path = config_manager.get_config_path(self.profile_id)
        if not os.path.exists(config_path):
            try:
                config_manager.ensure_profile(self.profile_id)
            except Exception:
                pass
        self.watcher = QFileSystemWatcher([config_path])
        self.watcher.fileChanged.connect(self.on_file_changed)
        
        # Setup Web Profile paths (lazy init to avoid QtWebEngine startup hangs)
        self.web_profile = None
        self.web_profile_dir = os.path.join(self.data_dir, "web_profile")
        if not os.path.exists(self.web_profile_dir):
            os.makedirs(self.web_profile_dir)

        # External VLC settings (video)
        self.vlc_path = self.find_vlc_path()
        self._vlc_pids = {}
        self._vlc_hwnds = {}
        self._vlc_paths = {}
        self._vlc_suspended = set()
        self._vlc_last_launch = {}

        # WebView host mode runtime state
        self._webview_host_pid = None
        self._webview_host_exe = None
        self._webview_host_cmd_path = os.path.join(self.data_dir, "webview_cmd.json")
        self._webview_host_status_path = os.path.join(self.data_dir, "webview_status.json")
        self._webview_host_disabled_until = 0.0
        self._webview_host_last_launch_time = 0.0
        self._webview_host_last_restart_time = 0.0
        self._webview_host_last_tags = set()
        self._webview_pending_stale_host_tags = {}
        self._webview_pending_stale_isolated_tags = {}
        self._webview_host_status_cache = ({}, 0.0)
        self._webview_render_health = {}
        self._webview_last_render_check = 0.0
        self._active_host_webview_tags = set()

        # Read config early for screen geometry, but defer scene loading until UI is ready.
        try:
            startup_cfg = config_manager.load_config()
            if isinstance(startup_cfg, dict):
                self.config = startup_cfg
                self._enforce_required_global_settings()
                self.playlist = self.config.get("playlist", [])
        except:
            pass

        self.enforce_timer = QTimer()
        self.enforce_timer.timeout.connect(self.enforce_loop)
        # Fix: Start timer at END of init to avoid AttributeError
        # self.enforce_timer.start(500) 
        
        
        # Alarm Setup
        self.log_debug("Initializing Alarms...")
        self.alarm_player = None
        self.alarm_audio = None
        self.alarm_use_beep_fallback = winsound is not None
        if QMediaPlayer is None or QAudioOutput is None:
            if self.alarm_use_beep_fallback:
                self.log_debug("QtMultimedia unavailable. Using winsound alarm fallback.")
                self.alarms_enabled = True
            else:
                self.log_debug("QtMultimedia unavailable. Alarms disabled.")
                self.alarms_enabled = False
        else:
            try:
                self.alarm_player = QMediaPlayer()
                self.alarm_audio = QAudioOutput()
                self.alarm_player.setAudioOutput(self.alarm_audio)
                self.alarm_audio.setVolume(1.0)
                self.alarms_enabled = True
                self.log_debug("Audio Output Initialized.")
            except Exception as e:
                self.alarm_player = None
                self.alarm_audio = None
                if self.alarm_use_beep_fallback:
                    self.alarms_enabled = True
                    self.log_debug(f"Audio Init Failed. Using winsound fallback: {e}")
                else:
                    self.alarms_enabled = False
                    self.log_debug(f"Audio Init Failed (Alarms Disabled): {e}")

        if not self.is_control_profile:
            self.alarms_enabled = False
            self.log_debug(
                f"Alarms disabled for profile '{self.profile_id}'. "
                f"Scheduled audio is controlled by '{self.control_profile_id}'."
            )

        self.last_alarm_date = None 
        self.played_alarms = set() 
        self._suspend_webview_raise_until = 0.0
        
        self.alarm_timer = QTimer()
        self.alarm_timer.timeout.connect(self.check_alarms)
        if self.is_control_profile:
            self.alarm_timer.start(5000) # Check every 5s
            self.log_debug("Alarm Timer Started.")
        else:
            self.log_debug("Alarm Timer not started for secondary profile.")

        # IPC Timer
        self.ipc_timer = QTimer()
        self.ipc_timer.timeout.connect(self.check_ipc)
        self.ipc_timer.start(500) 
        self.log_debug("IPC Timer Started.")

        # Mouse Auto-Hide
        self.setMouseTracking(True)
        self.cursor_timer = QTimer()
        self.cursor_timer.timeout.connect(self.hide_cursor)
        self.cursor_timer.start(3000)
        self.log_debug("Cursor Timer Started.")

        # Long Press for Touch (2 seconds)
        self.long_press_timer = QTimer()
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.timeout.connect(self.toggle_controls)
        self.press_start_pos = None

        self.log_debug("Initializing Controls...")
        self.controls = PlayerControls(self)
        self.controls.btn_prev.clicked.connect(self.action_prev)
        self.controls.btn_pause.clicked.connect(self.action_toggle_pause)
        self.controls.btn_next.clicked.connect(self.action_next)
        self.controls.btn_exit.clicked.connect(self.force_quit)
        
        # Floating Trigger (Assistive Touch)
        self.log_debug("Initializing Floating Trigger...")
        self.trigger_btn = None
        if self.is_control_profile:
            self.trigger_btn = FloatingTrigger(self)
            self.trigger_btn.show()
        else:
            self.log_debug("Floating Trigger disabled for secondary profile.")
        
        # Install Event Filter for global mouse tracking (even over webviews)
        # Must be done AFTER controls are created
        QApplication.instance().installEventFilter(self)
        
        # Resolution Handling: monitor changes on all screens.
        self._connect_screen_change_signals()
        
        # Move to target screen if configured 
        glob = self.config.get("global_settings", {})
        sx = glob.get("screen_x")
        sy = glob.get("screen_y")
        w = glob.get("screen_width", 1920)
        h = glob.get("screen_height", 1080)
        
        if sx is not None and sy is not None:
             try:
                 sx, sy = int(sx), int(sy)
                 target_screen = self.get_configured_screen()
                 if target_screen:
                     geo = target_screen.geometry()
                     sx, sy, w, h = geo.left(), geo.top(), geo.width(), geo.height()
                 # Force geometry (Position + Size) BEFORE showFullScreen
                 # This helps Qt decide which screen to maximize on
                 self.setGeometry(sx, sy, int(w), int(h))
                 self.log_debug(
                      f"Set Geometry to {sx},{sy} {w}x{h} (Qt logical), "
                      f"config canvas {glob.get('screen_width', 1920)}x{glob.get('screen_height', 1080)}"
                 )
             except: pass
        
        self._show_fullscreen_on_target()
        
        # Initial trigger pos
        QTimer.singleShot(100, self.update_trigger_pos)

        # Load scenes after fullscreen settles, then run startup relayout checks.
        # This avoids rare cases where Windows/Qt reports transient geometry right after restart.
        QTimer.singleShot(300, self.load_config)
        QTimer.singleShot(2200, self._startup_layout_recheck)
        QTimer.singleShot(5200, self._startup_layout_recheck)

        if not hasattr(self, '_enforce_busy'):
             self._enforce_busy = False
        
        # [Fix] Rate Limit for Strip Borders to prevent fighting with external apps
        if not hasattr(self, '_last_strip_time'):
             self._last_strip_time = {}

        # Start Enforce Timer LAST (to ensure all UI is ready)
        self.log_debug("Starting Enforce Timer...")
        self.enforce_timer.start(500)
        
        # Scheduled Restart (Memory Leak Prevention)
        # 3D/WebGL context often leaks memory over time.
        # We preemptively restart the app to clear all resources.
        # Paired with the .bat watchdog, this creates a robust loop.
        self.restart_timer = QTimer()
        self.restart_timer.timeout.connect(self.scheduled_restart)
        self._restart_interval_minutes = None
        self.apply_scheduled_restart_timer(reason="Scheduled Restart Timer", force=True)

    def apply_scheduled_restart_timer(self, reason="Scheduled Restart Timer Updated", force=False):
        if not hasattr(self, "restart_timer"):
            return
        restart_minutes = self.get_scheduled_restart_minutes()
        prev_minutes = getattr(self, "_restart_interval_minutes", None)

        if restart_minutes <= 0:
            if self.restart_timer.isActive():
                self.restart_timer.stop()
            self._restart_interval_minutes = 0
            if force or prev_minutes != 0:
                self.log_debug(f"{reason}: disabled")
            return

        interval_ms = restart_minutes * 60 * 1000

        # IMPORTANT: restart() the timer when interval changes, so new setting takes effect immediately
        # and does not inherit the remaining countdown from the previous interval.
        if force or not self.restart_timer.isActive() or prev_minutes != restart_minutes:
            self.restart_timer.stop()
            self.restart_timer.start(interval_ms)
            self._restart_interval_minutes = restart_minutes
            self.log_debug(f"{reason}: every {restart_minutes} minutes")

    def scheduled_restart(self):
        self.log_debug("Performing Scheduled Restart (Memory Cleanup)...")
        if self._schedule_self_relaunch():
            QApplication.instance().quit()
        else:
            self.log_debug("Restart cancelled; keeping current Player running.")

    def _schedule_self_relaunch(self):
        """
        Relaunch the same profile after the current process exits.

        The WebServer starts Player.exe directly, so there may be no watchdog
        batch file to bring the player back after a scheduled memory cleanup.
        """
        try:
            profile_id = getattr(self, "profile_id", PLAYER_PROFILE_ID)
            if getattr(sys, "frozen", False):
                relaunch_args = [sys.executable, "--profile", profile_id]
                relaunch_cwd = os.path.dirname(sys.executable)
            else:
                script_path = os.path.abspath(__file__)
                relaunch_args = [sys.executable, script_path, "--profile", profile_id]
                relaunch_cwd = os.path.dirname(script_path)

            relaunch_args += [
                "--wait-for-exit", str(os.getpid()),
                "--wait-for-created", str(psutil.Process().create_time()),
            ]
            env = os.environ.copy()
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            log_path = os.path.join(config_manager.get_data_dir(profile_id), "restart.log")
            with open(log_path, "a", encoding="utf-8", buffering=1) as restart_log:
                restart_log.write(f"{time.ctime()}: Relaunch {relaunch_args!r}\n")
                child = subprocess.Popen(
                    relaunch_args,
                    cwd=relaunch_cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=restart_log,
                    stderr=restart_log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    close_fds=True,
                )

            self.log_debug(f"Scheduled self relaunch for profile '{profile_id}', child PID={child.pid}")
            return True
        except Exception as e:
            self.log_debug(f"Failed to schedule self relaunch: {e}")
            return False

    def _enforce_required_global_settings(self):
        """
        Force critical runtime features always ON.
        These are kiosk-safety defaults and are no longer user-toggleable in WebAdmin.
        """
        if not isinstance(self.config, dict):
            self.config = {}
        glob = self.config.get("global_settings")
        if not isinstance(glob, dict):
            glob = {}
            self.config["global_settings"] = glob

        glob["cleanup_prev_scene"] = True
        glob["webview_host_mode"] = True
        # All web content should use the external WebView2 host by default.
        # Older configs may omit "use_edge"; treating that as QtWebEngine caused
        # MP4/web playback instability and repeated heavy reloads.
        glob["force_webview2_for_all_web"] = True
        # Default to closing inactive WebViews. Keeping many hidden 4K pages alive
        # costs too much GPU/CPU and causes visible show/move/resize flicker.
        glob.setdefault("keep_inactive_webviews", False)
        # Refresh a web page when its scene becomes visible again. This keeps
        # dashboards current without keeping every inactive Chromium window alive.
        glob.setdefault("reload_web_on_scene_enter", True)
        glob.setdefault("webview_host_stagger_ms", 700)
        # Keep the previous WebView visible only during scene handoff. This
        # avoids black gaps without permanently retaining hidden pages.
        glob.setdefault("webview_transition_grace_ms", 2500)
        # Host IPC must remain profile-local. Old configs may contain shared
        # cmd/status/profile paths from earlier builds, which lets screen2 attach
        # to or terminate the default profile host.
        for key in ("webview_host_cmd_path", "webview_host_status_path", "webview_host_profile_path"):
            glob.pop(key, None)

    def _should_use_external_webview(self, zone):
        """
        Decide whether a web zone should be rendered by the external WebView2 host.
        Legacy configs often do not contain use_edge=true, so global default is ON.
        A zone may explicitly set use_internal_qt=true for the old QtWebEngine path.
        """
        if not isinstance(zone, dict):
            return False
        if zone.get("type", "web") != "web":
            return False
        if bool(zone.get("use_internal_qt", False)):
            return False
        glob = self.config.get("global_settings", {}) if isinstance(self.config, dict) else {}
        force_all = bool(glob.get("force_webview2_for_all_web", True))
        if force_all:
            return True
        return bool(zone.get("use_edge", False))

    def _webview_tag_should_keep_alive(self, tag):
        try:
            if not bool(self.config.get("global_settings", {}).get("keep_inactive_webviews", False)):
                return False
            tag_url = getattr(self, "_webview_host_tag_url", {}).get(tag, "")
            if self._webview_reload_on_show(tag_url):
                return False
            tag_kind = getattr(self, "_webview_host_tag_kind", {}).get(tag, "web")
            return tag_kind == "web"
        except Exception:
            return False

    def _webview_reload_on_show(self, url):
        """
        Reload web content after it has been hidden and then shown again.
        Video carousel pages always reload because their media pipeline does not
        reliably resume after a display sleep or scene transition.
        """
        if self._webview_needs_viewport_fix(url):
            return True
        try:
            glob = self.config.get("global_settings", {}) if isinstance(self.config, dict) else {}
            return bool(glob.get("reload_web_on_scene_enter", True))
        except Exception:
            return True

    def _webview_needs_viewport_fix(self, url):
        """
        Some SPA video pages keep running timers while Chromium/WebView2 preserves
        an old viewport after hide/show or DPI changes. Limit the fix to known
        pages so normal dashboards and EXE/MP4 playback are not affected.
        """
        try:
            return "video-carousel" in str(url or "").lower()
        except Exception:
            return False

    def _zone_uses_isolated_webview(self, zone, url=None):
        """
        Optional per-zone escape hatch for WebView2 pages whose media pipeline
        does not recover after hide/show inside the shared host.
        """
        try:
            if not isinstance(zone, dict):
                return False
            return bool(
                zone.get("isolate_webview", False)
                or zone.get("dedicated_webview", False)
                or zone.get("standalone_webview", False)
            )
        except Exception:
            return False

    def _webview_transition_grace_seconds(self):
        try:
            glob = self.config.get("global_settings", {}) if isinstance(self.config, dict) else {}
            return max(0.0, float(glob.get("webview_transition_grace_ms", 2500) or 0) / 1000.0)
        except Exception:
            return 2.5

    def _find_webview_tag_hwnd(self, tag, require_visible=False):
        if not tag:
            return None
        try:
            cached = getattr(self, "_webview_tag_hwnds", {}).get(tag)
            if cached and win32gui.IsWindow(cached):
                if (not require_visible) or win32gui.IsWindowVisible(cached):
                    return cached
        except Exception:
            pass

        found = None
        try:
            def _title_cb(hwnd, _):
                nonlocal found
                try:
                    if require_visible and not win32gui.IsWindowVisible(hwnd):
                        return True
                    title = safe_get_window_text(hwnd) or ""
                    if tag in title:
                        found = hwnd
                        return False
                except Exception:
                    pass
                return True
            win32gui.EnumWindows(_title_cb, None)
        except Exception:
            return None

        if found:
            try:
                if not hasattr(self, "_webview_tag_hwnds"):
                    self._webview_tag_hwnds = {}
                self._webview_tag_hwnds[tag] = found
            except Exception:
                pass
        return found

    def _read_webview_host_status(self, max_cache_age=0.35):
        now = time.time()
        try:
            cached, cached_at = getattr(self, "_webview_host_status_cache", ({}, 0.0))
            if cached and now - float(cached_at or 0.0) <= max_cache_age:
                return cached
        except Exception:
            pass

        path = getattr(self, "_webview_host_status_path", None)
        data = {}
        try:
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception as e:
            print(f"[WebView Health] Failed to read host status: {e}")
        self._webview_host_status_cache = (data, now)
        return data

    def _webview_tags_ready(self, tags):
        """Return True only after host windows have loaded and passed a DOM probe."""
        try:
            tag_list = [t for t in (tags or set()) if t]
            if not tag_list:
                return True
            status = self._read_webview_host_status()
            details = status.get("window_status", {}) if isinstance(status, dict) else {}
            host_pid = getattr(self, "_webview_host_pid", None)
            tag_pids = getattr(self, "_webview_pids", {})
            for tag in tag_list:
                is_host_tag = bool(host_pid and tag_pids.get(tag) == host_pid)
                if is_host_tag:
                    item = details.get(tag)
                    if not isinstance(item, dict):
                        return False
                    if not item.get("shown", False) or item.get("state") != "healthy":
                        return False
                if not self._find_webview_tag_hwnd(tag, require_visible=True):
                    return False
            return True
        except Exception:
            return False

    def _sample_visible_webview_surface(self, hwnd):
        """Classify the composited on-screen window as content, pure black, or pure white."""
        try:
            if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return {"state": "unavailable", "samples": 0}
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = int(right - left)
            height = int(bottom - top)
            if width < 120 or height < 90:
                return {"state": "unavailable", "samples": 0}

            hdc = win32gui.GetDC(0)
            if not hdc:
                return {"state": "unavailable", "samples": 0}
            get_pixel = ctypes.windll.gdi32.GetPixel
            get_pixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            get_pixel.restype = ctypes.c_uint32
            black = 0
            white = 0
            valid = 0
            sums = [0, 0, 0]
            try:
                cols, rows = 16, 9
                for row in range(rows):
                    py = top + int(height * (row + 0.5) / rows)
                    for col in range(cols):
                        px = left + int(width * (col + 0.5) / cols)
                        color = int(get_pixel(ctypes.c_void_p(hdc), px, py))
                        if color == 0xFFFFFFFF:
                            continue
                        red = color & 0xFF
                        green = (color >> 8) & 0xFF
                        blue = (color >> 16) & 0xFF
                        valid += 1
                        sums[0] += red
                        sums[1] += green
                        sums[2] += blue
                        if max(red, green, blue) <= 5:
                            black += 1
                        if min(red, green, blue) >= 250:
                            white += 1
            finally:
                win32gui.ReleaseDC(0, hdc)

            if valid < 40:
                return {"state": "unavailable", "samples": valid}
            black_ratio = black / valid
            white_ratio = white / valid
            averages = [round(value / valid, 1) for value in sums]
            state = "content"
            if black_ratio >= 0.985:
                state = "black"
            elif white_ratio >= 0.985:
                state = "white"
            return {
                "state": state,
                "samples": valid,
                "black_ratio": round(black_ratio, 4),
                "white_ratio": round(white_ratio, 4),
                "average_rgb": averages,
            }
        except Exception as e:
            return {"state": "capture_error", "samples": 0, "error": str(e)}

    def _check_active_webview_render(self, active_tags):
        """Recover a host window only after repeated all-black/all-white screen samples."""
        now = time.time()
        if now - float(getattr(self, "_webview_last_render_check", 0.0) or 0.0) < WEBVIEW_RENDER_CHECK_INTERVAL_SEC:
            return
        self._webview_last_render_check = now
        self._active_host_webview_tags = set(active_tags or set())
        if not active_tags:
            return

        status = self._read_webview_host_status(max_cache_age=0.0)
        details = status.get("window_status", {}) if isinstance(status, dict) else {}
        health_map = getattr(self, "_webview_render_health", {})

        for tag in sorted(active_tags):
            host_item = details.get(tag, {}) if isinstance(details, dict) else {}
            if not isinstance(host_item, dict) or host_item.get("state") != "healthy":
                continue
            try:
                loaded_at = float(host_item.get("loaded_at", 0.0) or 0.0)
            except Exception:
                loaded_at = 0.0
            if loaded_at <= 0 or now - loaded_at < WEBVIEW_RENDER_GRACE_SEC:
                continue
            hwnd = self._find_webview_tag_hwnd(tag, require_visible=True)
            if not hwnd:
                continue

            sample = self._sample_visible_webview_surface(hwnd)
            item = health_map.setdefault(tag, {
                "blank_count": 0,
                "attempts": 0,
                "last_action_at": 0.0,
                "loaded_at": loaded_at,
            })
            if item.get("loaded_at") != loaded_at:
                item["blank_count"] = 0
                item["loaded_at"] = loaded_at
                if now - float(item.get("last_action_at", 0.0) or 0.0) > 60.0:
                    item["attempts"] = 0
            item["last_sample"] = sample
            item["last_sample_at"] = now

            surface_state = sample.get("state")
            if surface_state not in ("black", "white"):
                item["blank_count"] = 0
                if now - float(item.get("last_action_at", 0.0) or 0.0) > 12.0:
                    item["attempts"] = 0
                continue

            item["blank_count"] = int(item.get("blank_count", 0) or 0) + 1
            if item["blank_count"] == 1:
                self.log_debug(f"[WebView Health] {tag} first {surface_state} sample: {sample}")
            if item["blank_count"] < WEBVIEW_RENDER_FLAT_SAMPLE_LIMIT:
                continue

            attempts = int(item.get("attempts", 0) or 0)
            last_action_at = float(item.get("last_action_at", 0.0) or 0.0)
            if attempts <= 0:
                reason = f"visible_{surface_state}_{item['blank_count']}_samples"
                self.log_debug(f"[WebView Health] Reloading {tag}: {reason}; sample={sample}")
                print(f"[WebView Health] Reloading {tag}: {reason}")
                self.send_webview_host_cmds([{"op": "reload", "tag": tag, "reason": reason}])
                item["attempts"] = 1
                item["last_action_at"] = now
                item["blank_count"] = 0
            elif now - last_action_at >= WEBVIEW_RENDER_RESTART_DELAY_SEC:
                reason = f"{tag} stayed {surface_state} after reload"
                self.log_debug(f"[WebView Health] Restarting host: {reason}; sample={sample}")
                print(f"[WebView Health] Restarting host: {reason}")
                item["attempts"] = attempts + 1
                item["last_action_at"] = now
                item["blank_count"] = 0
                self.restart_webview_host(reason=reason)

        self._webview_render_health = health_map

    def _kill_webview_tag(self, tag):
        """Kill a standalone WebViewPlayer instance by tag without touching the shared host."""
        if not tag:
            return
        try:
            import psutil
        except Exception:
            return

        host_pid = getattr(self, "_webview_host_pid", None)
        killed = False

        try:
            pid = getattr(self, "_webview_pids", {}).get(tag)
            if pid and pid != host_pid:
                proc = psutil.Process(pid)
                if proc.is_running():
                    print(f"[WebView] Killing isolated stale tag {tag} (PID: {pid})")
                    proc.kill()
                    killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception as e:
            print(f"[WebView] Kill cached PID failed for {tag}: {e}")

        try:
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    pid = p.info.get("pid")
                    if pid == host_pid:
                        continue
                    name = (p.info.get("name") or "").lower()
                    if "python" not in name and "webviewplayer" not in name:
                        continue
                    cmdline = p.info.get("cmdline") or []
                    cmd_str = " ".join(cmdline)
                    if tag in cmd_str and ("webview_player.py" in cmd_str.lower() or "webviewplayer" in name):
                        print(f"[WebView] Killing isolated process {tag} (PID: {pid})")
                        p.kill()
                        killed = True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        except Exception as e:
            print(f"[WebView] Process scan kill failed for {tag}: {e}")

        for attr in ("_webview_pids", "_webview_tag_hwnds", "_webview_missing_since", "_webview_tag_last_launch"):
            try:
                mapping = getattr(self, attr, None)
                if isinstance(mapping, dict):
                    mapping.pop(tag, None)
            except Exception:
                pass

        try:
            match = self._webview_tag_pattern().search(str(tag))
            if match and hasattr(self, "_scene_nav_tracker"):
                scene_idx = int(match.group(1))
                zone_idx = int(match.group(2))
                if self._scene_nav_tracker[0] == scene_idx:
                    self._scene_nav_tracker[1].discard(zone_idx)
        except Exception:
            pass

        if killed:
            try:
                self.log_debug(f"[WebView] Isolated tag stopped: {tag}")
            except Exception:
                pass

    def get_scheduled_restart_minutes(self):
        self._enforce_required_global_settings()
        try:
            mins = int(self.config.get("global_settings", {}).get("scheduled_restart_minutes", 60))
        except Exception:
            mins = 60
        return max(0, min(1440, mins))

    def _managed_exe_name(self, path):
        try:
            return os.path.basename(str(path or "")).lower()
        except Exception:
            return ""

    def _is_project_dashboard_path(self, path):
        return self._managed_exe_name(path) in {
            "projectdashboard.exe",
            "projectdashboard_backup.exe",
        }

    def _is_ivms_path(self, path):
        name = self._managed_exe_name(path)
        return "ivms" in name or "ivms" in str(path or "").lower()

    def _should_skip_owner(self, path):
        # Electron/Chromium-like dashboards and iVMS can flicker/white-screen when
        # re-parented as owned windows. Keep z-order control explicit instead.
        return self._is_project_dashboard_path(path) or self._is_ivms_path(path)

    def _should_soft_hide_inactive_app(self, path):
        # Do not SW_HIDE/off-screen dashboards; some renderers do not repaint
        # reliably after being hidden and restored.
        return self._is_project_dashboard_path(path)

    def _is_rejected_ivms_helper(self, target_path, proc_name, proc_exe):
        if not self._is_ivms_path(target_path):
            return False

        candidate = f"{proc_name or ''} {proc_exe or ''}".lower()
        helper_markers = (
            "decodeprocess",
            "crashserverdamon",
            "alarmcenter",
            "devicemanagement",
        )
        return any(marker in candidate for marker in helper_markers)

    def _refresh_external_window_surface(self, hwnd, path, force_redraw=False):
        """
        Keep fragile external apps visible after scene switches.
        This is intentionally limited to the apps we know can get stuck white/behind.
        """
        if not (self._is_project_dashboard_path(path) or self._is_ivms_path(path)):
            return

        try:
            if not hwnd or not win32gui.IsWindow(hwnd):
                return

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.03)

            if not win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                time.sleep(0.03)

            flags = (
                win32con.SWP_NOACTIVATE
                | win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_SHOWWINDOW
                | 0x4000  # SWP_ASYNCWINDOWPOS
            )
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            # Keep these fullscreen external apps above the Player placeholders
            # while they are active. Dropping back to NOTOPMOST can intermittently
            # expose the black QFrame underneath on multi-monitor walls.

            if not hasattr(self, "_external_redraw_at"):
                self._external_redraw_at = {}
            redraw_key = (int(hwnd), self._managed_exe_name(path))
            now = time.time()
            if force_redraw or now - self._external_redraw_at.get(redraw_key, 0.0) > 2.0:
                self._external_redraw_at[redraw_key] = now
                try:
                    redraw_flags = 0x0001 | 0x0004 | 0x0080 | 0x0100  # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW
                    win32gui.RedrawWindow(hwnd, None, None, redraw_flags)
                except Exception:
                    try:
                        win32gui.InvalidateRect(hwnd, None, True)
                        win32gui.UpdateWindow(hwnd)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[DEBUG] External surface refresh failed for {path}: {e}")

    def _hwnd_matches_exact_target_exe(self, hwnd, path, track_pid=None):
        """
        Validate a cached HWND before reusing it for a generic EXE.
        Cache reuse is intentionally strict to avoid latching onto helper windows
        from the same folder (e.g. crash reporter / daemon windows).
        """
        try:
            if not hwnd or not win32gui.IsWindow(hwnd):
                return False
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid or pid == os.getpid():
                return False

            target_name = os.path.basename(path).lower()
            target_path = ""
            if path and os.path.isabs(path):
                target_path = os.path.normcase(os.path.normpath(os.path.abspath(path))).lower()

            proc = psutil.Process(pid)
            name = (proc.name() or "").lower()
            exe = ""
            try:
                exe = (proc.exe() or "").lower()
            except Exception:
                exe = ""

            if self._is_rejected_ivms_helper(path, name, exe):
                return False

            if track_pid and pid == track_pid:
                return True

            if target_path and exe:
                exe_norm = os.path.normcase(os.path.normpath(exe)).lower()
                if exe_norm == target_path:
                    return True

            if name == target_name:
                return True

            if exe and os.path.basename(exe) == target_name:
                return True
        except Exception:
            pass

        return False

    def _get_process_integrity_level(self, pid):
        try:
            import win32security
            process_query_limited_information = 0x1000
            token_integrity_level = 25
            hproc = win32api.OpenProcess(process_query_limited_information, False, int(pid))
            htok = win32security.OpenProcessToken(hproc, win32con.TOKEN_QUERY)
            info = win32security.GetTokenInformation(htok, token_integrity_level)
            sid = info[0]
            sid_str = win32security.ConvertSidToStringSid(sid)
            rid = int(str(sid_str).split("-")[-1])
            if rid >= 0x4000:
                return "system"
            if rid >= 0x3000:
                return "high"
            if rid >= 0x2000:
                return "medium"
            if rid >= 0x1000:
                return "low"
            return f"0x{rid:x}"
        except Exception:
            return None

    def _log_window_access_denied(self, path, hwnd=None, pid=None, action="window control"):
        try:
            if not hasattr(self, "_window_access_denied_warned"):
                self._window_access_denied_warned = set()

            key = (str(path).lower(), int(pid or 0), action)
            if key in self._window_access_denied_warned:
                return
            self._window_access_denied_warned.add(key)

            target_level = self._get_process_integrity_level(pid) if pid else None
            self_level = self._get_process_integrity_level(os.getpid())
            extra = ""
            if target_level or self_level:
                extra = f" player={self_level or '?'} target={target_level or '?'}"

            msg = (
                f"[Permission] Access denied while controlling window for {path} "
                f"(HWND={hwnd}, PID={pid}, action={action})."
                f"{extra} Likely Windows UIPI/elevation mismatch."
            )
            print(msg)
            self.log_debug(msg)
        except Exception:
            pass

    def force_quit(self):
        """
        User manually requested exit.
        We must KILL the parent watchdog (cmd.exe) to prevent it from restarting us.
        """
        self.log_debug("User requested FORCE EXIT. Attempting to kill watchdog...")
        try:
            parent = psutil.Process(os.getpid()).parent()
            if parent:
                parent_name = parent.name().lower()
                self.log_debug(f"Parent Process: {parent_name} (PID: {parent.pid})")
                
                # Only kill if it looks like a shell/batch runner
                if "cmd" in parent_name or "powershell" in parent_name:
                    print(f"Killing Parent Watchdog: {parent_name}")
                    parent.kill()
        except Exception as e:
            print(f"Failed to kill parent: {e}")
            
        # Finally quit self
        QApplication.instance().quit()

    def update_trigger_pos(self):
        # Position Bottom-Left
        if not getattr(self, 'trigger_btn', None): return
        h = self.height()
        w = self.width()
        # Debug Log
        # print(f"[Trigger] Win Size: {w}x{h}, Placing at 20, {h-60}")
        self.trigger_btn.move(20, h - 120)
        self._raise_trigger_topmost()

    def _raise_trigger_topmost(self):
        """Keep the floating menu above topmost external content windows."""
        try:
            if not hasattr(self, 'trigger_btn') or not self.trigger_btn or not self.trigger_btn.isVisible():
                return
            self.trigger_btn.raise_()
            trigger_hwnd = int(self.trigger_btn.winId())
            flags = (
                win32con.SWP_NOACTIVATE
                | win32con.SWP_NOMOVE
                | win32con.SWP_SHOWWINDOW
                | 0x4000  # SWP_ASYNCWINDOWPOS
            )
            win32gui.SetWindowPos(trigger_hwnd, win32con.HWND_TOPMOST, 0, 0, 60, 60, flags)
        except:
            pass

    def _startup_layout_recheck(self):
        """
        Re-validate layout shortly after startup.
        Some systems momentarily report unstable geometry right after process restart.
        """
        if self._suspend_for_missing_screen():
            return
        if getattr(self, "_loading_scene", False):
            QTimer.singleShot(300, self._startup_layout_recheck)
            return

        try:
            if self._restore_configured_screen_geometry("startup relayout"):
                QTimer.singleShot(700, self._startup_layout_recheck)
                return

            screen = self.get_active_screen()
            geo = screen.geometry() if screen else QApplication.primaryScreen().geometry()
            sw, sh = int(geo.width()), int(geo.height())
            lw, lh = getattr(self, "_last_layout_screen_size", (0, 0))
            ww, wh = int(self.width()), int(self.height())

            # Window too small or layout built against a different screen size.
            size_changed = abs(sw - lw) > 40 or abs(sh - lh) > 40
            window_too_small = ww < (sw - 80) or wh < (sh - 80)

            if size_changed or window_too_small:
                print(
                    f"[Startup Relayout] Rebuild layout. "
                    f"screen={sw}x{sh}, last_layout={lw}x{lh}, window={ww}x{wh}"
                )
                try:
                    self._show_fullscreen_on_target()
                except Exception:
                    pass

                if self.playlist and 0 <= self.current_scene_idx < len(self.playlist):
                    self.load_scene(self.current_scene_idx)
                else:
                    self.enforce_loop()
        except Exception as e:
            print(f"[Startup Relayout] Check failed: {e}")

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseMove:
            self.show_cursor()
            # Cancel long press if dragged
            if self.press_start_pos:
                try:
                    curr_pos = event.globalPosition().toPoint()
                    if (curr_pos - self.press_start_pos).manhattanLength() > 50: # 50px tolerance
                        self.long_press_timer.stop()
                        self.press_start_pos = None
                except: pass

        elif event.type() == QEvent.MouseButtonPress:
            # Start timer for Left Button
            if event.button() == Qt.LeftButton:
                try:
                    self.press_start_pos = event.globalPosition().toPoint()
                    self.long_press_timer.start(2000) # 2 seconds
                except: pass
                
        elif event.type() == QEvent.MouseButtonRelease:
            # Cancel timer
            self.long_press_timer.stop()
            self.press_start_pos = None
            
        elif event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.toggle_controls()
                return True
        return super().eventFilter(obj, event)

    def on_full_screen_requested(self, request):
        # Accept web fullscreen request (allows element to fill the View)
        print("WEB FULLSCREEN REQUESTED! Accepting.")
        request.accept()

    def on_render_crashed(self, webview, status, exit_code):
        """Handle Web Process Crashes (White Screen) -> Auto Reload"""
        print(f"[CRASH] Web Process Terminated! Status: {status}, Code: {exit_code}")
        print("[CRASH] Attempting Auto-Reload in 2s...")

        def _recover():
            try:
                webview.reload()
            except Exception as e:
                print(f"[CRASH] Reload failed: {e}")
            self.schedule_scene_layout_recovery("render crash", delay_ms=3200)

        QTimer.singleShot(2000, _recover)

    def schedule_scene_layout_recovery(self, reason="runtime recovery", delay_ms=0):
        """Re-apply the current scene layout after transient Web/DPI failures."""
        def _do_recover():
            if getattr(self, "_loading_scene", False):
                QTimer.singleShot(400, _do_recover)
                return
            try:
                if self.playlist and 0 <= self.current_scene_idx < len(self.playlist):
                    print(f"[Recovery] Rebuilding current scene layout after {reason}: {self.current_scene_idx}")
                    self.load_scene(self.current_scene_idx)
            except Exception as e:
                print(f"[Recovery] Scene layout recovery failed: {e}")

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, _do_recover)
        else:
            _do_recover()

    def on_webview_load_finished(self, webview, ok):
        if ok:
            try:
                webview.show()
            except Exception:
                pass
            return

        url = ""
        try:
            url = webview.url().toString()
        except Exception:
            pass
        print(f"[WEB] Load failed, scheduling recovery: {url}")

        def _reload_failed_webview():
            try:
                webview.reload()
            except Exception as e:
                print(f"[WEB] Reload after failed load failed: {e}")

        QTimer.singleShot(2000, _reload_failed_webview)
        self.schedule_scene_layout_recovery("web load failure", delay_ms=3500)

    def show_cursor(self):
        self.setCursor(Qt.ArrowCursor)
        self.cursor_timer.start(3000) # Reset timer
        
    def hide_cursor(self):
        # Don't hide if controls are visible
        if not self.controls.isVisible():
            self.setCursor(Qt.BlankCursor)

    # [Refactor] Helper for HTML5 Video
    def create_video_html(self, video_path):
        """
        Creates a temporary HTML file to play the video using HTML5 Video Tag with GPU acceleration.
        Returns the file URL string.
        """
        try:
            if not os.path.exists(video_path):
                print(f"[Video Error] File not found: {video_path}")
                return ""

            # Use QUrl for robust path handling (spaces, encoding)
            abs_path = os.path.abspath(video_path)
            video_url = QUrl.fromLocalFile(abs_path).toString()
            print(f"[Video HTML] Source URL: {video_url}")
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ margin: 0; padding: 0; background: black; overflow: hidden; width: 100vw; height: 100vh; }}
                    video {{ width: 100%; height: 100%; object-fit: cover; }}
                </style>
            </head>
            <body>
                <video src="{video_url}" autoplay loop muted playsinline></video>
            </body>
            </html>
            """
            
            # Create a dedicated temp directory
            temp_dir = os.path.join(tempfile.gettempdir(), "ScreenSplitter_Video")
            os.makedirs(temp_dir, exist_ok=True)
            
            # Use filename as key to allow multiple distinct videos
            # We use a hash of the full path to avoid collisions if files have same name in diff folders
            import hashlib
            name_hash = hashlib.md5(abs_path.encode('utf-8')).hexdigest()
            target_file = os.path.join(temp_dir, f"video_{name_hash}.html")
            
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(html_content)
                
            # Return PATH, not URL, to avoid double-encoding in load_scene
            return target_file
        except Exception as e:
            print(f"[Video Gen Error] {e}")
            return ""

    # [Deprecated] Old maximize_window logic was here
    def maximize_window(self):
        # Don't hide if controls are visible
        if not self.controls.isVisible():
            self.setCursor(Qt.BlankCursor)

    def toggle_controls(self):
        if self.controls.isVisible():
            self.controls.hide()
        else:
            # Center and show - Larger Dimensions
            w = 800  # Wider
            h = 100  # Taller
            cx = (self.width() - w) // 2
            cy = self.height() - h - 100 # Higher up from bottom 
            self.controls.setGeometry(cx, cy, w, h)
            self.controls.show()
            self.controls.raise_()
            self.show_cursor()
        
        # Avoid Z-order "fight" flicker right after user interaction
        try:
            # Give UI a moment to settle; avoid Z-order "fight" flicker
            self._suspend_webview_raise_until = time.time() + 3.0
        except: pass
            
        # Ensure trigger stays on top
        if getattr(self, 'trigger_btn', None):
            self.trigger_btn.raise_()

    def resizeEvent(self, event):
        if self.controls.isVisible():
            self.toggle_controls() # Re-center
        self.update_trigger_pos()
        super().resizeEvent(event)

    def _scene_duration_seconds(self, scene):
        try:
            duration = int(scene.get("duration", 10))
        except Exception:
            duration = 10
        return max(0, duration)

    def _start_scene_timer(self, scene, reason="scene timer"):
        if getattr(self, "_screen_waiting", False):
            self.scene_timer.stop()
            return False
        duration = self._scene_duration_seconds(scene)
        if duration <= 0:
            self.scene_timer.stop()
            print(f"[Timer] {reason}: duration=0, staying on current scene")
            return False
        self.scene_timer.start(duration * 1000)
        return True

    def _tag_profile_part(self):
        try:
            value = getattr(self, "profile_id", "default")
            return "".join(ch if ch.isalnum() else "_" for ch in str(value)) or "default"
        except Exception:
            return "default"

    def _webview_tag(self, scene_idx, zone_idx):
        return f"WebView2_{self._tag_profile_part()}_S{scene_idx}_Z{zone_idx}"

    def _vlc_tag(self, scene_idx, zone_idx):
        return f"VLC_{self._tag_profile_part()}_S{scene_idx}_Z{zone_idx}"

    def _webview_tag_pattern(self):
        import re
        return re.compile(rf"WebView2_{re.escape(self._tag_profile_part())}_S(\d+)_Z(\d+)")

    def _rect_to_physical_desktop(self, rx, ry, rw, rh, scale_x, scale_y, screen_geo=None):
        """
        Convert a zone-local Qt/logical rect to desktop coordinates for
        external native windows.

        The screen origin is already a virtual desktop coordinate. Only the
        zone-local offset/size should be scaled by DPR. Multiplying the origin
        pushes secondary monitors too far right when that monitor is 125%/150%
        scaled, e.g. 2560 becomes 3200 at 125% and leaves a black gap.
        """
        try:
            ox = screen_geo.left() if screen_geo else 0
            oy = screen_geo.top() if screen_geo else 0
        except Exception:
            ox = 0
            oy = 0
        return (
            int(ox + (rx * scale_x)),
            int(oy + (ry * scale_y)),
            max(1, int(rw * scale_x)),
            max(1, int(rh * scale_y)),
        )

    def action_prev(self):
        if self.playlist:
            self.current_scene_idx = (self.current_scene_idx - 1 + len(self.playlist)) % len(self.playlist)
            self.load_scene(self.current_scene_idx)

    def action_next(self):
        self.next_scene()

    def action_toggle_pause(self):
        if self.paused:
            self.paused = False
            self.controls.btn_pause.setText("⏸ 暫停/播放")
            # Resume timer
            if self.playlist:
                scene = self.playlist[self.current_scene_idx]
                self._start_scene_timer(scene, reason="resume")
        else:
            self.paused = True
            self.controls.btn_pause.setText("⏸ 暫停/播放")
            self.scene_timer.stop() 

    def on_file_changed(self, path):
        # Debounce
        QTimer.singleShot(200, self.load_config)

    def load_config(self):
        """
        頛閮剖?瑼?
        霈??config.json嚗蒂閫?????閬恣?? EXE 頝臬?嚗誑靘?蝥??憿舐內雿輻??
        """
        self.config = config_manager.load_config()
        if not isinstance(self.config, dict):
            self.config = {}
        self._enforce_required_global_settings()
        self.playlist = self.config.get("playlist", [])

        # Hot-apply scheduled restart interval when config changes.
        if hasattr(self, "restart_timer"):
            self.apply_scheduled_restart_timer(reason="Scheduled Restart Timer Updated")
        
        # Collect all managed paths (?園????臭葉?箇?? EXE)
        self.all_managed_paths = set()
        for scene in self.playlist:
            for zone in scene.get("zones", []):
                z_type = zone.get("type")
                content = zone.get("content")
                if z_type == "exe" and content:
                    self.all_managed_paths.add(content)
                elif z_type == "rdp":
                    self.all_managed_paths.add("mstsc.exe")
                elif z_type == "web" and False: # Disabled: zone.get("use_edge", False):
                    # Add browser to managed paths (chrome.exe or msedge.exe)
                    # [Fix] Track WebView helper process for hide/show/cleanup
                    # In frozen builds we launch WebViewPlayer.exe, in dev we launch python.exe
                    if getattr(sys, 'frozen', False):
                        self.all_managed_paths.add("WebViewPlayer.exe")
                    else:
                        self.all_managed_paths.add("python.exe")
                    
                    browser_type = zone.get("browser_type", "chrome").lower()
                    # [Fix] Fratricide Prevention:
                    # Do NOT add msedge.exe/chrome.exe to managed paths if using Embedded WebView (python.exe).
                    # If we track them here, the 'Hide Inactive' loop will find the embedded window (as msedge)
                    # and HIDE it because 'msedge.exe' is technically inactive (python.exe is active).
                    # if browser_type == "edge":
                    #     self.all_managed_paths.add("msedge.exe")
                    # else:
                    #     self.all_managed_paths.add("chrome.exe")  # Default to chrome
        
        if not self.playlist:
            self.load_empty()
            return
            
        # Validate index (蝣箔?蝝Ｗ???)
        if self.current_scene_idx >= len(self.playlist):
            self.current_scene_idx = 0
            
        self.load_scene(self.current_scene_idx)

    def load_empty(self):
        # Clear everything
        self.zone_widgets = {}
        self.zone_widgets = {}
        # self.media_players = {} # [Refactor] No longer used
        
        # Show Error/Empty Label
        label = QLabel(self.central_widget)
        label.setText("No Content / Config Empty\nWait for Config Update...")
        label.setStyleSheet("color: white; font-size: 32px; background: black;")
        label.setAlignment(Qt.AlignCenter)
        label.setGeometry(0, 0, self.width(), self.height())
        label.show()
        self.zone_widgets["error"] = label
        
    def next_scene(self):
        if not self.playlist: return
        self.current_scene_idx = (self.current_scene_idx + 1) % len(self.playlist)
        self.load_scene(self.current_scene_idx)

    def load_scene(self, index):
        self.log_debug(f"Entering load_scene: {index}")
        """
        頛?孵??湔
        1. 皜?? UI ?辣 (靽? Web Cache)??        2. ?迫??敶梁??剜?具?
        3. ?身閬??????
        4. ?寞? config 閮? Grid Layout??
        """
        if index < 0 or index >= len(self.playlist): return
        if self._suspend_for_missing_screen():
            self.current_scene_idx = index
            return
        # This token changes only when a scene is entered. The 500 ms enforce loop
        # reuses it, so cache-busted pages refresh once without reload thrashing.
        self._scene_load_token = time.time_ns()
        load_succeeded = False
        
        try:
            # Prevent enforce_loop from running while we mutate widgets/state
            self._loading_scene = True
            if hasattr(self, 'enforce_timer') and self.enforce_timer.isActive():
                self.enforce_timer.stop()

            # Cleanup previous scene playback (WebView/VLC) to free memory
            try:
                prev_idx = self.current_scene_idx
                if prev_idx != index:
                    self.cleanup_previous_scene(prev_idx, index)
            except Exception as e:
                print(f"[Cleanup] Previous scene cleanup failed: {e}")

            # Update current index so next/prev works from here
            self.current_scene_idx = index
            
            scene = self.playlist[index]
            print(f"Loading Scene: {scene.get('name')}")
            
            # 1. Clear Internal UI & Players (皜??隞?
            # We recreate them to ensure clean state, but preserve Cached WebViews (?遣 UI嚗?靽?蝬脤?敹怠?隞亙??賢?)
            for w in self.zone_widgets.values():
                w.hide()
                # Only delete if NOT in cache
                is_cached = False
                for cached_view in self.web_view_cache.values():
                    if w == cached_view:
                        is_cached = True
                        break
                if not is_cached:
                    w.deleteLater()
            self.zone_widgets = {}
            
            # Stop and clear old players (?迫?蔣??
            # Stop and clear old players (?迫?蔣??
            # [Refactor] Native players removed. WebViews handle video now.
            # for p in self.media_players.values():
            #     p.stop()
            #     p.setSource(QUrl()) # Detach
            #     p.deleteLater() # Explicit cleanup
            # self.media_players = {}
            
            # ... (Existing code for Active EXEs) ...
            
            # [Memory Optimization] Cache Cleanup (LRU-like)
            # Limit cache size to avoid OOM on long runs with many videos/pages
            MAX_CACHE_SIZE = 5
            if len(self.web_view_cache) > MAX_CACHE_SIZE:
                # remove oldest accessed
                # We need to track access time better, but for now remove those NOT in current scene
                # and not in next scene preloaded.


                
                # Simple cleanup: Remove items not used in current or next scene
                # until size is acceptable.
                
                # Identify URLs needed now and soon
                needed_urls = set()
                # Current scene
                for z in scene.get("zones", []):
                    if z.get("type") == "web" and z.get("content"):
                        needed_urls.add(z.get("content"))
                
                # Next scene
                next_idx = (index + 1) % len(self.playlist)
                for z in self.playlist[next_idx].get("zones", []):
                     if z.get("type") == "web" and z.get("content"):
                        needed_urls.add(z.get("content"))
                        
                # Identify candidates for removal
                to_remove = []
                for url, wv in self.web_view_cache.items():
                    if url not in needed_urls:
                        to_remove.append(url)
                        
                # Remove
                for url in to_remove:
                     if len(self.web_view_cache) <= MAX_CACHE_SIZE: break
                     print(f"[Memory] Cleaning up web cache for: {url}")
                     wv = self.web_view_cache.pop(url)
                     wv.deleteLater()

            # [Nuclear Option 3.0] Process-Based Cleanup (Transition Logic)

            # 2. Identify Active EXEs for this scene (蝣箄?甇文?舫?閬鈭?EXE)
            # [Fix] Do not clear managed paths list, so we can track and cleanup EXEs from previous scenes
            if not hasattr(self, 'all_managed_paths'):
                self.all_managed_paths = set()
            self.known_hwnds.clear() # Reset known windows for this scene to ensure forced resize (?身閬???誑撘瑕?摰?)
            self.owned_hwnds.clear() # Reset ownership tracking
            
            # 3. Setup Scene Dimensions (閮??Ｗ???Grid)
            self._restore_configured_screen_geometry("load scene")
            active_screen = self.get_configured_screen() or self.get_active_screen()
            screen_geo = active_screen.geometry() if active_screen else QApplication.primaryScreen().geometry()
            sw = screen_geo.width()
            sh = screen_geo.height()
            rows = scene.get("rows", 1)
            cols = scene.get("cols", 1)
            zones = scene.get("zones", [])
            glob = self.config.get("global_settings", {})
            video_backend = str(glob.get("video_backend", "webview") or "webview").lower()
            
            # Scale Calcs
            conf_sw = self.config.get("global_settings", {}).get("screen_width", 1920)
            conf_sh = self.config.get("global_settings", {}).get("screen_height", 1080)
            actual_sw = sw
            actual_sh = sh
            self._last_layout_screen_size = (int(actual_sw), int(actual_sh))
            try:
                dpr = float(active_screen.devicePixelRatio()) if active_screen else 1.0
            except Exception:
                dpr = 1.0
            print(
                f"[Layout] Qt logical screen: {actual_sw}x{actual_sh}, "
                f"Config canvas: {conf_sw}x{conf_sh}, DPR: {dpr:.3f}"
            )
            
            scale_x = actual_sw / conf_sw if conf_sw else 1.0
            scale_y = actual_sh / conf_sh if conf_sh else 1.0

            grid_w = actual_sw // cols
            grid_h = actual_sh // rows
            
            limit = rows * cols 
            for i, zone in enumerate(zones):
                if i >= limit: break
                # Calc Geometry
                
                # If coordinates exist in config, scale them
                if "x" in zone and "y" in zone:
                    x = int(zone.get("x") * scale_x)
                    y = int(zone.get("y") * scale_y)
                    w = int(zone.get("w", grid_w) * scale_x)
                    h = int(zone.get("h", grid_h) * scale_y)
                else:
                    # Fallback to simple grid
                    r = i // cols
                    c = i % cols
                    if r >= rows: continue 
                    x = c * grid_w
                    y = r * grid_h
                    w = grid_w
                    h = grid_h
                    
                zone["_rect"] = (x, y, w, h) # Store runtime rect
                
                ctype = zone.get("type", "web")
                content = zone.get("content", "")
                
                if ctype == "web":
                    # Special Case: Use External WebView2 (use_edge)
                    if self._should_use_external_webview(zone):
                        # Skip internal webview creation; handled in enforce_loop
                        frame = QFrame(self.central_widget)
                        frame.setStyleSheet("background-color: black;")
                        frame.setGeometry(x, y, w, h)
                        frame.show()
                        self.zone_widgets[i] = frame
                        continue

                    # Empty URL: show placeholder frame instead of creating WebEngineView
                    if not (content and str(content).strip()):
                        frame = QFrame(self.central_widget)
                        frame.setStyleSheet("background-color: black;")
                        frame.setGeometry(x, y, w, h)
                        frame.show()
                        self.zone_widgets[i] = frame
                        print("[WEB] Empty URL, using placeholder frame.")
                        continue

                    # Check Cache
                    # [Optimization] Do NOT cache heavy BIM/3D pages to prevent GPU memory leaks
                    is_heavy_3d = False
                    if content and ("/bim" in content.lower() or "/fsb" in content.lower() or "output.svf" in content.lower()):
                        is_heavy_3d = True
                        
                    cache_key = content
                    webview = None
                    
                    if not is_heavy_3d and cache_key in self.web_view_cache:
                        webview = self.web_view_cache[cache_key]
                        webview.setParent(self.central_widget)
                        print(f"Using Cached WebView for: {content}")
                        
                        # Smart Reload: If older than 1 hour (3600s), reload to keep session alive
                        last_loaded = getattr(webview, 'last_loaded', 0)
                        if time.time() - last_loaded > 3600:
                            print(f"Cache expired (>1h). Reloading {content}")
                            webview.reload()
                            webview.last_loaded = time.time()
                    else:
                        print(f"Creating New QWebEngineView for {content}") # Debug Log
                        webview = QWebEngineView(self.central_widget)
                        # Use custom page to ignore SSL errors
                        profile = self.ensure_web_profile()
                        page = IgnoreCertPage(profile, webview)
                        # Allow FullScreen requests
                        page.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
                        page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
                        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
                        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                        webview.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
                        webview.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
                        # Connect signal to accept the request
                        page.fullScreenRequested.connect(self.on_full_screen_requested)
                        webview.setPage(page)
                        
                        if content:
                            webview.setUrl(QUrl(content))
                        
                        # Crash / White Screen Handling
                        # Pass weakref or use lambda with default arg to capture webview safely
                        page.renderProcessTerminated.connect(
                            lambda s, c, w=webview: self.on_render_crashed(w, s, c)
                        )
                        
                        webview.last_loaded = time.time() # Stamp creation time
                        
                        # Add to cache only if NOT heavy 3D (to allow GC)
                        if content and not is_heavy_3d:
                            self.web_view_cache[cache_key] = webview

                    if not getattr(webview, "_ss_load_finished_hooked", False):
                        webview.loadFinished.connect(
                            lambda ok, w=webview: self.on_webview_load_finished(w, ok)
                        )
                        webview._ss_load_finished_hooked = True

                    webview.setGeometry(x, y, w, h)
                    
                    # Apply Zoom
                    scale = float(zone.get("scale", 1.0))
                    webview.setZoomFactor(scale)

                    webview.show()
                    self.zone_widgets[i] = webview
            

                elif ctype == "video":
                    if video_backend == "vlc":
                        # External VLC will render the video; keep a placeholder to reserve layout
                        frame = QFrame(self.central_widget)
                        frame.setStyleSheet("background-color: black;")
                        frame.setGeometry(x, y, w, h)
                        frame.show()
                        self.zone_widgets[i] = frame
                    else:
                        # WebView2 HTML5 video fallback (more stable on some machines)
                        video_path = content
                        if video_path:
                            video_url = self.create_video_html(video_path)
                            if video_url:
                                zone["_generated_url"] = QUrl.fromLocalFile(video_url).toString()
                        frame = QFrame(self.central_widget)
                        frame.setStyleSheet("background-color: black;")
                        frame.setGeometry(x, y, w, h)
                        frame.show()
                        self.zone_widgets[i] = frame

                elif ctype == "exe" or ctype == "rdp":
                    target_path = content
                    if ctype == "rdp":
                        # For RDP, content is IP. We launch mstsc.
                        # We store "mstsc.exe" in active paths? 
                        # Actually we need to track that THIS specific RDP session is active.
                        # But mstsc doesn't easily let us rename its window or process per IP easily without complex hooks.
                        # For V4 simple implementation: We launch `mstsc /v:IP`.
                        # We track `mstsc.exe`.
                        target_path = "mstsc.exe" # Generic tracker
                        
                        # Store the full launch command for later execution if needed
                        # But wait, logic below uses `content` as path to check process.
                        
                        # Let's map RDP content to the command.
                        # And for detection, we look for mstsc.exe.
                        pass

                    if content:
                        if ctype == "rdp":
                            self.all_managed_paths.add("mstsc.exe")
                        else:
                            self.all_managed_paths.add(content)
                        
                    # Visual placeholder
                    frame = QFrame(self.central_widget)
                    frame.setStyleSheet("background-color: black;")
                    frame.setGeometry(x, y, w, h)
                    frame.show()
                    self.zone_widgets[i] = frame
                    
                    # Store specific launch info for RDP
                    if ctype == "rdp":
                        self.zone_widgets[i].setProperty("rdp_ip", content) # Store IP for launch logic
            
            # Start Timer
            duration = scene.get("duration", 10)
            
            # Sticky Pause Logic:
            # If paused, we load the scene but DO NOT start the timer.
            if not self.paused:
                self._start_scene_timer(scene, reason="load_scene")
            else:
                self.scene_timer.stop() # Ensure it's stopped

            # 4. Preload Next Scene (Web Only)
            next_idx = (index + 1) % len(self.playlist)
            if next_idx != index:
                next_scene = self.playlist[next_idx]
                for zone in next_scene.get("zones", []):
                    if zone.get("type") == "web" and not zone.get("use_edge", False):
                        url = zone.get("content")
                        if url and url not in self.web_view_cache:
                            print(f"Preloading: {url}")
                            # Create hidden webview
                            wv = QWebEngineView(self.central_widget)
                            profile = self.ensure_web_profile()
                            wv.setPage(QWebEnginePage(profile, wv))
                            wv.setUrl(QUrl(url))
                            wv.hide() # Keep hidden
                            self.web_view_cache[url] = wv
            
            # [Fix] Force enforce_loop immediately after scene load to prevent "lag" in window positioning
            # Call it after a tiny delay to allow UI to settle, but much faster than the 500ms timer
            QTimer.singleShot(50, self.enforce_loop)
            QTimer.singleShot(200, self.enforce_loop) # Double tap to be sure
            # Cleanup inactive WebView2 processes to prevent window/process buildup
            QTimer.singleShot(1000, self.cleanup_rogue_processes)
            load_succeeded = True
                            
        except Exception as e:
            print(f"[Load Scene Error] {e}")
            traceback.print_exc()
            # Show visible error
            err_label = QLabel(self.central_widget)
            err_label.setText(f"SCENE LOAD ERROR:\n{e}")
            err_label.setStyleSheet("color: red; font-size: 24px; background: rgba(0,0,0,0.8);")
            err_label.setAlignment(Qt.AlignCenter)
            err_label.setGeometry(20, 20, 800, 600)
            err_label.show()
            self.zone_widgets["error"] = err_label
        finally:
            # Resume enforce loop after scene load
            self._loading_scene = False
            if load_succeeded:
                self._last_layout_screen_signature = self._screen_signature(
                    self.get_configured_screen() or self.get_active_screen()
                )
                self._pending_screen_signature = None
            if hasattr(self, 'enforce_timer') and not self.enforce_timer.isActive():
                self.enforce_timer.start(500)

    # [Nuclear Option 3.0] Process-Based Cleanup
    def cleanup_rogue_processes(self):
        try:
            import psutil
            try:
                if bool(self.config.get("global_settings", {}).get("webview_host_mode", False)):
                    return
            except Exception:
                pass
            current_scene_idx = self.current_scene_idx
            allowed_tags = set()
            persistent_tags = set()
            keep_inactive = False
            try:
                keep_inactive = bool(self.config.get("global_settings", {}).get("keep_inactive_webviews", False))
            except:
                keep_inactive = False
            if self.playlist:
                scene = self.playlist[self.current_scene_idx]
                zones = scene.get("zones", [])
                limit = scene.get("rows", 1) * scene.get("cols", 1)
                for i, zone in enumerate(zones):
                    if i >= limit: break
                    z_type = zone.get("type")
                    # Allow tags for external WebView2 instances:
                    # - web zones that explicitly use_edge
                    # - video zones (always use external WebView2 in current design)
                    if (z_type == "web" and self._should_use_external_webview(zone)) or z_type == "video":
                        allowed_tags.add(self._webview_tag(current_scene_idx, i))
                
                # Build persistent tags across ALL scenes (optional keep-alive)
                if keep_inactive:
                    for s_idx, sc in enumerate(self.playlist):
                        zlist = sc.get("zones", [])
                        zlimit = sc.get("rows", 1) * sc.get("cols", 1)
                        for zi, zone in enumerate(zlist):
                            if zi >= zlimit: break
                            z_type = zone.get("type")
                            if (z_type == "web" and self._should_use_external_webview(zone)) or z_type == "video":
                                persistent_tags.add(self._webview_tag(s_idx, zi))

            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (p.info['name'] or "").lower()
                    if "python" in name or "webviewplayer" in name:
                        cmdline = p.info['cmdline']
                        if cmdline:
                            cmd_str = " ".join(cmdline)
                            cmd_lower = cmd_str.lower()
                            if ("webview_player.py" in cmd_lower or "webviewplayer.exe" in cmd_lower) and "--title" in cmd_lower:
                                match = self._webview_tag_pattern().search(cmd_str)
                                if match:
                                    tag = match.group(0)
                                    if tag not in allowed_tags and tag not in persistent_tags:
                                        print(f"[Cleanup] Killing Orphaned Process {tag} (PID: {p.info['pid']})")
                                        p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                     pass
        except Exception as e:
            print(f"[Cleanup Error] {e}")

    def cleanup_previous_scene(self, prev_idx, next_idx):
        """
        Aggressively kill external playback helpers (WebView2/VLC) from previous scene
        to reduce memory usage.
        """
        try:
            if not self.playlist:
                return
            # Allow opt-out via config
            try:
                if not bool(self.config.get("global_settings", {}).get("cleanup_prev_scene", True)):
                    return
            except:
                pass

            # In host mode, WebView runs as a single long-lived process.
            # Do not kill WebView helpers on scene switch; only allow VLC cleanup.
            host_mode = False
            try:
                host_mode = bool(self.config.get("global_settings", {}).get("webview_host_mode", False))
            except Exception:
                host_mode = False

            if prev_idx < 0 or prev_idx >= len(self.playlist):
                return

            # Build tag patterns for the previous scene
            prev_scene = self.playlist[prev_idx]
            zones = prev_scene.get("zones", [])
            limit = prev_scene.get("rows", 1) * prev_scene.get("cols", 1)
            webview_tags = set()
            vlc_tags = set()
            for zi, zone in enumerate(zones):
                if zi >= limit:
                    break
                z_type = zone.get("type")
                if (not host_mode) and ((z_type == "web" and self._should_use_external_webview(zone)) or z_type == "video"):
                    webview_tags.add(self._webview_tag(prev_idx, zi))
                if z_type == "video":
                    vlc_tags.add(self._vlc_tag(prev_idx, zi))

            if not webview_tags and not vlc_tags:
                return

            # Kill helper processes by tag
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (p.info['name'] or "").lower()
                    cmdline = " ".join(p.info['cmdline'] or [])
                    cmd_lower = cmdline.lower()

                    # WebView2 helper
                    if webview_tags and ("webviewplayer" in name or "python" in name):
                        for tag in list(webview_tags):
                            if tag in cmdline:
                                print(f"[Cleanup] Killing previous WebView2 {tag} (PID: {p.info['pid']})")
                                p.kill()
                                webview_tags.discard(tag)
                                break

                    # VLC helper
                    if vlc_tags and ("vlc" in name or "vlc" in cmd_lower):
                        for tag in list(vlc_tags):
                            if tag in cmdline:
                                print(f"[Cleanup] Killing previous VLC {tag} (PID: {p.info['pid']})")
                                p.kill()
                                vlc_tags.discard(tag)
                                break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            print(f"[Cleanup] Previous scene process cleanup error: {e}")

    # Hide WebView2 windows that are NOT part of the current scene
    # [Optimization] Throttled to avoid main loop freeze
    def hide_inactive_webviews(self, active_tags):
        import time
        current_time = time.time()
        
        # Only run this expensive scan every 2.0 seconds
        if hasattr(self, '_last_webview_cleanup_time'):
            if current_time - self._last_webview_cleanup_time < 2.0:
                return
        self._last_webview_cleanup_time = current_time

        try:
            import psutil
            tag_pattern = self._webview_tag_pattern()

            # Pre-scan window titles so we can hide by tag (more reliable than PID-only)
            tagged_hwnds = []
            try:
                def collect_hwnds(hwnd, _):
                    try:
                        if win32gui.IsWindowVisible(hwnd): # Only care about visible windows
                            title = safe_get_window_text(hwnd)
                            if title and tag_pattern.search(title):
                                tagged_hwnds.append((title, hwnd))
                    except:
                        pass
                    return True
                win32gui.EnumWindows(collect_hwnds, None)
            except:
                pass

            def hide_hwnd(hwnd, tag=None):
                try:
                    # [Optimization] Only hide if actually visible
                    if win32gui.IsWindowVisible(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                        flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                        win32gui.SetWindowPos(hwnd, 0, -3000, -3000, 0, 0, flags)
                        if tag:
                            print(f"[DEBUG] Hiding Inactive WebView: {tag} (HWND: {hwnd})")
                except:
                    pass

            # [Optimization] If we found windows by title, handle them first
            # This avoids the expensive psutil loop if titles matched
            handled_tags = set()
            for title, hwnd in tagged_hwnds:
                # Extract tag from title
                m = tag_pattern.search(title)
                if m:
                    tag = m.group(0)
                    if tag not in active_tags:
                        hide_hwnd(hwnd, tag=tag)
                        handled_tags.add(tag)

            # Only perform deep process scan if we suspect we missed something? 
            # Or just do it anyway but throttled. Let's do it throttled.
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (p.info['name'] or "").lower()
                    
                    # Quick filter: strict name check first to avoid processing every process
                    if "webview" not in name and "python" not in name:
                        continue

                    cmdline_list = p.info['cmdline'] or []
                    cmd_str = " ".join(cmdline_list)

                    # Identify WebView2-tagged processes by cmdline tag
                    m = tag_pattern.search(cmd_str)
                    if not m:
                        continue

                    tag = m.group(0)
                    if tag in active_tags:
                        continue
                    
                    # If we already handled this via window title, verify if we need to do more?
                    # Usually window title hide is enough.
                    
                    # Determine process kind
                    is_webview_exe = "webviewplayer.exe" in name
                    is_webview_py = "python" in name and "webview_player.py" in cmd_str
                    
                    if not (is_webview_exe or is_webview_py):
                        continue

                    # Find and hide its window by PID if we haven't already
                    # pass 'track_pid' to be specific
                    path_hint = "WebViewPlayer.exe" if is_webview_exe else "python.exe"
                    
                    # Only search for main window if we didn't catch it via title?
                    # But 'find_main_window_for_exe' does EnumWindows inside it too...
                    # Let's rely on the 'tagged_hwnds' logic above for the majority of cases.
                    # We will ONLY do this fallback if the tag wasn't found in visible titles?
                    
                    # Actually, for safety, let's just do it, but rely on IsWindowVisible inside find_main_window/hide_hwnd
                    # Since we are throttled to 2s, this extra EnumWindows inside find_main_window is acceptable.
                    
                    hwnd = self.find_main_window_for_exe(path_hint, track_pid=p.info['pid'])
                    if hwnd:
                        hide_hwnd(hwnd, tag=tag)
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            print(f"[WebView Hide Error] {e}")

    def hide_inactive_vlc(self, active_tags):
        try:
            for tag, pid in list(self._vlc_pids.items()):
                if tag in active_tags:
                    continue

                # Hide window if we have it
                hwnd = self._vlc_hwnds.get(tag)
                if hwnd and win32gui.IsWindow(hwnd):
                    if win32gui.IsWindowVisible(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                        win32gui.SetWindowPos(
                            hwnd,
                            0,
                            -3000,
                            -3000,
                            0,
                            0,
                            win32con.SWP_NOACTIVATE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                        )
                else:
                    self._vlc_hwnds.pop(tag, None)

                # Suspend process to save CPU (VLC only)
                if pid and tag not in self._vlc_suspended:
                    try:
                        psutil.Process(pid).suspend()
                        self._vlc_suspended.add(tag)
                    except Exception as e:
                        print(f"[VLC] Suspend failed: {e}")
        except Exception as e:
            print(f"[VLC Hide Error] {e}")

    def ensure_web_profile(self):
        if self.web_profile:
            return self.web_profile
        profile_dir = self.web_profile_dir
        if not os.path.exists(profile_dir):
            os.makedirs(profile_dir)
        self.web_profile = QWebEngineProfile("ScreenPlayerProfile", self)
        self.web_profile.setPersistentStoragePath(profile_dir)
        self.web_profile.setCachePath(profile_dir)
        self.web_profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.web_profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        return self.web_profile

    def _path_token_for_cmdline(self, path):
        try:
            return os.path.normcase(os.path.abspath(str(path))).lower().replace("/", "\\")
        except Exception:
            return str(path or "").lower().replace("/", "\\")

    def _webview_host_cmdline_matches_paths(self, cmdline):
        """
        Host mode is profile-local. Never reuse/kill a WebView host unless its
        command line points at this profile's cmd/status IPC files.
        """
        try:
            cmd = " ".join(cmdline or []) if isinstance(cmdline, (list, tuple)) else str(cmdline or "")
            cmd = cmd.lower().replace("/", "\\")
            if "--host" not in cmd:
                return False
            cmd_path = self._path_token_for_cmdline(
                getattr(self, "_webview_host_cmd_path", None) or os.path.join(self.data_dir, "webview_cmd.json")
            )
            status_path = self._path_token_for_cmdline(
                getattr(self, "_webview_host_status_path", None) or os.path.join(self.data_dir, "webview_status.json")
            )
            return bool(cmd_path and cmd_path in cmd and status_path and status_path in cmd)
        except Exception:
            return False

    def _webview_host_process_matches(self, proc):
        try:
            name = ""
            try:
                name = (proc.name() or "").lower()
            except Exception:
                pass
            cmdline = proc.cmdline() or []
            cmd_lower = " ".join(cmdline).lower()
            is_host = "--host" in cmd_lower and (
                "webview_player.py" in cmd_lower
                or "webviewplayer.exe" in cmd_lower
                or "webviewplayer" in name
            )
            return bool(is_host and self._webview_host_cmdline_matches_paths(cmdline))
        except Exception:
            return False

    def stop_webview_host(self):
        """Best-effort stop for the single WebView host process."""
        try:
            self.send_webview_host_cmds([{"op": "exit"}])
        except Exception:
            pass

        pid = getattr(self, "_webview_host_pid", None)
        if pid:
            try:
                proc = psutil.Process(pid)
                if self._webview_host_process_matches(proc):
                    proc.kill()
                else:
                    self.log_debug(f"[WebView Host] Skip killing host PID {pid}; it belongs to another profile")
            except Exception:
                pass

        self._webview_host_pid = None
        self._webview_host_last_tags = set()

        # Clear stale IPC files so a future host starts from a clean state.
        for p in [
            getattr(self, "_webview_host_cmd_path", None),
            getattr(self, "_webview_host_status_path", None)
        ]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def disable_webview_host_temporarily(self, reason="", cooldown_sec=60):
        """Disable host mode for a short period and fall back to standalone windows."""
        try:
            cooldown = max(5.0, float(cooldown_sec))
        except Exception:
            cooldown = 60.0

        self._webview_host_disabled_until = time.time() + cooldown
        self._webview_host_disable_reason = reason or "unknown"
        msg = f"[WebView Host] Disabled for {int(cooldown)}s: {self._webview_host_disable_reason}"
        print(msg)
        try:
            self.log_debug(msg)
        except Exception:
            pass
        self.stop_webview_host()

    def restart_webview_host(self, reason="", cooldown_sec=8):
        """Restart host mode without dropping to standalone multi-process windows."""
        try:
            cooldown = max(2.0, float(cooldown_sec))
        except Exception:
            cooldown = 8.0

        now = time.time()
        last = float(getattr(self, "_webview_host_last_restart_time", 0.0) or 0.0)
        if now - last < cooldown:
            wait_left = max(0.0, cooldown - (now - last))
            print(f"[WebView Host] Restart cooldown active ({wait_left:.1f}s): {reason or 'unknown'}")
            return False

        self._webview_host_last_restart_time = now
        why = reason or "unknown"
        msg = f"[WebView Host] Restarting host: {why}"
        print(msg)
        try:
            self.log_debug(msg)
        except Exception:
            pass

        self.stop_webview_host()

        # Drop stale bindings from the old host instance.
        try:
            if hasattr(self, '_webview_tag_hwnds'):
                for tag in list(self._webview_tag_hwnds.keys()):
                    if str(tag).startswith("WebView2_"):
                        self._webview_tag_hwnds.pop(tag, None)
        except Exception:
            pass
        try:
            if hasattr(self, '_webview_pids'):
                for tag in list(self._webview_pids.keys()):
                    if str(tag).startswith("WebView2_"):
                        self._webview_pids.pop(tag, None)
        except Exception:
            pass
        try:
            if hasattr(self, '_webview_missing_since'):
                for tag in list(self._webview_missing_since.keys()):
                    if str(tag).startswith("WebView2_"):
                        self._webview_missing_since.pop(tag, None)
        except Exception:
            pass

        return bool(self.ensure_webview_host())

    def load_webview_host_tags_from_status(self):
        """Best-effort load of current host window tags from status JSON."""
        tags = set()
        status_path = getattr(self, "_webview_host_status_path", None)
        if not status_path or (not os.path.exists(status_path)):
            return tags
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for tag in data.get("windows", []):
                if isinstance(tag, str) and tag.startswith("WebView2_"):
                    tags.add(tag)
        except Exception:
            return set()
        return tags

    def ensure_webview_host(self):
        """
        Start or locate the single WebView2 Host process (multi-window) and
        store its PID for window matching.
        """
        try:
            glob = self.config.get("global_settings", {})
            if not bool(glob.get("webview_host_mode", False)):
                return False
        except Exception:
            return False

        # Keep IPC paths in sync even when attaching to an existing host process.
        cmd_path = os.path.abspath(glob.get("webview_host_cmd_path") or os.path.join(self.data_dir, "webview_cmd.json"))
        status_path = os.path.abspath(glob.get("webview_host_status_path") or os.path.join(self.data_dir, "webview_status.json"))
        self._webview_host_cmd_path = cmd_path
        self._webview_host_status_path = status_path

        # If host mode was temporarily disabled by watchdog, skip host usage.
        try:
            if time.time() < float(getattr(self, "_webview_host_disabled_until", 0.0) or 0.0):
                return False
        except Exception:
            pass

        # Reuse existing PID if still alive
        pid = getattr(self, "_webview_host_pid", None)
        if pid:
            try:
                proc = psutil.Process(pid)
                if proc.is_running():
                    cmdline = " ".join(proc.cmdline() or []).lower()
                    if (
                        "--host" in cmdline
                        and ("webview_player.py" in cmdline or "webviewplayer.exe" in cmdline)
                        and self._webview_host_process_matches(proc)
                    ):
                        try:
                            self._webview_host_exe = proc.exe() or self._webview_host_exe
                        except Exception:
                            pass
                        if not self._webview_host_last_tags:
                            self._webview_host_last_tags = self.load_webview_host_tags_from_status()
                        return True
                self._webview_host_pid = None
            except Exception:
                self._webview_host_pid = None

        # Try to find an existing host process
        try:
            for p in psutil.process_iter(['pid', 'name', 'cmdline', 'exe']):
                try:
                    name = (p.info['name'] or "").lower()
                    cmdline = " ".join(p.info['cmdline'] or [])
                    cmdline_lower = cmdline.lower()
                    if ("webviewplayer" in name or "python" in name) and "--host" in cmdline_lower:
                        if (
                            ("webview_player.py" in cmdline_lower or "webviewplayer.exe" in cmdline_lower)
                            and self._webview_host_cmdline_matches_paths(p.info.get('cmdline') or [])
                        ):
                            self._webview_host_pid = p.info['pid']
                            self._webview_host_exe = p.info.get('exe') or self._webview_host_exe
                            if not self._webview_host_last_tags:
                                self._webview_host_last_tags = self.load_webview_host_tags_from_status()
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass

        # Launch throttle to avoid rapid respawn loops when host is still booting.
        # Do not report "ready" unless there is a live host PID.
        try:
            now = time.time()
            last = getattr(self, "_webview_host_last_launch_time", 0.0)
            if now - last < 2.0:
                pid = getattr(self, "_webview_host_pid", None)
                if pid:
                    try:
                        if psutil.Process(pid).is_running():
                            return True
                    except Exception:
                        self._webview_host_pid = None
                return False
        except Exception:
            pass

        # Launch new host
        profile_path = os.path.abspath(glob.get("webview_host_profile_path") or os.path.join(self.web_profile_dir, "ExternalHost"))
        host_log_path = os.path.abspath(os.path.join(self.data_dir, "webview_host.log"))
        safe_mode = bool(glob.get("webview_safe_mode", False))
        browser_args = str(glob.get("webview_browser_args", "") or "")

        # Determine executable path for host
        launch_executable = ""
        launch_args = ""
        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
            wv_dir = os.path.join(base_path, "WebViewPlayer")
            wv_exe = os.path.join(wv_dir, "WebViewPlayer.exe")
            if not os.path.exists(wv_exe):
                alt = os.path.join(wv_dir, "webview_player.exe")
                if os.path.exists(alt):
                    wv_exe = alt
            launch_executable = wv_exe
            launch_args = ""
            self._webview_host_exe = wv_exe
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.join(base_path, "webview_player.py")
            launch_executable = sys.executable
            launch_args = f'"{script_path}"'
            self._webview_host_exe = sys.executable

        safe_part = " --safe-mode" if safe_mode else ""
        browser_args_part = ""
        if browser_args:
            # argparse treats values that start with "--" as options when passed as a separate token.
            # Use "--browser-args=<value>" so WebViewPlayer can parse it reliably.
            browser_args_clean = str(browser_args).strip().strip('"').strip("'")
            if " " in browser_args_clean:
                browser_args_clean = browser_args_clean.split()[0]
                print(f"[WebView Host] browser_args has spaces; using first token: {browser_args_clean}")
            browser_args_part = f" --browser-args={browser_args_clean}"
        launch_cmd = (
            f'"{launch_executable}" {launch_args} --host'
            f' --cmd-path "{cmd_path}" --status-path "{status_path}"'
            f' --profile-path "{profile_path}" --log-path "{host_log_path}"'
            f'{safe_part}{browser_args_part}'
        )

        try:
            self._webview_host_last_launch_time = time.time()
            proc = self.launch_exe(launch_cmd)
            if proc and proc.pid:
                self._webview_host_pid = proc.pid
                return True
        except Exception as e:
            print(f"[WebView Host] Launch failed: {e}")
        return False

    def send_webview_host_cmds(self, cmds):
        if not cmds:
            return
        cmd_path = getattr(self, "_webview_host_cmd_path", None) or os.path.join(self.data_dir, "webview_cmd.json")
        retries = 4
        for attempt in range(retries):
            tmp = f"{cmd_path}.tmp.{os.getpid()}.{attempt}"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cmds, f, ensure_ascii=False)
                os.replace(tmp, cmd_path)
                return
            except Exception as e:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                if attempt >= retries - 1:
                    print(f"[WebView Host] Cmd write failed: {e}")
                    return
                time.sleep(0.03)

    def get_external_web_profile_path(self, tag=None):
        """
        Return a WebView2 storage path.
        If tag is provided, use a dedicated subfolder to avoid profile lock conflicts.
        """
        base = os.path.join(self.web_profile_dir, "External")
        profile_path = os.path.join(base, tag) if tag else base
        if not os.path.exists(profile_path):
            os.makedirs(profile_path)
        return profile_path

    def _configured_screen_point(self):
        try:
            glob = self.config.get("global_settings", {}) if isinstance(self.config, dict) else {}
            if glob.get("screen_x") is None or glob.get("screen_y") is None:
                return None
            return int(glob.get("screen_x")), int(glob.get("screen_y"))
        except Exception:
            return None

    @staticmethod
    def _normalize_screen_device(value):
        try:
            return str(value or "").strip().replace("/", "\\").upper()
        except Exception:
            return ""

    def _configured_screen_device(self):
        try:
            glob = self.config.get("global_settings", {}) if isinstance(self.config, dict) else {}
            return self._normalize_screen_device(glob.get("screen_device"))
        except Exception:
            return ""

    def _screen_signature(self, screen):
        if not screen:
            return None
        try:
            geo = screen.geometry()
            try:
                dpr = round(float(screen.devicePixelRatio()), 3)
            except Exception:
                dpr = 1.0
            return (
                self._normalize_screen_device(screen.name()),
                int(geo.left()),
                int(geo.top()),
                int(geo.width()),
                int(geo.height()),
                dpr,
            )
        except Exception:
            return None

    def _screen_contains_point(self, screen, x, y, tolerance=8):
        try:
            geo = screen.geometry()
            return (
                x >= geo.left() - tolerance
                and y >= geo.top() - tolerance
                and x < geo.left() + geo.width() + tolerance
                and y < geo.top() + geo.height() + tolerance
            )
        except Exception:
            return False

    def get_configured_screen(self):
        """Return the configured target screen, preferring its stable device identity."""
        configured_device = self._configured_screen_device()
        if configured_device:
            try:
                for sc in QApplication.screens():
                    if self._normalize_screen_device(sc.name()) == configured_device:
                        return sc
            except Exception:
                pass
            # WebAdmin stores a Win32 display id; Qt may expose the EDID name.
            if configured_device.startswith("\\\\.\\DISPLAY"):
                try:
                    for monitor, _, rect in win32api.EnumDisplayMonitors():
                        info = win32api.GetMonitorInfo(monitor)
                        if self._normalize_screen_device(info.get("Device")) == configured_device:
                            return QApplication.screenAt(QPoint(rect[0] + 1, rect[1] + 1))
                except Exception:
                    pass
            # A selected monitor being unplugged must never select another one.
            return None

        point = self._configured_screen_point()
        if point is None:
            return None

        x, y = point
        try:
            sc = QApplication.screenAt(QPoint(x + 1, y + 1))
            if sc:
                return sc
        except Exception:
            pass

        try:
            for sc in QApplication.screens():
                if self._screen_contains_point(sc, x, y, tolerance=120):
                    return sc
        except Exception:
            pass

        return None

    def _hide_profile_windows_for_missing_screen(self):
        # Only scan while a selected monitor is absent; keep the normal loop unchanged.
        now = time.monotonic()
        if now - getattr(self, "_screen_hide_at", -10.0) < 2.0:
            return
        self._screen_hide_at = now
        tags = set(getattr(self, "_webview_host_last_tags", set()))
        tags.update(getattr(self, "_webview_pending_stale_host_tags", {}))
        if tags:
            self.send_webview_host_cmds([
                {"op": "hide", "tag": tag, "pause_media": True} for tag in sorted(tags)
            ])
        self.hide_inactive_webviews(set())
        self.hide_inactive_vlc(set())
        handles = set(getattr(self, "known_hwnds", set()))
        handles.update(getattr(self, "owned_hwnds", set()))
        for attr in ("_webview_tag_hwnds", "_exe_hwnds", "_browser_hwnds", "_vlc_hwnds"):
            handles.update(getattr(self, attr, {}).values())
        tracked_pids = {getattr(self, "_webview_host_pid", None)}
        for attr in ("_webview_pids", "_vlc_pids", "_generic_pids"):
            tracked_pids.update(getattr(self, attr, {}).values())
        tracked_pids.discard(None)
        valid_pids = set(tracked_pids)
        for pid in tracked_pids:
            try:
                valid_pids.update(child.pid for child in psutil.Process(pid).children(recursive=True))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        # The ordinary EXE finder may fall back to a same-name process. Validate
        # ownership below, including cached HWNDs that Windows may have reused.
        for path, pid in getattr(self, "_generic_pids", {}).items():
            hwnd = self.find_main_window_for_exe(path, track_pid=pid)
            if hwnd:
                handles.add(hwnd)
        tag_pattern = self._webview_tag_pattern()
        for hwnd in handles:
            try:
                if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    continue
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in valid_pids and not tag_pattern.search(safe_get_window_text(hwnd)):
                    continue
                ctypes.windll.user32.ShowWindowAsync(ctypes.c_void_p(int(hwnd)), win32con.SW_HIDE)
            except Exception:
                pass

    def _suspend_for_missing_screen(self):
        missing = self._target_screen_is_configured() and self.get_configured_screen() is None
        if missing:
            if not getattr(self, "_screen_waiting", False):
                self._screen_waiting = True
                self._screen_hide_at = -10.0
                self._screen_hidden_controls = list(getattr(self, "_screen_hidden_controls", []))
                for widget in (getattr(self, "trigger_btn", None), getattr(self, "controls", None)):
                    if widget is not None and widget.isVisible() and widget not in self._screen_hidden_controls:
                        self._screen_hidden_controls.append(widget)
                self.log_debug(f"[Screen Wait] Target absent: device={self._configured_screen_device()!r}, point={self._configured_screen_point()}; hiding profile windows")
            self.scene_timer.stop()
            self.hide()
            for widget in (getattr(self, "trigger_btn", None), getattr(self, "controls", None)):
                if widget is not None:
                    widget.hide()
            self._hide_profile_windows_for_missing_screen()
            return True
        if getattr(self, "_screen_waiting", False):
            self._screen_waiting = False
            self.log_debug(f"[Screen Return] Target available: device={self._configured_screen_device()!r}; restoring scene {self.current_scene_idx}")
            self._last_layout_screen_signature = None
            self._pending_screen_signature = None
            if not self._restore_configured_screen_geometry("screen returned", force=True):
                self._show_fullscreen_on_target()
            self._schedule_screen_relayout(700, followup=True)
            return True
        return False

    def _show_fullscreen_on_target(self):
        # Delayed fullscreen callbacks must recheck a monitor that disappeared again.
        if self._suspend_for_missing_screen():
            return
        self.showFullScreen()
        for widget in getattr(self, "_screen_hidden_controls", []):
            widget.show()
        self._screen_hidden_controls = []

    def _target_screen_is_configured(self):
        return bool(self._configured_screen_device()) or self._configured_screen_point() is not None

    def _ensure_screen_relayout_timers(self):
        if not hasattr(self, "_screen_relayout_timer"):
            self._screen_relayout_timer = QTimer(self)
            self._screen_relayout_timer.setSingleShot(True)
            self._screen_relayout_timer.timeout.connect(self._refresh_scene_layout_after_screen_change)
        if not hasattr(self, "_screen_relayout_followup_timer"):
            self._screen_relayout_followup_timer = QTimer(self)
            self._screen_relayout_followup_timer.setSingleShot(True)
            self._screen_relayout_followup_timer.timeout.connect(self._refresh_scene_layout_after_screen_change)

    def _schedule_screen_relayout(self, delay_ms=2800, followup=True):
        try:
            self._ensure_screen_relayout_timers()
            self._screen_relayout_timer.start(max(100, int(delay_ms)))
            if followup:
                self._screen_relayout_followup_timer.start(max(10000, int(delay_ms) + 5000))
        except Exception:
            pass

    def _connect_screen_change_signals(self):
        """Connect display topology/geometry changes, including monitors added after wake."""
        try:
            if not hasattr(self, "_screen_signal_ids"):
                self._screen_signal_ids = set()
            for sc in QApplication.screens():
                key = id(sc)
                if key in self._screen_signal_ids:
                    continue
                try:
                    sc.geometryChanged.connect(self.on_screen_changed)
                    self._screen_signal_ids.add(key)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            app = QApplication.instance()
            if app and not getattr(self, "_screen_list_signals_connected", False):
                app.screenAdded.connect(self._on_screen_list_changed)
                app.screenRemoved.connect(self._on_screen_list_changed)
                self._screen_list_signals_connected = True
        except Exception:
            pass

    def _on_screen_list_changed(self, *args):
        print("[Screen Changed] Screen list changed")
        self._connect_screen_change_signals()
        self.on_screen_changed(None)

    def _restore_configured_screen_geometry(self, reason="screen restore", force=False):
        """
        Ensure Player is fullscreen on the configured target screen before rebuilding
        scene geometry. Without this, Windows can move the window to another monitor
        while displays sleep and the layout is rebuilt against the wrong DPI/size.
        """
        if not self._target_screen_is_configured():
            return False

        target_screen = self.get_configured_screen()
        if not target_screen:
            return False

        try:
            current_screen = self.get_active_screen()
            target_geo = target_screen.geometry()
            current_geo = current_screen.geometry() if current_screen else None

            wrong_screen = current_screen is not target_screen
            if wrong_screen and current_geo == target_geo:
                wrong_screen = False
            too_small = self.width() < target_geo.width() - 60 or self.height() < target_geo.height() - 60
            state = self.windowState()
            not_fullscreen = not bool(state & Qt.WindowFullScreen)

            if not (force or wrong_screen or too_small or not_fullscreen):
                return False

            print(
                f"[Screen Restore] {reason}: target={target_geo}, "
                f"current={current_geo}, wrong={wrong_screen}, too_small={too_small}, fullscreen={not not_fullscreen}"
            )

            self.last_fix_time = time.time()
            self.showNormal()
            try:
                wh = self.windowHandle()
                if wh:
                    wh.setScreen(target_screen)
            except Exception:
                pass

            self.setGeometry(target_geo)
            QTimer.singleShot(120, self._show_fullscreen_on_target)
            QTimer.singleShot(450, self.enforce_loop)
            return True
        except Exception as e:
            print(f"[Screen Restore] Failed: {e}")
            return False

    def get_active_screen(self):
        """Return the screen currently hosting the player window."""
        try:
            wh = self.windowHandle()
            if wh:
                sc = wh.screen()
                if sc:
                    return sc
        except Exception:
            pass

        try:
            center = self.frameGeometry().center()
            sc = QApplication.screenAt(center)
            if sc:
                return sc
        except Exception:
            pass

        return QApplication.primaryScreen()

    def on_screen_changed(self, geo):
        self.log_debug(f"[Screen Changed] New Geometry: {geo}")
        # Collapse the burst of Windows display/DPI notifications into one rebuild.
        try:
            self._connect_screen_change_signals()
            self._schedule_screen_relayout(2800, followup=True)
        except Exception:
            pass

    def _refresh_scene_layout_after_screen_change(self):
        """Rebuild current scene runtime rects after display geometry/DPI changes."""
        if self._suspend_for_missing_screen():
            return
        if getattr(self, "_loading_scene", False):
            self._schedule_screen_relayout(600, followup=False)
            return

        try:
            if self.playlist and 0 <= self.current_scene_idx < len(self.playlist):
                print(f"[Screen Changed] Rebuilding scene layout: {self.current_scene_idx}")
                if self._target_screen_is_configured() and not self.get_configured_screen():
                    print("[Screen Changed] Configured target screen is absent; retrying layout later.")
                    self._schedule_screen_relayout(3000, followup=True)
                    return
                restored = self._restore_configured_screen_geometry("screen change")
                target_screen = self.get_configured_screen() or self.get_active_screen()
                signature = self._screen_signature(target_screen)
                previous_signature = getattr(self, "_last_layout_screen_signature", None)
                self._pending_screen_signature = None
                if restored:
                    QTimer.singleShot(700, lambda: self.load_scene(self.current_scene_idx))
                elif signature != previous_signature:
                    self.load_scene(self.current_scene_idx)
                else:
                    self.enforce_loop()
                return
        except Exception as e:
            print(f"[Screen Changed] Layout refresh failed: {e}")

        self.enforce_loop()
        
    def enforce_loop(self):
        # [Wrapper] Re-entry Guard
        if getattr(self, '_enforce_busy', False):
            return
        if getattr(self, '_loading_scene', False):
            return
        self._enforce_busy = True
        try:
            self._enforce_loop_body()
        finally:
            self._enforce_busy = False

    def _enforce_loop_body(self):
        """
        撘瑕餈游? (Heartbeat) - Implementation
        """
        if not hasattr(self, '_has_run_enforce'):
             self.log_debug("First Entering enforce_loop")
             self._has_run_enforce = True

        if not hasattr(self, '_debug_zones_printed'):
             self._debug_zones_printed = set()
        
        # Track browsers we've launched (key: url, value: timestamp)
        if not hasattr(self, '_launched_browsers'):
             self._launched_browsers = {}
        
        # Track browser HWNDs (key: url, value: hwnd)
        if not hasattr(self, '_browser_hwnds'):
             self._browser_hwnds = {}
        
        # Track EXE HWNDs (key: path, value: hwnd)
        if not hasattr(self, '_exe_hwnds'):
             self._exe_hwnds = {}

        # Track active WebView tags for this frame (used for hiding)
        active_webview_tags = set()
        active_host_webview_tags = set()
        active_isolated_webview_tags = set()
        # Track active VLC tags for this frame (used for suspend/hide)
        active_vlc_tags = set()
        # Host-mode command batch (single WebView2 host process)
        webview_host_cmds = []
        
        # Track missing WebView windows to allow recovery
        if not hasattr(self, '_webview_missing_since'):
            self._webview_missing_since = {}

        # 0. Self-Healing: Check if Player Window shrunk (e.g. monitor sleep, driver update)
        if self._suspend_for_missing_screen():
            return
        if self._restore_configured_screen_geometry("enforce loop"):
            self._schedule_screen_relayout(900, followup=True)
            return
        if self._target_screen_is_configured() and not self.get_configured_screen():
            return

        screen = self.get_active_screen()
        screen_geo = screen.geometry()
        screen_w = screen_geo.width()
        screen_h = screen_geo.height()
        current_signature = self._screen_signature(screen)
        last_signature = getattr(self, "_last_layout_screen_signature", None)
        pending_signature = getattr(self, "_pending_screen_signature", None)
        if current_signature and current_signature != last_signature and current_signature != pending_signature:
            self._pending_screen_signature = current_signature
            self._schedule_screen_relayout(1600, followup=True)
            return
        vlc_scale_x, vlc_scale_y = self.get_vlc_scale(screen_w, screen_h)
        
        # If screen reports a reasonable size (e.g. > 800 width)
        if screen_w > 800:
            current_w = self.width()
            current_h = self.height()
            
            # Condition 1: Size Mismatch (Tolerance 50px)
            size_mismatch = (current_w < screen_w - 50) or (current_h < screen_h - 50)
            # Condition 2: Lost window state (both FullScreen and Maximized are valid)
            window_state = self.windowState()
            is_maximized = bool(window_state & Qt.WindowMaximized)
            is_fullscreen = bool(window_state & Qt.WindowFullScreen)
            state_lost = not (is_maximized or is_fullscreen)
            
            if size_mismatch or state_lost:
                # Cooldown check: Don't fix if we just fixed it recently (?脫迫????)
                if time.time() - self.last_fix_time < 5.0:
                    return

                msg = f"[Self-Healing] {time.ctime()}: Mismatch detected. Screen: {screen_w}x{screen_h}, Window: {current_w}x{current_h}, State: {self.windowState()}. Restoring.\n"
                print(msg.strip())
                try:
                    log_file = os.path.join(self.data_dir, "window_log.txt") # Log to Data Dir
                    with open(log_file, "a", encoding="utf-8") as log:
                        log.write(msg)
                except: pass
                
                self.last_fix_time = time.time()
                    
                # Force Toggle to reset internal state.
                # Keep FullScreen mode if we were already in FullScreen, otherwise maximize.
                was_fullscreen = bool(self.windowState() & Qt.WindowFullScreen)
                self.showNormal()
                if was_fullscreen:
                    QTimer.singleShot(100, self._show_fullscreen_on_target)
                else:
                    QTimer.singleShot(100, lambda: None if self._suspend_for_missing_screen() else self.showMaximized())
                self.activateWindow()
        
        # Ensure Trigger is always raised and TOPMOST
        # This prevents it from being covered by "Owned" external EXEs (which are also TopMost relative to Player)
        if self.trigger_btn and self.trigger_btn.isVisible():
             self._raise_trigger_topmost()

        if not self.playlist: return

        # [Watchdog] Scene timer may stop unexpectedly; restart if not paused.
        try:
            if not self.paused and hasattr(self, "scene_timer") and not self.scene_timer.isActive():
                scene = self.playlist[self.current_scene_idx]
                duration = self._scene_duration_seconds(scene)
                if duration > 0:
                    self.scene_timer.start(duration * 1000)
                    print(f"[Watchdog] Restarted scene_timer for scene {self.current_scene_idx} ({duration}s)")
        except Exception as e:
            print(f"[Watchdog] Failed to restart scene_timer: {e}")
        scene = self.playlist[self.current_scene_idx]
        zones = scene.get("zones", [])

        glob = self.config.get("global_settings", {})
        webview_safe_mode = bool(glob.get("webview_safe_mode", False))
        webview_browser_args = str(glob.get("webview_browser_args", "") or "")
        webview_launch_cooldown = float(glob.get("webview_launch_cooldown_ms", 0) or 0) / 1000.0
        video_backend = str(glob.get("video_backend", "webview") or "webview").lower()
        webview_host_mode = bool(glob.get("webview_host_mode", False))
        webview_missing_timeout = WEBVIEW_MISSING_TIMEOUT_SEC
        webview_tag_cooldown = WEBVIEW_TAG_COOLDOWN_SEC
        webview_host_recovery_cooldown = WEBVIEW_HOST_FALLBACK_COOLDOWN_SEC
        webview_host_restart_cooldown = WEBVIEW_HOST_RESTART_COOLDOWN_SEC
        webview_host_allow_fallback = WEBVIEW_HOST_ALLOW_FALLBACK
        webview_host_recovery_mode = WEBVIEW_HOST_RECOVERY_MODE
        webview_host_stagger_ms = 0
        try:
            webview_host_stagger_ms = max(0, int(glob.get("webview_host_stagger_ms", 700) or 0))
        except Exception:
            webview_host_stagger_ms = 700
        try:
            if time.time() < float(getattr(self, "_webview_host_disabled_until", 0.0) or 0.0):
                webview_host_mode = False
        except Exception:
            pass
        # Auto-throttle threshold for many external WebView2 windows
        try:
            webview_safe_threshold = int(glob.get("webview_safe_threshold", 4) or 4)
        except Exception:
            webview_safe_threshold = 4
        webview_safe_mode_set = "webview_safe_mode" in glob
        webview_cooldown_set = "webview_launch_cooldown_ms" in glob
        webview_args_set = "webview_browser_args" in glob
        if not hasattr(self, "_last_webview_launch_time"):
            self._last_webview_launch_time = 0.0

        # Map of Path -> HWNDs found this cycle
        found_hwnds = set()
        # [Fix] Explicitly add SELF to found_hwnds to prevent:
        # 1. Being targeted as a "WebView" candidate (Self-Targeting -> Resize Loop)
        # 2. Being nuked by "Nuclear Option" (Hidden as rogue window)
        try:
            found_hwnds.add(int(self.winId()))
        except: pass
        
        active_paths_this_frame = set()
        found_active_manager_names = set()

        # ... (rest of loop) ...

        found_active_manager_names = set()
        
        limit = scene.get("rows", 1) * scene.get("cols", 1)

        # Auto-safeguards for heavy scenes (many WebView2 windows)
        webview_zone_count = 0
        for zi, z in enumerate(zones):
            if zi >= limit:
                break
            if z.get("type") == "web" and self._should_use_external_webview(z):
                webview_zone_count += 1
            elif z.get("type") == "video" and video_backend != "vlc":
                webview_zone_count += 1

        webview_host_ready = False
        if webview_host_mode and webview_zone_count > 0:
            webview_host_ready = self.ensure_webview_host()

        if webview_zone_count >= webview_safe_threshold:
            if not webview_safe_mode_set:
                webview_safe_mode = True
            if not webview_cooldown_set and webview_launch_cooldown <= 0:
                webview_launch_cooldown = 0.5
            if not webview_args_set and not webview_browser_args:
                webview_browser_args = "--renderer-process-limit=4"

        for i, zone in enumerate(zones):
            if i >= limit: break
            
            # [Fix] Initialize loop variables to prevent leakage
            required_cmdline_part_override = None

            z_type = zone.get("type", "web")
            content = zone.get("content", "")
            zone_use_edge = self._should_use_external_webview(zone)
            force_webview = False
            webview_kind = "web"

            if z_type == "video" and video_backend != "vlc":
                # Treat video as external WebView2 (HTML5) for stability
                force_webview = True
                zone_use_edge = True
                webview_kind = "video"
                content = zone.get("_generated_url")
                if not (content and str(content).strip()):
                    continue
                z_type = "web"

            if z_type == "video":
                video_path = content
                if not video_path:
                    continue
                if not os.path.exists(video_path):
                    print(f"[VLC] Video not found: {video_path}")
                    continue

                rx, ry, rw, rh = zone.get("_rect", (0, 0, 800, 600))
                vlc_tag = self._vlc_tag(self.current_scene_idx, i)
                active_vlc_tags.add(vlc_tag)

                # Resume if suspended
                if vlc_tag in self._vlc_suspended:
                    pid = self._vlc_pids.get(vlc_tag)
                    if pid:
                        try:
                            psutil.Process(pid).resume()
                        except Exception as e:
                            print(f"[VLC] Resume failed: {e}")
                    self._vlc_suspended.discard(vlc_tag)

                # Relaunch if content changed
                prev_path = self._vlc_paths.get(vlc_tag)
                if prev_path and os.path.abspath(prev_path) != os.path.abspath(video_path):
                    pid = self._vlc_pids.get(vlc_tag)
                    try:
                        if pid:
                            psutil.Process(pid).kill()
                    except:
                        pass
                    self._vlc_pids.pop(vlc_tag, None)
                    self._vlc_hwnds.pop(vlc_tag, None)
                    self._vlc_suspended.discard(vlc_tag)

                self._vlc_paths[vlc_tag] = video_path

                # Check if process for this tag already running
                is_running = False
                pid = self._vlc_pids.get(vlc_tag)
                if pid:
                    try:
                        proc = psutil.Process(pid)
                        is_running = proc.is_running()
                    except:
                        is_running = False

                if not is_running:
                    try:
                        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                            try:
                                cmdline = p.info['cmdline'] or []
                                if cmdline and vlc_tag in " ".join(cmdline):
                                    is_running = True
                                    self._vlc_pids[vlc_tag] = p.info['pid']
                                    break
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                    except Exception as e:
                        print(f"[Warning] VLC process scan failed: {e}")

                if not is_running:
                    # Throttle relaunch to avoid runaway spawning
                    last_launch = self._vlc_last_launch.get(vlc_tag, 0)
                    if time.time() - last_launch < 2.0:
                        continue
                    vlc_exe = self.vlc_path or "vlc"
                    vlc_args = [
                        f'"{vlc_exe}"',
                        "--no-video-title-show",
                        "--no-qt-privacy-ask",
                        "--qt-minimal-view",
                        "--no-qt-fs-controller",
                        "--no-qt-system-tray",
                        "--qt-notification=0",
                        "--loop",
                        "--quiet",
                        "--avcodec-hw=any",
                        f'--meta-title="{vlc_tag}"',
                        f'--input-title-format={vlc_tag}',
                        f'"{video_path}"'
                    ]
                    launch_cmd = " ".join(vlc_args)
                    print(f"[VLC] Launching: {launch_cmd}")
                    proc = self.launch_exe(launch_cmd)
                    if proc:
                        self._vlc_pids[vlc_tag] = proc.pid
                    self._vlc_last_launch[vlc_tag] = time.time()

                # Find VLC window by PID (title match preferred, PID match fallback)
                pid = self._vlc_pids.get(vlc_tag)

                def _vlc_hwnd_ok(h):
                    if not (h and win32gui.IsWindow(h)):
                        return False
                    try:
                        title = safe_get_window_text(h) or ""
                        if vlc_tag in title:
                            return True
                    except:
                        pass
                    if pid:
                        try:
                            _, wpid = win32process.GetWindowThreadProcessId(h)
                            if wpid == pid:
                                return True
                        except:
                            pass
                    return False

                hwnd = self._vlc_hwnds.get(vlc_tag)
                if not _vlc_hwnd_ok(hwnd):
                    self._vlc_hwnds.pop(vlc_tag, None)
                    hwnd = None

                if not hwnd:
                    hwnd = self.find_main_window_for_exe(self.vlc_path or "vlc.exe", track_pid=pid)
                    if not _vlc_hwnd_ok(hwnd):
                        hwnd = None

                if hwnd:
                    self._vlc_hwnds[vlc_tag] = hwnd

                    # Ensure visible
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    if not win32gui.IsWindowVisible(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

                    try:
                        if zone.get("strip_border", True):
                            self.strip_borders(hwnd)
                    except:
                        pass

                    prx, pry, prw, prh = self._rect_to_physical_desktop(
                        rx, ry, rw, rh, vlc_scale_x, vlc_scale_y, screen_geo
                    )

                    flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW | win32con.SWP_FRAMECHANGED | 0x4000
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, prx, pry, prw, prh, flags)

                continue

            # Allow exe, rdp, OR web-with-edge
            is_native_edge = (z_type == "web" and (zone_use_edge or force_webview))
            
            if z_type not in ["exe", "rdp"] and not is_native_edge: continue
            
            if not content: continue
            
            # Determine path to track
            path_to_track = content
            launch_cmd = content
            
            if z_type == "rdp":
                path_to_track = "mstsc.exe"
                launch_cmd = f"mstsc /v:{content}"
            # [Fix] Force Internal WebEngine (Ignore legacy 'use_edge' config)
            elif z_type == "web" and zone_use_edge:
                # [WebView2 Integration] Replacing Native Edge with Embedded WebView2
                # Instead of launching msedge.exe, we launch our helper script webview_player.py
                target_url = content
                if not (target_url and str(target_url).strip()):
                    print(f"[Navigation] Empty URL for WebView2 Zone {i}, skipping launch.")
                    continue
                if target_url and not (
                    target_url.startswith("http://")
                    or target_url.startswith("https://")
                    or target_url.startswith("file://")
                    or target_url.startswith("about:")
                ):
                     target_url = "https://" + target_url
                if bool(zone.get("cache_bust", False)):
                    target_url = append_scene_cache_buster(
                        target_url,
                        getattr(self, "_scene_load_token", time.time_ns()),
                    )

                # Calculate target rect in both logical (_rect) and physical pixels.
                # enforce_loop compares against physical GetWindowRect values,
                # so WebView launch/host commands must use physical coordinates too.
                rx, ry, rw, rh = zone.get("_rect", (0,0,800,600))
                prx, pry, prw, prh = self._rect_to_physical_desktop(
                    rx, ry, rw, rh, vlc_scale_x, vlc_scale_y, screen_geo
                )
                
                # Construct specialized title to track it
                # Title format: "WebView2_S{scene}_Z{zone}"
                # We use this to distinguish between multiple python processes
                webview_tag = self._webview_tag(self.current_scene_idx, i)
                webview_order = len(active_webview_tags)
                active_webview_tags.add(webview_tag)
                if not hasattr(self, "_webview_host_tag_kind"):
                    self._webview_host_tag_kind = {}
                self._webview_host_tag_kind[webview_tag] = webview_kind
                if not hasattr(self, "_webview_host_tag_url"):
                    self._webview_host_tag_url = {}
                self._webview_host_tag_url[webview_tag] = target_url
                
                # [Fix] Assign to override variable so FindWindow uses it (and PIDs)!
                required_cmdline_part_override = webview_tag
                
                isolate_webview = self._zone_uses_isolated_webview(zone, target_url)
                if isolate_webview:
                    active_isolated_webview_tags.add(webview_tag)

                use_host = bool(
                    webview_host_mode
                    and not isolate_webview
                    and (webview_host_ready or not webview_host_allow_fallback)
                )
                if use_host:
                    active_host_webview_tags.add(webview_tag)
                    # Single Host process: send IPC command, no per-window launch
                    webview_host_cmds.append({
                        "op": "upsert",
                        "tag": webview_tag,
                        "url": target_url,
                        "x": prx,
                        "y": pry,
                        "w": prw,
                        "h": prh,
                        "show": True,
                        "resume_media": webview_kind == "web",
                        "reload_on_show": self._webview_reload_on_show(target_url),
                        "viewport_fix": self._webview_needs_viewport_fix(target_url),
                        "delay_ms": webview_order * webview_host_stagger_ms
                    })
                    if getattr(self, "_webview_host_pid", None):
                        if not hasattr(self, '_webview_pids'):
                            self._webview_pids = {}
                        self._webview_pids[webview_tag] = self._webview_host_pid

                    # Track host executable for window matching
                    path_to_track = self._webview_host_exe or "webviewplayer.exe"
                    required_cmdline_part_override = webview_tag
                    launch_cmd = f"[Host] {webview_tag} {target_url}"
                else:
                    # [Fix] Frozen/Source Detection for WebViewPlayer
                    launch_executable = ""
                    launch_args = ""
                    path_to_track_initial = ""

                    if getattr(sys, 'frozen', False):
                        # Bundled mode: Use standalone EXE
                        base_path = os.path.dirname(sys.executable)
                        # It's in Player/WebViewPlayer/webview_player.exe
                        wv_dir = os.path.join(base_path, "WebViewPlayer")
                        wv_exe = os.path.join(wv_dir, "WebViewPlayer.exe")
                        if not os.path.exists(wv_exe):
                            # Backward-compatible fallback
                            alt = os.path.join(wv_dir, "webview_player.exe")
                            if os.path.exists(alt):
                                wv_exe = alt
                        
                        launch_executable = wv_exe
                        launch_args = "" # Arguments appended directly
                        path_to_track_initial = wv_exe
                    else:
                        # Script mode: Use python webview_player.py
                        base_path = os.path.dirname(os.path.abspath(__file__))
                        script_path = os.path.join(base_path, "webview_player.py")
                        
                        launch_executable = sys.executable
                        launch_args = f'"{script_path}"'
                        path_to_track_initial = sys.executable
                    
                    # [Fix] Use a per-tag profile path to avoid WebView2 profile lock conflicts
                    profile_path = self.get_external_web_profile_path(webview_tag)
                    
                    # Launch command uses physical pixels to avoid DPI tug-of-war.
                    safe_part = " --safe-mode" if webview_safe_mode else ""
                    browser_args_part = ""
                    if webview_browser_args:
                        # argparse treats values that start with "--" as options when passed as a separate token.
                        # Use "--browser-args=<value>" so WebViewPlayer can parse it reliably.
                        browser_args_clean = str(webview_browser_args).strip().strip('"').strip("'")
                        if " " in browser_args_clean:
                            browser_args_clean = browser_args_clean.split()[0]
                            print(f"[WebView] browser_args has spaces; using first token: {browser_args_clean}")
                        browser_args_part = f" --browser-args={browser_args_clean}"
                    viewport_fix_part = " --viewport-fix" if self._webview_needs_viewport_fix(target_url) else ""
                    standalone_log_path = os.path.abspath(os.path.join(self.data_dir, f"webview_{webview_tag}.log"))
                    launch_cmd = f'"{launch_executable}" {launch_args} --url "{target_url}" --title "{webview_tag}" --x {prx} --y {pry} --width {prw} --height {prh} --profile-path "{profile_path}" --log-path "{standalone_log_path}"{safe_part}{browser_args_part}{viewport_fix_part}'
                    
                    # IMPORTANT: path_to_track is the actual executable running the window
                    # In frozen mode -> webview_player.exe
                    # In script mode -> python.exe
                    path_to_track = path_to_track_initial
                    
                    # We set a special override variable that we will use later in find_main_window_for_exe
                    # This ensures we pick the specific python window with our Title in cmdline
                    required_cmdline_part_override = webview_tag

                    # [Navigation Fix] Force one-time launch per scene
                    if not hasattr(self, '_scene_nav_tracker'):
                        self._scene_nav_tracker = (-1, set())
                    
                    if self._scene_nav_tracker[0] != self.current_scene_idx:
                        self._scene_nav_tracker = (self.current_scene_idx, set())
                        
                    if i not in self._scene_nav_tracker[1]:
                        # Check if already running?
                        # is_process_running checks by name/exe.
                        # We need to check by Title/Cmdline for python scripts.
                        is_running = False
                        try:
                            # psutil imported at module level
                            # [Optimization] Iterate only once and be robust against Access Denied
                            for p in psutil.process_iter(['name', 'cmdline']):
                                try:
                                    pname = (p.info['name'] or "").lower()
                                    if "python" in pname or "webviewplayer" in pname:
                                        cmdline = p.info['cmdline']
                                        if cmdline:
                                            # Use faster string search
                                            if webview_tag in " ".join(cmdline):
                                                is_running = True
                                                break
                                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                                    pass
                        except Exception as e:
                             print(f"[Warning] psutil check failed: {e}")
                        
                        launched_or_running = is_running
                        if is_running:
                            print(f"[Navigation] Skipping WebView launch for Zone {i}, tag {webview_tag} already running.")
                        else:
                            print(f"[Navigation] Launching WebView for Zone {i}: {target_url}")
                            now = time.time()
                            if not hasattr(self, '_webview_tag_last_launch'):
                                self._webview_tag_last_launch = {}
                            last_tag_launch = self._webview_tag_last_launch.get(webview_tag, 0)
                            if webview_tag_cooldown > 0 and (now - last_tag_launch) < webview_tag_cooldown:
                                print(f"[Navigation] WebView tag cooldown active for {webview_tag} ({webview_tag_cooldown}s)")
                            elif webview_launch_cooldown > 0 and (now - self._last_webview_launch_time) < webview_launch_cooldown:
                                # Throttle WebView launches to avoid resource spikes
                                print(f"[Navigation] WebView launch throttled for Zone {i} (cooldown={webview_launch_cooldown}s)")
                            else:
                                proc = self.launch_exe(launch_cmd)
                                
                                # [Track PID] Store PID for robust window finding
                                if proc and proc.pid:
                                     if not hasattr(self, '_webview_pids'): self._webview_pids = {}
                                     self._webview_pids[webview_tag] = proc.pid
                                     print(f"[PID Tracker] Registered {webview_tag} -> PID {proc.pid}")
                                     launched_or_running = True
                                     self._last_webview_launch_time = now
                                     self._webview_tag_last_launch[webview_tag] = now
                        
                        # Only mark this zone as "launched" when we confirmed running or launch succeeded.
                        # If launch failed, keep retrying in the same scene.
                        if launched_or_running:
                            self._scene_nav_tracker[1].add(i)
                    
                    # Log only once
                    zone_debug_key = (self.current_scene_idx, i, target_url)
                    if zone_debug_key not in self._debug_zones_printed:
                         self.log_debug(f"[WebView Cmd] {launch_cmd}")
                         self._debug_zones_printed.add(zone_debug_key)
            
            active_paths_this_frame.add(path_to_track)
            
            # 1. Find Window
            # Use exclude_hwnds to prevent picking the same window for multiple zones
            print(f"[DEBUG] Searching for window: {path_to_track}")
            
            # For browsers, check if we have a cached HWND first
            hwnd = None
            path_to_track_lower = path_to_track.lower()
            path_to_track_name = os.path.basename(path_to_track_lower)
            is_browser = path_to_track_name in ["chrome.exe", "msedge.exe"]
            is_managed_webview = "python" in path_to_track_lower or "webview" in path_to_track_lower
            generic_target_pid = None
            if hasattr(self, '_generic_pids'):
                generic_target_pid = self._generic_pids.get(path_to_track_lower)
                if not generic_target_pid:
                    generic_target_pid = self._generic_pids.get(path_to_track_name)

            # Keep a stable tag->HWND binding for managed WebView windows.
            # This prevents zone windows from swapping positions when candidate scoring fluctuates.
            if required_cmdline_part_override and is_managed_webview:
                try:
                    if not hasattr(self, '_webview_tag_hwnds'):
                        self._webview_tag_hwnds = {}
                    cached_tag_hwnd = self._webview_tag_hwnds.get(required_cmdline_part_override)
                    if cached_tag_hwnd and win32gui.IsWindow(cached_tag_hwnd):
                        if cached_tag_hwnd not in found_hwnds:
                            hwnd = cached_tag_hwnd
                            print(f"[DEBUG] Using cached tag HWND {hwnd} for {required_cmdline_part_override}")
                    elif cached_tag_hwnd:
                        del self._webview_tag_hwnds[required_cmdline_part_override]
                except Exception:
                    pass

            # For EXEs, try cached HWND first (helps when hidden/off-screen)
            if not is_browser:
                try:
                    cached_exe_hwnd = self._exe_hwnds.get(path_to_track.lower())
                    if cached_exe_hwnd and win32gui.IsWindow(cached_exe_hwnd):
                        if (
                            cached_exe_hwnd not in found_hwnds
                            and self._hwnd_matches_exact_target_exe(
                                cached_exe_hwnd, path_to_track, track_pid=generic_target_pid
                            )
                        ):
                            hwnd = cached_exe_hwnd
                            print(f"[DEBUG] ??Using cached EXE HWND {hwnd} for {path_to_track}")
                        else:
                            del self._exe_hwnds[path_to_track.lower()]
                except:
                    pass
            
            # Determine the cache key (URL for browsers, path for EXEs)
            # Check if target_url was defined (only for web zones)
            cache_key = None
            if is_browser and 'target_url' in locals():
                cache_key = target_url
            
            
            if cache_key and cache_key in self._browser_hwnds:
                cached_hwnd = self._browser_hwnds[cache_key]
                # Verify the HWND is still valid
                try:
                    if win32gui.IsWindow(cached_hwnd):
                        # Check if it's not in exclude list (found_hwnds)
                        if cached_hwnd not in found_hwnds:
                            hwnd = cached_hwnd
                            print(f"[DEBUG] ??Using cached HWND {hwnd} for {cache_key}")
                        else:
                            print(f"[DEBUG] Cached HWND {cached_hwnd} already used in this frame, searching for new window")
                    else:
                        print(f"[DEBUG] Cached HWND {cached_hwnd} is no longer valid (IsWindow=False), removing from cache")
                        del self._browser_hwnds[cache_key]
                except Exception as e:
                    print(f"[DEBUG] Error checking cached HWND {cached_hwnd}: {e}, removing from cache")
                    if cache_key in self._browser_hwnds:
                        del self._browser_hwnds[cache_key]
            
            # If no cached HWND, search for window
            if not hwnd:
                is_host_ipc_cmd = isinstance(launch_cmd, str) and launch_cmd.startswith("[Host] ")
                # [Fix] WebView2 fast-path: find by window title tag before PID/name matching
                if required_cmdline_part_override:
                    try:
                        found_by_title = None
                        def _title_cb(h, _):
                            nonlocal found_by_title
                            try:
                                if h in found_hwnds:
                                    return True
                                t = safe_get_window_text(h)
                                if t and required_cmdline_part_override in t:
                                    found_by_title = h
                                    return False
                            except:
                                pass
                            return True
                        win32gui.EnumWindows(_title_cb, None)
                        if found_by_title:
                            hwnd = found_by_title
                            print(f"[DEBUG] ??Found window by title tag: {hwnd}")
                    except Exception as e:
                        print(f"[DEBUG] Title search failed for {required_cmdline_part_override}: {e}")
                
                # [Fix] WebView2 cmdline-based search (handles empty titles)
                if not hwnd and required_cmdline_part_override:
                    try:
                        # psutil imported at module level
                        found_by_cmdline = None
                        def _cmd_cb(h, _):
                            nonlocal found_by_cmdline
                            try:
                                if h in found_hwnds:
                                    return True
                                _, pid = win32process.GetWindowThreadProcessId(h)
                                if not pid:
                                    return True
                                proc = psutil.Process(pid)
                                cmdline = " ".join(proc.cmdline()).lower()
                                if required_cmdline_part_override.lower() in cmdline:
                                    found_by_cmdline = h
                                    return False
                            except:
                                pass
                            return True
                        win32gui.EnumWindows(_cmd_cb, None)
                        if found_by_cmdline:
                            hwnd = found_by_cmdline
                            print(f"[DEBUG] ??Found window by cmdline tag: {hwnd}")
                    except Exception as e:
                        print(f"[DEBUG] Cmdline search failed for {required_cmdline_part_override}: {e}")

                # Host mode: if tag-based matching failed, do NOT fall back to EXE/PID matching.
                # Fallback may grab the hidden host bootstrap window (__WEBVIEW_HOST__),
                # causing invalid HWND errors and black-screen loops.
                if not hwnd and is_host_ipc_cmd and required_cmdline_part_override:
                    pass
                elif not hwnd:
                # For browsers, check if we recently launched this URL
                    recently_launched_browser = False
                    if is_browser and cache_key:
                        if cache_key in self._launched_browsers:
                            time_since_launch = time.time() - self._launched_browsers[cache_key]
                            recently_launched_browser = time_since_launch < 10  # Within 10 seconds
                            if recently_launched_browser:
                                print(f"[DEBUG] Browser was launched {time_since_launch:.1f}s ago, using lenient matching")
                
                    # Calculate expected profile folder for strict matching (Browsers Only)
                    expected_profile = None
                    if is_browser:
                        # Must match the folder name used in launch_cmd
                        # [Fix] Match shorter path from Step 499
                        expected_profile = f"bd_S{self.current_scene_idx}_{i}"
                
                    # [Fix] WebView2/Python window targeting
                    # If we have a specific cmdline tag (from WebView2 launch), Use it!
                    cmdline_req = expected_profile
                    if required_cmdline_part_override:
                         cmdline_req = required_cmdline_part_override

                    # [PID Tracking]
                    target_pid = None
                    if required_cmdline_part_override and hasattr(self, '_webview_pids'):
                        target_pid = self._webview_pids.get(required_cmdline_part_override)
                
                    # [Fix] Fallback to Generic PID Tracker
                    if not target_pid and hasattr(self, '_generic_pids'):
                        target_pid = self._generic_pids.get(path_to_track.lower())
                
                    # [PID Recovery] If we lost the PID (e.g. after restart), try to find it again
                    if not target_pid and required_cmdline_part_override:
                        print(f"[PID Recovery] Scanning for orphaned process with tag: {required_cmdline_part_override}")
                        try:
                            # psutil imported at module level
                            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                                try:
                                    pname = (p.info['name'] or "").lower()
                                    if "python" in pname or "webviewplayer" in pname:
                                        cmdline = p.info['cmdline']
                                        if cmdline and required_cmdline_part_override in " ".join(cmdline):
                                            target_pid = p.info['pid']
                                            if not hasattr(self, '_webview_pids'): self._webview_pids = {}
                                            self._webview_pids[required_cmdline_part_override] = target_pid
                                            print(f"[PID Recovery] SUCCESS. Recovered PID {target_pid}")
                                            break
                                except: pass
                        except: pass
                
                    hwnd = self.find_main_window_for_exe(
                        path_to_track, 
                        exclude_hwnds=found_hwnds, 
                        recently_launched=recently_launched_browser, 
                        required_cmdline_part=cmdline_req,
                        track_pid=target_pid
                    )
                
                    # If found, cache it for browsers
                    if hwnd and cache_key:
                        self._browser_hwnds[cache_key] = hwnd
                        print(f"[DEBUG] Cached HWND {hwnd} for {cache_key}")
            
            if not hwnd:
                # [Fix] Stuck Browser Recovery
                if is_browser and cache_key and cache_key in self._launched_browsers:
                    time_since = time.time() - self._launched_browsers[cache_key]
                    if time_since > 12.0:
                         try:
                             expected_profile_kill = f"bd_S{self.current_scene_idx}_{i}"
                             # psutil imported at module level
                             for p in psutil.process_iter(['name', 'cmdline']):
                                 if p.info['name'] and p.info['name'].lower() in ["chrome.exe", "msedge.exe"]:
                                     try:
                                         c = " ".join(p.info['cmdline'] or []).lower()
                                         if expected_profile_kill.lower() in c:
                                             print(f"[Watchdog] Killing stuck PID {p.pid}")
                                             p.kill()
                                     except: pass
                         except: pass
                         if cache_key in self._launched_browsers: del self._launched_browsers[cache_key]

            if hwnd:
                print(f"[DEBUG] ??Found window HWND: {hwnd}")
                if required_cmdline_part_override and required_cmdline_part_override in self._webview_missing_since:
                    del self._webview_missing_since[required_cmdline_part_override]
                if required_cmdline_part_override and is_managed_webview:
                    try:
                        if not hasattr(self, '_webview_tag_hwnds'):
                            self._webview_tag_hwnds = {}
                        self._webview_tag_hwnds[required_cmdline_part_override] = hwnd
                    except Exception:
                        pass

                found_hwnds.add(hwnd)
                found_active_manager_names.add(os.path.basename(path_to_track).lower())
                # Cache EXE HWNDs for reliable restore
                if not is_browser and not is_managed_webview:
                    try:
                        if self._hwnd_matches_exact_target_exe(hwnd, path_to_track, track_pid=generic_target_pid):
                            self._exe_hwnds[path_to_track.lower()] = hwnd
                    except:
                        pass
                
                # 2. Show
                # Ensure it is VISIBLE and RESTORED
                is_visible = win32gui.IsWindowVisible(hwnd)
                print(f"[DEBUG] Window visible: {is_visible}")
                if not is_visible:
                    print(f"[DEBUG] Waking up {path_to_track} (SHOW)")
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    # Bring to front using TOPMOST trick
                    try:
                        flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
                        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
                    except:
                        pass
            
                # 3. Strip Borders Enforcement (Enhanced)
                # Check EVERY frame if the window has re-acquired borders (some apps do this on resize)
                if zone.get("strip_border", True) and not is_managed_webview:
                    try:
                        current_style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                        if (current_style & win32con.WS_CAPTION) or (current_style & win32con.WS_THICKFRAME):
                            print(f"[DEBUG] Detected borders on {path_to_track}, stripping...")
                            self.strip_borders(hwnd)
                    except:
                        pass
                
                # 4. Move
                rx, ry, rw, rh = zone.get("_rect", (0,0,100,100))
                
                # Convert Logical Pixels (Qt) to Physical Pixels (Windows API)
                # Use the same scale model as launch/host commands to avoid DPI drift.
                prx, pry, prw, prh = self._rect_to_physical_desktop(
                    rx, ry, rw, rh, vlc_scale_x, vlc_scale_y, screen_geo
                )
                
                # Clamp to this profile's target screen only. Using virtualGeometry
                # multiplied by the current screen DPR lets a secondary monitor's
                # scaling distort another monitor's bounds.
                screen_left_px, screen_top_px, screen_w_px, screen_h_px = self._rect_to_physical_desktop(
                    0, 0, screen_geo.width(), screen_geo.height(), vlc_scale_x, vlc_scale_y, screen_geo
                )
                screen_right_px = screen_left_px + screen_w_px
                screen_bottom_px = screen_top_px + screen_h_px

                if prw >= screen_w_px:
                    prx = screen_left_px
                else:
                    max_x = screen_right_px - prw
                    prx = max(screen_left_px, min(prx, max_x))

                if prh >= screen_h_px:
                    pry = screen_top_px
                else:
                    max_y = screen_bottom_px - prh
                    pry = max(screen_top_px, min(pry, max_y))
                
                print(f"[DEBUG] Target position: ({prx}, {pry}) size: {prw}x{prh}")
                
                try:
                    # CRITICAL: Check visibility FIRST, before any position checks
                    # This must happen regardless of position, because SW_HIDE doesn't change position
                    
                    # 1. Check if Minimized (Iconic)
                    if win32gui.IsIconic(hwnd):
                        print(f"[DEBUG] Window is Minimized (Iconic), restoring: {path_to_track}")
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                        time.sleep(0.05)

                    # 2. Check if Hidden
                    if not win32gui.IsWindowVisible(hwnd):
                        print(f"[DEBUG] Window is hidden (SW_HIDE), showing it...")
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                        # Wait a bit for show to complete
                        time.sleep(0.05)
                    
                    # Check current pos to avoid jitter
                    rect = win32gui.GetWindowRect(hwnd)
                    cx, cy = rect[0], rect[1]
                    cw = rect[2] - rect[0]
                    ch = rect[3] - rect[1]
                    
                    print(f"[DEBUG] Current position: ({cx}, {cy}) size: {cw}x{ch}")
                    
                    # Looser tolerance (Physical Pixels)
                    tol_pos = 10
                    tol_size = 10
                    
                    # Force update if this is the first time we see this HWND in this scene
                    force_update = False
                    if hwnd not in self.known_hwnds:
                        print(f"[DEBUG] First time seeing HWND {hwnd} in this scene. Forcing Update.")
                        force_update = True
                        self.known_hwnds.add(hwnd)

                    # Z-Order Fix: Set Owner to Player
                    # SKIP for Chromium-based windows (msedge/chrome/webview), setting owner breaks GPU rendering (black screen)
                    # Note: WebView2 windows are hosted by python/webview_player.exe, but are still Chromium.
                    is_chromium = (
                        "msedge.exe" in path_to_track.lower()
                        or "chrome.exe" in path_to_track.lower()
                        or "python.exe" in path_to_track.lower()
                        or "webview" in path_to_track.lower()
                    )
                    skip_owner = self._should_skip_owner(path_to_track)
                    if skip_owner:
                        try:
                            if win32gui.GetWindow(hwnd, win32con.GW_OWNER) == int(self.winId()):
                                print(f"[DEBUG] Clearing Owner for fragile external app: {path_to_track}")
                                win32gui.SetWindowLong(hwnd, -8, 0)
                        except Exception:
                            pass
                    elif hwnd not in self.owned_hwnds and not is_chromium:
                        try:
                            # GWL_HWNDPARENT = -8. Sets the Owner window.
                            # Owned windows are always above their owner in Z-order.
                            print(f"[DEBUG] Setting Owner of {hwnd} to Player ({int(self.winId())})")
                            win32gui.SetWindowLong(hwnd, -8, int(self.winId()))
                            self.owned_hwnds.add(hwnd)
                        except Exception as e:
                            print(f"[DEBUG] Failed to set Owner for {hwnd}: {e}")

                    # Only force if significantly different OR forced
                    # Compare Physical coords
                    
                    # Check if window is minimized (position at -32000, -32000)
                    is_minimized = cx < -30000 or cy < -30000
                    
                    if is_minimized:
                        print(f"[DEBUG] Window is minimized, restoring...")
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                        force_update = True
                        # Wait a bit for restore to complete
                        # Wait a bit for restore to complete
                        time.sleep(0.1)
                        # Re-get position after restore
                        rect = win32gui.GetWindowRect(hwnd)
                        cx, cy = rect[0], rect[1]
                        cw = rect[2] - rect[0]
                        ch = rect[3] - rect[1]
                        print(f"[DEBUG] After restore: ({cx}, {cy}) size: {cw}x{ch}")
                    
                    # [Fix] Check if window has borders but shouldn't (Force Update to Strip Borders)
                    if zone.get("strip_border", True):
                        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                        if (style & win32con.WS_CAPTION) or (style & win32con.WS_THICKFRAME):
                             # print(f"[DEBUG] Window {hwnd} has borders but shouldn't. Forcing update.")
                             force_update = True
                    
                    # [Fix] Managed WebView tolerance
                    # Use slightly wider size tolerance to avoid endless retries on
                    # platform-constrained frame sizes (e.g. persistent ~20-30px deltas).
                    is_managed_webview = "python" in path_to_track.lower() or "webview" in path_to_track.lower()
                    if is_managed_webview:
                        tol_pos = 6
                        tol_size = max(12, int(min(prw, prh) * 0.02))
                        if not hasattr(self, "_managed_webview_refresh_at"):
                            self._managed_webview_refresh_at = {}
                        refresh_key = required_cmdline_part_override or hwnd
                        last_refresh = self._managed_webview_refresh_at.get(refresh_key, 0.0)
                        try:
                            refresh_interval = float(glob.get("webview_periodic_geometry_refresh_sec", 0) or 0)
                        except Exception:
                            refresh_interval = 0
                        if refresh_interval > 0 and time.time() - last_refresh > refresh_interval:
                            force_update = True
                            print(f"[DEBUG] Periodic WebView geometry refresh: {refresh_key}")

                    did_window_update = False
                    if (
                        force_update
                        or abs(cx - prx) > tol_pos
                        or abs(cy - pry) > tol_pos
                        or abs(cw - prw) > tol_size
                        or abs(ch - prh) > tol_size
                    ):
                        print(f"[DEBUG] Position/size differs (Force={force_update}), updating window {path_to_track}...")
                        
                        # CRITICAL: Ensure window is visible before resizing
                        # If window was hidden (SW_HIDE), it won't respond to SetWindowPos properly
                        if not win32gui.IsWindowVisible(hwnd):
                            print(f"[DEBUG] Window is hidden, showing it first...")
                            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                            # Wait a bit for show to complete
                            time.sleep(0.05)
                        
                        # Also bring window to front (in case it's behind other windows)
                        try:
                            # [Fix] For WebView2, use SW_RESTORE only if minimized or MAXIMIZED (stuck)
                            if is_managed_webview:
                                if win32gui.IsIconic(hwnd) or win32gui.IsZoomed(hwnd):
                                     # print(f"[DEBUG] Window is Min/Max, restoring to allow move: {hwnd}")
                                     win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                                else:
                                     # Ensure it's not minimized but don't spam RESTORE
                                     pass
                            else:
                                win32gui.BringWindowToTop(hwnd)
                        except:
                            pass  # Ignore errors
                        
                        # For Chromium browsers, ALWAYS use SetWindowPos (SW_MAXIMIZE doesn't work well without SetOwner)
                        is_chromium = "msedge.exe" in path_to_track.lower() or "chrome.exe" in path_to_track.lower() or is_managed_webview
                        
                        if is_chromium:
                            # Force to exact position and size
                            # Use SWP_NOACTIVATE to avoid stealing focus from Player
                            # But temporarily set TOPMOST to ensure visibility, then remove it
                            flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW | 0x4000 # SWP_ASYNCWINDOWPOS
                            print(f"[DEBUG] Forcing Chromium Resize (Physical): {path_to_track} -> {prx},{pry} {prw}x{prh}")
                            
                            # [Fix] Ensure borders are stripped for Chrome too if requested! 
                            # (We skip SetOwner, but stripping borders is usually safe and needed for "Fullscreen" look)
                            if zone.get("strip_border", True) and not is_managed_webview:
                                self.strip_borders(hwnd)

                            # First, set as TOPMOST to bring to front
                            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, prx, pry, prw, prh, flags)
                            # Then remove TOPMOST flag (keep it normal but on top)
                            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, prx, pry, prw, prh, flags | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                            # Extra: Bring to top one more time to ensure it's really on top
                            try:
                                win32gui.BringWindowToTop(hwnd)
                            except:
                                pass
                            did_window_update = True
                            if is_managed_webview:
                                self._managed_webview_refresh_at[refresh_key] = time.time()
                        else:
                            # Non-Chromium: Use Force Resize instead of Maximize to cover Taskbar
                            # SW_MAXIMIZE respects taskbar, forcing size does not (if borders are stripped)
                            
                            # Use SWP_NOACTIVATE to avoid stealing focus
                            # But use TOPMOST trick to ensure visibility
                            flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW | 0x4000 # SWP_ASYNCWINDOWPOS
                            print(f"[DEBUG] Forcing Resize (Physical): {path_to_track} -> {prx},{pry} {prw}x{prh}")
                            
                            # Ensure borders are stripped if full screen
                            if zone.get("strip_border", True) and not is_managed_webview:
                                self.strip_borders(hwnd)
                                
                            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, prx, pry, prw, prh, flags)
                            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, prx, pry, prw, prh, flags | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                            did_window_update = True
                            if is_managed_webview:
                                self._managed_webview_refresh_at[refresh_key] = time.time()

                    else:
                        print(f"[DEBUG] Window position OK, skipping resize")

                    # Dashboards/iVMS can remain alive but visually stuck behind the
                    # player or white after SW_HIDE. Refresh z-order and repaint even
                    # when geometry is already correct.
                    self._refresh_external_window_surface(
                        hwnd,
                        path_to_track,
                        force_redraw=force_update or did_window_update,
                    )
                    
                    # [Fix] Keep managed WebView2 above Player after focus changes (e.g., clicking trigger)
                    if is_managed_webview:
                        try:
                            if getattr(self, '_suspend_webview_raise_until', 0) > time.time():
                                raise Exception("Suspend webview raise during UI interaction")
                            # If controls are visible, keep WebView below to avoid flicker
                            try:
                                if self.controls.isVisible():
                                    raise Exception("Controls visible; skip webview raise")
                            except:
                                pass
                            if not hasattr(self, '_webview_zorder_last'):
                                self._webview_zorder_last = {}
                            zkey = required_cmdline_part_override or hwnd
                            now = time.time()
                            need_raise = did_window_update or (zkey not in self._webview_zorder_last)
                            if need_raise:
                                flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
                                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
                                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
                            self._webview_zorder_last[zkey] = now
                        except:
                            pass
                except Exception as e:
                    try:
                        err_text = str(e).lower()
                    except Exception:
                        err_text = ""
                    if "access is denied" in err_text or "存取被拒" in err_text:
                        try:
                            _, denied_pid = win32process.GetWindowThreadProcessId(hwnd)
                        except Exception:
                            denied_pid = None
                        self._log_window_access_denied(path_to_track, hwnd=hwnd, pid=denied_pid, action="SetWindowPos/ownership")
                    print(f"[DEBUG] Window Move Error: {e}")
            else:
                print(f"[DEBUG] ??Window not found for {path_to_track}")
                # Not found? Launch it.
                # For browsers, check if we've already launched this URL recently
                is_host_ipc_cmd = isinstance(launch_cmd, str) and launch_cmd.startswith("[Host] ")
                if is_host_ipc_cmd:
                    # Host mode uses IPC markers like "[Host] <tag> <url>".
                    # Never try to execute this marker as a process path.
                    if required_cmdline_part_override:
                        now = time.time()
                        if required_cmdline_part_override not in self._webview_missing_since:
                            self._webview_missing_since[required_cmdline_part_override] = now
                        missing_for = now - self._webview_missing_since.get(required_cmdline_part_override, now)
                        host_recovery_timeout = min(max(webview_missing_timeout, 5.0), 30.0)
                        if missing_for > host_recovery_timeout:
                            if webview_host_recovery_mode == "fallback":
                                self.disable_webview_host_temporarily(
                                    reason=f"{required_cmdline_part_override} missing window for {missing_for:.1f}s",
                                    cooldown_sec=webview_host_recovery_cooldown
                                )
                                webview_host_ready = False
                                if hasattr(self, '_scene_nav_tracker') and self._scene_nav_tracker[0] == self.current_scene_idx:
                                    self._scene_nav_tracker[1].discard(i)
                                if hasattr(self, '_webview_pids'):
                                    self._webview_pids.pop(required_cmdline_part_override, None)
                                self._webview_missing_since.pop(required_cmdline_part_override, None)
                                print(f"[Recovery] Falling back to standalone WebView launch for {required_cmdline_part_override}")
                            else:
                                webview_host_ready = bool(self.restart_webview_host(
                                    reason=f"{required_cmdline_part_override} missing window for {missing_for:.1f}s",
                                    cooldown_sec=webview_host_restart_cooldown
                                ))
                                self._webview_missing_since.pop(required_cmdline_part_override, None)
                                print(f"[Recovery] Host restart requested for {required_cmdline_part_override}")
                            continue
                    host_ok = self.ensure_webview_host()
                    webview_host_ready = bool(host_ok)
                    if host_ok and getattr(self, "_webview_host_pid", None) and 'webview_tag' in locals():
                        if not hasattr(self, '_webview_pids'):
                            self._webview_pids = {}
                        self._webview_pids[webview_tag] = self._webview_host_pid
                    if host_ok:
                        print(f"[DEBUG] Host mode active for {required_cmdline_part_override}, waiting for window creation")
                    else:
                        print(f"[DEBUG] WebView host unavailable, skip host marker launch for {required_cmdline_part_override}")
                    continue

                path_to_track_lower = path_to_track.lower()
                path_to_track_name = os.path.basename(path_to_track_lower)
                is_browser = path_to_track_name in [
                    "chrome.exe",
                    "msedge.exe",
                    "python.exe",
                    "webviewplayer.exe",
                    "webview_player.exe",
                ]
                
                # [Fix] Strict Liveness Check for WebView2
                # If python process exists, we must ensure it is OUR webview instance (Tag Check)
                can_launch = True
                if (
                    (
                        "python.exe" in path_to_track_lower
                        or "webviewplayer.exe" in path_to_track_lower
                        or "webview_player.exe" in path_to_track_lower
                    )
                    and 'webview_tag' in locals()
                ):
                     tag_found = False
                     try:
                         # psutil imported at module level
                         for p in psutil.process_iter(['cmdline']):
                             try:  # Handle access denied on individual process
                                 if p.info['cmdline'] and webview_tag in " ".join(p.info['cmdline']):
                                     tag_found = True
                                     break
                             except: pass
                     except: pass
                     
                     if tag_found:
                         # Track missing window duration; if it stays missing, kill and relaunch
                         now = time.time()
                         if webview_tag not in self._webview_missing_since:
                             self._webview_missing_since[webview_tag] = now
                         missing_for = now - self._webview_missing_since.get(webview_tag, now)
                         print(f"[DEBUG] WebView Tag {webview_tag} found (Process Alive). Window missing for {missing_for:.1f}s")
                         if missing_for > webview_missing_timeout:
                             try:
                                 for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                                     try:
                                         cmdline = " ".join(p.info['cmdline'] or [])
                                         name = (p.info['name'] or "").lower()
                                         if webview_tag in cmdline and ("webview_player.py" in cmdline or "webview" in name or "python" in name):
                                             print(f"[Recovery] Killing stale WebView process for {webview_tag} (PID: {p.info['pid']})")
                                             p.kill()
                                     except: pass
                             except: pass
                             if hasattr(self, '_webview_pids') and webview_tag in self._webview_pids:
                                 del self._webview_pids[webview_tag]
                             if webview_tag in self._webview_missing_since:
                                 del self._webview_missing_since[webview_tag]
                             tag_found = False
                         if tag_found:
                             can_launch = False
                     else:
                         if webview_tag in self._webview_missing_since:
                             del self._webview_missing_since[webview_tag]

                if can_launch and is_browser and 'target_url' in locals():
                    current_time = time.time()
                    
                    # Check if we launched this URL in the last 10 seconds
                    if target_url in self._launched_browsers:
                        last_launch = self._launched_browsers[target_url]
                        if current_time - last_launch < 10:
                            print(f"[DEBUG] Browser for {target_url} was launched {current_time - last_launch:.1f}s ago, waiting...")
                            continue
                    
                    # Launch and record
                    print(f"[DEBUG] Launching {launch_cmd}")
                    self.launch_exe(launch_cmd)
                    self._launched_browsers[target_url] = current_time
                else:
                    # Non-browser: use old logic
                    # If can_launch is False (because Tag Found), we mistakenly fall here?
                    # No, if is_browser is True, we enter first branch (if can_launch).
                    # If can_launch False, we fall to `else`.
                    # Inside `else`, we call `is_process_running` (Generic).
                    # If generic returns True, we say "Running".
                    # If `tag_found` (can_launch=False), `is_process_running` will definitely be True.
                    # So it prints "Process is running but window not found". which is CORRECT.
                    
                    # Only risk: If is_browser=True, can_launch=False. Fall to else.
                    # is_process_running is True. -> Correct.
                    
                    # [Fix] Capture PID for Generic EXEs (Bypass strict path check)
                    is_running, pid = self.check_if_process_running(path_to_track)
                    print(f"[DEBUG] Process running check: {is_running} (PID: {pid})")
                    
                    if pid:
                         # Store PID for tracking
                         if not hasattr(self, '_generic_pids'): self._generic_pids = {}
                         self._generic_pids[path_to_track.lower()] = pid

                    if not is_running:
                        print(f"[DEBUG] Launching {launch_cmd}")
                        proc = self.launch_exe(launch_cmd)
                        if proc and proc.pid:
                             if not hasattr(self, '_generic_pids'): self._generic_pids = {}
                             self._generic_pids[path_to_track.lower()] = proc.pid
                             print(f"[PID Tracker] Registered Generic {path_to_track} -> PID {proc.pid}")
                    else:
                        print(f"[DEBUG] Process is running but window not found (still loading?)")
        
        # Minimize Inactive EXEs
        # Since we are "Owning" them, they sit on top of us. 
        # If we are showing something else (e.g. Web), we MUST hide/minimize the old EXE.
        # --- Z-Order & Cleanup Management (Enhanced) ---
        # Instead of just checking inactive paths, we scan ALL visible windows of managed apps
        # and hide any that are not currently claimed (found_hwnds).
        # This handles the case where an app (e.g. Chrome) is active, but has "extra" orphaned windows.
        
        print(f"[DEBUG] Active paths this frame: {active_paths_this_frame}")
        print(f"[DEBUG] Found HWNDs (Keep Visible): {found_hwnds}")
        




            

                
                # If it's a browser, check class. If not, we have to check PID (slower but necessary for custom EXEs)
                # Optimization: Only check PID if title is non-empty or it's a browser class?
                


        # Minimize Inactive EXEs (Passive Loop - Reverted from player_old.py)
        # Since we are "Owning" them, they sit on top of us. 
        # If we are showing something else (e.g. Web), we MUST hide/minimize the old EXE.
        print(f"[DEBUG] Active paths this frame: {active_paths_this_frame}")
        print(f"[DEBUG] All managed paths: {self.all_managed_paths}")
        
        # First, show windows that should be active but are hidden
        # Check all managed paths and see if they should be active
        for path in self.all_managed_paths:
            # Check if this path is in the active set (need to handle both full path and basename)
            is_active = False
            for active_path in active_paths_this_frame:
                if active_path == path or os.path.basename(active_path).lower() == os.path.basename(path).lower():
                    is_active = True
                    break

            if is_active:
                # For browsers, try to use cached HWND or use lenient search
                hwnd = None
                is_browser = path.lower() in ["chrome.exe", "msedge.exe"]

                if is_browser:
                    # Try to find cached HWND for any URL
                    for url, cached_hwnd in self._browser_hwnds.items():
                        try:
                            if win32gui.IsWindow(cached_hwnd):
                                hwnd = cached_hwnd
                                print(f"[DEBUG] Found cached browser HWND {hwnd} for {url}")
                                break
                        except:
                            pass

                # If no cached HWND, search with lenient mode for browsers
                if not hwnd:
                    # Use PID tracking for generic EXEs (launcher/child window cases)
                    target_pid = None
                    if hasattr(self, '_generic_pids'):
                        target_pid = self._generic_pids.get(path.lower())
                        if not target_pid:
                            target_pid = self._generic_pids.get(os.path.basename(path).lower())
                    hwnd = self.find_main_window_for_exe(path, recently_launched=is_browser, track_pid=target_pid)

                if hwnd:
                    if not win32gui.IsWindowVisible(hwnd):
                        print(f"[DEBUG] Showing Active App: {path} (HWND: {hwnd})")
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                        # Also bring to front using TOPMOST trick
                        try:
                            flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
                            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
                        except:
                            pass
        
        # Then, hide windows that should not be active
        
        # [Fix] browser_hwnds cleanup: Explicitly hide browser windows that are NOT in found_hwnds
        if hasattr(self, '_browser_hwnds'):
             for url, hwnd in self._browser_hwnds.items():
                if hwnd and win32gui.IsWindow(hwnd):
                    if hwnd not in found_hwnds:
                        if win32gui.IsWindowVisible(hwnd):
                             # print(f"[DEBUG] Hiding Inactive Browser Window: {url} (HWND: {hwnd})")
                             win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                             try:
                                flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                                win32gui.SetWindowPos(hwnd, 0, -3000, -3000, 0, 0, flags)
                             except: pass

        # [Nuclear Option] Rogue Browser Cleanup
        # If we are in a scene that uses browsers, we might have rogue windows from previous sessions 
        # that are NOT in _browser_hwnds (if they were launched before we started tracking properly).
        # We scan for ALL visible Chrome/Edge windows and hide them if they are not in found_hwnds.
        # only do this periodically to save CPU? No, z-order bugs are annoying. Do it every frame?
        # Maybe expensive. Let's do it if we found ANY browser window this frame.
        
        has_active_browser = any("chrome.exe" in p or "msedge.exe" in p for p in active_paths_this_frame)
        if has_active_browser:
            def hide_rogue_callback(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        cls = win32gui.GetClassName(hwnd)
                        if "Chrome_WidgetWin_1" in cls:
                             # It's a browser window. Is it one of ours?
                             if hwnd not in found_hwnds:
                                 # It is NOT an active window for this scene.
                                 # Check if it belongs to a "known" browser that should be hidden
                                 # But wait, what if it's the user's personal chrome?
                                 # We can check its thread ID / Process ID to see if it matches any of our managed processes?
                                 # But we know `chrome.exe` is managed.
                                 # If `chrome.exe` is in `all_managed_paths`, then ALL `chrome.exe` instances are technically fair game
                                 # unless we are very careful.
                                 # For this specific user (Kiosk context), we assume aggressive cleanup is desired.
                                 
                                 # Double check title to avoid hiding completely unrelated things if possible?
                                 # Actually, safer to check if process matches our known PIDs?
                                 # But we want to catch "orphaned" PIDs too.
                                 
                                 # Let's just HIDE it.
                                 # print(f"[DEBUG] Hiding ROGUE Browser Window: {hwnd}")
                                 win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                                 win32gui.SetWindowPos(hwnd, 0, -3000, -3000, 0, 0, win32con.SWP_NOACTIVATE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)
                    except:
                        pass
                return True
            try:
                win32gui.EnumWindows(hide_rogue_callback, None)
            except:
                pass
        
        # Cleanup done in load_scene now to prevent loop freeze
        pass

        # Send batched commands to WebView Host (single process)
        now = time.time()
        transition_grace_sec = self._webview_transition_grace_seconds()
        current_webview_ready = self._webview_tags_ready(active_webview_tags)
        protected_webview_tags = set(active_webview_tags)

        if webview_host_mode:
            previous_host_tags = set(getattr(self, "_webview_host_last_tags", set()) or set())
            stale_host_tags = previous_host_tags - set(active_host_webview_tags)
            pending_host_tags = dict(getattr(self, "_webview_pending_stale_host_tags", {}) or {})

            for active_tag in active_host_webview_tags:
                pending_host_tags.pop(active_tag, None)
            for stale_tag in stale_host_tags:
                pending_host_tags.setdefault(stale_tag, now + transition_grace_sec)

            due_host_tags = []
            for stale_tag, deadline in list(pending_host_tags.items()):
                if stale_tag in active_host_webview_tags:
                    pending_host_tags.pop(stale_tag, None)
                    continue
                if current_webview_ready or now >= float(deadline or now):
                    due_host_tags.append(stale_tag)
                    pending_host_tags.pop(stale_tag, None)
                else:
                    protected_webview_tags.add(stale_tag)

            self._webview_pending_stale_host_tags = pending_host_tags

            for stale_tag in sorted(due_host_tags):
                if self._webview_tag_should_keep_alive(stale_tag):
                    webview_host_cmds.append({
                        "op": "hide",
                        "tag": stale_tag,
                        "pause_media": True
                    })
                else:
                    webview_host_cmds.append({"op": "close", "tag": stale_tag})

        if webview_host_cmds:
            if not webview_host_ready:
                webview_host_ready = bool(self.ensure_webview_host())
            if webview_host_ready:
                self.send_webview_host_cmds(webview_host_cmds)
                self._webview_host_last_tags = set(active_host_webview_tags)
        elif not webview_host_mode:
            self._webview_host_last_tags = set()

        if webview_host_mode and webview_host_ready:
            self._check_active_webview_render(active_host_webview_tags)

        previous_isolated_tags = set(getattr(self, "_isolated_webview_last_tags", set()) or set())
        stale_isolated_tags = previous_isolated_tags - set(active_isolated_webview_tags)
        pending_isolated_tags = dict(getattr(self, "_webview_pending_stale_isolated_tags", {}) or {})

        for active_tag in active_isolated_webview_tags:
            pending_isolated_tags.pop(active_tag, None)
        for stale_tag in stale_isolated_tags:
            pending_isolated_tags.setdefault(stale_tag, now + transition_grace_sec)

        for stale_tag, deadline in list(pending_isolated_tags.items()):
            if stale_tag in active_isolated_webview_tags:
                pending_isolated_tags.pop(stale_tag, None)
                continue
            if current_webview_ready or now >= float(deadline or now):
                self._kill_webview_tag(stale_tag)
                pending_isolated_tags.pop(stale_tag, None)
            else:
                protected_webview_tags.add(stale_tag)

        self._webview_pending_stale_isolated_tags = pending_isolated_tags
        self._isolated_webview_last_tags = set(active_isolated_webview_tags)

        # Hide any inactive WebView2 windows by tag (more reliable than path-only)
        self.hide_inactive_webviews(protected_webview_tags)
        # Hide/Suspend inactive VLC windows
        self.hide_inactive_vlc(active_vlc_tags)

        # Debug: Show what we're checking
        print(f"[DEBUG] all_managed_paths: {self.all_managed_paths}")
        print(f"[DEBUG] active_paths_this_frame: {active_paths_this_frame}")
        
        for path in self.all_managed_paths:
            is_active = False
            for active_path in active_paths_this_frame:
                if active_path == path or os.path.basename(active_path).lower() == os.path.basename(path).lower():
                    is_active = True
                    break
            
            if not is_active:
                print(f"[DEBUG] Path {path} is INACTIVE, searching for window to hide...")
                
                # For browsers, try to use cached HWND
                hwnd = None
                is_browser = path.lower() in ["chrome.exe", "msedge.exe"]
                
                if is_browser:
                    # Try to find cached HWND for any URL
                    for url, cached_hwnd in self._browser_hwnds.items():
                        try:
                            if win32gui.IsWindow(cached_hwnd):
                                hwnd = cached_hwnd
                                print(f"[DEBUG] Found cached browser HWND {hwnd} for hiding")
                                break
                        except:
                            pass
                
                # If no cached HWND, search normally
                if not hwnd:
                    target_pid = None
                    if hasattr(self, '_generic_pids'):
                        target_pid = self._generic_pids.get(path.lower())
                        if not target_pid:
                            target_pid = self._generic_pids.get(os.path.basename(path).lower())
                    hwnd = self.find_main_window_for_exe(path, track_pid=target_pid)
                    print(f"[DEBUG] find_main_window_for_exe({os.path.basename(path)}) returned: {hwnd}")

                
                if hwnd:
                    if self._should_soft_hide_inactive_app(path):
                        if win32gui.IsWindowVisible(hwnd):
                            print(f"[DEBUG] Soft-hiding Inactive App: {path} (HWND: {hwnd})")
                            try:
                                flags = (
                                    win32con.SWP_NOACTIVATE
                                    | win32con.SWP_NOMOVE
                                    | win32con.SWP_NOSIZE
                                    | 0x0200  # SWP_NOOWNERZORDER
                                    | 0x4000  # SWP_ASYNCWINDOWPOS
                                )
                                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
                                win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0, flags)
                            except:
                                pass
                    elif win32gui.IsWindowVisible(hwnd):
                        # Force HIDE instead of Minimize
                        # Hiding removes it from Z-order considerations effectively
                        print(f"[DEBUG] Hiding Inactive App: {path} (HWND: {hwnd})")
                        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                        
                        # [Double Insurance] Move it off-screen in case SW_HIDE is ignored by some apps (e.g. Kiosk mode)
                        try:
                            flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                            win32gui.SetWindowPos(hwnd, 0, -3000, -3000, 0, 0, flags)
                        except:
                            pass
                    
                    # Also we might want to pop them from known_hwnds so we re-force them later
                    if hwnd in self.known_hwnds:
                        self.known_hwnds.remove(hwnd)
                    if hwnd in self.known_hwnds:
                        self.known_hwnds.remove(hwnd)
                else:
                    print(f"[DEBUG] Could not find window for inactive path: {path}")

        # External apps are raised during this loop; make the floating trigger
        # the final topmost window so it remains clickable.
        self._raise_trigger_topmost()

    def check_if_process_running(self, path):
        exe_name = os.path.basename(path).lower()
        target_path_lower = path.lower()
        normalized_target_path = ""
        if path and os.path.isabs(path):
            try:
                normalized_target_path = os.path.normcase(os.path.normpath(os.path.abspath(path))).lower()
            except Exception:
                normalized_target_path = target_path_lower
        
        # Special handling for Browsers to distinguish Kiosk instances from Personal instances
        is_browser = exe_name in ["chrome.exe", "msedge.exe"]
        
        found_processes = []
        kiosk_processes = []
        named_processes = []
        
        for proc in psutil.process_iter(['name', 'exe', 'cmdline', 'pid']):
            try:
                proc_exe = proc.info['exe'] or ""
                proc_exe_norm = ""
                if proc_exe:
                    try:
                        proc_exe_norm = os.path.normcase(os.path.normpath(proc_exe)).lower()
                    except Exception:
                        proc_exe_norm = proc_exe.lower()

                # 1. Name Match
                name_match = (proc.info['name'] and proc.info['name'].lower() == exe_name)
                # 2. Path Match (More precise)
                path_match = False
                if normalized_target_path and proc_exe_norm:
                    path_match = proc_exe_norm == normalized_target_path
                elif proc_exe:
                    path_match = proc_exe.lower() == target_path_lower
                
                if name_match or path_match:
                    found_processes.append(proc.info['pid'])
                    
                    # If it's a browser, we MUST check if it's OUR browser (Kiosk Mode)
                    if is_browser:
                        try:
                            cmdline = " ".join(proc.info['cmdline']).lower() if proc.info['cmdline'] else ""
                            # We identify our instance by usage of --app or user-data-dir
                            if "--app=" in cmdline or "user-data-dir" in cmdline:
                                kiosk_processes.append(proc.info['pid'])
                                print(f"[DEBUG] Found kiosk {exe_name} process: PID {proc.info['pid']}")
                                return True, proc.info['pid']
                            # Else: It's a personal browser, continue searching
                        except Exception as e:
                            # If we can't read cmdline, be conservative
                            # Assume it might be ours if it's recent (within 10 seconds)
                            print(f"[DEBUG] Can't read cmdline for PID {proc.info['pid']}: {e}")
                            pass
                    else:
                        # For absolute paths, prefer the exact executable path first.
                        if path_match:
                            print(f"[DEBUG] Found exact-path {exe_name} process: PID {proc.info['pid']}")
                            return True, proc.info['pid']
                        if name_match:
                            named_processes.append(proc.info['pid'])
                        
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if named_processes:
            print(f"[DEBUG] Found {exe_name} process: PID {named_processes[0]}")
            return True, named_processes[0]
        
        # Debug output
        if found_processes:
            print(f"[DEBUG] Found {len(found_processes)} {exe_name} processes, but {len(kiosk_processes)} are kiosk instances")
        else:
            print(f"[DEBUG] No {exe_name} processes found")
            
        return False, None

    def is_profile_running(self, profile_folder_name):
        """
        Check if a browser process is running with the specific profile folder.
        Args:
            profile_folder_name: The folder name (e.g. 'bd_S1_0') or full path part to match.
        """
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                name = proc.info['name'].lower() if proc.info['name'] else ""
                if name in ["chrome.exe", "msedge.exe"]:
                    cmdline = " ".join(proc.info['cmdline']).lower() if proc.info['cmdline'] else ""
                    if profile_folder_name.lower() in cmdline:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False


    def find_main_window_for_exe(self, path, exclude_hwnds=None, recently_launched=False, required_cmdline_part=None, track_pid=None):
        """
        Find the main window for an executable.
        
        Args:
            path: Path to the executable
            exclude_hwnds: Set of HWNDs to exclude
            recently_launched: If True, be more lenient for browsers (accept any visible window)
            track_pid: Optional specific PID to match (highest priority)
        """
        target_name = os.path.basename(path).lower()
        normalized_target_path = ""
        if path and os.path.isabs(path):
            try:
                normalized_target_path = os.path.normcase(os.path.normpath(os.path.abspath(path))).lower()
            except Exception:
                normalized_target_path = path.lower()
        
        # [Fix] Pre-calculate valid PIDs (Target + Children)
        # This solves the issue where WebView2 spawns child processes (msedge) that own the window
        valid_pids = set()
        if track_pid:
            valid_pids.add(track_pid)
            try:
                import psutil
                parent = psutil.Process(track_pid)
                children = parent.children(recursive=True)
                for child in children:
                    valid_pids.add(child.pid)
                # print(f"[DEBUG] TrackPID {track_pid} hierarchy: {valid_pids}")
            except: 
                pass
        strict_pid_filter = bool(
            valid_pids and (
                "python" in target_name
                or "webview" in target_name
                # or "vlc" in target_name  # [Fix] Loose filter for VLC to ensure we find the window
            )
        )

        candidates = []
        
        my_pid = os.getpid()
        
        def callback(hwnd, _):
            # Check exclusions
            if exclude_hwnds and hwnd in exclude_hwnds:
                return True
            
            # [Fix] Self-Exclusion: Do not return the Player's own window (Manager) as a content window
            # This prevents infinite resizing loops where Player finds itself and tries to resize itself to a Zone
            try:
                wt = safe_get_window_text(hwnd)
                if "Screen Player V4" in wt:
                    return True
            except: pass
            
            # [Fix] Ignore File Explorer (CabinetWClass) unless explicitly requested
            # This prevents "Captured my explorer" issues when path is generic or invalid
            try:
                cls = win32gui.GetClassName(hwnd)
                if cls == "CabinetWClass" and "explorer.exe" not in target_name:
                    return True
            except: pass
            
            # if not win32gui.IsWindowVisible(hwnd): return True # Filter removed to find SW_HIDE windows
            
            pid = 0
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except: pass
            
            # [Fix] Self-Exclusion by PID
            if pid == my_pid:
                return True
            
            # [Optimization] Early PID filter - skip windows that don't match track_pid before expensive psutil calls
            # This prevents massive "Access Denied" log spam when scanning system windows
            pid_match = bool(valid_pids and pid in valid_pids)
            if strict_pid_filter and not pid_match:
                # Quick reject: This window's PID doesn't match our target
                return True
            
            # [PID Match Priority]
            # Check against track_pid AND its children
            if strict_pid_filter and pid_match:
                try:
                    t = safe_get_window_text(hwnd)
                    if t and "__WEBVIEW_HOST__" in t:
                        return True
                except:
                    pass
                # Direct Hit! 
                # Verify visibility? Maybe not strictly required if we just launched it.
                # But we prefer visible.
                score = 1000
                if win32gui.IsWindowVisible(hwnd): score += 500
                
                # print(f"[DEBUG] PID Match {pid} (in {valid_pids}) for HWND {hwnd}")
                candidates.append((score, hwnd))
                return True

            # [Optimization] Filter by Window Class for Browsers to avoid expensive psutil calls
            if target_name in ["chrome.exe", "msedge.exe"]:
                try:
                    cls = win32gui.GetClassName(hwnd)
                    if "Chrome_WidgetWin_1" not in cls:
                        return True
                except:
                    pass
            
            try:
                # [Fix] Fallback: If psutil fails, check Window Class for Chrome
                name = ""
                exe = ""
                proc = None
                title = ""
                try:
                    proc = psutil.Process(pid)
                    name = proc.name().lower()
                    try: exe = proc.exe().lower() if proc.exe() else ""
                    except: pass
                except:
                    # Access Denied? Check class name as fallback for Chrome
                    if target_name in ["chrome.exe", "msedge.exe"]:
                        try:
                            cls = win32gui.GetClassName(hwnd)
                            if "Chrome_WidgetWin_1" in cls:
                                name = target_name # Force match
                        except: pass

                if self._is_rejected_ivms_helper(path, name, exe):
                    return True
                
                # [Strict PID Enforcement - Conditional]
                # Only apply strict PID check for python.exe/webview to prevent self-targeting.
                # For other apps (e.g. Tableau.exe), if PID tracking fails (Launcher), we WANT to fall back to name match.
                if strict_pid_filter and not pid_match:
                    return True
                
                # Check Name Match OR Same Directory Match
                # [Fix] Handle Launchers (ProjectDashboard -> UnityPlayer)
                # If name doesn't match, check if they live in the same folder.
                exe_basename = os.path.basename(exe) if exe else ""
                exact_path_match = False
                if normalized_target_path and exe:
                    try:
                        exe_norm = os.path.normcase(os.path.normpath(exe)).lower()
                        exact_path_match = exe_norm == normalized_target_path
                    except Exception:
                        exact_path_match = exe == normalized_target_path

                exact_name_match = name == target_name or exe_basename == target_name
                is_name_match = exact_name_match or exact_path_match
                is_dir_match = False
                if not is_name_match and exe and path:
                     try:
                         # Relaxed Check: Match if candidate EXE is anywhere inside target EXE's directory (Recursive)
                         # e.g. Target: /App/Launcher.exe
                         #      Child:  /App/Bin/Player.exe
                         target_dir = os.path.normpath(os.path.dirname(path)).lower()
                         candidate_dir = os.path.normpath(os.path.dirname(exe)).lower()
                         
                         # Match if candidate dir is the same OR a true subdirectory of target dir.
                         # This avoids false positives like "projectdashboard" matching "projectdashboard_backup".
                         target_dir_prefix = target_dir.rstrip("\\/") + os.sep
                         if candidate_dir == target_dir or candidate_dir.startswith(target_dir_prefix):
                             is_dir_match = True
                             print(f"[DEBUG] Directory Match! TargetDir: {target_dir}, Candidate: {exe}")
                     except: pass

                if is_name_match or is_dir_match:
                    # Read title early so strict-match logic can use it safely
                    title = safe_get_window_text(hwnd)
                    # Found a window for this process
                    
                    # [Browser Isolation Logic]
                    # If hunting for Chrome/Edge, ensure it's OUR instance (check for --app or --user-data-dir)
                    # This prevents grabbing the user's personal browser window!
                    if target_name in ["chrome.exe", "msedge.exe"]:
                        
                        # [Strict Match Priority]
                        # If we have a specific profile requirement, we MUST try to match it.
                        if required_cmdline_part:
                            try:
                                if not proc: raise Exception("No Proc")
                                cmdline = " ".join(proc.cmdline()).lower()
                                if required_cmdline_part.lower() in cmdline:
                                    # Perfect, strict match! Keep this candidate.
                                    pass # Do not return True, let it be added to candidates
                                else:
                                    # cmdline readable but MISMATCH.
                                    # Even if recently_launched, this is WRONG profile.
                                    # Skip it.
                                    # But wait, what if `proc.cmdline()` is empty?
                                    if not cmdline: raise Exception("Empty cmdline") # This will trigger the except block
                                    return True # Skip this candidate (mismatch)
                            except:
                                # Access Denied / Error reading cmdline
                                # If we can't read cmdline, we can't verify strict match.
                                # Fallback to recently_launched logic below.
                                pass

                        # [Strict Match with Fallback]
                        # If we have a required_cmdline_part (Profile), we MUST try to match it first.
                        # Even if recently_launched is True, we prefer a STRICT match over a loose match.
                        
                        is_strict_match = False
                        if required_cmdline_part:
                            # [Fix] For WebView2/Python, check Window Title too!
                            if title and required_cmdline_part.lower() in title.lower():
                                 is_strict_match = True
                                 # print(f"[DEBUG] Matched Title '{title}' for tag '{required_cmdline_part}'")

                            if not is_strict_match:
                                try:
                                    cmdline = " ".join(proc.cmdline()).lower()
                                    if required_cmdline_part.lower() in cmdline:
                                        is_strict_match = True
                                except:
                                    pass
                        
                        if recently_launched:
                             # If exact profile match required but not found, and we are lenient... 
                             # Wait! If we have multiple windows, we might grab the WRONG one if we are lenient.
                             # If strict match found, GREAT!
                             if is_strict_match:
                                 pass 
                             # If strict match NOT found, but we require it... 
                             # We should seemingly SKIP this window if we are confident others exist.
                             # But "recently_launched" usually means "we just requested it, so assume top window is ours".
                             # The issue is when an OLD window is still top.
                             # Strategy: If required_cmdline_part is set, REJECT windows that don't match it, 
                             # UNLESS we are desperate (which recently_launched implies, but we need to be careful).
                             
                             # [FIX] If required_cmdline_part is present, we enforce it strictly if possible.
                             elif required_cmdline_part and not is_strict_match:
                                 # This window does NOT match our profile.
                                 # If it has "user-data-dir" pointing to another profile, we MUST reject it.
                                 try:
                                     cmdline = " ".join(proc.cmdline()).lower()
                                     if "user-data-dir" in cmdline:
                                         # It belongs to another profile explicitely. Reject.
                                         # print(f"[DEBUG] Rejecting lenient match {hwnd} because it belongs to another profile")
                                         return True 
                                 except:
                                     pass
                        
                        # [Strict Match Logic]
                        if required_cmdline_part and not recently_launched:
                             if not is_strict_match: 
                                 # Strict mode on and no match -> Skip
                                 return True
                                 
                        if not recently_launched and not required_cmdline_part:
                            # Normal legacy logic (no specific profile required, just "kiosk")
                            try:
                                cmdline = " ".join(proc.cmdline()).lower()
                                our_profile_marker = "chrome_profile" if "chrome" in target_name else "edge_profile"
                                
                                if our_profile_marker in cmdline or "browser_data" in cmdline or "bd_s" in cmdline:
                                    pass
                                elif "--app=" in cmdline:
                                    pass
                                elif "user-data-dir" in cmdline:
                                    return True
                                elif "--app=" not in cmdline and "user-data-dir" not in cmdline:
                                    return True
                            except Exception as e:
                                print(f"[DEBUG] Could not read cmdline for {hwnd}: {e}")
                                pass

                    # Check Style
                    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                    # title already read above (safe), keep as-is
                    
                    # Get window size to filter out 0x0 background windows
                    try:
                        rect = win32gui.GetWindowRect(hwnd)
                        width = rect[2] - rect[0]
                        height = rect[3] - rect[1]
                        
                        # Skip 0x0 windows (Chrome background processes)
                        if width == 0 or height == 0:
                            # print(f"[DEBUG] Skipping 0x0 window: {hwnd}")
                            return True
                    except:
                        # If we can't get rect, skip this window
                        return True
                    
                    # Score it
                    score = 0
                    if win32gui.IsWindowVisible(hwnd): score += 50 # Prioritize Visible!
                    if (style & win32con.WS_CAPTION): score += 10
                    if (style & win32con.WS_THICKFRAME): score += 5
                    if title: score += 20 # Titles are important
                    if pid_match: score += 25 # Prefer tracked process family, but don't force for generic EXE.
                    if track_pid and pid == track_pid: score += 120
                    elif pid_match: score += 40
                    if exact_path_match: score += 140
                    if exact_name_match: score += 100
                    elif is_dir_match: score -= 25
                    
                    # Bonus for reasonable window size (not minimized, not tiny)
                    if width >= 100 and height >= 100:
                        score += 15

                    # Helper/bootstrap windows often expose an executable path as their title.
                    if title:
                        title_lower = title.lower()
                        if ":\\" in title_lower or title_lower.endswith(".exe"):
                            score -= 30
                    
                    # Penalize empty titles if we are looking for a browser
                    if not title and "msedge" in name: score -= 20
                    
                    # Prefer not owned windows (Main windows usually have no owner)
                    if win32gui.GetWindow(hwnd, win32con.GW_OWNER) == 0: score += 5
                    
                    # Debug candidate
                    # print(f"Candidate: {hwnd} ({title}) Score: {score}")

                    candidates.append((score, hwnd))
            except:
                pass
            return True

        try:
            win32gui.EnumWindows(callback, None)
        except Exception as e:
            print(f"EnumWindows Error: {e}")
        
        # Debug: Show all candidates found
        if target_name in ["chrome.exe", "msedge.exe"]:
            print(f"[DEBUG] Found {len(candidates)} candidate windows for {target_name}")
            for score, hwnd in sorted(candidates, reverse=True)[:5]:
                try:
                    title = safe_get_window_text(hwnd)
                    is_visible = win32gui.IsWindowVisible(hwnd)
                    rect = win32gui.GetWindowRect(hwnd)
                    size = f"{rect[2]-rect[0]}x{rect[3]-rect[1]}"
                    print(f"[DEBUG]   HWND {hwnd}: score={score}, visible={is_visible}, size={size}, title='{title[:40]}'")
                except Exception as e:
                    print(f"[DEBUG]   HWND {hwnd}: Error getting info: {e}")
        
        if candidates:
            # Sort by score desc
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_hwnd = candidates[0][1]
            print(f"[DEBUG] Selected best candidate: HWND {best_hwnd} (score={candidates[0][0]})")
            return best_hwnd
            
        return None

    def closeEvent(self, event):
        """
        ?嗉?蝒???閫貊嚗?
        撘瑕?????恣??憭蝔? (Child Processes)
        """
        self.log_debug("[Player] closing... Cleaning up managed processes.")
        self.cleanup_children()
        super().closeEvent(event)

    def cleanup_children(self):
        # Stop WebView Host if running
        try:
            # Do not gate cleanup on a config flag: an older config or a crashed
            # Player can otherwise leave the host and its Edge children orphaned.
            self.stop_webview_host()
        except Exception:
            pass

        # Ensure WebView2 helper processes are terminated (video/web)
        try:
            tag_pattern = self._webview_tag_pattern()
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (proc.info['name'] or "").lower()
                    cmdline = " ".join(proc.info['cmdline'] or [])
                    cmd_lower = cmdline.lower()
                    is_webview_helper = (
                        "webview_player.py" in cmd_lower
                        or "webviewplayer.exe" in cmd_lower
                        or "webviewplayer.exe" in name
                    )
                    if is_webview_helper and tag_pattern.search(cmdline):
                        print(f"[Cleanup] Killing webview helper (PID: {proc.info['pid']})")
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            self.log_debug(f"Cleanup webview helper error: {e}")

        # Cleanup VLC helper processes (external video)
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = " ".join(proc.info['cmdline'] or [])
                    if self._vlc_tag(0, 0).split("_S", 1)[0] + "_S" in cmdline:
                        print(f"[Cleanup] Killing VLC (PID: {proc.info['pid']})")
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            self.log_debug(f"Cleanup VLC error: {e}")

        for path in self.all_managed_paths:
            try:
                target_name = os.path.basename(path).lower()
                normalized_path = os.path.abspath(path).lower()
                
                # [Fix] Request cmdline info to distinguish personal vs managed browsers
                for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
                    try:
                        p_exe = proc.info['exe'] or ""
                        p_name = proc.info['name'] or ""
                        
                        # Match 1: Main EXE Path Match
                        # Match 2: Name Match (for system commands like mstsc.exe)
                        is_match = False
                        if p_exe and p_exe.lower() == normalized_path:
                            is_match = True
                        elif not os.path.isabs(path) and p_name.lower() == target_name:
                            is_match = True
                            
                        if is_match:
                            # [Fix] Additional check for Browsers to avoid killing user's personal instances
                            # We MUST verify this is OUR instance before killing it.
                            if target_name in ["chrome.exe", "msedge.exe"]:
                                try:
                                    cmdline_list = proc.info['cmdline'] or []
                                    cmdline = " ".join(cmdline_list).lower()
                                    
                                    # Markers that indicate THIS is our managed browser
                                    is_ours = False
                                    # Check for our profile directories
                                    if "chrome_profile" in cmdline or "edge_profile" in cmdline or "browser_data" in cmdline or "bd_s" in cmdline:
                                        is_ours = True
                                    # Fallback: Kiosk mode is likely us (or at least strictly isolated)
                                    elif "--app=" in cmdline:
                                        is_ours = True
                                    
                                    # If not confirmed ours, SKIP IT
                                    if not is_ours:
                                        # internal debug
                                        # msg = f"[Cleanup] Skipping personal/external browser: {p_name} ({proc.info['pid']})"
                                        # print(msg)
                                        continue
                                except Exception as e:
                                    # If we can't read cmdline, assume it's NOT ours (Safe Fail)
                                    continue

                            msg = f"[Cleanup] Killing {p_name} (PID: {proc.info['pid']})"
                            print(msg)
                            self.log_debug(msg)
                            proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except Exception as e:
                self.log_debug(f"Error checking cleanup for {path}: {e}")

    def log_debug(self, msg):
        try:
            try:
                if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > 5 * 1024 * 1024:
                    backup = self.log_path + ".1"
                    try:
                        if os.path.exists(backup):
                            os.remove(backup)
                    except Exception:
                        pass
                    try:
                        os.replace(self.log_path, backup)
                    except Exception:
                        pass
            except Exception:
                pass
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.ctime()}: {msg}\n")
        except: pass

    def launch_exe(self, cmd):
        """
        Launch an executable with arguments.
        cmd can be either:
        - A string command line (will be parsed)
        - A list of arguments
        """
        try:
            import shlex
            
            cmd_list = []
            # If cmd is a string, we need to parse it carefully
            if isinstance(cmd, str):
                # For Windows, use shlex with posix=False to handle quotes correctly
                cmd_list = shlex.split(cmd, posix=False)
                # Remove any remaining quotes from the executable path
                cmd_list = [arg.strip('"').strip("'") for arg in cmd_list]
                
                # [Fix] Handle empty command (e.g. invalid content)
                if not cmd_list:
                    print(f"[Launch Error] Empty command provided")
                    return None
            else:
                cmd_list = cmd
            
            # Extract the executable path for cwd
            exe_path = cmd_list[0]
            
            # [Fix] Check if file exists to avoid WinError 2 Loop
            if not os.path.exists(exe_path):
                # Maybe it's in PATH? (e.g. notepad.exe)
                import shutil
                if not shutil.which(exe_path):
                    print(f"[Launch Error] Executable not found: {exe_path}")
                    return None
            
            # [Fix] Check if it is a directory (Exploer Capture Fix)
            if os.path.isdir(exe_path):
                print(f"[Launch Error] Path is a directory, not an executable: {exe_path}")
                return None
                
            cwd = os.path.dirname(os.path.abspath(exe_path)) if os.path.exists(exe_path) else None
            
            print(f"[DEBUG] Launching with cmd_list: {cmd_list[:3]}... (cwd: {cwd})")
            
            # Use list format to avoid shell interpretation issues
            # CRITICAL: Clean Environment
            # Remove QT flags so child process (Chrome) can use GPU normally
            clean_env = os.environ.copy()
            
            # Keys to remove that might crash Chrome/Edge when inherited from PySide app
            keys_to_remove = [
                "QTWEBENGINE_CHROMIUM_FLAGS",
                "QT_API",
                "QT_QPA_PLATFORM_PLUGIN_PATH",
                "QT_PLUGIN_PATH"
            ]
            
            for k in keys_to_remove:
                if k in clean_env:
                    del clean_env[k]
            
            # Ensure proper profile path separators for Windows
            final_cmd_list = []
            for arg in cmd_list:
                if "--user-data-dir" in arg:
                    parts = arg.split("=", 1)
                    if len(parts) == 2:
                        prefix, path = parts
                        clean_path = os.path.normpath(path.strip('"').strip("'"))
                        final_cmd_list.append(f'{prefix}={clean_path}')
                    else:
                        final_cmd_list.append(arg)
                else:
                    final_cmd_list.append(arg)
            
            # print(f"[DEBUG] Final Cmd List: {final_cmd_list}") # Reduce noise
            
            # Using Shell=False for safety and proper PID tracking
            proc = subprocess.Popen(final_cmd_list, cwd=cwd, shell=False, env=clean_env)
            return proc
            
        except Exception as e:
            print(f"Launch Error: {e}")
            # [Fix] Disable traceback to prevent log flooding during loops
            # import traceback
            # traceback.print_exc()
            return None
    
    def strip_borders(self, hwnd):
        try:
            # [Fix] Rate Limit: Don't strip too often to avoid "fighting" with external apps
            if not hasattr(self, '_last_strip_time'): self._last_strip_time = {}
            
            import time
            now = time.time()
            last = self._last_strip_time.get(hwnd, 0)
            if now - last < 1.0: # Limit to once per second
                # print(f"[DEBUG] Skipping strip_borders for {hwnd} (Rate Limit)")
                return
            
            self._last_strip_time[hwnd] = now
            print(f"[DEBUG] strip_borders called for HWND {hwnd}")
            # 1. Strip Standard Borders & System Menu
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            style = style & ~win32con.WS_CAPTION
            style = style & ~win32con.WS_THICKFRAME
            style = style & ~win32con.WS_MINIMIZEBOX
            style = style & ~win32con.WS_MAXIMIZEBOX
            style = style & ~win32con.WS_SYSMENU   # Kill System Menu (Close/Min/Max buttons)
            style = style | win32con.WS_POPUP      # Force Popup (Borderless)
            
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
            
            # 2. Hide from Taskbar (Extended Style)
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            # Add ToolWindow (Hides from taskbar)
            ex_style = ex_style | win32con.WS_EX_TOOLWINDOW 
            # Remove AppWindow (Forces taskbar)
            ex_style = ex_style & ~win32con.WS_EX_APPWINDOW
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
            
            # 3. Force Visual Update (Crucial for removing Title Bar)
            # KICK RESIZE: Force re-layout by toggling size slightly
            # Some apps cache their frame; this forces them to redraw the client area.
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            
            # Kick: Resize to W-1
            win32gui.SetWindowPos(hwnd, 0, 0, 0, w-1, h, 
                win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)
                
            # Restore: Resize back to W
            win32gui.SetWindowPos(hwnd, 0, 0, 0, w, h, 
                win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)
            
            print(f"[DEBUG] strip_borders success for HWND {hwnd}")
            
        except Exception as e:
            print(f"Resize Error: {e}")

    def get_vlc_scale(self, qt_w, qt_h):
        try:
            # Prefer the player window's real pixel size vs Qt logical size.
            # This tracks DPI scaling correctly and avoids multi-monitor virtual-size distortion.
            hwnd = int(self.winId())
            rect = win32gui.GetWindowRect(hwnd)
            px_w = max(1, rect[2] - rect[0])
            px_h = max(1, rect[3] - rect[1])
            qt_win_w = max(1, int(self.width()))
            qt_win_h = max(1, int(self.height()))
            sx = px_w / float(qt_win_w)
            sy = px_h / float(qt_win_h)
            if 0.5 <= sx <= 3.0 and 0.5 <= sy <= 3.0:
                return sx, sy
        except Exception:
            pass

        try:
            # Fallback to Qt-reported device pixel ratio for the active screen.
            screen = self.get_active_screen()
            dpr = float(screen.devicePixelRatio()) if screen else 1.0
            if 0.5 <= dpr <= 3.0:
                return dpr, dpr
        except Exception:
            pass

        return 1.0, 1.0

    def find_vlc_path(self):
        # Prefer VLC bundled under Player folder (Player\VLC\vlc.exe)
        candidates = []
        try:
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            candidates.append(os.path.join(base_dir, "VLC", "vlc.exe"))
        except:
            pass

        # Fallbacks: shared root or Player subfolder under data_dir
        try:
            if hasattr(self, "data_dir"):
                candidates.append(os.path.join(self.data_dir, "Player", "VLC", "vlc.exe"))
                candidates.append(os.path.join(self.data_dir, "VLC", "vlc.exe"))
        except:
            pass

        # System install locations
        candidates.extend([
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"
        ])

        for c in candidates:
            if c and os.path.exists(c):
                return c

        # Finally, try PATH
        try:
            p = shutil.which("vlc")
            if p:
                return p
        except:
            pass

        return "vlc"

    def check_ipc(self):
        # 1. Check Command
        if os.path.exists(self.ipc_cmd_path):
            try:
                with open(self.ipc_cmd_path, "r", encoding="utf-8") as f:
                    cmd = f.read().strip()
                os.remove(self.ipc_cmd_path)
                
                if cmd:
                    print(f"DEBUG: IPC Received: '{cmd}'")

                if cmd == "NEXT":
                    self.next_scene()
                elif cmd.startswith("JUMP"):
                    try:
                        _, idx_str = cmd.split(" ")
                        target_idx = int(idx_str)
                        if self.playlist and 0 <= target_idx < len(self.playlist):
                            print(f"IPC Jump to {target_idx}")
                            self.load_scene(target_idx)
                    except ValueError:
                        print(f"Invalid Jump Command: {cmd}")
                elif cmd == "PREV":
                    # Go back 2 steps then next (which is idx-1)
                    # Or just idx - 1 and load
                    if self.playlist:
                        self.current_scene_idx = (self.current_scene_idx - 1 + len(self.playlist)) % len(self.playlist)
                        self.load_scene(self.current_scene_idx)
                elif cmd == "PAUSE":
                    self.paused = True
                    self.scene_timer.stop()
                elif cmd == "RESUME":
                    self.paused = False
                    if self.playlist:
                        scene = self.playlist[self.current_scene_idx]
                        self._start_scene_timer(scene, reason="ipc resume")
                elif cmd == "RELOAD":
                    print("IPC Reload config")
                    self.load_config()
                elif cmd == "EXIT":
                    QApplication.quit()
            except Exception as e:
                print(f"IPC Error: {e}")

        # 2. Write Status
        status = {
            "scene_idx": self.current_scene_idx,
            "paused": self.paused,
            "profile": getattr(self, "profile_id", "default"),
            "control_profile": getattr(self, "control_profile_id", CONTROL_PROFILE_ID),
            "is_control_profile": bool(getattr(self, "is_control_profile", False)),
            "timestamp": time.time(),
        }
        
        # Calculate remaining
        if self.scene_timer.isActive():
            remaining = self.scene_timer.remainingTime() # in ms
            status["remaining"] = remaining / 1000
        else:
            status["remaining"] = 0
            
        # Add total duration for progress calculation
        if self.playlist and self.current_scene_idx < len(self.playlist):
            status["total"] = self._scene_duration_seconds(self.playlist[self.current_scene_idx])

        try:
            active_tags = sorted(getattr(self, "_active_host_webview_tags", set()) or set())
            render_health = getattr(self, "_webview_render_health", {}) or {}
            status["webview"] = {
                "host_pid": getattr(self, "_webview_host_pid", None),
                "active_tags": active_tags,
                "render_health": {
                    tag: render_health.get(tag, {})
                    for tag in active_tags
                    if isinstance(render_health.get(tag), dict)
                },
            }
        except Exception:
            pass
            
        tmp_status_path = f"{self.ipc_status_path}.{os.getpid()}.tmp"
        try:
            with open(tmp_status_path, "w", encoding="utf-8") as f:
                json.dump(status, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_status_path, self.ipc_status_path)
        except Exception as e:
            self.log_debug(f"Status Write Error: {e}")
        finally:
            try:
                if os.path.exists(tmp_status_path):
                    os.remove(tmp_status_path)
            except Exception:
                pass


    def check_alarms(self):
        """Check and play scheduled alarms."""
        if not getattr(self, "is_control_profile", False):
            return
        if not getattr(self, "alarms_enabled", False):
            return

        try:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # Reset daily de-duplication state
            if self.last_alarm_date != today_str:
                self.last_alarm_date = today_str
                self.played_alarms = set()

            global_conf = self.config.get("global_settings", {})
            alarms = [
                ("lunch", "alarm_lunch_enabled", "alarm_lunch_time", "lunch.mp3"),
                ("wake",  "alarm_wake_enabled",  "alarm_wake_time",  "wakeup.mp3"),
                ("off",   "alarm_off_enabled",   "alarm_off_time",   "offwork.mp3"),
            ]

            for name, key_enable, key_time, mp3_file in alarms:
                if name in self.played_alarms:
                    continue

                raw_enabled = global_conf.get(key_enable, False)
                if isinstance(raw_enabled, str):
                    enabled = raw_enabled.strip().lower() in ("1", "true", "yes", "on")
                else:
                    enabled = bool(raw_enabled)
                if not enabled:
                    continue

                target_raw = str(global_conf.get(key_time, "") or "").strip()
                if not target_raw:
                    continue

                parsed_target = None
                for fmt in ("%H:%M", "%H:%M:%S"):
                    try:
                        parsed_target = datetime.datetime.strptime(target_raw, fmt).time()
                        break
                    except Exception:
                        pass
                if not parsed_target:
                    continue

                target_dt = now.replace(
                    hour=parsed_target.hour,
                    minute=parsed_target.minute,
                    second=0,
                    microsecond=0,
                )
                delta_sec = (now - target_dt).total_seconds()

                # Grace window avoids missing alarms during restart/timer jitter.
                if not (0 <= delta_sec <= 70):
                    continue

                print(f"[Alarm] Triggering {name} at {now.strftime('%H:%M:%S')}")
                self.played_alarms.add(name)

                played = False
                candidates = [
                    os.path.join(self.base_dir, "music", mp3_file),
                    os.path.join(self.data_dir, "music", mp3_file),
                    os.path.join(os.path.dirname(self.base_dir), "music", mp3_file),
                ]

                if self.alarm_player and self.alarm_audio:
                    for p in candidates:
                        if os.path.exists(p):
                            try:
                                self.alarm_player.stop()
                            except Exception:
                                pass
                            try:
                                self.alarm_player.setSource(QUrl.fromLocalFile(p))
                                self.alarm_player.play()
                                print(f"[Alarm] Playing: {p}")
                                played = True
                                break
                            except Exception as pe:
                                print(f"[Alarm] Play failed: {pe}")
                                break

                if not played and winsound:
                    try:
                        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
                        print(f"[Alarm] Fallback beep for {name}")
                        played = True
                    except Exception as be:
                        print(f"[Alarm] Fallback beep failed: {be}")

                if not played:
                    print(f"[Alarm] Triggered but no playable audio for {name}: {candidates}")

        except Exception as e:
            print(f"[Alarm] Error: {e}")

if __name__ == "__main__":
    runtime_check_output = _extract_cli_value("--runtime-check-output")
    if runtime_check_output:
        # Build-time smoke test uses the actual frozen Player, without playback,
        # screen changes, timers, or touching the user's profile/configuration.
        from PySide6.QtCore import qVersion
        check_app = QApplication(["ScreenSplitterRuntimeCheck", "-platform", "offscreen"])
        check_widget = QWidget()
        with open(runtime_check_output, "w", encoding="utf-8") as check_file:
            json.dump({"ok": True, "qt": qVersion(), "pid": os.getpid(),
                       "icu_modules": [m.path for m in psutil.Process().memory_maps()
                                       if "icu" in os.path.basename(m.path).lower()]}, check_file)
        check_widget.close()
        sys.exit(0)
    # Extensive Debugging for Startup
    try:
        # Determine strict path for emergency log
        if getattr(sys, 'frozen', False):
            d = os.path.dirname(sys.executable)
        else:
            d = os.path.dirname(os.path.abspath(__file__))
            
        # Try to use centralized DATA_DIR
        try:
            data_d = config_manager.get_data_dir(PLAYER_PROFILE_ID)
            os.makedirs(data_d, exist_ok=True)
        except:
            data_d = d
            
        dump_path = os.path.join(data_d, "startup_crash.txt")

        previous_pid = _extract_cli_value("--wait-for-exit")
        if previous_pid:
            wait_for_previous_player(previous_pid, _extract_cli_value("--wait-for-created"))
        
        # Redirect stdout/stderr to file if -w hides it
        # Single Instance Lock (?脫迫????)
        # 雿輻 Windows Mutex 蝣箔??芣?銝?祕靘?
        import win32event
        import win32api
        import win32con
        
        mutex_suffix = "".join(ch if ch.isalnum() else "_" for ch in PLAYER_PROFILE_ID)
        mutex_name = f"Global\\ScreenSplitter_Player_Mutex_{mutex_suffix}"
        mutex = win32event.CreateMutex(None, False, mutex_name)
        # ERROR_ALREADY_EXISTS = 183
        if win32api.GetLastError() == 183:
            print("Another instance is already running. Exiting.")
            sys.exit(0)

        # Only the mutex owner may truncate this profile's logs.
        sys.stdout = Unbuffered(open(os.path.join(data_d, "stdout.txt"), "w", encoding="utf-8"))
        sys.stderr = Unbuffered(open(os.path.join(data_d, "stderr.txt"), "w", encoding="utf-8"))
        print(f"Starting Player... Time: {time.ctime()}")
        print(f"Profile: {PLAYER_PROFILE_ID}")

        # Fix for QtWebEngine Crash 0xc0000005 & 0xe0000008
        # ULTIMATE SAFE MODE: Recent tests show GPU causes instability (0xe0000008).
        # We MUST disable GPU and OpenGL to Ensure stability, even if it uses more CPU.
        # Also keeping RendererCodeIntegrity disabled to prevent permission crashes.
        # [Fix] Do NOT overwrite flags here! Use the ones set at top of file.
        # os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu ..."

        # Fix DPI Policy Warning (Must be called BEFORE QApplication)
        try:
           from PySide6.QtGui import QGuiApplication
           if hasattr(QGuiApplication, 'setHighDpiScaleFactorRoundingPolicy'):
               QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except: pass

        # Fix for Autoplay and SSL Errors
        args = sys.argv + [
            "--autoplay-policy=no-user-gesture-required-playback",
            "--ignore-certificate-errors",
            "--ignore-ssl-errors"
        ]
        
        app = QApplication(args)
        
        # Qt6 DPI handling is automatic, manual attributes often cause warnings or issues.
        # attributes removed.

        window = PlayerWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        # Emergency Log
        with open(dump_path, "w", encoding="utf-8") as f:
            import traceback
            f.write(f"Crash Time: {time.ctime()}\n")
            f.write(f"Error: {str(e)}\n")
            f.write(traceback.format_exc())
        # Re-raise to let OS know
        raise e
