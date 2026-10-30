from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from agent.computer_state import ComputerState, Element, WindowState
from agent.planner import deterministic_v2
from agent.schemas import Action
from agent.verifier import Verifier, VerificationStatus
from safety.confirmation import ConfirmationManager
from safety.emergency_stop import EmergencyStop
from tools.element_tools import ElementTools, FORBIDDEN_TARGET
from utils.helpers import Cancelled, SafeError
from vision.grounding import ground
from vision.semantic import ElementReference, ScreenState


def ref(element):
    return ElementReference('element_' + 'a' * 16 + '_0', 'state', 7, 'explorer', element, 1.0)


def tool(element, wrapper=None, stop=None):
    windows = Mock()
    active = Mock(handle=7)
    active.process_id.return_value = 22
    active.class_name.return_value = 'CabinetWClass'
    windows.active.return_value = active
    windows.app_of.return_value = 'explorer'
    windows.executable.return_value = r'C:\Windows\explorer.exe'
    instance = ElementTools(windows, stop or Mock(), Mock())
    wrapper = wrapper or Mock()
    instance._target = Mock(return_value=(ref(element), wrapper))
    return instance, wrapper


def state(elements, focused=()):
    window = WindowState(7, 'explorer')
    return ComputerState(window, (window,), tuple(elements), available=True,
                         elements_complete=True, focused_id=focused)


def test_unique_button_discovery():
    result = ground(ScreenState.from_computer(state((Element((1,), 'Button', 'Settings'),))), 'Settings button')
    assert result.selected.element.name == 'Settings'


def test_unique_tab_discovery():
    result = ground(ScreenState.from_computer(state((Element((1,), 'TabItem', 'View'),))), 'the View tab')
    assert result.selected.element.type == 'TabItem'


def test_tab_selection_uses_selection_pattern():
    item = Element((1,), 'TabItem', 'View')
    instance, wrapper = tool(item)
    detail = instance.interact_element('last', 'select')
    wrapper.iface_selection_item.Select.assert_called_once()
    assert detail['operation'] == 'select'


@pytest.mark.parametrize('current,operation,calls', [(1, 'check', 0), (0, 'check', 1), (0, 'uncheck', 0), (1, 'uncheck', 1)])
def test_checkbox_reaches_explicit_state_without_blind_toggle(current, operation, calls):
    instance, wrapper = tool(Element((1,), 'CheckBox', 'Remember me'))
    wrapper.iface_toggle.CurrentToggleState = current
    instance.interact_element('last', operation)
    assert wrapper.iface_toggle.Toggle.call_count == calls


@pytest.mark.parametrize('operation,method', [('expand', 'Expand'), ('collapse', 'Collapse')])
def test_tree_item_expand_and_collapse(operation, method):
    instance, wrapper = tool(Element((1,), 'TreeItem', 'Navigation'))
    instance.interact_element('last', operation)
    getattr(wrapper.iface_expand_collapse, method).assert_called_once()


def test_unsupported_pattern_fails_closed():
    instance, wrapper = tool(Element((1,), 'Button', 'Settings'))
    wrapper.iface_invoke.Invoke.side_effect = AttributeError()
    with pytest.raises(SafeError, match='pattern'):
        instance.interact_element('last', 'activate')


def test_ambiguous_identical_labels_are_not_selected():
    button = Element((1,), 'Button', 'Settings')
    result = ground(ScreenState.from_computer(state((button, replace(button, runtime_id=(2,))))), 'Settings button')
    assert result.selected is None and len(result.candidates) == 2


def test_disabled_and_password_controls_are_rejected():
    disabled = Element((1,), 'Button', 'Settings', enabled=False)
    password = Element((2,), 'Edit', '', password=True)
    screen = ScreenState.from_computer(state((disabled, password)))
    assert ground(screen, 'Settings button').selected is None
    assert ground(screen, 'password').selected is None


def test_stale_reference_is_rejected():
    before = ScreenState.from_computer(state((Element((1,), 'TabItem', 'View'),)))
    after = ScreenState.from_computer(state((Element((2,), 'TabItem', 'View'),)))
    with pytest.raises(SafeError, match='STALE_ELEMENT'):
        after.resolve(before.elements[0])


def test_foreground_change_invalidates_reference():
    before = ScreenState.from_computer(state((Element((1,), 'TabItem', 'View'),)))
    changed = replace(state((Element((1,), 'TabItem', 'View'),)), active_window=WindowState(8, 'explorer'))
    with pytest.raises(SafeError, match='STALE_ELEMENT'):
        ScreenState.from_computer(changed).resolve(before.elements[0])


def test_unexpected_modal_prevents_grounding():
    modal = Element((2,), 'Window', 'Confirm', modal=True)
    button = Element((1,), 'Button', 'Settings')
    assert ground(ScreenState.from_computer(state((button, modal))), 'Settings button').selected is None


def test_confirmation_rejection():
    manager = ConfirmationManager(seconds=1, key_reader=lambda: (False, True), sleep=lambda _: None)
    assert manager.confirm(Action('interact_element', {'element_id': 'last', 'operation': 'select'}), None, EmergencyStop()) is False


def test_cancellation_before_interaction():
    stop = Mock()
    stop.check.side_effect = Cancelled('cancelled')
    instance, wrapper = tool(Element((1,), 'Button', 'Settings'), stop=stop)
    with pytest.raises(Cancelled):
        instance.interact_element('last', 'activate')
    wrapper.iface_invoke.Invoke.assert_not_called()


def test_tab_verification_requires_selected_state():
    action = Action('interact_element', {'element_id': 'last', 'operation': 'select'})
    detail = {'runtime_id': (1,), 'operation': 'select', 'control_type': 'TabItem'}
    before = state((Element((1,), 'TabItem', 'View', selected=False),))
    after = state((Element((1,), 'TabItem', 'View', selected=False),))
    result = NS(status='SUCCESS', detail=detail)
    assert Verifier().verify(action, result, before, after).status == VerificationStatus.FAILED


def test_button_invocation_without_independent_evidence_is_uncertain():
    action = Action('click_element', {'element_id': 'last'})
    detail = {'runtime_id': (1,), 'operation': 'activate', 'control_type': 'Button'}
    before = state((Element((1,), 'Button', 'Settings'),))
    assert Verifier().verify(action, NS(status='SUCCESS', detail=detail), before, before).status == VerificationStatus.UNCERTAIN


def test_sensitive_control_is_blocked_by_existing_target_policy():
    assert FORBIDDEN_TARGET.search('Delete account button')
    assert FORBIDDEN_TARGET.search('Security settings')


def test_no_coordinate_fallback_for_invoke_failure():
    instance, wrapper = tool(Element((1,), 'Button', 'Settings'))
    wrapper.iface_invoke.Invoke.side_effect = RuntimeError()
    with patch.dict('sys.modules', {'pyautogui': Mock()}) as modules:
        with pytest.raises(SafeError):
            instance.interact_element('last', 'activate')
        assert not modules['pyautogui'].method_calls


def test_browser_and_explorer_references_are_isolated():
    element = Element((1,), 'TabItem', 'View')
    explorer = ScreenState.from_computer(state((element,))).elements[0]
    chrome_window = WindowState(7, 'chrome')
    chrome = ScreenState.from_computer(ComputerState(chrome_window, (chrome_window,), (element,), available=True))
    with pytest.raises(SafeError, match='STALE_ELEMENT'):
        chrome.resolve(explorer)


@pytest.mark.parametrize('command,tools,operation', [
    ('Click the View tab', ['find_element', 'click_element'], None),
    ('Select the Home tab', ['find_element', 'interact_element'], 'select'),
    ('Find the search box', ['find_element'], None),
    ('Focus the search field', ['find_element', 'focus_element'], None),
    ('Expand the navigation tree', ['find_element', 'interact_element'], 'expand'),
    ('Check the Remember me checkbox', ['find_element', 'interact_element'], 'check'),
])
def test_deterministic_component_planning(command, tools, operation):
    plan = deterministic_v2(command)
    assert [a.tool for a in plan.actions] == tools
    if operation:
        assert plan.actions[-1].arguments['operation'] == operation


def test_invalid_operation_is_not_dispatched():
    instance, wrapper = tool(Element((1,), 'TreeItem', 'Navigation'))
    with pytest.raises(SafeError, match='unsupported'):
        instance.interact_element('last', 'delete')
    assert not wrapper.method_calls


def test_downloads_navigation_regression_remains_present():
    from tools.registry import SPECS
    assert SPECS['navigate_explorer_folder'].arguments == {'folder': str}


def test_worker_timeout_cleanup_regression_is_retained():
    from tests.test_v2_native_privacy import ObserverTimeoutTests
    ObserverTimeoutTests('test_observer_timeout_returns_unknown_and_terminates').test_observer_timeout_returns_unknown_and_terminates()



def _windows_for_reference(reference):
    windows = Mock()
    active = Mock(handle=reference.handle)
    windows.active.return_value = active
    windows.app_of.return_value = reference.app
    return windows

def test_cross_com_runtime_change_requires_unique_exact_semantic_identity():
    from tools.element_tools import resolve_reference
    original = Element((1,), 'TabItem', 'View', bounds=(10, 10, 50, 30))
    observed_state = state((original,))
    reference = ScreenState.from_computer(observed_state).elements[0]
    wrapper = Mock()
    refreshed = replace(original, runtime_id=(99,))
    with patch('tools.element_tools.collect', return_value=observed_state), \
         patch('tools.element_tools.visible_wrappers', return_value=[(refreshed, wrapper)]):
        resolved, selected = resolve_reference(_windows_for_reference(reference), reference)
    assert resolved.element.runtime_id == (99,) and resolved.element.name == 'View' and selected is wrapper


def test_cross_com_semantic_fallback_rejects_duplicates():
    from tools.element_tools import resolve_reference
    original = Element((1,), 'TabItem', 'View', bounds=(10, 10, 50, 30))
    observed_state = state((original,))
    reference = ScreenState.from_computer(observed_state).elements[0]
    refreshed = replace(original, runtime_id=(99,))
    with patch('tools.element_tools.collect', return_value=observed_state), \
         patch('tools.element_tools.visible_wrappers', return_value=[(refreshed, Mock()), (replace(refreshed, runtime_id=(100,)), Mock())]):
        with pytest.raises(SafeError, match='ambiguous'):
            resolve_reference(_windows_for_reference(reference), reference)


def test_empty_runtime_id_uses_unique_exact_semantic_identity():
    from tools.element_tools import resolve_reference
    original = Element((), 'TabItem', 'View', bounds=(10, 10, 50, 30))
    observed_state = state((original,))
    reference = ScreenState.from_computer(observed_state).elements[0]
    wrapper = Mock()
    with patch('tools.element_tools.collect', return_value=observed_state), \
         patch('tools.element_tools.visible_wrappers', return_value=[(original, wrapper), (Element((), 'Button', 'Other'), Mock())]):
        assert resolve_reference(_windows_for_reference(reference), reference)[1] is wrapper


def test_confirmed_element_can_reground_after_reference_ttl():
    element = Element((), 'TabItem', 'View', bounds=(10, 10, 50, 30), automation_id='view')
    screen = ScreenState.from_computer(state((element,)))
    reference = screen.elements[0]
    refreshed = replace(screen.computer, timestamp=reference.observed_at + 50)
    current = ScreenState.from_computer(refreshed)
    assert current.resolve(reference, now=reference.observed_at + 50, max_age=45,
                           strict_state=False, allow_expired=True).element.name == 'View'


def test_unconfirmed_expired_reference_remains_blocked():
    element = Element((), 'TabItem', 'View', bounds=(10, 10, 50, 30))
    screen = ScreenState.from_computer(state((element,)))
    with pytest.raises(SafeError, match='reference age exceeded'):
        screen.resolve(screen.elements[0], now=screen.elements[0].observed_at + 46,
                       max_age=45, strict_state=False)


def test_confirmed_reground_survives_unrelated_whole_tree_change():
    target = Element((), 'TabItem', 'View', bounds=(10, 10, 50, 30), automation_id='view')
    original = ScreenState.from_computer(state((target,)))
    changed = ScreenState.from_computer(state((target, Element((2,), 'Button', 'Other'))))
    assert changed.resolve(original.elements[0], now=original.elements[0].observed_at + 10,
                           strict_state=False, allow_expired=True).element.name == 'View'


def test_confirmed_reground_rejects_material_identity_change():
    target = Element((), 'TabItem', 'View', bounds=(10, 10, 50, 30), automation_id='view')
    original = ScreenState.from_computer(state((target,)))
    changed = ScreenState.from_computer(state((replace(target, name='Share'),)))
    with pytest.raises(SafeError, match='Target identity changed'):
        changed.resolve(original.elements[0], strict_state=False, allow_expired=True)


def test_confirmed_reground_rejects_duplicate_exact_targets():
    target = Element((), 'TabItem', 'View', bounds=(10, 10, 50, 30), automation_id='view')
    original = ScreenState.from_computer(state((target,)))
    duplicate = ScreenState.from_computer(state((target, target)))
    with pytest.raises(SafeError, match='Target identity changed'):
        duplicate.resolve(original.elements[0], strict_state=False, allow_expired=True)


def test_selection_state_change_does_not_change_fundamental_identity():
    target = Element((), 'TabItem', 'View', bounds=(10, 10, 50, 30), automation_id='view', selected=False)
    original = ScreenState.from_computer(state((target,)))
    selected = ScreenState.from_computer(state((replace(target, selected=True),)))
    assert selected.resolve(original.elements[0], strict_state=False, allow_expired=True).element.selected


def test_native_recheck_uses_distinct_confirmed_operation():
    from tools.native import NativeBridge
    bridge = NativeBridge({}, EmergencyStop())
    bridge.call = Mock(return_value='bound')
    action = Action('click_element', {'element_id': 'last'})
    assert bridge.recheck(action, 'bound') is None
    bridge.call.assert_called_once_with('recheck', action, 'bound')


def test_cancelled_confirmation_never_rechecks_or_invokes():
    manager = ConfirmationManager(seconds=1, key_reader=lambda: (False, False), sleep=lambda _: (_ for _ in ()).throw(Cancelled('cancel')))
    with pytest.raises(Cancelled):
        manager.confirm(Action('click_element', {'element_id': 'last'}), object(), EmergencyStop())
