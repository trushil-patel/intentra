from utils.performance import timed
"""Disposable native-operation worker. A timed-out worker cannot keep sending input."""
import multiprocessing
import time
from types import SimpleNamespace
from utils.helpers import SafeError, Cancelled


class SharedStop:
    def __init__(self, event): self.event = event
    def check(self):
        if self.event.is_set(): raise Cancelled('Native operation cancelled')
    def stop(self): self.event.set()
    def wait(self, seconds):
        self.check()
        self.event.wait(seconds)
        self.check()
    @property
    def stopped(self): return self.event.is_set()


def _native_worker(connection, event, allowlist, settings, operation, action, target, reference=None, content=None):
    from utils.worker_lifecycle import configure_worker, send_reply
    configure_worker()
    from utils.com import configure_com
    configure_com()
    store = None
    try:
        if operation == 'invoke' and action.tool == 'open_app':
            from tools.app_tools import Apps
            SharedStop(event).check()
            value = Apps(allowlist).open_app(**action.arguments)
            SharedStop(event).check()
            send_reply(connection, ('ok', value))
            return
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        from tools.factory import build_tools
        stop = SharedStop(event)
        registry, windows, store, guard = build_tools(allowlist, stop, settings)
        guard.element_reference = reference
        guard.task_content = content
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.005
        stop.check()
        if operation == 'snapshot':
            value = guard.snapshot(action)
        elif operation == 'recheck':
            guard.recheck(action, target)
            value = target
        elif operation == 'select':
            value = windows.select(**action.arguments).handle
        else:
            if target is not None:
                if action.tool in {"close_app", "close_window"}:
                    windows.expected_close = target
                else:
                    guard.arm(action, target)
                    guard.recheck(action, target)
            stop.check()
            value = registry.invoke(action)
        stop.check()
        send_reply(connection, ('ok', value))
    except Cancelled:
        send_reply(connection, ('cancelled', None))
    except SafeError as exc:
        send_reply(connection, ('blocked', str(exc)))
    except Exception:
        send_reply(connection, ('error', None))
    finally:
        if store: store.clear()
        connection.close()


class NativeBridge:
    def __init__(self, allowlist, stop, settings=None, timeout=15):
        self.allowlist, self.stop, self.settings, self.timeout = allowlist, stop, settings, timeout
        self.pending = None
        self.pending_close = None
        self.element_reference = None
        self.session_reference = None
        self.task_content = None
        self.response_baseline = None

    @timed("native_operation")
    def call(self, operation, action, target=None):
        self.stop.check()
        ctx = multiprocessing.get_context('spawn')
        reader, writer = ctx.Pipe(duplex=False)
        event = ctx.Event()
        process = ctx.Process(target=_native_worker,
                              args=(writer, event, self.allowlist, self.settings, operation, action, target,
                                    self.session_reference if action.tool == 'find_element' else
                                    self.element_reference if action.tool in {'click_element', 'interact_element', 'focus_element', 'type_url', 'navigate_url', 'edit_notepad', 'read_element_text', 'read_response'} else None,
                                    self.task_content if operation == 'invoke' and action.tool == 'write_task_text' else None))
        try:
            process.start()
            writer.close()
            budget = min(120, max(self.timeout, len(action.arguments.get("text", "")) * 0.15)) if operation == "invoke" and action.tool == "type_text" else self.timeout
            if action.tool == 'write_task_text': budget = 120
            deadline = time.monotonic() + budget
            while time.monotonic() < deadline:
                self.stop.check()
                if reader.poll(0.025):
                    kind, value = reader.recv()
                    self.stop.check()
                    if kind == 'ok': return value
                    if kind == 'blocked': raise SafeError(value)
                    if kind == 'cancelled': raise Cancelled('Native operation cancelled')
                    self.stop.stop()
                    raise SafeError('Native operation failed; emergency stop latched')
                if not process.is_alive(): break
            self.stop.stop()
            raise SafeError('Native operation timed out; emergency stop latched')
        finally:
            event.set()
            if process.pid:
                process.join(timeout=0.2)
                if process.is_alive(): process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
                if not process.is_alive(): process.close()
            reader.close()
            writer.close()

    def snapshot(self, action): return self.call('snapshot', action)
    def recheck(self, action, target):
        if self.call('recheck', action, target) != target:
            raise SafeError('Target changed; issue the command again')
    def arm(self, action, target): self.pending = (action, target)
    def disarm(self):
        self.pending = None
        self.pending_close = None
    def bind_closing(self, action, handle): self.pending_close = (action, handle)
    def select(self, **arguments):
        from agent.schemas import Action
        return SimpleNamespace(handle=self.call('select', Action('get_active_window', arguments)))
    def invoke(self, action):
        if action.tool == 'wait_for_response':
            from agent.response_wait import ResponseWaiter
            from vision.observer import Observer
            self.element_reference = ResponseWaiter(Observer(self.allowlist, self.stop), self.stop).wait(
                self.response_baseline, action.arguments['timeout'])
            return {'status': 'response_ready'}
        if action.tool == 'wait_for':
            from agent.wait_manager import WaitManager, Condition, ConditionKind
            from vision.observer import Observer
            from vision.semantic import ScreenState
            observer = Observer(self.allowlist, self.stop)
            WaitManager(observer, self.stop).wait_for(
                Condition(ConditionKind(action.arguments['condition']), action.arguments['value']),
                ScreenState.from_computer(observer.observe()), action.arguments['timeout'])
            return {'status': 'condition_met'}
        target = self.pending[1] if self.pending and self.pending[0] == action else None
        if self.pending_close and self.pending_close[0] == action:
            target = self.pending_close[1]
        value = self.call('invoke', action, target)
        if action.tool == 'read_response':
            from memory.task_content import TaskContent
            if not isinstance(value, TaskContent): raise SafeError('Invalid response content')
            self.task_content = value
        if action.tool == 'find_element':
            from vision.semantic import ElementReference
            if not isinstance(value, ElementReference): raise SafeError('Invalid discovery result')
            self.element_reference = value
            self.session_reference = value
        elif action.tool not in {'get_current_time', 'read_element_text', 'get_visible_elements', 'list_windows', 'read_response'}:
            self.element_reference = None
        return value

    def clear_session(self):
        self.session_reference = None
        self.clear_task()

    def clear_task(self):
        self.task_content = None
        self.response_baseline = None
        self.element_reference = None
        self.disarm()
    def spec(self, name):
        from tools.registry import SPECS
        if type(name) is not str or name not in SPECS: raise SafeError('Unknown tool blocked')
        return SPECS[name]
