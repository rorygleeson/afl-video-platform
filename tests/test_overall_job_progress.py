"""Pure, offline contract tests for the computed overall job progress.

The function is extracted by AST so the test does not initialise boto3
clients, load provider packages, or make any AWS/Google requests.
Run: python -m unittest discover -s tests -p 'test_overall_job_progress.py' -v
"""
import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "api" / "handler.py"
source = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
progress_fn = next(
    node for node in source.body
    if isinstance(node, ast.FunctionDef) and node.name == "overall_job_progress"
)


def make_job(status="COMPLETED", images=None, videos=None, assembly=None, sequence=None, approvals=None):
    if images is None:
        images = ("COMPLETED",) * 3
    return {
        "jobId": "a" * 32,
        "status": status,
        "scenes": {
            "scene" + str(n): {"status": value}
            for n, value in enumerate(images, start=1)
        },
        "realVideos": videos or {},
        "assembly": assembly or {},
        "videoSequence": sequence or {},
        "paidVideoApprovals": approvals or {},
    }


def complete_video(job, number):
    scene = "scene" + str(number)
    job["realVideos"][scene] = {
        "status": "COMPLETED",
        "resultKey": f"videos/{job['jobId']}/{scene}.mp4",
    }


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.video_calls = []
        self.assembly_calls = []
        self.video_responses = {}
        self.assembly_response = {"status": "NOT_READY"}

        def video_projection(job, scene):
            self.video_calls.append(scene)
            return {"status": self.video_responses.get(
                scene, job.get("realVideos", {}).get(scene, {}).get("status", "NOT_REQUESTED")
            )}

        def assembly_projection(job):
            self.assembly_calls.append(True)
            return self.assembly_response

        scope = {
            "VIDEO_SCENES": ("scene1", "scene2", "scene3"),
            "video_scene_public": video_projection,
            "assembly_public": assembly_projection,
        }
        unit = ast.Module(body=[progress_fn], type_ignores=[])
        exec(compile(unit, filename=str(SOURCE), mode="exec"), scope)
        self.calculate = scope["overall_job_progress"]

    def check(self, job, stage, status, images, videos):
        snapshot = repr(job)
        result = self.calculate(job)
        self.assertEqual((result["currentStage"], result["stageStatus"]),
                         (stage, status))
        self.assertEqual(result["imagesCompleted"], images)
        self.assertEqual(result["videosCompleted"], videos)
        self.assertEqual(result["imagesTotal"], 3)
        self.assertEqual(result["videosTotal"], 3)
        self.assertEqual(repr(job), snapshot, "Status GET must never mutate the job")
        return result

    def test_created_job_awaiting_images(self):
        self.check(make_job(status="AWAITING_IMAGE_PIPELINE", images=("PENDING",)*3),
                   "IMAGES", "PENDING", 0, 0)

    def test_three_images_processing_in_parallel(self):
        self.check(make_job(status="GENERATING_IMAGES",
                            images=("GENERATING", "COMPLETED", "SUBMITTING")),
                   "IMAGES", "PROCESSING", 1, 0)

    def test_one_429_retry_among_parallel_images(self):
        self.check(make_job(status="GENERATING_IMAGES",
                            images=("RETRY_WAIT", "COMPLETED", "GENERATING")),
                   "IMAGES", "RETRYING", 1, 0)

    def test_partially_successful_image_workflow(self):
        self.check(make_job(status="PARTIAL_SUCCESS",
                            images=("FAILED", "COMPLETED", "COMPLETED")),
                   "IMAGES", "FAILED", 2, 0)

    def test_image_completion_waits_for_finalize(self):
        self.check(make_job(status="GENERATING_IMAGES"),
                   "IMAGES", "PROCESSING", 3, 0)

    def test_video_one_waits_for_browser_start(self):
        self.check(make_job(), "VIDEO_1", "WAITING_FOR_ACTION", 3, 0)
        self.assertEqual(self.video_calls, [], "Do not query workflow before approval")

    def test_video_one_requested(self):
        job = make_job(sequence={"status": "APPROVED"},
                       approvals={"scene1": {"status": "REQUESTED"}})
        self.video_responses["scene1"] = "REQUESTED"
        self.check(job, "VIDEO_1", "PROCESSING", 3, 0)

    def test_video_submission_needs_review(self):
        job = make_job(sequence={"status": "APPROVED"},
                       videos={"scene1": {"status": "SUBMISSION_UNKNOWN"}})
        self.video_responses["scene1"] = "REVIEW_REQUIRED"
        self.check(job, "VIDEO_1", "REVIEW_REQUIRED", 3, 0)

    def test_failed_video_does_not_advance(self):
        job = make_job(sequence={"status": "APPROVED"},
                       videos={"scene1": {"status": "FAILED"}})
        self.check(job, "VIDEO_1", "FAILED", 3, 0)

    def test_video_two_running(self):
        job = make_job(sequence={"status": "APPROVED"},
                       videos={"scene2": {"status": "PROCESSING"}})
        complete_video(job, 1)
        self.check(job, "VIDEO_2", "PROCESSING", 3, 1)
        self.assertEqual(self.video_calls, ["scene2"])

    def test_video_three_running(self):
        job = make_job(sequence={"status": "APPROVED"},
                       videos={"scene3": {"status": "PROCESSING"}})
        complete_video(job, 1)
        complete_video(job, 2)
        self.check(job, "VIDEO_3", "PROCESSING", 3, 2)

    def test_stitching_waits_for_browser(self):
        job = make_job()
        for n in (1, 2, 3):
            complete_video(job, n)
        self.assembly_response = {"status": "READY"}
        self.check(job, "STITCHING", "WAITING_FOR_ACTION", 3, 3)

    def test_stitching_in_progress(self):
        job = make_job(assembly={"status": "PROCESSING"})
        for n in (1, 2, 3):
            complete_video(job, n)
        self.assembly_response = {"status": "PROCESSING"}
        self.check(job, "STITCHING", "PROCESSING", 3, 3)

    def test_stitching_failed(self):
        job = make_job(assembly={"status": "FAILED"})
        for n in (1, 2, 3):
            complete_video(job, n)
        self.assembly_response = {"status": "FAILED"}
        self.check(job, "STITCHING", "FAILED", 3, 3)

    def test_final_video_ready(self):
        job = make_job(assembly={"status": "COMPLETED"})
        for n in (1, 2, 3):
            complete_video(job, n)
        self.assembly_response = {"status": "COMPLETED", "url": "https://example.invalid/signed"}
        result = self.check(job, "COMPLETED", "COMPLETED", 3, 3)
        self.assertTrue(result["finalVideoReady"])

    def test_completed_videos_must_have_correct_result_keys(self):
        job = make_job(sequence={"status": "APPROVED"})
        job["realVideos"]["scene1"] = {
            "status": "COMPLETED", "resultKey": "videos/wrong/scene1.mp4"
        }
        self.video_responses["scene1"] = "REVIEW_REQUIRED"
        self.check(job, "VIDEO_1", "REVIEW_REQUIRED", 3, 0)


if __name__ == "__main__":
    unittest.main()
