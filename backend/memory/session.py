from dataclasses import dataclass
from uuid import uuid4

@dataclass
class Session:
    """No transcript history or persistent user memory in V1."""
    identifier: str = ""
    completed_commands: int = 0

    def __post_init__(self):
        self.identifier = uuid4().hex
