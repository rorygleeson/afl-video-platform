#!/usr/bin/env python3
"""Guarded API + watchdog recovery-record release; no Google calls or workflow starts."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
ARTIFACT_BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
BASE_COMMIT = "871abc99313ab1a63ba7dff5012474ab2d466f27"
PLANS = (
    ("api", "afl-app-dev-backend", "afl-app-dev-api", "ApiLambda",
     "ArtifactBucketName", "LambdaArtifactKey", "src/api/handler.py"),
    ("watchdog", "afl-app-dev-handoff-watchdog", "afl-app-dev-handoff-watchdog",
     "WatchdogLambda", "ArtifactBucket", "ArtifactKey", "src/handoff_watchdog/handler.py"),
)


def digest(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def repack(old, before, after):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(old)) as z:
        if z.testzip() is not None or z.namelist().count("handler.py") != 1:
            raise ValueError("Unknown existing Lambda ZIP layout")
        if z.read("handler.py") != before:
            raise ValueError("LIVE HANDLER DRIFT: not exactly the pre-PR103 source")
        names = z.namelist()
        with zipfile.ZipFile(out, "w") as target:
            for member in z.infolist():
                target.writestr(member, after if member.filename == "handler.py" else z.read(member.filename))
    result = out.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as check:
        if check.testzip() is not None or check.namelist() != names or check.read("handler.py") != after:
            raise ValueError("Repacked artifact validation failed")
    return result


def inspected_changes(cf, stack, name, logical):
    first = cf.describe_change_set(StackName=stack, ChangeSetName=name)
    if first["Status"] != "CREATE_COMPLETE":
        raise ValueError("CloudFormation preview not complete")
    changes = list(first.get("Changes", []))
    token = first.get("NextToken")
    while token:
        page = cf.describe_change_set(StackName=stack, ChangeSetName=name, NextToken=token)
        changes += page.get("Changes", [])
        token = page.get("NextToken")
    descriptions = []
    seen_code = False
    for item in changes:
        r = item["ResourceChange"]
        ident = r["LogicalResourceId"]
        details = r.get("Details", [])
        # A stable API Lambda ARN is referenced by the existing proxy method.
        # CloudFormation may recalculate this Integration even for Code-only updates.
        # Allow only the known non-replacing dynamic reference, never a static
        # integration edit or an API Gateway replacement.
        proxy_dependency = (
            logical == "ApiLambda" and ident == "ProxyAny"
            and r.get("ResourceType") == "AWS::ApiGateway::Method"
            and r.get("Action") == "Modify" and r.get("Replacement") == "False"
            and bool(details) and all(
                d.get("Evaluation") == "Dynamic"
                and d.get("ChangeSource") in ("ResourceReference", "ResourceAttribute")
                and str(d.get("CausingEntity", "")).startswith("ApiLambda")
                and d.get("Target", {}).get("Attribute") == "Properties"
                and d.get("Target", {}).get("Name") == "Integration"
                and d.get("Target", {}).get("RequiresRecreation") in ("Never", None)
                for d in details)
        )
        if proxy_dependency:
            descriptions.append({"logical_id": ident, "action": "Modify",
                                 "property": "Integration", "verified_dynamic_api_reference": True})
            continue
        if (r.get("Action") != "Modify" or r.get("Replacement") != "False"
                or r.get("ResourceType") != "AWS::Lambda::Function" or ident != logical
                or not details or any(
                    d.get("Target", {}).get("Attribute") != "Properties"
                    or d.get("Target", {}).get("Name") != "Code"
                    or d.get("Target", {}).get("RequiresRecreation") not in ("Never", None)
                    for d in details)):
            raise ValueError("BLOCKED UNEXPECTED CLOUDFORMATION RESOURCE: " +
                             json.dumps({"logical_id": ident, "action": r.get("Action"),
                                         "replacement": r.get("Replacement"),
                                         "properties": [d.get("Target", {}).get("Name") for d in details]}))
        seen_code = True
        descriptions.append({"logical_id": ident, "action": "Modify", "property": "Code"})
    if not seen_code or len([x for x in changes if x["ResourceChange"]["LogicalResourceId"] == logical]) != 1:
        raise ValueError("Exactly one Lambda Code modification required per stack")
    return first["ChangeSetId"], descriptions


def reviewed_preview(run_id, current):
    if not run_id.isdigit() or not os.environ.get("GH_TOKEN"):
        raise ValueError("Prior preview run ID and GH_TOKEN required")
    repo = os.environ["GITHUB_REPOSITORY"]
    run = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repo}/actions/runs/{run_id}"], text=True))
    mode = (run.get("inputs") or {}).get("change_set_preview")
    if (run.get("event") != "workflow_dispatch" or run.get("conclusion") != "success"
            or run.get("head_sha") != os.environ["GITHUB_SHA"]
            or run.get("head_branch") != "main"
            or not str(run.get("path", "")).split("@", 1)[0].endswith(".github/workflows/release.yml")
            or mode not in (None, "recovery_preview")):
        raise ValueError("Reviewed run is not a successful same-commit recovery preview")
    with tempfile.TemporaryDirectory(prefix="recovery-preview-") as directory:
        subprocess.run(["gh", "run", "download", run_id, "--repo", repo,
                        "--name", f"recovery-preview-{run_id}", "--dir", directory], check=True)
        prior = json.loads((Path(directory) / "recovery-preview.json").read_text())
    if prior != current or prior.get("verdict") != "SAFE_RECOVERY_PREVIEW":
        raise ValueError("Reviewed preview differs from current AWS change set")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preview", "deploy"), required=True)
    parser.add_argument("--reviewed-preview-run-id", default="")
    args = parser.parse_args()
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Main branch required")
    if args.mode == "deploy" and os.environ.get("DEPLOYMENT_CONFIRMATION") != "DEPLOY_RECOVERY":
        raise ValueError("Exact DEPLOY_RECOVERY confirmation required")
    session = boto3.session.Session(region_name=REGION)
    cf, s3, lam, sts = (session.client(name) for name in
                        ("cloudformation", "s3", "lambda", "sts"))
    identity = sts.get_caller_identity()
    if identity["Account"] != ACCOUNT or ":assumed-role/afl-video-github-deploy-role/" not in identity["Arn"]:
        raise ValueError("Unexpected AWS identity")
    packages = []
    for index, (label, stack_name, function, logical, bucket_param, key_param, path) in enumerate(PLANS):
        stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
        if stack["StackStatus"] != "UPDATE_COMPLETE":
            raise ValueError("Stack not steady: " + label)
        params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
        if params[bucket_param] != ARTIFACT_BUCKET:
            raise ValueError("Unexpected artifact bucket")
        before = subprocess.check_output(["git", "show", f"{BASE_COMMIT}:{path}"])
        source = Path(path).read_bytes()
        compile(source, path, "exec")
        old = s3.get_object(Bucket=ARTIFACT_BUCKET, Key=params[key_param])["Body"].read()
        live = lam.get_function_configuration(FunctionName=function)
        if (live["State"] != "Active" or live["LastUpdateStatus"] != "Successful"
                or live["CodeSha256"] != digest(old)):
            raise ValueError(label + ": live Lambda differs from CFN artifact")
        package = repack(old, before, source)
        if digest(package) == live["CodeSha256"]:
            raise ValueError(label + ": code already deployed, do not rerun")
        key = ("releases/durable-recovery/" + hashlib.sha256(package).hexdigest() +
               f"/{label}.zip")
        packages.append({"label": label, "stack": stack_name, "stack_id": stack["StackId"],
                         "function": function, "logical": logical, "params": params,
                         "parameter": key_param, "old_sha": live["CodeSha256"],
                         "new_sha": digest(package), "key": key, "package": package,
                         "change_name": f"recovery-{os.environ['GITHUB_RUN_ID']}-{index}"})
    executed = set()
    created = set()
    try:
        for plan in packages:
            cf.create_change_set(
                StackName=plan["stack"], ChangeSetName=plan["change_name"],
                ChangeSetType="UPDATE", UsePreviousTemplate=True,
                Capabilities=["CAPABILITY_NAMED_IAM"],
                Parameters=[{"ParameterKey": p, **(
                    {"ParameterValue": plan["key"]} if p == plan["parameter"]
                    else {"UsePreviousValue": True})} for p in plan["params"]])
            created.add(plan["label"])
            cf.get_waiter("change_set_create_complete").wait(
                StackName=plan["stack"], ChangeSetName=plan["change_name"],
                WaiterConfig={"Delay": 5, "MaxAttempts": 48})
            plan["change_id"], plan["changes"] = inspected_changes(
                cf, plan["stack"], plan["change_name"], plan["logical"])
        record = {"verdict": "SAFE_RECOVERY_PREVIEW",
                  "base_commit": BASE_COMMIT,
                  "plans": [{"label": p["label"], "stack_id": p["stack_id"],
                             "old_sha": p["old_sha"], "new_sha": p["new_sha"],
                             "artifact_key": p["key"], "changes": p["changes"]}
                            for p in packages]}
        Path("recovery-preview.json").write_text(json.dumps(record, indent=2) + "\n")
        print("RECOVERY_CODE_ONLY_PREVIEW_VERIFIED", json.dumps(record["plans"]), flush=True)
        if args.mode == "preview":
            print("PREVIEW ONLY: no artifacts uploaded and no change sets executed")
            return
        reviewed_preview(args.reviewed_preview_run_id, record)
        # API first: old watchdog remains compatible if watchdog update fails.
        for plan in packages:
            current = cf.describe_stacks(StackName=plan["stack"])["Stacks"][0]
            if (current["StackId"] != plan["stack_id"] or current["StackStatus"] != "UPDATE_COMPLETE"
                    or {x["ParameterKey"]: x["ParameterValue"] for x in current["Parameters"]} != plan["params"]
                    or lam.get_function_configuration(FunctionName=plan["function"])["CodeSha256"] != plan["old_sha"]):
                raise ValueError("Live stack/code changed since preview: " + plan["label"])
            try:
                existing = s3.get_object(Bucket=ARTIFACT_BUCKET, Key=plan["key"])["Body"].read()
                if existing != plan["package"]:
                    raise ValueError("Immutable S3 artifact collision")
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ("NoSuchKey", "NotFound", "404"):
                    raise
                s3.put_object(Bucket=ARTIFACT_BUCKET, Key=plan["key"], Body=plan["package"])
            cf.execute_change_set(StackName=plan["stack"], ChangeSetName=plan["change_id"])
            executed.add(plan["label"])
            cf.get_waiter("stack_update_complete").wait(
                StackName=plan["stack"], WaiterConfig={"Delay": 10, "MaxAttempts": 90})
            after = lam.get_function_configuration(FunctionName=plan["function"])
            if after["CodeSha256"] != plan["new_sha"] or after["LastUpdateStatus"] != "Successful":
                raise ValueError("Deployed Lambda hash/health mismatch: " + plan["label"])
            print("RECOVERY_LAMBDA_DEPLOYED_AND_VERIFIED", plan["label"], plan["new_sha"], flush=True)
        print("RECOVERY_API_AND_WATCHDOG_DEPLOYED_AND_VERIFIED", flush=True)
    finally:
        for plan in packages:
            if plan["label"] in created and plan["label"] not in executed:
                cf.delete_change_set(StackName=plan["stack"], ChangeSetName=plan["change_name"])


if __name__ == "__main__":
    main()
