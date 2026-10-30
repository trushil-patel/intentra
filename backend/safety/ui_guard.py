from config.app_catalog import BROWSERS
"""Fail-closed focus guard. This is defense in depth, not semantic page analysis."""
import ctypes
import hashlib
from dataclasses import dataclass
from safety.validator import safe_text
from utils.helpers import SafeError

@dataclass(frozen=True)
class Target:
    handle: int
    process: int
    runtime_id: tuple
    rectangle: tuple
    pointer: tuple | None
    app: str
    text_digest: str | None = None
    state_id: str | None = None
    insertion_safe: bool = False
    owner_process: int | None = None

class UIGuard:
    def __init__(self, windows):
        self.windows = windows

    def snapshot(self, action, *, allow_expired=False):
        user = ctypes.WinDLL("user32")
        if any(user.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise SafeError("Release modifier keys before automated input")
        window = self.windows.active()
        app = self.windows.app_of(window)
        from config.app_catalog import input_supported
        if app == "vscode" or not input_supported(self.windows, window):
            raise SafeError("Automated input is unavailable in this editor, console or administration app. Focus an ordinary text field in another app and repeat the command.")
        if action.tool == 'scroll':
            from tools.scroll_target import resolve_scroll
            return resolve_scroll(self.windows, window)[0]
        from tools.element_tools import ELEMENT_ACTIONS, FORBIDDEN_TARGET, resolve_reference
        if action.tool in ELEMENT_ACTIONS:
            reference, wrapper = resolve_reference(self.windows, getattr(self, 'element_reference', None),
                                                   allow_expired=allow_expired)
            element = reference.element
            if action.arguments.get('element_id', 'last') not in {'last', reference.id}:
                raise SafeError('Unknown element reference')
            if element.password:
                raise SafeError('SENSITIVE_INPUT_REQUIRED: Please enter sensitive information manually.')
            if not element.enabled or not safe_text(window.window_text()) or FORBIDDEN_TARGET.search(element.name):
                raise SafeError('Protected or consequential target is outside supported interaction')
            from config.settings import AgentLimits
            if element.source != 'uia' or element.confidence < AgentLimits().grounding_confidence:
                raise SafeError('Structured UIA evidence is required')
            return Target(window.handle, window.process_id(), element.runtime_id, element.bounds,
                          None, app, state_id=reference.state_id)
        if action.tool == 'write_task_text' and app != 'notepad':
            raise SafeError('Task data can only be written into Notepad')
        from tools.browser_tools import BROWSER_KEYS
        if action.tool in BROWSER_KEYS:
            if app not in BROWSERS or not safe_text(window.window_text()):
                raise SafeError("Browser action requires a safe supported browser window")
            return Target(window.handle, window.process_id(), (), (), None, app)
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        import pyautogui
        pointer = None
        if action.tool == "focus_text_field":
            from tools.accessibility import text_field
            element = text_field(self.windows).element_info
        elif action.tool in {"click", "double_click", "right_click"}:
            pointer = tuple(pyautogui.position())
            element = self.windows.desktop().from_point(*pointer).element_info
        else:
            element = UIAElementInfo(IUIA().iuia.GetFocusedElement())
        if element.element.CurrentIsPassword:
            raise SafeError("SENSITIVE_INPUT_REQUIRED: Please enter this sensitive information manually.")
        from tools.uia_ownership import focused_descendant
        owned = element.process_id == window.process_id()
        if not owned and app in BROWSERS:
            owned = focused_descendant(self.windows, window, element)
        if not owned or not element.enabled or not element.visible:
            raise SafeError("Input target is protected or belongs to another process")
        # Avoid unknown canvas controls, address bars, terminals and sensitive dialogs.
        name = element.name or ""
        if action.tool != "write_task_text" and (not safe_text(window.window_text() + " " + name) or any(word in window.window_text().lower() for word in ("devtools", "developer tools", "terminal", "console", "checkout", "bank", "payment"))):
            raise SafeError("Sensitive target blocked")
        if any(word in name.lower() for word in ("address", "omnibox", "terminal", "console", "buy", "pay", "delete", "install", "sign in", "log in")):
            raise SafeError("Target is outside permitted input controls")
        if element.control_type not in {"Edit", "Document"}:
            raise SafeError("Input requires an identifiable text editing control")
        digest = None
        if action.tool == "press_key" and action.arguments.get("key") == "enter":
            from pywinauto.controls.uiawrapper import UIAWrapper
            wrapper = UIAWrapper(element)
            try:
                value = wrapper.iface_value.CurrentValue
            except Exception:
                try:
                    value = wrapper.iface_text.DocumentRange.GetText(100001)
                except Exception:
                    raise SafeError("Cannot bind submission to visible field content; submit manually") from None
            if not isinstance(value, str) or len(value) > 100000 or not safe_text(value):
                raise SafeError("Sensitive or unreadable submission blocked; submit manually")
            digest = hashlib.sha256(value.encode()).hexdigest()
        insertion_safe = False
        if app in ({'notepad'} | BROWSERS) and action.tool in {'type_text', 'press_key'}:
            try:
                from pywinauto.controls.uiawrapper import UIAWrapper
                ranges = UIAWrapper(element).iface_text.GetSelection()
                insertion_safe = ranges.Length == 1 and ranges.GetElement(0).GetText(1) == ''
                if app in BROWSERS:
                    insertion_safe = insertion_safe and element.control_type == 'Edit' and not UIAWrapper(element).iface_value.CurrentIsReadOnly
            except Exception:
                pass
        return Target(window.handle, window.process_id(), tuple(element.runtime_id), (element.rectangle.left, element.rectangle.top, element.rectangle.right, element.rectangle.bottom), pointer, app, digest, insertion_safe=insertion_safe, owner_process=element.process_id)

    def recheck(self, action, expected):
        if self.snapshot(action, allow_expired=True) != expected:
            raise SafeError("Target changed; issue the command again")

    def arm(self, action, target):
        self.pending = (action, target)

    def disarm(self):
        self.pending = None

    def verify_input(self):
        if not getattr(self, "pending", None):
            raise SafeError("No authorized input operation")
        self.recheck(*self.pending)
