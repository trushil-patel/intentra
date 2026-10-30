from dataclasses import dataclass, field

@dataclass(frozen=True)
class Observation:
    """Untrusted data, never accepted as a command by Brain."""
    text: str = field(repr=False)
    source: str = "screen"
    trust: str = field(default="UNTRUSTED", init=False)
    executable: bool = field(default=False, init=False)
