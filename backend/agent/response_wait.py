from utils.performance import timed
import time
from adapters.browser import BrowserAdapter
from config.settings import AgentLimits
from utils.helpers import SafeError


class ResponseWaiter:
    def __init__(self, observer, stop, clock=time.monotonic, sleep=time.sleep):
        self.observer, self.stop, self.clock = observer, stop, clock
        self.sleep = stop.wait if sleep is time.sleep else sleep

    @timed("response_wait")
    def wait(self, baseline, timeout):
        if type(timeout) is not int or not 1 <= timeout <= AgentLimits().wait_seconds:
            raise SafeError('Invalid response timeout')
        adapter = BrowserAdapter()
        if baseline is None or not adapter.is_claude(baseline) or not baseline.elements_complete or any(e.modal for e in baseline.elements):
            raise SafeError('No verified Claude submission context')
        try:
            previous = adapter.response(baseline).element.content_digest
            if previous is None: raise SafeError('Existing response content is unreadable')
        except SafeError:
            if any(e.name.casefold() in adapter.response_labels or e.automation_id == 'assistant-response' for e in baseline.elements):
                raise SafeError('Existing response baseline is ambiguous or unreadable') from None
            previous = None
        deadline, stable, last = self.clock() + timeout, 0, None
        while self.clock() < deadline:
            self.stop.check()
            state = self.observer.observe()
            self.stop.check()
            if self.clock() >= deadline: break
            if (not adapter.is_claude(state) or not state.active_window
                    or state.active_window.handle != baseline.active_window.handle
                    or any(e.modal for e in state.elements)):
                raise SafeError('Response target changed or a dialog appeared')
            try:
                reference = adapter.response(state)
                digest = reference.element.content_digest
            except SafeError:
                reference, digest = None, None
            if adapter.generating(state) or not digest or digest == previous:
                stable, last = 0, None
            elif digest == last:
                stable += 1
            else:
                stable, last = 1, digest
            # Stable content plus absence of generation is a UI heuristic, not server proof.
            if stable >= 3: return reference
            self.sleep(min(0.5, max(0, deadline - self.clock())))
        self.stop.check()
        raise SafeError('WAIT_TIMEOUT: A finished response could not be established')
