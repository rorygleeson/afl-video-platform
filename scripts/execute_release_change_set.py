#!/usr/bin/env python3
"""Execute ONE already-created, validated backend change set.

This module is NOT wired to GitHub Actions. Requires explicit CLI opt-in.
The caller must retain the exact reviewed change set (preview workflow deletes
its previews). No new change set is created here. No Google/assembly changes.
"""
import argparse
import json
from pathlib import Path

import boto3

from validate_change_set import validate
from preview_release_change_set import verify_reconcile_permission

REGION = "ap-southeast-2"
BACKEND = "afl-app-dev-backend"
ALLOWED = {"ApiLambda", "ImageWorkerLambda", "VideoWorkerLambda"}


def preflight(cf, lam, events, baseline, plan, change_set_id, expected_change_set_id):
    if not change_set_id or change_set_id != expected_change_set_id:
        raise ValueError("Change set identity does not match explicit approval")
    if baseline.get("region") != REGION:
        raise ValueError("Unexpected region")
    if baseline.get("guardrails", {}).get("deploy_permitted") is not False:
        raise ValueError("Baseline safety guard changed")
    if plan.get("deploy_permitted") is not False:
        raise ValueError("Release plan safety guard changed")
    if baseline.get("verification_gaps"):
        raise ValueError("Unverified AWS baseline")
    if not baseline.get("known_drift", {}).get("google_worker"):
        raise ValueError("Expected protected Google drift not captured")
    previous = baseline["stacks"]["backend"]
    proposed = plan["stacks"]["backend"]
    if previous["name"] != BACKEND or proposed["name"] != BACKEND:
        raise ValueError("Unexpected stack")
    if proposed["current_parameters"] != previous["parameters"]:
        raise ValueError("Baseline/plan parameters disagree")
    live = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    if live["StackId"] != previous["stack_id"] or live["StackStatus"] != "UPDATE_COMPLETE":
        raise ValueError("Backend stack identity/status changed")
    live_params = {x["ParameterKey"]: x.get("ParameterValue") for x in live.get("Parameters", [])}
    if live_params != previous["parameters"]:
        raise ValueError("Backend parameters changed since capture")
    result = cf.describe_change_set(ChangeSetName=change_set_id, StackName=BACKEND)
    if result.get("ChangeSetId") != change_set_id:
        raise ValueError("CloudFormation returned a different change set")
    if result.get("StackId") != previous["stack_id"]:
        raise ValueError("Change set belongs to another stack")
    if result.get("ExecutionStatus") != "AVAILABLE":
        raise ValueError("Change set not available")
    if result.get("Status") != "CREATE_COMPLETE":
        raise ValueError("Change set not ready")
    expected_params = proposed["proposed_parameters"]
    actual_params = {x["ParameterKey"]: x.get("ParameterValue") for x in result.get("Parameters", [])}
    if actual_params != expected_params:
        raise ValueError("Change set parameters differ from approved plan")
    verified = verify_reconcile_permission(cf, lam, events, BACKEND, result.get("Changes", []))
    problems = validate(result, ALLOWED, verified_reconcile_permission=verified)
    if problems:
        raise ValueError("Unsafe change set: " + "; ".join(problems))
    return result


def execute(cf, change_set_id, approved_change_set_id, baseline, plan, lam, events, execute_confirmed=False):
    if not execute_confirmed:
        raise ValueError("Explicit execution confirmation required")
    result = preflight(cf, lam, events, baseline, plan, change_set_id, approved_change_set_id)
    cf.execute_change_set(ChangeSetName=result["ChangeSetId"], StackName=BACKEND)
    return result["ChangeSetId"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--change-set-id", required=True)
    p.add_argument("--approved-change-set-id", required=True)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    if not args.execute:
        raise SystemExit("BLOCKED: --execute required; no AWS calls made")
    cf = boto3.client("cloudformation", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    events = boto3.client("events", region_name=REGION)
    change_set = execute(
        cf, args.change_set_id, args.approved_change_set_id,
        json.loads(args.baseline.read_text()), json.loads(args.plan.read_text()),
        lam, events, execute_confirmed=True,
    )
    print("CloudFormation execution requested for approved change set:", change_set)
    print("IMPORTANT: deployment success and rollback are NOT verified by this command")


if __name__ == "__main__":
    main()
