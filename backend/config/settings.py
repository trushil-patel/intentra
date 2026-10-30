"""Trusted startup configuration; never writable through agent tools."""
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AgentLimits:
    grounding_confidence: float = 0.90
    reference_seconds: float = 45
    wait_seconds: float = 60
    poll_seconds: float = 0.25
    task_seconds: float = 300
    task_actions: int = 20
    max_replans: int = 2

    def __post_init__(self):
        import math
        values = (self.grounding_confidence, self.reference_seconds, self.wait_seconds,
                  self.poll_seconds, self.task_seconds)
        if any(type(v) not in {int, float} or not math.isfinite(v) for v in values):
            raise ValueError('Invalid agent limits')
        if (not 0.9 <= self.grounding_confidence <= 1 or not 1 <= self.reference_seconds <= 60
                or not 1 <= self.wait_seconds <= 120 or not 0.05 <= self.poll_seconds <= 2
                or not 10 <= self.task_seconds <= 600 or type(self.task_actions) is not int
                or not 1 <= self.task_actions <= 20 or type(self.max_replans) is not int
                or not 0 <= self.max_replans <= 2):
            raise ValueError('Unsafe agent limits')

@dataclass(frozen=True)
class Settings:
    whisper_model: str = "base.en"
    ollama_model: str = "qwen2.5:3b"
    ollama_url: str = "http://127.0.0.1:11434"
    max_actions: int = 10
    max_record_seconds: int = 20
    confirmation_seconds: int = 30
    debug_recording: bool = False
    tts: bool = False
    screenshots: bool = True
    ocr: bool = False
    voice_mode: str = "push_to_talk"
    vad_silence_timeout_ms: int = 768
    vad_min_speech_ms: int = 256
    vad_cooldown_ms: int = 256
    tts_cooldown_ms: int = 500
    duplicate_seconds: float = 2.0
    vad_pre_roll_ms: int = 256
    vad_threshold: float = 0.5
    whisper_device: str = 'cpu'
    whisper_compute_type: str = 'int8'
    whisper_language: str = 'en'
    whisper_beam_size: int = 5
    whisper_best_of: int = 5
    whisper_temperatures: tuple = (0.0,)
    whisper_vad_filter: bool = True
    whisper_hotwords: str = ''
    whisper_timeout_seconds: float = 15.0
    whisper_cpu_threads: int = 2
    whisper_num_workers: int = 1
    whisper_condition_on_previous_text: bool = False
    whisper_warmup: bool = False
    whisper_no_speech_threshold: float = 0.6
    whisper_logprob_threshold: float = -1.0
    whisper_compression_threshold: float = 2.4
    voice_diagnostics: bool = False

    def __post_init__(self):
        import math
        if type(self.whisper_timeout_seconds) not in {int, float} or not math.isfinite(self.whisper_timeout_seconds) or not 1 <= self.whisper_timeout_seconds <= 30:
            raise ValueError('Invalid STT timeout')
        if type(self.whisper_cpu_threads) is not int or not 1 <= self.whisper_cpu_threads <= 16:
            raise ValueError('Invalid STT CPU thread budget')
        if type(self.whisper_num_workers) is not int or self.whisper_num_workers != 1:
            raise ValueError('V3 requires exactly one STT inference worker')
        if type(self.whisper_condition_on_previous_text) is not bool:
            raise ValueError('Invalid STT previous-text setting')
        if type(self.whisper_warmup) is not bool:
            raise ValueError('Invalid STT warmup setting')
        if self.whisper_device not in {'cpu', 'cuda', 'auto'} or self.whisper_compute_type not in {'int8', 'float16', 'float32', 'int8_float16', 'auto'}:
            raise ValueError('Invalid local STT device/compute type')
        if not self.whisper_language.isalpha() or len(self.whisper_language) not in {2, 3}:
            raise ValueError('Invalid STT language')
        if type(self.whisper_beam_size) is not int or not 1 <= self.whisper_beam_size <= 10 or type(self.whisper_best_of) is not int or not 1 <= self.whisper_best_of <= 10:
            raise ValueError('Invalid STT decoding budget')
        if not self.whisper_temperatures or len(self.whisper_temperatures) > 6 or any(not math.isfinite(t) or not 0 <= t <= 1 for t in self.whisper_temperatures):
            raise ValueError('Invalid STT temperatures')
        if (not math.isfinite(self.whisper_no_speech_threshold) or not 0 < self.whisper_no_speech_threshold <= .6
                or not math.isfinite(self.whisper_logprob_threshold) or not -1 <= self.whisper_logprob_threshold <= 0
                or not math.isfinite(self.whisper_compression_threshold) or not 1 <= self.whisper_compression_threshold <= 2.4):
            raise ValueError('STT confidence thresholds may only be tightened')
        if len(self.whisper_hotwords) > 500 or any(ord(c) < 32 for c in self.whisper_hotwords):
            raise ValueError('Invalid STT vocabulary hint')
        if type(self.vad_pre_roll_ms) is not int or not 128 <= self.vad_pre_roll_ms <= 512 or not .1 <= self.vad_threshold <= .9:
            raise ValueError('Invalid VAD configuration')
        if self.voice_mode not in {'hands_free', 'push_to_talk'}:
            raise ValueError('Invalid voice mode')
        for value in (self.vad_silence_timeout_ms, self.vad_min_speech_ms, self.vad_cooldown_ms, self.tts_cooldown_ms):
            if type(value) is not int or not 128 <= value <= 3000:
                raise ValueError('Invalid voice timing')
        if not 0.1 <= self.duplicate_seconds <= 5:
            raise ValueError('Invalid duplicate interval')
        url = urlsplit(self.ollama_url)
        if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.username or url.password or url.path not in {"", "/"}
                or url.query or url.fragment):
            raise ValueError("Ollama must use a local HTTP endpoint")
        if not 1 <= self.max_actions <= 10 or not 1 <= self.max_record_seconds <= 60:
            raise ValueError("Unsafe limits")
        if not 5 <= self.confirmation_seconds <= 60:
            raise ValueError("Invalid confirmation timeout")

    @classmethod
    def load(cls):
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env", override=False)
        except ImportError:
            pass
        return cls(whisper_timeout_seconds=float(os.getenv('WHISPER_TIMEOUT_SECONDS', '15')),
                   whisper_cpu_threads=int(os.getenv('WHISPER_CPU_THREADS', '2')),
                   whisper_num_workers=int(os.getenv('WHISPER_NUM_WORKERS', '1')),
                   whisper_condition_on_previous_text=os.getenv('WHISPER_CONDITION_ON_PREVIOUS_TEXT', 'false').lower() == 'true',
                   whisper_no_speech_threshold=float(os.getenv('WHISPER_NO_SPEECH_THRESHOLD', '0.6')),
                   whisper_logprob_threshold=float(os.getenv('WHISPER_LOGPROB_THRESHOLD', '-1')),
                   whisper_compression_threshold=float(os.getenv('WHISPER_COMPRESSION_THRESHOLD', '2.4')),
                   whisper_warmup=os.getenv('WHISPER_WARMUP', 'false').lower() == 'true',
                   whisper_device=os.getenv('WHISPER_DEVICE', 'cpu'),
                   whisper_compute_type=os.getenv('WHISPER_COMPUTE_TYPE', 'int8'),
                   whisper_language=os.getenv('WHISPER_LANGUAGE', 'en'),
                   whisper_beam_size=int(os.getenv('WHISPER_BEAM_SIZE', '5')),
                   whisper_best_of=int(os.getenv('WHISPER_BEST_OF', '5')),
                   whisper_temperatures=tuple(float(v) for v in os.getenv('WHISPER_TEMPERATURES', '0').split(',')),
                   whisper_vad_filter=os.getenv('WHISPER_VAD_FILTER', 'true').lower() == 'true',
                   whisper_hotwords=os.getenv('WHISPER_HOTWORDS', '').strip(),
                   voice_diagnostics=os.getenv('VOICE_DIAGNOSTICS', 'false').lower() == 'true',
                   vad_pre_roll_ms=int(os.getenv('VAD_PRE_ROLL_MS', '256')),
                   vad_threshold=float(os.getenv('VAD_THRESHOLD', '0.5')),
                   voice_mode=os.getenv("VOICE_MODE", "push_to_talk"),
                   vad_silence_timeout_ms=int(os.getenv("VAD_SILENCE_TIMEOUT_MS", "768")),
                   vad_min_speech_ms=int(os.getenv("VAD_MIN_SPEECH_MS", "256")),
                   vad_cooldown_ms=int(os.getenv("VAD_COOLDOWN_MS", "256")),
                   tts_cooldown_ms=int(os.getenv("TTS_COOLDOWN_MS", "500")),
                   whisper_model=os.getenv("WHISPER_MODEL", "base.en"),
                   ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                   ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
                   debug_recording=os.getenv("DEBUG_RECORDING", "false").lower() == "true",
                   screenshots=os.getenv("SCREENSHOTS", "true").lower() == "true",
                   ocr=os.getenv("LOCAL_OCR", "false").lower() == "true",
                   tts=os.getenv("TTS", "false").lower() == "true")
