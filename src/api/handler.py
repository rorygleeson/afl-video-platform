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
SCENARIO_ASSET_BUCKET = os.environ.get("SCENARIO_ASSET_BUCKET")

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


def preview_video_prompt(job_id, scene_id):
    """No paid video API call. Resolve exact versioned prompt from private S3."""
    job = get_job(job_id)
    if not job:
        return response(404, {"error": "Job not found"})
    if not SCENARIO_ASSET_BUCKET:
        return response(503, {"error": "Prompt assets not configured"})
    result = render_scene(S3, SCENARIO_ASSET_BUCKET, job, scene_id)
    return response(200, result)


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
        if method == "GET" and path == "/video/auth-check":
            return google_auth_check()
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
