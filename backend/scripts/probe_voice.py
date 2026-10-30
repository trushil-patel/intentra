import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import time
import statistics
import numpy as np
from config.settings import Settings
from audio.hands_free import SileroDetector, HandsFreeListener
from safety.emergency_stop import EmergencyStop
from agent.planning_worker import BoundedOllamaProvider


def main():
    detector = SileroDetector()
    samples=[]
    for _ in range(100):
        start=time.perf_counter()
        assert not detector(np.zeros(2048, dtype=np.float32))
        samples.append((time.perf_counter()-start)*1000)
    print(json.dumps({'silero_silence_mean_ms':statistics.mean(samples),'silero_max_ms':max(samples)}))
    listener = HandsFreeListener(Settings(), lambda *_: None, lambda: True, lambda: None, detector=detector)
    try:
        listener.start()
        time.sleep(.3)
        print(json.dumps({'microphone_opened':True,'audio_failed':listener.failed.is_set(), 'capture_suppressed':True}))
    except Exception as exc:
        print(json.dumps({'microphone_opened':False,'error':str(exc)}))
    finally: listener.close()
    stop=EmergencyStop()
    provider=BoundedOllamaProvider(Settings(),stop,timeout=8)
    for _ in range(3):
        start=time.perf_counter()
        try:
            value=provider.generate_plan('What time is it?',{})
            print(json.dumps({'ollama_ms':(time.perf_counter()-start)*1000,'response_received':bool(value)}))
        except Exception as exc:
            print(json.dumps({'ollama_ms':(time.perf_counter()-start)*1000,'error':str(exc)}))
    provider.close()
if __name__=='__main__': main()
