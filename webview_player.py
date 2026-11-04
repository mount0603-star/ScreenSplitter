import webview
import sys
import argparse
import time
import ctypes
import os
import json
import threading
import logging
import math


def _enable_per_monitor_dpi_awareness():
    # Prefer Per-Monitor V2 so incoming coordinates are not system-DPI virtualized.
    try:
        user32 = ctypes.windll.user32
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                print("[DPI] SetProcessDpiAwarenessContext: PER_MONITOR_AWARE_V2")
                return
    except Exception:
        pass

    try:
        shcore = ctypes.windll.shcore
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        shcore.SetProcessDpiAwareness(2)
        print("[DPI] SetProcessDpiAwareness: PER_MONITOR")
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
        print("[DPI] SetProcessDPIAware fallback enabled")
    except Exception as e:
        print(f"[Warning] Failed to set DPI awareness: {e}")


def _set_webview2_args(args):
    # [Fix] Ignore SSL Errors and set WebView2 arguments
    base_args = [
        "--ignore-certificate-errors",
        "--allow-insecure-localhost",
        "--allow-running-insecure-content",
        "--autoplay-policy=no-user-gesture-required"
    ]
    if args.safe_mode:
        base_args.extend([
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-gpu-rasterization",
            "--disable-zero-copy",
            "--renderer-process-limit=4"
        ])
    else:
        # Keep GPU acceleration, but avoid zero-copy surfaces. Zero-copy is fast,
        # yet some display drivers leave a live WebView2 window black after many
        # create/destroy cycles or a monitor power transition.
        base_args.extend([
            "--enable-gpu-rasterization",
            "--disable-zero-copy"
        ])

    extra = (args.browser_args or "").strip()
    if extra:
        base_args.append(extra)

    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(base_args)


def _configure_process_log(path):
    if not path:
        return
    try:
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 5 * 1024 * 1024:
            backup = path + ".1"
            try:
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(path, backup)
            except Exception:
                pass
        stream = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[logging.StreamHandler(stream)],
            force=True,
        )
        print(f"[WebView Host] Log: {path}")
    except Exception:
        pass


def _run_on_native_ui(win, callback):
    native = getattr(win, "native", None)
    if native is None:
        raise RuntimeError("native window is not initialized")
    if getattr(native, "InvokeRequired", False):
        from System import Action
        return native.Invoke(Action(callback))
    return callback()


def normalize_webview_scale(value):
    try:
        value = float(value)
        return max(0.25, min(5.0, value)) if math.isfinite(value) and value > 0 else 1.0
    except (TypeError, ValueError, OverflowError):
        return 1.0


def apply_webview_zoom(win, scale):
    """Update the configured default once; repeated layout commands retain user zoom."""
    win._requested_zoom = normalize_webview_scale(scale)
    if getattr(win, '_configured_zoom', None) == win._requested_zoom:
        return True
    if getattr(win, 'native', None) is None:
        return False  # The loaded event will apply it after WebView2 initializes.

    def apply_native():
        value = win._requested_zoom
        if getattr(win, '_configured_zoom', None) == value:
            return True
        browser = getattr(win.native, 'browser', None)
        control = getattr(browser, 'webview', None)
        if control is None or getattr(control, 'CoreWebView2', None) is None:
            return False
        control.ZoomFactor = value
        win._configured_zoom = value
        print(f'[WebView Zoom] {win.title}: configured={value}')
        return True

    try:
        _run_on_native_ui(win, apply_native)
        return getattr(win, '_configured_zoom', None) == win._requested_zoom
    except Exception as e:
        print(f'[WebView Zoom] Apply failed: {e}')
        return False


def reload_webview(win, url):
    """Same-URL Source assignment is a no-op in WebView2; use native Reload."""
    def reload_native():
        browser = getattr(win.native, "browser", None)
        control = getattr(browser, "webview", None)
        core = getattr(control, "CoreWebView2", None)
        if core is None:
            return False
        core.Reload()
        return True

    if getattr(win, "native", None) is not None and _run_on_native_ui(win, reload_native):
        return
    win.load_url(url)


def _url_needs_viewport_fix(url):
    try:
        return "video-carousel" in str(url or "").lower()
    except Exception:
        return False


def _force_viewport_refresh(win):
    script = r"""
        (() => {
            try {
                const root = document.documentElement;
                const body = document.body;
                if (root) {
                    root.style.width = '100%';
                    root.style.height = '100%';
                    root.style.minWidth = '100vw';
                    root.style.minHeight = '100vh';
                }
                if (body) {
                    body.style.width = '100%';
                    body.style.height = '100%';
                    body.style.minWidth = '100vw';
                    body.style.minHeight = '100vh';
                    body.style.margin = '0';
                }
                const app = document.getElementById('app');
                if (app) {
                    app.style.width = '100vw';
                    app.style.minWidth = '100vw';
                    app.style.height = '100vh';
                    app.style.minHeight = '100vh';
                }
                try { window.dispatchEvent(new Event('resize')); } catch (_) {}
                try {
                    if (window.visualViewport) {
                        window.visualViewport.dispatchEvent(new Event('resize'));
                    }
                } catch (_) {}
                try {
                    document.querySelectorAll('video').forEach((v) => {
                        v.muted = true;
                        v.defaultMuted = true;
                        v.autoplay = true;
                        v.playsInline = true;
                        v.setAttribute('muted', '');
                        v.setAttribute('autoplay', '');
                        v.setAttribute('playsinline', '');
                        v.setAttribute('webkit-playsinline', '');
                        const p = v.play();
                        if (p && p.catch) p.catch(() => {});
                    });
                } catch (_) {}
                return true;
            } catch (_) {
                return false;
            }
        })();
    """
    try:
        if hasattr(win, "evaluate_js"):
            return win.evaluate_js(script)
    except Exception:
        return None
    return None


def _schedule_viewport_refresh(win, delays=(0.25, 0.9, 2.0)):
    for delay in delays:
        try:
            threading.Timer(float(delay), lambda w=win: _force_viewport_refresh(w)).start()
        except Exception:
            pass


def run_webview():
    _enable_per_monitor_dpi_awareness()

    parser = argparse.ArgumentParser(description='WebView2 Player')
    parser.add_argument('--url', required=False, default=None, help='URL to open')
    parser.add_argument('--title', default='WebView2 Player', help='Window Title')
    parser.add_argument('--x', type=int, default=0, help='X Position')
    parser.add_argument('--y', type=int, default=0, help='Y Position')
    parser.add_argument('--width', type=int, default=800, help='Width')
    parser.add_argument('--height', type=int, default=600, help='Height')
    parser.add_argument('--scale', type=normalize_webview_scale, default=1.0, help='Page zoom (0.25 to 5.0)')
    parser.add_argument('--profile-path', default=None, help='Storage Path for Cookies/Data')
    parser.add_argument('--safe-mode', action='store_true', help='Disable GPU/zero-copy for stability')
    parser.add_argument('--browser-args', default='', help='Extra WebView2 browser arguments')
    parser.add_argument('--debug', action='store_true', help='Enable Debug Mode')
    parser.add_argument('--host', action='store_true', help='Run as multi-window WebView2 host')
    parser.add_argument('--cmd-path', default=None, help='IPC command file path (host mode)')
    parser.add_argument('--status-path', default=None, help='IPC status file path (host mode)')
    parser.add_argument('--log-path', default=None, help='Host diagnostic log file path')
    parser.add_argument('--viewport-fix', action='store_true', help='Force SPA viewport resize after show/load')

    # Backward compatibility:
    # If browser args are passed as:
    #   --browser-args --renderer-process-limit=4
    # argparse treats the second token as a new option. Rewrite it to:
    #   --browser-args=--renderer-process-limit=4
    argv = list(sys.argv[1:])
    if "--browser-args" in argv:
        idx = argv.index("--browser-args")
        if idx + 1 < len(argv):
            nxt = argv[idx + 1]
            known_opts = {
                "-h", "--help", "--url", "--title", "--x", "--y",
                "--width", "--height", "--profile-path", "--safe-mode",
                "--browser-args", "--debug", "--host", "--cmd-path",
                "--status-path", "--log-path", "--viewport-fix", "--scale"
            }
            if nxt.startswith("--") and nxt not in known_opts:
                argv[idx] = f"--browser-args={nxt}"
                del argv[idx + 1]

    args = parser.parse_args(argv)

    # In single-window mode, URL is required. In host mode, it is intentionally optional.
    if not args.host and not (args.url and str(args.url).strip()):
        parser.error("the following arguments are required: --url")

    if args.host:
        host_status_path = args.status_path or os.path.join(os.getcwd(), "webview_status.json")
        host_log_path = args.log_path or os.path.join(os.path.dirname(os.path.abspath(host_status_path)), "webview_host.log")
        _configure_process_log(host_log_path)
    elif args.log_path:
        _configure_process_log(args.log_path)

    _set_webview2_args(args)

    if args.host:
        run_webview_host(args)
        return

    print(f"[WebView Launch] Title: {args.title}")
    print(f"[WebView Launch] Target Pos: ({args.x}, {args.y}) {args.width}x{args.height}")
    print(f"[WebView Launch] URL: {args.url}")
    print(f"[WebView Launch] Profile: {args.profile_path}")

    # Create the window
    # frameless=True ensures no OS borders (solving the strip_border issue entirely)
    # easy_drag=False prevents user from dragging the window by clicking on it
    win = webview.create_window(
        args.title,
        args.url,
        x=args.x,
        y=args.y,
        width=args.width,
        height=args.height,
        frameless=True,
        easy_drag=False,
        on_top=False,     # Let player.py manage Z-order or keep it normal
        text_select=True,
        zoomable=True
    )
    win._requested_zoom = args.scale

    standalone_state = {"last_recovery": 0.0, "probe_failures": 0, "stopped": False}

    def _standalone_probe():
        if standalone_state.get("stopped"):
            return
        result = None
        try:
            result = win.evaluate_js(r"""
                (() => ({
                    readyState: String(document.readyState || ''),
                    href: String(location.href || ''),
                    htmlLength: document.documentElement && document.documentElement.outerHTML
                        ? document.documentElement.outerHTML.length : 0
                }))();
            """)
        except Exception as e:
            print(f"[WebView Standalone] DOM probe failed: {e}")

        healthy = False
        if isinstance(result, dict):
            ready = str(result.get("readyState", "")).lower()
            href = str(result.get("href", "")).lower()
            try:
                html_length = int(result.get("htmlLength", 0) or 0)
            except Exception:
                html_length = 0
            healthy = ready in ("interactive", "complete") and href not in ("", "about:blank") and html_length >= 64

        if healthy:
            standalone_state["probe_failures"] = 0
        else:
            standalone_state["probe_failures"] = int(standalone_state.get("probe_failures", 0)) + 1
            now = time.time()
            if standalone_state["probe_failures"] >= 2 and now - standalone_state.get("last_recovery", 0.0) > 10.0:
                standalone_state["last_recovery"] = now
                standalone_state["probe_failures"] = 0
                try:
                    print(f"[WebView Standalone] Reloading after failed DOM probes: {args.url}")
                    reload_webview(win, args.url)
                except Exception as e:
                    print(f"[WebView Standalone] Reload failed: {e}")

        timer = threading.Timer(5.0, _standalone_probe)
        timer.daemon = True
        timer.start()

    native_failure_handler = {"handler": None}

    def _attach_standalone_failure_handler():
        if native_failure_handler.get("handler") is not None:
            return
        try:
            native = getattr(win, "native", None)
            browser = getattr(native, "browser", None)
            control = getattr(browser, "webview", None)
            if control is None:
                timer = threading.Timer(0.8, _attach_standalone_failure_handler)
                timer.daemon = True
                timer.start()
                return

            def _failed(sender, event_args):
                try:
                    kind = getattr(event_args, "ProcessFailedKind", None)
                    if kind is None and hasattr(event_args, "get_ProcessFailedKind"):
                        kind = event_args.get_ProcessFailedKind()
                except Exception:
                    kind = "unknown"
                print(f"[WebView Standalone] WebView2 process failed: {kind}")
                standalone_state["last_recovery"] = time.time()
                timer = threading.Timer(1.2, lambda: _safe_standalone_reload(f"process_failed:{kind}"))
                timer.daemon = True
                timer.start()

            def _attach():
                core = getattr(control, "CoreWebView2", None)
                if core is None:
                    raise RuntimeError("CoreWebView2 is not ready")
                core.ProcessFailed += _failed

            _run_on_native_ui(win, _attach)
            native_failure_handler["handler"] = _failed
            print("[WebView Standalone] ProcessFailed watchdog attached")
        except Exception as e:
            print(f"[WebView Standalone] ProcessFailed hook failed: {e}")

    def _safe_standalone_reload(reason):
        try:
            print(f"[WebView Standalone] Reloading: {reason}")
            reload_webview(win, args.url)
        except Exception as e:
            print(f"[WebView Standalone] Reload failed: {e}")

    def _on_standalone_loaded(window):
        print(f"[WebView Standalone] Loaded: {args.url}")
        apply_webview_zoom(window, args.scale)
        standalone_state["probe_failures"] = 0
        if args.viewport_fix or _url_needs_viewport_fix(args.url):
            _schedule_viewport_refresh(window)

    def _on_standalone_closed(window):
        standalone_state["stopped"] = True

    try:
        win.events.loaded += _on_standalone_loaded
        win.events.closed += _on_standalone_closed
    except Exception as e:
        print(f"[WebView Standalone] Event hook failed: {e}")

    # Start the WebView loop
    # [Fix] Use storage_path for persistence
    def _after_start():
        if args.viewport_fix or _url_needs_viewport_fix(args.url):
            _schedule_viewport_refresh(win)
        _attach_standalone_failure_handler()
        timer = threading.Timer(2.0, _standalone_probe)
        timer.daemon = True
        timer.start()

    webview.start(_after_start, debug=args.debug, private_mode=False, storage_path=args.profile_path)


def run_webview_host(args):
    cmd_path = args.cmd_path or os.path.join(os.getcwd(), "webview_cmd.json")
    status_path = args.status_path or os.path.join(os.getcwd(), "webview_status.json")
    profile_path = args.profile_path or os.path.join(os.getcwd(), "webview_profile")

    if profile_path and not os.path.exists(profile_path):
        try:
            os.makedirs(profile_path, exist_ok=True)
        except Exception:
            pass

    print(f"[WebView Host] PID: {os.getpid()}")
    print(f"[WebView Host] Cmd: {cmd_path}")
    print(f"[WebView Host] Status: {status_path}")
    print(f"[WebView Host] Profile: {profile_path}")

    windows = {}
    meta = {}
    meta_lock = threading.RLock()
    native_handlers = {}
    exit_flag = {"stop": False, "restart_reason": ""}
    pending_cmds = {}

    def _write_status():
        try:
            with meta_lock:
                window_status = {}
                public_fields = (
                    "url", "x", "y", "w", "h", "shown", "state", "scale",
                    "created_at", "load_started_at", "loaded_at", "last_probe_at",
                    "last_probe", "last_error", "last_error_at", "last_event",
                    "last_event_at", "recovery_count", "last_recovery_at",
                    "process_failed_kind", "viewport_fix"
                )
                for tag, values in meta.items():
                    item = {}
                    for field in public_fields:
                        if field in values:
                            item[field] = values.get(field)
                    window_status[tag] = item
                data = {
                    "pid": os.getpid(),
                    "windows": list(windows.keys()),
                    "window_status": window_status,
                    "restart_reason": exit_flag.get("restart_reason", ""),
                    "ts": time.time()
                }
            last_error = None
            for attempt in range(3):
                tmp = f"{status_path}.tmp.{os.getpid()}.{attempt}"
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                    os.replace(tmp, status_path)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
                    time.sleep(0.03)
            if last_error is not None:
                raise last_error
        except Exception as e:
            print(f"[WebView Host] status write failed: {e}")

    def _update_meta(tag, **values):
        if not tag:
            return {}
        with meta_lock:
            item = meta.setdefault(tag, {})
            item.update(values)
            return item

    def _record_error(tag, action, error):
        message = f"{action}: {error}"
        _update_meta(
            tag,
            last_error=message,
            last_error_at=time.time(),
            last_event="error",
            last_event_at=time.time(),
        )
        print(f"[WebView Host] {tag} {message}")

    def _safe_call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            print(f"[WebView Host] call failed ({getattr(fn, '__name__', 'unknown')}): {e}")
            return None

    def _normalize_cmd(cmd):
        op = cmd.get("op") or cmd.get("cmd") or "upsert"
        op = op.lower()
        tag = cmd.get("tag") or cmd.get("title")
        url = cmd.get("url") or "about:blank"
        x = cmd.get("x")
        y = cmd.get("y")
        w = cmd.get("w", cmd.get("width"))
        h = cmd.get("h", cmd.get("height"))
        show = cmd.get("show", True)
        return op, tag, url, x, y, w, h, show

    def _safe_js(win, script):
        try:
            if hasattr(win, "evaluate_js"):
                return win.evaluate_js(script)
        except Exception:
            return None
        return None

    probe_script = r"""
        (() => {
            try {
                const root = document.documentElement;
                const body = document.body;
                const rect = body ? body.getBoundingClientRect() : null;
                const media = Array.from(document.querySelectorAll('video,audio')).map((m) => ({
                    tag: (m.tagName || '').toLowerCase(),
                    paused: !!m.paused,
                    ended: !!m.ended,
                    readyState: Number(m.readyState || 0),
                    currentTime: Number(m.currentTime || 0)
                })).slice(0, 8);
                // Read-only evidence for this dashboard; never use tile state to reload a page.
                let mapDiagnostics = null;
                if (location.origin === 'http://itom.lkeng.com.tw:8080' &&
                    location.pathname.replace(/\/+$/, '') === '/overview') {
                    try {
                        const container = document.querySelector('.leaflet-container');
                        const bounds = container ? container.getBoundingClientRect() : null;
                        const tiles = container ? Array.from(container.querySelectorAll('img.leaflet-tile')) : [];
                        mapDiagnostics = {
                            present: !!container,
                            width: bounds ? Math.round(bounds.width) : 0,
                            height: bounds ? Math.round(bounds.height) : 0,
                            images: tiles.length,
                            loaded: tiles.filter(t => t.complete && t.naturalWidth > 0).length,
                            failed: tiles.filter(t => t.complete && !t.naturalWidth).length,
                            placeholders: container ? container.querySelectorAll('div.leaflet-tile').length : 0
                        };
                        try {
                            const usage = JSON.parse(localStorage.getItem('wr.carto.tile-usage.v1') || '{}') || {};
                            const settings = JSON.parse(localStorage.getItem('wr.carto.settings.v1') || '{}') || {};
                            const now = new Date();
                            const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
                            const count = usage.month === month && Number.isFinite(usage.count)
                                ? Math.max(0, Math.floor(usage.count)) : 0;
                            const validTime = (value, fallback) => {
                                const text = String(value || '').trim();
                                return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(text) ? text : fallback;
                            };
                            const activeStart = validTime(settings.activeStart, '08:00');
                            const activeEnd = validTime(settings.activeEnd, '21:00');
                            const minutes = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
                            const start = minutes(activeStart), end = minutes(activeEnd);
                            const current = now.getHours() * 60 + now.getMinutes();
                            const enabled = settings.enabled !== false;
                            const scheduleEnabled = settings.scheduleEnabled !== false;
                            const inSchedule = !scheduleEnabled || start === end ||
                                (start < end ? current >= start && current < end : current >= start || current < end);
                            // Mirrors the current dashboard's local safeguard, not CARTO billing.
                            const cap = 100000;
                            const reason = !enabled ? 'disabled' : !inSchedule ? 'schedule' : count >= cap ? 'budget' : 'allowed';
                            mapDiagnostics.carto = {basis: 'dashboard_local_safeguard', month, count, cap, enabled, scheduleEnabled,
                                activeStart, activeEnd, currentAllowed: reason === 'allowed', reason};
                            if (usage.month === month && !Number.isFinite(usage.count)) {
                                // The page may retain an earlier in-memory count; do not guess zero.
                                mapDiagnostics.carto.count = null;
                                mapDiagnostics.carto.currentAllowed = null;
                                mapDiagnostics.carto.reason = 'usage_unavailable';
                            }
                        } catch (_) {
                            mapDiagnostics.carto = {currentAllowed: null, reason: 'storage_unavailable'};
                        }
                    } catch (_) {
                        mapDiagnostics = {error: 'map_diagnostics_unavailable'};
                    }
                }
                return {
                    readyState: String(document.readyState || ''),
                    href: String(location.href || ''),
                    title: String(document.title || '').slice(0, 160),
                    htmlLength: root && root.outerHTML ? root.outerHTML.length : 0,
                    textLength: body && body.innerText ? body.innerText.trim().length : 0,
                    width: rect ? Math.round(rect.width) : 0,
                    height: rect ? Math.round(rect.height) : 0,
                    visualCount: document.querySelectorAll('img,svg,canvas,video,iframe,object,embed').length,
                    hidden: !!document.hidden,
                    media: media,
                    ...(mapDiagnostics ? {mapDiagnostics} : {})
                };
            } catch (e) {
                return {error: String(e && (e.stack || e.message) || e)};
            }
        })();
    """

    def _probe_window(tag, reason="periodic"):
        with meta_lock:
            win = windows.get(tag)
            item = meta.get(tag, {})
            if not win or not item.get("shown", False) or item.get("probe_inflight", False):
                return
            if not host_probe_due(item):
                return
            item["probe_inflight"] = True

        result = None
        error = ""
        try:
            result = win.evaluate_js(probe_script)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:
                    pass
        except Exception as e:
            error = str(e)

        now = time.time()
        healthy = False
        if isinstance(result, dict) and not result.get("error"):
            ready_state = str(result.get("readyState", "")).lower()
            href = str(result.get("href", "")).lower()
            try:
                html_length = int(result.get("htmlLength", 0) or 0)
                width = int(result.get("width", 0) or 0)
                height = int(result.get("height", 0) or 0)
            except Exception:
                html_length = width = height = 0
            healthy = (
                ready_state in ("interactive", "complete")
                and href not in ("", "about:blank")
                and not href.startswith("chrome-error://")
                and html_length >= 64
                and width > 0
                and height > 0
            )
        elif not error:
            error = "DOM probe returned no data"

        with meta_lock:
            item = meta.setdefault(tag, {})
            item["probe_inflight"] = False
            item["last_probe_at"] = now
            item["last_probe"] = result if isinstance(result, dict) else {"result": str(result)[:240]}
            item["last_event"] = f"probe:{reason}"
            item["last_event_at"] = now
            if healthy:
                item["state"] = "healthy"
                item["probe_failures"] = 0
                item["last_error"] = ""
            else:
                failures = int(item.get("probe_failures", 0) or 0) + 1
                item["probe_failures"] = failures
                item["state"] = "unhealthy"
                item["last_error"] = error or str((result or {}).get("error", "DOM probe failed"))
                item["last_error_at"] = now
                if failures >= 2:
                    item["recovery_requested"] = True
                    item["recovery_reason"] = item["last_error"]

    def _start_probe(tag, reason="periodic", delay=0.0):
        def _run():
            if delay > 0:
                time.sleep(delay)
            _probe_window(tag, reason=reason)
        try:
            threading.Thread(target=_run, name=f"wv-probe-{tag}", daemon=True).start()
        except Exception as e:
            _record_error(tag, "start probe", e)

    def _attach_native_failure_handler(tag, win):
        if tag in native_handlers:
            return
        try:
            native = getattr(win, "native", None)
            browser = getattr(native, "browser", None)
            control = getattr(browser, "webview", None)
            if control is None:
                return

            def _on_process_failed(sender, event_args, _tag=tag):
                try:
                    kind = getattr(event_args, "ProcessFailedKind", None)
                    if kind is None and hasattr(event_args, "get_ProcessFailedKind"):
                        kind = event_args.get_ProcessFailedKind()
                    kind_text = str(kind or "unknown")
                except Exception:
                    kind_text = "unknown"
                now = time.time()
                _update_meta(
                    _tag,
                    state="process_failed",
                    process_failed_kind=kind_text,
                    last_error=f"WebView2 process failed: {kind_text}",
                    last_error_at=now,
                    last_event="process_failed",
                    last_event_at=now,
                    recovery_requested=True,
                    recovery_reason=f"process_failed:{kind_text}",
                )
                print(f"[WebView Host] {_tag} process failed: {kind_text}")

            def _attach():
                core = getattr(control, "CoreWebView2", None)
                if core is None:
                    raise RuntimeError("CoreWebView2 is not ready")
                core.ProcessFailed += _on_process_failed

            _run_on_native_ui(win, _attach)
            native_handlers[tag] = _on_process_failed
            _update_meta(tag, native_failure_hook=True)
        except Exception as e:
            _record_error(tag, "attach ProcessFailed", e)

    def _wire_window_events(tag, win):
        try:
            def _before_load(window, _tag=tag):
                now = time.time()
                _update_meta(
                    _tag,
                    state="loading",
                    load_started_at=now,
                    last_event="before_load",
                    last_event_at=now,
                    probe_failures=0,
                )

            def _loaded(window, _tag=tag):
                now = time.time()
                apply_webview_zoom(window, getattr(window, '_requested_zoom', 1.0))
                _update_meta(
                    _tag,
                    state="loaded",
                    loaded_at=now,
                    last_event="loaded",
                    last_event_at=now,
                    probe_failures=0,
                )
                _attach_native_failure_handler(_tag, window)
                _start_probe(_tag, reason="loaded", delay=0.35)

            def _shown(window, _tag=tag):
                _update_meta(_tag, shown=True, last_event="shown", last_event_at=time.time())

            def _closed(window, _tag=tag):
                _update_meta(_tag, shown=False, state="closed", last_event="closed", last_event_at=time.time())

            win.events.before_load += _before_load
            win.events.loaded += _loaded
            win.events.shown += _shown
            win.events.closed += _closed
        except Exception as e:
            _record_error(tag, "wire events", e)

    def _reload_window(tag, reason="watchdog"):
        with meta_lock:
            win = windows.get(tag)
            item = meta.get(tag, {})
            url = item.get("url")
            now = time.time()
            last_recovery = float(item.get("last_recovery_at", 0.0) or 0.0)
            if not win or not url or url == "about:blank" or now - last_recovery < 8.0:
                return False
            recovery_count = int(item.get("recovery_count", 0) or 0) + 1
            item.update({
                "state": "loading",
                "load_started_at": now,
                "last_recovery_at": now,
                "recovery_count": recovery_count,
                "recovery_requested": False,
                "recovery_reason": "",
                "last_event": f"reload:{reason}",
                "last_event_at": now,
                "probe_failures": 0,
            })
        try:
            reload_webview(win, url)
            print(f"[WebView Host] Reloading {tag}: {reason}")
            return True
        except Exception as e:
            _record_error(tag, "reload", e)
            return False

    def _pause_media(win):
        _safe_js(win, """
            (() => {
                try {
                    document.querySelectorAll('video,audio').forEach((m) => {
                        try {
                            m.dataset.ssWasPlaying = m.paused ? '0' : '1';
                            m.pause();
                        } catch (_) {}
                    });
                } catch (_) {}
            })();
        """)

    def _resume_media(win):
        _safe_js(win, """
            (() => {
                try {
                    document.querySelectorAll('video,audio').forEach((m) => {
                        try {
                            const tag = (m.tagName || '').toLowerCase();
                            const isVideo = tag === 'video';
                            if (isVideo) {
                                m.muted = true;
                                m.defaultMuted = true;
                                m.autoplay = true;
                                m.playsInline = true;
                                m.setAttribute('muted', '');
                                m.setAttribute('autoplay', '');
                                m.setAttribute('playsinline', '');
                                m.setAttribute('webkit-playsinline', '');
                            }
                            const shouldPlay = isVideo || m.dataset.ssWasPlaying === '1' || m.autoplay || m.muted;
                            if (shouldPlay) {
                                const p = m.play();
                                if (p && p.catch) p.catch(() => {});
                            }
                        } catch (_) {}
                    });
                } catch (_) {}
            })();
        """)

    def _resume_media_for_tag(tag, min_interval=4.0):
        win = windows.get(tag)
        m = meta.get(tag, {})
        if not (win and m.get("shown", False)):
            return
        now = time.time()
        try:
            last_resume = float(m.get("last_resume", 0.0) or 0.0)
        except Exception:
            last_resume = 0.0
        if min_interval > 0 and (now - last_resume) < min_interval:
            return
        _resume_media(win)
        m["last_resume"] = now
        meta[tag] = m

    def _maybe_refresh_viewport(tag, win, force=False):
        m = meta.get(tag) or {}
        if not m.get("viewport_fix", False):
            return
        now = time.time()
        try:
            last_refresh = float(m.get("last_viewport_refresh", 0.0) or 0.0)
        except Exception:
            last_refresh = 0.0
        if not force and (now - last_refresh) < 5.0:
            return
        m["last_viewport_refresh"] = now
        meta[tag] = m
        _schedule_viewport_refresh(win)

    def _ensure_window(tag, url, x, y, w, h, show=True, reload_on_show=False, viewport_fix=False, scale=1.0):
        if not tag:
            return
        scale = normalize_webview_scale(scale)
        print(f"[WebView Host] Ensure {tag}: show={bool(show)} url={url}")
        viewport_fix = bool(viewport_fix) or _url_needs_viewport_fix(url)
        win = windows.get(tag)
        if not win:
            tx = int(x) if x is not None else 0
            ty = int(y) if y is not None else 0
            tw = max(1, int(w)) if w is not None else 800
            th = max(1, int(h)) if h is not None else 600
            try:
                win = webview.create_window(
                    tag,
                    url,
                    x=tx,
                    y=ty,
                    width=tw,
                    height=th,
                    frameless=True,
                    easy_drag=False,
                    on_top=False,
                    text_select=True,
                    zoomable=True
                )
            except Exception as e:
                print(f"[WebView Host] create_window failed for {tag}: {e}")
                _record_error(tag, "create_window", e)
                return
            now = time.time()
            with meta_lock:
                windows[tag] = win
                meta[tag] = {
                    "url": url,
                    "x": tx,
                    "y": ty,
                    "w": tw,
                    "h": th,
                    "shown": bool(show),
                    "viewport_fix": bool(viewport_fix),
                    "scale": scale,
                    "state": "loading",
                    "created_at": now,
                    "load_started_at": now,
                    "last_event": "created",
                    "last_event_at": now,
                    "recovery_count": 0,
                    "probe_failures": 0,
                }
            win._requested_zoom = scale
            _wire_window_events(tag, win)
            _maybe_refresh_viewport(tag, win, force=True)
        else:
            m = meta.get(tag) or {}
            m['scale'] = scale
            prev_url = meta.get(tag, {}).get("url")
            was_hidden = not m.get("shown", False)
            if viewport_fix:
                m["viewport_fix"] = True
            if url and url != prev_url:
                try:
                    win.load_url(url)
                    m["url"] = url
                    m["last_resume"] = 0.0
                    m["state"] = "loading"
                    m["load_started_at"] = time.time()
                    _maybe_refresh_viewport(tag, win, force=True)
                except Exception as e:
                    _record_error(tag, "load_url", e)
            elif show and reload_on_show and was_hidden:
                reload_url = url or prev_url
                if reload_url:
                    try:
                        reload_webview(win, reload_url)
                        m["url"] = reload_url
                        m["last_resume"] = 0.0
                        m["state"] = "loading"
                        m["load_started_at"] = time.time()
                        _maybe_refresh_viewport(tag, win, force=True)
                    except Exception as e:
                        _record_error(tag, "reload_on_show", e)

            # Idempotent geometry updates: avoid move/resize thrash when command stream repeats.
            if x is not None and y is not None:
                tx = int(x)
                ty = int(y)
                if m.get("x") != tx or m.get("y") != ty:
                    _safe_call(win.move, tx, ty)
                    m["x"] = tx
                    m["y"] = ty
                    _maybe_refresh_viewport(tag, win, force=True)
            if w is not None and h is not None:
                tw = max(1, int(w))
                th = max(1, int(h))
                if m.get("w") != tw or m.get("h") != th:
                    _safe_call(win.resize, tw, th)
                    m["w"] = tw
                    m["h"] = th
                    _maybe_refresh_viewport(tag, win, force=True)
            if show and hasattr(win, "show") and not m.get("shown", False):
                try:
                    win.show()
                    m["shown"] = True
                    m["last_event"] = "show"
                    m["last_event_at"] = time.time()
                    _maybe_refresh_viewport(tag, win, force=True)
                except Exception as e:
                    _record_error(tag, "show", e)
            if show:
                meta[tag] = m
                _resume_media_for_tag(tag)
                m = meta.get(tag, m)
                _maybe_refresh_viewport(tag, win)
            meta[tag] = m
        apply_webview_zoom(win, scale)
        print(f"[WebView Host] Ensure complete {tag}")

    def _hide_window(tag, pause_media=True):
        win = windows.get(tag)
        if win and hasattr(win, "hide"):
            if pause_media:
                _pause_media(win)
            try:
                win.hide()
                _update_meta(tag, shown=False, last_event="hide", last_event_at=time.time())
            except Exception as e:
                _record_error(tag, "hide", e)

    def _close_window(tag):
        win = windows.get(tag)
        if not win:
            return
        try:
            win.destroy()
        except Exception as e:
            _record_error(tag, "destroy", e)
        with meta_lock:
            windows.pop(tag, None)
            meta.pop(tag, None)
            native_handlers.pop(tag, None)

    def _pending_key(cmd):
        op = (cmd.get("op") or cmd.get("cmd") or "upsert").lower()
        tag = cmd.get("tag") or cmd.get("title") or ""
        return f"{op}:{tag}"

    def _schedule_or_process(cmd):
        try:
            delay_ms = int(cmd.get("delay_ms", 0) or 0)
        except Exception:
            delay_ms = 0
        key = _pending_key(cmd)
        if delay_ms > 0:
            due = time.time() + (delay_ms / 1000.0)
            existing = pending_cmds.get(key)
            if existing:
                # Keep the earliest due time so repeated Player heartbeats do not
                # keep postponing window creation forever. Update payload though.
                due = min(existing[0], due)
            pending_cmds[key] = (due, cmd)
            return
        if key in pending_cmds and cmd.get("op", "upsert").lower() in ("upsert", "open", "show"):
            # A staggered upsert is already waiting; refresh payload but preserve due.
            due = pending_cmds[key][0]
            pending_cmds[key] = (due, cmd)
            return
        _process_cmd(cmd)

    def _process_due_cmds():
        now = time.time()
        ready = [key for key, (due, _) in list(pending_cmds.items()) if due <= now]
        for key in ready:
            _, cmd = pending_cmds.pop(key, (None, None))
            if cmd:
                _process_cmd(cmd)

    def _process_cmd(cmd):
        op, tag, url, x, y, w, h, show = _normalize_cmd(cmd)
        print(f"[WebView Host] Command: op={op} tag={tag}")
        if op in ("hide", "close"):
            # A delayed show from before a screen/scene change must not revive this tag.
            for pending_op in ("upsert", "open", "show"):
                pending_cmds.pop(f"{pending_op}:{tag}", None)
        if op in ("upsert", "open", "show"):
            _ensure_window(
                tag,
                url,
                x,
                y,
                w,
                h,
                show=bool(show),
                reload_on_show=bool(cmd.get("reload_on_show", False)),
                viewport_fix=bool(cmd.get("viewport_fix", False)),
                scale=cmd.get('scale', 1.0),
            )
            if cmd.get("resume_media", False):
                _resume_media_for_tag(tag)
        elif op == "hide":
            _hide_window(tag, pause_media=bool(cmd.get("pause_media", True)))
        elif op in ("reload", "refresh"):
            _reload_window(tag, reason=str(cmd.get("reason") or op))
        elif op == "probe":
            _start_probe(tag, reason="command")
        elif op == "close":
            _close_window(tag)
        elif op == "exit":
            exit_flag["stop"] = True
            for existing_tag in list(windows.keys()):
                _close_window(existing_tag)
            # pywebview's GUI loop may keep running after windows are destroyed
            # from the command-pump thread. This helper is a dedicated child
            # process, so exit explicitly after the command has been handled.
            threading.Timer(0.2, lambda: os._exit(0)).start()

    def _read_cmds():
        if not os.path.exists(cmd_path):
            return []
        try:
            with open(cmd_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if not raw:
                os.remove(cmd_path)
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                cmds = [data]
            else:
                cmds = list(data)
            os.remove(cmd_path)
            return cmds
        except Exception:
            try:
                os.remove(cmd_path)
            except Exception:
                pass
            return []

    def _host_loop():
        last_status_write = 0.0
        last_probe_sweep = 0.0
        while not exit_flag["stop"]:
            cmds = _read_cmds()
            if cmds:
                for c in cmds:
                    try:
                        _schedule_or_process(c)
                    except Exception as e:
                        print(f"[WebView Host] command failed: {e}")
            _process_due_cmds()
            now = time.time()
            recovery_tags = []
            with meta_lock:
                for tag, item in list(meta.items()):
                    if tag == "__WEBVIEW_HOST__":
                        continue
                    if item.get("recovery_requested", False):
                        recovery_tags.append((tag, str(item.get("recovery_reason") or "watchdog")))
            for tag, reason in recovery_tags:
                _reload_window(tag, reason=reason)

            if now - last_probe_sweep > 4.0:
                with meta_lock:
                    probe_tags = [
                        tag for tag, item in meta.items()
                        if tag != "__WEBVIEW_HOST__"
                        and item.get("shown", False)
                        and host_probe_due(item, now)
                    ]
                for tag in probe_tags:
                    _start_probe(tag, reason="periodic")
                last_probe_sweep = now

            if cmds or (now - last_status_write) > 1.0:
                _write_status()
                last_status_write = now
            time.sleep(0.2)

    # Create a tiny off-screen window to bootstrap the GUI loop
    host_tag = "__WEBVIEW_HOST__"
    if host_tag not in windows:
        host_win = webview.create_window(
            host_tag,
            "about:blank",
            x=-10000,
            y=-10000,
            width=1,
            height=1,
            frameless=True,
            easy_drag=False,
            on_top=False,
            text_select=False
        )
        windows[host_tag] = host_win
        meta[host_tag] = {"url": "about:blank"}

    # Start the WebView loop with a command pump thread
    webview.start(_host_loop, debug=args.debug, private_mode=False, storage_path=profile_path)

HOST_LOADING_PROBE_TIMEOUT_SEC = 8.0


def host_probe_due(item, now=None):
    """Allow a DOM probe when a reload stays in loading past its grace period."""
    if not isinstance(item, dict) or item.get("state") != "loading":
        return True
    try:
        started_at = float(item.get("load_started_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        started_at = 0.0
    current = time.time() if now is None else float(now)
    return started_at > 0 and current - started_at >= HOST_LOADING_PROBE_TIMEOUT_SEC


if __name__ == "__main__":
    run_webview()
