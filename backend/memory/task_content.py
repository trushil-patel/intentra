"""Untrusted extracted text is never a command, plan, or permission."""
from dataclasses import dataclass, field
import re
from utils.helpers import SafeError


@dataclass(frozen=True)
class TaskContent:
    text: str = field(repr=False)
    source: str
    reference: str
    trust: str = field(default='UNTRUSTED', init=False)
    executable: bool = field(default=False, init=False)

    @classmethod
    def create(cls, text, source, reference):
        if (type(text) is not str or not 1 <= len(text) <= 4000
                or any((ord(c) < 32 and c not in '\n\r\t') or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
            raise SafeError('Extracted text is empty, unsupported or exceeds the transfer limit')
        if re.search(r'(?i)password|passwd|api[ _-]?key|secret|token|cookie|authorization|private[ _-]?key|(?:\d[ -]?){13,19}|sk-[A-Za-z0-9]+|ghp_|github_pat_', text):
            raise SafeError('Sensitive extracted content requires manual handling')
        return cls(text, source, reference)
