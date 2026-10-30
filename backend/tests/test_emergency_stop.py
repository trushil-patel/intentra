import unittest
from safety.emergency_stop import EmergencyStop
from utils.helpers import Cancelled

class EmergencyTests(unittest.TestCase):
    def test_latched(self):
        token = EmergencyStop()
        token.stop()
        for _ in range(2):
            with self.assertRaises(Cancelled): token.check()
        token.resume()
        token.check()
