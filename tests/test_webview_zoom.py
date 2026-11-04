"""Native zoom defaults must not continually override interactive user zoom."""
from types import SimpleNamespace
import unittest
from unittest import mock

import webview_player


class WebViewZoomTests(unittest.TestCase):
    def test_configuration_applies_once_and_layout_repeats_keep_user_zoom(self):
        control = SimpleNamespace(CoreWebView2=object(), ZoomFactor=1.0)
        window = SimpleNamespace(title='test', native=SimpleNamespace(
            InvokeRequired=False, browser=SimpleNamespace(webview=control)))
        with mock.patch.object(webview_player, '_run_on_native_ui', wraps=webview_player._run_on_native_ui) as invoke:
            self.assertTrue(webview_player.apply_webview_zoom(window, 2.5))
            self.assertEqual(control.ZoomFactor, 2.5)
            control.ZoomFactor = 3.0  # A user zoomed after initial configuration.
            for _ in range(5):
                self.assertTrue(webview_player.apply_webview_zoom(window, 2.5))
            self.assertEqual(control.ZoomFactor, 3.0)
            self.assertEqual(invoke.call_count, 1)
            self.assertTrue(webview_player.apply_webview_zoom(window, 1.5))
            self.assertEqual(control.ZoomFactor, 1.5)
            self.assertEqual(invoke.call_count, 2)

    def test_uninitialized_native_control_keeps_requested_zoom_for_loaded_retry(self):
        window = SimpleNamespace(title='loading', native=None)
        self.assertFalse(webview_player.apply_webview_zoom(window, 2.5))
        self.assertEqual(window._requested_zoom, 2.5)
        self.assertFalse(hasattr(window, '_configured_zoom'))
        control = SimpleNamespace(CoreWebView2=None, ZoomFactor=1.0)
        window.native = SimpleNamespace(InvokeRequired=False, browser=SimpleNamespace(webview=control))
        self.assertFalse(webview_player.apply_webview_zoom(window, window._requested_zoom))
        control.CoreWebView2 = object()
        self.assertTrue(webview_player.apply_webview_zoom(window, window._requested_zoom))
        self.assertEqual(control.ZoomFactor, 2.5)

    def test_failed_native_update_can_retry_and_other_window_keeps_its_zoom(self):
        control = SimpleNamespace(CoreWebView2=object(), ZoomFactor=1.0)
        window = SimpleNamespace(title='retry', native=SimpleNamespace(
            InvokeRequired=False, browser=SimpleNamespace(webview=control)))
        with mock.patch.object(webview_player, '_run_on_native_ui', side_effect=RuntimeError('not ready')):
            self.assertFalse(webview_player.apply_webview_zoom(window, 2.5))
        self.assertFalse(hasattr(window, '_configured_zoom'))
        # WinForms Invoke(Action) returns None even when its callback succeeded.
        def invoke_action(_window, callback):
            callback()
        with mock.patch.object(webview_player, '_run_on_native_ui', side_effect=invoke_action):
            self.assertTrue(webview_player.apply_webview_zoom(window, 2.5))
        other_control = SimpleNamespace(CoreWebView2=object(), ZoomFactor=1.0)
        other = SimpleNamespace(title='other', native=SimpleNamespace(
            InvokeRequired=False, browser=SimpleNamespace(webview=other_control)))
        webview_player.apply_webview_zoom(other, 1.25)
        self.assertEqual(control.ZoomFactor, 2.5)
        self.assertEqual(other_control.ZoomFactor, 1.25)

    def test_invalid_configuration_never_reaches_native_zoom_property(self):
        for value in (None, '', 'bad', [], {}, 0, -1, float('nan'), float('inf'), '-Infinity'):
            with self.subTest(value=value):
                self.assertEqual(webview_player.normalize_webview_scale(value), 1.0)
        self.assertEqual(webview_player.normalize_webview_scale('2.5'), 2.5)
        self.assertEqual(webview_player.normalize_webview_scale(.01), .25)
        self.assertEqual(webview_player.normalize_webview_scale(100), 5.0)


if __name__ == '__main__':
    unittest.main()
