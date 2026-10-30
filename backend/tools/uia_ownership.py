"""Verify UIA provider ownership without trusting process names alone."""
from pathlib import Path
from config.app_catalog import BROWSERS


def provider_owned(windows, window, info):
    if info.process_id == window.process_id():
        return True
    app = windows.app_of(window)
    if app not in BROWSERS:
        return False
    try:
        executable = windows.executable(info.process_id)
        return (Path(executable).resolve() == Path(windows.executable(window.process_id())).resolve()
                and windows.apps.identify(executable) == app)
    except Exception:
        return False


def focused_descendant(windows, window, info):
    """Cross-process focused targets must also be found in this foreground tree."""
    if not provider_owned(windows, window, info):
        return False
    pending, visited = [(window, 0)], 0
    while pending:
        wrapper, depth = pending.pop(0)
        visited += 1
        if visited > 200 or depth > 12:
            return False
        candidate = wrapper.element_info
        if not provider_owned(windows, window, candidate):
            continue
        if (candidate.process_id == info.process_id and tuple(candidate.runtime_id) == tuple(info.runtime_id)
                and candidate.visible and candidate.enabled):
            return True
        children = wrapper.children()
        if visited + len(pending) + len(children) > 200:
            return False
        pending.extend((child, depth + 1) for child in children)
    return False
