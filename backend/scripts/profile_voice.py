"""Content-free voice/router profiling. No desktop actions or model downloads."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from agent.command_classifier import classify
from agent.planner import deterministic_v2
from audio.command_normalizer import normalize_transcript
from audio.hands_free import SileroDetector
from audio.segments import audio_metrics
from config.settings import Settings
from utils.helpers import SafeError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stt-silence',action='store_true',help='load local Whisper and check silence rejection; NOT a speech accuracy test')
    args=parser.parse_args()
    settings=Settings.load()
    for label,command in [('simple','Open Notepad'),('uncertain','Open not bad'),('complex','Open Chrome and show the time')]:
        samples=[]
        for _ in range(100):
            start=time.perf_counter()
            normalized=normalize_transcript(command)
            kind=classify(normalized,deterministic_v2(normalized))
            samples.append((time.perf_counter()-start)*1000)
        print(json.dumps({'stage':'normalizer_parser_classifier','case':label,'kind':kind.value,'mean_ms':statistics.mean(samples),'max_ms':max(samples)}))
    detector=SileroDetector(settings.vad_threshold)
    samples=[]
    for _ in range(100):
        block=np.zeros(2048,dtype=np.float32)
        start=time.perf_counter()
        assert not detector(block)
        samples.append((time.perf_counter()-start)*1000)
    print(json.dumps({'stage':'local_vad_silence','block_ms':128,'mean_ms':statistics.mean(samples),'max_ms':max(samples)}))
    print(json.dumps({'audio_format':'16000 Hz mono float32','synthetic_audio_metrics':audio_metrics(np.zeros(16000,dtype=np.float32).tobytes())}))
    if args.stt_silence:
        from audio.speech_to_text import SpeechToText
        stt=SpeechToText(settings=settings)
        start=time.perf_counter(); stt.load()
        print(json.dumps({'stage':'whisper_local_load','model':settings.whisper_model,'ms':(time.perf_counter()-start)*1000}))
        try:
            stt.transcribe(np.zeros(16000,dtype=np.float32))
            raise RuntimeError('Silence unexpectedly produced a transcript')
        except SafeError as exc:
            print(json.dumps({'stage':'stt_silence_rejection','result':str(exc),'metrics':stt.last_metrics}))
if __name__=='__main__':main()
