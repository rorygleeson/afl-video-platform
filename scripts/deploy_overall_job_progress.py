#!/usr/bin/env python3
"""One-time, narrowly scoped deployment of read-only overall job progress.

Uses an existing CloudFormation backend stack and its execution role.
No model, image, video, assembly, DynamoDB or Step Functions modifications.
Runs only from an explicitly marked push on the protected release workflow.
"""
import base64
import hashlib
import io
import os
from pathlib import Path
import zipfile

import boto3
from botocore.exceptions import ClientError
from validate_change_set import dependency_only

ACCOUNT = "134387458447"
REGION = "ap-southeast-2"
BACKEND = "afl-app-dev-backend"
FRONTEND = "afl-app-dev-frontend"
ARTIFACT_BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
CFN_ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"


def digest(blob):
    return base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")


def updated_api_zip(original, new_handler):
    """Preserve every deployed ZIP entry except handler.py."""
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as old:
        assert old.testzip() is None
        assert old.namelist().count("handler.py") == 1
        members = old.namelist()
        with zipfile.ZipFile(stream, "w") as new:
            for member in old.infolist():
                new.writestr(member, new_handler if member.filename == "handler.py"
                             else old.read(member.filename))
    result = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as verify:
        assert verify.testzip() is None
        assert verify.namelist() == members
        assert verify.read("handler.py") == new_handler
    return result


def assert_api_only_change_set(change_set):
    changes = change_set.get("Changes", [])
    assert changes and not change_set.get("NextToken"), "Missing or paginated change set"
    for item in changes:
        change = item["ResourceChange"]
        print("PREVIEW_CHANGE", change["LogicalResourceId"], change["Action"],
              change.get("Replacement"), flush=True)
        if change["LogicalResourceId"] == "ProxyAny":
            # Existing verified CloudFormation dependency: API Gateway
            # Integration dynamically references ApiLambda.Arn, with
            # RequiresRecreation=Never and no actual integration settings edit.
            assert dependency_only(change), (
                "Refusing any independent API Gateway integration update")
            continue
        assert change["LogicalResourceId"] == "ApiLambda", (
            "Refusing unrelated CloudFormation resource changes")
        assert change["ResourceType"] == "AWS::Lambda::Function"
        assert change["Action"] == "Modify"
        assert change.get("Replacement") in (False, "False")
        for detail in change.get("Details", []):
            target = detail.get("Target") or {}
            assert target.get("Attribute") == "Properties"
            assert target.get("Name") == "Code", (
                "Refusing changes to Lambda environment, role, permissions or other configuration")
    assert sum(c["ResourceChange"]["LogicalResourceId"] == "ApiLambda"
               for c in changes) == 1, "Expected exactly one API Lambda Code change"
    print("API_ONLY_CHANGESET_VALIDATED", flush=True)


def main():
    assert os.environ.get("DEPLOY_PROGRESS_CHANGE") == "DEPLOY_JOB_PROGRESS_ONLY"
    run_id = os.environ["GITHUB_RUN_ID"]
    session = boto3.Session(region_name=REGION)
    identity = session.client("sts").get_caller_identity()
    assert identity["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in identity["Arn"]

    cf = session.client("cloudformation")
    lam = session.client("lambda")
    s3 = session.client("s3")
    states = session.client("stepfunctions")
    stack = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE"
    assert stack["RoleARN"] == CFN_ROLE
    params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    assert params["ArtifactBucketName"] == ARTIFACT_BUCKET
    assert params["LambdaArtifactKey"]

    api_fn = cf.describe_stack_resource(StackName=BACKEND, LogicalResourceId="ApiLambda")[
        "StackResourceDetail"]["PhysicalResourceId"]
    assert api_fn == "afl-app-dev-api"
    before_api = lam.get_function_configuration(FunctionName=api_fn)
    assert before_api["State"] == "Active"
    assert before_api["LastUpdateStatus"] == "Successful"

    # Prove the CloudFormation-owned artifact matches the live function before updating.
    original = s3.get_object(Bucket=ARTIFACT_BUCKET,
                             Key=params["LambdaArtifactKey"])["Body"].read()
    assert digest(original) == before_api["CodeSha256"], (
        "Live API Lambda differs from CloudFormation artifact; abort")

    # Guard against accidental deployment of any worker or orchestration change.
    image_fn = cf.describe_stack_resource(StackName=BACKEND,
                                           LogicalResourceId="ImageWorkerLambda")[
                                               "StackResourceDetail"]["PhysicalResourceId"]
    image_before = lam.get_function_configuration(FunctionName=image_fn)["CodeSha256"]
    scenario_arn = (f"arn:aws:states:{REGION}:{ACCOUNT}:"
                    "stateMachine:afl-app-dev-scene1-pilot")
    scenario_before = states.describe_state_machine(stateMachineArn=scenario_arn)
    scenario_definition = scenario_before["definition"]
    scenario_role = scenario_before["roleArn"]

    handler = Path("src/api/handler.py").read_bytes()
    compile(handler, "handler.py", "exec")
    assert b'def overall_job_progress(job):' in handler
    assert b'"progress": overall_job_progress(job)' in handler
    candidate = updated_api_zip(original, handler)
    expected_sha = digest(candidate)
    new_key = (f"releases/overall-job-progress/"
               f"{hashlib.sha256(candidate).hexdigest()}/api.zip")
    deployed = expected_sha == before_api["CodeSha256"]
    name = None
    executed = False
    if not deployed:
        try:
            try:
                current = s3.get_object(Bucket=ARTIFACT_BUCKET, Key=new_key)["Body"].read()
                assert current == candidate, "Immutable release object conflict"
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ("NoSuchKey", "NotFound", "404"):
                    raise
                s3.put_object(Bucket=ARTIFACT_BUCKET, Key=new_key, Body=candidate)
                assert s3.get_object(Bucket=ARTIFACT_BUCKET, Key=new_key)["Body"].read() == candidate

            name = "overall-job-progress-" + run_id
            cf.create_change_set(
                StackName=BACKEND, ChangeSetName=name, ChangeSetType="UPDATE",
                UsePreviousTemplate=True, RoleARN=CFN_ROLE,
                Parameters=[
                    {"ParameterKey": p["ParameterKey"],
                     **({"ParameterValue": new_key}
                        if p["ParameterKey"] == "LambdaArtifactKey"
                        else {"UsePreviousValue": True})}
                    for p in stack["Parameters"]
                ],
                Capabilities=["CAPABILITY_NAMED_IAM"],
                Description="API-only read-only overall job progress; no generation changes",
            )
            cf.get_waiter("change_set_create_complete").wait(
                StackName=BACKEND, ChangeSetName=name,
                WaiterConfig={"Delay": 5, "MaxAttempts": 50})
            preview = cf.describe_change_set(StackName=BACKEND, ChangeSetName=name)
            assert_api_only_change_set(preview)
            assert lam.get_function_configuration(FunctionName=api_fn)["CodeSha256"] == before_api["CodeSha256"]
            assert lam.get_function_configuration(FunctionName=image_fn)["CodeSha256"] == image_before
            assert states.describe_state_machine(stateMachineArn=scenario_arn)["definition"] == scenario_definition

            cf.execute_change_set(StackName=BACKEND, ChangeSetName=name)
            executed = True
            cf.get_waiter("stack_update_complete").wait(
                StackName=BACKEND, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
        finally:
            if name and not executed:
                try:
                    cf.delete_change_set(StackName=BACKEND, ChangeSetName=name)
                except ClientError:
                    pass
    else:
        print("API_ALREADY_DEPLOYED", flush=True)

    after_stack = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    assert after_stack["StackStatus"] == "UPDATE_COMPLETE"
    after_params = {p["ParameterKey"]: p["ParameterValue"]
                    for p in after_stack["Parameters"]}
    expected_params = dict(params)
    if not deployed:
        expected_params["LambdaArtifactKey"] = new_key
    assert after_params == expected_params, "Unexpected CloudFormation parameter changes"
    after_api = lam.get_function_configuration(FunctionName=api_fn)
    assert after_api["CodeSha256"] == expected_sha
    assert after_api["LastUpdateStatus"] == "Successful"
    assert lam.get_function_configuration(FunctionName=image_fn)["CodeSha256"] == image_before
    scenario_after = states.describe_state_machine(stateMachineArn=scenario_arn)
    assert scenario_after["definition"] == scenario_definition
    assert scenario_after["roleArn"] == scenario_role
    print("LIVE_API_CODE_VERIFIED", expected_sha, flush=True)

    frontend = cf.describe_stacks(StackName=FRONTEND)["Stacks"][0]
    assert frontend["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")
    outputs = {o["OutputKey"]: o["OutputValue"] for o in frontend["Outputs"]}
    bucket = outputs["FrontendBucketName"]
    distribution = outputs["DistributionId"]
    assert bucket == f"afl-app-dev-frontend-{ACCOUNT}-{REGION}"
    for key, path, content_type in (
        ("app.js", "frontend/app.js", "application/javascript; charset=utf-8"),
        ("index.html", "frontend/index.html", "text/html; charset=utf-8"),
    ):
        payload = Path(path).read_bytes()
        s3.put_object(Bucket=bucket, Key=key, Body=payload,
                      ContentType=content_type,
                      CacheControl="max-age=60, must-revalidate")
        assert s3.get_object(Bucket=bucket, Key=key)["Body"].read() == payload
        print("LIVE_FRONTEND_VERIFIED", key, digest(payload), flush=True)
    invalidation = session.client("cloudfront").create_invalidation(
        DistributionId=distribution,
        InvalidationBatch={
            "Paths": {"Quantity": 2, "Items": ["/app.js", "/index.html"]},
            "CallerReference": "overall-progress-" + run_id,
        })
    print("CLOUDFRONT_INVALIDATION", invalidation["Invalidation"]["Id"], flush=True)
    print("OVERALL_PROGRESS_API_AND_FRONTEND_DEPLOYED_VERIFIED", flush=True)


if __name__ == "__main__":
    main()
