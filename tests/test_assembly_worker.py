"""Stage 6 offline contract tests. No AWS access, paid models or FFmpeg required."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src/assembly_worker/handler.py"
API = ROOT / "src/api/handler.py"
FRONTEND = ROOT / "frontend/index.html"
FRONTEND_JS = ROOT / "frontend/app.js"


class AssemblyContractTests(unittest.TestCase):
    def test_worker_is_valid_python_and_never_calls_google(self):
        source = WORKER.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('SCENES = ("scene1", "scene2", "scene3")', source)
        self.assertIn('key = f"videos/{job_id}/final.mp4"', source)
        self.assertIn('"-f", "concat", "-safe", "0"', source)
        self.assertIn('"-c", "copy"', source)
        self.assertIn('"libx264"', source)
        self.assertIn('"aac"', source)
        self.assertIn('"QUEUED", "PROCESSING"', source)
        self.assertIn('"PROCESSING", "COMPLETED"', source)
        self.assertIn('"PROCESSING", "FAILED"', source)
        self.assertNotIn("google_json", source)
        self.assertNotIn("replicate.com", source)

    def test_assembly_is_gated_and_never_auto_runs_paid_generation(self):
        api = API.read_text(encoding="utf-8")
        front = FRONTEND.read_text(encoding="utf-8")
        front_js = FRONTEND_JS.read_text(encoding="utf-8")
        infra = (ROOT / "infrastructure/app/backend.yaml").read_text(encoding="utf-8")
        self.assertIn("def start_assembly(job_id):", api)
        self.assertIn("attribute_not_exists(assembly)", api)
        self.assertIn("def assembly_public(job):", api)
        self.assertIn('"ASSEMBLY_STATE_MACHINE_ARN"', api)
        self.assertIn("AssemblyEnabled:", infra)
        self.assertIn("AssemblyOn:", infra)
        self.assertIn("readyForAssembly = realStates.every", front_js)
        self.assertIn("st.mode === 'REAL' && st.status === 'COMPLETED'", front_js)
        self.assertIn("POST", front_js)
        self.assertIn("Download Final Video MP4", front)

    def test_queued_handoff_reconciles_named_execution_without_google(self):
        api = API.read_text(encoding="utf-8")
        ast.parse(api)
        self.assertIn("def ensure_assembly_execution(job_id):", api)
        self.assertIn('execution_name = "final-" + job_id', api)
        self.assertIn("STATES.describe_execution(executionArn=execution_arn)", api)
        self.assertIn('"ExecutionDoesNotExist"', api)
        self.assertIn("return ensure_assembly_execution(job_id)", api)
        self.assertIn('assembly.get("status") != "QUEUED"', api)
        self.assertIn("attribute_not_exists(assembly)", api)
        self.assertNotIn("start_video_sequence(job_id)", api[api.index("def ensure_assembly_execution"):api.index("def video_scene_public")])

    def test_existing_scene_download_contract_preserved(self):
        api = API.read_text(encoding="utf-8")
        self.assertIn('f"videos/{job[\'jobId\']}/{scene_id}.mp4"', api)
        self.assertIn('item.get("resultKey")', api)
        self.assertIn('"assembly": assembly_public(job)', api)
        self.assertIn('id="assembleFinal"', FRONTEND.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
