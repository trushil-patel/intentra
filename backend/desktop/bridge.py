"""Private inherited-pipe bridge. No sockets, arbitrary calls, or approvals."""
import json
import logging
import queue
import re
import sys
import os
import threading
from datetime import datetime, timezone
from uuid import uuid4
from utils.logger import redact

STATUSES = {'offline', 'idle', 'listening', 'transcribing', 'understanding',
            'planning', 'awaiting_confirmation', 'executing', 'completed',
            'failed', 'cancelled', 'emergency_stopped'}


class Bridge:
    def __init__(self, output):
        self.output = output
        self.queue = queue.Queue(maxsize=2048)
        self.failed = threading.Event()
        self.done = threading.Event()
        self.lock = threading.RLock()
        self.sequence = 0
        self.session = uuid4().hex
        self.run_id = uuid4().hex
        self.status = 'offline'
        self.runtime = None
        self.active_tool = None
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()

    def emit(self, status, kind, message='', payload=None, new_session=False):
        if status not in STATUSES:
            raise ValueError('Unknown status')
        with self.lock:
            # Physical stop wins over late worker events, including idle.
            if self.status == 'emergency_stopped' and status != 'emergency_stopped':
                return
            if new_session:
                self.session = uuid4().hex
            self.sequence += 1
            self.status = status
            event = dict(id=f'{self.run_id}:{self.sequence}', sessionId=self.session,
                         runId=self.run_id, sequence=self.sequence,
                         timestamp=datetime.now(timezone.utc).isoformat(), status=status,
                         type=kind, message=redact(str(message))[:4000], payload=payload or {})
            try:
                self.queue.put_nowait(event)
            except queue.Full:
                self.failed.set()

    def _write(self):
        while True:
            event = self.queue.get()
            try:
                if event is None:
                    return
                self.output.write(json.dumps(event, ensure_ascii=True) + '\n')
                self.output.flush()
            except (OSError, ValueError):
                self.failed.set()
            finally:
                self.queue.task_done()

    def status_line(self, line):
        line = str(line)
        tag = line.partition(']')[0] + ']'
        if re.fullmatch(r'\[STEP \d+/\d+\] [a-z_]+', line):
            self.active_tool = line.split('] ', 1)[1]
        mapping = {'[RECORDING]': ('listening', 'capture'),
                   '[RELEASED]': ('transcribing', 'state'),
                   '[PLAN]': ('planning', 'plan'),
                   '[VALIDATING]': ('planning', 'permission_check'),
                   '[EXECUTING]': ('executing', 'action_started'),
                   '[WAITING]': ('executing', 'waiting'),
                   '[CONFIRM]': ('awaiting_confirmation', 'confirmation'),
                   '[DONE]': ('completed', 'completed'),
                   '[BLOCKED]': ('failed', 'blocked'),
                   '[ERROR]': ('failed', 'error'),
                   '[FAILED]': ('failed', 'error'),
                   '[UNCERTAIN]': ('failed', 'uncertain'),
                   '[CANCELLED]': ('cancelled', 'cancelled'),
                   '[INPUT REQUIRED]': ('failed', 'input_required'),
                   '[IGNORED]': ('idle', 'capture_discarded')}
        if tag in mapping:
            status, kind = mapping[tag]
            if tag == '[DONE]' and line != '[DONE] Task verified.':
                return  # Native success is not verified success.
            pending = self.runtime and self.runtime.brain.router.confirmations.pending
            if tag == '[IGNORED]' and pending:
                status = 'awaiting_confirmation'
            payload = {}
            if tag == '[CONFIRM]' and self.runtime and self.active_tool:
                payload = {'tool': self.active_tool,
                           'risk': self.runtime.brain.router.registry.spec(self.active_tool).risk.value,
                           'reason': 'The existing Python policy requires explicit approval.'}
            self.emit(status, kind, line, payload, new_session=tag == '[RECORDING]' and not pending)

    def attach(self, runtime):
        self.runtime = runtime
        router = runtime.brain.router
        def wrap(callback):
            def observe(line):
                callback(line)
                try:
                    self.status_line(line)
                except Exception:
                    self.failed.set()
            return observe
        runtime.ptt.display = wrap(runtime.ptt.display)
        router.status = wrap(router.status)
        router.confirmations.display = wrap(router.confirmations.display)
        bridge = self
        class AuditObserver(logging.Handler):
            def emit(self, record):
                try:
                    data = json.loads(record.getMessage())
                    if 'verification' in data:
                        bridge.emit('executing', 'verification',
                                    data['tool'] + ': ' + data['verification'].lower(), data)
                except Exception:
                    bridge.failed.set()
        router.audit.logger.addHandler(AuditObserver())
        from audio.speech_to_text import SpeechToText
        from intentra_version import __version__
        config = SpeechToText(settings=runtime.settings).configuration()
        config.update(voice_mode=runtime.settings.voice_mode,
                      debug_recording=runtime.settings.debug_recording,
                      agent_version=__version__, planner='Optional loopback Ollama')
        self.emit('offline', 'configuration', 'Loading local voice engine', config)
        # Start stdin reading only after native imports. A blocking CRT stdin
        # read during NumPy DLL loading can stall Windows module initialization.
        threading.Thread(target=self.read_controls, args=(sys.stdin,), daemon=True).start()
        threading.Thread(target=self._health, daemon=True).start()

    def _health(self):
        while not self.done.wait(1):
            if self.failed.is_set():
                self.runtime.emergency_stop()
                return
            self.emit(self.status, 'heartbeat')

    def control(self, raw):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return False
        if not isinstance(data, dict) or set(data) != {'op'}:
            return False
        op = data['op']
        if op not in ('ping', 'stop', 'shutdown'):
            return False
        if op == 'ping':
            self.emit(self.status, 'heartbeat')
        elif self.runtime:
            # Same immediate latch as Ctrl+Alt+X; no queues or approval bypass.
            self.runtime.emergency_stop()
        return True

    def _read_windows_controls(self, source):
        # Avoid a blocking CRT stdin read on Windows: DLL initialization in
        # numpy/PortAudio and multiprocessing can otherwise wait on that handle.
        import ctypes
        import msvcrt
        from ctypes import wintypes
        peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
        peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        peek.restype = wintypes.BOOL
        fd = source.fileno()
        handle = msvcrt.get_osfhandle(fd)
        buffer = b''
        while not self.done.wait(.02):
            available = wintypes.DWORD()
            if not peek(handle, None, 0, None, ctypes.byref(available), None):
                break
            if not available.value:
                continue
            try:
                data = os.read(fd, min(available.value, 4096))
                if not data:
                    break
                buffer += data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if len(line) > 4096:
                        raise ValueError('Control frame too large')
                    self.control(line.decode('utf-8'))
                if len(buffer) > 4096:
                    break
            except (OSError, ValueError):
                break
        if not self.done.is_set():
            self.failed.set()
            if self.runtime:
                self.runtime.emergency_stop()

    def read_controls(self, source):
        if os.name == 'nt' and source is sys.stdin:
            return self._read_windows_controls(source)
        while not self.done.is_set():
            line = source.readline(4097)
            if not line:
                self.failed.set()
                if self.runtime:
                    self.runtime.emergency_stop()
                return
            if len(line) > 4096 or not line.endswith('\n'):
                self.failed.set()
                if self.runtime:
                    self.runtime.emergency_stop()
                return
            self.control(line)

    def close(self):
        self.done.set()
        try:
            self.queue.put(None, timeout=.5)
        except queue.Full:
            return
        self.writer.join(timeout=2)
