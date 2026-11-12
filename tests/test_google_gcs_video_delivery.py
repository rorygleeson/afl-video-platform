"""Non-billable tests for Google Omni 1.2 optional GCS video delivery."""
import io
import os
import unittest
from unittest.mock import patch
from test_google_video_stage5a import load_worker

JOB = "a" * 32
SCENE = "scene3"
PREFIX = "gs://afl-video-omni-output/omni/" + JOB + "/" + SCENE + "/"
URI = PREFIX + "output-01.mp4"


class GcsVideoDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.w = load_worker()

    def test_default_inline_contract_is_unchanged(self):
        w = self.w
        with patch.dict(os.environ, {"GOOGLE_VIDEO_OUTPUT_GCS_PREFIX": ""}):
            self.assertIsNone(w.checked_gcs_prefix(JOB, SCENE))
            body = w.image_payload(b"image bytes", "image/png", "test prompt", 8)
        self.assertEqual(body["response_format"][0]["delivery"], "inline")
        self.assertNotIn("gcs_uri", body["response_format"][0])

    def test_configured_uri_is_scene_specific_and_never_uses_untrusted_urls(self):
        w = self.w
        with patch.dict(os.environ, {"GOOGLE_VIDEO_OUTPUT_GCS_PREFIX": "gs://afl-video-omni-output/omni/"}):
            prefix = w.checked_gcs_prefix(JOB, SCENE)
            self.assertEqual(prefix, PREFIX)
            body = w.image_payload(b"image bytes", "image/png", "prompt", 8, prefix)
        self.assertEqual(body["response_format"][0]["delivery"], "uri")
        self.assertEqual(body["response_format"][0]["gcs_uri"], PREFIX)
        reply = {"steps": [{"type": "model_output", "content": [
            {"type": "video", "mime_type": "video/mp4", "uri": URI}
        ]}]}
        self.assertEqual(w.video_gcs_uri(reply, PREFIX), URI)
        for untrusted in ("https://evil.example/download", "gs://other-bucket/mp4",
                          PREFIX + "../outside.mp4", PREFIX):
            reply["steps"][0]["content"][0]["uri"] = untrusted
            with self.subTest(untrusted=untrusted), self.assertRaises(w.ContractError):
                w.video_gcs_uri(reply, PREFIX)

    def test_authenticated_bounded_gcs_read_saves_to_private_s3(self):
        w = self.w
        movie = b"\\x00\\x00\\x00\\x18ftypisom" + b"z" * 300
        w.S3.head_object.return_value = {"ContentLength": len(movie)}
        with patch.object(w, "credentials", return_value="test-oauth-token"), patch.object(
                w.urllib.request, "urlopen", return_value=io.BytesIO(movie)) as download:
            size = w.copy_gcs_video_to_private_s3(URI, PREFIX,
                                                    "videos/" + JOB + "/" + SCENE + ".mp4")
        self.assertEqual(size, len(movie))
        self.assertTrue(download.call_args.args[0].full_url.startswith(
            "https://storage.googleapis.com/storage/v1/b/afl-video-omni-output/o/"))
        self.assertNotIn("test-oauth-token", str(download.call_args.args[0].full_url))
        self.assertEqual(w.S3.upload_fileobj.call_count, 1)
        self.assertEqual(w.S3.upload_fileobj.call_args.kwargs["ExtraArgs"]["ServerSideEncryption"], "AES256")
        w.S3.put_object.assert_not_called()

    def test_oversized_video_never_uploads(self):
        w = self.w
        with patch.object(w, "credentials", return_value="test-token"), patch.object(
                w.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * 2048)), patch.object(
                w, "MAX_MP4_BYTES", 512):
            with self.assertRaisesRegex(w.ContractError, "exceeds bounded output size"):
                w.copy_gcs_video_to_private_s3(URI, PREFIX, "videos/" + JOB + "/" + SCENE + ".mp4")
        w.S3.upload_fileobj.assert_not_called()


if __name__ == "__main__":
    unittest.main()
