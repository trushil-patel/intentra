from config.app_catalog import BROWSERS
"""Foreground-bound Chrome UIA scrolling; never inject a global mouse wheel."""
import hashlib
import math
from pathlib import Path
from utils.helpers import SafeError
from safety.validator import normalize_url, safe_text


def focused_info():
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    return UIAElementInfo(IUIA().iuia.GetFocusedElement())


def resolve_scroll(windows, window):
    """Discover outside document subtrees; bind renderer to approved Chrome root.

    A renderer PID alone is insufficient. The provider must be reached through
    the verified foreground UIA tree AND use the exact approved Chrome binary.
    """
    from tools.element_tools import FORBIDDEN_TARGET
    from safety.ui_guard import Target
    try:
        app = windows.app_of(window)
        if app not in BROWSERS: return resolve_desktop_scroll(windows, window, app)
        if not window.is_visible() or not window.is_enabled(): raise SafeError('Scroll window is unavailable')
        title = window.window_text()
        if not safe_text(title) or FORBIDDEN_TARGET.search(title) or any(v in title.lower() for v in ('devtools', 'developer tools', 'terminal', 'console', 'checkout', 'bank', 'payment')):
            raise SafeError('Protected scroll context')
        focus = focused_info()
        if focus.element.CurrentIsPassword or FORBIDDEN_TARGET.search(focus.name or ''):
            raise SafeError('Protected focused context')
        browser_pid = window.process_id()
        browser_exe = Path(windows.executable(browser_pid)).resolve()
        pending, documents, addresses, visited = [(window, 0)], [], [], 0
        while pending:
            wrapper, depth = pending.pop(0)
            visited += 1
            if visited > 200 or depth > 12: raise SafeError('Scroll discovery exceeds bounds')
            info = wrapper.element_info
            if not info.visible: continue
            if info.process_id != browser_pid:
                if (Path(windows.executable(info.process_id)).resolve() != browser_exe
                        or windows.apps.identify(windows.executable(info.process_id)) != app):
                    raise SafeError('Scroll provider is not owned by this Chrome tree')
            if info.element.CurrentIsPassword:
                raise SafeError('Protected or disabled scroll surface')
            if wrapper is not window and info.control_type == 'Window':
                raise SafeError('Unexpected child window blocks scrolling')
            name = info.name or ''
            if info.control_type == 'Document':
                if not info.enabled: raise SafeError('Disabled scroll document')
                if not safe_text(name) or FORBIDDEN_TARGET.search(name): raise SafeError('Protected document')
                documents.append(wrapper)
                continue  # Never enumerate page text/links or select a nested iframe.
            if info.control_type == 'Edit' and any(label in name.casefold() for label in ('address', 'search or enter')) and info.process_id == browser_pid:
                url = normalize_url(wrapper.iface_value.CurrentValue)
                if not safe_text(url) or FORBIDDEN_TARGET.search(url): raise SafeError('Protected page URL')
                addresses.append(url)
            children = wrapper.children()
            if len(children) + visited + len(pending) > 200: raise SafeError('Scroll tree exceeds bounds')
            pending.extend((child, depth + 1) for child in children)
        if len(documents) != 1 or len(addresses) != 1:
            raise SafeError('Scroll document or page identity is missing or ambiguous')
        document = documents[0]
        info = document.element_info
        runtime_id = tuple(info.runtime_id)
        rect = info.rectangle
        if not runtime_id or rect.right <= rect.left or rect.bottom <= rect.top:
            raise SafeError('Scroll document identity is unavailable')
        pattern = document.iface_scroll
        if not pattern.CurrentVerticallyScrollable: raise SafeError('Document has no vertical ScrollPattern')
        percent(pattern)
        current = windows.active()
        if current.handle != window.handle or current.process_id() != browser_pid:
            raise SafeError('Foreground changed during scroll discovery')
        target = Target(window.handle, browser_pid, runtime_id,
            (rect.left, rect.top, rect.right, rect.bottom), None, app,
            state_id=hashlib.sha256(addresses[0].rstrip('/').encode()).hexdigest(),
            owner_process=info.process_id)
        return target, document
    except SafeError:
        raise
    except Exception:
        raise SafeError('Scroll target cannot be verified with UI Automation') from None


def percent(pattern):
    value = float(pattern.CurrentVerticalScrollPercent)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise SafeError('Scroll position is unavailable')
    return value


def resolve_desktop_scroll(windows, window, app):
    """Select one native scroll surface in an approved foreground application."""
    from safety.ui_guard import Target
    from tools.element_tools import FORBIDDEN_TARGET
    if not isinstance(app, str) or not app or app == 'vscode':
        raise SafeError('Scroll requires an approved application')
    if not window.is_visible() or not window.is_enabled() or not safe_text(window.window_text()):
        raise SafeError('Protected or unavailable scroll window')
    if FORBIDDEN_TARGET.search(window.window_text()):
        raise SafeError('Protected scroll context')
    focus = focused_info()
    if focus.element.CurrentIsPassword or FORBIDDEN_TARGET.search(focus.name or ''):
        raise SafeError('Protected focused context')
    pending, candidates, visited = [(window, 0)], [], 0
    while pending:
        wrapper, depth = pending.pop(0)
        visited += 1
        if visited > 200 or depth > 12:
            raise SafeError('Scroll tree exceeds bounds')
        info = wrapper.element_info
        if not info.visible:
            continue
        if info.process_id != window.process_id() or info.element.CurrentIsPassword:
            raise SafeError('Unverified scroll owner')
        if wrapper is not window and info.control_type == 'Window':
            raise SafeError('A dialog blocks scrolling')
        if info.enabled and safe_text(info.name or '') and not FORBIDDEN_TARGET.search(info.name or ''):
            try:
                pattern = wrapper.iface_scroll
                if pattern.CurrentVerticallyScrollable and 0 <= percent(pattern) <= 100:
                    candidates.append(wrapper)
            except Exception:
                pass
        children = wrapper.children()
        if len(children) + visited + len(pending) > 200:
            raise SafeError('Scroll tree exceeds bounds')
        pending.extend((child, depth + 1) for child in children)
    if len(candidates) != 1:
        raise SafeError('Scroll surface is missing or ambiguous')
    wrapper = candidates[0]
    info, current = wrapper.element_info, windows.active()
    rect = info.rectangle
    if not tuple(info.runtime_id) or rect.right <= rect.left or rect.bottom <= rect.top:
        raise SafeError('Scroll identity is unavailable')
    if current.handle != window.handle or current.process_id() != window.process_id():
        raise SafeError('Foreground changed during scroll discovery')
    return Target(window.handle, window.process_id(), tuple(info.runtime_id),
        (rect.left, rect.top, rect.right, rect.bottom), None, app, owner_process=info.process_id), wrapper


def scroll(windows, guard, stop, direction, amount):
    if direction not in {'up', 'down'} or type(amount) is not int or not 1 <= amount <= 10:
        raise SafeError('Invalid scroll request')
    pending = getattr(guard, 'pending', None)
    if not pending or pending[0].tool != 'scroll' or pending[0].arguments != {'direction': direction, 'amount': amount}:
        raise SafeError('No authorized scroll operation')
    expected = pending[1]
    before = after = None
    for _ in range(amount):
        stop.check()
        guard.verify_input()  # Includes modifier, foreground, URL and target rechecks.
        actual, document = resolve_scroll(windows, windows.active())
        if actual != expected: raise SafeError('Scroll target changed')
        pattern = document.iface_scroll
        if before is None: before = percent(pattern)
        stop.check()
        # UIA ScrollAmount: NoAmount=2, SmallDecrement=1, SmallIncrement=4.
        pattern.Scroll(2, 1 if direction == 'up' else 4)
        stop.check()
        guard.verify_input()
        after = percent(pattern)
    return {'status': 'scroll_observed', 'handle': expected.handle,
            'url_digest': expected.state_id, 'runtime_id': expected.runtime_id,
            'before_percent': before, 'after_percent': after}
