"""Read-only Phase 3.3 UIA completeness and grounding profiler."""
import argparse
import json
import statistics
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.app_catalog import discover_apps
from safety.emergency_stop import EmergencyStop
from vision.grounding import ground
from vision.observer import Observer, roles_for_query
from vision.semantic import ScreenState

QUERIES = {
    'explorer': ('View tab',),
    'notepad': ('text editor',),
    'chrome': ('address bar', 'New Tab button'),
}


def stats(values):
    return {'n': len(values), 'min_ms': round(min(values), 2),
            'median_ms': round(statistics.median(values), 2),
            'max_ms': round(max(values), 2)} if values else None


def state_summary(observer, state, elapsed_ms):
    return {
        'status': observer.last_status,
        'application': state.current_application,
        'available': state.available,
        'complete': state.elements_complete,
        'element_count': len(state.elements),
        'visited_count': state.visited_count,
        'max_depth': state.max_visited_depth,
        'limit_reached': state.limit_reached,
        'provider_failure': state.provider_failure,
        'traversal_ms': state.traversal_ms,
        'wall_ms': round(elapsed_ms, 2),
        'queried_roles': state.queried_roles,
        'query_scope': state.query_scope,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('application', choices=tuple(QUERIES))
    parser.add_argument('--samples', type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    allowlist = discover_apps(json.loads(
        (ROOT / 'config' / 'apps.json').read_text(encoding='utf-8')))
    observer = Observer(allowlist, EmergencyStop())
    output = {'expected_application': args.application, 'full': [], 'queries': {}}
    full_times = []
    for _ in range(args.samples):
        started = time.perf_counter()
        state = observer.observe()
        elapsed = (time.perf_counter() - started) * 1000
        full_times.append(elapsed)
        output['full'].append(state_summary(observer, state, elapsed))
    output['full_performance'] = stats(full_times)
    output['identity_verified'] = all(
        item['application'] == args.application for item in output['full'])
    for query in QUERIES[args.application]:
        role_times, grounding_times, samples = [], [], []
        for _ in range(args.samples):
            roles = roles_for_query(query)
            started = time.perf_counter()
            state = observer.observe(roles=roles)
            elapsed = (time.perf_counter() - started) * 1000
            role_times.append(elapsed)
            ground_started = time.perf_counter()
            result = ground(ScreenState.from_computer(state), query)
            grounding_times.append((time.perf_counter() - ground_started) * 1000)
            item = state_summary(observer, state, elapsed)
            item.update({
                'grounded': result.selected is not None,
                'grounding_reason': result.reason,
                'candidate_count': len(result.candidates),
                'selected_role': result.selected.element.type if result.selected else None,
                'selected_scope': result.selected.element.scope if result.selected else None,
                'selected_name_matches_query_contract': bool(
                    result.selected and (
                        query.casefold().replace(' button', '') in
                        result.selected.element.name.casefold()
                        or query.casefold() == 'address bar' and
                        'address' in result.selected.element.name.casefold()
                        or query.casefold() == 'text editor' and
                        result.selected.element.type in {'Edit', 'Document'})),
            })
            samples.append(item)
        output['queries'][query] = {
            'roles': roles_for_query(query),
            'samples': samples,
            'role_performance': stats(role_times),
            'grounding_performance': stats(grounding_times),
        }
    print(json.dumps(output, sort_keys=True))


if __name__ == '__main__':
    main()
