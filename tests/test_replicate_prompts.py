"""Offline tests for S3-owned Replicate image prompts; no AWS calls or billing."""
import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src/image_worker/handler.py"
CONFIG = ROOT / "scenarios/afl-grand-final-winning-kick/v1/config.json"

# Compile only pure prompt helper functions; importing the Lambda would initialise
# boto3 clients and require AWS environment variables in an offline unit test.
module = ast.parse(WORKER.read_text(encoding="utf-8"))
functions = [node for node in module.body if isinstance(node, ast.FunctionDef)
             and node.name in ("validate_prompt_config", "build_image_prompt")]
assert len(functions) == 2
namespace = {}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(WORKER), "exec"), namespace)
validate_prompt_config = namespace["validate_prompt_config"]
build_image_prompt = namespace["build_image_prompt"]


class ReplicatePromptTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_all_scenes_use_selfie_and_scene_specific_text(self):
        validate_prompt_config(self.config)
        self.assertEqual([s["id"] for s in self.config["scenes"]], ["scene1", "scene2", "scene3"])
        for scene in self.config["scenes"]:
            with self.subTest(scene=scene["id"]):
                result = build_image_prompt(self.config, scene, "geelong")
                self.assertEqual(result.count(self.config["sharedPrompt"]), 1)
                self.assertEqual(result.count(scene["promptInstructions"]), 1)
                self.assertIn("ONLY facial identity source", result)
                self.assertIn("Geelong Cats: navy blue and white horizontal hoops", result)
                self.assertIn(scene["promptInstructions"], result)
                self.assertEqual(scene["model"], "google/nano-banana-pro")
                self.assertIn("selfie", result.lower())

    def test_all_scenes_have_explicit_face_and_uniform_rules(self):
        for scene in self.config["scenes"]:
            with self.subTest(scene=scene["id"]):
                text = scene["promptInstructions"].lower()
                self.assertIn("face", text)
                if scene["id"] in ("scene1", "scene2"):
                    self.assertIn("essendon", text)
                else:
                    self.assertIn("do not introduce opposition players", text)
        self.assertIn("natural excited smile or slightly open mouth",
                      self.config["scenes"][2]["promptInstructions"])

    def test_missing_or_invalid_prompt_fails_before_generation(self):
        for invalid in (None, "", 10, "short"):
            with self.subTest(shared=invalid):
                cfg = dict(self.config)
                cfg["sharedPrompt"] = invalid
                with self.assertRaises(RuntimeError):
                    validate_prompt_config(cfg)
        for scene_id in range(3):
            with self.subTest(scene=scene_id):
                cfg = json.loads(json.dumps(self.config))
                del cfg["scenes"][scene_id]["promptInstructions"]
                with self.assertRaises(RuntimeError):
                    validate_prompt_config(cfg)

    def test_replicate_request_contract_unchanged(self):
        source = WORKER.read_text(encoding="utf-8")
        for required in (
            '"image_input": [master_url, selfie_url]',
            '"aspect_ratio": "9:16", "resolution": "2K"',
            '"output_format": "png", "allow_fallback_model": False',
            'https://api.replicate.com/v1/models/google/nano-banana-pro/predictions',
            'prompt = build_image_prompt(config, scene, job["team"])',
        ):
            self.assertIn(required, source)
        self.assertNotIn('Photorealistic cinematic Australian rules football image edit.', source)


if __name__ == "__main__":
    unittest.main()
