#!/usr/bin/env python3
"""Offline, fail-closed release plan for existing stacks. No AWS writes.

Uses a captured AWS baseline and deterministic release artifact manifest.
Refuses to plan if the Google worker has drift, until a separately reviewed
Google migration is designed. Does not silently update any CFN parameter.
"""
import argparse
import json
from pathlib import Path

STACKS = {
    "backend": ("afl-app-dev-backend", ("LambdaArtifactKey", "ImageWorkerArtifactKey", "VideoWorkerArtifactKey")),
    "google": ("afl-app-dev-google-video", ("GoogleWorkerArtifactKey",)),
    "assembly": ("afl-app-dev-assembly", ("AssemblyWorkerArtifactKey", "FfmpegLayerArtifactKey")),
    "frontend": ("afl-app-dev-frontend", ()),
}
ARTIFACTS = {
    ("backend", "LambdaArtifactKey"): "api",
    ("backend", "ImageWorkerArtifactKey"): "image-worker",
    ("backend", "VideoWorkerArtifactKey"): "legacy-video-worker",
    ("assembly", "AssemblyWorkerArtifactKey"): "assembly-worker",
}
def plan(baseline, artifacts):
    if baseline.get("region") != "ap-southeast-2":
        raise ValueError("Wrong AWS region")
    if baseline.get("guardrails", {}).get("deploy_permitted") is not False:
        raise ValueError("Unexpected baseline deployment guard")
    if baseline.get("known_drift", {}).get("google_worker") is not True:
        raise ValueError("Expected Google drift baseline changed; recapture and review")
    if not baseline.get("google_known_live_artifact"):
        raise ValueError("Verified live Google artifact missing")
    if artifacts.get("format") != 1:
        raise ValueError("Unsupported artifact manifest")
    result = {"schema": 1, "deploy_permitted": False, "stacks": {},
              "blockers": ["Google CloudFormation code drift requires separately reviewed reconciliation",
                           "FFmpeg layer immutable binary provenance is not pinned",
                           "No change sets have been created, reviewed or executed"]}
    for label, (name, required) in STACKS.items():
        current = baseline.get("stacks", {}).get(label)
        if not current or current.get("name") != name or current.get("status") not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise ValueError(f"Missing or unstable stack: {name}")
        params = current.get("parameters", {})
        missing = set(required) - set(params)
        if missing:
            raise ValueError(f"{name}: missing live parameters {sorted(missing)}")
        proposed = dict(params)
        for param in required:
            key = ARTIFACTS.get((label, param))
            if key:
                artifact = artifacts.get("artifacts", {}).get(key)
                if not artifact or not artifact.get("sha256_hex") or not artifact.get("s3_key"):
                    raise ValueError(f"Missing content-addressed artifact: {key}")
                proposed[param] = artifact["s3_key"]
        result["stacks"][label] = {"name": name, "current_parameters": params,
                                    "proposed_parameters": proposed,
                                    "changed_parameters": {k: {"before": params[k], "after": v}
                                        for k, v in proposed.items() if params.get(k) != v}}
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("release-plan.json"))
    args = parser.parse_args()
    result = plan(json.loads(args.baseline.read_text()), json.loads(args.artifacts.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Release plan written:", args.output)
    print("Deployment: BLOCKED (intentional).")
    for blocker in result["blockers"]:
        print("BLOCKER:", blocker)
if __name__ == "__main__":
    main()
