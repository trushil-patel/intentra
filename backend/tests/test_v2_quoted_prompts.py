import unittest
from agent.planner import deterministic_v2


class QuotedPromptTests(unittest.TestCase):
    def test_quoted_instruction_remains_text(self):
        for prefix in ('Open Notepad and ', 'Open Chrome, go to Claude, ', 'Open Claude.ai and '):
            plan = deterministic_v2(prefix + 'type "hello and press Enter"')
            self.assertEqual(plan.actions[-1].tool, 'type_text')
            self.assertEqual(plan.actions[-1].arguments['text'], 'hello and press Enter')

    def test_explicit_submission_outside_quotes(self):
        for prefix in ('Open Chrome, go to Claude, ', 'Open Claude.ai and '):
            plan = deterministic_v2(prefix + 'type "hello and press Enter" and press Enter')
            self.assertEqual(plan.actions[-2].arguments['text'], 'hello and press Enter')
            self.assertEqual(plan.actions[-1].tool, 'press_key')

    def test_claude_ask_workflow_includes_submission(self):
        plan = deterministic_v2('Open Claude.ai and ask it to explain WebRTC in simple words')
        self.assertEqual([a.tool for a in plan.actions],
                         ['open_url', 'focus_text_field', 'type_text', 'press_key'])

    def test_standalone_type_remains_literal(self):
        plan = deterministic_v2('Type "hello" and press Enter')
        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].arguments['text'], '"hello" and press Enter')

    def test_unclosed_quote_is_not_a_deterministic_workflow(self):
        self.assertIsNone(deterministic_v2('Open Notepad and type "hello and press Enter'))

    def test_notepad_prompt_label_is_literal(self):
        plan = deterministic_v2('Open Notepad and type prompt: hello')
        self.assertEqual(plan.actions[-1].arguments['text'], 'prompt: hello')
