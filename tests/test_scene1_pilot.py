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


if __name__ == "__main__":
    unittest.main()
