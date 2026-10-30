"""Task authorization must survive read-only steps and remain bound to its target."""
import unittest
from dataclasses import replace
from unittest.mock import Mock

from agent.computer_state import ComputerState, WindowState
from agent.executor import Executor
from agent.schemas import Action, Plan
from agent.router import Result
from agent.verifier import Verifier, VerificationStatus
from tools.registry import Registry, SPECS
from test_validator import make_router


WINDOW = WindowState(7, 'chrome')
FIELD = ComputerState(WINDOW, (WINDOW,), available=True, focused_id=(1,),
                      url_digest='requested-page', focused_text_digest='empty', focused_text_length=0)
TYPED = replace(FIELD, focused_text_digest='hello', focused_text_length=5, text_matches=True)


class ContinuityTests(unittest.TestCase):
    def run_plan(self, actions, states):
        calls = []
        def invoke(name, **kwargs):
            calls.append(name)
            return {'status': 'requested'}
        registry = Registry({name: (lambda n=name, **kw: invoke(n, **kw)) for name in SPECS})
        router, _ = make_router(registry=registry, guard=Mock())
        router.confirmations.confirm.return_value = True
        observer = Mock()
        observer.observe.side_effect = states
        result = Executor(router, observer, verification_seconds=0).execute(Plan('Task', tuple(actions)))
        return calls, result, router

    def test_page_switch_in_same_window_blocks_typing(self):
        for changed in (replace(FIELD, url_digest='other-page'), replace(FIELD, url_digest=None)):
            with self.subTest(url=changed.url_digest):
                calls, result, router = self.run_plan(
                    [Action('focus_app', {'app_name': 'chrome'}), Action('type_text', {'text': 'hello'})],
                    [FIELD, FIELD, FIELD, changed])
                self.assertEqual(calls, ['focus_app'])
                self.assertEqual(result[-1].status, 'BLOCKED')
                router.confirmations.confirm.assert_not_called()

    def test_read_only_step_preserves_submission_content_binding(self):
        calls, result, _ = self.run_plan(
            [Action('type_text', {'text': 'hello'}), Action('get_current_time', {}),
             Action('press_key', {'key': 'enter'})],
            [FIELD, FIELD, TYPED, TYPED, TYPED, replace(TYPED, focused_text_digest='changed')])
        self.assertEqual(calls, ['type_text', 'get_current_time'])
        self.assertEqual(result[-1].status, 'BLOCKED')

    def test_identical_text_in_different_field_cannot_be_submitted(self):
        calls, result, _ = self.run_plan(
            [Action('type_text', {'text': 'hello'}), Action('press_key', {'key': 'enter'})],
            [FIELD, FIELD, TYPED, replace(TYPED, focused_id=(2,))])
        self.assertEqual(calls, ['type_text'])
        self.assertEqual(result[-1].status, 'BLOCKED')

    def test_read_only_step_allows_unchanged_submission(self):
        calls, result, router = self.run_plan(
            [Action('type_text', {'text': 'hello'}), Action('get_current_time', {}),
             Action('press_key', {'key': 'enter'})],
            [FIELD, FIELD, TYPED, TYPED, TYPED, TYPED, FIELD])
        self.assertEqual(calls, ['type_text', 'get_current_time', 'press_key'])
        self.assertEqual(result[-1].status, 'SUCCESS')
        self.assertEqual(router.confirmations.confirm.call_count, 2)


class VerificationIdentityTests(unittest.TestCase):
    def test_typing_requires_observed_nonempty_field_identity(self):
        for before, after in ((replace(FIELD, available=False), TYPED),
                              (replace(FIELD, focused_id=()), replace(TYPED, focused_id=())),
                              (FIELD, replace(TYPED, url_digest='other-page')),
                              (FIELD, replace(TYPED, url_digest=None))):
            with self.subTest(before=before, after=after):
                result = Verifier().verify(Action('type_text', {'text': 'hello'}),
                                           Result('type_text', 'SUCCESS'), before, after)
                self.assertNotEqual(result.status, VerificationStatus.VERIFIED)

    def test_focus_cannot_verify_in_another_window_or_page(self):
        for after in (replace(FIELD, active_window=WindowState(8, 'chrome')),
                      replace(FIELD, url_digest='other-page')):
            result = Verifier().verify(Action('focus_text_field', {}),
                                       Result('focus_text_field', 'SUCCESS', {'runtime_id': (1,)}), FIELD, after)
            self.assertNotEqual(result.status, VerificationStatus.VERIFIED)

    def test_enter_with_unknown_field_identity_is_uncertain(self):
        result = Verifier().verify(Action('press_key', {'key': 'enter'}), Result('press_key', 'SUCCESS'),
                                   replace(TYPED, focused_id=()), replace(FIELD, focused_id=()))
        self.assertEqual(result.status, VerificationStatus.UNCERTAIN)
