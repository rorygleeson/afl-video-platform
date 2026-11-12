"""AFL API with explicitly started three-scene image generation."""
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from video_prompts import validate_name, render_scene

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
FRONTEND_ORIGIN = os.environ["FRONTEND_ORIGIN"]
IMAGE_STATE_MACHINE_ARN = os.environ["IMAGE_STATE_MACHINE_ARN"]
VIDEO_WORKER_ARN = os.environ.get("VIDEO_WORKER_ARN")
GOOGLE_WORKER_ARN = os.environ.get("GOOGLE_WORKER_ARN")
GOOGLE_STATE_MACHINE_ARN = os.environ.get("GOOGLE_STATE_MACHINE_ARN")
REAL_VIDEO_MODE = os.environ.get("REAL_VIDEO_MODE", "DISABLED")
ASSEMBLY_STATE_MACHINE_ARN = os.environ.get("ASSEMBLY_STATE_MACHINE_ARN", "")
SCENARIO_ASSET_BUCKET = os.environ.get("SCENARIO_ASSET_BUCKET")
VIDEO_STATE_MACHINE_ARN = os.environ.get("VIDEO_STATE_MACHINE_ARN")
VIDEO_MODE = os.environ.get("VIDEO_MODE", "DISABLED")
VIDEO_ADMISSION_TABLE = os.environ.get("VIDEO_ADMISSION_TABLE")
ADMISSION = (boto3.resource("dynamodb").Table(VIDEO_ADMISSION_TABLE)
             if VIDEO_ADMISSION_TABLE else None)
VIDEO_SCENES = ("scene1", "scene2", "scene3")
MAX_VIDEO_ATTEMPTS = 10

# Presigned browser POSTs must target the bucket's regional endpoint and use
# Signature Version 4. The default client can generate a global endpoint with
# legacy signature fields, triggering cross-origin redirects from browsers.
S3 = boto3.client(
    "s3",
    region_name=os.environ["AWS_REGION"],
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
DDB = boto3.resource("dynamodb").Table(JOBS_TABLE)
STATES = boto3.client("stepfunctions")
LAMBDA = boto3.client("lambda")

MAX_UPLOAD = 10 * 1024 * 1024
MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
SCENARIO_ID = "afl-grand-final-winning-kick"
TEAMS = {
    "adelaide", "brisbane", "carlton", "collingwood", "essendon",
    "fremantle", "geelong", "gold-coast", "gws", "hawthorn",
    "melbourne", "north-melbourne", "port-adelaide", "richmond",
    "st-kilda", "sydney", "west-coast", "western-bulldogs",
}
UPLOAD_KEY_RE = re.compile(r"^uploads/[0-9a-f]{32}\.(jpg|png|webp)$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_-]{12,128}$")
JOB_RE = re.compile(r"^[0-9a-f]{32}$")


def response(status, payload):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": FRONTEND_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,x-api-key,Idempotency-Key",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(payload, separators=(",", ":")),
    }


def body_json(event):
    body = event.get("body") or "{}"
    if len(body) > 32768 or event.get("isBase64Encoded"):
        raise ValueError("Invalid request body")
    try:
        data = json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return data


def presign_upload(event):
    data = body_json(event)
    content_type = data.get("contentType")
    size = data.get("sizeBytes")
    if content_type not in MIME_EXT:
        raise ValueError("Only JPEG, PNG and WebP photos are supported")
    if type(size) is not int or not (1 <= size <= MAX_UPLOAD):
        raise ValueError("Selfie must be between 1 byte and 10 MB")

    object_key = f"uploads/{uuid.uuid4().hex}.{MIME_EXT[content_type]}"
    form = S3.generate_presigned_post(
        Bucket=UPLOAD_BUCKET,
        Key=object_key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_UPLOAD],
        ],
        ExpiresIn=300,
    )
    return response(200, {
        "uploadKey": object_key,
        "upload": form,
        "expiresInSeconds": 300,
    })


def get_job(job_id):
    if not JOB_RE.fullmatch(job_id):
        return None
    result = DDB.get_item(Key={"jobId": job_id}, ConsistentRead=True)
    return result.get("Item")


def public_job(item):
    return {
        "jobId": item["jobId"],
        "scenarioId": item["scenarioId"],
        "team": item["team"],
        "firstName": item.get("firstName"),
        "lastName": item.get("lastName"),
        "status": item["status"],
        "createdAt": item["createdAt"],
        "imageCount": sum(1 for scene in item.get("scenes", {}).values() if scene.get("status") == "COMPLETED"),
        "sceneCount": 3,
        "completedScenes": sum(1 for scene in item.get("scenes", {}).values() if scene.get("status") == "COMPLETED"),
        "failedScenes": sum(1 for scene in item.get("scenes", {}).values() if scene.get("status") == "FAILED"),
        "scenes": {
            scene_id: {
                "status": item.get("scenes", {}).get(scene_id, {}).get("status", "PENDING"),
                **({"errorCode": item["scenes"][scene_id]["errorCode"]}
                   if item.get("scenes", {}).get(scene_id, {}).get("status") == "FAILED"
                   and item.get("scenes", {}).get(scene_id, {}).get("errorCode")
                   else {}),
                **({"retryCount": int(item["scenes"][scene_id].get("retryCount", 0)),
                    "maxRetries": int(item["scenes"][scene_id].get("maxRetries", 7)),
                    "nextRetryAt": int(item["scenes"][scene_id].get("nextRetryAt", 0))}
                   if item.get("scenes", {}).get(scene_id, {}).get("status")
                   in ("RETRY_WAIT", "SUBMITTING", "GENERATING")
                   and item.get("scenes", {}).get(scene_id, {}).get("retryCount")
                   else {}),
            }
            for scene_id in ("scene1", "scene2", "scene3")
        },
    }


def create_job(event):
    data = body_json(event)
    scenario_id = data.get("scenarioId")
    team = data.get("team")
    first_name = validate_name(data.get("firstName"), "First name")
    last_name = validate_name(data.get("lastName"), "Surname")
    upload_key = data.get("uploadKey")
    if scenario_id != SCENARIO_ID:
        raise ValueError("Unsupported scenario")
    if not isinstance(team, str) or team not in TEAMS:
        raise ValueError("Unsupported team")
    if not isinstance(upload_key, str) or not UPLOAD_KEY_RE.fullmatch(upload_key):
        raise ValueError("Invalid selfie upload key")

    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    idem = headers.get("idempotency-key", "")
    if not IDEMPOTENCY_RE.fullmatch(idem):
        raise ValueError("Idempotency-Key header is required (12-128 characters)")

    # Make retries of the same request safe without storing the client's API key.
    fingerprint = hashlib.sha256(
        json.dumps(
            {"scenarioId": scenario_id, "team": team, "uploadKey": upload_key,
             "firstName": first_name, "lastName": last_name},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()
    job_id = hashlib.sha256(
        ("afl-video-job-v1:" + idem).encode()
    ).hexdigest()[:32]

    # Recheck the file at job creation: a signed form alone doesn't prove upload.
    try:
        head = S3.head_object(Bucket=UPLOAD_BUCKET, Key=upload_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            raise ValueError("Selfie upload not found") from exc
        raise
    size = head.get("ContentLength", 0)
    if not (1 <= size <= MAX_UPLOAD):
        raise ValueError("Uploaded selfie exceeds the size limit")
    ext = upload_key.rsplit(".", 1)[-1]
    if MIME_EXT.get(head.get("ContentType")) != ext:
        raise ValueError("Uploaded selfie type doesn't match its extension")

    now = datetime.now(timezone.utc).isoformat()
    item = {
        "jobId": job_id,
        "scenarioId": scenario_id,
        "team": team,
        "firstName": first_name,
        "lastName": last_name,
        "uploadKey": upload_key,
        "requestFingerprint": fingerprint,
        "status": "AWAITING_IMAGE_PIPELINE",
        "createdAt": now,
        "expiresAt": int(time.time()) + 7 * 86400,
    }
    try:
        DDB.put_item(Item=item, ConditionExpression="attribute_not_exists(jobId)")
        return response(201, {"job": public_job(item)})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        existing = get_job(job_id)
        if existing and existing.get("requestFingerprint") == fingerprint:
            return response(200, {"job": public_job(existing)})
        return response(409, {"error": "Idempotency-Key has already been used for a different request"})


def start_images(job_id):
    """Explicitly start three parallel paid image edits, one execution per job."""
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    if job.get("status") != "AWAITING_IMAGE_PIPELINE":
        return response(200, {"jobId": job_id, "status": job["status"], "job": public_job(job), "message": "Job already started"})
    try:
        STATES.start_execution(
            stateMachineArn=IMAGE_STATE_MACHINE_ARN,
            name=job_id,
            input=json.dumps({"jobId": job_id}, separators=(",", ":")),
        )
    except STATES.exceptions.ExecutionAlreadyExists:
        return response(202, {"jobId": job_id, "status": "GENERATING_IMAGES",
                              "message": "Generation already requested"})
    return response(202, {"jobId": job_id, "status": "GENERATING_IMAGES",
                          "message": "Three parallel scene edits started; these may incur Replicate charges"})


def images_for_job(job):
    images = []
    for scene_id in ("scene1", "scene2", "scene3"):
        scene = job.get("scenes", {}).get(scene_id, {})
        key = scene.get("resultKey")
        if key and scene.get("status") == "COMPLETED":
            images.append({
                "sceneId": scene_id,
                "url": S3.generate_presigned_url(
                    "get_object", Params={"Bucket": UPLOAD_BUCKET, "Key": key},
                    ExpiresIn=300,
                ),
            })
    return response(200, {"jobId": job["jobId"], "status": job["status"],
                          "scenes": public_job(job)["scenes"], "images": images})


def safe_execution_failure_code(execution_arn):
    """Inspect existing SFN execution events, never start/retry an execution.

    Provider response bodies can contain sensitive information. Only return an
    allowlisted HTTP status code or a generic worker/orchestration category.
    """
    try:
        history = STATES.get_execution_history(
            executionArn=execution_arn, maxResults=30,
            reverseOrder=True, includeExecutionData=False)
        for event in history.get("events", []):
            detail = (event.get("taskFailedEventDetails") or
                      event.get("lambdaFunctionFailedEventDetails") or
                      event.get("executionFailedEventDetails") or {})
            if not detail:
                continue
            cause = str(detail.get("cause", ""))[:16000]
            http = re.search(r"Google (?:POST|GET) HTTP (400|401|403|404|409|429|500|502|503|504)", cause)
            if http:
                return "GOOGLE_HTTP_" + http.group(1)
            if "UNKNOWN_SUBMISSION_REQUIRES_MANUAL_RECONCILIATION" in cause:
                return "GOOGLE_SUBMISSION_UNCONFIRMED"
            if "PRE_SUBMISSION_VALIDATION_FAILED" in cause:
                return "PRE_SUBMISSION_VALIDATION_FAILED"
            if "SCENARIO_VIDEO_GENERATION_DISABLED" in cause:
                return "SCENARIO_VIDEO_DISABLED"
            if "Runtime.ImportModuleError" in cause:
                return "WORKER_IMPORT_FAILED"
            if "TaskTimedOut" in cause or "timed out" in cause.lower():
                return "WORKER_TIMEOUT"
            return "WORKER_FAILED"
    except ClientError as exc:
        LOG.warning("Could not inspect video failure history: %s",
                    exc.response.get("Error", {}).get("Code", "UNKNOWN")[:60])
    return "FAILURE_DETAILS_UNAVAILABLE"


def assembly_public(job):
    """Expose final assembly state, never mock clips as real."""
    job_id = job["jobId"]
    record = job.get("assembly", {})
    ready = all(
        job.get("realVideos", {}).get(scene, {}).get("status") == "COMPLETED"
        and job["realVideos"][scene].get("resultKey") == f"videos/{job_id}/{scene}.mp4"
        for scene in VIDEO_SCENES
    )
    status = record.get("status", "READY" if ready else "NOT_READY")
    result = {"status": status, "ready": ready, "enabled": bool(ASSEMBLY_STATE_MACHINE_ARN)}
    if status in ("QUEUED", "PROCESSING") and ASSEMBLY_STATE_MACHINE_ARN:
        arn = (ASSEMBLY_STATE_MACHINE_ARN.replace(":stateMachine:", ":execution:", 1)
               + ":final-" + job_id)
        try:
            execution = STATES.describe_execution(executionArn=arn)
            if execution["status"] in ("FAILED", "TIMED_OUT", "ABORTED"):
                result["status"] = "REVIEW_REQUIRED"
                result["errorCode"] = "ASSEMBLY_WORKFLOW_" + execution["status"]
        except ClientError:
            result["workflowStatus"] = "UNKNOWN"
    if status == "FAILED":
        result["errorCode"] = record.get("errorCode", "ASSEMBLY_FAILED")
    if status == "COMPLETED" and record.get("resultKey") == f"videos/{job_id}/final.mp4":
        key = record["resultKey"]
        result["url"] = S3.generate_presigned_url(
            "get_object", Params={"Bucket": UPLOAD_BUCKET, "Key": key}, ExpiresIn=300)
        result["downloadUrl"] = S3.generate_presigned_url(
            "get_object", Params={"Bucket": UPLOAD_BUCKET, "Key": key,
                                  "ResponseContentDisposition": 'attachment; filename="afl-grand-final.mp4"',
                                  "ResponseContentType": "video/mp4"}, ExpiresIn=300)
    return result


def start_assembly(job_id):
    """Reserve one assembly, verify all three actual files, then start workflow."""
    if not ASSEMBLY_STATE_MACHINE_ARN:
        return response(503, {"error": "Assembly is not deployed"})
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    if job.get("assembly"):
        return response(200, {"jobId": job_id, "assembly": assembly_public(job)})
    for scene in VIDEO_SCENES:
        video = job.get("realVideos", {}).get(scene, {})
        key = f"videos/{job_id}/{scene}.mp4"
        if video.get("status") != "COMPLETED" or video.get("resultKey") != key:
            return response(409, {"error": "All three real scene MP4s must be completed"})
        try:
            head = S3.head_object(Bucket=UPLOAD_BUCKET, Key=key)
            if head.get("ContentLength", 0) < 128:
                return response(409, {"error": "A scene MP4 is empty"})
        except ClientError:
            return response(409, {"error": "A scene MP4 is not available in S3"})
    try:
        DDB.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET assembly = :record",
            ConditionExpression="attribute_exists(jobId) AND attribute_not_exists(assembly)",
            ExpressionAttributeValues={":record": {
                "status": "QUEUED", "createdAt": datetime.now(timezone.utc).isoformat()}})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return response(202, {"status": "ALREADY_REQUESTED", "jobId": job_id})
        raise
    try:
        STATES.start_execution(
            stateMachineArn=ASSEMBLY_STATE_MACHINE_ARN,
            name="final-" + job_id,
            input=json.dumps({"jobId": job_id}, separators=(",", ":")))
    except STATES.exceptions.ExecutionAlreadyExists:
        return response(202, {"status": "ALREADY_REQUESTED", "jobId": job_id})
    except Exception:
        LOG.exception("Assembly handoff uncertain")
        return response(503, {"status": "HANDOFF_UNKNOWN", "error": "Check status; do not retry"})
    return response(202, {"status": "QUEUED", "jobId": job_id})


def video_scene_public(job, scene_id):
    """Mock states never pretend to contain playable MP4s."""
    real = job.get("realVideos", {}).get(scene_id, {})
    approval = job.get("paidVideoApprovals", {}).get(scene_id, {})
    item = real or ({"status": "REQUESTED"} if approval else job.get("videos", {}).get(scene_id, {}))
    status = item.get("status", "NOT_REQUESTED")
    result = {"sceneId": scene_id, "status": status,
              "mode": "REAL" if (real or approval) else ("MOCK_ONLY" if VIDEO_MODE == "MOCK_ONLY" else "DISABLED")}
    # Only confirmed terminal provider failures are eligible for a new paid
    # attempt. Never offer retry for uncertain, processing or completed videos.
    attempt_number = int(approval.get("attemptNumber", 1))
    result["attemptNumber"] = attempt_number
    result["maxAttempts"] = MAX_VIDEO_ATTEMPTS
    result["retryAvailable"] = bool(
        REAL_VIDEO_MODE == "ENABLED" and status == "FAILED"
        and real.get("providerFinalStatus") == "failed"
        and attempt_number < MAX_VIDEO_ATTEMPTS and not job.get("assembly")
    )
    if status in ("MOCK_CLAIMED", "MOCK_GENERATING", "MOCK_COMPLETED", "MOCK_FAILED"):
        result["mock"] = True
    if item.get("errorCode") and status in ("FAILED", "MOCK_FAILED", "SUBMISSION_UNKNOWN"):
        result["errorCode"] = item["errorCode"]
    # Operator diagnostics: stored metadata only, never raw Google payloads.
    if real:
        for key in ("interactionId", "requestId", "submissionStartedAt",
                    "submittedAt", "providerFailureObservedAt", "providerFinalStatus",
                    "providerErrorCode", "providerErrorCategory", "completedAt"):
            value = real.get(key)
            if isinstance(value, str) and len(value) <= 256:
                result[key] = value
    if (real or approval) and status not in ("COMPLETED", "FAILED") and GOOGLE_STATE_MACHINE_ARN:
        # Read-only reconciliation of Step Functions failures. Never issue a new
        # paid request or infer that a failed workflow was not charged.
        execution_name = (approval.get("executionName") or
                          "real-" + job["jobId"] + "-" + scene_id)
        arn = (GOOGLE_STATE_MACHINE_ARN.replace(":stateMachine:", ":execution:", 1)
               + ":" + execution_name)
        try:
            execution = STATES.describe_execution(executionArn=arn)
            if execution.get("status") in ("FAILED", "TIMED_OUT", "ABORTED"):
                result["status"] = "REVIEW_REQUIRED"
                result["errorCode"] = "WORKFLOW_" + execution["status"]
                result["failureCode"] = safe_execution_failure_code(arn)
                if real.get("errorCode"):
                    result["failureCode"] = real["errorCode"]
            elif execution.get("status") == "RUNNING":
                result["workflowStatus"] = "RUNNING"
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "ExecutionDoesNotExist":
                LOG.warning("Could not read real scene workflow status: %s", str(code)[:60])
                result["workflowStatus"] = "UNKNOWN"

    # Future real-provider stage may record MP4 metadata, but NEVER expose mock JSON as video.
    if status == "COMPLETED" and item.get("resultKey"):
        key = item["resultKey"]
        if key == f"videos/{job['jobId']}/{scene_id}.mp4":
            result["url"] = S3.generate_presigned_url(
                "get_object", Params={"Bucket": UPLOAD_BUCKET, "Key": key}, ExpiresIn=300)
            # Separate attachment link: a fresh status GET signs each download,
            # without restarting generation or affecting an existing video player.
            result["downloadUrl"] = S3.generate_presigned_url(
                "get_object", Params={
                    "Bucket": UPLOAD_BUCKET, "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="afl-{scene_id}.mp4"',
                    "ResponseContentType": "video/mp4",
                }, ExpiresIn=300)
    return result


def video_status(job_id, scene_id=None):
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    if scene_id:
        return response(200, {"jobId": job_id, "video": video_scene_public(job, scene_id)})
    return response(200, {"jobId": job_id, "videoGenerationMode": VIDEO_MODE,
                          "realVideoMode": REAL_VIDEO_MODE,
                          "scene1PaidApproved": REAL_VIDEO_MODE == "ENABLED",
                          "paidScenes": {scene: REAL_VIDEO_MODE == "ENABLED" and not job.get("paidVideoApprovals", {}).get(scene) and not job.get("realVideos", {}).get(scene) for scene in VIDEO_SCENES},
                          "videos": {scene: video_scene_public(job, scene) for scene in VIDEO_SCENES},
                          "assembly": assembly_public(job)})


def start_mock_video(job_id, scene_id, event):
    """Stage 4 is strictly mock-only. Named execution enforces once-per-scene."""
    if VIDEO_MODE != "MOCK_ONLY" or not VIDEO_STATE_MACHINE_ARN:
        return response(503, {"error": "Video mock integration is disabled"})
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    token = headers.get("idempotency-key", "")
    if not IDEMPOTENCY_RE.fullmatch(token):
        return response(400, {"error": "Idempotency-Key required (12-128 characters)"})
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    if any(job.get("scenes", {}).get(s, {}).get("status") != "COMPLETED"
           or not job.get("scenes", {}).get(s, {}).get("resultKey")
           for s in VIDEO_SCENES):
        return response(409, {"error": "Complete all three reference images before video testing"})
    scene = job.get("scenes", {}).get(scene_id, {})
    key = scene.get("resultKey")
    if scene.get("status") != "COMPLETED" or not isinstance(key, str) or not key.startswith(
            f"results/{job_id}/{scene_id}."):
        return response(409, {"error": "Complete the matching reference image first"})
    try:
        S3.head_object(Bucket=UPLOAD_BUCKET, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return response(409, {"error": "Reference image has expired or is missing"})
        raise
    current = job.get("videos", {}).get(scene_id)
    if current:
        return response(200, {"jobId": job_id, "video": video_scene_public(job, scene_id),
                              "message": "Scene already requested; no new execution created"})
    if ADMISSION and ADMISSION.get_item(Key={"slotId": "GLOBAL_VIDEO_SLOT_0"}, ConsistentRead=True).get("Item"):
        return response(409, {"error": "Another mock scene is running. Try after it finishes."})
    # Stable per idempotency key, but a new explicit click can retry when an
    # earlier mock execution lost admission before creating a scene record.
    # The DynamoDB worker slot remains the hard guard against parallel work.
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    execution_name = f"v-{job_id}-{scene_id}-{token_hash}"
    try:
        STATES.start_execution(
            stateMachineArn=VIDEO_STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps({"jobId": job_id, "sceneId": scene_id}, separators=(",", ":")),
        )
    except STATES.exceptions.ExecutionAlreadyExists:
        return response(202, {"jobId": job_id, "sceneId": scene_id,
                              "status": "ALREADY_REQUESTED",
                              "message": "Same mock execution already submitted"})
    return response(202, {"jobId": job_id, "sceneId": scene_id,
                          "status": "MOCK_REQUESTED",
                          "message": "Mock execution started. No paid generation."})


def preview_video_prompt(job_id, scene_id):
    """No paid video API call. Resolve exact versioned prompt from private S3."""
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    if not SCENARIO_ASSET_BUCKET:
        return response(503, {"error": "Prompt assets not configured"})
    result = render_scene(S3, SCENARIO_ASSET_BUCKET, job, scene_id)
    return response(200, result)



def failed_video_diagnostic(job_id, scene_id):
    """Read-only Google interaction lookup. No Step Functions or paid POST."""
    if not GOOGLE_WORKER_ARN:
        return response(503, {"error": "Google diagnostic worker unavailable"})
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    record = job.get("realVideos", {}).get(scene_id, {})
    if (record.get("status") not in ("FAILED", "REVIEW_REQUIRED") or
            not job.get("paidVideoApprovals", {}).get(scene_id)):
        return response(409, {"error": "No failed paid attempt to inspect"})
    # Fallback survives expired or unavailable Google interactions. These are
    # allowlisted fields written by our own worker, never raw provider text.
    stored = {
        "status": "UNAVAILABLE",
        "source": "DYNAMODB",
        "recordedStatus": record.get("status", "UNKNOWN"),
        "recordedErrorCode": record.get("errorCode", "NOT_RECORDED"),
        "errorCategory": record.get("providerErrorCategory", "UNSPECIFIED"),
        "reason": "GOOGLE_LOOKUP_UNAVAILABLE",
        "paidGenerationStarted": False,
    }
    if record.get("providerErrorCode"):
        stored["errorCode"] = record["providerErrorCode"]
    for key in ("interactionId", "requestId", "submissionStartedAt", "submittedAt",
                "providerFailureObservedAt", "providerFinalStatus", "completedAt"):
        value = record.get(key)
        if isinstance(value, str) and len(value) <= 256:
            stored[key] = value
    try:
        invocation = LAMBDA.invoke(
            FunctionName=GOOGLE_WORKER_ARN,
            InvocationType="RequestResponse",
            Payload=json.dumps({"op": "diagnose_failed_interaction",
                                "jobId": job_id, "sceneId": scene_id}).encode(),
        )
        if invocation.get("FunctionError"):
            return response(200, {"jobId": job_id, "sceneId": scene_id,
                                  "diagnostic": {**stored, "reason": "DIAGNOSTIC_WORKER_FAILED"}})
        details = json.loads(invocation["Payload"].read())
        if not isinstance(details, dict) or details.get("status") not in (
                "READ_ONLY_DIAGNOSTIC", "UNAVAILABLE", "RECORDED_PROVIDER_FAILURE"):
            return response(200, {"jobId": job_id, "sceneId": scene_id,
                                  "diagnostic": {**stored, "reason": "INVALID_DIAGNOSTIC_RESPONSE"}})
        return response(200, {"jobId": job_id, "sceneId": scene_id,
                              "diagnostic": details})
    except Exception:
        LOG.exception("Read-only Google failure diagnostic unavailable")
        return response(200, {"jobId": job_id, "sceneId": scene_id,
                              "diagnostic": stored})


def start_real_scene(job_id, scene_id, event):
    """Explicit, atomic, one-attempt-per-scene paid submission for this job."""
    if REAL_VIDEO_MODE != "ENABLED" or not GOOGLE_STATE_MACHINE_ARN:
        return response(503, {"status": "PAID_GENERATION_DISABLED"})
    if scene_id not in VIDEO_SCENES:
        return response(404, {"error": "Unknown scene"})
    data = body_json(event)
    if data != {"confirmPaidScene": scene_id}:
        return response(400, {"error": "Explicit confirmation for this paid scene required"})
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    token = headers.get("idempotency-key", "")
    if not IDEMPOTENCY_RE.fullmatch(token):
        return response(400, {"error": "Idempotency-Key required (12-128 characters)"})
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    if job.get("paidVideoApprovals", {}).get(scene_id) or job.get("realVideos", {}).get(scene_id):
        return response(409, {"error": "This scene already has a paid request; check status, do not retry"})
    if any(job.get("scenes", {}).get(scene, {}).get("status") != "COMPLETED"
           or not job.get("scenes", {}).get(scene, {}).get("resultKey")
           for scene in VIDEO_SCENES):
        return response(409, {"error": "All three reference images must be complete"})
    source = job["scenes"][scene_id]["resultKey"]
    if not re.fullmatch(r"results/" + re.escape(job_id) + "/" + scene_id + r"\.(png|jpg|jpeg|webp)", source):
        return response(409, {"error": "Unexpected reference image key"})
    try:
        S3.head_object(Bucket=UPLOAD_BUCKET, Key=source)
    except ClientError:
        return response(409, {"error": "Reference image missing or expired"})
    name = "real-" + job_id + "-" + scene_id
    # Ensure the map exists. No paid request is submitted by this initialisation.
    DDB.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET paidVideoApprovals = if_not_exists(paidVideoApprovals, :empty)",
        ConditionExpression="attribute_exists(jobId)",
        ExpressionAttributeValues={":empty": {}},
    )
    # Reserve the scene atomically BEFORE starting the workflow. An ambiguous
    # Step Functions response never authorises a second chargeable attempt.
    try:
        DDB.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET paidVideoApprovals.#scene = :approval",
            ConditionExpression="attribute_exists(jobId) AND attribute_not_exists(paidVideoApprovals.#scene) AND attribute_not_exists(realVideos.#scene)",
            ExpressionAttributeNames={"#scene": scene_id},
            ExpressionAttributeValues={":approval": {
                "status": "REQUESTED", "executionName": name,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "requestFingerprint": hashlib.sha256(token.encode()).hexdigest(),
            }},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return response(409, {"error": "This scene already has a paid request"})
        raise
    try:
        STATES.start_execution(
            stateMachineArn=GOOGLE_STATE_MACHINE_ARN, name=name,
            input=json.dumps({"jobId": job_id, "sceneId": scene_id}, separators=(",", ":")),
        )
    except STATES.exceptions.ExecutionAlreadyExists:
        return response(202, {"status": "ALREADY_REQUESTED", "sceneId": scene_id})
    except Exception:
        LOG.exception("Paid workflow handoff uncertain for scene %s", scene_id)
        return response(503, {"status": "SUBMISSION_STATUS_UNKNOWN",
                              "error": "Please check status; do not retry this scene"})
    return response(202, {"status": "REAL_VIDEO_REQUESTED", "sceneId": scene_id})



def retry_failed_real_scene(job_id, scene_id, event):
    """User-confirmed, scene-specific new paid attempt after terminal failure.

    Preserve the other two scenes and all completed reference images.
    A conditional DynamoDB replacement archives the previous attempt and
    prevents duplicate/concurrent retries.
    """
    if REAL_VIDEO_MODE != "ENABLED" or not GOOGLE_STATE_MACHINE_ARN:
        return response(503, {"status": "PAID_GENERATION_DISABLED"})
    if scene_id not in VIDEO_SCENES:
        return response(404, {"error": "Unknown scene"})
    if body_json(event) != {"confirmRetryScene": scene_id}:
        return response(400, {"error": "Explicit paid retry confirmation required"})
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    token = headers.get("idempotency-key", "")
    if not IDEMPOTENCY_RE.fullmatch(token):
        return response(400, {"error": "Idempotency-Key required (12-128 characters)"})
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    approval = job.get("paidVideoApprovals", {}).get(scene_id, {})
    current = job.get("realVideos", {}).get(scene_id, {})
    # Replaying the same API request cannot create a third attempt, including
    # when the first response was lost after Step Functions start_execution.
    if (approval.get("attemptNumber", 1) > 1
            and approval.get("requestFingerprint") == fingerprint):
        return response(202, {"status": "ALREADY_REQUESTED", "sceneId": scene_id})
    attempt = int(approval.get("attemptNumber", 1))
    if (current.get("status") != "FAILED"
            or current.get("providerFinalStatus") != "failed"
            or attempt >= MAX_VIDEO_ATTEMPTS or job.get("assembly")):
        return response(409, {"error": "Only confirmed failed scenes may be retried (maximum 10 paid attempts)"})
    old_name = approval.get("executionName")
    if not isinstance(old_name, str) or not re.fullmatch(
            r"real-" + re.escape(job_id) + "-" + scene_id + r"(?:-r(?:[2-9]|10))?", old_name):
        return response(409, {"error": "Original video workflow ownership invalid"})
    old_request = current.get("requestId")
    if old_request != old_name:
        return response(409, {"error": "Original video attempt requires reconciliation"})
    # The original workflow must have fully stopped before we replace its
    # attempt record: it may otherwise still be running Finalize.
    old_arn = GOOGLE_STATE_MACHINE_ARN.replace(":stateMachine:", ":execution:", 1) + ":" + old_name
    try:
        execution = STATES.describe_execution(executionArn=old_arn)
    except ClientError:
        return response(409, {"error": "Original workflow status unavailable; do not retry"})
    if execution.get("status") != "SUCCEEDED":
        return response(409, {"error": "Original video workflow not safely completed"})
    if any(job.get("scenes", {}).get(scene, {}).get("status") != "COMPLETED"
           for scene in VIDEO_SCENES):
        return response(409, {"error": "Reference images must remain completed"})
    original_key = current.get("sourceImageKey")
    if original_key != job.get("scenes", {}).get(scene_id, {}).get("resultKey"):
        return response(409, {"error": "Reference image changed since original generation"})
    try:
        S3.head_object(Bucket=UPLOAD_BUCKET, Key=original_key)
    except ClientError:
        return response(409, {"error": "Reference image missing or expired"})
    new_attempt = attempt + 1
    name = f"real-{job_id}-{scene_id}-r{new_attempt}"
    previous = list(approval.get("history", []))
    if len(previous) >= MAX_VIDEO_ATTEMPTS - 1:
        return response(409, {"error": "Video retry history limit reached"})
    # Keep the previous provider interaction ID, failure reason and timestamps
    # for operator audit; never erase successful videos in other scene keys.
    previous.append({"approval": {k: approval.get(k) for k in (
        "executionName", "createdAt", "requestFingerprint", "attemptNumber")},
        "video": current})
    next_approval = {
        "status": "REQUESTED", "executionName": name,
        "attemptNumber": new_attempt, "createdAt": datetime.now(timezone.utc).isoformat(),
        "requestFingerprint": fingerprint, "history": previous,
    }
    try:
        DDB.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET paidVideoApprovals.#scene = :approval REMOVE realVideos.#scene",
            ConditionExpression=(
                "attribute_exists(jobId) AND realVideos.#scene.#status = :failed "
                "AND realVideos.#scene.requestId = :request "
                "AND paidVideoApprovals.#scene.executionName = :oldname"
            ),
            ExpressionAttributeNames={"#scene": scene_id, "#status": "status"},
            ExpressionAttributeValues={
                ":approval": next_approval, ":failed": "FAILED",
                ":request": old_request, ":oldname": old_name,
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return response(409, {"error": "Scene already retried or changed; refresh status"})
        raise
    # The reservation is permanent if handoff is uncertain. Do not make a
    # second paid workflow invocation merely because this API call timed out.
    try:
        STATES.start_execution(
            stateMachineArn=GOOGLE_STATE_MACHINE_ARN, name=name,
            input=json.dumps({"jobId": job_id, "sceneId": scene_id}, separators=(",", ":")),
        )
    except STATES.exceptions.ExecutionAlreadyExists:
        return response(202, {"status": "ALREADY_REQUESTED", "sceneId": scene_id})
    except Exception:
        LOG.exception("Paid retry handoff uncertain for %s", scene_id)
        return response(503, {"status": "SUBMISSION_STATUS_UNKNOWN",
                              "error": "Check existing scene; do not resubmit"})
    return response(202, {"status": "REAL_VIDEO_RETRY_REQUESTED",
                          "sceneId": scene_id, "attemptNumber": new_attempt})


def scene1_preflight(job_id):
    """Read-only validation of Scene 1 S3 image and Google payload."""
    if not GOOGLE_WORKER_ARN:
        return response(503, {"status": "NOT_CONFIGURED", "paidGenerationEnabled": False})
    if not get_job(job_id):
        return response(404, {"error": "Job not found"})
    result = LAMBDA.invoke(
        FunctionName=GOOGLE_WORKER_ARN,
        InvocationType="RequestResponse",
        Payload=json.dumps({"op": "scene_preflight", "jobId": job_id,
                            "sceneId": "scene1", "requestId": "freepreflight"}).encode(),
    )
    if result.get("FunctionError"):
        return response(409, {"status": "PREFLIGHT_FAILED",
                              "error": "Scene 1 image or prompt validation failed",
                              "paidGenerationEnabled": False})
    data = json.loads(result["Payload"].read())
    if data.get("status") != "READY" or data.get("googleSubmissionMade") is not False:
        return response(502, {"status": "PREFLIGHT_FAILED", "paidGenerationEnabled": False})
    return response(200, data)


def paid_google_connectivity_check():
    """Free WIF connectivity check for the *actual paid-video* worker."""
    if not GOOGLE_WORKER_ARN:
        return response(503, {"status": "NOT_CONFIGURED", "errorCode": "GOOGLE_WORKER_UNAVAILABLE"})
    try:
        invocation = LAMBDA.invoke(
            FunctionName=GOOGLE_WORKER_ARN,
            InvocationType="RequestResponse",
            Payload=json.dumps({"op": "auth_check"}).encode(),
        )
        if invocation.get("FunctionError"):
            return response(502, {"status": "AUTHENTICATION_FAILED",
                                  "errorCode": "GOOGLE_WORKER_RUNTIME_ERROR"})
        result = json.loads(invocation["Payload"].read())
    except (ClientError, ValueError, TypeError):
        LOG.exception("Paid Google connection check could not complete")
        return response(502, {"status": "AUTHENTICATION_FAILED",
                              "errorCode": "GOOGLE_WORKER_CONNECTIVITY_ERROR"})
    if result.get("status") != "AUTHENTICATED":
        return response(502, {"status": "AUTHENTICATION_FAILED",
                              "errorCode": result.get("errorCode", "GOOGLE_AUTH_ERROR")})
    return response(200, {"status": "AUTHENTICATED", "videoGenerationEnabled": False,
                          "paidGenerationTriggered": False})


def google_auth_check():
    """Keyless Google authentication test; never starts a model job."""
    if not VIDEO_WORKER_ARN:
        return response(503, {"status": "NOT_CONFIGURED", "videoGenerationEnabled": False})
    invocation = LAMBDA.invoke(
        FunctionName=VIDEO_WORKER_ARN,
        InvocationType="RequestResponse",
        Payload=json.dumps({"op": "auth_check"}).encode(),
    )
    if invocation.get("FunctionError"):
        # Lambda may have failed before the worker's safe exception handler ran,
        # e.g. an import error during cold start. Only expose an allowlisted
        # runtime error category, not Lambda's raw error message or stack trace.
        try:
            failure = json.loads(invocation["Payload"].read())
        except (ValueError, TypeError):
            failure = {}
        runtime_type = failure.get("errorType", "") if isinstance(failure, dict) else ""
        if runtime_type in {"Runtime.ImportModuleError", "Runtime.UserCodeSyntaxError"}:
            # The module name is public package metadata, not a credential.
            # Extract only a constrained Python identifier from the Lambda
            # runtime error, never return the raw message or traceback.
            module_match = re.search(
                r"No module named ['\\\"]([A-Za-z_][A-Za-z0-9_.]{0,100})['\\\"]",
                failure.get("errorMessage", "") if isinstance(failure, dict) else "",
            )
            code = ("VIDEO_WORKER_MISSING_MODULE_" + module_match.group(1)
                    if module_match else "VIDEO_WORKER_IMPORT_ERROR")
        elif runtime_type in {"Runtime.ExitError", "Runtime.OutOfMemory", "Runtime.Unknown"}:
            code = "VIDEO_WORKER_RUNTIME_EXIT"
        elif runtime_type in {"TaskTimedOut", "TimeoutError"}:
            code = "VIDEO_WORKER_TIMEOUT"
        else:
            code = "VIDEO_WORKER_INVOCATION_ERROR"
        LOG.warning("Google auth probe runtime failure code=%s type=%s", code, runtime_type[:80])
        return response(502, {
            "status": "AUTHENTICATION_FAILED",
            "videoGenerationEnabled": False,
            "errorCode": code,
            "message": "Video worker failed before completing Google authentication",
        })
    result = json.loads(invocation["Payload"].read())
    if result.get("status") != "AUTHENTICATED":
        return response(502, {
            "status": "AUTHENTICATION_FAILED",
            "errorCode": result.get("errorCode", "GOOGLE_AUTH_ERROR"),
            "videoGenerationEnabled": False,
        })
    return response(200, {"status": "AUTHENTICATED", "videoGenerationEnabled": False})


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    try:
        assembly_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/assembly", path)
        if assembly_match:
            if method == "POST":
                return start_assembly(assembly_match.group(1))
            if method == "GET":
                job = get_job(assembly_match.group(1))
                if not job:
                    return response(404, {"error": "Job not found"})
                return response(200, {"jobId": job["jobId"], "assembly": assembly_public(job)})
        diagnostic_match = re.fullmatch(
            r"/jobs/([0-9a-f]{32})/real-videos/(scene[123])/diagnostics", path)
        if method == "GET" and diagnostic_match:
            return failed_video_diagnostic(diagnostic_match.group(1),
                                           diagnostic_match.group(2))
        retry_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/real-videos/(scene[123])/retry", path)
        if method == "POST" and retry_match:
            return retry_failed_real_scene(retry_match.group(1), retry_match.group(2), event)
        real_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/real-videos/(scene[123])", path)
        if method == "POST" and real_match:
            return start_real_scene(real_match.group(1), real_match.group(2), event)
        preflight_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/video/scene1/preflight", path)
        if method == "GET" and preflight_match:
            return scene1_preflight(preflight_match.group(1))
        if method == "GET" and path == "/video/auth-check":
            return google_auth_check()
        if method == "GET" and path == "/video/paid-google-auth-check":
            return paid_google_connectivity_check()
        if method == "GET" and path == "/scenarios":
            return response(200, {
                "scenarios": [{
                    "id": SCENARIO_ID,
                    "name": "AFL Grand Final — Winning Kick",
                    "sceneCount": 3,
                    "imageGenerationEnabled": True,
                    "parallelScenes": True,
                }]
            })
        if method == "POST" and path == "/uploads":
            return presign_upload(event)
        if method == "POST" and path == "/jobs":
            return create_job(event)
        preview_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/video/(scene[123])/preview", path)
        if method == "GET" and preview_match:
            return preview_video_prompt(preview_match.group(1), preview_match.group(2))
        video_scene_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/videos/(scene[123])", path)
        if video_scene_match:
            if method == "POST":
                return start_mock_video(video_scene_match.group(1), video_scene_match.group(2), event)
            if method == "GET":
                return video_status(video_scene_match.group(1), video_scene_match.group(2))
        video_all_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/videos", path)
        if method == "GET" and video_all_match:
            return video_status(video_all_match.group(1))
        generate_match = re.fullmatch(r"/jobs/([0-9a-f]{32})/generate", path)
        if method == "POST" and generate_match:
            return start_images(generate_match.group(1))
        match = re.fullmatch(r"/jobs/([0-9a-f]{32})(/images)?", path)
        if method == "GET" and match:
            job = get_job(match.group(1))
            if not job:
                return response(404, {"error": "Job not found"})
            if match.group(2):
                return images_for_job(job)
            return response(200, {"job": public_job(job)})
        return response(404, {"error": "Unknown endpoint"})
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except Exception:
        LOG.exception("AFL API request failed")
        return response(500, {"error": "Internal server error"})
