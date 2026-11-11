"""Three-scene parallel Vertex AI image editing worker.

Explicit job start -> prepare -> Map(MaxConcurrency=3) -> finalize.
Ambiguous paid requests are never automatically resubmitted.
"""
import json
import logging
from datetime import datetime, timezone
import os
import time

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
CONFIG_KEY = os.environ["SCENARIO_CONFIG_KEY"]
VALID_SCENES = ("scene1", "scene2", "scene3")
SCENARIO_PREFIX = "scenarios/afl-grand-final-winning-kick/v1/"


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
        if (scene.get("model") not in ("gemini-3-pro-image", "google/nano-banana-pro") or
                scene.get("operation") != "image_edit" or
                scene.get("masterKey") != SCENARIO_PREFIX + scene["id"] + "-start.png"):
            raise RuntimeError("Invalid scene model or master image")
    # Reject missing or malformed prompt data before any billable image submission.
    validate_prompt_config(config)
    return config


def validate_prompt_config(config):
    shared = config.get("sharedPrompt")
    if not isinstance(shared, str) or not 100 <= len(shared) <= 8000:
        raise RuntimeError("Invalid or missing shared image prompt in S3 configuration")
    for scene in config["scenes"]:
        instructions = scene.get("promptInstructions")
        if not isinstance(instructions, str) or not 100 <= len(instructions) <= 5000:
            raise RuntimeError("Invalid or missing image scene instructions: " + scene["id"])


def build_image_prompt(config, scene, team):
    # All authored prompt wording lives in S3; only safe dynamic composition lives here.
    validate_prompt_config(config)
    guernsey = config["teamGuernseys"][team]
    return (config["sharedPrompt"] + " Selected playing guernsey style: " +
            guernsey + ". " + scene["promptInstructions"])


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


def generate(job_id, scene_id):
    import google_direct
    return google_direct.generate(__import__(__name__), job_id, scene_id)


def fail_scene(job_id, scene_id):
    if scene_id not in VALID_SCENES:
        raise ValueError("Invalid scene")
    job = job_by_id(job_id)
    status = job.get("scenes", {}).get(scene_id, {}).get("status")
    if status not in ("COMPLETED", "FAILED"):
        try:
            previous = job.get("scenes", {}).get(scene_id, {})
            # A sanitized reason for the UI; no selfie URL, token, prompt
            # or raw provider error body is persisted.
            reason = previous.get("errorCode") or (
                "GOOGLE_IMAGE_FAILED" if status == "GENERATING" else "GOOGLE_PREPARATION_FAILED")
            scene_update(job_id, scene_id, "FAILED",
                         {"failedAt": datetime.now(timezone.utc).isoformat(),
                          "errorCode": reason},
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
    """Terminalize a failed execution; no new Google image calls.

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
                scene["errorCode"] = scene.get("errorCode") or "EXECUTION_RECOVERED"
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

    Safety threshold exceeds both Google image generation and the state-machine
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
    if op == "generate":
        return generate(job_id, scene_id)
    if op == "failed":
        return fail_scene(job_id, scene_id)
    if op == "finalize":
        return finalize(job_id)
    if op == "recover":
        return recover(job_id)
    raise ValueError("Unrecognized operation")
