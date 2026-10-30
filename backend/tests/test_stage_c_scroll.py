"""Foreground Chrome scroll authorization without global wheel injection."""
import ctypes
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from agent.schemas import Action
from safety.emergency_stop import EmergencyStop
from safety.ui_guard import UIGuard
from tools.scroll_target import scroll
from utils.helpers import SafeError


def node(kind, name, pid=11, identity=(1,)):
    info = NS(control_type=kind, name=name, process_id=pid, runtime_id=identity,
        visible=True, enabled=True, element=NS(CurrentIsPassword=False),
        rectangle=NS(left=0, top=0, right=800, bottom=600))
    wrapper = Mock(element_info=info)
    wrapper.children.return_value = []
    return wrapper


@pytest.fixture
def page(monkeypatch):
    root = node('Window', 'Example page')
    root.handle = 7; root.process_id.return_value = 11
    root.window_text.return_value = 'Example page - Chrome'
    root.is_visible.return_value = root.is_enabled.return_value = True
    address = node('Edit', 'Address and search bar')
    address.iface_value.CurrentValue = 'https://example.org/'
    document = node('Document', 'Example page', 22, (2, 3))
    pattern = NS(CurrentVerticallyScrollable=True, CurrentVerticalScrollPercent=50)
    def move(horizontal, vertical):
        assert horizontal == 2
        pattern.CurrentVerticalScrollPercent += -1 if vertical == 1 else 1
    pattern.Scroll = Mock(side_effect=move)
    document.iface_scroll = pattern
    toolbar = node('Button', 'Back'); toolbar.element_info.enabled = False
    root.children.return_value = [toolbar, address, document]
    windows = Mock()
    windows.active.return_value = root; windows.app_of.return_value = 'chrome'
    windows.executable.return_value = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    windows.apps.identify.return_value = 'chrome'
    focus = node('Pane', 'Web content', 22).element_info
    monkeypatch.setattr('tools.scroll_target.focused_info', lambda: focus)
    user = Mock(); user.GetAsyncKeyState.return_value = 0
    monkeypatch.setattr(ctypes, 'WinDLL', Mock(return_value=user))
    guard = UIGuard(windows)
    return NS(root=root, address=address, doc=document, pattern=pattern, focus=focus,
        windows=windows, guard=guard, user=user)


@pytest.mark.parametrize('direction,expected', [('down', 52), ('up', 48)])
def test_chrome_document_scroll_without_editable_focus(page, direction, expected):
    action = Action('scroll', {'direction': direction, 'amount': 2})
    target = page.guard.snapshot(action)
    assert target.process == 11 and target.owner_process == 22 and target.pointer is None
    page.guard.arm(action, target)
    result = scroll(page.windows, page.guard, EmergencyStop(), direction, 2)
    assert result['after_percent'] == expected and result['before_percent'] == 50
    page.windows.desktop.assert_not_called()
    page.doc.children.assert_not_called()


@pytest.mark.parametrize('failure', ['unknown', 'system', 'password', 'foreign_process', 'missing_pattern',
    'hidden', 'disabled', 'ambiguous', 'internal_url', 'modal', 'modifier', 'unreadable', 'no_document'])
def test_unverifiable_or_protected_scroll_denied(page, failure):
    if failure == 'unknown': page.windows.app_of.return_value = None
    elif failure == 'system': page.root.window_text.return_value = 'Windows Security credentials'
    elif failure == 'password': page.focus.element.CurrentIsPassword = True
    elif failure == 'foreign_process': page.windows.executable.side_effect = lambda pid: 'chrome.exe' if pid == 11 else 'other.exe'
    elif failure == 'missing_pattern': page.doc.iface_scroll = None
    elif failure == 'hidden': page.doc.element_info.visible = False
    elif failure == 'disabled': page.doc.element_info.enabled = False
    elif failure == 'ambiguous': page.root.children.return_value.append(node('Document', 'Other', 22))
    elif failure == 'internal_url': page.address.iface_value.CurrentValue = 'chrome://settings'
    elif failure == 'modal': page.root.children.return_value.append(node('Window', 'Dialog'))
    elif failure == 'modifier': page.user.GetAsyncKeyState.return_value = 0x8000
    elif failure == 'unreadable': page.root.children.side_effect = RuntimeError('UIA failed')
    elif failure == 'no_document': page.root.children.return_value = [page.address]
    with pytest.raises(SafeError): page.guard.snapshot(Action('scroll', {'direction': 'down', 'amount': 1}))
    page.pattern.Scroll.assert_not_called()


@pytest.mark.parametrize('change', ['window', 'url', 'runtime_id', 'renderer', 'stop'])
def test_stale_scroll_binding_blocks_before_input(page, change):
    action = Action('scroll', {'direction': 'down', 'amount': 1})
    page.guard.arm(action, page.guard.snapshot(action))
    stop = EmergencyStop()
    if change == 'window': page.root.handle = 8
    elif change == 'url': page.address.iface_value.CurrentValue = 'https://example.org/other'
    elif change == 'runtime_id': page.doc.element_info.runtime_id = (99,)
    elif change == 'renderer': page.doc.element_info.process_id = 33
    else: stop.stop()
    with pytest.raises(SafeError): scroll(page.windows, page.guard, stop, 'down', 1)
    page.pattern.Scroll.assert_not_called()


def test_scroll_requires_action_bound_authorization(page):
    with pytest.raises(SafeError): scroll(page.windows, page.guard, EmergencyStop(), 'down', 1)
    page.pattern.Scroll.assert_not_called()


@pytest.mark.parametrize('kind,pid', [('Pane', 11), ('Edit', 22)])
def test_typing_editable_and_same_process_requirements_unchanged(page, monkeypatch, kind, pid):
    info = node(kind, 'Content', pid).element_info
    monkeypatch.setitem(sys.modules, 'pywinauto.uia_defines', Mock())
    monkeypatch.setitem(sys.modules, 'pywinauto.uia_element_info', Mock(UIAElementInfo=Mock(return_value=info)))
    monkeypatch.setitem(sys.modules, 'pyautogui', Mock())
    with pytest.raises(SafeError): page.guard.snapshot(Action('type_text', {'text': 'literal'}))


@pytest.mark.parametrize('direction,after_percent,changed,expected', [
    ('down', 51, False, 'VERIFIED'), ('up', 49, False, 'VERIFIED'),
    ('down', 50, False, 'UNCERTAIN'), ('down', 49, False, 'UNCERTAIN'),
    ('down', float('nan'), False, 'UNCERTAIN'), ('down', 51, True, 'UNCERTAIN')])
def test_scroll_verification_requires_bound_directional_evidence(direction, after_percent, changed, expected):
    from agent.computer_state import ComputerState, WindowState
    from agent.verifier import Verifier
    from vision.observer import url_digest
    digest = url_digest('https://example.org/')
    before = ComputerState(active_window=WindowState(7, 'chrome'), available=True, url_digest=digest)
    after = ComputerState(active_window=WindowState(8 if changed else 7, 'chrome'), available=True, url_digest=digest)
    result = NS(status='SUCCESS', detail=dict(status='scroll_observed', handle=7, url_digest=digest,
        runtime_id=(2,3), before_percent=50, after_percent=after_percent))
    assert Verifier().verify(Action('scroll', {'direction': direction, 'amount': 1}), result, before, after).status == expected
