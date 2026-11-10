"""Unit tests for read-only rollback readiness."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("rollback_readiness", Path(__file__).resolve().parents[1] / "scripts/verify_rollback_readiness.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CF:
    def __init__(self, params=None):
        self.params = params if params is not None else [
            {"ParameterKey": "ArtifactBucketName", "ParameterValue": "bucket"},
            {"ParameterKey": "LambdaArtifactKey", "ParameterValue": "previous.zip"},
        ]

    def describe_stacks(self, StackName):
        return {"Stacks": [{"StackId": "id", "StackStatus": "UPDATE_COMPLETE", "Parameters": self.params}]}


class S3:
    def head_object(self, Bucket, Key):
        assert Bucket == "bucket" and Key == "previous.zip"
        return {}


class Tests(unittest.TestCase):
    def test_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backend.yaml"
            path.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
            entry = {"name": "afl-app-dev-backend", "stack_id": "id", "template_path": str(path),
                     "template_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "previous_parameters": {"ArtifactBucketName": "bucket", "LambdaArtifactKey": "previous.zip"}}
            manifest = {"region": "ap-southeast-2", "rollback_executable": False,
                        "deployment_executed": False, "stacks": {"backend": entry}}
            self.assertEqual(module.check(manifest, CF(), S3()), [])
            path.write_text("changed")
            self.assertTrue(any("hash mismatch" in x for x in module.check(manifest, CF(), S3())))
            path.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
            self.assertTrue(any("parameters changed" in x for x in module.check(manifest, CF([]), S3())))
            manifest["rollback_executable"] = True
            self.assertTrue(module.check(manifest, CF(), S3()))


if __name__ == "__main__":
    unittest.main()
