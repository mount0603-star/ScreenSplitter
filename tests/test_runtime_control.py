import json
import io
import os
import tempfile
import time
import unittest
import subprocess
import sys
import threading
import ast
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

import web_server
import config_manager
import player
from player import Unbuffered, append_scene_cache_buster, wait_for_previous_player
from webview_player import host_probe_due


class CacheBusterTests(unittest.TestCase):
    def test_adds_scene_token_and_preserves_query_and_fragment(self):
        result = append_scene_cache_buster(
            "http://itom.lkeng.com.tw:8080/site-status?view=all#summary",
            12345,
        )

        self.assertEqual(
            result,
            "http://itom.lkeng.com.tw:8080/site-status?view=all&_screensplitter=12345#summary",
        )

    def test_replaces_an_existing_scene_token(self):
        result = append_scene_cache_buster(
            "https://example.test/page?_screensplitter=old&mode=wall",
            "new",
        )

        self.assertEqual(
            result,
            "https://example.test/page?mode=wall&_screensplitter=new",
        )

    def test_leaves_non_http_content_unchanged(self):
        self.assertEqual(
            append_scene_cache_buster("file:///C:/display/index.html", 12345),
            "file:///C:/display/index.html",
        )


class WebViewHostProbeTests(unittest.TestCase):
    def test_same_url_reload_uses_native_reload_without_clearing_ready_events(self):
        import webview_player
        core = mock.Mock()
        win = SimpleNamespace(native=SimpleNamespace(InvokeRequired=False,
            browser=SimpleNamespace(webview=SimpleNamespace(CoreWebView2=core))), load_url=mock.Mock())
        webview_player.reload_webview(win, "https://example.test/")
        core.Reload.assert_called_once_with()
        win.load_url.assert_not_called()

    def test_reload_falls_back_when_native_webview_is_unavailable(self):
        import webview_player
        win = SimpleNamespace(native=None, load_url=mock.Mock())
        webview_player.reload_webview(win, "https://example.test/")
        win.load_url.assert_called_once_with("https://example.test/")

    def test_probe_is_available_before_the_main_event_loop_starts(self):
        import webview_player
        with open(webview_player.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        prefix = []
        for node in tree.body:
            if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
                break
            prefix.append(node)
        namespace = {"__name__": "startup_probe_test"}
        exec(compile(ast.Module(body=prefix, type_ignores=[]), webview_player.__file__, "exec"), namespace)
        self.assertTrue(namespace["host_probe_due"]({"state": "loading", "load_started_at": 90}, 100))

    def test_stale_loading_window_becomes_probeable(self):
        self.assertFalse(host_probe_due({"state": "loading", "load_started_at": 95}, now=100))
        self.assertTrue(host_probe_due({"state": "loading", "load_started_at": 91}, now=100))
        self.assertTrue(host_probe_due({"state": "healthy", "load_started_at": 99}, now=100))


class PlayerLogTests(unittest.TestCase):
    def test_repetitive_debug_output_is_suppressed(self):
        stream = io.StringIO()
        output = Unbuffered(stream)
        output.write("[DEBUG] repeated geometry check")
        output.write("\n")
        output.write("important event\n")
        self.assertEqual(stream.getvalue(), "important event\n")


class RuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        with web_server._STATUS_CACHE_LOCK:
            web_server._STATUS_CACHE.clear()

    def _paths(self, root, profile="default"):
        return {
            "profile": profile,
            "data_dir": root,
            "config_path": os.path.join(root, "config.json"),
            "ipc_cmd_path": os.path.join(root, "ipc_cmd.txt"),
            "ipc_status_path": os.path.join(root, "ipc_status.json"),
            "webview_status_path": os.path.join(root, "webview_status.json"),
        }

    def test_status_read_uses_last_good_value_during_partial_write(self):
        with tempfile.TemporaryDirectory() as root:
            paths = self._paths(root)
            with open(paths["ipc_status_path"], "w", encoding="utf-8") as handle:
                json.dump({"timestamp": time.time(), "paused": False, "scene_idx": 2}, handle)

            with mock.patch.object(web_server, "_profile_paths", return_value=paths):
                first = web_server._read_status_file("default")
                self.assertFalse(first.get("offline", False))
                self.assertEqual(first["scene_idx"], 2)

                with open(paths["ipc_status_path"], "w", encoding="utf-8") as handle:
                    handle.write('{"timestamp":')

                cached = web_server._read_status_file("default")
                self.assertFalse(cached.get("offline", False))
                self.assertEqual(cached["scene_idx"], 2)
                self.assertIn("read_warning", cached)

    def test_stale_status_is_offline(self):
        with tempfile.TemporaryDirectory() as root:
            paths = self._paths(root)
            with open(paths["ipc_status_path"], "w", encoding="utf-8") as handle:
                json.dump({"timestamp": time.time() - 30, "paused": False}, handle)

            with mock.patch.object(web_server, "_profile_paths", return_value=paths):
                status = web_server._read_status_file("default")
                self.assertTrue(status["offline"])
                self.assertTrue(status["stale"])


class RuntimeCommandTests(unittest.TestCase):
    def test_stop_profile_does_not_match_another_profile_prefix(self):
        processes = [
            SimpleNamespace(info={"name": "Player.exe", "cmdline": ["Player.exe", "--profile", "screen2"]}),
            SimpleNamespace(info={"name": "Player.exe", "cmdline": ["Player.exe", "--profile", "screen20"]}),
            SimpleNamespace(info={"name": "Player.exe", "cmdline": ["Player.exe", "--profile=screen2"]}),
        ]
        with mock.patch.object(web_server.psutil, "process_iter", return_value=processes):
            self.assertEqual(web_server._profile_runtime_roots("screen2"), [processes[0], processes[2]])

    def test_close_all_stops_every_profile_even_when_offline(self):
        results = {
            "default": {"profile": "default", "status": "stopped"},
            "screen2": {"profile": "screen2", "status": "already_offline"},
        }
        with mock.patch.object(web_server, "_all_profile_ids", return_value=["default", "screen2"]), mock.patch.object(
            web_server,
            "_stop_profile",
            side_effect=lambda profile_id, source="": results[profile_id],
        ) as stop_profile, mock.patch.object(web_server, "_audit_log"):
            client = web_server.app.test_client()
            response = client.post("/api/cmd?scope=all", json={"cmd": "EXIT", "scope": "all"})

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual([item["profile"] for item in body["results"]], ["default", "screen2"])
        self.assertEqual(stop_profile.call_count, 2)


class RestartTests(unittest.TestCase):
    def test_real_relaunch_in_directory_with_spaces_and_shell_characters(self):
        with tempfile.TemporaryDirectory(prefix="restart space & ") as root:
            helper = os.path.join(root, "helper.py")
            marker = os.path.join(root, "started.json")
            source_root = os.path.dirname(player.__file__)
            code = (
                "import sys, os, json\n"
                f"sys.path.insert(0, {source_root!r})\n"
                "import player\n"
                "from types import SimpleNamespace\n"
                "if '--wait-for-exit' in sys.argv:\n"
                "    player.wait_for_previous_player(player._extract_cli_value('--wait-for-exit'), player._extract_cli_value('--wait-for-created'))\n"
                f"    with open({(marker + '.tmp')!r}, 'w') as f: json.dump({{'args': sys.argv, 'pid': os.getpid(), 'created': player.psutil.Process().create_time()}}, f)\n"
                f"    os.replace({(marker + '.tmp')!r}, {marker!r})\n"
                "else:\n"
                "    player.__file__ = __file__\n"
                f"    player.config_manager.get_data_dir = lambda profile: {root!r}\n"
                "    window = SimpleNamespace(profile_id='screen2', log_debug=print)\n"
                "    assert player.PlayerWindow._schedule_self_relaunch(window)\n"
            )
            with open(helper, "w", encoding="utf-8") as handle:
                handle.write(code)
            result = subprocess.run([sys.executable, helper], capture_output=True, text=True, timeout=20,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            deadline = time.monotonic() + 15
            while not os.path.exists(marker) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertTrue(os.path.exists(marker))
            with open(marker) as handle:
                result = json.load(handle)
            wait_for_previous_player(result["pid"], result["created"], timeout=10)
            args = result["args"]
            self.assertEqual(args[:3], [helper, "--profile", "screen2"])

    def test_spawn_failure_keeps_player_running(self):
        window = SimpleNamespace(log_debug=mock.Mock(), _schedule_self_relaunch=lambda: False)
        with mock.patch.object(player, "QApplication") as app:
            player.PlayerWindow.scheduled_restart(window)
            app.instance.assert_not_called()

    def test_native_relaunch_preserves_path_profile_and_records_errors(self):
        with tempfile.TemporaryDirectory(prefix="restart space & ") as root:
            exe = os.path.join(root, "Player.exe")
            window = SimpleNamespace(profile_id="screen2", log_debug=mock.Mock())
            with mock.patch.object(player.sys, "frozen", True, create=True), \
                 mock.patch.object(player.sys, "executable", exe), \
                 mock.patch.object(config_manager, "get_data_dir", return_value=root), \
                 mock.patch.object(player.subprocess, "Popen") as spawn:
                self.assertTrue(player.PlayerWindow._schedule_self_relaunch(window))
                args, kwargs = spawn.call_args
                self.assertEqual(args[0][:3], [exe, "--profile", "screen2"])
                self.assertIn("--wait-for-exit", args[0])
                self.assertFalse(kwargs.get("shell", False))
                self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
                self.assertEqual(kwargs["stderr"].name, os.path.join(root, "restart.log"))

    def test_waits_for_actual_process_exit(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            created = player.psutil.Process(child.pid).create_time()
            wait_for_previous_player(child.pid, created)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()

    def test_timeout_and_reused_pid_do_not_start_over_live_player(self):
        process = mock.Mock()
        process.create_time.return_value = 123.0
        with mock.patch.object(player.psutil, "Process", return_value=process):
            wait_for_previous_player(100, 122.0)
            process.wait.assert_not_called()
            process.wait.side_effect = player.psutil.TimeoutExpired(60)
            with self.assertRaises(player.psutil.TimeoutExpired):
                wait_for_previous_player(100, 123.0)


class ConfigWriteTests(unittest.TestCase):
    def test_concurrent_saves_are_complete_and_use_distinct_temp_files(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "config.json")
            barrier = threading.Barrier(2)
            replace = os.replace
            temp_paths = []

            def synchronized_replace(source, destination):
                if source not in temp_paths:
                    temp_paths.append(source)
                    barrier.wait(timeout=5)
                replace(source, destination)

            with mock.patch.object(config_manager, "get_data_dir", return_value=root), \
                 mock.patch.object(config_manager, "get_config_path", return_value=target), \
                 mock.patch.object(config_manager.os, "replace", side_effect=synchronized_replace):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(config_manager.save_config, [{"value": 1}, {"value": 2}]))
            self.assertEqual(results, [target, target])
            self.assertEqual(len(set(temp_paths)), 2)
            with open(target, encoding="utf-8") as handle:
                self.assertIn(json.load(handle), [{"value": 1}, {"value": 2}])
            self.assertEqual(os.listdir(root), ["config.json"])


class BuildIsolationTests(unittest.TestCase):
    def test_build_path_excludes_unrelated_tool_dll_directories(self):
        import build_release
        with mock.patch.dict(os.environ, {"PATH": r"C:\unrelated\poppler;C:\unrelated\ffmpeg"}):
            build_release.configure_build_environment()
            self.assertNotIn("poppler", os.environ["PATH"])
            self.assertNotIn("ffmpeg", os.environ["PATH"])
            self.assertIn("System32", os.environ["PATH"])

    def test_packaging_rejects_a_shadowing_icu_library(self):
        import build_release
        from pathlib import Path
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as root:
            bad = Path(root) / "build/dist/Player/_internal/icuuc.dll"
            bad.parent.mkdir(parents=True)
            bad.write_bytes(b"incompatible ICU")
            try:
                os.chdir(root)
                with self.assertRaisesRegex(RuntimeError, "Unexpected bundled ICU"):
                    build_release.post_process()
            finally:
                os.chdir(previous)



class ScreenRecoveryTests(unittest.TestCase):
    @staticmethod
    def _screen(name, rect=(0, 0, 1920, 1080)):
        return SimpleNamespace(name=lambda: name, geometry=lambda: player.QRect(*rect), devicePixelRatio=lambda: 1.0)

    def _window(self, settings):
        # Use the actual control methods without constructing or launching a kiosk.
        class Harness:
            pass
        for name in ("_configured_screen_point", "_configured_screen_device", "_target_screen_is_configured",
                     "get_configured_screen", "_screen_contains_point", "_screen_signature",
                     "_suspend_for_missing_screen", "_show_fullscreen_on_target",
                     "_scene_duration_seconds", "_start_scene_timer", "load_scene",
                     "_tag_profile_part", "_webview_tag_pattern"):
            setattr(Harness, name, getattr(player.PlayerWindow, name))
        Harness._normalize_screen_device = staticmethod(player.PlayerWindow._normalize_screen_device)
        window = Harness()
        window.config = {"global_settings": dict(settings)}
        window.profile_id = "default"
        window.current_scene_idx = 0
        window.playlist = [{"name": "test", "duration": 60, "zones": []}]
        window.paused = False
        window.scene_timer = mock.Mock()
        window.enforce_timer = mock.Mock()
        window.trigger_btn = mock.Mock()
        window.trigger_btn.isVisible.return_value = True
        window.controls = mock.Mock()
        window.controls.isVisible.return_value = False
        window.hide = mock.Mock()
        window.showFullScreen = mock.Mock()
        window.log_debug = mock.Mock()
        window._hide_profile_windows_for_missing_screen = mock.Mock()
        window._restore_configured_screen_geometry = mock.Mock(return_value=True)
        window._schedule_screen_relayout = mock.Mock()
        window.enforce_loop = mock.Mock()
        window.cleanup_rogue_processes = mock.Mock()
        window.zone_widgets = {}
        window.web_view_cache = {}
        window.known_hwnds = set()
        window.owned_hwnds = set()
        window.all_managed_paths = set()
        return window

    def test_legacy_coordinates_do_not_lock_onto_the_monitor_present_while_tv_is_off(self):
        window = self._window({"screen_x": 0, "screen_y": 0})
        desktop, television = self._screen("VA3456-WQHD"), self._screen("BenQ TV")
        with mock.patch.object(player, "QApplication") as app:
            app.screens.return_value = [desktop]
            app.screenAt.return_value = desktop
            self.assertIs(window.get_configured_screen(), desktop)
            app.screens.return_value = [desktop, television]
            app.screenAt.return_value = television
            self.assertIs(window.get_configured_screen(), television)
        self.assertNotIn("screen_device", window.config["global_settings"])

    def test_explicit_absent_tv_never_falls_back_to_another_monitor(self):
        window = self._window({"screen_device": "BenQ TV", "screen_x": 0, "screen_y": 0})
        with mock.patch.object(player, "QApplication") as app:
            app.screens.return_value = [self._screen("VA3456-WQHD")]
            self.assertIsNone(window.get_configured_screen())
            app.screenAt.assert_not_called()

    def test_webadmin_win32_device_maps_to_qt_friendly_name(self):
        window = self._window({"screen_device": r"\\.\DISPLAY2"})
        television = self._screen("BenQ TV")
        with mock.patch.object(player, "QApplication") as app, \
             mock.patch.object(player.win32api, "EnumDisplayMonitors", return_value=[(42, None, (0, 0, 1920, 1080))]), \
             mock.patch.object(player.win32api, "GetMonitorInfo", return_value={"Device": r"\\.\DISPLAY2"}):
            app.screens.return_value = [television]
            app.screenAt.return_value = television
            self.assertIs(window.get_configured_screen(), television)
            app.screenAt.assert_called_once_with(player.QPoint(1, 1))

    def test_missing_screen_hides_then_returns_without_changing_pause_or_scene(self):
        window = self._window({"screen_device": "BenQ TV"})
        window.paused = True
        window.current_scene_idx = 2
        with mock.patch.object(player, "QApplication") as app:
            app.screens.return_value = []
            self.assertTrue(window._suspend_for_missing_screen())
            self.assertTrue(window._suspend_for_missing_screen())
            window.scene_timer.stop.assert_called()
            window.hide.assert_called()
            window.trigger_btn.hide.assert_called()
            window.controls.hide.assert_called()
            self.assertEqual(window.log_debug.call_count, 1)
            app.screens.return_value = [self._screen("BenQ TV")]
            self.assertTrue(window._suspend_for_missing_screen())
            window._restore_configured_screen_geometry.assert_called_once_with("screen returned", force=True)
            window._schedule_screen_relayout.assert_called_once_with(700, followup=True)
            window._show_fullscreen_on_target()
        self.assertTrue(window.paused)
        self.assertEqual(window.current_scene_idx, 2)
        window.trigger_btn.show.assert_called_once()
        window.controls.show.assert_not_called()
        self.assertEqual(window.log_debug.call_count, 2)

    def test_absent_startup_or_delayed_fullscreen_never_shows_on_other_monitor(self):
        window = self._window({"screen_device": "BenQ TV"})
        with mock.patch.object(player, "QApplication") as app:
            app.screens.return_value = []
            window._show_fullscreen_on_target()
            window.load_scene(0)
            self.assertFalse(window._start_scene_timer(window.playlist[0], reason="ipc resume"))
        window.showFullScreen.assert_not_called()
        window.enforce_loop.assert_not_called()
        window.scene_timer.start.assert_not_called()

    def test_returned_scene_keeps_pause_and_zero_duration_behavior(self):
        for paused, duration, starts in ((False, 60, True), (True, 60, False), (False, 0, False)):
            with self.subTest(paused=paused, duration=duration):
                window = self._window({"screen_device": "BenQ TV"})
                window.paused = paused
                window.playlist[0]["duration"] = duration
                with mock.patch.object(player, "QApplication") as app, mock.patch.object(player.QTimer, "singleShot"):
                    app.screens.return_value = [self._screen("BenQ TV")]
                    window.load_scene(0)
                self.assertEqual(window.scene_timer.start.called, starts)
                self.assertEqual(window.paused, paused)
                self.assertEqual(window.current_scene_idx, 0)

    def test_no_target_configuration_keeps_normal_single_screen_behavior(self):
        window = self._window({})
        with mock.patch.object(player, "QApplication") as app:
            app.screens.return_value = [self._screen("Only Monitor")]
            self.assertFalse(window._suspend_for_missing_screen())
            window._show_fullscreen_on_target()
        window.hide.assert_not_called()
        window.showFullScreen.assert_called_once()

    def test_wait_hides_only_tracked_native_windows_and_throttles_scans(self):
        window = self._window({"screen_device": "BenQ TV"})
        window._webview_host_last_tags = {"WebView2_default_S0_Z0"}
        window.known_hwnds = {101}
        window._exe_hwnds = {"owned.exe": 102, "reused-handle.exe": 999}
        window._generic_pids = {"owned.exe": 301}
        window._webview_pids = {"WebView2_default_S0_Z0": 302}
        window.find_main_window_for_exe = mock.Mock(return_value=998)
        window.send_webview_host_cmds = mock.Mock()
        window.hide_inactive_webviews = mock.Mock()
        window.hide_inactive_vlc = mock.Mock()
        with mock.patch.object(player.time, "monotonic", return_value=100.0), \
             mock.patch.object(player.win32gui, "IsWindow", return_value=True), \
             mock.patch.object(player.win32gui, "IsWindowVisible", return_value=True), \
             mock.patch.object(player.ctypes.windll.user32, "ShowWindowAsync") as hide, \
             mock.patch.object(player.psutil, "Process") as process, \
             mock.patch.object(player.win32process, "GetWindowThreadProcessId", side_effect=lambda hwnd: (1, {101: 302, 102: 301, 998: 998, 999: 999}[hwnd])), \
             mock.patch.object(player, "safe_get_window_text", return_value="another profile EXE"):
            process.return_value.children.return_value = []
            player.PlayerWindow._hide_profile_windows_for_missing_screen(window)
            player.PlayerWindow._hide_profile_windows_for_missing_screen(window)
        self.assertEqual({call.args[0].value for call in hide.call_args_list}, {101, 102})
        self.assertEqual(hide.call_count, 2)
        window.hide_inactive_webviews.assert_called_once_with(set())
        window.hide_inactive_vlc.assert_called_once_with(set())
        window.send_webview_host_cmds.assert_called_once_with([
            {"op": "hide", "tag": "WebView2_default_S0_Z0", "pause_media": True}
        ])

    def test_screen_wait_webview_hiding_is_scoped_to_its_profile(self):
        window = self._window({"screen_device": "BenQ TV"})
        titles = {101: "WebView2_default_S0_Z0", 202: "WebView2_screen2_S0_Z0"}
        def enumerate_windows(callback, context):
            for hwnd in titles:
                callback(hwnd, context)
        with mock.patch.object(player.win32gui, "EnumWindows", side_effect=enumerate_windows), \
             mock.patch.object(player.win32gui, "IsWindowVisible", return_value=True), \
             mock.patch.object(player, "safe_get_window_text", side_effect=titles.get), \
             mock.patch.object(player.win32gui, "ShowWindow") as show, \
             mock.patch.object(player.win32gui, "SetWindowPos"), \
             mock.patch.object(player.psutil, "process_iter", return_value=[]):
            player.PlayerWindow.hide_inactive_webviews(window, set())
        show.assert_called_once_with(101, player.win32con.SW_HIDE)


if __name__ == "__main__":
    unittest.main()
