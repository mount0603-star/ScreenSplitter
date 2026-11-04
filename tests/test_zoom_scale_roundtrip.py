"""A configured scale must survive the --scale command line unchanged, or the
standalone WebView would be killed and relaunched on every enforce tick."""
import unittest

import player
import webview_player


class ZoomScaleRoundTripTests(unittest.TestCase):
    def test_player_and_host_normalize_identically_after_command_line_formatting(self):
        for configured in (2.5, 1.15, 1.2345678, 0.333333333, 4.9999999, "1.00000001", 0.25, 5):
            with self.subTest(configured=configured):
                wanted = player.normalize_webview_scale(configured)
                on_command_line = f"{wanted:g}"  # exactly how player.py writes --scale
                reported = player.normalize_webview_scale(player._webview_command_value(
                    ["WebViewPlayer.exe", "--scale", on_command_line], "--scale", 1.0))
                self.assertEqual(reported, wanted)
                self.assertEqual(webview_player.normalize_webview_scale(on_command_line), wanted)

    def test_clamping_and_invalid_values_are_unchanged(self):
        for value, expected in ((0.1, 0.25), (10, 5.0), ("oops", 1.0), (None, 1.0), (0, 1.0)):
            with self.subTest(value=value):
                self.assertEqual(player.normalize_webview_scale(value), expected)
                self.assertEqual(webview_player.normalize_webview_scale(value), expected)


if __name__ == "__main__":
    unittest.main()
