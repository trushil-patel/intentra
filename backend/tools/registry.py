from dataclasses import dataclass
from types import MappingProxyType
from safety.policy import Risk
from utils.helpers import SafeError

@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    risk: Risk
    arguments: dict

_specs = {}
def _add(name, risk=Risk.LOW, **arguments):
    _specs[name] = ToolDefinition(name, name.replace("_", " "), risk, MappingProxyType(arguments))

for name in ("open_app", "focus_app", "minimize_app", "maximize_app"):
    _add(name, app_name=str)
_add("close_app", Risk.MEDIUM, app_name=str)
for name in ("focus_window", "minimize_window", "maximize_window"):
    _add(name, window_name=str)
_add("close_window", Risk.MEDIUM, window_name=str)
_add("type_text", Risk.MEDIUM, text=str)
_add('type_url', Risk.MEDIUM, element_id=str, url=str)
_add('navigate_url', Risk.MEDIUM, element_id=str, url=str)
_add('wait_accessibility_ready', url=str, timeout=int)
_add('edit_notepad', Risk.MEDIUM, element_id=str, text=str, mode=str)
_add("press_key", Risk.MEDIUM, key=str)
_add("hotkey", Risk.MEDIUM, keys=list)
for name in ("click", "double_click", "right_click"):
    _add(name, Risk.MEDIUM)
_add("scroll", direction=str, amount=int)
_add("open_url", url=str)
for name in ("get_active_window", "list_windows", "take_screenshot", "get_current_time"):
    _add(name)
_add("focus_text_field", Risk.MEDIUM)
for name in ("new_tab", "close_tab", "refresh_page", "browser_back", "browser_forward", "focus_address_bar"):
    _add(name, Risk.MEDIUM)
_add("search_web", Risk.MEDIUM, query=str)
for name in ("get_visible_elements", "read_visible_text"):
    _add(name)
_add('summarize_page')
_add('find_element', query=str)
_add('list_elements', role=str)
for name in ('click_element', 'focus_element', 'read_element_text'):
    _add(name, Risk.MEDIUM, element_id=str)
_add('interact_element', Risk.MEDIUM, element_id=str, operation=str)
_add('wait_for', condition=str, value=str, timeout=int)
_add('wait_for_response', timeout=int)
_add('read_response', Risk.MEDIUM)
_add('write_task_text', Risk.MEDIUM, slot=str)
_add(
    "navigate_explorer_folder",
    Risk.MEDIUM,
    folder=str,
)
SPECS = MappingProxyType(_specs)

class Registry:
    def __init__(self, handlers):
        if set(handlers) != set(SPECS):
            raise SafeError("Incomplete tool registry")
        self._handlers = MappingProxyType(dict(handlers))

    def spec(self, name):
        if type(name) is not str or name not in SPECS:
            raise SafeError("Unknown tool blocked")
        return SPECS[name]

    def invoke(self, action):
        # The router is the only production caller; no model dispatch/import path.
        return self._handlers[action.tool](**action.arguments)
