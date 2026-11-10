"""Fail-closed tests for separate preview approval."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_separate_preview_approval import approve


class SeparateApprovalTests(unittest.TestCase):
    def setUp(self):
        self.change = {"ResourceChange": {"Action": "Modify", "LogicalResourceId": "ApiLambda", "Replacement": "False"}}
        self.review = {"verdict": "SAFE_PREVIEW", "stack": "afl-app-dev-backend", "issues": [], "changes": [self.change]}
        self.candidate = dict(self.review, retained_for_separate_approval=True, change_set_id="arn:aws:cloudformation:region:account:changeSet/example/id")
        self.run = {"event": "workflow_dispatch", "conclusion": "success", "head_branch": "main",
                    "head_sha": "abc123", "path": ".github/workflows/release.yml",
                    "inputs": {"change_set_preview": "backend"}}

    def test_exact_match(self):
        self.assertTrue(approve(self.review, self.candidate, self.run, "abc123"))

    def test_different_changes_blocked(self):
        self.candidate["changes"] = [{"ResourceChange": {"Action": "Remove", "LogicalResourceId": "ApiLambda"}}]
        with self.assertRaisesRegex(ValueError, "differ"):
            approve(self.review, self.candidate, self.run, "abc123")

    def test_different_commit_blocked(self):
        with self.assertRaisesRegex(ValueError, "commit"):
            approve(self.review, self.candidate, self.run, "different")

    def test_offline_run_blocked(self):
        self.run["inputs"]["change_set_preview"] = "offline"
        with self.assertRaisesRegex(ValueError, "backend preview"):
            approve(self.review, self.candidate, self.run, "abc123")

    def test_failed_review_blocked(self):
        self.run["conclusion"] = "failure"
        with self.assertRaisesRegex(ValueError, "successful"):
            approve(self.review, self.candidate, self.run, "abc123")

    def test_missing_retained_change_set_blocked(self):
        del self.candidate["change_set_id"]
        with self.assertRaisesRegex(ValueError, "not retained"):
            approve(self.review, self.candidate, self.run, "abc123")


if __name__ == "__main__":
    unittest.main()
