#!/usr/bin/env python3
"""Safely switch image Step Functions Map from parallel to sequential.

Only change MaxConcurrency 3->1 in the *deployed* backend CloudFormation
template. Keep worker, prompts, retries, failure transitions and front end.
Refuse active executions, resource changes other than ImageStateMachine, or
unrelated live definition differences. No Google/image/video requests.
"""
import json
import os
import re

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACK = "afl-app-dev-backend"
EXEC_ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"


def sequential_definition(before):
    updated = json.loads(json.dumps(before))
    states = updated["States"]
    assert states["Prepare"]["Next"] == "ParallelScenes", "Unexpected image entry"
    image_map = states["ParallelScenes"]
    assert image_map["Type"] == "Map"
    assert image_map["MaxConcurrency"] == 3, "Already sequential or unexpected concurrency"
    assert image_map["ItemsPath"] == "$.prepared.Payload.scenes"
    assert image_map["Next"] == "Finalize"
    inner = image_map["ItemProcessor"]["States"]
    assert inner["Generate"]["Next"] == "AssessImageResult"
    assert inner["AssessImageResult"]["Choices"][0]["Next"] == "WaitForGoogleCapacity"
    assert inner["WaitForGoogleCapacity"]["Next"] == "Generate"
    assert inner["Generate"]["Catch"][0]["Next"] == "MarkSceneFailed"
    assert inner["MarkSceneFailed"]["Type"] == "Task"
    image_map["MaxConcurrency"] = 1
    return updated


def main():
    sess = boto3.Session(region_name=REGION)
    ident = sess.client("sts").get_caller_identity()
    assert ident["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in ident["Arn"]
    cf = sess.client("cloudformation")
    sf = sess.client("stepfunctions")
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE", "Backend stack unstable"
    assert stack["RoleARN"] == EXEC_ROLE, "Unexpected CFN execution role"
    machine_arn = cf.describe_stack_resource(
        StackName=STACK, LogicalResourceId="ImageStateMachine"
    )["StackResourceDetail"]["PhysicalResourceId"]
    assert machine_arn == (
        f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:afl-app-dev-scene1-pilot"
    ), "Unexpected image state machine"
    live = sf.describe_state_machine(stateMachineArn=machine_arn)
    assert live["status"] == "ACTIVE"
    before = json.loads(live["definition"])
    if before["States"]["ParallelScenes"]["MaxConcurrency"] == 1:
        print("ALREADY_SEQUENTIAL: image workflow already has MaxConcurrency 1", flush=True)
        return
    expected = sequential_definition(before)
    running = sf.list_executions(
        stateMachineArn=machine_arn, statusFilter="RUNNING", maxResults=100
    )["executions"]
    assert not running, "Image workflow executions currently running; deployment blocked"
    print("NO_ACTIVE_IMAGE_EXECUTIONS", flush=True)

    original = cf.get_template(StackName=STACK, TemplateStage="Original")["TemplateBody"]
    assert isinstance(original, str), "Unexpected deployed template format"
    matches = list(re.finditer(r"(?m)^(\s*)MaxConcurrency:\s*3\s*$", original))
    assert len(matches) == 1, "Expected exactly one image concurrency setting"
    updated = original[:matches[0].start()] + matches[0].group(1) + (
        "MaxConcurrency: 1"
    ) + original[matches[0].end():]
    assert updated.replace("MaxConcurrency: 1", "MaxConcurrency: 3", 1) == original
    repo = open("infrastructure/app/backend.yaml", encoding="utf-8").read()
    assert "MaxConcurrency: 1" in repo and "MaxConcurrency: 3" not in repo
    print("SINGLE_TEMPLATE_SETTING_CHANGED: MaxConcurrency 3 -> 1", flush=True)

    change_name = "sequential-images-" + os.environ["GITHUB_RUN_ID"]
    created = False
    executed = False
    try:
        cf.create_change_set(
            StackName=STACK, ChangeSetName=change_name, ChangeSetType="UPDATE",
            TemplateBody=updated, RoleARN=EXEC_ROLE,
            Parameters=[{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
                        for p in stack["Parameters"]],
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Only image generation map concurrency 3 -> 1; preserve retries",
        )
        created = True
        cf.get_waiter("change_set_create_complete").wait(
            StackName=STACK, ChangeSetName=change_name,
            WaiterConfig={"Delay":5,"MaxAttempts":36},
        )
        summary = cf.describe_change_set(StackName=STACK, ChangeSetName=change_name)
        assert not summary.get("NextToken"), "Unexpected paginated change set"
        changes = [x["ResourceChange"] for x in summary.get("Changes",[])]
        print("CHANGESET_RESOURCES", [(c["LogicalResourceId"],c["Action"],
                                       c.get("Replacement")) for c in changes], flush=True)
        assert {c["LogicalResourceId"] for c in changes} == {"ImageStateMachine"}, (
            "Unexpected CloudFormation resource changes"
        )
        change = changes[0]
        assert change["ResourceType"] == "AWS::StepFunctions::StateMachine"
        assert change["Action"] == "Modify"
        assert change.get("Replacement") in ("False", False)
        assert all(x.get("Target",{}).get("Attribute") == "Properties"
                   and x.get("Target",{}).get("Name") in ("Definition", "DefinitionString")
                   for x in change.get("Details",[]))
        print("SAFE_IMAGE_CHANGESET_VERIFIED", flush=True)

        # Recheck runtime immediately before executing.
        now = sf.describe_state_machine(stateMachineArn=machine_arn)
        assert json.loads(now["definition"]) == before
        assert now["roleArn"] == live["roleArn"]
        assert not sf.list_executions(
            stateMachineArn=machine_arn,statusFilter="RUNNING",maxResults=100
        )["executions"], "New image execution running; abort deployment"
        cf.execute_change_set(StackName=STACK, ChangeSetName=change_name)
        executed = True
        print("CFN_SEQUENTIAL_IMAGES_UPDATE_SUBMITTED",flush=True)
        cf.get_waiter("stack_update_complete").wait(
            StackName=STACK, WaiterConfig={"Delay":10,"MaxAttempts":90}
        )
        after_stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        assert after_stack["StackId"] == stack["StackId"]
        assert after_stack["StackStatus"] == "UPDATE_COMPLETE"
        assert {p["ParameterKey"]:p.get("ParameterValue") for p in after_stack["Parameters"]} == {
            p["ParameterKey"]:p.get("ParameterValue") for p in stack["Parameters"]
        }
        after = sf.describe_state_machine(stateMachineArn=machine_arn)
        assert json.loads(after["definition"]) == expected, (
            "State machine changed beyond the concurrency setting"
        )
        assert after["roleArn"] == live["roleArn"]
        assert after["status"] == "ACTIVE"
        print("SEQUENTIAL_IMAGE_DEPLOYMENT_VERIFIED: concurrency=1; retries unchanged",
              flush=True)
        print("NO_GOOGLE_REQUESTS_MADE",flush=True)
    finally:
        if created and not executed:
            try:
                cf.delete_change_set(StackName=STACK,ChangeSetName=change_name)
                print("UNEXECUTED_CHANGESET_REMOVED", flush=True)
            except ClientError:
                pass


if __name__ == "__main__":
    main()
