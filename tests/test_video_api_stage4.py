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
                "scene2": {"status": "PENDING"},
                "scene3": {"status": "PENDING"},
            },
        }
        self.ddb.get_item.return_value = {"Item": self.job}
        self.admission.get_item.return_value = {}
        self.states.start_execution.return_value = {
            "executionArn": "arn:aws:states:ap-southeast-2:123456789012:execution:video:mock"
        }

    def invoke(self, method, path, idem="test-idempotency-key-1234"):
        event = {"httpMethod": method, "path": path, "headers": {"Idempotency-Key": idem}}
        reply = self.api.lambda_handler(event, None)
        return reply["statusCode"], json.loads(reply["body"])

    def test_first_scene_mock_starts_without_google_or_replicate(self):
        code, data = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene1")
        self.assertEqual(code, 202)
        self.assertEqual(data["status"], "MOCK_REQUESTED")
        self.states.start_execution.assert_called_once()
        self.assertEqual(self.states.start_execution.call_args.kwargs["stateMachineArn"],
                         self.api.VIDEO_STATE_MACHINE_ARN)
        self.assertNotIn("google", str(self.states.start_execution.call_args).lower())

    def test_no_image_no_workflow(self):
        code, data = self.invoke("POST", f"/jobs/{self.jobid}/videos/scene2")
        self.assertEqual(code, 409)
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
