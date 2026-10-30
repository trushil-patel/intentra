import unittest
from unittest.mock import Mock
from agent.schemas import Action, Plan
from safety.validator import Validator
from test_validator import make_router
from utils.helpers import SafeError


class PlannedTargetTests(unittest.TestCase):
    def router(self):
        return make_router(validator=Validator({name: {'allowed': True} for name in ('chrome','notepad','vscode')}))

    def test_reported_plan_blocked_before_focus_or_confirmation(self):
        router, calls = self.router()
        result = router.execute(Plan('x', (Action('focus_app', {'app_name':'vscode'}),
                                          Action('focus_text_field', {}))), from_model=True)
        self.assertEqual(result[-1].status, 'BLOCKED')
        self.assertFalse(calls)
        router.confirmations.confirm.assert_not_called()

    def test_all_input_in_known_vscode_plan_rejected(self):
        for tool, args in (('type_text', {'text':'hello'}), ('press_key', {'key':'enter'}),
                           ('scroll', {'direction':'down','amount':1})):
            router, calls = self.router()
            with self.assertRaises(SafeError):
                router.preflight(Plan('x', (Action('open_app', {'app_name':'vscode'}), Action(tool,args))))
            self.assertFalse(calls)

    def test_standalone_vscode_window_management_remains_available(self):
        router, calls = self.router()
        result = router.execute(Plan('x', (Action('focus_app', {'app_name':'vscode'}),)))
        self.assertEqual(result[-1].status, 'SUCCESS')
        self.assertEqual(calls, ['focus_app'])

    def test_explicit_supported_destination_replaces_previous_app(self):
        for destination in (Action('focus_app', {'app_name':'notepad'}), Action('open_url', {'url':'https://claude.ai'})):
            router, calls = self.router()
            actions = (Action('focus_app', {'app_name':'vscode'}), destination, Action('focus_text_field', {}))
            self.assertEqual(len(router.preflight(Plan('x', actions))), 3)
            self.assertFalse(calls)

    def test_v2_full_preflight_rejects_before_one_action_execution(self):
        from agent.executor import Executor
        from agent.computer_state import ComputerState
        router, calls = self.router()
        observer = Mock()
        observer.observe.return_value = ComputerState()
        result = Executor(router, observer).execute(Plan('x', (Action('focus_app', {'app_name':'vscode'}),
                                                             Action('focus_text_field', {}))), from_model=True)
        self.assertEqual(result[-1].status, 'BLOCKED')
        self.assertFalse(calls)
        router.confirmations.confirm.assert_not_called()
