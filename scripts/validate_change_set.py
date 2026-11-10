#!/usr/bin/env python3
"""Fail-closed gate for CloudFormation describe-change-set JSON.

Usage: aws cloudformation describe-change-set ... > changes.json
       python scripts/validate_change_set.py changes.json --stack google --allow GoogleWorkerLambda
No change-set execution occurs here.
"""
import argparse
import json
from pathlib import Path

def validate(doc, allowed):
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
        if logical not in allowed:
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
