#!/usr/bin/env python3
"""Deploy Omni dual-image worker through the existing CloudFormation execution role.

GitHub role has no lambda:GetFunction or direct update rights. Validate the
change set before applying. Preserve dependencies from the managed artifact.
Never submit a paid Google request.
"""
import base64
import hashlib
import io
import os
from pathlib import Path
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
STACK = "afl-app-dev-google-video"
BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
PARAM = "GoogleWorkerArtifactKey"


def digest(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def main():
    session = boto3.session.Session(region_name=REGION)
    cf, s3, sts = (session.client(name) for name in ("cloudformation", "s3", "sts"))
    identity = sts.get_caller_identity()
    assert identity["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in identity["Arn"]

    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE"
    assert stack["RoleARN"] == ROLE
    previous = {p["ParameterKey"]:p["ParameterValue"] for p in stack["Parameters"]}
    assert previous["ArtifactBucketName"] == BUCKET
    assert previous["PaidVideoEnabled"] == "true"
    assert previous[PARAM]
    original = s3.get_object(Bucket=BUCKET, Key=previous[PARAM])["Body"].read()
    assert len(original) < 55 * 1024 * 1024

    source = Path("src/google_video_worker/handler.py").read_bytes()
    assert b"def load_selfie(item):" in source
    assert b"OMNI_SELFIE_REFERENCE_ENABLED" in source
    assert b'"task": "image_to_video"' in source
    compile(source, "handler.py", "exec")

    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as old:
        assert old.testzip() is None
        assert old.namelist().count("handler.py") == 1
        assert old.namelist().count("video_prompts.py") == 1
        with zipfile.ZipFile(output, "w") as new:
            for member in old.infolist():
                new.writestr(member, source if member.filename == "handler.py"
                             else old.read(member.filename))
        prior_prompts = old.read("video_prompts.py")
        member_names = set(old.namelist())
    packed = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(packed)) as verify:
        assert verify.testzip() is None
        assert set(verify.namelist()) == member_names
        assert verify.read("handler.py") == source
        assert verify.read("video_prompts.py") == prior_prompts

    key = "releases/omni-selfie-reference/" + hashlib.sha256(packed).hexdigest() + "/google.zip"
    try:
        existing = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        assert existing == packed
    except ClientError as error:
        assert error.response["Error"]["Code"] in ("NoSuchKey", "NotFound", "404")
        s3.put_object(Bucket=BUCKET, Key=key, Body=packed)
        assert s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == packed
    print("IMMUTABLE_GOOGLE_ARTIFACT_VERIFIED", digest(packed), flush=True)

    template = Path("infrastructure/app/google-video.yaml").read_text()
    assert "Sid: OriginalSelfieReadOnly" in template
    assert "/uploads/*" in template
    assert "OMNI_SELFIE_REFERENCE_ENABLED: 'true'" in template
    assert "Timeout: 300" in template
    assert "GoogleWorkerArtifactKey" in template
    run = os.environ["GITHUB_RUN_ID"]
    changeset = "omni-selfie-" + run
    args = [
        {"ParameterKey": name,
         **({"ParameterValue": key} if name == PARAM else {"UsePreviousValue": True})}
        for name in previous
    ]
    cf.create_change_set(
        StackName=STACK, ChangeSetName=changeset, ChangeSetType="UPDATE",
        TemplateBody=template, Parameters=args, RoleARN=ROLE,
        Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Omni code, exact upload-key read-only permission, selfie flag only",
    )
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK, ChangeSetName=changeset,
        WaiterConfig={"Delay":5, "MaxAttempts":48})
    change = cf.describe_change_set(StackName=STACK, ChangeSetName=changeset)
    assert not change.get("NextToken")
    resources = [x["ResourceChange"] for x in change["Changes"]]
    assert resources
    permitted = {"GoogleWorkerLambda", "GoogleWorkerRole",
                 "GoogleStateMachine", "GoogleStateMachineRole",
                 "DeveloperPreflightInvokePermission"}
    touched = {item["LogicalResourceId"] for item in resources}
    assert "GoogleWorkerLambda" in touched
    assert "GoogleWorkerRole" in touched
    assert touched <= permitted, f"Unexpected resources: {touched}"
    for item in resources:
        print("REVIEW", item["LogicalResourceId"], item["Action"],
              item.get("Replacement"), item.get("Details"), flush=True)
        assert item["Action"] == "Modify"
        assert item.get("Replacement") in (False, "False", "Conditional")
        if item["LogicalResourceId"] == "GoogleWorkerLambda":
            names = {d.get("Target", {}).get("Name") for d in item.get("Details", [])
                     if d.get("Target", {}).get("Attribute") == "Properties"}
            assert names <= {"Code", "Environment", "Role"}
        elif item["LogicalResourceId"] == "GoogleWorkerRole":
            names = {d.get("Target", {}).get("Name") for d in item.get("Details", [])
                     if d.get("Target", {}).get("Attribute") == "Properties"}
            assert names <= {"Policies"}
        else:
            assert all(d.get("Evaluation") == "Dynamic" for d in item.get("Details", []))
    print("SAFE_CFN_CHANGESET_APPROVED", sorted(touched), flush=True)
    cf.execute_change_set(StackName=STACK, ChangeSetName=changeset)
    cf.get_waiter("stack_update_complete").wait(
        StackName=STACK, WaiterConfig={"Delay":10, "MaxAttempts":60})
    updated = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert updated["StackStatus"] == "UPDATE_COMPLETE"
    params = {p["ParameterKey"]:p["ParameterValue"] for p in updated["Parameters"]}
    assert params[PARAM] == key
    assert all(params[k] == v for k,v in previous.items() if k != PARAM)
    print("AWS_CLOUDFORMATION_OMNI_SELFIE_DEPLOYED_VERIFIED", flush=True)
    print("No paid Google requests; no S3 video prompts/templates touched.", flush=True)


if __name__ == "__main__":
    main()
