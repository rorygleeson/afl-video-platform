"""Regression tests for browser-compatible regional S3 presigned POST forms."""
import base64
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

# These credentials are fake. Tests only generate a signed form locally;
# they never send network requests or create AWS resources.
os.environ["AWS_ACCESS_KEY_ID"] = "FAKEACCESSKEY123"
os.environ["AWS_SECRET_ACCESS_KEY"] = "FAKESECRET123"
os.environ["AWS_SESSION_TOKEN"] = "FAKESESSIONTOKEN"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ["AWS_REGION"] = "ap-southeast-2"
os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-2"
os.environ["UPLOAD_BUCKET"] = "afl-app-dev-uploads-134387458447-ap-southeast-2"
os.environ["JOBS_TABLE"] = "afl-app-dev-jobs"
os.environ["IMAGE_STATE_MACHINE_ARN"] = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:dummy"
os.environ["FRONTEND_ORIGIN"] = "https://d9i3mlqvt0ks6.cloudfront.net"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "api"))
import handler  # noqa: E402


class TestPresignedBrowserPost(unittest.TestCase):
    def test_regional_endpoint_sigv4_form_and_constraints(self):
        photo = {"contentType": "image/png", "sizeBytes": 1024}
        result = handler.presign_upload({"body": json.dumps(photo)})
        self.assertEqual(result["statusCode"], 200)
        payload = json.loads(result["body"])
        form = payload["upload"]
        url = urlparse(form["url"])
        self.assertEqual(url.scheme, "https")
        self.assertEqual(
            url.hostname,
            "afl-app-dev-uploads-134387458447-ap-southeast-2.s3.ap-southeast-2.amazonaws.com",
        )
        fields = form["fields"]
        self.assertEqual(fields["Content-Type"], "image/png")
        self.assertEqual(fields["x-amz-algorithm"], "AWS4-HMAC-SHA256")
        self.assertIn("/ap-southeast-2/s3/aws4_request", fields["x-amz-credential"])
        self.assertIn("x-amz-signature", fields)
        self.assertNotIn("AWSAccessKeyId", fields)
        self.assertNotIn("signature", fields)

        policy_data = fields["policy"]
        policy = json.loads(base64.b64decode(policy_data))
        self.assertIn({"Content-Type": "image/png"}, policy["conditions"])
        self.assertIn(["content-length-range", 1, 10 * 1024 * 1024], policy["conditions"])

    def test_unsupported_content_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Only JPEG, PNG and WebP"):
            handler.presign_upload({
                "body": json.dumps({"contentType": "image/svg+xml", "sizeBytes": 1024})
            })


if __name__ == "__main__":
    unittest.main()
