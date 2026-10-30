"""F8 release regressions. Keyboard/audio inputs are simulated, not hardware acceptance."""
import ctypes
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from agent.runtime import Runtime
from audio.push_to_talk import KeyMonitor
from audio.stt_worker import BoundedSpeechToText
from config.settings import Settings
from safety.confirmation import ConfirmationManager, Pending
from safety.emergency_stop import EmergencyStop


def make_runtime():
    brain = Mock()
    brain.router.confirmations = ConfirmationManager(key_reader=lambda: (False, False))
    runtime = Runtime(Settings(), brain, Mock(), EmergencyStop(), transcribe_only=True)
    runtime.transcriber.startup_metrics = {}
    runtime.keys = Mock(failed=False)
    runtime.recorder.start = Mock()
    runtime.recorder.finish = Mock(return_value=np.full(16000, .1, dtype=np.float32).tobytes())
    runtime.recorder.discard = Mock()
    runtime._spawn = lambda fn, *args: fn(*args)
    return runtime


@pytest.mark.parametrize('selected', [None, 'hands_free', 'push_to_talk'])
def test_settings_selection_without_development_dotenv(monkeypatch, tmp_path, selected):
    monkeypatch.setattr('config.settings.ROOT', tmp_path)
    monkeypatch.delenv('VOICE_MODE', raising=False)
    if selected:
        monkeypatch.setenv('VOICE_MODE', selected)
    assert Settings().voice_mode == 'push_to_talk'
    assert Settings.load().voice_mode == (selected or 'push_to_talk')


@pytest.mark.parametrize('frozen', [False, True])
@pytest.mark.parametrize('environment,cli,expected', [
    (None, None, 'push_to_talk'), ('hands_free', None, 'hands_free'),
    ('hands_free', 'push_to_talk', 'push_to_talk'),
    ('push_to_talk', 'hands_free', 'hands_free'),
])
def test_main_mode_selection_and_frozen_startup(monkeypatch, tmp_path, capsys, frozen, environment, cli, expected):
    import main
    monkeypatch.setattr('config.settings.ROOT', tmp_path)
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.delenv('VOICE_MODE', raising=False)
    if environment:
        monkeypatch.setenv('VOICE_MODE', environment)
    with patch('multiprocessing.freeze_support') as freeze, patch.object(main, 'initialize_com'), \
         patch('config.app_catalog.discover_apps', return_value={}), \
         patch('tools.native.NativeBridge'), patch('agent.planning_worker.BoundedOllamaProvider'), \
         patch('utils.logger.Audit'), patch('agent.runtime.Runtime') as runtime:
        args = ['--startup-check'] + (['--voice-mode', cli] if cli else [])
        assert main.main(args) == 0
        freeze.assert_called_once()
        assert runtime.call_args.args[0].voice_mode == expected
        runtime.return_value.run_voice.assert_called_once_with(check_only=True)
        assert '========== INTENTRA MVP v0.1.0 ==========' in capsys.readouterr().out


def test_default_startup_ready_without_microphone_or_vad(capsys):
    r = make_runtime()
    with patch('audio.hands_free.HandsFreeListener') as listener, patch('audio.hands_free.SileroDetector') as vad:
        r.run_voice(check_only=True)
    assert '[READY] Hold F8 to speak.' in capsys.readouterr().out
    r.recorder.start.assert_not_called()
    listener.assert_not_called()
    vad.assert_not_called()
    r.transcriber.load.assert_called_once()


def test_f8_key_edges_ignore_repeat_and_confirmation_keys(monkeypatch):
    user = Mock()
    user.RegisterHotKey.return_value = True
    user.PeekMessageW.return_value = 0
    down, up = Mock(), Mock()
    monitor = KeyMonitor(EmergencyStop(), down, up)
    states = iter([0, 0x8000, 0x8000, 0])
    def read(key):
        if key != 0x77:
            return 0
        try:
            return next(states)
        except StopIteration:
            monitor.closed.set()
            return 0
    user.GetAsyncKeyState.side_effect = read
    monkeypatch.setattr(ctypes, 'WinDLL', Mock(return_value=user))
    monitor._run()
    down.assert_called_once()
    up.assert_called_once()
    assert not monitor.failed
    assert all(call.args[0] not in (0x78, 0x79) for call in user.GetAsyncKeyState.call_args_list)
    user.RegisterHotKey.assert_called_once_with(None, 1, 0x4003, ord('X'))


def test_f8_release_submits_once_through_existing_bounded_stt():
    r = make_runtime()
    r.transcriber = BoundedSpeechToText(r.settings, r.stop)
    with patch.object(r.transcriber, 'transcribe', return_value='Open Chrome') as transcribe:
        r.ptt.down(); r.ptt.down()
        r.recorder.start.assert_called_once()
        transcribe.assert_not_called()
        r.ptt.up(); r.ptt.up()
        r.recorder.finish.assert_called_once()
        transcribe.assert_called_once()
        assert transcribe.call_args.args[0].shape == (16000,)
        assert transcribe.call_args.kwargs['valid']()
    r.brain.handle.assert_not_called()
    r.close()


def test_f8_never_authorizes_pending_confirmation():
    r = make_runtime()
    manager = r.brain.router.confirmations
    manager.pending = Pending('current', 'action', time.monotonic() + 30)
    r.ptt.down()
    assert manager._reply is None
    r.transcriber.transcribe.return_value = 'Open Chrome'
    r.ptt.up()
    assert manager._reply is None
    r.close()


@pytest.mark.parametrize('key,expected', [(0x78, (True, False)), (0x79, (False, True))])
def test_confirmation_key_mapping_unchanged(monkeypatch, key, expected):
    user = Mock()
    user.GetAsyncKeyState.side_effect = lambda code: 0x8000 if code == key else 0
    monkeypatch.setattr(ctypes, 'WinDLL', Mock(return_value=user))
    assert ConfirmationManager._windows_keys() == expected


def test_emergency_cancels_held_f8_and_pending_confirmation():
    r = make_runtime()
    manager = r.brain.router.confirmations
    manager.pending = Pending('current', 'action', time.monotonic() + 30)
    r.ptt.down()
    r.emergency_stop()
    r.ptt.up()
    r.close()
    r.recorder.finish.assert_not_called()
    r.transcriber.transcribe.assert_not_called()
    assert r.closed.is_set() and r.stop.stopped
    assert manager.pending is None and not manager.respond('yes', 'current')


def test_frozen_model_path_is_relative_to_executable(monkeypatch, tmp_path):
    import runpy
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'Intentra.exe'))
    monkeypatch.delenv('INTENTRA_MODEL_DIR', raising=False)
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'audio/speech_to_text.py'))
    assert module['MODEL_DIR'] == tmp_path / 'models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8'


def test_launcher_forwards_explicit_cli_mode():
    launcher = (Path(__file__).resolve().parents[1] / 'release/Start-Intentra.cmd').read_text()
    assert '"%~dp0Intentra.exe" %*' in launcher
    assert 'VOICE_MODE=' not in launcher
