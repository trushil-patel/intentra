from utils.performance import timed
"""Finite, cancellable observation conditions. No executable expressions."""
from dataclasses import dataclass
from enum import StrEnum
import time
from config.settings import AgentLimits
from vision.semantic import ScreenState
from vision.state_diff import compare
from utils.helpers import SafeError


class ConditionKind(StrEnum):
    WINDOW_EXISTS = 'window_exists'
    WINDOW_CLOSED = 'window_closed'
    ACTIVE_WINDOW_IS = 'active_window_is'
    ELEMENT_EXISTS = 'element_exists'
    ELEMENT_DISAPPEARS = 'element_disappears'
    TEXT_APPEARS = 'text_appears'
    SCREEN_CHANGED = 'screen_changed'


@dataclass(frozen=True)
class Condition:
    kind: ConditionKind
    value: str = ''

    def __post_init__(self):
        if not isinstance(self.kind, ConditionKind) or type(self.value) is not str or len(self.value) > 200:
            raise SafeError('Invalid wait condition')


def satisfied(condition: Condition, before: ScreenState, now: ScreenState) -> bool:
    state = now.computer
    if not state.available or now.has_modal:
        return False
    kind, value = condition.kind, condition.value
    if kind == ConditionKind.WINDOW_EXISTS: return any(w.app == value for w in state.open_windows)
    if kind == ConditionKind.WINDOW_CLOSED:
        return any(w.app == value for w in before.computer.open_windows) and not any(w.app == value for w in state.open_windows)
    if kind == ConditionKind.ACTIVE_WINDOW_IS: return state.current_application == value
    if not state.elements_complete: return False
    matches = [e for e in state.elements if not e.password and e.name.casefold() == value.casefold()]
    if kind in {ConditionKind.ELEMENT_EXISTS, ConditionKind.TEXT_APPEARS}: return bool(matches)
    if kind == ConditionKind.ELEMENT_DISAPPEARS:
        return before.computer.elements_complete and any(e.name.casefold() == value.casefold() for e in before.computer.elements) and not matches
    if kind == ConditionKind.SCREEN_CHANGED: return compare(before, now).changed
    return False


class WaitManager:
    def __init__(self, observer, stop, limits=None, clock=time.monotonic, sleep=time.sleep):
        self.observer, self.stop = observer, stop
        self.limits = limits or AgentLimits()
        self.clock = clock
        self.sleep = stop.wait if sleep is time.sleep else sleep

    @timed("wait")
    def wait_for(self, condition: Condition, before: ScreenState, timeout: float) -> ScreenState:
        if type(timeout) not in {int, float} or not 0 < timeout <= self.limits.wait_seconds:
            raise SafeError('Wait timeout exceeds limit')
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            self.stop.check()
            current = ScreenState.from_computer(self.observer.observe())
            self.stop.check()
            if current.has_modal:
                raise SafeError('Unexpected dialog; inspect it manually')
            if self.clock() >= deadline: break
            if satisfied(condition, before, current): return current
            self.sleep(min(self.limits.poll_seconds, max(0, deadline - self.clock())))
        self.stop.check()
        raise SafeError('WAIT_TIMEOUT: Condition was not established')
