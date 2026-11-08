"""Stage 5A Google Omni 1.2 Flash adapter, installed but HARD-LOCKED.

No API route invokes this worker. The companion Step Functions starts with a
gate which fails closed while PAID_GENERATION_ENABLED=false. Model defaults
UNCONFIGURED. Nothing in Stage 5A invokes Google's paid model.

Never retry an ambiguous POST: status UNKNOWN_SUBMISSION means an operator
must reconcile billing and provider-side submission before another request.
Google protocol is based on November 2026 public docs and NOT authenticated
end-to-end verified for this project's model quota.
"""
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from google.auth import load_credentials_from_dict
from google.auth.transport.requests import Request
from video_prompts import render_scene

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
S3 = boto3.client("s3")
JOBS = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
SLOTS = boto3.resource("dynamodb").Table(os.environ["ADMISSION_TABLE"])
BUCKET = os.environ["UPLOAD_BUCKET"]
PROMPT_BUCKET = os.environ["SCENARIO_ASSET_BUCKET"]

MODEL = os.environ.get("GOOGLE_VIDEO_MODEL", "UNCONFIGURED")
PAID_ENABLED = os.environ.get("PAID_GENERATION_ENABLED", "false")
PROJECT = os.environ.get("GOOGLE_PROJECT_ID", "afl-video-platform")
BASE = f"https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/global/interactions"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MP4_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 75 * 1024 * 1024
JOB_RE = re.compile(r"^[0-9a-f]{32}$")
SCENES = {"scene1", "scene2", "scene3"}
SLOT = "GLOBAL_VIDEO_SLOT_0"
TERMINAL = {"completed", "failed", "cancelled", "incomplete"}

class ContractError(ValueError):
    pass

class UnknownSubmission(RuntimeError):
    pass


def require_paid_unlock(job=None, scene=None):
    if PAID_ENABLED != "true" or MODEL != "gemini-omni-1.2-flash":
        raise PermissionError("PAID_GENERATION_DISABLED")
    if not isinstance(job, str) or not JOB_RE.fullmatch(job) or scene not in SCENES:
        raise PermissionError("PAID_SCENE_INVALID")
    approval = get_job(job).get("paidVideoApprovals", {}).get(scene, {})
    if approval.get("status") != "REQUESTED" or approval.get("executionName") != "real-" + job + "-" + scene:
        raise PermissionError("PAID_SCENE_NOT_USER_APPROVED")


def validate(event):
    job, scene, request = (event.get(k) for k in ("jobId", "sceneId", "requestId"))
    if not isinstance(job, str) or not JOB_RE.fullmatch(job):
        raise ContractError("Invalid job ID")
    if scene not in SCENES:
        raise ContractError("Invalid scene")
    if not isinstance(request, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request):
        raise ContractError("Invalid request ID")
    return job, scene, request


def safe_error_code(exc):
    """Categorise Google auth failures without exposing token or response body."""
    detail = str(exc).lower()
    if ("iamcredentials.googleapis.com" in detail or
            "iam.serviceaccounts.getaccesstoken" in detail):
        if any(x in detail for x in ("disabled", "not been used", "service_disabled", "not enabled")):
            return "GOOGLE_IAM_CREDENTIALS_API_DISABLED"
        return "GOOGLE_SERVICE_ACCOUNT_IMPERSONATION_DENIED"
    if "sts.googleapis.com" in detail:
        if any(x in detail for x in ("disabled", "not been used", "service_disabled")):
            return "GOOGLE_STS_API_DISABLED"
        return "GOOGLE_FEDERATION_STS_REJECTED"
    if "invalid_grant" in detail or "attribute_condition" in detail:
        return "GOOGLE_FEDERATION_REJECTED"
    if "permission_denied" in detail or "permission denied" in detail or "403" in detail:
        return "GOOGLE_PERMISSION_DENIED"
    if "timeout" in detail or "timed out" in detail:
        return "GOOGLE_AUTH_TIMEOUT"
    if "connection" in detail or "name resolution" in detail:
        return "GOOGLE_AUTH_NETWORK_ERROR"
    # Categorise common google-auth and AWS external-account errors without
    # returning provider messages, signed requests, or credential material.
    if any(x in detail for x in ("aws security credentials", "aws credentials",
                                 "aws metadata", "aws region", "169.254.169.254")):
        return "GOOGLE_AWS_CREDENTIAL_SOURCE_ERROR"
    if any(x in detail for x in ("invalid_target", "invalid_subject_token",
                                 "invalid audience", "invalid_request")):
        return "GOOGLE_FEDERATION_INVALID_REQUEST"
    if any(x in detail for x in ("unauthorized_client", "invalid_client",
                                 "invalid_credential", "invalid identity")):
        return "GOOGLE_FEDERATION_INVALID_CLIENT"
    if any(x in detail for x in ("certificate verify failed", "ssl:", "tls:")):
        return "GOOGLE_AUTH_TLS_ERROR"
    return "GOOGLE_AUTH_ERROR"


def credentials():
    """Use existing AWS->Google WIF provider; new IAM role needs Google binding."""
    number = os.environ["GOOGLE_PROJECT_NUMBER"]
    pool = os.environ["GOOGLE_WIF_POOL_ID"]
    provider = os.environ["GOOGLE_WIF_PROVIDER_ID"]
    account = os.environ["GOOGLE_SERVICE_ACCOUNT"]
    info = {
        "type": "external_account",
        "audience": f"//iam.googleapis.com/projects/{number}/locations/global/workloadIdentityPools/{pool}/providers/{provider}",
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url":
            f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{account}:generateAccessToken",
        "credential_source": {
            "environment_id": "aws1",
            "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
            "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
            "regional_cred_verification_url":
                "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
            "imdsv2_session_token_url": "http://169.254.169.254/latest/api/token",
        },
    }
    creds, _ = load_credentials_from_dict(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    if not creds.valid or not creds.token:
        raise ContractError("Google WIF access token unavailable")
    return creds.token


def google_json(method, url, payload=None, max_bytes=MAX_RESPONSE_BYTES):
    # No caller-supplied URL; only exact Vertex Interactions endpoint or
    # interaction IDs, never arbitrary remote or provider-supplied URIs.
    if url != BASE and not re.fullmatch(re.escape(BASE) + r"/[A-Za-z0-9_-]{1,256}", url):
        raise ContractError("Untrusted Google URL")
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    token = credentials()
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json; charset=utf-8",
                 "Accept": "application/json"},
    )
    # HTTP response is bounded to protect Lambda memory; never log Google
    # payload (contains private images, signed credentials, or video bytes).
    try:
        with urllib.request.urlopen(req, timeout=75) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ContractError("Google response exceeds safety limit")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        LOG.warning("Google HTTP %d operation=%s", exc.code, method)
        # Even HTTP 4xx may be ambiguous after POST; never resubmit in automation.
        raise ContractError(f"Google {method} HTTP {exc.code}") from None


def image_payload(blob, content_type, prompt, duration):
    if content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise ContractError("Unsupported reference image MIME type")
    if not 0 < len(blob) <= MAX_IMAGE_BYTES:
        raise ContractError("Reference image exceeds model input size")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 12000:
        raise ContractError("Invalid S3 scene prompt")
    if duration not in (8, 10):
        raise ContractError("Unexpected scene duration")
    return {
        "model": "gemini-omni-1.2-flash",
        "background": True,
        "input": [
            {"type": "text", "text": prompt},
            {"type": "image", "data": base64.b64encode(blob).decode("ascii"),
             "mime_type": content_type},
        ],
        "response_format": [{
            "type": "video", "delivery": "inline", "aspect_ratio": "9:16",
            "resolution": "720p", "duration": str(duration) + "s",
        }],
        "generation_config": {"video_config": {"task": "image_to_video"}},
    }


def scene_preflight(job, scene):
    """Free read-only contract check; NEVER calls Google Interactions or modifies job state."""
    if (PAID_ENABLED == "false" and MODEL != "UNCONFIGURED") or (PAID_ENABLED == "true" and MODEL != "gemini-omni-1.2-flash"):
        raise PermissionError("PREFLIGHT_WORKER_CONFIG_INVALID")
    if scene not in SCENES:
        raise ContractError("Invalid preflight scene")
    item = get_job(job)
    ref = item.get("scenes", {}).get(scene, {})
    key = ref.get("resultKey", "")
    if ref.get("status") != "COMPLETED" or not isinstance(key, str):
        raise ContractError("Scene 1 reference image not completed")
    if not re.fullmatch(r"results/" + re.escape(job) + "/" + scene + r"\.(png|jpg|jpeg|webp)", key):
        raise ContractError("Unexpected Scene 1 image key")
    config = render_scene(S3, PROMPT_BUCKET, item, scene)
    if config["durationSeconds"] not in (8, 10) or config["videoGenerationEnabled"] is not False:
        raise ContractError("Unexpected free preview configuration")
    # Validate actual current S3 object. Expired 7-day images must fail closed.
    response = S3.get_object(Bucket=BUCKET, Key=key)
    raw = response["Body"].read(MAX_IMAGE_BYTES + 1)
    req = image_payload(raw, response.get("ContentType", ""), config["prompt"], config["durationSeconds"])
    assert req["background"] is True and req["response_format"][0]["duration"] == str(config["durationSeconds"]) + "s"
    # Return only metadata, never the image, prompt, PII, auth token or encoded payload.
    return {"status": "READY", "sceneId": scene, "durationSeconds": config["durationSeconds"],
            "imageBytes": len(raw), "imageSha256": hashlib.sha256(raw).hexdigest(),
            "promptBytes": len(config["prompt"].encode("utf-8")),
            "modelConfigured": MODEL == "gemini-omni-1.2-flash", "paidGenerationEnabled": PAID_ENABLED == "true",
            "googleSubmissionMade": False}


def get_job(job):
    item = JOBS.get_item(Key={"jobId": job}, ConsistentRead=True).get("Item")
    if not item:
        raise ContractError("Job missing")
    return item


def owner_record(job, scene, request):
    record = get_job(job).get("realVideos", {}).get(scene, {})
    if record.get("requestId") != request:
        raise ContractError("Video attempt ownership mismatch")
    return record


def transition(job, scene, request, old, new, extra=None):
    expr = "SET realVideos.#scene.#status = :new, realVideos.#scene.updatedAt = :now"
    names = {"#scene": scene, "#status": "status"}
    values = {
        ":new": new, ":old": old, ":owner": request,
        ":now": datetime.now(timezone.utc).isoformat(),
    }
    for idx, (key, value) in enumerate((extra or {}).items()):
        placeholder = f"#k{idx}"
        v = f":v{idx}"
        expr += f", realVideos.#scene.{placeholder} = {v}"
        names[placeholder] = key
        values[v] = value
    JOBS.update_item(
        Key={"jobId": job}, UpdateExpression=expr,
        ConditionExpression="realVideos.#scene.requestId = :owner AND realVideos.#scene.#status = :old",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def claim(job, scene, request):
    require_paid_unlock(job, scene)
    item = get_job(job)
    ref = item.get("scenes", {}).get(scene, {})
    key = ref.get("resultKey", "")
    if ref.get("status") != "COMPLETED" or not isinstance(key, str):
        raise ContractError("Reference image not completed")
    if not re.fullmatch(r"results/" + re.escape(job) + "/" + scene + r"\.(png|jpg|jpeg|webp)", key):
        raise ContractError("Unexpected reference image S3 key")
    if item.get("realVideos", {}).get(scene):
        raise ContractError("Video scene already claimed")
    # Reserve globally against both mock and real workflows. Never auto-expire
    # the slot while the provider status might still be unknown.
    SLOTS.put_item(
        Item={"slotId": SLOT, "owner": request, "jobId": job, "sceneId": scene},
        ConditionExpression="attribute_not_exists(slotId)",
    )
    try:
        JOBS.update_item(
            Key={"jobId": job},
            UpdateExpression="SET realVideos = if_not_exists(realVideos, :empty)",
            ConditionExpression="attribute_exists(jobId)",
            ExpressionAttributeValues={":empty": {}},
        )
        JOBS.update_item(
            Key={"jobId": job},
            UpdateExpression="SET realVideos.#scene = :value",
            ConditionExpression="attribute_exists(jobId) AND attribute_not_exists(realVideos.#scene)",
            ExpressionAttributeNames={"#scene": scene},
            ExpressionAttributeValues={
                ":value": {"status": "PREPARED", "requestId": request,
                           "sourceImageKey": key, "createdAt": datetime.now(timezone.utc).isoformat()}
            },
        )
    except Exception:
        release(job, scene, request)
        raise
    return {"status": "PREPARED"}


def release(job, scene, request):
    try:
        SLOTS.delete_item(
            Key={"slotId": SLOT},
            ConditionExpression="#owner = :r AND jobId = :j AND sceneId = :s",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":r": request, ":j": job, ":s": scene},
        )
        return {"status": "RELEASED"}
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"status": "NOT_OWNER"}
        raise


def submit(job, scene, request):
    require_paid_unlock(job, scene)
    record = owner_record(job, scene, request)
    if record.get("status") != "PREPARED":
        raise ContractError("Submission cannot be retried or started from this state")
    # All local validation happens BEFORE any billable POST. Failures here
    # are known not to be charged and must not occupy the global slot forever.
    try:
        rendered = render_scene(S3, PROMPT_BUCKET, get_job(job), scene, allow_paid=True)
        if rendered.get("videoGenerationEnabled") is not True:
            raise PermissionError("SCENARIO_VIDEO_GENERATION_DISABLED")
        key = record["sourceImageKey"]
        image = S3.get_object(Bucket=BUCKET, Key=key)
        content_type = image.get("ContentType", "")
        blob = image["Body"].read(MAX_IMAGE_BYTES + 1)
        body = image_payload(blob, content_type, rendered["prompt"], rendered["durationSeconds"])
    except Exception:
        try:
            transition(job, scene, request, "PREPARED", "FAILED",
                       {"errorCode": "PRE_SUBMISSION_VALIDATION_FAILED"})
        finally:
            release(job, scene, request)
        raise
    # Set ambiguous state BEFORE issuing the billable POST. A Lambda timeout
    # must NEVER cause Step Functions to resubmit this operation.
    transition(job, scene, request, "PREPARED", "SUBMISSION_UNKNOWN")
    try:
        result = google_json("POST", BASE, body, max_bytes=1024 * 1024)
    except Exception:
        LOG.exception("Google submission outcome uncertain; manual reconciliation required")
        raise UnknownSubmission("UNKNOWN_SUBMISSION_REQUIRES_MANUAL_RECONCILIATION") from None
    interaction = result.get("id", "")
    if not isinstance(interaction, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", interaction):
        raise UnknownSubmission("No safe Google interaction ID; do not retry paid call")
    transition(job, scene, request, "SUBMISSION_UNKNOWN", "PROCESSING",
               {"interactionId": interaction})
    return {"status": "PROCESSING", "interactionId": interaction}


def get_video_content(response):
    """Only consume an actual inline MP4 in model_output steps."""
    for step in response.get("steps", []):
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for content in step.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "video":
                continue
            if content.get("mime_type") != "video/mp4":
                raise ContractError("Expected video/mp4 output")
            encoded = content.get("data")
            if not isinstance(encoded, str) or len(encoded) > (MAX_MP4_BYTES * 4 // 3 + 16):
                raise ContractError("Inline video missing or exceeds limit")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ContractError("Invalid inline MP4 base64") from None
            if not 128 <= len(data) <= MAX_MP4_BYTES or b"ftyp" not in data[:64]:
                raise ContractError("Video output does not appear to be a bounded MP4")
            return data
    raise ContractError("Completed response contains no inline MP4")


def poll(job, scene, request):
    require_paid_unlock(job, scene)
    record = owner_record(job, scene, request)
    if record.get("status") != "PROCESSING":
        raise ContractError("Video not in processing state")
    interaction = record.get("interactionId")
    if not isinstance(interaction, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", interaction):
        raise ContractError("Missing Google interaction ID")
    reply = google_json("GET", BASE + "/" + interaction)
    state = reply.get("status")
    # Google's Interactions API can queue asynchronous jobs before execution.
    # A queued job is still alive, not a failure or a second billable request.
    if state in ("queued", "in_progress", "requires_action"):
        return {"status": "WAITING", "providerStatus": state}
    if state in ("failed", "cancelled", "incomplete", "budget_exceeded"):
        transition(job, scene, request, "PROCESSING", "FAILED", {"errorCode": "GOOGLE_" + state.upper()})
        return {"status": "FAILED"}
    if state != "completed":
        raise ContractError("Unknown provider status, manual review")
    # Save output directly from Lambda to private S3, never via SFN result.
    blob = get_video_content(reply)
    key = f"videos/{job}/{scene}.mp4"
    S3.put_object(
        Bucket=BUCKET, Key=key, Body=blob,
        ContentType="video/mp4", ServerSideEncryption="AES256")
    S3.head_object(Bucket=BUCKET, Key=key)
    transition(job, scene, request, "PROCESSING", "COMPLETED",
               {"resultKey": key, "sizeBytes": len(blob)})
    return {"status": "COMPLETED", "resultKey": key}


def finalize(job, scene, request):
    state = owner_record(job, scene, request).get("status")
    if state not in ("COMPLETED", "FAILED"):
        raise ContractError("Nonterminal video cannot release admission")
    return release(job, scene, request)


def operator_slot_status():
    """Read-only concurrency ownership check; no Google or billable operations."""
    record = SLOTS.get_item(Key={"slotId": SLOT}, ConsistentRead=True).get("Item")
    if not record:
        return {"status": "FREE"}
    job_id = record.get("jobId", "")
    scene = record.get("sceneId", "")
    owner = record.get("owner", "")
    info = {}
    if isinstance(job_id, str) and JOB_RE.fullmatch(job_id):
        job = get_job(job_id)
        info = (job.get("realVideos", {}).get(scene, {}) or
                job.get("videos", {}).get(scene, {}))
    return {
        "status": "OCCUPIED",
        "jobId": job_id, "sceneId": scene, "owner": owner,
        "videoStatus": info.get("status", "NO_VIDEO_RECORD"),
        "providerInteractionKnown": bool(info.get("interactionId")),
        "providerInteractionId": info.get("interactionId", ""),
        "createdAt": info.get("createdAt", ""),
    }


def lambda_handler(event, context):
    op = event.get("op")
    # Gate is first in state machine; also independently enforce this for all
    # operations that could reach the Google paid API.
    if op == "gate":
        require_paid_unlock(event.get("jobId"), event.get("sceneId"))
        return {"status": "ENABLED"}
    if op == "operator_slot_status":
        return operator_slot_status()
    if op == "contract_health":
        return {"status": "LOCKED" if PAID_ENABLED != "true" else "CONFIGURED",
                "model": MODEL, "paidGenerationEnabled": PAID_ENABLED == "true"}
    if op == "scene_preflight":
        job, scene, _ = validate(event)
        return scene_preflight(job, scene)
    if op == "auth_check":
        # Non-billable diagnostic; the new worker role must successfully
        # exchange its AWS identity for the configured Google service account.
        # No model API endpoint or job can be accessed through this operation.
        if PAID_ENABLED not in ("true", "false"):
            raise PermissionError("INVALID_WORKER_MODE")
        try:
            credentials()
        except Exception as exc:
            code = safe_error_code(exc)
            LOG.warning("Google worker WIF preflight failed: code=%s exception_type=%s",
                        code, type(exc).__name__)
            return {"status": "AUTHENTICATION_FAILED", "errorCode": code,
                    "exceptionType": type(exc).__name__}
        return {"status": "AUTHENTICATED", "paidGenerationEnabled": False}
    job, scene, request = validate(event)
    if op == "claim":
        return claim(job, scene, request)
    if op == "submit":
        return submit(job, scene, request)
    if op == "poll":
        return poll(job, scene, request)
    if op == "finalize":
        return finalize(job, scene, request)
    raise ContractError("Unknown operation")
