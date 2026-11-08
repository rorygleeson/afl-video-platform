"""Stage 5A offline contract tests. Never connect to Google or charge."""
import base64
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "google_video_worker" / "handler.py"
sys.path.insert(0, str(ROOT / "src" / "api"))


def load_worker():
    import boto3
    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    transport = types.ModuleType("google.auth.transport")
    requests = types.ModuleType("google.auth.transport.requests")
    auth.load_credentials_from_dict = MagicMock()
    requests.Request = MagicMock()
    google.auth = auth
    auth.transport = transport
    transport.requests = requests
    env = {"PAID_GENERATION_ENABLED": "false", "GOOGLE_VIDEO_MODEL": "UNCONFIGURED",
           "UPLOAD_BUCKET": "fixture-uploads", "JOBS_TABLE": "fixture-jobs",
           "ADMISSION_TABLE": "fixture-lock", "SCENARIO_ASSET_BUCKET": "fixture-prompts",
           "GOOGLE_PROJECT_ID": "afl-video-platform"}
    modules = {"google": google, "google.auth": auth, "google.auth.transport": transport,
               "google.auth.transport.requests": requests}
    with patch.dict(sys.modules, modules), patch.dict(os.environ, env):
        with patch.object(boto3, "client", return_value=MagicMock()), patch.object(
            boto3, "resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location("stage5a_worker_test", SOURCE)
            worker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(worker)
    return worker


class GoogleVideoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.worker = load_worker()
        self.event = {"jobId": "a" * 32, "sceneId": "scene1",
                      "requestId": "request-12345678"}

    def test_public_model_gate_rejects_every_paid_action(self):
        w = self.worker
        with self.assertRaisesRegex(PermissionError, "PAID_GENERATION_DISABLED"):
            w.lambda_handler({"op": "gate"}, None)
        with self.assertRaisesRegex(PermissionError, "PAID_GENERATION_DISABLED"):
            w.lambda_handler({"op": "claim", **self.event}, None)
        with self.assertRaisesRegex(PermissionError, "PAID_GENERATION_DISABLED"):
            w.lambda_handler({"op": "submit", **self.event}, None)
        with self.assertRaisesRegex(PermissionError, "PAID_GENERATION_DISABLED"):
            w.lambda_handler({"op": "poll", **self.event}, None)
        w.S3.put_object.assert_not_called()
        w.SLOTS.put_item.assert_not_called()

    def test_health_does_not_exchange_google_credentials(self):
        w = self.worker
        result = w.lambda_handler({"op": "contract_health"}, None)
        self.assertEqual(result, {"status": "LOCKED", "model": "UNCONFIGURED",
                                  "paidGenerationEnabled": False})

    def test_inline_image_to_video_request_has_exact_fields(self):
        w = self.worker
        result = w.image_payload(b"png-data", "image/png", "Alex kicks the ball", 8)
        self.assertEqual(result["model"], "gemini-omni-1.2-flash")
        self.assertTrue(result["background"])
        self.assertEqual(result["generation_config"]["video_config"]["task"], "image_to_video")
        self.assertEqual(result["input"][1]["data"], base64.b64encode(b"png-data").decode())
        self.assertEqual(result["response_format"][0]["delivery"], "inline")
        self.assertEqual(result["response_format"][0]["duration"], "8s")
        self.assertNotIn("gcs_uri", result["response_format"][0])

    def test_scene3_duration_and_image_limit(self):
        w = self.worker
        self.assertEqual(w.image_payload(b"img", "image/webp", "prompt", 10)["response_format"][0]["duration"], "10s")
        with self.assertRaises(w.ContractError):
            w.image_payload(b"x" * (w.MAX_IMAGE_BYTES + 1), "image/png", "prompt", 8)
        with self.assertRaises(w.ContractError):
            w.image_payload(b"x", "application/octet-stream", "prompt", 8)

    def test_inline_mp4_parser_accepts_only_model_output(self):
        w = self.worker
        movie = b"\x00\x00\x00\x18ftypisom" + b"x" * 300
        reply = {"status": "completed", "steps": [
            {"type": "model_output", "content": [{
                "type": "video", "mime_type": "video/mp4",
                "data": base64.b64encode(movie).decode("ascii")}]},
        ]}
        self.assertEqual(w.get_video_content(reply), movie)

    def test_inline_parser_rejects_wrong_mime_invalid_data_and_uri(self):
        w = self.worker
        for part in ({"type": "video", "mime_type": "video/webm", "data": "abcd"},
                     {"type": "video", "mime_type": "video/mp4", "data": "not-base64!"},
                     {"type": "video", "mime_type": "video/mp4", "uri": "gs://bucket/out.mp4"}):
            with self.subTest(part=part), self.assertRaises(w.ContractError):
                w.get_video_content({"steps": [{"type": "model_output", "content": [part]}]})

    def test_google_endpoint_disallows_external_uris_without_network(self):
        w = self.worker
        with self.assertRaises(w.ContractError):
            w.google_json("GET", "https://attacker.example/steal")
        with self.assertRaises(w.ContractError):
            w.google_json("GET", w.BASE + "/../../secret")

    def test_unknown_submission_never_has_retry_in_workflow(self):
        template = (ROOT / "infrastructure" / "app" / "google-video.yaml").read_text()
        self.assertIn("PAID_GENERATION_ENABLED: 'false'", template)
        self.assertIn("GOOGLE_VIDEO_MODEL: UNCONFIGURED", template)
        self.assertIn('"StartAt":"PaidGate"', template)
        self.assertNotIn('"Retry"', template)
        self.assertNotIn("AWS::ApiGateway::", template)
        self.assertNotIn("AWS::S3::Bucket", template)

    def test_adapter_never_exposes_provider_result_bytes_in_stepfunctions(self):
        w = self.worker
        movie = b"\x00\x00\x00\x18ftypisom" + b"z" * 200
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        w.JOBS.get_item.return_value = {"Item": {"videos": {"scene1": {
            "status": "PROCESSING", "requestId": self.event["requestId"],
            "interactionId": "interact-12345"
        }}}}
        w.google_json = MagicMock(return_value={"status": "completed", "steps": [
            {"type": "model_output", "content": [{
                "type": "video", "mime_type": "video/mp4",
                "data": base64.b64encode(movie).decode()}]}
        ]})
        result = w.poll(self.event['jobId'], self.event['sceneId'], self.event['requestId'])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertNotIn("data", result)
        self.assertNotIn("video", result)
        self.assertEqual(result["resultKey"], f"videos/{self.event['jobId']}/scene1.mp4")
        w.S3.put_object.assert_called_once()
        self.assertEqual(w.S3.put_object.call_args.kwargs["ContentType"], "video/mp4")


if __name__ == "__main__":
    unittest.main()
