from config.app_catalog import BROWSERS
"""Observe/action/verify coordinator. The V1 Router remains the sole executor."""
import time
from agent.schemas import Action, Plan
from agent.router import Result
from agent.state_machine import TaskStatus, TERMINAL
from agent.verifier import Verifier, VerificationStatus
from agent.recovery import Recovery
from memory.task_memory import TaskMemory
from utils.helpers import SafeError, Cancelled


class Executor:
    def __init__(self, router, observer, verifier=None, recovery=None, verification_seconds=4, limits=None):
        self.router, self.observer = router, observer
        self.verifier, self.recovery = verifier or Verifier(), recovery or Recovery()
        self.verification_seconds = verification_seconds
        self.memory = TaskMemory()
        from config.settings import AgentLimits
        self.limits = limits or AgentLimits()
        self.started = time.monotonic()

    def prepare(self, plan=None):
        if hasattr(self.router.registry, 'clear_task'):
            self.router.registry.clear_task()
        self.started = time.monotonic()
        if hasattr(self.router.stop, 'begin_task'):
            self.router.stop.begin_task(self.limits.task_seconds)
        self.memory.clear()
        self.memory = TaskMemory()
        self.memory.machine.move(TaskStatus.PLANNING)
        if plan is not None:
            import hashlib
            self.memory.workflow.intent_digest = hashlib.sha256(
                plan.goal.encode('utf-8')).hexdigest()
            self.memory.workflow.total_steps = len(plan.actions)
            self.memory.workflow.deadline = self.started + self.limits.task_seconds
        self.router.stop.check()
        from vision.engine import ObservationEngine
        if plan is not None and isinstance(self.observer, ObservationEngine):
            # Deterministic plans need no pre-planning screen context. The first
            # action still gets a fresh observation before policy and execution.
            return {}
        self.memory.computer = self._observe(plan.actions[0] if plan and plan.actions else None)
        return self.memory.computer.planner_context()

    def _observe(self, action=None, expected_text=None, target=None):
        from vision.engine import ObservationEngine, ObservationLevel
        if isinstance(self.observer, ObservationEngine):
            metadata = action is not None and action.tool in {
                'open_app', 'focus_app', 'focus_window', 'minimize_app', 'maximize_app',
                'get_current_time', 'list_windows', 'get_active_window', 'wait_accessibility_ready'}
            return self.observer.observe(ObservationLevel.METADATA if metadata else ObservationLevel.ACCESSIBILITY,
                                         expected_text=expected_text, target=target)
        return self.observer.observe(expected_text=expected_text) if expected_text is not None else self.observer.observe()

    def execute(self, plan, from_model=False, needs_prompt=False):
        memory, stop, results = self.memory, self.router.stop, []
        try:
            if memory.machine.status != TaskStatus.PLANNING:
                self.prepare()
                memory = self.memory
            self.router.audit.task_id = memory.task_id
            self.router.audit.action_id = None
            # Validate the COMPLETE initial plan before any action or recovery.
            actions = self.router.preflight(plan)
            self.router.status('[PLAN] ' + ' -> '.join(a.tool + '(' + self.router.registry.spec(a.tool).risk.value + ')' for a in actions))
            expected_target = None
            expected_text_digest = None
            expected_field = None
            expected_url = None
            action_sequence = 0
            for index, original in enumerate(actions):
                memory.workflow.current_step = index + 1
                self.router.status(f"[STEP {index + 1}/{len(actions)}] {original.tool}")
                action, retries = original, 0
                while True:
                    stop.check()
                    if action_sequence >= self.limits.task_actions:
                        raise SafeError('Task action budget exhausted')
                    if time.monotonic() - self.started >= self.limits.task_seconds:
                        raise SafeError('Task duration budget exhausted')
                    memory.machine.move(TaskStatus.OBSERVING)
                    before = self._observe(action)
                    memory.computer = before
                    if any(e.modal for e in before.elements):
                        raise SafeError('Unexpected dialog; inspect it manually before continuing')
                    from safety.policy import INTERACTIVE
                    if expected_target is not None and action.tool in INTERACTIVE | {"scroll"}:
                        if not before.active_window or (before.active_window.handle, before.current_application) != expected_target:
                            raise SafeError("Task target changed; return to the intended application and repeat")
                    if expected_url is not None and action.tool in INTERACTIVE | {"scroll"}:
                        if not before.available or before.url_digest != expected_url:
                            raise SafeError("Task page changed or cannot be verified; return to the intended page and repeat")
                    if (expected_text_digest is not None and action.tool == "press_key"
                            and action.arguments.get("key") == "enter"
                            and (before.focused_text_digest != expected_text_digest
                                 or not before.active_window
                                 or (before.active_window.handle, before.focused_id) != expected_field)):
                        raise SafeError("Text or field changed after typing; review it and submit manually")
                    # Deterministic replanning never introduces a new goal or tool authority.
                    if action.tool == 'open_app' and before.available:
                        app_name = action.arguments['app_name']

                        if app_name != 'explorer':
                            matches = [
                                w for w in before.open_windows
                                if w.app == app_name
                            ]

                            if len(matches) == 1:
                                action = Action('focus_app', dict(action.arguments))

                            elif len(matches) > 1:
                                raise SafeError(
                                    'Application windows are ambiguous; select one manually'
                                )
                    stop.check()
                    memory.machine.move(TaskStatus.EXECUTING)
                    self.router.audit.action_id = action_sequence
                    action_sequence += 1
                    if (action.tool == 'press_key' and action.arguments.get('key') == 'enter'
                            and getattr(getattr(self.router, 'task_scope', None), 'transfer', False)):
                        self.router.registry.response_baseline = before
                    started = time.monotonic()
                    if hasattr(self.observer, 'invalidate'): self.observer.invalidate()
                    batch = self.router.execute(Plan('Validated task action', (action,)), from_model=from_model,
                                                lifecycle=memory.machine.move, announce=False)
                    stop.check()
                    result = batch[-1]
                    if result.status != 'SUCCESS':
                        memory.record(action.tool, result.status, 'NOT_RUN')
                        results.extend(batch)
                        memory.machine.move(TaskStatus.BLOCKED if result.status == 'BLOCKED' else TaskStatus.FAILED)
                        return results
                    memory.machine.move(TaskStatus.OBSERVING)
                    deadline = time.monotonic() + self.verification_seconds
                    while True:
                        stop.check()
                        expected_text = action.arguments.get('text') if action.tool == 'type_text' else None
                        if action.tool == 'write_task_text':
                            content = getattr(self.router.registry, 'task_content', None)
                            expected_text = content.text.replace('\r\n', '\n').replace('\r', '\n') if content else None
                        verification_target = (result.detail if action.tool in {
                            'click_element', 'interact_element', 'focus_element',
                            'type_url', 'navigate_url', 'edit_notepad'}
                            and isinstance(result.detail, dict) else None)
                        if action.tool == 'find_element':
                            from vision.semantic import ElementReference, element_identity
                            reference = result.detail
                            if isinstance(reference, ElementReference):
                                element = reference.element
                                verification_target = {
                                    'handle': reference.handle,
                                    'app': reference.app,
                                    'type': element.type,
                                    'name': element.name,
                                    'bounds': element.bounds,
                                    'automation_id': element.automation_id,
                                    'runtime_id': element.runtime_id,
                                    'scope': element.scope,
                                    'target_identity': element_identity(element),
                                    'operation': 'discover',
                                    'control_type': element.type,
                                }
                        after = self._observe(action, expected_text, verification_target)
                        memory.computer = after
                        verification = self.verifier.verify(action, result, before, after)
                        if verification.status == VerificationStatus.VERIFIED or time.monotonic() >= deadline:
                            break
                        stop.check()
                        stop.wait(0.05)
                    stop.check()
                    memory.machine.move(TaskStatus.VERIFYING)
                    memory.record(action.tool, result.status, verification.status.value)
                    self.router.audit.task_event(memory.task_id, action_sequence - 1, action.tool,
                                                 self.router.registry.spec(action.tool).risk.value,
                                                 verification.status.value, time.monotonic() - started)
                    if verification.status == VerificationStatus.VERIFIED:
                        memory.workflow.last_verified_result = verification.evidence
                        if after.active_window:
                            memory.workflow.expected_application = after.current_application
                            memory.workflow.window_handle = after.active_window.handle
                        if (action.tool in {'focus_element', 'type_url'}
                                and after.target_verification_complete
                                and len(after.elements) == 1):
                            from vision.semantic import ScreenState
                            fresh = ScreenState.from_computer(after).elements[0]
                            if hasattr(self.router.registry, 'element_reference'):
                                self.router.registry.element_reference = fresh
                            memory.workflow.last_target_id = fresh.id
                        if (action.tool == 'press_key' and action.arguments.get('key') == 'enter'
                                and getattr(getattr(self.router, 'task_scope', None), 'transfer', False)):
                            expected_url = after.url_digest
                        # Read-only actions must not erase the pending submission binding.
                        if action.tool == "type_text":
                            expected_text_digest = after.focused_text_digest
                            expected_field = (after.active_window.handle, after.focused_id)
                            expected_target = (after.active_window.handle, after.current_application)
                            expected_url = after.url_digest if after.current_application in BROWSERS else None
                        if action.tool in {"open_app", "focus_app", "focus_window", "open_url", "search_web"}:
                            if after.active_window:
                                expected_target = (after.active_window.handle, after.current_application)
                                expected_url = after.url_digest if after.current_application in BROWSERS else None
                            else:
                                raise SafeError("Task application is not active; focus it manually")
                        if action.tool == 'summarize_page':
                            import json
                            from utils.logger import redact
                            self.router.status('[UNTRUSTED SUMMARY] ' + json.dumps(redact(result.detail.text)))
                        results.append(Result(action.tool, 'SUCCESS', verification.evidence))
                        break
                    memory.machine.move(TaskStatus.RECOVERING)
                    stop.check()
                    next_action = self.recovery.choose(action, after, retries)
                    if (action.tool == 'open_app' and isinstance(result.detail, dict)
                            and result.detail.get('status') == 'launch_requested'
                            and not any(w.app == action.arguments['app_name'] for w in after.open_windows)):
                        next_action = None
                        self.router.status('[VERIFY] Launch accepted, but application window is not verified; not launching again.')
                    if next_action is None:
                        state = 'UNCERTAIN' if verification.status == VerificationStatus.UNCERTAIN else 'FAILED'
                        results.append(Result(action.tool, state, verification.evidence))
                        memory.machine.move(TaskStatus.BLOCKED if state == 'UNCERTAIN' else TaskStatus.FAILED)
                        self.router.status('[' + state + '] ' + verification.evidence
                                           + ': expected result could not be verified; please inspect the target.')
                        return results
                    retries += 1
                    action = next_action
            stop.check()
            if needs_prompt:
                memory.machine.move(TaskStatus.BLOCKED)
                message = "Claude navigation verified. What exact prompt should I enter? Say: Open Claude.ai and type, followed by your prompt."
                results.append(Result("type_text", "INPUT_REQUIRED", message))
                self.router.status("[INPUT REQUIRED] " + message)
                return results
            memory.machine.move(TaskStatus.COMPLETED)
            self.router.status('[DONE] Task verified.')
            return results
        except Exception as exc:
            memory = self.memory
            cancelled = isinstance(exc, Cancelled) or stop.stopped
            memory.workflow.cancelled = cancelled
            state = TaskStatus.CANCELLED if cancelled else TaskStatus.BLOCKED if isinstance(exc, SafeError) else TaskStatus.FAILED
            if memory.machine.status not in TERMINAL: memory.machine.move(state)
            if not isinstance(exc, SafeError): stop.stop()
            message = str(exc) if isinstance(exc, SafeError) else 'Task stopped safely'
            results.append(Result('unrecognized', state.value, message))
            try: self.router.audit.event('unrecognized', 'HIGH', 'CANCELLED' if cancelled else 'BLOCKED' if isinstance(exc, SafeError) else 'ERROR')
            except Exception: stop.stop()
            self.router.status('[' + state.value + '] ' + message)
            return results
        finally:
            if hasattr(self.observer, 'invalidate'): self.observer.invalidate()
            if hasattr(stop, 'end_task'): stop.end_task()
            if hasattr(self.router.registry, 'clear_task'):
                self.router.registry.clear_task()
            from agent.task_scope import TaskScope
            self.router.task_scope = TaskScope()
            self.router.audit.task_id = None
            self.router.audit.action_id = None
