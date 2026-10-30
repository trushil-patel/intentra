from dataclasses import dataclass
from enum import StrEnum

class Source(StrEnum):
    USER = "user"
    OBSERVED = "observed"

@dataclass(frozen=True)
class UserCommand:
    text: str
    source: Source = Source.USER
