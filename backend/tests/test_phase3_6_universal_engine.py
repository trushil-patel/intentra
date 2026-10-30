"""Phase 3.6 typed universal capability and release-safety regressions."""
import importlib
import inspect
from pathlib import Path
from unittest.mock import patch
import pytest

from adapters.capabilities import (
    ADAPTERS, CONTRACTS, CapabilityResolver, DestinationKind,
    UniversalIntent, resolve_destination,
)
from agent.planner import deterministic_v2
from safety.policy import Risk
from utils.helpers import SafeError


ALLOW={"chrome":{"allowed":True},"explorer":{"allowed":True},
       "notepad":{"allowed":True},"edge":{"allowed":True},
       "calculator":{"allowed":False},"app_writer":{"allowed":True}}


@pytest.mark.parametrize("intent", list(UniversalIntent))
def test_every_universal_action_has_complete_contract(intent):
    contract=CONTRACTS[intent]
    assert contract.intent is intent
    assert contract.tools and contract.preconditions and contract.verification and contract.failures
    assert contract.risk in set(Risk)
    assert type(contract.state_changing) is bool


@pytest.mark.parametrize("app,name",[
    ("chrome","chromium_chrome"),("explorer","file_explorer"),
    ("notepad","notepad"),("edge","generic_uia"),("app_writer","generic_uia"),
])
def test_exact_adapter_selection(app,name):
    assert CapabilityResolver(ALLOW).select_adapter(app).name==name


@pytest.mark.parametrize("app",["missing","calculator","",None])
def test_unknown_or_disabled_application_rejected(app):
    with pytest.raises(SafeError): CapabilityResolver(ALLOW).select_adapter(app)


@pytest.mark.parametrize("app,intent,supported",[
 ("chrome","NAVIGATE",True),("chrome","WAIT",True),("chrome","TYPE",False),
 ("explorer","NAVIGATE",True),("explorer","SELECT",True),("explorer","WAIT",False),
 ("notepad","TYPE",True),("notepad","WAIT",False),("notepad","NAVIGATE",False),
 ("edge","FIND",True),("edge","NAVIGATE",False),("app_writer","READ",True),
 ("app_writer","TYPE",False),
])
def test_capability_resolution(app,intent,supported):
    if supported:
        value=CapabilityResolver(ALLOW).resolve(app,intent)
        assert value.capability.intent==UniversalIntent(intent)
    else:
        with pytest.raises(SafeError): CapabilityResolver(ALLOW).resolve(app,intent)


@pytest.mark.parametrize("window_class,ok",[
    ("CabinetWClass",True),(None,True),("Progman",False),("ApplicationFrameWindow",False),
])
def test_explorer_window_class_contract(window_class,ok):
    if ok:
        assert CapabilityResolver(ALLOW).select_adapter("explorer",window_class=window_class).name=="file_explorer"
    else:
        with pytest.raises(SafeError): CapabilityResolver(ALLOW).select_adapter("explorer",window_class=window_class)


@pytest.mark.parametrize("value,app,kind,normalized",[
 ("github.com","chrome",DestinationKind.HTTPS_URL,"https://github.com"),
 ("https://github.com/","chrome",DestinationKind.HTTPS_URL,"https://github.com/"),
 ("downloads",None,DestinationKind.APPROVED_FOLDER,"downloads"),
 ("Downloads","explorer",DestinationKind.APPROVED_FOLDER,"downloads"),
 ("view","explorer",DestinationKind.APPLICATION_SECTION,"view"),
])
def test_destination_resolution(value,app,kind,normalized):
    assert resolve_destination(value,application=app)==(kind,normalized)


@pytest.mark.parametrize("value,app",[
 ("http://github.com","chrome"),("https://127.0.0.1","chrome"),
 ("javascript:alert(1)","chrome"),("view","chrome"),
 ("github.com","notepad"),("",None),
])
def test_unsafe_or_ambiguous_destination_rejected(value,app):
    with pytest.raises(SafeError): resolve_destination(value,application=app)


@pytest.mark.parametrize("command,tools",[
 ("Open Chrome.",["open_app"]),
 ("Open Notepad.",["open_app"]),
 ("Open File Explorer.",["open_app"]),
 ("Open Downloads.",["navigate_explorer_folder"]),
 ("Navigate to github.com.",["open_url"]),
 ("Find the Settings button.",["find_element"]),
 ("Click the Settings button.",["find_element","click_element"]),
 ("Focus the text editor.",["find_element","focus_element"]),
 ("Select the View tab.",["find_element","interact_element"]),
 ("Show available buttons.",["list_elements"]),
 ("Open Chrome and focus the address bar.",["open_app","find_element","focus_element"]),
 ("Open Chrome and go to github.com.",["open_app","find_element","focus_element","type_url","navigate_url"]),
 ("Open File Explorer and select View.",["open_app","find_element","interact_element"]),
 ("Open Notepad and write Hello.",["open_app","find_element","focus_element","edit_notepad"]),
])
def test_universal_user_commands_reuse_existing_tools(command,tools):
    value=deterministic_v2(command)
    assert value is not None
    assert [a.tool for a in value.actions]==tools


@pytest.mark.parametrize("command",[
 "Open Calculator.","Open unknown program.","Go to View.",
 "Click Settings or Preferences.","Type into the password field.",
])
def test_unsupported_or_ambiguous_commands_do_not_gain_authority(command):
    value=deterministic_v2(command)
    assert value is None or all(a.tool not in {"edit_notepad","navigate_url"} for a in value.actions)


def test_click_contract_never_claims_generic_success():
    contract=CONTRACTS[UniversalIntent.CLICK]
    assert contract.verification=="operation-specific expected effect"
    assert "EXPECTED_EFFECT_UNAVAILABLE" in contract.failures


def test_read_contract_is_structural_and_private():
    contract=CONTRACTS[UniversalIntent.READ]
    assert "structural" in contract.verification
    assert "SENSITIVE_CONTENT" in contract.failures


def test_type_contract_is_application_specific():
    contract=CONTRACTS[UniversalIntent.TYPE]
    assert "application-specific contract" in contract.preconditions
    assert set(contract.tools)=={"edit_notepad","type_url"}


def test_adapters_do_not_execute_or_import_native_workers():
    source=inspect.getsource(importlib.import_module("adapters.capabilities"))
    assert "NativeBridge" not in source and "pyautogui" not in source
    assert "subprocess" not in source


def test_no_edge_specialization_is_claimed():
    assert all("edge" not in adapter.applications for adapter in ADAPTERS)
    assert CapabilityResolver(ALLOW).select_adapter("edge").name=="generic_uia"


def test_frozen_multiprocessing_support_is_registered():
    import main
    assert "freeze_support()" in inspect.getsource(main.main)


def test_model_location_has_explicit_portable_override():
    source=Path("audio/speech_to_text.py").read_text()
    assert "INTENTRA_MODEL_DIR" in source
    assert "Parakeet model files are missing" in source


def test_no_content_based_instructions_in_capability_resolver():
    source=Path("adapters/capabilities.py").read_text()
    assert "planner" not in source.casefold()
    assert "execute(" not in source
    assert "ComputerState" not in source and "Element(" not in source


@pytest.mark.parametrize("intent",[
 UniversalIntent.FOCUS,UniversalIntent.CLICK,UniversalIntent.SELECT,
 UniversalIntent.TYPE,UniversalIntent.NAVIGATE,
])
def test_state_changing_contracts_require_risk_classification(intent):
    contract=CONTRACTS[intent]
    assert contract.state_changing is True
    assert contract.risk==Risk.MEDIUM


@pytest.mark.parametrize("intent",[
 UniversalIntent.FIND,UniversalIntent.READ,UniversalIntent.WAIT,
])
def test_read_only_contracts_are_low_risk(intent):
    contract=CONTRACTS[intent]
    assert contract.state_changing is False
    assert contract.risk==Risk.LOW


def test_discovered_executables_do_not_expand_allowlist(tmp_path):
    from config.app_catalog import discover_apps
    executable=tmp_path/"calculator.exe"
    executable.touch()
    catalog=discover_apps({},[("Calculator",str(executable))])
    entry=next(iter(catalog.values()))
    assert entry["allowed"] is False and entry["discovered"] is True
