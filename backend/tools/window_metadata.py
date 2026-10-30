"""Window metadata without importing UIA or traversing accessibility trees."""
import ctypes
import hashlib
import os
import time
from ctypes import wintypes
from pathlib import Path
from utils.helpers import SafeError
from agent.computer_state import ComputerState, WindowState, ForegroundOwner
from tools.app_tools import Apps
from tools.window_tools import Windows, canonical_top_level_handle, trace_foreground


def _metadata_identifier(allowlist):
    """Resolve trusted candidates once for this read-only observation only.

    Native action guards continue to use fresh Apps identity checks. Nothing is
    cached across observations, and duplicate identities retain catalog order.
    """
    apps = Apps(allowlist)
    identities = {}
    for app, entry in allowlist.items():
        if entry.get('allowed') is not True:
            continue
        try:
            for candidate in apps.candidates(app):
                identities.setdefault(candidate.resolve(), app)
        except SafeError:
            continue
    # Retain the existing WindowsApps Notepad rule without rescanning the full
    # discovered catalog for each unknown executable.
    notepad = Apps({'notepad': allowlist['notepad']} if 'notepad' in allowlist else {})
    def identify(executable):
        actual = Path(executable).resolve()
        if actual in identities:
            return identities[actual]
        return notepad.identify(executable)
    return identify


def supported_top_level(app, class_name):
    """Explorer identity alone does not make a shell surface a file window."""
    return app != 'explorer' or class_name == 'CabinetWClass'



def _foreground_owner(user, windows, identify, raw_handle, top_level_handle, context=None):
    """Capture ephemeral foreground ownership without retaining executable paths."""
    context = context or {}
    captured = time.monotonic_ns()
    capture_pid = os.getpid()
    pid = None
    executable_name = None
    executable_digest = None
    application = None
    window_class = None
    visible = None
    owner_handle = None
    root_owner_handle = None
    try:
        value = wintypes.DWORD()
        user.GetWindowThreadProcessId(top_level_handle, ctypes.byref(value))
        pid = value.value or None
        visible = bool(user.IsWindowVisible(top_level_handle))
        owner_handle = user.GetWindow(top_level_handle, 4) or None  # GW_OWNER
        root_owner_handle = user.GetAncestor(top_level_handle, 3) or None  # GA_ROOTOWNER
        class_name = ctypes.create_unicode_buffer(257)
        if user.GetClassNameW(top_level_handle, class_name, 257):
            window_class = class_name.value or None
        if pid:
            executable = Path(windows.executable(pid)).resolve()
            executable_name = executable.name.casefold() or None
            executable_digest = hashlib.sha256(str(executable).casefold().encode()).hexdigest()
            try:
                application = identify(executable)
            except Exception:
                application = None
    except Exception:
        pass
    if pid == context.get('intentra_pid'):
        category = 'intentra'
    elif pid == context.get('parent_terminal_pid'):
        category = 'parent_terminal'
    elif executable_name in {'windowsterminal.exe', 'wt.exe', 'conhost.exe',
                             'powershell.exe', 'pwsh.exe', 'cmd.exe'}:
        category = 'terminal'
    elif application == 'explorer' or executable_name == 'explorer.exe':
        category = 'file_explorer' if window_class == 'CabinetWClass' else 'explorer_shell'
    elif application:
        category = 'approved_application'
    else:
        category = 'other'
    return ForegroundOwner(raw_handle, top_level_handle, pid, executable_name,
                           executable_digest, application, window_class, visible,
                           owner_handle, root_owner_handle, category, captured,
                           capture_pid)

def collect_metadata(allowlist, foreground_context=None):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    for name in ('IsWindowVisible', 'IsIconic', 'IsZoomed'):
        getattr(user, name).argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    windows = Windows(Apps(allowlist))
    identify = _metadata_identifier(allowlist)
    items = []
    raw_foreground = user.GetForegroundWindow()
    foreground = canonical_top_level_handle(raw_foreground, user)
    trace_foreground('metadata', raw_handle=raw_foreground, top_level_handle=foreground)
    owner = _foreground_owner(user, windows, identify, raw_foreground, foreground,
                              foreground_context)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def visit(handle, _):
        if not user.IsWindowVisible(handle): return True
        try:
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            app = identify(windows.executable(pid.value))
            if app == 'explorer':
                class_name = ctypes.create_unicode_buffer(257)
                if (not user.GetClassNameW(handle, class_name, 257)
                        or not supported_top_level(app, class_name.value)):
                    return True
            title = ctypes.create_unicode_buffer(513)
            user.GetWindowTextW(handle, title, 513)
            items.append(WindowState(handle, app, bool(user.IsIconic(handle)), bool(user.IsZoomed(handle)), title.value))
        except Exception:
            pass
        return len(items) <= 50
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    ok = user.EnumWindows(visit, 0)
    if not ok or len(items) > 50: return ComputerState()
    return ComputerState(next((w for w in items if w.handle == foreground), None), tuple(items),
                         available=True, elements_complete=False,
                         foreground_owner=owner)
