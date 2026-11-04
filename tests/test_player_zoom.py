"""Exercise Player's real zoom/launch logic without opening windows or killing processes."""
import ast
import math
import os
from pathlib import Path
import re
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psutil


SOURCE = Path(__file__).resolve().parents[1] / "player.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
HELPERS = {
    "normalize_webview_scale", "_webview_command_value",
    "standalone_webview_process_matches", "_kill_webview_tag",
    "_restart_standalone_for_scale_change",
}
FUNCTIONS = [node for node in ast.walk(TREE) if isinstance(node, ast.FunctionDef) and node.name in HELPERS]
NAMESPACE = {"math": math, "os": os, "psutil": psutil, "print": Mock()}
exec(compile(ast.Module(body=FUNCTIONS, type_ignores=[]), str(SOURCE), "exec"), NAMESPACE)

# Run the actual WebView branch inside its enclosing zone loop. All OS effects are mocked.
WEB_TEST = ast.dump(ast.parse("z_type == 'web' and zone_use_edge", mode="eval").body)
WEB_BRANCH = next(node for node in ast.walk(TREE)
                  if isinstance(node, ast.If) and ast.dump(node.test) == WEB_TEST)
ZONE_LOOP = ast.parse("for i, zone in enumerate(zones): pass").body[0]
ZONE_LOOP.body = [WEB_BRANCH]
LAUNCH_CODE = compile(ast.fix_missing_locations(ast.Module(body=[ZONE_LOOP], type_ignores=[])), str(SOURCE), "exec")


def process(pid, tag, scale=1.0, name="WebViewPlayer.exe", extra=()):
    cmdline = [name, "--title", tag, "--scale", str(scale), *extra]
    proc = Mock()
    proc.info = {"pid": pid, "name": name, "cmdline": cmdline}
    proc.name.return_value = name
    proc.cmdline.return_value = cmdline
    proc.is_running.return_value = True
    return proc


class PlayerZoomTests(unittest.TestCase):
    def setUp(self):
        self.tag = "WebView2_default_S0_Z0"
        self.player = SimpleNamespace(
            current_scene_idx=0, data_dir=r"C:\zoom-test",
            _webview_host_pid=900, _webview_host_exe="WebViewPlayer.exe",
            _last_webview_launch_time=0, _debug_zones_printed=set(),
            _rect_to_physical_desktop=lambda x, y, w, h, *_: (x, y, w, h),
            _webview_tag=lambda scene, zone: f"WebView2_default_S{scene}_Z{zone}",
            _webview_tag_pattern=lambda: re.compile(r"WebView2_default_S(\d+)_Z(\d+)"),
            _zone_uses_isolated_webview=lambda zone, url: bool(zone.get("isolate_webview")),
            _webview_reload_on_show=lambda url: False,
            _webview_needs_viewport_fix=lambda url: False,
            get_external_web_profile_path=lambda tag: rf"C:\zoom-test\External\{tag}",
            launch_exe=Mock(return_value=SimpleNamespace(pid=42)), log_debug=Mock(),
        )
        for name in ("_kill_webview_tag", "_restart_standalone_for_scale_change"):
            setattr(self.player, name, MethodType(NAMESPACE[name], self.player))

    def launch_tick(self, scale=2.5, host=False, kind="web", isolated=False, processes=()):
        namespace = dict(NAMESPACE)
        namespace.update(
            self=self.player, __file__=str(SOURCE),
            sys=SimpleNamespace(frozen=False, executable=r"C:\Python\python.exe"),
            time=SimpleNamespace(time=lambda: 100.0),
            zones=[{"scale": scale, "isolate_webview": isolated}],
            z_type="web", zone_use_edge=True, webview_kind=kind,
            content="https://example.invalid/zoom-test", screen_geo=None,
            vlc_scale_x=1, vlc_scale_y=1,
            active_webview_tags=set(), active_host_webview_tags=set(),
            active_isolated_webview_tags=set(), webview_host_cmds=[],
            webview_host_mode=host, webview_host_ready=True,
            webview_host_allow_fallback=False, webview_host_stagger_ms=0,
            webview_safe_mode=False, webview_browser_args="",
            webview_tag_cooldown=0, webview_launch_cooldown=0,
        )
        with patch.object(psutil, "process_iter", return_value=list(processes)):
            exec(LAUNCH_CODE, namespace)
        return namespace["webview_host_cmds"]

    def test_normalization_handles_invalid_and_clamps_positive_values(self):
        normalize = NAMESPACE["normalize_webview_scale"]
        cases = [(None, 1), ("", 1), ("oops", 1), (0, 1), (-2, 1),
                 (math.nan, 1), (math.inf, 1), (-math.inf, 1), ({}, 1),
                 ("2.5", 2.5), (0.1, 0.25), (10, 5), (1, 1)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(normalize(value), expected)

    def test_shared_host_receives_scale_without_restarting(self):
        self.player._restart_standalone_for_scale_change = Mock()
        self.assertEqual(self.launch_tick(2.5, host=True)[0]["scale"], 2.5)
        self.assertEqual(self.launch_tick(1.5, host=True)[0]["scale"], 1.5)
        self.player._restart_standalone_for_scale_change.assert_not_called()
        self.player.launch_exe.assert_not_called()

    def test_html5_video_keeps_normal_scale_in_both_modes(self):
        self.assertEqual(self.launch_tick(2.5, host=True, kind="video")[0]["scale"], 1.0)
        self.launch_tick(2.5, kind="video")
        self.assertIn(" --scale 1 ", self.player.launch_exe.call_args.args[0])

    def test_standalone_command_has_scale_and_repeated_tick_does_not_restart(self):
        self.launch_tick(2.5, host=True, isolated=True)
        self.launch_tick(2.5, host=True, isolated=True)
        self.player.launch_exe.assert_called_once()
        self.assertIn(" --scale 2.5 ", self.player.launch_exe.call_args.args[0])
        self.assertEqual(self.player._webview_tag_scales[self.tag], 2.5)

    def test_scale_change_rebuilds_only_matching_tag_once(self):
        self.launch_tick(2.5)
        target = process(42, self.tag, 2.5)
        others = [process(43, "WebView2_default_S0_Z00", 2.5),
                  process(44, "WebView2_screen2_S0_Z0", 2.5),
                  process(900, self.tag, 2.5, extra=("--host",))]
        with patch.object(psutil, "Process", return_value=target):
            self.launch_tick(1.5, processes=[target, *others])
        target.kill.assert_called_once()
        for other in others:
            other.kill.assert_not_called()
        self.assertEqual(self.player.launch_exe.call_count, 1)
        self.launch_tick(1.5)
        self.launch_tick(1.5)
        self.assertEqual(self.player.launch_exe.call_count, 2)
        self.assertEqual(self.player._webview_tag_scales[self.tag], 1.5)
        self.assertIn(" --scale 1.5 ", self.player.launch_exe.call_args.args[0])

    def test_existing_process_scale_is_read_before_adoption(self):
        target = process(42, self.tag, 1.0)
        self.launch_tick(2.5, processes=[target])
        target.kill.assert_called_once()
        self.player.launch_exe.assert_not_called()
        self.launch_tick(2.5)
        self.player.launch_exe.assert_called_once()

    def test_matching_existing_process_is_adopted_without_reset(self):
        target = process(42, self.tag, 2.5)
        self.launch_tick(2.5, processes=[target])
        self.launch_tick(2.5, processes=[target])
        self.player.launch_exe.assert_not_called()
        target.kill.assert_not_called()
        self.assertEqual(self.player._webview_tag_scales[self.tag], 2.5)

    def test_failed_launch_is_not_recorded_as_applied(self):
        self.player.launch_exe.return_value = None
        self.launch_tick(2.5)
        self.assertNotIn(self.tag, self.player._webview_tag_scales)
        self.assertNotIn(0, self.player._scene_nav_tracker[1])
        self.player.launch_exe.return_value = SimpleNamespace(pid=42)
        self.launch_tick(2.5)
        self.assertEqual(self.player._webview_tag_scales[self.tag], 2.5)

    def test_kill_revalidates_cached_pid_and_matches_title_exactly(self):
        tag = "WebView2_default_S0_Z1"
        target = process(10, tag)
        wrong_zone = process(11, "WebView2_default_S0_Z10")
        other_profile = process(12, "WebView2_screen2_S0_Z1")
        host = process(13, tag, extra=("--host",))
        reused = process(14, tag, name="unrelated.exe")
        self.player._webview_pids = {tag: 14, "WebView2_default_S0_Z10": 11}
        self.player._webview_tag_scales = {tag: 2.5, "WebView2_default_S0_Z10": 1.5}
        self.player._scene_nav_tracker = (0, {1, 10})
        with patch.object(psutil, "Process", return_value=reused), patch.object(
            psutil, "process_iter", return_value=[target, wrong_zone, other_profile, host, reused]
        ):
            self.player._kill_webview_tag(tag)
        target.kill.assert_called_once()
        for other in (wrong_zone, other_profile, host, reused):
            other.kill.assert_not_called()
        self.assertEqual(self.player._scene_nav_tracker, (0, {10}))
        self.assertEqual(self.player._webview_tag_scales, {"WebView2_default_S0_Z10": 1.5})

    def test_process_match_accepts_real_helper_and_rejects_title_substrings(self):
        matches = NAMESPACE["standalone_webview_process_matches"]
        self.assertTrue(matches("WebViewPlayer.exe", ["WebViewPlayer.exe", f"--title={self.tag}"], self.tag))
        self.assertTrue(matches("python.exe", [r"C:\Python\python.exe", r"D:\ScreenSplitter\webview_player.py", "--title", self.tag], self.tag))
        self.assertFalse(matches("python.exe", ["python.exe", "unrelated.py", "--title", self.tag], self.tag))
        self.assertFalse(matches("WebViewPlayer.exe", ["WebViewPlayer.exe", "--title", self.tag + "0"], self.tag))
        self.assertFalse(matches("WebViewPlayer.exe", ["WebViewPlayer.exe", "--url", self.tag, "--title", "different"], self.tag))
        self.assertFalse(matches("WebViewPlayer.exe", ["WebViewPlayer.exe", "--title", self.tag, "--title=different"], self.tag))


if __name__ == "__main__":
    unittest.main()
