"""No-network tests for the explicit Scene 1 Replicate pilot."""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "FAKEACCESSKEY123")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "FAKESECRET123")
os.environ.setdefault("AWS_SESSION_TOKEN", "FAKESESSIONTOKEN")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("UPLOAD_BUCKET", "afl-app-dev-uploads-134387458447-ap-southeast-2")
os.environ.setdefault("ASSET_BUCKET", "afl-video-bootstrap-artifactbucket-xiuriidgyft4")
os.environ.setdefault("JOBS_TABLE", "afl-app-dev-jobs")
os.environ.setdefault("REPLICATE_SECRET_NAME", "afl-app/dev/replicate-api-token")
os.environ.setdefault("SCENARIO_CONFIG_KEY",
                     "scenarios/afl-grand-final-winning-kick/v1/config.json")
path = Path(__file__).resolve().parents[1] / "src" / "image_worker" / "handler.py"
spec = importlib.util.spec_from_file_location("afl_image_worker", path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

CONFIG = json.loads(
    (Path(__file__).resolve().parents[1] /
     "scenarios/afl-grand-final-winning-kick/v1/config.json").read_text()
)


class Scene1PilotTests(unittest.TestCase):
    def test_all_master_keys_exist_in_configuration(self):
        self.assertEqual(CONFIG["pilotSceneId"], "scene1")
        self.assertEqual(len(CONFIG["scenes"]), 3)
        for i, scene in enumerate(CONFIG["scenes"], start=1):
            self.assertEqual(
                scene["masterKey"],
                f"scenarios/afl-grand-final-winning-kick/v1/scene{i}-start.png"
            )
        self.assertFalse(CONFIG["scenes"][1]["enabled"])
        self.assertFalse(CONFIG["scenes"][2]["enabled"])

    def test_submit_sends_two_images_and_one_team_design(self):
        job = {
            "jobId": "a" * 32,
            "status": "AWAITING_IMAGE_PIPELINE",
            "uploadKey": "uploads/" + "b" * 32 + ".png",
            "team": "geelong",
        }
        presigned = [
            "https://asset.s3.ap-southeast-2.amazonaws.com/master.png",
            "https://uploads.s3.ap-southeast-2.amazonaws.com/selfie.png",
        ]
        with (
            patch.object(worker, "job_by_id", return_value=job),
            patch.object(worker, "load_scenario",
                         return_value=(CONFIG, CONFIG["scenes"][0])),
            patch.object(worker.S3, "head_object"),
            patch.object(worker.S3, "generate_presigned_url",
                         side_effect=presigned),
            patch.object(worker, "get_token", return_value="r8_fake"),
            patch.object(worker, "update_job") as update_job,
            patch.object(worker, "request_json",
                         return_value={"id": "one-prediction", "status": "starting"})
                         as replicate,
        ):
            result = worker.submit(job["jobId"])
        self.assertEqual(result["predictionId"], "one-prediction")
        self.assertEqual(replicate.call_count, 1)
        url, token, body = replicate.call_args.args
        self.assertEqual(url, worker.MODEL_URL)
        self.assertEqual(token, "r8_fake")
        image_input = body["input"]["image_input"]
        self.assertEqual(image_input, [presigned[1], presigned[0]])
        self.assertIn("navy blue and white horizontal hoops", body["input"]["prompt"])
        self.assertEqual(body["input"]["aspect_ratio"], "9:16")
        self.assertEqual(body["input"]["resolution"], "2K")
        self.assertEqual(body["input"]["output_format"], "png")
        self.assertEqual(update_job.call_count, 2)

    def test_failed_submit_does_not_retry_billable_prediction(self):
        job = {
            "status": "AWAITING_IMAGE_PIPELINE",
            "uploadKey": "uploads/" + "b" * 32 + ".png",
            "team": "carlton",
        }
        with (
            patch.object(worker, "job_by_id", return_value=job),
            patch.object(worker, "load_scenario",
                         return_value=(CONFIG, CONFIG["scenes"][0])),
            patch.object(worker.S3, "head_object"),
            patch.object(worker.S3, "generate_presigned_url",
                         return_value="https://example.com/input"),
            patch.object(worker, "get_token", return_value="r8_fake"),
            patch.object(worker, "update_job"),
            patch.object(worker, "request_json",
                         side_effect=RuntimeError("Replicate request failed")) as replicate,
        ):
            with self.assertRaises(RuntimeError):
                worker.submit("a" * 32)
        self.assertEqual(replicate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
