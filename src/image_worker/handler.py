"""Three-scene parallel Replicate image editing worker.

Explicit job start -> prepare -> Map(MaxConcurrency=3) -> finalize.
Submit is never automatically retried: billable API calls have uncertain outcomes.
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
from boto3.dynamodb.conditions import Attr

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
VALID_SCENES = ("scene1", "scene2", "scene3")
SCENARIO_PREFIX = "scenarios/afl-grand-final-winning-kick/v1/"


def get_token():
    secret = SECRETS.get_secret_value(SecretId=SECRET_NAME)["SecretString"]
    token = json.loads(secret)["REPLICATE_API_TOKEN"]
    if not isinstance(token, str) or not token.startswith("r8_"):
        raise RuntimeError("Invalid Replicate API token")
    return token


def request_json(url, token, payload=None):
    data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json",
                 "User-Agent": "afl-video-platform/2.0"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        LOG.error("Replicate HTTP error code=%d", exc.code)
        raise RuntimeError(f"Replicate request failed HTTP {exc.code}") from None


def job_by_id(job_id):
    item = TABLE.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
    if not item:
        raise RuntimeError("Job not found")
    return item


def update_job(job_id, expression, values, names=None, condition=None):
    args = {"Key": {"jobId": job_id}, "UpdateExpression": expression,
            "ExpressionAttributeValues": values}
    if names:
        args["ExpressionAttributeNames"] = names
    if condition:
        args["ConditionExpression"] = condition
    TABLE.update_item(**args)


def load_scenario():
    raw = S3.get_object(Bucket=ASSET_BUCKET, Key=CONFIG_KEY)["Body"].read()
    config = json.loads(raw)
    if config.get("scenarioId") != "afl-grand-final-winning-kick" or config.get("version") != "v1":
        raise RuntimeError("Unexpected scenario configuration")
    scenes = config.get("scenes", [])
    ids = [s.get("id") for s in scenes if s.get("enabled")]
    if ids != list(VALID_SCENES):
        raise RuntimeError("Three enabled scenes are required")
    for scene in scenes:
        if (scene.get("model") != "google/nano-banana-pro" or
                scene.get("operation") != "image_edit" or
                scene.get("masterKey") != SCENARIO_PREFIX + scene["id"] + "-start.png"):
            raise RuntimeError("Invalid scene model or master image")
    return config


def prepare(job_id):
    item = job_by_id(job_id)
    if item["status"] != "AWAITING_IMAGE_PIPELINE":
        raise RuntimeError("Image generation has already been requested")
    config = load_scenario()
    if item["team"] not in config["teamGuernseys"]:
        raise RuntimeError("Unsupported team")
    S3.head_object(Bucket=UPLOAD_BUCKET, Key=item["uploadKey"])
    # Fail before any billable call if any source asset is missing.
    for scene in config["scenes"]:
        S3.head_object(Bucket=ASSET_BUCKET, Key=scene["masterKey"])
    scenes = {scene["id"]: {"status": "PENDING"} for scene in config["scenes"]}
    update_job(
        job_id,
        "SET #status = :generating, scenes = :scenes, sceneCount = :three, "
        "completedScenes = :zero, failedScenes = :zero, generationStartedAt = :now",
        {":generating": "GENERATING_IMAGES",
         ":waiting": "AWAITING_IMAGE_PIPELINE",
         ":scenes": scenes, ":three": 3, ":zero": 0, ":now": int(time.time())},
        {"#status": "status"},
        "#status = :waiting",
    )
    return {"jobId": job_id, "scenes": list(VALID_SCENES)}


def scene_update(job_id, scene_id, status, other=None, expected=None):
    if scene_id not in VALID_SCENES:
        raise ValueError("Unknown scene")
    names = {"#sc": scene_id, "#status": "status"}
    values = {":status": status}
    expression = "SET scenes.#sc.#status = :status"
    for i, (key, value) in enumerate((other or {}).items()):
        alias = "#f" + str(i)
        token = ":v" + str(i)
        names[alias] = key
        values[token] = value
        expression += ", scenes.#sc." + alias + " = " + token
    condition = None
    if expected is not None:
        values[":expected"] = expected
        condition = "scenes.#sc.#status = :expected"
    update_job(job_id, expression, values, names, condition)


def submit(job_id, scene_id):
    if scene_id not in VALID_SCENES:
        raise ValueError("Unknown scene")
    job = job_by_id(job_id)
    if job.get("status") != "GENERATING_IMAGES":
        raise RuntimeError("Job not ready")
    if job["scenes"][scene_id]["status"] != "PENDING":
        raise RuntimeError("Scene has already been submitted")
    config = load_scenario()
    scene = next(s for s in config["scenes"] if s["id"] == scene_id)
    # Guard against duplicate billable submissions before contacting Replicate.
    scene_update(job_id, scene_id, "SUBMITTING",
                 {"submissionStartedAt": int(time.time())}, expected="PENDING")
    master_url = S3.generate_presigned_url(
        "get_object", Params={"Bucket": ASSET_BUCKET, "Key": scene["masterKey"]},
        ExpiresIn=1800)
    selfie_url = S3.generate_presigned_url(
        "get_object", Params={"Bucket": UPLOAD_BUCKET, "Key": job["uploadKey"]},
        ExpiresIn=1800)
    prompt = (
        "Photorealistic cinematic Australian rules football image edit. "
        "Image 1 is the MASTER COMPOSITION; image 2 is the CUSTOMER SELFIE and is the "
        "ONLY face identity reference for the central athlete. "
        "Preserve master camera angle, perspective, lighting, athletic anatomy, "
        "stadium and natural photographic composition. "
        "Make central athlete's face distinctly recognisable as the person in image 2, "
        "with realistic hair, skin and expression. Never copy selfie background. "
        "Selected playing guernsey style: " +
        config["teamGuernseys"][job["team"]] + ". " +
        scene.get("promptInstructions", "") +
        " No sponsor marks, licensed logos, misleading text, or watermarks. "
        "Single high quality vertical sports photograph; not a collage."
    )
    result = request_json(MODEL_URL, get_token(), {
        "input": {"prompt": prompt, "image_input": [master_url, selfie_url],
                  "aspect_ratio": "9:16", "resolution": "2K",
                  "output_format": "png", "allow_fallback_model": False}
    })
    prediction_id = result.get("id")
    if not isinstance(prediction_id, str) or not prediction_id:
        raise RuntimeError("Replicate did not return a prediction identifier")
    scene_update(job_id, scene_id, "GENERATING",
                 {"predictionId": prediction_id, "predictionStartedAt": int(time.time())},
                 expected="SUBMITTING")
    return {"jobId": job_id, "sceneId": scene_id, "predictionId": prediction_id}


def check(job_id, scene_id, prediction_id):
    job = job_by_id(job_id)
    scene = job["scenes"][scene_id]
    if scene.get("predictionId") != prediction_id:
        raise RuntimeError("Prediction ID mismatch")
    if time.time() - int(scene.get("predictionStartedAt", 0)) > 1200:
        return {"status": "failed"}
    prediction = request_json(
        "https://api.replicate.com/v1/predictions/" +
        urllib.parse.quote(prediction_id, safe=""), get_token())
    if prediction.get("id") != prediction_id:
        raise RuntimeError("Unexpected prediction ID")
    status = prediction.get("status")
    if status == "succeeded":
        return {"status": "succeeded", "output": prediction.get("output")}
    if status in ("failed", "canceled", "aborted"):
        return {"status": "failed"}
    return {"status": "processing"}


def save(job_id, scene_id, prediction_id, output):
    job = job_by_id(job_id)
    scene = job["scenes"][scene_id]
    if scene.get("predictionId") != prediction_id:
        raise RuntimeError("Prediction ID mismatch")
    if scene.get("status") == "COMPLETED":
        return {"sceneId": scene_id, "status": "COMPLETED"}
    url = output[0] if isinstance(output, list) and output else output
    if not isinstance(url, str):
        raise RuntimeError("No Replicate output image URL")
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (
        host == "replicate.delivery" or host.endswith(".replicate.delivery")
    ):
        raise RuntimeError("Untrusted output domain")
    req = urllib.request.Request(url, headers={"User-Agent": "afl-video-platform/2.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        mime = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise RuntimeError("Unsupported output image")
        image = resp.read(15 * 1024 * 1024 + 1)
    if not image or len(image) > 15 * 1024 * 1024:
        raise RuntimeError("Output image size invalid")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
    key = f"results/{job_id}/{scene_id}.{ext}"
    S3.put_object(Bucket=UPLOAD_BUCKET, Key=key, Body=image,
                  ContentType=mime, ServerSideEncryption="AES256")
    scene_update(job_id, scene_id, "COMPLETED",
                 {"resultKey": key, "completedAt": datetime.now(timezone.utc).isoformat()},
                 expected="GENERATING")
    return {"sceneId": scene_id, "status": "COMPLETED"}


def fail_scene(job_id, scene_id):
    if scene_id not in VALID_SCENES:
        raise ValueError("Invalid scene")
    job = job_by_id(job_id)
    status = job.get("scenes", {}).get(scene_id, {}).get("status")
    if status not in ("COMPLETED", "FAILED"):
        try:
            scene_update(job_id, scene_id, "FAILED",
                         {"failedAt": datetime.now(timezone.utc).isoformat()},
                         expected=status)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    return {"sceneId": scene_id, "status": "FAILED"}


def finalize(job_id):
    job = job_by_id(job_id)
    scenes = job.get("scenes", {})
    statuses = [scenes.get(k, {}).get("status") for k in VALID_SCENES]
    completed = statuses.count("COMPLETED")
    failed = statuses.count("FAILED")
    if completed + failed != len(VALID_SCENES):
        raise RuntimeError("Attempt to finalize unfinished scenes")
    outcome = "COMPLETED" if completed == 3 else ("FAILED" if completed == 0 else "PARTIAL_SUCCESS")
    update_job(
        job_id,
        "SET #status = :outcome, completedScenes = :completed, "
        "failedScenes = :failed, completedAt = :t",
        {":outcome": outcome, ":completed": completed, ":failed": failed,
         ":t": datetime.now(timezone.utc).isoformat(), ":generating": "GENERATING_IMAGES"},
        {"#status": "status"},
        "#status = :generating",
    )
    return {"status": outcome, "completedScenes": completed, "failedScenes": failed}



def recover(job_id):
    """Terminalize a failed execution; no new Replicate calls.

    Avoid overwriting any image already marked COMPLETED. The state
    machine's catch executes this after failed preparation, Map, or finalization.
    """
    for attempt in range(3):
        job = job_by_id(job_id)
        overall = job.get("status")
        if overall in ("COMPLETED", "PARTIAL_SUCCESS", "FAILED"):
            return {"status": overall}
        if overall not in ("AWAITING_IMAGE_PIPELINE", "GENERATING_IMAGES"):
            raise RuntimeError("Invalid job status for recovery")
        scenes = dict(job.get("scenes") or {})
        if not scenes:
            scenes = {name: {"status": "PENDING"} for name in VALID_SCENES}
        for name in VALID_SCENES:
            scene = dict(scenes.get(name) or {"status": "PENDING"})
            if scene.get("status") != "COMPLETED":
                scene["status"] = "FAILED"
                scene["failedAt"] = datetime.now(timezone.utc).isoformat()
            scenes[name] = scene
        completed = sum(1 for scene in scenes.values() if scene["status"] == "COMPLETED")
        failed = 3 - completed
        outcome = "FAILED" if completed == 0 else (
            "COMPLETED" if completed == 3 else "PARTIAL_SUCCESS")
        # Guard against losing a concurrently saved scene: require each
        # observed scene status to be unchanged before replacing the scene map.
        names = {"#status": "status"}
        values = {
            ":outcome": outcome, ":scenes": scenes,
            ":completed": completed, ":failed": failed,
            ":now": datetime.now(timezone.utc).isoformat(), ":old": overall,
        }
        conditions = ["#status = :old"]
        old_scenes = job.get("scenes") or {}
        for index, name in enumerate(VALID_SCENES):
            alias = "#scene" + str(index)
            names[alias] = name
            if name in old_scenes and "status" in old_scenes[name]:
                value_alias = ":old_scene" + str(index)
                values[value_alias] = old_scenes[name]["status"]
                conditions.append("scenes." + alias + ".#status = " + value_alias)
            else:
                conditions.append("attribute_not_exists(scenes." + alias + ")")
        try:
            TABLE.update_item(
                Key={"jobId": job_id},
                UpdateExpression=(
                    "SET #status = :outcome, scenes = :scenes, "
                    "completedScenes = :completed, failedScenes = :failed, "
                    "completedAt = :now"
                ),
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return {"status": outcome, "completedScenes": completed, "failedScenes": failed}
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    raise RuntimeError("Job changed concurrently during recovery")


def reconcile_stalled():
    """Bounded dev-only watchdog for abandoned active jobs.

    Safety threshold exceeds both Replicate polling and the state-machine
    timeout. It only marks jobs failed; never launches paid predictions.
    """
    cutoff = int(time.time()) - 45 * 60
    checked = recovered = 0
    last_key = None
    # Bounded scan: keep development Lambda runtime and DynamoDB RCU in check.
    for _ in range(10):
        kwargs = {
            "FilterExpression": Attr("status").eq("GENERATING_IMAGES") &
                                Attr("generationStartedAt").lt(cutoff),
            "Limit": 100,
            "ProjectionExpression": "jobId, #status, generationStartedAt",
            "ExpressionAttributeNames": {"#status": "status"},
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        page = TABLE.scan(**kwargs)
        for item in page.get("Items", []):
            checked += 1
            try:
                recover(item["jobId"])
                recovered += 1
            except Exception:
                LOG.exception("Could not recover stale job")
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
    return {"checked": checked, "recovered": recovered}

def lambda_handler(event, context):
    op = event["op"]
    if op == "reconcile":
        return reconcile_stalled()
    job_id = event["jobId"]
    scene_id = event.get("sceneId")
    if op == "prepare":
        return prepare(job_id)
    if op == "submit":
        return submit(job_id, scene_id)
    if op == "check":
        return check(job_id, scene_id, event["predictionId"])
    if op == "save":
        return save(job_id, scene_id, event["predictionId"], event["output"])
    if op == "failed":
        return fail_scene(job_id, scene_id)
    if op == "finalize":
        return finalize(job_id)
    if op == "recover":
        return recover(job_id)
    raise ValueError("Unrecognized operation")
