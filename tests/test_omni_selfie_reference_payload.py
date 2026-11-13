"""Offline contract tests: no AWS or Google network requests."""
import ast
import base64
import re
import unittest
from pathlib import Path


def load_functions():
    source = Path("src/google_video_worker/handler.py").read_text()
    tree = ast.parse(source)
    wanted = {"image_payload", "selfie_object_key", "load_selfie"}
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    class ContractError(ValueError):
        pass
    ns = {"base64": base64, "re": re,
          "MAX_IMAGE_BYTES": 20 * 1024 * 1024,
          "ContractError": ContractError}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "<extracted>", "exec"), ns)
    return ns


class OmniSelfieReferenceTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_functions()
        self.payload = self.ns["image_payload"]

    def test_existing_single_image_contract_unchanged(self):
        obj = self.payload(b"banana", "image/png", "Run forward", 4)
        self.assertEqual(len(obj["input"]), 2)
        self.assertEqual(obj["input"][1]["data"], base64.b64encode(b"banana").decode())
        self.assertEqual(obj["input"][0]["text"], "Run forward")
        self.assertEqual(obj["generation_config"]["video_config"]["task"], "image_to_video")
        self.assertEqual(obj["response_format"][0]["duration"], "4s")

    def test_optional_selfie_is_second_image_and_first_frame_remains_first(self):
        obj = self.payload(b"banana", "image/png", "Run forward", 4,
                           selfie_blob=b"selfie", selfie_type="image/jpeg")
        self.assertEqual(len(obj["input"]), 3)
        self.assertEqual(obj["input"][1]["data"], base64.b64encode(b"banana").decode())
        self.assertEqual(obj["input"][2]["data"], base64.b64encode(b"selfie").decode())
        self.assertEqual(obj["input"][2]["mime_type"], "image/jpeg")
        self.assertIn("Image 1 is the exact first frame", obj["input"][0]["text"])
        self.assertIn("NOT a last frame", obj["input"][0]["text"])
        self.assertEqual(obj["generation_config"]["video_config"]["task"], "image_to_video")

    def test_reject_missing_or_invalid_selfie(self):
        for args in ({"selfie_blob": b"abc"}, {"selfie_type": "image/jpeg"},
                     {"selfie_blob": b"", "selfie_type": "image/jpeg"},
                     {"selfie_blob": b"x", "selfie_type": "application/octet-stream"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.payload(b"banana", "image/png", "prompt", 4, **args)

    def test_restrict_selfie_key_to_existing_upload_convention(self):
        key = self.ns["selfie_object_key"]
        good = "uploads/" + "a" * 32 + ".jpg"
        self.assertEqual(key({"uploadKey": good}), good)
        for invalid in ("", "../bad.png", "results/" + "a" * 32 + ".png",
                        "uploads/" + "z" * 32 + ".jpg", "uploads/" + "a" * 32 + ".gif"):
            with self.subTest(key=invalid), self.assertRaises(ValueError):
                key({"uploadKey": invalid})


if __name__ == "__main__":
    unittest.main()
