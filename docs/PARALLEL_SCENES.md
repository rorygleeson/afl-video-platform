# Stage 1D — three images generated in parallel

On the phone, upload a selfie and create a job. The **Generate 3 reference images** button is separate and explicitly confirms **three billable Replicate requests**. Uploads by themselves do not invoke Replicate.

The previously successful Scene 1 image edit is extended to three independent image edits. **All three uploaded S3 master PNGs remain unchanged** at `scenarios/afl-grand-final-winning-kick/v1/scene{1,2,3}-start.png`. Scene 2 is also edited, rather than copying the master, because its face is visible. Scene 3's prompt changes the main player face only, while changing all teammates' playing guernseys to the selected team.

## Orchestration

A single Step Functions **Standard** execution runs:

1. `Prepare`: verify all four inputs (selfie + three masters), initialise `scenes` map to PENDING, set overall GENERATING_IMAGES
2. `ParallelScenes`: **inline Map, MaxConcurrency 3**, one item per configured scene
3. Each scene independently **Submit (never automatically retried) → Wait → Poll (read-only retries allowed) → Save**, or **MarkSceneFailed** on any branch error
4. `Finalize`: inspect all three scene statuses and write COMPLETED, PARTIAL_SUCCESS or FAILED

DynamoDB job item contains nested `scenes.scene1`, `scenes.scene2`, `scenes.scene3` with status, prediction ID and result key where available. A scene becomes visible on the phone as soon as it is saved. API GET status derives counts from the nested scene records. Partial results are retained even if other scenes fail.

**Replicate cost:** every click to generate a fresh job launches up to three paid predictions. A single job uses a deterministic execution name, conditional DynamoDB scene claims, and no submit retries to avoid duplicate billing from repeated requests.

## Deployment

Commit changes to GitHub, merge after CI, and trigger the existing GitHub Actions deploy-branch workflow. It redeploys the API, worker, Step Functions definition, scenario config and CloudFront page. No bootstrap IAM update or user-managed secrets are needed. The existing private S3 asset bucket and Secrets Manager entry remain.

**Validation:** CI checks CloudFormation, Python compilation, Boto3 upload signatures, 3-scene config, prompt/image ordering, one submission per scene, and success/partial/failure aggregation.

**Limitation:** API key remains a development-only usage plan, not customer identity; and a remote Replicate submission with unknown outcome is marked failed without automatic re-billing. A reconciler can be added before a consumer launch.
