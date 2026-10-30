"""Conservative UIA page adapter. Labels are evidence, not permissions."""
from vision.observer import url_digest
from vision.semantic import ScreenState
from utils.helpers import SafeError


class BrowserAdapter:
    response_labels = {"claude response", "claude's response", "assistant response", "assistant message"}
    generating_labels = {'stop generating', 'stop response', 'stop streaming'}

    def is_claude(self, state):
        return (state.available and state.current_application == 'chrome'
                and (state.origin_digest == url_digest('https://claude.ai')
                     or state.url_digest == url_digest('https://claude.ai')))

    def generating(self, state):
        return self.is_claude(state) and any(e.enabled and e.name.casefold() in self.generating_labels for e in state.elements)

    def response(self, state):
        if not self.is_claude(state) or not state.elements_complete:
            raise SafeError('Claude page or complete response structure cannot be verified')
        screen = ScreenState.from_computer(state)
        if screen.has_modal: raise SafeError('Unexpected dialog; inspect it manually')
        matches = [ref for ref in screen.elements if not ref.element.password
                   and (ref.element.name.casefold() in self.response_labels
                        or ref.element.automation_id == 'assistant-response')]
        if len(matches) != 1:
            raise SafeError('Response region is missing or ambiguous; select it manually')
        return matches[0]
