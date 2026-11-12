"""Offline contract test for master-template-preserving image prompt revision."""
import json
import unittest
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "scenarios/afl-grand-final-winning-kick/v1/config.json"


class TemplatePreservationPromptTests(unittest.TestCase):
    def test_master_frame_lock_is_shared_by_all_scenes(self):
        cfg = json.loads(CONFIG.read_text())
        shared = cfg["sharedPrompt"]
        self.assertIn("MASTER FRAME LOCK", shared)
        self.assertIn("Preserve Image 1 exactly", shared)
        self.assertIn("Image 1", shared)
        self.assertIn("Image 2 = the CUSTOMER SELFIE, the ONLY source", shared)
        self.assertIn("central-player skin blending", shared)
        self.assertLessEqual(len(shared), 8000)
        for scene in cfg["scenes"]:
            self.assertEqual(scene["operation"], "image_edit")
            self.assertEqual(scene["model"], "gemini-3-pro-image")
            self.assertLessEqual(len(scene["promptInstructions"]), 5000)

    def test_scene_one_freezes_existing_composition(self):
        cfg = json.loads(CONFIG.read_text())
        scene = cfg["scenes"][0]["promptInstructions"]
        for phrase in ("same Scene 1 master photograph", "ONE red Australian rules football",
                       "opposition", "NO visible goalposts", "Replace ONLY"):
            self.assertIn(phrase, scene)

if __name__ == "__main__":
    unittest.main()
