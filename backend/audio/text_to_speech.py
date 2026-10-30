import threading
import time

class Speaker:
    def __init__(self, enabled=False, cooldown_ms=500):
        self.enabled = enabled
        self.active = threading.Event()
        self.until = 0
        self.cooldown = cooldown_ms / 1000
        self.generation = 0

    @property
    def suppressed(self):
        return self.active.is_set() or time.monotonic() < self.until

    def say(self, text):
        if not self.enabled: return
        self.generation += 1
        self.active.set()
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        finally:
            self.until = time.monotonic() + self.cooldown
            self.active.clear()

    def say_status(self, status):
        if status in {"done", "blocked", "stopped"}: self.say(status)
