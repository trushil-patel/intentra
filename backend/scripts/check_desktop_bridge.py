"""Opt-in real model/pipe smoke check; no microphone recording or actions."""
import subprocess, json, threading, queue, time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
(root/'temp').mkdir(exist_ok=True)
p=subprocess.Popen([str(root/'.venv-win/Scripts/python.exe'),'-u','desktop_entry.py'],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
q=queue.Queue()
threading.Thread(target=lambda:[q.put(line) for line in p.stdout],daemon=True).start()
errors=[]
threading.Thread(target=lambda:[errors.append(line) for line in p.stderr],daemon=True).start()
seen=[]
try:
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        try: e=json.loads(q.get(timeout=1))
        except queue.Empty:
            if p.poll() is not None: break
            continue
        seen.append(e)
        if e['type'] in ('ready','startup_error'): break
    print('Startup event:',seen[-1] if seen else {'exit':p.poll()})
    assert any(e['type']=='ready' for e in seen), 'Real agent did not become ready'
    p.stdin.write('{"op":"stop"}\n');p.stdin.flush()
    p.wait(timeout=15)
    while not q.empty(): seen.append(json.loads(q.get()))
    assert any(e['status']=='emergency_stopped' for e in seen)
    print('Real Parakeet READY, pipe emergency stop, and process exit: PASS')
finally:
    if p.poll() is None:
        p.stdin.close()
        try:p.wait(timeout=10)
        except subprocess.TimeoutExpired:p.kill()
    (root/'temp/os-real-stderr.log').write_text(''.join(errors),encoding='utf-8')
    (root/'temp/os-real-bridge.json').write_text(json.dumps(seen,indent=2),encoding='utf-8')
