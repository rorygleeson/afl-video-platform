#!/usr/bin/env python3
"""Read-only guarded CloudFormation migration change-set preview. NEVER executes."""
import json
import os
from pathlib import Path

import boto3
import yaml

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
STACKS = (
    ("backend", "afl-app-dev-backend", "infrastructure/app/backend.yaml"),
    ("watchdog", "afl-app-dev-handoff-watchdog", "infrastructure/app/handoff-watchdog.yaml"),
)
# Exact source-only transformations approved in PR #107.
EXPECTED = {
    "backend": {
        ("Resources", "ProxyAny", "Properties", "Integration", "Uri"): (
            {"Fn::Sub": "arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${ApiLambda.Arn}/invocations"},
            {"Fn::Sub": "arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:afl-app-dev-api/invocations"}),
    },
    "watchdog": {
        ("Resources", "WatchdogMachineRole", "Properties", "Policies", 0, "PolicyDocument", "Statement", 0, "Resource"): (
            {"Fn::GetAtt": ["WatchdogLambda", "Arn"]},
            {"Fn::Sub": "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:afl-app-dev-handoff-watchdog"}),
        ("Resources", "WatchdogMachine", "Properties", "DefinitionString"): (
            None, None),  # Validate the single exact string substitution below.
        ("Resources", "EveryMinute", "Properties", "Targets", 0, "Arn"): (
            {"Fn::GetAtt": ["WatchdogLambda", "Arn"]},
            {"Fn::Sub": "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:afl-app-dev-handoff-watchdog"}),
        ("Resources", "InvokeFromEventBridge", "Properties", "FunctionName"): (
            {"Fn::GetAtt": ["WatchdogLambda", "Arn"]}, "afl-app-dev-handoff-watchdog"),
        ("Resources", "InvokeFromEventBridge", "Properties", "SourceArn"): (
            {"Fn::GetAtt": ["EveryMinute", "Arn"]},
            {"Fn::Sub": "arn:${AWS::Partition}:events:${AWS::Region}:${AWS::AccountId}:rule/afl-app-dev-handoff-watchdog-image-start"}),
    },
}


class CfnLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, suffix, node):
    value = (loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
             else loader.construct_sequence(node, deep=True) if isinstance(node, yaml.SequenceNode)
             else loader.construct_mapping(node, deep=True))
    if suffix == "Ref":
        return {"Ref": value}
    if suffix == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {"Fn::" + suffix: value}


CfnLoader.add_multi_constructor("!", intrinsic)


def parse_template(value):
    if isinstance(value, dict):
        return value
    return yaml.load(value, Loader=CfnLoader)


def at(root, path):
    result = root
    for part in path:
        result = result[part]
    return result


def differences(a, b, path=()):
    """Yield only paths; never include live configuration values in logs."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                yield ".".join(map(str, path + (key,)))
            else:
                yield from differences(a[key], b[key], path + (key,))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield ".".join(map(str, path)) + ".length"
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                yield from differences(x, y, path + (i,))
    elif a != b:
        yield ".".join(map(str, path))


def validate_exact_template_transition(old, source, label):
    """Build an EXACT-live-derived template; source is an approval reference only.

    Unrelated source/live differences are reported, but NEVER included in the
    CloudFormation change-set template. Only enumerated fields can change.
    """
    migrated = json.loads(json.dumps(old))
    for path, (old_value, new_value) in EXPECTED[label].items():
        parent = at(migrated, path[:-1])
        key = path[-1]
        actual = parent[key]
        if label == "watchdog" and key == "DefinitionString":
            old_string = "${WatchdogLambda.Arn}"
            new_string = "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:afl-app-dev-handoff-watchdog"
            if not isinstance(actual, dict) or set(actual) != {"Fn::Sub"} or actual["Fn::Sub"].count(old_string) != 1:
                raise ValueError("Live watchdog state machine definition differs from expected stable reference")
            target = {"Fn::Sub": actual["Fn::Sub"].replace(old_string, new_string)}
        else:
            if actual != old_value:
                raise ValueError("Unexpected live template at " + label + "." + ".".join(map(str, path)))
            target = new_value
        # Cross-check approved source property; it cannot supply any other fields.
        if at(source, path) != target:
            raise ValueError("Source approved reference differs at " + label + "." + ".".join(map(str, path)))
        parent[key] = target
    if not list(differences(old, migrated)):
        raise ValueError("No approved migration changes detected: " + label)
    # The report identifies pre-existing source/live divergence, not a migration.
    excluded_source_differences = list(differences(migrated, source))
    return migrated, excluded_source_differences


def summarize_change_set(cf, name, stack):
    first = cf.describe_change_set(StackName=stack, ChangeSetName=name)
    if first["Status"] != "CREATE_COMPLETE":
        raise ValueError("Change set not ready for " + stack)
    items = list(first.get("Changes", []))
    token = first.get("NextToken")
    while token:
        page = cf.describe_change_set(StackName=stack, ChangeSetName=name, NextToken=token)
        items.extend(page.get("Changes", []))
        token = page.get("NextToken")
    results = []
    for entry in items:
        resource = entry["ResourceChange"]
        results.append({
            "logical_id": resource.get("LogicalResourceId"),
            "type": resource.get("ResourceType"),
            "action": resource.get("Action"),
            "replacement": resource.get("Replacement"),
            "details": [{
                "property": x.get("Target", {}).get("Name"),
                "requires_recreation": x.get("Target", {}).get("RequiresRecreation"),
                "source": x.get("ChangeSource"),
                "evaluation": x.get("Evaluation"),
            } for x in resource.get("Details", [])],
        })
    return results


def main():
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Only main may preview live migration")
    sts = boto3.client("sts", region_name=REGION)
    identity = sts.get_caller_identity()
    if (identity["Account"] != ACCOUNT
            or ":assumed-role/afl-video-github-deploy-role/" not in identity["Arn"]):
        raise ValueError("Unexpected AWS identity")
    cf = boto3.client("cloudformation", region_name=REGION)
    report = {"verdict": "REVIEW_REQUIRED_NO_DEPLOYMENT",
              "git_sha": os.environ["GITHUB_SHA"], "stacks": []}
    created = []
    try:
        for label, stack_name, path in STACKS:
            stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
            if stack["StackStatus"] != "UPDATE_COMPLETE":
                raise ValueError("Stack is not steady: " + label)
            raw_live = cf.get_template(StackName=stack_name)["TemplateBody"]
            raw_source = Path(path).read_text()
            migrated, excluded_paths = validate_exact_template_transition(
                parse_template(raw_live), parse_template(raw_source), label)
            proposed_body = json.dumps(migrated, separators=(",", ":"))
            # Prove serialization preserved the exact reviewed migration tree.
            if json.loads(proposed_body) != migrated:
                raise ValueError("Migration template serialization changed semantics")
            name = "stable-references-" + os.environ["GITHUB_RUN_ID"] + "-" + label
            params = [{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
                      for p in stack.get("Parameters", [])]
            cf.create_change_set(
                StackName=stack_name, ChangeSetName=name, ChangeSetType="UPDATE",
                TemplateBody=proposed_body, Parameters=params,
                Capabilities=["CAPABILITY_NAMED_IAM"])
            created.append((stack_name, name))
            cf.get_waiter("change_set_create_complete").wait(
                StackName=stack_name, ChangeSetName=name,
                WaiterConfig={"Delay": 5, "MaxAttempts": 48})
            report["stacks"].append({
                "label": label, "stack_id": stack["StackId"],
                "excluded_source_only_path_count": len(excluded_paths),
                "excluded_source_only_paths": excluded_paths[:30],
                "changes": summarize_change_set(cf, name, stack_name),
            })
        Path("reference-migration-preview.json").write_text(json.dumps(report, indent=2) + "\n")
        print("REVIEW_REQUIRED_NO_DEPLOYMENT", json.dumps(report["stacks"]), flush=True)
        print("NO CLOUDFORMATION CHANGE SETS WERE EXECUTED", flush=True)
    finally:
        for stack_name, name in created:
            cf.delete_change_set(StackName=stack_name, ChangeSetName=name)


if __name__ == "__main__":
    main()
