import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from smoke_test_api import verify_payload

class ApiSmokeTests(unittest.TestCase):
    def test_valid_scenario(self):
        self.assertEqual("afl-grand-final", verify_payload(200, {"scenarios": [
            {"id": "afl-grand-final", "name": "Winning Kick", "sceneCount": 3}]}))
    def test_rejects_wrong_status(self):
        with self.assertRaises(ValueError):
            verify_payload(403, {"scenarios": []})
    def test_rejects_invalid_payload(self):
        with self.assertRaises(ValueError):
            verify_payload(200, {"scenarios": [{"id": "x", "sceneCount": 2}]})
if __name__ == "__main__":
    unittest.main()
