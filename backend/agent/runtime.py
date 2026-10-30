"""Console runtime: key polling stays independent of recording and planning."""
import queue
from desktop.events import emit
import json
from utils.logger import redact
import threading
import time
from datetime import datetime
from config.settings import ROOT
from agent.context import UserCommand
from agent.planner import normalized, STOP_WORDS
from audio.recorder import Recorder
from audio.command_normalizer import normalize_spoken_navigation
from audio.push_to_talk import KeyMonitor
from audio.text_to_speech import Speaker
from utils.helpers import SafeError

class Runtime:
    def __init__(self, settings, brain, transcriber, stop, transcribe_only=False):
        self.settings, self.brain, self.transcriber, self.stop = settings, brain, transcriber, stop
        self.transcribe_only = transcribe_only
        self.diagnostic_sink = None
        self.task_serial = 0
        self.events = queue.Queue(maxsize=4)
        self.closed = threading.Event()
        self.emergency_latched = threading.Event()
        self._emergency_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._cleanup_done = False
        self.busy = threading.Lock()
        self.transcribing = threading.Lock()
        self.speech_pending = threading.Event()
        self.command_pending = threading.Event()
        self.input_overflow = threading.Event()
        self.recorder = Recorder(settings.max_record_seconds)
        self.speaker = Speaker(settings.tts)
        self.keys = KeyMonitor(stop, lambda: self.enqueue("down"), lambda: self.enqueue("up"), self.resume, on_emergency=self.emergency_stop)
        self.workers = []
        self.listener = None
        from audio.push_to_talk import PushToTalkCapture
        self.ptt = PushToTalkCapture(self.recorder, self._utterance, self._voice_context,
                                    self._ptt_allowed, self._capture_valid)
        from audio.hands_free import DuplicateFilter
        self.duplicates = DuplicateFilter(settings.duplicate_seconds)
        self.speaker.cooldown = settings.tts_cooldown_ms / 1000
        self.brain.router.confirmations.voice_prompt = self.speaker.say
        if settings.voice_mode == 'hands_free':
            self.keys.on_down = lambda: None
            self.keys.on_up = lambda: None

    def emergency_stop(self):
        """Hotkey-thread signal only: no input queue, model, UIA or joins here."""
        with self._emergency_lock:
            if self.emergency_latched.is_set(): return
            self.stop.stop()
            self.emergency_latched.set()
            self.closed.set()
            emit('emergency_stopped', 'emergency', 'Emergency stop latched. Restart the agent to continue.')

    def enqueue(self, event):
        if self.closed.is_set(): return
        # F8 is capture only, including while a confirmation is pending.
        try: self.events.put_nowait((event, self._voice_context(), self._ptt_allowed() if event == 'down' else True))
        except queue.Full:
            self.input_overflow.set()

    def _capture_valid(self, context):
        return (context is not None and context[0] == self.stop.generation
                and not self.closed.is_set() and not self.speaker.suppressed
                and (len(context) < 4 or context[3] == self.speaker.generation)
                and (len(context) < 5 or context[4] == self.task_serial))

    def _ptt_allowed(self):
        if self.closed.is_set(): return False
        if self.transcribing.locked() or self.speech_pending.is_set() or self.speaker.suppressed:
            return False
        from safety.confirmation import Pending
        pending = self.brain.router.confirmations.pending
        return not (self.busy.locked() or self.command_pending.is_set()) or isinstance(pending, Pending)

    def _idle_status(self):
        if self.closed.is_set() or self.busy.locked() or self.transcribing.locked() or self.speech_pending.is_set() or self.command_pending.is_set(): return
        if self.settings.voice_mode == 'push_to_talk':
            print('[READY] Hold F8 to speak.')
            emit('idle', 'ready', 'Hold F8 to speak.')
        else:
            print('[LISTENING] Speak after the quiet interval.')

    def resume(self):
        if self.closed.is_set(): return
        if not self.busy.locked() and not self.transcribing.locked() and not self.speech_pending.is_set() and self.recorder.stream is None:
            self.stop.resume()
            print("[IDLE] Emergency stop reset")
        else:
            print("[BLOCKED] Wait for pending work to finish before resuming")

    def _spawn(self, fn, *args):
        self.workers = [worker for worker in self.workers if worker.is_alive()]
        worker = threading.Thread(target=fn, args=args, daemon=True)
        self.workers.append(worker)
        worker.start()

    def _voice_context(self):
        pending = self.brain.router.confirmations.pending
        from safety.confirmation import Pending
        return (self.stop.generation, pending.nonce if isinstance(pending, Pending) else None,
                self.busy.locked() or self.command_pending.is_set(), self.speaker.generation, self.task_serial)

    def _utterance(self, data, context, segment_metadata=None):
        if not self._capture_valid(context):
            self.ptt.completed()
            return
        if self.transcribing.locked() or self.speech_pending.is_set():
            print('[IGNORED] STT busy; no speech is queued.')
            self.ptt.completed()
            return
        try:
            from audio.segments import usable_audio
            usable_audio(data)
        except SafeError as exc:
            print('[IGNORED] ' + str(exc))
            self.ptt.completed()
            self._idle_status()
            return
        self.speech_pending.set()
        self._spawn(self._transcribe, data, context, segment_metadata)

    def _process(self, text, generation=None, task_serial=None):
        if generation is None: generation = self.stop.generation
        if self.closed.is_set(): return
        # Reject stale work before any branch, including confirmation replies.
        if generation != self.stop.generation or (task_serial is not None and task_serial != self.task_serial): return
        if normalized(text) in STOP_WORDS:
            if generation != self.stop.generation or (task_serial is not None and task_serial != self.task_serial): return
            self.stop.stop()
            print("[CANCELLED] Direct stop command received.")
            return
        if self.transcribe_only:
            print("[HEARD] " + json.dumps(redact(text)))
            return
        pending = self.brain.router.confirmations.pending
        from safety.confirmation import Pending
        if normalized(text) in {'yes', 'no', 'confirm', 'deny'}:
            if isinstance(pending, Pending): self.brain.router.confirmations.respond(text, pending.nonce)
            return
        if not self.busy.acquire(blocking=False):
            print("[BLOCKED] Another command is active; command discarded")
            self.command_pending.clear()
            return
        initialized_com = False
        try:
            if self.closed.is_set(): return
            if generation is not None and generation != self.stop.generation: return
            if task_serial is not None and task_serial != self.task_serial: return
            self.task_serial += 1
            # Old workers have finished and the busy lock is held: a new direct command
            # may start a new task. Never clear cancellation while old work runs.
            if self.stop.stopped: self.stop.resume(expected_generation=generation)
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            initialized_com = True
            emit('understanding', 'transcript', redact(text))
            print("[UNDERSTANDING]")
            results = self.brain.handle(UserCommand(text))
            if self.stop.stopped:
                emit('cancelled', 'cancelled', 'Task stopped')
                print("[CANCELLED] Task stopped. Give a new command when idle.")
            elif results:
                self.speaker.say_status("done" if results[-1].status == "SUCCESS" else "blocked")
            return results
        except SafeError as exc:
            emit('failed', 'error', str(exc))
            print("[BLOCKED] " + str(exc))
            self.brain.router.audit.event("unrecognized", "HIGH", "BLOCKED")
        except Exception:
            self.stop.stop()
            emit('failed', 'error', 'Command failed safely; stop token latched')
            print("[ERROR] Command failed safely; emergency stop latched")
        finally:
            if initialized_com: pythoncom.CoUninitialize()
            self.busy.release()
            self.command_pending.clear()
            self._idle_status()

    def _transcribe(self, data, context=None, segment_metadata=None):
        if not self.transcribing.acquire(blocking=False):
            print("[BLOCKED] Transcription is busy; recording discarded")
            return
        context = self._voice_context() if context is None else context
        audio = None
        endpoint_started = time.perf_counter()
        heard = error = None
        segment_metrics = None
        decoded = False
        route = None
        grammar_kind = None
        try:
            if not self._capture_valid(context): return
            import numpy as np
            if self.transcribe_only or self.settings.voice_diagnostics:
                from audio.segments import report_segment
                segment_metrics = report_segment(data, mode=self.settings.voice_mode, **(segment_metadata or {}))
            audio = np.frombuffer(data, dtype=np.float32).copy()
            data = b""
            if self.settings.debug_recording:
                from scipy.io.wavfile import write
                folder = ROOT / "recordings"
                folder.mkdir(exist_ok=True)
                write(folder / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".wav"), 16000, audio)
            emit('transcribing', 'state', 'Transcribing locally')
            print("[TRANSCRIBING]")
            decoded = True
            from audio.stt_worker import BoundedSpeechToText
            if isinstance(self.transcriber, BoundedSpeechToText):
                text = self.transcriber.transcribe(audio, valid=lambda: self._capture_valid(context))
            else:
                text = self.transcriber.transcribe(audio)
            heard = text
            if not self._capture_valid(context): return
            from audio.command_normalizer import normalize_transcript
            interpreted = normalize_transcript(text)
            # Explicit diagnostic opt-in only; never audit-log transcripts.
            if self.transcribe_only:
                print("[HEARD] " + json.dumps(redact(text)))
                from agent.command_classifier import classify
                from agent.planner import deterministic_v2
                from agent.command_language import recognize
                grammar_kind = recognize(interpreted).kind.value
                route = classify(interpreted, deterministic_v2(interpreted)).value
                print('[PARSER] ' + route)
                if normalize_spoken_navigation(text) != text:
                    print("[INTERPRETED] Spoken destination: Claude.ai")
                elif interpreted != text:
                    print('[NORMALIZER] Command syntax normalized')
                if normalized(text) in STOP_WORDS: self.stop.stop()
                return
            if normalized(text) in STOP_WORDS:
                self.stop.stop()
                emit('cancelled', 'cancelled', 'Task stopped')
                print("[CANCELLED] Task stopped. Give a new command when idle.")
            elif not self.closed.is_set():
                if self.speaker.suppressed or context[0] != self.stop.generation: return
                if normalized(text) in {'yes', 'no', 'confirm', 'deny'}:
                    self.brain.router.confirmations.respond(text, context[1])
                    return
                if context[2] or not self.duplicates.accept(text): return
                if normalize_spoken_navigation(text) != text:
                    print("[INTERPRETED] Spoken destination: Claude.ai")
                elif interpreted != text:
                    print('[NORMALIZER] Command syntax normalized')
                self.command_pending.set()
                self._spawn(self._process, interpreted, context[0], context[4] if len(context) > 4 else None)
        except SafeError as exc:
            error = str(exc)
            # ComparisonSession reports failure after the sample is persisted,
            # then prompts the next phrase. Production still asks for repetition.
            if not (self.transcribe_only and self.diagnostic_sink and decoded and self._capture_valid(context)):
                emit('failed', 'error', str(exc))
                print("[BLOCKED] " + str(exc))
        except Exception:
            error = 'Transcription failed safely'
            if context[0] == self.stop.generation and not self.closed.is_set(): self.stop.stop()
            emit('failed', 'error', 'Transcription failed safely')
            print("[ERROR] Transcription failed safely")
        finally:
            if audio is not None: audio.fill(0)
            data = b""
            if self.transcribe_only or self.settings.voice_diagnostics:
                metrics = getattr(self.transcriber, 'last_metrics', None) if decoded else None
                if isinstance(metrics, dict): print('[CONFIDENCE] ' + json.dumps(metrics, sort_keys=True))
                print(f'[TOTAL] endpoint_to_dispatch_ms={(time.perf_counter()-endpoint_started)*1000:.2f}')
            # Collect only explicitly opted-in diagnostic samples, never execute them.
            try:
                if self.transcribe_only and self.diagnostic_sink and decoded and self._capture_valid(context):
                    self.diagnostic_sink({'heard': redact(heard) if heard else '', 'error': error,
                        'confidence': dict(metrics) if isinstance(metrics, dict) else {},
                        'segment': segment_metrics, 'route': route or 'REPEAT',
                        'grammar': grammar_kind,
                        'accepted': bool(heard) and error is None,
                        'total_ms': round((time.perf_counter()-endpoint_started)*1000, 2),
                        'retry_count': 0, 'escalation_reason': None,
                        'stt_config': self.transcriber.configuration()
                            if hasattr(self.transcriber, 'configuration') else None})
            finally:
                self.transcribing.release()
                self.speech_pending.clear()
            self.ptt.completed()
            self._idle_status()

    def run_voice(self, check_only=False):
        if self.settings.voice_mode == 'hands_free':
            return self._run_hands_free(check_only)
        self.keys.start()
        try:
            from audio.stt_worker import BoundedSpeechToText
            if isinstance(self.transcriber, BoundedSpeechToText):
                self.transcriber.load(valid=lambda: not self.closed.is_set())
            else:
                self.transcriber.load()
            if self.closed.is_set(): return
            if self.settings.whisper_warmup: self.transcriber.warmup()
            if self.transcribe_only or self.settings.voice_diagnostics:
                self.transcriber.print_configuration()
                print('[STT STARTUP] ' + json.dumps(self.transcriber.startup_metrics, sort_keys=True))
            print('[READY] Hold F8 to speak.')
            emit('idle', 'ready', 'Hold F8 to speak.')
            if check_only: return
            while not self.closed.is_set():
                if self.keys.failed: raise SafeError('Emergency key monitor failed')
                self.ptt.poll(self.settings.max_record_seconds)
                if self.input_overflow.is_set():
                    self.ptt.cancel()
                    while not self.events.empty():
                        try: self.events.get_nowait()
                        except queue.Empty: break
                    self.input_overflow.clear()
                    print('[IGNORED] Capture events lost; release F8 and repeat.')
                try: event, context, permitted = self.events.get(timeout=.02)
                except queue.Empty: continue
                try:
                    if event == 'down': self.ptt.down(context, permitted)
                    elif event == 'up':
                        self.ptt.up()
                        self._idle_status()
                except SafeError as exc:
                    print('[ERROR] ' + str(exc))
                    self._idle_status()
        except SafeError:
            if not self.emergency_latched.is_set(): raise
        finally:
            self.close()

    def _run_hands_free(self, check_only=False):
        from audio.hands_free import HandsFreeListener, SileroDetector
        # Start the independent emergency monitor before loading any models.
        self.keys.start()
        try:
            detector = SileroDetector(self.settings.vad_threshold)
            self.transcriber.load()
            if self.closed.is_set(): return
            if self.settings.whisper_warmup: self.transcriber.warmup()
            if self.transcribe_only or self.settings.voice_diagnostics:
                self.transcriber.print_configuration()
                print('[STT STARTUP] ' + json.dumps(self.transcriber.startup_metrics, sort_keys=True))
            if check_only:
                print("[READY] Hands-free VAD, local STT and emergency monitor loaded; microphone not opened.")
                return
            self.listener = HandsFreeListener(self.settings, self._utterance,
                lambda: self.speaker.suppressed or self.transcribing.locked() or self.speech_pending.is_set(), self._voice_context, detector=detector)
            self.listener.start()
            emit('listening', 'ready', 'Hands-free listening')
            print('[LISTENING] Speak normally. Say yes/no for confirmation. Ctrl+Alt+X emergency stop.')
            while not self.closed.wait(.05):
                if self.keys.failed or self.listener.failed.is_set():
                    self.stop.stop()
                    raise SafeError('Voice or emergency monitor failed; stopped safely')
        except SafeError:
            if not self.emergency_latched.is_set(): raise
        finally:
            self.close()

    def run_once(self, command):
        """Finite acceptance command using real policy, confirmations and emergency keys."""
        self.keys.on_down = lambda: None
        self.keys.on_up = lambda: None
        self.keys.start()
        try:
            return self._process(command)
        finally:
            self.close()

    def run_text(self):
        self.keys.on_down = lambda: None
        self.keys.on_up = lambda: None
        self.keys.start()
        print("[IDLE] Type a command; /quit exits, /resume clears an idle stop. Type yes/no to confirm (optional F9/F10).")
        try:
            while not self.closed.is_set():
                if self.keys.failed: raise SafeError("Emergency key monitor failed")
                # A blocked console read must not prevent emergency teardown.
                line = queue.Queue(maxsize=1)
                def read_line():
                    try: line.put(input("> "))
                    except EOFError: line.put(None)
                threading.Thread(target=read_line, name='console-input', daemon=True).start()
                while not self.closed.is_set():
                    try:
                        text = line.get(timeout=.05)
                        break
                    except queue.Empty:
                        if self.keys.failed: raise SafeError("Emergency key monitor failed")
                if self.closed.is_set() or text is None: break
                text = text.strip()
                if text == "/quit": break
                if text == "/resume": self.resume()
                elif normalized(text) in {'yes', 'no', 'confirm', 'deny'}:
                    pending = self.brain.router.confirmations.pending
                    if pending: self.brain.router.confirmations.respond(text, pending.nonce)
                elif text: self._spawn(self._process, text, self.stop.generation, self.task_serial)
        finally:
            self.close()

    def close(self):
        with self._cleanup_lock:
            if self._cleanup_done: return
            self._cleanup_done = True
            self.closed.set()
            if not self.stop.stopped: self.stop.stop()
            self.brain.router.confirmations.cancel_pending()
            self.keys.close()
            if self.listener: self.listener.close()
            self.ptt.cancel()
            while not self.events.empty():
                try: self.events.get_nowait()
                except queue.Empty: break
            from audio.stt_worker import BoundedSpeechToText
            if isinstance(self.transcriber, BoundedSpeechToText): self.transcriber.close()
            for worker in self.workers:
                if worker is not threading.current_thread(): worker.join(timeout=2)
            if self.emergency_latched.is_set():
                print('[EMERGENCY STOP] Capture, confirmation and pending work cancelled; runtime closed.')
