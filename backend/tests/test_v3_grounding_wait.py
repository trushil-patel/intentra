import unittest
from dataclasses import replace
from unittest.mock import Mock
from agent.computer_state import Element
from vision.semantic import ScreenState
from vision.grounding import ground
from agent.wait_manager import WaitManager, Condition, ConditionKind
from safety.emergency_stop import EmergencyStop
from utils.helpers import SafeError, Cancelled
from test_v3_semantic import state


class GroundingTests(unittest.TestCase):
    def test_unique_and_ambiguous(self):
        self.assertIsNotNone(ground(ScreenState.from_computer(state()), 'Settings button').selected)
        button = state().elements[0]
        self.assertIsNone(ground(ScreenState.from_computer(state([button, replace(button, runtime_id=(2,))])), 'Settings').selected)

    def test_low_confidence_ocr_and_password_never_select(self):
        for button in (replace(state().elements[0], confidence=.5), replace(state().elements[0], source='ocr'),
                       replace(state().elements[0], password=True)):
            self.assertIsNone(ground(ScreenState.from_computer(state([button])), 'Settings').selected)

    def test_relational_and_ordering(self):
        elements = [Element((1,), 'Edit', 'Search', bounds=(0, 0, 30, 10)),
                    Element((2,), 'Button', 'Go', bounds=(0, 20, 30, 30)),
                    Element((3,), 'Button', 'More', bounds=(100, 40, 130, 50))]
        screen = ScreenState.from_computer(state(elements))
        self.assertEqual(ground(screen, 'button below Search').selected.element.name, 'Go')
        self.assertEqual(ground(screen, 'second button').selected.element.name, 'More')
        self.assertIsNone(ground(screen, 'second search result').selected)


class WaitTests(unittest.TestCase):
    def test_timeout_and_cancellation(self):
        now = [0.0]
        observer = Mock()
        observer.observe.return_value = state()
        stop = EmergencyStop()
        waiter = WaitManager(observer, stop, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n))
        condition = Condition(ConditionKind.ELEMENT_EXISTS, 'Missing')
        with self.assertRaisesRegex(SafeError, 'WAIT_TIMEOUT'):
            waiter.wait_for(condition, ScreenState.from_computer(state()), 1)
        self.assertLessEqual(observer.observe.call_count, 4)
        stop.stop()
        with self.assertRaises(Cancelled): waiter.wait_for(condition, ScreenState.from_computer(state()), 1)

    def test_unexpected_modal_pauses(self):
        observer = Mock()
        observer.observe.return_value = state([Element((2,), 'Window', 'Confirm', modal=True)])
        with self.assertRaisesRegex(SafeError, 'dialog'):
            WaitManager(observer, EmergencyStop()).wait_for(
                Condition(ConditionKind.ELEMENT_EXISTS, 'Confirm'), ScreenState.from_computer(state()), 1)


class ReferenceContinuationTests(unittest.TestCase):
    def test_pronoun_uses_only_unchanged_observation(self):
        screen = ScreenState.from_computer(state([
            Element((1,), 'Edit', 'Search', bounds=(0, 0, 30, 10)),
            Element((2,), 'Button', 'Go', bounds=(0, 20, 30, 30))]))
        previous = ground(screen, 'Search').selected
        self.assertEqual(ground(screen, 'button below it', previous=previous).selected.element.name, 'Go')
        changed = ScreenState.from_computer(replace(screen.computer, focused_id=(99,)))
        self.assertIsNone(ground(changed, 'button below it', previous=previous).selected)
        self.assertIsNone(ground(screen, 'button below it').selected)

    def test_session_reference_expires_and_clears(self):
        from tools.native import NativeBridge
        bridge = NativeBridge({}, EmergencyStop())
        reference = ScreenState.from_computer(state()).elements[0]
        bridge.session_reference = reference
        bridge.clear_task()
        self.assertIs(bridge.session_reference, reference)
        bridge.clear_session()
        self.assertIsNone(bridge.session_reference)
