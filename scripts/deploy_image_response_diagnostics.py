#!/usr/bin/env python3
"""Guarded additive image diagnostics release. No AI calls or state transitions."""
import base64
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from preview_release_change_set import verify_reconcile_permission
from validate_change_set import expected_reconcile_permission, dependency_only

REGION="ap-southeast-2"
ACCOUNT="134387458447"
ARTIFACT="afl-video-bootstrap-artifactbucket-xiuriidgyft4"
CFN_ROLE=f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
BACKEND="afl-app-dev-backend"
IMAGE_MACHINE=f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:afl-app-dev-scene1-pilot"


def sha_b64(blob):
    return base64.b64encode(hashlib.sha256(blob).digest()).decode()


def package(original, updates):
    """Keep all original package members, changing only approved Python files."""
    with zipfile.ZipFile(io.BytesIO(original)) as z:
        assert z.testzip() is None
        names=z.namelist()
        assert all(names.count(name)==1 for name in updates), (
            "Deployed ZIP missing expected handler(s)"
        )
        new=io.BytesIO()
        with zipfile.ZipFile(new,"w") as out:
            for entry in z.infolist():
                out.writestr(entry,updates.get(entry.filename,z.read(entry.filename)))
    result=new.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as z:
        assert z.testzip() is None
        assert z.namelist()==names
        for name,content in updates.items():
            assert z.read(name)==content
    return result


def active_images(states):
    result=states.list_executions(
        stateMachineArn=IMAGE_MACHINE,statusFilter="RUNNING",maxResults=100)
    assert not result.get("executions"), "Image generation is currently running; abort deployment"


def change_set(cf,lam,events,stack,updates,run):
    """Single atomic backend CFN change set for both Lambda packages."""
    name="image-response-diag-"+run
    cf.create_change_set(StackName=BACKEND,ChangeSetName=name,
        ChangeSetType="UPDATE",UsePreviousTemplate=True,RoleARN=CFN_ROLE,
        Parameters=[{"ParameterKey":p["ParameterKey"],
                     **({"ParameterValue":updates[p["ParameterKey"]]["key"]}
                        if p["ParameterKey"] in updates
                        else {"UsePreviousValue":True})}
                    for p in stack["Parameters"]],
        Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Allowlisted image diagnostics; no provider or workflow changes")
    cf.get_waiter("change_set_create_complete").wait(
        StackName=BACKEND,ChangeSetName=name,
        WaiterConfig={"Delay":5,"MaxAttempts":45})
    page=cf.describe_change_set(StackName=BACKEND,ChangeSetName=name)
    assert not page.get("NextToken")
    changes=[c["ResourceChange"] for c in page["Changes"]]
    verified_permission=verify_reconcile_permission(
        cf,lam,events,BACKEND,page["Changes"])
    logicals={"ImageWorkerLambda","ApiLambda"}
    ids={c["LogicalResourceId"] for c in changes}
    assert ids & logicals == {p["logical"] for p in updates.values()}, (
        "Missing expected Lambda code change"
    )
    # Allow only direct code modifications on the two worker/API Lambdas.
    # Any other resource must be a purely dynamic dependency that cannot
    # create/replace/modify direct configuration.
    for c in changes:
        rid=c["LogicalResourceId"]
        print("CHANGE",rid,c["Action"],c.get("Replacement"),c.get("Details"),flush=True)
        if expected_reconcile_permission(c, verified_permission):
            print("EXISTING_EVENTBRIDGE_PERMISSION_LIVE_VERIFIED",flush=True)
            continue
        if dependency_only(c):
            print("DEPENDENCY_ONLY_RESOURCE_VERIFIED",rid,flush=True)
            continue
        assert c["Action"]=="Modify" and c.get("Replacement") in ("False",False)
        for detail in c.get("Details",[]):
            target=detail.get("Target",{})
            assert target.get("Attribute")=="Properties"
            if rid in logicals:
                assert target.get("Name")=="Code", "Unexpected Lambda configuration edit"
            else:
                assert detail.get("Evaluation")=="Dynamic"
                assert detail.get("ChangeSource") in ("ResourceAttribute","ResourceReference")
                assert target.get("Name") in (
                    "Policies","Integration","RoleArn","DefinitionString",
                    "Definition","FunctionName","SourceArn")
    print("ATOMIC_IMAGE_DIAGNOSTIC_CHANGESET_SAFE",flush=True)
    return name


def main():
    session=boto3.Session(region_name=REGION)
    assert session.client("sts").get_caller_identity()["Account"]==ACCOUNT
    cf=session.client("cloudformation")
    lam=session.client("lambda")
    sf=session.client("stepfunctions")
    s3=session.client("s3")
    stack=cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    assert stack["StackStatus"]=="UPDATE_COMPLETE"
    assert stack["RoleARN"]==CFN_ROLE
    params={p["ParameterKey"]:p["ParameterValue"] for p in stack["Parameters"]}
    assert params["ArtifactBucketName"]==ARTIFACT
    assert params["PaidVideoEnabled"]=="true"
    active_images(sf)
    machine=sf.describe_state_machine(stateMachineArn=IMAGE_MACHINE)
    before=json.loads(machine["definition"])
    assert before["States"]["ParallelScenes"]["MaxConcurrency"]==1
    assert machine["status"]=="ACTIVE"
    machine_role=machine["roleArn"]
    plans=[
        ("image","ImageWorkerArtifactKey","ImageWorkerLambda",
         "afl-app-dev-image-worker",{
            "handler.py":Path("src/image_worker/handler.py").read_bytes(),
            "google_direct.py":Path("src/image_worker/google_direct.py").read_bytes()}),
        ("api","LambdaArtifactKey","ApiLambda","afl-app-dev-api",{
            "handler.py":Path("src/api/handler.py").read_bytes(),
            "video_prompts.py":Path("src/api/video_prompts.py").read_bytes()}),
    ]
    prepared=[]
    for kind,parameter,logical,fn,updates in plans:
        live=lam.get_function_configuration(FunctionName=fn)
        assert live["State"]=="Active" and live["LastUpdateStatus"]=="Successful"
        original=s3.get_object(Bucket=ARTIFACT,Key=params[parameter])["Body"].read()
        assert sha_b64(original)==live["CodeSha256"], "Lambda/CFN code drift"
        blob=package(original,updates)
        if sha_b64(blob)==live["CodeSha256"]:
            print("ALREADY_DEPLOYED",kind,flush=True)
            continue
        key=f"releases/image-response-diagnostics/{hashlib.sha256(blob).hexdigest()}/{kind}.zip"
        try:
            current=s3.get_object(Bucket=ARTIFACT,Key=key)["Body"].read()
            assert current==blob
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchKey","NotFound","404"):
                raise
            s3.put_object(Bucket=ARTIFACT,Key=key,Body=blob)
            assert s3.get_object(Bucket=ARTIFACT,Key=key)["Body"].read()==blob
        prepared.append(dict(kind=kind,param=parameter,logical=logical,
                             fn=fn,key=key,expected=sha_b64(blob),
                             oldsha=live["CodeSha256"]))
    run=os.environ["GITHUB_RUN_ID"]
    updates={p["param"]:p for p in prepared}
    name=None
    executed=False
    try:
        if prepared:
            name=change_set(cf,lam,session.client("events"),stack,updates,run)
            active_images(sf)
            current_machine=sf.describe_state_machine(stateMachineArn=IMAGE_MACHINE)
            assert json.loads(current_machine["definition"])==before
            for p in prepared:
                assert lam.get_function_configuration(FunctionName=p["fn"])["CodeSha256"]==p["oldsha"]
            cf.execute_change_set(StackName=BACKEND,ChangeSetName=name)
            executed=True
            cf.get_waiter("stack_update_complete").wait(
                StackName=BACKEND,WaiterConfig={"Delay":10,"MaxAttempts":90})
        after_stack=cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
        assert after_stack["StackStatus"]=="UPDATE_COMPLETE"
        expectedparams=dict(params)
        expectedparams.update({p["param"]:p["key"] for p in prepared})
        changed={p["ParameterKey"]:p["ParameterValue"] for p in after_stack["Parameters"]}
        assert changed==expectedparams,"Unexpected backend parameter change"
        for p in prepared:
            now=lam.get_function_configuration(FunctionName=p["fn"])
            assert now["CodeSha256"]==p["expected"] and now["LastUpdateStatus"]=="Successful"
            print("DIAGNOSTIC_LAMBDA_DEPLOYED_VERIFIED",p["kind"],flush=True)
        after=sf.describe_state_machine(stateMachineArn=IMAGE_MACHINE)
        assert json.loads(after["definition"])==before and after["roleArn"]==machine_role

        frontend=cf.describe_stacks(StackName="afl-app-dev-frontend")["Stacks"][0]
        assert frontend["StackStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")
        outputs={v["OutputKey"]:v["OutputValue"] for v in frontend["Outputs"]}
        bucket=outputs["FrontendBucketName"]
        assert bucket==f"afl-app-dev-frontend-{ACCOUNT}-{REGION}"
        for key,local,mime in (
            ("app.js","frontend/app.js","application/javascript; charset=utf-8"),
            ("index.html","frontend/index.html","text/html; charset=utf-8")):
            payload=Path(local).read_bytes()
            s3.put_object(Bucket=bucket,Key=key,Body=payload,ContentType=mime,
                          CacheControl="max-age=60, must-revalidate")
            assert s3.get_object(Bucket=bucket,Key=key)["Body"].read()==payload
            print("DIAGNOSTIC_FRONTEND_VERIFIED",key,flush=True)
        session.client("cloudfront").create_invalidation(
            DistributionId=outputs["DistributionId"],
            InvalidationBatch={"Paths":{"Quantity":2,"Items":["/app.js","/index.html"]},
                               "CallerReference":"image-outcome-diag-"+run})
        print("IMAGE_DIAGNOSTICS_RELEASE_COMPLETE: no Google calls or retry changes",flush=True)
    finally:
        if name and not executed:
            try:cf.delete_change_set(StackName=BACKEND,ChangeSetName=name)
            except ClientError:pass


if __name__=="__main__":
    main()
