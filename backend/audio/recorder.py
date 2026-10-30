import threading
from utils.helpers import SafeError

class Recorder:
    sample_rate = 16000

    def __init__(self, max_seconds=20, stream_factory=None):
        self.limit = max_seconds * self.sample_rate * 4
        self.factory = stream_factory
        self.buffer = bytearray()
        self.stream = None
        self.failed = False
        self.lock = threading.Lock()

    def _callback(self, data, frames, timing, status):
        with self.lock:
            if status or len(self.buffer) + len(data) > self.limit:
                self.failed = True
                return
            self.buffer.extend(data)

    def start(self):
        if self.stream is not None:
            raise SafeError("Recording already active")
        with self.lock:
            self.buffer.clear()
            self.failed = False
        if self.factory is None:
            import sounddevice
            factory = sounddevice.RawInputStream
        else:
            factory = self.factory
        try:
            self.stream = factory(samplerate=self.sample_rate, channels=1, dtype="float32", callback=self._callback)
            self.stream.start()
        except Exception:
            self.discard()
            raise SafeError("Microphone unavailable") from None

    def finish(self):
        try:
            if self.stream is None:
                raise SafeError("No recording active")
            self.stream.stop()
            self.stream.close()
            self.stream = None
            with self.lock:
                if self.failed:
                    raise SafeError("Recording exceeded its limit or microphone data was lost")
                if len(self.buffer) < self.sample_rate * 4 // 5:
                    raise SafeError("No usable speech recording")
                detached = bytes(self.buffer)
                self.buffer.clear()
                return detached
        finally:
            self.discard()

    def discard(self):
        if self.stream is not None:
            try: self.stream.abort()
            finally:
                self.stream.close()
                self.stream = None
        with self.lock:
            self.buffer.clear()
