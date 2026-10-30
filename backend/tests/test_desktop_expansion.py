from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest
from config.app_catalog import ALIASES, configure_aliases, discover_apps
from agent.planner import deterministic_v2
from agent.schemas import Action
from tools.app_tools import Apps
from tools.browser_tools import Browser
from safety.validator import Validator
from tools.registry import Registry
from utils.helpers import SafeError
from test_stage_c_scroll import page, node


@pytest.fixture(autouse=True)
def restore_aliases():
    old = dict(ALIASES)
    yield
    ALIASES.clear()
    ALIASES.update(old)


@pytest.mark.parametrize('name,app', [('Microsoft Edge','edge'), ('Firefox','firefox'), ('Google Chrome','chrome')])
def test_browser_command_and_composition(name, app):
    plan = deterministic_v2('Open ' + name + ' and go to example.org and type "hello and press Enter"')
    assert [a.tool for a in plan.actions] == ['open_app','open_url','focus_text_field','type_text']
    assert plan.actions[0].arguments == {'app_name': app}
    assert plan.actions[-1].arguments['text'] == 'hello and press Enter'


def test_discovered_app_requires_explicit_approval(tmp_path):
    exe = tmp_path / 'writer.exe'; exe.touch()
    catalog = discover_apps({}, [('My Writer', str(exe))])
    configure_aliases(catalog)
    app = next(iter(catalog))
    assert catalog[app]['allowed'] is False
    assert catalog[app]['discovered'] is True
    assert deterministic_v2('Open My Writer and type hello') is None
    with pytest.raises(SafeError):
        Apps(catalog).open_app(app)


def test_discovery_collision_and_disabled_identity(tmp_path):
    first = tmp_path / 'one.exe'; first.touch()
    second = tmp_path / 'two.exe'; second.touch()
    catalog = discover_apps({}, [('Writer', str(first)), ('Writer', str(second))])
    configure_aliases(catalog)
    assert 'writer' not in ALIASES
    disabled = {'writer': {'allowed': False, 'executable': str(first)}}
    # Explicit disabled paths must not be rediscovered under another identity.
    catalog = discover_apps(disabled, [('Writer', str(first))])
    assert not any(e['allowed'] for e in catalog.values())


def test_discovery_ignores_scripts_arguments_and_uninstaller(tmp_path):
    exe = tmp_path / 'safe.exe'; exe.touch()
    script = tmp_path / 'unsafe.cmd'; script.touch()
    assert discover_apps({}, [('script', str(script)), ('args', str(exe)+' --run'),
                              ('Un-install app', str(exe))]) == {}


@pytest.mark.parametrize('browser', ['chrome','edge','firefox'])
def test_navigation_uses_foreground_browser(browser):
    apps = Mock(); apps.resolve.return_value = Path('browser.exe')
    guard = Mock(); guard.windows.app_of.return_value = browser
    with patch('tools.browser_tools.subprocess.Popen') as launch:
        result = Browser(apps, guard=guard).open_url('example.org')
    apps.resolve.assert_called_once_with(browser)
    assert result['browser'] == browser
    assert launch.call_args.args[0][1] == 'https://example.org'


@pytest.mark.parametrize('browser', ['edge','firefox'])
def test_scroll_other_browsers_with_renderer_binding(page, browser):
    from tools.scroll_target import scroll
    from safety.emergency_stop import EmergencyStop
    page.windows.app_of.return_value = browser
    page.windows.apps.identify.return_value = browser
    action = Action('scroll', {'direction':'down','amount':1})
    target = page.guard.snapshot(action)
    assert target.app == browser and target.owner_process == 22
    page.guard.arm(action, target)
    assert scroll(page.windows, page.guard, EmergencyStop(), 'down', 1)['after_percent'] == 51


def test_generic_website_and_literal_payload():
    plan = deterministic_v2('Open example.org and type "open Firefox" and press Enter')
    assert [a.tool for a in plan.actions] == ['open_url','focus_text_field','type_text','press_key']
    assert plan.actions[2].arguments['text'] == 'open Firefox'
    assert deterministic_v2('Type open example.org').actions[0].tool == 'type_text'


def test_desktop_scroll_unique_surface(page):
    from tools.scroll_target import scroll
    from safety.emergency_stop import EmergencyStop
    page.windows.app_of.return_value = 'notepad'
    page.doc.element_info.process_id = 11
    page.root.iface_scroll = None
    page.address.iface_scroll = None
    page.root.children.return_value = [page.doc]
    action = Action('scroll', {'direction':'down','amount':1})
    target = page.guard.snapshot(action)
    assert target.state_id is None and target.app == 'notepad'
    page.guard.arm(action, target)
    assert scroll(page.windows, page.guard, EmergencyStop(), 'down', 1)['after_percent'] == 51


def test_console_input_capability_is_not_granted():
    from config.app_catalog import input_supported
    windows = Mock(); windows.app_of.return_value = 'app_console'
    windows.executable.return_value = 'C:/Windows/System32/cmd.exe'
    assert not input_supported(windows, Mock())


def test_browser_renderer_must_be_in_foreground_tree(page):
    from tools.uia_ownership import focused_descendant
    assert focused_descendant(page.windows, page.root, page.doc.element_info)
    impostor = node('Edit', 'Message', 22, (99,)).element_info
    assert not focused_descendant(page.windows, page.root, impostor)
    page.windows.executable.side_effect = lambda pid: 'browser.exe' if pid == 11 else 'foreign.exe'
    assert not focused_descendant(page.windows, page.root, page.doc.element_info)


@pytest.mark.parametrize('browser', ['edge', 'firefox'])
def test_navigation_and_scroll_verified_for_other_browsers(browser):
    from agent.computer_state import ComputerState, WindowState
    from agent.router import Result
    from agent.verifier import Verifier, VerificationStatus
    from vision.observer import url_digest
    window = WindowState(7, browser, False, False, 'Example')
    state = ComputerState(active_window=window, open_windows=(window,), available=True,
                          url_digest=url_digest('https://example.org'))
    action = Action('open_url', {'url':'https://example.org'})
    assert Verifier().verify(action, Result('open_url','SUCCESS',{}), state, state).status == VerificationStatus.VERIFIED
    action = Action('scroll', {'direction':'down','amount':1})
    detail = dict(status='scroll_observed', handle=7, runtime_id=(2,), url_digest=state.url_digest,
                  before_percent=20, after_percent=25)
    assert Verifier().verify(action, Result('scroll','SUCCESS',detail), state, state).status == VerificationStatus.VERIFIED
