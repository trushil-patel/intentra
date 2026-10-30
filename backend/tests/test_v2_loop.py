import unittest
from unittest.mock import Mock
from agent.computer_state import ComputerState, WindowState
from agent.executor import Executor
from agent.schemas import Plan, Action
from agent.router import Result
from agent.recovery import Recovery
from agent.verifier import Verifier, VerificationStatus as V
from agent.state_machine import TaskStatus
from agent.planner import deterministic_v2
from vision.observer import url_digest
from test_validator import make_router

EMPTY = ComputerState(available=True)
CHROME = ComputerState(WindowState(7, 'chrome'), (WindowState(7, 'chrome'),), available=True)

class LoopTests(unittest.TestCase):
    def executor(self, states):
        router, calls = make_router()
        observer = Mock()
        observer.observe.side_effect = states
        executor = Executor(router, observer, verification_seconds=0)
        return executor, router, calls

    def test_open_verified(self):
        ex, router, calls = self.executor([EMPTY, EMPTY, CHROME])
        result = ex.execute(Plan('x', (Action('open_app', {'app_name':'chrome'}),)))
        self.assertEqual(result[-1].status, 'SUCCESS')
        self.assertEqual(ex.memory.machine.status, TaskStatus.COMPLETED)
        self.assertEqual(calls, ['open_app'])

    def test_existing_app_focused_through_router(self):
        ex, router, calls = self.executor([CHROME, CHROME, CHROME])
        ex.execute(Plan('x', (Action('open_app', {'app_name':'chrome'}),)))
        self.assertEqual(calls, ['focus_app'])

    def test_recovery_limit(self):
        ex, router, calls = self.executor([EMPTY] * 7)
        result = ex.execute(Plan('x', (Action('open_app', {'app_name':'chrome'}),)))
        self.assertEqual(calls, ['open_app'] * 3)
        self.assertEqual(result[-1].status, 'FAILED')

    def test_recovery_then_success(self):
        ex, router, calls = self.executor([EMPTY, EMPTY, EMPTY, EMPTY, CHROME])
        result = ex.execute(Plan('x', (Action('open_app', {'app_name':'chrome'}),)))
        self.assertEqual(calls, ['open_app', 'open_app'])
        self.assertEqual(result[-1].status, 'SUCCESS')

    def test_unknown_never_success_or_next_action(self):
        ex, router, calls = self.executor([EMPTY, EMPTY, ComputerState()])
        result = ex.execute(Plan('x', (Action('open_app', {'app_name':'chrome'}), Action('get_current_time', {}))))
        self.assertEqual(calls, ['open_app'])
        self.assertEqual(result[-1].status, 'UNCERTAIN')

    def test_invalid_later_step_no_execution(self):
        ex, router, calls = self.executor([EMPTY])
        result = ex.execute(Plan('x', (Action('open_app', {'app_name':'chrome'}), Action('disable_safety', {}))))
        self.assertFalse(calls)
        self.assertEqual(result[-1].status, 'BLOCKED')

    def test_cancel_after_action(self):
        ex, router, calls = self.executor([EMPTY, EMPTY])
        router.registry._handlers = {'open_app': lambda **kwargs: router.stop.stop()}
        result = ex.execute(Plan('x', (Action('open_app', {'app_name':'chrome'}), Action('get_current_time', {}))))
        self.assertEqual(result[-1].status, 'CANCELLED')
        self.assertEqual(ex.memory.machine.status, TaskStatus.CANCELLED)

    def test_recovery_never_replays_input_or_submission(self):
        for tool, args in [('type_text', {'text':'hello'}), ('press_key', {'key':'enter'}), ('open_url', {'url':'claude.ai'})]:
            self.assertIsNone(Recovery().choose(Action(tool,args), CHROME, 0))
        with self.assertRaises(ValueError): Recovery(3)

    def test_wrong_url_does_not_verify(self):
        state = ComputerState(CHROME.active_window, CHROME.open_windows, available=True, url_digest=url_digest('https://example.com'))
        check = Verifier().verify(Action('open_url', {'url':'claude.ai'}), Result('open_url','SUCCESS'), CHROME, state)
        self.assertEqual(check.status, V.FAILED)

    def test_typing_changed_focus_does_not_verify(self):
        before = ComputerState(CHROME.active_window, available=True, focused_id=(1,))
        after = ComputerState(CHROME.active_window, available=True, focused_id=(2,), text_matches=True)
        check = Verifier().verify(Action('type_text', {'text':'hello'}), Result('type_text','SUCCESS'), before, after)
        self.assertEqual(check.status, V.FAILED)

    def test_successful_input_delivery_is_not_submission_proof(self):
        check = Verifier().verify(Action('press_key', {'key':'enter'}), Result('press_key','SUCCESS'), CHROME, CHROME)
        self.assertEqual(check.status, V.UNCERTAIN)

    def test_demo_parser_and_literal_text(self):
        plan = deterministic_v2('Open Chrome, go to Claude, type Explain WebRTC in simple words and press Enter')
        self.assertEqual([a.tool for a in plan.actions], ['open_app','open_url','focus_text_field','type_text','press_key'])
        self.assertEqual(deterministic_v2('Type hello and press Enter').actions[0].arguments['text'], 'hello and press Enter')
        self.assertEqual(deterministic_v2('Go to Claude.').actions[0].arguments['url'], 'https://claude.ai')
        for command in ('Launch Chrome', 'Start Chrome', 'Can you open Chrome?', 'Please open Google Chrome.'):
            self.assertEqual(deterministic_v2(command).actions[0].tool, 'open_app')

class MoreLoopSafetyTests(unittest.TestCase):
    def test_app_switch_between_steps_blocks_input(self):
        router, calls = make_router(guard=Mock())
        router.confirmations.confirm.return_value = True
        other = ComputerState(WindowState(8,'notepad'), (WindowState(8,'notepad'),), available=True)
        observer = Mock()
        observer.observe.side_effect = [EMPTY, EMPTY, CHROME, other]
        executor = Executor(router, observer, verification_seconds=0)
        result = executor.execute(Plan('x',(Action('open_app',{'app_name':'chrome'}),Action('type_text',{'text':'hello'}))))
        self.assertEqual(calls, ['open_app'])
        self.assertEqual(result[-1].status, 'BLOCKED')

    def test_observation_only_plan_cannot_register_or_change_policy(self):
        for tool in ('register_tool', 'set_permissions', 'disable_safety', 'run_shell'):
            router, calls = make_router()
            observer = Mock()
            observer.observe.return_value = EMPTY
            executor = Executor(router, observer, verification_seconds=0)
            result = executor.execute(Plan('x', (Action(tool, {}),)))
            self.assertEqual(result[-1].status, 'BLOCKED')
            self.assertFalse(calls)

    def test_oversized_plan_never_executes(self):
        router, calls = make_router()
        observer = Mock()
        observer.observe.return_value = EMPTY
        executor = Executor(router, observer, verification_seconds=0)
        result = executor.execute(Plan('x', (Action('get_current_time', {}),) * 11))
        self.assertFalse(calls)
        self.assertEqual(result[-1].status, 'BLOCKED')

    def test_model_confirmation_remains_action_specific(self):
        router, calls = make_router()
        router.confirmations.confirm.side_effect = [True, False]
        observer = Mock()
        observer.observe.return_value = CHROME
        executor = Executor(router, observer, verification_seconds=0)
        result = executor.execute(Plan('x', (Action('focus_app', {'app_name':'chrome'}), Action('focus_app', {'app_name':'chrome'}))), from_model=True)
        self.assertEqual(calls, ['focus_app'])
        self.assertEqual(router.confirmations.confirm.call_count, 2)
        self.assertEqual(result[-1].status, 'BLOCKED')

class TypingEvidenceTests(unittest.TestCase):
    def test_no_change_cannot_verify_typing(self):
        before = ComputerState(CHROME.active_window, available=True, focused_id=(1,), focused_text_digest='same')
        after = ComputerState(CHROME.active_window, available=True, focused_id=(1,), text_matches=True, focused_text_digest='same')
        result = Verifier().verify(Action('type_text', {'text':'hello'}), Result('type_text','SUCCESS'), before, after)
        self.assertNotEqual(result.status, V.VERIFIED)

    def test_changed_text_and_matching_suffix_verify(self):
        before = ComputerState(CHROME.active_window, available=True, focused_id=(1,), focused_text_digest='before')
        after = ComputerState(CHROME.active_window, available=True, focused_id=(1,), text_matches=True, focused_text_digest='after')
        result = Verifier().verify(Action('type_text', {'text':'hello'}), Result('type_text','SUCCESS'), before, after)
        self.assertEqual(result.status, V.VERIFIED)

class SubmissionLoopTests(unittest.TestCase):
    def test_text_changed_after_typing_blocks_enter(self):
        router, calls = make_router(guard=Mock())
        router.confirmations.confirm.return_value = True
        before = ComputerState(CHROME.active_window, available=True, focused_id=(1,), focused_text_digest='empty')
        typed = ComputerState(CHROME.active_window, available=True, focused_id=(1,), text_matches=True, focused_text_digest='hello')
        changed = ComputerState(CHROME.active_window, available=True, focused_id=(1,), focused_text_digest='injected')
        observer = Mock()
        observer.observe.side_effect = [before, before, typed, changed]
        executor = Executor(router, observer, verification_seconds=0)
        result = executor.execute(Plan('x', (Action('type_text',{'text':'hello'}), Action('press_key',{'key':'enter'}))))
        self.assertEqual(calls, ['type_text'])
        self.assertEqual(result[-1].status, 'BLOCKED')

class EndToEndDemoTests(unittest.TestCase):
    def run_demo(self, approve_submission):
        from agent.brain import Brain
        from agent.context import UserCommand
        from tools.registry import Registry, SPECS
        prompt = 'Explain WebRTC in simple words'
        field_id = (7,8)
        page = ComputerState(CHROME.active_window, CHROME.open_windows, available=True,
                             url_digest=url_digest('https://claude.ai'))
        field = ComputerState(CHROME.active_window, CHROME.open_windows, available=True, focused_id=field_id,
                              url_digest=page.url_digest, focused_text_length=0, focused_text_digest='empty')
        typed = ComputerState(CHROME.active_window, CHROME.open_windows, available=True, focused_id=field_id,
                              url_digest=page.url_digest, text_matches=True, focused_text_length=len(prompt), focused_text_digest='typed')
        calls = []
        def handler(name, **kwargs):
            calls.append(name)
            return {'runtime_id':field_id} if name == 'focus_text_field' else {'status':'requested'}
        registry = Registry({name: (lambda n=name, **kwargs: handler(n, **kwargs)) for name in SPECS})
        router, _ = make_router(registry=registry, guard=Mock())
        router.confirmations.confirm.side_effect = [True, True, approve_submission]
        observer = Mock()
        observer.observe.side_effect = [EMPTY, EMPTY, CHROME, CHROME, page, page, field, field, typed, typed, field]
        executor = Executor(router, observer, verification_seconds=0)
        provider = Mock()
        results = Brain(provider, router, router.stop, executor).handle(UserCommand(
            'Open Chrome, go to Claude, type Explain WebRTC in simple words and press Enter'))
        provider.generate_plan.assert_not_called()
        self.assertEqual(router.confirmations.confirm.call_count, 3)
        return calls, results, executor.memory.machine.status

    def test_complete_demo_observes_and_verifies_each_step(self):
        calls, results, status = self.run_demo(True)
        self.assertEqual(calls, ['open_app','open_url','focus_text_field','type_text','press_key'])
        self.assertTrue(all(r.status == 'SUCCESS' for r in results))
        self.assertEqual(status, TaskStatus.COMPLETED)

    def test_denied_submission_never_sends_enter(self):
        calls, results, status = self.run_demo(False)
        self.assertEqual(calls, ['open_app','open_url','focus_text_field','type_text'])
        self.assertEqual(results[-1].status, 'BLOCKED')
        self.assertEqual(status, TaskStatus.BLOCKED)

class ClaudeCommandRegressionTests(unittest.TestCase):
    def test_reported_command_includes_navigation_without_invented_text(self):
        from agent.planner import PROMPT_REQUIRED_GOAL
        plan = deterministic_v2('Open the Claude.ai and enter the prompt')
        self.assertEqual(plan.goal, PROMPT_REQUIRED_GOAL)
        self.assertEqual(plan.actions, (Action('open_url', {'url':'https://claude.ai'}),))

    def test_explicit_prompt_keeps_navigation_first(self):
        plan = deterministic_v2('Open the Claude.ai and enter Explain WebRTC in simple words')
        self.assertEqual([a.tool for a in plan.actions], ['open_url','focus_text_field','type_text'])
        self.assertEqual(plan.actions[-1].arguments['text'], 'Explain WebRTC in simple words')

    def test_missing_prompt_asks_after_verified_navigation_from_vscode(self):
        from agent.brain import Brain
        from agent.context import UserCommand
        from tools.registry import Registry, SPECS
        calls = []
        def handler(name, **kwargs):
            calls.append(name)
            return {'status':'requested'}
        registry = Registry({name: (lambda n=name, **kwargs: handler(n, **kwargs)) for name in SPECS})
        router, _ = make_router(registry=registry)
        router.status = Mock()
        vscode = ComputerState(WindowState(9, 'vscode'), available=True)
        page = ComputerState(CHROME.active_window, CHROME.open_windows, available=True,
                             url_digest=url_digest('https://claude.ai'))
        observer = Mock()
        observer.observe.side_effect = [vscode, vscode, page]
        executor = Executor(router, observer, verification_seconds=0)
        provider = Mock()
        results = Brain(provider, router, router.stop, executor).handle(
            UserCommand('Open the Claude.ai and enter the prompt'))
        self.assertEqual(calls, ['open_url'])
        self.assertEqual(results[-1].status, 'INPUT_REQUIRED')
        self.assertEqual(executor.memory.machine.status, TaskStatus.BLOCKED)
        provider.generate_plan.assert_not_called()
        self.assertFalse(any('[DONE]' in str(call) for call in router.status.call_args_list))

    def test_missing_prompt_does_not_claim_unverified_navigation(self):
        from agent.planner import PROMPT_REQUIRED_GOAL
        from tools.registry import Registry, SPECS
        registry = Registry({name: lambda **kwargs: {'status':'requested'} for name in SPECS})
        router, _ = make_router(registry=registry)
        observer = Mock()
        observer.observe.return_value = ComputerState()
        executor = Executor(router, observer, verification_seconds=0)
        results = executor.execute(Plan(PROMPT_REQUIRED_GOAL, (Action('open_url',{'url':'https://claude.ai'}),)), needs_prompt=True)
        self.assertEqual(results[-1].status, 'UNCERTAIN')
