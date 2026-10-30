import threading
import time
from unittest.mock import Mock, patch
import numpy as np
import pytest
from config.settings import Settings
from safety.emergency_stop import EmergencyStop
from safety.confirmation import ConfirmationManager
from utils.helpers import Cancelled
from agent.schemas import Action
from audio.hands_free import Segmenter, DuplicateFilter


def test_cancel_wait_is_immediate_and_old_generation_never_resumes():
    stop = EmergencyStop()
    old = stop.generation
    started = threading.Event()
    finished = threading.Event()
    def waiting():
        started.set()
        with pytest.raises(Cancelled): stop.wait(30)
        finished.set()
    worker = threading.Thread(target=waiting)
    worker.start()
    assert started.wait(1)
    begin = time.perf_counter()
    stop.stop()
    assert finished.wait(.5)
    assert time.perf_counter() - begin < .5
    worker.join()
    stop.resume()
    with pytest.raises(Cancelled): stop.check_generation(old)
    stop.check_generation(stop.generation)


def test_stale_planner_result_cannot_execute_after_reset():
    from agent.brain import Brain
    from agent.context import UserCommand
    from test_validator import make_router
    router, calls = make_router()
    def delayed(*args):
        router.stop.stop()
        router.stop.resume()
        return '{"goal":"x","actions":[{"tool":"open_app","arguments":{"app_name":"notepad"}}]}'
    provider = Mock()
    provider.generate_plan.side_effect = delayed
    with pytest.raises(Cancelled): Brain(provider, router, router.stop).handle(UserCommand('Please get my editor ready'))
    assert not calls


def test_segmentation_noise_two_utterances_and_no_overlap():
    segmenter = Segmenter(Settings())
    block = np.zeros(2048, dtype=np.float32)
    assert all(segmenter.feed(block, False) is None for _ in range(30))
    # A single 128 ms spike is not enough speech.
    segmenter.feed(block, True)
    assert all(segmenter.feed(block, False) is None for _ in range(10))
    utterances = []
    for _ in range(2):
        for _ in range(4): segmenter.feed(block, True, ('task', 'nonce', False))
        for _ in range(10):
            result = segmenter.feed(block, False)
            if result: utterances.append(result)
    assert len(utterances) == 2
    assert all(item[1] == ('task', 'nonce', False) for item in utterances)


def test_overlong_speech_is_discarded():
    s = Segmenter(Settings(max_record_seconds=1))
    assert all(s.feed(np.zeros(2048, dtype=np.float32), True) is None for _ in range(8))
    assert not s.parts


def test_duplicate_expiry():
    clock = [0.]
    f = DuplicateFilter(clock=lambda: clock[0])
    assert f.accept('Open Chrome.')
    assert not f.accept('open chrome')
    clock[0] = 2.1
    assert f.accept('Open Chrome!')


def test_voice_yes_bound_to_current_nonce_and_one_use():
    manager = ConfirmationManager(key_reader=lambda: (False, False), display=lambda _: None)
    stop = EmergencyStop()
    values = []
    def confirm(): values.append(manager.confirm(Action('press_key', {'key': 'enter'}), 'field', stop))
    worker = threading.Thread(target=confirm)
    worker.start()
    deadline = time.monotonic() + 1
    while manager.pending is None and time.monotonic() < deadline: time.sleep(.001)
    assert manager.pending
    nonce = manager.pending.nonce
    assert not manager.respond('yes', 'old-nonce')
    assert manager.respond('yes', nonce)
    assert not manager.respond('yes', nonce)
    worker.join(1)
    assert values == [True]
    assert not manager.respond('yes', nonce)


def runtime(transcript):
    from agent.runtime import Runtime
    brain, stt = Mock(), Mock()
    brain.router.confirmations = ConfirmationManager(key_reader=lambda: (False,False))
    stt.transcribe.return_value = transcript
    r = Runtime(Settings(), brain, stt, EmergencyStop())
    r._spawn = Mock()
    return r


def test_stop_transcript_bypasses_busy_planner():
    r = runtime('Stop!')
    r.busy.acquire()
    r._transcribe(np.zeros(3200, dtype=np.float32).tobytes())
    assert r.stop.stopped
    r.brain.handle.assert_not_called()
    r._spawn.assert_not_called()
    r.busy.release()


def test_tts_and_cancelled_transcript_never_dispatch():
    for reason in ('tts', 'cancel'):
        r = runtime('Open Notepad')
        context = r._voice_context()
        if reason == 'tts': r.speaker.active.set()
        else: r.stop.stop()
        r._transcribe(np.zeros(3200, dtype=np.float32).tobytes(), context)
        r._spawn.assert_not_called()


def test_explicit_hands_free_activation_ignores_f8():
    from dataclasses import replace
    from agent.runtime import Runtime
    r = runtime('Open Chrome')
    r = Runtime(replace(r.settings, voice_mode='hands_free'), r.brain, r.transcriber, r.stop)
    r.keys.on_down()
    assert r.events.empty()
    assert not r.stop.stopped
    assert r.settings.voice_mode == 'hands_free'


def test_busy_speech_does_not_queue_future_action():
    r = runtime('Open Chrome')
    r._transcribe(np.zeros(3200, dtype=np.float32).tobytes(), (r.stop.generation, None, True))
    r._spawn.assert_not_called()


def test_exact_requested_flagship_is_deterministic():
    from agent.planner import deterministic_v2
    from agent.task_scope import TaskScope
    text = 'Open Chrome, go to Claude, ask it to explain WebRTC in simple words, wait until the response is finished, then put the response into Notepad.'
    plan = deterministic_v2(text)
    assert len(plan.actions) == 10
    assert plan.actions[3].arguments['text'] == 'explain WebRTC in simple words'
    assert TaskScope.from_user(text).transfer


def test_address_bar_alias_and_duplicate_submit():
    from agent.computer_state import ComputerState, WindowState, Element
    from vision.semantic import ScreenState
    from vision.grounding import ground
    screen = ScreenState.from_computer(ComputerState(WindowState(1,'chrome'), available=True,
        elements=(Element((1,), 'Edit', 'Address and search bar', scope='browser_chrome'), Element((2,), 'Button', 'Submit'), Element((3,), 'Button', 'Submit'))))
    assert ground(screen, 'address bar').selected
    result = ground(screen, 'Submit')
    assert len(result.candidates) == 2 and result.selected is None


def test_input_confirmation_exemption_needs_trusted_empty_selection():
    from safety.policy import ordinary_input
    from safety.ui_guard import Target
    action = Action('type_text', {'text':'hello'})
    assert not ordinary_input(action, Mock())
    assert ordinary_input(action, Target(1,1,(1,),(),None,'chrome', insertion_safe=True))
    assert not ordinary_input(Action('press_key', {'key':'enter'}), Target(1,1,(1,),(),None,'chrome', insertion_safe=True))
    assert not ordinary_input(action, Target(1,1,(1,),(),None,'notepad'))
    assert ordinary_input(action, Target(1,1,(1,),(),None,'notepad', insertion_safe=True))


def test_simple_app_path_requests_no_uia():
    from agent.executor import Executor
    from vision.engine import ObservationEngine
    from agent.computer_state import ComputerState
    from test_validator import make_router
    observer = Mock()
    observer.observe.return_value = ComputerState(available=True)
    router, _ = make_router()
    ex = Executor(router, ObservationEngine(observer))
    ex.prepare(plan=__import__('agent.planner', fromlist=['deterministic_v2']).deterministic_v2('Open Notepad'))
    observer.observe.assert_not_called()
    ex._observe(Action('open_app', {'app_name':'notepad'}))
    assert observer.observe.call_args.kwargs['level'] == 0


def test_accepted_launch_not_repeated_on_missing_window():
    from agent.executor import Executor
    from agent.schemas import Plan
    from agent.computer_state import ComputerState
    from test_validator import make_router
    router, _ = make_router()
    from safety.validator import Validator
    router.validator = Validator({'notepad': {'allowed': True}})
    router.registry.invoke = Mock(return_value={'status':'launch_requested','app':'notepad'})
    observer = Mock()
    observer.observe.return_value = ComputerState(available=True)
    ex = Executor(router, observer, verification_seconds=0)
    results = ex.execute(Plan('open', (Action('open_app', {'app_name':'notepad'}),)))
    assert results[-1].status != 'SUCCESS'
    router.registry.invoke.assert_called_once()


def test_new_stop_cannot_be_cleared_by_stale_input():
    token = EmergencyStop()
    old = token.generation
    token.stop()
    with pytest.raises(Cancelled): token.resume(expected_generation=old)
    assert token.stopped


def test_queued_command_is_not_revived_after_stop():
    r = runtime('Open Chrome')
    old = r.stop.generation
    r.stop.stop()
    r._process('Open Chrome', old)
    r.brain.handle.assert_not_called()
    assert r.stop.stopped


def test_pending_is_cleared_when_tts_fails():
    manager = ConfirmationManager(display=lambda _: None)
    manager.voice_prompt = Mock(side_effect=RuntimeError('TTS unavailable'))
    with pytest.raises(RuntimeError): manager.confirm(Action('press_key', {'key':'enter'}), None, EmergencyStop())
    assert manager.pending is None


def test_notepad_enter_needs_line_break_evidence():
    from dataclasses import replace
    from agent.computer_state import ComputerState, WindowState
    from agent.verifier import Verifier, VerificationStatus
    from agent.router import Result
    before = ComputerState(WindowState(1,'notepad'), available=True, focused_id=(1,),
        focused_text_digest='before', focused_text_length=5, focused_line_breaks=0)
    after = replace(before, focused_text_digest='after', focused_text_length=6, focused_line_breaks=1)
    action = Action('press_key', {'key':'enter'})
    assert Verifier().verify(action, Result('press_key','SUCCESS'), before, after).status == VerificationStatus.VERIFIED
    assert Verifier().verify(action, Result('press_key','SUCCESS'), before, replace(after, focused_line_breaks=0)).status != VerificationStatus.VERIFIED


def test_overlong_utterance_drains_until_silence():
    s = Segmenter(Settings(max_record_seconds=1))
    block=np.zeros(2048, dtype=np.float32)
    assert all(s.feed(block, True) is None for _ in range(100))
    assert s.draining
    assert all(s.feed(block, False) is None for _ in range(6))
    assert not s.draining


def test_real_listener_queue_segments_without_keyboard():
    from audio.hands_free import HandsFreeListener
    result=[]
    class Stream:
        def __init__(self, **kwargs): self.callback=kwargs['callback']
        def start(self): pass
        def abort(self): pass
        def close(self): pass
    decisions=iter([True]*4 + [False]*6)
    listener=HandsFreeListener(Settings(), lambda *args: result.append(args), lambda:False,
        lambda:('task',None,False), detector=lambda _:next(decisions), stream_factory=Stream)
    listener.start()
    try:
        for _ in range(10):
            listener._callback(np.zeros(2048,dtype=np.float32).tobytes(),2048,None,False)
            time.sleep(.01)
        deadline=time.monotonic()+1
        while not result and time.monotonic()<deadline: time.sleep(.005)
        assert len(result)==1 and not listener.failed.is_set()
    finally: listener.close()


def test_listener_drops_audio_captured_during_tts():
    from audio.hands_free import HandsFreeListener
    listener=HandsFreeListener(Settings(), Mock(), lambda:True, lambda:None, detector=Mock())
    thread=threading.Thread(target=listener._run)
    thread.start()
    try:
        listener._callback(np.zeros(2048,dtype=np.float32).tobytes(),2048,None,False)
        time.sleep(.02)
    finally:
        listener.closed.set()
        thread.join(1)
    listener.detector.assert_not_called()
    listener.utterance.assert_not_called()


def test_local_injection_fixture_is_only_summary_data():
    from pathlib import Path
    from agent.computer_state import ComputerState, Element
    from agent.brain import Brain
    from agent.context import UserCommand
    from agent.executor import Executor
    from vision.ocr import ScreenReader
    from tools.registry import Registry, SPECS
    from test_validator import make_router
    content=(Path(__file__).parent/'fixtures/injection.html').read_text(encoding='utf-8-sig')
    observer=Mock()
    observer.observe.return_value=ComputerState(available=True,elements=(Element((1,),'Text',content),))
    reader=ScreenReader(observer,Mock(),EmergencyStop())
    calls=[]
    def dispatch(name, **kwargs):
        calls.append(name)
        assert name=='summarize_page'
        return reader.summarize_page()
    registry=Registry({name:(lambda n=name,**kw:dispatch(n,**kw)) for name in SPECS})
    router,_=make_router(registry=registry)
    provider=Mock()
    results=Brain(provider,router,router.stop,Executor(router,observer,verification_seconds=0)).handle(UserCommand('Summarize this page'))
    assert calls==['summarize_page'] and results[-1].status=='SUCCESS'
    provider.generate_plan.assert_not_called()
