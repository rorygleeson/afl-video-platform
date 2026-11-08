"""Deterministic, strictly validated preview of S3-hosted AFL video prompts.

No video model calls, no S3 writes and no billing. Templates and scenario
configuration originate in version-controlled GitHub and are published to S3.
"""
import json
import re
from string import Template

PREFIX = "scenarios/afl-grand-final-winning-kick/v1/"
VIDEO_PREFIX = PREFIX + "video/"
SCENE_IDS = ("scene1", "scene2", "scene3")
MAX_TEMPLATE_BYTES = 12000
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '-][^\W\d_]+)*$", re.UNICODE)
TEAM_NAMES = {
    "adelaide": "Adelaide Crows", "brisbane": "Brisbane Lions",
    "carlton": "Carlton Blues", "collingwood": "Collingwood Magpies",
    "essendon": "Essendon Bombers", "fremantle": "Fremantle Dockers",
    "geelong": "Geelong Cats", "gold-coast": "Gold Coast Suns",
    "gws": "Greater Western Sydney Giants", "hawthorn": "Hawthorn Hawks",
    "melbourne": "Melbourne Demons", "north-melbourne": "North Melbourne Kangaroos",
    "port-adelaide": "Port Adelaide Power", "richmond": "Richmond Tigers",
    "st-kilda": "St Kilda Saints", "sydney": "Sydney Swans",
    "west-coast": "West Coast Eagles", "western-bulldogs": "Western Bulldogs",
}


def validate_name(value, label):
    if not isinstance(value, str):
        raise ValueError(label + " is required")
    normalized = " ".join(value.strip().split())
    if len(normalized) > 40 or not NAME_RE.fullmatch(normalized):
        raise ValueError(label + " must be 1-40 letters with optional spaces, hyphens or apostrophes")
    return normalized


def render_scene(s3, asset_bucket, job, scene_id, *, allow_paid=False):
    if scene_id not in SCENE_IDS:
        raise ValueError("Unsupported scene")
    team = job.get("team")
    if team not in TEAM_NAMES:
        raise ValueError("Unsupported team")
    first = validate_name(job.get("firstName"), "First name")
    last = validate_name(job.get("lastName"), "Surname")
    config_raw = s3.get_object(Bucket=asset_bucket, Key=VIDEO_PREFIX + "config.json")["Body"].read(MAX_TEMPLATE_BYTES)
    config = json.loads(config_raw)
    enabled = config.get("videoGenerationEnabled")
    if type(enabled) is not bool:
        raise ValueError("Invalid video generation configuration")
    # Paid approval cannot be inferred from S3 alone: the caller must
    # explicitly opt in, and the worker/API additionally enforce IAM+job gates.
    scenes = {scene["id"]: scene for scene in config["scenes"]}
    definition = scenes[scene_id]
    template_name = definition["template"]
    if template_name != scene_id + "-video.txt":
        raise ValueError("Unexpected scene template")
    guernsey_raw = s3.get_object(Bucket=asset_bucket, Key=PREFIX + "config.json")["Body"].read(MAX_TEMPLATE_BYTES)
    guernsey = json.loads(guernsey_raw)["teamGuernseys"][team]
    variables = {
        "firstName": first,
        "lastName": last,
        "fullName": first + " " + last,
        "teamName": TEAM_NAMES[team],
        "guernsey": guernsey,
    }
    commentary = Template(definition["commentaryTemplate"]).substitute(variables)
    variables["commentary"] = commentary
    prompt = s3.get_object(Bucket=asset_bucket, Key=VIDEO_PREFIX + template_name)["Body"].read(MAX_TEMPLATE_BYTES).decode("utf-8")
    rendered = Template(prompt).substitute(variables)
    return {
        "sceneId": scene_id,
        "promptVersion": config["version"],
        "prompt": rendered,
        "commentary": commentary,
        "playerName": variables["fullName"],
        "teamName": variables["teamName"],
        "videoGenerationEnabled": enabled and allow_paid,
        "videoSubmissionAvailable": enabled and allow_paid,
        "durationSeconds": definition["durationSeconds"],
    }
