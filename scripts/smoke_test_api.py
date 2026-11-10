#!/usr/bin/env python3
"""Read-only API smoke test via the deployed API Gateway endpoint.

GET only. Never creates jobs, uploads images or calls paid generation.
"""
import argparse
import json
import urllib.error
import urllib.request

import boto3


def verify_payload(status, data):
    if status != 200:
        raise ValueError(f"Unexpected API status {status}")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 1:
        raise ValueError("Expected exactly one scenario")
    scenario = scenarios[0]
    if scenario.get("sceneCount") != 3 or not scenario.get("id") or not scenario.get("name"):
        raise ValueError("Scenario metadata is invalid")
    return scenario["id"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stack", default="afl-app-dev-backend")
    args = p.parse_args()
    cf = boto3.client("cloudformation", region_name="ap-southeast-2")
    stack = cf.describe_stacks(StackName=args.stack)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    endpoints = [v.rstrip("/") for k, v in outputs.items()
                 if "api" in k.lower() and v.startswith("https://")]
    if len(endpoints) != 1:
        raise SystemExit("FAIL: exactly one API Gateway HTTPS output is required")
    url = endpoints[0] + "/scenarios"
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            data = json.loads(response.read(65536))
    except (urllib.error.URLError, ValueError) as exc:
        raise SystemExit(f"FAIL: API smoke test failed: {type(exc).__name__}") from exc
    scenario = verify_payload(status, data)
    print(f"PASS: deployed API /scenarios read-only smoke test; scenario={scenario}")


if __name__ == "__main__":
    main()
