#!/usr/bin/env python3
"""Guarded Item 1 stitching recovery release: API + assembly worker/SFN + watchdog.

No API calls to Google, no video generation, no direct Lambda writes.
Source is only merged to main after isolated offline tests pass.
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
WATCHDOG = "afl-app-dev-handoff-watchdog"
API_STACK = "afl-app-dev-backend"
ASSEMBLY_STACK = "afl-app-dev-assembly"
STAGES = [
    ("api", API_STACK, "ApiLambda", "afl-app-dev-api",
     "LambdaArtifactKey", Path("src/api/handler.py")),
    ("assembly-worker", ASSEMBLY_STACK, "AssemblyWorker", "afl-app-dev-assembly",
     "AssemblyWorkerArtifactKey", Path("src/assembly_worker/handler.py")),
]


def d64(raw):
    return base64.b64encode(hashlib.sha256(raw).digest()).decode()


def parameters(stack):
    return {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}


def require_stack(cf, name):
    stack = cf.describe_stacks(StackName=name)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE", f"{name} not steady"
    assert stack["RoleARN"] == ROLE, "Unknown CFN execution role"
    assert not stack.get("DisableRollback"), "Rollback disabled"
    return stack


def replace_member(old, member, value):
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(old)) as z:
        assert z.testzip() is None
        assert z.namelist().count(member) == 1
        with zipfile.ZipFile(result, "w") as output:
            for item in z.infolist():
                output.writestr(item, value if item.filename == member else z.read(item.filename))
    data = result.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as check:
        assert check.testzip() is None and check.read(member) == value
    return data


def upload_immutable(s3, payload, suffix):
    key = f"releases/assembly-resilience/{hashlib.sha256(payload).hexdigest()}/{suffix}"
    try:
        existing = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        assert existing == payload, "Immutable artifact key collision"
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
            raise
        s3.put_object(Bucket=BUCKET, Key=key, Body=payload,
                      ServerSideEncryption="AES256")
        assert s3.get_object(Bucket=BUCKET, Key=key)["Body"].read() == payload
    return key


def change_set(cf, stack, name, template=None, parameter_replace=None):
    old_params = parameters(stack)
    args = dict(StackName=stack["StackName"], ChangeSetName=name,
                ChangeSetType="UPDATE", RoleARN=ROLE,
                Capabilities=["CAPABILITY_NAMED_IAM"])
    args["Parameters"] = [
        {"ParameterKey": k, **(
            {"ParameterValue": parameter_replace[1]}
            if parameter_replace and k == parameter_replace[0]
            else {"UsePreviousValue": True})}
        for k in old_params]
    if template is None:
        args["UsePreviousTemplate"] = True
    else:
        args["TemplateBody"] = template
    cf.create_change_set(**args)
    cf.get_waiter("change_set_create_complete").wait(
        StackName=stack["StackName"], ChangeSetName=name,
        WaiterConfig={"Delay":5, "MaxAttempts":36})
    result = cf.describe_change_set(StackName=stack["StackName"], ChangeSetName=name)
    assert not result.get("NextToken"), "Unreviewed paged change set"
    return [x["ResourceChange"] for x in result["Changes"]]


def validate(changes, allowed, required, kind):
    ids = {x["LogicalResourceId"] for x in changes}
    assert required <= ids <= allowed, f"{kind} unexpected CFN resources: {ids}"
    for c in changes:
        assert c["Action"] == "Modify", f"{kind} non-modify action"
        assert c.get("Replacement") in ("False", False, None), f"{kind} replacement"
        for detail in c.get("Details", []):
            target = detail.get("Target", {})
            if detail.get("Evaluation") == "Static" and c["LogicalResourceId"] in required:
                if kind in ("api", "assembly-worker"):
                    assert target.get("Name") == "Code", f"{kind} non-code setting change"
                elif kind == "assembly-sfn":
                    assert target.get("Name") in ("DefinitionString", "Definition"), "SFN unrelated setting"
                elif kind == "watchdog":
                    assert target.get("Name") in ("Code", "Environment", "Policies"), "Watchdog unrelated setting"
    print("GUARDED_CHANGESET", kind, sorted(ids), flush=True)


def inline_definition(template):
    match = re.search(r"(?ms)^      DefinitionString: !Sub \\|\\n(?P<definition>.*?)(?=^Outputs:|\\Z)", template)
    if not match:
        # Safe diagnostic: show only formatting around the CFN key, never
        # parameters, secrets, complete template, or customer content.
        line = next((line for line in template.splitlines()
                     if "DefinitionString" in line), "<absent>")
        print("ASSEMBLY_TEMPLATE_FORMAT", {
            "firstCharacter": template.lstrip()[:1],
            "definitionKeyLine": line[:140],
            "lineCount": len(template.splitlines()),
        }, flush=True)
        raise RuntimeError("Assembly template definition format unsupported; no deployment")
    raw = match.group("definition")
    assert raw.strip().endswith("}"), "Unexpected assembly definition structure"
    return match, raw


def assemble_sfn_template(live, source):
    old_match, old_def = inline_definition(live)
    _, new_def = inline_definition(source)
    assert '"StartAt": "Stitch"' in old_def
    assert '"RetryConfirmedAssemblyFailure"' not in old_def or old_def == new_def
    # This changes ONLY the assembly definition, retaining deployed stack resources and parameters.
    result = live[:old_match.start("definition")] + new_def + live[old_match.end("definition"):]
    assert result.count("DefinitionString: !Sub |") == live.count("DefinitionString: !Sub |")
    assert "RetryConfirmedAssemblyFailure" in result
    return result


def main():
    assert os.environ.get("DEPLOY_ITEM1") == "AUTHORIZE_ITEM1_STITCHING"
    sess = boto3.session.Session(region_name=REGION)
    who = sess.client("sts").get_caller_identity()
    assert who["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in who["Arn"]
    cf, lam = sess.client("cloudformation"), sess.client("lambda")
    s3, sf = sess.client("s3"), sess.client("stepfunctions")
    run = os.environ["GITHUB_RUN_ID"]
    plans = []
    for kind, stack_name, logical, function, parameter, path in STAGES:
        stack = require_stack(cf, stack_name)
        params = parameters(stack)
        assert params["ArtifactBucketName"] == BUCKET
        old = s3.get_object(Bucket=BUCKET, Key=params[parameter])["Body"].read()
        deployed = lam.get_function_configuration(FunctionName=function)
        assert deployed["State"] == "Active"
        assert deployed["LastUpdateStatus"] == "Successful"
        assert deployed["CodeSha256"] == d64(old), f"{function} differs from CFN artifact"
        if kind == "assembly-worker":
            assert deployed["Timeout"] == 600
        updated = replace_member(old, "handler.py", path.read_bytes())
        if d64(updated) == deployed["CodeSha256"]:
            print("ALREADY_DEPLOYED", kind, flush=True)
            continue
        key = upload_immutable(s3, updated, f"{kind}.zip")
        plans.append(dict(kind=kind, stack=stack, logical=logical,
                          function=function, param=parameter, key=key,
                          before=deployed["CodeSha256"], after=d64(updated),
                          original=params[parameter]))
    assembly_stack = require_stack(cf, ASSEMBLY_STACK)
    live_assembly = cf.get_template(StackName=ASSEMBLY_STACK)["TemplateBody"]
    assert isinstance(live_assembly, str)
    assembly_source = Path("infrastructure/app/assembly.yaml").read_text()
    updated_assembly = assemble_sfn_template(live_assembly, assembly_source)
    update_definition = updated_assembly.strip() != live_assembly.strip()
    machine_arn = cf.describe_stack_resource(StackName=ASSEMBLY_STACK,
                                               LogicalResourceId="AssemblyStateMachine")[
                                                   "StackResourceDetail"]["PhysicalResourceId"]
    before_machine = sf.describe_state_machine(stateMachineArn=machine_arn)
    assert before_machine["status"] == "ACTIVE"
    assert before_machine["type"] == "STANDARD"
    assembly_plan = next((p for p in plans if p["kind"] == "assembly-worker"), None)
    if assembly_plan and update_definition:
        # One combined CFN change set for the assembly stack. Two independent
        # change sets from the original stack could otherwise revert the worker.
        assembly_plan["template"] = updated_assembly
        assembly_plan["machine"] = machine_arn
    elif update_definition:
        plans.append(dict(kind="assembly-sfn", stack=assembly_stack,
                          template=updated_assembly, machine=machine_arn))
    wd_stack = require_stack(cf, WATCHDOG)
    wd_source = Path("src/handoff_watchdog/handler.py").read_bytes()
    wd_buffer = io.BytesIO()
    with zipfile.ZipFile(wd_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("handler.py", wd_source)
    wd_payload = wd_buffer.getvalue()
    wd_key = upload_immutable(s3, wd_payload, "watchdog.zip")
    wd_template = Path("infrastructure/app/handoff-watchdog.yaml").read_text()
    live_wd = cf.get_template(StackName=WATCHDOG)["TemplateBody"]
    assert isinstance(live_wd, str)
    wd_fn = lam.get_function_configuration(FunctionName="afl-app-dev-handoff-watchdog")
    assert wd_fn["LastUpdateStatus"] == "Successful"
    old_wd_param = parameters(wd_stack)["ArtifactKey"]
    old_wd_payload = s3.get_object(Bucket=BUCKET, Key=old_wd_param)["Body"].read()
    assert d64(old_wd_payload) == wd_fn["CodeSha256"]
    if wd_template.strip() != live_wd.strip() or wd_key != old_wd_param:
        plans.append(dict(kind="watchdog", stack=wd_stack, template=wd_template,
                          wd_key=wd_key, old_wd_key=old_wd_param))

    # Complete all change-set previews BEFORE any execution.
    if not plans:
        print("ITEM1_ALREADY_DEPLOYED_NOOP", flush=True)
        return
    print("RELEASE_PLAN", [p["kind"] for p in plans], flush=True)
    created = []
    for idx, p in enumerate(plans):
        name = f"item1-stitch-{run}-{idx}"
        change = change_set(cf, p["stack"], name,
                            template=p.get("template"),
                            parameter_replace=(p["param"], p["key"]) if "param" in p else
                                              ("ArtifactKey", p["wd_key"]) if p["kind"] == "watchdog" else None)
        allowed = {
            "api": {"ApiLambda", "InvokePermission", "ProxyAny"},
            "assembly-worker": {"AssemblyWorker", "AssemblyStateMachine"},
            "assembly-sfn": {"AssemblyStateMachine"},
            "watchdog": {"WatchdogRole", "WatchdogLambda", "WatchdogMachine"},
        }[p["kind"]]
        required = {
            "api": {"ApiLambda"},
            "assembly-worker": {"AssemblyWorker"},
            "assembly-sfn": {"AssemblyStateMachine"},
            "watchdog": {"WatchdogRole", "WatchdogLambda"},
        }[p["kind"]]
        validate(change, allowed, required, p["kind"])
        p["change"] = name
        created.append(p)
    # Executes only after every stack change-set has passed guard checks.
    # Failure stops subsequent stacks. CloudFormation has rollback on each stack.
    for p in plans:
        stack_name = p["stack"]["StackName"]
        current = require_stack(cf, stack_name)
        assert current["StackId"] == p["stack"]["StackId"]
        if "before" in p:
            fn = lam.get_function_configuration(FunctionName=p["function"])
            assert fn["CodeSha256"] == p["before"]
        cf.execute_change_set(StackName=stack_name, ChangeSetName=p["change"])
        cf.get_waiter("stack_update_complete").wait(
            StackName=stack_name, WaiterConfig={"Delay":10, "MaxAttempts":80})
        assert require_stack(cf, stack_name)["StackStatus"] == "UPDATE_COMPLETE"
        if "after" in p:
            fn = lam.get_function_configuration(FunctionName=p["function"])
            assert fn["CodeSha256"] == p["after"]
            assert fn["LastUpdateStatus"] == "Successful"
        if "machine" in p:
            actual = sf.describe_state_machine(stateMachineArn=p["machine"])
            assert "RetryConfirmedAssemblyFailure" in actual["definition"]
            assert actual["type"] == "STANDARD"
        if p["kind"] == "watchdog":
            fn = lam.get_function_configuration(FunctionName="afl-app-dev-handoff-watchdog")
            assert fn["CodeSha256"] == d64(wd_payload)
            assert fn["Environment"]["Variables"]["ASSEMBLY_STATE_MACHINE_ARN"].endswith(
                "stateMachine:afl-app-dev-assembly")
        print("DEPLOYED_AND_VERIFIED", p["kind"], flush=True)
    print("ITEM1_STITCHING_RECOVERY_DEPLOYED: no paid Google calls", flush=True)


if __name__ == "__main__":
    main()
