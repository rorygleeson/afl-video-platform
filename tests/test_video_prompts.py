"""Offline strict-video-prompt tests; no paid inference or AWS network calls."""
import importlib.util
import io
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "afl-grand-final-winning-kick" / "v1"
spec = importlib.util.spec_from_file_location("video_prompts", ROOT / "src" / "api" / "video_prompts.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class LocalS3:
    def get_object(self, Bucket, Key):
        assert Bucket == "test-scenario-bucket"
        path = ROOT / Key
        return {"Body": io.BytesIO(path.read_bytes())}


class VideoPromptTests(unittest.TestCase):
    def test_prompt_substitutions_for_each_scene(self):
        job = {"team": "geelong", "firstName": "Alex", "lastName": "Tonelli"}
        for scene in ("scene1", "scene2", "scene3"):
            with self.subTest(scene=scene):
                result = mod.render_scene(LocalS3(), "test-scenario-bucket", job, scene)
                self.assertIn("Alex Tonelli", result["prompt"])
                self.assertIn("Geelong Cats", result["prompt"])
                self.assertIn("navy blue and white horizontal hoops", result["prompt"])
                self.assertIn(result["commentary"], result["prompt"])
                self.assertNotIn("$fullName", result["prompt"])
                self.assertNotIn("$commentary", result["prompt"])
                self.assertFalse(result["videoGenerationEnabled"])
                self.assertFalse(result["videoSubmissionAvailable"])
                self.assertNotIn("videoConcurrency", result)
                self.assertEqual(result["sceneId"], scene)
        scene3 = mod.render_scene(LocalS3(), "test-scenario-bucket", job, "scene3")
        self.assertEqual(scene3["commentary"], "Tonelli wins it for Geelong Cats! They\u0027re the premiers!")
        expected = {
            "scene1": ("Tonelli has it! Ten seconds to go!", 8),
            "scene2": ("He goes for goal! What a kick! He\u0027s scored!", 8),
            "scene3": ("Tonelli wins it for Geelong Cats! They\u0027re the premiers!", 10),
        }
        for scene_id, (commentary, duration) in expected.items():
            with self.subTest(scene_id=scene_id):
                result = mod.render_scene(LocalS3(), "test-scenario-bucket", job, scene_id)
                self.assertEqual(result["commentary"], commentary)
                self.assertEqual(result["durationSeconds"], duration)
                self.assertIn(commentary, result["prompt"])
                self.assertIn("ESSENDON" if scene_id != "scene1" else "Essendon", result["prompt"])

    def test_mandatory_name_and_input_validation(self):
        for value in ("", "Bob123", "Bob;", "$fullName", "x" * 41):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    mod.validate_name(value, "First name")
        for value in ("Rory", "O'Brien", "Mary-Jane", "José"):
            self.assertEqual(mod.validate_name(value, "First name"), value)
        with self.assertRaises(ValueError):
            mod.render_scene(LocalS3(), "test-scenario-bucket",
                             {"team": "geelong", "firstName": "Alex", "lastName": "Tonelli"},
                             "scene4")

    def test_paid_video_requires_explicit_caller_opt_in(self):
        cfg = json.loads((SCENARIO / "video" / "config.json").read_text())
        self.assertIs(cfg["videoGenerationEnabled"], True)
        self.assertNotIn("videoConcurrency", cfg)
        self.assertEqual(cfg["maxPaidScenesPerRequest"], 1)
        self.assertEqual(len(cfg["scenes"]), 3)
        job = {"team": "geelong", "firstName": "Alex", "lastName": "Tonelli"}
        self.assertFalse(mod.render_scene(LocalS3(), "test-scenario-bucket", job, "scene1")["videoGenerationEnabled"])
        self.assertTrue(mod.render_scene(LocalS3(), "test-scenario-bucket", job, "scene1", allow_paid=True)["videoGenerationEnabled"])


if __name__ == "__main__":
    unittest.main()
