"""UI Automation window operations with exact, unambiguous targets."""
import ctypes
import time
from ctypes import wintypes

from utils.helpers import SafeError


def canonical_top_level_handle(handle, user=None):
    """Normalize only through the Win32 owner tree; never by process identity."""
    if not handle:
        return handle
    user = user or ctypes.WinDLL("user32")
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    root = user.GetAncestor(handle, 2)  # GA_ROOT
    return root or handle


def trace_foreground(stage, **values):
    """Opt-in structural HWND diagnostics; titles and UI content are forbidden."""
    import os
    if os.getenv('VOXPILOT_FOREGROUND_DIAGNOSTICS', 'false').lower() != 'true':
        return
    safe = ' '.join(f'{key}={values[key]}' for key in sorted(values))
    print(f'[FOREGROUND] stage={stage} {safe}')


class Windows:
    def __init__(self, apps):
        self.apps = apps

    def desktop(self):
        from pywinauto import Desktop
        return Desktop(backend="uia")

    def executable(self, pid):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise SafeError("Cannot verify target process")
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                raise SafeError("Cannot verify target process")
            return buffer.value
        finally:
            kernel.CloseHandle(handle)

    def app_of(self, window):
        return self.apps.identify(self.executable(window.process_id()))

    def active(self):
        user = ctypes.WinDLL("user32")
        user.GetForegroundWindow.restype = wintypes.HWND
        raw_handle = user.GetForegroundWindow()
        if not raw_handle:
            raise SafeError("No foreground window")
        handle = canonical_top_level_handle(raw_handle, user)
        trace_foreground('windows.active', raw_handle=raw_handle, top_level_handle=handle)
        return self.desktop().window(handle=handle).wrapper_object()

    def approved_windows(self):
        result = []
        for window in self.desktop().windows():
            try:
                app = self.app_of(window)
                if app == 'explorer' and window.class_name() != 'CabinetWClass':
                    continue
                if window.is_visible():
                    result.append(window)
            except (SafeError, RuntimeError):
                continue
        return result

    def select(self, app_name=None, window_name=None):
        found = [w for w in self.approved_windows() if
                 (app_name is not None and self.app_of(w) == app_name) or
                 (window_name is not None and w.window_text().casefold() == window_name.casefold())]
        if len(found) != 1:
            raise SafeError("Window missing or ambiguous; leave exactly one matching window")
        return found[0]


    def apply(self, operation, app_name=None, window_name=None):
        window = self.select(app_name, window_name)

        if operation == "focus":
            user = ctypes.WinDLL("user32")
            user.IsIconic.argtypes = [wintypes.HWND]

            if user.IsIconic(window.handle):
                window.restore()

            # Request focus only once
            window.set_focus()

            # Allow Windows a short time to settle
            deadline = time.monotonic() + 0.3

            while True:
                try:
                    active_window = self.active()

                except SafeError as exc:
                    # Retry only when no foreground window exists
                    if str(exc) != "No foreground window":
                        raise

                    if time.monotonic() >= deadline:
                        raise SafeError(
                            "Windows refused foreground focus"
                        ) from None

                    time.sleep(0.02)
                    continue

                # Never accept a different foreground window
                if active_window.handle != window.handle:
                    raise SafeError(
                        "Windows refused foreground focus"
                    )

                break

        elif operation == "close":
            if (
                getattr(self, "expected_close", window.handle)
                != window.handle
            ):
                raise SafeError(
                    "Window changed during confirmation"
                )

            window.close()

        elif operation == "minimize":
            window.minimize()

        elif operation == "maximize":
            window.maximize()

        return {"status": "requested"}


    def get_active_window(self):
        w = self.active()
        return {"handle": w.handle, "app": self.app_of(w), "title": w.window_text()}

    def list_windows(self):
        return [{"handle": w.handle, "app": self.app_of(w), "title": w.window_text()} for w in self.approved_windows()]
