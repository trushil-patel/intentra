import json
from utils.logger import redact
from vision.semantic import ScreenState


def inspection_report(state):
    screen = ScreenState.from_computer(state)
    return {'available': state.available, 'complete': state.elements_complete,
            'application': state.current_application,
            'window_handle': state.active_window.handle if state.active_window else None,
            'state_id': screen.state_id, 'modal': screen.has_modal,
            'windows': [{'handle': w.handle, 'application': w.app} for w in state.open_windows],
            'diagnostics': {'element_count': len(state.elements), 'visited_count': state.visited_count,
                'max_visited_depth': state.max_visited_depth, 'limit_reached': state.limit_reached,
                'traversal_ms': state.traversal_ms, 'provider_failure': state.provider_failure,
                'target_verification_complete': state.target_verification_complete,
                'application_verified': bool(state.current_application),
                'window_verified': bool(state.active_window),
                'foreground_owner': ({
                    'raw_handle': state.foreground_owner.raw_handle,
                    'top_level_handle': state.foreground_owner.top_level_handle,
                    'pid': state.foreground_owner.pid,
                    'executable_name': state.foreground_owner.executable_name,
                    'executable_digest': state.foreground_owner.executable_digest,
                    'application': state.foreground_owner.application,
                    'window_class': state.foreground_owner.window_class,
                    'visible': state.foreground_owner.visible,
                    'owner_handle': state.foreground_owner.owner_handle,
                    'root_owner_handle': state.foreground_owner.root_owner_handle,
                    'category': state.foreground_owner.category,
                    'captured_monotonic_ns': state.foreground_owner.captured_monotonic_ns,
                    'capture_process_id': state.foreground_owner.capture_process_id,
                } if state.foreground_owner else None),
                'verification_started_monotonic_ns': state.verification_started_monotonic_ns,
                'verification_process_id': state.verification_process_id},
            'elements': [{'id': r.id, 'role': r.element.type,
                          'name': '[PROTECTED]' if r.element.password else redact(r.element.name),
                          'bounds': r.element.bounds, 'source': r.element.source,
                          'confidence': r.element.confidence, 'focused': r.element.focused}
                         for r in screen.elements]}
