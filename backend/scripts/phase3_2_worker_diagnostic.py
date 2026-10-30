"""Safe, read-only diagnostic for the Phase 3.2 target verification worker."""
import json
from pathlib import Path
from config.app_catalog import discover_apps
from safety.emergency_stop import EmergencyStop
from vision.observer import Observer

ROOT = Path(__file__).resolve().parents[1]

def main():
    allowlist = discover_apps(json.loads((ROOT / "config" / "apps.json").read_text(encoding="utf-8")))
    observer = Observer(allowlist, EmergencyStop())
    initial = observer.observe()
    target = next((item for item in initial.elements if item.type == "TabItem" and item.name == "View"), None)
    summary = {
        "initial_status": observer.last_status,
        "application": initial.current_application,
        "initial_available": initial.available,
        "initial_complete": initial.elements_complete,
        "initial_elements": initial.visited_count,
        "initial_provider_failure": initial.provider_failure,
        "target_accessible": target is not None,
    }
    if target is not None and initial.active_window is not None:
        from vision.semantic import element_identity
        request = {
            "handle": initial.active_window.handle, "app": initial.current_application,
            "type": target.type, "name": target.name, "bounds": target.bounds,
            "automation_id": target.automation_id, "runtime_id": target.runtime_id,
            "target_identity": element_identity(target), "operation": "activate",
            "control_type": target.type,
        }
        verified = observer.observe(target=request)
        owner = verified.foreground_owner
        summary.update({
            "worker_status": observer.last_status,
            "verification_available": verified.available,
            "target_complete": verified.target_verification_complete,
            "matches": len(verified.elements),
            "selected": verified.elements[0].selected if len(verified.elements) == 1 else None,
            "provider_failure": verified.provider_failure,
            "candidate_count": verified.visited_count,
            "traversal_ms": verified.traversal_ms,
            "foreground_owner": ({
                "raw_handle": owner.raw_handle,
                "top_level_handle": owner.top_level_handle,
                "pid": owner.pid,
                "executable_name": owner.executable_name,
                "executable_digest": owner.executable_digest,
                "application": owner.application,
                "window_class": owner.window_class,
                "visible": owner.visible,
                "owner_handle": owner.owner_handle,
                "root_owner_handle": owner.root_owner_handle,
                "category": owner.category,
                "captured_monotonic_ns": owner.captured_monotonic_ns,
                "capture_process_id": owner.capture_process_id,
            } if owner else None),
            "verification_started_monotonic_ns":
                verified.verification_started_monotonic_ns,
            "verification_process_id": verified.verification_process_id,
        })
    print(json.dumps(summary, sort_keys=True))

if __name__ == "__main__":
    main()
