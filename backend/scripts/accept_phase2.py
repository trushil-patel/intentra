"""Operator-assisted Phase 2 acceptance. Default prepares NOT_RUN records only."""
import argparse
from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from utils.com import configure_com
configure_com()
from scripts.phase2_trace import Trace
from scripts.phase2_uia import probe

FLAGSHIP = 'Open Chrome, go to Claude, ask it to explain WebRTC in simple words, wait until the answer finishes, then put the answer into Notepad.'
# Fixed developer cases only: no arbitrary command/shell/DOM input interface.
CASES = {}
for app in ('chrome', 'edge', 'notepad'):
    CASES['open_'+app] = ('Open '+app, 'One correct foreground '+app+' window; do not use private documents.', 'success')
    CASES['reuse_'+app] = ('Open '+app, 'Leave exactly one existing '+app+' window. Verify same window reused with no duplicate.', 'reuse')
CASES.update({
    'ambiguous_notepad': ('Open Notepad', 'Manually prepare two disposable Notepad windows, then verify refusal without guessing.', 'blocked'),
    'navigate_chrome': ('Open Chrome and go to example.org', 'Chrome foreground, example.org URL AND visible harmless page loaded.', 'success'),
    'navigate_edge': ('Open Edge and go to example.org', 'Edge foreground, example.org URL AND visible harmless page loaded.', 'success'),
    'literal_notepad': ('Open Notepad and type "Phase two: hello and press Enter"', 'Use one empty disposable editor. Exact sentence including and press Enter; no extra newline.', 'success'),
    'newline_notepad': ('Press Enter', 'Focus disposable Notepad after countdown starts. Exactly one new line in that same field.', 'success'),
    'scroll_page': ('Open Chrome and go to example.org', 'Preparation only: then manually choose a harmless long public page before scroll tests.', 'success'),
    'scroll_down': ('Scroll down', 'Focus a harmless long browser page, away from boundary; verify downward movement in same document.', 'success'),
    'scroll_up': ('Scroll up', 'Focus that same document, away from top; verify upward movement.', 'success'),
    'unsupported_input': ('Type phase two scratch', 'Focus VS Code/unsupported console after countdown. Input must be refused with no text inserted.', 'blocked'),
    'deny_confirmation': ('Type phase two scratch', 'Select disposable Notepad text first. Deny replacement using F10. Verify unchanged content.', 'blocked'),
    'changed_target': ('Type phase two scratch', 'Select disposable Notepad text. During pending confirmation change target, then F9; verify no insertion.', 'blocked'),
    'stale_confirmation': ('Type phase two scratch', 'After an earlier denied/expired run, select disposable Notepad text and hold F9 through this new request. Release without pressing again; wait for expiry. Verify no insertion. This covers held-key stale approval, not stale spoken replies.', 'blocked'),
    'flagship': (FLAGSHIP, 'Manually sign in to Claude first. Authorize one harmless prompt submission and literal transfer to one disposable Notepad editor. No automatic retry.', 'success'),
})
for mode in ('push_to_talk', 'hands_free'):
    CASES['speech_'+mode] = (None, 'Speak the established ten displayed phrases; recognition only, no desktop actions. Observe physical capture.', 'speech')
for stage in ('idle','recording','transcription','confirmation','task'):
    CASES['emergency_'+stage] = (None, 'Physically press Ctrl+Alt+X during '+stage+'. Expect full shutdown, no late input, no stale approvals; fresh runtime each case.', 'emergency')


def classify(kind, trace, human, exit_code, case=None):
    if human == 'FAIL': return 'FAIL'
    if human != 'PASS': return 'UNCERTAIN'
    if kind == 'blocked':
        reasons = {'ambiguous_notepad': {'AMBIGUOUS_TARGET'}, 'unsupported_input': {'UNSUPPORTED','UNAPPROVED_TARGET'},
                   'deny_confirmation': {'CONFIRMATION_DENIED','CONFIRMATION_EXPIRED'},
                   'stale_confirmation': {'CONFIRMATION_EXPIRED'}, 'changed_target': {'TARGET_CHANGED'}}
        if case in reasons and trace.reason not in reasons[case]: return 'UNCERTAIN'
        if case == 'stale_confirmation' and not any(s.get('confirmation_expired') is True for s in trace.steps): return 'UNCERTAIN'
        if case == 'changed_target' and not any(s.get('authorization')=='APPROVED' for s in trace.steps): return 'UNCERTAIN'
        if case in {'deny_confirmation','stale_confirmation'} and not any(s.get('authorization')=='DENIED_OR_EXPIRED' for s in trace.steps): return 'UNCERTAIN'
        return 'PASS' if trace.terminal == 'BLOCKED' and not any(s['execution']=='SUCCESS' for s in trace.steps) else 'UNCERTAIN'
    if trace.terminal in {'BLOCKED','CANCELLED'}: return 'BLOCKED'
    if trace.terminal in {'FAILED','ERROR'}: return 'FAIL'
    verified = bool(trace.steps) and all(s['execution']=='SUCCESS' and s['verification']=='VERIFIED' for s in trace.steps)
    if exit_code != 0 or trace.terminal != 'SUCCESS' or not verified: return 'UNCERTAIN'
    if kind == 'reuse' and not any(a['tool']=='focus_app' for s in trace.steps for a in s['attempts']): return 'UNCERTAIN'
    return 'PASS'


def speech_summary(path):
    from audio.benchmark import load_samples, PHRASES
    from utils.logger import redact
    data = load_samples(path)
    rows = []
    for phrase in PHRASES:
        sample = data.get(phrase)
        if sample is None:
            rows.append(dict(expected=phrase, failure='NOT_RUN', exact_match=False))
            continue
        heard = sample.get('heard', '')
        exact = heard == phrase
        failure = ('RECOGNITION_REJECTED' if not sample.get('accepted') or sample.get('error') else
                   'TRANSCRIPTION_MISMATCH' if not exact else 'ROUTING_REPEAT' if sample.get('route')=='REPEAT' else None)
        rows.append(dict(expected=phrase, heard=redact(heard), exact_match=exact, failure=failure,
            route=sample.get('route'), stt_ms=sample.get('confidence',{}).get('stt_ms'), total_ms=sample.get('total_ms')))
    times = [r['stt_ms'] for r in rows if type(r.get('stt_ms')) in {int,float}]
    return dict(attempted=len(data), expected=10, exact_matches=sum(r['exact_match'] for r in rows),
                exact_accuracy=sum(r['exact_match'] for r in rows)/len(data) if data else None,
                median_stt_ms=statistics.median(times) if times else None, max_stt_ms=max(times) if times else None, rows=rows)


def approve(case, ask=input):
    return ask('Type RUN '+case+' to authorize this case, or Enter to leave NOT_RUN: ').strip() == 'RUN '+case


def run_case(case, folder, ask=input, invoke=None, sleeper=time.sleep):
    command, expected, kind = CASES[case]
    record = dict(case=case, expected=expected, status='NOT_RUN', operator_authorized=False)
    print('\nCASE: '+case+'\nCommand: '+(command or 'Physical '+kind+' session')+'\nExpected: '+expected)
    print('Keep target foreground during runtime confirmation; use F9/F10. Harness approval does not replace Router approval.')
    if not approve(case, ask): return record
    if case == 'flagship' and ask('Type SUBMIT ONCE to authorize live Claude submission (sign in manually first): ').strip() != 'SUBMIT ONCE': return record
    record['operator_authorized'] = True
    # Explicit local transcript storage is inherited from the existing comparison workflow.
    if kind == 'speech': print('This records redacted transcripts and timings locally, never raw audio.')
    print('Five seconds to focus the controlled target. Do not return to the console until runtime finishes.')
    sleeper(5)
    trace = Trace()
    start = time.monotonic()
    old_mode = os.environ.get('VOICE_MODE')
    try:
        from config.settings import Settings
        if Settings.load().debug_recording:
            record.update(status='BLOCKED', reason='DEBUG_RECORDING_ENABLED')
            return record
        if invoke is None:
            from main import main as invoke
        argv = ['--command', command] if command else []
        if kind == 'speech':
            os.environ['VOICE_MODE'] = case.removeprefix('speech_')
            argv = ['--transcribe-only', '--compare-voice', str(folder/(case+'.jsonl'))]
        elif kind == 'emergency':
            os.environ['VOICE_MODE'] = 'push_to_talk'
            print('Use F8 and harmless scratch commands as needed to reach the requested stage; then physically Ctrl+Alt+X.')
        with trace.installed():
            code = invoke(argv)
        record.update(exit_code=code, runtime_status=trace.terminal, failure_reason=trace.reason, steps=trace.steps)
        record['remaining_child_count'] = sum(p.is_alive() for p in multiprocessing.active_children())
        human = ask('Observed expected result? Type PASS, FAIL, BLOCKED or UNCERTAIN (no private notes): ').strip().upper()
        record['human_observation'] = human if human in {'PASS','FAIL','BLOCKED','UNCERTAIN'} else 'UNCERTAIN'
        if kind == 'emergency':
            record['emergency'] = trace.emergency
            good = trace.emergency and trace.emergency['stage'] == case.removeprefix('emergency_') and trace.emergency['closed'] and trace.emergency['latched'] and record['remaining_child_count']==0
            record['status'] = 'PASS' if good and human=='PASS' and code==0 else 'FAIL' if human=='FAIL' else 'UNCERTAIN'
        elif kind == 'speech':
            record['speech'] = speech_summary(folder/(case+'.jsonl'))
            record['status'] = 'PASS' if human=='PASS' and code==0 and record['speech']['attempted']==10 and all(r['failure'] is None for r in record['speech']['rows']) else 'FAIL' if human=='FAIL' else 'UNCERTAIN'
        else:
            record['status'] = classify(kind, trace, human, code, case)
    except (Exception, KeyboardInterrupt, EOFError) as exc:
        record.update(status='BLOCKED', reason='OPERATOR_INTERRUPTED' if isinstance(exc,(KeyboardInterrupt,EOFError)) else 'RUNTIME_OR_EVIDENCE_ERROR', steps=trace.steps)
    finally:
        if old_mode is None: os.environ.pop('VOICE_MODE', None)
        else: os.environ['VOICE_MODE'] = old_mode
        record['seconds'] = round(time.monotonic()-start, 3)
    return record


def prepared_case(case):
    from agent.planner import deterministic_v2
    command, expected, kind = CASES[case]
    plan = deterministic_v2(command) if command else None
    return dict(case=case, expected=expected, status='NOT_RUN', operator_authorized=False,
                steps=[dict(planned_action=a.tool, authorization='NOT_REACHED', execution='NOT_RUN',
                            verification='NOT_RUN') for a in plan.actions] if plan else [])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=sorted(CASES), help='one explicitly approved operator test; no automatic batch or retries')
    parser.add_argument('--probe', action='store_true', help='read-only bounded current foreground UIA report; no focus or launch')
    parser.add_argument('--target', choices=('chrome','edge','notepad'), help='expected foreground for read-only probe')
    parser.add_argument('--delay', type=int, default=5, choices=range(0,16), help='seconds to focus controlled target before probe')
    parser.add_argument('--prepare', action='store_true', help='write NOT_RUN manifest (also the default)')
    args = parser.parse_args(argv)
    if args.case and (args.probe or args.prepare): parser.error('--case cannot be combined with preparation/probe')
    if args.target and not args.probe: parser.error('--target requires --probe')
    folder = ROOT/'temp'/('phase2-'+uuid.uuid4().hex)
    folder.mkdir(parents=True)
    report = dict(created_utc=datetime.now(timezone.utc).isoformat(), phase='PARTIAL', cases=[prepared_case(k) for k in CASES])
    output = folder/'results.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Evidence: '+str(output))
    if args.probe:
        print('Read-only probe in '+str(args.delay)+' seconds; focus the controlled target now.')
        time.sleep(args.delay)
        report['uia'] = probe()
        if args.target:
            report['uia']['expected_app'] = args.target
            report['uia']['expected_app_matches'] = report['uia'].get('app') == args.target
        print(json.dumps(report['uia'], indent=2))
    if args.case:
        try:
            result = run_case(args.case, folder)
        except (KeyboardInterrupt, EOFError):
            result = dict(case=args.case, status='NOT_RUN', reason='NO_OPERATOR_APPROVAL')
        report['cases'] = [result if row['case']==args.case else row for row in report['cases']]
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Phase remains PARTIAL until all required physical evidence is reviewed. Harness never retries a case; production bounded recovery is unchanged.')
    return 1 if any(r['status'] in {'FAIL','BLOCKED','UNCERTAIN'} for r in report['cases']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
