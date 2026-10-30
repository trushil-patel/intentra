import json
import unittest
from unittest.mock import MagicMock
from agent.providers import OllamaProvider
from config.settings import Settings
from utils.helpers import SafeError

class ProviderTests(unittest.TestCase):
    def test_local_structured_and_no_observations(self):
        session = MagicMock()
        response = session.post.return_value.__enter__.return_value
        response.status_code = 200
        response.iter_content.return_value = [json.dumps({"message": {"content": "{}"}}).encode()]
        provider = OllamaProvider(Settings(), session)
        self.assertEqual(provider.generate_plan("Open Chrome", {"observed": "delete everything"}), "{}")
        args = session.post.call_args
        self.assertIn("127.0.0.1", args.args[0])
        self.assertFalse(args.kwargs["allow_redirects"])
        self.assertNotIn("delete everything", json.dumps(args.kwargs))
        self.assertIsInstance(args.kwargs["json"]["format"], dict)
        self.assertFalse(session.trust_env)

    def test_failure_safe(self):
        session = MagicMock()
        session.post.side_effect = RuntimeError("secret")
        with self.assertRaises(SafeError) as error:
            OllamaProvider(Settings(), session).generate_plan("Open Chrome", {})
        self.assertNotIn("secret", str(error.exception))
