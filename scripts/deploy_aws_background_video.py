#!/usr/bin/env python3
"""Release AWS-owned background video progression using guarded CloudFormation.

No Google calls, paid generation, model setting changes or direct
Lambda UpdateFunctionCode/Configuration calls. Fail closed on CFN surprises.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION="ap-southeast-2"
ACCOUNT="134387458447"
ROLE=f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
BUCKET="afl-video-bootstrap-artifactbucket-xiuriidgyft4"
PLANS=(
    ("api","afl-app-dev-backend","LambdaArtifactKey",
     "ApiLambda","afl-app-dev-api"),
)

def digest(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")

def new_package(kind, previous):
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(previous)) as prior:
        assert prior.testzip() is None
        names=prior.namelist()
        assert names.count("handler.py")==1
        if kind=="google":
            source=Path("src/google_video_worker/handler.py").read_bytes()
            assert b"def diagnose_failed_interaction(" in source
            assert b"PAID_SCENE_ATTEMPT_MISMATCH" in source
            assert b"attemptNumber" in source
            assert b"timeout=150" in source
            overrides={"handler.py":source}
        else:
            source=Path("src/api/handler.py").read_bytes()
            prompts=Path("src/api/video_prompts.py").read_bytes()
            assert b"def retry_failed_real_scene(" in source
            assert b"confirmRetryScene" in source
            assert b"RECORDED_PROVIDER_FAILURE" in source
            assert names.count("video_prompts.py")==1
            overrides={"handler.py":source,"video_prompts.py":prompts}
        with zipfile.ZipFile(output,"w") as fresh:
            for member in prior.infolist():
                fresh.writestr(member,overrides.get(member.filename,prior.read(member.filename)))
    packed=output.getvalue()
    with zipfile.ZipFile(io.BytesIO(packed)) as verify:
        assert verify.testzip() is None
        for name,payload in overrides.items():
            assert verify.read(name)==payload
        # Every dependency, file name, permission and unchanged member is preserved.
        assert set(verify.namelist())==set(names)
    return packed

def changes(cf,stack,name,parameters):
    cf.create_change_set(
        StackName=stack,ChangeSetName=name,ChangeSetType="UPDATE",
        UsePreviousTemplate=True,
        Parameters=[{"ParameterKey":k,**({"ParameterValue":parameters["new"][k]} if k in parameters["new"]
                    else {"UsePreviousValue":True})}
                    for k,v in parameters["old"].items()],
        RoleARN=ROLE,Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Sequential scene API gate code only; no Google calls",
    )
    cf.get_waiter("change_set_create_complete").wait(
        StackName=stack,ChangeSetName=name,WaiterConfig={"Delay":5,"MaxAttempts":36})
    d=cf.describe_change_set(StackName=stack,ChangeSetName=name)
    assert not d.get("NextToken"),"Paginated change set"
    return [e["ResourceChange"] for e in d["Changes"]]

def review(kind,items):
    # Only the worker/API's code and dynamically referenced stable ARNs
    # may change; role/policy/paid mode/Step Functions definitions must not.
    assert items,"No update found"
    ids={x["LogicalResourceId"] for x in items}
    allowed={
        "google":{"GoogleWorkerLambda","DeveloperPreflightInvokePermission",
                  "GoogleStateMachineRole","GoogleStateMachine"},
        "api":{"ApiLambda","InvokePermission","ProxyAny"},
    }[kind]
    assert ids<=allowed and ("GoogleWorkerLambda" if kind=="google" else "ApiLambda") in ids, (
        f"Unexpected resource changes: {sorted(ids)}")
    for c in items:
        name=c["LogicalResourceId"]
        print("CHANGE",kind,name,c["Action"],c.get("Replacement"),c.get("Details"),flush=True)
        assert c["Action"]=="Modify", "Create/delete denied"
        direct=name==("GoogleWorkerLambda" if kind=="google" else "ApiLambda")
        if direct:
            assert c["ResourceType"]=="AWS::Lambda::Function"
            assert c.get("Replacement") in (False,"False")
            for d in c.get("Details",[]):
                t=d.get("Target",{})
                assert t.get("Attribute")=="Properties" and t.get("Name")=="Code"
        else:
            assert c.get("Replacement") in (False,"False","Conditional")
            for d in c.get("Details",[]):
                t=d.get("Target",{})
                assert d.get("Evaluation")=="Dynamic"
                assert d.get("ChangeSource") in ("ResourceAttribute","ResourceReference")
                if kind=="google":
                    assert d.get("CausingEntity") in (
                        "GoogleWorkerLambda.Arn","GoogleStateMachineRole.Arn")
                    assert t.get("Name") in (
                        "FunctionName","Policies","DefinitionString","RoleArn")
                else:
                    assert d.get("CausingEntity") in (
                        "ApiLambda.Arn","RestApi.RootResourceId","ApiLambda")
                    assert t.get("Name") in ("FunctionName","Integration")
    print("SAFE_CF_CHANGESET",kind,flush=True)

def install_completion_rule(cf, run):
    """Create the isolated EventBridge rule stack only after live API code verifies.

    No changes to Google model, SFN, IAM roles, DynamoDB table, existing
    application stacks, or customer media. Reject surprises in the changeset.
    """
    stack_name="afl-app-dev-video-sequence-events"
    expected={"CompletedGoogleVideoExecution":"AWS::Events::Rule",
              "AllowVideoSequenceEvent":"AWS::Lambda::Permission"}
    template=Path("infrastructure/app/video-sequence-events.yaml").read_text()
    assert "aws.states" in template and "SUCCEEDED" in template
    assert "afl-app-dev-google-omni" in template and "afl-app-dev-api" in template
    try:
        current=cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    except ClientError as exc:
        code=exc.response.get("Error",{}).get("Code")
        message=exc.response.get("Error",{}).get("Message","")
        if code!="ValidationError" or "does not exist" not in message:
            raise
    else:
        assert current["StackStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")
        live_template=cf.get_template(StackName=stack_name)["TemplateBody"]
        assert isinstance(live_template,str) and live_template.strip()==template.strip(), (
            "EventBridge stack source changed; require isolated reviewed change")
        for logical,resource_type in expected.items():
            res=cf.describe_stack_resource(StackName=stack_name,LogicalResourceId=logical)["StackResourceDetail"]
            assert res["ResourceType"]==resource_type
            assert res["ResourceStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")
        print("AWS_VIDEO_COMPLETION_EVENTS_ALREADY_INSTALLED_VERIFIED",flush=True)
        return
    name="aws-background-events-"+run
    cf.create_change_set(
        StackName=stack_name,ChangeSetName=name,ChangeSetType="CREATE",
        TemplateBody=template,RoleARN=ROLE,Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Only Step Functions SUCCEEDED completion events to existing AWS API Lambda")
    cf.get_waiter("change_set_create_complete").wait(
        StackName=stack_name,ChangeSetName=name,WaiterConfig={"Delay":5,"MaxAttempts":36})
    plan=cf.describe_change_set(StackName=stack_name,ChangeSetName=name)
    assert not plan.get("NextToken"),"Paginated event stack changeset refused"
    changes={x["ResourceChange"]["LogicalResourceId"]:x["ResourceChange"]
             for x in plan["Changes"]}
    assert set(changes)==set(expected),f"Unexpected EventBridge resources {sorted(changes)}"
    for logical,kind in expected.items():
        change=changes[logical]
        assert change["Action"]=="Add" and change["ResourceType"]==kind
        assert change.get("Replacement") in (None,False,"False")
    print("SAFE_EVENTBRIDGE_COMPLETION_CHANGESET",sorted(changes),flush=True)
    cf.execute_change_set(StackName=stack_name,ChangeSetName=name)
    cf.get_waiter("stack_create_complete").wait(
        StackName=stack_name,WaiterConfig={"Delay":10,"MaxAttempts":60})
    result=cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    assert result["StackStatus"]=="CREATE_COMPLETE"
    for logical,kind in expected.items():
        res=cf.describe_stack_resource(StackName=stack_name,LogicalResourceId=logical)["StackResourceDetail"]
        assert res["ResourceType"]==kind and res["ResourceStatus"]=="CREATE_COMPLETE"
    print("AWS_VIDEO_COMPLETION_EVENTS_CREATED_AND_VERIFIED",flush=True)


def main():
    from datetime import datetime, timezone
    sess=boto3.session.Session(region_name=REGION)
    cf=sess.client("cloudformation")
    lam=sess.client("lambda")
    s3=sess.client("s3")
    sts=sess.client("sts")
    sf=sess.client("stepfunctions")
    ident=sts.get_caller_identity()
    assert ident["Account"]==ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in ident["Arn"]
    run=os.environ["GITHUB_RUN_ID"]
    prepared=[]
    for kind,stack,parameter,logical,function in PLANS:
        before=cf.describe_stacks(StackName=stack)["Stacks"][0]
        assert before["StackStatus"]=="UPDATE_COMPLETE" and before["RoleARN"]==ROLE
        old={p["ParameterKey"]:p["ParameterValue"] for p in before["Parameters"]}
        assert old["ArtifactBucketName"]==BUCKET
        assert old["PaidVideoEnabled"]=="true","Paid video mode changed"
        current=lam.get_function_configuration(FunctionName=function)
        assert current["State"]=="Active" and current["LastUpdateStatus"]=="Successful"
        if kind=="google":
            assert current["Timeout"]==300,"Google worker timeout unexpectedly changed"
            machine_arn=cf.describe_stack_resource(StackName=stack,LogicalResourceId="GoogleStateMachine")["StackResourceDetail"]["PhysicalResourceId"]
            state_before=sf.describe_state_machine(stateMachineArn=machine_arn)
            machine_def=state_before["definition"]
            machine_role=state_before["roleArn"]
            states=json.loads(machine_def)["States"]
            assert "Retry" not in states["Submit"],"Paid POST retry unexpectedly configured"
            assert states["PollVideo"]["Retry"]==[{
                "ErrorEquals":["States.TaskFailed"],
                "IntervalSeconds":10,"MaxAttempts":3,"BackoffRate":2
            }]
            assert '"WaitScene2"' in machine_def and '"WaitScene3"' in machine_def
        else:
            machine_arn=machine_def=machine_role=None
        oldzip=s3.get_object(Bucket=BUCKET,Key=old[parameter])["Body"].read()
        assert digest(oldzip)==current["CodeSha256"],"Live Lambda/CFN artifact drift"
        # If the API Lambda is already deployed and verified, a retry of this
        # guarded release publishes the frontend without a needless CFN update.
        # This matters after a partial success: CFN code was updated but UI
        # publication was interrupted. Never change Google worker resources.
        if kind=="api":
            with zipfile.ZipFile(io.BytesIO(oldzip)) as archive:
                assert archive.testzip() is None
                backend_matches = (
                    archive.read("handler.py")==Path("src/api/handler.py").read_bytes()
                    and archive.read("video_prompts.py")==Path("src/api/video_prompts.py").read_bytes()
                )
            if backend_matches:
                print("API_ALREADY_DEPLOYED_VERIFIED",flush=True)
                continue
        packed=new_package(kind,oldzip)
        new_key=f"releases/aws-background-video/{hashlib.sha256(packed).hexdigest()}/{kind}.zip"
        try:
            previous=s3.get_object(Bucket=BUCKET,Key=new_key)["Body"].read()
            assert previous==packed
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchKey","NotFound","404"): raise
            s3.put_object(Bucket=BUCKET,Key=new_key,Body=packed)
            assert s3.get_object(Bucket=BUCKET,Key=new_key)["Body"].read()==packed
        prepared.append({"kind":kind,"stack":stack,"parameter":parameter,"logical":logical,
                         "function":function,"old":old,"new":{parameter:new_key},
                         "expected":digest(packed),"original_sha":current["CodeSha256"],
                         "stack_id":before["StackId"],"machine_arn":machine_arn,
                         "machine_def":machine_def,"machine_role":machine_role})
    # Preflight BOTH CloudFormation change sets before making any runtime changes.
    for p in prepared:
        p["changeset"]=f"aws-background-video-{p['kind']}-{run}"
        try:
            c=changes(cf,p["stack"],p["changeset"],p)
            review(p["kind"],c)
        except Exception:
            for q in prepared:
                if q.get("changeset"):
                    try: cf.delete_change_set(StackName=q["stack"],ChangeSetName=q["changeset"])
                    except ClientError: pass
            raise
    for p in prepared:
        current_stack=cf.describe_stacks(StackName=p["stack"])["Stacks"][0]
        assert current_stack["StackId"]==p["stack_id"] and current_stack["StackStatus"]=="UPDATE_COMPLETE"
        current=lam.get_function_configuration(FunctionName=p["function"])
        assert current["CodeSha256"]==p["original_sha"]
        cf.execute_change_set(StackName=p["stack"],ChangeSetName=p["changeset"])
        print("CFN_UPDATE_SUBMITTED",p["kind"],flush=True)
        cf.get_waiter("stack_update_complete").wait(
            StackName=p["stack"],WaiterConfig={"Delay":10,"MaxAttempts":60})
        after=cf.describe_stacks(StackName=p["stack"])["Stacks"][0]
        assert after["StackStatus"]=="UPDATE_COMPLETE"
        params={x["ParameterKey"]:x["ParameterValue"] for x in after["Parameters"]}
        assert params[p["parameter"]]==p["new"][p["parameter"]]
        assert all(params[k]==v for k,v in p["old"].items() if k!=p["parameter"])
        live=lam.get_function_configuration(FunctionName=p["function"])
        assert live["CodeSha256"]==p["expected"] and live["LastUpdateStatus"]=="Successful"
        if p["kind"]=="google":
            assert live["Timeout"]==300
            now=sf.describe_state_machine(stateMachineArn=p["machine_arn"])
            assert now["definition"]==p["machine_def"],"Paid scene workflow modified unexpectedly"
            assert now["roleArn"]==p["machine_role"]
        print("CFN_DEPLOYED_VERIFIED",p["kind"],flush=True)

    # The live API must be ready before the completion event rule can invoke it.
    install_completion_rule(cf,run)

    # Release frontend only after the API and completion rule are verified. Existing
    # versioned S3 bucket and CloudFront distribution remain under IaC.
    frontend=cf.describe_stacks(StackName="afl-app-dev-frontend")["Stacks"][0]
    assert frontend["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")
    out={v["OutputKey"]:v["OutputValue"] for v in frontend["Outputs"]}
    bucket=out["FrontendBucketName"]
    distribution=out["DistributionId"]
    assert bucket==f"afl-app-dev-frontend-{ACCOUNT}-{REGION}"
    js=Path("frontend/app.js").read_bytes()
    html=Path("frontend/index.html").read_bytes()
    assert b"confirmPaidSequence" in js and b"submitNextSequentialScene" not in js
    assert b"in sequence" in html.lower() and b"retryVideoScene1" in html
    assert b"def video_sequence_event(" in Path("src/api/handler.py").read_bytes()
    assert all((f"retryVideoScene{n}".encode() in html) for n in (1,2,3))
    for key,data,ctype in (
            ("app.js",js,"application/javascript; charset=utf-8"),
            ("index.html",html,"text/html; charset=utf-8")):
        s3.put_object(Bucket=bucket,Key=key,Body=data,ContentType=ctype,
                      CacheControl="max-age=60, must-revalidate")
        assert s3.get_object(Bucket=bucket,Key=key)["Body"].read()==data
        print("FRONTEND_ASSET_VERIFIED",key,flush=True)
    cloudfront=sess.client("cloudfront")
    cloudfront.create_invalidation(
        DistributionId=distribution,
        InvalidationBatch={
            "Paths":{"Quantity":2,"Items":["/app.js","/index.html"]},
            "CallerReference":"aws-background-video-"+run
        })
    print("FRONTEND_PUBLISHED_VERIFIED",flush=True)
    print("AWS_BACKGROUND_VIDEO_RELEASE_COMPLETE: API + EventBridge + UI verified; Google worker/state-machine unchanged; no paid calls",flush=True)

if __name__=="__main__":
    main()
