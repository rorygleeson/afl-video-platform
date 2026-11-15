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
            "ASSEMBLY_STATE_MACHINE_ARN": "arn:aws:states:ap-southeast-2:123456789012:stateMachine:afl-app-dev-assembly",
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
        self.states.describe_execution.return_value = {"status": "RUNNING"}
        # Botocore exposes an Exception subclass here, unlike MagicMock's
        # default placeholder. Exercise the non-ExecutionAlreadyExists path.
        class ExecutionAlreadyExists(Exception):
            pass
        self.states.exceptions.ExecutionAlreadyExists = ExecutionAlreadyExists

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

    def image_completion(self):
        arn = self.api.IMAGE_STATE_MACHINE_ARN
        return {"source": "aws.states",
                "detail-type": "Step Functions Execution Status Change",
                "detail": {"status": "SUCCEEDED", "stateMachineArn": arn,
                           "executionArn": arn.replace(":stateMachine:", ":execution:") + ":" + self.id}}

    def test_image_success_starts_original_video_only_with_explicit_job_consent(self):
        self.job["status"] = "COMPLETED"
        self.job["autoVideoConsent"] = True
        result = self.api.lambda_handler(self.image_completion(), None)
        self.assertEqual(result["status"], "SEQUENCE_APPROVED")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene1")
        self.assertTrue(any("paidVideoApprovals" in c.kwargs.get("UpdateExpression", "")
                            for c in self.ddb.update_item.call_args_list))

    def test_image_event_without_consent_never_starts_paid_video(self):
        self.job["status"] = "COMPLETED"
        result = self.api.lambda_handler(self.image_completion(), None)
        self.assertEqual(result["status"], "IGNORED_NOT_AUTHORISED")
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()

    def test_image_event_rejects_partial_or_unfinished_images(self):
        self.job["autoVideoConsent"] = True
        self.job["status"] = "GENERATING_IMAGES"
        self.assertEqual(self.api.lambda_handler(self.image_completion(), None)["status"],
                         "IGNORED_IMAGES_NOT_COMPLETE")
        self.job["status"] = "COMPLETED"
        self.job["scenes"]["scene2"]["status"] = "FAILED"
        self.assertEqual(self.api.lambda_handler(self.image_completion(), None)["status"],
                         "IGNORED_IMAGES_NOT_COMPLETE")
        self.states.start_execution.assert_not_called()

    def test_image_event_rejects_unauthorised_execution_name_and_wrong_status(self):
        self.job["autoVideoConsent"] = True
        self.job["status"] = "COMPLETED"
        bad = self.image_completion()
        bad["detail"]["executionArn"] += "-unexpected"
        self.assertEqual(self.api.lambda_handler(bad, None)["status"], "IGNORED")
        bad = self.image_completion()
        bad["detail"]["status"] = "FAILED"
        self.assertEqual(self.api.lambda_handler(bad, None)["status"], "IGNORED")
        self.states.start_execution.assert_not_called()

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

    def test_existing_approval_reconciles_unreserved_scene_after_lost_handoff(self):
        self.approve()
        status, data = self.route()
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "ALREADY_APPROVED")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene1")
        self.assertFalse(any("videoSequence" in c.kwargs.get("UpdateExpression", "")
                             for c in self.ddb.update_item.call_args_list))

    def test_existing_approval_does_not_restart_reserved_paid_scene(self):
        self.approve()
        self.job["paidVideoApprovals"]["scene1"] = {
            "executionName": f"real-{self.id}-scene1", "status": "REQUESTED"}
        status, data = self.route()
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "ALREADY_APPROVED")
        self.states.start_execution.assert_not_called()

    def test_existing_approval_can_resume_at_missing_scene2_safely(self):
        self.approve()
        self.mark_complete("scene1")
        status, data = self.route()
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "ALREADY_APPROVED")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene2")

    def test_existing_approval_rejects_nonapproved_status(self):
        self.job["videoSequence"] = {"status": "REVIEW_REQUIRED"}
        status, data = self.route()
        self.assertEqual(status, 409)
        self.assertEqual(data["status"], "SEQUENCE_NEEDS_REVIEW")
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

    def test_duplicate_completion_with_missing_next_execution_never_claims_started(self):
        from botocore.exceptions import ClientError
        self.approve()
        self.mark_complete("scene1")
        self.job["paidVideoApprovals"]["scene2"] = {
            "executionName": f"real-{self.id}-scene2"}
        self.states.describe_execution.side_effect = ClientError(
            {"Error":{"Code":"ExecutionDoesNotExist","Message":"not found"}},
            "DescribeExecution")
        result = self.api.lambda_handler(self.completion("scene1"), None)
        self.assertEqual(result["status"], "NEXT_SCENE_HANDOFF_NEEDS_REVIEW")
        self.states.start_execution.assert_not_called()

    def test_lost_sfn_start_response_never_claims_paid_scene_has_started(self):
        self.approve()
        self.mark_complete("scene1")
        self.states.start_execution.side_effect = RuntimeError("Uncertain AWS acknowledgement")
        result = self.api.lambda_handler(self.completion("scene1"), None)
        self.assertEqual(result["status"], "NEXT_SCENE_HANDOFF_NEEDS_REVIEW")
        self.states.start_execution.assert_called_once()

    def test_missing_workflow_reports_review_after_two_minutes_not_indefinite_wait(self):
        from botocore.exceptions import ClientError
        self.approve()
        self.job["paidVideoApprovals"]["scene1"] = {
            "executionName": f"real-{self.id}-scene1",
            "createdAt": "2026-11-12T00:00:00+00:00"}
        self.states.describe_execution.side_effect = ClientError(
            {"Error":{"Code":"ExecutionDoesNotExist","Message":"not found"}},
            "DescribeExecution")
        status = self.api.video_scene_public(self.job, "scene1")
        self.assertEqual(status["status"], "REVIEW_REQUIRED")
        self.assertEqual(status["failureCode"], "WORKFLOW_EXECUTION_NOT_FOUND")
        self.assertEqual(status["retryBlockedReason"], "WORKFLOW_REVIEW_REQUIRED")
        self.assertFalse(status["retryAvailable"])

    def test_scene2_completed_event_starts_scene3_only_with_both_real_mp4s(self):
        self.approve()
        self.mark_complete("scene1")
        self.mark_complete("scene2")
        result = self.api.lambda_handler(self.completion("scene2"), None)
        self.assertEqual(result["sceneId"], "scene3")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.id}-scene3")
        self.assertGreaterEqual(self.s3.head_object.call_count, 3)

    def test_scene3_success_starts_one_ffmpeg_assembly_from_existing_clips(self):
        self.approve()
        for scene in ("scene1", "scene2", "scene3"):
            self.mark_complete(scene)
        result = self.api.lambda_handler(self.completion("scene3"), None)
        self.assertEqual(result["status"], "ASSEMBLY_REQUESTED")
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         "final-" + self.id)
        self.assertEqual(self.states.start_execution.call_args.kwargs["stateMachineArn"],
                         self.api.ASSEMBLY_STATE_MACHINE_ARN)
        self.assertEqual(self.s3.head_object.call_count, 4)
        self.assertTrue(any(c.kwargs.get("ConditionExpression") ==
                            "attribute_exists(jobId) AND attribute_not_exists(assembly)"
                            for c in self.ddb.update_item.call_args_list))

    def test_scene3_duplicate_event_never_starts_another_assembly(self):
        self.approve()
        for scene in ("scene1", "scene2", "scene3"):
            self.mark_complete(scene)
        self.assertEqual(self.api.lambda_handler(self.completion("scene3"), None)["status"],
                         "ASSEMBLY_REQUESTED")
        self.job["assembly"] = {"status": "QUEUED"}
        self.assertEqual(self.api.lambda_handler(self.completion("scene3"), None)["status"],
                         "ASSEMBLY_ALREADY_REQUESTED")
        self.states.start_execution.assert_called_once()

    def test_scene3_unverified_or_failed_does_not_assemble(self):
        self.approve()
        for scene in ("scene1", "scene2", "scene3"):
            self.mark_complete(scene)
        self.job["realVideos"]["scene3"]["status"] = "FAILED"
        self.assertEqual(self.api.lambda_handler(self.completion("scene3"), None)["status"],
                         "WAITING_FOR_MANUAL_RETRY_OR_REVIEW")
        self.states.start_execution.assert_not_called()
        self.job["realVideos"]["scene3"]["status"] = "COMPLETED"
        self.job["realVideos"]["scene2"]["status"] = "FAILED"
        with self.assertRaisesRegex(RuntimeError, "preflight not ready"):
            self.api.lambda_handler(self.completion("scene3"), None)
        self.states.start_execution.assert_not_called()

    def test_scene3_old_attempt_and_missing_sequence_cannot_assemble(self):
        for scene in ("scene1", "scene2", "scene3"):
            self.mark_complete(scene)
        self.assertEqual(self.api.lambda_handler(self.completion("scene3"), None)["status"],
                         "IGNORED_NO_SEQUENCE")
        self.approve()
        self.job["paidVideoApprovals"]["scene3"]["executionName"] =             f"real-{self.id}-scene3-r2"
        self.assertEqual(self.api.lambda_handler(self.completion("scene3"), None)["status"],
                         "IGNORED_SUPERSEDED_ATTEMPT")
        self.states.start_execution.assert_not_called()

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
        self.assertEqual(self.api.lambda_handler(self.completion("scene1"), None)["status"],
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
