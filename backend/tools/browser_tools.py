import subprocess
from urllib.parse import quote

BROWSER_KEYS = {"new_tab": ("ctrl", "t"), "close_tab": ("ctrl", "w"), "refresh_page": ("ctrl", "r"),
                "browser_back": ("alt", "left"), "browser_forward": ("alt", "right"), "focus_address_bar": ("ctrl", "l")}
from safety.validator import normalize_url
from config.app_catalog import BROWSERS
import os

class Browser:
    def __init__(self, apps, stop=None, guard=None):
        self.apps, self.stop, self.guard = apps, stop, guard

    def open_url(self, url):
        # Fixed browser executable; never use shell associations or a command string.
        browser = os.environ.get('VOXPILOT_BROWSER', 'chrome').casefold()
        if browser not in BROWSERS:
            from utils.helpers import SafeError
            raise SafeError('VOXPILOT_BROWSER must be chrome, edge or firefox')
        if self.guard is not None:
            try:
                active = self.guard.windows.app_of(self.guard.windows.active())
                if active in BROWSERS:
                    browser = active
            except Exception:
                pass
        subprocess.Popen([str(self.apps.resolve(browser)), normalize_url(url)], shell=False)
        return {"status": "navigation_requested", "browser": browser}

    def shortcut(self, operation):
        import pyautogui
        self.stop.check()
        self.guard.verify_input()
        pyautogui.hotkey(*BROWSER_KEYS[operation])
        return {"status": "input_sent"}

    def search_web(self, query):
        return self.open_url("https://www.google.com/search?q=" + quote(query, safe=""))
