#!/usr/bin/env python3
"""Read-only live AWS release inventory. Never print environment variables or secrets."""
import base64
import hashlib
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
STACKS = {
    "backend": "afl-app-dev-backend",
    "google": "afl-app-dev-google-video",
    "assembly": "afl-app-dev-assembly",
    "frontend": "afl-app-dev-frontend",
    "mock_video": "afl-app-dev-video",
}
FUNCTIONS = {
    "api": ("backend", "afl-app-dev-api", "LambdaArtifactKey"),
    "image": ("backend", "afl-app-dev-image-worker", "ImageWorkerArtifactKey"),
    "legacy_video": ("backend", "afl-app-dev-video-worker", "VideoWorkerArtifactKey"),
    "google": ("google", "afl-app-dev-google-omni-worker", "GoogleWorkerArtifactKey"),
    "assembly": ("assembly", "afl-app-dev-assembly", "AssemblyWorkerArtifactKey"),
}
cf = boto3.client("cloudformation", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
sf = boto3.client("stepfunctions", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)


def attempt(action):
    try:
        return action(), None
    except (ClientError, PermissionError) as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else "PermissionError"
        return None, code


def main():
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "region": REGION,
        "account": sts.get_caller_identity()["Account"],
        "stacks": {},
        "functions": {},
        "state_machines": [],
        "warnings": [],
    }
    raw_stacks = {}
    for label, name in STACKS.items():
        response, err = attempt(lambda n=name: cf.describe_stacks(StackName=n))
        if err:
            report["stacks"][label] = {"name": name, "inspection_error": err}
            report["warnings"].append(f"stack {label}: {err}")
            continue
        stack = response["Stacks"][0]
        raw_stacks[label] = stack
        # No parameter values, outputs, tags or template bodies in the public Actions summary.
        report["stacks"][label] = {
            "name": name,
            "status": stack["StackStatus"],
            "last_updated": (stack.get("LastUpdatedTime") or stack["CreationTime"]).isoformat(),
            "parameters_present": sorted(p["ParameterKey"] for p in stack.get("Parameters", [])),
            "resource_count": None,
        }
        resources, resource_error = attempt(lambda n=name: cf.list_stack_resources(StackName=n))
        if resource_error:
            report["warnings"].append(f"stack resources {label}: {resource_error}")
        else:
            report["stacks"][label]["resource_count"] = len(resources["StackResourceSummaries"])
    for label, (owner, name, key_parameter) in FUNCTIONS.items():
        response, err = attempt(lambda n=name: lam.get_function_configuration(FunctionName=n))
        if err:
            report["functions"][label] = {"name": name, "inspection_error": err}
            report["warnings"].append(f"lambda {label}: {err}")
            continue
        entry = {
            "name": name,
            "owner_stack": STACKS[owner],
            "runtime": response.get("Runtime"),
            "architecture": response.get("Architectures", []),
            "state": response.get("State"),
            "update_status": response.get("LastUpdateStatus"),
            "last_modified": response.get("LastModified"),
            "live_code_sha256": response.get("CodeSha256"),
            "artifact_comparison": "unavailable",
        }
        stack = raw_stacks.get(owner)
        if stack:
            parameters = {p["ParameterKey"]: p["ParameterValue"] for p in stack.get("Parameters", [])}
            bucket, key = parameters.get("ArtifactBucketName"), parameters.get(key_parameter)
            if bucket and key:
                # Read only the referenced deployment package, never source/data buckets.
                obj, read_error = attempt(lambda b=bucket, k=key: s3.get_object(Bucket=b, Key=k))
                if read_error:
                    entry["artifact_comparison"] = "unavailable: " + read_error
                    report["warnings"].append(f"artifact {label}: {read_error}")
                else:
                    digest = hashlib.sha256()
                    body = obj["Body"]
                    try:
                        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                            digest.update(chunk)
                    finally:
                        body.close()
                    expected = base64.b64encode(digest.digest()).decode("ascii")
                    entry["artifact_comparison"] = "MATCH" if expected == entry["live_code_sha256"] else "DRIFT"
                    if entry["artifact_comparison"] == "DRIFT":
                        report["warnings"].append(f"CODE DRIFT: {label}")
            else:
                entry["artifact_comparison"] = "unavailable: artifact parameter missing"
        report["functions"][label] = entry
    # Metadata only. Never fetch definitions, execution inputs, job records or log payloads.
    token = None
    while True:
        args = {"maxResults": 100}
        if token:
            args["nextToken"] = token
        page, err = attempt(lambda a=args: sf.list_state_machines(**a))
        if err:
            report["warnings"].append("state machines: " + err)
            break
        report["state_machines"].extend(
            {"name": x["name"], "type": x["type"], "creation_date": x["creationDate"].isoformat()}
            for x in page["stateMachines"] if x["name"].startswith("afl-app-")
        )
        token = page.get("nextToken")
        if not token:
            break

    # Only sanitized inventory metadata and hashes are printed or uploaded.
    with open("aws-live-inventory.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Read-only AWS inventory\n\n")
            summary.write("| Component | State | Code/artifact |\n|---|---|---|\n")
            for label, entry in report["functions"].items():
                summary.write(f"| {label} | {entry.get('state', entry.get('inspection_error', 'unknown'))} | {entry.get('artifact_comparison', 'unknown')} |\n")
            summary.write("\n**No AWS writes or paid provider calls performed.**\n")
    # Fail closed for unknown/mismatched API and Google code, but preserve report.
    for critical in ("api", "google"):
        if report["functions"].get(critical, {}).get("artifact_comparison") != "MATCH":
            raise SystemExit(f"Inventory needs review: {critical} not verified against CloudFormation artifact")


if __name__ == "__main__":
    main()
