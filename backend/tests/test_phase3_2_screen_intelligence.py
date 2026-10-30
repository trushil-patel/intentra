from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest

from agent.computer_state import ComputerState, Element, WindowState
from agent.schemas import Action
from agent.verifier import Verifier, VerificationStatus
from safety.emergency_stop import EmergencyStop
from utils.helpers import SafeError
from vision.semantic import element_identity


def state(elements, *, app='explorer', handle=7, complete=True, available=True):
    window=WindowState(handle,app)
    return ComputerState(window if available else None,(window,),tuple(elements),
                         available=available,elements_complete=complete)


def verify(before,after,detail,tool='click_element',operation_args=None):
    args=operation_args or {'element_id':'last'}
    return Verifier().verify(Action(tool,args),NS(status='SUCCESS',detail=detail),before,after)


def detail(element,operation='activate'):
    return {'runtime_id':element.runtime_id,'target_identity':element_identity(element),
            'operation':operation,'control_type':element.type,
            'requested_state':True if operation=='check' else False if operation=='uncheck' else None}


def test_tab_selection_verified_with_runtime_id():
    e=Element((1,),'TabItem','View')
    assert verify(state((e,)),state((replace(e,selected=True),)),detail(e)).status==VerificationStatus.VERIFIED


def test_provider_selected_state_is_authoritative():
    e=Element((1,),'TabItem','View',selected=False)
    result=verify(state((e,)),state((replace(e,selected=True),)),detail(e))
    assert result.evidence=='SELECTION_OBSERVED'


def test_visible_change_without_selection_is_failed():
    e=Element((1,),'TabItem','View')
    other=Element((2,),'Button','Other')
    result=verify(state((e,)),state((e,other)),detail(e))
    assert result.status==VerificationStatus.FAILED and result.evidence=='POST_ACTION_SELECTION_NOT_OBSERVED'


def test_select_return_without_state_change_fails():
    e=Element((1,),'TabItem','View')
    assert verify(state((e,)),state((e,)),detail(e)).status==VerificationStatus.FAILED


def test_permitted_state_change_keeps_identity():
    e=Element((),'TabItem','View',bounds=(1,2,3,4))
    assert element_identity(e)==element_identity(replace(e,selected=True))


def test_runtime_absent_uses_unique_semantic_identity():
    e=Element((),'TabItem','View',bounds=(1,2,3,4))
    result=verify(state((e,)),state((replace(e,selected=True),)),detail(e))
    assert result.status==VerificationStatus.VERIFIED


def test_multiple_semantic_targets_fail():
    e=Element((),'TabItem','View',bounds=(1,2,3,4))
    result=verify(state((e,)),state((replace(e,selected=True),replace(e,selected=True))),detail(e))
    assert result.evidence=='POST_ACTION_TARGET_AMBIGUOUS'


def test_foreground_change_has_precise_reason():
    e=Element((1,),'TabItem','View')
    result=verify(state((e,)),state((e,),handle=8),detail(e))
    assert result.evidence=='POST_ACTION_WINDOW_CHANGED'


def test_modal_during_verification_is_uncertain():
    e=Element((1,),'TabItem','View')
    modal=Element((2,),'Window','Confirm',modal=True)
    result=verify(state((e,)),state((e,modal)),detail(e))
    assert result.status==VerificationStatus.UNCERTAIN and result.evidence=='POST_ACTION_MODAL_DIALOG'


def test_incomplete_observation_is_uncertain():
    e=Element((1,),'TabItem','View')
    assert verify(state((e,)),state((e,),complete=False),detail(e)).evidence=='POST_ACTION_OBSERVATION_INCOMPLETE'


def test_unavailable_observation_is_uncertain():
    e=Element((1,),'TabItem','View')
    assert verify(state((e,)),state((),available=False),detail(e)).evidence=='POST_ACTION_OBSERVATION_UNAVAILABLE'


@pytest.mark.parametrize('operation,wanted', [('check',True),('uncheck',False)])
def test_checkbox_state_verification(operation,wanted):
    e=Element((1,),'CheckBox','Remember',checked=not wanted)
    result=verify(state((e,)),state((replace(e,checked=wanted),)),detail(e,operation),'interact_element',
                  {'element_id':'last','operation':operation})
    assert result.status==VerificationStatus.VERIFIED


@pytest.mark.parametrize('operation,wanted', [('expand','expanded'),('collapse','collapsed')])
def test_tree_state_verification(operation,wanted):
    e=Element((1,),'TreeItem','Navigation',expanded='collapsed' if operation=='expand' else 'expanded')
    result=verify(state((e,)),state((replace(e,expanded=wanted),)),detail(e,operation),'interact_element',
                  {'element_id':'last','operation':operation})
    assert result.status==VerificationStatus.VERIFIED


def test_focus_verification():
    e=Element((1,),'Edit','Search')
    result=verify(state((e,)),state((replace(e,focused=True),)),detail(e,'focus'),'interact_element',
                  {'element_id':'last','operation':'focus'})
    assert result.evidence=='FOCUS_OBSERVED'


def test_known_invoke_effect_target_removed():
    e=Element((1,),'Button','Dismiss')
    assert verify(state((e,)),state(()),detail(e)).status==VerificationStatus.VERIFIED


def test_unknown_button_effect_is_uncertain():
    e=Element((1,),'Button','Settings')
    result=verify(state((e,)),state((e,)),detail(e))
    assert result.status==VerificationStatus.UNCERTAIN and result.evidence=='UNSUPPORTED_VERIFICATION_CONTRACT'


def test_single_pass_resolver_checks_foreground_twice():
    from tools.element_tools import resolve_reference
    from vision.semantic import ScreenState
    e=Element((),'TabItem','View',bounds=(1,2,3,4))
    reference=ScreenState.from_computer(state((e,))).elements[0]
    windows=Mock(); active=Mock(handle=7); windows.active.return_value=active; windows.app_of.return_value='explorer'
    wrapper=Mock()
    with patch('tools.element_tools.visible_wrappers',return_value=[(e,wrapper)]) as traversal:
        assert resolve_reference(windows,reference)[1] is wrapper
    traversal.assert_called_once_with(windows)
    assert windows.active.call_count==2


def test_worker_timeout_cleanup_coverage_retained():
    from test_v2_native_privacy import ObserverTimeoutTests
    ObserverTimeoutTests('test_observer_timeout_returns_unknown_and_terminates').test_observer_timeout_returns_unknown_and_terminates()


def test_native_recheck_boundary_retained():
    from tools.native import NativeBridge
    bridge=NativeBridge({},EmergencyStop()); bridge.call=Mock(return_value='target')
    action=Action('click_element',{'element_id':'last'})
    bridge.recheck(action,'target')
    bridge.call.assert_called_once_with('recheck',action,'target')


def test_downloads_navigation_registration_retained():
    from tools.registry import SPECS
    assert 'navigate_explorer_folder' in SPECS


def test_confirmation_and_stale_safety_retained():
    from vision.semantic import ScreenState
    e=Element((1,),'TabItem','View')
    screen=ScreenState.from_computer(state((e,)))
    with pytest.raises(SafeError,match='reference age exceeded'):
        screen.resolve(screen.elements[0],now=screen.elements[0].observed_at+46,max_age=45,strict_state=False)


def test_deterministic_timing_span_emits_only_stage(monkeypatch,capsys):
    from utils.performance import measure
    monkeypatch.setenv('VOXPILOT_PERF','true')
    with patch('utils.performance.time.perf_counter',side_effect=[1.0,1.025]):
        with measure('uia_interaction'):
            pass
    assert capsys.readouterr().out.strip()=='[PERF] uia_interaction = 25.00 ms'


def test_cross_application_identity_isolation():
    e=Element((),'TabItem','View',bounds=(1,2,3,4))
    result=verify(state((e,),app='explorer'),state((e,),app='chrome'),detail(e))
    assert result.evidence=='POST_ACTION_WINDOW_CHANGED'


def _target():
    e=Element((),'TabItem','View',bounds=(10,10,50,30),automation_id='')
    return e, {'handle':7,'app':'explorer','type':'TabItem','name':'View',
               'bounds':e.bounds,'automation_id':'','runtime_id':(),
               'target_identity':element_identity(e),'operation':'activate','control_type':'TabItem'}


def _target_wrapper(selected=True, name='View', bounds=(10,10,50,30)):
    info=NS(visible=True,enabled=True,control_type='TabItem',name=name,automation_id='',
            runtime_id=(),rectangle=NS(left=bounds[0],top=bounds[1],right=bounds[2],bottom=bounds[3]),
            element=NS(CurrentHasKeyboardFocus=False))
    wrapper=Mock(element_info=info)
    wrapper.iface_selection_item.CurrentIsSelected=selected
    return wrapper


def _target_windows(candidates):
    active=Mock(handle=7)
    active.descendants.return_value=candidates
    windows=Mock()
    windows.active.return_value=active
    windows.app_of.return_value='explorer'
    window=WindowState(7,'explorer')
    metadata=ComputerState(window,(window,),available=True,elements_complete=False)
    return windows,active,metadata


def test_expanded_global_tree_can_use_complete_target_query():
    from vision.observer import collect_target
    e,target=_target()
    windows,active,metadata=_target_windows([_target_wrapper(True)])
    with patch('vision.observer._scoped_windows',return_value=(metadata,active)),          patch('vision.observer.provider_owned',return_value=True):
        after=collect_target(windows,target)
    assert not after.elements_complete and after.target_verification_complete
    assert after.elements[0].selected
    assert verify(state((e,)),after,target).status==VerificationStatus.VERIFIED


def test_more_than_200_target_candidates_fails_safely():
    from vision.observer import collect_target
    _,target=_target()
    windows,active,metadata=_target_windows([_target_wrapper() for _ in range(201)])
    with patch('vision.observer._scoped_windows',return_value=(metadata,active)):
        after=collect_target(windows,target)
    assert not after.target_verification_complete
    assert after.limit_reached=='TARGET_CANDIDATE_LIMIT'


def test_target_present_does_not_override_incomplete_global_observation_without_complete_query():
    e,target=_target()
    after=state((replace(e,selected=True),),complete=False)
    result=verify(state((e,)),after,target)
    assert result.status==VerificationStatus.UNCERTAIN
    assert result.evidence.startswith('POST_ACTION_OBSERVATION_INCOMPLETE')


def test_duplicate_exact_target_anywhere_in_root_is_ambiguous():
    from vision.observer import collect_target
    _,target=_target()
    windows,active,metadata=_target_windows([_target_wrapper(),_target_wrapper()])
    with patch('vision.observer._scoped_windows',return_value=(metadata,active)),          patch('vision.observer.provider_owned',return_value=True):
        after=collect_target(windows,target)
    assert after.target_verification_complete and len(after.elements)==2
    assert after.provider_failure=='POST_ACTION_TARGET_AMBIGUOUS'


def test_target_query_rejects_foreground_change():
    from vision.observer import collect_target
    _,target=_target()
    windows,active,metadata=_target_windows([_target_wrapper()])
    changed=Mock(handle=8)
    windows.active.side_effect=[changed]
    with patch('vision.observer._scoped_windows',return_value=(metadata,active)),          patch('vision.observer.provider_owned',return_value=True):
        after=collect_target(windows,target)
    assert after.provider_failure=='POST_ACTION_FOREGROUND_CHANGED'


def test_target_provider_failure_is_diagnostic_and_inconclusive():
    from vision.observer import collect_target
    _,target=_target()
    windows,active,metadata=_target_windows([])
    active.descendants.side_effect=RuntimeError('provider')
    with patch('vision.observer._scoped_windows',return_value=(metadata,active)):
        after=collect_target(windows,target)
    assert after.provider_failure=='POST_ACTION_TARGET_QUERY_FAILED'
    assert not after.target_verification_complete


def test_complete_target_verification_reports_selected_false_as_failure():
    e,target=_target()
    after=replace(state((e,),complete=False),target_verification_complete=True)
    result=verify(state((e,)),after,target)
    assert result.status==VerificationStatus.FAILED
    assert result.evidence=='POST_ACTION_SELECTION_NOT_OBSERVED'


def test_full_observation_diagnostics_record_element_limit():
    from test_phase3_uia_observation import _accessible_fixture
    from vision.observer import collect
    import sys
    overflowing=[Mock() for _ in range(200)]
    windows,user,modules=_accessible_fixture(overflowing)
    with patch('ctypes.WinDLL',return_value=user),patch.dict(sys.modules,modules):
        observed=collect(windows)
    assert not observed.elements_complete
    assert observed.limit_reached=='ELEMENT_LIMIT'
    assert observed.visited_count>=1 and observed.traversal_ms>=0


def test_cancelled_target_observer_cleanup_coverage_retained():
    from test_v2_native_privacy import ObserverTimeoutTests
    ObserverTimeoutTests('test_observer_timeout_returns_unknown_and_terminates').test_observer_timeout_returns_unknown_and_terminates()


def test_downloads_navigation_still_registered_after_targeted_verification():
    from tools.registry import SPECS
    assert SPECS['navigate_explorer_folder'].arguments=={'folder':str}


def _observer_with_reply(reply):
    reader, writer, process, context = Mock(), Mock(), Mock(pid=123), Mock()
    reader.poll.return_value = True
    reader.recv.return_value = reply
    process.is_alive.return_value = False
    context.Pipe.return_value = reader, writer
    context.Process.return_value = process
    return context, process


def test_verification_worker_timeout_has_precise_diagnostic():
    from test_v2_native_privacy import hung_worker
    from vision.observer import Observer
    with patch('vision.observer._worker', hung_worker):
        observed = Observer({}, EmergencyStop(), timeout=.1).observe(target={'type':'TabItem'})
    assert not observed.available
    assert observed.provider_failure == 'VERIFICATION_WORKER_TIMEOUT'


def test_verification_worker_crash_has_precise_diagnostic():
    from vision.observer import Observer
    context, process = _observer_with_reply(None)
    reader = context.Pipe.return_value[0]
    reader.poll.return_value = False
    process.is_alive.return_value = False
    with patch('vision.observer.multiprocessing.get_context', return_value=context):
        observed = Observer({}, EmergencyStop()).observe(target={'type':'TabItem'})
    assert observed.provider_failure == 'VERIFICATION_WORKER_CRASH'


def test_verification_worker_invalid_result_is_rejected():
    from vision.observer import Observer
    context, _ = _observer_with_reply({'available': True})
    with patch('vision.observer.multiprocessing.get_context', return_value=context):
        observed = Observer({}, EmergencyStop()).observe(target={'type':'TabItem'})
    assert observed.provider_failure == 'POST_ACTION_RESULT_INVALID'


def test_verification_worker_start_failure_is_precise():
    from vision.observer import Observer
    context, process = _observer_with_reply(None)
    process.start.side_effect = OSError('spawn failed')
    with patch('vision.observer.multiprocessing.get_context', return_value=context):
        observed = Observer({}, EmergencyStop()).observe(target={'type':'TabItem'})
    assert observed.provider_failure == 'VERIFICATION_WORKER_START_FAILED'


def test_verification_com_initialization_failure_is_returned():
    from vision.observer import _worker
    connection = Mock()
    with patch('utils.com.configure_com', side_effect=RuntimeError('COM unavailable')):
        _worker(connection, {}, None, target={'type':'TabItem'})
    observed = connection.send.call_args.args[0]
    assert observed.provider_failure == 'VERIFICATION_COM_INITIALIZATION_FAILED'
    assert not observed.available


def test_target_selected_state_unavailable_is_uncertain():
    from vision.observer import collect_target
    e, target = _target()
    wrapper = _target_wrapper()
    type(wrapper.iface_selection_item).CurrentIsSelected = property(
        lambda self: (_ for _ in ()).throw(RuntimeError('state unavailable')))
    windows, active, metadata = _target_windows([wrapper])
    with patch('vision.observer._scoped_windows', return_value=(metadata, active)),          patch('vision.observer.provider_owned', return_value=True):
        after = collect_target(windows, target)
    result = verify(state((e,)), after, target)
    assert after.target_verification_complete
    assert result.status == VerificationStatus.UNCERTAIN
    assert result.evidence == 'POST_ACTION_STATE_UNAVAILABLE'


def test_target_missing_has_precise_diagnostic():
    from vision.observer import collect_target
    e, target = _target()
    windows, active, metadata = _target_windows([])
    with patch('vision.observer._scoped_windows', return_value=(metadata, active)):
        after = collect_target(windows, target)
    assert after.target_verification_complete
    assert verify(state((e,)), after, target).evidence == 'POST_ACTION_TARGET_MISSING'


def test_provider_failure_can_be_retried_with_fresh_complete_query():
    from vision.observer import collect_target
    e, target = _target()
    windows, active, metadata = _target_windows([_target_wrapper(True)])
    active.descendants.side_effect = [RuntimeError('temporary'), [_target_wrapper(True)]]
    with patch('vision.observer._scoped_windows', return_value=(metadata, active)),          patch('vision.observer.provider_owned', return_value=True):
        first = collect_target(windows, target)
        second = collect_target(windows, target)
    assert first.provider_failure == 'POST_ACTION_TARGET_QUERY_FAILED'
    assert verify(state((e,)), second, target).status == VerificationStatus.VERIFIED


def test_lifecycle_failure_never_verifies_successful_execution():
    e, target = _target()
    after = ComputerState(provider_failure='VERIFICATION_WORKER_TIMEOUT')
    result = verify(state((e,)), after, target)
    assert result.status == VerificationStatus.UNCERTAIN
    assert result.evidence == 'VERIFICATION_WORKER_TIMEOUT'


def test_canonical_top_level_handle_keeps_stable_explorer_hwnd():
    from tools.window_tools import canonical_top_level_handle
    user = Mock()
    user.GetAncestor.return_value = 7
    assert canonical_top_level_handle(7, user) == 7
    user.GetAncestor.assert_called_once_with(7, 2)


def test_child_foreground_handle_normalizes_to_authorized_top_level():
    from tools.window_tools import Windows
    user = Mock()
    user.GetForegroundWindow.return_value = 70
    user.GetAncestor.return_value = 7
    root = Mock(handle=7)
    desktop = Mock()
    desktop.window.return_value.wrapper_object.return_value = root
    windows = Windows(Mock())
    windows.desktop = Mock(return_value=desktop)
    with patch('tools.window_tools.ctypes.WinDLL', return_value=user):
        assert windows.active() is root
    desktop.window.assert_called_once_with(handle=7)


def test_explorer_window_replacement_remains_rejected():
    from vision.observer import collect_target
    _, target = _target()
    replacement = WindowState(8, 'explorer')
    metadata = ComputerState(replacement, (replacement,), available=True,
                             elements_complete=False)
    with patch('vision.observer._scoped_windows',
               return_value=(metadata, Mock(handle=8))):
        after = collect_target(Mock(), target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'
    assert not after.target_verification_complete


def test_other_genuine_explorer_window_foreground_is_not_equivalent():
    from vision.observer import collect_target
    _, target = _target()
    original = WindowState(7, 'explorer')
    other = WindowState(8, 'explorer')
    metadata = ComputerState(other, (original, other), available=True,
                             elements_complete=False)
    with patch('vision.observer._scoped_windows',
               return_value=(metadata, Mock(handle=8))):
        after = collect_target(Mock(), target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'


def test_temporary_foreground_change_is_not_silently_accepted():
    from vision.observer import collect_target
    _, target = _target()
    original = WindowState(7, 'explorer')
    temporary = WindowState(9, 'notepad')
    first = ComputerState(temporary, (original, temporary), available=True,
                          elements_complete=False)
    restored = ComputerState(original, (original, temporary), available=True,
                             elements_complete=False)
    scoped = Mock(side_effect=[(first, Mock(handle=9)),
                               (restored, Mock(handle=7))])
    with patch('vision.observer._scoped_windows', scoped):
        after = collect_target(Mock(), target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'
    assert scoped.call_count == 1


def test_foreground_trace_is_opt_in_and_excludes_titles(monkeypatch, capsys):
    from tools.window_tools import trace_foreground
    monkeypatch.setenv('VOXPILOT_FOREGROUND_DIAGNOSTICS', 'true')
    trace_foreground('test', authorized_handle=7, authorized_app='explorer')
    output = capsys.readouterr().out
    assert 'authorized_handle=7' in output
    assert 'authorized_app=explorer' in output
    assert 'title' not in output.casefold()


def test_foreground_change_never_false_positive_verifies():
    e, target = _target()
    original = state((e,))
    changed_window = WindowState(8, 'explorer')
    changed = ComputerState(changed_window,
                            (WindowState(7, 'explorer'), changed_window),
                            available=True, elements_complete=False,
                            provider_failure='POST_ACTION_FOREGROUND_CHANGED')
    result = verify(original, changed, target)
    assert result.status != VerificationStatus.VERIFIED


def _owner_fixture(tmp_path, *, pid=44, executable='other.exe',
                   window_class='ConsoleWindowClass', visible=True, context=None,
                   application=None):
    from tools.window_metadata import _foreground_owner
    path = tmp_path / executable
    path.touch()
    user = Mock()
    def assign_pid(handle, pointer):
        pointer._obj.value = pid
    user.GetWindowThreadProcessId.side_effect = assign_pid
    user.IsWindowVisible.return_value = visible
    user.GetWindow.return_value = 0
    user.GetAncestor.return_value = 0
    def assign_class(handle, buffer, size):
        buffer.value = window_class
        return len(window_class)
    user.GetClassNameW.side_effect = assign_class
    windows = Mock()
    windows.executable.return_value = str(path)
    def identify(_):
        if application is None:
            raise SafeError('unapproved')
        return application
    return _foreground_owner(user, windows, identify, 70, 7, context or {})


def test_foreground_owner_captures_ephemeral_structural_identity(tmp_path):
    owner = _owner_fixture(tmp_path, pid=44, executable='conhost.exe',
                           visible=True)
    assert owner.raw_handle == 70 and owner.top_level_handle == 7
    assert owner.pid == 44 and owner.executable_name == 'conhost.exe'
    assert len(owner.executable_digest) == 64
    assert owner.window_class == 'ConsoleWindowClass'
    assert owner.visible is True and owner.category == 'terminal'
    assert str(tmp_path) not in repr(owner)


def test_foreground_owner_classifies_intentra_and_parent_terminal(tmp_path):
    intentra = _owner_fixture(tmp_path, pid=44,
                              context={'intentra_pid': 44,
                                       'parent_terminal_pid': 55})
    parent = _owner_fixture(tmp_path, pid=55,
                            context={'intentra_pid': 44,
                                     'parent_terminal_pid': 55})
    assert intentra.category == 'intentra'
    assert parent.category == 'parent_terminal'


def test_foreground_owner_classifies_file_explorer(tmp_path):
    owner = _owner_fixture(tmp_path, pid=77, executable='explorer.exe',
                           window_class='CabinetWClass',
                           application='explorer')
    assert owner.category == 'file_explorer'
    assert owner.application == 'explorer'


def test_foreground_owner_survives_pickle_after_window_disappears(tmp_path):
    import pickle
    owner = _owner_fixture(tmp_path, pid=88, executable='windowsterminal.exe')
    state_value = ComputerState(foreground_owner=owner,
                                provider_failure='POST_ACTION_FOREGROUND_CHANGED')
    restored = pickle.loads(pickle.dumps(state_value))
    assert restored.foreground_owner == owner
    assert restored.foreground_owner.pid == 88
    assert restored.foreground_owner.category == 'terminal'


def test_action_completion_timestamp_is_process_scoped():
    item = Element((1,), 'TabItem', 'View')
    from test_phase3_general_ui_interaction import tool
    instance, _ = tool(item)
    with patch('time.monotonic_ns', return_value=123456),          patch('os.getpid', return_value=321):
        result = instance.interact_element('last', 'select')
    assert result['action_completed_monotonic_ns'] == 123456
    assert result['action_process_id'] == 321


def test_verification_start_timestamp_is_process_scoped():
    from agent.computer_state import ForegroundOwner
    from vision.observer import collect_target
    _, target = _target()
    windows, active, metadata = _target_windows([_target_wrapper(True)])
    owner = ForegroundOwner(7, 7, 10, 'explorer.exe', 'a' * 64,
                            'explorer', 'CabinetWClass', True,
                            None, None, 'file_explorer', 100, 10)
    metadata = replace(metadata, foreground_owner=owner)
    with patch('vision.observer._scoped_windows', return_value=(metadata, active)),          patch('vision.observer.provider_owned', return_value=True),          patch('vision.observer.time.monotonic_ns', return_value=654321),          patch('vision.observer.os.getpid', return_value=987):
        after = collect_target(windows, target)
    assert after.verification_started_monotonic_ns == 654321
    assert after.verification_process_id == 987
    assert after.foreground_owner == owner


def test_cross_process_timestamps_are_reported_without_derived_delta(tmp_path):
    from agent.computer_state import ForegroundOwner
    from vision.inspect import inspection_report
    owner = ForegroundOwner(7, 7, 10, 'explorer.exe', 'b' * 64,
                            'explorer', 'CabinetWClass', True,
                            None, None, 'file_explorer', 100, 10)
    report = inspection_report(ComputerState(
        foreground_owner=owner,
        verification_started_monotonic_ns=200,
        verification_process_id=20))
    diagnostics = report['diagnostics']
    assert diagnostics['verification_started_monotonic_ns'] == 200
    assert diagnostics['verification_process_id'] == 20
    assert 'delta' not in diagnostics


def _auxiliary_case(tmp_path, candidates, *, owner_handle=7, root_owner=None,
                    auxiliary_pid=22, root_pid=22, root_class='CabinetWClass',
                    root_visible=True, auxiliary_class='Net UI Tool Window',
                    include_original=True):
    import hashlib
    from agent.computer_state import ForegroundOwner
    executable = (tmp_path / 'explorer.exe').resolve()
    executable.touch()
    digest = hashlib.sha256(str(executable).casefold().encode()).hexdigest()
    original = WindowState(7, 'explorer')
    owner = ForegroundOwner(
        70, 70, auxiliary_pid, 'explorer.exe', digest, 'explorer',
        auxiliary_class, True, owner_handle, root_owner, 'explorer_shell',
        100, 200)
    metadata = ComputerState(
        None, (original,) if include_original else (), available=True,
        elements_complete=False, foreground_owner=owner)
    root = Mock(handle=7)
    root.process_id.return_value = root_pid
    root.class_name.return_value = root_class
    root.is_visible.return_value = root_visible
    root.descendants.return_value = candidates
    desktop = Mock()
    desktop.window.return_value.wrapper_object.return_value = root
    windows = Mock()
    windows.desktop.return_value = desktop
    windows.executable.return_value = str(executable)
    windows.app_of.return_value = 'explorer'
    element, target = _target()
    target.update({
        'authorized_pid': 22,
        'authorized_executable_digest': digest,
        'authorized_window_class': 'CabinetWClass',
    })
    return element, target, windows, metadata, root, desktop


def test_owned_explorer_auxiliary_allows_read_only_selected_verification(tmp_path):
    from vision.observer import collect_target
    element, target, windows, metadata, root, desktop = _auxiliary_case(
        tmp_path, [_target_wrapper(True)])
    with patch('vision.observer._scoped_windows',
               side_effect=[(metadata, None), (metadata, None)]),          patch('vision.observer.provider_owned', return_value=True):
        after = collect_target(windows, target)
    assert after.target_verification_complete
    assert verify(state((element,)), after, target).status == VerificationStatus.VERIFIED
    assert all(call.kwargs.get('handle') == 7
               for call in desktop.window.call_args_list)
    root.set_focus.assert_not_called()


def test_same_process_auxiliary_without_owner_relationship_is_rejected(tmp_path):
    from vision.observer import collect_target
    _, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(True)], owner_handle=None, root_owner=None)
    with patch('vision.observer._scoped_windows',
               return_value=(metadata, None)):
        after = collect_target(windows, target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'
    assert not after.target_verification_complete


def test_different_real_explorer_window_is_not_auxiliary_exception(tmp_path):
    from vision.observer import collect_target
    _, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(True)])
    other = WindowState(8, 'explorer')
    metadata = replace(metadata, active_window=other,
                       open_windows=(WindowState(7, 'explorer'), other))
    with patch('vision.observer._scoped_windows',
               return_value=(metadata, Mock(handle=8))):
        after = collect_target(windows, target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'


@pytest.mark.parametrize('change', ['missing', 'pid', 'class', 'visibility'])
def test_auxiliary_path_rejects_changed_original_window(tmp_path, change):
    from vision.observer import collect_target
    kwargs = {
        'include_original': change != 'missing',
        'root_pid': 99 if change == 'pid' else 22,
        'root_class': 'OtherClass' if change == 'class' else 'CabinetWClass',
        'root_visible': change != 'visibility',
    }
    _, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(True)], **kwargs)
    with patch('vision.observer._scoped_windows',
               return_value=(metadata, None)):
        after = collect_target(windows, target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'
    assert not after.target_verification_complete


def test_auxiliary_path_rejects_executable_change(tmp_path):
    from vision.observer import collect_target
    _, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(True)])
    changed = tmp_path / 'changed-explorer.exe'
    changed.touch()
    windows.executable.return_value = str(changed)
    with patch('vision.observer._scoped_windows',
               return_value=(metadata, None)):
        after = collect_target(windows, target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'


def test_auxiliary_disappearance_during_query_is_uncertain(tmp_path):
    from vision.observer import collect_target
    element, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(True)])
    disappeared = replace(metadata, foreground_owner=None)
    with patch('vision.observer._scoped_windows',
               side_effect=[(metadata, None), (disappeared, None)]),          patch('vision.observer.provider_owned', return_value=True):
        after = collect_target(windows, target)
    result = verify(state((element,)), after, target)
    assert result.status != VerificationStatus.VERIFIED
    assert result.evidence == 'POST_ACTION_FOREGROUND_CHANGED'


def test_owned_auxiliary_duplicate_targets_do_not_verify(tmp_path):
    from vision.observer import collect_target
    element, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(True), _target_wrapper(True)])
    with patch('vision.observer._scoped_windows',
               side_effect=[(metadata, None), (metadata, None)]),          patch('vision.observer.provider_owned', return_value=True):
        after = collect_target(windows, target)
    result = verify(state((element,)), after, target)
    assert result.status != VerificationStatus.VERIFIED
    assert result.evidence == 'POST_ACTION_TARGET_AMBIGUOUS'


def test_owned_auxiliary_selected_false_does_not_verify(tmp_path):
    from vision.observer import collect_target
    element, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(False)])
    with patch('vision.observer._scoped_windows',
               side_effect=[(metadata, None), (metadata, None)]),          patch('vision.observer.provider_owned', return_value=True):
        after = collect_target(windows, target)
    result = verify(state((element,)), after, target)
    assert result.status == VerificationStatus.FAILED
    assert result.evidence == 'POST_ACTION_SELECTION_NOT_OBSERVED'


def test_unreviewed_explorer_auxiliary_class_is_rejected(tmp_path):
    from vision.observer import collect_target
    _, target, windows, metadata, _, _ = _auxiliary_case(
        tmp_path, [_target_wrapper(True)], auxiliary_class='WorkerW')
    with patch('vision.observer._scoped_windows',
               return_value=(metadata, None)):
        after = collect_target(windows, target)
    assert after.provider_failure == 'POST_ACTION_FOREGROUND_CHANGED'
