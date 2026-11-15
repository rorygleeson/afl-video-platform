"""Isolated, conservative reconciliation for missed AWS completion notifications.

Never calls Google, writes DynamoDB, or retries a paid submission. Replays
only VERIFIED SUCCEEDED Step Functions events to the *existing* API handler,
which independently validates S3, consent and atomic reservations.
Pre-deployment jobs are excluded to avoid unexpectedly starting old jobs.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
JOBS = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
STATES = boto3.client("stepfunctions")
API = boto3.client("lambda")
IMAGE_ARN = os.environ["IMAGE_STATE_MACHINE_ARN"]
VIDEO_ARN = os.environ["GOOGLE_STATE_MACHINE_ARN"]
API_ARN = os.environ["API_LAMBDA_ARN"]
ACTIVATED = datetime.fromisoformat(os.environ["WATCHDOG_ACTIVATED_AT"].replace("Z", "+00:00"))
JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
SCENES = ("scene1", "scene2", "scene3")
MIN_AGE_SECONDS = 120
WATCHDOG_MACHINE_ARN = os.environ["WATCHDOG_MACHINE_ARN"]
MAX_LIFETIME_SECONDS = 7200


def when(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def arn_for(machine, name):
    return machine.replace(":stateMachine:", ":execution:", 1) + ":" + name


def execution_succeeded(machine, name, now):
    try:
        value = STATES.describe_execution(executionArn=arn_for(machine, name))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ExecutionDoesNotExist":
            return False
        raise
    stopped = value.get("stopDate")
    return (value.get("status") == "SUCCEEDED"
            and isinstance(stopped, datetime) and stopped.tzinfo is not None
            and 0 <= (now - stopped).total_seconds()
            and (now - stopped).total_seconds() >= MIN_AGE_SECONDS)


def replay(machine, name):
    event = {"source": "aws.states",
             "detail-type": "Step Functions Execution Status Change",
             "detail": {"status": "SUCCEEDED", "stateMachineArn": machine,
                        "executionArn": arn_for(machine, name)}}
    result = API.invoke(FunctionName=API_ARN, InvocationType="RequestResponse",
                        Payload=json.dumps(event).encode())
    if result.get("FunctionError"):
        raise RuntimeError("Existing API completion handler failed")
    answer = json.loads(result["Payload"].read())
    if not isinstance(answer, dict):
        raise RuntimeError("Unexpected completion handler reply")
    return answer.get("status", "UNKNOWN")


def request_assembly_reconciliation(job_id):
    """Ask existing API to inspect/reconcile the one named FFmpeg execution.

    This is assembly-only. Watchdog cannot submit Google requests or
    change DynamoDB state directly.
    """
    payload = {"op": "reconcile_assembly", "jobId": job_id}
    result = API.invoke(FunctionName=API_ARN, InvocationType="RequestResponse",
                        Payload=json.dumps(payload).encode())
    if result.get("FunctionError"):
        raise RuntimeError("Assembly reconciliation API failed")
    answer = json.loads(result["Payload"].read())
    if not isinstance(answer, dict):
        raise RuntimeError("Invalid assembly reconciliation reply")
    if answer.get("statusCode") in (200, 202):
        return "ASSEMBLY_RECONCILED"
    return "REVIEW_ASSEMBLY_RECONCILIATION"


def reconcile(job, now):
    job_id = job.get("jobId")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        return "SKIP_INVALID_ID"
    created = when(job.get("createdAt"))
    if not created or created < ACTIVATED:
        return "SKIP_OLD_JOB"
    if job.get("status") != "COMPLETED" or job.get("autoVideoConsent") is not True:
        return "SKIP_UNAUTHORISED"
    if any((job.get("scenes") or {}).get(s, {}).get("status") != "COMPLETED"
           or not (job.get("scenes") or {}).get(s, {}).get("resultKey")
           for s in SCENES):
        return "SKIP_IMAGES_NOT_COMPLETE"
    if job.get("assembly"):
        # Never retry an already reserved assembly execution.
        return "REVIEW_OR_COMPLETE_ASSEMBLY_RESERVED"

    sequence = job.get("videoSequence") or {}
    approved = job.get("paidVideoApprovals") or {}
    videos = job.get("realVideos") or {}
    if not sequence and not approved and not videos:
        if execution_succeeded(IMAGE_ARN, job_id, now):
            return replay(IMAGE_ARN, job_id)
        return "WAIT_IMAGE_EXECUTION"

    if sequence.get("status") != "APPROVED":
        return "REVIEW_SEQUENCE_NOT_APPROVED"

    for index, scene in enumerate(SCENES):
        record = videos.get(scene) or {}
        approval = approved.get(scene) or {}
        if record.get("status") == "COMPLETED" and record.get("resultKey") == (
                f"videos/{job_id}/{scene}.mp4"):
            name = approval.get("executionName")
            if not isinstance(name, str) or not re.fullmatch(
                    rf"real-{job_id}-{scene}(?:-r(?:[2-9]|10))?", name):
                return "REVIEW_EXECUTION_OWNER"
            if record.get("requestId") != name:
                return "REVIEW_REQUEST_OWNER"
            completed = when(record.get("completedAt"))
            if not completed or (now - completed).total_seconds() < MIN_AGE_SECONDS:
                return "WAIT_VIDEO_SETTLEMENT"
            if not execution_succeeded(VIDEO_ARN, name, now):
                return "WAIT_PREVIOUS_EXECUTION"
            # Do not replay an event until after verifying that the NEXT stage
            # has no reservation, attempt, or assembly record.
            if index == 2:
                return replay(VIDEO_ARN, name)
            following = SCENES[index + 1]
            if approved.get(following) or videos.get(following):
                # Continue inspection of the next stage; it might already
                # have completed and be missing its own successor handoff.
                continue
            return replay(VIDEO_ARN, name)
        # The next video may already be paid, in flight, failed or ambiguous.
        # No replay in any of those states, including stale reservations.
        if approval or record:
            return "SKIP_EXISTING_PAID_ATTEMPT"
        return "REVIEW_GAP_WITHOUT_VERIFIED_PREDECESSOR"
    return "SKIP_ALREADY_COMPLETE"


def start_for_image_execution(event):
    """Start one per-job watchdog when the image Step Functions execution starts."""
    detail = event.get("detail") or {}
    if (event.get("source") != "aws.states"
            or event.get("detail-type") != "Step Functions Execution Status Change"
            or detail.get("status") != "RUNNING"
            or detail.get("stateMachineArn") != IMAGE_ARN):
        return {"status": "IGNORED_EVENT"}
    execution = detail.get("executionArn", "")
    prefix = IMAGE_ARN.replace(":stateMachine:", ":execution:", 1) + ":"
    if not isinstance(execution, str) or not execution.startswith(prefix):
        return {"status": "IGNORED_EXECUTION"}
    job_id = execution[len(prefix):]
    if not JOB_ID.fullmatch(job_id):
        return {"status": "IGNORED_JOB_ID"}
    job = JOBS.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
    if not job or job.get("autoVideoConsent") is not True:
        return {"status": "IGNORED_UNAUTHORISED"}
    created = when(job.get("createdAt"))
    if not created or created < ACTIVATED:
        return {"status": "IGNORED_OLD_JOB"}
    try:
        STATES.start_execution(
            stateMachineArn=WATCHDOG_MACHINE_ARN,
            name="watch-" + job_id,
            input=json.dumps({"jobId": job_id,
                              "startedAt": datetime.now(timezone.utc).isoformat()},
                             separators=(",", ":")))
    except STATES.exceptions.ExecutionAlreadyExists:
        return {"status": "ALREADY_MONITORING"}
    return {"status": "MONITORING_STARTED"}


def check_one(event):
    """Read exactly one job. Never scan or directly start a paid workflow."""
    job_id = event.get("jobId")
    started = when(event.get("startedAt"))
    now = datetime.now(timezone.utc)
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or not started:
        return {"continue": False, "status": "INVALID_WATCHDOG_INPUT"}
    if (now - started).total_seconds() > MAX_LIFETIME_SECONDS:
        LOG.warning("Watchdog deadline reached; operator review required")
        return {"continue": False, "status": "REVIEW_WATCHDOG_DEADLINE"}
    job = JOBS.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
    if not job:
        return {"continue": False, "status": "JOB_NOT_FOUND"}
    created = when(job.get("createdAt"))
    if not created or created < ACTIVATED or job.get("autoVideoConsent") is not True:
        return {"continue": False, "status": "NOT_AUTHORISED"}
    if job.get("status") in ("FAILED", "PARTIAL_SUCCESS"):
        return {"continue": False, "status": "IMAGE_JOB_TERMINAL_FAILURE"}
    assembly = job.get("assembly") or {}
    if assembly.get("status") == "COMPLETED":
        return {"continue": False, "status": "FINAL_VIDEO_COMPLETED"}
    if assembly.get("status") == "FAILED":
        return {"continue": False, "status": "REVIEW_ASSEMBLY_FAILED"}
    if assembly:
        if assembly.get("status") == "QUEUED":
            queued_at = when(assembly.get("createdAt"))
            if not queued_at or (now - queued_at).total_seconds() < MIN_AGE_SECONDS:
                return {"continue": True, "status": "WAIT_ASSEMBLY_SETTLEMENT"}
            outcome = request_assembly_reconciliation(job_id)
            # Keep watching until a confirmed finished job or deadline.
            return {"continue": True, "status": outcome}
        # Do not launch a second worker for an in-flight/uncertain assembly.
        return {"continue": True, "status": "ASSEMBLY_IN_PROGRESS_OR_REVIEW"}
    for record in (job.get("realVideos") or {}).values():
        if record.get("status") == "FAILED":
            return {"continue": False, "status": "REVIEW_VIDEO_FAILED"}
        if record.get("status") == "SUBMISSION_UNKNOWN":
            # Worker records this BEFORE its paid POST, so it may be a normal
            # transient in-flight state. Keep monitoring but never resubmit.
            return {"continue": True, "status": "WAIT_UNKNOWN_PAID_SUBMISSION"}
    outcome = reconcile(job, now)
    if outcome.startswith("REVIEW"):
        return {"continue": False, "status": outcome}
    LOG.info("Job watchdog outcome=%s", outcome)
    return {"continue": True, "status": outcome}


def lambda_handler(event, context):
    if event.get("source") == "aws.states":
        return start_for_image_execution(event)
    if event.get("op") == "check" and isinstance(event.get("input"), dict):
        return check_one(event["input"])
    return {"continue": False, "status": "IGNORED_INVALID_REQUEST"}
