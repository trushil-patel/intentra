
import ctypes
import time

from pywinauto import Desktop
from tools.app_tools import Apps
from tools.window_tools import Windows
from utils.helpers import SafeError


FOLDERS = {
    "downloads": ("Downloads (pinned)", "Address: Downloads"),
}


class ExplorerNavigation:
    def __init__(self, allowlist, stop):
        self.windows = Windows(Apps(allowlist))
        self.stop = stop

    def navigate(self, folder):
        self.stop.check()

        if folder not in FOLDERS:
            raise SafeError("Folder is not approved")

        target_name, expected_address = FOLDERS[folder]
        desktop = Desktop(backend="uia")

        # Genuine Explorer windows only; exclude desktop/taskbar.
        candidates = []
        for window in desktop.windows():
            self.stop.check()
            try:
                if (
                    window.class_name() == "CabinetWClass"
                    and window.is_visible()
                    and self.windows.app_of(window) == "explorer"
                ):
                    candidates.append(window)
            except Exception:
                continue

        if len(candidates) != 1:
            raise SafeError("Expected exactly one verified Explorer window")

        handle = candidates[0].handle

        def fresh_window():
            window = desktop.window(handle=handle).wrapper_object()
            if (
                window.class_name() != "CabinetWClass"
                or not window.is_visible()
                or self.windows.app_of(window) != "explorer"
            ):
                raise SafeError("Explorer window changed")
            return window

        def find_target(window):
            matches = [
                element
                for element in window.descendants()
                if element.element_info.control_type == "TreeItem"
                and element.window_text().strip() == target_name
                and element.is_visible()
                and element.is_enabled()
            ]
            if len(matches) != 1:
                raise SafeError("Explorer navigation item is missing or ambiguous")
            return matches[0]

        window = fresh_window()
        find_target(window)

        self.stop.check()
        window.set_focus()

        ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
        if ctypes.windll.user32.GetForegroundWindow() != handle:
            raise SafeError("Explorer is not the foreground window")

        window = fresh_window()
        target = find_target(window)

        self.stop.check()
        # Narrow exception: input is derived from a verified UIA item.
        # No arbitrary coordinates are accepted from the caller.
        target.double_click_input()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.stop.check()
            window = fresh_window()

            addresses = [
                element.window_text().strip()
                for element in window.descendants()
                if element.element_info.control_type == "ToolBar"
                and element.window_text().startswith("Address:")
            ]

            if expected_address in addresses:
                return {
                    "status": "address_observed",
                    "app": "explorer",
                    "handle": handle,
                    "folder": folder,
                    "address": expected_address,
                }

            self.stop.wait(0.05)

        raise SafeError("Explorer destination could not be observed")
