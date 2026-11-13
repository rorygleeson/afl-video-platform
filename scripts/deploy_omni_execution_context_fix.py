#!/usr/bin/env python3
"""Repair Step Functions execution-name JSONPaths in existing Omni video state machine.

No Lambda, API, IAM, S3 image or prompt updates and no billable video calls.
"""
import os
from pathlib import Path

import boto3

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACK = "afl-app-dev-google-video"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
CORRECT = '"requestId.$":"$$.Execution.Name"'
INCORRECT = '"requestId.$":"$.Execution.Name"'


def main():
    session = boto3.session.Session(region_name=REGION)
    cf = session.client("cloudformation")
    sf = session.client("stepfunctions")
    identity = session.client("sts").get_caller_identity()
    assert identity["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in identity["Arn"]
    before = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert before["StackStatus"] == "UPDATE_COMPLETE"
    assert before["RoleARN"] == ROLE
    parameters = {p["ParameterKey"]: p["ParameterValue"] for p in before["Parameters"]}
    assert parameters["PaidVideoEnabled"] == "true"
    template = Path("infrastructure/app/google-video.yaml").read_text()
    assert INCORRECT not in template, "Bad execution-name JSONPath remains"
    assert template.count(CORRECT) == 4, "All four task states must use the execution context"
    assert "OMNI_SELFIE_REFERENCE_ENABLED: 'true'" in template

    arn = cf.describe_stack_resource(
        StackName=STACK, LogicalResourceId="GoogleStateMachine"
    )["StackResourceDetail"]["PhysicalResourceId"]
    live = sf.describe_state_machine(stateMachineArn=arn)
    assert live["status"] == "ACTIVE"
    definition = live["definition"]
    assert definition.count(INCORRECT) == 2, "Live workflow differs from known failure"
    assert definition.count(CORRECT) == 2, "Unexpected live workflow context references"
    assert live["roleArn"], "Expected workflow execution role"

    name = "omni-execution-context-" + os.environ["GITHUB_RUN_ID"]
    cf.create_change_set(
        StackName=STACK, ChangeSetName=name, ChangeSetType="UPDATE",
        TemplateBody=template,
        Parameters=[{"ParameterKey": key, "UsePreviousValue": True} for key in parameters],
        RoleARN=ROLE, Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Fix only Step Functions Claim/PollVideo execution context",
    )
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK, ChangeSetName=name, WaiterConfig={"Delay":5,"MaxAttempts":48}
    )
    change = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
    assert not change.get("NextToken")
    items = [x["ResourceChange"] for x in change["Changes"]]
    assert items, "Empty change set"
    assert {x["LogicalResourceId"] for x in items} == {"GoogleStateMachine"}, (
        f"Unexpected resources: {items}"
    )
    only = items[0]
    assert only["Action"] == "Modify" and only.get("Replacement") in (False,"False")
    print("SAFE_STATE_MACHINE_ONLY_CHANGE", only.get("Details"), flush=True)
    cf.execute_change_set(StackName=STACK, ChangeSetName=name)
    cf.get_waiter("stack_update_complete").wait(
        StackName=STACK, WaiterConfig={"Delay":10,"MaxAttempts":60}
    )
    after = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert after["StackStatus"] == "UPDATE_COMPLETE"
    final = sf.describe_state_machine(stateMachineArn=arn)
    assert final["roleArn"] == live["roleArn"]
    assert final["definition"].count(INCORRECT) == 0
    assert final["definition"].count(CORRECT) == 4
    assert final["status"] == "ACTIVE"
    print("OMNI_EXECUTION_CONTEXT_FIXED_AND_VERIFIED", flush=True)
    print("No billable requests submitted; failed execution remains unchanged.", flush=True)


if __name__ == "__main__":
    main()
