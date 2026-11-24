"""Offline behavioural tests for isolated assembly handoff (no AWS or Google calls)."""
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
API_FILE = ROOT / "src/api/handler.py"
JOB_ID = "a" * 32
MACHINE = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:afl-app-dev-assembly"


class AlreadyExists(Exception):
    pass


class AssemblyHandoffBehaviourTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(API_FILE.read_text(encoding="utf-8"))
        selected = [node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name in ("ensure_assembly_execution", "start_assembly")]
        self.assertEqual(len(selected), 2)
        self.states = MagicMock()
        self.states.exceptions.ExecutionAlreadyExists = AlreadyExists
        self.ddb, self.s3 = MagicMock(), MagicMock()
        self.s3.head_object.return_value = {"ContentLength": 1024}
        self.job = {"jobId": JOB_ID, "assembly": {"status": "QUEUED"},
                    "realVideos": {s: {"status": "COMPLETED",
                                       "resultKey": f"videos/{JOB_ID}/{s}.mp4"}
                                   for s in ("scene1", "scene2", "scene3")}}
        self.env = {
            "ASSEMBLY_STATE_MACHINE_ARN": MACHINE, "STATES": self.states,
            "DDB": self.ddb, "S3": self.s3, "UPLOAD_BUCKET": "test-bucket",
            "VIDEO_SCENES": ("scene1", "scene2", "scene3"),
            "json": json, "ClientError": ClientError,
            "LOG": MagicMock(), "get_job": lambda _: self.job,
            "definitive_failure": lambda job: False,
            "assembly_public": lambda job: job.get("assembly", {}),
            "response": lambda status, data: {"statusCode": status, "body": json.dumps(data)},
        }
        import datetime
        self.env["datetime"] = datetime.datetime
        self.env["timezone"] = datetime.timezone
        module = ast.Module(body=selected, type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(API_FILE), "exec"), self.env)

    def error(self, code):
        return ClientError({"Error": {"Code": code, "Message": code}}, "DescribeExecution")

    def test_existing_execution_does_not_start_another(self):
        self.states.describe_execution.return_value = {"status": "RUNNING"}
        response = self.env["start_assembly"](JOB_ID)
        self.assertEqual(response["statusCode"], 202)
        self.assertEqual(json.loads(response["body"])["executionStatus"], "RUNNING")
        self.states.start_execution.assert_not_called()

    def test_terminal_execution_requires_review_not_new_start(self):
        self.states.describe_execution.return_value = {"status": "FAILED"}
        response = self.env["start_assembly"](JOB_ID)
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(json.loads(response["body"])["status"],
                         "ASSEMBLY_WORKFLOW_NEEDS_REVIEW")
        self.states.start_execution.assert_not_called()

    def test_missing_execution_starts_stable_name_once(self):
        self.states.describe_execution.side_effect = self.error("ExecutionDoesNotExist")
        response = self.env["start_assembly"](JOB_ID)
        self.assertEqual(json.loads(response["body"])["status"], "QUEUED")
        args = self.states.start_execution.call_args.kwargs
        self.assertEqual(args["name"], "final-" + JOB_ID)
        self.assertEqual(args["stateMachineArn"], MACHINE)
        self.assertEqual(json.loads(args["input"]), {"jobId": JOB_ID})

    def test_unknown_describe_error_fails_closed(self):
        self.states.describe_execution.side_effect = self.error("AccessDeniedException")
        response = self.env["start_assembly"](JOB_ID)
        self.assertEqual(response["statusCode"], 503)
        self.states.start_execution.assert_not_called()

    def test_race_execution_already_exists_is_safe(self):
        self.states.describe_execution.side_effect = self.error("ExecutionDoesNotExist")
        self.states.start_execution.side_effect = AlreadyExists()
        response = self.env["start_assembly"](JOB_ID)
        self.assertEqual(json.loads(response["body"])["status"], "ALREADY_REQUESTED")
        self.states.start_execution.assert_called_once()

    def test_completed_assembly_is_not_restarted(self):
        self.job["assembly"] = {"status": "COMPLETED", "resultKey": f"videos/{JOB_ID}/final.mp4"}
        self.env["assembly_public"] = lambda job: job["assembly"]
        response = self.env["start_assembly"](JOB_ID)
        self.assertEqual(response["statusCode"], 200)
        self.states.describe_execution.assert_not_called()
        self.states.start_execution.assert_not_called()

    def test_missing_scene_blocks_restart(self):
        self.job["realVideos"]["scene2"]["status"] = "FAILED"
        response = self.env["start_assembly"](JOB_ID)
        self.assertEqual(response["statusCode"], 409)
        self.states.start_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
