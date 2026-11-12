#!/usr/bin/env python3
"""One-time split CloudFormation timeout + verified Lambda code deployment: 300s/150s.

Do not modify paid generation submission, Lambda dependencies, other resources or
Step Functions definitions. Preflight and inspect actual change set before execute.
"""
import base64
import hashlib
import io
import re
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACK = "afl-app-dev-google-video"
FUNCTION = "afl-app-dev-google-omni-worker"
BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
OLD_KEY = "google-diagnostics/956c2a9faa7efb1f39ed1a8ecc376510ffb1ec34.zip"
OLD_SHA = "RKx1wcvmxcE3Sx7kzlxbTI3NhBlNlt/Ydpy6J4ZOpmI="
CFN_ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"


def digest(raw):
    return base64.b64encode(hashlib.sha256(raw).digest()).decode()


def main():
    sess = boto3.session.Session(region_name=REGION)
    cf = sess.client("cloudformation")
    lam = sess.client("lambda")
    s3 = sess.client("s3")
    sts = sess.client("sts")
    caller = sts.get_caller_identity()
    assert caller["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in caller["Arn"]
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE", "Stack is not stable"
    assert stack["RoleARN"] == CFN_ROLE, "Unexpected execution role"
    detail = cf.describe_stack_resource(
        StackName=STACK, LogicalResourceId="GoogleWorkerLambda"
    )["StackResourceDetail"]
    assert detail["PhysicalResourceId"] == FUNCTION
    before = lam.get_function_configuration(FunctionName=FUNCTION)
    assert before["Timeout"] == 180, "Unexpected live Lambda timeout"
    assert before["CodeSha256"] == OLD_SHA, "Live Lambda code changed; stop"
    assert before["State"] == "Active"
    assert before["LastUpdateStatus"] == "Successful"

    oldzip = s3.get_object(Bucket=BUCKET, Key=OLD_KEY)["Body"].read()
    assert digest(oldzip) == OLD_SHA, "Archived ZIP does not match live Lambda"
    with zipfile.ZipFile(io.BytesIO(oldzip)) as src:
        assert src.testzip() is None
        assert src.namelist().count("handler.py") == 1
        original = src.read("handler.py")
        search = b"with urllib.request.urlopen(req, timeout=75) as response:"
        assert original.count(search) == 1, "Expected HTTP timeout code absent"
        changed = original.replace(search, search.replace(b"timeout=75", b"timeout=150"))
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for member in src.infolist():
                target.writestr(
                    member, changed if member.filename == "handler.py" else src.read(member.filename)
                )
    newzip = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(newzip)) as z:
        assert z.testzip() is None
        assert z.read("handler.py") == changed
    new_sha = digest(newzip)

    params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    assert params["ArtifactBucketName"] == BUCKET
    template = cf.get_template(StackName=STACK, TemplateStage="Original")["TemplateBody"]
    assert isinstance(template, str), "Unexpected deployed template representation"
    # Change the *deployed* template, not the potentially drifted repository copy.
    matches = list(re.finditer(r"(?m)^([ \t]*)Timeout:[ \t]*180[ \t]*$", template))
    assert len(matches) == 1, "Expected exactly one 180s Lambda timeout"
    updated_template = (
        template[:matches[0].start()]
        + matches[0].group(1)
        + "Timeout: 300"
        + template[matches[0].end():]
    )
    assert updated_template.replace("Timeout: 300", "Timeout: 180", 1) == template
    print("PRECHECK_OK: exact working ZIP, live SHA, single template timeout alteration")

    # Update ONLY the existing Lambda Timeout through CloudFormation.
    # Keep the existing artifact parameter, avoiding transitive IAM/SFN changes.
    cfn_params = [
        {"ParameterKey": key, "ParameterValue": value}
        for key, value in params.items()
    ]
    cs_name = "afl-google-limit-only-" + hashlib.sha256(newzip).hexdigest()[:12]
    try:
        cf.create_change_set(
            StackName=STACK, ChangeSetName=cs_name, ChangeSetType="UPDATE",
            TemplateBody=updated_template, Parameters=cfn_params, RoleARN=CFN_ROLE,
            Capabilities=["CAPABILITY_NAMED_IAM"], Description="Only Google worker Lambda Timeout 180 to 300"
        )
        cf.get_waiter("change_set_create_complete").wait(
            StackName=STACK, ChangeSetName=cs_name, WaiterConfig={"Delay":5,"MaxAttempts":36}
        )
        result = cf.describe_change_set(StackName=STACK, ChangeSetName=cs_name)
        assert not result.get("NextToken"), "Unexpected paginated change set"
        changes = [entry["ResourceChange"] for entry in result["Changes"]]
        print("CFN_CHANGESET:", [
            (c["LogicalResourceId"], c["Action"], c.get("Replacement"))
            for c in changes
        ])
        # Stop on ANY unexpected IAM, Step Functions, permission, replacement or deletion.
        assert len(changes) == 1, "Extra CloudFormation resource changes; refusing deployment"
        c = changes[0]
        assert c["LogicalResourceId"] == "GoogleWorkerLambda"
        assert c["ResourceType"] == "AWS::Lambda::Function"
        assert c["Action"] == "Modify"
        assert c.get("Replacement") in ("False", False)
        targets = {(x.get("Target",{}).get("Attribute"), x.get("Target",{}).get("Name"))
                   for x in c.get("Details",[])}
        assert targets <= {("Properties","Timeout")}, (
            "Unexpected Lambda property changes"
        )
        cf.execute_change_set(StackName=STACK, ChangeSetName=cs_name)
        print("CFN_UPDATE_SUBMITTED: waiting for stack update")
        cf.get_waiter("stack_update_complete").wait(
            StackName=STACK, WaiterConfig={"Delay":10,"MaxAttempts":60}
        )
        after = lam.get_function_configuration(FunctionName=FUNCTION)
        assert after["Timeout"] == 300
        assert after["CodeSha256"] == OLD_SHA, "CFN unexpectedly changed Lambda code"
        # GitHub deploy role is authorized for CODE only. The verified ZIP
        # patches precisely the HTTP timeout and retains all dependencies.
        resp = lam.update_function_code(
            FunctionName=FUNCTION, ZipFile=newzip, RevisionId=after["RevisionId"]
        )
        lam.get_waiter("function_updated").wait(
            FunctionName=FUNCTION, WaiterConfig={"Delay":5,"MaxAttempts":36}
        )
        deployed=lam.get_function_configuration(FunctionName=FUNCTION)
        assert deployed["Timeout"] == 300 and deployed["CodeSha256"] == new_sha
        assert deployed["State"] == "Active" and deployed["LastUpdateStatus"] == "Successful"
        assert after["State"] == "Active" and after["LastUpdateStatus"] == "Successful"
        actual = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        assert actual["StackStatus"] == "UPDATE_COMPLETE"
        final_params = {p["ParameterKey"]: p["ParameterValue"] for p in actual["Parameters"]}
        assert final_params["GoogleWorkerArtifactKey"] == params["GoogleWorkerArtifactKey"]
        print("DEPLOYED_AND_VERIFIED: Google Lambda 300s, Google HTTP 150s, zero paid requests.")
        print("NOTE: Lambda code drift remains after code-only update; later CloudFormation changes must reconcile artifact safely.")
    except Exception:
        try:
            cs = cf.describe_change_set(StackName=STACK, ChangeSetName=cs_name)
            if cs["Status"] in ("CREATE_COMPLETE", "FAILED"):
                cf.delete_change_set(StackName=STACK, ChangeSetName=cs_name)
                print("Unused change set deleted; no deployment.")
        except ClientError:
            pass
        raise


if __name__ == "__main__":
    main()
