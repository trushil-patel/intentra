"""Read-only V2 native smoke check. No screenshots, typed input or private labels."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.com import configure_com
configure_com()
import json
from config.settings import ROOT, Settings
from safety.emergency_stop import EmergencyStop
from vision.observer import Observer
from tools.native import NativeBridge
from agent.schemas import Action


def main():
    apps = json.loads((ROOT / 'config/apps.json').read_text())
    stop = EmergencyStop()
    try:
        state = Observer(apps, stop, timeout=5).observe()
        print('observation_available:', state.available)
        print('approved_window_count:', len(state.open_windows))
        print('visible_element_count:', len(state.elements))
        result = NativeBridge(apps, stop, Settings.load()).invoke(Action('get_current_time', {}))
        good = isinstance(result, dict) and 'time' in result
        print('native_worker_query:', good)
        return 0 if state.available and good else 1
    finally:
        stop.stop()


if __name__ == '__main__':
    raise SystemExit(main())
