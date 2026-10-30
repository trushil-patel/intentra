
"""Intentra: offline NVIDIA Parakeet STT via Sherpa-ONNX."""

import os
import sys
import time
from pathlib import Path

import numpy as np

from audio.segments import audio_metrics
from config.settings import ROOT, Settings
from utils.helpers import SafeError


MODEL_DIR = Path(
    os.getenv(
        "INTENTRA_MODEL_DIR",
        str((Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(ROOT)) / "models" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"),
    )
).expanduser()


class SpeechToText:
    def __init__(self, model_name=None, model=None, settings=None):
        self.settings = settings or Settings()
        self.model_name = "parakeet-tdt-0.6b-v3-int8"
        self.model = model
        self.last_metrics = {}
        self.startup_metrics = {}
        self._warmed = False

    def configuration(self):
        return {
            "engine": "sherpa-onnx",
            "model": self.model_name,
            "device": "cpu",
            "threads": self.settings.whisper_cpu_threads,
            "cpu_threads": self.settings.whisper_cpu_threads,
            "num_workers": 1,
            "timeout_seconds": self.settings.whisper_timeout_seconds,
        }
    def print_configuration(self):
        import json

        print(
            "[STT CONFIG] "
            + json.dumps(self.configuration(), sort_keys=True)
        )
    def load(self, allow_download=False):
        if self.model is not None:
            return

        required = [
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ]

        missing = [
            filename
            for filename in required
            if not (MODEL_DIR / filename).is_file()
        ]

        if missing:
            raise SafeError(
                "Parakeet model files are missing: "
                + ", ".join(missing)
            )

        try:
            import sherpa_onnx

            start = time.perf_counter()

            self.model = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(MODEL_DIR / "encoder.int8.onnx"),
                decoder=str(MODEL_DIR / "decoder.int8.onnx"),
                joiner=str(MODEL_DIR / "joiner.int8.onnx"),
                tokens=str(MODEL_DIR / "tokens.txt"),
                num_threads=self.settings.whisper_cpu_threads,
                decoding_method="greedy_search",
                model_type="nemo_transducer",
                provider="cpu",
            )

            self.startup_metrics["load_ms"] = round(
                (time.perf_counter() - start) * 1000, 2
            )

        except Exception:
            raise SafeError(
                "Unable to load local Parakeet model"
            ) from None

    def warmup(self):
        if self._warmed:
            return

        self.load()
        start = time.perf_counter()

        try:
            stream = self.model.create_stream()
            silence = np.zeros(16000, dtype=np.float32)

            stream.accept_waveform(16000, silence)
            self.model.decode_stream(stream)

            # Never interpret warmup output as a command.
            self._warmed = True

        except Exception:
            raise SafeError("Parakeet warmup failed") from None

        finally:
            self.startup_metrics["warmup_ms"] = round(
                (time.perf_counter() - start) * 1000, 2
            )

    def transcribe(self, audio):
        started = time.perf_counter()

        self.last_metrics = {
            "accepted": False,
            "retry_count": 0,
            "escalation_reason": None,
        }

        try:
            if (
                not isinstance(audio, np.ndarray)
                or audio.dtype != np.float32
                or audio.ndim != 1
            ):
                raise SafeError(
                    "STT requires mono float32 audio at 16 kHz"
                )

            metrics = audio_metrics(audio.tobytes())

            self.last_metrics.update(
                duration_ms=metrics["duration_ms"],
                audio_rms=metrics["rms"],
                audio_peak=metrics["peak"],
            )

            if (
                metrics["frames"]
                > self.settings.max_record_seconds * 16000
            ):
                raise SafeError("Audio exceeds recording limit")

            if metrics["duration_ms"] < 200:
                raise SafeError("Audio segment too short")

            if metrics["peak"] < 0.00001:
                raise SafeError("No usable speech detected")

            self.load()

            stream = self.model.create_stream()

            samples = np.ascontiguousarray(audio)
            stream.accept_waveform(16000, samples)

            self.model.decode_stream(stream)

            text = stream.result.text.strip()

            if not text or len(text) > 2000:
                raise SafeError("No usable command detected")

            self.last_metrics["accepted"] = True
            return text

        except SafeError:
            raise
        except Exception:
            raise SafeError(
                "Local Parakeet transcription failed"
            ) from None
        finally:
            self.last_metrics["stt_ms"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
