"""Offline, bill-free tests for parallel direct Google image generation."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import base64
import types

ENV = {
    "AWS_REGION": "ap-southeast-2", "AWS_DEFAULT_REGION": "ap-southeast-2",
    "AWS_ACCESS_KEY_ID": "FAKEACCESSKEY123", "AWS_SECRET_ACCESS_KEY": "FAKESECRET123",
    "AWS_SESSION_TOKEN": "FAKESESSIONTOKEN", "AWS_EC2_METADATA_DISABLED": "true",
    "UPLOAD_BUCKET": "test-uploads", "ASSET_BUCKET": "test-artifacts",
    "JOBS_TABLE": "test-jobs", "SCENARIO_CONFIG_KEY": "scenarios/afl-grand-final-winning-kick/v1/config.json",
    "GOOGLE_PROJECT_ID": "afl-video-platform", "GOOGLE_PROJECT_NUMBER": "345956846110",
    "GOOGLE_WIF_POOL_ID": "afl-video-aws", "GOOGLE_WIF_PROVIDER_ID": "afl-aws-dev",
    "GOOGLE_SERVICE_ACCOUNT": "afl-video-worker@afl-video-platform.iam.gserviceaccount.com",
}
for key, value in ENV.items():
    os.environ.setdefault(key, value)
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "image_worker"
sys.path.insert(0, str(SRC))
spec = importlib.util.spec_from_file_location("afl_parallel_worker", SRC / "handler.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
import google_direct as google
CONFIG = json.loads((ROOT / "scenarios/afl-grand-final-winning-kick/v1/config.json").read_text())


def png():
    # Enough PNG signature and IHDR for byte-header validation; no network/images.
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (1152).to_bytes(4, "big") + (2048).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"


def result_image():
    return {"candidates": [{"content": {"parts": [
        {"text": "created"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png()).decode()}}
    ]}}]}


def image_worker():
    return types.SimpleNamespace(
        VALID_SCENES=worker.VALID_SCENES, ASSET_BUCKET="test-artifacts",
        UPLOAD_BUCKET="test-uploads", S3=Mock(), job_by_id=Mock(), load_scenario=Mock(return_value=CONFIG),
        build_image_prompt=worker.build_image_prompt, scene_update=Mock())


def setup_scene(scene="scene1"):
    mock = image_worker()
    mock.job_by_id.return_value = {
        "status": "GENERATING_IMAGES", "team": "geelong",
        "uploadKey": "uploads/selfie.png",
        "scenes": {scene: {"status": "PENDING"}},
    }
    return mock


class ImageContractTests(unittest.TestCase):
    def test_config_three_scenes_and_model(self):
        self.assertEqual([s["id"] for s in CONFIG["scenes"]], list(worker.VALID_SCENES))
        self.assertEqual({s["model"] for s in CONFIG["scenes"]}, {"gemini-3-pro-image"})
        for i, scene in enumerate(CONFIG["scenes"], 1):
            self.assertEqual(scene["masterKey"], f"scenarios/afl-grand-final-winning-kick/v1/scene{i}-start.png")

    def test_old_s3_config_accepted_during_atomic_cutover(self):
        old = json.loads(json.dumps(CONFIG))
        for scene in old["scenes"]:
            scene["model"] = "google/nano-banana-pro"
        with patch.object(worker.S3, "get_object",
                          return_value={"Body": Mock(read=Mock(return_value=json.dumps(old).encode()))}):
            self.assertEqual(len(worker.load_scenario()["scenes"]), 3)

    def test_parallel_sfn_and_recovery_unchanged(self):
        template = (ROOT / "infrastructure/app/backend.yaml").read_text()
        self.assertIn("MaxConcurrency: 3", template)
        self.assertIn("StartAt: Generate", template)
        self.assertIn("op: generate", template)
        self.assertIn("Next: MarkSceneFailed", template)
        self.assertIn("Next: RecoverJob", template)
        self.assertNotIn("WaitForPrediction", template)
        self.assertIn("ImageReconcileSchedule:", template)

    def test_prepare_claims_all_three_scenes(self):
        job = {"status": "AWAITING_IMAGE_PIPELINE", "team": "geelong", "uploadKey": "uploads/selfie.png"}
        with patch.object(worker, "job_by_id", return_value=job), \
             patch.object(worker, "load_scenario", return_value=CONFIG), \
             patch.object(worker.S3, "head_object") as head, \
             patch.object(worker, "update_job") as update:
            result = worker.prepare("a" * 32)
        self.assertEqual(result["scenes"], ["scene1", "scene2", "scene3"])
        self.assertEqual(head.call_count, 4)
        self.assertEqual(update.call_args.args[2][":scenes"],
                         {s: {"status": "PENDING"} for s in worker.VALID_SCENES})

    def test_scene_payload_retains_both_images_and_prompts(self):
        for scene in worker.VALID_SCENES:
            with self.subTest(scene=scene):
                m = setup_scene(scene)
                with patch.object(google, "s3_image", side_effect=[
                    {"inlineData": {"mimeType": "image/png", "data": "master"}},
                    {"inlineData": {"mimeType": "image/jpeg", "data": "selfie"}}]), \
                     patch.object(google, "access_token", return_value="test-token"), \
                     patch.object(google, "post_json", return_value=result_image()) as post:
                    result = google.generate(m, "a" * 32, scene)
                self.assertEqual(result["status"], "COMPLETED")
                body = post.call_args.args[1]
                parts = body["contents"][0]["parts"]
                self.assertIn("navy blue and white horizontal hoops", parts[0]["text"])
                self.assertEqual(parts[1]["inlineData"]["data"], "master")
                self.assertEqual(parts[2]["inlineData"]["data"], "selfie")
                self.assertEqual(body["generationConfig"]["imageConfig"],
                                 {"aspectRatio": "9:16", "imageSize": "2K",
                                  "imageOutputOptions": {"mimeType": "image/png"}})
                self.assertEqual(m.S3.put_object.call_args.kwargs["Key"],
                                 f"results/{'a' * 32}/{scene}.png")
                self.assertEqual(m.scene_update.call_args.args[2], "COMPLETED")

    def test_no_double_submission_of_completed_scene(self):
        m = setup_scene()
        m.job_by_id.return_value["scenes"]["scene1"]["status"] = "COMPLETED"
        with patch.object(google, "post_json") as paid:
            with self.assertRaises(RuntimeError):
                google.generate(m, "a" * 32, "scene1")
            paid.assert_not_called()

    def test_429_retries_only_after_explicit_rejection(self):
        m = setup_scene()
        with patch.object(google, "s3_image", return_value={"inlineData": {"mimeType": "image/png", "data": "x"}}), \
             patch.object(google, "access_token", return_value="test-token"), \
             patch.object(google, "post_json", side_effect=[
                 google.GoogleHTTPError(429, 15), result_image()]) as paid, \
             patch.object(google.time, "sleep") as sleep, \
             patch.object(google.random, "uniform", return_value=0):
            google.generate(m, "a" * 32, "scene1")
        self.assertEqual(paid.call_count, 2)
        sleep.assert_called_once_with(15)

    def test_5xx_and_timeout_do_not_resubmit(self):
        for problem, code in [(google.GoogleHTTPError(503), "GOOGLE_HTTP_503"),
                              (TimeoutError("connection uncertain"), "GOOGLE_SUBMISSION_UNKNOWN")]:
            with self.subTest(code=code):
                m = setup_scene()
                with patch.object(google, "s3_image", return_value={"inlineData": {"mimeType": "image/png", "data": "x"}}), \
                     patch.object(google, "access_token", return_value="test-token"), \
                     patch.object(google, "post_json", side_effect=problem) as paid:
                    with self.assertRaises((google.GoogleHTTPError, TimeoutError)):
                        google.generate(m, "a" * 32, "scene1")
                self.assertEqual(paid.call_count, 1)
                self.assertEqual(m.scene_update.call_args.args[3]["errorCode"], code)

    def test_retry_after_beyond_limit_fails_without_resend(self):
        m = setup_scene()
        with patch.object(google, "s3_image", return_value={"inlineData": {"mimeType": "image/png", "data": "x"}}), \
             patch.object(google, "access_token", return_value="test-token"), \
             patch.object(google, "post_json", side_effect=google.GoogleHTTPError(429, 180)) as paid:
            with self.assertRaises(google.GoogleHTTPError):
                google.generate(m, "a" * 32, "scene1")
        self.assertEqual(paid.call_count, 1)
        self.assertEqual(m.scene_update.call_args.args[3]["errorCode"], "GOOGLE_RETRY_AFTER_TOO_LONG")

    def test_invalid_or_absent_image_is_never_saved(self):
        for bad in [{}, {"candidates": [{"content": {"parts": [{"text": "no image"}]}}]},
                    {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "??"}}]}}]}]:
            with self.subTest(result=bad):
                m = setup_scene()
                with patch.object(google, "s3_image", return_value={"inlineData": {"mimeType": "image/png", "data": "x"}}), \
                     patch.object(google, "access_token", return_value="test-token"), \
                     patch.object(google, "post_json", return_value=bad):
                    with self.assertRaises(ValueError):
                        google.generate(m, "a" * 32, "scene1")
                m.S3.put_object.assert_not_called()

    def test_completed_scenes_survive_recovery(self):
        job = {"status": "GENERATING_IMAGES", "scenes": {
            "scene1": {"status": "COMPLETED", "resultKey": "results/id/scene1.png"},
            "scene2": {"status": "GENERATING"}, "scene3": {"status": "PENDING"}}}
        with patch.object(worker, "job_by_id", return_value=job), \
             patch.object(worker.TABLE, "update_item") as update:
            result = worker.recover("a" * 32)
        self.assertEqual(result["status"], "PARTIAL_SUCCESS")
        out = update.call_args.kwargs["ExpressionAttributeValues"][":scenes"]
        self.assertEqual(out["scene1"], job["scenes"]["scene1"])
        self.assertEqual(out["scene2"]["status"], "FAILED")

    def test_finalize_counts_partial_success(self):
        job = {"scenes": {"scene1": {"status": "COMPLETED"},
                          "scene2": {"status": "FAILED"}, "scene3": {"status": "COMPLETED"}}}
        with patch.object(worker, "job_by_id", return_value=job), \
             patch.object(worker, "update_job"):
            self.assertEqual(worker.finalize("a" * 32)["status"], "PARTIAL_SUCCESS")

    def test_nonbillable_auth_probe_records_only_sanitised_code(self):
        with patch.object(worker.TABLE, "update_item") as update, \
             patch.object(google, "probe_auth", return_value="GOOGLE_AUTH_STS_INVALID_TARGET") as check, \
             patch.object(google, "post_json") as paid:
            worker.probe_google_auth_once()
        self.assertEqual(update.call_count, 2)
        check.assert_called_once()
        paid.assert_not_called()
        self.assertEqual(update.call_args.kwargs["ExpressionAttributeValues"][":code"],
                         "GOOGLE_AUTH_STS_INVALID_TARGET")

    def test_google_auth_error_allowlist(self):
        self.assertEqual(google.auth_diagnostic(RuntimeError("invalid_subject_token")),
                         "STS_INVALID_SUBJECT_TOKEN")
        self.assertEqual(google.auth_diagnostic(RuntimeError("permission_denied")),
                         "IAM_PERMISSION_DENIED")
        self.assertEqual(google.auth_diagnostic(ValueError("secret=123")),
                         "EXCEPTION_ValueError")

    def test_watchdog_never_starts_paid_generation(self):
        with patch.object(worker.TABLE, "scan", return_value={"Items": [{"jobId": "a" * 32}]}), \
             patch.object(worker, "probe_google_auth_once") as probe, \
             patch.object(worker, "recover", return_value={"status": "FAILED"}) as recovered, \
             patch.object(google, "post_json") as paid:
            worker.reconcile_stalled()
        recovered.assert_called_once()
        probe.assert_called_once()
        paid.assert_not_called()


    def test_auth_and_policy_failures_are_not_retried(self):
        for status, code in [(401, "GOOGLE_AUTH_ERROR"), (403, "GOOGLE_AUTH_ERROR"),
                             (400, "GOOGLE_INPUT_OR_SAFETY")]:
            with self.subTest(status=status):
                m = setup_scene()
                with patch.object(google, "s3_image", return_value={"inlineData": {"mimeType": "image/png", "data": "x"}}), \
                     patch.object(google, "access_token", return_value="test-token"), \
                     patch.object(google, "post_json", side_effect=google.GoogleHTTPError(status)) as paid:
                    with self.assertRaises(google.GoogleHTTPError):
                        google.generate(m, "a" * 32, "scene1")
                self.assertEqual(paid.call_count, 1)
                self.assertEqual(m.scene_update.call_args.args[3]["errorCode"], code)

    def test_wif_reuses_working_google_auth_external_account_config(self):
        from google.auth import load_credentials_from_dict
        credentials = Mock(valid=True, token="impersonated")
        with patch("google.auth.load_credentials_from_dict",
                   return_value=(credentials, None)) as load, \
             patch("google.auth.transport.requests.Request") as transport:
            self.assertEqual(google.access_token(), "impersonated")
        info = load.call_args.args[0]
        self.assertEqual(info["type"], "external_account")
        self.assertEqual(info["subject_token_type"],
                         "urn:ietf:params:aws:token-type:aws4_request")
        self.assertEqual(info["audience"],
                         "//iam.googleapis.com/projects/345956846110/locations/global/workloadIdentityPools/afl-video-aws/providers/afl-aws-dev")
        self.assertEqual(info["service_account_impersonation_url"],
                         "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/afl-video-worker@afl-video-platform.iam.gserviceaccount.com:generateAccessToken")
        self.assertIn("sts.{region}.amazonaws.com", info["credential_source"]["regional_cred_verification_url"])
        self.assertEqual(load.call_args.kwargs["scopes"],
                         ["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh.assert_called_once()
        transport.assert_called_once()

    def test_preparation_stage_is_sanitised_without_billing(self):
        cases = [
            ("MASTER_READ", "GOOGLE_MASTER_READ_ERROR", 0),
            ("SELFIE_READ", "GOOGLE_SELFIE_READ_ERROR", 1),
            ("PROMPT", "GOOGLE_PROMPT_ERROR", 2),
            ("AUTH", "GOOGLE_AUTH_HTTP_403", 2),
        ]
        for stage, expected, completed_reads in cases:
            with self.subTest(stage=stage):
                worker_mock = setup_scene()
                def read(*args, **kwargs):
                    if read.call_count == completed_reads:
                        raise ValueError("private_image_url")
                    read.call_count += 1
                    return {"inlineData": {"mimeType": "image/png", "data": "x"}}
                read.call_count = 0
                with patch.object(google, "s3_image", side_effect=read), \
                     patch.object(google, "access_token", side_effect=google.GoogleHTTPError(403) if stage == "AUTH" else None), \
                     patch.object(google, "post_json") as paid:
                    if stage == "PROMPT":
                        worker_mock.build_image_prompt = Mock(side_effect=ValueError("private_prompt"))
                    if stage in ("MASTER_READ", "SELFIE_READ"):
                        expected_exception = ValueError
                    else:
                        expected_exception = google.GoogleHTTPError if stage == "AUTH" else ValueError
                    with self.assertRaises(expected_exception):
                        google.generate(worker_mock, "a" * 32, "scene1")
                    self.assertEqual(worker_mock.scene_update.call_args.args[3]["errorCode"], expected)
                    self.assertNotIn("private", worker_mock.scene_update.call_args.args[3]["errorCode"])
                    paid.assert_not_called()

    def test_no_credentials_exposed_in_sanitised_error(self):
        m = setup_scene()
        with patch.object(google, "s3_image", return_value={"inlineData": {"mimeType": "image/png", "data": "x"}}), \
             patch.object(google, "access_token", side_effect=RuntimeError("provider private error body")):
            with self.assertRaises(RuntimeError):
                google.generate(m, "a" * 32, "scene1")
        self.assertEqual(m.scene_update.call_args.args[3], {"errorCode": "GOOGLE_AUTH_INVALID_RESPONSE"})



if __name__ == "__main__":
    unittest.main()
