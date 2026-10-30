from tools.uia_ownership import provider_owned
from config.app_catalog import BROWSERS
from utils.performance import timed
"""Bounded, local UIA observation in a disposable process, never a model tool executor."""
import hashlib
import os
import multiprocessing
import time
from agent.computer_state import ComputerState, WindowState, Element
from safety.validator import normalize_url, safe_text
from utils.helpers import Cancelled


def url_digest(url):
    return hashlib.sha256(normalize_url(url).rstrip("/").encode()).hexdigest()


@timed('foreground_metadata')
def _scoped_windows(windows, foreground_context=None):
    """Return a lightweight snapshot and its verified foreground UIA wrapper."""
    allowlist = getattr(getattr(windows, 'apps', None), 'allowlist', None)
    if isinstance(allowlist, dict):
        from tools.window_metadata import collect_metadata
        state = collect_metadata(allowlist, foreground_context)
        if not state.available:
            return state, None
        current = state.active_window
        if current is None:
            return state, None
        try:
            wrapper = windows.desktop().window(handle=current.handle).wrapper_object()
            if wrapper.handle != current.handle or windows.app_of(wrapper) != current.app:
                return ComputerState(open_windows=state.open_windows), None
            if current.app == 'explorer' and wrapper.class_name() != 'CabinetWClass':
                return ComputerState(open_windows=state.open_windows), None
            return state, wrapper
        except Exception:
            return ComputerState(open_windows=state.open_windows), None

    wrappers = windows.approved_windows()
    if len(wrappers) > 50:
        return ComputerState(), None
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32")
    user.IsIconic.argtypes = [wintypes.HWND]
    user.IsZoomed.argtypes = [wintypes.HWND]
    items = tuple(WindowState(w.handle, windows.app_of(w), bool(user.IsIconic(w.handle)),
                              bool(user.IsZoomed(w.handle)), w.window_text())
                  for w in wrappers[:50])
    try:
        active = windows.active()
        current = next((w for w in items if w.handle == active.handle), None)
    except Exception:
        active, current = None, None
    return ComputerState(current, items, available=True, elements_complete=False), active


@timed('uia_tree_collection')
def collect(windows, expected_text=None, level=1, foreground_context=None):
    started = time.perf_counter()
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    from vision.element_finder import safe_editor
    metadata, active = _scoped_windows(windows, foreground_context)
    items, current = metadata.open_windows, metadata.active_window
    if level == 0:
        return metadata
    elements, focused_id, matched, address, text_length, text_digest = [], (), None, None, None, None
    complete = False
    line_breaks = None
    origin = None
    visited = 0
    max_depth = 0
    limit_reached = None
    provider_failure = None
    try:
        from config.app_catalog import input_supported
        if current and active and input_supported(windows, active) and safe_text(current.title):
            try:
                focused_runtime = tuple(UIAElementInfo(IUIA().iuia.GetFocusedElement()).runtime_id)
            except Exception:
                focused_runtime = ()
            # Depth and node budgets bound returned data; the enclosing process bounds native calls.
            pending = [(active, 0)]
            visited = 0
            complete = True
            while pending and visited < 200:
                wrapper, depth = pending.pop(0)
                visited += 1
                max_depth = max(max_depth, depth)
                info = wrapper.element_info
                if info.visible and provider_owned(windows, active, info):
                    password = bool(info.element.CurrentIsPassword)
                    rect = info.rectangle
                    modal = depth > 0 and info.control_type == 'Window'
                    if info.control_type == 'Window':
                        try: modal = modal or bool(wrapper.iface_window.CurrentIsModal)
                        except Exception: pass
                    content_digest = None
                    if not password and ((info.name or '').casefold() in {
                            "claude response", "claude's response", "assistant response", "assistant message"}
                            or info.automation_id == 'assistant-response'):
                        try:
                            value = wrapper.iface_text.DocumentRange.GetText(4001)
                            if isinstance(value, str) and 0 < len(value) <= 4000:
                                content_digest = hashlib.sha256(value.encode()).hexdigest()
                        except Exception: pass
                    selected = False
                    checked = None
                    expanded = None
                    if info.control_type in {'TabItem', 'ListItem', 'RadioButton', 'TreeItem'}:
                        try:
                            selected = bool(wrapper.iface_selection_item.CurrentIsSelected)
                        except Exception:
                            pass
                    if info.control_type == 'CheckBox':
                        try:
                            checked = int(wrapper.iface_toggle.CurrentToggleState) == 1
                        except Exception:
                            pass
                    if info.control_type == 'TreeItem':
                        try:
                            expand_state = int(wrapper.iface_expand_collapse.CurrentExpandCollapseState)
                            expanded = {0: 'collapsed', 1: 'expanded'}.get(expand_state)
                        except Exception:
                            pass
                    element = Element(tuple(info.runtime_id), info.control_type,
                                      "" if password else (info.name or "")[:200], bool(info.enabled), password,
                                      read_only=current.app in BROWSERS and info.control_type == "Document",
                                      bounds=(rect.left, rect.top, rect.right, rect.bottom),
                                      focused=(bool(getattr(info.element, 'CurrentHasKeyboardFocus', False))
                                               or (bool(focused_runtime)
                                                   and tuple(info.runtime_id) == focused_runtime)),
                                      selected=selected, checked=checked, expanded=expanded,
                                      clickable=info.control_type in {'Button', 'Hyperlink', 'TabItem', 'MenuItem',
                                                                     'ListItem', 'TreeItem', 'CheckBox', 'RadioButton'},
                                      automation_id=info.automation_id or '',
                                      modal=modal, content_digest=content_digest)
                    elements.append(element)
                    if element.focused and not password:
                        focused_id = element.runtime_id
                        if safe_editor(element):
                            try:
                                value = wrapper.iface_value.CurrentValue
                                text_length = len(value) if isinstance(value, str) and len(value) <= 100000 else None
                                line_breaks = value.count("\n") if text_length is not None else None
                                text_digest = hashlib.sha256(value.encode()).hexdigest() if text_length is not None else None
                                matched = value.replace("\r\n", "\n").endswith(expected_text.replace("\r\n", "\n")) if text_length is not None and expected_text is not None else None
                            except Exception:
                                try:
                                    value = wrapper.iface_text.DocumentRange.GetText(100001)
                                    text_length = len(value) if len(value) <= 100000 else None
                                    line_breaks = value.count("\n") if text_length is not None else None
                                    text_digest = hashlib.sha256(value.encode()).hexdigest() if text_length is not None else None
                                    matched = value.replace("\r\n", "\n").rstrip("\r\n").endswith(expected_text.replace("\r\n", "\n")) if text_length is not None and expected_text is not None else None
                                except Exception:
                                    pass
                    if current.app in BROWSERS and info.process_id == active.process_id() and not password and element.type == "Edit" and any(label in element.name.casefold() for label in ("address", "search or enter")):
                        try:
                            from urllib.parse import urlsplit
                            raw_url = normalize_url(wrapper.iface_value.CurrentValue)
                            address = url_digest(raw_url)
                            parts = urlsplit(raw_url)
                            origin = url_digest(parts.scheme + '://' + parts.netloc)
                        except Exception:
                            pass
                children = wrapper.children()
                remaining = 200 - visited - len(pending)
                if children and (depth >= 12 or len(children) > remaining):
                    complete = False
                    limit_reached = 'DEPTH_LIMIT' if depth >= 12 else 'ELEMENT_LIMIT'
                if depth < 12:
                    pending.extend((child, depth + 1) for child in children[:max(0, remaining)])
            complete = complete and not pending
            if pending and limit_reached is None:
                limit_reached = 'ELEMENT_LIMIT'
    except Exception:
        # Accessibility patterns can disappear mid-observation. Preserve only the
        # independently observed windows, never manufacture field evidence.
        elements, focused_id, matched, address, text_length, text_digest = [], (), None, None, None, None
        complete = False
        provider_failure = 'UIA_PROVIDER_ERROR'
    return ComputerState(current, items, tuple(elements), available=True, focused_id=focused_id,
                         text_matches=matched, url_digest=address, focused_text_length=text_length, focused_text_digest=text_digest,
                         elements_complete=complete, origin_digest=origin, focused_line_breaks=line_breaks,
                         visited_count=visited, max_visited_depth=max_depth,
                         limit_reached=limit_reached,
                         traversal_ms=round((time.perf_counter() - started) * 1000, 2),
                         provider_failure=provider_failure,
                         foreground_owner=metadata.foreground_owner)


def _diagnostic_state(category, *, current=None, open_windows=()):
    """Return structural failure evidence without exposing UI content."""
    return ComputerState(current, tuple(open_windows), available=current is not None,
                         elements_complete=False, provider_failure=category)




def roles_for_query(query):
    """Return the complete UIA role set needed by deterministic grounding."""
    import re
    value = query.strip().strip('"').strip("'").casefold()
    value = re.sub(r'^the\s+', '', value)
    if value in {'address bar', 'address and search bar', 'search field',
                 'search box'}:
        return ('Edit',)
    if value in {'text editor', 'editor'}:
        return ('Edit', 'Document')
    labelled = re.fullmatch(
        r'(?:the )?.+? (radio button|tree item|checkbox|button|link|field|input|box|tab)',
        value)
    mapping = {'radio button': 'RadioButton', 'tree item': 'TreeItem',
               'checkbox': 'CheckBox', 'button': 'Button',
               'link': 'Hyperlink', 'field': 'Edit', 'input': 'Edit',
               'box': 'Edit', 'tab': 'TabItem'}
    if labelled:
        return (mapping[labelled.group(1)],)
    return ()


def _wrapper_scope(wrapper, root, app):
    if app not in BROWSERS:
        return 'application'
    current = wrapper
    for _ in range(32):
        try:
            if current.element_info.control_type == 'Document':
                return 'webpage'
            parent = current.parent()
        except Exception:
            break
        if parent is None or getattr(parent, 'handle', None) == root.handle:
            break
        current = parent
    return 'browser_chrome'


@timed('uia_role_collection')
def collect_roles(windows, roles, foreground_context=None):
    """Collect every candidate of requested roles under one verified UIA root."""
    started = time.perf_counter()
    roles = tuple(dict.fromkeys(roles))
    metadata, active = _scoped_windows(windows, foreground_context)
    current = metadata.active_window
    if (not current or not active or not roles or len(roles) > 4
            or any(role not in {'Edit', 'Document', 'Button', 'Hyperlink',
                                'TabItem', 'ListItem', 'TreeItem', 'CheckBox',
                                'RadioButton'} for role in roles)):
        return ComputerState(current, metadata.open_windows, available=bool(current),
                             elements_complete=False,
                             provider_failure='ROLE_QUERY_UNAVAILABLE',
                             foreground_owner=metadata.foreground_owner,
                             queried_roles=roles)
    try:
        from config.app_catalog import input_supported
        if not input_supported(windows, active):
            raise RuntimeError('unsupported')
        candidates = []
        for role in roles:
            found = active.descendants(control_type=role)
            if len(candidates) + len(found) > 200:
                return ComputerState(
                    current, metadata.open_windows, available=True,
                    elements_complete=False, visited_count=len(candidates) + len(found),
                    limit_reached='ROLE_CANDIDATE_LIMIT',
                    traversal_ms=round((time.perf_counter() - started) * 1000, 2),
                    foreground_owner=metadata.foreground_owner,
                    queried_roles=roles, query_scope='role-wide')
            candidates.extend(found)
        # Dialog detection is separate from role candidates and must also complete.
        dialogs = active.descendants(control_type='Window')
        if len(dialogs) > 50:
            return ComputerState(
                current, metadata.open_windows, available=True,
                elements_complete=False, limit_reached='DIALOG_CANDIDATE_LIMIT',
                foreground_owner=metadata.foreground_owner,
                queried_roles=roles, query_scope='role-wide')
        modal = False
        for dialog in dialogs:
            try:
                if dialog.handle != active.handle and dialog.is_visible():
                    modal = modal or bool(dialog.iface_window.CurrentIsModal)
            except Exception:
                continue
        elements = []
        for wrapper in candidates:
            info = wrapper.element_info
            if not info.visible or not provider_owned(windows, active, info):
                continue
            password = bool(info.element.CurrentIsPassword)
            rect = getattr(info, 'rectangle', None)
            bounds = ((rect.left, rect.top, rect.right, rect.bottom)
                      if rect is not None else ())
            selected = None
            if info.control_type in {'TabItem', 'ListItem', 'RadioButton',
                                     'TreeItem'}:
                try:
                    selected = bool(wrapper.iface_selection_item.CurrentIsSelected)
                except Exception:
                    selected = None
            elements.append(Element(
                tuple(info.runtime_id), info.control_type,
                '' if password else (info.name or '')[:200],
                bool(info.enabled), password,
                read_only=current.app in BROWSERS and
                          info.control_type == 'Document',
                bounds=bounds,
                focused=bool(getattr(info.element,
                                     'CurrentHasKeyboardFocus', False)),
                selected=selected,
                clickable=info.control_type in {
                    'Button', 'Hyperlink', 'TabItem', 'ListItem',
                    'TreeItem', 'CheckBox', 'RadioButton'},
                automation_id=getattr(info, 'automation_id', '') or '',
                modal=modal,
                scope=_wrapper_scope(wrapper, active, current.app)))
        after = windows.active()
        if after.handle != current.handle or windows.app_of(after) != current.app:
            return ComputerState(
                current, metadata.open_windows, available=True,
                elements_complete=False,
                provider_failure='ROLE_QUERY_FOREGROUND_CHANGED',
                foreground_owner=metadata.foreground_owner,
                queried_roles=roles, query_scope='role-wide')
        return ComputerState(
            current, metadata.open_windows, tuple(elements), available=True,
            focused_id=next((e.runtime_id for e in elements if e.focused), ()),
            elements_complete=True, visited_count=len(candidates),
            max_visited_depth=1,
            traversal_ms=round((time.perf_counter() - started) * 1000, 2),
            foreground_owner=metadata.foreground_owner,
            queried_roles=roles, query_scope='role-wide')
    except Exception:
        return ComputerState(
            current, metadata.open_windows, available=True,
            elements_complete=False,
            traversal_ms=round((time.perf_counter() - started) * 1000, 2),
            provider_failure='ROLE_QUERY_PROVIDER_ERROR',
            foreground_owner=metadata.foreground_owner,
            queried_roles=roles, query_scope='role-wide')

def _trusted_explorer_auxiliary(windows, metadata, target):
    """Return the exact authorized Explorer root for read-only verification only."""
    owner = metadata.foreground_owner
    authorized_handle = target.get('handle')
    if not (target.get('app') == 'explorer'
            and target.get('authorized_window_class') == 'CabinetWClass'
            and type(target.get('authorized_pid')) is int
            and target.get('authorized_executable_digest')
            and owner
            and owner.top_level_handle != authorized_handle
            and owner.pid == target['authorized_pid']
            and owner.executable_name == 'explorer.exe'
            and owner.executable_digest == target['authorized_executable_digest']
            and owner.application == 'explorer'
            and owner.window_class == 'Net UI Tool Window'
            and owner.visible is True
            and (owner.owner_handle == authorized_handle
                 or owner.root_owner_handle == authorized_handle)
            and any(w.handle == authorized_handle and w.app == 'explorer'
                    for w in metadata.open_windows)):
        return None
    try:
        import hashlib
        from pathlib import Path
        root = windows.desktop().window(handle=authorized_handle).wrapper_object()
        pid = root.process_id()
        executable = Path(windows.executable(pid)).resolve()
        digest = hashlib.sha256(str(executable).casefold().encode()).hexdigest()
        if (root.handle != authorized_handle
                or root.class_name() != 'CabinetWClass'
                or pid != target['authorized_pid']
                or digest != target['authorized_executable_digest']
                or windows.app_of(root) != 'explorer'
                or not root.is_visible()):
            return None
        return root
    except Exception:
        return None

def _focused_element_evidence(windows, active, app, target, matched_element):
    """Return privacy-safe authoritative UIA focus evidence for one exact target."""
    result = {
        'status': 'FOCUSED_ELEMENT_QUERY_FAILED', 'runtime_id': (),
        'control_type': None, 'automation_id': None,
        'provider_owned': None, 'scope': None, 'matches': False,
    }
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        from pywinauto.controls.uiawrapper import UIAWrapper
        info = UIAElementInfo(IUIA().get_focused_element())
        wrapper = UIAWrapper(info)
        runtime_id = tuple(info.runtime_id or ())
        control_type = info.control_type
        automation_id = info.automation_id or ''
        owned = bool(provider_owned(windows, active, info))
        scope = _wrapper_scope(wrapper, active, app) if owned else None
        rect = getattr(info, 'rectangle', None)
        bounds = ((rect.left, rect.top, rect.right, rect.bottom)
                  if rect is not None else ())
        exact_runtime = bool(runtime_id and runtime_id == matched_element.runtime_id)
        # Some providers omit/recreate runtime IDs. Accept only the exact stable
        # semantic identity of the one role-wide, provider-owned target.
        semantic_match = (
            control_type == matched_element.type
            and (info.name or '') == matched_element.name
            and automation_id == matched_element.automation_id
            and bounds == matched_element.bounds
            and (bool(automation_id) or not runtime_id
                 or not matched_element.runtime_id))
        matches = bool(owned and control_type == matched_element.type
                       and scope == target.get('scope', scope)
                       and (exact_runtime or semantic_match))
        result.update({
            'status': ('FOCUS_OBSERVED' if matches else
                       'FOCUSED_ELEMENT_IDENTITY_MISMATCH'),
            'runtime_id': runtime_id, 'control_type': control_type,
            'automation_id': automation_id, 'provider_owned': owned,
            'scope': scope, 'matches': matches,
        })
    except Exception:
        pass
    return result


def collect_target(windows, target, foreground_context=None):
    started = time.perf_counter()
    verification_started_ns = time.monotonic_ns()
    verification_pid = os.getpid()
    metadata, active = _scoped_windows(windows, foreground_context)
    current = metadata.active_window
    authorized_handle = target.get('handle')
    authorized_app = target.get('app')
    original_exists = any(w.handle == authorized_handle and w.app == authorized_app
                          for w in metadata.open_windows)
    auxiliary_handle = None
    auxiliary_verified = False
    if (not current or not active) and original_exists:
        authorized_root = _trusted_explorer_auxiliary(windows, metadata, target)
        if authorized_root is not None:
            active = authorized_root
            current = next(w for w in metadata.open_windows
                           if w.handle == authorized_handle and w.app == authorized_app)
            auxiliary_handle = metadata.foreground_owner.top_level_handle
            auxiliary_verified = True

    def finish(state):
        from dataclasses import replace
        return replace(state, foreground_owner=metadata.foreground_owner,
                       verification_started_monotonic_ns=verification_started_ns,
                       verification_process_id=verification_pid)

    from tools.window_tools import trace_foreground
    owner = metadata.foreground_owner
    trace_foreground(
        'verification.start',
        authorized_handle=authorized_handle,
        authorized_app=authorized_app,
        action_before_handle=target.get('foreground_before_handle'),
        action_before_app=target.get('foreground_before_app'),
        action_after_handle=target.get('foreground_after_handle'),
        action_after_app=target.get('foreground_after_app'),
        action_completed_monotonic_ns=target.get('action_completed_monotonic_ns'),
        action_process_id=target.get('action_process_id'),
        verification_started_monotonic_ns=verification_started_ns,
        verification_process_id=verification_pid,
        verification_handle=owner.top_level_handle if owner else None,
        verification_pid_owner=owner.pid if owner else None,
        verification_executable=owner.executable_name if owner else None,
        verification_class=owner.window_class if owner else None,
        verification_visible=owner.visible if owner else None,
        verification_category=owner.category if owner else None,
        verification_app=owner.application if owner else None,
        original_exists=original_exists,
        original_foreground=bool(
            metadata.active_window
            and metadata.active_window.handle == authorized_handle
            and metadata.active_window.app == authorized_app),
        auxiliary_verified=auxiliary_verified,
        auxiliary_handle=auxiliary_handle,
        auxiliary_owner_handle=(owner.owner_handle if owner else None),
        auxiliary_root_owner_handle=(owner.root_owner_handle if owner else None))
    if not current or not active:
        return finish(_diagnostic_state('POST_ACTION_FOREGROUND_CHANGED',
                                        current=current,
                                        open_windows=metadata.open_windows))
    if current.handle != authorized_handle or current.app != authorized_app:
        return finish(_diagnostic_state('POST_ACTION_FOREGROUND_CHANGED',
                                        current=current,
                                        open_windows=metadata.open_windows))
    try:
        role = target['type']
        candidates = active.descendants(control_type=role)
    except Exception:
        return finish(ComputerState(
            current, metadata.open_windows, available=True, elements_complete=False,
            traversal_ms=round((time.perf_counter() - started) * 1000, 2),
            provider_failure='POST_ACTION_TARGET_QUERY_FAILED'))
    if len(candidates) > 200:
        return finish(ComputerState(
            current, metadata.open_windows, available=True, elements_complete=False,
            visited_count=len(candidates), limit_reached='TARGET_CANDIDATE_LIMIT',
            traversal_ms=round((time.perf_counter() - started) * 1000, 2),
            provider_failure='POST_ACTION_TARGET_AMBIGUOUS'))
    try:
        matches = []
        expected_runtime = tuple(target.get('runtime_id') or ())
        expected_bounds = tuple(target.get('bounds') or ())
        state_unavailable = False
        for wrapper in candidates:
            info = wrapper.element_info
            if not info.visible or not info.enabled or not provider_owned(windows, active, info):
                continue
            rect = info.rectangle
            bounds = (rect.left, rect.top, rect.right, rect.bottom)
            if info.control_type != role or (info.name or '') != target.get('name'):
                continue
            automation_id = info.automation_id or ''
            runtime_id = tuple(info.runtime_id)
            scope = _wrapper_scope(wrapper, active, current.app)
            if target.get('scope') and scope != target['scope']:
                continue
            from vision.semantic import element_identity
            candidate_identity = element_identity(Element(
                runtime_id, role, info.name or '', bool(info.enabled),
                bool(getattr(info.element, 'CurrentIsPassword', False)), bounds=bounds,
                automation_id=automation_id, scope=scope))
            identity = ((bool(expected_runtime) and runtime_id == expected_runtime)
                        or candidate_identity == target.get('target_identity'))
            if not identity:
                continue
            selected = None
            checked = None
            expanded = None
            content_digest = None
            try:
                focused = bool(info.element.CurrentHasKeyboardFocus)
            except Exception:
                focused = None
            try:
                keyboard_focusable = bool(info.element.CurrentIsKeyboardFocusable)
            except Exception:
                keyboard_focusable = None
            if role in {'TabItem', 'ListItem', 'RadioButton', 'TreeItem'}:
                try:
                    selected = bool(wrapper.iface_selection_item.CurrentIsSelected)
                except Exception:
                    state_unavailable = True
            if role == 'CheckBox':
                try:
                    checked = int(wrapper.iface_toggle.CurrentToggleState) == 1
                except Exception:
                    state_unavailable = True
            if role == 'TreeItem':
                try:
                    expanded = {0: 'collapsed', 1: 'expanded'}.get(
                        int(wrapper.iface_expand_collapse.CurrentExpandCollapseState))
                    state_unavailable = state_unavailable or expanded is None
                except Exception:
                    state_unavailable = True
            if role == 'Edit' and target.get('operation') in {'set_url', 'navigate_url'}:
                try:
                    content_digest = url_digest(wrapper.iface_value.CurrentValue)
                except Exception:
                    state_unavailable = True
            if role in {'Edit', 'Document'} and target.get('operation') == 'edit_notepad':
                try:
                    value = wrapper.iface_value.CurrentValue
                    if not isinstance(value, str) or len(value) > 100000: raise ValueError()
                    content_digest = hashlib.sha256(value.encode()).hexdigest()
                except Exception:
                    state_unavailable = True
            matches.append(Element(runtime_id, role, info.name or '', True, False,
                                   bounds=bounds, selected=selected, checked=checked,
                                   expanded=expanded, focused=focused,
                                   automation_id=automation_id, scope=scope,
                                   keyboard_focusable=keyboard_focusable,
                                   content_digest=content_digest))
        focus_result = None
        if target.get('operation') == 'focus' and len(matches) == 1:
            from dataclasses import replace
            focus_result = _focused_element_evidence(
                windows, active, current.app, target, matches[0])
            if matches[0].focused is True:
                focus_result['status'] = 'FOCUS_OBSERVED'
                focus_result['matches'] = True
            elif (matches[0].focused is False
                  and focus_result['status'] == 'FOCUSED_ELEMENT_QUERY_FAILED'):
                focus_result['status'] = 'FOCUS_NOT_OBSERVED'
            elif focus_result['matches']:
                matches[0] = replace(matches[0], focused=True)
            elif matches[0].focused is None and focus_result['status'] == 'FOCUSED_ELEMENT_QUERY_FAILED':
                matches[0] = replace(matches[0], focused=None)
            else:
                matches[0] = replace(matches[0], focused=False)
        if auxiliary_verified:
            after_metadata, after_active = _scoped_windows(windows, foreground_context)
            returned_to_original = bool(
                after_metadata.active_window
                and after_metadata.active_window.handle == authorized_handle
                and after_metadata.active_window.app == authorized_app
                and after_active)
            same_auxiliary = bool(
                not after_metadata.active_window and
                after_metadata.foreground_owner and
                after_metadata.foreground_owner.top_level_handle == auxiliary_handle and
                _trusted_explorer_auxiliary(windows, after_metadata, target))
            if not (returned_to_original or same_auxiliary):
                return finish(_diagnostic_state(
                    'POST_ACTION_FOREGROUND_CHANGED', current=current,
                    open_windows=metadata.open_windows))
            after_handle = (authorized_handle if returned_to_original
                            else auxiliary_handle)
            after_app = ('explorer' if returned_to_original else
                         after_metadata.foreground_owner.application)
        else:
            after = windows.active()
            after_app = windows.app_of(after)
            after_handle = after.handle
            if after.handle != current.handle or after_app != current.app:
                return finish(_diagnostic_state(
                    'POST_ACTION_FOREGROUND_CHANGED', current=current,
                    open_windows=metadata.open_windows))
        trace_foreground('verification.finish',
                         authorized_handle=authorized_handle,
                         authorized_app=authorized_app,
                         verification_handle=after_handle,
                         verification_app=after_app,
                         original_exists=original_exists,
                         original_foreground=after_handle == authorized_handle,
                         auxiliary_verified=auxiliary_verified,
                         target_present_unique=len(matches) == 1)
        failure = ('POST_ACTION_TARGET_MISSING' if not matches else
                   'POST_ACTION_TARGET_AMBIGUOUS' if len(matches) > 1 else
                   'POST_ACTION_STATE_UNAVAILABLE' if state_unavailable else None)
        return finish(ComputerState(
            current, metadata.open_windows, tuple(matches), available=True,
            focused_id=(matches[0].runtime_id if len(matches) == 1
                        and matches[0].focused is True else ()),
            elements_complete=False, target_verification_complete=True,
            visited_count=len(candidates), max_visited_depth=1,
            traversal_ms=round((time.perf_counter() - started) * 1000, 2),
            provider_failure=failure,
            focus_evidence=(focus_result['status'] if focus_result else None),
            focused_element_runtime_id=(focus_result['runtime_id'] if focus_result else ()),
            focused_element_type=(focus_result['control_type'] if focus_result else None),
            focused_element_automation_id=(focus_result['automation_id'] if focus_result else None),
            focused_element_provider_owned=(focus_result['provider_owned'] if focus_result else None),
            focused_element_scope=(focus_result['scope'] if focus_result else None)))
    except Exception:
        return finish(ComputerState(
            current, metadata.open_windows, available=True, elements_complete=False,
            traversal_ms=round((time.perf_counter() - started) * 1000, 2),
            provider_failure='POST_ACTION_TARGET_QUERY_FAILED'))

def _worker(connection, allowlist, expected_text, level=1, target=None, foreground_context=None, roles=None):
    from utils.worker_lifecycle import send_reply
    try:
        from utils.worker_lifecycle import configure_worker
        configure_worker()
    except Exception:
        send_reply(connection, _diagnostic_state('VERIFICATION_WORKER_START_FAILED'))
        connection.close()
        return
    try:
        if level == 0:
            from tools.window_metadata import collect_metadata
            send_reply(connection, collect_metadata(allowlist, foreground_context))
            return
        try:
            from utils.com import configure_com
            configure_com()
            import pythoncom
            from utils.performance import measure
            with measure('com_initialization'):
                pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except Exception:
            send_reply(connection, _diagnostic_state('VERIFICATION_COM_INITIALIZATION_FAILED'))
            return
        from tools.app_tools import Apps
        from tools.window_tools import Windows
        windows = Windows(Apps(allowlist))
        result = (collect_target(windows, target, foreground_context) if target else
                  collect_roles(windows, roles, foreground_context) if roles else
                  collect(windows, expected_text, level, foreground_context))
        send_reply(connection, result)
    except Exception:
        category = ('POST_ACTION_TARGET_QUERY_FAILED' if target else
                    'ROLE_QUERY_PROVIDER_ERROR' if roles else
                    'VERIFICATION_WORKER_CRASH')
        send_reply(connection, _diagnostic_state(category))
    finally:
        connection.close()


class Observer:
    def __init__(self, allowlist, stop, timeout=7):
        self.allowlist, self.stop, self.timeout = allowlist, stop, timeout
        self.last_status = "UNCERTAIN"

    @timed("observation")
    def observe(self, expected_text=None, level=1, target=None, roles=None):
        self.stop.check()
        if roles is not None:
            roles = tuple(roles)
        self.last_status = "FAILED"
        ctx = multiprocessing.get_context("spawn")
        reader, writer = ctx.Pipe(duplex=False)
        foreground_context = {
            'intentra_pid': os.getpid(),
            'parent_terminal_pid': os.getppid(),
        }
        process = ctx.Process(
            target=_worker,
            args=(writer, self.allowlist, expected_text, level, target,
                  foreground_context, roles),
            daemon=True)
        try:
            from utils.performance import measure
            try:
                with measure('worker_spawn'):
                    process.start()
            except Exception:
                self.last_status = 'START_FAILED'
                return _diagnostic_state('VERIFICATION_WORKER_START_FAILED')
            writer.close()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                self.stop.check()
                if reader.poll(0.025):
                    try:
                        result = reader.recv()
                    except (EOFError, OSError):
                        self.last_status = 'CRASH'
                        return _diagnostic_state('VERIFICATION_WORKER_CRASH')
                    self.stop.check()
                    if type(result) is not ComputerState:
                        self.last_status = 'INVALID'
                        return _diagnostic_state('POST_ACTION_RESULT_INVALID')
                    self.last_status = ('SUCCESS' if result.available and
                                        (level == 0 or
                                         ((result.elements_complete or
                                           result.target_verification_complete)
                                          and result.active_window))
                                        else 'UNCERTAIN')
                    return result
                if not process.is_alive():
                    self.last_status = 'CRASH'
                    return _diagnostic_state('VERIFICATION_WORKER_CRASH')
            self.last_status = "TIMEOUT"
            return _diagnostic_state('VERIFICATION_WORKER_TIMEOUT')
        except Cancelled:
            raise
        except Exception:
            self.last_status = 'CRASH'
            return _diagnostic_state('VERIFICATION_WORKER_CRASH')
        finally:
            if process.pid:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
                if not process.is_alive():
                    process.close()
            reader.close()
            writer.close()
