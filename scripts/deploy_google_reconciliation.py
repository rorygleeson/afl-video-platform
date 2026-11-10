#!/usr/bin/env python3
"""One-time user-approved Google CFN reconciliation. No paid video calls."""
import base64
import json
from pathlib import Path

import boto3

from prepare_google_reconciliation import prepare, EXPECTED_FUNCTION, EXPECTED_KEY, EXPECTED_SHA
from preview_release_change_set import check_plan, preview

STACK = "afl-app-dev-google-video"
REGION = "ap-southeast-2"
EXPECTED_RESOURCES = {
    "GoogleWorkerLambda", "DeveloperPreflightInvokePermission",
    "GoogleStateMachineRole", "GoogleStateMachine",
}


def main():
    baseline = json.loads(Path("release-baseline.json").read_text())
    plan = json.loads(Path("release-plan.json").read_text())
    reconciliation = prepare(baseline)
    if reconciliation["verdict"] != "READY_FOR_SEPARATE_CHANGE_SET_PREVIEW":
        raise RuntimeError("Reconciliation not required or baseline changed")
    if plan["stacks"]["google"]["proposed_parameters"] != reconciliation["proposed_parameters"]:
        raise RuntimeError("Google parameter plan mismatch")
    name, template, parameters = check_plan(plan, baseline, "google", Path.cwd())
    if name != STACK or set(plan["stacks"]["google"]["changed_parameters"]) != {"GoogleWorkerArtifactKey"}:
        raise RuntimeError("Unexpected stack or parameter change")
    cf = boto3.client("cloudformation", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    states = boto3.client("stepfunctions", region_name=REGION)
    live_stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    if live_stack["StackId"] != baseline["stacks"]["google"]["stack_id"] or live_stack["StackStatus"] != "UPDATE_COMPLETE":
        raise RuntimeError("Google stack changed since baseline")
    current_params = {p["ParameterKey"]: p.get("ParameterValue") for p in live_stack.get("Parameters", [])}
    if current_params != baseline["stacks"]["google"]["parameters"]:
        raise RuntimeError("Live Google stack parameters changed since baseline")
    current_fn = lam.get_function_configuration(FunctionName=EXPECTED_FUNCTION)
    if current_fn["CodeSha256"] != EXPECTED_SHA or current_fn.get("LastUpdateStatus") != "Successful":
        raise RuntimeError("Working Lambda code changed since baseline")
    google_machines = [m for m in baseline["state_machines"] if m["name"] == "afl-app-dev-google-omni"]
    if len(google_machines) != 1 or not google_machines[0]["definition_verified"]:
        raise RuntimeError("Google state machine baseline not verified")
    before = google_machines[0]
    current_machine = states.describe_state_machine(stateMachineArn=before["arn"])
    import hashlib
    if hashlib.sha256(current_machine["definition"].encode()).hexdigest() != before["definition_sha256"]:
        raise RuntimeError("State machine definition changed since baseline")
    run_id = __import__("os").environ["GITHUB_RUN_ID"]
    result = preview(cf, STACK, template, parameters, {"GoogleWorkerLambda"}, run_id + "-approved", retain=True)
    Path("google-deployment-change-set.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    if result.get("verdict") != "SAFE_PREVIEW" or not result.get("retained_for_separate_approval"):
        raise RuntimeError("CloudFormation change set did not pass validation")
    changes = {c["ResourceChange"]["LogicalResourceId"] for c in result["changes"]}
    if changes != EXPECTED_RESOURCES:
        raise RuntimeError(f"Unexpected Google resources in change set: {sorted(changes)}")
    change_set_id = result["change_set_id"]
    print("Executing validated Google CloudFormation change set:", change_set_id, flush=True)
    cf.execute_change_set(ChangeSetName=change_set_id, StackName=STACK)
    cf.get_waiter("stack_update_complete").wait(StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
    after = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    after_params = {p["ParameterKey"]: p.get("ParameterValue") for p in after.get("Parameters", [])}
    if after_params != reconciliation["proposed_parameters"]:
        raise RuntimeError("Post-deploy stack parameters do not match approved plan")
    function = lam.get_function_configuration(FunctionName=EXPECTED_FUNCTION)
    if function["CodeSha256"] != EXPECTED_SHA or function["State"] != "Active" or function["LastUpdateStatus"] != "Successful":
        raise RuntimeError("Post-deploy Lambda does not match verified working ZIP")
    updated_machine = states.describe_state_machine(stateMachineArn=before["arn"])
    if hashlib.sha256(updated_machine["definition"].encode()).hexdigest() != before["definition_sha256"]:
        raise RuntimeError("Google state machine definition changed after reconciliation")
    if updated_machine["status"] != "ACTIVE" or updated_machine["roleArn"] != before["role_arn"]:
        raise RuntimeError("Google state machine identity/status changed")
    print("SUCCESS: Google stack UPDATE_COMPLETE; ZIP parameter reconciled; Lambda SHA unchanged; state machine unchanged.")


if __name__ == "__main__":
    main()
