import unittest
from dataclasses import replace
from agent.computer_state import ComputerState, WindowState, Element
from vision.semantic import ScreenState, ObservationCache
from vision.state_diff import compare
from utils.helpers import SafeError


def state(elements=None, **kwargs):
    return ComputerState(WindowState(1, 'chrome'), (WindowState(1, 'chrome'),),
                         tuple(elements or [Element((1,), 'Button', 'Settings', bounds=(0, 0, 20, 20))]),
                         available=True, **kwargs)


class SemanticTests(unittest.TestCase):
    def test_reference_changes_with_layout_and_expires(self):
        original = ScreenState.from_computer(state())
        ref = original.elements[0]
        self.assertEqual(original.resolve(ref).id, ref.id)
        changed = ScreenState.from_computer(state([replace(ref.element, bounds=(20, 0, 40, 20))]))
        with self.assertRaises(SafeError): changed.resolve(ref)
        with self.assertRaises(SafeError): original.resolve(ref, now=ref.observed_at + 6)

    def test_unknown_and_modal_cannot_resolve(self):
        original = ScreenState.from_computer(state())
        for data in (replace(original.computer, available=False),
                     replace(original.computer, elements_complete=False),
                     state([replace(original.elements[0].element, modal=True)])):
            with self.assertRaises(SafeError): ScreenState.from_computer(data).resolve(original.elements[0])

    def test_diff_and_cache_invalidation(self):
        before = ScreenState.from_computer(state())
        after = ScreenState.from_computer(state([Element((2,), 'Window', 'Confirm', modal=True)]))
        delta = compare(before, after)
        self.assertTrue(delta.dialog_appeared)
        self.assertEqual(delta.removed, ((1,),))
        self.assertFalse(compare(before, ScreenState.from_computer(ComputerState())).changed)
        cache = ObservationCache()
        cache.put(before.computer)
        self.assertIsNotNone(cache.get())
        cache.invalidate()
        self.assertIsNone(cache.get())
