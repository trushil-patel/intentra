
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from audio.speech_to_text import SpeechToText
from utils.helpers import SafeError


def sample_audio():
    """One second of valid test samples, not real speech."""
    return np.full(16000, 0.1, dtype=np.float32)


def fake_parakeet(text):
    """Mock the Sherpa-ONNX offline recognizer interface."""
    stream = Mock()
    stream.result = SimpleNamespace(text=text)

    model = Mock()
    model.create_stream.return_value = stream
    return model, stream


class TranscriptionTests(unittest.TestCase):

    def test_local_result(self):
        model, stream = fake_parakeet(" Open Chrome ")
        audio = sample_audio()

        stt = SpeechToText(model=model)

        self.assertEqual(
            stt.transcribe(audio),
            "Open Chrome"
        )

        model.create_stream.assert_called_once()
        model.decode_stream.assert_called_once_with(stream)

        args = stream.accept_waveform.call_args.args
        self.assertEqual(args[0], 16000)
        np.testing.assert_array_equal(args[1], audio)

        self.assertTrue(stt.last_metrics["accepted"])

    def test_no_speech(self):
        model, _ = fake_parakeet("")

        stt = SpeechToText(model=model)

        with self.assertRaises(SafeError):
            stt.transcribe(sample_audio())

        self.assertFalse(stt.last_metrics["accepted"])

    def test_invalid_audio(self):
        model, _ = fake_parakeet("Open Chrome")

        stt = SpeechToText(model=model)

        with self.assertRaises(SafeError):
            stt.transcribe(None)

        model.decode_stream.assert_not_called()

    def test_digital_silence(self):
        model, _ = fake_parakeet("Open Chrome")

        stt = SpeechToText(model=model)

        silence = np.zeros(16000, dtype=np.float32)

        with self.assertRaises(SafeError):
            stt.transcribe(silence)

        model.decode_stream.assert_not_called()

    def test_warmup(self):
        model, stream = fake_parakeet("Unexpected command")

        stt = SpeechToText(model=model)

        stt.warmup()
        stt.warmup()

        model.decode_stream.assert_called_once_with(stream)

        self.assertTrue(stt._warmed)
        self.assertEqual(stt.last_metrics, {})

    def test_failed_warmup(self):
        model, _ = fake_parakeet("")
        model.decode_stream.side_effect = RuntimeError("Decoder failed")

        stt = SpeechToText(model=model)

        with self.assertRaises(SafeError):
            stt.warmup()

        self.assertFalse(stt._warmed)


if __name__ == "__main__":
    unittest.main()
