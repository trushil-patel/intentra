import unittest
from audio.recorder import Recorder
from utils.helpers import SafeError

class FakeStream:
    def __init__(self, **kwargs): self.callback = kwargs["callback"]
    def start(self): self.callback(b"a" * 16000, 4000, None, False)
    def stop(self): pass
    def close(self): pass
    def abort(self): pass

class AudioTests(unittest.TestCase):
    def test_audio_cleared_after_success(self):
        recorder = Recorder(stream_factory=FakeStream)
        recorder.start()
        self.assertEqual(len(recorder.finish()), 16000)
        self.assertFalse(recorder.buffer)
        self.assertIsNone(recorder.stream)

    def test_overflow_cleared(self):
        recorder = Recorder(max_seconds=0, stream_factory=FakeStream)
        recorder.start()
        with self.assertRaises(SafeError): recorder.finish()
        self.assertFalse(recorder.buffer)
        self.assertIsNone(recorder.stream)

    def test_microphone_failure(self):
        def broken(**kwargs): raise RuntimeError("device error")
        recorder = Recorder(stream_factory=broken)
        with self.assertRaises(SafeError): recorder.start()
        self.assertFalse(recorder.buffer)
