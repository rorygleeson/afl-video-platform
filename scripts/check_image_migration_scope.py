#!/usr/bin/env python3
"""Offline fail-closed scope check for the Replicate -> Vertex image migration.

Inputs are YAML templates from *the same* stack. This checks code review scope;
it does not substitute for a live AWS CloudFormation change-set preview.
"""
import argparse
import copy
import json
from pathlib import Path
import yaml

RESOURCE_ALLOWLIST = {"ImageWorkerLambda", "ImageWorkerRole", "ImageStateMachine"}
LAMBDA_PROPS = {"Code", "Environment", "MemorySize"}
ROLE_POLICY_NAME = "PilotImageWorker"


class CloudFormationLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    key = "Ref" if suffix == "Ref" else "Fn::" + suffix
    if suffix == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {key: value}


CloudFormationLoader.add_multi_constructor("!", intrinsic)


def load(path):
    document = yaml.load(Path(path).read_text(), Loader=CloudFormationLoader)
    if not isinstance(document, dict):
        raise ValueError("CloudFormation template is not a mapping")
    return document


def assert_scoped(before, after):
    if before.get("Parameters") != after.get("Parameters"):
        raise ValueError("CloudFormation stack parameters changed")
    for section in ("Conditions", "Mappings", "Outputs", "Transform", "Rules"):
        if before.get(section) != after.get(section):
            raise ValueError(f"Unrelated top-level section changed: {section}")
    old_resources = before.get("Resources", {})
    new_resources = after.get("Resources", {})
    if set(old_resources) != set(new_resources):
        raise ValueError("Resource identities changed")
    changed = {key for key in old_resources if old_resources[key] != new_resources[key]}
    if changed != RESOURCE_ALLOWLIST:
        raise ValueError(f"Unexpected resource scope: {sorted(changed)}")

    old_lambda = old_resources["ImageWorkerLambda"]
    new_lambda = new_resources["ImageWorkerLambda"]
    for key in set(old_lambda) | set(new_lambda):
        if key != "Properties" and old_lambda.get(key) != new_lambda.get(key):
            raise ValueError(f"Image Lambda metadata changed: {key}")
    old_props = old_lambda["Properties"]
    new_props = new_lambda["Properties"]
    changed_props = {key for key in set(old_props) | set(new_props)
                     if old_props.get(key) != new_props.get(key)}
    if not changed_props <= LAMBDA_PROPS or not {"Environment", "MemorySize"} <= changed_props:
        raise ValueError(f"Image Lambda properties out of scope: {sorted(changed_props)}")
    old_vars = old_props["Environment"]["Variables"]
    new_vars = new_props["Environment"]["Variables"]
    assert old_vars.get("REPLICATE_SECRET_NAME"), "Missing original Replicate secret variable"
    if "REPLICATE_SECRET_NAME" in new_vars:
        raise ValueError("Replicate secret still configured")
    expected_vars = copy.deepcopy(old_vars)
    del expected_vars["REPLICATE_SECRET_NAME"]
    expected_vars.update({
        "GOOGLE_PROJECT_ID": "afl-video-platform",
        "GOOGLE_PROJECT_NUMBER": "345956846110",
        "GOOGLE_WIF_POOL_ID": "afl-video-aws",
        "GOOGLE_WIF_PROVIDER_ID": "afl-aws-dev",
        "GOOGLE_SERVICE_ACCOUNT": "afl-video-worker@afl-video-platform.iam.gserviceaccount.com",
        "GOOGLE_IMAGE_MODEL": "gemini-3-pro-image",
    })
    if new_vars != expected_vars:
        raise ValueError("Unexpected image worker environment change")

    old_role = copy.deepcopy(old_resources["ImageWorkerRole"])
    new_role = copy.deepcopy(new_resources["ImageWorkerRole"])
    old_policies = old_role["Properties"]["Policies"]
    new_policies = new_role["Properties"]["Policies"]
    if len(old_policies) != 1 or len(new_policies) != 1:
        raise ValueError("Unexpected IAM policy count")
    if old_policies[0].get("PolicyName") != ROLE_POLICY_NAME:
        raise ValueError("Unexpected old IAM policy")
    old_statements = old_policies[0]["PolicyDocument"]["Statement"]
    new_statements = new_policies[0]["PolicyDocument"]["Statement"]
    if [stmt for stmt in old_statements if stmt.get("Sid") != "ReadReplicateToken"] != new_statements:
        raise ValueError("Image role contains unrelated IAM changes")
    old_role["Properties"]["Policies"] = new_policies
    if old_role != new_role:
        raise ValueError("Image worker role changed beyond removed Replicate permission")

    old_machine = copy.deepcopy(old_resources["ImageStateMachine"])
    new_machine = copy.deepcopy(new_resources["ImageStateMachine"])
    old_def = old_machine["Properties"]["Definition"]
    new_def = new_machine["Properties"]["Definition"]
    old_scenes = old_def["States"]["ParallelScenes"]["ItemProcessor"]
    new_scenes = new_def["States"]["ParallelScenes"]["ItemProcessor"]
    if old_scenes["ProcessorConfig"] != new_scenes["ProcessorConfig"]:
        raise ValueError("Image Map processor configuration changed")
    if new_scenes["StartAt"] != "Generate" or set(new_scenes["States"]) != {"Generate", "MarkSceneFailed"}:
        raise ValueError("Image Map branches not limited to Generate/Failed")
    new_states = new_scenes["States"]
    if new_states["Generate"].get("Retry") or new_states["Generate"].get("Next"):
        raise ValueError("Billable Generate must not have a Step Functions retry")
    if new_states["Generate"]["Catch"][0]["Next"] != "MarkSceneFailed":
        raise ValueError("Image failure handler missing")
    old_machine["Properties"]["Definition"]["Comment"] = new_def["Comment"]
    old_machine["Properties"]["Definition"]["States"]["ParallelScenes"]["ItemProcessor"] = new_scenes
    if old_machine != new_machine:
        raise ValueError("Image state machine changed beyond scene operation and comment")
    if new_def["States"]["ParallelScenes"]["MaxConcurrency"] != 3:
        raise ValueError("Three-scene concurrency changed")
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    args = parser.parse_args()
    changed = assert_scoped(load(args.before), load(args.after))
    print("PASS: offline image migration scope:", ", ".join(sorted(changed)))
    print("No AWS resources touched; live CloudFormation change set still required.")


if __name__ == "__main__":
    main()
