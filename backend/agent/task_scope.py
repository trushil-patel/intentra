"""A fixed cross-app workflow is authorized only by the direct user request."""
from dataclasses import dataclass
import re
from agent.schemas import Action, Plan
from utils.helpers import SafeError


TRANSFER = re.compile(
    r'(?is)(?:please )?open chrome,? (?:and )?go to claude(?:\.ai)?,? (?:and )?'
    r'ask it to (.+?),? (?:and )?wait (?:for (?:the )?(?:response|answer)|until (?:the )?(?:answer|response) (?:finishes|is finished)|until it finishes),? '
    r'(?:then |and )?(?:put|copy|paste) (?:the )?(?:answer|response) (?:into|in|to) notepad[.!]?$')
TRANSFER_TOOLS = {'wait_for_response', 'read_response', 'write_task_text'}


def transfer_plan(command):
    match = TRANSFER.fullmatch(command.strip())
    if not match: return None
    prompt = match[1].strip().rstrip(',')
    if len(prompt) >= 2 and prompt[0] == prompt[-1] and prompt[0] in {'"', "'"}:
        prompt = prompt[1:-1]
    if not prompt: return None
    return Plan('User-requested Claude response transfer to Notepad', (
        Action('open_app', {'app_name': 'chrome'}),
        Action('open_url', {'url': 'https://claude.ai'}),
        Action('focus_text_field', {}), Action('type_text', {'text': prompt}),
        Action('press_key', {'key': 'enter'}), Action('wait_for_response', {'timeout': 60}),
        Action('read_response', {}), Action('open_app', {'app_name': 'notepad'}),
        Action('focus_text_field', {}), Action('write_task_text', {'slot': 'response'})))


@dataclass(frozen=True)
class TaskScope:
    transfer: bool = False

    @classmethod
    def from_user(cls, command):
        return cls(transfer_plan(command) is not None)

    def validate(self, actions):
        if any(a.tool in TRANSFER_TOOLS for a in actions) and not self.transfer:
            raise SafeError('Data transfer is outside the explicit user goal')


def validate_model_scope(command, plan, context):
    """Conservative traceability, not an LLM judgment about its own authority."""
    text = command.casefold()
    from urllib.parse import urlsplit
    from safety.validator import normalize_url
    operation_words = {
        'get_current_time': ('time',), 'list_windows': ('window',),
        'get_active_window': ('window', 'active app'),
        'take_screenshot': ('screenshot',), 'get_visible_elements': ('visible', 'element'),
        'read_visible_text': ('read',), 'read_element_text': ('read',),
        'summarize_page': ('summariz',), 'scroll': ('scroll', 'down', 'up'),
        'focus_text_field': ('type', 'write', 'enter', 'ask', 'field', 'input'),
        'focus_element': ('focus', 'select', 'field', 'input'),
        'click_element': ('click', 'select', 'choose'),
        'interact_element': ('click', 'select', 'choose', 'expand', 'collapse', 'check', 'uncheck'), 'click': ('click',),
        'double_click': ('double click',), 'right_click': ('right click',),
        'new_tab': ('new tab',), 'close_tab': ('close tab',),
        'refresh_page': ('refresh', 'reload'), 'browser_back': ('back',),
        'browser_forward': ('forward',), 'focus_address_bar': ('address',),
        'hotkey': ('press', 'copy', 'select all', 'undo', 'find'),
        'wait_for': ('wait',),
    }
    for action in plan.actions:
        tool, args = action.tool, action.arguments
        if tool in TRANSFER_TOOLS:
            raise SafeError('Model-generated data transfer requires an explicit supported workflow')
        if 'app_name' in args:
            names = {'chrome': ('chrome', 'browser', 'claude'), 'notepad': ('notepad',),
                     'edge': ('edge', 'microsoft edge'), 'firefox': ('firefox', 'mozilla firefox'),
                     'vscode': ('vs code', 'vscode', 'visual studio code')}
            if args['app_name'] not in names or not any(n in text for n in names[args['app_name']]):
                raise SafeError('Proposed application is outside the user goal')
        elif tool == 'open_url':
            try: host = urlsplit(normalize_url(args['url'])).hostname
            except Exception: raise SafeError('Proposed destination is invalid') from None
            requested_urls = set()
            for token in re.findall(r'(?:https?://)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:[/?#][^\s,]+)?', text):
                try: requested_urls.add(normalize_url(token.strip("'\"().")).rstrip('/'))
                except SafeError: pass
            if re.search(r'\bclaude\b', text): requested_urls.add('https://claude.ai')
            if normalize_url(args['url']).rstrip('/') not in requested_urls:
                raise SafeError('Proposed destination was not requested by the user')
        elif tool in {'type_text', 'search_web', 'find_element'}:
            key = {'type_text': 'text', 'search_web': 'query', 'find_element': 'query'}[tool]
            value = args.get(key)
            if type(value) is not str or value.casefold() not in text:
                raise SafeError('Proposed text or target cannot be traced to the user request')
        elif tool == 'press_key':
            key = args.get('key')
            if type(key) is not str or (key not in text and not (key == 'enter' and any(w in text for w in ('submit', 'send', 'ask')))):
                raise SafeError('Proposed key was not requested by the user')
        elif 'window_name' in args:
            if type(args['window_name']) is not str or args['window_name'].casefold() not in text:
                raise SafeError('Proposed window was not named by the user')
        elif tool not in operation_words or not any(word in text for word in operation_words[tool]):
            raise SafeError('Proposed operation is outside the user goal; clarify the request')
