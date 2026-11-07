"""Stage 3 mock-only video worker. Contains NO provider client or billable path.

Stage 4 will add an API admission boundary; this Stage 3 isolated state
machine deliberately requires an existing completed reference image and
writes MOCK_COMPLETED markers only (never claims to have produced an MP4).
A stuck slot fails CLOSED and requires operator reconciliation; no TTL unlock.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

if os.environ.get("MODE") != "MOCK_ONLY":
    raise RuntimeError("Stage 3 worker runs only in MOCK_ONLY mode")

S3 = boto3.client("s3")
JOBS = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
SLOTS = boto3.resource("dynamodb").Table(os.environ["ADMISSION_TABLE"])
BUCKET = os.environ["UPLOAD_BUCKET"]
SCENES = {"scene1", "scene2", "scene3"}
ID_RE = re.compile(r"^[0-9a-f]{32}$")
REQUEST_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
SLOT = "GLOBAL_VIDEO_SLOT_0"


def validate(event):
    job_id, scene_id, request_id = (event.get(k) for k in ("jobId", "sceneId", "requestId"))
    if not isinstance(job_id, str) or not ID_RE.fullmatch(job_id):
        raise ValueError("Invalid job ID")
    if scene_id not in SCENES:
        raise ValueError("Invalid scene")
    if not isinstance(request_id, str) or not REQUEST_RE.fullmatch(request_id):
        raise ValueError("Invalid request ID")
    return job_id, scene_id, request_id


def get_job(job_id):
    result = JOBS.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
    if not result:
        raise ValueError("Job not found")
    return result


def current_record(job_id, scene_id, request_id):
    record = get_job(job_id).get("videos", {}).get(scene_id, {})
    if record.get("requestId") != request_id:
        raise RuntimeError("Request ownership mismatch")
    return record


def set_scene(job_id, scene_id, request_id, status, expected):
    JOBS.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET videos.#s.#st = :new, videos.#s.updatedAt = :now",
        ConditionExpression="videos.#s.requestId = :owner AND videos.#s.#st = :old",
        ExpressionAttributeNames={"#s": scene_id, "#st": "status"},
        ExpressionAttributeValues={
            ":owner": request_id, ":old": expected, ":new": status,
            ":now": datetime.now(timezone.utc).isoformat(),
        },
    )


def acquire(job_id, scene_id, request_id):
    job = get_job(job_id)
    source = job.get("scenes", {}).get(scene_id, {})
    if source.get("status") != "COMPLETED" or not source.get("resultKey"):
        raise ValueError("Reference image not completed")
    source_key = source["resultKey"]
    if not isinstance(source_key, str) or not source_key.startswith(f"results/{job_id}/{scene_id}."):
        raise ValueError("Unexpected image key")
    S3.head_object(Bucket=BUCKET, Key=source_key)
    # No automatic expiry: fail closed if execution state is unknown.
    # Only a reconciled, confirmed-terminal owner may release the slot.
    SLOTS.put_item(
        Item={"slotId": SLOT, "owner": request_id, "jobId": job_id,
              "sceneId": scene_id, "acquiredAt": int(time.time())},
        ConditionExpression="attribute_not_exists(slotId)",
    )
    try:
        # The parent map must exist before setting a nested scene attribute.
        JOBS.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET videos = if_not_exists(videos, :empty)",
            ConditionExpression="attribute_exists(jobId)",
            ExpressionAttributeValues={":empty": {}},
        )
        JOBS.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET videos.#s = :record",
            ConditionExpression="attribute_exists(jobId) AND attribute_not_exists(videos.#s)",
            ExpressionAttributeNames={"#s": scene_id},
            ExpressionAttributeValues={":record": {
                "status": "MOCK_CLAIMED", "requestId": request_id,
                "sourceImageKey": source_key, "updatedAt": datetime.now(timezone.utc).isoformat(),
            }},
        )
    except Exception:
        # Ownership still proven because we created this slot; never steal a different owner.
        release(job_id, scene_id, request_id)
        raise
    return {"status": "MOCK_CLAIMED", "sourceImageKey": source_key}


def release(job_id, scene_id, request_id):
    try:
        SLOTS.delete_item(
            Key={"slotId": SLOT},
            ConditionExpression="#o = :owner AND jobId = :job AND sceneId = :scene",
            ExpressionAttributeNames={"#o": "owner"},
            ExpressionAttributeValues={
                ":owner": request_id, ":job": job_id, ":scene": scene_id,
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"status": "NOT_OWNER"}
        raise
    return {"status": "RELEASED"}


def lambda_handler(event, context):
    job_id, scene_id, request_id = validate(event)
    op = event.get("op")
    if op == "claim":
        return acquire(job_id, scene_id, request_id)
    if op == "submit":
        record = current_record(job_id, scene_id, request_id)
        if record["status"] == "MOCK_CLAIMED":
            set_scene(job_id, scene_id, request_id, "MOCK_GENERATING", "MOCK_CLAIMED")
        elif record["status"] != "MOCK_GENERATING":
            raise RuntimeError("Invalid mock state")
        return {"status": "MOCK_GENERATING"}
    if op == "poll":
        if current_record(job_id, scene_id, request_id).get("status") != "MOCK_GENERATING":
            raise RuntimeError("Mock scene not generating")
        return {"status": "MOCK_READY"}
    if op == "save":
        record = current_record(job_id, scene_id, request_id)
        if record["status"] != "MOCK_GENERATING":
            raise RuntimeError("Invalid mock state")
        # Not an MP4: honest marker for infrastructure testing only.
        key = f"video-mock/{job_id}/{scene_id}/{request_id}.json"
        S3.put_object(
            Bucket=BUCKET, Key=key,
            Body=json.dumps({"mock": True, "jobId": job_id,
                             "sceneId": scene_id, "requestId": request_id}).encode(),
            ContentType="application/json", ServerSideEncryption="AES256",
        )
        set_scene(job_id, scene_id, request_id, "MOCK_COMPLETED", "MOCK_GENERATING")
        return {"status": "MOCK_COMPLETED", "markerKey": key}
    if op == "failed":
        try:
            record = current_record(job_id, scene_id, request_id)
            if record.get("status") not in ("MOCK_COMPLETED", "MOCK_FAILED"):
                JOBS.update_item(
                    Key={"jobId": job_id},
                    UpdateExpression="SET videos.#s.#st = :failed",
                    ConditionExpression="videos.#s.requestId = :owner",
                    ExpressionAttributeNames={"#s": scene_id, "#st": "status"},
                    ExpressionAttributeValues={":failed": "MOCK_FAILED", ":owner": request_id},
                )
        finally:
            release(job_id, scene_id, request_id)
        return {"status": "MOCK_FAILED"}
    if op == "release":
        if current_record(job_id, scene_id, request_id).get("status") not in ("MOCK_COMPLETED", "MOCK_FAILED"):
            raise RuntimeError("Cannot release a nonterminal generation")
        return release(job_id, scene_id, request_id)
    raise ValueError("Unsupported mock operation")
