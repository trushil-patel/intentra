import unittest
from unittest.mock import Mock
from agent.schemas import Action, Plan
from agent.router import Router
from safety.validator import Validator
from safety.permissions import Permissions
from safety.rate_limiter import RateLimiter
from safety.emergency_stop import EmergencyStop
from tools.registry import Registry, SPECS
from utils.helpers import SafeError

def make_router(**kwargs):
    calls = []
    registry = Registry({name: (lambda n=name, **kw: calls.append(n)) for name in SPECS})
    args = dict(registry=registry, validator=Validator({"chrome": {"allowed": True}}), permissions=Permissions(), limiter=RateLimiter(), stop=EmergencyStop(), confirmations=Mock(), audit=Mock(), status=lambda x: None)
    args.update(kwargs)
    return Router(**args), calls

class ValidatorTests(unittest.TestCase):
    def test_entire_plan_validated(self):
        router, calls = make_router()
        plan = Plan("x", (Action("open_app", {"app_name": "chrome"}), Action("run_shell", {"command": "calc"})))
        with self.assertRaises(SafeError): router.preflight(plan)
        self.assertFalse(calls)

    def test_permission_failure_denies(self):
        p = Mock()
        p.evaluate.side_effect = RuntimeError("security failure")
        router, calls = make_router(permissions=p)
        with self.assertRaises(SafeError): router.preflight(Plan("x", (Action("get_current_time", {}),)))
        self.assertFalse(calls)

    def test_no_risk_override(self):
        router, calls = make_router()
        with self.assertRaises(SafeError): router.preflight(Plan("x", (Action("click", {"risk": "LOW"}),)))
        self.assertFalse(calls)
