#!/usr/bin/env python3
"""Publish Google provider audit diagnostics UI after verifying CFN worker/API.

Safe to run after worker and API updates succeeded but previous UI publish
stopped on frontend stack CREATE_COMPLETE. No Lambda, CFN, paid model edits.
"""
import base64
import hashlib
import io
from pathlib import Path
import zipfile

import boto3

REGION="ap-southeast-2"
ACCOUNT="134387458447"

def sha(raw):
    return base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii")

def main():
    session=boto3.session.Session(region_name=REGION)
    cf=session.client("cloudformation")
    s3=session.client("s3")
    lam=session.client("lambda")
    sts=session.client("sts")
    who=sts.get_caller_identity()
    assert who["Account"]==ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in who["Arn"]
    for stack_name,param,function,local in (
        ("afl-app-dev-google-video","GoogleWorkerArtifactKey",
         "afl-app-dev-google-omni-worker","src/google_video_worker/handler.py"),
        ("afl-app-dev-backend","LambdaArtifactKey",
         "afl-app-dev-api","src/api/handler.py"),
    ):
        stack=cf.describe_stacks(StackName=stack_name)["Stacks"][0]
        assert stack["StackStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")
        params={p["ParameterKey"]:p["ParameterValue"] for p in stack["Parameters"]}
        assert params["PaidVideoEnabled"]=="true"
        artifact=s3.get_object(Bucket=params["ArtifactBucketName"],
                               Key=params[param])["Body"].read()
        worker=lam.get_function_configuration(FunctionName=function)
        assert worker["State"]=="Active" and worker["LastUpdateStatus"]=="Successful"
        assert worker["CodeSha256"]==sha(artifact),"CFN/live Lambda drift"
        if function.endswith("omni-worker"):
            assert worker["Timeout"]==300
        with zipfile.ZipFile(io.BytesIO(artifact)) as z:
            assert z.testzip() is None
            assert z.read("handler.py")==Path(local).read_bytes(),(
                "Live Lambda handler differs from expected audited source"
            )
        print("VERIFIED_CFN_CODE",function,flush=True)

    frontend=cf.describe_stacks(StackName="afl-app-dev-frontend")["Stacks"][0]
    assert frontend["StackStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")
    outputs={o["OutputKey"]:o["OutputValue"] for o in frontend["Outputs"]}
    bucket=outputs["FrontendBucketName"]
    distribution=outputs["DistributionId"]
    assert bucket==f"afl-app-dev-frontend-{ACCOUNT}-{REGION}"
    js=Path("frontend/app.js").read_bytes()
    assert b"RECORDED_PROVIDER_FAILURE" in js
    assert b"providerFailureObservedAt" in js
    s3.put_object(Bucket=bucket,Key="app.js",Body=js,
                  ContentType="application/javascript; charset=utf-8",
                  CacheControl="max-age=60, must-revalidate")
    assert s3.get_object(Bucket=bucket,Key="app.js")["Body"].read()==js
    session.client("cloudfront").create_invalidation(
        DistributionId=distribution,
        InvalidationBatch={
            "Paths":{"Quantity":1,"Items":["/app.js"]},
            "CallerReference":"google-audit-ui-"+__import__("os").environ["GITHUB_RUN_ID"],
        })
    print("GOOGLE_AUDIT_UI_PUBLISHED: original Google error fields shown in diagnostics",flush=True)
    print("NO_PAID_GOOGLE_CALLS_OR_RETRIES",flush=True)

if __name__=="__main__":
    main()
