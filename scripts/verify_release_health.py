#!/usr/bin/env python3
"""Read-only post-release health gate. No video generation or application writes.

Requires a freshly captured baseline and verifies the exact expected resource
identities, active Lambda state and code hashes, and Step Functions definitions.
This is a verification building block; it does not authorize deployment.
"""
import argparse
import hashlib
import json
from pathlib import Path

import boto3

REGION = "ap-southeast-2"


def verify(baseline, cf, lam, states):
    if baseline.get("region") != REGION:
        raise ValueError("Unexpected region")
    if baseline.get("guardrails", {}).get("deploy_permitted") is not False:
        raise ValueError("Baseline guardrail missing")
    failures = []
    for label, previous in baseline.get("stacks", {}).items():
        name = previous["name"]
        try:
            stack = cf.describe_stacks(StackName=name)["Stacks"][0]
            if stack["StackId"] != previous["stack_id"]:
                failures.append(f"{label}: stack identity changed")
            if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                failures.append(f"{label}: stack status {stack['StackStatus']}")
        except Exception as exc:
            failures.append(f"{label}: stack inspection failed: {type(exc).__name__}")
    for name, previous in baseline.get("lambdas", {}).items():
        try:
            current = lam.get_function_configuration(FunctionName=name)
            if current.get("State") != "Active" or current.get("LastUpdateStatus") != "Successful":
                failures.append(f"{name}: Lambda not ready")
            # Google worker is explicitly protected from silent code changes.
            if name == "afl-app-dev-google-omni-worker" and current.get("CodeSha256") != previous["sha256"]:
                failures.append(f"{name}: protected Google code hash changed")
        except Exception as exc:
            failures.append(f"{name}: Lambda inspection failed: {type(exc).__name__}")
    for previous in baseline.get("state_machines", []):
        if not previous.get("definition_verified"):
            failures.append(f"{previous['name']}: baseline definition unverified")
            continue
        try:
            current = states.describe_state_machine(stateMachineArn=previous["arn"])
            if current.get("status") != "ACTIVE":
                failures.append(f"{previous['name']}: state machine not active")
            if hashlib.sha256(current["definition"].encode()).hexdigest() != previous["definition_sha256"]:
                failures.append(f"{previous['name']}: definition changed (requires explicit approval)")
        except Exception as exc:
            failures.append(f"{previous['name']}: state machine inspection failed: {type(exc).__name__}")
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    failures = verify(
        baseline,
        boto3.client("cloudformation", region_name=REGION),
        boto3.client("lambda", region_name=REGION),
        boto3.client("stepfunctions", region_name=REGION),
    )
    for failure in failures:
        print("FAIL:", failure)
    if failures:
        raise SystemExit(1)
    print("PASS: read-only AWS resource health and protected-code checks")


if __name__ == "__main__":
    main()
