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
events = boto3.client("events", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)

EXPECTED_MACHINES = {
    "afl-app-dev-google-omni": ("Claim", "Submit", "PollVideo", "Finalize"),
    "afl-app-dev-scene1-pilot": (),
    "afl-app-dev-assembly": (),
    "afl-app-dev-handoff-watchdog": (),
    "afl-app-dev-video": (),
}
EXPECTED_RULES = (
    "afl-app-dev-reconcile-images",
    "afl-app-dev-images-to-video",
    "afl-app-dev-google-video-completed",
    "afl-app-dev-handoff-watchdog-image-start",
)


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
        "state_machine_checks": {},
        "event_rule_checks": {},
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
    # Fetch definitions only to check structural invariants in memory; never emit
    # definitions, task payloads, execution inputs, job records or log payloads.
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

    names = {machine["name"] for machine in report["state_machines"]}
    for name, required_states in EXPECTED_MACHINES.items():
        result = {"present": name in names, "definition_checked": False}
        if name not in names:
            report["warnings"].append(f"MISSING state machine: {name}")
        else:
            arn = f"arn:aws:states:{REGION}:{report['account']}:stateMachine:{name}"
            response, err = attempt(lambda a=arn: sf.describe_state_machine(stateMachineArn=a))
            if err:
                result["inspection_error"] = err
                report["warnings"].append(f"state machine {name}: {err}")
            else:
                result["status"] = response.get("status")
                try:
                    definition = json.loads(response["definition"])
                    states = definition.get("States", {})
                    result["definition_checked"] = True
                    result["state_count"] = len(states)
                    result["required_states_present"] = all(s in states for s in required_states)
                    if name == "afl-app-dev-google-omni":
                        result["execution_name_idempotency"] = all(
                            states[s].get("Parameters", {}).get("Payload", {}).get("requestId.$")
                            == "$$.Execution.Name" for s in required_states if s in states
                        ) and result["required_states_present"]
                    if not result["required_states_present"] or result.get("execution_name_idempotency") is False:
                        report["warnings"].append(f"STATE MACHINE INVARIANT FAILED: {name}")
                except (ValueError, TypeError, KeyError):
                    result["definition_checked"] = False
                    report["warnings"].append(f"unreadable state machine definition: {name}")
                if result["status"] != "ACTIVE":
                    report["warnings"].append(f"state machine not ACTIVE: {name}")
        report["state_machine_checks"][name] = result

    for name in EXPECTED_RULES:
        result = {"inspected": False}
        rule, err = attempt(lambda n=name: events.describe_rule(Name=n))
        if err:
            result["inspection_error"] = err
            report["warnings"].append(f"event rule {name}: {err}")
        else:
            result["inspected"] = True
            result["state"] = rule.get("State")
            targets, target_error = attempt(lambda n=name: events.list_targets_by_rule(Rule=n))
            if target_error:
                result["targets_inspection_error"] = target_error
                report["warnings"].append(f"event targets {name}: {target_error}")
            else:
                result["target_count"] = len(targets.get("Targets", []))
                if result["target_count"] == 0:
                    report["warnings"].append(f"event rule has no targets: {name}")
            if result["state"] != "ENABLED":
                report["warnings"].append(f"event rule not ENABLED: {name}")
        report["event_rule_checks"][name] = result

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
            summary.write("\n### State machines\n\n| Name | Status | Definition check |\n|---|---|---|\n")
            for name, entry in report["state_machine_checks"].items():
                summary.write(f"| {name} | {entry.get('status', entry.get('inspection_error', 'MISSING'))} | {entry['definition_checked']} |\n")
            summary.write("\n### Event rules\n\n| Name | State | Targets |\n|---|---|---|\n")
            for name, entry in report["event_rule_checks"].items():
                summary.write(f"| {name} | {entry.get('state', entry.get('inspection_error', 'unknown'))} | {entry.get('target_count', 'unverified')} |\n")
            if report["warnings"]:
                summary.write("\n### Warnings\n\n" + "".join(f"- {warning}\n" for warning in report["warnings"]))
            summary.write("\n**No AWS writes or paid provider calls performed.**\n")
    # Fail closed for unknown/mismatched API and Google code, but preserve report.
    for critical in ("api", "google"):
        if report["functions"].get(critical, {}).get("artifact_comparison") != "MATCH":
            raise SystemExit(f"Inventory needs review: {critical} not verified against CloudFormation artifact")
    if report["warnings"]:
        raise SystemExit("Live AWS audit reported warnings; review sanitized inventory artifact")


if __name__ == "__main__":
    main()
