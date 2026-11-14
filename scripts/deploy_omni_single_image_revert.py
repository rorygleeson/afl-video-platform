#!/usr/bin/env python3
"""Revert Omni to ONE Banana starting image via guarded CloudFormation.

Repackages only the existing worker handler from the deployed S3 artifact,
retains dependencies and Step Functions definition, and removes the abandoned
selfie environment variable and IAM permission. Never calls Google.
"""
import base64
import hashlib
import io
import os
from pathlib import Path
import zipfile
import boto3
from botocore.exceptions import ClientError

ACCOUNT = "134387458447"
REGION = "ap-southeast-2"
BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
STACK = "afl-app-dev-google-video"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
PARAM = "GoogleWorkerArtifactKey"


def digest(value):
    return base64.b64encode(hashlib.sha256(value).digest()).decode("ascii")


def main():
    session = boto3.session.Session(region_name=REGION)
    cf, s3, sts, sf = (session.client(k) for k in ("cloudformation","s3","sts","stepfunctions"))
    caller = sts.get_caller_identity()
    assert caller["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in caller["Arn"]
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE" and stack["RoleARN"] == ROLE
    params = {x["ParameterKey"]: x["ParameterValue"] for x in stack["Parameters"]}
    assert params["PaidVideoEnabled"] == "true" and params["ArtifactBucketName"] == BUCKET
    source = Path("src/google_video_worker/handler.py").read_bytes()
    assert b"OMNI_SELFIE_REFERENCE_ENABLED" not in source
    assert b"def load_selfie(" not in source
    assert b'"task": "image_to_video"' in source
    compile(source, "handler.py", "exec")

    old_zip = s3.get_object(Bucket=BUCKET, Key=params[PARAM])["Body"].read()
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(old_zip)) as old:
        assert old.testzip() is None
        files = old.namelist()
        assert files.count("handler.py") == 1 and files.count("video_prompts.py") == 1
        original_prompts = old.read("video_prompts.py")
        with zipfile.ZipFile(buf, "w") as new:
            for item in old.infolist():
                new.writestr(item, source if item.filename == "handler.py" else old.read(item.filename))
    new_zip = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(new_zip)) as z:
        assert z.testzip() is None
        assert z.namelist() == files
        assert z.read("handler.py") == source and z.read("video_prompts.py") == original_prompts
    key = "releases/omni-single-image/" + hashlib.sha256(new_zip).hexdigest() + "/google.zip"
    try:
        assert s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == new_zip
    except ClientError as error:
        assert error.response["Error"]["Code"] in ("NoSuchKey","NotFound","404")
        s3.put_object(Bucket=BUCKET, Key=key, Body=new_zip)
        assert s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == new_zip

    template = Path("infrastructure/app/google-video.yaml").read_text()
    assert "OMNI_SELFIE_REFERENCE_ENABLED" not in template
    assert "OriginalSelfieReadOnly" not in template
    assert template.count('"requestId.$":"$$.Execution.Name"') == 4

    machine_arn = cf.describe_stack_resource(StackName=STACK, LogicalResourceId="GoogleStateMachine")["StackResourceDetail"]["PhysicalResourceId"]
    machine = sf.describe_state_machine(stateMachineArn=machine_arn)
    name = "omni-restore-single-image-" + os.environ["GITHUB_RUN_ID"]
    cf.create_change_set(
        StackName=STACK, ChangeSetName=name, ChangeSetType="UPDATE",
        TemplateBody=template,
        Parameters=[{"ParameterKey": k, **({"ParameterValue": key} if k == PARAM else {"UsePreviousValue": True})} for k in params],
        RoleARN=ROLE, Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Revert Google Omni video to one starting image; no Google requests",
    )
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK, ChangeSetName=name, WaiterConfig={"Delay":5,"MaxAttempts":48})
    plan = cf.describe_change_set(StackName=STACK,ChangeSetName=name)
    assert not plan.get("NextToken")
    changes = [x["ResourceChange"] for x in plan["Changes"]]
    touched = {x["LogicalResourceId"] for x in changes}
    permitted = {"GoogleWorkerLambda","GoogleWorkerRole","GoogleStateMachine",
                 "GoogleStateMachineRole","DeveloperPreflightInvokePermission"}
    assert {"GoogleWorkerLambda","GoogleWorkerRole"} <= touched
    assert touched <= permitted, f"Unexpected changes: {touched}"
    for x in changes:
        assert x["Action"] == "Modify" and x.get("Replacement") in (False,"False","Conditional")
        logical = x["LogicalResourceId"]
        if logical == "GoogleWorkerLambda":
            prop = {d.get("Target",{}).get("Name") for d in x.get("Details",[])
                    if d.get("Target",{}).get("Attribute") == "Properties"}
            assert prop <= {"Code","Environment","Role"}, prop
        elif logical == "GoogleWorkerRole":
            prop = {d.get("Target",{}).get("Name") for d in x.get("Details",[])
                    if d.get("Target",{}).get("Attribute") == "Properties"}
            assert prop <= {"Policies"}, prop
        else:
            assert all(d.get("Evaluation") == "Dynamic" for d in x.get("Details",[])), logical
        print("REVIEW",logical,x.get("Details"),flush=True)
    print("SINGLE_IMAGE_CHANGESET_VALIDATED",sorted(touched),flush=True)
    cf.execute_change_set(StackName=STACK,ChangeSetName=name)
    cf.get_waiter("stack_update_complete").wait(
        StackName=STACK,WaiterConfig={"Delay":10,"MaxAttempts":60})
    after = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    final_params={x["ParameterKey"]:x["ParameterValue"] for x in after["Parameters"]}
    assert after["StackStatus"]=="UPDATE_COMPLETE"
    assert final_params[PARAM]==key
    assert all(final_params[k]==v for k,v in params.items() if k!=PARAM)
    final_machine=sf.describe_state_machine(stateMachineArn=machine_arn)
    assert final_machine["definition"]==machine["definition"], "Step Functions changed"
    assert final_machine["roleArn"]==machine["roleArn"]
    print("OMNI_SINGLE_IMAGE_REVERT_DEPLOYED_VERIFIED",digest(new_zip),flush=True)
    print("No Google paid calls and no image or video prompts modified.",flush=True)


if __name__=="__main__":
    main()
