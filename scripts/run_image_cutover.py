#!/usr/bin/env python3
"""ONE-TIME controlled image-only cutover. Requires explicit workflow opt-in.

Never invokes paid Gemini/video models. Fails closed before CloudFormation if
active image jobs cannot be verified. Keeps original template/params/S3 version.
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

from validate_change_set import validate
from preview_release_change_set import verify_reconcile_permission

REGION = "ap-southeast-2"
STACK = "afl-app-dev-backend"
SM_ARN = "arn:aws:states:ap-southeast-2:134387458447:stateMachine:afl-app-dev-scene1-pilot"
CONFIG_KEY = "scenarios/afl-grand-final-winning-kick/v1/config.json"
ALLOW = {"ImageWorkerLambda", "ImageWorkerRole", "ImageStateMachine"}
PIN = "759ee6efc932343e2b075693d03ac266bbbb256b"


def write_report(report):
    Path("image-cutover-report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")


def wait_stack(cf):
    cf.get_waiter("stack_update_complete").wait(StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 90})


def changes(cf, name):
    doc = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
    for _ in range(90):
        if doc["Status"] in ("CREATE_COMPLETE", "FAILED"):
            break
        time.sleep(2)
        doc = cf.describe_change_set(StackName=STACK, ChangeSetName=name)
    else:
        raise RuntimeError("Change-set creation timed out")
    items = list(doc.get("Changes", []))
    token = doc.get("NextToken")
    while token:
        page = cf.describe_change_set(StackName=STACK, ChangeSetName=name, NextToken=token)
        if page.get("ChangeSetId") != doc.get("ChangeSetId") or page.get("StackId") != doc.get("StackId"):
            raise RuntimeError("Change-set identity changed across pages")
        items.extend(page.get("Changes", []))
        token = page.get("NextToken")
    return doc, items


def no_active_image_jobs(cf, states):
    # Check state-machine executions AND DynamoDB jobs. Fails closed on access errors.
    # GitHub deployment role has no states:ListExecutions. Read complete job
    # state from DynamoDB instead; fail if no read permission.
    table = cf.describe_stack_resource(StackName=STACK, LogicalResourceId="JobsTable")["StackResourceDetail"]["PhysicalResourceId"]
    ddb = boto3.client("dynamodb", region_name=REGION)
    scan_args = {"TableName": table, "ProjectionExpression": "jobId, #s",
                 "ExpressionAttributeNames": {"#s": "status"}}
    count = 0
    while True:
        page = ddb.scan(**scan_args)
        for item in page.get("Items", []):
            status = item.get("status", {}).get("S")
            if status == "GENERATING_IMAGES":
                count += 1
        if "LastEvaluatedKey" not in page:
            break
        scan_args["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if count:
        raise RuntimeError(f"Found {count} image jobs in GENERATING_IMAGES")
    return table


def main():
    if os.environ.get("IMAGE_MIGRATION_APPROVED") != "CUTOVER_GOOGLE_IMAGE_ONLY":
        raise RuntimeError("Explicit image-only cutover approval missing")
    source = Path("/tmp/image-migration")
    manifest = json.loads((source / "dist/release/manifest.json").read_text())["artifacts"]["image-worker"]
    image = (source / manifest["local_file"]).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != manifest["sha256_hex"] or manifest["s3_key"] != f"releases/sha256/{digest}/image-worker.zip":
        raise RuntimeError("Image package provenance invalid")
    cf = boto3.client("cloudformation", region_name=REGION)
    states = boto3.client("stepfunctions", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    events = boto3.client("events", region_name=REGION)
    live = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    if live["StackStatus"] != "UPDATE_COMPLETE":
        raise RuntimeError("Backend stack unhealthy")
    params = {x["ParameterKey"]: x.get("ParameterValue") for x in live["Parameters"]}
    old_image = lam.get_function_configuration(FunctionName="afl-app-dev-image-worker")
    if old_image.get("State") != "Active" or old_image.get("LastUpdateStatus") != "Successful":
        raise RuntimeError("Existing image Lambda unhealthy")
    if not old_image.get("Environment", {}).get("Variables", {}).get("REPLICATE_SECRET_NAME"):
        raise RuntimeError("Old image worker is no longer Replicate; refusing cutover")
    machine = states.describe_state_machine(stateMachineArn=SM_ARN)
    if machine["status"] != "ACTIVE" or json.loads(machine["definition"])["States"]["ParallelScenes"]["MaxConcurrency"] != 3:
        raise RuntimeError("Unexpected live image state machine")
    report = {"verdict": "STARTED", "deployed": False, "s3_config_switched": False,
              "original_image_artifact": params.get("ImageWorkerArtifactKey"),
              "original_lambda_code_sha256": old_image.get("CodeSha256"),
              "migration_image_artifact": manifest["s3_key"], "candidate_commit": PIN}
    bucket = params["ArtifactBucketName"]
    config = s3.get_object(Bucket=params["ScenarioAssetsBucket"], Key=CONFIG_KEY)
    original_bytes = config["Body"].read(200000)
    original = json.loads(original_bytes)
    report["original_s3_version"] = config.get("VersionId")
    report["original_s3_hash"] = hashlib.sha256(original_bytes).hexdigest()
    if [x.get("model") for x in original.get("scenes", [])] != ["google/nano-banana-pro"] * 3:
        raise RuntimeError("Unexpected live scenario model configuration")
    updated = json.loads(original_bytes)
    for scene in updated["scenes"]:
        scene["model"] = "gemini-3-pro-image"
    if any(old["promptInstructions"] != new["promptInstructions"] or old["masterKey"] != new["masterKey"]
           for old, new in zip(original["scenes"], updated["scenes"])):
        raise RuntimeError("Scenario prompts/master images changed")
    if updated["sharedPrompt"] != original["sharedPrompt"]:
        raise RuntimeError("Shared prompt modified")
    # Exact original template and parameters are essential for rollback.
    old_template = cf.get_template(StackName=STACK, TemplateStage="Original")["TemplateBody"]
    if not isinstance(old_template, str):
        raise RuntimeError("Unsupported current live CFN template body format")
    Path("image-cutover-original-backend.yaml").write_text(old_template)
    report["old_template_sha256"] = hashlib.sha256(old_template.encode()).hexdigest()
    write_report(report)

    # Immutable package upload is non-activating.
    key = manifest["s3_key"]
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
            raise
        s3.put_object(Bucket=bucket, Key=key, Body=image, ContentType="application/zip", IfNoneMatch="*")
    else:
        with obj["Body"] as stream:
            if hashlib.sha256(stream.read()).hexdigest() != digest:
                raise RuntimeError("S3 immutable artifact mismatch")
    # Fails closed if either execution-list or DynamoDB scan is unavailable.
    report["jobs_table_checked"] = no_active_image_jobs(cf, states)
    write_report(report)
    proposed = dict(params, ImageWorkerArtifactKey=key)
    name = "afl-image-go-live-" + os.environ["GITHUB_RUN_ID"]
    created = False
    executed = False
    changed_config = False
    try:
        cf.create_change_set(StackName=STACK, ChangeSetName=name, ChangeSetType="UPDATE",
            TemplateBody=(source / "infrastructure/app/backend.yaml").read_text(),
            Parameters=[{"ParameterKey":k, "ParameterValue":v} for k, v in sorted(proposed.items())],
            Capabilities=["CAPABILITY_NAMED_IAM"], Description="One-time approved direct Google image cutover")
        created = True
        doc, items = changes(cf, name)
        verified = verify_reconcile_permission(cf, lam, events, STACK, items)
        issues = validate(dict(doc, Changes=items), ALLOW, verified_reconcile_permission=verified)
        changed_ids = sorted({item.get("ResourceChange", {}).get("LogicalResourceId") for item in items})
        allowed_dynamic = {"ImageReconcileInvokePermission", "ImageReconcileSchedule", "ImageStateMachineRole"}
        if set(changed_ids) - (ALLOW | allowed_dynamic):
            issues.append("Unexpected modified resources")
        if not verified:
            issues.append("Conditional EventBridge permission not verified")
        if issues:
            report.update({"verdict": "BLOCKED", "issues": issues, "changed_ids": changed_ids})
            write_report(report)
            raise RuntimeError("Live change-set preview is unsafe")
        report.update({"verdict": "SAFE_LIVE_CHANGE_SET", "changed_ids": changed_ids})
        write_report(report)

        # Check again immediately before execution. In-flight work must be drained.
        no_active_image_jobs(cf, states)
        cf.execute_change_set(StackName=STACK, ChangeSetName=name)
        executed = True
        wait_stack(cf)
        current = lam.get_function_configuration(FunctionName="afl-app-dev-image-worker")
        expected_sha = base64.b64encode(bytes.fromhex(digest)).decode()
        if (current.get("CodeSha256") != expected_sha or current.get("State") != "Active" or
                current.get("LastUpdateStatus") != "Successful" or current.get("MemorySize") != 1024):
            raise RuntimeError("Deployed image Lambda code/config doesn't match approved release")
        machine = states.describe_state_machine(stateMachineArn=SM_ARN)
        definition = json.loads(machine["definition"])
        branch = definition["States"]["ParallelScenes"]["ItemProcessor"]
        if machine["status"] != "ACTIVE" or branch["StartAt"] != "Generate" or branch["MaxConcurrency"] != 3:
            raise RuntimeError("Deployed image state machine not updated properly")
        # Lambda now accepts old and new scenario model IDs: switch model last.
        new_payload = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        s3.put_object(Bucket=params["ScenarioAssetsBucket"], Key=CONFIG_KEY,
            Body=new_payload, ContentType="application/json", ServerSideEncryption="AES256")
        changed_config = True
        current_config = s3.get_object(Bucket=params["ScenarioAssetsBucket"], Key=CONFIG_KEY)
        current_bytes = current_config["Body"].read()
        if hashlib.sha256(current_bytes).hexdigest() != hashlib.sha256(new_payload).hexdigest():
            raise RuntimeError("Runtime S3 configuration verification failed")
        report.update({"verdict": "DEPLOYED_AND_VERIFIED", "deployed": True, "s3_config_switched": True,
                       "new_s3_version": current_config.get("VersionId"),
                       "deployed_lambda_sha256": current["CodeSha256"]})
        write_report(report)
        print("IMAGE_MIGRATION_SUCCESS="+json.dumps(report,sort_keys=True))
    except Exception:
        # A failed cutover must not be represented as successful.
        report["verdict"] = "CUTOVER_FAILED"
        report["deployment_started"] = executed
        report["s3_switch_started"] = changed_config
        write_report(report)
        if executed:
            # If the stack finished but a post-deploy verification/config step
            # failed, restore the original CFN template and image ZIP parameter.
            try:
                current_stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]["StackStatus"]
                if current_stack == "UPDATE_COMPLETE":
                    rollback = "afl-image-rollback-" + os.environ["GITHUB_RUN_ID"]
                    cf.create_change_set(StackName=STACK, ChangeSetName=rollback,
                        ChangeSetType="UPDATE", TemplateBody=old_template,
                        Parameters=[{"ParameterKey":k,"ParameterValue":v} for k,v in sorted(params.items())],
                        Capabilities=["CAPABILITY_NAMED_IAM"],
                        Description="Restore prior Replicate image backend")
                    rollback_doc, _ = changes(cf, rollback)
                    if rollback_doc["Status"] != "CREATE_COMPLETE":
                        raise RuntimeError("Rollback change set not ready")
                    cf.execute_change_set(StackName=STACK, ChangeSetName=rollback)
                    wait_stack(cf)
                    report["rollback_backend"] = "RESTORED"
                else:
                    report["rollback_backend"] = "STACK_NOT_STABLE_" + current_stack
                if changed_config:
                    s3.put_object(Bucket=params["ScenarioAssetsBucket"],Key=CONFIG_KEY,
                        Body=original_bytes,ContentType="application/json",
                        ServerSideEncryption="AES256")
                    report["rollback_s3_config"] = "RESTORED"
            except Exception as rollback_exc:
                report["rollback_error_type"] = type(rollback_exc).__name__
            write_report(report)
        raise
    finally:
        if created and not executed:
            cf.delete_change_set(StackName=STACK, ChangeSetName=name)


if __name__ == "__main__":
    main()
