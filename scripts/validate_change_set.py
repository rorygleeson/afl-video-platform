#!/usr/bin/env python3
"""Fail-closed gate for CloudFormation describe-change-set JSON.

Usage: aws cloudformation describe-change-set ... > changes.json
       python scripts/validate_change_set.py changes.json --stack google --allow GoogleWorkerLambda
No change-set execution occurs here.
"""
import argparse
import json
from pathlib import Path

# Dependency-only modifications observed in AWS preview #7. These are NOT
# blanket approvals: only these exact dynamic attribute references qualify.
EXPECTED_DEPENDENCIES = {
    "ApiRole": {("Policies", "VideoWorkerLambda.Arn")},
    "ImageReconcileSchedule": {("Targets", "ImageWorkerLambda.Arn")},
    "ImageStateMachineRole": {("Policies", "ImageWorkerLambda.Arn")},
    "ImageStateMachine": {("DefinitionSubstitutions", "ImageWorkerLambda.Arn"),
                          ("RoleArn", "ImageStateMachineRole.Arn")},
    "ProxyAny": {("Integration", "ApiLambda.Arn")},
}

def dependency_only(r):
    expected = EXPECTED_DEPENDENCIES.get(r.get("LogicalResourceId"))
    if not expected or r.get("Action") != "Modify" or r.get("Replacement") != "False":
        return False
    details = r.get("Details", [])
    if not details:
        return False
    return all(
        d.get("Evaluation") == "Dynamic"
        and d.get("ChangeSource") == "ResourceAttribute"
        and d.get("Target", {}).get("Attribute") == "Properties"
        and d.get("Target", {}).get("RequiresRecreation") == "Never"
        and (d.get("Target", {}).get("Name"), d.get("CausingEntity")) in expected
        for d in details
    )

# A conditional replacement of this permission can be a false-positive:
# CloudFormation cannot statically resolve the ARN of the *unchanged* named
# EventBridge rule. Accept only the exact single dynamic reference below,
# and only after a live AWS check confirms both names and the current policy.
def expected_reconcile_permission(r, verified=False):
    if not verified or r.get("LogicalResourceId") != "ImageReconcileInvokePermission":
        return False
    if r.get("Action") != "Modify" or r.get("Replacement") != "Conditional":
        return False
    details = r.get("Details", [])
    return len(details) == 1 and all(
        d.get("Evaluation") == "Dynamic"
        and d.get("ChangeSource") == "ResourceAttribute"
        and d.get("CausingEntity") == "ImageReconcileSchedule.Arn"
        and d.get("Target", {}).get("Attribute") == "Properties"
        and d.get("Target", {}).get("Name") == "SourceArn"
        and d.get("Target", {}).get("RequiresRecreation") == "Always"
        for d in details
    )


# CloudFormation flags unchanged GetAtt references as dynamic when the named
# Google Lambda code changes. These are the exact observed dependencies.
GOOGLE_DEPENDENCIES = {
    "GoogleStateMachineRole": {("Policies", "GoogleWorkerLambda.Arn")},
    "GoogleStateMachine": {("DefinitionString", "GoogleWorkerLambda.Arn"),
                           ("RoleArn", "GoogleStateMachineRole.Arn")},
}

def google_dependency_only(r):
    expected = GOOGLE_DEPENDENCIES.get(r.get("LogicalResourceId"))
    if not expected or r.get("Action") != "Modify" or r.get("Replacement") != "False":
        return False
    details = r.get("Details", [])
    return bool(details) and all(
        d.get("Evaluation") == "Dynamic"
        and d.get("ChangeSource") == "ResourceAttribute"
        and d.get("Target", {}).get("Attribute") == "Properties"
        and d.get("Target", {}).get("RequiresRecreation") == "Never"
        and (d.get("Target", {}).get("Name"), d.get("CausingEntity")) in expected
        for d in details
    )

def google_preflight_permission_only(r):
    if r.get("LogicalResourceId") != "DeveloperPreflightInvokePermission":
        return False
    if r.get("Action") != "Modify" or r.get("Replacement") != "Conditional":
        return False
    details = r.get("Details", [])
    return len(details) == 1 and all(
        d.get("Evaluation") == "Dynamic"
        and d.get("ChangeSource") == "ResourceAttribute"
        and d.get("CausingEntity") == "GoogleWorkerLambda.Arn"
        and d.get("Target", {}).get("Attribute") == "Properties"
        and d.get("Target", {}).get("Name") == "FunctionName"
        and d.get("Target", {}).get("RequiresRecreation") == "Always"
        for d in details
    )

def validate(doc, allowed, verified_reconcile_permission=False):
    errors = []
    status = doc.get("Status")
    if status != "CREATE_COMPLETE":
        return [f"Change set is not ready: {status} ({doc.get('StatusReason', '')})"]
    changes = doc.get("Changes", [])
    if not changes:
        return ["No resource changes in completed change set"]
    for item in changes:
        r = item.get("ResourceChange", {})
        logical = r.get("LogicalResourceId", "<unknown>")
        action = r.get("Action")
        replacement = r.get("Replacement", "Conditional")
        if expected_reconcile_permission(r, verified_reconcile_permission):
            continue
        if allowed == {"GoogleWorkerLambda"} and (google_dependency_only(r) or google_preflight_permission_only(r)):
            continue
        if logical not in allowed and not dependency_only(r):
            errors.append(f"{logical}: not in explicitly approved allowlist")
        if action != "Modify":
            errors.append(f"{logical}: forbidden action {action}")
        if replacement != "False":
            errors.append(f"{logical}: replacement is {replacement}; only False allowed")
        for detail in r.get("Details", []):
            if detail.get("Target", {}).get("RequiresRecreation") not in ("Never", None):
                errors.append(f"{logical}: detail requires recreation")
    return errors

def main():
    p = argparse.ArgumentParser()
    p.add_argument("json_file", type=Path)
    p.add_argument("--allow", action="append", required=True, help="Explicitly permitted logical resource ID")
    args = p.parse_args()
    doc = json.loads(args.json_file.read_text())
    errors = validate(doc, set(args.allow))
    if errors:
        for e in errors:
            print("BLOCKED:", e)
        raise SystemExit(1)
    print("PASS: only explicitly permitted non-replacing modifications")
if __name__ == "__main__":
    main()
