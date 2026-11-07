"""Offline tests for keyless auth configuration: no Google traffic or video charges."""
import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

PATH = pathlib.Path(__file__).resolve().parents[1] / "src/video_worker/handler.py"


def load_worker(fake_loader):
    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    transport = types.ModuleType("google.auth.transport")
    requests = types.ModuleType("google.auth.transport.requests")
    auth.load_credentials_from_dict = fake_loader
    requests.Request = lambda: {"transport": "requests"}
    google.auth = auth
    auth.transport = transport
    transport.requests = requests
    modules = {
        "google": google,
        "google.auth": auth,
        "google.auth.transport": transport,
        "google.auth.transport.requests": requests,
    }
    env = {
        "GOOGLE_PROJECT_NUMBER": "345956846110",
        "GOOGLE_WIF_POOL_ID": "afl-video-aws",
        "GOOGLE_WIF_PROVIDER_ID": "afl-aws-dev",
        "GOOGLE_SERVICE_ACCOUNT": "afl-video-worker@afl-video-platform.iam.gserviceaccount.com",
    }
    with patch.dict(sys.modules, modules), patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("video_worker_test", PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class VideoFederationTests(unittest.TestCase):
    def test_only_allowed_operation_is_auth_check(self):
        worker = load_worker(lambda *_args, **_kwargs: self.fail("No token exchange expected"))
        with self.assertRaisesRegex(ValueError, "Only auth_check"):
            worker.lambda_handler({"op": "generate_video"}, None)

    def test_config_restricts_service_account_and_pool(self):
        worker = load_worker(lambda *_args, **_kwargs: self.fail("No token exchange expected"))
        config = worker.external_account_info()
        self.assertIn("/345956846110/", config["audience"])
        self.assertTrue(config["audience"].endswith("/afl-video-aws/providers/afl-aws-dev"))
        self.assertIn("afl-video-worker@", config["service_account_impersonation_url"])
        self.assertNotIn("private_key", str(config))
        self.assertEqual(config["credential_source"]["environment_id"], "aws1")

    def test_auth_check_refreshes_without_exposing_token(self):
        captured = {}

        class FakeCredentials:
            valid = True
            token = "SECRET_TOKEN_MUST_NOT_BE_RETURNED"

            def refresh(self, request):
                captured["request"] = request

        def loader(info, scopes):
            captured["info"] = info
            captured["scopes"] = scopes
            return FakeCredentials(), None

        worker = load_worker(loader)
        result = worker.lambda_handler({"op": "auth_check"}, None)
        self.assertEqual(result["status"], "AUTHENTICATED")
        self.assertFalse(result["videoGenerationEnabled"])
        self.assertNotIn("SECRET_TOKEN", str(result))
        self.assertEqual(captured["request"]["transport"], "requests")
        self.assertEqual(captured["scopes"], [worker.CLOUD_PLATFORM_SCOPE])


if __name__ == "__main__":
    unittest.main()
