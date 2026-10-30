"""Narrow voice-navigation aliases confirmed by the user; never rewrite payloads."""
import re


_CLAUDE_NAVIGATION = re.compile(
    r"(?i)^(?P<prefix>\s*(?:(?:please|can you|could you)\s+)?"
    r"(?:open|visit|go to|navigate to)\s+(?:the\s+)?)"
    r"(?:cloud\.ai|claud\.ai)(?=$|[.!?](?:\s|$)|\s+and\b|,\s)"
)


def normalize_spoken_navigation(text):
    return _CLAUDE_NAVIGATION.sub(lambda match: match['prefix'] + 'Claude.ai', text, count=1)


def normalize_transcript(text):
    """Normalize syntax only. Literal typing payloads remain byte-for-byte intact."""
    from utils.performance import measure
    with measure('normalizer'):
        text = normalize_spoken_navigation(text)
        # Preserve payload spacing/casing/punctuation, including compound commands.
        match = re.search(r'(?i)\b(?:type|write|search(?:\s+web)?\s+for|ask it to|enter)\s*:?\s+', text)
        if match:
            return re.sub(r'\s+', ' ', text[:match.start()]).lstrip() + text[match.start():]
        return re.sub(r'\s+', ' ', text).strip()
