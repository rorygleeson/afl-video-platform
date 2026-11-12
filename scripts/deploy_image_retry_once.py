#!/usr/bin/env python3
"""One-time scoped image-retry deployment. No calls to Google or paid job endpoints.
Refuse drift, replacements, extra resources, active jobs, or changed object baselines.
"""
import hashlib
import json
import os
import subprocess
import time
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
import boto3
from botocore.exceptions import ClientError
from validate_change_set import validate
from preview_release_change_set import verify_reconcile_permission

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
BACKEND = "afl-app-dev-backend"
FRONTEND = "afl-app-dev-frontend"
ARTIFACT_BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
IMAGE_MACHINE = "afl-app-dev-scene1-pilot"
BASE = "213ea884023601f8de5156a8e469116f4b4c0a65^"
EXPECTED = {
    "ApiLambda", "ImageWorkerLambda", "ImageStateMachine",
    "ProxyAny", "ImageStateMachineRole", "ImageReconcileSchedule",
    "ImageReconcileInvokePermission"
}
CHANGED = {"ApiLambda", "ImageWorkerLambda", "ImageStateMachine"}


def assert_live_source(lam, name, files):
    live = lam.get_function(FunctionName=name)
    archive = urllib.request.urlopen(live["Code"]["Location"], timeout=60).read()
    with zipfile.ZipFile(BytesIO(archive)) as z:
        for file, local in files.items():
            old = subprocess.check_output(["git", "show", BASE + ":" + local])
            if z.read(file) != old:
                raise RuntimeError("STOP: unexpected live code drift at " + name + "/" + file)
    print("LIVE_BASELINE_MATCH", name, flush=True)


def assert_current_frontend(s3, bucket):
    current = s3.get_object(Bucket=bucket, Key="app.js")["Body"].read()
    previous = subprocess.check_output(["git", "show", BASE + ":frontend/app.js"])
    if current != previous:
        # The one previously identified live-only difference is a legacy
        # status phrase from Replicate -> Google cutover; never allow any other drift.
        corrected = current.replace(b"Starting three Replicate reference images",
                                    b"Starting three Google reference images")
        if corrected != previous:
            raise RuntimeError("STOP: frontend contains drift beyond the reviewed legacy provider label")
        print("LIVE_FRONTEND_ONLY_REVIEWED_REPLICATE_LABEL_DRIFT", flush=True)
    print("LIVE_FRONTEND_BASELINE_MATCH", flush=True)


def main():
    sts = boto3.client("sts", region_name=REGION)
    ident = sts.get_caller_identity()
    assert ident["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in ident["Arn"]
    cf = boto3.client("cloudformation", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    sf = boto3.client("stepfunctions", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    events = boto3.client("events", region_name=REGION)
    cdn = boto3.client("cloudfront")
    before = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
    assert before["StackStatus"] == "UPDATE_COMPLETE"
    assert_live_source(lam, "afl-app-dev-api", {
        "handler.py": "src/api/handler.py",
        "video_prompts.py": "src/api/video_prompts.py"
    })
    assert_live_source(lam, "afl-app-dev-image-worker", {
        "handler.py": "src/image_worker/handler.py",
        "google_direct.py": "src/image_worker/google_direct.py"
    })
    sm = cf.describe_stack_resource(StackName=BACKEND, LogicalResourceId="ImageStateMachine")["StackResourceDetail"]["PhysicalResourceId"]
    assert sm.endswith(":stateMachine:" + IMAGE_MACHINE)
    if sf.list_executions(stateMachineArn=sm, statusFilter="RUNNING", maxResults=20)["executions"]:
        raise RuntimeError("STOP: existing paid image workflow still running")
    print("NO_ACTIVE_IMAGE_EXECUTIONS", flush=True)
    fstack = cf.describe_stacks(StackName=FRONTEND)["Stacks"][0]
    out = {v["OutputKey"]: v["OutputValue"] for v in fstack.get("Outputs", [])}
    bucket = out["FrontendBucketName"]
    distribution = out["DistributionId"]
    assert bucket == "afl-app-dev-frontend-134387458447-ap-southeast-2"
    assert out["FrontendUrl"] == "https://d9i3mlqvt0ks6.cloudfront.net"
    assert_current_frontend(s3, bucket)
    manifest = json.loads(Path("dist/release/manifest.json").read_text())
    selected = {n: manifest["artifacts"][n] for n in ("api", "image-worker")}
    params = {x["ParameterKey"]: x["ParameterValue"] for x in before["Parameters"]}
    assert set(params).issuperset({"LambdaArtifactKey", "ImageWorkerArtifactKey"})
    params["LambdaArtifactKey"] = selected["api"]["s3_key"]
    params["ImageWorkerArtifactKey"] = selected["image-worker"]["s3_key"]
    print("PARAMETER_CHANGE_ONLY", list(sorted(["LambdaArtifactKey", "ImageWorkerArtifactKey"])), flush=True)
    name = "afl-image-retry-" + os.environ["GITHUB_RUN_ID"]
    created = False
    executed = False
    try:
        cf.create_change_set(
            StackName=BACKEND, ChangeSetName=name, ChangeSetType="UPDATE",
            TemplateBody=Path("infrastructure/app/backend.yaml").read_text(),
            Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in sorted(params.items())],
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Guarded image 429 retry deployment only")
        created = True
        for _ in range(75):
            result = cf.describe_change_set(StackName=BACKEND, ChangeSetName=name)
            if result["Status"] in ("CREATE_COMPLETE", "FAILED"):
                break
            time.sleep(2)
        assert result["Status"] == "CREATE_COMPLETE", result.get("StatusReason")
        changes = list(result.get("Changes", []))
        while result.get("NextToken"):
            result = cf.describe_change_set(StackName=BACKEND, ChangeSetName=name, NextToken=result["NextToken"])
            changes += result.get("Changes", [])
        diff = [x["ResourceChange"] for x in changes]
        names = {x["LogicalResourceId"] for x in diff}
        print("PROPOSED_RESOURCE_IDS", sorted(names), flush=True)
        if names != EXPECTED:
            raise RuntimeError("STOP: unexpected CFN change set resources; differences " + str(sorted(names ^ EXPECTED)))
        if any(x["Action"] != "Modify" for x in diff):
            raise RuntimeError("STOP: change set includes create/delete")
        for x in diff:
            if x["LogicalResourceId"] in CHANGED and x.get("Replacement") != "False":
                raise RuntimeError("STOP: core resource replacement")
        try:
            permission_ok = verify_reconcile_permission(cf, lam, events, BACKEND, changes)
        except ClientError as exc:
            raise RuntimeError("STOP: cannot verify EventBridge invocation permission: "+exc.response["Error"]["Code"]) from None
        issues = validate({"Status": "CREATE_COMPLETE", "Changes": changes},
                          CHANGED, verified_reconcile_permission=permission_ok)
        if issues:
            raise RuntimeError("STOP: CloudFormation change set guard: " + "; ".join(issues))
        print("CFN_GUARDS_PASSED", "reconcile_permission_verified="+str(permission_ok), flush=True)
        # Upload packages only after the full resource safety gate passes.
        for kind, entry in selected.items():
            payload = Path(entry["local_file"]).read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            assert digest == entry["sha256_hex"]
            key = entry["s3_key"]
            assert key == f"releases/sha256/{digest}/{kind}.zip"
            try:
                obj = s3.get_object(Bucket=ARTIFACT_BUCKET, Key=key)
            except s3.exceptions.NoSuchKey:
                s3.put_object(Bucket=ARTIFACT_BUCKET, Key=key, Body=payload,
                              ContentType="application/zip", Metadata={"sha256": digest}, IfNoneMatch="*")
                obj = s3.get_object(Bucket=ARTIFACT_BUCKET, Key=key)
            if hashlib.sha256(obj["Body"].read()).hexdigest() != digest:
                raise RuntimeError("STOP: S3 artifact digest mismatch: "+kind)
            print("IMMUTABLE_ARTIFACT_VERIFIED", kind, flush=True)
        # The change set is retained for this exact, same-job execution only.
        cf.execute_change_set(ChangeSetName=name, StackName=BACKEND)
        executed = True
        print("CHANGESET_EXECUTED", flush=True)
        cf.get_waiter("stack_update_complete").wait(StackName=BACKEND,
                   WaiterConfig={"Delay": 10, "MaxAttempts": 72})
        after = cf.describe_stacks(StackName=BACKEND)["Stacks"][0]
        assert after["StackStatus"] == "UPDATE_COMPLETE"
        postparams = {x["ParameterKey"]: x["ParameterValue"] for x in after["Parameters"]}
        assert postparams == params
        for func, kind in [("afl-app-dev-api","api"), ("afl-app-dev-image-worker","image-worker")]:
            blob = urllib.request.urlopen(lam.get_function(FunctionName=func)["Code"]["Location"], timeout=60).read()
            with zipfile.ZipFile(BytesIO(blob)) as z:
                assert z.testzip() is None
                assert (b"RETRY_WAIT" in z.read("google_direct.py")) if kind == "image-worker" else (b"retryCount" in z.read("handler.py"))
            print("LIVE_LAMBDA_VERIFIED",func,flush=True)
        state = sf.describe_state_machine(stateMachineArn=sm)
        assert "WaitForGoogleCapacity" in state["definition"]
        assert "AssessImageResult" in state["definition"]
        print("LIVE_STATE_MACHINE_VERIFIED",flush=True)
        # Only the browser JS changed; leave HTML/CSS untouched.
        new_js = Path("frontend/app.js").read_bytes()
        s3.put_object(Bucket=bucket,Key="app.js",Body=new_js,
                      ContentType="application/javascript",CacheControl="no-cache")
        stored = s3.get_object(Bucket=bucket,Key="app.js")["Body"].read()
        assert stored == new_js
        invalidation=cdn.create_invalidation(DistributionId=distribution,
          InvalidationBatch={"Paths":{"Quantity":1,"Items":["/app.js"]},
            "CallerReference":"image-retry-"+os.environ["GITHUB_RUN_ID"]})
        print("FRONTEND_DEPLOYED_AND_INVALIDATED",invalidation["Invalidation"]["Id"],flush=True)
        print("DEPLOYMENT_SUCCEEDED",flush=True)
    finally:
        if created and not executed:
            cf.delete_change_set(StackName=BACKEND,ChangeSetName=name)
            print("UNEXECUTED_CHANGESET_DELETED",flush=True)

if __name__ == "__main__":
    main()
