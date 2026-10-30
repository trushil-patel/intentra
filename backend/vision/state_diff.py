from dataclasses import dataclass
from vision.semantic import ScreenState


@dataclass(frozen=True)
class StateDiff:
    known: bool
    window_changed: bool = False
    page_changed: bool = False
    focus_changed: bool = False
    dialog_appeared: bool = False
    added: tuple[tuple, ...] = ()
    removed: tuple[tuple, ...] = ()
    content_changed: bool = False

    @property
    def changed(self):
        return self.known and any((self.window_changed, self.page_changed, self.focus_changed,
                                   self.dialog_appeared, self.added, self.removed, self.content_changed))


def compare(before: ScreenState, after: ScreenState) -> StateDiff:
    a, b = before.computer, after.computer
    if not a.available or not b.available or not a.elements_complete or not b.elements_complete:
        return StateDiff(False)
    previous = {e.runtime_id for e in a.elements}
    current = {e.runtime_id for e in b.elements}
    return StateDiff(True,
                     (a.active_window.handle if a.active_window else None) !=
                     (b.active_window.handle if b.active_window else None),
                     bool(a.url_digest and b.url_digest and a.url_digest != b.url_digest),
                     a.focused_id != b.focused_id,
                     after.has_modal and not before.has_modal,
                     tuple(sorted(current - previous, key=repr)), tuple(sorted(previous - current, key=repr)),
                     before.state_id != after.state_id or a.focused_text_digest != b.focused_text_digest)
