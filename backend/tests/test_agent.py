import unittest
from agent.planner import deterministic, normalized, STOP_WORDS

class ParserTests(unittest.TestCase):
    def test_examples(self):
        for text, tool in (("Open Chrome.", "open_app"), ("Open VS Code", "open_app"), ("Go to claude.ai", "open_url"), ("Press Enter", "press_key"), ("Scroll down", "scroll"), ("Switch to VS Code", "focus_app"), ("Close Notepad", "close_app")):
            self.assertEqual(deterministic(text).actions[0].tool, tool)

    def test_type_is_literal(self):
        plan = deterministic("Type: hello and press Enter")
        self.assertEqual(plan.actions[0].arguments["text"], "hello and press Enter")
        self.assertEqual(len(plan.actions), 1)

    def test_multi_step_uses_planner(self):
        self.assertIsNone(deterministic("Open Chrome and go to claude.ai"))

    def test_stop(self):
        self.assertIn(normalized("Stop!"), STOP_WORDS)
