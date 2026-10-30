"""Cancellation regressions; no microphone, native keys or desktop actions."""
import time
from unittest.mock import Mock
import pytest
from safety.confirmation import Pending
from test_stage_c_emergency import runtime
from utils.helpers import SafeError


@pytest.mark.parametrize('stale', ['generation', 'task'])
def test_stale_dispatch_cannot_answer_current_confirmation(stale):
    r = runtime()
    manager = r.brain.router.confirmations
    manager.pending = Pending('current', 'action-target-content', time.monotonic() + 30)
    generation, serial = r.stop.generation, r.task_serial
    if stale == 'generation':
        r.stop.stop()
        r.stop.resume()
    else:
        r.task_serial += 1
    r._process('yes', generation, serial)
    assert manager._reply is None
    r.brain.handle.assert_not_called()
    r.close()


@pytest.mark.parametrize('emergency', [False, True])
def test_hands_free_startup_failure_cleanup(monkeypatch, emergency, capsys):
    r = runtime()
    monkeypatch.setattr('audio.hands_free.SileroDetector', Mock())
    def load():
        if emergency:
            r.emergency_stop()
        raise SafeError('startup interrupted')
    r.transcriber.load.side_effect = load
    if emergency:
        r._run_hands_free(check_only=True)
    else:
        with pytest.raises(SafeError, match='startup interrupted'):
            r._run_hands_free(check_only=True)
    assert r.closed.is_set()
    r.keys.close.assert_called_once()
    assert '[READY]' not in capsys.readouterr().out
    r.brain.handle.assert_not_called()
