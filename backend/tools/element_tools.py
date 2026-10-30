"""UIA-only interaction with scoped references; no coordinate input capability."""
import re
from config.settings import AgentLimits
from vision.observer import collect, collect_roles, roles_for_query
from vision.semantic import ScreenState, ElementReference
from vision.grounding import ground
from tools.accessibility import visible_wrappers, visible_role_wrappers
from utils.helpers import SafeError
from utils.performance import timed


ELEMENT_ACTIONS = {'click_element', 'interact_element', 'focus_element', 'type_url',
                   'navigate_url', 'wait_accessibility_ready', 'edit_notepad',
                   'read_element_text', 'read_response'}
FORBIDDEN_TARGET = re.compile(r'(?i)\b(delete|remove|erase|uninstall|reset|format|purchase|pay|buy|transfer|download|upload|install|password|credential|security|privacy|antivirus|firewall|sign in|log in)\b')


def resolve_reference(windows, reference, *, allow_expired=False):
    if not isinstance(reference, ElementReference):
        raise SafeError('Unknown element reference; identify the target first')
    import time
    from dataclasses import replace
    age = time.monotonic() - reference.observed_at
    if not allow_expired and not 0 <= age <= AgentLimits().reference_seconds:
        raise SafeError('STALE_ELEMENT: reference age exceeded')
    before = windows.active()
    if before.handle != reference.handle or windows.app_of(before) != reference.app:
        raise SafeError('STALE_ELEMENT: foreground window or application changed')
    observed = (visible_role_wrappers(windows, reference.element.type)
                if reference.app in {'chrome', 'edge', 'firefox'}
                else visible_wrappers(windows))
    after = windows.active()
    if after.handle != reference.handle or windows.app_of(after) != reference.app:
        raise SafeError('STALE_ELEMENT: foreground window or application changed')
    if any(element.modal for element, _ in observed):
        raise SafeError('STALE_ELEMENT: unexpected modal dialog')
    matches = ([(element, wrapper) for element, wrapper in observed
                if element.runtime_id == reference.element.runtime_id
                and element.type == reference.element.type
                and element.name == reference.element.name]
               if reference.element.runtime_id else [])
    if not matches:
        expected = reference.element
        matches = [(element, wrapper) for element, wrapper in observed
                   if (element.type, element.name, element.bounds, element.automation_id,
                       element.enabled, element.password)
                   == (expected.type, expected.name, expected.bounds, expected.automation_id,
                       expected.enabled, expected.password)]
    if len(matches) != 1:
        raise SafeError('STALE_ELEMENT: target missing, changed or ambiguous')
    element, wrapper = matches[0]
    if not element.enabled or element.password:
        raise SafeError('STALE_ELEMENT: target is disabled or protected')
    return replace(reference, element=element), wrapper



class ElementTools:
    def __init__(self, windows, stop, guard):
        self.windows, self.stop, self.guard = windows, stop, guard

    def find_element(self, query):
        self.stop.check()
        roles = roles_for_query(query)
        observed = (collect_roles(self.windows, roles) if roles
                    else collect(self.windows))
        result = ground(ScreenState.from_computer(observed), query,
                        previous=getattr(self.guard, 'element_reference', None))
        if result.selected is None:
            raise SafeError('INPUT_REQUIRED: ' + result.reason)
        return result.selected

    def list_elements(self, role):
        self.stop.check()
        mapping = {'button': 'Button', 'tab': 'TabItem', 'field': 'Edit'}
        if role not in mapping:
            raise SafeError('Unsupported element-list role')
        state = collect_roles(self.windows, (mapping[role],))
        if not state.available or not state.elements_complete:
            raise SafeError('UI role query is incomplete')
        from utils.logger import redact
        return tuple({
            'label': redact(element.name),
            'control_type': element.type,
            'enabled': element.enabled,
            'operation': ('invoke' if element.type == 'Button' else
                          'select' if element.type == 'TabItem' else 'focus'),
            'scope': element.scope,
        } for element in state.elements[:50]
          if (not element.password and element.name
              and (state.current_application not in {'chrome', 'edge', 'firefox'}
                   or element.scope == 'browser_chrome')))

    def _target(self, element_id):
        self.stop.check()
        self.guard.verify_input()
        reference = getattr(self.guard, 'element_reference', None)
        if not reference or element_id not in {'last', reference.id}:
            raise SafeError('Unknown element reference')
        return resolve_reference(self.windows, reference, allow_expired=True)

    @timed('uia_interaction')
    def _interact(self, element_id, operation):
        reference, wrapper = self._target(element_id)
        element = reference.element
        allowed = {
            'activate': {'Button', 'Hyperlink', 'TabItem', 'ListItem', 'MenuItem', 'RadioButton'},
            'select': {'TabItem', 'ListItem', 'RadioButton', 'TreeItem'},
            'expand': {'TreeItem'},
            'collapse': {'TreeItem'},
            'check': {'CheckBox'},
            'uncheck': {'CheckBox'},
        }
        if operation not in allowed or element.type not in allowed[operation]:
            raise SafeError('Requested UIA operation is unsupported for this control')
        self.stop.check()
        before_action = self.windows.active()
        before_app = self.windows.app_of(before_action)
        if before_action.handle != reference.handle or before_app != reference.app:
            raise SafeError('STALE_ELEMENT: foreground window or application changed')
        authorized_pid = before_action.process_id()
        try:
            from pathlib import Path
            import hashlib
            authorized_executable = Path(
                self.windows.executable(authorized_pid)).resolve()
            authorized_executable_digest = hashlib.sha256(
                str(authorized_executable).casefold().encode()).hexdigest()
        except Exception:
            raise SafeError('Cannot verify authorized executable identity') from None
        try:
            if operation == 'activate':
                if element.type in {'TabItem', 'ListItem', 'RadioButton'}:
                    wrapper.iface_selection_item.Select()
                else:
                    wrapper.iface_invoke.Invoke()
            elif operation == 'select':
                wrapper.iface_selection_item.Select()
            elif operation == 'expand':
                wrapper.iface_expand_collapse.Expand()
            elif operation == 'collapse':
                wrapper.iface_expand_collapse.Collapse()
            else:
                desired = operation == 'check'
                current = int(wrapper.iface_toggle.CurrentToggleState)
                if current not in {0, 1}:
                    raise SafeError('Checkbox state is indeterminate')
                if (current == 1) != desired:
                    wrapper.iface_toggle.Toggle()
        except SafeError:
            raise
        except Exception:
            raise SafeError('Required UIA interaction pattern is unavailable') from None
        import os
        import time
        action_completed_monotonic_ns = time.monotonic_ns()
        action_process_id = os.getpid()
        try:
            after_action = self.windows.active()
            after_handle = after_action.handle
            after_app = self.windows.app_of(after_action)
        except Exception:
            after_handle, after_app = None, None
        from tools.window_tools import trace_foreground
        trace_foreground('element.action',
                         authorized_handle=reference.handle,
                         before_handle=before_action.handle, before_app=before_app,
                         after_handle=after_handle, after_app=after_app)
        from vision.semantic import element_identity
        return {'status': 'requested', 'runtime_id': element.runtime_id,
                'target_identity': element_identity(element),
                'handle': reference.handle, 'app': reference.app,
                'foreground_before_handle': before_action.handle,
                'foreground_before_app': before_app,
                'foreground_after_handle': after_handle,
                'foreground_after_app': after_app,
                'action_completed_monotonic_ns': action_completed_monotonic_ns,
                'action_process_id': action_process_id,
                'authorized_pid': authorized_pid,
                'authorized_executable_digest': authorized_executable_digest,
                'authorized_window_class': before_action.class_name(),
                'type': element.type, 'name': element.name, 'bounds': element.bounds,
                'automation_id': element.automation_id, 'scope': element.scope,
                'operation': operation, 'control_type': element.type,
                'requested_state': True if operation == 'check' else False if operation == 'uncheck' else None}

    def click_element(self, element_id):
        return self._interact(element_id, 'activate')

    def interact_element(self, element_id, operation):
        return self._interact(element_id, operation)

    def focus_element(self, element_id):
        reference, wrapper = self._target(element_id)
        if (reference.element.type not in {'Edit', 'Document'}
                or reference.element.read_only):
            raise SafeError('Target is not an editable field')
        self.stop.check()
        try:
            wrapper.set_focus()
        except Exception:
            raise SafeError('Required UIA focus operation is unavailable') from None
        from vision.semantic import element_identity
        return {'status': 'requested', 'runtime_id': reference.element.runtime_id,
                'target_identity': element_identity(reference.element),
                'handle': reference.handle, 'app': reference.app,
                'type': reference.element.type, 'name': reference.element.name,
                'bounds': reference.element.bounds, 'automation_id': reference.element.automation_id,
                'scope': reference.element.scope,
                'operation': 'focus', 'control_type': reference.element.type}

    def _address_target(self, element_id):
        reference, wrapper = self._target(element_id)
        element = reference.element
        if (reference.app not in {'chrome', 'edge', 'firefox'}
                or element.type != 'Edit' or element.scope != 'browser_chrome'
                or element.password or element.read_only
                or 'address' not in element.name.casefold()):
            raise SafeError('Target is not the verified browser address bar')
        return reference, wrapper

    def type_url(self, element_id, url):
        from safety.validator import normalize_navigation_url
        from vision.observer import url_digest
        url = normalize_navigation_url(url)
        reference, wrapper = self._address_target(element_id)
        self.guard.verify_input()
        self.stop.check()
        try:
            if not bool(wrapper.element_info.element.CurrentHasKeyboardFocus):
                raise SafeError('Address bar lost keyboard focus')
            wrapper.iface_value.SetValue(url)
        except SafeError:
            raise
        except Exception:
            raise SafeError('Address-bar ValuePattern is unavailable') from None
        from vision.semantic import element_identity
        return {'status': 'url_value_requested', 'runtime_id': reference.element.runtime_id,
                'target_identity': element_identity(reference.element),
                'handle': reference.handle, 'app': reference.app,
                'type': reference.element.type, 'name': reference.element.name,
                'bounds': reference.element.bounds,
                'automation_id': reference.element.automation_id,
                'scope': reference.element.scope, 'operation': 'set_url',
                'control_type': reference.element.type,
                'expected_url_digest': url_digest(url)}

    def navigate_url(self, element_id, url):
        from safety.validator import normalize_navigation_url
        from vision.observer import url_digest
        url = normalize_navigation_url(url)
        reference, wrapper = self._address_target(element_id)
        self.guard.verify_input()
        self.stop.check()
        try:
            if not bool(wrapper.element_info.element.CurrentHasKeyboardFocus):
                raise SafeError('Address bar lost keyboard focus')
            current = wrapper.iface_value.CurrentValue
            if url_digest(current) != url_digest(url):
                raise SafeError('Address-bar value changed before navigation')
        except SafeError:
            raise
        except Exception:
            raise SafeError('Cannot verify address-bar value before navigation') from None
        import pyautogui
        pyautogui.press('enter')
        from vision.semantic import element_identity
        return {'status': 'navigation_requested', 'runtime_id': reference.element.runtime_id,
                'target_identity': element_identity(reference.element),
                'handle': reference.handle, 'app': reference.app,
                'type': reference.element.type, 'name': reference.element.name,
                'bounds': reference.element.bounds,
                'automation_id': reference.element.automation_id,
                'scope': reference.element.scope, 'operation': 'navigate_url',
                'control_type': reference.element.type,
                'expected_url_digest': url_digest(url)}

    def wait_accessibility_ready(self, url, timeout):
        """Read-only bounded Chrome accessibility readiness, not network completion."""
        import hashlib, time
        from safety.validator import normalize_navigation_url
        from tools.uia_ownership import provider_owned
        from vision.observer import url_digest
        url = normalize_navigation_url(url)
        if type(timeout) is not int or not 1 <= timeout <= 15:
            raise SafeError('Invalid readiness timeout')
        active = self.windows.active()
        app, handle = self.windows.app_of(active), active.handle
        if app != 'chrome':
            raise SafeError('Accessibility readiness requires the authorized Chrome window')
        expected, stable, samples = url_digest(url), None, 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.stop.check()
            current = self.windows.active()
            if current.handle != handle or self.windows.app_of(current) != app:
                raise SafeError('READINESS_WINDOW_CHANGED')
            try:
                addresses = [w for w in current.descendants(control_type='Edit') if w.element_info.visible and provider_owned(self.windows,current,w.element_info) and any(x in (w.element_info.name or '').casefold() for x in ('address','search or enter'))]
                if len(addresses) != 1 or url_digest(addresses[0].iface_value.CurrentValue) != expected:
                    raise SafeError('UNEXPECTED_DESTINATION')
                dialogs = [w for w in current.descendants(control_type='Window') if w.element_info.visible and provider_owned(self.windows,current,w.element_info)]
                if dialogs: raise SafeError('BROWSER_DIALOG_BLOCKED')
                documents = [w for w in current.descendants(control_type='Document') if w.element_info.visible and provider_owned(self.windows,current,w.element_info)]
                if len(documents) != 1:
                    stable = None
                else:
                    info=documents[0].element_info
                    if any(x in (info.name or '').casefold() for x in ("site can't be reached",'page unresponsive','network error','privacy error')):
                        raise SafeError('NAVIGATION_ERROR')
                    busy=bool(getattr(info.element,'CurrentIsOffscreen',False))
                    identity=(tuple(info.runtime_id or ()),info.automation_id or '')
                    if not busy and stable == identity:
                        return {'status':'ACCESSIBILITY_READY','handle':handle,'app':app,'expected_url_digest':expected,'document_identity_digest':hashlib.sha256(repr(identity).encode()).hexdigest(),'samples':samples+1}
                    stable=None if busy else identity
                samples += 1
            except SafeError: raise
            except Exception: stable=None
            self.stop.wait(0.2)
        return {'status':'PAGE_LOAD_UNCERTAIN','handle':handle,'app':app,'expected_url_digest':expected,'samples':samples}

    def edit_notepad(self, element_id, text, mode):
        """Set verified Notepad content; creation requires an empty editor."""
        import hashlib
        if mode not in {'insert','replace'}: raise SafeError('Unsupported text-editing mode')
        reference,wrapper=self._target(element_id); element=reference.element
        if reference.app != 'notepad' or element.type not in {'Edit','Document'} or element.password or element.read_only:
            raise SafeError('Target is not the verified Notepad editor')
        self.guard.verify_input(); self.stop.check()
        try:
            if not bool(wrapper.element_info.element.CurrentHasKeyboardFocus): raise SafeError('Notepad editor lost keyboard focus')
            current=wrapper.iface_value.CurrentValue
            if not isinstance(current,str) or len(current)>100000: raise SafeError('Notepad content cannot be verified safely')
            if mode == 'insert' and current: raise SafeError('Existing text protected; use an explicit replace command')
            wrapper.iface_value.SetValue(text)
        except SafeError: raise
        except Exception: raise SafeError('Notepad ValuePattern is unavailable') from None
        from vision.semantic import element_identity
        return {'status':'text_change_requested','runtime_id':element.runtime_id,'target_identity':element_identity(element),'handle':reference.handle,'app':reference.app,'type':element.type,'name':element.name,'bounds':element.bounds,'automation_id':element.automation_id,'scope':element.scope,'operation':'edit_notepad','control_type':element.type,'edit_mode':mode,'before_content_digest':hashlib.sha256(current.encode()).hexdigest(),'expected_content_digest':hashlib.sha256(text.encode()).hexdigest()}

    def read_response(self):
        return self.read_element_text('last')

    def read_element_text(self, element_id):
        reference, wrapper = self._target(element_id)
        from memory.task_content import TaskContent
        try:
            value = wrapper.iface_text.DocumentRange.GetText(4001)
        except Exception:
            value = wrapper.iface_value.CurrentValue
        self.stop.check()
        import hashlib
        if reference.element.content_digest and hashlib.sha256(value.encode()).hexdigest() != reference.element.content_digest:
            raise SafeError('Response changed after completion; please repeat observation')
        return TaskContent.create(value, reference.app, reference.id)
