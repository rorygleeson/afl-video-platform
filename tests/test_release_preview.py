import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from preview_release_change_set import check_plan, preview

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
class FakeCloudFormation:
    def __init__(self, changes, status="CREATE_COMPLETE", reason=""):
        self.changes = changes
        self.status = status
        self.reason = reason
        self.created = False
        self.deleted = False
    def create_change_set(self, **kwargs):
        self.created = True
    def describe_change_set(self, **kwargs):
        return {"Status": self.status, "StatusReason": self.reason,
                "Changes": self.changes}
    def delete_change_set(self, **kwargs):
        self.deleted = True

class PreviewLifecycleTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.template = Path(self.temp.name) / "stack.yaml"
        self.template.write_text("AWSTemplateFormatVersion: '2010-09-09'")
    def test_safe_preview_is_always_deleted(self):
        changes = [{"ResourceChange": {"LogicalResourceId": "ApiLambda",
                   "Action": "Modify", "Replacement": "False"}}]
        cf = FakeCloudFormation(changes)
        result = preview(cf, "afl-app-dev-backend", self.template, [], {"ApiLambda"}, "123")
        self.assertEqual(result["verdict"], "SAFE_PREVIEW")
        self.assertTrue(cf.created and cf.deleted)
        self.assertFalse(result["executed"])
    def test_unexpected_change_blocked_and_deleted(self):
        changes = [{"ResourceChange": {"LogicalResourceId": "JobsTable",
                   "Action": "Remove", "Replacement": "False"}}]
        cf = FakeCloudFormation(changes)
        result = preview(cf, "afl-app-dev-backend", self.template, [], {"ApiLambda"}, "123")
        self.assertEqual(result["verdict"], "BLOCKED")
        self.assertTrue(cf.deleted)
    def test_empty_change_set_deleted(self):
        cf = FakeCloudFormation([], "FAILED", "The submitted information didn't contain changes.")
        result = preview(cf, "afl-app-dev-backend", self.template, [], set(), "123")
        self.assertEqual(result["verdict"], "NO_CHANGES")
        self.assertTrue(cf.deleted)

if __name__ == "__main__":
    unittest.main()
