"""update.bat must run from a copy of itself: git reset rewrites the file while it runs."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UpdateBatSelfCopyTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "update.bat").read_text(encoding="utf-8")
        self.lines = self.text.splitlines()

    def index_of(self, needle):
        return next(i for i, line in enumerate(self.lines) if needle in line)

    def test_copies_itself_before_touching_git(self):
        copy_line = self.index_of("copy /y \"%~f0\"")
        call_line = self.index_of("call \"!TEMP!\\ScreenSplitter_update.bat\" %*")
        first_git = self.index_of("\"%GIT%\"")
        self.assertLess(copy_line, call_line)
        self.assertLess(call_line, first_git, "the copy must run before any git command")
        self.assertIn("if not defined SS_UPDATE_ROOT (", self.lines[:copy_line])
        self.assertIn("    exit /b !ERRORLEVEL!", self.lines[call_line:call_line + 2])

    def test_copy_resolves_paths_from_the_install_folder_not_temp(self):
        guard_end = self.index_of("set \"ROOT=%SS_UPDATE_ROOT%\"")
        for line in self.lines[guard_end:]:
            self.assertNotIn("%~dp0", line, line)
            self.assertNotIn("%~f0", line, line)
        self.assertIn("cd /d \"%ROOT%\"", self.lines[guard_end:guard_end + 2])

    def test_still_resets_and_restarts(self):
        reset = self.index_of("reset --hard origin/%BRANCH%")
        self.assertLess(reset, self.index_of("call \"%ROOT%run_webserver.bat\""))
        self.assertLess(reset, self.index_of("start \"\" \"%ROOT%run_player.bat\""))
        self.assertNotIn("timeout ", self.text)


if __name__ == "__main__":
    unittest.main()
