#!/usr/bin/env python3
"""Fail closed on competing application deployment writers.

Only release.yml may modify application CloudFormation stacks, Lambda code,
or frontend objects. The scenario publisher is scoped to scenario assets.
Run with --enforce after legacy writers are retired; until then audit reports
the exact blockers and exits nonzero only in enforce mode.
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
OWNER = "release.yml"
ASSETS = "publish-scenario-assets.yml"
WRITE_PATTERNS = {
    "cloudformation write": r"(?:aws\s+cloudformation\s+(?:deploy|update-stack|create-stack|delete-stack|execute-change-set)|(?:cf|cloudformation)\.(?:update_stack|create_stack|delete_stack|execute_change_set)\s*\()",
    "direct Lambda code update": r"(?:aws\s+lambda\s+update-function-code|(?:lam|lambda_client|lambda_)\.update_function_code\s*\()",
    "frontend publication": r"(?:aws\s+s3\s+(?:cp|sync)\s+[^\n]*frontend/(?:index\.html|app\.js|styles\.css)|aws\s+cloudfront\s+create-invalidation)",
}
LEGACY = {
    "deploy-replicate-identity.yml",
    "deploy-stage5c-paid-scenes.yml",
    "deploy-stage6-assembly.yml",
    "release-google-diagnostics.yml",
}
def audit():
    problems = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        content = path.read_text()
        for kind, pattern in WRITE_PATTERNS.items():
            if re.search(pattern, content, re.I):
                if path.name not in {OWNER}:
                    problems.append((path.name, kind))
        if path.name == ASSETS and re.search(r"cloudformation\s+(?:deploy|delete-stack)|update_function_code", content):
            problems.append((path.name, "scenario publisher must never deploy application code"))
    return sorted(set(problems))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    problems = audit()
    if problems:
        print("COMPETING WRITERS DETECTED (migration incomplete):")
        for filename, kind in problems:
            print(f"  {filename}: {kind}")
    else:
        print("PASS: no competing application writers detected by static audit")
    if args.enforce and problems:
        sys.exit(1)
    if not args.enforce and problems:
        print("Audit-only mode: no deployment allowed until this list is empty.")
