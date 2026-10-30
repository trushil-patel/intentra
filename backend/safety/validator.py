import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit
from agent.schemas import Action
from utils.helpers import SafeError

SENSITIVE = re.compile(r"(?i)(password|passwd|api[ _-]?key|secret|token|cookie|authorization|private[ _-]?key|credit[ -]?card|banking|purchase|checkout|payment|transfer money|delete file|format drive|powershell|cmd\.exe|regedit|disable (?:antivirus|firewall)|sudo|invoke-expression|[;&|`]|\$\()")
KEYS = {"enter", "tab", "escape", "space", "up", "down", "left", "right", "home", "end", "pageup", "pagedown"}
HOTKEYS = {("ctrl", "a"), ("ctrl", "c"), ("ctrl", "v"), ("ctrl", "f"), ("ctrl", "z")}

def safe_text(value):
    return (not SENSITIVE.search(value) and not re.search(r"(?:\d[ -]?){13,19}|sk-[A-Za-z0-9]+|ghp_|github_pat_", value))

def normalize_url(value):
    if type(value) is not str or not 1 <= len(value) <= 2048:
        raise SafeError("Invalid URL")
    if any(ord(c) < 33 for c in value) or "\\" in value:
        raise SafeError("Invalid URL characters")
    if "://" not in value:
        if ":" in value or value.startswith("/"):
            raise SafeError("URL scheme blocked")
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host):
            raise ValueError()
        if host == "localhost" or host.endswith((".localhost", ".local")) or "." not in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9.-]+", host) or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError()
        else:
            if not address.is_global:
                raise ValueError()
        if parts.port not in {None, 80, 443}:
            raise ValueError()
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, parts.fragment))
    except (ValueError, UnicodeError):
        raise SafeError("URL is not an approved public HTTP(S) address") from None

def normalize_navigation_url(value):
    """Normalize only unambiguous public HTTPS workflow destinations."""
    if type(value) is not str or not value.isascii():
        raise SafeError('Navigation URL must be an ASCII public HTTPS address')
    explicit = '://' in value
    if explicit and not value.casefold().startswith('https://'):
        raise SafeError('Workflow navigation requires HTTPS')
    normalized = normalize_url(value)
    parts = urlsplit(normalized)
    if parts.scheme != 'https' or any(label.startswith('xn--')
                                      for label in parts.hostname.split('.')):
        raise SafeError('Ambiguous or non-HTTPS destination blocked')
    # Canonicalize the default HTTPS port without changing path/query/fragment.
    netloc = parts.hostname
    if parts.port not in {None, 443}:
        raise SafeError('Workflow navigation requires the default HTTPS port')
    return urlunsplit(('https', netloc, parts.path, parts.query, parts.fragment))


class Validator:
    def __init__(self, apps):
        self.apps = apps

    def validate(self, action, spec):
        if not isinstance(action, Action) or type(action.arguments) is not dict or set(action.arguments) != set(spec.arguments):
            raise SafeError("Unexpected tool arguments")
        args = dict(action.arguments)
        for key, expected in spec.arguments.items():
            if type(args[key]) is not expected:
                raise SafeError("Wrong argument type")
            if expected is str and (not args[key] or len(args[key]) > 2000 or any(ord(c) < 32 for c in args[key])):
                raise SafeError("Invalid text argument")
        if "app_name" in args:
            if self.apps.get(args["app_name"], {}).get("allowed") is not True:
                raise SafeError("Application is not approved")
        if "url" in args:
            args["url"] = (normalize_navigation_url(args["url"])
                           if action.tool in {'type_url', 'navigate_url', 'wait_accessibility_ready'}
                           else normalize_url(args["url"]))
            if not safe_text(args["url"]):
                raise SafeError("Sensitive URL blocked")
        if any(not safe_text(args[k]) for k in ("text", "query") if k in args):
            raise SafeError("Sensitive or command-like text blocked; enter it manually")
        if "key" in args and args["key"] not in KEYS:
            raise SafeError("Key is not approved")
        if "keys" in args:
            if any(type(k) is not str for k in args["keys"]) or tuple(args["keys"]) not in HOTKEYS:
                raise SafeError("Shortcut is not approved")
        if "direction" in args and (args["direction"] not in {"up", "down"} or not 1 <= args["amount"] <= 10):
            raise SafeError("Invalid scroll limits")
        if "window_name" in args and (len(args["window_name"]) > 200 or not safe_text(args["window_name"])):
            raise SafeError("Window target blocked")
        if action.tool == 'write_task_text' and args['slot'] != 'response':
            raise SafeError('Unknown task content slot')
        if action.tool == 'wait_for_response' and not 1 <= args['timeout'] <= 60:
            raise SafeError('Invalid response timeout')
        if 'element_id' in args and not re.fullmatch(r'last|element_[a-f0-9]{16}_\d{1,3}', args['element_id']):
            raise SafeError('Invalid element reference')
        if action.tool == 'interact_element' and args['operation'] not in {
                'activate', 'select', 'expand', 'collapse', 'check', 'uncheck'}:
            raise SafeError('Unsupported UIA operation')
        if action.tool == 'edit_notepad' and args['mode'] not in {'insert', 'replace'}:
            raise SafeError('Unsupported text-editing mode')
        if action.tool == 'wait_accessibility_ready' and not 1 <= args['timeout'] <= 15:
            raise SafeError('Invalid readiness timeout')
        if action.tool == 'wait_for':
            from agent.wait_manager import ConditionKind
            from config.settings import AgentLimits
            if args['condition'] not in {kind.value for kind in ConditionKind} or not 1 <= args['timeout'] <= AgentLimits().wait_seconds:
                raise SafeError('Invalid bounded wait')
        if action.tool == "navigate_explorer_folder":
            if args["folder"] not in {"downloads"}:
                raise SafeError("Explorer folder is not approved")
            if self.apps.get("explorer", {}).get("allowed") is not True:
                raise SafeError("File Explorer is not approved")
        return Action(action.tool, args)
