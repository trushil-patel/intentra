import unittest
from config.settings import Settings
from utils.logger import redact

class ConfigurationTests(unittest.TestCase):
    def test_local_only(self):
        with self.assertRaises(ValueError):
            Settings(ollama_url="https://example.com")
        Settings()

    def test_limits(self):
        with self.assertRaises(ValueError):
            Settings(max_actions=11)

    def test_redaction(self):
        for secret in ("password=hunter2", "Authorization: Bearer abc", "sk-abcdef", "4111 1111 1111 1111"):
            self.assertNotIn(secret, redact(secret))
