"""Repeatable Phase 1 developer checks; never executes an assistant command.

Writes local diagnostics only. Physical acceptance is always recorded as pending.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PLANS = (
    'Open Chrome', 'Open Edge', 'Open Notepad',
    'Open Chrome and go to example.org',
    'Open Notepad and type "hello and press Enter"',
    'Open Chrome, go to Claude, ask it to explain WebRTC in simple words, wait until the answer finishes, then put the answer into Notepad.',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--startup', action='store_true', help='also load the selected local STT/VAD models and register/release emergency keys in both voice modes; no recording')
    args = parser.parse_args()
    folder = ROOT / 'temp' / ('phase1-' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    env = dict(os.environ, TEMP=str(folder), TMP=str(folder), PYTHONIOENCODING='utf-8')
    checks = [
        ('regression', ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', str(folder / 'cases')], {}),
        ('dependencies', ['-m', 'pip', 'check'], {}),
        ('doctor', ['main.py', '--doctor'], {}),
        ('applications', ['main.py', '--list-apps'], {}),
        ('native_read_only', ['scripts/check_v2.py'], {}),
        ('compile', ['-m', 'compileall', '-q', 'agent', 'audio', 'config', 'tools', 'safety', 'vision', 'memory', 'utils', 'adapters', 'scripts', 'main.py'], {}),
    ]
    checks += [('plan_' + str(i), ['main.py', '--plan', goal], {}) for i, goal in enumerate(PLANS, 1)]
    if args.startup:
        checks += [('startup_' + mode, ['main.py', '--transcribe-only', '--startup-check'], {'VOICE_MODE': mode})
                   for mode in ('push_to_talk', 'hands_free')]
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'python': sys.executable,
              'physical_acceptance': 'NOT_RUN', 'checks': []}
    for name, arguments, overrides in checks:
        start = time.perf_counter()
        command = [sys.executable, *arguments]
        print('Running ' + name, flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env | overrides, capture_output=True, text=True, encoding='utf-8', errors='replace', shell=False)
        report['checks'].append(dict(name=name, command=command, environment_overrides=overrides,
            exit_code=result.returncode, seconds=round(time.perf_counter()-start, 3),
            stdout=result.stdout, stderr=result.stderr))
        (folder / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'{name}: exit={result.returncode}; {report["checks"][-1]["seconds"]}s', flush=True)
    print('Evidence: ' + str(folder / 'results.json'))
    print('Microphone, physical keys, desktop input and Claude transfer remain unverified.')
    return int(any(check['exit_code'] != 0 for check in report['checks']))


if __name__ == '__main__':
    raise SystemExit(main())
