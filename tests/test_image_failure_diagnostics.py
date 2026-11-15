"""Offline diagnostics tests. No AWS access or paid Google model calls."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
GOOGLE = ROOT / "src/image_worker/google_direct.py"
API = ROOT / "src/api/handler.py"

def isolated(names):
    tree = ast.parse(GOOGLE.read_text())
    imports = [n for n in tree.body if isinstance(n, ast.Import) and
               all(alias.name in ("json","math") for alias in n.names)]
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef,ast.ClassDef))
                 and n.name in names]
    scope = {}
    exec(compile(ast.Module(body=imports+functions,type_ignores=[]),str(GOOGLE),"exec"),scope)
    return scope

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        # Safe constants loaded by AST: never import boto3 / contact provider.
        tree = ast.parse(GOOGLE.read_text())
        allowed = ["FINISH_REASONS","BLOCK_REASONS","SAFETY_CATEGORIES"]
        constants = [n for n in tree.body if isinstance(n,ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id in allowed for t in n.targets)]
        self.scope=isolated(["GoogleOutputError","allowed_enum","image_response_diagnostic","extract_png"])
        self.scope["frozenset"] = frozenset
        exec(compile(ast.Module(body=constants,type_ignores=[]),str(GOOGLE),"exec"),self.scope)

    def failure(self, value, expected):
        with self.assertRaises(self.scope["GoogleOutputError"]) as error:
            self.scope["extract_png"](value)
        self.assertEqual(error.exception.code,expected)
        return error.exception.diagnostic

    def test_image_safety_finish_reason(self):
        d=self.failure({"candidates":[{"finishReason":"IMAGE_SAFETY",
            "content":{"parts":[{"text":"never persist sensitive narration"}]}}]},
            "GOOGLE_IMAGE_SAFETY_BLOCK")
        self.assertEqual(d["providerFinishReason"],"IMAGE_SAFETY")
        self.assertTrue(d["providerTextOnly"])
        self.assertNotIn("never persist",repr(d))

    def test_prompt_block_without_candidates(self):
        d=self.failure({"promptFeedback":{"blockReason":"SAFETY",
             "blockReasonMessage":"child privacy detail"},"candidates":[]},
             "GOOGLE_PROMPT_BLOCKED")
        self.assertEqual(d["providerBlockReason"],"SAFETY")
        self.assertNotIn("child privacy detail",repr(d))

    def test_empty_response_has_no_guessed_safety_cause(self):
        d=self.failure({"candidates":[{"finishReason":"STOP",
              "content":{"parts":[{"text":"model could not finish"}]}}]},
              "GOOGLE_NO_IMAGE_CONTENT")
        self.assertEqual(d["providerFinishReason"],"STOP")
        self.assertNotIn("model could not finish",repr(d))

    def test_unknown_enums_never_persist(self):
        d=self.failure({"candidates":[{"finishReason":"secret token blah",
             "safetyRatings":[{"category":"arbitrary private string","blocked":True}]}]},
             "GOOGLE_IMAGE_SAFETY_BLOCK")
        self.assertEqual(d["providerFinishReason"],"UNKNOWN")
        self.assertEqual(d["providerBlockedCategories"],["UNKNOWN"])

    def test_no_candidates_preserved(self):
        d=self.failure({"candidates":[]}, "GOOGLE_NO_CANDIDATES")
        self.assertEqual(d["providerCandidateCount"],0)

    def test_valid_png_still_uses_existing_normalizer(self):
        self.scope["normalise_image_png"]=lambda data,mime: (data,mime)
        result=self.scope["extract_png"]({"candidates":[{"content":{"parts":[
            {"inlineData":{"data":"png-bytes","mimeType":"image/png"}}]}}]})
        self.assertEqual(result,("png-bytes","image/png"))

    def test_retry_rules_and_sfn_unchanged(self):
        src=GOOGLE.read_text()
        self.assertIn("MAX_ATTEMPTS = 8",src)
        self.assertIn("MAX_RETRY_WINDOW_SECONDS = 600",src)
        self.assertIn("if exc.status == 429:",src)
        self.assertIn("GOOGLE_SUBMISSION_UNKNOWN",src)
        self.assertIn('{"errorCode": exc.code, **exc.diagnostic}',src)
        config=(ROOT/"infrastructure/app/backend.yaml").read_text()
        self.assertIn("MaxConcurrency: 1",config)

    def test_failed_image_dynamodb_decimal_counters_are_json_serializable(self):
        import json
        from decimal import Decimal
        tree=ast.parse(API.read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                and n.name=="public_job")
        scope={}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(API),"exec"),scope)
        item={"jobId":"a"*32,"scenarioId":"afl-grand-final-winning-kick",
              "team":"collingwood","status":"PARTIAL_SUCCESS","createdAt":"2026-11-15",
              "scenes":{
                "scene1":{"status":"FAILED","errorCode":"GOOGLE_IMAGE_SAFETY_BLOCK",
                  "providerFinishReason":"IMAGE_PROHIBITED_CONTENT",
                  "providerBlockReason":"UNKNOWN","providerBlockedCategories":[],
                  "providerCandidateCount":Decimal("1"),"providerImageParts":Decimal("0"),
                  "providerTextOnly":True},
                "scene2":{"status":"COMPLETED"},
                "scene3":{"status":"FAILED","errorCode":"GOOGLE_IMAGE_SAFETY_BLOCK",
                  "providerCandidateCount":Decimal("1"),"providerImageParts":Decimal("0")},
              }}
        public=scope["public_job"](item)
        encoded=json.loads(json.dumps(public))
        self.assertEqual(encoded["scenes"]["scene1"]["providerCandidateCount"],1)
        self.assertEqual(encoded["scenes"]["scene1"]["providerImageParts"],0)
        self.assertEqual(encoded["scenes"]["scene1"]["providerFinishReason"],
                         "IMAGE_PROHIBITED_CONTENT")
        self.assertTrue(encoded["scenes"]["scene1"]["providerTextOnly"])
        self.assertNotIn("providerCandidateCount",encoded["scenes"]["scene2"])

    def test_api_only_exposes_diagnostics_on_failure(self):
        api=API.read_text()
        self.assertIn('"providerFinishReason"',api)
        self.assertIn('"providerBlockReason"',api)
        self.assertIn('"providerBlockedCategories"',api)
        self.assertIn('if item.get("scenes", {}).get(scene_id, {}).get("status") == "FAILED"',api)

if __name__=="__main__":
    unittest.main()
