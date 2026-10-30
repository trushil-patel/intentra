"""Ephemeral semantic references. UI labels are data, never authority."""
from dataclasses import dataclass, field
import hashlib
import json
import time

from agent.computer_state import ComputerState, Element
from utils.helpers import SafeError


def element_identity(element: Element) -> str:
    payload = (element.type, element.name, element.bounds, element.automation_id,
               element.enabled, element.password)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


def fingerprint(state: ComputerState) -> str:
    payload = (state.active_window.handle if state.active_window else None,
               state.current_application, state.url_digest, state.available,
               state.elements_complete, state.focused_id,
               [(e.runtime_id, e.type, e.name, e.bounds, e.enabled, e.password,
                 e.read_only, e.selected, e.checked, e.expanded, e.modal, e.source,
                 e.content_digest, e.scope, e.keyboard_focusable) for e in state.elements],
               state.queried_roles, state.query_scope, state.focus_evidence,
               state.focused_element_runtime_id, state.focused_element_type,
               state.focused_element_automation_id,
               state.focused_element_provider_owned, state.focused_element_scope)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class ElementReference:
    id: str
    state_id: str
    handle: int
    app: str
    element: Element = field(repr=False)
    observed_at: float


@dataclass(frozen=True)
class ScreenState:
    computer: ComputerState = field(repr=False)
    state_id: str
    elements: tuple[ElementReference, ...]

    @classmethod
    def from_computer(cls, state: ComputerState):
        digest = fingerprint(state)
        refs = ()
        if state.available and state.active_window:
            refs = tuple(ElementReference(
                'element_' + digest[:16] + '_' + str(index), digest,
                state.active_window.handle, state.current_application, element, state.timestamp)
                for index, element in enumerate(state.elements))
        return cls(state, digest, refs)

    @property
    def has_modal(self) -> bool:
        return any(e.element.modal for e in self.elements)

    def resolve(self, reference: ElementReference, *, now=None, max_age=5.0,
                strict_state=True, allow_expired=False) -> ElementReference:
        now = time.monotonic() if now is None else now
        age = now - reference.observed_at
        if not self.computer.available or not self.computer.elements_complete:
            raise SafeError('STALE_ELEMENT: observation unavailable or incomplete')
        if self.has_modal:
            raise SafeError('STALE_ELEMENT: unexpected modal dialog')
        if not allow_expired and not 0 <= age <= max_age:
            raise SafeError('STALE_ELEMENT: reference age exceeded')
        if strict_state and reference.state_id != self.state_id:
            raise SafeError('STALE_ELEMENT: whole-state fingerprint changed')
        if (not self.computer.active_window
                or self.computer.active_window.handle != reference.handle
                or self.computer.current_application != reference.app):
            raise SafeError('STALE_ELEMENT: foreground window or application changed')
        if strict_state:
            found = [e for e in self.elements if e.id == reference.id and e.element == reference.element]
        elif reference.element.runtime_id:
            found = [e for e in self.elements if e.element.runtime_id == reference.element.runtime_id
                     and e.element.type == reference.element.type
                     and e.element.name == reference.element.name]
        else:
            expected = reference.element
            found = [e for e in self.elements
                     if (e.element.type, e.element.name, e.element.bounds, e.element.automation_id)
                     == (expected.type, expected.name, expected.bounds, expected.automation_id)]
        if len(found) != 1 or found[0].handle != reference.handle or found[0].app != reference.app:
            raise SafeError('STALE_ELEMENT: Target identity changed')
        return found[0]


class ObservationCache:
    """Optional read-only reuse; actions always invalidate and reobserve."""
    def __init__(self, ttl=0.5, clock=time.monotonic):
        self.ttl, self.clock, self.screen = ttl, clock, None

    def put(self, state: ComputerState) -> ScreenState:
        self.screen = ScreenState.from_computer(state)
        return self.screen

    def get(self) -> ScreenState | None:
        if self.screen and 0 <= self.clock() - self.screen.computer.timestamp <= self.ttl:
            return self.screen
        self.invalidate()
        return None

    def invalidate(self):
        self.screen = None
