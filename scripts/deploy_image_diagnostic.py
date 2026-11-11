#!/usr/bin/env python3
"""One-time safe image-worker diagnostic code update (no paid generation).

Current live CloudFormation template and parameters are reused byte-for-byte,
except the image worker's immutable ZIP parameter. Never modifies S3 scenario
configuration, Step Functions definition or other services deliberately.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import boto3
from botocore.exceptions import ClientError
from validate_change_set import validate, dependency_only, expected_reconcile_permission
from preview_release_change_set import verify_reconcile_permission

REGION = "ap-southeast-2"
STACK = "afl-app-dev-backend"
ARN = "arn:aws:states:ap-southeast-2:134387458447:stateMachine:afl-app-dev-scene1-pilot"
TARGET = "afl-app-dev-image-worker"
SOURCE_SHA = "f57a6d6f4d2501ffb182dbc6f265ecf28e9afdf1"


def check_idle(cf, states):
    active = states.list_executions(stateMachineArn=ARN,statusFilter="RUNNING",maxResults=100)
    if active.get("executions"):
        raise RuntimeError("Image executions currently running; refuse code update")
    table = cf.describe_stack_resource(StackName=STACK,LogicalResourceId="JobsTable")["StackResourceDetail"]["PhysicalResourceId"]
    ddb = boto3.client("dynamodb",region_name=REGION)
    args={"TableName":table,"ProjectionExpression":"#s",
          "ExpressionAttributeNames":{"#s":"status"}}
    while True:
        resp=ddb.scan(**args)
        if any(item.get("status",{}).get("S") == "GENERATING_IMAGES" for item in resp.get("Items",[])):
            raise RuntimeError("Image jobs still generating; refuse update")
        if not resp.get("LastEvaluatedKey"):
            break
        args["ExclusiveStartKey"]=resp["LastEvaluatedKey"]


def main():
    if os.environ.get("IMAGE_DIAGNOSTIC_APPROVAL") != "DEPLOY_IMAGE_DIAGNOSTIC_ONLY":
        raise RuntimeError("Diagnostic deployment not authorised")
    cf=boto3.client("cloudformation",region_name=REGION)
    s3=boto3.client("s3",region_name=REGION)
    lam=boto3.client("lambda",region_name=REGION)
    states=boto3.client("stepfunctions",region_name=REGION)
    events=boto3.client("events",region_name=REGION)
    source=Path("/tmp/afl-image-diagnostic")
    item=json.loads((source/"dist/release/manifest.json").read_text())["artifacts"]["image-worker"]
    blob=(source/item["local_file"]).read_bytes()
    digest=hashlib.sha256(blob).hexdigest()
    if digest != item["sha256_hex"] or item["s3_key"] != f"releases/sha256/{digest}/image-worker.zip":
        raise RuntimeError("Invalid immutable image package")

    stack=cf.describe_stacks(StackName=STACK)["Stacks"][0]
    if stack["StackStatus"] != "UPDATE_COMPLETE":
        raise RuntimeError("Backend stack not healthy")
    params={p["ParameterKey"]:p.get("ParameterValue") for p in stack["Parameters"]}
    fn=lam.get_function_configuration(FunctionName=TARGET)
    if fn.get("State")!="Active" or fn.get("LastUpdateStatus")!="Successful":
        raise RuntimeError("Image Lambda not active")
    if fn.get("Environment",{}).get("Variables",{}).get("GOOGLE_IMAGE_MODEL") != "gemini-3-pro-image":
        raise RuntimeError("Current worker not Google model; refusing")
    machine=states.describe_state_machine(stateMachineArn=ARN)
    definition=json.loads(machine["definition"])
    if (machine.get("status") != "ACTIVE"
        or definition["States"]["ParallelScenes"]["MaxConcurrency"] != 3
        or definition["States"]["ParallelScenes"]["ItemProcessor"]["StartAt"] != "Generate"):
        raise RuntimeError("Current Step Functions unexpected")
    check_idle(cf,states)
    original_template=cf.get_template(StackName=STACK,TemplateStage="Original")["TemplateBody"]
    if not isinstance(original_template,str):
        raise RuntimeError("Original CloudFormation template was not text")
    report={"source_sha":SOURCE_SHA,"old_lambda_sha256":fn.get("CodeSha256"),
            "old_artifact_key":params["ImageWorkerArtifactKey"],
            "new_artifact_key":item["s3_key"],"status":"STARTED","executed":False}
    def save():
        Path("image-diagnostic-deploy-report.json").write_text(json.dumps(report,indent=2,default=str)+"\n")
    save()
    key=item["s3_key"]
    bucket=params["ArtifactBucketName"]
    try:
        obj=s3.get_object(Bucket=bucket,Key=key)
    except ClientError as e:
        if e.response.get("Error",{}).get("Code") not in ("404","NoSuchKey"): raise
        s3.put_object(Bucket=bucket,Key=key,Body=blob,ContentType="application/zip",
                      IfNoneMatch="*")
    else:
        with obj["Body"] as body:
            if hashlib.sha256(body.read()).hexdigest()!=digest:
                raise RuntimeError("Image artifact immutable S3 object mismatch")
    proposed=dict(params,ImageWorkerArtifactKey=key)
    if {k for k in params if params[k]!=proposed[k]} != {"ImageWorkerArtifactKey"}:
        raise RuntimeError("Only image-worker artifact parameter may change")
    name="afl-image-diagnostic-"+os.environ["GITHUB_RUN_ID"]
    created=False
    executed=False
    try:
        cf.create_change_set(StackName=STACK,ChangeSetName=name,ChangeSetType="UPDATE",
            TemplateBody=original_template,
            Parameters=[{"ParameterKey":k,"ParameterValue":v} for k,v in sorted(proposed.items())],
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Image worker diagnostic code only - no paid generation")
        created=True
        for _ in range(90):
            change=cf.describe_change_set(StackName=STACK,ChangeSetName=name)
            if change["Status"] in ("CREATE_COMPLETE","FAILED"): break
            time.sleep(2)
        else: raise RuntimeError("Change-set creation timeout")
        results=list(change.get("Changes",[]))
        next_token=change.get("NextToken")
        while next_token:
            page=cf.describe_change_set(StackName=STACK,ChangeSetName=name,NextToken=next_token)
            if page.get("ChangeSetId")!=change.get("ChangeSetId") or page.get("StackId")!=change.get("StackId"):
                raise RuntimeError("Paginated CFN change-set identity changed")
            results+=page.get("Changes",[])
            next_token=page.get("NextToken")
        expected={"ImageWorkerLambda"}
        permission_ok=verify_reconcile_permission(cf,lam,events,STACK,results)
        issues=validate(dict(change,Changes=results),expected,verified_reconcile_permission=permission_ok)
        extraneous=[
            r["ResourceChange"]["LogicalResourceId"]
            for r in results
            if r["ResourceChange"]["LogicalResourceId"] not in expected
            and not (dependency_only(r["ResourceChange"]) or
                expected_reconcile_permission(r["ResourceChange"],permission_ok))
        ]
        if extraneous: issues.append("Unexpected resource modifications: "+str(extraneous))
        actual={r["ResourceChange"]["LogicalResourceId"] for r in results}
        if "ImageWorkerLambda" not in actual: issues.append("Missing image-worker code change")
        report["change_resources"]=sorted(actual)
        report["preview_issues"]=issues
        save()
        if issues: raise RuntimeError("Unsafe image-only change set; not executed")
        check_idle(cf,states)
        cf.execute_change_set(StackName=STACK,ChangeSetName=name)
        executed=True
        report["executed"]=True
        save()
        cf.get_waiter("stack_update_complete").wait(StackName=STACK,WaiterConfig={"Delay":10,"MaxAttempts":90})
        after=lam.get_function_configuration(FunctionName=TARGET)
        expected_sha=base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        if (after.get("CodeSha256")!=expected_sha or after.get("State")!="Active"
            or after.get("LastUpdateStatus")!="Successful"
            or after.get("MemorySize")!=1024):
            raise RuntimeError("Image Lambda failed diagnostic package verification")
        machine=states.describe_state_machine(stateMachineArn=ARN)
        definition=json.loads(machine["definition"])
        if (machine.get("status")!="ACTIVE"
            or definition["States"]["ParallelScenes"]["MaxConcurrency"]!=3
            or definition["States"]["ParallelScenes"]["ItemProcessor"]["StartAt"]!="Generate"):
            raise RuntimeError("Step Functions unexpectedly modified")
        report["status"]="DEPLOYED_AND_VERIFIED"
        report["new_lambda_sha256"]=after["CodeSha256"]
        save()
        print("GOOGLE_IMAGE_DIAGNOSTIC_DEPLOY="+json.dumps(report,sort_keys=True))
    except Exception:
        report["status"]="FAILED"
        save()
        if executed:
            try:
                current=cf.describe_stacks(StackName=STACK)["Stacks"][0]
                if current["StackStatus"]=="UPDATE_COMPLETE":
                    rollback="afl-image-diagnostic-revert-"+os.environ["GITHUB_RUN_ID"]
                    cf.create_change_set(StackName=STACK,ChangeSetName=rollback,ChangeSetType="UPDATE",
                        TemplateBody=original_template,
                        Parameters=[{"ParameterKey":k,"ParameterValue":v} for k,v in sorted(params.items())],
                        Capabilities=["CAPABILITY_NAMED_IAM"],Description="Revert Google image diagnostic ZIP")
                    for _ in range(90):
                        state=cf.describe_change_set(StackName=STACK,ChangeSetName=rollback)
                        if state["Status"] in ("CREATE_COMPLETE","FAILED"): break
                        time.sleep(2)
                    if state["Status"]!="CREATE_COMPLETE": raise RuntimeError("Diagnostic rollback preview failed")
                    cf.execute_change_set(StackName=STACK,ChangeSetName=rollback)
                    cf.get_waiter("stack_update_complete").wait(StackName=STACK,WaiterConfig={"Delay":10,"MaxAttempts":90})
                    report["rollback"]="RESTORED"
                else:
                    report["rollback"]="STACK_"+current["StackStatus"]
            except Exception as exc:
                report["rollback"]="FAILED_"+type(exc).__name__
            save()
        raise
    finally:
        if created and not executed:
            cf.delete_change_set(StackName=STACK,ChangeSetName=name)

if __name__=="__main__":
    main()
