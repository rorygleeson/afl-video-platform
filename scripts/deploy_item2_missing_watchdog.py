#!/usr/bin/env python3
"""Guarded Item 2 missed-watchdog recovery deployment, two app stacks only.

No Google/API paid submission, DynamoDB data writes, or Step Functions execution.
Uses the existing CFN execution role; source is merged and offline tested first.
"""
import base64
import hashlib
import io
import os
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
WD_STACK = "afl-app-dev-handoff-watchdog"
BE_STACK = "afl-app-dev-backend"
SID = "BootstrapExistingWatchdogOnly"
IAM_STATEMENT = """              - Sid: BootstrapExistingWatchdogOnly
                Effect: Allow
                Action: lambda:InvokeFunction
                Resource: !Sub 'arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:afl-app-dev-handoff-watchdog'
"""


def digest(blob):
    return base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")


def params(stack):
    return {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}


def read_stack(cf, name):
    item = cf.describe_stacks(StackName=name)["Stacks"][0]
    assert item["StackStatus"] == "UPDATE_COMPLETE", f"{name}: stack not ready"
    assert item["RoleARN"] == ROLE, f"{name}: wrong execution role"
    assert not item.get("DisableRollback"), f"{name}: rollback disabled"
    return item


def replace_handler(original, data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as old:
        assert old.testzip() is None and old.namelist().count("handler.py") == 1
        with zipfile.ZipFile(buffer, "w") as out:
            for entry in old.infolist():
                out.writestr(entry, data if entry.filename == "handler.py" else old.read(entry.filename))
    result = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as check:
        assert check.testzip() is None and check.read("handler.py") == data
    return result


def immutable_artifact(s3, payload, kind):
    key = "releases/item2-missed-watchdog/" + hashlib.sha256(payload).hexdigest() + f"/{kind}.zip"
    try:
        existing = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        assert existing == payload, "Artifact checksum collision"
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("NoSuchKey", "404", "NotFound"):
            raise
        s3.put_object(Bucket=BUCKET, Key=key, Body=payload, ServerSideEncryption="AES256")
        assert s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == payload
    return key


def patch_backend_iam(live):
    marker = "              - Sid: WorkerLogging\n"
    image_section = live.split("  ImageWorkerRole:", 1)
    assert len(image_section) == 2, "Unrecognised backend image worker section"
    image_part = image_section[1].split("  ImageWorkerLambda:", 1)
    assert len(image_part) == 2, "Unrecognised backend role layout"
    pre = image_part[0]
    if "              - Sid: " + SID in pre:
        assert pre.count("              - Sid: " + SID) == 1
        return live
    assert pre.count(marker) == 1
    updated_pre = pre.replace(marker, IAM_STATEMENT + marker)
    result = image_section[0] + "  ImageWorkerRole:" + updated_pre + "  ImageWorkerLambda:" + image_part[1]
    assert result.count("              - Sid: " + SID) == 1
    assert result.replace(IAM_STATEMENT, "") == live
    return result


def make_change_set(cf, stack, title, artifact_parameter, key, template=None):
    args = {
        "StackName": stack["StackName"], "ChangeSetName": title,
        "ChangeSetType": "UPDATE", "RoleARN": ROLE,
        "Capabilities": ["CAPABILITY_NAMED_IAM"],
        "Parameters": [
            {"ParameterKey": p["ParameterKey"], **(
                {"ParameterValue": key} if p["ParameterKey"] == artifact_parameter
                else {"UsePreviousValue": True})}
            for p in stack["Parameters"]
        ],
    }
    if template is None:
        args["UsePreviousTemplate"] = True
    else:
        args["TemplateBody"] = template
    assert artifact_parameter in params(stack)
    cf.create_change_set(**args)
    cf.get_waiter("change_set_create_complete").wait(
        StackName=stack["StackName"], ChangeSetName=title,
        WaiterConfig={"Delay": 5, "MaxAttempts": 35})
    result = cf.describe_change_set(StackName=stack["StackName"], ChangeSetName=title)
    assert not result.get("NextToken"), "Paged CFN preview; fail closed"
    return [x["ResourceChange"] for x in result.get("Changes", [])]


def check_changes(changes, kind):
    allowed = {
        "watchdog": {"WatchdogLambda", "WatchdogMachine", "WatchdogMachineRole",
                     "EveryMinute", "InvokeFromEventBridge"},
        "image": {"ImageWorkerLambda", "ImageWorkerRole", "ImageStateMachine",
                  "ImageStateMachineRole", "ImageReconcileSchedule",
                  "ImageReconcileInvokePermission"},
    }[kind]
    required = {"watchdog": {"WatchdogLambda"},
                "image": {"ImageWorkerLambda", "ImageWorkerRole"}}[kind]
    ids = {c["LogicalResourceId"] for c in changes}
    print("PREVIEW_RESOURCES", kind, sorted(ids), flush=True)
    assert required <= ids <= allowed, f"Unexpected {kind} resources: {ids}"
    for change in changes:
        logical = change["LogicalResourceId"]
        assert change["Action"] == "Modify", f"{kind}: unexpected action for {logical}"
        replacement = change.get("Replacement")
        if logical in ("EveryMinute", "InvokeFromEventBridge",
                       "ImageReconcileSchedule", "ImageReconcileInvokePermission"):
            assert replacement in ("False", False, None, "Conditional"), f"{logical}: replacement"
        else:
            assert replacement in ("False", False, None), f"{logical}: replacement"
        for detail in change.get("Details", []):
            target = detail.get("Target") or {}
            attr = target.get("Attribute")
            name = target.get("Name")
            print("PREVIEW_DETAIL", kind, logical, detail.get("Evaluation"),
                  detail.get("ChangeSource"), attr, name, flush=True)
            if detail.get("Evaluation") == "Static":
                assert attr == "Properties", f"{logical}: non-property change"
                if logical in ("WatchdogLambda", "ImageWorkerLambda"):
                    assert name == "Code", f"{logical}: unexpected static property"
                elif logical == "ImageWorkerRole":
                    assert name == "Policies", f"{logical}: unexpected IAM property"
                else:
                    raise AssertionError(f"{logical}: unrelated static modification")
            else:
                source = detail.get("ChangeSource")
                if source == "DirectModification":
                    # CFN may mark an explicit S3 artifact change Dynamic.
                    # Only the exact intended Lambda Code or IAM policy may
                    # qualify; never accept another direct configuration edit.
                    assert attr == "Properties", f"{logical}: direct non-property change"
                    assert ((logical in ("WatchdogLambda", "ImageWorkerLambda") and name == "Code")
                            or (logical == "ImageWorkerRole" and name == "Policies")), (
                        f"{logical}: unexpected dynamic direct modification {name}")
                else:
                    assert source in ("ResourceReference", "ResourceAttribute"), (
                        f"{logical}: unsafe dynamic change")
    print("PREVIEW_ACCEPTED", kind, flush=True)


def main():
    assert os.environ.get("DEPLOY_ITEM2") == "AUTHORIZE_MISSED_WATCHDOG"
    assert os.environ.get("GITHUB_REF") == "refs/heads/main"
    sess = boto3.session.Session(region_name=REGION)
    ident = sess.client("sts").get_caller_identity()
    assert ident["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in ident["Arn"]
    cf, s3, lam = sess.client("cloudformation"), sess.client("s3"), sess.client("lambda")
    stack_w = read_stack(cf, WD_STACK)
    stack_b = read_stack(cf, BE_STACK)
    live_backend = cf.get_template(StackName=BE_STACK)["TemplateBody"]
    assert isinstance(live_backend, str)
    patched_backend = patch_backend_iam(live_backend)
    assert patched_backend != live_backend or SID in live_backend

    plans = [
        {"kind": "watchdog", "stack": stack_w, "name": "afl-app-dev-handoff-watchdog",
         "param": "ArtifactKey", "code": Path("src/handoff_watchdog/handler.py")},
        {"kind": "image", "stack": stack_b, "name": "afl-app-dev-image-worker",
         "param": "ImageWorkerArtifactKey", "code": Path("src/image_worker/handler.py")},
    ]
    pending = []
    for plan in plans:
        stack = plan["stack"]
        assert params(stack).get("ArtifactBucket" if plan["kind"] == "watchdog"
                                else "ArtifactBucketName") == BUCKET
        original = s3.get_object(Bucket=BUCKET, Key=params(stack)[plan["param"]])["Body"].read()
        before = lam.get_function_configuration(FunctionName=plan["name"])
        assert before["State"] == "Active" and before["LastUpdateStatus"] == "Successful"
        assert before["CodeSha256"] == digest(original), f"{plan['kind']}: live code drift"
        updated = replace_handler(original, plan["code"].read_bytes())
        plan["before"] = before["CodeSha256"]
        plan["after"] = digest(updated)
        if plan["before"] != plan["after"]:
            plan["key"] = immutable_artifact(s3, updated, plan["kind"])
        else:
            plan["key"] = params(stack)[plan["param"]]
        needs_iam = plan["kind"] == "image" and patched_backend != live_backend
        if plan["before"] != plan["after"] or needs_iam:
            pending.append(plan)
    assert pending, "Already deployed; no need for changes"
    run = os.environ["GITHUB_RUN_ID"]
    for i, plan in enumerate(pending):
        plan["change"] = f"item2-watchdog-{run}-{i}"
        template = patched_backend if plan["kind"] == "image" else None
        changes = make_change_set(cf, plan["stack"], plan["change"],
                                  plan["param"], plan["key"], template=template)
        check_changes(changes, plan["kind"])
    print("ALL_CHANGESETS_PREVIEWED_AND_ACCEPTED", flush=True)
    for plan in pending:
        name = plan["stack"]["StackName"]
        current = read_stack(cf, name)
        assert current["StackId"] == plan["stack"]["StackId"], "Stack changed during preview"
        assert lam.get_function_configuration(FunctionName=plan["name"])["CodeSha256"] == plan["before"]
        cf.execute_change_set(StackName=name, ChangeSetName=plan["change"])
        cf.get_waiter("stack_update_complete").wait(
            StackName=name, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
        updated = lam.get_function_configuration(FunctionName=plan["name"])
        assert updated["LastUpdateStatus"] == "Successful"
        assert updated["CodeSha256"] == plan["after"]
        if plan["kind"] == "image":
            template = cf.get_template(StackName=BE_STACK)["TemplateBody"]
            assert SID in template, "Image IAM permission not persisted"
        print("DEPLOYED_AND_VERIFIED", plan["kind"], flush=True)
    print("ITEM2_MISSED_WATCHDOG_DEPLOYED: NO GOOGLE OR JOB SUBMISSIONS", flush=True)


if __name__ == "__main__":
    main()
