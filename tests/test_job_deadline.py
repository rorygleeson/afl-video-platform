"""Offline 15-minute deadline contract and safety checks. Zero AWS / Google calls."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
API=ROOT/"src/api/handler.py"
IMAGE=ROOT/"src/image_worker/handler.py"
IMAGE_GOOGLE=ROOT/"src/image_worker/google_direct.py"
VIDEO=ROOT/"src/google_video_worker/handler.py"
WATCHDOG=ROOT/"src/handoff_watchdog/handler.py"
IAM=ROOT/"infrastructure/app/handoff-watchdog.yaml"


def helpers():
    tree=ast.parse(API.read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef)
           and n.name in ("job_deadline","admission_deadline_blocked")]
    scope={"time":__import__("time"),"JOB_MAX_SECONDS":900}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(API),"exec"),scope)
    return scope


class FifteenMinuteDeadlineTests(unittest.TestCase):
    def test_exact_boundary_and_legacy_generation_start(self):
        scope=helpers()
        with patch.object(scope["time"],"time",return_value=1000):
            self.assertEqual(scope["job_deadline"]({"generationStartedAt":100}),1000)
            self.assertTrue(scope["admission_deadline_blocked"]({"generationStartedAt":100}))
            self.assertFalse(scope["admission_deadline_blocked"]({"generationStartedAt":101}))
            self.assertTrue(scope["admission_deadline_blocked"]({"jobDeadlineAt":999}))
            self.assertFalse(scope["admission_deadline_blocked"]({"jobDeadlineAt":1001}))
            self.assertTrue(scope["admission_deadline_blocked"]({"deadlineStatus":"EXPIRED"}))
            self.assertFalse(scope["admission_deadline_blocked"]({"createdAt":"some date"}))

    def test_only_worker_initial_prepare_creates_deadline(self):
        source=IMAGE.read_text()
        self.assertIn("jobDeadlineAt = :deadline",source)
        self.assertIn('":deadline": int(time.time()) + 900',source)
        self.assertIn('#status = :waiting',source)

    def test_all_api_paid_admissions_have_deadline_and_atomic_fences(self):
        tree=ast.parse(API.read_text())
        names=("start_real_scene","start_video_sequence","retry_failed_real_scene","start_assembly")
        for n in tree.body:
            if isinstance(n,ast.FunctionDef) and n.name in names:
                code=ast.get_source_segment(API.read_text(),n)
                self.assertIn("admission_deadline_blocked(job)",code,n.name)
                self.assertIn("jobDeadlineAt",code,n.name)
                self.assertIn("attribute_not_exists(deadlineStatus)",code,n.name)

    def test_paid_google_image_and_video_post_gates_exist(self):
        image=IMAGE_GOOGLE.read_text()
        worker=VIDEO.read_text()
        self.assertIn('latest = worker.job_by_id(job_id)',image)
        self.assertIn('raise RuntimeError("JOB_DEADLINE_EXCEEDED")',image)
        self.assertLess(image.index('latest = worker.job_by_id(job_id)'),
                        image.index('result = post_json(ENDPOINT, payload'))
        self.assertIn('raise PermissionError("JOB_DEADLINE_EXCEEDED")',worker)
        self.assertIn('require_paid_unlock(job, scene, request)\n    # Set ambiguous state',worker)
        self.assertIn('AND attribute_not_exists(deadlineStatus)',worker)
        self.assertIn('jobDeadlineAt > :nowEpoch',worker)

    def test_watchdog_only_owned_sfn_stop_privilege(self):
        code=WATCHDOG.read_text()
        config=IAM.read_text()
        self.assertIn("CUSTOMER_JOB_SECONDS = 900",code)
        self.assertIn("deadlineStatus = :expired",code)
        self.assertIn('STATES.stop_execution(executionArn=arn',code)
        self.assertNotIn("google_json(",code)
        self.assertIn("Action: states:StopExecution",config)
        self.assertIn("Action: dynamodb:UpdateItem",config)
        self.assertNotIn("Action: dynamodb:Scan",config)
        self.assertNotIn("ScheduleExpression:",config)


if __name__=="__main__":
    unittest.main()
