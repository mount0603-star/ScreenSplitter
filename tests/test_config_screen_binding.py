import copy
import os
import tempfile
import unittest
from unittest import mock

import config_manager
import web_server


class ConfigScreenBindingTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory(prefix="screen-binding-api-")
        self.addCleanup(root.cleanup)
        for name, path in (("DATA_DIR", root.name), ("PROFILES_DIR", os.path.join(root.name, "profiles"))):
            patcher = mock.patch.object(config_manager, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        for profile, device in (("default", "BenQ TV"), ("screen2", "VA3456-WQHD")):
            config_manager.save_config({
                "global_settings": {"screen_device": device, "screen_x": 0, "screen_y": 0},
                "playlist": [{"name": profile, "duration": 60, "zones": []}],
            }, profile)
        self.client = web_server.app.test_client()

    def test_old_tab_payload_keeps_binding_and_saves_other_user_changes(self):
        original = config_manager.load_config("default")
        payload = copy.deepcopy(original)
        del payload["global_settings"]["screen_device"]
        payload["global_settings"]["screen_x"] = 11
        payload["playlist"][0]["zones"] = [{"type": "web", "isolate_webview": True}]
        response = self.client.post("/api/config?profile=default", json=payload)
        self.assertEqual(response.status_code, 200)
        expected = copy.deepcopy(payload)
        expected["global_settings"]["screen_device"] = "BenQ TV"
        self.assertEqual(config_manager.load_config("default"), expected)
        self.assertEqual(response.get_json()["config"], {**expected, "_profile": "default"})

    def test_explicit_changed_or_cleared_binding_is_honored(self):
        for device in ("Replacement TV", ""):
            with self.subTest(device=device):
                payload = config_manager.load_config("default")
                payload["global_settings"]["screen_device"] = device
                response = self.client.post("/api/config?profile=default", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(config_manager.load_config("default"), payload)

    def test_omitted_binding_comes_from_the_resolved_profile_only(self):
        original_default = config_manager.load_config("default")
        payload = config_manager.load_config("screen2")
        del payload["global_settings"]["screen_device"]
        payload["_profile"] = "screen2"
        payload["playlist"][0]["duration"] = 0
        response = self.client.post("/api/config?profile=default", json=payload)
        self.assertEqual(response.status_code, 200)
        actual = config_manager.load_config("screen2")
        self.assertEqual(actual["global_settings"]["screen_device"], "VA3456-WQHD")
        self.assertEqual(actual["playlist"][0]["duration"], 0)
        self.assertEqual(config_manager.load_config("default"), original_default)
        self.assertFalse(actual["global_settings"]["alarm_lunch_enabled"])

    def test_no_previous_binding_is_not_invented(self):
        payload = {"global_settings": {"screen_x": 0, "screen_y": 0}, "playlist": []}
        config_manager.save_config(payload, "default")
        response = self.client.post("/api/config?profile=default", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(config_manager.load_config("default"), payload)


if __name__ == "__main__":
    unittest.main()
