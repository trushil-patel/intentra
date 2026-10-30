"""Independent Windows key-monitor thread. No always-on microphone."""
import ctypes
from ctypes import wintypes
import threading
import time
from utils.helpers import SafeError

class KeyMonitor:
    def __init__(self, stop, on_down, on_up, on_resume=lambda: None, on_emergency=None):
        self.stop = stop
        self.on_emergency = on_emergency or stop.stop
        self.on_down, self.on_up, self.on_resume = on_down, on_up, on_resume
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.failed = False
        self.thread = threading.Thread(target=self._run, name="emergency-keys", daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(3) or self.failed:
            self.close()
            raise SafeError("Global emergency shortcut unavailable; startup denied")

    def _run(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        registered = False
        try:
            if not user.RegisterHotKey(None, 1, 0x4003, ord("X")):
                raise SafeError("Emergency shortcut already reserved")
            registered = True
            self.ready.set()
            held = False
            resume_held = False
            message = wintypes.MSG()
            while not self.closed.is_set():
                while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == 0x0312 and message.wParam == 1:
                        self.on_emergency()
                down = bool(user.GetAsyncKeyState(0x77) & 0x8000)
                if down and not held: self.on_down()
                if held and not down: self.on_up()
                held = down
                resume = all(user.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, ord("R")))
                if resume and not resume_held: self.on_resume()
                resume_held = resume
                self.closed.wait(0.01)
        except Exception:
            self.failed = True
            self.stop.stop()
        finally:
            self.ready.set()
            if registered: user.UnregisterHotKey(None, 1)

    def close(self):
        self.closed.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread(): self.thread.join(timeout=2)


class PushToTalkCapture:
    """One immutable capture context and one submission per key hold.

    VAD never endpoints this recorder. Key release is the only normal endpoint.
    """
    def __init__(self, recorder, submit, context, allowed, valid, display=print, clock=time.monotonic):
        self.recorder, self.submit, self.context = recorder, submit, context
        self.allowed, self.valid = allowed, valid
        self.display, self.clock = display, clock
        self.held = False
        self.started = None
        self.capture_context = None
        self.utterance_id = 0
        self.state = 'READY'

    def down(self, context=None, permitted=True):
        if self.held: return
        self.held = True
        self.capture_context = self.context() if context is None else context
        if (self.state != 'READY' or not permitted or not self.allowed() or not self.valid(self.capture_context)
                or (self.capture_context[2] and self.capture_context[1] is None)):
            self.display('[BUSY] Finish or cancel the current task before recording another command.')
            return
        self.utterance_id += 1
        try:
            self.recorder.start()
            self.started = self.clock()
            self.state = 'RECORDING'
            self.display('[RECORDING] Hold F8 and speak...')
        except SafeError:
            self.cancel()
            raise

    def up(self):
        if not self.held: return
        self.held = False
        if self.started is None: return
        context = self.capture_context
        self.started = None
        try:
            if not self.valid(context):
                self.recorder.discard()
                self.display('[IGNORED] Recording invalidated; please repeat.')
                return
            data = self.recorder.finish()
            from audio.segments import usable_audio
            usable_audio(data)
            self.state = 'TRANSCRIBING'
            self.display('[RELEASED] Processing speech...')
            self.submit(data, context)
        except SafeError as exc:
            self.display('[IGNORED] ' + str(exc))
        finally:
            self.capture_context = None
            if self.state != 'TRANSCRIBING': self.state = 'READY'

    def poll(self, maximum_seconds):
        if self.started is not None and (not self.valid(self.capture_context) or self.clock() - self.started >= maximum_seconds):
            self.cancel()
            self.display('[IGNORED] Recording cancelled or too long; release F8 and repeat.')

    def completed(self):
        if self.state == 'TRANSCRIBING': self.state = 'READY'

    def cancel(self):
        self.recorder.discard()
        self.started = None
        self.capture_context = None
        self.state = 'READY'
        # Keep held true until release: auto-repeat cannot restart this hold.
