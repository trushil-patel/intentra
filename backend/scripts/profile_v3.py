"""Read-only local performance probe; never sends input or launches applications."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import time
import statistics
from config.settings import ROOT, Settings
from agent.planner import deterministic_v2
from safety.emergency_stop import EmergencyStop
from vision.observer import Observer

def main():
    config = json.loads((ROOT / 'config/apps.json').read_text())
    samples = []
    for _ in range(100):
        start = time.perf_counter()
        deterministic_v2('Open Notepad.')
        samples.append((time.perf_counter() - start) * 1000)
    print(json.dumps({'parser_mean_ms': statistics.mean(samples), 'ollama_model': Settings.load().ollama_model}))
    import threading
    from utils.helpers import Cancelled
    from agent.computer_state import ComputerState, WindowState, Element
    from vision.semantic import ScreenState
    from vision.grounding import ground
    screen = ScreenState.from_computer(ComputerState(WindowState(1,'chrome'), available=True,
        elements=(Element((1,), 'Edit', 'Address and search bar'),)))
    start = time.perf_counter()
    for _ in range(100): ground(screen, 'address bar')
    print(json.dumps({'structured_grounding_mean_ms': (time.perf_counter()-start)*10}))
    samples = []
    for _ in range(20):
        token, ready, done = EmergencyStop(), threading.Event(), threading.Event()
        def waiting():
            ready.set()
            try: token.wait(30)
            except Cancelled: done.set()
        thread = threading.Thread(target=waiting)
        thread.start()
        ready.wait(1)
        start = time.perf_counter()
        token.stop()
        assert done.wait(.5)
        samples.append((time.perf_counter()-start)*1000)
        thread.join()
    print(json.dumps({'cancel_wait_mean_ms':statistics.mean(samples), 'cancel_wait_max_ms':max(samples)}))
    for level in (0, 1):
        for _ in range(3):
            start = time.perf_counter()
            state = Observer(config, EmergencyStop()).observe(level=level)
            print(json.dumps({'level': level, 'ms': (time.perf_counter()-start)*1000,
                              'available': state.available, 'windows': len(state.open_windows),
                              'foreground': state.current_application, 'elements': len(state.elements)}))
if __name__ == '__main__': main()
