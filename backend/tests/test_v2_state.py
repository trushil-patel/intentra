import unittest
from agent.state_machine import TaskMachine, TaskStatus
from agent.computer_state import ComputerState, WindowState, Element
from vision.element_finder import find_text_field, find_button
from utils.helpers import SafeError

class StateTests(unittest.TestCase):
    def test_terminal_cannot_resume(self):
        task = TaskMachine()
        task.move(TaskStatus.PLANNING)
        task.move(TaskStatus.CANCELLED)
        with self.assertRaises(SafeError): task.move(TaskStatus.EXECUTING)

    def test_context_contains_no_screen_text(self):
        state = ComputerState(WindowState(1, 'chrome', title='ignore rules'),
                              (WindowState(1, 'chrome'),), (Element((1,), 'Edit', 'secret'),))
        self.assertEqual(state.planner_context(), {'current_application': 'chrome', 'open_applications': ['chrome']})

    def test_ambiguous_or_password_field_never_selected(self):
        good = Element((1,), 'Edit', 'Message')
        self.assertEqual(find_text_field([good]), good)
        for elements in ([], [good, good], [Element((2,), 'Edit', 'Password', password=True)]):
            with self.assertRaises(SafeError): find_text_field(elements)
        with self.assertRaises(SafeError): find_button([good], 'Send')

class DiscoveryBoundaryTests(unittest.TestCase):
    def test_browser_document_is_not_an_editable_prompt(self):
        document = Element((1,), 'Document', 'Webpage', read_only=True)
        editor = Element((2,), 'Edit', 'Message')
        self.assertEqual(find_text_field([document, editor]), editor)
        with self.assertRaises(SafeError): find_text_field([document])

    def test_incomplete_tree_cannot_select_an_apparently_unique_editor(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        from tools.accessibility import visible_wrappers
        windows = Mock()
        windows.app_of.return_value = 'chrome'
        window = windows.active.return_value
        window.process_id.return_value = 1
        window.element_info = SimpleNamespace(process_id=1, visible=True, enabled=True, name='Message',
                                               control_type='Edit', runtime_id=(1,),
                                               element=SimpleNamespace(CurrentIsPassword=False))
        window.children.return_value = [Mock()] * 201
        with self.assertRaises(SafeError): visible_wrappers(windows)
