"""Non-microphone decoder probe. Synthetic timings are NOT speech accuracy/latency.

Each process uses one local model only. No downloads, audio files or desktop actions.
Run separately for each thread/model setting to avoid competing inference pools.
"""
from dataclasses import replace
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from audio.speech_to_text import SpeechToText
from config.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warmup', action='store_true')
    parser.add_argument('--bounded', action='store_true', help='probe the actual persistent worker; no microphone or desktop')
    args = parser.parse_args()
    if args.bounded:
        from audio.stt_worker import BoundedSpeechToText
        from safety.emergency_stop import EmergencyStop
        from utils.helpers import SafeError
        worker = BoundedSpeechToText(replace(Settings.load(), whisper_vad_filter=False, whisper_warmup=args.warmup), EmergencyStop())
        try:
            worker.load(); worker.print_configuration()
            if args.warmup: worker.warmup()
            print('[STARTUP] ' + json.dumps(worker.startup_metrics))
            for index in range(3):
                try:
                    worker.transcribe(np.zeros(16000, dtype=np.float32))
                except SafeError:
                    pass
                print(json.dumps({'bounded_synthetic_index': index+1, 'metrics': worker.last_metrics,
                    'resident_worker_alive': worker.process is not None and worker.process.is_alive()}))
        finally: worker.close()
        return
    stt = SpeechToText(settings=replace(Settings.load(), whisper_warmup=args.warmup))
    stt.load()
    stt.print_configuration()
    if args.warmup: stt.warmup()
    print('[STARTUP] ' + json.dumps(stt.startup_metrics))
    # Disable VAD for this probe ONLY: filtered silence never exercises the decoder.
    # Cap synthetic output at 16 tokens; discard every token, never route it.
    for index in range(3):
        start = time.perf_counter()
        segments, _ = stt.model.transcribe(np.zeros(16000, dtype=np.float32),
            language=stt.settings.whisper_language, temperature=0.0,
            beam_size=stt.settings.whisper_beam_size, best_of=stt.settings.whisper_best_of,
            vad_filter=False, condition_on_previous_text=False,
            initial_prompt=None, hotwords=None, max_new_tokens=16)
        for _ in segments: pass
        print(json.dumps({'synthetic_decode_index': index + 1,
            'ms': round((time.perf_counter()-start)*1000, 2),
            'vad_filter': False, 'max_new_tokens': 16, 'speech_accuracy': 'UNMEASURED'}))

if __name__ == '__main__': main()
