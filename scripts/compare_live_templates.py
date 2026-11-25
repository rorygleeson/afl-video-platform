#!/usr/bin/env python3
"""Read-only, sanitized GitHub source versus deployed CloudFormation inventory."""
import hashlib
import json
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
TEMPLATES = {
    "backend": ("afl-app-dev-backend", "infrastructure/app/backend.yaml"),
    "google": ("afl-app-dev-google-video", "infrastructure/app/google-video.yaml"),
    "assembly": ("afl-app-dev-assembly", "infrastructure/app/assembly.yaml"),
    "frontend": ("afl-app-dev-frontend", "infrastructure/app/frontend.yaml"),
    "mock_video": ("afl-app-dev-video", "infrastructure/app/video.yaml"),
}


class CfnLoader(yaml.SafeLoader):
    pass


def cfn_tag(loader, suffix, node):
    key = "Fn::" + suffix
    if suffix == "Ref":
        key = "Ref"
    if suffix == "GetAtt" and isinstance(node, yaml.ScalarNode):
        return {key: loader.construct_scalar(node).split(".")}
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {key: value}


CfnLoader.add_multi_constructor("!", cfn_tag)


def parse_template(body):
    if isinstance(body, dict):
        return body
    return yaml.load(body, Loader=CfnLoader)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def compare(source, live):
    """Only logical identifiers/types/hashes leave this process; no property values."""
    source_resources = source.get("Resources", {})
    live_resources = live.get("Resources", {})
    shared = sorted(set(source_resources) & set(live_resources))
    changed = [
        {"logical_id": name,
         "source_type": source_resources[name].get("Type"),
         "live_type": live_resources[name].get("Type"),
         "source_digest": digest(source_resources[name]),
         "live_digest": digest(live_resources[name])}
        for name in shared if digest(source_resources[name]) != digest(live_resources[name])
    ]
    differences = {
        "source_only_resources": sorted(set(source_resources) - set(live_resources)),
        "live_only_resources": sorted(set(live_resources) - set(source_resources)),
        "changed_resources": changed,
        "source_only_parameters": sorted(set(source.get("Parameters", {})) - set(live.get("Parameters", {}))),
        "live_only_parameters": sorted(set(live.get("Parameters", {})) - set(source.get("Parameters", {}))),
        "source_only_outputs": sorted(set(source.get("Outputs", {})) - set(live.get("Outputs", {}))),
        "live_only_outputs": sorted(set(live.get("Outputs", {})) - set(source.get("Outputs", {}))),
        "source_only_conditions": sorted(set(source.get("Conditions", {})) - set(live.get("Conditions", {}))),
        "live_only_conditions": sorted(set(live.get("Conditions", {})) - set(source.get("Conditions", {}))),
    }
    return {"structurally_equal": not any(differences.values()), **differences}


def main():
    cf = boto3.client("cloudformation", region_name=REGION)
    result = {"scope": "source-versus-deployed-template; read-only; not CloudFormation drift detection",
              "stacks": {}, "requires_review": False}
    for label, (stack, path) in TEMPLATES.items():
        try:
            source = parse_template(Path(path).read_text(encoding="utf-8"))
            deployed = parse_template(cf.get_template(StackName=stack, TemplateStage="Original")["TemplateBody"])
            entry = compare(source, deployed)
            entry["stack"] = stack
            entry["source_path"] = path
        except ClientError as exc:
            entry = {"stack": stack, "inspection_error": exc.response["Error"]["Code"]}
        except (ValueError, yaml.YAMLError, OSError, TypeError) as exc:
            entry = {"stack": stack, "inspection_error": type(exc).__name__}
        result["stacks"][label] = entry
        if entry.get("structurally_equal") is not True:
            result["requires_review"] = True
    Path("aws-source-live-drift.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if result["requires_review"]:
        raise SystemExit("Source/live template differences or inspection gaps require review; no AWS changes made")


if __name__ == "__main__":
    main()
