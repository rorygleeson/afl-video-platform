"""Direct Vertex AI Nano Banana Pro adapter. No third-party credentials or dependencies.

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
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

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


def access_token():
    """Google-documented AWS SigV4 subject token -> STS -> service-account token."""
    region = os.environ["AWS_REGION"]
    audience = (f"//iam.googleapis.com/projects/{NUMBER}/locations/global/"
                f"workloadIdentityPools/{POOL}/providers/{PROVIDER}")
    host = f"sts.{region}.amazonaws.com"
    request = AWSRequest(
        method="POST",
        url=f"https://{host}/?Action=GetCallerIdentity&Version=2011-06-15",
        headers={"Host": host, "x-goog-cloud-target-resource": audience})
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS Lambda role credentials")
    SigV4Auth(credentials.get_frozen_credentials(), "sts", region).add_auth(request)
    assertion = json.dumps({
        "url": request.url, "method": request.method,
        "headers": [{"key": k, "value": v} for k, v in request.headers.items()],
    }, separators=(",", ":"))
    token = post_json("https://sts.googleapis.com/v1/token", {
        "audience": audience,
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "subject_token": assertion,
    }, form=True).get("access_token")
    if not token or not isinstance(token, str):
        raise RuntimeError("Google WIF exchange failed")
    impersonation = post_json(
        f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{ACCOUNT}:generateAccessToken",
        {"scope": ["https://www.googleapis.com/auth/cloud-platform"], "lifetime": "3600s"},
        bearer=token)
    result = impersonation.get("accessToken")
    if not isinstance(result, str) or not result:
        raise RuntimeError("Google service-account impersonation failed")
    return result


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
    try:
        master = s3_image(worker.S3, worker.ASSET_BUCKET, scene["masterKey"])
        selfie = s3_image(worker.S3, worker.UPLOAD_BUCKET, job["uploadKey"])
        prompt = worker.build_image_prompt(config, scene, job["team"])
        token = access_token()
        payload = request_body(prompt, master, selfie)
    except Exception:
        worker.scene_update(job_id, scene_id, "SUBMITTING",
                            {"errorCode": "GOOGLE_PREPARATION_ERROR"}, expected="SUBMITTING")
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
