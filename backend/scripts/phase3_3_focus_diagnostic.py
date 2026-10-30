"""Privacy-safe, read-only Chrome address-bar focus evidence diagnostic."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.app_catalog import discover_apps
from safety.emergency_stop import EmergencyStop
from vision.grounding import ground
from vision.observer import Observer
from vision.semantic import ScreenState, element_identity


def main():
    allowlist = discover_apps(json.loads(
        (ROOT / 'config' / 'apps.json').read_text(encoding='utf-8')))
    observer = Observer(allowlist, EmergencyStop())
    started = time.perf_counter()
    discovered = observer.observe(roles=('Edit',))
    discovery_ms = (time.perf_counter() - started) * 1000
    grounded = ground(ScreenState.from_computer(discovered), 'address bar')
    output = {
        'application': discovered.current_application,
        'discovery_status': observer.last_status,
        'discovery_complete': discovered.elements_complete,
        'edit_candidates': discovered.visited_count,
        'address_bar_unique': grounded.selected is not None,
        'discovery_ms': round(discovery_ms, 2),
    }
    if grounded.selected is not None:
        reference = grounded.selected
        element = reference.element
        target = {
            'handle': reference.handle, 'app': reference.app,
            'type': element.type, 'name': element.name,
            'bounds': element.bounds, 'automation_id': element.automation_id,
            'runtime_id': element.runtime_id,
            'target_identity': element_identity(element),
            'scope': element.scope, 'operation': 'focus',
            'control_type': element.type,
        }
        started = time.perf_counter()
        verified = observer.observe(target=target)
        verification_ms = (time.perf_counter() - started) * 1000
        match = verified.elements[0] if len(verified.elements) == 1 else None
        output.update({
            'verification_status': observer.last_status,
            'target_query_complete': verified.target_verification_complete,
            'target_matches': len(verified.elements),
            'target_property_focus': match.focused if match else None,
            'target_keyboard_focusable': (match.keyboard_focusable
                                          if match else None),
            'focused_id_matches_target': bool(
                match and verified.focused_id == match.runtime_id),
            'focus_evidence': verified.focus_evidence,
            'focused_element_runtime_matches_target': bool(
                match and verified.focused_element_runtime_id == match.runtime_id),
            'focused_element_type': verified.focused_element_type,
            'focused_element_automation_id_present': bool(
                verified.focused_element_automation_id),
            'focused_element_provider_owned':
                verified.focused_element_provider_owned,
            'focused_element_scope': verified.focused_element_scope,
            'provider_failure': verified.provider_failure,
            'verification_ms': round(verification_ms, 2),
            'target_traversal_ms': verified.traversal_ms,
        })
    print(json.dumps(output, sort_keys=True))


if __name__ == '__main__':
    main()
