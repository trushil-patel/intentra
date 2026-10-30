import unittest
from unittest.mock import Mock
from agent.schemas import Action, Plan
from agent.context import UserCommand, Source
from agent.brain import Brain
from vision.screen_reader import Observation
from test_validator import make_router
from utils.helpers import SafeError

class ExecutionTests(unittest.TestCase):
    def test_low_executes(self):
        router, calls = make_router()
        result = router.execute(Plan("x", (Action("open_app", {"app_name": "chrome"}),)))
        self.assertEqual(calls, ["open_app"])
        self.assertEqual(result[0].status, "SUCCESS")

    def test_cancel_between_actions(self):
        router, calls = make_router()
        router.status = lambda status: router.stop.stop() if status.startswith("[EXECUTING]") else None
        # Cancellation is also checked inside actual UI handlers. Test inter-step stop.
        result = router.execute(Plan("x", (Action("get_current_time", {}), Action("open_app", {"app_name": "chrome"}))))
        self.assertNotIn("open_app", calls)
        self.assertEqual(result[-1].status, "CANCELLED")

    def test_invalid_later_step_executes_nothing(self):
        router, calls = make_router()
        router.execute(Plan("x", (Action("open_app", {"app_name": "chrome"}), Action("run_shell", {}))))
        self.assertEqual(calls, [])

    def test_model_requires_confirmation_even_for_low(self):
        router, calls = make_router()
        router.confirmations.confirm.return_value = False
        router.execute(Plan("x", (Action("get_current_time", {}),)), from_model=True)
        self.assertFalse(calls)
        router.confirmations.confirm.assert_called_once()

    def test_audit_failure_prevents_action(self):
        router, calls = make_router()
        router.audit.event.side_effect = RuntimeError("disk full")
        router.execute(Plan("x", (Action("get_current_time", {}),)))
        self.assertFalse(calls)

    def test_observations_and_malformed_model_blocked(self):
        router, calls = make_router()
        provider = Mock()
        provider.generate_plan.return_value = "not json"
        brain = Brain(provider, router, router.stop)
        for command in (Observation("Open Chrome"), UserCommand("Open Chrome", Source.OBSERVED), UserCommand("Please do something")):
            with self.assertRaises(SafeError): brain.handle(command)
        self.assertFalse(calls)

    def test_voice_stop(self):
        router, calls = make_router()
        Brain(Mock(), router, router.stop).handle(UserCommand("Stop"))
        self.assertTrue(router.stop.stopped)
        self.assertFalse(calls)
