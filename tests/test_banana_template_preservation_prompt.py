"""Offline contracts for minimal scene prompts and shared Nano Banana edit rules."""
import json
import unittest
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "scenarios/afl-grand-final-winning-kick/v1/config.json"

class TemplatePreservationPromptTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_master_image_and_selfie_roles(self):
        prompt = self.config["sharedPrompt"].lower()
        for term in ("image 1", "image 2", "master", "selfie", "face", "hair",
                     "jersey", "shorts", "skin", "position", "pose", "background"):
            self.assertIn(term, prompt)
        self.assertLessEqual(len(prompt), 8000)

    def test_scene_prompt_is_only_scene_specific(self):
        for scene in self.config["scenes"]:
            self.assertEqual(scene["operation"], "image_edit")
            self.assertEqual(scene["model"], "gemini-3-pro-image")
            self.assertLessEqual(len(scene["promptInstructions"].split()), 115)

    def test_scene1_keeps_defender_ball_and_running_pose(self):
        s = self.config["scenes"][0]["promptInstructions"].lower()
        for term in ("defender", "ball", "running", "goalposts"):
            self.assertIn(term, s)

    def test_scene2_goalposts_and_rear_facing(self):
        s = self.config["scenes"][1]["promptInstructions"].lower()
        for term in ("rear", "four", "goalposts", "80%", "red", "ball"):
            self.assertIn(term, s)

    def test_scene3_hair_and_neutral_expression(self):
        s = self.config["scenes"][2]["promptInstructions"].lower()
        for term in ("fist", "teammates", "closed-mouth", "confetti"):
            self.assertIn(term, s)

if __name__ == "__main__":
    unittest.main()
