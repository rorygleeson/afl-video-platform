"""Direct Vertex AI Nano Banana Pro adapter with Google's supported WIF auth.

Runs within the existing image Lambda and preserves its DynamoDB/S3 contracts.
Only an explicitly rejected HTTP 429 is retried. All ambiguous failures are terminal.
"""
import base64
import binascii
from datetime import datetime, timezone
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
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


def post_json(url, body, bearer=None, limit=1024 * 1024, timeout=20, form=False):
    allowed = {
        ENDPOINT, "https://sts.googleapis.com/v1/token",
        f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{ACCOUNT}:generateAccessToken",
    }
    if url not in allowed:
        raise ValueError("Unexpected Google endpoint")
    payload = (urllib.parse.urlencode(body).encode() if form else
               json.dumps(body, separators=(",", ":")).encode())
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
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


def probe_auth():
    """No paid image API call: only STS and IAMCredentials authentication."""
    from google.auth import load_credentials_from_dict
    from google.auth.transport.requests import Request
    try:
        credentials, _ = load_credentials_from_dict(
            credential_info(), scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        if not credentials.valid or not credentials.token:
            return "GOOGLE_AUTH_NO_ACCESS_TOKEN"
        return "GOOGLE_AUTH_OK"
    except Exception as exc:
        return "GOOGLE_AUTH_" + auth_diagnostic(exc)


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


def extract_png(result):
    candidates = result.get("candidates") if isinstance(result, dict) else None
    if not isinstance(candidates, list):
        raise ValueError("Google returned no candidates")
    for candidate in candidates:
        for part in (candidate.get("content") or {}).get("parts", []):
            data = part.get("inlineData") if isinstance(part, dict) else None
            if not isinstance(data, dict) or not data.get("data"):
                continue
            try:
                payload = base64.b64decode(data["data"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Invalid Google image encoding") from exc
            if not payload or len(payload) > MAX_IMAGE:
                raise ValueError("Google output image size invalid")
            if image_type(payload) != data.get("mimeType") or data["mimeType"] != "image/png":
                # Preserve the existing *.png S3/resultKey contract; don't mislabel JPEG.
                raise ValueError("Google did not return PNG output")
            if len(payload) < 24 or payload[12:16] != b"IHDR":
                raise ValueError("Google PNG header invalid")
            width = int.from_bytes(payload[16:20], "big")
            height = int.from_bytes(payload[20:24], "big")
            if width < 720 or height < 1280 or abs(width * 16 - height * 9) > max(width, height) // 50:
                raise ValueError("Google output is not high-resolution vertical 9:16")
            return payload
    raise ValueError("Google returned no image content")


def retry_delay(error, attempt):
    if error.retry_after is not None:
        return None if error.retry_after > 120 else max(1, error.retry_after + random.uniform(0, 1))
    return min(60, 2 ** attempt + random.uniform(0, 2))


def generate(worker, job_id, scene_id):
    """Generate and persist one scene. Existing worker owns the scene lifecycle."""
    if scene_id not in worker.VALID_SCENES:
        raise ValueError("Unknown scene")
    job = worker.job_by_id(job_id)
    if job.get("status") != "GENERATING_IMAGES" or job["scenes"][scene_id]["status"] != "PENDING":
        raise RuntimeError("Job or scene not ready")
    config = worker.load_scenario()
    scene = next(s for s in config["scenes"] if s["id"] == scene_id)
    worker.scene_update(job_id, scene_id, "SUBMITTING",
                        {"submissionStartedAt": int(time.time())}, expected="PENDING")
    stage = "MASTER_READ"
    try:
        master = s3_image(worker.S3, worker.ASSET_BUCKET, scene["masterKey"])
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
    for attempt in range(6):
        try:
            result = post_json(ENDPOINT, payload, bearer=token,
                               limit=MAX_RESPONSE, timeout=240)
            break
        except GoogleHTTPError as exc:
            if exc.status == 429 and attempt < 5:
                delay = retry_delay(exc, attempt)
                if delay is None:
                    worker.scene_update(job_id, scene_id, "GENERATING",
                                        {"errorCode": "GOOGLE_RETRY_AFTER_TOO_LONG"}, expected="GENERATING")
                    raise
                time.sleep(delay)
                continue
            error = ("GOOGLE_RATE_LIMIT" if exc.status == 429 else
                     "GOOGLE_AUTH_ERROR" if exc.status in (401, 403) else
                     "GOOGLE_INPUT_OR_SAFETY" if exc.status in (400, 422) else
                     f"GOOGLE_HTTP_{exc.status}")
            worker.scene_update(job_id, scene_id, "GENERATING",
                                {"errorCode": error}, expected="GENERATING")
            raise
        except Exception:
            # Timeout, broken connection or unreadable response: may have incurred cost.
            worker.scene_update(job_id, scene_id, "GENERATING",
                                {"errorCode": "GOOGLE_SUBMISSION_UNKNOWN"}, expected="GENERATING")
            raise
    try:
        image = extract_png(result)
        key = f"results/{job_id}/{scene_id}.png"
        worker.S3.put_object(Bucket=worker.UPLOAD_BUCKET, Key=key, Body=image,
                             ContentType="image/png", ServerSideEncryption="AES256")
        worker.scene_update(job_id, scene_id, "COMPLETED",
                            {"resultKey": key,
                             "completedAt": datetime.now(timezone.utc).isoformat()},
                            expected="GENERATING")
        return {"sceneId": scene_id, "status": "COMPLETED"}
    except Exception:
        worker.scene_update(job_id, scene_id, "GENERATING",
                            {"errorCode": "GOOGLE_OUTPUT_ERROR"}, expected="GENERATING")
        raise
