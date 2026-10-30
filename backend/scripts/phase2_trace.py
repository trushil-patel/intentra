"""Read-only telemetry around the existing runtime; wrappers never authorize actions."""
from contextlib import ExitStack, contextmanager
import re
import time
from unittest.mock import patch
from scripts.phase2_uia import state_summary


def reason_code(message):
    if not isinstance(message, str): return None
    for needle, code in [('ambiguous', 'AMBIGUOUS_TARGET'), ('changed', 'TARGET_CHANGED'),
                         ('timeout', 'TIMEOUT'), ('expired', 'CONFIRMATION_EXPIRED'),
                         ('Confirmation denied', 'CONFIRMATION_DENIED'), ('unsupported', 'UNSUPPORTED'),
                         ('Automated input is unavailable', 'UNSUPPORTED'), ('not approved', 'UNAPPROVED_TARGET'), ('could not be verified', 'UNVERIFIED')]:
        if needle.casefold() in message.casefold(): return code
    return 'RUNTIME_REFUSAL' if message else None


class Trace:
    def __init__(self):
        self.steps = []
        self.current = None
        self.terminal = 'NOT_RUN'
        self.reason = None
        self.emergency = None

    @contextmanager
    def installed(self):
        from agent.executor import Executor
        from agent.router import Router
        from agent.verifier import Verifier
        from safety.confirmation import ConfirmationManager
        from agent.runtime import Runtime
        execute, dispatch, verify = Executor.execute, Router.execute, Verifier.verify
        confirm, emergency = ConfirmationManager.confirm, Runtime.emergency_stop
        trace = self
        def execution(obj, plan, *args, **kwargs):
            target = None
            trace.current = None
            trace.steps = []
            for action in plan.actions:
                target = action.arguments.get('app_name', target)
                trace.steps.append(dict(planned_action=action.tool, target=target or 'verified_foreground',
                    authorization='NOT_REACHED', execution='NOT_RUN', verification='NOT_RUN', attempts=[]))
            status = obj.router.status
            step_started = None
            def observed_status(value):
                nonlocal step_started
                match = re.fullmatch(r'\[STEP (\d+)/(\d+)\] \w+', value)
                if match:
                    if trace.current is not None and step_started is not None:
                        trace.current['total_ms'] = round((time.monotonic()-step_started)*1000, 2)
                    trace.current = trace.steps[int(match[1])-1]
                    step_started = time.monotonic()
                return status(value)
            obj.router.status = observed_status
            try:
                results = execute(obj, plan, *args, **kwargs)
                trace.terminal = results[-1].status if results else 'NOT_RUN'
                trace.reason = reason_code(results[-1].detail) if results and trace.terminal != 'SUCCESS' else None
                if trace.current is not None and trace.reason:
                    trace.current['failure_reason'] = trace.reason
                return results
            finally:
                if trace.current is not None and step_started is not None:
                    trace.current['total_ms'] = round((time.monotonic()-step_started)*1000, 2)
                obj.router.status = status
        def dispatched(obj, plan, *args, **kwargs):
            start = time.monotonic()
            result = dispatch(obj, plan, *args, **kwargs)
            if trace.current is not None:
                status = result[-1].status if result else 'ERROR'
                trace.current['execution'] = status
                trace.current['attempts'].append(dict(tool=plan.actions[0].tool, status=status,
                    dispatch_ms=round((time.monotonic()-start)*1000, 2)))
                if status == 'SUCCESS' and trace.current['authorization'] == 'NOT_REACHED':
                    trace.current['authorization'] = 'ROUTER_POLICY_ALLOWED'
                if status != 'SUCCESS': trace.current['failure_reason'] = reason_code(result[-1].detail) if result else 'EMPTY_RESULT'
            return result
        def confirmed(obj, *args, **kwargs):
            start = time.monotonic()
            confirmation_started = obj.clock()
            if trace.current is not None: trace.current['authorization'] = 'PENDING'
            result = confirm(obj, *args, **kwargs)
            if trace.current is not None:
                trace.current.update(authorization='APPROVED' if result is True else 'DENIED_OR_EXPIRED',
                                     confirmation_ms=round((time.monotonic()-start)*1000, 2),
                                     confirmation_expired=obj.clock()-confirmation_started >= obj.seconds)
            return result
        def verified(obj, action, result, before, after):
            start = time.monotonic()
            outcome = verify(obj, action, result, before, after)
            if trace.current is not None:
                trace.current.update(verification=outcome.status.value, before=state_summary(before), after=state_summary(after),
                    verification_ms=round((time.monotonic()-start)*1000, 2))
                if action.tool == 'open_url':
                    from vision.observer import url_digest
                    trace.current['url_matches_requested'] = after.url_digest == url_digest(action.arguments['url'])
                if 'app_name' in action.arguments:
                    trace.current['app_matches_requested'] = after.current_application == action.arguments['app_name']
            return outcome
        def stopped(obj):
            from safety.confirmation import Pending
            stage = ('confirmation' if isinstance(obj.brain.router.confirmations.pending, Pending) else
                     'transcription' if obj.transcribing.locked() else 'recording' if obj.recorder.stream is not None else
                     'task' if obj.busy.locked() else 'idle')
            result = emergency(obj)
            trace.emergency = dict(stage=stage, closed=obj.closed.is_set(), latched=obj.emergency_latched.is_set())
            return result
        with ExitStack() as stack:
            for cls, name, replacement in [(Executor,'execute',execution),(Router,'execute',dispatched),
                    (Verifier,'verify',verified),(ConfirmationManager,'confirm',confirmed),(Runtime,'emergency_stop',stopped)]:
                stack.enter_context(patch.object(cls, name, replacement))
            yield self
