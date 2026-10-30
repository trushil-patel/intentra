"""Explicit opt-in, ten-phrase diagnostic collection; no command execution."""
import json
from pathlib import Path
from utils.logger import redact
from utils.helpers import SafeError

PHRASES = (
    'Open Notepad', 'Open Chrome', 'Open VS Code', 'Press Enter', 'Press Control A',
    'Scroll down', 'Scroll up', 'Switch to Chrome', 'Close Notepad', 'Type hello from VoxPilot',
)


class ComparisonSession:
    def __init__(self, path, settings, completed):
        self.path = Path(path)
        self.settings, self.completed = settings, completed
        self.index = 0
        try:
            self.file = self.path.open('x', encoding='utf-8')
        except FileExistsError:
            raise SafeError('Comparison file already exists; choose a new filename') from None
        self.profile = {key: getattr(settings, key) for key in (
            'voice_mode', 'whisper_model', 'whisper_device', 'whisper_compute_type', 'whisper_language',
            'whisper_beam_size', 'whisper_best_of', 'whisper_temperatures', 'whisper_vad_filter',
            'whisper_cpu_threads', 'whisper_warmup', 'vad_threshold', 'vad_pre_roll_ms', 'vad_silence_timeout_ms', 'vad_min_speech_ms')}
        # The tested phrases are never passed as hints to the transcriber.
        from audio.speech_to_text import SpeechToText
        self.profile['stt_config'] = SpeechToText(settings=settings).configuration()
        self.profile['vocabulary_hint_enabled'] = bool(settings.whisper_hotwords)

    def prompt(self):
        if self.index < len(PHRASES):
            print(f'[COMPARE {self.index+1}/10] After READY/LISTENING and a quiet interval, say: {PHRASES[self.index]}')

    def accept(self, result):
        if self.index >= len(PHRASES): return
        profile = dict(self.profile)
        if isinstance(result.get('stt_config'), dict): profile['stt_config'] = result['stt_config']
        sample = {'expected': PHRASES[self.index], 'profile': profile,
                  'heard': redact(result.get('heard') or ''),
                  'error': redact(result['error']) if result.get('error') else None,
                  'confidence': result.get('confidence', {}), 'segment': result.get('segment'),
                  'accepted': result.get('accepted', not bool(result.get('error'))),
                  'route': result.get('route'), 'grammar': result.get('grammar'), 'total_ms': result.get('total_ms'),
                  'retry_count': result.get('retry_count', 0),
                  'escalation_reason': result.get('escalation_reason')}
        self.file.write(json.dumps(sample, ensure_ascii=True) + '\n')
        self.file.flush()
        if sample['error'] or not sample['accepted'] or sample['route'] == 'REPEAT':
            print('[COMPARE] Speech was unclear; sample recorded as failed.')
        self.index += 1
        if self.index == len(PHRASES):
            print('[COMPARE] Ten samples collected; diagnostic session finished.')
            self.completed()
        else: self.prompt()

    def close(self):
        self.file.close()


def load_samples(path, mode=None):
    samples = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        data = json.loads(line)
        if (mode is not None and data.get('profile', {}).get('voice_mode') != mode) or data.get('expected') not in PHRASES:
            raise SafeError('Invalid comparison record')
        if data['expected'] in samples: raise SafeError('Duplicate comparison record')
        samples[data['expected']] = data
    return samples


def comparison_table(hands_free, push_to_talk):
    def cell(value): return redact(str(value)).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    lines = ['| Command | Hands-Free Transcript | F8 Transcript | HF STT ms | F8 STT ms |',
             '|---|---|---|---:|---:|']
    for phrase in PHRASES:
        hf, ptt = hands_free.get(phrase, {}), push_to_talk.get(phrase, {})
        def heard(sample):
            return sample.get('heard') or ('REJECTED: '+sample['error'] if sample.get('error') else 'Not measured')
        lines.append('| '+' | '.join(cell(v) for v in (phrase, heard(hf), heard(ptt),
            hf.get('confidence',{}).get('stt_ms','—'), ptt.get('confidence',{}).get('stt_ms','—')))+' |')
    return '\n'.join(lines)


def configuration_table(baseline, candidate):
    """Compare any two capture/configuration runs; no correctness claims from routing."""
    def cell(value):
        return redact(str(value)).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    lines = []
    for label, samples in [('Baseline', baseline), ('Candidate', candidate)]:
        profiles = {json.dumps(v.get('profile', {}), sort_keys=True) for v in samples.values()}
        lines.append(label + ' configuration: ' + ('; '.join(sorted(profiles)) or 'Not measured'))
    lines.extend(['', '| Expected | Baseline heard | Candidate heard | B route / accepted | C route / accepted | B STT / total ms | C STT / total ms |',
                  '|---|---|---|---|---|---|---|'])
    for phrase in PHRASES:
        a, b = baseline.get(phrase, {}), candidate.get(phrase, {})
        def heard(sample):
            return sample.get('heard') or ('REJECTED: ' + sample['error'] if sample.get('error') else 'Not measured')
        def route(sample):
            return str(sample.get('route', '?')) + ' / ' + str(sample.get('accepted', '?'))
        def latency(sample):
            return str(sample.get('confidence', {}).get('stt_ms', '?')) + ' / ' + str(sample.get('total_ms', '?'))
        lines.append('| ' + ' | '.join(cell(v) for v in (phrase, heard(a), heard(b), route(a), route(b), latency(a), latency(b))) + ' |')
    lines.append('Accepted means STT confidence passed, not correct recognition or action authorization. Total measures endpoint processing, excluding recording and desktop execution.')
    return '\n'.join(lines)
