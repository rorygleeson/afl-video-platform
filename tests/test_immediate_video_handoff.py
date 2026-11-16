"""Offline guard: fixed video scheduling waits removed; no cloud or model calls."""
import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/deploy_google_immediate_handoff.py"
TREE = ast.parse(SOURCE.read_text())
NODES = [n for n in TREE.body if isinstance(n, (ast.Assign, ast.FunctionDef)) and
         ((isinstance(n, ast.FunctionDef) and n.name == "immediate_definition") or
          (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("OLD","NEW") for t in n.targets)))]
scope = {"json":json}
exec(compile(ast.Module(body=NODES, type_ignores=[]),str(SOURCE),"exec"),scope)


class ImmediateHandoffTests(unittest.TestCase):
    def test_live_definition_removes_only_two_waits_and_choice(self):
        original = {
            "StartAt": "PaidGate", "TimeoutSeconds": 3600,
            "States": {
                "PaidGate": {"Type":"Task","Next":"Claim"},
                "Claim":{"Type":"Task","Next":"SubmissionSchedule"},
                "SubmissionSchedule":{"Type":"Choice","Default":"Submit",
                  "Choices":[{"Variable":"$.sceneId","StringEquals":"scene2","Next":"WaitScene2"},
                             {"Variable":"$.sceneId","StringEquals":"scene3","Next":"WaitScene3"}]},
                "WaitScene2":{"Type":"Wait","Seconds":10,"Next":"Submit"},
                "WaitScene3":{"Type":"Wait","Seconds":20,"Next":"Submit"},
                "Submit":{"Type":"Task","Next":"WaitForVideo"},
                "WaitForVideo":{"Type":"Wait","Seconds":20,"Next":"PollVideo"},
                "PollVideo":{"Type":"Task","Retry":[{"ErrorEquals":["States.TaskFailed"],"MaxAttempts":3}],"Next":"Finalize"},
                "Finalize":{"Type":"Task","End":True},
            }
        }
        after = scope["immediate_definition"](json.dumps(original))
        self.assertEqual(after["States"]["Claim"]["Next"],"Submit")
        self.assertEqual(set(original["States"])-set(after["States"]),
                         {"SubmissionSchedule","WaitScene2","WaitScene3"})
        for key in after["States"]:
            if key != "Claim":
                self.assertEqual(original["States"][key],after["States"][key])
        self.assertEqual(original["TimeoutSeconds"],after["TimeoutSeconds"])

    def test_infrastructure_template_is_direct_handoff_no_stagger(self):
        template = (ROOT/"infrastructure/app/google-video.yaml").read_text()
        self.assertIn('"ResultPath":"$.claim","Next":"Submit"',template)
        for state in ("SubmissionSchedule","WaitScene2","WaitScene3"):
            self.assertNotIn('"'+state+'"',template)
        self.assertIn('"WaitForVideo":{"Type":"Wait","Seconds":20',template)
        self.assertNotIn('"Retry":',template)

    def test_fails_closed_on_changed_live_waits(self):
        original = {"TimeoutSeconds":3600,"States":{
            "Claim":{"Next":"SubmissionSchedule"},
            "SubmissionSchedule":{"Default":"Submit"},
            "WaitScene2":{"Type":"Wait","Seconds":0,"Next":"Submit"},
            "WaitScene3":{"Type":"Wait","Seconds":20,"Next":"Submit"},
            "Submit":{},"PollVideo":{},"WaitForVideo":{}
        }}
        with self.assertRaises(ValueError):
            scope["immediate_definition"](json.dumps(original))


if __name__ == "__main__":
    unittest.main()
