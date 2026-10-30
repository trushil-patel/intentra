from utils.performance import timed
import re
from agent.schemas import Action, Plan

from agent.command_language import ALIASES, STOP_WORDS, recognize, command_body
PROMPT_REQUIRED_GOAL = "Open Claude; prompt text required"

def normalized(command):
    return command.strip().rstrip(".!?").casefold()

def deterministic(command):
    recognized = recognize(command)
    if recognized.action is not None:
        return Plan("User command", (recognized.action,))
    command = command_body(command)
    text = normalized(command)
    # Existing explicit non-phonetic navigation/convenience phrases stay supported.
    text = {"go down": "scroll down", "move down the page": "scroll down",
            "go up": "scroll up", "move up the page": "scroll up"}.get(text, text)
    action = recognize(text).action
    match = re.fullmatch(r"(?:go to|navigate to) (\S+)", command.strip().rstrip("."), re.I)
    if match: action = Action("open_url", {"url": match.group(1)})
    fixed = {"copy this": Action("hotkey", {"keys": ["ctrl", "c"]}), "paste this": Action("hotkey", {"keys": ["ctrl", "v"]}),
             "click": Action("click", {}), "double click": Action("double_click", {}), "right click": Action("right_click", {}),
             "take screenshot": Action("take_screenshot", {}), "what time is it": Action("get_current_time", {}), "list windows": Action("list_windows", {})}
    action = fixed.get(text, action)
    return Plan("User command", (action,)) if action else None


def _workflow_text(rest, prompt_prefix=False):
    """Submission syntax is recognized only outside an explicitly quoted payload."""
    typed = re.fullmatch(r"(?is)(?:type\s*:?\s+|enter\s+|write (?:a short )?note saying |write\s+|ask it to )(.+)", rest)
    if not typed:
        return None
    payload = typed[1]
    if prompt_prefix:
        payload = re.sub(r"(?i)^(?:the )?prompt\s*:\s*", "", payload)
    wants_submit = rest.casefold().startswith("ask it to ")
    if payload.startswith(('"', "'")):
        quoted = re.fullmatch(r'''(?is)(["'])(.*?)\1(,? and press enter[.!]?)?[.!]?''', payload)
        if not quoted:
            return None
        payload = quoted[2]
        wants_submit = wants_submit or bool(quoted[3])
    else:
        submit = re.search(r"(?i),? and press enter[.!]?$", payload)
        if submit:
            payload = payload[:submit.start()].strip()
            wants_submit = True
    if not payload:
        return None
    actions = [Action("focus_text_field", {}), Action("type_text", {"text": payload})]
    if wants_submit:
        actions.append(Action("press_key", {"key": "enter"}))
    return actions


def _phase34_workflow(command):
    """Small explicit grammar for approved dependent desktop workflows."""
    match = re.fullmatch(
        r'(?is)(?:please\s+)?(?:open|launch|start)\s+'
        r'(google chrome|chrome|notepad|file explorer|explorer)\s*'
        r'(?:,\s*(?:(?:and|then)\s+)?|(?:and|then)\s+)(.+?)[.!]?$', command.strip())
    if not match:
        return None
    spoken_app, remainder = match.groups()
    app = {'google chrome': 'chrome', 'chrome': 'chrome',
           'notepad': 'notepad', 'file explorer': 'explorer',
           'explorer': 'explorer'}[spoken_app.casefold()]
    remainder = remainder.strip()
    if app == 'chrome' and re.fullmatch(
            r'(?i)focus (?:the )?address bar', remainder):
        return Plan('Open Chrome and focus its verified address bar', (
            Action('open_app', {'app_name': 'chrome'}),
            Action('find_element', {'query': 'address bar'}),
            Action('focus_element', {'element_id': 'last'})))
    if app == 'notepad' and re.fullmatch(
            r'(?i)focus (?:the )?text editor', remainder):
        return Plan('Open Notepad and focus its verified text editor', (
            Action('open_app', {'app_name': 'notepad'}),
            Action('find_element', {'query': 'text editor'}),
            Action('focus_element', {'element_id': 'last'})))
    explorer = re.fullmatch(r'(?i)(?:select|click) (?:the )?view(?: tab)?', remainder)
    if app == 'explorer' and explorer:
        return Plan('Open File Explorer and select its verified View tab', (
            Action('open_app', {'app_name': 'explorer'}),
            Action('find_element', {'query': 'View tab'}),
            Action('interact_element', {'element_id': 'last',
                                        'operation': 'select'})))
    navigation = re.fullmatch(
        r'(?i)(?:go to|navigate to|visit)\s+(\S+?)(?:,?\s+and\s+wait\s+until\s+(?:the\s+)?page\s+is\s+ready)?', remainder)
    if app == 'chrome' and navigation:
        from safety.validator import normalize_navigation_url
        destination = normalize_navigation_url(navigation[1])
        actions = [
            Action('open_app', {'app_name': 'chrome'}),
            Action('find_element', {'query': 'address bar'}),
            Action('focus_element', {'element_id': 'last'}),
            Action('type_url', {'element_id': 'last', 'url': destination}),
            Action('navigate_url', {'element_id': 'last', 'url': destination})]
        if re.search(r'(?i)wait\s+until\s+(?:the\s+)?page\s+is\s+ready', remainder):
            actions.append(Action('wait_accessibility_ready', {'url': destination, 'timeout': 10}))
        return Plan('Open Chrome and verify accessibility readiness' if len(actions) == 6
                    else 'Open Chrome and navigate to the authorized HTTPS destination', tuple(actions))
    editing = re.fullmatch(r'(?is)(write|type)\s+(.+)|replace\s+(?:the\s+)?text\s+with\s+(.+)', remainder)
    if app == 'notepad' and editing:
        mode = 'replace' if editing[3] is not None else 'insert'
        payload = (editing[3] if mode == 'replace' else editing[2]).strip()
        if payload.startswith(('"', "'")):
            return None
        if (not payload or len(payload) > 2000
                or re.search(r'(?i)\s+and\s+(?:save|close|submit|send|delete|download)(?:\s+it)?$', payload)):
            return None
        return Plan('Open Notepad and modify its verified editor', (
            Action('open_app', {'app_name': 'notepad'}),
            Action('find_element', {'query': 'text editor'}),
            Action('focus_element', {'element_id': 'last'}),
            Action('edit_notepad', {'element_id': 'last', 'text': payload, 'mode': mode})))
    return None


@timed("deterministic_parser")
def deterministic_v2(command):
    """Conservative composition grammar; typing payload is never split into commands."""
    from agent.task_scope import transfer_plan
    transfer = transfer_plan(command)
    if transfer: return transfer
    if re.fullmatch(r'(?is)(?:please\s+)?(?:open|launch|start)\s+notepad.+\s+and\s+(?:save|close|submit|send|delete|download)(?:\s+it)?[.!]?', command.strip()):
        return None
    workflow = _phase34_workflow(command)
    if workflow: return workflow
    website_generic = re.fullmatch(
        r'(?is)(?:please\s+)?(?:open|visit|go to|navigate to)\s+(https?://[^\s,]+|[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s,]*)?)(?:\s+and\s+(.+))?', command.strip())
    if website_generic and website_generic[1].casefold().rstrip('/') not in {'claude.ai', 'https://claude.ai', 'http://claude.ai'}:
        url = website_generic[1].rstrip('.')
        from safety.validator import normalize_url
        normalize_url(url)
        actions = [Action('open_url', {'url': url})]
        if website_generic[2]:
            typed = _workflow_text(website_generic[2], prompt_prefix=True)
            if typed is None:
                # Preserve the existing Claude prompt-required behavior below.
                if 'claude.ai' not in url.casefold(): return None
            else:
                return Plan('Enter requested text on website', tuple(actions + typed))
        else:
            return Plan('Open requested website', tuple(actions))
    website = re.fullmatch(
        r"(?is)(?:please\s+)?(?:open|visit|go to|navigate to)\s+(?:the\s+)?(?:https?://)?claude(?:\.ai|\s+dot\s+ai|\s+ai)?/?(?:[.!]?|\s+and\s+(.+))",
        command.strip(),
    )
    if website:
        actions = [Action("open_url", {"url": "https://claude.ai"})]
        remainder = website[1]
        if remainder is None:
            return Plan("Open requested website", tuple(actions))
        if re.fullmatch(r"(?i)(?:enter|type|write)(?: in)? (?:the|a|my) prompt[.!]?", remainder):
            return Plan(PROMPT_REQUIRED_GOAL, tuple(actions))
        typed = _workflow_text(remainder, prompt_prefix=True)
        if typed:
            actions.extend(typed)
            return Plan("Enter requested text in Claude", tuple(actions))
        return None
    simple = deterministic(command)
    if simple:
        if simple.actions[0].tool == "open_url" and simple.actions[0].arguments["url"].casefold() == "claude":
            return Plan("Open requested website", (Action("open_url", {"url": "https://claude.ai"}),))
        return simple
    # Preserve the existing "now search for" syntax without stripping its payload.
    search = recognize(re.sub(r"(?i)^now\s+", "", command.lstrip()))
    if search.action is not None and search.action.tool == 'search_web':
        return Plan("Web search", (search.action,))
    text = re.sub(r"(?i)^now\s+", "", command.strip())
    if normalized(text) in {'show available buttons', 'show buttons',
                             'find buttons on this screen'}:
        return Plan('List available buttons',
                    (Action('list_elements', {'role': 'button'}),))
    discovery = re.fullmatch(r'(?is)find (.+?)[.!]?$', text)
    if discovery and normalized(text) != 'find text field':
        return Plan('Find user-named element', (Action('find_element', {'query': discovery[1]}),))
    focus = re.fullmatch(r'(?is)focus (.+?(?:field|box|bar|editor))[.!]?$', text)
    if focus:
        return Plan('Focus user-named field', (
            Action('find_element', {'query': focus[1]}),
            Action('focus_element', {'element_id': 'last'})))
    interaction = re.fullmatch(r'(?is)(click|select|expand|collapse|check|uncheck) (.+?)[.!]?$', text)
    if interaction:
        verb, query = interaction.groups()
        if verb.casefold() == 'click' and query.casefold() == 'new tab':
            query = 'New Tab button'
        operation = {'select': 'select', 'expand': 'expand', 'collapse': 'collapse',
                     'check': 'check', 'uncheck': 'uncheck'}.get(verb.casefold())
        action = (Action('click_element', {'element_id': 'last'}) if operation is None else
                  Action('interact_element', {'element_id': 'last', 'operation': operation}))
        return Plan('Interact with user-named element', (
            Action('find_element', {'query': query}), action))
    match = re.fullmatch(r'(?i)wait for (.+) to open', text.rstrip('.'))
    if match and match[1].lower() in ALIASES:
        return Plan('Wait for requested application', (Action('wait_for', {
            'condition': 'window_exists', 'value': ALIASES[match[1].lower()], 'timeout': 30}),))
    fixed = {"new tab": "new_tab", "close tab": "close_tab", "refresh page": "refresh_page",
             "go back": "browser_back", "go forward": "browser_forward", "focus address bar": "focus_address_bar",
             "find text field": "focus_text_field", "read visible text": "read_visible_text",
             "show visible elements": "get_visible_elements"}
    fixed.update({'summarize this page': 'summarize_page', 'summarize the page': 'summarize_page'})
    if normalized(text) in fixed:
        return Plan("User command", (Action(fixed[normalized(text)], {}),))
    match = re.fullmatch(r"(?is)(?:please )?(?:open|launch|start) (.+?)(?:,? and |, )(.+)", text)
    if not match or match[1].casefold() not in ALIASES: return None
    actions = [Action("open_app", {"app_name": ALIASES[match[1].casefold()]})]
    rest = match[2]
    navigation = re.match(r"(?i)(?:open|visit|go to|navigate to) (claude(?:\.ai)?|https?://[^ ,]+|[a-z0-9.-]+\.[a-z]{2,})(?:[.]?$|,? and |, )", rest)
    if navigation and ALIASES[match[1].casefold()] not in {'chrome', 'edge', 'firefox'}:
        return None
    if navigation:
        url = navigation[1]
        if url.casefold() == "claude": url = "https://claude.ai"
        actions.append(Action("open_url", {"url": url}))
        rest = rest[navigation.end():]
    if rest:
        typed = _workflow_text(rest)
        if not typed: return None
        actions.extend(typed)
    return Plan("User requested desktop workflow", tuple(actions)) if len(actions) > 1 else None