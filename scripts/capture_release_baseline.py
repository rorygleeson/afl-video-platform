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
GOOGLE_KNOWN_SHA = "RKx1wcvmxcE3Sx7kzlxbTI3NhBlNlt/Ydpy6J4ZOpmI="
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
    google = manifest["lambdas"]["afl-app-dev-google-omni-worker"]
    if google["sha256"] != GOOGLE_KNOWN_SHA:
        raise RuntimeError("Google live SHA changed; baseline invalid. STOP.")
    google_params = manifest["stacks"]["google"]["parameters"]
    bucket = google_params["ArtifactBucketName"]
    key = "google-diagnostics/956c2a9faa7efb1f39ed1a8ecc376510ffb1ec34.zip"
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    h = hashlib.sha256()
    try:
        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
            h.update(chunk)
    finally:
        body.close()
    if base64.b64encode(h.digest()).decode() != GOOGLE_KNOWN_SHA:
        raise RuntimeError("Known Google ZIP digest mismatch. STOP.")
    manifest["google_known_live_artifact"] = {"bucket": bucket, "key": key, "sha256": GOOGLE_KNOWN_SHA}
    manifest["known_drift"] = {"google_worker": google_params.get("GoogleWorkerArtifactKey") != key}
    path = Path(os.environ.get("BASELINE_OUTPUT", "release-baseline.json"))
    path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    if manifest.get("verification_gaps"):
        print("INCOMPLETE BASELINE — inspection gaps:", *manifest["verification_gaps"], sep="\\n  ")
    print(f"Read-only baseline captured: {path}")
    print("NO DEPLOYMENT PERFORMED. Google drift:", manifest["known_drift"]["google_worker"])
if __name__ == "__main__":
    main()
