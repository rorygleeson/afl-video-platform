"""Item 4: HTTP diagnostics never leak raw provider responses or start paid calls."""
import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch
from test_google_video_stage5a import load_worker


def http_error(code, payload, headers=None):
    h = Message()
    for key, value in (headers or {}).items():
        h[key] = value
    return urllib.error.HTTPError("https://google.example", code, "failure", h,
                                  io.BytesIO(json.dumps(payload).encode()))


class GoogleHttpDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.w = load_worker()
        self.job = "a" * 32
        self.scene = "scene3"
        self.request = "real-" + self.job + "-" + self.scene

    def test_429_captures_allowlisted_details_without_raw_payload(self):
        w = self.w
        failure = http_error(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
            "message": "Rate limit exceeded; retry later",
            "debug": "PRIVATE-SELFIE-AND-TOKEN"}},
            {"Retry-After": "30", "x-goog-request-id": "request-123",
             "Authorization": "Bearer secret", "Set-Cookie": "private-cookie"})
        with patch.object(w, "credentials", return_value="token"), patch.object(
                w.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaises(w.GoogleHttpFailure) as raised:
                w.google_json("GET", w.BASE + "/interaction01")
        details = raised.exception.details
        self.assertEqual(details["googleHttpStatus"], 429)
        self.assertEqual(details["googleRetryAfterSeconds"], 30)
        self.assertEqual(details["googleRequestId"], "request-123")
        self.assertEqual(details["googleErrorStatus"], "RESOURCE_EXHAUSTED")
        self.assertEqual(details["googleErrorNumber"], 429)
        self.assertEqual(details["googleSafeErrorMessage"], "Rate limit exceeded; retry later")
        self.assertNotIn("PRIVATE-SELFIE", str(details))
        self.assertNotIn("secret", str(details))
        self.assertNotIn("private-cookie", str(details))

    def test_raw_prompt_like_error_message_is_discarded(self):
        w = self.w
        f = http_error(403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
            "message": "private customer prompt: secret text"}},
            {"Retry-After": "Mon, 12 Nov 2026 20:00:00 GMT", "x-goog-request-id": "unsafe@email"})
        details = w.safe_google_http_failure(f)
        self.assertNotIn("googleSafeErrorMessage", details)
        self.assertNotIn("googleRetryAfterSeconds", details)
        self.assertNotIn("googleRequestId", details)
        self.assertEqual(details["googleErrorStatus"], "PERMISSION_DENIED")

    def test_get_error_persists_http_details_before_readonly_retry(self):
        w = self.w
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        details = {"googleHttpStatus": 503, "googleErrorStatus": "UNAVAILABLE",
                   "googleRetryAfterSeconds": 12}
        with patch.object(w, "require_paid_unlock"), patch.object(w, "owner_record",
                 return_value={"status": "PROCESSING", "interactionId": "interaction01"}), patch.object(
                 w, "google_json", side_effect=w.GoogleHttpFailure("GET", 503, details)
                 ) as google, patch.object(w, "transition") as transition:
            with self.assertRaises(w.GoogleHttpFailure):
                w.poll(self.job, self.scene, self.request)
        google.assert_called_once_with("GET", w.BASE + "/interaction01")
        self.assertEqual(transition.call_args.args[3:5], ("PROCESSING", "PROCESSING"))
        self.assertEqual(transition.call_args.args[5]["googleHttpStatus"], 503)
        w.S3.put_object.assert_not_called()

    def test_post_http_failure_never_triggers_second_paid_submission(self):
        w = self.w
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        details = {"googleHttpStatus": 429, "googleErrorStatus": "RESOURCE_EXHAUSTED"}
        with patch.object(w, "require_paid_unlock"), patch.object(
            w, "owner_record", return_value={"status": "PREPARED", "sourceImageKey": "image.jpg"}
        ), patch.object(w, "render_scene", return_value={
            "durationSeconds": 8, "videoGenerationEnabled": True, "prompt": "prompt"
        }), patch.object(w, "get_job", return_value={}), patch.object(w, "image_payload", return_value={
            "model": "gemini-omni-1.2-flash"
        }), patch.object(w, "checked_gcs_prefix", return_value=None), patch.object(
            w, "google_json", side_effect=w.GoogleHttpFailure("POST", 429, details)
        ) as google, patch.object(w, "transition") as transition:
            w.S3.get_object.return_value = {"ContentType": "image/jpeg",
                                              "Body": io.BytesIO(b"fake image")}
            with self.assertRaises(w.UnknownSubmission):
                w.submit(self.job, self.scene, self.request)
        self.assertEqual(google.call_count, 1)
        self.assertEqual(transition.call_args.args[3:5],
                         ("SUBMISSION_UNKNOWN", "SUBMISSION_UNKNOWN"))
        self.assertEqual(transition.call_args.args[5]["googleHttpStatus"], 429)


if __name__ == "__main__":
    unittest.main()
