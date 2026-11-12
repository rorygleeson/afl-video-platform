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

    def test_new_worker_auth_preflight_never_invokes_video_model(self):
        w = self.worker
        with patch.object(w, "credentials", return_value="test-token") as auth:
            result = w.lambda_handler({"op": "auth_check"}, None)
        self.assertEqual(result, {"status": "AUTHENTICATED", "paidGenerationEnabled": False})
        auth.assert_called_once()
        w.S3.put_object.assert_not_called()
        w.SLOTS.put_item.assert_not_called()

    def test_new_worker_auth_preflight_reports_failure_without_secrets(self):
        w = self.worker
        with patch.object(w, "credentials", side_effect=RuntimeError("secret-value")):
            result = w.lambda_handler({"op": "auth_check"}, None)
        self.assertEqual(result["status"], "AUTHENTICATION_FAILED")
        self.assertNotIn("secret-value", str(result))
        w.S3.put_object.assert_not_called()

    def test_new_worker_auth_preflight_categorises_google_iam_without_leaking_details(self):
        w = self.worker
        with patch.object(w, "credentials",
                          side_effect=RuntimeError("iamcredentials.googleapis.com PERMISSION_DENIED private-token")):
            result = w.lambda_handler({"op": "auth_check"}, None)
        self.assertEqual(result, {
            "status": "AUTHENTICATION_FAILED",
            "errorCode": "GOOGLE_SERVICE_ACCOUNT_IMPERSONATION_DENIED",
            "exceptionType": "RuntimeError",
        })
        self.assertNotIn("private-token", str(result))

    def test_error_diagnostic_never_returns_provider_message(self):
        w=self.worker
        with patch.object(w, "credentials",
            side_effect=RuntimeError("Could not load AWS security credentials; TOP_SECRET")):
            result=w.lambda_handler({"op":"auth_check"},None)
        self.assertEqual(result["errorCode"],"GOOGLE_AWS_CREDENTIAL_SOURCE_ERROR")
        self.assertEqual(result["exceptionType"],"RuntimeError")
        self.assertNotIn("TOP_SECRET",str(result))

    def test_auth_probe_stays_non_billable_even_if_paid_mode_enabled(self):
        w = self.worker
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        with patch.object(w, "credentials", return_value="test-token"), patch.object(w, "google_json") as paid:
            result = w.lambda_handler({"op": "auth_check"}, None)
        self.assertEqual(result["status"], "AUTHENTICATED")
        paid.assert_not_called()

    def test_free_scene1_preflight_checks_s3_and_never_calls_google(self):
        w = self.worker
        self.assertEqual(w.PAID_ENABLED, "false")
        job = "a" * 32
        scene = {"status": "COMPLETED", "resultKey": f"results/{job}/scene1.png"}
        w.JOBS.get_item.return_value = {"Item": {"jobId": job, "scenes": {"scene1": scene}}}
        w.S3.get_object.return_value = {
            "ContentType": "image/png", "Body": __import__("io").BytesIO(b"real-image-bytes")}
        with patch.object(w, "render_scene", return_value={
            "durationSeconds": 5, "videoGenerationEnabled": False, "prompt": "Ready to run"
        }), patch.object(w, "google_json") as paid:
            data = w.lambda_handler({"op": "scene_preflight", "jobId": job,
                                    "sceneId": "scene1", "requestId": "freepreflight"}, None)
        self.assertEqual(data["status"], "READY")
        self.assertEqual(data["imageBytes"], len(b"real-image-bytes"))
        self.assertFalse(data["paidGenerationEnabled"])
        self.assertFalse(data["googleSubmissionMade"])
        paid.assert_not_called()
        w.SLOTS.put_item.assert_not_called()
        w.JOBS.update_item.assert_not_called()

    def test_preflight_scene2_and_scene3_free_even_in_paid_mode(self):
        w = self.worker
        job = "a" * 32
        for scene, duration in (("scene2", 8), ("scene3", 10)):
            w.JOBS.get_item.return_value = {"Item": {"jobId": job,
                "scenes": {scene: {"status": "COMPLETED", "resultKey": f"results/{job}/{scene}.png"}}}}
            w.S3.get_object.return_value = {"ContentType": "image/png",
                "Body": __import__("io").BytesIO(b"image")}
            with patch.object(w, "render_scene", return_value={
                "durationSeconds": duration, "videoGenerationEnabled": False, "prompt": "Prompt"
            }), patch.object(w, "google_json") as paid:
                result = w.scene_preflight(job, scene)
            self.assertEqual(result["durationSeconds"], duration)
            paid.assert_not_called()
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        w.S3.get_object.return_value = {"ContentType": "image/png",
            "Body": __import__("io").BytesIO(b"image")}
        with patch.object(w, "render_scene", return_value={
            "durationSeconds": 10, "videoGenerationEnabled": False, "prompt": "Prompt"
        }), patch.object(w, "google_json") as paid:
            self.assertTrue(w.scene_preflight(job, "scene3")["modelConfigured"])
        paid.assert_not_called()

    def test_preflight_missing_or_expired_image_fails_closed(self):
        w = self.worker
        job = "a" * 32
        w.JOBS.get_item.return_value = {"Item": {
            "jobId": job, "scenes": {"scene1": {"status": "COMPLETED",
            "resultKey": f"results/{job}/scene1.png"}}}}
        w.S3.get_object.side_effect = RuntimeError("NoSuchKey")
        with patch.object(w, "render_scene", return_value={
            "durationSeconds": 5, "videoGenerationEnabled": False, "prompt": "Safe"
        }), patch.object(w, "google_json") as paid:
            with self.assertRaises(RuntimeError):
                w.scene_preflight(job, "scene1")
        paid.assert_not_called()
        w.SLOTS.put_item.assert_not_called()

    def test_paid_gate_requires_dynamic_job_scene_approval(self):
        w = self.worker
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        job = self.event["jobId"]
        w.JOBS.get_item.return_value = {"Item": {"paidVideoApprovals": {
            "scene1": {"status": "REQUESTED", "executionName": "real-" + job + "-scene1"},
            "scene2": {"status": "REQUESTED", "executionName": "real-" + job + "-scene2"},
        }}}
        for scene in ("scene1", "scene2"):
            self.assertEqual(w.lambda_handler({"op": "gate", "jobId": job,
                                               "sceneId": scene}, None)["status"], "ENABLED")
        with self.assertRaises(PermissionError):
            w.lambda_handler({"op": "gate", "jobId": job, "sceneId": "scene3"}, None)
        with self.assertRaises(PermissionError):
            w.lambda_handler({"op": "gate", "jobId": "b"*32, "sceneId": "scene1"}, None)
        w.JOBS.update_item.assert_not_called()
        w.SLOTS.put_item.assert_not_called()

    def test_paid_claim_ignores_legacy_global_slot_but_prevents_duplicate_scene(self):
        w = self.worker
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        job, request = self.event["jobId"], self.event["requestId"]
        w.JOBS.get_item.return_value = {"Item": {
            "paidVideoApprovals": {"scene1": {
                "status": "REQUESTED", "executionName": "real-" + job + "-scene1"}},
            "scenes": {"scene1": {"status": "COMPLETED",
                                 "resultKey": "results/" + job + "/scene1.png"}},
            "realVideos": {}}}
        self.assertEqual(w.claim(job, "scene1", request)["status"], "PREPARED")
        self.assertFalse(any(call.kwargs.get("Key", {}).get("slotId")
                             for call in w.SLOTS.get_item.call_args_list))
        w.SLOTS.put_item.assert_not_called()
        w.SLOTS.delete_item.assert_not_called()
        writes = w.JOBS.update_item.call_args_list
        self.assertEqual(len(writes), 2)
        self.assertIn("attribute_not_exists(realVideos.#scene)",
                      writes[1].kwargs["ConditionExpression"])

    def test_unbillable_validation_failure_marks_failed_and_releases_slot(self):
        w = self.worker
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        job = self.event["jobId"]
        request = self.event["requestId"]
        w.JOBS.get_item.return_value = {"Item": {
            "paidVideoApprovals": {"scene1": {
                "status": "REQUESTED", "executionName": "real-" + job + "-scene1"
            }},
            "realVideos": {"scene1": {
                "status": "PREPARED", "requestId": request, "sourceImageKey": "missing"
            }},
        }}
        with (
            patch.object(w, "render_scene", side_effect=ValueError("missing prompt")),
            patch.object(w, "transition") as transition,
            patch.object(w, "release") as release,
            patch.object(w, "google_json") as billable,
        ):
            with self.assertRaises(ValueError):
                w.submit(job, "scene1", request)
        transition.assert_called_once_with(
            job, "scene1", request, "PREPARED", "FAILED",
            {"errorCode": "PRE_SUBMISSION_VALIDATION_FAILED"})
        release.assert_called_once_with(job, "scene1", request)
        billable.assert_not_called()

    def test_submission_diagnostic_is_scene_scoped_and_never_retries(self):
        # Offline regression for all three scenes. No Google calls are made.
        w = self.worker
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        job, request = self.event["jobId"], self.event["requestId"]
        for scene in ("scene1", "scene2", "scene3"):
            w.JOBS.get_item.return_value = {"Item": {
                "paidVideoApprovals": {scene: {
                    "status": "REQUESTED",
                    "executionName": "real-" + job + "-" + scene}},
                "realVideos": {scene: {
                    "status": "PREPARED", "requestId": request,
                    "sourceImageKey": "results/" + job + "/" + scene + ".png"}}}}
            w.S3.get_object.return_value = {
                "ContentType": "image/png",
                "Body": __import__("io").BytesIO(b"img")}
            with (patch.object(w, "render_scene", return_value={
                      "durationSeconds": 10 if scene == "scene3" else (5 if scene == "scene1" else 8),
                      "videoGenerationEnabled": True, "prompt": "Test"}),
                  patch.object(w, "google_json",
                      side_effect=w.ContractError("Google POST HTTP 403")) as google,
                  patch.object(w, "transition") as transition):
                with self.assertRaisesRegex(w.UnknownSubmission, "MANUAL_RECONCILIATION"):
                    w.submit(job, scene, request)
                google.assert_called_once()
                self.assertEqual(transition.call_args_list[0].args[:5],
                                 (job, scene, request, "PREPARED", "SUBMISSION_UNKNOWN"))
                self.assertEqual(transition.call_args_list[1].args,
                                 (job, scene, request, "SUBMISSION_UNKNOWN",
                                  "SUBMISSION_UNKNOWN", {"errorCode": "GOOGLE_HTTP_403"}))
            w.SLOTS.put_item.assert_not_called()

    def test_read_only_diagnosis_uses_recorded_interaction_and_never_submits_paid_request(self):
        w = self.worker
        job = "a" * 32
        scene = "scene3"
        w.JOBS.get_item.return_value = {"Item": {
            "paidVideoApprovals": {scene: {"status": "REQUESTED"}},
            "realVideos": {scene: {
                "status": "FAILED", "interactionId": "known-interaction-123"}}}}
        with patch.object(w, "google_json", return_value={
            "status": "failed",
            "errors": [{"code": "safety_policy",
                        "message": "Blocked by safety policy: private prompt and image bytes"}],
            "input": {"secret": "private selfie payload"},
        }) as google:
            result = w.lambda_handler({"op": "diagnose_failed_interaction",
                                       "jobId": job, "sceneId": scene}, None)
        google.assert_called_once_with(
            "GET", w.BASE + "/known-interaction-123", max_bytes=256 * 1024)
        self.assertEqual(result["status"], "READ_ONLY_DIAGNOSTIC")
        self.assertEqual(result["errorCategory"], "SAFETY_OR_POLICY")
        self.assertEqual(result["errorCode"], "safety_policy")
        self.assertFalse(result["paidGenerationStarted"])
        self.assertNotIn("private", str(result))
        self.assertNotIn("selfie", str(result))
        w.S3.put_object.assert_not_called()
        w.SLOTS.put_item.assert_not_called()

    def test_diagnosis_rejects_unfinished_or_wrong_scene_without_google_call(self):
        w = self.worker
        job = "a" * 32
        w.JOBS.get_item.return_value = {"Item": {
            "paidVideoApprovals": {"scene3": {"status": "REQUESTED"}},
            "realVideos": {"scene3": {
                "status": "PROCESSING", "interactionId": "known-interaction"}}}}
        with patch.object(w, "google_json") as google:
            with self.assertRaisesRegex(w.ContractError, "failed paid"):
                w.lambda_handler({"op": "diagnose_failed_interaction",
                                  "jobId": job, "sceneId": "scene3"}, None)
            google.assert_not_called()

    def test_diagnosis_without_provider_explanation_returns_explicit_unknown(self):
        w = self.worker
        job = "a" * 32
        w.JOBS.get_item.return_value = {"Item": {
            "paidVideoApprovals": {"scene3": {"status": "REQUESTED"}},
            "realVideos": {"scene3": {
                "status": "FAILED", "interactionId": "known-interaction"}}}}
        with patch.object(w, "google_json", return_value={"status": "failed"}):
            result = w.lambda_handler({"op": "diagnose_failed_interaction",
                                       "jobId": job, "sceneId": "scene3"}, None)
        self.assertEqual(result["errorCategory"], "UNSPECIFIED")
        self.assertEqual(result["reason"], "GOOGLE_DID_NOT_SUPPLY_A_CLASSIFIABLE_REASON")
        self.assertFalse(result["providerMessagePresent"])

    def test_inline_image_to_video_request_has_exact_fields(self):
        w = self.worker
        result = w.image_payload(b"png-data", "image/png", "Alex kicks the ball", 8)
        self.assertEqual(result["model"], "gemini-omni-1.2-flash")
        self.assertTrue(result["background"])
        self.assertEqual(result["generation_config"]["video_config"]["task"], "image_to_video")
        self.assertEqual(result["input"][1]["data"], base64.b64encode(b"png-data").decode())
        self.assertEqual(result["response_format"][0]["delivery"], "inline")
        self.assertEqual(result["response_format"][0]["duration"], "8s")
        self.assertEqual(w.image_payload(b"png-data", "image/png", "Scene 1 run", 5)["response_format"][0]["duration"], "5s")
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
        self.assertIn("PAID_GENERATION_ENABLED: !If", template)
        self.assertIn("GOOGLE_VIDEO_MODEL: !If", template)
        self.assertIn('"StartAt":"PaidGate"', template)
        self.assertIn('"Retry":[{"ErrorEquals":["States.TaskFailed"]', template)
        self.assertNotIn('"Submit":{"Type":"Task","Retry"', template)
        self.assertIn("Timeout: 300", template)
        self.assertIn("timeout=150", SOURCE.read_text())
        self.assertNotIn("AWS::ApiGateway::", template)
        self.assertNotIn("AWS::S3::Bucket", template)

    def test_adapter_never_exposes_provider_result_bytes_in_stepfunctions(self):
        w = self.worker
        movie = b"\x00\x00\x00\x18ftypisom" + b"z" * 200
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"

        w.JOBS.get_item.return_value = {"Item": {"paidVideoApprovals": {"scene1": {"status": "REQUESTED", "executionName": "real-" + self.event["jobId"] + "-scene1"}}, "realVideos": {"scene1": {
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
