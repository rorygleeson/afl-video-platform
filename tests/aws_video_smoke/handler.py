"""One-off AWS integration test, invoked by a disposable CloudFormation custom resource.

Uses ONE dedicated mock job ID; never invokes Google, Replicate or existing
image state machine. Removes its own fixture even on test failure. Never
deletes a concurrency slot owned by another request.
"""
import base64
import json
import logging
import os
import time
import urllib.request
import uuid

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
JOB_ID = "e" * 32
SCENE = "scene1"
LOCK = "GLOBAL_VIDEO_SLOT_0"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6rVMAAAAASUVORK5CYII="
)


def respond(event, context, status, reason, data=None):
    payload = json.dumps({
        "Status": status,
        "Reason": reason[:800],
        "PhysicalResourceId": "afl-video-stage3-smoke-test",
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=payload, method="PUT",
                                 headers={"content-type": "", "content-length": str(len(payload))})
    with urllib.request.urlopen(req, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("CloudFormation response failed")


def wait_for_execution(sfn, execution_arn, timeout=120):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        status = sfn.describe_execution(executionArn=execution_arn)["status"]
        if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            return status
        time.sleep(1)
    raise TimeoutError("Mock execution did not finish in time")


def run():
    region = os.environ["AWS_REGION"]
    bucket = os.environ["UPLOAD_BUCKET"]
    jobs = boto3.resource("dynamodb", region_name=region).Table(os.environ["JOBS_TABLE"])
    slots = boto3.resource("dynamodb", region_name=region).Table(os.environ["ADMISSION_TABLE"])
    s3 = boto3.client("s3", region_name=region)
    sfn = boto3.client("stepfunctions", region_name=region)
    machine = os.environ["VIDEO_STATE_MACHINE_ARN"]
    source_key = f"results/{JOB_ID}/{SCENE}.png"
    marker_key = None
    fake_lock_owner = "smoke-blocker-" + uuid.uuid4().hex[:16]
    created_fixture = False
    owned_blocker = False
    execution_arns = []
    try:
        # Do not replace any job or media already present, even in a test.
        jobs.put_item(
            Item={"jobId": JOB_ID, "status": "SMOKE_FIXTURE",
                  "scenes": {SCENE: {"status": "COMPLETED", "resultKey": source_key}},
                  "expiresAt": int(time.time()) + 86400},
            ConditionExpression="attribute_not_exists(jobId)",
        )
        created_fixture = True
        s3.put_object(Bucket=bucket, Key=source_key, Body=PNG,
                      ContentType="image/png", ServerSideEncryption="AES256")
        # Deterministic occupied-slot test: no timing race with an async Lambda.
        slots.put_item(
            Item={"slotId": LOCK, "owner": fake_lock_owner,
                  "jobId": JOB_ID, "sceneId": SCENE,
                  "acquiredAt": int(time.time())},
            ConditionExpression="attribute_not_exists(slotId)",
        )
        owned_blocker = True
        blocked_name = "smoke-blocked-" + uuid.uuid4().hex[:16]
        blocked = sfn.start_execution(
            stateMachineArn=machine, name=blocked_name,
            input=json.dumps({"jobId": JOB_ID, "sceneId": SCENE}),
        )["executionArn"]
        execution_arns.append(blocked)
        blocked_status = wait_for_execution(sfn, blocked)
        if blocked_status != "FAILED":
            raise AssertionError(f"Occupied slot should reject execution, got {blocked_status}")
        # Fail-safe: remove ONLY our synthetic lock, not somebody else's.
        slots.delete_item(
            Key={"slotId": LOCK},
            ConditionExpression="#o = :owner AND jobId = :job",
            ExpressionAttributeNames={"#o": "owner"},
            ExpressionAttributeValues={":owner": fake_lock_owner, ":job": JOB_ID},
        )
        owned_blocker = False
        successful_name = "smoke-success-" + uuid.uuid4().hex[:16]
        succeeded = sfn.start_execution(
            stateMachineArn=machine, name=successful_name,
            input=json.dumps({"jobId": JOB_ID, "sceneId": SCENE}),
        )["executionArn"]
        execution_arns.append(succeeded)
        success_status = wait_for_execution(sfn, succeeded)
        if success_status != "SUCCEEDED":
            details = sfn.describe_execution(executionArn=succeeded)
            raise AssertionError(f"Expected success; got {success_status}, error={details.get('error', '')}")
        record = jobs.get_item(Key={"jobId": JOB_ID}, ConsistentRead=True)["Item"]
        video = record.get("videos", {}).get(SCENE, {})
        if video.get("status") != "MOCK_COMPLETED" or video.get("requestId") != successful_name:
            raise AssertionError(f"Unexpected final video state: {video.get('status')}")
        marker_key = f"video-mock/{JOB_ID}/{SCENE}/{successful_name}.json"
        result = json.loads(s3.get_object(Bucket=bucket, Key=marker_key)["Body"].read())
        if not result.get("mock") or result.get("requestId") != successful_name:
            raise AssertionError("Mock S3 marker missing or incorrect")
        if slots.get_item(Key={"slotId": LOCK}, ConsistentRead=True).get("Item"):
            raise AssertionError("Global admission slot was not released")
        LOG.info("PASS occupied-slot rejection, mock SFN success, DynamoDB, S3 marker and release")
        return {"BlockedExecution": blocked_status, "SuccessfulExecution": success_status,
                "VideoStatus": video["status"], "AdmissionSlotReleased": "true"}
    finally:
        # Stop any of OUR still-running executions. Do not mutate other jobs.
        for arn in execution_arns:
            try:
                if sfn.describe_execution(executionArn=arn)["status"] == "RUNNING":
                    sfn.stop_execution(executionArn=arn, error="SmokeCleanup", cause="Fixture cleanup")
            except Exception:
                LOG.exception("Could not stop smoke execution; inspect before re-run")
        if owned_blocker:
            try:
                slots.delete_item(
                    Key={"slotId": LOCK},
                    ConditionExpression="#o = :owner AND jobId = :job",
                    ExpressionAttributeNames={"#o": "owner"},
                    ExpressionAttributeValues={":owner": fake_lock_owner, ":job": JOB_ID},
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    LOG.exception("Failed to clean synthetic slot")
        if created_fixture:
            if marker_key:
                try:
                    s3.delete_object(Bucket=bucket, Key=marker_key)
                except Exception:
                    LOG.exception("Could not clean test marker")
            try:
                s3.delete_object(Bucket=bucket, Key=source_key)
                jobs.delete_item(Key={"jobId": JOB_ID}, ConditionExpression="#st = :test",
                                 ExpressionAttributeNames={"#st": "status"},
                                 ExpressionAttributeValues={":test": "SMOKE_FIXTURE"})
            except Exception:
                LOG.exception("Could not clean test fixture")


def lambda_handler(event, context):
    request_type = event.get("RequestType")
    if request_type == "Delete":
        respond(event, context, "SUCCESS", "No side effects on deletion")
        return
    if request_type != "Create":
        respond(event, context, "SUCCESS", "Smoke test runs on initial creation only")
        return
    try:
        data = run()
        respond(event, context, "SUCCESS", "Mock AWS integration passed", data)
    except Exception as exc:
        LOG.exception("AWS mock smoke test failed, type=%s", type(exc).__name__)
        respond(event, context, "FAILED", f"Smoke test failure: {type(exc).__name__}: {str(exc)[:200]}")
