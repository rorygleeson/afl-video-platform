#!/usr/bin/env python3
"""Deploy 0/10/20s Google video submission offsets via existing CloudFormation stack.

Only the Google Step Functions state machine definition changes. No model calls,
Lambda code changes, automatic retries, new paid submissions or UI changes.
"""
import json
import textwrap

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
STACK = "afl-app-dev-google-video"
ACCOUNT = "134387458447"
EXECUTION_ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
LAMBDA_NAME = "afl-app-dev-google-omni-worker"
MACHINE_NAME = "afl-app-dev-google-omni"

CLAIM_END = '"ResultPath":"$.claim","Next":"Submit"},'
STAGGER_CLAIM_END = '"ResultPath":"$.claim","Next":"SubmissionSchedule"},'
SUBMIT_HEAD = '            "Submit":{"Type":"Task"'
EXTRA_STATES = (
    '            "SubmissionSchedule":{"Type":"Choice","Choices":['
    '{"Variable":"$.sceneId","StringEquals":"scene2","Next":"WaitScene2"},'
    '{"Variable":"$.sceneId","StringEquals":"scene3","Next":"WaitScene3"}],'
    '"Default":"Submit"},\n'
    '            "WaitScene2":{"Type":"Wait","Seconds":10,"Next":"Submit"},\n'
    '            "WaitScene3":{"Type":"Wait","Seconds":20,"Next":"Submit"},\n'
)


def stagger_definition(definition):
    """Return only the expected ASL changes, retaining all existing task logic."""
    original = json.loads(definition)
    states = original["States"]
    if states["Claim"].get("Next") != "Submit":
        raise ValueError("Claim routing differs from baseline")
    for name in ("SubmissionSchedule", "WaitScene2", "WaitScene3"):
        if name in states:
            raise ValueError("Stagger states already exist")
    if any("Retry" in state for state in states.values()):
        raise ValueError("Unexpected automatic retry policy")
    for name in ("Submit", "PaidGate", "Claim", "PollVideo", "WaitForVideo"):
        if name not in states:
            raise ValueError(f"Missing protected paid-video state: {name}")
    updated = json.loads(definition)
    u = updated["States"]
    u["Claim"]["Next"] = "SubmissionSchedule"
    u["SubmissionSchedule"] = {
        "Type": "Choice", "Choices": [
            {"Variable": "$.sceneId", "StringEquals": "scene2", "Next": "WaitScene2"},
            {"Variable": "$.sceneId", "StringEquals": "scene3", "Next": "WaitScene3"},
        ], "Default": "Submit",
    }
    u["WaitScene2"] = {"Type": "Wait", "Seconds": 10, "Next": "Submit"}
    u["WaitScene3"] = {"Type": "Wait", "Seconds": 20, "Next": "Submit"}
    return updated


def stagger_template(template):
    """Modify the deployed CloudFormation template's one DefinitionString only."""
    if not isinstance(template, str):
        raise ValueError("Expected original YAML template text")
    if template.count(CLAIM_END) != 1 or template.count(SUBMIT_HEAD) != 1:
        raise ValueError("Unexpected deployed Step Functions template structure")
    updated = template.replace(CLAIM_END, STAGGER_CLAIM_END, 1)
    updated = updated.replace(SUBMIT_HEAD, EXTRA_STATES + SUBMIT_HEAD, 1)
    if updated == template:
        raise ValueError("No stagger modification")
    return updated


def main():
    session = boto3.session.Session(region_name=REGION)
    cf = session.client("cloudformation")
    lam = session.client("lambda")
    states = session.client("stepfunctions")
    sts = session.client("sts")

    identity = sts.get_caller_identity()
    assert identity["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in identity["Arn"]

    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE"
    assert stack["RoleARN"] == EXECUTION_ROLE
    params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    assert params.get("PaidVideoEnabled") == "true"

    worker = lam.get_function_configuration(FunctionName=LAMBDA_NAME)
    assert worker["State"] == "Active" and worker["LastUpdateStatus"] == "Successful"
    assert worker["Timeout"] == 300
    original_worker_sha = worker["CodeSha256"]

    resource = cf.describe_stack_resource(
        StackName=STACK, LogicalResourceId="GoogleStateMachine"
    )["StackResourceDetail"]
    machine_arn = resource["PhysicalResourceId"]
    assert machine_arn.endswith(":stateMachine:" + MACHINE_NAME)
    live = states.describe_state_machine(stateMachineArn=machine_arn)
    assert live["status"] == "ACTIVE"
    old_definition = live["definition"]
    new_definition = stagger_definition(old_definition)
    old_role = live["roleArn"]

    original_template = cf.get_template(StackName=STACK, TemplateStage="Original")["TemplateBody"]
    new_template = stagger_template(original_template)

    # The committed IaC must include exactly the same scheduling states.
    from pathlib import Path
    repo_template = Path("infrastructure/app/google-video.yaml").read_text()
    assert repo_template.count(STAGGER_CLAIM_END) == 1
    assert repo_template.count(EXTRA_STATES) == 1
    assert '"Retry":' not in repo_template

    name = "afl-google-stagger-10sec-" + __import__("os").environ["GITHUB_RUN_ID"]
    created = False
    executed = False
    try:
        cf.create_change_set(
            StackName=STACK, ChangeSetName=name, ChangeSetType="UPDATE",
            TemplateBody=new_template,
            Parameters=[{"ParameterKey": k, "UsePreviousValue": True} for k in params],
            RoleARN=EXECUTION_ROLE,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Stagger Google paid scene submissions 0/10/20s; no retries",
        )
        created = True
        cf.get_waiter("change_set_create_complete").wait(
            StackName=STACK, ChangeSetName=name, WaiterConfig={"Delay":5, "MaxAttempts":36})
        changeset = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
        assert not changeset.get("NextToken"), "Unexpected changeset pagination"
        changes = [c["ResourceChange"] for c in changeset["Changes"]]
        print("CHANGESET:", [(c["LogicalResourceId"], c["Action"], c.get("Replacement"))
                              for c in changes], flush=True)
        assert len(changes) == 1, "Changes outside Google State Machine"
        change = changes[0]
        assert change["LogicalResourceId"] == "GoogleStateMachine"
        assert change["ResourceType"] == "AWS::StepFunctions::StateMachine"
        assert change["Action"] == "Modify"
        assert change.get("Replacement") in (False, "False")
        assert change.get("Details"), "Unexplained CloudFormation change"
        for detail in change["Details"]:
            target = detail.get("Target", {})
            assert target.get("Attribute") == "Properties"
            assert target.get("Name") == "DefinitionString"
            assert target.get("RequiresRecreation") == "Never"
        # Re-read live state right before execution to catch concurrent changes.
        current = states.describe_state_machine(stateMachineArn=machine_arn)
        assert current["definition"] == old_definition
        assert current["roleArn"] == old_role
        assert lam.get_function_configuration(FunctionName=LAMBDA_NAME)["CodeSha256"] == original_worker_sha
        cf.execute_change_set(StackName=STACK, ChangeSetName=name)
        executed = True
        print("CFN_STAGGER_UPDATE_SUBMITTED", flush=True)
        cf.get_waiter("stack_update_complete").wait(
            StackName=STACK, WaiterConfig={"Delay":10, "MaxAttempts":60})
        after = states.describe_state_machine(stateMachineArn=machine_arn)
        assert after["status"] == "ACTIVE"
        assert after["roleArn"] == old_role
        assert json.loads(after["definition"]) == new_definition
        verify = lam.get_function_configuration(FunctionName=LAMBDA_NAME)
        assert verify["Timeout"] == 300 and verify["CodeSha256"] == original_worker_sha
        after_stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        assert after_stack["StackStatus"] == "UPDATE_COMPLETE"
        assert {p["ParameterKey"]:p["ParameterValue"] for p in after_stack["Parameters"]} == params
        print("DEPLOYED_AND_VERIFIED: Google paid submission staggering scene1=0s scene2=10s scene3=20s", flush=True)
        print("VERIFIED: same Lambda SHA, 300s timeout, no retries, same paid-generation logic", flush=True)
    finally:
        if created and not executed:
            try:
                cf.delete_change_set(StackName=STACK, ChangeSetName=name)
                print("Unused CloudFormation change set removed; stack unchanged", flush=True)
            except ClientError:
                pass


if __name__ == "__main__":
    main()
