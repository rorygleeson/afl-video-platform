"""Offline tests: isolated watchdog never retries a paid request or acts on old jobs."""
import importlib.util
import io
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1] / "src/handoff_watchdog/handler.py"
MACHINE = "arn:aws:states:ap-southeast-2:123456789012:stateMachine:"
NOW = datetime.now(timezone.utc)
ID = "a"*32

class WatchdogTests(unittest.TestCase):
    def setUp(self):
        import boto3
        self.ddb, self.states, self.api = MagicMock(), MagicMock(), MagicMock()

        self.states.describe_execution.return_value = {
            "status": "SUCCEEDED", "stopDate": NOW-timedelta(minutes=3)}
        self.api.invoke.return_value = {"Payload": io.BytesIO(b'{"status":"NEXT_SCENE_REQUESTED"}')}
        def resource(k, **kw):
            o=MagicMock(); o.Table.return_value=self.ddb; return o
        def client(k, **kw):
            return {"stepfunctions":self.states,"lambda":self.api}[k]
        env={"JOBS_TABLE":"jobs", "AWS_REGION":"ap-southeast-2",
             "IMAGE_STATE_MACHINE_ARN":MACHINE+"afl-app-dev-scene1-pilot",
             "GOOGLE_STATE_MACHINE_ARN":MACHINE+"afl-app-dev-google-omni",
             "API_LAMBDA_ARN":"arn:aws:lambda:ap-southeast-2:123456789012:function:afl-app-dev-api",
             "WATCHDOG_ACTIVATED_AT":(NOW-timedelta(days=1)).isoformat(),
             "WATCHDOG_MACHINE_ARN":MACHINE+"afl-app-dev-handoff-watchdog"}
        with patch.dict(os.environ,env),patch.object(boto3,"client",side_effect=client),patch.object(boto3,"resource",side_effect=resource):
            spec=importlib.util.spec_from_file_location("isolated_watchdog",ROOT)
            self.w=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.w)
        self.job={"jobId":ID,"createdAt":NOW.isoformat(),"status":"COMPLETED",
                  "autoVideoConsent":True,
                  "scenes":{s:{"status":"COMPLETED","resultKey":f"results/{ID}/{s}.png"} for s in ("scene1","scene2","scene3")}}
    def complete(self,scene):
        name=f"real-{ID}-{scene}"
        self.job.setdefault("paidVideoApprovals",{})[scene]={"executionName":name}
        self.job.setdefault("realVideos",{})[scene]={
            "status":"COMPLETED","requestId":name,
            "resultKey":f"videos/{ID}/{scene}.mp4",
            "completedAt":(NOW-timedelta(minutes=3)).isoformat()}
    def test_new_job_images_success_replays_existing_api_only(self):
        self.assertEqual(self.w.reconcile(self.job,NOW),"NEXT_SCENE_REQUESTED")
        self.assertEqual(self.api.invoke.call_count,1)
        self.assertIn(b"afl-app-dev-scene1-pilot",self.api.invoke.call_args.kwargs["Payload"])
        self.ddb.update_item.assert_not_called()
    def test_old_job_never_resubmitted(self):
        self.job["createdAt"]=(NOW-timedelta(days=5)).isoformat()
        self.assertEqual(self.w.reconcile(self.job,NOW),"SKIP_OLD_JOB")
        self.api.invoke.assert_not_called()
    def test_waits_for_two_minutes(self):
        self.states.describe_execution.return_value["stopDate"]=NOW-timedelta(seconds=80)
        self.assertEqual(self.w.reconcile(self.job,NOW),"WAIT_IMAGE_EXECUTION")
        self.api.invoke.assert_not_called()
    def test_skips_when_paid_reservation_exists(self):
        self.job["videoSequence"]={"status":"APPROVED"}
        self.job["paidVideoApprovals"]={"scene1":{"executionName":f"real-{ID}-scene1"}}
        self.assertEqual(self.w.reconcile(self.job,NOW),"SKIP_EXISTING_PAID_ATTEMPT")
        self.api.invoke.assert_not_called()
    def test_scene1_success_replays_to_advance_scene2(self):
        self.job["videoSequence"]={"status":"APPROVED"}
        self.complete("scene1")
        self.assertEqual(self.w.reconcile(self.job,NOW),"NEXT_SCENE_REQUESTED")
        self.assertIn(b"real-",self.api.invoke.call_args.kwargs["Payload"])
    def test_scene2_success_replays_to_advance_scene3(self):
        self.job["videoSequence"]={"status":"APPROVED"}
        self.complete("scene1");self.complete("scene2")
        # Already exists earlier? the watchdog returns before considering scene2,
        # so ensure it continues past an already reserved subsequent scene.
        self.assertEqual(self.w.reconcile(self.job,NOW),"NEXT_SCENE_REQUESTED")
    def test_scene3_success_replays_assembly_only(self):
        self.job["videoSequence"]={"status":"APPROVED"}
        for s in ("scene1","scene2","scene3"):self.complete(s)
        self.assertEqual(self.w.reconcile(self.job,NOW),"NEXT_SCENE_REQUESTED")
        self.assertIn(b"scene3",self.api.invoke.call_args.kwargs["Payload"])
    def test_assembly_reserved_never_replayed(self):
        self.job["assembly"]={"status":"QUEUED"}
        self.assertEqual(self.w.reconcile(self.job,NOW),"REVIEW_OR_COMPLETE_ASSEMBLY_RESERVED")
        self.api.invoke.assert_not_called()
    def test_google_unknown_or_failed_does_not_submit(self):
        self.job["videoSequence"]={"status":"APPROVED"}
        self.job["realVideos"]={"scene1":{"status":"SUBMISSION_UNKNOWN"}}
        self.assertEqual(self.w.reconcile(self.job,NOW),"SKIP_EXISTING_PAID_ATTEMPT")
        self.api.invoke.assert_not_called()
    def test_no_consent_no_action(self):
        self.job["autoVideoConsent"]=False
        self.assertEqual(self.w.reconcile(self.job,NOW),"SKIP_UNAUTHORISED")
        self.api.invoke.assert_not_called()
    def test_invalid_or_missing_execution_not_replayed(self):
        from botocore.exceptions import ClientError
        self.states.describe_execution.side_effect=ClientError(
            {"Error":{"Code":"ExecutionDoesNotExist","Message":"not found"}},"DescribeExecution")
        self.assertEqual(self.w.reconcile(self.job,NOW),"WAIT_IMAGE_EXECUTION")
        self.api.invoke.assert_not_called()
    def start_event(self):
        machine=self.w.IMAGE_ARN
        return {"source":"aws.states",
                "detail-type":"Step Functions Execution Status Change",
                "detail":{"stateMachineArn":machine,"status":"RUNNING",
                          "executionArn":self.w.arn_for(machine,ID)}}

    def test_image_start_event_creates_one_named_watchdog(self):
        self.ddb.get_item.return_value={"Item":self.job}
        status=self.w.lambda_handler(self.start_event(),None)
        self.assertEqual(status["status"],"MONITORING_STARTED")
        self.states.start_execution.assert_called_once()
        params=self.states.start_execution.call_args.kwargs
        self.assertEqual(params["name"],"watch-"+ID)
        self.assertEqual(params["stateMachineArn"],self.w.WATCHDOG_MACHINE_ARN)
        self.ddb.scan.assert_not_called()

    def test_duplicate_image_start_is_idempotent(self):
        self.ddb.get_item.return_value={"Item":self.job}
        self.states.exceptions.ExecutionAlreadyExists=type("ExecutionAlreadyExists",(Exception,),{})
        self.states.start_execution.side_effect=self.states.exceptions.ExecutionAlreadyExists()
        self.assertEqual(self.w.lambda_handler(self.start_event(),None)["status"],"ALREADY_MONITORING")

    def test_old_job_and_mismatched_start_event_ignored(self):
        self.ddb.get_item.return_value={"Item":self.job}
        self.job["createdAt"]=(NOW-timedelta(days=3)).isoformat()
        self.assertEqual(self.w.lambda_handler(self.start_event(),None)["status"],"IGNORED_OLD_JOB")
        self.states.start_execution.assert_not_called()
        event=self.start_event()
        event["detail"]["status"]="SUCCEEDED"
        self.assertEqual(self.w.lambda_handler(event,None)["status"],"IGNORED_EVENT")
        self.states.start_execution.assert_not_called()

    def test_job_check_single_get_no_scan_and_finished_stops(self):
        self.ddb.get_item.return_value={"Item":self.job}
        self.job["assembly"]={"status":"COMPLETED"}
        result=self.w.lambda_handler({"op":"check","input":{
            "jobId":ID,"startedAt":NOW.isoformat()}},None)
        self.assertEqual(result,{"continue":False,"status":"FINAL_VIDEO_COMPLETED"})
        self.ddb.get_item.assert_called_once()
        self.ddb.scan.assert_not_called()
        self.states.start_execution.assert_not_called()

    def test_active_job_check_continues(self):
        self.ddb.get_item.return_value={"Item":self.job}
        self.job["status"]="GENERATING_IMAGES"
        result=self.w.check_one({"jobId":ID,"startedAt":NOW.isoformat()})
        self.assertTrue(result["continue"])
        self.assertEqual(result["status"],"SKIP_UNAUTHORISED")
        self.ddb.scan.assert_not_called()

    def test_ambiguous_video_check_stops_without_retry(self):
        self.ddb.get_item.return_value={"Item":self.job}
        self.job["realVideos"]={"scene1":{"status":"SUBMISSION_UNKNOWN"}}
        result=self.w.check_one({"jobId":ID,"startedAt":NOW.isoformat()})
        self.assertEqual(result,{"continue":False,"status":"REVIEW_VIDEO_FAILURE_OR_UNKNOWN"})
        self.api.invoke.assert_not_called()

    def test_expired_job_watchdog_stops_without_api_call(self):
        result=self.w.check_one({"jobId":ID,"startedAt":(NOW-timedelta(hours=3)).isoformat()})
        self.assertEqual(result["continue"],False)
        self.ddb.get_item.assert_not_called()
        self.api.invoke.assert_not_called()

if __name__=="__main__":
    unittest.main()
