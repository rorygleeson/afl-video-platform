"""Offline regression tests: preserve original Google failure evidence.

No network, AWS writes or paid video submissions.
"""
import unittest
from unittest.mock import patch

from test_google_video_stage5a import load_worker
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OriginalProviderFailureAuditTests(unittest.TestCase):
    def setUp(self):
        self.worker = load_worker()
        self.worker.PAID_ENABLED = "true"
        self.worker.MODEL = "gemini-omni-1.2-flash"
        self.job = "a" * 32
        self.scene = "scene3"
        self.request = "real-" + self.job + "-" + self.scene

    def test_failed_poll_persists_original_google_code_category_and_time(self):
        w = self.worker
        response = {
            "status": "failed",
            "errors": [{"code": "prohibited_content",
                        "message": "private customer-related prompt bytes not for retention"}],
            "input": {"data": "private image"},
        }
        with patch.object(w, "require_paid_unlock"), patch.object(
                w, "owner_record", return_value={
                    "status": "PROCESSING", "interactionId": "original-google-id"
                }), patch.object(w, "google_json", return_value=response) as google, patch.object(
                w, "transition") as save:
            result = w.poll(self.job, self.scene, self.request)
        self.assertEqual(result, {"status": "FAILED"})
        google.assert_called_once_with("GET", w.BASE + "/original-google-id")
        args = save.call_args.args
        self.assertEqual(args[:5], (self.job, self.scene, self.request, "PROCESSING", "FAILED"))
        evidence = args[5]
        self.assertEqual(evidence["errorCode"], "GOOGLE_FAILED")
        self.assertEqual(evidence["providerFinalStatus"], "failed")
        self.assertEqual(evidence["providerErrorCode"], "prohibited_content")
        self.assertEqual(evidence["providerErrorCategory"], "SAFETY_OR_POLICY")
        self.assertIn("providerFailureObservedAt", evidence)
        self.assertNotIn("message", str(evidence))
        self.assertNotIn("private", str(evidence))
        self.assertEqual(w.S3.put_object.call_count, 0)

    def test_safe_provider_error_message_retention(self):
        w = self.worker
        failure = w.extract_provider_failure({
            "errors": [{"code": "prohibited_content",
                        "message": "Video generation was blocked by safety filters"}]
        })
        self.assertEqual(failure["providerSafeMessages"],
                         ["Video generation was blocked by safety filters"])
        unsafe = w.extract_provider_failure({
            "errors": [{"code": "prohibited_content",
                        "message": "private customer-related prompt bytes not for retention"}]
        })
        self.assertNotIn("providerSafeMessages", unsafe)
        self.assertTrue(unsafe["providerMessagePresent"])

    def test_completed_poll_retry_does_not_access_google_or_resubmit(self):
        w = self.worker
        with patch.object(w, "require_paid_unlock"), patch.object(
                w, "owner_record", return_value={"status": "COMPLETED"}), patch.object(
                w, "google_json") as google:
            result = w.poll(self.job, self.scene, self.request)
        self.assertEqual(result, {"status": "COMPLETED"})
        google.assert_not_called()

    def test_original_provider_error_does_not_repeat_large_google_get(self):
        w = self.worker
        record = {
            "status": "FAILED", "errorCode": "GOOGLE_FAILED",
            "providerErrorCategory": "SAFETY_OR_POLICY",
            "providerErrorCode": "prohibited_content",
            "providerFinalStatus": "failed",
            "interactionId": "original-google-id",
            "requestId": self.request,
            "submittedAt": "2026-11-12T04:51:31+00:00",
            "providerFailureObservedAt": "2026-11-12T04:52:15+00:00",
        }
        w.JOBS.get_item.return_value = {"Item": {
            "paidVideoApprovals": {self.scene: {"status": "REQUESTED"}},
            "realVideos": {self.scene: record},
        }}
        with patch.object(w, "google_json") as google:
            result = w.diagnose_failed_interaction(self.job, self.scene)
        self.assertEqual(result["status"], "RECORDED_PROVIDER_FAILURE")
        self.assertEqual(result["source"], "DYNAMODB")
        self.assertEqual(result["errorCode"], "prohibited_content")
        self.assertEqual(result["providerFailureObservedAt"], record["providerFailureObservedAt"])
        self.assertEqual(result["interactionId"], record["interactionId"])
        google.assert_not_called()
        w.S3.put_object.assert_not_called()

    def test_fallback_remains_read_only_when_legacy_record_has_no_category(self):
        w = self.worker
        w.JOBS.get_item.return_value = {"Item": {
            "paidVideoApprovals": {self.scene: {"status": "REQUESTED"}},
            "realVideos": {self.scene: {
                "status": "FAILED", "interactionId": "original-google-id"
            }},
        }}
        with patch.object(w, "google_json", return_value={"status": "failed"}) as google:
            result = w.diagnose_failed_interaction(self.job, self.scene)
        self.assertEqual(result["status"], "READ_ONLY_DIAGNOSTIC")
        google.assert_called_once()
        w.S3.put_object.assert_not_called()

    def test_frontend_displays_stored_evidence_without_paid_retry(self):
        js = (ROOT / "frontend/app.js").read_text()
        api = (ROOT / "src/api/handler.py").read_text()
        self.assertIn("RECORDED_PROVIDER_FAILURE", js)
        self.assertIn("providerFailureObservedAt", js)
        self.assertIn("providerErrorCode", api)
        self.assertIn("submittedAt", api)
        self.assertNotIn("fetch('/google", js)


if __name__ == "__main__":
    unittest.main()
