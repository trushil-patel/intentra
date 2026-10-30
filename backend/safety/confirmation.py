import ctypes
import json
import time
import threading
from dataclasses import dataclass
from uuid import uuid4
from utils.logger import redact

@dataclass(frozen=True)
class Pending:
    nonce: str
    fingerprint: str
    expires: float

class ConfirmationManager:
    def __init__(self, seconds=30, clock=time.monotonic, key_reader=None, sleep=time.sleep, display=print):
        self.seconds, self.clock, self.sleep, self.display = seconds, clock, sleep, display
        self.key_reader = key_reader or self._windows_keys
        self.pending = None
        self._reply = None
        self._lock = threading.Lock()
        self.voice_prompt = None

    def cancel_pending(self):
        with self._lock:
            self.pending = None
            self._reply = None

    def respond(self, text, nonce):
        # Only Runtime supplies direct-user transcripts, bound at speech start.
        from agent.planner import normalized
        answer = normalized(text)
        if answer not in {'yes', 'no', 'confirm', 'deny'}: return False
        with self._lock:
            if not self.pending or nonce != self.pending.nonce or self.clock() >= self.pending.expires:
                return False
            if self._reply is not None: return False
            self._reply = (nonce, answer in {'yes', 'confirm'})
            return True

    @staticmethod
    def _windows_keys():
        user = ctypes.WinDLL("user32")
        return bool(user.GetAsyncKeyState(0x78) & 0x8000), bool(user.GetAsyncKeyState(0x79) & 0x8000)

    def confirm(self, action, target, stop):
        fingerprint = json.dumps({"tool": action.tool, "arguments": action.arguments, "target": str(target)}, sort_keys=True)
        request = Pending(uuid4().hex, fingerprint, self.clock() + self.seconds)
        with self._lock:
            self.pending = request
            self._reply = None
        if action.tool == "press_key" and action.arguments.get("key") == "enter":
            self.display("[CONFIRM] Enter may submit a message or form. Approval applies only to this field and its current content.")
        if action.tool == 'edit_notepad':
            operation = ('replace all text in the verified Notepad editor'
                         if action.arguments.get('mode') == 'replace'
                         else 'create text in the verified empty Notepad editor')
            shown = json.dumps({'application': 'notepad', 'operation': operation,
                                'data_changes': True})
        elif action.tool in {'type_url', 'navigate_url'}:
            shown = json.dumps({'application': 'browser',
                                'operation': ('enter an approved HTTPS destination'
                                              if action.tool == 'type_url'
                                              else 'navigate to the approved HTTPS destination'),
                                'data_changes': action.tool == 'type_url'})
        else:
            shown = redact(json.dumps({"tool": action.tool, "arguments": action.arguments}))
        self.display("[CONFIRM] " + shown +
                     f" | Say yes / no (optional F9 / F10) ({self.seconds}s). Keep the target focused. Never approve purchases, credentials or security changes.")
        released = False
        try:
            if self.voice_prompt: self.voice_prompt("Should I continue?")
            while self.clock() < request.expires:
                stop.check()
                with self._lock:
                    if self._reply is not None and self._reply[0] == request.nonce:
                        stop.check()
                        return self.pending is request and self.clock() < request.expires and self._reply[1]
                yes, no = self.key_reader()
                if not yes and not no: released = True
                if released and no: return False
                if released and yes:
                    stop.check()
                    return self.pending is request and self.clock() < request.expires
                self.sleep(0.02)
            return False
        finally:
            with self._lock:
                self.pending = None
                self._reply = None
