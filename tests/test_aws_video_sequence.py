"""Offline AWS-owned sequential paid video progression tests. No cloud calls."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "src/api/handler.py"
sys.path.insert(0, str(API.parent))
STATE_ARN = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:afl-app-dev-google-omni"


class AwsVideoSequenceTests(unittest.TestCase):
    def setUp(self):
        import boto3
        self.s3, self.states, self.ddb = MagicMock(), MagicMock(), MagicMock()
        env = {
            "UPLOAD_BUCKET": "test-uploads", "JOBS_TABLE": "test-jobs",
            "FRONTEND_ORIGIN": "https://example.test",
            "IMAGE_STATE_MACHINE_ARN": "arn:aws:states:ap-southeast-2:123456789012:stateMachine:images",
            "GOOGLE_STATE_MACHINE_ARN": STATE_ARN,
            "REAL_VIDEO_MODE": "ENABLED",
            "AWS_REGION": "ap-southeast-2",
        }
        def client(service, **kwargs):
            return {"s3": self.s3, "stepfunctions": self.states, "lambda": MagicMock()}[service]
        def resource(service, **kwargs):
            r = MagicMock()
            r.Table.return_value = self.ddb
            return r
        with patch.dict(os.environ, env), patch.object(boto3, "client", side_effect=client), patch.object(
                boto3, "resource", side_effect=resource):
            spec = importlib.util.spec_from_file_location("api_sequence_under_test", API)
            self.api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.api)
        self.id = "a" * 32
        self.job = {
            "jobId": self.id, "scenes": {
                f"scene{i}": {"status": "COMPLETED", "resultKey": f"results/{self.id}/scene{i}.png"}
                for i in (1, 2, 3)},
            "paidVideoApprovals": {}, "realVideos": {},
        }
        self.ddb.get_item.return_value = {"Item": self.job}
        self.s3.head_object.return_value = {"ContentLength": 2048}

    def route(self, body=None, path=None):
        event = {"httpMethod": "POST",
                 "path": path or f"/jobs/{self.id}/real-videos/sequence",
                 "body": json.dumps(body if body is not None else {"confirmPaidSequence": True}),
                 "headers": {"Idempotency-Key": "-".join(("test", "sequence", "key", "000000000000"))}}
        reply = self.api.lambda_handler(event, None)
        return reply["statusCode"], json.loads(reply["body"])

    def completion(self, scene, *, attempt=""):
        execution = f"real-{self.id}-{scene}{attempt}"
        return {"source": "aws.states",
                "detail-type": "Step Functions Execution Status Change",
                "detail": {"status": "SUCCEEDED", "stateMachineArn": STATE_ARN,
                           "executionArn": STATE_ARN.replace(":stateMachine:", ":execution:") + ":" + execution}}

    def approve(self):
        self.job["videoSequence"] = {"status": "APPROVED", "mode": "AWS_EVENT_DRIVEN"}

    def mark_complete(self, scene, *, attempt=""):
        self.job["realVideos"][scene] = {
            "status": "COMPLETED", "resultKey": f"videos/{self.id}/{scene}.mp4"}
        self.job["paidVideoApprovals"][scene] = {
            "executionName": f"real-{self.id}-{scene}{attempt}"}

    def test_sequence_requires_explicit_consent_and_one_atomic_reservation(self):
        status, _ = self.route({"confirmPaidSequence": False})
        self.assertEqual(status, 400)
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()
        status, data = self.route()
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "SEQUENCE_APPROVED")
        self.assertEqual(data["sequence"]["mode"], "AWS_EVENT_DRIVEN")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene1")
        self.assertTrue(any("videoSequence" in c.kwargs.get("UpdateExpression", "")
                            for c in self.ddb.update_item.call_args_list))
        self.assertTrue(any("paidVideoApprovals.#scene" in c.kwargs.get("UpdateExpression", "")
                            for c in self.ddb.update_item.call_args_list))
        self.assertEqual(self.route({"confirmPaidSequence": False})[0], 400)

    def test_existing_approval_does_not_restart_paid_generation(self):
        self.approve()
        status, data = self.route()
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "ALREADY_APPROVED")
        self.states.start_execution.assert_not_called()

    def test_scene1_completed_event_starts_scene2_exactly_once(self):
        self.approve()
        self.mark_complete("scene1")
        event = self.completion("scene1")
        result = self.api.lambda_handler(event, None)
        self.assertEqual(result, {"status": "NEXT_SCENE_REQUESTED", "sceneId": "scene2"})
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene2")
        self.job["paidVideoApprovals"]["scene2"] = {"executionName": f"real-{self.id}-scene2"}
        replay = self.api.lambda_handler(event, None)
        self.assertEqual(replay["status"], "ALREADY_STARTED")
        self.states.start_execution.assert_called_once()

    def test_scene2_completed_event_starts_scene3_only_with_both_real_mp4s(self):
        self.approve()
        self.mark_complete("scene1")
        self.mark_complete("scene2")
        result = self.api.lambda_handler(self.completion("scene2"), None)
        self.assertEqual(result["sceneId"], "scene3")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene3")
        self.assertGreaterEqual(self.s3.head_object.call_count, 3)

    def test_failed_scene_never_advances_or_paid_retries(self):
        self.approve()
        self.job["paidVideoApprovals"]["scene1"] = {"executionName": f"real-{self.id}-scene1"}
        self.job["realVideos"]["scene1"] = {"status": "FAILED", "providerFinalStatus": "failed",
                                            "providerErrorCode": "too_many_requests"}
        result = self.api.lambda_handler(self.completion("scene1"), None)
        self.assertEqual(result["status"], "WAITING_FOR_MANUAL_RETRY_OR_REVIEW")
        self.states.start_execution.assert_not_called()

    def test_manually_retried_scene2_success_triggers_scene3(self):
        self.approve()
        self.mark_complete("scene1")
        self.mark_complete("scene2", attempt="-r2")
        result = self.api.lambda_handler(self.completion("scene2", attempt="-r2"), None)
        self.assertEqual(result["sceneId"], "scene3")
        self.states.start_execution.assert_called_once()

    def test_no_user_consent_no_background_paid_work(self):
        self.mark_complete("scene1")
        result = self.api.lambda_handler(self.completion("scene1"), None)
        self.assertEqual(result["status"], "IGNORED_NO_SEQUENCE")
        self.states.start_execution.assert_not_called()

    def test_wrong_workflow_and_stale_attempt_are_ignored(self):
        self.approve()
        self.mark_complete("scene1", attempt="-r2")
        old = self.api.lambda_handler(self.completion("scene1"), None)
        self.assertEqual(old["status"], "IGNORED_SUPERSEDED_ATTEMPT")
        event = self.completion("scene1", attempt="-r2")
        event["detail"]["stateMachineArn"] = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:other"
        self.assertEqual(self.api.lambda_handler(event, None)["status"], "IGNORED")
        self.states.start_execution.assert_not_called()

    def test_original_paid_request_does_not_imply_batch_consent(self):
        self.mark_complete("scene1")
        self.assertEqual(self.api.lambda_handler(self.completion("scene1"))["status"],
                         "IGNORED_NO_SEQUENCE")
        self.states.start_execution.assert_not_called()

    def test_sequence_approval_reuses_existing_completed_scene1(self):
        self.mark_complete("scene1")
        status, data = self.route()
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "SEQUENCE_APPROVED")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene2")


if __name__ == "__main__":
    unittest.main()
