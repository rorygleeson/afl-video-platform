"""Pure offline documentation-to-source regression checks.

Scope: route table, current ASL handoff/retry shape, canonical document
navigation, and historic labelling. No boto3, AWS credentials or paid calls.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
API = (ROOT / "src/api/handler.py").read_text()
VIDEO_ASL = (ROOT / "infrastructure/app/google-video.yaml").read_text()
ASSEMBLY_ASL = (ROOT / "infrastructure/app/assembly.yaml").read_text()
ENDPOINTS = (DOCS / "API_ENDPOINTS.md").read_text()
RETRIES = (DOCS / "RETRIES_AND_TIMEOUTS.md").read_text()
STATUSES = (DOCS / "JOB_STATUSES.md").read_text()
ARCH = (DOCS / "ARCHITECTURE.md").read_text()

EXPECTED = [
    ("GET", "/scenarios"),
    ("POST", "/uploads"),
    ("POST", "/jobs"),
    ("GET", "/jobs/{jobId}"),
    ("GET", "/jobs/{jobId}/selfie"),
    ("POST", "/jobs/{jobId}/generate"),
    ("GET", "/jobs/{jobId}/images"),
    ("GET", "/jobs/{jobId}/video/{scene}/preview"),
    ("GET", "/jobs/{jobId}/video/scene1/preflight"),
    ("GET", "/video/auth-check"),
    ("GET", "/video/paid-google-auth-check"),
    ("POST", "/jobs/{jobId}/real-videos/sequence"),
    ("POST", "/jobs/{jobId}/real-videos/{scene}"),
    ("POST", "/jobs/{jobId}/real-videos/{scene}/retry"),
    ("GET", "/jobs/{jobId}/real-videos/{scene}/diagnostics"),
    ("GET", "/jobs/{jobId}/videos"),
    ("GET", "/jobs/{jobId}/videos/{scene}"),
    ("POST", "/jobs/{jobId}/videos/{scene}"),
    ("POST", "/jobs/{jobId}/assembly"),
    ("GET", "/jobs/{jobId}/assembly"),
]
HISTORICAL = [
    "API_STAGE1B.md", "BANANA_TEMPLATE_LOCK_RELEASE.md", "BOOTSTRAP.md",
    "CODE_REVIEW_2026-11-08.md", "DEPLOYMENT.md",
    "DEPLOYMENT_BASELINE_2026-11-15.md", "DEPLOYMENT_OWNERSHIP_AUDIT.md",
    "GOOGLE_OMNI_STAGE2_API_CONTRACT.md", "GOOGLE_WIF.md",
    "PAID_MP4_CODE_AUDIT_2026-11-08.md", "PARALLEL_SCENES.md",
    "PRODUCTION_PIPELINE_PLAN.md", "SCENE1_REPLICATE_PILOT.md",
    "TEN_VIDEO_ATTEMPTS_RELEASE.md", "VIDEO_IMPLEMENTATION_PLAN.md",
    "VIDEO_SCENE_RETRY_RELEASE.md", "VIDEO_SCENE_RETRY_UI_RELEASE.md",
    "RESOURCE_CLEANUP.md", "VIDEO_PROMPTS.md",
]


class DocumentationContractTests(unittest.TestCase):
    def test_catalog_has_all_twenty_method_path_combinations(self):
        rows = re.findall(r"^\|\s*\d+\s*\|\s*(GET|POST)\s*\|\s*`([^\`]+)`\s*\|", ENDPOINTS, re.M)
        self.assertEqual(len(rows), 20, "Update API endpoint inventory when dispatcher changes")
        self.assertEqual(set(rows), set(EXPECTED))
        self.assertEqual(len(set(rows)), len(rows), "Duplicate HTTP route table row")

    def test_dispatcher_source_still_exposes_expected_route_shapes(self):
        # These checks anchor the human-reviewed table to core implementation.
        for phrase in (
            'def lambda_handler(event, context):',
            'if method == "GET" and path == "/scenarios"',
            'if method == "POST" and path == "/uploads"',
            'if method == "POST" and path == "/jobs"',
            'if method == "GET" and selfie_match:',
            'if method == "POST" and retry_match:',
            'if method == "POST" and sequence_match:',
            'if method == "POST" and real_match:',
            'if method == "GET" and diagnostic_match:',
            'if method == "GET" and video_all_match:',
            'if method == "POST" and generate_match:',
            'if method == "GET" and match:',
            'if method == "POST":\n                return start_assembly(',
            'if method == "GET":\n                job = get_job(',
        ):
            self.assertIn(phrase, API, f"Route source changed: {phrase}")
        self.assertIn('return selfie_for_job(job)', API)

    def test_real_video_submit_has_no_retry_or_stagger(self):
        self.assertIn('"ResultPath":"$.claim","Next":"Submit"', VIDEO_ASL)
        self.assertNotIn('"SubmissionSchedule":', VIDEO_ASL)
        self.assertNotIn('"WaitScene2":', VIDEO_ASL)
        self.assertNotIn('"WaitScene3":', VIDEO_ASL)
        self.assertIn('"WaitForVideo":{"Type":"Wait","Seconds":20', VIDEO_ASL)
        self.assertIn('"PollVideo":{"Type":"Task"', VIDEO_ASL)
        self.assertIn('"Retry":[{"ErrorEquals":["States.TaskFailed"]', VIDEO_ASL)
        self.assertIn('No fixed scheduling waits', RETRIES)

    def test_assembly_confirms_single_retry_and_terminal_source_gate(self):
        self.assertIn('"WaitBeforeConfirmedRetry"', ASSEMBLY_ASL)
        self.assertIn('"Seconds": 15', ASSEMBLY_ASL)
        self.assertIn('"RetryConfirmedAssemblyFailure"', ASSEMBLY_ASL)
        self.assertIn('def assembly_failure_disposition(job):', API)
        self.assertIn('record.get("retryCount") == 1', API)
        self.assertIn('if status == "FAILED"', API)
        self.assertIn('REVIEW_REQUIRED', STATUSES)
        self.assertIn('STITCHING_FAILED', STATUSES)
        self.assertIn('WaitBeforeConfirmedRetry', RETRIES)
        self.assertIn('**15s**', RETRIES)

    def test_canonical_docs_link_targets_exist(self):
        current = ["README.md", "API_ENDPOINTS.md", "JOB_STATUSES.md",
                   "RETRIES_AND_TIMEOUTS.md", "ARCHITECTURE.md",
                   "INFRASTRUCTURE.md", "RESILIENCE_AUDIT_2026-11-16.md",
                   "RESILIENCY_ROADMAP.md", "GOOGLE_OMNI_RELIABILITY_CHECKLIST.md",
                   "DOCUMENTATION_QUALITY_REVIEW_2026-11-16.md"]
        for path in current:
            self.assertTrue((DOCS / path).is_file(), path)
            text = (DOCS / path).read_text()
            self.assertTrue(text.startswith("# "), path)
            for target in re.findall(r"\]\(([^)]+)\)", text):
                base = target.split("#", 1)[0]
                if not base or "://" in base or base.startswith("mailto:"):
                    continue
                self.assertTrue((DOCS / base).exists(), f"Broken link {path} → {target}")

    def test_historical_stages_cannot_masquerade_as_live_contract(self):
        for name in HISTORICAL:
            text = (DOCS / name).read_text()
            self.assertIn("DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC", text[:1100], name)
            self.assertIn("README.md", text[:1300], name)

    def test_no_obsolete_retries_in_current_primary_docs(self):
        for doc in (RETRIES, STATUSES, ARCH):
            self.assertNotIn("assembly has no Step Functions Retry/Catch", doc)
            self.assertNotIn("Source includes 10-second scene 2", doc)
        self.assertIn("20 method/path combinations", ENDPOINTS)
        self.assertIn("RETRYING", STATUSES)


if __name__ == "__main__":
    unittest.main()
