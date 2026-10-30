"""Focus settling reads state only; no repeated focus or weaker identity match."""
from unittest.mock import Mock
import pytest
from tools.window_tools import Windows
from utils.helpers import SafeError


@pytest.fixture
def window(monkeypatch):
    native=Mock(); native.IsIconic.return_value=False
    monkeypatch.setattr('tools.window_tools.ctypes.WinDLL',lambda *a,**kw:native)
    w=Windows(Mock()); target=Mock(handle=7); w.select=Mock(return_value=target)
    return w,target


def test_focus_waits_for_transient_no_foreground(window):
    w,target=window
    w.active=Mock(side_effect=[SafeError('No foreground window'),Mock(handle=7)])
    assert w.apply('focus',app_name='edge')=={'status':'requested'}
    target.set_focus.assert_called_once()


def test_focus_does_not_accept_a_different_window(window):
    w,target=window; w.active=Mock(return_value=Mock(handle=8))
    with pytest.raises(SafeError): w.apply('focus',app_name='edge')
    target.set_focus.assert_called_once()


def test_focus_never_claims_success_without_a_foreground_window(window):
    w,target=window; w.active=Mock(side_effect=SafeError('No foreground window'))
    with pytest.raises(SafeError): w.apply('focus',app_name='edge')
    target.set_focus.assert_called_once()
