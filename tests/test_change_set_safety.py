import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_change_set import validate

def doc(action="Modify", replacement="False", name="GoogleWorkerLambda"):
    return {"Status": "CREATE_COMPLETE", "Changes": [
        {"ResourceChange": {"LogicalResourceId": name, "Action": action, "Replacement": replacement}}
    ]}

class ChangeSetSafetyTests(unittest.TestCase):
    def test_permitted_code_only(self):
        self.assertEqual(validate(doc(), {"GoogleWorkerLambda"}), [])
    def test_replacement_true_blocked(self):
        self.assertTrue(validate(doc(replacement="True"), {"GoogleWorkerLambda"}))
    def test_conditional_replacement_blocked(self):
        self.assertTrue(validate(doc(replacement="Conditional"), {"GoogleWorkerLambda"}))
    def test_delete_blocked(self):
        self.assertTrue(validate(doc(action="Remove"), {"GoogleWorkerLambda"}))
    def test_unexpected_resource_blocked(self):
        self.assertTrue(validate(doc(name="DeveloperPreflightInvokePermission"), {"GoogleWorkerLambda"}))
    def test_incomplete_change_set_blocked(self):
        self.assertTrue(validate({"Status": "CREATE_PENDING"}, {"GoogleWorkerLambda"}))
    def test_empty_change_set_blocked(self):
        self.assertTrue(validate({"Status": "CREATE_COMPLETE", "Changes": []}, {"GoogleWorkerLambda"}))
if __name__ == "__main__":
    unittest.main()
