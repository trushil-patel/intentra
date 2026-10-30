import json
import unittest
from dataclasses import replace
from unittest.mock import MagicMock
from PIL import Image
from agent.schemas import Action, Plan
from agent.router import Result
from agent.verifier import Verifier, VerificationStatus
from agent.response_wait import ResponseWaiter
from agent.task_scope import validate_model_scope
from safety.emergency_stop import EmergencyStop
from config.settings import Settings
from vision.providers.local import LocalVisionProvider
from vision.semantic import ScreenState
from vision.grounding import ground
from utils.helpers import SafeError
from test_v3_semantic import state
from test_v3_transfer import FIELD, READY


class EvidenceTests(unittest.TestCase):
    def test_unknown_response_baseline_cannot_finish(self):
        for baseline in (replace(FIELD, elements_complete=False),
                         replace(READY, elements=(replace(READY.elements[0], content_digest=None),))):
            with self.assertRaises(SafeError): ResponseWaiter(MagicMock(), EmergencyStop()).wait(baseline, 1)

    def test_unrelated_screen_change_does_not_verify_click(self):
        before = state()
        after = replace(before, focused_text_digest='unrelated animation')
        result = Verifier().verify(Action('click_element', {'element_id': 'last'}),
                                   Result('click_element', 'SUCCESS', {'runtime_id': (1,)}), before, after)
        self.assertEqual(result.status, VerificationStatus.UNCERTAIN)

    def test_model_cannot_expand_requested_url_with_hidden_path(self):
        for url in ('https://example.com/delete', 'https://example.com/?data=private'):
            with self.assertRaises(SafeError):
                validate_model_scope('Open example.com', Plan('x', (Action('open_url', {'url': url}),)), {})

    def test_vision_transport_local_and_output_not_clickable(self):
        session = MagicMock()
        response = session.post.return_value.__enter__.return_value
        response.status_code = 200
        answer = {'description': 'Visible Settings button', 'regions': [
            {'name': 'Settings', 'bounds': [0, 0, 10, 10], 'confidence': .99}]}
        response.iter_content.return_value = [json.dumps({'message': {'content': json.dumps(answer)}}).encode()]
        image = Image.new('RGB', (20, 20))
        try:
            result = LocalVisionProvider(Settings(), model='configured-local-model', enabled=True, session=session).analyze_screen(
                image, ScreenState.from_computer(state()))
        finally: image.close()
        self.assertFalse(session.trust_env)
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
        self.assertTrue(session.post.call_args.args[0].startswith('http://127.0.0.1:'))
        self.assertIsNone(ground(ScreenState.from_computer(state(result.regions)), 'Settings').selected)

    def test_vision_action_proposals_are_rejected(self):
        session = MagicMock()
        response = session.post.return_value.__enter__.return_value
        response.status_code = 200
        response.iter_content.return_value = [json.dumps({'message': {'content': json.dumps({'actions': []})}}).encode()]
        image = Image.new('RGB', (20, 20))
        try:
            with self.assertRaises(SafeError):
                LocalVisionProvider(Settings(), model='configured-local-model', enabled=True, session=session).analyze_screen(
                    image, ScreenState.from_computer(state()))
        finally: image.close()
