#!/usr/bin/env python3
"""Deploy only the image worker team-master lookup; no Google generation.

Reuses the live CloudFormation template and current deployed image-worker ZIP,
replacing only handler.py and google_direct.py. Stops for running image jobs,
stack drift, replacements, or additional resource changes.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

import boto3
from botocore.exceptions import ClientError
from validate_change_set import validate
from preview_release_change_set import verify_reconcile_permission

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACK = "afl-app-dev-backend"
MACHINE = "arn:aws:states:ap-southeast-2:134387458447:stateMachine:afl-app-dev-scene1-pilot"
SOURCES = {
    "handler.py": Path("src/image_worker/handler.py"),
    "google_direct.py": Path("src/image_worker/google_direct.py"),
}


def main():
    session = boto3.session.Session(region_name=REGION)
    sts, cf, s3, sf, lam, events = (session.client(k) for k in (
        "sts", "cloudformation", "s3", "stepfunctions", "lambda", "events"))
    caller = sts.get_caller_identity()
    assert caller["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in caller["Arn"]
    before = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert before["StackStatus"] == "UPDATE_COMPLETE"
    role = before.get("RoleARN")
    params = {x["ParameterKey"]: x["ParameterValue"] for x in before["Parameters"]}
    assert "ImageWorkerArtifactKey" in params and "ArtifactBucketName" in params
    bucket, old_key = params["ArtifactBucketName"], params["ImageWorkerArtifactKey"]

    running = sf.list_executions(stateMachineArn=MACHINE, statusFilter="RUNNING", maxResults=100)
    assert not running["executions"], "Image generation is in progress; refusing deployment"
    current_machine = sf.describe_state_machine(stateMachineArn=MACHINE)

    response = s3.get_object(Bucket=bucket, Key=old_key)
    existing_zip = response["Body"].read()
    memory = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(existing_zip)) as original:
        assert original.testzip() is None
        entries = original.namelist()
        assert all(entries.count(key) == 1 for key in SOURCES)
        with zipfile.ZipFile(memory, "w") as proposed:
            for member in original.infolist():
                source = SOURCES.get(member.filename)
                payload = source.read_bytes() if source else original.read(member.filename)
                if source:
                    compile(payload, member.filename, "exec")
                proposed.writestr(member, payload)
    archive = memory.getvalue()
    sha = hashlib.sha256(archive).hexdigest()
    key = f"releases/team-master-lookup/{sha}/image-worker.zip"
    with zipfile.ZipFile(io.BytesIO(archive)) as check:
        assert check.testzip() is None
        assert check.namelist() == entries
        for filename, source in SOURCES.items():
            assert check.read(filename) == source.read_bytes()
    assert key != old_key, "Already deployed or no code change"
    try:
        live_artifact = s3.get_object(Bucket=bucket,Key=key)["Body"].read()
        assert live_artifact == archive, "Immutable artifact differs"
    except ClientError as error:
        assert error.response["Error"]["Code"] in ("NoSuchKey", "NotFound", "404")
        s3.put_object(Bucket=bucket, Key=key, Body=archive,
                      ContentType="application/zip")
        assert s3.get_object(Bucket=bucket,Key=key)["Body"].read() == archive

    raw_template = cf.get_template(StackName=STACK, TemplateStage="Original")["TemplateBody"]
    assert isinstance(raw_template, str), "Expected textual deployed template"
    args = [{"ParameterKey": k,
             **({"ParameterValue": key} if k == "ImageWorkerArtifactKey"
                else {"UsePreviousValue": True})}
            for k in params]
    name = "team-master-lookup-" + os.environ["GITHUB_RUN_ID"]
    change_kwargs = dict(StackName=STACK,ChangeSetName=name,
                         ChangeSetType="UPDATE",TemplateBody=raw_template,
                         Parameters=args,Capabilities=["CAPABILITY_NAMED_IAM"])
    if role:
        change_kwargs["RoleARN"] = role
    cf.create_change_set(**change_kwargs)
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK,ChangeSetName=name,WaiterConfig={"Delay":5,"MaxAttempts":48})
    plan = cf.describe_change_set(StackName=STACK,ChangeSetName=name)
    assert not plan.get("NextToken"), "Review paginated changeset manually"
    changes=[x["ResourceChange"] for x in plan["Changes"]]
    assert changes, "No change"
    names={c["LogicalResourceId"] for c in changes}
    print("PROPOSED_IMAGE_WORKER_CHANGESET", [(c["LogicalResourceId"],
          c.get("Action"), c.get("Replacement"), c.get("Details")) for c in changes],flush=True)
    verified=verify_reconcile_permission(cf, lam, events, STACK, plan["Changes"])
    issues=validate(plan, {"ImageWorkerLambda"},
                    verified_reconcile_permission=verified)
    assert not issues, "Unsafe CloudFormation changes: " + repr(issues)
    assert "ImageWorkerLambda" in names, names
    assert not sf.list_executions(stateMachineArn=MACHINE,statusFilter="RUNNING",maxResults=100)["executions"]
    print("TEAM_MASTER_LOOKUP_CHANGESET_APPROVED", sorted(names), flush=True)
    cf.execute_change_set(StackName=STACK,ChangeSetName=name)
    cf.get_waiter("stack_update_complete").wait(
        StackName=STACK,WaiterConfig={"Delay":10,"MaxAttempts":60})
    after = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert after["StackStatus"] == "UPDATE_COMPLETE"
    after_params={x["ParameterKey"]:x["ParameterValue"] for x in after["Parameters"]}
    assert after_params["ImageWorkerArtifactKey"]==key
    assert all(after_params[k]==v for k,v in params.items()
               if k!="ImageWorkerArtifactKey")
    assert sf.describe_state_machine(stateMachineArn=MACHINE)["definition"]==current_machine["definition"]
    fn = lam.get_function_configuration(FunctionName="afl-app-dev-image-worker")
    assert fn["State"]=="Active" and fn["LastUpdateStatus"]=="Successful"
    print("TEAM_MASTER_LOOKUP_AWS_DEPLOYED_VERIFIED",sha,flush=True)
    print("No paid image or video calls were submitted.",flush=True)


if __name__ == "__main__":
    main()
