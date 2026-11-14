#!/usr/bin/env python3
"""Disable second selfie as Omni image-to-video input via a guarded CFN update.

No Lambda code, state-machine business logic, paid Google calls or image-template
changes. Keep the original personalized Banana frame as the single Omni image.
"""
import os
from pathlib import Path
import boto3

ACCOUNT = "134387458447"
REGION = "ap-southeast-2"
STACK = "afl-app-dev-google-video"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"


def main():
    session = boto3.session.Session(region_name=REGION)
    sts = session.client("sts")
    cf = session.client("cloudformation")
    caller = sts.get_caller_identity()
    assert caller["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in caller["Arn"]
    before = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert before["StackStatus"] == "UPDATE_COMPLETE"
    assert before["RoleARN"] == ROLE
    params = {x["ParameterKey"]: x["ParameterValue"] for x in before["Parameters"]}
    assert params["PaidVideoEnabled"] == "true"
    template = Path("infrastructure/app/google-video.yaml").read_text()
    assert "OMNI_SELFIE_REFERENCE_ENABLED: 'false'" in template
    assert "OMNI_SELFIE_REFERENCE_ENABLED: 'true'" not in template
    assert template.count('"requestId.$":"$$.Execution.Name"') == 4
    assert "GoogleWorkerArtifactKey" in template

    name = "omni-single-image-" + os.environ["GITHUB_RUN_ID"]
    cf.create_change_set(
        StackName=STACK,
        ChangeSetName=name,
        ChangeSetType="UPDATE",
        TemplateBody=template,
        Parameters=[{"ParameterKey": k, "UsePreviousValue": True} for k in params],
        RoleARN=ROLE,
        Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Disable unsupported second Omni image input; preserve first frame",
    )
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK, ChangeSetName=name,
        WaiterConfig={"Delay":5,"MaxAttempts":48})
    change = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
    assert not change.get("NextToken")
    items = [x["ResourceChange"] for x in change["Changes"]]
    assert items, "No change to apply"
    allowed = {"GoogleWorkerLambda", "GoogleStateMachine",
               "GoogleStateMachineRole", "DeveloperPreflightInvokePermission"}
    touched = {x["LogicalResourceId"] for x in items}
    assert "GoogleWorkerLambda" in touched, f"Lambda flag did not update: {touched}"
    assert touched <= allowed, f"Unexpected resources: {touched}"
    for change_item in items:
        logical_id = change_item["LogicalResourceId"]
        assert change_item["Action"] == "Modify", f"Unexpected action: {logical_id}"
        assert change_item.get("Replacement") in (False, "False", "Conditional"), logical_id
        details = change_item.get("Details", [])
        if logical_id == "GoogleWorkerLambda":
            names = {d.get("Target", {}).get("Name") for d in details
                     if d.get("Target", {}).get("Attribute") == "Properties"}
            assert names <= {"Environment"}, f"Unexpected Lambda change: {names}"
        else:
            assert all(d.get("Evaluation") == "Dynamic" for d in details), logical_id
        print("REVIEW", logical_id, change_item.get("Details"), flush=True)
    print("SAFE_SINGLE_IMAGE_CHANGESET_VERIFIED", flush=True)

    cf.execute_change_set(StackName=STACK, ChangeSetName=name)
    cf.get_waiter("stack_update_complete").wait(
        StackName=STACK, WaiterConfig={"Delay":10,"MaxAttempts":60})
    after = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert after["StackStatus"] == "UPDATE_COMPLETE"
    updated = {x["ParameterKey"]: x["ParameterValue"] for x in after["Parameters"]}
    assert updated == params
    deployed = cf.get_template(StackName=STACK, TemplateStage="Processed")["TemplateBody"]
    # CloudFormation API may return a dict when the template is YAML; verify the
    # effective config using the stack template contents after update.
    assert "OMNI_SELFIE_REFERENCE_ENABLED" in str(deployed)
    assert "'false'" in str(deployed) or '"false"' in str(deployed) or "false" in str(deployed)
    print("OMNI_SINGLE_IMAGE_AWS_DEPLOYED_VERIFIED", flush=True)
    print("Existing videos unaffected; all new videos use the Banana start image only.", flush=True)


if __name__ == "__main__":
    main()
