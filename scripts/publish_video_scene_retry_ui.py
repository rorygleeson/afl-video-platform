#!/usr/bin/env python3
"""Publish only the verified scene-retry UI after code-only backend release.

No Lambda updates, CloudFormation updates or paid Google model calls.
"""
import base64
import hashlib
import io
import os
from pathlib import Path
import zipfile
import boto3

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
ARTIFACT_BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"


def sha(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def main():
    s = boto3.Session(region_name=REGION)
    sts, cf, lam, s3 = (s.client(name) for name in (
        "sts", "cloudformation", "lambda", "s3"))
    identity = sts.get_caller_identity()
    assert identity["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in identity["Arn"]
    for stack_name, artifact_param, function, source in (
        ("afl-app-dev-google-video", "GoogleWorkerArtifactKey",
         "afl-app-dev-google-omni-worker", "src/google_video_worker/handler.py"),
        ("afl-app-dev-backend", "LambdaArtifactKey",
         "afl-app-dev-api", "src/api/handler.py"),
    ):
        stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
        assert stack["StackStatus"] == "UPDATE_COMPLETE"
        params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
        assert params["ArtifactBucketName"] == ARTIFACT_BUCKET
        blob = s3.get_object(Bucket=ARTIFACT_BUCKET, Key=params[artifact_param])["Body"].read()
        live = lam.get_function_configuration(FunctionName=function)
        assert sha(blob) == live["CodeSha256"], "Live Lambda artifact drift"
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.testzip() is None
            assert archive.read("handler.py") == Path(source).read_bytes(), "Backend code mismatch"
        print("BACKEND_RETRY_CODE_VERIFIED", function, flush=True)

    frontend = cf.describe_stacks(StackName="afl-app-dev-frontend")["Stacks"][0]
    print("FRONTEND_STACK_STATUS", frontend["StackStatus"], flush=True)
    assert frontend["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")
    outputs = {v["OutputKey"]: v["OutputValue"] for v in frontend["Outputs"]}
    bucket, distribution = outputs["FrontendBucketName"], outputs["DistributionId"]
    assert bucket == f"afl-app-dev-frontend-{ACCOUNT}-{REGION}"
    js = Path("frontend/app.js").read_bytes()
    html = Path("frontend/index.html").read_bytes()
    assert b"confirmRetryScene" in js and b"retryAvailable" in js
    assert b"'retryVideoScene' + n" in js
    for n in (1, 2, 3):
        assert f'id="retryVideoScene{n}"'.encode() in html
    for key, data, content_type in (
        ("app.js", js, "application/javascript; charset=utf-8"),
        ("index.html", html, "text/html; charset=utf-8"),
    ):
        s3.put_object(
            Bucket=bucket, Key=key, Body=data,
            ContentType=content_type, CacheControl="max-age=60, must-revalidate"
        )
        actual = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        assert actual == data
        print("FRONTEND_SCENE_RETRY_FILE_VERIFIED", key, hashlib.sha256(actual).hexdigest(), flush=True)
    cloudfront = s.client("cloudfront")
    result = cloudfront.create_invalidation(
        DistributionId=distribution,
        InvalidationBatch={
            "Paths":{"Quantity":2,"Items":["/app.js","/index.html"]},
            "CallerReference":"video-scene-retry-ui-" + os.environ["GITHUB_RUN_ID"],
        })
    invalidation_id = result["Invalidation"]["Id"]
    print("CLOUDFRONT_INVALIDATION_SUBMITTED", invalidation_id, flush=True)
    cloudfront.get_waiter("invalidation_completed").wait(
        DistributionId=distribution, Id=invalidation_id,
        WaiterConfig={"Delay":10,"MaxAttempts":30})
    print("VIDEO_SCENE_RETRY_UI_PUBLISHED_VERIFIED_NO_PAID_CALLS", flush=True)


if __name__ == "__main__":
    main()
