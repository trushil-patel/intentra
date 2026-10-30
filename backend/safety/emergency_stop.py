import threading
import time
from utils.helpers import Cancelled

class EmergencyStop:
    def __init__(self):
        self._event = threading.Event()
        self._deadline = None
        self.generation = 0
        self._lock = threading.Lock()

    def stop(self):
        with self._lock:
            self.generation += 1
            self._event.set()

    def check_generation(self, generation):
        self.check()
        if generation != self.generation:
            raise Cancelled('Discarded cancelled task result')

    def wait(self, seconds):
        self.check()
        self._event.wait(seconds)
        self.check()

    def check(self):
        if self._event.is_set():
            raise Cancelled("Emergency stop is latched; resume explicitly")

        if self._deadline is not None and time.monotonic() >= self._deadline:
            from utils.helpers import SafeError
            raise SafeError('Task duration budget exhausted')

    def begin_task(self, seconds):
        self._deadline = time.monotonic() + seconds

    def end_task(self):
        self._deadline = None

    @property
    def stopped(self):
        return self._event.is_set()

    def resume(self, expected_generation=None):
        with self._lock:
            if expected_generation is not None and expected_generation != self.generation:
                raise Cancelled('A newer emergency stop cannot be reset by old input')
            self._event.clear()
