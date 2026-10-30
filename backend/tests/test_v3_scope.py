import unittest
from agent.schemas import Action, Plan
from agent.task_scope import validate_model_scope
from agent.state_machine import TaskMachine, TaskStatus
from utils.helpers import SafeError


class ScopeTests(unittest.TestCase):
    def test_unrelated_model_actions_rejected(self):
        for action in (Action('open_app', {'app_name': 'notepad'}),
                       Action('open_url', {'url': 'https://example.com'}),
                       Action('type_text', {'text': 'Unrequested message'}),
                       Action('press_key', {'key': 'enter'})):
            with self.assertRaises(SafeError):
                validate_model_scope('Open Chrome and show the time', Plan('x', (action,)), {})

    def test_related_explicit_model_actions_allowed(self):
        validate_model_scope('Open Chrome and go to example.com', Plan('x', (
            Action('open_app', {'app_name': 'chrome'}), Action('open_url', {'url': 'https://example.com'}))), {})

    def test_wait_has_explicit_lifecycle(self):
        machine = TaskMachine()
        for status in ('PLANNING', 'OBSERVING', 'EXECUTING', 'WAITING', 'OBSERVING', 'VERIFYING', 'COMPLETED'):
            machine.move(status)
        self.assertEqual(machine.status, TaskStatus.COMPLETED)
