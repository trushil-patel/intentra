import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pytest
from config.settings import Settings
from audio.hands_free import Segmenter, HandsFreeListener
from audio.push_to_talk import PushToTalkCapture
from audio.recorder import Recorder
from audio.segments import audio_metrics, usable_audio
from audio.speech_to_text import SpeechToText
from audio.command_normalizer import normalize_transcript
from safety.emergency_stop import EmergencyStop
from safety.confirmation import ConfirmationManager, Pending
from utils.helpers import SafeError

AUDIO = np.full(8000, .02, dtype=np.float32).tobytes()


def capture():
    recorder = Mock()
    recorder.finish.return_value = AUDIO
    submit = Mock()
    context = [0, None, False, 0]
    capture = PushToTalkCapture(recorder, submit, lambda:tuple(context), lambda:True,
        lambda old:old == tuple(context), display=lambda _:None)
    return capture, recorder, submit, context


def test_first_down_repeat_and_release_once():
    c,r,submit,_=capture()
    c.down(); c.down(); c.down()
    assert c.state=='RECORDING'
    r.start.assert_called_once()
    submit.assert_not_called()
    c.up(); c.up()
    r.finish.assert_called_once()
    submit.assert_called_once_with(AUDIO,(0,None,False,0))
    assert c.state=='TRANSCRIBING'
    c.completed()
    assert c.state=='READY'


def test_second_hold_has_new_identity():
    c,r,submit,_=capture()
    for _ in range(2): c.down(); c.up(); c.completed()
    assert c.utterance_id==2 and r.start.call_count==2 and submit.call_count==2


@pytest.mark.parametrize('audio', [b'', np.zeros(8000,dtype=np.float32).tobytes(), np.ones(16,dtype=np.float32).tobytes()], ids=['empty','silence','tap'])
def test_empty_tap_never_submits(audio):
    c,r,submit,_=capture(); r.finish.return_value=audio
    c.down(); c.up()
    submit.assert_not_called()
    assert c.state=='READY'


def test_cancel_while_held_discards_and_does_not_restart_on_repeat():
    c,r,submit,context=capture()
    c.down(); context[0]+=1; c.poll(20); c.down(); c.up()
    r.discard.assert_called_once()
    r.start.assert_called_once()
    submit.assert_not_called()


def test_tts_epoch_invalidates_entire_hold_even_after_tts_ends():
    c,r,submit,context=capture(); c.down(); context[3]+=1; c.up()
    submit.assert_not_called()
    r.discard.assert_called_once()


def test_busy_does_not_record_or_authorize():
    c,r,submit,_=capture(); c.allowed=lambda:False
    c.down(); c.up()
    r.start.assert_not_called(); submit.assert_not_called()


def test_hold_not_ended_by_long_pause():
    c,r,submit,_=capture()
    c.down(); c.poll(20)
    assert c.state=='RECORDING'
    submit.assert_not_called(); r.finish.assert_not_called()


def test_timeout_drains_current_hold():
    c,r,submit,_=capture(); now=[0.]; c.clock=lambda:now[0]
    c.down(); now[0]=21; c.poll(20); c.down(); c.up()
    submit.assert_not_called(); r.start.assert_called_once()


def test_recorder_detaches_fresh_buffers():
    class Stream:
        def __init__(self, **kwargs): self.callback=kwargs['callback']
        def start(self): pass
        def stop(self): pass
        def close(self): pass
        def abort(self): pass
    r=Recorder(stream_factory=Stream)
    first=np.full(4000,.02,dtype=np.float32).tobytes()
    second=np.full(4000,.04,dtype=np.float32).tobytes()
    r.start(); r._callback(first,4000,None,False); assert r.finish()==first
    r.start(); r._callback(second,4000,None,False); assert r.finish()==second
    assert not r.buffer


def test_pre_roll_and_last_phoneme_are_preserved():
    s=Segmenter(Settings())
    first=np.full(2048,.01,dtype=np.float32)
    last=np.full(2048,.07,dtype=np.float32)
    s.feed(first,False); s.feed(first,False)
    for _ in range(3): s.feed(last,True)
    result=None
    for _ in range(6): result=s.feed(np.zeros(2048,dtype=np.float32),False) or result
    audio=np.frombuffer(result[0],dtype=np.float32)
    assert np.array_equal(audio[:4096],np.concatenate([first,first]))
    assert np.array_equal(audio[4096:10240],np.concatenate([last]*3))
    assert len(audio)==11*2048


def test_pre_roll_configuration_is_block_quantized():
    s=Segmenter(Settings(vad_pre_roll_ms=384))
    assert s.pre.maxlen==3


def test_speech_with_internal_pause_remains_one_segment():
    s=Segmenter(Settings()); block=np.full(2048,.02,dtype=np.float32)
    for decision in [True]*3+[False]*3+[True]*3+[False]*5:
        assert s.feed(block,decision) is None
    assert s.feed(block,False) is not None


def test_queue_is_bounded_and_overflow_fails_closed():
    listener=HandsFreeListener(Settings(),Mock(),lambda:False,lambda:None,detector=Mock())
    for _ in range(9): listener._callback(bytes(8192),2048,None,False)
    assert listener.queue.qsize()==8 and listener.failed.is_set()


def test_suppressed_busy_audio_is_not_detected_or_queued_for_stt():
    listener=HandsFreeListener(Settings(),Mock(),lambda:True,lambda:(0,None,False),detector=Mock())
    listener._callback(bytes(8192),2048,None,False)
    thread=threading.Thread(target=listener._run); thread.start()
    time.sleep(.03); listener.closed.set(); thread.join(1)
    listener.detector.assert_not_called(); listener.utterance.assert_not_called()
    assert listener.draining_suppressed


@pytest.mark.parametrize('bad',[np.array([np.nan],dtype=np.float32).tobytes(),np.array([2.],dtype=np.float32).tobytes(),b'x'])
def test_invalid_audio_never_reaches_whisper(bad):
    with pytest.raises(SafeError): usable_audio(bad)


def test_audio_metrics_never_change_samples():
    original=AUDIO
    m=audio_metrics(original)
    assert original==AUDIO and m['duration_ms']==500 and abs(m['rms']-.02)<.00001
    assert set(m)=={'duration_ms','frames','rms','peak','clipped_fraction'}


@pytest.mark.parametrize('heard',['Open not bad.','place enter','Open','Notepad','works pilot version 3.','Press control','Switch to crumb'])
def test_uncertain_command_has_no_observation_or_model_or_execution(heard):
    from agent.brain import Brain
    from agent.executor import Executor
    from agent.context import UserCommand
    from test_validator import make_router
    router,calls=make_router(); observer=Mock(); provider=Mock()
    ex=Executor(router,observer)
    with pytest.raises(SafeError,match='Please repeat'):
        Brain(provider,router,router.stop,ex).handle(UserCommand(heard))
    observer.observe.assert_not_called(); provider.generate_plan.assert_not_called()
    assert not calls


@pytest.mark.parametrize('command',['Open Chrome','Open Notepad','Open VS Code','Press Enter','Press Control A','Scroll down','Scroll up','Switch to Chrome','Close Notepad','Type hello from VoxPilot'])
def test_simple_command_never_calls_model(command):
    from agent.brain import Brain
    from agent.context import UserCommand
    from test_validator import make_router
    router,_=make_router(); provider=Mock(); executor=Mock()
    Brain(provider,router,router.stop,executor).handle(UserCommand(command))
    provider.generate_plan.assert_not_called()
    assert executor.execute.call_args.args[0].actions


def test_genuine_complex_goal_keeps_planner():
    from agent.command_classifier import classify,CommandKind
    assert classify('Open Chrome and show the time')==CommandKind.COMPLEX
    assert classify('Summarize the document and compare the two sections')==CommandKind.COMPLEX


@pytest.mark.parametrize('text',['Type not bad  place enter.','Open Notepad and type not bad  place enter.','Type: Case, punctuation!'])
def test_normalizer_never_rewrites_literal_payload(text):
    assert normalize_transcript(text)==text


def test_syntax_only_normalization():
    assert normalize_transcript('  Open   Notepad.  ')=='Open Notepad.'
    assert normalize_transcript('Open not bad.')=='Open not bad.'
    assert normalize_transcript('place enter')=='place enter'


def test_stt_options_are_explicit_and_no_hint_is_authority():
    model = Mock()
    stream = Mock()

    stream.result = SimpleNamespace(text="Open Chrome")
    model.create_stream.return_value = stream

    stt = SpeechToText(
        model=model,
        settings=Settings(
            whisper_hotwords="Notepad, Chrome",
            whisper_cpu_threads=2,
        ),
    )

    audio = np.full(16000, 0.1, dtype=np.float32)

    assert stt.transcribe(audio) == "Open Chrome"

    config = stt.configuration()

    assert config["engine"] == "sherpa-onnx"
    assert config["cpu_threads"] == 2
    assert config["num_workers"] == 1

    # Legacy Whisper hotwords must not become authorization.
    assert "Notepad, Chrome" not in str(config)

    model.create_stream.assert_called_once()
    model.decode_stream.assert_called_once_with(stream)

    args = stream.accept_waveform.call_args.args

    assert args[0] == 16000
    np.testing.assert_array_equal(args[1], audio)

    assert stt.last_metrics["accepted"] is True
    assert stt.last_metrics["retry_count"] == 0


@pytest.mark.parametrize('overrides',[{'avg_logprob':float('nan')},{'compression_ratio':3.},{'no_speech_prob':.9},{'avg_logprob':-2.}])
def test_bad_confidence_fails_closed(overrides):
    values=dict(text='Open Chrome',avg_logprob=-.2,no_speech_prob=.1,compression_ratio=1.)
    values.update(overrides)
    model=Mock(); model.transcribe.return_value=(iter([SimpleNamespace(**values)]),None)
    stt=SpeechToText(model=model)
    with pytest.raises(SafeError):stt.transcribe(None)
    assert stt.last_metrics['accepted'] is False


def make_runtime(mode='push_to_talk'):
    from agent.runtime import Runtime
    brain,stt=Mock(),Mock()
    brain.router.confirmations=ConfirmationManager(key_reader=lambda:(False,False))
    stt.transcribe.return_value='Open Chrome'
    r=Runtime(Settings(voice_mode=mode),brain,stt,EmergencyStop())
    return r


def test_f8_busy_never_sets_emergency_latch():
    r=make_runtime(); r.busy.acquire(); r.enqueue('down')
    assert not r.stop.stopped and not r._ptt_allowed()
    r.busy.release()


def test_f8_can_record_yes_only_for_current_pending_confirmation():
    r=make_runtime(); r.busy.acquire()
    assert not r._ptt_allowed()
    r.brain.router.confirmations.pending=Pending('nonce','operation',time.monotonic()+30)
    assert r._ptt_allowed()
    assert r._voice_context()[1]=='nonce'
    r.busy.release()


def test_tts_held_f8_cannot_start_recording():
    r=make_runtime(); r.speaker.active.set(); r.recorder.start=Mock()
    r.ptt.down(); r.ptt.up()
    r.recorder.start.assert_not_called()


def test_valid_f8_hold_calls_stt_once_in_diagnostic_mode():
    r=make_runtime(); r.transcribe_only=True
    r.recorder.start=Mock(); r.recorder.finish=Mock(return_value=AUDIO)
    r._spawn=lambda fn,*args:fn(*args)
    r.ptt.down(); r.ptt.down()
    r.transcriber.transcribe.assert_not_called()
    r.ptt.up(); r.ptt.up()
    r.transcriber.transcribe.assert_called_once(); r.brain.handle.assert_not_called()


def test_cancelled_f8_buffer_never_transcribed():
    r=make_runtime(); r.recorder.start=Mock(); r.recorder.discard=Mock(); r.recorder.finish=Mock(return_value=AUDIO)
    r.ptt.down(); r.stop.stop(); r.ptt.up()
    r.transcriber.transcribe.assert_not_called(); r.brain.handle.assert_not_called()


def test_delayed_stt_error_cannot_relatch_after_cancel():
    r=make_runtime(); context=r._voice_context()
    def fail(_):
        r.stop.stop(); r.stop.resume(); raise RuntimeError('late failure')
    r.transcriber.transcribe.side_effect=fail
    r._transcribe(AUDIO,context)
    assert not r.stop.stopped
    r.brain.handle.assert_not_called()


def test_stale_transcript_cannot_approve_new_prompt():
    r=make_runtime(); r.transcriber.transcribe.return_value='Yes'
    r.brain.router.confirmations.pending=Pending('new','operation',time.monotonic()+30)
    r._transcribe(AUDIO,(r.stop.generation,'old',True,0))
    assert r.brain.router.confirmations._reply is None
    r.brain.handle.assert_not_called()


def test_f8_busy_event_cannot_start_recording_after_task_finishes():
    r=make_runtime(); r.busy.acquire(); r.enqueue('down'); r.busy.release()
    event,context,permitted=r.events.get_nowait()
    r.recorder.start=Mock(); r.ptt.down(context,permitted)
    r.recorder.start.assert_not_called()
    assert not r.stop.stopped


def test_stale_stop_transcript_cannot_cancel_replacement_task():
    r=make_runtime(); context=r._voice_context()
    def delayed(_):
        r.task_serial+=1
        return 'Stop'
    r.transcriber.transcribe.side_effect=delayed
    r._transcribe(AUDIO,context)
    assert not r.stop.stopped


def test_stale_voice_command_cannot_start_after_replacement_task():
    r=make_runtime(); old=r.task_serial; r.task_serial+=1
    r._process('Open Chrome',r.stop.generation,old)
    r.brain.handle.assert_not_called()


def test_ptt_mode_never_starts_hands_free_listener():
    from unittest.mock import patch
    r=make_runtime(); r.keys=Mock()
    with patch('audio.hands_free.HandsFreeListener') as listener:
        r.run_voice(check_only=True)
        listener.assert_not_called()
    r.transcriber.load.assert_called_once()


@pytest.mark.parametrize('command',['Disable firewall','Open C:\\random\\malware.exe'])
def test_f8_red_and_unknown_application_cannot_execute(command):
    from agent.runtime import Runtime
    from agent.brain import Brain
    from test_validator import make_router
    router,calls=make_router(); stt=Mock(); stt.transcribe.return_value=command
    r=Runtime(Settings(voice_mode='push_to_talk'),Brain(Mock(),router,router.stop),stt,router.stop)
    r.recorder.start=Mock(); r.recorder.finish=Mock(return_value=AUDIO)
    r._spawn=lambda fn,*args:fn(*args)
    r.ptt.down(); r.ptt.up()
    assert not calls
    router.confirmations.confirm.assert_not_called()


def test_f8_is_not_yellow_authorization():
    from agent.runtime import Runtime
    from agent.brain import Brain
    from test_validator import make_router
    router,calls=make_router(guard=Mock()); router.confirmations.confirm.return_value=False
    stt=Mock(); stt.transcribe.return_value='Press Enter'
    r=Runtime(Settings(voice_mode='push_to_talk'),Brain(Mock(),router,router.stop),stt,router.stop)
    r.recorder.start=Mock(); r.recorder.finish=Mock(return_value=AUDIO)
    r._spawn=lambda fn,*args:fn(*args)
    r.ptt.down(); r.ptt.up()
    assert not calls
    router.confirmations.confirm.assert_called_once()


def test_comparison_capture_requires_explicit_path_and_redacts(tmp_path):
    from audio.benchmark import ComparisonSession,PHRASES,load_samples,comparison_table
    path=tmp_path/'hf.jsonl'; completed=Mock()
    session=ComparisonSession(path,Settings(voice_mode='hands_free'),completed)
    for phrase in PHRASES:
        session.accept({'heard':'password=secretvalue' if phrase==PHRASES[0] else phrase,'error':None,'confidence':{'stt_ms':12},'segment':{}})
    session.close()
    completed.assert_called_once()
    assert 'secretvalue' not in path.read_text()
    samples=load_samples(path,'hands_free')
    assert len(samples)==10 and 'Not measured' in comparison_table(samples,{})
    with pytest.raises(SafeError,match='already exists'): ComparisonSession(path,Settings(),Mock())


def test_transcribe_only_comparison_sink_gets_no_desktop_actions():
    r=make_runtime(); r.transcribe_only=True; r.diagnostic_sink=Mock()
    r.transcriber.last_metrics={'stt_ms':1,'accepted':True}
    r._transcribe(AUDIO)
    r.diagnostic_sink.assert_called_once()
    assert r.diagnostic_sink.call_args.args[0]['heard']=='Open Chrome'
    r.brain.handle.assert_not_called()


def test_stale_queued_text_stop_cannot_cancel_new_task():
    r=make_runtime(); old=r.task_serial; r.task_serial+=1
    r._process('Stop',r.stop.generation,old)
    assert not r.stop.stopped
