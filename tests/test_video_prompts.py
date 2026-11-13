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
                self.assertIn("Geelong Cats", result["prompt"]) if scene == "scene3" else self.assertNotIn("Geelong Cats", result["prompt"])
                self.assertIn("navy blue and white horizontal hoops", result["prompt"])
                self.assertIn(result["commentary"], result["prompt"])
                self.assertNotIn("$fullName", result["prompt"])
                self.assertNotIn("$commentary", result["prompt"])
                self.assertFalse(result["videoGenerationEnabled"])
                self.assertFalse(result["videoSubmissionAvailable"])
                self.assertNotIn("videoConcurrency", result)
                self.assertEqual(result["sceneId"], scene)
        scene3 = mod.render_scene(LocalS3(), "test-scenario-bucket", job, "scene3")
        self.assertEqual(scene3["commentary"], "Alex Tonelli has won the premiership for Geelong Cats!")
        expected = {
            "scene1": ("Tonelli breaks free! SIX SECONDS LEFT!", 4),
            "scene2": ("He kicks… IT'S A GOAL!", 8),
            "scene3": ("Alex Tonelli has won the premiership for Geelong Cats!", 5),
        }
        for scene_id, (commentary, duration) in expected.items():
            with self.subTest(scene_id=scene_id):
                result = mod.render_scene(LocalS3(), "test-scenario-bucket", job, scene_id)
                self.assertEqual(result["commentary"], commentary)
                self.assertEqual(result["durationSeconds"], duration)
                self.assertIn(commentary, result["prompt"])
                self.assertTrue(result["prompt"].strip())

    def test_scene2_goalposts_are_fixed_architecture_not_generated_objects(self):
        job = {"team": "geelong", "firstName": "Alex", "lastName": "Tonelli"}
        result = mod.render_scene(LocalS3(), "test-scenario-bucket", job, "scene2")
        prompt = result["prompt"]
        self.assertIn("FOUR EXISTING GOALPOSTS — IMMUTABLE BACKGROUND ARCHITECTURE", prompt)
        self.assertIn("TWO tall central goalposts", prompt)
        self.assertIn("TWO shorter outer behind posts", prompt)
        self.assertIn("Never add or invent a fifth post", prompt)
        self.assertIn("their image positions must NEVER move", prompt)
        self.assertIn("CAMERA — COMPLETELY STATIONARY FOR THE ENTIRE CLIP", prompt)
        self.assertIn("No pan, tilt, zoom, tracking", prompt)
        self.assertIn("WHILE IT IS STILL AIRBORNE", prompt)
        self.assertIn("NOT a rugby drop kick", prompt)
        self.assertIn("never touching a post", prompt)
        self.assertNotIn("camera may", prompt.lower())
        self.assertNotIn("small upward tilt", prompt.lower())
        self.assertEqual(result["durationSeconds"], 8)
        self.assertEqual(result["commentary"], "He kicks… IT'S A GOAL!")

    def test_scene2_never_turns_to_camera_and_ball_flies_high(self):
        job = {"team": "geelong", "firstName": "Alex", "lastName": "Tonelli"}
        prompt = mod.render_scene(LocalS3(), "test-scenario-bucket", job, "scene2")["prompt"]
        self.assertIn("PLAYER ORIENTATION AND IDENTITY — ALWAYS FACING AWAY", prompt)
        self.assertIn("NO TURN TO CAMERA", prompt)
        self.assertIn("no glance or turn towards the camera", prompt.lower())
        self.assertIn("never show a frontal face", prompt.lower())
        self.assertIn("rockets HIGH into the air", prompt)
        self.assertIn("HIGH ABOVE THE GROUND", prompt)
        self.assertIn("below their tops", prompt)
        self.assertIn("BETWEEN the TWO TALL CENTRAL POSTS", prompt)
        self.assertIn("CAMERA — COMPLETELY STATIONARY", prompt)
        self.assertIn("Never add or invent a fifth post", prompt)
        self.assertIn("WHILE IT IS STILL AIRBORNE", prompt)

    def test_old_video_templates_preserved_and_active_templates_unbranded(self):
        image = json.loads((SCENARIO / "config.json").read_text())
        for scene in ("scene1", "scene2", "scene3"):
            source = (SCENARIO / "video" / f"{scene}-video.txt").read_text()
            original = (SCENARIO / "video" / f"{scene}-video_old.txt").read_text()
            self.assertIn("AFL Grand Final", original)
            self.assertIn("BRANDING:", source)
            self.assertIn("$teamName", source) if scene == "scene3" else self.assertNotIn("$teamName", source)
            self.assertNotIn("Essendon", source)
        self.assertEqual(image["version"], "v1")
        self.assertEqual(
            json.loads((SCENARIO / "video" / "config_old.json").read_text())["scenes"][2]["durationSeconds"], 5)

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
