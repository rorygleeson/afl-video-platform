"""Offline tests for the parallel three-scene image generation pipeline."""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

for key, value in {
    "AWS_REGION": "ap-southeast-2",
    "AWS_DEFAULT_REGION": "ap-southeast-2",
    "AWS_ACCESS_KEY_ID": "FAKEACCESSKEY123",
    "AWS_SECRET_ACCESS_KEY": "FAKESECRET123",
    "AWS_SESSION_TOKEN": "FAKESESSIONTOKEN",
    "AWS_EC2_METADATA_DISABLED": "true",
    "UPLOAD_BUCKET": "afl-app-dev-uploads-134387458447-ap-southeast-2",
    "ASSET_BUCKET": "afl-video-bootstrap-artifactbucket-xiuriidgyft4",
    "JOBS_TABLE": "afl-app-dev-jobs",
    "REPLICATE_SECRET_NAME": "afl-app/dev/replicate-api-token",
    "SCENARIO_CONFIG_KEY": "scenarios/afl-grand-final-winning-kick/v1/config.json",
}.items():
    os.environ.setdefault(key, value)

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "afl_parallel_worker", root / "src" / "image_worker" / "handler.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
CONFIG = json.loads(
    (root / "scenarios/afl-grand-final-winning-kick/v1/config.json").read_text())


class ParallelSceneTests(unittest.TestCase):
    def test_config_has_three_enabled_master_scene_edits(self):
        self.assertEqual([s["id"] for s in CONFIG["scenes"]],
                         ["scene1", "scene2", "scene3"])
        self.assertEqual([s["enabled"] for s in CONFIG["scenes"]], [True] * 3)
        for i, scene in enumerate(CONFIG["scenes"], 1):
            self.assertEqual(scene["masterKey"],
                             f"scenarios/afl-grand-final-winning-kick/v1/scene{i}-start.png")
            self.assertEqual(scene["operation"], "image_edit")
            self.assertEqual(scene["model"], "google/nano-banana-pro")

    def test_prepare_initializes_three_statuses_before_paid_requests(self):
        job = {"status": "AWAITING_IMAGE_PIPELINE", "team": "geelong",
               "uploadKey": "uploads/" + "b" * 32 + ".jpg"}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "load_scenario", return_value=CONFIG),
              patch.object(worker.S3, "head_object") as head,
              patch.object(worker, "update_job") as update):
            result = worker.prepare("a" * 32)
        self.assertEqual(result["scenes"], ["scene1", "scene2", "scene3"])
        self.assertEqual(head.call_count, 4)
        self.assertEqual(update.call_args.args[2][":scenes"],
                         {name: {"status": "PENDING"} for name in worker.VALID_SCENES})

    def test_each_scene_calls_one_prediction_with_correct_master_and_team(self):
        for scene_id in worker.VALID_SCENES:
            with self.subTest(scene=scene_id):
                job = {"status": "GENERATING_IMAGES",
                       "uploadKey": "uploads/" + "b" * 32 + ".png",
                       "team": "geelong",
                       "scenes": {scene_id: {"status": "PENDING"}}}
                with (patch.object(worker, "job_by_id", return_value=job),
                      patch.object(worker, "load_scenario", return_value=CONFIG),
                      patch.object(worker.S3, "generate_presigned_url",
                                   side_effect=[
                                       "https://asset.s3.ap-southeast-2.amazonaws.com/" + scene_id,
                                       "https://uploads.s3.ap-southeast-2.amazonaws.com/selfie",
                                   ]),
                      patch.object(worker, "scene_update") as update,
                      patch.object(worker, "get_token", return_value="r8_fake"),
                      patch.object(worker, "request_json",
                                   return_value={"id": "pred-" + scene_id}) as remote):
                    result = worker.submit("a" * 32, scene_id)
                self.assertEqual(result["sceneId"], scene_id)
                self.assertEqual(remote.call_count, 1)
                body = remote.call_args.args[2]
                self.assertIn(scene_id, body["input"]["image_input"][0])
                self.assertIn("selfie", body["input"]["image_input"][1])
                self.assertIn("navy blue and white horizontal hoops",
                              body["input"]["prompt"])
                self.assertIn(CONFIG["scenes"][
                    worker.VALID_SCENES.index(scene_id)]["promptInstructions"],
                    body["input"]["prompt"])
                self.assertEqual(update.call_count, 2)
                self.assertEqual(update.call_args_list[0].args[2], "SUBMITTING")
                self.assertEqual(body["input"]["resolution"], "2K")

    def test_submission_failure_not_retried(self):
        job = {"status": "GENERATING_IMAGES", "team": "geelong",
               "uploadKey": "uploads/" + "b" * 32 + ".png",
               "scenes": {"scene1": {"status": "PENDING"}}}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "load_scenario", return_value=CONFIG),
              patch.object(worker.S3, "generate_presigned_url",
                           return_value="https://s3.example.com/input"),
              patch.object(worker, "scene_update"),
              patch.object(worker, "get_token", return_value="r8_fake"),
              patch.object(worker, "request_json",
                           side_effect=RuntimeError("Network failure")) as remote):
            with self.assertRaises(RuntimeError):
                worker.submit("a" * 32, "scene1")
        self.assertEqual(remote.call_count, 1)

    def test_aggregate_complete_partial_and_failure(self):
        for statuses, expected in [
            (["COMPLETED"] * 3, "COMPLETED"),
            (["COMPLETED", "FAILED", "COMPLETED"], "PARTIAL_SUCCESS"),
            (["FAILED"] * 3, "FAILED"),
        ]:
            with self.subTest(status=expected):
                job = {"scenes": dict(zip(
                    worker.VALID_SCENES,
                    [{"status": status} for status in statuses]))}
                with (patch.object(worker, "job_by_id", return_value=job),
                      patch.object(worker, "update_job") as update):
                    result = worker.finalize("a" * 32)
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["completedScenes"],
                                 statuses.count("COMPLETED"))
                self.assertEqual(update.call_args.args[2][":outcome"], expected)


    def test_recover_failed_preparation_marks_all_failed(self):
        job = {"status": "AWAITING_IMAGE_PIPELINE"}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker.TABLE, "update_item") as update):
            result = worker.recover("a" * 32)
        self.assertEqual(result["status"], "FAILED")
        args = update.call_args.kwargs
        self.assertEqual(args["ExpressionAttributeValues"][":failed"], 3)
        self.assertEqual(args["ExpressionAttributeValues"][":completed"], 0)
        self.assertEqual(
            {name: scene["status"] for name, scene in
             args["ExpressionAttributeValues"][":scenes"].items()},
            {name: "FAILED" for name in worker.VALID_SCENES})
        self.assertIn("attribute_not_exists", args["ConditionExpression"])

    def test_recover_preserves_completed_images_and_marks_others_failed(self):
        job = {"status": "GENERATING_IMAGES", "scenes": {
            "scene1": {"status": "COMPLETED", "resultKey": "results/id/scene1.png"},
            "scene2": {"status": "GENERATING", "predictionId": "p2"},
            "scene3": {"status": "PENDING"},
        }}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker.TABLE, "update_item") as update):
            result = worker.recover("a" * 32)
        self.assertEqual(result, {
            "status": "PARTIAL_SUCCESS",
            "completedScenes": 1, "failedScenes": 2})
        args = update.call_args.kwargs
        scenes = args["ExpressionAttributeValues"][":scenes"]
        self.assertEqual(scenes["scene1"]["resultKey"], "results/id/scene1.png")
        self.assertEqual(scenes["scene1"]["status"], "COMPLETED")
        self.assertEqual(scenes["scene2"]["status"], "FAILED")
        self.assertEqual(scenes["scene3"]["status"], "FAILED")
        self.assertIn("scenes.#scene0.#status", args["ConditionExpression"])

    def test_recover_terminal_job_does_not_overwrite(self):
        with (patch.object(worker, "job_by_id",
                           return_value={"status": "COMPLETED"}),
              patch.object(worker.TABLE, "update_item") as update):
            self.assertEqual(worker.recover("a" * 32), {"status": "COMPLETED"})
        update.assert_not_called()

    def test_watchdog_marks_only_stale_jobs_without_replicate_request(self):
        page = {"Items": [{"jobId": "a" * 32}, {"jobId": "b" * 32}]}
        with (patch.object(worker.TABLE, "scan", return_value=page) as scan,
              patch.object(worker, "recover", return_value={"status": "FAILED"}) as recover,
              patch.object(worker, "request_json") as replicate):
            output = worker.reconcile_stalled()
        self.assertEqual(output, {"checked": 2, "recovered": 2})
        self.assertEqual(recover.call_count, 2)
        replicate.assert_not_called()
        self.assertEqual(scan.call_count, 1)

    def test_finalize_only_when_generating(self):
        job = {"scenes": {
            name: {"status": "COMPLETED"} for name in worker.VALID_SCENES
        }}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "update_job") as update):
            worker.finalize("a" * 32)
        args = update.call_args.args
        self.assertEqual(args[4], "#status = :generating")
        self.assertEqual(args[2][":generating"], "GENERATING_IMAGES")


    def test_known_429_rejections_are_retried_without_double_spend(self):
        job = {"status": "GENERATING_IMAGES", "team": "geelong",
               "uploadKey": "uploads/" + "b" * 32 + ".png",
               "scenes": {"scene2": {"status": "PENDING"}}}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "load_scenario", return_value=CONFIG),
              patch.object(worker.S3, "generate_presigned_url",
                           return_value="https://s3.example.com/input"),
              patch.object(worker, "scene_update") as update,
              patch.object(worker, "get_token", return_value="r8_fake"),
              patch.object(worker.time, "sleep") as sleep,
              patch.object(worker.random, "uniform", return_value=0),
              patch.object(worker, "request_json",
                           side_effect=[worker.ReplicateHTTPError(429, retry_after=15),
                                        {"id": "pred-scene2"}]) as remote):
            result = worker.submit("a" * 32, "scene2")
        self.assertEqual(result["predictionId"], "pred-scene2")
        self.assertEqual(remote.call_count, 2)
        sleep.assert_called_once_with(15)
        self.assertEqual(update.call_count, 2)

    def test_ambiguous_submission_http_500_is_not_retried(self):
        job = {"status": "GENERATING_IMAGES", "team": "geelong",
               "uploadKey": "uploads/" + "b" * 32 + ".png",
               "scenes": {"scene1": {"status": "PENDING"}}}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "load_scenario", return_value=CONFIG),
              patch.object(worker.S3, "generate_presigned_url",
                           return_value="https://s3.example.com/input"),
              patch.object(worker, "scene_update") as update,
              patch.object(worker, "get_token", return_value="r8_fake"),
              patch.object(worker.time, "sleep") as sleep,
              patch.object(worker, "request_json",
                           side_effect=worker.ReplicateHTTPError(500)) as remote):
            with self.assertRaises(worker.ReplicateHTTPError):
                worker.submit("a" * 32, "scene1")
        self.assertEqual(remote.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(update.call_args.args[3]["errorCode"], "REPLICATE_HTTP_500")

    def test_throttling_exhaustion_records_reason(self):
        job = {"status": "GENERATING_IMAGES", "team": "geelong",
               "uploadKey": "uploads/" + "b" * 32 + ".png",
               "scenes": {"scene3": {"status": "PENDING"}}}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "load_scenario", return_value=CONFIG),
              patch.object(worker.S3, "generate_presigned_url",
                           return_value="https://s3.example.com/input"),
              patch.object(worker, "scene_update") as update,
              patch.object(worker, "get_token", return_value="r8_fake"),
              patch.object(worker.time, "sleep") as sleep,
              patch.object(worker.random, "uniform", return_value=0),
              patch.object(worker, "request_json",
                           side_effect=worker.ReplicateHTTPError(429)) as remote):
            with self.assertRaises(worker.ReplicateHTTPError):
                worker.submit("a" * 32, "scene3")
        self.assertEqual(remote.call_count, 6)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4, 8, 16])
        self.assertEqual(update.call_args.args[3]["errorCode"],
                         "REPLICATE_RATE_LIMIT")

    def test_retry_after_http_header_numeric_and_http_date(self):
        from datetime import datetime, timezone
        self.assertEqual(worker.retry_after_seconds("60"), 60.0)
        self.assertEqual(worker.retry_after_seconds("1.5"), 1.5)
        self.assertIsNone(worker.retry_after_seconds("not a date"))
        self.assertEqual(worker.retry_after_seconds(
            "Sat, 07 Nov 2026 11:10:00 GMT",
            now=datetime(2026, 11, 7, 11, 9, tzinfo=timezone.utc)), 60)

    def test_retry_after_oversized_rejection_is_not_resent(self):
        job = {"status": "GENERATING_IMAGES", "team": "geelong",
               "uploadKey": "uploads/" + "b" * 32 + ".png",
               "scenes": {"scene1": {"status": "PENDING"}}}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "load_scenario", return_value=CONFIG),
              patch.object(worker.S3, "generate_presigned_url",
                           return_value="https://s3.example.com/input"),
              patch.object(worker, "scene_update") as update,
              patch.object(worker, "get_token", return_value="r8_fake"),
              patch.object(worker.time, "sleep") as sleep,
              patch.object(worker, "request_json",
                           side_effect=worker.ReplicateHTTPError(429, 180)) as remote):
            with self.assertRaises(worker.ReplicateHTTPError):
                worker.submit("a" * 32, "scene1")
        self.assertEqual(remote.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(update.call_args.args[3]["errorCode"],
                         "REPLICATE_RETRY_AFTER_TOO_LONG")

    def test_failed_scene_records_sanitized_reason(self):
        job = {"scenes": {"scene2": {
            "status": "SUBMITTING", "errorCode": "REPLICATE_RATE_LIMIT"}}}
        with (patch.object(worker, "job_by_id", return_value=job),
              patch.object(worker, "scene_update") as update):
            worker.fail_scene("a" * 32, "scene2")
        self.assertEqual(update.call_args.args[3]["errorCode"],
                         "REPLICATE_RATE_LIMIT")


if __name__ == "__main__":
    unittest.main()
