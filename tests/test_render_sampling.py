"""Verify the real sampling method using synthetic colors and mocked GDI calls."""
import ast
import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "player.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
METHOD = next(node for node in ast.walk(TREE)
              if isinstance(node, ast.FunctionDef) and node.name == "_sample_visible_webview_surface")
CODE = compile(ast.Module(body=[METHOD], type_ignores=[]), str(SOURCE), "exec")


class RenderSamplingTests(unittest.TestCase):
    def setUp(self):
        self.trace = []
        self.gdi = SimpleNamespace()
        for name, result in {
            "CreateCompatibleDC": 20, "CreateCompatibleBitmap": 30,
            "SelectObject": 40, "DeleteObject": 1, "DeleteDC": 1,
            "SetStretchBltMode": 1, "StretchBlt": 1, "GdiFlush": 1, "GetPixel": 0,
        }.items():
            def effect(*args, _name=name, _result=result):
                self.trace.append((_name, args))
                return _result
            setattr(self.gdi, name, Mock(side_effect=effect))
        self.gui = SimpleNamespace(
            IsWindow=Mock(return_value=True), IsWindowVisible=Mock(return_value=True),
            IsIconic=Mock(return_value=False), GetWindowRect=Mock(return_value=(0, 0, 1920, 1080)),
            WindowFromPoint=Mock(return_value=100), IsChild=Mock(return_value=False),
            GetDC=Mock(return_value=10),
            ReleaseDC=Mock(side_effect=lambda *args: self.trace.append(("ReleaseDC", args))),
        )
        self.metrics = {76: 0, 77: 0, 78: 1920, 79: 1080}
        namespace = {
            "ctypes": SimpleNamespace(c_void_p=ctypes.c_void_p, c_int=ctypes.c_int,
                                      c_uint32=ctypes.c_uint32, windll=SimpleNamespace(gdi32=self.gdi)),
            "win32gui": self.gui,
            "win32api": SimpleNamespace(GetSystemMetrics=lambda index: self.metrics[index]),
            "win32con": SimpleNamespace(SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77,
                                        SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79,
                                        SRCCOPY=0x00CC0020),
        }
        exec(CODE, namespace)
        self.sample = lambda hwnd=100: namespace["_sample_visible_webview_surface"](None, hwnd)

    def assert_cleaned(self, bitmap=True, memory=True, restore=True):
        if restore:
            self.gdi.SelectObject.assert_any_call(20, 40)
        if bitmap:
            self.gdi.DeleteObject.assert_called_once_with(30)
        else:
            self.gdi.DeleteObject.assert_not_called()
        if memory:
            self.gdi.DeleteDC.assert_called_once_with(20)
        else:
            self.gdi.DeleteDC.assert_not_called()
        self.gui.ReleaseDC.assert_called_once_with(0, 10)

    def test_single_capture_samples_memory_and_preserves_black_result_fields(self):
        result = self.sample()
        self.assertEqual(result, {"state": "black", "samples": 144, "black_ratio": 1.0,
                                  "white_ratio": 0.0, "average_rgb": [0.0, 0.0, 0.0]})
        self.gdi.StretchBlt.assert_called_once_with(20, 0, 0, 16, 9, 10, 0, 0, 1920, 1080, 0x00CC0020)
        self.gdi.CreateCompatibleBitmap.assert_called_once_with(10, 16, 9)
        self.gdi.SetStretchBltMode.assert_called_once_with(20, 3)
        self.assertEqual(len(self.gdi.GetPixel.call_args_list), 144)
        self.assertEqual([call.args for call in self.gdi.GetPixel.call_args_list],
                         [(20, col, row) for row in range(9) for col in range(16)])
        self.assertEqual(self.gui.WindowFromPoint.call_count, 5)
        self.assert_cleaned()
        self.assertEqual([name for name, _ in self.trace[-4:]],
                         ["SelectObject", "DeleteObject", "DeleteDC", "ReleaseDC"])

    def test_classification_keeps_existing_thresholds_and_averages(self):
        for color, expected in ((0xFFFFFF, "white"), (0x808080, "content"), (0x050505, "black"),
                                (0x060606, "content"), (0xFAFAFA, "white"), (0xF9F9F9, "content")):
            with self.subTest(color=color):
                self.gdi.GetPixel.side_effect = None
                self.gdi.GetPixel.return_value = color
                result = self.sample()
                self.assertEqual(result["state"], expected)
                self.assertEqual(result["samples"], 144)
                self.assertEqual(result["average_rgb"], [float(color & 0xFF)] * 3)

    def test_flat_ratio_boundary_is_unchanged(self):
        for count, expected in ((142, "black"), (141, "content")):
            with self.subTest(count=count):
                self.gdi.GetPixel.side_effect = [0] * count + [0x808080] * (144 - count)
                result = self.sample()
                self.assertEqual(result["state"], expected)
                self.assertEqual(result["black_ratio"], round(count / 144, 4))

    def test_invalid_pixels_do_not_become_black(self):
        for valid_count in (0, 39, 40):
            with self.subTest(valid_count=valid_count):
                self.gdi.GetPixel.side_effect = [0] * valid_count + [0xFFFFFFFF] * (144 - valid_count)
                result = self.sample()
                self.assertEqual(result["state"], "unavailable" if valid_count < 40 else "black")
                self.assertEqual(result["samples"], valid_count)

    def test_hidden_minimized_missing_or_small_windows_are_not_captured(self):
        for guard in ("IsWindow", "IsWindowVisible", "IsIconic", "GetWindowRect"):
            with self.subTest(guard=guard):
                self.setUp()
                getattr(self.gui, guard).return_value = {
                    "IsWindow": False, "IsWindowVisible": False,
                    "IsIconic": True, "GetWindowRect": (0, 0, 119, 89),
                }[guard]
                self.assertEqual(self.sample()["state"], "unavailable")
                self.gui.GetDC.assert_not_called()
                self.gdi.StretchBlt.assert_not_called()

    def test_offscreen_edges_are_not_captured(self):
        for rect in ((-1, 0, 1919, 1080), (0, -1, 1920, 1079),
                     (0, 0, 1921, 1080), (0, 0, 1920, 1081)):
            with self.subTest(rect=rect):
                self.gui.GetWindowRect.return_value = rect
                self.assertEqual(self.sample()["state"], "unavailable")
        self.gui.GetDC.assert_not_called()
        self.gui.WindowFromPoint.assert_not_called()

    def test_negative_coordinate_monitor_is_valid_inside_virtual_desktop(self):
        self.metrics = {76: -1920, 77: -100, 78: 3840, 79: 1180}
        self.gui.GetWindowRect.return_value = (-1800, -50, -200, 950)
        self.gui.WindowFromPoint.return_value = 101
        self.gui.IsChild.return_value = True
        self.assertEqual(self.sample()["samples"], 144)
        self.assertEqual(self.gui.IsChild.call_count, 5)
        self.gdi.StretchBlt.assert_called_once_with(20, 0, 0, 16, 9, 10, -1800, -50, 1600, 1000, 0x00CC0020)

    def test_occlusion_or_desktop_gap_skips_capture(self):
        for at_point in (999, 0):
            with self.subTest(at_point=at_point):
                self.gui.WindowFromPoint.return_value = at_point
                self.assertEqual(self.sample()["state"], "unavailable")
        self.gui.GetDC.assert_not_called()
        self.gdi.GetPixel.assert_not_called()

    def test_any_of_five_occluded_positions_skips_capture(self):
        for position in range(5):
            with self.subTest(position=position):
                points = [100] * 5
                points[position] = 999
                self.gui.WindowFromPoint.side_effect = points
                self.assertEqual(self.sample()["state"], "unavailable")
        self.gui.GetDC.assert_not_called()

    def test_failed_gdi_allocation_releases_only_acquired_resources(self):
        cases = (("CreateCompatibleDC", False, False, False),
                 ("CreateCompatibleBitmap", False, True, False),
                 ("SelectObject", True, True, False))
        for failed, bitmap, memory, restore in cases:
            with self.subTest(failed=failed):
                self.setUp()
                getattr(self.gdi, failed).side_effect = None
                getattr(self.gdi, failed).return_value = 0
                result = self.sample()
                self.assertEqual(result["state"], "capture_error")
                self.assertEqual(result["samples"], 0)
                self.assert_cleaned(bitmap=bitmap, memory=memory, restore=restore)
                self.gdi.GetPixel.assert_not_called()

    def test_failed_stretch_or_flush_restores_and_releases_every_resource(self):
        for failed in ("SetStretchBltMode", "StretchBlt", "GdiFlush"):
            with self.subTest(failed=failed):
                self.setUp()
                getattr(self.gdi, failed).side_effect = None
                getattr(self.gdi, failed).return_value = 0
                self.assertEqual(self.sample()["state"], "capture_error")
                self.assert_cleaned()
                self.gdi.GetPixel.assert_not_called()

    def test_sampling_exception_still_releases_resources(self):
        self.gdi.GetPixel.side_effect = RuntimeError("synthetic read failure")
        self.assertEqual(self.sample()["state"], "capture_error")
        self.assert_cleaned()

    def test_cleanup_exception_does_not_skip_remaining_cleanup(self):
        self.gdi.SelectObject.side_effect = [40, RuntimeError("synthetic restore failure")]
        self.assertEqual(self.sample()["state"], "capture_error")
        self.assert_cleaned()

    def test_missing_screen_dc_is_inconclusive(self):
        self.gui.GetDC.return_value = 0
        self.assertEqual(self.sample(), {"state": "unavailable", "samples": 0})
        self.gdi.CreateCompatibleDC.assert_not_called()
        self.gui.ReleaseDC.assert_not_called()


if __name__ == "__main__":
    unittest.main()
