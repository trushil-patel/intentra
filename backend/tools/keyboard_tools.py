from utils.helpers import SafeError
from safety.validator import safe_text

class Keyboard:
    def __init__(self, stop, guard, unicode_input=False):
        self.stop = stop
        self.guard = guard
        self.unicode_input = unicode_input

    def type_text(self, text):
        import pyautogui
        if type(text) is not str or not 1 <= len(text) <= 2000 or not safe_text(text):
            raise SafeError("Invalid or sensitive text input")
        if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
            raise SafeError("Unsupported input characters")
        if not self.unicode_input and not text.isascii():
            raise SafeError("V1 typing supports ASCII only")
        for char in text:
            self.stop.check()
            self.guard.verify_input()
            if char.isascii():
                pyautogui.write(char, interval=0.01)
            else:
                # Direct Unicode key events, never a key-sequence parser or clipboard.
                from pywinauto.keyboard import KeyAction
                pyautogui.failSafeCheck()
                encoded = char.encode("utf-16-le")
                for offset in range(0, len(encoded), 2):
                    self.stop.check()
                    KeyAction(chr(int.from_bytes(encoded[offset:offset + 2], "little"))).run()
        return {"status": "input_sent"}

    def write_task_text(self, slot):
        from memory.task_content import TaskContent
        from adapters.notepad import NotepadAdapter
        content = getattr(self.guard, 'task_content', None)
        if slot != 'response' or not isinstance(content, TaskContent):
            raise SafeError('No authorized task-scoped response')
        content = TaskContent.create(content.text, content.source, content.reference)
        import pyautogui
        from pywinauto.keyboard import KeyAction
        # Unlike type_text, literal transferred DATA may contain command-like prose.
        # It is confined to Notepad with a fresh target check for every character.
        for char in content.text.replace('\r\n', '\n').replace('\r', '\n'):
            self.stop.check()
            self.guard.verify_input()
            NotepadAdapter.require_document(self.guard.pending[1])
            if char == '\n': pyautogui.press('enter')
            elif char == '\t': pyautogui.press('tab')
            else:
                pyautogui.failSafeCheck()
                encoded = char.encode('utf-16-le')
                for offset in range(0, len(encoded), 2):
                    self.stop.check()
                    KeyAction(chr(int.from_bytes(encoded[offset:offset + 2], 'little'))).run()
        return {'status': 'input_sent'}

    def press_key(self, key):
        import pyautogui
        self.stop.check()
        self.guard.verify_input()
        pyautogui.press(key)
        return {"status": "input_sent"}

    def hotkey(self, keys):
        import pyautogui
        self.stop.check()
        self.guard.verify_input()
        if keys == ["ctrl", "v"]:
            # Never paste opaque clipboard data or execute hidden commands.
            raise SafeError("Clipboard paste is blocked in V1; use explicit Type text")
        pyautogui.hotkey(*keys)
        return {"status": "input_sent"}
