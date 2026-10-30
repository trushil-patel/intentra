import unittest
from agent.schemas import Action, Plan
from safety.confirmation import ConfirmationManager
from safety.emergency_stop import EmergencyStop
from utils.helpers import Cancelled
from test_validator import make_router
from unittest.mock import Mock

class ConfirmationTests(unittest.TestCase):
    def manager(self, keys):
        counter = [0]
        def clock():
            counter[0] += 0.05
            return counter[0]
        iterator = iter(keys)
        return ConfirmationManager(seconds=0.5, clock=clock, key_reader=lambda: next(iterator, (False, False)), sleep=lambda _: None, display=lambda _: None)

    def test_fresh_press_and_single_use(self):
        manager = self.manager([(True, False), (False, False), (True, False)])
        self.assertTrue(manager.confirm(Action("click", {}), "target", EmergencyStop()))
        self.assertIsNone(manager.pending)
        self.assertFalse(manager.confirm(Action("click", {}), "other target", EmergencyStop()))

    def test_stale_yes_not_accepted(self):
        manager = self.manager([(True, False)] * 20)
        self.assertFalse(manager.confirm(Action("click", {}), None, EmergencyStop()))

    def test_stop_during_confirmation(self):
        token = EmergencyStop()
        token.stop()
        manager = self.manager([])
        with self.assertRaises(Cancelled): manager.confirm(Action("click", {}), None, token)
        self.assertIsNone(manager.pending)

    def test_medium_denied_no_execution(self):
        guard = Mock()
        router, calls = make_router(guard=guard)
        router.confirmations.confirm.return_value = False
        router.execute(Plan("x", (Action("click", {}),)))
        self.assertFalse(calls)
        router.confirmations.confirm.assert_called_once()

    def test_target_changed_no_execution(self):
        guard = Mock()
        guard.recheck.side_effect = RuntimeError("changed")
        router, calls = make_router(guard=guard)
        router.confirmations.confirm.return_value = True
        router.execute(Plan("x", (Action("click", {}),)))
        self.assertFalse(calls)
