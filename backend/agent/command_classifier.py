"""Conservative routing, never phonetic correction or action authorization."""
import re
from enum import StrEnum
from utils.performance import timed


class CommandKind(StrEnum):
    DETERMINISTIC = 'DETERMINISTIC'
    REPEAT = 'REPEAT'
    COMPLEX = 'COMPLEX'


@timed('command_classifier')
def classify(command, plan=None):
    if plan is not None: return CommandKind.DETERMINISTIC
    from agent.command_language import recognize, LanguageKind
    grammar = recognize(command)
    if grammar.kind == LanguageKind.STOP: return CommandKind.DETERMINISTIC
    if grammar.kind in {LanguageKind.UNRESOLVED, LanguageKind.AMBIGUOUS}: return CommandKind.REPEAT
    text = re.sub(r'\s+', ' ', command.strip().casefold()).rstrip('.!?')
    text = re.sub(r'^(?:please |can you |could you )+', '', text)
    words = text.split()
    if len(words) < 2: return CommandKind.REPEAT
    # Explicit semantic goals may need planning even when brief. This grants no
    # extra capabilities: model scope, schema and policy still validate the plan.
    if re.match(r'^(?:help|summarize|explain|compare|organize|prepare|describe)\b', text):
        return CommandKind.COMPLEX
    if re.fullmatch(r'get .+ ready', text): return CommandKind.COMPLEX
    # An unsupported elementary command is a repeat request, not a model puzzle.
    compound = bool(re.search(r'\b(?:and|then|after|before|until)\b', text))
    if len(words) <= 5 and not compound: return CommandKind.REPEAT
    if re.match(r'^(?:open|launch|start|close|switch|focus|press|scroll|type)\b', text) and not compound:
        return CommandKind.REPEAT
    if compound and len(words) >= 5: return CommandKind.COMPLEX
    if len(words) >= 6 and re.match(r'^(?:find|show|create|read|what|how|when|where|why|i need|i want)\b', text):
        return CommandKind.COMPLEX
    return CommandKind.REPEAT
