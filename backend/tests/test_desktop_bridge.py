import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from desktop.bridge import Bridge
from desktop import events


def drain(bridge, output):
    bridge.queue.join()
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_order_ids_redaction_and_session():
    out = io.StringIO()
    b = Bridge(out)
    try:
        b.emit('listening', 'capture', new_session=True)
        b.emit('understanding', 'transcript', 'password abc')
        records = drain(b, out)
        assert [r['sequence'] for r in records] == [1, 2]
        assert records[0]['sessionId'] == records[1]['sessionId']
        assert records[0]['id'] != records[1]['id']
        assert 'abc' not in records[1]['message']
    finally:
        b.close()


def test_native_done_does_not_claim_verification():
    out = io.StringIO()
    b = Bridge(out)
    try:
        b.status_line('[DONE]')
        assert drain(b, out) == []
        b.status_line('[DONE] Task verified.')
        assert drain(b, out)[0]['status'] == 'completed'
    finally:
        b.close()


def test_emergency_latch_wins_late_idle():
    out = io.StringIO()
    b = Bridge(out)
    try:
        b.emit('emergency_stopped', 'emergency')
        b.emit('idle', 'ready')
        assert len(drain(b, out)) == 1
    finally:
        b.close()


def test_controls_do_not_expose_execution_or_confirmation():
    out = io.StringIO()
    b = Bridge(out)
    b.runtime = SimpleNamespace(emergency_stop=Mock())
    try:
        for raw in ['{}', '[]', 'not json', '{"op":"confirm"}', '{"op":"eval"}', '{"op":"stop","extra":1}']:
            assert not b.control(raw)
        b.runtime.emergency_stop.assert_not_called()
        assert b.control('{"op":"stop"}')
        b.runtime.emergency_stop.assert_called_once()
    finally:
        b.close()


def test_pipe_disconnect_stops_managed_runtime():
    b = Bridge(io.StringIO())
    b.runtime = SimpleNamespace(emergency_stop=Mock())
    b.read_controls(io.StringIO(''))
    assert b.failed.is_set()
    b.runtime.emergency_stop.assert_called_once()
    b.close()


def test_oversized_control_stops_runtime():
    b = Bridge(io.StringIO())
    b.runtime = SimpleNamespace(emergency_stop=Mock())
    b.read_controls(io.StringIO('x' * 4098))
    assert b.failed.is_set()
    b.runtime.emergency_stop.assert_called_once()
    b.close()


def test_telemetry_failure_does_not_raise_into_engine():
    sink = SimpleNamespace(emit=Mock(side_effect=ValueError), failed=Mock())
    events.install(sink)
    try:
        events.emit('idle', 'ready')
        sink.failed.set.assert_called_once()
    finally:
        events.install(None)


def test_real_subprocess_pipe_protocol():
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-u', 'desktop_entry.py', '--bridge-check'],
                            cwd=backend, input='{"op":"ping"}\n{"op":"shutdown"}\n',
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r['type'] for r in records] == ['bridge_ready', 'heartbeat']
    assert [r['sequence'] for r in records] == [1, 2]
    assert records[0]['runId'] == records[1]['runId']


def test_real_windows_polling_pipe_and_shutdown():
    if sys.platform != 'win32':
        return  # Windows-only native pipe implementation; portable protocol above.
    backend = Path(__file__).resolve().parents[1]
    code = '''import sys
from types import SimpleNamespace
from desktop.bridge import Bridge
b=Bridge(sys.stdout)
b.runtime=SimpleNamespace(emergency_stop=b.done.set)
b.read_controls(sys.stdin)
b.close()
'''
    result = subprocess.run([sys.executable, '-u', '-c', code], cwd=backend,
                            input='{"op":"ping"}\n{"op":"shutdown"}\n',
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1
    assert records[0]['type'] == 'heartbeat'
