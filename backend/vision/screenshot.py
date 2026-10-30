from utils.performance import timed
"""Explicit local captures with private session directory and bounded retention."""
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
import re
from uuid import uuid4
import time
from utils.helpers import SafeError


class ScreenshotStore:
    def __init__(self, enabled=True, ttl=60):
        self.image = None
        self.enabled, self.ttl = enabled, ttl
        self._directory = None

    def cleanup(self):
        if self._directory:
            for path in Path(self._directory.name).glob('*.png'):
                if time.time() - path.stat().st_mtime >= self.ttl:
                    path.unlink(missing_ok=True)

    def clear(self):
        if self.image is not None:
            self.image.close()
            self.image = None
        if self._directory:
            self._directory.cleanup()
            self._directory = None

    def cleanup_stale(self):
        # Only our exact file namespace, never recursive deletion or linked directories.
        root = Path(gettempdir()).resolve()
        for folder in root.glob("voxpilot-screens-*"):
            try:
                if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
                    continue
                if folder.resolve().parent != root:
                    continue
                for path in folder.iterdir():
                    if (re.fullmatch(r"[a-f0-9]{32}\.png", path.name) and not path.is_symlink()
                            and path.is_file() and time.time() - path.stat().st_mtime >= self.ttl):
                        path.unlink(missing_ok=True)
                if not any(folder.iterdir()): folder.rmdir()
            except OSError:
                continue

    @timed("screenshot")
    def capture(self, persist=False):
        if not self.enabled:
            raise SafeError('Screenshot functionality is disabled')
        import pyautogui
        self.cleanup_stale()
        self.clear()
        self.image = pyautogui.screenshot()
        metadata = {'status': 'captured_locally', 'size': self.image.size}
        if persist:
            self._directory = TemporaryDirectory(prefix='voxpilot-screens-')
            path = Path(self._directory.name) / (uuid4().hex + '.png')
            self.image.save(path)
            metadata['reference'] = str(path)
        return metadata
