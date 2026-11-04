"""Online update of a source-mode host: status check and starting update.bat from WebAdmin."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import config_manager
import web_server


REAL_UPDATE_RUNNING = web_server._update_running  # captured before setUp patches it


def git_result(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


class UpdateApiTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory(prefix="screensplitter-update-")
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        (self.root / ".git" / "refs" / "heads").mkdir(parents=True)
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (self.root / ".git" / "refs" / "heads" / "main").write_text("1111111111111111111111111111111111111111\n", encoding="utf-8")
        (self.root / "update.bat").write_text("@echo off\n", encoding="utf-8")
        (self.root / "git" / "cmd").mkdir(parents=True)
        (self.root / "git" / "cmd" / "git.exe").write_bytes(b"fake")
        for patcher in (
            mock.patch.object(config_manager, "SHARED_ROOT", str(self.root)),
            mock.patch.object(web_server, "DATA_DIR", str(self.root)),
            mock.patch.object(web_server, "APP_VERSION", "4.4.1"),
            mock.patch.object(web_server, "_update_running", return_value=False),
            mock.patch.object(web_server, "_audit_log"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        web_server._UPDATE_CACHE["result"] = None
        web_server._UPDATE_CACHE["checked_at"] = 0.0
        self.client = web_server.app.test_client()

    def fake_git(self, remote_commit, remote_version="4.4.2", fetch_rc=0):
        def run(args, timeout=45):
            if args[0] == "fetch":
                return git_result("", fetch_rc, "fatal: could not read Username" if fetch_rc else "")
            if args[0] == "rev-parse":
                return git_result(remote_commit + "\n")
            if args[0] == "show":
                return git_result(remote_version + "\n")
            raise AssertionError(args)
        return run

    def test_status_reports_an_available_version(self):
        with mock.patch.object(web_server, "_run_git", side_effect=self.fake_git("2" * 40)):
            body = self.client.get("/api/update/status").get_json()
        self.assertTrue(body["supported"])
        self.assertEqual(body["installed"], {"version": "4.4.1", "commit": "111111111111"})
        self.assertEqual(body["available"], {"version": "4.4.2", "commit": "2" * 40})
        self.assertTrue(body["update_available"])
        self.assertIsNone(body["error"])
        self.assertFalse(body["running"])

    def test_status_is_up_to_date_when_origin_matches_head(self):
        with mock.patch.object(web_server, "_run_git", side_effect=self.fake_git("1" * 40, "4.4.1")):
            body = self.client.get("/api/update/status").get_json()
        self.assertFalse(body["update_available"])
        self.assertEqual(body["available"]["version"], "4.4.1")

    def test_status_uses_the_cache_until_refresh_is_requested(self):
        run = mock.Mock(side_effect=self.fake_git("2" * 40))
        with mock.patch.object(web_server, "_run_git", run):
            self.client.get("/api/update/status")
            self.client.get("/api/update/status")
            fetches = [c for c in run.call_args_list if c.args[0][0] == "fetch"]
            self.assertEqual(len(fetches), 1)
            self.client.get("/api/update/status?refresh=1")
            fetches = [c for c in run.call_args_list if c.args[0][0] == "fetch"]
            self.assertEqual(len(fetches), 2)

    def test_fetch_failure_is_reported_not_raised(self):
        with mock.patch.object(web_server, "_run_git", side_effect=self.fake_git("2" * 40, fetch_rc=128)):
            body = self.client.get("/api/update/status").get_json()
        self.assertFalse(body["update_available"])
        self.assertIn("could not read Username", body["error"])
        self.assertIsNone(body["available"])

    def test_packaged_build_or_missing_checkout_is_unsupported(self):
        with mock.patch.object(web_server.sys, "frozen", True, create=True):
            body = self.client.get("/api/update/status").get_json()
            self.assertFalse(body["supported"])
            self.assertEqual(body["mode"], "packaged")
            self.assertEqual(self.client.post("/api/update").status_code, 400)
        (self.root / "update.bat").unlink()
        self.assertFalse(web_server._update_supported())

    def test_start_runs_update_bat_detached_with_log_file(self):
        with mock.patch.object(web_server.subprocess, "Popen", return_value=SimpleNamespace(pid=4242)) as popen:
            response = self.client.post("/api/update", json={})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["pid"], 4242)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["cmd.exe", "/c", str(self.root / "update.bat")])
        self.assertEqual(kwargs["cwd"], str(self.root))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"].name, str(self.root / "update.log"))
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertTrue((self.root / "update.log").exists())
        web_server._audit_log.assert_called()

    def test_force_flag_and_already_running(self):
        with mock.patch.object(web_server.subprocess, "Popen", return_value=SimpleNamespace(pid=1)) as popen:
            self.client.post("/api/update", json={"force": True})
        self.assertEqual(popen.call_args.args[0][-1], "force")
        with mock.patch.object(web_server, "_update_running", return_value=True), \
             mock.patch.object(web_server.subprocess, "Popen") as popen:
            response = self.client.post("/api/update", json={})
        self.assertEqual(response.status_code, 409)
        popen.assert_not_called()

    def test_running_detection_matches_only_this_installation(self):
        mine = SimpleNamespace(info={"name": "cmd.exe", "cmdline": ["cmd.exe", "/c", str(self.root / "update.bat")]})
        other = SimpleNamespace(info={"name": "cmd.exe", "cmdline": ["cmd.exe", "/c", r"D:\other\update.bat"]})
        with mock.patch.object(web_server.psutil, "process_iter", return_value=[other]):
            self.assertFalse(REAL_UPDATE_RUNNING())
        with mock.patch.object(web_server.psutil, "process_iter", return_value=[other, mine]):
            self.assertTrue(REAL_UPDATE_RUNNING())

    def test_log_tail_decodes_console_output(self):
        (self.root / "update.log").write_bytes("Installed commit : abc\n更新完成\n".encode("cp950"))
        self.assertEqual(web_server._update_log_tail().splitlines()[-1], "更新完成")

    def test_management_page_has_version_badge_and_update_button(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="version-badge"', page)
        self.assertIn('id="update-btn"', page)
        self.assertIn("/api/update/status", page)
        self.assertIn("'/api/update'", page)


if __name__ == "__main__":
    unittest.main()
