"""Offline Nano Banana reference-image edit prompt regression checks."""
import json
import unittest
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "scenarios/afl-grand-final-winning-kick/v1/config.json"

class TemplatePreservationPromptTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_master_image_and_selfie_roles(self):
        shared = self.cfg["sharedPrompt"]
        for item in ("MASTER FRAME LOCK", "HIGHEST VISUAL PRIORITY",
                     "Image 1 is a LOCKED ORIGINAL PHOTOGRAPH",
                     "Image 2 = the CUSTOMER SELFIE",
                     "EVERYTHING ELSE MUST STAY AS IN IMAGE 1",
                     "body position", "background", "ONLY AUTHORISED CHANGES"):
            self.assertIn(item, shared)
        self.assertLessEqual(len(shared), 8000)

    def test_existing_shorts_and_guernsey_both_recoloured(self):
        shared = self.cfg["sharedPrompt"]
        self.assertIn("GUERNSEY (JERSEY) AND SHORTS", shared)
        self.assertIn("Never modify socks, boots", shared)
        self.assertNotIn("NEVER modify shorts", shared)
        self.assertNotIn("GUERNSEY ONLY", shared)

    def test_each_scene_edit_model_and_size(self):
        for scene in self.cfg["scenes"]:
            self.assertEqual(scene["operation"], "image_edit")
            self.assertEqual(scene["model"], "gemini-3-pro-image")
            self.assertLessEqual(len(scene["promptInstructions"]), 5000)

    def test_scene_one_existing_action_is_locked(self):
        scene = self.cfg["scenes"][0]["promptInstructions"]
        for item in ("ONE red Australian rules football", "opposition defender",
                     "NO visible goalposts", "body position", "guernsey AND shorts"):
            self.assertIn(item, scene)

    def test_scene_two_existing_goalposts_and_rear_view(self):
        scene = self.cfg["scenes"][1]["promptInstructions"]
        for item in ("REAR THREE-QUARTER", "FOUR EXISTING", "EVEN SPACING",
                     "FOUR-FIFTHS (80%)", "red base paddings", "RUNNING TOWARD",
                     "NOT BEEN DROPPED OR KICKED"):
            self.assertIn(item, scene)

    def test_scene_three_selfie_and_celebration_pose(self):
        scene = self.cfg["scenes"][2]["promptInstructions"]
        for item in ("REPLACE ENTIRE FOREGROUND HEAD AND HAIR", "REMOVE ALL TEMPLATE HAIR",
                     "CLOSED-MOUTH", "smiling starts later in VIDEO",
                     "TWO distant teammates", "NOT TOUCHING HIM", "GOLD CONFETTI"):
            self.assertIn(item, scene)

if __name__ == "__main__":
    unittest.main()
