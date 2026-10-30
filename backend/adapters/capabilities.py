"""Typed capability declarations over Intentra's existing tools.

This module resolves authority; it does not execute UIA or bypass Router policy.
"""
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit
from safety.policy import Risk
from safety.validator import normalize_navigation_url
from utils.helpers import SafeError

class UniversalIntent(StrEnum):
    OPEN="OPEN"; SWITCH="SWITCH"; NAVIGATE="NAVIGATE"; FIND="FIND"; FOCUS="FOCUS"
    CLICK="CLICK"; SELECT="SELECT"; TYPE="TYPE"; READ="READ"; WAIT="WAIT"

class DestinationKind(StrEnum):
    HTTPS_URL="HTTPS_URL"; APPROVED_FOLDER="APPROVED_FOLDER"; APPLICATION_SECTION="APPLICATION_SECTION"

@dataclass(frozen=True)
class Capability:
    intent: UniversalIntent
    tools: tuple[str, ...]
    roles: frozenset[str]
    risk: Risk
    preconditions: tuple[str, ...]
    verification: str
    failures: tuple[str, ...]
    state_changing: bool

@dataclass(frozen=True)
class AdapterDeclaration:
    name: str
    applications: frozenset[str]
    capabilities: frozenset[UniversalIntent]
    window_classes: frozenset[str]=frozenset()

CONTRACTS=MappingProxyType({
 UniversalIntent.OPEN: Capability(UniversalIntent.OPEN,("open_app","navigate_explorer_folder"),frozenset(),Risk.LOW,("allowlisted target",),"exact approved foreground HWND and executable",("APPLICATION_NOT_APPROVED","APPLICATION_AMBIGUOUS","WINDOW_NOT_VERIFIED"),True),
 UniversalIntent.SWITCH: Capability(UniversalIntent.SWITCH,("focus_app","focus_window"),frozenset(),Risk.LOW,("unique approved window",),"exact requested HWND foreground",("WINDOW_AMBIGUOUS","FOREGROUND_CHANGED"),True),
 UniversalIntent.NAVIGATE: Capability(UniversalIntent.NAVIGATE,("type_url","navigate_url","navigate_explorer_folder","interact_element"),frozenset({"Edit","TreeItem","TabItem"}),Risk.MEDIUM,("typed destination","adapter support"),"destination-specific authoritative state",("DESTINATION_AMBIGUOUS","UNEXPECTED_DESTINATION","PROVIDER_UNAVAILABLE"),True),
 UniversalIntent.FIND: Capability(UniversalIntent.FIND,("find_element",),frozenset({"Button","Edit","Document","TabItem","ListItem","TreeItem","CheckBox","RadioButton"}),Risk.LOW,("complete relevant role query",),"one exact provider-owned target",("TARGET_MISSING","TARGET_AMBIGUOUS","OBSERVATION_INCOMPLETE"),False),
 UniversalIntent.FOCUS: Capability(UniversalIntent.FOCUS,("focus_element",),frozenset({"Edit","Document"}),Risk.MEDIUM,("unique writable target",),"authoritative keyboard focus",("FOCUS_UNAVAILABLE","FOREGROUND_CHANGED"),True),
 UniversalIntent.CLICK: Capability(UniversalIntent.CLICK,("click_element",),frozenset({"Button","Hyperlink","MenuItem"}),Risk.MEDIUM,("unique target","supported UIA pattern"),"operation-specific expected effect",("PATTERN_UNSUPPORTED","EXPECTED_EFFECT_UNAVAILABLE"),True),
 UniversalIntent.SELECT: Capability(UniversalIntent.SELECT,("interact_element",),frozenset({"TabItem","ListItem","RadioButton","TreeItem"}),Risk.MEDIUM,("unique selection target",),"SelectionItem selected state",("SELECTION_UNAVAILABLE","TARGET_AMBIGUOUS"),True),
 UniversalIntent.TYPE: Capability(UniversalIntent.TYPE,("edit_notepad","type_url"),frozenset({"Edit","Document"}),Risk.MEDIUM,("exact writable target","application-specific contract"),"exact private content digest",("PASSWORD_FIELD","VALUE_PATTERN_UNAVAILABLE","CONTENT_UNVERIFIED"),True),
 UniversalIntent.READ: Capability(UniversalIntent.READ,("list_elements","get_visible_elements"),frozenset({"Button","Edit","TabItem","ListItem","TreeItem"}),Risk.LOW,("complete scoped structural observation",),"bounded privacy-filtered structural observation",("OBSERVATION_INCOMPLETE","SENSITIVE_CONTENT"),False),
 UniversalIntent.WAIT: Capability(UniversalIntent.WAIT,("wait_for","wait_accessibility_ready"),frozenset({"Document"}),Risk.LOW,("bounded timeout","adapter readiness contract"),"provider-tested condition",("TIMEOUT","READINESS_UNCERTAIN","CANCELLED"),False),
})
ADAPTERS=(
 AdapterDeclaration("chromium_chrome",frozenset({"chrome"}),frozenset({UniversalIntent.OPEN,UniversalIntent.SWITCH,UniversalIntent.NAVIGATE,UniversalIntent.FIND,UniversalIntent.FOCUS,UniversalIntent.CLICK,UniversalIntent.SELECT,UniversalIntent.READ,UniversalIntent.WAIT})),
 AdapterDeclaration("file_explorer",frozenset({"explorer"}),frozenset({UniversalIntent.OPEN,UniversalIntent.SWITCH,UniversalIntent.NAVIGATE,UniversalIntent.FIND,UniversalIntent.FOCUS,UniversalIntent.CLICK,UniversalIntent.SELECT,UniversalIntent.READ}),frozenset({"CabinetWClass"})),
 AdapterDeclaration("notepad",frozenset({"notepad"}),frozenset({UniversalIntent.OPEN,UniversalIntent.SWITCH,UniversalIntent.FIND,UniversalIntent.FOCUS,UniversalIntent.TYPE,UniversalIntent.READ})),
)
GENERIC_CAPABILITIES=frozenset({UniversalIntent.OPEN,UniversalIntent.SWITCH,UniversalIntent.FIND,UniversalIntent.FOCUS,UniversalIntent.CLICK,UniversalIntent.SELECT,UniversalIntent.READ})

@dataclass(frozen=True)
class Resolution:
    adapter: AdapterDeclaration
    capability: Capability

class CapabilityResolver:
    def __init__(self,allowlist): self.allowlist=allowlist
    def select_adapter(self,application,*,window_class=None):
        if self.allowlist.get(application,{}).get("allowed") is not True: raise SafeError("Application is not approved")
        matches=[a for a in ADAPTERS if application in a.applications]
        if len(matches)>1: raise SafeError("Application adapter is ambiguous")
        if matches:
            adapter=matches[0]
            if adapter.window_classes and window_class not in {None,*adapter.window_classes}: raise SafeError("Application window class is not supported")
            return adapter
        return AdapterDeclaration("generic_uia",frozenset({application}),GENERIC_CAPABILITIES)
    def resolve(self,application,intent,*,window_class=None):
        try: intent=UniversalIntent(intent)
        except (ValueError,TypeError): raise SafeError("Unknown universal intent") from None
        adapter=self.select_adapter(application,window_class=window_class)
        if intent not in adapter.capabilities: raise SafeError("Application adapter does not support this capability")
        return Resolution(adapter,CONTRACTS[intent])

def resolve_destination(value,*,application=None):
    if not isinstance(value,str) or not value.strip(): raise SafeError("Navigation destination is required")
    value=value.strip()
    if value.casefold()=="downloads": return DestinationKind.APPROVED_FOLDER,"downloads"
    if value.casefold()=="view":
        if application!="explorer": raise SafeError("Application section is ambiguous")
        return DestinationKind.APPLICATION_SECTION,"view"
    normalized=normalize_navigation_url(value)
    if application not in {None,"chrome"}: raise SafeError("HTTPS navigation requires the supported Chrome adapter")
    if urlsplit(normalized).scheme!="https": raise SafeError("Unsupported navigation destination")
    return DestinationKind.HTTPS_URL,normalized
