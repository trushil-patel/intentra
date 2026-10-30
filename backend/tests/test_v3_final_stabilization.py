"""Final grammar/production contract. No microphone, network or desktop effects."""
from unittest.mock import Mock, patch
import json
import os
import pytest
from agent.command_language import recognize, LanguageKind, ALIASES
from agent.command_classifier import classify, CommandKind
from agent.planner import deterministic_v2
from agent.brain import Brain
from agent.context import UserCommand
from audio.command_normalizer import normalize_transcript
from audio.speech_to_text import SpeechToText
from config.settings import Settings, ROOT
from safety.emergency_stop import EmergencyStop
from utils.helpers import SafeError

APPS = [('Notepad', 'notepad'), ('Chrome', 'chrome'), ('VS Code', 'vscode')]
CONTROLS = [(f'{verb} {spoken}', tool, {'app_name': app})
    for verb, tool in [('Open', 'open_app'), ('Switch to', 'focus_app'), ('Close', 'close_app')]
    for spoken, app in APPS]
CONTROLS += [(f'Press {key.title()}', 'press_key', {'key': key}) for key in ['enter', 'escape', 'tab']]
CONTROLS += [(f'Press Control {key.upper()}', 'hotkey', {'keys': ['ctrl', key]}) for key in 'acv']
CONTROLS += [(f'Scroll {direction}', 'scroll', {'direction': direction, 'amount': 3}) for direction in ['down', 'up']]


@pytest.mark.parametrize('text,tool,args', CONTROLS)
def test_exact_command_language(text, tool, args):
    parsed = recognize(text)
    assert parsed.kind == LanguageKind.CONTROL
    plan = deterministic_v2(text)
    assert len(plan.actions) == 1 and plan.actions[0].tool == tool
    assert plan.actions[0].arguments == args
    assert classify(text, plan) == CommandKind.DETERMINISTIC
    provider, executor, router = Mock(), Mock(), Mock()
    brain = Brain(provider, router, EmergencyStop(), executor=executor)
    brain.handle(UserCommand(text))
    provider.generate_plan.assert_not_called()
    executor.execute.assert_called_once_with(plan, from_model=False, needs_prompt=False)


BAD = ['Open not bad', 'Open not pad', 'Open load pad', 'Open Nootpad', 'open.pad',
       'Open crawl', 'Open control', 'Switch to groan', 'Praises enter', 'Prey center',
       'Please enter', 'place enter', 'Praise enter', 'Enter.', 'I like Chrome',
       'Chrome is open', 'My notes are bad', 'Please enter the room', 'Scrolls are old',
       'Open Notepad or Chrome', 'Open', 'Press', 'Scroll sideways',
       'Open coffee and tea', 'Press unknown and unknown', 'Search for', 'Type']


@pytest.mark.parametrize('text', BAD)
def test_unresolved_or_unrelated_speech_cannot_execute_or_call_llm(text):
    interpreted = normalize_transcript(text)
    assert deterministic_v2(interpreted) is None
    assert classify(interpreted) == CommandKind.REPEAT
    provider, executor, router = Mock(), Mock(), Mock()
    with pytest.raises(SafeError, match='repeat'):
        Brain(provider, router, EmergencyStop(), executor=executor).handle(UserCommand(interpreted))
    provider.generate_plan.assert_not_called()
    executor.prepare.assert_not_called(); executor.execute.assert_not_called()
    router.execute.assert_not_called()


@pytest.mark.parametrize('verb,tool,key', [('Type', 'type_text', 'text'),
    ('Write', 'type_text', 'text'), ('Enter text', 'type_text', 'text'),
    ('Search for', 'search_web', 'query'), ('Search web for', 'search_web', 'query')])
@pytest.mark.parametrize('payload', ['hello from workspilote', 'open Notepad', 'press enter',
    'Case  and spacing!  ', 'Stop and Cancel', 'best restaurants near Ahmedabad'])
def test_payload_takes_precedence_over_embedded_commands(verb, tool, key, payload):
    text = verb + ' ' + payload
    normalized = normalize_transcript(text)
    assert normalized == text
    recognized = recognize(normalized)
    assert recognized.kind == LanguageKind.PAYLOAD
    plan = deterministic_v2(normalized)
    assert len(plan.actions) == 1
    assert plan.actions[0].tool == tool and plan.actions[0].arguments[key] == payload


@pytest.mark.parametrize('text', ['Stop', 'Cancel', 'Abort', 'STOP!'])
def test_stop_never_enters_planner_observer_or_executor(text):
    provider, executor, router, stop = Mock(), Mock(), Mock(), EmergencyStop()
    assert recognize(text).kind == LanguageKind.STOP
    assert classify(text) == CommandKind.DETERMINISTIC
    assert Brain(provider, router, stop, executor=executor).handle(UserCommand(text)) == []
    assert stop.stopped
    provider.generate_plan.assert_not_called(); executor.prepare.assert_not_called()
    executor.execute.assert_not_called(); router.execute.assert_not_called()


@pytest.mark.parametrize('text', ['Open Chrome and show the time', 'Summarize the document and compare the two sections',
                                  'Help me organize these notes'])
def test_genuine_semantic_goals_keep_complex_route(text):
    assert classify(text, deterministic_v2(text)) == CommandKind.COMPLEX


def test_grammar_app_names_cannot_expand_allowlist():
    from test_validator import make_router
    router, calls = make_router()  # Chrome allowed; Notepad not allowed here.
    with pytest.raises(SafeError, match='approved'):
        router.preflight(deterministic_v2('Open Notepad'))
    assert not calls
    supported = json.loads((ROOT / 'config/apps.json').read_text())
    assert set(ALIASES.values()) == set(supported)


def test_grammar_does_not_authorize_yellow():
    from test_validator import make_router
    router, calls = make_router(guard=Mock())
    router.confirmations.confirm.return_value = False
    Brain(Mock(), router, router.stop).handle(UserCommand('Press Control V'))
    assert not calls
    router.confirmations.confirm.assert_called_once()


def test_final_settings_defaults_and_environment_overrides(monkeypatch):
    for key in list(os.environ):
        if key.startswith('WHISPER_'): monkeypatch.delenv(key)
    with patch('dotenv.load_dotenv'):
        settings = Settings.load()
    assert settings.whisper_model == 'base.en'
    assert settings.whisper_cpu_threads == 2 and settings.whisper_temperatures == (0,)
    assert settings.whisper_num_workers == 1 and settings.whisper_beam_size == 5
    assert settings.whisper_timeout_seconds == 15 and settings.whisper_hotwords == ''
    assert not settings.whisper_warmup and not settings.whisper_condition_on_previous_text
    monkeypatch.setenv('WHISPER_CPU_THREADS', '4')
    monkeypatch.setenv('WHISPER_TEMPERATURES', '0,0.2')
    monkeypatch.setenv('WHISPER_HOTWORDS', ' ')
    monkeypatch.setenv('WHISPER_CONDITION_ON_PREVIOUS_TEXT', 'true')
    with patch('dotenv.load_dotenv'): overridden = Settings.load()
    assert overridden.whisper_cpu_threads == 4 and overridden.whisper_temperatures == (0,.2)
    assert overridden.whisper_hotwords == '' and overridden.whisper_condition_on_previous_text
    config = SpeechToText(settings=overridden).configuration()

    assert config["engine"] == "sherpa-onnx"
    assert config["cpu_threads"] == 4
    assert config["num_workers"] == 1

    # Legacy setting must not silently influence Parakeet decoding.
    assert "condition_on_previous_text" not in config


@pytest.mark.parametrize('workers', [0, 2, True])
def test_one_worker_remains_enforced(workers):
    with pytest.raises(ValueError, match='one STT'): Settings(whisper_num_workers=workers)


@pytest.mark.parametrize('env,value', [('WHISPER_NO_SPEECH_THRESHOLD', '.7'),
    ('WHISPER_LOGPROB_THRESHOLD', '-2'), ('WHISPER_COMPRESSION_THRESHOLD', '3')])
def test_environment_cannot_weaken_confidence(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with patch('dotenv.load_dotenv'), pytest.raises(ValueError, match='tightened'):
        Settings.load()


@pytest.mark.parametrize('flags', [['--command', 'Open Notepad'], ['--plan', 'Open Chrome'],
    ['--inspect'], ['--ground', 'Submit'], ['--download-model'], ['--doctor']])
def test_diagnostic_cli_cannot_select_action_or_setup_mode(flags):
    from main import main
    with patch('main.initialize_com') as com, pytest.raises(SystemExit) as error:
        main(['--transcribe-only'] + flags)
    assert error.value.code == 2
    com.assert_not_called()


def test_benchmark_records_payload_class_without_execution():
    from test_voice_reliability import make_runtime, AUDIO
    runtime = make_runtime(); runtime.transcribe_only = True; runtime.diagnostic_sink = Mock()
    runtime.transcriber.transcribe.return_value = 'Search for open Notepad  later.'
    runtime.transcriber.last_metrics = {'accepted': True, 'stt_ms': 12}
    runtime._transcribe(AUDIO)
    record = runtime.diagnostic_sink.call_args.args[0]
    assert record['route'] == 'DETERMINISTIC' and record['grammar'] == 'PAYLOAD'
    assert record['heard'] == 'Search for open Notepad  later.'
    runtime.brain.handle.assert_not_called()


def test_existing_now_search_keeps_literal_payload():
    text = 'now Search for hello  from workspilote.  '
    plan = deterministic_v2(normalize_transcript(text))
    assert plan.actions[0].tool == 'search_web'
    assert plan.actions[0].arguments['query'] == 'hello  from workspilote.  '


def test_control_syntax_spacing_does_not_change_payload():
    plan = deterministic_v2(normalize_transcript('Please   OPEN   VS   CODE.'))
    assert plan.actions[0].arguments == {'app_name': 'vscode'}
    assert recognize('Open VS-Code').kind == LanguageKind.UNRESOLVED


def test_parakeet_transcribes_without_whisper_decoder_context():
    import numpy as np
    from types import SimpleNamespace
    from unittest.mock import Mock

    # Create a fake Parakeet recognition stream
    stream = Mock()
    stream.result = SimpleNamespace(text="Open Chrome")

    # Create a fake Parakeet model
    model = Mock()
    model.create_stream.return_value = stream

    # Initialize Intentra's STT with Parakeet
    stt = SpeechToText(
        model=model,
        settings=Settings(
            whisper_condition_on_previous_text=True
        )
    )

    # Generate valid 16-kHz mono float32 test audio
    audio = np.full(
        16000,
        0.1,
        dtype=np.float32
    )

    # Test transcription
    result = stt.transcribe(audio)

    assert result == "Open Chrome"

    # Verify the Parakeet stream was created
    model.create_stream.assert_called_once()

    # Verify the decoder ran exactly once
    model.decode_stream.assert_called_once_with(stream)

    # Verify the original audio was passed correctly
    args = stream.accept_waveform.call_args.args

    assert args[0] == 16000
    np.testing.assert_array_equal(args[1], audio)

    # Verify transcription metrics
    assert stt.last_metrics["accepted"] is True
    assert stt.last_metrics["retry_count"] == 0

    # Verify no Whisper decoding interface was invoked
    model.transcribe.assert_not_called()


@pytest.mark.parametrize('text', ['Open Notepad or Chrome', 'Switch to VS Code or Notepad',
                                  'Press Enter or Escape', 'Press Control A or Control V'])
def test_explicit_alternatives_are_ambiguous_and_never_selected(text):
    assert recognize(text).kind == LanguageKind.AMBIGUOUS
    assert deterministic_v2(text) is None
    assert classify(text) == CommandKind.REPEAT
