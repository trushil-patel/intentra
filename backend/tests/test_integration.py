import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agent.brain import Brain
from agent.context import UserCommand
from agent.schemas import Action, Plan
from safety.policy import Risk
from safety.permissions import Permissions
from safety.validator import Validator, normalize_url
from tools.registry import SPECS
from utils.logger import Audit
from utils.helpers import SafeError
from test_validator import make_router

class IntegrationTests(unittest.TestCase):
    def test_multi_step_model_plan(self):
        router, calls = make_router()
        router.confirmations.confirm.return_value = True
        provider = Mock()
        provider.generate_plan.return_value = json.dumps({"goal": "Open site", "actions": [
            {"tool": "open_app", "arguments": {"app_name": "chrome"}},
            {"tool": "open_url", "arguments": {"url": "https://claude.ai"}}]})
        results = Brain(provider, router, router.stop).handle(UserCommand("Open Chrome and go to claude.ai"))
        self.assertEqual(calls, ["open_app", "open_url"])
        self.assertTrue(all(r.status == "SUCCESS" for r in results))
        self.assertEqual(router.confirmations.confirm.call_count, 2)

    def test_shell_and_path_plans_rejected(self):
        for action in ({"tool": "run_powershell", "arguments": {"code": "calc"}},
                       {"tool": "open_app", "arguments": {"app_name": "C:/evil.exe"}},
                       {"tool": "open_url", "arguments": {"url": "javascript:alert(1)"}}):
            router, calls = make_router()
            router.confirmations.confirm.return_value = True
            provider = Mock()
            provider.generate_plan.return_value = json.dumps({"goal": "x", "actions": [action]})
            result = Brain(provider, router, router.stop).handle(UserCommand("Please help me now"))
            self.assertFalse(calls)
            self.assertEqual(result[-1].status, "BLOCKED")

    def test_policy_cannot_be_weakened(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "permissions.json"
            path.write_text(json.dumps({"LOW":"allow", "MEDIUM":"allow", "HIGH":"allow"}))
            with self.assertRaises(SafeError): Permissions(path)

    def test_actual_logs_omit_arbitrary_secret_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.log"
            audit = Audit(path)
            audit.event("password=hunter2", "secret-token", "4111111111111111")
            audit.close()
            text = path.read_text()
            for secret in ("hunter2", "secret-token", "4111111111111111"):
                self.assertNotIn(secret, text)

    def test_actual_logging_failure_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Audit(Path(tmp) / "audit.log")
            with patch.object(audit.logger.handlers[0], "shouldRollover", side_effect=OSError("disk full")):
                with self.assertRaises(SafeError): audit.event("open_app", "LOW", "ALLOWED")
            audit.close()

    def test_shortcuts_no_terminal_or_delete(self):
        validator = Validator({})
        for keys in (["win", "r"], ["ctrl", "shift", "j"], ["alt", "f4"], ["shift", "delete"]):
            with self.assertRaises(SafeError): validator.validate(Action("hotkey", {"keys": keys}), SPECS["hotkey"])

    def test_boolean_scroll_not_integer(self):
        with self.assertRaises(SafeError): Validator({}).validate(Action("scroll", {"direction": "down", "amount": True}), SPECS["scroll"])

    def test_obfuscated_local_urls_rejected(self):
        for value in ("http://127.1", "http://0x7f.0.0.1", "http://2130706433", "http://localhost", "https://example.com:9999"):
            with self.assertRaises(SafeError): normalize_url(value)

    def test_no_retry_after_failure(self):
        registry = Mock()
        registry.spec.return_value = SPECS["get_current_time"]
        registry.invoke.side_effect = RuntimeError("failure")
        router, calls = make_router(registry=registry)
        result = router.execute(Plan("x", (Action("get_current_time", {}), Action("get_current_time", {}))))
        self.assertEqual(registry.invoke.call_count, 1)
        self.assertEqual(result[-1].status, "ERROR")
