import unittest
from unittest.mock import Mock
from agent.runtime import Runtime
from agent.router import Result
from config.settings import Settings
from safety.emergency_stop import EmergencyStop


class OneCommandTests(unittest.TestCase):
    def test_uses_brain_and_real_monitor_lifecycle(self):
        brain = Mock()
        brain.handle.return_value = [Result('get_current_time', 'SUCCESS')]
        runtime = Runtime(Settings(), brain, Mock(), EmergencyStop())
        runtime.keys = Mock()
        result = runtime.run_once('What time is it')
        runtime.keys.start.assert_called_once()
        runtime.keys.close.assert_called_once()
        self.assertEqual(brain.handle.call_args.args[0].text, 'What time is it')
        self.assertEqual(result[-1].status, 'SUCCESS')
        self.assertTrue(runtime.stop.stopped)
