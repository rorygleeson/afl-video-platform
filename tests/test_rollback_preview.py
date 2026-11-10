"""Offline rollback change-set safety tests."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("rollback_preview", Path(__file__).resolve().parents[1] / "scripts/preview_rollback_change_set.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def test_expected_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backend.yaml"
            path.write_text("test template")
            manifest = {"region": "ap-southeast-2", "rollback_executable": False,
                        "deployment_executed": False, "stacks": {"backend": {
                            "name": "afl-app-dev-backend", "stack_id": "stack-id",
                            "template_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "previous_parameters": {"LambdaArtifactKey": "old"}}}}
            live = {"StackId": "stack-id", "StackStatus": "UPDATE_COMPLETE",
                    "Parameters": [{"ParameterKey": "LambdaArtifactKey", "ParameterValue": "new"}]}
            self.assertEqual(module.prepare(manifest, live, path)[1][0]["ParameterValue"], "old")
            live["Parameters"][0]["ParameterValue"] = "old"
            with self.assertRaises(ValueError):
                module.prepare(manifest, live, path)
            live["Parameters"][0]["ParameterValue"] = "new"
            path.write_text("tampered")
            with self.assertRaises(ValueError):
                module.prepare(manifest, live, path)

    def test_rejects_unexpected_resources(self):
        self.assertEqual(module.inspect({"Status": "CREATE_COMPLETE", "Changes": [
            {"ResourceChange": {"LogicalResourceId": "ApiLambda", "Action": "Modify", "Replacement": "False"}}
        ]}), 1)
        for logical in ("GoogleWorkerLambda", "ApiRole", "ImageReconcileInvokePermission"):
            with self.assertRaises(ValueError):
                module.inspect({"Status": "CREATE_COMPLETE", "Changes": [
                    {"ResourceChange": {"LogicalResourceId": logical, "Action": "Modify", "Replacement": "False"}}
                ]})


if __name__ == "__main__":
    unittest.main()
