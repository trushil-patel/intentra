"""Local numeric diagnostics only: no waveform or transcript persistence."""
import json
import numpy as np
from utils.helpers import SafeError

SAMPLE_RATE = 16000


def audio_metrics(data):
    if not data or len(data) % 4:
        raise SafeError('No usable audio segment')
    values = np.frombuffer(data, dtype=np.float32)
    if not np.isfinite(values).all() or np.max(np.abs(values)) > 1.01:
        raise SafeError('Invalid microphone samples; check the input format')
    return {'duration_ms': round(len(values) / 16, 1), 'frames': len(values),
            'rms': round(float(np.sqrt(np.mean(values.astype(np.float64) ** 2))), 7),
            'peak': round(float(np.max(np.abs(values))), 7),
            'clipped_fraction': round(float(np.mean(np.abs(values) >= .999)), 5)}


def usable_audio(data):
    metrics = audio_metrics(data)
    # Reject digital silence/empty taps, not quiet speech by arbitrary gain changes.
    if metrics['duration_ms'] < 200 or metrics['peak'] < .00001:
        raise SafeError('No speech detected')
    return metrics


def report_segment(data, **metadata):
    values = audio_metrics(data)
    values.update(metadata)
    print('[SEGMENT] ' + json.dumps(values, sort_keys=True))
    return values
