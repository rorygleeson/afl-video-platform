"""Fail-closed tests for the controlled CloudFormation executor."""
import importlib.util
from pathlib import Path
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("execute_release", Path(__file__).resolve().parents[1] / "scripts/execute_release_change_set.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CF:
    def __init__(self):
        self.executions = []

    def describe_stacks(self, StackName):
        return {"Stacks": [{"StackId": "stack-id", "StackStatus": "UPDATE_COMPLETE",
                             "Parameters": [{"ParameterKey": "LambdaArtifactKey", "ParameterValue": "old"}]}]}

    def describe_change_set(self, ChangeSetName, StackName):
        return {"ChangeSetId": "cs-id", "StackId": "stack-id", "Status": "CREATE_COMPLETE",
                "ExecutionStatus": "AVAILABLE",
                "Parameters": [{"ParameterKey": "LambdaArtifactKey", "ParameterValue": "new"}],
                "Changes": [{"ResourceChange": {"LogicalResourceId": "ApiLambda",
                    "Action": "Modify", "Replacement": "False"}}]}

    def execute_change_set(self, **kwargs):
        self.executions.append(kwargs)


def inputs():
    baseline = {"region": "ap-southeast-2", "guardrails": {"deploy_permitted": False},
                "known_drift": {"google_worker": False},
                "google_known_live_artifact": {"key": "verified.zip"},
                "stacks": {"google": {"parameters": {"GoogleWorkerArtifactKey": "verified.zip"}},
                           "backend": {"name": "afl-app-dev-backend", "stack_id": "stack-id",
                                      "parameters": {"LambdaArtifactKey": "old"}}}}
    plan = {"deploy_permitted": False, "stacks": {"backend": {
        "name": "afl-app-dev-backend", "current_parameters": {"LambdaArtifactKey": "old"},
        "proposed_parameters": {"LambdaArtifactKey": "new"}}}}
    return baseline, plan


class Tests(unittest.TestCase):
    @patch.object(module, "verify_reconcile_permission", return_value=False)
    def test_executes_only_with_exact_confirmation(self, permission):
        cf = CF()
        baseline, plan = inputs()
        with self.assertRaises(ValueError):
            module.execute(cf, "cs-id", "cs-id", baseline, plan, None, None)
        self.assertEqual(cf.executions, [])
        module.execute(cf, "cs-id", "cs-id", baseline, plan, None, None, execute_confirmed=True)
        self.assertEqual(len(cf.executions), 1)

    @patch.object(module, "verify_reconcile_permission", return_value=False)
    def test_rejects_forbidden_change_on_second_page(self, permission):
        cf = CF()
        original = cf.describe_change_set

        def paged(**kwargs):
            if kwargs.get("NextToken") == "page-2":
                return {"ChangeSetId": "cs-id", "StackId": "stack-id",
                        "Changes": [{"ResourceChange": {
                            "LogicalResourceId": "GoogleWorkerLambda",
                            "Action": "Remove", "Replacement": "True"}}]}
            first = original(**kwargs)
            first["NextToken"] = "page-2"
            return first

        cf.describe_change_set = paged
        baseline, plan = inputs()
        with self.assertRaisesRegex(ValueError, "Unsafe change set"):
            module.execute(cf, "cs-id", "cs-id", baseline, plan, None, None, True)
        self.assertEqual(cf.executions, [])

    def test_rejects_wrong_approval(self):
        cf = CF()
        baseline, plan = inputs()
        with self.assertRaises(ValueError):
            module.execute(cf, "cs-id", "another-id", baseline, plan, None, None, True)
        self.assertEqual(cf.executions, [])

    def test_rejects_unreconciled_google_drift(self):
        cf = CF()
        baseline, plan = inputs()
        baseline["known_drift"]["google_worker"] = True
        with self.assertRaisesRegex(ValueError, "Google worker reconciliation"):
            module.execute(cf, "cs-id", "cs-id", baseline, plan, None, None, True)
        self.assertEqual(cf.executions, [])

    def test_rejects_mismatched_google_artifact(self):
        cf = CF()
        baseline, plan = inputs()
        baseline["stacks"]["google"]["parameters"]["GoogleWorkerArtifactKey"] = "stale.zip"
        with self.assertRaisesRegex(ValueError, "Google artifact"):
            module.execute(cf, "cs-id", "cs-id", baseline, plan, None, None, True)
        self.assertEqual(cf.executions, [])

    def test_rejects_changed_live_stack(self):
        cf = CF()
        baseline, plan = inputs()
        baseline["stacks"]["backend"]["stack_id"] = "another-stack"
        with self.assertRaises(ValueError):
            module.execute(cf, "cs-id", "cs-id", baseline, plan, None, None, True)
        self.assertEqual(cf.executions, [])


if __name__ == "__main__":
    unittest.main()
