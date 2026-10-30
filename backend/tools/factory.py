from functools import partial
from safety.ui_guard import UIGuard
from tools.app_tools import Apps
from tools.window_tools import Windows
from tools.keyboard_tools import Keyboard
from tools.mouse_tools import Mouse
from tools.browser_tools import Browser
from tools.screen_tools import Screen
from tools.system_tools import get_current_time
from tools.registry import Registry
from vision.screenshot import ScreenshotStore

def build_tools(allowlist, stop, settings=None):
    apps = Apps(allowlist)
    windows = Windows(apps)
    guard = UIGuard(windows)
    keyboard = Keyboard(stop, guard, unicode_input=True)
    mouse = Mouse(stop, guard)
    from tools.accessibility import Accessibility
    from tools.browser_tools import BROWSER_KEYS
    from vision.observer import Observer
    from vision.ocr import ScreenReader
    store = ScreenshotStore(enabled=getattr(settings, "screenshots", True))
    browser = Browser(apps, stop, guard)
    observer = Observer(allowlist, stop)
    reader = ScreenReader(observer, store, stop, enabled=getattr(settings, "ocr", False))
    handlers = {"open_app": apps.open_app, "open_url": browser.open_url,
                "get_active_window": windows.get_active_window, "list_windows": windows.list_windows,
                "get_current_time": get_current_time, "take_screenshot": Screen(store).take_screenshot}
    for op in ("focus", "close", "minimize", "maximize"):
        handlers[op + "_app"] = partial(windows.apply, op)
        handlers[op + "_window"] = partial(windows.apply, op)
    for name in ("type_text", "press_key", "hotkey"):
        handlers[name] = getattr(keyboard, name)
    for name in ("click", "double_click", "right_click", "scroll"):
        handlers[name] = getattr(mouse, name)
    handlers["focus_text_field"] = Accessibility(windows, stop, guard).focus_text_field
    handlers["search_web"] = browser.search_web
    handlers["get_visible_elements"] = reader.get_visible_elements
    handlers["read_visible_text"] = reader.read_visible_text
    handlers["summarize_page"] = reader.summarize_page
    for name in BROWSER_KEYS:
        handlers[name] = partial(browser.shortcut, name)
    from tools.element_tools import ElementTools
    elements = ElementTools(windows, stop, guard)
    for name in ('find_element', 'list_elements', 'click_element', 'interact_element',
                 'focus_element', 'type_url', 'navigate_url', 'wait_accessibility_ready',
                 'edit_notepad', 'read_element_text'):
        handlers[name] = getattr(elements, name)
    # Production NativeBridge runs waits in the parent, keeping cancellation responsive.
    from agent.wait_manager import WaitManager, Condition, ConditionKind
    from vision.semantic import ScreenState
    def wait_for(condition, value, timeout):
        WaitManager(observer, stop).wait_for(Condition(ConditionKind(condition), value),
                                            ScreenState.from_computer(observer.observe()), timeout)
        return {'status': 'condition_met'}
    handlers['wait_for'] = wait_for
    handlers['read_response'] = elements.read_response
    handlers['write_task_text'] = keyboard.write_task_text
    def wait_for_response(timeout):
        from utils.helpers import SafeError
        raise SafeError('Response waits require task context')
    handlers['wait_for_response'] = wait_for_response
    from tools.explorer_navigation import ExplorerNavigation

    explorer_navigation = ExplorerNavigation(allowlist, stop)

    handlers["navigate_explorer_folder"] = explorer_navigation.navigate
    return Registry(handlers), windows, store, guard
