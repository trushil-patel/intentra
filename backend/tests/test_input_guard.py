import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from agent.schemas import Action
from safety.ui_guard import UIGuard
from safety.emergency_stop import EmergencyStop
from tools.keyboard_tools import Keyboard
from utils.helpers import SafeError, Cancelled

class InputTests(unittest.TestCase):
    def test_typing_checks_each_character(self):
        token = EmergencyStop()
        guard = Mock()
        pyauto = Mock()
        with patch.dict(sys.modules, {"pyautogui": pyauto}):
            Keyboard(token, guard).type_text("hello")
        self.assertEqual(guard.verify_input.call_count, 5)
        self.assertEqual(pyauto.write.call_count, 5)

    def test_stop_during_typing(self):
        token = EmergencyStop()
        guard = Mock()
        pyauto = Mock()
        pyauto.write.side_effect = lambda *a, **kw: token.stop()
        with patch.dict(sys.modules, {"pyautogui": pyauto}):
            with self.assertRaises(Cancelled): Keyboard(token, guard).type_text("hello")
        self.assertEqual(pyauto.write.call_count, 1)

    def test_unicode_denied_without_input(self):
        pyauto = Mock()
        with patch.dict(sys.modules, {"pyautogui": pyauto}):
            with self.assertRaises(SafeError): Keyboard(EmergencyStop(), Mock()).type_text("\u20ac")
        pyauto.write.assert_not_called()

    def test_paste_denied_without_clipboard_access(self):
        pyauto = Mock()
        with patch.dict(sys.modules, {"pyautogui": pyauto}):
            with self.assertRaises(SafeError): Keyboard(EmergencyStop(), Mock()).hotkey(["ctrl", "v"])
        pyauto.hotkey.assert_not_called()

    def test_no_unarmed_input(self):
        with self.assertRaises(SafeError): UIGuard(Mock()).verify_input()

    def test_changed_target_denied(self):
        guard = UIGuard(Mock())
        guard.snapshot = Mock(return_value="other")
        with self.assertRaises(SafeError): guard.recheck(Action("click", {}), "approved")

    def test_held_modifier_denies_before_ui_access(self):
        user = Mock()
        user.GetAsyncKeyState.return_value = 0x8000
        windows = Mock()
        with patch("safety.ui_guard.ctypes.WinDLL", return_value=user, create=True):
            with self.assertRaises(SafeError): UIGuard(windows).snapshot(Action("click", {}))
        windows.active.assert_not_called()
