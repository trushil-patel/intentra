from config.app_catalog import BROWSERS
from utils.performance import timed
from dataclasses import dataclass
from enum import StrEnum
from vision.observer import url_digest


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    evidence: str


class Verifier:
    @timed("verification")
    def verify(self, action, result, before, after):
        if result.status != "SUCCESS":
            return VerificationResult(VerificationStatus.FAILED, "Execution did not succeed")
        tool, args = action.tool, action.arguments
        observed = None
        reason = None
        same_field = (before.available and after.available and bool(before.focused_id)
                      and before.focused_id == after.focused_id
                      and before.active_window is not None and after.active_window is not None
                      and before.active_window.handle == after.active_window.handle
                      and before.current_application == after.current_application
                      and (before.url_digest is None or before.url_digest == after.url_digest))
        if tool == 'summarize_page':
            from vision.screen_reader import Observation
            observed = isinstance(result.detail, Observation) and result.detail.source == 'extractive_summary'
        elif tool == 'find_element':
            from vision.semantic import ElementReference, ScreenState
            reference = result.detail
            screen = ScreenState.from_computer(after)
            matches = ([candidate for candidate in screen.elements
                        if isinstance(reference, ElementReference)
                        and candidate.element.runtime_id == reference.element.runtime_id
                        and candidate.element.name == reference.element.name
                        and candidate.element.type == reference.element.type]
                       if (after.available
                           and (after.elements_complete or after.target_verification_complete)
                           and after.active_window and not screen.has_modal) else [])
            observed = (len(matches) == 1 and matches[0].handle == reference.handle
                        and matches[0].app == reference.app
                        and after.active_window.handle == reference.handle
                        and after.current_application == reference.app) if isinstance(reference, ElementReference) else False
        elif tool in {'read_element_text', 'read_response'}:
            from memory.task_content import TaskContent
            observed = isinstance(result.detail, TaskContent)
        elif tool == 'wait_for_response':
            observed = isinstance(result.detail, dict) and result.detail.get('status') == 'response_ready'
        elif tool == 'wait_for':
            observed = isinstance(result.detail, dict) and result.detail.get('status') == 'condition_met'
        elif tool in {'click_element', 'interact_element', 'focus_element',
                     'type_url', 'navigate_url', 'edit_notepad'}:
            from vision.semantic import ScreenState, element_identity
            detail = result.detail if isinstance(result.detail, dict) else {}
            target_id = detail.get('runtime_id')
            target_identity = detail.get('target_identity')
            operation = detail.get('operation') or ('focus' if tool == 'focus_element' else None)
            screen = ScreenState.from_computer(after)
            diagnostic = after.provider_failure
            lifecycle_failures = {
                'VERIFICATION_WORKER_TIMEOUT',
                'VERIFICATION_WORKER_CRASH',
                'VERIFICATION_WORKER_START_FAILED',
                'VERIFICATION_COM_INITIALIZATION_FAILED',
                'POST_ACTION_RESULT_INVALID',
            }
            if diagnostic in lifecycle_failures:
                reason = diagnostic
            elif not after.available:
                reason = diagnostic or 'POST_ACTION_OBSERVATION_UNAVAILABLE'
            elif not after.elements_complete and not after.target_verification_complete:
                reason = diagnostic or ('POST_ACTION_OBSERVATION_INCOMPLETE'
                          + (':' + after.limit_reached if after.limit_reached else ''))
            elif diagnostic == 'POST_ACTION_FOREGROUND_CHANGED':
                observed, reason = False, diagnostic
            elif screen.has_modal:
                reason = 'POST_ACTION_MODAL_DIALOG'
            elif (not before.active_window or not after.active_window
                  or before.active_window.handle != after.active_window.handle
                  or before.current_application != after.current_application):
                observed, reason = False, 'POST_ACTION_WINDOW_CHANGED'
            else:
                if target_id:
                    before_matches = [e for e in before.elements if e.runtime_id == target_id]
                    after_matches = [e for e in after.elements if e.runtime_id == target_id]
                    if not before_matches and target_identity:
                        before_matches = [e for e in before.elements
                                          if element_identity(e) == target_identity]
                    if not after_matches and target_identity:
                        after_matches = [e for e in after.elements
                                         if element_identity(e) == target_identity]
                else:
                    before_matches = [e for e in before.elements if element_identity(e) == target_identity]
                    after_matches = [e for e in after.elements if element_identity(e) == target_identity]
                if diagnostic == 'POST_ACTION_TARGET_QUERY_FAILED':
                    reason = diagnostic
                elif len(after_matches) > 1:
                    observed, reason = False, 'POST_ACTION_TARGET_AMBIGUOUS'
                elif not after_matches:
                    if diagnostic == 'POST_ACTION_TARGET_MISSING':
                        reason = diagnostic
                    elif (operation == 'activate' and len(before_matches) == 1
                          and before.current_application not in BROWSERS
                          and detail.get('control_type') not in {
                              'TabItem', 'ListItem', 'RadioButton', 'TreeItem'}):
                        observed, reason = True, 'TARGET_REMOVED_AFTER_INVOKE'
                    else:
                        reason = 'POST_ACTION_TARGET_MISSING'
                else:
                    after_target = after_matches[0]
                    if operation == 'select' or (operation == 'activate' and detail.get('control_type') in {
                            'TabItem', 'ListItem', 'RadioButton'}):
                        if after_target.selected is None:
                            reason = 'POST_ACTION_STATE_UNAVAILABLE'
                        elif after_target.selected:
                            observed, reason = True, 'SELECTION_OBSERVED'
                        else:
                            observed, reason = False, 'POST_ACTION_SELECTION_NOT_OBSERVED'
                    elif operation in {'check', 'uncheck'}:
                        if after_target.checked is None:
                            reason = 'UIA_TOGGLE_STATE_UNAVAILABLE'
                        else:
                            observed = after_target.checked is detail.get('requested_state')
                            reason = 'CHECK_STATE_OBSERVED' if observed else 'CHECK_STATE_NOT_OBSERVED'
                    elif operation in {'expand', 'collapse'}:
                        if after_target.expanded is None:
                            reason = 'UIA_EXPANSION_STATE_UNAVAILABLE'
                        else:
                            observed = after_target.expanded == ('expanded' if operation == 'expand' else 'collapsed')
                            reason = 'EXPANSION_STATE_OBSERVED' if observed else 'EXPANSION_STATE_NOT_OBSERVED'
                    elif operation == 'focus':
                        if after_target.focused is True:
                            observed, reason = True, 'FOCUS_OBSERVED'
                        elif after_target.focused is False:
                            observed, reason = False, (after.focus_evidence or
                                                       'FOCUS_NOT_OBSERVED')
                        else:
                            reason = after.focus_evidence or 'FOCUS_STATE_UNAVAILABLE'
                    elif operation == 'set_url':
                        expected = detail.get('expected_url_digest')
                        if not expected or after_target.content_digest is None:
                            reason = 'ADDRESS_VALUE_UNAVAILABLE'
                        elif after_target.content_digest != expected:
                            observed, reason = False, 'ADDRESS_VALUE_MISMATCH'
                        elif after_target.focused is not True:
                            reason = after.focus_evidence or 'FOCUS_STATE_UNAVAILABLE'
                        else:
                            observed, reason = True, 'ADDRESS_VALUE_OBSERVED'
                    elif operation == 'navigate_url':
                        expected = detail.get('expected_url_digest')
                        if not expected or after_target.content_digest is None:
                            reason = 'NAVIGATION_LOCATION_UNAVAILABLE'
                        else:
                            observed = after_target.content_digest == expected
                            reason = ('NAVIGATION_LOCATION_OBSERVED' if observed else
                                      'UNEXPECTED_NAVIGATION_DESTINATION')
                    elif operation == 'edit_notepad':
                        expected, previous = detail.get('expected_content_digest'), detail.get('before_content_digest')
                        if not expected or after_target.content_digest is None: reason = 'TEXT_CONTENT_UNAVAILABLE'
                        elif after_target.content_digest != expected: observed, reason = False, 'TEXT_CONTENT_MISMATCH'
                        elif expected == previous: observed, reason = False, 'TEXT_CHANGE_NOT_OBSERVED'
                        else: observed, reason = True, 'TEXT_CONTENT_OBSERVED'
                    elif operation == 'activate':
                        reason = 'UNSUPPORTED_VERIFICATION_CONTRACT'
        elif tool == 'wait_accessibility_ready':
            detail = result.detail if isinstance(result.detail, dict) else {}
            if detail.get('status') == 'PAGE_LOAD_UNCERTAIN': reason = 'PAGE_LOAD_UNCERTAIN'
            elif (detail.get('status') == 'ACCESSIBILITY_READY' and after.available and after.active_window and after.active_window.handle == detail.get('handle') and after.current_application == detail.get('app') == 'chrome'):
                observed, reason = True, 'ACCESSIBILITY_READY'
            else: observed, reason = False, 'READINESS_CONTEXT_CHANGED'
        elif tool in {"get_current_time", "take_screenshot", "list_windows", "get_active_window", "get_visible_elements", "read_visible_text", "list_elements"}:
            observed = result.detail is not None
        elif tool == "navigate_explorer_folder":
            detail = result.detail if isinstance(result.detail, dict) else {}

            observed = (
                detail.get("status") == "address_observed"
                and detail.get("app") == "explorer"
                and detail.get("folder") == args["folder"]
                and detail.get("address") == "Address: Downloads"
                and after.available
                and any(
                    w.handle == detail.get("handle") and w.app == "explorer"
                    for w in after.open_windows
                )
            )
        elif after.available:
            matches = [w for w in after.open_windows if
                       ("app_name" in args and w.app == args["app_name"]) or
                       ("window_name" in args and w.title.casefold() == args["window_name"].casefold())]
            if tool == "open_app": observed = len(matches) == 1 and after.active_window is not None and after.active_window.handle == matches[0].handle
            elif tool in {"focus_app", "focus_window"}:
                observed = len(matches) == 1 and after.active_window == matches[0]
            elif tool in {"minimize_app", "minimize_window"}: observed = bool(matches) and all(w.minimized for w in matches)
            elif tool in {"maximize_app", "maximize_window"}: observed = bool(matches) and all(w.maximized for w in matches)
            elif tool in {"close_app", "close_window"}: observed = not matches
            elif tool == "open_url":
                observed = after.current_application in BROWSERS and after.url_digest == url_digest(args["url"]) if after.url_digest else None
            elif tool == 'scroll':
                import math
                detail = result.detail if isinstance(result.detail, dict) else {}
                first, last = detail.get('before_percent'), detail.get('after_percent')
                bound = (before.available and before.active_window is not None and after.active_window is not None
                    and before.current_application == after.current_application
                    and before.active_window.handle == after.active_window.handle == detail.get('handle')
                    and before.url_digest == after.url_digest == detail.get('url_digest')
                    and (after.current_application not in BROWSERS or before.url_digest is not None)
                    and detail.get('status') == 'scroll_observed' and bool(detail.get('runtime_id')))
                valid = all(type(v) in {int, float} and math.isfinite(v) and 0 <= v <= 100 for v in (first, last))
                if bound and valid:
                    # Unchanged/edge-of-page evidence remains UNCERTAIN, not success.
                    observed = True if (last > first if args['direction'] == 'down' else last < first) else None
            elif tool == "search_web":
                from urllib.parse import quote
                observed = after.current_application in BROWSERS and after.url_digest == url_digest("https://www.google.com/search?q=" + quote(args["query"], safe="")) if after.url_digest else None
            elif tool == "press_key" and args.get("key") == "enter":
                from adapters.browser import BrowserAdapter
                adapter = BrowserAdapter()
                if (adapter.is_claude(before) and adapter.is_claude(after)
                        and before.active_window.handle == after.active_window.handle
                        and before.focused_text_length and after.focused_text_length == 0
                        and before.focused_id and before.focused_id == after.focused_id
                        and adapter.generating(after)):
                    observed = True
                if (same_field and before.current_application == after.current_application and after.current_application not in BROWSERS
                        and before.focused_text_digest is not None and after.focused_text_digest is not None
                        and before.focused_text_digest != after.focused_text_digest
                        and before.focused_text_length is not None and after.focused_text_length is not None
                        and after.focused_text_length - before.focused_text_length in {1, 2}
                        and before.focused_line_breaks is not None and after.focused_line_breaks is not None
                        and after.focused_line_breaks == before.focused_line_breaks + 1):
                    observed = True
                # Evidence that the browser consumed the prompt, NOT that a server delivered a reply.
                if (before.current_application == after.current_application and after.current_application in BROWSERS and before.url_digest
                        and before.url_digest == after.url_digest and before.focused_text_length
                        and after.focused_text_length == 0 and same_field):
                    observed = True
            elif tool in {"type_text", "write_task_text"}:
                observed = (after.text_matches and before.focused_text_digest is not None
                            and after.focused_text_digest is not None and before.focused_text_digest != after.focused_text_digest
                            and same_field) if after.text_matches is not None else None
            elif tool == "focus_text_field":
                observed = (before.available and before.active_window is not None and after.active_window is not None
                            and before.active_window.handle == after.active_window.handle
                            and before.current_application == after.current_application
                            and (before.url_digest is None or before.url_digest == after.url_digest)
                            and bool(after.focused_id) and isinstance(result.detail, dict)
                            and after.focused_id == result.detail.get("runtime_id"))
        status = VerificationStatus.UNCERTAIN if observed is None else VerificationStatus.VERIFIED if observed else VerificationStatus.FAILED
        evidence = reason or ('OBSERVED_EXPECTED_STATE' if observed else 'EXPECTED_STATE_NOT_ESTABLISHED')
        return VerificationResult(status, evidence)
