from utils.helpers import SafeError
from safety.validator import safe_text


def safe_editor(element):
    name = element.name.casefold()
    return (element.type in {"Edit", "Document"} and element.enabled and not element.password and not element.read_only
            and safe_text(name) and not any(word in name for word in
                ("address", "omnibox", "terminal", "console", "sign in", "log in", "search or enter")))


def find_element_by_text(elements, text, kind=None):
    matches = [e for e in elements if e.enabled and not e.password and e.name.casefold() == text.casefold()
               and (kind is None or e.type == kind)]
    if len(matches) != 1:
        raise SafeError("UI element missing or ambiguous; no interaction performed")
    return matches[0]


def find_button(elements, text):
    return find_element_by_text(elements, text, "Button")


def find_text_field(elements):
    matches = [e for e in elements if safe_editor(e)]
    if len(matches) != 1:
        raise SafeError("Text field missing or ambiguous; select it manually")
    return matches[0]
