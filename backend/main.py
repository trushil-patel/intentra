"""Intentra prototype terminal entry point. Run without administrator privileges."""
import sys
if __name__ == "__mp_main__":
    from utils.worker_lifecycle import configure_worker
    configure_worker()
from utils.com import configure_com
configure_com()
import argparse
from dataclasses import replace
from intentra_version import __version__
import importlib
import json
import os
from config.settings import ROOT, Settings
from utils.helpers import SafeError


def initialize_com():
    if os.name == "nt":
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)


def doctor():
    initialize_com()
    import sys
    print("Python:", sys.version.split()[0], "Windows:", os.name == "nt")
    good = os.name == "nt" and sys.version_info >= (3, 12)
    for module in ("faster_whisper", "sounddevice", "numpy", "scipy", "pyautogui", "pywinauto", "pyttsx3", "dotenv", "PIL", "requests"):
        try:
            importlib.import_module(module)
            print(module + ": available")
        except Exception:
            good = False
            print(module + ": unavailable")
    return 0 if good else 1


def main(argv=None, desktop=None):
    import multiprocessing
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="Intentra: constrained local voice control")
    parser.add_argument("--voice-mode", choices=("push_to_talk", "hands_free"),
                        help="capture mode; overrides VOICE_MODE (default: push_to_talk)")
    parser.add_argument("--list-apps", action="store_true", help="list discovered application voice names without launching")
    parser.add_argument("--startup-check", action="store_true", help="load model, register keys and cleanly exit without recording")
    parser.add_argument("--level", choices=("metadata", "uia"), default="uia", help="inspection depth; no automatic OCR/vision")
    parser.add_argument("--ground", metavar="LABEL", help="inspect and ground a label without clicking")
    parser.add_argument("--inspect", action="store_true", help="inspect local UIA metadata without executing actions")
    parser.add_argument("--doctor", action="store_true", help="check Python and dependency imports")
    parser.add_argument("--download-model", action="store_true", help="explicitly download the configured Whisper model")
    parser.add_argument("--compare-voice", metavar="NEW_JSONL_FILE", help="opt-in ten-phrase transcript/timing capture; requires --transcribe-only; no raw audio")
    parser.add_argument("--transcribe-only", action="store_true", help="show redacted recognized speech without executing desktop actions")
    parser.add_argument("--command", metavar="COMMAND", help="execute one goal with normal safety and confirmation, then exit")
    parser.add_argument("--text", action="store_true", help="use typed commands for diagnostics")
    parser.add_argument("--plan", metavar="COMMAND", help="validate and display a plan without executing it")
    args = parser.parse_args(argv)
    if args.list_apps and (args.transcribe_only or args.compare_voice or args.command or args.plan or args.inspect or args.ground or args.download_model or args.doctor or args.startup_check or args.text):
        parser.error('--list-apps is a standalone discovery command')
    if args.transcribe_only and (args.command or args.plan or args.inspect or args.ground or args.download_model or args.doctor):
        parser.error('--transcribe-only cannot be combined with execution, planning, inspection or setup modes')
    if args.compare_voice and (not args.transcribe_only or args.text or args.command or args.plan or args.inspect or args.ground or args.startup_check or args.download_model or args.doctor):
        parser.error('--compare-voice requires only --transcribe-only voice mode')
    if args.doctor: return doctor()
    from config.app_catalog import discover_apps, configure_aliases
    configured = json.loads((ROOT / "config/apps.json").read_text(encoding="utf-8"))
    initialize_com()
    allowlist = discover_apps(configured)
    configure_aliases(allowlist)
    if args.list_apps:
        for app, entry in sorted(allowlist.items()):
            if entry.get('allowed') is True:
                print(app + ': ' + ', '.join(entry.get('aliases', [app])))
        return 0
    settings = Settings.load()
    if args.voice_mode:
        settings = replace(settings, voice_mode=args.voice_mode)
    initialize_com()
    from audio.speech_to_text import SpeechToText
    transcriber = SpeechToText(settings=settings)
    if args.transcribe_only or settings.voice_diagnostics: transcriber.print_configuration()
    if args.download_model:
        transcriber.load(allow_download=True)
        print("Whisper model ready locally")
        return 0
    if os.name != "nt" and not args.plan:
        raise SafeError("Desktop execution requires Windows 10/11")
    from safety.emergency_stop import EmergencyStop
    from safety.validator import Validator
    from safety.permissions import Permissions
    from safety.rate_limiter import RateLimiter
    from safety.confirmation import ConfirmationManager
    from agent.router import Router
    from agent.brain import Brain
    from agent.planning_worker import BoundedOllamaProvider
    from utils.logger import Audit
    stop = EmergencyStop()
    from audio.stt_worker import BoundedSpeechToText
    transcriber = BoundedSpeechToText(settings, stop)
    if args.inspect or args.ground:
        from vision.observer import Observer
        from vision.inspect import inspection_report
        import time
        observer = Observer(allowlist, stop)
        started = time.perf_counter()
        state = observer.observe(level=0 if args.level == 'metadata' else 1)
        report = inspection_report(state)
        report.update(status=observer.last_status, observation_ms=round((time.perf_counter()-started)*1000, 2))
        if args.ground:
            from vision.grounding import ground
            from vision.semantic import ScreenState
            result = ground(ScreenState.from_computer(state), args.ground)
            report['grounding'] = {'candidate_ids': [r.id for r in result.candidates],
                'selected': result.selected.id if result.selected else None,
                'confidence': result.confidence, 'ambiguous': len(result.candidates) > 1,
                'reason': result.reason}
        print(json.dumps(report, indent=2))
        return 0 if observer.last_status == 'SUCCESS' else 1
    from tools.native import NativeBridge
    native = NativeBridge(allowlist, stop, settings)
    registry, windows, guard = native, native, native
    audit = Audit(ROOT / "logs/audit.log")
    provider = None
    runtime = None
    comparison = None
    try:
        provider = BoundedOllamaProvider(settings, stop)
        router = Router(registry, Validator(allowlist), Permissions(ROOT / "config/permissions.json"),
                        RateLimiter(settings.max_actions), stop, ConfirmationManager(settings.confirmation_seconds), audit, guard, windows)
        from vision.observer import Observer
        from agent.executor import Executor
        from vision.engine import ObservationEngine
        executor = Executor(router, ObservationEngine(Observer(allowlist, stop)))
        brain = Brain(provider, router, stop, executor=executor)
        if args.plan:
            from agent.planner import deterministic_v2, normalized, STOP_WORDS
            if normalized(args.plan) in STOP_WORDS:
                stop.stop()
                print(json.dumps({"status": "CANCELLED", "actions": []}))
                return 0
            from agent.schemas import Plan
            from safety.validator import safe_text
            if not safe_text(args.plan): raise SafeError("Sensitive or dangerous request blocked")
            plan = deterministic_v2(args.plan)
            if plan is None:
                from agent.command_classifier import classify, CommandKind
                if classify(args.plan) == CommandKind.REPEAT:
                    raise SafeError("I didn't understand that command. Please repeat.")
                plan = Plan.parse(provider.generate_plan(args.plan, {}))
                from agent.task_scope import validate_model_scope
                validate_model_scope(args.plan, plan, {})
            from agent.task_scope import TaskScope
            router.task_scope = TaskScope.from_user(args.plan)
            actions = router.preflight(plan)
            print(json.dumps({"actions": [{"tool": a.tool, "arguments": a.arguments} for a in actions]}, indent=2))
            return 0
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        from agent.runtime import Runtime
        runtime = Runtime(settings, brain, transcriber, stop, transcribe_only=args.transcribe_only)
        if desktop is not None: desktop.attach(runtime)
        if args.compare_voice:
            from audio.benchmark import ComparisonSession
            comparison = ComparisonSession(args.compare_voice, settings, runtime.closed.set)
            runtime.diagnostic_sink = comparison.accept
            comparison.prompt()
        print(f"========== INTENTRA MVP v{__version__} ==========")
        if settings.debug_recording: print("[PRIVACY] DEBUG_RECORDING enabled: WAV files will persist locally")
        if args.transcribe_only: print("[DIAGNOSTIC] Recognition only: recognized text is shown locally; desktop actions are disabled.")
        if args.command:
            results = runtime.run_once(args.command)
            return 0 if results and results[-1].status == 'SUCCESS' else 1
        if args.text: runtime.run_text()
        else: runtime.run_voice(check_only=args.startup_check)
        return 0
    finally:
        stop.stop()
        if runtime: runtime.close()
        if comparison: comparison.close()
        if "executor" in locals(): executor.memory.clear()
        native.clear_session()
        if provider: provider.close()
        audit.close()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nStopped")
    except SafeError as exc:
        print("[BLOCKED] " + str(exc))
        raise SystemExit(1)
    except Exception:
        print("[ERROR] Startup failed safely. Run python main.py --doctor and check configuration.")
        raise SystemExit(1)
