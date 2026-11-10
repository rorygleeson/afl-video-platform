import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from preview_release_change_set import check_plan, preview

class PreviewPreflightTests(unittest.TestCase):
    def test_backend_uses_exact_deployed_template(self):
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            deployed = root / "deployed.yaml"
            deployed.write_text("AWSTemplateFormatVersion: '2010-09-09'\
")
            params = {"Foo": "before"}
            b = {"stacks": {"backend": {"name": "afl-app-dev-backend",
                "parameters": params, "deployed_template_path": "deployed.yaml",
                "deployed_template_sha256": hashlib.sha256(deployed.read_bytes()).hexdigest()}},
                "guardrails": {"deploy_permitted": False}}
            p = {"deploy_permitted": False, "stacks": {"backend": {
                "name": "afl-app-dev-backend", "current_parameters": params,
                "proposed_parameters": params, "changed_parameters": {}}}}
            name, template, values = check_plan(p, b, "backend", root)
            self.assertEqual(template, deployed)
            deployed.write_text("tampered")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                check_plan(p, b, "backend", root)
    def test_plan_preserves_parameters(self):
        b={"stacks":{"backend":{"name":"afl-app-dev-backend","parameters":{"Foo":"before"}}},
           "guardrails":{"deploy_permitted":False}}
        p={"deploy_permitted":False,"stacks":{"backend":{"name":"afl-app-dev-backend",
           "current_parameters":{"Foo":"before"},"proposed_parameters":{"Foo":"after"},
           "changed_parameters":{"Foo":{"before":"before","after":"after"}}}}}
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            deployed = Path(d) / "backend.yaml"
            deployed.write_text("template")
            b["stacks"]["backend"]["deployed_template_path"] = "backend.yaml"
            b["stacks"]["backend"]["deployed_template_sha256"] = hashlib.sha256(deployed.read_bytes()).hexdigest()
            name, template, params=check_plan(p,b,"backend",Path(d))
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
class DependencyPreviewTests(unittest.TestCase):
    def test_expected_dynamic_dependency_is_accepted(self):
        from validate_change_set import validate
        change = {"Status": "CREATE_COMPLETE", "Changes": [{"ResourceChange": {
            "LogicalResourceId": "ProxyAny", "Action": "Modify",
            "Replacement": "False", "Details": [{
                "Target": {"Attribute": "Properties", "Name": "Integration",
                           "RequiresRecreation": "Never"},
                "Evaluation": "Dynamic", "ChangeSource": "ResourceAttribute",
                "CausingEntity": "ApiLambda.Arn"}]}}]}
        self.assertEqual(validate(change, {"ApiLambda"}), [])
        change["Changes"][0]["ResourceChange"]["Details"][0]["CausingEntity"] = "OtherLambda.Arn"
        self.assertTrue(validate(change, {"ApiLambda"}))
    def test_conditional_permission_remains_blocked(self):
        from validate_change_set import validate
        change = {"Status": "CREATE_COMPLETE", "Changes": [{"ResourceChange": {
            "LogicalResourceId": "ImageReconcileInvokePermission",
            "Action": "Modify", "Replacement": "Conditional",
            "Details": [{"Target": {"Attribute": "Properties", "Name": "SourceArn",
                                   "RequiresRecreation": "Always"},
                         "Evaluation": "Dynamic", "ChangeSource": "ResourceAttribute",
                         "CausingEntity": "ImageReconcileSchedule.Arn"}]}}]}
        errors = validate(change, set())
        self.assertTrue(any("replacement" in e for e in errors))
        self.assertTrue(any("recreation" in e for e in errors))

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
