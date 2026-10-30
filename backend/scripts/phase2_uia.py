"""Developer-only bounded, read-only foreground diagnostics. Never returns labels/values."""
import multiprocessing
import time


def state_summary(state):
    from vision.element_finder import safe_editor
    from config.app_catalog import BROWSERS
    app = state.current_application
    return dict(available=state.available, app=app if app in BROWSERS | {'notepad', 'vscode'} else ('other_approved' if app else None),
        handle=state.active_window.handle if state.active_window else None,
        approved_windows=len(state.open_windows), elements=len(state.elements), complete=state.elements_complete,
        editor_candidates=sum(safe_editor(e) for e in state.elements), focused_field=bool(state.focused_id),
        url_observed=state.url_digest is not None, text_match=state.text_matches)


def _probe(connection):
    from utils.worker_lifecycle import configure_worker, send_reply
    configure_worker()
    from utils.com import configure_com
    configure_com()
    report = {'status': 'BLOCKED', 'reason': 'PROBE_UNAVAILABLE'}
    try:
        import ctypes
        from ctypes import wintypes
        import json
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        from config.settings import ROOT
        from config.app_catalog import discover_apps, input_supported
        from tools.app_tools import Apps
        from tools.window_tools import Windows
        from tools.uia_ownership import provider_owned
        from vision.observer import collect
        user = ctypes.WinDLL('user32')
        user.GetForegroundWindow.restype = wintypes.HWND
        handle = user.GetForegroundWindow()
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        report.update(handle=handle, process_id=pid.value, foreground_present=bool(handle))
        send_reply(connection, dict(report))
        windows = Windows(Apps(discover_apps(json.loads((ROOT/'config/apps.json').read_text()))))
        active = windows.active()
        report['provider_available'] = True
        try:
            app = windows.app_of(active)
        except Exception:
            report.update(reason='FOREGROUND_UNAPPROVED', approved=False)
            send_reply(connection, report)
            return
        report.update(approved=True, app=app if app in {'chrome','edge','notepad','vscode','firefox'} else 'other_approved',
                      input_supported=input_supported(windows, active))
        send_reply(connection, dict(report))
        state = collect(windows)
        report['observation'] = state_summary(state)
        report['same_foreground'] = bool(state.active_window and state.active_window.handle == handle)
        patterns = dict(value=0, text=0, scroll=0)
        pending, visited, owned, rejected = [(active, 0)], 0, 0, 0
        # Pattern availability only; no field values, names or page contents persisted.
        while pending and visited < 200:
            wrapper, depth = pending.pop(0)
            visited += 1
            info = wrapper.element_info
            if not provider_owned(windows, active, info):
                rejected += 1
                continue
            owned += 1
            if info.visible and not bool(info.element.CurrentIsPassword):
                for name in patterns:
                    try:
                        if getattr(wrapper, 'iface_' + name) is not None:
                            patterns[name] += 1
                    except Exception:
                        pass
            if depth < 12:
                pending.extend((child, depth+1) for child in wrapper.children()[:max(0, 200-visited-len(pending))])
        report.update(pattern_counts=patterns, ownership_accepted=owned, ownership_rejected=rejected,
                      traversal_bounded=True)
        if not report['same_foreground']:
            reason = 'FOREGROUND_CHANGED_OR_NOT_OBSERVED'
        elif not report['input_supported']:
            reason = 'INPUT_UNSUPPORTED'
        elif not state.elements_complete:
            reason = 'INCOMPLETE_TREE'
        elif not state.elements:
            reason = 'EMPTY_TREE'
        else:
            reason = 'UIA_OBSERVED'
        report.update(status='OBSERVED' if reason == 'UIA_OBSERVED' else 'UNCERTAIN', reason=reason)
        send_reply(connection, report)
    except Exception:
        report.update(status='BLOCKED', reason='PROVIDER_OR_OBSERVATION_ERROR')
        send_reply(connection, report)
    finally:
        connection.close()


def probe(timeout=8):
    ctx = multiprocessing.get_context('spawn')
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_probe, args=(writer,), daemon=True)
    report = {'status': 'BLOCKED', 'reason': 'PROBE_UNAVAILABLE'}
    start = time.monotonic()
    try:
        process.start()
        writer.close()
        while time.monotonic()-start < timeout:
            try:
                if reader.poll(.05):
                    report = reader.recv()
                elif not process.is_alive():
                    break
            except (EOFError, OSError):
                break
        else:
            report = dict(report, status='UNCERTAIN', reason='PROBE_TIMEOUT')
    finally:
        if process.pid:
            if process.is_alive(): process.terminate()
            process.join(.5)
            if process.is_alive(): process.kill(); process.join(.5)
            if not process.is_alive(): process.close()
        reader.close(); writer.close()
    return dict(report, seconds=round(time.monotonic()-start, 3))
