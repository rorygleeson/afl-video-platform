"""Executable offline safety tests for the FFmpeg retry admission guard.

Runs the actual assemble() function with fake AWS; deliberately fails before
any temporary media processing. No provider, AWS, or filesystem operations.
"""
import ast
from pathlib import Path
import re
import unittest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

FILE = Path(__file__).resolve().parents[1] / "src/assembly_worker/handler.py"
ID = "a" * 32
SCENES = ("scene1", "scene2", "scene3")


class AssemblyRetryRuntimeTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse(FILE.read_text(encoding="utf-8"))
        node = next(n for n in source.body
                    if isinstance(n, ast.FunctionDef) and n.name == "assemble")
        code = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                       str(FILE), "exec")
        self.job = {"jobId": ID,
                    "assembly": {"status": "FAILED", "retryCount": 0},
                    "realVideos": {s: {"status": "COMPLETED",
                                       "resultKey": f"videos/{ID}/{s}.mp4"}
                                   for s in SCENES}}
        self.ddb = MagicMock()
        self.ddb.get_item.return_value = {"Item": self.job}
        self.set_status = MagicMock()
        self.tempfile = MagicMock()
        self.tempfile.TemporaryDirectory.side_effect = RuntimeError("synthetic media failure")
        self.env = {
            "DDB": self.ddb, "JOB_PATTERN": re.compile("^[0-9a-f]{32}$"),
            "SCENES": SCENES, "AssemblyError": type("AssemblyError", (RuntimeError,), {}),
            "set_status": self.set_status, "tempfile": self.tempfile,
            "ClientError": ClientError, "LOG": MagicMock(),
        }
        exec(code, self.env)

    def test_confirmed_failed_worker_can_retry_once(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic media failure"):
            self.env["assemble"](ID, retry=True)
        self.assertEqual(self.set_status.call_args_list[0].args,
                         (ID, "FAILED", "PROCESSING"))
        self.assertEqual(self.set_status.call_args_list[0].kwargs["retryCount"], 1)
        self.assertEqual(self.set_status.call_args_list[1].args,
                         (ID, "PROCESSING", "FAILED"))

    def test_unknown_processing_timeout_must_not_retry(self):
        self.job["assembly"] = {"status": "PROCESSING"}
        with self.assertRaisesRegex(RuntimeError, "ASSEMBLY_RETRY_NOT_SAFE"):
            self.env["assemble"](ID, retry=True)
        self.set_status.assert_not_called()
        self.tempfile.TemporaryDirectory.assert_not_called()

    def test_exhausted_failed_worker_cannot_retry_again(self):
        self.job["assembly"] = {"status": "FAILED", "retryCount": 1}
        with self.assertRaisesRegex(RuntimeError, "ASSEMBLY_RETRY_NOT_SAFE"):
            self.env["assemble"](ID, retry=True)
        self.set_status.assert_not_called()

    def test_completed_video_cannot_be_overwritten_by_retry(self):
        self.job["assembly"] = {"status": "COMPLETED"}
        with self.assertRaisesRegex(RuntimeError, "ASSEMBLY_RETRY_NOT_SAFE"):
            self.env["assemble"](ID, retry=True)
        self.set_status.assert_not_called()

    def test_initial_attempt_only_accepts_queued(self):
        with self.assertRaisesRegex(RuntimeError, "ASSEMBLY_NOT_QUEUED"):
            self.env["assemble"](ID, retry=False)
        self.set_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
