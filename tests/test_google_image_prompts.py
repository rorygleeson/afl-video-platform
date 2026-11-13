"""Offline regression tests for S3-owned Nano Banana reference-image prompts."""
import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scenarios/afl-grand-final-winning-kick/v1/config.json"
WORKER = ROOT / "src/image_worker/handler.py"

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
        self.shared = self.config["sharedPrompt"]
        self.scenes = self.config["scenes"]

    def test_configuration_and_combined_prompts(self):
        validate_prompt_config(self.config)
        self.assertEqual([s["id"] for s in self.scenes], ["scene1", "scene2", "scene3"])
        for scene in self.scenes:
            with self.subTest(scene=scene["id"]):
                self.assertEqual(scene["operation"], "image_edit")
                self.assertEqual(scene["model"], "gemini-3-pro-image")
                prompt = build_image_prompt(self.config, scene, "geelong")
                self.assertEqual(prompt.count(self.shared), 1)
                self.assertEqual(prompt.count(scene["promptInstructions"]), 1)
                self.assertIn("navy blue and white horizontal hoops", prompt)
                self.assertLessEqual(len(scene["promptInstructions"]), 5000)
        self.assertLessEqual(len(self.shared), 8000)

    def test_master_photo_remains_authoritative(self):
        for phrase in ("MASTER FRAME LOCK", "HIGHEST VISUAL PRIORITY",
                       "Image 1 is a LOCKED ORIGINAL PHOTOGRAPH",
                       "Image 2 = the CUSTOMER SELFIE",
                       "EVERYTHING ELSE MUST STAY AS IN IMAGE 1",
                       "body position", "background", "camera"):
            self.assertIn(phrase, self.shared)
        self.assertIn("Never change the action", self.shared)
        self.assertIn("PRESERVATION OVERRIDES", self.shared)

    def test_exact_authorised_edits_not_just_guernsey(self):
        self.assertIn("GUERNSEY (JERSEY) AND SHORTS", self.shared)
        self.assertIn("EXPOSED SKIN COMPLEXION", self.shared)
        self.assertIn("entire visible head", self.shared)
        self.assertIn("Never modify socks, boots", self.shared)
        self.assertNotIn("GUERNSEY ONLY", self.shared)
        self.assertNotIn("NEVER modify shorts", self.shared)
        for scene in self.scenes:
            self.assertIn("shorts", scene["promptInstructions"].lower())

    def test_scene1_running_pose_defender_and_ball_locked(self):
        scene = self.scenes[0]["promptInstructions"]
        for phrase in ("SAME Scene 1 master photograph", "ONE red Australian rules football",
                       "BOTH hands", "opposition defender", "NO visible goalposts",
                       "body position", "red diagonal sash"):
            self.assertIn(phrase, scene)

    def test_scene2_rear_view_four_goalposts_and_opponents(self):
        scene = self.scenes[1]["promptInstructions"]
        for phrase in ("REAR THREE-QUARTER", "FAR LEFT", "NOT BEEN DROPPED OR KICKED",
                       "FOUR EXISTING", "EVEN SPACING", "FOUR-FIFTHS (80%)",
                       "red base paddings", "RUNNING TOWARD", "No background retouching"):
            self.assertIn(phrase, scene)
        self.assertIn("rear-facing view", self.shared)
        video = (CONFIG.parent / "video/scene2-video.txt").read_text(encoding="utf-8").lower()
        for phrase in ("four existing goalposts", "camera remains stationary",
                       "two tall central goalposts", "authentic australian rules drop punt"):
            self.assertIn(phrase, video)

    def test_scene3_selfie_hair_neutral_expression_and_teammates(self):
        scene = self.scenes[2]["promptInstructions"]
        for phrase in ("REPLACE ENTIRE FOREGROUND HEAD AND HAIR", "REMOVE ALL TEMPLATE HAIR",
                       "CLOSED-MOUTH", "smiling starts later in VIDEO", "TWO distant teammates",
                       "NOT TOUCHING HIM", "GOLD CONFETTI", "DO NOT INTRODUCE AN EMBRACE"):
            self.assertIn(phrase, scene)
        self.assertIn("only the EXISTING GUERNSEYS of the two distant teammates", self.shared)

    def test_fictional_context_and_no_rebranding(self):
        self.assertIn("CONSENT AND FICTIONAL CONTEXT", self.shared)
        self.assertIn("provided with their permission", self.shared)
        self.assertIn("not footage of a real match", self.shared)
        self.assertIn("does not override content safety requirements", self.shared)
        self.assertIn("master images are already free of branding", self.shared)
        for scene in self.scenes:
            self.assertNotIn("remove existing sponsor", scene["promptInstructions"].lower())

    def test_invalid_prompts_rejected_before_paid_generation(self):
        for bad in (None, "", 10, "short"):
            altered = json.loads(json.dumps(self.config))
            altered["sharedPrompt"] = bad
            with self.assertRaises(RuntimeError):
                validate_prompt_config(altered)
        for i in range(3):
            altered = json.loads(json.dumps(self.config))
            del altered["scenes"][i]["promptInstructions"]
            with self.assertRaises(RuntimeError):
                validate_prompt_config(altered)

    def test_google_request_contract_unchanged(self):
        source = (ROOT / "src/image_worker/google_direct.py").read_text(encoding="utf-8")
        self.assertIn('"responseModalities": ["TEXT", "IMAGE"]', source)
        self.assertIn('"aspectRatio": "9:16"', source)
        self.assertIn('"imageSize": "2K"', source)
        self.assertNotIn("api.replicate.com", source)

if __name__ == "__main__":
    unittest.main()
