import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from plan_release import plan, STACKS

def fixtures():
    baseline = {"region": "ap-southeast-2", "guardrails": {"deploy_permitted": False},
                "known_drift": {"google_worker": False},
                "google_known_live_artifact": {"key": "verified.zip"},
                "stacks": {}}
    for label, (name, required) in STACKS.items():
        baseline["stacks"][label] = {"name": name, "status": "UPDATE_COMPLETE",
                                    "parameters": {k: "existing/" + k for k in required}}
    baseline["stacks"]["google"]["parameters"]["GoogleWorkerArtifactKey"] = "verified.zip"
    baseline["stacks"]["backend"]["parameters"]["PaidVideoEnabled"] = "true"
    artifacts = {"format": 1, "artifacts": {k: {"sha256_hex": "abc", "s3_key": "releases/sha256/abc/" + k + ".zip"}
                                         for k in ("api", "image-worker", "legacy-video-worker", "assembly-worker")}}
    return baseline, artifacts

class ReleasePlanTests(unittest.TestCase):
    def test_preserves_live_flags(self):
        b, a = fixtures()
        result = plan(b, a)
        self.assertFalse(result["deploy_permitted"])
        self.assertEqual(result["stacks"]["backend"]["proposed_parameters"]["PaidVideoEnabled"], "true")
        self.assertEqual(result["stacks"]["google"]["changed_parameters"], {})
        self.assertIn("FfmpegLayerArtifactKey", result["stacks"]["assembly"]["proposed_parameters"])
    def test_missing_stack_fails(self):
        b, a = fixtures()
        del b["stacks"]["assembly"]
        with self.assertRaises(ValueError): plan(b, a)
    def test_google_drift_not_silently_resolved(self):
        b, a = fixtures()
        b["known_drift"]["google_worker"] = True
        with self.assertRaises(ValueError): plan(b, a)
    def test_reject_mismatched_google_stack_key(self):
        b, a = fixtures()
        b["stacks"]["google"]["parameters"]["GoogleWorkerArtifactKey"] = "stale.zip"
        with self.assertRaises(ValueError): plan(b, a)
    def test_reject_missing_google_drift_verification(self):
        b, a = fixtures()
        del b["known_drift"]
        with self.assertRaises(ValueError): plan(b, a)
    def test_unverified_google_zip_fails(self):
        b, a = fixtures()
        del b["google_known_live_artifact"]
        with self.assertRaises(ValueError): plan(b, a)
    def test_missing_artifact_fails(self):
        b, a = fixtures()
        del a["artifacts"]["api"]
        with self.assertRaises(ValueError): plan(b, a)
if __name__ == "__main__":
    unittest.main()
