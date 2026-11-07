"""Stage 3 guard tests: completely offline, zero cloud or model calls."""
import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "src" / "video_mock_worker" / "handler.py"
TEMPLATE = ROOT / "infrastructure" / "app" / "video.yaml"


class VideoMockWorkerTests(unittest.TestCase):
    def load_worker(self):
        module_name = "stage3_video_mock_handler_test"
        import boto3
        with patch.dict(os.environ, {
            "MODE": "MOCK_ONLY",
            "JOBS_TABLE": "mock-jobs",
            "ADMISSION_TABLE": "mock-admission",
            "UPLOAD_BUCKET": "mock-uploads",
        }):
            with patch.object(boto3, "client") as client, patch.object(boto3, "resource") as resource:
                resource.return_value.Table.return_value = MagicMock()
                spec = importlib.util.spec_from_file_location(module_name, WORKER)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
        return module

    def test_paid_mode_cannot_import(self):
        import boto3
        with patch.dict(os.environ, {"MODE": "REAL", "JOBS_TABLE": "mock",
                                      "ADMISSION_TABLE": "mock", "UPLOAD_BUCKET": "mock"}):
            with patch.object(boto3, "client"), patch.object(boto3, "resource"):
                spec = importlib.util.spec_from_file_location("stage3_reject_real", WORKER)
                with self.assertRaisesRegex(RuntimeError, "MOCK_ONLY"):
                    spec.loader.exec_module(importlib.util.module_from_spec(spec))

    def test_bad_scene_and_request_rejected(self):
        worker = self.load_worker()
        with self.assertRaisesRegex(ValueError, "Invalid scene"):
            worker.validate({"jobId": "a" * 32, "sceneId": "scene4", "requestId": "request12345"})
        with self.assertRaisesRegex(ValueError, "Invalid request"):
            worker.validate({"jobId": "a" * 32, "sceneId": "scene1", "requestId": "!"})

    def test_claim_requires_completed_personalised_image(self):
        worker = self.load_worker()
        worker.JOBS.get_item.return_value = {"Item": {"jobId": "a" * 32, "scenes": {
            "scene1": {"status": "PENDING"}
        }}}
        with self.assertRaisesRegex(ValueError, "not completed"):
            worker.acquire("a" * 32, "scene1", "request12345")
        worker.SLOTS.put_item.assert_not_called()

    def test_claim_uses_conditional_dynamo_lock(self):
        worker = self.load_worker()
        jobid = "a" * 32
        worker.JOBS.get_item.return_value = {"Item": {"jobId": jobid, "scenes": {
            "scene1": {"status": "COMPLETED", "resultKey": f"results/{jobid}/scene1.png"}
        }}}
        result = worker.acquire(jobid, "scene1", "request12345")
        self.assertEqual(result["status"], "MOCK_CLAIMED")
        self.assertEqual(worker.SLOTS.put_item.call_args.kwargs["ConditionExpression"],
                         "attribute_not_exists(slotId)")
        self.assertEqual(worker.JOBS.update_item.call_count, 2)

    def test_mock_completion_does_not_masquerade_as_mp4(self):
        worker = self.load_worker()
        jobid = "a" * 32
        worker.JOBS.get_item.return_value = {"Item": {
            "jobId": jobid,
            "videos": {"scene1": {"status": "MOCK_GENERATING", "requestId": "request12345"}}
        }}
        result = worker.lambda_handler({"op": "save", "jobId": jobid,
                                        "sceneId": "scene1", "requestId": "request12345"}, None)
        self.assertEqual(result["status"], "MOCK_COMPLETED")
        self.assertTrue(result["markerKey"].startswith("video-mock/"))
        self.assertTrue(result["markerKey"].endswith(".json"))
        worker.S3.put_object.assert_called_once()

    def test_stack_only_declares_new_video_resources(self):
        data = TEMPLATE.read_text()
        for forbidden in ("Type: AWS::ApiGateway::", "Type: AWS::CloudFront::",
                          "Type: AWS::S3::Bucket"):
            self.assertNotIn(forbidden, data)
        self.assertIn("MODE: MOCK_ONLY", data)
        self.assertIn("attribute_not_exists", WORKER.read_text())
        self.assertNotIn("aiplatform.googleapis.com", WORKER.read_text())
        self.assertNotIn("google.auth", WORKER.read_text())


if __name__ == "__main__":
    unittest.main()
