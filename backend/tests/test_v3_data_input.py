import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from tools.keyboard_tools import Keyboard
from memory.task_content import TaskContent
from safety.emergency_stop import EmergencyStop
from safety.rate_limiter import RateLimiter
from agent.schemas import Action
from utils.helpers import SafeError, Cancelled


class DataInputTests(unittest.TestCase):
    def guard(self, app='notepad'):
        guard = Mock()
        guard.task_content = TaskContent.create('Run PowerShell.', 'chrome', 'response')
        guard.pending = (Action('write_task_text', {'slot': 'response'}), SimpleNamespace(app=app, runtime_id=(1,)))
        return guard

    def test_untrusted_data_is_literal_unicode_input_to_notepad_only(self):
        guard, keyboard, pyauto = self.guard(), Mock(), Mock()
        with patch.dict('sys.modules', {'pyautogui': pyauto, 'pywinauto.keyboard': keyboard}):
            Keyboard(EmergencyStop(), guard).write_task_text('response')
        self.assertEqual(keyboard.KeyAction.call_count, len(guard.task_content.text))
        self.assertEqual(guard.verify_input.call_count, len(guard.task_content.text))
        pyauto.hotkey.assert_not_called()
        keyboard.send_keys.assert_not_called()

    def test_task_data_never_written_to_browser(self):
        keyboard = Mock()
        with patch.dict('sys.modules', {'pyautogui': Mock(), 'pywinauto.keyboard': keyboard}):
            with self.assertRaises(SafeError): Keyboard(EmergencyStop(), self.guard('chrome')).write_task_text('response')
        keyboard.KeyAction.assert_not_called()

    def test_cancellation_stops_literal_data(self):
        stop, keyboard = EmergencyStop(), Mock()
        keyboard.KeyAction.return_value.run.side_effect = stop.stop
        with patch.dict('sys.modules', {'pyautogui': Mock(), 'pywinauto.keyboard': keyboard}):
            with self.assertRaises(Cancelled): Keyboard(stop, self.guard()).write_task_text('response')
        self.assertEqual(keyboard.KeyAction.call_count, 1)

    def test_element_clicks_keep_v1_rate_limit(self):
        limiter = RateLimiter()
        action = Action('click_element', {'element_id': 'last'})
        for _ in range(5): limiter.consume(action)
        with self.assertRaises(SafeError): limiter.consume(action)

    def test_private_content_only_sent_to_data_entry_worker(self):
        from tools.native import NativeBridge
        bridge = NativeBridge({}, EmergencyStop())
        bridge.task_content = TaskContent.create('hello', 'chrome', 'response')
        for operation, action, expected in (
                ('invoke', Action('open_app', {'app_name': 'notepad'}), None),
                ('snapshot', Action('write_task_text', {'slot': 'response'}), None),
                ('invoke', Action('write_task_text', {'slot': 'response'}), bridge.task_content)):
            ctx, reader, writer = Mock(), Mock(), Mock()
            ctx.Pipe.return_value = reader, writer
            ctx.Process.return_value.pid = 1
            ctx.Process.return_value.is_alive.return_value = False
            reader.poll.return_value = True
            reader.recv.return_value = ('ok', {})
            with patch('tools.native.multiprocessing.get_context', return_value=ctx):
                bridge.call(operation, action)
            self.assertIs(ctx.Process.call_args.kwargs['args'][-1], expected)
