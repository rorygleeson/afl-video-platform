"""Stage 1B development API. No image generation in this milestone."""
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
FRONTEND_ORIGIN = os.environ["FRONTEND_ORIGIN"]

S3 = boto3.client("s3")
DDB = boto3.resource("dynamodb").Table(JOBS_TABLE)

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
        "status": item["status"],
        "createdAt": item["createdAt"],
        "imageCount": 0,
    }


def create_job(event):
    data = body_json(event)
    scenario_id = data.get("scenarioId")
    team = data.get("team")
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
            {"scenarioId": scenario_id, "team": team, "uploadKey": upload_key},
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


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    try:
        if method == "GET" and path == "/scenarios":
            return response(200, {
                "scenarios": [{
                    "id": SCENARIO_ID,
                    "name": "AFL Grand Final — Winning Kick",
                    "sceneCount": 3,
                    "imageGenerationEnabled": False,
                }]
            })
        if method == "POST" and path == "/uploads":
            return presign_upload(event)
        if method == "POST" and path == "/jobs":
            return create_job(event)
        match = re.fullmatch(r"/jobs/([0-9a-f]{32})(/images)?", path)
        if method == "GET" and match:
            job = get_job(match.group(1))
            if not job:
                return response(404, {"error": "Job not found"})
            if match.group(2):
                return response(200, {"jobId": job["jobId"], "status": job["status"], "images": []})
            return response(200, {"job": public_job(job)})
        return response(404, {"error": "Unknown endpoint"})
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except Exception:
        LOG.exception("AFL API request failed")
        return response(500, {"error": "Internal server error"})
