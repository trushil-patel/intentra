"""Regression coverage for STT tuning/observability; no microphone or downloads."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
import pytest
from audio.speech_to_text import SpeechToText
from config.settings import Settings
from utils.helpers import SafeError
from test_voice_reliability import make_runtime, AUDIO

def fake_parakeet(text="Open Notepad"):
    stream = Mock()
    stream.result.text = text

    model = Mock()
    model.create_stream.return_value = stream

    return model, stream

def segment(text='Open Notepad', **kwargs):
    return SimpleNamespace(text=text, avg_logprob=-.1, no_speech_prob=.01,
                           compression_ratio=1.1, temperature=0, **kwargs)


@pytest.mark.parametrize('threads', [0, -1, 17, True])
def test_invalid_thread_budget(threads):
    with pytest.raises(ValueError): Settings(whisper_cpu_threads=threads)


def test_model_loaded_once_with_explicit_threads_and_one_worker():
    with patch(
        "sherpa_onnx.OfflineRecognizer.from_transducer"
    ) as factory:
        stt = SpeechToText(
            settings=Settings(whisper_cpu_threads=2)
        )

        stt.load()
        stt.load()

    factory.assert_called_once()

    assert factory.call_args.kwargs["num_threads"] == 2
    assert factory.call_args.kwargs["provider"] == "cpu"
    assert factory.call_args.kwargs["model_type"] == "nemo_transducer"
    assert stt.startup_metrics["load_ms"] >= 0


def test_configuration_identity_does_not_print_hint_content(capsys):
    stt = SpeechToText(
        settings=Settings(
            whisper_hotwords="private vocabulary",
            whisper_cpu_threads=2,
        )
    )

    stt.print_configuration()

    output = capsys.readouterr().out

    # Sensitive vocabulary must never appear in logs.
    assert "private vocabulary" not in output

    config = json.loads(output.split("[STT CONFIG] ")[1])

    assert config["engine"] == "sherpa-onnx"
    assert config["model"] == "parakeet-tdt-0.6b-v3-int8"
    assert config["device"] == "cpu"
    assert config["cpu_threads"] == 2
    assert config["num_workers"] == 1
    assert config["timeout_seconds"] == 15.0


def test_warmup_runs_decoder_once_discards_output_and_preserves_metrics():
    model, stream = fake_parakeet("Press Enter")

    stt = SpeechToText(model=model)
    stt.last_metrics = {"accepted": False}

    stt.warmup()
    stt.warmup()

    model.create_stream.assert_called_once()
    model.decode_stream.assert_called_once_with(stream)

    args = stream.accept_waveform.call_args.args

    assert args[0] == 16000
    assert np.array_equal(
        args[1],
        np.zeros(16000, dtype=np.float32)
    )

    assert stt.last_metrics == {"accepted": False}
    assert stt.startup_metrics["warmup_ms"] >= 0


def test_failed_warmup_does_not_mark_ready():
    model, _ = fake_parakeet()
    model.decode_stream.side_effect = RuntimeError("private details")

    stt = SpeechToText(model=model)

    with pytest.raises(SafeError, match="warmup failed"):
        stt.warmup()

    assert not stt._warmed
    assert stt.last_metrics == {}


@pytest.mark.parametrize(
    "text",
    [
        "Open Notepad",
        "Open not bad",
        "Type hello from workspilote",
    ],
)
def test_single_stage_preserves_transcript_and_same_audio(text):
    model, stream = fake_parakeet(text)

    stt = SpeechToText(model=model)
    audio = np.full(16000, 0.1, dtype=np.float32)
    original = audio.copy()

    assert stt.transcribe(audio) == text

    model.create_stream.assert_called_once()
    model.decode_stream.assert_called_once_with(stream)

    args = stream.accept_waveform.call_args.args
    assert args[0] == 16000
    np.testing.assert_array_equal(args[1], original)
    np.testing.assert_array_equal(audio, original)

    assert stt.last_metrics["accepted"]
    assert stt.last_metrics["retry_count"] == 0


@pytest.mark.parametrize("case", ["digital_silence", "empty_text"])
def test_unusable_audio_rejected_without_retry(case):
    model, stream = fake_parakeet("")

    stt = SpeechToText(model=model)

    if case == "digital_silence":
        audio = np.zeros(16000, dtype=np.float32)
    else:
        audio = np.full(16000, 0.1, dtype=np.float32)

    with pytest.raises(SafeError):
        stt.transcribe(audio)

    assert stt.last_metrics["accepted"] is False
    assert stt.last_metrics["retry_count"] == 0

    if case == "digital_silence":
        model.decode_stream.assert_not_called()
    else:
        model.decode_stream.assert_called_once_with(stream)

@pytest.mark.parametrize('heard,route', [('Open Notepad', 'DETERMINISTIC'),
    ('Open not bad', 'REPEAT'), ('Open load pad', 'REPEAT'), ('Open Nootpad', 'REPEAT'),
    ('Praise enter', 'REPEAT'), ('Place enter', 'REPEAT'), ('Enter.', 'REPEAT')])
def test_diagnostic_records_route_without_execution_or_retry(heard, route):
    runtime = make_runtime(); runtime.transcribe_only = True
    runtime.transcriber.transcribe.return_value = heard
    runtime.transcriber.last_metrics = {'accepted': True, 'stt_ms': 12}
    runtime.diagnostic_sink = Mock()
    with patch('scipy.io.wavfile.write') as write:
        runtime._transcribe(AUDIO)
    runtime.transcriber.transcribe.assert_called_once()
    runtime.brain.handle.assert_not_called(); write.assert_not_called()
    record = runtime.diagnostic_sink.call_args.args[0]
    assert record['route'] == route and record['accepted'] is True
    assert record['total_ms'] >= 0 and record['retry_count'] == 0
    assert record['segment']['peak'] > 0


@pytest.mark.parametrize('invalidate', ['cancel', 'replacement'])
def test_late_decode_cannot_publish_benchmark_or_execute(invalidate):
    runtime = make_runtime(); runtime.transcribe_only = True; runtime.diagnostic_sink = Mock()
    def decode(_):
        if invalidate == 'cancel': runtime.stop.stop()
        else: runtime.task_serial += 1
        return 'Open Notepad'
    runtime.transcriber.transcribe.side_effect = decode
    runtime._transcribe(AUDIO)
    runtime.transcriber.transcribe.assert_called_once()
    runtime.diagnostic_sink.assert_not_called(); runtime.brain.handle.assert_not_called()


def test_configuration_comparison_supports_same_capture_mode(tmp_path):
    from audio.benchmark import ComparisonSession, load_samples, configuration_table
    path = tmp_path / 'run.jsonl'
    session = ComparisonSession(path, Settings(voice_mode='push_to_talk'), Mock())
    session.accept({'heard': 'Open not bad', 'route': 'REPEAT', 'accepted': True,
        'confidence': {'stt_ms': 123}, 'total_ms': 124, 'retry_count': 0, 'segment': {'rms': .1}})
    session.close()
    sample = load_samples(path)
    report = configuration_table(sample, sample)
    assert 'REPEAT / True' in report and '123 / 124' in report
    assert 'not correct recognition or action authorization' in report
    assert sample['Open Notepad']['profile']['stt_config']['cpu_threads'] == 2
    assert list(tmp_path.iterdir()) == [path]


def test_oversize_audio_rejected_before_model_call():
    model = Mock(); stt = SpeechToText(model=model, settings=Settings(max_record_seconds=1))
    with pytest.raises(SafeError, match='recording limit'):
        stt.transcribe(np.zeros(16001, dtype=np.float32))
    model.transcribe.assert_not_called()


def test_environment_loads_candidate_without_changing_confidence(monkeypatch):
    monkeypatch.setenv('WHISPER_CPU_THREADS', '2')
    monkeypatch.setenv('WHISPER_WARMUP', 'true')
    monkeypatch.setenv('WHISPER_TEMPERATURES', '0')
    settings = Settings.load()
    assert settings.whisper_cpu_threads == 2 and settings.whisper_warmup
    assert settings.whisper_temperatures == (0,)
    assert settings.whisper_logprob_threshold == -1 and settings.whisper_no_speech_threshold == .6


# Real process/IPC tests use a tiny trusted fake decoder, never a model download.
def fake_stt_process(connection, settings):
    import time
    connection.send(('ready', {'effective_device': 'cpu'}, {'load_ms': 1}))
    try:
        while True:
            operation, audio = connection.recv()
            if operation == 'warmup':
                connection.send(('warm', None, {'warmup_ms': 1}))
            elif settings.whisper_model == 'hang':
                time.sleep(10)
            elif settings.whisper_model == 'reject':
                connection.send(('rejected', 'Speech was unclear; please repeat', {'accepted': False}))
            else:
                connection.send(('ok', 'Open Notepad', {'accepted': True, 'retry_count': 0,
                    'audio_sum': float(audio.sum())}))
    except (EOFError, OSError): pass
    finally: connection.close()


def worker_for_test(monkeypatch, model='fake'):
    from audio import stt_worker
    from safety.emergency_stop import EmergencyStop
    monkeypatch.setattr(stt_worker, '_worker', fake_stt_process)
    worker = stt_worker.BoundedSpeechToText(Settings(whisper_model=model, whisper_timeout_seconds=1), EmergencyStop())
    worker.load()
    return worker


def test_resident_process_reuses_model_and_preserves_audio(monkeypatch):
    worker = worker_for_test(monkeypatch)
    try:
        pid = worker.process.pid
        audio = np.full(16000, .1, dtype=np.float32)
        expected_sum = float(audio.sum())
        for _ in range(2):
            assert worker.transcribe(audio) == 'Open Notepad'
            assert worker.last_metrics['audio_sum'] == expected_sum
        assert worker.process.pid == pid
        assert float(audio.sum()) == expected_sum
        assert worker.last_metrics['retry_count'] == 0
    finally: worker.close()
    assert worker.process is None and worker.connection is None


def test_native_timeout_terminates_worker_without_retry(monkeypatch):
    import time
    worker = worker_for_test(monkeypatch, 'hang')
    start = time.monotonic()
    with pytest.raises(SafeError, match='timed out'):
        worker.transcribe(np.zeros(16000, dtype=np.float32))
    assert time.monotonic() - start < 2.5
    assert worker.process is None and not worker.last_metrics['accepted']
    assert worker.last_metrics['retry_count'] == 0


@pytest.mark.parametrize('reason', ['stop', 'replacement'])
def test_cancel_or_replacement_interrupts_native_wait(monkeypatch, reason):
    import threading
    import time
    worker = worker_for_test(monkeypatch, 'hang')
    valid = threading.Event(); valid.set()
    invalidate = worker.stop.stop if reason == 'stop' else valid.clear
    timer = threading.Timer(.05, invalidate)
    start = time.monotonic(); timer.start()
    try:
        with pytest.raises(SafeError, match='cancelled'):
            worker.transcribe(np.zeros(16000, dtype=np.float32), valid=valid.is_set)
        assert time.monotonic() - start < 1
        assert worker.process is None
    finally: timer.join(); worker.close()


def test_confidence_rejection_keeps_resident_worker_without_retry(monkeypatch):
    worker = worker_for_test(monkeypatch, 'reject')
    try:
        pid = worker.process.pid
        with pytest.raises(SafeError, match='unclear'):
            worker.transcribe(np.zeros(16000, dtype=np.float32))
        assert worker.process.pid == pid and worker.process.is_alive()
        assert worker.last_metrics['retry_count'] == 0
    finally: worker.close()


@pytest.mark.parametrize('seconds', [0, 31, float('nan'), True])
def test_invalid_inference_deadline(seconds):
    with pytest.raises(ValueError): Settings(whisper_timeout_seconds=seconds)


def test_fresh_capture_can_reload_after_interrupted_worker(monkeypatch):
    worker = worker_for_test(monkeypatch)
    worker.close()
    try:
        assert worker.transcribe(np.zeros(16000, dtype=np.float32)) == 'Open Notepad'
        assert worker.last_metrics['recovery_load_ms'] >= 0
        assert worker.last_metrics['retry_count'] == 0
    finally: worker.close()
