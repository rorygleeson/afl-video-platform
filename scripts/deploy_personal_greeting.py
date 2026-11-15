#!/usr/bin/env python3
"""Guarded personal-greeting release: only API and assembly Lambda CODE + frontend.

Keep existing CloudFormation templates, parameters, Lambda runtimes/roles,
Step Functions, payment gates, video scene generation and published prompts.
Fail closed on drift, non-code direct changes, unsupported FFmpeg filters or
unknown dependent-resource changes. Never start generation/assembly jobs.
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "134387458447"
BUCKET = "afl-video-bootstrap-artifactbucket-xiuriidgyft4"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/afl-video-cfn-execution-role"
PLANS = (
    ("assembly", "afl-app-dev-assembly", "AssemblyWorkerArtifactKey",
     "AssemblyWorker", "afl-app-dev-assembly"),
    ("api", "afl-app-dev-backend", "LambdaArtifactKey",
     "ApiLambda", "afl-app-dev-api"),
)


def digest(raw):
    return base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii")


def zip_package(kind, old, font):
    output = io.BytesIO()
    dependencies = Path("dist/greeting-deps")
    if kind == "assembly":
        assert (dependencies / "PIL" / "__init__.py").is_file(), (
            "Pillow dependency package not bundled"
        )
    with zipfile.ZipFile(io.BytesIO(old)) as original:
        assert original.testzip() is None
        names = original.namelist()
        assert names.count("handler.py") == 1
        if kind == "api":
            assert names.count("video_prompts.py") == 1
            replacements = {
                "handler.py": Path("src/api/handler.py").read_bytes(),
                "video_prompts.py": Path("src/api/video_prompts.py").read_bytes(),
            }
        else:
            replacements = {"handler.py": Path("src/assembly_worker/handler.py").read_bytes(),
                            "DejaVuSans.ttf": font}
        with zipfile.ZipFile(output, "w") as z:
            for member in original.infolist():
                if member.filename == "DejaVuSans.ttf":
                    continue
                z.writestr(member, replacements.get(member.filename,
                                                    original.read(member.filename)))
            if kind == "assembly":
                z.writestr("DejaVuSans.ttf", font)
                for local in sorted(dependencies.rglob("*")):
                    if local.is_file() and "__pycache__" not in local.parts:
                        key = local.relative_to(dependencies).as_posix()
                        assert key not in names, "Worker dependency conflict: " + key
                        z.write(local, key, compress_type=zipfile.ZIP_DEFLATED)
    payload = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        assert z.testzip() is None
        for name, value in replacements.items():
            assert z.read(name) == value
        assert set(names).issubset(set(z.namelist()))
        if kind == "assembly":
            assert z.read("DejaVuSans.ttf") == font
            assert "PIL/__init__.py" in z.namelist()
    return payload


def check_ffmpeg_layer(s3, key, font):
    """Test the exact live static FFmpeg against PNG overlay, fade and tpad."""
    from PIL import Image, ImageDraw, ImageFont

    with tempfile.TemporaryDirectory() as location:
        root = Path(location)
        blob = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.testzip() is None
            assert "bin/ffmpeg" in archive.namelist() and "bin/ffprobe" in archive.namelist()
            archive.extract("bin/ffmpeg", root)
            archive.extract("bin/ffprobe", root)
        ffmpeg = root / "bin/ffmpeg"
        ffprobe = root / "bin/ffprobe"
        ffmpeg.chmod(0o755)
        ffprobe.chmod(0o755)
        filters = subprocess.run([str(ffmpeg), "-filters"], capture_output=True,
                                 text=True, timeout=20, check=True).stdout
        for required in (" overlay ", " tpad ", " fade "):
            assert required in filters, "STOP: deployed FFmpeg missing " + required
        print("LIVE_FFMPEG_FILTERS_AVAILABLE: overlay, tpad, fade", flush=True)
        card = Image.new("RGBA", (720,1280), (0,0,0,0))
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle((24,1040,696,1235),radius=18,fill=(0,0,0,188))
        font_file = root / "font.ttf"
        font_file.write_bytes(font)
        text_font = ImageFont.truetype(str(font_file),35)
        draw.text((70,1100),"Happy Birthday Dad",font=text_font,fill="white")
        picture = root / "card.png"
        card.save(picture)
        sample = root / "test.mp4"
        vf = ("[0:v]tpad=stop_mode=clone:stop_duration=2[base];"
              "[1:v]format=rgba,fade=t=in:st=0.500:d=0.5:alpha=1[card];"
              "[base][card]overlay=0:0:shortest=1:format=auto[v]")
        result = subprocess.run(
            [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=blue:s=720x1280:r=15:d=1",
             "-loop", "1", "-framerate", "30", "-i", str(picture),
             "-filter_complex", vf, "-map", "[v]", "-c:v", "libx264",
             "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", "3",
             "-y", str(sample)], capture_output=True, text=True, timeout=90)
        if result.returncode:
            print("LIVE_FFMPEG_RENDER_PRECHECK_FAILED:",
                  result.stderr[-1200:],flush=True)
            raise RuntimeError("Deployed FFmpeg cannot composite PNG greetings")
        probe = subprocess.run([str(ffprobe), "-v", "error", "-show_entries",
                                "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                                str(sample)], capture_output=True, text=True,
                               timeout=15, check=True)
        assert float(probe.stdout.strip()) >= 2.9
        print("LIVE_FFMPEG_LAYER_GREETING_RENDER_VERIFIED", flush=True)


def changes(cf, p, run):
    name = "personal-greeting-" + p["kind"] + "-" + run
    cf.create_change_set(
        StackName=p["stack"], ChangeSetName=name, ChangeSetType="UPDATE",
        UsePreviousTemplate=True,
        Parameters=[{"ParameterKey":k,
                     **({"ParameterValue":p["key"]} if k == p["parameter"]
                        else {"UsePreviousValue":True})}
                    for k in p["parameters"]],
        RoleARN=ROLE, Capabilities=["CAPABILITY_NAMED_IAM"],
        Description="Personal greeting only; Lambda code packages with no model calls",
    )
    cf.get_waiter("change_set_create_complete").wait(
        StackName=p["stack"], ChangeSetName=name,
        WaiterConfig={"Delay":5,"MaxAttempts":36},
    )
    page=cf.describe_change_set(StackName=p["stack"],ChangeSetName=name)
    assert not page.get("NextToken"), "Paginated change set not allowed"
    changes=[entry["ResourceChange"] for entry in page["Changes"]]
    allowed = ({"AssemblyWorker","AssemblyStateMachineRole","AssemblyStateMachine"}
               if p["kind"] == "assembly"
               else {"ApiLambda","InvokePermission","ProxyAny"})
    ids={item["LogicalResourceId"] for item in changes}
    assert p["logical"] in ids and ids <= allowed, (
        f"Unexpected resources in {p['kind']} change set: {sorted(ids)}"
    )
    for item in changes:
        name_id = item["LogicalResourceId"]
        print("CHANGE",p["kind"],name_id,item["Action"],item.get("Replacement"),
              item.get("Details"), flush=True)
        assert item["Action"] == "Modify"
        assert item.get("Replacement") in ("False",False,"Conditional")
        for detail in item.get("Details",[]):
            target=detail.get("Target",{})
            assert target.get("Attribute") == "Properties"
            if name_id == p["logical"]:
                assert item.get("Replacement") in ("False",False)
                assert target.get("Name") == "Code", (
                    "Direct worker/API setting change forbidden"
                )
            else:
                assert detail.get("Evaluation") == "Dynamic"
                assert detail.get("ChangeSource") in ("ResourceAttribute","ResourceReference")
                assert target.get("Name") in ("FunctionName","Integration",
                        "Policies","RoleArn","DefinitionString","Definition")
    print("SAFE_CHANGESET",p["kind"],flush=True)
    return name


def main():
    session=boto3.Session(region_name=REGION)
    sts=session.client("sts")
    ident=sts.get_caller_identity()
    assert ident["Account"]==ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in ident["Arn"]
    cf=session.client("cloudformation")
    lam=session.client("lambda")
    s3=session.client("s3")
    sf=session.client("stepfunctions")
    run=os.environ["GITHUB_RUN_ID"]
    font=Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").read_bytes()
    assert len(font)>100_000

    plans=[]
    for kind,stack_name,param,logical,function in PLANS:
        stack=cf.describe_stacks(StackName=stack_name)["Stacks"][0]
        assert stack["StackStatus"]=="UPDATE_COMPLETE"
        assert stack["RoleARN"]==ROLE
        params={p["ParameterKey"]:p["ParameterValue"] for p in stack["Parameters"]}
        assert params["ArtifactBucketName"]==BUCKET
        fn=lam.get_function_configuration(FunctionName=function)
        assert fn["State"]=="Active" and fn["LastUpdateStatus"]=="Successful"
        old=s3.get_object(Bucket=BUCKET,Key=params[param])["Body"].read()
        assert digest(old)==fn["CodeSha256"],"Live Lambda/CFN package mismatch"
        if kind=="assembly":
            assert fn["Timeout"]==600
            check_ffmpeg_layer(s3, params["FfmpegLayerArtifactKey"],font)
            arn=cf.describe_stack_resource(StackName=stack_name,
                   LogicalResourceId="AssemblyStateMachine")["StackResourceDetail"]["PhysicalResourceId"]
            live_sf=sf.describe_state_machine(stateMachineArn=arn)
            assert live_sf["status"]=="ACTIVE"
            # Do not change the assembly workflow, or replace its Lambda.
            machine=(arn,live_sf["definition"],live_sf["roleArn"])
        else:
            assert params["PaidVideoEnabled"]=="true"
            machine=None
        payload=zip_package(kind,old,font)
        # Check whether a partially successful prior deployment already installed this.
        if digest(payload)==fn["CodeSha256"]:
            print("ALREADY_DEPLOYED_VERIFIED",kind,flush=True)
            continue
        sha=hashlib.sha256(payload).hexdigest()
        key=f"releases/personal-greeting/{sha}/{kind}.zip"
        try:
            exists=s3.get_object(Bucket=BUCKET,Key=key)["Body"].read()
            assert exists==payload
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404","NoSuchKey","NotFound"):
                raise
            s3.put_object(Bucket=BUCKET,Key=key,Body=payload)
            assert s3.get_object(Bucket=BUCKET,Key=key)["Body"].read()==payload
        plans.append(dict(kind=kind,stack=stack_name,parameter=param,logical=logical,
                          function=function,params=params,parameters=params,
                          key=key,original_sha=fn["CodeSha256"],expected=digest(payload),
                          machine=machine,stack_id=stack["StackId"]))
    # Preflight all currently required changes before touching an AWS runtime.
    try:
        for p in plans:
            p["change"]=changes(cf,p,run)
    except Exception:
        for p in plans:
            if "change" in p:
                try:cf.delete_change_set(StackName=p["stack"],ChangeSetName=p["change"])
                except ClientError:pass
        raise

    for p in plans:
        before=cf.describe_stacks(StackName=p["stack"])["Stacks"][0]
        assert before["StackId"]==p["stack_id"] and before["StackStatus"]=="UPDATE_COMPLETE"
        fn=lam.get_function_configuration(FunctionName=p["function"])
        assert fn["CodeSha256"]==p["original_sha"]
        cf.execute_change_set(StackName=p["stack"],ChangeSetName=p["change"])
        print("CFN_CODE_UPDATE_SUBMITTED",p["kind"],flush=True)
        cf.get_waiter("stack_update_complete").wait(
            StackName=p["stack"],WaiterConfig={"Delay":10,"MaxAttempts":80})
        after=cf.describe_stacks(StackName=p["stack"])["Stacks"][0]
        assert after["StackStatus"]=="UPDATE_COMPLETE"
        changed={a["ParameterKey"]:a["ParameterValue"] for a in after["Parameters"]}
        assert all(changed[k]==(p["key"] if k==p["parameter"] else v)
                   for k,v in p["parameters"].items()),"Unexpected CFN parameter change"
        live=lam.get_function_configuration(FunctionName=p["function"])
        assert live["CodeSha256"]==p["expected"] and live["LastUpdateStatus"]=="Successful"
        if p["machine"]:
            arn,definition,role=p["machine"]
            now=sf.describe_state_machine(stateMachineArn=arn)
            assert now["definition"]==definition and now["roleArn"]==role
        print("LAMBDA_DEPLOYED_VERIFIED",p["kind"],flush=True)

    # Publish frontend only after both AWS code packages are verified.
    frontend=cf.describe_stacks(StackName="afl-app-dev-frontend")["Stacks"][0]
    assert frontend["StackStatus"] in ("CREATE_COMPLETE","UPDATE_COMPLETE")
    outputs={v["OutputKey"]:v["OutputValue"] for v in frontend["Outputs"]}
    bucket=outputs["FrontendBucketName"]
    assert bucket==f"afl-app-dev-frontend-{ACCOUNT}-{REGION}"
    for key,local,mime in (
        ("app.js","frontend/app.js","application/javascript; charset=utf-8"),
        ("index.html","frontend/index.html","text/html; charset=utf-8"),
    ):
        payload=Path(local).read_bytes()
        s3.put_object(Bucket=bucket,Key=key,Body=payload,ContentType=mime,
                      CacheControl="max-age=60, must-revalidate")
        assert s3.get_object(Bucket=bucket,Key=key)["Body"].read()==payload
        print("FRONTEND_VERIFIED",key,flush=True)
    session.client("cloudfront").create_invalidation(
        DistributionId=outputs["DistributionId"],
        InvalidationBatch={"Paths":{"Quantity":2,
                  "Items":["/app.js","/index.html"]},
                           "CallerReference":"personal-greeting-"+run})
    print("PERSONAL_GREETING_RELEASE_COMPLETE: API, FFmpeg assembly and UI verified; no Google calls",flush=True)


if __name__=="__main__":
    main()
