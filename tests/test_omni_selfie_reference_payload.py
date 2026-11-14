"""Offline regression contract: Omni image-to-video receives one starting image only."""
import ast
import base64
import unittest
from pathlib import Path


class SingleImagePayloadTests(unittest.TestCase):
    def test_original_single_start_frame_request_contract(self):
        source = Path("src/google_video_worker/handler.py").read_text()
        tree = ast.parse(source)
        definition = next(node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == "image_payload")
        self.assertEqual([x.arg for x in definition.args.args],
                         ["blob", "content_type", "prompt", "duration", "gcs_prefix"])
        class ContractError(ValueError):
            pass
        scope = {"base64": base64, "MAX_IMAGE_BYTES": 20 * 1024 * 1024,
                 "ContractError": ContractError}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "<source>", "exec"), scope)
        request = scope["image_payload"](b"banana", "image/png", "run", 4)
        self.assertEqual(len(request["input"]), 2)
        self.assertEqual(request["input"][0], {"type":"text", "text":"run"})
        self.assertEqual(request["input"][1]["type"], "image")
        self.assertEqual(request["input"][1]["data"], base64.b64encode(b"banana").decode())
        self.assertEqual(request["generation_config"]["video_config"]["task"], "image_to_video")
        self.assertNotIn("selfie", source.lower().split("def image_payload(", 1)[1].split("def scene_preflight(", 1)[0])
        self.assertNotIn("OMNI_SELFIE_REFERENCE_ENABLED", source)


if __name__ == "__main__":
    unittest.main()
