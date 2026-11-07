"""Stage 1C: explicit, billable Scene 1 Replicate pilot worker.

Only the API's explicit /generate action starts this Step Functions workflow.
No image is processed during upload or CloudFormation deployment.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
REGION = os.environ["AWS_REGION"]
UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
ASSET_BUCKET = os.environ["ASSET_BUCKET"]
TABLE = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
S3 = boto3.client(
    "s3", region_name=REGION,
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
SECRETS = boto3.client("secretsmanager", region_name=REGION)
SECRET_NAME = os.environ["REPLICATE_SECRET_NAME"]
CONFIG_KEY = os.environ["SCENARIO_CONFIG_KEY"]
MODEL_URL = "https://api.replicate.com/v1/models/google/nano-banana-pro/predictions"

def get_token():
    secret = SECRETS.get_secret_value(SecretId=SECRET_NAME)["SecretString"]
    token = json.loads(secret)["REPLICATE_API_TOKEN"]
    if not isinstance(token, str) or not token.startswith("r8_"):
        raise RuntimeError("Invalid Replicate token in AWS Secrets Manager")
    return token

def request_json(url, token, payload=None):
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "afl-video-platform-scene1/1.0",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        # Never log tokens, request payloads, or private source URLs.
        LOG.error("Replicate HTTP error code=%d", exc.code)
        raise RuntimeError(f"Replicate request failed (HTTP {exc.code})") from None

def job_by_id(job_id):
    data = TABLE.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
    if not data:
        raise RuntimeError("Unknown job")
    return data

def update_job(job_id, expression, values, names=None, condition=None):
    args = {
        "Key": {"jobId": job_id},
        "UpdateExpression": expression,
        "ExpressionAttributeValues": values,
    }
    if names:
        args["ExpressionAttributeNames"] = names
    if condition:
        args["ConditionExpression"] = condition
    TABLE.update_item(**args)

def load_scenario():
    raw = S3.get_object(Bucket=ASSET_BUCKET, Key=CONFIG_KEY)["Body"].read()
    config = json.loads(raw)
    if config.get("version") != "v1" or config.get("pilotSceneId") != "scene1":
        raise RuntimeError("Unsupported scenario config")
    scene = next((x for x in config["scenes"] if x["id"] == "scene1"), None)
    if not scene or scene.get("model") != "google/nano-banana-pro" or not scene.get("enabled"):
        raise RuntimeError("Scene 1 is not enabled for the pilot")
    return config, scene

def submit(job_id):
    job = job_by_id(job_id)
    if job.get("status") != "AWAITING_IMAGE_PIPELINE":
        raise RuntimeError("Job has already started or cannot be submitted")
    config, scene = load_scenario()
    team = job["team"]
    if team not in config["teamGuernseys"]:
        raise RuntimeError("Unknown selected team")
    key = job["uploadKey"]
    master_key = scene["masterKey"]
    if not master_key.startswith("scenarios/afl-grand-final-winning-kick/v1/"):
        raise RuntimeError("Invalid scenario master key")
    # Check both input files exist before triggering a billable prediction.
    S3.head_object(Bucket=UPLOAD_BUCKET, Key=key)
    S3.head_object(Bucket=ASSET_BUCKET, Key=master_key)
    selfie_url = S3.generate_presigned_url(
        "get_object", Params={"Bucket": UPLOAD_BUCKET, "Key": key}, ExpiresIn=1800,
    )
    master_url = S3.generate_presigned_url(
        "get_object", Params={"Bucket": ASSET_BUCKET, "Key": master_key}, ExpiresIn=1800,
    )
    prompt = (
        "Photorealistic cinematic AFL stadium sports image edit. "
        "Input image 1 is the MASTER COMPOSITION to edit. Input image 2 is the CUSTOMER SELFIE "
        "and is the ONLY facial identity reference. Keep the master image's camera position, "
        "framing, athlete pose, hands, red Australian football, background and stadium lighting. "
        "Replace the CENTRAL player's facial appearance and visible hair with the recognisable "
        "person in image 2, preserving realistic anatomy, expression and matching stadium light. "
        "Change ONLY the central player's playing guernsey and shorts to this selected team style: "
        + config["teamGuernseys"][team] + ". "
        "Avoid sponsor names, copyrighted badges, text, logos and watermarks. "
        "Keep the scene natural and credible. Do not copy image 2's background or clothes. "
        "One continuous 9:16 vertical photo, no collage, sharp professional sports photography."
    )
    # Idempotence boundary: record SUBMITTING before externally billable call.
    # Never automatically retry an uncertain-outcome prediction submission.
    update_job(
        job_id,
        "SET #s = :s, submissionStartedAt = :t",
        {":s": "GENERATING_IMAGES", ":await": "AWAITING_IMAGE_PIPELINE",
         ":t": int(time.time())},
        {"#s": "status"}, "#s = :await",
    )
    body = {
        "input": {
            "prompt": prompt,
            "image_input": [master_url, selfie_url],
            "aspect_ratio": "9:16",
            "resolution": "2K",
            "output_format": "png",
            "allow_fallback_model": False,
        }
    }
    resp = request_json(MODEL_URL, get_token(), body)
    prediction_id = resp.get("id")
    if not isinstance(prediction_id, str) or not prediction_id:
        raise RuntimeError("Replicate did not return prediction id")
    update_job(
        job_id,
        "SET predictionId = :p, predictionStartedAt = :t",
        {":p": prediction_id, ":t": int(time.time())},
    )
    return {"jobId": job_id, "predictionId": prediction_id, "status": "processing"}

def check(job_id, prediction_id):
    job = job_by_id(job_id)
    if job.get("predictionId") != prediction_id:
        raise RuntimeError("Prediction ID does not match job")
    # Wait no longer than 20 minutes; fail without submitting a new paid request.
    if time.time() - int(job.get("predictionStartedAt", 0)) > 1200:
        return {"jobId": job_id, "predictionId": prediction_id, "status": "failed"}
    response = request_json(
        "https://api.replicate.com/v1/predictions/" +
        urllib.parse.quote(prediction_id, safe=""),
        get_token(),
    )
    if response.get("id") != prediction_id:
        raise RuntimeError("Unexpected Replicate prediction ID")
    remote_status = response.get("status")
    if remote_status == "succeeded":
        return {"jobId": job_id, "predictionId": prediction_id, "status": "succeeded",
                "output": response.get("output")}
    if remote_status in ("failed", "canceled", "aborted"):
        return {"jobId": job_id, "predictionId": prediction_id, "status": "failed"}
    return {"jobId": job_id, "predictionId": prediction_id, "status": "processing"}

def save(job_id, prediction_id, output):
    job = job_by_id(job_id)
    if job.get("predictionId") != prediction_id:
        raise RuntimeError("Prediction ID mismatch")
    # Nano Banana Pro returns a URL; some models return a list of URLs.
    url = output[0] if isinstance(output, list) and output else output
    if not isinstance(url, str):
        raise RuntimeError("Replicate output was not an image URL")
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (
        host == "replicate.delivery" or host.endswith(".replicate.delivery")
    ):
        raise RuntimeError("Untrusted Replicate output host")
    req = urllib.request.Request(url, headers={"User-Agent": "afl-video-platform/1.0"})
    # Limit output size; never allow unlimited external responses into memory.
    with urllib.request.urlopen(req, timeout=30) as resp:
        mime = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise RuntimeError("Replicate output was not a supported image")
        data = resp.read(15 * 1024 * 1024 + 1)
        if not data or len(data) > 15 * 1024 * 1024:
            raise RuntimeError("Replicate output was empty or too large")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
    result_key = f"results/{job_id}/scene1.{ext}"
    S3.put_object(
        Bucket=UPLOAD_BUCKET, Key=result_key, Body=data,
        ContentType=mime, ServerSideEncryption="AES256",
    )
    update_job(
        job_id,
        "SET #s = :s, scene1Key = :k, completedAt = :t",
        {":s": "SCENE1_COMPLETED", ":k": result_key,
         ":t": datetime.now(timezone.utc).isoformat()},
        {"#s": "status"},
    )
    return {"jobId": job_id, "status": "SCENE1_COMPLETED"}

def failed(job_id):
    # Do not overwrite a completed job if failure handling arrives late.
    try:
        update_job(
            job_id,
            "SET #s = :fail",
            {":fail": "FAILED", ":done": "SCENE1_COMPLETED"},
            {"#s": "status"}, "#s <> :done",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    return {"jobId": job_id, "status": "FAILED"}

def lambda_handler(event, context):
    job_id = event["jobId"]
    op = event["op"]
    if op == "submit":
        return submit(job_id)
    if op == "check":
        return check(job_id, event["predictionId"])
    if op == "save":
        return save(job_id, event["predictionId"], event["output"])
    if op == "failed":
        return failed(job_id)
    raise ValueError("Invalid worker operation")
