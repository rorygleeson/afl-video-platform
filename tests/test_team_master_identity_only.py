"""Offline team-image routing and identity-only prompt regression tests.

No AWS credentials, Google calls, or paid image generation.
"""
import ast
import json
import unittest
from pathlib import Path

BASE = Path("scenarios/afl-grand-final-winning-kick/v1")
CONFIG = json.loads((BASE / "config.json").read_text())
SOURCE = Path("src/image_worker/handler.py").read_text()
PREFIX = "scenarios/afl-grand-final-winning-kick/v1/"


class MissingObject(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "404"},
                         "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeS3:
    def __init__(self, keys):
        self.keys = set(keys)
        self.reads = []

    def head_object(self, Bucket, Key):
        self.reads.append(Key)
        if Key not in self.keys:
            raise MissingObject()
        return {"ContentLength": 2_000_000}


def exported_functions():
    tree = ast.parse(SOURCE)
    names = ("team_scene_master_key", "team_master_keys", "validate_prompt_config", "build_image_prompt")
    functions = [x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name in names]
    assert len(functions) == 4
    def unused():
        pass
    scope = {"S3": None, "ASSET_BUCKET": "test-only",
             "SCENARIO_PREFIX": PREFIX, "ClientError": MissingObject,
             "VALID_SCENES": ("scene1", "scene2", "scene3")}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "<test-functions>", "exec"), scope)
    return scope


class TeamMasterTests(unittest.TestCase):
    def setUp(self):
        self.scope = exported_functions()
        self.scope["validate_prompt_config"](CONFIG)

    def keys(self, team):
        return {self.scope["team_scene_master_key"](team, f"scene{i}")
                for i in (1, 2, 3)}

    def test_all_18_team_directories_without_selector_restrictions(self):
        self.assertEqual(len(CONFIG["teamGuernseys"]), 18)
        self.assertEqual(set(CONFIG["teamGuernseys"]),
                         {p.name for p in (BASE / "teams").iterdir() if p.is_dir()})

    def test_collingwood_uses_only_its_three_team_master_pngs(self):
        fake = FakeS3(self.keys("collingwood"))
        self.scope["S3"] = fake
        result = self.scope["team_master_keys"](CONFIG, "collingwood")
        self.assertEqual(set(result.values()), self.keys("collingwood"))
        self.assertEqual(set(fake.reads), self.keys("collingwood"))

    def test_scene3_masked_collingwood_only(self):
        for team in CONFIG["teamGuernseys"]:
            path = self.scope["team_scene_master_key"](team, "scene3")
            if team == "collingwood":
                self.assertTrue(path.endswith("/scene3-start-masked.png"))
            else:
                self.assertTrue(path.endswith("/scene3-start.png"))
        self.assertTrue(self.scope["team_scene_master_key"]("collingwood", "scene1").endswith("/scene1-start.png"))
        self.assertTrue(self.scope["team_scene_master_key"]("collingwood", "scene2").endswith("/scene2-start.png"))

    def test_missing_team_png_fails_closed_instead_of_legacy_fallback(self):
        fake = FakeS3(self.keys("collingwood"))
        self.scope["S3"] = fake
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.scope["team_master_keys"](CONFIG, "geelong")

    def test_partial_upload_rejected_before_paid_generation(self):
        fake = FakeS3(set(list(self.keys("collingwood"))[:2]))
        self.scope["S3"] = fake
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.scope["team_master_keys"](CONFIG, "collingwood")

    def test_all_team_prompts_are_identical_for_same_scene(self):
        for scene in CONFIG["scenes"]:
            prompts = [self.scope["build_image_prompt"](CONFIG, scene, team)
                       for team in CONFIG["teamGuernseys"]]
            self.assertEqual(len(set(prompts)), 1)
            text = prompts[0].lower()
            self.assertIn("selfie", text)
            self.assertIn("do not", text)
            self.assertNotIn("selected playing guernsey style", text)

    def test_exactly_four_identity_prompts_no_legacy(self):
        self.assertNotIn("sharedPrompt", CONFIG)
        self.assertIn("identityOnlyPrompt", CONFIG)
        self.assertNotIn("teamArtworkReady", SOURCE)
        self.assertNotIn('scene["masterKey"]', SOURCE)
        for scene in CONFIG["scenes"]:
            self.assertEqual(set(scene),
                             {"id", "operation", "model", "enabled", "identityOnlyInstructions"})


if __name__ == "__main__":
    unittest.main()
