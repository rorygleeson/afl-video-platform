"""Offline tests for S3-owned Google image prompts; no AWS calls or billing."""
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


class GooglePromptTests(unittest.TestCase):
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
                self.assertIn("the ONLY source for the central player's identity", result)
                self.assertIn("Geelong Cats: navy blue and white horizontal hoops", result)
                self.assertIn(scene["promptInstructions"], result)
                self.assertEqual(scene["model"], "gemini-3-pro-image")
                self.assertIn("selfie", result.lower())

    def test_all_scenes_have_explicit_face_and_uniform_rules(self):
        for scene in self.config["scenes"]:
            with self.subTest(scene=scene["id"]):
                text = scene["promptInstructions"].lower()
                self.assertIn("scene ", text)
                if scene["id"] in ("scene1", "scene2"):
                    self.assertIn("essendon", text)
                else:
                    self.assertIn("no essendon opponents", text)
        self.assertIn("mouth is open in a triumphant cheer",
                      self.config["scenes"][2]["promptInstructions"])

    def test_customer_hair_and_selected_team_override_master(self):
        shared = self.config["sharedPrompt"].lower()
        self.assertIn("entire visible head", shared)
        self.assertIn("image 2 = the customer selfie", shared)
        self.assertIn("do not retain the template man's facial features", shared)
        self.assertIn("selected team\'s specified guernsey colours/pattern", shared)
        for scene in self.config["scenes"]:
            with self.subTest(scene=scene["id"]):
                prompt = namespace["build_image_prompt"](self.config, scene, "geelong").lower()
                self.assertIn("navy blue and white horizontal hoops", prompt)
                self.assertIn("hairstyle", prompt)
                self.assertIn("selected team", prompt)
        celebration = self.config["scenes"][2]["promptInstructions"].lower()
        self.assertIn("both teammates", celebration)
        self.assertIn("the selected team's coordinated kit", celebration)

    def test_concise_scene_prompts_do_not_repeat_shared_identity_rules(self):
        shared = self.config["sharedPrompt"].lower()
        for term in ("the only source for the central player's identity", "entire visible head",
                     "hairstyle", "skin tone", "selected team\'s specified guernsey colours/pattern"):
            self.assertIn(term, shared)
        self.assertLess(len(shared.split()), 650)
        for scene in self.config["scenes"]:
            with self.subTest(scene=scene["id"]):
                scene_text = scene["promptInstructions"].lower()
                self.assertLess(len(scene_text.split()), 230)
                self.assertNotIn("full head replacement", scene_text)
                self.assertNotIn("only facial identity source", scene_text)
        self.assertIn("red afl football", self.config["scenes"][0]["promptInstructions"].lower())
        self.assertIn("not been dropped or kicked yet", self.config["scenes"][1]["promptInstructions"].lower())
        self.assertIn("both teammates",
                      self.config["scenes"][2]["promptInstructions"].lower())

    def test_scene2_rear_camera_master_and_video_match(self):
        scene = self.config["scenes"][1]["promptInstructions"].lower()
        shared = self.config["sharedPrompt"].lower()
        video = (ROOT / "scenarios/afl-grand-final-winning-kick/v1/video/scene2-video.txt").read_text().lower()
        self.assertIn("scene 2", shared)
        self.assertIn("rear three-quarter", scene)
        self.assertIn("four", scene)
        self.assertIn("four-fifths (80%)", scene)
        self.assertIn("even spacing", scene)
        self.assertIn("running toward", scene)
        self.assertIn("four existing goalposts", video)
        self.assertIn("while it is still airborne", video)
        self.assertIn("camera remains stationary", video)
        self.assertIn("two tall central goalposts", video)
        self.assertNotIn("no goalposts are visible", scene)
        self.assertNotIn("drop punt", video)

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

    def test_google_request_contract_preserves_scene_prompts(self):
        source = (ROOT / "src/image_worker/google_direct.py").read_text()
        self.assertIn('"responseModalities": ["TEXT", "IMAGE"]', source)
        self.assertIn('"aspectRatio": "9:16"', source)
        self.assertIn('"imageSize": "2K"', source)
        self.assertIn('def extract_png(result):', source)
        self.assertNotIn("api.replicate.com", source)
        self.assertNotIn("REPLICATE_API_TOKEN", source)


if __name__ == "__main__":
    unittest.main()
