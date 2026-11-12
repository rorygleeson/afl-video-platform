#!/usr/bin/env python3
"""Reconcile verified Google HTTP 150 live worker and deploy CFN 300s Lambda timeout.

Do not modify paid generation submission, retries, or source code. Detect
CloudFormation transitive reference changes and fail closed on unknown effects.
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
    assert before["CodeSha256"] == new_sha, "Live Lambda does not match verified HTTP=150 patch"

    params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    assert params["ArtifactBucketName"] == BUCKET
    new_key = f"google-timeouts/{hashlib.sha256(newzip).hexdigest()}/google-worker.zip"
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

    # Reconcile the already-running HTTP=150 code with an immutable IaC artifact.
    # No direct Lambda updates; all final changes owned by CloudFormation.
    try:
        stored=s3.get_object(Bucket=BUCKET,Key=new_key)["Body"].read()
        assert digest(stored)==new_sha
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404","NoSuchKey","NotFound"):
            raise
        s3.put_object(Bucket=BUCKET,Key=new_key,Body=newzip)
        assert digest(s3.get_object(Bucket=BUCKET,Key=new_key)["Body"].read())==new_sha
    cfn_params = [
        {"ParameterKey": key, "ParameterValue": new_key if key=="GoogleWorkerArtifactKey" else value}
        for key,value in params.items()
    ]
    cs_name = "afl-google-iac-reconcile-" + hashlib.sha256(newzip).hexdigest()[:12]
    try:
        cf.create_change_set(
            StackName=STACK, ChangeSetName=cs_name, ChangeSetType="UPDATE",
            TemplateBody=updated_template, Parameters=cfn_params, RoleARN=CFN_ROLE,
            Capabilities=["CAPABILITY_NAMED_IAM"], Description="Reconcile proven HTTP 150 artifact and Lambda Timeout 300"
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
        # CloudFormation's dynamic !GetAtt references can cause indirect modifications.
        # Inspect every impacted resource before execute; never permit replacements,
        # direct edits of other resources, or any unknown dependencies.
        expected = {
            "GoogleWorkerLambda": "AWS::Lambda::Function",
            "DeveloperPreflightInvokePermission": "AWS::Lambda::Permission",
            "GoogleStateMachineRole": "AWS::IAM::Role",
            "GoogleStateMachine": "AWS::StepFunctions::StateMachine",
        }
        assert {c["LogicalResourceId"]:c["ResourceType"] for c in changes} == expected, (
            "Unexpected CloudFormation resource set"
        )
        for c in changes:
            print("CHANGE_DETAIL", c["LogicalResourceId"], c["Action"], c.get("Replacement"),
                  c.get("Details",[]), flush=True)
            assert c["Action"]=="Modify", "Destructive CFN action forbidden"
            logical=c["LogicalResourceId"]
            assert c.get("Replacement") in ("False",False) or (
                logical=="DeveloperPreflightInvokePermission"
                and c.get("Replacement")=="Conditional"
            ), "Unexpected CFN replacement"
            details=c.get("Details",[])
            assert details, "No reason supplied for CFN change"
            for d in details:
                if logical=="GoogleWorkerLambda":
                    assert d.get("Target",{}).get("Attribute")=="Properties"
                    assert d.get("Target",{}).get("Name") in ("Timeout","Code")
                else:
                    # !GetAtt ARNs are reported as ResourceAttribute, not
                    # ResourceReference. The physical Lambda name is fixed,
                    # so the ARN remains unchanged across code/timeout updates.
                    assert d.get("ChangeSource")=="ResourceAttribute", (
                        "Direct dependent resource modification blocked"
                    )
                    assert d.get("CausingEntity") in (
                        "GoogleWorkerLambda.Arn", "GoogleStateMachineRole.Arn"
                    ), "Unexpected dependent-resource attribute"
                    assert d.get("Evaluation")=="Dynamic", (
                        "Static dependent resource modification blocked"
                    )
                    assert d.get("Target",{}).get("Attribute")=="Properties"
                    allowed_targets={
                        "DeveloperPreflightInvokePermission":{"FunctionName"},
                        "GoogleStateMachineRole":{"Policies"},
                        "GoogleStateMachine":{"DefinitionString","RoleArn"}
                    }
                    assert d.get("Target",{}).get("Name") in allowed_targets[logical], (
                        "Unexpected dependent-resource target"
                    )
        print("CFN_DEPENDENCIES_AUDITED: only Lambda code/timeout direct, references indirect",flush=True)
        cf.execute_change_set(StackName=STACK, ChangeSetName=cs_name)
        print("CFN_UPDATE_SUBMITTED: waiting for stack update")
        cf.get_waiter("stack_update_complete").wait(
            StackName=STACK, WaiterConfig={"Delay":10,"MaxAttempts":60}
        )
        after = lam.get_function_configuration(FunctionName=FUNCTION)
        assert after["Timeout"]==300 and after["CodeSha256"]==new_sha
        assert after["State"]=="Active" and after["LastUpdateStatus"]=="Successful"
        actual = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        assert actual["StackStatus"] == "UPDATE_COMPLETE"
        final_params = {p["ParameterKey"]: p["ParameterValue"] for p in actual["Parameters"]}
        assert final_params["GoogleWorkerArtifactKey"] == new_key
        print("DEPLOYED_AND_VERIFIED: Google Lambda 300s, Google HTTP 150s, zero paid requests.")
        print("CFN_RECONCILED: Lambda code is owned by CloudFormation; checked preexisting Google HTTP 150 artifact.")
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
