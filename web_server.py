import os
import sys
import json
import time
import hmac
import html
import subprocess
import threading
from flask import Flask, Response, redirect, render_template, jsonify, request, send_from_directory

import config_manager

try:
    import psutil
except Exception:
    psutil = None

# 判斷是否為打包後的執行檔 (PyInstaller)
if getattr(sys, 'frozen', False):
    # 執行檔模式：設定正確的 template 和 static 資料夾路徑
    BASE_DIR = os.path.dirname(sys.executable)
    template_folder = os.path.join(BASE_DIR, 'templates')
    static_folder = os.path.join(BASE_DIR, 'static')
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
else:
    # 腳本模式：使用當前資料夾
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__)

app.secret_key = "super_secret_key"

# 定義 IPC (跨進程通訊) 檔案路徑，用於跟 Player 溝通
# 從 config_manager 引用統一的存放目錄 (Portable or AppData)
from config_manager import DATA_DIR

CONTROL_PROFILE_ID = getattr(config_manager, "DEFAULT_PROFILE_ID", "default")
_STATUS_CACHE = {}
_STATUS_CACHE_LOCK = threading.Lock()


def _is_control_profile(profile_id):
    return config_manager.sanitize_profile_id(profile_id) == CONTROL_PROFILE_ID


def _all_profile_ids():
    ids = []
    try:
        for profile in config_manager.list_profiles():
            pid = config_manager.sanitize_profile_id(profile.get("id"))
            if pid and pid not in ids:
                ids.append(pid)
    except Exception:
        pass
    if "default" not in ids:
        ids.insert(0, "default")
    return ids


def _scope_all_requested():
    try:
        if str(request.args.get("scope", "")).lower() == "all":
            return True
        if request.method in ("POST", "PUT", "PATCH") and request.is_json:
            body = request.get_json(silent=True) or {}
            return str(body.get("scope", "")).lower() == "all"
    except Exception:
        pass
    return False


def should_autolaunch_player(profile_id=None):
    """
    Control whether WebServer startup auto-launches Player when it is offline.
    It is intentionally profile-scoped so a secondary screen can opt in without
    forcing every configured Player to start.
    """
    try:
        config = config_manager.load_config(profile_id)
        glob = config.get("global_settings", {}) if isinstance(config, dict) else {}
        return bool(glob.get("web_autorun_player", False))
    except Exception:
        return False
    
IPC_CMD_PATH = os.path.join(DATA_DIR, "ipc_cmd.txt")      # 傳送指令給 Player
IPC_STATUS_PATH = os.path.join(DATA_DIR, "ipc_status.json") # 讀取 Player 狀態


LOG_EXTENSIONS = (".log", ".txt", ".json")
LOG_EXCLUDE_WORDS = ("secret", "password", "passwd", "token", "credential", "cookie")


def _request_profile_id(default="default"):
    try:
        if request.method in ("POST", "PUT", "PATCH") and request.is_json:
            body = request.get_json(silent=True) or {}
            if body.get("profile"):
                return config_manager.sanitize_profile_id(body.get("profile"))
        if request.args.get("profile"):
            return config_manager.sanitize_profile_id(request.args.get("profile"))
    except Exception:
        pass
    return config_manager.sanitize_profile_id(default)


def _profile_paths(profile_id=None):
    profile_id = config_manager.sanitize_profile_id(profile_id or "default")
    data_dir = config_manager.get_data_dir(profile_id)
    return {
        "profile": profile_id,
        "data_dir": data_dir,
        "config_path": config_manager.get_config_path(profile_id),
        "ipc_cmd_path": os.path.join(data_dir, "ipc_cmd.txt"),
        "ipc_status_path": os.path.join(data_dir, "ipc_status.json"),
        "webview_status_path": os.path.join(data_dir, "webview_status.json"),
    }


def _audit_log(message):
    try:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        print(line)
        path = os.path.join(DATA_DIR, "command_audit.log")
        try:
            if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
                backup = path + ".1"
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(path, backup)
        except Exception:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _request_source_summary():
    try:
        remote = request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown"
        ua = (request.headers.get("User-Agent") or "").replace("\n", " ")[:120]
        ref = (request.headers.get("Referer") or "").replace("\n", " ")[:160]
        return f"remote={remote} ua={ua!r} ref={ref!r}"
    except Exception:
        return "remote=unknown"


# ---------------------------------------------------------------------------
# Optional API access control.
# Nothing changes until <DATA_DIR>\api_access.json exists or SCREENSPLITTER_API_TOKEN
# is set: without them every client is accepted exactly as before. Loopback
# clients are always accepted so WebAdmin on the player PC itself needs no setup.
# ---------------------------------------------------------------------------
API_ACCESS_FILE = os.path.join(DATA_DIR, "api_access.json")
API_TOKEN_ENV = "SCREENSPLITTER_API_TOKEN"
API_TOKEN_COOKIE = "screensplitter_api_token"
API_TOKEN_COOKIE_MAX_AGE = 30 * 24 * 3600
_LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
_DENY_LOG_INTERVAL_SEC = 60.0
_DENY_LOG_LAST = {}
_DENY_LOG_LOCK = threading.Lock()


def _load_api_access():
    """Return the current access policy: {"restricted", "token", "allowed_ips", "error"}.

    An unreadable api_access.json fails closed (loopback only) instead of silently
    reopening the API while someone is editing the file.
    """
    token = str(os.environ.get(API_TOKEN_ENV) or "").strip()
    allowed_ips = []
    error = ""
    restricted = bool(token)
    if os.path.exists(API_ACCESS_FILE):
        restricted = True
        try:
            with open(API_ACCESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("api_access.json must contain a JSON object")
            file_token = str(data.get("token") or "").strip()
            if file_token:
                token = file_token
            raw_ips = data.get("allowed_ips") or []
            if isinstance(raw_ips, str):
                raw_ips = [raw_ips]
            allowed_ips = [str(ip).strip() for ip in raw_ips if str(ip).strip()]
        except Exception as exc:
            error = str(exc)
            token = ""
            allowed_ips = []
    return {"restricted": restricted, "token": token, "allowed_ips": allowed_ips, "error": error}


def _presented_api_token():
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("X-Api-Token")
    if header:
        return header.strip()
    return (request.cookies.get(API_TOKEN_COOKIE) or "").strip()


def _token_matches(access, presented):
    token = str(access.get("token") or "")
    presented = str(presented or "")
    return bool(token) and bool(presented) and hmac.compare_digest(presented, token)


def _api_request_allowed(access=None):
    access = access or _load_api_access()
    if not access["restricted"]:
        return True
    # Only the socket peer address counts. X-Forwarded-For is shown in the audit
    # log for context but is never trusted for access decisions.
    remote = (request.remote_addr or "").strip()
    if remote in _LOOPBACK_ADDRS:
        return True
    if remote and remote in access["allowed_ips"]:
        return True
    return _token_matches(access, _presented_api_token())


def _log_denied_request(reason):
    remote = request.remote_addr or "unknown"
    now = time.time()
    with _DENY_LOG_LOCK:
        if now - _DENY_LOG_LAST.get(remote, 0.0) < _DENY_LOG_INTERVAL_SEC:
            return
        _DENY_LOG_LAST[remote] = now
    _audit_log(f"[AUTH] denied {request.method} {request.path} reason={reason} {_request_source_summary()}")


def _api_access_mode_summary():
    access = _load_api_access()
    if access["error"]:
        return f"RESTRICTED (loopback only: cannot read {API_ACCESS_FILE}: {access['error']})"
    if not access["restricted"]:
        return f"OPEN (create {API_ACCESS_FILE} or set {API_TOKEN_ENV} to restrict remote access)"
    parts = []
    if access["token"]:
        parts.append("token")
    if access["allowed_ips"]:
        parts.append("allowed_ips=" + ",".join(access["allowed_ips"]))
    return "RESTRICTED (" + (", ".join(parts) or "loopback only") + ")"


def _access_notice_response(error_text=""):
    error_html = f'<p class="err">{html.escape(error_text)}</p>' if error_text else ""
    page = (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>ScreenSplitter WebAdmin 存取限制</title>'
        "<style>body{font-family:'Segoe UI',sans-serif;background:#1a1a2e;color:#e0e0e0;margin:0;"
        "min-height:100vh;display:flex;align-items:center;justify-content:center}"
        "main{background:#16213e;border:1px solid #0f3460;border-radius:8px;padding:28px 32px;max-width:420px}"
        "h1{font-size:18px;color:#00d2ff;margin:0 0 12px}p{font-size:14px;line-height:1.6}"
        "input{width:100%;box-sizing:border-box;padding:8px;margin:8px 0;border:1px solid #0f3460;"
        "border-radius:4px;background:#0f3460;color:#fff}"
        "button{padding:8px 16px;border:0;border-radius:4px;background:#00d2ff;color:#1a1a2e;font-weight:bold;cursor:pointer}"
        ".err{color:#e94560}</style></head><body><main>"
        "<h1>WebAdmin 已啟用存取限制</h1>"
        "<p>這台 WebServer 只接受本機、api_access.json 列出的 IP，或持有權杖的用戶端。"
        "請輸入權杖，或請管理者把這台電腦的 IP 加入 allowed_ips。播放不受此設定影響。</p>"
        '<form method="post" action="/login"><input type="password" name="token" placeholder="API 權杖" '
        'autofocus autocomplete="current-password"><button type="submit">進入 WebAdmin</button></form>'
        f"{error_html}</main></body></html>"
    )
    return Response(page, status=401, mimetype="text/html")


@app.before_request
def _enforce_api_access():
    if not (request.path or "").startswith("/api/"):
        return None
    access = _load_api_access()
    if _api_request_allowed(access):
        return None
    _log_denied_request("config error" if access["error"] else "unauthorized")
    return jsonify({
        "error": "unauthorized",
        "hint": "Send the token as 'Authorization: Bearer <token>' or 'X-Api-Token', sign in once at / "
                "in the browser, or add this client's IP to allowed_ips in api_access.json.",
    }), 401


@app.route("/login", methods=["POST"])
def login():
    """Browser sign-in for restricted mode: stores the token in an HttpOnly cookie."""
    supplied = str(request.form.get("token") or "").strip()
    if _token_matches(_load_api_access(), supplied):
        response = redirect("/")
        response.set_cookie(API_TOKEN_COOKIE, supplied, max_age=API_TOKEN_COOKIE_MAX_AGE,
                            httponly=True, samesite="Lax")
        return response
    _log_denied_request("bad token")
    return _access_notice_response("權杖不正確，或此 WebServer 沒有設定權杖（只接受 allowed_ips）。")


def _read_status_file(profile_id=None):
    paths = _profile_paths(profile_id)
    profile_id = paths["profile"]
    last_error = ""
    status = None
    for attempt in range(3):
        try:
            if not os.path.exists(paths["ipc_status_path"]):
                break
            with open(paths["ipc_status_path"], "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                status = loaded
                with _STATUS_CACHE_LOCK:
                    _STATUS_CACHE[profile_id] = dict(loaded)
                break
        except Exception as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(0.02 * (attempt + 1))

    if status is None:
        with _STATUS_CACHE_LOCK:
            cached = _STATUS_CACHE.get(profile_id)
            status = dict(cached) if isinstance(cached, dict) else None

    if status is None:
        result = {"offline": True, "profile": profile_id}
        if last_error:
            result["read_error"] = last_error
        return result

    status.setdefault("profile", profile_id)
    try:
        age = max(0.0, time.time() - float(status.get("timestamp", 0)))
    except Exception:
        age = 999999.0
    status["status_age"] = round(age, 3)
    if age > 8.0:
        status["offline"] = True
        status["stale"] = True
    else:
        status.pop("offline", None)
        status.pop("stale", None)
    if last_error:
        status["read_warning"] = last_error
    return status


def _is_profile_online(profile_id=None, stale_after=5.0):
    status = _read_status_file(profile_id)
    if status.get("offline"):
        return False
    try:
        return (time.time() - float(status.get("timestamp", 0))) <= stale_after
    except Exception:
        return False


def _write_profile_cmd(profile_id, cmd, skip_offline=True, source=""):
    profile_id = config_manager.sanitize_profile_id(profile_id)
    cmd = str(cmd or "").strip().upper()
    if not cmd:
        return {"profile": profile_id, "status": "skipped", "reason": "empty command"}
    if skip_offline and cmd != "RELOAD" and not _is_profile_online(profile_id, stale_after=8.0):
        result = {"profile": profile_id, "status": "skipped", "reason": "offline"}
        _audit_log(f"[CMD] skipped cmd={cmd} profile={profile_id} reason=offline {source}")
        return result
    paths = _profile_paths(profile_id)
    os.makedirs(paths["data_dir"], exist_ok=True)
    tmp_path = f"{paths['ipc_cmd_path']}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(cmd)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, paths["ipc_cmd_path"])
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
    _audit_log(f"[CMD] wrote cmd={cmd} profile={profile_id} path={paths['ipc_cmd_path']} {source}")
    return {"profile": profile_id, "status": "ok", "cmd": cmd}


def _has_profile_arg(args, profile_id):
    for index, arg in enumerate(args):
        if arg == "--profile" and index + 1 < len(args):
            return args[index + 1] == profile_id
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1] == profile_id
    return False


def _profile_runtime_roots(profile_id):
    """Return Player/WebViewPlayer roots belonging to one profile."""
    if psutil is None:
        return []
    profile_id = config_manager.sanitize_profile_id(profile_id)
    paths = _profile_paths(profile_id)
    data_dir = os.path.normcase(os.path.abspath(paths["data_dir"]))
    roots = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in {"player.exe", "webviewplayer.exe"}:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or [])
            lower_cmd = os.path.normcase(cmdline)
            if name == "player.exe":
                belongs = _has_profile_arg(proc.info.get("cmdline") or [], profile_id)
            elif profile_id == CONTROL_PROFILE_ID:
                belongs = data_dir in lower_cmd and f"{os.sep}profiles{os.sep}" not in lower_cmd
            else:
                belongs = any(
                    os.path.normcase(arg).startswith(data_dir + os.sep)
                    for arg in (proc.info.get("cmdline") or [])
                )
            if belongs:
                roots.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue
    return roots


def _terminate_profile_runtime(profile_id, root_pids=None):
    if psutil is None:
        return {"terminated": 0, "killed": 0}
    roots = []
    if root_pids is None:
        roots = _profile_runtime_roots(profile_id)
    else:
        for pid in root_pids:
            try:
                roots.append(psutil.Process(int(pid)))
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                pass

    processes = []
    seen = set()
    for root in roots:
        try:
            for proc in root.children(recursive=True) + [root]:
                if proc.pid not in seen:
                    processes.append(proc)
                    seen.add(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for proc in reversed(processes):
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _gone, alive = psutil.wait_procs(processes, timeout=2.0) if processes else ([], [])
    killed = 0
    for proc in alive:
        try:
            proc.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"terminated": len(processes), "killed": killed}


def _remove_stale_profile_runtime_files(profile_id):
    paths = _profile_paths(profile_id)
    for key in ("ipc_status_path", "webview_status_path"):
        try:
            os.remove(paths[key])
        except FileNotFoundError:
            pass
        except Exception:
            pass
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE.pop(paths["profile"], None)


def _stop_profile(profile_id, source=""):
    profile_id = config_manager.sanitize_profile_id(profile_id)
    roots = _profile_runtime_roots(profile_id)
    root_pids = [proc.pid for proc in roots]
    online = _is_profile_online(profile_id, stale_after=8.0)
    if online or any((proc.info.get("name") or "").lower() == "player.exe" for proc in roots):
        result = _write_profile_cmd(profile_id, "EXIT", skip_offline=False, source=source)

        def cleanup_after_exit():
            time.sleep(3.0)
            cleanup = _terminate_profile_runtime(profile_id, root_pids=root_pids)
            _remove_stale_profile_runtime_files(profile_id)
            _audit_log(
                f"[CMD] EXIT cleanup profile={profile_id} terminated={cleanup['terminated']} "
                f"killed={cleanup['killed']} {source}"
            )

        threading.Thread(target=cleanup_after_exit, daemon=True).start()
        result["cleanup_scheduled"] = True
        return result

    cleanup = _terminate_profile_runtime(profile_id, root_pids=root_pids)
    _remove_stale_profile_runtime_files(profile_id)
    state = "stopped" if cleanup["terminated"] else "already_offline"
    _audit_log(
        f"[CMD] EXIT profile={profile_id} status={state} terminated={cleanup['terminated']} {source}"
    )
    return {"profile": profile_id, "status": state, **cleanup}


def _log_roots(profile_id=None):
    paths = _profile_paths(profile_id)
    roots = []
    for candidate in (
        paths["data_dir"],
        getattr(config_manager, "SHARED_ROOT", None),
        BASE_DIR,
        os.getcwd(),
    ):
        if candidate and os.path.isdir(candidate) and candidate not in roots:
            roots.append(candidate)
    return roots


def _is_log_candidate(filename):
    lower = filename.lower()
    if not lower.endswith(LOG_EXTENSIONS):
        return False
    return not any(word in lower for word in LOG_EXCLUDE_WORDS)


def _discover_log_files(profile_id=None):
    profile_id = config_manager.sanitize_profile_id(profile_id or "default")
    files = {}
    for root in _log_roots(profile_id):
        try:
            for name in os.listdir(root):
                if not _is_log_candidate(name):
                    continue
                path = os.path.abspath(os.path.join(root, name))
                if not os.path.isfile(path):
                    continue
                key = os.path.basename(name)
                if key not in files:
                    stat = os.stat(path)
                    files[key] = {
                        "name": key,
                        "path": path,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "url": f"/api/logs/{key}?profile={profile_id}",
                    }
        except Exception:
            pass
    return files


def _resolve_log_path(name, profile_id=None):
    safe_name = os.path.basename(str(name or "").replace("\\", "/"))
    if not safe_name or not _is_log_candidate(safe_name):
        return None
    return _discover_log_files(profile_id).get(safe_name, {}).get("path")


def _tail_text_file(path, lines=400, max_bytes=1024 * 1024):
    try:
        lines = max(1, min(int(lines), 5000))
    except Exception:
        lines = 400
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes), os.SEEK_SET)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception as e:
        return f"Unable to read log: {e}"


def _search_text_file(path, pattern, lines=200, max_bytes=4 * 1024 * 1024):
    pattern = str(pattern or "").lower()
    if not pattern:
        return []
    try:
        lines = max(1, min(int(lines), 2000))
    except Exception:
        lines = 200
    text = _tail_text_file(path, lines=20000, max_bytes=max_bytes)
    matches = [line for line in text.splitlines() if pattern in line.lower()]
    return matches[-lines:]


def _process_snapshot(profile_id=None):
    if psutil is None:
        return {"available": False, "items": []}
    profile_id = config_manager.sanitize_profile_id(profile_id or "default")
    tag_piece = profile_id.replace("-", "_").replace(".", "_")
    data_dir = _profile_paths(profile_id)["data_dir"].lower()
    is_default_profile = profile_id == "default"
    items = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "create_time"]):
        try:
            name = (proc.info.get("name") or "")
            lower_name = name.lower()
            cmd = " ".join(proc.info.get("cmdline") or [])
            lower_cmd = cmd.lower()
            profile_arg_match = _has_profile_arg(proc.info.get("cmdline") or [], profile_id)
            tag_match = f"webview2_{tag_piece.lower()}_" in lower_cmd
            if is_default_profile:
                data_dir_match = data_dir in lower_cmd and "\\profiles\\" not in lower_cmd
                profile_match = profile_arg_match or tag_match or data_dir_match
            else:
                profile_match = profile_arg_match or tag_match or data_dir in lower_cmd
            interesting_process = (
                "player.exe" in lower_name
                or "webviewplayer" in lower_name
                or "webview_player.py" in lower_cmd
                or "msedgewebview2" in lower_name
                or "webserver" in lower_name
            )
            if not (interesting_process and profile_match):
                continue
            mem = proc.info.get("memory_info")
            items.append({
                "pid": proc.info.get("pid"),
                "name": name,
                "rss_mb": round((getattr(mem, "rss", 0) or 0) / 1024 / 1024, 1),
                "create_time": proc.info.get("create_time"),
                "cmdline": cmd[:600],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue
    return {"available": True, "items": items}


@app.route("/")
def index():
    """首頁: 載入 Web Admin 介面"""
    if not _api_request_allowed():
        return _access_notice_response()
    return render_template("web_admin.html")

@app.route("/help", methods=["GET"])
def usage_help():
    """Serve the same offline manual shipped beside the application."""
    return send_from_directory(os.path.join(config_manager.SHARED_ROOT, "docs"), "usage.html")

@app.route("/api/config", methods=["GET"])
def get_config():
    """API: 取得目前的設定檔內容"""
    profile_id = _request_profile_id()
    config_manager.ensure_profile(profile_id)
    config = config_manager.load_config(profile_id)
    # 確保 playlist 欄位存在
    if "playlist" not in config:
        config["playlist"] = []
    config["_profile"] = profile_id
    return jsonify(config)

@app.route("/api/config", methods=["POST"])
def save_config():
    """API: 儲存設定檔內容"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be application/json"}), 400

        data = request.get_json(silent=False)
        if not isinstance(data, dict):
            return jsonify({"error": "Config payload must be a JSON object"}), 400

        profile_id = config_manager.sanitize_profile_id(data.pop("_profile", None) or data.pop("profile", None) or _request_profile_id())
        config_manager.ensure_profile(profile_id)
        # A WebAdmin tab opened before screen binding was saved can omit this
        # field. Preserve only the omitted binding; explicit values still win.
        incoming_globals = data.get("global_settings")
        if isinstance(incoming_globals, dict) and "screen_device" not in incoming_globals:
            current = config_manager.load_config(profile_id)
            current_globals = current.get("global_settings", {}) if isinstance(current, dict) else {}
            if isinstance(current_globals, dict) and "screen_device" in current_globals:
                incoming_globals["screen_device"] = current_globals["screen_device"]
        if not _is_control_profile(profile_id):
            glob = data.get("global_settings")
            if not isinstance(glob, dict):
                glob = {}
                data["global_settings"] = glob
            glob["alarm_lunch_enabled"] = False
            glob["alarm_wake_enabled"] = False
            glob["alarm_off_enabled"] = False
        saved_path = config_manager.save_config(data, profile_id)
        return jsonify({
            "status": "ok",
            "profile": profile_id,
            "path": saved_path,
            "config": {**config_manager.load_config(profile_id), "_profile": profile_id}
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/status", methods=["GET"])
def get_status():
    """
    API: 取得 Player 目前的播放狀態
    如果狀態檔不存在或超過容錯時間未更新，視為離線 (Offline)。
    """
    if _scope_all_requested():
        return jsonify({
            "status": "ok",
            "profiles": {
                pid: _read_status_file(pid)
                for pid in _all_profile_ids()
            }
        })
    return jsonify(_read_status_file(_request_profile_id()))


@app.route("/api/profiles", methods=["GET"])
def list_profiles_api():
    profiles = config_manager.list_profiles()
    return jsonify({
        "status": "ok",
        "active": _request_profile_id(),
        "profiles": profiles,
    })


@app.route("/api/profiles", methods=["POST"])
def create_profile_api():
    body = request.get_json(silent=True) or {}
    profile_id = config_manager.sanitize_profile_id(body.get("id") or body.get("profile") or f"screen-{int(time.time())}")
    source = config_manager.sanitize_profile_id(body.get("source") or "default")
    data_dir = config_manager.ensure_profile(profile_id, source_profile=source)
    return jsonify({
        "status": "ok",
        "profile": profile_id,
        "data_dir": data_dir,
        "profiles": config_manager.list_profiles(),
    })


@app.route("/api/logs", methods=["GET"])
def list_logs():
    """API: list local logs that are safe for remote diagnostics."""
    profile_id = _request_profile_id()
    files = list(_discover_log_files(profile_id).values())
    files.sort(key=lambda item: item.get("mtime", 0), reverse=True)
    return jsonify({
        "status": "ok",
        "profile": profile_id,
        "data_dir": _profile_paths(profile_id)["data_dir"],
        "logs": files,
    })


@app.route("/api/logs/<path:name>", methods=["GET"])
def get_log(name):
    """API: read the tail of a known local log file."""
    profile_id = _request_profile_id()
    path = _resolve_log_path(name, profile_id)
    if not path:
        return jsonify({"error": "log not found"}), 404
    try:
        lines = request.args.get("lines", request.args.get("tail", 400))
        text = _tail_text_file(path, lines=lines)
        stat = os.stat(path)
        return jsonify({
            "status": "ok",
            "profile": profile_id,
            "name": os.path.basename(path),
            "path": path,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "lines": int(lines) if str(lines).isdigit() else 400,
            "content": text,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/log-search", methods=["GET", "POST"])
def search_log():
    profile_id = _request_profile_id()
    body = request.get_json(silent=True) if request.is_json else {}
    filename = body.get("file") or request.args.get("file", "player_debug.log")
    query = body.get("q") or body.get("pattern") or request.args.get("q", request.args.get("pattern", ""))
    path = _resolve_log_path(filename, profile_id)
    if not path:
        return jsonify({"error": "log not found"}), 404
    matches = _search_text_file(path, query, lines=body.get("lines") or request.args.get("lines", 200))
    return jsonify({
        "status": "ok",
        "profile": profile_id,
        "name": os.path.basename(path),
        "query": query,
        "count": len(matches),
        "matches": matches,
    })


@app.route("/api/processes", methods=["GET"])
def processes_api():
    if _scope_all_requested():
        return jsonify({
            "status": "ok",
            "profiles": {
                pid: _process_snapshot(pid)
                for pid in _all_profile_ids()
            }
        })
    profile_id = _request_profile_id()
    return jsonify({
        "status": "ok",
        "profile": profile_id,
        "processes": _process_snapshot(profile_id),
    })


@app.route("/api/diagnostics", methods=["GET"])
def diagnostics():
    """API: compact status/config/log index for Codex or remote troubleshooting."""
    if _scope_all_requested():
        return jsonify({
            "status": "ok",
            "profiles": {
                pid: _diagnostics_for_profile(pid)
                for pid in _all_profile_ids()
            }
        })

    profile_id = _request_profile_id()
    payload = _diagnostics_for_profile(profile_id)
    payload["status"] = "ok"
    return jsonify(payload)


def _diagnostics_for_profile(profile_id):
    status = _read_status_file(profile_id)
    paths = _profile_paths(profile_id)
    webview_status = {}
    try:
        if os.path.exists(paths["webview_status_path"]):
            with open(paths["webview_status_path"], "r", encoding="utf-8") as f:
                loaded_status = json.load(f)
            if isinstance(loaded_status, dict):
                webview_status = loaded_status
    except Exception as e:
        webview_status = {"error": str(e)}

    cfg_summary = {}
    try:
        cfg = config_manager.load_config(profile_id)
        glob = cfg.get("global_settings", {}) if isinstance(cfg, dict) else {}
        playlist = cfg.get("playlist", []) if isinstance(cfg, dict) else []
        cfg_summary = {
            "screen_device": glob.get("screen_device"),
            "screen_x": glob.get("screen_x"),
            "screen_y": glob.get("screen_y"),
            "screen_width": glob.get("screen_width"),
            "screen_height": glob.get("screen_height"),
            "playlist_count": len(playlist),
        }
    except Exception as e:
        cfg_summary = {"error": str(e)}

    return {
        "profile": profile_id,
        "status": status,
        "webview": webview_status,
        "config": cfg_summary,
        "logs": list(_discover_log_files(profile_id).values()),
        "processes": _process_snapshot(profile_id),
    }


@app.route("/api/cmd", methods=["POST"])
def send_cmd():
    """
    API: 傳送指令給 Player (例如: NEXT, PAUSE, RESUME)
    寫入 ipc_cmd.txt，Player 會定期讀取並執行。
    """
    body = request.get_json(silent=True) or {}
    profile_id = _request_profile_id()
    cmd = body.get("cmd")
    if not cmd:
        return jsonify({"error": "No command"}), 400
    
    try:
        source = _request_source_summary()
        if _scope_all_requested():
            _audit_log(f"[CMD] request scope=all cmd={cmd} {source}")
            if str(cmd).strip().upper() == "EXIT":
                results = [_stop_profile(pid, source=source) for pid in _all_profile_ids()]
            else:
                results = [
                    _write_profile_cmd(pid, cmd, skip_offline=True, source=source)
                    for pid in _all_profile_ids()
                ]
            return jsonify({"status": "ok", "scope": "all", "results": results})

        _audit_log(f"[CMD] request profile={profile_id} cmd={cmd} {source}")
        if str(cmd).strip().upper() == "EXIT":
            result = _stop_profile(profile_id, source=source)
        else:
            result = _write_profile_cmd(profile_id, cmd, skip_offline=True, source=source)
        http_status = 200 if result.get("status") in {"ok", "stopped", "already_offline"} else 202
        return jsonify(result), http_status
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/screens", methods=['GET'])
def get_screens_api():
    """API: 列舉伺服器端連結的所有顯示器解析度"""
    screens = []
    try:
        import win32api
        import win32con

        monitors = win32api.EnumDisplayMonitors()
        for i, monitor in enumerate(monitors):
            # monitor is (hMonitor, hdcMonitor, pyRect)
            pyRect = monitor[2]
            logical_width = pyRect[2] - pyRect[0]
            logical_height = pyRect[3] - pyRect[1]
            info = win32api.GetMonitorInfo(monitor[0]) or {}
            device = str(info.get("Device") or "")
            mode = {}
            if device:
                try:
                    dm = win32api.EnumDisplaySettings(device, win32con.ENUM_CURRENT_SETTINGS)
                    mode = {"width": int(dm.PelsWidth), "height": int(dm.PelsHeight)}
                except Exception:
                    mode = {}
            width = int(mode.get("width") or logical_width)
            height = int(mode.get("height") or logical_height)
            dpr_x = round(width / logical_width, 4) if logical_width else 1
            dpr_y = round(height / logical_height, 4) if logical_height else 1
            screens.append({
                "id": i,
                "width": width,
                "height": height,
                "logical_width": logical_width,
                "logical_height": logical_height,
                "left": pyRect[0],
                "top": pyRect[1],
                "device": device,
                "device_name": device,
                "dpr_x": dpr_x,
                "dpr_y": dpr_y,
                "label": f"Screen {i+1} ({width}x{height})"
            })
    except Exception as e:
        print(f"Monitor enum error: {e}")
        # Fallback 1: GetSystemMetrics (Primary Screen only)
        try:
            import ctypes
            user32 = ctypes.windll.user32
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
            screens.append({
                "id": 0,
                "width": width,
                "height": height,
                "label": f"Primary ({width}x{height})"
            })
        except:
            pass
            
    return jsonify(screens)

import sys

def launch_player_process(profile_id=None):
    """內部函式: 執行 Player.exe"""
    profile_id = config_manager.sanitize_profile_id(profile_id or "default")
    config_manager.ensure_profile(profile_id)
    try:
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            dist_root = os.path.dirname(exe_dir)
            player_exe = os.path.join(dist_root, "Player", "Player.exe")
            if not os.path.exists(player_exe):
                player_exe = os.path.join(exe_dir, "Player.exe")
            subprocess.Popen([player_exe, "--profile", profile_id], cwd=dist_root)
        else:
            script = os.path.join(BASE_DIR, "player.py")
            subprocess.Popen([sys.executable, script, "--profile", profile_id], cwd=BASE_DIR)
            print(f"[Dev] Launching Player profile={profile_id}")
        return True, f"Launched {profile_id}"
    except Exception as e:
        return False, str(e)

@app.route("/api/run_player", methods=["POST"])
def run_player():
    """API: 啟動 Player 程式 (如果它沒在執行的話)"""
    if _scope_all_requested():
        results = []
        for pid in _all_profile_ids():
            if _is_profile_online(pid):
                results.append({"profile": pid, "status": "online"})
                continue
            success, msg = launch_player_process(pid)
            results.append({
                "profile": pid,
                "status": "launched" if success else "error",
                "message": msg,
            })
        return jsonify({"status": "ok", "scope": "all", "results": results})

    profile_id = _request_profile_id()
    if _is_profile_online(profile_id):
        return jsonify({"status": "online", "profile": profile_id})
    success, msg = launch_player_process(profile_id)
    if success:
        return jsonify({"status": "launched", "profile": profile_id})
    else:
        return jsonify({"error": msg}), 500

def check_and_autolaunch():
    """啟動時檢查 Player 是否活著，沒活著就叫醒它"""
    print("Checking Player status for all profiles...")
    launched = 0
    for profile_id in _all_profile_ids():
        if not should_autolaunch_player(profile_id):
            print(f"Player auto-launch disabled: {profile_id}")
            continue
        if _is_profile_online(profile_id):
            print(f"Player already online: {profile_id}")
            continue
        print(f"Player offline. Auto-launching: {profile_id}")
        ok, msg = launch_player_process(profile_id)
        if ok:
            launched += 1
        else:
            print(f"Player auto-launch failed: {profile_id}: {msg}")
    print(f"Player auto-launch complete. launched={launched}")

if __name__ == "__main__":
    # 啟動時嘗試自動開啟 Player (只在打包模式下生效，避免開發時煩人)
    if getattr(sys, 'frozen', False):
        # Redirect Output to Log File (因為沒有黑視窗，必須寫檔才看得到)
        # using DATA_DIR from config_manager (imported above)
        log_path = os.path.join(DATA_DIR, "web_server.log")
        sys.stdout = sys.stderr = open(log_path, "w", encoding="utf-8", buffering=1)

        print(f"API access: {_api_access_mode_summary()}")
        check_and_autolaunch()
        # Production mode: No debug (removes the reloader process)
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        # Development mode keeps the auto-reloader (live edits still apply) but
        # not the interactive Werkzeug debugger: that page lets any browser
        # reaching port 5000 run Python on this machine.
        print(f"API access: {_api_access_mode_summary()}")
        app.run(host="0.0.0.0", port=5000, debug=True, use_debugger=False)


