import unittest
from unittest.mock import patch
from pathlib import Path
from tools.app_tools import Apps
from tools.browser_tools import Browser
from tools.factory import build_tools
from safety.emergency_stop import EmergencyStop
from utils.helpers import SafeError

class ToolTests(unittest.TestCase):
    def test_approved_launch(self):
        apps = Apps({"chrome": {"allowed": True}})
        with patch.object(apps, "resolve", return_value=Path("C:/trusted/chrome.exe")), patch("tools.app_tools.subprocess.Popen") as launch:
            apps.open_app("chrome")
            self.assertFalse(launch.call_args.kwargs["shell"])
            self.assertIsInstance(launch.call_args.args[0], list)

    def test_path_cannot_be_app(self):
        with self.assertRaises(SafeError): Apps({}).resolve("C:/evil.exe")

    def test_browser_fixed_executable(self):
        apps = Apps({})
        with patch.object(apps, "resolve", return_value=Path("C:/trusted/chrome.exe")), patch("tools.browser_tools.subprocess.Popen") as launch:
            Browser(apps).open_url("claude.ai")
            self.assertEqual(launch.call_args.args[0][1], "https://claude.ai")
            self.assertFalse(launch.call_args.kwargs["shell"])

    def test_registry_complete(self):
        build_tools({}, EmergencyStop())
