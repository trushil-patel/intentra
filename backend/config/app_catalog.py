"""Local application identities shared by parsing and native execution."""
import os
import re
from pathlib import Path

BROWSERS = frozenset({'chrome', 'edge', 'firefox'})
BUILTIN_ALIASES = {'chrome': 'chrome', 'google chrome': 'chrome',
    'edge': 'edge', 'microsoft edge': 'edge', 'firefox': 'firefox', 'mozilla firefox': 'firefox',
    'file explorer': 'explorer', 'explorer': 'explorer', 'windows explorer': 'explorer',
    'notepad': 'notepad', 'vs code': 'vscode', 'visual studio code': 'vscode', 'vscode': 'vscode'}
ALIASES = dict(BUILTIN_ALIASES)
NO_INPUT_EXECUTABLES = frozenset({'cmd.exe', 'powershell.exe', 'pwsh.exe', 'powershell_ise.exe',
    'wt.exe', 'windowsterminal.exe', 'conhost.exe', 'python.exe', 'pythonw.exe', 'node.exe',
    'wscript.exe', 'cscript.exe', 'regedit.exe', 'mmc.exe', 'control.exe', 'systemsettings.exe',
    'msconfig.exe', 'recoverydrive.exe', 'winget.exe', 'bash.exe', 'wsl.exe'})


def input_supported(windows, window):
    app = windows.app_of(window)
    if app == 'vscode':
        return False
    if app in BROWSERS or app == 'notepad':
        return True
    try:
        return Path(windows.executable(window.process_id())).name.casefold() not in NO_INPUT_EXECUTABLES
    except Exception:
        return False



def configure_aliases(catalog):
    ALIASES.clear()
    ALIASES.update(BUILTIN_ALIASES)
    ambiguous = set()
    for app, entry in catalog.items():
        if entry.get('allowed') is not True:
            continue
        for name in [app, *entry.get('aliases', [])]:
            name = ' '.join(name.casefold().split())
            if name in ALIASES and ALIASES[name] != app:
                ambiguous.add(name)
            else:
                ALIASES[name] = app
    for name in ambiguous:
        ALIASES.pop(name, None)


def executable_path(value):
    """Only explicit local EXEs; never command strings, scripts or associations."""
    if not isinstance(value, str) or not value or value.startswith(('\\\\', '//')):
        return None
    path = Path(os.path.expandvars(value))
    if not path.is_absolute() or path.suffix.casefold() != '.exe' or not path.is_file():
        return None
    return path.resolve()


def installed_entries():
    """Read Start Menu shortcuts and App Paths; do not execute shortcut arguments."""
    if os.name != 'nt':
        return
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\App Paths', 0,
                                    winreg.KEY_READ | view) as root:
                    for i in range(min(winreg.QueryInfoKey(root)[0], 1000)):
                        name = winreg.EnumKey(root, i)
                        try:
                            with winreg.OpenKey(root, name) as key:
                                value, _ = winreg.QueryValueEx(key, '')
                            yield Path(name).stem, value.strip('"')
                        except OSError:
                            continue
            except OSError:
                continue
    try:
        import win32com.client
        shell = win32com.client.Dispatch('WScript.Shell')
        for variable in ('APPDATA', 'ProgramData'):
            base = os.environ.get(variable)
            if not base:
                continue
            root = Path(base) / 'Microsoft/Windows/Start Menu/Programs'
            for i, link in enumerate(root.rglob('*.lnk')):
                if i >= 1000:
                    break
                try:
                    shortcut = shell.CreateShortcut(str(link))
                    if not shortcut.Arguments:
                        yield link.stem, shortcut.TargetPath
                except Exception:
                    continue
    except Exception:
        # Some Windows sessions lack shortcut COM access; registry discovery remains usable.
        return


def discover_apps(configured, entries=None):
    """Snapshot installed identities once at startup. Explicit configuration wins."""
    catalog = {key: dict(value) for key, value in configured.items()}
    from tools.app_tools import Apps
    known = {}
    for app, entry in configured.items():
        try:
            candidates = Apps({app: dict(entry, allowed=True)}).candidates(app)
            for path in candidates:
                known[str(path.resolve()).casefold()] = app
        except Exception:
            continue
    for name, value in installed_entries() if entries is None else entries:
        path = executable_path(value)
        if path is None or not re.fullmatch(r'[\w .()+-]{1,100}', name):
            continue
        # Do not expose uninstallers/installers as ordinary applications.
        if re.search(r'(?i)un[- ]?install|setup|installer|update|repair', name + ' ' + path.stem):
            continue
        identity = str(path).casefold()
        app = known.get(identity)
        if app is None:
            import hashlib
            app = 'app_' + hashlib.sha256(identity.encode()).hexdigest()[:12]
            known[identity] = app
            catalog[app] = {'allowed': False, 'discovered': True, 'executable': str(path), 'aliases': []}
        if catalog[app].get('allowed') is True:
            aliases = list(catalog[app].get('aliases', []))
            if name.casefold() not in aliases:
                aliases.append(name.casefold())
            catalog[app]['aliases'] = aliases
    return catalog
