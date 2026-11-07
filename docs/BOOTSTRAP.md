# Stage 0 — AWS bootstrap (CloudFormation)

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

The deploy role trusts **only** the `main` branch in this repository. PR validation must not assume this role; deployment runs after merge.

## What the bootstrap creates

- An S3 artifact bucket for Lambda ZIPs/CloudFormation deployment packages (private, encrypted and versioned).
- A GitHub Actions role restricted to this repository's `main` branch via the existing OIDC provider.
- A CloudFormation execution role for `afl-app-*` stacks, with IAM role creation limited to `afl-app-*` roles and a mandatory permissions boundary.
- A managed IAM permissions boundary for application runtime roles.

**Permissions review:** the execution role has broad non-IAM permissions within the development account to enable initial application-stack deployment. It must be narrowed as the app's concrete resource names become known. Keep production in a separate AWS account or with a separately restricted role. Do not run unreviewed workflow changes from `main`.

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
