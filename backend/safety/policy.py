from config.app_catalog import BROWSERS
from enum import StrEnum

class Risk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

# UI operations can submit or overwrite content. Never silently auto-approve.
INTERACTIVE = {"type_text", "press_key", "hotkey", "click", "double_click", "right_click", "focus_text_field", "new_tab", "close_tab", "refresh_page", "browser_back", "browser_forward", "focus_address_bar"}
INTERACTIVE |= {'click_element', 'interact_element', 'focus_element', 'type_url', 'navigate_url',
                'edit_notepad', 'read_element_text', 'read_response', 'write_task_text'}


def validate_planned_targets(actions):
    """Reject known unsupported sequences before any effect or confirmation.

    This predicts only explicit app changes. Unknown foreground state still goes
    through the live input guard; a model's proposed target is never evidence.
    """
    from utils.helpers import SafeError
    target = None
    browser_only = {"new_tab", "close_tab", "refresh_page", "browser_back",
                    "browser_forward", "focus_address_bar", "type_url", "navigate_url",
                    "wait_accessibility_ready"}
    for action in actions:
        if action.tool in {"open_app", "focus_app"}:
            target = action.arguments["app_name"]
        elif action.tool in {"open_url", "search_web"}:
            target = "chrome"
        elif action.tool in {"focus_window", "close_window", "minimize_window", "maximize_window",
                             "close_app", "minimize_app", "maximize_app"}:
            target = None
        elif action.tool in INTERACTIVE | {"scroll"}:
            if target == "vscode":
                raise SafeError("Plan requests automated input in VS Code, which is unsupported. No actions executed. Specify an intended Notepad or Chrome text field.")
            if target is not None and target not in BROWSERS and action.tool in browser_only:
                raise SafeError("Plan requests a browser operation outside a supported browser. No actions executed.")


def ordinary_input(action, target):
    """Only trusted live evidence can avoid a redundant input confirmation.

    Insertion requires a live, safe editor with no selection; browser editors
    also need a writable ValuePattern. Browser Enter still requires confirmation.
    """
    from safety.ui_guard import Target
    if type(target) is not Target or target.app not in ({'notepad'} | BROWSERS) or not target.runtime_id:
        return False
    if action.tool == 'type_text': return target.insertion_safe
    if action.tool == 'press_key':
        return target.app == 'notepad' and target.insertion_safe and action.arguments.get('key') in {
            'enter', 'space', 'left', 'right', 'up', 'down', 'home', 'end', 'pageup', 'pagedown'}
    return False
