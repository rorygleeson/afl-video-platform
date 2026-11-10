"""Rollback preview must reject destructive changes and permit known safe dependencies."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from preview_rollback_change_set import inspect


class RollbackChangeSetTests(unittest.TestCase):
    def test_rejects_failed_change_set(self):
        with self.assertRaisesRegex(ValueError, "not CREATE_COMPLETE"):
            inspect({"Status": "FAILED", "Changes": [
                {"ResourceChange": {"LogicalResourceId": "ApiLambda",
                                    "Action": "Modify", "Replacement": "False"}}]})

    def test_rejects_delete(self):
        with self.assertRaisesRegex(ValueError, "Unsafe rollback"):
            inspect({"Status": "CREATE_COMPLETE", "Changes": [
                {"ResourceChange": {"LogicalResourceId": "ApiLambda",
                                    "Action": "Remove", "Replacement": "False"}}]})

    def test_rejects_unapproved_resource(self):
        with self.assertRaisesRegex(ValueError, "Unsafe rollback"):
            inspect({"Status": "CREATE_COMPLETE", "Changes": [
                {"ResourceChange": {"LogicalResourceId": "GoogleWorkerLambda",
                                    "Action": "Modify", "Replacement": "False"}}]})

    def test_allows_safe_backend_lambda(self):
        self.assertEqual(1, inspect({"Status": "CREATE_COMPLETE", "Changes": [
            {"ResourceChange": {"LogicalResourceId": "ApiLambda",
                                "Action": "Modify", "Replacement": "False"}}]}))

    def test_rejects_empty(self):
        with self.assertRaisesRegex(ValueError, "Unsafe rollback"):
            inspect({"Status": "CREATE_COMPLETE", "Changes": []})


if __name__ == "__main__":
    unittest.main()
