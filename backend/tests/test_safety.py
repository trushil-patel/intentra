import unittest
from agent.schemas import Action, Plan
from safety.permissions import Permissions
from safety.policy import Risk
from safety.validator import Validator, normalize_url
from safety.rate_limiter import RateLimiter
from tools.registry import Registry, SPECS
from utils.helpers import SafeError

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry({name: lambda **kw: None for name in SPECS})
        self.validator = Validator({"chrome": {"allowed": True}})

    def test_unknown_tool(self):
        with self.assertRaises(SafeError): self.registry.spec("run_shell")

    def test_app_boundary(self):
        for app in ("cmd", "C:/evil.exe", "notepad"):
            with self.assertRaises(SafeError):
                self.validator.validate(Action("open_app", {"app_name": app}), SPECS["open_app"])
        self.validator.validate(Action("open_app", {"app_name": "chrome"}), SPECS["open_app"])

    def test_arguments(self):
        for args in ({"path": "evil.exe"}, {"app_name": "chrome", "risk": "LOW"}, {"app_name": 1}):
            with self.assertRaises(SafeError): self.validator.validate(Action("open_app", args), SPECS["open_app"])

    def test_schemes(self):
        for url in ("file:///x", "javascript:alert(1)", "data:text/plain,hi", "shell:run", "https://u:p@example.com", "http://127.0.0.1", "https://example.com\\evil", "cmd:calc"):
            with self.assertRaises(SafeError): normalize_url(url)
        self.assertEqual(normalize_url("claude.ai"), "https://claude.ai")

    def test_risks(self):
        p = Permissions()
        self.assertEqual(p.evaluate(Risk.LOW), "allow")
        self.assertEqual(p.evaluate(Risk.MEDIUM), "confirm")
        self.assertEqual(p.evaluate(Risk.HIGH), "deny")
        self.assertEqual(p.evaluate("LOW"), "deny")

    def test_limits(self):
        limiter = RateLimiter()
        with self.assertRaises(SafeError): limiter.check_plan([Action("click", {})] * 4)
        for _ in range(5): limiter.consume(Action("click", {}))
        with self.assertRaises(SafeError): limiter.consume(Action("click", {}))

    def test_malformed(self):
        for raw in ('oops', '{"goal":"x","actions":[]}', '{"goal":"x","goal":"y","actions":[]}', '{"goal":"x","actions":[{"tool":"click","arguments":{},"risk":"LOW"}]}'):
            with self.assertRaises(SafeError): Plan.parse(raw)
