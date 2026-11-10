import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_rollback import rollback_manifest

class RollbackEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "backend.yaml"
        self.path.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
        self.baseline = {"region": "ap-southeast-2", "captured_utc": "2026-11-10",
            "guardrails": {"deploy_permitted": False}, "lambdas": {}, "stacks": {
                "backend": {"name": "afl-app-dev-backend", "stack_id": "stack-id",
                    "parameters": {"Flag": "true"}, "resources": [],
                    "deployed_template_path": str(self.path),
                    "deployed_template_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()}}}
        self.plan = {"deploy_permitted": False, "blockers": ["deployment disabled"],
            "stacks": {"backend": {"name": "afl-app-dev-backend",
                "current_parameters": {"Flag": "true"}}}}
    def test_captures_original_template(self):
        result = rollback_manifest(self.baseline, self.plan)
        self.assertTrue(result["stacks"]["backend"]["template_body_saved"])
        self.assertFalse(result["rollback_executable"])
    def test_rejects_tampered_template(self):
        self.path.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "digest"):
            rollback_manifest(self.baseline, self.plan)
    def test_rejects_changed_parameters(self):
        self.plan["stacks"]["backend"]["current_parameters"]["Flag"] = "false"
        with self.assertRaisesRegex(ValueError, "Baseline changed"):
            rollback_manifest(self.baseline, self.plan)
if __name__ == "__main__":
    unittest.main()
