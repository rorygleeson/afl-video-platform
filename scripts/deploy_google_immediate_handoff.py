#!/usr/bin/env python3
"""Guarded backend-only removal of historical Scene 2/3 paid submission waits.

CloudFormation changes Google Step Functions ASL only; Lambda, Google requests,
API, retry policies, permissions, and media processing remain untouched.
"""
import json
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACK = "afl-app-dev-google-video"
MACHINE = "afl-app-dev-google-omni"
WORKER = "afl-app-dev-google-omni-worker"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
OLD = (
    '"ResultPath":"$.claim","Next":"SubmissionSchedule"},\n'
    '            "SubmissionSchedule":{"Type":"Choice","Choices":[{"Variable":"$.sceneId","StringEquals":"scene2","Next":"WaitScene2"},{"Variable":"$.sceneId","StringEquals":"scene3","Next":"WaitScene3"}],"Default":"Submit"},\n'
    '            "WaitScene2":{"Type":"Wait","Seconds":10,"Next":"Submit"},\n'
    '            "WaitScene3":{"Type":"Wait","Seconds":20,"Next":"Submit"},'
)
NEW = '"ResultPath":"$.claim","Next":"Submit"},'


def immediate_definition(raw):
    before = json.loads(raw)
    states = before["States"]
    if states["Claim"].get("Next") != "SubmissionSchedule":
        raise ValueError("Unexpected Claim routing")
    if states["SubmissionSchedule"].get("Default") != "Submit":
        raise ValueError("Unexpected scheduling branch")
    if states["WaitScene2"] != {"Type": "Wait", "Seconds": 10, "Next": "Submit"}:
        raise ValueError("Unexpected Scene2 wait")
    if states["WaitScene3"] != {"Type": "Wait", "Seconds": 20, "Next": "Submit"}:
        raise ValueError("Unexpected Scene3 wait")
    after = json.loads(raw)
    after["States"]["Claim"]["Next"] = "Submit"
    for name in ("SubmissionSchedule", "WaitScene2", "WaitScene3"):
        del after["States"][name]
    # Other tasks, waits, paid submit, retry configuration, and timeouts stay identical.
    assert after["States"]["Submit"] == before["States"]["Submit"]
    assert after["States"]["PollVideo"] == before["States"]["PollVideo"]
    assert after["States"]["WaitForVideo"] == before["States"]["WaitForVideo"]
    assert "Retry" not in after["States"]["Submit"]
    assert after["TimeoutSeconds"] == before["TimeoutSeconds"]
    return after


def main():
    assert os.environ.get("DEPLOY_IMMEDIATE_HANDOFF") == "REMOVE_FIXED_SCENE_WAITS"
    session = boto3.Session(region_name=REGION)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    assert identity["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in identity["Arn"]
    cf, sf, lam = (session.client("cloudformation"),
                   session.client("stepfunctions"), session.client("lambda"))
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE"
    assert stack["RoleARN"] == ROLE
    params = {x["ParameterKey"]: x["ParameterValue"] for x in stack["Parameters"]}
    assert params["PaidVideoEnabled"] == "true"
    arn = cf.describe_stack_resource(
        StackName=STACK, LogicalResourceId="GoogleStateMachine"
    )["StackResourceDetail"]["PhysicalResourceId"]
    assert arn.endswith(":stateMachine:" + MACHINE)
    before = sf.describe_state_machine(stateMachineArn=arn)
    assert before["status"] == "ACTIVE"
    expected = immediate_definition(before["definition"])
    old_role = before["roleArn"]
    worker_before = lam.get_function_configuration(FunctionName=WORKER)
    assert worker_before["State"] == "Active"
    assert worker_before["LastUpdateStatus"] == "Successful"
    sha = worker_before["CodeSha256"]
    timeout = worker_before["Timeout"]

    original = cf.get_template(StackName=STACK, TemplateStage="Original")["TemplateBody"]
    assert isinstance(original, str)
    assert original.count(OLD) == 1, "Deployed CFN template differs from reviewed schedule"
    updated = original.replace(OLD, NEW, 1)
    repo = Path("infrastructure/app/google-video.yaml").read_text()
    assert NEW in repo and OLD not in repo
    assert '"WaitScene2"' not in repo and '"WaitScene3"' not in repo
    assert '"Submit":{"Type":"Task"' in repo
    assert '"WaitForVideo":{"Type":"Wait","Seconds":20' in repo
    name = "afl-immediate-handoff-" + os.environ["GITHUB_RUN_ID"]
    created = False
    executed = False
    try:
        cf.create_change_set(
            StackName=STACK, ChangeSetName=name, ChangeSetType="UPDATE",
            TemplateBody=updated,
            Parameters=[{"ParameterKey": k, "UsePreviousValue": True} for k in params],
            RoleARN=ROLE, Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Remove 10s and 20s paid-scene scheduling waits only",
        )
        created = True
        cf.get_waiter("change_set_create_complete").wait(
            StackName=STACK, ChangeSetName=name,
            WaiterConfig={"Delay": 5, "MaxAttempts": 36})
        changeset = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
        assert not changeset.get("NextToken")
        changes = [x["ResourceChange"] for x in changeset["Changes"]]
        assert len(changes) == 1, "Unrelated CloudFormation resources changed"
        change = changes[0]
        assert change["LogicalResourceId"] == "GoogleStateMachine"
        assert change["ResourceType"] == "AWS::StepFunctions::StateMachine"
        assert change["Action"] == "Modify"
        assert change.get("Replacement") in (False, "False")
        assert change.get("Details")
        for detail in change["Details"]:
            target = detail.get("Target") or {}
            assert target.get("Attribute") == "Properties"
            assert target.get("Name") == "DefinitionString"
            assert target.get("RequiresRecreation") == "Never"
        fresh = sf.describe_state_machine(stateMachineArn=arn)
        assert fresh["definition"] == before["definition"], "Workflow changed during preflight"
        assert fresh["roleArn"] == old_role
        assert lam.get_function_configuration(FunctionName=WORKER)["CodeSha256"] == sha
        cf.execute_change_set(StackName=STACK, ChangeSetName=name)
        executed = True
        cf.get_waiter("stack_update_complete").wait(
            StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
        after = sf.describe_state_machine(stateMachineArn=arn)
        assert after["status"] == "ACTIVE" and after["roleArn"] == old_role
        assert json.loads(after["definition"]) == expected
        final_worker = lam.get_function_configuration(FunctionName=WORKER)
        assert final_worker["CodeSha256"] == sha and final_worker["Timeout"] == timeout
        assert final_worker["LastUpdateStatus"] == "Successful"
        final_stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        assert final_stack["StackStatus"] == "UPDATE_COMPLETE"
        assert {x["ParameterKey"]: x["ParameterValue"]
                for x in final_stack["Parameters"]} == params
        print("IMMEDIATE_SCENE_HANDOFF_DEPLOYED_AND_VERIFIED", flush=True)
        print("NO_FIXED_WAIT_SCENE2_OR_SCENE3_NO_PAID_REQUEST_NO_WORKER_CHANGE", flush=True)
    finally:
        if created and not executed:
            try:
                cf.delete_change_set(StackName=STACK, ChangeSetName=name)
            except ClientError:
                pass


if __name__ == "__main__":
    main()
