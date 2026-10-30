from utils.performance import timed
"""Deterministic candidate selection, never execution or model-made coordinates."""
from dataclasses import dataclass, field
import re
from config.settings import AgentLimits
from vision.semantic import ScreenState, ElementReference
from utils.helpers import SafeError


@dataclass(frozen=True)
class GroundingResult:
    candidates: tuple[ElementReference, ...] = field(repr=False)
    selected: ElementReference | None = None
    confidence: float = 0.0
    reason: str = 'No matching element; please identify the target'


ROLES = {'button': 'Button', 'link': 'Hyperlink', 'result': 'Hyperlink',
         'search result': 'Hyperlink', 'field': 'Edit', 'input': 'Edit',
         'tab': 'TabItem', 'option': 'ListItem', 'text': 'Text', 'box': 'Edit',
         'checkbox': 'CheckBox', 'tree item': 'TreeItem', 'radio button': 'RadioButton'}
ORDINALS = {'first': 0, 'second': 1, 'third': 2, 'last': -1}


@timed("grounding")
def ground(screen: ScreenState, query: str, limits: AgentLimits | None = None, previous: ElementReference | None = None) -> GroundingResult:
    limits = limits or AgentLimits()
    if not screen.computer.available or not screen.computer.elements_complete or screen.has_modal:
        return GroundingResult((), reason='Observation incomplete or dialog present; inspect the screen')
    query = query.strip().strip('"').strip("'")
    query = re.sub(r'(?i)^the\s+', '', query)
    original_query = query.casefold()
    if original_query in {'address bar', 'address and search bar'}:
        query = 'Address and search bar'
    candidates = [r for r in screen.elements if r.element.enabled and not r.element.password]
    if screen.computer.current_application in {'chrome', 'edge', 'firefox'}:
        toolbar_request = (original_query in {'address bar', 'address and search bar'}
                           or original_query in {'new tab', 'new tab button'}
                           or (original_query.endswith(' tab')
                               and original_query not in {'search tab'}))
        if toolbar_request:
            candidates = [r for r in candidates
                          if r.element.scope == 'browser_chrome']
    relation = re.fullmatch(r'(?i)(?:the )?(button|link|field|input|tab) (below|above|next to) (.+)', query)
    ordered = re.fullmatch(r'(?i)(?:the )?(first|second|third|last) (search result|result|button|link|tab|option)', query)
    if relation:
        role, direction, anchor = relation.groups()
        if anchor.casefold() in {'it', 'that'}:
            try: anchors = [screen.resolve(previous, max_age=limits.reference_seconds)] if previous else []
            except SafeError: anchors = []
        else:
            anchors = [r for r in candidates if r.element.name.casefold() == anchor.casefold()]
        if len(anchors) != 1 or len(anchors[0].element.bounds) != 4:
            return GroundingResult((), reason='Reference element is missing or ambiguous')
        left, top, right, bottom = anchors[0].element.bounds
        def related(ref):
            e = ref.element
            if e.type != ROLES[role.lower()] or len(e.bounds) != 4 or ref.id == anchors[0].id:
                return False
            x1, y1, x2, y2 = e.bounds
            if direction.lower() == 'below': return y1 >= bottom and x1 < right and x2 > left
            if direction.lower() == 'above': return y2 <= top and x1 < right and x2 > left
            return y1 < bottom and y2 > top and (x1 >= right or x2 <= left)
        candidates = [r for r in candidates if related(r)]
    elif ordered:
        ordinal, role = ordered.groups()
        candidates = [r for r in candidates if r.element.type == ROLES[role.lower()]]
        # Generic links are not proof of search-result membership.
        if 'result' in role.lower():
            candidates = [r for r in candidates if r.element.automation_id.startswith('search-result-')]
        if not candidates or any(len(r.element.bounds) != 4 for r in candidates):
            return GroundingResult((), reason='Ordering cannot be established; select the target manually')
        candidates.sort(key=lambda r: (r.element.bounds[1], r.element.bounds[0]))
        # Overlapping candidates do not establish an unambiguous reading order.
        for a, b in zip(candidates, candidates[1:]):
            if b.element.bounds[1] < a.element.bounds[3]:
                return GroundingResult(tuple(candidates), reason='Ordering is ambiguous; clarify the target')
        index = ORDINALS[ordinal.lower()]
        candidates = [candidates[index]] if -len(candidates) <= index < len(candidates) else []
    else:
        labelled = re.fullmatch(r'(?i)(?:the )?(.+?) (radio button|tree item|checkbox|button|link|field|input|box|tab)', query)
        if labelled:
            label, role = labelled.groups()
            exact_names = {label.casefold()}
            if role.lower() == 'box':
                exact_names.add((label + ' box').casefold())
            candidates = [r for r in candidates if r.element.type == ROLES[role.lower()]
                          and r.element.name.casefold() in exact_names]
        else:
            candidates = [r for r in candidates if r.element.name.casefold() == query.casefold()]
    candidates = tuple(candidates)
    if len(candidates) != 1:
        reason = 'Multiple matching elements; specify a label, role or position' if candidates else 'No matching element; please identify the target'
        return GroundingResult(candidates, reason=reason)
    ref = candidates[0]
    confidence = ref.element.confidence
    if not limits.grounding_confidence <= confidence <= 1 or ref.element.source not in {'uia', 'accessibility'}:
        return GroundingResult(candidates, confidence=confidence, reason='Structured verification required before interaction')
    return GroundingResult(candidates, ref, confidence, 'Unique structured element')
