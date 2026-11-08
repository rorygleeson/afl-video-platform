"""Direct, free AWS->Google WIF diagnostic with NO CloudFormation callback and NO video."""
from handler import exchange, classify

def lambda_handler(event, context):
    if event.get("op") != "auth_check":
        return {"status": "REJECTED", "errorCode": "AUTH_CHECK_ONLY"}
    try:
        result = exchange()
        return {"status": result["AuthStatus"], "errorCode": result["ErrorCode"]}
    except Exception as exc:
        details = str(exc).lower()
        reason = classify(exc)
        # Diagnostic categories only, never log raw Google/AWS response or tokens.
        flags = {
            "mentionsSts": "sts.googleapis.com" in details,
            "mentionsIamCredentials": "iamcredentials.googleapis.com" in details,
            "mentionsRegion": "region" in details,
            "mentionsAwsCredentials": "aws" in details and "credential" in details,
            "mentionsPermission": any(x in details for x in ("403", "permission", "denied")),
            "mentionsTypeError": isinstance(exc, TypeError),
            "mentionsAttributeError": isinstance(exc, AttributeError),
            "mentionsRefreshError": type(exc).__name__ == "RefreshError",
            "mentionsHttpError": "http" in type(exc).__name__.lower(),
            "mentionsConnection": any(x in details for x in ("connection", "network", "timeout", "timed out")),
        }
        known_oauth_codes = (
            "invalid_target", "invalid_grant", "invalid_request", "invalid_subject_token",
            "invalid_credential", "unauthorized_client", "unauthorized", "access_denied",
            "permission_denied", "invalid_client", "invalid_scope", "invalid_token",
            "invalid_audience", "service_account", "workload_identity", "attribute_condition",
            "subject_token", "malformed", "impersonation", "principal",
        )
        codes = [token for token in known_oauth_codes if token in details]
        return {"status": "AUTHENTICATION_FAILED", "errorCode": reason,
                "safeOauthCodes": codes,
                "errorType": type(exc).__name__, "diagnostics": flags}
