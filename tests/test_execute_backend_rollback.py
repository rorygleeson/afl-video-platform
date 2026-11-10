"""Offline rollback authorization and snapshot guard tests."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from execute_backend_rollback import rollback


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.cf = Mock()
        self.manifest = {"stacks": {"backend": {
            "name": "afl-app-dev-backend", "previous_parameters": {"LambdaArtifactKey": "old"},
            "stack_id": "stack-id", "template_path": "/nonexistent", "template_sha256": "x"}}}
        self.plan = {"deploy_permitted": False, "stacks": {"backend": {
            "name": "afl-app-dev-backend", "current_parameters": {"LambdaArtifactKey": "old"},
            "proposed_parameters": {"LambdaArtifactKey": "new"}}}}

    def test_confirmation_required_before_any_aws_call(self):
        with self.assertRaisesRegex(ValueError, "confirmation"):
            rollback(self.cf, None, None, self.manifest, self.plan, "wrong", "123")
        self.cf.assert_not_called()
        self.cf.describe_stacks.assert_not_called()

    def test_refuse_wrong_release_parameters(self):
        self.cf.describe_stacks.return_value = {"Stacks": [{
            "StackId": "stack-id", "StackStatus": "UPDATE_COMPLETE",
            "Parameters": [{"ParameterKey": "LambdaArtifactKey", "ParameterValue": "unrelated"}]}]}
        with self.assertRaisesRegex(ValueError, "approved post-release"):
            rollback(self.cf, None, None, self.manifest, self.plan, "ROLLBACK_BACKEND", "123")
        self.cf.create_change_set.assert_not_called()

    def test_refuse_mismatched_snapshot(self):
        self.manifest["stacks"]["backend"]["previous_parameters"]["LambdaArtifactKey"] = "other"
        with self.assertRaisesRegex(ValueError, "snapshot and release plan"):
            rollback(self.cf, None, None, self.manifest, self.plan, "ROLLBACK_BACKEND", "123")
        self.cf.describe_stacks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
