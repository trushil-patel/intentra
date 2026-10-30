"""Short-lived evidence. UI labels are untrusted and never planner instructions."""
from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True)
class WindowState:
    handle: int
    app: str
    minimized: bool = False
    maximized: bool = False
    title: str = field(default="", repr=False)


@dataclass(frozen=True)
class ForegroundOwner:
    raw_handle: int | None
    top_level_handle: int | None
    pid: int | None
    executable_name: str | None
    executable_digest: str | None
    application: str | None
    window_class: str | None
    visible: bool | None
    owner_handle: int | None
    root_owner_handle: int | None
    category: str
    captured_monotonic_ns: int
    capture_process_id: int


@dataclass(frozen=True)
class Element:
    runtime_id: tuple
    type: str
    name: str = field(repr=False)
    enabled: bool = True
    password: bool = False
    source: str = "uia"
    read_only: bool = False
    bounds: tuple[int, int, int, int] = ()
    focused: bool | None = False
    selected: bool | None = False
    checked: bool | None = None
    expanded: str | None = None
    clickable: bool = False
    confidence: float = 1.0
    automation_id: str = field(default="", repr=False)
    modal: bool = False
    content_digest: str | None = None
    scope: str = "application"
    keyboard_focusable: bool | None = None


@dataclass(frozen=True)
class ComputerState:
    active_window: WindowState | None = None
    open_windows: tuple[WindowState, ...] = ()
    elements: tuple[Element, ...] = ()
    timestamp: float = field(default_factory=monotonic)
    available: bool = False
    focused_id: tuple = ()
    text_matches: bool | None = None
    url_digest: str | None = None
    focused_text_length: int | None = None
    focused_text_digest: str | None = None
    screenshot_reference: str | None = field(default=None, repr=False)
    elements_complete: bool = True
    origin_digest: str | None = None
    focused_line_breaks: int | None = None
    visited_count: int = 0
    max_visited_depth: int = 0
    limit_reached: str | None = None
    traversal_ms: float = 0.0
    provider_failure: str | None = None
    target_verification_complete: bool = False
    foreground_owner: ForegroundOwner | None = None
    verification_started_monotonic_ns: int | None = None
    verification_process_id: int | None = None
    queried_roles: tuple[str, ...] = ()
    query_scope: str | None = None
    focus_evidence: str | None = None
    focused_element_runtime_id: tuple = ()
    focused_element_type: str | None = None
    focused_element_automation_id: str | None = field(default=None, repr=False)
    focused_element_provider_owned: bool | None = None
    focused_element_scope: str | None = None

    @property
    def current_application(self):
        return self.active_window.app if self.active_window else None

    def planner_context(self):
        # Only canonical, allowlisted application identities leave the observer.
        apps = {"chrome", "edge", "firefox", "notepad", "vscode"}
        return {"current_application": self.current_application if self.current_application in apps else None,
                "open_applications": sorted({w.app for w in self.open_windows if w.app in apps})}
