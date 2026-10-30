"""Exact shortcut syntax and comparison-only failure wording regressions."""
import json
from unittest.mock import Mock
import pytest
from agent.command_classifier import classify, CommandKind
from agent.command_language import recognize, LanguageKind
from agent.planner import deterministic_v2
from agent.brain import Brain
from agent.context import UserCommand
from audio.command_normalizer import normalize_transcript
from audio.benchmark import ComparisonSession
from config.settings import Settings
from safety.emergency_stop import EmergencyStop
from utils.helpers import SafeError
from test_voice_reliability import make_runtime, AUDIO

FORMS = ['Control {key}', 'Ctrl {key}', 'Control-{key}', 'Ctrl-{key}', 'Control+{key}', 'Ctrl+{key}']


@pytest.mark.parametrize('key', list('acvfz'))
@pytest.mark.parametrize('form', FORMS)
@pytest.mark.parametrize('case', ['original', 'lower', 'upper'])
def test_supported_shortcut_forms(key, form, case):
    text = 'Press ' + form.format(key=key.upper())
    if case == 'lower': text = text.lower()
    if case == 'upper': text = text.upper()
    plan = deterministic_v2(normalize_transcript(text))
    assert recognize(text).kind == LanguageKind.CONTROL
    assert len(plan.actions) == 1
    assert plan.actions[0].tool == 'hotkey'
    assert plan.actions[0].arguments == {'keys': ['ctrl', key]}
    assert classify(text, plan) == CommandKind.DETERMINISTIC


@pytest.mark.parametrize('text', ['pReSs cTrL-a', 'Press Control + A', 'Press Ctrl - A', 'Press Ctrl+A.'])
def test_allowed_case_spacing_and_terminal_punctuation(text):
    assert deterministic_v2(text).actions[0].arguments == {'keys': ['ctrl', 'a']}


INVALID = ['Press Alt+A', 'Press Shift-A', 'Press Meta+A', 'Press Win+A',
    'Press Control+B', 'Press Ctrl+S', 'Press Ctrl+Enter', 'Press Ctrl+F1',
    'Press Ctrl++A', 'Press Ctrl--A', 'Press Ctrl+-A', 'Press Ctrl-+A',
    'Press Ctrl+A+', 'Press Ctrl-A-', 'Press Ctrl+', 'Press +A',
    'Press CtrlA', 'Press Ctrl/A', 'Press Ctrl:A', 'Press Ctrl+A+C',
    'Press Ctrl+Shift+A', 'Press Ctrl- A extra', 'Press Ctrl + + A']
PHONETIC = ['Praises enter', 'Prey center', 'Please enter', 'Open not bad', 'Open not pad',
    'Open load pad', 'Open Nootpad', 'Open crawl', 'Switch to grown', 'Blue is not bad']


@pytest.mark.parametrize('text', INVALID + PHONETIC)
def test_unresolved_syntax_and_phonetics_never_call_llm_or_execute(text):
    interpreted = normalize_transcript(text)
    assert deterministic_v2(interpreted) is None
    assert classify(interpreted) == CommandKind.REPEAT
    provider, router, executor = Mock(), Mock(), Mock()
    with pytest.raises(SafeError, match='repeat'):
        Brain(provider, router, EmergencyStop(), executor=executor).handle(UserCommand(interpreted))
    provider.generate_plan.assert_not_called()
    executor.prepare.assert_not_called(); executor.execute.assert_not_called()
    router.execute.assert_not_called()


@pytest.mark.parametrize('payload', ['Press ' + form.format(key='A') for form in FORMS] + ['open Notepad', 'Press Ctrl++A'])
def test_type_payload_remains_literal(payload):
    text = 'Type ' + payload
    assert normalize_transcript(text) == text
    plan = deterministic_v2(text)
    assert recognize(text).kind == LanguageKind.PAYLOAD
    assert len(plan.actions) == 1 and plan.actions[0].tool == 'type_text'
    assert plan.actions[0].arguments == {'text': payload}


@pytest.mark.parametrize('form', FORMS)
def test_equivalent_shortcut_does_not_bypass_yellow_confirmation(form):
    from test_validator import make_router
    router, calls = make_router(guard=Mock())
    router.confirmations.confirm.return_value = False
    provider = Mock()
    Brain(provider, router, router.stop).handle(UserCommand('Press ' + form.format(key='A')))
    assert not calls
    router.confirmations.confirm.assert_called_once()
    provider.generate_plan.assert_not_called()


def test_compare_stt_rejection_records_failure_before_next_prompt(tmp_path, capsys):
    runtime = make_runtime(); runtime.transcribe_only = True
    runtime.transcriber.transcribe.side_effect = SafeError('Speech was unclear; please repeat')
    runtime.transcriber.last_metrics = {'accepted': False, 'stt_ms': 1}
    path = tmp_path / 'samples.jsonl'
    session = ComparisonSession(path, Settings(), Mock())
    runtime.diagnostic_sink = session.accept
    try: runtime._transcribe(AUDIO)
    finally: session.close()
    output = capsys.readouterr().out
    assert 'please repeat' not in output.lower()
    assert 'Speech was unclear; sample recorded as failed.' in output
    assert output.index('sample recorded as failed') < output.index('[COMPARE 2/10]')
    assert 'say: Open Chrome' in output
    sample = json.loads(path.read_text())
    assert not sample['accepted'] and sample['route'] == 'REPEAT'
    assert sample['error'] == 'Speech was unclear; please repeat'  # Preserve diagnostic evidence.
    assert sample['expected'] == 'Open Notepad' and session.index == 1
    runtime.brain.handle.assert_not_called()


def test_compare_parser_rejection_advances_without_repeat_request(tmp_path, capsys):
    runtime = make_runtime(); runtime.transcribe_only = True
    runtime.transcriber.transcribe.return_value = 'Open not bad'
    runtime.transcriber.last_metrics = {'accepted': True, 'stt_ms': 1}
    session = ComparisonSession(tmp_path / 'samples.jsonl', Settings(), Mock())
    runtime.diagnostic_sink = session.accept
    try: runtime._transcribe(AUDIO)
    finally: session.close()
    output = capsys.readouterr().out
    assert '[PARSER] REPEAT' in output and 'please repeat' not in output.lower()
    assert output.index('sample recorded as failed') < output.index('[COMPARE 2/10]')
    runtime.brain.handle.assert_not_called()


@pytest.mark.parametrize('diagnostic_only', [False, True])
def test_repeat_wording_unchanged_outside_comparison(diagnostic_only, capsys):
    runtime = make_runtime(); runtime.transcribe_only = diagnostic_only
    runtime.transcriber.transcribe.side_effect = SafeError('Speech was unclear; please repeat')
    runtime.transcriber.last_metrics = {'accepted': False}
    runtime._transcribe(AUDIO)
    output = capsys.readouterr().out
    assert '[BLOCKED] Speech was unclear; please repeat' in output
    assert 'sample recorded as failed' not in output


def test_final_failed_comparison_sample_finishes_without_extra_prompt(tmp_path, capsys):
    completed = Mock()
    session = ComparisonSession(tmp_path / 'samples.jsonl', Settings(), completed)
    try:
        session.index = 9
        session.accept({'accepted': False, 'error': 'No speech detected; please repeat'})
    finally: session.close()
    output = capsys.readouterr().out
    assert 'sample recorded as failed' in output and 'session finished' in output
    assert 'please repeat' not in output.lower() and '[COMPARE 11/' not in output
    completed.assert_called_once()


def test_successful_comparison_has_no_failure_wording(tmp_path, capsys):
    session = ComparisonSession(tmp_path / 'samples.jsonl', Settings(), Mock())
    try: session.accept({'accepted': True, 'heard': 'Open Notepad', 'route': 'DETERMINISTIC'})
    finally: session.close()
    output = capsys.readouterr().out
    assert 'failed' not in output and '[COMPARE 2/10]' in output
