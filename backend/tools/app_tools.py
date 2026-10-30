from utils.performance import timed
"""Executable paths come only from trusted Windows installation locations."""
import os
import subprocess
from pathlib import Path
from utils.helpers import SafeError

class Apps:
    def __init__(self, allowlist):
        self.allowlist = allowlist

    def candidates(self, app):
        if self.allowlist.get(app, {}).get("allowed") is not True:
            raise SafeError("Application is not approved")
        if 'executable' in self.allowlist[app]:
            from config.app_catalog import executable_path
            path = executable_path(self.allowlist[app]['executable'])
            if path is None: raise SafeError("Configured executable is unavailable")
            return [path]
        bases = [Path(os.environ.get(k, "C:/__missing__")) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        if app == "chrome":
            return [p / "Google/Chrome/Application/chrome.exe" for p in bases]
        if app == "edge":
            return [p / "Microsoft/Edge/Application/msedge.exe" for p in bases]
        if app == "firefox":
            return [p / "Mozilla Firefox/firefox.exe" for p in bases]
        if app == "vscode":
            return [bases[0] / "Microsoft VS Code/Code.exe", bases[2] / "Programs/Microsoft VS Code/Code.exe"]
        if app != "notepad": raise SafeError("Application needs an explicit executable")
        return [Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/notepad.exe"]

    def resolve(self, app):
        for path in self.candidates(app):
            if path.is_file():
                return path.resolve()
        raise SafeError("Approved application is not installed in a supported location")

    def identify(self, executable):
        actual = Path(executable).resolve()
        for app in self.allowlist:
            if self.allowlist[app].get("allowed") is True:
                try:
                    if any(actual == p.resolve() for p in self.candidates(app)):
                        return app
                except SafeError:
                    continue
        # Packaged Windows 11 Notepad is verified using its WindowsApps package path.
        parts = actual.parts
        windows_apps = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "WindowsApps"
        if self.allowlist.get("notepad", {}).get("allowed") is True and actual.is_relative_to(windows_apps):
            relative = actual.relative_to(windows_apps).parts
            if len(relative) == 3 and relative[0].startswith("Microsoft.WindowsNotepad_") and relative[1:] == ("Notepad", "Notepad.exe"):
                return "notepad"
        raise SafeError("Foreground application is not approved")

    @timed("app_launch")
    def open_app(self, app_name):
        subprocess.Popen([str(self.resolve(app_name))], shell=False)
        return {"status": "launch_requested", "app": app_name}
