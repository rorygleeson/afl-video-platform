#!/usr/bin/env python3
"""Prepare (never execute) a narrowly scoped Google worker CFN parameter reconciliation.

Consumes a fresh read-only release baseline. Does not call AWS, edit templates,
create change sets, or update Lambda. The later CFN preview must use the exact
captured *deployed* template, not repository HEAD.
"""
import argparse
import json
from pathlib import Path

EXPECTED_STACK = "afl-app-dev-google-video"
EXPECTED_FUNCTION = "afl-app-dev-google-omni-worker"
EXPECTED_SHA = "RKx1wcvmxcE3Sx7kzlxbTI3NhBlNlt/Ydpy6J4ZOpmI="
EXPECTED_KEY = "google-diagnostics/956c2a9faa7efb1f39ed1a8ecc376510ffb1ec34.zip"


def prepare(baseline):
    if baseline.get("verification_gaps"):
        raise ValueError("Incomplete AWS baseline")
    stack = baseline["stacks"]["google"]
    live = baseline["lambdas"][EXPECTED_FUNCTION]
    artifact = baseline["google_known_live_artifact"]
    params = stack["parameters"]
    if stack["name"] != EXPECTED_STACK or stack["status"] != "UPDATE_COMPLETE":
        raise ValueError("Google stack identity or state mismatch")
    if live["sha256"] != EXPECTED_SHA or live["state"] != "Active" or live["last_update_status"] != "Successful":
        raise ValueError("Working Google Lambda SHA/state changed; STOP")
    if artifact != {"bucket": params["ArtifactBucketName"], "key": EXPECTED_KEY, "sha256": EXPECTED_SHA}:
        raise ValueError("Verified S3 ZIP does not match known working Google Lambda")
    if not stack.get("deployed_template_path") or not stack.get("deployed_template_sha256"):
        raise ValueError("Exact deployed CloudFormation template not captured")
    old = params["GoogleWorkerArtifactKey"]
    if old == EXPECTED_KEY:
        return {"verdict": "ALREADY_RECONCILED", "stack": EXPECTED_STACK}
    proposed = dict(params)
    proposed["GoogleWorkerArtifactKey"] = EXPECTED_KEY
    changed = {k: {"before": v, "after": proposed[k]} for k, v in params.items() if proposed[k] != v}
    if set(changed) != {"GoogleWorkerArtifactKey"}:
        raise ValueError("Unexpected parameter change")
    return {
        "verdict": "READY_FOR_SEPARATE_CHANGE_SET_PREVIEW",
        "stack": EXPECTED_STACK,
        "live_lambda": EXPECTED_FUNCTION,
        "verified_live_code_sha256": EXPECTED_SHA,
        "deployed_template_path": stack["deployed_template_path"],
        "deployed_template_sha256": stack["deployed_template_sha256"],
        "stack_id": stack["stack_id"],
        "changed_parameters": changed,
        "proposed_parameters": proposed,
        "aws_changed": False,
        "next_gate": "Preview using exact captured deployed template. Permit ONLY Modify GoogleWorkerLambda with no replacement. Reject any IAM/StepFunctions/permission change. Recheck live SHA and stack parameters immediately before execution; require explicit approval."
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("google-reconciliation-plan.json"))
    args = parser.parse_args()
    result = prepare(json.loads(args.baseline.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(result["verdict"], "— NO AWS CHANGES")


if __name__ == "__main__":
    main()
