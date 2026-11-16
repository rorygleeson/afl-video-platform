# Stage 0 — AWS bootstrap (CloudFormation)

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

This is a **one-time** setup and is intentionally separate from application deployments.
The bootstrap stack **reuses** an existing GitHub OIDC provider; it never creates a second one.

## Before deploying

- Confirm the AWS account and region in the console (expected development region: `ap-southeast-2`).
- Under IAM > Identity providers, verify `token.actions.githubusercontent.com` exists with audience `sts.amazonaws.com`.
- Inspect `infrastructure/bootstrap/bootstrap.yaml`, especially the execution role's AWS service permissions.
- Use an AWS identity authorised to create IAM roles and CloudFormation stacks.
- Do **not** add long-lived AWS keys or your Replicate token to GitHub.

## Deploy manually once

1. AWS Console > CloudFormation > Create stack > With new resources (standard).
2. Upload `infrastructure/bootstrap/bootstrap.yaml` from this repository.
3. Stack name: `afl-video-bootstrap`.
4. Region: `ap-southeast-2`.
5. Accept defaults for `GitHubOwner` (`rorygleeson`), `GitHubRepository` (`afl-video-platform`) and `GitHubBranch` (`main`).
6. Set `ExistingGitHubOidcProviderArn` from the actual identity-provider ARN shown in IAM.
7. Review the stack changes and acknowledge creation of **named IAM resources** (`CAPABILITY_NAMED_IAM`).
8. Wait for `CREATE_COMPLETE`. If it fails, inspect CloudFormation Events before retrying.
9. In the stack **Outputs**, copy `GitHubDeploymentRoleArn`, `CloudFormationExecutionRoleArn` and `ArtifactBucketName`.

These are not secrets, but do not paste account-specific ARNs here unless needed.

## Configure GitHub Actions

After merging the bootstrap PR to `main`, configure repository **Actions variables**:

- `AWS_REGION` — `ap-southeast-2`
- `AWS_ROLE_ARN` — output `GitHubDeploymentRoleArn`
- `CFN_EXECUTION_ROLE_ARN` — output `CloudFormationExecutionRoleArn`
- `ARTIFACT_BUCKET` — output `ArtifactBucketName`

The deploy role trusts **only** the `main` and `deploy` branches in this repository. PR validation must not assume this role. Production must use separately reviewed permissions.

## What the bootstrap creates

- An S3 artifact bucket for Lambda ZIPs/CloudFormation deployment packages (private, encrypted and versioned).
- A GitHub Actions role restricted to this repository's `main` and `deploy` branches via the existing OIDC provider. The deploy branch is used by the existing Kubrick-style `git deploy` flow.
- A CloudFormation execution role for `afl-app-*` stacks, with IAM role creation limited to `afl-app-*` roles and a mandatory permissions boundary.
- A managed IAM permissions boundary for application runtime roles.

**Permissions review:** the execution role has broad non-IAM permissions, including CloudFront, within the development account to enable initial application-stack deployment. It must be narrowed as the app's concrete resource names become known. Keep production in a separate AWS account or with a separately restricted role. Do not run unreviewed workflow changes from `main`.

## First deployment sequence

1. Push PR → CI validates YAML and source without AWS credentials.
2. Review/merge PR → bootstrap template is available on `main`.
3. Deploy the bootstrap stack from the AWS Console (one time).
4. Configure the variables above.
5. Run the deployment-authentication workflow to execute `aws sts get-caller-identity`.
6. Only then add `afl-app-dev` infrastructure to the deployment pipeline.

## Recovery and lifecycle

- Keep `afl-video-bootstrap` independent from `afl-app-dev`.
- Never automatically destroy the bootstrap stack from CI/CD.
- The artifact bucket has a Retain policy; cleaning it up later is a deliberate manual decision.
- GitHub Actions cannot modify the bootstrap stack with its application CloudFormation permissions.
- Do not assume that a successful GitHub workflow proves the application is deployed; subsequent stages have their own tests.

## CloudFront static frontend

Application CloudFormation will create the private frontend S3 bucket, CloudFront distribution, Origin Access Control and bucket policy. The execution role can provision CloudFront resources. The GitHub deployment role can create invalidations against distributions in this account; tighten to the actual distribution ARN when available. No custom domain, ACM certificate or Route 53 configuration is required for the initial test page.

**If this bootstrap stack is already deployed:** use CloudFormation **Update stack** with the amended YAML. Do not create a duplicate stack. Existing named roles and artifact bucket will be updated in place where possible; review the Change Set first.
