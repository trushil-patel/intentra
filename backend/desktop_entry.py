"""Run the unchanged CLI composition with optional private desktop telemetry."""
import sys
if __name__ == "__mp_main__":
    from utils.worker_lifecycle import configure_worker
    configure_worker()
from utils.com import configure_com
configure_com()


def main():
    from desktop.bridge import Bridge
    from desktop.events import install
    output = sys.stdout
    sys.stdout = sys.stderr  # Existing console messages never become protocol data.
    bridge = Bridge(output)
    install(bridge)
    try:
        if sys.argv[1:] == ['--bridge-check']:
            bridge.emit('offline', 'bridge_ready', 'Private transport ready')
            for line in sys.stdin:
                bridge.control(line)
                if line.strip() == '{"op":"shutdown"}':
                    break
            return 0
        from main import main as agent_main
        return agent_main([], desktop=bridge)
    except Exception:
        bridge.emit('failed', 'startup_error', 'Agent startup failed. Check Python dependencies, model files, and emergency shortcut availability.')
        return 1
    finally:
        install(None)
        bridge.close()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
