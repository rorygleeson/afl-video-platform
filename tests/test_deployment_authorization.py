"""Deployment authorization must fail closed."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("deployment_gate", Path(__file__).resolve().parents[1] / "scripts/authorize_deployment.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def test_denies_without_environment_protection_and_execution(self):
        plan = {"deploy_permitted": False}
        baseline = {"guardrails": {"deploy_permitted": False},
                    "known_drift": {"google_worker": True}}
        preview = {"verdict": "SAFE_PREVIEW", "executed": False, "stack": "afl-app-dev-backend"}
        context = {"repository": "rorygleeson/afl-video-platform", "ref": "refs/heads/main",
                   "sha": "a" * 40, "environment": "afl-production-approval",
                   "operator_approved": True}
        failures = module.authorize(plan, baseline, preview, context)
        self.assertTrue(any("environment protection" in x for x in failures))
        self.assertTrue(any("same-change-set" in x for x in failures))

    def test_rejects_replacement(self):
        plan = {"deploy_permitted": False}
        baseline = {"guardrails": {"deploy_permitted": False},
                    "known_drift": {"google_worker": True}}
        preview = {"verdict": "SAFE_PREVIEW", "executed": False, "stack": "afl-app-dev-backend",
                   "changes": [{"ResourceChange": {"LogicalResourceId": "ApiLambda",
                               "Action": "Modify", "Replacement": "True"}}]}
        context = {"repository": "rorygleeson/afl-video-platform", "ref": "refs/heads/main",
                   "sha": "a" * 40, "environment": "afl-production-approval",
                   "environment_reviewers_verified": True, "operator_approved": True,
                   "same_change_set_execution_implemented": True}
        self.assertTrue(any("unsafe Lambda" in x for x in module.authorize(plan, baseline, preview, context)))


if __name__ == "__main__":
    unittest.main()
