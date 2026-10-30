from typing import Protocol
from dataclasses import dataclass, field
from agent.computer_state import Element


@dataclass(frozen=True)
class VisionObservation:
    regions: tuple[Element, ...]
    description: str = field(default='', repr=False)
    source: str = 'vision'
    trusted: bool = False


class VisionProvider(Protocol):
    def analyze_screen(self, image, context) -> VisionObservation: ...
    def locate_elements(self, image, query, context) -> VisionObservation: ...
    def describe_region(self, image, region, context) -> VisionObservation: ...
