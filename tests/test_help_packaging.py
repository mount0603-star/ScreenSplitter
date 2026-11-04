import os
import re
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

import build_release


PROJECT_ROOT = Path(build_release.__file__).resolve().parent


class HelpPackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="screensplitter-help-package-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.root)
        self.target = self.root / "build" / "ScreenSplitter_Dist"
        self.assertEqual(self.target.resolve().parent.parent, self.root)

    def test_missing_manual_fails_before_replacing_existing_staging(self):
        self.target.mkdir(parents=True)
        existing = self.target / "previous-build.txt"
        existing.write_bytes(b"keep previous staging")
        with self.assertRaisesRegex(RuntimeError, r"Required offline user manual missing: docs[\\/]usage\.html"):
            build_release.post_process()
        self.assertEqual(existing.read_bytes(), b"keep previous staging")

    def test_post_process_ships_identical_manual_at_the_installer_help_target(self):
        for component in ("AdminTool", "Player", "WebServer", "WebViewPlayer"):
            component_dir = self.root / "build" / "dist" / component
            component_dir.mkdir(parents=True)
            (component_dir / (component + ".exe")).write_bytes(b"minimal packaging fixture")
        for folder in ("templates", "static", "docs"):
            (self.root / folder).mkdir()
        content = "<!doctype html><meta charset=utf-8><title>使用說明</title><p>離線交接手冊</p>".encode("utf-8")
        (self.root / "docs" / "usage.html").write_bytes(content)
        (self.root / "工程開發日誌.html").write_text("historical notes", encoding="utf-8")
        # Only the executable launch is replaced; staging and file copies are real.
        with mock.patch.object(build_release, "verify_player_runtime") as runtime_check:
            build_release.post_process()
        runtime_check.assert_called_once_with(os.path.join("build", "ScreenSplitter_Dist"))
        installed_manual = self.target / "docs" / "usage.html"
        self.assertEqual(installed_manual.read_bytes(), content)
        self.assertEqual(list((self.target / "docs").iterdir()), [installed_manual])
        self.assertFalse((self.target / "Player" / "工程開發日誌.html").exists())

        setup = (PROJECT_ROOT / "setup_script.iss").read_text(encoding="utf-8-sig")
        help_link = next(line for line in setup.splitlines() if line.startswith('Name: "{autoprograms}\\ScreenSplitter\\'))
        target = re.search(r'Filename: "\{app\}\\([^"\n]+)"', help_link).group(1)
        self.assertEqual(target, r"docs\usage.html")
        self.assertEqual(self.target.joinpath(*PureWindowsPath(target).parts), installed_manual)
        self.assertIn('Source: "build\\ScreenSplitter_Dist\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs', setup)


if __name__ == "__main__":
    unittest.main()
