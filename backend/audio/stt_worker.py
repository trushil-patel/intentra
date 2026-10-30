"""Persistent local STT worker with bounded waits and killable native inference."""
import threading
import multiprocessing
import time
from audio.speech_to_text import SpeechToText
from utils.helpers import SafeError


def _worker(connection, settings):
    from utils.worker_lifecycle import configure_worker, send_reply
    configure_worker()
    stt = SpeechToText(settings=settings)
    try:
        stt.load()
        send_reply(connection, ('ready', stt.configuration(), stt.startup_metrics))
        while True:
            operation, audio = connection.recv()
            try:
                if operation == 'warmup':
                    stt.warmup()
                    send_reply(connection, ('warm', None, stt.startup_metrics))
                elif operation == 'transcribe':
                    try:
                        text = stt.transcribe(audio)
                        send_reply(connection, ('ok', text, stt.last_metrics))
                    except SafeError as exc:
                        send_reply(connection, ('rejected', str(exc), stt.last_metrics))
                else:
                    break
            finally:
                if audio is not None: audio.fill(0)
                audio = None
    except (EOFError, OSError):
        pass
    except Exception:
        try: send_reply(connection, ('error', None, {}))
        except (EOFError, OSError): pass
    finally:
        connection.close()


class BoundedSpeechToText:
    def __init__(self, settings, stop):
        self.settings, self.stop = settings, stop
        self._lifecycle_lock = threading.RLock()
        self.process = self.connection = None
        self.last_metrics = {}
        self.startup_metrics = {}
        self._configuration = SpeechToText(settings=settings).configuration()
        self._configuration['timeout_seconds'] = settings.whisper_timeout_seconds

    def configuration(self):
        return dict(self._configuration)

    def print_configuration(self):
        import json
        print('[STT CONFIG] ' + json.dumps(self.configuration(), sort_keys=True))

    def _receive(self, timeout, generation, valid=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stop.generation != generation or (valid is not None and not valid()):
                raise SafeError('Transcription cancelled; captured audio discarded')
            if self.connection.poll(min(.02, max(0, deadline-time.monotonic()))):
                result = self.connection.recv()
                if self.stop.generation != generation or (valid is not None and not valid()):
                    raise SafeError('Transcription cancelled; captured audio discarded')
                if time.monotonic() >= deadline:
                    break
                return result
            if not self.process.is_alive():
                raise SafeError('Local STT worker exited; please repeat')
        raise SafeError('Local STT timed out; please repeat')

    def load(self, valid=None):
        generation = self.stop.generation
        with self._lifecycle_lock:
            if self.stop.generation != generation or (valid is not None and not valid()):
                raise SafeError('Transcription cancelled')
            if self.process is not None and self.process.is_alive(): return
            self.close()
            ctx = multiprocessing.get_context('spawn')
            parent, child = ctx.Pipe()
            process = ctx.Process(target=_worker, args=(child, self.settings), daemon=True)
            self.process, self.connection = process, parent
            try:
                process.start()
            except BaseException:
                child.close()
                self.close()
                raise
        try:
            child.close()
            kind, configuration, metrics = self._receive(30, generation, valid)
            if kind != 'ready': raise SafeError('Local STT model unavailable')
            self._configuration.update(configuration)
            self.startup_metrics.update(metrics)
        except Exception:
            self.close()
            raise SafeError('Local STT startup failed or cancelled; model must be cached locally') from None
        finally:
            child.close()

    def warmup(self):
        self.load()
        generation = self.stop.generation
        try:
            self.connection.send(('warmup', None))
            kind, _, metrics = self._receive(30, generation)
            if kind != 'warm': raise SafeError('Local STT warmup failed')
            self.startup_metrics.update(metrics)
        except Exception:
            self.close()
            raise SafeError('Local STT warmup failed or cancelled') from None

    def transcribe(self, audio, valid=None):
        started = time.perf_counter()
        generation = self.stop.generation
        self.last_metrics = {'accepted': False, 'retry_count': 0, 'escalation_reason': None}
        try:
            if valid is not None and not valid(): raise SafeError('Transcription cancelled')
            if self.process is None or not self.process.is_alive():
                print('[STT RECOVERY] Reloading local model after interrupted inference.')
                reload_started = time.perf_counter()
                self.load(valid=valid)
                self.last_metrics['recovery_load_ms'] = round((time.perf_counter()-reload_started)*1000, 2)
            if self.stop.generation != generation or (valid is not None and not valid()):
                raise SafeError('Transcription cancelled')
            import numpy as np
            from audio.segments import audio_metrics
            if not isinstance(audio, np.ndarray) or audio.dtype != np.float32 or audio.ndim != 1:
                raise SafeError('STT requires mono float32 samples at 16 kHz')
            metrics = audio_metrics(audio.tobytes())
            if metrics['frames'] > self.settings.max_record_seconds * 16000:
                raise SafeError('Audio exceeds the configured recording limit')
            decode_started = time.monotonic()
            self.connection.send(('transcribe', audio))
            kind, result, metrics = self._receive(max(0, self.settings.whisper_timeout_seconds - (time.monotonic()-decode_started)), generation, valid)
            self.last_metrics.update(metrics)
            if kind == 'rejected': raise SafeError(result)
            if kind != 'ok' or type(result) is not str or not 1 <= len(result) <= 2000:
                raise SafeError('Invalid local STT result')
            return result
        except Exception as exc:
            # A confidence rejection is a completed reply; the resident model is reusable.
            if not (isinstance(exc, SafeError) and locals().get('kind') == 'rejected'):
                self.close()
            self.last_metrics['accepted'] = False
            if isinstance(exc, SafeError): raise
            raise SafeError('Local STT worker failed safely') from None
        finally:
            self.last_metrics['stt_ms'] = round((time.perf_counter()-started)*1000, 2)

    def close(self):
        with self._lifecycle_lock:
            process, connection = self.process, self.connection
            self.process = self.connection = None
            if process is not None:
                try:
                    if process.pid:
                        if process.is_alive(): process.terminate()
                        process.join(.25)
                        if process.is_alive():
                            process.kill()
                            process.join(.25)
                        if not process.is_alive(): process.close()
                except (ValueError, OSError): pass
            if connection is not None: connection.close()
