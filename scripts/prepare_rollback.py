#!/usr/bin/env python3
"""Prepare a rollback manifest from the exact pre-release AWS baseline.

This records recovery inputs; it does NOT claim rollback is executable or
verified. CloudFormation template bodies must also be retained before deploy.
"""
import argparse
import hashlib
import json
from pathlib import Path

def rollback_manifest(baseline, plan):
    if baseline.get("region") != "ap-southeast-2":
        raise ValueError("Unexpected region")
    if baseline.get("guardrails", {}).get("deploy_permitted") is not False:
        raise ValueError("Unsafe baseline")
    if plan.get("deploy_permitted") is not False:
        raise ValueError("Unsafe release plan")
    if plan.get("blockers") is None:
        raise ValueError("Missing blocker inventory")
    result = {"schema": 1, "region": baseline["region"],
              "captured_utc": baseline["captured_utc"],
              "deployment_executed": False, "rollback_executable": False,
              "requires": ["Save deployed CloudFormation templates for each stack",
                           "Verify artifact objects remain available",
                           "Review and approve rollback change sets",
                           "Revalidate live stack identity and parameter drift"],
              "stacks": {}, "lambdas": baseline["lambdas"],
              "state_machines": baseline.get("state_machines", []),
              "known_live_google_artifact": baseline.get("google_known_live_artifact")}
    for label, entry in plan["stacks"].items():
        original = baseline["stacks"].get(label)
        if not original or original["name"] != entry["name"]:
            raise ValueError(f"Missing stack baseline: {label}")
        if original["parameters"] != entry["current_parameters"]:
            raise ValueError(f"Baseline changed: {label}")
        result["stacks"][label] = {
            "stack_id": original["stack_id"],
            "name": original["name"],
            "previous_parameters": original["parameters"],
            "previous_resources": original["resources"],
            "template_body_saved": False,
        }
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--output", type=Path, default=Path("rollback-manifest.json"))
    args = p.parse_args()
    result = rollback_manifest(json.loads(args.baseline.read_text()),
                               json.loads(args.plan.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Rollback inventory captured; rollback execution remains DISABLED")
if __name__ == "__main__":
    main()
