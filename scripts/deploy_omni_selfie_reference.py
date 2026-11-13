#!/usr/bin/env python3
"""One-time guarded deployment: Omni first-frame + optional selfie reference.

Only replaces handler.py inside existing Google worker Lambda ZIP and enables
OMNI_SELFIE_REFERENCE_ENABLED. Does not POST to Google or modify other stacks.
Preserves installed dependencies and restores original code/config on failure.
"""
import base64
import hashlib
import io
from pathlib import Path
import zipfile

import boto3

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
FUNCTION = "afl-app-dev-google-omni-worker"
STACK = "afl-app-dev-google-video"
BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"


def digest(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def main():
    s = boto3.session.Session(region_name=REGION)
    sts, cf, lam, s3 = (s.client(x) for x in ("sts", "cloudformation", "lambda", "s3"))
    ident = sts.get_caller_identity()
    assert ident["Account"] == ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in ident["Arn"]
    stack = cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"] == "UPDATE_COMPLETE"
    resource = cf.describe_stack_resource(StackName=STACK, LogicalResourceId="GoogleWorkerLambda")["StackResourceDetail"]
    assert resource["PhysicalResourceId"] == FUNCTION
    before = lam.get_function_configuration(FunctionName=FUNCTION)
    assert before["State"] == "Active" and before["LastUpdateStatus"] == "Successful"
    assert before["Timeout"] == 300
    assert before["Environment"]["Variables"]["PAID_GENERATION_ENABLED"] == "true"
    assert before["Environment"]["Variables"]["GOOGLE_VIDEO_MODEL"] == "gemini-omni-1.2-flash"
    assert before.get("CodeSha256") and before.get("RevisionId")
    prior_env = dict(before["Environment"]["Variables"])

    # Read exact deployed package; preserve dependencies and all other archive members.
    pkg = lam.get_function(FunctionName=FUNCTION)["Code"]["Location"]
    import urllib.request
    with urllib.request.urlopen(pkg, timeout=35) as response:
        old_zip = response.read(55 * 1024 * 1024)
    assert digest(old_zip) == before["CodeSha256"], "Live package changed during inspection"
    source = Path("src/google_video_worker/handler.py").read_bytes()
    assert b"def load_selfie(item):" in source
    assert b"OMNI_SELFIE_REFERENCE_ENABLED" in source
    assert b'"task": "image_to_video"' in source
    compile(source, "handler.py", "exec")

    original = io.BytesIO(old_zip)
    new = io.BytesIO()
    with zipfile.ZipFile(original) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert names.count("handler.py") == 1 and names.count("video_prompts.py") == 1
        old_handler = archive.read("handler.py")
        assert b"def image_payload(" in old_handler and b"def submit(" in old_handler
        with zipfile.ZipFile(new, "w") as target:
            for member in archive.infolist():
                target.writestr(member, source if member.filename == "handler.py" else archive.read(member.filename))
    new_zip = new.getvalue()
    with zipfile.ZipFile(io.BytesIO(new_zip)) as check:
        assert check.testzip() is None
        assert check.read("handler.py") == source
        assert check.read("video_prompts.py") == zipfile.ZipFile(io.BytesIO(old_zip)).read("video_prompts.py")
    new_sha = digest(new_zip)

    # Immutable S3 backup for recovery, then code/config update (no model invocation).
    backup_key = "releases/omni-selfie-reference/backup-" + hashlib.sha256(old_zip).hexdigest() + ".zip"
    s3.put_object(Bucket=BUCKET, Key=backup_key, Body=old_zip)
    assert digest(s3.get_object(Bucket=BUCKET, Key=backup_key)["Body"].read()) == before["CodeSha256"]
    print("BACKUP_VERIFIED", backup_key, flush=True)

    changed = False
    try:
        if new_sha != before["CodeSha256"]:
            lam.update_function_code(FunctionName=FUNCTION, ZipFile=new_zip, RevisionId=before["RevisionId"])
            changed = True
            lam.get_waiter("function_updated").wait(FunctionName=FUNCTION, WaiterConfig={"Delay":5,"MaxAttempts":48})
        current = lam.get_function_configuration(FunctionName=FUNCTION)
        assert current["LastUpdateStatus"] == "Successful"
        env = dict(current["Environment"]["Variables"])
        env["OMNI_SELFIE_REFERENCE_ENABLED"] = "true"
        if env != current["Environment"]["Variables"]:
            lam.update_function_configuration(FunctionName=FUNCTION, Environment={"Variables":env},
                                              RevisionId=current["RevisionId"])
            changed = True
            lam.get_waiter("function_updated").wait(FunctionName=FUNCTION, WaiterConfig={"Delay":5,"MaxAttempts":48})
        after = lam.get_function_configuration(FunctionName=FUNCTION)
        assert after["CodeSha256"] == new_sha
        assert after["Environment"]["Variables"]["OMNI_SELFIE_REFERENCE_ENABLED"] == "true"
        assert after["State"] == "Active" and after["LastUpdateStatus"] == "Successful"
        assert after["Timeout"] == before["Timeout"]
        for key in prior_env:
            assert after["Environment"]["Variables"][key] == prior_env[key]
        print("OMNI_SELFIE_REFERENCE_DEPLOYED_VERIFIED", flush=True)
        print("No Google paid generation, prompts, templates, or state-machine changes.", flush=True)
    except Exception:
        if changed:
            print("DEPLOY_FAILED_ROLLING_BACK", flush=True)
            current = lam.get_function_configuration(FunctionName=FUNCTION)
            if current.get("LastUpdateStatus") == "InProgress":
                lam.get_waiter("function_updated").wait(FunctionName=FUNCTION)
                current = lam.get_function_configuration(FunctionName=FUNCTION)
            if current["CodeSha256"] != before["CodeSha256"]:
                lam.update_function_code(FunctionName=FUNCTION, ZipFile=old_zip,
                                         RevisionId=current["RevisionId"])
                lam.get_waiter("function_updated").wait(FunctionName=FUNCTION)
                current = lam.get_function_configuration(FunctionName=FUNCTION)
            if current["Environment"]["Variables"] != prior_env:
                lam.update_function_configuration(
                    FunctionName=FUNCTION, Environment={"Variables":prior_env},
                    RevisionId=current["RevisionId"])
                lam.get_waiter("function_updated").wait(FunctionName=FUNCTION)
            restored = lam.get_function_configuration(FunctionName=FUNCTION)
            assert restored["CodeSha256"] == before["CodeSha256"]
            assert restored["Environment"]["Variables"] == prior_env
            print("ROLLBACK_VERIFIED", flush=True)
        raise


if __name__ == "__main__":
    main()
