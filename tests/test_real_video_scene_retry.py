"""Offline paid video retry contract: no Google API requests in these tests."""
import json
from decimal import Decimal
import unittest
from unittest.mock import patch

from test_video_api_stage4 import VideoStage4ApiTests
from test_google_video_stage5a import load_worker


class SceneRetryTests(unittest.TestCase):
    def setUp(self):
        base = VideoStage4ApiTests()
        base.setUp()
        self.api = base.api
        self.ddb = base.ddb
        self.s3 = base.s3
        self.states = base.states
        self.job = base.job
        self.jobid = base.jobid
        self.api.REAL_VIDEO_MODE = "ENABLED"
        self.api.GOOGLE_STATE_MACHINE_ARN = (
            "arn:aws:states:ap-southeast-2:123456789012:stateMachine:real")
        self.states.describe_execution.return_value = {"status": "SUCCEEDED"}
        self.job["paidVideoApprovals"] = {}
        self.job["realVideos"] = {}
        for n in (1, 2, 3):
            scene = "scene" + str(n)
            name = "real-" + self.jobid + "-" + scene
            self.job["paidVideoApprovals"][scene] = {
                "status": "REQUESTED", "executionName": name,
                "requestFingerprint": "original",
            }
            self.job["realVideos"][scene] = {
                "status": "FAILED", "requestId": name, "interactionId": "google-" + scene,
                "providerFinalStatus": "failed", "providerErrorCode": "too_many_requests",
                "sourceImageKey": f"results/{self.jobid}/{scene}.png",
            }
        self.ddb.get_item.return_value = {"Item": self.job}

    def retry(self, n=1, key="retry-example-20261112", body=None):
        scene = "scene" + str(n)
        event = {"httpMethod": "POST",
                 "path": f"/jobs/{self.jobid}/real-videos/{scene}/retry",
                 "headers": {"Idempotency-Key": key},
                 "body": json.dumps(body if body is not None else {"confirmRetryScene": scene})}
        res = self.api.lambda_handler(event, None)
        return res["statusCode"], json.loads(res["body"])

    def test_each_failed_scene_can_be_retried_without_touching_other_scenes(self):
        for n in (1, 2, 3):
            self.ddb.reset_mock()
            self.states.start_execution.reset_mock()
            status, payload = self.retry(n)
            self.assertEqual(status, 202)
            self.assertEqual(payload["attemptNumber"], 2)
            name = f"real-{self.jobid}-scene{n}-r2"
            self.assertEqual(self.states.start_execution.call_args.kwargs["name"], name)
            writes = self.ddb.update_item.call_args_list
            self.assertEqual(len(writes), 1)
            cmd = writes[0].kwargs
            self.assertEqual(cmd["ExpressionAttributeNames"]["#scene"], "scene" + str(n))
            self.assertEqual(cmd["ExpressionAttributeValues"][":approval"]["history"][0]
                             ["video"]["interactionId"], "google-scene" + str(n))
            self.assertIn("REMOVE realVideos.#scene", cmd["UpdateExpression"])
            self.assertIn("realVideos.#scene.requestId = :request", cmd["ConditionExpression"])

    def test_all_confirmed_google_terminal_failures_allow_independent_retry(self):
        for provider in ("failed", "cancelled", "incomplete", "budget_exceeded"):
            self.job["realVideos"]["scene2"]["providerFinalStatus"] = provider
            public = self.api.video_scene_public(self.job, "scene2")
            self.assertTrue(public["retryAvailable"], provider)
            self.ddb.reset_mock()
            self.states.start_execution.reset_mock()
            code, _ = self.retry(2, key="retry-confirmed-" + provider)
            self.assertEqual(code, 202, provider)
            self.states.start_execution.assert_called_once()

    def test_failed_scene_returns_explicit_blocker_instead_of_silent_missing_retry(self):
        self.job["realVideos"]["scene1"]["providerFinalStatus"] = None
        result = self.api.video_scene_public(self.job, "scene1")
        self.assertFalse(result["retryAvailable"])
        self.assertEqual(result["retryBlockedReason"], "GOOGLE_FAILURE_NOT_CONFIRMED")
        self.job["realVideos"]["scene1"]["status"] = "PROCESSING"
        self.states.describe_execution.return_value = {"status": "FAILED"}
        result = self.api.video_scene_public(self.job, "scene1")
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertFalse(result["retryAvailable"])
        self.assertEqual(result["retryBlockedReason"], "WORKFLOW_REVIEW_REQUIRED")

    def test_never_retry_unconfirmed_or_inflight_or_successful_scene(self):
        for state, provider in (("COMPLETED", "completed"),
                                ("PROCESSING", None),
                                ("FAILED", None),
                                ("SUBMISSION_UNKNOWN", None)):
            self.job["realVideos"]["scene1"]["status"] = state
            self.job["realVideos"]["scene1"]["providerFinalStatus"] = provider
            self.assertEqual(self.retry()[0], 409)
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()

    def test_requires_previous_sfn_to_be_finished(self):
        self.states.describe_execution.return_value = {"status": "RUNNING"}
        status, _ = self.retry()
        self.assertEqual(status, 409)
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()

    def test_retry_requires_explicit_consent_and_max_ten_attempts(self):
        self.assertEqual(self.retry(body={})[0], 400)
        self.job["paidVideoApprovals"]["scene1"]["attemptNumber"] = 10
        self.assertEqual(self.retry()[0], 409)
        self.states.start_execution.assert_not_called()

    def test_ninth_retry_generates_tenth_attempt_only(self):
        scene = "scene3"
        self.job["paidVideoApprovals"][scene]["attemptNumber"] = 9
        self.job["paidVideoApprovals"][scene]["executionName"] = (
            f"real-{self.jobid}-{scene}-r9")
        self.job["paidVideoApprovals"][scene]["history"] = [
            {"approval":{"attemptNumber": i}, "video":{"status":"FAILED"}}
            for i in range(1, 9)
        ]
        self.job["realVideos"][scene]["requestId"] = (
            f"real-{self.jobid}-{scene}-r9")
        code, response = self.retry(3)
        self.assertEqual(code, 202)
        self.assertEqual(response["attemptNumber"], 10)
        self.assertEqual(self.states.start_execution.call_args.kwargs["name"],
                         f"real-{self.jobid}-{scene}-r10")
        self.assertEqual(self.ddb.update_item.call_args.kwargs
                         ["ExpressionAttributeValues"][":approval"]["history"].__len__(), 9)

    def test_retry_status_exposes_ten_attempt_limit(self):
        self.job["paidVideoApprovals"]["scene3"]["attemptNumber"] = 9
        status = self.api.video_scene_public(self.job, "scene3")
        self.assertEqual(status["maxAttempts"], 10)
        self.assertEqual(status["attemptNumber"], 9)
        self.assertIs(status["retryAvailable"], True)
        self.job["paidVideoApprovals"]["scene3"]["attemptNumber"] = 10
        status = self.api.video_scene_public(self.job, "scene3")
        self.assertIs(status["retryAvailable"], False)

    def test_duplicate_reservation_returns_conflict_without_second_paid_workflow(self):
        from botocore.exceptions import ClientError
        self.ddb.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        self.assertEqual(self.retry()[0], 409)
        self.states.start_execution.assert_not_called()

    def test_retry_approval_must_match_exact_execution_and_scene(self):
        w = load_worker()
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        approval = {"status": "REQUESTED",
                    "attemptNumber": 2,
                    "executionName": f"real-{self.jobid}-scene1-r2"}
        with patch.object(w, "get_job", return_value={"paidVideoApprovals": {"scene1": approval}}):
            w.require_paid_unlock(self.jobid, "scene1", approval["executionName"])
            with self.assertRaises(PermissionError):
                w.require_paid_unlock(self.jobid, "scene1",
                                      f"real-{self.jobid}-scene1")
            with self.assertRaises(PermissionError):
                w.require_paid_unlock(self.jobid, "scene1",
                                      f"real-{self.jobid}-scene2-r2")

    def test_dynamodb_decimal_attempt_approved_at_paid_gate(self):
        # Real boto3 Table.get_item returns Decimal('2') rather than int(2).
        w = load_worker()
        w.PAID_ENABLED = "true"
        w.MODEL = "gemini-omni-1.2-flash"
        scene = "scene3"
        name = f"real-{self.jobid}-{scene}-r2"
        approval = {"status": "REQUESTED", "attemptNumber": Decimal("2"),
                    "executionName": name}
        with patch.object(w, "get_job", return_value={
                "paidVideoApprovals": {scene: approval}}):
            w.require_paid_unlock(self.jobid, scene)
            w.require_paid_unlock(self.jobid, scene, name)
            with self.assertRaises(PermissionError):
                w.require_paid_unlock(self.jobid, scene, name + "-wrong")
            with self.assertRaises(PermissionError):
                w.require_paid_unlock(self.jobid, "scene1")
        for invalid in (Decimal("1.5"), Decimal("11"), Decimal("NaN"), True, "2"):
            approval["attemptNumber"] = invalid
            with patch.object(w, "get_job", return_value={
                    "paidVideoApprovals": {scene: approval}}):
                with self.assertRaises(PermissionError):
                    w.require_paid_unlock(self.jobid, scene)

    def test_ui_has_all_three_retry_controls_and_no_reference_regeneration(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        html = (root / "frontend/index.html").read_text()
        js = (root / "frontend/app.js").read_text()
        for n in (1, 2, 3):
            self.assertIn(f'id="retryVideoScene{n}"', html)
            self.assertIn(f'id="retryVideoResult{n}"', html)
        self.assertIn("confirmRetryScene", js)
        self.assertIn("retryAvailable", js)
        self.assertNotIn("start_images(job_id)", self.api.retry_failed_real_scene.__code__.co_names)



    def test_blocked_policy_job_is_final_and_never_offers_a_paid_retry(self):
        self.job["videoSequence"] = {
            "status": "BLOCKED", "failureCode": "CONTENT_BLOCKED",
            "failedScene": "scene2", "terminalAt": "2026-11-16T00:00:00Z",
        }
        self.job["realVideos"]["scene1"] = {
            "status": "COMPLETED",
            "resultKey": f"videos/{self.jobid}/scene1.mp4"}
        self.job["realVideos"]["scene2"]["providerErrorCategory"] = "SAFETY_OR_POLICY"
        outcome = self.api.overall_job_progress(self.job)
        self.assertEqual(outcome["stageStatus"], "BLOCKED")
        self.assertTrue(outcome["terminal"])
        self.assertEqual(outcome["outcome"], "FAILED")
        self.assertEqual(outcome["failureCode"], "CONTENT_BLOCKED")
        self.assertEqual(outcome["failedScene"], "scene2")
        self.assertTrue(outcome["canStartNewJob"])
        self.assertFalse(outcome["finalVideoReady"])
        scene = self.api.video_scene_public(self.job, "scene2")
        self.assertFalse(scene["retryAvailable"])
        self.assertEqual(scene["retryBlockedReason"], "CONTENT_BLOCKED")
        self.assertEqual(self.retry(2)[0], 409)
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()

    def test_blocked_job_rejects_sequence_approval_and_manual_scene_start(self):
        import json
        self.job["videoSequence"] = {
            "status": "BLOCKED", "failureCode": "CONTENT_BLOCKED",
            "failedScene": "scene1"}
        self.api.REAL_VIDEO_MODE = "ENABLED"
        event = {
            "httpMethod":"POST",
            "path":f"/jobs/{self.jobid}/real-videos/sequence",
            "body":json.dumps({"confirmPaidSequence":True}),
            "headers":{"Idempotency-Key":"sequence-after-block-12345"},
        }
        response = self.api.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 409)
        event["path"] = f"/jobs/{self.jobid}/real-videos/scene3"
        event["body"] = json.dumps({"confirmPaidScene":"scene3"})
        response = self.api.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 409)
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()

    def test_blocked_eventbridge_handoff_is_ignored(self):
        self.job["videoSequence"] = {
            "status": "BLOCKED", "failureCode": "CONTENT_BLOCKED",
            "failedScene": "scene2"}
        state_machine = self.api.GOOGLE_STATE_MACHINE_ARN
        event = {
            "source": "aws.states",
            "detail-type": "Step Functions Execution Status Change",
            "detail": {
                "status": "SUCCEEDED",
                "stateMachineArn": state_machine,
                "executionArn": state_machine.replace(":stateMachine:",":execution:",1)
                    + ":real-" + self.jobid + "-scene2",
            },
        }
        result = self.api.video_sequence_event(event)
        self.assertEqual(result["status"], "IGNORED_NO_SEQUENCE")
        self.states.start_execution.assert_not_called()
        self.ddb.update_item.assert_not_called()


    def test_exhausted_video_job_has_universal_final_failed_outcome(self):
        self.job["videoSequence"] = {"status": "APPROVED"}
        self.job["paidVideoApprovals"]["scene1"]["attemptNumber"] = 10
        self.job["realVideos"]["scene1"]["providerFinalStatus"] = "failed"
        result = self.api.overall_job_progress(self.job)
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "FAILED")
        self.assertEqual(result["failureCode"], "VIDEO_ATTEMPTS_EXHAUSTED")
        self.assertFalse(result["retryable"])
        self.assertTrue(result["canStartNewJob"])
        # No additional paid attempt or orchestration may begin.
        self.assertEqual(self.retry(1)[0], 409)
        self.states.start_execution.assert_not_called()

    def test_terminal_image_failure_rejects_new_video_sequence(self):
        self.job["status"] = "PARTIAL_SUCCESS"
        self.job["scenes"]["scene2"]["status"] = "FAILED"
        self.job["videoSequence"] = {}
        result = self.api.overall_job_progress(self.job)
        self.assertEqual(result["failureCode"], "REFERENCE_IMAGES_FAILED")
        import json
        event = {"httpMethod": "POST",
                 "path": f"/jobs/{self.jobid}/real-videos/sequence",
                 "headers": {"Idempotency-Key": "terminal-images-example"},
                 "body": json.dumps({"confirmPaidSequence": True})}
        self.assertEqual(self.api.lambda_handler(event, None)["statusCode"], 409)
        self.states.start_execution.assert_not_called()

    def test_failed_assembly_blocks_next_attempts_without_reprocessing(self):
        self.job["assembly"] = {"status": "FAILED", "errorCode": "MEDIA_PROCESSING_FAILED"}
        result = self.api.overall_job_progress(self.job)
        self.assertFalse(result["terminal"],
                         "A first failed FFmpeg attempt cannot end the job before its safe retry")
        self.assertNotIn("failureCode", result)
        self.assertEqual(self.retry(1)[0], 409,
                         "Cannot restart paid video after assembly has begun")
        self.states.start_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
