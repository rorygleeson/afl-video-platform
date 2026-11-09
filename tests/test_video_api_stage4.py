"""Stage 4 API contract/safety regression tests (no AWS calls)."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "src" / "api" / "handler.py"
sys.path.insert(0, str(ROOT / "src" / "api"))


class VideoStage4ApiTests(unittest.TestCase):
    def setUp(self):
        import boto3
        env = {
            "UPLOAD_BUCKET": "fake-uploads", "JOBS_TABLE": "fake-jobs",
            "FRONTEND_ORIGIN": "https://test.example",
            "IMAGE_STATE_MACHINE_ARN": "arn:aws:states:ap-southeast-2:123456789012:stateMachine:image",
            "VIDEO_STATE_MACHINE_ARN": "arn:aws:states:ap-southeast-2:123456789012:stateMachine:video",
            "VIDEO_MODE": "MOCK_ONLY", "VIDEO_ADMISSION_TABLE": "fake-admission",
            "AWS_REGION": "ap-southeast-2",
        }
        self.s3 = MagicMock()
        self.states = MagicMock()
        self.ddb = MagicMock()
        self.admission = MagicMock()
        def client(service, **kw):
            return {"s3": self.s3, "stepfunctions": self.states,
                    "lambda": MagicMock()}[service]
        def resource(service):
            res = MagicMock()
            res.Table.side_effect = lambda name: self.ddb if name == "fake-jobs" else self.admission
            return res
        with patch.dict(os.environ, env), patch.object(boto3, "client", side_effect=client), patch.object(
                boto3, "resource", side_effect=resource):
            spec = importlib.util.spec_from_file_location("stage4_api_under_test", API)
            self.api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.api)
        self.jobid = "a" * 32
        self.job = {
            "jobId": self.jobid, "status": "COMPLETED", "scenarioId": "afl-grand-final-winning-kick",
            "team": "geelong", "createdAt": "2026-11-08T00:00:00Z",
            "scenes": {
                "scene1": {"status": "COMPLETED", "resultKey": f"results/{self.jobid}/scene1.png"},
                "scene2": {"status": "COMPLETED", "resultKey": f"results/{self.jobid}/scene2.png"},
                "scene3": {"status": "COMPLETED", "resultKey": f"results/{self.jobid}/scene3.png"},
            },
        }
        self.ddb.get_item.return_value = {"Item": self.job}
        self.admission.get_item.return_value = {}
        self.states.start_execution.return_value = {
            "executionArn": "arn:aws:states:ap-southeast-2:123456789012:execution:video:mock"
        }

    def invoke(self, method, path, idem="request-example-20261108"):
        event = {"httpMethod": method, "path": path, "headers": {"Idempotency-Key": idem}}
        reply = self.api.lambda_handler(event, None)
        return reply["statusCode"], json.loads(reply["body"])

    def test_paid_worker_google_connectivity_is_free_and_uses_actual_worker(self):
        import io
        self.api.GOOGLE_WORKER_ARN = "arn:aws:lambda:ap-southeast-2:123456789012:function:paid-worker"
        self.api.LAMBDA.invoke.return_value = {"Payload": io.BytesIO(
            json.dumps({"status":"AUTHENTICATED", "paidGenerationEnabled":False}).encode())}
        code, data = self.invoke("GET", "/video/paid-google-auth-check")
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "AUTHENTICATED")
        self.assertIs(data["paidGenerationTriggered"], False)
        self.assertEqual(self.api.LAMBDA.invoke.call_args.kwargs["FunctionName"],
                         self.api.GOOGLE_WORKER_ARN)
        self.assertEqual(json.loads(self.api.LAMBDA.invoke.call_args.kwargs["Payload"]),
                         {"op":"auth_check"})
        self.states.start_execution.assert_not_called()

    def test_failed_google_diagnostic_is_read_only_and_uses_existing_worker(self):
        import io
        self.api.GOOGLE_WORKER_ARN = "arn:aws:lambda:ap-southeast-2:123456789012:function:paid-worker"
        self.job["paidVideoApprovals"] = {"scene3": {"status": "REQUESTED"}}
        self.job["realVideos"] = {"scene3": {
            "status": "FAILED", "errorCode": "GOOGLE_FAILED",
            "interactionId": "known-provider-id"}}
        self.api.LAMBDA.invoke.return_value = {"Payload": io.BytesIO(
            json.dumps({"status": "READ_ONLY_DIAGNOSTIC", "providerStatus": "failed",
                        "errorCategory": "UNSPECIFIED",
                        "paidGenerationStarted": False}).encode())}
        code, data = self.invoke("GET",
            "/jobs/" + self.jobid + "/real-videos/scene3/diagnostics")
        self.assertEqual(code, 200)
        self.assertEqual(data["diagnostic"]["providerStatus"], "failed")
        sent = json.loads(self.api.LAMBDA.invoke.call_args.kwargs["Payload"])
        self.assertEqual(sent, {"op": "diagnose_failed_interaction",
                                "jobId": self.jobid, "sceneId": "scene3"})
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()
        code, data = self.invoke("GET",
            "/jobs/" + self.jobid + "/real-videos/scene2/diagnostics")
        self.assertEqual(code, 409)
        self.api.LAMBDA.invoke.assert_called_once()

    def test_real_scene1_is_hard_locked_and_never_starts_sfn(self):
        self.api.REAL_VIDEO_MODE = "DISABLED"
        status, body = self.invoke("POST", "/jobs/" + self.jobid + "/real-videos/scene1")
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "PAID_GENERATION_DISABLED")
        self.states.start_execution.assert_not_called()

    def test_dynamic_paid_scene_submissions_require_exact_consent(self):
        self.api.REAL_VIDEO_MODE = "ENABLED"
        self.api.GOOGLE_STATE_MACHINE_ARN = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:real"
        for n in (1, 2, 3):
            scene = "scene" + str(n)
            event = {"httpMethod": "POST",
                     "path": "/jobs/" + self.jobid + "/real-videos/" + scene,
                     "headers": {"Idempotency-Key": "request-example-20261108"},
                     "body": json.dumps({"confirmPaidScene": scene})}
            self.states.reset_mock()
            self.ddb.reset_mock()
            self.s3.head_object.return_value = {"ContentLength": 100}
            status, body = self.api.lambda_handler(event, None)["statusCode"], None
            self.assertEqual(status, 202)
            self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                             "real-" + self.jobid + "-" + scene)
            self.assertEqual(self.states.start_execution.call_args.kwargs["input"],
                             json.dumps({"jobId": self.jobid, "sceneId": scene}, separators=(",", ":")))
            self.assertTrue(any("paidVideoApprovals" in c.kwargs.get("UpdateExpression", "")
                                for c in self.ddb.update_item.call_args_list))
            event["body"] = "{}"
            self.states.reset_mock()
            self.assertEqual(self.api.lambda_handler(event, None)["statusCode"], 400)
            self.states.start_execution.assert_not_called()
        self.api.REAL_VIDEO_MODE = "DISABLED"

    def test_other_paid_scene_is_independent_of_processing_scene(self):
        self.api.REAL_VIDEO_MODE = "ENABLED"
        self.api.GOOGLE_STATE_MACHINE_ARN = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:real"
        self.job["paidVideoApprovals"] = {"scene1": {"status": "REQUESTED"}}
        self.job["realVideos"] = {"scene1": {"status": "PROCESSING"}}
        self.admission.get_item.return_value = {"Item": {"slotId": "GLOBAL_VIDEO_SLOT_0"}}
        event = {"httpMethod": "POST", "path": "/jobs/" + self.jobid + "/real-videos/scene2",
                 "headers": {"Idempotency-Key": "request-example-20261108"},
                 "body": json.dumps({"confirmPaidScene": "scene2"})}
        response = self.api.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 202)
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         "real-" + self.jobid + "-scene2")
        self.admission.get_item.assert_not_called()
        self.api.REAL_VIDEO_MODE = "DISABLED"

    def test_failed_sfn_is_shown_as_review_required_not_new_paid_attempt(self):
        self.api.GOOGLE_STATE_MACHINE_ARN = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:afl-app-dev-google-omni"
        self.job["paidVideoApprovals"] = {"scene1": {
            "status": "REQUESTED", "executionName": "real-" + self.jobid + "-scene1"}}
        self.states.describe_execution.return_value = {"status": "FAILED"}
        result = self.api.video_scene_public(self.job, "scene1")
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["errorCode"], "WORKFLOW_FAILED")
        self.assertNotIn("url", result)
        self.assertIn(":execution:afl-app-dev-google-omni:", 
                      self.states.describe_execution.call_args.kwargs["executionArn"])
        self.states.start_execution.assert_not_called()

    def test_approval_marks_real_request_pending_not_mock_completed(self):
        self.job["videos"] = {"scene1": {"status": "MOCK_COMPLETED"}}
        self.job["paidVideoApprovals"] = {"scene1": {"status": "REQUESTED"}}
        result = self.api.video_scene_public(self.job, "scene1")
        self.assertEqual(result["mode"], "REAL")
        self.assertEqual(result["status"], "REQUESTED")
        self.assertNotIn("url", result)

    def test_failed_paid_execution_sanitizes_google_http_error(self):
        self.api.GOOGLE_STATE_MACHINE_ARN = (
            "arn:aws:states:ap-southeast-2:123456789012:stateMachine:afl-app-dev-google-omni")
        self.job["paidVideoApprovals"] = {
            "scene1": {"status": "REQUESTED",
                       "executionName": "real-" + self.jobid + "-scene1"}}
        self.job["realVideos"] = {"scene1": {"status": "SUBMISSION_UNKNOWN"}}
        self.states.describe_execution.return_value = {"status": "FAILED"}
        self.states.get_execution_history.return_value = {"events": [{
            "taskFailedEventDetails": {
                "cause": "Google POST HTTP 403 private-token=must-never-be-leaked"}
        }]}
        public = self.api.video_scene_public(self.job, "scene1")
        self.assertEqual(public["status"], "REVIEW_REQUIRED")
        self.assertEqual(public["failureCode"], "GOOGLE_HTTP_403")
        self.assertNotIn("private-token", str(public))
        self.assertEqual(self.states.get_execution_history.call_args.kwargs[
            "includeExecutionData"], False)
        self.states.start_execution.assert_not_called()

    def test_mock_results_cannot_masquerade_as_real_mp4(self):
        self.job["videos"] = {"scene1": {"status": "MOCK_COMPLETED"}}
        self.job["realVideos"] = {"scene1": {"status": "PROCESSING", "requestId": "real-1"}}
        result = self.api.video_scene_public(self.job, "scene1")
        self.assertEqual(result["mode"], "REAL")
        self.assertEqual(result["status"], "PROCESSING")
        self.assertNotIn("url", result)

    def test_first_scene_mock_starts_without_google_or_replicate(self):
        code, data = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1")
        self.assertEqual(code, 202)
        self.assertEqual(data["status"], "MOCK_REQUESTED")
        self.states.start_execution.assert_called_once()
        self.assertEqual(self.states.start_execution.call_args.kwargs["stateMachineArn"],
                         self.api.VIDEO_STATE_MACHINE_ARN)
        self.assertNotIn("google", str(self.states.start_execution.call_args).lower())

    def test_no_image_no_workflow(self):
        self.job["scenes"]["scene2"] = {"status": "PENDING"}
        code, data = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene2")
        self.assertEqual(code, 409)
        self.states.start_execution.assert_not_called()

    def test_scene1_rejects_if_other_reference_images_are_missing(self):
        self.job["scenes"]["scene3"] = {"status": "PENDING"}
        code, data = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1")
        self.assertEqual(code, 409)
        self.assertIn("all three", data["error"])
        self.states.start_execution.assert_not_called()

    def test_global_busy_rejects_without_execution(self):
        self.admission.get_item.return_value = {"Item": {"slotId": "GLOBAL_VIDEO_SLOT_0"}}
        code, _ = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1")
        self.assertEqual(code, 409)
        self.states.start_execution.assert_not_called()

    def test_repeated_idempotency_key_gets_same_execution_name(self):
        self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1", idem="same-request-key-0001")
        self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1", idem="same-request-key-0001")
        calls = self.states.start_execution.call_args_list
        self.assertEqual(calls[0].kwargs["name"], calls[1].kwargs["name"])

    def test_new_request_can_retry_after_admission_rejection(self):
        self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1", idem="first-request-key-0001")
        self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1", idem="second-request-key-0002")
        calls = self.states.start_execution.call_args_list
        self.assertNotEqual(calls[0].kwargs["name"], calls[1].kwargs["name"])

    def test_existing_mock_record_not_restarted(self):
        self.job["videos"] = {"scene1": {"status": "MOCK_COMPLETED",
                                            "requestId": "request-existing"}}
        code, data = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1")
        self.assertEqual(code, 200)
        self.assertEqual(data["video"]["status"], "MOCK_COMPLETED")
        self.states.start_execution.assert_not_called()

    def test_mock_status_has_no_playable_video_url(self):
        self.job["videos"] = {"scene1": {"status": "MOCK_COMPLETED",
                                            "requestId": "request-existing"}}
        code, data = self.invoke("GET", f"/jobs/{self.jobid}/videos")
        self.assertEqual(code, 200)
        self.assertEqual(data["videos"]["scene1"]["status"], "MOCK_COMPLETED")
        self.assertTrue(data["videos"]["scene1"]["mock"])
        self.assertNotIn("url", data["videos"]["scene1"])
        self.s3.generate_presigned_url.assert_not_called()

    def test_invalid_key_cannot_start(self):
        code, _ = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1", idem="!")
        self.assertEqual(code, 400)
        self.states.start_execution.assert_not_called()

    def test_unknown_scene_is_not_routable(self):
        code, _ = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene4")
        self.assertEqual(code, 404)
        self.states.start_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
