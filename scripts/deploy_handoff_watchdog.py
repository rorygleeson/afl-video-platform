#!/usr/bin/env python3
"""Migrate ONLY the isolated watchdog stack from idle cron to per-job monitoring.

Fail closed on unexpected CloudFormation changes. No application stack changes,
paid Google calls, or DynamoDB mutations.
"""
import base64
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import boto3

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACK = "afl-app-dev-handoff-watchdog"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
ARTIFACT_BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
EXISTING = {
    "WatchdogRole": "AWS::IAM::Role",
    "WatchdogLogs": "AWS::Logs::LogGroup",
    "WatchdogLambda": "AWS::Lambda::Function",
    "EveryMinute": "AWS::Events::Rule",
    "InvokeFromEventBridge": "AWS::Lambda::Permission",
}
NEW = {
    "WatchdogMachineRole": "AWS::IAM::Role",
    "WatchdogMachine": "AWS::StepFunctions::StateMachine",
}
ALLOWED = {**EXISTING, **NEW}


def main():
    session = boto3.session.Session(region_name=REGION)
    ident = session.client("sts").get_caller_identity()
    assert ident["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in ident["Arn"]

    cf = session.client("cloudformation")
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "CREATE_COMPLETE"
    assert stack["RoleARN"] == ROLE
    for name, kind in EXISTING.items():
        item = cf.describe_stack_resource(
            StackName=STACK, LogicalResourceId=name)["StackResourceDetail"]
        assert item["ResourceType"] == kind
        assert item["ResourceStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")

    src = Path("src/handoff_watchdog/handler.py").read_bytes()
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("handler.py", src)
    body = payload.getvalue()
    checksum = hashlib.sha256(body).digest()
    key = "releases/handoff-watchdog/" + hashlib.sha256(body).hexdigest() + ".zip"
    s3 = session.client("s3")
    s3.put_object(Bucket=ARTIFACT_BUCKET, Key=key, Body=body,
                  ServerSideEncryption="AES256", ContentType="application/zip")
    assert s3.get_object(Bucket=ARTIFACT_BUCKET, Key=key)["Body"].read() == body
    print("ISOLATED_WATCHDOG_PACKAGE_VERIFIED", flush=True)

    template = Path("infrastructure/app/handoff-watchdog.yaml").read_text()
    assert "ScheduleExpression:" not in template
    assert "status:" in template and "- RUNNING" in template
    assert 'StateMachineType: STANDARD' in template
    assert '"Seconds": 60' in template
    assert "ReservedConcurrentExecutions: 1" in template
    assert "dynamodb:Scan" not in template
    assert "dynamodb:GetItem" in template
    assert "afl-app-dev-handoff-watchdog-image-start" in template
    assert "afl-app-dev-handoff-watchdog-minute" not in template
    assert "StartExecution" in template  # To launch ONLY the watchdog workflow.
    assert "stateMachine:afl-app-dev-handoff-watchdog" in template

    # Never reset the activation cutoff, even on redeployment.
    parameters = [{"ParameterKey": "ArtifactKey", "ParameterValue": key}]
    parameters += [{"ParameterKey": p, "UsePreviousValue": True}
                   for p in ("ArtifactBucket", "PermissionsBoundaryArn", "ActivatedAt")]
    name = "per-job-watchdog-" + os.environ["GITHUB_RUN_ID"]
    cf.create_change_set(
        StackName=STACK, ChangeSetName=name, ChangeSetType="UPDATE",
        TemplateBody=template, RoleARN=ROLE, Parameters=parameters,
        Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Only isolated watchdog update; remove idle timer and add per-job wait-loop")
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK, ChangeSetName=name,
        WaiterConfig={"Delay": 5, "MaxAttempts": 36})
    desc = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
    assert not desc.get("NextToken"), "Unreviewed paged changeset"
    changes = {x["ResourceChange"]["LogicalResourceId"]: x["ResourceChange"]
               for x in desc["Changes"]}
    print("ISOLATED_WATCHDOG_CHANGESET", json.dumps(
        {n: {"type": v["ResourceType"], "action": v["Action"],
             "replacement": v.get("Replacement"),
             "details": [{"name": (x.get("Target") or {}).get("Name"),
                          "attribute": (x.get("Target") or {}).get("Attribute"),
                          "evaluation": x.get("Evaluation")}
                         for x in v.get("Details", [])]}
         for n, v in changes.items()}, sort_keys=True), flush=True)
    assert changes, "Empty changeset; require explicit verification"
    assert set(changes).issubset(set(ALLOWED)), "Unexpected non-watchdog resource"
    assert set(NEW).issubset(changes), "Missing two new per-job SFN resources"
    assert {"WatchdogRole", "WatchdogLambda", "EveryMinute", "InvokeFromEventBridge"}.issubset(changes)
    assert "WatchdogLogs" not in changes, "Existing log retention must be unchanged"
    for logical, change in changes.items():
        assert change["ResourceType"] == ALLOWED[logical]
        assert change["Action"] == ("Add" if logical in NEW else "Modify")
        # Rule name/schedule replacement and LambdaPermission SourceArn changes
        # may replace ONLY those isolated resources.
        if logical not in ("EveryMinute", "InvokeFromEventBridge"):
            assert change.get("Replacement") in (None, False, "False"), (
                "Unexpected replacement: " + logical)
        # IAM, Lambda and state machine roles are never silently replaced.
        if logical == "WatchdogRole":
            assert change.get("Replacement") in (None, False, "False")
        if logical == "WatchdogLambda":
            assert change.get("Replacement") in (None, False, "False")
        if logical in NEW:
            assert change.get("Replacement") in (None, False, "False")
    print("ISOLATED_WATCHDOG_REVIEW_APPROVED", sorted(changes), flush=True)

    cf.execute_change_set(StackName=STACK, ChangeSetName=name)
    cf.get_waiter("stack_update_complete").wait(
        StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
    after = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert after["StackStatus"] == "UPDATE_COMPLETE"
    for logical, kind in ALLOWED.items():
        item = cf.describe_stack_resource(StackName=STACK,
                                          LogicalResourceId=logical)["StackResourceDetail"]
        assert item["ResourceType"] == kind
        assert item["ResourceStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")

    lam = session.client("lambda").get_function_configuration(
        FunctionName="afl-app-dev-handoff-watchdog")
    assert lam["LastUpdateStatus"] == "Successful"
    assert lam["CodeSha256"] == base64.b64encode(checksum).decode()
    assert "WATCHDOG_ACTIVATED_AT" in lam["Environment"]["Variables"]
    assert lam["Environment"]["Variables"]["WATCHDOG_MACHINE_ARN"].endswith(
        ":stateMachine:afl-app-dev-handoff-watchdog")
    sf = session.client("stepfunctions")
    state = sf.describe_state_machine(
        stateMachineArn=lam["Environment"]["Variables"]["WATCHDOG_MACHINE_ARN"])
    assert state["status"] == "ACTIVE"
    assert state["type"] == "STANDARD"
    rule = session.client("events").describe_rule(
        Name="afl-app-dev-handoff-watchdog-image-start")
    assert rule["State"] == "ENABLED" and "RUNNING" in rule["EventPattern"]
    assert not rule.get("ScheduleExpression"), "Old idle schedule still active"
    print("PER_JOB_WATCHDOG_DEPLOYED_VERIFIED", flush=True)
    print("NO_IDLE_SCHEDULE_VERIFIED", flush=True)


if __name__ == "__main__":
    main()
