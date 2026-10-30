"""Exact elementary command grammar. Recognition proposes; Router authorizes.

No fuzzy/phonetic matching, no acoustic guesses, and no rewriting of payloads.
The app vocabulary is the existing supported registry; runtime allowlist checks
remain mandatory even when an application name is recognized here.
"""
from dataclasses import dataclass
from enum import StrEnum
import re

from agent.schemas import Action
from safety.validator import KEYS, HOTKEYS
from config.app_catalog import ALIASES

STOP_WORDS = frozenset({'stop', 'cancel', 'abort'})
APP_VERBS = {
    'open': 'open_app', 'launch': 'open_app', 'start': 'open_app',
    'switch to': 'focus_app', 'focus': 'focus_app', 'close': 'close_app',
    'minimize': 'minimize_app', 'maximize': 'maximize_app',
}


class LanguageKind(StrEnum):
    CONTROL = 'CONTROL'
    PAYLOAD = 'PAYLOAD'
    STOP = 'STOP'
    UNRESOLVED = 'UNRESOLVED'
    AMBIGUOUS = 'AMBIGUOUS'
    OTHER = 'OTHER'


@dataclass(frozen=True)
class Recognition:
    kind: LanguageKind
    action: Action | None = None


def command_body(command):
    # Strip syntax at the front only; do not strip arbitrary payload at the end.
    return re.sub(r'(?i)^(?:(?:please|can you|could you)\s+){1,2}', '', command.lstrip())


def recognize(command):
    if command.strip().rstrip('.!?').casefold() in STOP_WORDS:
        return Recognition(LanguageKind.STOP)

    body = command_body(command)
    text = re.sub(r'\s+', ' ', body).strip().rstrip('.!?').casefold()

    # Exact, authorized File Explorer navigation; no fuzzy name guessing.
    if text in {'go to downloads', 'open downloads', 'navigate to downloads'}:
        return Recognition(
            LanguageKind.CONTROL,
            Action('navigate_explorer_folder', {'folder': 'downloads'}),
        )

    # Preserve payload exactly as spoken, including case and punctuation.
    payload = re.fullmatch(
        r'(?is)(type|write|enter\s+text|search(?:\s+web)?\s+for)(?:\s*:\s*|\s+)(.+)',
        body,
    )
    if payload:
        value = payload[2]
        if not value.strip():
            return Recognition(LanguageKind.UNRESOLVED)
        search = payload[1].casefold().startswith('search')
        return Recognition(
            LanguageKind.PAYLOAD,
            Action('search_web' if search else 'type_text', {'query' if search else 'text': value}),
        )

    app = re.fullmatch(r'(open|launch|start|switch to|focus|close|minimize|maximize) (.+)', text)
    if app and app[2] in ALIASES:
        return Recognition(LanguageKind.CONTROL, Action(APP_VERBS[app[1]], {'app_name': ALIASES[app[2]]}))
    if app:
        options = app[2].split(' or ')
        if len(options) > 1 and all(option in ALIASES for option in options):
            return Recognition(LanguageKind.AMBIGUOUS)

    key = re.fullmatch(r'press (.+)', text)
    if key:
        options = key[1].split(' or ')
        if len(options) > 1 and all(recognize('press ' + option).kind == LanguageKind.CONTROL for option in options):
            return Recognition(LanguageKind.AMBIGUOUS)
        if key[1] in KEYS:
            return Recognition(LanguageKind.CONTROL, Action('press_key', {'key': key[1]}))
        # Standard shortcut separators only, after the literal-payload boundary.
        # Repeated/mixed separators and extra keys cannot match this grammar.
        shortcut = re.fullmatch(r'(control|ctrl)(?: +| *[-+] *)([a-z])', key[1])
        if shortcut:
            keys = ('ctrl', shortcut[2])
            if keys in HOTKEYS:
                return Recognition(LanguageKind.CONTROL, Action('hotkey', {'keys': list(keys)}))

    scroll = re.fullmatch(r'scroll (up|down)(?: (\d{1,2}))?', text)
    if scroll and 1 <= int(scroll[2] or 3) <= 10:
        return Recognition(
            LanguageKind.CONTROL,
            Action('scroll', {'direction': scroll[1], 'amount': int(scroll[2] or 3)}),
        )

    # Unsupported elementary commands must not become open-ended model guesses.
    # Supported first clauses may continue through the composition planner.
    elementary = re.match(r'^(open|launch|start|switch|focus|close|minimize|maximize|press|scroll|type|write|search|enter|go to|navigate to)\b', text)
    if elementary:
        prefix = re.split(r',? (?:and|then|after|before|until|when) |, ', text, maxsplit=1)
        if len(prefix) == 2 and prefix[1] and prefix[0] != text:
            head = recognize(prefix[0])
            if head.kind == LanguageKind.CONTROL:
                return Recognition(LanguageKind.OTHER)
        return Recognition(LanguageKind.UNRESOLVED)
    return Recognition(LanguageKind.OTHER)
