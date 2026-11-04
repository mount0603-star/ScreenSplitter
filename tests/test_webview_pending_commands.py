"""Exercise the host's real queue without opening production WebView windows."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class WebViewPendingCommandsTests(unittest.TestCase):
    def test_hide_and_close_cancel_only_the_same_tags_delayed_windows(self):
        source = Path(__file__).resolve().parents[1] / "webview_player.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        names = {"_normalize_cmd", "_pending_key", "_schedule_or_process", "_process_due_cmds", "_process_cmd"}
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        code = compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec")
        for operation in ("hide", "close"):
            with self.subTest(operation=operation):
                clock = [100.0]
                namespace = {
                    "time": SimpleNamespace(time=lambda: clock[0]),
                    "pending_cmds": {},
                    "_ensure_window": Mock(), "_hide_window": Mock(),
                    "_close_window": Mock(), "_resume_media_for_tag": Mock(),
                    "print": Mock(),
                }
                exec(code, namespace)
                schedule = namespace["_schedule_or_process"]
                for op in ("upsert", "open", "show"):
                    schedule({"op": op, "tag": "WebView2_default_S0_Z1", "delay_ms": 1000})
                schedule({"op": "upsert", "tag": "WebView2_default_S0_Z2", "delay_ms": 1000})
                schedule({"op": "upsert", "tag": "WebView2_screen2_S0_Z1", "delay_ms": 1000})
                schedule({"op": operation, "tag": "WebView2_default_S0_Z1"})
                self.assertEqual(len(namespace["pending_cmds"]), 2)
                clock[0] += 2
                namespace["_process_due_cmds"]()
                self.assertEqual(
                    [call.args[0] for call in namespace["_ensure_window"].call_args_list],
                    ["WebView2_default_S0_Z2", "WebView2_screen2_S0_Z1"],
                )
                self.assertEqual(namespace["pending_cmds"], {})
                # A new explicit show after the screen returns is still allowed.
                schedule({"op": "show", "tag": "WebView2_default_S0_Z1"})
                self.assertEqual(namespace["_ensure_window"].call_args.args[0], "WebView2_default_S0_Z1")


if __name__ == "__main__":
    unittest.main()
