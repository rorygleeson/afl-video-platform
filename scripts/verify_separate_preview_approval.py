#!/usr/bin/env python3
"""Require a separate successful backend preview run before deployment.

Compares the complete CloudFormation resource changes, not a user-supplied
SAFE_PREVIEW label. Review run must use the same commit and be successful.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path


def approve(review, candidate, run, sha):
    if run.get("event") != "workflow_dispatch" or run.get("conclusion") != "success":
        raise ValueError("Reviewed workflow was not a successful manual run")
    if run.get("head_branch") != "main" or run.get("head_sha") != sha:
        raise ValueError("Reviewed run branch or commit does not match deployment")
    if not str(run.get("path", "")).endswith(".github/workflows/release.yml"):
        raise ValueError("Reviewed run is not the unified release")
    if run.get("inputs", {}).get("change_set_preview") != "backend":
        raise ValueError("Reviewed run must be backend preview mode")
    for item in (review, candidate):
        if item.get("verdict") != "SAFE_PREVIEW" or item.get("stack") != "afl-app-dev-backend" or item.get("issues"):
            raise ValueError("Both previews must be safe for the backend")
        if not item.get("changes"):
            raise ValueError("Empty change set is not deployable")
    if review["changes"] != candidate["changes"]:
        raise ValueError("CloudFormation resource changes differ from separately reviewed preview")
    if not candidate.get("retained_for_separate_approval") or not candidate.get("change_set_id"):
        raise ValueError("Deployment change set was not retained")
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--review-run-id", required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--head-sha", required=True)
    args = p.parse_args()
    if not args.review_run_id.isdigit() or not os.getenv("GH_TOKEN"):
        raise SystemExit("Review run ID and GitHub token required")
    repo = os.environ["GITHUB_REPOSITORY"]
    run = json.loads(subprocess.check_output([
        "gh", "api", f"repos/{repo}/actions/runs/{args.review_run_id}"], text=True))
    folder = Path("reviewed-preview")
    folder.mkdir(exist_ok=True)
    subprocess.run([
        "gh", "run", "download", args.review_run_id,
        "--repo", repo, "--name", f"cfn-preview-{args.review_run_id}",
        "--dir", str(folder)], check=True)
    reviewed = json.loads((folder / "change-set-preview.json").read_text())
    candidate = json.loads(args.candidate.read_text())
    approve(reviewed, candidate, run, args.head_sha)
    print("PASS: deployment changes exactly match separately reviewed successful backend preview")


if __name__ == "__main__":
    main()
