"""One-off non-billable Google WIF preflight as an AWS CloudFormation custom resource.

Uses the *existing Stage 5A Google worker execution IAM role* to prove exactly
which AWS principal Google sees. Does not call a Vertex/model endpoint, does not
read customer data and does not log access tokens or provider responses.
"""
import json
import logging
import os
import urllib.request

from google.auth import load_credentials_from_dict
from google.auth.transport.requests import Request

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)


def exchange():
    number = os.environ["GOOGLE_PROJECT_NUMBER"]
    pool = os.environ["GOOGLE_WIF_POOL_ID"]
    provider = os.environ["GOOGLE_WIF_PROVIDER_ID"]
    account = os.environ["GOOGLE_SERVICE_ACCOUNT"]
    info = {
        "type": "external_account",
        "audience": f"//iam.googleapis.com/projects/{number}/locations/global/workloadIdentityPools/{pool}/providers/{provider}",
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url":
            f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{account}:generateAccessToken",
        "credential_source": {
            "environment_id": "aws1",
            "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
            "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
            "regional_cred_verification_url":
                "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
            "imdsv2_session_token_url": "http://169.254.169.254/latest/api/token",
        },
    }
    creds, _ = load_credentials_from_dict(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    class BoundedRequest:
        def __init__(self):
            self.delegate = Request()
        def __call__(self, *args, **kwargs):
            kwargs["timeout"] = min(float(kwargs.get("timeout", 8)), 8)
            return self.delegate(*args, **kwargs)
    creds.refresh(BoundedRequest())
    if not creds.valid:
        raise RuntimeError("Invalid Google WIF credentials")
    # Do NOT return token, account data, JWT or provider body.
    return {"AuthStatus": "AUTHENTICATED", "ErrorCode": "NONE"}


def classify(exc):
    detail = str(exc).lower()
    if "iamcredentials.googleapis.com" in detail or "iam.serviceaccounts.getaccesstoken" in detail:
        if "disabled" in detail or "not been used" in detail:
            return "GOOGLE_IAM_CREDENTIALS_API_DISABLED"
        return "GOOGLE_SERVICE_ACCOUNT_IMPERSONATION_DENIED"
    if "sts.googleapis.com" in detail:
        return "GOOGLE_FEDERATION_STS_REJECTED"
    if "invalid_grant" in detail or "attribute_condition" in detail:
        return "GOOGLE_FEDERATION_REJECTED"
    if "permission_denied" in detail or "permission denied" in detail or "403" in detail:
        return "GOOGLE_PERMISSION_DENIED"
    if "timeout" in detail or "timed out" in detail:
        return "GOOGLE_AUTH_TIMEOUT"
    return "GOOGLE_AUTH_ERROR"


def send(event, context, status, reason, data):
    body = json.dumps({
        "Status": status,
        "Reason": reason[:500],
        "PhysicalResourceId": "afl-stage5a-google-wif-preflight",
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data,
    }).encode()
    request = urllib.request.Request(
        event["ResponseURL"], data=body, method="PUT",
        headers={"content-type": "", "content-length": str(len(body))},
    )
    with urllib.request.urlopen(request, timeout=15) as res:
        if res.status != 200:
            raise RuntimeError("CloudFormation response rejected")


def lambda_handler(event, context):
    if event.get("RequestType") == "Delete":
        send(event, context, "SUCCESS", "Deletion requires no work", {})
        return
    if event.get("RequestType") != "Create":
        send(event, context, "SUCCESS", "Preflight runs once, on creation", {
            "AuthStatus": "NOT_RERUN", "ErrorCode": "NONE"})
        return
    try:
        data = exchange()
    except Exception as exc:
        data = {"AuthStatus": "AUTHENTICATION_FAILED", "ErrorCode": classify(exc)}
    LOG.info("Google WIF preflight AuthStatus=%s ErrorCode=%s",
             data["AuthStatus"], data["ErrorCode"])
    # IAM denied is NOT a CloudFormation creation failure; this is a diagnostic.
    send(event, context, "SUCCESS", "Google WIF nonbillable diagnostic completed", data)
