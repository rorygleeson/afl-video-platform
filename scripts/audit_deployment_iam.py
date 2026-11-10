#!/usr/bin/env python3
"""Read-only AWS IAM deployment and rollback permission audit.

IAM simulation is advisory: it cannot prove CloudFormation resource execution,
service role permissions, SCPs, or all resource conditions. Fail closed.
"""
import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ROLE = "arn:aws:iam::134387458447:role/afl-video-github-deploy-role"
ACTIONS = [
    "cloudformation:CreateChangeSet",
    "cloudformation:DescribeChangeSet",
    "cloudformation:ExecuteChangeSet",
    "cloudformation:DeleteChangeSet",
    "cloudformation:DescribeStacks",
    "cloudformation:GetTemplate",
    "cloudformation:UpdateStack",
    "s3:GetObject",
    "s3:PutObject",
    "lambda:GetFunctionConfiguration",
    "lambda:GetFunction",
    "states:DescribeStateMachine",
]
RESOURCES = {
    "cloudformation:": "arn:aws:cloudformation:ap-southeast-2:134387458447:stack/afl-app-dev-backend/*",
    "s3:": "arn:aws:s3:::afl-video-bootstrap-artifactbucket-xiuriidgyft4/release/*",
    "lambda:": "arn:aws:lambda:ap-southeast-2:134387458447:function:afl-app-dev-api",
    "states:": "arn:aws:states:ap-southeast-2:134387458447:stateMachine:afl-app-dev-scene1-pilot",
}


def evaluate(results):
    failures = []
    for item in results:
        decision = item.get("EvalDecision")
        if decision != "allowed":
            failures.append(f"{item.get('EvalActionName')} on {item.get('EvalResourceName')}: {decision}")
    return failures


def main():
    sts = boto3.client("sts", region_name=REGION)
    identity = sts.get_caller_identity()
    if identity["Account"] != "134387458447" or ":assumed-role/afl-video-github-deploy-role/" not in identity["Arn"]:
        raise SystemExit("FAIL: unexpected AWS role identity")
    iam = boto3.client("iam", region_name=REGION)
    failures = []
    for action in ACTIONS:
        prefix = action.split(":")[0] + ":"
        resource = RESOURCES[prefix]
        try:
            response = iam.simulate_principal_policy(
                PolicySourceArn=ROLE, ActionNames=[action], ResourceArns=[resource])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("AccessDenied", "AccessDeniedException"):
                raise SystemExit(
                    "AUDIT BLOCKED (NOT A DEPLOYMENT PERMISSION FAILURE): "
                    "GitHub deployment role lacks iam:SimulatePrincipalPolicy. "
                    "Do not grant broad IAM privileges to the deployment role. "
                    "Have an AWS administrator run this read-only simulation separately "
                    "or verify exact permissions through a separately authorized audit role. "
                    "Deployment readiness remains UNVERIFIED."
                ) from None
            raise
        failures.extend(evaluate(response["EvaluationResults"]))
    if failures:
        for failure in failures:
            print("FAIL:", failure)
        raise SystemExit(1)
    print("PASS: IAM policy simulator allowed listed deployment/rollback actions")
    print("NOTE: simulation is not proof of CloudFormation execution or rollback success")


if __name__ == "__main__":
    main()
