#!/usr/bin/env python3
"""Guarded Google Lambda handler-only CFN preview/deploy through unified release.

Preview creates/deletes a CloudFormation change set; deploy requires a prior
successful, same-commit preview and executes only an independently rechecked
change set. Neither mode invokes Google or changes the state-machine definition.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import boto3
from botocore.exceptions import ClientError
from validate_change_set import google_dependency_only, google_preflight_permission_only

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACK = "afl-app-dev-google-video"
FUNCTION = "afl-app-dev-google-omni-worker"
PARAM = "GoogleWorkerArtifactKey"
WORKER = Path("src/google_video_worker/handler.py")
# Exact source bytes immediately before PR #90's five-line change. A live
# hotfix or other worker revision must NEVER be silently replaced by this release.
EXPECTED_PRIOR_HANDLER_SHA256 = "cc05259e11834e00589b2694194fd11214744dbf74c47c0049fff9ff02453337"
EXPECTED_PR90_HANDLER_SHA256 = "8a1024bf8a9788d2c4a910f7bb67bc770ac26ffd534aed44c89c6f0a08039f67"


def code_sha(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def replace_handler(previous, handler):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(previous)) as old:
        if old.testzip() is not None or old.namelist().count("handler.py") != 1:
            raise ValueError("Unexpected original worker ZIP layout")
        if hashlib.sha256(old.read("handler.py")).hexdigest() != EXPECTED_PRIOR_HANDLER_SHA256:
            raise ValueError("Live worker handler is NOT the known pre-PR90 version; refuse overwrite")
        names = old.namelist()
        with zipfile.ZipFile(output, "w") as new:
            for item in old.infolist():
                new.writestr(item, handler if item.filename == "handler.py" else old.read(item.filename))
    result = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as rebuilt:
        if rebuilt.testzip() is not None or rebuilt.namelist() != names or rebuilt.read("handler.py") != handler:
            raise ValueError("Repacked worker verification failed")
    return result


def validate_preflight_permission(lam, cf, worker_arn):
    """Confirm CFN's existing preflight permission is exactly the known scoped grant."""
    physical = cf.describe_stack_resource(StackName=STACK,
        LogicalResourceId="DeveloperPreflightInvokePermission")["StackResourceDetail"]["PhysicalResourceId"]
    policy = json.loads(lam.get_policy(FunctionName=FUNCTION)["Policy"])
    expected = f"arn:aws:iam::{ACCOUNT}:role/afl-video-github-deploy-role"
    matching = [s for s in policy.get("Statement", []) if s.get("Sid") == physical]
    if len(matching) != 1:
        raise ValueError("Preflight permission CFN statement is missing or ambiguous")
    statement = matching[0]
    if (statement.get("Effect") != "Allow"
        or statement.get("Action") != "lambda:InvokeFunction"
        or statement.get("Principal") != {"AWS": expected}
        or statement.get("Resource") != worker_arn
        or statement.get("Condition")):
        raise ValueError("Live preflight permission does not match expected scoped grant")
    return {"sid": physical, "statement": statement}


def validate_changes(changes, verified_preflight=False):
    """Reject IAM, state-machine, runtime, description and other modifications."""
    worker = 0
    descriptors = []
    for entry in changes:
        r = entry.get("ResourceChange", {})
        logical = r.get("LogicalResourceId")
        if google_preflight_permission_only(r) and verified_preflight:
            descriptors.append({"logical_id": logical, "action": "Modify",
                                "replacement": "Conditional", "property_names": ["FunctionName"],
                                "verified_live_preflight_permission": True})
            continue
        if r.get("Action") != "Modify" or r.get("Replacement") != "False":
            # Only log CloudFormation's structural metadata, never physical IDs,
            # parameter values, Lambda environment or other sensitive values.
            detail = [{"name": d.get("Target", {}).get("Name"),
                       "requires_recreation": d.get("Target", {}).get("RequiresRecreation"),
                       "evaluation": d.get("Evaluation"),
                       "change_source": d.get("ChangeSource"),
                       "causing_entity": d.get("CausingEntity")}
                      for d in r.get("Details", [])]
            raise ValueError("Blocked CloudFormation change: " + json.dumps({
                "logical_id": logical, "resource_type": r.get("ResourceType"),
                "action": r.get("Action"), "replacement": r.get("Replacement"),
                "details": detail}, sort_keys=True))
        if logical == "GoogleWorkerLambda":
            if r.get("ResourceType") != "AWS::Lambda::Function":
                raise ValueError("Unexpected worker resource type")
            details = r.get("Details", [])
            if not details or any(d.get("Target", {}).get("Attribute") != "Properties"
                                  or d.get("Target", {}).get("Name") != "Code"
                                  or d.get("Target", {}).get("RequiresRecreation") not in ("Never", None)
                                  for d in details):
                raise ValueError("Google worker changes must affect Code only")
            worker += 1
        elif not google_dependency_only(r):
            raise ValueError(f"Unexpected CloudFormation change to {logical}")
        # Only stable review attributes are exported; never full change-set details.
        descriptors.append({"logical_id": logical, "action": r.get("Action"),
                            "replacement": r.get("Replacement"),
                            "property_names": sorted(d.get("Target", {}).get("Name") for d in r.get("Details", []))})
    if worker != 1:
        raise ValueError("Exactly one GoogleWorkerLambda Code modification required")
    return sorted(descriptors, key=lambda d: d["logical_id"])


def review_prior_run(run_id, head_sha, candidate):
    if not run_id.isdigit() or not os.getenv("GH_TOKEN"):
        raise ValueError("Review run ID and GitHub token required")
    repo = os.environ["GITHUB_REPOSITORY"]
    run = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repo}/actions/runs/{run_id}"], text=True))
    if (run.get("event") != "workflow_dispatch" or run.get("conclusion") != "success"
            or run.get("head_branch") != "main" or run.get("head_sha") != head_sha
            or not str(run.get("path", "")).endswith(".github/workflows/release.yml")
            or run.get("inputs", {}).get("change_set_preview") != "google_worker_preview"):
        raise ValueError("Reviewed run is not a successful same-commit Google worker preview")
    with tempfile.TemporaryDirectory(prefix="reviewed-google-worker-") as td:
        subprocess.run(["gh", "run", "download", run_id, "--repo", repo,
                        "--name", f"google-worker-preview-{run_id}", "--dir", td], check=True)
        reviewed = json.loads((Path(td) / "google-worker-preview.json").read_text())
    if reviewed != candidate or reviewed.get("verdict") != "SAFE_PREVIEW":
        raise ValueError("Current artifact/change set differs from separately reviewed preview")


def inspect_changes(cf, change_set_name, verified_preflight=False):
    change_set = cf.describe_change_set(StackName=STACK, ChangeSetName=change_set_name)
    if change_set.get("Status") != "CREATE_COMPLETE":
        raise ValueError("CloudFormation preview did not complete")
    changes = list(change_set.get("Changes", []))
    token = change_set.get("NextToken")
    while token:
        page = cf.describe_change_set(StackName=STACK, ChangeSetName=change_set_name, NextToken=token)
        if page.get("ChangeSetId") != change_set.get("ChangeSetId"):
            raise ValueError("Paginated change-set identity changed")
        changes.extend(page.get("Changes", []))
        token = page.get("NextToken")
    return change_set, validate_changes(changes, verified_preflight=verified_preflight)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preview", "deploy"), required=True)
    parser.add_argument("--reviewed-preview-run-id", default="")
    args = parser.parse_args()
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise SystemExit("Only main branch may prepare/deploy Google worker")
    if args.mode == "deploy" and os.environ.get("DEPLOYMENT_CONFIRMATION") != "DEPLOY_GOOGLE_WORKER":
        raise SystemExit("Exact deployment confirmation required")
    session = boto3.session.Session(region_name=REGION)
    cf, lam, s3, sf, sts = (session.client(name) for name in
                            ("cloudformation", "lambda", "s3", "stepfunctions", "sts"))
    identity = sts.get_caller_identity()
    if identity["Account"] != ACCOUNT or ":assumed-role/afl-video-github-deploy-role/" not in identity["Arn"]:
        raise ValueError("Unexpected AWS deployment identity")
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    if stack["StackStatus"] != "UPDATE_COMPLETE":
        raise ValueError("Google stack is not stable")
    old_params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    bucket, current_key = old_params["ArtifactBucketName"], old_params[PARAM]
    live = lam.get_function_configuration(FunctionName=FUNCTION)
    if live["State"] != "Active" or live["LastUpdateStatus"] != "Successful":
        raise ValueError("Google worker not healthy")
    preflight = validate_preflight_permission(lam, cf, live["FunctionArn"])
    obj = s3.get_object(Bucket=bucket, Key=current_key)["Body"]
    try:
        previous = obj.read()
    finally:
        obj.close()
    if code_sha(previous) != live["CodeSha256"]:
        raise ValueError("Current Google Lambda differs from deployed CFN artifact")
    handler = WORKER.read_bytes()
    compile(handler, "handler.py", "exec")
    if hashlib.sha256(handler).hexdigest() != EXPECTED_PR90_HANDLER_SHA256:
        raise ValueError("Repository worker handler differs from approved PR90 target")
    if b'if request is not None and request != execution:' not in handler:
        raise ValueError("Missing first-attempt idempotency guard")
    package = replace_handler(previous, handler)
    proposed_sha = code_sha(package)
    if proposed_sha == live["CodeSha256"]:
        raise ValueError("Target code already deployed; no change needed")
    key = "releases/google-idempotency/" + hashlib.sha256(package).hexdigest() + "/google.zip"
    machine_arn = cf.describe_stack_resource(
        StackName=STACK, LogicalResourceId="GoogleStateMachine")["StackResourceDetail"]["PhysicalResourceId"]
    machine_before = sf.describe_state_machine(stateMachineArn=machine_arn)
    change_set_name = "afl-google-worker-" + os.environ["GITHUB_RUN_ID"]
    created, executed = False, False
    try:
        cf.create_change_set(StackName=STACK, ChangeSetName=change_set_name,
                             ChangeSetType="UPDATE", UsePreviousTemplate=True,
                             Capabilities=["CAPABILITY_NAMED_IAM"],
                             Parameters=[{"ParameterKey": name, **({"ParameterValue": key} if name == PARAM
                                         else {"UsePreviousValue": True})} for name in old_params])
        created = True
        cf.get_waiter("change_set_create_complete").wait(
            StackName=STACK, ChangeSetName=change_set_name,
            WaiterConfig={"Delay": 5, "MaxAttempts": 48})
        change_set, changes = inspect_changes(cf, change_set_name, verified_preflight=True)
        record = {"verdict": "SAFE_PREVIEW", "stack_id": stack["StackId"],
                  "previous_code_sha": live["CodeSha256"], "proposed_code_sha": proposed_sha,
                  "artifact_key": key, "changes": changes}
        Path("google-worker-preview.json").write_text(json.dumps(record, indent=2) + "\n")
        print("GOOGLE_WORKER_CODE_ONLY_PREVIEW_VERIFIED", json.dumps(changes), flush=True)
        if args.mode == "preview":
            print("PREVIEW ONLY: No CloudFormation execution or artifact upload.")
            return
        review_prior_run(args.reviewed_preview_run_id, os.environ["GITHUB_SHA"], record)
        # Recheck the named stack, parameters, code and state-machine definition
        # immediately before the single approved change set is executed.
        now = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        if now["StackId"] != stack["StackId"] or now["StackStatus"] != "UPDATE_COMPLETE":
            raise ValueError("Live Google stack changed during review")
        if {p["ParameterKey"]: p["ParameterValue"] for p in now["Parameters"]} != old_params:
            raise ValueError("Live Google parameters changed during review")
        if lam.get_function_configuration(FunctionName=FUNCTION)["CodeSha256"] != live["CodeSha256"]:
            raise ValueError("Google code changed during review")
        if validate_preflight_permission(lam, cf, live["FunctionArn"]) != preflight:
            raise ValueError("Live preflight Lambda permission changed during review")
        if sf.describe_state_machine(stateMachineArn=machine_arn)["definition"] != machine_before["definition"]:
            raise ValueError("Google state machine changed during review")
        try:
            existing = s3.get_object(Bucket=bucket, Key=key)["Body"]
            try:
                if existing.read() != package:
                    raise ValueError("Immutable artifact key collision")
            finally:
                existing.close()
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("NoSuchKey", "NotFound", "404"):
                raise
            s3.put_object(Bucket=bucket, Key=key, Body=package)
        cf.execute_change_set(StackName=STACK, ChangeSetName=change_set["ChangeSetId"])
        executed = True
        cf.get_waiter("stack_update_complete").wait(
            StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
        after = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        if after["StackStatus"] != "UPDATE_COMPLETE":
            raise ValueError("Google stack update incomplete")
        after_params = {p["ParameterKey"]: p["ParameterValue"] for p in after["Parameters"]}
        if after_params != {**old_params, PARAM: key}:
            raise ValueError("Unintended parameter mutation after Google release")
        current = lam.get_function_configuration(FunctionName=FUNCTION)
        if current["CodeSha256"] != proposed_sha or current["State"] != "Active" or current["LastUpdateStatus"] != "Successful":
            raise ValueError("Google worker post-release hash/health mismatch")
        machine_after = sf.describe_state_machine(stateMachineArn=machine_arn)
        if machine_after["definition"] != machine_before["definition"] or machine_after["roleArn"] != machine_before["roleArn"]:
            raise ValueError("Google state machine changed during worker-only release")
        validate_preflight_permission(lam, cf, current["FunctionArn"])
        print("GOOGLE_IDEMPOTENCY_WORKER_DEPLOYED_AND_VERIFIED", current["CodeSha256"])
    finally:
        if created and not executed:
            cf.delete_change_set(StackName=STACK, ChangeSetName=change_set_name)


if __name__ == "__main__":
    main()
