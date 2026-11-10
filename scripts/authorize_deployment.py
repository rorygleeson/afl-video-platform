#!/usr/bin/env python3
"""Fail-closed deployment authorization gate; performs NO AWS writes.

A deploy must be bound to a specific reviewed change set, immutable run SHA,
GitHub environment approval, and separately enabled execution implementation.
"""
import argparse
import json
from pathlib import Path

APPROVED_RESOURCES = {"ApiLambda", "ImageWorkerLambda", "VideoWorkerLambda"}


def authorize(plan, baseline, preview, context):
    failures = []
    if context.get("repository") != "rorygleeson/afl-video-platform":
        failures.append("unexpected GitHub repository")
    if context.get("ref") != "refs/heads/main":
        failures.append("deployment must originate from main")
    if not context.get("sha") or len(context["sha"]) != 40:
        failures.append("commit SHA not pinned")
    if context.get("environment") != "afl-production-approval":
        failures.append("protected approval environment not selected")
    if context.get("environment_reviewers_verified") is not True:
        failures.append("GitHub environment protection has not been independently verified")
    if context.get("operator_approved") is not True:
        failures.append("explicit operator approval missing")
    if baseline.get("guardrails", {}).get("deploy_permitted") is not False:
        failures.append("baseline guardrail changed")
    if plan.get("deploy_permitted") is not False:
        failures.append("release plan guardrail changed")
    if baseline.get("verification_gaps"):
        failures.append("unverified live AWS resources")
    if baseline.get("known_drift", {}).get("google_worker") is not True:
        failures.append("Google drift baseline unexpected")
    if preview.get("verdict") != "SAFE_PREVIEW" or preview.get("executed") is not False:
        failures.append("safe unexecuted preview required")
    if preview.get("stack") != "afl-app-dev-backend":
        failures.append("only backend deployment is in scope")
    for item in preview.get("changes", []):
        resource = item.get("ResourceChange", {})
        if resource.get("LogicalResourceId") in APPROVED_RESOURCES:
            if resource.get("Action") != "Modify" or resource.get("Replacement") != "False":
                failures.append("unsafe Lambda modification")
    # The existing preview is deleted after inspection. A new independently
    # approved execution design must retain and execute the SAME change set.
    if not context.get("same_change_set_execution_implemented"):
        failures.append("same-change-set execution and recovery not implemented")
    return failures


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--preview", type=Path, required=True)
    p.add_argument("--context", type=Path, required=True)
    args = p.parse_args()
    failures = authorize(
        json.loads(args.plan.read_text()), json.loads(args.baseline.read_text()),
        json.loads(args.preview.read_text()), json.loads(args.context.read_text()))
    for failure in failures:
        print("BLOCKED:", failure)
    if failures:
        raise SystemExit(1)
    print("Authorization prerequisites satisfied; execution is NOT implemented here")


if __name__ == "__main__":
    main()
