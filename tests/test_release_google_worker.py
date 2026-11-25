"""Offline safety regression tests for the unified Google-worker release."""
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_google_worker import replace_handler, validate_changes


class GoogleWorkerReleaseTests(unittest.TestCase):
    def test_repack_preserves_other_files_and_metadata(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("handler.py", b"old")
            archive.writestr("video_prompts.py", b"keep prompt bytes")
            archive.writestr("pkg/dependency.py", b"keep dependency bytes")
        result = replace_handler(raw.getvalue(), b"new")
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            self.assertEqual(b"new", archive.read("handler.py"))
            self.assertEqual(b"keep prompt bytes", archive.read("video_prompts.py"))
            self.assertEqual(b"keep dependency bytes", archive.read("pkg/dependency.py"))

    def test_requires_single_code_only_lambda_change(self):
        change = {"ResourceChange": {"LogicalResourceId": "GoogleWorkerLambda",
            "ResourceType": "AWS::Lambda::Function", "Action": "Modify", "Replacement": "False",
            "Details": [{"Target": {"Attribute": "Properties", "Name": "Code", "RequiresRecreation": "Never"}}]}}
        result = validate_changes([change])
        self.assertEqual("GoogleWorkerLambda", result[0]["logical_id"])

    def test_rejects_description_and_timeout_changes(self):
        for field in ("Description", "Timeout", "Environment"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_changes([{"ResourceChange": {"LogicalResourceId": "GoogleWorkerLambda",
                    "ResourceType": "AWS::Lambda::Function", "Action": "Modify", "Replacement": "False",
                    "Details": [{"Target": {"Attribute": "Properties", "Name": field, "RequiresRecreation": "Never"}}]}}])

    def test_rejects_state_machine_definition_and_replacement(self):
        for logical, replacement in (("GoogleStateMachine", "False"), ("GoogleWorkerLambda", "True")):
            with self.subTest(logical=logical), self.assertRaises(ValueError):
                validate_changes([{"ResourceChange": {"LogicalResourceId": logical,
                    "ResourceType": "AWS::Lambda::Function", "Action": "Modify", "Replacement": replacement,
                    "Details": [{"Target": {"Attribute": "Properties", "Name": "Code"}}]}}])


if __name__ == "__main__":
    unittest.main()
