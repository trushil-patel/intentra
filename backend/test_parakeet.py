
from pathlib import Path
import sys
import time

import numpy as np
import soundfile as sf
import sherpa_onnx

ROOT = Path(__file__).resolve().parent

MODEL_DIR = (
    ROOT / "models" /
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
)

print("Loading NVIDIA Parakeet...")

start = time.perf_counter()

recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
    encoder=str(MODEL_DIR / "encoder.int8.onnx"),
    decoder=str(MODEL_DIR / "decoder.int8.onnx"),
    joiner=str(MODEL_DIR / "joiner.int8.onnx"),
    tokens=str(MODEL_DIR / "tokens.txt"),
    num_threads=2,
    decoding_method="greedy_search",
    model_type="nemo_transducer",
    provider="cpu",
)

print(f"Model loaded in {time.perf_counter() - start:.2f}s")

audio_file = sys.argv[1] if len(sys.argv) > 1 else "test_command.wav"

audio, sample_rate = sf.read(audio_file, dtype="float32")

if sample_rate != 16000 or audio.ndim != 1:
    raise ValueError("Audio must be mono, 16 kHz")

if len(audio) == 0 or not np.isfinite(audio).all():
    raise ValueError("Invalid audio")

stream = recognizer.create_stream()
stream.accept_waveform(16000, np.ascontiguousarray(audio))

print("Transcribing...")

start = time.perf_counter()
recognizer.decode_stream(stream)

print("Recognized:", stream.result.text)
print(f"Decode time: {time.perf_counter() - start:.2f} seconds")
