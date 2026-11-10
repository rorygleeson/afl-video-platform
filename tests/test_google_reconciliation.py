"""Offline tests for Google worker reconciliation planner (no AWS calls)."""
import copy
import unittest
from scripts.prepare_google_reconciliation import prepare, EXPECTED_KEY, EXPECTED_SHA


def baseline():
    return {
        "stacks": {"google": {
            "name": "afl-app-dev-google-video",
            "status": "UPDATE_COMPLETE",
            "stack_id": "stack-id",
            "deployed_template_path": "dist/release/deployed-templates/google.yaml",
            "deployed_template_sha256": "abc",
            "parameters": {
                "ArtifactBucketName": "artifact-bucket",
                "GoogleWorkerArtifactKey": "old.zip",
                "PaidVideoEnabled": "true",
            },
        }},
        "lambdas": {"afl-app-dev-google-omni-worker": {
            "sha256": EXPECTED_SHA, "state": "Active", "last_update_status": "Successful",
        }},
        "google_known_live_artifact": {
            "bucket": "artifact-bucket", "key": EXPECTED_KEY, "sha256": EXPECTED_SHA,
        },
    }


class GoogleReconciliationTests(unittest.TestCase):
    def test_only_artifact_key_changes(self):
        result = prepare(baseline())
        self.assertEqual(result["verdict"], "READY_FOR_SEPARATE_CHANGE_SET_PREVIEW")
        self.assertEqual(set(result["changed_parameters"]), {"GoogleWorkerArtifactKey"})
        self.assertEqual(result["proposed_parameters"]["PaidVideoEnabled"], "true")
        self.assertFalse(result["aws_changed"])

    def test_noop_if_already_aligned(self):
        b = baseline()
        b["stacks"]["google"]["parameters"]["GoogleWorkerArtifactKey"] = EXPECTED_KEY
        self.assertEqual(prepare(b)["verdict"], "ALREADY_RECONCILED")

    def test_reject_unexpected_live_sha(self):
        b = baseline()
        b["lambdas"]["afl-app-dev-google-omni-worker"]["sha256"] = "different"
        with self.assertRaises(ValueError):
            prepare(b)

    def test_reject_unverified_artifact(self):
        b = baseline()
        b["google_known_live_artifact"]["sha256"] = "different"
        with self.assertRaises(ValueError):
            prepare(b)

    def test_reject_verification_gap(self):
        b = baseline()
        b["verification_gaps"] = ["Missing state machine permission"]
        with self.assertRaises(ValueError):
            prepare(b)


if __name__ == "__main__":
    unittest.main()
