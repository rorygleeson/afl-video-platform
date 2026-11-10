#!/usr/bin/env python3
"""Preview a backend rollback against saved pre-release state; NEVER execute.

Only use after an explicitly approved backend release. Requires exact saved
template and previous parameters. The change set is always deleted.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import boto3

from validate_change_set import validate
from preview_release_change_set import verify_reconcile_permission

REGION = "ap-southeast-2"
BACKEND = "afl-app-dev-backend"
ALLOWED = {"ApiLambda", "ImageWorkerLambda", "VideoWorkerLambda"}
FORBIDDEN = {"afl-app-dev-google-omni-worker"}


def prepare(manifest, live, template_path):
    if manifest.get("region") != REGION or manifest.get("rollback_executable") is not False:
        raise ValueError("Invalid rollback manifest")
    if manifest.get("deployment_executed") is not False:
        raise ValueError("Expected pre-release snapshot")
    entry = manifest["stacks"]["backend"]
    if entry["name"] != BACKEND or live["StackId"] != entry["stack_id"]:
        raise ValueError("Backend stack identity mismatch")
    if live["StackStatus"] != "UPDATE_COMPLETE":
        raise ValueError("Backend stack is not stable")
    template = Path(template_path)
    if hashlib.sha256(template.read_bytes()).hexdigest() != entry["template_sha256"]:
        raise ValueError("Saved backend template checksum mismatch")
    previous = entry["previous_parameters"]
    current = {p["ParameterKey"]: p.get("ParameterValue") for p in live.get("Parameters", [])}
    if set(previous) != set(current):
        raise ValueError("Backend parameter keys changed")
    changed = {k for k in previous if previous[k] != current[k]}
    if not changed or not changed <= {"LambdaArtifactKey", "ImageWorkerArtifactKey", "VideoWorkerArtifactKey"}:
        raise ValueError("Rollback requires only the three approved backend artifact parameter changes")
    return template.read_text(), [{"ParameterKey": k, "ParameterValue": v} for k, v in sorted(previous.items())]


def inspect(changes, verified_reconcile_permission=False):
    """Reuse the forward release's strict dependency and permission checks."""
    issues = validate(changes, ALLOWED, verified_reconcile_permission=verified_reconcile_permission)
    if issues:
        raise ValueError("Unsafe rollback: " + "; ".join(issues))
    return len(changes["Changes"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--create-preview", action="store_true")
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", type=Path, default=Path("rollback-preview.json"))
    args = p.parse_args()
    if not args.create_preview:
        raise ValueError("Explicit --create-preview required; no AWS calls made")
    if not args.run_id.isdigit():
        raise ValueError("run-id must be numeric")
    manifest = json.loads(args.manifest.read_text())
    entry = manifest["stacks"]["backend"]
    cf = boto3.client("cloudformation", region_name=REGION)
    live = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    template, params = prepare(manifest, live, entry["template_path"])
    name = "afl-rollback-preview-" + args.run_id
    created = False
    try:
        cf.create_change_set(
            StackName=BACKEND, ChangeSetName=name, ChangeSetType="UPDATE",
            TemplateBody=template, Parameters=params,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Rollback preview only. NEVER EXECUTE.",
        )
        created = True
        for _ in range(90):
            result = cf.describe_change_set(StackName=BACKEND, ChangeSetName=name)
            if result["Status"] in ("CREATE_COMPLETE", "FAILED"):
                break
            time.sleep(2)
        else:
            raise RuntimeError("Rollback preview timed out")
        changes = list(result.get("Changes", []))
        next_token = result.get("NextToken")
        while next_token:
            page = cf.describe_change_set(StackName=BACKEND, ChangeSetName=name, NextToken=next_token)
            if page.get("ChangeSetId") != result.get("ChangeSetId") or page.get("StackId") != result.get("StackId"):
                raise ValueError("Rollback change-set pagination identity changed")
            changes.extend(page.get("Changes", []))
            next_token = page.get("NextToken")
        result["Changes"] = changes
        lam = boto3.client("lambda", region_name=REGION)
        events = boto3.client("events", region_name=REGION)
        verified = verify_reconcile_permission(cf, lam, events, BACKEND, changes)
        count = inspect(result, verified_reconcile_permission=verified)
        args.output.write_text(json.dumps({
            "stack": BACKEND, "verdict": "SAFE_ROLLBACK_PREVIEW",
            "change_count": count, "executed": False,
            "changes": changes,
        }, indent=2, default=str) + "\n")
        print("SAFE_ROLLBACK_PREVIEW:", count, "resources; execution DISABLED")
    finally:
        if created:
            cf.delete_change_set(StackName=BACKEND, ChangeSetName=name)


if __name__ == "__main__":
    main()
