from tools.uia_ownership import provider_owned
from config.app_catalog import BROWSERS
"""UIA discovery for the approved foreground process; no coordinate guessing."""
from agent.computer_state import Element
from vision.element_finder import find_text_field
from utils.helpers import SafeError


def visible_wrappers(windows):
    window = windows.active()
    app = windows.app_of(window)
    from config.app_catalog import input_supported
    if app == 'vscode' or not input_supported(windows, window):
        raise SafeError('Element discovery is unavailable for this application')
    pending, found = [(window, 0)], []
    while pending and len(found) < 200:
        wrapper, depth = pending.pop(0)
        info = wrapper.element_info
        if provider_owned(windows, window, info) and info.visible:
            password = bool(info.element.CurrentIsPassword)
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
                    expanded = {0: 'collapsed', 1: 'expanded'}.get(
                        int(wrapper.iface_expand_collapse.CurrentExpandCollapseState))
                except Exception:
                    pass
            rect = getattr(info, 'rectangle', None)
            bounds = ((rect.left, rect.top, rect.right, rect.bottom) if rect is not None else ())
            modal = depth > 0 and info.control_type == 'Window'
            if info.control_type == 'Window':
                try:
                    modal = modal or bool(wrapper.iface_window.CurrentIsModal)
                except Exception:
                    pass
            found.append((Element(tuple(info.runtime_id), info.control_type,
                                  '' if password else (info.name or '')[:200], bool(info.enabled), password,
                                  read_only=app in BROWSERS and info.control_type == 'Document',
                                  bounds=bounds, selected=selected, checked=checked, expanded=expanded,
                                  automation_id=getattr(info, 'automation_id', '') or '', modal=modal), wrapper))
        children = wrapper.children()
        remaining = 200 - len(found) - len(pending)
        if children and (depth >= 12 or len(children) > remaining):
            raise SafeError('UI tree exceeds discovery limits; select the intended field manually')
        pending.extend((child, depth + 1) for child in children)
    if pending:
        raise SafeError('UI tree is incomplete; select the intended field manually')
    return found



def visible_role_wrappers(windows, role):
    """Complete, bounded role-wide revalidation under the exact foreground root."""
    window = windows.active()
    app = windows.app_of(window)
    from config.app_catalog import input_supported
    if not input_supported(windows, window):
        raise SafeError('Element discovery is unavailable for this application')
    try:
        candidates = window.descendants(control_type=role)
        if len(candidates) > 200:
            raise SafeError('UI role query exceeds discovery limits')
        dialogs = window.descendants(control_type='Window')
        if len(dialogs) > 50:
            raise SafeError('Dialog query exceeds discovery limits')
        for dialog in dialogs:
            try:
                if (dialog.handle != window.handle and dialog.is_visible()
                        and bool(dialog.iface_window.CurrentIsModal)):
                    raise SafeError('Unexpected modal dialog')
            except SafeError:
                raise
            except Exception:
                continue
        found = []
        from vision.observer import _wrapper_scope
        for wrapper in candidates:
            info = wrapper.element_info
            if not info.visible or not provider_owned(windows, window, info):
                continue
            password = bool(info.element.CurrentIsPassword)
            rect = getattr(info, 'rectangle', None)
            bounds = ((rect.left, rect.top, rect.right, rect.bottom)
                      if rect is not None else ())
            selected = None
            if role in {'TabItem', 'ListItem', 'RadioButton', 'TreeItem'}:
                try:
                    selected = bool(wrapper.iface_selection_item.CurrentIsSelected)
                except Exception:
                    selected = None
            found.append((Element(
                tuple(info.runtime_id), info.control_type,
                '' if password else (info.name or '')[:200],
                bool(info.enabled), password,
                read_only=app in BROWSERS and role == 'Document',
                bounds=bounds, selected=selected,
                automation_id=getattr(info, 'automation_id', '') or '',
                scope=_wrapper_scope(wrapper, window, app)), wrapper))
        after = windows.active()
        if after.handle != window.handle or windows.app_of(after) != app:
            raise SafeError('Foreground window changed during role query')
        return found
    except SafeError:
        raise
    except Exception:
        raise SafeError('UI role query failed') from None

def text_field(windows):
    found = visible_wrappers(windows)
    selected = find_text_field([e for e, _ in found])
    return next(wrapper for e, wrapper in found if e.runtime_id == selected.runtime_id)


class Accessibility:
    def __init__(self, windows, stop, guard):
        self.windows, self.stop, self.guard = windows, stop, guard

    def focus_text_field(self):
        self.stop.check()
        self.guard.verify_input()
        wrapper = text_field(self.windows)
        runtime_id = tuple(wrapper.element_info.runtime_id)
        if runtime_id != self.guard.pending[1].runtime_id:
            raise SafeError('Text field changed; no focus performed')
        self.stop.check()
        wrapper.set_focus()
        return {'runtime_id': runtime_id}
