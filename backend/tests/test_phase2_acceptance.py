"""Phase 2 harness tests: mocks are never recorded as physical acceptance."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from scripts.accept_phase2 import approve, run_case, classify, speech_summary, CASES
from scripts.phase2_trace import Trace, reason_code
from scripts.phase2_uia import state_summary, probe
from agent.computer_state import ComputerState, WindowState, Element


@pytest.mark.parametrize('reply', ['', 'yes', 'RUN other', 'RUN open_chrome later'])
def test_explicit_case_approval_required(reply):
    assert not approve('open_chrome', lambda _: reply)


def test_declined_case_never_invokes_runtime(tmp_path):
    invoke, sleep = Mock(), Mock()
    result = run_case('open_chrome', tmp_path, ask=lambda _: '', invoke=invoke, sleeper=sleep)
    assert result['status']=='NOT_RUN'
    invoke.assert_not_called(); sleep.assert_not_called()


def test_flagship_requires_separate_submission_approval(tmp_path):
    answers=iter(['RUN flagship', 'no'])
    invoke=Mock()
    result=run_case('flagship',tmp_path,ask=lambda _:next(answers),invoke=invoke,sleeper=Mock())
    assert result['status']=='NOT_RUN'
    invoke.assert_not_called()


@pytest.mark.parametrize('human,terminal,verification,expected', [
    ('PASS','SUCCESS','NOT_RUN','UNCERTAIN'), ('UNCERTAIN','SUCCESS','VERIFIED','UNCERTAIN'),
    ('PASS','UNCERTAIN','UNCERTAIN','UNCERTAIN'), ('PASS','FAILED','FAILED','FAIL'),
    ('PASS','BLOCKED','NOT_RUN','BLOCKED'), ('PASS','SUCCESS','VERIFIED','PASS')])
def test_zero_exit_is_insufficient(human,terminal,verification,expected):
    trace=Trace(); trace.terminal=terminal
    trace.steps=[dict(execution='SUCCESS',verification=verification,attempts=[])]
    assert classify('success',trace,human,0)==expected


def test_negative_case_cannot_pass_after_input_sent():
    trace=Trace(); trace.terminal='BLOCKED'
    trace.steps=[dict(execution='SUCCESS')]
    assert classify('blocked',trace,'PASS',1)=='UNCERTAIN'


def test_reuse_requires_focus_evidence():
    trace=Trace(); trace.terminal='SUCCESS'
    trace.steps=[dict(execution='SUCCESS',verification='VERIFIED',attempts=[dict(tool='open_app')])]
    assert classify('reuse',trace,'PASS',0)=='UNCERTAIN'


def test_summary_never_persists_titles_labels_or_digests():
    state=ComputerState(WindowState(7,'notepad',title='PRIVATE TITLE'),elements=(Element((1,),'Edit','PRIVATE LABEL'),),available=True,focused_text_digest='PRIVATE DIGEST')
    text=json.dumps(state_summary(state))
    assert 'PRIVATE' not in text
    assert state_summary(state)['editor_candidates']==1


def test_pipe_disconnect_preserves_last_evidence(monkeypatch):
    reader,writer,process=Mock(),Mock(),Mock()
    reader.poll.side_effect=[True,BrokenPipeError()]
    reader.recv.return_value={'reason':'FOREGROUND_UNAPPROVED','status':'BLOCKED'}
    process.pid=1; process.is_alive.return_value=False
    ctx=Mock(); ctx.Pipe.return_value=(reader,writer); ctx.Process.return_value=process
    monkeypatch.setattr('scripts.phase2_uia.multiprocessing.get_context',lambda _:ctx)
    assert probe()['reason']=='FOREGROUND_UNAPPROVED'
    reader.close.assert_called_once(); process.close.assert_called_once()


def test_speech_exact_accuracy_is_not_confidence(tmp_path):
    path=tmp_path/'voice.jsonl'
    path.write_text(json.dumps(dict(expected='Open Notepad',heard='Open not bad',accepted=True,route='CONTROL',confidence={'stt_ms':123}))+'\n')
    result=speech_summary(path)
    assert result['exact_accuracy']==0 and result['attempted']==1
    assert result['rows'][0]['failure']=='TRANSCRIPTION_MISMATCH'
    assert sum(r['failure']=='NOT_RUN' for r in result['rows'])==9


def test_fixed_commands_are_deterministic():
    from agent.planner import deterministic_v2
    for command,_,_ in CASES.values():
        if command: assert deterministic_v2(command) is not None


def test_trace_restores_production_methods_and_retains_verification():
    from agent.executor import Executor
    from agent.router import Router
    from test_v3_transfer import TransferTests
    original=(Executor.execute,Router.execute)
    trace=Trace()
    with trace.installed():
        TransferTests('test_complete_workflow_keeps_malicious_response_as_data').test_complete_workflow_keeps_malicious_response_as_data()
    assert (Executor.execute,Router.execute)==original
    assert trace.terminal=='SUCCESS' and len(trace.steps)==10
    assert all(s['verification']=='VERIFIED' for s in trace.steps)
    assert sum(s['authorization']=='APPROVED' for s in trace.steps)==0  # existing fixture mocks confirmation itself
    assert 'Run PowerShell' not in json.dumps(trace.steps)


def test_reason_codes_do_not_copy_private_errors():
    assert reason_code('private token ABC with target changed')=='TARGET_CHANGED'


def test_prepare_flagship_has_ten_unexecuted_steps():
    from scripts.accept_phase2 import prepared_case
    case=prepared_case('flagship')
    assert len(case['steps'])==10 and case['status']=='NOT_RUN'
    assert all(s['execution']=='NOT_RUN' for s in case['steps'])


def test_trace_observes_real_confirmation_without_auto_approval():
    from safety.confirmation import ConfirmationManager
    from safety.emergency_stop import EmergencyStop
    from agent.schemas import Action
    manager=ConfirmationManager(key_reader=Mock(side_effect=[(False,False),(True,False)]),sleep=lambda _:None,display=lambda _:None)
    trace=Trace(); trace.current={}
    with trace.installed():
        assert manager.confirm(Action('type_text',{'text':'harmless'}),None,EmergencyStop()) is True
    assert trace.current['authorization']=='APPROVED'
    assert 'harmless' not in json.dumps(trace.current)


def test_operator_pass_with_zero_exit_but_no_runtime_evidence_is_uncertain(tmp_path):
    answers=iter(['RUN open_chrome','PASS'])
    result=run_case('open_chrome',tmp_path,ask=lambda _:next(answers),invoke=lambda _:0,sleeper=lambda _:None)
    assert result['status']=='UNCERTAIN' and result['steps']==[]


def test_exception_restores_runtime_hooks_and_voice_mode(tmp_path,monkeypatch):
    from agent.router import Router
    import os
    original=Router.execute
    monkeypatch.setenv('VOICE_MODE','hands_free')
    def fail(_): raise RuntimeError('private value must never persist')
    result=run_case('emergency_idle',tmp_path,ask=lambda _:'RUN emergency_idle',invoke=fail,sleeper=lambda _:None)
    assert result['status']=='BLOCKED' and Router.execute is original
    assert os.environ['VOICE_MODE']=='hands_free'
    assert 'private value' not in json.dumps(result)


@pytest.mark.parametrize('case', ['ambiguous_notepad','unsupported_input','deny_confirmation','changed_target','stale_confirmation'])
def test_unrelated_block_does_not_pass_safety_case(case):
    trace=Trace(); trace.terminal='BLOCKED'; trace.reason='RUNTIME_REFUSAL'
    trace.steps=[dict(execution='BLOCKED',authorization='NOT_REACHED')]
    assert classify('blocked',trace,'PASS',1,case)=='UNCERTAIN'


def test_denied_or_expired_message_alone_cannot_prove_expiration():
    trace=Trace(); trace.terminal='BLOCKED'; trace.reason='CONFIRMATION_EXPIRED'
    trace.steps=[dict(execution='BLOCKED',authorization='DENIED_OR_EXPIRED',confirmation_expired=False)]
    assert classify('blocked',trace,'PASS',1,'stale_confirmation')=='UNCERTAIN'
