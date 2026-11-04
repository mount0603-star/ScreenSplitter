import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import admin
import config_manager
import web_server


class UsageHelpTests(unittest.TestCase):
    def test_help_serves_only_the_shipped_file_without_touching_player_data(self):
        with tempfile.TemporaryDirectory(prefix="ScreenSplitter help ") as root:
            docs = Path(root) / "docs"
            docs.mkdir()
            content = '<!doctype html><meta charset="utf-8"><h1>使用說明</h1>'.encode("utf-8")
            (docs / "usage.html").write_bytes(content)
            (Path(root) / "config.json").write_text('private fixture')
            with mock.patch.object(config_manager, "SHARED_ROOT", root), \
                 mock.patch.object(config_manager, "save_config") as save, \
                 mock.patch.object(config_manager, "ensure_profile") as ensure, \
                 mock.patch.object(web_server, "_write_profile_cmd") as command:
                client = web_server.app.test_client()
                response = client.get('/help?profile=screen2&filename=../config.json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'text/html')
                self.assertEqual(response.data, content)
                response.close()
                self.assertEqual(client.post('/help').status_code, 405)
                self.assertEqual(client.get('/help/../../config.json').status_code, 404)
                (docs / "usage.html").unlink()
                self.assertEqual(client.get('/help').status_code, 404)
                save.assert_not_called()
                ensure.assert_not_called()
                command.assert_not_called()

    def test_management_page_opens_help_without_replacing_the_editor(self):
        page = web_server.app.test_client().get('/').get_data(as_text=True)
        self.assertIn('href="/help" target="_blank" rel="noopener"', page)
        self.assertIn('onclick="saveConfig()"', page)
        self.assertIn('id="profile-select"', page)

    def test_desktop_help_uses_install_root_not_the_profile_or_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="ScreenSplitter 說明 ") as root:
            path = Path(root) / 'docs/usage.html'
            path.parent.mkdir()
            path.write_text('offline manual', encoding='utf-8')
            window = SimpleNamespace()
            with mock.patch.object(config_manager, 'SHARED_ROOT', root), \
                 mock.patch.object(config_manager, 'DATA_DIR', os.path.join(root, 'other-profile')), \
                 mock.patch.object(admin.QDesktopServices, 'openUrl', return_value=True) as open_url, \
                 mock.patch.object(admin.QMessageBox, 'warning') as warning:
                admin.AdminWindow.open_help(window)
                self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), path)
                warning.assert_not_called()
                open_url.return_value = False
                admin.AdminWindow.open_help(window)
                self.assertEqual(warning.call_count, 1)
                path.unlink()
                open_url.reset_mock()
                admin.AdminWindow.open_help(window)
                open_url.assert_not_called()
                self.assertEqual(warning.call_count, 2)


if __name__ == '__main__':
    unittest.main()
