"""Offline checks for Google Nano Banana image prompts (no paid calls)."""
import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scenarios/afl-grand-final-winning-kick/v1/config.json"
WORKER = ROOT / "src/image_worker/handler.py"
module = ast.parse(WORKER.read_text(encoding="utf-8"))
functions = [n for n in module.body if isinstance(n, ast.FunctionDef)
             and n.name in ("validate_prompt_config", "build_image_prompt")]
assert len(functions) == 2
ns = {}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(WORKER), "exec"), ns)

class GooglePromptTests(unittest.TestCase):
    def setUp(self):
        self.c = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.scenes = self.c["scenes"]
        self.shared = self.c["identityOnlyPrompt"]

    def test_shared_is_authoritative_and_scenes_short(self):
        ns["validate_prompt_config"](self.c)
        self.assertEqual([s["id"] for s in self.scenes], ["scene1","scene2","scene3"])
        self.assertGreater(len(self.shared), 100)
        self.assertLessEqual(len(self.shared), 8000)
        for word in ("Image 1", "Image 2", "selfie", "head", "hair",
                     "guernsey", "shorts", "skin", "body", "pose", "background"):
            self.assertIn(word.lower(), self.shared.lower())
        for s in self.scenes:
            self.assertEqual(s["operation"], "image_edit")
            self.assertEqual(s["model"], "gemini-3-pro-image")
            self.assertTrue(100 <= len(s["identityOnlyInstructions"]) <= 5000)
            rendered = ns["build_image_prompt"](self.c, s, "geelong")
            self.assertEqual(rendered.count(self.shared), 1)
            self.assertEqual(rendered.count(s["identityOnlyInstructions"]), 1)
            self.assertIn("finished team-specific MASTER PHOTO", rendered)

    def test_no_contradictory_shorts_lock(self):
        self.assertIn("shorts", self.shared.lower())
        self.assertNotIn("never modify shorts", self.shared.lower())
        self.assertNotIn("guernsey only", self.shared.lower())

    def test_scene1_action_preserved(self):
        s=self.scenes[0]["identityOnlyInstructions"].lower()
        for w in ("defender","ball","running","goalposts"):
            self.assertIn(w,s)

    def test_scene2_four_posts_and_rear_view(self):
        s=self.scenes[1]["identityOnlyInstructions"].lower()
        for w in ("rear","four","goalposts","red","ball"):
            self.assertIn(w,s)
        video=(CONFIG.parent/"video/scene2-video.txt").read_text().lower()
        self.assertIn("four existing goalposts",video)

    def test_scene3_neutral_face_and_other_players_locked(self):
        s=self.scenes[2]["identityOnlyInstructions"].lower()
        for w in ("closed-mouth","fist","teammates","confetti"):
            self.assertIn(w,s)

    def test_consent_and_safe_existing_pipeline(self):
        self.assertIn("customer-provided selfie",self.shared.lower())
        self.assertIn("fictional",self.shared.lower())
        google=(ROOT/"src/image_worker/google_direct.py").read_text()
        self.assertIn('"responseModalities": ["TEXT", "IMAGE"]',google)
        self.assertIn('"aspectRatio": "9:16"',google)
        self.assertIn('"imageSize": "2K"',google)

    def test_missing_prompts_rejected(self):
        for invalid in ("",None,5):
            c=json.loads(json.dumps(self.c))
            c["identityOnlyPrompt"]=invalid
            with self.assertRaises(RuntimeError): ns["validate_prompt_config"](c)

if __name__ == "__main__":
    unittest.main()
