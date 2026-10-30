"""Bounded local speech segmentation. Whisper runs only on completed utterances."""
from collections import deque
import queue
import threading
import time
import numpy as np
from utils.helpers import SafeError


class SileroDetector:
    """Use the local Silero model shipped with faster-whisper; no download or cloud."""
    def __init__(self, threshold=0.5):
        self.threshold = threshold
        from faster_whisper.vad import get_vad_model
        self.model = get_vad_model()
        self.history = deque(maxlen=4)

    def reset(self):
        self.history.clear()

    def __call__(self, block):
        self.history.append(block)
        probabilities = np.asarray(self.model(np.concatenate(tuple(self.history)))).reshape(-1)
        return bool(np.mean(probabilities[-4:]) >= self.threshold)


class Segmenter:
    block_samples = 2048  # 128 ms at 16 kHz, four Silero frames
    block_ms = 128

    def __init__(self, settings):
        self.settings = settings
        self.last_metrics = None
        self.reset()

    def reset(self):
        import math
        self.pre = deque(maxlen=math.ceil(self.settings.vad_pre_roll_ms / self.block_ms))
        self.parts = []
        self.speech_ms = self.silence_ms = self.elapsed_ms = 0
        self.cooldown = 0
        self.context = None
        self.draining = False
        self.drain_silence = 0

    def feed(self, block, speech, context=None):
        if self.draining:
            self.drain_silence = 0 if speech else self.drain_silence + self.block_ms
            if self.drain_silence >= self.settings.vad_silence_timeout_ms: self.reset()
            return None
        if self.cooldown > 0:
            self.cooldown -= self.block_ms
            self.pre.append(block.copy())
            return None
        if not self.parts:
            if not speech:
                self.pre.append(block)
                return None
            self.parts = list(self.pre)
            self.pre.clear()
            self.context = context
            print('[VOICE] Speech detected')
            self.pre_roll_ms = len(self.parts) * self.block_ms
        self.parts.append(block)
        self.elapsed_ms += self.block_ms
        self.speech_ms += self.block_ms if speech else 0
        self.silence_ms = 0 if speech else self.silence_ms + self.block_ms
        if self.elapsed_ms >= self.settings.max_record_seconds * 1000:
            self.reset()
            self.draining = True
            return None  # Discard the entire overlong utterance through its final silence.
        if self.silence_ms >= self.settings.vad_silence_timeout_ms:
            result = (np.concatenate(self.parts).astype(np.float32).tobytes(), self.context) if self.speech_ms >= self.settings.vad_min_speech_ms else None
            if result:
                print('[VOICE] Speech end')
                self.last_metrics = {'speech_ms': self.speech_ms, 'pre_roll_ms': self.pre_roll_ms,
                                     'trailing_silence_ms': self.silence_ms}
            self.reset()
            self.cooldown = self.settings.vad_cooldown_ms
            return result
        return None


class HandsFreeListener:
    def __init__(self, settings, utterance, suppressed, context, detector=None, stream_factory=None):
        self.segmenter = Segmenter(settings)
        self.utterance, self.suppressed, self.context = utterance, suppressed, context
        self.detector = detector
        self.stream_factory = stream_factory
        self.queue = queue.Queue(maxsize=8)
        self.closed = threading.Event()
        self.failed = threading.Event()
        self.stream = None
        self.thread = None
        self.draining_suppressed = False
        self.resume_silence_ms = 0
        self.previous_context = None

    def _callback(self, data, frames, timing, status):
        if status or frames != Segmenter.block_samples:
            self.failed.set()
            return
        try:
            self.queue.put_nowait((bytes(data), self.suppressed(), self.context()))
        except queue.Full:
            self.failed.set()  # Lost audio must not produce a partial command.

    def start(self):
        if self.detector is None: self.detector = SileroDetector(self.segmenter.settings.vad_threshold)
        if self.stream_factory is None:
            import sounddevice
            self.stream_factory = sounddevice.RawInputStream
        try:
            self.stream = self.stream_factory(samplerate=16000, blocksize=Segmenter.block_samples,
                channels=1, dtype='float32', callback=self._callback)
            self.thread = threading.Thread(target=self._run, name='voice-vad', daemon=True)
            self.thread.start()
            self.stream.start()
        except Exception:
            self.close()
            raise SafeError('Hands-free microphone unavailable') from None

    def _run(self):
        try:
            while not self.closed.is_set() and not self.failed.is_set():
                try: data, suppressed, context = self.queue.get(timeout=.05)
                except queue.Empty: continue
                if suppressed or self.suppressed():
                    self.segmenter.reset()
                    self.draining_suppressed = True
                    self.resume_silence_ms = 0
                    if hasattr(self.detector, 'reset'): self.detector.reset()
                    continue
                if self.previous_context is not None and context != self.previous_context:
                    self.segmenter.reset()
                    self.draining_suppressed = True
                    self.resume_silence_ms = 0
                self.previous_context = context
                block = np.frombuffer(data, dtype=np.float32).copy()
                speech = self.detector(block)
                if self.draining_suppressed:
                    self.resume_silence_ms = 0 if speech else self.resume_silence_ms + Segmenter.block_ms
                    if self.resume_silence_ms >= self.segmenter.settings.vad_silence_timeout_ms:
                        self.draining_suppressed = False
                    continue
                if self.segmenter.parts and self.segmenter.context != context:
                    # A confirmation, cancellation or busy boundary changed mid-speech.
                    self.segmenter.reset()
                    self.draining_suppressed = True
                    self.resume_silence_ms = 0
                    continue
                result = self.segmenter.feed(block, speech, context)
                if result: self.utterance(*result, self.segmenter.last_metrics)
        except Exception:
            self.failed.set()
        finally:
            self.segmenter.reset()

    def close(self):
        self.closed.set()
        if self.stream:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(.5)
        while not self.queue.empty():
            try: self.queue.get_nowait()
            except queue.Empty: break


class DuplicateFilter:
    def __init__(self, seconds=2, clock=time.monotonic):
        self.seconds, self.clock = seconds, clock
        self.previous, self.at = None, 0

    def accept(self, text):
        from agent.planner import normalized
        import hashlib
        key, now = hashlib.sha256(normalized(text).encode()).digest(), self.clock()
        if key == self.previous and now - self.at < self.seconds: return False
        self.previous, self.at = key, now
        return True
