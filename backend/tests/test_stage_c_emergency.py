"""Stage C regression tests: no microphone, model or desktop input."""
import ctypes
import multiprocessing
import signal
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from agent.runtime import Runtime
from agent.schemas import Action
from audio.push_to_talk import KeyMonitor
from config.settings import Settings
from safety.confirmation import ConfirmationManager
from safety.emergency_stop import EmergencyStop
from utils.helpers import SafeError, Cancelled


def runtime():
    brain = Mock()
    brain.router.confirmations = ConfirmationManager(key_reader=lambda: (False, False))
    r = Runtime(Settings(voice_mode='push_to_talk'), brain, Mock(), EmergencyStop())
    r.keys = Mock(failed=False)
    r.recorder = Mock(stream=None)
    r.ptt.recorder = r.recorder
    return r


@pytest.mark.parametrize('capture', [False, True])
def test_emergency_idle_or_capture_invalidates_and_cleans_once(capture, capsys):
    r = runtime()
    context = r._voice_context()
    if capture: r.ptt.down()
    r.enqueue('down')
    r.emergency_stop()
    generation = r.stop.generation
    r.emergency_stop()
    assert r.stop.generation == generation and r.closed.is_set()
    assert not r._capture_valid(context)
    r.enqueue('up')
    r._process('Open Notepad', context[0], context[4])
    r.resume()
    r.close(); r.close()
    assert r.stop.stopped and r.events.empty()
    r.brain.handle.assert_not_called()
    r.recorder.discard.assert_called_once()
    r.keys.close.assert_called_once()
    assert r.ptt.capture_context is None and r.ptt.started is None
    assert capsys.readouterr().out.count('[EMERGENCY STOP]') == 1


def test_emergency_confirmation_cannot_authorize_execution():
    r = runtime()
    manager = r.brain.router.confirmations
    failures = []
    def confirming():
        try:
            manager.confirm(Action('type_text', {'text': 'literal'}), None, r.stop)
            pytest.fail('Cancelled confirmation returned')
        except Cancelled: failures.append('cancelled')
    t = threading.Thread(target=confirming); t.start()
    deadline = time.monotonic() + 1
    while manager.pending is None and time.monotonic() < deadline: time.sleep(.005)
    assert manager.pending is not None
    nonce = manager.pending.nonce
    r.workers.append(t)
    r.emergency_stop(); r.close()
    assert failures == ['cancelled'] and not t.is_alive()
    assert manager.pending is None and not manager.respond('yes', nonce)


def test_hotkey_dispatch_independent_of_f8_and_registration(monkeypatch):
    user = Mock()
    user.RegisterHotKey.return_value = True
    user.GetAsyncKeyState.return_value = 0
    stop, callback = EmergencyStop(), Mock()
    monitor = KeyMonitor(stop, Mock(), Mock(), on_emergency=callback)
    def message(pointer, *args):
        pointer._obj.message = 0x0312
        pointer._obj.wParam = 1
        monitor.closed.set()
        user.PeekMessageW.side_effect = None
        user.PeekMessageW.return_value = 0
        return 1
    user.PeekMessageW.side_effect = message
    monkeypatch.setattr(ctypes, 'WinDLL', Mock(return_value=user))
    monitor.start(); monitor.close()
    user.RegisterHotKey.assert_called_once_with(None, 1, 0x4003, ord('X'))
    callback.assert_called_once_with()
    monitor.on_down.assert_not_called()
    user.UnregisterHotKey.assert_called_once()


def test_hotkey_registration_failure_denies_startup(monkeypatch):
    user = Mock(); user.RegisterHotKey.return_value = 0
    monkeypatch.setattr(ctypes, 'WinDLL', Mock(return_value=user))
    stop = EmergencyStop()
    monitor = KeyMonitor(stop, Mock(), Mock())
    with pytest.raises(SafeError, match='startup denied'): monitor.start()
    assert stop.stopped


def test_emergency_during_real_spawned_inference_reaps_worker(monkeypatch):
    import numpy as np
    from test_stt_optimization import worker_for_test
    r = runtime()
    r.transcriber = worker_for_test(monkeypatch, 'hang')
    r.stop = r.transcriber.stop
    pid = r.transcriber.process.pid
    context = r._voice_context()
    t = threading.Thread(target=r._transcribe, args=(np.zeros(16000, dtype=np.float32).tobytes(), context))
    r.workers.append(t); t.start()
    deadline = time.monotonic() + 1
    while not r.transcribing.locked() and time.monotonic() < deadline: time.sleep(.005)
    start = time.monotonic()
    r.emergency_stop(); r.close()
    assert time.monotonic() - start < 2
    assert not t.is_alive() and r.transcriber.process is None
    assert all(p.pid != pid for p in multiprocessing.active_children())
    r.brain.handle.assert_not_called()


def disconnected_worker(connection):
    from utils.worker_lifecycle import configure_worker, send_reply
    configure_worker()
    signal.raise_signal(signal.SIGINT)
    connection.close()
    assert not send_reply(connection, ('cancelled', None))


def test_spawned_child_interrupt_and_disconnected_reply_exit_cleanly(capfd):
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=disconnected_worker, args=(child,))
    process.start(); child.close(); parent.close()
    process.join(3)
    try:
        assert not process.is_alive() and process.exitcode == 0
        assert 'Traceback' not in capfd.readouterr().err
    finally:
        if process.is_alive(): process.terminate(); process.join()
        process.close()


def test_emergency_during_startup_closes_without_ready(capsys):
    r = runtime()
    def load():
        r.emergency_stop()
        raise SafeError('startup cancelled')
    r.transcriber.load.side_effect = load
    r.run_voice()
    assert '[READY]' not in capsys.readouterr().out
    r.keys.start.assert_called_once()
    r.keys.close.assert_called_once()


def test_emergency_interrupts_blocked_console_read(monkeypatch):
    r = runtime()
    reading, release = threading.Event(), threading.Event()
    def blocked_input(prompt):
        reading.set(); release.wait(3); return 'Open Notepad'
    monkeypatch.setattr('builtins.input', blocked_input)
    t = threading.Thread(target=r.run_text); t.start()
    try:
        assert reading.wait(1)
        r.emergency_stop(); t.join(1)
        assert not t.is_alive()
        r.brain.handle.assert_not_called()
    finally: release.set(); t.join(1)


def test_emergency_immediately_before_native_execution():
    from agent.schemas import Plan
    from test_validator import make_router
    r = runtime()
    router, calls = make_router(stop=r.stop, guard=Mock())
    router.status = lambda status: r.emergency_stop() if status.startswith('[EXECUTING]') else None
    results = router.execute(Plan('scroll down', (Action('scroll', {'direction': 'down', 'amount': 1}),)))
    assert results[-1].status == 'CANCELLED' and calls == []
    r.close()


def test_cancelled_startup_never_spawns(monkeypatch):
    from audio.stt_worker import BoundedSpeechToText
    context = Mock()
    monkeypatch.setattr('audio.stt_worker.multiprocessing.get_context', context)
    worker = BoundedSpeechToText(Settings(), EmergencyStop())
    with pytest.raises(SafeError, match='cancelled'): worker.load(valid=lambda: False)
    context.assert_not_called()
    worker.close(); worker.close()


def test_close_serializes_with_process_start(monkeypatch):
    from audio.stt_worker import BoundedSpeechToText
    entered, release = threading.Event(), threading.Event()
    process = Mock(pid=123)
    process.is_alive.return_value = True
    def start(): entered.set(); assert release.wait(2)
    process.start.side_effect = start
    process.terminate.side_effect = lambda: setattr(process.is_alive, 'return_value', False)
    parent, child = Mock(), Mock()
    context = Mock(); context.Pipe.return_value = (parent, child); context.Process.return_value = process
    monkeypatch.setattr('audio.stt_worker.multiprocessing.get_context', Mock(return_value=context))
    worker = BoundedSpeechToText(Settings(), EmergencyStop())
    worker._receive = Mock(return_value=('ready', {}, {}))
    loading = threading.Thread(target=worker.load); loading.start()
    assert entered.wait(1)
    closing = threading.Thread(target=worker.close); closing.start()
    release.set(); loading.join(2); closing.join(2)
    assert not loading.is_alive() and not closing.is_alive()
    process.terminate.assert_called_once()
    process.close.assert_called_once()
    assert worker.process is None and worker.connection is None
