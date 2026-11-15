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
MAX_PAGES = 10
SCAN_LIMIT = 100
MAX_HANDOFFS = 10


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
                return "SKIP_NEXT_ALREADY_RESERVED"
            return replay(VIDEO_ARN, name)
        # The next video may already be paid, in flight, failed or ambiguous.
        # No replay in any of those states, including stale reservations.
        if approval or record:
            return "SKIP_EXISTING_PAID_ATTEMPT"
        return "REVIEW_GAP_WITHOUT_VERIFIED_PREDECESSOR"
    return "SKIP_ALREADY_COMPLETE"


def lambda_handler(event, context):
    now = datetime.now(timezone.utc)
    counters = {"checked": 0, "replayed": 0, "review": 0, "errors": 0}
    exclusive = None
    for _ in range(MAX_PAGES):
        request = {"Limit": SCAN_LIMIT,
                   "FilterExpression": __import__("boto3").dynamodb.conditions.Attr("status").eq("COMPLETED")}
        if exclusive:
            request["ExclusiveStartKey"] = exclusive
        page = JOBS.scan(**request)
        for job in page.get("Items", []):
            counters["checked"] += 1
            try:
                outcome = reconcile(job, now)
                if outcome in ("SEQUENCE_APPROVED", "ALREADY_APPROVED",
                               "NEXT_SCENE_REQUESTED", "ASSEMBLY_REQUESTED"):
                    counters["replayed"] += 1
                elif outcome.startswith("REVIEW"):
                    counters["review"] += 1
                LOG.info("Reconciliation outcome=%s", outcome)
            except Exception:
                counters["errors"] += 1
                LOG.exception("Reconciliation requires operator investigation")
            if counters["replayed"] >= MAX_HANDOFFS:
                return counters
        exclusive = page.get("LastEvaluatedKey")
        if not exclusive:
            break
    return counters
