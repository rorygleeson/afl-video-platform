"""Authentication-only AWS Lambda for Google Cloud Workload Identity Federation.

There is deliberately no video/model API call in this function. The only
network calls exchange AWS STS identity for a short-lived Google token.
"""
import logging
import os
from google.auth import load_credentials_from_dict
from google.auth.transport.requests import Request

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

PROJECT_NUMBER = os.environ["GOOGLE_PROJECT_NUMBER"]
POOL_ID = os.environ["GOOGLE_WIF_POOL_ID"]
PROVIDER_ID = os.environ["GOOGLE_WIF_PROVIDER_ID"]
SERVICE_ACCOUNT = os.environ["GOOGLE_SERVICE_ACCOUNT"]

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def external_account_info():
    # Mirrors Google's AWS external-account credential configuration, with
    # metadata URLs used only as fallback. On Lambda, AWS_* env vars exist.
    audience = (
        f"//iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/"
        f"workloadIdentityPools/{POOL_ID}/providers/{PROVIDER_ID}"
    )
    return {
        "type": "external_account",
        "audience": audience,
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": (
            "https://iamcredentials.googleapis.com/v1/projects/-/"
            f"serviceAccounts/{SERVICE_ACCOUNT}:generateAccessToken"
        ),
        "credential_source": {
            "environment_id": "aws1",
            "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
            "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
            "regional_cred_verification_url": (
                "https://sts.{region}.amazonaws.com?"
                "Action=GetCallerIdentity&Version=2011-06-15"
            ),
            "imdsv2_session_token_url": "http://169.254.169.254/latest/api/token",
        },
    }


def safe_error_code(exc):
    """Categorise Google auth failures without exposing token or response body."""
    detail = str(exc).lower()
    if ("iamcredentials.googleapis.com" in detail or
            "iam.serviceaccounts.getaccesstoken" in detail):
        if any(x in detail for x in ("disabled", "not been used", "service_disabled", "not enabled")):
            return "GOOGLE_IAM_CREDENTIALS_API_DISABLED"
        return "GOOGLE_SERVICE_ACCOUNT_IMPERSONATION_DENIED"
    if "sts.googleapis.com" in detail:
        if any(x in detail for x in ("disabled", "not been used", "service_disabled")):
            return "GOOGLE_STS_API_DISABLED"
        return "GOOGLE_FEDERATION_STS_REJECTED"
    if "invalid_grant" in detail or "attribute_condition" in detail:
        return "GOOGLE_FEDERATION_REJECTED"
    if "permission_denied" in detail or "permission denied" in detail or "403" in detail:
        return "GOOGLE_PERMISSION_DENIED"
    if "timeout" in detail or "timed out" in detail:
        return "GOOGLE_AUTH_TIMEOUT"
    if "connection" in detail or "name resolution" in detail:
        return "GOOGLE_AUTH_NETWORK_ERROR"
    return "GOOGLE_AUTH_ERROR"


def lambda_handler(event, context):
    if event.get("op") != "auth_check":
        raise ValueError("Only auth_check is available; video generation disabled")
    try:
        creds, _ = load_credentials_from_dict(
            external_account_info(), scopes=[CLOUD_PLATFORM_SCOPE]
        )
        creds.refresh(Request())
        if not creds.valid:
            raise RuntimeError("Google workload federation did not return valid credentials")
    except Exception as exc:
        code = safe_error_code(exc)
        # Do not print the exception: provider responses might contain private tokens.
        LOG.warning("Google WIF check failed: code=%s exception_type=%s", code, type(exc).__name__)
        return {"status": "AUTHENTICATION_FAILED", "errorCode": code,
                "videoGenerationEnabled": False}

    LOG.info("Google workload federation succeeded; no video generation attempted")
    return {"status": "AUTHENTICATED", "videoGenerationEnabled": False}
