import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from agent.computer_state import ComputerState, Element, WindowState
from safety.emergency_stop import EmergencyStop
from tools.window_metadata import supported_top_level
from utils.helpers import SafeError
from vision.grounding import ground
from vision.observer import Observer, _scoped_windows, collect
from vision.semantic import ScreenState


def _window_state(handle=7, app='explorer'):
    window = WindowState(handle, app, False, False, 'File Explorer')
    return ComputerState(window, (window,), available=True, elements_complete=False)


def test_shell_surfaces_are_not_file_explorer_windows():
    assert supported_top_level('explorer', 'CabinetWClass')
    assert not supported_top_level('explorer', 'Shell_TrayWnd')
    assert not supported_top_level('explorer', 'Progman')
    assert supported_top_level('notepad', 'Notepad')


def test_scoped_observation_preserves_multiple_genuine_explorer_windows():
    first = WindowState(7, 'explorer', False, False, 'Downloads')
    second = WindowState(8, 'explorer', False, False, 'Documents')
    state = ComputerState(first, (first, second), available=True, elements_complete=False)
    wrapper = Mock(handle=7)
    wrapper.class_name.return_value = 'CabinetWClass'
    windows = Mock()
    windows.apps.allowlist = {'explorer': {'allowed': True}}
    windows.desktop.return_value.window.return_value.wrapper_object.return_value = wrapper
    windows.app_of.return_value = 'explorer'
    with patch('tools.window_metadata.collect_metadata', return_value=state):
        observed, active = _scoped_windows(windows)
    assert observed.open_windows == (first, second)
    assert active is wrapper
    windows.desktop.return_value.window.assert_called_once_with(handle=7)


def test_foreground_identity_change_is_rejected():
    windows = Mock()
    windows.apps.allowlist = {'explorer': {'allowed': True}}
    windows.desktop.return_value.window.return_value.wrapper_object.return_value = Mock(handle=99)
    with patch('tools.window_metadata.collect_metadata', return_value=_window_state()):
        state, active = _scoped_windows(windows)
    assert not state.available
    assert active is None


def _accessible_fixture(children=()):
    root = Mock(handle=7)
    root.process_id.return_value = 22
    root.window_text.return_value = 'File Explorer'
    root.children.return_value = list(children)
    root.class_name.return_value = 'CabinetWClass'
    info = NS(visible=True, enabled=True, process_id=22, runtime_id=(1,),
              control_type='TabItem', name='Home', automation_id='home',
              rectangle=NS(left=0, top=0, right=50, bottom=20),
              element=NS(CurrentIsPassword=False))
    root.element_info = info
    windows = Mock()
    windows.apps = Mock(spec=[])
    windows.approved_windows.return_value = [root]
    windows.active.return_value = root
    windows.app_of.return_value = 'explorer'
    windows.executable.return_value = r'C:\Windows\explorer.exe'
    user = Mock()
    user.IsIconic.return_value = False
    user.IsZoomed.return_value = False
    focused = NS(runtime_id=(99,))
    modules = {
        'pywinauto.uia_defines': Mock(IUIA=Mock(return_value=NS(iuia=NS(GetFocusedElement=Mock(return_value=focused))))),
        'pywinauto.uia_element_info': Mock(UIAElementInfo=lambda value: value),
    }
    return windows, user, modules


def test_uia_observation_succeeds_for_mocked_accessible_window():
    windows, user, modules = _accessible_fixture()
    with patch('ctypes.WinDLL', return_value=user), patch.dict(sys.modules, modules):
        state = collect(windows)
    assert state.available and state.elements_complete
    assert [(e.name, e.type) for e in state.elements] == [('Home', 'TabItem')]


def test_incomplete_tree_is_reported_without_selecting_target():
    overflowing = [Mock() for _ in range(200)]
    windows, user, modules = _accessible_fixture(overflowing)
    with patch('ctypes.WinDLL', return_value=user), patch.dict(sys.modules, modules):
        state = collect(windows)
    assert state.available and not state.elements_complete
    assert ground(ScreenState.from_computer(state), 'the Home tab').selected is None


def test_missing_and_ambiguous_controls_fail_safely():
    window = WindowState(7, 'explorer')
    home = Element((1,), 'TabItem', 'Home')
    missing = ScreenState.from_computer(ComputerState(window, (window,), (), available=True))
    assert ground(missing, 'the Home tab').selected is None
    duplicate = ScreenState.from_computer(ComputerState(window, (window,), (home, home), available=True))
    result = ground(duplicate, 'the Home tab')
    assert result.selected is None and len(result.candidates) == 2


def test_stale_reference_is_rejected():
    window = WindowState(7, 'explorer')
    state = ComputerState(window, (window,), (Element((1,), 'TabItem', 'Home'),), available=True)
    screen = ScreenState.from_computer(state)
    reference = screen.elements[0]
    changed = ScreenState.from_computer(ComputerState(window, (window,), (Element((2,), 'TabItem', 'Home'),), available=True))
    with pytest.raises(SafeError, match='STALE_ELEMENT'):
        changed.resolve(reference)


def test_timeout_terminates_and_joins_worker():
    process = Mock(pid=123)
    process.is_alive.side_effect = [True, True, False, False]
    reader, writer = Mock(), Mock()
    reader.poll.return_value = False
    ctx = Mock()
    ctx.Pipe.return_value = (reader, writer)
    ctx.Process.return_value = process
    clock = [0.0]
    def monotonic():
        clock[0] += 0.06
        return clock[0]
    with patch('vision.observer.multiprocessing.get_context', return_value=ctx), \
         patch('vision.observer.time.monotonic', side_effect=monotonic):
        state = Observer({}, EmergencyStop(), timeout=.1).observe()
    assert not state.available
    process.terminate.assert_called_once()
    assert process.join.call_count >= 1


def test_downloads_navigation_still_uses_verified_uia_item():
    from tools.explorer_navigation import ExplorerNavigation
    target = Mock()
    target.element_info.control_type = 'TreeItem'
    target.window_text.return_value = 'Downloads (pinned)'
    target.is_visible.return_value = True
    target.is_enabled.return_value = True
    address = Mock()
    address.element_info.control_type = 'ToolBar'
    address.window_text.return_value = 'Address: Downloads'
    window = Mock(handle=7)
    window.class_name.return_value = 'CabinetWClass'
    window.is_visible.return_value = True
    window.descendants.side_effect = [[target], [target], [address]]
    desktop = Mock()
    desktop.windows.return_value = [window]
    desktop.window.return_value.wrapper_object.return_value = window
    navigation = ExplorerNavigation({'explorer': {'allowed': True, 'executable': r'C:\Windows\explorer.exe'}},
                                    EmergencyStop())
    user = Mock()
    user.GetForegroundWindow.return_value = 7
    with patch.object(navigation.windows, 'app_of', return_value='explorer'), \
         patch('tools.explorer_navigation.Desktop', return_value=desktop), \
         patch('tools.explorer_navigation.ctypes.windll.user32', user):
        result = navigation.navigate('downloads')
    assert result['status'] == 'address_observed'
    target.double_click_input.assert_called_once()


def test_approved_windows_filters_explorer_shell_surfaces():
    from tools.window_tools import Windows
    cabinet = Mock()
    cabinet.is_visible.return_value = True
    cabinet.class_name.return_value = 'CabinetWClass'
    taskbar = Mock()
    taskbar.is_visible.return_value = True
    taskbar.class_name.return_value = 'Shell_TrayWnd'
    windows = Windows(Mock())
    windows.desktop = Mock(return_value=Mock(windows=Mock(return_value=[cabinet, taskbar])))
    windows.app_of = Mock(return_value='explorer')
    assert windows.approved_windows() == [cabinet]


def test_approved_windows_keeps_multiple_genuine_explorer_windows():
    from tools.window_tools import Windows
    cabinets = [Mock(), Mock()]
    for cabinet in cabinets:
        cabinet.is_visible.return_value = True
        cabinet.class_name.return_value = 'CabinetWClass'
    windows = Windows(Mock())
    windows.desktop = Mock(return_value=Mock(windows=Mock(return_value=cabinets)))
    windows.app_of = Mock(return_value='explorer')
    assert windows.approved_windows() == cabinets


def test_observer_default_deadline_is_bounded_for_measured_uia_startup_and_state_patterns():
    observer = Observer({}, EmergencyStop())
    assert observer.timeout == 7
