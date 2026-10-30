"""Local inference with explicit decoding settings and content-free confidence evidence."""
import hashlib
import json
import math
import time
from utils.performance import timed
from utils.helpers import SafeError
from config.settings import ROOT, Settings


class SpeechToText:
    def __init__(self, model_name='base.en', model=None, settings=None):
        self.settings = settings or Settings(whisper_model=model_name)
        self.model_name = self.settings.whisper_model
        self.model = model
        self.last_metrics = {}
        self.startup_metrics = {}
        self._warmed = False

    def load(self, allow_download=False):
        if self.model is None:
            try:
                started = time.perf_counter()
                from faster_whisper import WhisperModel
                self.model = WhisperModel(self.model_name, device=self.settings.whisper_device,
                    compute_type=self.settings.whisper_compute_type,
                    cpu_threads=self.settings.whisper_cpu_threads, num_workers=self.settings.whisper_num_workers,
                    local_files_only=not allow_download, download_root=str(ROOT / 'models'))
                self.startup_metrics['load_ms'] = round((time.perf_counter()-started)*1000, 2)
            except Exception:
                raise SafeError('Whisper model unavailable; run python main.py --download-model first') from None

    def configuration(self):
        settings = self.settings
        result = {name.removeprefix('whisper_'): getattr(settings, name) for name in (
            'whisper_model', 'whisper_device', 'whisper_compute_type', 'whisper_language',
            'whisper_beam_size', 'whisper_best_of', 'whisper_temperatures',
            'whisper_vad_filter', 'whisper_cpu_threads', 'whisper_warmup', 'whisper_timeout_seconds',
            'whisper_no_speech_threshold', 'whisper_logprob_threshold', 'whisper_compression_threshold')}
        # Identify differing custom hints without printing their potentially private content.
        hint = settings.whisper_hotwords
        result.update(hotwords_enabled=bool(hint),
            hotwords_sha256=hashlib.sha256(hint.encode()).hexdigest() if hint else None,
            num_workers=settings.whisper_num_workers, condition_on_previous_text=settings.whisper_condition_on_previous_text, initial_prompt=None,
            max_decode_attempts_per_chunk=len(settings.whisper_temperatures),
            escalation_enabled=False)
        if self.model is not None:
            backend = getattr(self.model, 'model', None)
            for key in ('device', 'compute_type'):
                value = getattr(backend, key, None)
                if isinstance(value, str): result['effective_' + key] = value
        return result

    def print_configuration(self):
        print('[STT CONFIG] ' + json.dumps(self.configuration(), sort_keys=True))

    def warmup(self):
        """Optional one-time encoder/decoder initialization, never a microphone capture.

        One token on synthetic silence bypasses VAD so inference really runs.
        Its output is discarded and can never become a command or confidence result.
        """
        if self._warmed: return
        self.load()
        import numpy as np
        started = time.perf_counter()
        try:
            segments, _ = self.model.transcribe(np.zeros(16000, dtype=np.float32),
                language=self.settings.whisper_language, temperature=0.0, beam_size=1,
                best_of=1, vad_filter=False, condition_on_previous_text=False,
                initial_prompt=None, hotwords=None, max_new_tokens=1)
            for _ in segments: pass
            self._warmed = True
        except Exception:
            raise SafeError('Local STT warmup failed') from None
        finally:
            self.startup_metrics['warmup_ms'] = round((time.perf_counter()-started)*1000, 2)

    @timed('stt')
    def transcribe(self, audio):
        started = time.perf_counter()
        self.last_metrics = {'accepted': False, 'retry_count': 0, 'escalation_reason': None}
        try:
            if audio is not None:
                import numpy as np
                if not isinstance(audio, np.ndarray) or audio.ndim != 1 or audio.dtype != np.float32:
                    raise SafeError('STT requires mono float32 samples at 16 kHz')
                from audio.segments import audio_metrics
                metrics = audio_metrics(audio.tobytes())
                if metrics['frames'] > self.settings.max_record_seconds * 16000:
                    raise SafeError('Audio exceeds the configured recording limit')
            self.load()
            settings = self.settings
            segments, _ = self.model.transcribe(audio, beam_size=settings.whisper_beam_size,
                best_of=settings.whisper_best_of, language=settings.whisper_language,
                temperature=settings.whisper_temperatures,
                vad_filter=settings.whisper_vad_filter, condition_on_previous_text=settings.whisper_condition_on_previous_text,
                initial_prompt=None, hotwords=settings.whisper_hotwords or None,
                no_speech_threshold=settings.whisper_no_speech_threshold,
                log_prob_threshold=settings.whisper_logprob_threshold,
                compression_ratio_threshold=settings.whisper_compression_threshold)
            result = list(segments)
            if not result: raise SafeError('No speech detected')
            logs = [float(s.avg_logprob) for s in result]
            silence = [float(s.no_speech_prob) for s in result]
            compression = [float(getattr(s, 'compression_ratio', 0)) for s in result]
            temperatures = [float(getattr(s, 'temperature', 0)) for s in result]
            if not all(math.isfinite(v) for v in logs + silence + compression + temperatures):
                raise SafeError('Invalid speech confidence; please repeat')
            words = [float(w.probability) for s in result for w in (getattr(s, 'words', None) or [])]
            if words and not all(math.isfinite(v) and 0 <= v <= 1 for v in words):
                raise SafeError('Invalid word confidence; please repeat')
            self.last_metrics.update(segments=len(result), min_avg_logprob=min(logs),
                max_no_speech_prob=max(silence), max_compression_ratio=max(compression),
                max_decode_temperature=max(temperatures), min_word_probability=min(words) if words else None)
            # Reject likely silence only when both indicators agree.
            likely_silence = all(
                s.no_speech_prob > settings.whisper_no_speech_threshold
                and s.avg_logprob < settings.whisper_logprob_threshold
                for s in result
            )

            # Detect potentially repetitive or corrupted decoding.
            bad_compression = any(
                s.compression_ratio > settings.whisper_compression_threshold
                for s in result
            )

            if likely_silence:
                raise SafeError("No reliable speech detected; please repeat")

            if bad_compression:
                raise SafeError("Unusable transcription; please repeat")
            text = ' '.join(s.text.strip() for s in result).strip()
            if not text or len(text) > 2000:
                raise SafeError('No usable command; please repeat')
            self.last_metrics['accepted'] = True
            return text
        except SafeError:
            raise
        except Exception:
            raise SafeError('Local transcription failed; please repeat') from None
        finally:
            self.last_metrics['stt_ms'] = round((time.perf_counter() - started) * 1000, 2)
