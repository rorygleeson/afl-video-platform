"""Direct Vertex AI Nano Banana Pro adapter with Google's supported WIF auth.

Runs within the existing image Lambda and preserves its DynamoDB/S3 contracts.
Only an explicitly rejected HTTP 429 is retried. All ambiguous failures are terminal.
"""
import base64
import binascii
from io import BytesIO
from datetime import datetime, timezone
import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

import boto3

MODEL = "gemini-3-pro-image"
PROJECT = os.environ["GOOGLE_PROJECT_ID"]
NUMBER = os.environ["GOOGLE_PROJECT_NUMBER"]
POOL = os.environ["GOOGLE_WIF_POOL_ID"]
PROVIDER = os.environ["GOOGLE_WIF_PROVIDER_ID"]
ACCOUNT = os.environ["GOOGLE_SERVICE_ACCOUNT"]
ENDPOINT = (f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/"
            f"publishers/google/models/{MODEL}:generateContent")
MAX_INPUT = 15 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
MAX_RESPONSE = 40 * 1024 * 1024


class GoogleHTTPError(RuntimeError):
    def __init__(self, status, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"Google HTTP {status}")


def retry_after_seconds(value):
    if not value:
        return None
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except (TypeError, ValueError):
        pass
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, (stamp - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def post_json(url, body, bearer=None, limit=1024 * 1024, timeout=20):
    allowed = {ENDPOINT}
    if url not in allowed:
        raise ValueError("Unexpected Google endpoint")
    payload = json.dumps(body, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Google response exceeds size limit")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        retry_after = retry_after_seconds(exc.headers.get("Retry-After")) if exc.headers else None
        code = exc.code
        exc.close()
        raise GoogleHTTPError(code, retry_after) from None


def credential_info():
    """Match the working video worker's keyless AWS external-account config."""
    info = {
        "type": "external_account",
        "audience": (f"//iam.googleapis.com/projects/{NUMBER}/locations/global/"
                     f"workloadIdentityPools/{POOL}/providers/{PROVIDER}"),
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url":
            f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{ACCOUNT}:generateAccessToken",
        "credential_source": {
            "environment_id": "aws1",
            "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
            "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
            "regional_cred_verification_url":
                "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
            "imdsv2_session_token_url": "http://169.254.169.254/latest/api/token",
        },
    }

    return info


def auth_diagnostic(exc):
    """Allowlisted error classification only; no provider bodies or credentials."""
    detail = " ".join(str(a) for a in getattr(exc, "args", ())).lower()
    cls = type(exc).__name__
    patterns = (
        ("invalid_target", "STS_INVALID_TARGET"),
        ("invalid_subject_token", "STS_INVALID_SUBJECT_TOKEN"),
        ("invalid_grant", "STS_INVALID_GRANT"),
        ("invalid_request", "STS_INVALID_REQUEST"),
        ("unauthorized_client", "STS_UNAUTHORIZED_CLIENT"),
        ("permission_denied", "IAM_PERMISSION_DENIED"),
        ("permission denied", "IAM_PERMISSION_DENIED"),
        ("getaccesstoken", "IAM_IMPERSONATION_DENIED"),
        ("impersonated credentials", "IAM_IMPERSONATION_FAILED"),
        ("service_disabled", "API_DISABLED"),
        ("not been used", "API_DISABLED"),
        ("invalid_scope", "INVALID_SCOPE"),
        ("metadata server", "AWS_METADATA_ERROR"),
        ("aws region", "AWS_REGION_ERROR"),
        ("aws security credentials", "AWS_CREDENTIALS_ERROR"),
        ("failed to establish a new connection", "NETWORK_ERROR"),
        ("name resolution", "NETWORK_ERROR"),
        ("timeout", "NETWORK_TIMEOUT"),
        ("response is not json", "INVALID_PROVIDER_RESPONSE"),
    )
    for needle, code in patterns:
        if needle in detail:
            return code
    return "EXCEPTION_" + (cls if cls in ("RefreshError", "TransportError",
                                          "DefaultCredentialsError", "ImportError",
                                          "ModuleNotFoundError", "ValueError", "TypeError",
                                          "RuntimeError", "AttributeError") else "OTHER")


def access_token():
    """Reuse working video Lambda's google-auth AWS WIF implementation."""
    from google.auth import load_credentials_from_dict
    from google.auth.transport.requests import Request
    try:
        credentials, _ = load_credentials_from_dict(
            credential_info(), scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        if not credentials.valid or not credentials.token:
            raise RuntimeError("No Google WIF access token")
        return credentials.token
    except Exception as exc:
        raise RuntimeError("GOOGLE_WIF_" + auth_diagnostic(exc)) from None



def image_type(payload):
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Invalid source image type")


def s3_image(s3, bucket, key):
    response = s3.get_object(Bucket=bucket, Key=key)
    raw = response["Body"].read(MAX_INPUT + 1)
    if not raw or len(raw) > MAX_INPUT:
        raise ValueError("Source image size invalid")
    return {"inlineData": {"mimeType": image_type(raw),
                           "data": base64.b64encode(raw).decode("ascii")}}


def request_body(prompt, master, selfie):
    return {"contents": [{"role": "user", "parts": [
        {"text": prompt}, master, selfie
    ]}], "generationConfig": {
        "responseModalities": ["TEXT", "IMAGE"],
        "imageConfig": {"aspectRatio": "9:16", "imageSize": "2K",
                        "imageOutputOptions": {"mimeType": "image/png"}},
    }}


class GoogleOutputError(ValueError):
    """An allowlisted output error that is safe to persist without raw content."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def normalise_image_png(data, declared_mime):
    """Keep existing PNG S3 paths while accepting valid Google JPEG/WEBP images."""
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise GoogleOutputError("GOOGLE_BAD_IMAGE_ENCODING") from None
    if not raw or len(raw) > MAX_IMAGE:
        raise GoogleOutputError("GOOGLE_BAD_IMAGE_SIZE")
    try:
        actual_mime = image_type(raw)
    except ValueError:
        raise GoogleOutputError("GOOGLE_UNSUPPORTED_IMAGE_TYPE") from None
    if actual_mime != declared_mime:
        raise GoogleOutputError("GOOGLE_IMAGE_MIME_MISMATCH")
    if actual_mime == "image/png":
        if len(raw) < 24 or raw[12:16] != b"IHDR":
            raise GoogleOutputError("GOOGLE_BAD_PNG_HEADER")
        width = int.from_bytes(raw[16:20], "big")
        height = int.from_bytes(raw[20:24], "big")
        output = raw
    elif actual_mime in ("image/jpeg", "image/webp"):
        # These formats may legitimately be returned even when PNG was requested.
        # Do not mislabel a JPEG as a .png S3 object.
        from PIL import Image, ImageOps, UnidentifiedImageError
        try:
            with Image.open(BytesIO(raw)) as src:
                if src.width * src.height > 20_000_000:
                    raise GoogleOutputError("GOOGLE_IMAGE_TOO_LARGE")
                rgb = ImageOps.exif_transpose(src).convert("RGB")
                width, height = rgb.size
                buf = BytesIO()
                rgb.save(buf, format="PNG")
                output = buf.getvalue()
        except (OSError, ValueError, UnidentifiedImageError):
            raise GoogleOutputError("GOOGLE_IMAGE_CONVERSION_FAILED") from None
    else:
        raise GoogleOutputError("GOOGLE_UNSUPPORTED_IMAGE_TYPE")
    # 9:16 crops can be rounded to model-specific pixel grids. Accept up to
    # five-percent aspect deviation; validate size and vertical orientation.
    if width < 720 or height < 1280 or not 0.53 <= width / height <= 0.60:
        raise GoogleOutputError("GOOGLE_IMAGE_DIMENSIONS")
    if len(output) > MAX_IMAGE:
        raise GoogleOutputError("GOOGLE_PNG_TOO_LARGE")
    return output


def extract_png(result):
    candidates = result.get("candidates") if isinstance(result, dict) else None
    if not isinstance(candidates, list) or not candidates:
        raise GoogleOutputError("GOOGLE_NO_CANDIDATES")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for part in (candidate.get("content") or {}).get("parts", []):
            data = part.get("inlineData") if isinstance(part, dict) else None
            if not isinstance(data, dict) or not data.get("data"):
                continue
            return normalise_image_png(data["data"], data.get("mimeType"))
    # Distinguish an image-less completion from invalid bytes or S3 failures.
    if any(c.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "IMAGE_SAFETY")
           for c in candidates if isinstance(c, dict)):
        raise GoogleOutputError("GOOGLE_IMAGE_SAFETY_BLOCK")
    raise GoogleOutputError("GOOGLE_NO_IMAGE_CONTENT")


# An explicit HTTP 429 rejection is the ONLY provider response that permits a
# new paid attempt. Step Functions owns the wait, not a sleeping Lambda.
MAX_ATTEMPTS = 8
MAX_RETRY_WINDOW_SECONDS = 600


def retry_delay(error, attempt):
    # Wait intervals in seconds, including a modest jitter to avoid bursts.
    # Respect provider Retry-After even when it exceeds our default backoff.
    import math
    if error.retry_after is not None:
        return max(1, math.ceil(error.retry_after + random.uniform(0, 1)))
    return min(180, 15 * (2 ** (attempt - 1))) + math.ceil(random.uniform(0, 3))


def generate(worker, job_id, scene_id):
    """Generate and persist one scene. Existing worker owns the scene lifecycle."""
    if scene_id not in worker.VALID_SCENES:
        raise ValueError("Unknown scene")
    job = worker.job_by_id(job_id)
    existing = job.get("scenes", {}).get(scene_id, {})
    previous_status = existing.get("status")
    if job.get("status") != "GENERATING_IMAGES" or previous_status not in ("PENDING", "RETRY_WAIT"):
        raise RuntimeError("Job or scene not ready: no duplicate or completed submissions")
    attempt = int(existing.get("attemptCount", 0)) + 1
    if attempt > MAX_ATTEMPTS:
        raise RuntimeError("Image retry budget already exhausted")
    first_attempt_at = int(existing.get("firstAttemptAt", 0)) or int(time.time())
    # The conditional DynamoDB claim is the per-scene admission gate.
    # Only a Step Functions retry of an explicitly rejected attempt can re-enter.
    worker.scene_update(job_id, scene_id, "SUBMITTING",
                        {"submissionStartedAt": int(time.time()),
                         "firstAttemptAt": first_attempt_at,
                         "attemptCount": attempt},
                        expected=previous_status)
    config = worker.load_scenario()
    scene = next(s for s in config["scenes"] if s["id"] == scene_id)
    stage = "MASTER_READ"
    try:
        masters = job.get("teamMasterKeys")
        if not isinstance(masters, dict) or set(masters) != set(worker.VALID_SCENES):
            raise RuntimeError("Team master images not locked by preflight")
        expected_key = worker.team_scene_master_key(job["team"], scene_id)
        if masters[scene_id] != expected_key:
            raise RuntimeError("Team master image path mismatch")
        master = s3_image(worker.S3, worker.ASSET_BUCKET, expected_key)
        stage = "SELFIE_READ"
        selfie = s3_image(worker.S3, worker.UPLOAD_BUCKET, job["uploadKey"])
        stage = "PROMPT"
        prompt = worker.build_image_prompt(config, scene, job["team"])
        stage = "AUTH"
        token = access_token()
        stage = "REQUEST_FORMAT"
        payload = request_body(prompt, master, selfie)
    except Exception as exc:
        # Failure category only: no credentials, signed identity token, prompts,
        # presigned S3 URLs, customer photos or provider response bodies.
        if stage == "AUTH":
            if isinstance(exc, GoogleHTTPError):
                code = "GOOGLE_AUTH_HTTP_" + str(exc.status)
            elif isinstance(exc, (TimeoutError, OSError)):
                code = "GOOGLE_AUTH_NETWORK"
            elif isinstance(exc, (RuntimeError, ValueError)):
                code = "GOOGLE_AUTH_INVALID_RESPONSE"
            else:
                code = "GOOGLE_AUTH_UNEXPECTED"
        else:
            code = "GOOGLE_" + stage + "_ERROR"
        worker.scene_update(job_id, scene_id, "SUBMITTING",
                            {"errorCode": code}, expected="SUBMITTING")
        raise
    worker.scene_update(job_id, scene_id, "GENERATING",
                        {"generationStartedAt": int(time.time())}, expected="SUBMITTING")
    try:
        # Exactly one Google submission per invocation. 429s are explicitly
        # rejected, so Step Functions can resubmit after a durable Wait state.
        result = post_json(ENDPOINT, payload, bearer=token,
                           limit=MAX_RESPONSE, timeout=240)
    except GoogleHTTPError as exc:
        if exc.status == 429:
            delay = retry_delay(exc, attempt)
            remaining = MAX_RETRY_WINDOW_SECONDS - (int(time.time()) - first_attempt_at)
            if attempt < MAX_ATTEMPTS and delay <= remaining:
                worker.scene_update(job_id, scene_id, "RETRY_WAIT",
                                    {"errorCode": "GOOGLE_RATE_LIMIT",
                                     "retryCount": attempt,
                                     "maxRetries": MAX_ATTEMPTS - 1,
                                     "retryAfterSeconds": delay,
                                     "nextRetryAt": int(time.time()) + delay},
                                    expected="GENERATING")
                return {"sceneId": scene_id, "status": "RETRY_WAIT",
                        "retryAfterSeconds": delay}
        error = ("GOOGLE_RATE_LIMIT" if exc.status == 429 else
                 "GOOGLE_AUTH_ERROR" if exc.status in (401, 403) else
                 "GOOGLE_INPUT_OR_SAFETY" if exc.status in (400, 422) else
                 f"GOOGLE_HTTP_{exc.status}")
        worker.scene_update(job_id, scene_id, "GENERATING",
                            {"errorCode": error}, expected="GENERATING")
        raise
    except Exception:
        # A timeout or unreadable response is ambiguous: Google might have
        # accepted and charged for it. Never automatically resubmit it.
        worker.scene_update(job_id, scene_id, "GENERATING",
                            {"errorCode": "GOOGLE_SUBMISSION_UNKNOWN"}, expected="GENERATING")
        raise
    try:
        image = extract_png(result)
    except GoogleOutputError as exc:
        worker.scene_update(job_id, scene_id, "GENERATING",
                            {"errorCode": exc.code}, expected="GENERATING")
        raise
    except Exception:
        worker.scene_update(job_id, scene_id, "GENERATING",
                            {"errorCode": "GOOGLE_IMAGE_PARSE_FAILED"}, expected="GENERATING")
        raise
    key = f"results/{job_id}/{scene_id}.png"
    try:
        worker.S3.put_object(Bucket=worker.UPLOAD_BUCKET, Key=key, Body=image,
                             ContentType="image/png", ServerSideEncryption="AES256")
    except Exception:
        worker.scene_update(job_id, scene_id, "GENERATING",
                            {"errorCode": "GOOGLE_S3_SAVE_FAILED"}, expected="GENERATING")
        raise
    worker.scene_update(job_id, scene_id, "COMPLETED",
                        {"resultKey": key,
                         "completedAt": datetime.now(timezone.utc).isoformat()},
                        expected="GENERATING")
    return {"sceneId": scene_id, "status": "COMPLETED"}
