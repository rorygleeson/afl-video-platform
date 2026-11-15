#!/usr/bin/env python3
"""Create isolated watchdog stack only. No application stack mutations or Google calls."""
import hashlib
import base64
import io
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

REGION="ap-southeast-2"
ACCOUNT="134387458447"
STACK="afl-app-dev-handoff-watchdog"
ROLE=f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
ARTIFACT_BUCKET="afl-video-bootstrap-artifactbucket-xiuriidgyft4"
EXPECTED={
    "WatchdogRole":"AWS::IAM::Role",
    "WatchdogLogs":"AWS::Logs::LogGroup",
    "WatchdogLambda":"AWS::Lambda::Function",
    "EveryMinute":"AWS::Events::Rule",
    "InvokeFromEventBridge":"AWS::Lambda::Permission",
}
def main():
    session=boto3.session.Session(region_name=REGION)
    identity=session.client("sts").get_caller_identity()
    assert identity["Account"]==ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in identity["Arn"]
    cf=session.client("cloudformation")
    try:
        existing=cf.describe_stacks(StackName=STACK)["Stacks"][0]
    except ClientError as e:
        if e.response.get("Error",{}).get("Code")!="ValidationError" or "does not exist" not in e.response.get("Error",{}).get("Message",""):
            raise
        existing=None
    if existing:
        assert existing["StackStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")
        assert existing["RoleARN"]==ROLE
        for logical, kind in EXPECTED.items():
            resource=cf.describe_stack_resource(StackName=STACK,LogicalResourceId=logical)["StackResourceDetail"]
            assert resource["ResourceType"]==kind
            assert resource["ResourceStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")

    source=Path("src/handoff_watchdog/handler.py").read_bytes()
    packed=io.BytesIO()
    with zipfile.ZipFile(packed,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("handler.py",source)
    blob=packed.getvalue()
    key="releases/handoff-watchdog/"+hashlib.sha256(blob).hexdigest()+".zip"
    s3=session.client("s3")
    s3.put_object(Bucket=ARTIFACT_BUCKET,Key=key,Body=blob,
                  ContentType="application/zip",ServerSideEncryption="AES256")
    assert s3.get_object(Bucket=ARTIFACT_BUCKET,Key=key)["Body"].read()==blob
    print("WATCHDOG_PACKAGE_S3_VERIFIED",flush=True)

    backend=cf.describe_stacks(StackName="afl-app-dev-backend")["Stacks"][0]
    assert backend["StackStatus"]=="UPDATE_COMPLETE"
    boundary=next(x["ParameterValue"] for x in backend["Parameters"]
                  if x["ParameterKey"]=="PermissionsBoundaryArn")
    activated=(next(x["ParameterValue"] for x in existing["Parameters"]
                    if x["ParameterKey"]=="ActivatedAt")
               if existing else datetime.now(timezone.utc).isoformat())
    template=Path("infrastructure/app/handoff-watchdog.yaml").read_text()
    if existing:
        live=cf.get_template(StackName=STACK)["TemplateBody"]
        assert isinstance(live,str) and live.strip()==template.strip(), (
            "Existing watchdog stack template drift: refuse update")
        expected_code=base64.b64encode(hashlib.sha256(blob).digest()).decode()
        current=session.client("lambda").get_function_configuration(
            FunctionName="afl-app-dev-handoff-watchdog")
        if current["CodeSha256"]==expected_code:
            print("WATCHDOG_ALREADY_DEPLOYED_VERIFIED",flush=True)
            return
    assert "rate(1 minute)" in template and "ReservedConcurrentExecutions: 1" in template
    assert "states:StartExecution" not in template and "dynamodb:UpdateItem" not in template
    name="watchdog-"+os.environ["GITHUB_RUN_ID"]
    cf.create_change_set(
        StackName=STACK,ChangeSetName=name,ChangeSetType="UPDATE" if existing else "CREATE",
        TemplateBody=template,RoleARN=ROLE,
        Parameters=([{"ParameterKey":"ArtifactKey","ParameterValue":key}]
                    + [{"ParameterKey":k,"UsePreviousValue":True}
                       for k in ("ArtifactBucket","PermissionsBoundaryArn","ActivatedAt")]
                    if existing else
                    [{"ParameterKey":k,"ParameterValue":v} for k,v in (
                        ("ArtifactBucket",ARTIFACT_BUCKET),("ArtifactKey",key),
                        ("PermissionsBoundaryArn",boundary),("ActivatedAt",activated))]),
        Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Isolated 1-minute watchdog; no paid calls, no existing stack updates")
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK,ChangeSetName=name,WaiterConfig={"Delay":5,"MaxAttempts":36})
    result=cf.describe_change_set(StackName=STACK,ChangeSetName=name)
    assert not result.get("NextToken"),"Paginated changeset requires manual review"
    resources={x["ResourceChange"]["LogicalResourceId"]:x["ResourceChange"]
               for x in result["Changes"]}
    allowed={"WatchdogLambda"} if existing else set(EXPECTED)
    assert set(resources)==allowed,f"Unexpected resource changes: {list(resources)}"
    for logical,kind in ({"WatchdogLambda":EXPECTED["WatchdogLambda"]} if existing else EXPECTED).items():
        change=resources[logical]
        assert change["Action"]==("Modify" if existing else "Add") and change["ResourceType"]==kind
        assert change.get("Replacement") in (None,False,"False")
        if existing:
            assert all(x.get("Target",{}).get("Name")=="Code"
                       for x in change.get("Details",[])), (
                "Watchdog update may modify only Lambda Code")
    print("SAFE_ISOLATED_WATCHDOG_CHANGESET",sorted(resources),flush=True)
    cf.execute_change_set(StackName=STACK,ChangeSetName=name)
    cf.get_waiter("stack_update_complete" if existing else "stack_create_complete").wait(
        StackName=STACK,WaiterConfig={"Delay":10,"MaxAttempts":60})
    actual=cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert actual["StackStatus"]==("UPDATE_COMPLETE" if existing else "CREATE_COMPLETE")
    for logical,kind in EXPECTED.items():
        r=cf.describe_stack_resource(StackName=STACK,LogicalResourceId=logical)["StackResourceDetail"]
        assert r["ResourceType"]==kind and r["ResourceStatus"]=="CREATE_COMPLETE"
    lam=session.client("lambda").get_function_configuration(FunctionName="afl-app-dev-handoff-watchdog")
    assert lam["LastUpdateStatus"]=="Successful"
    assert lam["CodeSha256"]==base64.b64encode(hashlib.sha256(blob).digest()).decode()
    assert lam["Environment"]["Variables"]["WATCHDOG_ACTIVATED_AT"]==activated
    print("WATCHDOG_DEPLOYED_VERIFIED",activated,flush=True)
    print("WATCHDOG_ISOLATED_RELEASE_COMPLETE",flush=True)

if __name__=="__main__":
    main()
