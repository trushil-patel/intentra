import unittest
from unittest.mock import Mock, patch
import numpy as np
from agent.runtime import Runtime
from config.settings import Settings
from safety.emergency_stop import EmergencyStop
from agent.planner import deterministic_v2


class RecognitionDiagnosticTests(unittest.TestCase):
    def test_diagnostic_never_dispatches_recognized_command(self):
        brain, transcriber = Mock(), Mock()
        transcriber.transcribe.return_value = 'Open Claude.ai.'
        runtime = Runtime(Settings(), brain, transcriber, EmergencyStop(), transcribe_only=True)
        with patch('builtins.print') as output:
            runtime._transcribe(np.zeros(3200, dtype=np.float32).tobytes())
        brain.handle.assert_not_called()
        self.assertFalse(runtime.workers)
        self.assertIn('Open Claude.ai.', str(output.call_args_list))
        self.assertFalse(runtime.transcribing.locked())

    def test_diagnostic_does_not_execute_typed_input_either(self):
        brain = Mock()
        runtime = Runtime(Settings(), brain, Mock(), EmergencyStop(), transcribe_only=True)
        with patch('builtins.print') as output:
            runtime._process('password=hunter2')
        brain.handle.assert_not_called()
        self.assertNotIn('hunter2', str(output.call_args_list))

    def test_normal_voice_mode_keeps_transcript_private(self):
        transcriber = Mock()
        transcriber.transcribe.return_value = 'Open Claude.ai.'
        runtime = Runtime(Settings(), Mock(), transcriber, EmergencyStop())
        runtime._spawn = Mock()
        with patch('builtins.print') as output:
            runtime._transcribe(np.zeros(3200, dtype=np.float32).tobytes())
        self.assertNotIn('Open Claude.ai.', str(output.call_args_list))
        runtime._spawn.assert_called_once()

    def test_claude_spoken_domain_variants_are_local(self):
        for text in ('Open Claude.ai.', 'Open Claude AI.', 'Open Claude dot AI.'):
            plan = deterministic_v2(text)
            self.assertEqual([a.tool for a in plan.actions], ['open_url'])
            self.assertEqual(plan.actions[0].arguments['url'], 'https://claude.ai')

class SpokenNavigationAliasTests(unittest.TestCase):
    def test_reported_spellings_dispatch_claude_navigation(self):
        for heard in ('Open cloud.ai', 'Open claud.ai'):
            transcriber = Mock()
            transcriber.transcribe.return_value = heard
            runtime = Runtime(Settings(), Mock(), transcriber, EmergencyStop())
            runtime._spawn = Mock()
            with patch('builtins.print'):
                runtime._transcribe(np.zeros(3200, dtype=np.float32).tobytes())
            interpreted = runtime._spawn.call_args.args[1]
            plan = deterministic_v2(interpreted)
            self.assertEqual([a.tool for a in plan.actions], ['open_url'])
            self.assertEqual(plan.actions[0].arguments['url'], 'https://claude.ai')

    def test_diagnostic_preserves_heard_text_and_shows_interpretation(self):
        transcriber, brain = Mock(), Mock()
        transcriber.transcribe.return_value = 'Open cloud.ai'
        runtime = Runtime(Settings(), brain, transcriber, EmergencyStop(), transcribe_only=True)
        with patch('builtins.print') as output:
            runtime._transcribe(np.zeros(3200, dtype=np.float32).tobytes())
        self.assertIn('Open cloud.ai', str(output.call_args_list))
        self.assertIn('[INTERPRETED] Spoken destination: Claude.ai', str(output.call_args_list))
        brain.handle.assert_not_called()
        self.assertFalse(runtime.workers)

    def test_literal_input_and_other_domains_are_unchanged(self):
        from audio.command_normalizer import normalize_spoken_navigation
        for text in ('Type Open cloud.ai', 'Type claud.ai', 'Search for cloud.ai',
                     'Open cloud.ai.example.com', 'Open cloud.ai/private',
                     'Open Notepad and type Open cloud.ai'):
            self.assertEqual(normalize_spoken_navigation(text), text)
        text = 'Open cloud.ai and type cloud.ai'
        self.assertEqual(normalize_spoken_navigation(text), 'Open Claude.ai and type cloud.ai')

    def test_typed_url_is_not_aliased_by_parser(self):
        plan = deterministic_v2('Go to cloud.ai')
        self.assertEqual(plan.actions[0].arguments['url'], 'cloud.ai')
