"""WebServer behaviour needed when the host runs the source checkout instead of the EXEs."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import build_release
import config_manager
import web_server


def process(pid, name, cmdline):
    return SimpleNamespace(info={"pid": pid, "name": name, "cmdline": cmdline, "memory_info": None, "create_time": 1.0})


class RuntimeProcessKindTests(unittest.TestCase):
    def test_packaged_and_source_processes_are_classified(self):
        kind = web_server._runtime_process_kind
        self.assertEqual(kind("Player.exe", ["Player.exe", "--profile", "default"]), "player")
        self.assertEqual(kind("WebViewPlayer.exe", ["WebViewPlayer.exe", "--host"]), "webview")
        self.assertEqual(kind("pythonw.exe", [r"D:\app\python\pythonw.exe", r"D:\app\player.py", "--profile", "screen2"]), "player")
        self.assertEqual(kind("python.exe", ["python.exe", r"D:\app\webview_player.py", "--title", "x"]), "webview")
        self.assertIsNone(kind("python.exe", ["python.exe", r"D:\app\web_server.py", "--production"]))
        self.assertIsNone(kind("python.exe", ["python.exe"]))
        self.assertIsNone(kind("msedgewebview2.exe", ["msedgewebview2.exe"]))

    def test_source_mode_players_are_found_per_profile(self):
        procs = [
            process(1, "pythonw.exe", [r"D:\app\python\pythonw.exe", r"D:\app\player.py", "--profile", "screen2"]),
            process(2, "pythonw.exe", [r"D:\app\python\pythonw.exe", r"D:\app\player.py", "--profile", "default"]),
            process(3, "pythonw.exe", [r"D:\app\python\pythonw.exe", r"D:\app\web_server.py", "--production"]),
            process(4, "Player.exe", [r"D:\app\Player\Player.exe", "--profile", "screen2"]),
        ]
        with mock.patch.object(web_server.psutil, "process_iter", return_value=procs):
            self.assertEqual(web_server._profile_runtime_roots("screen2"), [procs[0], procs[3]])
            self.assertEqual(web_server._profile_runtime_roots("default"), [procs[1]])

    def test_process_snapshot_lists_source_mode_player_and_webserver(self):
        root = config_manager.get_data_dir("default")
        procs = [
            process(1, "pythonw.exe", [os.path.join(root, "python", "pythonw.exe"), os.path.join(root, "player.py"), "--profile", "default"]),
            process(2, "pythonw.exe", [os.path.join(root, "python", "pythonw.exe"), os.path.join(root, "web_server.py"), "--production"]),
            process(3, "pythonw.exe", [os.path.join(root, "python", "pythonw.exe"), os.path.join(root, "player.py"), "--profile", "screen2"]),
            process(4, "python.exe", ["python.exe", "-m", "streamlit", "run", "app.py"]),
        ]
        with mock.patch.object(web_server.psutil, "process_iter", return_value=procs):
            pids = [item["pid"] for item in web_server._process_snapshot("default")["items"]]
        self.assertEqual(pids, [1, 2])


class VersionTests(unittest.TestCase):
    def test_version_endpoint_reads_version_file_and_git_head(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "VERSION").write_text("9.9.9\n", encoding="utf-8")
            git = Path(root, ".git")
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (git / "refs" / "heads" / "main").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
            with mock.patch.object(config_manager, "SHARED_ROOT", root):
                with mock.patch.object(web_server, "APP_VERSION", web_server._read_app_version()):
                    body = web_server.app.test_client().get("/api/version").get_json()
                    profiles = web_server.app.test_client().get("/api/profiles").get_json()
        self.assertEqual(body["version"], "9.9.9")
        self.assertEqual(body["commit"], "0123456789ab")
        self.assertEqual(body["mode"], "source")
        self.assertEqual(profiles["version"], "9.9.9")

    def test_packed_refs_and_missing_files_are_handled(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(config_manager, "SHARED_ROOT", root):
                self.assertEqual(web_server._read_app_version(), "unknown")
                self.assertEqual(web_server._read_git_commit(), "")
                git = Path(root, ".git")
                git.mkdir()
                (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
                (git / "packed-refs").write_text("# pack-refs\nfedcba9876543210fedcba9876543210fedcba98 refs/heads/main\n", encoding="utf-8")
                self.assertEqual(web_server._read_git_commit(), "fedcba987654")

    def test_packaged_build_ships_the_version_file(self):
        temporary = tempfile.TemporaryDirectory(prefix="screensplitter-version-package-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(root)
        for component in ("AdminTool", "Player", "WebServer", "WebViewPlayer"):
            (root / "build" / "dist" / component).mkdir(parents=True)
        for folder in ("templates", "static", "docs"):
            (root / folder).mkdir()
        (root / "docs" / "usage.html").write_text("<p>manual</p>", encoding="utf-8")
        (root / "VERSION").write_text("7.7.7\n", encoding="utf-8")
        with mock.patch.object(build_release, "verify_player_runtime"):
            build_release.post_process()
        self.assertEqual((root / "build" / "ScreenSplitter_Dist" / "VERSION").read_text(encoding="utf-8"), "7.7.7\n")


class ProductionModeTests(unittest.TestCase):
    def test_flag_environment_or_frozen_select_production(self):
        mode = web_server._production_mode
        self.assertFalse(mode([], {}, False))
        self.assertTrue(mode(["--production"], {}, False))
        self.assertTrue(mode([], {"SCREENSPLITTER_PRODUCTION": "1"}, False))
        self.assertFalse(mode([], {"SCREENSPLITTER_PRODUCTION": "0"}, False))
        self.assertTrue(mode([], {}, True))


if __name__ == "__main__":
    unittest.main()
