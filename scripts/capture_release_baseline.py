#!/usr/bin/env python3
"""Read-only AWS release baseline: no CloudFormation writes or paid API calls."""
import base64
import boto3
from botocore.exceptions import ClientError
import datetime
import hashlib
import json
import os
from pathlib import Path

REGION = "ap-southeast-2"
STACKS = {
    "backend": "afl-app-dev-backend",
    "google": "afl-app-dev-google-video",
    "assembly": "afl-app-dev-assembly",
    "frontend": "afl-app-dev-frontend",
    "legacy_video": "afl-app-dev-video",
}
FUNCTIONS = [
    "afl-app-dev-api", "afl-app-dev-image-worker",
    "afl-app-dev-video-worker", "afl-app-dev-google-omni-worker",
    "afl-app-dev-assembly",
]
def main():
    cf = boto3.client("cloudformation", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    states = boto3.client("stepfunctions", region_name=REGION)
    manifest = {"captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "region": REGION, "stacks": {}, "lambdas": {}, "guardrails": {"deploy_permitted": False}}
    for label, name in STACKS.items():
        stack = cf.describe_stacks(StackName=name)["Stacks"][0]
        if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise RuntimeError(f"Unstable stack {name}: {stack['StackStatus']}")
        parameters = {p["ParameterKey"]: p.get("ParameterValue") for p in stack.get("Parameters", [])}
        resources = []
        for page in cf.get_paginator("list_stack_resources").paginate(StackName=name):
            resources.extend(page["StackResourceSummaries"])
        manifest["stacks"][label] = {
            "name": name, "stack_id": stack["StackId"], "status": stack["StackStatus"],
            "parameters": parameters,
            "resources": [{"logical_id": x["LogicalResourceId"], "physical_id": x.get("PhysicalResourceId"),
                           "type": x["ResourceType"]} for x in resources],
        }
    # Preserve the exact deployed templates for disaster recovery, not repo HEAD.
    template_dir = Path(os.environ.get("BASELINE_TEMPLATE_DIR", "dist/release/deployed-templates"))
    template_dir.mkdir(parents=True, exist_ok=True)
    for label, name in STACKS.items():
        response = cf.get_template(StackName=name, TemplateStage="Original")
        body = response["TemplateBody"]
        if not isinstance(body, str):
            body = json.dumps(body, sort_keys=True)
        template_file = template_dir / (label + ".yaml")
        template_file.write_text(body)
        manifest["stacks"][label]["deployed_template_sha256"] = hashlib.sha256(body.encode()).hexdigest()
        manifest["stacks"][label]["deployed_template_path"] = str(template_file)
    for name in FUNCTIONS:
        fn = lam.get_function_configuration(FunctionName=name)
        manifest["lambdas"][name] = {
            "sha256": fn["CodeSha256"], "runtime": fn.get("Runtime"),
            "last_update_status": fn.get("LastUpdateStatus"), "state": fn.get("State"),
            "version": fn.get("Version"),
        }
        if fn.get("State") != "Active" or fn.get("LastUpdateStatus") != "Successful":
            raise RuntimeError(f"Lambda not ready: {name}")
    manifest["state_machines"] = []
    for page in states.get_paginator("list_state_machines").paginate():
        for machine in page["stateMachines"]:
            if machine["name"].startswith("afl-app-dev-"):
                entry = {"name": machine["name"], "arn": machine["stateMachineArn"],
                         "definition_verified": False}
                try:
                    detail = states.describe_state_machine(stateMachineArn=machine["stateMachineArn"])
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") not in ("AccessDenied", "AccessDeniedException"):
                        raise
                    entry["inspection_error"] = "states:DescribeStateMachine permission denied"
                    manifest.setdefault("verification_gaps", []).append(
                        f"Step Functions definition unverified: {machine['name']}"
                    )
                else:
                    entry.update({"definition_verified": True, "status": detail["status"],
                                  "definition_sha256": hashlib.sha256(detail["definition"].encode()).hexdigest(),
                                  "role_arn": detail["roleArn"]})
                manifest["state_machines"].append(entry)
    # Derive the trusted Google artifact from the CURRENT CloudFormation
    # parameter, never from an obsolete release's hard-coded hash/key.
    google = manifest["lambdas"]["afl-app-dev-google-omni-worker"]
    google_params = manifest["stacks"]["google"]["parameters"]
    bucket = google_params["ArtifactBucketName"]
    key = google_params["GoogleWorkerArtifactKey"]
    if not bucket or not key:
        raise RuntimeError("Google CloudFormation artifact location missing. STOP.")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    h = hashlib.sha256()
    try:
        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
            h.update(chunk)
    finally:
        body.close()
    recorded_sha = base64.b64encode(h.digest()).decode("ascii")
    if google["sha256"] != recorded_sha:
        raise RuntimeError(
            "Google Lambda code differs from CloudFormation-managed artifact. STOP."
        )
    # Verify expected *deployed* timeout, not merely the source template.
    configuration = lam.get_function_configuration(
        FunctionName="afl-app-dev-google-omni-worker"
    )
    if configuration["Timeout"] != 300:
        raise RuntimeError("Google worker Lambda timeout is not 300 seconds. STOP.")
    manifest["google_known_live_artifact"] = {
        "bucket": bucket, "key": key, "sha256": recorded_sha
    }
    manifest["known_drift"] = {"google_worker": False}
    path = Path(os.environ.get("BASELINE_OUTPUT", "release-baseline.json"))
    path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    if manifest.get("verification_gaps"):
        print("INCOMPLETE BASELINE — inspection gaps:", *manifest["verification_gaps"], sep="\\n  ")
    print(f"Read-only baseline captured: {path}")
    print("NO DEPLOYMENT PERFORMED. Google drift:", manifest["known_drift"]["google_worker"])
if __name__ == "__main__":
    main()
