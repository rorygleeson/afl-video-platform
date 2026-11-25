"""Offline safety regression tests for the unified Google-worker release."""
import io
import hashlib
import sys
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_google_worker import replace_handler, validate_changes, validate_preflight_permission


class GoogleWorkerReleaseTests(unittest.TestCase):
    def test_repack_preserves_other_files_and_metadata(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("handler.py", b"old")
            archive.writestr("video_prompts.py", b"keep prompt bytes")
            archive.writestr("pkg/dependency.py", b"keep dependency bytes")
        with patch("release_google_worker.EXPECTED_PRIOR_HANDLER_SHA256", hashlib.sha256(b"old").hexdigest()):
            result = replace_handler(raw.getvalue(), b"new")
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            self.assertEqual(b"new", archive.read("handler.py"))
            self.assertEqual(b"keep prompt bytes", archive.read("video_prompts.py"))
            self.assertEqual(b"keep dependency bytes", archive.read("pkg/dependency.py"))

    def test_blocks_unrecognized_live_worker_handler(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("handler.py", b"live-hotfix-must-not-overwrite")
        with self.assertRaisesRegex(ValueError, "known pre-PR90"):
            replace_handler(raw.getvalue(), b"new")

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

    def test_replacement_rejection_reports_safe_resource_metadata(self):
        change = {"ResourceChange": {"LogicalResourceId": "DeveloperPreflightInvokePermission",
            "ResourceType": "AWS::Lambda::Permission", "Action": "Modify", "Replacement": "Conditional",
            "PhysicalResourceId": "do-not-print-this-id",
            "Details": [{"Evaluation": "Dynamic", "ChangeSource": "ResourceAttribute",
                "CausingEntity": "GoogleWorkerLambda.Arn", "Target": {
                    "Attribute": "Properties", "Name": "FunctionName", "RequiresRecreation": "Always"}}]}}
        with self.assertRaisesRegex(ValueError, "Blocked CloudFormation change") as caught:
            validate_changes([change])
        message = str(caught.exception)
        self.assertIn("DeveloperPreflightInvokePermission", message)
        self.assertIn("Conditional", message)
        self.assertIn("FunctionName", message)
        self.assertNotIn("do-not-print-this-id", message)

    def test_conditional_preflight_only_after_live_verification(self):
        permission = {"ResourceChange": {"LogicalResourceId": "DeveloperPreflightInvokePermission",
            "ResourceType": "AWS::Lambda::Permission", "Action": "Modify", "Replacement": "Conditional",
            "Details": [{"Evaluation": "Dynamic", "ChangeSource": "ResourceAttribute",
                "CausingEntity": "GoogleWorkerLambda.Arn", "Target": {"Attribute": "Properties",
                    "Name": "FunctionName", "RequiresRecreation": "Always"}}]}}
        worker = {"ResourceChange": {"LogicalResourceId": "GoogleWorkerLambda",
            "ResourceType": "AWS::Lambda::Function", "Action": "Modify", "Replacement": "False",
            "Details": [{"Target": {"Attribute": "Properties", "Name": "Code", "RequiresRecreation": "Never"}}]}}
        with self.assertRaises(ValueError):
            validate_changes([permission, worker])
        accepted = validate_changes([permission, worker], verified_preflight=True)
        self.assertEqual(2, len(accepted))
        self.assertTrue(any(x.get("verified_live_preflight_permission") for x in accepted))
        permission["ResourceChange"]["Details"][0]["Target"]["Name"] = "Principal"
        with self.assertRaises(ValueError):
            validate_changes([permission, worker], verified_preflight=True)

    def test_preflight_permission_requires_exact_existing_grant(self):
        from unittest.mock import Mock
        arn = "arn:aws:lambda:ap-southeast-2:134387458447:function:afl-app-dev-google-omni-worker"
        principal = "arn:aws:iam::134387458447:role/afl-video-github-deploy-role"
        statement = {"Sid": "permission-sid", "Effect": "Allow", "Action": "lambda:InvokeFunction",
                     "Principal": {"AWS": principal}, "Resource": arn}
        cf = Mock()
        cf.describe_stack_resource.return_value = {"StackResourceDetail": {"PhysicalResourceId": "permission-sid"}}
        lam = Mock()
        import json
        lam.get_policy.return_value = {"Policy": json.dumps({"Statement": [statement]})}
        self.assertEqual("permission-sid", validate_preflight_permission(lam, cf, arn)["sid"])
        statement["Condition"] = {"StringEquals": {"aws:SourceAccount": "other"}}
        lam.get_policy.return_value = {"Policy": json.dumps({"Statement": [statement]})}
        with self.assertRaises(ValueError):
            validate_preflight_permission(lam, cf, arn)


if __name__ == "__main__":
    unittest.main()
