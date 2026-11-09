"""Stage 6 offline contract tests. No AWS access, paid models or FFmpeg required."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src/assembly_worker/handler.py"
API = ROOT / "src/api/handler.py"
FRONTEND = ROOT / "frontend/index.html"


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

    def test_existing_scene_download_contract_preserved(self):
        api = API.read_text(encoding="utf-8")
        self.assertIn('f"videos/{job[\'jobId\']}/{scene_id}.mp4"', api)
        self.assertIn('item.get("resultKey")', api)
        self.assertIn('"assembly": assembly_public(job)', api)
        self.assertIn('id="assembleFinal"', FRONTEND.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
