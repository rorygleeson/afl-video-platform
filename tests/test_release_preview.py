import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from preview_release_change_set import check_plan

class PreviewPreflightTests(unittest.TestCase):
    def test_plan_preserves_parameters(self):
        b={"stacks":{"backend":{"name":"afl-app-dev-backend","parameters":{"Foo":"before"}}},
           "guardrails":{"deploy_permitted":False}}
        p={"deploy_permitted":False,"stacks":{"backend":{"name":"afl-app-dev-backend",
           "current_parameters":{"Foo":"before"},"proposed_parameters":{"Foo":"after"},
           "changed_parameters":{"Foo":{"before":"before","after":"after"}}}}}
        name, template, params=check_plan(p,b,"backend",Path(__file__).resolve().parents[1])
        self.assertEqual(name,"afl-app-dev-backend")
        self.assertEqual(params,[{"ParameterKey":"Foo","ParameterValue":"after"}])
    def test_detects_undocumented_parameter_change(self):
        b={"stacks":{"backend":{"name":"afl-app-dev-backend","parameters":{"Foo":"before"}}},
           "guardrails":{"deploy_permitted":False}}
        p={"deploy_permitted":False,"stacks":{"backend":{"name":"afl-app-dev-backend",
           "current_parameters":{"Foo":"before"},"proposed_parameters":{"Foo":"after"},
           "changed_parameters":{}}}}
        with self.assertRaisesRegex(ValueError,"diff"):
            check_plan(p,b,"backend",Path(__file__).resolve().parents[1])
    def test_rejects_enabled_deployment_flag(self):
        b={"stacks":{},"guardrails":{"deploy_permitted":False}}
        with self.assertRaises(ValueError):
            check_plan({"deploy_permitted":True},b,"backend",Path("."))
if __name__ == "__main__":
    unittest.main()
