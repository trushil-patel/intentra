"""Exact executable identity and bounded work for one metadata observation."""
from pathlib import Path
from unittest.mock import Mock
import pytest
from tools.app_tools import Apps
from tools.window_metadata import collect_metadata


@pytest.fixture
def desktop(monkeypatch):
    user=Mock()
    processes={}
    user.GetForegroundWindow.return_value=1
    user.GetAncestor.side_effect=lambda handle, flag: handle
    user.IsWindowVisible.return_value=True
    user.IsIconic.return_value=False
    user.IsZoomed.return_value=False
    def pid(handle,pointer): pointer._obj.value=handle
    user.GetWindowThreadProcessId.side_effect=pid
    def enum(callback,param):
        for handle in processes:
            if not callback(handle,param): return False
        return True
    user.EnumWindows.side_effect=enum
    monkeypatch.setattr('tools.window_metadata.ctypes.WinDLL',lambda *a,**kw:user)
    monkeypatch.setattr('tools.window_metadata.Windows.executable',lambda self,pid:processes[pid])
    return processes


def executable(tmp_path,name):
    p=tmp_path/name; p.parent.mkdir(parents=True,exist_ok=True); p.touch()
    return str(p)


def test_metadata_resolves_catalog_once_per_observation(tmp_path,desktop,monkeypatch):
    first=executable(tmp_path,'first.exe'); target=executable(tmp_path,'target.exe')
    catalog={'first':{'allowed':True,'executable':first},'target':{'allowed':True,'executable':target}}
    desktop.update({i:target for i in range(1,6)})
    original=Apps.candidates; calls=[]
    def candidates(self,app): calls.append(app); return original(self,app)
    monkeypatch.setattr(Apps,'candidates',candidates)
    state=collect_metadata(catalog)
    assert state.available and len(state.open_windows)==5
    assert state.current_application=='target'
    assert calls.count('first')==1 and calls.count('target')==1


def test_metadata_refreshes_configured_path_on_each_observation(tmp_path,desktop):
    first=executable(tmp_path,'first.exe'); second=executable(tmp_path,'second.exe')
    catalog={'editor':{'allowed':True,'executable':first}}
    desktop[1]=first
    assert collect_metadata(catalog).current_application=='editor'
    catalog['editor']['executable']=second
    assert collect_metadata(catalog).active_window is None
    desktop[1]=second
    assert collect_metadata(catalog).current_application=='editor'


def test_metadata_rejects_same_basename_from_other_directory(tmp_path,desktop):
    approved=executable(tmp_path,'approved/editor.exe'); foreign=executable(tmp_path,'foreign/editor.exe')
    desktop[1]=foreign
    assert collect_metadata({'editor':{'allowed':True,'executable':approved}}).open_windows==()


def test_metadata_disabled_app_cannot_be_observed_as_approved(tmp_path,desktop):
    path=executable(tmp_path,'editor.exe'); desktop[1]=path
    assert collect_metadata({'editor':{'allowed':False,'executable':path}}).open_windows==()


def test_metadata_retains_first_configured_identity(tmp_path,desktop):
    path=executable(tmp_path,'editor.exe'); desktop[1]=path
    catalog={key:{'allowed':True,'executable':path} for key in ('first','second')}
    assert collect_metadata(catalog).current_application=='first'


def test_metadata_retains_packaged_notepad_rule(tmp_path,desktop,monkeypatch):
    monkeypatch.setenv('ProgramFiles',str(tmp_path))
    desktop[1]=executable(tmp_path,'WindowsApps/Microsoft.WindowsNotepad_test/Notepad/Notepad.exe')
    assert collect_metadata({'notepad':{'allowed':True}}).current_application=='notepad'
    assert collect_metadata({'notepad':{'allowed':False}}).active_window is None
