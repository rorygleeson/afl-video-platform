#!/usr/bin/env python3
"""Fail-closed, read-only rollback readiness inspection.

This does NOT execute rollback, create change sets, or authorize deployment.
It verifies that saved recovery templates match their manifest, current stack
identities are intact, and prior immutable artifact keys still exist.
"""
import argparse
import hashlib
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"


def check(manifest, cf, s3):
    problems = []
    if manifest.get("region") != REGION or manifest.get("rollback_executable") is not False:
        return ["Invalid rollback safety metadata"]
    if manifest.get("deployment_executed") is not False:
        return ["Rollback manifest is not a predeployment snapshot"]
    for label, entry in manifest.get("stacks", {}).items():
        try:
            path = Path(entry["template_path"])
            if not path.is_file():
                problems.append(f"{label}: saved template missing")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["template_sha256"]:
                problems.append(f"{label}: saved template hash mismatch")
            stack = cf.describe_stacks(StackName=entry["name"])["Stacks"][0]
            if stack["StackId"] != entry["stack_id"]:
                problems.append(f"{label}: stack identity changed")
            if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                problems.append(f"{label}: stack not stable")
            current = {p["ParameterKey"]: p.get("ParameterValue") for p in stack.get("Parameters", [])}
            if current != entry["previous_parameters"]:
                problems.append(f"{label}: stack parameters changed since capture")
            bucket = current.get("ArtifactBucketName")
            for key_name, key in current.items():
                if key_name.endswith("ArtifactKey") and key:
                    if not bucket:
                        problems.append(f"{label}: no artifact bucket for {key_name}")
                    else:
                        try:
                            s3.head_object(Bucket=bucket, Key=key)
                        except ClientError as exc:
                            problems.append(f"{label}: artifact unavailable for {key_name}: {exc.response['Error']['Code']}")
        except (KeyError, ValueError, ClientError) as exc:
            problems.append(f"{label}: inspection failed: {type(exc).__name__}")
    if not manifest.get("stacks"):
        problems.append("No rollback stacks recorded")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    problems = check(
        json.loads(args.manifest.read_text()),
        boto3.client("cloudformation", region_name=REGION),
        boto3.client("s3", region_name=REGION),
    )
    for problem in problems:
        print("BLOCKED:", problem)
    if problems:
        raise SystemExit(1)
    print("PASS: rollback snapshot templates, identities, parameters and artifacts verified")
    print("Rollback execution remains DISABLED; no changes were made")


if __name__ == "__main__":
    main()
