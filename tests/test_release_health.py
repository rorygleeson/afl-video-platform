"""Offline tests for the read-only release health gate."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "verify_release_health", Path(__file__).resolve().parents[1] / "scripts/verify_release_health.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CloudFormation:
    def describe_stacks(self, StackName):
        return {"Stacks": [{"StackId": "stack-123", "StackStatus": "UPDATE_COMPLETE"}]}


class Lambda:
    def __init__(self, google_hash="known"):
        self.google_hash = google_hash

    def get_function_configuration(self, FunctionName):
        return {"State": "Active", "LastUpdateStatus": "Successful",
                "CodeSha256": self.google_hash}


class States:
    def describe_state_machine(self, stateMachineArn):
        return {"status": "ACTIVE", "definition": '{"StartAt":"A"}'}


def baseline():
    return {
        "region": "ap-southeast-2", "guardrails": {"deploy_permitted": False},
        "stacks": {"backend": {"name": "afl-app-dev-backend", "stack_id": "stack-123"}},
        "lambdas": {"afl-app-dev-google-omni-worker": {"sha256": "known"}},
        "state_machines": [{"name": "test", "arn": "arn:test",
            "definition_verified": True,
            "definition_sha256": hashlib.sha256(b'{"StartAt":"A"}').hexdigest()}],
    }


class HealthTests(unittest.TestCase):
    def test_matching_baseline(self):
        self.assertEqual(module.verify(baseline(), CloudFormation(), Lambda(), States()), [])

    def test_google_code_drift_fails(self):
        failures = module.verify(baseline(), CloudFormation(), Lambda("changed"), States())
        self.assertTrue(any("protected Google code hash changed" in x for x in failures))

    def test_missing_definition_evidence_fails(self):
        b = baseline()
        b["state_machines"][0]["definition_verified"] = False
        self.assertTrue(module.verify(b, CloudFormation(), Lambda(), States()))

    def test_guardrail_missing_fails(self):
        b = baseline()
        b["guardrails"]["deploy_permitted"] = True
        with self.assertRaises(ValueError):
            module.verify(b, CloudFormation(), Lambda(), States())


if __name__ == "__main__":
    unittest.main()
