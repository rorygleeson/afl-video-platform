"""Direct, free AWS->Google WIF diagnostic with NO CloudFormation callback and NO video."""
from handler import exchange, classify

def lambda_handler(event, context):
    if event.get("op") != "auth_check":
        return {"status": "REJECTED", "errorCode": "AUTH_CHECK_ONLY"}
    try:
        result = exchange()
        return {"status": result["AuthStatus"], "errorCode": result["ErrorCode"]}
    except Exception as exc:
        return {"status": "AUTHENTICATION_FAILED", "errorCode": classify(exc)}
