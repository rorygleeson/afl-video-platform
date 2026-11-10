#!/usr/bin/env python3
"""Explicitly authorized backend rollback to an exact saved pre-release snapshot.

Only the backend stack can be changed. This script validates the current stack
parameters against the *approved post-release plan*, creates and inspects a
CloudFormation rollback change set, and executes that same change set only with
an explicit CLI confirmation. Never touches Google or assembly stacks.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import boto3

from preview_rollback_change_set import prepare, inspect, BACKEND, REGION
from preview_release_change_set import verify_reconcile_permission


def rollback(cf, lam, events, manifest, plan, confirm, run_id):
    if confirm != "ROLLBACK_BACKEND":
        raise ValueError("Explicit ROLLBACK_BACKEND confirmation required")
    if not run_id.isdigit():
        raise ValueError("Run ID must be numeric")
    if plan.get("deploy_permitted") is not False:
        raise ValueError("Unexpected release plan guard")
    original = manifest["stacks"]["backend"]
    proposed = plan["stacks"]["backend"]
    if original["name"] != BACKEND or proposed["name"] != BACKEND:
        raise ValueError("Unexpected backend identity")
    if proposed["current_parameters"] != original["previous_parameters"]:
        raise ValueError("Rollback snapshot and release plan differ")
    live = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    actual = {p["ParameterKey"]: p.get("ParameterValue") for p in live.get("Parameters", [])}
    if actual != proposed["proposed_parameters"]:
        raise ValueError("Live backend does not match the approved post-release parameters")
    template, parameters = prepare(manifest, live, original["template_path"])
    name = "afl-rollback-exec-" + run_id
    created = False
    executed = False
    try:
        cf.create_change_set(
            StackName=BACKEND, ChangeSetName=name, ChangeSetType="UPDATE",
            TemplateBody=template, Parameters=parameters,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Explicit approved backend rollback to saved pre-release snapshot",
        )
        created = True
        for _ in range(90):
            result = cf.describe_change_set(StackName=BACKEND, ChangeSetName=name)
            if result["Status"] in ("CREATE_COMPLETE", "FAILED"):
                break
            time.sleep(2)
        else:
            raise RuntimeError("Rollback change set timed out")
        changes = list(result.get("Changes", []))
        token = result.get("NextToken")
        while token:
            page = cf.describe_change_set(StackName=BACKEND, ChangeSetName=name, NextToken=token)
            if page.get("ChangeSetId") != result.get("ChangeSetId") or page.get("StackId") != result.get("StackId"):
                raise ValueError("Rollback pagination identity mismatch")
            changes.extend(page.get("Changes", []))
            token = page.get("NextToken")
        result["Changes"] = changes
        verified = verify_reconcile_permission(cf, lam, events, BACKEND, changes)
        inspect(result, verified_reconcile_permission=verified)
        latest = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
        if latest["StackId"] != live["StackId"] or latest["StackStatus"] != "UPDATE_COMPLETE":
            raise ValueError("Stack identity or status changed before rollback")
        if {p["ParameterKey"]: p.get("ParameterValue") for p in latest.get("Parameters", [])} != actual:
            raise ValueError("Stack parameters changed before rollback")
        cf.execute_change_set(ChangeSetName=result["ChangeSetId"], StackName=BACKEND)
        executed = True
        return result["ChangeSetId"]
    finally:
        if created and not executed:
            cf.delete_change_set(StackName=BACKEND, ChangeSetName=name)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--confirmation", required=True)
    args = p.parse_args()
    if args.confirmation != "ROLLBACK_BACKEND":
        raise SystemExit("BLOCKED: exact rollback confirmation required")
    manifest = json.loads(args.manifest.read_text())
    plan = json.loads(args.plan.read_text())
    cf = boto3.client("cloudformation", region_name=REGION)
    arn = rollback(cf, boto3.client("lambda", region_name=REGION),
                   boto3.client("events", region_name=REGION),
                   manifest, plan, args.confirmation, args.run_id)
    print("Rollback change set execution requested:", arn)
    cf.get_waiter("stack_update_complete").wait(StackName=BACKEND)
    latest = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    params = {p["ParameterKey"]: p.get("ParameterValue") for p in latest.get("Parameters", [])}
    if params != manifest["stacks"]["backend"]["previous_parameters"]:
        raise RuntimeError("Rollback completed but previous parameters not restored")
    print("PASS: backend rollback completed and previous parameters restored")


if __name__ == "__main__":
    main()
