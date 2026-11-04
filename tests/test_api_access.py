"""Optional WebServer access control must stay invisible until it is configured."""
import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config_manager
import web_server


class ApiAccessTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory(prefix="screensplitter-api-access-")
        self.addCleanup(root.cleanup)
        self.access_file = os.path.join(root.name, "api_access.json")
        mock.patch.object(web_server, "API_ACCESS_FILE", self.access_file).start()
        self.audit = mock.patch.object(web_server, "_audit_log").start()
        mock.patch.dict(os.environ).start()
        self.addCleanup(mock.patch.stopall)
        os.environ.pop(web_server.API_TOKEN_ENV, None)
        with web_server._DENY_LOG_LOCK:
            web_server._DENY_LOG_LAST.clear()
        self.client = web_server.app.test_client()

    def write_access(self, **data):
        with open(self.access_file, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def get(self, path, remote="10.0.0.9", **kwargs):
        return self.client.get(path, environ_base={"REMOTE_ADDR": remote}, **kwargs)

    def test_open_mode_is_unchanged_without_configuration(self):
        self.assertEqual(self.get("/api/profiles").status_code, 200)
        self.assertIn('id="profile-select"', self.get("/").get_data(as_text=True))
        self.assertIn("OPEN", web_server._api_access_mode_summary())
        self.audit.assert_not_called()

    def test_token_file_blocks_remote_clients_until_a_token_is_presented(self):
        self.write_access(token="s3cret")
        denied = self.get("/api/profiles")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.get_json()["error"], "unauthorized")
        self.assertEqual(self.get("/api/profiles", headers={"X-Api-Token": "wrong"}).status_code, 401)
        self.assertEqual(self.get("/api/profiles", headers={"X-Api-Token": "s3cret"}).status_code, 200)
        self.assertEqual(self.get("/api/profiles", headers={"Authorization": "Bearer s3cret"}).status_code, 200)
        self.assertEqual(self.get("/api/profiles", remote="127.0.0.1").status_code, 200)
        self.assertEqual(self.get("/api/profiles", remote="::1").status_code, 200)

    def test_browser_sign_in_sets_http_only_cookie_and_serves_editor(self):
        self.write_access(token="s3cret")
        page = self.get("/")
        self.assertEqual(page.status_code, 401)
        self.assertIn('action="/login"', page.get_data(as_text=True))
        bad = self.client.post("/login", data={"token": "nope"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(self.get("/api/profiles").status_code, 401)
        good = self.client.post("/login", data={"token": "s3cret"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
        self.assertEqual(good.status_code, 302)
        self.assertIn("HttpOnly", good.headers.get("Set-Cookie", ""))
        self.assertIn('id="profile-select"', self.get("/").get_data(as_text=True))
        self.assertEqual(self.get("/api/profiles").status_code, 200)

    def test_allowed_ips_admit_listed_clients_without_a_token(self):
        self.write_access(allowed_ips=["10.0.0.9"])
        self.assertEqual(self.get("/api/profiles").status_code, 200)
        self.assertEqual(self.get("/api/profiles", remote="10.0.0.10").status_code, 401)
        self.assertEqual(self.get("/", remote="10.0.0.10").status_code, 401)
        self.assertEqual(self.get("/api/profiles", remote="10.0.0.10",
                                  headers={"X-Forwarded-For": "10.0.0.9"}).status_code, 401)

    def test_env_token_restricts_without_a_file(self):
        os.environ[web_server.API_TOKEN_ENV] = "env-token"
        self.assertEqual(self.get("/api/profiles").status_code, 401)
        self.assertEqual(self.get("/api/profiles", headers={"X-Api-Token": "env-token"}).status_code, 200)

    def test_unreadable_file_fails_closed_except_for_loopback(self):
        with open(self.access_file, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        os.environ[web_server.API_TOKEN_ENV] = "env-token"
        self.assertEqual(self.get("/api/profiles", headers={"X-Api-Token": "env-token"}).status_code, 401)
        self.assertEqual(self.get("/api/profiles", remote="127.0.0.1").status_code, 200)
        self.assertIn("cannot read", web_server._api_access_mode_summary())

    def test_help_stays_public_in_restricted_mode(self):
        self.write_access(token="s3cret")
        with tempfile.TemporaryDirectory() as root:
            docs = Path(root) / "docs"
            docs.mkdir()
            (docs / "usage.html").write_text("<p>manual</p>", encoding="utf-8")
            with mock.patch.object(config_manager, "SHARED_ROOT", root):
                response = self.get("/help")
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_denied_requests_are_audited_once_per_client_per_minute(self):
        self.write_access(token="s3cret")
        for _ in range(3):
            self.get("/api/status?profile=default")
        self.get("/api/status?profile=default", remote="10.0.0.10")
        self.assertEqual(self.audit.call_count, 2)
        self.assertIn("[AUTH] denied GET /api/status", self.audit.call_args_list[0].args[0])

    def test_dev_server_never_enables_the_interactive_debugger(self):
        tree = ast.parse(Path(web_server.__file__).read_text(encoding="utf-8"))
        runs = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and ast.unparse(node.func) == "app.run"]
        self.assertEqual(len(runs), 2)
        for call in runs:
            keywords = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            self.assertEqual(keywords["port"], 5000)
            if keywords.get("debug"):
                self.assertIs(keywords.get("use_debugger"), False)


if __name__ == "__main__":
    unittest.main()
