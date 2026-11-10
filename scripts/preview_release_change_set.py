#!/usr/bin/env python3
"""Create a review-only CloudFormation change set, fail closed, then delete it.

This NEVER executes a change set. Template and parameter preservation are
checked before AWS is contacted. Intended for the single unified release.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
import boto3
from botocore.exceptions import ClientError
from validate_change_set import validate
import re

STACK_TEMPLATES = {
    "backend": ("afl-app-dev-backend", "infrastructure/app/backend.yaml"),
    "google": ("afl-app-dev-google-video", "infrastructure/app/google-video.yaml"),
    "assembly": ("afl-app-dev-assembly", "infrastructure/app/assembly.yaml"),
    "frontend": ("afl-app-dev-frontend", "infrastructure/app/frontend.yaml"),
}

def check_plan(plan, baseline, label, repo):
    if plan.get("deploy_permitted") is not False or baseline.get("guardrails", {}).get("deploy_permitted") is not False:
        raise ValueError("Deployment safety flags must both be false")
    if label not in STACK_TEMPLATES:
        raise ValueError("Unknown stack")
    stack_name, template_path = STACK_TEMPLATES[label]
    planned = plan["stacks"][label]
    live = baseline["stacks"][label]
    if planned["name"] != stack_name or live["name"] != stack_name:
        raise ValueError("Stack identity mismatch")
    if planned["current_parameters"] != live["parameters"]:
        raise ValueError("Baseline/plan mismatch")
    proposed = planned["proposed_parameters"]
    changed = planned["changed_parameters"]
    if set(proposed) != set(live["parameters"]):
        raise ValueError("Parameter set changed unexpectedly")
    expected = {k: {"before": v, "after": proposed[k]}
                for k, v in live["parameters"].items() if proposed[k] != v}
    if changed != expected:
        raise ValueError("Parameter diff mismatch")
    template = repo / template_path
    # During reconciliation, preview the actual deployed template rather than
    # replacing it with repository HEAD. The latter caused unrelated IAM,
    # API Gateway and Step Functions changes in preview #6.
    if label == "backend":
        original = live.get("deployed_template_path")
        digest = live.get("deployed_template_sha256")
        if not original or not digest:
            raise ValueError("Missing captured deployed backend template")
        deployed = repo / original
        if not deployed.is_file() or hashlib.sha256(deployed.read_bytes()).hexdigest() != digest:
            raise ValueError("Deployed backend template missing or digest mismatch")
        template = deployed
    if not template.is_file():
        raise ValueError("Missing source template")
    return stack_name, template, [
        {"ParameterKey": key, "ParameterValue": value}
        for key, value in sorted(proposed.items())
    ]

def verify_reconcile_permission(cf, lam, events, stack_name, changes):
    """Read-only check of the exact existing EventBridge-to-Lambda permission."""
    candidates = [x.get("ResourceChange", {}) for x in changes]
    permission = [x for x in candidates if x.get("LogicalResourceId") == "ImageReconcileInvokePermission"]
    if len(permission) != 1:
        return False
    resource = cf.describe_stack_resource(StackName=stack_name,
        LogicalResourceId="ImageReconcileInvokePermission")["StackResourceDetail"]
    if resource.get("PhysicalResourceId") != permission[0].get("PhysicalResourceId"):
        return False
    rule = events.describe_rule(Name="afl-app-dev-reconcile-images")
    arn = rule.get("Arn", "")
    if not re.fullmatch(r"arn:aws[a-zA-Z-]*:events:ap-southeast-2:134387458447:rule/afl-app-dev-reconcile-images", arn):
        return False
    targets = events.list_targets_by_rule(Rule="afl-app-dev-reconcile-images").get("Targets", [])
    if not any(x.get("Arn") == "arn:aws:lambda:ap-southeast-2:134387458447:function:afl-app-dev-image-worker" for x in targets):
        return False
    policy = json.loads(lam.get_policy(FunctionName="afl-app-dev-image-worker")["Policy"])
    return any(
        s.get("Effect") == "Allow"
        and s.get("Principal", {}).get("Service") == "events.amazonaws.com"
        and s.get("Action") == "lambda:InvokeFunction"
        and s.get("Condition", {}).get("ArnLike", {}).get("AWS:SourceArn") == arn
        for s in policy.get("Statement", [])
    )

def preview(cf, stack_name, template, parameters, allowed, run_id, verify_permission=None, retain=False):
    name = f"afl-unified-preview-{run_id}"
    created = False
    safe_to_retain = False
    try:
        cf.create_change_set(
            StackName=stack_name, ChangeSetName=name, ChangeSetType="UPDATE",
            TemplateBody=template.read_text(), Parameters=parameters,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Description="Unified release PREVIEW ONLY; never execute",
        )
        created = True
        for _ in range(90):
            result = cf.describe_change_set(StackName=stack_name, ChangeSetName=name)
            if result["Status"] in ("CREATE_COMPLETE", "FAILED"):
                break
            time.sleep(2)
        else:
            raise RuntimeError("Timed out waiting for preview change set")
        changes = list(result.get("Changes", []))
        next_token = result.get("NextToken")
        while next_token:
            page = cf.describe_change_set(StackName=stack_name, ChangeSetName=name, NextToken=next_token)
            if page.get("ChangeSetId") != result.get("ChangeSetId") or page.get("StackId") != result.get("StackId"):
                raise ValueError("Paginated preview change-set identity changed")
            changes.extend(page.get("Changes", []))
            next_token = page.get("NextToken")
        result["Changes"] = changes
        output = {
            "stack": stack_name, "name": name,
            "status": result["Status"],
            "reason": result.get("StatusReason", ""),
            "changes": changes,
            "executed": False,
        }
        if result["Status"] == "FAILED" and "didn't contain changes" in result.get("StatusReason", ""):
            output["verdict"] = "NO_CHANGES"
        else:
            verified = bool(verify_permission and verify_permission(result.get('Changes', [])))
            output['reconcile_permission_live_verified'] = verified
            issues = validate(result, allowed, verified_reconcile_permission=verified)
            output["verdict"] = "BLOCKED" if issues else "SAFE_PREVIEW"
            output["issues"] = issues
            safe_to_retain = not issues
            if retain and safe_to_retain:
                output["change_set_id"] = result["ChangeSetId"]
                output["retained_for_separate_approval"] = True
        return output
    finally:
        if created and not (retain and safe_to_retain):
            cf.delete_change_set(StackName=stack_name, ChangeSetName=name)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--stack", choices=STACK_TEMPLATES, required=True)
    p.add_argument("--allow", action="append", default=[])
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", type=Path, default=Path("change-set-preview.json"))
    p.add_argument("--create-preview", action="store_true", help="Explicitly authorize AWS change-set creation and deletion")
    p.add_argument("--retain-reviewed", action="store_true", help="Retain only a SAFE_PREVIEW change set for separate explicit deployment approval")
    args = p.parse_args()
    baseline = json.loads(args.baseline.read_text())
    plan = json.loads(args.plan.read_text())
    stack_name, template, parameters = check_plan(plan, baseline, args.stack, Path(__file__).resolve().parents[1])
    if args.retain_reviewed and not args.create_preview:
        raise ValueError("--retain-reviewed requires --create-preview")
    if not args.create_preview:
        print("PRECHECK ONLY; AWS unchanged:", stack_name)
        return
    if not args.run_id.isdigit():
        raise ValueError("run-id must be numeric")
    # A preview never executes. Known Google/FFmpeg deployment blockers must
    # not suppress a scoped preview of a different stack.
    if baseline.get("verification_gaps"):
        raise ValueError("AWS baseline has unverified resources; refusing preview")
    if args.stack == "google" and baseline.get("known_drift", {}).get("google_worker"):
        raise ValueError("Google worker drift: preview requires a separate reconciliation design")
    if args.stack == "assembly":
        raise ValueError("Assembly FFmpeg layer provenance not pinned; preview disabled")
    cf = boto3.client("cloudformation", region_name="ap-southeast-2")
    current = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    if current["StackId"] != baseline["stacks"][args.stack]["stack_id"]:
        raise ValueError("Live stack identity changed since baseline")
    current_params = {x["ParameterKey"]: x.get("ParameterValue") for x in current.get("Parameters", [])}
    if current_params != baseline["stacks"][args.stack]["parameters"]:
        raise ValueError("Live stack parameters changed since baseline")
    lam = boto3.client('lambda', region_name='ap-southeast-2')
    events = boto3.client('events', region_name='ap-southeast-2')
    output = preview(cf, stack_name, template, parameters, set(args.allow), args.run_id,
        verify_permission=lambda changes: verify_reconcile_permission(cf, lam, events, stack_name, changes),
        retain=args.retain_reviewed)
    args.output.write_text(json.dumps(output, indent=2, default=str) + "\n")
    print(output["verdict"], output.get("issues", []))
    if args.retain_reviewed and output["verdict"] != "SAFE_PREVIEW":
        raise SystemExit("Refusing to retain change set without SAFE_PREVIEW verdict")
    if output["verdict"] == "BLOCKED":
        raise SystemExit(1)
if __name__ == "__main__":
    main()
