#!/usr/bin/env python3
"""Repair ONLY the image-worker S3 team artwork permissions via live CFN stack.

CloudFormation source is the deployed template, not a potentially divergent
repository copy. Changeset must be a non-replacing ImageWorkerRole policy
modification; fail closed otherwise. No job or paid Google calls.
"""
import os
from pathlib import Path
import boto3
from validate_change_set import validate

REGION="ap-southeast-2"
ACCOUNT="134387458447"
STACK="afl-app-dev-backend"
SOURCE=Path("infrastructure/app/backend.yaml")

def main():
    session=boto3.Session(region_name=REGION)
    sts=session.client("sts")
    cf=session.client("cloudformation")
    sf=session.client("stepfunctions")
    who=sts.get_caller_identity()
    assert who["Account"]==ACCOUNT
    assert ":assumed-role/afl-video-github-deploy-role/" in who["Arn"]
    stack=cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert stack["StackStatus"]=="UPDATE_COMPLETE"
    live=cf.get_template(StackName=STACK,TemplateStage="Original")["TemplateBody"]
    assert isinstance(live,str), "Expected YAML live template"
    source=SOURCE.read_text()
    marker="              - Sid: UpdateAndReadJobs"
    first="              # Refresh the exact team-artwork object permission"
    assert marker in live and live.count(marker)==1
    assert source.count(first)==1
    region=source.index(first)
    end=source.index(marker,region)
    permission_block=source[region:end]
    assert "Sid: ReadTeamArtworkExplicit" in permission_block
    assert "Sid: ListScenarioTeamArtworkForPreflight" in permission_block
    assert "!Sub" in permission_block and "s3:ListBucket" in permission_block
    assert "Sid: ReadVersionedScenarioMastersAndConfig" in live
    assert "Sid: ReadTeamArtworkExplicit" not in live, "Already in live template"
    proposed=live.replace(marker,permission_block+marker,1)
    assert proposed!=live
    machine="arn:aws:states:ap-southeast-2:134387458447:stateMachine:afl-app-dev-scene1-pilot"
    assert not sf.list_executions(stateMachineArn=machine,statusFilter="RUNNING",maxResults=100)["executions"],"Active image pipeline"
    name="image-master-permission-"+os.environ["GITHUB_RUN_ID"]
    params=[{"ParameterKey":p["ParameterKey"],"UsePreviousValue":True}
            for p in stack["Parameters"]]
    args=dict(StackName=STACK,ChangeSetName=name,ChangeSetType="UPDATE",
              TemplateBody=proposed,Parameters=params,Capabilities=["CAPABILITY_NAMED_IAM"])
    if stack.get("RoleARN"):
        args["RoleARN"]=stack["RoleARN"]
    cf.create_change_set(**args)
    cf.get_waiter("change_set_create_complete").wait(
        StackName=STACK,ChangeSetName=name,WaiterConfig={"Delay":5,"MaxAttempts":48})
    plan=cf.describe_change_set(StackName=STACK,ChangeSetName=name)
    assert not plan.get("NextToken"),"Paginated changeset: manual review needed"
    changes=[x["ResourceChange"] for x in plan["Changes"]]
    print("PROPOSED_CHANGESET",[(x.get("LogicalResourceId"),x.get("Action"),
          x.get("Replacement"),x.get("Details")) for x in changes],flush=True)
    assert len(changes)==1 and changes[0]["LogicalResourceId"]=="ImageWorkerRole", \
        "Refusing any other resource changes"
    assert changes[0]["Action"]=="Modify" and changes[0]["Replacement"] in ("False",False)
    issues=validate(plan,{"ImageWorkerRole"})
    assert not issues,issues
    assert all(d.get("Target",{}).get("Name")=="Policies" and
               d.get("Target",{}).get("RequiresRecreation")=="Never"
               for d in changes[0].get("Details",[]))
    assert not sf.list_executions(stateMachineArn=machine,statusFilter="RUNNING",maxResults=100)["executions"]
    cf.execute_change_set(StackName=STACK,ChangeSetName=name)
    cf.get_waiter("stack_update_complete").wait(
        StackName=STACK,WaiterConfig={"Delay":10,"MaxAttempts":60})
    current=cf.describe_stacks(StackName=STACK)["Stacks"][0]
    assert current["StackStatus"]=="UPDATE_COMPLETE"
    assert cf.get_template(StackName=STACK,TemplateStage="Original")["TemplateBody"]==proposed
    print("IMAGE_TEAM_S3_IAM_DEPLOYED_VERIFIED",flush=True)
    print("No image or video generation requests were submitted.",flush=True)

if __name__=="__main__":
    main()
